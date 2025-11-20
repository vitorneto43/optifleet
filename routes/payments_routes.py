# routes/payments_routes.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
import os

from mercadopago_client import (
    criar_preferencia_plano,
    criar_assinatura_mensal,
)

bp_payments = Blueprint("payments", __name__, url_prefix="/api/payments")

# Valores em REAIS
PLANOS = {
    "start": 399.0,
    "pro": 1499.0,
    "enterprise": 2200.0,
}

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.optifleet.com.br")


def _preco_plano(plano: str) -> float:
    return PLANOS.get(plano, PLANOS["start"])


@bp_payments.route("/checkout", methods=["POST"])
@login_required
def checkout_plano():
    """
    Pagamento ÚNICO do plano (sem recorrência) via Mercado Pago.
    Usa preference -> init_point (PIX/cartão/boleto).
    """
    data = request.get_json() or {}
    plano = (data.get("plan") or "").lower()

    if plano not in PLANOS:
        return jsonify({"error": "Plano inválido"}), 400

    preco = _preco_plano(plano)
    user_id = getattr(current_user, "id", None)

    if not user_id:
        return jsonify({"error": "Usuário inválido"}), 400

    external_reference = f"user-{user_id}-plan-{plano}"

    back_urls = {
        "success": f"{FRONTEND_URL}/checkout/success?plan={plano}",
        "pending": f"{FRONTEND_URL}/checkout/pending?plan={plano}",
        "failure": f"{FRONTEND_URL}/checkout/failure?plan={plano}",
    }

    try:
        pref = criar_preferencia_plano(
            plano_id=plano,
            descricao=f"OptiFleet plano {plano.upper()}",
            preco=preco,
            external_reference=external_reference,
            back_urls=back_urls,
        )
    except Exception as e:
        return jsonify({"error": "Falha ao criar preferência", "details": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "preference_id": pref.get("id"),
            "init_point": pref.get("init_point"),
            "sandbox_init_point": pref.get("sandbox_init_point"),
        }
    ), 200


@bp_payments.route("/subscribe", methods=["POST"])
@login_required
def subscribe_plano():
    """
    ASSINATURA MENSAL (recorrente).
    Cria um preapproval no Mercado Pago.
    """
    data = request.get_json() or {}
    plano = (data.get("plan") or "").lower()

    if plano not in PLANOS:
        return jsonify({"error": "Plano inválido"}), 400

    preco = _preco_plano(plano)
    user_id = getattr(current_user, "id", None)
    email = getattr(current_user, "email", None)

    if not user_id or not email:
        return jsonify({"error": "Usuário sem id/email"}), 400

    external_reference = f"user-{user_id}-plan-{plano}"

    try:
        preapproval = criar_assinatura_mensal(
            plano_id=plano,
            descricao=f"Assinatura OptiFleet plano {plano.upper()}",
            preco=preco,
            payer_email=email,
            external_reference=external_reference,
        )
    except Exception as e:
        return jsonify({"error": "Falha ao criar assinatura", "details": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "preapproval_id": preapproval.get("id"),
            "init_point": preapproval.get("init_point"),  # redirecionar o usuário
        }
    ), 200
