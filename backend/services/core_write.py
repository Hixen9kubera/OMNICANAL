"""
core_write.py — CORTE F6 del dominio CORE (el registro civil del catálogo).

Este dominio es distinto a costos/pedidos/channel: WooCommerce (wp_posts) NO
se retira — sigue siendo la fuente de verdad del catálogo. Lo que se corta es
CÓMO se mantiene core.products:

  ANTES (seam v0.65): cada evento de ciclo de vida (nacimiento en Crear,
  publish, trash/deleted de la auditoría) viajaba por el espejo clásico —
  cola en memoria + workers, best-effort. kubera se enteraba "casi en vivo".

  CON EL CORTE (SUPABASE_WRITE_CORE=true): el evento se escribe SÍNCRONO en
  la misma petición (kubera primaria como registro), reutilizando el MISMO
  upsert del seam (kubera_mirror._up_core_product: update-por-wc_id primero,
  candado solo_por_wc_id para eventos destructivos). Si kubera está caída, el
  evento cae a la cola del espejo clásico (espejo_kubera_log, reprocesable
  desde /migracion) y Slack avisa — el flujo de negocio jamás se bloquea.

  El ETL de las 06:15 (etl_core_products_v2) queda de AUDITOR/respaldo: sigue
  corriendo, pero su acta ahora reporta `con_deltas` si tuvo que corregir
  algo que el seam debió cubrir. El criterio de cierre: 14 actas con hueco
  cero (medido en cero desde el 08-ago).

Sin corte (flag off), registrar() delega en kubera_mirror.espejar — el
comportamiento de siempre. Revertir = apagar el flag.
"""
from __future__ import annotations

import logging
from typing import Any

from config import settings
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.core_write")


def activo() -> bool:
    return settings.supabase_write_core and sdb.disponible()


def registrar(origen_py: str, funcion: str, payload: dict[str, Any],
              clave: str | None = None) -> None:
    """Registra un evento de ciclo de vida en core.products."""
    from services import kubera_mirror
    if not activo():
        kubera_mirror.espejar(origen_py, funcion, "wp_posts", "core.products",
                              "UPSERT", payload, clave=clave)
        return
    try:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('statement_timeout', '4000', true)")
            cur.execute("select set_config('app.via', 'corte_core', true)")
            kubera_mirror._up_core_product(cur, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("primaria kubera core.products(%s) falló — el evento cae a "
                    "la cola del espejo: %s", clave, exc)
        kubera_mirror.espejar(origen_py, f"{funcion} (corte→espejo)", "wp_posts",
                              "core.products", "UPSERT", payload, clave=clave)
        try:
            from services import alertas
            alertas.avisar(
                "escritura_fallback:core",
                f"⚠️ Registro de CORE cayó a la cola ({clave}): "
                f"{type(exc).__name__}: {str(exc)[:140]}. Reprocesable en /migracion.")
        except Exception:  # noqa: BLE001
            pass
