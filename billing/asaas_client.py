import os, requests

# Mantido como você já usa:
BASE = os.getenv("ASAAS_BASE", "https://www.asaas.com/api/v3")
API_KEY = os.getenv("ASAAS_API_KEY", "")
TIMEOUT = int(os.getenv("ASAAS_TIMEOUT", "20"))

HEADERS = {
    "access_token": '$aact_prod_000MzkwODA2MWY2OGM3MWRlMDU2NWM3MzJlNzZmNGZhZGY6Ojk2MWEwYjM3LTI1MTEtNDRkMS1iYjBhLWU4ODg5NmQyZWRhNzo6JGFhY2hfNjJlZDE4NzEtMDEwMC00ZDQwLTg0YjMtMWQxMmU5ZDZjZmFj',
    "Content-Type": "application/json"
}

def _url(path: str) -> str:
    return f"{BASE.rstrip('/')}/{path.lstrip('/')}"

# -----------------------------
# Customers
# -----------------------------
def create_customer(name: str, email: str, cpfCnpj: str | None = None) -> dict:
    """
    Cria um cliente no Asaas. 'cpfCnpj' é opcional (mantém compatibilidade).
    """
    payload = {
        "name": name or email,
        "email": email
    }
    if cpfCnpj:
        payload["cpfCnpj"] = cpfCnpj

    r = requests.post(_url("/customers"), headers=HEADERS, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_customers_by_email(email: str) -> dict:
    r = requests.get(_url("/customers"), headers=HEADERS, params={"email": email}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def find_or_create_customer(name: str, email: str, cpfCnpj: str | None = None) -> dict:
    """
    Busca por email e, se não existir, cria.
    """
    data = get_customers_by_email(email)
    if data.get("totalCount", 0) > 0 and data.get("data"):
        return data["data"][0]
    return create_customer(name, email, cpfCnpj)

# -----------------------------
# Payments
# -----------------------------
def create_payment(customer_id: str, value: float, billing_type: str,
                   description: str, due_date: str, external_ref: str) -> dict:
    """
    billing_type: "CREDIT_CARD" | "PIX" | "BOLETO"
    """
    r = requests.post(_url("/payments"), headers=HEADERS, json={
        "customer": customer_id,
        "billingType": billing_type,
        "value": float(value),
        "description": description,
        "dueDate": due_date,                 # "YYYY-MM-DD"
        "externalReference": external_ref
    }, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def create_boleto_payment(customer_id: str, value: float,
                          description: str, due_date: str, external_ref: str) -> dict:
    """
    Atalho seguro para BOLETO (usado no Enterprise).
    """
    return create_payment(
        customer_id=customer_id,
        value=value,
        billing_type="BOLETO",
        description=description,
        due_date=due_date,
        external_ref=external_ref
    )

def get_payment(payment_id: str) -> dict:
    r = requests.get(_url(f"/payments/{payment_id}"), headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

