def pick(*keys, src: dict = None, default=None):
    for k in keys:
        if k in src and src[k] is not None:
            return src[k]
    return default

def to_float(x, default=0.0):
    try: return float(x)
    except: return default

def normalize_generic(p: dict) -> dict:
    tracker = pick("deviceId","device_id","imei","trackerId", src=p, default=None)
    lat = to_float(pick("lat","latitude","Lat", src=p))
    lon = to_float(pick("lon","lng","longitude","Lon", src=p))
    sp  = to_float(pick("speed","speedKmh","speed_kmh","velocity", src=p), 0.0)
    fuel = to_float(pick("fuel","fuel_pct", src=p), 0.0)
    engt = to_float(pick("engine_temp","temp","engineTemp", src=p), 0.0)
    odo  = to_float(pick("odometer","odo","odometer_km", src=p), 0.0)
    obd  = int(pick("obd_alerts","alerts","dtc","obd", src=p, default=0) or 0)
    return {
        "tracker_id": str(tracker) if tracker is not None else None,
        "lat": lat, "lon": lon, "speed_kmh": sp,
        "fuel_pct": fuel, "engine_temp": engt, "odometer_km": odo, "obd_alerts": obd
    }

# stubs para provedores específicos (adicione mapeamentos se tiver o contrato)
def normalize_gt06_gateway(p: dict) -> dict:
    # se você tiver um gateway que já converte GT06→JSON
    return normalize_generic(p)

def normalize_sascar(p: dict) -> dict:
    return normalize_generic(p)

def normalize_cobli(p: dict) -> dict:
    return normalize_generic(p)

ADAPTERS = {
    "generic": normalize_generic,
    "gt06": normalize_gt06_gateway,
    "sascar": normalize_sascar,
    "cobli": normalize_cobli,
}
