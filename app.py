# app.py
from pathlib import Path
import os
import time
from datetime import date, datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2
import json
import csv
from io import StringIO, BytesIO
import traceback  # para logs de erro

from dotenv import load_dotenv
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, flash, send_file
)
from flask_login import (
    LoginManager, login_required, current_user
)

# ===== Core / DB / Visual =====
from core.visual.map_render import build_map, _coerce_path_to_coords
from core.db import (
    get_user_by_id, obter_posicoes, get_active_subscription, get_active_trial,
    create_trial, expire_trial, trial_users_upsert, trial_users_summary,
    list_trial_users, trial_users_backfill_from_trials, get_conn
)
from core.db_connection import close_db

# ===== Rotas/Blueprints =====
from routes.auth_routes import bp_auth
from routes.fleet_routes import bp_fleet
from routes.telemetry_routes import bp_tele
from routes.reroute_routes import bp_reroute
from routes.notify_routes import bp_notify
from routes.vendor_ingest_routes import bp_vendor
from routes.billing_routes import bp_billing
from billing.asaas_routes import bp_asaas
from billing.asaas_webhook import bp_asaas_webhook
from routes.trial_routes import bp_trial
from routes.contact_routes import bp_contact
from routes.checkout_routes import bp_checkout
from routes.demo_routes import bp_demo
from routes.account_routes import bp_account
from routes.admin_routes import admin_bp

# ===== Solver / Providers / Maintenance =====
from core.models import Location, TimeWindow, Depot, Vehicle, Stop, OptimizeRequest, hhmm_to_minutes
from core.providers.maps import RoutingProvider
from core.solver.vrptw import solve_vrptw
from core.maintenance.predictor import predict_failure_risk
from core.telemetry import salvar_telemetria

# ----------------------------------------------------------------------------- #
# App / Config
# ----------------------------------------------------------------------------- #
load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)
app.register_blueprint(admin_bp, url_prefix="/admin")

AUTO_TRIAL = os.getenv("AUTO_TRIAL", "1") == "1"
DEFAULT_TRIAL_PLAN = os.getenv("DEFAULT_TRIAL_PLAN", "full")
DEFAULT_TRIAL_VEHICLES = int(os.getenv("DEFAULT_TRIAL_VEHICLES", "10"))
# 15 dias reais
DEFAULT_TRIAL_DAYS = int(os.getenv("DEFAULT_TRIAL_DAYS", "15"))

_last_audit_time = {}

# ----------------------------------------------------------------------------- #
# SQL helpers (COMPATÍVEIS COM DUCKDB)
# ----------------------------------------------------------------------------- #
def _dialect():
    """Identifica DuckDB explicitamente"""
    return "duckdb"  # Já sabemos que é DuckDB

def _sql_now():
    return "NOW()"

def _sql_date(expr):
    return f"CAST({expr} AS DATE)"

def _sql_hours_ago(hours:int):
    return f"NOW() - INTERVAL '{hours} hours'"

def _sql_minutes_ago(minutes:int):
    return f"NOW() - INTERVAL '{minutes} minutes'"

def _sql_this_week_start():
    return "CURRENT_DATE - INTERVAL '6 days'"  # Simplificado para DuckDB

def _has_column(conn, table, column):
    """Verifica se coluna existe (compatível com DuckDB) - CORRIGIDO"""
    try:
        result = conn.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = ? AND column_name = ?
        """, [table.lower(), column.lower()]).fetchone()
        return result is not None
    except Exception:
        return False

# ----------------------------------------------------------------------------- #
# Bootstrap admin em DuckDB (CORRIGIDO)
# ----------------------------------------------------------------------------- #
def _bootstrap_duckdb_admin():
    from core.db import get_conn
    admin_email = os.getenv("ADMIN_EMAIL")
    if not admin_email:
        return
    try:
        con = get_conn()
        # Verifica se coluna is_admin existe
        has_admin_col = _has_column(con, "users", "is_admin")
        if not has_admin_col:
            con.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
        con.execute("UPDATE users SET is_admin = TRUE WHERE email = ?", (admin_email,))
        con.close()
    except Exception as e:
        print(f"[admin bootstrap] erro: {e}")

_bootstrap_duckdb_admin()

# ----------------------------------------------------------------------------- #
# Utils / Helpers
# ----------------------------------------------------------------------------- #
def _parse_dt_any(val):
    """Converte str/naive/aware -> aware UTC. Retorna None se não der."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc) if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(val, str):
        s = val.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
    return None

def _status_of(record, idx_if_tuple=None, key_if_dict="status"):
    """Extrai status robustamente de tupla ou dict."""
    if not record:
        return ""
    try:
        if hasattr(record, "get"):
            val = record.get(key_if_dict)
            if val is not None:
                return str(val).lower()
    except Exception:
        pass
    if idx_if_tuple is not None:
        try:
            return str(record[idx_if_tuple]).lower()
        except Exception:
            pass
    return ""

def _trial_status_of(trial):
    return _status_of(trial, idx_if_tuple=5, key_if_dict="status")  # DuckDB: índice 5 para status

def _trial_end_of(trial):
    """Extrai trial_end de dict ou tupla e padroniza para datetime aware UTC."""
    if not trial:
        return None
    if hasattr(trial, "get"):
        end = trial.get("trial_end") or trial.get("end") or trial.get("trialEnd")
        return _parse_dt_any(end)
    for idx in (4, 3):  # DuckDB: trial_end no índice 4
        try:
            return _parse_dt_any(trial[idx])
        except Exception:
            continue
    return None

def _sub_status_of(sub):
    return _status_of(sub, idx_if_tuple=4, key_if_dict="status")  # DuckDB: índice 4 para status

def _days_left(trial):
    """Dias restantes inteiros (ceil)."""
    end = _trial_end_of(trial)
    if not end:
        return None
    now = datetime.now(timezone.utc)
    sec = (end - now).total_seconds()
    if sec <= 0:
        return 0
    from math import ceil
    return int(ceil(sec / 86400))

