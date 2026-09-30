"""全局技能。"""

GLOBAL_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS global_skills (
    id          VARCHAR(100) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    uploaded_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
    file_size   INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

def _run_dated_migrations():
    """执行 migrations/ 下按日期命名的 schema 迁移(20*.py,均暴露幂等 run())。

    init_db 与 app.py 启动钩子共用这批迁移文件作为唯一事实来源——此前"迁移
    建表、init_db 遗漏"导致全新环境缺表(生产 ai_skill_invocations 事故即此)。
    按文件名日期序执行,保证外键依赖(如 skillopt 依赖先建的执行审计表)。
    任一迁移失败即非零退出,避免静默半初始化。须在主 DDL 与各业务 DDL 之后
    调用(迁移可能引用基础表)。

    两遍扫描：文件名序与依赖序不完全一致时(如 2026_09_18_session_group_icon
    按名序排在 session_groups 之前、但其依赖后者建的表),第二遍重试首轮失败
    的迁移——迁移全部幂等,重试无副作用;两遍后仍失败才非零退出。"""
    import importlib.util
    mig_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations')
    files = sorted(f for f in os.listdir(mig_dir)
                   if f.startswith('20') and f.endswith('.py'))

    def _load(fname):
        spec = importlib.util.spec_from_file_location(
            f'_initdb_mig_{fname[:-3]}', os.path.join(mig_dir, fname))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    failures = []
    for fname in files:
        try:
            _load(fname).run()
            print(f"migration {fname}: ok")
        except Exception as e:
            failures.append(fname)
            print(f"migration {fname}: FAILED — {e}")
    if failures:
        # 第二遍：先跑完的迁移可能补齐了依赖，重试首轮失败者
        retry = list(failures)
        failures = []
        for fname in retry:
            try:
                _load(fname).run()
                print(f"migration {fname}: ok (retry pass)")
            except Exception as e:
                failures.append(fname)
                print(f"migration {fname}: FAILED — {e}")
    if failures:
        raise SystemExit(f"{len(failures)} migration(s) failed: "
                         + ', '.join(failures))
