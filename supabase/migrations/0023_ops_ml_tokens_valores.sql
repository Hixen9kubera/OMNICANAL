-- 0023_ops_ml_tokens_valores.sql — PASO 6: que los tokens de ML puedan vivir
-- fuera de MySQL.
--
-- POR QUÉ ESTA MIGRACIÓN EXISTE
-- -----------------------------
-- `ops.ml_tokens` se creó pensando en Vault: sus columnas son
-- `vault_access_secret` / `vault_refresh_secret`, o sea REFERENCIAS a secretos,
-- no los valores. Y lleva vacía desde que se creó.
--
-- Mientras tanto, toda la autenticación de Mercado Libre vive en el MySQL que
-- estamos retirando (`ml_tokens` y `ml_tokens_dashboard`). Medido el 19-ago con
-- `probar_corte_total.py`: apagar MySQL hoy deja `meli._access_token()`
-- devolviendo `None`, y sin token no hay API de ML — se van las ventas por
-- webhook, publicar, el sync de inventario y competencia.
--
-- Ir a Vault de una vez sería mezclar dos proyectos. Lo que desbloquea el
-- retiro es MUCHO más chico: que el token pueda guardarse aquí igual que hoy se
-- guarda allá — cifrado con la misma llave Fernet del backend. Las columnas de
-- Vault SE QUEDAN, sin usar, para cuando toque esa mudanza.
--
-- LO QUE **NO** SE GUARDA AQUÍ, A PROPÓSITO
-- ------------------------------------------
-- `app_id` y `client_secret`. En MySQL viven en una fila de
-- `ml_tokens_dashboard`, y ese `client_secret` es justo el que está EXPUESTO en
-- el repo `publicador` y pendiente de rotación. Copiarlo a una tabla nueva sería
-- esparcir un secreto quemado.
--
-- Salen de las variables de entorno (`MELI_APP_ID` / `MELI_CLIENT_SECRET`), que
-- es donde va un secreto. El código ya tenía ese camino: era el fallback de
-- `_credenciales_refresh`. Aquí deja de ser el fallback y pasa a ser el camino.
--
-- IDEMPOTENTE: se puede re-aplicar sin daño.

alter table ops.ml_tokens
  add column if not exists access_token  text,
  add column if not exists refresh_token text;

comment on column ops.ml_tokens.access_token is
  'Token de acceso de ML, cifrado con la llave Fernet del backend (misma forma '
  'que MySQL ml_tokens.access_token). Provisional hasta que se mude a Vault, '
  'que es para lo que existen las columnas vault_*.';

comment on column ops.ml_tokens.refresh_token is
  'Refresh token de ML, cifrado. OJO: ML LO ROTA EN CADA USO, asi que dos '
  'procesos que renueven se invalidan mutuamente. Escribir en los dos lados es '
  'seguro (es el mismo valor); RENOVAR desde los dos, no.';
