# core/billing.py
from dataclasses import dataclass

PLAN_CATALOG = {
    # plano: preço mensal, limite de veículos, desconto anual
    "route": {"label": "Start", "monthly": 399.00,  "max_vehicles": 5,  "annual_discount": 0.15},
    "full":  {"label": "Pro",   "monthly": 1499.00, "max_vehicles": 50, "annual_discount": 0.15},
}

@dataclass
class Quote:
    plan: str
    plan_label: str
    vehicles: int
    max_vehicles: int
    billing: str                       # 'monthly' | 'annual'
    monthly_price: float               # R$ por mês do plano (preço de tabela)
    monthly_equivalent: float          # R$ por mês equivalente (no anual é com 15% OFF)
    total_per_period: float            # valor cobrado no período (mês OU ano à vista)
    description: str                   # texto amigável

def _r2(x: float) -> float:
    return float(f"{x:.2f}")

def quote(plan: str, vehicles: int, billing: str = "monthly") -> Quote:
    plan = (plan or "").lower().strip()
    if plan not in PLAN_CATALOG:
        raise ValueError("Plano inválido. Use 'route' (Start) ou 'full' (Pro).")

    info = PLAN_CATALOG[plan]
    label = info["label"]
    cap   = int(info["max_vehicles"])
    monthly = float(info["monthly"])
    disc = float(info["annual_discount"])

    # normaliza veículos (apenas valida limite)
    try:
        vehicles = int(vehicles or 1)
    except Exception:
        vehicles = 1
    if vehicles < 1:
        vehicles = 1
    if vehicles > cap:
        raise ValueError(f"O plano {label} permite até {cap} veículos.")

    if billing == "annual":
        monthly_equiv = _r2(monthly * (1 - disc))              # “/mês” equivalente
        total = _r2(monthly * 12 * (1 - disc))                 # cobra o ANO à vista
        desc = f"{label} — anual (15% OFF) — até {cap} veículos — equiv. R$ {monthly_equiv:.2f}/mês"
    else:
        monthly_equiv = _r2(monthly)                           # igual ao mensal tabelado
        total = _r2(monthly)                                   # cobra o MÊS
        desc = f"{label} — mensal — até {cap} veículos"

    return Quote(
        plan=plan,
        plan_label=label,
        vehicles=vehicles,
        max_vehicles=cap,
        billing=billing,
        monthly_price=_r2(monthly),
        monthly_equivalent=_r2(monthly_equiv),
        total_per_period=total,
        description=desc,
    )

