# routes/checkout_routes.py
from flask import Blueprint, request, redirect, url_for, render_template, abort
from flask_login import current_user, login_required
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from core.db import create_subscription, mark_subscription_status

bp_checkout = Blueprint("checkout", __name__, url_prefix="/billing")

# -----------------------------
# Helpers de preço
# -----------------------------
def _period_end(billing: str):
    now = datetime.now(timezone.utc)
    return now + (timedelta(days=365) if billing == "annual" else timedelta(days=30))

def _validate(plan: str, billing: str, vehicles: int) -> bool:
    return plan in {"route", "full"} and billing in {"monthly", "annual"} and vehicles > 0

def _quote(plan: str, billing: str, vehicles: int):
    """
    Mantém consistência com a landing:
    - Start (route): R$ 399/mês (mensal) | R$ 339,15/mês (anual) — até 5 veículos
    - Pro   (full) : R$ 1499/mês (mensal) | R$ 1274,15/mês (anual) — até 50 veículos
    """
    if plan == "route":
        base = 399.00
        limit = 5
    else:
        base = 1499.00
        limit = 50

    # trava veículos no limite do plano (evita inconsistência)
    vehicles = max(1, min(vehicles, limit))

    if billing == "annual":
        monthly_effective = round(base * 0.85, 2)  # -15%
        total_per_period = monthly_effective * 12
        period_label = "12 meses (anual)"
    else:
        monthly_effective = base
        total_per_period = base
        period_label = "1 mês (mensal)"

    return {
        "plan": plan,
        "billing": billing,
        "vehicles": vehicles,
        "limit": limit,
        "monthly_effective": monthly_effective,
        "total_per_period": round(total_per_period, 2),
        "period_label": period_label,
        "plan_label": "Start" if plan == "route" else "Pro"
    }

# -----------------------------
# Fluxo
# -----------------------------
@bp_checkout.get("/billing/go")
@login_required
def start_checkout_get():
    """
    Entrada do fluxo de compra.
    - Se não logado: envia ao /login com next preservando a query.
    - Se logado: redireciona para a página de CHECKOUT (GET /billing/checkout)
    """
    plan    = (request.args.get("plan") or "").strip().lower()
    billing = (request.args.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.args.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    if not current_user.is_authenticated:
        qs = urlencode({"plan": plan, "billing": billing, "vehicles": vehicles})
        return redirect(url_for("auth.login_page", next=f"/billing/go?{qs}"))

    # logado: manda para a página de checkout (exibe preços e botão Confirmar)
    qs = urlencode({"plan": plan, "billing": billing, "vehicles": vehicles})
    return redirect(url_for("checkout.view_checkout") + f"?{qs}")

@bp_checkout.get("/checkout")
@login_required
def view_checkout():
    """
    Mostra o resumo do pedido + valores + ação de confirmar.
    """
    plan    = (request.args.get("plan") or "").strip().lower()
    billing = (request.args.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.args.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    q = _quote(plan, billing, vehicles)
    return render_template("checkout.html", quote=q)

@bp_checkout.post("/checkout/confirm")
@login_required
def confirm_checkout():
    """
    Ao confirmar no checkout:
    - (Produção) aqui você chamaria o provedor (Asaas) para abrir o pagamento hospedado.
    - (Agora / MOCK) ativa imediatamente e redireciona para sucesso.
    """
    plan    = (request.form.get("plan") or "").strip().lower()
    billing = (request.form.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.form.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    q = _quote(plan, billing, vehicles)

    # MOCK: cria como pendente e já marca como ativa (ou mantenha 'pending' até webhook, se quiser simular melhor).
    sub_id = create_subscription(
        user_id=int(current_user.get_id()),
        plan=plan,
        billing=billing,
        vehicles=q["vehicles"],
        status="active",                 # se quiser simular pagamento futuro, troque para 'pending'
        provider="mock",
        provider_ref=f"mock-{plan}",
        started_at=datetime.now(timezone.utc),
        current_period_end=_period_end(billing)
    )
    # Garante status (ex: se decidir usar 'pending' acima, mude aqui quando “pago”)
    mark_subscription_status(sub_id, "active")

    return redirect(url_for("checkout.return_success", sid=sub_id))

@bp_checkout.get("/return/success")
@login_required
def return_success():
    # Renderize uma página própria se preferir
    return redirect(url_for("home", plan="ok"))

@bp_checkout.get("/return/fail")
@login_required
def return_fail():
    return redirect(url_for("home", plan="fail"))




