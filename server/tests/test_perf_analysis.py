# -*- coding: utf-8 -*-
"""SkillOpt 任务性能分析（spec 2026-10-09）：覆盖切分/任务指标/诊断规则/端点。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.perf_analysis import coverage_split


class TestCoverageSplit:
    def test_disjoint_no_overlap(self):
        # 墙钟 0..1000；模型 [0,400]，子代理 [600,1000]（与模型不重叠）
        r = coverage_split(0, 1000, [(0, 400)], [(600, 1000)])
        assert r == {'wallMs': 1000, 'modelMs': 400, 'subagentWaitMs': 400, 'idleMs': 200}

    def test_subagent_under_model_is_wait_zero(self):
        # 子代理区间完全落在模型活跃内（父轮次在等 task 返回）→ 等待为 0
        r = coverage_split(0, 1000, [(0, 1000)], [(200, 800)])
        assert r['subagentWaitMs'] == 0 and r['modelMs'] == 1000 and r['idleMs'] == 0

    def test_partial_overlap_counts_only_exposed_wait(self):
        # 子代理 [300,900] 与模型 [0,500] 重叠 200 → 等待只算露出的 [500,900]=400
        r = coverage_split(0, 1000, [(0, 500)], [(300, 900)])
        assert r['subagentWaitMs'] == 400 and r['idleMs'] == 100

    def test_overlapping_model_turns_unioned_not_summed(self):
        # 两轮模型区间重叠 [200,400]：并集 [0,600]=600，不是 400+400
        r = coverage_split(0, 1000, [(0, 400), (200, 600)], [])
        assert r['modelMs'] == 600 and r['idleMs'] == 400

    def test_empty_intervals_all_idle(self):
        r = coverage_split(0, 500, [], [])
        assert r == {'wallMs': 500, 'modelMs': 0, 'subagentWaitMs': 0, 'idleMs': 500}

    def test_intervals_outside_wall_clamped(self):
        r = coverage_split(100, 500, [(0, 300)], [(400, 900)])
        assert r['modelMs'] == 200 and r['subagentWaitMs'] == 100 and r['wallMs'] == 400

    def test_sum_equals_wall(self):
        r = coverage_split(0, 9999, [(0, 3000), (2500, 7000)], [(1000, 2000), (6500, 9999)])
        assert r['modelMs'] + r['subagentWaitMs'] + r['idleMs'] == r['wallMs']
