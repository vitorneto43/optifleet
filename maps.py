import time
from typing import Dict, Tuple, List, Optional

from config import (
    GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_LANGUAGE, GOOGLE_MAPS_REGION,
    FALLBACK_AVG_KMH, ALLOW_GEOCODE_OFF
)
from core.utils.geo import haversine_km

try:
    import googlemaps  # só será usado se houver API KEY
except Exception:
    googlemaps = None


class RoutingProvider:
    def __init__(self):
        # Se não houver API KEY e não estiver permitido rodar sem geocode, aborta.
        if not GOOGLE_MAPS_API_KEY and not ALLOW_GEOCODE_OFF:
            raise RuntimeError("Configure GOOGLE_MAPS_API_KEY no .env")

        self.gmaps = None
        if GOOGLE_MAPS_API_KEY and googlemaps is not None:
            self.gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    # ---------------- Geocodificação ----------------
    def geocode(self, address: str) -> Optional[tuple]:
        """
        Retorna (lat, lon) via Google. Se não houver API ou ALLOW_GEOCODE_OFF,
        retorna None (forçando o front a informar lat/lon).
        """
        if not address:
            return None
        if not self.gmaps:
            # sem API → sem geocode
            return None

        res = self.gmaps.geocode(address, language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION)
        if not res:
            return None
        loc = res[0]["geometry"]["location"]
        return (loc["lat"], loc["lng"])
    # ------------------------------------------------

    def _fallback_matrix(self, points: List) -> Dict[Tuple[int, int], Dict]:
        """
        Matriz por Haversine + velocidade média (FALLBACK_AVG_KMH).
        Serve para demo quando não há Google ou quando a célula falha.
        """
        coords = [(getattr(p, 'loc', p).lat, getattr(p, 'loc', p).lon) for p in points]
        n = len(coords)
        out: Dict[Tuple[int, int], Dict] = {}
        for i in range(n):
            for j in range(n):
                km = haversine_km(coords[i], coords[j])
                minutes = (km / max(FALLBACK_AVG_KMH, 1e-6)) * 60.0
                out[(i, j)] = {"minutes": minutes, "km": km}
        return out

    def travel_matrix(self, points: List) -> Dict[Tuple[int, int], Dict]:
        """
        Tenta Distance Matrix com trânsito. Se não houver API, ou falhar,
        cai total ou parcialmente para o fallback Haversine.
        """
        # Sem Google? usa fallback inteiro
        if not self.gmaps:
            return self._fallback_matrix(points)

        origins = [(getattr(p, 'loc', p).lat, getattr(p, 'loc', p).lon) for p in points]
        destinations = origins
        now = int(time.time())

        try:
            matrix = self.gmaps.distance_matrix(
                origins=origins, destinations=destinations,
                mode="driving", language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION,
                departure_time=now
            )
        except Exception:
            # Qualquer erro na chamada → matriz inteira por fallback
            return self._fallback_matrix(points)

        # Monta saída, caindo por célula quando necessário
        out: Dict[Tuple[int, int], Dict] = {}
        fb = self._fallback_matrix(points)
        for i, row in enumerate(matrix.get("rows", [])):
            for j, cell in enumerate(row.get("elements", [])):
                if not cell or cell.get("status") != "OK":
                    out[(i, j)] = fb[(i, j)]
                else:
                    dur = cell.get("duration_in_traffic") or cell.get("duration")
                    minutes = (dur["value"] / 60.0) if dur else fb[(i, j)]["minutes"]
                    km = (cell.get("distance", {}).get("value", 0) / 1000.0) or fb[(i, j)]["km"]
                    out[(i, j)] = {"minutes": minutes, "km": km}
        return out

    def leg_polyline(self, origin, destination):
        """
        Polyline do trajeto. Sem API → retorna lista vazia (o mapa ainda abre,
        só sem a linha de rota).
        """
        if not self.gmaps:
            return []
        try:
            directions = self.gmaps.directions(
                origin=(origin.lat, origin.lon),
                destination=(destination.lat, destination.lon),
                mode="driving", language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION,
                departure_time="now", traffic_model="best_guess"
            )
            if not directions:
                return []
            overview = directions[0].get("overview_polyline", {}).get("points")
            if not overview:
                return []
            return googlemaps.convert.decode_polyline(overview)
        except Exception:
            return []

    def route_cost_with_tolls(self, origin, destination) -> float:
        """
        Tenta pegar 'fare' da rota. Sem API → 0.0.
        """
        if not self.gmaps:
            return 0.0
        try:
            directions = self.gmaps.directions(
                origin=(origin.lat, origin.lon),
                destination=(destination.lat, destination.lon),
                mode="driving", language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION,
                departure_time="now", traffic_model="best_guess"
            )
            if directions and "fare" in directions[0]:
                val = directions[0]["fare"].get("value")
                if isinstance(val, (int, float)):
                    return float(val)
        except Exception:
            pass
        return 0.0


