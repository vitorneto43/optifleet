# core/db.py
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta, timezone
import duckdb
import secrets
import os
import json
import time
import random

# =========================
# Config / Conexão
# =========================
DB_PATH = Path(os.getenv("DUCKDB_PATH", "/var/data/optifleet/optifleet.duckdb"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def get_conn():
    """Abre uma conexão curta por operação (evita 'arquivo em uso' no Windows)."""
    return duckdb.connect(str(DB_PATH))

# =========================
# Schema (criado no import)
# =========================
def _init_schema():
    con = get_conn()

    # Usuários
    con.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL
    );
    """)

    # Veículos (PK composta)
    con.execute("""
    CREATE TABLE IF NOT EXISTS vehicles (
      id TEXT,                 -- ex.: "V1"
      client_id INTEGER,       -- dono (users.id)
      name TEXT,
      plate TEXT,
      driver TEXT,             -- nome/id de motorista
      capacity INTEGER,
      tags TEXT,               -- CSV/JSON
      obd_id TEXT,             -- identificador do rastreador/OBD
      last_lat DOUBLE,
      last_lon DOUBLE,
      last_speed DOUBLE,
      last_ts TIMESTAMP,
      status TEXT,             -- online|offline|maintenance
      last_service_km DOUBLE DEFAULT 0,
      last_service_date TIMESTAMP,
      next_service_km DOUBLE DEFAULT 0,
      notes TEXT,
      PRIMARY KEY (client_id, id)
    );
    """)

    # Telemetria crua
    con.execute("""
    CREATE TABLE IF NOT EXISTS telemetry (
      client_id INTEGER,
      vehicle_id TEXT,
      timestamp TIMESTAMP,
      lat DOUBLE, lon DOUBLE,
      speed DOUBLE, fuel DOUBLE
    );
    """)

    # Manutenção (logs)
    con.execute("""
    CREATE TABLE IF NOT EXISTS maint_logs (
      client_id INTEGER,
      vehicle_id TEXT,
      kind TEXT,                -- oil, tires, brakes, inspection...
      when_km DOUBLE,
      when_date TIMESTAMP,
      notes TEXT
    );
    """)

    # Assinaturas (billing)
    con.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
      id INTEGER,
      user_id INTEGER,
      plan TEXT,                -- 'route' | 'full' | 'trial'
      billing TEXT,             -- 'monthly' | 'annual' | 'trial'
      vehicles INT,
      status TEXT,              -- 'pending' | 'active' | 'past_due' | 'canceled'
      started_at TIMESTAMP,
      current_period_end TIMESTAMP,
      provider TEXT,            -- 'asaas' | 'mock' | 'internal'
      provider_ref TEXT,        -- id/link do provedor
      PRIMARY KEY (id)
    );
    """)

    # Trials (controle de direito de uso)
    con.execute("""
    CREATE TABLE IF NOT EXISTS trials (
      id INTEGER,
      user_id INTEGER,
      plan TEXT,                -- 'route' | 'full'
      vehicles INT,
      started_at TIMESTAMP,
      trial_end TIMESTAMP,
      status TEXT,              -- 'active' | 'expired' | 'converted'
      PRIMARY KEY (id)
    );
    """)

    # Contatos
    con.execute("""
    CREATE TABLE IF NOT EXISTS contacts (
      id INTEGER,
      name TEXT,
      email TEXT,
      company TEXT,
      message TEXT,
      created_at TIMESTAMP,
      PRIMARY KEY (id)
    );
    """)

    # Trackers
    con.execute("""
    CREATE TABLE IF NOT EXISTS trackers (
      id            INTEGER PRIMARY KEY,
      client_id     INTEGER,
      tracker_id    TEXT,                 -- identificador lógico (pode ser = IMEI)
      secret_token  TEXT,                 -- token para autenticar ingestão
      vehicle_id    TEXT,
      imei          TEXT,                 -- IMEI (único se presente)
      vendor        TEXT,                 -- fabricante (opcional)
      status        TEXT DEFAULT 'active',
      created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Auditoria de Trials
    con.execute("""
    CREATE TABLE IF NOT EXISTS trial_users (
      id              INTEGER,
      user_id         INTEGER NOT NULL,
      email           TEXT NOT NULL,
      nome            TEXT,
      trial_start     TIMESTAMP NOT NULL,
      trial_end       TIMESTAMP NOT NULL,
      status          TEXT DEFAULT 'ativo',   -- 'ativo' | 'expirado' | 'convertido'
      converted       BOOLEAN DEFAULT FALSE,
      created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (id)
    );
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_trial_users_user ON trial_users(user_id);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trial_users_status ON trial_users(status);")

    # ---- migrações leves dentro do MESMO con ----
    def _ensure_col(table, col, ddl):
        rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
        cols = {r[1].lower() for r in rows}  # r[1] = nome da coluna
        if col.lower() not in cols:
            con.execute(ddl)

    _ensure_col("trackers", "client_id", "ALTER TABLE trackers ADD COLUMN client_id INTEGER;")
    _ensure_col("trackers", "tracker_id", "ALTER TABLE trackers ADD COLUMN tracker_id TEXT;")
    _ensure_col("trackers", "secret_token", "ALTER TABLE trackers ADD COLUMN secret_token TEXT;")
    _ensure_col("trackers", "status", "ALTER TABLE trackers ADD COLUMN status TEXT DEFAULT 'active';")
    _ensure_col("trackers", "vendor", "ALTER TABLE trackers ADD COLUMN vendor TEXT;")
    _ensure_col("trial_users", "converted", "ALTER TABLE trial_users ADD COLUMN converted BOOLEAN DEFAULT FALSE;")

    # Índices úteis
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_trackers_imei ON trackers(imei);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tel_client_ts ON telemetry(client_id, timestamp);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tel_client_vid_ts ON telemetry(client_id, vehicle_id, timestamp);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);")

    con.close()

