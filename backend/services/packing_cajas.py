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

import gc
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
# Cuantos packing lists se abren por pasada. Cada uno puede costar medio
# giga de RAM, y el contenedor tambien atiende el webhook de ventas de ML.
# El tope es de TIEMPO, no de memoria: cada indice se suelta antes de abrir el
# siguiente, asi que el pico es UN archivo, no N. Doce archivos a ~30 s son 6
# min en un hilo de fondo, que nadie espera.
_MAX_ARCHIVOS = 12
# Umbral del dHash, el mismo de la validacion de costos (92% de acierto medido).
_DIST_ACEPTA = 8
# Hueco minimo contra el siguiente candidato para dar el empate por bueno.
_HUECO_MINIMO = 8
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
    """El trabajo lento: renglón → Drive → xlsx → cartones. Deja todo en la
    caché, incluidos los SKUs sin dato (como `None`), para no reintentarlos.

    EL RENGLÓN SALE DE DOS SITIOS, en este orden:

      1. `costing.caja_compartida` — lo que la validación de costos ya empató y
         guardó. Barato: la fila está en la BD.
      2. LA FOTO DE ODOO contra las fotos del packing list — cuando no hay fila
         registrada. Es la misma escalera de la pestaña Costos (sha256 exacto,
         luego dHash ≤ 8), y hace falta porque `caja_compartida` solo tiene 60
         filas para 15,849 SKUs: sin este peldaño el 99.6% del catálogo se queda
         sin renglón teniendo su packing list en Drive.

    El peldaño 2 lo pidió Brandon el 9-sep con un caso concreto: `ORG-0863-ROS`
    salía «sin renglón del packing list» teniendo su archivo ahí. Medido: su
    foto de Odoo empata con **distancia 0** contra el renglón 28 de
    `TLLU5922910 Lista de empaque.xlsx` —25 cajas y 50,000 piezas, que es
    exactamente lo que dice Odoo— y el siguiente candidato está a distancia 22.
    No hay ambigüedad.
    """
    from services import odoo, supabase_db as sdb
    from services import packing_drive_carpeta as drive, packing_indice as pidx

    registrados: dict[str, dict[str, Any]] = {}
    try:
        for f in sdb.fetch_all(
                "select sku::text as sku, archivo, renglones, contenedor_base "
                "from costing.caja_compartida where sku::text = any(%s)", (skus,)):
            registrados[f["sku"]] = f
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: caja_compartida no disponible: %s", exc)

    try:
        inventario = drive.inventario()
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: inventario de Drive no disponible: %s", exc)
        return {}

    # ── A qué ARCHIVO va cada SKU ────────────────────────────────────────────
    # Se agrupa por archivo a propósito: indexar un packing list cuesta hasta
    # medio giga de RAM (medido, 473 MB de pico con el de 89 MB y 394 fotos) y
    # esto corre en el MISMO contenedor que atiende el webhook de ventas de
    # Mercado Libre. Agrupando se abre cada archivo UNA vez y se suelta antes de
    # pasar al siguiente, en vez de tener varios vivos a la vez.
    contenedores: dict[str, str] = {}
    sin_registro = [x for x in skus if x not in registrados]
    if sin_registro:
        try:
            contenedores = odoo.contenedores_por_sku(sin_registro)
        except Exception as exc:  # noqa: BLE001
            log.warning("packing_cajas: contenedores de Odoo: %s", exc)

    por_archivo: dict[tuple[str, str], list[str]] = {}
    for sku in skus:
        reg = registrados.get(sku)
        if reg:
            archivo = (reg.get("archivo") or "").strip()
            if archivo:
                clave = (archivo, reg.get("contenedor_base") or "")
                por_archivo.setdefault(clave, []).append(sku)
            continue
        crudo = contenedores.get(sku.upper()) or contenedores.get(sku) or ""
        if not crudo:
            continue
        for ref in drive.codigos_de(crudo):
            hallados = drive.archivos_de(ref, inventario)
            if hallados:
                por_archivo.setdefault((hallados[0][1], ref), []).append(sku)
                break

    # Tope por pasada: sin él, pedir 200 SKUs de 40 contenedores distintos
    # abriría 40 archivos grandes seguidos. Los que no entren NO se cachean, así
    # que se resuelven en la pasada siguiente.
    archivos = list(por_archivo.items())[:_MAX_ARCHIVOS]
    if len(por_archivo) > _MAX_ARCHIVOS:
        log.info("packing_cajas: %s archivos pedidos, se abren %s en esta pasada",
                 len(por_archivo), _MAX_ARCHIVOS)

    salida: dict[str, dict[str, Any]] = {}
    for (archivo, contenedor), suyos in archivos:
        ix = _indexar(archivo, contenedor, inventario, drive, pidx)
        if ix is None:
            continue
        try:
            for sku in suyos:
                reg = registrados.get(sku)
                base = sorted(int(r) for r in (reg.get("renglones") or [])) if reg else []
                origen = "registrado" if base else "foto"
                if not base:
                    base = _por_foto_de_odoo(ix, sku, pidx)
                if not base:
                    continue
                dato = _medir(ix, base, archivo, origen)
                if dato:
                    salida[sku] = dato
        finally:
            # SOLTAR EL ÍNDICE ANTES DEL SIGUIENTE ARCHIVO. No es higiene: son
            # cientos de MB de fotos por archivo y el contenedor es compartido.
            del ix
            gc.collect()

    ahora = time.monotonic()
    for sku, v in salida.items():
        _cache[sku] = (ahora, v)
    # Solo se marcan «sin dato» los que de verdad se intentaron: los que se
    # quedaron fuera por el tope deben reintentarse, no cachearse en vacío.
    intentados = [x for _k, v in archivos for x in v]
    _marcar_sin_dato(intentados, set(salida))
    log.info("packing_cajas: resueltos %s de %s SKUs (%s archivos)",
             len(salida), len(skus), len(archivos))
    return salida


