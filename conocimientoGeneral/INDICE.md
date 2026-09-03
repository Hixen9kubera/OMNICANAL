# ÍNDICE — qué se puede hacer ya

> ¿Chat nuevo? El mensaje de arranque está en [PROMPT_INICIO.md](PROMPT_INICIO.md), y las reglas en [LEEME.md](LEEME.md).

**Este archivo ES la regla 1.** Antes de construir cualquier proceso nuevo, se
busca aquí. Si ya está, se usa. Si no está, se agrega — y **se registra aquí**,
porque un trabajo que no aparece en el índice es un trabajo que el siguiente
chat va a volver a hacer desde cero.

> Cómo leerlo: **PUEDE** = existe y corre hoy. **SE SABE CÓMO** = está
> documentado, pero todavía no hay script. **NO** = no existe y hay que
> construirlo.

---

## Mercado Libre

| Capacidad | Estado | Dónde |
|---|---|---|
| Llenar atributos de una categoría con IA | SE SABE CÓMO | [ML_PUBLICACIONES_IA/01_ATRIBUTOS_IA.md](ML_PUBLICACIONES_IA/01_ATRIBUTOS_IA.md) |
| Redactar título y descripción con IA | SE SABE CÓMO | [ML_PUBLICACIONES_IA/02_CONTENIDO_IA.md](ML_PUBLICACIONES_IA/02_CONTENIDO_IA.md) |
| Elegir categoría y guía de tallas | SE SABE CÓMO | [ML_PUBLICACIONES_IA/03_CATEGORIA_Y_TALLAS.md](ML_PUBLICACIONES_IA/03_CATEGORIA_Y_TALLAS.md) |
| Armar el payload completo de una publicación | SE SABE CÓMO | [ML_PUBLICACIONES_IA/04_PIPELINE_PUBLICAR.md](ML_PUBLICACIONES_IA/04_PIPELINE_PUBLICAR.md) |
| Calcular el precio de venta desde el costo | SE SABE CÓMO | [ML_PUBLICACIONES_IA/05_PRECIO_Y_COSTO.md](ML_PUBLICACIONES_IA/05_PRECIO_Y_COSTO.md) |
| **Generar contenido + atributos de una lista de SKUs** | **PUEDE** | [ML_PUBLICACIONES_IA/scripts/](ML_PUBLICACIONES_IA/scripts/) |

## Imágenes

| Capacidad | Estado | Dónde |
|---|---|---|
| Generar o editar imágenes con IA | NO | crear `IMAGENES_IA/` — es el ejemplo que puso Brandon |
| Convertir a JPEG ≥1000px para Amazon | NO | producción lo hace en `services/imagenes_amazon.py`; falta extraerlo |

## Otros canales

| Capacidad | Estado | Dónde |
|---|---|---|
| Amazon: contenido con IA y sus límites | NO | producción: `services/amazon_ia.py`, `services/amazon_contenido.py` |
| TikTok: categoría y atributos | NO | producción: `services/tiktok*.py`, `docs/TIKTOK_MANUAL.md` |
| Temu | NO | producción: `docs/TEMU_MANUAL.md` |
| Walmart | NO | producción: `scripts/publicar_walmart.py` |

---

## Lo que NUNCA va a estar aquí

Y no por falta de tiempo, sino a propósito:

- **publicar, activar o pausar** una publicación;
- **cambiar precios** o stock en cualquier canal;
- cualquier cosa que **escriba** en WooCommerce, kubera u Odoo.

Todo eso se hace **desde el panel**, que registra quién lo hizo — y esa
trazabilidad es justo lo que se pierde cuando alguien corre un script suelto.

Estos scripts te dan el **contenido ya hecho**. Aplicarlo es una decisión, y las
decisiones llevan nombre.
