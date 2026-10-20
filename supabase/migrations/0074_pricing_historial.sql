-- 0074_pricing_historial.sql  (versión 3.3 · 19-oct-2026 · reemplaza a la v1 eb53689)
--
-- QUÉ HACE
--   Crea el esquema `pricing`: la memoria de largo plazo de los precios, las
--   promociones, la calidad/experiencia y la competencia de cada publicación,
--   pensada para una regresión lineal futura. Pedido de Eduardo (19-oct-2026):
--   «capturar en la base de datos la información necesaria a través de una
--   lectura a largo plazo para entender el comportamiento de cambio de precios,
--   promociones y otros factores […] que se apoye de la experiencia y calidad de
--   un producto […] para una regresión lineal a futuro». Y luego: «verifica que
--   la normalización sea lo más eficiente y seguridad posible».
--   SOLO el esquema: ningún proceso lo llena todavía.
--
-- POR QUÉ (medido en producción el 19-oct-2026) Y QUÉ CAMBIÓ CONTRA LA v1
--   · ERROR CORREGIDO. La v1 decía que `enrich.listing_health` guarda «solo la
--     última» calidad y experiencia y que «la historia se pisa cada día». Es
--     FALSO: `enrich.listing_health_hist` (0053) guarda una fila por
--     publicación, métrica y DÍA de México desde el 23-sep (33,260 filas, 1,032
--     publicaciones), con el estado de tres valores (medida | no_calculada |
--     sin_datos) que la copia de la v1 perdía. Esta versión la LEE; no la copia.
--     Si pricing sigue publicaciones que calidad_ml.py no mide, se amplía ese
--     escritor (el único); no se crea un segundo.
--   · SE GUARDA aquí solo lo que no se puede reconstruir después:
--       - qué ofertas tuvo cada publicación cada día y cuál cobró (los avisos de
--         ML se purgan a los 3 días; en 56 de 252 con descuento del 15-oct
--         cobraba una campaña y no el descuento);
--       - el precio leído cada día y cada cambio con su hora, de dónde vino y
--         quién (channel.listing_history es por SKU, «aletea» entre publicaciones
--         que comparten SKU y no dice qué lo movió);
--       - las visitas diarias (ML solo da 150 días atrás), las preguntas y los
--         anuncios;
--       - el stock, el buybox y la disponibilidad del día;
--       - la ficha (título, fotos, categoría, logística, catálogo) por periodos;
--       - los precios de los rivales (enrich.market_search_results se pisa) y
--         con qué rivales se comparó cada publicación;
--       - las decisiones de pricing y las corridas del modelo;
--       - si cada orden de ML fue reventa (`meli_resale`), que channel.orders
--         no guarda (pricing.orden_etiqueta).
--   · SE DERIVA con vistas lo que ya vive en otro lado (una sola fuente de
--     verdad): unidades, ingreso, comisión y cancelaciones (channel.orders +
--     order_items, que se corrigen solos: la comisión pasa de 0 a su valor real
--     y una cancelación tardía sale sola); devoluciones (channel.returns_daily);
--     calidad y experiencia (enrich.listing_health_hist); posición en el top de
--     la categoría (enrich.market_highlights_hist) y el costo que regía cada día
--     (costing.cost_history + costing.costos_validados).
--   · v3 (19-oct, tras la revisión con verificación y dos críticos): la reventa
--     se guarda (orden_etiqueta) y la conversión ya no sale sesgada; el envío por
--     orden se deriva de enrich.order_shipping_cost; las horas con stock pasan a
--     resultado_dia (se cierran al terminar el día); la foto se puede COMPLETAR
--     (NULL → valor) sin reescribir lo medido; los periodos no se enciman; la
--     llave de una publicación es inmutable; cada fila diaria queda amarrada a
--     una captura de su mismo día y grupo; la competencia toma la fecha de
--     medición correcta y no cuenta rivales de nuestra otra cuenta; el precio
--     del día también va ponderado por horas.
--   · v3.1 (crítica final, 27 problemas confirmados): los rivales de nuestra otra
--     cuenta se excluyen por item_id (core.accounts.external_id está vacío en
--     ML); los periodos aceptan upserts (ON CONFLICT) y no se enciman ni
--     cambiando la llave; el envío se reparte por ENVÍO (un carrito de varias
--     órdenes comparte uno); las horas con stock van en su propia tabla
--     (disponibilidad_dia); un día con lector de órdenes incompleto ya no deja
--     ceros sesgados; precio_obs y la ficha solo aceptan atribución; la captura
--     y las campañas no se mueven después; el filtro de secretos reconoce más
--     llaves; la competencia no usa precios de rivales de más de 7 días.
--   · v3.2 (pedido de Eduardo, 19-oct): «guardar quién hizo qué cambio en
--     pricing, los accionables que hacen, para tener esa memoria y después un
--     entendimiento de la toma de acciones». Nace la BITÁCORA DE ACCIONES:
--     pricing.accion (quién, cuándo, desde dónde, qué, por qué —motivo de un
--     catálogo y texto—, con qué regla o recomendación, ligada a una decisión, y
--     cómo terminó), pricing.accion_publicacion (por publicación: cómo estaba,
--     qué se pidió, cómo quedó y QUÉ VEÍA la persona) y pricing.motivo (catálogo
--     que crece sin migración). También las acciones SIN cambio («revisada sin
--     cambio», «nota»): saber qué se decidió NO hacer es parte de la memoria. El
--     «quién y por qué» vive SOLO aquí: precio_obs, oferta, listing_ficha y
--     evento ya no llevan autor ni decisión, apuntan a la acción (accion_id).
--     Vistas para entender las decisiones: accion_v, accion_efecto_v (14 días
--     antes contra 14 después de cada acción) y actor_resumen_v.
--   · v3.3 (crítica de la bitácora, 37 ajustes confirmados): el tipo de actor
--     sale del actor (Claude y los crons ya no quedan como «persona») y el
--     origen de la sesión (app.origen: panel, script, claude, cron); quién
--     actuó y quién lo ANOTÓ se guardan aparte (un reclamo de Seller Center no
--     borra a ninguno); la atribución solo se llena, no se reescribe; el motivo,
--     el error y la hora de término no cambian después de cerrar; cada
--     publicación guarda la recomendación de la pantalla (clave y precio), lo
--     pedido completo (hasta, salir de campañas, campaña, categoría…) y la foto
--     de ANTES que necesita deshacer; se registra quién terminó una oferta; una
--     observación solo se atribuye a una acción que tocó esa publicación; la
--     acción y sus publicaciones no se contradicen (revisión al COMMIT); la
--     decisión aplicada se llena sola desde la acción; accion_efecto_v no
--     compara contra ventanas que no han terminado, separa la reventa, revisa
--     la cobertura del lector de órdenes y marca otros cambios y días sin stock.
--   · La v1 tenía UNA fila ancha por publicación y día (~70 columnas y un jsonb
--     de fuentes por fila: ~0.8 GB/año con listing_promo_dia). Mezclaba datos
--     con granos y ritmos distintos, guardaba «qué cobró» dos veces y repetía un
--     `sku` que no depende de la publicación (variaciones). Ésta pone cada hecho
--     en su grano (~0.3 GB/año, ver TAMAÑO).
--
-- LAS TABLAS (todas cuelgan de `pricing.publicacion`, salvo las de cuenta,
-- calendario, rivales y modelos)
--   Dimensiones y relaciones
--    1. publicacion       una publicación de una cuenta (account_id + listing_id), id integer,
--                         con su fecha de alta.
--    2. publicacion_sku   qué SKUs tuvo cada publicación y desde cuándo (variaciones).
--    3. listing_ficha     la ficha por periodos: título, fotos, categoría, logística, catálogo.
--    4. rival             cada publicación ajena (o de la otra cuenta) con la que se compara.
--    5. listing_rival     con qué rivales se comparó cada publicación y con qué veredicto.
--    6. seguimiento       qué se sigue a mano o como control, por periodos.
--   La foto diaria (de SOLO AGREGAR: una captura por día)
--    7. captura           cada corrida del escritor: día, grupo, fuentes y versión.
--    8. listing_dia       la foto del día: estado, precio leído, qué oferta cobró, stock, buybox.
--    9. oferta_dia        cada oferta vista ese día, su precio y quién la financia.
--   10. cuenta_dia        la reputación de la cuenta por día.
--   Series que se escriben cuando cambian o que llegan tarde
--   11. precio_obs        cada cambio del precio propio, con su hora, fuente y quién.
--   12. promocion         las campañas del canal que tienen id (C-MLM…, P-MLM…).
--   13. oferta            la participación de UNA publicación en una promoción o su
--                         descuento propio, con las fechas de ESA publicación.
--   14. rival_precio      el precio de cada rival por tramos (solo cuando cambia).
--   15. resultado_dia     visitas y preguntas por día (se reescriben hasta que cierran).
--   16. ads_dia           Product Ads por publicación, día y campaña (solo si hubo).
--   Decisiones, calendario y modelos
--   17. decision          las decisiones de pricing («Qué cambió»).
--   18. decision_listing  a qué publicaciones aplicó cada decisión y cuándo.
--   19. evento            lo que ninguna otra tabla registra (descripción, otro), con quién.
--   20. calendario        eventos que mueven la demanda, con rango de fechas.
--   21. parametro_canal   reglas del canal con vigencia (umbral de envío gratis).
--   22. modelo_corrida    cada regresión: especificación, muestra exacta (hash y ruta).
--   23. modelo_coef       un renglón por variable y corrida (coeficiente, error, p).
--   24. orden_etiqueta    si cada orden de ML fue reventa (meli_resale), leída de la API.
--   25. disponibilidad_dia horas del día con stock vendible (se calcula al cerrar el día).
--   Bitácora de acciones (quién hizo qué y por qué)
--   26. motivo            catálogo de motivos de una acción (crece sin migración).
--   27. accion            cada acción de una persona o proceso: quién, cuándo, desde dónde,
--                         qué, por qué, con qué regla, qué decisión y cómo terminó.
--   28. accion_publicacion por publicación: antes, lo pedido, después y lo que se veía.
--   VISTAS (todas `security_invoker = on`)
--   venta_linea_v        cada línea de pedido con su día, cuenta, reventa y cancelación.
--   ventas_dia_v         ventas por publicación y día (normales, reventa, canceladas).
--   precio_dia_v         cuántas veces cambió el precio base y el cobrado cada día.
--   costo_vigencia_v     el costo de cada SKU por intervalos de vigencia.
--   competencia_dia_v    mínimo, mediana y referencia de los rivales comparables.
--   experiencia_razon_v  la razón de la experiencia cada vez que cambió.
--   decision_v           la decisión con su fecha de aplicación (calculada).
--   decision_dia_v       cada día de la ventana de una decisión: antes, aplicación, después.
--   cambio_v             la línea de tiempo de «Qué cambió» (precio, ofertas, ficha, eventos).
--   seguimiento_hoy_v    la lista de publicaciones que el escritor debe leer hoy.
--   panel_v              EL PANEL para la regresión, publicación × día, en float8.
--   panel_semana_v       el mismo panel por semana ISO (el diario es casi todo ceros).
--   accion_v             cada acción con su motivo, su decisión y cuántas publicaciones salieron bien.
--   accion_efecto_v      lo que pasó 14 días antes contra 14 después de cada acción, por publicación.
--   actor_resumen_v      por persona y tipo de acción: cuántas, cuántas salieron bien o se deshicieron.
--
-- REGLAS DE LOS DATOS
--   · NULL = «no se midió». Un 0 es un 0 medido. Nunca se escribe 0 por no saber.
--   · El día es el de México (America/Mexico_City). Los periodos con `date`
--     (desde/hasta) son INCLUSIVOS y hasta NULL = sigue; los de `timestamptz`
--     (listing_ficha) cierran cuando se vio la versión siguiente.
--   · QUIÉN Y POR QUÉ viven SOLO en pricing.accion. Las tablas que observan
--     cambios (precio_obs, oferta, listing_ficha, evento) dicen qué cambió, cuándo
--     y su origen; si lo hizo alguien, accion_id apunta a la acción (origen
--     'panel' la exige). Un cambio visto desde fuera (Seller Center) se puede
--     reclamar después: una acción con origen_app 'seller_center' (actor = quien
--     lo hizo, registrada_por = quien lo anota) y el accion_id en la observación
--     (precio_obs, oferta, listing_ficha; un evento no se reclama: queda como
--     línea accion:… en cambio_v). La atribución solo se llena: no se cambia ni
--     se borra.
--   · Una publicación = (cuenta, listing_id) en `pricing.publicacion`. El CANAL
--     NO se repite: sale de core.accounts.channel_id (depende de la cuenta; la v1
--     lo llevaba en cada fila sin nada que obligara a que coincidiera).
--   · Publicación ↔ SKU es muchos a muchos con vigencia (publicacion_sku). El
--     precio, las promociones, las visitas y el buybox son de la PUBLICACIÓN; el
--     costo y el stock propio son del SKU.
--   · Montos en MXN por pieza con numeric, igual que channel.* y costing.* (sin
--     centavos: mezclar unidades en los joins es peor). Las vistas del panel los
--     entregan en float8 para pandas/statsmodels.
--   · La reventa (`meli_resale`) es atributo de la ORDEN. channel.orders no la
--     guarda, así que pricing.orden_etiqueta la guarda 1:1 por orden (sin llave
--     foránea a channel.orders: no se amarra una tabla caliente). En ML, una
--     orden sin etiqueta leída = NO SE SABE: ese día unidades_normal,
--     unidades_reventa y la conversión salen NULL, y si la publicación no vendió
--     también (para que un dropna() no deje solo los ceros). Fuera de ML,
--     es_reventa = false. Cuando channel guarde la etiqueta, solo cambia
--     venta_linea_v.
--   · La foto (listing_dia, oferta_dia, cuenta_dia) es de SOLO AGREGAR Y
--     COMPLETAR: una captura por día a hora fija; lo que falló queda NULL y una
--     segunda pasada del MISMO día solo puede llenar esos NULL
--     (pricing.tg_solo_completar). Un valor medido no se reescribe nunca.
--     Las bitácoras (evento, modelo_corrida, modelo_coef) también son de solo
--     agregar. Las demás tablas aceptan UPDATE (cerrar un periodo, atribuir un
--     cambio) pero NO se borran: los candados (triggers) frenan también al dueño.
--   · Desnormalizaciones elegidas, a propósito:
--       - listing_dia.precio_base/precio_cobrado son la LECTURA de la captura;
--         precio_obs guarda los CAMBIOS con hora y quién. Coinciden por
--         construcción: la captura escribe en precio_obs solo si su lectura
--         difiere de la última. Para «cuándo y quién» manda precio_obs.
--       - disponibilidad_dia.horas_disponible la calcula el escritor desde
--         channel.listing_history (cada 15 min, por SKU) DESPUÉS de cerrar el
--         día: reconstruirla por publicación necesita reglas del escritor
--         (variaciones, aleteo). Va en su propia tabla: otra fuente, otro
--         momento y otro escritor que las visitas.
--       - listing_rival.clase es una FOTO del veredicto del juez
--         (enrich.market_rival_juicio lo pisa): es historia, no copia.
--       - captura.fuentes guarda de dónde salió cada grupo UNA vez por corrida,
--         no por fila (la v1 lo repetía en cada fila: ~70 % del peso).
--
-- DE DÓNDE SALE CADA COSA (el escritor no existe todavía)
--   listing_dia (estado, precio, stock, envío gratis, vendidos) . ML /items (multiget)
--     precio_cobrado y qué oferta cobra ......................... ML /items/{id}/sale_price
--     buybox_estado, buybox_precio_ganar, visit_share ............ ML /items/{id}/price_to_win
--   disponibilidad_dia ........................................... channel.listing_history (stock_full, stock_own, status)
--   orden_etiqueta ............................................... ML /orders/{id} (static_tags, tags), solo ids y etiquetas
--   oferta, oferta_dia, promocion ................................ ML /seller-promotions/items/{id} (fechas por ítem)
--   precio_obs ................................................... la captura, el monitor de precios y el panel
--   listing_ficha, publicacion_sku ............................... ML /items (title, pictures, attributes,
--                                                                  catalog_product_id, variations, logistic_type)
--   resultado_dia.visitas ........................................ ML /items/{id}/visits/time_window
--   resultado_dia.preguntas ...................................... ML /questions/search
--   ads_dia ...................................................... ML Product Ads (por ítem y día)
--   rival, rival_precio, listing_rival ........................... revisión de rivales de PRICING, catálogo
--                                                                  (/products/{id}/items) y el juez (0063)
--   cuenta_dia ................................................... ML /users/{id} (seller_reputation)
--   accion, accion_publicacion ................................... el editor de PRICING (services/pricing_precio.py:
--                                                                  aplicar/deshacer, v0.665.0) y cualquier pantalla que
--                                                                  actúe; relleno de lo hecho desde el 19-oct con
--                                                                  ops.process_log (proceso 'precio', origen 'pricing')
--   ventas, ingreso, comisión, cancelaciones (vista) ............. channel.orders + channel.order_items
--   envío que pagamos (vista) .................................... enrich.order_shipping_cost (por ENVÍO; parcial: NULL = no medido)
--   devoluciones (vista) ......................................... channel.returns_daily (por día en que se abrió)
--   calidad y experiencia (vista) ................................ enrich.listing_health_hist
--   posición en el top de la categoría (vista) ................... enrich.market_highlights_hist
--   costo que regía cada día (vista) ............................. costing.cost_history + costos_validados.costo_cbm
--
-- TAMAÑO ESTIMADO (~1,000 publicaciones seguidas × 365 días)
--   listing_dia ~365 mil filas/año de ~120 B (~55 MB con índices) · oferta_dia
--   ~1.1 millones de ~68 B (~100 MB) · resultado_dia ~35 MB · rival_precio ~0.5
--   millones solo cuando cambia (~50 MB) · lo demás <30 MB. Total ~0.25-0.3
--   GB/año (la v1: ~0.8 GB/año). Con 1.5 millones de publicación-días, ×4. Sin
--   particionar: a ese volumen no hace falta (las PK ya incluyen la fecha).
--   Las llaves foráneas no llevan índice del lado que referencia salvo que
--   sirva a una consulta: los padres no se borran (candados).
--
-- LO QUE NO ES
--   No es fuente de verdad de pedidos, stock ni precios vigentes: siguen en
--   channel.* y en ML. No copia ventas, devoluciones, calidad ni costos: los
--   lee. Las vistas dependen de channel.orders, channel.order_items,
--   channel.returns_daily, channel.listings, channel.listing_history,
--   enrich.listing_health_hist, enrich.market_highlights_hist, costing.* y
--   core.accounts: un cambio de forma en ésas tiene que revisar estas vistas.
--   enrich.listing_health_hist NO debe recibir retención (pg_cron) mientras
--   pricing dependa de ella: anotarlo también en la 0053.
--   SE TRASLAPA con la propuesta 0062 (`market.*`, solo en el sandbox, sin la
--   firma de Eduardo): visitas propias (market.item_mes), precios por item
--   (market.item_hist) y calendario (market.evento). Esta migración NO toca
--   market.*. Antes de que exista un escritor hay que decidir UNA casa para
--   cada hecho; propuesta: el precio propio en pricing.precio_obs (con hora y
--   atribución) e item_hist solo para rivales.
--
-- SEGURIDAD (blindaje explícito por objeto, donde nace, sin bloques DO para
-- que backend/scripts/verificar_rls.py lo vea)
--   · RLS encendida y CERO políticas en las 28 tablas: solo pasan service_role y
--     postgres (bypass). anon y authenticated: nada, ni USAGE del esquema.
--   · Mínimo privilegio a service_role: SELECT + INSERT en las de solo agregar;
--     SELECT + INSERT + UPDATE en las demás; DELETE solo en decision_listing
--     (borradores) y calendario (captura a mano). Nunca TRUNCATE, REFERENCES ni
--     TRIGGER. Sin privilegios por omisión en el esquema: una tabla futura nace
--     cerrada.
--   · Candados por trigger que frenan también al dueño (`postgres`, con el que
--     entra el backend): ops.tg_solo_agregar y ops.tg_sin_truncate (0064) y
--     pricing.tg_sin_borrado. Contrato de errores de la casa: SQLSTATE 42501
--     con constraint <tabla>_solo_agregar | _sin_borrado | _sin_truncate.
--     Ninguna llave foránea borra en cascada.
--   · Las vistas con `security_invoker = on` (la forma que reconoce el modo
--     --vivo). Un lector futuro (p. ej. un rol pricing_lector para la
--     regresión) necesita SELECT y una política en CADA tabla base que lean las
--     vistas: si falta una, la vista devuelve columnas vacías sin error. Nunca
--     el DSN de postgres ni BYPASSRLS. El esquema NO se expone en Settings › API.
--   · Columnas libres (jsonb y texto) con forma, tope y un filtro de tokens
--     (pricing.sin_secretos: ML, Bearer, Apify, JWT, Amazon, Woo, sk-, Fernet,
--     llaves privadas, URL con contraseña, Slack, Google/Gemini, Supabase,
--     GitHub y Replicate). Los datos
--     personales de compradores se tapan en el escritor; nunca la respuesta
--     cruda de /orders.
--   · `actor` (accion) y `autor` (decision, seguimiento, modelo_corrida) toman la
--     firma de la sesión (app.usuario, 0029). Es el correo de quien actuó: dato
--     de empleados, no de compradores; nada de clientes entra a la bitácora.
--     Un cambio con origen 'panel' no puede quedar sin acción.
--   · OJO: el backend entra como `postgres` (dueño, BYPASSRLS): los GRANT a
--     service_role no lo limitan. Lo que SÍ lo frena son los triggers (no borrar,
--     solo completar, solo atribuir, llaves inmutables, periodos sin encimar,
--     captura coherente con su día, grupo y cuenta). Por eso las reglas de integridad viven en triggers y no solo
--     en privilegios.
--   · La migración se autoverifica antes del COMMIT (RLS, privilegios de anon y
--     authenticated, security_invoker, candado contra TRUNCATE, ACL por omisión).
--
-- DEPENDE de core.accounts y core.channels (0001), la extensión citext,
-- ops.tg_solo_agregar() y ops.tg_sin_truncate() (0064), ops.migraciones (0064),
-- enrich.listing_health_hist (0053), enrich.market_highlights_hist (0043/0044),
-- channel.returns_daily (0049), channel.sales_daily/orders/order_items (0005,
-- 0030), channel.listings y channel.listing_history (0001), costing.* (0001) y
-- enrich.order_shipping_cost (0020). FUERA DE pricing crea UN objeto: el índice
-- enrich.order_shipping_cost (cuenta, shipment_id), que necesita el reparto del
-- envío por carrito (tabla de ~33 mil filas: el candado SHARE dura un instante).
-- Truena con KB000 si falta algo. Al crearla toma SHARE ROW EXCLUSIVE sobre
-- core.accounts y core.channels hasta el COMMIT (no bloquea lecturas, solo
-- escrituras a esas dos tablas: hoy solo los scripts cargar_* a mano).
--
-- NUMERACIÓN: 0072 está en producción y 0073 solo en el sandbox (ninguna en
-- main); 0062 está reservada para market. Si se unifica con market.*, la
-- dependencia va en la migración de número mayor.
--
-- QUIÉN LA CORRE: en el sandbox, `aplicar_migraciones.py --plan` y luego
-- `--hasta 0074` (el archivo se registra solo en ops.migraciones); en
-- producción, el acta, con rol postgres. EN EL MISMO COMMIT (hecho): 'pricing'
-- en ESQUEMAS_NEGOCIO (verificar_rls.py), ESQUEMAS_PROPIOS
-- (aplicar_migraciones.py) y ESQUEMAS (routers/flujo.py); la prueba del
-- aplicador incluye la 0074; y clonar_a_sandbox.py apaga los triggers de
-- pricing mientras corre su `truncate core.* cascade` (sin eso el candado
-- sin_truncate tumba el clon).
-- PARA ENSAYAR A MANO: este archivo trae su propio begin/commit. Con psql
-- CONFIRMA. Para un ensayo con ROLLBACK, quitar esas dos líneas o usar el
-- aplicador (`--plan` no ejecuta nada).
--
-- IDEMPOTENTE para el MISMO texto: `if not exists` en tablas e índices,
-- `create or replace` en funciones, vistas y triggers, y el registro con `where
-- not exists`. El aplicador salta por nombre y no compara el sha256: un cambio
-- de forma después de aplicada va en OTRA migración (con ALTER), nunca editando
-- ésta. Si el esquema existe sin su fila en ops.migraciones, truena.
--
-- REVERSA: solo mientras esté VACÍA, sin cascade y con tope de candado (al pie
-- del archivo). Toma ACCESS EXCLUSIVE breve sobre core.accounts y core.channels
-- (borra los triggers de las llaves foráneas): fuera de hora pico.
--
-- Estado: APLICADA el 20-oct-2026 a las 17:20 UTC en el SANDBOX y en PRODUCCIÓN
-- (v0.671.0), 1.6 s en cada base. Se aplicó este mismo texto salvo este párrafo
-- (sha256 del archivo aplicado: 22f69eee43a2abbcf839c02e911e1368ead2fba0a3559b0a32347dd6c51e55b3).
-- Antes se ensayó en el sandbox dentro de una transacción con ROLLBACK (151 pruebas).

begin;

set local lock_timeout = '5s';
set local statement_timeout = '60s';

-- ───────────────────────────────────────────────────────────────────────────
-- 0) Antes de tocar nada: lo que esta migración usa tiene que existir
-- ───────────────────────────────────────────────────────────────────────────
do $previo$
declare
  falta text[] := '{}';
  r     text;
begin
  foreach r in array array['core.accounts', 'core.channels', 'channel.orders', 'channel.order_items',
                           'channel.returns_daily', 'channel.listings', 'channel.listing_history',
                           'enrich.listing_health_hist', 'enrich.market_highlights_hist', 'enrich.order_shipping_cost',
                           'costing.cost_history', 'costing.costos_validados', 'ops.migraciones'] loop
    if to_regclass(r) is null then
      falta := falta || r;
    end if;
  end loop;
  if to_regprocedure('ops.tg_solo_agregar()') is null or to_regprocedure('ops.tg_sin_truncate()') is null then
    falta := falta || 'ops.tg_solo_agregar() u ops.tg_sin_truncate() (0064)'::text;
  end if;
  if to_regtype('citext') is null then
    falta := falta || 'la extensión citext'::text;
  end if;
  if cardinality(falta) > 0 then
    raise exception using errcode = 'KB000',
      message = '0074: falta ' || array_to_string(falta, ', ');
  end if;
  -- Una versión anterior a medias: el esquema existe y la migración no está registrada.
  if to_regnamespace('pricing') is not null and to_regclass('ops.migraciones') is not null
     and not exists (select 1 from ops.migraciones where migracion = '0074_pricing_historial') then
    raise exception using errcode = 'KB000',
      message = '0074: el esquema pricing ya existe sin su fila en ops.migraciones (¿quedó la v1, o se recreó el sandbox '
                'sin pricing en ESQUEMAS_PROPIOS?). Revisar a mano: un if not exists no corrige una forma vieja.';
  end if;