def _ensure_trial(user_id: int):
    if not AUTO_TRIAL or not user_id:
        return
    trial = get_active_trial(user_id)
    if trial:
        return
    try:
        create_trial(
            user_id=user_id,
            plan=DEFAULT_TRIAL_PLAN,
            vehicles=DEFAULT_TRIAL_VEHICLES,
            days=DEFAULT_TRIAL_DAYS,
        )
        print(f"[trial] criado para user {user_id}")
    except Exception as e:
        print("[trial] falhou ao criar:", e)

def _parse_maybe_dt(x):
    """Converte str/None/datetime em datetime timezone-aware (UTC) ou None."""
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, str):
        s = x.strip().replace("Z", "")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s.split(".")[0], fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        try:
            dt = datetime.fromisoformat(s.split(".")[0])
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None

def _iso_utc(dt):
    """Retorna ISO8601 em UTC com 'Z' no final, ou ''."""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = _parse_maybe_dt(dt)
    if not dt:
        return ""
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

# ----------------------------------------------------------------------------- #
# Login / Session
# ----------------------------------------------------------------------------- #
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"
login_manager.init_app(app)

@app.teardown_appcontext
def teardown_db(exception):
    close_db()

@login_manager.user_loader
def load_user(user_id: str):
    row = get_user_by_id(int(user_id))
    if not row:
        return None
    class UserObj:
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
    return UserObj(row["id"], row["email"])

@app.context_processor
def inject_globals():
    uid = int(getattr(current_user, "id", 0) or 0)
    sub = get_active_subscription(uid) if uid else None
    trial = get_active_trial(uid) if uid else None
    return {
        "user_email": getattr(current_user, "email", None),
        "sub": sub,
        "trial": trial,
        "days_left": _days_left(trial),
    }

# ----------------------------------------------------------------------------- #
# before_request: auditoria / expiração de trial
# ----------------------------------------------------------------------------- #
@app.before_request
def _sync_trial_audit_and_expire():
    """
    Versão otimizada que evita auditorias muito frequentes para o mesmo usuário.
    """
    try:
        uid = int(getattr(current_user, "id", 0) or 0)
        if not uid:
            return

        # Rate limiting: máximo 1 auditoria por minuto por usuário
        current_time = time.time()
        last_time = _last_audit_time.get(uid, 0)
        if current_time - last_time < 60:  # 60 segundos
            return

        _last_audit_time[uid] = current_time

        trial = get_active_trial(uid)
        if not trial:
            return

        # Verifica expiração
        end = _trial_end_of(trial)
        if end is not None:
            now = datetime.now(timezone.utc)
            if end < now:
                try:
                    tid = trial.get("id") if hasattr(trial, "get") else trial[0]
                    if tid:
                        expire_trial(tid)
                        print(f"[trial] expirado para user {uid}")
                except Exception as e:
                    print(f"[expire_trial] erro: {e}")
                return

        # Auditoria - apenas dados essenciais
        trial_start = None
        if hasattr(trial, "get"):
            trial_start = _parse_dt_any(trial.get("started_at") or trial.get("start"))
        else:
            for idx in (3, 2):
                try:
                    trial_start = _parse_dt_any(trial[idx])
                    break
                except Exception:
                    continue

        # Converte para string de forma segura
        def _safe_isoformat(dt):
            if not dt:
                return None
            if isinstance(dt, str):
                return dt
            try:
                return dt.isoformat()
            except Exception:
                return None

        trial_start_str = _safe_isoformat(trial_start)
        trial_end_str = _safe_isoformat(end)
        is_converted = (_trial_status_of(trial) == "converted")

        # UPSERT com tratamento silencioso de erro
        try:
            trial_users_upsert(
                user_id=uid,
                email=(getattr(current_user, "email", "") or ""),
                nome=None,
                trial_start=trial_start_str,
                trial_end=trial_end_str,
                converted=is_converted,
            )
        except Exception:
            # Log silencioso - não polui os logs
            pass

    except Exception:
        # Log silencioso para erros gerais
        pass

# ----------------------------------------------------------------------------- #
# Infra inicial (tabelas auxiliares)
# ----------------------------------------------------------------------------- #
with get_conn() as con:
    con.execute("""
        CREATE TABLE IF NOT EXISTS last_plans (
            user_id     INTEGER,
            created_at  TIMESTAMP,
            req_json    TEXT,
            routes_json TEXT,
            map_url     TEXT,
            path_json   TEXT
        );
    """)

# ----------------------------------------------------------------------------- #
# Assets / Providers
# ----------------------------------------------------------------------------- #
(Path(app.static_folder) / "maps").mkdir(parents=True, exist_ok=True)
rp = RoutingProvider()

try:
    salvar_telemetria("empresa_123", "V1", -8.05, -34.9, 60.5, 80.0)
except Exception:
    pass

# ----------------------------------------------------------------------------- #
# Rotas simples
# ----------------------------------------------------------------------------- #
@app.get("/", endpoint="home")
def home_public():
    if getattr(current_user, "is_authenticated", False):
        uid = int(current_user.id)
        sub = get_active_subscription(uid)
        trial = get_active_trial(uid)
        ok_sub = (_sub_status_of(sub) == "active")
        ok_trial = (_trial_status_of(trial) == "active")
        if ok_sub or ok_trial:
            return redirect(url_for("dashboard"))
        return render_template("landing.html", paywall_hint=True, days_left=_days_left(trial))
    return render_template("landing.html")

@app.get("/site")
def landing():
    return render_template("landing.html")

