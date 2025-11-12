# core/providers/geocoding.py
import requests
import time
import re
from functools import lru_cache

# =============================
#   ERROS ESPECÍFICOS
# =============================
class GeocodingError(Exception):
    pass

class GeocodingAmbiguous(Exception):
    """Endereço encontrado, mas impreciso demais (ex.: cidade sem rua)."""
    pass


# =============================
#  SANITIZAÇÃO E NORMALIZAÇÃO
# =============================
def normalize_address(address: str) -> str:
    """Remove lixo, espaços repetidos e normaliza o endereço."""
    if not address:
        return ""

    txt = address.strip()
    # Remove múltiplos espaços:
    txt = re.sub(r"\s+", " ", txt)

    # Corrige vírgulas grudadas
    txt = txt.replace(", ", ",").replace(",", ", ")

    return txt


# =============================
#   PROVEDORES DE GEOCODIFICAÇÃO
#   Ordem: Nominatim → Geocode.maps.co → Google (opcional com API KEY)
# =============================
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MAPSCO_URL = "https://geocode.maps.co/search"


def _call_nominatim(address: str):
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "br"
    }
    headers = {
        "User-Agent": "OptiFleet/1.0 (contato@optifleet.com.br)"
    }
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
    if r.status_code == 429:
        time.sleep(1)  # rate limit
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


def _call_mapsco(address: str):
    params = {
        "q": address,
        "api_key": "free"  # maps.co não exige chave para uso moderado
    }
    r = requests.get(MAPSCO_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


# =============================
#     VALIDAÇÃO DOS RESULTADOS
# =============================
def _validate_geodata(item: dict, address: str):
    """Garante lat/lon válidos e detecta imprecisão (ex.: cidade sem rua)."""

    if item is None:
        raise GeocodingError(f"Endereço não encontrado: {address}")

    lat = float(item.get("lat"))
    lon = float(item.get("lon"))

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise GeocodingError(f"Coordenadas inválidas retornadas para: {address}")

    # Detectar resultados genéricos: ex.: "São Paulo" sem número
    disp = it
