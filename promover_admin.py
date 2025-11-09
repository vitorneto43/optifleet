# promover_admin.py
import os, sqlite3, sys, pathlib

if len(sys.argv) < 2:
    print("Uso: python promover_admin.py vitor_veiga@yahoo.com.br")
    sys.exit(1)

email = sys.argv[1]
DB_PATH = os.getenv("DB_PATH", "opti_fleet.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Confirma se tabela e coluna existem
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
assert cur.fetchone(), "Tabela 'users' não existe. Rode init_db_minimo.py primeiro."

# Garante coluna is_admin
cur.execute("PRAGMA table_info(users)")
cols = [r[1] for r in cur.fetchall()]
if "is_admin" not in cols:
    cur.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;")

# Se o e-mail não existir, cria um usuário vazio (você pode depois ajustar a senha pelo fluxo normal)
cur.execute("SELECT id, is_admin FROM users WHERE email = ?", (email,))
row = cur.fetchone()
if not row:
    cur.execute("INSERT INTO users (email, is_admin) VALUES (?, 1)", (email,))
    print(f"Usuário criado e promovido: {email}")
else:
    cur.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
    print(f"Usuário promovido a admin: {email}")

conn.commit()
conn.close()
print("DB:", pathlib.Path(DB_PATH).resolve())


