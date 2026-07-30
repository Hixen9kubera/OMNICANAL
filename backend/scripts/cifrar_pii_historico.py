"""
Cifra los datos personales YA guardados en los pedidos históricos.

POR QUÉ (30-jul-2026)
---------------------
El cuestionario de cumplimiento de Temu rechazó la solicitud de API con:

    "For storage of personally identifiable information (PII) such as names,
     phone numbers, addresses, and emails, encryption is required."

El censo en vivo del 30-jul encontró que el ÚNICO dato personal en toda la base
es el nombre del comprador (y su nick de ML). No hay un solo correo, teléfono
ni dirección guardados:

    wp_wc_order_addresses  → 7,275 nombres en texto plano
    wp_wc_orders_meta      → 7,091 nicks (`_ml_comprador`) en texto plano

`services/pedidos_ml.py` ya cifra los pedidos NUEVOS. Este script cierra el
histórico.

POR QUÉ SQL DIRECTO Y NO LA API REST
------------------------------------
La regla de la casa es no hacer DML sobre las tablas `wp_*`. Aquí se hace una
excepción acotada y por una razón de seguridad, no de comodidad:

  - `wc_order_addresses` y `wc_orders_meta` son ALMACENAMIENTO PLANO de HPOS,
    no tablas derivadas como `wc_product_meta_lookup`. No hay nada que
    recalcular ni que pueda quedar inconsistente.
  - Un `PUT /orders/{id}` de la API REST dispara los hooks de actualización de
    WooCommerce sobre 7,275 pedidos de PRODUCCIÓN: recálculos, correos y
    webhooks. Ese es el camino peligroso, no éste.

Aun así, el script VERIFICA por la API REST después de escribir, para descartar
que quede caché servida por WooCommerce con el valor viejo.

SEGURIDAD DEL PROCESO
---------------------
  - IDEMPOTENTE: un valor que ya empieza con `enc:` se salta. Se puede repetir.
  - REVERSIBLE: `pii.descifrar()` recupera el original con la llave.
  - SE NIEGA A CORRER SIN LLAVE: sin `PII_KEY` aborta en vez de escribir
    marcadores genéricos y perder los nombres para siempre.

Uso:
    python -m scripts.cifrar_pii_historico                  # dry-run
    python -m scripts.cifrar_pii_historico --limite 1 --aplicar   # prueba
    python -m scripts.cifrar_pii_historico --aplicar        # todo
"""
from __future__ import annotations

import logging
import sys

logging.disable(logging.WARNING)

# La consola de Windows llega en cp1252 y revienta con los acentos y flechas.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — consolas que no lo soportan
    pass


def _resumen(cur, P: str) -> None:
    cur.execute(f"""SELECT
            SUM(first_name LIKE 'enc:%%') AS nom_cif,
            SUM(first_name IS NOT NULL AND first_name <> ''
                AND first_name NOT LIKE 'enc:%%') AS nom_claro
        FROM {P}wc_order_addresses WHERE address_type = 'billing'""")
    a = cur.fetchone()
    cur.execute(f"""SELECT
            SUM(meta_value LIKE 'enc:%%') AS nick_cif,
            SUM(meta_value <> '' AND meta_value NOT LIKE 'enc:%%') AS nick_claro
        FROM {P}wc_orders_meta WHERE meta_key = '_ml_comprador'""")
    b = cur.fetchone()
    print(f"  nombres : cifrados={int(a['nom_cif'] or 0):,}  "
          f"en claro={int(a['nom_claro'] or 0):,}")
    print(f"  nicks   : cifrados={int(b['nick_cif'] or 0):,}  "
          f"en claro={int(b['nick_claro'] or 0):,}")


