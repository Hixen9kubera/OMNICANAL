# Laboratorio de precios — diseño y contratos

> Rama `sandbox/precios-optimos` (worktree `C:\Users\diaz2\omnicanal-sandbox`). Nada de
> esta rama llega a producción por sí solo: Railway despliega `main`. El laboratorio se
> despliega como un servicio APARTE (`laboratorio-precios`) con su propia URL.

## 0. Reglas que no se negocian

1. **Solo lectura.** SELECT en kubera, `search_read` en Odoo, GET en las APIs. `candados.py`
   hace reventar cualquier escritura (incluida la renovación de tokens de ML). Nunca se
   desactivan los candados.
2. **No se crean ni modifican tablas de Supabase.** Todo resultado va a archivos en
   `LAB_DATOS_DIR` (ver `almacen.py`). En Railway, un volumen montado en `/data`.
3. **No se mueve ningún precio.** El laboratorio PROPONE. No existe en esta rama código que
   haga PUT/POST a un marketplace. Cada propuesta sale con `autorizacion: "pendiente"`.
4. **El token de ML jamás se renueva desde aquí** (`ml_http.get` relee una vez y aborta).
5. **Datos fuera de git**: `backend/sandbox_precios/datos/` está en `.git/info/exclude`
   (el repo es PÚBLICO). Nunca `git add -A` sin revisar.
6. En una corrutina, nada síncrono de red/disco (regla 11 de CLAUDE.md): `asyncio.to_thread`.
7. Distinguir SIEMPRE "medido en vivo" de "según caché". Un dato ausente es `null` + aviso,
   nunca 0 inventado.

## 1. Arranque

```python
import sys; sys.path.insert(0, r"C:\Users\diaz2\omnicanal-sandbox\backend")
from sandbox_precios import _entorno; _entorno.cargar()   # SIEMPRE antes de importar services.*
```

- Python local: `C:\Users\diaz2\OneDrive\Escritorio\omnicanal\backend\.venv\Scripts\python.exe`, cwd `backend/`.
- `ml_http.get(ruta, params, cuenta)` → `(status, json)`; freno global `LAB_ML_RPS` (4/s), pausa 11:50–12:30 UTC.
- `almacen.escribir_json / leer_json / crudo() / ultimo() / fotografiar_ultimo()`.
- `parametros.json`: todos los números de negocio (525,000, piso de margen, pasos, supuestos por canal).

## 2. Módulos y responsables

| Módulo | Qué hace | Produce |
|---|---|---|
| `extraer_kubera.py` | SELECTs a kubera (publicaciones de los 6 canales, productos, costos_validados, costos_finales, stock Odoo `ops.stock_watch_photo`, ventas item×día 150 d, líneas de pedido 150 d, competencia SERP+bestsellers, historial de precio limpio, envío real) | `crudo/<día>/kubera_*.json` |
| `extraer_ml.py` | API de ML: universo vivo (scan active+paused de ambas cuentas + multiget de 20), visitas diarias 150 d de las FULL, `/items/{id}/prices` de activas, `/seller-promotions/items/{id}` de activas FULL, `shipping_options/free?item_price=` de activas FULL, `listing_prices` por categoría×tramo (caché 7 d), `/suggestions/user/{uid}/items` + detalles, `price_to_win` de catálogo. Incremental: visitas con `last=3` si ya hay serie | `crudo/<día>/ml_*.jsonl`, `cache/comisiones.json` |
| `packing100.py` + `prorrateo.py` | Elige 100 SKUs publicados (activos en ML, preferencia FULL y más vendidos) cuyo SKU esté en Odoo CON `container_numbers`; si no, salta al siguiente. Ubica archivo+renglón (Ferraforme → sha256 → dHash ≤8 con margen 4 → IA título/foto como último peldaño, con tope), calcula el CBM TOTAL del contenedor y el costo por varios métodos | `ultimo/packing100.json`, `ultimo/contenedores.json`, `ultimo/packing_saltados.json` |
| `economia.py` | Funciones PURAS: comisión ML por categoría y tramo de precio (sobre precio CON IVA), envío FULL por peso facturable y tramo, IVA, utilidad y margen a un precio P; precio de equilibrio y piso; costo aterrizado por SKU con su FUENTE | — |
| `costos_lab.py` | Costo aterrizado por SKU para TODO el catálogo: exacto (packing100) > prorrateo_kubera (525k / m³ del contenedor reconstruido desde costos_validados) > tarifa 7,500 > sin costo | `ultimo/costos.json` |
| `elasticidad.py` | Panel item×semana (150 d): visitas, unidades, precio realizado; censura (días pausada/sin stock); estimación within-item de β_unidades y β_visitas con encogimiento empírico-bayesiano hacia categoría raíz → global | `ultimo/elasticidades.json` |
| `optimizador.py` | Rejilla de precios por publicación ML FULL; punto óptimo; plan de ajuste día/semana con topes; razones | `ultimo/precios.json`, `ultimo/curvas.json` |
| `canales.py` | Costo por publicación y precio de paridad en Amazon/Walmart/Temu/TikTok con parámetros `supuesto` | (dentro de publicaciones.json) |
| `construir.py` | Arma `publicaciones.json`, `historial.json`, `metricas.json`, `estado.json` | `ultimo/*.json` |
| `pipeline.py` | CLI y función `correr(etapas=None, sin_ml=False)`; bitácora en `estado.json`; al final `fotografiar_ultimo()` | — |
| `api.py` | FastAPI de SOLO lectura sobre `ultimo/` y `snapshots/`, llave compartida, sirve la web estática, corre el pipeline diario en un hilo | — |

