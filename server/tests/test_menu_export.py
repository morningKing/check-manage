"""
菜单级导出工具函数单元测试

测试 get_menu_collections, get_menu_collections_with_info, execute_menu_export。
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, call
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.menu_export import (
    get_menu_collections,
    get_menu_collections_with_info,
    execute_menu_export,
)


# ==================== get_menu_collections ====================

class TestGetMenuCollections:
    """测试递归获取菜单下的所有 collection"""

    def test_single_leaf_menu(self):
        """单个叶子菜单返回其 collection"""
        mock_cur = MagicMock()
        # 模拟菜单有 page_id
        mock_cur.fetchone.return_value = ('page-inspection-case',)
        # 无子菜单
        mock_cur.fetchall.return_value = []

        result = get_menu_collections(mock_cur, 'menu-1')

        assert result == ['inspection-case']

    def test_menu_without_page_id(self):
        """没有 page_id 的菜单返回空列表"""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (None,)  # 无 page_id
        mock_cur.fetchall.return_value = []

        result = get_menu_collections(mock_cur, 'menu-no-page')

        assert result == []

    def test_menu_with_static_page_id(self):
        """静态页面（不以 page- 开头）不返回 collection"""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ('system-config',)  # 静态页面
        mock_cur.fetchall.return_value = []

        result = get_menu_collections(mock_cur, 'menu-static')

        assert result == []

    def test_parent_menu_with_children(self):
        """父菜单递归获取所有子菜单的 collection"""
        mock_cur = MagicMock()
        # 父菜单无 page_id，然后 2 个子菜单各有 page_id
        mock_cur.fetchone.side_effect = [
            (None,),  # 父菜单 page_id
            ('page-child-1',),  # 子菜单1 page_id
            ('page-child-2',),  # 子菜单2 page_id
        ]
        # 父菜单有2个子菜单，子菜单无子菜单
        mock_cur.fetchall.side_effect = [
            [('menu-child-1',), ('menu-child-2',)],  # 父菜单的子菜单列表
            [],  # 子菜单1无子菜单
            [],  # 子菜单2无子菜单
        ]

        result = get_menu_collections(mock_cur, 'menu-parent')

        assert sorted(result) == ['child-1', 'child-2']

    def test_nested_menu_structure(self):
        """三层菜单结构正确递归"""
        mock_cur = MagicMock()
        # 菜单结构：root -> level1 -> level2（叶子）
        mock_cur.fetchone.side_effect = [
            (None,),  # root 无 page_id
            (None,),  # level1 无 page_id
            ('page-leaf',),  # level2 有 page_id
        ]
        mock_cur.fetchall.side_effect = [
            [('menu-level1',)],  # root 的子菜单
            [('menu-level2',)],  # level1 的子菜单
            [],  # level2 无子菜单
        ]

        result = get_menu_collections(mock_cur, 'menu-root')

        assert result == ['leaf']


# ==================== get_menu_collections_with_info ====================

class TestGetMenuCollectionsWithInfo:
    """测试获取菜单下所有页面的详细信息"""

    def test_returns_page_info_with_record_count(self):
        """返回包含记录数的页面信息"""
        mock_cur = MagicMock()
        # 模拟数据
        mock_cur.fetchone.side_effect = [
            ('page-test-collection',),  # page_id
            ('测试页面',),  # page_configs.name
            (100,),  # COUNT(*)
        ]
        mock_cur.fetchall.return_value = []  # 无子菜单

        result = get_menu_collections_with_info(mock_cur, 'menu-1')

        assert len(result) == 1
        assert result[0]['collection'] == 'test-collection'
        assert result[0]['pageName'] == '测试页面'
        assert result[0]['recordCount'] == 100

    def test_missing_page_config_uses_collection_as_name(self):
        """缺少页面配置时使用 collection 作为名称"""
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [
            ('page-orphan',),  # page_id
            None,  # page_configs 不存在
            (0,),  # COUNT(*)
        ]
        mock_cur.fetchall.return_value = []

        result = get_menu_collections_with_info(mock_cur, 'menu-1')

        assert result[0]['pageName'] == 'orphan'

    def test_aggregates_multiple_pages(self):
        """聚合多个页面的信息"""
        mock_cur = MagicMock()
        # 两个子菜单，每个有数据
        mock_cur.fetchone.side_effect = [
            (None,),  # 父菜单无 page_id
            ('page-table1',),  # 子菜单1 page_id
            ('表1',),  # 子菜单1 name
            (50,),  # 子菜单1 count
            ('page-table2',),  # 子菜单2 page_id
            ('表2',),  # 子菜单2 name
            (30,),  # 子菜单2 count
        ]
        mock_cur.fetchall.side_effect = [
            [('menu-child1',), ('menu-child2',)],  # 父菜单的子菜单
            [],  # 子菜单1无子菜单
            [],  # 子菜单2无子菜单
        ]

        result = get_menu_collections_with_info(mock_cur, 'menu-parent')

        assert len(result) == 2
        assert sum(p['recordCount'] for p in result) == 80


# ==================== execute_menu_export ====================

class TestExecuteMenuExport:
    """测试菜单级导出执行"""

    def test_menu_not_found_returns_error(self):
        """菜单不存在时返回错误"""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None  # 菜单不存在

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['non-existent'])

        assert zip_bytes is None
        assert len(errors) == 1
        assert '不存在' in errors[0]

    def test_script_not_found_returns_error(self):
        """脚本不存在时返回错误"""
        mock_cur = MagicMock()
        # 菜单存在，但脚本不存在
        mock_cur.fetchone.side_effect = [
            ('测试菜单', 'script-1'),  # 菜单信息
            None,  # 脚本不存在
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-1'], 'script-1')

        assert zip_bytes is None
        assert len(errors) == 1
        assert '不存在' in errors[0]

    def test_menu_without_data_pages_returns_error(self):
        """菜单下没有数据页面时返回错误"""
        mock_cur = MagicMock()
        # 菜单信息 -> 脚本信息 -> get_menu_collections 调用
        mock_cur.fetchone.side_effect = [
            ('测试菜单', 'script-1'),  # 菜单信息
            ('脚本名', 'result = json.dumps(data)', 'json', 'id', 'page'),  # 脚本信息（5列）
            (None,),  # get_menu_collections: 无 page_id
        ]
        mock_cur.fetchall.return_value = []  # 无子菜单

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-1'])

        assert zip_bytes is None
        assert len(errors) == 1
        assert '没有数据页面' in errors[0]

    def test_page_scope_script_with_data(self):
        """表级脚本处理有数据的表"""
        mock_cur = MagicMock()

        # 完整的调用序列：
        # 1. 菜单信息 (name, export_script_id)
        # 2. 脚本信息 (id, name, script, output_format, scope)
        # 3. get_menu_collections:
        #    - fetchone for page_id
        #    - fetchall for children (empty)
        # 4. For the collection (page scope):
        #    - fetchone for page_configs
        #    - fetchall for data records
        mock_cur.fetchone.side_effect = [
            ('测试菜单', 'script-1'),  # 1. 菜单信息
            ('JSON脚本', 'id-1', 'result = json.dumps(data)', 'json', 'page'),  # 2. 脚本信息
            ('page-test-col',),  # 3a. get_menu_collections: page_id
            ('测试页面', []),  # 4a. page_configs
        ]
        mock_cur.fetchall.side_effect = [
            [],  # 3b. 无子菜单
            [('id-1', 'test-col', {'name': 'test'}, datetime.now(timezone.utc))],  # 4b. 数据记录
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-1'], 'script-1')

        # 应该生成 ZIP
        assert zip_bytes is not None
        assert filename.endswith('.zip')
        assert len(errors) == 0

    def test_menu_scope_script_receives_all_data(self):
        """菜单级脚本接收所有数据表的数据"""
        mock_cur = MagicMock()

        # 调用序列：
        # 1. 菜单信息
        # 2. 脚本信息 (scope='menu')
        # 3. get_menu_collections: 父菜单无 page_id -> 有2个子菜单
        #    - 子菜单1 page_id
        #    - 子菜单1 children (empty)
        #    - 子菜单2 page_id
        #    - 子菜单2 children (empty)
        # 4. For menu scope, for each collection:
        #    - page_configs
        #    - data records
        mock_cur.fetchone.side_effect = [
            ('测试菜单', 'script-1'),  # 1. 菜单信息
            ('菜单脚本', 'id-1', 'result = json.dumps(menu_data)', 'json', 'menu'),  # 2. 脚本 (scope='menu')
            (None,),  # 3a. 父菜单无 page_id
            ('page-col1',),  # 3b. 子菜单1 page_id
            ('page-col2',),  # 3c. 子菜单2 page_id
            ('页面1', []),  # 4a. col1 page_configs
            ('页面2', []),  # 4b. col2 page_configs
        ]
        mock_cur.fetchall.side_effect = [
            [('child1',), ('child2',)],  # 父菜单的子菜单
            [],  # 子菜单1无子菜单
            [],  # 子菜单2无子菜单
            [('id-1', 'col1', {'name': 'test1'}, datetime.now(timezone.utc))],  # col1 数据
            [('id-2', 'col2', {'name': 'test2'}, datetime.now(timezone.utc))],  # col2 数据
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-1'], 'script-1')

        assert zip_bytes is not None
        assert filename.endswith('.zip')


# ==================== ZIP 结构测试 ====================

class TestMenuExportZipStructure:
    """测试 ZIP 文件结构"""

    def test_page_scope_creates_separate_files(self):
        """表级脚本为每个表创建单独文件"""
        mock_cur = MagicMock()

        # 调用序列：
        # 1. 菜单信息
        # 2. 脚本信息 (scope='page')
        # 3. get_menu_collections: 父菜单无 page_id -> 2个子菜单
        # 4. For each collection (page scope):
        #    - page_configs
        #    - data records
        mock_cur.fetchone.side_effect = [
            ('父菜单', 'script-1'),  # 1. 菜单
            ('JSON脚本', 'id-1', 'result = json.dumps(data)', 'json', 'page'),  # 2. 脚本
            (None,),  # 3a. 父菜单无 page_id
            ('page-col-a',),  # 3b. 子菜单A page_id
            ('page-col-b',),  # 3c. 子菜单B page_id
            ('页面A', []),  # 4a. col-a page_configs
            ('页面B', []),  # 4b. col-b page_configs
        ]
        mock_cur.fetchall.side_effect = [
            [('child-a',), ('child-b',)],  # 父菜单的子菜单
            [],  # 子菜单A无子菜单
            [],  # 子菜单B无子菜单
            [('id-1', 'col-a', {'name': 'record1'}, datetime.now(timezone.utc))],  # 数据A
            [('id-2', 'col-b', {'name': 'record2'}, datetime.now(timezone.utc))],  # 数据B
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-parent'], 'script-1')

        assert zip_bytes is not None
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            names = zf.namelist()
            # 每个表一个文件
            assert len(names) == 2

    def test_menu_scope_multi_file_output(self):
        """菜单级脚本可输出多个文件"""
        mock_cur = MagicMock()

        multi_file_script = '''
result = [
    {'filename': 'custom1.json', 'content': '{"a": 1}'},
    {'filename': 'custom2.json', 'content': '{"b": 2}'},
]
'''
        # 调用序列：
        # 1. 菜单信息
        # 2. 脚本信息 (scope='menu')
        # 3. get_menu_collections: 有1个子菜单
        # 4. For menu scope, for collection:
        #    - page_configs
        #    - data records
        mock_cur.fetchone.side_effect = [
            ('测试菜单', 'script-1'),  # 1. 菜单
            ('多文件脚本', 'id-1', multi_file_script, 'json', 'menu'),  # 2. 脚本
            (None,),  # 3a. 父菜单无 page_id
            ('page-col',),  # 3b. 子菜单 page_id
            ('页面', []),  # 4a. page_configs
        ]
        mock_cur.fetchall.side_effect = [
            [('child',)],  # 子菜单
            [],  # 子菜单无子菜单
            [('id-1', 'col', {'name': 'test'}, datetime.now(timezone.utc))],  # 数据
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        zip_bytes, filename, errors = execute_menu_export(mock_conn, ['menu-1'], 'script-1')

        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            names = zf.namelist()
            assert any('custom1.json' in n for n in names)
            assert any('custom2.json' in n for n in names)