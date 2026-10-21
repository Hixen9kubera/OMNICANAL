"""
fanout_recuperar.py — Vuelve a encolar los cambios de stock que nunca se repartieron.

POR QUÉ EXISTE
--------------
La cola del fan-out (`fanout_stock._pendientes`) vive en MEMORIA. Un reinicio del
backend a media cola —un despliegue, o cambiar una variable en Railway (regla 12)—
la tira, y como el fan-out solo actúa cuando Woo cambia, el SKU se queda con el
número viejo en los canales hasta que su stock se vuelva a mover.

Pasó el 29-sep-2026: la pasada de stock_watch de las 12:04 (CDMX) escribió 72
cambios en Woo, el fan-out alcanzó a repartir 3 y el despliegue de v0.596.0
reemplazó el contenedor. Los otros 69 no llegaron a ningún canal; dos días
después MUE-0293-AZL seguía a la venta en Temu con 37 piezas y Woo en 0.

CÓMO LO RESUELVE
----------------
Sin guardar la cola en otra parte: `ops.fanout_log` YA es el registro durable de
lo que stock_watch escribió (`odoo_delta` / `woo_cambio` / `vendible_cambio`) y de lo que el fan-out
repartió. Al arrancar y luego cada `fanout_recuperar_min`, se buscan los SKUs
cuyo ÚLTIMO cambio no tiene después ningún evento del fan-out, y se vuelven a
encolar. Es idempotente: el fan-out relee Woo en vivo y, si el canal ya coincide,
el plan sale `sin_cambio`.

LÍMITES (a propósito)
---------------------
- Solo cambios con más de `gracia_min` de antigüedad: lo reciente puede seguir en
  la cola. Al arrancar la cola está recién vacía, así que la gracia baja a 3 min.
- Solo cambios de las últimas `horas`: lo muy viejo pide una alineación
  consciente (`POST /api/fanout/alinear`), no un reenvío automático.
- Tope de SKUs por vuelta, y se salta lo que ya está en la cola. Con la cola rápida
  encendida (`fanout_stock.cola_rapida()`) mira la cola COMPLETA —lo pendiente, el lote
  y lo que está en proceso—; con las tres banderas apagadas sólo lo pendiente, como
  siempre (el 12-oct eso reencoló 5 SKUs que seguían en el lote).
- Al arrancar también vuelve a encolar los SKUs con una escritura diferida a Temu desde
  2 min antes del arranque (`reencolar_diferidas`): su verificación vivía en la memoria
  del proceso anterior. Va con la bandera de Temu encendida o apagada (apagarla reinicia
  el contenedor: es justo cuando quedan a medias) y, con el recuperador apagado, el
  scheduler la programa sola.
- Un mismo cambio (sku, ts) se reencola UNA vez por proceso: si su evento no llega
  a la bitácora (kubera caída), no entra en bucle.
- Lo que el fan-out VIO y no repartió no cuenta como repartido, pero tampoco se
  reencola en cada vuelta (`sin_repartir`): `sin_numero` (Woo no tiene `_stock` del
  SKU; reencolarlo no lo cambia) se cuenta y nada más; `lectura_fallida` (el libro no
  se pudo leer) ya tiene su reintento con espera en la cola (`fanout_stock._procesar`)
  y sólo vuelve, UNA vez, si no está en la cola (su reintento vivía en otro proceso).
  Con el libro (`FANOUT_FUENTE=kubera`) no hay `sin_numero` nuevos: un SKU que el
  libro no trae vale 0 (D4) y se reparte. Una marca `sin_numero` de la OTRA fuente
  (de Woo, puesta antes de pasar al libro; o al revés, al volver) no vale: con la fuente
  de hoy ese SKU sí tiene número, así que su cambio se reencola como cualquier otro.
  Los dos se cuentan aparte («sin repartir por falta de número: N», «lectura del
  libro fallida: N») y, si duran `AVISO_TRAS_VUELTAS` vueltas seguidas, se avisan por
  Slack (`alertas.avisar_estado`, tipos `fanout_recuperar_sin_numero` y
  `fanout_recuperar_lectura_fallida`); también se avisa cuando se vacían.
- Una lectura fallida que dura más que la ventana de `horas` no se pierde: además de
  `candidatos` (cuya ventana es de `horas`), cada vuelta busca las lecturas VARADAS
  (`lecturas_varadas`): SKUs cuya última fila del fan-out es `lectura_fallida`, de los
  últimos `DIAS_VARADAS` días, sin evento del fan-out después. Se cuentan aparte como
  cualquier lectura fallida y, si no están en la cola (el reintento vivía en el proceso
  anterior: un despliegue con el libro todavía caído), vuelven UNA vez. Y el «ya no
  quedan» de las lecturas fallidas no se anuncia mientras el fan-out tenga SKUs
  esperando reintento (`fanout_stock.lectura_estado`), ni en una vuelta que no pudo leer
  qué quedó sin repartir.
- Necesita que la bitácora se escriba en kubera (`supabase_write_fanout_log`):
  sin ella, todo cambio parecería sin repartir.
- Cubre lo que pasa por stock_watch. Una venta que se pierde en la cola la vuelve
  a ver stock_watch en su siguiente pasada (como `woo_cambio`), y si ESE cambio
  también se pierde, lo recupera esto.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fanout_recuperar")

# Lo que cuenta como «el fan-out ya lo procesó»: cualquier fila de un evento suyo.
# `sin_destinos` sí (no había a quién escribirle); `sin_numero` y `lectura_fallida` NO
# (`_ACC_SIN_REPARTIR`): el cambio sigue sin llegar a los canales.
_ACC_FANOUT = ["escribir", "omitir", "sin_cambio", "sin_destinos"]
# El fan-out vio el cambio y NO lo repartió: el SKU no tenía número (`sin_numero`) o el
# número no se pudo leer (`lectura_fallida`). Ni se dan por repartidos ni se reencolan
# en cada vuelta: se cuentan aparte y, si duran, se avisan (ver `revisar`).
_ACC_SIN_REPARTIR = ["sin_numero", "lectura_fallida"]
MOTIVO = "recuperado: cambio sin repartir"
MOTIVO_LECTURA = "recuperado: lectura del libro fallida"
# Hasta cuántos días atrás se buscan las lecturas fallidas varadas (`lecturas_varadas`).
DIAS_VARADAS = 7
MOTIVO_DIFERIDA = "recuperado: verificación de Temu pendiente"
VENTANA_DIFERIDA_S = 120
GRACIA_ARRANQUE_MIN = 3
# Vueltas SEGUIDAS con algo sin repartir antes de avisar por Slack (a 10 min por vuelta,
# media hora): una sola puede ser un parpadeo del libro.
AVISO_TRAS_VUELTAS = 3
_TEXTOS = {"sin_numero": "sin repartir por falta de número",
           "lectura_fallida": "lectura del libro fallida"}


def _texto(tipo: str) -> str:
    """`_TEXTOS`, con la lectura fallida según lo que lee el fan-out: con Woo también
    hay lecturas fallidas (`fanout_stock.LecturaFallida`) y no son «del libro»."""
    if tipo == "lectura_fallida":
        from services import stock_libro
        if not stock_libro.lee_libro():
            return "lectura del número fallida"
    return _TEXTOS[tipo]

_lock = threading.Lock()
_ya: dict[tuple[str, str], float] = {}   # (sku, ts del cambio) → cuándo se reencoló
_ultimo: dict[str, Any] = {}
_rachas: dict[str, int] = {k: 0 for k in _ACC_SIN_REPARTIR}           # vueltas seguidas con N>0
_avisado: dict[str, bool | None] = {k: None for k in _ACC_SIN_REPARTIR}  # None = no se sabe aún


def habilitado() -> bool:
    return bool(getattr(settings, "fanout_recuperar_enabled", False))


def candidatos(horas: int, gracia_min: int, limite: int) -> list[dict[str, Any]]:
    """SKUs cuyo último cambio de stock_watch no tiene evento del fan-out después.

    El cambio que falló al escribirse en Woo («ESCRITURA FALLÓ») no cuenta:
    stock_watch no lo encola y lo reintenta en su siguiente pasada.
    """
    from services import stock_watch
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """with c as (
             select distinct on (sku) sku::text as sku, ts from ops.fanout_log
              where canal = 'woocommerce' and accion = any(%(c)s)
                and resultado not like '%%FALLÓ%%'
                and ts > now() - make_interval(hours => %(h)s)
              order by sku, ts desc, id desc),
           e as (
             select sku::text as sku, max(ts) as t from ops.fanout_log
              where accion = any(%(a)s) and coalesce(canal, '') <> 'woocommerce'
                and ts > now() - make_interval(hours => %(h)s + 1)
              group by sku)
           select c.sku, c.ts from c left join e using (sku)
            where c.ts < now() - make_interval(mins => %(g)s)
              and (e.t is null or e.t < c.ts)
            order by c.ts
            limit %(n)s""",
        {"h": int(horas), "g": int(gracia_min), "a": _ACC_FANOUT, "n": int(limite),
         "c": list(stock_watch.ACCIONES_CAMBIO)})


def lecturas_varadas(dias: int, limite: int) -> list[dict[str, Any]]:
    """SKUs cuya ÚLTIMA fila del fan-out es una `lectura_fallida` de los últimos `dias`,
    sin ningún evento del fan-out después (`_ACC_FANOUT` o `sin_numero`): su cambio sigue
    sin llegar a los canales aunque ya haya salido de la ventana de `candidatos`. Sólo
    lee; una consulta por vuelta (las lecturas fallidas son pocas: se buscan primero)."""
    from services import fanout_stock
    from services import supabase_db as sdb
    return sdb.fetch_all(
        """with lf as (
             select sku, max(ts) as ts from ops.fanout_log
              where accion = %(lf)s and ts > now() - make_interval(days => %(d)s)
              group by sku)
           select lf.sku::text as sku, lf.ts from lf
            where not exists (
              select 1 from ops.fanout_log e
               where e.sku = lf.sku and e.ts > lf.ts and e.accion = any(%(a)s)
                 and coalesce(e.canal, '') <> 'woocommerce')
            order by lf.ts
            limit %(n)s""",
        {"lf": fanout_stock.ACC_LECTURA_FALLIDA, "d": int(dias), "n": int(limite),
         "a": _ACC_FANOUT + ["sin_numero"]})


def _marca_de_otra_fuente(fila: dict[str, Any], libro: bool) -> bool:
    """¿Esta fila `sin_numero` la puso el fan-out leyendo OTRA fuente que la de hoy?
    Se sabe por su texto (`fanout_stock.SIN_NUMERO_WOO` / `SIN_NUMERO_LIBRO`, en
    `resultado`). Un texto que no se reconoce cuenta como de la fuente de hoy (se queda
    aparte, como antes)."""
    from services import fanout_stock
    texto = str(fila.get("resultado") or "").strip()
    if texto.startswith(fanout_stock.SIN_NUMERO_WOO):
        return libro
    if texto.startswith(fanout_stock.SIN_NUMERO_LIBRO):
        return not libro
    return False


def sin_repartir(filas: list[dict[str, Any]], horas: int) -> dict[str, str]:
    """De los candidatos, los que el fan-out SÍ vio y no repartió: {SKU tal cual viene
    en `filas`: 'sin_numero' | 'lectura_fallida'} — la última fila de esas dos acciones
    del SKU, si es POSTERIOR a su cambio. Lo que no aparece es un cambio que nunca llegó
    al fan-out (lo de siempre: se reencola). Una consulta por vuelta.

    Una marca `sin_numero` de la otra fuente (`_marca_de_otra_fuente`) tampoco aparece:
    con la fuente de hoy ese SKU sí tiene número (con el libro, D4: 0 si no lo trae),
    así que reencolarlo SÍ lo cambia."""
    if not filas:
        return {}
    from services import stock_libro
    from services import supabase_db as sdb
    ultimas = {f["sku"]: f for f in sdb.fetch_all(
        """select distinct on (upper(sku::text)) upper(sku::text) as sku, accion, ts, resultado
             from ops.fanout_log
            where accion = any(%(a)s) and upper(sku::text) = any(%(s)s)
              and ts > now() - make_interval(hours => %(h)s + 1)
            order by upper(sku::text), ts desc, id desc""",
        {"a": list(_ACC_SIN_REPARTIR), "s": sorted({str(f["sku"]).upper() for f in filas}),
         "h": int(horas)})}
    libro = stock_libro.lee_libro()
    salida: dict[str, str] = {}
    for f in filas:
        u = ultimas.get(str(f["sku"]).upper())
        if not u or u["ts"] <= f["ts"]:
            continue
        if u["accion"] == "sin_numero" and _marca_de_otra_fuente(u, libro):
            continue
        salida[str(f["sku"])] = u["accion"]
    return salida


def _en_reintento() -> int:
    """Cuántos SKUs esperan reintento de lectura en la cola del fan-out (memoria)."""
    from services import fanout_stock
    try:
        return int(fanout_stock.lectura_estado().get("en_reintento") or 0)
    except Exception:  # noqa: BLE001 — sin saberlo, como si no hubiera
        return 0


def _vigilar(tipo: str, skus: list[str]) -> None:
    """Avisa por Slack cuando `tipo` lleva `AVISO_TRAS_VUELTAS` vueltas seguidas con
    algo sin repartir, y avisa cuando se vacía. Por cambio de estado
    (`alertas.avisar_estado`): mientras siga igual se calla (recordatorio cada 24 h).
    Corre dentro de `revisar`, que ya va en un hilo (regla 11)."""
    from services import alertas
    if not skus and tipo == "lectura_fallida" and _en_reintento() > 0:
        # La bitácora ya no las ve, pero el fan-out sigue reintentándolas en la cola: ni
        # racha nueva ni «ya no quedan».
        return
    _rachas[tipo] = _rachas[tipo] + 1 if skus else 0
    clave = f"fanout_recuperar_{tipo}"
    if _rachas[tipo] >= AVISO_TRAS_VUELTAS:
        from services import stock_libro
        if tipo == "sin_numero":
            # Con el libro no debería pasar (D4: lo que no trae vale 0); si pasa,
            # `stock_libro` volvió a devolver «sin número» y hay que mirarlo.
            por = ("el libro de kubera no dio número para esos SKUs" if stock_libro.lee_libro()
                   else "Woo no tiene `_stock` en esos SKUs")
        else:
            por = (f"el fan-out no pudo leer {'el libro' if stock_libro.lee_libro() else 'el número'}; "
                   f"los reintenta con espera creciente")
        alertas.avisar_estado(
            clave, "sin_repartir",
            f"*Fan-out · recuperador*: {len(skus)} cambio(s) {_texto(tipo)} desde hace "
            f"{_rachas[tipo]} vueltas ({por}): {', '.join(skus[:10])}{' …' if len(skus) > 10 else ''}",
            texto_ok=f"*Fan-out · recuperador*: ya no quedan cambios {_texto(tipo)}.")
        _avisado[tipo] = True
    elif not skus and _avisado[tipo] is not False:
        # Una vez por proceso con todo en orden (por si el aviso viene de antes del
        # despliegue) y después de cada aviso: anuncia la recuperación si la hubo.
        alertas.avisar_estado(clave, "ok", "",
                              texto_ok=f"*Fan-out · recuperador*: ya no quedan cambios {_texto(tipo)}.")
        _avisado[tipo] = False


def diferidas_recientes() -> list[str]:
    """SKUs con una escritura a Temu «ok (verificación diferida…)» desde
    `VENTANA_DIFERIDA_S` antes del arranque de este proceso: la convergencia vivía en la
    RAM del anterior (que en un despliegue sigue escribiendo un rato junto al nuevo, por
    eso la ventana llega hasta ahora). Volver a repartirlos es idempotente: el plan sale
    otra vez de Woo y del censo, y Temu se relee en vivo si hay que escribirle."""
    from services import fanout_stock
    from services import supabase_db as sdb
    desde = fanout_stock._ARRANQUE - VENTANA_DIFERIDA_S
    return [f["sku"] for f in sdb.fetch_all(
        """select distinct sku::text as sku from ops.fanout_log
            where canal = 'temu' and accion = 'escribir' and not dry_run
              and resultado like %(r)s
              and ts between to_timestamp(%(a)s) and to_timestamp(%(b)s)""",
        {"r": f"ok ({fanout_stock._TEMU_DIFERIDA}%", "a": desde, "b": time.time()})]


def reencolar_diferidas(excluir: set[str] | None = None) -> list[str]:
    """Al ARRANCAR: vuelve a encolar las verificaciones de Temu que el proceso anterior
    dejó a medias. Corre con FANOUT_TEMU_VERIFICAR_DIFERIDO encendida O APAGADA —la
    reversa de esa bandera es justo un reinicio, y apagada no quedaba nadie que las
    recuperara— y aunque el recuperador esté apagado (el scheduler la programa sola). Con
    la bandera apagada desde siempre no hay filas «ok (verificación diferida…)» en la
    ventana: una consulta de 0.04 ms (medido en producción) y nada más."""
    from services import fanout_stock
    if not fanout_stock.habilitado() or not getattr(settings, "supabase_write_fanout_log", False):
        return []
    try:
        filas = diferidas_recientes()
    except Exception as exc:  # noqa: BLE001 — sin bitácora, el censo y los excedentes lo cubren
        log.warning("Fan-out: no pude leer las verificaciones de Temu pendientes: %s", exc)
        return []
    ya = fanout_stock.en_cola() | {s.upper() for s in (excluir or set())}
    diferidas = [s for s in filas if s.upper() not in ya]
    if diferidas:
        fanout_stock.encolar_varios(diferidas, motivo=MOTIVO_DIFERIDA)
        log.warning("Fan-out: %d SKU(s) con la verificación de Temu a medias al reiniciar, "
                    "de vuelta a la cola: %s", len(diferidas), ", ".join(diferidas[:10]))
    return diferidas


def revisar(arranque: bool = False) -> dict[str, Any]:
    """Una vuelta. SÍNCRONA a propósito: el scheduler la corre con
    `asyncio.to_thread` (regla 11: lee la base)."""
    from services import fanout_stock
    if not habilitado():
        return {"ok": False, "motivo": "FANOUT_RECUPERAR_ENABLED apagado"}
    if not fanout_stock.habilitado():
        return {"ok": False, "motivo": "el fan-out está apagado"}
    if not getattr(settings, "supabase_write_fanout_log", False):
        return {"ok": False, "motivo": "la bitácora del fan-out no se escribe en kubera: no hay con qué comparar"}
    horas = max(1, int(settings.fanout_recuperar_horas))
    gracia = max(1, int(settings.fanout_recuperar_gracia_min))
    if arranque:
        gracia = min(gracia, GRACIA_ARRANQUE_MIN)
    tope = max(1, int(settings.fanout_recuperar_tope))

    t0 = time.time()
    filas = candidatos(horas, gracia, tope)
    # `ciego`: no se pudo saber qué quedó sin repartir. Se reencola como siempre, pero ni
    # se cuentan rachas ni se anuncia que «ya no quedan» (`_vigilar`).
    ciego = False
    try:
        vistos = sin_repartir(filas, horas)
    except Exception as exc:  # noqa: BLE001 — sin la marca, se reencola como siempre (idempotente)
        log.warning("Fan-out: no pude leer qué cambios quedaron sin número o sin lectura: %s", exc)
        vistos, ciego = {}, True
    try:
        varadas = lecturas_varadas(DIAS_VARADAS, tope)
    except Exception as exc:  # noqa: BLE001 — la vuelta sigue con lo de `candidatos`
        log.warning("Fan-out: no pude leer las lecturas fallidas varadas: %s", exc)
        varadas, ciego = [], True
    if fanout_stock.cola_rapida():
        en_cola = fanout_stock.en_cola()          # pendientes + lote + en proceso, en mayúsculas

        def esta(s: str) -> bool:
            return s.upper() in en_cola
    else:
        with fanout_stock._lock:
            en_cola = set(fanout_stock._pendientes)

        def esta(s: str) -> bool:
            return s in en_cola
    nuevos: list[str] = []
    lectura: list[str] = []                       # lecturas fallidas que vuelven a la cola
    aparte: dict[str, list[str]] = {k: [] for k in _ACC_SIN_REPARTIR}
    with _lock:
        vencido = time.time() - 2 * horas * 3600
        for clave in [k for k, cuando in _ya.items() if cuando < vencido]:
            _ya.pop(clave, None)
        for f in filas:
            clave = (str(f["sku"]), f["ts"].isoformat())
            visto = vistos.get(clave[0])
            if visto:
                aparte[visto].append(clave[0])
            if visto == "sin_numero":
                # El fan-out ya leyó la fuente de hoy y no había número (hoy, sólo Woo):
                # reencolarlo no lo cambia. Cuando lo traiga, ese cambio entra solo por
                # stock_watch. (Una marca de la otra fuente no llega aquí: `sin_repartir`.)
                continue
            if clave in _ya or esta(clave[0]):
                continue
            # Lectura fallida fuera de la cola = su reintento vivía en la memoria de otro
            # proceso (un despliegue): vuelve UNA vez y el reintento con espera sigue aquí.
            _ya[clave] = time.time()
            (lectura if visto == "lectura_fallida" else nuevos).append(clave[0])
        # Las varadas que `candidatos` ya no ve (su cambio salió de la ventana de `horas`).
        ya_vistos = {str(f["sku"]).upper() for f in filas}
        for f in varadas:
            sku = str(f["sku"])
            if sku.upper() in ya_vistos:
                continue
            ya_vistos.add(sku.upper())
            aparte["lectura_fallida"].append(sku)
            clave = (sku, f["ts"].isoformat())
            if clave in _ya or esta(sku):
                continue
            _ya[clave] = time.time()
            lectura.append(sku)
    if nuevos:
        fanout_stock.encolar_varios(nuevos, motivo=MOTIVO)
        log.warning("Fan-out: %d cambio(s) que nunca se repartieron, de vuelta a la cola: %s%s",
                    len(nuevos), ", ".join(nuevos[:10]), " …" if len(nuevos) > 10 else "")
    if lectura:
        fanout_stock.encolar_varios(lectura, motivo=MOTIVO_LECTURA)
        log.warning("Fan-out: %d cambio(s) con la lectura fallida y sin reintento en "
                    "la cola, de vuelta: %s", len(lectura), ", ".join(lectura[:10]))
    for tipo, skus in aparte.items():
        if skus:
            log.warning("Fan-out: %s: %d (%s%s)", _texto(tipo), len(skus), ", ".join(skus[:10]),
                        " …" if len(skus) > 10 else "")
        if ciego:
            continue
        try:
            _vigilar(tipo, skus)
        except Exception as exc:  # noqa: BLE001 — un aviso roto no tumba la vuelta
            log.warning("Fan-out: aviso de %s: %s", tipo, exc)
    diferidas = reencolar_diferidas(set(nuevos) | set(lectura)) if arranque else []
    _ultimo.clear()
    _ultimo.update(ts=time.time(), arranque=arranque, candidatos=len(filas),
                   encolados=len(nuevos) + len(lectura), reencolados_lectura=len(lectura),
                   varadas=len(varadas), ciego=ciego,
                   sin_numero=len(aparte["sin_numero"]),
                   lectura_fallida=len(aparte["lectura_fallida"]),
                   sin_repartir={tipo: {"n": len(skus), "texto": f"{_texto(tipo)}: {len(skus)}",
                                        "vueltas_seguidas": _rachas[tipo], "muestra": skus[:15]}
                                 for tipo, skus in aparte.items()},
                   diferidas_temu=len(diferidas),
                   muestra=(nuevos + lectura)[:15], segundos=round(time.time() - t0, 2), horas=horas,
                   gracia_min=gracia)
    return {"ok": True, **_ultimo}


def estado() -> dict[str, Any]:
    return {
        "habilitado": habilitado(),
        "cada_min": max(5, int(getattr(settings, "fanout_recuperar_min", 10))),
        "horas": int(getattr(settings, "fanout_recuperar_horas", 6)),
        "gracia_min": int(getattr(settings, "fanout_recuperar_gracia_min", 15)),
        "tope": int(getattr(settings, "fanout_recuperar_tope", 300)),
        "ultima_vuelta": dict(_ultimo) or None,
        # «sin repartir por falta de número: N» y «lectura del libro fallida: N» de la
        # última vuelta, con cuántas vueltas seguidas llevan (a las 3 se avisa).
        "sin_repartir": _ultimo.get("sin_repartir"),
        "aviso_tras_vueltas": AVISO_TRAS_VUELTAS,
    }
