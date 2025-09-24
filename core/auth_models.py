# core/db.py
from pathlib import Path
import os
import datetime

# -----------------------------
#  Parte 1 — SQLAlchemy (auth)
# -----------------------------
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use SQLite por padrão; em produção você pode setar DATABASE_URL=postgresql+psycopg2://...
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")

# Para SQLite local, precisa do connect_args; para Postgres não
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ------------------------------------------------
#  Parte 2 — DuckDB (telemetria / rastreamento)
# ------------------------------------------------
import duckdb

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data.duckdb"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Conexão única (leve e sem servidor)
duck_con = duckdb.connect(str(DB_PATH))

# Cria tabelas de telemetria/veículos (se não existirem)
duck_con.execute("""
CREATE TABLE IF NOT EXISTS vehicles (
  client_id TEXT,
  vehicle_id TEXT,
  imei TEXT,
  name TEXT,
  PRIMARY KEY (client_id, vehicle_id)
);
""")

duck_con.execute("""
CREATE TABLE IF NOT EXISTS telemetry (
  client_id TEXT,
  vehicle_id TEXT,
  timestamp TIMESTAMP,
  lat DOUBLE,
  lon DOUBLE,
  speed DOUBLE,
  fuel DOUBLE
);
""")


# ---------------------------------------------
#  Helpers simples para telemetria (opcional)
# ---------------------------------------------
def salvar_telemetria(client_id: str, vehicle_id: str, lat: float, lon: float,
                      speed: float, fuel: float, ts: datetime.datetime | None = None) -> None:
    """Insere uma amostra de telemetria no DuckDB."""
    if ts is None:
        ts = datetime.datetime.utcnow()
    duck_con.execute(
        "INSERT INTO telemetry (client_id, vehicle_id, timestamp, lat, lon, speed, fuel) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [client_id, vehicle_id, ts, float(lat), float(lon), float(speed), float(fuel)]
    )

def obter_posicoes(client_id: str):
    """
    Retorna a última posição conhecida por veículo (para um cliente).
    Saída: lista de dicts [{vehicle_id, lat, lon, speed, fuel, ts}, ...]
    """
    rows = duck_con.execute("""
        SELECT DISTINCT ON (vehicle_id)
               vehicle_id, lat, lon, speed, fuel, timestamp AS ts
        FROM telemetry
        WHERE client_id = ?
        QUALIFY row_number() OVER (PARTITION BY vehicle_id ORDER BY timestamp DESC) = 1
        ORDER BY vehicle_id
    """, [client_id]).fetchall()

    # rows é uma lista de tuplas; converto para dicts para facilitar no JSON
    out = []
    for vehicle_id, lat, lon, speed, fuel, ts in rows:
        out.append({
            "vehicle_id": vehicle_id,
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "speed": float(speed) if speed is not None else None,
            "fuel": float(fuel) if fuel is not None else None,
            "ts": ts.isoformat() if ts is not None else None,
        })
    return out

