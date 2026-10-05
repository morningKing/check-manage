"""压测结果 md 渲染（spec §5）。输入 = capacity 套件落盘的 JSON。"""


def render_capacity_report(data: dict) -> str:
    lines = ['# AI 容量阶梯报告', '',
             f"**容量结论: {data['capacity']} 并发**（最后全绿级）", '',
             '| 级 | done | failed | cancelled | 成功率 | 吞吐(cpm) | 不变量 |',
             '|----|------|--------|-----------|--------|-----------|--------|']
    for lv in data['levels']:
        inv = lv['invariants']
        inv_txt = '绿' if (inv['orphans'] == 0 and inv['zombie_running'] == 0
                           and inv['counter_violations'] == 0) else \
            f"异常(孤儿{inv['orphans']}/僵尸{inv['zombie_running']}/计数{inv['counter_violations']})"
        mark = ' ✅' if lv['success_rate'] >= 0.99 else ' ❌ L%d 未达标' % lv['level']
        lines.append(
            f"| {lv['level']} | {lv['done']} | {lv['failed']} | "
            f"{lv['cancelled']} | {lv['success_rate']:.1%} | "
            f"{lv['throughput_cpm']:.1f} | {inv_txt}{mark} |")
    return '\n'.join(lines)
