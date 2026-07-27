# Plan de absorción de dailytrackMeli (xaxbkijc) — triage por tabla

> Decisión de Eduardo, 2026-07-27, tras auditoría tabla por tabla del proyecto
> de analítica. Alcance INICIAL: absorber a la BD kubera solo las tablas del
> tema FULFILLMENT. Estado: PLANEADO (ejecutar tras el candado de producción;
> idealmente post-corte o en paralelo sin tocar dominios en racha).

## El triage (dictamen por tabla, textual de Eduardo)

| Tabla | Dictamen | Acción derivada |
|---|---|---|
| `cron_runs` | **FULFILLMENT** | Absorber a kubera |
| `daily_sales` | **FULFILLMENT** | Absorber a kubera |
| `daily_stock` | **FULFILLMENT** | Absorber a kubera |
| `daily_visits` | **FULFILLMENT** | Absorber a kubera |
| `ml_accounts` | API tokens Mercado Libre | ⚠️ ver matiz 1 — NO absorber a la ligera |
| `products_snapshot` | Log diario de consulta por producto (cada atributo) | **OPTIMIZAR O DECIDIR** (pendiente) |
| `product_changes` | Log diario de cambios por producto | **OPTIMIZAR O DECIDIR** (pendiente) |
| `sales` / `sales_weekly_archive` / `sales_monthly_archive` | No se utiliza — el webhook ya lo hace con pedidos en Woo | Retirar (dump + baja) |
| `competitor_watchlist` / `competition_cache` | No se utiliza | Retirar (dump + baja) |
| `reporte_monitoreo_competencia_diario` / `_30day` | No se utiliza | Retirar (dump + baja) |
| `notifications` | No se utiliza | Retirar (dump + baja) |
| `goals` | Bórralo | Retirar (dump + baja) |
| `metrics_daily` | Bórralo (0 filas) | Retirar |

Con esto, de las 18 tablas: **4 se absorben** (~460k filas, decenas de MB — cabe
sin drama), **2 quedan en decisión** (las gordas: ~1 GB entre ambas por el
`raw jsonb`), **12 se retiran**.

## Matices detectados (leer antes de ejecutar)

1. **`ml_accounts` NO es solo un catálogo**: es el mapa `account_id (uuid) →
   nickname` que `products_snapshot` y las series diarias usan como FK, y hay
   que verificar si guarda TOKENS (si los guarda, entra al tema P3/Vault — los
   secretos no se copian entre proyectos a la ligera). Además `supabase_rest`
   (presencia ML del panel) la lee. Acción: al absorber las 4 series, absorber
   TAMBIÉN `ml_accounts` como catálogo (sin columnas de token si las tiene) —
   las FKs de las series la necesitan.
2. **Las tablas no se mueven sin su ESCRITOR**: las 4 series las llena el cron
   diario de MLREgisterDaily (snapshot 14:00 UTC, ~39 min). Absorber los datos
   sin re-apuntar el cron = tablas congeladas. Y OJO: con este triage,
   MLREgisterDaily pierde la mayoría de su razón de ser (ventas/competencia/
   goals muertas) — la pregunta de fondo es si su job de snapshot se re-apunta
   (rápido) o se ABSORBE como cron de OMNICANAL (alineado con retirarlo, como
   KuberaPipeline). Decisión pendiente de Eduardo/Brandon.
3. **Los retiros van DESPUÉS del re-apunte del escritor**: si se borra `goals`
   o `notifications` mientras la app MLREgisterDaily sigue viva, su UI truena.
   Orden: dump de TODO el proyecto → absorber las 4+1 → re-apuntar/absorber el
   cron → retirar las 12 → decidir las 2 gordas → dar de baja el proyecto
   `xaxbkijc` (libera las variables ANALYTICS_* y un slot de proyecto).
4. **`products_snapshot` sigue siendo lo que usa la presencia ML del panel**
   (`supabase_rest`): mientras su decisión esté pendiente, la presencia sigue
   leyendo de xaxbkijc → el split ANALYTICS_* del candado SIGUE siendo
   necesario hasta la baja final. No se pierde trabajo: al morir xaxbkijc,
   esas variables se borran.

## Opciones para las 2 pendientes ("OPTIMIZAR O DECIDIR")

- `products_snapshot`: (a) absorber solo un RESUMEN sin `raw jsonb` (mata ~80%
  del peso) + retención 90 días; (b) volverla event-driven (topic items del
  webhook refresca solo lo que cambió + barrido semanal de reconciliación);
  (c) retirarla y derivar la presencia del panel de `channel.listings` +
  `listing_history` (que ya registran precio/stock/status por día en kubera —
  candidata natural a sustituirla a mediano plazo).
- `product_changes`: `channel.listing_history` ya cumple ese rol en kubera
  (trigger por campo). Candidata a retiro directo tras verificar que no haya
  consumidor.

## Fases de ejecución (cuando se dé el GO)

1. **F-A**: dump completo de xaxbkijc (respaldo) + migración `0004_analytics_fulfillment.sql`
   en kubera (esquema `analytics`: las 4 series + `ml_accounts` catálogo) +
   copia de datos por lotes + acta de paridad (conteos/checksums).
2. **F-B**: decisión del escritor (re-apuntar MLREgisterDaily vs absorber el
   snapshot como cron OMNICANAL) + ejecutarla + 3 días de observación.
3. **F-C**: retiros de las 12 (dump ya hecho en F-A) + decisión de las 2 gordas.
4. **F-D**: baja del proyecto xaxbkijc + borrar variables `ANALYTICS_*` +
   actualizar CLAUDE.md/docs.
