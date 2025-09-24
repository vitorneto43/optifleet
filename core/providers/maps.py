import os
import requests

class RoutingProvider:
    def __init__(self):
        self._maps_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

    def geocode(self, address: str):
        """
        Geocodifica um endereço usando a API do Google Maps.
        Retorna (lat, lon) ou None se falhar.
        """
        if not self._maps_key:
            print("[GEOCODE] Falhou: GOOGLE_MAPS_API_KEY não configurada.")
            return None
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {"address": address, "key": self._maps_key}
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "OK" and data["results"]:
                loc = data["results"][0]["geometry"]["location"]
                return (loc["lat"], loc["lng"])
            else:
                print("[GEOCODE] Falha:", data.get("status"), data.get("error_message"))
                return None
        except Exception as e:
            print("[GEOCODE] Exception:", e)
            return None

    def travel_matrix(self, points):
        """
        Recebe uma lista de objetos com .loc.lat e .loc.lon
        e retorna um dicionário {(i,j): {"minutes": x, "km": y}}.
        """
        coords = [(p.loc.lat, p.loc.lon) for p in points]
        n = len(coords)
        result = {}

        if self._maps_key:
            try:
                origins = "|".join(f"{lat},{lon}" for lat, lon in coords)
                destinations = origins
                url = "https://maps.googleapis.com/maps/api/distancematrix/json"
                params = {
                    "origins": origins,
                    "destinations": destinations,
                    "key": self._maps_key,
                    "language": "pt-BR",
                    "region": "br",
                }
                r = requests.get(url, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
                if data.get("status") == "OK":
                    rows = data["rows"]
                    for i in range(n):
                        for j in range(n):
                            elem = rows[i]["elements"][j]
                            if elem.get("status") == "OK":
                                dist_m = elem["distance"]["value"] / 1000.0
                                dur_min = elem["duration"]["value"] / 60.0
                            else:
                                dist_m, dur_min = 9999.0, 9999.0
                            result[(i, j)] = {"km": dist_m, "minutes": dur_min}
                    return result
                else:
                    print("[MATRIX][GOOGLE] Falha:", data.get("status"), data.get("error_message"))
            except Exception as e:
                print("[MATRIX][GOOGLE] Exception:", e)

        # fallback fake se não tiver key ou der erro
        for i in range(n):
            for j in range(n):
                if i == j:
                    result[(i, j)] = {"km": 0.0, "minutes": 0.0}
                else:
                    result[(i, j)] = {"km": 5.0, "minutes": 10.0}
        return result

    def leg_polyline(self, origin, dest):
        """
        Retorna uma polyline codificada entre dois pontos usando Directions API.
        """
        if not self._maps_key:
            return None
        try:
            url = "https://maps.googleapis.com/maps/api/directions/json"
            params = {
                "origin": f"{origin.lat},{origin.lon}",
                "destination": f"{dest.lat},{dest.lon}",
                "key": self._maps_key,
            }
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "OK":
                return data["routes"][0]["overview_polyline"]["points"]
            else:
                print("[DIRECTIONS] Falha:", data.get("status"), data.get("error_message"))
                return None
        except Exception as e:
            print("[DIRECTIONS] Exception:", e)
            return None


