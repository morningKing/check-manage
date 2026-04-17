"""
测试 delete_version 用户检查功能

Critical fix: 删除版本前检查是否有用户正在使用该分支

使用直接数据库插入方式，绕过 create_version_snapshot 的快照大小限制
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db
import psycopg2.extras
from datetime import datetime, timezone
import uuid


def _create_test_version_directly(version_id, collection, name, created_by):
    """直接插入版本数据，绕过快照限制"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO collection_versions '
            '(id, name, description, collection, version_type, status, created_by, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (version_id, name, f'测试版本-{name}', collection, 'branch', 'active', created_by, datetime.now(timezone.utc))
        )
        # 追踪collection
        cur.execute(
            'INSERT INTO version_collections (version_id, collection, created_at) '
            'VALUES (%s, %s, %s)',
            (version_id, collection, datetime.now(timezone.utc))
        )
        conn.commit()


def _cleanup_test_version(version_id):
    """清理测试版本数据"""
    with get_db() as conn:
        cur = conn.cursor()
        # 清理用户分支设置
        cur.execute('DELETE FROM user_current_branch WHERE branch_id = %s', (version_id,))
        # 清理动态数据
        cur.execute('DELETE FROM dynamic_data WHERE branch_id = %s', (version_id,))
        # 清理关联关系
        cur.execute('DELETE FROM data_relations WHERE branch_id = %s', (version_id,))
        # 清理版本追踪
        cur.execute('DELETE FROM version_collections WHERE version_id = %s', (version_id,))
        # 清理快照（如果有）
        cur.execute('DELETE FROM version_snapshots WHERE version_id = %s', (version_id,))
        # 清理版本元数据
        cur.execute('DELETE FROM collection_versions WHERE id = %s', (version_id,))
        conn.commit()


def test_get_users_on_branch_signature():
    """测试 get_users_on_branch 新签名"""
    from utils.version import get_users_on_branch

    version_id = f'test-sig-{uuid.uuid4().hex[:8]}'
    collection = 'test-collection-sig'
    test_user = 'test_user_signature'

    # 1. 创建测试版本
    _create_test_version_directly(version_id, collection, '签名测试', test_user)

    # 2. 添加用户分支设置
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('ucb-sig-001', 'user-sig-001', 'alice', collection, version_id)
        )
        conn.commit()

    # 3. 测试新签名 (collection, branch_id)
    users = get_users_on_branch(collection, version_id)

    assert isinstance(users, list), '应返回列表'
    assert len(users) == 1, f'应有1个用户，实际{len(users)}'
    assert users[0] == 'alice', f'用户名应为alice，实际{users[0]}'

    # 4. 测试其他collection无用户
    other_users = get_users_on_branch('other-collection', version_id)
    assert len(other_users) == 0, '其他collection应无用户'

    # 5. 清理
    _cleanup_test_version(version_id)
    print('[OK] get_users_on_branch 新签名测试通过')


def test_delete_version_with_users_on_branch_fails():
    """测试有用户使用分支时删除失败"""
    from utils.version import delete_version, get_users_on_branch

    version_id = f'test-block-{uuid.uuid4().hex[:8]}'
    collection = 'test-collection-block'
    test_user = 'test_user_block'

    # 1. 创建测试版本
    _create_test_version_directly(version_id, collection, '阻止删除测试', test_user)

    # 2. 添加测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-block-001', collection, psycopg2.extras.Json({'name': '测试数据'}), version_id, 1)
        )
        conn.commit()

    # 3. 添加用户分支设置（模拟用户正在使用该分支）
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('ucb-block-001', 'user-block-001', 'bob', collection, version_id)
        )
        conn.commit()

    # 4. 验证用户确实在使用
    users = get_users_on_branch(collection, version_id)
    assert len(users) == 1, '应有用户在使用'
    assert 'bob' in users, 'bob应在用户列表中'

    # 5. 尝试删除（confirmed=True），应抛出 ValueError
    try:
        delete_version(version_id, confirmed=True)
        assert False, '删除应该失败，抛出 ValueError'
    except ValueError as e:
        error_msg = str(e)
        assert 'bob' in error_msg, f'错误信息应包含用户名bob: {error_msg}'
        assert '用户正在使用' in error_msg or '正在使用该分支' in error_msg, f'错误信息应包含提示: {error_msg}'
        print(f'[OK] 正确抛出错误: {error_msg}')

    # 6. 验证版本仍然存在
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM collection_versions WHERE id = %s', (version_id,))
        count = cur.fetchone()[0]
        assert count == 1, '版本应保留，未被删除'

    # 7. 验证数据仍然存在
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM dynamic_data WHERE branch_id = %s', (version_id,))
        count = cur.fetchone()[0]
        assert count == 1, '数据应保留，未被删除'

    # 8. 清理用户分支设置
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM user_current_branch WHERE id = %s', ('ucb-block-001',))
        conn.commit()

    # 9. 现在删除应该成功
    success = delete_version(version_id, confirmed=True)
    assert success == True, '移除用户后删除应成功'

    print('[OK] 用户阻止删除测试通过')


