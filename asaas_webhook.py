from flask import Blueprint, request, jsonify
from core.db import get_subscription_by_provider_ref, mark_subscription_status
from datetime import datetime, timedelta, timezone
import os, hmac, hashlib, json

bp_asaas_webhook = Blueprint("asaas_webhook", __name__, url_prefix="/billing")

def _valid_sig(raw: bytes, provided: str) -> bool:
    secret = os.getenv("BILLING_WEBHOOK_SECRET", "")
    if not secret: return True  # se não configurar, não valida (dev)
    mac = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, provided or "")

@bp_asaas_webhook.post("/webhook")
def webhook():
    raw = request.get_data()
    sig = request.headers.get("X-Signature", "")
    if not _valid_sig(raw, sig):
        return jsonify({"ok": False, "error": "bad_signature"}), 401

    payload = request.get_json(force=True)
    # O ASAAS envia algo como: {"event":"PAYMENT_RECEIVED", "payment":{"id":"pay_123", "status":"RECEIVED", ...}}
    event = (payload.get("event") or "").upper()
    p = payload.get("payment") or {}
    pay_id = p.get("id")

    sub = get_subscription_by_provider_ref(pay_id)
    if not sub:
        return jsonify({"ok": True})  # desconhecido mas não erra

    # sub: (id, user_id, plan, billing, vehicles, status, current_period_end)
    sub_id, _, _, billing, *_rest = sub
    now = datetime.now(timezone.utc)

    if event in {"PAYMENT_RECEIVED","PAYMENT_CONFIRMED"}:
        # ativa e ajusta período para frente (começando hoje)
        days = 365 if billing == "annual" else 30
        mark_subscription_status(sub_id, "active", current_period_end=now + timedelta(days=days))
    elif event in {"PAYMENT_OVERDUE","PAYMENT_DELETED","PAYMENT_REFUNDED"}:
        mark_subscription_status(sub_id, "past_due")
    return jsonify({"ok": True})

