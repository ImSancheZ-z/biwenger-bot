# Biwenger Bot

Asistente local de análisis para Biwenger (LaLiga Fantasy): detecta jugadores
con buena relación puntos/precio, guarda snapshots diarios de precios y los
muestra en un dashboard de Streamlit.

## Instalación

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env       # y rellena tus credenciales reales
```

`.env` nunca se sube al repo (está en `.gitignore`). Las credenciales se
cargan con `python-dotenv`, nunca hardcodeadas en el código.

## Verificar que el login funciona

```bash
python scripts/verify_login.py
```

Hace login contra `POST /api/v2/auth/login`, guarda el token en memoria y
llama a `GET /api/v2/account`. Imprime las ligas asociadas a tu cuenta (de
ahí sacarás tu `BIWENGER_LEAGUE_ID` si quieres fijarlo en `.env`).

## Descargar snapshot diario

```bash
python scripts/fetch_daily.py
```

Descarga el catálogo público de jugadores de LaLiga (sin necesidad de login)
y guarda un snapshot del día en `biwenger_data.sqlite3`. Pensado para
ejecutarse una vez al día (Task Scheduler, cron, o más adelante GitHub
Actions).

## Dashboard

```bash
streamlit run app.py
```

Pestañas:
- **Catálogo LaLiga**: todos los jugadores de LaLiga, filtrable por
  posición/estado/equipo.
- **Chollos (recomendaciones)**: ranking por el motor de `analysis/engine.py`.
- **Mi plantilla**: alineación actual, cláusulas y qué jugadores tienes en venta.
- **Mercado activo (liga)**: jugadores en venta ahora mismo en tu liga
  (libres del sistema o de otros usuarios), con ratio puntos/precio, y tus
  ofertas de compra pendientes.
- **Cláusulas** (`analysis/clauses.py`): jugadores de OTROS usuarios de tu
  liga cuya cláusula ya se puede pagar o se podrá en los próximos días
  (elegible), valorados con el mismo score, tendencia de precio y
  comparación con precio de mercado que el resto del dashboard. Sale de
  `owner.clause`/`owner.clauseLockedUntil` en la plantilla de cada usuario
  (mecánica "clause: steal" de tu liga). Filtrable por posición y por
  propietario actual. Incluye una segunda tabla de **arbitraje**: jugadores
  con cláusula ≤ precio de mercado actual Y precio subiendo día a día —
  clausularlos y venderlos al mercado al día siguiente deja beneficio casi
  seguro, sea o no un buen jugador (estrategia de trading, no de
  rendimiento). Una tercera tabla ordena a los jugadores que están subiendo
  de precio por una combinación de tres rankings (subida, score, descuento
  vs. mercado) en vez de por valor absoluto, para que ninguna señal domine
  solo por tener una escala más grande. Las columnas de tendencia se
  colorean en verde/rojo y muestran tanto el % como el valor en euros/día.
- **Clasificación**: posición y puntos de todos los usuarios de tu liga.
- **Movimientos de liga**: histórico de fichajes/ventas de tu liga.
- **Fichajes por usuario** (`analysis/scouting.py`): qué compra/vende cada
  rival, con tendencias detectadas en texto (agresividad en subastas,
  posición favorita, si paga por encima o por debajo del precio de mercado
  actual...). Haz clic en una fila de la tabla para ver el historial
  completo de ese usuario.
- **Economía** (`analysis/economy.py` + `analysis/initial_budget.py`):
  reconstruye el saldo estimado de CADA usuario de la liga, partiendo de la
  regla real de esta liga (40.000.000 € menos el valor — al precio exacto
  del día del reparto — de la plantilla inicial de cada uno) y sumando/
  restando lo cobrado cada jornada y lo gastado/ingresado en fichajes.
  Verificado contra la cuenta real del usuario con un margen de ~600.000 €
  sobre una plantilla de ~26M€ (ver sección de más abajo). La primera carga
  tarda ~1 minuto (descarga el histórico de precios de cada jugador del
  reparto de cada usuario); luego queda cacheada 24h.

Todas las pestañas de liga usan `BIWENGER_LEAGUE_ID` de tu `.env` (si lo
dejas vacío, el cliente coge automáticamente la primera liga de tu cuenta).

## Estado de los endpoints

### Confirmados y en uso

| Endpoint | Método | Auth | Uso en el proyecto |
|---|---|---|---|
| `biwenger.as.com/api/v2/auth/login` | POST | no | `BiwengerClient.login()` |
| `biwenger.as.com/api/v2/account` | GET | sí | `BiwengerClient.get_account()` |
| `cf.biwenger.com/api/v2/competitions/la-liga/data` | GET | no | `BiwengerClient.get_competition_data()` — catálogo de jugadores + calendario |
| `biwenger.as.com/api/v2/league/{id}/board?type=transfer,market,adminTransfer,exchange,loan,loanReturn,clauseIncrement` | GET | sí | `BiwengerClient.get_league_movements()` — histórico de mercado de TODA la liga |
| `biwenger.as.com/api/v2/user?fields=*,lineup(...),players(id,owner),market,offers,-trophies` | GET | sí | `BiwengerClient.get_my_team()` — plantilla propia, alineación, cláusulas |
| `biwenger.as.com/api/v2/league?include=all&fields=*,standings,tournaments,group,settings(description)` | GET | sí | `BiwengerClient.get_league_standings()` — clasificación enriquecida |
| `biwenger.as.com/api/v2/market` | GET | sí | `BiwengerClient.get_market()` — mercado activo: jugadores en venta + tus ofertas de compra pendientes |
| `biwenger.as.com/api/v2/user/{id}?fields=*,lineup(...),players(id,owner),market,offers,-trophies` | GET | sí | `BiwengerClient.get_other_user_roster()` — plantilla de CUALQUIER usuario de la liga, no solo la tuya |
| `biwenger.as.com/api/v2/league/{id}/board?type=roundFinished` | GET | sí | `BiwengerClient.get_round_results()` — bono (dinero) que cobra cada usuario cada jornada |
| `cf.biwenger.com/api/v2/players/la-liga/{slug}?fields=prices` | GET | no | `BiwengerClient.get_player_price_history()` — histórico DIARIO de precio de un jugador, ~366 días |

**Los tres endpoints que estaban pendientes ya están confirmados y
funcionando** contra tu cuenta real (probado el 2026-08-28):

- **Mi plantilla**: no fue por prueba y error — el endpoint real
  (`GET /api/v2/user?fields=...`) lo sacó el usuario directamente de las
  DevTools del navegador mientras miraba la pestaña de su plantilla, y
  encaja con el resto de la API una vez confirmado.
- **Clasificación**: primero la encontramos por prueba y error
  (`GET /league/{id}?fields=*,standings`, ya que la ruta intuitiva
  `/league/{id}/standings` no existe — 400), pero esa versión recortaba
  cada entrada a solo `{id, name, icon, points, position}`. Después el
  usuario aportó la versión real de las DevTools:
  `GET /league?include=all&fields=*,standings,tournaments,group,settings(description)`
  — el parámetro clave que faltaba era `include=all`; sin él, Biwenger
  devuelve 200 igualmente pero con los datos de standings recortados, sin
  avisar de que faltan campos. Con `include=all` cada usuario trae también
  `lastPositions` (posiciones en las 2 últimas jornadas), `teamSize`,
  `teamValue`, `teamValueInc` (variación de valor de plantilla),
  `lastTrophy` y `positionInc`. Tampoco hace falta poner el id de liga en
  la ruta (`/league` a secas funciona igual que `/league/{id}`): las
  cabeceras X-League/X-User ya identifican la liga.
- **Mercado de liga**: la primera aproximación (`GET /league/{id}?fields=market`)
  fue un callejón sin salida — responde 200 pero con lista vacía siempre,
  no es el endpoint real (lo dejamos documentado en el docstring de
  `get_market()` para no repetir el error). El endpoint correcto, aportado
  por el usuario a partir de las DevTools, es `GET /api/v2/market`
  **top-level** (no bajo `/league/{id}`) — se apoya en las mismas cabeceras
  `X-League`/`X-User`. Devuelve `sales` (jugadores en venta, libres del
  sistema o de otros usuarios, con precio y hasta cuándo) y `offers` (tus
  pujas de compra pendientes). En tu liga había 29 jugadores en venta en el
  momento de la prueba.

**Corrección posterior importante — `type=userMovements` era el endpoint
equivocado**, y esta vez sí fue un error nuestro, no un endpoint sin
descubrir. El nombre "userMovements" sonaba a "todos los movimientos" pero
en realidad es un feed recortado a solo los eventos donde participa el
usuario logueado. El usuario encontró en las DevTools la query real que usa
la propia web para el tablón completo:
`GET /league/{id}/board?type=transfer,market,adminTransfer,exchange,loan,loanReturn,clauseIncrement`
(paginada con `offset`/`limit` normalmente). En la misma liga, esto pasó de
devolver 12 eventos (todos con el usuario logueado de por medio) a **146**
— incluyendo fichajes y subastas de TODOS los usuarios entre sí. También
hay un endpoint separado `type=clauses` que filtra solo los pagos de
cláusula, redundante con filtrar client-side por
`content[].type == "clause"` sobre el tablón completo (no lo usamos aparte).
Tipos de evento nuevos que aparecieron con la query completa:
`clauseIncrement` (la cláusula de un jugador sube sola con el tiempo, no es
una operación — se ignora en `parse_movements`) y `adminTransfer`
(movimiento forzado manualmente por un administrador de la liga, con
"admin" y "reason" en vez de "from").

Esta corrección también mejoró mucho la fiabilidad del estimador de puja
(`analysis/bidding.py`): pasamos de 1 subasta competida detectada a 56,
porque cada evento "market" agrupa varias ventas simultáneas de jugadores
libres, cada una con su propio array de pujas.

**Bug real encontrado y corregido: `get_competition_data()` podía devolver
403 tras haber usado el cliente para algo de liga.** `cf.biwenger.com`
(el catálogo público) y `biwenger.as.com` (todo lo demás) son hosts
distintos, cada uno detrás de su propio Cloudflare. Si reutilizas la misma
instancia de `BiwengerClient` para ambas cosas, la sesión ya lleva
`Authorization`/`X-League`/`X-User` de las llamadas de liga, y mandar esas
cabeceras a `cf.biwenger.com` hace que Cloudflare devuelva 403 — confirmado
reproduciéndolo con una petición manual. `get_competition_data()` ahora usa
siempre una petición nueva con solo las cabeceras públicas, nunca la sesión
autenticada. Si en el futuro vuelve a aparecer un 403 ahí, es el primer
sitio donde mirar.

**Nuevo: `GET /board?type=roundFinished`** trae, para cada usuario, cuánto
ingresa cada jornada jugada (campo `bonus`, con el desglose en `reason`:
puntos, posición en la jornada, alineación ideal, portería a cero...). Es
la base de la pestaña "Economía" — ver más abajo.

**Cabecera obligatoria no documentada**: todos los endpoints de liga
(`/board`, `/user`, `/league/{id}`) devuelven `400 "X-League and X-User
headers required"` si no se mandan las cabeceras `X-League` (id de liga) y
`X-User`. Importante: **`X-User` NO es el id global de la cuenta**
(`account.data.account.id`) — es el id de usuario **dentro de esa liga
concreta** (`account.data.leagues[i].user.id`), que Biwenger trata como una
identidad distinta por liga (de hecho el nombre de usuario puede ser
distinto del nombre de la cuenta — en pruebas, cuenta "Jose Rueda" con
usuario de liga "Lamin Gamal"). `BiwengerClient._ensure_league_context()`
resuelve esto automáticamente llamando a `/account` la primera vez.