def test_delete_version_with_multiple_collections_users():
    """测试跨Collection时多个用户阻止删除"""
    from utils.version import delete_version, get_users_on_branch

    version_id = f'test-mc-{uuid.uuid4().hex[:8]}'
    collection1 = 'test-collection-mc-1'
    collection2 = 'test-collection-mc-2'
    test_user = 'test_user_multi_coll'

    # 1. 创建测试版本（跨Collection）
    _create_test_version_directly(version_id, collection1, '多Collection测试', test_user)

    # 2. 添加第二个collection追踪
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO version_collections (version_id, collection, created_at) '
            'VALUES (%s, %s, %s)',
            (version_id, collection2, datetime.now(timezone.utc))
        )
        conn.commit()

    # 3. 添加跨Collection数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-mc-001', collection1, psycopg2.extras.Json({'name': '数据1'}), version_id, 1)
        )
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-mc-002', collection2, psycopg2.extras.Json({'name': '数据2'}), version_id, 1)
        )
        conn.commit()

    # 4. 添加两个用户在不同Collection上使用同一分支
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('ucb-mc-001', 'user-mc-001', 'alice', collection1, version_id)
        )
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('ucb-mc-002', 'user-mc-002', 'bob', collection2, version_id)
        )
        conn.commit()

    # 5. 尝试删除，应抛出 ValueError（包含两个用户）
    try:
        delete_version(version_id, confirmed=True)
        assert False, '删除应该失败'
    except ValueError as e:
        error_msg = str(e)
        assert 'alice' in error_msg, f'错误信息应包含alice: {error_msg}'
        assert 'bob' in error_msg, f'错误信息应包含bob: {error_msg}'
        print(f'[OK] 正确提示多个用户: {error_msg}')

    # 6. 清理用户并删除
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM user_current_branch WHERE branch_id = %s', (version_id,))
        conn.commit()

    success = delete_version(version_id, confirmed=True)
    assert success == True, '移除用户后删除应成功'

    print('[OK] 多Collection用户阻止测试通过')


def test_delete_impact_report_includes_users():
    """测试影响报告包含用户信息"""
    from utils.version import get_version_delete_impact

    version_id = f'test-impact-{uuid.uuid4().hex[:8]}'
    collection = 'test-collection-impact'
    test_user = 'test_user_impact'

    # 1. 创建测试版本
    _create_test_version_directly(version_id, collection, '影响报告测试', test_user)

    # 2. 添加测试数据
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-impact-001', collection, psycopg2.extras.Json({'name': '测试数据'}), version_id, 1)
        )
        conn.commit()

    # 3. 添加用户分支设置
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO user_current_branch (id, user_id, username, collection, branch_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('ucb-impact-001', 'user-impact-001', 'charlie', collection, version_id)
        )
        conn.commit()

    # 4. 获取影响报告
    impact = get_version_delete_impact(version_id)

    # 5. 验证报告包含用户信息
    assert 'usersOnBranch' in impact, '报告应包含 usersOnBranch'
    assert len(impact['usersOnBranch']) == 1, f'应有1个用户，实际{len(impact["usersOnBranch"])}'

    user_info = impact['usersOnBranch'][0]
    assert user_info['username'] == 'charlie', f'用户名应为charlie: {user_info["username"]}'
    assert user_info['collection'] == collection, f'collection应为{collection}: {user_info["collection"]}'

    assert impact['hasUsersOnBranch'] == True, 'hasUsersOnBranch应为True'
    assert 'charlie' in impact['warningMessage'], '警告信息应包含用户名'

    print(f'[OK] 影响报告包含用户: {impact["usersOnBranch"]}')

    # 6. 清理
    _cleanup_test_version(version_id)
    print('[OK] 影响报告用户测试通过')


def test_delete_version_no_users_succeeds():
    """测试无用户时删除成功"""
    from utils.version import delete_version

    version_id = f'test-no-user-{uuid.uuid4().hex[:8]}'
    collection = 'test-collection-no-user'
    test_user = 'test_user_no_user'

    # 1. 创建测试版本
    _create_test_version_directly(version_id, collection, '无用户删除测试', test_user)

    # 2. 添加测试数据（但不添加用户分支设置）
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dynamic_data (id, collection, data, branch_id, version) '
            'VALUES (%s, %s, %s, %s, %s)',
            ('test-no-user-001', collection, psycopg2.extras.Json({'name': '测试数据'}), version_id, 1)
        )
        conn.commit()

    # 3. 直接删除（无用户），应成功
    success = delete_version(version_id, confirmed=True)
    assert success == True, '无用户时删除应成功'

    # 4. 验证版本已被删除
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM collection_versions WHERE id = %s', (version_id,))
        count = cur.fetchone()[0]
        assert count == 0, '版本应已删除'

    print('[OK] 无用户删除测试通过')


if __name__ == '__main__':
    print('测试 Critical fix: 用户检查功能\n')
    test_get_users_on_branch_signature()
    test_delete_version_with_users_on_branch_fails()
    test_delete_version_with_multiple_collections_users()
    test_delete_impact_report_includes_users()
    test_delete_version_no_users_succeeds()
    print('\n所有 Critical fix 测试通过！')