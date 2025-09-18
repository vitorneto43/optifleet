from flask import Blueprint, request, jsonify

bp_asaas_webhook = Blueprint("asaas_webhook", __name__)

@bp_asaas_webhook.post("/billing/webhook")
def webhook():
    event = request.json
    event_type = event.get("event")
    payment = event.get("payment", {})

    # eventos: PAYMENT_RECEIVED, PAYMENT_OVERDUE, PAYMENT_CONFIRMED
    if event_type == "PAYMENT_RECEIVED":
        print(f"Pagamento recebido: {payment.get('id')}, valor {payment.get('value')}")
        # aqui você marca o tenant como ativo no seu banco
    elif event_type == "PAYMENT_OVERDUE":
        print(f"Pagamento atrasado: {payment.get('id')}")
    return jsonify({"received": True})
