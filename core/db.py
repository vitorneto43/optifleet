# core/db.py
import os
import pathlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Lê a URL do banco a partir do .env, padrão é SQLite local
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./data/opti.db")

# Se for SQLite, cria a pasta ./data
if DB_URL.startswith("sqlite"):
    pathlib.Path("./data").mkdir(parents=True, exist_ok=True)

# Configura o engine
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    echo=False,  # pode ativar True para debug de SQL
    future=True
)

# Sessão e Base para modelos
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()