def _medir(ix: Any, base: list[int], archivo: str,
           origen: str) -> dict[str, Any] | None:
    """De un puñado de renglones a la cifra de cajas y piezas.

    LOS RENGLONES SE COMPLETAN CON LA FOTO. `caja_compartida` guarda los que la
    validación llegó a empatar, y a veces son MENOS de los que el SKU ocupa. Se
    suman sus gemelos por dHash dentro del mismo archivo — gratis, el índice ya
    trae las fotos hasheadas.

    Medido el 9-sep contra lo que Odoo pidió, en los 9 SKUs con renglón: solos,
    los registrados aciertan 4; solos, los gemelos por foto aciertan 4 pero
    PIERDEN uno (`ROP-0731-BLN` baja de 4 renglones a 3, porque son vestidos de
    colores distintos y el dHash los separa). La UNIÓN acierta 6 y no pierde
    ninguno, así que es la unión.
    """
    # LOS GEMELOS SOLO SE BUSCAN CUANDO EL RENGLON VIENE REGISTRADO. Ahi la
    # validacion de costos ya decidio QUE producto es, y sus gemelos por foto
    # son mas renglones del mismo SKU en el mismo embarque: sumarlos acierta
    # (TV-0001-MET pasa de 1 renglon a 40 y da exacto las 40 piezas que pidio
    # Odoo). Cuando el renglon lo encontro la foto es al reves: los "gemelos"
    # son OTROS LOTES del mismo producto, y sumarlos infla. Medido: TEC-0008-AMR
    # daba 600 piezas -contra 208 recibidas- por arrastrar un segundo lote.
    todos = (sorted(set(base) | _gemelos_por_foto(ix, base[0]))
             if origen == "registrado" else sorted(set(base)))

    piezas = 0.0
    cajas = 0.0
    cartones: set[frozenset] = set()
    compartida = False
    renglones_carton = len(todos)
    piezas_grupo = None

    for r in todos:
        try:
            d = ix.datos(r)
        except Exception:  # noqa: BLE001
            continue
        # Las PIEZAS se suman entre renglones: son piezas distintas.
        piezas += float(d.get("piezas_fila") or 0)
        if piezas_grupo is None:
            piezas_grupo = _num(d.get("piezas_grupo"))

        # Las CAJAS no: se cuentan UNA VEZ POR CARTÓN. Cuando varios renglones
        # comparten cartón, todos reportan el mismo número (se hereda del ancla
        # del merge) y sumarlos multiplica la caja.
        grupo = set(d.get("grupo") or [])
        if grupo:
            compartida = True
            renglones_carton = max(renglones_carton, len(grupo | set(todos)))
        carton = frozenset(grupo | {r})
        if carton in cartones:
            continue
        cartones.add(carton)
        c = d.get("cajas")
        # `Indice.cajas()` devuelve None —y no 1— cuando el archivo no trae
        # columna de cartones. No se inventa el 1.
        if c is not None:
            cajas += float(c)

    if not cartones:
        return None
    return {
        "cajas": round(cajas, 2) or None,
        "compartida": compartida,
        "renglones_carton": renglones_carton,
        "renglones": todos,
        "renglones_registrados": base,
        # De dónde salió el renglón: de la tabla o de la foto de Odoo.
        "origen_renglon": origen,
        "archivo": archivo,
        "piezas_fila": round(piezas, 2) or None,
        "piezas_grupo": piezas_grupo,
    }


