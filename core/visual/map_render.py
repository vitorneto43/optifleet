import folium, time
from typing import List, Dict
from core.providers.maps import RoutingProvider

def build_map(points, routes: List[Dict], out_path: str):
    # centra no depósito
    depot = points[0]
    m = folium.Map(location=[depot.loc.lat, depot.loc.lon], zoom_start=12, control_scale=True)
    # marcadores
    folium.Marker([depot.loc.lat, depot.loc.lon], tooltip="Depósito", icon=folium.Icon(color="green")).add_to(m)
    for i, p in enumerate(points[1:], start=1):
        folium.Marker([p.loc.lat, p.loc.lon], tooltip=f"Stop {i}").add_to(m)

    rp = RoutingProvider()

    # para cada rota de veículo, desenhar as pernas com polyline real da via
    colors = ["blue", "red", "purple", "orange", "darkred", "cadetblue"]
    for ridx, route in enumerate(routes):
        nodes = route["nodes"]
        color = colors[ridx % len(colors)]
        for a, b in zip(nodes[:-1], nodes[1:]):
            origin = points[a].loc
            dest = points[b].loc
            poly = rp.leg_polyline(origin, dest)  # lista de dicts {lat,lng}
            if poly:
                coords = [(pt["lat"], pt["lng"]) for pt in poly]
            else:
                coords = [(origin.lat, origin.lon), (dest.lat, dest.lon)]
            folium.PolyLine(coords, weight=5, opacity=0.8, tooltip=f"{route['vehicle_id']}", color=color).add_to(m)

    m.save(out_path)
    return out_path
