-- 0024_ops_tiktok_tokens.sql — PASO 6b: el TERCER almacén de credenciales.
--
-- CÓMO APARECIÓ
-- -------------
-- No lo buscábamos. Salió del triaje de los 95 `try/except` que se tragan un
-- fallo de MySQL (20-ago): al abrir los 15 sospechosos, catorce se cayeron
-- —ocho ya cubiertos por bandera, seis inofensivos— y quedó uno solo, que no
-- estaba en ninguna lista del plan de migración.
--
-- `tiktok_tokens` vive en el MySQL que estamos retirando. 1 fila, escrita el
-- 18-ago: viva. Y kubera no tenía dónde ponerla — solo existía `ops.ml_tokens`.
--
-- Es la misma lección de siempre en este proyecto: creíamos que el bloqueador
-- de credenciales era uno (Mercado Libre) y eran dos.
--
-- POR QUÉ ES PEOR QUE EL DE ML, AUNQUE SEA MÁS CHICO
-- ---------------------------------------------------
-- El de ML falla de frente: sin token, no hay llamada. El de TikTok falla
-- DISFRAZADO. Sin `shop_cipher`, TikTok contesta `shop_cipher is required`
-- aunque el token sea válido — y el propio comentario de `services/tiktok.py`
-- ya lo advertía: *"parece un problema de permisos"*. El día del corte, alguien
-- perdería horas revisando permisos en el panel de TikTok.
--
-- QUÉ SE COPIA Y QUÉ NO
-- ---------------------
-- Se copia la fila entera MENOS las llaves de la app (`tiktok_app_key`,
-- `tiktok_app_secret`, `tiktok_service_id`), que ya viven en variables de
-- entorno y ahí se quedan — mismo criterio que la 0023 con el `client_secret`
-- de ML: un secreto va en el entorno, no en una tabla.
--
-- `access_token` y `refresh_token` van CIFRADOS con la misma llave Fernet, como
-- en MySQL. `services/tiktok.py::_cifrar` ya se negaba a guardarlos en claro sin
-- `DB_ENCRYPTION_KEY`; esa guarda no cambia.
--
-- LA PK ES `shop_id`, NO UN AUTOINCREMENT
-- ---------------------------------------
-- En MySQL la tabla tiene `id BIGINT AUTO_INCREMENT` y un `UNIQUE KEY uq_shop
-- (shop_id)`. El id no lo usa nadie: todas las consultas van por `shop_id` o por
-- `ORDER BY updated_at`. Aquí el `shop_id` es la llave y ya — un autoincrement
-- que nadie lee es una columna que solo sirve para que dos filas de la misma
-- tienda puedan coexistir.
--
-- IDEMPOTENTE: se puede re-aplicar sin daño.

create table if not exists ops.tiktok_tokens (
    shop_id        text        primary key,
    seller_name    text,
    open_id        text,
    shop_cipher    text,
    access_token   text        not null,
    refresh_token  text,
    expira         timestamptz,
    refresh_expira timestamptz,
    updated_at     timestamptz not null default now()
);

comment on table ops.tiktok_tokens is
  'Credenciales de TikTok Shop. Reemplaza tiktok_tokens del MySQL kubera_ml. '
  'access_token y refresh_token van cifrados con la llave Fernet del backend '
  '(DB_ENCRYPTION_KEY). Las llaves de la APP viven en variables de entorno.';

comment on column ops.tiktok_tokens.shop_cipher is
  'Parametro obligatorio de casi toda la API de TikTok que no sea catalogo '
  'publico. Sin el, una conexion con token VALIDO contesta "shop_cipher is '
  'required" y parece un problema de permisos: falla disfrazado.';