def _verificar(order_id: int) -> int:
    """
    Contrasta lo que hay EN LA BASE contra lo que devuelve la API REST.

    Sirve para dos cosas: descartar que WooCommerce esté sirviendo un valor
    viejo desde caché, y producir la evidencia que pide el cuestionario.
    """
    import httpx

    from config import settings
    from services import pii, wp_db

    P = wp_db._prefix()
    fila = wp_db._fetch_all(
        f"""SELECT first_name, last_name FROM {P}wc_order_addresses
            WHERE order_id = %s AND address_type = 'billing'""", (order_id,))
    if not fila:
        print(f"No existe el pedido {order_id}")
        return 1
    guardado = fila[0]["first_name"]

    r = httpx.get(f"{settings.wc_url.rstrip('/')}/wp-json/wc/v3/orders/{order_id}",
                  auth=(settings.wc_consumer_key, settings.wc_consumer_secret),
                  timeout=30.0)
    rest = (r.json().get("billing") or {}).get("first_name") if r.status_code == 200 else f"HTTP {r.status_code}"

    print(f"Pedido {order_id}")
    print(f"  en la base : {guardado}")
    print(f"  por la API : {rest}")
    print(f"  descifrado : {pii.descifrar(guardado)}")
    coincide = guardado == rest
    print(f"\n  {'OK - la API devuelve lo mismo que la base (sin caché vieja)' if coincide else 'OJO - la API devuelve algo distinto: hay caché de por medio'}")
    return 0 if coincide else 1


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    limite = 0
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])

    from services import pii, wp_db

    if "--verificar" in sys.argv:
        return _verificar(int(sys.argv[sys.argv.index("--verificar") + 1]))

    if not pii.habilitado():
        print("ABORTA: PII_KEY no está definida. Sin llave este script "
              "escribiría marcadores genéricos y los nombres se perderían.")
        return 1

    P = wp_db._prefix()
    tope = f"LIMIT {limite}" if limite else ""

    with wp_db._cursor() as cur:
        print("ANTES:")
        _resumen(cur, P)

        # ---- 1) nombres del comprador (wc_order_addresses) -------------
        cur.execute(f"""SELECT order_id, first_name, last_name
                        FROM {P}wc_order_addresses
                        WHERE address_type = 'billing'
                          AND ((first_name <> '' AND first_name NOT LIKE 'enc:%%')
                            OR (last_name  <> '' AND last_name  NOT LIKE 'enc:%%'))
                        ORDER BY order_id {tope}""")
        direcciones = list(cur.fetchall())

        # ---- 2) nicks de ML (wc_orders_meta) ---------------------------
        cur.execute(f"""SELECT id, meta_value
                        FROM {P}wc_orders_meta
                        WHERE meta_key = '_ml_comprador'
                          AND meta_value <> '' AND meta_value NOT LIKE 'enc:%%'
                        ORDER BY id {tope}""")
        nicks = list(cur.fetchall())

        print(f"\nA cifrar: {len(direcciones):,} nombres · {len(nicks):,} nicks")
        if not aplicar:
            print("\n(dry-run — no se escribió nada; agrega --aplicar)")
            for d in direcciones[:3]:
                print(f"  pedido {d['order_id']}: "
                      f"{str(d['first_name'])[:3]}… → enc:…")
            return 0

        hechos = fallos = 0
        for d in direcciones:
            try:
                cur.execute(
                    f"""UPDATE {P}wc_order_addresses
                        SET first_name = %s, last_name = %s
                        WHERE order_id = %s AND address_type = 'billing'""",
                    (pii.cifrar(d["first_name"]), pii.cifrar(d["last_name"]),
                     d["order_id"]))
                hechos += 1
            except Exception as exc:  # noqa: BLE001
                fallos += 1
                print(f"  ! pedido {d['order_id']}: {exc}")
            if hechos % 500 == 0 and hechos:
                print(f"  … {hechos:,} nombres")

        nick_ok = 0
        for n in nicks:
            try:
                cur.execute(
                    f"UPDATE {P}wc_orders_meta SET meta_value = %s WHERE id = %s",
                    (pii.cifrar(n["meta_value"]), n["id"]))
                nick_ok += 1
            except Exception as exc:  # noqa: BLE001
                fallos += 1
                print(f"  ! meta {n['id']}: {exc}")

        print(f"\nEscritos: {hechos:,} nombres · {nick_ok:,} nicks · fallos={fallos}")
        print("\nDESPUÉS:")
        _resumen(cur, P)

        # comprobación de ida y vuelta sobre lo que se acaba de escribir
        if direcciones:
            cur.execute(f"""SELECT first_name, last_name FROM {P}wc_order_addresses
                            WHERE order_id = %s AND address_type = 'billing'""",
                        (direcciones[0]["order_id"],))
            r = cur.fetchone()
            claro = pii.descifrar(r["first_name"])
            ok = claro == str(direcciones[0]["first_name"]).strip()
            print(f"\nDescifrado de control (pedido {direcciones[0]['order_id']}): "
                  f"{'OK — el original se recupera' if ok else 'FALLÓ'}")
            print(f"  guardado : {str(r['first_name'])[:24]}…")
            print(f"  descifra : {'sí, coincide' if ok else 'NO COINCIDE'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
