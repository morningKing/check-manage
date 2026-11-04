import logging
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request
from flask_cors import CORS
from config import (FLASK_PORT, FLASK_DEBUG, CORS_ALLOWED_ORIGINS,
                    JWT_SECRET_IS_DEFAULT)
from routes.menus import menus_bp
from routes.page_configs import page_configs_bp
from routes.relations import relations_bp
from routes.dynamic import dynamic_bp
from routes.auth import auth_bp
from routes.users import users_bp
from routes.operation_logs import operation_logs_bp
from routes.backups import backups_bp
from routes.export_scripts import export_scripts_bp
from routes.api_keys import api_keys_bp
from routes.open_api import open_api_bp
from routes.open_api_batches import open_api_batches_bp
from routes.open_api_row_actions import open_api_row_actions_bp
from routes.open_api_scan_tasks import open_api_scan_tasks_bp
from routes.open_api_prompt_templates import open_api_prompt_templates_bp
from routes.open_api_memories import open_api_memories_bp
from routes.open_api_orchestrations import open_api_orchestrations_bp
from routes.open_api_ai_sessions import open_api_ai_sessions_bp
from routes.external_takeover import external_takeover_bp
from routes.validation_scripts import validation_scripts_bp
from routes.etl_tasks import etl_tasks_bp
from routes.relation_graph import relation_graph_bp
from routes.query import query_bp
from routes.comments import comments_bp
from routes.timeline import timeline_bp
from routes.dashboards import dashboards_bp
from routes.notifications import notifications_bp
from routes.trigger_rules import trigger_rules_bp
from routes.ai import ai_bp
from routes.project_versions import project_versions_bp
from routes.cross_project_dependencies import cross_project_deps_bp
from routes.webhooks import webhook_bp
from routes.menu_export import menu_export_bp
from routes.system_config import system_config_bp
from routes.home_widgets import home_widgets_bp
from routes.column_views import column_views_bp
from routes.ai_chat import ai_chat_bp
from routes.ai_chat_prompt_templates import ai_chat_prompt_templates_bp
from routes.ai_chat_batches import ai_chat_batches_bp
from routes.ai_batch_admin import ai_batch_admin_bp
from routes.ai_session_admin import ai_session_admin_bp, ai_execution_admin_bp
from routes.metrics import metrics_bp
from routes.ai_skills import ai_skills_bp
from routes.ai_opencode_admin import ai_opencode_admin_bp
from routes.ai_scan_tasks import ai_scan_tasks_bp
from routes.ai_memory_internal import ai_memory_internal_bp
from routes.ai_gate_internal import ai_gate_internal_bp, token_fingerprint
from routes.ai_gate_policies import ai_gate_policies_bp
from routes.ai_logs import ai_logs_bp
from routes.ai_data_internal import ai_data_internal_bp
from routes.ai_subagent_internal import ai_subagent_internal_bp
from routes.ai_orchestrations import ai_orchestrations_bp
from routes.ai_approvals import ai_approvals_bp
from routes.artifacts import artifacts_bp
from routes.data_files import data_files_bp
from routes.roles import roles_bp
from routes.workflows import workflows_bp
from routes.import_runs import import_runs_bp
from routes.kefu_admin import kefu_admin_bp
from routes.kefu_public import kefu_public_bp
from routes.suggest_editor import suggest_editor_bp
from routes.plugins_admin import plugins_bp
from common.logging_setup import setup_logging

# Configure logging (console + rotating file) before anything logs. Skip the
# file handler under pytest so test runs don't spew ai-chat.log.
setup_logging(to_file='pytest' not in sys.modules)

# 未设置 JWT_SECRET 时，登录令牌是用一个**仓库里公开可见的常量**签的 —— 任何读过
# 源码的人都能伪造管理员 token。config.py 已经保证默认值长度合法（否则新版 PyJWT
# 会拒签），所以它不会再以报错的形式暴露出来，只能靠这里喊一嗓子。
if JWT_SECRET_IS_DEFAULT and 'pytest' not in sys.modules:
    logging.getLogger(__name__).warning(
        '安全警告：未设置 JWT_SECRET，正在使用源码里的公开默认值签发登录令牌。'
        '任何人都可据此伪造任意用户（含管理员）的身份。'
        '生产环境务必设置：python -c "import secrets;print(secrets.token_urlsafe(48))" '
        '并写入 server/.env 的 JWT_SECRET=')

