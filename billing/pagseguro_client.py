# billing/pagseguro_client.py
import os
import requests
from datetime import date, timedelta

# ===============================================
# CONFIGURAÇÕES (carrega do .env quando existir)
# ===============================================

PAGSEGURO_TOKEN = os.getenv("PAGSEGURO_TOKEN")

# 🔹 Default já apontando para PRODUÇÃO,
# mas se no .env tiver PAGSEGURO_BASE_URL, ele usa o de lá
PAGSEGURO_BASE_URL = os.getenv(
    "PAGSEGURO_BASE_URL",
    "https://api.pagseguro.com",
)

# 🔹 Webhook default apontando para a rota que criamos na OptiFleet
PAGSEGURO_NOTIFICATION_URL = os.getenv(
    "PAGSEGURO_NOTIFICATION_URL",
    "https://www.optifleet.com.br/api/payments/pagseguro/webhook",
)


def _headers():
    if not PAGSEGURO_TOKEN:
        raise ValueError("PAGSEGURO_TOKEN não configurado no .env")
    return {
        "Authorization": f"Bearer {PAGSEGURO_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ======================================================
# ======================   PIX   =======================
# ======================================================

def criar_pedido_pix_optifleet(reference_id: str, total_centavos: int, customer: dict):
    """
    Cria pedido PagSeguro com QR Code PIX para o plano OptiFleet.
    Usa o endpoint /orders com qr_codes.
    """
    url = f"{PAGSEGURO_BASE_URL.rstrip('/')}/orders"

    body = {
        "reference_id": reference_id,
        "customer": {
            "name": customer["name"],
            "email": customer["email"],
            "tax_id": customer["tax_id"],
        },
        "items": [
            {
                "name": "Plano OptiFleet",
                "quantity": 1,
                "unit_amount": total_centavos,
            }
        ],
        "qr_codes": [
            {
                "amount": {"value": total_centavos}
            }
        ],
        "notification_urls": [PAGSEGURO_NOTIFICATION_URL],
    }

    resp = requests.post(url, headers=_headers(), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ======================================================
# ====================== CARTÃO ========================
# ======================================================

def criar_pedido_cartao_optifleet(
    reference_id: str,
    total_centavos: int,
    customer: dict,
    encrypted_card: str,
    security_code: str,
    installments: int = 1,
):
    """
    Cria e paga pedido com cartão de crédito usando encryptedCard vindo do front.
    Usa o endpoint /orders com charges -> CREDIT_CARD.
    """
    url = f"{PAGSEGURO_BASE_URL.rstrip('/')}/orders"

    body = {
        "reference_id": reference_id,
        "customer": {
            "name": customer["name"],
            "email": customer["email"],
            "tax_id": customer["tax_id"],
        },
        "items": [
            {
                "name": "Plano OptiFleet",
                "quantity": 1,
                "unit_amount": total_centavos,
            }
        ],
        "charges": [
            {
                "reference_id": f"{reference_id}-card-1",
                "description": "Assinatura OptiFleet - Cartão",
                "amount": {
                    "value": total_centavos,
                    "currency": "BRL",
                },
                "payment_method": {
                    "type": "CREDIT_CARD",
                    "installments": installments,
                    "capture": True,
                    "card": {
                        "encrypted": encrypted_card,  # token gerado no front
                        "security_code": security_code,
                    },
                },
            }
        ],
        "notification_urls": [PAGSEGURO_NOTIFICATION_URL],
    }

    resp = requests.post(url, headers=_headers(), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ======================================================
# =====================  BOLETO  =======================
# ======================================================

def criar_pedido_boleto_optifleet(
    reference_id: str,
    total_centavos: int,
    customer: dict,
    dias_vencimento: int = 3,
):
    """
    Cria pedido PagSeguro pago com boleto.
    Usa o endpoint /orders com charges -> BOLETO.
    """
    url = f"{PAGSEGURO_BASE_URL.rstrip('/')}/orders"

    due = (date.today() + timedelta(days=dias_vencimento)).isoformat()

    body = {
        "reference_id": reference_id,
        "customer": {
            "name": customer["name"],
            "email": customer["email"],
            "tax_id": customer["tax_id"],
        },
        "items": [
            {
                "name": "Plano OptiFleet",
                "quantity": 1,
                "unit_amount": total_centavos,
            }
        ],
        "charges": [
            {
                "reference_id": f"{reference_id}-boleto-1",
                "description": "Assinatura OptiFleet - Boleto",
                "amount": {
                    "value": total_centavos,
                    "currency": "BRL",
                },
                "payment_method": {
                    "type": "BOLETO",
                    "boleto": {
                        "due_date": due,
                        "instruction_lines": {
                            "line_1": "Pagamento do plano OptiFleet.",
                            "line_2": "Obrigado por utilizar nossa plataforma.",
                        },
                        "holder": {
                            "name": customer["name"],
                            "tax_id": customer["tax_id"],
                            "email": customer["email"],
                        },
                    },
                },
            }
        ],
        "notification_urls": [PAGSEGURO_NOTIFICATION_URL],
    }

    resp = requests.post(url, headers=_headers(), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ======================================================
# =========  CLASSE COMPATÍVEL COM billing_routes  =====
# ======================================================

class PagSeguroClient:
    """
    Wrapper orientado a objeto para ser usado em billing_routes.py

    - pagseguro.create_charge(payload): usa o endpoint /charges (fluxo genérico)
    - você ainda pode usar as funções criar_pedido_* se quiser algo
      mais específico para PIX/Cartão/Boleto em outro lugar.
    """

    def __init__(self):
        if not PAGSEGURO_TOKEN:
            raise ValueError("PAGSEGURO_TOKEN não configurado no .env")

        self.base_url = PAGSEGURO_BASE_URL.rstrip("/")
        self.token = PAGSEGURO_TOKEN
        self.notification_url = PAGSEGURO_NOTIFICATION_URL

    def _headers(self):
        return _headers()

    def create_charge(self, payload: dict) -> dict:
        """
        Cria uma cobrança genérica usando o endpoint /charges.
        Compatível com o payload montado em billing_routes.go_checkout.
        """
        # Garante que tem notification_urls no payload
        if "notification_urls" not in payload:
            payload["notification_urls"] = [self.notification_url]

        url = f"{self.base_url}/charges"
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_charge(self, charge_id: str) -> dict:
        url = f"{self.base_url}/charges/{charge_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
