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
from billing.pagseguro_client import criar_pedido_pix_optifleet
import re

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

def _tax_id_from_form_or_user(source) -> str:
    """
    Pega CPF/CNPJ do form (cpfCnpj) ou do usuário logado,
    e deixa só números.
    """
    cpf_cnpj = (
        (source.get("cpfCnpj") if hasattr(source, "get") else None)
        or getattr(current_user, "cpf_cnpj", "")
        or ""
    )
    return re.sub(r"\D", "", cpf_cnpj)


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


@bp_billing.route("/go", methods=["GET", "POST"])
@login_required
def go_checkout():
    """
    - GET: chamado pela tela de pricing via querystring
      /billing/go?plan=start&billing=monthly&vehicles=5

    - POST: chamado pelo <form method="post" action="{{ url_for('billing.go_checkout') }}">
      presente em checkout.html
    """
    # Usa form se for POST, senão usa querystring
    source = request.form if request.method == "POST" else request.args

    plan = (source.get("plan") or "pro").lower()
    billing = (source.get("billing") or "monthly").lower()
    vehicles_str = source.get("vehicles") or "1"

    try:
        vehicles = int(vehicles_str)
    except ValueError:
        vehicles = 1

    price = _price(plan, billing, vehicles)
    total_centavos = int(price * 100)

    # Monta os dados do cliente pro helper de PIX
    tax_id = _tax_id_from_form_or_user(source)

    customer = {
        "name": getattr(current_user, "name", "") or source.get("name") or "Cliente OptiFleet",
        "email": getattr(current_user, "email", "") or source.get("email") or "contato@optifleet.com.br",
        "tax_id": tax_id,
    }

    reference_id = f"OPT-{current_user.id}-{plan}-{billing}"

    try:
        # Usa o MESMO helper que você já está usando em /api/payments/checkout/pix
        order = criar_pedido_pix_optifleet(reference_id, total_centavos, customer)
        current_app.logger.info("Resposta PagSeguro (PIX via billing/go): %r", order)
    except Exception:
        current_app.logger.exception("Erro ao criar pedido PIX no PagSeguro em /billing/go")
        flash("Ocorreu um erro ao iniciar o pagamento PIX. Tente novamente em instantes.", "danger")
        return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

    # Pega o primeiro QR code retornado
    qr_codes = order.get("qr_codes") or []
    if not qr_codes:
        current_app.logger.error("Nenhum qr_codes retornado pelo PagSeguro em /billing/go: %r", order)
        flash("Não foi possível gerar o QR Code do PIX. Tente novamente mais tarde.", "danger")
        return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

    qr = qr_codes[0]
    links = qr.get("links") or []

    # NORMALMENTE o PagBank manda um link de imagem PNG do QR Code
    qr_png = None
    for link in links:
        if link.get("media") in ("image/png", "image/jpeg", "image/jpg"):
            qr_png = link.get("href")
            break

    # Fallback: se não achar, pega o primeiro link mesmo
    if not qr_png and links:
        qr_png = links[0].get("href")

    if not qr_png:
        current_app.logger.error("Nenhum link para QR Code encontrado em /billing/go: %r", order)
        flash("Não foi possível obter a imagem do QR Code PIX.", "danger")
        return redirect(url_for("billing.checkout", plan=plan, billing=billing, vehicles=vehicles))

    # 👉 Aqui, pra ficar simples e rodando AGORA, vou te redirecionar direto pro PNG do QR.
    # O cliente vai ver a imagem do QR e pode escanear pelo app do banco.
    # Depois, se você quiser, criamos uma página bonita 'pix_checkout.html' mostrando
    # o QR na tela com botão de copiar código.
    return redirect(qr_png)


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