@app.get("/app")
@login_required
def dashboard():
    uid = int(current_user.id)
    _ensure_trial(uid)

    sub = get_active_subscription(uid)
    trial = get_active_trial(uid)
    ok_sub = (_sub_status_of(sub) == "active")
    ok_trial = (_trial_status_of(trial) == "active")

    if ok_sub or ok_trial:
        # TODO: pegar KPIs reais do banco
        kpis = {"total_veiculos": 12, "viagens_hoje": 8, "viagens_semana": 43, "alertas": 3}
        return render_template("index.html", hide_paywall=True, kpis=kpis)

    return redirect(url_for("billing.pricing_page"))

@app.get("/pricing")
def pricing_alias():
    return redirect(url_for("billing.pricing_page"))

@app.get("/vehicles")
@login_required
def vehicles_page():
    return render_template("vehicles.html")

@app.get("/tracking")
@login_required
def tracking():
    return render_template("telemetry.html")

@app.get("/link_tracker")
@login_required
def link_tracker_page():
    return render_template("link_tracker.html")

@app.get("/contact")
def contact_page():
    return render_template("contact.html", today=date.today().strftime("%d/%m/%Y"))

@app.post("/contact")
def contact_submit():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    company = (request.form.get("company") or "").strip()
    message = (request.form.get("message") or "").strip()
    print("[CONTACT]", {
        "name": name,
        "email": email,
        "company": company,
        "message": message,
        "ip": request.remote_addr
    })
    flash("Recebemos sua mensagem. Em breve entraremos em contato.", "success")
    return redirect(url_for("contact_thanks"))

@app.get("/contact/thanks")
def contact_thanks():
    return render_template("contact_thanks.html", today=date.today().strftime("%d/%m/%Y"))

@app.get("/terms")
def terms_page():
    return render_template("terms.html", today=date.today().strftime("%d/%m/%Y"))

@app.get("/privacy")
def privacy_page():
    return render_template("privacy.html", today=date.today().strftime("%d/%m/%Y"))

@app.get("/_routes")
def _routes():
    lines = []
    for r in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
        methods = ",".join(sorted(m for m in r.methods if m in {"GET","POST","PUT","PATCH","DELETE"}))
        lines.append(f"{r.rule}  ->  {r.endpoint}  [{methods}]")
    return "<pre>" + "\n".join(lines) + "</pre>"

@app.get("/demo")
def demo_page():
    return render_template("demo.html")

@app.get("/admin/trials")
@login_required
def admin_trials():
    try:
        uid = int(current_user.id)
        me = get_user_by_id(uid)
        is_admin = False
        if me:
            # DuckDB retorna boolean para is_admin
            is_admin = bool(me.get("is_admin")) if hasattr(me, "get") else bool(me["is_admin"])
        if not is_admin:
            return "Forbidden", 403
    except Exception:
        return "Forbidden", 403

    status = (request.args.get("status") or "").strip().lower() or None
    rows = list_trial_users(status=status, limit=500, offset=0)
    summary = trial_users_summary() or {}

    html = []
    html.append("<h2>Trials (auditoria)</h2>")
    if isinstance(summary, dict):
        resumo = ", ".join([f"{k}: {v}" for k, v in summary.items()])
    else:
        resumo = str(summary)
    html.append(f"<p>Resumo: {resumo}</p>")
    html.append("""
        <p>Filtrar:
          <a href="/admin/trials">todos</a> |
          <a href="/admin/trials?status=ativo">ativos</a> |
          <a href="/admin/trials?status=expirado">expirados</a> |
          <a href="/admin/trials?status=convertido">convertidos</a>
        </p>
    """)
    html.append('<table border="1" cellspacing="0" cellpadding="6">')
    html.append("<tr><th>User ID</th><th>Email</th><th>Nome</th><th>Início</th><th>Fim</th><th>Status</th><th>Atualizado</th></tr>")

    for r in rows or []:
        if hasattr(r, "get"):
            user_id = r.get("user_id")
            email = r.get("email")
            nome = r.get("nome") or ""
            ts = r.get("trial_start")
            te = r.get("trial_end")
            st = r.get("status")
            upd = r.get("updated_at") or r.get("updated")
        else:
            user_id, email, nome, ts, te, st, upd = (list(r) + [None]*7)[:7]

        html.append(
            f"<tr><td>{user_id}</td><td>{email}</td><td>{nome or ''}</td>"
            f"<td>{ts}</td><td>{te}</td><td>{st}</td><td>{upd}</td></tr>"
        )

    html.append("</table>")
    return "".join(html)

@app.get("/admin/trials/backfill")
@login_required
def admin_trials_backfill():
    try:
        if int(current_user.id) != 1:
            return "Forbidden", 403
    except Exception:
        return "Forbidden", 403

    trial_users_backfill_from_trials()
    return redirect(url_for("admin_trials"))

# ----------------------------------------------------------------------------- #
# KPIs (compatível com DuckDB)
# ----------------------------------------------------------------------------- #
@app.get("/api/kpis")
@login_required
def api_kpis():
    out = {"total_veiculos": 0, "viagens_hoje": 0, "viagens_semana": 0, "alertas": 0}
    try:
        conn = get_conn()

        # total_veiculos nos últimos 5 min
        try:
            out["total_veiculos"] = conn.execute(f"""
                SELECT COUNT(DISTINCT vehicle_id)
                FROM telemetry
                WHERE timestamp >= {_sql_minutes_ago(5)}
            """).fetchone()[0] or 0
        except Exception:
            pass

        desloc_thresh = 0.01

        # viagens_hoje (>= 20 pontos e deslocamento mínimo)
        try:
            out["viagens_hoje"] = conn.execute(f"""
                SELECT COUNT(*) FROM (
                  SELECT vehicle_id
                  FROM telemetry
                  WHERE {_sql_date("timestamp")} = {_sql_date("CURRENT_TIMESTAMP")}
                  GROUP BY vehicle_id
                  HAVING COUNT(*) >= 20
                     AND ( (MAX(lat)-MIN(lat)) + (MAX(lon)-MIN(lon)) ) > {desloc_thresh}
                ) t
            """).fetchone()[0] or 0
        except Exception:
            pass

        # viagens_semana ~ últimos 7 dias
        try:
            out["viagens_semana"] = conn.execute(f"""
                SELECT COUNT(*) FROM (
                  SELECT vehicle_id
                  FROM telemetry
                  WHERE {_sql_date("timestamp")} >= {_sql_this_week_start()}
                  GROUP BY vehicle_id
                  HAVING COUNT(*) >= 60
                     AND ( (MAX(lat)-MIN(lat)) + (MAX(lon)-MIN(lon)) ) > {desloc_thresh}
                ) t
            """).fetchone()[0] or 0
        except Exception:
            pass

        # alertas: overspeed na última hora
        try:
            if _has_column(conn, "telemetry", "speed"):
                out["alertas"] = conn.execute(f"""
                    SELECT COUNT(*)
                    FROM telemetry
                    WHERE timestamp >= {_sql_minutes_ago(60)}
                      AND COALESCE(speed, 0) > 100
                """).fetchone()[0] or 0
        except Exception:
            pass

    except Exception:
        pass
    return jsonify(out)

