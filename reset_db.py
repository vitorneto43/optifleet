# tools/reset_db.py
from pathlib import Path
import importlib
import core.db as db

Path(db.DB_PATH).unlink(missing_ok=True)
importlib.reload(db)  # reimport -> recria o schema
print("Reset OK em:", db.DB_PATH.resolve())
