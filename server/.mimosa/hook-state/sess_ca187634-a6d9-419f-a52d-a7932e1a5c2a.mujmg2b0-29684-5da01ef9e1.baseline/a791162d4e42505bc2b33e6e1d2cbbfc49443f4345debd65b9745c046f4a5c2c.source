import sys
import os

# 添加 server 目录到路径，以便导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import psycopg2
import psycopg2.extras
import json
from config import DB_CONFIG

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# 1. 创建页面配置 - 项目甘特图
page_id = 'page-project-gantt'

fields_json = json.dumps([
    {
        'id': 'field-g1',
        'fieldName': 'taskName',
        'label': '任务名称',
        'controlType': 'text',
        'required': True,
        'order': 1,
        'isPrimaryKey': True
    },
    {
        'id': 'field-g2',
        'fieldName': 'startDate',
        'label': '开始日期',
        'controlType': 'date',
        'required': True,
        'order': 2
    },
    {
        'id': 'field-g3',
        'fieldName': 'endDate',
        'label': '结束日期',
        'controlType': 'date',
        'required': True,
        'order': 3
    },
    {
        'id': 'field-g4',
        'fieldName': 'progress',
        'label': '进度',
        'controlType': 'number',
        'required': False,
        'order': 4,
        'min': 0,
        'max': 100
    },
    {
        'id': 'field-g5',
        'fieldName': 'status',
        'label': '状态',
        'controlType': 'select',
        'required': False,
        'order': 5,
        'options': [
            {'label': '待处理', 'value': 'pending'},
            {'label': '进行中', 'value': 'progress'},
            {'label': '已完成', 'value': 'done'},
            {'label': '阻塞', 'value': 'blocked'}
        ]
    },
    {
        'id': 'field-g6',
        'fieldName': 'priority',
        'label': '优先级',
        'controlType': 'select',
        'required': False,
        'order': 6,
        'options': [
            {'label': '紧急', 'value': 'high'},
            {'label': '一般', 'value': 'medium'},
            {'label': '低', 'value': 'low'}
        ]
    },
    {
        'id': 'field-g7',
        'fieldName': 'assignee',
        'label': '负责人',
        'controlType': 'text',
        'required': False,
        'order': 7
    },
    {
        'id': 'field-g8',
        'fieldName': 'dependencies',
        'label': '依赖任务',
        'controlType': 'multiSelect',
        'required': False,
        'order': 8,
        'optionsSource': {
            'type': 'dynamic',
            'collection': 'project-gantt',
            'labelField': 'taskName',
            'valueField': 'id'
        }
    },
    {
        'id': 'field-g9',
        'fieldName': 'description',
        'label': '描述',
        'controlType': 'textarea',
        'required': False,
        'order': 9
    }
], ensure_ascii=False)

# 甘特图视图配置
view_config_json = json.dumps({
    'defaultView': 'gantt',
    'gantt': {
        'startDateField': 'startDate',
        'endDateField': 'endDate',
        'titleField': 'taskName',
        'progressField': 'progress',
        'dependenciesField': 'dependencies',
        'colorField': 'status'
    }
}, ensure_ascii=False)

# 检查是否已存在
cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
if cur.fetchone():
    cur.execute('UPDATE page_configs SET name = %s, fields = %s, view_config = %s WHERE id = %s',
        ('项目甘特图', fields_json, view_config_json, page_id))
    print(f'更新页面配置: {page_id}')
else:
    cur.execute('INSERT INTO page_configs (id, name, description, api_endpoint, fields, view_config) VALUES (%s, %s, %s, %s, %s, %s)',
        (page_id, '项目甘特图', '软件开发项目甘特图视图测试数据', '/api/project-gantt', fields_json, view_config_json))
    print(f'创建页面配置: {page_id}')

# 2. 创建菜单项
menu_id = 'menu-project-gantt'
roles_json = json.dumps(['admin', 'developer', 'guest'])

