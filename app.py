from flask import Flask, request, jsonify, render_template
from pathlib import Path
import time
from core.models import (Location, TimeWindow, Depot, Vehicle, Stop, OptimizeRequest, hhmm_to_minutes)
from core.providers.maps import RoutingProvider
from core.solver.vrptw import solve_vrptw
from core.maintenance.predictor import predict_failure_risk
from core.visual.map_render import build_map
from billing.asaas_routes import bp_asaas
from billing.asaas_webhook import bp_asaas_webhook
from routes.fleet_routes import bp_fleet
from routes.telemetry_routes import bp_tele
from routes.reroute_routes import bp_reroute
from routes.report_routes import bp_reports
from routes.notify_routes import bp_notify
from routes.vendor_ingest_routes import bp_vendor
from dotenv import load_dotenv
from core.db import Base, engine
import core.fleet_models  # noqa: F401
from flask_login import LoginManager, login_required, current_user
from routes.auth_routes import bp_auth
from core.auth_models import User
from core.db import SessionLocal
import os
from functools import wraps
from flask import jsonify
from flask_login import current_user


# cria tabelas se não existirem
Base.metadata.create_all(bind=engine)

load_dotenv()




app = Flask(__name__, template_folder='templates', static_folder='static')

# 🔐 segredo de sessão
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# 🔐 Flask-Login
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"  # rota de login do seu blueprint
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    s = SessionLocal()
    try:
        return s.get(User, int(user_id))
    finally:
        s.close()

app.register_blueprint(bp_auth)
app.register_blueprint(bp_asaas)
app.register_blueprint(bp_asaas_webhook)
app.register_blueprint(bp_fleet)
app.register_blueprint(bp_tele)
app.register_blueprint(bp_reroute)
app.register_blueprint(bp_reports)
app.register_blueprint(bp_notify)
app.register_blueprint(bp_vendor)

# garante pasta dos mapas
Path(app.static_folder, "maps").mkdir(parents=True, exist_ok=True)

rp = RoutingProvider()

# exemplo simples: liga/desliga por variável (ajuste depois para seu banco/tenant)
ENFORCE_SUBSCRIPTION = False  # coloque True quando quiser bloquear de fato

from functools import wraps