def _por_foto_de_odoo(ix: Any, sku: str, pidx: Any) -> list[int]:
    """El renglón del SKU buscado con SU FOTO DE ODOO, cuando no hay registro.

    Los dos primeros peldaños de la escalera de la pestaña Costos, y solo esos:
    sha256 exacto y, si falla, dHash ≤ 8. NO se sube al peldaño de título ni al
    de IA — cuestan llamadas de red por SKU, y aquí se busca un dato de apoyo,
    no una validación de costo. Si la foto no alcanza, el SKU se queda sin
    renglón y la pantalla lo dice en vez de inventarlo.
    """
    import base64
    import hashlib
    from services import odoo
    try:
        uri = (odoo.miniaturas_por_sku([sku]) or {}).get(sku) or ""
        if "," not in uri:
            return []
        crudo = base64.b64decode(uri.split(",", 1)[1])
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: foto de Odoo de %s: %s", sku, exc)
        return []
    if not crudo:
        return []

    sha = hashlib.sha256(crudo).hexdigest()
    exactos = [ix.idx_de_fila[i] for i, f in ix.fotos.items()
               if i in ix.idx_de_fila and f.get("sha") == sha]
    if exactos:
        return sorted(exactos)

    dh = pidx.dhash(crudo)
    if dh is None:
        return []
    cerca = sorted((pidx.distancia(f["dh"], dh), ix.idx_de_fila[i])
                   for i, f in ix.fotos.items() if i in ix.idx_de_fila)
    if not cerca or cerca[0][0] > _DIST_ACEPTA:
        return []

    # EL EMPATE TIENE QUE SER INEQUIVOCO, y esta es la parte que mas importa.
    # Estos packing lists traen VARIOS RENGLONES DEL MISMO PRODUCTO -lotes
    # distintos, con fotos casi identicas- y quedarse con "el mas parecido"
    # es echar un volado con cara de dato. Medido el 9-sep en TEC-0008-AMR:
    # cuatro renglones "Lavadora de autos" a distancias 3, 5, 10 y 24, con
    # 200, 400, 300 y 200 cajas. Elegir el de distancia 3 no esta justificado
    # cuando hay otro a 5.
    #
    # Asi que se exige un HUECO claro contra el siguiente candidato que no sea
    # el mismo empate. Sin hueco no se devuelve nada y la pantalla dice "sin
    # renglon", que es la verdad: no se sabe cual es.
    mejor = cerca[0][0]
    filas = [f for d, f in cerca if d == mejor]
    siguiente = next((d for d, _f in cerca if d > mejor), None)
    if siguiente is not None and (siguiente - mejor) < _HUECO_MINIMO:
        log.info("packing_cajas: %s sin empate inequivoco (mejor %s, sigue %s)",
                 sku, mejor, siguiente)
        return []
    return sorted(filas)


def _marcar_sin_dato(skus: list[str], resueltos: set[str]) -> None:
    """Los que no tienen renglón también se cachean, como `None`: si no, cada
    carga volvería a bajar y abrir los xlsx para no encontrar nada."""
    ahora = time.monotonic()
    for s in skus:
        if s not in resueltos:
            _cache[s] = (ahora, None)


def _gemelos_por_foto(ix: Any, fila: int, umbral: int = 8) -> set[int]:
    """Los renglones del MISMO archivo cuya foto es la misma que la de `fila`.

    Es la misma detección de imagen que usa la validación de costos, en su
    peldaño de dHash: distancia de Hamming ≤ 8 sobre 64 bits, el umbral que ya
    está medido en el repo (92% de acierto exacto). Aquí es aún más seguro que
    allá, porque no se compara contra el catálogo entero sino contra los
    renglones de UN packing list.

    Nunca reemplaza a los renglones registrados, solo los completa: en
    `ROP-0731-BLN` esta búsqueda encuentra 3 y los registrados son 4 (son cinco
    vestidos de colores distintos y el dHash los separa). Por eso el llamador
    hace la unión.
    """
    from services import packing_indice as pidx
    try:
        idx = ix.fila_de_idx.get(fila)
        base = ix.fotos.get(idx) if idx is not None else None
        if not base:
            return set()
        return {ix.idx_de_fila[i] for i, ft in ix.fotos.items()
                if i in ix.idx_de_fila
                and pidx.distancia(ft["dh"], base["dh"]) <= umbral}
    except Exception as exc:  # noqa: BLE001
        log.warning("packing_cajas: gemelos por foto de la fila %s: %s", fila, exc)
        return set()


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
