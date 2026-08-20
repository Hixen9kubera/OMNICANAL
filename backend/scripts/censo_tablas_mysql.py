"""
censo_tablas_mysql.py — Las 38 tablas de `kubera_ml`, una por una: qué son, quién
las lee, quién las escribe, y QUÉ PASA EL DÍA DEL CORTE.

POR QUÉ EXISTE
--------------
Hasta hoy los defectos del corte aparecieron **de uno en uno, tropezando**:

  · los tokens de TikTok, un tercer almacén de credenciales que no estaba en
    ninguna lista
  · `SUPABASE_READ_WEBHOOKS`, una bandera que parecía pendiente y en realidad
    convierte la campana en una manguera
  · el escritor de la caché de imágenes, que sin MySQL deja de guardar y sube
    copias duplicadas a WordPress
  · un agujero en la propia red de seguridad, en el camino que justamente usa
    el corte

Cuatro en dos días, todos encontrados por casualidad al hacer otra cosa. **Eso
no escala y no es forma de llegar a un corte de golpe.** Este censo cambia el
método: en vez de esperar a tropezar, se recorre todo antes.

QUÉ MIDE, Y POR QUÉ CADA COSA
------------------------------
Por cada tabla:

  VIVA / QUIETA / MUERTA   última escritura real. Una tabla quieta no es una
                           tabla muerta: puede estar esperando un evento raro.
  LECTORES · ESCRITORES    en `services/`, `routers/` y `scripts/`, por archivo.
  ¿DECIDE?                 si alguna lectura alimenta un `if` o una función que
                           pregunta algo. Es la distinción que separa "se ve
                           feo" de "cuesta dinero", y la que CLAUDE.md pide.
  ¿SE TRAGA EL ERROR?      si la lectura vive en un `try/except` que no relanza.
                           Sin MySQL eso NO falla: contesta. Ese es el peligro.
  DESTINO EN KUBERA        la tabla equivalente, si se sabe.

El cruce que importa es **decide + se traga el error + sin destino**: ahí el
corte produce una respuesta falsa, con seguridad, sin avisar.

Uso:
  ...python backend/scripts/censo_tablas_mysql.py
  ...python backend/scripts/censo_tablas_mysql.py --tabla fanout_log
  ...python backend/scripts/censo_tablas_mysql.py --md docs/CENSO_38_TABLAS.md
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from datetime import datetime
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent.parent
BACK = ROOT / "backend"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Destino conocido en kubera. "" = sin destino todavía; "—" = no lo necesita.
DESTINO = {
    "pedidos_ml": "channel.orders", "canal_inventario": "channel.listings",
    "costos_finales": "costing.costos_finales", "costos_validados": "costing.costos_validados",
    "costos_logs": "ops.process_log", "categorias_ml": "channel.listing_categories",
    "crear_logs": "ops.process_log", "productos": "core.products",
    "amazon_imagenes": "enrich.product_media", "ml_image_edit_backlog": "ops.channel_submissions",
    "ml_progress": "channel.listings + ops.channel_submissions",
    "amazon_progress": "channel.listings + ops.channel_submissions",
    "ml_backlog": "ops.channel_submissions", "amazon_backlog": "ops.channel_submissions",
    "stock_watch_foto": "ops.stock_watch_photo", "webhook_eventos": "ops.webhook_events",
    "fanout_log": "ops.fulfillment_operations + ops.fba_watermark",
    "ml_tokens": "ops.ml_tokens", "ml_tokens_dashboard": "ops.ml_tokens",
    "tiktok_tokens": "ops.tiktok_tokens", "ml_envio_real": "enrich.order_shipping_cost",
    "ml_ficha": "enrich.listing_weight", "ml_visitas": "enrich.listing_visits",
    "alertas_estado": "— (se deja morir: alertas.py ya guarda en memoria)",
    "espejo_kubera_log": "— (no puede ir a kubera: registra que kubera fallo)",
    "ventas_horarias": "— (cache detenida, VENTAS_ML_REFRESH=false)",
    "ventas_sync": "— (idem)",
}
_ROBOT = ("scraping_alibaba", "atributos_ia", "imagenes_producto", "costos_ml",
          "odoo_ranking", "odoo_sync_backlog", "odoo_sync_procesados",
          "pipeline_runs", "sync_procesados", "backlog_errores", "ml_estado")

_LEE = re.compile(r"\b(from|join)\s+`?(\w+)`?", re.I)
_ESCRIBE = re.compile(r"\b(insert\s+into|update|delete\s+from|replace\s+into|create\s+table(?:\s+if\s+not\s+exists)?)\s+`?(\w+)`?", re.I)
_PREGUNTA = ("_ya_", "ya_", "existe", "previo", "disponible", "hay_", "esta_",
             "tiene_", "_es_", "puede_", "conocid", "vist", "_cache_get")


def _cadenas(nodo) -> str:
    out = []
    for n in ast.walk(nodo):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            out += [v.value for v in n.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return " ".join(out)


def analizar_codigo(tablas: set[str]) -> dict[str, dict]:
    res = {t: {"lee": set(), "escribe": set(), "decide": set(), "traga": set()}
           for t in tablas}
    carpetas = [BACK / "services", BACK / "routers", BACK / "scripts"]
    for carpeta in carpetas:
        for f in sorted(carpeta.glob("*.py")):
            rel = f"{carpeta.name}/{f.name}"
            try:
                arbol = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            # Por funcion: que tablas toca, si pregunta algo, si se traga el error
            for fn in ast.walk(arbol):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                sql = _cadenas(fn).lower()
                if not sql:
                    continue
                leidas = {m[1] for m in _LEE.findall(sql)} & tablas
                escritas = {m[1] for m in _ESCRIBE.findall(sql)} & tablas
                pregunta = any(p in fn.name.lower() for p in _PREGUNTA)
                traga = any(
                    isinstance(n, ast.Try) and n.handlers
                    and all(not any(isinstance(x, ast.Raise) for x in ast.walk(h))
                            for h in n.handlers)
                    for n in ast.walk(fn))
                for t in leidas:
                    res[t]["lee"].add(f"{rel}::{fn.name}")
                    if pregunta:
                        res[t]["decide"].add(f"{rel}::{fn.name}")
                    if traga:
                        res[t]["traga"].add(f"{rel}::{fn.name}")
                for t in escritas:
                    res[t]["escribe"].add(f"{rel}::{fn.name}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabla", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    E = {}
    for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            E[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    my = pymysql.connect(host=E["DB_HOST"], port=int(E.get("DB_PORT", 3306)),
                         user=E["DB_USER"], password=E["DB_PASSWORD"],
                         database=E["DB_NAME"], connect_timeout=25,
                         cursorclass=pymysql.cursors.DictCursor)
    with my.cursor() as c:
        c.execute("SHOW TABLES")
        tablas = sorted(list(r.values())[0] for r in c.fetchall())
        c.execute("SELECT NOW() n")
        ahora = c.fetchone()["n"]
        vida = {}
        for t in tablas:
            c.execute(f"SHOW COLUMNS FROM `{t}`")
            cols = [r["Field"] for r in c.fetchall()]
            fecha = next((x for x in ("updated_at", "actualizado", "recibido", "ts",
                                      "created_at", "creado", "last_submitted",
                                      "submitted_at", "fecha")
                          if x in cols), None)
            c.execute(f"SELECT COUNT(*) n FROM `{t}`")
            n = c.fetchone()["n"]
            ult = None
            if fecha and n:
                c.execute(f"SELECT MAX(`{fecha}`) x FROM `{t}`")
                ult = c.fetchone()["x"]
            # Algunas columnas son DATE y no DATETIME (`ventas_sync.fecha`):
            # restarle un datetime revienta. Se normaliza a datetime.
            if ult is not None and not isinstance(ult, datetime):
                ult = datetime.combine(ult, datetime.min.time())
            horas = (ahora - ult).total_seconds() / 3600 if ult else None
            vida[t] = {"filas": n, "ultima": ult, "horas": horas, "col": fecha}
    my.close()

    codigo = analizar_codigo(set(tablas))

    def estado(t):
        h = vida[t]["horas"]
        if h is None:
            return "SIN FECHA" if vida[t]["filas"] else "VACIA"
        if h < 24:
            return "VIVA"
        if h < 24 * 7:
            return "QUIETA"
        return "MUERTA"

    lineas = []
    def P(s=""):
        print(s)
        lineas.append(s)

    P("CENSO DE LAS 38 TABLAS DE kubera_ml")
    P(f"medido contra la base el {ahora} UTC\n")
    P("  El cruce que importa: DECIDE + se traga el error + sin destino.")
    P("  Ahi el corte produce una respuesta falsa, con seguridad y sin avisar.\n")

    riesgo = []
    for t in tablas:
        if args.tabla and t != args.tabla:
            continue
        v, cod = vida[t], codigo[t]
        est = estado(t)
        dest = DESTINO.get(t, "(robot Alibaba / legado)" if t in _ROBOT else "")
        peligro = bool(cod["decide"] and cod["traga"]) and not dest.startswith(("—", "("))
        if peligro:
            riesgo.append(t)
        P(f"{'*' if peligro else ' '} {t}")
        edad = f"hace {v['horas']:.0f} h" if v["horas"] is not None else "sin fecha"
        P(f"    {est:9s} {v['filas']:7d} filas · ultima {v['ultima']} ({edad})")
        P(f"    destino  : {dest or '** SIN DESTINO **'}")
        P(f"    lectores : {len(cod['lee'])}   escritores: {len(cod['escribe'])}"
          f"   deciden: {len(cod['decide'])}   se tragan el error: {len(cod['traga'])}")
        if args.tabla or peligro:
            for k, et in (("lee", "LEE"), ("escribe", "ESCRIBE"), ("decide", "DECIDE")):
                for x in sorted(cod[k])[:8]:
                    P(f"        {et:8s} {x}")
        P()

    P("=" * 72)
    P(f"  tablas con riesgo (deciden + se tragan el error + hay destino): {len(riesgo)}")
    for t in riesgo:
        P(f"    · {t}")
    P()
    for e in ("VIVA", "QUIETA", "MUERTA", "VACIA", "SIN FECHA"):
        n = [t for t in tablas if estado(t) == e]
        P(f"  {e:9s} {len(n):3d}  {', '.join(n[:9])}{' …' if len(n) > 9 else ''}")

    if args.md:
        (ROOT / args.md).write_text(
            "# Censo de las 38 tablas de `kubera_ml`\n\n```\n"
            + "\n".join(lineas) + "\n```\n", encoding="utf-8")
        print(f"\n  escrito en {args.md}")


if __name__ == "__main__":
    main()
