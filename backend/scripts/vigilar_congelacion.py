"""
vigilar_congelacion.py — El detector de las fallas SILENCIOSAS del apagado de
los espejos. READ-ONLY: no escribe una sola fila.

POR QUÉ EXISTE. Congelar una tabla no produce errores: produce datos que dejan
de moverse mientras todo parece bien. Los 964 pedidos fantasma del 12-ago no
lanzaron una sola excepción, y el turno del sync congelado tampoco lo habría
hecho. Un arnés de paridad ya no sirve —MySQL deja de ser la referencia—, así
que lo que hay que vigilar es OTRA COSA: que kubera siga MOVIÉNDOSE.

Cinco latidos, cada uno con su umbral:
  1. El turno del sync AVANZA — que la marca más vieja de channel.listings se
     recorra. Si se queda quieta, el barrido se atoró en los mismos SKUs y el
     resto del catálogo dejó de observarse (el hallazgo de v0.128.0).
  2. Pedidos ENTRANDO a channel.orders.
  3. Costos ESCRIBIÉNDOSE en costing.costos_finales.
  4. Las tablas de MySQL efectivamente CONGELADAS (confirma que el apagado tomó
     efecto; si siguen moviéndose, el flag no se aplicó).
  5. El padrón vivo: altas recientes en core.products.

Uso:  backend/.venv/Scripts/python.exe backend/scripts/vigilar_congelacion.py
Correrlo ANTES de apagar deja la línea base contra la cual comparar después.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Cada latido: (nombre, horas_de_gracia, qué significa si se pasa)
# ⚠️ ESTE LATIDO NO SE PUEDE MEDIR HOY — leer antes de reactivarlo.
#
# `channel.listings.updated_at` NO es "cuándo se revisó": es "cuándo CAMBIÓ".
# El upsert de channel_mirror lleva `where … is distinct from …`, así que el
# UPDATE solo dispara si el dato cambió, y `trg_touch_listings` (BEFORE UPDATE)
# solo entonces toca la fecha. Una publicación pausada con precio y stock
# estables puede visitarse cada 15 min y conservar la fecha de hace diez días.
#
# Sobre esa columna se construyeron DOS métricas equivocadas el 13-ago-2026:
# primero un tope de antigüedad (que además crecía con el reloj) y después un
# "ciclo completo" — las dos leían "no lo han revisado" donde el dato decía
# "no ha cambiado". No hay ninguna columna que registre la VISITA, así que la
# cobertura del barrido hoy NO es medible desde la base.
#
# Para medirla haría falta un `last_seen_at` escrito en cada visita, pase lo que
# pase con el dato. Sería para PODER MEDIR, no para arreglar nada: el turno de
# `inventario._lote_desde_ml` ordena por `updated_at`, así que en teoría una
# publicación estable no cede su lugar al visitarse — pero eso NO produce datos
# viejos. Comprobado contra la API de ML el 13-ago-2026: 24 publicaciones, las
# 12 más rancias y las 12 recién cambiadas, **cero diferencias** en precio,
# stock y situación. La fila puede repetir a los estables sin consecuencia,
# porque justamente son los que no tienen nada nuevo que contar.
_MIDE_COBERTURA = False

UMBRALES = {
    # `turno_sync` ya NO se mide por antigüedad. La primera versión ponía un tope
    # fijo de 240 h a la marca más vieja, y eso está mal de origen: si el barrido
    # no alcanza esa fila, su antigüedad crece 1:1 con el reloj y el umbral se
    # cruza solo, sin que nada haya empeorado. Cruzó a las 14 h exactas de
    # ponerlo (226.1 + 14.1 = 240.2) con la marca más vieja INTACTA al segundo.
    # Un umbral absoluto sobre algo que crece con el calendario mide el paso del
    # tiempo, no la salud. Ver `_ciclo_horas`.
    "pedidos": (6, "no entran pedidos (puede ser noche o día flojo; contrastar "
                   "con el tab de Ventas antes de alarmarse)"),
    "costos": (72, "nadie ha recalculado un costo (normal si no se usó el panel)"),
    "padron": (30, "el ETL de las 06:15 no dio de alta nada — revisar el cron"),
}


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for l in (ROOT / nombre).read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


def _horas(ts) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def _linea(nombre: str, ts, extra: str = "") -> bool:
    h = _horas(ts)
    tope, motivo = UMBRALES.get(nombre, (24, ""))
    ok = h is not None and h <= tope
    marca = "OK  " if ok else "ALTO"
    edad = f"{h:.1f} h" if h is not None else "nunca"
    print(f"  [{marca}] {nombre:12s} hace {edad:>9s} (tope {tope} h) {extra}")
    if not ok:
        print(f"         → {motivo}")
    return ok


def main() -> None:
    E = cargar(".env")
    print(f"LATIDOS DE KUBERA — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n", flush=True)
    pg = psycopg2.connect(E["SUPABASE_DB_URL"], connect_timeout=25)
    c = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    todo_ok = True

    # 1. El turno del sync, medido como CUÁNTO TARDA UNA VUELTA COMPLETA al
    #    catálogo al ritmo actual: total ÷ (tocadas por hora). Es independiente
    #    del calendario —no crece solo— y dice lo único que importa: si el
    #    barrido alcanza a observar todo antes de que el dato envejezca.
    #    Mirar la marca más NUEVA no sirve: con el barrido atorado los mismos
    #    SKUs se refrescan cada 15 min y el "último visto" se ve perfecto.
    c.execute("""select min(updated_at) mas_vieja, max(updated_at) mas_nueva,
                        count(*) filter (where updated_at > now() - interval '3 hours') ultimas_3h,
                        count(*) total,
                        count(*) filter (where updated_at < now() - interval '7 days') viejas
                   from channel.listings where canal in ('mercado_libre','amazon')""")
    r = c.fetchone()
    print(f"  [ n/d] turno_sync   {r['ultimas_3h']} publicaciones CAMBIARON en 3 h "
          f"de {r['total']}")
    print(f"         (no es cobertura: updated_at dice cuándo CAMBIÓ el dato, no "
          f"cuándo se revisó — ver la nota de _MIDE_COBERTURA)")
    if not _MIDE_COBERTURA:
        pass  # este latido no entra al veredicto hasta que exista `last_seen_at`

    c.execute("select max(creado_at) t from channel.orders")
    todo_ok &= _linea("pedidos", c.fetchone()["t"])
    c.execute("select max(updated_at) t from costing.costos_finales")
    todo_ok &= _linea("costos", c.fetchone()["t"])
    c.execute("select max(created_at) t from core.products")
    todo_ok &= _linea("padron", c.fetchone()["t"])
    pg.close()

    # 5. ¿El apagado tomó efecto? Las tablas del espejo deben estar QUIETAS.
    print("\n  Espejo MySQL (debe estar CONGELADO tras apagar los flags):")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as mc:
        for tabla, col in (("costos_finales", "updated_at"),
                           ("pedidos_ml", "actualizado"),
                           ("canal_inventario", "updated_at")):
            try:
                mc.execute(f"SELECT MAX({col}) t FROM {tabla}")
                h = _horas(mc.fetchone()["t"])
                estado = "congelada" if (h or 0) > 1 else "AÚN ESCRIBIENDO"
                print(f"    {tabla:20s} última escritura hace "
                      f"{h:.1f} h  → {estado}" if h is not None
                      else f"    {tabla:20s} vacía")
            except Exception as exc:  # noqa: BLE001
                print(f"    {tabla:20s} ? ({exc})")
    my.close()

    print(f"\nRESULTADO: {'todo late' if todo_ok else 'REVISAR lo marcado ALTO'}")
    sys.exit(0 if todo_ok else 1)


if __name__ == "__main__":
    main()
