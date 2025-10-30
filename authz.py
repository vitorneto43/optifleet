# core/authz.py
from datetime import datetime, timezone
from core.db import get_active_trial, get_active_subscription

def user_has_access(user_id: int) -> bool:
    sub = get_active_subscription(user_id)
    if sub and sub[4] == 'active':   # status
        return True
    trial = get_active_trial(user_id)
    if trial:
        trial_end = trial[4]
        if hasattr(trial_end, "tzinfo"):
            return trial_end > datetime.now(timezone.utc)
        return datetime.fromisoformat(trial_end) > datetime.now(timezone.utc)
    return False
