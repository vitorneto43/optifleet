import os
from dotenv import load_dotenv
load_dotenv()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
GOOGLE_MAPS_LANGUAGE = os.getenv("GOOGLE_MAPS_LANGUAGE", "pt-BR")
GOOGLE_MAPS_REGION = os.getenv("GOOGLE_MAPS_REGION", "br")
FALLBACK_AVG_KMH = float(os.getenv("FALLBACK_AVG_KMH", "35"))