# 检查是否已存在
cur.execute('SELECT id FROM menus WHERE id = %s', (menu_id,))
if cur.fetchone():
    cur.execute('UPDATE menus SET name = %s, page_id = %s, path = %s WHERE id = %s',
        ('项目甘特图', page_id, '/project-gantt', menu_id))
    print(f'更新菜单: {menu_id}')
else:
    cur.execute('INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (menu_id, '项目甘特图', 'Calendar', page_id, 'menu-2', 50, '/project-gantt', roles_json, 'data'))
    print(f'创建菜单: {menu_id}')

# 3. 创建测试数据 - 软件开发项目任务（带依赖关系）
collection = 'project-gantt'

# 任务ID前缀
tasks = [
    # 第一阶段：需求分析
    {
        'id': 'gantt-req-1',
        'data': {
            'taskName': '需求调研',
            'startDate': '2026-05-01',
            'endDate': '2026-05-05',
            'progress': 100,
            'status': 'done',
            'priority': 'high',
            'assignee': '张三',
            'dependencies': [],
            'description': '收集用户需求，整理需求文档'
        }
    },
    {
        'id': 'gantt-req-2',
        'data': {
            'taskName': '需求文档编写',
            'startDate': '2026-05-05',
            'endDate': '2026-05-08',
            'progress': 100,
            'status': 'done',
            'priority': 'high',
            'assignee': '张三',
            'dependencies': ['gantt-req-1'],
            'description': '编写详细需求规格说明书'
        }
    },
    {
        'id': 'gantt-req-3',
        'data': {
            'taskName': '需求评审',
            'startDate': '2026-05-08',
            'endDate': '2026-05-09',
            'progress': 100,
            'status': 'done',
            'priority': 'medium',
            'assignee': '全员',
            'dependencies': ['gantt-req-2'],
            'description': '组织需求评审会议'
        }
    },
    # 第二阶段：设计
    {
        'id': 'gantt-design-1',
        'data': {
            'taskName': '系统架构设计',
            'startDate': '2026-05-10',
            'endDate': '2026-05-14',
            'progress': 100,
            'status': 'done',
            'priority': 'high',
            'assignee': '李四',
            'dependencies': ['gantt-req-3'],
            'description': '设计系统整体架构和技术方案'
        }
    },
    {
        'id': 'gantt-design-2',
        'data': {
            'taskName': '数据库设计',
            'startDate': '2026-05-12',
            'endDate': '2026-05-15',
            'progress': 80,
            'status': 'progress',
            'priority': 'high',
            'assignee': '王五',
            'dependencies': ['gantt-design-1'],
            'description': '设计数据库表结构和索引'
        }
    },
    {
        'id': 'gantt-design-3',
        'data': {
            'taskName': 'UI/UX设计',
            'startDate': '2026-05-10',
            'endDate': '2026-05-16',
            'progress': 60,
            'status': 'progress',
            'priority': 'medium',
            'assignee': '设计团队',
            'dependencies': ['gantt-req-3'],
            'description': '设计用户界面和交互流程'
        }
    },
    # 第三阶段：开发
    {
        'id': 'gantt-dev-1',
        'data': {
            'taskName': '后端API开发',
            'startDate': '2026-05-15',
            'endDate': '2026-05-25',
            'progress': 40,
            'status': 'progress',
            'priority': 'high',
            'assignee': '开发团队',
            'dependencies': ['gantt-design-1', 'gantt-design-2'],
            'description': '开发RESTful API接口'
        }
    },
    {
        'id': 'gantt-dev-2',
        'data': {
            'taskName': '前端页面开发',
            'startDate': '2026-05-16',
            'endDate': '2026-05-26',
            'progress': 30,
            'status': 'progress',
            'priority': 'high',
            'assignee': '前端团队',
            'dependencies': ['gantt-design-3'],
            'description': '开发前端页面和组件'
        }
    },
    {
        'id': 'gantt-dev-3',
        'data': {
            'taskName': '集成开发',
            'startDate': '2026-05-25',
            'endDate': '2026-05-28',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '开发团队',
            'dependencies': ['gantt-dev-1', 'gantt-dev-2'],
            'description': '前后端集成联调'
        }
    },
    # 第四阶段：测试
    {
        'id': 'gantt-test-1',
        'data': {
            'taskName': '单元测试',
            'startDate': '2026-05-20',
            'endDate': '2026-05-27',
            'progress': 20,
            'status': 'progress',
            'priority': 'medium',
            'assignee': '测试团队',
            'dependencies': ['gantt-dev-1'],
            'description': '编写和执行单元测试'
        }
    },
    {
        'id': 'gantt-test-2',
        'data': {
            'taskName': '集成测试',
            'startDate': '2026-05-28',
            'endDate': '2026-05-31',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '测试团队',
            'dependencies': ['gantt-dev-3', 'gantt-test-1'],
            'description': '执行系统集成测试'
        }
    },
    {
        'id': 'gantt-test-3',
        'data': {
            'taskName': '性能测试',
            'startDate': '2026-05-31',
            'endDate': '2026-06-02',
            'progress': 0,
            'status': 'pending',
            'priority': 'medium',
            'assignee': '测试团队',
            'dependencies': ['gantt-test-2'],
            'description': '执行性能和压力测试'
        }
    },
    # 第五阶段：部署
    {
        'id': 'gantt-deploy-1',
        'data': {
            'taskName': '部署准备',
            'startDate': '2026-06-02',
            'endDate': '2026-06-04',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '运维团队',
            'dependencies': ['gantt-test-3'],
            'description': '准备生产环境和部署脚本'
        }
    },
    {
        'id': 'gantt-deploy-2',
        'data': {
            'taskName': '上线部署',
            'startDate': '2026-06-04',
            'endDate': '2026-06-05',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '运维团队',
            'dependencies': ['gantt-deploy-1'],
            'description': '执行生产环境部署'
        }
    },
    {
        'id': 'gantt-deploy-3',
        'data': {
            'taskName': '上线验证',
            'startDate': '2026-06-05',
            'endDate': '2026-06-06',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '全员',
            'dependencies': ['gantt-deploy-2'],
            'description': '验证上线功能和性能'
        }
    },
    # 阻塞任务示例
    {
        'id': 'gantt-block-1',
        'data': {
            'taskName': '等待第三方API',
            'startDate': '2026-05-18',
            'endDate': '2026-05-22',
            'progress': 10,
            'status': 'blocked',
            'priority': 'high',
            'assignee': '开发团队',
            'dependencies': [],
            'description': '等待支付接口文档和密钥'
        }
    },
    {
        'id': 'gantt-block-2',
        'data': {
            'taskName': '支付模块开发',
            'startDate': '2026-05-22',
            'endDate': '2026-05-28',
            'progress': 0,
            'status': 'pending',
            'priority': 'high',
            'assignee': '开发团队',
            'dependencies': ['gantt-block-1', 'gantt-dev-1'],
            'description': '集成支付模块（依赖第三方API）'
        }
    }
]

for task in tasks:
    data_json = json.dumps(task['data'], ensure_ascii=False)
    cur.execute('SELECT id FROM dynamic_data WHERE id = %s AND collection = %s', (task['id'], collection))
    if cur.fetchone():
        cur.execute('UPDATE dynamic_data SET data = %s WHERE id = %s AND collection = %s',
            (data_json, task['id'], collection))
    else:
        cur.execute('INSERT INTO dynamic_data (id, collection, data) VALUES (%s, %s, %s)',
            (task['id'], collection, data_json))

print(f'创建 {len(tasks)} 条甘特图测试任务数据')

conn.commit()
conn.close()
print('\n甘特图测试数据创建完成!')
print('访问路径: http://localhost:5173/project-gantt')
print('默认视图: 甘特图')