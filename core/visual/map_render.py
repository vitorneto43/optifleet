# core/visual/map_render.py
from typing import List, Dict, Any, Callable, Optional, Sequence
import folium
from folium.plugins import PolyLineTextPath

def _latlon(obj):
    if hasattr(obj, "lat") and hasattr(obj, "lon"):
        return float(obj.lat), float(obj.lon)
    if hasattr(obj, "loc"):
        return float(obj.loc.lat), float(obj.loc.lon)
    return float(obj[0]), float(obj[1])

def _decode_polyline(encoded: str) -> List[List[float]]:
    coords = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        result = 1; shift = 0
        while True:  # lat
            b = ord(encoded[index]) - 63 - 1; index += 1
            result += b << shift; shift += 5
            if b < 0x1f: break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        result = 1; shift = 0
        while True:  # lon
            b = ord(encoded[index]) - 63 - 1; index += 1
            result += b << shift; shift += 5
            if b < 0x1f: break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append([lat * 1e-5, lng * 1e-5])
    return coords

def _coerce_path_to_coords(path: Any) -> List[List[float]]:
    """Converte o retorno do fetch_path em [[lat, lon], ...]. Aceita vários formatos."""
    if path is None:
        return []

    # 1) string codificada
    if isinstance(path, str):
        return _decode_polyline(path)

    # 2) lista de strings (cada uma uma polyline)
    if isinstance(path, list) and path and isinstance(path[0], str):
        coords: List[List[float]] = []
        for poly in path:
            part = _decode_polyline(poly)
            if coords and part and coords[-1] == part[0]:
                coords.extend(part[1:])
            else:
                coords.extend(part)
        return coords

    # 3) lista de [lat, lon]
    if isinstance(path, list) and path and isinstance(path[0], (list, tuple)):
        try:
            return [[float(p[0]), float(p[1])] for p in path]
        except Exception:
            # cai para mais tentativas
            pass

    # 4) lista de dicts com lat/lng (ou latitude/longitude)
    if isinstance(path, list) and path and isinstance(path[0], dict):
        coords = []
        for p in path:
            if "lat" in p and ("lng" in p or "lon" in p):
                lon = p.get("lng", p.get("lon"))
                coords.append([float(p["lat"]), float(lon)])
            elif "latitude" in p and "longitude" in p:
                coords.append([float(p["latitude"]), float(p["longitude"])])
        if coords:
            return coords

    # 5) dict com polyline/points
    if isinstance(path, dict):
        # Google Directions (overview)
        try:
            ov = path.get("routes", [{}])[0].get("overview_polyline", {})
            if "points" in ov:
                return _decode_polyline(ov["points"])
        except Exception:
            pass
        # chaves comuns
        for key in ("polyline", "points", "path"):
            if key in path and isinstance(path[key], str):
                return _decode_polyline(path[key])
            if key in path and isinstance(path[key], list):
                return _coerce_path_to_coords(path[key])

    # fallback: nada reconhecido
    return []

def build_map(
    points: List[Any],
    routes: List[Dict[str, Any]],
    out_html: str,
    *,
    fetch_path: Optional[Callable[[Sequence[float], Sequence[float]], Any]] = None,
    vehicles: Optional[List[Dict[str, Any]]] = None
) -> None:
    if not points:
        raise ValueError("Sem pontos para desenhar.")

    c_lat, c_lon = _latlon(points[0])
    m = folium.Map(location=[c_lat, c_lon], zoom_start=12, tiles="cartodbpositron")

    folium.Marker([c_lat, c_lon], popup="Depósito",
                  icon=folium.Icon(color="green", icon="home")).add_to(m)

    for idx, p in enumerate(points[1:], start=1):
        lat, lon = _latlon(p)
        folium.CircleMarker([lat, lon], radius=5, color="#3a86ff",
                            fill=True, fill_opacity=0.95,
                            popup=f"Stop {idx}").add_to(m)

    palette = ["#3a86ff", "#ff006e", "#fb5607", "#8338ec",
               "#ffbe0b", "#06d6a0", "#118ab2", "#ef476f"]

    for r_i, route in enumerate(routes):
        color = palette[r_i % len(palette)]
        nodes = route.get("nodes_abs") or route.get("nodes") or []
        if len(nodes) < 2:
            continue

        full_coords: List[List[float]] = []
        for a, b in zip(nodes[:-1], nodes[1:]):
            o_lat, o_lon = _latlon(points[a])
            d_lat, d_lon = _latlon(points[b])

            leg_coords: List[List[float]] = []
            if fetch_path:
                try:
                    path = fetch_path((o_lat, o_lon), (d_lat, d_lon))
                    leg_coords = _coerce_path_to_coords(path)
                except Exception:
                    leg_coords = []
            if not leg_coords:  # fallback reta
                leg_coords = [[o_lat, o_lon], [d_lat, d_lon]]

            if full_coords and leg_coords and full_coords[-1] == leg_coords[0]:
                full_coords.extend(leg_coords[1:])
            else:
                full_coords.extend(leg_coords)

        if not full_coords:
            continue

        pl = folium.PolyLine(full_coords, weight=5, opacity=0.95, color=color).add_to(m)
        PolyLineTextPath(
            pl, " ▶▶▶ ", repeat=True, offset=8,
            attributes={"fill": color, "font-weight": "bold", "font-size": "16"}
        ).add_to(m)

        v = route.get("vehicle_id", f"V{r_i+1}")
        t = route.get("time_min", "?")
        d = route.get("dist_km", "?")
        folium.Marker(
            full_coords[-1],
            popup=f"Veículo {v} — {t} min / {d} km",
            icon=folium.Icon(color="blue", icon="flag")
        ).add_to(m)

    if vehicles:
        for v in vehicles:
            try:
                if isinstance(v, dict):
                    lat = float(v["lat"]); lon = float(v["lon"]); label = v.get("vehicle_id", "")
                else:
                    label, lat, lon, *_ = v  # tupla (vehicle_id, lat, lon, ...)
                folium.Marker(
                    [lat, lon],
                    popup=f"Posição atual — {label}",
                    icon=folium.Icon(color="orange", icon="truck", prefix="fa")
                ).add_to(m)
            except Exception:
                pass

    m.save(out_html)