_init_schema()

# =========================
# Helpers gerais
# =========================
def _next_id(table: str) -> int:
    con = get_conn()
    nid = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0]
    con.close()
    return int(nid)

def _gen_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)

# =========================
# Users
# =========================
def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    con = get_conn()
    row = con.execute(
        "SELECT id, email, password FROM users WHERE email = ?",
        [email]
    ).fetchone()
    con.close()
    return None if not row else {"id": row[0], "email": row[1], "password": row[2]}

def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    con = get_conn()
    row = con.execute(
        "SELECT id, email, password FROM users WHERE id = ?",
        [user_id]
    ).fetchone()
    con.close()
    return None if not row else {"id": row[0], "email": row[1], "password": row[2]}

def insert_user(email: str, password_hash: str) -> int:
    con = get_conn()
    next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM users").fetchone()[0]
    con.execute(
        "INSERT INTO users (id, email, password) VALUES (?, ?, ?)",
        [next_id, email, password_hash]
    )
    con.close()
    return int(next_id)

# =========================
# Vehicles (CRUD)
# =========================
def vehicle_get(client_id: int, veh_id: str):
    con = get_conn()
    row = con.execute("""
        SELECT id, client_id, name, plate, driver, capacity, tags, obd_id,
               last_lat, last_lon, last_speed, last_ts, status,
               last_service_km, last_service_date, next_service_km, notes
        FROM vehicles
        WHERE client_id=? AND id=?
    """, [client_id, veh_id]).fetchone()
    con.close()
    return row

def upsert_vehicle(client_id: int, v: dict):
    # normaliza payload
    v = {**{
        "id": None, "name": None, "plate": None, "driver": None, "capacity": None,
        "tags": None, "obd_id": None, "status": "offline",
        "last_lat": None, "last_lon": None, "last_speed": None, "last_ts": None,
        "last_service_km": 0, "last_service_date": None, "next_service_km": 0,
        "notes": None
    }, **v}
    if not v["id"]:
        raise ValueError("upsert_vehicle: id obrigatório")

    con = get_conn()
    con.execute("""
    INSERT INTO vehicles (id, client_id, name, plate, driver, capacity, tags, obd_id,
                          last_lat, last_lon, last_speed, last_ts, status,
                          last_service_km, last_service_date, next_service_km, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (client_id, id) DO UPDATE SET
      name=excluded.name, plate=excluded.plate, driver=excluded.driver,
      capacity=excluded.capacity, tags=excluded.tags, obd_id=excluded.obd_id,
      status=excluded.status, last_lat=excluded.last_lat, last_lon=excluded.last_lon,
      last_speed=excluded.last_speed, last_ts=excluded.last_ts,
      last_service_km=excluded.last_service_km, last_service_date=excluded.last_service_date,
      next_service_km=excluded.next_service_km, notes=excluded.notes
    """, [
        v["id"], client_id, v["name"], v["plate"], v["driver"], v["capacity"], v["tags"], v["obd_id"],
        v["last_lat"], v["last_lon"], v["last_speed"], v["last_ts"], v["status"],
        v["last_service_km"], v["last_service_date"], v["next_service_km"], v["notes"]
    ])
    con.close()

