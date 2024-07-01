import os
import shutil
from time import sleep
import logging 
import configparser
import gitlab
from colorama import init, Fore, Style
import subprocess

import urllib3

DISABLE_INSECURE_HTTPS_WARNINGS = True

# Инициализация colorama
init(autoreset=True)

config = configparser.ConfigParser()
config.read('config.ini')

# Конфигурация GitLab
GITLAB1_URL = config['gitlab1']['url']
GITLAB1_USER = config['gitlab1']['user']
GITLAB1_TOKEN = config['gitlab1']['token']

GITLAB2_URL = config['gitlab2']['url']
GITLAB2_USER = config['gitlab2']['user']
GITLAB2_TOKEN = config['gitlab2']['token']

# Инициализация экземпляров GitLab
gl1 = gitlab.Gitlab(GITLAB1_URL, private_token=GITLAB1_TOKEN, ssl_verify=False)
gl2 = gitlab.Gitlab(GITLAB2_URL, private_token=GITLAB2_TOKEN, ssl_verify=False)

# Настройка логирования
logger = logging.getLogger('sync_logger')
logger.setLevel(logging.DEBUG)

# Обработчик для записи логов в файл
file_handler = logging.FileHandler('sync_log.txt')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Обработчик для вывода логов в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Списки для логирования
synced_without_changes = []
synced_with_changes = []
sync_errors = []

def run_command(command):
    logger.info(f"Выполнение команды: {command}")
    result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        logger.error(Fore.RED + f"Ошибка при выполнении команды: {command}\n{result.stderr}")
    else:
        logger.debug(Fore.GREEN + f"Команда выполнена успешно: {command}\n{result.stdout}")
    return result

def get_group_info_and_subgroups(group_id, gitlab_instance):
    logger.debug(f"Получение информации о группе и подгруппах для ID группы: {group_id}")
    group = gitlab_instance.groups.get(group_id)
    subgroups = group.subgroups.list(all=True)
    logger.debug(f"Получено {len(subgroups)} подгрупп для ID группы: {group_id}")
    return group, subgroups

def get_projects_in_group(group_id, gitlab_instance):
    logger.debug(f"Получение проектов для ID группы: {group_id}")
    group = gitlab_instance.groups.get(group_id)
    projects = group.projects.list(all=True)
    logger.debug(f"Получено {len(projects)} проектов для ID группы: {group_id}")
    return projects

def project_exists_in_gitlab2(project_name_gl2, gl2):
    logger.debug(f"Проверка существования проекта {project_name_gl2} в GitLab2")
    try:
        gl2.projects.get(project_name_gl2)
        logger.debug(Fore.GREEN + f"Проект {project_name_gl2} существует в GitLab2")
        return True
    except gitlab.exceptions.GitlabGetError:
        logger.debug(Fore.YELLOW + f"Проект {project_name_gl2} не существует в GitLab2")
        return False

def create_project_in_gitlab2(project_name_gl2, gl2, group_id_gl2):
    logger.debug(f"Создание проекта {project_name_gl2} в GitLab2")
    try:
        group = gl2.groups.get(group_id_gl2)
        project = gl2.projects.create({'name': project_name_gl2.split('/')[-1], 'namespace_id': group.id})
        logger.info(Fore.GREEN + f"Проект {project_name_gl2} создан в GitLab2.")
        return project
    except Exception as e:
        logger.error(Fore.RED + f"Произошла ошибка при создании проекта {project_name_gl2} в GitLab2: {e}")
        return None

def get_default_branch(project):
    try:
        return project.default_branch
    except Exception as e:
        logger.error(Fore.RED + f"Не удалось получить основную ветку для проекта {project.path_with_namespace}: {e}")
        return None

