# mercadopago_client.py
import os
import requests

BASE_URL = "https://api.mercadopago.com"

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")
if not MP_ACCESS_TOKEN:
    raise RuntimeError("MP_ACCESS_TOKEN não definido no .env")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def criar_preferencia_plano(
    plano_id: str,
    descricao: str,
    preco: float,
    external_reference: str,
    back_urls: dict,
) -> dict:
    """
    Cria uma preferência de pagamento (pagamento único: PIX/cartão/boleto).
    """
    url = f"{BASE_URL}/checkout/preferences"

    payload = {
        "items": [
            {
                "title": descricao,
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": round(preco, 2),
            }
        ],
        "external_reference": external_reference,
        "back_urls": back_urls,
        "auto_return": "approved",
    }

    print("=== MP PREFERENCE REQUEST ===")
    print(url)
    print(payload)

    resp = requests.post(url, json=payload, headers=_headers(), timeout=60)

    print("=== MP PREFERENCE RESPONSE ===")
    print(resp.status_code, resp.text)

    resp.raise_for_status()
    return resp.json()


def criar_assinatura_mensal(
    plano_id: str,
    descricao: str,
    preco: float,
    payer_email: str,
    external_reference: str,
) -> dict:
    """
    Cria uma assinatura mensal (preapproval) com cobrança automática.
    """
    url = f"{BASE_URL}/preapproval"

    payload = {
        "reason": descricao,
        "external_reference": external_reference,
        "payer_email": payer_email,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": round(preco, 2),
            "currency_id": "BRL",
        },
        "back_url": "https://www.optifleet.com.br/assinatura/retorno",
    }

    print("=== MP PREAPPROVAL REQUEST ===")
    print(url)
    print(payload)

    resp = requests.post(url, json=payload, headers=_headers(), timeout=60)

    print("=== MP PREAPPROVAL RESPONSE ===")
    print(resp.status_code, resp.text)

    resp.raise_for_status()
    return resp.json()


def consultar_pagamento(payment_id: str) -> dict:
    """
    Consulta um pagamento (usado no webhook quando type=payment).
    """
    url = f"{BASE_URL}/v1/payments/{payment_id}"
    resp = requests.get(url, headers=_headers(), timeout=60)
    print("=== MP GET PAYMENT ===")
    print(url, resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()


def consultar_assinatura(preapproval_id: str) -> dict:
    """
    Consulta uma assinatura (preapproval).
    """
    url = f"{BASE_URL}/preapproval/{preapproval_id}"
    resp = requests.get(url, headers=_headers(), timeout=60)
    print("=== MP GET PREAPPROVAL ===")
    print(url, resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()
