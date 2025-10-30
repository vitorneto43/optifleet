# core/db_connection.py
import os
import sqlite3
from flask import g

# caminho padrão do banco (mude se usar Postgres no Render)
DB_PATH = os.getenv("DB_PATH", "opti_fleet.db")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()
