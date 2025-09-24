# routes/billing_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from math import ceil

bp_billing = Blueprint("billing", __name__, url_prefix="/billing")

# tabela fixa dos planos (preço base no MENSAL)
PLAN_BASE = {
    "start": 399.00,   # R$ 399/mês
    "pro":   1499.00,  # R$ 1499/mês
}
DISCOUNT_ANNUAL = 0.15  # 15%

def _fmt_brl(v):
    # formata 1234.5 -> R$ 1.234,50
    s = f"R$ {v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@bp_billing.get("/checkout")
@login_required
def checkout():
    from billing.asaas_routes import _price  # fonte única de preço

    raw_plan = (request.args.get("plan") or "").strip().lower()
    # aliases de plano
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
        if raw_plan in {"start", "pro", "enterprise"}:
            plan = raw_plan
        else:
            plan = "pro"  # fallback seguro

    billing = (request.args.get("billing") or "monthly").strip().lower()
    vehicles = int(request.args.get("vehicles") or 5)

    # limites por plano (None = ilimitado)
    MAX_VEHICLES = {
        "start": 5,
        "pro": 50,
        "enterprise": None,  # ilimitado
    }
    limit = MAX_VEHICLES.get(plan)

    # ✅ só compara se houver limite
    if limit is not None and vehicles > limit:
        vehicles = limit
        flash(f"O plano {plan.capitalize()} permite no máximo {limit} veículos.", "warning")

    price = _price(plan, billing, vehicles)

    plan_label = {"start": "Start", "pro": "Pro", "enterprise": "Enterprise"}[plan]
    billing_label = "Anual (15% OFF)" if billing in {"annual", "anual", "yearly"} else "Mensal"
    monthly_equiv = round(price / 12.0, 2) if billing in {"annual", "anual", "yearly"} else None

    return render_template(
        "checkout.html",
        plan=plan,
        plan_label=plan_label,
        billing=billing,
        billing_label=billing_label,
        vehicles=vehicles,
        price=price,
        monthly_equiv=monthly_equiv,
    )




@bp_billing.get("/go")
@login_required
def go_checkout():
    plan = (request.args.get("plan") or "pro").lower()
    billing = (request.args.get("billing") or "monthly").lower()
    vehicles = int(request.args.get("vehicles") or 5)
    return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

@bp_billing.get("/pricing")
def pricing_page():
    annual_discount = 0.15
    plans = {
        "start": {"name": "Start", "monthly": 399.00,
                  "annual": round(399.00 * 12 * (1 - annual_discount), 2),
                  "max_vehicles": 50},
        "pro":   {"name": "Pro",   "monthly": 1499.00,
                  "annual": round(1499.00 * 12 * (1 - annual_discount), 2),
                  "max_vehicles": 200},
        "enterprise": {"name": "Enterprise", "monthly": 2200.00,
                  "annual": round(2200.00 * 12 * (1 - annual_discount), 2),
                  "max_vehicles": None},
    }
    return render_template("pricing.html", annual_discount=annual_discount, plans=plans, fmt=_fmt_brl)


