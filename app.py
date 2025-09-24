











# app.py
from pathlib import Path
import os
import time
from datetime import date, datetime, timedelta, timezone
from math import radians, sin, cos, sqrt, atan2

from dotenv import load_dotenv
from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, flash, send_file
)
from flask_login import (
    LoginManager, login_required, current_user
)

# ===== DB / Users =====
from core.db import get_user_by_id, get_conn, obter_posicoes, get_active_subscription

# ===== Core (rotas, solver, mapa, manutenção) =====
from core.models import Location, TimeWindow, Depot, Vehicle, Stop, OptimizeRequest, hhmm_to_minutes
from core.providers.maps import RoutingProvider
from core.solver.vrptw import solve_vrptw
from core.maintenance.predictor import predict_failure_risk
from core.visual.map_render import build_map

# ===== Telemetria (seed opcional) =====
from core.telemetry import salvar_telemetria

# ===== Blueprints =====
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
from core.db import get_active_subscription, get_active_trial
from datetime import datetime, timezone
from routes.account_routes import bp_account
# Disponibiliza user/sub/trial globalmente nos templates (Jinja)
from core.db import get_active_subscription, get_active_trial, create_trial


# ===== Export helpers =====
from io import StringIO, BytesIO
import csv

# Helpers
# ----------------------------------------------------------------------
def _to_aware_utc(dt):
    """Converte dt (str/datetime) para datetime c/ tz=UTC."""
    if dt is None:
        return None
    if isinstance(dt, str):
        # tolera formatos ISO sem timezone
        try:
            dt = datetime.fromisoformat(dt.replace("Z","").split(".")[0])
        except Exception:
            return None
    if dt.tzinfo is None:
        # timestamp do DuckDB normalmente vem ingênuo -> assume UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _days_left(trial):
    if not trial:
        return None
    # aceita tuple/duckdb.Row ou dict
    end = None
    try:
        end = trial["trial_end"]  # se vier como dict
    except Exception:
        try:
            end = trial[4]         # se vier como tupla: (id, user_id, plan, vehicles, trial_end, status, ...)
        except Exception:
            end = None
    end = _to_aware_utc(end)
    if not end:
        return None
    now = datetime.now(timezone.utc)
    delta = end - now
    # arredonda por baixo em dias
    return max(0, int(delta.total_seconds() // 86400))

def _ensure_trial(user_id: int):
    """Cria trial se não houver e AUTO_TRIAL estiver ligado."""
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

# -----------------------------------------------------------------------------
# App / Config
# -----------------------------------------------------------------------------
load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")

# 🔐 chave de sessão
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# Cookies permissivos no ambiente local http://127.0.0.1
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
)

AUTO_TRIAL = os.getenv("AUTO_TRIAL", "1") == "1"  # ativa trial automática em dev
DEFAULT_TRIAL_PLAN = os.getenv("DEFAULT_TRIAL_PLAN", "full")
DEFAULT_TRIAL_VEHICLES = int(os.getenv("DEFAULT_TRIAL_VEHICLES", "10"))
DEFAULT_TRIAL_DAYS = int(os.getenv("DEFAULT_TRIAL_DAYS", "14"))


# -----------------------------------------------------------------------------
# Login
# -----------------------------------------------------------------------------
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"
login_manager.init_app(app)

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
        "days_left": _days_left(trial),  # <- use exatamente este
    }



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

@login_manager.user_loader
def load_user(user_id: str):
    try:
        row = get_user_by_id(int(user_id))
    except Exception:
        row = None
    if not row:
        return None
    return UserObj(row["id"], row["email"])

# -----------------------------------------------------------------------------
# Blueprints
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Assets / Providers
# -----------------------------------------------------------------------------
(Path(app.static_folder) / "maps").mkdir(parents=True, exist_ok=True)
rp = RoutingProvider()

# Semente de telemetria (tenta uma vez)
try:
    salvar_telemetria("empresa_123", "V1", -8.05, -34.9, 60.5, 80.0)
except Exception:
    pass

# -----------------------------------------------------------------------------
# Rotas simples / páginas
# -----------------------------------------------------------------------------
@app.get("/")
@login_required
def home():
    uid = int(current_user.id)

    # cria trial automático em dev, se não houver
    _ensure_trial(uid)

    sub = get_active_subscription(uid)
    trial = get_active_trial(uid)

    ok_sub = bool(sub and (sub[4] == "active"))        # subscriptions.status
    ok_trial = bool(trial and (trial[5] == "active"))  # trials.status

    if ok_sub or ok_trial:
        return render_template("index.html", hide_paywall=True)

    # se chegou aqui, não tem sub/trial e auto_trial está desligado (prod)
    return redirect(url_for("landing"))



