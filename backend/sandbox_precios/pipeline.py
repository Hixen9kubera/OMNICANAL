"""Pipeline del laboratorio: las etapas en orden, cada una aislada, con bitácora.

    extraer_kubera → extraer_ml → packing100 → costos → elasticidad →
    optimizador → construir → snapshot

`correr(etapas=None, sin_ml=False)` corre todas (o las pedidas, SIEMPRE en este
orden) y devuelve un resumen. Lo llama `api.py` en un hilo (`asyncio.to_thread`)
una vez al día y desde `POST /recalcular`; aquí todo es síncrono.

Reglas (DISENO §0 y §2):

- **Una etapa rota no tumba a las demás ni borra `ultimo/`.** Cada módulo escribe
  sus archivos de forma atómica y SOLO al terminar bien, así que si `optimizador`
  falla, `construir` arma con el `precios.json` de la corrida anterior (y lo dice
  su frescura). La excepción es `EscrituraProhibida`: un candado reventó porque
  algo intentó escribir fuera del laboratorio. Eso es un bug, no un caso a
  manejar: se anota en `estado.json` y se re-lanza (api.py lo registra como tal).
- **`extraer_ml` se salta** con `sin_ml`, si ya hubo una extracción HOY (completa
  o parcial: un día parcial cuenta como hecho — antes bastaba un ítem fallido
  para que cada clic en «Recalcular» repitiera 5,700–8,500 GET), o si la última
  EMPEZÓ hace menos de `LAB_ML_ENFRIAMIENTO_H` horas (6 por omisión; marca
  persistida en `cache/ml_extraccion.json`, se escribe ANTES de llamar a ML para
  que una corrida que truena también cuente). Son ~36 min y ~8,500 GET que
  comparten cupo con producción. `LAB_ML_FORZAR=true` la corre igual (y aun así
  la frena el presupuesto diario de `ml_http`).
- **`packing100` no es diario**: baja y re-indexa packing lists de Drive (y en el
  último peldaño llama a la IA con tope). Corre solo si `ultimo/packing100.json`
  no existe o tiene más de `LAB_PACKING_DIAS` (7) días, o con `LAB_PACKING_DIARIO=true`.
- **`snapshot` solo fotografía lo de HOY**: si `construir` no dejó un
  `publicaciones.json` de hoy (falló, o no se pidió y el de disco es viejo), no se
  toma la foto — un snapshot con datos de ayer fechado hoy falsearía la serie de
  precios que lee `api._SerieSnapshots`. La foto es COMPACTA (`almacen.fotografiar_ultimo`):
  `resumen.json` con {id: {pc, pr, pl, u30, v30, sf, e}} + `metricas.json` +
  `estado.json`, ~1 MB por día en vez de los ~28 MB de copiar `ultimo/`. Se
  guardan para siempre. Los días en el formato viejo se compactan en esta etapa.
- `estado.json` se reescribe al terminar CADA etapa (la web ve el avance) y al
  final se añade la corrida a la bitácora (últimas 30).

CLI: ``python -m sandbox_precios.pipeline [--etapas a,b] [--sin-ml]``
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sandbox_precios import _entorno  # noqa: E402

_entorno.cargar()
from sandbox_precios import almacen  # noqa: E402

log = logging.getLogger("laboratorio.pipeline")

ETAPAS = ("extraer_kubera", "extraer_ml", "packing100", "costos", "elasticidad",
          "optimizador", "construir", "snapshot")
PACKING_DIAS_DEFECTO = 7
_SI = {"1", "true", "si", "sí", "yes"}


def _flag(nombre: str) -> bool:
    return (os.environ.get(nombre) or "").strip().lower() in _SI


def _hoy() -> str:
    return almacen.hoy_cdmx().isoformat()


def _generado_hoy(nombre: str) -> tuple[bool, str | None]:
    """¿`ultimo/<nombre>` se generó hoy (día CDMX)? Lee solo el arranque del archivo
    cuando se puede: `generado_at` va primero en los documentos de construir."""
    p = almacen.ultimo(nombre)
    if not p.exists():
        return False, None
    gen = None
    try:
        with open(p, encoding="utf-8") as fh:
            cabeza = fh.read(200)
        i = cabeza.find('"generado_at":"')
        if i >= 0:
            gen = cabeza[i + 15:cabeza.find('"', i + 15)]
    except OSError:
        pass
    t = None
    if gen:
        try:
            t = dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))
        except ValueError:
            t = None
    if t is None:
        t = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)
        gen = t.isoformat(timespec="seconds")
    return t.astimezone(almacen.CDMX).date().isoformat() == _hoy(), gen


# ── etapas ────────────────────────────────────────────────────────────────────
# Cada una devuelve {"ok", "filas", "avisos", ...}; los imports son perezosos para
# que un módulo roto (o a medio escribir por otro agente) solo tumbe SU etapa.

def _extraer_kubera(**_: Any) -> dict[str, Any]:
    from sandbox_precios import extraer_kubera

    r = extraer_kubera.extraer()
    return {"ok": bool(r.get("ok")), "filas": r.get("filas"), "avisos": list(r.get("avisos") or []),
            "archivos": {k: {"ok": v.get("ok"), "filas": v.get("filas")} for k, v in (r.get("archivos") or {}).items()}}


ENFRIAMIENTO_ML_H_DEFECTO = 6.0


def _enfriamiento_ml_h() -> float:
    try:
        return max(0.0, float(os.environ.get("LAB_ML_ENFRIAMIENTO_H") or ENFRIAMIENTO_ML_H_DEFECTO))
    except ValueError:
        return ENFRIAMIENTO_ML_H_DEFECTO


def _extraer_ml(sin_ml: bool = False, **_: Any) -> dict[str, Any]:
    if sin_ml:
        return {"ok": True, "saltada": True, "filas": None, "avisos": ["saltada: --sin-ml (se usan los crudos existentes)"]}
    forzar = _flag("LAB_ML_FORZAR")
    previo = almacen.leer_json(almacen.crudo("extraer_ml_resumen.json"), None)
    if previo and not forzar:
        estado_prev = "completo" if previo.get("ok") else "PARCIAL"
        return {"ok": True, "saltada": True, "filas": (previo.get("etapas") or {}).get("universo", {}).get("filas"),
                "avisos": [f"saltada: ya hubo extracción de ML hoy ({estado_prev}, {previo.get('generado_at')}); "
                           "un día parcial cuenta como hecho. LAB_ML_FORZAR=true para repetir"]}
    marca_ruta = almacen.ruta("cache", "ml_extraccion.json")
    marca = almacen.leer_json(marca_ruta, {}) or {}
    try:
        ultima = dt.datetime.fromisoformat(str(marca.get("inicio")).replace("Z", "+00:00")) if marca.get("inicio") else None
    except ValueError:
        ultima = None
    horas = _enfriamiento_ml_h()
    if ultima is not None and not forzar:
        edad_h = (dt.datetime.now(dt.timezone.utc) - ultima).total_seconds() / 3600.0
        if edad_h < horas:
            return {"ok": True, "saltada": True, "filas": None,
                    "avisos": [f"saltada: la última extracción de ML empezó hace {edad_h:.1f} h "
                               f"(enfriamiento {horas:g} h, LAB_ML_ENFRIAMIENTO_H); LAB_ML_FORZAR=true para repetir"]}
    # La marca va ANTES de llamar a ML: una corrida que truena a la mitad también cuenta.
    almacen.escribir_json(marca_ruta, {"inicio": almacen.ahora_iso(), "forzada": forzar})
    from sandbox_precios import extraer_ml

    r = extraer_ml.extraer(incremental=True)
    almacen.escribir_json(marca_ruta, {"inicio": (almacen.leer_json(marca_ruta, {}) or {}).get("inicio"),
                                       "fin": almacen.ahora_iso(), "ok": bool(r.get("ok")), "forzada": forzar,
                                       "presupuesto_ml": r.get("presupuesto_ml")})
    avisos = list(r.get("avisos") or [])
    avisos += [f"cuenta abortada {c}: {m}" for c, m in (r.get("cuentas_abortadas") or {}).items()]
    avisos += [f"{e}: {v.get('error')}" for e, v in (r.get("etapas") or {}).items() if not v.get("ok")]
    return {"ok": bool(r.get("ok")), "filas": ((r.get("etapas") or {}).get("universo") or {}).get("filas"),
            "avisos": avisos, "contadores_ml_api": r.get("contadores_ml_api")}


def _packing100(**_: Any) -> dict[str, Any]:
    p = almacen.ultimo("packing100.json")
    dias_max = int(os.environ.get("LAB_PACKING_DIAS") or PACKING_DIAS_DEFECTO)
    if p.exists() and not _flag("LAB_PACKING_DIARIO"):
        edad = (time.time() - p.stat().st_mtime) / 86400.0
        if edad <= dias_max:
            n = len((almacen.leer_json(p, {}) or {}).get("filas") or [])
            return {"ok": True, "saltada": True, "filas": n,
                    "avisos": [f"saltada: packing100.json vigente ({edad:.1f} d ≤ {dias_max}); LAB_PACKING_DIARIO=true para repetir"]}
    from sandbox_precios import packing100

    r = packing100.correr()
    doc = almacen.leer_json(p, {}) or {}
    saltados = len((almacen.leer_json(almacen.ultimo("packing_saltados.json"), {}) or {}).get("filas") or [])
    return {"ok": True, "filas": len(doc.get("filas") or []),
            "avisos": [f"{saltados} SKUs saltados (ver packing_saltados.json)"] if saltados else [],
            "resumen": {k: v for k, v in (r or {}).items() if isinstance(v, (int, float, str, bool))}}


def _costos(**_: Any) -> dict[str, Any]:
    from sandbox_precios import costos_lab

    r = costos_lab.calcular()
    return {"ok": True, "filas": r.get("skus"), "avisos": list(r.get("avisos") or []),
            "por_fuente": r.get("por_fuente")}


def _elasticidad(**_: Any) -> dict[str, Any]:
    from sandbox_precios import elasticidad

    r = elasticidad.estimar()
    panel = r.get("_panel") or {}
    return {"ok": True, "filas": sum(1 for k in r if not k.startswith("_")),
            "avisos": list(panel.get("avisos") or []),
            "beta_global": (r.get("_global") or {}).get("beta")}


def _optimizador(**_: Any) -> dict[str, Any]:
    from sandbox_precios import optimizador

    r = optimizador.optimizar(reestimar=False)
    res = r.get("resumen") or {}
    return {"ok": True, "filas": len(r.get("filas") or []), "avisos": [],
            "con_recomendacion": res.get("con_recomendacion"), "clases": res.get("clases")}


def _construir(**_: Any) -> dict[str, Any]:
    from sandbox_precios import construir

    r = construir.construir()
    avisos = list(r.get("avisos") or [])
    avisos += [f"{k}: {v.get('error')}" for k, v in (r.get("archivos") or {}).items() if not v.get("ok")]
    return {"ok": bool(r.get("ok")), "filas": r.get("filas"), "avisos": avisos,
            "archivos": {k: {kk: vv for kk, vv in v.items() if kk in ("ok", "filas", "duracion_s", "error")}
                         for k, v in (r.get("archivos") or {}).items()}}


def _snapshot(**_: Any) -> dict[str, Any]:
    faltan = []
    for nombre in ("publicaciones.json", "precios.json"):
        de_hoy, gen = _generado_hoy(nombre)
        if not de_hoy:
            faltan.append(f"{nombre} no es de hoy ({gen or 'no existe'})")
    if faltan:
        return {"ok": False, "filas": None,
                "avisos": ["snapshot NO tomado: " + "; ".join(faltan)
                           + " — una foto de ayer fechada hoy falsearía la serie de precios"]}
    destino = almacen.fotografiar_ultimo()
    avisos = []
    # Días que sigan en el formato viejo (copia entera de ultimo/, ~28 MB): se
    # compactan aquí mismo; es idempotente y en régimen no encuentra nada.
    migrados = [m for m in almacen.migrar_snapshots() if m.get("ok")]
    if migrados:
        avisos.append(f"{len(migrados)} snapshots viejos compactados: "
                      + ", ".join(m["dia"] for m in migrados[:10]))
    resumen = almacen.leer_json(destino / almacen.SNAPSHOT_RESUMEN, {}) or {}
    tam = sum(p.stat().st_size for p in destino.iterdir() if p.is_file())
    return {"ok": True, "filas": (resumen.get("_meta") or {}).get("filas"), "avisos": avisos,
            "dia": destino.name, "formato": almacen.SNAPSHOT_FORMATO, "bytes": tam,
            "archivos": sorted(p.name for p in destino.iterdir() if p.is_file()),
            "snapshots": len(almacen.dias_con_snapshot())}


_FUNCIONES: dict[str, Callable[..., dict[str, Any]]] = {
    "extraer_kubera": _extraer_kubera, "extraer_ml": _extraer_ml, "packing100": _packing100,
    "costos": _costos, "elasticidad": _elasticidad, "optimizador": _optimizador,
    "construir": _construir, "snapshot": _snapshot,
}


def _estado(etapas: dict[str, Any], corrida: dict[str, Any] | None = None) -> None:
    """Escribe estado.json sin que un fallo aquí tumbe la corrida."""
    try:
        from sandbox_precios import construir

        construir.estado(etapas, corrida)
    except Exception:  # noqa: BLE001
        log.exception("pipeline: no se pudo escribir estado.json")


def _ordenar(etapas: Iterable[str] | None) -> tuple[list[str], list[str]]:
    if not etapas:
        return list(ETAPAS), []
    pedidas = {e.strip() for e in etapas if e and e.strip()}
    return [e for e in ETAPAS if e in pedidas], sorted(pedidas - set(ETAPAS))


def correr(etapas: Iterable[str] | None = None, sin_ml: bool = False) -> dict[str, Any]:
    """Corre las etapas (todas, o las pedidas en el orden canónico). Devuelve
    ``{"ok", "inicio", "fin", "duracion_s", "etapas": {nombre: {...}}, "avisos"}``.

    ``ok`` es False si alguna etapa corrida falló; las saltadas cuentan como ok.
    """
    t0 = time.monotonic()
    inicio = almacen.ahora_iso()
    orden, desconocidas = _ordenar(etapas)
    avisos = [f"etapas desconocidas ignoradas: {desconocidas}"] if desconocidas else []
    if not orden:
        return {"ok": False, "inicio": inicio, "fin": almacen.ahora_iso(), "duracion_s": 0.0,
                "etapas": {}, "avisos": avisos + ["no hay etapas que correr"]}
    log.info("pipeline: %s (sin_ml=%s)", ", ".join(orden), sin_ml)
    resultado: dict[str, dict[str, Any]] = {}
    prohibida: BaseException | None = None
    for nombre in orden:
        t1 = time.monotonic()
        log.info("pipeline: etapa %s", nombre)
        try:
            r = _FUNCIONES[nombre](sin_ml=sin_ml)
        except Exception as exc:  # noqa: BLE001 — se registra y se sigue con la siguiente
            es_candado = type(exc).__name__ == "EscrituraProhibida"
            log.error("pipeline: etapa %s falló: %s", nombre, exc, exc_info=not es_candado)
            r = {"ok": False, "filas": None,
                 "avisos": [("ESCRITURA PROHIBIDA (bug del laboratorio): " if es_candado else "")
                            + f"{type(exc).__name__}: {exc}"[:500]],
                 "error": f"{type(exc).__name__}: {exc}"[:500],
                 "traza": traceback.format_exc(limit=4)[-1500:]}
            if es_candado:
                prohibida = exc
        r.setdefault("avisos", [])
        r["duracion_s"] = round(time.monotonic() - t1, 1)
        r["corrida_at"] = almacen.ahora_iso()
        resultado[nombre] = r
        log.info("pipeline: %s ok=%s filas=%s en %.1fs", nombre, r.get("ok"), r.get("filas"), r["duracion_s"])
        _estado({nombre: r})
        if prohibida is not None:
            avisos.append(f"corrida detenida en {nombre}: un candado de escritura reventó")
            break
    fin = almacen.ahora_iso()
    salida = {"ok": prohibida is None and all(v.get("ok") for v in resultado.values()),
              "inicio": inicio, "fin": fin, "duracion_s": round(time.monotonic() - t0, 1),
              "sin_ml": sin_ml, "etapas": resultado, "avisos": avisos}
    _estado({}, {"inicio": inicio, "fin": fin, "duracion_s": salida["duracion_s"], "ok": salida["ok"],
                 "sin_ml": sin_ml, "etapas": {k: {"ok": v.get("ok"), "duracion_s": v.get("duracion_s"),
                                                  "saltada": v.get("saltada", False)}
                                              for k, v in resultado.items()},
                 "avisos": avisos})
    if prohibida is not None:
        raise prohibida
    return salida


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Pipeline del laboratorio de precios (solo lectura contra producción)")
    ap.add_argument("--etapas", help=f"subconjunto separado por comas de: {','.join(ETAPAS)}")
    ap.add_argument("--sin-ml", action="store_true", help="no llama a la API de ML (usa los crudos que haya)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    res = correr(etapas=a.etapas.split(",") if a.etapas else None, sin_ml=a.sin_ml)
    print(json.dumps({k: v for k, v in res.items()}, ensure_ascii=False, indent=1, default=str)[:30000])
    raise SystemExit(0 if res["ok"] else 1)