Nota de implementación adicional descubierta al probar contra la API real:
**`cf.biwenger.com` está detrás de Cloudflare y devuelve 403 si la petición
no lleva un `User-Agent` de navegador** (el User-Agent por defecto de
`requests` es bloqueado). El cliente ya manda uno fijo en
`DEFAULT_HEADERS`. Si en el futuro cambia el bloqueo, es el primer sitio
donde mirar.

También: el campo `fitness` de cada jugador puede mezclar enteros con
`"discarded"` o `null` cuando el jugador no pudo puntuar esa jornada — el
parser (`biwenger/parse.py`) y el modelo (`Player.recent_form`) ya lo tienen
en cuenta.

## Motor de recomendación (v1)

```
score = puntos_por_millon
      + 1.5 * forma_reciente
      - 3.0 * dificultad_normalizada(-1..1)
```

- `puntos_por_millon`: puntos totales de la temporada por cada millón de
  precio. Señal principal.
- `forma_reciente`: media de puntos de las últimas jornadas jugadas.
- `dificultad_normalizada`: `(difficulty.rating - 50) / 50`, así un rival
  medio no penaliza, uno muy difícil penaliza al máximo y uno muy fácil da
  un empujón extra.

Excluye por defecto lesionados/sancionados/descartados (`analysis/engine.py`,
`AVAILABLE_STATUSES`). Es una v1 deliberadamente simple para iterar — los
pesos (`FORM_WEIGHT`, `DIFFICULTY_WEIGHT`) están para ajustarlos a ojo.

