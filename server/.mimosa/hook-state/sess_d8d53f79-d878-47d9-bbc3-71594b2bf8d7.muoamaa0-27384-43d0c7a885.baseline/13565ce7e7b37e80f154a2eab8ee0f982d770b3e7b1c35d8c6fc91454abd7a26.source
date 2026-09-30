"""Tests for utils.ai_message_meta (timing / tokens / cost extraction)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ai_message_meta import (
    meta_from_info, aggregate_metas, public_meta, tool_duration_ms)


def test_meta_from_info_basic():
    info = {
        'role': 'assistant',
        'time': {'created': 1000, 'completed': 5400},
        'tokens': {'input': 18886, 'output': 159, 'reasoning': 56},
        'cost': 0.0021,
    }
    m = meta_from_info(info)
    assert m['durationMs'] == 4400
    assert m['tokensInput'] == 18886
    assert m['tokensOutput'] == 159
    assert m['cost'] == 0.0021
    assert m['_created'] == 1000 and m['_completed'] == 5400


def test_meta_from_info_incomplete_returns_none():
    assert meta_from_info({'time': {'created': 1000}}) is None   # no completed
    assert meta_from_info({}) is None
    assert meta_from_info(None) is None


def test_public_meta_strips_internals():
    m = meta_from_info({'time': {'created': 1, 'completed': 2},
                        'tokens': {'input': 3, 'output': 4}, 'cost': 0})
    pub = public_meta(m)
    assert set(pub.keys()) == {'durationMs', 'tokensInput', 'tokensOutput', 'cost'}
    assert public_meta(None) is None


def test_aggregate_spans_and_sums():
    metas = [
        meta_from_info({'time': {'created': 1000, 'completed': 3000},
                        'tokens': {'input': 100, 'output': 20}, 'cost': 0.001}),
        meta_from_info({'time': {'created': 3010, 'completed': 6000},
                        'tokens': {'input': 250, 'output': 35}, 'cost': 0.002}),
    ]
    agg = aggregate_metas(metas)
    assert agg['durationMs'] == 5000            # 6000 - 1000
    assert agg['tokensInput'] == 250            # max (cumulative), not 350
    assert agg['tokensOutput'] == 55            # summed
    assert agg['cost'] == 0.003                 # summed
    assert '_created' not in agg


def test_aggregate_none_when_empty():
    assert aggregate_metas([]) is None
    assert aggregate_metas([None, None]) is None


def test_tool_duration():
    assert tool_duration_ms({'time': {'start': 1782397501502, 'end': 1782397501507}}) == 5
    assert tool_duration_ms({'time': {'start': 100}}) is None    # no end
    assert tool_duration_ms({}) is None
    assert tool_duration_ms(None) is None