def require_active_subscription(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if os.environ.get("PAYWALL_DISABLED") == "1":
            # 👉 Libera sempre
            return fn(*args, **kwargs)
        if not current_user.is_authenticated:
            return jsonify({"status":"unauthorized"}), 401
        if not current_user.has_active_subscription():
            return jsonify({"status":"forbidden"}), 403
        return fn(*args, **kwargs)
    return wrapper





@app.get('/')
@login_required
def home():
    return render_template('index.html')


@app.get('/pricing')
def pricing():
    return render_template('pricing.html')


def parse_request(payload) -> OptimizeRequest:
    # ----- DEPÓSITO: aceita lat/lon ou address -----
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

    # ----- VEÍCULOS -----
    vehicles = []
    for v in payload['vehicles']:
        vehicles.append(Vehicle(
            id=v['id'],
            capacity=int(v.get('capacity', 999999)),
            start_min=hhmm_to_minutes(v.get('start_time','00:00')),
            end_min=hhmm_to_minutes(v.get('end_time','23:59')),
            speed_factor=float(v.get('speed_factor',1.0))
        ))

    # ----- PARADAS: aceitam lat/lon OU address -----
    stops = []
    for s in payload['stops']:
        s_lat, s_lon = s.get('lat'), s.get('lon')
        if (s_lat is None or s_lon is None) and s.get('address'):
            geo = rp.geocode(s['address'])
            if not geo:
                raise ValueError(f"Não foi possível geocodificar o endereço da parada {s.get('id', '?')}: {s['address']}")
            s_lat, s_lon = geo
        tw = None
        if s.get('tw_start') and s.get('tw_end'):
            tw = TimeWindow(hhmm_to_minutes(s['tw_start']), hhmm_to_minutes(s['tw_end']))
        stops.append(Stop(
            id=s['id'],
            loc=Location(float(s_lat), float(s_lon)),
            demand=int(s.get('demand',0)),
            service_min=int(s.get('service_min',0)),
            window=tw
        ))

    return OptimizeRequest(
        depot=d,
        vehicles=vehicles,
        stops=stops,
        objective=payload.get('objective','min_cost'),
        include_tolls=bool(payload.get('include_tolls', True))
    )
@app.get('/vehicles')
@login_required
def vehicles_page():
    return render_template('vehicles.html')
@app.get('/tracking')
@login_required
def tracking():
    return render_template('tracking.html')


@app.post('/optimize')
@login_required
@require_active_subscription
def optimize():
    # 1) Parse
    raw = request.get_json(force=True)
    try:
        req = parse_request(raw)
    except Exception as e:
        return jsonify({"status":"bad_request", "message": str(e)}), 400

    # 2) Matriz ÚNICA (depot + TODAS as paradas) -> depois fatiamos por veículo
    all_points = [req.depot] + req.stops
    try:
        m = rp.travel_matrix(all_points)
    except Exception as e:
        return jsonify({"status": "provider_error", "message": f"Erro no provedor de rotas: {e}"}), 500

    n_all = len(all_points)
    time_m_all = [[0.0]*n_all for _ in range(n_all)]
    dist_m_all = [[0.0]*n_all for _ in range(n_all)]
    for i in range(n_all):
        for j in range(n_all):
            cell = m[(i, j)]
            time_m_all[i][j] = cell['minutes']
            dist_m_all[i][j] = cell['km']

    # 3) Atribuição de paradas a veículos (se o payload tiver "vehicle" em cada stop)
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

    # 4) Função utilitária: fatiar matriz e chamar solver
    def slice_and_solve(stops_subset, vehicles_subset):
        points = [req.depot] + stops_subset
        # índice 0 no all_points é o depósito; as paradas começam em 1
        idx_map = [0] + [1 + req.stops.index(s) for s in stops_subset]

        n = len(points)
        tmat = [[0.0]*n for _ in range(n)]
        dmat = [[0.0]*n for _ in range(n)]
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

    # 5) Rodar por veículo (se houver atribuições) OU global (como antes)
    results = []
    total_time = 0.0
    total_dist = 0.0

    if has_assignment:
        veh_by_id = {v.id: v for v in req.vehicles}
        for vcode, stops_subset in stops_by_vehicle.items():
            v = veh_by_id.get(vcode)
            if not v:
                return jsonify({"status":"bad_request",
                                "message": f"Paradas atribuídas ao veículo '{vcode}', mas ele não existe na lista de veículos."}), 400
            veh_def = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min}]
            r = slice_and_solve(stops_subset, veh_def)
            if r.get('status') != 'ok':
                return jsonify(r), 400
            # Já vem uma única rota para esse 'veh_def'
            for route in r['routes']:
                route['vehicle_id'] = v.id  # garante o id correto
                results.append(route)
                total_time += route['time_min']
                total_dist += route['dist_km']
    else:
        # Solver global (multi-veículos decididos pelo solver)
        vehs = [{"id": v.id, "capacity": v.capacity, "start_min": v.start_min, "end_min": v.end_min} for v in req.vehicles]
        r = slice_and_solve(req.stops, vehs)
        if r.get('status') != 'ok':
            return jsonify(r), 400
        results = r['routes']
        total_time = r['total_time_min']
        total_dist = r['total_dist_km']

    # 6) Pedágios somados sobre 'results'
    toll_total = 0.0
    if req.include_tolls:
        # para pegar coordenadas orig/dest usamos all_points:
        # índice 0 = depósito; paradas = 1..N na ordem de req.stops
        for route in results:
            nodes = route['nodes']  # índices relativos ao subproblema [0..len(subset)]
            # Precisamos reconstruir as coordenadas reais: mais simples é recomputar com req.depot/req.stops
            # No solver, nodes referem-se ao subproblema; aqui, como usamos slice por veículo,
            # consideramos que nodes são [0..len(stops_subset)], então mapeie 0->depot, >0->parada correspondente.
            # Para não complicar, fazemos um fallback pelo parse direto usando os stops do subproblema não guardados.
            # Como não os temos aqui, vamos estimar pelo all_points usando pular: depot seguido dos ids
            # => alternativa simples: usar a matriz all_points e aproximar de acordo com os índices reais:
            # Como isso ficaria complexo, vamos medir pedágio via geocodificação direta origem/dest do par de nós,
            # pegando do req.depot/req.stops com os ids nos route['nodes_human'] se existir.
            # Para manter simples e robusto agora, usamos as lat/lon do depot e das paradas nos índices do ALL:
            # route['nodes'] sempre começam em 0 (depot) e depois param STOPS ORDENADAS; mapeamos pelo req.stops
            # (essa estratégia funciona para nosso solver atual).
            for a, b in zip(nodes[:-1], nodes[1:]):
                if a == 0:
                    origin = req.depot.loc
                else:
                    origin = req.stops[a-1].loc
                if b == 0:
                    dest = req.depot.loc
                else:
                    dest = req.stops[b-1].loc
                try:
                    toll_total += rp.route_cost_with_tolls(origin, dest)
                except Exception:
                    pass

    # 7) Manutenção a partir da telemetria do payload
    telemetry_all = raw.get('telemetry', {})
    maintenance = []
    for v in req.vehicles:
        tel = telemetry_all.get(v.id, {"km_rodados": 20000, "dias_desde_ultima_manutencao": 60, "alertas_obd": 0})
        maintenance.append({"vehicle_id": v.id, "failure_risk": predict_failure_risk(tel)})

    # 8) Mapa (usa 'results')
    ts = int(time.time())
    maps_dir = Path(app.static_folder) / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    map_path = maps_dir / f"route_{ts}.html"
    map_rel = f"/static/maps/route_{ts}.html"
    try:
        build_map([req.depot] + req.stops, results, str(map_path))
    except Exception:
        map_rel = ""

    # 9) Resposta
    return jsonify({
        "status": "ok",
        "routes": results,
        "total_time_min": total_time,
        "total_dist_km": total_dist,
        "total_toll_cost": round(toll_total, 2),
        "maintenance": maintenance,
        "map_url": map_rel
    })

if __name__ == '__main__':
    app.run(debug=True)