## Estimador de puja (v1) — `analysis/bidding.py`

En la pestaña "Mercado activo (liga)", cada jugador en venta trae una
recomendación de acción y de cantidad. Diferencia dos mecánicas reales de
Biwenger:

- **Venta directa de otro usuario** (precio fijo): no hay subasta, gana
  quien compra antes. Recomendación = comprar ya si el `score` es bueno, o
  evitar si no compensa.
- **Jugador libre del sistema** (subasta a ciegas, con fecha límite): la
  puja sugerida = precio de salida × prima estimada, con un +10% extra si
  te falta esa posición en tu plantilla, siempre topada a tu saldo y al
  `maximumBid` que impone Biwenger.

La "prima estimada" sale de reconstruir subastas competidas reales de tu
liga a partir del tablón completo (`BiwengerClient.BOARD_MARKET_TYPES`, que
trae **todas** las pujas de cada subasta, no solo la ganadora): `premio =
puja_ganadora / segunda_mejor_puja`. Si tu liga aún no tiene suficientes
subastas competidas registradas (`MIN_SAMPLES_FOR_TRUST = 3`), se mezcla
con un supuesto genérico del 8% en vez de fiarse de una muestra minúscula.

**Tendencia de precio** (`price_trend_pct_per_day()`): usa el histórico
diario de precio del jugador (`get_player_price_history()`) para calcular
la variación media de los últimos 7 días. Si cae con fuerza (≤ -1,5%/día)
la recomendación es directamente **evitar**, por buen ratio puntos/precio
que tenga hoy — un activo que pierde valor cada día es mala inversión
aunque hoy parezca un chollo. Caídas más suaves recortan un 5% la puja
sugerida; subidas fuertes (≥ +1%/día) añaden un 8%, porque mañana costará
más caro. La pestaña también tiene un filtro (radio) para ver solo
jugadores libres del sistema, solo de otros usuarios, o todos.

