"""
tiendas.py — Da de alta una tienda: inventa su código y arma su renglón para `MAYOREO_TIENDAS`.

    python tiendas.py "Nombre de la tienda" [--partner 123456]

El código se enseña UNA vez, aquí; lo que se guarda en Railway es su huella (SHA-256), no el código.
Para dar de baja una tienda se pone `"activa": false` en su renglón (o se borra): las demás no se tocan.

`--partner` es el id del cliente de esa tienda en Odoo (res.partner). Si no se pone, sus órdenes van
al cliente por omisión, `MAYOREO_PARTNER_ID`.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys

from pedidos import huella_codigo

LETRAS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # sin 0/O, 1/I/L: se dictan por teléfono


def nuevo_codigo() -> str:
    trozo = lambda: "".join(secrets.choice(LETRAS) for _ in range(4))  # noqa: E731
    return f"TDA-{trozo()}-{trozo()}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("nombre")
    ap.add_argument("--partner", type=int, default=None)
    a = ap.parse_args()
    codigo = nuevo_codigo()
    renglon = {"nombre": a.nombre, "hash": huella_codigo(codigo), "activa": True}
    if a.partner:
        renglon["partner_id"] = a.partner
    print(f"Código para {a.nombre} (dáselo a la tienda; no se vuelve a mostrar):  {codigo}")
    print("Renglón para agregar a la lista de MAYOREO_TIENDAS en Railway:")
    print(json.dumps(renglon, ensure_ascii=False))
