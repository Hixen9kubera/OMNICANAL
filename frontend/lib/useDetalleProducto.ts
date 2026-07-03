"use client";

// useDetalleProducto — cache + reutilización de datos para el detalle de un SKU.
//
//  #3  Al abrir, pinta la ficha AL INSTANTE con el producto que ya trae la lista
//      (detalle "parcial"), mientras llega el detalle completo.
//  #2  Cache en memoria estilo SWR: reabrir el mismo SKU dentro de TTL no
//      re-consulta; si está "viejo", muestra el cache y revalida en silencio.

import { useCallback, useEffect, useRef, useState } from "react";
import { detalleProducto } from "./api";
import type { DetalleProducto, Producto } from "./types";

const TTL = 3 * 60 * 1000; // 3 min: dentro de esta ventana NO se re-consulta

const cache = new Map<string, { data: DetalleProducto; ts: number }>();

export function setDetalleCache(sku: string, data: DetalleProducto): void {
  cache.set(sku, { data, ts: Date.now() });
}
export function getDetalleCache(sku: string) {
  return cache.get(sku);
}
export function invalidarDetalle(sku: string): void {
  cache.delete(sku);
}

/**
 * Construye un DetalleProducto PARCIAL a partir del producto de la lista, para
 * mostrar la ficha al instante (#3). El canal "general" solo se arma cuando el
 * item viene de WooCommerce (evita etiquetar mal datos de un marketplace).
 */
export function productoADetalleParcial(p: Producto): DetalleProducto {
  const canales =
    p.origen === "woocommerce"
      ? [
          {
            canal: "general",
            publicado: p.publicado,
            item_id: p.item_id,
            url: p.url,
            precio: p.precio,
            precio_base: p.precio_base,
            stock: p.stock,
            stock_real: p.stock_real,
            stock_full: p.stock_full,
            stock_fba: p.stock_fba,
            situacion: p.situacion,
            full: p.full,
            full_label: p.full_label,
            categoria_id: p.categoria_id,
            categoria_path: p.categoria_path,
            estado: p.estado,
            extra: {},
          },
        ]
      : [];
  return {
    sku: p.sku,
    wc_id: p.wc_id,
    odoo_id: p.odoo_id,
    nombre: p.nombre,
    imagen: p.imagen,
    imagenes: p.imagen ? [p.imagen] : [],
    marca: p.marca,
    descripcion: null,
    descripcion_corta: p.descripcion_corta,
    atributos: [],
    precio_base: p.precio_base,
    precio_oferta: null,
    stock_odoo: null,
    costo: null,
    peso_kg: null,
    dimensiones: null,
    canales,
  };
}

export function useDetalleProducto(sku: string | null, inicial?: Producto | null) {
  const [data, setData] = useState<DetalleProducto | null>(null);
  const [cargando, setCargando] = useState(false);
  const inicialRef = useRef(inicial);
  inicialRef.current = inicial;

  const recargar = useCallback(async () => {
    if (!sku) return;
    try {
      const fresco = await detalleProducto(sku);
      setDetalleCache(sku, fresco);
      setData(fresco);
    } catch {
      /* noop */
    }
  }, [sku]);

  useEffect(() => {
    if (!sku) {
      setData(null);
      setCargando(false);
      return;
    }
    const entry = getDetalleCache(sku);
    const now = Date.now();

    if (entry) {
      setData(entry.data); // instantáneo desde cache
      setCargando(false);
      if (now - entry.ts < TTL) return; // fresco → NO re-consultamos
      // viejo → mostramos cache y revalidamos en silencio (SWR)
    } else if (inicialRef.current && inicialRef.current.sku === sku) {
      setData(productoADetalleParcial(inicialRef.current)); // #3 parcial al instante
      setCargando(true);
    } else {
      setData(null);
      setCargando(true);
    }

    const ctrl = new AbortController();
    detalleProducto(sku, ctrl.signal)
      .then((fresco) => {
        setDetalleCache(sku, fresco);
        setData(fresco);
      })
      .catch(() => {})
      .finally(() => setCargando(false));
    return () => ctrl.abort();
    // inicial se lee vía ref para no re-disparar al cambiar su identidad
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  return { data, cargando, recargar };
}
