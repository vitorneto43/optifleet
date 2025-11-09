# core/db.py
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta, timezone
import duckdb
import secrets
import os


# =========================
# Config / Conexão
# =========================
DB_PATH = Path("data/optifleet.duckdb")
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

    # Trials
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
    # Trial Users (auditoria de testes) — DuckDB friendly
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

    # após criar trial_users:
    _ensure_col("trial_users", "converted", "ALTER TABLE trial_users ADD COLUMN converted BOOLEAN DEFAULT FALSE;")

    con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trackers_imei
            ON trackers(imei);
        """)

    # Índices úteis (os seus já existentes)
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

def delete_vehicle(client_id: int, vehicle_id: str):
    con = get_conn()
    con.execute("DELETE FROM vehicles WHERE client_id=? AND id=?", [client_id, vehicle_id])
    con.close()

def list_vehicles(client_id: int, q: Optional[str]=None, only: Optional[str]=None):
    con = get_conn()
    base = """
      SELECT
        v.*,
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
    if only in ("online","offline","maintenance"):
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

def tracker_bind_vehicle(client_id: int, tracker_id: str, vehicle_id: str, force: bool = False):
    con = get_conn()
    row = con.execute("""
      SELECT vehicle_id FROM trackers WHERE client_id=? AND tracker_id=?
    """, [client_id, tracker_id]).fetchone()
    if not row:
        token = _gen_token()
        con.execute("""
          INSERT INTO trackers (client_id, tracker_id, secret_token, vehicle_id, status)
          VALUES (?,?,?,?, 'active')
        """, [client_id, tracker_id, token, vehicle_id])
    else:
        current = row[0]
        if current and current != vehicle_id and not force:
            con.close()
            return False
        con.execute("""
          UPDATE trackers SET vehicle_id=? WHERE client_id=? AND tracker_id=?
        """, [vehicle_id, client_id, tracker_id])
    con.close()
    return True

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
    """
    Compat: versões antigas chamavam upsert_tracker(client_id, imei, secret_token, vehicle_id)
    No schema novo, usamos tracker_id (string arbitrária). Aqui mapeamos imei -> tracker_id.
    - Cria se não existir
    - Atualiza secret_token se mudar
    - Opcionalmente vincula vehicle_id
    """
    # garante existência
    row = tracker_get(client_id, imei)  # imei como tracker_id
    if not row:
        con = get_conn()
        con.execute("""
          INSERT INTO trackers (client_id, tracker_id, secret_token, vehicle_id, imei, status)
          VALUES (?, ?, ?, ?, ?, 'active')
        """, [client_id, imei, secret_token, vehicle_id, imei])
        con.close()
        return

    # já existe: atualiza token e vínculo se necessário
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

# --- REMOVA esta linha do topo do db.py ---
# from core.db_connection import get_db

# ... restante do arquivo igual ...

def get_latest_subscription_for_user(user_id: int):
    """Retorna a assinatura mais recente do usuário (via DuckDB, sem db_connection)."""
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
# Trials
# =========================

def list_trial_users(status: Optional[str] = None, q: Optional[str] = None,
                     page: int = 1, per_page: int = 50) -> List[Tuple]:
    """
    Retorna (trial_id, user_id, email, started_at, ends_at, status)
    status é 'active' ou 'expired'
    """
    offset = (page - 1) * per_page
    params = {}
    where = []
    # status opcional
    if status == "active":
        where.append("t.ends_at >= NOW()")
    elif status == "expired":
        where.append("t.ends_at < NOW()")
    # busca opcional por email
    if q:
        where.append("u.email ILIKE %(q)s")
        params["q"] = f"%{q}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = f"""
        SELECT
            t.id, t.user_id, u.email, t.started_at, t.ends_at,
            CASE WHEN t.ends_at >= NOW() THEN 'active' ELSE 'expired' END AS status
        FROM trials t
        JOIN users u ON u.id = t.user_id
        {where_sql}
        ORDER BY t.ends_at DESC
        LIMIT %(per_page)s OFFSET %(offset)s
    """
    params.update({"per_page": per_page, "offset": offset})

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()

def trial_users_summary() -> Dict[str, int]:
    """
    Retorna {'ativos': X, 'expirados': Y, 'proximos_de_expirar': Z}
    proximos_de_expirar = ends_at entre agora e 3 dias
    """
    sql = """
        SELECT
          COUNT(*) FILTER (WHERE ends_at >= NOW()) AS ativos,
          COUNT(*) FILTER (WHERE ends_at < NOW()) AS expirados,
          COUNT(*) FILTER (
            WHERE ends_at BETWEEN NOW() AND NOW() + INTERVAL '3 days'
          ) AS proximos_de_expirar
        FROM trials
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return {
            "ativos": row[0] or 0,
            "expirados": row[1] or 0,
            "proximos_de_expirar": row[2] or 0,
        }
