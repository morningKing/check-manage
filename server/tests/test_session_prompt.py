"""Tests for the shared ordinary/batch session prompt contract."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_build_session_prompt_augments_inputs_but_keeps_stored_parts_raw(tmp_path, monkeypatch):
    from utils import session_prompt

    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'notes.txt').write_text('TEXT-ATTACHMENT', encoding='utf-8')
    (tmp_path / 'uploads' / 'data.bin').write_bytes(b'\x00\xffBINARY')

    monkeypatch.setattr(session_prompt, 'search_memory',
                        lambda user_id, content, limit=5: ['memory result'])
    monkeypatch.setattr(session_prompt, 'render_memory_block',
                        lambda memories: '[MEMORY]\n')
    monkeypatch.setattr(session_prompt, 'is_export_intent', lambda content: True)
    monkeypatch.setattr(session_prompt, 'resolve_collection_from_text',
                        lambda content: ('cases', 'Cases'))
    monkeypatch.setattr(session_prompt, 'export_collection_to_xlsx',
                        lambda collection, workspace_path, role=None:
                        {'path': 'outputs/cases.xlsx', 'rows': 2})

    prompt, stored = session_prompt.build_session_prompt(
        content='请处理 @uploads/notes.txt 并导出',
        workspace_path=str(tmp_path),
        attachments=['uploads/notes.txt', 'uploads/data.bin'],
        agent_mentions=[{'name': 'build'}],
        user_id='user-1',
        role='developer',
    )

    assert '[系统规则]' in prompt
    assert '[MEMORY]' in prompt
    assert 'TEXT-ATTACHMENT' in prompt
    assert 'data.bin' in prompt
    assert 'outputs/cases.xlsx' in prompt
    assert '@uploads/notes.txt' in prompt
    assert stored == [
        {'type': 'text', 'text': '请处理 @uploads/notes.txt 并导出'},
        {'type': 'file', 'name': 'notes.txt', 'path': 'uploads/notes.txt'},
        {'type': 'file', 'name': 'data.bin', 'path': 'uploads/data.bin'},
    ]
    assert '[MEMORY]' not in stored[0]['text']


def test_ordinary_route_and_batch_worker_inputs_have_full_prompt_parity(tmp_path, monkeypatch):
    from utils import session_prompt

    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'attached.txt').write_text('ATTACHED-TEXT', encoding='utf-8')
    (tmp_path / 'uploads' / 'attached.bin').write_bytes(b'\x00\xffATTACHED-BINARY')
    (tmp_path / 'uploads' / 'mentioned.txt').write_text('MENTIONED-TEXT', encoding='utf-8')
    (tmp_path / 'uploads' / 'mentioned.bin').write_bytes(b'\x00\xffMENTIONED-BINARY')

    monkeypatch.setattr(
        session_prompt, 'search_memory',
        lambda user_id, content, limit=5: [f'memory for {user_id}'],
    )
    monkeypatch.setattr(session_prompt, 'render_memory_block',
                        lambda memories: '[MEMORY ' + ','.join(memories) + ']\n')
    monkeypatch.setattr(session_prompt, 'is_export_intent', lambda content: True)
    monkeypatch.setattr(session_prompt, 'resolve_collection_from_text',
                        lambda content: ('cases', 'Cases'))
    monkeypatch.setattr(session_prompt, 'export_collection_to_xlsx',
                        lambda collection, workspace_path, role=None:
                        {'path': 'outputs/cases.xlsx', 'rows': 3})

    content = ('请处理 @uploads/mentioned.txt 和 @uploads/mentioned.bin，'
               '并导出 Cases 数据')
    ordinary_request = {
        'content': content,
        'attachments': ['uploads/attached.txt', 'uploads/attached.bin'],
        'agentMentions': [],
    }
    ordinary_prompt, ordinary_parts = session_prompt.build_session_prompt(
        content=ordinary_request['content'],
        workspace_path=str(tmp_path),
        attachments=ordinary_request['attachments'],
        agent_mentions=ordinary_request['agentMentions'],
        user_id='user-1',
        role='developer',
    )

    batch_session = {
        'continue_prompt': content,
        'input_files': [
            {'name': 'attached.txt', 'path': 'batch-staging/user-1/attached.txt'},
            {'name': 'attached.bin', 'path': 'batch-staging/user-1/attached.bin'},
        ],
        'user_id': 'user-1',
        'role': 'developer',
    }
    batch_prompt, batch_parts = session_prompt.build_session_prompt(
        content=batch_session['continue_prompt'],
        workspace_path=str(tmp_path),
        attachments=[f['path'].replace('batch-staging/user-1/', 'uploads/')
                     for f in batch_session['input_files']],
        agent_mentions=[],
        user_id=batch_session['user_id'],
        role=batch_session['role'],
    )

    assert batch_prompt == ordinary_prompt
    assert batch_parts == ordinary_parts
    assert 'ATTACHED-TEXT' in batch_prompt
    assert 'MENTIONED-TEXT' in batch_prompt
    assert 'attached.bin' in batch_prompt and '工具读取' in batch_prompt
    mentioned_binary_path = str(tmp_path / 'uploads' / 'mentioned.bin')
    assert (
        '[用户用 @ 引用的文件 uploads/mentioned.bin 为二进制或超过内联大小限制，'
        f'如需请直接用工具读取，工作区绝对路径：{mentioned_binary_path}]'
    ) in batch_prompt
    assert '[MEMORY memory for user-1]' in batch_prompt
    assert 'outputs/cases.xlsx' in batch_prompt
    assert all(part['type'] in ('text', 'file') for part in batch_parts)
    assert '@uploads/mentioned.txt' in batch_parts[0]['text']
    assert not any(part.get('path') == 'uploads/mentioned.txt' for part in batch_parts)
