"""自定义模型端点单测（OC2 阶段五）：provider map 渲染（V1 格式，V2 内存
规范化兼容）+ write_opencode_config 融合 + CRUD 路由。"""
import json
from unittest.mock import patch, MagicMock

from utils.oc_endpoints import enabled_endpoints, provider_config_v1
from utils.workspace import write_opencode_config

_EP = {'provider_id': 'siliconflow', 'display_name': '硅基流动',
       'base_url': 'https://api.siliconflow.cn/v1', 'api_key': 'sk-test',
       'models': [{'id': 'longcat-2.5', 'name': 'LongCat 2.5'}]}


def test_provider_config_v1_shape():
    cfg = provider_config_v1([_EP])
    p = cfg['siliconflow']
    assert p['npm'] == '@ai-sdk/openai-compatible'      # V1 格式载体
    assert p['api'] == 'https://api.siliconflow.cn/v1'
    assert p['options']['apiKey'] == 'sk-test'
    assert p['models']['longcat-2.5']['name'] == 'LongCat 2.5'


def test_provider_config_v1_skips_invalid_entries():
    out = provider_config_v1([
        {'provider_id': '', 'base_url': 'https://x'},
        {'provider_id': 'no-url'},
        {'provider_id': 'ok', 'base_url': 'https://y',
         'models': [{'id': ''}, {'id': 'm1', 'name': 'M1'}]},
    ])
    assert set(out.keys()) == {'ok'}
    assert out['ok']['models'] == {'m1': {'name': 'M1'}}


def test_provider_config_v1_empty():
    assert provider_config_v1([]) == {}
    assert provider_config_v1(None) == {}


def test_write_opencode_config_merges_providers(tmp_path):
    ws = str(tmp_path)
    providers = provider_config_v1([_EP])
    write_opencode_config(ws, mcp_name='check-manage',
                          mcp_url='http://127.0.0.1:3003/mcp?token=t',
                          model='m0/old', providers=providers)
    cfg = json.loads((tmp_path / 'opencode.json').read_text(encoding='utf-8'))
    assert cfg['provider']['siliconflow']['api'] == _EP['base_url']
    assert cfg['mcp']['check-manage']['url'].endswith('token=t')
    assert cfg['model'] == 'm0/old'


def test_write_opencode_config_no_providers_omits_key(tmp_path):
    ws = str(tmp_path)
    write_opencode_config(ws, mcp_name='check-manage',
                          mcp_url='http://127.0.0.1:3003/mcp?token=t')
    cfg = json.loads((tmp_path / 'opencode.json').read_text(encoding='utf-8'))
    assert 'provider' not in cfg        # 无自定义端点时不写键（V1 行为不变）


def test_enabled_endpoints_db_error_returns_empty():
    with patch('db.get_db', side_effect=RuntimeError('db down')):
        assert enabled_endpoints() == []


def test_enabled_endpoints_reads_rows():
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = [
        ('siliconflow', '硅基流动', 'https://api.siliconflow.cn/v1', 'sk',
         [{'id': 'longcat-2.5', 'name': 'LongCat 2.5'}])]
    conn.cursor.return_value = cur
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    with patch('db.get_db', return_value=ctx):
        out = enabled_endpoints()
    assert out[0]['provider_id'] == 'siliconflow'
    assert out[0]['models'][0]['id'] == 'longcat-2.5'
