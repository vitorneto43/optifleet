from flask import Blueprint, jsonify
from datetime import datetime, timedelta, timezone
from core.db import get_conn, create_subscription, mark_subscription_status
from billing.asaas_client import create_payment, create_customer

bp_billing_cron = Blueprint("billing_cron", __name__, url_prefix="/billing")

@bp_billing_cron.post("/renew")
def renew():
    conn = get_conn()
    rows = conn.execute("""
      SELECT id, user_id, plan, billing, vehicles, status, current_period_end, provider, provider_ref
      FROM subscriptions
      WHERE status='active'
    """).fetchall()

    ahead = int(os.getenv("BILLING_FROM_DAYS","5"))
    now = datetime.now(timezone.utc)

    created = 0
    for r in rows:
      sub_id, user_id, plan, billing, vehicles, status, period_end, provider, provider_ref = r
      days_left = (period_end - now).days if period_end else 0
      if days_left <= ahead:
        # Gerar nova fatura (mesma regra do /billing/go)
        cust = create_customer(f"user-{user_id}", f"user-{user_id}@example.com")
        value = 399.0 if plan=="full" else 199.0
        if billing == "annual": value *= 10  # exemplo
        pay = create_payment(cust["id"], value, "PIX", f"OptiFleet {plan}-{billing}", now.date().isoformat(), f"renew:{sub_id}:{now.isoformat()}")
        # cria novo registro pendente (para o próximo ciclo)
        days = 365 if billing == "annual" else 30
        create_subscription(
          user_id=user_id, plan=plan, billing=billing, vehicles=vehicles, status="pending",
          provider="asaas", provider_ref=pay["id"], started_at=now, current_period_end=now + timedelta(days=days)
        )
        created += 1

    # Expiração de pendências antigas (15 dias de tolerância)
    conn.execute("""
      UPDATE subscriptions
      SET status='canceled'
      WHERE status='pending' AND current_period_end < (now() - INTERVAL 15 DAY)
    """)
    return jsonify({"ok": True, "created": created})
