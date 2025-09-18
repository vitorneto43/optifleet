# tools/reset_db.py
from sqlalchemy import MetaData, text
from sqlalchemy.exc import ProgrammingError
from core.db import engine, Base

# garanta que TODOS os modelos foram importados (para o create_all depois)
import core.fleet_models  # noqa: F401

def hard_reset():
    md = MetaData()
    md.reflect(bind=engine)

    dialect = engine.dialect.name  # "postgresql", "sqlite", etc.

    with engine.begin() as conn:
        if dialect == "postgresql":
            # Derruba com CASCADE usando SQL bruto (ordem não importa com CASCADE)
            for table in md.sorted_tables:
                schema = f'"{table.schema}".' if table.schema else ""
                qualified = f'{schema}"{table.name}"'
                try:
                    conn.exec_driver_sql(f'DROP TABLE IF EXISTS {qualified} CASCADE;')
                    print(f"🔻 DROP {qualified}")
                except ProgrammingError as e:
                    print(f"⚠️  Falha ao dropar {qualified}: {e}")
        else:
            # Para SQLite e outros, usar o drop_all padrão (respeita FKs conhecidas)
            Base.metadata.drop_all(bind=conn)
            print("🔻 DROP ALL (drop_all)")

        # recria do zero
        Base.metadata.create_all(bind=conn)
        print("✅ Banco recriado com sucesso.")

if __name__ == "__main__":
    hard_reset()

