-- ═══════════════════════════════════════════════════════════════════════════
-- 0019 — `channel.categories`: si la categoría es HOJA y si está DISPONIBLE.
--
-- Estado: NO APLICADA al escribirse. Sandbox primero.
--
-- QUÉ RESUELVE
-- ------------
-- `cargar_tiktok.py` ya lee los dos datos de la entrega de TikTok y los TIRA en
-- cada corrida — su propia salida lo dice: *"is_leaf, permission_status y
-- publicable NO SE GUARDAN: channel.categories no tiene esas columnas"*.
--
-- El que duele es el segundo. Las categorías `INVITE_ONLY` **no rechazan el
-- producto**: lo aceptan y lo dejan en `PENDING` para siempre, sin error y sin
-- aviso. Medido: de las 900 publicaciones de TikTok, las que quedaron en
-- categorías restringidas nunca llegaron a estar a la venta — y los 7 productos
-- en `PENDING` de toda la tienda son exactamente ésos (verificado: 900 = 599
-- DRAFT + 283 ACTIVATE + 11 FAILED + 7 PENDING).
--
-- Sin este dato, la vista previa solo puede decir "no se puede verificar". Con
-- él, bloquea de verdad.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- LAS TRES DECISIONES, Y POR QUÉ (revisadas por el consejo)
-- ═══════════════════════════════════════════════════════════════════════════
--
-- (A) `disponibilidad` es TEXT, no boolean.
--
--     La propuesta original pedía `disponible boolean`. Un boolean **aplasta el
--     porqué**: TikTok entrega `AVAILABLE` / `INVITE_ONLY` y podría sumar
--     estados; además 35 de las 451 `INVITE_ONLY` ni siquiera son hoja, así que
--     hay DOS razones distintas por las que una categoría no sirve.
--
--     Es el mismo criterio que ya sigue esta base: `channel.listings` guarda
--     `status` Y `situacion` por separado porque —cita del propio código—
--     "aplastarlas en una columna perdería justo la que explica por qué algo no
--     se vende". Y el mismo de `field_requirements.campo` (nativo) +
--     `campo_canonico`: se conserva el valor del canal para poder auditarlo.
--
--     `publicable` NO lleva columna: se deriva
--     (`is_leaf and disponibilidad = 'AVAILABLE'`). Guardarlo sería un tercer
--     lugar donde la misma verdad puede desincronizarse.
--
-- (B) Los nombres van en INGLÉS, como el resto de ESTA tabla.
--
--     La propuesta los pedía en español por la regla "un concepto, un nombre".
--     La regla es correcta pero apunta a otra cosa: es **no usar el vocabulario
--     nativo del canal** (`permission_status`, `is_final_category`), no elegir
--     idioma. Un nombre genérico en inglés cumple las dos.
--
--     `channel.categories` es 100% inglés (`channel_id`, `category_id`, `name`,
--     `path`, y las tres que agregó la 0012: `parent_id`, `root_id`,
--     `root_name`). El esquema global es bilingüe y ya arrastra un caso de
--     vocabulario duplicado para el mismo concepto — `is_fulfillment` en
--     `channel.listings` contra `es_fulfillment` en `channel.order_items`.
--     Meter español aquí repetiría ese defecto DENTRO de una tabla consistente.
--
--     (`disponibilidad` se queda en español por petición explícita: es el único
--     que se apartó de la convención, a sabiendas.)
--
-- (C) El número 0019 se toma, y NO se renumera ningún 0018.
--
--     Hay dos archivos `0018` y también dos `0004`. No rompe nada:
--     `aplicar_migraciones.py:93` aplica con `sorted(glob("*.sql"))` — orden
--     alfabético por nombre COMPLETO, no por número. Verificado.
--
--     Y `0018_channel_field_requirements.sql` YA ESTÁ APLICADA en producción:
--     renumerar una migración aplicada desincroniza el ledger. Su encabezado
--     decía "NO APLICADA" (quedó rancio); se corrige en este mismo commit para
--     que nadie intente "arreglar" el número.
-- ═══════════════════════════════════════════════════════════════════════════

alter table channel.categories
  add column if not exists is_leaf        boolean,
  add column if not exists disponibilidad text;

comment on column channel.categories.is_leaf is
  'Categoría HOJA. Solo en hojas se publica: las intermedias devuelven '
  '"12052024 Category is not final category" en TikTok. NULL = no se sabe — '
  'para ML es RELLENABLE (una categoría es hoja si ninguna fila la tiene como '
  'parent_id), no es "no aplica".';

comment on column channel.categories.disponibilidad is
  'Acceso de NUESTRA tienda a la categoría, con el valor NATIVO del canal '
  '(TikTok: AVAILABLE | INVITE_ONLY). Las INVITE_ONLY NO rechazan el producto: '
  'lo aceptan y lo dejan en PENDING para siempre, sin error. NULL = el canal no '
  'expone este dato (p. ej. Mercado Libre). `publicable` se DERIVA: '
  'is_leaf and disponibilidad = ''AVAILABLE''.';


-- ═══════════════════════════════════════════════════════════════════════════
-- ESTA MIGRACIÓN NO SIEMBRA DATOS. A PROPÓSITO.
--
-- El UPDATE de las 2,168 filas de TikTok lo hace `cargar_tiktok.py`, que ya
-- tiene el dato y es idempotente. Mismo criterio que la 0018: la migración crea
-- el destino, el cargador lo llena.
--
-- GAP CONOCIDO, igual que el que advirtió la 0012: una categoría de TikTok que
-- entre por otra vía nacerá con estas dos columnas en NULL y nadie las llenará
-- salvo re-correr el cargador. Si aparece un ETL de categorías de TikTok, tiene
-- que traer estos dos campos o dejará huecos silenciosos.
-- ═══════════════════════════════════════════════════════════════════════════

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICACIÓN tras aplicar y correr el cargador
--
--   select disponibilidad, is_leaf, count(*)
--     from channel.categories where channel_id = 'tiktok'
--    group by 1, 2 order by 1, 2;
--   -- esperado: 2,168 filas repartidas; 1,937 con is_leaf = true
--
--   -- Las publicables (lo que la vista previa debe dejar pasar):
--   select count(*) from channel.categories
--    where channel_id = 'tiktok' and is_leaf and disponibilidad = 'AVAILABLE';
--
--   -- Las trampa: hoja PERO restringida — aceptan el producto y lo dejan en
--   -- PENDING sin decir nada.
--   select count(*) from channel.categories
--    where channel_id = 'tiktok' and is_leaf and disponibilidad = 'INVITE_ONLY';
--
--   -- ML sigue en NULL (no es "no aplica": is_leaf es rellenable ahí):
--   select count(*) from channel.categories
--    where channel_id = 'mercado_libre' and is_leaf is null;   -- 2,692
--
-- ROLLBACK
--   alter table channel.categories
--     drop column if exists is_leaf,
--     drop column if exists disponibilidad;
-- ═══════════════════════════════════════════════════════════════════════════
