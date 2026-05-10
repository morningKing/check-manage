import psycopg2
import psycopg2.extras
import json
from config import DB_CONFIG

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# 1. 创建页面配置 - 任务管理（带日期字段）
page_id = 'page-task-calendar'

fields_json = json.dumps([
    {
        'id': 'field-1',
        'fieldName': 'taskName',
        'label': '任务名称',
        'controlType': 'text',
        'required': True,
        'order': 1,
        'isPrimaryKey': True
    },
    {
        'id': 'field-2',
        'fieldName': 'startDate',
        'label': '开始日期',
        'controlType': 'date',
        'required': True,
        'order': 2
    },
    {
        'id': 'field-3',
        'fieldName': 'endDate',
        'label': '结束日期',
        'controlType': 'date',
        'required': False,
        'order': 3
    },
    {
        'id': 'field-4',
        'fieldName': 'status',
        'label': '状态',
        'controlType': 'select',
        'required': False,
        'order': 4,
        'options': [
            {'label': '待处理', 'value': 'pending'},
            {'label': '进行中', 'value': 'progress'},
            {'label': '已完成', 'value': 'done'},
            {'label': '已阻塞', 'value': 'blocked'}
        ]
    },
    {
        'id': 'field-5',
        'fieldName': 'priority',
        'label': '优先级',
        'controlType': 'select',
        'required': False,
        'order': 5,
        'options': [
            {'label': '高', 'value': 'high'},
            {'label': '中', 'value': 'medium'},
            {'label': '低', 'value': 'low'}
        ]
    },
    {
        'id': 'field-6',
        'fieldName': 'description',
        'label': '描述',
        'controlType': 'textarea',
        'required': False,
        'order': 6
    },
    {
        'id': 'field-7',
        'fieldName': 'assignee',
        'label': '负责人',
        'controlType': 'text',
        'required': False,
        'order': 7
    }
], ensure_ascii=False)

view_config_json = json.dumps({
    'defaultView': 'calendar',
    'calendar': {
        'dateField': 'startDate',
        'endDateField': 'endDate',
        'cardTitle': 'taskName',
        'cardColorField': 'status',
        'defaultMode': 'month'
    }
}, ensure_ascii=False)

# 检查是否已存在
cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
if cur.fetchone():
    cur.execute('UPDATE page_configs SET name = %s, fields = %s WHERE id = %s',
        ('任务管理', fields_json, page_id))
    print(f'更新页面配置: {page_id}')
else:
    cur.execute('INSERT INTO page_configs (id, name, description, api_endpoint, fields) VALUES (%s, %s, %s, %s, %s)',
        (page_id, '任务管理', '用于测试日历视图的任务管理页面', '/api/task-calendar', fields_json))
    print(f'创建页面配置: {page_id}')

# 2. 创建菜单项
menu_id = 'menu-task-calendar'
roles_json = json.dumps(['admin', 'developer', 'guest'])

# 检查是否已存在
cur.execute('SELECT id FROM menus WHERE id = %s', (menu_id,))
if cur.fetchone():
    cur.execute('UPDATE menus SET name = %s, page_id = %s, path = %s WHERE id = %s',
        ('任务管理', page_id, '/task-calendar', menu_id))
    print(f'更新菜单: {menu_id}')
else:
    cur.execute('INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (menu_id, '任务管理', 'Calendar', page_id, 'menu-2', 100, '/task-calendar', roles_json, 'data'))
    print(f'创建菜单: {menu_id}')

# 3. 创建测试数据
collection = 'task-calendar'

test_tasks = [
    {
        'id': 'task-1',
        'data': {
            'taskName': '完成需求文档',
            'startDate': '2026-05-01',
            'endDate': '2026-05-05',
            'status': 'done',
            'priority': 'high',
            'assignee': '张三',
            'description': '编写V2.0版本需求文档'
        }
    },
    {
        'id': 'task-2',
        'data': {
            'taskName': '开发登录模块',
            'startDate': '2026-05-05',
            'endDate': '2026-05-10',
            'status': 'progress',
            'priority': 'high',
            'assignee': '李四',
            'description': '实现新的登录功能'
        }
    },
    {
        'id': 'task-3',
        'data': {
            'taskName': '设计数据库表结构',
            'startDate': '2026-05-08',
            'endDate': '2026-05-12',
            'status': 'progress',
            'priority': 'medium',
            'assignee': '王五',
            'description': '设计新的用户模块数据库'
        }
    },
    {
        'id': 'task-4',
        'data': {
            'taskName': '修复Bug#123',
            'startDate': '2026-05-10',
            'status': 'pending',
            'priority': 'high',
            'assignee': '张三',
            'description': '修复登录超时问题'
        }
    },
    {
        'id': 'task-5',
        'data': {
            'taskName': '编写单元测试',
            'startDate': '2026-05-15',
            'endDate': '2026-05-18',
            'status': 'pending',
            'priority': 'medium',
            'assignee': '李四',
            'description': '为新模块编写测试'
        }
    },
    {
        'id': 'task-6',
        'data': {
            'taskName': '等待外部依赖',
            'startDate': '2026-05-12',
            'endDate': '2026-05-20',
            'status': 'blocked',
            'priority': 'low',
            'assignee': '王五',
            'description': '等待第三方API接口'
        }
    },
    {
        'id': 'task-7',
        'data': {
            'taskName': '代码评审会议',
            'startDate': '2026-05-09',
            'status': 'done',
            'priority': 'medium',
            'assignee': '全员',
            'description': '本周代码评审'
        }
    },
    {
        'id': 'task-8',
        'data': {
            'taskName': '部署上线',
            'startDate': '2026-05-25',
            'endDate': '2026-05-28',
            'status': 'pending',
            'priority': 'high',
            'assignee': '运维团队',
            'description': 'V2.0版本上线部署'
        }
    },
]

for task in test_tasks:
    data_json = json.dumps(task['data'], ensure_ascii=False)
    cur.execute('SELECT id FROM dynamic_data WHERE id = %s AND collection = %s', (task['id'], collection))
    if cur.fetchone():
        cur.execute('UPDATE dynamic_data SET data = %s WHERE id = %s AND collection = %s',
            (data_json, task['id'], collection))
    else:
        cur.execute('INSERT INTO dynamic_data (id, collection, data) VALUES (%s, %s, %s)',
            (task['id'], collection, data_json))

print(f'创建 {len(test_tasks)} 条测试任务数据')

conn.commit()
conn.close()
print('\n测试数据创建完成!')