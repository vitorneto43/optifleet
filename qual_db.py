# qual_db.py
import os, pathlib
DB_PATH = os.getenv("DB_PATH", "opti_fleet.db")
print("DB_PATH =", DB_PATH)
print("ABS_PATH =", pathlib.Path(DB_PATH).resolve())
print("EXISTS? ", pathlib.Path(DB_PATH).exists())
