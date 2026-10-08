# -*- coding: utf-8 -*-
"""
backfill_devoluciones_ml.py — RETIRADO (5-oct-2026). Aborta en cualquier modo.

Lo reemplaza `scripts/recuperar_devoluciones_ml.py`, cuyo dry-run cuenta lo
mismo (y más: mediaciones, refresco de lo abierto, F4/F5 sobre lo guardado)
con candados que este nunca tuvo.

POR QUÉ SE RETIRÓ, Y NO SOLO SE BLOQUEÓ SU `--aplicar`
─────────────────────────────────────────────────────
1. Filtraba `type == "returns"`: repetía la falla F1 y tiraba las devoluciones
   que ML abre como reclamo `mediations`.
2. Escribía con `_guardar` directo, sin el candado de duplicados ni el de
   degradación de `sincronizar`.
3. Su dry-run tampoco era inocuo. Sacaba el token con `meli._access_token`, que
   NO renueva nada (solo lee), pero fuera del backend y con
   `TOKENS_SOLO_KUBERA` apagado (el valor por omisión de `config.py`) arbitra
   contra el MySQL: `db.fetch_one` sobre `ml_tokens_dashboard`/`ml_tokens`.
   Corrido desde OMNICANAL, cuyo `.env` trae el MySQL de producción, leía MySQL
   y el Supabase de producción. Y pegaba a ML sin transporte vigilado (sin tope,
   sin alto ante 401/403, solo GET por costumbre, no por candado).

Lo que este script sabía y no se pierde:

· `abierta_at` SIEMPRE con `claim.date_created` (trampa 1 del encabezado de
  `services/devoluciones_ml.py`); el script nuevo imprime el rango de fechas.
· La historia útil arranca en julio de 2026: antes, los pedidos no están en
  `channel.orders` (de 771 devoluciones del 9-sep, 423 sin su pedido) y
  saldrían sin SKU ni precio y afirmando DROP. Decisión de Brandon (9-sep):
  se captura desde julio. El script nuevo mira `--dias 90` hacia atrás.
· `claims/search` se recorre por tramos mensuales (el `paging.total` se
  vuelve poco fiable en rangos largos) y se deduplica (`_buscar`).
"""
from __future__ import annotations

import sys

AVISO = ("ABORT: backfill_devoluciones_ml.py está RETIRADO (5-oct-2026), también "
         "su dry-run. Usa backend/scripts/recuperar_devoluciones_ml.py (dry-run por "
         "omisión; lee su encabezado para el uso).")


def main(argv: list[str] | None = None) -> int:
    """No importa nada del backend: ni `config` ni `meli` llegan a cargarse."""
    print(AVISO, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
