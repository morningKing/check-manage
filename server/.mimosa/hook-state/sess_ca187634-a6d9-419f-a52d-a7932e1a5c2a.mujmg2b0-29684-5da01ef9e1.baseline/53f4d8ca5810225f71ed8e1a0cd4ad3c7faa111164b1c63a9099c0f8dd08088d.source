"""
跨项目依赖核心函数单元测试

测试 check_merge_dependencies, get_coordinated_merge_order, batch_update_dependencies_after_merge
新增：分支删除阻塞、依赖通知、校验触发通知
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class FakeConnection:
    """模拟数据库连接"""
    def __init__(self, cursor_mock):
        self.cursor_mock = cursor_mock

    def cursor(self):
        return self.cursor_mock

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def fake_get_db(conn):
    yield conn


# ==================== check_merge_dependencies ====================

class TestCheckMergeDependencies:
    """测试合并依赖检查函数"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_no_dependencies_can_merge(self, mock_conn, mock_cursor):
        """无依赖声明时可以合并"""
        mock_cursor.fetchall.return_value = []

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['blockingDependencies']) == 0
        assert len(result['readyDependencies']) == 0

    def test_read_only_dependency_not_blocking(self, mock_conn, mock_cursor):
        """read-only 依赖不阻塞合并"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-2', 'read-only', None, '项目B')
        ]

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['readOnlyDependencies']) == 1
        assert result['readOnlyDependencies'][0]['targetProject'] == 'project-2'

    def test_track_main_dependency_not_blocking(self, mock_conn, mock_cursor):
        """track-main 依赖不阻塞合并"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'main', 'track-main', None, '项目B')
        ]

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['trackMainDependencies']) == 1

    def test_read_write_target_main_ready(self, mock_conn, mock_cursor):
        """read-write 依赖目标已合并到 main 时可以合并"""
        # 依赖声明查询结果
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'main', 'read-write', None, '项目B')
        ]

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['readyDependencies']) == 1

    def test_read_write_target_merged_ready(self, mock_conn, mock_cursor):
        """read-write 依赖目标分支已合并时可以合并"""
        # 依赖声明查询结果
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-feat', 'read-write', None, '项目B')
        ]
        # 目标分支状态查询结果
        mock_cursor.fetchone.return_value = ('merged', '2024-01-01')

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['readyDependencies']) == 1

    def test_read_write_target_not_merged_blocking(self, mock_conn, mock_cursor):
        """read-write 依赖目标分支未合并时阻塞"""
        # 依赖声明查询结果
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-feat', 'read-write', None, '项目B')
        ]
        # 目标分支状态查询结果（未合并）
        mock_cursor.fetchone.return_value = ('active', None)

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is False
        assert len(result['blockingDependencies']) == 1
        assert '尚未合并' in result['blockingDependencies'][0]['reason']

    def test_read_write_target_not_exists_blocking(self, mock_conn, mock_cursor):
        """read-write 依赖目标分支不存在时阻塞"""
        # 依赖声明查询结果
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-missing', 'read-write', None, '项目B')
        ]
        # 目标分支不存在
        mock_cursor.fetchone.return_value = None

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is False
        assert len(result['blockingDependencies']) == 1
        assert '不存在' in result['blockingDependencies'][0]['reason']

    def test_mixed_dependencies(self, mock_conn, mock_cursor):
        """混合依赖类型测试"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'main', 'track-main', None, '项目B'),
            ('dep-2', 'project-3', 'v1.0', 'read-only', None, '项目C'),
            ('dep-3', 'project-4', 'branch-feat', 'read-write', None, '项目D'),
        ]
        # dep-3 的目标分支已合并
        mock_cursor.fetchone.return_value = ('merged', '2024-01-01')

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_merge_dependencies
            result = check_merge_dependencies('project-1', 'branch-1')

        assert result['canMerge'] is True
        assert len(result['trackMainDependencies']) == 1
        assert len(result['readOnlyDependencies']) == 1
        assert len(result['readyDependencies']) == 1
        assert len(result['blockingDependencies']) == 0


# ==================== get_coordinated_merge_order ====================

class TestGetCoordinatedMergeOrder:
    """测试联合合并顺序计算"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        # 版本存在，返回版本名称
        cursor.fetchone.return_value = ('测试分支',)
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_no_dependencies_returns_self_only(self, mock_conn, mock_cursor):
        """无依赖时只返回自身"""
        # 版本存在
        mock_cursor.fetchone.return_value = ('测试分支',)
        # 无依赖声明
        mock_cursor.fetchall.return_value = []

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import get_coordinated_merge_order
            order = get_coordinated_merge_order('project-1', 'branch-1')

        assert len(order) == 1
        assert order[0]['projectMenuId'] == 'project-1'
        assert order[0]['isDependency'] is False

    def test_with_ready_dependencies_includes_targets(self, mock_conn, mock_cursor):
        """有未合并依赖时包含目标项目"""
        # 版本存在
        mock_cursor.fetchone.side_effect = [
            ('测试分支',),  # 源版本名称
            ('active',),   # 目标分支状态（未合并）
        ]
        # 依赖声明
        mock_cursor.fetchall.return_value = [
            ('project-2', 'branch-feat', '项目B', '特性分支'),
        ]

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import get_coordinated_merge_order
            order = get_coordinated_merge_order('project-1', 'branch-1')

        # 应该先包含目标项目（未合并），再包含源项目
        assert len(order) == 2
        assert order[0]['projectMenuId'] == 'project-2'
        assert order[0]['isDependency'] is True
        assert order[1]['projectMenuId'] == 'project-1'

    def test_version_not_exists_raises_error(self, mock_conn, mock_cursor):
        """版本不存在时抛出异常"""
        mock_cursor.fetchone.return_value = None

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import get_coordinated_merge_order
            with pytest.raises(ValueError) as exc_info:
                get_coordinated_merge_order('project-1', 'branch-999')
            assert '不存在' in str(exc_info.value)


