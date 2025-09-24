import os, requests

BASE = os.getenv("ASAAS_BASE", "https://www.asaas.com/api/v3")
API_KEY = os.getenv("ASAAS_API_KEY", "")
HEADERS = {"access_token": API_KEY, "Content-Type": "application/json"}

def _url(path): return f"{BASE.rstrip('/')}/{path.lstrip('/')}"

def create_customer(name: str, email: str) -> dict:
    r = requests.post(_url("/customers"), headers=HEADERS, json={
        "name": name or email, "email": email
    }, timeout=20)
    r.raise_for_status()
    return r.json()

def create_payment(customer_id: str, value: float, billing_type: str,
                   description: str, due_date: str, external_ref: str) -> dict:
    # billing_type: "CREDIT_CARD" | "PIX" | "BOLETO"
    r = requests.post(_url("/payments"), headers=HEADERS, json={
        "customer": customer_id, "billingType": billing_type,
        "value": value, "description": description, "dueDate": due_date,
        "externalReference": external_ref
    }, timeout=20)
    r.raise_for_status()
    return r.json()

def get_payment(payment_id: str) -> dict:
    r = requests.get(_url(f"/payments/{payment_id}"), headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()
