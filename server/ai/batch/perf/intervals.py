import time as _time


def _to_ms(dt) -> int | None:
    """psycopg2 timestamptz（tz-aware datetime）→ epoch 毫秒。"""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _union(intervals: list[tuple[int, int]], lo: int, hi: int) -> list[tuple[int, int]]:
    """夹到 [lo,hi] 后合并重叠区间，返回升序不重叠列表。"""
    clamped = [(max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo)]
    out: list[tuple[int, int]] = []
    for s, e in sorted(clamped):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _covered_ms(intervals: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in intervals)


def _subtract(base: list[tuple[int, int]], mask: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base - mask（都为合并过的升序区间），返回剩余区间。"""
    out: list[tuple[int, int]] = []
    for s, e in base:
        cur = s
        for ms, me in mask:
            if me <= cur or ms >= e:
                continue
            if ms > cur:
                out.append((cur, ms))
            cur = max(cur, me)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return out


def _intersect(base: list[tuple[int, int]], mask: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """base ∩ mask（都为合并过的升序区间），返回相交区间。"""
    out: list[tuple[int, int]] = []
    for s, e in base:
        for ms, me in mask:
            if me <= s or ms >= e:
                continue
            out.append((max(s, ms), min(e, me)))
    return out


def coverage_split(wall_start_ms: int, wall_end_ms: int,
                   model_intervals: list[tuple[int, int]],
                   subagent_intervals: list[tuple[int, int]],
                   tool_intervals: list[tuple[int, int]] | None = None,
                   task_intervals: list[tuple[int, int]] | None = None) -> dict:
    """attempt 墙钟 → 互斥覆盖时长。v2 优先级分解（工具 > 子代理等待 > 模型
    > 间隙），四类之和恒 = wallMs：

    - toolMs          非 task 工具覆盖（bash/read 等真实工具执行）
    - subagentWaitMs  子代理跨度 ∪ task 工具跨度（等委托），扣除非 task 工具
                      ——task 跨度无论是否落在轮内都归等待（轮次时长是毛
                      跨度含挂起等委托，不切出来会被误标为模型/工具）
    - modelMs         模型轮次跨度扣除以上两类（纯推理）
    - idleMs          其余
    task_intervals 缺省为空：此时 subagentWait = 子代理跨度原值。"""
    wall_ms = max(0, wall_end_ms - wall_start_ms)
    model = _union(model_intervals, wall_start_ms, wall_end_ms)
    sub = _union(subagent_intervals, wall_start_ms, wall_end_ms)
    tool = _union(tool_intervals or [], wall_start_ms, wall_end_ms)
    task = _union(task_intervals or [], wall_start_ms, wall_end_ms)
    delegate = _union(sub + task, wall_start_ms, wall_end_ms)
    tool_ms = _covered_ms(tool)
    wait_ms = _covered_ms(_subtract(delegate, tool))
    model_ms = _covered_ms(_subtract(_subtract(model, tool), delegate))
    idle_ms = max(0, wall_ms - tool_ms - wait_ms - model_ms)
    return {'wallMs': wall_ms, 'modelMs': model_ms, 'toolMs': tool_ms,
            'subagentWaitMs': wait_ms, 'idleMs': idle_ms}


# ---- 任务指标与聚合（SQL 层）----


def _now_ms() -> int:
    return int(_time.time() * 1000)


def _percentile(sorted_vals: list, q: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]



__all__ = [
    '_time',
    '_to_ms',
    '_union',
    '_covered_ms',
    '_subtract',
    '_intersect',
    'coverage_split',
    '_now_ms',
    '_percentile',
]
