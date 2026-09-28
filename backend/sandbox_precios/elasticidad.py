"""Elasticidad precio de la demanda por publicación de ML FULL → `ultimo/elasticidades.json`.

DISENO §5. Dos piezas:

``construir_panel()``  panel publicación×DÍA (150 d) y publicación×SEMANA ISO con
                       unidades, ingreso, visitas, precio realizado, días censurados
                       e índice de estacionalidad por cuenta.
``estimar()``          β de unidades y βv de visitas por publicación (OLS within-item),
                       encogimiento empírico-bayesiano hacia la categoría raíz → global
                       → prior, validación fuera de muestra y la BASE (U0, V0) que usa
                       el optimizador.

Modelo (log-log con efecto fijo por publicación):

    log(u_is/I_s + 0.5) − media_i = β · (log p_is − media_i) + e

`u_is` son unidades de la semana normalizadas a 7 días VÁLIDOS, `I_s` el índice de
visitas de la cuenta en esa semana y `p_is` el precio realizado (ingreso/unidades).

Por qué cada decisión (medido el 28-sep-2026 con los crudos del día):

- **Solo publicaciones con serie de visitas (las 1,899 FULL).** Son el 96% de las
  unidades de 150 d (76,093 de 79,965) y son las únicas donde la censura se puede
  medir: sin visitas no hay forma de distinguir "pausada" de "nadie la quiso".
- **Censura = sin oferta, no demanda cero.** Un día sin venta se EXCLUYE si la
  publicación no existía (antes de `date_created`), si su estado era pausada / en
  revisión / sin stock FULL en algún momento del día, o si cae en una racha de ≥7
  días seguidos con 0 visitas que no esté cubierta de punta a punta por un estado
  CONOCIDO "activa con stock" (la API omite los días en 0 y en el caso medido los
  30 días faltantes coincidían con la pausa). Un día CON venta nunca es censura.
  Estados: `analytics.stock_hist` hasta el 15-jul (por item_id) y
  `channel.listing_history` desde el 17-jul (por sku×cuenta; solo se usa cuando el
  par apunta a UNA publicación — 55 pares tienen dos o más y ahí manda la serie).
- **Precio en semanas sin venta**: el realizado de la semana con venta más cercana
  (±2 semanas; geométrica si empatan) y, si no hay, el precio de LISTA del historial
  limpio × la razón realizado/lista mediana de la publicación. `listing_history`
  guarda `item.price` (las promociones son invisibles ahí), por eso se escala y
  va al último.
- **Estacionalidad**: índice semanal de visitas de la cuenta
  (`/users/{uid}/items_visits/time_window`). Ojo: mezcla temporada con tamaño del
  catálogo activo (pausar 300 publicaciones baja las visitas de la cuenta sin que
  la demanda de las demás cambie). La validación fuera de muestra lo compara con
  un índice por publicación y con no ajustar.
- **Encogimiento**: `β_i* = (β_i/se_i² + β_g/τ²)/(1/se_i² + 1/τ²)`. τ² = varianza
  entre publicaciones − ruido medio, con piso 0.05 (DISENO). La varianza y el ruido
  se miden ROBUSTOS (MAD y mediana): con la media, un puñado de β de ±15 con se de
  8 infla τ² y deja pasar sin encoger justo a los más ruidosos. El promedio del
  grupo se encoge a su vez hacia el global (piso 0.01): una categoría de 5
  publicaciones no debe pesar como una de 200.
- Recorte final a [elasticidad_min, elasticidad_max] de parametros.json; βv a
  [elasticidad_min, 0] y nunca más negativo que β (la conversión no puede SUBIR
  con el precio: βc = β − βv ≤ 0).

Todo es de LECTURA de archivos (`crudo/`, `cache/`); no toca la red ni la base.
Las funciones son síncronas: desde una corrutina van en `asyncio.to_thread`
(regla 11 de CLAUDE.md).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()
from sandbox_precios import almacen  # noqa: E402

log = logging.getLogger("laboratorio.elasticidad")

CUENTAS = ("BEKURA", "SANCORFASHION")
VENTANA_DIAS = 150            # máximo de la API de visitas (last=365 da 400)
MIN_SEMANAS = 6               # DISENO: estimación propia con ≥6 semanas válidas…
MIN_CV_PRECIO = 0.05          # …y CV de precio ≥ 5%
MIN_DIAS_SEMANA = 4           # una semana con <4 días con oferta no se usa
RACHA_CERO_VISITAS = 7        # ≥7 días seguidos en 0 visitas = sin oferta (salvo estado conocido)
ARRASTRE_SEMANAS = 2          # precio de semanas sin venta: ±2 semanas
TAU2_PISO = 0.05              # DISENO
TAU2_PISO_GRUPOS = 0.01       # varianza entre categorías (agregado, ver docstring)
SE_PISO = 0.05                # un ajuste perfecto con 6 puntos no es precisión infinita
MIN_ITEMS_GRUPO = 5           # DISENO: grupo = categoría raíz con ≥5 publicaciones
SEMANAS_PRUEBA = 4            # validación: se entrena sin las últimas 4 semanas
SE_ALTA = 0.5                 # DISENO: confianza alta = estimación propia con se < 0.5
# Topes de τ² que se prueban fuera de muestra (None = la fórmula sin tope). Medido
# el 28-sep: la fórmula da τ² ≈ 2.9 global (sd de 1.7 entre publicaciones) y con
# él el modelo predice PEOR que "mismas unidades" (WAPE 1.19 vs 1.04 en las
# semanas con cambio de precio ≥5%); con τ² ≤ 0.25 baja a 0.90. La fórmula mide
# heterogeneidad + error de especificación (precio semanal ruidoso, promociones),
# y un τ² inflado casi no encoge a los β extremos.
TOPES_TAU2: tuple[float | None, ...] = (None, 2.0, 1.0, 0.5, 0.25, 0.1)
# Se elige el tope MÁS ALTO (menos encogimiento) cuyo WAPE quede a ≤2% del mejor:
# entre dos topes que predicen igual, se respeta más la heterogeneidad medida.
TOLERANCIA_WAPE = 0.02
INICIO_LISTING_HISTORY = dt.date(2026, 7, 16)  # stock_hist se congeló el 15-jul
ESTADOS_OK = {"active"}


# ── utilidades ────────────────────────────────────────────────────────────────
def _parametros() -> dict:
    try:
        return json.loads(Path(__file__).with_name("parametros.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v: float | None, d: int = 4) -> float | None:
    return None if v is None else round(v, d)


def _crudo(nombre: str) -> Any:
    p = almacen.ultimo_crudo(nombre)
    if p is None:
        return None
    return almacen.leer_jsonl(p) if nombre.endswith(".jsonl") else almacen.leer_json(p)


def _id(cuenta: str, listing_id: str) -> str:
    return f"mercado_libre:{cuenta}:{listing_id}"


def _ts_cdmx(s: Any) -> dt.datetime | None:
    """ISO con zona → datetime en CDMX (las ventas se agrupan por día CDMX)."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(almacen.CDMX)


