# tools/init_db.py
from core.db import Base, engine
# IMPORTA os modelos para registrar as tabelas no metadata!
import core.fleet_models  # noqa: F401

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("✅ Tabelas criadas/atualizadas com sucesso.")

