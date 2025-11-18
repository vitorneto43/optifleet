# routes/billing_routes.py
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from flask_login import login_required, current_user

# 👉 Ajuste o import conforme onde está seu cliente PagSeguro
# se estiver em core.billing.pagseguro_client, troque a linha abaixo:
from billing.pagseguro_client import PagSeguroClient

bp_billing = Blueprint("billing", __name__, url_prefix="/billing")
pagseguro = PagSeguroClient()


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


@bp_billing.get("/go")
@login_required
def go_checkout():
    """
    Chamada pela tela de pricing:
    /billing/go?plan=start&billing=monthly&vehicles=5

    Aqui criamos a cobrança no PagSeguro e redirecionamos
    o usuário diretamente para o link de pagamento.
    """
    plan = (request.args.get("plan") or "pro").lower()
    billing = (request.args.get("billing") or "monthly").lower()
    vehicles_str = request.args.get("vehicles") or "1"

    try:
        vehicles = int(vehicles_str)
    except ValueError:
        vehicles = 1

    price = _price(plan, billing, vehicles)

    # Monta payload para PagSeguro
    # ⚠ Se no seu PagSeguro for `create_checkout`, troque o nome do método mais embaixo
    payload = {
        "reference_id": f"OPT-{current_user.id}-{plan}-{billing}",
        "description": f"Plano {plan.upper()} ({billing}) - OptiFleet",
        "amount": {
            "value": int(price * 100),  # em centavos
            "currency": "BRL",
        },
        "payment_method": {
            "type": "PIX",  # depois você pode mudar para CREDIT_CARD se quiser
        },
        "notification_urls": [
            "https://www.optifleet.com.br/api/payments/pagseguro/webhook"
        ],
        "customer": {
            "name": getattr(current_user, "name", "") or "Cliente OptiFleet",
            "email": getattr(current_user, "email", "") or "contato@optifleet.com.br",
        },
    }

    try:
        # 👉 Se no seu cliente o método for create_checkout, troque aqui:
        res = pagseguro.create_charge(payload)
        current_app.logger.info("Resposta PagSeguro (billing/go): %r", res)
    except Exception:
        current_app.logger.exception("Erro ao criar cobrança no PagSeguro em /billing/go")
        flash("Ocorreu um erro ao iniciar o pagamento. Tente novamente em instantes.", "danger")
        return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

    pay_url = None
    for link in res.get("links", []):
        if link.get("rel") in ("PAY", "PAYMENT_LINK"):
            pay_url = link.get("href")
            break

    if not pay_url:
        current_app.logger.error(
            "Nenhum link de pagamento encontrado na resposta do PagSeguro: %r", res
        )
        flash("Não foi possível obter o link de pagamento. Tente novamente mais tarde.", "danger")
        return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

    # Redireciona o usuário para o PagSeguro
    return redirect(pay_url)


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



