"""Cliente HTTP para la API de Biwenger.

Todos los endpoints usados aquí están confirmados contra la API real
(agosto 2026) — ver README.md para el detalle de cómo se descubrió cada uno,
incluidas las cabeceras X-League/X-User no documentadas y el truco de pedir
sub-recursos vía el parámetro "fields".
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

AUTH_BASE = "https://biwenger.as.com/api/v2"
CDN_BASE = "https://cf.biwenger.com/api/v2"

DEFAULT_HEADERS = {
    "X-Lang": "es",
    "Content-Type": "application/json",
    # Sin un User-Agent de navegador, Cloudflare devuelve 403 en cf.biwenger.com
    # (confirmado empíricamente: el User-Agent por defecto de requests falla).
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


class BiwengerAuthError(Exception):
    """Fallo de login o token inválido/caducado."""


@dataclass
class ProbeResult:
    url: str
    status_code: int
    ok: bool
    note: str = ""


class BiwengerClient:
    MY_TEAM_FIELDS = (
        "*,lineup(type,playersID,reservesID,captain,striker,coach,date),"
        "players(id,owner),market,offers,-trophies"
    )

    def __init__(self, email: str, password: str, league_id: Optional[str] = None):
        self._email = email
        self._password = password
        self._league_id = league_id
        self._league_user_id: Optional[int] = None
        self._token: Optional[str] = None
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------
    def login(self) -> str:
        resp = self._session.post(
            f"{AUTH_BASE}/auth/login",
            json={"email": self._email, "password": self._password},
            timeout=15,
        )
        if resp.status_code != 200:
            raise BiwengerAuthError(
                f"Login fallido ({resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        token = data.get("token")
        if not token:
            raise BiwengerAuthError(f"Login sin token en la respuesta: {data}")
        self._token = token
        self._session.headers["Authorization"] = f"Bearer {token}"
        return token

    def _ensure_token(self) -> None:
        if not self._token:
            self.login()

    def _authed_request(
        self, method: str, url: str, retry_on_auth_fail: bool = True, **kwargs
    ) -> requests.Response:
        self._ensure_token()
        resp = self._session.request(method, url, timeout=15, **kwargs)
        if resp.status_code in (401, 403) and retry_on_auth_fail:
            self.login()
            resp = self._session.request(method, url, timeout=15, **kwargs)
        return resp

    # ------------------------------------------------------------------
    # Endpoints confirmados
    # ------------------------------------------------------------------
    def get_account(self) -> dict[str, Any]:
        resp = self._authed_request("GET", f"{AUTH_BASE}/account")
        resp.raise_for_status()
        return resp.json()

    @property
    def league_user_id(self) -> Optional[int]:
        """Tu id de usuario DENTRO de la liga actual (no el id global de la
        cuenta). None hasta que se resuelve el contexto de liga (llama a
        cualquier método de liga primero, p.ej. get_market())."""
        return self._league_user_id

    def _ensure_league_context(self, league_id: Optional[str] = None) -> None:
        """Resuelve y fija las cabeceras X-League / X-User que exigen los
        endpoints de liga (descubierto empíricamente: sin ellas, /board
        devuelve 400 "X-League and X-User headers required").

        X-User NO es el id global de la cuenta (GET /account -> data.account.id):
        es el id de usuario DENTRO de esa liga concreta
        (data.leagues[i].user.id), que Biwenger trata como una identidad
        distinta por liga.
        """
        target_league = league_id or self._league_id
        if self._league_id == target_league and self._league_user_id is not None:
            return  # ya resuelto para esta liga

        account = self.get_account()
        leagues = account.get("data", {}).get("leagues", [])
        if not leagues:
            raise BiwengerAuthError("La cuenta no tiene ninguna liga asociada")

        if target_league:
            match = next((lg for lg in leagues if str(lg.get("id")) == str(target_league)), None)
            if not match:
                raise ValueError(
                    f"League_id {target_league} no aparece en /account. "
                    f"Ligas disponibles: {[lg.get('id') for lg in leagues]}"
                )
        else:
            match = leagues[0]  # si no se especifica, usamos la primera liga de la cuenta

        self._league_id = str(match["id"])
        self._league_user_id = match["user"]["id"]
        self._session.headers["X-League"] = self._league_id
        self._session.headers["X-User"] = str(self._league_user_id)

    def get_competition_data(self, competition: str = "la-liga", score: int = 5) -> dict[str, Any]:
        """Catálogo completo de jugadores + calendario. Público, sin token.

        OJO: no reutilizar self._session directamente. Una vez logueado, la
        sesión lleva Authorization/X-League/X-User (necesarios para
        biwenger.as.com), y si esas cabeceras llegan a cf.biwenger.com
        (un host distinto, detrás de su propio Cloudflare) la petición
        vuelve 403 "Forbidden" — confirmado empíricamente. Por eso aquí se
        manda una petición nueva con solo las cabeceras públicas.
        """
        resp = requests.get(
            f"{CDN_BASE}/competitions/{competition}/data",
            params={"lang": "es", "score": score},
            headers=DEFAULT_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def get_player_price_history(self, slug: str) -> list[list[int]]:
        """Histórico DIARIO de precio de un jugador, ~366 días. Público, sin
        token — mismo host que el catálogo (cf.biwenger.com), así que aplica
        la misma advertencia: nunca usar self._session aquí.

        Aportado por el usuario a partir de las DevTools:
        GET /api/v2/players/la-liga/{slug}?fields=prices
        Cada punto es [fecha, precio] con fecha en formato AAMMDD (p.ej.
        260806 = 6 de agosto de 2026). Es lo que permite calcular el precio
        EXACTO de un jugador en una fecha pasada, en vez de aproximarlo con
        el precio de catálogo de hoy.
        """
        resp = requests.get(
            f"{CDN_BASE}/players/la-liga/{slug}",
            params={"lang": "es", "fields": "prices"},
            headers=DEFAULT_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("prices", [])

    def get_other_user_roster(self, user_id: int, league_id: Optional[str] = None) -> dict[str, Any]:
        """Plantilla de OTRO usuario de tu liga (no solo la tuya). Confirmado
        empíricamente: el mismo truco de "fields" de get_my_team() funciona
        con /user/{id} para cualquier usuario de la liga, no solo el
        logueado — Biwenger no lo restringe a "tu propia plantilla"."""
        self._ensure_league_context(league_id)
        resp = self._authed_request(
            "GET", f"{AUTH_BASE}/user/{user_id}", params={"fields": self.MY_TEAM_FIELDS}
        )
        resp.raise_for_status()
        return resp.json()

    # Tipos de evento del tablón que representan operaciones reales de mercado
    # (fichajes, subastas, cláusulas, cesiones...). Aportado por el usuario a
    # partir de las DevTools: es la query que usa la propia web de Biwenger.
    #
    # OJO, esto corrige un error nuestro: al principio usábamos
    # `type=userMovements`, que NO es "todos los movimientos de la liga" pese
    # al nombre — es un feed recortado a solo los movimientos en los que
    # participa el usuario logueado. Con la lista de tipos de abajo, la
    # misma liga pasó de devolver 12 eventos a 146 (fichajes y subastas de
    # TODOS los usuarios, no solo los tuyos). Ver README para el detalle.
    BOARD_MARKET_TYPES = "transfer,market,adminTransfer,exchange,loan,loanReturn,clauseIncrement"

    def get_league_movements(
        self, league_id: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Una página del histórico de movimientos de mercado de la liga
        (de todos los usuarios, no solo el tuyo)."""
        self._ensure_league_context(league_id)
        resp = self._authed_request(
            "GET",
            f"{AUTH_BASE}/league/{self._league_id}/board",
            params={"type": self.BOARD_MARKET_TYPES, "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", [])

    def get_all_league_movements(
        self, league_id: Optional[str] = None, page_size: int = 50, max_pages: int = 200
    ) -> list[dict[str, Any]]:
        """Pagina get_league_movements hasta que una página vuelve vacía."""
        all_items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            page = self.get_league_movements(league_id=league_id, limit=page_size, offset=offset)
            if not page:
                break
            all_items.extend(page)
            offset += page_size
            time.sleep(0.2)  # cortesía, evitar martillear la API
        return all_items

    def get_round_results(
        self, league_id: Optional[str] = None, page_size: int = 50, max_pages: int = 50
    ) -> list[dict[str, Any]]:
        """Todos los eventos "roundFinished" del tablón: el resumen de cada
        jornada ya jugada, con puntos Y "bonus" (el dinero que ingresa cada
        usuario esa jornada) por usuario. Es la fuente real de ingresos que
        alimenta analysis/economy.py."""
        self._ensure_league_context(league_id)
        all_items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            resp = self._authed_request(
                "GET",
                f"{AUTH_BASE}/league/{self._league_id}/board",
                params={"type": "roundFinished", "offset": offset, "limit": page_size},
            )
            resp.raise_for_status()
            page = resp.json().get("data", [])
            if not page:
                break
            all_items.extend(page)
            offset += page_size
            time.sleep(0.2)
        return all_items

    def probe_endpoints(self, candidates: list[str]) -> list[ProbeResult]:
        """Prueba una lista de rutas relativas a AUTH_BASE con el token actual.

        No lanza excepción por 404s: los recoge todos para que puedas
        decidir cuál (si alguna) es la buena.
        """
        self._ensure_token()
        results = []
        for path in candidates:
            url = f"{AUTH_BASE}{path}"
            try:
                resp = self._session.get(url, timeout=10)
                ok = resp.status_code == 200
                note = ""
                if ok:
                    try:
                        body = resp.json()
                        note = str(list(body.keys()))[:200] if isinstance(body, dict) else str(type(body))
                    except ValueError:
                        note = "respuesta no-JSON"
                results.append(ProbeResult(url=url, status_code=resp.status_code, ok=ok, note=note))
            except requests.RequestException as exc:
                results.append(ProbeResult(url=url, status_code=-1, ok=False, note=str(exc)))
            time.sleep(0.15)
        return results

    def get_my_team(self, league_id: Optional[str] = None) -> dict[str, Any]:
        """Mi plantilla en la liga: alineación actual, jugadores en propiedad
        (con su cláusula) y ofertas de venta abiertas.

        Confirmado con las DevTools (aportado por el usuario a partir del
        tráfico real de la web): GET /api/v2/user con el "fields" compuesto
        de abajo, y las cabeceras X-League/X-User ya fijadas por
        _ensure_league_context (igual que exige /board).
        """
        self._ensure_league_context(league_id)
        resp = self._authed_request(
            "GET", f"{AUTH_BASE}/user", params={"fields": self.MY_TEAM_FIELDS}
        )
        resp.raise_for_status()
        return resp.json()

    def get_market(self, league_id: Optional[str] = None) -> dict[str, Any]:
        """Mercado activo de la liga: jugadores en venta ahora mismo (tanto
        libres del sistema como puestos por otros usuarios) y tus ofertas de
        compra pendientes.

        Confirmado: GET /api/v2/market (top-level, NO bajo /league/{id}) con
        las cabeceras X-League/X-User ya fijadas por _ensure_league_context
        -> data = {status: {balance, maximumBid}, sales: [...], offers: [...]}.
        Cada entrada de "sales" trae player.id, price, until (timestamp) y
        "user" (None si el jugador está libre, o el vendedor si es de otro
        usuario de la liga).

        Nota histórica: antes probamos GET /league/{id}?fields=market, que
        SÍ responde 200 pero con una lista vacía siempre — no es el endpoint
        correcto, simplemente no da error. Lo dejamos documentado aquí para
        no repetir el mismo callejón sin salida.
        """
        self._ensure_league_context(league_id)
        resp = self._authed_request("GET", f"{AUTH_BASE}/market")
        resp.raise_for_status()
        return resp.json()

    def get_league_standings(self, league_id: Optional[str] = None) -> dict[str, Any]:
        """Clasificación de la liga, con detalle enriquecido por usuario.

        Confirmado (aportado por el usuario a partir de las DevTools):
        GET /api/v2/league?include=all&fields=*,standings,tournaments,group,
        settings(description) -> data.standings = [{id, name, icon, points,
        position, lastPositions, teamSize, teamValue, teamValueInc,
        lastTrophy, positionInc, ...}, ...].

        El parámetro clave es "include=all": sin él, "fields=*,standings"
        sigue funcionando pero cada entrada de standings viene recortada a
        solo {id, name, icon, points, position} (lo confirmamos comparando
        la respuesta con y sin include=all). No hace falta indicar el id de
        liga en la ruta: basta con las cabeceras X-League/X-User.
        """
        self._ensure_league_context(league_id)
        resp = self._authed_request(
            "GET",
            f"{AUTH_BASE}/league",
            params={
                "include": "all",
                "fields": "*,standings,tournaments,group,settings(description)",
            },
        )
        resp.raise_for_status()
        return resp.json()
