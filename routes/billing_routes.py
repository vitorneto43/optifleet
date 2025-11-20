# routes/billing_routes.py
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
)
from flask_login import login_required, current_user

bp_billing = Blueprint("billing", __name__, url_prefix="/billing")


def _fmt_brl(v: float) -> str:
    s = f"R$ {v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _price(plan: str, billing: str, vehicles: int) -> float:
    """
    Calcula o valor do plano com base na tabela interna.
    Aqui você pode ajustar a lógica (por veículo, etc.) depois.
    """
    plan = (plan or "pro").lower()
    billing = (billing or "monthly").lower()

    annual_discount = 0.15

    monthly_base = {
        "start": 399.00,
        "pro": 1499.00,
        "enterprise": 2200.00,
    }

    base = monthly_base.get(plan, monthly_base["pro"])

    if billing in {"annual", "anual", "yearly"}:
        return round(base * 12 * (1 - annual_discount), 2)
    else:
        return base


@bp_billing.get("/checkout")
@login_required
def checkout():
    """
    Tela de resumo do plano (sem iniciar pagamento).
    O pagamento agora é feito via Mercado Pago pela rota /api/payments/subscribe,
    chamada diretamente da página de pricing (pricing.html).
    """
    raw_plan = (request.args.get("plan") or "").strip().lower()

    ALIASES = {
        "start": {"start", "starter", "route", "rota", "routing", "inicial", "basic", "essentials"},
        "pro": {"pro", "pró", "professional", "profissional"},
        "enterprise": {"enterprise", "empresarial", "empresa", "corp", "corporate"},
    }

    plan = None
    for canon, names in ALIASES.items():
        if raw_plan in {n.lower() for n in names}:
            plan = canon
            break
    if plan is None:
        plan = "pro"

    billing = (request.args.get("billing") or "monthly").strip().lower()
    vehicles = int(request.args.get("vehicles") or 5)

    # limites por plano
    MAX_VEHICLES = {
        "start": 5,
        "pro": 50,
        "enterprise": None,  # não trava
    }
    limit = MAX_VEHICLES.get(plan)
    if limit is not None and vehicles > limit:
        vehicles = limit
        flash(f"O plano {plan.capitalize()} permite no máximo {limit} veículos.", "warning")

    price = _price(plan, billing, vehicles)

    return render_template(
        "checkout.html",
        plan=plan,
        plan_label={"start": "Start", "pro": "Pro", "enterprise": "Enterprise"}[plan],
        billing=billing,
        billing_label="Anual (15% OFF)" if billing in {"annual", "anual", "yearly"} else "Mensal",
        vehicles=vehicles,
        price=price,
        monthly_equiv=round(price / 12, 2) if billing in {"annual", "anual", "yearly"} else None,
    )


@bp_billing.get("/pricing")
def pricing_page():
    """
    Página de planos. O template pricing.html já chama
    /api/payments/subscribe via JavaScript (assinarPlano(plan))
    para iniciar a assinatura no Mercado Pago.
    """
    annual_discount = 0.15
    plans = {
        "start": {
            "name": "Start",
            "monthly": 399.00,
            "annual": round(399.00 * 12 * (1 - annual_discount), 2),
            "max_vehicles": 5,
        },
        "pro": {
            "name": "Pro",
            "monthly": 1499.00,
            "annual": round(1499.00 * 12 * (1 - annual_discount), 2),
            "max_vehicles": 50,
        },
        "enterprise": {
            "name": "Enterprise",
            "monthly": 2200.00,
            "annual": round(2200.00 * 12 * (1 - annual_discount), 2),
            "max_vehicles": None,
        },
    }
    return render_template("pricing.html", annual_discount=annual_discount, plans=plans, fmt=_fmt_brl)