Web: `laboratorio/web/` (Next.js 14, export estático, Tailwind 3.4.17, lucide-react; misma paleta del panel).

## 3. Prorrateo del contenedor (525,000 MXN)

Brandon: el costo del producto en el packing list NO aplica; lo que cuesta es el contenedor
(525,000 MXN, sin IVA según `lib/margen.ts:100`). Métodos que se calculan lado a lado:

| clave | fórmula por pieza | cuándo |
|---|---|---|
| `volumetrico_real` (**recomendado**) | `525000 × cbm_pieza / CBM_total_del_packing_list` | Siempre que haya CBM (medido: el Σ del `Indice` cuadra exacto con la columna de volumen del archivo) |
| `tarifa_fija_7500` | `cbm_pieza × 7500` | Referencia actual del panel (equivale a suponer 70 m³) |
| `peso_volumen_wm` | `525000 × max(cbm_pieza, kg_pieza/1000) / Σ max(cbm, t)` | Corrige productos densos (tonelada-flete marítima W/M) |
| `valor_fob` | `525000 × usd_pieza / Σ usd` | Solo informativo (40% de renglones sin precio USD) |
| `hibrido_70_30` | `0.7·volumétrico + 0.3·valor_fob` (si hay USD) | Propuesta si el 525k incluye aranceles ad valorem |

Invariante verificable: por contenedor, Σ(costo_pieza × piezas) = 525,000 ± redondeo en
`volumetrico_real` y `peso_volumen_wm`.

Cajas mixtas: `piezas_grupo` (regla vigente de Brandon: reparto igual por pieza).

## 4. Economía por unidad en ML FULL (a un precio P con IVA)

```
comision(P) = P × pct(categoria, tramo(P))           # ML cobra sobre el precio CON IVA (medido)
envio(P)    = calc_fee_envio_ml(peso_facturable, P)  # lo paga el vendedor también < $299 en FULL
iva(P)      = P − P/1.16
utilidad(P) = P − comision(P) − envio(P) − iva(P) − costo − costo_full_unitario
margen(P)   = utilidad(P) / P
```

- `pct` por categoría y tramo: `listing_prices?price=&listing_type_id=gold_pro&category_id=&logistic_type=fulfillment`
  a los precios muestra (199/399/599/1199) → tramos [0,299) [299,500) [500,1000) [1000,∞).
  Respaldo: mediana real de `order_items.comision/(precio×cantidad)` por categoría y tramo; luego
  `comision_respaldo_por_tramo` de parametros.json. Guardar la FUENTE.
