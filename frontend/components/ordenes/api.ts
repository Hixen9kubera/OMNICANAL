/**
 * Las llamadas de Inventario → ÓRDENES DE VENTA a `backend/routers/ordenes_venta.py`.
 *
 * Todo pasa por `fetchSesion` (token + un reintento tras el 401). Dos trampas
 * que ya costaron y aquí quedan resueltas en un solo sitio:
 *   · `fetchSesion` PISA `init.headers`: el `Content-Type` va en el TERCER
 *     argumento. Si no, el JSON viaja como texto y FastAPI contesta 422.
 *   · Con `FormData` NO se manda `Content-Type`: el navegador pone el boundary.
 *
 * Los errores salen como `ApiError` (lib/api.ts), que conserva el `detail` del
 * backend y el `status`. Un 409 tiene varios sentidos y TODOS traen su texto:
 * la orden cambió mientras tanto, no alcanzó el stock para apartar (al
 * confirmar, o al guardar el cambio de una confirmada), la venta ya tiene
 * orden, o el módulo está en modo prueba. La pantalla relee y enseña el
 * `detail`; no adivina.
 */

import { API_BASE, ApiError, descargar, fetchSesion } from "@/lib/api";
import type {
  DatosOrden, EntregaLinea, EstadoModulo, FiltroEstado, ListaOrdenes, Orden, RespConciliar,
  RespMensajes, RespOrden, RespVentas, SkuOpcion, TipoArchivo,
} from "./tipos";

const RAIZ = `${API_BASE}/api/ordenes-venta`;
const JSON_H = { "Content-Type": "application/json" };

async function fallo(res: Response, ruta: string): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const j = (await res.json()) as { detail?: unknown };
    if (typeof j.detail === "string" && j.detail.trim()) detail = j.detail.trim();
    // Un 422 de pydantic trae `detail` como LISTA ({loc, msg}): se dice cuál campo
    // y por qué, en vez de dejar el «no se pudo» genérico.
    else if (Array.isArray(j.detail) && j.detail.length) {
      detail = (j.detail as { loc?: unknown[]; msg?: string }[]).slice(0, 3)
        .map((d) => `${(d.loc ?? []).filter((x) => x !== "body").join(".") || "dato"}: ${d.msg ?? "no válido"}`)
        .join(" · ");
    }
  } catch {
    // cuerpo vacío o no-JSON: queda el mensaje genérico
  }
  return new ApiError(res.status, ruta, detail);
}

async function leer<T>(ruta: string, signal?: AbortSignal): Promise<T> {
  const res = await fetchSesion(`${RAIZ}${ruta}`, { signal, cache: "no-store" });
  if (!res.ok) throw await fallo(res, ruta);
  return res.json() as Promise<T>;
}

async function mandar<T>(metodo: "POST" | "PUT" | "DELETE", ruta: string,
                         cuerpo?: unknown): Promise<T> {
  const res = await fetchSesion(
    `${RAIZ}${ruta}`,
    cuerpo === undefined ? { method: metodo } : { method: metodo, body: JSON.stringify(cuerpo) },
    cuerpo === undefined ? {} : JSON_H,
  );
  if (!res.ok) throw await fallo(res, ruta);
  return res.json() as Promise<T>;
}

/** ¿Es un 409? (la orden cambió, no alcanzó, ya existe…). El porqué viene en el `detail`. */
export function esConflicto(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409;
}

// ── Módulo ────────────────────────────────────────────────────────────────────

export function leerEstado(signal?: AbortSignal): Promise<EstadoModulo> {
  return leer<EstadoModulo>("/estado", signal);
}

export interface FiltrosLista {
  estado?: FiltroEstado;
  q?: string;
  canal?: string;
  pagina?: number;
  por_pagina?: number;
}

export function listarOrdenes(f: FiltrosLista = {}, signal?: AbortSignal): Promise<ListaOrdenes> {
  const p = new URLSearchParams();
  if (f.estado && f.estado !== "todas") p.set("estado", f.estado);
  if (f.q?.trim()) p.set("q", f.q.trim());
  if (f.canal) p.set("canal", f.canal);
  if (f.pagina && f.pagina > 1) p.set("pagina", String(f.pagina));
  if (f.por_pagina) p.set("por_pagina", String(f.por_pagina));
  const qs = p.toString();
  return leer<ListaOrdenes>(qs ? `?${qs}` : "", signal);
}

/** Revisa AHORA si el canal canceló alguna venta que tiene orden propia. */
export function conciliar(): Promise<RespConciliar> {
  return mandar<RespConciliar>("POST", "/conciliar");
}

// ── Orden ─────────────────────────────────────────────────────────────────────

/** `ref` es el id numérico o el folio («OV-00012»). */
export function leerOrden(ref: number | string, signal?: AbortSignal): Promise<Orden> {
  return leer<Orden>(`/${encodeURIComponent(String(ref))}`, signal);
}

/** `clave` la genera el navegador UNA vez por formulario: el doble clic no crea dos órdenes. */
export function crearOrden(datos: DatosOrden, clave: string): Promise<RespOrden> {
  return mandar<RespOrden>("POST", "", { ...datos, clave });
}

/**
 * Guarda encabezado y renglones: de un BORRADOR o de una CONFIRMADA (0071). En
 * la confirmada el servidor vuelve a apartar, todo o nada: si un renglón no
 * alcanza contesta 409 diciendo cuál, no guarda nada y NO mueve la `rev` (así
 * se distingue de «la orden cambió mientras tanto», que sí la mueve).
 */