def sync_project(project, gl1, gl2, group1, group2, group_id_gl2):
    project_id = project.id
    project_name_gl1 = str(project.path_with_namespace)
    group_name_gl1 = str(group1)
    group_name_gl2 = str(group2)
    project_name_gl2 = group2 + project_name_gl1[len(group1):]

    logger.info(Fore.CYAN + f"Начало синхронизации проекта {project_name_gl1} с {project_name_gl2}")
    logger.info(Fore.CYAN + f"{group1} {group2}")
    if not project_exists_in_gitlab2(project_name_gl2, gl2):
        create_project_in_gitlab2(project_name_gl2, gl2, group_id_gl2)

    try:
        # Клонирование репозитория из GitLab1
        clone_url = f"https://{GITLAB1_USER}:{GITLAB1_TOKEN}@{gl1.url.split('://')[1]}/{project_name_gl1}.git"
        clone_command = f"git clone {clone_url} {project_name_gl1}"
        if run_command(clone_command).returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        os.chdir(project_name_gl1)

        # Настройка Git
        run_command("git config --global push.followTags true")
        run_command("git config --global core.bigFileThreshold 1024M")
        run_command("git config pull.rebase false")

        # Добавление удаленного репозитория для GitLab2
        new_url = f"https://{GITLAB2_USER}:{GITLAB2_TOKEN}@{gl2.url.split('://')[1]}/{project_name_gl2}.git"
        add_remote_command = f"git remote add gitlab2 {new_url}"
        if run_command(add_remote_command).returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        # Получение изменений из GitLab2
        fetch_command = f"git fetch gitlab2"
        if run_command(fetch_command).returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        # Получение основной ветки проекта
        default_branch = get_default_branch(project)
        if not default_branch:
            logger.error(Fore.RED + f"Не удалось определить основную ветку для проекта {project_name_gl1}.")
            sync_errors.append(project_name_gl1)
            return

        # Проверка наличия основной ветки в GitLab2
        branch_check_command = f"git ls-remote --heads gitlab2 {default_branch}"
        branch_exists = run_command(branch_check_command).returncode == 0

        if not branch_exists:
            logger.info(Fore.YELLOW + f"Ветка '{default_branch}' не существует в удаленном репозитории gitlab2 для проекта {project_name_gl2}.")
            # Создание основной ветки в удаленном репозитории GitLab2
            run_command(f"git checkout -b {default_branch}")
            run_command(f"git push gitlab2 {default_branch}")

        # Настройка pull для использования merge
        run_command("git config pull.rebase false")

        # Получение изменений из GitLab2 для интеграции обновлений
        pull_command = f"git pull gitlab2 {default_branch} --allow-unrelated-histories"
        if run_command(pull_command).returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        # Отправка всех веток и тегов в GitLab2
        push_command = f"git push gitlab2 --all"
        push_result = run_command(push_command)
        if push_result.returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        push_tags_command = f"git push gitlab2 --tags"
        push_tags_result = run_command(push_tags_command)
        if push_tags_result.returncode != 0:
            sync_errors.append(project_name_gl1)
            return

        if "Already up to date." in push_result.stdout and "Already up to date." in push_tags_result.stdout:
            logger.info(Fore.GREEN + f"Проект {project_name_gl1} уже синхронизирован с GitLab2.")
            synced_without_changes.append(project_name_gl1)
        else:
            logger.info(Fore.GREEN + f"Проект {project_name_gl1} успешно синхронизирован с GitLab2.")
            synced_with_changes.append(project_name_gl1)

    except Exception as e:
        logger.error(Fore.RED + f"Произошла ошибка при синхронизации проекта {project_name_gl1} с GitLab2: {e}")
        sync_errors.append(project_name_gl1)

    finally:
        os.chdir('..')
        # shutil.rmtree(project_name_gl1)

def sync_group_and_subgroups(group_id_gl1, group_name_gl1, group_name_gl2, group_id_gl2):
    group_gl1, subgroups_gl1 = get_group_info_and_subgroups(group_id_gl1, gl1)
    projects_gl1 = get_projects_in_group(group_id_gl1, gl1)

    for project in projects_gl1:
        sync_project(project, gl1, gl2, group_name_gl1, group_name_gl2, group_id_gl2)

    for subgroup in subgroups_gl1:
        sync_group_and_subgroups(subgroup.id, group_name_gl1, group_name_gl2, group_id_gl2)

def main():
    if DISABLE_INSECURE_HTTPS_WARNINGS:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    group_id_gl1 = int(config['gitlab1']['group_id'])
    group_id_gl2 = int(config['gitlab2']['group_id'])
    group_name_gl1 = config['gitlab1']['group_name']
    group_name_gl2 = config['gitlab2']['group_name']

    sync_group_and_subgroups(group_id_gl1, group_name_gl1, group_name_gl2, group_id_gl2)

    # Запись результатов синхронизации в лог
    logger.info("Проекты, синхронизированные без изменений:")
    for project in synced_without_changes:
        logger.info(project)

    logger.info("Проекты, синхронизированные с изменениями:")
    for project in synced_with_changes:
        logger.info(project)

    logger.info("Проекты, при синхронизации которых возникли ошибки:")
    for project in sync_errors:
        logger.info(project)

if __name__ == "__main__":
    main()