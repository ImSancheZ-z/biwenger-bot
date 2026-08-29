"""Carga configuración desde variables de entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# En Streamlit Community Cloud no hay .env: las credenciales se configuran
# como Secrets y llegan aquí via st.secrets, no como variables de entorno.
try:
    import streamlit as st

    for _key in ("BIWENGER_EMAIL", "BIWENGER_PASSWORD", "BIWENGER_LEAGUE_ID"):
        if not os.getenv(_key) and _key in st.secrets:
            os.environ[_key] = str(st.secrets[_key])
except Exception:
    pass


@dataclass
class Settings:
    email: str
    password: str
    league_id: str | None


def load_settings() -> Settings:
    email = os.getenv("BIWENGER_EMAIL")
    password = os.getenv("BIWENGER_PASSWORD")
    league_id = os.getenv("BIWENGER_LEAGUE_ID") or None
    if not email or not password:
        raise RuntimeError(
            "Faltan BIWENGER_EMAIL / BIWENGER_PASSWORD. "
            "Copia .env.example a .env y rellena tus credenciales."
        )
    return Settings(email=email, password=password, league_id=league_id)
