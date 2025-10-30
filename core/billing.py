# core/billing.py
from dataclasses import dataclass

# catálogo central de planos
PLAN_CATALOG = {
    # plano: preço mensal, limite de veículos, desconto anual
    # (route = Start)
    "route": {
        "label": "Start",
        "monthly": 399.00,
        "max_vehicles": 5,
        "annual_discount": 0.15,
        "min_vehicles": 1,
    },
    # (full = Pro)
    "full": {
        "label": "Pro",
        "monthly": 1499.00,
        "max_vehicles": 50,
        "annual_discount": 0.15,
        "min_vehicles": 1,
    },
    # novo plano: enterprise
    "enterprise": {
        "label": "Enterprise",
        "monthly": 2200.00,
        # aqui não temos limite real, mas vamos usar um número alto
        "max_vehicles": 999_999,
        "annual_discount": 0.15,
        # enterprise começa em 51 veículos
        "min_vehicles": 51,
    },
}


@dataclass
class Quote:
    plan: str
    plan_label: str
    vehicles: int
    max_vehicles: int
    billing: str                       # 'monthly' | 'annual'
    monthly_price: float               # preço de tabela do plano
    monthly_equivalent: float          # se anual, preço equivalente /mês
    total_per_period: float            # o que vai ser cobrado no período (mês ou ano)
    description: str                   # texto amigável


def _r2(x: float) -> float:
    return float(f"{x:.2f}")


def quote(plan: str, vehicles: int, billing: str = "monthly") -> Quote:
    """
    Retorna uma cotação coerente para o plano informado.
    Aceita: 'route', 'full', 'enterprise'.
    - route: 1..5
    - full:  1..50
    - enterprise: 51..infinito (aqui usamos 999_999)
    """
    plan = (plan or "").lower().strip()

    # aliases pra evitar erro vindo da UI
    ALIASES = {
        "start": "route",
        "starter": "route",
        "pro": "full",
        "professional": "full",
        "profissional": "full",
        "empresa": "enterprise",
        "empresarial": "enterprise",
        "corp": "enterprise",
    }
    if plan in ALIASES:
        plan = ALIASES[plan]

    if plan not in PLAN_CATALOG:
        raise ValueError("Plano inválido. Use 'route', 'full' ou 'enterprise'.")

    info = PLAN_CATALOG[plan]
    label = info["label"]
    cap = int(info["max_vehicles"])
    min_v = int(info.get("min_vehicles", 1))
    monthly = float(info["monthly"])
    disc = float(info["annual_discount"])

    # normaliza veículos
    try:
        vehicles = int(vehicles or 1)
    except Exception:
        vehicles = 1

    # aplica mínimo por plano
    if vehicles < min_v:
        vehicles = min_v

    # aplica máximo só se não for enterprise “ilimitado”
    if vehicles > cap:
        # para route e full a gente trava, para enterprise cap é gigante
        vehicles = cap

    billing = (billing or "monthly").lower().strip()
    billing = billing.replace("á", "a").replace("ã", "a")

    if billing in ("annual", "anual", "yearly"):
        monthly_equiv = _r2(monthly * (1 - disc))
        total = _r2(monthly * 12 * (1 - disc))
        desc = (
            f"{label} — anual (15% OFF) — "
            f"{'a partir de ' if min_v > 1 else ''}{min_v} veículos — "
            f"equiv. R$ {monthly_equiv:.2f}/mês"
        )
        billing = "annual"
    else:
        monthly_equiv = _r2(monthly)
        total = _r2(monthly)
        desc = (
            f"{label} — mensal — "
            f"{'a partir de ' if min_v > 1 else ''}{min_v} veículos"
        )
        billing = "monthly"

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

