## Verificación contra el código

Antes de opinar, verifiqué las afirmaciones más fuertes del brief contra el repo real (no solo confié en el grep original). Resultado: casi todo lo que dice el brief es correcto y verificable — pero encontré **un candado que falta en el inventario** y **un problema de conteo** que nadie mencionó.

---

## 1. Hallazgo nuevo: `fanout_log` tiene un SEGUNDO candado, no solo `_ya_compensado`

El brief solo menciona `_ya_compensado` (pedidos_ml.py:389). Pero hay otro exactamente igual en `backend/services/stock_full.py:129-142`:

```python
def _ya_procesada(operacion_id: str) -> bool:
    ...
    r = db.fetch_one(
        f"""SELECT id FROM fanout_log
            WHERE item_id=%s AND accion IN ({ph}) ...""", ...)
    return bool(r)
except Exception as exc:
    log.warning(...)
    return False   # ← el mismo patrón exacto de los 964
```

Este candado protege **movimientos reales de stock FULL/FBA hacia Woo** (`INBOUND_RECEPTION` → resta bodega propia), no una compensación de cancelación. Si `fanout_log` desaparece, `_ya_procesada` decide "no se ha aplicado" → Woo se ajusta de nuevo → mismo patrón de los 964, pero en el camino de INGRESO de mercancía, no de cancelación.

**Matiz importante que sí encontré**: `FULL_WATCH_ENABLED=false` en producción hoy (`backend/config.py:360`, confirmado en README líneas 2304/2726). El candado está **dormido, no activo**. Eso es justo el problema: no es visible en ningún log de producción hoy, no aparece en los "8 sitios" que el brief cuenta para `fanout_log`, y el día que alguien encienda ese flag (todo el código defensivo de `stock_full.py` está construido para esa eventualidad — "nace APAGADO" implica que se prevé encenderlo) va a reproducir el bug de los 964 con inventario real, y nadie se va a acordar de revisarlo porque hoy no figura en ningún acta.

**Acción concreta**: antes de tocar `fanout_log`, documentar explícitamente los DOS candados (no uno) y decidir el destino de ambos juntos. Si la propuesta es moverlos a un flag de estado en `channel.orders` (ver punto 4), `_ya_procesada` necesita el mismo tratamiento, aunque hoy esté dormido.

---

## 2. El conteo no cierra: son 30, no 31

Sumé los grupos tal como están escritos: 11 + 5 + 2 + 4 + 3 + 3 + 2 = **30**. El título dice "31 tablas". Falta una.

No es pedantería — es la clase de error que el propio brief advierte contra: contar mal antes de congelar es exactamente cómo se cuela una tabla sin dueño. Antes de aprobar el orden, hay que sacar el `SHOW TABLES` literal de `u531713409_kubera_ml`, restar las 30 nombradas aquí más las ya archivadas por separado en F8 (`legacy_costos_ml`, `fx_rates`/`pricing_params`, `marketplace_identity`, `productos` semi-vivo por `stock_odoo`), y ver qué sobra. Candidata obvia a revisar: `canal_inventario` — no está en ninguno de los 7 grupos, pero **sí sigue siendo leído por decisión** en `stock_full.py`, `fanout_stock.py`, `inventario.py`, `presencia.py` (35 archivos la mencionan). Mi lectura es que está fuera de este brief a propósito porque es el espejo del dominio `channel` ya cubierto por el apagado del 13-ago (`CHANNEL_ESPEJO_INVERSO=false`, documentado en `APAGADO_ESPEJOS_MYSQL.md`) — pero eso debería decirse explícitamente en el brief, no asumirse. Si es otra tabla la que falta, peor: significa que el barrido de grep se comió una.

---

## 3. Pregunta 1 — Split vs. gemela 1:1

Coincido con la conclusión (split por intención, no gemela 1:1) pero por una razón adicional a "es más limpio": **una gemela 1:1 no resuelve el problema de los 63 item_id distintos, lo congela.** Si migras `ml_progress` completo a una tabla espejo en kubera, los 19 lectores van a seguir preguntándole a una fuente que ya sabes que está desactualizada en 63 casos — solo cambiaste el motor de base de datos, no la pregunta. El split obliga a resolver esos 63 caso por caso porque cada lector se muda a la fuente que SÍ tiene la respuesta correcta.

