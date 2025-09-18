import time
import googlemaps
from typing import Dict, Tuple, List, Optional
from config import GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_LANGUAGE, GOOGLE_MAPS_REGION, FALLBACK_AVG_KMH
from core.utils.geo import haversine_km

class RoutingProvider:
    def __init__(self):
        if not GOOGLE_MAPS_API_KEY:
            raise RuntimeError("Configure GOOGLE_MAPS_API_KEY no .env")
        self.gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

    # ---------- NOVO: geocodificação ----------
    def geocode(self, address: str) -> Optional[tuple]:
        if not address:
            return None
        res = self.gmaps.geocode(address, language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION)
        if not res:
            return None
        loc = res[0]["geometry"]["location"]
        return (loc["lat"], loc["lng"])
    # ------------------------------------------

    def travel_matrix(self, points: List) -> Dict[Tuple[int,int], Dict]:
        origins = [(getattr(p, 'loc', p).lat, getattr(p, 'loc', p).lon) for p in points]
        destinations = origins
        now = int(time.time())
        matrix = self.gmaps.distance_matrix(
            origins=origins, destinations=destinations,
            mode="driving", language=GOOGLE_MAPS_LANGUAGE, region=GOOGLE_MAPS_REGION,
            departure_time=now
        )
        out = {}
        for i, row in enumerate(matrix["rows"]):
            for j, cell in enumerate(row["elements"]):
                if cell.get("status") != "OK":
                    km = haversine_km(origins[i], destinations[j])
                    minutes = (km / max(FALLBACK_AVG_KMH, 1e-6)) * 60.0
                else:
                    dur = cell.get("duration_in_traffic") or cell.get("duration")
                    minutes = dur["value"]/60.0
                    km = cell["distance"]["value"]/1000.0
                out[(i,j)] = {"minutes": minutes, "km": km}
        return out

    def leg_polyline(self, origin, destination):
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

    def route_cost_with_tolls(self, origin, destination) -> float:
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
        return 0.0


