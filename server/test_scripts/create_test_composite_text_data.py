"""
测试 compositeText（组合文本）字段类型

创建一个"客户信息"页面，包含：
- province / city / district：text 字段
- fullAddress：compositeText，组合省市区，分隔符 " - "
- company / title：text 字段
- personInfo：compositeText，组合公司+职位，分隔符 " · "

测试数据覆盖：
1. 全部源字段都有值 → 完整拼接
2. 部分源字段为空   → 仅拼接有值部分
3. 全部源字段为空   → 显示空值
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import psycopg2
import psycopg2.extras
import json
from config import DB_CONFIG

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ============================================================
# 1. 创建页面配置
# ============================================================
page_id = 'page-composite-test'

fields = [
    {
        'id': 'ct-field-1',
        'fieldName': 'name',
        'label': '客户姓名',
        'controlType': 'text',
        'required': True,
        'order': 1,
        'isPrimaryKey': True
    },
    {
        'id': 'ct-field-2',
        'fieldName': 'province',
        'label': '省份',
        'controlType': 'text',
        'required': False,
        'order': 2
    },
    {
        'id': 'ct-field-3',
        'fieldName': 'city',
        'label': '城市',
        'controlType': 'text',
        'required': False,
        'order': 3
    },
    {
        'id': 'ct-field-4',
        'fieldName': 'district',
        'label': '区县',
        'controlType': 'text',
        'required': False,
        'order': 4
    },
    {
        'id': 'ct-field-5',
        'fieldName': 'fullAddress',
        'label': '完整地址',
        'controlType': 'compositeText',
        'required': False,
        'order': 5,
        'compositeTextConfig': {
            'sourceFields': ['province', 'city', 'district'],
            'separator': ' - '
        }
    },
    {
        'id': 'ct-field-6',
        'fieldName': 'company',
        'label': '公司',
        'controlType': 'text',
        'required': False,
        'order': 6
    },
    {
        'id': 'ct-field-7',
        'fieldName': 'title',
        'label': '职位',
        'controlType': 'text',
        'required': False,
        'order': 7
    },
    {
        'id': 'ct-field-8',
        'fieldName': 'personInfo',
        'label': '个人信息',
        'controlType': 'compositeText',
        'required': False,
        'order': 8,
        'compositeTextConfig': {
            'sourceFields': ['name', 'company', 'title'],
            'separator': ' · '
        }
    },
]

fields_json = json.dumps(fields, ensure_ascii=False)

cur.execute('SELECT id FROM page_configs WHERE id = %s', (page_id,))
if cur.fetchone():
    cur.execute('UPDATE page_configs SET name = %s, fields = %s WHERE id = %s',
        ('客户信息(compositeText测试)', fields_json, page_id))
    print(f'更新页面配置: {page_id}')
else:
    cur.execute('INSERT INTO page_configs (id, name, description, api_endpoint, fields) VALUES (%s, %s, %s, %s, %s)',
        (page_id, '客户信息(compositeText测试)', '测试组合文本字段类型', '/api/composite-test', fields_json))
    print(f'创建页面配置: {page_id}')

# ============================================================
# 2. 创建菜单项（挂在"数据工具"下）
# ============================================================
menu_id = 'menu-composite-test'
roles_json = json.dumps(['admin', 'developer', 'guest'])

cur.execute('SELECT id FROM menus WHERE id = %s', (menu_id,))
if cur.fetchone():
    cur.execute('UPDATE menus SET name = %s, page_id = %s, path = %s WHERE id = %s',
        ('客户信息(compositeText测试)', page_id, '/composite-test', menu_id))
    print(f'更新菜单: {menu_id}')
else:
    cur.execute('INSERT INTO menus (id, name, icon, page_id, parent_id, "order", path, roles, menu_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (menu_id, '客户信息(compositeText测试)', 'User', page_id, 'menu-2', 200, '/composite-test', roles_json, 'data'))
    print(f'创建菜单: {menu_id}')

# ============================================================
# 3. 创建测试数据
# ============================================================
collection = 'composite-test'

def _composite(source_fields, separator, data):
    """模拟前端 computeCompositeValue 逻辑"""
    parts = [data.get(f, '') for f in source_fields]
    parts = [v for v in parts if v not in (None, '', [])]
    parts = [str(v) for v in parts]
    return separator.join(parts) if parts else ''

test_records = [
    {
        'id': 'ct-001',
        'data': {
            'name': '张三',
            'province': '广东省',
            'city': '深圳市',
            'district': '南山区',
            # fullAddress: "广东省 - 深圳市 - 南山区"  (全部源字段)
            'company': '腾讯科技',
            'title': '高级工程师',
            # personInfo: "张三 · 腾讯科技 · 高级工程师"  (全部源字段)
        }
    },
    {
        'id': 'ct-002',
        'data': {
            'name': '李四',
            'province': '北京市',
            'city': '北京市',
            # district 缺失 → fullAddress: "北京市 - 北京市"
            'company': '字节跳动',
            # title 缺失 → personInfo: "李四 · 字节跳动"
        }
    },
    {
        'id': 'ct-003',
        'data': {
            'name': '王五',
            # province / city / district 全部缺失 → fullAddress: ""
            # company / title 全部缺失 → personInfo: "王五"
        }
    },
    {
        'id': 'ct-004',
        'data': {
            'name': '赵六',
            'province': '浙江省',
            # city 缺失
            'district': '西湖区',
            # fullAddress: "浙江省 - 西湖区"  (跳过空值 city)
            'company': '阿里巴巴',
            'title': '产品经理',
            # personInfo: "赵六 · 阿里巴巴 · 产品经理"
        }
    },
]

# 为每条记录预计算 compositeText 值
composite_configs = {
    'fullAddress': {'sourceFields': ['province', 'city', 'district'], 'separator': ' - '},
    'personInfo':  {'sourceFields': ['name', 'company', 'title'],     'separator': ' · '},
}
for rec in test_records:
    d = rec['data']
    for field_name, cfg in composite_configs.items():
        d[field_name] = _composite(cfg['sourceFields'], cfg['separator'], d)

for rec in test_records:
    data_json = json.dumps(rec['data'], ensure_ascii=False)
    cur.execute('SELECT id FROM dynamic_data WHERE id = %s AND collection = %s', (rec['id'], collection))
    if cur.fetchone():
        cur.execute('UPDATE dynamic_data SET data = %s WHERE id = %s AND collection = %s',
            (data_json, rec['id'], collection))
    else:
        cur.execute('INSERT INTO dynamic_data (id, collection, data) VALUES (%s, %s, %s)',
            (rec['id'], collection, data_json))

print(f'创建 {len(test_records)} 条测试数据')

# ============================================================
# 4. 打印验证信息
# ============================================================
print('\n===== compositeText 验证预期 =====')
print()
for rec in test_records:
    d = rec['data']
    # 模拟拼接逻辑
    addr_parts = [d.get('province', ''), d.get('city', ''), d.get('district', '')]
    addr_parts = [v for v in addr_parts if v]
    full_addr = ' - '.join(addr_parts) if addr_parts else ''

    info_parts = [d.get('name', ''), d.get('company', ''), d.get('title', '')]
    info_parts = [v for v in info_parts if v]
    person_info = ' · '.join(info_parts) if info_parts else ''

    print(f"  {rec['id']} ({d['name']})")
    print(f"    fullAddress = \"{full_addr}\"")
    print(f"    personInfo  = \"{person_info}\"")
    print()

conn.commit()
conn.close()
print('测试数据创建完成!')
