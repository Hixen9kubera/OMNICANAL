# Barrido de lectores de `kubera_ml` — quién se rompe si se retira MySQL

> Hecho el 16-ago-2026. Es la tarea que el consejo marcó como **requisito antes
> de borrar cualquier tabla**: hasta hoy, mi barrido solo cubría
> `backend/services/` y `backend/routers/`, y cada "cero lectores" era una
> hipótesis, no un hecho.
>
> Método: SQL real (`FROM` / `JOIN` / `INTO` / `UPDATE` / `CREATE TABLE`), no
> menciones en prosa. 38 tablas buscadas en cuatro superficies.

## Resultado en una tabla

| Superficie | Tablas tocadas | Veredicto |
|---|---|---|
| `frontend/` | **0** | **limpio** — todo pasa por la API |
| `backend/scripts/` | 14 en 33 archivos | la mayoría es andamiaje de la propia migración; **5 scripts operativos no estaban en la lista** |
| `MonitoreoOperaciones` | **7** | ⚠️ el plan decía **1**. Es el dashboard entero |
| `MLREgisterDaily` | **1, y ESCRIBE** | 🛑 es el renovador de tokens de ML |
| `Aplicacion_Excel`, `personal` | 0 | limpio (los aciertos eran la palabra "productos" en prosa y `pymysql` dentro de `.venv`) |
| `publicador`, `KuberaPipelineV1.0` | — | se retiran con el robot de Alibaba (decisión del 23-jul) |

## 🛑 Lo más grave: los tokens de Mercado Libre

`MLREgisterDaily/backend/app/db/mysql_tokens.py` **lee y ESCRIBE `ml_tokens`**
en el MySQL que se va a retirar:

```python
SELECT cuenta AS nickname, access_token, refresh_token FROM ml_tokens
UPDATE ml_tokens SET access_token=%s, refresh_token=%s WHERE cuenta=%s
```

Es el único escritor de esa tabla, y la regla 8 de la casa dice que *"los tokens
los renueva un proceso externo irregular"* — **es éste**.

**Si el esquema se retira sin mover esto, los tokens de ML dejan de renovarse y
se cae toda la integración**: webhooks de ventas, publicación, sync de
inventario, competencia. No es un panel que se congela: es la arteria.

Ya estaba anotado como PASO 6 ("tokens a Vault, junto con apagar
MLREgisterDaily"), pero como una tarea de limpieza. **No lo es: es un bloqueador
del retiro del esquema**, y hay que subirlo de categoría.

## ⚠️ `MonitoreoOperaciones` lee SIETE tablas, no una

CLAUDE.md dice *"lee `productos` de MySQL"*. Medido en su código:

| Tabla | Dónde |
|---|---|
| `ml_backlog` | `/api/summary`, `/daily`, `/hourly`, `/errors`, `/products`, `/catalogo`, `/ready`, y el export a Excel |
| `amazon_progress` | `/api/amazon/summary`, `/errors`, `/products` |
| `amazon_backlog` | `/api/amazon/summary`, `/daily`, `/errors` |
| `scraping_alibaba` | `/api/pipeline/summary` |
| `atributos_ia` | `/api/pipeline/summary` |
| `costos_ml` | `/api/pipeline/summary` |
| `productos` | `/api/pipeline/productos` (con JOIN a las tres de arriba) |

`ml_backlog` es su columna vertebral: casi todos sus KPIs salen de ahí.

Eso cambia la decisión pendiente. No es *"repuntar `productos` o avisar que ese
panel se congela"*: es que **el panel entero deja de funcionar**. Y tres de las
tablas que consume (`scraping_alibaba`, `atributos_ia`, `costos_ml`) son del
robot de Alibaba, que está desconectado desde el 23-jul — o sea que esa sección
de su dashboard ya muestra historia congelada aunque MySQL siga vivo.

## `backend/scripts/` — separar andamiaje de operación

De los 33 archivos, la mayoría son **de la propia migración** y mueren con ella:
`comparar_*`, `probar_*`, `backfill_*`, `migrar_*`, `rescatar_*`,
`etl_channel_listings` (el espejo), `etl_core_products` (retirado con candado) y
`sembrar_sandbox` (obsoleto).

Lo que sí es **operación** y hay que repuntar o archivar:

| Script | Tablas | ¿Estaba en la lista de 8? |
|---|---|---|
| `alinear_ml_drop` · `alinear_amazon_drop` | `canal_inventario`, `fanout_log` | sí |
| `marcar_amazon_muertas` | `canal_inventario` | sí |
| `corregir_status_publicados` · `corregir_stock_woo_full` | `canal_inventario` | sí |
| `sincronizar_ml_huerfanas` | `canal_inventario`, `fanout_log` | sí |
| `publicar_walmart` | `canal_inventario`, `amazon_imagenes` | sí |
| `sync_odoo_woo_seguro` | `pedidos_ml` | sí |
| **`corregir_stock_amazon`** | `fanout_log` | **NO** |
| **`actualizar_comision`** | `costos_finales` | **NO** |
| **`backfill_dims_validados`** | `costos_finales` | **NO** |
| **`competencia_analisis`** | `categorias_ml`, `ml_progress`, `productos` | **NO** |
| **`reporte_sync_desde_ml`** | `ml_progress` | **NO** |

**Son 13, no 8.** Ninguno es un flujo vivo —se corren a mano— pero dejarán de
funcionar sin aviso el día del retiro.

## Lo que el barrido confirma del método

Las tres veces que este proyecto midió en vez de suponer, el número cambió:

- los lectores del grupo 4 eran **25 y no 19**,
- `ml_progress` estaba **viva**, no congelada,
- y ahora `MonitoreoOperaciones` lee **7 tablas y no 1**, y los scripts son
  **13 y no 8**.

Ningún conteo se movió a la baja. **La estimación de memoria siempre subestimó.**

## Qué queda por barrer

- **`publicador` y `KuberaPipelineV1.0`**: no se revisaron a fondo porque se
  retiran con el robot de Alibaba. Conviene confirmarlo antes del retiro, no
  asumirlo — es exactamente el tipo de suposición que este barrido desmintió
  tres veces.
- **`MCPPruebaWOO`**: sin revisar.
- **SQL dinámico**: el método busca nombres de tabla literales. Una consulta
  armada con f-strings a partir de una variable no aparece. En este repo el
  riesgo es bajo (los nombres van en el literal), pero no es cero.
