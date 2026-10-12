# -*- coding: utf-8 -*-
"""agent_ledger 工具时长提取测试（SkillOpt 性能分析二期 Task 2）。

extract_from_parts / extract_from_part_map 返回七元组：
(part_id, tool, args_text, state, started_at, duration_ms, meta_session_id)——
started_at 由 state.time.start（epoch ms）换算 tz-aware UTC datetime，
meta_session_id 是 task part 携带的子会话 id（子代理归属）。
纯函数用例，不触库。
"""
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
        pid, tool, args, state, started, dur, meta_sid = rows[0]
        assert tool == 'bash' and state == 'completed'
        assert dur == 5_000
        assert started is not None
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    def test_raw_shape_without_time_yields_nones(self):
        p = {'id': 'p2', 'type': 'tool', 'tool': 'read',
             'state': {'status': 'completed', 'input': {}}}
        pid, tool, args, state, started, dur, meta_sid = extract_from_parts([p])[0]
        assert started is None and dur is None

    def test_mapped_shape_extracts_start_and_duration(self):
        # map_part 透传后：tool_use 带 durationMs + time（Task 2 的 map_part 改动）
        mapped = {'type': 'tool_use', 'name': 'read', 'status': 'completed',
                  'input': {'filePath': 'a.py'}, 'durationMs': 2_500,
                  'time': {'start': 1_700_000_000_000, 'end': 1_700_000_002_500}}
        rows = extract_from_part_map({'p9': mapped})
        pid, tool, args, state, started, dur, meta_sid = rows[0]
        assert tool == 'read' and dur == 2_500
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    def test_subtask_use_maps_to_task_row(self):
        """map_part 对 tool:'task' 的 subtask_use 占位（含透传 time）必须还原
        成 task 行：交互路径此前整类被跳过 → 委派从不落账，性能分解的
        「开子代理」段与嵌套分解缺数据。"""
        mapped = {'type': 'subtask_use', 'subtaskId': 'ses_child_1',
                  'agent': 'general', 'description': '统计行数',
                  'status': 'completed', 'segmentCount': 1,
                  'time': {'start': 1_700_000_000_000, 'end': 1_700_000_009_000}}
        rows = extract_from_part_map({'pt1': mapped})
        assert len(rows) == 1
        pid, tool, args, state, started, dur, meta_sid = rows[0]
        assert pid == 'pt1' and tool == 'task' and state == 'completed'
        assert args == '统计行数'
        assert dur == 9_000
        assert started == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
        assert meta_sid == 'ses_child_1'

    def test_subtask_use_without_time_yields_nones(self):
        """占位没带 time（live 事件只到 pending/running 时）：时长与起点为
        None，交给子代理生命周期兜底回填——但行仍要出（subtask 链接在）。"""
        mapped = {'type': 'subtask_use', 'subtaskId': 'ses_child_2',
                  'agent': 'general', 'description': 'd', 'status': 'running'}
        pid, tool, args, state, started, dur, meta_sid = \
            extract_from_part_map({'pt2': mapped})[0]
        assert tool == 'task' and state == 'running'
        assert started is None and dur is None
        assert meta_sid == 'ses_child_2'
