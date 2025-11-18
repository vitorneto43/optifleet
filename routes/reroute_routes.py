from flask import Blueprint, request, jsonify, render_template
from core.providers.maps import RoutingProvider
from core.providers.geocoding import (
    geocode_address,
    GeocodingError,
    GeocodingAmbiguous,
)
from core.models import Location
from core.visual.map_render import build_map

bp_reroute = Blueprint("reroute", __name__)
rp2 = RoutingProvider()


@bp_reroute.post("/api/reroute/by_address")
def reroute_by_address():
    """
    Body (JSON):
    {
      "origin_address": "Rua tal, 123, Bairro, Recife - PE",
      "dest_address":   "Av tal, 999, Bairro, São Paulo - SP"
    }

    Retorna:
    {
      "origin": { "address": ..., "lat": ..., "lon": ... },
      "dest":   { "address": ..., "lat": ..., "lon": ... },
      "km": ...,
      "minutes": ...,
      "toll_cost": ...
    }
    """
    try:
        p = request.get_json(force=True) or {}

        origin_addr = (p.get("origin_address") or "").strip()
        dest_addr   = (p.get("dest_address") or "").strip()

        if not origin_addr or not dest_addr:
            return jsonify({"error": "Origem e destino são obrigatórios."}), 400

        # 1) Geocodificar
        o_lat, o_lon = geocode_address(origin_addr)
        d_lat, d_lon = geocode_address(dest_addr)

        origin_loc = Location(o_lat, o_lon)
        dest_loc   = Location(d_lat, d_lon)

        # 2) Distância e tempo
        km, minutes = rp2.route_distance_time(origin_loc, dest_loc)

        # 3) Pedágio (tolerante a erro)
        toll_cost = 0.0
        try:
            toll_cost = rp2.route_cost_with_tolls(origin_loc, dest_loc)
        except Exception:
            pass

        return jsonify({
            "origin": {"address": origin_addr, "lat": o_lat, "lon": o_lon},
            "dest":   {"address": dest_addr, "lat": d_lat, "lon": d_lon},
            "km": km,
            "minutes": minutes,
            "toll_cost": toll_cost,
        })

    except GeocodingAmbiguous as ge:
        return jsonify({"error": str(ge)}), 400

    except GeocodingError as ge:
        return jsonify({"error": str(ge)}), 400

    except Exception as e:
        print("ERRO reroute_by_address:", repr(e))
        return jsonify({"error": "Erro inesperado ao calcular rota."}), 500


@bp_reroute.post("/routes/show")
def show_route():
    """
    Form POST (HTML):
      origin_address=...
      dest_address=...

    Renderiza mapa HTML com origem→destino usando uma linha simples.
    (Certifique-se de ter templates/routes/show_route.html configurado.)
    """
    try:
        origin_addr = (request.form.get("origin_address") or "").strip()
        dest_addr   = (request.form.get("dest_address") or "").strip()

        if not origin_addr or not dest_addr:
            return "<h2>Origem e destino são obrigatórios.</h2>", 400

        # geocodificar
        o_lat, o_lon = geocode_address(origin_addr)
        d_lat, d_lon = geocode_address(dest_addr)

        # Rota "reta" por enquanto
        route_coords = [(o_lat, o_lon), (d_lat, d_lon)]

        # Renderizar no mapa
        map_html = build_map(
            paths=[route_coords],
            center=(o_lat, o_lon),
            zoom=5,
            fit_bounds=True,
        )

        return render_template(
            "routes/show_route.html",
            map_html=map_html,
            origin=origin_addr,
            dest=dest_addr,
            o_lat=o_lat, o_lon=o_lon,
            d_lat=d_lat, d_lon=d_lon,
        )

    except GeocodingError as ge:
        return f"<h2>Erro de geocodificação: {ge}</h2>", 400

    except Exception as e:
        print("ERRO show_route:", repr(e))
        return "<h2>Erro inesperado ao renderizar a rota.</h2>", 500


def _coerce_point(raw: dict, role: str):
    """
    Converte um dict vindo do front em (lat, lon, address) robusto.
    Se não vier lat/lon, mas vier endereço, geocodifica.

    role: 'Depósito' ou 'Parada X' (só para mensagem de erro).
    """
    address = (raw.get("address") or raw.get("endereco") or "").strip()
    lat_raw = raw.get("lat")
    lon_raw = raw.get("lon")

    # Se já vierem lat/lon válidos, usa direto
    if lat_raw not in (None, "") and lon_raw not in (None, ""):
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
            return lat, lon, address
        except ValueError:
            # Se não der pra converter, cai para geocodificação
            pass

    # Aqui: não tem lat/lon usáveis, tentar geocodificar
    if not address:
        raise ValueError(f"{role} sem lat/lon e sem endereço (não dá pra localizar).")

    lat, lon = geocode_address(address)
    return lat, lon, address