end
$previo$;

create schema if not exists pricing;
comment on schema pricing is
  'Memoria de largo plazo de precios, promociones, calidad y competencia por publicación (0074 v2). Ver el encabezado de la migración.';
revoke all on schema pricing from public, anon, authenticated;
grant usage on schema pricing to service_role;

-- ───────────────────────────────────────────────────────────────────────────
-- 1) Funciones del esquema
-- ───────────────────────────────────────────────────────────────────────────
-- El filtro de tokens de las columnas libres, en UN solo lugar. Solo patrones
-- inconfundibles (token de ML, refresh de ML, Bearer, Apify); los datos
-- personales de compradores se tapan en el escritor.
create or replace function pricing.sin_secretos(texto text) returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog
as $$
  select texto is null
      or texto !~ ('(APP_USR-[0-9]{6,}|TG-[0-9a-f]{20,}|[Bb]earer [A-Za-z0-9._~+/=-]{20,}|apify_api_'
                   '|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}'      -- JWT (llaves de Supabase)
                   '|Atz[ar]\|[A-Za-z0-9_-]{20,}'                        -- Amazon LWA
                   '|\m(ck|cs)_[0-9a-f]{40}\M'                           -- WooCommerce
                   '|\msk-[A-Za-z0-9_-]{20,}'                             -- llaves sk-
                   '|\mgAAAAA[A-Za-z0-9_-]{40,}'                          -- Fernet (tokens cifrados)
                   '|-----BEGIN [A-Z ]*PRIVATE KEY-----'
                   '|[a-z][a-z0-9+.-]*://[^/[:space:]:@]+:[^@[:space:]/]+@'   -- URL con usuario:contraseña
                   '|hooks\.slack\.com/services/|\mxox[abposr]-'            -- Slack
                   '|\mAIza[0-9A-Za-z_-]{35}'                                -- Google / Gemini
                   '|\msb_secret_'                                           -- Supabase (llaves nuevas)
                   '|\mgh[pousr]_[A-Za-z0-9]{36}|github_pat_'                -- GitHub
                   '|\mr8_[A-Za-z0-9]{20,})')                                -- Replicate
$$;
comment on function pricing.sin_secretos(text) is
  'true si el texto no trae un token reconocible (ML APP_USR-/TG-, Bearer, Apify, JWT, Amazon LWA, Woo ck_/cs_, sk-, Fernet, llave privada, URL con contraseña, Slack, Google/Gemini, Supabase sb_secret_, GitHub, Replicate). La usan los CHECK de las columnas libres de pricing.';

-- Historia que se corrige con UPDATE pero NO se borra (las de solo agregar
-- usan ops.tg_solo_agregar). Por sentencia: no cuesta nada por fila.
create or replace function pricing.tg_sin_borrado() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception using errcode = '42501', constraint = tg_table_name || '_sin_borrado',
    message = format('%s.%s no se borra: DELETE rechazado', tg_table_schema, tg_table_name),
    hint = 'Se cierra el periodo (hasta) o se cambia el estado. Borrar es su propia acta, con el trigger desactivado y respaldo.';
end
$$;
comment on function pricing.tg_sin_borrado() is
  'BEFORE DELETE por sentencia de las tablas de pricing que se corrigen pero no se borran. SQLSTATE 42501, constraint <tabla>_sin_borrado.';

-- La oferta que cobra tiene que haber estado ACTIVA ese día (una FK solo
-- puede decir que se vio).
create or replace function pricing.tg_cobra_iniciada() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.oferta_cobra_id is not null and not exists (
       select 1
         from pricing.oferta_dia od
         join pricing.oferta o on o.id = od.oferta_id
        where od.oferta_id = new.oferta_cobra_id
          and od.fecha = new.fecha
          and od.estado = 'started'
          and o.publicacion_id = new.publicacion_id) then
    raise exception using errcode = '23514', constraint = 'listing_dia_cobra_iniciada_chk',
      message = format('pricing.listing_dia: la oferta %s no es de la publicación %s o no estaba activa (started) el %s',
                       new.oferta_cobra_id, new.publicacion_id, new.fecha),
      hint = 'La oferta que cobra es de ESTA publicación y se escribe antes en pricing.oferta_dia con estado started.';
  end if;
  return new;
end
$$;
comment on function pricing.tg_cobra_iniciada() is
  'BEFORE INSERT/UPDATE de pricing.listing_dia: la oferta que cobra es de esa publicación y estaba started ese día. SQLSTATE 23514, constraint listing_dia_cobra_iniciada_chk.';

-- Foto diaria: se puede COMPLETAR (NULL → valor) pero un valor medido no se
-- reescribe. Por fila; compara columna por columna.
create or replace function pricing.tg_solo_completar() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  viejo jsonb := to_jsonb(old);
  nuevo jsonb := to_jsonb(new);
  k text;
begin
  for k in select jsonb_object_keys(viejo) loop
    if (viejo -> k) <> 'null'::jsonb and (nuevo -> k) is distinct from (viejo -> k) then
      raise exception using errcode = '42501', constraint = tg_table_name || '_solo_completar',
        message = format('%s.%s: «%s» ya estaba medida; solo se pueden llenar valores que estaban en NULL',
                         tg_table_schema, tg_table_name, k),
        hint = 'La foto no se reescribe. Corregir un valor medido es su propia acta, con el trigger desactivado y respaldo.';
    end if;
  end loop;
  return new;
end
$$;
comment on function pricing.tg_solo_completar() is
  'BEFORE UPDATE por fila: deja llenar columnas que estaban en NULL y rechaza cambiar cualquier valor ya medido (incluida la llave). SQLSTATE 42501, constraint <tabla>_solo_completar.';

-- Periodos [desde, hasta] (date, inclusivos; hasta NULL = sigue) que no se
-- enciman para la misma llave. TG_ARGV = columnas de la llave sin desde.
create or replace function pricing.tg_sin_traslape() returns trigger
language plpgsql
set search_path = pg_catalog, public, extensions   -- el = de citext vive en public (patrón de la 0065)
as $$
declare
  igual_nuevo text := '';
  igual_viejo text := '';
  col         text;
  hay         boolean;
begin
  -- `=` y no `is not distinct from`: las columnas de TG_ARGV son NOT NULL y así
  -- se usa la PK (llave, desde) en vez de recorrer la tabla.
  foreach col in array tg_argv loop
    igual_nuevo := igual_nuevo || format(' and t.%1$I = $1.%1$I', col);
    igual_viejo := igual_viejo || format(' and t.%1$I = $2.%1$I', col);
  end loop;
  -- La fila misma no es traslape: en INSERT, la de la misma llave y el mismo
  -- desde la resuelven la PK o el ON CONFLICT (los BEFORE INSERT corren antes);
  -- en UPDATE se excluye por la llave VIEJA.
  execute format('select exists (select 1 from %I.%I t where daterange(t.desde, t.hasta, ''[]'') && daterange($1.desde, $1.hasta, ''[]'')',
                 tg_table_schema, tg_table_name)
          || igual_nuevo
          || case when tg_op = 'UPDATE' then ' and not (true' || igual_viejo || ' and t.desde = $2.desde)'
                  else ' and t.desde <> $1.desde' end
          || ')'
    into hay using new, old;
  if hay then
    raise exception using errcode = '23P01', constraint = tg_table_name || '_sin_traslape',
      message = format('%s.%s: el periodo [%s, %s] se encima con otro de la misma llave',
                       tg_table_schema, tg_table_name, new.desde, coalesce(new.hasta::text, '…')),
      hint = 'Cerrar antes el periodo anterior (hasta) o corregir las fechas. Con ON CONFLICT DO UPDATE, el [desde, hasta] propuesto se compara contra los OTROS tramos.';
  end if;
  return new;
end
$$;
comment on function pricing.tg_sin_traslape() is
  'BEFORE INSERT/UPDATE: rechaza un periodo [desde, hasta] que se encime con otro de la misma llave (TG_ARGV: columnas NOT NULL). Acepta upserts (la fila de la misma PK no cuenta) y cambios de llave (se excluye por la llave vieja). SQLSTATE 23P01, constraint <tabla>_sin_traslape. Un solo escritor: no cubre dos inserciones concurrentes.';

-- Cada fila diaria cuelga de una captura de SU día y de SU grupo.
create or replace function pricing.tg_captura_coherente() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  cta         uuid;
  cuenta_fila uuid;
begin
  select c.account_id into cta
    from pricing.captura c
   where c.id = new.captura_id and c.fecha = new.fecha and c.grupo = tg_argv[0];
  if not found then
    raise exception using errcode = '23514', constraint = tg_table_name || '_captura_coherente_chk',
      message = format('%s.%s: la captura %s no es del grupo %s del día %s',
                       tg_table_schema, tg_table_name, new.captura_id, tg_argv[0], new.fecha),
      hint = 'captura.fecha es el día de los DATOS y captura.grupo el de la tabla.';
  end if;
  if cta is not null then
    -- Una sentencia por rama: plpgsql solo resuelve new.<col> de la rama que corre.
    if tg_argv[1] = 'publicacion' then
      select p.account_id into cuenta_fila from pricing.publicacion p where p.id = new.publicacion_id;
    elsif tg_argv[1] = 'oferta' then
      select p.account_id into cuenta_fila
        from pricing.oferta o join pricing.publicacion p on p.id = o.publicacion_id
       where o.id = new.oferta_id;
    elsif tg_argv[1] = 'cuenta' then
      cuenta_fila := new.account_id;
    end if;
    if cuenta_fila is distinct from cta then
      raise exception using errcode = '23514', constraint = tg_table_name || '_captura_coherente_chk',
        message = format('%s.%s: la captura %s es de otra cuenta', tg_table_schema, tg_table_name, new.captura_id);
    end if;
  end if;
  return new;
end
$$;
comment on function pricing.tg_captura_coherente() is
  'BEFORE INSERT/UPDATE: la captura de la fila es del mismo día (captura.fecha = fecha), del grupo TG_ARGV[0] y, si la captura es de una cuenta, de la cuenta de la fila (TG_ARGV[1]: publicacion | oferta | cuenta). SQLSTATE 23514.';

-- La llave natural de una dimensión no se cambia (re-etiquetaría la historia).
create or replace function pricing.tg_llave_inmutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception using errcode = '42501', constraint = tg_table_name || '_llave_inmutable',
    message = format('%s.%s: la llave natural no se cambia (re-etiquetaría toda su historia)', tg_table_schema, tg_table_name),
    hint = 'Si el canal cambió el id, es OTRA fila.';
end
$$;
comment on function pricing.tg_llave_inmutable() is
  'BEFORE UPDATE OF <llave natural>: rechaza cambiarla. SQLSTATE 42501, constraint <tabla>_llave_inmutable.';

-- La oferta copia el tipo de su campaña (no puede contradecirlo).
create or replace function pricing.tg_oferta_tipo() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  pr_tipo   text;
  pr_cuenta uuid;
  pr_canal  text;
  pu_cuenta uuid;
  pu_canal  text;
begin
  if new.promocion_id is not null then
    select p.tipo, p.account_id, p.canal into pr_tipo, pr_cuenta, pr_canal
      from pricing.promocion p where p.id = new.promocion_id;
    if not found then
      raise exception using errcode = '23503', constraint = 'oferta_promocion_fk',
        message = format('pricing.oferta: la promoción %s no existe', new.promocion_id);
    end if;
    select pu.account_id, a.channel_id into pu_cuenta, pu_canal
      from pricing.publicacion pu join core.accounts a on a.id = pu.account_id
     where pu.id = new.publicacion_id;
    if pr_canal is distinct from pu_canal or (pr_cuenta is not null and pr_cuenta is distinct from pu_cuenta) then
      raise exception using errcode = '23514', constraint = 'oferta_promocion_cuenta_chk',
        message = format('pricing.oferta: la promoción %s es de otro canal o de otra cuenta que la publicación %s',
                         new.promocion_id, new.publicacion_id);
    end if;
    new.tipo := pr_tipo;
  end if;
  return new;
end
$$;
comment on function pricing.tg_oferta_tipo() is
  'BEFORE INSERT/UPDATE de pricing.oferta: con campaña, el tipo es el de la campaña (dependencia transitiva resuelta en un solo lugar), la campaña existe (23503) y es del canal de la publicación y, si es de un vendedor, de su misma cuenta (23514).';

-- La campaña de un vendedor es del canal de esa cuenta.
create or replace function pricing.tg_promocion_coherente() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.account_id is not null
     and new.canal is distinct from (select a.channel_id from core.accounts a where a.id = new.account_id) then
    raise exception using errcode = '23514', constraint = 'promocion_cuenta_canal_chk',
      message = format('pricing.promocion: la cuenta %s no es del canal %s', new.account_id, new.canal);
  end if;
  return new;
end
$$;
comment on function pricing.tg_promocion_coherente() is
  'BEFORE INSERT/UPDATE OF canal, account_id de pricing.promocion: la cuenta dueña es del mismo canal. SQLSTATE 23514.';

-- Si el canal corrige el tipo de una campaña, sus ofertas lo siguen.
create or replace function pricing.tg_promocion_tipo_propaga() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  update pricing.oferta o set tipo = new.tipo where o.promocion_id = new.id and o.tipo is distinct from new.tipo;
  return null;
end
$$;
comment on function pricing.tg_promocion_tipo_propaga() is
  'AFTER UPDATE OF tipo de pricing.promocion (solo si cambió): copia el tipo nuevo a sus ofertas.';

-- Una decisión con publicaciones ya aplicadas no regresa a propuesta ni a descartada.
create or replace function pricing.tg_decision_estado_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if exists (select 1 from pricing.decision_listing dl where dl.decision_id = new.id and dl.aplicado_at is not null)
     or exists (select 1 from pricing.accion a where a.decision_id = new.id and a.estado in ('pedida', 'aplicada', 'parcial', 'deshecha')) then
    raise exception using errcode = '23514', constraint = 'decision_estado_chk',
      message = format('pricing.decision %s ya tiene publicaciones aplicadas: no puede volver a «%s»', new.id, new.estado),
      hint = 'Usar «revertida» (con revertida_at).';
  end if;
  return new;
end
$$;
comment on function pricing.tg_decision_estado_guarda() is
  'BEFORE UPDATE OF estado de pricing.decision hacia propuesta o descartada: rechaza si ya hay publicaciones aplicadas. SQLSTATE 23514.';

-- Columnas que no se reescriben salvo las de atribución (TG_ARGV).
create or replace function pricing.tg_solo_atribuir() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  viejo jsonb := to_jsonb(old) - tg_argv;
  nuevo jsonb := to_jsonb(new) - tg_argv;
  k     text;
begin
  if viejo is distinct from nuevo then
    select x.key into k from jsonb_each(viejo) x where (nuevo -> x.key) is distinct from x.value limit 1;
    raise exception using errcode = '42501', constraint = tg_table_name || '_solo_atribuir',
      message = format('%s.%s: «%s» no se reescribe; solo se pueden cambiar %s',
                       tg_table_schema, tg_table_name, k, array_to_string(tg_argv, ', ')),
      hint = 'Un precio o una ficha distinta es OTRA fila (otro desde).';
  end if;
  return new;
end
$$;
comment on function pricing.tg_solo_atribuir() is
  'BEFORE UPDATE por fila: solo deja cambiar las columnas de TG_ARGV (atribución, cierre del periodo); lo medido no se reescribe. SQLSTATE 42501, constraint <tabla>_solo_atribuir.';

-- Las visitas de un día ya cerrado no se reescriben.
create or replace function pricing.tg_resultado_cerrado() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if old.visitas_completa
     and (new.visitas, new.preguntas_recibidas, new.preguntas_sin_responder, new.visitas_completa)
         is distinct from (old.visitas, old.preguntas_recibidas, old.preguntas_sin_responder, old.visitas_completa) then
    raise exception using errcode = '42501', constraint = 'resultado_dia_cerrado',
      message = format('pricing.resultado_dia: el día %s de la publicación %s ya cerró (visitas_completa)', old.fecha, old.publicacion_id);
  end if;
  return new;
end
$$;
comment on function pricing.tg_resultado_cerrado() is
  'BEFORE UPDATE de pricing.resultado_dia: con visitas_completa = true, visitas, preguntas y la bandera ya no cambian. SQLSTATE 42501.';

-- Al dar de alta una acción: el tipo de actor sale del actor, no hay acciones
-- anónimas, un deshacer apunta a algo deshacible, la decisión no es un borrador
-- y el motivo está activo.
create or replace function pricing.tg_accion_alta() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  obj_estado text;
  obj_tipo   text;
begin
  if new.actor = 'anonimo' then
    raise exception using errcode = '42501', constraint = 'accion_actor_chk',
      message = 'pricing.accion: una acción anónima no se registra';
  end if;
  if new.actor_tipo is null then
    new.actor_tipo := case when new.actor = 'automatico' then 'automatico'
                           when position('@' in new.actor) > 1 then 'persona'
                           else 'maquina' end;
  end if;
  if new.deshace_id is not null then
    select x.estado, x.tipo into obj_estado, obj_tipo from pricing.accion x where x.id = new.deshace_id;
    if obj_estado is null or obj_estado not in ('aplicada', 'parcial') or obj_tipo in ('nota', 'revisada_sin_cambio', 'deshacer') then
      raise exception using errcode = '23514', constraint = 'accion_deshacer_objetivo_chk',
        message = format('pricing.accion: la acción %s no se puede deshacer (solo una aplicada o parcial que dejó cambios)', new.deshace_id);
    end if;
  end if;
  if new.decision_id is not null
     and exists (select 1 from pricing.decision d where d.id = new.decision_id and d.estado in ('propuesta', 'descartada')) then
    raise exception using errcode = '23514', constraint = 'accion_decision_abierta_chk',
      message = format('pricing.accion: la decisión %s está en propuesta o descartada; pasarla a aplicada antes de actuar', new.decision_id);
  end if;
  if new.motivo is not null and exists (select 1 from pricing.motivo m where m.codigo = new.motivo and not m.activo) then
    raise exception using errcode = '23514', constraint = 'accion_motivo_activo_chk',
      message = format('pricing.accion: el motivo «%s» está desactivado', new.motivo);
  end if;
  return new;
end
$$;
comment on function pricing.tg_accion_alta() is
  'BEFORE INSERT de pricing.accion: deriva actor_tipo del actor (automatico | persona si trae @ | maquina), rechaza anónimos, deshacer de algo no deshacible, decisiones en borrador y motivos desactivados.';

-- Una acción avanza en un solo sentido; lo que describe cómo terminó no se
-- reescribe después de cerrar; la decisión y el motivo solo se llenan.
create or replace function pricing.tg_accion_estado() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.estado is distinct from old.estado then
    if not ((old.estado = 'pedida' and new.estado in ('aplicada', 'parcial', 'error', 'sin_ejecutar'))
         or (old.estado in ('aplicada', 'parcial') and new.estado = 'deshecha')) then
      raise exception using errcode = '23514', constraint = 'accion_estado_transicion_chk',
        message = format('pricing.accion %s no puede pasar de «%s» a «%s»', old.id, old.estado, new.estado),
        hint = 'pedida → aplicada | parcial | error | sin_ejecutar; aplicada | parcial → deshecha.';
    end if;
    if new.estado = 'deshecha' and not exists (select 1 from pricing.accion x
                                                where x.deshace_id = old.id and x.estado in ('aplicada', 'parcial')) then
      raise exception using errcode = '23514', constraint = 'accion_deshecha_chk',
        message = format('pricing.accion %s: no hay un deshacer aplicado que la regrese', old.id);
    end if;
  end if;
  if old.estado <> 'pedida' then
    if new.motivo is distinct from old.motivo or new.motivo_texto is distinct from old.motivo_texto then
      raise exception using errcode = '42501', constraint = 'accion_motivo_inmutable',
        message = format('pricing.accion %s: el motivo se guarda al momento (al crearla o al cerrarla), no después', old.id),
        hint = 'Para agregar contexto después, una acción «nota».';
    end if;
    if new.terminada_at is distinct from old.terminada_at or new.error is distinct from old.error then
      raise exception using errcode = '42501', constraint = 'accion_cierre_inmutable',
        message = format('pricing.accion %s ya cerró: su hora de término y su error no cambian', old.id);
    end if;
  elsif (old.motivo is not null and new.motivo is distinct from old.motivo)
     or (old.motivo_texto is not null and new.motivo_texto is distinct from old.motivo_texto) then
    raise exception using errcode = '42501', constraint = 'accion_motivo_inmutable',
      message = format('pricing.accion %s: el motivo ya escrito no se reescribe', old.id);
  end if;
  if new.decision_id is distinct from old.decision_id then
    if old.decision_id is not null then
      raise exception using errcode = '42501', constraint = 'accion_decision_inmutable',
        message = format('pricing.accion %s: la decisión ya atribuida no se cambia', old.id);
    end if;
    if exists (select 1 from pricing.decision d where d.id = new.decision_id and d.estado in ('propuesta', 'descartada')) then
      raise exception using errcode = '23514', constraint = 'accion_decision_abierta_chk',
        message = format('pricing.accion: la decisión %s está en propuesta o descartada', new.decision_id);
    end if;
  end if;
  if new.motivo is distinct from old.motivo and new.motivo is not null
     and exists (select 1 from pricing.motivo m where m.codigo = new.motivo and not m.activo) then
    raise exception using errcode = '23514', constraint = 'accion_motivo_activo_chk',
      message = format('pricing.accion: el motivo «%s» está desactivado', new.motivo);
  end if;
  return new;
end
$$;
comment on function pricing.tg_accion_estado() is
  'BEFORE UPDATE de pricing.accion: el estado solo avanza (pedida → aplicada | parcial | error | sin_ejecutar; aplicada | parcial → deshecha, y solo con un deshacer aplicado); motivo y texto se escriben al crear o al cerrar; hora de término y error no cambian tras cerrar; la decisión solo se llena. SQLSTATE 23514 / 42501.';

-- La acción y sus publicaciones no se contradicen. Se revisa AL COMMIT
-- (constraint trigger diferido): el escritor puede cerrar la acción y luego sus
-- filas, o al revés, dentro de la misma transacción.
create or replace function pricing.tg_accion_coherente() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  aid bigint;
  est text;
  tip text;
begin
  if tg_table_name = 'accion' then
    aid := new.id;
  else
    aid := new.accion_id;
  end if;
  select a.estado, a.tipo into est, tip from pricing.accion a where a.id = aid;
  if est <> 'pedida' and exists (select 1 from pricing.accion_publicacion ap where ap.accion_id = aid and ap.resultado = 'pendiente') then
    raise exception using errcode = '23514', constraint = 'accion_coherente_chk',
      message = format('pricing.accion %s está «%s» pero tiene publicaciones pendientes', aid, est);
  end if;
  if est = 'error' and exists (select 1 from pricing.accion_publicacion ap where ap.accion_id = aid and ap.resultado in ('ok', 'parcial')) then
    raise exception using errcode = '23514', constraint = 'accion_coherente_chk',
      message = format('pricing.accion %s: «error» quiere decir que no quedó ningún cambio, pero hay publicaciones ok o parciales (usar «parcial»)', aid);
  end if;
  if tip in ('nota', 'revisada_sin_cambio')
     and exists (select 1 from pricing.accion_publicacion ap where ap.accion_id = aid and ap.resultado <> 'sin_cambio') then
    raise exception using errcode = '23514', constraint = 'accion_coherente_chk',
      message = format('pricing.accion %s es «%s»: sus publicaciones van con resultado sin_cambio', aid, tip);
  end if;
  if tip = 'deshacer' and est in ('aplicada', 'parcial')
     and not exists (select 1 from pricing.accion_publicacion ap where ap.accion_id = aid) then
    raise exception using errcode = '23514', constraint = 'accion_coherente_chk',
      message = format('pricing.accion %s: un deshacer aplicado dice qué publicaciones regresó', aid);
  end if;
  return null;
end
$$;
comment on function pricing.tg_accion_coherente() is
  'CONSTRAINT TRIGGER diferido (al COMMIT) en accion y accion_publicacion: acción cerrada sin filas pendientes; «error» sin filas ok ni parciales; notas y revisiones con filas sin_cambio; un deshacer aplicado con al menos una publicación. SQLSTATE 23514, constraint accion_coherente_chk.';

-- Una fila de un deshacer solo existe si la acción original tocó esa publicación.
create or replace function pricing.tg_accion_publicacion_alta() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  tip text;
  obj bigint;
begin
  select a.tipo, a.deshace_id into tip, obj from pricing.accion a where a.id = new.accion_id;
  if tip = 'deshacer' and not exists (select 1 from pricing.accion_publicacion x
                                       where x.accion_id = obj and x.publicacion_id = new.publicacion_id) then
    raise exception using errcode = '23514', constraint = 'accion_publicacion_deshacer_chk',
      message = format('pricing.accion_publicacion: la acción %s no tocó la publicación %s; no hay qué deshacer ahí', obj, new.publicacion_id);
  end if;
  return new;
end
$$;
comment on function pricing.tg_accion_publicacion_alta() is
  'BEFORE INSERT de pricing.accion_publicacion: un deshacer solo regresa publicaciones que la acción original tocó. SQLSTATE 23514.';

-- El resultado de una publicación avanza en un solo sentido y su error no se borra.
create or replace function pricing.tg_accion_publicacion_resultado() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.resultado is distinct from old.resultado
     and not (old.resultado = 'pendiente' or (old.resultado in ('ok', 'parcial') and new.resultado = 'regresada')) then
    raise exception using errcode = '23514', constraint = 'accion_publicacion_resultado_transicion_chk',
      message = format('pricing.accion_publicacion (%s, %s): no puede pasar de «%s» a «%s»',
                       old.accion_id, old.publicacion_id, old.resultado, new.resultado),
      hint = 'pendiente → ok | error | parcial | sin_cambio | regresada; ok | parcial → regresada. Un reintento es OTRA acción (reintenta_id).';
  end if;
  if old.error is not null and new.error is distinct from old.error then
    raise exception using errcode = '42501', constraint = 'accion_publicacion_error_inmutable',
      message = format('pricing.accion_publicacion (%s, %s): el error ya escrito no se borra', old.accion_id, old.publicacion_id);
  end if;
  return new;
end
$$;
comment on function pricing.tg_accion_publicacion_resultado() is
  'BEFORE UPDATE OF resultado, error de pricing.accion_publicacion: el resultado solo avanza y el error solo se llena.';

-- La decisión aplicada se llena SOLA desde la acción (una sola fuente: la acción).
create or replace function pricing.tg_accion_publicacion_decision() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  dec    bigint;
  cuando timestamptz;