# ----------------------------------------------------------------------------- #
# Telemetria (APIs) — consultas compatíveis com DuckDB
# ----------------------------------------------------------------------------- #
@app.get("/api/telemetry")
@login_required
def api_telemetry():
    client_id = int(current_user.id)
    rows = obter_posicoes(client_id)
    return jsonify(rows)

def _haversine_km(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0
    dlat = radians(b_lat - a_lat)
    dlon = radians(b_lon - a_lon)
    la1 = radians(a_lat); lo1 = radians(a_lon); la2 = radians(b_lat); lo2 = radians(b_lon)
    h = sin(dlat/2)**2 + cos(la1)*cos(la2)*sin(dlon/2)**2
    return 2*R*atan2(sqrt(h), sqrt(1-h))

def _bearing_deg(a_lat, a_lon, b_lat, b_lon):
    la1, la2, dlon = radians(a_lat), radians(b_lat), radians(b_lon - a_lon)
    x = sin(dlon)*cos(la2)
    y = cos(la1)*sin(la2) - sin(la1)*cos(la2)*cos(dlon)
    br = (atan2(x, y) * 180.0 / 3.141592653589793)
    return (br + 360) % 360

def _turn_delta(a, b):
    d = abs(a - b)
    return d if d <= 180 else 360 - d

def _fetch_points(hours:int, vehicle_id:str|None=None, limit:int=20000):
    conn = get_conn()
    if vehicle_id:
        rows = conn.execute(f"""
            SELECT vehicle_id, lat, lon, timestamp AS ts, COALESCE(speed,0) AS speed
            FROM telemetry
            WHERE timestamp >= {_sql_hours_ago(hours)}
              AND vehicle_id = ?
            ORDER BY ts
            LIMIT ?
        """, [vehicle_id, limit]).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT vehicle_id, lat, lon, timestamp AS ts, COALESCE(speed,0) AS speed
            FROM telemetry
            WHERE timestamp >= {_sql_hours_ago(hours)}
            ORDER BY vehicle_id, ts
            LIMIT ?
        """, [limit]).fetchall()

    pts = []
    for r in rows:
        try:
            pts.append({
                "vehicle_id": str(r[0]),
                "lat": float(r[1]),
                "lon": float(r[2]),
                "ts": str(r[3]),
                "speed": float(r[4]),
                "bearing": None,
            })
        except Exception:
            continue
    return pts

def _detect_events(points, overspeed_kmh=100, stop_speed_kmh=3, stop_min_minutes=5, harsh_turn_deg=60):
    events = []
    if not points:
        return events

    from itertools import groupby
    for vid, group in groupby(points, key=lambda x: x["vehicle_id"]):
        g = list(group)

        # overspeed
        for p in g:
            if p["speed"] > overspeed_kmh:
                events.append({
                    "type": "overspeed", "vehicle_id": vid,
                    "lat": p["lat"], "lon": p["lon"], "ts": p["ts"],
                    "value": p["speed"]
                })

        # paradas
        start_idx = None
        for i, p in enumerate(g):
            if p["speed"] < stop_speed_kmh:
                if start_idx is None:
                    start_idx = i
            else:
                if start_idx is not None:
                    st = g[start_idx]["ts"]; en = g[i-1]["ts"]
                    try:
                        t0 = datetime.fromisoformat(str(st).replace("Z","").split(".")[0])
                        t1 = datetime.fromisoformat(str(en).replace("Z","").split(".")[0])
                        mins = (t1 - t0).total_seconds()/60.0
                    except Exception:
                        mins = 0
                    if mins >= stop_min_minutes:
                        mid = g[(start_idx + i-1)//2]
                        events.append({
                            "type": "stop", "vehicle_id": vid,
                            "lat": mid["lat"], "lon": mid["lon"], "ts": str(en),
                            "minutes": round(mins, 1)
                        })
                    start_idx = None
        if start_idx is not None and len(g) - start_idx >= 2:
            st = g[start_idx]["ts"]; en = g[-1]["ts"]
            try:
                t0 = datetime.fromisoformat(str(st).replace("Z","").split(".")[0])
                t1 = datetime.fromisoformat(str(en).replace("Z","").split(".")[0])
                mins = (t1 - t0).total_seconds()/60.0
            except Exception:
                mins = 0
            if mins >= stop_min_minutes:
                mid = g[(start_idx + len(g)-1)//2]
                events.append({
                    "type": "stop", "vehicle_id": vid,
                    "lat": mid["lat"], "lon": mid["lon"], "ts": str(en),
                    "minutes": round(mins, 1)
                })

        # curvas bruscas
        for i in range(1, len(g)):
            a, b = g[i-1], g[i]
            try:
                br1 = _bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"])
            except Exception:
                continue
            if i >= 2:
                c = g[i-2]
                try:
                    br0 = _bearing_deg(c["lat"], c["lon"], a["lat"], a["lon"])
                    delta = _turn_delta(br0, br1)
                    if delta >= harsh_turn_deg:
                        events.append({
                            "type": "harsh_turn", "vehicle_id": vid,
                            "lat": a["lat"], "lon": a["lon"], "ts": a["ts"],
                            "delta_deg": round(delta, 0)
                        })
                except Exception:
                    pass
    return events

@app.get("/api/telemetry/history")
@login_required
def api_telemetry_history():
    hours = int(request.args.get("hours", 6))
    vid = request.args.get("vehicle_id")
    pts = _fetch_points(hours, vid)
    return jsonify(pts)

@app.get("/api/telemetry/events")
@login_required
def api_telemetry_events():
    hours = int(request.args.get("hours", 6))
    vid = request.args.get("vehicle_id")
    overspeed = float(request.args.get("overspeed", 100))
    stop_speed = float(request.args.get("stop_speed", 3))
    stop_minutes = float(request.args.get("stop_minutes", 5))
    harsh_turn = float(request.args.get("harsh_turn", 60))
    buffer_km = float(request.args.get("offroute_km", 0.3))

    pts = _fetch_points(hours, vid)
    ev = _detect_events(
        pts,
        overspeed_kmh=overspeed,
        stop_speed_kmh=stop_speed,
        stop_min_minutes=stop_minutes,
        harsh_turn_deg=harsh_turn
    )

    paths_by_vehicle = {}
    try:
        uid = int(current_user.id)
        row = get_conn().execute("""
            SELECT path_json
            FROM last_plans
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, [uid]).fetchone()
        if row and row[0]:
            paths_by_vehicle = json.loads(row[0])
    except Exception as e:
        print("[offroute] falha ao carregar path_json:", e)

    # Função auxiliar para calcular distância ponto-linha
    def _point_to_linestring_km(point, line):
        min_dist = float('inf')
        for i in range(len(line)-1):
            a = line[i]
            b = line[i+1]
            dist = _haversine_km(point[0], point[1], a[0], a[1])
            min_dist = min(min_dist, dist)
        return min_dist

    if paths_by_vehicle:
        for p in pts:
            v = p["vehicle_id"]
            line = paths_by_vehicle.get(v)
            if not line or len(line) < 2:
                continue
            try:
                d = _point_to_linestring_km((p["lat"], p["lon"]), line)
                if d > buffer_km:
                    ev.append({
                        "type": "off_route",
                        "vehicle_id": v,
                        "ts": p["ts"],
                        "lat": p["lat"], "lon": p["lon"],
                        "distance_km": round(d, 3)
                    })
            except Exception:
                continue

    return jsonify(ev)

