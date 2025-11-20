# routes/webhooks_routes.py
from flask import Blueprint, request, jsonify
import os

from mercadopago_client import consultar_pagamento, consultar_assinatura

bp_webhooks = Blueprint("webhooks", __name__, url_prefix="/webhooks")

MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")


@bp_webhooks.route("/mercadopago", methods=["POST"])
def webhook_mercadopago():
    """
    Configure no painel do Mercado Pago:
    URL: https://www.optifleet.com.br/webhooks/mercadopago
    Eventos: payment, preapproval, etc.
    """
    # Se quiser autenticar com um secret (query param ?secret=MP_WEBHOOK_SECRET)
    secret = request.args.get("secret")
    if MP_WEBHOOK_SECRET and secret != MP_WEBHOOK_SECRET:
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json() or {}
    print("=== WEBHOOK MERCADO PAGO ===")
    print(payload)

    # Exemplo de payload resumido:
    # { "id": "123456789", "live_mode": true, "type": "payment", "date_created": "...", ... }

    mp_type = payload.get("type") or payload.get("action")
    data_id = payload.get("data", {}).get("id") or payload.get("id")

    if not mp_type or not data_id:
        return jsonify({"status": "ignored"}), 200

    try:
        if mp_type.startswith("payment"):
            pagamento = consultar_pagamento(data_id)
            # TODO: atualizar banco: marcar fatura como paga, ativar plano etc.
            print("DETALHE PAGAMENTO:", pagamento)

        elif mp_type.startswith("preapproval"):
            assinatura = consultar_assinatura(data_id)
            # TODO: atualizar banco: status da assinatura, próxima cobrança, etc.
            print("DETALHE ASSINATURA:", assinatura)

    except Exception as e:
        print("Erro ao consultar Mercado Pago:", e)

    return jsonify({"status": "ok"}), 200