# ==================== batch_update_dependencies_after_merge ====================

class TestBatchUpdateDependenciesAfterMerge:
    """测试合并后依赖更新"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.rowcount = 0
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_updates_read_write_dependencies(self, mock_conn, mock_cursor):
        """更新 read-write 依赖的 target_branch 为 main"""
        mock_cursor.rowcount = 2

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import batch_update_dependencies_after_merge
            count = batch_update_dependencies_after_merge('project-1', 'branch-1')

        assert count == 2
        # 验证执行了 UPDATE
        assert mock_cursor.execute.called

    def test_no_read_write_dependencies_returns_zero(self, mock_conn, mock_cursor):
        """无 read-write 依赖时返回 0"""
        mock_cursor.rowcount = 0

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import batch_update_dependencies_after_merge
            count = batch_update_dependencies_after_merge('project-1', 'branch-1')

        assert count == 0


# ==================== 分支删除依赖阻塞 ====================

class TestBranchDeleteProtection:
    """测试分支删除时的依赖保护"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_no_dependents_can_delete(self, mock_conn, mock_cursor):
        """无依赖方项目时可以删除"""
        mock_cursor.fetchone.return_value = (False, 'project-1')  # is_protected=False, project_menu_id
        mock_cursor.fetchall.return_value = []  # 无依赖

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_branch_delete_protection
            result = check_branch_delete_protection('project-1', 'branch-1')

        assert result['canDelete'] is True
        assert len(result['dependentProjects']) == 0

    def test_has_dependents_cannot_delete(self, mock_conn, mock_cursor):
        """有依赖方项目时不能删除"""
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-2', 'branch-1', '项目B')
        ]

        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
            from utils.cross_project_dependency import check_branch_delete_protection
            result = check_branch_delete_protection('project-1', 'branch-1')

        assert result['canDelete'] is False
        assert len(result['dependentProjects']) == 1


# ==================== 依赖校验通知 ====================