@app.get("/api/telemetry/series")
@login_required
def api_telemetry_series():
    hours = int(request.args.get("hours", 6))
    vid = (request.args.get("vehicle_id") or "").strip()
    if not vid:
        return jsonify([])

    conn = get_conn()
    rows = conn.execute(f"""
        SELECT timestamp AS ts, COALESCE(speed, 0) AS speed
        FROM telemetry
        WHERE vehicle_id = ?
          AND timestamp >= {_sql_hours_ago(hours)}
        ORDER BY ts
        LIMIT 20000
    """, [vid]).fetchall()

    out = []
    for ts, spd in rows:
        try:
            out.append({"t": str(ts), "speed": float(spd)})
        except Exception:
            continue
    return jsonify(out)

@app.get("/api/telemetry/export")
@login_required
def export_telemetry():
    fmt = (request.args.get("fmt") or "csv").lower()
    hours = int(request.args.get("hours") or 6)
    vehicle_id = request.args.get("vehicle_id") or None

    pts = _fetch_points(hours, vehicle_id)
    evs = _detect_events(pts)

    if fmt == "csv":
        return _export_csv(pts, evs, hours, vehicle_id)
    elif fmt == "pdf":
        try:
            return _export_pdf(pts, evs, hours, vehicle_id)
        except Exception as e:
            return jsonify({"ok": False, "error": f"PDF indisponível ({e}). Instale 'reportlab' ou exporte CSV."}), 501
    else:
        return jsonify({"ok": False, "error": "Formato inválido. Use csv ou pdf."}), 400

def _export_csv(pts, evs, hours, vehicle_id):
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["# OptiFleet export"])
    w.writerow([f"Filtro horas: {hours}", f"Veículo: {vehicle_id or 'todos'}"])
    w.writerow([])

    w.writerow(["TRILHA"])
    w.writerow(["vehicle_id","ts","lat","lon","speed_kmh","bearing"])
    for p in pts:
        w.writerow([
            p.get("vehicle_id",""), p.get("ts",""),
            p.get("lat",""), p.get("lon",""),
            p.get("speed",""), p.get("bearing",""),
        ])
    w.writerow([])

    w.writerow(["EVENTOS"])
    w.writerow(["vehicle_id","type","ts","lat","lon","minutes","value","delta_deg"])
    for e in evs:
        w.writerow([
            e.get("vehicle_id",""), e.get("type",""), e.get("ts",""),
            e.get("lat",""), e.get("lon",""),
            e.get("minutes",""), e.get("value",""), e.get("delta_deg",""),
        ])

    data = buf.getvalue().encode("utf-8-sig")
    bio = BytesIO(data)
    fname = f"optifleet_{vehicle_id or 'all'}_{hours}h.csv"
    return send_file(bio, mimetype="text/csv", as_attachment=True, download_name=fname)

