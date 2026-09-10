# Prompt — Rediseño de la pestaña **Productos / Publicador**

> Pégalo tal cual al abrir el chat nuevo. Todo lo medido aquí es del **9 y
> 10-sep-2026** contra producción. **Re-mide antes de creer**: el catálogo se
> mueve a diario.

---

## El encargo

Rediseñar **únicamente** la pestaña **Productos (Publicador)** del panel
Omnicanal de Kubera, incluido su **Estudio de producto**.

Lo nuevo respecto al diseño de hoy: **cada variante es un SKU con su propio
contenido**, y ese contenido se publica **por canal** — a veces agrupado bajo una
sola publicación, a veces como publicaciones separadas.

Hay una propuesta de Claude Design con cuatro pantallas (`2a` agrupado en ML,
`2b` individual en TikTok, `2c` desagrupar, `2d` lista). **Tu trabajo no es
copiarla: es aterrizarla.** Abajo están las tres cosas que propone y que hoy **no
existen en el backend**, con su evidencia.

### Lee esto ANTES de proponer nada

| | |
|---|---|
| El modelo de variantes, sus 6 trampas medidas y las piezas reutilizables | [`docs/VARIANTES_COMO_SKU.md`](VARIANTES_COMO_SKU.md) |
| Por qué hoy publicamos **plano** en Mercado Libre, con la medición del daño | [`docs/ML_VARIACIONES.md`](ML_VARIACIONES.md) |
| Reglas de la casa (violarlas ya causó incidentes reales) | [`CLAUDE.md`](../CLAUDE.md) |
| Changelog: entradas **v0.457.0**, **v0.464.0**, **v0.468.0** | [`README.md`](../README.md) |

---

## 1 · El modelo mental, en una tabla

Un producto variable de WooCommerce son **dos objetos**, y el reparto **no es
negociable** — lo impone Woo, no nosotros:

| | vive en la VARIANTE | vive en el PADRE |
|---|---|---|
| SKU, precio, stock | ✅ propios | — |
| **título** | ❌ **no existe** | `post_title` |
| **categoría** | ❌ **0 de 7,477** | `product_cat` |
| descripción | `_variation_description` (3,348 la tienen) | `post_content` |
| imágenes | `_thumbnail_id` (una) | miniatura + galería |
| atributos | el valor **fijado** | la **lista** de opciones |

**La frase que resume todo: la variante tiene lo comercial, el padre tiene lo de
publicar.** De ahí sale cada decisión de pantalla.

Y el corolario que más afecta al diseño: **ninguna variante tiene título ni
categoría propios.** Una ficha de variante **no se puede pintar leyendo sólo su
post** — siempre hay que ir al padre por algo, y la pantalla tiene que decir qué
está heredado. Eso ya existe: `wp_db.postmeta_con_herencia()` devuelve
`(metas, heredadas)` justamente para poder marcarlo.

### Dónde vive el contenido por variante

`enrich.channel_content` (Postgres/kubera), llave **`(sku, canal, cuenta)`**, un
`jsonb` con `titulo`, `descripcion`, `bullets`, `highlights`, `atributos`.

**Es la única casa posible del título por variante**, porque Woo deriva el
`name` de una variación de su padre. La llave empieza por SKU y sigue por canal:
o sea que **el modelo de datos YA soporta contenido distinto por canal**, que es
justo lo que el diseño necesita. Esa parte no hay que inventarla.

---

## 2 · ⚠️ Lo que la propuesta enseña y HOY NO EXISTE

Esto no es para descartar el diseño. Es para que no lo dibujes como si ya
funcionara, y para que quede explícito qué habría que construir.

### 2.1 «Agrupado en Mercado Libre · 1 publicación con 6 variantes» — no existe

`build_payload()` arma **un diccionario plano**. **La llave `variations` no
aparece ni una vez en todo el repositorio.** Verificado en los dos pipelines
(`vendor/ml_ready/publisher_core.py`, 726 líneas, y el original de 1,370).

Y no es que el panel no las cree: **no existe ni una sola en producción.**

| Cuenta | Ítems leídos | Con `variations` |
|---|---|---|
| SANCORFASHION | 2,521 | **0** |
| BEKURA | 2,486 | **0** |

**Qué pasa hoy con un padre variable**: se publica igual, **aplastado** — una
oferta única, con el **precio mínimo** de la familia, y el comprador sin nada que
elegir. El daño está medido: **311 padres publicados solos dejan 1,722 variantes
imposibles de comprar** (`TEC-0377`: 103 variantes → 1 oferta), y otros **107
padres compiten contra sus propias variantes** en la misma cuenta (`ROP-0425`:
22 publicaciones nuestras peleándose la misma búsqueda).

**La buena noticia, para que el diseño no se frene**: 80 familias están **listas
hoy** —sus ejes son color/talla y su categoría de ML los acepta— y consolidar
quitaría **504 publicaciones de 5,314 (9.5% de catálogo duplicado contra sí
mismo)**. El criterio es computable con lo que ya se llama:
`get_category_attrs_cached()` ya trae `tags.allow_variations`, que hoy se ignora.

