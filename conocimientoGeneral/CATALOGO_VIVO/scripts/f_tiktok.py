"""
f_tiktok.py — Lo que hay publicado en TikTok Shop (tienda KUBERA), EN VIVO.

  1. CENSO   `/product/202309/products/search`, paginado por `page_token`. TikTok
             lista con un verbo que no es GET, pero es una lectura (ver `comun.py`).
             Trae id, título, estado, auditoría y, por SKU, precio e inventario.
  2. FOTO    `/product/202309/products/{id}` (GET) — el listado no trae imágenes.
             Solo se pide para lo que está a la venta.

DOS MAÑAS DE ESTA API, ambas medidas en producción:
  · Contesta HTTP 200 aunque falle: el veredicto va en `code` del cuerpo.
  · Tiene lista blanca de IP. Fuera de ella responde `36009033 IP not in allow
    list`; eso NO es un problema de token ni de firma, y aquí se reporta tal cual.

La firma es HMAC-SHA256 sobre: secreto + ruta + (parámetros ordenados, sin `sign`
ni `access_token`, pegados clave+valor) + cuerpo + secreto.

El token se lee de kubera y NO se renueva (ver `tokens_kubera.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Any

import tokens_kubera
from comun import Cfg, Red, ahora_iso, aviso, escribir_json, num

API = "https://open-api.tiktokglobalshop.com"
PAGINA = 100
MAX_PAGINAS = 80
ALMACEN_VENTAS = "7647893424175580935"
A_LA_VENTA = {"ACTIVATE"}


class _TikTok:
    def __init__(self, cfg: Cfg, red: Red, acceso: str, cifra: str):
        self.red, self.acceso, self.cifra = red, acceso, cifra
        self.llave_app, self.secreto = cfg("TIKTOK_APP_KEY"), cfg("TIKTOK_APP_SECRET")
        if not (self.llave_app and self.secreto):
            raise RuntimeError("TikTok sin configurar (TIKTOK_APP_KEY / TIKTOK_APP_SECRET)")

    def _firmados(self, ruta: str, params: dict[str, Any], cuerpo: str = "") -> dict[str, Any]:
        p = dict(params)
        p["app_key"] = self.llave_app
        p["timestamp"] = str(int(time.time()))
        partes = "".join(f"{k}{p[k]}" for k in sorted(p) if k not in ("sign", "access_token"))
        envuelto = f"{self.secreto}{ruta}{partes}{cuerpo}{self.secreto}"
        p["sign"] = hmac.new(self.secreto.encode(), envuelto.encode(), hashlib.sha256).hexdigest()
        return p

    def _leer(self, r: Any, ruta: str) -> dict[str, Any]:
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"TikTok {ruta}: respuesta que no es JSON (HTTP {r.status_code})")
        if j.get("code") not in (0, "0"):
            raise RuntimeError(f"TikTok {ruta}: code={j.get('code')} {j.get('message')}")
        return j.get("data") or {}

    def listar(self, pagina: str | None) -> dict[str, Any]:
        ruta = "/product/202309/products/search"
        params: dict[str, Any] = {"shop_cipher": self.cifra, "page_size": PAGINA}
        if pagina:
            params["page_token"] = pagina
        r = self.red.lectura_sin_get(API + ruta, params=self._firmados(ruta, params),
                                     headers={"x-tts-access-token": self.acceso,
                                              "Content-Type": "application/json"}, content="")
        return self._leer(r, ruta)

    def detalle(self, pid: str) -> dict[str, Any]:
        ruta = f"/product/202309/products/{pid}"
        r = self.red.get(API + ruta, params=self._firmados(ruta, {"shop_cipher": self.cifra}),
                         headers={"x-tts-access-token": self.acceso,
                                  "Content-Type": "application/json"})
        return self._leer(r, ruta)


def _fila(p: dict[str, Any]) -> list[dict[str, Any]]:
    """Un producto de TikTok → una fila por SKU (un producto puede traer varios)."""
    pid = str(p.get("id") or "")
    estado = str(p.get("status") or "SIN_ESTADO")
    auditoria = str((p.get("audit") or {}).get("status") or "") or None
    filas = []
    for s in p.get("skus") or []:
        sku = str(s.get("seller_sku") or "").strip()
        if not (pid and sku):
            continue
        pr = s.get("price") or {}
        stock = None
        inventario = s.get("inventory") or []
        for inv in inventario:
            if str(inv.get("warehouse_id") or "") == ALMACEN_VENTAS:
                stock = inv.get("quantity")
                break
        if stock is None and inventario:
            stock = inventario[0].get("quantity")
        filas.append({
            "sku": sku, "id": pid, "titulo": p.get("title"), "imagen": None,
            "precio": num(pr.get("sale_price")) or num(pr.get("tax_exclusive_price")),
            "precio_sin_iva": num(pr.get("tax_exclusive_price")),
            "moneda": pr.get("currency") or "MXN",
            "estado": estado + (f" · {auditoria}" if auditoria and auditoria != "APPROVED" else ""),
            "a_la_venta": estado in A_LA_VENTA, "stock_canal": stock,
            "link": f"https://shop.tiktok.com/view/product/{pid}?region=MX&locale=es-MX",
            "creado": p.get("create_time"), "editado": p.get("update_time"),
        })
    return filas


def extraer(cfg: Cfg, salida: Path) -> dict[str, Any]:
    tok = tokens_kubera.tiktok(cfg)
    if not tok or not tok.get("cifra_tienda"):
        raise RuntimeError("no hay token o shop_cipher legible de TikTok en kubera")
    red = Red(rps=4.0, timeout=60.0)
    tk = _TikTok(cfg, red, tok["acceso"], tok["cifra_tienda"])
    inicio = ahora_iso()
    avisos: list[str] = []

    productos: dict[str, dict[str, Any]] = {}
    declarado = None
    pagina: str | None = None
    for n in range(MAX_PAGINAS):
        data = tk.listar(pagina)
        declarado = data.get("total_count", declarado)
        lote = data.get("products") or []
        for p in lote:
            pid = str(p.get("id") or "")
            if pid:
                productos.setdefault(pid, p)
        aviso(f"tiktok: página {n + 1} · {len(productos)} productos (TikTok declara {declarado})")
        pagina = data.get("next_page_token") or None
        if not pagina or not lote:
            break

    filas: list[dict[str, Any]] = []
    for p in productos.values():
        filas.extend(_fila(p))

    # La foto solo existe en el detalle; se pide para lo que está a la venta.
    vivos = sorted({f["id"] for f in filas if f["a_la_venta"]})
    fotos: dict[str, str] = {}
    for n, pid in enumerate(vivos, 1):
        try:
            d = tk.detalle(pid)
        except Exception as exc:  # noqa: BLE001 — sin foto, pero la fila se queda
            if n <= 3:
                avisos.append(f"detalle {pid}: {str(exc)[:160]}")
            continue
        for im in d.get("main_images") or []:
            url = next(iter(im.get("thumb_urls") or im.get("urls") or []), None)
            if url:
                fotos[pid] = url
                break
        if n % 100 == 0 or n == len(vivos):
            aviso(f"tiktok: fotos {n}/{len(vivos)}")
    for f in filas:
        f["imagen"] = fotos.get(f["id"])

    doc = {
        "canal": "tiktok", "cuenta": tok.get("tienda") or "KUBERA",
        "fuente": "TikTok Shop Open API · products/search + products/{id}",
        "leido_desde": inicio, "leido_hasta": ahora_iso(),
        "declarado_por_el_canal": declarado, "productos": len(productos),
        "publicaciones": len(filas),
        "a_la_venta": sum(1 for f in filas if f["a_la_venta"]),
        "token_de": tok.get("actualizado"), "avisos": avisos, "filas": filas,
    }
    escribir_json(salida / "datos" / "tiktok.json", doc)
    aviso(f"tiktok: {len(filas)} SKUs en {len(productos)} productos, {doc['a_la_venta']} a la venta")
    return {k: v for k, v in doc.items() if k != "filas"}
