/**
 * Imitación de `/api/lab/*` con los JSON de `fixtures/` (datos INVENTADOS por
 * `scripts/generar-fixtures.mjs`, con la forma exacta de DISENO §6).
 *
 * Sólo se carga en `next dev` con NEXT_PUBLIC_LAB_FIXTURES=1; en cualquier otro
 * build next.config.mjs lo cambia por `datos-prueba-vacio.ts`. Filtra, ordena y
 * pagina igual que se espera de api.py, para que la paginación de la tabla se
 * pruebe de verdad y no sólo con una página.
 */
import type { Params } from "./api";
import type { Publicacion, Recomendacion } from "./tipos";

type Fila = Record<string, unknown>;

async function cargar(nombre: string): Promise<unknown> {
  switch (nombre) {
    case "estado": return (await import("../fixtures/estado.json")).default;
    case "publicaciones": return (await import("../fixtures/publicaciones.json")).default;
    case "precios": return (await import("../fixtures/precios.json")).default;
    case "curvas": return (await import("../fixtures/curvas.json")).default;
    case "historial": return (await import("../fixtures/historial.json")).default;
    case "packing": return (await import("../fixtures/packing100.json")).default;
    case "contenedores": return (await import("../fixtures/contenedores.json")).default;
    case "metricas": return (await import("../fixtures/metricas.json")).default;
    case "parametros": return (await import("../fixtures/parametros.json")).default;
    default: return null;
  }
}

function valor(f: Fila, campo: string): unknown {
  if (campo.includes(".")) return campo.split(".").reduce<unknown>((o, k) => (o as Fila | null)?.[k], f);
  return f[campo];
}

function ordenar<T extends Fila>(filas: T[], orden?: unknown): T[] {
  if (!orden) return filas;
  const s = String(orden);
  const desc = s.startsWith("-");
  const campo = desc ? s.slice(1) : s;
  return [...filas].sort((a, b) => {
    const x = valor(a, campo); const y = valor(b, campo);
    // Los nulos al final siempre: «sin dato» no es el más chico ni el más grande.
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "es");
    return desc ? -c : c;
  });
}

function paginar<T>(filas: T[], params: Params, generado_at: unknown, extra: Record<string, unknown> = {}) {
  const page = Math.max(1, Number(params.page ?? 1));
  const per = Math.max(1, Number(params.per_page ?? 50));
  return { generado_at, total: filas.length, page, per_page: per, filas: filas.slice((page - 1) * per, page * per), ...extra };
}

const texto = (q: unknown) => String(q ?? "").trim().toLowerCase();

export async function responder(camino: string, params: Params, metodo: string, cuerpo?: unknown): Promise<unknown | null> {
  if (camino === "/sesion" && metodo === "POST") {
    const c = (cuerpo ?? {}) as { llave?: string; salir?: boolean };
    if (c.salir) return { ok: true, salir: true };
    return { ok: (c.llave ?? "").trim().length > 0 };
  }
  if (camino === "/sesion") return { modo: "llave", requiere_llave: true, autenticado: false };
  if (camino === "/estado" || camino === "/metricas" || camino === "/parametros" || camino === "/contenedores") {
    return cargar(camino.slice(1));
  }
  if (camino === "/packing") {
    const d = (await cargar("packing")) as Record<string, unknown>;
    return { ...d, saltados: [
      { sku: "TEC-0199", motivo: "padre_con_muchas_variantes" }, { sku: "ORG-0934-NEG", motivo: "sin_container_numbers" },
      { sku: "EST-0091-CAF", motivo: "sin_archivo" }, { sku: "MAN-0490-DOR", motivo: "sin_renglon" }, { sku: "CALZ-0119-BLN-36", motivo: "ambiguo" },
    ] };
  }
  if (camino.startsWith("/curva/")) {
    const id = decodeURIComponent(camino.slice("/curva/".length));
    const todas = (await cargar("curvas")) as Record<string, unknown>;
    return todas[id] ?? { puntos: [], marcadores: {} };
  }
  if (camino.startsWith("/historial/")) {
    const id = decodeURIComponent(camino.slice("/historial/".length));
    const todo = (await cargar("historial")) as Record<string, unknown>;
    return todo[id] ?? { serie: [] };
  }
  if (camino === "/publicaciones") {
    const d = (await cargar("publicaciones")) as { generado_at: string; filas: Publicacion[] };
    const conteos: Record<string, number> = {};
    for (const f of d.filas) {
      conteos[f.canal] = (conteos[f.canal] ?? 0) + 1;
      conteos[`${f.canal}:${f.cuenta}`] = (conteos[`${f.canal}:${f.cuenta}`] ?? 0) + 1;
    }
    const fuentes = params.fuente_costo ? String(params.fuente_costo).split(",") : null;
    const q = texto(params.q);
    let filas = d.filas.filter((f) =>
      (!params.canal || f.canal === params.canal)
      && (!params.cuenta || f.cuenta === params.cuenta)
      && (!params.estado || f.estado === params.estado)
      && (params.full === undefined || params.full === "" || String(f.es_full) === String(params.full === "1" || params.full === true || params.full === "true"))
      && (!fuentes || fuentes.includes(f.costo?.fuente ?? "sin_costo"))
      && (!q || (f.sku ?? "").toLowerCase().includes(q) || (f.titulo ?? "").toLowerCase().includes(q) || f.listing_id.toLowerCase().includes(q)));
    filas = ordenar(filas as unknown as Fila[], params.orden) as unknown as Publicacion[];
    return paginar(filas, params, d.generado_at, { conteos });
  }
  if (camino === "/precios") {
    const d = (await cargar("precios")) as { generado_at: string; parametros: unknown; filas: Recomendacion[] };
    const q = texto(params.q);
    let filas = d.filas.filter((f) =>
      (!params.cuenta || f.cuenta === params.cuenta)
      && (!params.razon || f.razones.includes(String(params.razon)))
      && (!params.confianza || f.elasticidad.confianza === params.confianza)
      && (!q || (f.sku ?? "").toLowerCase().includes(q) || (f.titulo ?? "").toLowerCase().includes(q) || f.listing_id.toLowerCase().includes(q)));
    filas = ordenar(filas as unknown as Fila[], params.orden) as unknown as Recomendacion[];
    return paginar(filas, params, d.generado_at, { parametros: d.parametros });
  }
  return null;
}

export async function csvPrecios(): Promise<string> {
  const d = (await cargar("precios")) as { filas: Recomendacion[] };
  const cab = ["sku", "cuenta", "listing_id", "titulo", "precio_actual", "precio_recomendado", "cambio_pct", "precio_piso", "confianza", "autorizacion"];
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const filas = d.filas.map((f) => [f.sku, f.cuenta, f.listing_id, f.titulo, f.precio_actual, f.precio_recomendado, f.cambio_pct,
    f.precio_piso, f.elasticidad.confianza, f.autorizacion].map(esc).join(","));
  return [cab.join(","), ...filas].join("\n");
}