→ **Diseña el modo agrupado, pero como capacidad NUEVA**, y deja claro en la
pantalla cuándo una familia puede agruparse y cuándo no. Las tres poblaciones y
su respuesta están en `ML_VARIACIONES.md` §5.

### 2.2 «142 productos parecen variantes sueltas del mismo padre» — cuidado ahí

La pantalla `2d` propone detectar familias por **mismo nombre base, distinto
sufijo** (`MASC-1022-ROS` y `MASC-1022-CAF`). Eso es agrupar por el **prefijo del
SKU**, y es exactamente lo que ya causó un incidente documentado:

> Tomar los dos primeros segmentos como padre **ya fusionó en WooCommerce 104
> pares `-EST`/`-MET`, de los que 34 eran productos DISTINTOS** — un
> refractómetro con una hebilla de mancuerna, un cierra-puertas con una funda de
> palanca. Ver `services/odoo.py:640`.

Y al revés también falla: en Odoo, `JUGU-1153-MET` y `JUGU-1153-MET-B` comparten
prefijo pero viven en **plantillas distintas**.

→ **El prefijo es una PISTA, no un parentesco.** El parentesco real es
`wp_posts.post_parent` (Woo) y `product_tmpl_id` (Odoo). El diseño puede
proponerlo como **sugerencia que una persona confirma** —el botón «Revisar y
agrupar» del mockup va en la dirección correcta— pero **nunca** como agrupación
automática, y la pantalla debe decir que es una corazonada, no un hecho.

### 2.3 «Desagrupar» — la consecuencia es de negocio, no de UI

El mockup `2c` ya lo dice bien (*«se pierden 34 preguntas, 12 reseñas y la
antigüedad»*). Confirmado: consolidar o separar significa **dar de baja
publicaciones vivas con historial, reputación y posición de búsqueda**. Eso lo
decide Brandon, no el panel. El diálogo de confirmación escrita es la forma
correcta; mantenlo.

---

## 3 · Qué existe ya, y hay que reutilizar

El aplanado **ya está construido** (v0.457.0 / v0.464.0), pero **apagado por
omisión**: `listado_aplanado: bool = False` en `config.py:82`. Se enciende con
`LISTADO_APLANADO` en Railway o con `?aplanar=true` en `/api/productos`.

⚠️ **Por eso la captura de producción de hoy enseña 2,947 productos con chips
«Padre» y «Ver variantes», y no las 5,297 filas aplanadas.** Verifica en qué
estado está el flag antes de asumir cuál es «el diseño de hoy».

Piezas que un diseño nuevo debería usar en vez de reinventar:

| pieza | qué da |
|---|---|
| `wp_db.indice_plano(vista, …)` | índice aplanado paginado |
| `wp_db.variantes_como_productos(ids)` | una variación **con la forma de un producto REST** |
| `wp_db.postmeta_con_herencia(wc_id)` | `(metas propias, heredadas)` → para marcar en pantalla |
| `wp_db._atributos_de_variacion(id, padre)` | el valor fijado, **no** la lista de la familia |
| `wp_db.hermanas_pendientes(padres)` | `{padre: {total, pendientes}}` |
| `wp_db.imagenes(wc_id)` | miniatura propia + galería propia + las del padre, sin repetir |
| `woocommerce.ruta_escritura(wc_id)` | la ruta REST correcta para ESCRIBIR |

**El truco de diseño que hace que nada más se entere**:
`variantes_como_productos` devuelve la variación con la misma forma que un
producto de la REST, así que normalización, categorías, precios y chips siguen
funcionando sin tocarse. **Si añades una fuente de filas nueva, adapta en la
frontera, no aguas abajo.**

---

## 4 · Números para dimensionar (re-medibles)

| | valor |
|---|---|
| variantes vivas | 7,477, bajo 1,504 padres |
| Productos: anidado → aplanado | 2,908 → **5,297** |
| variantes en `publish` con padre en `draft` | 4,522 |
| sin precio propio | 1,436 · **941 sin precio en ninguna parte** |
| sin miniatura propia | 578 · **140 sin ninguna imagen posible, ni heredando** |
| **con diagonal en el SKU** | **293** |

Las dos últimas filas son de diseño, no de datos: hay **140 filas que no van a
poder mostrar foto** y **293 cuyo SKU lleva `/`** (`CALZ-0194-BLN/AZL-40`). Las
rutas ya se arreglaron con `{sku:path}`, pero ojo — esa comodín **compila a `.*`
y se traga a sus rutas hermanas** si se registra antes que ellas (§2.4 de
`VARIANTES_COMO_SKU.md`). Si el diseño agrega una ruta nueva bajo un SKU, ese es
el detalle que la rompe en silencio: responde **200 con la respuesta
equivocada**.

La relación que **sí** es estable — úsala como prueba, no los absolutos:

