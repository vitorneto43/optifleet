from flask import Blueprint, request, redirect, render_template, url_for, flash, jsonify
from flask_login import login_required, current_user
import os, re, json, requests
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta, datetime, timezone
from core.db import create_subscription, mark_subscription_status
import unicodedata

# -------------------------------------------------------------------
# Blueprint
# -------------------------------------------------------------------
bp_asaas = Blueprint("asaas", __name__, url_prefix="/asaas")

# -------------------------------------------------------------------
# Constantes e utilitários
# -------------------------------------------------------------------
CYCLE_ALIASES = {
    "MONTHLY": {"monthly", "mensal", "m"},
    "WEEKLY":  {"weekly", "semanal", "w"},
    "YEARLY":  {"yearly", "anual", "y", "annual"},
}

ANNUAL_DISCOUNT = 0.15  # 15% OFF para todos os planos no anual

def _env() -> str:
    """production (default) | sandbox"""
    return (os.getenv("ASAAS_ENV") or "production").lower()

def _base() -> str:
    """Base URL do Asaas (usa ASAAS_BASE_URL se setado)."""
    forced = os.getenv("ASAAS_BASE_URL")
    if forced:
        return forced.strip()
    # produção vs sandbox
    return "https://api.asaas.com/v3" if _env() == "production" else "https://sandbox.asaas.com/api/v3"

def _has_api_key() -> bool:
    from flask import current_app
    k = (os.environ.get("ASAAS_API_KEY") or current_app.config.get("ASAAS_API_KEY") or "").strip()
    return bool(k)

def _headers() -> dict:
    from flask import current_app
    api_key = (os.environ.get("ASAAS_API_KEY") or current_app.config.get("ASAAS_API_KEY") or "").strip()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "OptiFleet/1.0",
        "access_token": api_key,  # autenticação Asaas
    }

def _today_iso() -> str:
    return date.today().isoformat()

# -------------------------------------------------------------------
# Preços / Ciclos (ajuste conforme seu pricing real)
# -------------------------------------------------------------------
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

PLAN_ALIASES = {
    "start": {"start", "starter", "inicial", "basic", "essentials", "route", "rota", "roteirizacao", "routing"},
    "pro": {"pro", "pró", "professional", "profissional"},
    "enterprise": {"enterprise", "empresarial", "empresa", "corp", "corporate"},
}

def _norm_plan_name(name: str | None) -> str:
    n = _strip_accents((name or "").strip().lower())
    for canonical, aliases in PLAN_ALIASES.items():
        if n in {_strip_accents(x) for x in aliases}:
            return canonical
    return "start" if not n else ("pro" if n not in ("start","pro","enterprise") else n)

def price_for(plan_name_or_dict, billing: str, vehicles: int | None = None) -> float:
    """
    start      = 399/mês
    pro        = 1499/mês
    enterprise = 2200/mês   ← o que você quer
    anual      = 15% OFF no total anual
    """
    if isinstance(plan_name_or_dict, dict):
        v_exp = parse_money(plan_name_or_dict.get("valor"))
        if v_exp > 0:
            return float(v_exp)
        plan_name = _norm_plan_name(plan_name_or_dict.get("nome"))
    else:
        plan_name = _norm_plan_name(str(plan_name_or_dict))

    MONTHLY = {
        "start": 399.00,
        "pro": 1499.00,
        "enterprise": 2200.00,  # ← AQUI 2200
    }

    base = MONTHLY.get(plan_name, 1499.00)

    bill = _strip_accents((billing or "monthly").strip().lower())
    if bill in {"annual", "anual", "yearly"}:
        return round(base * 12 * 0.85, 2)  # 15% OFF no ano
    return float(base)

def _price(plan, billing, vehicles: int | None = None) -> float:
    return price_for(plan, billing, vehicles)

def _asaas_cycle(billing: str) -> str:
    return "YEARLY" if (billing or "").lower() == "annual" else "MONTHLY"

