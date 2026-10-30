# Publicar en Mercado Libre desde el Catálogo — el método (28-oct-2026)

> Pedido de Brandon (30-oct-2026): «guarda el progreso de cómo es que publicaste para que podamos empezar a publicar
> desde catálogo a mercado libre». Esto es lo que se hizo, paso por paso y con las reglas que salieron en el camino,
> para convertirlo en el botón «Publicar» de la pestaña **Catálogo** (`/catalogo`, v0.703.0).
>
> Repo PÚBLICO: aquí no van costos, tokens ni credenciales.

## Qué se publicó con este método

| Tanda | SKUs | Publicaciones | Estado |
|---|---|---|---|
| 28-oct (mañana), republicadas por cambio de categoría | MUE-0080-NEG, TEC-1450-PLA, OFI-0110-AZL, TEC-0686-NEG, DEC-0019-VER | 10 (BEKURA + SANCORFASHION) | pausadas |
| 28-oct (tarde), con foto del packing list | TEC-0110-NEG, MES-0058-BLN-DOR, JUGU-0047-MUL, TEC-0234-PLA, TEC-1261-NEG, TEC-0214-AZL, ORG-1071-EST, MUN-0050-BEI, VAR-0123-PC-NEG, TEC-1277-NEG-48W | 20 | pausadas |

Los IDs de cada publicación están en `Documents\omnicanal_catalogo_ml_26oct\publicadas_28oct_v2.csv` y
`Documents\omnicanal_fotos_pl_28oct\publicadas_28oct_fotos_pl.csv` (fuera del repo).

## El método, paso por paso

Todo va por las rutas del PANEL, con la sesión de quien publica: nada de scripts que escriban directo en Woo o en ML.

### 0. Escoger los SKUs

- Que estén en **Crear** como borrador de Woo, con stock y con precio de ML en el catálogo maestro.
- **Producto simple.** Una variante se puede publicar, pero nunca el SKU padre (ver memoria «nunca publicar SKU padre»);
  en las dos tandas se evitaron las variantes para no mezclar galerías.
- Fuera ropa y calzado: piden guía de tallas.
- Desde v0.705.0 la pestaña Catálogo abre en **«Por validar»**, ordenada por mayor retorno. Un SKU con motivo para
  validar (precio por revisar, foto que no corresponde, título por confirmar, contenedor sin validar…) se valida ANTES
  de publicarlo.

### 1. Precio

- **Regla de Brandon (28-oct):** el precio de ML sale del **catálogo maestro**:
  `precio = piso(media de ML × 0.97)` — siempre un poco abajo de la media, nunca arriba de la media ni del máximo.
- Lo muestra el Estudio en «Sugerir precio» (`GET /api/precio-sugerido/catalogo/{sku}`) y la pestaña Catálogo.
- Si el SKU no tiene precio en el catálogo: «Sugerir precio» en modo **Completo**.
- **Revisar antes de mandar:** si la media de ML del catálogo es incongruente (MUN-0050-BEI: media $190 con mínimo y
  máximo de $692.99, y Amazon vende el mismo producto en ~$989), avisar a Brandon antes de publicar.

### 2. Categoría

1. Buscar la hoja correcta: `GET /api/crear/categorias-ml?q=<nombre corto>` y, si hace falta, el
   `domain_discovery` público de ML. Confirmar que es HOJA (`children_categories` vacío).
2. Guardarla en Woo: `POST /api/crear/categoria-ml {wc_id, category_id}`. La elección del panel manda sobre el
   predictor (regla de la casa 2).
3. Mostrar la ruta completa a Brandon antes de publicar: el 28-oct corrigió una (TEC-1450-PLA → Vinilos Decorativos) y
   eso obligó a eliminar y republicar.

### 3. Título, descripción y atributos

1. `POST /api/ia/mejorar` con `{canal: "mercado_libre", cuenta: "BEKURA", producto: {nombre, categoria (ruta),
   ml_cat_id, sku, wc_id, descripcion, precio, atributos}}`. La `descripcion` que se le pasa es una frase con lo que SÍ
   se sabe del producto (título del catálogo, renglón del packing list, foto).
