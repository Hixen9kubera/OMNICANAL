"""
resolver.py — "Resolver" de la pantalla de Costos: packing list vs costos actuales.

  POST   /api/resolver/analizar        → sube el .xlsx y arranca el análisis
  POST   /api/resolver/analizar-url    → lo mismo, desde una liga de Google Drive
  GET    /api/resolver/{id}            → estado + comparación + análisis del agente
  PATCH  /api/resolver/{id}/empate     → corrige a mano el SKU de un renglón
  POST   /api/resolver/{id}/guardar    → UPSERT de lo confirmado en costos_validados

Herramienta de un solo uso: **no persiste nada**. El trabajo vive en memoria del
backend (3 h) y lo único que llega a escribirse es el UPSERT final, con lo que el
usuario confirme.

El análisis corre en segundo plano porque la homologación de un contenedor grande
son cientos de llamadas al LLM. La UI hace polling a ``GET /{id}``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from services import packing_comparador, packing_drive, packing_resolver

log = logging.getLogger("omnicanal.routers.resolver")
router = APIRouter(prefix="/api/resolver", tags=["resolver"])

# Tope de tamaño del .xlsx. NO es un número de adorno: estos packing lists
# llevan la FOTO de cada producto embebida —el parser las saca del ZIP a mano y
# el empate por imagen las necesita cuando el texto no alcanza ("Auriculares"
# del proveedor contra "Audífonos Invisibles Bluetooth" del catálogo)—, así que
# un contenedor real con cientos de fotos pesa decenas de MB. Con el tope en 25
# la herramienta rechazaba justo los archivos para los que fue construida
# (medido el 19-ago-2026: cuatro 413 seguidos, y también por la liga de Drive,
# que pasa por aquí mismo).
#
# 100 MB cabe de sobra: el contenedor de Railway tiene 24 GB de RAM y usa 0.3.
# Lo que se queda 3 h en memoria son las MINIATURAS (≤90 KB c/u), no las fotos
# originales — ver packing_resolver._miniatura.
_MAX_MB = 100


class AnalizarUrlReq(BaseModel):
    url: str
    contenedor: str | None = None
    costo_contenedor: float | None = None
    tipo_cambio: float | None = None
    usar_vision: bool = True


class EmpateReq(BaseModel):
    indice: int
    sku: str | None = None


class FilaEditadaReq(BaseModel):
    """Valores finales de un renglón. Lo que no venga conserva lo calculado."""
    indice: int
    sku: str | None = None
    largo: float | None = None
    ancho: float | None = None
    alto: float | None = None
    peso: float | None = None
    costo_producto: float | None = None
    costo_cbm: float | None = None
    costo_total: float | None = None
    cajas: float | None = None
    piezas_por_caja: float | None = None


class GuardarReq(BaseModel):
    """``skus`` acota qué se escribe; si no viene, se guarda todo lo que tenga SKU."""
    skus: list[str] | None = None
    editados: list[FilaEditadaReq] | None = None


def _arrancar(datos: bytes, nombre: str, contenedor: str | None,
              costo_contenedor: float | None, tipo_cambio: float | None,
              usar_vision: bool) -> dict:
    if not datos:
        raise HTTPException(400, "El archivo llegó vacío.")
    if len(datos) > _MAX_MB * 1024 * 1024:
        raise HTTPException(
            413,
            f"El archivo pesa {len(datos) / 1024 / 1024:.1f} MB y el máximo es "
            f"{_MAX_MB} MB. Casi todo el peso son las fotos embebidas: en Excel, "
            f"Formato de imagen → Comprimir imágenes → 150 ppp, aplicar a todas, "
            f"y guardar. NO las borres: el empate por imagen las usa.")
    try:
        return packing_resolver.iniciar(
            datos, nombre, contenedor=contenedor,
            costo_contenedor=costo_contenedor, tipo_cambio=tipo_cambio,
            usar_vision=usar_vision,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("No se pudo arrancar el resolver")
        raise HTTPException(500, f"No se pudo arrancar el análisis: {exc}")


@router.post("/analizar")
async def analizar(
    archivo: UploadFile = File(...),
    contenedor: str | None = Form(None),
    costo_contenedor: float | None = Form(None),
    tipo_cambio: float | None = Form(None),
    usar_vision: bool = Form(True),
):
    """Sube el packing list y arranca el análisis en segundo plano."""
    nombre = archivo.filename or "packing.xlsx"
    if not nombre.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "El archivo debe ser .xlsx (el .xls antiguo no se lee).")
    return _arrancar(await archivo.read(), nombre, contenedor,
                     costo_contenedor, tipo_cambio, usar_vision)


@router.post("/analizar-url")
def analizar_url(req: AnalizarUrlReq):
    """
    Igual que /analizar, pero bajando el .xlsx de una liga de Google Drive.

    Solo funciona con archivos compartidos como "Cualquier persona con el
    enlace": no se usan credenciales de Google.
    """
    if not packing_drive.es_url_drive(req.url):
        raise HTTPException(
            400, "Por ahora solo se aceptan ligas de Google Drive.")
    try:
        datos, nombre = packing_drive.descargar(req.url)
    except packing_drive.DriveError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Descarga de Drive falló")
        raise HTTPException(502, f"No se pudo bajar el archivo: {exc}")
    return _arrancar(datos, nombre, req.contenedor, req.costo_contenedor,
                     req.tipo_cambio, req.usar_vision)


@router.get("/{jid}")
def estado(jid: str):
    """
    Estado del análisis. Mientras corre trae el paso y el avance; al terminar,
    la comparación completa, el análisis del agente y el TSV para copiar.
    """
    e = packing_resolver.estado(jid)
    if not e:
        raise HTTPException(
            404, "Ese análisis ya no está disponible. Vuelve a subir el archivo "
                 "(los análisis viven 3 horas y no sobreviven a un reinicio del backend).")
    return e


@router.get("/{jid}/buscar-sku")
def buscar_sku(jid: str, q: str = Query(..., min_length=2)):
    """
    Busca un SKU en todo el catálogo para empatarlo a mano.

    Cubre el punto ciego del empate por contenedor: si el SKU correcto quedó
    capturado con otro contenedor, no sale entre los candidatos y el renglón se
    queda huérfano. Cada resultado dice con qué contenedor está hoy y **en qué
    renglones de este análisis ya se usó**, para no asignarlo a ciegas.
    """
    _exigir_supabase()
    resultados = packing_comparador.buscar_sku(q)

    e = packing_resolver.estado(jid) or {}
    filas = (e.get("comparacion") or {}).get("filas") or []
    usados: dict[str, list[int]] = {}
    for i, f in enumerate(filas):
        if f.get("sku"):
            usados.setdefault(f["sku"], []).append(i)

    for r in resultados:
        r["usado_en_filas"] = usados.get(r["sku"], [])
    return {"resultados": resultados}


@router.patch("/{jid}/empate")
def empate(jid: str, req: EmpateReq):
    """Corrige el SKU de un renglón y recalcula SU comparación contra el costo actual."""
    fila = packing_resolver.actualizar_empate(jid, req.indice, req.sku)
    if fila is None:
        raise HTTPException(404, "Análisis o renglón no encontrado.")
    return fila


class CapturaReq(BaseModel):
    """
    Datos que el usuario captura de un renglón. Manda lo que tengas: el
    solucionador deduce el resto hasta llegar a dimensiones y peso por pieza.
    """
    indice: int
    numero_cajas: float | None = None
    unidades_por_caja: float | None = None
    unidades_totales: float | None = None
    largo_caja: float | None = None
    ancho_caja: float | None = None
    alto_caja: float | None = None
    peso_caja: float | None = None
    cbm_caja: float | None = None
    costo_usd: float | None = None
    # Los de pieza también son capturables: si el usuario los sabe, mandan
    # sobre cualquier estimación.
    largo_pieza: float | None = None
    ancho_pieza: float | None = None
    alto_pieza: float | None = None
    peso_unidad: float | None = None
    cbm_por_pieza: float | None = None


@router.patch("/{jid}/fila")
def capturar(jid: str, req: CapturaReq):
    """
    Captura datos de caja o de pieza y re-deriva todo lo que dependa de ellos.

    Recalcula el contenedor completo, no solo el renglón: el flete se prorratea
    sobre el CBM total, así que cambiar las unidades de una fila mueve el costo
    de todas.
    """
    _exigir_supabase()
    datos = req.model_dump(exclude_none=True)
    datos.pop("indice", None)
    if not datos:
        raise HTTPException(400, "No mandaste ningún dato que capturar.")
    try:
        r = packing_resolver.capturar(jid, req.indice, datos)
    except Exception as exc:  # noqa: BLE001
        log.exception("Captura falló")
        raise HTTPException(502, f"No se pudo capturar: {exc}")
    if r is None:
        raise HTTPException(404, "Análisis o renglón no encontrado.")
    return r


@router.post("/{jid}/guardar")
def guardar(jid: str, req: GuardarReq):
    """
    Escribe los costos confirmados en costos_validados (MySQL).

    Se saltan los renglones sin SKU y los que no tengan dimensiones de pieza:
    un cero ahí se traduce en un peso volumétrico mal calculado y un fee de envío
    equivocado en cada venta de ese SKU.
    """
    try:
        r = packing_resolver.guardar(
            jid, req.skus,
            [e.model_dump(exclude_none=True) for e in (req.editados or [])],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Guardar costos falló")
        raise HTTPException(502, f"No se pudieron guardar los costos: {exc}")
    if r is None:
        raise HTTPException(404, "Análisis no encontrado o todavía sin resultado.")
    return r