- Peso facturable: `shipping_options/free.coverage.all_country.billable_weight` (g) cuando exista;
  si no, `costos._peso_efectivo(peso, l, a, h)` desde costos_validados; si no hay peso → `sin_peso`
  (no se recomienda precio, se avisa).
- Escalones a respetar: envío salta en $299; comisión baja en $500. La rejilla los incluye.

## 5. Modelo de demanda y punto óptimo

Base por publicación (últimos `dias_base`=28 días NO censurados): `P0` precio cobrado actual
(`price_sale` vivo), `U0` unidades/día, `V0` visitas/día, `CR0 = U0/V0`.

```
U(P) = U0 · (P/P0)^β        V(P) = V0 · (P/P0)^βv        CR(P) = U(P)/V(P)
Π(P) = U(P) · utilidad(P)
```

Estimación de β (unidades) y βv (visitas):
- Panel item×semana ISO, 150 días. Precio semanal = ingreso/unidades; semanas sin venta:
  precio arrastrado ±2 semanas o del historial limpio; si no, fuera de la regresión de precio.
- Censura: días con la publicación pausada / sin stock FULL se EXCLUYEN (no son demanda cero).
  Faltar en la serie de visitas de la API = 0 visitas (la API omite días en cero).
- Estacionalidad: dividir entre el índice semanal de visitas de la cuenta (`/users/{uid}/items_visits/time_window`).
- OLS within-item: `log(u+0.5) − media_i = β·(log p − media_i) + e`. Por item si ≥6 semanas válidas
  y CV de precio ≥ 5%; si no, hereda.
- Encogimiento: `β_i* = (β_i/se_i² + β_g/τ²)/(1/se_i² + 1/τ²)`, grupo = categoría raíz (≥5 items)
  → global → prior de parametros.json. Recorte a [elasticidad_min, elasticidad_max].
- Confianza: `alta` (item con se<0.5), `media` (categoría), `baja` (global/prior).

Precios que se reportan (rejilla de `rejilla_min_factor·P0` a `rejilla_max_factor·P0`, paso 1%,
más los escalones 298.99/299/499/500/999/1000, más terminaciones psicológicas en 9):
- `precio_equilibrio`: utilidad = 0.
- `precio_piso`: margen = `piso_margen`.
- `precio_max_utilidad`: argmax Π.
- `precio_max_volumen`: el menor P con margen ≥ piso (máximas unidades rentables).
- **`precio_recomendado` (punto óptimo)**: el MENOR P con Π(P) ≥ (1 − sacrificio)·Π_max y margen ≥ piso.
  Es "el precio con más visitas y ventas que casi no sacrifica utilidad". `sacrificio` =
  `sacrificio_utilidad_max` (0.10); sube a `sacrificio_liquidacion` si la cobertura de stock
  > `cobertura_dias_exceso`; baja a 0 si < `cobertura_dias_escasez`.
- Banda de competencia: si hay referencia confiable, el recomendado se acota a
  `banda_competencia × ref` (sin romper el piso). Referencia: mediana SERP filtrada (n≥5, ≤30 días)
  > sugerido de ML (`PRICE_DISCOUNT.suggested_discounted_price`) > mediana bestsellers de la hoja.
- **Plan de ajuste automático** (propuesta, no ejecución): pasos desde P0 hacia el recomendado con
  tope `paso_max_semana` (modo semanal) o `paso_max_dia` (modo diario), lista `[{fecha, precio}]`.
  Estado `autorizacion: "pendiente"`. Si β es de confianza baja, el plan incluye una PRUEBA
  (una semana a −5% y medir) antes de moverse más.
- Razones (chips): `sobre_competencia`, `bajo_competencia`, `escalon_envio_299`, `tramo_comision_500`,
  `stock_excesivo`, `stock_escaso`, `sin_costo`, `sin_peso`, `perdiendo_dinero`, `elasticidad_baja_confianza`,
  `promo_vigente` (precio actual viene de una promoción con fecha de fin), `pausada_sin_stock`.

## 6. Contratos de salida (`ultimo/`)

