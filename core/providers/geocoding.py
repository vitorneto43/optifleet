# core/providers/geocoding.py

import requests
import time
import re
from functools import lru_cache


class GeocodingError(Exception):
    """Erro genérico de geocodificação."""
    pass


class GeocodingAmbiguous(GeocodingError):
    """Endereço encontrado, mas impreciso demais (ex.: só cidade, sem rua/número)."""
    pass


def normalize_address(address: str) -> str:
    """Normaliza o texto do endereço para evitar lixo."""
    if not address:
        return ""
    txt = address.strip()
    # Remove espaços múltiplos
    txt = re.sub(r"\s+", " ", txt)
    # Ajusta vírgulas grudadas
    txt = txt.replace(", ", ",").replace(",", ", ")
    return txt


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MAPSCO_URL = "https://geocode.maps.co/search"


def _call_nominatim(address: str):
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "br",
    }
    headers = {
        "User-Agent": "OptiFleet/1.0 (contato@optifleet.com.br)"
    }
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=10)
    if r.status_code == 429:
        time.sleep(1)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


def _call_mapsco(address: str):
    params = {
        "q": address,
        "api_key": "free",  # uso moderado sem chave
    }
    r = requests.get(MAPSCO_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


def _validate_geodata(item: dict, address: str) -> tuple[float, float]:
    """Garante lat/lon válidos e detecta resultado impreciso."""
    if item is None:
        raise GeocodingError(f"Endereço não encontrado: {address}")

    lat = float(item.get("lat"))
    lon = float(item.get("lon"))

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise GeocodingError(f"Coordenadas inválidas retornadas para: {address}")

    display = (item.get("display_name") or "").lower()

    # Exemplo de heurística de resultado genérico:
    # se a pessoa mandou "Recife" e o resultado é só a cidade inteira
    if "brazil" in display and (", recife" in display or ", são paulo" in display):
        # se o endereço original não tinha número, provavelmente é genérico
        if re.match(r"^[^0-9]*$", address):
            raise GeocodingAmbiguous(
                f"O endereço '{address}' é muito genérico. "
                "Inclua rua, número, bairro e cidade."
            )

    return lat, lon


@lru_cache(maxsize=2000)
def geocode_address(address: str) -> tuple[float, float]:
    """
    Converte um endereço de texto em (lat, lon).
    Usa múltiplos provedores e cache para ficar robusto em produção.
    """

    if not address or not address.strip():
        raise GeocodingError("Endereço vazio.")

    address = normalize_address(address)

    # ---- PROVEDOR 1: NOMINATIM (OSM) ----
    try:
        item = _call_nominatim(address)
        if item:
            return _validate_geodata(item, address)
    except Exception:
        # log poderia ir para logger em produção
        pass

    # ---- PROVEDOR 2: MAPS.CO (fallback) ----
    try:
        item = _call_mapsco(address)
        if item:
            return _validate_geodata(item, address)
    except Exception:
        pass

    # (Opcional) PROVEDOR 3: Google Maps API, se quiser no futuro

    # Se nenhum provedor retornou algo utilizável:
    raise GeocodingError(f"Endereço não encontrado: {address}")
