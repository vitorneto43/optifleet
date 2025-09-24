
# depois
from core.db import get_conn
import datetime

def salvar_telemetria(client_id, vehicle_id, lat, lon, speed, fuel):
    duck_con.execute("""
        INSERT INTO telemetry (client_id, vehicle_id, timestamp, lat, lon, speed, fuel)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [client_id, vehicle_id, datetime.datetime.utcnow(), lat, lon, speed, fuel])


def obter_posicoes(client_id):
    rows = duck_con.execute(f"""
        SELECT vehicle_id, lat, lon, speed, fuel, max(timestamp) as ts
        FROM telemetry
        WHERE client_id = '{client_id}'
        GROUP BY vehicle_id, lat, lon, speed, fuel
    """).fetchall()
    return rows