begin
  if new.resultado is distinct from old.resultado then
    select a.decision_id, coalesce(a.terminada_at, now()) into dec, cuando from pricing.accion a where a.id = new.accion_id;
    if dec is not null then
      if new.resultado in ('ok', 'parcial') then
        insert into pricing.decision_listing (decision_id, publicacion_id, aplicado_at, precio_objetivo)
        values (dec, new.publicacion_id, cuando, new.precio_cobrado_pedido)
        on conflict (decision_id, publicacion_id)
        do update set aplicado_at = coalesce(pricing.decision_listing.aplicado_at, excluded.aplicado_at);
      elsif new.resultado = 'regresada' then
        update pricing.decision_listing
           set revertido_at = coalesce(revertido_at, now())
         where decision_id = dec and publicacion_id = new.publicacion_id and aplicado_at is not null;
      end if;
    end if;
  end if;
  return null;
end
$$;
comment on function pricing.tg_accion_publicacion_decision() is
  'AFTER UPDATE OF resultado de pricing.accion_publicacion: si la acción es de una decisión, llena decision_listing.aplicado_at (ok o parcial) o revertido_at (regresada). Así «cuándo se aplicó la decisión aquí» sale de la acción y no se escribe dos veces.';

-- La atribución de una observación solo se LLENA: la acción no se cambia ni se
-- borra, el origen solo se corrige desde «desconocido» o al atribuir, y un
-- cambio del panel nunca deja de serlo. TG_ARGV = pares (columna origen, columna acción).
create or replace function pricing.tg_atribucion() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v  jsonb := to_jsonb(old);
  w  jsonb := to_jsonb(new);
  i  integer;
  oc text;
  ac text;
begin
  for i in 0 .. (tg_nargs / 2) - 1 loop
    oc := tg_argv[2 * i];
    ac := tg_argv[2 * i + 1];
    if (v -> ac) <> 'null'::jsonb and (w -> ac) is distinct from (v -> ac) then
      raise exception using errcode = '42501', constraint = tg_table_name || '_atribucion',
        message = format('%s.%s: «%s» ya está atribuida; no se cambia ni se borra', tg_table_schema, tg_table_name, ac),
        hint = 'Si la atribución estaba mal, una acción «nota» lo explica.';
    end if;
    if (w -> oc) is distinct from (v -> oc) then
      if (v ->> oc) = 'panel' then
        raise exception using errcode = '42501', constraint = tg_table_name || '_atribucion',
          message = format('%s.%s: un cambio del panel no cambia de origen', tg_table_schema, tg_table_name);
      end if;
      if not ((v ->> oc) = 'desconocido' or ((v -> ac) = 'null'::jsonb and (w -> ac) <> 'null'::jsonb)) then
        raise exception using errcode = '42501', constraint = tg_table_name || '_atribucion',
          message = format('%s.%s: «%s» solo se corrige desde «desconocido» o al atribuir la acción', tg_table_schema, tg_table_name, oc);
      end if;
    end if;
  end loop;
  return new;
end
$$;
comment on function pricing.tg_atribucion() is
  'BEFORE UPDATE de las observaciones (precio_obs, listing_ficha, oferta): la acción atribuida solo pasa de NULL a un valor; el origen solo cambia desde «desconocido» o en la misma sentencia que atribuye; «panel» no cambia. SQLSTATE 42501, constraint <tabla>_atribucion.';

-- decision_listing: solo se borran filas de un borrador, y una publicación no
-- se marca aplicada si la decisión sigue en propuesta o descartada.
create or replace function pricing.tg_decision_listing_guarda() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  est text;
begin
  select x.estado into est from pricing.decision x
   where x.id = case when tg_op = 'DELETE' then old.decision_id else new.decision_id end;
  if tg_op = 'DELETE' then
    if old.aplicado_at is not null or est not in ('propuesta', 'descartada') then
      raise exception using errcode = '42501', constraint = 'decision_listing_sin_borrado',
        message = format('pricing.decision_listing: la publicación %s de la decisión %s ya se aplicó o la decisión no es borrador',
                         old.publicacion_id, old.decision_id),
        hint = 'Se revierte (revertido_at), no se borra.';
    end if;
    return old;
  end if;
  if new.aplicado_at is not null and est in ('propuesta', 'descartada') then
    raise exception using errcode = '23514', constraint = 'decision_listing_estado_chk',
      message = format('pricing.decision_listing: la decisión %s está en «%s»; pasarla a aplicada antes de aplicar publicaciones',
                       new.decision_id, est);
  end if;
  return new;
end
$$;
comment on function pricing.tg_decision_listing_guarda() is
  'BEFORE INSERT/UPDATE/DELETE de pricing.decision_listing: solo se borran filas sin aplicar de un borrador; no hay publicación aplicada en una decisión propuesta o descartada.';

revoke all on function pricing.sin_secretos(text)  from public, anon, authenticated;
revoke all on function pricing.tg_sin_borrado()    from public, anon, authenticated;
revoke all on function pricing.tg_cobra_iniciada() from public, anon, authenticated;
revoke all on function pricing.tg_solo_completar() from public, anon, authenticated;
revoke all on function pricing.tg_sin_traslape() from public, anon, authenticated;
revoke all on function pricing.tg_captura_coherente() from public, anon, authenticated;
revoke all on function pricing.tg_llave_inmutable() from public, anon, authenticated;
revoke all on function pricing.tg_oferta_tipo() from public, anon, authenticated;
revoke all on function pricing.tg_decision_listing_guarda() from public, anon, authenticated;
revoke all on function pricing.tg_promocion_coherente() from public, anon, authenticated;
revoke all on function pricing.tg_promocion_tipo_propaga() from public, anon, authenticated;
revoke all on function pricing.tg_decision_estado_guarda() from public, anon, authenticated;
revoke all on function pricing.tg_solo_atribuir() from public, anon, authenticated;
revoke all on function pricing.tg_resultado_cerrado() from public, anon, authenticated;
revoke all on function pricing.tg_accion_estado() from public, anon, authenticated;
revoke all on function pricing.tg_accion_alta() from public, anon, authenticated;
revoke all on function pricing.tg_accion_coherente() from public, anon, authenticated;
revoke all on function pricing.tg_accion_publicacion_alta() from public, anon, authenticated;
revoke all on function pricing.tg_accion_publicacion_resultado() from public, anon, authenticated;
revoke all on function pricing.tg_accion_publicacion_decision() from public, anon, authenticated;
revoke all on function pricing.tg_atribucion() from public, anon, authenticated;
-- Un CHECK que llama a una función exige EXECUTE a quien escribe.
grant execute on function pricing.sin_secretos(text) to service_role;

-- ───────────────────────────────────────────────────────────────────────────
-- 2) pricing.captura — cada corrida del escritor
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.captura (
    id            integer      generated always as identity,
    fecha         date         not null,
    iniciada_at   timestamptz  not null default now(),
    terminada_at  timestamptz,
    n_filas       integer,
    version       smallint     not null default 1,
    account_id    uuid,
    grupo         text         not null,
    codigo_ver    text,
    fuentes       jsonb        not null default '{}'::jsonb,
    constraint captura_pkey primary key (id),
    constraint captura_cuenta_fk foreign key (account_id) references core.accounts (id),
    constraint captura_grupo_chk check (grupo in ('foto', 'resultado', 'disponibilidad', 'competencia', 'ads', 'cuenta', 'ordenes')),
    constraint captura_fechas_chk check (terminada_at is null or terminada_at >= iniciada_at),
    constraint captura_n_chk check (n_filas is null or n_filas >= 0),
    constraint captura_version_chk check (version >= 1),
    constraint captura_fuentes_chk check (jsonb_typeof(fuentes) = 'object' and octet_length(fuentes::text) <= 8192),
    constraint captura_limpia_chk check (pricing.sin_secretos(fuentes::text) and pricing.sin_secretos(codigo_ver)
                                         and char_length(coalesce(codigo_ver, '')) <= 80)
);
create index if not exists captura_fecha_idx on pricing.captura (fecha, grupo);
comment on table pricing.captura is
  'Cada corrida del escritor: de qué día son los DATOS (fecha), qué grupo (foto, resultado, competencia, ads, cuenta, ordenes), cuándo empezó y terminó, con qué versión y de dónde salió cada grupo (fuentes, UNA vez por corrida y no por fila). Una corrida que lee el día D después de la medianoche lleva fecha = D.';
comment on column pricing.captura.fecha is
  'El día (México) de los DATOS que cubre la corrida, no el día en que corrió. Las filas diarias exigen captura.fecha = su fecha (pricing.tg_captura_coherente).';
comment on column pricing.captura.fuentes is
  '{grupo: {fuente, at}} de toda la corrida, p. ej. {"precio": {"fuente": "ml_sale_price", "at": "…"}}. Sin respuestas crudas ni tokens.';
comment on column pricing.captura.account_id is
  'NULL = la corrida cubrió todas las cuentas. Para ads: sin fila en ads_dia + captura terminada del día = cero anuncios; sin captura = no se midió. Para ordenes: una captura terminada del día = TODAS las órdenes de ML de ese día (y cuenta) tienen su fila en orden_etiqueta.';
alter table pricing.captura enable row level security;
revoke all on pricing.captura from public, anon, authenticated, service_role;
grant select, insert, update on pricing.captura to service_role;
create or replace trigger captura_llave_inmutable before update of fecha, grupo, account_id on pricing.captura
  for each row when ((old.fecha, old.grupo, old.account_id) is distinct from (new.fecha, new.grupo, new.account_id))
  execute function pricing.tg_llave_inmutable();
create or replace trigger captura_sin_borrado before delete on pricing.captura
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger captura_sin_truncate before truncate on pricing.captura
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 3) pricing.publicacion — la dimensión
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.publicacion (
    id          integer      generated always as identity,
    account_id  uuid         not null,
    listing_id  text         not null,
    creada_at   timestamptz  not null default now(),
    fecha_alta  timestamptz,
    constraint publicacion_pkey primary key (id),
    constraint publicacion_natural_uq unique (account_id, listing_id),
    constraint publicacion_cuenta_fk foreign key (account_id) references core.accounts (id),
    constraint publicacion_listing_chk check (char_length(listing_id) between 1 and 100 and listing_id = btrim(listing_id))
);
comment on table pricing.publicacion is
  'Una publicación de una cuenta: la llave de todo pricing (id integer, 4 B en vez de ~50 B de canal+cuenta+listing en cada fila e índice). El canal sale de core.accounts.channel_id. El escritor hace insert … on conflict (account_id, listing_id) do nothing y luego lee el id.';
comment on column pricing.publicacion.listing_id is
  'El id con que el canal identifica la publicación (MLM… en ML), como enrich.listing_health.listing_id. Inmutable.';
comment on column pricing.publicacion.fecha_alta is
  'date_created de la publicación en el canal (depende solo de la publicación: no va en la ficha). Se llena una vez.';
alter table pricing.publicacion enable row level security;
revoke all on pricing.publicacion from public, anon, authenticated, service_role;
grant select, insert, update on pricing.publicacion to service_role;
create or replace trigger publicacion_solo_completar before update on pricing.publicacion
  for each row execute function pricing.tg_solo_completar();
create or replace trigger publicacion_sin_borrado before delete on pricing.publicacion
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger publicacion_sin_truncate before truncate on pricing.publicacion
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 4) pricing.promocion — campañas del canal que TIENEN id
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.promocion (
    id               integer      generated always as identity,
    account_id       uuid,
    inicio           timestamptz,
    fin              timestamptz,
    visto_primero_at timestamptz  not null default now(),
    visto_ultimo_at  timestamptz  not null default now(),
    aporte_canal_pct numeric(5,4),
    promo_id         text         not null,
    canal            text         not null,
    tipo             text         not null,
    subtipo          text,
    nombre           text,
    financia         text         not null default 'desconocido',
    detalle          jsonb,
    constraint promocion_pkey primary key (id),
    constraint promocion_canal_uq unique (canal, promo_id),
    constraint promocion_canal_fk foreign key (canal) references core.channels (id),
    constraint promocion_cuenta_fk foreign key (account_id) references core.accounts (id),
    constraint promocion_no_sintetica_chk check (promo_id <> 'PRICE_DISCOUNT' and char_length(promo_id) between 1 and 80),
    constraint promocion_tipo_chk check (tipo ~ '^[A-Za-z][A-Za-z0-9_]{1,59}$'),
    constraint promocion_financia_chk check (financia in ('vendedor', 'canal', 'compartido', 'desconocido')),
    constraint promocion_aporte_chk check (aporte_canal_pct is null or aporte_canal_pct between 0 and 1),
    constraint promocion_fechas_chk check (fin is null or inicio is null or fin >= inicio),
    constraint promocion_visto_chk check (visto_ultimo_at >= visto_primero_at),
    constraint promocion_textos_chk check (char_length(coalesce(nombre, '')) <= 300 and char_length(coalesce(subtipo, '')) <= 80),
    constraint promocion_detalle_chk check (detalle is null or (jsonb_typeof(detalle) = 'object'
                                            and octet_length(detalle::text) <= 32768 and pricing.sin_secretos(detalle::text)))
);
comment on table pricing.promocion is
  'Campañas con id propio (C-MLM…, P-MLM…), una vez por canal: las del canal (DEAL, SMART…) tienen id global y no se repiten por cuenta. account_id = la cuenta dueña de una campaña del vendedor (C-MLM…); NULL = campaña del canal. El descuento individual (PRICE_DISCOUNT) NO tiene id en la API y no vive aquí: es una oferta sin promocion_id. tipo es el de ML, sin lista cerrada porque ML agrega tipos.';
comment on column pricing.promocion.aporte_canal_pct is
  'El aporte que DECLARA la campaña (0..1). El de cada publicación varía (SMART) y vive en oferta_dia.meli_pct.';
alter table pricing.promocion enable row level security;
revoke all on pricing.promocion from public, anon, authenticated, service_role;
grant select, insert, update on pricing.promocion to service_role;
create or replace trigger promocion_llave_inmutable before update of canal, promo_id on pricing.promocion
  for each row when ((old.canal, old.promo_id) is distinct from (new.canal, new.promo_id))
  execute function pricing.tg_llave_inmutable();
create or replace trigger promocion_coherente before insert or update of canal, account_id on pricing.promocion
  for each row execute function pricing.tg_promocion_coherente();
create or replace trigger promocion_tipo_propaga after update of tipo on pricing.promocion
  for each row when (old.tipo is distinct from new.tipo) execute function pricing.tg_promocion_tipo_propaga();
create or replace trigger promocion_sin_borrado before delete on pricing.promocion
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger promocion_sin_truncate before truncate on pricing.promocion
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 5) pricing.decision + pricing.decision_listing — «Qué cambió»
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.decision (
    id                    bigint       generated always as identity,
    creada_at             timestamptz  not null default now(),
    evaluada_at           timestamptz,
    revertida_at          timestamptz,
    meta                  numeric,
    ventana_antes_dias    smallint     not null default 14,
    ventana_despues_dias  smallint     not null default 14,
    autor                 text         not null default nullif(current_setting('app.usuario', true), ''),
    titulo                text         not null,
    tipo                  text         not null,
    estado                text         not null default 'propuesta',
    metrica_objetivo      text,
    hipotesis             text,
    notas                 text,
    resultado             jsonb,
    constraint decision_pkey primary key (id),
    constraint decision_autor_chk check (char_length(autor) between 2 and 120),
    constraint decision_textos_chk check (char_length(titulo) between 1 and 200
                                          and char_length(coalesce(hipotesis, '')) <= 4000
                                          and char_length(coalesce(notas, '')) <= 4000),
    constraint decision_tipo_chk check (tipo in ('subir_precio', 'bajar_precio', 'descuento', 'quitar_descuento',
        'entrar_campana', 'salir_campana', 'cupon', 'mejorar_publicacion', 'cambiar_categoria', 'otro')),
    constraint decision_metrica_chk check (metrica_objetivo is null or metrica_objetivo in
        ('unidades', 'conversion', 'ingreso', 'margen', 'visitas', 'buybox', 'otro')),
    constraint decision_estado_chk check (estado in ('propuesta', 'aplicada', 'en_medicion', 'evaluada', 'revertida', 'descartada')),
    constraint decision_ventanas_chk check (ventana_antes_dias between 1 and 180 and ventana_despues_dias between 1 and 180),
    constraint decision_evaluada_chk check (estado <> 'evaluada' or evaluada_at is not null),
    constraint decision_revertida_chk check (estado <> 'revertida' or revertida_at is not null),
    constraint decision_resultado_chk check (resultado is null or (jsonb_typeof(resultado) = 'object'
                                             and octet_length(resultado::text) <= 65536)),
    constraint decision_limpia_chk check (pricing.sin_secretos(resultado::text) and pricing.sin_secretos(notas)
                                          and pricing.sin_secretos(hipotesis))
);
comment on table pricing.decision is
  'Una decisión de pricing con su hipótesis y la ventana con que se mide (antes contra después). No se borra: se marca descartada o revertida. Cuándo se aplicó NO vive aquí: es el mínimo de decision_listing.aplicado_at (ver pricing.decision_v).';
comment on column pricing.decision.autor is
  'Quién la propuso. Por omisión la firma de la sesión (app.usuario, que pone supabase_db.get_cursor). Sin firma ni autor explícito, el INSERT falla.';
alter table pricing.decision enable row level security;
revoke all on pricing.decision from public, anon, authenticated, service_role;
grant select, insert, update on pricing.decision to service_role;
create or replace trigger decision_estado_guarda before update of estado on pricing.decision
  for each row when (new.estado in ('propuesta', 'descartada') and old.estado is distinct from new.estado)
  execute function pricing.tg_decision_estado_guarda();
create or replace trigger decision_sin_borrado before delete on pricing.decision
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger decision_sin_truncate before truncate on pricing.decision
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.decision_listing (
    decision_id      bigint        not null,
    publicacion_id   integer       not null,
    promocion_id     integer,
    aplicado_at      timestamptz,
    revertido_at     timestamptz,
    precio_visto     numeric(12,2),
    precio_objetivo  numeric(12,2),
    constraint decision_listing_pkey primary key (decision_id, publicacion_id),
    constraint decision_listing_decision_fk foreign key (decision_id) references pricing.decision (id) on delete restrict,
    constraint decision_listing_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint decision_listing_promocion_fk foreign key (promocion_id) references pricing.promocion (id),
    constraint decision_listing_precios_chk check (coalesce(precio_visto, 0) >= 0 and coalesce(precio_objetivo, 0) >= 0),
    constraint decision_listing_fechas_chk check (revertido_at is null or (aplicado_at is not null and revertido_at >= aplicado_at))
);
create index if not exists decision_listing_pub_idx on pricing.decision_listing (publicacion_id, aplicado_at);
comment on table pricing.decision_listing is
  'A qué publicaciones aplica cada decisión, cuándo se aplicó y, si fue el caso, cuándo se revirtió en ESA publicación. Las filas de un borrador se pueden borrar; las demás no se tocan.';
comment on column pricing.decision_listing.precio_visto is
  'El precio que vio quien propuso (foto de la propuesta). El precio real sale de precio_obs.';
comment on column pricing.decision_listing.promocion_id is
  'La campaña a la que se decidió entrar o salir. NULL si la decisión es un descuento propio (basta precio_objetivo).';
alter table pricing.decision_listing enable row level security;
revoke all on pricing.decision_listing from public, anon, authenticated, service_role;
grant select, insert, update, delete on pricing.decision_listing to service_role;
create or replace trigger decision_listing_guarda before insert or update or delete on pricing.decision_listing
  for each row execute function pricing.tg_decision_listing_guarda();
create or replace trigger decision_listing_sin_truncate before truncate on pricing.decision_listing
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 5b) BITÁCORA DE ACCIONES — quién hizo qué, por qué, qué vio y cómo terminó
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.motivo (
    codigo       text         not null,
    nombre       text         not null,
    descripcion  text,
    activo       boolean      not null default true,
    creado_at    timestamptz  not null default now(),
    constraint motivo_pkey primary key (codigo),
    constraint motivo_codigo_chk check (codigo ~ '^[a-z][a-z0-9_]{1,39}$'),
    constraint motivo_textos_chk check (char_length(nombre) between 1 and 80 and char_length(coalesce(descripcion, '')) <= 500)
);
comment on table pricing.motivo is
  'Catálogo de motivos de una acción: un vocabulario fijo para poder contar y comparar («¿cuántas bajadas por competencia funcionaron?»), que crece sin migración (insert). No se borra ni se renombra: se desactiva (activo = false) y un significado nuevo es un código nuevo, para que las acciones viejas sigan diciendo lo que dijeron.';
alter table pricing.motivo enable row level security;
revoke all on pricing.motivo from public, anon, authenticated, service_role;
grant select, insert, update on pricing.motivo to service_role;
create or replace trigger motivo_solo_atribuir before update on pricing.motivo
  for each row execute function pricing.tg_solo_atribuir('activo');
create or replace trigger motivo_sin_borrado before delete on pricing.motivo
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger motivo_sin_truncate before truncate on pricing.motivo
  for each statement execute function ops.tg_sin_truncate();
insert into pricing.motivo (codigo, nombre, descripcion) values
  ('mas_cara_que_competencia',   'Más cara que la competencia',  'Cobra más que el rival comparable más barato.'),
  ('mas_barata_que_competencia', 'Muy abajo de la competencia',  'Deja dinero en la mesa frente a los rivales.'),
  ('sin_ventas',                 'Sin ventas',                   'No ha vendido en el periodo que se mira.'),
  ('poca_conversion',            'Convierte poco',               'Tiene visitas y convierte menos que la meta.'),
  ('subir_margen',               'Subir margen',                 'El precio deja poco margen sobre el costo.'),
  ('bajo_costo',                 'Precio abajo del costo',       'Vende por debajo de su piso (costo + comisión + envío).'),
  ('sobrestock',                 'Mucho stock',                  'Stock alto para su ritmo de venta.'),
  ('se_agota',                   'Se va a agotar',               'Pocos días de stock al ritmo actual.'),
  ('campana_canal',              'Campaña del canal',            'Entrar, salir o ajustarse a una campaña del canal.'),
  ('temporada',                  'Temporada o evento',           'Buen Fin, Hot Sale, quincena, festivo.'),
  ('correccion',                 'Corregir un error',            'Un precio, descuento o dato que estaba mal.'),
  ('prueba',                     'Prueba o experimento',         'Cambio para medir una hipótesis.'),
  ('instruccion',                'Instrucción de alguien',       'Lo pidió otra persona (decirlo en el texto).'),
  ('otro',                       'Otro',                         'Explicarlo en el texto.')
on conflict (codigo) do nothing;

create table if not exists pricing.accion (
    id              bigint       generated always as identity,
    iniciada_at     timestamptz  not null default now(),
    terminada_at    timestamptz,
    registrada_at   timestamptz  not null default now(),
    foto_generado   timestamptz,
    decision_id     bigint,
    deshace_id      bigint,
    reintenta_id    bigint,
    process_log_id  bigint,
    actor           text         not null default nullif(current_setting('app.usuario', true), ''),
    actor_tipo      text         not null,
    actor_rol       text,
    registrada_por  text         not null default nullif(current_setting('app.usuario', true), ''),
    origen_app      text         not null default nullif(current_setting('app.origen', true), ''),
    pantalla        text,
    tipo            text         not null,
    estado          text         not null default 'pedida',
    motivo          text,
    motivo_texto    text,
    regla_version   text,
    error           text,
    umbrales        jsonb,
    constraint accion_pkey primary key (id),
    constraint accion_decision_fk foreign key (decision_id) references pricing.decision (id) on delete restrict,
    constraint accion_deshace_fk foreign key (deshace_id) references pricing.accion (id) on delete restrict,
    constraint accion_reintenta_fk foreign key (reintenta_id) references pricing.accion (id) on delete restrict,
    constraint accion_motivo_fk foreign key (motivo) references pricing.motivo (codigo),
    constraint accion_actor_chk check (char_length(actor) between 2 and 120 and char_length(registrada_por) between 2 and 120),
    constraint accion_actor_tipo_chk check (actor_tipo in ('persona', 'maquina', 'automatico')),
    constraint accion_actor_coherente_chk check ((actor = 'automatico') = (actor_tipo = 'automatico')
                                                 and (actor <> 'servicio' or actor_tipo = 'maquina')),
    constraint accion_rol_chk check (actor_rol is null or actor_rol in ('admin', 'operador', 'lectura')),
    constraint accion_origen_chk check (origen_app in ('panel', 'seller_center', 'script', 'claude', 'cron', 'api', 'otro')),
    constraint accion_tipo_chk check (tipo in ('editar_precio', 'cambiar_precio', 'poner_descuento', 'quitar_descuento',
        'entrar_campana', 'salir_campana', 'deshacer', 'cambiar_categoria', 'cambiar_ficha', 'pausar', 'activar',
        'revisada_sin_cambio', 'nota', 'otro')),
    constraint accion_estado_chk check (estado in ('pedida', 'aplicada', 'parcial', 'error', 'deshecha', 'sin_ejecutar')),
    constraint accion_deshacer_chk check ((tipo = 'deshacer') = (deshace_id is not null) and deshace_id is distinct from id),
    constraint accion_reintento_chk check (reintenta_id is distinct from id),
    constraint accion_sin_cambio_chk check (tipo not in ('revisada_sin_cambio', 'nota') or estado = 'sin_ejecutar'),
    constraint accion_cierre_chk check ((estado = 'pedida') = (terminada_at is null)
                                        and (terminada_at is null or terminada_at >= iniciada_at)
                                        and registrada_at >= iniciada_at - interval '1 minute'),
    constraint accion_error_chk check (estado <> 'error' or error is not null),
    constraint accion_otro_chk check (motivo is distinct from 'otro' or motivo_texto is not null),
    constraint accion_textos_chk check (char_length(coalesce(motivo_texto, '')) <= 2000 and char_length(coalesce(error, '')) <= 1000
                                        and char_length(coalesce(pantalla, '')) <= 80 and char_length(coalesce(regla_version, '')) <= 40),
    constraint accion_umbrales_chk check (umbrales is null or (jsonb_typeof(umbrales) = 'object' and octet_length(umbrales::text) <= 4096)),
    constraint accion_limpia_chk check (pricing.sin_secretos(motivo_texto) and pricing.sin_secretos(error)
                                        and pricing.sin_secretos(umbrales::text))
);
create index if not exists accion_iniciada_idx on pricing.accion (iniciada_at);
create index if not exists accion_actor_idx on pricing.accion (actor, iniciada_at);
create index if not exists accion_decision_idx on pricing.accion (decision_id) where decision_id is not null;
create index if not exists accion_deshace_idx on pricing.accion (deshace_id) where deshace_id is not null;
create unique index if not exists accion_process_log_uq on pricing.accion (process_log_id) where process_log_id is not null;
comment on table pricing.accion is
  'LA BITÁCORA: cada acción de pricing de una persona o un proceso. Quién (actor; actor_tipo se deriva: persona, maquina o automatico), cuándo (iniciada_at, terminada_at), desde dónde (origen_app de la sesión: panel, script, claude, cron…; pantalla), qué (tipo; el detalle por publicación en accion_publicacion), por qué (motivo del catálogo + texto, escritos al crear o al cerrar), con qué umbrales y versión de reglas veía la pantalla, para qué decisión y cómo terminó (estado, error). También lo que se decidió NO hacer (revisada_sin_cambio) y las notas, que nacen sin_ejecutar. «error» = no quedó ningún cambio; «parcial» = quedó algún cambio aunque algo falló. registrada_por/registrada_at = quién y cuándo lo anotó (un reclamo de Seller Center: actor es quien lo hizo). No se borra.';
