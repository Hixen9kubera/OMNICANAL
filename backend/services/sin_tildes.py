"""
sin_tildes.py — El título de Mercado Libre va SIN acentos (Brandon, 3-nov-2026).

Misma redacción, solo sin tildes ni diéresis: á→a, é→e, í→i, ó→o, ú→u, ü→u. La ñ SE
QUEDA («Niño», «baño», «año» siguen bien escritos). Es SOLO Mercado Libre: el nombre en
Woo (chunche.shop), Temu, TikTok y Walmart conservan su ortografía. Amazon ya iba sin
acentos desde julio con su propia regla (`ia_generadores._sin_acentos`, que además
cambia ñ→n).

EL PROMPT PIDE Y EL CÓDIGO GARANTIZA, como en `sin_garantia`:
  · `REGLA` va en los prompts del Estudio que escriben el título de ML;
  · `quitar` lo asegura en lo que la IA devuelve y en lo que SALE a ML: el alta
    (`publicar_ready`, sobre `title` y `family_name` del envío, venga de donde venga
    el título) y la actualización de una publicación (`publicar._confirmar_ml`).

LO QUE NO ALCANZA (medido el 3-nov en las 207 publicaciones del lote Deportes):
  · ML arma el título visible con `family_name` + valores de SU lista de atributos
    («… Negro Con Café», «… Único», «Marrón Claro»): esa tilde la pone ML.
  · A una publicación con `family_name` —todas las altas nuevas— ML no le deja
    cambiar el título ni el `family_name` (400, cause 374). Las publicadas antes de
    esta regla se quedan como están.
"""
from __future__ import annotations

import unicodedata

REGLA = ("El TÍTULO va SIN acentos ni diéresis (a, e, i, o, u en lugar de á, é, í, ó, ú, ü): "
         "misma redacción, solo sin tildes. La ñ SÍ se escribe.")

_SE_QUEDAN = frozenset("ñÑ")


def quitar(texto: str | None) -> str:
    """El texto sin tildes ni diéresis; la ñ y todo lo demás quedan igual.

    Se descompone letra por letra en NFD (no NFKD: «½» o «m²» no se tocan) y se
    quitan sus marcas combinantes, salvo en la ñ."""
    if not texto:
        return texto or ""
    return "".join(
        c if c in _SE_QUEDAN
        else "".join(m for m in unicodedata.normalize("NFD", c) if not unicodedata.combining(m))
        for c in unicodedata.normalize("NFC", str(texto)))
