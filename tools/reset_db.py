# tools/reset_db.py
from pathlib import Path
import importlib
import core.db as db

# Apaga o arquivo do banco
Path(db.DB_PATH).unlink(missing_ok=True)

# Recarrega o módulo -> recria o schema automaticamente
importlib.reload(db)

print("Reset OK em:", db.DB_PATH.resolve())

