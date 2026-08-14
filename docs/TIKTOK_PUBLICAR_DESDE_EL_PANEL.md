# TikTok Shop MX — publicar desde el panel y mejorar con IA

Traspaso de lo aprendido publicando **970 productos el 12-ago-2026**, para que
el tab de Omnicanal lo haga sin repetir los tropiezos. Todo lo que dice
"medido" se verificó contra la API en vivo ese día.

Complementa a [TIKTOK_MANUAL.md](TIKTOK_MANUAL.md) (operación del canal) y a
[PROMPT_CAMPOS_POR_CANAL.md](PROMPT_CAMPOS_POR_CANAL.md) (campos por canal).

---

# PARTE 1 · Qué necesita un SKU para publicarse

## Los bloqueantes duros

Si falta uno, **no hay publicación**. El panel debería mostrarlos como semáforo
antes de que nadie apriete el botón.

| Requisito | Regla exacta | Se ve en |
|---|---|---|
| **Stock** | `[1, 99999]` — **0 no es válido** | Woo `_stock` |
| **Precio** | > 0 | Woo `_regular_price` |
| **Peso** | > 0 kg. ⚠️ 18 SKUs traen `0.0`, y algunos **188 y 871 kg** | Woo `_weight` |
| **Dimensiones** | **L + A + H ≤ 160 cm** | Woo `_length/_width/_height` |
| **≥1 imagen** | ≥800 px, convertida a JPEG 1000 px | galería Woo |
| **Categoría hoja** | de las 1,937; **416 no están `AVAILABLE`** | API TikTok |
| **Atributos obligatorios** | los de esa categoría (ver abajo) | API TikTok |

## Los que se saltan a propósito

- **Ropa y calzado**: piden guía de tallas, es trabajo aparte.
- **Refacciones mecánicas**: TikTok MX **no tiene dónde ponerlas**. Sus 79 hojas
  de "Automoción" son accesorios (alfombrillas, cámaras, cascos). Ahí el rechazo
  es la respuesta correcta — no forzarlo.

## 🔴 Los dos techos que mandan sobre todo

1. **300 publicaciones a la venta por día** (*Shop Probation Period*). Crear
   borradores **no cuenta**; activarlos sí. No hay forma de consultar el
   contador: sólo se sabe cuando pega `12052093`. Un lote grande se planifica en
   tandas diarias, no de un jalón.

2. **`AS_DRAFT` no valida casi nada; `LISTING` valida todo.** Es la trampa más
   cara del canal: un lote entero puede verse perfecto en borrador y rebotar
   completo al ponerlo a la venta. **Si el panel publica en borrador, el
   semáforo verde no significa que se pueda vender.**

---

# PARTE 2 · El orden de las llamadas

```
1. categoría        POST /product/202309/categories/recommend
                    ⚠️ falla el ~50% → respaldo con IA obligatorio
2. atributos        GET  /product/202309/categories/{id}/attributes
                    ⚠️ obligatorio = llave `is_requried` (errata de TikTok)
3. imágenes         POST /product/202309/images/upload   (SIN shop_cipher)
                    → devuelve `uri`, que es lo que va en el payload
4. crear            POST /product/202309/products        (save_mode)
5. stock            POST /product/202309/products/{id}/inventory/update
6. activar          PUT  /product/202309/products/{id}   save_mode=LISTING
```

⚠️ **El verbo cambia por endpoint** y el único síntoma es `36009010 Invalid
method`. Los volcados de la doc dicen `METHOD: 1` en todos, así que no se puede
leer de ahí: webhooks es `PUT`, inventory es `POST`, editar es `PUT`.

⚠️ **`code=0` no prueba que pasó.** Medido dos veces el mismo día: una
suscripción de webhook que acusó éxito y no quedó, y ~100 borrados que
respondieron `Success` sin borrar nada. **Verificar el estado después de
escribir**, siempre.

---

# PARTE 3 · El prompt para mejorar un listing

**No existía.** Había prompt de *categoría* (`tk_categoria_ia.py`) y de
*atributos* (`services/tiktok_atributos.py`), pero ninguno de **contenido**.
Éste sigue el mismo contrato que ya funciona en ML, Temu y Amazon: **la IA
propone, el código valida, y lo que no pasa no se manda.**

## Límites reales (verificados contra la API, 12-ago)

| Campo | Límite |
|---|---|
| `title` | **MX: [1, 300] caracteres** (otras regiones 255) |
| `description` | HTML, **máx 10,000 caracteres**, máx 30 `<img>` |
| imágenes en la descripción | **sólo URLs rehospedadas por TikTok** — una URL de chunche.shop se rechaza |

## El prompt

