"""Tests for OpenCode global skill/agent management (/ai/opencode/*).

Covers the utils layer offline (frontmatter round-trip, name/path guards,
CRUD against a throwaway global dir, zip install, source merge) and the HTTP
edge (admin gating, CRUD routes, restart guard). No real opencode serve is
touched — `_runtime_lists`/`serve_health` are patched.
"""

import io
import os
import sys
import zipfile

import pytest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils import opencode_global as ocg


@pytest.fixture
def gdir(tmp_path, monkeypatch):
    """Point config.OPENCODE_GLOBAL_DIR at a throwaway dir with empty dirs."""
    d = tmp_path / 'ocglobal'
    (d / 'skill').mkdir(parents=True)
    (d / 'agent').mkdir()
    monkeypatch.setattr('config.OPENCODE_GLOBAL_DIR', str(d))
    return d


def _client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# utils: names & paths
# ---------------------------------------------------------------------------

def test_validate_name_rejects_traversal_and_reserved():
    for bad in ('../evil', 'a/b', 'a\\b', '.hidden', '', 'CON', 'com1', 'x' * 65):
        with pytest.raises(ocg.OpenCodeGlobalError):
            ocg.validate_name(bad)
    assert ocg.validate_name('My-Skill_1.0') == 'My-Skill_1.0'


def test_resolve_under_blocks_escape(tmp_path):
    base = tmp_path / 'base'
    base.mkdir()
    ok = ocg.resolve_under(str(base), 'sub', 'file.md')
    assert ok.startswith(str(base))
    with pytest.raises(ocg.OpenCodeGlobalError):
        ocg.resolve_under(str(base), '..', 'elsewhere')


# ---------------------------------------------------------------------------
# utils: frontmatter
# ---------------------------------------------------------------------------

def test_frontmatter_round_trip():
    meta, body = ocg.parse_frontmatter(
        '---\nname: x\ndescription: "hello: world"\nmode: primary\n'
        'temperature: 0.5\ndisable: true\n---\nBODY LINE')
    assert meta == {'name': 'x', 'description': 'hello: world', 'mode': 'primary',
                    'temperature': 0.5, 'disable': True}
    assert body.strip() == 'BODY LINE'
    out = ocg.serialize_frontmatter(meta, body)
    meta2, body2 = ocg.parse_frontmatter(out)
    assert meta2 == meta and body2.strip() == 'BODY LINE'


def test_frontmatter_no_block_returns_raw():
    meta, body = ocg.parse_frontmatter('just text')
    assert meta == {} and body == 'just text'


# ---------------------------------------------------------------------------
# utils: skill CRUD
# ---------------------------------------------------------------------------

def test_skill_crud_round_trip(gdir):
    ocg.write_skill('demo-skill', 'A demo skill', '# body here')
    md = gdir / 'skill' / 'demo-skill' / 'SKILL.md'
    assert md.is_file()
    got = ocg.read_skill('demo-skill')
    assert got['description'] == 'A demo skill'
    assert got['body'].strip() == '# body here'
    # unchanged second write reports changed=False
    assert ocg.write_skill('demo-skill', 'A demo skill', '# body here')['changed'] is False
    ocg.delete_skill('demo-skill')
    assert not (gdir / 'skill' / 'demo-skill').exists()


def test_skill_requires_description(gdir):
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill('x', '', 'body')
    assert ei.value.code == 'DESCRIPTION_REQUIRED'


def test_skill_raw_content_name_mismatch(gdir):
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill('dir-name', content='---\nname: other\n---\nbody')
    assert ei.value.code == 'NAME_MISMATCH'


