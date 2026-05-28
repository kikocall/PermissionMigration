import csv
import json
import argparse
import sys
import os
'''
脚本功能：从Sentry导出的权限策略CSV文件生成Guardian API调用脚本
输入：Sentry导出的权限策略CSV文件
输出：Guardian API调用脚本
python sentry_to_guardian.py --input sentry_policies.csv --output permission_migration.sh
'''
def main():
    # 设置命令行参数
    parser = argparse.ArgumentParser(description='Generate Guardian API scripts from Sentry policies CSV')
    parser.add_argument('--input', '-i', 
                        default='D:\\Desktop\\vscode_workspace\\sentrytoguardian\\opabg-cdh.csv',
                        help='Input CSV file path (default: D:\\Desktop\\python_workhouse\\sentrytoguardian\\opabg-cdh.csv)')
    parser.add_argument('--output', '-o', 
                        default='.\\permission_migration.sh',
                        help='Output script file path (default: .\\permission_migration.sh)')
    
    args = parser.parse_args()
    
    # CSV 文件路径
    csv_file_path = args.input
    
    # 输出脚本文件路径
    output_script_path = args.output
    
    # 检查输入文件是否存在
    if not os.path.exists(csv_file_path):
        print(f"错误: 输入文件 {csv_file_path} 不存在")
        sys.exit(1)

    # 存储已处理的用户、组和角色，避免重复创建
    processed_users = set()
    processed_groups = set()
    processed_roles = set()

    # 存储角色-组、组-用户的分配关系，避免重复分配
    role_assigned_groups = {}
    group_assigned_users = {}

    # 读取CSV文件
    with open(csv_file_path, mode='r', newline='', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        
        # 打开输出脚本文件
        with open(output_script_path, mode='w', encoding='utf-8') as scriptfile:
            for row in reader:
                # 从新格式中获取用户、组和角色信息
                user_name = row['user']
                group_name = row['group']
                role_name = row['principal_name']
                principal_type = row['principal_type']
                
                # 创建用户
                if user_name and user_name not in processed_users:
                    user_email = f"{user_name}@unionpay.io"
                    user_password = "P@ssw0rd"
                    create_user_command = (
                        f'curl -k -X POST "https://147.80.29.17:8380/api/v1/users?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                        f'-H "accept: */*" -H "Content-Type: application/json" '
                        f'-d \'{{ "userEmail": "{user_email}", "userName": "{user_name}", "userPassword": "{user_password}"}}\'\n'
                    )
                    scriptfile.write(create_user_command)
                    processed_users.add(user_name)
                
                # 创建组
                if group_name and group_name not in processed_groups:
                    create_group_command = (
                        f'curl -k -X POST "https://147.80.29.17:8380/api/v1/groups?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                        f'-H "accept: */*" -H "Content-Type: application/json" '
                        f'-d\'{{ "groupName": "{group_name}"}}\'\n'
                    )
                    scriptfile.write(create_group_command)
                    processed_groups.add(group_name)
                
                # 创建角色
                if role_name and role_name not in processed_roles:
                    create_role_command = (
                        f'curl -k -X POST "https://147.80.29.17:8380/api/v1/roles?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                        f'-H "accept: */*" -H "Content-Type: application/json" '
                        f'-d \'{{ "roleName": "{role_name}"}}\'\n'
                    )
                    scriptfile.write(create_role_command)
                    processed_roles.add(role_name)
                
                # 分配用户到组
                if user_name and group_name:
                    if group_name not in group_assigned_users:
                        group_assigned_users[group_name] = set()
                    if user_name not in group_assigned_users[group_name]:
                        add_user_to_group_command = (
                            f'curl -k -X PUT "https://147.80.29.17:8380/api/v1/groups/{group_name}/assign?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                            f'-H "accept: */*" -H "Content-Type: application/json" '
                            f'-d\'{{ "groupName": "{group_name}", "name": "{user_name}", "principalType": "USER"}}\'\n'
                        )
                        scriptfile.write(add_user_to_group_command)
                        group_assigned_users[group_name].add(user_name)
                
                # 分配组到角色
                if group_name and role_name:
                    if role_name not in role_assigned_groups:
                        role_assigned_groups[role_name] = set()
                    if group_name not in role_assigned_groups[role_name]:
                        add_group_to_role_command = (
                            f'curl -k -X PUT "https://147.80.29.17:8380/api/v1/roles/{role_name}/assign?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                            f'-H "accept: */*" -H "Content-Type: application/json" '
                            f'-d\'{{ "name": "{group_name}", "principalType": "GROUP", "roleName": "{role_name}"}}\'\n'
                        )
                        scriptfile.write(add_group_to_role_command)
                        role_assigned_groups[role_name].add(group_name)

                # 处理权限
                database = row['database']
                table = row['table']
                partition = row['partition']
                column = row['column']

                # 判断是HDFS路径权限还是Hive库表权限
                # 如果database包含"/"或为"*"，则为HDFS路径权限，不包含为Hive库表权限
                is_hdfs_permission = ('/' in database) or (database == '*')
                is_hive_permission = ('/' not in database)
                
                if is_hdfs_permission:  # HDFS路径权限
                    privilege = row['privilege']
                    if privilege.strip().upper() == 'ALL' or privilege == '*':
                        privilege = 'READ,WRITE,EXECUTE,ADMIN'
                    for priv in privilege.split(','):
                        priv = priv.strip()
                        if database == '*':
                            data_source = ["GLOBAL"]
                        else:
                            path_parts = database.split('/')
                            data_source = ["PATH", "/"] + [part for part in path_parts if part != 'hdfs:' and part != '']
                        
                        permission_command = (
                            f'curl -k -X PUT "https://147.80.29.17:8380/api/v1/perms/grant?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                            f'-H "accept: */*" -H "Content-Type: application/json" '
                            f'-d \'{{ "name": "{role_name}", "permissionVo": {{ "action": "{priv.upper()}", "administrative": true, "component": "tdfs1", "dataSource": {json.dumps(data_source)}, "grantable": false, "heritable": true }}, "principalType": "{principal_type}" }}\'\n'
                        )
                        scriptfile.write(permission_command)
                    
                if is_hive_permission:  # Hive库表权限
                    privilege = row['privilege']
                    if privilege.strip().upper() == 'ALL' or privilege == '*':
                        privilege = 'CREATE,SELECT,INSERT,UPDATE,DELETE,ADMIN'
                    for priv in privilege.split(','):
                        priv = priv.strip()
                        if database == '*':
                            data_source = ["GLOBAL"]
                        else:
                            data_source = ["TABLE_OR_VIEW"]
                            if database and database != 'GLOBAL':
                                data_source.append(database)
                            if table:
                                data_source.append(table)
                            if partition:
                                data_source.append(partition)
                            if column:
                                data_source.append(column)
                        permission_command = (
                            f'curl -k -X PUT "https://147.80.29.17:8380/api/v1/perms/grant?guardian_access_token=MajRDkS61VVw4kKESlnD-TDH" '
                            f'-H "accept: */*" -H "Content-Type: application/json" '
                            f'-d \'{{ "name": "{role_name}", "permissionVo": {{ "action": "{priv.upper()}", "administrative": true, "component": "quark1", "dataSource": {json.dumps(data_source)}, "grantable": false, "heritable": true }}, "principalType": "{principal_type}" }}\'\n'
                        )
                        scriptfile.write(permission_command)


    print(f"API 调用脚本已生成并保存到 {output_script_path}")

if __name__ == "__main__":
    main()