comment on column pricing.accion.actor is
  'Quién actuó: el correo de la sesión (app.usuario, lo pone supabase_db.get_cursor), «automatico» para código o la etiqueta de la llave (servicio). Sin firma ni actor explícito, el INSERT falla.';
comment on column pricing.accion.origen_app is
  'Por dónde entró: el de la sesión (app.origen: panel por el middleware; script, claude o cron por core.actor) o seller_center para un reclamo. Sin firma ni valor explícito, el INSERT falla.';
comment on column pricing.accion.tipo is
  'El editor de PRICING (aplicar) usa siempre editar_precio: un solo aplicar cambia el precio normal, quita y pone el descuento y sale de campañas; el qué está en accion_publicacion. Los tipos de un solo propósito son para pantallas que hacen una sola cosa.';
comment on column pricing.accion.umbrales is
  'Los umbrales de las reglas que tenía la pantalla al actuar (viven en el navegador de cada quien): sin ellos la recomendación no se puede reproducir.';
comment on column pricing.accion.foto_generado is
  'De qué foto de PRICING salieron los datos que se veían (foto.meta.generado): la pantalla no es en vivo.';
comment on column pricing.accion.process_log_id is
  'La fila técnica en ops.process_log (pasos y respuestas de ML) de esta acción; única. Sin llave foránea: es otra bitácora. El relleno desde ops.process_log usa on conflict (process_log_id).';
comment on column pricing.accion.deshace_id is
  'Si tipo = deshacer: la acción que regresa (aplicada o parcial). La original pasa a «deshecha» solo cuando este deshacer quedó aplicado.';
comment on column pricing.accion.reintenta_id is
  'Si es un reintento: la acción que reintenta (que se queda con su error o parcial, sin borrarse).';
alter table pricing.accion enable row level security;
revoke all on pricing.accion from public, anon, authenticated, service_role;
grant select, insert, update on pricing.accion to service_role;
create or replace trigger accion_alta before insert on pricing.accion
  for each row execute function pricing.tg_accion_alta();
create or replace trigger accion_solo_atribuir before update on pricing.accion
  for each row execute function pricing.tg_solo_atribuir('estado', 'terminada_at', 'error', 'process_log_id', 'decision_id', 'motivo', 'motivo_texto');
create or replace trigger accion_estado before update of estado, terminada_at, error, decision_id, motivo, motivo_texto on pricing.accion
  for each row execute function pricing.tg_accion_estado();
drop trigger if exists accion_coherente on pricing.accion;
create constraint trigger accion_coherente after insert or update on pricing.accion
  deferrable initially deferred for each row execute function pricing.tg_accion_coherente();
create or replace trigger accion_sin_borrado before delete on pricing.accion
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger accion_sin_truncate before truncate on pricing.accion
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.accion_publicacion (
    accion_id               bigint         not null,
    publicacion_id          integer        not null,
    hasta_pedido            date,
    precio_base_antes       numeric(12,2),
    precio_cobrado_antes    numeric(12,2),
    precio_base_pedido      numeric(12,2),
    descuento_pct_pedido    numeric(5,4),
    precio_cobrado_pedido   numeric(12,2),
    precio_recomendado      numeric(12,2),
    precio_base_despues     numeric(12,2),
    precio_cobrado_despues  numeric(12,2),
    salir_campanas          boolean,
    recomendacion_clave     text,
    resultado               text           not null default 'pendiente',
    error                   text,
    antes                   jsonb,
    pedido                  jsonb,
    promos_quitadas         jsonb,
    contexto                jsonb,
    constraint accion_publicacion_pkey primary key (accion_id, publicacion_id),
    constraint accion_publicacion_accion_fk foreign key (accion_id) references pricing.accion (id) on delete restrict,
    constraint accion_publicacion_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint accion_publicacion_precios_chk check (
        coalesce(precio_base_antes, 0) >= 0 and coalesce(precio_cobrado_antes, 0) >= 0
        and coalesce(precio_base_pedido, 0) >= 0 and coalesce(precio_cobrado_pedido, 0) >= 0
        and coalesce(precio_recomendado, 0) >= 0
        and coalesce(precio_base_despues, 0) >= 0 and coalesce(precio_cobrado_despues, 0) >= 0),
    constraint accion_publicacion_pct_chk check (descuento_pct_pedido is null or descuento_pct_pedido between 0 and 0.8),
    constraint accion_publicacion_recomendacion_chk check (recomendacion_clave is null or recomendacion_clave ~ '^[a-z_]{2,40}$'),
    constraint accion_publicacion_resultado_chk check (resultado in ('pendiente', 'ok', 'parcial', 'error', 'sin_cambio', 'regresada')),
    constraint accion_publicacion_error_chk check ((resultado not in ('error', 'parcial') or error is not null)
                                                   and char_length(coalesce(error, '')) <= 1000 and pricing.sin_secretos(error)),
    constraint accion_publicacion_antes_chk check (antes is null or (jsonb_typeof(antes) = 'object'
                                                   and octet_length(antes::text) <= 16384 and pricing.sin_secretos(antes::text))),
    constraint accion_publicacion_pedido_chk check (pedido is null or (jsonb_typeof(pedido) = 'object'
                                                    and octet_length(pedido::text) <= 4096 and pricing.sin_secretos(pedido::text))),
    constraint accion_publicacion_promos_chk check (promos_quitadas is null or (jsonb_typeof(promos_quitadas) = 'array'
                                                    and jsonb_array_length(promos_quitadas) <= 20
                                                    and octet_length(promos_quitadas::text) <= 4096
                                                    and pricing.sin_secretos(promos_quitadas::text))),
    constraint accion_publicacion_contexto_chk check (contexto is null or (jsonb_typeof(contexto) = 'object'
                                                      and octet_length(contexto::text) <= 16384
                                                      and not jsonb_path_exists(contexto, 'strict $.* ? (@.type() == "object" || @.type() == "array")', '{}', true)
                                                      and pricing.sin_secretos(contexto::text)))
);
create index if not exists accion_publicacion_pub_idx on pricing.accion_publicacion (publicacion_id, accion_id);
comment on table pricing.accion_publicacion is
  'Por cada publicación que tocó una acción: cómo estaba (antes: la foto completa que necesita deshacer), qué se pidió (precio, descuento, hasta, salir de campañas y, en pedido, lo demás: campaña, categoría, campos de la ficha), qué recomendaba la pantalla (clave y precio), cómo quedó (después) y su resultado. contexto = lo que la persona VEÍA al decidir (objeto plano: conversión, visitas, referencia de competencia, stock, días para agotarse…). Lo de antes, lo pedido, la recomendación y el contexto no se reescriben; el después y el resultado se llenan al terminar y el resultado solo avanza.';
comment on column pricing.accion_publicacion.antes is
  'La publicación antes de actuar, como la leyó el escritor: precio normal, lo que cobraba y sus promociones de precio con id, tipo, precio, inicio y fin. Es lo que «Deshacer» necesita (el precio y la fecha de fin del descuento individual, el deal_price de las campañas).';
comment on column pricing.accion_publicacion.pedido is
  'Lo pedido que no es precio: promocion_id o promo_canal_id, categoria_id, campos de ficha… (objeto, ≤ 4 KB).';
comment on column pricing.accion_publicacion.recomendacion_clave is
  'Qué recomendaba la pantalla para ESTA publicación (la clave de reglas.ts, p. ej. bajar, subir, mantener…) y precio_recomendado su precio: con ellos accion_efecto_v dice si se siguió la recomendación.';
comment on column pricing.accion_publicacion.resultado is
  'pendiente → ok | parcial (quedó algún cambio y algo falló) | error (no quedó nada) | sin_cambio (notas, revisiones) | regresada (falló y se regresó limpio); ok | parcial → regresada cuando un deshacer la regresa.';
comment on column pricing.accion_publicacion.promos_quitadas is
  'Arreglo de {id, tipo, precio} de las campañas de las que se sacó la publicación (C-MLM…, P-MLM…): para volver a meterla y para medir.';
comment on column pricing.accion_publicacion.contexto is
  'Lo que mostraba la pantalla al actuar, como objeto PLANO de valores (p. ej. {"conversion": 0.012, "visitas_30d": 340, "comp_referencia": 649, "stock_full": 12, "dias_agota": 9}). Hasta 16 KB; sin objetos ni arreglos anidados. Nada de compradores ni tokens.';
alter table pricing.accion_publicacion enable row level security;
revoke all on pricing.accion_publicacion from public, anon, authenticated, service_role;
grant select, insert, update on pricing.accion_publicacion to service_role;
create or replace trigger accion_publicacion_alta before insert on pricing.accion_publicacion
  for each row execute function pricing.tg_accion_publicacion_alta();
create or replace trigger accion_publicacion_solo_atribuir before update on pricing.accion_publicacion
  for each row execute function pricing.tg_solo_atribuir('precio_base_despues', 'precio_cobrado_despues', 'resultado', 'error', 'promos_quitadas');
create or replace trigger accion_publicacion_resultado before update of resultado, error on pricing.accion_publicacion
  for each row execute function pricing.tg_accion_publicacion_resultado();
create or replace trigger accion_publicacion_decision after update of resultado on pricing.accion_publicacion
  for each row execute function pricing.tg_accion_publicacion_decision();
drop trigger if exists accion_publicacion_coherente on pricing.accion_publicacion;
create constraint trigger accion_publicacion_coherente after insert or update on pricing.accion_publicacion
  deferrable initially deferred for each row execute function pricing.tg_accion_coherente();
create or replace trigger accion_publicacion_sin_borrado before delete on pricing.accion_publicacion
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger accion_publicacion_sin_truncate before truncate on pricing.accion_publicacion
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 6) pricing.publicacion_sku — publicación ↔ SKU con vigencia
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.publicacion_sku (
    publicacion_id  integer  not null,
    desde           date     not null,
    hasta           date,
    sku             citext   not null,
    variacion_id    text,
    constraint publicacion_sku_pkey primary key (publicacion_id, sku, desde),
    constraint publicacion_sku_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint publicacion_sku_fechas_chk check (hasta is null or hasta >= desde),
    constraint publicacion_sku_sku_chk check (char_length(sku::text) between 1 and 100),
    constraint publicacion_sku_variacion_chk check (char_length(coalesce(variacion_id, '')) <= 40)
);
create unique index if not exists publicacion_sku_vigente_uq on pricing.publicacion_sku (publicacion_id, sku) where hasta is null;
create index if not exists publicacion_sku_sku_idx on pricing.publicacion_sku (sku, desde);
comment on table pricing.publicacion_sku is
  'Qué SKUs tuvo cada publicación y desde cuándo (desde/hasta inclusivos; hasta NULL = sigue). Una publicación con variaciones tiene varios SKUs y un SKU puede estar en varias publicaciones: channel.listings (PK por SKU) lo pisa y aletea. Sin llave a core.products: guarda también SKUs que luego se separan o reciclan.';
alter table pricing.publicacion_sku enable row level security;
revoke all on pricing.publicacion_sku from public, anon, authenticated, service_role;
grant select, insert, update on pricing.publicacion_sku to service_role;
create or replace trigger publicacion_sku_sin_traslape before insert or update of publicacion_id, sku, desde, hasta on pricing.publicacion_sku
  for each row execute function pricing.tg_sin_traslape('publicacion_id', 'sku');
create or replace trigger publicacion_sku_sin_borrado before delete on pricing.publicacion_sku
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger publicacion_sku_sin_truncate before truncate on pricing.publicacion_sku
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 7) pricing.listing_ficha — la ficha por periodos
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.listing_ficha (
    publicacion_id          integer      not null,
    desde                   timestamptz  not null,
    hasta                   timestamptz,
    accion_id               bigint,
    n_fotos                 smallint,
    n_atributos             smallint,
    es_catalogo             boolean,
    tienda_oficial          boolean,
    tiene_video             boolean,
    precio_automatico       boolean,
    categoria_id            text,
    tipo_publicacion        text,
    logistica               text,
    catalog_product_id      text,
    user_product_id         text,
    relacionada_listing_id  text,
    titulo                  text,
    fotos_huella            text,
    huella                  text         not null,
    origen                  text         not null default 'desconocido',
    constraint listing_ficha_pkey primary key (publicacion_id, desde),
    constraint listing_ficha_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint listing_ficha_accion_fk foreign key (accion_id, publicacion_id) references pricing.accion_publicacion (accion_id, publicacion_id) on delete restrict,
    constraint listing_ficha_fechas_chk check (hasta is null or hasta > desde),
    constraint listing_ficha_conteos_chk check (coalesce(n_fotos, 0) >= 0 and coalesce(n_atributos, 0) >= 0),
    constraint listing_ficha_vocabulario_chk check (
        (tipo_publicacion is null or tipo_publicacion ~ '^[A-Za-z_]{1,40}$')
        and (logistica is null or logistica ~ '^[A-Za-z_]{1,40}$')),
    constraint listing_ficha_ids_chk check (
        char_length(coalesce(categoria_id, '')) <= 40 and char_length(coalesce(catalog_product_id, '')) <= 40
        and char_length(coalesce(user_product_id, '')) <= 40 and char_length(coalesce(relacionada_listing_id, '')) <= 100),
    constraint listing_ficha_titulo_chk check (char_length(coalesce(titulo, '')) <= 300),
    constraint listing_ficha_huella_chk check (huella ~ '^[0-9a-f]{32}$'
                                               and (fotos_huella is null or fotos_huella ~ '^[0-9a-f]{32}$')),
    constraint listing_ficha_origen_chk check (origen in ('panel', 'canal_automatico', 'campana_canal', 'externo', 'desconocido')),
    constraint listing_ficha_accion_chk check (origen <> 'panel' or accion_id is not null)
);
create unique index if not exists listing_ficha_vigente_uq on pricing.listing_ficha (publicacion_id) where hasta is null;
comment on table pricing.listing_ficha is
  'La ficha de la publicación por periodos: una fila nueva solo cuando cambia la huella (md5 de los campos). desde = cuándo se vio esta versión por primera vez; hasta = cuándo se vio la siguiente (NULL = vigente). Lo que casi no cambia (categoría, tipo, logística, catálogo) vive aquí y no se repite a diario. origen dice de dónde vino el cambio y accion_id, si lo hizo alguien, quién y por qué (pricing.accion).';
comment on column pricing.listing_ficha.logistica is
  'logistic_type del canal tal cual (ML: fulfillment, xd_drop_off, cross_docking, drop_off, self_service…). FULL = fulfillment.';
comment on column pricing.listing_ficha.tipo_publicacion is
  'listing_type_id del canal (ML: gold_pro = Premium con meses sin intereses, gold_special = Clásica).';
comment on column pricing.listing_ficha.catalog_product_id is
  'Página de catálogo (MLM…) si la publicación está en catálogo. Con user_product_id (MLMU…) empareja el top de enrich.market_highlights_hist, cuyos ids son casi todos páginas.';
alter table pricing.listing_ficha enable row level security;
revoke all on pricing.listing_ficha from public, anon, authenticated, service_role;
grant select, insert, update on pricing.listing_ficha to service_role;
create or replace trigger listing_ficha_solo_atribuir before update on pricing.listing_ficha
  for each row execute function pricing.tg_solo_atribuir('hasta', 'origen', 'accion_id');
create or replace trigger listing_ficha_atribucion before update of origen, accion_id on pricing.listing_ficha
  for each row execute function pricing.tg_atribucion('origen', 'accion_id');
create or replace trigger listing_ficha_sin_borrado before delete on pricing.listing_ficha
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger listing_ficha_sin_truncate before truncate on pricing.listing_ficha
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 8) pricing.precio_obs — cada cambio del precio propio, con hora y quién
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.precio_obs (
    publicacion_id  integer      not null,
    desde           timestamptz  not null,
    desde_min       timestamptz,
    detectado_at    timestamptz  not null default now(),
    accion_id       bigint,
    precio_base     numeric(12,2) not null,
    precio_cobrado  numeric(12,2) not null,
    fuente          text         not null,
    origen          text         not null default 'desconocido',
    constraint precio_obs_pkey primary key (publicacion_id, desde),
    constraint precio_obs_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint precio_obs_accion_fk foreign key (accion_id, publicacion_id) references pricing.accion_publicacion (accion_id, publicacion_id) on delete restrict,
    constraint precio_obs_precios_chk check (precio_base >= 0 and precio_cobrado >= 0),
    constraint precio_obs_ventana_chk check (desde_min is null or desde_min <= desde),
    constraint precio_obs_detectado_chk check (detectado_at >= desde - interval '1 day'),
    constraint precio_obs_fuente_chk check (fuente in ('captura', 'monitor', 'panel', 'aviso_canal', 'relleno')),
    constraint precio_obs_origen_chk check (origen in ('panel', 'canal_automatico', 'campana_canal', 'externo', 'desconocido')),
    constraint precio_obs_accion_chk check ((origen <> 'panel' or accion_id is not null) and (fuente <> 'panel' or origen = 'panel'))
);
create index if not exists precio_obs_accion_idx on pricing.precio_obs (accion_id) where accion_id is not null;
comment on table pricing.precio_obs is
  'La FUENTE CANÓNICA del precio propio por publicación: el estado COMPLETO (base y cobrado, nunca uno solo) cada vez que alguno cambia respecto a la última (la primera fila es el punto de partida). Con hora, fuente y la acción que lo produjo (accion_id). channel.listing_history no sirve para esto: es por SKU y aletea entre publicaciones que comparten SKU. Se puede atribuir después (UPDATE de origen y accion_id: alguien reclama un cambio hecho en Seller Center); lo medido no se reescribe y no se borra.';
comment on column pricing.precio_obs.desde is
  'Mejor estimación de cuándo empezó a regir este precio (aviso del canal, hora del panel o la lectura que lo vio).';
comment on column pricing.precio_obs.desde_min is
  'Cota inferior: la lectura anterior que todavía veía el precio viejo. NULL = la hora es exacta (panel, aviso con hora).';
comment on column pricing.precio_obs.fuente is
  'captura (la foto diaria), monitor (lecturas más seguidas), panel (lo hizo alguien desde OMNICANAL), aviso_canal (webhook de ML), relleno (reconstruido de channel.listing_history).';
alter table pricing.precio_obs enable row level security;
revoke all on pricing.precio_obs from public, anon, authenticated, service_role;
grant select, insert, update on pricing.precio_obs to service_role;
create or replace trigger precio_obs_solo_atribuir before update on pricing.precio_obs
  for each row execute function pricing.tg_solo_atribuir('origen', 'accion_id');
create or replace trigger precio_obs_atribucion before update of origen, accion_id on pricing.precio_obs
  for each row execute function pricing.tg_atribucion('origen', 'accion_id');
create or replace trigger precio_obs_sin_borrado before delete on pricing.precio_obs
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger precio_obs_sin_truncate before truncate on pricing.precio_obs
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 9) pricing.oferta + pricing.oferta_dia — promociones de cada publicación
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.oferta (
    id               integer      generated always as identity,
    publicacion_id   integer      not null,
    promocion_id     integer,
    inicio           timestamptz,
    fin              timestamptz,
    creada_at        timestamptz  not null default now(),
    accion_id        bigint,
    fin_accion_id    bigint,
    tipo             text         not null,
    oferta_canal_id  text,
    inicio_fuente    text         not null default 'canal',
    origen           text         not null default 'desconocido',
    fin_origen       text         not null default 'desconocido',
    constraint oferta_pkey primary key (id),
    constraint oferta_natural_uq unique nulls not distinct (publicacion_id, tipo, promocion_id, inicio),
    constraint oferta_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint oferta_promocion_fk foreign key (promocion_id) references pricing.promocion (id),
    constraint oferta_accion_fk foreign key (accion_id, publicacion_id) references pricing.accion_publicacion (accion_id, publicacion_id) on delete restrict,
    constraint oferta_tipo_chk check (tipo ~ '^[A-Za-z][A-Za-z0-9_]{1,59}$'),
    constraint oferta_fechas_chk check (fin is null or inicio is null or fin >= inicio),
    constraint oferta_inicio_fuente_chk check (inicio_fuente in ('canal', 'lectura')),
    constraint oferta_canal_id_chk check (char_length(coalesce(oferta_canal_id, '')) <= 80),
    constraint oferta_origen_chk check (origen in ('panel', 'canal_automatico', 'campana_canal', 'externo', 'desconocido')),
    constraint oferta_accion_chk check (origen <> 'panel' or accion_id is not null),
    constraint oferta_fin_accion_fk foreign key (fin_accion_id, publicacion_id) references pricing.accion_publicacion (accion_id, publicacion_id) on delete restrict,
    constraint oferta_fin_origen_chk check (fin_origen in ('panel', 'canal_automatico', 'campana_canal', 'externo', 'desconocido')),
    constraint oferta_fin_accion_chk check ((fin_origen <> 'panel' or fin_accion_id is not null) and (fin_accion_id is null or fin is not null))
);
create index if not exists oferta_promocion_idx on pricing.oferta (promocion_id) where promocion_id is not null;
create index if not exists oferta_accion_idx on pricing.oferta (accion_id) where accion_id is not null;
create index if not exists oferta_fin_accion_idx on pricing.oferta (fin_accion_id) where fin_accion_id is not null;
comment on table pricing.oferta is
  'UNA publicación dentro de una promoción, o su descuento propio, con las fechas de ESA publicación (start_date/finish_date de /seller-promotions/items/{id}): un relámpago de horas o un descuento programado junto a uno activo son ofertas distintas. Sustituye a la fila sintética PRICE_DISCOUNT de la v1. origen/accion_id dicen de dónde vino el ALTA y fin_origen/fin_accion_id quién la TERMINÓ (sacarla de una campaña, quitar un descuento). La atribución solo se llena.';
comment on column pricing.oferta.promocion_id is
  'La campaña con id del canal. NULL = el canal no le da id (PRICE_DISCOUNT siempre; a veces LIGHTNING). No hay CHECK que lo exija por tipo: un tipo nuevo no debe tumbar la captura.';
comment on column pricing.oferta.inicio_fuente is
  'canal = inicio/fin vienen del canal; lectura = el canal no los dio y se usó la hora de la lectura.';
alter table pricing.oferta enable row level security;
revoke all on pricing.oferta from public, anon, authenticated, service_role;
grant select, insert, update on pricing.oferta to service_role;
create or replace trigger oferta_tipo before insert or update of promocion_id, tipo on pricing.oferta
  for each row execute function pricing.tg_oferta_tipo();
create or replace trigger oferta_llave_inmutable before update of publicacion_id on pricing.oferta
  for each row when (old.publicacion_id is distinct from new.publicacion_id) execute function pricing.tg_llave_inmutable();
create or replace trigger oferta_atribucion before update of origen, accion_id, fin_origen, fin_accion_id on pricing.oferta
  for each row execute function pricing.tg_atribucion('origen', 'accion_id', 'fin_origen', 'fin_accion_id');
create or replace trigger oferta_sin_borrado before delete on pricing.oferta
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger oferta_sin_truncate before truncate on pricing.oferta
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.oferta_dia (
    oferta_id     integer        not null,
    fecha         date           not null,
    captura_id    integer        not null,
    precio        numeric(12,2),
    meli_pct      numeric(5,4),
    vendedor_pct  numeric(5,4),
    estado        text           not null,
    constraint oferta_dia_pkey primary key (oferta_id, fecha),
    constraint oferta_dia_oferta_fk foreign key (oferta_id) references pricing.oferta (id),
    constraint oferta_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint oferta_dia_estado_chk check (estado in ('started', 'pending')),
    constraint oferta_dia_precio_chk check (precio is null or precio >= 0),
    constraint oferta_dia_pct_chk check ((meli_pct is null or meli_pct between 0 and 1)
                                         and (vendedor_pct is null or vendedor_pct between 0 and 1))
) with (autovacuum_vacuum_insert_scale_factor = 0.02);
create index if not exists oferta_dia_fecha_brin on pricing.oferta_dia using brin (fecha) with (autosummarize = on);
comment on table pricing.oferta_dia is
  'Cada oferta vista ese día (solo activas o programadas: las candidatas son cientos) con su precio y quién la financia. SOLO AGREGAR Y COMPLETAR: una fila por oferta y día; una segunda pasada solo llena NULL. Qué oferta COBRÓ no vive aquí: es listing_dia.oferta_cobra_id (una sola fuente). Verificar con una publicación con variaciones si ML da precio por variación; si sí, agregar variacion_id (otra migración).';
comment on column pricing.oferta_dia.meli_pct is
  'Parte del precio de lista que pone el canal (0..1; ML manda meli_percentage en 0..100). El aporte en pesos = precio_base × meli_pct y NO es dinero nuestro.';
comment on column pricing.oferta_dia.precio is
  'Precio de la oferta en esta publicación ese día. En los cupones es el precio con cupón.';
alter table pricing.oferta_dia enable row level security;
revoke all on pricing.oferta_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.oferta_dia to service_role;
create or replace trigger oferta_dia_solo_completar before update on pricing.oferta_dia
  for each row execute function pricing.tg_solo_completar();
create or replace trigger oferta_dia_solo_agregar before delete on pricing.oferta_dia
  for each row execute function ops.tg_solo_agregar();
create or replace trigger oferta_dia_captura_coherente before insert or update of captura_id, fecha on pricing.oferta_dia
  for each row execute function pricing.tg_captura_coherente('foto', 'oferta');
create or replace trigger oferta_dia_sin_truncate before truncate on pricing.oferta_dia
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 10) pricing.listing_dia — LA FOTO del día (solo agregar)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.listing_dia (
    fecha                date           not null,
    publicacion_id       integer        not null,
    captura_id           integer        not null,
    oferta_cobra_id      integer,
    stock_disponible     integer,
    vendidos_acumulado   integer,
    precio_base          numeric(12,2),
    precio_cobrado       numeric(12,2),
    buybox_precio_ganar  numeric(12,2),
    envio_gratis         boolean,
    estado               text,
    sub_estado           text[],
    cobra_estado         text,
    buybox_estado        text,
    visit_share          text,
    constraint listing_dia_pkey primary key (publicacion_id, fecha),
    constraint listing_dia_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint listing_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    -- la oferta que cobra se vio ESTE día (que sea de ESTA publicación y estuviera
    -- started lo revisa el trigger listing_dia_cobra_iniciada)
    constraint listing_dia_cobra_dia_fk foreign key (oferta_cobra_id, fecha)
        references pricing.oferta_dia (oferta_id, fecha),
    constraint listing_dia_precios_chk check (coalesce(precio_base, 0) >= 0 and coalesce(precio_cobrado, 0) >= 0
                                              and coalesce(buybox_precio_ganar, 0) >= 0),
    constraint listing_dia_conteos_chk check (coalesce(stock_disponible, 0) >= 0 and coalesce(vendidos_acumulado, 0) >= 0),
    constraint listing_dia_cobra_chk check (
           (cobra_estado is null and oferta_cobra_id is null)
        or (cobra_estado in ('ninguna', 'no_identificada') and oferta_cobra_id is null)
        or (cobra_estado = 'identificada' and oferta_cobra_id is not null)),
    constraint listing_dia_vocabulario_chk check (
        (estado is null or estado ~ '^[a-z_]{1,40}$')
        and (buybox_estado is null or buybox_estado ~ '^[a-z_]{1,40}$')
        and (visit_share is null or visit_share ~ '^[a-z_]{1,20}$')
        and (sub_estado is null or cardinality(sub_estado) <= 10))
) with (autovacuum_vacuum_insert_scale_factor = 0.02);
create index if not exists listing_dia_fecha_brin on pricing.listing_dia using brin (fecha)
    with (pages_per_range = 32, autosummarize = on);
