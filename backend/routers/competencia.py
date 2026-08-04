"""
competencia.py — Competencia en Mercado Libre (MVP, 10 SKUs).

  GET    /api/competencia/estado        → qué fuentes están vivas y qué NO se puede medir
  GET    /api/competencia/skus          → los SKUs vigilados con su término general
  POST   /api/competencia/sembrar       → alta de SKUs (nombre/categoría de MySQL + término por IA)
  PATCH  /api/competencia/skus/{sku}    → corrige el término general a mano
  GET    /api/competencia/tabla         → vista por CATEGORÍA con sus SKUs y mis posiciones
  GET    /api/competencia/detalle       → los resultados de un SKU (general | titulo | categoria)
  GET    /api/competencia/corrida       → cuándo se midió por última vez y cuánto costó
  POST   /api/competencia/correr        → dispara la corrida (para pruebas; en prod es un cron)

La corrida mensual la dispara un **cron de Railway** (`backend/railway.competencia.json`
→ `scripts/competencia_cron.py`), no un scheduler embebido: el web es un solo
proceso y si se reinicia el día 1, un cron en proceso no dispara ese mes.
`POST /correr` existe para probar a mano.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from services import (
    competencia_captura, competencia_mas_vendidos, competencia_scraper, competencia_store,
)

log = logging.getLogger("omnicanal.routers.competencia")
router = APIRouter(prefix="/api/competencia", tags=["competencia"])

# Progreso de la corrida manual, en memoria (mismo patrón que sync_woo/crear).
_corrida: dict[str, Any] = {"estado": "inactivo"}

_TIPOS = ("general", "titulo", "categoria")
# Los dos niveles que manda la vista: la RAÍZ del path (Accesorios para
# Vehículos) y la ÚLTIMA categoría (Tapetes). Los intermedios quedan disponibles
# pero no son el caso de uso: agrupar por cat2 partía "Accesorios para Vehículos"
# en Performance / Refacciones / Motos, que fragmenta de más.
_NIVELES = ("raiz_nombre", "categoria_nombre", "cat2", "cat3", "cat4")


class SembrarReq(BaseModel):
    skus: list[str]
    con_ia: bool = True


class TerminoReq(BaseModel):
    termino_general: str


@router.get("/estado")
def estado():
    """Diagnóstico honesto: qué fuentes hay y qué NO se puede medir, con el motivo."""
    return {
        "supabase": competencia_store.disponible(),
        # El raspado del ranking corre con NAVEGADOR LOCAL (selenium + bs4). Apify
        # queda solo para las búsquedas por término, que todavía pasan por el actor.
        "navegador_local": competencia_mas_vendidos.disponible(),
        "scraper_apify": competencia_scraper.disponible(),
        "top_por_busqueda": settings.competencia_top,
        "con_detalle": settings.competencia_con_detalle,
        "costo_por_busqueda_usd": competencia_scraper.costo_estimado(
            1, settings.competencia_top, settings.competencia_con_detalle),
        "limites": {
            "posicion_organica": "Solo por scraper: GET /sites/MLM/search responde 403.",
            "ficha_ajena": "GET /items/{id} de un competidor responde 403; título, "
                           "precio, imagen y descripción solo vienen del scraper.",
            "descripcion": "Es la descripción CORTA derivada de atributos "
                           "('Largo: 4 m | Ancho: 6 m'), no el texto largo del "
                           "vendedor: ML no expone ese texto de publicaciones ajenas.",
            "categoria_del_scraper": "El actor devuelve categoryId nulo; la categoría "
                                     "sale de nuestra taxonomía (categorias_ml).",
            "visitas": "API de ML, funcionan para cualquier publicación, de a UN id "
                       "por llamada (dos ids → HTTP 400).",
            "vendidos": "Del scraper. La API no expone sold_quantity de items ajenos.",
            "historico": "NO se guarda: cada corrida borra la anterior y reescribe.",
        },
    }


# ── SKUs vigilados ───────────────────────────────────────────────────────────

@router.get("/skus")
def skus(solo_activos: bool = True):
    return {"skus": competencia_store.listar_skus(solo_activos)}


@router.post("/sembrar")
def sembrar(req: SembrarReq):
    """
    Alta de los SKUs del MVP. Nombre y categoría salen de MySQL; el término
    general lo propone la IA y queda editable.
    """
    r = competencia_captura.sembrar_skus(req.skus, con_ia=req.con_ia)
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "La siembra falló.")
    return r


@router.patch("/skus/{sku}")
def corregir_termino(sku: str, req: TerminoReq):
    """
    Corrige el término general. Queda marcado como 'manual' y ninguna corrida
    posterior lo vuelve a pisar con una propuesta de la IA.
    """
    termino = req.termino_general.strip()
    if not termino:
        raise HTTPException(422, "El término general no puede ir vacío.")
    if not competencia_store.actualizar_termino(sku, termino):
        raise HTTPException(404, f"No se pudo actualizar el término de {sku}.")
    return {"ok": True, "sku": sku, "termino_general": termino,
            "termino_origen": "manual"}


@router.post("/visitas-propias")
async def visitas_propias(skus: str | None = None):
    """
    Refresca las visitas de 30 días de NUESTRAS publicaciones, por SKU y cuenta.

    Es GRATIS (API de ML, no scraper), así que se puede llamar cuando se quiera y
    no depende de la corrida mensual. Una llamada por publicación: ML no acepta
    multiget (`/visits/items?ids=A,B` → 400) y el endpoint por usuario devuelve el
    total de la cuenta sin desglosar por item.
    """
    lista = [x.strip() for x in (skus or "").split(",") if x.strip()] or None
    r = await competencia_captura.refrescar_visitas_propias(lista)
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "No se pudieron leer las visitas.")
    return r


@router.get("/visitas-propias")
def ver_visitas_propias(sku: str | None = None):
    return {"visitas": competencia_store.visitas_propias(sku)}


# ── Vistas ───────────────────────────────────────────────────────────────────

@router.get("/tabla")
def tabla(agrupar: str = "raiz_nombre", canal: str = "mercado_libre"):
    """
    Una fila por SKU con mi posición en las tres mediciones, agrupada por el nivel
    de categoría que se pida.

    Por defecto agrupa por la RAÍZ del path (`raiz_nombre`), que es la categoría
    principal — "Accesorios para Vehículos", no "Accesorios de Auto y Camioneta".
    `categoria_nombre` agrupa por la última categoría.

    `canal` filtra las publicaciones que se muestran. Hoy solo hay
    'mercado_libre'; cuando entre Amazon, sus ASINs ya vienen como filas con
    canal='amazon' y este filtro los separa sin tocar la vista.
    """
    if agrupar not in _NIVELES:
        raise HTTPException(400, f"agrupar debe ser uno de {_NIVELES}")

    grupos: dict[str, dict[str, Any]] = {}
    for f in competencia_store.tabla():
        f["tiendas"] = [t for t in f.get("tiendas", []) if t.get("canal") == canal]
        clave = f.get(agrupar) or "Sin categoría"
        g = grupos.setdefault(clave, {
            "grupo": clave, "nivel": agrupar,
            # El id del grupo permite pedir SU ranking de más vendidos.
            "categoria_id": f.get("raiz_id") if agrupar == "raiz_nombre"
                            else f.get("categoria_id"),
            "skus": [],
        })
        g["skus"].append(f)

    corrida = competencia_store.ultima_corrida()
    return {
        "agrupar": agrupar,
        "canal": canal,
        "niveles": list(_NIVELES),
        "grupos": sorted(grupos.values(), key=lambda g: g["grupo"]),
        "corrida": corrida,
    }


@router.get("/top-categoria")
def top_categoria(sku: str, limite: int = 10):
    """
    Los más vendidos de la categoría de ese SKU: el ranking OFICIAL de Mercado
    Libre (`/highlights`), que ya viene ordenado por posición. Es lo que se abre
    al hacer clic en la categoría.
    """
    fila = next((s for s in competencia_store.listar_skus(False)
                 if s["sku"] == sku), None)
    if not fila:
        raise HTTPException(404, f"{sku} no está entre los SKUs vigilados.")
    filas = competencia_store.top_categoria(sku, limite)
    return {
        "sku": sku,
        "categoria_id": fila.get("categoria_id"),
        "categoria_nombre": fila.get("categoria_nombre"),
        "ruta": fila.get("ruta"),
        "top": filas,
        # Sin ranking no es un fallo del módulo: ML simplemente no publica
        # "más vendidos" para todas las categorías (Bujías y Turbos, p. ej.).
        "aviso": None if filas else
                 "Mercado Libre no publica ranking de más vendidos para esta categoría.",
    }


@router.get("/vista")
def vista(canal: str = "mercado_libre"):
    """
    El árbol completo del tab, de un solo golpe: raíz → subcategorías → SKUs.

    Un GET y no varios porque la página necesita los tres niveles a la vez para
    poder mostrar NUESTROS SKUs desde el arranque, sin filtrar: el filtro por
    subcategoría es sobre datos que ya están en el navegador.
    """
    arbol = competencia_store.vista(canal)
    # CUÁNDO se capturó el ranking. Es el dato que importa en una vista de solo
    # lectura: el navegador no hace falta para MOSTRAR (los datos ya están), solo
    # para REFRESCAR. Sin esta fecha, la UI advertía de un navegador ausente
    # mientras enseñaba títulos, fotos y precios — un aviso que se contradecía solo.
    capturas = [x["capturado_en"] for r in arbol
                for x in (r.get("top") or []) if x.get("capturado_en")]
    for r in arbol:
        r["nichos"] = competencia_captura.nichos_del_top(r, tope=5)
    return {
        "canal": canal,
        "raices": arbol,
        "capturado_en": max(capturas) if capturas else None,
        # Este servidor NO puede refrescar el ranking (sin Chrome no hay raspado y
        # la API de ML da 403 en publicaciones ajenas). No es un fallo: la captura
        # corre una vez al mes desde una máquina con navegador y sube a Supabase.
        "puede_refrescar": competencia_mas_vendidos.disponible(),
        "aviso": None if arbol else
                 "No hay SKUs vigilados. Corre POST /api/competencia/sembrar.",
    }


@router.get("/sku/{sku}")
def detalle_sku(sku: str, limite_terminos: int = 20):
    """
    Lo que se abre al hacer clic en un SKU: sus dos búsquedas, lado a lado.

    - TÉRMINO GENERAL: los términos más buscados de su subcategoría (`/trends`),
      cada uno marcado según si NUESTRO título lo cubre. Un ✗ arriba de la lista
      es tráfico que no nos puede encontrar.
    - TÉRMINO DIRECTO: el top de la subcategoría, que es la competencia cara a
      cara del mismo producto.
    """
    fila = next((s for s in competencia_store.listar_skus(False) if s["sku"] == sku), None)
    if not fila:
        raise HTTPException(404, f"{sku} no está entre los SKUs vigilados.")

    cat = fila.get("categoria_id")
    pubs = competencia_store.publicaciones(sku)
    # Un título por TIENDA: son distintos y por eso la cobertura de términos se
    # mide por tienda. Si no hay publicaciones, se usa el nombre del catálogo.
    titulos = {p["cuenta"]: p["titulo"] for p in pubs if p.get("titulo")}
    if not titulos:
        titulos = {"catálogo": fila.get("nombre") or ""}
    terminos = (competencia_store.terminos_categoria(cat, titulos, limite_terminos)
                if cat else [])
    total = competencia_store.total_terminos(cat) if cat else 0
    top = competencia_store.ranking_categoria(cat, "hoja", limite=20) if cat else []

    return {
        "sku": sku,
        "nombre": fila.get("nombre"),
        "imagen": fila.get("imagen"),
        "categoria_id": cat,
        "categoria_nombre": fila.get("categoria_nombre"),
        "ruta": fila.get("ruta"),
        "termino_general": fila.get("termino_general"),
        "termino_origen": fila.get("termino_origen"),
        "publicaciones": pubs,
        "terminos": terminos,
        "terminos_total": total,
        "terminos_cubiertos": sum(1 for t in terminos if t.get("cubierto")),
        "top": top,
        # Las dos ausencias posibles, separadas: sin ranking Y sin términos = ML
        # no tiene datos de la categoría (Bujías, Cartuchos de Turbo). Con una sí
        # y otra no, falta correr la captura.
        "sin_datos_ml": not top and not total,
        "aviso": ("Mercado Libre no publica ni ranking ni términos de búsqueda de "
                  "esta categoría. No es un fallo de la captura.")
        if (not top and not total) else None,
    }


@router.get("/subcategoria/{categoria_id}/skus")
def skus_subcategoria(categoria_id: str):
    """
    Todos nuestros SKUs de esa categoría con su barra por tienda.

    Los vigilados traen la barra medida; los del catálogo que nadie mide traen su
    publicación (MLM + link) y visitas/ventas en null, que NO es lo mismo que 0.
    """
    return competencia_captura.skus_de_categoria(categoria_id)


@router.get("/subcategoria/{categoria_id}/terminos")
def terminos_subcategoria(categoria_id: str, limite: int = 30):
    """
    La barra de términos de una subcategoría: qué se busca ahí y qué cubrimos,
    medido contra todos nuestros títulos de ese nicho juntos.
    """
    return competencia_captura.terminos_de_subcategoria(categoria_id, limite)


@router.post("/subcategoria/{categoria_id}/sugerir")
def sugerir_subcategoria(categoria_id: str):
    """
    Palabras clave sugeridas para toda la subcategoría.

    Parte de datos reales —los términos que ML publica y los títulos de los
    líderes— y marca cada palabra con `respaldada`: si no aparece en ninguno de
    los dos, la IA la inventó y hay que poder verlo.
    """
    r = competencia_captura.sugerir_palabras_subcategoria(categoria_id)
    if not r.get("ok"):
        raise HTTPException(422, r.get("motivo") or "No se pudo sugerir.")
    return r


@router.post("/sku/{sku}/sugerir")
def sugerir_sku(sku: str):
    """
    UN título de máximo 60 caracteres para ese SKU, hecho a partir de la
    competencia directa (los títulos que lideran su subcategoría).

    Trae `largo` y `cubre_verificado` recalculados en el backend: lo que la IA
    presume sobre su propio título no basta — en la prueba con Fundas declaraba
    cubrir 22 términos y cubría 2.
    """
    r = competencia_captura.sugerir_titulo(sku)
    if not r.get("ok"):
        raise HTTPException(422, r.get("motivo") or "No se pudo sugerir.")
    return r


@router.get("/ranking-categoria")
def ranking_categoria(categoria_id: str, nivel: str | None = None, limite: int = 10):
    """
    Top de más vendidos de una categoría, raspado de `/mas-vendidos/{cat}`.

    `nivel` es 'raiz' (la categoría principal del path, p. ej. MLM1747 Accesorios
    para Vehículos) u 'hoja' (la última, p. ej. MLM162997 Tapetes). Son las dos
    vistas del tab: el panorama amplio y el nicho exacto.

    Trae ranking del badge oficial, título, score, vendidos, precio base y precio
    con descuento. **Visitas va vacía**: sería otra llamada a la API por cada
    publicación del ranking y esto es panorama, no comparación uno-a-uno.
    """
    if nivel and nivel not in ("raiz", "hoja"):
        raise HTTPException(400, "nivel debe ser 'raiz' u 'hoja'")
    filas = competencia_store.ranking_categoria(categoria_id, nivel, limite)
    return {
        "categoria_id": categoria_id, "nivel": nivel, "top": filas,
        "aviso": None if filas else
                 "Sin ranking guardado para esta categoría. Corre "
                 "POST /api/competencia/rankings para raspar los más vendidos.",
    }


@router.post("/rankings")
async def capturar_rankings():
    """
    Raspa el top 10 de más vendidos de la categoría raíz y de la última categoría
    de cada SKU vigilado. Se agrupa por CATEGORÍA, así que 8 SKUs son 8 páginas.

    Barato: el navegador cobra por cómputo (~$0.007/página), no por item.
    """
    r = await competencia_captura.capturar_rankings_categorias()
    if not r.get("ok"):
        raise HTTPException(400, r.get("motivo") or "La captura falló.")
    return r


@router.get("/detalle")
def detalle(sku: str, tipo: str | None = None):
    """Los resultados de un SKU. `tipo` en general | titulo | categoria."""
    if tipo and tipo not in _TIPOS:
        raise HTTPException(400, f"tipo debe ser uno de {_TIPOS}")
    filas = competencia_store.resultados(sku, tipo)
    por_tipo: dict[str, list[dict[str, Any]]] = {t: [] for t in _TIPOS}
    for f in filas:
        por_tipo.setdefault(f["tipo"], []).append(f)
    return {
        "sku": sku,
        "posiciones": competencia_store.posiciones(sku),
        "resultados": por_tipo if not tipo else {tipo: por_tipo.get(tipo, [])},
    }


@router.get("/corrida")
def corrida():
    return {"ultima": competencia_store.ultima_corrida(), "en_curso": _corrida}


# ── Corrida manual (en producción la dispara el cron de Railway) ────────────

@router.post("/correr")
async def correr(skus: str | None = None):
    """
    Dispara la corrida en segundo plano y responde de inmediato; la UI hace
    polling a /corrida. `skus` acepta una lista separada por comas para probar
    con uno solo sin gastar la corrida completa.
    """
    if _corrida.get("estado") == "corriendo":
        raise HTTPException(409, "Ya hay una corrida de competencia en curso.")

    lista = [s.strip() for s in (skus or "").split(",") if s.strip()] or None

    async def _tarea():
        _corrida.update(estado="corriendo", resultado=None, error=None)
        try:
            r = await competencia_captura.correr(origen="manual", skus=lista)
            _corrida.update(estado="listo" if r.get("ok") else "error",
                            resultado=r, error=r.get("motivo"))
        except Exception as exc:  # noqa: BLE001
            log.error("Corrida de competencia falló: %s", exc)
            _corrida.update(estado="error", error=str(exc)[:500])

    asyncio.create_task(_tarea())
    return {"ok": True, "estado": "corriendo", "skus": lista or "todos"}
