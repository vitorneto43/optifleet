from typing import Dict

def predict_failure_risk(telemetry: Dict) -> float:
    km = float(telemetry.get("km_rodados", 0))
    days = float(telemetry.get("dias_desde_ultima_manutencao", 0))
    alerts = float(telemetry.get("alertas_obd", 0))
    # normalizações simples
    km_score = min(km/40000.0, 1.0)      # 40k km ciclo
    days_score = min(days/180.0, 1.0)    # 6 meses
    alert_score = min(alerts/5.0, 1.0)   # 5 alertas
    score = 0.6*km_score + 0.3*days_score + 0.1*alert_score
    return round(max(0.0, min(1.0, score)), 3)

