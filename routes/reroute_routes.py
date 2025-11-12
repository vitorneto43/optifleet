from flask import Blueprint, request, jsonify, render_template
from core.providers.maps import RoutingProvider
from core.providers.geocoding import geocode_address, GeocodingError, GeocodingAmbiguous
from core.models import Location
from core.visual.map_render import build_map

bp_reroute = Blueprint("reroute", __name__)
rp2 = RoutingProvider()


@bp_reroute.post("/api/reroute/by_address")
def reroute_by_address():
    """
    Body: { "origin_address": "...", "dest_address": "..." }
    Retorna km, minutos, pedágio e coordenadas.
    """
    try:
        p = request.get_json(force=True)

        origin_addr = p.get("origin_address") or ""
        dest_addr = p.get("dest_address") or ""

        if not origin_addr or not dest_addr:
            return jsonify({"error": "Origem e destino são obrigatórios"}), 400

        # 1) Geocodificar
        o_lat, o_lon = geocode_address(origin_addr)
        d_lat, d_lon = geocode_address(dest_addr)

        origin_loc = Location(o_lat, o_lon)
        dest_loc  = Location(d_lat, d_lon)

        # 2) Distância e tempo
        km, minutes = rp2.route_distance_time(origin_loc, dest_loc)

        # 3) Pedágio (tolerante a erro)
        toll_cost = 0.0
        try:
            toll_cost = rp2.route_cost_with_tolls(origin_loc, dest_loc)
        except:
            pass

        return jsonify({
            "origin": {"address": origin_addr, "lat": o_lat, "lon": o_lon},
            "dest":   {"address": dest_addr, "lat": d_lat, "lon": d_lon},
            "km": km,
            "minutes": minutes,
            "toll_cost": toll_cost
        })

    except GeocodingAmbiguous as ge:
        return jsonify({"error": str(ge)}), 400

    except GeocodingError as ge:
        return jsonify({"error": str(ge)}), 400

    except Exception as e:
        print("ERRO reroute_by_address:", repr(e))
        return jsonify({"error": "Erro inesperado"}), 500


@bp_reroute.post("/routes/show")
def show_route():
    """
    Form POST (HTML):
    depot_address=...
    stop_address=...

    Renderiza mapa HTML com origem→destino
    """
    try:
        origin_addr = request.form.get("origin_address")
        dest_addr = request.form.get("dest_address")

        # geocodificar
        o_lat, o_lon = geocode_address(origin_addr)
        d_lat, d_lon = geocode_address(dest_addr)

        # Rota "reta" por enquanto:
        route_coords = [(o_lat, o_lon), (d_lat, d_lon)]

        # Renderizar no mapa
        map_html = build_map(
            paths=[route_coords],
            center=(o_lat, o_lon),
            zoom=5,
            fit_bounds=True
        )

        return render_template(
            "routes/show_route.html",
            map_html=map_html,
            origin=origin_addr,
            dest=dest_addr,
            o_lat=o_lat, o_lon=o_lon,
            d_lat=d_lat, d_lon=d_lon
        )

    except GeocodingError as ge:
        return f"<h2>Erro: {ge}</h2>", 400
