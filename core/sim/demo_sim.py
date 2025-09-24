# core/sim/demo_sim.py
import threading, time, math, random
from datetime import datetime, timezone
from core.db import insert_telemetry

class DemoSim:
    def __init__(self):
        self._lock = threading.Lock()
        self._t = None
        self._running = False
        self._clients = {}  # client_id -> dict(vehicle_id -> state)

    def start(self, client_id: int):
        with self._lock:
            if client_id not in self._clients:
                self._clients[client_id] = self._bootstrap(client_id)
            if not self._running:
                self._running = True
                self._t = threading.Thread(target=self._loop, daemon=True)
                self._t.start()

    def stop(self, client_id: int):
        with self._lock:
            self._clients.pop(client_id, None)
            if not self._clients:
                self._running = False

    def status(self, client_id: int):
        with self._lock:
            return {
                "running": self._running and client_id in self._clients,
                "vehicles": list(self._clients.get(client_id, {}).keys())
            }

    def _bootstrap(self, client_id: int):
        # Recife centro aproximado
        base = (-8.05, -34.9)
        def mk(v, r):
            ang = random.random()*math.tau
            return {"vehicle_id": v, "lat": base[0]+r*math.cos(ang), "lon": base[1]+r*math.sin(ang),
                    "speed": 35+random.random()*25, "heading": random.random()*360}
        return {
            "V1": mk("V1", 0.05),
            "V2": mk("V2", 0.06),
            "V3": mk("V3", 0.07),
        }

    def _step(self, s):
        # integra ~1s: move ~ (speed_kmh / 3.6) m/s ~ delta em graus simples
        speed = s["speed"]   # km/h
        heading = s["heading"] + random.uniform(-8, 8)  # aleatoriza
        s["heading"] = heading % 360
        # deslocamento em km por segundo
        km_per_sec = speed / 3600.0
        # aproximação grosseira: 1 deg lat ~ 111km; 1 deg lon ~ 111km*cos(lat)
        dlat = (km_per_sec / 111.0) * math.cos(math.radians(heading))
        dlon = (km_per_sec / (111.0*math.cos(math.radians(s["lat"]) or 0.0001))) * math.sin(math.radians(heading))
        s["lat"] += dlat
        s["lon"] += dlon
        # pequenos eventos de overspeed
        if random.random() < 0.02:
            s["speed"] = min(110, s["speed"] + random.uniform(10, 25))
        else:
            s["speed"] = max(20, s["speed"] + random.uniform(-8, 8))

    def _loop(self):
        while True:
            with self._lock:
                if not self._running:
                    break
                snapshot = {cid: {k: dict(v) for k, v in d.items()} for cid, d in self._clients.items()}
            # grava no DB
            now = datetime.now(timezone.utc)
            for cid, vd in snapshot.items():
                for vid, state in vd.items():
                    self._step(state)
                    try:
                        insert_telemetry(cid, vid, now, state["lat"], state["lon"], state["speed"], fuel=50.0)
                    except Exception:
                        pass
                    # salva de volta
                    with self._lock:
                        if cid in self._clients and vid in self._clients[cid]:
                            self._clients[cid][vid].update(state)
            time.sleep(1.0)

sim = DemoSim()
