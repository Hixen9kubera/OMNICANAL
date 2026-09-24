"""
fulfillment_ia.py — La planeación semanal CON IA: Claude revisa la propuesta con
el PROMPT ESTÁNDAR de planeación (el que le pasaron a Brandon el 24-sep-2026) y
sugiere ajustes, reemplazos y alertas.

POR QUÉ ASÍ Y NO "QUE LA IA CALCULE"
────────────────────────────────────
Las cantidades las calcula el código con las reglas del mismo prompt
(`proponer.ts`): velocidad, objetivo, faltante, tope por lo libre en Odoo. Un
modelo que suma no es un modelo que decide mejor, y una cifra inventada termina
en una orden de Odoo. Lo que la IA SÍ aporta es juicio: qué ganador agotado
reemplazar y por cuál, qué SKU sube (la semana va muy arriba del mes), qué
publicación parece un SKU reciclado (el título de ML no se parece al producto),
y un resumen que confirma cuenta, ventana y qué es dato en vivo.

Y aun así NADA de lo que diga se aplica solo: cada ajuste se valida aquí contra
los datos que se le mandaron (la tienda existe, el SKU está en su planeación o
entre los reemplazos que el código encontró, la cantidad no pasa de lo libre) y
en la pantalla la persona lo acepta renglón por renglón.

Modelo: Claude Opus 5 con salida JSON validada por esquema (`output_config.format`)
y los reemplazos del servidor ante un rechazo (`fallbacks: "default"`). El SDK del
backend es el 0.42.0, anterior a esos parámetros: van por `extra_body`, que el SDK
manda tal cual, y la respuesta se lee cruda (`with_raw_response`) para no depender
de tipos que esa versión no conoce.

Tarda (lee cientos de renglones y piensa): corre en un hilo y la pantalla pregunta
por el resultado (`iniciar` / `estado`). Nada de esto escribe en ningún lado.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.fulfillment_ia")

MODELO = "claude-opus-5"
MAX_TRABAJOS = 2          # a la vez: cada corrida cuesta (~centavos de dólar) y tarda
VIDA_TRABAJO_S = 3600

# El prompt TAL CUAL lo pasaron (24-sep-2026). Lo que cambió en el panel se dice
# aparte (AJUSTES_DEL_PANEL) en vez de reescribirlo: así se puede comparar.
PROMPT_ESTANDAR = """PLANEACIÓN SEMANAL DE FULL — KUBERA / OMNICANAL

Rol: eres el asistente de planeación de reabasto a los almacenes de marketplace
(FULL de Mercado Libre — cuentas BEKURA=Kubera y SANCORFASHION=San Corpe —,
FBA de Amazon San Corpe, WFS de Walmart). Tu salida es una LISTA de qué SKUs y
cuántas piezas conviene mandar esta semana, lista para pegarse en el apartado
"Planeación semanal" de /fulfillment.

REGLAS DE LA CASA (no negociables):
1. Inventario: ODOO es el MASTER. "Libre Odoo" = free_qty por almacén (lo
   vendible, ya descontadas reservas). Nunca propongas más piezas que free_qty.
2. Ventas = fuente de verdad WooCommerce (pedidos con _ml_cuenta =
   BEKURA/SANCORFASHION). Stock ya en Full = MySQL canal_inventario
   (canal=mercado_libre, stock_full). Todo es LECTURA: no escribas en Woo,
   Odoo, kubera ni ningún marketplace.
3. Un dato ausente NO es un cero: si Bodega aún no revisa un renglón, va como
   "pendiente", no como recorte a 0.
4. Entrega los SKUs separados por coma (para capturarlos en Omnicanal).

CÁLCULO POR SKU:
- velocidad_dia = unidades_vendidas_en_ventana / dias_ventana
- objetivo = ceil(velocidad_dia * cobertura_objetivo)
- faltante = max(0, objetivo - stock_en_full_actual)
- enviar = min(free_qty_odoo, faltante)
- Estado del renglón:
    · "aprobado" si Bodega puede cubrir lo pedido (bodega >= pidió)
    · "recorte"  si free_qty no alcanza (bodega < pidió); Bodega puede = free_qty
    · "pendiente" si Bodega todavía no revisó ese renglón

GANADORES AGOTADOS → REEMPLAZO:
- Ganador agotado = vendió >= 3 en la ventana Y free_qty = 0 Y stock_full = 0.
- Para cada uno sugiere reemplazo con stock, en este orden:
    (1) MISMO MODELO (misma base CAT-NNNN, otro color/talla) con free_qty>0
    (2) MISMA CATEGORÍA ML (categorias_ml) con free_qty>0, mayor free_qty primero
- Solo reemplazos YA PUBLICADOS (en ml_progress con ml_item_id, o en
  canal_inventario ML). Marca si el match fue "misma categoría ML" o el más
  débil "misma por nombre".