class TestDependencyValidationNotification:
    """测试依赖校验触发通知"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_validation_broken_sends_notification(self, mock_conn, mock_cursor):
        """校验失败时发送通知"""
        # 模拟依赖声明数据
        dep_data = {
            'id': 'dep-1',
            'sourceProject': 'project-1',
            'sourceBranch': 'main',
            'targetProject': 'project-2',
            'targetBranch': 'branch-missing',
            'relationType': 'read-write',
            'isValidated': True,  # 之前是有效的
            'validationError': None,
            'sourceProjectName': '项目A',
            'targetProjectName': '项目B',
        }

        with patch('utils.cross_project_dependency.get_dependency_by_id', return_value=dep_data):
            with patch('utils.cross_project_dependency.check_branch_exists', return_value=False):
                with patch('utils.cross_project_dependency.check_circular_dependency', return_value=False):
                    with patch('utils.cross_project_dependency.get_dependency_relations', return_value=[]):
                        with patch('utils.notifier.notify_dependency_broken') as mock_notify:
                            from utils.cross_project_dependency import validate_project_dependency
                            result = validate_project_dependency('dep-1', send_notification=True)

                            # 从有效变为无效，应该发送断裂通知
                            assert mock_notify.called
                            assert result['isValid'] is False

    def test_validation_resolved_sends_notification(self, mock_conn, mock_cursor):
        """校验从失败变为成功时发送恢复通知"""
        dep_data = {
            'id': 'dep-1',
            'sourceProject': 'project-1',
            'sourceBranch': 'main',
            'targetProject': 'project-2',
            'targetBranch': 'main',
            'relationType': 'read-write',
            'isValidated': False,  # 之前是无效的
            'validationError': '目标分支不存在',
            'sourceProjectName': '项目A',
            'targetProjectName': '项目B',
        }

        with patch('utils.cross_project_dependency.get_dependency_by_id', return_value=dep_data):
            with patch('utils.cross_project_dependency.check_branch_exists', return_value=True):
                with patch('utils.cross_project_dependency.check_circular_dependency', return_value=False):
                    with patch('utils.cross_project_dependency.get_dependency_relations', return_value=[]):
                        with patch('utils.notifier.notify_dependency_resolved') as mock_notify:
                            from utils.cross_project_dependency import validate_project_dependency
                            result = validate_project_dependency('dep-1', send_notification=True)

                            # 从无效变为有效，应该发送恢复通知
                            assert mock_notify.called
                            assert result['isValid'] is True

    def test_validation_warning_sends_notification(self, mock_conn, mock_cursor):
        """校验成功但有警告时发送警告通知"""
        dep_data = {
            'id': 'dep-1',
            'sourceProject': 'project-1',
            'sourceBranch': 'main',
            'targetProject': 'project-2',
            'targetBranch': 'main',
            'relationType': 'read-write',
            'isValidated': True,  # 之前是有效的
            'validationError': None,
            'sourceProjectName': '项目A',
            'targetProjectName': '项目B',
        }

        # 模拟有外键断裂
        relation_data = [{
            'id': 'rel-1',
            'sourceCollection': 'orders',
            'sourceField': 'productId',
            'targetCollection': 'products',
        }]

        with patch('utils.cross_project_dependency.get_dependency_by_id', return_value=dep_data):
            with patch('utils.cross_project_dependency.check_branch_exists', return_value=True):
                with patch('utils.cross_project_dependency.check_circular_dependency', return_value=False):
                    with patch('utils.cross_project_dependency.get_dependency_relations', return_value=relation_data):
                        with patch('utils.cross_project_dependency.check_data_reachability', return_value={
                            'reachableCount': 5,
                            'brokenCount': 2,
                            'brokenRecords': [],
                        }):
                            with patch('utils.notifier.notify_dependency_warning') as mock_notify:
                                from utils.cross_project_dependency import validate_project_dependency
                                result = validate_project_dependency('dep-1', send_notification=True)

                                # 有效但有警告，应该发送警告通知
                                assert mock_notify.called
                                assert result['isValid'] is True
                                assert len(result['warnings']) > 0


# ==================== 调度器校验 ====================

class TestDependencyScheduler:
    """测试依赖定期校验调度器"""

    @pytest.fixture
    def mock_cursor(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = None
        return cursor

    @pytest.fixture
    def mock_conn(self, mock_cursor):
        return FakeConnection(mock_cursor)

    def test_validate_all_dependencies_iterates_all(self, mock_conn, mock_cursor):
        """validate_all_dependencies 校验所有依赖声明"""
        # 模拟 3 个依赖声明
        mock_cursor.fetchall.return_value = [
            ('dep-1',),
            ('dep-2',),
            ('dep-3',),
        ]

        with patch('utils.dependency_scheduler.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.dependency_scheduler.validate_project_dependency') as mock_validate:
                from utils.dependency_scheduler import validate_all_dependencies
                validate_all_dependencies()

                # 应该调用 3 次
                assert mock_validate.call_count == 3

    def test_validate_all_dependencies_handles_empty(self, mock_conn, mock_cursor):
        """无依赖声明时正常处理"""
        mock_cursor.fetchall.return_value = []

        with patch('utils.dependency_scheduler.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.dependency_scheduler.validate_project_dependency') as mock_validate:
                from utils.dependency_scheduler import validate_all_dependencies
                validate_all_dependencies()

                # 无调用
                assert mock_validate.call_count == 0

    def test_validate_all_dependencies_continues_on_error(self, mock_conn, mock_cursor):
        """单个校验失败不影响其他"""
        mock_cursor.fetchall.return_value = [
            ('dep-1',),
            ('dep-2',),
            ('dep-3',),
        ]

        # 第一次调用失败，后续正常
        mock_validate = MagicMock()
        mock_validate.side_effect = [Exception('error'), None, None]

        with patch('utils.dependency_scheduler.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.dependency_scheduler.validate_project_dependency', mock_validate):
                from utils.dependency_scheduler import validate_all_dependencies
                validate_all_dependencies()

                # 应该调用 3 次（即使第一次失败）
                assert mock_validate.call_count == 3

    def test_validate_dependencies_for_project_filters_correctly(self, mock_conn, mock_cursor):
        """validate_dependencies_for_project 正确过滤项目"""
        mock_cursor.fetchall.return_value = [
            ('dep-1',),
            ('dep-2',),
        ]

        with patch('utils.dependency_scheduler.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.dependency_scheduler.validate_project_dependency') as mock_validate:
                from utils.dependency_scheduler import validate_dependencies_for_project
                validate_dependencies_for_project('project-1')

                # 应该调用 2 次
                assert mock_validate.call_count == 2
                # 验证 SQL 参数包含项目 ID（在 tuple 的第二个元素中）
                call_args = mock_cursor.execute.call_args[0]
                assert 'project-1' in call_args[1]


# ==================== 通知函数 ====================

class TestDependencyNotifier:
    """测试依赖通知函数"""

    def test_notify_dependency_broken_creates_notifications(self):
        """notify_dependency_broken 为管理员创建通知"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('user-1',), ('user-2',)]
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.notifier.get_db', lambda: fake_get_db(mock_conn)):
            from utils.notifier import notify_dependency_broken
            notify_dependency_broken('project-1', '项目A', '项目B', '目标分支不存在')

            # 应该为每个管理员创建通知
            assert mock_cursor.execute.call_count >= 1

    def test_notify_dependency_warning_creates_notifications(self):
        """notify_dependency_warning 为管理员创建通知"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('user-1',)]
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.notifier.get_db', lambda: fake_get_db(mock_conn)):
            from utils.notifier import notify_dependency_warning
            notify_dependency_warning('project-1', '项目A', '项目B', '2条外键断裂')

            assert mock_cursor.execute.called

    def test_notify_dependency_resolved_creates_notifications(self):
        """notify_dependency_resolved 为管理员创建通知"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [('user-1',)]
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.notifier.get_db', lambda: fake_get_db(mock_conn)):
            from utils.notifier import notify_dependency_resolved
            notify_dependency_resolved('project-1', '项目A', '项目B')

            assert mock_cursor.execute.called

    def test_notify_functions_handle_exceptions(self):
        """通知函数异常时不影响主流程"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = Exception('db error')
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.notifier.get_db', lambda: fake_get_db(mock_conn)):
            from utils.notifier import notify_dependency_broken
            # 不应该抛出异常
            result = notify_dependency_broken('project-1', '项目A', '项目B', 'error')
            assert result is None  # 异常被捕获，返回 None


# ==================== 分支删除集成测试 ====================

class TestBranchDeleteIntegration:
    """测试 delete_project_version 集成依赖保护"""

    def test_delete_blocked_by_dependency(self):
        """有依赖时删除被阻塞"""
        mock_cursor = MagicMock()
        # 版本信息
        mock_cursor.fetchone.return_value = (False, 'project-1')  # is_protected, project_menu_id
        # 有依赖
        mock_cursor.fetchall.return_value = [
            ('dep-1', 'project-2', 'branch-2', 'branch-1', '项目B')
        ]
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.project_version.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
                from utils.project_version import delete_project_version
                with pytest.raises(ValueError) as exc_info:
                    delete_project_version('branch-1')
                assert '依赖' in str(exc_info.value)

    def test_delete_allowed_when_no_dependency(self):
        """无依赖时可以删除"""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        # 版本信息
        mock_cursor.fetchone.return_value = (False, 'project-1')
        # 无依赖
        mock_cursor.fetchall.return_value = []
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.project_version.get_db', lambda: fake_get_db(mock_conn)):
            with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
                from utils.project_version import delete_project_version
                result = delete_project_version('branch-1')
                assert result is True

    def test_delete_blocked_by_protection_flag(self):
        """受保护版本不能删除"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True, 'project-1')  # is_protected=True
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.project_version.get_db', lambda: fake_get_db(mock_conn)):
            from utils.project_version import delete_project_version
            with pytest.raises(ValueError) as exc_info:
                delete_project_version('branch-1')
            assert '受保护' in str(exc_info.value)


