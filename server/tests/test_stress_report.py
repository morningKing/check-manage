from tests.stress.report import render_capacity_report


def test_renders_table_and_conclusion():
    data = {'capacity': 20, 'levels': [
        {'level': 5, 'done': 100, 'failed': 0, 'cancelled': 0,
         'success_rate': 1.0, 'throughput_cpm': 30.5,
         'invariants': {'orphans': 0, 'zombie_running': 0,
                        'counter_violations': 0}},
        {'level': 10, 'done': 99, 'failed': 1, 'cancelled': 0,
         'success_rate': 0.99, 'throughput_cpm': 41.2,
         'invariants': {'orphans': 0, 'zombie_running': 0,
                        'counter_violations': 0}},
        {'level': 20, 'done': 90, 'failed': 10, 'cancelled': 0,
         'success_rate': 0.9, 'throughput_cpm': 38.0,
         'invariants': {'orphans': 2, 'zombie_running': 1,
                        'counter_violations': 0}},
    ]}
    md = render_capacity_report(data)
    assert '容量结论: 20 并发' in md
    assert '| 5 |' in md and '| 20 |' in md
    assert 'L20 未达标' in md