- Cruza SIEMPRE contra free_qty antes de declarar "agotado": SOLD = demanda,
  no stockout. Cuidado con SKUs reciclados (categoría/atributos del producto
  viejo).

SALIDA (en este formato):
A) Tabla por SKU: SKU · nombre (usar nombre de Omnicanal) | Destino | Libre Odoo
   | Pidió | Bodega puede | Propuesta | Estado.
B) Totales: piezas pedidas, piezas propuestas, tasa de validado
   (bodega / pedido de renglones revisados), % final vs pedido.
C) Ganadores agotados y su reemplazo sugerido.
D) Lista de SKUs a enviar, separada por coma.
E) Alertas: SKUs sin categoría ML, dimensiones sospechosas (caja master:
   peso<=0.5kg con dims ~60x41x41), reciclados detectados.

Antes de entregar, confirma qué cuenta y ventana usaste, y marca claramente lo
que es dato en vivo vs. lo que quedó pendiente por falta de revisión de Bodega."""

AJUSTES_DEL_PANEL = """CÓMO SE CORRE ESTE PROMPT DENTRO DEL PANEL (manda sobre lo de arriba donde choquen):

- Los datos YA vienen leídos y calculados en el JSON del usuario. No los busques ni los
  recalcules por tu cuenta y NUNCA inventes una cifra que no esté ahí. MySQL
  (canal_inventario, ml_progress) está congelado desde el 13-ago-2026: el panel usa kubera
  (channel.listings, ventas), Mercado Libre verificado en vivo y Odoo en vivo.
- El panel ya aplicó el CÁLCULO POR SKU, con una diferencia deliberada: al faltante también
  le resta lo que ya va en camino y lo que está en borradores, para no mandar dos veces lo
  mismo. En el JSON: "pidio" = faltante, "libre" = bodega puede (free_qty menos el colchón
  para DROP, ya repartido entre tiendas si varias piden el mismo SKU), "propuesta" = enviar,
  "a_mandar" = lo que la persona dejó hoy (puede ser distinto de la propuesta).
- Las tablas A, B y D las arma el panel. Tu trabajo es revisarlas y devolver SÓLO el JSON
  del esquema:
  · "confirmacion": qué tiendas, semana, ventana y cobertura se usaron, y qué es dato en vivo
    contra lo pendiente (renglones sin dato de Odoo).
  · "ajustes": cambios concretos a "a_mandar" que recomiendas, cada uno con su motivo (p. ej.
    la venta de 7 días va muy arriba del ritmo de la ventana; el SKU parece reciclado y no
    conviene mandarlo; redondear a caja completa cuando "caja" lo dice). Sólo SKUs que estén
    en "renglones" de esa tienda o entre los "candidatos" de un ganador agotado, y nunca más
    que su "libre". Si no recomiendas cambios, lista vacía.
  · "reemplazos": para cada ganador agotado, el mejor candidato de SU lista (los candidatos
    ya están publicados y tienen free_qty > 0), con el tipo de match: "mismo modelo",
    "misma categoría ML" o "misma por nombre".
  · "alertas": sin categoría ML, dimensiones sospechosas, reciclados (el título del
    marketplace no corresponde al nombre de Omnicanal) y cualquier cosa rara que veas en los
    datos.
  · "resumen": 3 a 6 frases para quien va a crear la orden: qué se manda, qué no se puede
    mandar por falta de stock en Odoo (señal de compras) y qué revisar antes.
- En "ajustes", "reemplazos" y "alertas", "tienda" es la LLAVE de la tienda tal como viene en
  el objeto "tiendas" del JSON ("meli:Kubera", "meli:San Corpe", "amazon", "walmart"), no su nombre.
