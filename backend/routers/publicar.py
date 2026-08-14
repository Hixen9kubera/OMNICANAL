"""
publicar.py — Paso 4: actualizar la publicación en el canal seleccionado.

  POST /api/publicar/preview       → arma y devuelve el payload (NO escribe nada)
  POST /api/publicar/confirmar     → ejecuta el update en vivo + registra en bitácora
  GET  /api/publicar/amazon/tipos  → buscador de product types (como el picker de ML)
  POST /api/publicar/amazon/tipo   → guarda la elección (meta amz_product_type en Woo)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from services import publicar

router = APIRouter(prefix="/api/publicar", tags=["publicar"])


class AtributoIn(BaseModel):
    # TEMU HABLA OTRO IDIOMA. ML, Amazon y TikTok mandan `{nombre, valor}`; el
    # generador de Temu produce `{name, value: [...]}` porque es el formato que
    # pide `temu.local.goods.v3.add`, y guardarlo traducido sería guardar algo
    # distinto de lo que se publica. Con `nombre` obligatorio, el Estudio mandaba
    # los atributos de Temu y el modelo los rechazaba con un 422 que en pantalla
    # solo decía "No se pudo generar la vista previa" — el mensaje no nombraba ni
    # el canal ni el campo. Medido publicando JUGU-0053-MUL desde el panel.
    #
    # Se aceptan las dos formas y se normaliza a `nombre`/`valor`; `campo` y
    # `valor_id` siguen intactos para TikTok.
    nombre: str = ""
    valor: str = ""
    name: str | None = None
    value: list[str] | str | None = None

    @model_validator(mode="after")
    def _unificar(self) -> "AtributoIn":
        if not self.nombre and self.name:
            self.nombre = self.name
        if not self.valor and self.value is not None:
            self.valor = (", ".join(str(v) for v in self.value)
                          if isinstance(self.value, list) else str(self.value))
        return self
    # EL NOMBRE NO BASTA EN TIKTOK, y este modelo lo estaba tirando.
    #
    # `campo` es el nombre NATIVO del canal (`product_attributes.100107`) y
    # `valor_id` los IDs de valor que TikTok exige — sin ellos rechaza el
    # producto entero con `12052104 … is missing product attribute ID 100107`,
    # aunque el atributo viaje con su nombre y su texto.
    #
    # Pydantic descarta lo que no está declarado, así que el panel los mandaba,
    # el modelo los borraba y el publicador armaba el payload sin ellos. No daba
    # error en el panel: fallaba en TikTok, con un mensaje que parecía decir que
    # faltaba el atributo cuando lo que faltaba era su id. Medido publicando
    # `CONS-0016-EST` el 13-ago.
    campo: str | None = None
    valor_id: list[str] = Field(default_factory=list)


class CamposPublicar(BaseModel):
    titulo: str | None = None
    descripcion: str | None = None
    highlights: str | None = None
    # Amazon: viaja como `generic_keyword`. Sin declararlo aquí, pydantic lo
    # descartaba en silencio y `_rellenar_desde_guardado` no tenía dónde
    # dejarlo — se generaba y se tiraba, como pasó con `highlights`.
    backend_search_terms: str | None = None
    bullets: list[str] = Field(default_factory=list)
    atributos: list[AtributoIn] = Field(default_factory=list)
    # Datos usados al CREAR en Amazon (precio y dimensiones)
    precio_regular: float | None = None
    peso: float | None = None
    largo: float | None = None
    ancho: float | None = None
    alto: float | None = None


class PublicarRequest(BaseModel):
    canal: str = "mercado_libre"
    cuenta: str | None = None
    sku: str | None = None
    wc_id: int | None = None
    item_id: str | None = None
    campos: CamposPublicar = Field(default_factory=CamposPublicar)

    def a_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d["campos"]["atributos"] = [a for a in d["campos"]["atributos"]]
        return d


@router.post("/preview")
async def preview(req: PublicarRequest) -> dict[str, Any]:
    return await publicar.preview(req.a_dict())


@router.post("/confirmar")
async def confirmar(req: PublicarRequest) -> dict[str, Any]:
    # Un fallo aquí llega al usuario como el genérico "ERROR DE CONEXIÓN" y
    # rara vez se reporta → alerta push a Slack con el contexto completo, para
    # enterarnos antes (o aunque nadie reporte). El error se re-lanza igual.
    try:
        return await publicar.confirmar(req.a_dict())
    except HTTPException:
        raise  # errores controlados (validación): ya llegan legibles al modal
    except Exception as exc:
        try:
            from services import alertas
            alertas.avisar(
                f"publicar_500:{req.sku or req.wc_id}",
                f"*Publicar falló (500)*: `{req.sku or req.wc_id}` en "
                f"{req.cuenta or req.canal} — {type(exc).__name__}: "
                f"{str(exc)[:140]}. El panel mostró 'ERROR DE CONEXIÓN'; "
                f"ver logs de Railway (`/api/publicar/confirmar`).")
        except Exception:  # noqa: BLE001
            pass
        raise


@router.get("/amazon/tipos")
async def amazon_tipos(q: str = Query(..., min_length=2, max_length=60)):
    """Busca product types de Amazon por palabras clave (relevancia de Amazon)."""
    tipos = await publicar.buscar_product_types(q)
    return {"tipos": tipos}


@router.get("/amazon/tipo")
async def amazon_tipo_actual(sku: str = Query(...), wc_id: int = Query(...)):
    """El product type que se usaría HOY para este SKU y de dónde sale."""
    pt, origen = publicar._pt_resuelto(sku, wc_id)
    return {"product_type": pt, "origen": origen}


@router.get("/amazon/tipo/sugerido")
async def amazon_tipo_sugerido(sku: str = Query(...), titulo: str = Query("")):
    """
    Un `productType` RECOMENDADO para el panel. Sugerencia: NO se guarda.

    `amazon_ia.sugerir_tipo` existía desde v0.137.0 y **ningún endpoint la
    llamaba**, así que el picker de Amazon solo sabía buscar: quien abría el
    Estudio no veía ninguna propuesta y tenía que adivinar el término en inglés.
    Mientras tanto el detector automático seguía corriendo AL PUBLICAR, que es el
    peor momento para enterarse de que eligió mal — es como
    "Contadora de Monedas" acabó en HOME.

    Se guarda cuando una persona la acepta (el POST de abajo). Escribirla sola la
    volvería indistinguible de una elección humana, y toda la precedencia del
    panel se apoya en esa diferencia.
    """
    from services import amazon_ia, woocommerce
    if not titulo:
        p = await woocommerce.obtener_producto_por_sku(sku)
        titulo = (p or {}).get("nombre") or ""
    return await amazon_ia.sugerir_tipo(sku, titulo)


class TipoAmazonIn(BaseModel):
    sku: str
    wc_id: int
    product_type: str = Field(min_length=2, max_length=80)


@router.post("/amazon/tipo")
async def amazon_tipo(req: TipoAmazonIn):
    """Guarda el product type elegido en el panel (meta `amz_product_type`).
    Esa elección MANDA sobre el histórico y el detector automático."""
    from services import woocommerce
    pt = req.product_type.strip().upper().replace(" ", "_")
    ok = await woocommerce.guardar_meta(req.wc_id, "amz_product_type", pt)
    if not ok:
        raise HTTPException(502, "WooCommerce no aceptó el guardado de la meta.")
    return {"ok": True, "sku": req.sku, "product_type": pt}