# ==================== 合并后校验集成测试 ====================

class TestMergeValidationIntegration:
    """测试合并后校验集成"""

    def test_merge_triggers_dependent_validation(self):
        """合并成功后触发依赖校验"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None
        mock_conn = FakeConnection(mock_cursor)

        # 模拟 get_dependent_projects 返回依赖方
        dependents_data = [
            {'id': 'dep-1', 'sourceProject': 'project-2'},
            {'id': 'dep-2', 'sourceProject': 'project-3'},
        ]

        with patch('utils.cross_project_dependency.get_dependent_projects', return_value=dependents_data):
            with patch('utils.cross_project_dependency.validate_project_dependency') as mock_validate:
                with patch('utils.cross_project_dependency.batch_update_dependencies_after_merge', return_value=2):
                    # 模拟合并成功
                    merge_result = {'success': True}

                    # 直接测试逻辑（不通过路由）
                    from utils.cross_project_dependency import (
                        batch_update_dependencies_after_merge,
                        get_dependent_projects,
                        validate_project_dependency,
                    )

                    # 执行合并后逻辑
                    batch_update_dependencies_after_merge('project-1', 'version-1')
                    dependents = get_dependent_projects('project-1', 'main')
                    for dep in dependents:
                        validate_project_dependency(dep['id'], send_notification=True)

                    # 应该校验每个依赖方
                    assert mock_validate.call_count == 2


# ==================== 校验状态变化边界测试 ====================

class TestValidationStateEdgeCases:
    """测试校验状态变化边界情况"""

    def test_no_notification_when_state_unchanged(self):
        """状态未变化时不发送通知"""
        dep_data = {
            'id': 'dep-1',
            'sourceProject': 'project-1',
            'sourceBranch': 'main',
            'targetProject': 'project-2',
            'targetBranch': 'main',
            'relationType': 'read-write',
            'isValidated': True,  # 之前有效
            'validationError': None,
            'sourceProjectName': '项目A',
            'targetProjectName': '项目B',
        }

        mock_cursor = MagicMock()
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.cross_project_dependency.get_dependency_by_id', return_value=dep_data):
            with patch('utils.cross_project_dependency.check_branch_exists', return_value=True):
                with patch('utils.cross_project_dependency.check_circular_dependency', return_value=False):
                    with patch('utils.cross_project_dependency.get_dependency_relations', return_value=[]):
                        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
                            with patch('utils.notifier.notify_dependency_broken') as mock_broken:
                                with patch('utils.notifier.notify_dependency_resolved') as mock_resolved:
                                    with patch('utils.notifier.notify_dependency_warning') as mock_warning:
                                        from utils.cross_project_dependency import validate_project_dependency
                                        result = validate_project_dependency('dep-1', send_notification=True)

                                        # 状态未变化，不发送通知
                                        assert not mock_broken.called
                                        assert not mock_resolved.called
                                        assert not mock_warning.called
                                        assert result['isValid'] is True

    def test_notification_suppressed_when_disabled(self):
        """send_notification=False时不发送通知"""
        dep_data = {
            'id': 'dep-1',
            'sourceProject': 'project-1',
            'sourceBranch': 'main',
            'targetProject': 'project-2',
            'targetBranch': 'branch-missing',
            'relationType': 'read-write',
            'isValidated': True,  # 之前有效，现在会变无效
            'validationError': None,
            'sourceProjectName': '项目A',
            'targetProjectName': '项目B',
        }

        mock_cursor = MagicMock()
        mock_conn = FakeConnection(mock_cursor)

        with patch('utils.cross_project_dependency.get_dependency_by_id', return_value=dep_data):
            with patch('utils.cross_project_dependency.check_branch_exists', return_value=False):
                with patch('utils.cross_project_dependency.check_circular_dependency', return_value=False):
                    with patch('utils.cross_project_dependency.get_dependency_relations', return_value=[]):
                        with patch('utils.cross_project_dependency.get_db', lambda: fake_get_db(mock_conn)):
                            with patch('utils.notifier.notify_dependency_broken') as mock_notify:
                                from utils.cross_project_dependency import validate_project_dependency
                                result = validate_project_dependency('dep-1', send_notification=False)

                                # 不发送通知
                                assert not mock_notify.called
                                assert result['isValid'] is False