"""幂等 seed 一个演示客服实例（/kefu/demo）。

可由 init_db.py 末尾自动调用，也可单独运行：
    cd server && python seed_kefu.py
已存在同 slug 则跳过，绝不覆盖已有数据。
"""
from utils import kefu_repo

DEMO_INSTANCE = {
    'slug': 'demo',
    'name': '演示客服',
    'welcome_message': '你好 👋 我是在线客服，有什么可以帮你？',
    'guided_questions': ['如何私有化部署？', '有哪些定价套餐？', '怎么申请试用？'],
    'panel_blocks': [
        {'id': 'blk_l', 'type': 'links', 'title': '快捷入口', 'enabled': True,
         'config': {'items': [
             {'url': 'https://example.com/docs', 'icon': '📘', 'label': '帮助文档'},
             {'url': '/tickets', 'icon': '🎫', 'label': '提交工单'}]}},
        {'id': 'blk_f', 'type': 'faq', 'title': '热点问题', 'enabled': True,
         'config': {'limit': 5}},
        {'id': 'blk_r', 'type': 'richtext', 'title': '公告', 'enabled': True,
         'config': {'markdown': '## 服务时间\n工作日 **9:00–18:00**'}},
        {'id': 'blk_c', 'type': 'contact', 'title': '联系我们', 'enabled': True,
         'config': {'phone': '400-123-4567', 'email': 'help@example.com'}},
    ],
}

DEMO_FAQ = [
    {'question': '如何重置密码？', 'category': '账户',
     'answer': '进入设置页，点击"重置密码"，按邮件指引操作。', 'enabled': True},
    {'question': '支持哪些支付方式？', 'category': '支付',
     'answer': '支付宝 / 微信 / 对公转账。', 'enabled': True},
]


def seed_kefu_instance(spec: dict, faqs: list) -> bool:
    """幂等 seed 一个客服实例 + 其 FAQ。已存在同 slug 则跳过返回 False；新建返回 True。"""
    if kefu_repo.get_instance_by_slug(spec['slug']):
        return False
    inst = kefu_repo.create_instance(spec)
    for f in faqs:
        kefu_repo.create_faq(inst['id'], f)
    return True


def seed_kefu_demo() -> bool:
    created = seed_kefu_instance(DEMO_INSTANCE, DEMO_FAQ)
    print('已 seed 演示客服（/kefu/demo）' if created else '演示客服已存在，跳过')
    return created


if __name__ == '__main__':
    seed_kefu_demo()
