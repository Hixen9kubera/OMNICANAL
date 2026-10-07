"""
publico.py — Lo ÚNICO del backend que se sirve sin credencial y que no es un
webhook ni el healthcheck: las fotos del catálogo para los marketplaces.

POR QUÉ HAY ALGO PÚBLICO AQUÍ
─────────────────────────────
Walmart no recibe la imagen: recibe una URL y manda a su descargador por ella.
Desde el 17-sep ese descargador rechazaba TODAS las de `chunche.shop`
("We are not authorized to download the image") y no entró ni una alta en tres
semanas. La historia completa y lo que se descartó midiendo está en
`services/imagenes_walmart.py`.

QUÉ SE ABRE, EXACTAMENTE
────────────────────────
  GET|HEAD /pub/img/wm/{ruta}.jpg   una foto de `chunche.shop/wp-content/uploads/`
                                    re-codificada a JPEG ≥ 1000 px.
  GET      /robots.txt              200, permite `/pub/img/` y cierra lo demás.

Y POR QUÉ ABRIRLO NO EXPONE NADA
  · Son las fotos de la tienda: ya son públicas en su sitio.
  · El host de origen es FIJO (el de `WC_URL`). La ruta solo elige un archivo
    dentro de `/wp-content/uploads/`: no hay forma de hacer que el backend lea
    otro servidor, ni se siguen redirecciones.
  · La respuesta SIEMPRE es un JPEG recién codificado. Si el archivo no es una
    imagen que se pueda abrir, es un 404: nunca se devuelven bytes ajenos tal
    cual (ni HTML, ni SVG, ni nada que un navegador ejecute).
  · No lee ni escribe base de datos, y no toca nada de `/api`.

`robots.txt` va explícito porque sin él esta API contesta 401 a esa ruta, y un
descargador que consulta robots antes de bajar una imagen no tiene por qué
interpretar un 401 a nuestro favor.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

router = APIRouter(tags=["publico"])

_ROBOTS = "User-agent: *\nAllow: /pub/img/\nDisallow: /\n"
_CABECERAS_IMG = {
    # La URL dice qué archivo es y el archivo no cambia sin cambiar de nombre.
    "Cache-Control": "public, max-age=604800",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    return PlainTextResponse(_ROBOTS, headers={"Cache-Control": "public, max-age=3600"})


@router.api_route("/pub/img/wm/{ruta:path}", methods=["GET", "HEAD"],
                  include_in_schema=False)
async def foto_walmart(ruta: str, request: Request) -> Response:
    """Una foto de la tienda, lista para el descargador de Walmart."""
    from services import imagenes_walmart
    data = await imagenes_walmart.servir(ruta)
    if data is None:
        raise HTTPException(404, "No existe esa imagen.")
    if request.method == "HEAD":
        return Response(status_code=200, media_type="image/jpeg",
                        headers={**_CABECERAS_IMG, "Content-Length": str(len(data))})
    return Response(content=data, media_type="image/jpeg", headers=_CABECERAS_IMG)
