from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from database import db
from models import Employee

auth_bp = Blueprint("auth", __name__)

DEFAULT_ACCOUNTS = [
    ("管理员", "admin", "admin123"),
    ("主管", "manager", "manager123"),
    ("客服", "service", "service123"),
    ("财务", "finance", "finance123"),
    ("安全", "safety", "safety123"),
    ("运营", "operation", "operation123"),
    ("普通员工", "employee", "employee123"),
]


@auth_bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.current_user = db.session.get(Employee, user_id) if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.current_user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped_view


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if g.current_user is None:
                return redirect(url_for("auth.login"))
            if g.current_user.role not in roles:
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        employee = Employee.query.filter_by(username=username, employee_status="在职").first()

        if employee is None or not check_password_hash(employee.password, password):
            flash("用户名或密码错误。", "danger")
        else:
            session.clear()
            session["user_id"] = employee.employee_id
            flash(f"欢迎回来，{employee.employee_name}。", "success")
            return redirect(url_for("main.index"))

    return render_template("login.html", default_accounts=DEFAULT_ACCOUNTS)


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("您已退出登录。", "info")
    return redirect(url_for("auth.login"))
