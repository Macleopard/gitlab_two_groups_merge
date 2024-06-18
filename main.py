import os
import shutil
from time import sleep

import gitlab

# Конфигурация GitLab
GITLAB1_URL = 'https://gitlab.local'
GITLAB1_USER = ""
GITLAB1_TOKEN = ''

GITLAB2_URL = 'https://gitlab.com'
GITLAB2_USER = ""
GITLAB2_TOKEN = ''

gl1 = gitlab.Gitlab(GITLAB1_URL, private_token=GITLAB1_TOKEN, ssl_verify=False)
gl2 = gitlab.Gitlab(GITLAB2_URL, private_token=GITLAB2_TOKEN, ssl_verify=False)


def run_command(command):
    print(f"Executing: {command}")
    result = os.system(command)
    if result != 0:
        print(f"Error executing: {command}")
    return result


def get_group_info_and_subgroups(group_id, gitlab_instance):
    group = gitlab_instance.groups.get(group_id)
    subgroups = group.subgroups.list(all=True)
    return group, subgroups


def get_projects_in_group(group_id, gitlab_instance):
    group = gitlab_instance.groups.get(group_id)
    projects = group.projects.list(all=True)
    return projects


def sync_project(project, gl1, gl2, group1, group2):
    project_id = project.id
    project_name_gl1 = project.path_with_namespace
    group_name_gl1 = group1
    group_name_gl2 = group2
    project_name_gl2 = project_name_gl1.replace(group_name_gl1, group_name_gl2)
    # print(project_name_gl1, project_name_gl2)
    try:
        # Клонируем репозиторий из гитлаб1 с использованием токена
        clone_url = f"https://{GITLAB1_USER}:{GITLAB1_TOKEN}@{gl1.url.split('://')[1]}/{project_name_gl1}.git"
        clone_command = f"git clone {clone_url} {project_name_gl1}"
        if run_command(clone_command) != 0:
            return

        config_command = "git config --global push.followTags true"
        run_command(config_command)

        config_big_file_threshold = "git config --global core.bigFileThreshold 1024M"
        run_command(config_big_file_threshold)

        # Переходим в директорию проекта
        os.chdir(project_name_gl1)

        # Добавляем удаленный репозиторий гитлаб2 с токеном
        new_url = f"https://{GITLAB2_USER}:{GITLAB2_TOKEN}@{gl2.url.split('://')[1]}/{project_name_gl2}.git"
        add_remote_command = f"git remote add gitlab2 {new_url}"
        if run_command(add_remote_command) != 0:
            return

        # Получаем изменения из гитлаб2
        fetch_command = f"git fetch gitlab2"
        if run_command(fetch_command) != 0:
            return

        # Проверяем наличие ветки main в удаленном репозитории gitlab2
        branch_check_command = f"git ls-remote --heads gitlab2 main"
        if run_command(branch_check_command) != 0:
            print("Branch 'main' does not exist in the remote repository gitlab2.")
            return

        # Сливаем изменения из гитлаб2 в текущую ветку
        merge_command = f"git merge gitlab2/main"
        if run_command(merge_command) != 0:
            return

        # Пушим изменения в гитлаб2
        push_command = f"git push gitlab2"
        run_command(push_command)

        push_command = f"git push gitlab2 --tags"
        run_command(push_command)

    finally:
        # Возвращаемся в исходную директорию и удаляем временный клон
        os.chdir('..')
        folder_to_del = os.getcwd()

        # shutil.rmtree(f"{folder_to_del}")


def sync_group(group1, group2, gl1, gl2, confirmation=True, group_name1=None, group_name2=None):
    group1_info, subgroups1 = get_group_info_and_subgroups(group1.id, gl1)
    group2_info, subgroups2 = get_group_info_and_subgroups(group2.id, gl2)

    # Синхронизация проектов в группе
    projects1 = get_projects_in_group(group1.id, gl1)
    actions = []

    for project in projects1:
        actions.append(f"Синхронизация проекта: {project.path_with_namespace}")

    # Синхронизация подгрупп и их проектов
    for subgroup1 in subgroups1:
        subgroup2 = next((sg for sg in subgroups2 if sg.name == subgroup1.name), None)
        if subgroup2:
            actions.append(f"Синхронизация подгруппы: {subgroup1.full_path}")
        else:
            actions.append(f"Пропуск подгруппы, так как она отсутствует в GitLab2: {subgroup1.full_path}")

    if not confirmation:
        print("\nСледующие действия будут выполнены:")
        for action in actions:
            print(action)

        proceed = input("\nВы хотите продолжить? (да/нет): ").strip().lower()
        if proceed != 'да':
            print("Операция отменена пользователем.")
            return

    for project in projects1:
        sync_project(project, gl1, gl2, group1_name, group2_name)

    for subgroup1 in subgroups1:
        subgroup2 = next((sg for sg in subgroups2 if sg.name == subgroup1.name), None)
        if subgroup2:
            sync_group(subgroup1, subgroup2, gl1, gl2, confirmation=True)


if __name__ == "__main__":
    GROUP1_ID = ''  # ID группы в GitLab1
    GROUP2_ID = ''  # ID группы в GitLab2

    group1 = gl1.groups.get(GROUP1_ID)
    group2 = gl2.groups.get(GROUP2_ID)
    group1_name = group1.name.lower()
    group2_name = group2.name.lower()
    sync_group(group1, group2, gl1, gl2, True, group1_name, group2_name)