- Temu y TikTok son únicamente DROP: no entran en esta planeación.
- Escribe en español de México, directo y sin tecnicismos."""

# La tienda va por su LLAVE: en la primera corrida real (24-sep) la IA escribió «ML Kubera»
# y el validador descartó los 14 ajustes y todos los reemplazos por no reconocerla.
_LLAVES = ["meli:Kubera", "meli:San Corpe", "amazon", "walmart"]

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "confirmacion": {"type": "string"},
        "ajustes": {"type": "array", "items": {
            "type": "object",
            "properties": {"tienda": {"type": "string", "enum": _LLAVES}, "sku": {"type": "string"},
                           "cantidad": {"type": "integer"}, "motivo": {"type": "string"}},
            "required": ["tienda", "sku", "cantidad", "motivo"], "additionalProperties": False}},
        "reemplazos": {"type": "array", "items": {
            "type": "object",
            "properties": {"tienda": {"type": "string", "enum": _LLAVES}, "agotado": {"type": "string"},
                           "reemplazo": {"type": "string"}, "tipo_match": {"type": "string"},
                           "motivo": {"type": "string"}},
            "required": ["tienda", "agotado", "reemplazo", "tipo_match", "motivo"],
            "additionalProperties": False}},
        "alertas": {"type": "array", "items": {
            "type": "object",
            "properties": {"tienda": {"type": "string"}, "sku": {"type": "string"},
                           "tipo": {"type": "string"}, "detalle": {"type": "string"}},
            "required": ["tienda", "sku", "tipo", "detalle"], "additionalProperties": False}},
        "resumen": {"type": "string"},
    },
    "required": ["confirmacion", "ajustes", "reemplazos", "alertas", "resumen"],
    "additionalProperties": False,
}

_trabajos: dict[str, dict[str, Any]] = {}
_candado = threading.Lock()


def disponible() -> bool:
    return bool(settings.anthropic_api_key)


_SINONIMOS = {"kubera": "meli:Kubera", "bekura": "meli:Kubera", "ml kubera": "meli:Kubera",
              "san corpe": "meli:San Corpe", "sancorfashion": "meli:San Corpe", "ml san corpe": "meli:San Corpe",
              "amazon fba": "amazon", "fba": "amazon", "walmart wfs": "walmart", "wfs": "walmart"}


def tienda_de(valor: Any, tiendas: dict[str, Any]) -> str:
    """La llave de la tienda aunque la IA la nombre como la ve («ML Kubera», «BEKURA», «meli_bekura»)."""
    v = str(valor or "").strip()
    if v in tiendas:
        return v
    bajo = v.lower()
    for t, d in tiendas.items():
        if bajo in (t.lower(), str((d or {}).get("nombre") or "").lower(), str((d or {}).get("destino") or "").lower()):
            return t
    return _SINONIMOS.get(bajo, v)


def validar(respuesta: dict[str, Any], datos: dict[str, Any]) -> dict[str, Any]:
    """
    Lo que la IA contestó, pasado por los datos que se le mandaron. Función pura.
    Un ajuste fuera de la planeación o por encima de lo libre NO pasa: se descarta
    y se dice por qué.
    """
    tiendas = datos.get("tiendas") or {}
    permitidos: dict[str, dict[str, int]] = {}
    candidatos: dict[tuple[str, str], set[str]] = {}
    for t, d in tiendas.items():
        libres = {r["sku"]: int(r.get("libre") or 0) for r in d.get("renglones") or []}
        for g in d.get("ganadores_agotados") or []:
            cs = {c["sku"]: int(c.get("libre") or 0) for c in g.get("candidatos") or []}
            candidatos[(t, g["sku"])] = set(cs)
            for s, n in cs.items():
                libres.setdefault(s, n)
        permitidos[t] = libres
    ajustes, descartados = [], []
    for a in respuesta.get("ajustes") or []:
        t, sku = tienda_de(a.get("tienda"), tiendas), str(a.get("sku") or "").strip()
        try:
            n = int(a.get("cantidad"))
        except (TypeError, ValueError):
            descartados.append({**a, "porque": "cantidad no numérica"})
            continue
        if t not in permitidos or sku not in permitidos[t]:
            descartados.append({**a, "porque": "el SKU no está en la planeación de esa tienda"})
            continue
        if n < 0:
            descartados.append({**a, "porque": "cantidad negativa"})
            continue
        tope = permitidos[t][sku]
        nota = None
        if n > tope:
            nota = f"la IA pidió {n}; se topó a lo libre ({tope})"
            n = tope
        ajustes.append({"tienda": t, "sku": sku, "cantidad": n, "motivo": str(a.get("motivo") or "")[:400],
                        "nota": nota})
    reemplazos = []
    for r in respuesta.get("reemplazos") or []:
        t, ag, re_ = tienda_de(r.get("tienda"), tiendas), str(r.get("agotado") or ""), str(r.get("reemplazo") or "")
        if re_ in candidatos.get((t, ag), set()):
            reemplazos.append({"tienda": t, "agotado": ag, "reemplazo": re_,
                               "tipo_match": str(r.get("tipo_match") or "")[:40],
                               "motivo": str(r.get("motivo") or "")[:400]})
        else:
            descartados.append({**r, "porque": "el reemplazo no está entre los candidatos publicados con stock"})
    alertas = [{"tienda": tienda_de(a.get("tienda"), tiendas), "sku": str(a.get("sku") or ""),
                "tipo": str(a.get("tipo") or "")[:40], "detalle": str(a.get("detalle") or "")[:400]}
               for a in (respuesta.get("alertas") or [])][:80]
    return {"confirmacion": str(respuesta.get("confirmacion") or "")[:2000],
            "resumen": str(respuesta.get("resumen") or "")[:2000],
            "ajustes": ajustes, "reemplazos": reemplazos, "alertas": alertas, "descartados": descartados}


def _llamar(datos: dict[str, Any]) -> dict[str, Any]:
    """La llamada a Claude. BLOQUEA (corre en su hilo)."""
    import anthropic

    cli = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=600.0, max_retries=1)
    # Razonamiento adaptativo (el modelo decide cuánto pensar) y la salida atada al esquema.
    cuerpo_extra: dict[str, Any] = {"thinking": {"type": "adaptive"},
                                    "output_config": {"format": {"type": "json_schema", "schema": ESQUEMA}}}
    params = dict(model=MODELO, max_tokens=32000,
                  system=PROMPT_ESTANDAR + "\n\n" + AJUSTES_DEL_PANEL,
                  messages=[{"role": "user", "content": json.dumps(datos, ensure_ascii=False)}])
    try:
        # Con reemplazo del servidor si el modelo declina (skill claude-api).
        raw = cli.beta.messages.with_raw_response.create(
            **params, betas=["server-side-fallback-2026-07-01"],
            extra_body={**cuerpo_extra, "fallbacks": "default"})
    except anthropic.BadRequestError as exc:
        if "fallback" not in str(exc).lower():
            raise
        log.warning("planeación IA: la cuenta no acepta fallbacks (%s); sin ellos", exc)
        raw = cli.messages.with_raw_response.create(**params, extra_body=cuerpo_extra)
    # El SDK instalado (0.42) devuelve un LegacyAPIResponse: sin `.json()`, con `.text`.
    cuerpo = json.loads(raw.text)
    uso = cuerpo.get("usage") or {}
    log.info("planeación IA: %s · entrada %s · salida %s · %s", cuerpo.get("model"), uso.get("input_tokens"),
             uso.get("output_tokens"), cuerpo.get("stop_reason"))
    if cuerpo.get("stop_reason") == "refusal":
        raise RuntimeError("el modelo declinó revisar esta planeación")
    if cuerpo.get("stop_reason") == "max_tokens":
        raise RuntimeError("la respuesta de la IA se cortó (demasiados renglones): prueba con menos tiendas")
    texto = next((b.get("text") for b in cuerpo.get("content") or [] if b.get("type") == "text"), None)
    if not texto:
        raise RuntimeError("la IA no devolvió texto")
    return {"respuesta": json.loads(texto), "modelo": cuerpo.get("model"),
            "tokens": {"entrada": uso.get("input_tokens"), "salida": uso.get("output_tokens")}}


def _podar() -> None:
    ahora = time.time()
    for k in [k for k, v in _trabajos.items() if ahora - v["inicio"] > VIDA_TRABAJO_S]:
        _trabajos.pop(k, None)


def iniciar(datos: dict[str, Any], quien: str = "") -> dict[str, Any]:
    """Arranca la revisión en un hilo. Devuelve el id para preguntar por él."""
    if not disponible():
        return {"ok": False, "motivo": "La IA no está configurada en este ambiente (falta ANTHROPIC_API_KEY)."}
    tiendas = datos.get("tiendas") or {}
    if not any((d.get("renglones") or d.get("ganadores_agotados")) for d in tiendas.values()):
        return {"ok": False, "motivo": "La planeación no trae renglones que revisar."}
    with _candado:
        _podar()
        vivos = [v for v in _trabajos.values() if v["estado"] == "corriendo"]
        if len(vivos) >= MAX_TRABAJOS:
            return {"ok": False, "motivo": "Ya hay revisiones de IA corriendo; espera a que terminen."}
        tid = uuid.uuid4().hex[:12]
        _trabajos[tid] = {"estado": "corriendo", "inicio": time.time(), "quien": quien}

    def correr() -> None:
        try:
            r = _llamar(datos)
            revisado = validar(r["respuesta"], datos)
            _trabajos[tid].update(estado="listo", resultado={**revisado, "modelo": r["modelo"],
                                                              "tokens": r["tokens"]})
        except Exception as exc:  # noqa: BLE001 — el error se enseña, no se esconde
            log.warning("planeación IA: falló (%s)", exc)
            _trabajos[tid].update(estado="error", error=str(exc)[:400])
        finally:
            _trabajos[tid]["fin"] = time.time()

    threading.Thread(target=correr, name=f"planeacion-ia-{tid}", daemon=True).start()
    log.info("planeación IA: %s arrancó (%s)", tid, (quien or "?").split("@")[0])
    return {"ok": True, "id": tid}


def estado(tid: str) -> dict[str, Any]:
    t = _trabajos.get(tid)
    if not t:
        return {"estado": "desconocido", "motivo": "No existe esa revisión (el servidor pudo reiniciarse)."}
    salida = {"estado": t["estado"], "segundos": round((t.get("fin") or time.time()) - t["inicio"])}
    if t["estado"] == "listo":
        salida["resultado"] = t["resultado"]
    if t["estado"] == "error":
        salida["motivo"] = t.get("error")
    return salida
