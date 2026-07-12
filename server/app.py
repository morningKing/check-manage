import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask
from flask_cors import CORS
from config import FLASK_PORT, FLASK_DEBUG, CORS_ALLOWED_ORIGINS
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
from routes.ai_scan_tasks import ai_scan_tasks_bp
from routes.ai_memory_internal import ai_memory_internal_bp
from routes.data_files import data_files_bp
from routes.roles import roles_bp
from routes.workflows import workflows_bp
from routes.kefu_admin import kefu_admin_bp
from routes.kefu_public import kefu_public_bp
from utils.logging_setup import setup_logging

# Configure logging (console + rotating file) before anything logs. Skip the
# file handler under pytest so test runs don't spew ai-chat.log.
setup_logging(to_file='pytest' not in sys.modules)

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
app.register_blueprint(menu_export_bp)
app.register_blueprint(system_config_bp)
app.register_blueprint(home_widgets_bp)
app.register_blueprint(column_views_bp)
app.register_blueprint(ai_chat_bp)
app.register_blueprint(ai_chat_prompt_templates_bp)
app.register_blueprint(ai_chat_batches_bp)
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

if __name__ == '__main__':
    # threaded=True: serve requests concurrently (one thread per request) so a
    # slow outbound call (AI query, webhook, ETL) no longer blocks the whole
    # backend. The DB layer already uses a thread-safe ThreadedConnectionPool.
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True,
            exclude_patterns=['*/backups/*'])
