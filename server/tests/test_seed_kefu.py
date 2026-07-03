from unittest.mock import patch
import seed_kefu
from seed_kefu import seed_kefu_instance, seed_kefu_demo, DEMO_INSTANCE, DEMO_FAQ


def test_seed_creates_when_absent():
    spec = {'slug': 'demo-seedtest', 'name': 'T'}
    faqs = [{'question': 'q1', 'answer': 'a1'}, {'question': 'q2', 'answer': 'a2'}]
    with patch.object(seed_kefu.kefu_repo, 'get_instance_by_slug', return_value=None), \
         patch.object(seed_kefu.kefu_repo, 'create_instance',
                      return_value={'id': 'kf_x', 'slug': 'demo-seedtest'}) as mc, \
         patch.object(seed_kefu.kefu_repo, 'create_faq') as mf:
        created = seed_kefu_instance(spec, faqs)
    assert created is True
    mc.assert_called_once_with(spec)
    assert mf.call_count == 2
    assert mf.call_args_list[0].args[0] == 'kf_x'   # faq attached to created instance id


def test_seed_skips_when_present():
    with patch.object(seed_kefu.kefu_repo, 'get_instance_by_slug',
                      return_value={'id': 'kf_e', 'slug': 'demo'}), \
         patch.object(seed_kefu.kefu_repo, 'create_instance') as mc, \
         patch.object(seed_kefu.kefu_repo, 'create_faq') as mf:
        created = seed_kefu_instance({'slug': 'demo'}, DEMO_FAQ)
    assert created is False
    mc.assert_not_called()
    mf.assert_not_called()


def test_demo_constants_shape():
    assert DEMO_INSTANCE['slug'] == 'demo'
    assert DEMO_INSTANCE['name'] == '演示客服'
    assert len(DEMO_INSTANCE['guided_questions']) == 3
    assert [b['type'] for b in DEMO_INSTANCE['panel_blocks']] == ['links', 'faq', 'richtext', 'contact']
    assert len(DEMO_FAQ) == 2


def test_seed_demo_uses_demo_constants():
    with patch('seed_kefu.seed_kefu_instance', return_value=True) as m:
        result = seed_kefu_demo()
    assert result is True
    m.assert_called_once_with(DEMO_INSTANCE, DEMO_FAQ)