export function guardarOrden(id: number, rev: number, datos: DatosOrden): Promise<RespOrden> {
  return mandar<RespOrden>("PUT", `/${id}`, { ...datos, rev });
}

/**
 * Confirma y APARTA el stock de cada renglón en su bodega. Es todo o nada: si
 * un renglón no alcanza, contesta 409 diciendo cuál y no aparta ninguno.
 */
export function confirmarOrden(id: number, rev: number): Promise<RespOrden> {
  return mandar<RespOrden>("POST", `/${id}/confirmar`, { rev });
}

/**
 * DELIVERED: almacén la entregó a la paquetería. Sin `lineas` salen todos los
 * renglones completos; con ellas, lo que salió de cada uno (entrega parcial).
 */
export function entregarOrden(id: number, rev: number, lineas?: EntregaLinea[]): Promise<RespOrden> {
  return mandar<RespOrden>("POST", `/${id}/entregar`, lineas ? { rev, lineas } : { rev });
}

export function cancelarOrden(id: number, rev: number, motivo: string): Promise<RespOrden> {
  return mandar<RespOrden>("POST", `/${id}/cancelar`, { rev, motivo });
}

/**
 * La respuesta de Bodega cuando el canal canceló con el paquete en camino:
 * `salio = true` → DELIVERED but CANCELLED (se espera la devolución);
 * `salio = false` → cancelada, y el apartado se suelta.
 */
export function responderSalio(id: number, rev: number, salio: boolean): Promise<RespOrden> {
  return mandar<RespOrden>("POST", `/${id}/salio`, { rev, salio });
}

/** Admin: una orden ya CANCELADA cuyo paquete sí había salido. */
export function salioTarde(id: number, rev: number): Promise<RespOrden> {
  return mandar<RespOrden>("POST", `/${id}/salio-tarde`, { rev });
}

/** Admin: borra la orden (no se elimina: queda quién y por qué). Motivo de 10 caracteres o más. */
export function borrarOrden(id: number, rev: number, motivo: string): Promise<RespOrden> {
  // El motivo es texto libre: va en el CUERPO, como el de cancelar. En la
  // dirección quedaba copiado en el registro de accesos del servidor.
  return mandar<RespOrden>("DELETE", `/${id}`, { rev, motivo });
}

// ── Chat ──────────────────────────────────────────────────────────────────────

/**
 * Con `esperar` (segundos, máx. 25) el backend sostiene la petición hasta que
 * haya un mensaje nuevo o la orden cambie. Sin él contesta al instante.
 */
export function leerMensajes(id: number, desdeId: number, esperar: number,
                             signal?: AbortSignal): Promise<RespMensajes> {
  const p = new URLSearchParams({ desde_id: String(desdeId) });
  if (esperar > 0) p.set("esperar", String(esperar));
  return leer<RespMensajes>(`/${id}/mensajes?${p.toString()}`, signal);
}

/**
 * `clave` (uuid, una por mensaje escrito) hace el envío idempotente: si la
 * respuesta se pierde y la persona vuelve a mandar, no queda duplicado.
 */
export function enviarMensaje(id: number, cuerpo: string, clave?: string): Promise<RespMensajes> {
  return mandar<RespMensajes>("POST", `/${id}/mensajes`, clave ? { cuerpo, clave } : { cuerpo });
}

// ── PDF ───────────────────────────────────────────────────────────────────────

/**
 * Adjunta un PDF. `tipo` es obligatorio: comprobante, factura o envío a FULL.
 * Las guías con la dirección del comprador NO se guardan aquí.
 */
export async function subirArchivo(id: number, archivo: File, tipo: TipoArchivo): Promise<RespOrden> {
  const fd = new FormData();
  fd.append("pdf", archivo);
  fd.append("tipo", tipo);
  // Sin Content-Type: el navegador pone el boundary del multipart.
  const ruta = `/${id}/archivos`;
  const res = await fetchSesion(`${RAIZ}${ruta}`, { method: "POST", body: fd });
  if (!res.ok) throw await fallo(res, ruta);
  return res.json() as Promise<RespOrden>;
}

/** Baja el PDF con la sesión puesta (un `<a href>` no manda el token). */
export function bajarArchivo(id: number, archivoId: number, nombre: string): Promise<Headers> {
  return descargar(`${RAIZ}/${id}/archivos/${archivoId}`, nombre);
}

export function borrarArchivo(id: number, archivoId: number): Promise<RespOrden> {
  return mandar<RespOrden>("DELETE", `/${id}/archivos/${archivoId}`);
}

// ── Buscadores ────────────────────────────────────────────────────────────────

export function buscarSkus(q: string, signal?: AbortSignal): Promise<{ opciones: SkuOpcion[] }> {
  return leer<{ opciones: SkuOpcion[] }>(`/skus?q=${encodeURIComponent(q)}`, signal);
}

/** Busca UNA venta de marketplace por su id, para prellenar el borrador con su precio. */
export function buscarVenta(orden: string, canal?: string,
                            signal?: AbortSignal): Promise<RespVentas> {
  const p = new URLSearchParams({ orden });
  if (canal) p.set("canal", canal);
  return leer<RespVentas>(`/marketplace/venta?${p.toString()}`, signal);
}

/** Ventas DROP recientes que todavía no tienen orden propia. */
export function ventasPendientes(dias = 7, canal?: string,
                                 signal?: AbortSignal): Promise<RespVentas> {
  const p = new URLSearchParams({ dias: String(dias) });
  if (canal) p.set("canal", canal);
  return leer<RespVentas>(`/marketplace/pendientes?${p.toString()}`, signal);
}