# -------------------------------------------------------------------
# Helpers de parsing/normalização
# -------------------------------------------------------------------
def parse_money(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    s = re.sub(r'[^\d,.-]', '', s)
    if ',' in s and '.' not in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return 0.0

def _normalize_plan(plano, veiculos=None) -> dict:
    if isinstance(plano, str):
        s = plano.strip()
        try:
            plano = json.loads(s)
        except Exception:
            plano = {"nome": s}

    if not isinstance(plano, dict):
        plano = {}

    nome = _norm_plan_name(plano.get("nome"))
    valor = parse_money(plano.get("valor"))
    return {"nome": nome, "valor": float(valor) if valor > 0 else 0.0}

def _normalize_billing(faturamento) -> dict:
    """
    Aceita:
      - dict: {"ciclo": "MONTHLY", "dia_venc": 10, "billingType": "BOLETO"}
      - str:  "MONTHLY", "mensal", "weekly", "anual", "annual", etc.
      - str JSON: '{"ciclo":"MONTHLY","dia_venc":10,"billingType":"BOLETO"}'
    Retorna {"ciclo","billingType","nextDueDate"} normalizados.
    """
    if isinstance(faturamento, str):
        fat_str = faturamento.strip()
        # tenta JSON primeiro
        try:
            faturamento = json.loads(fat_str)
        except Exception:
            # 🔥 AQUI ESTAVA O PROBLEMA
            # vamos comparar tudo em minúsculo
            low = fat_str.lower()
            ciclo = None
            for k, aliases in CYCLE_ALIASES.items():
                # k = "MONTHLY" / "YEARLY" ... então compara em lower tb
                if low in {a.lower() for a in aliases} or low == k.lower():
                    ciclo = k  # mantém o nome oficial em maiúsculo
                    break
            if not ciclo:
                ciclo = "MONTHLY"
            faturamento = {"ciclo": ciclo}

    if not isinstance(faturamento, dict):
        faturamento = {}

    ciclo = (faturamento.get("ciclo") or "MONTHLY").upper()
    if ciclo not in {"MONTHLY", "WEEKLY", "YEARLY"}:
        ciclo = "MONTHLY"

    billing_type = (faturamento.get("billingType") or "BOLETO").upper()
    if billing_type not in {"BOLETO", "CREDIT_CARD"}:
        billing_type = "BOLETO"

    # vencimento
    dia_venc = faturamento.get("dia_venc")
    today = date.today()
    if isinstance(dia_venc, int) and 1 <= dia_venc <= 28 and ciclo in {"MONTHLY", "YEARLY"}:
        if today.day < dia_venc:
            next_due = date(today.year, today.month, dia_venc)
        else:
            mm = today.month + 1
            yy = today.year + (1 if mm > 12 else 0)
            mm = 1 if mm > 12 else mm
            next_due = date(yy, mm, dia_venc)
    else:
        next_due = today + timedelta(days=1)

    return {
        "ciclo": ciclo,
        "billingType": billing_type,
        "nextDueDate": next_due.isoformat(),
    }

# -------------------------------------------------------------------
# Chamadas à API do Asaas
# -------------------------------------------------------------------
def _create_or_get_customer(name, email, cpfCnpj, phone=None) -> dict:
    BASE = _base()
    H = _headers()

    q = requests.get(f"{BASE}/customers", headers=H, params={"email": email}, timeout=15)
    q.raise_for_status()
    items = q.json().get("data", []) if q.headers.get("content-type","").startswith("application/json") else []
    if items:
        cust = items[0]
        cid = cust["id"]
        if not cust.get("cpfCnpj") and cpfCnpj:
            up = requests.put(f"{BASE}/customers/{cid}", headers=H, json={"cpfCnpj": cpfCnpj}, timeout=15)
            up.raise_for_status()
            cust = up.json()
        return cust

    payload = {"name": name, "email": email, "cpfCnpj": cpfCnpj}
    if phone:
        payload["mobilePhone"] = phone
    r = requests.post(f"{BASE}/customers", headers=H, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def build_subscription_payload(customer_id, plano, faturamento, veiculos, user_id=None, credit_card_token=None) -> dict:
    plan = _normalize_plan(plano, veiculos=veiculos)
    bill = _normalize_billing(faturamento)

    cycle = bill["ciclo"]
    billing_txt = "annual" if cycle == "YEARLY" else "monthly"

    value = price_for(plan, billing_txt, veiculos)
    if value <= 0.0:
        raise ValueError("Valor do plano não definido (> 0). Informe 'valor' ou ajuste as regras.")

    desc_nome = plan["nome"]
    desc_veic = f"{veiculos} veículos" if veiculos not in (None, "") else ""
    desc_ciclo = "anual" if billing_txt == "annual" else "mensal"
    desc = " | ".join(x for x in (desc_nome, desc_ciclo, desc_veic) if x)

    payload = {
        "customer": customer_id,
        "value": value,
        "cycle": bill["ciclo"],
        "billingType": bill["billingType"],
        "nextDueDate": bill["nextDueDate"],
        "description": desc,
    }

    if payload["billingType"] == "CREDIT_CARD":
        token = credit_card_token
        if not token and isinstance(faturamento, dict):
            token = faturamento.get("creditCardToken")
        if not token and isinstance(plano, dict):
            token = plano.get("creditCardToken")
        if token:
            payload["creditCardToken"] = token
        else:
            payload["description"] = f"{desc} (ATENÇÃO: falta creditCardToken)"

    return payload

def _create_subscription(customer_id, plano, faturamento, veiculos, user_id) -> dict:
    BASE = _base()
    H = _headers()
    payload = build_subscription_payload(customer_id, plano, faturamento, veiculos, user_id=user_id)
    r = requests.post(f"{BASE}/subscriptions", headers=H, json=payload, timeout=20)

    if r.status_code >= 400:
        try:
            err = r.json()
        except Exception:
            err = {"raw": r.text}
        print("[ASAAS][SUB] PAYLOAD_ENVIADO:", payload)
        print("[ASAAS][SUB] ERRO:", r.status_code, err)
        r.raise_for_status()
    return r.json()

def _ensure_first_payment(subscription_id: str, value: float, prefer: str = "UNDEFINED") -> dict:
    payload = {
        "value": value,
        "dueDate": _today_iso(),
        "billingType": prefer,
        "description": "Primeira cobrança da assinatura",
    }
    r = requests.post(
        f"{_base()}/subscriptions/{subscription_id}/payments",
        headers=_headers(), json=payload, timeout=15
    )
    r.raise_for_status()
    return r.json()

def _extract_checkout_url(obj: dict) -> str | None:
    for k in ("invoiceUrl", "bankSlipUrl", "paymentUrl", "checkoutUrl", "subscribeUrl"):
        url = obj.get(k)
        if isinstance(url, str) and url.startswith("http"):
            return url
    for k in ("charge", "payment", "data"):
        sub = obj.get(k)
        if isinstance(sub, dict):
            for kk in ("invoiceUrl", "bankSlipUrl", "paymentUrl", "checkoutUrl", "subscribeUrl"):
                url = sub.get(kk) if isinstance(sub, dict) else None
                if isinstance(url, str) and url.startswith("http"):
                    return url
    return None

def _find_payment_url(payment_obj: dict) -> str | None:
    for k in ("invoiceUrl", "bankSlipUrl", "paymentUrl", "checkoutUrl"):
        u = payment_obj.get(k)
        if isinstance(u, str) and u.startswith("http"):
            return u
    for k in ("charge", "payment", "data"):
        sub = payment_obj.get(k)
        if isinstance(sub, dict):
            for kk in ("invoiceUrl", "bankSlipUrl", "paymentUrl", "checkoutUrl"):
                u = sub.get(kk)
                if isinstance(u, str) and u.startswith("http"):
                    return u
    return None

def _get_latest_payment_url(subscription_id: str) -> str | None:
    r = requests.get(
        f"{_base()}/payments",
        headers=_headers(),
        params={"subscription": subscription_id, "limit": 1, "offset": 0, "order": "desc"},
        timeout=15
    )
    if not r.ok:
        return None
    j = r.json()
    items = j.get("data") or j.get("dados") or []
    if items:
        return _find_payment_url(items[0]) or _extract_checkout_url(items[0])
    return None

def _create_oneoff_payment(customer_id: str, value: float, desc: str) -> dict:
    payload = {
        "customer": customer_id,
        "billingType": "UNDEFINED",
        "value": value,
        "dueDate": _today_iso(),
        "description": desc,
    }
    r = requests.post(f"{_base()}/payments", headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

# -------------------------------------------------------------------
# Rotas de UI (checkout + start via formulário)
# -------------------------------------------------------------------
# -------------------------------
# ASAAS CHECKOUT (GET)
# -------------------------------
@bp_asaas.get("/checkout")
@login_required
def checkout():
    raw_plan = request.args.get("plan") or request.form.get("plan") or "start"
    plan = _norm_plan_name(raw_plan)

    billing = (request.args.get("billing") or request.form.get("billing") or "monthly").strip().lower()
    billing = billing.replace("á", "a").replace("ã", "a")  # anual/anual -> annual

    v_str = request.args.get("vehicles") or request.form.get("vehicles") or "1"
    try:
        vehicles = int(v_str)
    except ValueError:
        vehicles = 1

    # 🔒 trava por plano
    if plan == "start":
        vehicles = min(max(1, vehicles), 5)
    elif plan == "pro":
        vehicles = min(max(1, vehicles), 50)
    else:  # enterprise
        vehicles = min(max(51, vehicles), 9_999_999)

    # 🔁 usa o mesmo motor de preço
    price = _price(plan, billing, vehicles)

    plan_label = {"start": "Start", "pro": "Pro", "enterprise": "Enterprise"}[plan]
    billing_label = "Anual (15% OFF)" if billing in {"annual", "anual", "yearly"} else "Mensal"
    monthly_equiv = round(price / 12, 2) if billing in {"annual", "anual", "yearly"} else None

    return render_template(
        "checkout.html",
        plan=plan,
        plan_label=plan_label,
        billing=billing,
        billing_label=billing_label,
        vehicles=vehicles,
        price=price,
        monthly_equiv=monthly_equiv,
    )



# -------------------------------
# ASAAS START (POST)
# -------------------------------
@bp_asaas.post("/start")
@login_required
def start_subscription_form():
    # ---------- 1. plano ----------
    raw_plan = request.form.get("plan") or request.args.get("plan") or "start"
    plan = _norm_plan_name(raw_plan)

    # ---------- 2. billing ----------
    billing_raw = (request.form.get("billing") or request.args.get("billing") or "monthly").lower()
    billing_raw = billing_raw.replace("á", "a").replace("ã", "a")

    # vamos normalizar AQUI usando a função boa
    billing_norm = _normalize_billing(billing_raw)  # -> {"ciclo": "...", "billingType": "...", "nextDueDate": "..."}

    # isso aqui é o que o nosso pricing usa para calcular
    billing_txt = "annual" if billing_norm["ciclo"] == "YEARLY" else "monthly"

    # ---------- 3. veículos ----------
    v_str = request.form.get("vehicles") or request.args.get("vehicles") or "1"
    try:
        vehicles = int(v_str)
    except ValueError:
        vehicles = 1

    if plan == "start":
        vehicles = min(max(1, vehicles), 5)
    elif plan == "pro":
        vehicles = min(max(1, vehicles), 50)
    else:  # enterprise
        vehicles = min(max(51, vehicles), 9_999_999)

    # ---------- 4. dados do cliente ----------
    name = request.form.get("name") or current_user.email.split("@")[0]
    email = request.form.get("email") or current_user.email
    cpfCnpj = request.form.get("cpfCnpj") or None
    phone = request.form.get("phone") or None

    if not _has_api_key():
        flash("Configuração do Asaas ausente (ASAAS_API_KEY).", "error")
        return redirect(url_for("asaas.checkout", plan=plan, billing=billing_txt, vehicles=vehicles))

    # ---------- 5. cliente no Asaas ----------
    try:
        cust = _create_or_get_customer(name=name, email=email, cpfCnpj=cpfCnpj, phone=phone)
    except requests.HTTPError:
        flash("Falha ao criar/atualizar cliente no Asaas.", "error")
        return redirect(url_for("asaas.checkout", plan=plan, billing=billing_txt, vehicles=vehicles))

    # ---------- 6. assinatura no Asaas ----------
    try:
        # 👉 AQUI é o pulo do gato:
        # passamos o billing JÁ NORMALIZADO, e não só a string "annual"
        sub = _create_subscription(
            customer_id=cust["id"],
            plano=plan,
            faturamento=billing_norm,
            veiculos=vehicles,
            user_id=int(current_user.id),
        )
    except requests.HTTPError as e:
        try:
            err = e.response.json()
        except Exception:
            err = {"raw": getattr(e.response, "text", "")[:300]}
        print("[ASAAS][SUB] ERRO AO CRIAR ASSINATURA:", err)
        flash("Falha ao criar assinatura no Asaas. Verifique os dados e tente novamente.", "error")
        return redirect(url_for("asaas.checkout", plan=plan, billing=billing_txt, vehicles=vehicles))

    # ---------- 7. grava local ----------
    provider_ref = sub.get("id") or sub.get("subscription") or ""
    started_at = datetime.now(timezone.utc)
    period_end = started_at + (
        timedelta(days=365) if billing_norm["ciclo"] == "YEARLY" else timedelta(days=31)
    )

    create_subscription(
        user_id=int(current_user.id),
        plan=plan,
        billing=billing_txt,  # salva "monthly" / "annual" no seu banco
        vehicles=vehicles,
        status="pending",
        provider="asaas",
        provider_ref=provider_ref or "unknown",
        started_at=started_at,
        current_period_end=period_end,
    )

    # ---------- 8. redireciona pro boleto/pagamento ----------
    url = _extract_checkout_url(sub) or _find_payment_url(sub) or _get_latest_payment_url(provider_ref)
    if url:
        return redirect(url)

    flash("Assinatura criada. O Asaas enviará as instruções de pagamento por e-mail.", "info")
    return redirect(url_for("account.account_home"))



# -------------------------------------------------------------------
# Webhook Asaas
# -------------------------------------------------------------------
@bp_asaas.post("/webhook")
def webhook():
    j = request.get_json(force=True) or {}
    event = j.get("event", "")
    data = j.get("payment") or j.get("subscription") or {}
    provider_ref = data.get("id") or data.get("subscription") or ""
    status = None
    if event in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED", "SUBSCRIPTION_ACTIVATED"):
        status = "active"
    elif event in ("PAYMENT_OVERDUE", "SUBSCRIPTION_SUSPENDED"):
        status = "past_due"
    elif event in ("PAYMENT_REFUNDED", "SUBSCRIPTION_DELETED", "SUBSCRIPTION_CANCELED"):
        status = "canceled"
    if provider_ref and status:
        try:
            from core.db import get_subscription_by_provider_ref
            row = get_subscription_by_provider_ref(provider_ref)
            if row:
                mark_subscription_status(row[0], status)
        except Exception:
            pass
    return {"ok": True}

# -------------------------------------------------------------------
# Diagnóstico simples
# -------------------------------------------------------------------
@bp_asaas.get("/diag")
def diag():
    try:
        r = requests.get(
            f"{_base()}/subscriptions",
            headers=_headers(),
            params={"limit": 1},
            timeout=8
        )
        ok = r.ok
        status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
    except requests.exceptions.RequestException as e:
        return {
            "ok": False,
            "status": None,
            "env": _env(),
            "base": _base(),
            "has_key": _has_api_key(),
            "error": str(e),
        }, 500

    return {
        "ok": ok,
        "status": status,
        "env": _env(),
        "base": _base(),
        "has_key": _has_api_key(),
        "sample": body,
    }, (status or 200)

# -------------------------------------------------------------------
# AuthDiag (confirma chave/formato)
# -------------------------------------------------------------------
@bp_asaas.get("/authdiag")
def asaas_authdiag():
    BASE = _base()
    API_KEY = (os.getenv("ASAAS_API_KEY") or "").strip()

    def hex_of(s: str) -> str:
        return s.encode("utf-8").hex()

    diag = {
        "base_url": BASE,
        "api_key_len": len(API_KEY),
        "api_key_preview": (API_KEY[:8] + "..." + API_KEY[-8:]) if API_KEY else None,
        "api_key_hex_head": hex_of(API_KEY[:8]) if API_KEY else None,
        "api_key_hex_tail": hex_of(API_KEY[-8:]) if API_KEY else None,
        "format_ok": API_KEY.startswith("$aact_prod_") or API_KEY.startswith("$aact_hmlg_"),
    }

    try:
        r = requests.get(f"{BASE}/myAccount", headers=_headers(), timeout=10)
        diag["status_code"] = r.status_code
        diag["response_preview"] = r.text[:200]
        diag["valid"] = (r.status_code == 200)
    except Exception as e:
        diag["error"] = str(e)
        diag["valid"] = False

    return diag
