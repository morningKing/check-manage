from flask import g, jsonify
from utils.permissions import can_page


def require_page_action(collection, action):
    """Return a (response, 403) tuple if the current user's role lacks `action`
    on this collection's page, else None. Read role from g.current_user."""
    user = getattr(g, 'current_user', {}) or {}
    role = user.get('role')
    if not can_page(role, f'page-{collection}', action):
        return jsonify({'error': '权限不足'}), 403
    return None
