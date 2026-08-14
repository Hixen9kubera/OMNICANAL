---

## SECOND OPINION: PASO 0 — Los Candados en Bitácora

**Veredicto general:** El planteamiento identifica un problema REAL y crítico, pero tiene tres puntos ciegos que pueden sabotear la migración. Uno de ellos está medido mal. Les paso qué arreglar.

---

## 1. El Split en Tres Casas — CORRECTO, pero falta una guardia

**Recomendación:** Aprobar el split. La lógica es sólida: son **tres unidades de análisis distintas**, no tres instancias del mismo problema metidas en lugares diferentes.

| Ubicación | Unidad | Verificación |
|---|---|---|
| `channel.orders.compensado_en` (nueva columna) | PEDIDO WC | Llave natural: `wc_order_id` |
| `ops.fulfillment_operations` | OPERACIÓN DE BODEGA | Llave natural: `operacion_id` |
| `ops.fba_watermark` | SKU (watch photo) | Llave natural: `sku` |

**LO QUE FALTA:** El documento dice "la columna nueva viene con su índice". Pero **no especifica si es UNIQUE**. 

- Si es `UNIQUE (wc_order_id)`: perfecto, un solo candado por pedido.
- Si es solo índice: problema. Alguien podría insertar dos filas con el mismo `wc_order_id`, y el candado no vería el duplicado.

**Acción:** Requerir `UNIQUE (wc_order_id)` en el DDL, o documentar por qué el índice sin uniqueness es suficiente (ej: si la inserción está serializada).

---

## 2. Propagar en vez de `return False` — CORRECTO, pero con matiz de contexto

**Veredicto:** Sí, hay que propagar. El argumento es sólido. **PERO**: el "no romper la venta" que dice el código (línea 516 de pedidos_ml.py, `nunca rompe la venta`) se va a ROMPER si propagas.

**Análisis del flujo:**
- Línea 510-517: `_ya_compensado(wc_id)` es `False` → entra a compensar
- Si compensar lanza excepción y la propagas, ¿qué le pasa al webhook?

Mirando el código actual (línea 516): `except Exception: log.warning(...)`  es un "log and continue". El pedido ya fue creado en Woo (línea 482) **antes** de compensar. Así que el orden de operaciones es:

1. Crear/actualizar orden en Woo ✓
2. Intentar compensación
3. Si falla, log.warning y sigue ✓

Si propagas la excepción en paso 2:
- El handler de webhook ve una excepción
- El webhook se reporta fallido
- Railway/MQ reintenta
- En el reintento, idempotencia encuentra la orden ya creada (sigue)
- Vuelve a intentar compensación (propaga de nuevo)
- **Loop infinito hasta que kubera se despierte**

**Esto es MEJOR que `return False`**, pero diferente de "nunca rompe la venta". Es: "si kubera está caída, la venta se queda en pending de compensación, no se crea dos veces".

**Decisión correcta, comunicación equivocada.** Hay que cambiar el comentario a:

```python
except Exception as exc:  # noqa: BLE001
    # Si kubera está caída, la venta ya está en Woo. La compensación 
    # reintentará en la próxima ejecución. NUNCA confundir "no sé" con "no hay".
    log.warning("compensación FULL/FBA de %s falló: %s — reintentará", order_id, exc)
    raise  # propagar, no silenciar
```

**Riesgo:** Si hay código en el handler que asume "if sync() returns, todo está ok", eso rompe. Hay que revisar `routers/webhooks.py` (NO VISTO AÚN) y ver cómo maneja el retorno de `sincronizar`.

---

## 3. ¿Se Escapó Algún Estado? — SÍ, al menos DOS

**Hallazgo 1: `stock_full.revisar_fba()` también parsea la marca de agua.**

Líneas 370-379 de stock_full.py:

```python
for r in db.fetch_all("""SELECT f.sku, f.resultado FROM fanout_log f..."""):
    m = _re.search(r"→\s*(\d+)", str(r["resultado"] or ""))
    if m:
        previos[r["sku"]] = int(m.group(1))
```

Esto NO es un candado (no decide "procesar o no"), pero **sí depende de fanout_log para su lógica**. Si la tabla se borra y se recrea vacía:
- `previos` sale vacío (línea 370 da 0 resultados)
- El `setdefault` en línea 388 pondría stock_fba del sync como línea base
- **PRIMERA EJECUCIÓN TRAS BORRAR:** comparación contra cero → ve TODO como ingreso nuevo → **downgrades en Amazon que nunca pasaron**.

**Esto se ve atenuado por el fallback a `channel_read.stock_fba_amazon()` (línea 387)**, así que no es catastrófico. Pero es una cicatriz de fragilidad.

**Hallazgo 2: scripts de mantenimiento (`alinear_ml_drop.py`, etc.)**

El documento dice: "se corren a mano, no flujos vivos". Mirando las líneas de código, ninguna de esas llamadas está en el flujo de producción. Así que OK, no son decisiones en línea.

**Acción:** Documentar que tras `fanout_log` se borra, la primera ejecución de `revisar_fba()` puede ver falsos ingresos. Propuesta: mantener la marca de agua en su propia tabla (`ops.fba_watermark`), y cambiar el fallback en línea 387 a que **lea de la tabla nueva**, no de `channel_read`.