2. **Limpiar lo que la IA inventa.** En cada tanda inventó datos: número de piezas, watts, canales, material,
   «carburo de tungsteno», ángulos, capacidad de carga, compatibilidades de marca. Se quitan del atributo y de la
   descripción; un atributo que no se sabe va vacío.
3. **Datos fiscales: NO se llenan ni se tocan** (v0.699.0 los excluye solos). **Garantía: la IA nunca la escribe**
   (v0.700.0).
4. Guardar en Woo: `POST /api/productos/{sku}/contenido {wc_id, titulo, descripcion, atributos}` con los atributos ya
   limpios.

### 4. Medidas y peso

- `GET /api/precio-sugerido/catalogo/{sku}` → las publicaciones de Amazon con `match` → `POST
  /api/precio-sugerido/medidas-amazon {sku, asins, q}` → `sugeridas` (largo, ancho, alto en cm; peso en kg).
- **Solo valen con `origen: "match"`.** Con `origen: "busqueda"` Amazon devuelve juegos y kits (la piedra de afilar
  salió en 1.83 kg, la broca en V en 0.47 kg): se estima a mano y se avisa.
- Las medidas de Woo NO son medidas: son el volumen reconstruido del CBM.

### 5. Fotos

- El publicador manda la **galería de Woo, en su orden** (`CamposPublicar` no recibe una lista de imágenes).
- En la tanda de la tarde se subió la foto del packing list a la galería de Woo de 5 SKUs (3 sin foto, y 2 donde
  quedó de principal), por pedido explícito de Brandon para esa tanda.
- **Regla vigente (Brandon, 29-oct):** la foto del packing list vive **solo en la pestaña Catálogo**; las fotos de Woo,
  de Odoo y del artifact no se mueven. Para publicar desde el Catálogo hay que decidir con él qué foto sale a ML cuando
  la de Woo es mala o no existe.
- Revisar la foto de Woo antes de publicar: la de Odoo de TEC-0234-PLA era OTRO producto (un kit de cables).

### 6. Vista previa

`POST /api/publicar/preview` con:

```json
{"canal": "mercado_libre", "cuenta": "BEKURA", "sku": "...", "wc_id": 0, "item_id": null,
 "campos": {"titulo": "...", "descripcion": "...", "highlights": null, "bullets": [], "atributos": [...],
            "precio_regular": 0, "peso": 0, "largo": 0, "ancho": 0, "alto": 0}}
```

Revisar en la respuesta: `ok`, `payload.price`, `payload.category_id`, las fotos (`payload.pictures`), que no haya
atributos fiscales y el veredicto de categoría (`categoria.veredicto`, `categoria.vectores.color`).

### 7. Publicar

`POST /api/publicar/confirmar` con el MISMO cuerpo más `categoria_confirmada` (la de la vista previa) y, si el
veredicto salió `distinta` o en rojo, `categoria_motivo` con el porqué. **Una sola llamada crea la publicación en
BEKURA y en SANCORFASHION, PAUSADA** (toda alta nueva del panel nace pausada). La respuesta trae, por cuenta, `item_id`,
`estado_ml` y la URL.

### 8. Verificar

- Lo que ML contestó al crear (201 + `paused`) es la primera prueba.
- Leer la publicación desde fuera YA pide sesión: el multiget público da 401 y la página del artículo manda a login.
  Leer los tokens de kubera para verificar pide permiso de Brandon (nunca renovarlos).

## Lo que falta para «Publicar desde el Catálogo»

1. Un botón «Publicar en ML» en el detalle de cada producto de la pestaña Catálogo que haga los pasos 1–7 con la
   persona revisando: precio propuesto, categoría con su ruta completa, título/descripción/atributos limpios, medidas
   con su origen, la foto que va a salir y la vista previa; y solo entonces confirmar.
2. Decidir con Brandon: qué foto sale a ML (paso 5) y quién activa las publicaciones (nacen pausadas).
3. Que el producto exista como borrador en Woo: los productos del catálogo SIN SKU, o que no están en Woo, primero
   pasan por **Crear**.
