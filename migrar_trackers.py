# migrar_trackers.py
from pathlib import Path
import duckdb

DB_PATH = Path("data/optifleet.duckdb")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
con = duckdb.connect(str(DB_PATH))

def table_exists(name: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables WHERE lower(table_name)=lower(?)",
        [name]
    ).fetchone())

def table_has_column(table: str, col: str) -> bool:
    if not table_exists(table):
        return False
    rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
    cols = {r[1].lower() for r in rows}  # (cid, name, type, …)
    return col.lower() in cols

# --- caso 1: não existe 'trackers' -> cria no formato novo
if not table_exists("trackers"):
    con.execute("""
    CREATE TABLE trackers (
      client_id INTEGER,
      tracker_id TEXT,
      secret_token TEXT,
      vehicle_id TEXT,
      imei TEXT,
      status TEXT,
      PRIMARY KEY (client_id, tracker_id)
    );
    """)
    print("✅ Criado 'trackers' no formato novo (não existia).")

# --- caso 2: existe, mas está no formato antigo (sem 'tracker_id', com 'imei' como identificador)
elif not table_has_column("trackers", "tracker_id") and table_has_column("trackers", "imei"):
    print("🔁 Migrando 'trackers' do formato antigo para o novo…")
    con.execute("""
    CREATE TABLE IF NOT EXISTS trackers_new (
      client_id INTEGER,
      tracker_id TEXT,
      secret_token TEXT,
      vehicle_id TEXT,
      imei TEXT,
      status TEXT,
      PRIMARY KEY (client_id, tracker_id)
    );
    """)
    # Copia dados (transforma imei -> tracker_id, marca status='active' se não existia)
    con.execute("""
    INSERT INTO trackers_new (client_id, tracker_id, secret_token, vehicle_id, imei, status)
    SELECT client_id,
           imei AS tracker_id,
           COALESCE(secret_token, '') AS secret_token,
           vehicle_id,
           imei,
           COALESCE(status, 'active') AS status
    FROM trackers;
    """)
    con.execute("DROP TABLE trackers;")
    con.execute("ALTER TABLE trackers_new RENAME TO trackers;")
    print("✅ Migração concluída.")

# --- caso 3: já está no formato novo
else:
    print("✔️ Tabela 'trackers' já está no formato novo. Nada a fazer.")

# (opcional) mostra colunas finais
cols = con.execute("PRAGMA table_info('trackers')").fetchall()
print("📋 Esquema final de 'trackers':")
for _, name, coltype, *_ in cols:
    print(f"  - {name} {coltype}")

con.close()

