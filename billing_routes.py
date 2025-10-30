# routes/billing_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
bp_billing = Blueprint("billing", __name__, url_prefix="/billing")

def _fmt_brl(v):
    s = f"R$ {v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

@bp_billing.get("/checkout")
@login_required
def checkout():
    from billing.asaas_routes import _price  # vamos usar a mesma fonte

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
        "enterprise": None,  # NÃO trava
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

@bp_billing.get("/go")
@login_required
def go_checkout():
    plan = (request.args.get("plan") or "pro").lower()
    billing = (request.args.get("billing") or "monthly").lower()
    vehicles = request.args.get("vehicles") or "1"
    return redirect(url_for("asaas.checkout", plan=plan, billing=billing, vehicles=vehicles))


@bp_billing.get("/pricing")
def pricing_page():
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