app = Flask(__name__)
if CORS_ALLOWED_ORIGINS:
    CORS(app, origins=CORS_ALLOWED_ORIGINS)
elif FLASK_DEBUG:
    CORS(app)

# Register blueprints - auth first, then static routes, then catch-all dynamic routes
app.register_blueprint(auth_bp)
app.register_blueprint(users_bp)
app.register_blueprint(menus_bp)
app.register_blueprint(page_configs_bp)
app.register_blueprint(relations_bp)
app.register_blueprint(operation_logs_bp)
app.register_blueprint(backups_bp)
app.register_blueprint(export_scripts_bp)
app.register_blueprint(api_keys_bp)
app.register_blueprint(open_api_bp)
app.register_blueprint(open_api_batches_bp)
app.register_blueprint(open_api_row_actions_bp)
app.register_blueprint(open_api_scan_tasks_bp)
app.register_blueprint(open_api_prompt_templates_bp)
app.register_blueprint(open_api_memories_bp)
app.register_blueprint(open_api_orchestrations_bp)
app.register_blueprint(open_api_ai_sessions_bp)
app.register_blueprint(external_takeover_bp)
app.register_blueprint(validation_scripts_bp)
app.register_blueprint(etl_tasks_bp)
app.register_blueprint(relation_graph_bp)
app.register_blueprint(query_bp)
app.register_blueprint(comments_bp)
app.register_blueprint(timeline_bp)
app.register_blueprint(dashboards_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(trigger_rules_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(project_versions_bp)
app.register_blueprint(cross_project_deps_bp)
app.register_blueprint(webhook_bp)
app.register_blueprint(import_runs_bp)
app.register_blueprint(menu_export_bp)
app.register_blueprint(system_config_bp)
app.register_blueprint(home_widgets_bp)
app.register_blueprint(column_views_bp)
app.register_blueprint(ai_chat_bp)
app.register_blueprint(ai_chat_prompt_templates_bp)
app.register_blueprint(ai_chat_batches_bp)
app.register_blueprint(ai_batch_admin_bp)
app.register_blueprint(ai_session_admin_bp)
app.register_blueprint(ai_execution_admin_bp)
# 监控端点必须在 catch-all dynamic_bp 之前注册（P1-B3）
app.register_blueprint(metrics_bp)


@app.before_request
def _ai_open_api_body_limit():
    """AI 对外 JSON 端点的请求体门（common/upload_limits.body_limit_for_path 的
    Flask 侧执行点之二）。

    open_api_batches 蓝图有自己的 before_request（语义相同）；这里补上其余
    AI 对外家族（/v1/ai-sessions、/v1/memories、/v1/prompt-templates、
    /v1/ai-scan-tasks、行操作 run）——此前这些端点只有生产代理 proxy.py 会
    拦，直连后端 / 经 Vite 开发代理的超大 JSON 会完整进入 Flask。函数对
    限度之外的路径（含备份还原、/v1/collections 数据接口）一律放行，详见
    upload_limits 模块注释。"""
    from common.upload_limits import body_limit_for_path
    limit = body_limit_for_path(request.path)
    if limit is None:
        return None
    length = request.content_length
    if length is None:
        if 'chunked' in (request.headers.get('Transfer-Encoding') or '').lower():
            return jsonify({'error': '请求必须携带 Content-Length，不支持分块传输',
                            'code': 'INVALID_ARGUMENT'}), 411
        return None
    if length > limit:
        return jsonify({'error': f'请求体超过 {limit // 1024 // 1024} MB 的上限',
                        'code': 'PAYLOAD_TOO_LARGE'}), 413
    return None

# 执行审计表/回填随启动幂等执行（execution-audit §17）：旧分析会话
# （标题前缀）自动补 kind=trace_analysis，无需手动跑迁移。
# 迁移启动注册表（显式有序，行为等价于原逐段 try/except 块；新迁移必须在此
# 登记且顺序不可调整——后续迁移依赖前面迁移建的表。init_db 的自动扫描不覆盖
# 生产重启路径，见 docs/superpowers/specs/2026-11-03-backend-restructure-design.md §7）。
# SkillOpt/插件启动块（原 L182-322）位于 PRE 与 MAIN 之间，执行顺序原样保留。
_BOOT_MIGRATIONS_PRE = [
    ('2026_09_17_execution_audit_tables.py',
     'execution audit migration on boot failed'),
]


def _run_boot_migrations(entries):
    import importlib.util as _ilu
    _base = os.path.dirname(os.path.abspath(__file__))
    for _fname, _errmsg in entries:
        _mp = os.path.join(_base, 'migrations', _fname)
        try:
            _spec = _ilu.spec_from_file_location('_boot_' + _fname[:-3], _mp)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            _m.run()
        except Exception as _e:
            logging.warning('%s: %s', _errmsg, _e)


_run_boot_migrations(_BOOT_MIGRATIONS_PRE)

# SkillOpt P2：运行时插件自动安装（skill load/invoke 事件上报 → invoked
# confirmed）与审计事件保留策略（明文 30 天/行 180 天）
try:
    import os as _os
    from ai.opencode.opencode_global import OPENCODE_GLOBAL_DIR as _OGD  # noqa
except Exception:
    _OGD = None
try:
    from ai.skills import skillopt as _sk
    # 插件运行在 OpenCode 宿主进程内，直连 Flask 网关最短路径；端口跟随
    # FLASK_PORT，路径必须与 routes/ai_memory_internal.py 的实际路由一致。
    from config import FLASK_PORT as _FPORT
    _ep = f'http://127.0.0.1:{_FPORT}/ai/memory/internal/runtime-events'
    # PreToolUse 门禁校验端点（P3-C3）：与 skill 上报同一插件进程、同一 token
    _gate_ep = f'http://127.0.0.1:{_FPORT}/ai/gate/internal/pre-check'
    # checkpoint after 时机 post-check 端点：after 钩子非阻断追加注入
    _gate_post_ep = f'http://127.0.0.1:{_FPORT}/ai/gate/internal/post-check'
    _sk.ensure_runtime_plugin(_OGD, _ep,
                              _os.getenv('MCP_INTERNAL_TOKEN', ''),
                              gate_endpoint=_gate_ep,
                              gate_post_endpoint=_gate_post_ep)
    # 内部通道 token 留痕（2026-10-15 生产排查）：配置值指纹随启动打印——
    # 与插件加载日志的 fingerprint、403 日志的 server=/got= 三处对照，
    # token 错位无需再猜。未配置时全部内部端点一律 403（消费方 fail-open
    # 静默降级，见 docs/ops/mcp-internal-token-403-修复.md）。
    _tok = _os.getenv('MCP_INTERNAL_TOKEN', '')
    if not _tok:
        logging.warning(
            'MCP_INTERNAL_TOKEN 未配置：所有内部端点（门禁/记忆/轨迹上报/data）'
            '一律 403，消费方全部 fail-open 静默降级')
    else:
        logging.info('MCP_INTERNAL_TOKEN configured fingerprint=%s',
                     token_fingerprint(_tok))
    _ret = _sk.apply_retention()
    # 运营日志表保留（大数据量优化 §2.2/2.3/2.4）：operation_logs /
    # agent_tool_calls / webhook_logs 分批清理过期行
    try:
        from common.log_retention import apply_log_retention
        _lr = apply_log_retention()
        logging.info('log retention applied: %s', _lr)
    except Exception as _e_lr:
        logging.warning('log retention failed: %s', _e_lr)
    logging.info('SkillOpt: runtime plugin ensured at %s; retention %s',
                 _OGD, _ret)

    # 子代理会话复用插件（tool.execute.before 强制注入 task_id）：与 SkillOpt
    # 插件同目录部署；OC serve 启动时加载——部署后需重启 serve 生效。
    try:
        from ai.opencode.subagent_reuse_plugin import ensure_subagent_reuse_plugin
        _sru_ep = f'http://127.0.0.1:{_FPORT}/ai/subagent-internal'
        ensure_subagent_reuse_plugin(_OGD, _sru_ep,
                                     _os.getenv('MCP_INTERNAL_TOKEN', ''))
    except Exception as e3:
        logging.warning('subagent reuse plugin deploy failed: %s', e3)

    # 判官 agent（动作门禁 verifier 的 OpenCode 子代理定义）：与上述插件同
    # 目录部署；OC serve 启动时加载——部署后需重启 serve 生效。
    try:
        from ai.batch.verifier import ensure_verifier_agent
        ensure_verifier_agent(_OGD)
    except Exception as e5:
        logging.warning('verifier agent deploy failed: %s', e5)  # 判官 agent 部署失败不阻断启动（与插件同口径）；核对期走 fail-closed

    # 内置工具超时门限插件（tool.execute.before 挂看门狗，超时 abort 会话）：
    # 补齐交互侧门限——OC 内置工具仅 bash/webfetch 自带超时，其余无门限。
    # serve 启动时加载——部署后需重启 serve 生效。
    try:
        from ai.opencode.tool_timeout_plugin import (ensure_tool_timeout_plugin,
                                               DEFAULT_EXEMPT, DEFAULT_TIMEOUT_MS)
        _tt_ms = int(_os.getenv('OPENCODE_TOOL_TIMEOUT_MS', '') or DEFAULT_TIMEOUT_MS)
        _tt_exempt = (_os.getenv('OPENCODE_TOOL_TIMEOUT_EXEMPT', '') or DEFAULT_EXEMPT)
        ensure_tool_timeout_plugin(_OGD, _tt_ms, _tt_exempt)
    except Exception as e4:
        logging.warning('tool timeout plugin deploy failed: %s', e4)

    def _audit_retention_daily():
        try:
            r = _sk.apply_retention()
            logging.info('SkillOpt retention: %s', r)
        except Exception as e2:
            logging.warning('SkillOpt retention failed: %s', e2)
        # 运营日志表保留（与执行事件 retention 同一每日作业）
        try:
            from common.log_retention import apply_log_retention
            lr = apply_log_retention()
            logging.info('log retention: %s', lr)
        except Exception as e2:
            logging.warning('log retention failed: %s', e2)

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _sched = BackgroundScheduler(timezone='Asia/Shanghai')
        _sched.add_job(_audit_retention_daily, 'cron', hour=3, minute=17,
                       id='execution-audit-retention', replace_existing=True)
        # P1 §9.3/§7.3：workspace 回收 + ai_batch_events 保留（同一每日作业）
        def _workspace_and_events_retention_daily():
            try:
                from projects.workspace import reclaim_expired_workspaces
                logging.info('workspace retention: reclaimed %s',
                             reclaim_expired_workspaces())
            except Exception as e3:
                logging.warning('workspace retention failed: %s', e3)
            try:
                from ai.sessions.artifact_store import purge_expired
                logging.info('artifact retention: purged %s', purge_expired())
            except Exception as e3:
                logging.warning('artifact retention failed: %s', e3)
            try:
                from config import AI_BATCH_EVENT_RETENTION_DAYS as _d
                from db import get_db as _gdb
                with _gdb() as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "DELETE FROM ai_batch_events "
                            "WHERE created_at < NOW() - (%s || ' days')::interval",
                            (str(_d),))
                        _n = _cur.rowcount
                    _conn.commit()
                if _n:
                    logging.info('batch events retention: pruned %s', _n)
            except Exception as e3:
                logging.warning('batch events retention failed: %s', e3)

        _sched.add_job(_workspace_and_events_retention_daily, 'cron',
                       hour=3, minute=41,
                       id='workspace-events-retention', replace_existing=True)
        # P2-B2：统一租约框架——audit retention（03:17 每日作业，含 SkillOpt
        # retention + 运营日志 retention）抢 audit_retention scheduler 租约，
        # 抢不到不在本进程启动（多实例部署只跑一份；本批不做后台重试）。
        from ai.batch import execution_lease as execution_lease
        _ar_ok, _ = execution_lease.acquire(
            'audit_retention', execution_lease.owner_id(),
            lease_kind='scheduler')
        if not _ar_ok:
            logging.warning('audit retention scheduler lease NOT acquired; '
                            'disabled in this process')
        else:
            _sched.start()
            logging.info('SkillOpt retention scheduler started (03:17 daily)')
    except Exception as e2:
        logging.warning('SkillOpt retention scheduler failed: %s', e2)
except Exception as _e:
    logging.warning('SkillOpt boot failed (non-fatal): %s', _e)

_BOOT_MIGRATIONS_MAIN = [
    ('2026_09_18_session_groups.py', 'session groups migration on boot failed'),
    ('2026_09_18_session_group_icon.py', 'session group icon migration on boot failed'),
    ('2026_09_19_session_pin.py', 'session pin migration on boot failed'),
    ('2026_09_18_scan_output_field.py', 'scan output field migration on boot failed'),
    ('2026_09_20_agent_action_gate.py', 'agent action gate migration on boot failed'),
    ('2026_09_18_skillopt_p2.py', 'skillopt p2 migration on boot failed'),
    ('2026_09_21_batch_child_soft_delete.py', 'batch child soft-delete migration on boot failed'),
    ('2026_09_23_harness_p0_execution_safety.py', 'harness p0 migration on boot failed'),
    ('2026_09_23_harness_p1_durable_execution.py', 'harness p1 migration on boot failed'),
    ('2026_09_23_harness_p2_orchestration.py', 'harness p2 migration on boot failed'),
    ('2026_09_24_subagent_session_reuse.py', 'subagent reuse migration on boot failed'),
    ('2026_09_25_artifact_owner_scope.py', 'artifact owner scope migration on boot failed'),
    ('2026_09_28_message_search_text.py', 'message search_text migration on boot failed'),
    ('2026_09_29_tool_calls_backfill.py', 'tool calls backfill migration on boot failed'),
    ('2026_09_29_embedding_provider.py', 'embedding provider migration on boot failed'),
    ('2026_09_26_batch_notify_trigger.py', 'batch notify trigger migration on boot failed'),
    ('2026_09_26_action_gate_mode.py', 'action gate mode migration on boot failed'),
    ('2026_10_11_gate_checkpoint_policy.py', 'gate checkpoint/policy migration on boot failed'),
    ('2026_10_01_skill_fit_tables.py', 'skill fit tables migration on boot failed'),
    ('2026_10_05_def_version_content_archive.py', 'def version content archive migration on boot failed'),
    ('2026_10_27_run_diagnoses.py', 'run diagnoses migration on boot failed'),
    ('2026_10_27_skill_candidates.py', 'skill candidates migration on boot failed'),
    ('2026_10_10_tool_call_duration.py', 'tool call duration migration on boot failed'),
    ('2026_10_18_suggest_editor_tables.py', 'suggest editor migration on boot failed'),
    ('2026_10_21_suggest_editor_attachments.py', 'suggest editor attachments migration on boot failed'),
    ('2026_10_25_batch_takeover.py', 'batch takeover migration on boot failed'),
    ('2026_10_25b_platform_plugins.py', 'platform plugins migration on boot failed'),
    ('2026_10_25c_mcp_tool_governance.py', 'mcp tool governance migration on boot failed'),
    ('2026_10_27_batch_multi_model.py', 'batch multi-model migration on boot failed'),
    ('2026_10_30_message_feedback.py', 'message feedback migration on boot failed'),
    ('2026_10_31_session_permission_mode.py', 'session permission_mode migration on boot failed'),
]

_run_boot_migrations(_BOOT_MIGRATIONS_MAIN)

# 内置技能种子(仓库 skills/ → 全局技能,只插缺不覆盖):部署拉代码重启即自带
try:
    from ai.skills.global_skills import ensure_builtin_skills
    _builtin = ensure_builtin_skills()
    if _builtin:
        logging.info('builtin skills installed: %s', ', '.join(_builtin))
except Exception as _e:
    logging.warning('builtin skills seed on boot failed: %s', _e)
app.register_blueprint(ai_skills_bp)
app.register_blueprint(ai_opencode_admin_bp)
app.register_blueprint(ai_scan_tasks_bp)
app.register_blueprint(ai_memory_internal_bp)
app.register_blueprint(ai_gate_internal_bp)
app.register_blueprint(ai_gate_policies_bp)
app.register_blueprint(ai_logs_bp)
app.register_blueprint(ai_subagent_internal_bp)
app.register_blueprint(ai_data_internal_bp)
app.register_blueprint(ai_orchestrations_bp)
app.register_blueprint(ai_approvals_bp)
app.register_blueprint(artifacts_bp)
app.register_blueprint(data_files_bp)
app.register_blueprint(roles_bp)
app.register_blueprint(workflows_bp)
app.register_blueprint(kefu_admin_bp)
app.register_blueprint(kefu_public_bp)
# 智能写作（suggest-editor）必须在 catch-all dynamic_bp 之前注册
# 第一方插件门（plugin-architecture spec §5）：必须在 register 之前挂——
# Flask 蓝图首次注册后 setup 锁定，之后再 before_request 会 AssertionError。
# smart-writing 禁用即全蓝图 41 端点 404 PLUGIN_DISABLED（gate 内含
# url_prefix 一致性启动校验）。
from ai.opencode.plugin_registry import gate_plugin  # noqa: E402
gate_plugin(suggest_editor_bp, 'smart-writing')
app.register_blueprint(suggest_editor_bp)
# 第一方插件管理 API（plugin-architecture spec §4）——同样先于 catch-all
app.register_blueprint(plugins_bp)
app.register_blueprint(dynamic_bp)

# Start backup scheduler (only in the reloader child process to avoid double-start).
# Also skip background workers when pytest is driving the process — otherwise the
# batch worker steals pending rows the route tests just inserted.
_RUNNING_UNDER_PYTEST = 'pytest' in sys.modules
if (not FLASK_DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true') \
        and not _RUNNING_UNDER_PYTEST:
    from projects.backup import start_backup_scheduler
    start_backup_scheduler(app)

    # Start dependency validation scheduler
    from data.dependency_scheduler import start_dependency_scheduler
    start_dependency_scheduler(app)

    # Start in-process batch worker (drives child sessions via OpenCode HTTP API)
    from ai.batch.engine import get_worker
    get_worker().start()

    # Start scheduled AI row-processor scheduler
    from ai.scan.ai_scan_scheduler import start_scan_scheduler
    start_scan_scheduler(app)

    # Start statusBadge timeout fallback scheduler
    from ai.sessions.status_badge_timeout_scheduler import start_status_badge_timeout_scheduler
    start_status_badge_timeout_scheduler(app)

    # Start field-index build/drop worker (async CREATE/DROP INDEX CONCURRENTLY)
    from data.field_index_scheduler import start_field_index_scheduler
    start_field_index_scheduler(app)

    # OpenCode serve 崩溃看门狗（连续不可达自动拉起；多进程租约互斥）
    try:
        from ai.batch.oc_watchdog import start as _start_oc_watchdog
        _start_oc_watchdog(app)
    except Exception as _e5:
        logging.warning('oc-watchdog start failed: %s', _e5)

    # Start ETL background scheduler (async run of large imports, see data/etl_scheduler.py)
    from data.etl_scheduler import start_etl_scheduler
    start_etl_scheduler(app)

    # P1：outbox 投递器（持 delivery 租约；AI_DELIVERY_OUTBOX_ENABLED=0 时回退
    # 旧直接 POST 回调路径，见 ai/batch/delivery_outbox.py）
    from ai.batch.delivery_outbox import start_delivery_loop
    start_delivery_loop()

    # P2：编排调度器（持 scheduler 租约：DAG 推进 + 审批超时）
    from ai.orchestration.engine import start_scheduler
    start_scheduler()

# F12（ai-harness-p0 spec §9）：审计 retention 任务此前注册了两次（SkillOpt
# 启动块内一个 scheduler + 下面 _start_audit_retention_job 又一个 scheduler），
# replace_existing 只在同一 scheduler 内去重，两个实例互不可见——apply_retention
# 每天 03:17 跑两遍。只保留 SkillOpt 块内那一处注册，这里不再重复建。

if __name__ == '__main__':
    # threaded=True: serve requests concurrently (one thread per request) so a
    # slow outbound call (AI query, webhook, ETL) no longer blocks the whole
    # backend. The DB layer already uses a thread-safe ThreadedConnectionPool.
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True,
            exclude_patterns=['*/backups/*'])