def _export_pdf(pts, evs, hours, vehicle_id):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    bio = BytesIO()
    c = canvas.Canvas(bio, pagesize=landscape(A4))
    width, height = landscape(A4)

    title = f"OptiFleet — Exportação ({hours}h, veículo: {vehicle_id or 'todos'})"
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, height-40, title)

    def draw_table(x, y, rows, col_widths):
        line_h = 14
        cur_y = y
        for i, row in enumerate(rows):
            cur_x = x
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 9)
            for col, w in zip(row, col_widths):
                txt = str(col)[:80]
                c.drawString(cur_x+2, cur_y, txt)
                cur_x += w
            cur_y -= line_h
            if cur_y < 40:
                c.showPage()
                c.setFont("Helvetica", 9)
                cur_y = height - 60
        return cur_y

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, height-70, "Trilha (amostra)")
    rows1 = [["vehicle_id","ts","lat","lon","speed_kmh","bearing"]]
    for p in pts[:40]:
        rows1.append([
            p.get("vehicle_id",""), p.get("ts",""),
            round(p.get("lat",0), 6), round(p.get("lon",0), 6),
            p.get("speed",""), p.get("bearing",""),
        ])
    y = draw_table(40, height-90, rows1, [90,140,90,90,80,80])

    if y < 160:
        c.showPage()
        y = height - 60

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Eventos (amostra)")
    rows2 = [["vehicle_id","type","ts","lat","lon","minutes","value","delta_deg"]]
    for e in evs[:40]:
        rows2.append([
            e.get("vehicle_id",""), e.get("type",""), e.get("ts",""),
            round(e.get("lat",0), 6), round(e.get("lon",0), 6),
            e.get("minutes",""), e.get("value",""), e.get("delta_deg",""),
        ])
    draw_table(40, y-20, rows2, [90,80,140,90,90,70,70,70])

    c.showPage()
    c.save()
    bio.seek(0)
    fname = f"optifleet_{vehicle_id or 'all'}_{hours}h.pdf"
    return send_file(bio, mimetype="application/pdf", as_attachment=True, download_name=fname)

# ----------------------------------------------------------------------------- #
# Subscribe shim
# ----------------------------------------------------------------------------- #
@app.get("/subscribe")
@login_required
def subscribe_shim():
    qs = request.query_string.decode()
    return redirect(f"/billing/go?{qs}")

# ----------------------------------------------------------------------------- #
# Otimização / Roteirização
# ----------------------------------------------------------------------------- #
def parse_request(payload) -> OptimizeRequest:
    depot = payload["depot"]

    # =============== DEPÓSITO ==================
    d_lat = depot.get("lat")
    d_lon = depot.get("lon")
    depot_addr = (depot.get("address") or depot.get("endereco") or "").strip()

    if not d_lat or not d_lon:
        if not depot_addr:
            raise ValueError("Depósito sem coordenadas e sem endereço.")
        geo = rp.geocode(depot_addr)
        if not geo:
            raise ValueError(f"Não foi possível geocodificar o depósito: {depot_addr}")
        d_lat, d_lon = geo

    d = Depot(
        loc=Location(float(d_lat), float(d_lon)),
        window=TimeWindow(
            hhmm_to_minutes(depot.get("start_window", "00:00")),
            hhmm_to_minutes(depot.get("end_window", "23:59")),
        ),
    )

    # =============== VEÍCULOS ==================
    vehicles = []
    for v in payload["vehicles"]:
        vehicles.append(
            Vehicle(
                id=v["id"],
                capacity=int(v.get("capacity", 999999)),
                start_min=hhmm_to_minutes(v.get("start_time", "00:00")),
                end_min=hhmm_to_minutes(v.get("end_time", "23:59")),
                speed_factor=float(v.get("speed_factor", 1.0)),
            )
        )

    # =============== PARADAS ==================
    stops = []
    for idx, s in enumerate(payload["stops"], start=1):

        # Aceitar address OU endereco
        s_addr = (s.get("address") or s.get("endereco") or "").strip()
        s_lat = s.get("lat")
        s_lon = s.get("lon")

        # Caso 1: veio lat/lon válidos → usa direto
        if s_lat not in (None, "", " ") and s_lon not in (None, "", " "):
            try:
                lat = float(s_lat)
                lon = float(s_lon)
            except Exception:
                lat = None
                lon = None
        else:
            lat = None
            lon = None

        # Caso 2: veio endereço → geocodifica
        if (lat is None or lon is None):
            if not s_addr:
                raise ValueError(
                    f"Parada {s.get('id', f'S{idx}')} sem lat/lon e sem endereço."
                )
            geo = rp.geocode(s_addr)
            if not geo:
                raise ValueError(
                    f"Não foi possível geocodificar a parada "
                    f"{s.get('id', f'S{idx}')}: {s_addr}"
                )
            lat, lon = geo

        tw = None
        if s.get("tw_start") and s.get("tw_end"):
            tw = TimeWindow(
                hhmm_to_minutes(s["tw_start"]),
                hhmm_to_minutes(s["tw_end"]),
            )

        stops.append(
            Stop(
                id=s.get("id") or f"S{idx}"],
                loc=Location(lat, lon),
                demand=int(s.get("demand", 0)),
                service_min=int(s.get("service_min", 0)),
                window=tw,
            )
        )

    return OptimizeRequest(
        depot=d,
        vehicles=vehicles,
        stops=stops,
        objective=payload.get("objective", "min_cost"),
        include_tolls=bool(payload.get("include_tolls", True)),
    )