-- Sirve a la llave de la oferta que cobra (sin él, una purga con acta de
-- ofertas recorrería listing_dia completa por cada una).
create index if not exists listing_dia_cobra_idx on pricing.listing_dia (oferta_cobra_id, fecha)
    where oferta_cobra_id is not null;
comment on table pricing.listing_dia is
  'LA FOTO del día: una fila por publicación y día de México, de SOLO AGREGAR Y COMPLETAR (una captura a hora fija; una segunda pasada del mismo día solo llena NULL). NULL = no se midió. Precios en MXN por pieza. Lo que se puede derivar (ventas, calidad, competencia, costo) NO está aquí: lo arma pricing.panel_v.';
comment on column pricing.listing_dia.precio_base is
  'item.price de ML en la captura, siempre que se midió (no es NULL por no haber promoción). Subirlo borra los descuentos activos en ML.';
comment on column pricing.listing_dia.precio_cobrado is
  'Lo que pagaba el comprador sin cupón a la hora de la captura (sale_price de ML). Los cambios con su hora están en pricing.precio_obs.';
comment on column pricing.listing_dia.cobra_estado is
  'ninguna (no cobraba ninguna oferta) | identificada (oferta_cobra_id dice cuál) | no_identificada (cobraba una que no se pudo emparejar). NULL = no se midió.';
comment on column pricing.listing_dia.oferta_cobra_id is
  'La oferta que puso precio_cobrado (ML cobra la más baja activa). Tiene que estar started ese día en oferta_dia (trigger listing_dia_cobra_iniciada).';
comment on column pricing.listing_dia.stock_disponible is
  'available_quantity de la publicación en la captura: SUMA de sus variaciones. El stock por SKU está en channel.listing_history (hoy stock_own es el espejo de Woo y esa fuente cambiará con la salida de Woo y Odoo).';
comment on column pricing.listing_dia.vendidos_acumulado is
  'sold_quantity de ML en la captura (lo que la ficha muestra como «+100 vendidos»): prueba social.';
comment on column pricing.listing_dia.envio_gratis is
  'shipping.free_shipping en la captura. En MLM depende del precio (umbral en pricing.parametro_canal) y de la logística.';
comment on column pricing.listing_dia.buybox_estado is
  'status de /price_to_win (winning, sharing_first_place, competing, listed…), solo en catálogo.';
comment on column pricing.listing_dia.visit_share is
  'visit_share de /price_to_win: una categoría (maximum, medium, minimum), no una cifra.';
alter table pricing.listing_dia enable row level security;
revoke all on pricing.listing_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.listing_dia to service_role;
create or replace trigger listing_dia_cobra_iniciada before insert or update of oferta_cobra_id on pricing.listing_dia
  for each row execute function pricing.tg_cobra_iniciada();
create or replace trigger listing_dia_solo_completar before update on pricing.listing_dia
  for each row execute function pricing.tg_solo_completar();
create or replace trigger listing_dia_solo_agregar before delete on pricing.listing_dia
  for each row execute function ops.tg_solo_agregar();
create or replace trigger listing_dia_captura_coherente before insert or update of captura_id, fecha on pricing.listing_dia
  for each row execute function pricing.tg_captura_coherente('foto', 'publicacion');
create or replace trigger listing_dia_sin_truncate before truncate on pricing.listing_dia
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 11) pricing.resultado_dia — visitas y preguntas (llegan tarde)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.resultado_dia (
    publicacion_id           integer      not null,
    fecha                    date         not null,
    visitas                  integer,
    captura_id               integer      not null,
    leido_at                 timestamptz  not null default now(),
    preguntas_recibidas      smallint,
    preguntas_sin_responder  smallint,
    visitas_completa         boolean      not null default false,
    constraint resultado_dia_pkey primary key (publicacion_id, fecha),
    constraint resultado_dia_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint resultado_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint resultado_dia_conteos_chk check (coalesce(visitas, 0) >= 0 and coalesce(preguntas_recibidas, 0) >= 0
                                                and coalesce(preguntas_sin_responder, 0) >= 0)
) with (fillfactor = 85, autovacuum_vacuum_scale_factor = 0.02);
comment on table pricing.resultado_dia is
  'Resultados que no viven en ningún otro lado y llegan tarde: visitas y preguntas por publicación y día. Se reescriben hasta que visitas_completa = true (ML consolida en 24-48 h). El upsert compara TODAS las columnas que actualiza (where (…) is distinct from (…)) para no crear versiones muertas. Sin llave a listing_dia: puede haber resultados de días sin foto. Ventas, devoluciones y comisión NO van aquí: se derivan de channel.*.';
comment on column pricing.resultado_dia.visitas is
  'Visitas del día según /items/{id}/visits/time_window. El día es la etiqueta del canal: verificar UNA vez si es UTC o México y anotarlo aquí. NULL = no se midió.';
comment on column pricing.resultado_dia.visitas_completa is
  'true = el día ya cerró en ML y el número no va a cambiar; false = parcial (día en curso o sin consolidar).';
alter table pricing.resultado_dia enable row level security;
revoke all on pricing.resultado_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.resultado_dia to service_role;
create or replace trigger resultado_dia_captura_coherente before insert or update of captura_id, fecha on pricing.resultado_dia
  for each row execute function pricing.tg_captura_coherente('resultado', 'publicacion');
create or replace trigger resultado_dia_llave_inmutable before update of publicacion_id, fecha on pricing.resultado_dia
  for each row execute function pricing.tg_llave_inmutable();
create or replace trigger resultado_dia_cerrado before update on pricing.resultado_dia
  for each row execute function pricing.tg_resultado_cerrado();
create or replace trigger resultado_dia_sin_borrado before delete on pricing.resultado_dia
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger resultado_dia_sin_truncate before truncate on pricing.resultado_dia
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 11b) pricing.disponibilidad_dia — horas con stock vendible (al cerrar el día)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.disponibilidad_dia (
    publicacion_id    integer       not null,
    fecha             date          not null,
    captura_id        integer       not null,
    horas_disponible  numeric(4,2),
    constraint disponibilidad_dia_pkey primary key (publicacion_id, fecha),
    constraint disponibilidad_dia_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint disponibilidad_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint disponibilidad_dia_horas_chk check (horas_disponible is null or horas_disponible between 0 and 24)
) with (autovacuum_vacuum_insert_scale_factor = 0.02);
comment on table pricing.disponibilidad_dia is
  'Cuántas horas del día estuvo vendible cada publicación. Otro hecho que las visitas: sale de channel.listing_history, lo calcula otro proceso y solo cuando el día ya cerró. SOLO AGREGAR Y COMPLETAR.';
comment on column pricing.disponibilidad_dia.horas_disponible is
  'Horas del día (0..24) con estado active y stock vendible > 0 según la logística (FULL: stock_full; propia: stock_own), reconstruidas desde channel.listing_history (15 min) DESPUÉS de que el día cerró. Con variaciones cuenta la hora si al menos una variante tenía stock. NULL = no se pudo reconstruir. La demanda de un día con < 23.5 h está censurada.';
alter table pricing.disponibilidad_dia enable row level security;
revoke all on pricing.disponibilidad_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.disponibilidad_dia to service_role;
create or replace trigger disponibilidad_dia_solo_completar before update on pricing.disponibilidad_dia
  for each row execute function pricing.tg_solo_completar();
create or replace trigger disponibilidad_dia_captura_coherente before insert or update of captura_id, fecha on pricing.disponibilidad_dia
  for each row execute function pricing.tg_captura_coherente('disponibilidad', 'publicacion');
create or replace trigger disponibilidad_dia_solo_agregar before delete on pricing.disponibilidad_dia
  for each row execute function ops.tg_solo_agregar();
create or replace trigger disponibilidad_dia_sin_truncate before truncate on pricing.disponibilidad_dia
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 12) pricing.ads_dia — Product Ads (solo cuando los hubo)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.ads_dia (
    publicacion_id  integer        not null,
    fecha           date           not null,
    impresiones     integer,
    clics           integer,
    captura_id      integer        not null,
    costo           numeric(12,2),
    campana_id      text           not null,
    constraint ads_dia_pkey primary key (publicacion_id, fecha, campana_id),
    constraint ads_dia_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint ads_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint ads_dia_conteos_chk check (coalesce(impresiones, 0) >= 0 and coalesce(clics, 0) >= 0 and coalesce(costo, 0) >= 0),
    constraint ads_dia_campana_chk check (char_length(campana_id) between 1 and 80)
) with (fillfactor = 90);
comment on table pricing.ads_dia is
  'Product Ads por publicación, día y campaña: una fila solo si hubo anuncios. Sin fila + captura de ads terminada ese día (pricing.captura grupo ads) = cero; sin captura = no se midió.';
alter table pricing.ads_dia enable row level security;
revoke all on pricing.ads_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.ads_dia to service_role;
create or replace trigger ads_dia_captura_coherente before insert or update of captura_id, fecha on pricing.ads_dia
  for each row execute function pricing.tg_captura_coherente('ads', 'publicacion');
create or replace trigger ads_dia_sin_borrado before delete on pricing.ads_dia
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger ads_dia_sin_truncate before truncate on pricing.ads_dia
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 13) Competencia: pricing.rival, pricing.rival_precio, pricing.listing_rival
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.rival (
    id                integer      generated always as identity,
    visto_primero_at  timestamptz  not null default now(),
    canal             text         not null,
    item_id           text         not null,
    vendedor_id       text,
    constraint rival_pkey primary key (id),
    constraint rival_item_uq unique (canal, item_id),
    constraint rival_canal_fk foreign key (canal) references core.channels (id),
    constraint rival_ids_chk check (char_length(item_id) between 1 and 100 and char_length(coalesce(vendedor_id, '')) <= 40)
);
comment on table pricing.rival is
  'Cada publicación con la que se compara alguna nuestra (un rival compite con varias de las nuestras: se guarda UNA vez). Si es nuestra se DERIVA por item_id (channel.listings, pricing.publicacion) y de respaldo por vendedor_id = core.accounts.external_id: no se guarda una bandera que pueda contradecirlo.';
alter table pricing.rival enable row level security;
revoke all on pricing.rival from public, anon, authenticated, service_role;
grant select, insert, update on pricing.rival to service_role;
create or replace trigger rival_llave_inmutable before update of canal, item_id on pricing.rival
  for each row execute function pricing.tg_llave_inmutable();
create or replace trigger rival_sin_borrado before delete on pricing.rival
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger rival_sin_truncate before truncate on pricing.rival
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.rival_precio (
    rival_id      integer        not null,
    desde         date           not null,
    hasta         date,
    visto_hasta   date           not null,
    captura_id    integer,
    precio        numeric(12,2)  not null,
    precio_lista  numeric(12,2),
    envio_gratis  boolean,
    premium       boolean,
    es_full       boolean,
    gana_buybox   boolean,
    fuente        text           not null,
    constraint rival_precio_pkey primary key (rival_id, desde),
    constraint rival_precio_rival_fk foreign key (rival_id) references pricing.rival (id),
    constraint rival_precio_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint rival_precio_fechas_chk check ((hasta is null or hasta >= desde)
                                              and visto_hasta >= desde and (hasta is null or visto_hasta <= hasta)),
    constraint rival_precio_precios_chk check (precio >= 0 and coalesce(precio_lista, 0) >= 0),
    constraint rival_precio_fuente_chk check (fuente in ('catalogo', 'busqueda', 'revision_fotos', 'manual'))
) with (fillfactor = 90);
create unique index if not exists rival_precio_abierto_uq on pricing.rival_precio (rival_id) where hasta is null;
comment on table pricing.rival_precio is
  'El precio de cada rival por tramos: una fila nueva solo cuando cambia (precio, lista, envío gratis, premium, FULL o buybox). desde/hasta inclusivos; hasta NULL = sigue. visto_hasta = último día en que se vio (el escritor lo avanza: update HOT, no está en ningún índice). Un día después de visto_hasta y antes de hasta es NO MEDIDO, nunca «sigue igual».';
comment on column pricing.rival_precio.premium is
  'Publicación premium (en ML: meses sin intereses, gold_pro).';
alter table pricing.rival_precio enable row level security;
revoke all on pricing.rival_precio from public, anon, authenticated, service_role;
grant select, insert, update on pricing.rival_precio to service_role;
create or replace trigger rival_precio_sin_traslape before insert or update of rival_id, desde, hasta on pricing.rival_precio
  for each row execute function pricing.tg_sin_traslape('rival_id');
create or replace trigger rival_precio_sin_borrado before delete on pricing.rival_precio
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger rival_precio_sin_truncate before truncate on pricing.rival_precio
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.listing_rival (
    publicacion_id  integer  not null,
    rival_id        integer  not null,
    desde           date     not null,
    hasta           date,
    fuente          text     not null,
    clase           text,
    version_juez    text,
    constraint listing_rival_pkey primary key (publicacion_id, rival_id, desde),
    constraint listing_rival_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint listing_rival_rival_fk foreign key (rival_id) references pricing.rival (id),
    constraint listing_rival_fechas_chk check (hasta is null or hasta >= desde),
    constraint listing_rival_fuente_chk check (fuente in ('catalogo', 'busqueda', 'revision_fotos', 'manual')),
    constraint listing_rival_clase_chk check ((clase is null or clase ~ '^[a-z_]{2,30}$')
                                              and char_length(coalesce(version_juez, '')) <= 80)
);
create unique index if not exists listing_rival_abierto_uq on pricing.listing_rival (publicacion_id, rival_id) where hasta is null;
create index if not exists listing_rival_rival_idx on pricing.listing_rival (rival_id);
comment on table pricing.listing_rival is
  'Con qué rivales se comparó cada publicación, por periodos (inclusivos; hasta NULL = sigue). Si el juez cambia el veredicto se cierra el tramo y se abre otro: así la competencia de cualquier día se puede RECALCULAR con otra regla.';
comment on column pricing.listing_rival.clase is
  'FOTO del veredicto de enrich.market_rival_juicio en el tramo (mismo, otro_paquete, otra_gama, refaccion, otro_producto, dudoso). Solo «mismo» cuenta como comparable. NULL = sin juzgar.';
alter table pricing.listing_rival enable row level security;
revoke all on pricing.listing_rival from public, anon, authenticated, service_role;
grant select, insert, update on pricing.listing_rival to service_role;
create or replace trigger listing_rival_sin_traslape before insert or update of publicacion_id, rival_id, desde, hasta on pricing.listing_rival
  for each row execute function pricing.tg_sin_traslape('publicacion_id', 'rival_id');
create or replace trigger listing_rival_sin_borrado before delete on pricing.listing_rival
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger listing_rival_sin_truncate before truncate on pricing.listing_rival
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 14) pricing.seguimiento — lo que se sigue a mano o como control
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.seguimiento (
    publicacion_id  integer      not null,
    desde           date         not null default (now() at time zone 'America/Mexico_City')::date,
    hasta           date,
    creado_at       timestamptz  not null default now(),
    motivo          text         not null,
    autor           text         default nullif(current_setting('app.usuario', true), ''),
    nota            text,
    constraint seguimiento_pkey primary key (publicacion_id, motivo, desde),
    constraint seguimiento_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint seguimiento_motivo_chk check (motivo in ('control', 'control_no_full', 'manual')),
    constraint seguimiento_fechas_chk check (hasta is null or hasta >= desde),
    constraint seguimiento_textos_chk check (char_length(coalesce(nota, '')) <= 1000 and char_length(coalesce(autor, '')) <= 120
                                             and pricing.sin_secretos(nota))
);
create unique index if not exists seguimiento_abierto_uq on pricing.seguimiento (publicacion_id, motivo) where hasta is null;
comment on table pricing.seguimiento is
  'Lo que se sigue por decisión de alguien, por periodos (una publicación puede salir y volver; cada periodo queda). full_activo y modificado NO se guardan: se derivan (ver seguimiento_hoy_v y panel_v.es_full). control_no_full = muestra aleatoria de publicaciones no FULL sin tocar: semilla y fecha en nota. Con esto se puede corregir el sesgo de selección de la muestra.';
alter table pricing.seguimiento enable row level security;
revoke all on pricing.seguimiento from public, anon, authenticated, service_role;
grant select, insert, update on pricing.seguimiento to service_role;
create or replace trigger seguimiento_sin_traslape before insert or update of publicacion_id, motivo, desde, hasta on pricing.seguimiento
  for each row execute function pricing.tg_sin_traslape('publicacion_id', 'motivo');
create or replace trigger seguimiento_sin_borrado before delete on pricing.seguimiento
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger seguimiento_sin_truncate before truncate on pricing.seguimiento
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 15) pricing.evento — lo que ninguna otra tabla registra (solo agregar)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.evento (
    id              bigint       generated always as identity,
    publicacion_id  integer      not null,
    ocurrio_at      timestamptz  not null,
    ocurrio_desde   timestamptz,
    detectado_at    timestamptz  not null default now(),
    accion_id       bigint,
    es_relleno      boolean      not null default false,
    tipo            text         not null,
    origen          text         not null default 'desconocido',
    huella          text         not null,
    descripcion     text,
    antes           jsonb,
    despues         jsonb,
    evidencia       jsonb,
    constraint evento_pkey primary key (id),
    constraint evento_huella_uq unique (publicacion_id, huella),
    constraint evento_publicacion_fk foreign key (publicacion_id) references pricing.publicacion (id),
    constraint evento_accion_fk foreign key (accion_id, publicacion_id) references pricing.accion_publicacion (accion_id, publicacion_id) on delete restrict,
    constraint evento_tipo_chk check (tipo in ('descripcion', 'otro')),
    constraint evento_otro_chk check (tipo <> 'otro' or descripcion is not null),
    constraint evento_origen_chk check (origen in ('panel', 'canal_automatico', 'campana_canal', 'externo', 'desconocido')),
    constraint evento_accion_chk check (origen <> 'panel' or accion_id is not null),
    constraint evento_fechas_chk check ((ocurrio_desde is null or ocurrio_desde <= ocurrio_at)
                                        and detectado_at >= ocurrio_at - interval '1 day'),
    constraint evento_huella_chk check (huella ~ '^[0-9a-f]{32}$'),
    constraint evento_textos_chk check (char_length(coalesce(descripcion, '')) <= 1000),
    constraint evento_jsonb_chk check (octet_length(coalesce(antes::text, '')) <= case when tipo = 'descripcion' then 262144 else 32768 end
                                       and octet_length(coalesce(despues::text, '')) <= case when tipo = 'descripcion' then 262144 else 32768 end
                                       and (evidencia is null or (jsonb_typeof(evidencia) = 'object'
                                                                  and octet_length(evidencia::text) <= 32768))),
    constraint evento_limpio_chk check (pricing.sin_secretos(antes::text) and pricing.sin_secretos(despues::text)
                                        and pricing.sin_secretos(evidencia::text) and pricing.sin_secretos(descripcion))
);
create index if not exists evento_pub_idx on pricing.evento (publicacion_id, ocurrio_at);
create index if not exists evento_accion_idx on pricing.evento (accion_id) where accion_id is not null;
comment on table pricing.evento is
  'Solo lo que ninguna otra tabla registra (cambio de descripción, otro), con su hora y, si lo hizo alguien, su acción. El precio vive en precio_obs, las promociones en oferta, la ficha en listing_ficha y el estado/stock en listing_dia; «Qué cambió» los junta en pricing.cambio_v. SOLO AGREGAR. huella = md5(tipo | antes | despues | día) evita el mismo cambio visto por dos vías.';
comment on column pricing.evento.ocurrio_desde is
  'Cota inferior (la lectura anterior). NULL = ocurrio_at es exacta.';
comment on column pricing.evento.antes is
  'Lo de antes. Para una descripción conviene {md5, longitud} (la huella ya es md5); si se guarda el texto, hasta 256 KB (ML admite 50,000 caracteres).';
comment on column pricing.evento.evidencia is
  'Solo ids (publicación, promoción), status y tipo de error. NUNCA la respuesta cruda de /orders ni el str() de un error de httpx.';
alter table pricing.evento enable row level security;
revoke all on pricing.evento from public, anon, authenticated, service_role;
grant select, insert on pricing.evento to service_role;
create or replace trigger evento_solo_agregar before update or delete on pricing.evento
  for each row execute function ops.tg_solo_agregar();
create or replace trigger evento_sin_truncate before truncate on pricing.evento
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 16) pricing.cuenta_dia — la reputación de la cuenta (solo agregar)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.cuenta_dia (
    account_id          uuid          not null,
    fecha               date          not null,
    captura_id          integer       not null,
    ventas_60d          integer,
    tasa_reclamos       numeric(6,4),
    tasa_cancelaciones  numeric(6,4),
    tasa_demoras        numeric(6,4),
    en_vacaciones       boolean,
    reputacion_nivel    text,
    lider               text,
    restricciones       jsonb,
    constraint cuenta_dia_pkey primary key (account_id, fecha),
    constraint cuenta_dia_cuenta_fk foreign key (account_id) references core.accounts (id),
    constraint cuenta_dia_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint cuenta_dia_tasas_chk check (coalesce(tasa_reclamos, 0) between 0 and 1
                                           and coalesce(tasa_cancelaciones, 0) between 0 and 1
                                           and coalesce(tasa_demoras, 0) between 0 and 1
                                           and coalesce(ventas_60d, 0) >= 0),
    constraint cuenta_dia_vocabulario_chk check ((lider is null or lider ~ '^[a-z_]{2,30}$')
                                                 and (reputacion_nivel is null or reputacion_nivel ~ '^[0-9a-z_]{1,40}$')),
    constraint cuenta_dia_restricciones_chk check (restricciones is null or (jsonb_typeof(restricciones) in ('object', 'array')
                                                   and octet_length(restricciones::text) <= 8192
                                                   and pricing.sin_secretos(restricciones::text)))
);
comment on table pricing.cuenta_dia is
  'Reputación de la cuenta por día (ML /users/{id}: seller_reputation). Afecta a todas sus publicaciones; con dos cuentas casi no varía y queda colineal con el efecto fijo de la cuenta. SOLO AGREGAR Y COMPLETAR. El canal sale de core.accounts.';
comment on column pricing.cuenta_dia.lider is
  'power_seller_status de ML: platinum | gold | silver | ninguno (sin medalla, medido). NULL = no se midió.';
comment on column pricing.cuenta_dia.tasa_reclamos is
  'Las tasas son la VENTANA MÓVIL de ML (≈60 días), no del día.';
alter table pricing.cuenta_dia enable row level security;
revoke all on pricing.cuenta_dia from public, anon, authenticated, service_role;
grant select, insert, update on pricing.cuenta_dia to service_role;
create or replace trigger cuenta_dia_solo_completar before update on pricing.cuenta_dia
  for each row execute function pricing.tg_solo_completar();
create or replace trigger cuenta_dia_solo_agregar before delete on pricing.cuenta_dia
  for each row execute function ops.tg_solo_agregar();
create or replace trigger cuenta_dia_captura_coherente before insert or update of captura_id, fecha on pricing.cuenta_dia
  for each row execute function pricing.tg_captura_coherente('cuenta', 'cuenta');
create or replace trigger cuenta_dia_sin_truncate before truncate on pricing.cuenta_dia
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 17) pricing.calendario + pricing.parametro_canal
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.calendario (
    id          integer   generated always as identity,
    desde       date      not null,
    hasta       date      not null,
    intensidad  smallint,
    canal       text,
    tipo        text      not null,
    nombre      text      not null,
    nota        text,
    constraint calendario_pkey primary key (id),
    constraint calendario_uq unique nulls not distinct (tipo, nombre, canal, desde),
    constraint calendario_canal_fk foreign key (canal) references core.channels (id),
    constraint calendario_rango_chk check (hasta >= desde),
    constraint calendario_tipo_chk check (tipo in ('campana_canal', 'festivo', 'temporada', 'financiamiento', 'interno')),
    constraint calendario_intensidad_chk check (intensidad is null or intensidad between 1 and 3),
    constraint calendario_textos_chk check (char_length(nombre) between 1 and 120 and char_length(coalesce(nota, '')) <= 1000
                                            and pricing.sin_secretos(nota))
);
create index if not exists calendario_rango_idx on pricing.calendario (desde, hasta);
comment on table pricing.calendario is
  'Eventos que mueven la demanda, UNA fila por evento con su rango (desde/hasta inclusivos). canal NULL = todos. La quincena y el día de la semana NO se guardan: salen de la fecha en panel_v. financiamiento = campañas bancarias de meses sin intereses. Si entra la 0062, unificar con market.evento: un solo calendario.';
alter table pricing.calendario enable row level security;
revoke all on pricing.calendario from public, anon, authenticated, service_role;
grant select, insert, update, delete on pricing.calendario to service_role;
create or replace trigger calendario_sin_truncate before truncate on pricing.calendario
  for each statement execute function ops.tg_sin_truncate();

create table if not exists pricing.parametro_canal (
    canal  text     not null,
    clave  text     not null,
    desde  date     not null,
    hasta  date,
    valor  numeric  not null,
    nota   text,
    constraint parametro_canal_pkey primary key (canal, clave, desde),
    constraint parametro_canal_canal_fk foreign key (canal) references core.channels (id),
    constraint parametro_canal_fechas_chk check (hasta is null or hasta >= desde),
    constraint parametro_canal_clave_chk check (clave ~ '^[a-z][a-z0-9_]{1,59}$'),
    constraint parametro_canal_nota_chk check (char_length(coalesce(nota, '')) <= 1000 and pricing.sin_secretos(nota))
);
create unique index if not exists parametro_canal_vigente_uq on pricing.parametro_canal (canal, clave) where hasta is null;
comment on table pricing.parametro_canal is
  'Reglas del canal con vigencia (desde/hasta inclusivos). Hoy: umbral_envio_gratis (MXN; en MLM, debajo del umbral paga el comprador, y el precio efectivo da un salto).';