# core/db.py
def delete_vehicle(client_id: int, vehicle_id: str) -> bool:
    con = get_conn()
    try:
        # verifica existência
        found = con.execute(
            "SELECT 1 FROM vehicles WHERE client_id=? AND id=?",
            [client_id, vehicle_id]
        ).fetchone()
        if not found:
            return False

        # apaga veículo
        con.execute(
            "DELETE FROM vehicles WHERE client_id=? AND id=?",
            [client_id, vehicle_id]
        )

        # desassocia tracker que apontava para esse vehicle_id (se houver)
        con.execute(
            "UPDATE trackers SET vehicle_id=NULL WHERE client_id=? AND vehicle_id=?",
            [client_id, vehicle_id]
        )

        return True
    finally:
        con.close()


def list_vehicles(client_id: int, q: Optional[str] = None, only: Optional[str] = None):
    con = get_conn()
    base = """
      SELECT
        v.id,
        v.client_id,
        v.name,
        v.plate,
        v.driver,
        v.capacity,
        v.tags,
        v.obd_id,
        v.last_lat,
        v.last_lon,
        v.last_speed,
        v.last_ts,
        v.status,
        v.last_service_km,
        v.last_service_date,
        v.next_service_km,
        v.notes,
        t.imei   AS imei,
        t.vendor AS vendor
      FROM vehicles v
      LEFT JOIN trackers t
        ON t.client_id = v.client_id
       AND t.vehicle_id = v.id
      WHERE v.client_id = ?
    """
    args = [client_id]
    if q:
        base += """
          AND (
            lower(v.name)  LIKE ?
            OR lower(v.plate) LIKE ?
            OR lower(v.id)    LIKE ?
            OR lower(v.driver) LIKE ?
            OR (v.tags IS NOT NULL AND lower(v.tags) LIKE ?)
          )
        """
        qlike = f"%{q.lower()}%"
        args += [qlike, qlike, qlike, qlike, qlike]
    if only in ("online", "offline", "maintenance"):
        base += " AND v.status = ?"
        args += [only]
    base += " ORDER BY v.id"
    rows = con.execute(base, args).fetchall()
    con.close()
    return rows

def get_vehicle(client_id: int, vehicle_id: str):
    con = get_conn()
    r = con.execute("SELECT * FROM vehicles WHERE client_id=? AND id=?", [client_id, vehicle_id]).fetchone()
    con.close()
    return r

def import_vehicles_bulk(client_id: int, rows: List[dict]):
    for r in rows:
        if "id" not in r or not str(r["id"]).strip():
            continue
        r = {**r, "id": str(r["id"]).strip()}
        upsert_vehicle(client_id, r)

# =========================
# Trackers
# =========================
def vehicles_list_with_tracker(client_id: int):
    con = get_conn()
    rows = con.execute("""
      SELECT v.id, v.plate AS code, v.name, v.capacity,
             NULL AS avg_consumption_km_l,
             t.tracker_id
      FROM vehicles v
      LEFT JOIN trackers t
        ON t.client_id = v.client_id AND t.vehicle_id = v.id
      WHERE v.client_id = ?
      ORDER BY v.id
    """, [client_id]).fetchall()
    con.close()
    return [{
        "id": r[0], "code": r[1], "model": r[2], "capacity": r[3],
        "avg_consumption_km_l": r[4], "tracker_id": r[5]
    } for r in rows]

def tracker_get(client_id: int, tracker_id: str):
    con = get_conn()
    row = con.execute("""
        SELECT client_id, tracker_id, secret_token, vehicle_id, imei, status
        FROM trackers
        WHERE client_id=? AND tracker_id=?
    """, [client_id, tracker_id]).fetchone()
    con.close()
    return row

def tracker_list(client_id: int):
    con = get_conn()
    rows = con.execute("""
        SELECT tracker_id, secret_token, vehicle_id, imei, status
        FROM trackers
        WHERE client_id=?
        ORDER BY tracker_id
    """, [client_id]).fetchall()
    con.close()
    return rows

def tracker_get_or_create(client_id: int, tracker_id: str):
    row = tracker_get(client_id, tracker_id)
    if row:
        return row
    token = _gen_token()
    con = get_conn()
    con.execute("""
      INSERT INTO trackers (client_id, tracker_id, secret_token, vehicle_id, imei, status)
      VALUES (?, ?, ?, NULL, NULL, 'active')
    """, [client_id, tracker_id, token])
    con.close()
    return tracker_get(client_id, tracker_id)

