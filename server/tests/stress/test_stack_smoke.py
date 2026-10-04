"""压测栈冒烟：建库→init_db→起后端→登录→查询不变量→拆栈。唯一目的 =
证明 conftest 的编排器自身可用；运行约 30-60s。"""
import pytest

pytestmark = pytest.mark.stress


def test_stack_boots_and_login_works(stress_stack):
    import requests
    r = requests.get(f'{stress_stack.base}/health', timeout=5)
    assert r.status_code in (200, 401)          # 活着即可（health 可能带鉴权）
    assert stress_stack.token                    # admin 登录成功
    rows = stress_stack.db_query(
        "SELECT count(*) FROM users WHERE username='admin'")
    assert rows[0][0] == 1


def test_invariants_clean_on_fresh_stack(stress_stack):
    inv = stress_stack.invariants()
    assert inv['orphans'] == 0 and inv['zombie_running'] == 0