@app.post("/optimize")
@login_required
def optimize():
    raw = request.get_json(force=True)
    try:
        req = parse_request(raw)
    except Exception as e:
        return jsonify({"status": "bad_request", "message": str(e)}), 400

    all_points = [req.depot] + req.stops
    try:
        m = rp.travel_matrix(all_points)
    except Exception as e:
        return jsonify({"status": "provider_error", "message": f"Erro no provedor de rotas: {e}"}), 500

    n_all = len(all_points)
    time_m_all = [[0.0] * n_all for _ in range(n_all)]
    dist_m_all = [[0.0] * n_all for _ in range(n_all)]
    for i in range(n_all):
        for j in range(n_all):
            cell = m[(i, j)]
            time_m_all[i][j] = cell["minutes"]
            dist_m_all[i][j] = cell["km"]

    raw_stops = raw.get("stops", [])
    id_to_vehicle = {rs.get("id"): rs.get("vehicle") for rs in raw_stops if rs.get("id")}
    stops_by_vehicle = {}
    has_assignment = False
    for s in req.stops:
        vcode = id_to_vehicle.get(s.id)
        if vcode:
            has_assignment = True
            setattr(s, "assigned_vehicle", vcode)
            stops_by_vehicle.setdefault(vcode, []).append(s)

    results = []
    total_time = 0.0
    total_dist = 0.0
    veh_by_id = {v.id: v for v in req.vehicles}

    def slice_and_solve(stops_subset, vehicles_subset):
        points = [req.depot] + stops_subset
        idx_map = [0] + [1 + req.stops.index(s) for s in stops_subset]
        n = len(points)

        tmat = [[0.0] * n for _ in range(n)]
        dmat = [[0.0] * n for _ in range(n)]
        for a, ia in enumerate(idx_map):
            for b, ib in enumerate(idx_map):
                tmat[a][b] = time_m_all[ia][ib]
                dmat[a][b] = dist_m_all[ia][ib]

        depot_index = 0
        service_times = [0] + [s.service_min for s in stops_subset]
        demands = [0] + [s.demand for s in stops_subset]
        time_windows = [(req.depot.window.start_min, req.depot.window.end_min)]
        for s in stops_subset:
            if s.window:
                time_windows.append((s.window.start_min, s.window.end_min))
            else:
                time_windows.append((req.depot.window.start_min, req.depot.window.end_min))

        return solve_vrptw(
            time_matrix_min=tmat, dist_matrix_km=dmat,
            depot_index=depot_index, service_times=service_times, demands=demands,
            time_windows=time_windows, vehicles=vehicles_subset, objective=req.objective
        )

    def rr_partition(stops, vid_list):
        buckets = {vid: [] for vid in vid_list}
        if not stops:
            return buckets
        for i, s in enumerate(stops):
            buckets[vid_list[i % len(vid_list)]].append(s)
        return buckets

    FORCE_PER_VEHICLE = True
    if FORCE_PER_VEHICLE:
        if not has_assignment:
            veh_ids = [v.id for v in req.vehicles]
            stops_by_vehicle = rr_partition(req.stops, veh_ids)

        for vid, v in veh_by_id.items():
            subset = stops_by_vehicle.get(vid, [])
            if subset:
                veh_def = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min}]
                r = slice_and_solve(subset, veh_def)
                if r.get("status") != "ok":
                    return jsonify(r), 400
                for route in r["routes"]:
                    route["vehicle_id"] = v.id
                    abs_nodes = []
                    for n in route.get("nodes", []):
                        abs_nodes.append(0 if n == 0 else (1 + req.stops.index(subset[n - 1])))
                    route["nodes_abs"] = abs_nodes
                    route["nodes"] = route.get("nodes_abs", route.get("nodes", []))
                    results.append(route)
                    total_time += route["time_min"]
                    total_dist += route["dist_km"]
            else:
                results.append({"vehicle_id": v.id, "nodes": [0,0], "nodes_abs": [0,0], "time_min": 0.0, "dist_km": 0.0})
    else:
        if has_assignment:
            for vcode, subset in stops_by_vehicle.items():
                v = veh_by_id.get(vcode)
                if not v:
                    return jsonify({"status": "bad_request", "message": f"Veículo '{vcode}' não existe."}), 400
                veh_def = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min}]
                r = slice_and_solve(subset, veh_def)
                if r.get("status") != "ok":
                    return jsonify(r), 400
                for route in r["routes"]:
                    route["vehicle_id"] = v.id
                    results.append(route)
                    total_time += route["time_min"]
                    total_dist += route["dist_km"]
        else:
            vehs = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min} for v in req.vehicles]
            r = slice_and_solve(req.stops, vehs)
            if r.get("status") != "ok":
                return jsonify(r), 400
            results = r["routes"]
            for route in results:
                if "nodes_abs" not in route and "nodes" in route:
                    route["nodes_abs"] = route["nodes"]
            total_time = r["total_time_min"]
            total_dist = r["total_dist_km"]

    telemetry_all = raw.get("telemetry", {})
    maintenance = []
    for v in req.vehicles:
        tel = telemetry_all.get(v.id, {"km_rodados": 20000, "dias_desde_ultima_manutencao": 60, "alertas_obd": 0})
        maintenance.append({"vehicle_id": v.id, "failure_risk": predict_failure_risk(tel)})

    ts = int(time.time())
    maps_dir = Path(app.static_folder) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    map_path = maps_dir / f"route_{ts}.html"
    # Agora o mapa é servido como arquivo estático diretamente
    map_rel = f"/static/maps/route_{ts}.html"

    PALETTE = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]
    veh_ids = [v.id for v in req.vehicles]
    color_by_vehicle = {vid: PALETTE[i % len(PALETTE)] for i, vid in enumerate(veh_ids)}

    def _fetch_path(origin_latlon, dest_latlon):
        class P:
            def __init__(self, lat, lon):
                self.lat = lat; self.lon = lon
        o = P(origin_latlon[0], origin_latlon[1])
        d = P(dest_latlon[0], dest_latlon[1])
        return rp.leg_polyline(o, d)

    def _ll(obj):
        try:
            return float(obj.loc.lat), float(obj.loc.lon)
        except Exception:
            return float(obj.lat), float(obj.lon)

    all_pts = [req.depot] + req.stops
    vehicle_paths: dict[str, list[list[float]]] = {}
    for route in results:
        vid = route.get("vehicle_id", "V?")
        nodes = route.get("nodes_abs") or route.get("nodes") or []
        if len(nodes) < 2:
            continue

        full_coords: list[list[float]] = []
        for a, b in zip(nodes[:-1], nodes[1:]):
            o_lat, o_lon = _ll(all_pts[a])
            d_lat, d_lon = _ll(all_pts[b])

            leg_coords: list[list[float]] = []
            try:
                path = rp.leg_polyline(
                    type("P", (), {"lat": o_lat, "lon": o_lon})(),
                    type("P", (), {"lat": d_lat, "lon": d_lon})()
                )
                leg_coords = _coerce_path_to_coords(path) or []
            except Exception:
                leg_coords = []

            if not leg_coords:
                leg_coords = [[o_lat, o_lon], [d_lat, d_lon]]

            if full_coords and leg_coords and full_coords[-1] == leg_coords[0]:
                full_coords.extend(leg_coords[1:])
            else:
                full_coords.extend(leg_coords)

        if full_coords:
            vehicle_paths[vid] = full_coords

    map_ok = True
    try:
        build_map(
            [req.depot] + req.stops,
            results,
            str(map_path),
            fetch_path=_fetch_path,
            color_by_vehicle=color_by_vehicle,
            legend_title="Rotas por veículo"
        )
    except Exception as e:
        print("[MAP] Falha ao gerar mapa:", e)
        map_ok = False

    uid = int(current_user.id)
    with get_conn() as con:
        con.execute("DELETE FROM last_plans WHERE user_id = ?", [uid])
        con.execute("""
            INSERT INTO last_plans (user_id, created_at, req_json, routes_json, map_url, path_json)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
        """, [
            uid,
            json.dumps(raw),
            json.dumps(results),
            map_rel if map_ok else "",
            json.dumps(vehicle_paths)
        ])

    return jsonify({
        "status": "ok",
        "routes": results,
        "total_time_min": total_time,
        "total_dist_km": total_dist,
        "maintenance": maintenance,
        "map_url": map_rel if map_ok else ""
    })

