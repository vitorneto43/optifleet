# billing/asaas_routes.py
from flask import Blueprint, request, redirect, url_for, abort, jsonify
from flask_login import login_required, current_user
import os, requests

bp_asaas = Blueprint("billing", __name__, url_prefix="/billing")

PLANS = {
    "starter": {"name": "OptiFleet Starter", "value": 399.00, "cycle": "MONTHLY"},
    "pro": {"name": "OptiFleet Pro", "value": 1499.00, "cycle": "MONTHLY"},
}

ASAAS_API_KEY = os.environ.get("ASAAS_API_KEY", "")
ASAAS_BASE = "https://www.asaas.com/api/v3"

def _asaas_headers():
    return {"Content-Type":"application/json", "access_token": ASAAS_API_KEY}

def _find_or_create_customer(name, email, cpf_cnpj):
    # tente buscar por email
    r = requests.get(f"{ASAAS_BASE}/customers", params={"email": email}, headers=_asaas_headers(), timeout=15)
    r.raise_for_status()
    items = r.json().get("data", [])
    if items:
        return items[0]["id"]
    # cria
    payload = {"name": name, "email": email}
    if cpf_cnpj:
        payload["cpfCnpj"] = cpf_cnpj
    r = requests.post(f"{ASAAS_BASE}/customers", json=payload, headers=_asaas_headers(), timeout=15)
    r.raise_for_status()
    return r.json()["id"]

@login_required
@bp_asaas.get("/buy/<plan_code>")
def buy(plan_code):
    plan = PLANS.get(plan_code)
    if not plan:
        abort(404)

    # pegue dados do usuário logado
    name = getattr(current_user, "name", None) or current_user.email
    email = current_user.email
    cpf_cnpj = getattr(current_user, "cpf_cnpj", None)

    try:
        # cria/acha customer
        cust_id = _find_or_create_customer(name, email, cpf_cnpj)

        # cria assinatura
        payload = {
            "customer": cust_id,
            "billingType": "BOLETO",   # mude p/ "CREDIT_CARD" ou "PIX" se quiser
            "value": float(plan["value"]),
            "cycle": plan["cycle"],    # MONTHLY
            "description": plan["name"],
        }
        r = requests.post(f"{ASAAS_BASE}/subscriptions", json=payload, headers=_asaas_headers(), timeout=20)
        r.raise_for_status()
        sub = r.json()

        # pega a primeira cobrança/fatura para redirecionar
        # (algumas vezes vem em sub['invoiceUrl'], senão procurar em /payments)
        invoice_url = sub.get("invoiceUrl")
        if not invoice_url:
            # busca pagamentos associados à assinatura
            payments = requests.get(f"{ASAAS_BASE}/payments", params={"subscription": sub["id"]},
                                    headers=_asaas_headers(), timeout=15).json().get("data", [])
            if payments:
                invoice_url = payments[0].get("invoiceUrl")

        if invoice_url:
            return redirect(invoice_url)

        # fallback: volta para /pricing com uma mensagem simples via query string
        return redirect(url_for("pricing", _scheme=None, _external=False))
    except Exception as e:
        # em produção: logar o erro
        return jsonify({"error": f"Falha ao iniciar compra: {e}"}), 500
