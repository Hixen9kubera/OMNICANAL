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
| **Precio óptimo ML FULL (elasticidad precio → visitas → conversión) y costo por prorrateo de 525k por contenedor, con 100 SKUs ubicados en su packing list** | **PUEDE** (solo lectura: propone, no aplica) | Rama `sandbox/precios-optimos`: `backend/sandbox_precios/DISENO.md` y `laboratorio/README.md`. En vivo desde el 28-sep-2026: servicio Railway `laboratorio-precios` |

## Catálogo e inventario

| Capacidad | Estado | Dónde |
|---|---|---|
| **Qué tenemos, cuánto tenemos, COSTO TOTAL, COSTO TOTAL PRORRATEADO y PRECIO TOTAL del inventario: todo el catálogo de Odoo leído en vivo (solo `free_qty` de productos activos — reservados y archivados no cuentan), con precio de venta, categoría, costo de producto, costo prorrateado y packing list, por categoría y en una página web** | **PUEDE** (solo lectura) | [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Costo prorrateado por producto (el contenedor repartido por volumen) y qué mandar a revisión cuando el volumen por pieza está mal capturado | **PUEDE** | `f_costos.py` y `pagina.py` en [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Explicar por qué dos reportes de inventario no dan el mismo número de SKUs «con stock» (libre contra físico, archivados, por bodega) | SE SABE CÓMO | [CATALOGO_VIVO/CONOCIMIENTO.md](CATALOGO_VIVO/CONOCIMIENTO.md), sección 2c |
| **Inventario contado desde los packing lists: comprado − lo que Odoo movió = lo que queda, con SOLD OUT, por SKU, por categoría y por contenedor** | **PUEDE** (solo lectura) | etapas `pl_bajar pl_leer movimientos inventario pagina_pl` de [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Bajar y leer todos los packing lists (originales del proveedor y validados por bodega) sin llave de Storage | **PUEDE** | `pl_bajar.py`, `pl_leer.py`, `pl_parser.py` |
| Historial de movimientos de Odoo por SKU (entradas, salidas a cliente por tipo de socio, ajustes) y compras recibidas | **PUEDE** | `mov_odoo.py` |
| Empatar un SKU con su renglón de packing list (foto idéntica, nombre y cantidad, foto de Odoo, foto parecida, orden de compra) y decir cómo se supo | **PUEDE** | `inventario_pl.py` |
| Título estilo Mercado Libre, término de búsqueda y unidades por paquete para todo el catálogo con DeepSeek (con la foto cuando el nombre no describe) | **PUEDE** (gasta saldo de DeepSeek) | `ia_titulos.py` |
| Precio de la competencia en Amazon México por palabra clave (caja de compra, una por familia, con juez) para todo el catálogo | **PUEDE** | `mercado_amazon.py`, `mercado_comun.py` |
| Precio de la competencia en Mercado Libre: lo que el panel ya midió + catálogo de ML; categoría con el predictor de ML | **PUEDE** (el buscador de ML no se puede leer: ver CONOCIMIENTO 2d) | `mercado_ml.py` |
| Ponerle precio de venta a un SKU: el más bajo a la venta → en pausa → catálogo de WooCommerce; qué descartar (el `1.00` de borrador, el «Sales Price» de Odoo) y qué mandar a revisión (paquete contra pieza, precio sin proporción con el costo) | **PUEDE** | `pagina.py` en [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Darle a cada producto su categoría de mercado (árbol de ML, 31 raíces) desde su publicación o desde WooCommerce | **PUEDE** | etapas `woo` y `categorias` de [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Leer WooCommerce completo por REST, variaciones incluidas (`/products?sku=a,b,c`), sin que el hosting bloquee | **PUEDE** | `f_woo.py` en [CATALOGO_VIVO/scripts/](CATALOGO_VIVO/scripts/) |
| Censar en vivo ML ×2, Amazon (con su estimador de comisiones), TikTok y Walmart; Temu por `/investigacion` | **PUEDE** | `f_ml.py`, `f_amazon.py`, `f_tiktok.py`, `f_walmart.py`, `f_temu.py` |
| Lista de lo que hay que limpiar en el catálogo (sin precio, sin costo, sin categoría, stock dudoso, títulos, SKUs repetidos) con su impacto | **PUEDE** (diagnostica; corregir es desde el panel) | pestaña «Limpieza de datos» que arma `pagina.py` |
| Cómo se lee cada sistema y lo que se midió al hacerlo | SE SABE CÓMO | [CATALOGO_VIVO/CONOCIMIENTO.md](CATALOGO_VIVO/CONOCIMIENTO.md) |

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
