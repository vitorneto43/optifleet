import os
import re
import base64
import requests

import cloudinary
import cloudinary.uploader

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    redirect,
    url_for,
    flash,
    abort,
    session,
)

from sqlalchemy import or_
from flask_migrate import Migrate
from werkzeug.utils import secure_filename

from config import Config
from models import db, Dev, Repo, Deploy, Portfolio, PortfolioMedia, User, MediaUpload  # ajuste conforme seus models


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_change_me")

    # =========================
    # CONFIG / DB
    # =========================
    raw_db_url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    if raw_db_url.startswith("postgres://"):
        raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = raw_db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB

    db.init_app(app)
    Migrate(app, db)

    # =========================
    # UPLOAD CONFIG
    # =========================
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "mov", "webm"}
    ALLOWED_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS

    def allowed_file(filename: str) -> bool:
        return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

    # =========================
    # CLOUDINARY CONFIG
    # =========================
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
        api_key=os.environ.get("CLOUDINARY_API_KEY"),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
        secure=True,
    )

    def save_upload_cloudinary(file_storage, folder: str):
        """
        Retorna (url, file_type) onde file_type é 'image' ou 'video'
        """
        if not file_storage or not getattr(file_storage, "filename", ""):
            return (None, None)

        if not allowed_file(file_storage.filename):
            return (None, None)

        filename = secure_filename(file_storage.filename)
        ext = filename.rsplit(".", 1)[1].lower()
        resource_type = "video" if ext in ALLOWED_VIDEO_EXTENSIONS else "image"

        try:
            result = cloudinary.uploader.upload(
                file_storage,
                folder=folder,
                resource_type=resource_type,
                unique_filename=True,
                overwrite=False,
            )
            return (result.get("secure_url"), resource_type)
        except Exception:
            return (None, None)

    def save_upload(file_storage, folder: str, allowed_set=None):
        """
        Retorna (url, file_type). Se allowed_set vier, valida por ele também.
        """
        if not file_storage or not getattr(file_storage, "filename", ""):
            return (None, None)

        filename = secure_filename(file_storage.filename)
        if "." not in filename:
            return (None, None)

        ext = filename.rsplit(".", 1)[1].lower()

        if allowed_set is not None and ext not in allowed_set:
            return (None, None)

        if ext not in ALLOWED_EXTENSIONS:
            return (None, None)

        resource_type = "video" if ext in ALLOWED_VIDEO_EXTENSIONS else "image"

        try:
            result = cloudinary.uploader.upload(
                file_storage,
                folder=folder,
                resource_type=resource_type,
                unique_filename=True,
                overwrite=False,
            )
            return (result.get("secure_url"), resource_type)
        except Exception:
            return (None, None)

    # =========================
    # HELPERS
    # =========================
    def admin_key_ok():
        key = request.headers.get("X-ADMIN-KEY") or request.args.get("key") or request.form.get("key")
        return key == os.environ.get("ADMIN_KEY", "123")

    def to_embed_url(url: str) -> str:
        url = (url or "").strip()
        if not url:
            return ""

        m = re.search(r"(youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_\-]+)", url)
        if m:
            vid = m.group(2)
            return f"https://www.youtube.com/embed/{vid}"

        m = re.search(r"vimeo\.com/(\d+)", url)
        if m:
            vid = m.group(1)
            return f"https://player.vimeo.com/video/{vid}"

        return url

    def parse_github_repo(url: str):
        url = (url or "").strip().replace(".git", "")
        m = re.search(r"github\.com/([^/]+)/([^/]+)", url)
        if not m:
            return None
        return (m.group(1), m.group(2))

    def github_headers(token: str | None):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        return headers

    def fetch_repo_info(repo_url: str, token: str | None = None):
        parsed = parse_github_repo(repo_url)
        if not parsed:
            return (None, "URL inválida. Ex: https://github.com/usuario/repo")

        owner, repo = parsed
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        r = requests.get(api_url, headers=github_headers(token), timeout=12)

        if r.status_code == 403:
            remaining = r.headers.get("X-RateLimit-Remaining")
            reset = r.headers.get("X-RateLimit-Reset")
            try:
                msg = r.json().get("message", "403 no GitHub")
            except Exception:
                msg = "403 no GitHub"

            if remaining == "0":
                return (None, f"GitHub rate limit estourou (403). Configure GITHUB_TOKEN. msg={msg} reset={reset}")
            return (None, f"GitHub respondeu 403: {msg}")

        if r.status_code != 200:
            try:
                msg = r.json().get("message", "")
            except Exception:
                msg = ""
            return (None, f"GitHub respondeu {r.status_code}. {msg}")

        data = r.json()
        return ({
            "repo_name": data.get("name", "") or "",
            "repo_description": data.get("description", "") or "",
            "repo_language": data.get("language", "") or "",
            "repo_stars": int(data.get("stargazers_count", 0) or 0),
            "repo_html_url": data.get("html_url", "") or "",
        }, None)

    def fetch_repo_tree(owner, repo, token=None):
        api = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        r = requests.get(api, headers=github_headers(token), timeout=15)
        if r.status_code != 200:
            return []
        return r.json().get("tree", [])

    def fetch_repo_file(owner, repo, path, token=None):
        api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
        r = requests.get(api, headers=github_headers(token), timeout=15)
        if r.status_code != 200:
            return None
        content = r.json().get("content", "")
        return base64.b64decode(content).decode("utf-8", errors="ignore")

    def current_user():
        uid = session.get("user_id")
        if not uid:
            return None
        return User.query.get(uid)

    def login_required(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return wrapper

    # =========================
    # DB INIT + SEED
    # =========================
    def seed_if_empty():
        # Dev seed
        if Dev.query.count() == 0:
            vitor = Dev(
                username="vitorneto43",
                name="Vitor Veiga",
                bio="Builder of apps, systems and AI. Shipping fast.",
                readme="Deploy Infinity.tech is a dev-first network focused on real proof: live demos + repos + portfolio.",
                github="https://github.com/vitorneto43",
            )
            db.session.add(vitor)
            db.session.commit()

    with app.app_context():
        db.create_all()
        seed_if_empty()

    # =========================
    # HOME
    # =========================
    @app.get("/")
    def home():
        deploys = Deploy.query.order_by(Deploy.created_at.desc()).limit(8).all()
        portfolios = Portfolio.query.order_by(Portfolio.created_at.desc()).limit(8).all()
        return render_template("home.html", deploys=deploys, portfolios=portfolios)

    # =========================
    # AUTH
    # =========================
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            avatar = request.files.get("avatar")

            if not name or not email or not password:
                flash("Preencha nome, e-mail e senha.")
                return redirect(url_for("register"))

            if User.query.filter_by(email=email).first():
                flash("Esse e-mail já está cadastrado.")
                return redirect(url_for("register"))

            avatar_url = None
            if avatar and avatar.filename:
                avatar_url, _ = save_upload(avatar, folder="deployinfinity/avatars", allowed_set=ALLOWED_IMAGE_EXTENSIONS)
                if avatar_url is None:
                    flash("Avatar inválido. Envie PNG/JPG/JPEG/WEBP/GIF.")
                    return redirect(url_for("register"))

            u = User(name=name, email=email, avatar_url=avatar_url)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()

            session["user_id"] = u.id
            flash("Conta criada com sucesso!")
            return redirect(url_for("profile"))

        return render_template("auth_register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            u = User.query.filter_by(email=email).first()
            if not u or not u.check_password(password):
                flash("E-mail ou senha inválidos.")
                return redirect(url_for("login"))

            session["user_id"] = u.id
            flash("Login OK!")
            return redirect(url_for("profile"))

        return render_template("auth_login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/profile")
    @login_required
    def profile():
        u = current_user()
        return render_template("profile.html", user=u)

    @app.route("/profile/avatar", methods=["POST"])
    @login_required
    def update_avatar():
        u = current_user()
        avatar = request.files.get("avatar")

        if not avatar or not avatar.filename:
            flash("Selecione uma imagem.")
            return redirect(url_for("profile"))

        avatar_url, _ = save_upload(avatar, folder="deployinfinity/avatars", allowed_set=ALLOWED_IMAGE_EXTENSIONS)
        if avatar_url is None:
            flash("Avatar inválido. Envie PNG/JPG/JPEG/WEBP/GIF.")
            return redirect(url_for("profile"))

        u.avatar_url = avatar_url
        db.session.commit()

        flash("Avatar atualizado!")
        return redirect(url_for("profile"))

    # =========================
    # API UPLOAD (IMAGEM/VÍDEO)
    # =========================
    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "Nenhum arquivo enviado (campo 'file')."}), 400

        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "Arquivo sem nome."}), 400

        if not allowed_file(f.filename):
            return jsonify({"ok": False, "error": "Extensão não permitida."}), 400

        url, ftype = save_upload_cloudinary(f, folder="deployinfinity/uploads")
        if not url:
            return jsonify({"ok": False, "error": "Falha no upload para Cloudinary."}), 500

        return jsonify({"ok": True, "url": url, "file_type": ftype})

    # =========================
    # UPLOAD PAGE (SALVA NO BANCO)
    # =========================
    @app.route("/upload", methods=["GET", "POST"])
    @login_required
    def upload_media():
        u = current_user()

        if request.method == "POST":
            file = request.files.get("file")

            if not file or not file.filename:
                flash("Selecione um arquivo.")
                return redirect(url_for("upload_media"))

            url, ftype = save_upload(file, folder="deployinfinity/media")
            if url is None:
                flash("Formato inválido. Imagens: png/jpg/jpeg/webp/gif | Vídeos: mp4/webm/mov")
                return redirect(url_for("upload_media"))

            up = MediaUpload(user_id=u.id, file_url=url, file_type=ftype)
            db.session.add(up)
            db.session.commit()

            flash("Upload realizado!")
            return redirect(url_for("upload_media"))

        uploads = MediaUpload.query.filter_by(user_id=u.id).order_by(MediaUpload.created_at.desc()).all()
        return render_template("upload_media.html", user=u, uploads=uploads)

    # =========================
    # PORTFOLIO ADD MEDIA (GALERIA)
    # =========================
    @app.route("/portfolio/<int:portfolio_id>/media", methods=["POST"])
    @login_required
    def portfolio_add_media(portfolio_id):
        u = current_user()
        portfolio = Portfolio.query.get_or_404(portfolio_id)

        if hasattr(portfolio, "user_id") and portfolio.user_id and portfolio.user_id != u.id:
            abort(403)

        file = request.files.get("file")
        if not file or not file.filename:
            flash("Selecione um arquivo.")
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        url, ftype = save_upload(file, folder="deployinfinity/portfolio_media")
        if url is None:
            flash("Formato inválido. Imagens: png/jpg/jpeg/webp/gif | Vídeos: mp4/webm/mov")
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        pm = PortfolioMedia(
            portfolio_id=portfolio.id,
            file_url=url,
            file_type=ftype,
        )
        db.session.add(pm)
        db.session.commit()

        flash("Mídia adicionada ao portfólio!")
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

    # =========================
    # GITHUB IMPORT
    # =========================
    @app.route("/github/import", methods=["GET", "POST"])
    @login_required
    def import_github_repo():
        u = current_user()

        if request.method == "POST":
            repo_url = request.form.get("repo_url", "").strip()
            gh_token = os.environ.get("GITHUB_TOKEN", "").strip() or None

            info, err = fetch_repo_info(repo_url, gh_token)
            if err:
                flash(err)
                return redirect(url_for("import_github_repo"))

            # ⚠️ Atenção: seu Repo usa dev_id (Dev), mas você loga como User.
            # Solução rápida: usar o primeiro Dev como dono (ou crie um campo user_id no Repo).
            dev = Dev.query.first()
            if not dev:
                flash("Nenhum Dev cadastrado.")
                return redirect(url_for("import_github_repo"))

            repo = Repo(
                dev_id=dev.id,
                title=info["repo_name"],
                url=repo_url,
                description=info["repo_description"],
                stack=info["repo_language"],
                image_url="",
                is_public=True,
                reported=False,
            )

            db.session.add(repo)
            db.session.commit()

            flash("Repositório importado com sucesso!")
            return redirect(url_for("repo_detail", repo_id=repo.id))

        return render_template("github_import.html")

    # =========================
    # LISTAGENS
    # =========================
    @app.get("/repos")
    @login_required
    def repos():
        q = request.args.get("q", "").strip()
        query = Repo.query.filter_by(is_public=True, reported=False)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Repo.title.ilike(like))
                | (Repo.description.ilike(like))
                | (Repo.tags.ilike(like))
                | (Repo.stack.ilike(like))
            )
        repos_list = query.order_by(Repo.created_at.desc()).all()
        return render_template("repos.html", repos=repos_list, q=q)

    @app.route("/repo/<int:repo_id>")
    @login_required
    def repo_detail(repo_id):
        repo = Repo.query.get_or_404(repo_id)

        parsed = parse_github_repo(repo.url)
        if not parsed:
            flash("URL do GitHub inválida nesse Repo cadastrado.")
            return redirect(url_for("repos"))

        owner, name = parsed
        tree = fetch_repo_tree(owner, name, os.environ.get("GITHUB_TOKEN"))

        return render_template("repo_detail.html", repo=repo, tree=tree)

    @app.route("/repo/<int:repo_id>/file")
    @login_required
    def repo_file(repo_id):
        path = request.args.get("path")
        repo = Repo.query.get_or_404(repo_id)

        parsed = parse_github_repo(repo.url)
        if not parsed:
            flash("URL do GitHub inválida nesse Repo cadastrado.")
            return redirect(url_for("repos"))

        if not path:
            flash("Informe o path do arquivo.")
            return redirect(url_for("repo_detail", repo_id=repo_id))

        owner, name = parsed
        content = fetch_repo_file(owner, name, path, os.environ.get("GITHUB_TOKEN"))

        return render_template("repo_file.html", repo=repo, path=path, content=content)

    @app.get("/deploys")
    @login_required
    def deploys():
        q = request.args.get("q", "").strip()
        query = Deploy.query.filter_by(is_public=True, reported=False)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Deploy.title.ilike(like))
                | (Deploy.stack.ilike(like))
                | (Deploy.status.ilike(like))
            )
        deploys_list = query.order_by(Deploy.created_at.desc()).all()
        return render_template("deploys.html", deploys=deploys_list, q=q)

    @app.get("/portfolio")
    @login_required
    def portfolio():
        q = request.args.get("q", "").strip()
        query = Portfolio.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Portfolio.title.ilike(like),
                    Portfolio.description.ilike(like),
                    Portfolio.category.ilike(like),
                )
            )
        items = query.order_by(Portfolio.created_at.desc()).all()
        return render_template("portfolio.html", items=items, q=q)

    @app.get("/portfolio/<int:portfolio_id>")
    @login_required
    def portfolio_detail(portfolio_id):
        portfolio = Portfolio.query.get_or_404(portfolio_id)
        medias = sorted(getattr(portfolio, "medias", []), key=lambda m: getattr(m, "sort_order", 0))
        return render_template("portfolio_detail.html", portfolio=portfolio, medias=medias)

    @app.route("/deploy/<int:deploy_id>")
    @login_required
    def deploy_detail(deploy_id):
        deploy = Deploy.query.get_or_404(deploy_id)
        return render_template("deploy_detail.html", deploy=deploy)

    # =========================
    # PERFIL DO DEV
    # =========================
    @app.route("/devs")
    @login_required
    def devs():
        devs_list = Dev.query.order_by(Dev.created_at.desc()).all()
        return render_template("devs.html", devs=devs_list)

    @app.get("/dev/<username>")
    @login_required
    def dev_profile(username):
        dev = Dev.query.filter_by(username=username).first_or_404()
        repos_ = Repo.query.filter_by(dev_id=dev.id, is_public=True, reported=False).order_by(Repo.created_at.desc()).all()
        deploys_ = Deploy.query.filter_by(dev_id=dev.id, is_public=True, reported=False).order_by(Deploy.created_at.desc()).all()
        items_ = Portfolio.query.filter_by(dev_id=dev.id, is_public=True, reported=False).order_by(Portfolio.created_at.desc()).all()
        return render_template("dev_profile.html", dev=dev, repos=repos_, deploys=deploys_, items=items_)

    # =========================
    # FORMS (NEW)
    # =========================
    @app.get("/new/repo")
    @login_required
    def new_repo_form():
        devs_list = Dev.query.order_by(Dev.created_at.desc()).all()
        return render_template("form_repo.html", devs=devs_list)

    @app.post("/new/repo")
    @login_required
    def new_repo():
        if not admin_key_ok():
            abort(403)

        repo = Repo(
            dev_id=int(request.form["dev_id"]),
            title=request.form["title"].strip(),
            url=request.form["url"].strip(),
            description=request.form.get("description", "").strip(),
            stack=request.form.get("stack", "").strip(),
            tags=request.form.get("tags", "").strip(),
            image_url=request.form.get("image_url", "").strip(),
            is_public=True,
            reported=False,
        )
        db.session.add(repo)
        db.session.commit()
        flash("Repo cadastrado!", "ok")
        return redirect(url_for("repos"))

    @app.get("/new/deploy")
    @login_required
    def new_deploy_form():
        devs_list = Dev.query.order_by(Dev.created_at.desc()).all()
        return render_template("form_deploy.html", devs=devs_list)

    @app.post("/new/deploy")
    @login_required
    def new_deploy():
        repo_url = request.form.get("repo_url", "").strip()
        gh_token = os.environ.get("GITHUB_TOKEN")
        info = None
        if repo_url:
            info, _err = fetch_repo_info(repo_url, gh_token)

        deploy = Deploy(
            dev_id=int(request.form["dev_id"]),
            title=request.form["title"].strip(),
            deploy_url=request.form["deploy_url"].strip(),
            repo_url=repo_url,
            status=request.form.get("status", "online").strip(),
            stack=request.form.get("stack", "").strip(),
            image_url=request.form.get("image_url", "").strip(),
            repo_name=(info["repo_name"] if info else ""),
            repo_description=(info["repo_description"] if info else ""),
            repo_language=(info["repo_language"] if info else ""),
            repo_stars=(info["repo_stars"] if info else 0),
            repo_html_url=(info["repo_html_url"] if info else ""),
        )
        db.session.add(deploy)
        db.session.commit()
        flash("Deploy cadastrado!", "ok")
        return redirect(url_for("deploys"))

    @app.get("/new/portfolio")
    @login_required
    def new_portfolio_form():
        devs_list = Dev.query.order_by(Dev.created_at.desc()).all()
        return render_template("form_portfolio.html", devs=devs_list)

    @app.post("/new/portfolio")
    @login_required
    def new_portfolio():
        dev_id = int(request.form["dev_id"])
        title = request.form["title"].strip()

        portfolio_ = Portfolio(
            dev_id=dev_id,
            title=title,
            category=request.form.get("category", "").strip(),
            description=request.form.get("description", "").strip(),
            repo_url=request.form.get("repo_url", "").strip(),
            demo_url=request.form.get("demo_url", "").strip(),
            cover_image_url=request.form.get("cover_image_url", "").strip(),
            is_public=True,
            reported=False,
        )

        db.session.add(portfolio_)
        db.session.commit()

        flash("Portfólio cadastrado!", "ok")
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio_.id))

    # =========================
    # API GITHUB (diagnóstico)
    # =========================
    @app.get("/api/github/repo")
    @login_required
    def api_github_repo():
        repo_url = request.args.get("url", "").strip()
        parsed = parse_github_repo(repo_url)
        if not parsed:
            return jsonify({"error": "URL inválida do GitHub. Ex: https://github.com/usuario/repo"}), 400

        owner, repo = parsed
        api_url = f"https://api.github.com/repos/{owner}/{repo}"

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = github_headers(token if token else None)

        r = requests.get(api_url, headers=headers, timeout=12)
        if r.status_code == 403:
            remaining = r.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                return jsonify({"error": "Rate limit do GitHub estourou no servidor. Configure GITHUB_TOKEN."}), 403
            return jsonify({"error": "403 Forbidden. Token faltando/inválido ou sem permissão."}), 403

        if r.status_code != 200:
            return jsonify({"error": f"GitHub respondeu {r.status_code}."}), 400

        j = r.json()
        return jsonify({
            "name": j.get("name"),
            "description": j.get("description") or "",
            "stars": j.get("stargazers_count", 0),
            "language": j.get("language") or "",
            "html_url": j.get("html_url") or repo_url,
        })

    # =========================
    # REPORTAR
    # =========================
    @app.post("/report/<kind>/<int:item_id>")
    def report(kind, item_id):
        model = {"repo": Repo, "deploy": Deploy, "portfolio": Portfolio}.get(kind)
        if not model:
            abort(404)
        item = model.query.get_or_404(item_id)
        item.reported = True
        db.session.commit()
        flash("Report recebido. Obrigado.", "ok")
        return redirect(request.referrer or url_for("home"))

    # =========================
    # GOVERNANÇA
    # =========================
    @app.get("/rules")
    def rules():
        return render_template("rules.html")

    @app.get("/terms")
    def terms():
        return render_template("terms.html")

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html")

    # ✅ AGORA SIM: return app no FINAL
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