Todos los montos en MXN con IVA salvo `costo` (sin IVA). Fechas ISO. `null` = sin dato.

### `estado.json`
```json
{"generado_at":"…","version":"lab-0.1","etapas":{"extraer_kubera":{"ok":true,"filas":1234,"duracion_s":12.3,"avisos":[]}},
 "frescura":{"ml_listings":"…","amazon_listings":"…","walmart_listings":"…","competencia_serp":"…","competencia_best":"…","ventas":"…","visitas_api":"…"},
 "contadores_ml_api":{"get":0,"429":0}, "snapshots":["2026-09-28"]}
```

### `publicaciones.json` → `{"generado_at", "filas":[Publicacion]}`
```json
{"id":"mercado_libre:BEKURA:MLM123","canal":"mercado_libre","cuenta":"BEKURA","listing_id":"MLM123",
 "sku":"TEC-0393-ROS","titulo":"…","url":"…","thumbnail":"…","categoria_id":"MLM1234",
 "estado":"activa|pausada|en_revision|cerrada|inactiva|otra","situacion":"active","sub_status":["out_of_stock"],
 "logistica":"fulfillment|xd_drop_off|…","es_full":true,
 "precio_cobrado":99.0,"precio_lista":399.0,"promo":{"tipo":"custom","fin":"2026-10-02T05:59:59Z"},
 "stock_full":12,"stock_propio":0,"stock_odoo":340,
 "costo":{"unitario":18.4,"fuente":"packing_list_exacto|prorrateo_kubera|tarifa_7500|sin_costo","contenedor":"…","validado":true,"revisado_por":"Andrea","costo_panel":79.02},
 "comision_pct":0.195,"comision":19.31,"envio":34.0,"iva":13.66,"utilidad":13.63,"margen_pct":0.138,
 "visitas_30d":7200,"unidades_30d":1400,"conversion_30d":0.194,"ingreso_30d":138600.0,
 "competencia":{"n":8,"promedio":201.7,"mediana":193,"minimo":97,"maximo":296,"fuente":"serp|bestsellers|sugerido_ml","capturado_en":"…","sugerido_ml":164.0},
 "tags_calidad":["poor_quality_thumbnail"],"frescura_at":"…","avisos":["amazon_sin_cambios_desde_18_sep"],
 "supuesto_canal":false}
```
Otros canales: mismas llaves; `comision_pct` y `envio` de `parametros.canales` con `supuesto_canal: true`.

### `precios.json` → `{"generado_at","parametros":{…},"filas":[Recomendacion]}` (ML FULL activas + pausadas FULL)
```json
{"id":"mercado_libre:BEKURA:MLM123","sku":"…","cuenta":"BEKURA","listing_id":"MLM123","titulo":"…","estado":"activa",
 "precio_actual":99.0,"precio_recomendado":94.0,"cambio_pct":-0.0505,
 "precio_equilibrio":61.2,"precio_piso":72.9,"precio_max_utilidad":129.0,"precio_max_volumen":72.9,
 "precio_ref_competencia":193.0,"fuente_ref":"serp",
 "unidades_dia":{"actual":46.7,"recomendado":50.9},"visitas_dia":{"actual":240,"recomendado":247},
 "conversion":{"actual":0.194,"recomendado":0.206},
 "utilidad_dia":{"actual":636.5,"recomendado":655.0},"margen":{"actual":0.138,"recomendado":0.121},
 "elasticidad":{"beta":-1.9,"beta_visitas":-0.4,"beta_conversion":-1.5,"fuente":"item|categoria|global|prior","n_semanas":18,"confianza":"alta|media|baja"},
 "stock":{"full":12,"odoo":340,"cobertura_dias":0.3},
 "razones":["stock_escaso"],"plan":{"modo":"semanal","pasos":[{"fecha":"2026-09-29","precio":95.0}]},
 "autorizacion":"pendiente"}
```

### `curvas.json` → `{"<id>": {"puntos":[{"precio","visitas_dia","conversion","unidades_dia","utilidad_unit","utilidad_dia","margen_pct"}],"marcadores":{"actual","recomendado","piso","equilibrio","max_utilidad","ref_competencia"}}}`

