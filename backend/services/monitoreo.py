"""
monitoreo.py — Cuánto lleva hecho cada persona, y en qué canal.

Lee `ops.process_log` filtrando por los procesos de PERSONA. Deja fuera lo
automático: el sondeo, el fan-out y los ETL también escriben ahí, y contarlos
inflaría los números con trabajo que nadie hizo.

LO QUE ESTA PANTALLA NO PUEDE HACER, Y POR QUÉ IMPORTA MÁS QUE LO QUE SÍ
────────────────────────────────────────────────────────────────────────
«No lo hizo» y «no lo sabemos» son cosas distintas y **no pueden verse iguales**.
Un 0 en TikTok sería una mentira: esa persona quizá publicó 200 productos con un
script de escritorio, que no deja firma. Por eso esta capa devuelve `None` —que
la pantalla pinta RAYADO— donde no se puede atribuir, y `{0, 0}` donde sí se
puede y la respuesta es cero.

Ese reparto se MIDE en cada consulta (`_canales_sin_registro`,
`_procesos_sin_registro`), no se cablea: la instrumentación de v0.398–v0.402 ya
hace que TikTok, Temu y Walmart firmen cuando se publican desde el panel, así
que la pantalla tiene que corregirse sola en cuanto llegue la primera firma —
sin que nadie edite una lista.

⚠️ EL MISMO HUMANO PUEDE TENER DOS CORREOS. Thalía entra con `thalias@` o con
`sancorpethalia@` según la cuenta, así que sus movimientos aparecen partidos.
Se unifican aquí con `_MISMA_PERSONA` en vez de en la consulta: la bitácora debe
conservar el correo REAL con el que se hizo cada cosa —eso es lo que la vuelve
auditable— y la fusión es una decisión de presentación.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db

log = logging.getLogger("omnicanal.monitoreo")

# Procesos que nacen de un botón. Ver services/bitacora.py.
_DE_PERSONA = ("publicar", "costos", "crear", "precio", "stock")

# ⚠️ `crear` escribe un renglon POR PASO, no por producto: "En cola…",
# "1/5 Scrapeando Alibaba…", "2/5 Mejorando titulo…". Medido el 1-sep: 147 filas
# de cola y 129 de scrapeo para un pu&#241;ado de productos. Contarlas todas infla
# el trabajo de cada persona ~10x y vuelve el tablero inservible.
#
# Solo cuentan los estados TERMINALES: el producto quedo creado, o no.
_INTERMEDIOS = ("en_cola", "procesando")

# Cada proceso llama distinto al exito. `crear` dice 'completado', los demas
# 'ok'. Sin esto, las creaciones saldrian todas como fallidas.
_EXITO = ("ok", "completado", "succeeded")

# Dos correos, una persona (Brandon, 5-ago). Se fusionan al MOSTRAR.
_MISMA_PERSONA = {"sancorpethalia@kubera.mx": "thalias@kubera.mx"}


def _persona(correo: str | None) -> str:
    c = (correo or "").strip().lower()
    return _MISMA_PERSONA.get(c, c)


# ═══════════════════════════════════════════════════════════════════════════
# «NO LO HIZO» vs «NO LO SABEMOS» — la distinción que sostiene la pantalla
# ═══════════════════════════════════════════════════════════════════════════
#
# Si el tablero le pinta un 0 a un KAM en TikTok, MIENTE: esa persona quizá
# publicó 200 productos con un script de escritorio, que no deja firma. El primer
# KAM que lo note deja de creerle al tablero, y con razón.
#
# Por eso la API devuelve `None` —que la pantalla pinta RAYADO— y no un cero.
#
# ⚠️ Y SE MIDE, NO SE CABLEA. Sería más fácil escribir aquí
# `_SIN_REGISTRO = ("tiktok", "temu", "walmart")`, y sería un error: el día que
# esos canales empiecen a firmar —la instrumentación de v0.398-v0.402 ya está
# puesta— la lista mentiría al revés, ocultando trabajo que sí quedó registrado.
# Un canal está "sin registro" si TUVO envíos en la ventana y NINGUNO trae actor.
# Así la pantalla se corrige sola en cuanto llegue la primera publicación firmada.


def _canales_sin_registro(dias: int) -> list[str]:
    """Canales que PUBLICARON en la ventana y de los que no se puede saber quién.

    Se miran las DOS tablas, y hace falta que las dos callen:

      · `ops.channel_submissions.actor` — la firma del envío. Es columna nueva
        (migración 0046, 4-sep), así que los envíos anteriores están todos en
        NULL y por sí sola acusaría a canales que sí sabemos atribuir.
      · `ops.process_log` con `proceso='publicar'` — la firma de la PERSONA, que
        existe desde el 1-sep y cubre los cinco canales, porque
        `POST /api/publicar/confirmar` los despacha a todos.

    Un canal sólo se declara mudo cuando tuvo envíos y NINGUNA de las dos supo
    decir quién. Medido el 4-sep: ML sale atribuible por la segunda (60 de 60)
    aunque la primera esté vacía; TikTok, Temu y Walmart no salen por ninguna,
    porque se publican con scripts de escritorio.
    """
    try:
        envios = supabase_db.fetch_all(
            """select canal, count(*) envios,
                      count(*) filter (where actor is not null) firmados
                 from ops.channel_submissions
                where created_at >= now() - make_interval(days => %s)
                group by canal""", (dias,))
        personas = supabase_db.fetch_all(
            """select detalle->>'canal' canal,
                      count(*) filter (where actor is not null) firmadas
                 from ops.process_log
                where proceso = 'publicar'
                  and created_at >= now() - make_interval(days => %s)
                group by 1""", (dias,))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo medir qué canales firman: %s", exc)
        return []
    por_persona = {p["canal"]: p["firmadas"] for p in personas if p["canal"]}
    return sorted(f["canal"] for f in envios
                  if f["envios"] > 0 and f["firmados"] == 0
                  and por_persona.get(f["canal"], 0) == 0)


def _procesos_sin_registro(dias: int) -> list[str]:
    """Procesos que en la ventana no atribuyeron NI UNA fila a una persona.

    Mismo criterio que los canales: la celda va rayada sólo cuando de verdad no
    se puede saber, no cuando alguien no hizo nada. Un proceso con actividad
    firmada por otros y cero por mí es un `0 / 0` legítimo — «no lo hizo».
    """
    try:
        filas = supabase_db.fetch_all(
            """select proceso,
                      count(*) filas,
                      count(*) filter (where actor is not null) firmadas
                 from ops.process_log
                where proceso = any(%s)
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by proceso""",
            (list(_DE_PERSONA), list(_INTERMEDIOS), dias))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo medir qué procesos firman: %s", exc)
        return []
    return sorted(f["proceso"] for f in filas
                  if f["filas"] > 0 and f["firmadas"] == 0)


def _canales_por_persona(dias: int) -> dict[str, dict[str, dict[str, int]]]:
    """Por persona → por canal·cuenta: éxitos e intentos.

    ⚠️ POR QUÉ ESTO NO ES UN SIMPLE `group by detalle->>'cuenta'`. Medido el
    4-sep: de las 60 publicaciones de Mercado Libre con actor, **53 no traen
    cuenta** — sólo 4 de SANCORFASHION y 3 de BEKURA. No es un fallo del
    registro: es que publicar en ML manda a las DOS cuentas de una vez, así que
    la petición no tiene *una* cuenta que anotar y el campo sale vacío.

    Pero el dato SÍ está, en otro lado: desde v0.395.0 el detalle guarda
    `resultados: [{cuenta, ok, error}]`, una entrada por cuenta, porque es lo que
    contesta *"falló en BEKURA pero entró en SANCOR"*. Aquí se expande esa lista
    para que la pantalla pueda separar Kubera de San Corpe, que es justo lo que
    no se podía ver: tres chips seguidos que decían los tres «MELI».

    Cuando no hay ni `cuenta` ni `resultados` la fila cae en `''`, y la pantalla
    la muestra sin cuenta en vez de inventarle una.
    """
    try:
        filas = supabase_db.fetch_all(
            """select actor,
                      coalesce(detalle->>'canal', '(sin canal)') canal,
                      coalesce(r.value->>'cuenta', detalle->>'cuenta', '') cuenta,
                      count(*) total,
                      count(*) filter (
                        where case when r.value is not null
                                   then (r.value->>'ok')::boolean
                                   else estado = any(%s) end) exitos
                 from ops.process_log
                 left join lateral jsonb_array_elements(
                        case when jsonb_typeof(detalle->'resultados') = 'array'
                             then detalle->'resultados' else '[]'::jsonb end) r
                        on true
                where proceso = any(%s)
                  and actor is not null
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by actor, canal, cuenta""",
            (list(_EXITO), list(_DE_PERSONA), list(_INTERMEDIOS), dias))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron leer los canales por persona: %s", exc)
        return {}
    out: dict[str, dict[str, dict[str, int]]] = {}
    for f in filas:
        if f["canal"] == "(sin canal)":
            continue
        u = out.setdefault(_persona(f["actor"]), {})
        clave = f"{f['canal']}·{f['cuenta']}" if f["cuenta"] else f["canal"]
        c = u.setdefault(clave, {"total": 0, "exitos": 0})
        c["total"] += f["total"]; c["exitos"] += f["exitos"]
    return out


def cobertura(dias: int = 30) -> list[dict[str, Any]]:
    """Qué parte de cada proceso sabemos atribuir. El insumo de la tarjeta 1a.6.

    La parte que falta NO es inactividad: son movimientos cuyo actor no se
    guardó. Decirlo importa porque el hueco se ve idéntico a "nadie trabajó".
    """
    try:
        return supabase_db.fetch_all(
            """select proceso,
                      count(*) filas,
                      count(*) filter (where actor is not null) con_actor,
                      count(distinct actor) filter (where actor is not null) personas
                 from ops.process_log
                where proceso = any(%s)
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by proceso
                order by filas desc""",
            (list(_DE_PERSONA), list(_INTERMEDIOS), dias))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer la cobertura: %s", exc)
        return []


def publicaciones_semana() -> list[dict[str, Any]]:
    """Altas NUEVAS por canal en la semana en curso, y si se pueden atribuir.

    «Nueva» = primera submission exitosa de ese (canal, cuenta, SKU). No sirve
    contar `operacion='alta'`: Amazon la escribe también cuando REEMPLAZA, con
    un PUT create-or-replace, y el 48.5% de sus altas históricas son repeticiones
    del mismo SKU. Contarlas infla ese canal casi al doble.
    """
    try:
        return supabase_db.fetch_all(
            """with primeras as (
                 select canal, cuenta, sku, min(created_at) primera,
                        (array_agg(actor order by created_at))[1] actor
                   from ops.channel_submissions
                  where success is true
                  group by canal, cuenta, sku)
               select canal,
                      count(*) nuevas,
                      count(*) filter (where actor is not null) con_actor
                 from primeras
                where primera >= date_trunc('week', now() at time zone 'America/Mexico_City')
                group by canal order by nuevas desc""")
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron contar las altas de la semana: %s", exc)
        return []


def _series(dias: int) -> dict[str, list[int]]:
    """Movimientos por día y por persona — la chispa del renglón (sparkline)."""
    try:
        filas = supabase_db.fetch_all(
            """select actor,
                      (created_at at time zone 'America/Mexico_City')::date dia,
                      count(*) n
                 from ops.process_log
                where proceso = any(%s) and actor is not null
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by actor, dia""",
            (list(_DE_PERSONA), list(_INTERMEDIOS), dias))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron leer las series: %s", exc)
        return {}
    from datetime import date, timedelta
    hoy = date.today()
    eje = [hoy - timedelta(days=i) for i in range(dias - 1, -1, -1)]
    idx = {d: i for i, d in enumerate(eje)}
    out: dict[str, list[int]] = {}
    for f in filas:
        u = _persona(f["actor"])
        serie = out.setdefault(u, [0] * dias)
        i = idx.get(f["dia"])
        if i is not None:
            serie[i] += f["n"]
    return out


def _sin_movimientos(activos: set[str]) -> list[dict[str, str]]:
    """Los operadores que NO aparecen en la ventana.

    Se muestran, en gris y con su advertencia: **no existe ninguna tabla que
    asigne un KAM a un canal o a una categoría**, así que un cero aquí puede ser
    inactividad real o trabajo por un camino sin registro. Sin ese mapa el
    tablero no puede afirmar que esa persona debía haber hecho algo — se enseña
    el hueco, no una acusación.
    """
    try:
        filas = supabase_db.fetch_all(
            """select email, nombre from core.usuarios
                where rol = 'operador' and coalesce(activo, true)""")
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer core.usuarios: %s", exc)
        return []
    return [{"usuario": _persona(f["email"]), "correo": f["email"],
             "nombre": f.get("nombre")}
            for f in filas if _persona(f["email"]) not in activos]


def resumen(dias: int = 30) -> dict[str, Any]:
    """
    Por usuario y por canal: cuántas acciones, cuántas salieron bien, y cuándo
    fue la última.

    `exitos` y `total` van por separado a propósito. Un usuario con 40 intentos
    y 12 éxitos no es lo mismo que uno con 12 de 12, y esa diferencia es
    justamente lo que hay que ver — mide productividad Y señala dónde algo
    está rebotando.
    """
    try:
        filas = supabase_db.fetch_all(
            """select actor, proceso,
                      coalesce(detalle->>'canal', '(sin canal)') canal,
                      coalesce(detalle->>'cuenta', '') cuenta,
                      count(*) total,
                      count(*) filter (where estado = any(%s)) exitos,
                      max(created_at) ultima
                 from ops.process_log
                where proceso = any(%s)
                  and actor is not null
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by actor, proceso, canal, cuenta
                order by total desc""",
            (list(_EXITO), list(_DE_PERSONA), list(_INTERMEDIOS), dias),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer el monitoreo: %s", exc)
        return {"ok": False, "motivo": str(exc)[:200], "usuarios": []}

    # Se agrupa en Python y no en SQL porque la fusión de los dos correos de
    # Thalía tiene que ocurrir DESPUÉS de leer, para no perder cuál se usó.
    por_usuario: dict[str, dict[str, Any]] = {}
    for f in filas:
        u = por_usuario.setdefault(_persona(f["actor"]), {
            "usuario": _persona(f["actor"]), "total": 0, "exitos": 0,
            "ultima": None, "correos": set(), "canales": {}, "procesos": {}})
        u["total"] += f["total"]
        u["exitos"] += f["exitos"]
        u["correos"].add(f["actor"])
        if u["ultima"] is None or f["ultima"] > u["ultima"]:
            u["ultima"] = f["ultima"]
        etiqueta = f"{f['canal']}·{f['cuenta']}" if f["cuenta"] else f["canal"]
        c = u["canales"].setdefault(etiqueta, {"total": 0, "exitos": 0})
        c["total"] += f["total"]; c["exitos"] += f["exitos"]
        p = u["procesos"].setdefault(f["proceso"], {"total": 0, "exitos": 0})
        p["total"] += f["total"]; p["exitos"] += f["exitos"]

    usuarios = sorted(por_usuario.values(), key=lambda x: -x["total"])

    # Lo que convierte esto en un tablero creíble: qué NO se puede atribuir.
    proc_mudos = _procesos_sin_registro(dias)
    # Los procesos que de verdad ocurrieron. `precio` y `stock` estan declarados
    # en `bitacora.py` y NO tienen un solo call site en el backend: dibujarles
    # una columna de ceros seria inventar una medicion que nadie hizo.
    cob = cobertura(dias)
    con_filas = [c["proceso"] for c in cob]
    can_mudos = _canales_sin_registro(dias)
    series = _series(dias)
    # Los canales salen de su propia consulta: la de arriba no puede separar
    # BEKURA de SANCORFASHION porque `cuenta` viene vacía en el 88% de las filas.
    por_canal = _canales_por_persona(dias)
    errores = _errores_por_persona(dias)

    for u in usuarios:
        u["correos"] = sorted(u["correos"])
        u["ultima"] = u["ultima"].isoformat() if u["ultima"] else None
        u["serie"] = series.get(u["usuario"], [0] * dias)
        u["errores"] = errores.get(u["usuario"], 0)
        # ⚠️ AQUÍ VIVE LA REGLA DE ORO. Un proceso que en toda la ventana no
        # atribuyó ni una fila sale como `None` —la pantalla lo pinta rayado—;
        # uno que sí atribuye, pero no a esta persona, sale como `0 / 0`, que
        # es «no lo hizo» y es verdad. **Si el backend colapsara `None` a `0`,
        # la pantalla perdería su razón de ser.**
        celdas: dict[str, Any] = {}
        for p in con_filas:
            if p in proc_mudos:
                celdas[p] = None
            else:
                d = u["procesos"].get(p)
                celdas[p] = {"exitos": d["exitos"], "intentos": d["total"]} if d \
                    else {"exitos": 0, "intentos": 0}
        u["celdas"] = celdas
        u["canales_sin_registro"] = can_mudos
        u["canales"] = por_canal.get(u["usuario"], u["canales"])

    activos = {u["usuario"] for u in usuarios}
    return {"ok": True, "dias": dias, "usuarios": usuarios,
            "total": sum(u["total"] for u in usuarios),
            "procesos": con_filas,
            "procesos_sin_registro": proc_mudos,
            "canales_sin_registro": can_mudos,
            "cobertura": cob,
            "publicaciones_semana": publicaciones_semana(),
            "sin_movimientos": _sin_movimientos(activos)}


def _errores_por_persona(dias: int) -> dict[str, int]:
    """Cuántos movimientos fallaron, por persona. La píldora del renglón."""
    try:
        filas = supabase_db.fetch_all(
            """select actor, count(*) n
                 from ops.process_log
                where proceso = any(%s) and actor is not null
                  and estado <> all(%s) and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by actor""",
            (list(_DE_PERSONA), list(_INTERMEDIOS), list(_EXITO), dias))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron contar los errores: %s", exc)
        return {}
    out: dict[str, int] = {}
    for f in filas:
        out[_persona(f["actor"])] = out.get(_persona(f["actor"]), 0) + f["n"]
    return out


def movimientos(limite: int = 100, usuario: str | None = None,
                canal: str | None = None, dias: int = 30) -> list[dict[str, Any]]:
    """El detalle, uno por uno: quién, qué, sobre qué SKU y cuándo."""
    where = ["proceso = any(%s)", "actor is not null",
             "estado <> all(%s)",
             "created_at >= now() - make_interval(days => %s)"]
    params: list[Any] = [list(_DE_PERSONA), list(_INTERMEDIOS), dias]
    if usuario:
        # Se busca por los DOS correos si es alguien con cuenta doble.
        correos = [usuario] + [c for c, u in _MISMA_PERSONA.items() if u == usuario]
        where.append("actor = any(%s)"); params.append(correos)
    if canal:
        where.append("detalle->>'canal' = %s"); params.append(canal)
    params.append(limite)
    try:
        return supabase_db.fetch_all(
            f"""select created_at, actor, proceso, accion, sku, estado,
                       detalle->>'canal' canal, detalle->>'cuenta' cuenta,
                       detalle, duracion_s
                  from ops.process_log
                 where {' and '.join(where)}
                 order by created_at desc limit %s""", tuple(params))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron leer los movimientos: %s", exc)
        return []