# No core/db.py, na função tracker_bind_vehicle, substitua por:

def tracker_bind_vehicle(client_id: int, tracker_id: str, vehicle_id: str, force: bool = False):
    """Vincula um tracker a um veículo - VERSÃO CORRIGIDA"""
    try:
        con = get_conn()

        # Verifica se o tracker existe
        existing = con.execute("""
            SELECT id, vehicle_id FROM trackers 
            WHERE client_id=? AND tracker_id=?
        """, [client_id, tracker_id]).fetchone()

        if existing:
            tracker_db_id, current_vehicle = existing
            # Se já está vinculado a outro veículo e não é force, retorna erro
            if current_vehicle and current_vehicle != vehicle_id and not force:
                con.close()
                return False

            # Atualiza o vínculo
            con.execute("""
                UPDATE trackers SET vehicle_id=?
                WHERE id=?
            """, [vehicle_id, tracker_db_id])
        else:
            # Cria novo tracker se não existir - GERA ID CORRETAMENTE
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trackers").fetchone()[0]
            token = _gen_token()
            con.execute("""
                INSERT INTO trackers 
                (id, client_id, tracker_id, secret_token, vehicle_id, imei, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
            """, [next_id, client_id, tracker_id, token, vehicle_id, tracker_id])

        con.close()
        return True

    except Exception as e:
        print(f"[ERROR] tracker_bind_vehicle: {e}")
        if con:
            con.close()
        return False

def tracker_unbind_vehicle(client_id: int, vehicle_id: str):
    con = get_conn()
    con.execute("""
      UPDATE trackers SET vehicle_id=NULL
      WHERE client_id=? AND vehicle_id=?
    """, [client_id, vehicle_id])
    con.close()

def tracker_rotate_token(client_id: int, tracker_id: str) -> str:
    newtok = _gen_token()
    con = get_conn()
    con.execute("""
      UPDATE trackers SET secret_token=? WHERE client_id=? AND tracker_id=?
    """, [newtok, client_id, tracker_id])
    con.close()
    return newtok

def get_tracker_owner(tracker_id: str, token: str):
    """Retorna (client_id, vehicle_id) se token bater e status ativo."""
    con = get_conn()
    row = con.execute("""
      SELECT client_id, vehicle_id
      FROM trackers
      WHERE tracker_id=? AND secret_token=? AND status='active'
    """, [tracker_id, token]).fetchone()
    con.close()
    return None if not row else (row[0], row[1])

# ---- SHIM de compatibilidade com versões antigas ----
def upsert_tracker(client_id: int, imei: str, secret_token: str, vehicle_id: str | None = None) -> None:
    row = tracker_get(client_id, imei)  # imei como tracker_id
    if not row:
        con = get_conn()
        con.execute("""
          INSERT INTO trackers (client_id, tracker_id, secret_token, vehicle_id, imei, status)
          VALUES (?, ?, ?, ?, ?, 'active')
        """, [client_id, imei, secret_token, vehicle_id, imei])
        con.close()
        return

    con = get_conn()
    if vehicle_id:
        con.execute("""
          UPDATE trackers SET secret_token=?, vehicle_id=?, imei=COALESCE(imei, ?)
          WHERE client_id=? AND tracker_id=?
        """, [secret_token, vehicle_id, imei, client_id, imei])
    else:
        con.execute("""
          UPDATE trackers SET secret_token=?, imei=COALESCE(imei, ?)
          WHERE client_id=? AND tracker_id=?
        """, [secret_token, imei, client_id, imei])
    con.close()