### `historial.json` → `{"<id>": {"serie":[{"fecha","precio_realizado","unidades","visitas","precio_ofrecido","precio_recomendado"}]}}`
- 150 días reconstruidos (precio realizado de ventas, visitas de la API, precio ofrecido del
  historial limpio/`stock_hist`) + un punto por cada `snapshots/<día>` (precio cobrado y recomendado).

### `packing100.json` → `{"generado_at","contenedor_mxn":525000,"filas":[…]}`
```json
{"sku":"…","titulo":"…","cuentas":["BEKURA"],"listing_ids":["MLM…"],"precio_cobrado":99.0,"unidades_30d":120,
 "odoo":{"container_numbers":"PCIU9516451=CI^0PL contenedor 33","codigos":["PCIU9516451"]},
 "archivo":{"nombre":"…","file_id":"…","sha256":"…","contenedor":"PCIU9516451"},"fila":382,
 "empate":{"metodo":"ferraforme|sha256|dhash|ia_titulo|ia_foto","distancia":0,"confianza":"alta|media|baja"},
 "cajas":40,"piezas_fila":800,"piezas_grupo":65,"caja_mixta":true,"cbm_caja":0.1336,"cbm_pieza":0.00206,"peso_pieza_kg":0.53,"precio_usd":1.2,
 "costos":{"volumetrico_real":15.1,"tarifa_fija_7500":15.4,"peso_volumen_wm":15.0,"valor_fob":null,"hibrido_70_30":null},
 "kubera":{"costo_total":146.33,"costo_cbm":15.45,"costo_producto":130.9,"revisado_at":"…","revisado_por":"…","validado":true},
 "margen_con_525k":0.41,"margen_panel":0.02}
```
### `contenedores.json` → `{"filas":[{"codigo","archivo","sha256","total_cbm","total_piezas","total_peso_kg","total_usd","costo_m3","rango_ok","renglones","renglones_sin_cbm","cajas_mixtas","skus_en_lote"}]}`
### `packing_saltados.json` → `{"filas":[{"sku","motivo":"sin_sku_en_odoo|sin_container_numbers|sin_archivo|sin_renglon|ambiguo|padre_con_muchas_variantes"}]}`

### `metricas.json`
```json
{"por_cuenta":{"BEKURA":{"publicaciones":0,"activas":0,"activas_full":0,"pausadas_full":0,"pausadas_full_sin_stock":0,"pausadas_full_con_stock_odoo":0,
  "visitas_dia":0,"unidades_dia":0,"conversion":0,"ingreso_30d":0,"margen_mediano":0,"perdiendo_dinero":0,"con_costo":0,"con_competencia":0}},
 "por_canal":{"amazon":{"publicaciones":0,"activas":0,"frescura":"…"}},
 "embudo":{"visitas_30d":0,"unidades_30d":0},
 "elasticidad":{"global":-1.6,"por_categoria":[{"categoria","beta","n"}],"histograma":[…]},
 "palancas":[{"tipo":"reactivar_full","sku","cuenta","listing_id","ventas_perdidas_dia":0,"stock_odoo":0,"motivo":"…"}],
 "serie_diaria":[{"fecha","cuenta","unidades","ingreso","visitas"}]}
```

## 7. API (`api.py`, prefijo `/api/lab`)

`GET /estado` · `GET /publicaciones?canal&cuenta&estado&full&q&orden&page&per_page` · `GET /precios?cuenta&razon&q&orden&page`
· `GET /curva/{id}` · `GET /historial/{id}` · `GET /packing` · `GET /contenedores` · `GET /metricas`
· `GET /parametros` · `GET /exportar/precios.csv` · `POST /sesion` (llave) · `POST /recalcular` (llave, con candado de una corrida a la vez)

Auth: `LAB_ACCESS_KEY`. Sin llave definida: abierto SOLO fuera de Railway (modo local, banner);
en Railway sin llave → todo 503 (falla cerrado). Cookie httpOnly `lab_sesion` = HMAC de la llave.
