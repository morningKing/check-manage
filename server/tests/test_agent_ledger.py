# -*- coding: utf-8 -*-
"""agent_ledger 工具时长提取测试（SkillOpt 性能分析二期 Task 2）。

extract_from_parts / extract_from_part_map 返回六元组：
(part_id, tool, args_text, state, started_at, duration_ms)——
started_at 由 state.time.start（epoch ms）换算 tz-aware UTC datetime。
纯函数用例，不触库。
"""
import uuid
from datetime import datetime, timezone

from utils.agent_ledger import extract_from_parts, extract_from_part_map


def _raw_tool_part(part_id='p1', tool='bash', start=1_700_000_000_000, end=1_700_000_005_000):
    return {'id': part_id, 'type': 'tool', 'tool': tool,
            'state': {'status': 'completed', 'input': {'cmd': 'ls'},
                      'time': {'start': start, 'end': end}}}


class TestExtractDuration:
    def test_raw_shape_extracts_start_and_duration(self):
        rows = extract_from_parts([_raw_tool_part()])
        assert len(rows) == 1
        pid, tool, args, state, started, dur = rows[0]
        assert tool == 'bash' and state == 'completed'
        assert dur == 5_000
        assert started is not None
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    def test_raw_shape_without_time_yields_nones(self):
        p = {'id': 'p2', 'type': 'tool', 'tool': 'read',
             'state': {'status': 'completed', 'input': {}}}
        pid, tool, args, state, started, dur = extract_from_parts([p])[0]
        assert started is None and dur is None

    def test_mapped_shape_extracts_start_and_duration(self):
        # map_part 透传后：tool_use 带 durationMs + time（Task 2 的 map_part 改动）
        mapped = {'type': 'tool_use', 'name': 'read', 'status': 'completed',
                  'input': {'filePath': 'a.py'}, 'durationMs': 2_500,
                  'time': {'start': 1_700_000_000_000, 'end': 1_700_000_002_500}}
        rows = extract_from_part_map({'p9': mapped})
        pid, tool, args, state, started, dur = rows[0]
        assert tool == 'read' and dur == 2_500
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
