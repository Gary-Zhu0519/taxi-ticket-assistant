from __future__ import annotations

import os
from datetime import datetime

from flask import Flask, g, render_template, url_for

from ai_routes import ai_bp
from auth import auth_bp
from database import db, ensure_supporting_objects, init_app
from services import (
    get_priority_badge,
    get_sidebar_menu_items,
    get_status_badge,
    get_urgency_badge,
    is_ticket_overdue,
)
from views import main_bp


def create_app(test_config: dict | None = None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    if test_config:
        app.config.update(test_config)

    init_app(app)

    with app.app_context():
        db.create_all()
        ensure_supporting_objects()

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(ai_bp)

    @app.context_processor
    def inject_globals():
        current_user = g.get("current_user")
        sidebar_menu = []
        if current_user:
            for item in get_sidebar_menu_items(current_user):
                sidebar_menu.append(
                    {
                        **item,
                        "url": url_for(item["endpoint"], **item.get("params", {})),
                    }
                )
        return {
            "current_user": current_user,
            "status_badge": get_status_badge,
            "priority_badge": get_priority_badge,
            "urgency_badge": get_urgency_badge,
            "is_ticket_overdue": is_ticket_overdue,
            "sidebar_menu": sidebar_menu,
            "system_name": "第三方打车平台后台订单投诉与工单闭环管理系统",
        }

    @app.template_filter("datetime_display")
    def datetime_display(value):
        if not value:
            return "-"
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value)

    @app.template_filter("currency")
    def currency(value):
        if value is None:
            return "-"
        return f"¥{float(value):.2f}"

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="无权限访问该页面。"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="页面不存在或记录已被删除。"), 404

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=False)
