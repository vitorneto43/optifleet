from flask import Blueprint, request, jsonify
from core.providers.maps import RoutingProvider
from core.models import Location
rp2 = RoutingProvider()

bp_reroute = Blueprint("reroute", __name__)

@bp_reroute.post("/api/reroute")
def reroute():
    """
    Body: { "origin": {"lat":..., "lon":...}, "dest": {"lat":..., "lon":...}, "policy": {...} }
    Retorna alternativa com tempo/distância atualizados (ex.: trânsito)
    """
    p = request.get_json(force=True)
    o = p["origin"]; d = p["dest"]
    include_tolls = p.get("policy", {}).get("include_tolls", True)
    km, minutes = rp2.route_distance_time(Location(o["lat"], o["lon"]), Location(d["lat"], d["lon"]))
    cost_toll = 0.0
    if include_tolls:
        try: cost_toll = rp2.route_cost_with_tolls(Location(o["lat"], o["lon"]), Location(d["lat"], d["lon"]))
        except: pass
    return jsonify({"km": km, "minutes": minutes, "toll_cost": cost_toll})
