"""
categorias_write.py — CORTE F6 del dominio CATEGORÍAS (ML).

La elección de categoría del panel es la que MANDA (regla 2 de la casa). Hoy
esa elección se persiste en WooCommerce (postmeta ml_categoria_id) y kubera se
entera hasta el ETL de las 06:15 (etl_channel_categories, que la lee del
postmeta con source='panel').

CON EL CORTE (SUPABASE_WRITE_CATEGORIAS=true): al guardarla en el panel, la
elección también se escribe SÍNCRONA en kubera — el árbol
(channel.categories) y la asignación (channel.product_category,
source='panel') en la misma transacción. El sku se resuelve por wc_id contra
core.products (por eso este corte va de la mano del de CORE: sin acta del
maestro no hay FK posible — el evento cae a la cola y el reproceso lo aplica
cuando el acta exista).

Si kubera está caída: cola del espejo clásico (espejo_kubera_log,
reprocesable) + Slack; el guardado en Woo jamás se bloquea. El ETL queda de
AUDITOR/respaldo con acta estricta (con_deltas si corrigió algo).

Sin corte (flag off), registrar() delega en kubera_mirror.espejar — que hoy
el censo (KUBERA_MIRROR_TABLAS) mantiene apagado para wp_postmeta: es decir,
flag off = comportamiento actual exacto (solo el ETL nocturno).
"""
from __future__ import annotations

import logging

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.categorias_write")

_ORIGEN = "routers/crear.py"


def activo() -> bool:
    return settings.supabase_write_categorias and sdb.disponible()


def registrar(wc_id: int, category_id: str, nombre: str, ruta: str) -> None:
    """Registra la elección de categoría del panel en kubera."""
    from services import kubera_mirror
    payload = {"wc_id": int(wc_id), "category_id": str(category_id),
               "name": nombre or None, "path": ruta or None}
    clave = f"{wc_id}:{category_id}"
    if not activo():
        kubera_mirror.espejar(_ORIGEN, "categoria-ml", "wp_postmeta",
                              "channel.product_category", "UPSERT", payload,
                              clave=clave)
        return
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('statement_timeout', '4000', true)")
            cur.execute("select set_config('app.via', 'corte_categorias', true)")
            kubera_mirror._up_channel_categoria(cur, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("primaria kubera categoría(%s) falló — el evento cae a la "
                    "cola del espejo: %s", clave, exc)
        # Directo a espejo_kubera_log (no espejar): el censo tiene wp_postmeta
        # apagado a propósito y filtraría la cola. _persistir_error también
        # dispara la alerta de Slack del espejo.
        try:
            kubera_mirror._persistir_error(_ORIGEN, "categoria-ml", "wp_postmeta",
                                           "channel.product_category", "UPSERT",
                                           clave, exc, payload)
        except Exception as exc2:  # noqa: BLE001
            log.error("no se pudo encolar la categoría %s: %s", clave, exc2)