Nota sobre el alcance: la dificultad del próximo rival ya estaba incluida
en el `score` (vía `analysis/engine.py`), pero solo del **próximo**
partido — la API pública de Biwenger no expone un calendario de varias
jornadas, así que no se puede mirar más allá sin otra fuente de datos.

**Tope de prima (+12% por defecto, +20% excepcional)**: la fórmula
(prima histórica × necesidad de posición × tendencia) podía llegar a +30%
combinando bonus menores, algo que no debería ser "normal". Ahora se
recorta a +12% salvo que el jugador sea de un **equipo top-5** (por valor
total de plantilla — más estable que los puntos de las primeras
jornadas, donde salían equipos irregulares por ruido de muestra pequeña)
**y** sea **titular habitual** (aproximado como estar entre los 11 con
más puntos de su equipo; no hay dato real de "once probable" de rivales
vía API). Ver `compute_top_teams_by_value()` y `compute_likely_starters()`
en `analysis/bidding.py`.

**Tope de saldo (40% máximo por jugador)**: nunca se recomienda poner más
del 40% del saldo disponible en un único jugador (pujando o comprando
directo), para no vaciar el margen de maniobra del resto de la jornada —
comprobado: hizo que un jugador de equipo top-5 y titular (Baena, 8,2M€)
quedara en "evitar" pese a cumplir el resto de criterios, porque su precio
superaba ese tope.

**Limitación importante, no un descuido**: no se usa el saldo real de tus
rivales porque tu liga tiene la configuración `settings.balance = "hidden"`
(la fija el administrador de la liga) — ni la propia app de Biwenger se lo
muestra a nadie, no hay ningún endpoint que lo revele. El estimador lo
compensa parcialmente con el histórico real de cuánto han pujado tus
rivales en el pasado, que sí es público dentro de tu liga.

## Economía — `analysis/economy.py` + `analysis/initial_budget.py`

Reconstruye una línea temporal de ingresos y gastos por usuario, combinando
dos fuentes reales:

- **Ingresos**: `GET /board?type=roundFinished` trae, para cada jornada
  jugada, el `bonus` (dinero) que cobra cada usuario.
- **Gastos/ingresos de mercado**: las mismas compras/ventas que ya usa
  `analysis/scouting.py`.

`analysis/economy.reconstruct_balances()` parte de un **saldo inicial
conocido** y va sumando/restando cada evento hacia adelante — necesita ese
punto de partida para dar un saldo absoluto en vez de solo un flujo neto.

### De dónde sale el saldo inicial: la regla de los 40M

El usuario aportó la regla real de su liga: cada usuario empieza la
temporada con **40.000.000 €** menos el valor de los jugadores que se le
asignan en el reparto inicial. `analysis/initial_budget.py` la implementa
así:

1. **Detectar qué jugadores fueron del reparto inicial de un usuario**: los
   de su plantilla actual sin `owner.price` en `GET /user/{id}` (nunca
   comprados/vendidos por él) + los que vendió sin haberlos comprado nunca
   (los tenía desde el reparto). Se cruza por `player_id`, no por nombre.
2. **La fecha del reparto** es la misma para TODA la liga — se calcula UNA
   vez a partir de tu propia plantilla y se reutiliza para todos. (Bug real
   que esto corrige: al principio la recalculábamos por usuario a partir de
   su plantilla actual, y fallaba con usuarios que ya habían vendido TODOS
   sus jugadores originales — ningún jugador sin `owner.price` del que
   sacar la fecha, así que su reparto entero caía como "sin precio
   recuperable" y el saldo inicial calculado salía exactamente 40.000.000 €
   sin haber restado nada. Se detectó porque ese número era sospechosamente
   redondo.)
3. **El valor de cada jugador del reparto se calcula al precio EXACTO de
   aquel día**, no al de hoy — usando `GET /players/la-liga/{slug}?fields=prices`,
   que el usuario encontró en las DevTools y da el histórico diario de
   precio de un jugador (~366 días, formato `[fecha_AAMMDD, precio]`).

### Verificación (el único caso comprobable)

Solo se puede verificar contra la cuenta del propio usuario, porque es el
único saldo real conocido (`GET /market` → `status.balance`). El proceso de
validación, en orden:

| Método de valoración de la plantilla inicial | Saldo inicial predicho | Diferencia vs. saldo real (13.520.000 €) |
|---|---|---|
| Precio de HOY, solo plantilla actual (sin contar vendidos) | 27.750.000 € | 14.230.000 € |
| Precio de HOY, incluyendo vendidos sin comprar | 15.500.000 € | 1.980.000 € |
| Precio EXACTO del día del reparto, incluyendo vendidos | 14.120.000 € | **600.000 €** |

El error final (600.000 € sobre una plantilla de ~26M€, ≈1,5%) se explica
por un único jugador (`#37714`) que ya no aparece en el catálogo actual de
LaLiga (probablemente descendido o fuera de competición), no por un fallo
del método — confirma que la regla de los 40M es correcta.

### Limitación que persiste

Para el resto de usuarios **no hay forma de verificar el resultado final**
(su saldo real sigue oculto por `settings.balance = "hidden"`), así que ese
mismo margen de ~1-2% por jugadores sin precio histórico recuperable es la
mejor estimación de precisión disponible, no una certeza. Si un usuario ya
no está en la liga, su plantilla deja de ser consultable (`GET /user/{id}`
devuelve 404) y para él se muestra solo el flujo de caja neto acumulado
desde 0 (`analysis.economy.cumulative_flow()`), como en la v1 original.

## Si más adelante se automatiza con GitHub Actions

No está montado todavía. Para cuando se quiera:
- Las credenciales van como *Secrets* del repo (`BIWENGER_EMAIL`,
  `BIWENGER_PASSWORD`), añadidos por ti desde la web de GitHub o con
  `gh secret set` — nunca pegados en el chat ni en el código.
- El workflow ejecutaría `scripts/fetch_daily.py` con esos secrets como
  variables de entorno y haría commit del `.sqlite3` actualizado (o lo
  subiría a un artifact/storage externo si el histórico crece mucho).
- Cosas a probar primero: si Cloudflare bloquea también IPs de los runners
  de GitHub Actions (algunos rangos están en listas negras) — si pasa,
  habría que valorar un proxy o ejecutar el fetch en un runner
  self-hosted.
