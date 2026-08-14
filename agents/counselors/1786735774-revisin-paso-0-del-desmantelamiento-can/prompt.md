# Second Opinion Request

## Question
# Revisión: paso 0 del desmantelamiento — candados que viven en una bitácora

Eres un revisor independiente. Sé crítico y concreto. El planteamiento ya está
escrito y MEDIDO contra producción; lo que se busca es que lo rompan, no que lo
aprueben.

## Contexto

Proyecto OMNICANAL (panel omnicanal de Kubera): FastAPI + Next.js, MySQL legado
`u531713409_kubera_ml` migrándose a Supabase Postgres ("kubera"). La migración
está casi cerrada; queda el desmantelamiento. Este es el PASO 0.

## Documento a revisar

@docs/PASO_0_CANDADOS.md   ← el planteamiento completo, con números medidos

## Código involucrado

@backend/services/pedidos_ml.py       (candado 1: `_ya_compensado`, línea ~389;
                                       `_compensar_stock_protegido`, ~299;
                                       los dos usos, ~495-515)
@backend/services/stock_full.py       (candado 2: `_ya_procesada`, ~131;
                                       `_ACCIONES_APLICADAS`, ~128;
                                       la marca de agua por regex, ~365-380)
@backend/services/fanout_stock.py     (`_asegurar_schema` con
                                       CREATE TABLE IF NOT EXISTS, ~597)

Contexto adicional del proyecto y sus reglas: @CLAUDE.md
Plan general del desmantelamiento: @docs/PLAN_31_TABLAS.md

## Datos medidos hoy (14-ago-2026) contra producción

- `fanout_log`: 7,860 filas, 2.3 MB, viva (se escribió hace minutos).
- Estado escondido ahí dentro: 6 `full_compensado`, 17 movimientos aplicados
  (`full_ingreso`/`full_retiro`/`fba_ingreso`), 99 marcas de agua FBA parseadas
  del texto libre del campo `resultado` con la regex `→\s*(\d+)`.
- Verificado que compensar NO borra `_reduced_stock` en Woo (solo lo borra Woo
  al reponer en una cancelación), así que una segunda compensación devolvería
  las piezas otra vez. El candado es la única protección.
- La marca de agua NO equivale a `channel.listings.stock_fba`: 96 de 99 SKUs
  dan distinto, porque miden cosas distintas.
- `FULL_WATCH_ENABLED=true` en Railway, pero `full_watch_solo_registro` no está
  definida y su default es True: hoy simula, no mueve.

## Lo que quiero que revisen

1. **El split en tres casas** (columna en `channel.orders`, tabla de operaciones,
   tabla de marca de agua). ¿Es la forma correcta? ¿Hay una mejor? ¿Alguna de
   las tres está mal ubicada?

2. **Cambiar `except → return False` por PROPAGAR.** El candado 1 vive en el
   camino de un webhook de ML que llega EN RÁFAGA. Si el candado propaga y la
   base está caída, ¿qué le pasa al pedido? ¿Se pierde la venta? ¿Hay que
   distinguir entre "propagar" y "abortar solo la compensación"? Argumenten el
   caso contrario si lo ven.

3. **¿Se me escapó algún estado?** El barrido buscó `fanout_log` en todo el
   repo. ¿Hay otra decisión que dependa de esa tabla y que no esté en la lista
   de tres? Ojo con SQL dinámico y con lecturas indirectas.

4. **La fase de doble lectura.** Con solo 23 filas de estado, ¿vale la pena o
   hay una verificación mejor y más barata?

5. **El orden propuesto.** ¿Quitar el `CREATE TABLE IF NOT EXISTS` va al final
   como propongo, o antes? ¿Qué se rompe con cada orden?

6. **Contradigan lo medido si pueden.** Especialmente la afirmación de que la
   marca de agua NO se puede reemplazar por `channel.listings.stock_fba` — el
   plan original decía lo contrario y lo cambié por una medición.

## Instrucciones

- Lean los archivos referenciados; no se queden en el documento.
- Sean directos y opinionados; no hedgeen.
- Si algo del planteamiento está mal, díganlo con el archivo y la línea.
- Estructuren la respuesta con encabezados claros.

## Instructions
You are providing an independent second opinion. Be critical and thorough.
- Analyze the question in the context provided
- Identify risks, tradeoffs, and blind spots
- Suggest alternatives if you see better approaches
- Be direct and opinionated — don't hedge
- Structure your response with clear headings
- Keep your response focused and actionable
