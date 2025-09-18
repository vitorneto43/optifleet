# routes/auth_routes.py
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from core.db import SessionLocal
from core.auth_models import User
from datetime import datetime, timedelta

bp_auth = Blueprint("auth", __name__)

@bp_auth.get("/login")
def login_page():
    return render_template("login.html")

@bp_auth.post("/login")
def login_post():
    s = SessionLocal()
    try:
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        u = s.query(User).filter(User.email==email).first()
        if not u or not u.check_password(password):
            flash("Credenciais inválidas", "error")
            return redirect(url_for("auth.login_page"))
        login_user(u)
        return redirect(url_for("home"))
    finally:
        s.close()

@bp_auth.get("/register")
def register_page():
    return render_template("register.html")

@bp_auth.post("/register")
def register_post():
    s = SessionLocal()
    try:
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if s.query(User).filter(User.email==email).first():
            flash("E-mail já cadastrado", "error")
            return redirect(url_for("auth.register_page"))
        u = User(email=email, tenant_id=None)
        u.set_password(password)
        s.add(u); s.commit()
        login_user(u)
        return redirect(url_for("home"))
    finally:
        s.close()

@bp_auth.post("/logout")
@login_required
def logout_post():
    logout_user()
    return redirect(url_for("auth.login_page"))
