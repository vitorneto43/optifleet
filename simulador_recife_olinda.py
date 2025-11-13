import requests
import time
import math
import random

# =======================
# CONFIGURAÇÕES GERAIS
# =======================

API_URL = "https://www.optifleet.com.br/telemetry"  # ajuste se sua rota for outra, ex: /api/telemetry

IMEI = "359339075123456"
TOKEN = "TK-TEST-859211"

# Intervalo entre envios (segundos)
SEND_INTERVAL = 5

# Velocidade média desejada em km/h (para simulação)
SPEED_KMH = 40.0  # você pode aumentar/diminuir

# =======================
# ROTA REALISTA: RECIFE → OLINDA
# (coordenadas aproximadas, suficientes para teste)
# =======================

WAYPOINTS = [
    # lat, lon  (pontos aproximados só para formar o caminho)
    (-8.0632, -34.8711),  # Recife - região do Marco Zero
    (-8.0500, -34.8700),  # Subindo pela área central
    (-8.0400, -34.8705),  # Próximo Av. Agamenon Magalhães
    (-8.0280, -34.8718),  # Seguindo em direção a Olinda
    (-8.0200, -34.8650),  # Chegando em Olinda
    (-8.0080, -34.8500),  # Olinda - ponto final
]

# Quantos "pontos" teremos entre cada par de WAYPOINTS
POINTS_PER_SEGMENT = 20


# =======================
# FUNÇÕES DE SUPORTE
# =======================

def haversine_km(lat1, lon1, lat2, lon2):
    """Calcula distância em KM entre dois pontos de lat/lon."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def build_route_points(waypoints, points_per_segment):
    """
    Cria uma lista de pontos interpolados entre os WAYPOINTS.
    Cada par de pontos gera vários pontos intermediários,
    formando uma rota suave.
    """
    route = []

    for i in range(len(waypoints) - 1):
        lat1, lon1 = waypoints[i]
        lat2, lon2 = waypoints[i + 1]

        for step in range(points_per_segment):
            t = step / float(points_per_segment)
            lat = lat1 + (lat2 - lat1) * t
            lon = lon1 + (lon2 - lon1) * t
            route.append((lat, lon))

    # Garante o último ponto final
    route.append(waypoints[-1])
    return route


def send_telemetry(lat, lon, speed_kmh, ignition=1):
    payload = {
        "imei": IMEI,
        "token": TOKEN,
        "latitude": lat,
        "longitude": lon,
        "speed": int(speed_kmh),
        "ignition": ignition,
        "timestamp": int(time.time()),
    }

    try:
        r = requests.post(API_URL, json=payload, timeout=10)
        print(
            f"Enviado: lat={lat:.6f}, lon={lon:.6f}, "
            f"speed={speed_kmh:.1f} km/h -> status {r.status_code}"
        )
        print("Resposta:", r.text[:200], "...\n")
    except Exception as e:
        print("Erro ao enviar telemetria:", e)


# =======================
# LOOP PRINCIPAL
# =======================

def main():
    route = build_route_points(WAYPOINTS, POINTS_PER_SEGMENT)
    print(f"Rota gerada com {len(route)} pontos.")
    print("Iniciando simulação Recife → Olinda...\n")

    while True:
        # percorre toda a rota
        for idx, (lat, lon) in enumerate(route):
            # simula pequenas variações de velocidade
            base_speed = SPEED_KMH + random.uniform(-5, 5)
            base_speed = max(0, base_speed)

            # simula paradas rápidas a cada 25 pontos
            if idx % 25 == 0 and idx != 0:
                speed = 0
                ignition = 1
            else:
                speed = base_speed
                ignition = 1

            send_telemetry(lat, lon, speed, ignition)
            time.sleep(SEND_INTERVAL)

        # quando chega ao final da rota, você pode:
        # 1) parar o script (break)
        # 2) ou voltar ao início (loop infinito)
        print("Chegou em Olinda. Recomeçando a rota do início...\n")
        # se quiser parar depois de uma ida, descomente:
        # break


if __name__ == "__main__":
    main()
