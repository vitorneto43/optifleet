# app.py
from pathlib import Path
import os
import time

from flask import Flask, request, jsonify, render_template, redirect, url_for
from dotenv import load_dotenv
from flask_login import LoginManager, login_required, current_user

from core.db import Base, engine, SessionLocal
import core.fleet_models  # garante que os modelos da frota sejam registrados
from core.auth_models import User

from core.models import (
    Location, TimeWindow, Depot, Vehicle, Stop, OptimizeRequest, hhmm_to_minutes
)
from core.providers.maps import RoutingProvider
from core.solver.vrptw import solve_vrptw
from core.maintenance.predictor import predict_failure_risk
from core.visual.map_render import build_map

# blueprints
from routes.auth_routes import bp_auth
from routes.fleet_routes import bp_fleet
from routes.telemetry_routes import bp_tele
from routes.reroute_routes import bp_reroute
# from routes.report_routes import bp_reports   # opcional
from routes.notify_routes import bp_notify
from routes.vendor_ingest_routes import bp_vendor
from billing.asaas_routes import bp_asaas
from billing.asaas_webhook import bp_asaas_webhook

# ----- carregamento de env e DB -----
load_dotenv()
Base.metadata.create_all(bind=engine)

# ----- app -----
app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# ----- login -----
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    s = SessionLocal()
    try:
        return s.get(User, int(user_id))
    finally:
        s.close()

# ----- blueprints -----
app.register_blueprint(bp_auth)
app.register_blueprint(bp_asaas)
app.register_blueprint(bp_asaas_webhook)
app.register_blueprint(bp_fleet)
app.register_blueprint(bp_tele)
app.register_blueprint(bp_reroute)
# app.register_blueprint(bp_reports)
app.register_blueprint(bp_notify)
app.register_blueprint(bp_vendor)

# ----- assets -----
Path(app.static_folder, "maps").mkdir(parents=True, exist_ok=True)

# ----- providers -----
rp = RoutingProvider()

# =========================
#   Paywall DESLIGADO
# =========================
def require_active_subscription(fn):
    # No demo: nunca bloqueia
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper

# =========================
#   Rotas básicas
# =========================
@app.get("/")
@login_required
def home():
    # hide_paywall=True -> templates/index.html não mostra cartão "Acesso restrito"
    return render_template("index.html", hide_paywall=True)

# qualquer tentativa de abrir pricing cai no dashboard
@app.get("/pricing")
@login_required
def pricing():
    return redirect(url_for("home"))

# botão “Assinar X” pode apontar para cá, que só volta ao dashboard com flag
@app.get("/go/plan/<code>")
@login_required
def go_plan(code):
    return redirect(url_for("home", plan=code))

@app.get("/vehicles")
@login_required
def vehicles_page():
    return render_template("vehicles.html")

@app.get("/tracking")
@login_required
def tracking():
    return render_template("tracking.html")

# =========================
#   Otimização
# =========================
def parse_request(payload) -> OptimizeRequest:
    # ---- Depósito ----
    depot = payload['depot']
    d_lat, d_lon = depot.get('lat'), depot.get('lon')
    if (d_lat is None or d_lon is None) and depot.get('address'):
        geo = rp.geocode(depot['address'])
        if not geo:
            raise ValueError(f"Não foi possível geocodificar o endereço do depósito: {depot['address']}")
        d_lat, d_lon = geo

    d = Depot(
        loc=Location(float(d_lat), float(d_lon)),
        window=TimeWindow(hhmm_to_minutes(depot['start_window']), hhmm_to_minutes(depot['end_window']))
    )

    # ---- Veículos ----
    vehicles = []
    for v in payload['vehicles']:
        vehicles.append(Vehicle(
            id=v['id'],
            capacity=int(v.get('capacity', 999999)),
            start_min=hhmm_to_minutes(v.get('start_time', '00:00')),
            end_min=hhmm_to_minutes(v.get('end_time', '23:59')),
            speed_factor=float(v.get('speed_factor', 1.0))
        ))

    # ---- Paradas ----
    stops = []
    for s in payload['stops']:
        s_lat, s_lon = s.get('lat'), s.get('lon')
        if (s_lat is None or s_lon is None) and s.get('address'):
            geo = rp.geocode(s['address'])
            if not geo:
                raise ValueError(f"Não foi possível geocodificar o endereço da parada {s.get('id','?')}: {s['address']}")
            s_lat, s_lon = geo

        tw = None
        if s.get('tw_start') and s.get('tw_end'):
            tw = TimeWindow(hhmm_to_minutes(s['tw_start']), hhmm_to_minutes(s['tw_end']))

        stops.append(Stop(
            id=s['id'],
            loc=Location(float(s_lat), float(s_lon)),
            demand=int(s.get('demand', 0)),
            service_min=int(s.get('service_min', 0)),
            window=tw
        ))

    return OptimizeRequest(
        depot=d,
        vehicles=vehicles,
        stops=stops,
        objective=payload.get('objective', 'min_cost'),
        include_tolls=bool(payload.get('include_tolls', True))
    )