```
Eres redactor de fichas de producto para TikTok Shop México.

Mejoras el título y la descripción de un producto que YA está publicado. No
inventas: sólo reescribes con lo que te doy.

PRODUCTO
  SKU:         {sku}
  Título hoy:  {titulo_actual}
  Descripción: {descripcion_actual}
  Categoría:   {ruta_categoria}
  Atributos confirmados: {atributos_validados}

REGLAS
1. NO INVENTES DATOS. Si no sabes el material, los watts, la capacidad o las
   medidas, no los menciones. Un dato inventado se publica sin dar error y
   nadie se entera hasta que un cliente reclama.
2. El TÍTULO manda: es lo que busca la gente. Empieza por QUÉ ES el producto,
   después su rasgo distintivo. Máximo 300 caracteres, sin emojis, sin MAYÚSCULAS
   sostenidas, sin signos de admiración.
3. Escribe como busca un comprador mexicano, no como habla un catálogo chino.
   "Hervidor eléctrico" y no "Kettle 1.7L Multifuncional Home Appliance".
4. NADA de promesas que no controlamos: envío gratis, entrega en X días,
   garantía de por vida, "el mejor", "#1", precios.
5. La DESCRIPCIÓN en HTML simple: <p>, <ul>, <li>, <strong>. Nada de <img>,
   <script>, <table> ni estilos.
6. 3 a 6 puntos clave, cada uno un beneficio concreto, no un adjetivo.

SALIDA — sólo JSON:
{
  "titulo": "<máx 300 caracteres>",
  "descripcion_html": "<HTML simple>",
  "puntos_clave": ["<punto 1>", "..."],
  "palabras_clave": ["<lo que teclearía un comprador>"],
  "confianza": 0.0,
  "flags": ["<lo que NO pudiste confirmar del producto>"]
}
```

## Lo que el código valida antes de mandar

Esto es lo que de verdad protege — no el prompt:

1. **Longitud**: título ≤ 300, descripción ≤ 10,000. Se rechaza, no se trunca:
   truncar corta a media palabra y queda peor que el original.
2. **Etiquetas HTML**: sólo la lista blanca. Cualquier `<img>`, `<script>` o
   `<style>` se elimina.
3. **`flags` = descarte.** Si la IA anota que no confirmó algo, ese dato se
   quita del texto. Salió de un caso real en atributos: el modelo puso "1.5V" y
   en la misma respuesta escribió "voltaje no confirmado".
4. **Comparar contra el original**: si el título nuevo pierde el sustantivo
   principal del viejo, se descarta la propuesta. Así se evita que un
   "Collar de recuperación para gato" se convierta en "Collar elegante".

---

# PARTE 4 · Los productos que hay que mejorar con IA

Prioridad medida sobre el catálogo real:

## 1. Categoría equivocada — lo más urgente

- **227 SKUs** con categoría **APROXIMADA** (la IA navegó ramas y eligió la más
  próxima): lista en `tk_aproximadas.json`.
- ⚠️ **Y los que nadie marcó**: el recomendador de TikTok **también acierta con
  confianza en la categoría equivocada**. Caso real: *"Collar de recuperación
  para gato"* (cono veterinario) quedó en **Accesorios de moda → Joyas para
  disfraces**. Esos **no llevan marca** y no están en los 227.

  **Cómo detectarlos sin revisar 900 a mano**: pedirle a la IA que juzgue
  *título vs categoría asignada* y devuelva sólo los que no cuadran. Es una
  llamada barata por producto y no requiere tocar TikTok.

## 2. Títulos tal cual vinieron de Alibaba

Son los que más venta dejan sobre la mesa: traen inglés mezclado, medidas sin
contexto y palabras que nadie busca en México.

## 3. Descripciones vacías

Hoy la descripción es **el título repetido dentro de un `<p>`** — así lo dejó el
publicador masivo, a propósito, para no inventar contenido sin supervisión. Los
970 productos tienen descripción pobre.

## 4. Atributos sin llenar

Cero obligatorios ≠ da igual: **son los filtros con los que el comprador
encuentra el producto**. Un producto sin atributos existe pero no aparece.

---

# PARTE 5 · Lo que el panel debería mostrar por SKU

Un semáforo por canal, y para TikTok estas filas:

```
TikTok Shop MX
  ├─ stock          ✅ 40          (0 = no publicable)
  ├─ precio         ✅ $515
  ├─ peso           ❌ 0.0 kg      ← bloqueante
  ├─ dimensiones    ✅ 45+30+20 = 95 cm   (tope 160)
  ├─ imágenes       ✅ 5
  ├─ categoría      ⚠️ APROXIMADA  ← revisar
  ├─ atributos      ⚠️ falta "Dirección de Fabricante/Importador"
  └─ estado         DRAFT · no está vendiendo
```

Con dos avisos arriba del tab, que son los que costaron el día:

- **cupo de hoy**: cuántas de las 300 publicaciones diarias quedan
- **borrador ≠ publicable**: un SKU en verde para borrador puede rebotar al
  activarse, porque `LISTING` valida lo que `AS_DRAFT` no
