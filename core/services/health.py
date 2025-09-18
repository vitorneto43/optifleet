def maintenance_risk_score(km_rodados: int, dias_desde: int, alertas_obd: int) -> float:
    # 0 a 1 (baixo -> alto). Heurística simples (troque pelo seu modelo)
    a = min(km_rodados / 15000.0, 1.0)
    b = min(dias_desde / 90.0, 1.0)
    c = min(alertas_obd / 5.0, 1.0)
    return round(0.5*a + 0.3*b + 0.2*c, 3)

def risk_color(score: float) -> str:
    if score < 0.33: return "green"
    if score < 0.66: return "yellow"
    return "red"
