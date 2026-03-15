"""
版本管理部分合并功能测试

测试 apply_partial_merge 函数
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.version import apply_partial_merge
from utils.errors import MergeError, VERSION_NOT_FOUND


class TestPartialMerge:
    """部分合并测试"""

    @pytest.fixture
    def mock_db_setup(self):
        """模拟数据库设置"""
        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            # 模拟版本查询结果
            mock_cur.fetchone.side_effect = [
                ('test_collection', 'active', 'snapshot'),  # 版本信息
                ({'name': 'test'},),  # 当前记录数据
            ]
            mock_cur.fetchall.return_value = []

            yield {'conn': mock_conn, 'cur': mock_cur}

    def test_partial_merge_add_records(self, mock_db_setup):
        """测试部分合并新增记录"""
        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        # 模拟源数据
        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '新增记录'}],
                []
            )

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True
        assert result['merged_count'] == 1

    def test_partial_merge_remove_records(self, mock_db_setup):
        """测试部分合并删除记录"""
        # 设置 rowcount 模拟删除成功
        mock_db_setup['cur'].rowcount = 1

        decisions = {
            'added_record_ids': [],
            'removed_record_ids': ['record-2'],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = ([], [])

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True
        assert result['merged_count'] == 1

    def test_partial_merge_modified_records(self, mock_db_setup):
        """测试部分合并修改记录"""
        # 为修改记录测试重新设置 fetchone 返回值
        # 需要: 版本信息、当前记录数据
        mock_db_setup['cur'].fetchone.side_effect = [
            ('test_collection', 'active', 'snapshot'),  # 版本信息
            ({'name': '旧名称'},),  # 当前记录数据 (tuple with dict)
        ]

        decisions = {
            'added_record_ids': [],
            'removed_record_ids': [],
            'modified_records': [{
                'record_id': 'record-3',
                'field_values': {'name': '新名称'}
            }]
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = ([], [])

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True
        assert result['merged_count'] == 1

    def test_partial_merge_with_relations(self, mock_db_setup):
        """测试部分合并同步处理关系数据"""
        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            # load_version_data returns (records, relations_map)
            # relations_map: {record_id: {field_name: [related_ids]}}
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '关联记录'}],
                {'record-1': {'tags': ['tag-1']}}  # dict format, not list
            )

            result = apply_partial_merge(
                source_version_id='version-1',
                target_branch='main',
                decisions=decisions,
                merged_by='admin'
            )

        assert result['success'] is True

    def test_partial_merge_rollback_on_error(self, mock_db_setup):
        """测试部分合并失败时抛出异常"""
        mock_db_setup['cur'].execute.side_effect = Exception('DB Error')

        decisions = {
            'added_record_ids': ['record-1'],
            'removed_record_ids': [],
            'modified_records': []
        }

        with patch('utils.version.load_version_data') as mock_load:
            mock_load.return_value = (
                [{'id': 'record-1', 'name': '测试'}],
                []
            )

            with pytest.raises(Exception, match='DB Error'):
                apply_partial_merge(
                    source_version_id='version-1',
                    target_branch='main',
                    decisions=decisions,
                    merged_by='admin'
                )

    def test_partial_merge_version_not_found(self):
        """测试版本不存在时抛出 MergeError"""
        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            with pytest.raises(MergeError) as exc_info:
                apply_partial_merge(
                    source_version_id='nonexistent',
                    target_branch='main',
                    decisions={},
                    merged_by='admin'
                )

            assert exc_info.value.code == VERSION_NOT_FOUND

    def test_partial_merge_version_already_merged(self):
        """测试版本已合并时抛出 MergeError"""
        from utils.errors import VERSION_ALREADY_MERGED

        with patch('utils.version.get_db') as mock_get_db:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = ('test', 'merged', 'snapshot')
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__ = lambda self: self
            mock_conn.__exit__ = lambda self, *args: None
            mock_get_db.return_value = mock_conn

            with pytest.raises(MergeError) as exc_info:
                apply_partial_merge(
                    source_version_id='version-1',
                    target_branch='main',
                    decisions={},
                    merged_by='admin'
                )

            assert exc_info.value.code == VERSION_ALREADY_MERGED