---

## 4. Doble Lectura — Cheap pero incompleta

**Veredicto:** Con 23 filas de estado, sí vale la pena. Pero el plan **no especifica cómo se detectan discrepancias**.

**Lo que falta:**
- ¿Se comparan uno por uno durante doble lectura, o al final con un batch?
- ¿Qué métrica se usa para marcar "discrepancia" (valor exacto, threshold)?
- ¿Dónde se anotan los desacuerdos? (en `espejo_kubera_log`? en logs? en una tabla nueva?)
- ¿Qué pasa si hay una discrepancia? (¿bloquea el encendido de la lectura?)

**Recomendación:** Añadir a la matriz de pruebas (T1-T6) **una verificación de paridad**:

```
T7 · Paridad después de doble lectura
     Después de 48 h en doble lectura:
     - Los 23 casos de estado: exacto match en ambas casas
     - Las 99 marcas de agua: exacto match en ambas casas
     - Cero discrepancias en el log
```

Si no cuadra, **bloquea** el paso 5.

---

## 5. El Orden — ACIERTO, pero el CREATE TABLE va ANTES de lo que dicen

**Análisis del orden actual:**
1. Migración ✓
2. Copiar datos ✓
3. Gémelas (lectura apagada) ✓
4. Doble lectura ✓
5. Encender lectura ✓
6. **Quitar CREATE TABLE, repuntar escritores** ← AQUÍ está el problema

**El problema:** Dicen "repuntar los 8 escritores". Mirando el código:

**Escritores a fanout_log que quedan vivos:**
1. `pedidos_ml._compensar_stock_protegido()` — línea 371
2. `stock_full.procesar_operacion()` — línea 326 (via `_registrar`)
3. `stock_full.revisar_fba()` — línea 441 (via `_registrar`)

**Lectores:**
1. `pedidos_ml._ya_compensado()` — línea 392
2. `stock_full._ya_procesada()` — línea 136

Cuando quiten el CREATE TABLE (fin del paso 6), **los LECTORES se rompen instantáneamente** si la tabla no existe. Pero los ESCRITORES pueden quedar.

**El orden correcto:**
- Paso 5a: Doble lectura ha validado que ambas casas están en sync
- Paso 5b: **Quitar el CREATE TABLE IF NOT EXISTS de fanout_stock.py:597** (ya los vivos se escriben en ops.*, no en fanout_log)
- Paso 5c: Repuntar los 3 escritores que queden en fanout_log (los de auditoría)
- Paso 6: Borrar fanout_log

Esto protege contra "tabla cae entre que quité el CREATE y que actualicé el lector".

**Acción:** Reordenar como arriba, o explicar por qué CREATE TABLE puede quedarse indefinidamente.

---

## 6. La Marca de Agua — Medición CORRECTA, pero el plan original era una trampa silenciosa

**Veredicto:** Están BIEN. Los 96 SKUs que divergen prueban que la marca de agua y stock_fba **son cosas distintas**.

- Marca de agua = "cuándo vio ESTE vigilante ESTE SKU la última vez"
- stock_fba = "foto del sync hace ≤15 min de TODOS los SKUs"

Usar stock_fba como watermark hacía que el MISMO ingreso se contara dos veces en ciclos seguidos (auditoría 27-jul).

**PERO:** No es suficiente poner la marca de agua en su propia columna y olvidar. Hay un **riesgo silencioso**:

Si watemark y stock_fba **divergen MUCHO** (ej: watermark=30, stock_fba=100 para el mismo SKU), ¿quién tiene razón?
- Watermark desactualizado (el watcher se cayó hace días)
- stock_fba equivocado (bug del sync)
- Stock REAL en Amazon es uno de esos dos, o un tercero

**El plan no aborda este caso.** Debería:
1. Mantener la marca de agua en `ops.fba_watermark.last_seen`
2. Agregar `ops.fba_watermark.stock_fba_at_last_seen` (snapshot del sync en el momento del último evento)
3. Si watermark y stock_fba actual divergen **>threshold**, emitir alerta (no fallar, solo avisar)

Esto permite auditores detectar que el vigilante está desfasado.

---

## RESUMEN DE ACCIONES ANTES DE PROCEDER

| # | Acción | Severidad | Bloqueador |
|---|---|---|---|
| 1 | Especificar `UNIQUE (wc_order_id)` en columna de compensación | Media | No |
| 2 | Revisar `routers/webhooks.py` para confirmar que propagar no rompe el handler | Alta | **SÍ** |
| 3 | Documentar que `revisar_fba()` debe usar marca de agua de `ops.fba_watermark`, no fanout_log | Media | No |
| 4 | Añadir T7 (prueba de paridad 48h) y bloquear T5 si divergencias | Media | No |
| 5 | Reordenar: quitar CREATE TABLE ANTES de borrar la tabla | Media | No |
| 6 | Agregar snapshot `stock_fba_at_last_seen` en watermark y lógica de alertas | Baja | No |

**Conclusión:** El planteamiento es SÓLIDO y está bien medido. Los riesgos identificados son mitigables con estos ajustes. **Adelante con sandbox, pero no mergear a main sin resolver el #2 (handlers) y el #5 (orden de pasos).**