alter table pricing.parametro_canal enable row level security;
revoke all on pricing.parametro_canal from public, anon, authenticated, service_role;
grant select, insert, update on pricing.parametro_canal to service_role;
create or replace trigger parametro_canal_sin_traslape before insert or update of canal, clave, desde, hasta on pricing.parametro_canal
  for each row execute function pricing.tg_sin_traslape('canal', 'clave');
create or replace trigger parametro_canal_sin_borrado before delete on pricing.parametro_canal
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger parametro_canal_sin_truncate before truncate on pricing.parametro_canal
  for each statement execute function ops.tg_sin_truncate();

-- ───────────────────────────────────────────────────────────────────────────
-- 18) pricing.modelo_corrida + pricing.modelo_coef (solo agregar)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.modelo_corrida (
    id               bigint       generated always as identity,
    creada_at        timestamptz  not null default now(),
    datos_corte_at   timestamptz,
    n                integer,
    r2               numeric,
    autor            text         not null default nullif(current_setting('app.usuario', true), ''),
    especificacion   text         not null,
    muestra_hash     text         not null,
    muestra_ruta     text,
    codigo_version   text,
    notas            text,
    muestra          jsonb        not null,
    metricas         jsonb,
    constraint modelo_corrida_pkey primary key (id),
    constraint modelo_corrida_n_chk check (n is null or n >= 0),
    constraint modelo_corrida_r2_chk check (r2 is null or r2 <= 1),
    constraint modelo_corrida_autor_chk check (char_length(autor) between 2 and 120),
    constraint modelo_corrida_textos_chk check (char_length(especificacion) between 1 and 8000
                                                and char_length(coalesce(notas, '')) <= 4000
                                                and char_length(coalesce(muestra_ruta, '')) <= 500
                                                and char_length(coalesce(codigo_version, '')) <= 80),
    constraint modelo_corrida_hash_chk check (muestra_hash ~ '^[0-9a-f]{32,128}$'),
    constraint modelo_corrida_jsonb_chk check (jsonb_typeof(muestra) = 'object' and octet_length(muestra::text) <= 1048576
                                               and (metricas is null or (jsonb_typeof(metricas) = 'object'
                                                                         and octet_length(metricas::text) <= 1048576))),
    constraint modelo_corrida_limpia_chk check (pricing.sin_secretos(muestra::text) and pricing.sin_secretos(metricas::text)
                                                and pricing.sin_secretos(notas) and pricing.sin_secretos(muestra_ruta))
);
comment on table pricing.modelo_corrida is
  'Cada regresión corrida sobre el panel: fórmula (especificacion), filtros (muestra), y la muestra EXACTA (muestra_hash + muestra_ruta: Parquet/CSV en un bucket PRIVADO de Storage). El panel se deriva de datos vivos (cancelaciones tardías, costos corregidos, reventa), así que sin el export la corrida no se puede repetir. Los coeficientes van en pricing.modelo_coef. SOLO AGREGAR.';
comment on column pricing.modelo_corrida.datos_corte_at is
  'Informativo: hasta qué captura entró. La foto no se reescribe (solo agregar), pero las ventas de channel.* sí cambian: por eso manda el export.';
comment on column pricing.modelo_corrida.r2 is
  'R² (≤ 1). Fuera de muestra puede ser muy negativo: sin cota inferior.';
alter table pricing.modelo_corrida enable row level security;
revoke all on pricing.modelo_corrida from public, anon, authenticated, service_role;
grant select, insert on pricing.modelo_corrida to service_role;
create or replace trigger modelo_corrida_solo_agregar before update or delete on pricing.modelo_corrida
  for each row execute function ops.tg_solo_agregar();
create or replace trigger modelo_corrida_sin_truncate before truncate on pricing.modelo_corrida
  for each statement execute function ops.tg_solo_agregar();

create table if not exists pricing.modelo_coef (
    corrida_id  bigint            not null,
    coef        double precision,
    error_std   double precision,
    p_valor     double precision,
    variable    text              not null,
    constraint modelo_coef_pkey primary key (corrida_id, variable),
    constraint modelo_coef_corrida_fk foreign key (corrida_id) references pricing.modelo_corrida (id) on delete restrict,
    constraint modelo_coef_valores_chk check ((error_std is null or error_std >= 0) and (p_valor is null or p_valor between 0 and 1)),
    constraint modelo_coef_variable_chk check (char_length(variable) between 1 and 200)
);
comment on table pricing.modelo_coef is
  'Un renglón por variable y corrida (coeficiente, error estándar y p): comparar la elasticidad entre corridas es un join, no desarmar un jsonb. float8, como los produce el modelo. SOLO AGREGAR.';
alter table pricing.modelo_coef enable row level security;
revoke all on pricing.modelo_coef from public, anon, authenticated, service_role;
grant select, insert on pricing.modelo_coef to service_role;
create or replace trigger modelo_coef_solo_agregar before update or delete on pricing.modelo_coef
  for each row execute function ops.tg_solo_agregar();
create or replace trigger modelo_coef_sin_truncate before truncate on pricing.modelo_coef
  for each statement execute function ops.tg_solo_agregar();

-- ───────────────────────────────────────────────────────────────────────────
-- 18b) pricing.orden_etiqueta — si la orden fue reventa (atributo de la ORDEN)
-- ───────────────────────────────────────────────────────────────────────────
create table if not exists pricing.orden_etiqueta (
    canal              text         not null,
    cuenta             text         not null,
    external_order_id  text         not null,
    captura_id         integer,
    leido_at           timestamptz  not null default now(),
    es_reventa         boolean      not null,
    etiquetas          text[]       not null default '{}',
    constraint orden_etiqueta_pkey primary key (canal, cuenta, external_order_id),
    constraint orden_etiqueta_canal_fk foreign key (canal) references core.channels (id),
    constraint orden_etiqueta_captura_fk foreign key (captura_id) references pricing.captura (id),
    constraint orden_etiqueta_ids_chk check (char_length(cuenta) between 1 and 40 and char_length(external_order_id) between 1 and 40),
    constraint orden_etiqueta_etiquetas_chk check (cardinality(etiquetas) <= 30 and array_position(etiquetas, null) is null
                                                   and array_to_string(etiquetas, ',') ~ '^[a-z0-9_,]*$'),
    constraint orden_etiqueta_reventa_chk check (canal <> 'mercado_libre' or es_reventa = ('meli_resale' = any (etiquetas)))
);
comment on table pricing.orden_etiqueta is
  'Una fila por orden del canal con lo que channel.orders no guarda: si fue reventa (ML static_tags meli_resale: la compró ML para revender, paga ~58 % y no cobra comisión ni envío) y sus etiquetas. Misma llave que channel.orders, SIN llave foránea (no se amarra una tabla caliente al crear esto). Sin fila = no se leyó (en ML las ventas de ese día quedan «sin clasificar»). La cobertura de un día la marca una captura grupo ordenes terminada. Solo ids y etiquetas: nunca datos del comprador.';
comment on column pricing.orden_etiqueta.etiquetas is
  'static_tags + tags de la orden en minúsculas (meli_resale, b2b, pack_order, catalog, supermarket…). Solo [a-z0-9_]. En ML, es_reventa = (meli_resale está aquí): si se recortan a 30, nunca dejar fuera meli_resale.';
alter table pricing.orden_etiqueta enable row level security;
revoke all on pricing.orden_etiqueta from public, anon, authenticated, service_role;
grant select, insert, update on pricing.orden_etiqueta to service_role;
create or replace trigger orden_etiqueta_llave_inmutable before update of canal, cuenta, external_order_id on pricing.orden_etiqueta
  for each row execute function pricing.tg_llave_inmutable();
create or replace trigger orden_etiqueta_sin_borrado before delete on pricing.orden_etiqueta
  for each statement execute function pricing.tg_sin_borrado();
create or replace trigger orden_etiqueta_sin_truncate before truncate on pricing.orden_etiqueta
  for each statement execute function ops.tg_sin_truncate();

-- El reparto del envío por carrito busca las órdenes del mismo envío. Único
-- objeto que esta migración crea FUERA de pricing (tabla de ~33 mil filas).
create index if not exists order_shipping_cost_envio_idx on enrich.order_shipping_cost (cuenta, shipment_id);

-- ───────────────────────────────────────────────────────────────────────────
-- 19) VISTAS: lo que ya existe en otro lado se DERIVA, no se copia.
--     Todas security_invoker = on y con columnas explícitas (sin `*`): una
--     columna nueva en una tabla base no cambia la vista en silencio y se
--     agrega AL FINAL con create or replace, que siempre re-declara el with.
-- ───────────────────────────────────────────────────────────────────────────

-- Cada línea de pedido, con su día de México, su cuenta, si se canceló, si fue
-- reventa y el envío que nos costó. ES EL ÚNICO LUGAR donde viven esas reglas:
-- si channel llega a guardar la etiqueta meli_resale, solo cambia es_reventa
-- aquí (mismas columnas, mismo tipo).
create or replace view pricing.venta_linea_v with (security_invoker = on) as
select (o.creado_at at time zone 'America/Mexico_City')::date                     as fecha,
       i.canal,
       coalesce(o.account_id, cu.id)                                                as account_id,
       i.item_id                                                                    as listing_id,
       i.sku,
       i.external_order_id,
       i.linea,
       i.cantidad,
       i.precio_unitario * i.cantidad                                               as importe,
       i.comision,
       lower(coalesce(o.estado_canal, '')) in ('cancelled', 'invalid', 'canceled')  as cancelada,
       case when i.canal <> 'mercado_libre' then false else oe.es_reventa end       as es_reventa,
       -- El costo guardado es del ENVÍO (senders.cost de /shipments/{id}/costs) y
       -- cada orden de un carrito lo trae completo: se reparte por piezas entre
       -- TODAS las órdenes del mismo envío (sin envío, entre las líneas de la
       -- orden). NULL = no medido (la tabla es parcial); la reventa no paga envío.
       -- El CASE corta: la subconsulta solo corre donde hay costo.
       case when i.canal = 'mercado_libre' and oe.es_reventa then 0::numeric
            when sc.costo_vendedor is null then null
            when sc.shipment_id is not null then
                 sc.costo_vendedor * i.cantidad
                 / nullif((select sum(i2.cantidad)
                             from enrich.order_shipping_cost s2
                             join channel.order_items i2
                               on i2.canal = 'mercado_libre' and i2.cuenta = s2.cuenta
                              and i2.external_order_id = s2.external_order_id
                            where s2.cuenta = sc.cuenta and s2.shipment_id = sc.shipment_id), 0)
            else sc.costo_vendedor * i.cantidad
                 / nullif((select sum(i2.cantidad) from channel.order_items i2
                            where i2.canal = i.canal and i2.cuenta = i.cuenta
                              and i2.external_order_id = i.external_order_id), 0) end as envio_costo
  from channel.order_items i
  join channel.orders o
    on o.canal = i.canal and o.cuenta = i.cuenta and o.external_order_id = i.external_order_id
  left join pricing.orden_etiqueta oe
    on oe.canal = i.canal and oe.cuenta = i.cuenta and oe.external_order_id = i.external_order_id
  left join enrich.order_shipping_cost sc
    on i.canal = 'mercado_libre' and sc.cuenta = i.cuenta and sc.external_order_id = i.external_order_id
  left join lateral (
         select a.id
           from core.accounts a
          where a.legacy_code = i.cuenta and a.channel_id = i.canal
          order by a.id
          limit 1) cu on true;
comment on view pricing.venta_linea_v is
  'Cada línea de pedido de channel.order_items con su día de México (creado_at), su cuenta (orders.account_id o core.accounts por legacy_code), cancelada (mismo criterio que channel.sales_daily, sin importar mayúsculas), es_reventa (false fuera de ML; en ML la de pricing.orden_etiqueta y NULL si la orden no se ha leído: NO se sabe, no es «no») y envio_costo (el costo del ENVÍO de enrich.order_shipping_cost repartido por piezas entre todas las órdenes de ese envío; 0 en reventa; NULL = no medido; si el caché tiene solo algunas órdenes de un carrito, el total cuadra pero se lo llevan ésas).';
revoke all on pricing.venta_linea_v from public, anon, authenticated, service_role;
grant select on pricing.venta_linea_v to service_role;

create or replace view pricing.ventas_dia_v with (security_invoker = on) as
select v.fecha,
       v.canal,
       v.account_id,
       v.listing_id,
       coalesce(sum(v.cantidad) filter (where not v.cancelada), 0)                           as unidades_total,
       case when bool_or(v.es_reventa is null) filter (where not v.cancelada) then null
            else coalesce(sum(v.cantidad) filter (where not v.cancelada and not v.es_reventa), 0) end as unidades_normal,
       case when bool_or(v.es_reventa is null) filter (where not v.cancelada) then null
            else coalesce(sum(v.cantidad) filter (where not v.cancelada and v.es_reventa), 0) end     as unidades_reventa,
       coalesce(sum(v.cantidad) filter (where not v.cancelada and v.es_reventa is null), 0)  as unidades_sin_clasificar,
       coalesce(sum(v.importe) filter (where not v.cancelada), 0)                            as ingreso_total,
       case when bool_or(v.es_reventa is null) filter (where not v.cancelada) then null
            else coalesce(sum(v.importe) filter (where not v.cancelada and not v.es_reventa), 0) end  as ingreso_normal,
       case when bool_or(v.es_reventa is null) filter (where not v.cancelada) then null
            else coalesce(sum(v.importe) filter (where not v.cancelada and v.es_reventa), 0) end      as ingreso_reventa,
       case when count(*) filter (where not v.cancelada) = 0 then 0
            else sum(v.comision) filter (where not v.cancelada) end                          as comision,
       case when bool_or(v.envio_costo is null) filter (where not v.cancelada) then null
            else coalesce(sum(v.envio_costo) filter (where not v.cancelada), 0) end          as envio_costo,
       (count(distinct v.external_order_id) filter (where not v.cancelada and v.envio_costo is not null))::numeric
         / nullif(count(distinct v.external_order_id) filter (where not v.cancelada), 0)    as envio_cobertura,
       count(distinct v.external_order_id) filter (where not v.cancelada)                    as ordenes,
       coalesce(sum(v.cantidad) filter (where v.cancelada), 0)                               as unidades_canceladas,
       count(distinct v.external_order_id) filter (where v.cancelada)                        as ordenes_canceladas
  from pricing.venta_linea_v v
 group by v.fecha, v.canal, v.account_id, v.listing_id;
comment on view pricing.ventas_dia_v is
  'Ventas por publicación y día de México, derivadas en vivo de channel.orders + order_items (la comisión pasa de 0 a su valor real y las cancelaciones tardías salen solas). unidades_normal/reventa e ingreso_normal/reventa son NULL si alguna línea del día no se sabe clasificar (orden de ML sin etiqueta leída). envio_costo es NULL si a alguna orden del día le falta su costo; envio_cobertura = fracción de órdenes vivas con costo medido. Un día con solo canceladas da comisión 0. La comisión es de ventas normales (la reventa no paga). ordenes cuenta pedidos distintos: no se suma entre días ni variaciones.';
revoke all on pricing.ventas_dia_v from public, anon, authenticated, service_role;
grant select on pricing.ventas_dia_v to service_role;

create or replace view pricing.precio_dia_v with (security_invoker = on) as
select x.publicacion_id,
       (x.desde at time zone 'America/Mexico_City')::date                                     as fecha,
       count(*) filter (where x.precio_base is not null and x.base_antes is not null
                          and x.precio_base <> x.base_antes)                                    as n_cambios_base,
       count(*) filter (where x.precio_cobrado is not null and x.cobrado_antes is not null
                          and x.precio_cobrado <> x.cobrado_antes)                              as n_cambios_cobrado,
       count(*) filter (where x.origen = 'panel')                                               as n_cambios_panel,
       min(x.desde)                                                                             as primer_cambio_at,
       max(x.desde)                                                                             as ultimo_cambio_at
  from (select o.publicacion_id,
               o.desde,
               o.precio_base,
               o.precio_cobrado,
               o.origen,
               lag(o.precio_base)    over (partition by o.publicacion_id order by o.desde) as base_antes,
               lag(o.precio_cobrado) over (partition by o.publicacion_id order by o.desde) as cobrado_antes
          from pricing.precio_obs o) x
 group by x.publicacion_id, (x.desde at time zone 'America/Mexico_City')::date;
comment on view pricing.precio_dia_v is
  'Cuántas veces cambió el precio base y el cobrado cada día (de pricing.precio_obs; la primera observación no cuenta como cambio). Sustituye a min/max/n_cambios_precio de la v1, que podían contradecir a los eventos.';
revoke all on pricing.precio_dia_v from public, anon, authenticated, service_role;
grant select on pricing.precio_dia_v to service_role;

-- El costo que regía en cada momento, sin copiarlo: cost_history guarda la fila
-- COMPLETA anterior en cada UPDATE o DELETE de costos_validados. Vista y no
-- función: los operadores de citext se resuelven al crearla (usa
-- idx_cost_history_sku, sin search_path ni EXECUTE que administrar).
create or replace view pricing.costo_vigencia_v with (security_invoker = on) as
select h.sku,
       (h.snapshot ->> 'costo_cbm')::numeric                                                as costo_cbm,
       coalesce(lag(h.created_at) over (partition by h.sku order by h.created_at, h.id),
                (h.snapshot ->> 'created_at')::timestamptz,
                '-infinity'::timestamptz)                                                    as vigente_desde,
       h.created_at                                                                          as vigente_hasta,
       'historia'::text                                                                      as fuente
  from costing.cost_history h
 where h.tabla = 'costos_validados'
union all
select v.sku,
       v.costo_cbm,
       greatest(v.created_at,
                (select max(h2.created_at)
                   from costing.cost_history h2
                  where h2.sku = v.sku and h2.tabla = 'costos_validados'))                    as vigente_desde,
       'infinity'::timestamptz                                                               as vigente_hasta,
       'actual'::text                                                                        as fuente
  from costing.costos_validados v;
comment on view pricing.costo_vigencia_v is
  'costo_cbm de cada SKU por intervalos [vigente_desde, vigente_hasta): las versiones viejas salen de costing.cost_history (fila completa previa a cada cambio) y la vigente de costos_validados. El costo «que se creía cierto» el día D es el que regía al cierre de D. OJO: ~30 % del catálogo tiene costo provisional (USD×19); hasta que costing lo marque, toda conclusión de margen va con esa reserva.';
revoke all on pricing.costo_vigencia_v from public, anon, authenticated, service_role;
grant select on pricing.costo_vigencia_v to service_role;

create or replace view pricing.competencia_dia_v with (security_invoker = on) as
with obs as (
  select d.publicacion_id,
         d.fecha,
         rp.precio,
         rp.medido_el
    from pricing.listing_dia d
    join pricing.listing_rival lr
      on lr.publicacion_id = d.publicacion_id
     and lr.desde <= d.fecha and (lr.hasta is null or lr.hasta >= d.fecha)
     and lr.clase = 'mismo'
    join pricing.rival r
      on r.id = lr.rival_id
    -- ni nuestras publicaciones ni nuestras cuentas: por item_id (siempre se
    -- sabe) y, de respaldo, por vendedor (core.accounts.external_id está vacío
    -- en ML; se mide el 19-oct)
     and not exists (select 1 from channel.listings l where l.canal = r.canal and l.listing_id = r.item_id)
     and not exists (select 1 from pricing.publicacion p2 join core.accounts a2 on a2.id = p2.account_id
                      where a2.channel_id = r.canal and p2.listing_id = r.item_id)
     and not exists (select 1 from core.accounts ca
                      where ca.channel_id = r.canal and ca.external_id is not null and ca.external_id = r.vendedor_id)
    cross join lateral (
           select x.precio,
                  least(d.fecha, x.visto_hasta) as medido_el
             from pricing.rival_precio x
            where x.rival_id = lr.rival_id
              and x.desde <= d.fecha and (x.hasta is null or x.hasta >= d.fecha)
              and d.fecha - x.visto_hasta <= 7          -- un precio de más de 7 días no entra
            order by x.desde desc
            limit 1) rp
),
agg as (
  select o.publicacion_id,
         o.fecha,
         count(*)                                              as comp_n,
         min(o.precio)                                         as comp_min,
         percentile_cont(0.5) within group (order by o.precio) as comp_mediana,
         min(o.medido_el)                                      as comp_medido_el,
         array_agg(o.precio order by o.precio)                 as precios
    from obs o
   group by o.publicacion_id, o.fecha
)
select a.publicacion_id,
       a.fecha,
       a.comp_n,
       a.comp_min,
       a.comp_mediana,
       case when a.comp_n >= 3
            then (select min(x) from unnest(a.precios) x where x >= 0.7 * a.comp_mediana)
            else a.comp_min end                                            as comp_referencia,
       a.comp_medido_el,
       'v2: rivales «mismo» medidos hace ≤ 7 días; con 3+ el más barato a no más de 30 % bajo la mediana, si no el mínimo'::text as comp_regla
  from agg a;
comment on view pricing.competencia_dia_v is
  'La competencia de cada publicación y día, RECALCULABLE: rivales vigentes ese día con veredicto «mismo», sin nuestras publicaciones ni cuentas (por item_id contra channel.listings y pricing.publicacion; de respaldo por vendedor) y con precio medido hace 7 días o menos. comp_medido_el = la medición más vieja que entró (un precio visto por última vez antes del día cuenta, pero se ve lo viejo que es). comp_regla dice qué regla produjo comp_referencia: cambiar la regla es cambiar esta vista, no reescribir historia.';
revoke all on pricing.competencia_dia_v from public, anon, authenticated, service_role;
grant select on pricing.competencia_dia_v to service_role;

create or replace view pricing.experiencia_razon_v with (security_invoker = on) as
select p.id                       as publicacion_id,
       h.canal,
       h.account_id,
       h.listing_id,
       h.dia                      as desde,
       h.detalle ->> 'razon'      as razon
  from enrich.listing_health_hist h
  left join pricing.publicacion p
    on p.account_id = h.account_id and p.listing_id = h.listing_id
 where h.metrica = 'experiencia'
   and h.detalle is not null;
comment on view pricing.experiencia_razon_v is
  'La razón de la experiencia de compra cada vez que cambió (enrich.listing_health_hist guarda detalle solo el día en que cambia la huella). Rige desde ese día hasta el siguiente cambio: unir «as of» (merge_asof). Fuera de panel_v a propósito: buscar hacia atrás por cada fila del panel es caro.';
revoke all on pricing.experiencia_razon_v from public, anon, authenticated, service_role;
grant select on pricing.experiencia_razon_v to service_role;

create or replace view pricing.decision_v with (security_invoker = on) as
select x.id,
       x.creada_at,
       x.autor,
       x.titulo,
       x.tipo,
       x.estado,
       x.metrica_objetivo,
       x.meta,
       x.ventana_antes_dias,
       x.ventana_despues_dias,
       x.evaluada_at,
       x.revertida_at,
       l.aplicada_at,
       l.n_publicaciones,
       l.n_aplicadas
  from pricing.decision x
  cross join lateral (
         select min(dl.aplicado_at)  as aplicada_at,
                count(*)             as n_publicaciones,
                count(dl.aplicado_at) as n_aplicadas
           from pricing.decision_listing dl
          where dl.decision_id = x.id) l;
comment on view pricing.decision_v is
  'La decisión con su fecha de aplicación CALCULADA (la primera aplicación entre sus publicaciones): ya no se guarda dos veces.';
revoke all on pricing.decision_v from public, anon, authenticated, service_role;
grant select on pricing.decision_v to service_role;

create or replace view pricing.decision_dia_v with (security_invoker = on) as
select dl.decision_id,
       dl.publicacion_id,
       g.dia::date                                                as fecha,
       (g.dia::date - ap.dia)                                     as dias_relativos,
       case when g.dia::date < ap.dia then 'antes'
            when g.dia::date = ap.dia then 'aplicacion'
            else 'despues' end                                    as fase,
       coalesce(g.dia::date >= rv.dia, false)                     as revertida
  from pricing.decision_listing dl
  join pricing.decision x on x.id = dl.decision_id
  cross join lateral (select (dl.aplicado_at at time zone 'America/Mexico_City')::date as dia) ap
  cross join lateral (select (coalesce(dl.revertido_at, x.revertida_at) at time zone 'America/Mexico_City')::date as dia) rv
  cross join lateral generate_series((ap.dia - x.ventana_antes_dias)::timestamp,
                                     (ap.dia + x.ventana_despues_dias)::timestamp,
                                     interval '1 day') g(dia)
 where dl.aplicado_at is not null
   and x.estado not in ('propuesta', 'descartada');
comment on view pricing.decision_dia_v is
  'Cada día de la ventana de cada decisión aplicada, por publicación: antes, aplicacion (el día mismo NO cuenta como tratado: pudo aplicarse a las 23:50) y despues; revertida = desde el día de la reversión (de la publicación o de toda la decisión). Base del estudio de eventos.';
revoke all on pricing.decision_dia_v from public, anon, authenticated, service_role;
grant select on pricing.decision_dia_v to service_role;

create or replace view pricing.cambio_v with (security_invoker = on) as
select po.publicacion_id,
       po.desde                                                       as ocurrio_at,
       'precio'::text                                                 as que,
       po.origen,
       po.accion_id,
       a.actor,
       a.decision_id,
       a.motivo,
       jsonb_build_object('precio_base', po.precio_base, 'precio_cobrado', po.precio_cobrado,
                          'fuente', po.fuente, 'desde_min', po.desde_min)  as detalle
  from pricing.precio_obs po
  left join pricing.accion a on a.id = po.accion_id
union all
select o.publicacion_id,
       coalesce(o.inicio, o.creada_at),
       'oferta_inicio',
       o.origen,
       o.accion_id,
       a.actor,
       a.decision_id,
       a.motivo,
       jsonb_build_object('oferta_id', o.id, 'tipo', o.tipo, 'promocion_id', o.promocion_id, 'fin', o.fin)
  from pricing.oferta o
  left join pricing.accion a on a.id = o.accion_id
union all
select o.publicacion_id,
       o.fin,
       'oferta_fin',
       o.fin_origen,
       o.fin_accion_id,
       af.actor,
       af.decision_id,
       af.motivo,
       jsonb_build_object('oferta_id', o.id, 'tipo', o.tipo)
  from pricing.oferta o
  left join pricing.accion af on af.id = o.fin_accion_id
 where o.fin is not null and o.fin <= now()
union all
select f.publicacion_id,
       f.desde,
       'ficha',
       f.origen,
       f.accion_id,
       a.actor,
       a.decision_id,
       a.motivo,
       jsonb_build_object('huella', f.huella, 'titulo', f.titulo, 'categoria_id', f.categoria_id,
                          'logistica', f.logistica, 'tipo_publicacion', f.tipo_publicacion,
                          'n_fotos', f.n_fotos, 'fotos_huella', f.fotos_huella)
  from pricing.listing_ficha f
  left join pricing.accion a on a.id = f.accion_id