def trial_users_upsert(user_id:int, email:str, nome:str|None, trial_start, trial_end, converted:bool):
    with get_conn() as con:
        # gera id sequencial opcional
        tid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trial_users").fetchone()[0]
        # tenta encontrar registro atual do usuário
        row = con.execute("""
            SELECT id FROM trial_users WHERE user_id = ?
        """, [user_id]).fetchone()
        if row:
            con.execute("""
                UPDATE trial_users
                   SET email=?, nome=?, trial_start=?, trial_end=?, 
                       status = CASE 
                                  WHEN ? THEN 'convertido' 
                                  ELSE 'ativo' 
                                END,
                       converted=?, updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=?
            """, [email, nome, trial_start, trial_end, converted, converted, user_id])
        else:
            con.execute("""
                INSERT INTO trial_users
                  (id, user_id, email, nome, trial_start, trial_end, status, converted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, [tid, user_id, email, nome, trial_start, trial_end, 'convertido' if converted else 'ativo', converted])

def expire_trial(trial_id: int) -> None:
    """Força o fim do trial (seta ends_at = NOW())"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE trials SET ends_at = NOW() WHERE id = %s", (trial_id,))
        conn.commit()
def create_trial(user_id: int, plan: str, vehicles: int, days: int = 14) -> int:
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
      ORDER BY trial_end DESC
      LIMIT 1
    """, [user_id]).fetchone()
    con.close()
    return row



def mark_trial_converted(trial_id: int):
    con = get_conn()
    con.execute("UPDATE trials SET status='converted' WHERE id=?", [trial_id])
    con.close()

# =========================
# Trial Users (auditoria)
# =========================
def _status_from_dates(start: datetime, end: datetime, converted: bool = False) -> str:
    if converted:
        return "convertido"
    now = datetime.now(timezone.utc)
    return "ativo" if now <= end else "expirado"

# core/db.py (trechos essenciais)



def list_trial_users(
    as_admin: bool,
    user_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict]:
    """
    Se as_admin=True -> retorna todos os trials (paginado).
    Senão -> retorna apenas do user_id.
    """
    with engine.connect() as conn:
        if as_admin:
            q = text("""
                SELECT t.id, t.user_id, u.email, u.name,
                       t.started_at, t.expires_at,
                       CASE WHEN NOW() > t.expires_at THEN 0
                            ELSE EXTRACT(EPOCH FROM (t.expires_at - NOW()))/86400
                       END AS days_left
                FROM trials t
                JOIN users u ON u.id = t.user_id
                ORDER BY t.started_at DESC
                LIMIT :limit OFFSET :offset
            """)
            rows = conn.execute(q, {"limit": limit, "offset": offset}).mappings().all()
        else:
            if not user_id:
                return []
            q = text("""
                SELECT t.id, t.user_id, u.email, u.name,
                       t.started_at, t.expires_at,
                       CASE WHEN NOW() > t.expires_at THEN 0
                            ELSE EXTRACT(EPOCH FROM (t.expires_at - NOW()))/86400
                       END AS days_left
                FROM trials t
                JOIN users u ON u.id = t.user_id
                WHERE t.user_id = :user_id
                ORDER BY t.started_at DESC
                LIMIT :limit OFFSET :offset
            """)
            rows = conn.execute(q, {"user_id": user_id, "limit": limit, "offset": offset}).mappings().all()
    # arredonda days_left pra cima (quem tem 0.3 dia ainda tem hoje)
    for r in rows:
        r["days_left"] = int(max(0, (r["days_left"] or 0) + 0.999))
    return rows

def trial_users_summary(as_admin: bool, user_id: Optional[str] = None) -> Dict:
    with engine.connect() as conn:
        base = """
            FROM trials t
            WHERE 1=1
        """
        params = {}
        if not as_admin:
            base += " AND t.user_id = :user_id"
            params["user_id"] = user_id

        total = conn.execute(text(f"SELECT COUNT(*) {base}"), params).scalar() or 0
        ativos = conn.execute(text(f"SELECT COUNT(*) {base} AND NOW() <= t.expires_at"), params).scalar() or 0
        expirados = total - ativos
        return {"total": total, "ativos": ativos, "expirados": expirados}

def get_latest_trial_for_user(user_id: int):
    """Último trial do usuário (ativo ou não)."""
    con = get_conn()
    row = con.execute("""
        SELECT id, user_id, email, nome, trial_start, trial_end, status, created_at, updated_at
        FROM trial_users
        WHERE user_id = ?
        ORDER BY trial_end DESC
        LIMIT 1
    """, [user_id]).fetchone()
    con.close()
    return row



def mark_trial_converted_by_user(user_id: int):
    """Conveniente p/ quando o usuário assina e converte o trial."""
    con = get_conn()
    con.execute("""
        UPDATE trial_users
           SET status = 'convertido', updated_at = CURRENT_TIMESTAMP
         WHERE user_id = ? AND status <> 'convertido'
    """, [user_id])
    con.close()
def trial_users_backfill_from_trials():
    """
    Copia dados da tabela trials -> trial_users (apenas quem ainda não está em trial_users).
    """
    with get_conn() as con:
        # ponto de partida para o id sequencial
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

