# routes/payments_routes.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
import uuid
import re

from billing.pagseguro_client import (
    criar_pedido_pix_optifleet,
    criar_pedido_cartao_optifleet,
    criar_pedido_boleto_optifleet,
)

# Se você já tiver essa função em outro módulo, importe daqui:
# from core.db import ativar_plano_usuario

bp_payments = Blueprint("payments", __name__, url_prefix="/api/payments")

# Valores em CENTAVOS
PLANOS = {
    "start": 39900,        # R$ 399,00
    "pro": 149900,         # R$ 1.499,00
    "enterprise": 220000,  # R$ 2.200,00
}


def _preco_plano(plano: str) -> int:
    """Retorna o valor em centavos do plano informado."""
    return PLANOS.get(plano, PLANOS["start"])


def _tax_id_from_user() -> str:
    """Garante que o CPF/CNPJ vai só com números."""
    cpf_cnpj = getattr(current_user, "cpf_cnpj", "") or ""
    return re.sub(r"\D", "", cpf_cnpj)


# ======================================================
# =======================  PIX  ========================
# ======================================================

@bp_payments.post("/checkout/pix")
@login_required
def checkout_pix():
    data = request.get_json() or {}
    plano = data.get("plano", "start")
    total = _preco_plano(plano)

    reference_id = f"optifleet-{current_user.id}-{uuid.uuid4().hex[:8]}"

    customer = {
        "name": current_user.name,
        "email": current_user.email,
        "tax_id": _tax_id_from_user(),  # CPF ou CNPJ só com números
    }

    order = criar_pedido_pix_optifleet(reference_id, total, customer)

    qr_codes = order.get("qr_codes", [])
    qr = qr_codes[0] if qr_codes else {}

    return jsonify({
        "order_id": order.get("id"),
        "reference_id": order.get("reference_id"),
        "qr_code_id": qr.get("id"),
        "qr_code_text": qr.get("text"),  # texto "copia e cola"
        "qr_code_png": (
            qr.get("links", [])[0].get("href")
            if qr.get("links") else None
        ),
        "expiration_date": qr.get("expiration_date"),
        "status": order.get("status"),
        "plano": plano,
        "valor_centavos": total,
    })


# ======================================================
# ====================  CARTÃO  =======================
# ======================================================

@bp_payments.post("/checkout/card")
@login_required
def checkout_card():
    data = request.get_json() or {}
    plano = data.get("plano", "start")
    total = _preco_plano(plano)

    encrypted_card = data.get("encrypted_card")
    security_code = data.get("security_code")
    installments = int(data.get("installments") or 1)

    if not encrypted_card or not security_code:
        return jsonify({
            "error": "encrypted_card e security_code são obrigatórios."
        }), 400

    reference_id = f"optifleet-{current_user.id}-{uuid.uuid4().hex[:8]}"

    customer = {
        "name": current_user.name,
        "email": current_user.email,
        "tax_id": _tax_id_from_user(),
    }

    order = criar_pedido_cartao_optifleet(
        reference_id=reference_id,
        total_centavos=total,
        customer=customer,
        encrypted_card=encrypted_card,
        security_code=security_code,
        installments=installments,
    )

    return jsonify({
        "order_id": order.get("id"),
        "reference_id": order.get("reference_id"),
        "status": order.get("status"),
        "charges": order.get("charges", []),
        "plano": plano,
        "valor_centavos": total,
    })


# ======================================================
# ====================  BOLETO  =======================
# ======================================================

@bp_payments.post("/checkout/boleto")
@login_required
def checkout_boleto():
    data = request.get_json() or {}
    plano = data.get("plano", "start")
    total = _preco_plano(plano)

    reference_id = f"optifleet-{current_user.id}-{uuid.uuid4().hex[:8]}"

    customer = {
        "name": current_user.name,
        "email": current_user.email,
        "tax_id": _tax_id_from_user(),
    }

    order = criar_pedido_boleto_optifleet(
        reference_id=reference_id,
        total_centavos=total,
        customer=customer,
    )

    charges = order.get("charges", [])
    charge = charges[0] if charges else {}

    boleto_info = charge.get("payment_method", {}).get("boleto", {})

    return jsonify({
        "order_id": order.get("id"),
        "reference_id": order.get("reference_id"),
        "status": order.get("status"),
        "charges": charges,
        "boleto": boleto_info,  # barcode, due_date etc.
        "plano": plano,
        "valor_centavos": total,
    })


# ======================================================
# ===================  WEBHOOK  =======================
# ======================================================

@bp_payments.route("/webhook", methods=["POST"])
def pagseguro_webhook():
    """
    Webhook chamado pelo PagSeguro quando o status da cobrança mudar.
    Exemplo de URL final: https://www.optifleet.com.br/api/payments/webhook
    (é essa URL que você configura lá no painel do PagSeguro).
    """
    event = request.get_json() or {}

    charge = (event.get("data") or {}).get("charge") or {}
    charge_id = charge.get("id")
    status = charge.get("status")

    # Aqui você pode logar para auditoria se quiser
    # print("Webhook PagSeguro:", charge_id, status, flush=True)

    if status == "PAID":
        # ativa o plano no banco (implemente essa função)
        # ativar_plano_usuario(event)
        pass

    return jsonify({"success": True}), 200
