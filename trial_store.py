# core/trial_store.py
import json, os, hashlib, hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path

TRIAL_FILE = Path("data/trials.json")
TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
TRIAL_HASH_SALT = os.getenv("TRIAL_HASH_SALT", "troque-esta-salt")

def _h(s: str) -> str:
    if not s: return ""
    return hmac.new(TRIAL_HASH_SALT.encode(), s.encode(), hashlib.sha256).hexdigest()

def _load():
    if TRIAL_FILE.exists():
        with open(TRIAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def _save(rows):
    with open(TRIAL_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def trial_exists_recent(email: str, ip: str, fp: str|None, days=90) -> bool:
    rows = _load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    eh, ih, fh = _h((email or "").lower()), _h(ip or ""), _h(fp or "")
    for r in rows:
        ts = datetime.fromisoformat(r["created_at"])
        if ts >= cutoff and (r.get("email_hash")==eh or r.get("ip_hash")==ih or r.get("fp_hash")==fh):
            return True
    return False

def trial_register(user_id: int|None, email: str, ip: str, fp: str|None, note="trial start"):
    rows = _load()
    rows.append({
        "user_id": user_id,
        "email_hash": _h((email or "").lower()),
        "ip_hash": _h(ip or ""),
        "fp_hash": _h(fp or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": note
    })
    _save(rows)
