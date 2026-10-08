# -*- coding: utf-8 -*-
"""迁移启动注册护栏（生产「拉代码+重启」路径防漏注册）。

app.py 的启动迁移块是显式注册表：init_db 的 _run_dated_migrations 自动扫描
只保证**全新库**带表，生产升级走「拉代码 + 重启」，不经过 init_db——新迁移
若不在 app.py 显式注册，生产库就永远缺表（2026_09_18_skillopt_p2 漏注册
事故、2026_10_01_skill_fit_tables 漏注册致生产任务拟合页全空，同因两次）。

护栏：自注册表政策生效日（2026_09_17，首个注册块）起，migrations/ 下每个
带日期的迁移文件必须在 app.py 源码中被引用（按文件名）。更早的迁移属于
建库基线，不要求。
"""
import os
import re

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGISTRATION_POLICY_SINCE = '2026_09_17'


def test_dated_migrations_are_registered_in_app_boot():
    migrations_dir = os.path.join(_SERVER_DIR, 'migrations')
    dated = sorted(
        f for f in os.listdir(migrations_dir)
        if re.fullmatch(r'2026_\d{2}_\d{2}[_\w]*\.py', f)
        and f[:10] >= _REGISTRATION_POLICY_SINCE
    )
    assert dated, '迁移目录扫描异常：未找到带日期迁移文件'
    with open(os.path.join(_SERVER_DIR, 'app.py'), encoding='utf-8') as f:
        app_src = f.read()
    missing = [f for f in dated if f"migrations', '{f}'" not in app_src
               and f"'{f}'" not in app_src]
    assert not missing, (
        f'以下迁移未在 app.py 启动注册表注册——生产「拉代码+重启」路径不会跑 '
        f'init_db 自动扫描，生产库将缺表：{missing}。'
        f'请在 app.py 迁移块区（仿 2026_10_05 块）补注册。')
