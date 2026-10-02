"""
full_publicaciones.py — Qué fila de `channel.listings` cuenta para el stock FULL de
cada publicación de Mercado Libre.

UNA PUBLICACIÓN, DOS FILAS. La llave de `channel.listings` es (sku, cuenta, canal),
así que una publicación puede quedar en dos filas: la del SKU que la publicación
DECLARA y la de otro SKU que la reclama —el padre que quedó de antes (TEC-1813 frente
a TEC-1813-NEG-17´) o un hermano mal escrito (SIL-0008-NEG frente a SIL-008-NEG—.
Medido el 2-oct-2026 contra la API de ML: 83 publicaciones FULL así y NINGUNA con
variantes en ML. Cada una declara un SKU y sólo esa fila trae el stock de verdad:
la fila declarada sumó 1,620 pzs, lo mismo que ML; sumando las dos filas daban
4,107. La otra fila se queda con un número viejo porque el sync y los avisos
escriben en la declarada.

LA REGLA — acertó 83 de 83 contra el SKU que declara cada publicación:
  1. no el PADRE de otra fila de la misma publicación (por sí sola: 79 de 83);
  2. la del aviso de FULL más reciente: `stock_full` resuelve el SKU con la API
     de ML, así que el aviso llega con el SKU declarado (por sí sola: 79 de 83);
  3. la del cambio de stock FULL más reciente en el historial del sync;
  4. la más reciente.
Una publicación con una sola fila cuenta tal cual.

SOLO LEE. `CTE` deja la tabla `h` con cada fila FULL de Kubera y San Corpe y la
columna `cuenta_fila`: true en la fila que cuenta.
"""
from __future__ import annotations

# Va con `%%`: quien la usa le pasa parámetros, y psycopg2 lee `%` como un hueco.
CTE = r"""
    with f as (select upper(a.legacy_code) as cuenta, l.account_id, l.listing_id, l.sku::text as sku,
                      l.situacion, coalesce(l.stock_full, 0) as st, l.updated_at
                 from channel.listings l join core.accounts a on a.id = l.account_id
                where l.canal = 'mercado_libre' and l.is_fulfillment
                  and a.legacy_code in ('BEKURA', 'SANCORFASHION')),
         dob as (select f.* from f
                   join (select cuenta, listing_id from f group by 1, 2 having count(*) > 1) m
                  using (cuenta, listing_id)),
         padre as (select distinct p.cuenta, p.listing_id, p.sku
                     from dob p join dob c
                       on c.cuenta = p.cuenta and c.listing_id = p.listing_id
                      and c.sku <> p.sku and c.sku like p.sku || '-%%'),
         aviso as (select upper(x.cuenta) as cuenta, x.sku::text as sku, max(x.ts) as ts
                     from ops.fanout_log x
                    where x.accion like 'full\_%%' and x.ts > now() - interval '30 days'
                      and x.sku::text in (select sku from dob)
                    group by 1, 2),
         foto as (select hi.account_id, hi.sku::text as sku, max(hi.changed_at) as ts
                    from channel.listing_history hi
                   where hi.campo = 'stock_full' and hi.changed_at > now() - interval '90 days'
                     and hi.sku::text in (select sku from dob)
                   group by 1, 2),
         orden as (select dob.cuenta, dob.listing_id, dob.sku,
                          row_number() over (partition by dob.cuenta, dob.listing_id
                                             order by (padre.sku is not null), aviso.ts desc nulls last,
                                                      foto.ts desc nulls last, dob.updated_at desc) as n
                     from dob
                     left join padre on padre.cuenta = dob.cuenta and padre.listing_id = dob.listing_id
                                    and padre.sku = dob.sku
                     left join aviso on aviso.cuenta = dob.cuenta and aviso.sku = dob.sku
                     left join foto on foto.account_id = dob.account_id and foto.sku = dob.sku),
         h as (select f.*, (o.n is null or o.n = 1) as cuenta_fila
                 from f left join orden o on o.cuenta = f.cuenta and o.listing_id = f.listing_id
                                         and o.sku = f.sku)
"""

# La fila que cuenta de cada publicación que vive en más de una, y las que no:
# para marcar en Crear FULL un SKU cuya publicación es de OTRO.
SQL_DUPLICADAS = CTE + r"""
    select h.cuenta, h.sku, h.listing_id, h.st,
           (select d.sku from h d where d.cuenta = h.cuenta and d.listing_id = h.listing_id
                                    and d.cuenta_fila limit 1) as declarada
      from h
     where not h.cuenta_fila
"""
