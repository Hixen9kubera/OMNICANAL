// api.ts — Cliente del backend FastAPI.

import type {
  CanalInfo,
  DetalleProducto,
  RespuestaProductos,
} from "./types";

const BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}

export interface ListarParams {
  canal: string;
  page?: number;
  perPage?: number;
  search?: string;
  soloPublicados?: boolean;
  cuenta?: string | null;
}

export function listarProductos(
  p: ListarParams,
  signal?: AbortSignal,
): Promise<RespuestaProductos> {
  const q = new URLSearchParams();
  q.set("canal", p.canal);
  q.set("page", String(p.page ?? 1));
  q.set("per_page", String(p.perPage ?? 40));
  if (p.search) q.set("search", p.search);
  if (p.soloPublicados) q.set("solo_publicados", "true");
  if (p.cuenta) q.set("cuenta", p.cuenta);
  return getJSON<RespuestaProductos>(`/api/productos?${q.toString()}`, signal);
}

export function listarCanales(signal?: AbortSignal): Promise<CanalInfo[]> {
  return getJSON<CanalInfo[]>(`/api/canales`, signal);
}

export function detalleProducto(
  sku: string,
  signal?: AbortSignal,
): Promise<DetalleProducto> {
  return getJSON<DetalleProducto>(
    `/api/productos/${encodeURIComponent(sku)}`,
    signal,
  );
}

export async function refrescarCanal(
  canal: string,
  sku: string,
  cuenta?: string | null,
): Promise<Record<string, unknown>> {
  const q = cuenta ? `?cuenta=${encodeURIComponent(cuenta)}` : "";
  const res = await fetch(
    `${BASE}/api/canales/${canal}/refrescar/${encodeURIComponent(sku)}${q}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Refresco falló: ${res.status}`);
  return res.json();
}

export const API_BASE = BASE;
