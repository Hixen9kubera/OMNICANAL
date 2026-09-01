"""
costos_publicados.py — "Validar costo de PRODUCTOS PUBLICADOS EN MERCADO LIBRE".

  POST   /api/costos-publicados/preflight       → pronóstico, sin gastar IA
  POST   /api/costos-publicados                 → arranca el análisis
  GET    /api/costos-publicados/{jid}           → estado + filas (polling)
  PATCH  /api/costos-publicados/{jid}/fila      → "es el renglón 34, no el 12"
  POST   /api/costos-publicados/{jid}/archivo   → liga del packing list a mano
  POST   /api/costos-publicados/{jid}/guardar   → UPSERT de lo aprobado

Es el Resolver al revés: en vez de cargar un packing list y empatar sus
renglones contra el catálogo, se parte de unos SKUs y a cada uno se le busca su
renglón. Toda la lógica vive en ``services/packing_publicados.py``.

**Los handlers son `def` síncronos a propósito.** FastAPI los manda al
threadpool; con `async def` cualquiera de las lecturas de aquí (psycopg2, xmlrpc
de Odoo) congelaría el event loop y con él el backend entero — regla 11, la del
apagón de cinco horas.

La regla crítica de Brandon —solo productos publicados en Mercado Libre— se
aplica en el SERVICIO, dos veces: al arrancar y otra vez antes de escribir. Lo
que el frontend filtre es comodidad para el usuario, no la regla. Y "publicado"
tiene UNA sola redacción, la de ``channel_read``: la misma que pinta la insignia
de la tabla de Costos, para que la pantalla no diga una cosa y el botón haga
otra.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings
from services import packing_drive, packing_publicados

log = logging.getLogger("omnicanal.routers.costos_publicados")
router = APIRouter(prefix="/api/costos-publicados", tags=["costos"])

# Cota de la SELECCIÓN, que no es la del trabajo. Aquí solo evita que una lista
# absurda (un "seleccionar todo" de 13k) llegue a expandirse contra Odoo; el
# tope que de verdad acota el gasto se mide en FILAS y es `MAX_FILAS` del
# servicio, más abajo.
_MAX_SKUS = 1_000

# Cotas de los parámetros del costeo. El frontend ya tapa el 0 y el NaN con
# `Number(x) || DEFAULT`, pero un NEGATIVO pasa limpio por ese truco y de ahí
# salen fletes y costos negativos que después se escriben y quedan bajo el
# candado de COSTO VALIDADO — o sea, basura difícil de deshacer. Los topes
# superiores no son opinión de negocio: son "esto es un dedazo" a dos órdenes de
# magnitud de los valores avalados (7,500 MXN/m³ y 19 MXN/USD).
_TARIFA_MAX = 1_000_000.0
_TC_MAX = 1_000.0
_USD_MAX = 100_000.0   # tope del unitario capturado a mano


def _exigir_kubera() -> None:
    """
    Sin lectura de kubera no se puede saber quién está publicado en ML, y **no
    poder verificar no es lo mismo que "no está publicado"**: el fallback MySQL
    ni siquiera tiene `channel.listings`. Falla CERRADO.
    """
    if not settings.supabase_read_costing:
        raise HTTPException(
            503, "Este flujo necesita la lectura desde kubera "
                 "(SUPABASE_READ_COSTING): la publicación en Mercado Libre vive "
                 "en channel.listings, que no existe en MySQL.")


def _filas_expandidas(skus: list[str]) -> int:
    """
    Cuántas CORRIDAS DE ESCALERA saldrían de esta selección — que es lo que hay
    que topar, no cuántos SKUs se marcaron en la pantalla.

    La escalera con IA corre una vez por VARIANTE, no por SKU pedido: 305 de los
    2,524 publicados en ML son padres, así que 200 SKUs pueden ser 200 filas o
    1,011. Medir lo pedido era medir lo que no cuesta.

    La expansión y su catálogo son del servicio (``expandir`` / ``_catalogo``);
    aquí solo se suman, para que el tope cuente EXACTAMENTE lo mismo que después
    se va a procesar. Si Odoo no contesta, ``_catalogo`` vuelve vacío,
    ``expandir`` devuelve el SKU tal cual y la cuenta degrada a una fila por SKU:
    nunca bloquea de más por no poder preguntar.

    Se normaliza y de-duplica igual que el servicio porque un SKU repetido en la
    selección no genera trabajo — se omite como "duplicado" — y no debe gastar
    presupuesto del tope.
    """
    cat = packing_publicados._catalogo()
    unicos = {s.strip().upper() for s in skus if (s or "").strip()}
    return sum(len(packing_publicados.expandir(s, cat)) for s in unicos)


def _exigir_cupo(skus: list[str]) -> None:
    """Frena la tanda ANTES de crearla, y dice cuántas filas saldrían."""
    filas = _filas_expandidas(skus)
    if filas > packing_publicados.MAX_FILAS:
        raise HTTPException(
            400,
            f"Son {len(skus)} SKUs que se abren en {filas} variantes y el máximo "
            f"por tanda es {packing_publicados.MAX_FILAS}. La escalera usa IA una "
            f"vez por variante: una tanda más grande cuesta dinero y no termina "
            f"antes de que el análisis caduque. Selecciona menos SKUs padre.")


class PreflightReq(BaseModel):
    skus: list[str] = Field(default_factory=list)


class ArrancarReq(BaseModel):
    skus: list[str] = Field(default_factory=list)
    # gt=0 y no ge=0: un flete de cero también da un costo que no es el costo.
    # El tope superior está en la cabecera del módulo.
    tarifa_mxn_m3: float = Field(default=packing_publicados.TARIFA_MXN_M3,
                                 gt=0, le=_TARIFA_MAX)
    tipo_cambio: float = Field(default=packing_publicados.TIPO_CAMBIO,
                               gt=0, le=_TC_MAX)
    usar_ia: bool = True


class FilaReq(BaseModel):
    """El renglón que el humano eligió para ese SKU."""
    sku: str
    file_id: str | None = None
    fila_excel: int
    # Precio unitario USD capturado a mano. `None` = respeta el del archivo.
    # Con cota superior porque de aquí sale un costo que se blinda con el
    # candado: un dedazo de más ceros no debería poder escribirse.
    precio_usd: float | None = Field(default=None, gt=0, le=_USD_MAX)


class ArchivoReq(BaseModel):
    """Liga del packing list, para los SKUs sin contenedor conocido."""
    sku: str
    url: str


class GuardarReq(BaseModel):
    skus: list[str] = Field(default_factory=list)
    # Subconjunto de `skus`: SOLO esos se des-marcan antes de escribir. Un SKU
    # con COSTO VALIDADO que no venga aquí se salta con su motivo, nunca se
    # pisa por descuido.
    liberar_candado: list[str] = Field(default_factory=list)


@router.post("/preflight")
def preflight(req: PreflightReq):
    """
    Qué va a pasar con esta selección: cuántos son elegibles, cuántos se omiten
    y por qué, cuántos tienen contenedor y foto, y cuántos ya están validados.

    No crea trabajo ni gasta una sola llamada de IA. Existe porque la cobertura
    real no es del 100% y es mejor decirlo antes.
    """
    _exigir_kubera()
    if not req.skus:
        raise HTTPException(400, "No mandaste ningún SKU.")
    if len(req.skus) > _MAX_SKUS:
        raise HTTPException(
            400, f"Son {len(req.skus)} SKUs y el máximo por tanda es {_MAX_SKUS}.")
    # El pronóstico ya expande; topar aquí con el MISMO criterio evita que la
    # pantalla prometa un trabajo que el arranque va a rechazar.
    _exigir_cupo(req.skus)
    try:
        return packing_publicados.preflight(req.skus)
    except Exception as exc:  # noqa: BLE001
        log.exception("Preflight de publicados falló")
        raise HTTPException(502, f"No se pudo calcular el pronóstico: {exc}")


@router.post("", status_code=202)
def arrancar(req: ArrancarReq):
    """
    Arranca el análisis en segundo plano y devuelve el ``id`` para el polling.

    La validación contra Mercado Libre ocurre ANTES de contestar: los rechazados
    vienen en ``omitidos`` con su motivo, para que el usuario vea de inmediato
    que seleccionó 40 y se van a procesar 12 — no que lo descubra al final.
    """
    _exigir_kubera()
    if not req.skus:
        raise HTTPException(400, "No mandaste ningún SKU.")
    if len(req.skus) > _MAX_SKUS:
        raise HTTPException(
            400, f"Son {len(req.skus)} SKUs y el máximo por tanda es {_MAX_SKUS}.")
    _exigir_cupo(req.skus)
    try:
        r = packing_publicados.iniciar(
            req.skus, tarifa_mxn_m3=req.tarifa_mxn_m3,
            tipo_cambio=req.tipo_cambio, usar_ia=req.usar_ia)
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo arrancar el validador de publicados")
        raise HTTPException(500, f"No se pudo arrancar el análisis: {exc}")

    if not r.get("id"):
        motivos = "; ".join(
            f"{o['sku']}: {o['detalle']}" for o in (r.get("omitidos") or [])[:10])
        raise HTTPException(
            400, "Ninguno de los SKUs seleccionados está publicado en Mercado "
                 f"Libre, así que no hay nada que validar. {motivos}")
    return r


@router.get("/{jid}")
def estado(jid: str):
    """Estado del análisis: mientras corre, el paso y el avance; al terminar,
    una fila por SKU con su renglón, su costo y las fotos para revisarlo."""
    e = packing_publicados.estado(jid)
    if not e:
        raise HTTPException(
            404, "Ese análisis ya no está disponible. Vuelve a lanzarlo: los "
                 "análisis viven 3 horas, no se guardan y no sobreviven a un "
                 "reinicio del backend.")
    return e


@router.patch("/{jid}/fila")
def corregir(jid: str, req: FilaReq):
    """
    Corrige a mano el renglón de un SKU y recalcula SU costo.

    Solo el suyo: aquí el flete sale de una tarifa fija por m³, no de un
    prorrateo sobre el contenedor, así que mover un renglón no mueve a los demás
    —al revés que en el Resolver clásico, donde había que recalcular todo—.
    """
    try:
        fila = packing_publicados.corregir_fila(
            jid, req.sku, req.file_id, req.fila_excel, req.precio_usd)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Corrección de renglón falló")
        raise HTTPException(502, f"No se pudo recalcular: {exc}")
    if fila is None:
        raise HTTPException(404, "Análisis o SKU no encontrado.")
    return fila


@router.post("/{jid}/archivo", status_code=202)
def archivo(jid: str, req: ArchivoReq):
    """
    Le da a un SKU su packing list a mano, con la liga de Drive.

    Es el escape de los SKUs que no tienen contenedor en ninguna fuente: sin
    esto se quedan sin resolver y no hay manera de rescatarlos desde la
    pantalla.
    """
    try:
        fila = packing_publicados.agregar_archivo(jid, req.sku, req.url)
    except packing_drive.DriveError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo usar el archivo indicado")
        raise HTTPException(502, f"No se pudo procesar el archivo: {exc}")
    if fila is None:
        raise HTTPException(404, "Análisis o SKU no encontrado.")
    return fila


@router.post("/{jid}/guardar")
def guardar(jid: str, req: GuardarReq):
    """
    Escribe los costos aprobados en ``costing.costos_validados`` (kubera) y deja
    cada uno bajo el candado de COSTO VALIDADO.

    Es lo ÚNICO que persiste de todo el flujo, y lo hace solo con la lista
    explícita de SKUs que el usuario aprobó. Lo que no se pudo escribir vuelve
    en ``saltados`` **con su motivo**: un contador de "N saltados" sin decir por
    qué hace creer que se guardó lo que no se guardó.
    """
    _exigir_kubera()
    if not req.skus:
        raise HTTPException(400, "No mandaste ningún SKU que guardar.")
    # Sin cota, `guardar` era la puerta abierta: no validaba largo en absoluto y
    # es la única ruta que ESCRIBE. Un trabajo nunca tiene más filas que
    # MAX_FILAS, así que pedir más que eso no es un guardado — es alguien
    # empujando una lista al endpoint. Aquí no hace falta expandir: se guarda por
    # fila ya resuelta, y las variantes ya están contadas dentro del trabajo.
    tope = packing_publicados.MAX_FILAS
    if len(req.skus) > tope or len(req.liberar_candado) > tope:
        raise HTTPException(
            400, f"Un análisis tiene cuando mucho {tope} filas y estás mandando "
                 f"{len(req.skus)} SKUs ({len(req.liberar_candado)} a liberar). "
                 f"Guarda lo que el análisis devolvió.")
    try:
        r = packing_publicados.guardar(jid, req.skus, req.liberar_candado)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Guardar costos de publicados falló")
        raise HTTPException(502, f"No se pudieron guardar los costos: {exc}")
    if r is None:
        raise HTTPException(404, "Análisis no encontrado o ya caducado.")
    return r