def _inicio_dia(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(0), tzinfo=almacen.CDMX)


def _cuantiles(xs: Iterable[float], qs=(0.1, 0.25, 0.5, 0.75, 0.9)) -> dict[str, Any]:
    v = sorted(x for x in xs if x is not None)
    n = len(v)
    if not n:
        return {"n": 0}
    out: dict[str, Any] = {"n": n, "media": round(sum(v) / n, 3)}
    for q in qs:
        out[f"p{int(q * 100)}"] = round(v[min(n - 1, int(q * n))], 3)
    return out


def _mad_var(xs: list[float]) -> float:
    """Varianza robusta: (1.4826·MAD)²."""
    if len(xs) < 2:
        return 0.0
    m = median(xs)
    return (1.4826 * median(abs(x - m) for x in xs)) ** 2


# ── insumos ───────────────────────────────────────────────────────────────────
def _insumos() -> dict[str, Any]:
    universo = _crudo("ml_universo.jsonl") or []
    ventas = (_crudo("kubera_ventas_dia.json") or {}).get("ml_dia") or []
    hist = _crudo("kubera_historial_precio.json") or {}
    comp = _crudo("kubera_competencia.json") or {}
    vcuenta = _crudo("ml_visitas_cuenta.jsonl") or []
    series = almacen.leer_json(almacen.ruta("cache", "visitas_serie.json"), {}) or {}
    faltan = [n for n, v in (("ml_universo", universo), ("kubera_ventas_dia", ventas),
                             ("visitas_serie", series), ("ml_visitas_cuenta", vcuenta)) if not v]
    return {"universo": universo, "ventas": ventas, "hist": hist, "comp": comp,
            "vcuenta": vcuenta, "series": series, "faltan": faltan}