# =========================
# Telemetria
# =========================
def insert_telemetry(client_id: int, vehicle_id: str, ts: datetime,
                     lat: float, lon: float, speed: float, fuel: float) -> None:
    con = get_conn()
    con.execute("""
        INSERT INTO telemetry (client_id, vehicle_id, timestamp, lat, lon, speed, fuel)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [client_id, vehicle_id, ts, lat, lon, speed, fuel])
    con.close()

def latest_positions(client_id: int) -> List[tuple]:
    """
    Última posição por veículo (lat/lon/speed/fuel do maior timestamp de cada vehicle_id).
    Retorno: [(vehicle_id, lat, lon, speed, fuel, ts), ...]
    """
    con = get_conn()
    rows = con.execute("""
        SELECT
          vehicle_id,
          arg_max(lat, timestamp)   AS lat,
          arg_max(lon, timestamp)   AS lon,
          arg_max(speed, timestamp) AS speed,
          arg_max(fuel, timestamp)  AS fuel,
          max(timestamp)            AS ts
        FROM telemetry
        WHERE client_id = ?
        GROUP BY vehicle_id
        ORDER BY vehicle_id
    """, [client_id]).fetchall()
    con.close()
    return rows

# Alias compat
def obter_posicoes(client_id: int) -> List[tuple]:
    return latest_positions(client_id)

# =========================
# Billing / Assinaturas
# =========================
def create_subscription(user_id: int, plan: str, billing: str, vehicles: int,
                        status: str, provider: str, provider_ref: str,
                        started_at: datetime, current_period_end: datetime) -> int:
    sub_id = _next_id("subscriptions")
    con = get_conn()
    con.execute("""
        INSERT INTO subscriptions
          (id, user_id, plan, billing, vehicles, status, started_at, current_period_end, provider, provider_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [sub_id, user_id, plan, billing, vehicles, status, started_at, current_period_end, provider, provider_ref])
    con.close()
    return sub_id

def get_active_subscription(user_id: int):
    con = get_conn()
    row = con.execute("""
        SELECT id, plan, billing, vehicles, status, started_at, current_period_end, provider, provider_ref
        FROM subscriptions
        WHERE user_id = ? AND status = 'active'
        ORDER BY current_period_end DESC
        LIMIT 1
    """, [user_id]).fetchone()
    con.close()
    return row

def mark_subscription_status(sub_id: int, status: str, current_period_end: Optional[datetime] = None) -> None:
    con = get_conn()
    if current_period_end is None:
        con.execute("UPDATE subscriptions SET status = ? WHERE id = ?", [status, sub_id])
    else:
        con.execute("UPDATE subscriptions SET status = ?, current_period_end = ? WHERE id = ?",
                    [status, current_period_end, sub_id])
    con.close()

def get_subscription_by_provider_ref(provider_ref: str):
    con = get_conn()
    row = con.execute("""
      SELECT id, user_id, plan, billing, vehicles, status, current_period_end
      FROM subscriptions
      WHERE provider='asaas' AND provider_ref=?
      ORDER BY id DESC LIMIT 1
    """, [provider_ref]).fetchone()
    con.close()
    return row

def get_latest_subscription_for_user(user_id: int):
    """Retorna a assinatura mais recente do usuário (via DuckDB)."""
    con = get_conn()
    row = con.execute("""
        SELECT id, user_id, plan, billing, vehicles, status,
               started_at, current_period_end, provider, provider_ref
        FROM subscriptions
        WHERE user_id = ?
        ORDER BY started_at DESC
        LIMIT 1
    """, [user_id]).fetchone()
    con.close()
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "plan": row[2],
        "billing": row[3],
        "vehicles": row[4],
        "status": row[5],
        "started_at": row[6],
        "current_period_end": row[7],
        "provider": row[8],
        "provider_ref": row[9],
    }

# =========================
# Trials (direito de uso)
# =========================
def create_trial(user_id: int, plan: str, vehicles: int, days: int = 15) -> int:
    trial_id = _next_id("trials")
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    con = get_conn()
    con.execute("""
      INSERT INTO trials (id, user_id, plan, vehicles, started_at, trial_end, status)
      VALUES (?, ?, ?, ?, ?, ?, 'active')
    """, [trial_id, user_id, plan, vehicles, now, end])
    con.close()
    return trial_id

def get_active_trial(user_id: int):
    con = get_conn()
    row = con.execute("""
      SELECT id, plan, vehicles, started_at, trial_end, status
      FROM trials
      WHERE user_id = ? AND status = 'active'
        AND trial_end >= CURRENT_TIMESTAMP
      ORDER BY trial_end DESC
      LIMIT 1
    """, [user_id]).fetchone()
    con.close()
    return row

def expire_trial(trial_id: int) -> None:
    con = get_conn()
    con.execute("""
        UPDATE trials
        SET status = 'expired', trial_end = CURRENT_TIMESTAMP
        WHERE id = ?
    """, [trial_id])
    con.close()

def mark_trial_converted(trial_id: int):
    con = get_conn()
    con.execute("UPDATE trials SET status='converted' WHERE id=?", [trial_id])
    con.close()

