# config.py
import os

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_MAPS_LANGUAGE = os.getenv("GOOGLE_MAPS_LANGUAGE", "pt-BR")
GOOGLE_MAPS_REGION   = os.getenv("GOOGLE_MAPS_REGION", "BR")
FALLBACK_AVG_KMH     = float(os.getenv("FALLBACK_AVG_KMH", "35"))
ALLOW_GEOCODE_OFF    = os.getenv("ALLOW_GEOCODE_OFF") == "1"   # << NOVO


