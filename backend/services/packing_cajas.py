# -*- coding: utf-8 -*-
"""
packing_cajas.py — Las CAJAS del packing list, leídas del renglón de verdad.

QUÉ PROBLEMA RESUELVE
---------------------
La pestaña Inventario mostraba las cajas del packing list desde
`costing.costos_validados.cajas`, que es un CONGELADO de dos cargas masivas
(21-may y 3-jun-2026): todo lo dado de alta después nace en NULL. Por eso
`ACC-0907-MET` salía con «PL —» aunque su renglón del packing list está
perfectamente identificado — Brandon lo encontró a mano el 9-sep: fila 762 de
`SZLS50213900=CIPL(1) (1).xlsx`, una caja con 20 piezas.

Aquí se lee del ARCHIVO, no de la columna congelada.

DE DÓNDE SALE EL RENGLÓN, Y POR QUÉ NO HAY QUE VOLVER A BUSCARLO
-----------------------------------------------------------------
La escalera de detección de imagen (foto de Odoo → dHash → título → foto de la
publicación de ML + IA) ya corrió: vive en `packing_publicados.py` y es lo que
la pestaña Costos ejecuta al validar. **Su resultado se persiste** en
`costing.caja_compartida` (archivo + renglones + piezas_grupo + cbm_grupo), que
hasta hoy NO LA LEÍA NADIE — se escribía y se olvidaba.

Así que aquí no se vuelve a resolver nada: se toma el renglón ya resuelto y se
va al xlsx a leer la columna de cartones (箱数 / CTNS). Eso importa porque la
escalera cuesta descargas de Drive y llamadas de IA, y esto cuesta abrir un
archivo que además ya está cacheado en disco.

Cobertura medida el 9-sep-2026: `caja_compartida` tiene 60 filas para 15,849
SKUs de `costos_validados` (0.38%). Es poquísimo, pero cubre 9 de los 13 SKUs
del piloto — y es dato EXACTO donde la columna congelada estaba vacía o
equivocada.

LA TRAMPA DE LA CAJA COMPARTIDA (y es la razón de la mitad de este archivo)
---------------------------------------------------------------------------
Cuando varios renglones comparten un cartón, **cada renglón reporta el MISMO
número de cartones**, porque el valor se hereda del ancla del merge. Sumarlos
multiplica la caja por el número de renglones.

Medido en `ROP-0731-BLN`, que ocupa las filas 684-687 de su packing list y
comparte cartón con la 683 (son cinco colores del mismo vestido de novia):
cada una de las cuatro dice `cajas = 1`. Sumar da **4 cajas**. La verdad es
**UNA caja compartida entre cinco renglones**, con 20 piezas dentro de las que
19 son de este SKU.

Por eso el resultado distingue `compartida` y nunca suma dentro de un grupo.

QUÉ NO HACE
-----------
No escribe nada. No corre la escalera de detección. No inventa el renglón
cuando `caja_compartida` no lo tiene — en ese caso devuelve nada y quien llama
se queda con lo que ya tenía.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("omnicanal.services.packing_cajas")

# ESTO NO PUEDE CORRER DENTRO DE UNA PETICIÓN. Medido el 9-sep-2026 sobre los
# 13 SKUs del piloto: 52 s en frío y 45 s con los archivos YA en disco — o sea
# que el costo no es bajar de Drive, es PARSEAR: el packing list del contenedor
# SZLS50213900 pesa 11.6 MB porque lleva una foto incrustada por renglón.
#
# Por eso el contrato es: la petición LEE la caché y nunca espera. La primera
# vez devuelve vacío y deja un hilo llenándola; a partir de la segunda hay
# dato. Un hilo y no `asyncio.to_thread` porque debe sobrevivir a la petición
# que lo disparó — si el usuario recarga, el trabajo ya viene en camino.
#
# La caché es POR SKU (no por la lista pedida) para que dos pantallas con
# universos distintos compartan lo ya calculado. Una hora de TTL: los packing
# lists son documentos de embarque cerrados, no cambian.
_TTL = 3600.0
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_candado = threading.Lock()
_calentando: set[str] = set()


def por_sku(skus: list[str], *, esperar: bool = False) -> dict[str, dict[str, Any]]:
    """
    ``{ SKU: {cajas, compartida, renglones, archivo, piezas_fila, piezas_grupo} }``

    Solo trae los SKUs cuya procedencia está registrada en
    `costing.caja_compartida`. Los demás no aparecen en el diccionario — no se
    devuelve ``None`` por SKU para que quien llama distinga «no lo sé» de
    «cero», que son cosas distintas y confundirlas ya costó un incidente
    (ver el aviso de congelación en CLAUDE.md).

    `esperar=False` (lo que usa el panel) devuelve lo que haya en caché y
    calienta el resto en segundo plano. `esperar=True` bloquea hasta tener el
    dato — solo para scripts y diagnósticos, JAMÁS dentro de una corrutina.
    """
    skus = [s for s in (skus or []) if s]
    if not skus:
        return {}

    ahora = time.monotonic()
    listos = {s: v for s in skus
              if (g := _cache.get(s)) and (ahora - g[0]) < _TTL
              for v in [g[1]] if v is not None}
    faltan = [s for s in skus
              if not (g := _cache.get(s)) or (ahora - g[0]) >= _TTL]

    if not faltan:
        return listos
    if not esperar:
        _calentar_aparte(faltan)
        return listos

    listos.update(_resolver(faltan))
    return listos


def _calentar_aparte(skus: list[str]) -> None:
    """Un hilo llena la caché sin que la petición lo espere."""
    with _candado:
        pendientes = [s for s in skus if s not in _calentando]
        if not pendientes:
            return
        _calentando.update(pendientes)

    def _correr() -> None:
        try:
            _resolver(pendientes)
        except Exception as exc:  # noqa: BLE001
            log.warning("packing_cajas: calentado falló: %s", exc)
        finally:
            with _candado:
                _calentando.difference_update(pendientes)

    threading.Thread(target=_correr, name="packing-cajas", daemon=True).start()


def _resolver(skus: list[str]) -> dict[str, dict[str, Any]]:
    """El trabajo lento: BD → Drive → xlsx → renglón. Deja todo en la caché,
    incluidos los SKUs sin dato (como `None`), para no reintentarlos cada vez."""
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(
            "select sku::text as sku, archivo, renglones, contenedor_base "
            "from costing.caja_compartida where sku::text = any(%s)", (skus,))
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: caja_compartida no disponible: %s", exc)
        return {}
    if not filas:
        _marcar_sin_dato(skus, set())
        return {}

    from services import packing_drive_carpeta as drive, packing_indice as pidx
    try:
        inventario = drive.inventario()
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: inventario de Drive no disponible: %s", exc)
        return {}

    indices: dict[str, Any] = {}
    salida: dict[str, dict[str, Any]] = {}

    for f in filas:
        archivo = (f.get("archivo") or "").strip()
        renglones = sorted(int(r) for r in (f.get("renglones") or []))
        if not archivo or not renglones:
            continue

        if archivo not in indices:
            indices[archivo] = _indexar(archivo, f.get("contenedor_base") or "",
                                        inventario, drive, pidx)
        ix = indices[archivo]
        if ix is None:
            continue

        # El PRIMER renglón manda: cuando el cartón se comparte, todos los
        # renglones del grupo traen el mismo conteo de cartones, así que tomarlo
        # una vez es lo correcto y sumarlo es el error.
        try:
            d = ix.datos(renglones[0])
        except Exception as exc:  # noqa: BLE001
            log.warning("packing_cajas: fila %s de %s ilegible: %s",
                        renglones[0], archivo, exc)
            continue

        cajas = d.get("cajas")
        if cajas is None:
            # El archivo no trae columna de cartones. NO se asume 1: el propio
            # `Indice.cajas()` devuelve None y no 1 justamente por esto.
            continue

        grupo = list(d.get("grupo") or [])
        compartida = bool(grupo)

        # Piezas del SKU: cuando ocupa varios renglones se SUMAN (son piezas
        # distintas), al revés que las cajas. `ROP-0731-BLN` tiene 6+5+6+2 = 19
        # piezas repartidas en cuatro renglones de un mismo cartón.
        piezas = 0.0
        for r in renglones:
            try:
                piezas += float(ix.datos(r).get("piezas_fila") or 0)
            except Exception:  # noqa: BLE001
                pass

        salida[f["sku"]] = {
            "cajas": round(float(cajas), 2),
            "compartida": compartida,
            # Cuántos renglones comparten el cartón EN TOTAL (los del SKU más
            # los ajenos). Es lo que explica que una caja "valga" menos de una.
            "renglones_carton": len(set(grupo) | set(renglones)) if compartida else len(renglones),
            "renglones": renglones,
            "archivo": archivo,
            "piezas_fila": round(piezas, 2) or None,
            "piezas_grupo": _num(d.get("piezas_grupo")),
        }

    ahora = time.monotonic()
    for sku, v in salida.items():
        _cache[sku] = (ahora, v)
    _marcar_sin_dato(skus, set(salida))
    log.info("packing_cajas: resueltos %s de %s SKUs", len(salida), len(skus))
    return salida


def _marcar_sin_dato(skus: list[str], resueltos: set[str]) -> None:
    """Los que no tienen renglón registrado también se cachean, como `None`:
    si no, cada carga volvería a abrir los xlsx para no encontrar nada."""
    ahora = time.monotonic()
    for s in skus:
        if s not in resueltos:
            _cache[s] = (ahora, None)


def _indexar(archivo: str, contenedor: str, inventario: dict[str, str],
             drive: Any, pidx: Any) -> Any:
    """El xlsx del renglón, indexado. `None` si no se puede."""
    # OJO: `inventario()` va {file_id: nombre}, no al revés. Buscar por nombre
    # con `inventario.get(archivo)` devuelve None SIEMPRE y en silencio.
    candidatos = []
    try:
        candidatos = drive.archivos_de(contenedor, inventario) if contenedor else []
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: archivos_de(%s) falló: %s", contenedor, exc)
    fid = next((i for i, n in candidatos if (n or "").strip() == archivo), None)
    if not fid:
        # Último recurso: el nombre exacto en el inventario completo.
        fid = next((i for i, n in inventario.items() if (n or "").strip() == archivo), None)
    if not fid:
        log.warning("packing_cajas: no se encontró en Drive: %s", archivo)
        return None
    try:
        return pidx.indexar(drive.bajar(fid, archivo), archivo, fid)
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: no se pudo indexar %s: %s", archivo, exc)
        return None


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None