@app.post("/optimize")
@login_required
@require_active_subscription
def optimize():
    raw = request.get_json(force=True)
    try:
        req = parse_request(raw)
    except Exception as e:
        return jsonify({"status": "bad_request", "message": str(e)}), 400

    # matriz única
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
            time_m_all[i][j] = cell['minutes']
            dist_m_all[i][j] = cell['km']

    # atribuições por veículo se vier "vehicle" em cada stop
    raw_stops = raw.get("stops", [])
    id_to_vehicle = {rs.get("id"): rs.get("vehicle") for rs in raw_stops if rs.get("id")}
    stops_by_vehicle, has_assignment = {}, False
    for s in req.stops:
        vcode = id_to_vehicle.get(s.id)
        if vcode:
            has_assignment = True
            setattr(s, "assigned_vehicle", vcode)
            stops_by_vehicle.setdefault(vcode, []).append(s)

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

    results, total_time, total_dist = [], 0.0, 0.0
    if has_assignment:
        veh_by_id = {v.id: v for v in req.vehicles}
        for vcode, stops_subset in stops_by_vehicle.items():
            v = veh_by_id.get(vcode)
            if not v:
                return jsonify({"status": "bad_request",
                                "message": f"Paradas atribuídas ao veículo '{vcode}', mas ele não existe."}), 400
            veh_def = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min}]
            r = slice_and_solve(stops_subset, veh_def)
            if r.get('status') != 'ok':
                return jsonify(r), 400
            for route in r['routes']:
                route['vehicle_id'] = v.id
                results.append(route)
                total_time += route['time_min']
                total_dist += route['dist_km']
    else:
        vehs = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min}
                for v in req.vehicles]
        r = slice_and_solve(req.stops, vehs)
        if r.get('status') != 'ok':
            return jsonify(r), 400
        results = r['routes']
        total_time = r['total_time_min']
        total_dist = r['total_dist_km']

    # pedágios
    toll_total = 0.0
    if req.include_tolls:
        for route in results:
            nodes = route['nodes']
            for a, b in zip(nodes[:-1], nodes[1:]):
                origin = req.depot.loc if a == 0 else req.stops[a - 1].loc
                dest   = req.depot.loc if b == 0 else req.stops[b - 1].loc
                try:
                    toll_total += rp.route_cost_with_tolls(origin, dest)
                except Exception:
                    pass

    # manutenção
    telemetry_all = raw.get('telemetry', {})
    maintenance = []
    for v in req.vehicles:
        tel = telemetry_all.get(v.id, {"km_rodados": 20000, "dias_desde_ultima_manutencao": 60, "alertas_obd": 0})
        maintenance.append({"vehicle_id": v.id, "failure_risk": predict_failure_risk(tel)})

    # mapa
    ts = int(time.time())
    maps_dir = Path(app.static_folder) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    map_path = maps_dir / f"route_{ts}.html"
    map_rel = f"/static/maps/route_{ts}.html"
    try:
        build_map([req.depot] + req.stops, results, str(map_path))
    except Exception:
        map_rel = ""

    return jsonify({
        "status": "ok",
        "routes": results,
        "total_time_min": total_time,
        "total_dist_km": total_dist,
        "total_toll_cost": round(toll_total, 2),
        "maintenance": maintenance,
        "map_url": map_rel
    })

# ----- run -----
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)




