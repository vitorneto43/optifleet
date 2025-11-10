# core/db_connection.py (se você tiver este arquivo)
import duckdb
from pathlib import Path
import os

DB_PATH = Path(os.getenv("DUCKDB_PATH", "data/optifleet.duckdb"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def get_conn():
    """Conexão DuckDB com configurações para evitar conflitos"""
    conn = duckdb.connect(str(DB_PATH))
    # Configurações para melhor performance e evitar conflitos
    conn.execute("PRAGMA threads=1")  # Reduz concorrência
    conn.execute("PRAGMA enable_progress_bar=false")
    return conn

def close_db():
    """Fecha conexão se necessário"""
    pass  # DuckDB gerencia automaticamente