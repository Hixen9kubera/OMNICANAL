"""
inventario.py — Endpoints de la pestaña INVENTARIO · Catálogo Maestro.

TODO ES DE LECTURA. No hay POST/PUT/PATCH a propósito: la pestaña nace VISOR
(ver la cabecera de `services/inventario_maestro.py` para el porqué medido), y
la captura humana de entradas —que sí escribiría stock y por tanto enciende un
flujo vivo— va aparte y con el dale de Brandon.

REGLA 11 DE LA CASA, la que costó el apagón de cinco horas del 13-ago: en una
corrutina nada que espere a la red o al disco se llama de forma síncrona.
Aquí eso aplica a TODO — `wp_db` (pymysql), `supabase_db` (psycopg2) y `odoo`
(xmlrpc) son los tres bloqueantes, y el historial además tarda ~1 s por SKU
contra Odoo. Por eso cada endpoint envuelve su trabajo en `asyncio.to_thread`:
sin eso, un solo clic en Trazabilidad congelaría el backend ENTERO —no solo a
quien lo pidió— mientras Odoo contesta. La excepción son los TRES del flujo del
SKU (`/flujo`, `/flujo/skus`, `/flujo/canal`): no esperan a nadie, leen una foto
en memoria.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.marketplaces import es_canal_valido
from services import inventario_flujo as invf
from services import inventario_maestro as inv
from services import specs_editor

log = logging.getLogger("omnicanal.routers.inventario")

router = APIRouter(prefix="/api/inventario", tags=["inventario"])

# Las causas que el libro de Odoo sabe distinguir. `reales` es el filtro por
# omisión: todo menos los pasos internos PICK/PACK, que son mayoría y ruido.
_CAUSAS = {"todo", "reales", "entrada", "venta", "envio_full", "devolucion",
           "ajuste", "traspaso", "preparacion", "merma", "cuarentena", "otro"}


def _skus(crudo: str | None) -> list[str] | None:
    """Convierte 'A, B , C' en ['A','B','C']. Sin nada, la sonda del piloto."""
    if not crudo or not crudo.strip():
        return None
    return [s.strip() for s in crudo.replace("\n", ",").split(",") if s.strip()]


@router.get("")
async def listar(
    skus: str | None = Query(
        None,
        description="SKUs separados por coma. Sin esto se devuelven los 10 del "
                    "piloto que Brandon fijó como sonda."),
):
    """
    La tabla del catálogo maestro: imagen, empaque, existencias y las cinco
    etapas, un renglón por SKU de WooCommerce (padres y variaciones).

    Sin paginación a propósito: hoy la sonda son 10 SKUs y cada fila cuesta una
    consulta a Odoo. Cuando se abra al catálogo completo, la paginación entra
    junto con el criterio de orden, no antes.
    """
    pedidos = _skus(skus)
    if pedidos and len(pedidos) > 200:
        raise HTTPException(
            400, "Máximo 200 SKUs por consulta: cada fila cruza Woo, Odoo y kubera "
                 "en vivo, y un lote mayor tarda más de lo que aguanta el proxy.")
    try:
        filas = await asyncio.to_thread(inv.filas, pedidos)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.listar falló")
        raise HTTPException(502, f"No se pudo leer el inventario: {exc}") from exc
    return {
        "items": filas,
        "total": len(filas),
        "piloto": list(inv.PILOTO),
        "es_piloto": pedidos is None,
        "resumen": inv.resumen(filas),
    }


# -- Flujo del SKU -------------------------------------------------------------
# Van AQUÍ, antes de las dos comodines `{sku:path}`, y no es estética: la ficha
# se registra al final precisamente para no tragarse a sus hermanas, pero
# `/{sku:path}/movimientos` y la propia ficha casan cualquier cosa. Registradas
# después, `/flujo` se resolvería como la ficha del SKU «flujo» y devolvería
# 404 sin un solo error en los logs. (Ningún SKU real se llama «flujo».)
#
# Estas tres NO usan `asyncio.to_thread` a propósito, y no violan la regla 11:
# solo leen la foto en memoria. El armado (12–35 s contra Odoo) corre en el
# hilo propio de `inventario_flujo`, nunca dentro de la petición.

@router.get("/flujo")
async def flujo():
    """
    Conteos por etapa del flujo del SKU, con definición, fuente y qué
    información falta en cada una. Sin montos: solo SKUs, conteos y fechas.

    Si todavía no hay foto (arranque en frío) responde `estado: "calentando"` y
    deja el armado corriendo; el panel reintenta.
    """
    try:
        return invf.conteos()
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.flujo falló")
        raise HTTPException(502, "No se pudo leer el flujo del SKU") from exc


# Quién lo usa HOY: ninguna pantalla —la barra de /inventario que lo estrenó se
# quitó, y /omnicanal filtra con `etapa=` en /api/productos—; queda para el
# equipo, que lo consulta a mano para diagnosticar qué SKUs trae una etapa.
# Lo mismo vale para `/flujo` de aquí arriba: también se quedó sin pantalla.
@router.get("/flujo/skus")
async def flujo_skus(
    etapa: str = Query(..., description="recibido · bodega_3de4 · validado_bodega · "
                                        "listo_envio · en_full · en_drop · costo_validado"),
    page: int = Query(1, ge=1),
    per_page: int = Query(40, ge=1, le=200),
    q: str | None = Query(None, max_length=60,
                          description="Subcadena del SKU, sin distinguir mayúsculas"),
):
    """
    Una página de los SKUs de una etapa, por SKU ascendente. La tabla de abajo
    los pide después con `GET /api/inventario?skus=` — solo los de la página,
    porque cada fila cruza Woo, Odoo y kubera en vivo.

    Parámetros de consulta y no `/flujo/{etapa}`: así la ruta no se acerca a las
    comodines `{sku:path}`.
    """
    if etapa == "restock":
        raise HTTPException(400, "restock: etapa por definir, sin lista")
    if etapa not in invf.ETAPAS_FILTRABLES:
        raise HTTPException(400, f"{etapa[:40]}: etapa desconocida")
    try:
        return invf.skus_de_etapa(etapa, page, per_page, q)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.flujo_skus(%s) falló", etapa)
        raise HTTPException(502, "No se pudo leer la lista de la etapa") from exc


@router.get("/flujo/canal")
async def flujo_canal(
    canal: str = Query(..., description="general · mercado_libre · amazon · "
                                        "tiktok · temu · walmart · shein"),
    cuenta: str | None = Query(None, max_length=40,
                               description="Solo Mercado Libre: BEKURA o SANCORFASHION. "
                                           "Sin cuenta, «Todas» SUMA publicaciones."),
    criterio: str = Query("todas", pattern="^(todas|publicados|activas)$",
                          description="El interruptor de la lista, no el contador "
                                      "de la pestaña: en TikTok y Walmart no son lo mismo"),
    aplanar: bool | None = Query(None, description="Solo General: cambia la UNIDAD "
                                                   "(filas de Woo en vez de productos)"),
):
    """
    Los conteos del stepper del flujo para UNA pestaña: cuántas publicaciones
    (o productos de Woo, en General) hay en cada etapa con los filtros de canal,
    cuenta y criterio que tiene puestos la lista.

    EL NÚMERO Y EL CLIC VAN SEPARADOS. Si `channel.listings` no se pudo leer,
    las cifras salen en `null` con su motivo y los filtros siguen pulsables: la
    etapa se aplica desde la foto, así que no saber contar no impide filtrar.

    Tampoco usa `asyncio.to_thread`, por lo mismo que `/flujo`: solo lee la foto
    en memoria.
    """
    if not es_canal_valido(canal):
        raise HTTPException(400, f"canal desconocido: {canal[:40]}")
    try:
        return invf.conteos_canal(canal=canal, cuenta=cuenta, criterio=criterio,
                                  aplanar=aplanar)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.flujo_canal(%s) falló", canal)
        raise HTTPException(502, "No se pudo leer el conteo del flujo por canal") from exc


async def ficha(sku: str):
    """La ficha de un SKU — lo mismo que un renglón, pero solo. Alimenta el
    cajón lateral del diseño."""
    try:
        filas = await asyncio.to_thread(inv.filas, [sku])
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.ficha(%s) falló", sku)
        raise HTTPException(502, f"No se pudo leer el SKU: {exc}") from exc
    if not filas:
        raise HTTPException(404, f"SKU {sku} no encontrado")
    f = filas[0]
    # Un SKU que no está en Woo, ni en Odoo, ni tiene renglón de costo, no
    # existe en ninguna parte: eso es un 404. OJO con no confundirlo con el
    # caso legítimo de DEPO-0048-EST, que NO está en Woo ni en Odoo pero SÍ
    # tiene costo de packing list — ése hay que mostrarlo, porque el hueco es
    # justo lo que la pestaña tiene que hacer visible.
    if not f["existe_en_woo"] and not f["existe_en_odoo"] and not f["contenedor"]:
        raise HTTPException(404, f"SKU {sku} no existe en WooCommerce, Odoo ni costos")
    return f


@router.get("/{sku:path}/movimientos")
async def movimientos(
    sku: str,
    causa: str | None = Query(
        "reales", description="Filtro de causa. 'reales' (por omisión) esconde "
                              "los pasos internos PICK/PACK de Odoo."),
    limite: int = Query(200, ge=1, le=1000),
    dias: int | None = Query(
        None, ge=1, le=3650,
        description="Ventana en días. Sin esto, todo el histórico. El SALDO se "
                    "calcula siempre sobre el libro COMPLETO, no sobre la "
                    "ventana: si no, el renglón más viejo arrancaría en cero."),
):
    """
    El historial de bodega de un SKU: entradas, ventas, envíos a FULL/FBA,
    devoluciones, ajustes, traspasos y mermas, con SALDO corriente.

    Sale de Odoo y solo de Odoo — es la única fuente de movimiento real que
    existe en la casa, y son 9 meses de historia que hoy no están copiados en
    ninguna parte. Tarda ~1 s por SKU, de ahí el `to_thread`.

    Devuelve además `pendientes`: las recepciones ABIERTAS del SKU, una fila por
    documento. No son movimientos —nada se movió— pero sin ellas un SKU que
    todavía no llega enseña un historial vacío teniendo cientos de piezas
    prometidas, que es justo la pregunta que la gente trae al abrir la pantalla.
    """
    if causa and causa not in _CAUSAS:
        raise HTTPException(400, f"Causa desconocida: {causa}. "
                                 f"Válidas: {', '.join(sorted(_CAUSAS))}")
    try:
        return await asyncio.to_thread(
            inv.movimientos, sku, None if causa == "todo" else causa, limite, dias)
    except Exception as exc:  # noqa: BLE001
        log.exception("inventario.movimientos(%s) falló", sku)
        raise HTTPException(502, f"No se pudo leer el historial: {exc}") from exc


# -- Registro DIFERIDO de la ruta comodín -------------------------------------
# `{sku}` pasó a `{sku:path}` para que los 293 SKUs con diagonal
# (`CALZ-0194-BLN/AZL-40`) dejen de dar 404: el parámetro normal no puede
# abarcar un `/`, y el servidor decodifica el `%2F` ANTES de enrutar, así que
# tampoco servía escaparlo desde el frontend.
#
# El precio de `:path` es que compila a `.*`, que es GOLOSO. Registrada en su
# lugar original, esta comodín se tragaría a sus hermanas GET de más abajo
# resolviéndolas como un SKU llamado "TEC-0935-ROS/movimientos"
# — y no fallaría: devolvería 200 con la respuesta EQUIVOCADA y NINGÚN
# error en los logs. Starlette devuelve la PRIMERA ruta que casa entera,
# así que la comodín se declara arriba (donde se lee) y se REGISTRA aquí,
# la última.
# ── SPECS EDITABLES (Brandon, 18-sep) ──────────────────────────────────────
# Van ANTES del comodín `/{sku:path}` de abajo: `:path` es goloso y, registrado
# primero, se tragaría "TEC-0370-NEG/specs/mercado_libre" entero como SKU.

class _GuardarSpecs(BaseModel):
    valores: dict[str, str] = {}
    etiquetas: dict[str, str] = {}


@router.get("/{sku:path}/specs/{canal}")
async def specs_de_canal(sku: str, canal: str):
    """Los atributos de un SKU en un canal, con su valor actual. Solo kubera y
    la API pública del canal: nada de WordPress (Brandon, 17-sep)."""
    return await asyncio.to_thread(specs_editor.editor_sync, sku.strip(), canal)


@router.put("/{sku:path}/specs/{canal}")
async def guardar_specs(sku: str, canal: str, body: _GuardarSpecs):
    """Guarda lo que capturó Bodega en `enrich.channel_content`, el mismo sitio
    donde escribe el Publicador. Ver services/specs_editor.py."""
    res = await asyncio.to_thread(specs_editor.guardar_sync, sku.strip(), canal,
                                  body.valores, body.etiquetas)
    if not res.get("ok"):
        raise HTTPException(400, res.get("motivo") or "No se pudo guardar.")
    return res


router.get("/{sku:path}")(ficha)
