-- ═══════════════════════════════════════════════════════════════════════════
-- 0067 — Se quita la vista ops.devoluciones_vs_canal_v (de la 0065).
--        Fase 0b del reorden de esquemas (REORDEN_ESQUEMAS_VENTAS_ALMACEN §4.2-5
--        y §6), aprobado por Eduardo el 8-oct-2026 (D6: devoluciones).
-- ═══════════════════════════════════════════════════════════════════════════
--
-- POR QUÉ
--   La vista cruzaba lo recibido en REVISION (ops.devoluciones) contra
--   channel.return_items. ops.devoluciones tiene 0 filas y no tiene escritor, así
--   que la vista nunca devolvió nada; y DEPENDE de channel.return_items (canal,
--   cuenta, external_return_id, sku, cantidad): mientras exista, cualquier ALTER
--   de esas columnas o la reversa de la 0049 truena (2BP01 / 0A000). El trabajo
--   de devoluciones de ML (v0.626.0) y la trazabilidad (v0.627.0) van sobre
--   channel.returns, así que la vista estorba. Va numerada ANTES de cualquier
--   ALTER de channel.return_items.
--
-- LECTORES (git grep sobre origin/main f0471c6, 8-oct-2026)
--   · Ninguno en runtime: ni backend, ni frontend, ni el scheduler. La vigía de
--     la pestaña Bodegas (services/fanout_bodegas.py) lee SOLO
--     ops.stock_apartado_descuadre_v y ya pregunta to_regclass.
--   · backend/scripts/verificar_0064_0065.py: la revisa solo si existe.
--   · backend/tests/test_ordenes_venta_bd.py: aplica esta migración después de la
--     0065 y ya no la lee.
--   · docs/MIGRACION_0064_0065_GUIA_AGENTE.md y supabase/schema_manifest.json: al día.
--   Nada depende de ella en la base (pg_depend: 0 vistas encima, medido en el
--   sandbox el 8-oct; tampoco funciones que la nombren ni pg_cron, y en
--   pg_stat_statements del sandbox solo aparece DDL).
--   · Fuera del repo: _paso_prod_ov_0064_0065/aplicar_0064_0065.py (el aplicador
--     del acta del 6-oct) la trata como obligatoria. Queda HISTÓRICO: tras esta
--     migración su revisión da «parcial» y su --ensayo --desde-cero truena en
--     QUITAR. No se edita (lo cubre SHA256SUMS y el acta está firmada); hay una
--     nota LEEME_DESPUES_DE_0067.txt junto a él.
--
-- PRECHEQUEO DEL ACTA EN PRODUCCIÓN (solo lectura; git y pg_depend no ven
-- cuerpos plpgsql ni clientes de fuera del repo — así apareció restock_panel):
--   begin; set transaction read only;
--   select calls, left(query, 200) from extensions.pg_stat_statements
--    where query ilike '%devoluciones_vs_canal_v%'
--      and query !~* '^\s*(--|create|alter|comment|grant|revoke|drop)';
--   select p.oid::regprocedure from pg_proc p where p.prosrc ilike '%devoluciones_vs_canal%';
--   select c.oid::regclass from pg_depend d join pg_rewrite r on r.oid = d.objid
--     join pg_class c on c.oid = r.ev_class
--    where d.refobjid = 'ops.devoluciones_vs_canal_v'::regclass and c.oid <> d.refobjid;
--   rollback;
--   Solo se sigue si los únicos aciertos de pg_stat_statements son los
--   `select count(*)` del aplicador y del verificador del 6-oct, y las otras dos
--   consultas vienen vacías. Cualquier otro lector: se detiene y se avisa.
--
-- QUÉ NO TOCA
--   ops.devoluciones, ops.tg_devoluciones_guarda, ops.tg_devolucion_de_mas, el
--   trigger stock_mov_devolucion_cuadra, la bodega REVISION ni la vigía
--   principal ops.stock_apartado_descuadre_v (cuya CTE `rv` sigue leyendo
--   ops.devoluciones): eso es la Fase 1, con su acta. De la vigía principal solo
--   se reescribe su COMMENT, que decía «junto con ops.devoluciones_vs_canal_v»
--   (un agente que lo leyera buscaría una segunda vigía que ya no existe).
--
-- OJO, MIENTRAS EXISTA LA 0065 TAL CUAL: re-correr la 0065 a mano (o
-- verificar_0064_0065.py --en-transaccion) VUELVE A CREAR la vista (hace
-- `drop view if exists` + `create view`). aplicar_migraciones.py ya no re-corre
-- lo registrado; si alguien re-aplica la 0065, re-aplica también esta.
--
-- REVERSA: volver a correr el bloque de la vista de la 0065 (0065:1123-1143), su
-- comment (0065:1224-1227) y `grant select on ops.devoluciones_vs_canal_v to
-- service_role` (0065:1264), y el comment de ops.stock_apartado_descuadre_v
-- (0065:1220-1223). Es una vista: no guarda datos.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- Un DROP VIEW pide ACCESS EXCLUSIVE sobre la vista (no sobre sus tablas):
-- espera corta y truena, en vez de formar a nadie detrás.
set local lock_timeout = '3s';

-- Registro de esta aplicación (antes del drop, para dejar dicho si existía).
insert into ops.migraciones (migracion, detalle)
values ('0067_ops_quitar_devoluciones_vs_canal_v',
        jsonb_build_object('vista',   'ops.devoluciones_vs_canal_v',
                           'existia', to_regclass('ops.devoluciones_vs_canal_v') is not null));

-- Sin CASCADE: si algo dependiera de ella, que truene y lo diga.
drop view if exists ops.devoluciones_vs_canal_v;

-- El mismo texto de la 0065, sin la mención a la vista que se acaba de quitar.
comment on view ops.stock_apartado_descuadre_v is
  'Vigía: solo devuelve filas cuando algo no cuadra (apartado vs renglones, apartado en OV no confirmadas, libre < 0, '
  'apartado sin admite_ov, devolución de más, formato confirmado sin cuadrar con el libro, REVISION vs devoluciones '
  'abiertas). No depende de channel.*. Aviso cada 15 min si da ≥ 1 fila (la segunda vigía, '
  'ops.devoluciones_vs_canal_v, la quitó la 0067).';

commit;