union all
select e.publicacion_id,
       e.ocurrio_at,
       e.tipo,
       e.origen,
       e.accion_id,
       a.actor,
       a.decision_id,
       a.motivo,
       jsonb_build_object('descripcion', e.descripcion, 'antes', e.antes, 'despues', e.despues)
  from pricing.evento e
  left join pricing.accion a on a.id = e.accion_id
union all
-- lo que una persona HIZO o DECIDIÓ (incluye lo que decidió no hacer y las notas)
select ap.publicacion_id,
       coalesce(a.terminada_at, a.iniciada_at),
       'accion:' || a.tipo,
       a.origen_app,
       a.id,
       a.actor,
       a.decision_id,
       a.motivo,
       jsonb_build_object('estado', a.estado, 'resultado', ap.resultado, 'motivo_texto', a.motivo_texto,
                          'recomendacion', ap.recomendacion_clave, 'precio_recomendado', ap.precio_recomendado,
                          'actor_tipo', a.actor_tipo, 'precio_cobrado_antes', ap.precio_cobrado_antes,
                          'precio_cobrado_pedido', ap.precio_cobrado_pedido, 'precio_cobrado_despues', ap.precio_cobrado_despues)
  from pricing.accion_publicacion ap
  join pricing.accion a on a.id = ap.accion_id;
comment on view pricing.cambio_v is
  '«Qué cambió»: UNA línea de tiempo con los cambios OBSERVADOS (precio_obs, ofertas que empezaron o terminaron, ficha, eventos) y las ACCIONES de las personas (que = accion:<tipo>, incluidas las revisiones sin cambio y las notas). Cada cambio observado trae su acción (accion_id → quién, decisión y motivo) si la hubo: una acción y el cambio que produjo aparecen ambos (lo pedido y lo que se vio). La primera fila de cada fuente es el punto de partida, no un cambio.';
revoke all on pricing.cambio_v from public, anon, authenticated, service_role;
grant select on pricing.cambio_v to service_role;

create or replace view pricing.seguimiento_hoy_v with (security_invoker = on) as
with hoy as (
  select (now() at time zone 'America/Mexico_City')::date as dia
),
lista as (
  select a.channel_id   as canal,
         p.account_id,
         p.listing_id,
         s.motivo
    from pricing.seguimiento s
    join pricing.publicacion p on p.id = s.publicacion_id
    join core.accounts a on a.id = p.account_id
    cross join hoy
   where s.desde <= hoy.dia and (s.hasta is null or s.hasta >= hoy.dia)
  union all
  select l.canal, l.account_id, l.listing_id, 'full_activo'
    from channel.listings l
   where l.canal = 'mercado_libre' and l.is_fulfillment and l.situacion = 'active'
     and l.listing_id is not null
  union all
  select l.canal, l.account_id, l.listing_id, 'modificado'
    from channel.listing_history h
    join channel.listings l
      on l.sku = h.sku and l.account_id = h.account_id and l.canal = h.canal
   where h.campo = 'price' and h.changed_at >= now() - interval '30 days'
     and l.listing_id is not null
  union all
  select a.channel_id, p.account_id, p.listing_id, 'modificado'
    from pricing.oferta o
    join pricing.publicacion p on p.id = o.publicacion_id
    join core.accounts a on a.id = p.account_id
   where o.origen = 'panel' and o.creada_at >= now() - interval '30 days'
  union all
  select a2.channel_id, p.account_id, p.listing_id, 'modificado'
    from pricing.accion_publicacion ap
    join pricing.accion ac on ac.id = ap.accion_id
    join pricing.publicacion p on p.id = ap.publicacion_id
    join core.accounts a2 on a2.id = p.account_id
   where ac.estado in ('aplicada', 'parcial', 'error', 'deshecha') and ac.tipo not in ('nota', 'revisada_sin_cambio')
     and ac.iniciada_at >= now() - interval '30 days'
)
select x.canal,
       x.account_id,
       x.listing_id,
       p.id                                           as publicacion_id,
       array_agg(distinct x.motivo order by x.motivo) as motivos
  from lista x
  left join pricing.publicacion p
    on p.account_id = x.account_id and p.listing_id = x.listing_id
 group by x.canal, x.account_id, x.listing_id, p.id;
comment on view pricing.seguimiento_hoy_v is
  'Qué debe leer hoy el escritor y por qué: lo seguido a mano o como control (pricing.seguimiento), las FULL activas de ML (channel.listings: is_fulfillment y situacion active; alinear con services/full_publicaciones antes de usarla) y lo modificado en los últimos 30 días (cambio de price en channel.listing_history u oferta dada de alta desde el panel). publicacion_id NULL = todavía no existe en pricing.publicacion.';
revoke all on pricing.seguimiento_hoy_v from public, anon, authenticated, service_role;
grant select on pricing.seguimiento_hoy_v to service_role;

-- EL PANEL. Por conjuntos: lo agregado va en subconsultas agrupadas por
-- (publicacion_id, fecha) y unidas por llave; los «a esa fecha» son búsquedas
-- por índice. Un filtro de IGUALDAD por publicación o por fecha baja hasta las
-- subconsultas propias de pricing; un RANGO de fechas no (Postgres no lo empuja
-- a través de un LEFT JOIN a una subconsulta agrupada). ventas_dia_v y las
-- devoluciones se unen por (cuenta, listing, fecha): un filtro por
-- publicacion_id NO llega a ellas y la vista agrega channel.* completo una vez
-- por consulta. Para UNA publicación, consultar ventas_dia_v por listing_id
-- (baja al índice de order_items). Antes de producción: EXPLAIN (ANALYZE) por
-- publicación y por rango en el sandbox clonado.
-- Todo lo numérico sale en float8.
create or replace view pricing.panel_v with (security_invoker = on) as
with skus as (
  select d.publicacion_id,
         d.fecha,
         count(distinct s.sku)                                                      as n_skus,
         case when count(distinct s.sku) = 1 then min(s.sku::text) end              as sku,
         case when count(distinct s.sku) = 1 then min(c.costo_cbm) end              as costo_unitario
    from pricing.listing_dia d
    join pricing.publicacion_sku s
      on s.publicacion_id = d.publicacion_id
     and s.desde <= d.fecha and (s.hasta is null or s.hasta >= d.fecha)
    left join pricing.costo_vigencia_v c
      on c.sku = s.sku
     and c.vigente_desde <= ((d.fecha + 1)::timestamp at time zone 'America/Mexico_City')
     and c.vigente_hasta >  ((d.fecha + 1)::timestamp at time zone 'America/Mexico_City')
   group by d.publicacion_id, d.fecha
),
promos as (
  select o.publicacion_id,
         od.fecha,
         array_agg(distinct o.tipo order by o.tipo)                                 as promos_activas,
         count(*)                                                                   as n_ofertas_activas,
         min(od.precio) filter (where o.tipo ilike '%coupon%')                      as precio_con_cupon
    from pricing.oferta_dia od
    join pricing.oferta o on o.id = od.oferta_id
   where od.estado = 'started'
   group by o.publicacion_id, od.fecha
),
devoluciones as (
  select rd.date                                                                    as fecha,
         cu.id                                                                      as account_id,
         rd.item_id                                                                 as listing_id,
         sum(rd.units_returned)                                                     as unidades_devueltas
    from channel.returns_daily rd
    cross join lateral (
           select a.id
             from core.accounts a
            where a.legacy_code = rd.cuenta and a.channel_id = rd.canal
            order by a.id
            limit 1) cu
   where rd.venta_contaba is not false   -- las de pedidos ya cancelados ya salieron de ventas_dia_v
   group by rd.date, cu.id, rd.item_id
),
ads as (
  select x.publicacion_id,
         x.fecha,
         sum(x.costo)                                                               as costo,
         sum(x.clics)                                                               as clics,
         sum(x.impresiones)                                                         as impresiones
    from pricing.ads_dia x
   group by x.publicacion_id, x.fecha
),
cal as (
  select d.publicacion_id,
         d.fecha,
         array_agg(distinct c.tipo order by c.tipo)                                 as eventos_tipo,
         max(c.intensidad)                                                          as evento_intensidad
    from pricing.listing_dia d
    join pricing.publicacion p on p.id = d.publicacion_id
    join core.accounts a on a.id = p.account_id
    join pricing.calendario c
      on d.fecha between c.desde and c.hasta
     and (c.canal is null or c.canal = a.channel_id)
   group by d.publicacion_id, d.fecha
),
decs as (
  select dd.publicacion_id,
         dd.fecha,
         array_agg(dd.decision_id order by dd.decision_id)                          as decisiones_vigentes
    from pricing.decision_dia_v dd
   where dd.fase = 'despues' and not dd.revertida
   group by dd.publicacion_id, dd.fecha
)
select
    -- llaves
    d.fecha,
    d.publicacion_id,
    a.channel_id                                                                    as canal,
    p.account_id,
    p.listing_id,
    sk.sku,
    coalesce(sk.n_skus, 0)                                                          as n_skus,
    -- la ficha vigente al cierre del día
    f.categoria_id,
    f.tipo_publicacion,
    f.logistica,
    (f.logistica = 'fulfillment')                                                   as es_full,
    f.es_catalogo,
    f.tiene_video,
    f.n_fotos,
    f.precio_automatico,
    (d.fecha - (p.fecha_alta at time zone 'America/Mexico_City')::date)            as antiguedad_dias,
    -- la foto: precio y promoción
    d.estado,
    d.precio_base::float8                                                           as precio_base,
    d.precio_cobrado::float8                                                        as precio_cobrado,
    case when d.precio_cobrado > 0 then ln(d.precio_cobrado::float8) end            as ln_precio,
    case when d.precio_base > 0 and d.precio_cobrado <= d.precio_base
         then 1 - d.precio_cobrado::float8 / d.precio_base::float8 end              as descuento_mostrado_pct,
    d.cobra_estado,
    oc.tipo                                                                         as promo_tipo_cobra,
    odc.meli_pct::float8                                                            as cobra_meli_pct,
    case when d.precio_base > 0 and d.precio_cobrado <= d.precio_base and d.cobra_estado = 'ninguna'
              then 1 - d.precio_cobrado::float8 / d.precio_base::float8
         when d.precio_base > 0 and d.precio_cobrado <= d.precio_base and d.cobra_estado = 'identificada'
              and odc.meli_pct is not null
              then 1 - d.precio_cobrado::float8 / d.precio_base::float8 - odc.meli_pct::float8
    end                                                                             as descuento_vendedor_pct,
    hoc.horas_oferta_cobra,
    pr.promos_activas,
    coalesce(pr.n_ofertas_activas, 0)                                               as n_ofertas_activas,
    pr.precio_con_cupon::float8                                                     as precio_con_cupon,
    coalesce(pd.n_cambios_base, 0)                                                  as n_cambios_base,
    coalesce(pd.n_cambios_cobrado, 0)                                               as n_cambios_cobrado,
    (d.fecha - (uc.desde at time zone 'America/Mexico_City')::date)                as dias_desde_cambio_precio,   -- NULL = sin cambio desde que se observa
    pp.precio_cobrado_pond                                                          as precio_cobrado_pond,
    d1.precio_cobrado::float8                                                       as precio_cobrado_ayer,
    d7.precio_cobrado::float8                                                       as precio_cobrado_hace_7d,
    case when d7.precio_cobrado > 0
         then d.precio_cobrado::float8 / d7.precio_cobrado::float8 - 1 end          as cambio_precio_7d,
    -- disponibilidad (censura)
    d.stock_disponible,
    dp.horas_disponible::float8                                                     as horas_disponible,
    case when dp.horas_disponible is not null then dp.horas_disponible < 23.5 end   as demanda_censurada,
    -- envío
    d.envio_gratis,
    u.valor::float8                                                                 as umbral_envio_gratis,
    case when u.valor > 0 then d.precio_cobrado >= u.valor end                      as sobre_umbral_envio,
    case when u.valor > 0 then d.precio_cobrado::float8 / u.valor::float8 - 1 end   as distancia_umbral_pct,
    -- buybox y prueba social
    d.buybox_estado,
    d.buybox_precio_ganar::float8                                                   as buybox_precio_ganar,
    d.visit_share,
    d.vendidos_acumulado,
    -- las bandas que muestra la ficha («+100 vendidos»); verificar los cortes contra ML
    case when d.vendidos_acumulado is null then null
         when d.vendidos_acumulado >= 50000 then 50000
         when d.vendidos_acumulado >= 10000 then 10000
         when d.vendidos_acumulado >= 5000 then 5000
         when d.vendidos_acumulado >= 1000 then 1000
         when d.vendidos_acumulado >= 500 then 500
         when d.vendidos_acumulado >= 100 then 100
         when d.vendidos_acumulado >= 50  then 50
         when d.vendidos_acumulado >= 25  then 25
         when d.vendidos_acumulado >= 5   then 5
         when d.vendidos_acumulado >= 1   then 1
         else 0 end                                                                 as banda_vendidos,
    -- demanda: visitas y preguntas (resultado_dia) y ventas (channel.*)
    r.visitas,
    r.visitas_completa,
    r.preguntas_recibidas,
    r.preguntas_sin_responder,
    un.unidades_total,
    un.unidades_normal,
    un.unidades_reventa,
    un.unidades_sin_clasificar,
    un.ordenes,
    un.ingreso_total,
    un.ingreso_normal,
    un.ingreso_reventa,
    un.comision,
    un.envio_costo,
    un.envio_cobertura,
    -- neto_antes_envio sigue la misma regla de «se sabe» que unidades_normal
    -- (0 en días sin venta solo si el lector de órdenes cubrió el día).
    case when un.ingreso_normal is not null and un.comision is not null
         then un.ingreso_normal - un.comision + un.ingreso_reventa end              as neto_antes_envio,
    -- neto solo en días CON venta y con todo el envío medido: NO hacer dropna
    -- sobre esta columna (la cobertura del envío no es aleatoria).
    case when not un.sin_ventas and un.ingreso_normal is not null and un.comision is not null and un.envio_costo is not null
         then un.ingreso_normal - un.comision - un.envio_costo + un.ingreso_reventa end as neto,
    un.unidades_canceladas,
    case when a.channel_id = 'mercado_libre' then coalesce(dv.unidades_devueltas, 0) end as unidades_devueltas_abiertas,   -- NULL = canal sin captura de devoluciones
    case when r.visitas > 0 and un.unidades_normal is not null
         then un.unidades_normal::float8 / r.visitas end                            as conversion,
    case when r.visitas > 0 then un.unidades_total::float8 / r.visitas end          as conversion_total_con_reventa,
    case when dp.horas_disponible > 0 and un.unidades_normal is not null
         then un.unidades_normal::float8 / dp.horas_disponible::float8 end          as unidades_normal_por_hora,
    case when un.unidades_total > 0 then un.ingreso_total / un.unidades_total end   as precio_realizado,   -- incluye la reventa (~58 %)
    case when un.unidades_normal > 0 then un.ingreso_normal / un.unidades_normal end as precio_realizado_normal,
    -- publicidad
    am.ads_medido,
    case when am.ads_medido then coalesce(ad.costo, 0)::float8 end                  as ads_costo,
    case when am.ads_medido then coalesce(ad.clics, 0) end                          as ads_clics,
    case when am.ads_medido then coalesce(ad.impresiones, 0) end                    as ads_impresiones,
    case when am.ads_medido and r.visitas is not null
         then r.visitas - coalesce(ad.clics, 0) end                                 as visitas_organicas_aprox,
    -- calidad y experiencia del día ANTERIOR (rezago: la experiencia sale de las mismas ventas)
    hc.estado                                                                       as calidad_estado_d1,
    hc.valor::float8                                                                as calidad_valor_d1,
    hc.nivel                                                                        as calidad_nivel_d1,
    hc.n_pendientes                                                                 as calidad_pendientes_d1,
    he.estado                                                                       as experiencia_estado_d1,
    he.valor::float8                                                                as experiencia_valor_d1,
    he.nivel                                                                        as experiencia_nivel_d1,
    -- competencia
    cp.comp_n,
    cp.comp_min::float8                                                             as comp_min,
    cp.comp_mediana,
    cp.comp_referencia::float8                                                      as comp_referencia,
    cp.comp_medido_el,
    (d.fecha - cp.comp_medido_el)                                                   as comp_dias_sin_medir,
    cp.comp_regla,
    case when cp.comp_referencia > 0
         then d.precio_cobrado::float8 / cp.comp_referencia::float8 end             as precio_vs_comp,
    -- top de la categoría ese día (NULL con top_medido = fuera del top)
    (h.dia is not null)                                                             as top_medido,
    pt.posicion::smallint                                                           as posicion_top,
    -- costo
    sk.costo_unitario::float8                                                       as costo_unitario,
    case when sk.costo_unitario > 0
         then d.precio_cobrado::float8 / sk.costo_unitario::float8 end              as precio_vs_costo,
    -- calendario
    extract(isodow from d.fecha)::smallint                                          as dia_semana,
    (extract(day from d.fecha) = 15 or extract(day from d.fecha + 1) = 1)           as es_quincena,
    (cl.eventos_tipo is not null)                                                   as hay_evento,
    cl.eventos_tipo,
    cl.evento_intensidad,
    (pe.proximo - d.fecha)                                                          as dias_a_proximo_evento,
    -- decisiones y muestra
    dc.decisiones_vigentes,
    (coalesce(cardinality(dc.decisiones_vigentes), 0) > 1)                          as decision_traslape,
    sg.motivos                                                                      as motivos_seguimiento,
    d.captura_id,
    ac.acciones_del_dia,
    ac.tipos_accion_del_dia,
    ac.tipos_tratamiento_del_dia,
    ac.revisiones_del_dia,
    ac.acciones_fallidas_del_dia
  from pricing.listing_dia d
  join pricing.publicacion p on p.id = d.publicacion_id
  join core.accounts a on a.id = p.account_id
  cross join lateral (
         select ((d.fecha + 1)::timestamp at time zone 'America/Mexico_City') as fin_dia,
                (d.fecha::timestamp at time zone 'America/Mexico_City')       as ini_dia) t
  left join lateral (
         select lf.categoria_id, lf.tipo_publicacion, lf.logistica, lf.es_catalogo, lf.tiene_video,
                lf.n_fotos, lf.precio_automatico, lf.catalog_product_id, lf.user_product_id
           from pricing.listing_ficha lf
          where lf.publicacion_id = d.publicacion_id and lf.desde < t.fin_dia
          order by lf.desde desc
          limit 1) f on true
  left join pricing.oferta oc on oc.id = d.oferta_cobra_id
  left join pricing.oferta_dia odc on odc.oferta_id = d.oferta_cobra_id and odc.fecha = d.fecha
  cross join lateral (
         select case when oc.id is not null then
                  greatest(0::numeric,
                           extract(epoch from least(coalesce(oc.fin, t.fin_dia), t.fin_dia)
                                              - greatest(coalesce(oc.inicio, t.ini_dia), t.ini_dia)) / 3600)::float8
                end as horas_oferta_cobra) hoc
  left join skus sk on sk.publicacion_id = d.publicacion_id and sk.fecha = d.fecha
  left join promos pr on pr.publicacion_id = d.publicacion_id and pr.fecha = d.fecha
  left join pricing.precio_dia_v pd on pd.publicacion_id = d.publicacion_id and pd.fecha = d.fecha
  left join lateral (
         select po.desde
           from pricing.precio_obs po
          where po.publicacion_id = d.publicacion_id and po.desde < t.fin_dia
            and exists (select 1 from pricing.precio_obs p0     -- la primera observación es el punto de partida, no un cambio
                         where p0.publicacion_id = po.publicacion_id and p0.desde < po.desde)
          order by po.desde desc
          limit 1) uc on true
  -- precio cobrado ponderado por el tiempo que rigió dentro del día (precio_obs)
  left join lateral (
         with tramos as (
           select po.desde,
                  po.precio_cobrado,
                  coalesce(lead(po.desde) over (order by po.desde), t.fin_dia) as hasta
             from pricing.precio_obs po
            where po.publicacion_id = d.publicacion_id
              and po.desde < t.fin_dia
              and po.desde >= coalesce((select max(p0.desde) from pricing.precio_obs p0
                                         where p0.publicacion_id = d.publicacion_id and p0.desde <= t.ini_dia),
                                       '-infinity'::timestamptz))
         select (sum(x.precio_cobrado * extract(epoch from least(x.hasta, t.fin_dia) - greatest(x.desde, t.ini_dia)))
                 / nullif(sum(extract(epoch from least(x.hasta, t.fin_dia) - greatest(x.desde, t.ini_dia))), 0))::float8
                   as precio_cobrado_pond
           from tramos x
          where x.hasta > t.ini_dia) pp on true
  left join pricing.listing_dia d1 on d1.publicacion_id = d.publicacion_id and d1.fecha = d.fecha - 1
  left join pricing.listing_dia d7 on d7.publicacion_id = d.publicacion_id and d7.fecha = d.fecha - 7
  left join lateral (
         select pc.valor
           from pricing.parametro_canal pc
          where pc.canal = a.channel_id and pc.clave = 'umbral_envio_gratis'
            and pc.desde <= d.fecha and (pc.hasta is null or pc.hasta >= d.fecha)
          order by pc.desde desc
          limit 1) u on true
  left join pricing.resultado_dia r on r.publicacion_id = d.publicacion_id and r.fecha = d.fecha
  left join pricing.disponibilidad_dia dp on dp.publicacion_id = d.publicacion_id and dp.fecha = d.fecha
  left join pricing.ventas_dia_v v
    on v.account_id = p.account_id and v.listing_id = p.listing_id and v.fecha = d.fecha
  -- Un día SIN ventas vale 0 «normales» solo si el lector de etiquetas CUBRIÓ
  -- ese día (captura grupo ordenes terminada): si no corrió, tampoco se sabe y
  -- va NULL, como los días con ventas sin clasificar (si no, un dropna() deja
  -- solo los ceros y la regresión sale sesgada). Fuera de ML siempre se sabe.
  cross join lateral (
         select (a.channel_id <> 'mercado_libre'
                 or exists (select 1 from pricing.captura c
                             where c.grupo = 'ordenes' and c.terminada_at is not null and c.fecha = d.fecha
                               and (c.account_id is null or c.account_id = p.account_id))) as clasificable) cl0
  cross join lateral (
         select (v.fecha is null or v.unidades_total = 0) as sin_ventas) sv
  -- Cuando el día no se puede clasificar (en ML: el lector de órdenes no lo
  -- cubrió), normal y reventa van NULL tanto con ventas como sin ellas; si se
  -- puede, un día sin ventas (o con solo canceladas) vale 0.
  cross join lateral (
         select coalesce(v.unidades_total, 0)                                             as unidades_total,
                sv.sin_ventas,
                case when not cl0.clasificable then null when sv.sin_ventas then 0
                     else v.unidades_normal end                                           as unidades_normal,
                case when not cl0.clasificable then null when sv.sin_ventas then 0
                     else v.unidades_reventa end                                          as unidades_reventa,
                coalesce(v.unidades_sin_clasificar, 0)                                    as unidades_sin_clasificar,
                coalesce(v.ordenes, 0)                                                    as ordenes,
                coalesce(v.ingreso_total, 0)::float8                                      as ingreso_total,
                case when not cl0.clasificable then null when sv.sin_ventas then 0::float8
                     else v.ingreso_normal::float8 end                                    as ingreso_normal,
                case when not cl0.clasificable then null when sv.sin_ventas then 0::float8
                     else v.ingreso_reventa::float8 end                                   as ingreso_reventa,
                case when sv.sin_ventas then 0::float8 else v.comision::float8 end        as comision,
                case when sv.sin_ventas then 0::float8 else v.envio_costo::float8 end     as envio_costo,
                case when sv.sin_ventas then null else v.envio_cobertura::float8 end      as envio_cobertura,
                coalesce(v.unidades_canceladas, 0)                                        as unidades_canceladas) un
  left join devoluciones dv
    on dv.account_id = p.account_id and dv.listing_id = p.listing_id and dv.fecha = d.fecha
  left join ads ad on ad.publicacion_id = d.publicacion_id and ad.fecha = d.fecha
  cross join lateral (
         select exists (select 1
                          from pricing.captura c
                         where c.grupo = 'ads' and c.terminada_at is not null and c.fecha = d.fecha
                           and (c.account_id is null or c.account_id = p.account_id)) as ads_medido) am
  left join enrich.listing_health_hist hc
    on hc.canal = a.channel_id and hc.account_id = p.account_id and hc.listing_id = p.listing_id
   and hc.metrica = 'calidad' and hc.dia = d.fecha - 1
  left join enrich.listing_health_hist he
    on he.canal = a.channel_id and he.account_id = p.account_id and he.listing_id = p.listing_id
   and he.metrica = 'experiencia' and he.dia = d.fecha - 1
  left join pricing.competencia_dia_v cp on cp.publicacion_id = d.publicacion_id and cp.fecha = d.fecha
  left join enrich.market_highlights_hist h
    on h.canal = a.channel_id and h.categoria_id = f.categoria_id and h.dia = d.fecha
  left join lateral (
         select min(e.n) as posicion
           from jsonb_array_elements_text(h.top) with ordinality e(id, n)
          where e.id in (p.listing_id, f.catalog_product_id, f.user_product_id)) pt on true
  left join cal cl on cl.publicacion_id = d.publicacion_id and cl.fecha = d.fecha
  left join lateral (
         select min(c2.desde) as proximo
           from pricing.calendario c2
          where c2.desde > d.fecha and (c2.canal is null or c2.canal = a.channel_id)) pe on true
  left join decs dc on dc.publicacion_id = d.publicacion_id and dc.fecha = d.fecha
  left join lateral (
         select array_agg(s.motivo order by s.motivo) as motivos
           from pricing.seguimiento s
          where s.publicacion_id = d.publicacion_id
            and s.desde <= d.fecha and (s.hasta is null or s.hasta >= d.fecha)) sg on true
  left join lateral (
         select array_agg(x.id order by x.id)                       as acciones_del_dia,
                array_agg(distinct x.tipo order by x.tipo)          as tipos_accion_del_dia,
                -- el TRATAMIENTO: lo que se aplicó y quedó en la publicación
                array_agg(distinct x.tipo order by x.tipo)
                  filter (where x.estado in ('aplicada', 'parcial', 'deshecha') and ap.resultado in ('ok', 'parcial', 'regresada'))
                                                                    as tipos_tratamiento_del_dia,
                count(*) filter (where x.tipo in ('nota', 'revisada_sin_cambio'))                   as revisiones_del_dia,
                count(*) filter (where x.estado = 'error' or ap.resultado = 'error')                as acciones_fallidas_del_dia
           from pricing.accion_publicacion ap
           join pricing.accion x on x.id = ap.accion_id
          where ap.publicacion_id = d.publicacion_id
            and coalesce(x.terminada_at, x.iniciada_at) >= t.ini_dia
            and coalesce(x.terminada_at, x.iniciada_at) < t.fin_dia) ac on true;