# --------------------------------------------------------------------- #
# Último mapa otimizado por cliente (usado no dashboard)
# --------------------------------------------------------------------- #
@app.get("/api/fleet/optimize/last_map")
@login_required
def api_optimize_last_map():
    """
    Retorna o último mapa de otimização do usuário logado.
    Bate com o fetch('/api/fleet/optimize/last_map') no front.
    """
    try:
        uid = int(current_user.id)
        row = get_conn().execute("""
            SELECT map_url
            FROM last_plans
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, [uid]).fetchone()
        if not row or not row[0]:
            return jsonify({"ok": False, "map_url": None})
        return jsonify({"ok": True, "map_url": row[0]})
    except Exception as e:
        print("[ERROR] GET /api/fleet/optimize/last_map:", e)
        print(traceback.format_exc())
        return jsonify({"ok": False, "map_url": None}), 500

# Alias de API para o mesmo fluxo de /optimize
@bp_fleet.post("/api/optimize")
@login_required
def api_optimize():
    """
    Alias para o mesmo fluxo de otimização usado em /optimize.
    O frontend pode chamar /api/optimize com o mesmo payload JSON
    e recebe exatamente a mesma resposta que em POST /optimize.
    """
    return optimize()

# ----------------------------------------------------------------------------- #
# APIs de veículos / rastreadores
# ----------------------------------------------------------------------------- #
@app.get("/api/vehicles")
@login_required
def api_veiculos():
    """
    Retorna lista de veículos do usuário logado.
    Formato: [{"id": "V1", "name": "Fusca Azul"}, ...]
    """
    uid = int(current_user.id)
    # TODO: substituir por busca real no banco
    vehicles = [
        {"id": "V1", "name": "Caminhão 1"},
        {"id": "V2", "name": "Caminhão 2"},
        {"id": "V3", "name": "Carro de Entrega"}
    ]
    return jsonify(vehicles)

@app.post("/api/trackers/link")
@login_required
def api_trackers_link():
    """
    Vincula um rastreador (IMEI + token) a um veículo.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "Dados ausentes"}), 400

        imei = data.get("imei")
        secret_token = data.get("secret_token")
        vehicle_id = data.get("vehicle_id")

        if not imei or not secret_token or not vehicle_id:
            return jsonify({"ok": False, "error": "IMEI, token ou veículo ausente"}), 400

        # TODO: implementar lógica real de vinculação com banco
        print(f"[link_tracker] IMEI={imei}, Token={secret_token}, Veículo={vehicle_id}")
        return jsonify({"ok": True, "message": "Rastreador vinculado com sucesso!"})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/mapfile/<name>")
@login_required
def serve_tmp_map(name):
    """
    Mantido por compatibilidade, mas agora lê de static/maps,
    onde o build_map salva o HTML.
    """
    filepath = Path(app.static_folder) / "maps" / name
    if not filepath.exists():
        return "Mapa não encontrado", 404
    return send_file(str(filepath), mimetype="text/html")

# ----------------------------------------------------------------------------- #
# Blueprints
# ----------------------------------------------------------------------------- #
app.register_blueprint(bp_auth)
app.register_blueprint(bp_fleet)
app.register_blueprint(bp_tele)
app.register_blueprint(bp_reroute)
app.register_blueprint(bp_notify)
app.register_blueprint(bp_vendor)
app.register_blueprint(bp_billing)
app.register_blueprint(bp_asaas)
app.register_blueprint(bp_asaas_webhook)
app.register_blueprint(bp_trial)
app.register_blueprint(bp_contact)
app.register_blueprint(bp_checkout)
app.register_blueprint(bp_demo)
app.register_blueprint(bp_account)

# ----------------------------------------------------------------------------- #
# Run (compatível com Render e local)
# ----------------------------------------------------------------------------- #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    host = "0.0.0.0"
    print(f"🚀 Servidor OptiFleet iniciando em http://{host}:{port}")
    app.run(host=host, port=port, debug=False)










