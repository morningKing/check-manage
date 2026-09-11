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


def test_shared_helper_gives_ordinary_and_batch_inputs_same_augmented_prompt(tmp_path,
                                                                               monkeypatch):
    from utils import session_prompt

    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'uploads' / 'input.txt').write_text('SHARED-CONTENT', encoding='utf-8')
    monkeypatch.setattr(session_prompt, 'search_memory', lambda *args, **kwargs: [])
    monkeypatch.setattr(session_prompt, 'is_export_intent', lambda content: False)

    kwargs = dict(
        content='总结 @uploads/input.txt',
        workspace_path=str(tmp_path),
        attachments=['uploads/input.txt'],
        agent_mentions=[],
        user_id='user-1',
        role=None,
    )
    ordinary_prompt, ordinary_parts = session_prompt.build_session_prompt(**kwargs)
    batch_prompt, batch_parts = session_prompt.build_session_prompt(**kwargs)

    assert batch_prompt == ordinary_prompt
    assert batch_parts == ordinary_parts
    assert 'SHARED-CONTENT' in batch_prompt
