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
from routes.open_api_ai_sessions import open_api_ai_sessions_bp
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
from routes.ai_skills import ai_skills_bp
from routes.ai_opencode_admin import ai_opencode_admin_bp
from routes.ai_scan_tasks import ai_scan_tasks_bp
from routes.ai_memory_internal import ai_memory_internal_bp
from routes.data_files import data_files_bp
from routes.roles import roles_bp
from routes.workflows import workflows_bp
from routes.import_runs import import_runs_bp
from routes.kefu_admin import kefu_admin_bp
from routes.kefu_public import kefu_public_bp
from utils.logging_setup import setup_logging

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
app.register_blueprint(open_api_ai_sessions_bp)
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


@app.before_request
def _ai_open_api_body_limit():
    """AI 对外 JSON 端点的请求体门（utils/upload_limits.body_limit_for_path 的
    Flask 侧执行点之二）。

    open_api_batches 蓝图有自己的 before_request（语义相同）；这里补上其余
    AI 对外家族（/v1/ai-sessions、/v1/memories、/v1/prompt-templates、
    /v1/ai-scan-tasks、行操作 run）——此前这些端点只有生产代理 proxy.py 会
    拦，直连后端 / 经 Vite 开发代理的超大 JSON 会完整进入 Flask。函数对
    限度之外的路径（含备份还原、/v1/collections 数据接口）一律放行，详见
    upload_limits 模块注释。"""
    from utils.upload_limits import body_limit_for_path
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
try:
    import importlib.util as _ilu
    _mp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'migrations', '2026_09_17_execution_audit_tables.py')
    _spec = _ilu.spec_from_file_location('_exec_audit_migration_boot', _mp)
    _m = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_m)
    _m.run()
except Exception as _e:
    logging.warning('execution audit migration on boot failed: %s', _e)

# SkillOpt P2：运行时插件自动安装（skill load/invoke 事件上报 → invoked
# confirmed）与审计事件保留策略（明文 30 天/行 180 天）
try:
    import os as _os
    from utils.opencode_global import OPENCODE_GLOBAL_DIR as _OGD  # noqa
except Exception:
    _OGD = None
try:
    from utils import skillopt as _sk
    # 插件运行在 OpenCode 宿主进程内，直连 Flask 网关最短路径；端口跟随
    # FLASK_PORT，路径必须与 routes/ai_memory_internal.py 的实际路由一致。
    from config import FLASK_PORT as _FPORT
    _ep = f'http://127.0.0.1:{_FPORT}/ai/memory/internal/runtime-events'
    _sk.ensure_runtime_plugin(_OGD, _ep,
                              _os.getenv('MCP_INTERNAL_TOKEN', ''))
    _ret = _sk.apply_retention()
    logging.info('SkillOpt: runtime plugin ensured at %s; retention %s',
                 _OGD, _ret)

    def _audit_retention_daily():
        try:
            r = _sk.apply_retention()
            logging.info('SkillOpt retention: %s', r)
        except Exception as e2:
            logging.warning('SkillOpt retention failed: %s', e2)

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _sched = BackgroundScheduler(timezone='Asia/Shanghai')
        _sched.add_job(_audit_retention_daily, 'cron', hour=3, minute=17,
                       id='execution-audit-retention', replace_existing=True)
        _sched.start()
        logging.info('SkillOpt retention scheduler started (03:17 daily)')
    except Exception as e2:
        logging.warning('SkillOpt retention scheduler failed: %s', e2)
except Exception as _e:
    logging.warning('SkillOpt boot failed (non-fatal): %s', _e)

# 会话自定义分组表（2026-09-18）：同样随启动幂等执行
try:
    _mp2 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'migrations', '2026_09_18_session_groups.py')
    _spec2 = _ilu.spec_from_file_location('_session_groups_migration_boot', _mp2)
    _m2 = _ilu.module_from_spec(_spec2)
    _spec2.loader.exec_module(_m2)
    _m2.run()
except Exception as _e:
    logging.warning('session groups migration on boot failed: %s', _e)

