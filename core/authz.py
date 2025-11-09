# core/authz.py
from datetime import datetime, timezone
from core.db import get_active_trial, get_active_subscription
from functools import wraps
from flask import abort
from flask_login import current_user

def user_has_access(user_id: int) -> bool:
    # Admin passa
    if getattr(current_user, "is_admin", False):
        return True
    sub = get_active_subscription(user_id)
    if sub and sub[4] == 'active':
        return True
    trial = get_active_trial(user_id)
    if trial:
        trial_end = trial[4]
        now_utc = datetime.now(timezone.utc)
        # Normaliza datetime (com ou sem tz) e dá tolerância até 23:59:59 do dia final
        if hasattr(trial_end, "tzinfo") and trial_end.tzinfo:
            return trial_end > now_utc
        # trial_end veio como string ISO (sem tz)
        dt = datetime.fromisoformat(trial_end)
        if not getattr(dt, "tzinfo", None):
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > now_utc
    return False


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        # ajuste conforme seu model: is_admin, role == 'admin', etc.
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper
