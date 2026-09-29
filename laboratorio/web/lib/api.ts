/**
 * Todo lo que la web le pide al laboratorio pasa por aquí.
 *
 * Mismo origen (`/api/lab/*`) y `credentials: "include"`: la sesión es la cookie
 * httpOnly `lab_sesion` que pone `POST /api/lab/sesion` (DISENO §7). La web nunca
 * ve ni guarda la llave.
 *
 * Fixtures: sólo en `next dev` con NEXT_PUBLIC_LAB_FIXTURES=1 (next.config.mjs
 * decide y lo publica como NEXT_PUBLIC_LAB_FIXTURES_ACTIVAS). En el export el
 * módulo `datos-prueba` se sustituye por uno vacío: en producción SIEMPRE fetch.
 *
 * Los sobres de respuesta de §7 no están fijados en el contrato; `aPagina`
 * acepta las variantes razonables (lista pelona, {filas}, {items}) para que la
 * web no se rompa si api.py elige otra.
 */
import type { Pagina } from "./tipos";

export const USA_FIXTURES =
  process.env.NEXT_PUBLIC_LAB_FIXTURES_ACTIVAS === "1" && process.env.NODE_ENV === "development";

const CON_DIAGONAL = process.env.NEXT_PUBLIC_LAB_EXPORT === "1";

/** Ruta interna respetando el `trailingSlash` del export estático. */
export function ruta(p: string): string {
  if (!CON_DIAGONAL || p.endsWith("/") || p.includes("?")) return p;
  return `${p}/`;
}

export class NoAutorizado extends Error {
  constructor() { super("La sesión del laboratorio expiró o no existe."); }
}
export class ErrorApi extends Error {
  status: number;
  constructor(status: number, mensaje: string) { super(mensaje); this.status = status; }
}

export type Params = Record<string, string | number | boolean | null | undefined>;

function consulta(params?: Params): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

let yendoALogin = false;
function irALogin() {
  if (typeof window === "undefined" || yendoALogin) return;
  if (window.location.pathname.startsWith("/login")) return;
  yendoALogin = true;
  const volver = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`${ruta("/login")}?volver=${volver}`);
}

async function fixture<T>(camino: string, params?: Params, metodo = "GET", cuerpo?: unknown): Promise<T | null> {
  if (!USA_FIXTURES) return null;
  const m = await import("./datos-prueba");
  // Latencia fingida: sin ella nunca se ven los estados de carga en desarrollo.
  await new Promise((r) => setTimeout(r, 180));
  return (await m.responder(camino, params ?? {}, metodo, cuerpo)) as T | null;
}

/**
 * GET a `/api/lab{camino}`. Un 401 manda a /login (salvo `sinRedirigir`).
 * `nulo404`: un 404 («sin historial para …», «aún no hay …») se lee como `null`,
 * no como error: en el cajón de una publicación de Amazon no hay historia y eso
 * no es una falla.
 */
