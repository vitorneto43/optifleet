import os, requests
from dotenv import load_dotenv
load_dotenv()

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_BASE_URL = os.getenv("ASAAS_BASE_URL", "https://sandbox.asaas.com/api/v3")
HEADERS = {"access_token": ASAAS_API_KEY, "Content-Type": "application/json"}

def create_customer(name, email, cpf_cnpj):
    payload = {"name": name, "email": email, "cpfCnpj": cpf_cnpj}
    r = requests.post(f"{ASAAS_BASE_URL}/customers", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def create_subscription(customer_id, value, billing_type="PIX", cycle="MONTHLY", description="Plano OptiFleet"):
    payload = {
        "customer": customer_id,
        "billingType": billing_type,   # PIX, BOLETO, CREDIT_CARD
        "value": value,
        "cycle": cycle,
        "description": description
    }
    r = requests.post(f"{ASAAS_BASE_URL}/subscriptions", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def list_payments(subscription_id):
    r = requests.get(f"{ASAAS_BASE_URL}/subscriptions/{subscription_id}/payments", headers=HEADERS)
    r.raise_for_status()
    return r.json()
