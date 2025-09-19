# billing/asaas_routes.py
from flask import Blueprint, redirect, url_for
from flask_login import login_required

bp_asaas = Blueprint("billing", __name__, url_prefix="/billing")

@bp_asaas.get("/buy/<plan_code>")
@login_required
def buy(plan_code):
    # DEMO absoluto: nunca chama Asaas, só volta pro dashboard
    return redirect(url_for("home"))


    # ----------------------------
    # BLOCO DEMO (ativo)
    # ----------------------------
    # Ideia: permitir navegação e demonstração SEM cobrar nada.
    # Redireciona para o dashboard (ou para /pricing se preferir).

    # return redirect(url_for("pricing"))

    # ---------------------------------------------------------
    # BLOCO REAL (PRODUÇÃO) — DESCOMENTAR QUANDO FOR PRO AR
    # ---------------------------------------------------------
    # if not ASAAS_API_KEY:
    #     return jsonify({"error": "Configurar ASAAS_API_KEY nas variáveis de ambiente."}), 500
    #
    # def _asaas_headers():
    #     return {
    #         "Content-Type": "application/json",
    #         "access_token": ASAAS_API_KEY
    #     }
    #
    # def _find_or_create_customer(name, email, cpf_cnpj):
    #     # Tenta buscar por email
    #     r = requests.get(f"{ASAAS_BASE}/customers",
    #                      params={"email": email},
    #                      headers=_asaas_headers(), timeout=15)
    #     r.raise_for_status()
    #     items = r.json().get("data", [])
    #     if items:
    #         return items[0]["id"]
    #     # Cria se não existir
    #     payload = {"name": name, "email": email}
    #     if cpf_cnpj:
    #         payload["cpfCnpj"] = cpf_cnpj
    #     r = requests.post(f"{ASAAS_BASE}/customers",
    #                       json=payload,
    #                       headers=_asaas_headers(), timeout=15)
    #     r.raise_for_status()
    #     return r.json()["id"]
    #
    # try:
    #     # Dados do usuário logado
    #     name = getattr(current_user, "name", None) or current_user.email
    #     email = current_user.email
    #     cpf_cnpj = getattr(current_user, "cpf_cnpj", None)
    #
    #     # Cria/acha customer
    #     cust_id = _find_or_create_customer(name, email, cpf_cnpj)
    #
    #     # Cria assinatura
    #     payload = {
    #         "customer": cust_id,
    #         "billingType": "BOLETO",   # ou "CREDIT_CARD" / "PIX"
    #         "value": float(plan["value"]),
    #         "cycle": plan["cycle"],    # "MONTHLY"
    #         "description": plan["name"],
    #     }
    #     r = requests.post(f"{ASAAS_BASE}/subscriptions",
    #                       json=payload, headers=_asaas_headers(), timeout=20)
    #     r.raise_for_status()
    #     sub = r.json()
    #
    #     # Redireciona para a fatura quando disponível
    #     invoice_url = sub.get("invoiceUrl")
    #     if not invoice_url:
    #         payments = requests.get(
    #             f"{ASAAS_BASE}/payments",
    #             params={"subscription": sub["id"]},
    #             headers=_asaas_headers(), timeout=15
    #         ).json().get("data", [])
    #         if payments:
    #             invoice_url = payments[0].get("invoiceUrl")
    #
    #     if invoice_url:
    #         return redirect(invoice_url)
    #
    #     # Fallback: volta para a página de planos
    #     return redirect(url_for("pricing"))
    #
    # except Exception as e:
    #     return jsonify({"error": f"Falha ao iniciar compra: {e}"}), 500