export async function pedir<T>(camino: string, params?: Params, opciones: { signal?: AbortSignal; sinRedirigir?: boolean; nulo404?: boolean } = {}): Promise<T> {
  const f = await fixture<T>(camino, params);
  if (f !== null) return f;
  const resp = await fetch(`/api/lab${camino}${consulta(params)}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
    signal: opciones.signal,
    cache: "no-store",
  });
  if (resp.status === 401) {
    if (!opciones.sinRedirigir) irALogin();
    throw new NoAutorizado();
  }
  if (resp.status === 404 && opciones.nulo404) return null as T;
  if (!resp.ok) throw new ErrorApi(resp.status, await detalle(resp));
  return (await resp.json()) as T;
}

/**
 * POST /api/lab/recalcular (api.py: 202 arranca, 409 ya corre, 403 desactivado).
 * `sinMl`: usa los crudos de Mercado Libre que ya hay (≈1.5 min) en vez de volver
 * a pedir ~8,500 consultas a la API. Devuelve null si arrancó, o el motivo.
 */
export async function recalcular(sinMl: boolean): Promise<string | null> {
  if (USA_FIXTURES) return null;
  const resp = await fetch("/api/lab/recalcular", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ sin_ml: sinMl }),
  });
  if (resp.status === 202) return null;
  if (resp.status === 401) { irALogin(); return "La sesión expiró."; }
  if (resp.status === 409) return "Ya hay un recálculo en curso.";
  return detalle(resp);
}

async function detalle(resp: Response): Promise<string> {
  try {
    const j = await resp.json();
    // api.py: 500 → {"detail", "ref"}; la referencia cruza con el log del servidor.
    if (typeof j?.detail === "string") return typeof j?.ref === "string" ? `${j.detail} (ref ${j.ref})` : j.detail;
    if (typeof j?.detalle === "string") return j.detalle;
  } catch { /* cuerpo no JSON */ }
  if (resp.status === 503) return "El laboratorio no tiene llave configurada: falla cerrado (503).";
  return `El laboratorio respondió ${resp.status}.`;
}

/** POST /api/lab/sesion con la llave compartida. Devuelve null si entró, o el motivo. */
export async function iniciarSesion(llave: string): Promise<string | null> {
  const f = await fixture<{ ok: boolean }>("/sesion", {}, "POST", { llave });
  if (f !== null) return f.ok ? null : "Llave incorrecta.";
  const resp = await fetch("/api/lab/sesion", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ llave }),
  });
  if (resp.ok) return null;
  if (resp.status === 401 || resp.status === 403) return "Llave incorrecta.";
  if (resp.status === 429) return "Demasiados intentos. Espera un momento.";
  return detalle(resp);
}

/**
 * GET /api/lab/sesion: {modo: "llave"|"abierto"|"cerrado", requiere_llave, autenticado}.
 * Lo usa /login para no pedir llave cuando ya hay sesión o el laboratorio corre abierto (local).
 */
export async function estadoSesion(): Promise<{ modo?: string; requiere_llave?: boolean; autenticado?: boolean } | null> {
  const f = await fixture<{ autenticado?: boolean }>("/sesion");
  if (f !== null) return f;
  try {
    const r = await fetch("/api/lab/sesion", { credentials: "include", headers: { Accept: "application/json" }, cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

/** {"salir": true} borra la cookie `lab_sesion` (api.py). */
export async function cerrarSesion(): Promise<void> {
  const f = await fixture<unknown>("/sesion", {}, "POST", { salir: true });
  if (f === null) {
    try {
      await fetch("/api/lab/sesion", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ salir: true }),
      });
    } catch { /* sin red: igual se va a /login */ }
  }
  window.location.assign(ruta("/login"));
}

/** Normaliza cualquier sobre de lista a `Pagina<T>`. */
export function aPagina<T>(r: unknown): Pagina<T> {
  if (Array.isArray(r)) return { total: r.length, page: 1, per_page: r.length, filas: r as T[] };
  const o = (r ?? {}) as Record<string, unknown>;
  const filas = (o.filas ?? o.items ?? o.data ?? []) as T[];
  return {
    generado_at: (o.generado_at as string) ?? null,
    total: Number(o.total ?? o.count ?? filas.length),
    page: Number(o.page ?? o.pagina ?? 1),
    per_page: Number(o.per_page ?? o.por_pagina ?? filas.length),
    filas,
    conteos: o.conteos as Record<string, number> | undefined,
    facetas: o.facetas as Record<string, Record<string, number>> | undefined,
    parametros: o.parametros as Record<string, unknown> | undefined,
    sin_datos: o.sin_datos === true,
  };
}

/**
 * Trae TODAS las filas de un listado paginado (para Precios: 1,744 publicaciones
 * FULL el 28-sep, y los KPIs de arriba deben sumar el universo, no la página).
 * Pide páginas grandes; si el servidor topa `per_page`, sigue pidiendo de a 4.
 */
export async function traerTodo<T>(camino: string, params: Params = {}, signal?: AbortSignal): Promise<Pagina<T>> {
  const porPagina = 1000;
  const primera = aPagina<T>(await pedir(camino, { ...params, page: 1, per_page: porPagina }, { signal }));
  const filas = [...primera.filas];
  const tam = primera.filas.length || porPagina;
  const paginas = Math.ceil(primera.total / tam);
  for (let p = 2; p <= paginas; p += 4) {
    const lote = await Promise.all(
      Array.from({ length: Math.min(4, paginas - p + 1) }, (_, k) =>
        pedir(camino, { ...params, page: p + k, per_page: porPagina }, { signal }).then((r) => aPagina<T>(r).filas)),
    );
    for (const l of lote) filas.push(...l);
  }
  return { ...primera, filas, total: Math.max(primera.total, filas.length), page: 1, per_page: filas.length };
}

/**
 * CSV de precios (DISENO §7). api.py acepta los MISMOS filtros que /precios
 * (cuenta, estado, razon, confianza, q, orden): lo que se ve es lo que se baja.
 * En fixtures se arma aquí.
 */
export async function descargarCsvPrecios(params?: Params): Promise<void> {
  if (USA_FIXTURES) {
    const m = await import("./datos-prueba");
    const csv = await m.csvPrecios();
    if (csv === null) return;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = "precios_recomendados.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return;
  }
  // Mismo origen: la cookie viaja sola y el navegador guarda el archivo.
  const a = document.createElement("a");
  a.href = `/api/lab/exportar/precios.csv${consulta(params)}`;
  a.download = "";
  a.click();
}
