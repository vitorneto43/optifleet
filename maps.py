import time
from typing import Dict, Tuple, List, Optional
from config import (
    GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_LANGUAGE, GOOGLE_MAPS_REGION,
    FALLBACK_AVG_KMH, ALLOW_GEOCODE_OFF
)
from core.utils.geo import haversine_km

try:
    import googlemaps
except Exception:
    googlemaps = None


class RoutingProvider:
    def __init__(self):
        # se não houver chave e não estiver liberado rodar sem geocode → trava
        if not GOOGLE_MAPS_API_KEY and not ALLOW_GEOCODE_OFF:
            raise RuntimeError("Configure GOOGLE_MAPS_API_KEY no .env")
        self.gmaps = None
        if GOOGLE_MAPS_API_KEY and googlemaps is not None:
            self.gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    def geocode(self, address: str) -> Optional[tuple]:
        # sem chave → não geocodifica (front deve enviar lat/lon)
        if not address or not self.gmaps:
            return None
        res = self.gmaps.geocode(address, language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION)
        if not res:
            return None
        loc = res[0]["geometry"]["location"]
        return (loc["lat"], loc["lng"])

    def _fallback_matrix(self, points: List) -> Dict[Tuple[int,int], Dict]:
        coords = [(getattr(p, 'loc', p).lat, getattr(p, 'loc', p).lon) for p in points]
        n = len(coords)
        out = {}
        for i in range(n):
            for j in range(n):
                km = haversine_km(coords[i], coords[j])
                minutes = (km / max(FALLBACK_AVG_KMH, 1e-6)) * 60.0
                out[(i,j)] = {"minutes": minutes, "km": km}
        return out

    def travel_matrix(self, points: List) -> Dict[Tuple[int,int], Dict]:
        if not self.gmaps:
            return self._fallback_matrix(points)

        origins = [(getattr(p, 'loc', p).lat, getattr(p, 'loc', p).lon) for p in points]
        now = int(time.time())
        try:
            matrix = self.gmaps.distance_matrix(
                origins=origins, destinations=origins,
                mode="driving", language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION,
                departure_time=now
            )
        except Exception:
            return self._fallback_matrix(points)

        out = {}
        fb = self._fallback_matrix(points)
        for i, row in enumerate(matrix.get("rows", [])):
            for j, cell in enumerate(row.get("elements", [])):
                if not cell or cell.get("status") != "OK":
                    out[(i,j)] = fb[(i,j)]
                else:
                    dur = cell.get("duration_in_traffic") or cell.get("duration")
                    minutes = (dur["value"]/60.0) if dur else fb[(i,j)]["minutes"]
                    km = (cell.get("distance", {}).get("value", 0)/1000.0) or fb[(i,j)]["km"]
                    out[(i,j)] = {"minutes": minutes, "km": km}
        return out

    def leg_polyline(self, origin, destination):
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