```
anidado  = productos_sin_hijas + padres_con_hijas
aplanado = productos_sin_hijas + hijas
```

---

## 5 · Los canales y sus reglas

Cinco canales, y **no se comportan igual**. El diseño tiene que reflejarlo:

| Canal | Cuentas | Agrupación | Nota |
|---|---|---|---|
| **Mercado Libre** | 2 (BEKURA = «Kubera», SANCORFASHION = «San Corpe») | hoy **plano**; agrupado = capacidad nueva | publica el MISMO producto en las dos cuentas |
| **Amazon** | 1 (San Corpe) | individual | el tipo de producto lo manda el panel; se hereda del padre |
| **TikTok Shop** | 1 | individual | API propia |
| **Walmart** | 1 | individual, **un artículo por variante en forma plana** | el veredicto llega minutos después: `ACCEPTED` ≠ publicado |
| **Temu** | 1 | individual | |

Dos cosas que el diseño debe poder expresar:

1. **ML publica en DOS cuentas.** Un chip «Mercado Libre» que no distinga Kubera
   de San Corpe esconde la mitad de la información. Ya pasó antes en Monitoreo.
2. **Aceptado no es publicado.** Walmart y Amazon confirman después. Una paloma
   verde puesta al enviar afirma algo que no se sabe. El rail de estado del
   mockup `2c` (Publicada / Pausada / Sin stock / Falta) va bien encaminado —
   **añade «enviado, sin confirmar»**, que es un estado real y hoy se cuenta como
   éxito.

---

## 6 · Reglas de la casa que aplican

1. **Busca primero en `conocimientoGeneral`** (rama `conocimiento`, worktree
   `Escritorio\omnicanal-conocimiento`). Es la regla 14.
2. **`backend/vendor/` NO SE TOCA** — es el pipeline que publicó 1,200+
   productos. Se ajustan los ADAPTADORES (`publicar_ready.py`, `publicar.py`).
3. **La elección del PANEL manda** sobre cualquier detector automático. Ignorarlo
   ya publicó una máquina sexual en «Máquinas de Coser».
4. **Nunca se publica un SKU padre, siempre la variante.**
5. **Publicar es un flujo de negocio vivo** (regla 3): no sube a `main` sin el
   dale explícito de Brandon. Cambios de UI/lectura sí van directo.
6. **Odoo es el MASTER del inventario**, y `free_qty` — no `qty_available`, que
   no descuenta lo comprometido.
7. **Tres sesiones comparten este repo** (vive en OneDrive) y comparten `HEAD` y
   el índice de git. Saca el trabajo por `git worktree add --detach <tmp>
   origin/main`, commitea por rutas explícitas, y revisa
   `git diff origin/main HEAD | grep '^-'` antes de empujar.

---

## 7 · Cómo verificar sin romper nada

- **Enciende y apaga con `?aplanar=true|false`** en `/api/productos`. Apagarlo
  devuelve la vista de siempre, no una a medias.
- **La REST de Woo miente en dos sitios**: `GET /products?include=<variante>`
  devuelve `[]` **sin error**, y `GET /products/{id}` **lee** una variación pero
  escribir ahí **no persiste, también sin error**. Prueba con `GET` antes de
  asumir que un `PUT` funcionó.
- **Nunca `LEFT JOIN` sobre `wp_postmeta`**: un padre variable guarda una fila
  `_price` por variante (`VEH-0315` tiene 28). Un JOIN devolvió 8,062 filas donde
  había 7,477. Usa `wp_db._meta_sub()`.
- **Prueba la relación, no el número.** Un test que clava `total == 5297` falla
  mañana sin que nada esté roto.

---

## 8 · Lo que hay que preguntarle a Brandon

1. **¿El modo agrupado de ML entra en este diseño, o se dibuja como “próximamente”?**
   Hoy no existe en el backend; construirlo es un proyecto aparte (y toca fan-out
   de stock, sync de inventario y `channel.listings` — ver `ML_VARIACIONES.md` §3).
2. **Las 107 familias canibalizadas**: consolidar da de baja publicaciones vivas
   con reputación. ¿Se consolidan, se dejan, o se decide caso por caso?
3. **Los 149 padres con eje `variante` genérico** (`"VER-XXL"`, color y talla
   pegados en un campo): no se pueden agrupar sin limpiar Woo primero. ¿Se
   limpian, o se quedan individuales?
4. **¿`LISTADO_APLANADO` se enciende con este rediseño?** El diseño cambia de
   sentido según se dibuje sobre 2,947 filas con padres o sobre 5,297 aplanadas.

Empieza por medir, confirma estas cuatro con él, y recién entonces diseña.

---

*(Nota: los archivos de `design_handoff_publicador/` —`support.js`, `README.md`,
`Publicador Studio.dc.html`, `Publicador Studio-standalone.html`— no estaban
accesibles al escribir este prompt. Si el chat nuevo los tiene, léelos: mandan
sobre las capturas.)*
