# routes/auth_routes.py
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash

from core.db import get_user_by_email, insert_user
from urllib.parse import urlparse


bp_auth = Blueprint("auth", __name__)

# -------- util --------
def _safe_next(u: str) -> bool:
    if not u:
        return False
    p = urlparse(u)
    # só permite URLs internas (sem domínio) ou caminhos do próprio site
    return not p.netloc and u.startswith("/")


class UserObj:
    # User leve sem ORM (compatível com flask_login)
    def __init__(self, id, email):
        self.id = str(id)
        self.email = email
    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return self.id

# -------- páginas --------
@bp_auth.get("/login")
def login_page():
    # passa o next para o template (se veio do flask_login, já estará em ?next=...)
    nxt = request.args.get("next", "")
    return render_template("login.html", next=nxt)

@bp_auth.get("/register")
def register_page():
    # mantém o next para o formulário de registro também
    nxt = request.args.get("next", "")
    return render_template("register.html", next=nxt)

# -------- ações --------
@bp_auth.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    nxt = request.form.get("next") or request.args.get("next")

    row = get_user_by_email(email)  # -> {"id","email","password"} ou None
    if not row or not check_password_hash(row["password"], password):
        flash("Credenciais inválidas", "error")
        # preserve next na volta
        if nxt:
            return redirect(url_for("auth.login_page", next=nxt))
        return redirect(url_for("auth.login_page"))

    login_user(UserObj(row["id"], row["email"]))

    # Redireciona de volta para o fluxo original (checkout, etc.)
    if _safe_next(nxt):
        return redirect(nxt)
    return redirect(url_for("home"))

@bp_auth.post("/register")
def register_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    nxt = request.form.get("next") or request.args.get("next")

    if not email or not password:
        flash("Preencha e-mail e senha.", "error")
        if nxt:
            return redirect(url_for("auth.register_page", next=nxt))
        return redirect(url_for("auth.register_page"))

    if get_user_by_email(email):
        flash("E-mail já cadastrado.", "error")
        if nxt:
            return redirect(url_for("auth.register_page", next=nxt))
        return redirect(url_for("auth.register_page"))

    pw_hash = generate_password_hash(password)
    insert_user(email, pw_hash)

    # login automático
    row = get_user_by_email(email)
    login_user(UserObj(row["id"], row["email"]))

    if _safe_next(nxt):
        return redirect(nxt)
    return redirect(url_for("home"))

@bp_auth.post("/logout")
@login_required
def logout_post():
    logout_user()
    return redirect(url_for("auth.login_page"))