def _mapa_categorias(comp: dict, universo: list[dict]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(categoria→raíz, sku→raíz, raíz→nombre) desde `sku_categoria` de competencia."""
    cat2raiz: dict[str, str] = {}
    sku2raiz: dict[str, str] = {}
    nombres: dict[str, str] = {}
    for r in comp.get("sku_categoria") or []:
        raiz = r.get("raiz_id")
        if not raiz:
            continue
        nombres[raiz] = r.get("raiz_nombre") or raiz
        if r.get("categoria_id"):
            cat2raiz[r["categoria_id"]] = raiz
        if r.get("sku"):
            sku2raiz[str(r["sku"]).strip().upper()] = raiz
    return cat2raiz, sku2raiz, nombres


# ── estados (censura) ─────────────────────────────────────────────────────────
class _Estados:
    """Marca por día si la publicación estuvo SIN OFERTA (True), CON oferta (False)
    o no se sabe (None). "Sin oferta" en cualquier momento del día cuenta."""

    def __init__(self, desde: dt.date, n: int):
        self.desde, self.n = desde, n
        self.malo = [False] * n
        self.conocido = [False] * n

    def marcar(self, ini: dt.datetime, fin: dt.datetime, malo: bool | None) -> None:
        if malo is None or fin <= ini:
            return
        a = max(0, (ini.date() - self.desde).days)
        # el día de `fin` solo cuenta si el intervalo entra en él
        ult = fin.date() if fin.time() > dt.time(0) else fin.date() - dt.timedelta(days=1)
        b = min(self.n - 1, (ult - self.desde).days)
        for k in range(a, b + 1):
            self.conocido[k] = True
            if malo:
                self.malo[k] = True

    def estado(self) -> list[bool | None]:
        return [True if m else (False if c else None) for m, c in zip(self.malo, self.conocido)]


def _intervalos_stock_hist(filas: list[dict]
                           ) -> tuple[list[tuple[dt.datetime, dt.datetime, bool]], bool | None]:
    """Intervalos de `analytics.stock_hist` (29-abr → 15-jul) y el último estado."""
    out = []
    ultimo: tuple[dt.date, bool] | None = None
    for f in filas:
        try:
            vf = dt.date.fromisoformat(f["valid_from"])
        except (TypeError, ValueError, KeyError):
            continue
        vt = None
        if f.get("valid_to"):
            try:
                vt = dt.date.fromisoformat(f["valid_to"])
            except ValueError:
                vt = None
        stock = f.get("stock_full")
        # Sin stock FULL solo es "sin oferta" mientras la publicación ERA FULL: en
        # cross_docking/xd_drop_off vende de bodega propia con stock_full = 0.
        malo = (f.get("status") not in ESTADOS_OK) or (
            f.get("logistic_type") == "fulfillment" and stock is not None and stock <= 0)
        ini = _inicio_dia(vf)
        fin = _inicio_dia(vt) if vt and vt > vf else (_inicio_dia(vf + dt.timedelta(days=1)) if vt else None)
        if fin is None:  # intervalo abierto al 15-jul: se arrastra (lo decide el llamador)
            fin = _inicio_dia(INICIO_LISTING_HISTORY)
        out.append((ini, fin, malo))
        if ultimo is None or vf >= ultimo[0]:
            ultimo = (vf, malo)
    return out, (ultimo[1] if ultimo else None)


def _intervalos_eventos(eventos: list[dict], es_malo, inicio: dt.datetime, fin: dt.datetime
                        ) -> list[tuple[dt.datetime, dt.datetime, bool | None]]:
    """Eventos (anterior→nuevo con marca de tiempo) de UN campo → intervalos.
    Antes del primer evento vale `anterior` (desde `inicio`)."""
    evs = sorted((t, e) for e in eventos if (t := _ts_cdmx(e.get("changed_at"))) is not None)
    if not evs:
        return []
    out = []
    t0, e0 = evs[0]
    if e0.get("anterior") is not None:
        out.append((inicio, t0, es_malo(e0.get("anterior"))))
    for k, (t, e) in enumerate(evs):
        t_sig = evs[k + 1][0] if k + 1 < len(evs) else fin
        out.append((t, t_sig, es_malo(e.get("nuevo"))))
    return out


def _malo_situacion(v: Any) -> bool | None:
    return None if v is None else (str(v) not in ESTADOS_OK)


def _malo_stock(v: Any) -> bool | None:
    x = _f(v)
    return None if x is None else x <= 0


# ── panel ─────────────────────────────────────────────────────────────────────
def construir_panel(hasta: dt.date | None = None, dias: int = VENTANA_DIAS,
                    indice: str = "cuenta") -> dict[str, Any]:
    """Panel publicación×día y publicación×semana ISO de las ML FULL con serie de visitas.

    ``indice``: "cuenta" (DISENO: visitas de la cuenta), "por_publicacion" (mediana
    de las visitas normalizadas de las publicaciones con oferta) o "ninguno".
    """
    t0 = time.monotonic()
    ins = _insumos()
    hasta = hasta or (almacen.hoy_cdmx() - dt.timedelta(days=1))  # hoy está incompleto
    desde = hasta - dt.timedelta(days=dias - 1)
    n = dias
    fechas = [desde + dt.timedelta(days=k) for k in range(n)]
    pos = {d.isoformat(): k for k, d in enumerate(fechas)}
    fin_global = _inicio_dia(hasta + dt.timedelta(days=1))
    avisos: list[str] = [f"falta crudo: {x}" for x in ins["faltan"]]

    universo = {u["id"]: u for u in ins["universo"] if u.get("id") and u.get("cuenta") in CUENTAS}
    cat2raiz, sku2raiz, nombres_raiz = _mapa_categorias(ins["comp"], ins["universo"])
    por_par: dict[tuple[str, str], list[str]] = defaultdict(list)
    for u in universo.values():
        if u.get("sku"):
            por_par[(str(u["sku"]).strip().upper(), u["cuenta"])].append(u["id"])

    series = ins["series"]
    items: dict[str, dict[str, Any]] = {}
    for lid, serie in series.items():
        u = universo.get(lid)
        if not u:
            continue
        cuenta = u["cuenta"]
        sku = str(u.get("sku") or "").strip().upper()
        v = [None] * n
        vivo = [False] * n
        for fecha, total in serie:
            k = pos.get(fecha)
            if k is not None:
                v[k] = int(total or 0)
                vivo[k] = True
        creado = _ts_cdmx(u.get("date_created"))
        items[lid] = {
            "id": _id(cuenta, lid), "listing_id": lid, "cuenta": cuenta, "sku": sku,
            "titulo": u.get("titulo"), "categoria_id": u.get("category_id"),
            "raiz_id": cat2raiz.get(u.get("category_id") or "") or sku2raiz.get(sku),
            "es_full": bool(u.get("es_full")), "status": u.get("status"),
            "creado": creado.date().isoformat() if creado else None,
            "u": [0] * n, "r": [0.0] * n, "v": v, "vivo": vivo,
        }

    # Ventas (día CDMX). Las 410 filas sin item_id (backfill de Woo, jul) se casan
    # por sku×cuenta solo si el par tiene UNA publicación.
    sin_id = sin_casar = 0
    for f in ins["ventas"]:
        k = pos.get(f.get("fecha"))
        if k is None or f.get("cuenta") not in CUENTAS:
            continue
        lid = f.get("item_id")
        if not lid:
            sin_id += 1
            cands = por_par.get((str(f.get("sku") or "").strip().upper(), f["cuenta"])) or []
            lid = cands[0] if len(cands) == 1 else None
            if lid is None:
                sin_casar += 1
                continue
        it = items.get(lid)
        if it is None:
            continue
        it["u"][k] += int(f.get("units_sold") or 0)
        it["r"][k] += float(f.get("revenue") or 0.0)
    if sin_id:
        avisos.append(f"{sin_id} filas de venta sin item_id; {sin_casar} no se pudieron casar por sku×cuenta")

    # Estados: stock_hist por item_id + listing_history por sku×cuenta (si es único).
    hist = ins["hist"]
    sh_por_item: dict[str, list[dict]] = defaultdict(list)
    for f in hist.get("stock_hist") or []:
        if f.get("item_id") in items:
            sh_por_item[f["item_id"]].append(f)
    ev_por_item: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    ambiguos = set()
    for e in hist.get("censura") or []:
        par = (str(e.get("sku") or "").strip().upper(), e.get("cuenta"))
        cands = [c for c in por_par.get(par, []) if c in items]
        if len(cands) != 1:
            if len(por_par.get(par, [])) > 1:
                ambiguos.add(par)
            continue
        ev_por_item[cands[0]][e.get("campo")].append(e)

    inicio_lh = _inicio_dia(INICIO_LISTING_HISTORY)
    causas = Counter()
    for lid, it in items.items():
        est = _Estados(desde, n)
        ivs, ultimo_sh = _intervalos_stock_hist(sh_por_item.get(lid, []))
        for ini, fin, malo in ivs:
            est.marcar(ini, fin, malo)
        evs = ev_por_item.get(lid, {})
        ivs_lh = _intervalos_eventos(evs.get("situacion", []), _malo_situacion, inicio_lh, fin_global)
        if it["es_full"]:
            ivs_lh += _intervalos_eventos(evs.get("stock_full", []), _malo_stock, inicio_lh, fin_global)
        for ini, fin, malo in ivs_lh:
            est.marcar(ini, fin, malo)
        # Arrastre del último estado de stock_hist hasta donde listing_history empieza a saber.
        conocidos_lh = [ini for ini, _, m in ivs_lh if m is not None]
        hasta_arrastre = min(conocidos_lh) if conocidos_lh else fin_global
        if ultimo_sh is not None and hasta_arrastre > inicio_lh:
            est.marcar(inicio_lh, hasta_arrastre, ultimo_sh)
        estado = est.estado()

        u, v, vivo = it["u"], it["v"], it["vivo"]
        cens = [False] * n
        for k in range(n):
            if u[k] > 0:
                continue
            if not vivo[k]:
                cens[k] = True
                causas["no_existia"] += 1
            elif estado[k] is True:
                cens[k] = True
                causas["pausada_o_sin_stock"] += 1
        # Rachas de ≥7 días en 0 visitas.
        k = 0
        while k < n:
            if vivo[k] and v[k] == 0:
                j = k
                while j < n and vivo[j] and v[j] == 0:
                    j += 1
                if j - k >= RACHA_CERO_VISITAS and any(estado[x] is not False for x in range(k, j)):
                    for x in range(k, j):
                        if u[x] == 0 and not cens[x]:
                            cens[x] = True
                            causas["racha_cero_visitas"] += 1
                k = j
            else:
                k += 1
        it["cens"] = cens
        it["estado"] = estado
        it["estado_conocido_dias"] = sum(1 for e in estado if e is not None)

    # Precio de lista del historial (último recurso para semanas sin venta).
    lista_por_item = _precio_lista(items, sh_por_item, hist.get("precio") or [], por_par, desde, n)

    # Índices de estacionalidad.
    idx_dia = _indice_cuenta(ins["vcuenta"], fechas)
    semanas = _semanas(fechas)
    idx_sem_cuenta = {c: {s["clave"]: _media([idx_dia[c][k] for k in s["dias"] if idx_dia[c][k]])
                          for s in semanas} for c in idx_dia}

    for it in items.values():
        it["semanas"] = _agregar_semanas(it, semanas, lista_por_item.get(it["listing_id"]))

    idx_sem_pub = _indice_por_publicacion(items, semanas)
    for it in items.values():
        c = it["cuenta"]
        for s in it["semanas"]:
            if indice == "cuenta":
                s["idx"] = idx_sem_cuenta.get(c, {}).get(s["semana"]) or 1.0
            elif indice == "por_publicacion":
                s["idx"] = idx_sem_pub.get(c, {}).get(s["semana"]) or 1.0
            else:
                s["idx"] = 1.0

    resumen = {
        "desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": n,
        "publicaciones": len(items),
        "con_venta": sum(1 for it in items.values() if sum(it["u"]) > 0),
        "unidades": sum(sum(it["u"]) for it in items.values()),
        "dias_censurados": dict(causas),
        "item_dias": n * len(items),
        "pares_sku_cuenta_ambiguos_en_historial": len(ambiguos),
        "fuente_precio_semanas": dict(Counter(s["precio_fuente"] for it in items.values()
                                              for s in it["semanas"] if s["valida"])),
        "semanas_validas": sum(1 for it in items.values() for s in it["semanas"] if s["valida"]),
        "indice": indice,
        "indice_cuenta_semanal": {c: {k: _r(v, 3) for k, v in d.items()} for c, d in idx_sem_cuenta.items()},
        "duracion_s": round(time.monotonic() - t0, 2),
        "avisos": avisos,
    }
    return {"desde": desde, "hasta": hasta, "fechas": fechas, "semanas": semanas,
            "idx_dia": idx_dia, "items": items, "nombres_raiz": nombres_raiz, "resumen": resumen}


def _media(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _semanas(fechas: list[dt.date]) -> list[dict[str, Any]]:
    """Semanas ISO (lunes-domingo) con los índices de día de la ventana."""
    out: dict[str, dict[str, Any]] = {}
    for k, d in enumerate(fechas):
        y, w, _ = d.isocalendar()
        clave = f"{y}-W{w:02d}"
        s = out.setdefault(clave, {"clave": clave, "lunes": (d - dt.timedelta(days=d.weekday())).isoformat(),
                                   "dias": []})
        s["dias"].append(k)
    lista = list(out.values())
    for j, s in enumerate(lista):
        s["orden"] = j
        s["completa"] = len(s["dias"]) == 7
    return lista


def _indice_cuenta(vcuenta: list[dict], fechas: list[dt.date]) -> dict[str, list[float | None]]:
    """Visitas diarias de la cuenta / su media en la ventana (1.0 = día típico)."""
    out: dict[str, list[float | None]] = {}
    for r in vcuenta:
        c = r.get("cuenta")
        if c not in CUENTAS:
            continue
        m = {f: int(t or 0) for f, t in (r.get("serie") or [])}
        vals = [m.get(d.isoformat()) for d in fechas]
        pres = [x for x in vals if x]
        base = sum(pres) / len(pres) if pres else None
        out[c] = [(x / base if (x and base) else None) for x in vals]
    for c in CUENTAS:
        out.setdefault(c, [None] * len(fechas))
    return out


def _indice_por_publicacion(items: dict[str, dict], semanas: list[dict]) -> dict[str, dict[str, float]]:
    """Alternativa al índice de cuenta: mediana, entre publicaciones con oferta, de
    sus visitas semanales / su propia media. No se contamina con el tamaño del
    catálogo activo (pausar publicaciones no mueve la mediana de las demás)."""
    acumula: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for it in items.values():
        vs = [s for s in it["semanas"] if s["valida"] and s["v7"] is not None]
        if len(vs) < 8:
            continue
        m = sum(s["v7"] for s in vs) / len(vs)
        if m < 5:
            continue
        for s in vs:
            acumula[it["cuenta"]][s["semana"]].append(s["v7"] / m)
    return {c: {w: median(xs) for w, xs in d.items() if len(xs) >= 20} for c, d in acumula.items()}


def _precio_lista(items: dict, sh_por_item: dict, precios: list[dict], por_par: dict,
                  desde: dt.date, n: int) -> dict[str, list[float | None]]:
    """Precio de LISTA por día (stock_hist hasta el 15-jul; listing_history después,
    solo pares sku×cuenta con una publicación). Se usa escalado, ver `_agregar_semanas`."""
    out: dict[str, list[float | None]] = {}
    ev: dict[str, list[tuple[dt.datetime, float | None, float | None]]] = defaultdict(list)
    for e in precios:
        par = (str(e.get("sku") or "").strip().upper(), e.get("cuenta"))
        cands = [c for c in por_par.get(par, []) if c in items]
        if len(cands) != 1:
            continue
        t = _ts_cdmx(e.get("changed_at"))
        if t:
            ev[cands[0]].append((t, _f(e.get("anterior")), _f(e.get("nuevo"))))
    for lid in items:
        p: list[float | None] = [None] * n
        for f in sh_por_item.get(lid, []):
            pr = _f(f.get("price"))
            if not pr or pr <= 0:
                continue
            try:
                vf = dt.date.fromisoformat(f["valid_from"])
                vt = dt.date.fromisoformat(f["valid_to"]) if f.get("valid_to") else INICIO_LISTING_HISTORY
            except (TypeError, ValueError, KeyError):
                continue
            for k in range(max(0, (vf - desde).days), min(n, (max(vt, vf + dt.timedelta(days=1)) - desde).days)):
                p[k] = pr
        evs = sorted(ev.get(lid, []))
        if evs:
            k0 = max(0, (INICIO_LISTING_HISTORY - desde).days)
            if evs[0][1]:
                for k in range(k0, min(n, (evs[0][0].date() - desde).days)):
                    p[k] = evs[0][1]
            for j, (t, _, nuevo) in enumerate(evs):
                if not nuevo:
                    continue
                fin = evs[j + 1][0].date() if j + 1 < len(evs) else desde + dt.timedelta(days=n)
                for k in range(max(0, (t.date() - desde).days), min(n, (fin - desde).days)):
                    p[k] = nuevo
        if any(x for x in p):
            out[lid] = p
    return out


def _agregar_semanas(it: dict, semanas: list[dict], lista: list[float | None] | None) -> list[dict]:
    u, r, v, cens = it["u"], it["r"], it["v"], it["cens"]
    filas = []
    for s in semanas:
        validos = [k for k in s["dias"] if not cens[k]]
        dv = len(validos)
        uu = sum(u[k] for k in validos)
        rr = sum(r[k] for k in validos)
        vv = sum(v[k] or 0 for k in validos) if any(v[k] is not None for k in validos) else None
        lista_s = None
        if lista:
            ls = [lista[k] for k in s["dias"] if lista[k]]
            lista_s = median(ls) if ls else None
        filas.append({
            "semana": s["clave"], "lunes": s["lunes"], "orden": s["orden"], "dias_validos": dv,
            "valida": dv >= MIN_DIAS_SEMANA, "u": uu, "r": round(rr, 2), "v": vv,
            "u7": uu * 7.0 / dv if dv else None,
            "v7": (vv * 7.0 / dv) if (dv and vv is not None) else None,
            "precio": (rr / uu) if uu > 0 else None,
            "precio_fuente": "realizado" if uu > 0 else None,
            "lista": lista_s,
        })
    # Semanas válidas sin venta: arrastre ±2 semanas; luego lista × razón realizado/lista.
    con_precio = [f for f in filas if f["precio_fuente"] == "realizado"]
    razones = [f["precio"] / f["lista"] for f in con_precio if f["lista"]]
    razon = median(razones) if len(razones) >= 2 else None
    if razon is not None and not (0.2 <= razon <= 1.2):
        razon = None
    for f in filas:
        if not f["valida"] or f["precio"] is not None:
            continue
        mejor = None
        for dist in range(1, ARRASTRE_SEMANAS + 1):
            lados = [g["precio"] for g in con_precio if abs(g["orden"] - f["orden"]) == dist]
            if lados:
                mejor = math.exp(sum(math.log(x) for x in lados) / len(lados))
                break
        if mejor is not None:
            f["precio"], f["precio_fuente"] = mejor, "arrastrado"
        elif f["lista"] and razon:
            f["precio"], f["precio_fuente"] = f["lista"] * razon, "historial"
        else:
            f["precio_fuente"] = "sin_precio"
    return filas


# ── estimación ────────────────────────────────────────────────────────────────
def _ols_within(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Pendiente y error estándar de y sobre x, ambas centradas en la media del item
    (efecto fijo). gl = n − 2 (media + pendiente)."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    rss = sum(((y - my) - b * (x - mx)) ** 2 for x, y in zip(xs, ys))
    s2 = rss / (n - 2)
    return b, math.sqrt(s2 / sxx)


def _cv(ps: list[float]) -> float:
    if len(ps) < 2:
        return 0.0
    m = sum(ps) / len(ps)
    var = sum((p - m) ** 2 for p in ps) / (len(ps) - 1)
    return math.sqrt(var) / m if m > 0 else 0.0


def _ajustar_items(items: dict[str, dict], semanas_ok: set[str] | None = None) -> dict[str, dict]:
    """Regresión within-item por publicación (unidades y visitas)."""
    out: dict[str, dict] = {}
    for lid, it in items.items():
        obs = [s for s in it["semanas"] if s["valida"] and s["precio"] and s["precio"] > 0
               and (semanas_ok is None or s["semana"] in semanas_ok)]
        ps = [s["precio"] for s in obs]
        cv = _cv(ps)
        e: dict[str, Any] = {"n_semanas": len(obs), "cv_precio": round(cv, 4), "beta": None, "se": None,
                             "beta_visitas": None, "se_v": None,
                             "unidades": sum(s["u"] for s in obs)}
        if len(obs) >= MIN_SEMANAS and cv >= MIN_CV_PRECIO and e["unidades"] > 0:
            xs = [math.log(s["precio"]) for s in obs]
            ys = [math.log(s["u7"] / s["idx"] + 0.5) for s in obs]
            r = _ols_within(xs, ys)
            if r:
                e["beta"], e["se"] = r[0], max(r[1], SE_PISO)
            ov = [s for s in obs if s["v7"] is not None]
            if len(ov) >= MIN_SEMANAS and _cv([s["precio"] for s in ov]) >= MIN_CV_PRECIO:
                rv = _ols_within([math.log(s["precio"]) for s in ov],
                                 [math.log(s["v7"] / s["idx"] + 0.5) for s in ov])
                if rv:
                    e["beta_visitas"], e["se_v"] = rv[0], max(rv[1], SE_PISO)
        out[lid] = e
    return out


def _mezcla(bs: list[float], ses: list[float], piso: float, tope: float | None = None) -> dict[str, Any]:
    """Media ponderada de efectos aleatorios con τ² robusto (MAD − mediana de se²).

    ``tope`` acota τ² por arriba (ver `TOPES_TAU2`): la fórmula mide heterogeneidad
    + error de especificación, y un τ² inflado casi no encoge."""
    k = len(bs)
    var_rob = _mad_var(bs)
    ruido = median(s * s for s in ses) if ses else 0.0
    var_simple = (sum((b - sum(bs) / k) ** 2 for b in bs) / (k - 1)) if k > 1 else 0.0
    ruido_medio = (sum(s * s for s in ses) / k) if k else 0.0
    tau2_formula = max(piso, var_rob - ruido)
    tau2 = tau2_formula if tope is None else max(piso, min(tope, tau2_formula))
    w = [1.0 / (s * s + tau2) for s in ses]
    sw = sum(w)
    beta = sum(wi * b for wi, b in zip(w, bs)) / sw
    return {"beta": beta, "se": math.sqrt(1.0 / sw), "tau2": tau2, "n": k,
            "tau2_formula": tau2_formula,
            "tau2_formula_simple": max(piso, var_simple - ruido_medio)}


def _encoger(raw: dict[str, dict], items: dict[str, dict], campo: str, campo_se: str,
             prior: float, lim: tuple[float, float], tope: float | None = None
             ) -> tuple[dict[str, dict], dict[str, dict], dict]:
    """Empírico-bayesiano: publicación → categoría raíz (≥5) → global → prior."""
    con = {lid: e for lid, e in raw.items() if e.get(campo) is not None}
    if len(con) >= MIN_ITEMS_GRUPO:
        g = _mezcla([e[campo] for e in con.values()], [e[campo_se] for e in con.values()], TAU2_PISO, tope)
        glob = {**g, "fuente": "global"}
    else:
        glob = {"beta": prior, "se": None, "tau2": TAU2_PISO * 4, "n": len(con), "fuente": "prior"}

    por_grupo: dict[str, list[str]] = defaultdict(list)
    for lid in con:
        rz = items[lid].get("raiz_id")
        if rz:
            por_grupo[rz].append(lid)
    grupos: dict[str, dict] = {}
    for rz, lids in por_grupo.items():
        if len(lids) < MIN_ITEMS_GRUPO:
            continue
        grupos[rz] = _mezcla([con[x][campo] for x in lids], [con[x][campo_se] for x in lids], TAU2_PISO, tope)
    # Las medias de categoría se encogen hacia la global.
    if grupos and glob["fuente"] == "global":
        bs = [g["beta"] for g in grupos.values()]
        ses = [g["se"] for g in grupos.values()]
        tau2_b = max(TAU2_PISO_GRUPOS, _mad_var(bs) - median(s * s for s in ses)) if len(bs) >= 3 else TAU2_PISO_GRUPOS
        glob["tau2_entre_categorias"] = tau2_b
        for g in grupos.values():
            g["beta_crudo"] = g["beta"]
            g["beta"] = ((g["beta"] / g["se"] ** 2 + glob["beta"] / tau2_b)
                         / (1.0 / g["se"] ** 2 + 1.0 / tau2_b))

    final: dict[str, dict] = {}
    for lid, it in items.items():
        e = raw.get(lid) or {}
        rz = it.get("raiz_id")
        if rz in grupos:
            meta, fuente_meta = grupos[rz], "categoria"
        else:
            meta, fuente_meta = glob, glob["fuente"]
        b, se = e.get(campo), e.get(campo_se)
        if b is not None:
            prec = 1.0 / se ** 2 + 1.0 / meta["tau2"]
            post = (b / se ** 2 + meta["beta"] / meta["tau2"]) / prec
            post_se = math.sqrt(1.0 / prec)
            fuente = "item"
        else:
            post = meta["beta"]
            post_se = math.sqrt(meta["tau2"] + (meta.get("se") or 0.0) ** 2)
            fuente = fuente_meta
        final[lid] = {"valor": min(lim[1], max(lim[0], post)), "sin_recorte": post, "se": post_se,
                      "fuente": fuente, "meta": fuente_meta, "crudo": b, "se_crudo": se}
    return final, grupos, glob


def _confianza(fu: dict) -> str:
    if fu["fuente"] == "item" and fu["se_crudo"] is not None and fu["se_crudo"] < SE_ALTA:
        return "alta"
    base = fu["meta"] if fu["fuente"] == "item" else fu["fuente"]
    return "media" if base == "categoria" else "baja"


def _combinar(items: dict, raw: dict, par_opt: dict, tope: float | None = None) -> tuple[dict[str, dict], dict]:
    prior_u = float(par_opt.get("elasticidad_prior_unidades", -1.6))
    prior_v = float(par_opt.get("elasticidad_prior_visitas", -0.6))
    lo, hi = float(par_opt.get("elasticidad_min", -6.0)), float(par_opt.get("elasticidad_max", -0.3))
    fu, gru, glu = _encoger(raw, items, "beta", "se", prior_u, (lo, hi), tope)
    fv, grv, glv = _encoger(raw, items, "beta_visitas", "se_v", prior_v, (lo, 0.0), tope)
    res: dict[str, dict] = {}
    for lid, it in items.items():
        b, bv = fu[lid], fv[lid]
        beta, beta_v = b["valor"], bv["valor"]
        beta_v = max(beta_v, beta)  # βc = β − βv ≤ 0: la conversión no sube con el precio
        e = raw.get(lid) or {}
        res[lid] = {
            "beta": round(beta, 4), "se": _r(b["se"]), "beta_visitas": round(beta_v, 4), "se_v": _r(bv["se"]),
            "beta_conversion": round(beta - beta_v, 4), "fuente": b["fuente"],
            "fuente_visitas": bv["fuente"], "n_semanas": e.get("n_semanas", 0),
            "cv_precio": e.get("cv_precio"), "confianza": _confianza(b),
            "beta_item_crudo": _r(b["crudo"]), "se_item_crudo": _r(b["se_crudo"]),
            "beta_visitas_item_crudo": _r(bv["crudo"]),
            "recortada": abs(b["sin_recorte"] - beta) > 1e-9,
        }
    meta = {"unidades": {"global": glu, "grupos": gru}, "visitas": {"global": glv, "grupos": grv}}
    return res, meta


# ── base para el optimizador ──────────────────────────────────────────────────
def base_item(it: dict, panel: dict, dias_base: int = 28) -> dict[str, Any]:
    """U0, V0 y precio realizado de los últimos `dias_base` días NO censurados
    (para una pausada: su último periodo con oferta). `factor_estacional` lleva
    esas unidades a la temporada de los últimos 28 días de calendario."""
    n = len(panel["fechas"])
    elegidos = []
    for k in range(n - 1, -1, -1):
        if not it["cens"][k]:
            elegidos.append(k)
            if len(elegidos) >= dias_base:
                break
    idx = panel["idx_dia"].get(it["cuenta"]) or [None] * n
    if not elegidos:
        return {"dias": 0, "u0": None, "v0": None, "p_base": None, "factor_estacional": 1.0}
    uu = sum(it["u"][k] for k in elegidos)
    rr = sum(it["r"][k] for k in elegidos)
    tiene_v = any(it["v"][k] is not None for k in elegidos)
    vv = sum(it["v"][k] or 0 for k in elegidos) if tiene_v else None
    i_base = _media([idx[k] for k in elegidos if idx[k]])
    i_rec = _media([x for x in idx[max(0, n - dias_base):] if x])
    factor = (i_rec / i_base) if (i_base and i_rec) else 1.0
    d = len(elegidos)
    return {"dias": d, "desde": panel["fechas"][min(elegidos)].isoformat(),
            "hasta": panel["fechas"][max(elegidos)].isoformat(),
            "continua": max(elegidos) == n - 1, "unidades": uu, "visitas": vv, "ingreso": round(rr, 2),
            "u0": round(uu / d, 4), "v0": None if vv is None else round(vv / d, 3),
            "p_base": round(rr / uu, 2) if uu > 0 else None, "factor_estacional": round(factor, 4)}


# ── validación fuera de muestra ───────────────────────────────────────────────
def _validar(panel: dict, par_opt: dict, tope: float | None = None) -> dict[str, Any]:
    """Entrena con las semanas completas 1..N−4 y predice las unidades de las
    últimas 4 con el precio que de verdad se cobró. Modelos:

    - ``ingenuo``: mismas unidades/semana que las últimas 4 de entrenamiento.
    - ``estacional``: ingenuo × índice de la semana / índice de la base.
    - ``modelo``: estacional × (p_t / p_base)^β  (β encogida y recortada).
    - ``beta_global`` / ``beta_prior``: igual con una sola β para todos.
    """
    completas = [s["clave"] for s in panel["semanas"] if s["completa"]]
    if len(completas) < SEMANAS_PRUEBA + MIN_SEMANAS + 2:
        return {"error": "semanas insuficientes"}
    prueba, entreno = completas[-SEMANAS_PRUEBA:], completas[:-SEMANAS_PRUEBA]
    base_s = set(entreno[-4:])
    items = panel["items"]
    raw = _ajustar_items(items, set(entreno))
    res, meta = _combinar(items, raw, par_opt, tope)
    b_glob = meta["unidades"]["global"]["beta"]
    lo, hi = float(par_opt.get("elasticidad_min", -6.0)), float(par_opt.get("elasticidad_max", -0.3))
    b_glob = min(hi, max(lo, b_glob))
    b_prior = float(par_opt.get("elasticidad_prior_unidades", -1.6))

    modelos = ("ingenuo", "estacional", "modelo", "beta_global", "beta_prior")
    acc = {m: {"abs": 0.0, "err": 0.0} for m in modelos}
    acc_cambio = {m: {"abs": 0.0, "err": 0.0} for m in modelos}
    acc_conf: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: {m: {"abs": 0.0, "err": 0.0} for m in modelos})
    real_tot = real_cambio = 0.0
    real_conf: Counter = Counter()
    ape_item = {m: [] for m in modelos}
    n_obs = n_cambio = n_items = 0
    for lid, it in items.items():
        sem = {s["semana"]: s for s in it["semanas"]}
        base = [sem[w] for w in base_s if w in sem and sem[w]["valida"]]
        test = [sem[w] for w in prueba if w in sem and sem[w]["valida"]]
        if len(base) < 2 or not test:
            continue
        ub = sum(s["u7"] for s in base) / len(base)
        ub_adj = sum(s["u7"] / s["idx"] for s in base) / len(base)
        pb = [s["precio"] for s in base if s["precio"]]
        p_base = math.exp(sum(math.log(p) for p in pb) / len(pb)) if pb else None
        beta = res[lid]["beta"]
        conf = res[lid]["confianza"]
        n_items += 1
        tot = {m: 0.0 for m in modelos}
        tot_real = 0.0
        for s in test:
            real = s["u7"]
            rel = (s["precio"] / p_base) if (p_base and s["precio"]) else 1.0
            pred = {"ingenuo": ub, "estacional": ub_adj * s["idx"],
                    "modelo": ub_adj * s["idx"] * rel ** beta,
                    "beta_global": ub_adj * s["idx"] * rel ** b_glob,
                    "beta_prior": ub_adj * s["idx"] * rel ** b_prior}
            cambio = abs(math.log(rel)) >= 0.05
            n_obs += 1
            real_tot += real
            real_conf[conf] += real
            if cambio:
                n_cambio += 1
                real_cambio += real
            for m in modelos:
                e = pred[m] - real
                acc[m]["abs"] += abs(e)
                acc[m]["err"] += e
                acc_conf[conf][m]["abs"] += abs(e)
                acc_conf[conf][m]["err"] += e
                if cambio:
                    acc_cambio[m]["abs"] += abs(e)
                    acc_cambio[m]["err"] += e
                tot[m] += pred[m]
            tot_real += real
        if tot_real >= 4:
            for m in modelos:
                ape_item[m].append(abs(tot[m] - tot_real) / tot_real)

    def _met(a: dict, total: float) -> dict[str, Any]:
        return {m: {"wape": _r(a[m]["abs"] / total), "sesgo": _r(a[m]["err"] / total)} for m in modelos} if total else {}

    return {
        "semanas_entreno": [entreno[0], entreno[-1]], "semanas_prueba": [prueba[0], prueba[-1]],
        "publicaciones": n_items, "item_semanas": n_obs, "item_semanas_con_cambio_precio_5pct": n_cambio,
        "beta_global_entreno": _r(b_glob), "beta_prior": b_prior,
        "todas": _met(acc, real_tot), "con_cambio_precio": _met(acc_cambio, real_cambio),
        "por_confianza": {c: _met(acc_conf[c], real_conf[c]) for c in acc_conf},
        "mape_item_4_semanas": {m: _r(median(v)) if v else None for m, v in ape_item.items()},
        "n_items_mape": len(ape_item["modelo"]),
        "nota": "wape = Σ|pred−real|/Σreal sobre item×semana (u normalizadas a 7 días con oferta); "
                "sesgo = Σ(pred−real)/Σreal; mape_item = mediana del error % del total de 4 semanas "
                "en publicaciones con ≥4 unidades reales",
    }


# ── principal ─────────────────────────────────────────────────────────────────
def _elegir_tope(panel: dict, par_opt: dict) -> tuple[float | None, list[dict], dict | None]:
    """Valida cada tope de τ² fuera de muestra y elige (ver `TOLERANCIA_WAPE`)."""
    tabla, validaciones = [], {}
    for tope in TOPES_TAU2:
        v = _validar(panel, par_opt, tope)
        validaciones[tope] = v
        m = (v.get("con_cambio_precio") or {}).get("modelo") or {}
        mt = (v.get("todas") or {}).get("modelo") or {}
        tabla.append({"tope_tau2": tope if tope is not None else "formula",
                      "wape_con_cambio": m.get("wape"), "sesgo_con_cambio": m.get("sesgo"),
                      "wape_todas": mt.get("wape"), "sesgo_todas": mt.get("sesgo"),
                      "mape_item": (v.get("mape_item_4_semanas") or {}).get("modelo"),
                      "beta_global_entreno": v.get("beta_global_entreno")})
    con = [t for t in tabla if t["wape_con_cambio"] is not None]
    if not con:
        return None, tabla, validaciones.get(None)
    mejor = min(t["wape_con_cambio"] for t in con)
    aceptables = [t for t in con if t["wape_con_cambio"] <= mejor * (1 + TOLERANCIA_WAPE)]
    # "formula" = sin tope = el más alto de todos.
    elegido = max(aceptables, key=lambda t: float("inf") if t["tope_tau2"] == "formula" else t["tope_tau2"])
    tope = None if elegido["tope_tau2"] == "formula" else elegido["tope_tau2"]
    for t in tabla:
        t["elegido"] = t is elegido
    return tope, tabla, validaciones[tope]


def estimar(dias_base: int | None = None, indice: str = "cuenta", escribir: bool = True,
            validar: bool = True, tope_tau2: float | str | None = "validado") -> dict[str, Any]:
    """Construye el panel, estima y escribe `ultimo/elasticidades.json`.

    ``tope_tau2``: "validado" (se elige fuera de muestra, ver `TOPES_TAU2`), None
    (la fórmula de DISENO sin tope) o un número fijo.
    """
    t0 = time.monotonic()
    par = _parametros()
    par_opt = par.get("optimizador") or {}
    dias_base = int(dias_base or par_opt.get("dias_base", 28))
    panel = construir_panel(indice=indice)
    items = panel["items"]
    seleccion = None
    validacion = None
    if tope_tau2 == "validado":
        tope, seleccion, validacion = _elegir_tope(panel, par_opt)
    else:
        tope = None if tope_tau2 is None else float(tope_tau2)
        validacion = _validar(panel, par_opt, tope) if validar else None
    raw = _ajustar_items(items)
    res, meta = _combinar(items, raw, par_opt, tope)

    salida: dict[str, Any] = {}
    for lid, it in items.items():
        r = dict(res[lid])
        r.update({"listing_id": lid, "cuenta": it["cuenta"], "sku": it["sku"],
                  "categoria_id": it["categoria_id"], "raiz_id": it["raiz_id"],
                  "base": base_item(it, panel, dias_base)})
        salida[it["id"]] = r

    nombres = panel["nombres_raiz"]

    def _grupo_json(g: dict, gv: dict | None, rz: str | None = None) -> dict:
        d = {"beta": _r(g.get("beta")), "se": _r(g.get("se")), "tau2": _r(g.get("tau2")),
             "tau2_formula": _r(g.get("tau2_formula")),
             "tau2_formula_simple": _r(g.get("tau2_formula_simple")), "n_items": g.get("n"),
             "beta_crudo": _r(g.get("beta_crudo"))}
        if gv:
            d.update({"beta_visitas": _r(gv.get("beta")), "se_visitas": _r(gv.get("se")),
                      "n_items_visitas": gv.get("n")})
        if rz:
            d["nombre"] = nombres.get(rz)
        return d

    gu, gv = meta["unidades"]["grupos"], meta["visitas"]["grupos"]
    salida["_grupos"] = {rz: _grupo_json(g, gv.get(rz), rz) for rz, g in gu.items()}
    glu, glv = meta["unidades"]["global"], meta["visitas"]["global"]
    salida["_global"] = {**_grupo_json(glu, glv), "fuente": glu.get("fuente"),
                         "tau2_entre_categorias": _r(glu.get("tau2_entre_categorias"))}

    # Diagnóstico.
    crudos = [e["beta"] for e in raw.values() if e.get("beta") is not None]
    crudos_v = [e["beta_visitas"] for e in raw.values() if e.get("beta_visitas") is not None]
    finales = [r["beta"] for r in res.values()]
    por_conf = defaultdict(list)
    for r in res.values():
        por_conf[r["confianza"]].append(r["beta"])
    hist_bins = [-6, -4, -3, -2.5, -2, -1.5, -1, -0.5, -0.3, 0, 1, 99]
    histo = []
    for a, b in zip(hist_bins[:-1], hist_bins[1:]):
        histo.append({"desde": a, "hasta": b, "crudas": sum(1 for x in crudos if a <= x < b),
                      "finales": sum(1 for x in finales if a <= x < b or (b == -0.3 and x == -0.3))})
    motivos = Counter()
    for e in raw.values():
        if e.get("beta") is not None:
            motivos["estimada"] += 1
        elif e["n_semanas"] < MIN_SEMANAS:
            motivos["menos_de_6_semanas"] += 1
        elif e["cv_precio"] < MIN_CV_PRECIO:
            motivos["cv_precio_menor_5pct"] += 1
        else:
            motivos["sin_ventas"] += 1
    diag = {
        "items_con_estimacion_propia": len(crudos),
        "motivos": dict(motivos),
        "beta_cruda": _cuantiles(crudos),
        "pct_positivas_antes_de_encoger": _r(sum(1 for x in crudos if x > 0) / len(crudos)) if crudos else None,
        "pct_positivas_significativas": _r(sum(1 for lid, e in raw.items() if e.get("beta") is not None
                                               and e["beta"] - 2 * e["se"] > 0) / len(crudos)) if crudos else None,
        "beta_visitas_cruda": _cuantiles(crudos_v),
        "pct_visitas_positivas_antes": _r(sum(1 for x in crudos_v if x > 0) / len(crudos_v)) if crudos_v else None,
        "beta_final": _cuantiles(finales),
        "por_confianza": {c: _cuantiles(v) for c, v in por_conf.items()},
        "por_fuente": dict(Counter(r["fuente"] for r in res.values())),
        "recortadas": sum(1 for r in res.values() if r["recortada"]),
        "en_limite_max": sum(1 for r in res.values() if r["beta"] >= float(par_opt.get("elasticidad_max", -0.3)) - 1e-9),
        "histograma": histo,
        "se_cruda": _cuantiles([e["se"] for e in raw.values() if e.get("se") is not None]),
    }
    if validacion is not None:
        validacion = dict(validacion)
        validacion["seleccion_tope_tau2"] = seleccion
    salida["_diagnostico"] = diag
    salida["_validacion"] = validacion
    salida["_panel"] = {k: v for k, v in panel["resumen"].items()}
    salida["_meta"] = {"generado_at": almacen.ahora_iso(), "version": "lab-0.1",
                       "modelo": "log(u7/I+0.5) within-item OLS + EB robusto → raíz → global → prior",
                       "dias_base": dias_base, "indice": indice,
                       "tope_tau2": tope if tope is not None else "formula",
                       "parametros": {k: par_opt.get(k) for k in ("elasticidad_prior_unidades",
                                                                   "elasticidad_prior_visitas",
                                                                   "elasticidad_min", "elasticidad_max")},
                       "duracion_s": round(time.monotonic() - t0, 2)}
    if escribir:
        almacen.escribir_json(almacen.ultimo("elasticidades.json"), salida)
    return salida


def leer() -> dict[str, Any]:
    """`ultimo/elasticidades.json` ({} si aún no se estima)."""
    return almacen.leer_json(almacen.ultimo("elasticidades.json"), {}) or {}


def comparar_indices() -> dict[str, Any]:
    """Validación fuera de muestra con los tres índices de estacionalidad (diagnóstico)."""
    par_opt = (_parametros().get("optimizador") or {})
    out = {}
    for ind in ("cuenta", "por_publicacion", "ninguno"):
        panel = construir_panel(indice=ind)
        out[ind] = _validar(panel, par_opt, 0.25)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Elasticidades del laboratorio de precios (solo archivos).")
    ap.add_argument("--indice", default="cuenta", choices=("cuenta", "por_publicacion", "ninguno"))
    ap.add_argument("--comparar-indices", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.comparar_indices:
        print(json.dumps(comparar_indices(), ensure_ascii=False, indent=1, default=str))
    else:
        s = estimar(indice=a.indice)
        print(json.dumps({k: s[k] for k in ("_global", "_diagnostico", "_validacion", "_panel", "_meta")},
                         ensure_ascii=False, indent=1, default=str))
