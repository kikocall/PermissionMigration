#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import csv
import argparse
import sys
import os


def convert_ranger_to_sentry(ranger_json_path, sentry_csv_path):
    """
    将Ranger策略JSON文件转换为Sentry CSV格式
    
    Ranger JSON格式字段:
    - service: 服务名称 (如 cm_hive)
    - serviceType: 服务类型 (如 hive, hdfs, hbase)
    - resources: 资源定义
    - policyItems: 策略项，包含用户、组、角色和权限
    
    Sentry CSV格式字段:
    - database
    - table
    - partition
    - column
    - principal_name
    - group
    - user
    - principal_type
    - privilege
    - grant_option
    - grant_time
    - grantor
    """
    
    # 定义各服务类型允许的权限类型
    hdfs_allowed_privileges = {'READ', 'WRITE', 'EXECUTE', 'ADMIN', 'ACCESS'}
    hive_allowed_privileges = {'CREATE', 'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ADMIN','ACCESS'}
    
    # 读取Ranger策略JSON文件
    with open(ranger_json_path, 'r', encoding='utf-8') as f:
        ranger_data = json.load(f)
    
    # 准备CSV数据
    csv_data = []
    header = [
        'database', 'table', 'partition', 'column', 
        'principal_name', 'group', 'user', 'principal_type', 
        'privilege', 'grant_option', 'grant_time', 'grantor'
    ]
    
    # 处理每个策略
    policies = ranger_data.get('policies', [])
    
    for policy in policies:
        service_type = policy.get('serviceType', '')
        resources = policy.get('resources', {})
        policy_items = policy.get('policyItems', [])
        
        # 根据服务类型处理资源
        if service_type == 'hive':
            # 处理Hive资源
            database_values = resources.get('database', {}).get('values', [])
            table_values = resources.get('table', {}).get('values', [])
            column_values = resources.get('column', {}).get('values', [])
            partition_values = ''
            
            # 处理策略项
            for item in policy_items:
                roles = item.get('roles', [])
                groups = item.get('groups', [])
                users = item.get('users', [])
                accesses = item.get('accesses', [])
                
                # 获取权限列表，并过滤掉不允许的权限类型
                privileges = [access['type'].upper() for access in accesses if access.get('isAllowed', False)]
                if service_type == 'hive':
                    privileges = [priv for priv in privileges if priv in hive_allowed_privileges]
                elif service_type == 'hdfs':
                    privileges = [priv for priv in privileges if priv in hdfs_allowed_privileges]
                
                # 处理资源组合
                if database_values and table_values and column_values and partition_values:
                    # 数据库、表、列和分区都存在
                    for db in database_values:
                        for tbl in table_values:
                            for col in column_values:
                                for part in partition_values:
                                    for privilege in privileges:
                                        # 为每个角色创建一行记录
                                        if roles:
                                            for role in roles:
                                                csv_data.append([
                                                    db, tbl, part, col, 
                                                    role, '', '', 'ROLE', 
                                                    privilege, 'FALSE', '', '--'
                                                ])
                                        
                                        # 为每个组创建一行记录
                                        if groups:
                                            for group in groups:
                                                csv_data.append([
                                                    db, tbl, part, col, 
                                                    '', group, '', 'GROUP', 
                                                    privilege, 'FALSE', '', '--'
                                                ])
                                                
                                        # 为每个用户创建一行记录
                                        if users:
                                            for user in users:
                                                csv_data.append([
                                                    db, tbl, part, col, 
                                                    '', '', user, 'USER', 
                                                    privilege, 'FALSE', '', '--'
                                                ])
                                                
                                        # 如果都没有指定，则创建一行空记录
                                        if not users and not groups and not roles:
                                            csv_data.append([
                                                db, tbl, part, col, 
                                                '', '', '', '', 
                                                privilege, 'FALSE', '', '--'
                                            ])
                elif database_values and table_values and column_values:
                    # 数据库、表和列存在，但没有分区
                    for db in database_values:
                        for tbl in table_values:
                            for col in column_values:
                                for privilege in privileges:
                                    # 为每个角色创建一行记录
                                    if roles:
                                        for role in roles:
                                            csv_data.append([
                                                db, tbl, '', col, 
                                                role, '', '', 'ROLE', 
                                                privilege, 'FALSE', '', '--'
                                            ])
                                    
                                    # 为每个组创建一行记录
                                    if groups:
                                        for group in groups:
                                            csv_data.append([
                                                db, tbl, '', col, 
                                                '', group, '', 'GROUP', 
                                                privilege, 'FALSE', '', '--'
                                            ])
                                            
                                    # 为每个用户创建一行记录
                                    if users:
                                        for user in users:
                                            csv_data.append([
                                                db, tbl, '', col, 
                                                '', '', user, 'USER', 
                                                privilege, 'FALSE', '', '--'
                                            ])
                                            
                                    # 如果都没有指定，则创建一行空记录
                                    if not users and not groups and not roles:
                                        csv_data.append([
                                            db, tbl, '', col, 
                                            '', '', '', '', 
                                            privilege, 'FALSE', '', '--'
                                        ])
                elif database_values and table_values:
                    # 数据库和表存在，但没有列和分区
                    for db in database_values:
                        for tbl in table_values:
                            for privilege in privileges:
                                # 为每个角色创建一行记录
                                if roles:
                                    for role in roles:
                                        csv_data.append([
                                            db, tbl, '', '', 
                                            role, '', '', 'ROLE', 
                                            privilege, 'FALSE', '', '--'
                                        ])
                                
                                # 为每个组创建一行记录
                                if groups:
                                    for group in groups:
                                        csv_data.append([
                                            db, tbl, '', '', 
                                            '', group, '', 'GROUP', 
                                            privilege, 'FALSE', '', '--'
                                        ])
                                        
                                # 为每个用户创建一行记录
                                if users:
                                    for user in users:
                                        csv_data.append([
                                            db, tbl, '', '', 
                                            '', '', user, 'USER', 
                                            privilege, 'FALSE', '', '--'
                                        ])
                                        
                                # 如果都没有指定，则创建一行空记录
                                if not users and not groups and not roles:
                                    csv_data.append([
                                        db, tbl, '', '', 
                                        '', '', '', '', 
                                        privilege, 'FALSE', '', '--'
                                    ])
                elif database_values:
                    # 只有数据库
                    for db in database_values:
                        for privilege in privileges:
                            # 为每个角色创建一行记录
                            if roles:
                                for role in roles:
                                    csv_data.append([
                                        db, '', '', '', 
                                        role, '', '', 'ROLE', 
                                        privilege, 'FALSE', '', '--'
                                    ])
                            
                            # 为每个组创建一行记录
                            if groups:
                                for group in groups:
                                    csv_data.append([
                                        db, '', '', '', 
                                        '', group, '', 'GROUP', 
                                        privilege, 'FALSE', '', '--'
                                    ])
                                    
                            # 为每个用户创建一行记录
                            if users:
                                for user in users:
                                    csv_data.append([
                                        db, '', '', '', 
                                        '', '', user, 'USER', 
                                        privilege, 'FALSE', '', '--'
                                    ])
                                    
                            # 如果都没有指定，则创建一行空记录
                            if not users and not groups and not roles:
                                csv_data.append([
                                    db, '', '', '', 
                                    '', '', '', '', 
                                    privilege, 'FALSE', '', '--'
                                ])
                                
        elif service_type == 'hdfs':
            # 处理HDFS资源
            path_values = resources.get('path', {}).get('values', [])
            
            for item in policy_items:
                roles = item.get('roles', [])
                groups = item.get('groups', [])
                users = item.get('users', [])
                accesses = item.get('accesses', [])
                
                # 获取权限列表，并过滤掉不允许的权限类型
                privileges = [access['type'].upper() for access in accesses if access.get('isAllowed', False)]
                privileges = [priv for priv in privileges if priv in hdfs_allowed_privileges]
                
                for path in path_values:
                    for privilege in privileges:
                        # 为每个角色创建一行记录
                        if roles:
                            for role in roles:
                                csv_data.append([
                                    path, '', '', '', 
                                    role, '', '', 'ROLE', 
                                    privilege, 'FALSE', '', '--'
                                ])
                        
                        # 为每个组创建一行记录
                        if groups:
                            for group in groups:
                                csv_data.append([
                                    path, '', '', '', 
                                    '', group, '', 'GROUP', 
                                    privilege, 'FALSE', '', '--'
                                ])
                                
                        # 为每个用户创建一行记录
                        if users:
                            for user in users:
                                csv_data.append([
                                    path, '', '', '', 
                                    '', '', user, 'USER', 
                                    privilege, 'FALSE', '', '--'
                                ])
                                
                        # 如果都没有指定，则创建一行空记录
                        if not users and not groups and not roles:
                            csv_data.append([
                                path, '', '', '', 
                                '', '', '', '', 
                                privilege, 'FALSE', '', '--'
                            ])
                            
        # elif service_type == 'hbase':
        #     # 处理HBase资源
        #     table_values = resources.get('table', {}).get('values', [])
            
        #     for item in policy_items:
        #         roles = item.get('roles', [])
        #         groups = item.get('groups', [])
        #         users = item.get('users', [])
        #         accesses = item.get('accesses', [])
                
        #         # 获取权限列表
        #         privileges = [access['type'].upper() for access in accesses if access.get('isAllowed', False)]
                
        #         for table in table_values:
        #             # 特殊处理table列
        #             if table == '*':
        #                 table = ''
                        
        #             # 简化处理，只考虑表级别权限
        #             for privilege in privileges:
        #                 # 为每个用户创建一行记录
        #                 if users:
        #                     for user in users:
        #                         csv_data.append([
        #                             service_type, table, '', '', '', 
        #                             user, '', '', 
        #                             privilege
        #                         ])
                        
        #                 # 为每个组创建一行记录
        #                 if groups:
        #                     for group in groups:
        #                         csv_data.append([
        #                             service_type, table, '', '', '', 
        #                             '', group, '', 
        #                             privilege
        #                         ])
                                
        #                 # 为每个角色创建一行记录
        #                 if roles:
        #                     for role in roles:
        #                         csv_data.append([
        #                             service_type, table, '', '', '', 
        #                             '', '', role, 
        #                             privilege
        #                         ])
                                
        #                 # 如果都没有指定，则创建一行空记录
        #                 if not users and not groups and not roles:
        #                     csv_data.append([
        #                         service_type, table, '', '', '', 
        #                         '', '', '', 
        #                         privilege
        #                     ])
    
    # 写入CSV文件
    with open(sentry_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        writer.writerows(csv_data)
    
    print(f"成功转换 {len(csv_data)} 条策略记录")
    print(f"输出文件: {sentry_csv_path}")


def main():
    parser = argparse.ArgumentParser(description='将Ranger策略JSON转换为Sentry CSV格式')
    parser.add_argument('input', help='Ranger策略JSON文件路径')
    parser.add_argument('output', help='输出Sentry CSV文件路径')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"错误: 输入文件 {args.input} 不存在")
        sys.exit(1)
    
    try:
        convert_ranger_to_sentry(args.input, args.output)
        print("转换完成!")
    except Exception as e:
        print(f"转换过程中出现错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
    # python ranger_to_sentry.py Ranger_Policies_20251027_081637.json sentry_policies.csv