@app.get("/site")
def landing():
    return render_template("landing.html")

@app.get("/app")
@login_required
def dashboard():
    kpis = {"total_veiculos": 12, "viagens_hoje": 8, "viagens_semana": 43, "alertas": 3}
    return render_template("index.html", kpis=kpis)

@app.get("/pricing")
@login_required
def pricing_alias():
    return redirect(url_for("billing.pricing_page"))


@app.get("/vehicles")
@login_required
def vehicles_page():
    return render_template("vehicles.html")

@app.get("/tracking")
@login_required
def tracking():
    return render_template("tracking.html")

@app.get("/contact")
def contact_page():
    return render_template("contact.html", today=date.today().strftime("%d/%m/%Y"))

@app.post("/contact")
def contact_submit():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    company = (request.form.get("company") or "").strip()
    message = (request.form.get("message") or "").strip()
    print("[CONTACT]", {"name": name, "email": email, "company": company, "message": message, "ip": request.remote_addr})
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

# -----------------------------------------------------------------------------
# KPIs (defensivo sobre DuckDB)
# -----------------------------------------------------------------------------
@app.get("/api/kpis")
@login_required
def api_kpis():
    out = {"total_veiculos": 0, "viagens_hoje": 0, "viagens_semana": 0, "alertas": 0}
    try:
        conn = get_conn()

        # Veículos com posição nos últimos 5min
        try:
            out["total_veiculos"] = conn.execute("""
                SELECT COUNT(DISTINCT vehicle_id)
                FROM telemetry
                WHERE timestamp >= NOW() - INTERVAL 5 MINUTE
            """).fetchone()[0] or 0
        except Exception:
            pass

        desloc_thresh = 0.01  # ~1km+ (aprox) somando variação lat/lon

        # Viagens hoje
        try:
            out["viagens_hoje"] = conn.execute(f"""
                SELECT COUNT(*) FROM (
                  SELECT vehicle_id
                  FROM telemetry
                  WHERE CAST(timestamp AS DATE) = CURRENT_DATE
                  GROUP BY vehicle_id
                  HAVING COUNT(*) >= 20
                     AND ( (MAX(lat)-MIN(lat)) + (MAX(lon)-MIN(lon)) ) > {desloc_thresh}
                ) t
            """).fetchone()[0] or 0
        except Exception:
            pass

        # Viagens na semana ISO corrente
        try:
            out["viagens_semana"] = conn.execute(f"""
                WITH base AS (
                  SELECT *
                  FROM telemetry
                  WHERE CAST(timestamp AS DATE) >= DATE_TRUNC('week', CURRENT_DATE)
                )
                SELECT COUNT(*) FROM (
                  SELECT vehicle_id
                  FROM base
                  GROUP BY vehicle_id
                  HAVING COUNT(*) >= 60
                     AND ( (MAX(lat)-MIN(lat)) + (MAX(lon)-MIN(lon)) ) > {desloc_thresh}
                ) t
            """).fetchone()[0] or 0
        except Exception:
            pass

        # Alertas (se existir speed)
        try:
            cols = {r[1].lower() for r in conn.execute("PRAGMA_table_info('telemetry')").fetchall()}
            if "speed" in cols:
                out["alertas"] = conn.execute("""
                    SELECT COUNT(*) FROM telemetry
                    WHERE timestamp >= NOW() - INTERVAL 1 HOUR
                      AND COALESCE(speed, 0) > 100
                """).fetchone()[0] or 0
        except Exception:
            pass

    except Exception:
        pass
    return jsonify(out)