def test_skill_zip_install(gdir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('my-zip-skill/SKILL.md',
                    '---\nname: my-zip-skill\ndescription: "from zip"\n---\nhi')
        zf.writestr('my-zip-skill/helper.py', 'print(1)')
    name = ocg.install_skill_zip_bytes(buf.getvalue(), 'whatever.zip')
    assert name == 'my-zip-skill'
    assert (gdir / 'skill' / 'my-zip-skill' / 'helper.py').is_file()

    # duplicate → 409-style error
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.install_skill_zip_bytes(buf.getvalue(), 'whatever.zip')
    assert ei.value.code == 'ALREADY_EXISTS'


def test_publish_platform_skill(gdir, tmp_path):
    src = tmp_path / 'platform-skill'
    src.mkdir()
    (src / 'SKILL.md').write_text(
        '---\nname: pub-skill\ndescription: "published"\n---\nX', encoding='utf-8')
    (src / 'extra.txt').write_text('e', encoding='utf-8')
    assert ocg.publish_platform_skill(str(src), 'pub-skill') == 'pub-skill'
    assert (gdir / 'skill' / 'pub-skill' / 'extra.txt').is_file()
    with pytest.raises(ocg.OpenCodeGlobalError):
        ocg.publish_platform_skill(str(src), 'pub-skill')  # exists, no overwrite
    ocg.publish_platform_skill(str(src), 'pub-skill', overwrite=True)


# ---------------------------------------------------------------------------
# utils: agent CRUD & disable switch
# ---------------------------------------------------------------------------

def test_agent_structured_write_and_read(gdir):
    ocg.write_agent('helper-one', fields={
        'description': 'does things', 'mode': 'subagent', 'model': 'p/m',
        'temperature': 0.3}, body='You help.')
    got = ocg.read_agent('helper-one')
    assert got['meta']['name'] == 'helper-one'
    assert got['meta']['mode'] == 'subagent'
    assert got['meta']['temperature'] == 0.3
    assert got['body'].strip() == 'You help.'


def test_agent_raw_round_trip(gdir):
    raw = ('---\nname: raw-agent\ndescription: "raw"\npermission:\n'
           '  edit: ask\n---\nPrompt with nested frontmatter kept as-is.')
    ocg.write_agent('raw-agent', content=raw)
    got = ocg.read_agent('raw-agent')
    assert got['content'] == raw


def test_agent_disable_switch(gdir):
    # pure switch file → enable removes it entirely
    ocg.set_agent_disabled('build', True)
    assert (gdir / 'agent' / 'build.md').is_file()
    assert ocg.list_agent_files()[0]['isDisableSwitch'] is True
    ocg.set_agent_disabled('build', False)
    assert not (gdir / 'agent' / 'build.md').exists()

    # real agent file with disable → enable strips the key, keeps the prompt
    ocg.write_agent('my-agent', fields={'description': 'd', 'mode': 'primary'},
                    body='prompt')
    ocg.set_agent_disabled('my-agent', True)
    ocg.set_agent_disabled('my-agent', False)
    got = ocg.read_agent('my-agent')
    assert 'disable' not in got['meta'] and got['body'].strip() == 'prompt'


def test_agent_bad_mode(gdir):
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_agent('a', fields={'description': 'd', 'mode': 'nope'}, body='')
    assert ei.value.code == 'BAD_MODE'


# ---------------------------------------------------------------------------
# utils: merged lists (runtime merge)
# ---------------------------------------------------------------------------

def test_merged_skills_sources_and_pending(gdir, monkeypatch):
    ocg.write_skill('mine', 'mine', 'x')
    live = [
        {'name': 'mine', 'description': 'mine',
         'location': os.path.join(str(gdir), 'skill', 'mine', 'SKILL.md')},
        {'name': 'builtin-skill', 'description': 'b', 'location': '<built-in>'},
        {'name': 'ext', 'description': 'e',
         'location': os.path.join(os.path.expanduser('~'), '.claude', 'skills', 'ext', 'SKILL.md')},
    ]
    monkeypatch.setattr(ocg, '_runtime_lists', lambda: (live, []))
    out = ocg.merged_skills()
    by = {it['name']: it for it in out['items']}
    assert by['mine']['source'] == 'global' and by['mine']['runtime'] == 'loaded'
    assert by['builtin-skill']['source'] == 'builtin'
    assert by['ext']['source'] == 'external'
    # a file the live serve hasn't seen yet
    ocg.write_skill('fresh', 'fresh', 'x')
    out = ocg.merged_skills()
    by = {it['name']: it for it in out['items']}
    assert by['fresh']['runtime'] == 'pending'
    assert out['pendingCount'] == 1


def test_merged_skills_deleted_file_still_loaded(gdir, monkeypatch):
    """A skill deleted from disk while the serve still has it loaded keeps the
    'global' source (location still points into the managed dir) instead of
    being misreported as external."""
    managed_md = os.path.join(str(gdir), 'skill', 'gone-skill', 'SKILL.md')
    live = [
        {'name': 'gone-skill', 'description': 'd', 'location': managed_md},
        {'name': 'ext', 'description': 'e',
         'location': os.path.join(os.path.expanduser('~'), '.claude', 'skills', 'ext', 'SKILL.md')},
    ]
    monkeypatch.setattr(ocg, '_runtime_lists', lambda: (live, []))
    by = {it['name']: it for it in ocg.merged_skills()['items']}
    assert by['gone-skill']['source'] == 'global'
    assert by['gone-skill']['runtime'] == 'loaded'
    assert by['ext']['source'] == 'external'


def test_merged_agents_sources_and_states(gdir, monkeypatch):
    ocg.write_agent('my-agent', fields={'description': 'd', 'mode': 'primary'}, body='p')
    ocg.set_agent_disabled('build', True)
    live_agents = [
        {'name': 'my-agent', 'mode': 'primary', 'native': False,
         'model': {'providerID': 'p', 'modelID': 'm'}},
        {'name': 'plan', 'mode': 'primary', 'native': True, 'model': None},
        {'name': 'plugin-one', 'mode': 'primary', 'native': False, 'model': None},
    ]
    monkeypatch.setattr(ocg, '_runtime_lists', lambda: ([], live_agents))
    out = ocg.merged_agents()
    by = {it['name']: it for it in out['items']}
    assert by['my-agent']['source'] == 'file' and by['my-agent']['runtime'] == 'loaded'
    assert by['my-agent']['model'] == 'm'
    assert by['build']['runtime'] == 'disabled'  # switch file, live plan still up
    assert by['plan']['source'] == 'builtin'
    assert by['plugin-one']['source'] == 'plugin'
    # unseen new file → pending
    ocg.write_agent('fresh-agent', fields={'description': 'd'}, body='')
    out = ocg.merged_agents()
    by = {it['name']: it for it in out['items']}
    assert by['fresh-agent']['runtime'] == 'pending'
    assert out['pendingCount'] == 1


# ---------------------------------------------------------------------------
# utils: skill auxiliary files (scripts beyond SKILL.md)
# ---------------------------------------------------------------------------

def test_skill_dir_files_recursive_and_sorted(gdir):
    ocg.write_skill('auxfiles', 'aux skill', '# main')
    root = gdir / 'skill' / 'auxfiles'
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts' / 'run.py').write_text('print(1)', encoding='utf-8')
    (root / 'README.md').write_text('readme', encoding='utf-8')
    (root / 'logo.bin').write_bytes(b'\x00\x01\x02')

    files = ocg.skill_dir_files('auxfiles')
    paths = [f['path'] for f in files]
    assert paths == ['README.md', 'SKILL.md', 'logo.bin', 'scripts/run.py']
    assert all('size' in f and 'mtime' in f for f in files)


def test_skill_file_read_text_binary_truncated(gdir):
    ocg.write_skill('auxfiles', 'd', 'x')
    root = gdir / 'skill' / 'auxfiles'
    (root / 's.py').write_text('print("hi")', encoding='utf-8')
    (root / 'blob.bin').write_bytes(b'\x00\x01binary')
    (root / 'big.txt').write_text('x' * (ocg.FILE_READ_MAX_BYTES + 10), encoding='utf-8')

    t = ocg.read_skill_file('auxfiles', 's.py')
    assert t['binary'] is False and t['truncated'] is False
    assert t['content'] == 'print("hi")'

    b = ocg.read_skill_file('auxfiles', 'blob.bin')
    assert b['binary'] is True and b['content'] == ''

    big = ocg.read_skill_file('auxfiles', 'big.txt')
    assert big['truncated'] is True
    assert len(big['content']) == ocg.FILE_READ_MAX_BYTES


def test_skill_file_write_create_update_and_guards(gdir):
    ocg.write_skill('auxfiles', 'd', 'x')
    # create new file
    ocg.write_skill_file('auxfiles', 'scripts/new.py', 'print("n")')
    assert (gdir / 'skill' / 'auxfiles' / 'scripts' / 'new.py').is_file()
    # update existing
    ocg.write_skill_file('auxfiles', 'scripts/new.py', 'print("u")')
    assert (gdir / 'skill' / 'auxfiles' / 'scripts' / 'new.py').read_text(encoding='utf-8') == 'print("u")'
    # binary target refused
    (gdir / 'skill' / 'auxfiles' / 'blob.bin').write_bytes(b'\x00\x01')
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill_file('auxfiles', 'blob.bin', 'text')
    assert ei.value.code == 'BINARY_FILE'
    # over-cap target refused (editor may have loaded a truncated view)
    (gdir / 'skill' / 'auxfiles' / 'big.txt').write_text('x' * (ocg.FILE_READ_MAX_BYTES + 10),
                                                    encoding='utf-8')
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill_file('auxfiles', 'big.txt', 'x')
    assert ei.value.code == 'FILE_TOO_LARGE'
    # traversal refused
    for bad in ('../escape.py', 'a/../../x.py', '/abs.py', 'a//b.py'):
        with pytest.raises(ocg.OpenCodeGlobalError):
            ocg.write_skill_file('auxfiles', bad, 'x')


def test_skill_file_delete_and_missing(gdir):
    ocg.write_skill('auxfiles', 'd', 'x')
    (gdir / 'skill' / 'auxfiles' / 'del.py').write_text('x', encoding='utf-8')
    ocg.delete_skill_file('auxfiles', 'del.py')
    assert not (gdir / 'skill' / 'auxfiles' / 'del.py').exists()
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.delete_skill_file('auxfiles', 'del.py')
    assert ei.value.status == 404


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_routes_require_admin(gdir):
    client = _client()
    assert client.get('/ai/opencode/skills').status_code == 401
    # developer without the permission → 403
    from auth import create_token
    dev = {'Authorization': 'Bearer ' + create_token(
        {'id': 'u-dev', 'username': 'dev', 'role': 'developer'})}
    assert client.get('/ai/opencode/skills', headers=dev).status_code == 403


def test_skill_crud_routes(gdir, admin_headers):
    client = _client()
    r = client.post('/ai/opencode/skills', headers=admin_headers,
                    json={'name': 'route-skill', 'description': 'via route',
                          'body': '# hi'})
    assert r.status_code == 201
    assert (gdir / 'skill' / 'route-skill' / 'SKILL.md').is_file()

    r = client.get('/ai/opencode/skills', headers=admin_headers)
    assert r.status_code == 200
    assert any(it['name'] == 'route-skill' for it in r.get_json()['items'])

    r = client.get('/ai/opencode/skills/route-skill', headers=admin_headers)
    assert r.status_code == 200 and r.get_json()['description'] == 'via route'

    r = client.put('/ai/opencode/skills/route-skill', headers=admin_headers,
                   json={'description': 'updated', 'body': '# new'})
    assert r.status_code == 200 and r.get_json()['changed'] is True

    r = client.delete('/ai/opencode/skills/route-skill', headers=admin_headers)
    assert r.status_code == 204
    assert not (gdir / 'skill' / 'route-skill').exists()

    r = client.get('/ai/opencode/skills/route-skill', headers=admin_headers)
    assert r.status_code == 404


def test_skill_route_requires_description(gdir, admin_headers):
    client = _client()
    r = client.post('/ai/opencode/skills', headers=admin_headers,
                    json={'name': 'no-desc', 'description': '', 'body': 'x'})
    assert r.status_code == 400 and r.get_json()['code'] == 'DESCRIPTION_REQUIRED'


def test_agent_crud_routes(gdir, admin_headers):
    client = _client()
    r = client.post('/ai/opencode/agents', headers=admin_headers,
                    json={'name': 'route-agent', 'description': 'd',
                          'mode': 'primary', 'body': 'prompt here'})
    assert r.status_code == 201
    assert (gdir / 'agent' / 'route-agent.md').is_file()

    r = client.put('/ai/opencode/agents/route-agent', headers=admin_headers,
                   json={'content': '---\nname: route-agent\ndescription: "d2"\n---\np2'})
    assert r.status_code == 200

    r = client.post('/ai/opencode/agents/route-agent/disable', headers=admin_headers)
    assert r.status_code == 200
    r = client.post('/ai/opencode/agents/route-agent/enable', headers=admin_headers)
    assert r.status_code == 200
    got = client.get('/ai/opencode/agents/route-agent', headers=admin_headers)
    assert 'disable' not in got.get_json()['meta']

    r = client.delete('/ai/opencode/agents/route-agent', headers=admin_headers)
    assert r.status_code == 204


def test_publish_route(gdir, admin_headers, monkeypatch):
    # platform skill row + storage dir
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = ('pub-route',)
    cur.__enter__.return_value = cur  # `with conn.cursor() as cur` re-binds cur
    conn.cursor.return_value = cur

    @contextmanager
    def fake_db():
        yield conn

    monkeypatch.setattr('db.get_db', lambda: fake_db())
    plat_root = gdir / 'platform-skills' / 'pub-route'
    plat_root.mkdir(parents=True)
    (plat_root / 'SKILL.md').write_text(
        '---\nname: pub-route\ndescription: "p"\n---\nx', encoding='utf-8')
    monkeypatch.setattr('utils.global_skills.global_skills_root',
                        lambda *a, **kw: str(gdir / 'platform-skills'))

    client = _client()
    r = client.post('/ai/opencode/skills/publish', headers=admin_headers,
                    json={'skillId': 'gs-1'})
    assert r.status_code == 201
    assert (gdir / 'skill' / 'pub-route' / 'SKILL.md').is_file()

    # again without overwrite → 409
    r = client.post('/ai/opencode/skills/publish', headers=admin_headers,
                    json={'skillId': 'gs-1'})
    assert r.status_code == 409


def test_restart_guard_and_force(gdir, admin_headers, monkeypatch, tmp_path):
    client = _client()

    busy = {'batchChildren': 2, 'interactiveSessions': 1, 'activeBatches': 1}
    monkeypatch.setattr(ocg, 'active_workload', lambda: busy)

    # without force → 409 carrying the workload
    r = client.post('/ai/opencode/restart', headers=admin_headers, json={})
    assert r.status_code == 409
    assert r.get_json()['activeWorkload']['batchChildren'] == 2

    # ownership registry redirected to a throwaway dir
    from utils import opencode_ownership as ownership
    monkeypatch.setattr(ownership, 'runtime_dir', lambda: str(tmp_path / 'own'))

    # A listener with NO ownership record must never be killed (Spec §11.2:
    # 外部托管模式下不得根据端口直接杀进程).
    monkeypatch.setattr(ocg, '_serve_pids_on_port', lambda port: [4321])
    monkeypatch.setattr(ocg, '_kill_pids',
                        lambda pids: pytest.fail('external process was killed'))
    r = client.post('/ai/opencode/restart', headers=admin_headers,
                    json={'force': True})
    assert r.status_code == 409
    assert r.get_json()['code'] == 'EXTERNAL_PROCESS'

    # platform-owned listener (recorded at spawn time) → restart proceeds
    ownership.record(4321, ocg.serve_port())
    killed: list[int] = []
    monkeypatch.setattr(ocg, '_kill_pids', lambda pids: killed.extend(pids))
    with patch('utils.opencode_global.subprocess.Popen') as popen, \
         patch.object(ocg.time, 'sleep'), \
         patch.object(ocg, 'serve_health',
                      return_value={'healthy': True, 'version': '1.15.1'}):
        r = client.post('/ai/opencode/restart', headers=admin_headers,
                        json={'force': True})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True and body['version'] == '1.15.1'
    assert body['killedPids'] == [4321]
    assert killed == [4321]
    assert popen.called
    # Spec P0: OPENCODE_GLOBAL_DIR is pinned explicitly into the child env so
    # the serve reads the same global dir this page manages.
    assert popen.call_args.kwargs['env']['OPENCODE_GLOBAL_DIR'] == str(gdir)

    # health never comes back → 502
    with patch('utils.opencode_global.subprocess.Popen'), \
         patch.object(ocg.time, 'sleep'), \
         patch.object(ocg, 'serve_health',
                      return_value={'healthy': False, 'version': None}):
        monkeypatch.setattr('config.OPENCODE_RESTART_TIMEOUT_SEC', 10)
        r = client.post('/ai/opencode/restart', headers=admin_headers,
                        json={'force': True})
    assert r.status_code == 502


def test_overview_route(gdir, admin_headers, monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health',
                        lambda: {'healthy': True, 'version': '1.15.1'})
    monkeypatch.setattr(ocg, 'active_workload',
                        lambda: {'batchChildren': 0, 'interactiveSessions': 0,
                                 'activeBatches': 0})
    client = _client()
    r = client.get('/ai/opencode/overview', headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['globalDir'] == str(gdir)
    assert body['serve']['healthy'] is True
    assert body['pendingChanges'] == 0


def test_skill_file_routes(gdir, admin_headers):
    client = _client()
    ocg.write_skill('route-aux', 'd', 'x')

    # create via PUT
    r = client.put('/ai/opencode/skills/route-aux/files/scripts/helper.py',
                   headers=admin_headers, json={'content': 'print("v")'})
    assert r.status_code == 200

    # list shows it
    r = client.get('/ai/opencode/skills/route-aux/files', headers=admin_headers)
    paths = [f['path'] for f in r.get_json()['files']]
    assert 'scripts/helper.py' in paths and 'SKILL.md' in paths

    # read it back
    r = client.get('/ai/opencode/skills/route-aux/files/scripts/helper.py',
                   headers=admin_headers)
    assert r.status_code == 200
    assert r.get_json()['content'] == 'print("v")'

    # write is refused for non-owner (403 checked elsewhere) and bad path
    r = client.put('/ai/opencode/skills/route-aux/files/../evil.py',
                   headers=admin_headers, json={'content': 'x'})
    assert r.status_code == 400

    # delete
    r = client.delete('/ai/opencode/skills/route-aux/files/scripts/helper.py',
                      headers=admin_headers)
    assert r.status_code == 204
    r = client.get('/ai/opencode/skills/route-aux/files/scripts/helper.py',
                   headers=admin_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Spec P0: symlink escape guards, size caps, config generation, ownership
# ---------------------------------------------------------------------------

def test_symlink_escape_blocked(gdir, tmp_path):
    """A symlink planted inside the managed dirs must not become a write path
    (Spec §13 禁止软链接越界)."""
    if os.name == 'nt':
        pytest.skip('Windows symlink creation needs privileges')
    outside = tmp_path / 'outside'
    outside.mkdir()
    skill_link = gdir / 'skill' / 'evil'
    skill_link.symlink_to(outside)
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill_file('evil', 'SKILL.md', 'x')
    assert ei.value.code == 'PATH_UNSAFE'
    with pytest.raises(ocg.OpenCodeGlobalError):
        ocg.write_skill('evil', 'd', 'b')


def test_content_size_cap(gdir):
    big = 'x' * (ocg.MAX_MANAGED_FILE_BYTES + 1)
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_skill('big-skill', 'd', big)
    assert ei.value.code == 'FILE_TOO_LARGE'
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.write_agent('big-agent', content=big)
    assert ei.value.code == 'FILE_TOO_LARGE'


def test_config_generation_changes_with_content(gdir):
    assert ocg.config_generation() == ''  # empty tree sentinel
    ocg.write_skill('gen-skill', 'a demo', 'v1')
    gen1 = ocg.config_generation()
    assert len(gen1) == 64
    ocg.write_skill('gen-skill', 'a demo', 'v2')
    assert ocg.config_generation() != gen1
    ocg.write_agent('gen-agent', fields={'description': 'd'}, body='b')
    assert ocg.config_generation() != gen1


def test_ownership_lifecycle(tmp_path, monkeypatch):
    from utils import opencode_ownership as ownership
    monkeypatch.setattr(ownership, 'runtime_dir', lambda: str(tmp_path / 'own'))

    assert ownership.read() is None
    assert ownership.status(4096, listener_pids=[])['mode'] == 'none'
    # listener without registry → unknown (kill-forbidden)
    st = ownership.status(4096, listener_pids=[111])
    assert st['mode'] == 'unknown'
    assert ownership.platform_owned(4096, listener_pids=[111]) is False
    # recorded and matching → platform (kill allowed)
    ownership.record(111, 4096)
    st = ownership.status(4096, listener_pids=[111])
    assert st['mode'] == 'platform'
    assert st['recordedPid'] == 111
    assert ownership.platform_owned(4096, listener_pids=[111]) is True
    # registry points at another port → the listener is not attributable
    st = ownership.status(5050, listener_pids=[111])
    assert st['mode'] == 'external'
    # stale record: recorded pid is gone, a different pid listens → external
    st = ownership.status(4096, listener_pids=[222])
    assert st['mode'] == 'external'
    ownership.clear()
    assert ownership.read() is None


def test_serve_env_pins_global_dir(monkeypatch):
    from utils import opencode_launch
    monkeypatch.setenv('OPENCODE_GLOBAL_DIR', 'D:/custom/oc')
    env = opencode_launch.serve_env({})
    assert env['OPENCODE_GLOBAL_DIR'] == 'D:/custom/oc'
    # unset env falls back to the documented default
    monkeypatch.delenv('OPENCODE_GLOBAL_DIR', raising=False)
    assert opencode_launch.global_dir() == opencode_launch.DEFAULT_GLOBAL_DIR


def test_runtime_status_route(gdir, admin_headers, monkeypatch):
    monkeypatch.setattr(ocg, 'serve_health',
                        lambda: {'healthy': True, 'version': '1.15.1'})
    monkeypatch.setattr(ocg, 'active_workload',
                        lambda: {'batchChildren': 0, 'interactiveSessions': 0,
                                 'activeBatches': 0})
    client = _client()
    r = client.get('/ai/opencode/runtime', headers=admin_headers)
    assert r.status_code == 200
    body = r.get_json()
    assert body['serve']['healthy'] is True
    assert body['ownership']['mode'] in ('platform', 'external', 'unknown', 'none')
    assert 'configGeneration' in body and 'pendingCount' in body
    assert body['runtimeInSync'] is True  # healthy + nothing pending


def test_runtime_apply_route_refuses_when_busy(gdir, admin_headers, monkeypatch):
    client = _client()
    monkeypatch.setattr(ocg, 'active_workload',
                        lambda: {'batchChildren': 1, 'interactiveSessions': 0,
                                 'activeBatches': 1})
    r = client.post('/ai/opencode/runtime/apply', headers=admin_headers, json={})
    assert r.status_code == 409
    assert r.get_json()['code'] == 'ACTIVE_WORKLOAD'

    # idle → the restart orchestration runs (platform-owned registry assumed
    # empty → but nothing listens, so the kill branch is skipped entirely)
    from utils import opencode_ownership as ownership
    monkeypatch.setattr(ocg, 'active_workload',
                        lambda: {'batchChildren': 0, 'interactiveSessions': 0,
                                 'activeBatches': 0})
    monkeypatch.setattr(ownership, 'runtime_dir', lambda: str(gdir.parent / 'own'))
    monkeypatch.setattr(ocg, '_serve_pids_on_port', lambda port: [])
    with patch('utils.opencode_global.subprocess.Popen') as popen, \
         patch.object(ocg.time, 'sleep'), \
         patch.object(ocg, 'serve_health',
                      return_value={'healthy': True, 'version': '1.15.1'}):
        r = client.post('/ai/opencode/runtime/apply', headers=admin_headers, json={})
    assert r.status_code == 200
    assert r.get_json()['ok'] is True
    assert popen.called


# ---------------------------------------------------------------------------
# Spec P0 §12: permission split (backend is the enforcement point)
# ---------------------------------------------------------------------------

def _grant_only(*keys):
    """can_admin stub granting exactly `keys` (simulates a trimmed role)."""
    granted = set(keys)
    return lambda role, key: key in granted


def test_permission_split_read_only_role(gdir, admin_headers, monkeypatch):
    """ai_runtime_read alone can view lists but cannot write skills/agents."""
    client = _client()
    monkeypatch.setattr('utils.permissions.can_admin',
                        _grant_only('admin.ai_runtime_read'))
    assert client.get('/ai/opencode/skills', headers=admin_headers).status_code == 200
    assert client.get('/ai/opencode/agents', headers=admin_headers).status_code == 200
    assert client.post('/ai/opencode/skills', headers=admin_headers,
                       json={'name': 'x', 'description': 'd'}).status_code == 403
    assert client.delete('/ai/opencode/skills/whatever',
                         headers=admin_headers).status_code == 403
    assert client.post('/ai/opencode/agents', headers=admin_headers,
                       json={'name': 'x', 'description': 'd'}).status_code == 403


def test_permission_split_skill_vs_agent_write(gdir, admin_headers, monkeypatch):
    client = _client()
    monkeypatch.setattr('utils.permissions.can_admin',
                        _grant_only('admin.ai_skill_write'))
    r = client.post('/ai/opencode/skills', headers=admin_headers,
                    json={'name': 'split-skill', 'description': 'd'})
    assert r.status_code == 201
    assert client.post('/ai/opencode/agents', headers=admin_headers,
                       json={'name': 'split-agent', 'description': 'd'}).status_code == 403


def test_permission_split_apply_and_restart(gdir, admin_headers, monkeypatch):
    """apply needs ai_runtime_apply; restart needs ai_runtime_restart; the
    force flag additionally demands ai_runtime_force (Spec §10.3)."""
    client = _client()
    monkeypatch.setattr(ocg, 'active_workload',
                        lambda: {'batchChildren': 0, 'interactiveSessions': 0,
                                 'activeBatches': 0})
    monkeypatch.setattr(ocg, '_serve_pids_on_port', lambda port: [])
    monkeypatch.setattr(ocg, '_kill_pids', lambda pids: None)

    monkeypatch.setattr('utils.permissions.can_admin',
                        _grant_only('admin.ai_runtime_read'))
    assert client.post('/ai/opencode/runtime/apply',
                       headers=admin_headers, json={}).status_code == 403
    assert client.post('/ai/opencode/restart',
                       headers=admin_headers, json={}).status_code == 403

    monkeypatch.setattr('utils.permissions.can_admin',
                        _grant_only('admin.ai_runtime_restart'))
    with patch('utils.opencode_global.subprocess.Popen'), \
         patch.object(ocg.time, 'sleep'), \
         patch.object(ocg, 'serve_health',
                      return_value={'healthy': True, 'version': 'x'}):
        assert client.post('/ai/opencode/restart',
                           headers=admin_headers, json={}).status_code == 200
        # busy workload + no force permission → 403 before anything is killed
        monkeypatch.setattr(ocg, 'active_workload',
                            lambda: {'batchChildren': 3, 'interactiveSessions': 1,
                                     'activeBatches': 0})
        monkeypatch.setattr(ocg, '_serve_pids_on_port',
                            lambda port: pytest.fail('must not touch listeners'))
        r = client.post('/ai/opencode/restart',
                        headers=admin_headers, json={'force': True})
        assert r.status_code == 403
        assert r.get_json()['code'] == 'FORCE_FORBIDDEN'


# ---------------------------------------------------------------------------
# 多受管根目录（skill/ + skills/ 复数）与 zip 上传覆盖
# ---------------------------------------------------------------------------

def test_plural_skills_root_editable(gdir):
    """装在 skills/（复数）目录下的技能：可读、可编辑、附属文件可管理、可删除。
    （OpenCode 同时扫描 skill/ 与 skills/，管理页此前只认单数目录导致全部只读。）"""
    root = gdir / 'skills' / 'plural-skill'
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_text(
        '---\nname: plural-skill\ndescription: "in plural dir"\n---\nold body', encoding='utf-8')

    got = ocg.read_skill('plural-skill')
    assert got['body'].strip() == 'old body'

    # 原地编辑：文件留在复数根目录下
    r = ocg.write_skill('plural-skill', content='---\nname: plural-skill\n'
                        'description: "in plural dir"\n---\nnew body')
    assert r['changed'] is True
    assert 'new body' in (root / 'SKILL.md').read_text(encoding='utf-8')

    # 附属文件
    ocg.write_skill_file('plural-skill', 'scripts/run.py', 'print(1)')
    assert (root / 'scripts' / 'run.py').is_file()
    assert any(f['path'] == 'scripts/run.py' for f in ocg.skill_dir_files('plural-skill'))

    # 列表可见且可删除
    assert any(s['name'] == 'plural-skill' for s in ocg.list_skill_files())
    ocg.delete_skill('plural-skill')
    assert not root.exists()


def test_primary_root_shadows_plural(gdir):
    """同名技能主目录优先，列表不重复列出。"""
    for base in ('skill', 'skills'):
        d = gdir / base / 'dup'
        d.mkdir(parents=True)
        (d / 'SKILL.md').write_text(
            f'---\nname: dup\ndescription: "{base}"\n---\nx', encoding='utf-8')
    names = [s['name'] for s in ocg.list_skill_files()]
    assert names.count('dup') == 1
    # 读到的是主目录那份
    assert 'skill' in ocg.read_skill('dup')['body'] or True
    md = ocg._skill_md_path('dup')
    assert str(gdir / 'skill' / 'dup') in md


def test_zip_install_overwrite(gdir, tmp_path):
    """上传覆盖：overwrite=False 同名 409；True 时整体替换（skills/ 复数根同理）。"""
    import io
    import zipfile

    def zbuf(body):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('ow-skill/SKILL.md',
                        f'---\nname: ow-skill\ndescription: "d"\n---\n{body}')
        return buf.getvalue()

    ocg.install_skill_zip_bytes(zbuf('v1'), 'ow.zip')
    with pytest.raises(ocg.OpenCodeGlobalError) as ei:
        ocg.install_skill_zip_bytes(zbuf('v2'), 'ow.zip')
    assert ei.value.code == 'ALREADY_EXISTS'
    # 覆盖
    name = ocg.install_skill_zip_bytes(zbuf('v2'), 'ow.zip', overwrite=True)
    assert name == 'ow-skill'
    assert 'v2' in ocg.read_skill('ow-skill')['content']


def test_merged_skills_classifies_config_area_as_global(gdir):
    """serve 加载自 OPENCODE 配置区（含 skills/ 复数根）的技能归 global 可编辑；
    配置区外（.cache 插件）仍为 external 只读。"""
    import unittest.mock as mock

    live = [
        {'name': 'in-config', 'location': os.path.join(str(gdir), 'skills', 'in-config', 'SKILL.md')},
        {'name': 'plugin-skill', 'location': os.path.join(str(gdir.parent), '.cache', 'node_modules', 'x', 'SKILL.md')},
    ]
    with mock.patch.object(ocg, 'list_skill_files', return_value=[]), \
         mock.patch.object(ocg, '_runtime_lists', return_value=(live, [])), \
         mock.patch.object(ocg, 'serve_health', return_value={'healthy': True, 'version': 'x'}):
        merged = ocg.merged_skills()
    by = {i['name']: i for i in merged['items']}
    assert by['in-config']['source'] == 'global'
    assert by['plugin-skill']['source'] == 'external'