# 分组图标列（2026-09-18）：随启动幂等执行
try:
    _mp4 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'migrations', '2026_09_18_session_group_icon.py')
    _spec4 = _ilu.spec_from_file_location('_session_group_icon_boot', _mp4)
    _m4 = _ilu.module_from_spec(_spec4)
    _spec4.loader.exec_module(_m4)
    _m4.run()
except Exception as _e:
    logging.warning('session group icon migration on boot failed: %s', _e)

# 会话置顶列（2026-09-19）：随启动幂等执行
try:
    _mp5 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'migrations', '2026_09_19_session_pin.py')
    _spec5 = _ilu.spec_from_file_location('_session_pin_boot', _mp5)
    _m5 = _ilu.module_from_spec(_spec5)
    _spec5.loader.exec_module(_m5)
    _m5.run()
except Exception as _e:
    logging.warning('session pin migration on boot failed: %s', _e)

# 扫描任务产出文件字段（2026-09-18）：随启动幂等执行
try:
    _mp3 = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'migrations', '2026_09_18_scan_output_field.py')
    _spec3 = _ilu.spec_from_file_location('_scan_output_field_boot', _mp3)
    _m3 = _ilu.module_from_spec(_spec3)
    _spec3.loader.exec_module(_m3)
    _m3.run()
except Exception as _e:
    logging.warning('scan output field migration on boot failed: %s', _e)
app.register_blueprint(ai_skills_bp)
app.register_blueprint(ai_opencode_admin_bp)
app.register_blueprint(ai_scan_tasks_bp)
app.register_blueprint(ai_memory_internal_bp)
app.register_blueprint(data_files_bp)
app.register_blueprint(roles_bp)
app.register_blueprint(workflows_bp)
app.register_blueprint(kefu_admin_bp)
app.register_blueprint(kefu_public_bp)
app.register_blueprint(dynamic_bp)

# Start backup scheduler (only in the reloader child process to avoid double-start).
# Also skip background workers when pytest is driving the process — otherwise the
# batch worker steals pending rows the route tests just inserted.
_RUNNING_UNDER_PYTEST = 'pytest' in sys.modules
if (not FLASK_DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true') \
        and not _RUNNING_UNDER_PYTEST:
    from utils.backup import start_backup_scheduler
    start_backup_scheduler(app)

    # Start dependency validation scheduler
    from utils.dependency_scheduler import start_dependency_scheduler
    start_dependency_scheduler(app)

    # Start in-process batch worker (drives child sessions via OpenCode HTTP API)
    from utils.batch_engine import get_worker
    get_worker().start()

    # Start scheduled AI row-processor scheduler
    from utils.ai_scan_scheduler import start_scan_scheduler
    start_scan_scheduler(app)

    # Start statusBadge timeout fallback scheduler
    from utils.status_badge_timeout_scheduler import start_status_badge_timeout_scheduler
    start_status_badge_timeout_scheduler(app)

    # Start field-index build/drop worker (async CREATE/DROP INDEX CONCURRENTLY)
    from utils.field_index_scheduler import start_field_index_scheduler
    start_field_index_scheduler(app)

    # Start ETL background scheduler (async run of large imports, see utils/etl_scheduler.py)
    from utils.etl_scheduler import start_etl_scheduler
    start_etl_scheduler(app)

def _start_audit_retention_job():
    """每日 03:17 执行审计事件分层保留（SkillOpt P2）。"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(timezone='Asia/Shanghai')
        sched.add_job(lambda: _sk.apply_retention(), 'cron', hour=3, minute=17,
                      id='execution-audit-retention', replace_existing=True)
        sched.start()
        logging.info('execution audit retention scheduler started (03:17 daily)')
    except Exception as e:
        logging.warning('audit retention scheduler failed: %s', e)


_start_audit_retention_job()

if __name__ == '__main__':
    # threaded=True: serve requests concurrently (one thread per request) so a
    # slow outbound call (AI query, webhook, ETL) no longer blocks the whole
    # backend. The DB layer already uses a thread-safe ThreadedConnectionPool.
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True,
            exclude_patterns=['*/backups/*'])
