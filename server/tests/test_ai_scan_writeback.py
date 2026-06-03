import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from utils.ai_scan_engine import extract_json


def test_extract_fenced_json():
    text = "分析如下\n```json\n{\"结论\": \"通过\", \"意见\": \"ok\"}\n```\n谢谢"
    assert extract_json(text) == {'结论': '通过', '意见': 'ok'}


def test_extract_last_balanced_object():
    text = '随便 {\"a\":1} 中间 {\"结论\": \"驳回\"}'
    assert extract_json(text) == {'结论': '驳回'}


def test_extract_none_returns_none():
    assert extract_json('没有 JSON 的纯文本') is None