comment on view pricing.panel_v is
  'EL PANEL de la regresión: una fila por publicación y día con foto (listing_dia), todo en float8. Ventas de channel.* en vivo (conversion = unidades_NORMALES ÷ visitas; en ML es NULL si el lector de órdenes no cubrió el día o falta la etiqueta de alguna orden, con o sin ventas; conversion_total_con_reventa incluye la reventa); precio_cobrado_pond = precio ponderado por el tiempo que rigió en el día (precio_obs); neto_antes_envio con la misma regla que unidades_normal; neto solo en días con venta y envío medido (no hacer dropna sobre neto); precio_realizado incluye la reventa, precio_realizado_normal no; horas_disponible de disponibilidad_dia; devoluciones solo en ML; calidad y experiencia del día ANTERIOR de enrich.listing_health_hist (sin fila = no se midió); top de la categoría de enrich.market_highlights_hist (top guarda ids en orden: la posición es el ordinal); costo vigente al cierre del día (solo si la publicación tiene UN SKU); demanda_censurada = < 23.5 h disponibles (NULL = no se sabe). devoluciones por día en que se ABRIERON, no por día de venta. Al agregar columnas: create or replace con with (security_invoker = on) y las nuevas AL FINAL. Sin vista materializada hasta medir con EXPLAIN que hace falta.';
revoke all on pricing.panel_v from public, anon, authenticated, service_role;
grant select on pricing.panel_v to service_role;

create or replace view pricing.panel_semana_v with (security_invoker = on) as
select x.publicacion_id,
       x.canal,
       x.account_id,
       x.listing_id,
       date_trunc('week', x.fecha::timestamp)::date                                         as semana,
       count(*)                                                                               as n_dias_medidos,
       case when count(x.visitas) = count(*) then sum(x.visitas) end                          as visitas,
       sum(x.unidades_total)                                                                  as unidades_total,
       case when count(x.unidades_normal) = count(*) then sum(x.unidades_normal) end          as unidades_normal,
       sum(x.precio_cobrado * x.horas_disponible)
           filter (where x.precio_cobrado is not null and x.horas_disponible is not null)
         / nullif(sum(x.horas_disponible)
           filter (where x.precio_cobrado is not null and x.horas_disponible is not null), 0) as precio_cobrado_pond,
       avg(x.precio_cobrado)                                                                  as precio_cobrado_prom,
       sum(x.horas_disponible)                                                                as horas_disponible,
       bool_or(x.demanda_censurada)                                                           as alguna_censura,
       bool_or(x.hay_evento)                                                                  as hay_evento
  from pricing.panel_v x
 group by x.publicacion_id, x.canal, x.account_id, x.listing_id, date_trunc('week', x.fecha::timestamp)::date;
comment on view pricing.panel_semana_v is
  'El panel por semana ISO (lunes): el diario es casi todo ceros (~73 % de las publicaciones con < 1 visita al día). visitas y unidades_normal son NULL si falta algún día medido de la semana; precio_cobrado_pond pondera por horas disponibles; n_dias_medidos distingue una semana completa de una parcial.';
revoke all on pricing.panel_semana_v from public, anon, authenticated, service_role;
grant select on pricing.panel_semana_v to service_role;

-- ── La bitácora, para entender la toma de acciones ─────────────────────────
create or replace view pricing.accion_v with (security_invoker = on) as
select a.id,
       a.iniciada_at,
       a.terminada_at,
       a.actor,
       a.actor_tipo,
       a.actor_rol,
       a.registrada_por,
       a.registrada_at,
       a.origen_app,
       a.pantalla,
       a.tipo,
       a.estado,
       a.motivo,
       m.nombre                                                    as motivo_nombre,
       a.motivo_texto,
       a.regla_version,
       a.foto_generado,
       a.decision_id,
       dx.titulo                                                   as decision_titulo,
       a.deshace_id,
       a.reintenta_id,
       dh.id                                                       as deshecha_por,
       dh.intentos                                                 as intentos_deshacer,
       n.n_publicaciones,
       n.n_ok,
       n.n_parcial,
       n.n_error,
       n.n_siguio_recomendacion
  from pricing.accion a
  left join pricing.motivo m on m.codigo = a.motivo
  left join pricing.decision dx on dx.id = a.decision_id
  left join lateral (select min(x.id) filter (where x.estado in ('aplicada', 'parcial')) as id,
                            count(*)                                                     as intentos
                       from pricing.accion x
                      where x.deshace_id = a.id) dh on true
  cross join lateral (
         select count(*)                                          as n_publicaciones,
                count(*) filter (where ap.resultado = 'ok')         as n_ok,
                count(*) filter (where ap.resultado = 'parcial')    as n_parcial,
                count(*) filter (where ap.resultado = 'error')      as n_error,
                count(*) filter (where ap.precio_cobrado_pedido is not null and ap.precio_recomendado is not null
                                   and abs(ap.precio_cobrado_pedido - ap.precio_recomendado)
                                       <= greatest(1, 0.01 * ap.precio_recomendado)) as n_siguio_recomendacion
           from pricing.accion_publicacion ap
          where ap.accion_id = a.id) n;
comment on view pricing.accion_v is
  'Cada acción con su motivo (nombre del catálogo), su decisión, quién la anotó, el deshacer APLICADO que la regresó (y cuántos intentos hubo), y cuántas publicaciones salieron bien, parciales, con error y siguiendo la recomendación de la pantalla (±1 % o $1).';
revoke all on pricing.accion_v from public, anon, authenticated, service_role;
grant select on pricing.accion_v to service_role;

create or replace view pricing.accion_efecto_v with (security_invoker = on) as
select ap.accion_id,
       ap.publicacion_id,
       a.actor,
       a.actor_tipo,
       a.origen_app,
       a.tipo,
       a.motivo,
       ap.recomendacion_clave,
       a.decision_id,
       a.estado,
       ap.resultado,
       f.dia                                                       as fecha_accion,
       ap.precio_cobrado_antes::float8                             as precio_cobrado_antes,
       ap.precio_cobrado_pedido::float8                            as precio_cobrado_pedido,
       ap.precio_recomendado::float8                               as precio_recomendado,
       ap.precio_cobrado_despues::float8                           as precio_cobrado_despues,
       case when ap.precio_cobrado_pedido is not null and ap.precio_recomendado is not null
            then abs(ap.precio_cobrado_pedido - ap.precio_recomendado) <= greatest(1, 0.01 * ap.precio_recomendado) end
                                                                   as siguio_recomendacion,
       ap.hasta_pedido,
       rg.dia                                                      as regresada_dia,
       (coalesce(rg.dia, h.hoy) - f.dia)                           as dias_vigente,
       (f.dia + 14 < h.hoy)                                        as ventana_despues_completa,
       least(14, greatest(0, h.hoy - f.dia - 1))                   as dias_despues_transcurridos,
       pa.dias_foto                                                as dias_foto_antes,
       pd.dias_foto                                                as dias_foto_despues,
       pa.precio_prom::float8                                      as precio_prom_antes,
       pd.precio_prom::float8                                      as precio_prom_despues,
       ra.visitas                                                  as visitas_antes,
       case when f.dia + 14 < h.hoy then rd.visitas end            as visitas_despues,
       va.unidades_total                                           as unidades_antes,
       case when f.dia + 14 < h.hoy then vd.unidades_total end     as unidades_despues,
       case when ca.clasificable then va.unidades_normal end       as unidades_normal_antes,
       case when f.dia + 14 < h.hoy and cd.clasificable then vd.unidades_normal end   as unidades_normal_despues,
       case when ca.clasificable then va.unidades_reventa end      as unidades_reventa_antes,
       case when f.dia + 14 < h.hoy and cd.clasificable then vd.unidades_reventa end  as unidades_reventa_despues,
       case when ra.visitas > 0 and ca.clasificable then va.unidades_normal::float8 / ra.visitas end           as conversion_normal_antes,
       case when f.dia + 14 < h.hoy and rd.visitas > 0 and cd.clasificable
            then vd.unidades_normal::float8 / rd.visitas end                                                   as conversion_normal_despues,
       case when ra.visitas > 0 then va.unidades_total::float8 / ra.visitas end                               as conversion_total_antes,
       case when f.dia + 14 < h.hoy and rd.visitas > 0 then vd.unidades_total::float8 / rd.visitas end         as conversion_total_despues,
       va.ingreso_total::float8                                    as ingreso_antes,
       case when f.dia + 14 < h.hoy then vd.ingreso_total::float8 end                                          as ingreso_despues,
       case when ca.clasificable then va.ingreso_normal::float8 end                                            as ingreso_normal_antes,
       case when f.dia + 14 < h.hoy and cd.clasificable then vd.ingreso_normal::float8 end                     as ingreso_normal_despues,
       da.horas::float8                                            as horas_disponible_antes,
       case when f.dia + 14 < h.hoy then dd.horas::float8 end      as horas_disponible_despues,
       da.censurados                                               as dias_censurados_antes,
       case when f.dia + 14 < h.hoy then dd.censurados end         as dias_censurados_despues,
       ot.otra_accion                                              as otra_accion_en_ventana,
       oc.otro_cambio                                              as otro_cambio_en_ventana
  from pricing.accion_publicacion ap
  join pricing.accion a on a.id = ap.accion_id
  join pricing.publicacion p on p.id = ap.publicacion_id
  join core.accounts acc on acc.id = p.account_id
  cross join lateral (select (coalesce(a.terminada_at, a.iniciada_at) at time zone 'America/Mexico_City')::date as dia) f
  cross join lateral (select (now() at time zone 'America/Mexico_City')::date as hoy) h
  -- el día en que un deshacer APLICADO regresó esta publicación
  left join lateral (select min((coalesce(x.terminada_at, x.iniciada_at) at time zone 'America/Mexico_City')::date) as dia
                       from pricing.accion x
                       join pricing.accion_publicacion xp on xp.accion_id = x.id and xp.publicacion_id = ap.publicacion_id
                      where x.deshace_id = ap.accion_id and x.estado in ('aplicada', 'parcial')
                        and xp.resultado in ('ok', 'parcial')) rg on true
  -- 14 días ANTES y 14 DESPUÉS, sin el día de la acción
  cross join lateral (select count(*) as dias_foto, avg(d.precio_cobrado) as precio_prom
                        from pricing.listing_dia d
                       where d.publicacion_id = ap.publicacion_id and d.fecha between f.dia - 14 and f.dia - 1) pa
  cross join lateral (select count(*) as dias_foto, avg(d.precio_cobrado) as precio_prom
                        from pricing.listing_dia d
                       where d.publicacion_id = ap.publicacion_id and d.fecha between f.dia + 1 and f.dia + 14) pd
  cross join lateral (select case when count(r.visitas) = 14 then sum(r.visitas) end as visitas
                        from pricing.resultado_dia r
                       where r.publicacion_id = ap.publicacion_id and r.fecha between f.dia - 14 and f.dia - 1) ra
  cross join lateral (select case when count(r.visitas) = 14 then sum(r.visitas) end as visitas
                        from pricing.resultado_dia r
                       where r.publicacion_id = ap.publicacion_id and r.fecha between f.dia + 1 and f.dia + 14) rd
  -- la reventa se puede separar solo si el lector de órdenes cubrió los 14 días (en ML)
  cross join lateral (select (acc.channel_id <> 'mercado_libre'
                              or (select count(distinct c.fecha) from pricing.captura c
                                   where c.grupo = 'ordenes' and c.terminada_at is not null
                                     and c.fecha between f.dia - 14 and f.dia - 1
                                     and (c.account_id is null or c.account_id = p.account_id)) = 14) as clasificable) ca
  cross join lateral (select (acc.channel_id <> 'mercado_libre'
                              or (select count(distinct c.fecha) from pricing.captura c
                                   where c.grupo = 'ordenes' and c.terminada_at is not null
                                     and c.fecha between f.dia + 1 and f.dia + 14
                                     and (c.account_id is null or c.account_id = p.account_id)) = 14) as clasificable) cd
  cross join lateral (select coalesce(sum(v.unidades_total), 0) as unidades_total,
                             case when bool_or(v.unidades_normal is null) then null else coalesce(sum(v.unidades_normal), 0) end   as unidades_normal,
                             case when bool_or(v.unidades_reventa is null) then null else coalesce(sum(v.unidades_reventa), 0) end as unidades_reventa,
                             coalesce(sum(v.ingreso_total), 0) as ingreso_total,
                             case when bool_or(v.ingreso_normal is null) then null else coalesce(sum(v.ingreso_normal), 0) end     as ingreso_normal
                        from pricing.ventas_dia_v v
                       where v.listing_id = p.listing_id and v.account_id = p.account_id
                         and v.fecha between f.dia - 14 and f.dia - 1) va
  cross join lateral (select coalesce(sum(v.unidades_total), 0) as unidades_total,
                             case when bool_or(v.unidades_normal is null) then null else coalesce(sum(v.unidades_normal), 0) end   as unidades_normal,
                             case when bool_or(v.unidades_reventa is null) then null else coalesce(sum(v.unidades_reventa), 0) end as unidades_reventa,
                             coalesce(sum(v.ingreso_total), 0) as ingreso_total,
                             case when bool_or(v.ingreso_normal is null) then null else coalesce(sum(v.ingreso_normal), 0) end     as ingreso_normal
                        from pricing.ventas_dia_v v
                       where v.listing_id = p.listing_id and v.account_id = p.account_id
                         and v.fecha between f.dia + 1 and f.dia + 14) vd
  cross join lateral (select case when count(x.horas_disponible) = 14 then sum(x.horas_disponible) end as horas,
                             count(*) filter (where x.horas_disponible < 23.5)                       as censurados
                        from pricing.disponibilidad_dia x
                       where x.publicacion_id = ap.publicacion_id and x.fecha between f.dia - 14 and f.dia - 1) da
  cross join lateral (select case when count(x.horas_disponible) = 14 then sum(x.horas_disponible) end as horas,
                             count(*) filter (where x.horas_disponible < 23.5)                       as censurados
                        from pricing.disponibilidad_dia x
                       where x.publicacion_id = ap.publicacion_id and x.fecha between f.dia + 1 and f.dia + 14) dd
  -- otra acción que quedó en la misma publicación a ±14 días (el efecto se mezcla)
  cross join lateral (select exists (select 1
                                       from pricing.accion_publicacion ap2
                                       join pricing.accion a2 on a2.id = ap2.accion_id
                                      where ap2.publicacion_id = ap.publicacion_id and ap2.accion_id <> ap.accion_id
                                        and a2.tipo not in ('nota', 'revisada_sin_cambio')
                                        and ap2.resultado in ('ok', 'parcial', 'regresada')
                                        and abs(((coalesce(a2.terminada_at, a2.iniciada_at) at time zone 'America/Mexico_City')::date) - f.dia) <= 14)
                             as otra_accion) ot
  -- otro cambio OBSERVADO sin esta acción (campañas del canal, Seller Center…) a ±14 días
  cross join lateral (select (exists (select 1 from pricing.precio_obs po
                                       where po.publicacion_id = ap.publicacion_id
                                         and po.accion_id is distinct from ap.accion_id
                                         and abs(((po.desde at time zone 'America/Mexico_City')::date) - f.dia) <= 14)
                           or exists (select 1 from pricing.oferta o
                                       where o.publicacion_id = ap.publicacion_id
                                         and ((o.accion_id is distinct from ap.accion_id and o.inicio is not null
                                               and abs(((o.inicio at time zone 'America/Mexico_City')::date) - f.dia) <= 14)
                                           or (o.fin_accion_id is distinct from ap.accion_id and o.fin is not null
                                               and abs(((o.fin at time zone 'America/Mexico_City')::date) - f.dia) <= 14))))
                             as otro_cambio) oc
 where a.estado in ('aplicada', 'parcial', 'deshecha')
   and ap.resultado in ('ok', 'parcial', 'regresada');
comment on view pricing.accion_efecto_v is
  'Qué pasó alrededor de cada acción aplicada, por publicación: 14 días ANTES contra 14 DESPUÉS (sin el día de la acción). Lo de DESPUÉS es NULL hasta que la ventana termina (ventana_despues_completa, dias_despues_transcurridos). Separa normal y reventa (NULL si el lector de órdenes no cubrió los 14 días; ingreso_* y conversion_total_* SÍ incluyen la reventa); visitas NULL si falta un día medido; horas con stock y días censurados de disponibilidad_dia; siguio_recomendacion (±1 % o $1 del precio recomendado); regresada_dia y dias_vigente si un deshacer la regresó. otra_accion_en_ventana y otro_cambio_en_ventana avisan que el efecto se mezcla. NO es causal: comparar contra publicaciones parecidas sin acción (panel_v, decision_dia_v) antes de concluir.';
revoke all on pricing.accion_efecto_v from public, anon, authenticated, service_role;
grant select on pricing.accion_efecto_v to service_role;

create or replace view pricing.actor_resumen_v with (security_invoker = on) as
select a.actor,
       a.actor_tipo,
       a.origen_app,
       a.tipo,
       count(*)                                                      as acciones,
       count(*) filter (where a.estado in ('aplicada', 'parcial'))  as aplicadas,
       count(*) filter (where a.estado = 'error')                   as con_error,
       count(*) filter (where a.estado = 'deshecha')                as deshechas,
       count(*) filter (where a.motivo is not null)                 as con_motivo,
       count(*) filter (where a.decision_id is not null)            as con_decision,
       sum(n.n_publicaciones)                                        as publicaciones_tocadas,
       sum(n.n_siguio)                                               as publicaciones_siguiendo_recomendacion,
       min(a.iniciada_at)                                            as primera,
       max(a.iniciada_at)                                            as ultima
  from pricing.accion a
  cross join lateral (select count(*) as n_publicaciones,
                             count(*) filter (where ap.precio_cobrado_pedido is not null and ap.precio_recomendado is not null
                                                and abs(ap.precio_cobrado_pedido - ap.precio_recomendado)
                                                    <= greatest(1, 0.01 * ap.precio_recomendado)) as n_siguio
                        from pricing.accion_publicacion ap where ap.accion_id = a.id) n
 group by a.actor, a.actor_tipo, a.origen_app, a.tipo;
comment on view pricing.actor_resumen_v is
  'Por persona (o proceso), su tipo, por dónde entró y tipo de acción: cuántas hizo, cuántas se aplicaron, fallaron o se deshicieron, cuántas traen motivo y decisión, cuántas publicaciones tocó y en cuántas siguió la recomendación. Para entender cómo actúa cada quien, no para calificarlo: el efecto se mide en accion_efecto_v.';
revoke all on pricing.actor_resumen_v from public, anon, authenticated, service_role;
grant select on pricing.actor_resumen_v to service_role;

-- ───────────────────────────────────────────────────────────────────────────
-- 20) Cinturón: secuencias de las columnas identity (un INSERT no las necesita)
-- ───────────────────────────────────────────────────────────────────────────
revoke all on all sequences in schema pricing from public, anon, authenticated;

-- ───────────────────────────────────────────────────────────────────────────
-- 21) Autoverificación: si algo no quedó como se declara, no hay COMMIT
--     (no depende del análisis estático: mira la base)
-- ───────────────────────────────────────────────────────────────────────────
do $verifica$
declare
  malos text;
begin
  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind in ('r', 'p') and not c.relrowsecurity;
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: tablas de pricing sin RLS: ' || malos;
  end if;

  if has_schema_privilege('anon', 'pricing', 'usage') or has_schema_privilege('authenticated', 'pricing', 'usage') then
    raise exception using errcode = 'KB000', message = '0074: anon o authenticated tienen USAGE en pricing';
  end if;

  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind in ('r', 'p', 'v', 'm')
     and (has_table_privilege('anon', c.oid, 'select, insert, update, delete, truncate, references, trigger, maintain')
          or has_table_privilege('authenticated', c.oid, 'select, insert, update, delete, truncate, references, trigger, maintain'));
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: anon o authenticated con privilegios en: ' || malos;
  end if;

  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'S'
     and (has_sequence_privilege('anon', c.oid, 'usage, select, update')
          or has_sequence_privilege('authenticated', c.oid, 'usage, select, update'));
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: anon o authenticated con privilegios en las secuencias: ' || malos;
  end if;

  select string_agg(p.proname, ', ' order by p.proname) into malos
    from pg_catalog.pg_proc p
   where p.pronamespace = 'pricing'::regnamespace
     and (has_function_privilege('anon', p.oid, 'execute') or has_function_privilege('authenticated', p.oid, 'execute'));
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: anon o authenticated pueden ejecutar: ' || malos;
  end if;

  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'v'
     and not (coalesce(c.reloptions, '{}'::text[]) && array['security_invoker=on', 'security_invoker=true']);
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: vistas de pricing sin security_invoker: ' || malos;
  end if;

  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'r'
     and has_table_privilege('service_role', c.oid, 'truncate, references, trigger, maintain');
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: service_role puede vaciar o colgar triggers en: ' || malos;
  end if;

  select string_agg(c.relname, ', ' order by c.relname) into malos
    from pg_catalog.pg_class c
   where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'r'
     and not exists (select 1 from pg_catalog.pg_trigger t
                      where t.tgrelid = c.oid and not t.tgisinternal and (t.tgtype & 32) <> 0);
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: tablas de pricing sin candado contra TRUNCATE: ' || malos;
  end if;

  select string_agg(pol.tablename || '.' || pol.policyname, ', ') into malos
    from pg_catalog.pg_policies pol where pol.schemaname = 'pricing';
  if malos is not null then
    raise exception using errcode = 'KB000', message = '0074: pricing debe tener CERO políticas (patrón de la casa): ' || malos;
  end if;

  if exists (select 1 from pg_catalog.pg_default_acl d
              where d.defaclrole = 'postgres'::regrole
                and (d.defaclnamespace = 0::oid or d.defaclnamespace = 'pricing'::regnamespace::oid)
                and array_to_string(d.defaclacl, ',') ~ '(^|,)(anon|authenticated)=') then
    raise exception using errcode = 'KB000', message = '0074: un ACL por omisión de postgres le da algo a anon o authenticated';
  end if;
end
$verifica$;

insert into ops.migraciones (migracion, detalle)
select '0074_pricing_historial',
       jsonb_build_object('version', '3.3',
                          'tablas', (select count(*) from pg_catalog.pg_class c
                                      where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'r'),
                          'vistas', (select count(*) from pg_catalog.pg_class c
                                      where c.relnamespace = 'pricing'::regnamespace and c.relkind = 'v'))
 where to_regclass('ops.migraciones') is not null
   and not exists (select 1 from ops.migraciones where migracion = '0074_pricing_historial');

commit;

-- VERIFICACIÓN (después del COMMIT, o dentro del ensayo antes del ROLLBACK)
--   select count(*) filter (where relkind = 'r'), count(*) filter (where relkind = 'v')
--     from pg_class where relnamespace = 'pricing'::regnamespace;            -- 28 · 15
--   select relname from pg_class where relnamespace = 'pricing'::regnamespace
--      and relkind = 'r' and not relrowsecurity;                            -- 0 filas
--   select has_table_privilege('anon', 'pricing.listing_dia', 'select');   -- false
--   select has_table_privilege('service_role', 'pricing.listing_dia', 'delete'); -- false (solo agregar y completar)
--   select conrelid::regclass, contype, count(*) from pg_constraint
--    where connamespace = 'pricing'::regnamespace group by 1, 2 order by 1, 2;
--   Candados (dentro de una transacción con ROLLBACK):
--     delete from pricing.publicacion;         -- 42501 publicacion_sin_borrado
--     truncate pricing.oferta_dia;             -- 42501 oferta_dia_solo_agregar
--   explain (analyze, buffers) select * from pricing.panel_v where fecha >= current_date - 30;
--   python backend/scripts/verificar_rls.py   -- exit 0 CON 'pricing' en ESQUEMAS_NEGOCIO
--   select * from ops.migraciones where migracion = '0074_pricing_historial';  -- 1 fila
--
-- REVERSA (solo mientras esté VACÍA; sin cascade a propósito; antes, revertir el
-- código que escriba en pricing; fuera de hora pico: borra los triggers de las
-- llaves foráneas y toma ACCESS EXCLUSIVE breve sobre core.accounts y
-- core.channels; si sale 55P03, reintentar)
--   begin;
--   set local lock_timeout = '2s';
--   set local statement_timeout = '30s';
--   do $$ begin
--     if exists (select 1 from pricing.listing_dia) or exists (select 1 from pricing.oferta_dia)
--        or exists (select 1 from pricing.precio_obs) or exists (select 1 from pricing.evento)
--        or exists (select 1 from pricing.decision) or exists (select 1 from pricing.modelo_corrida)
--        or exists (select 1 from pricing.accion) then
--       raise exception 'pricing tiene historia: pg_dump -n pricing y acta antes de tirarlo';
--     end if;
--   end $$;
--   drop view if exists pricing.actor_resumen_v, pricing.accion_efecto_v, pricing.accion_v, pricing.panel_semana_v, pricing.panel_v, pricing.seguimiento_hoy_v, pricing.cambio_v,
--     pricing.decision_dia_v, pricing.decision_v, pricing.experiencia_razon_v, pricing.competencia_dia_v,
--     pricing.costo_vigencia_v, pricing.precio_dia_v, pricing.ventas_dia_v, pricing.venta_linea_v;
--   drop table if exists pricing.disponibilidad_dia, pricing.orden_etiqueta, pricing.modelo_coef, pricing.modelo_corrida, pricing.parametro_canal,
--     pricing.calendario, pricing.cuenta_dia, pricing.evento, pricing.seguimiento, pricing.listing_rival,
--     pricing.rival_precio, pricing.rival, pricing.ads_dia, pricing.resultado_dia, pricing.listing_dia,
--     pricing.oferta_dia, pricing.oferta, pricing.precio_obs, pricing.listing_ficha, pricing.publicacion_sku,
--     pricing.accion_publicacion, pricing.accion, pricing.motivo,
--     pricing.decision_listing, pricing.decision, pricing.promocion, pricing.publicacion, pricing.captura;
--   drop function if exists pricing.tg_cobra_iniciada(), pricing.tg_sin_borrado(), pricing.sin_secretos(text),
--     pricing.tg_solo_completar(), pricing.tg_sin_traslape(), pricing.tg_captura_coherente(),
--     pricing.tg_llave_inmutable(), pricing.tg_oferta_tipo(), pricing.tg_decision_listing_guarda(),
--     pricing.tg_promocion_coherente(), pricing.tg_promocion_tipo_propaga(), pricing.tg_decision_estado_guarda(),
--     pricing.tg_solo_atribuir(), pricing.tg_resultado_cerrado(), pricing.tg_accion_estado(),
--     pricing.tg_accion_alta(), pricing.tg_accion_coherente(), pricing.tg_accion_publicacion_alta(),
--     pricing.tg_accion_publicacion_resultado(), pricing.tg_accion_publicacion_decision(), pricing.tg_atribucion();
--   drop index if exists enrich.order_shipping_cost_envio_idx;
--   drop schema if exists pricing;   -- sin cascade: truena si algo de afuera depende de pricing
--   commit;
--   La fila de ops.migraciones se queda (es de solo agregar): una reaplicación
--   futura va con otro número.
