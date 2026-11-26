"""URLs públicas de fotos y escudos en el CDN de Biwenger.

No son peticiones a la API de datos: el navegador pide la imagen directamente
a cdn.biwenger.com a partir del id que ya trae el catálogo o la clasificación.
"""
from __future__ import annotations

from typing import Optional

CDN = "https://cdn.biwenger.com"


def player_photo_url(player_id) -> Optional[str]:
    if player_id is None:
        return None
    return f"{CDN}/i/p/{int(player_id)}.png"


def team_shield_url(team_id) -> Optional[str]:
    if team_id is None:
        return None
    return f"{CDN}/i/t/{int(team_id)}.png"


def user_avatar_url(icon, user_id=None) -> Optional[str]:
    """Normaliza el campo `icon` de la clasificación.

    Biwenger puede devolver una URL absoluta (a veces de Facebook), una ruta
    del CDN o nada. Si no hay icono, se usa el retrato por id de usuario de
    liga, que es el mismo recurso que pinta la web.
    """
    if isinstance(icon, str):
        value = icon.strip()
        if value.startswith("https://") or value.startswith("http://"):
            return value
        if value.startswith("//"):
            return "https:" + value
        if value.startswith("/"):
            return CDN + value
        if value.isdigit():
            return f"{CDN}/i/u/{value}.png"
    if isinstance(icon, int) and not isinstance(icon, bool):
        return f"{CDN}/i/u/{icon}.png"
    if user_id is not None:
        return f"{CDN}/i/u/{int(user_id)}.png"
    return None
