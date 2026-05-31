"""Tests for utils.prompt_template CRUD helpers."""
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def user_id(db_conn):
    """Insert a throwaway user, yield its UUID, clean up after."""
    uid = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (uid, f'pt_user_{uid[:8]}', 'x', f'PT User {uid[:8]}'),
        )
    db_conn.commit()
    yield uid
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    db_conn.commit()


def test_create_returns_row_with_id(user_id):
    from utils.prompt_template import create_template
    row = create_template(user_id, name='巡检用例', content='帮我开发巡检用例')
    assert row['id']
    assert row['name'] == '巡检用例'
    assert row['content'] == '帮我开发巡检用例'
    assert row['user_id'] == user_id


def test_create_rejects_duplicate_name_for_same_user(user_id):
    from utils.prompt_template import create_template, DuplicateTemplateName
    create_template(user_id, name='dup', content='a')
    with pytest.raises(DuplicateTemplateName):
        create_template(user_id, name='dup', content='b')


def test_create_allows_same_name_for_different_users(db_conn, user_id):
    from utils.prompt_template import create_template
    other = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (other, f'pt_other_{other[:8]}', 'x', f'PT Other {other[:8]}'),
        )
    db_conn.commit()
    try:
        create_template(user_id, name='shared', content='a')
        # Should not raise
        create_template(other, name='shared', content='b')
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (other,))
            cur.execute("DELETE FROM users WHERE id = %s", (other,))
        db_conn.commit()


def test_list_returns_user_templates_ordered(user_id):
    from utils.prompt_template import create_template, list_templates
    a = create_template(user_id, name='a', content='1')
    b = create_template(user_id, name='b', content='2')
    rows = list_templates(user_id)
    ids = [r['id'] for r in rows]
    # Most recently updated first; b was created after a
    assert ids[0] == b['id']
    assert a['id'] in ids


def test_update_changes_content_and_bumps_updated_at(user_id):
    from utils.prompt_template import create_template, update_template, get_template
    row = create_template(user_id, name='x', content='old')
    update_template(user_id, row['id'], name='x', content='new')
    fresh = get_template(user_id, row['id'])
    assert fresh['content'] == 'new'
    assert fresh['updated_at'] >= row['updated_at']


def test_update_other_users_template_returns_none(user_id, db_conn):
    from utils.prompt_template import create_template, update_template
    other = str(uuid.uuid4())
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role) "
            "VALUES (%s, %s, %s, %s, 'developer')",
            (other, f'pt_other2_{other[:8]}', 'x', f'PT Other2 {other[:8]}'),
        )
    db_conn.commit()
    try:
        row = create_template(other, name='x', content='theirs')
        result = update_template(user_id, row['id'], name='hacked', content='!!')
        assert result is None  # cross-user write blocked
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_prompt_templates WHERE user_id = %s", (other,))
            cur.execute("DELETE FROM users WHERE id = %s", (other,))
        db_conn.commit()


def test_delete_removes_row(user_id):
    from utils.prompt_template import create_template, delete_template, get_template
    row = create_template(user_id, name='x', content='c')
    assert delete_template(user_id, row['id']) is True
    assert get_template(user_id, row['id']) is None
