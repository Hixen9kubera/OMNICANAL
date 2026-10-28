"""
sin_garantia.py — La IA NUNCA escribe una garantía (Brandon, 28-oct-2026).

La garantía la configuran las KAM en cada canal: en Mercado Libre, «Garantía del
vendedor» y su plazo son condiciones de venta de la publicación, no texto de la
descripción. Una garantía escrita por la IA contradice lo que la KAM pone y no se
puede cumplir.

Medido el 28-oct (solo lectura): 334 de 4,099 descripciones de Woo mencionaban una
garantía que nadie dio («Garantía de 3 años», «Garantia de 1 año por defectos de
fabricacion», «Garantia: 30 días»…), y en `enrich.channel_content` 213 contenidos
de Amazon, 71 de ML, 16 de TikTok y 4 de Walmart. El prompt de Amazon hasta la pedía
en la 5.ª viñeta.

EL PROMPT PIDE Y EL CÓDIGO GARANTIZA, como en Walmart, TikTok y Temu:
  · `REGLA` va en cada prompt que escribe una descripción;
  · `quitar` saca de un texto ya generado la oración, la viñeta o el «Garantía: X»
    que la mencione (Crear y el Estudio de ML/General, que no tienen validador);
  · `menciona` la detecta para los validadores con ciclo de reparación (Amazon,
    TikTok, Walmart), que no pueden recortar sin romper sus límites de largo.

El VERBO no cuenta: «garantiza un secado uniforme» no promete ninguna garantía.
"""
from __future__ import annotations

import re

REGLA = ("NO menciones garantía de ningún tipo (ni del vendedor, ni del fabricante, "
         "ni plazos en días, meses o años): la garantía la configura el equipo en cada canal.")

# «garantía», «garantias», «warranty»… pero no «garantiza» ni «garantizado».
_PALABRA = re.compile(r"\b(?:garant[ií]as?|warrant(?:y|ies))\b", re.IGNORECASE)
# Etiqueta y valor («Garantía: 30 días», «Garantía del vendedor: 30 días»), sin
# llevarse lo que está al lado en la misma línea («Uso: doméstico Garantia: 30 días»).
_ETIQUETA = re.compile(
    r"\b(?:garant[ií]as?|warrant(?:y|ies))\b[^:\n<.]{0,40}:\s*[^\n<.;]{0,60}[.;]?",
    re.IGNORECASE)
_BLOQUE = re.compile(r"<(li|p|h[1-6])\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)
_VINETA = re.compile(r"^(\s*(?:[-•*]|\d+[.)])\s+)")
_LETRA = re.compile(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")


def menciona(texto: str | None) -> bool:
    """True si el texto promete o nombra una garantía."""
    return bool(texto) and bool(_PALABRA.search(str(texto)))


def _sin_oraciones(plano: str) -> str:
    """Texto plano sin las etiquetas ni las oraciones que nombran una garantía."""
    plano = _ETIQUETA.sub(" ", plano)
    partes = re.split(r"(?<=[.!?])\s+", plano)
    return re.sub(r"\s+", " ", " ".join(p for p in partes if not _PALABRA.search(p))).strip()


def quitar(texto: str | None) -> str:
    """
    El texto (HTML simple o texto plano) sin ninguna mención de garantía.

    · Un `<li>`, `<p>` o encabezado que la menciona se queda solo con sus otras
      oraciones —en texto plano: su formato interno se pierde, el resto del HTML
      no—, y desaparece si no le queda nada.
    · Fuera de esos bloques (la descripción de ML va en texto plano) se trabaja
      por línea, conservando la viñeta «- » de la línea si le queda texto.
    """
    if not menciona(texto):
        return texto or ""

    def _bloque(m: re.Match) -> str:
        if not _PALABRA.search(m.group(2)):
            return m.group(0)
        resto = _sin_oraciones(re.sub(r"<[^>]+>", " ", m.group(2)))
        if not _LETRA.search(resto):
            return ""
        abre = m.group(0)[: m.group(0).index(">") + 1]
        return f"{abre}{resto}</{m.group(1)}>"

    salida = _BLOQUE.sub(_bloque, str(texto))
    lineas = []
    for linea in salida.split("\n"):
        if _PALABRA.search(linea):
            vineta = _VINETA.match(linea)
            prefijo = vineta.group(1) if vineta else ""
            resto = _sin_oraciones(linea[len(prefijo):])
            if not _LETRA.search(resto):
                continue
            linea = prefijo + resto
        lineas.append(linea)
    salida = "\n".join(lineas)
    salida = re.sub(r"<(ul|ol)\b[^>]*>\s*</\1\s*>", "", salida, flags=re.IGNORECASE)
    return re.sub(r"\n{3,}", "\n\n", salida).strip()
