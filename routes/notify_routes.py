# routes/notify_routes.py
from flask import Blueprint, request, jsonify
from core.services.notifier import send_email, send_whatsapp_via_provider

bp_notify = Blueprint("notify", __name__)

@bp_notify.post("/api/notify/maintenance")
def notify_maintenance():
    p = request.get_json(force=True)
    email = p.get("email")
    phone = p.get("phone")      # E.164 ex: +5581...
    vehicle = p.get("vehicle")
    risk = p.get("risk")

    subj = f"[OptiFleet] Manutenção recomendada — {vehicle}"
    html = f"<h3>Manutenção</h3><p>O veículo <b>{vehicle}</b> está com risco <b>{risk}</b>.</p>"

    if email:
        try: send_email(email, subj, html)
        except Exception as e: return jsonify({"error": f"email: {e}"}), 500
    if phone:
        try: send_whatsapp_via_provider(phone, f"Manutenção recomendada para {vehicle}. Risco: {risk}.")
        except Exception as e: return jsonify({"error": f"whatsapp: {e}"}), 500

    return jsonify({"ok": True})