# =========================
# Trial Users (auditoria)
# =========================
# core/db.py (apenas a função problemática corrigida)
# core/db.py - função trial_users_upsert corrigida
def trial_users_upsert(user_id: int, email: str, nome: str | None, trial_start, trial_end, converted: bool):
    """
    UPSERT com retry automático para evitar conflitos de transação no DuckDB.
    """
    max_retries = 3
    base_delay = 0.1  # 100ms

    for attempt in range(max_retries):
        try:
            con = get_conn()

            # Lógica de status simplificada
            status = 'convertido' if converted else 'ativo'

            # Usar uma única operação SQL para evitar conflitos
            # Primeiro tenta UPDATE, se não afetar linhas, faz INSERT
            result = con.execute("""
                UPDATE trial_users 
                SET email=?, nome=?, trial_start=?, trial_end=?, 
                    status=?, converted=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=?
            """, [email, nome, trial_start, trial_end, status, converted, user_id])

            rows_updated = result.fetchone()[0]

            if rows_updated == 0:
                # Nenhuma linha atualizada, faz INSERT
                tid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trial_users").fetchone()[0]
                con.execute("""
                    INSERT INTO trial_users
                    (id, user_id, email, nome, trial_start, trial_end, status, converted, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, [tid, user_id, email, nome, trial_start, trial_end, status, converted])

            con.close()
            return  # Sucesso, sai da função

        except Exception as e:
            con.close()  # Fecha conexão em caso de erro
            if attempt < max_retries - 1:
                # Espera exponencial com jitter antes de retry
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
                print(f"[trial_users_upsert] tentativa {attempt + 1} falhou, retry em {delay:.2f}s: {e}")
                time.sleep(delay)
            else:
                print(f"[trial_users_upsert] todas as {max_retries} tentativas falharam: {e}")
                # Não propaga a exceção para não quebrar o request
def list_trial_users(status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Tuple]:
    """
    Retorna tuplas: (user_id, email, nome, trial_start, trial_end, status, updated_at)
    Aceita status em {'ativo','expirado','convertido'} ou None.
    """
    con = get_conn()
    base = """
      SELECT user_id, email, nome, trial_start, trial_end, status, updated_at
      FROM trial_users
      WHERE 1=1
    """
    args: List[Any] = []
    if status in ("ativo", "expirado", "convertido"):
        base += " AND status = ?"
        args.append(status)
    base += " ORDER BY trial_end DESC, updated_at DESC LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    rows = con.execute(base, args).fetchall()
    con.close()
    return rows

def trial_users_summary() -> Dict[str, int]:
    """
    Retorna {'ativos': X, 'expirados': Y, 'convertidos': Z}
    """
    con = get_conn()
    rows = con.execute("""
        SELECT status, COUNT(*) FROM trial_users
        GROUP BY status
    """).fetchall()
    con.close()
    out = {"ativos": 0, "expirados": 0, "convertidos": 0}
    for st, n in rows:
        s = (st or "").lower()
        if s.startswith("ativo"):
            out["ativos"] += n or 0
        elif s.startswith("expirado"):
            out["expirados"] += n or 0
        elif s.startswith("convertido"):
            out["convertidos"] += n or 0
    return out

def trial_users_backfill_from_trials():
    """
    Copia dados da tabela trials -> trial_users (apenas quem ainda não está em trial_users).
    """
    with get_conn() as con:
        start_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM trial_users").fetchone()[0] or 0
        con.execute(f"""
            INSERT INTO trial_users (id, user_id, email, nome, trial_start, trial_end, status, converted, created_at, updated_at)
            SELECT
                {start_id} + ROW_NUMBER() OVER () AS id,
                t.user_id,
                COALESCE(u.email, '') AS email,
                NULL AS nome,
                t.started_at AS trial_start,
                t.trial_end  AS trial_end,
                CASE LOWER(COALESCE(t.status,'')) 
                    WHEN 'active'    THEN 'ativo'
                    WHEN 'converted' THEN 'convertido'
                    ELSE 'expirado'
                END AS status,
                CASE LOWER(COALESCE(t.status,'')) WHEN 'converted' THEN TRUE ELSE FALSE END AS converted,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM trials t
            LEFT JOIN users u ON u.id = t.user_id
            WHERE NOT EXISTS (
                SELECT 1 FROM trial_users tu WHERE tu.user_id = t.user_id
            );
        """)

# =========================
# Contatos
# =========================
def contact_save(name: str, email: str, company: str, message: str):
    cid = _next_id("contacts")
    now = datetime.now(timezone.utc)
    con = get_conn()
    con.execute("""
      INSERT INTO contacts (id, name, email, company, message, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    """, [cid, name, email, company, message, now])
    con.close()
    return cid







