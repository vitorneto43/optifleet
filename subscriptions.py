# core/subscriptions.py
from flask_login import current_user
from datetime import datetime, timezone
from core.db import get_latest_subscription_for_user
from functools import wraps
from flask import redirect, url_for, flash

def user_has_active_subscription(user_id: int) -> bool:
    sub = get_latest_subscription_for_user(user_id)
    if not sub:
        return False

    status = sub["status"]  # active, pending, past_due, canceled
    period_end = sub["current_period_end"]

    # se estiver sem data, considera que não está ativo
    if not period_end:
        return False

    now = datetime.now(timezone.utc)
    # vencido no tempo OU marcado como past_due/canceled → bloqueia
    if period_end < now:
        return False

    if status not in ("active",):   # só active passa
        return False

    return True
def subscription_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = int(current_user.get_id())
        if not user_has_active_subscription(uid):
            flash("Sua assinatura está vencida ou pendente. Regularize o pagamento.", "error")
            return redirect(url_for("billing.pricing_page"))
        return view(*args, **kwargs)
    return wrapped