# -----------------------------------------------------------------------------
# Telemetria (API leve + export)
# -----------------------------------------------------------------------------
@app.get("/api/telemetry")
@login_required
def api_telemetry():
    client_id = str(current_user.id)
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
        rows = conn.execute("""
            SELECT vehicle_id, lat, lon, timestamp AS ts, COALESCE(speed,0) AS speed
            FROM telemetry
            WHERE timestamp >= NOW() - INTERVAL ? HOUR
              AND vehicle_id = ?
            ORDER BY ts
            LIMIT ?
        """, [hours, vehicle_id, limit]).fetchall()
    else:
        rows = conn.execute("""
            SELECT vehicle_id, lat, lon, timestamp AS ts, COALESCE(speed,0) AS speed
            FROM telemetry
            WHERE timestamp >= NOW() - INTERVAL ? HOUR
            ORDER BY vehicle_id, ts
            LIMIT ?
        """, [hours, limit]).fetchall()

    pts = []
    for r in rows:
        try:
            pts.append({
                "vehicle_id": str(r[0]),
                "lat": float(r[1]),
                "lon": float(r[2]),
                "ts": str(r[3]),
                "speed": float(r[4]),
                "bearing": None,  # pode ser preenchido depois se quiser
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

        # stop (janela contínua com speed baixa)
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

        # harsh_turn: variação de rumo grande
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

    pts = _fetch_points(hours, vid)
    ev = _detect_events(
        pts,
        overspeed_kmh=overspeed,
        stop_speed_kmh=stop_speed,
        stop_min_minutes=stop_minutes,
        harsh_turn_deg=harsh_turn
    )
    return jsonify(ev)

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

# -----------------------------------------------------------------------------
# Subscribe shim
# -----------------------------------------------------------------------------
@app.get("/subscribe")
@login_required
def subscribe_shim():
    qs = request.query_string.decode()
    return redirect(f"/billing/go?{qs}")

# -----------------------------------------------------------------------------
# Otimização / Roteirização
# -----------------------------------------------------------------------------
def parse_request(payload) -> OptimizeRequest:
    depot = payload["depot"]
    d_lat, d_lon = depot.get("lat"), depot.get("lon")
    if (d_lat is None or d_lon is None) and depot.get("address"):
        geo = rp.geocode(depot["address"])
        if not geo:
            raise ValueError(f"Não foi possível geocodificar o endereço do depósito: {depot['address']}")
        d_lat, d_lon = geo

    d = Depot(
        loc=Location(float(d_lat), float(d_lon)),
        window=TimeWindow(hhmm_to_minutes(depot["start_window"]), hhmm_to_minutes(depot["end_window"]))
    )

    vehicles = []
    for v in payload["vehicles"]:
        vehicles.append(Vehicle(
            id=v["id"],
            capacity=int(v.get("capacity", 999999)),
            start_min=hhmm_to_minutes(v.get("start_time", "00:00")),
            end_min=hhmm_to_minutes(v.get("end_time", "23:59")),
            speed_factor=float(v.get("speed_factor", 1.0))
        ))

    stops = []
    for s in payload["stops"]:
        s_lat, s_lon = s.get("lat"), s.get("lon")
        if (s_lat is None or s_lon is None) and s.get("address"):
            geo = rp.geocode(s["address"])
            if not geo:
                raise ValueError(f"Não foi possível geocodificar o endereço da parada {s.get('id','?')}: {s['address']}")
            s_lat, s_lon = geo

        tw = None
        if s.get("tw_start") and s.get("tw_end"):
            tw = TimeWindow(hhmm_to_minutes(s["tw_start"]), hhmm_to_minutes(s["tw_end"]))

        stops.append(Stop(
            id=s["id"],
            loc=Location(float(s_lat), float(s_lon)),
            demand=int(s.get("demand", 0)),
            service_min=int(s.get("service_min", 0)),
            window=tw
        ))

    return OptimizeRequest(
        depot=d,
        vehicles=vehicles,
        stops=stops,
        objective=payload.get("objective", "min_cost"),
        include_tolls=bool(payload.get("include_tolls", True))
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

    FORCE_PER_VEHICLE = True  # fixa um veículo por rota (simplifica visual)
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
                        if n == 0:
                            abs_nodes.append(0)
                        else:
                            abs_nodes.append(1 + req.stops.index(subset[n - 1]))
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
    map_rel = f"/static/maps/route_{ts}.html"

    def _fetch_path(origin_latlon, dest_latlon):
        class P:  # mini wrapper
            def __init__(self, lat, lon):
                self.lat = lat; self.lon = lon
        o = P(origin_latlon[0], origin_latlon[1])
        d = P(dest_latlon[0], dest_latlon[1])
        return rp.leg_polyline(o, d)

    map_ok = True
    try:
        build_map([req.depot] + req.stops, results, str(map_path), fetch_path=_fetch_path)
    except Exception as e:
        print("[MAP] Falha ao gerar mapa:", e)
        map_ok = False

    return jsonify({
        "status": "ok",
        "routes": results,
        "total_time_min": total_time,
        "total_dist_km": total_dist,
        "maintenance": maintenance,
        "map_url": map_rel if map_ok else ""
    })

# -----------------------------------------------------------------------------
# Run
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)








