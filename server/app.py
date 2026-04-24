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
app.register_blueprint(dynamic_bp)

# Start backup scheduler (only in the reloader child process to avoid double-start)
if not FLASK_DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    from utils.backup import start_backup_scheduler
    start_backup_scheduler(app)

    # Start dependency validation scheduler
    from utils.dependency_scheduler import start_dependency_scheduler
    start_dependency_scheduler(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=FLASK_DEBUG,
            exclude_patterns=['*/backups/*'])