Lo que yo agregaría al método (no está en el brief ni en la otra opinión que vi en esta carpeta): no clasifiques los 19 lectores por lectura de código y listo — eso es exactamente el tipo de juicio manual que falló en el 12-ago. Antes de cortar cada sitio, ponlo en modo **doble lectura con log de discrepancia** (lee de `ml_progress` Y de `channel.listings`, compara, loguea si difieren, sigue usando la respuesta vieja) durante unos días. Es el mismo patrón de arnés de paridad que ya usaron en los otros 4 dominios — no hay razón para saltárselo aquí solo porque la medición inicial (0 casos en progress-no-en-listings) se ve limpia. Los 63 con item_id distinto ya demuestran que "se ve limpio" y "está limpio" no son lo mismo en este proyecto.

---

## 4. Pregunta 2 — Orden

Mi recomendación: **5 primero, pero no por "barato" — por ensayo de instrumentación.** Si van a construir el arnés de doble-lectura que propongo arriba (o cualquier variante), probarlo primero contra Grupo 5 (1 lector + 1 escritor, sin flujo de negocio vivo detrás) expone bugs de la herramienta misma sin apostar nada. Después Grupo 4, con el método Y la herramienta ya probados, no solo el método "fresco en la cabeza". Ir directo a Grupo 4 primero apuesta la tabla más cara del proyecto a que el procedimiento salga bien la primera vez que se ejecuta — eso es lo que falló el 11 y 12-ago (el propio CLAUDE.md dice que el retiro se intentó dos veces y se revirtió).

---

## 5. Pregunta 3 — Qué falta

Ya cubierto arriba (`_ya_procesada`, conteo 30 vs 31). Además:

- **`sincronizar_ml_huerfanas.py`** (verificado) documenta que `canal_inventario` para ML se llena **desde `ml_progress`** y por eso hay 794 publicaciones vivas en ML que el sistema nunca vio. Esto es evidencia directa a favor de la medición del brief (channel.listings SÍ es más completo, porque viene del universo de la API, no de la bitácora del publicador) — pero también dice que ese script (uno de los 8 "de mantenimiento" del F8) es OTRO lector de `ml_progress` que el barrido por `services/`+`routers/` no iba a capturar. Confirma que sí hace falta ampliar el grep a `backend/scripts/`, como sospechaba el brief en el punto 3.
- No hay tests que mockeen ninguna de estas tablas (`grep` de `ml_progress|fanout_log|stock_watch_foto` en `test_*.py` → 0 resultados). No es un blindspot real, se puede descartar.

---

## 6. Pregunta 4 — `fanout_log`

De acuerdo en que el candado no debería vivir en una bitácora de texto libre. Mi único desacuerdo con moverlo a una columna en `channel.orders`: **eso solo cubre `_ya_compensado`** (pedidos). `_ya_procesada` (stock_full) no tiene un "pedido" al que pegarse — su unidad es `operation_id` de un movimiento de bodega FULL/FBA, no una orden. Necesita su propia tabla de estado pequeña (`fanout_operaciones_aplicadas` o similar), no reutilizar `channel.orders`. Tratar ambos candados como el mismo problema con la misma solución sería repetir el error de origen: meter dos conceptos distintos en una sola tabla porque "ya existe y es cómoda".

---

## 7. Pregunta 6 — Riesgos de secuencia

Verificado en `channel_mirror.py:271-298`: `stock_watch_foto` es efectivamente la **única** fuente del canal `general` de `channel.listings` — el comentario del propio código lo confirma ("la ÚNICA verdad del stock propio desde el 17-jul"; el canal viejo "murió el 14-jul"). No hay fallback. Esto valida la preocupación del brief: `stock_watch_foto` no se puede archivar en el Grupo 2 tal cual — necesita migrarse a una fuente kubera-nativa (lectura directa de Woo, como hace `fanout_stock._stock_drop` hoy) **antes** de tocarla, o el fan-out de stock del DROP se queda ciego.

---

## Resumen de lo accionable

1. Sumar `_ya_procesada` (stock_full.py) al inventario de `fanout_log`, aunque esté dormido por `FULL_WATCH_ENABLED=false` — diseñar su destino junto con `_ya_compensado`, en tabla propia, no en `channel.orders`.
2. Reconciliar 30 vs 31 con un `SHOW TABLES` literal antes de aprobar el orden.
3. Antes de cortar cada uno de los 19 lectores de `ml_progress`, doble-lectura con log de discrepancia, no solo clasificación por lectura de código.
4. Orden: 5 (ensayo de herramienta) → 4 (método y herramienta ya probados) → resto.
5. `stock_watch_foto` sale del Grupo 2 "caché" y entra como migración obligatoria antes de tocarla — es fuente única sin fallback del canal `general`.
6. Ampliar el barrido a `backend/scripts/` — ya confirmé que `sincronizar_ml_huerfanas.py` lee `ml_progress`/`canal_inventario` para decidir.
