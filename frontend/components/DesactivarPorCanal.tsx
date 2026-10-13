"use client";

/**
 * DesactivarPorCanal — «Publicaciones por canal · desactivar en lote» (Publicador).
 *
 * Brandon, 13-oct-2026: «tenemos problemas de stock directamente con almacén […]
 * necesito por el momento apagar todo Temu», y que el Publicador deje escoger un
 * canal, ver sus SKUs publicados, seleccionar un lote o TODOS y desactivarlos.
 *
 * El canal se escoge con las MISMAS pestañas de Omnicanal (`MarketplaceTabs`). Hoy
 * sólo Temu y TikTok tienen una llamada de «sacar de la venta» probada en vivo; los
 * demás canales salen deshabilitados y lo dicen.
 *
 * Cuatro cosas que esta pantalla no puede dejar a la imaginación:
 *   1. INACTIVA, NO BORRA. La publicación sigue en el canal, apagada, igual que si
 *      se apagara a mano en el Seller Center.
 *   2. La lista es la del ÚLTIMO CENSO (pasa cada hora) y lo dice. Al desactivar,
 *      el backend lee cada publicación EN VIVO: la que ya estaba apagada no se toca.
 *   3. Lo que se apaga aquí se queda apagado: ni el seguro de stock 0 lo regresa.
 *      Volver a prenderlo es otra decisión.
 *   4. Va por tandas de 5 y enseña el avance: cerrar la ventana a media corrida deja
 *      SIN apagar lo que faltaba (lo ya apagado, apagado se queda).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, PowerOff, RotateCw, Search, ShieldAlert, X } from "lucide-react";

import MarketplaceTabs from "@/components/MarketplaceTabs";
import {
  ApiError, desactivarPublicacionesLote, listarPublicacionesLote, mensajeDeError,
  type FilaResultadoLote, type ListaLote, type PublicacionLote, type ResultadoLote,
} from "@/lib/api";
import type { CanalInfo } from "@/lib/types";

/** Canales donde hoy se puede sacar de la venta por lote. El backend manda. */
const CON_LOTE = ["temu", "tiktok"];
const TANDA = 5;
const POR_PAGINA = 100;
const ROJO = "#E11D48";

type Filtro = "publicadas" | "a_la_venta" | "apagadas" | "sin_publicar" | "todas";

const FILTROS: { id: Filtro; texto: string; ayuda: string }[] = [
  { id: "publicadas", texto: "Publicadas", ayuda: "Las que se pueden desactivar: a la venta y las que el canal regresa solo a la venta" },
  { id: "a_la_venta", texto: "A la venta", ayuda: "Las que se pueden comprar en este momento" },
  { id: "apagadas", texto: "Apagadas", ayuda: "Ya fuera de la venta" },
  { id: "sin_publicar", texto: "Sin publicar", ayuda: "Borradores, incompletas, en revisión o borradas" },
  { id: "todas", texto: "Todas", ayuda: "Todo lo que el canal tiene de nosotros" },
];

function entra(p: PublicacionLote, f: Filtro): boolean {
  if (f === "todas") return true;
  if (f === "publicadas") return p.desactivable;
  if (f === "a_la_venta") return p.grupo === "a_la_venta";
  if (f === "apagadas") return p.grupo === "apagada" || p.grupo === "apagada_canal";
  return !p.desactivable && p.grupo !== "apagada" && p.grupo !== "apagada_canal";
}

const CHIP_GRUPO: Record<string, string> = {
  a_la_venta: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  publicada: "bg-amber-50 text-amber-800 ring-amber-200",
  en_revision: "bg-sky-50 text-sky-700 ring-sky-200",
  apagada: "bg-slate-100 text-slate-600 ring-slate-200",
  apagada_canal: "bg-slate-100 text-slate-600 ring-slate-200",
  sin_publicar: "bg-slate-50 text-slate-400 ring-slate-200",
  borrada: "bg-slate-50 text-slate-400 ring-slate-200",
  desconocido: "bg-violet-50 text-violet-700 ring-violet-200",
};

const RESULTADO: Record<ResultadoLote, { texto: string; clase: string }> = {
  apagada: { texto: "Apagada", clase: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  ya_apagada: { texto: "Ya estaba apagada", clase: "bg-slate-100 text-slate-600 ring-slate-200" },
  no_aplica: { texto: "No aplica", clase: "bg-slate-50 text-slate-500 ring-slate-200" },
  sin_publicacion: { texto: "Sin publicación", clase: "bg-slate-50 text-slate-500 ring-slate-200" },
  sin_confirmar: { texto: "Sin confirmar", clase: "bg-amber-50 text-amber-800 ring-amber-200" },
  error: { texto: "Error", clase: "bg-rose-50 text-rose-700 ring-rose-200" },
};

const num = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : new Intl.NumberFormat("es-MX").format(n);

interface Corrida {
  total: number;
  hechos: number;
  corriendo: boolean;
  detenida: boolean;
  resumen: Partial<Record<ResultadoLote, number>>;
  error: string | null;
}

interface Props {
  /** Los canales del panel (los mismos de Omnicanal), ya cargados por la página. */
  canales: CanalInfo[];
  onCerrar: () => void;
}

export default function DesactivarPorCanal({ canales, onCerrar }: Props) {
  const [canal, setCanal] = useState<string | null>(null);
  const [lista, setLista] = useState<ListaLote | null>(null);
  const [cargando, setCargando] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>("publicadas");
  const [buscar, setBuscar] = useState("");
  const [pagina, setPagina] = useState(1);
  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  const [confirmando, setConfirmando] = useState(false);
  const [corrida, setCorrida] = useState<Corrida | null>(null);
  const [resultados, setResultados] = useState<Record<string, FilaResultadoLote>>({});
  const detener = useRef(false);
  const corriendo = corrida?.corriendo ?? false;

  // Las pestañas de Omnicanal, sin «General» (no es un canal de venta) y con los
  // canales sin desactivación por lote deshabilitados.
  const pestanas = useMemo(
    () => canales
      .filter((c) => c.id !== "general" && c.habilitado)
      .map((c) => ({ ...c, habilitado: CON_LOTE.includes(c.id) })),
    [canales],
  );
  const tema = canales.find((c) => c.id === canal);

  const cargar = useCallback(async (c: string) => {
    setCargando(true);
    setFalla(null);
    try {
      setLista(await listarPublicacionesLote(c));
    } catch (e) {
      setLista(null);
      setFalla(mensajeDeError(e, "No se pudieron leer las publicaciones del canal."));
    } finally {
      setCargando(false);
    }
  }, []);

  function escoger(c: string) {
    if (corriendo || c === canal) return;
    setCanal(c);
    setLista(null);
    setSeleccion(new Set());
    setResultados({});
    setCorrida(null);
    setFiltro("publicadas");
    setBuscar("");
    setPagina(1);
    void cargar(c);
  }

  // Escape cierra, salvo a media corrida: cerrar ahí deja el lote a medias.
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || corriendo) return;
      if (confirmando) setConfirmando(false);
      else onCerrar();
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [corriendo, confirmando, onCerrar]);

  const visibles = useMemo(() => {
    const q = buscar.trim().toLowerCase();
    return (lista?.items ?? []).filter((p) =>
      entra(p, filtro)
      && (!q || p.sku.toLowerCase().includes(q) || (p.titulo ?? "").toLowerCase().includes(q)
          || p.listing_id.includes(q)));
  }, [lista, filtro, buscar]);

  const cuentas = useMemo(() => {
    const items = lista?.items ?? [];
    return Object.fromEntries(FILTROS.map((f) => [f.id, items.filter((p) => entra(p, f.id)).length])) as Record<Filtro, number>;
  }, [lista]);

  // Seleccionables de la lista FILTRADA (todas sus páginas, no sólo la que se ve).
  const seleccionables = useMemo(
    () => [...new Set(visibles.filter((p) => p.desactivable).map((p) => p.sku))],
    [visibles],
  );
  const todasMarcadas = seleccionables.length > 0 && seleccionables.every((s) => seleccion.has(s));
  const paginas = Math.max(1, Math.ceil(visibles.length / POR_PAGINA));
  const enPagina = visibles.slice((pagina - 1) * POR_PAGINA, pagina * POR_PAGINA);

  function alternar(sku: string) {
    if (corriendo) return;
    setSeleccion((s) => {
      const n = new Set(s);
      if (n.has(sku)) n.delete(sku); else n.add(sku);
      return n;
    });
  }

  function alternarTodas() {
    if (corriendo) return;
    setSeleccion((s) => {
      const n = new Set(s);
      if (todasMarcadas) seleccionables.forEach((x) => n.delete(x));
      else seleccionables.forEach((x) => n.add(x));
      return n;
    });
  }

  async function ejecutar() {
    if (!canal || seleccion.size === 0) return;
    const skus = [...seleccion];
    setConfirmando(false);
    detener.current = false;
    let hechos = 0;
    const resumen: Partial<Record<ResultadoLote, number>> = {};
    let error: string | null = null;
    setResultados({});
    setCorrida({ total: skus.length, hechos: 0, corriendo: true, detenida: false, resumen: {}, error: null });
    for (let i = 0; i < skus.length; i += TANDA) {
      if (detener.current) break;
      const tanda = skus.slice(i, i + TANDA);
      try {
        const r = await desactivarPublicacionesLote(canal, tanda);
        const nuevos: Record<string, FilaResultadoLote> = {};
        for (const f of r.resultados) {
          nuevos[f.listing_id ?? `sku:${f.sku}`] = f;
          resumen[f.resultado] = (resumen[f.resultado] ?? 0) + 1;
        }
        setResultados((prev) => ({ ...prev, ...nuevos }));
      } catch (e) {
        // Sin permiso o sin sesión no tiene caso seguir: las demás tandas darían lo mismo.
        error = mensajeDeError(e, "El backend no contestó a esta tanda.");
        const fatal = e instanceof ApiError && (e.status === 401 || e.status === 403);
        const nuevos: Record<string, FilaResultadoLote> = {};
        for (const sku of tanda) {
          nuevos[`sku:${sku}`] = { sku, listing_id: null, antes: null, despues: null, resultado: "error", detalle: error };
          resumen.error = (resumen.error ?? 0) + 1;
        }
        setResultados((prev) => ({ ...prev, ...nuevos }));
        if (fatal) { hechos += tanda.length; break; }
      }
      hechos += tanda.length;
      setCorrida({ total: skus.length, hechos, corriendo: true, detenida: false, resumen: { ...resumen }, error });
    }
    setCorrida({
      total: skus.length, hechos: Math.min(hechos, skus.length), corriendo: false,
      detenida: detener.current && hechos < skus.length, resumen: { ...resumen }, error,
    });
    setSeleccion(new Set());
    void cargar(canal);
  }

  const resultadoDe = (p: PublicacionLote) => resultados[p.listing_id] ?? resultados[`sku:${p.sku}`];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 backdrop-blur-sm" role="dialog" aria-modal="true"
         aria-label="Publicaciones por canal: desactivar en lote">
      <div className="mx-auto my-6 max-w-[1280px] rounded-3xl bg-slate-50 shadow-2xl">
        {/* Encabezado */}
        <div className="flex items-start justify-between gap-4 rounded-t-3xl border-b border-slate-200 bg-white px-6 py-5">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-rose-600">
              <PowerOff size={14} /> Publicador · por canal
            </div>
            <h2 className="mt-1 text-2xl font-extrabold tracking-tight text-slate-900">Desactivar publicaciones en lote</h2>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              Escoge un canal, selecciona un lote o todas, y sácalas de la venta. <strong>Se inactivan, no se
              borran</strong>: quedan en el canal, apagadas, igual que si las apagaras a mano en su Seller Center.
              Lo que apagues aquí se queda apagado: no lo regresa el seguro de stock 0.
            </p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            disabled={corriendo}
            title={corriendo ? "Espera a que termine el lote (o detenlo)" : "Cerrar"}
            className="rounded-lg border border-slate-200 bg-white p-2 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-6 pb-8 pt-5">
          {/* El canal: las mismas pestañas de Omnicanal */}
          <MarketplaceTabs
            canales={pestanas}
            activo={canal ?? ""}
            onSelect={escoger}
            motivoDeshabilitado="Todavía no se puede desactivar por lote en este canal desde el panel"
          />

          {!canal && (
            <div className="mt-6 rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-500">
              Escoge un canal para ver sus publicaciones. Hoy se puede desactivar por lote en <strong>Temu</strong> y{" "}
              <strong>TikTok</strong>.
            </div>
          )}

          {canal && falla && (
            <div className="mt-6 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {falla}
            </div>
          )}

          {canal && cargando && !lista && (
            <div className="mt-6 flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" /> Leyendo las publicaciones de {tema?.label ?? canal}…
            </div>
          )}

          {canal && lista && (
            <>
              {/* Filtros de estado + buscador */}
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  {FILTROS.map((f) => {
                    const activo = filtro === f.id;
                    return (
                      <button
                        key={f.id}
                        type="button"
                        onClick={() => { setFiltro(f.id); setPagina(1); }}
                        title={f.ayuda}
                        aria-pressed={activo}
                        className={[
                          "rounded-full border px-3 py-1.5 text-sm font-semibold transition-colors",
                          activo ? "border-slate-900 bg-slate-900 text-white" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                        ].join(" ")}
                      >
                        {f.texto} <span className="ml-1 tabular-nums opacity-70">{num(cuentas[f.id])}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      value={buscar}
                      onChange={(e) => { setBuscar(e.target.value); setPagina(1); }}
                      placeholder="SKU, nombre o id…"
                      className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => void cargar(canal)}
                    disabled={corriendo}
                    title="Volver a leer la lista"
                    className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 hover:bg-slate-50 disabled:opacity-40"
                  >
                    <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
                  </button>
                </div>
              </div>
              <p className="mt-2 text-xs text-slate-500">
                Lista del último censo de {lista.nombre}{lista.censo ? ` (${lista.censo}, hora de México)` : ""}; el censo
                pasa cada hora. Al desactivar se lee cada publicación en vivo: la que ya esté apagada no se toca.
              </p>

              {/* Selección + acción */}
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <label className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-slate-700">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-slate-300 accent-rose-600"
                    checked={todasMarcadas}
                    disabled={corriendo || seleccionables.length === 0}
                    onChange={alternarTodas}
                  />
                  {seleccionables.length === 0
                    ? "En esta lista no hay nada que se pueda desactivar"
                    : `Seleccionar las ${num(seleccionables.length)} de esta lista`}
                </label>
                <div className="flex items-center gap-3">
                  <span className="text-sm tabular-nums text-slate-600">
                    <strong className="text-slate-900">{num(seleccion.size)}</strong> seleccionadas
                  </span>
                  {seleccion.size > 0 && !corriendo && (
                    <button type="button" onClick={() => setSeleccion(new Set())}
                            className="text-sm font-medium text-slate-500 underline-offset-2 hover:underline">
                      Quitar selección
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setConfirmando(true)}
                    disabled={corriendo || seleccion.size === 0}
                    className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-sm transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                    style={{ backgroundColor: ROJO }}
                  >
                    <PowerOff size={16} /> Desactivar {seleccion.size > 0 ? num(seleccion.size) : ""}
                  </button>
                </div>
              </div>

              {/* Avance y resultado de la corrida */}
              {corrida && (
                <div className="mt-3 rounded-2xl border border-slate-200 bg-white px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                    <div className="flex items-center gap-2 font-semibold text-slate-800">
                      {corrida.corriendo
                        ? <Loader2 size={16} className="animate-spin text-rose-600" />
                        : <Check size={16} className="text-emerald-600" />}
                      {corrida.corriendo ? "Desactivando…" : corrida.detenida ? "Detenido" : "Terminado"}
                      <span className="font-normal tabular-nums text-slate-500">
                        {num(corrida.hechos)} de {num(corrida.total)} SKUs
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {(Object.keys(RESULTADO) as ResultadoLote[]).filter((k) => corrida.resumen[k]).map((k) => (
                        <span key={k} className={`rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${RESULTADO[k].clase}`}>
                          {RESULTADO[k].texto} {num(corrida.resumen[k])}
                        </span>
                      ))}
                      {corrida.corriendo && (
                        <button type="button" onClick={() => { detener.current = true; }}
                                className="ml-2 rounded-lg border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                          Detener al terminar esta tanda
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full transition-all"
                         style={{ width: `${corrida.total ? Math.round((corrida.hechos / corrida.total) * 100) : 0}%`, backgroundColor: ROJO }} />
                  </div>
                  {corrida.corriendo && (
                    <p className="mt-2 text-xs text-slate-500">
                      No cierres esta ventana: va por tandas de {TANDA} y cada publicación se relee para confirmar
                      (unos 10 segundos por tanda). Lo que falte al cerrar se queda sin apagar.
                    </p>
                  )}
                  {corrida.error && (
                    <p className="mt-2 flex items-start gap-1.5 text-xs text-rose-700">
                      <AlertTriangle size={14} className="mt-0.5 shrink-0" /> {corrida.error}
                    </p>
                  )}
                </div>
              )}

              {/* La lista */}
              <div className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-white">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                        <th className="w-10 px-3 py-2"></th>
                        <th className="px-3 py-2">SKU</th>
                        <th className="px-3 py-2">Producto</th>
                        <th className="px-3 py-2">Estado en {lista.nombre}</th>
                        <th className="px-3 py-2 text-right" title="Lo que el canal ofrece, según el censo">Ofrece</th>
                        <th className="px-3 py-2 text-right" title="Stock en WooCommerce (la foto de stock_watch)">Woo</th>
                        <th className="px-3 py-2">Resultado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {enPagina.length === 0 && (
                        <tr><td colSpan={7} className="px-3 py-8 text-center text-sm text-slate-400">
                          Nada en este filtro.
                        </td></tr>
                      )}
                      {enPagina.map((p) => {
                        const marcada = seleccion.has(p.sku);
                        const r = resultadoDe(p);
                        return (
                          <tr key={p.listing_id}
                              className={["border-b border-slate-100 last:border-0", marcada ? "bg-rose-50/40" : ""].join(" ")}>
                            <td className="px-3 py-2">
                              <input
                                type="checkbox"
                                className="h-4 w-4 rounded border-slate-300 accent-rose-600 disabled:opacity-30"
                                checked={marcada}
                                disabled={!p.desactivable || corriendo}
                                title={p.desactivable ? "Seleccionar" : "No hay nada que apagar en esta publicación"}
                                onChange={() => alternar(p.sku)}
                                aria-label={`Seleccionar ${p.sku}`}
                              />
                            </td>
                            <td className="whitespace-nowrap px-3 py-2 font-mono text-xs font-semibold text-slate-800">
                              {p.url
                                ? <a href={p.url} target="_blank" rel="noreferrer" className="hover:underline">{p.sku}</a>
                                : p.sku}
                              <div className="font-sans text-[10px] font-normal text-slate-400">{p.listing_id}</div>
                            </td>
                            <td className="max-w-[360px] truncate px-3 py-2 text-slate-600" title={p.titulo ?? ""}>
                              {p.titulo ?? <span className="text-slate-400">sin nombre en el catálogo</span>}
                            </td>
                            <td className="px-3 py-2">
                              <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${CHIP_GRUPO[p.grupo] ?? CHIP_GRUPO.desconocido}`}
                                    title={`Estado del canal: ${p.estado}`}>
                                {p.etiqueta}
                              </span>
                              {p.apagada_por && (
                                <div className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-500">
                                  <ShieldAlert size={11} />
                                  {p.apagada_por.quien === "seguro"
                                    ? "la apagó el seguro de stock 0"
                                    : `apagada desde el panel${p.apagada_por.cuando ? ` · ${p.apagada_por.cuando}` : ""}`}
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-700">{num(p.stock_canal)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-slate-700">{num(p.stock_woo)}</td>
                            <td className="px-3 py-2">
                              {r && (
                                <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ${RESULTADO[r.resultado].clase}`}
                                      title={r.detalle ?? (r.antes && r.despues ? `${r.antes} → ${r.despues}` : "")}>
                                  {RESULTADO[r.resultado].texto}
                                </span>
                              )}
                              {r?.detalle && r.resultado !== "ya_apagada" && (
                                <div className="mt-0.5 max-w-[280px] truncate text-[11px] text-slate-500" title={r.detalle}>{r.detalle}</div>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {paginas > 1 && (
                  <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2 text-sm text-slate-600">
                    <span className="tabular-nums">
                      {num((pagina - 1) * POR_PAGINA + 1)}–{num(Math.min(pagina * POR_PAGINA, visibles.length))} de {num(visibles.length)}
                    </span>
                    <div className="flex items-center gap-2">
                      <button type="button" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}
                              className="rounded-lg border border-slate-200 bg-white px-3 py-1 font-medium hover:bg-slate-50 disabled:opacity-40">
                        Anterior
                      </button>
                      <span className="tabular-nums">Página {pagina} de {paginas}</span>
                      <button type="button" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}
                              className="rounded-lg border border-slate-200 bg-white px-3 py-1 font-medium hover:bg-slate-50 disabled:opacity-40">
                        Siguiente
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Confirmación */}
      {confirmando && canal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/60 px-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
            <div className="flex items-center gap-2 text-lg font-extrabold text-slate-900">
              <PowerOff size={20} style={{ color: ROJO }} />
              Sacar de la venta {num(seleccion.size)} {seleccion.size === 1 ? "SKU" : "SKUs"} en {lista?.nombre ?? canal}
            </div>
            <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-slate-600">
              <li>Sus publicaciones quedan <strong>apagadas, no borradas</strong>.</li>
              <li>No se regresan solas: ni el seguro de stock 0 las va a prender aunque vuelva el stock.</li>
              <li>Para volver a venderlas habrá que prenderlas otra vez.</li>
              <li>Tarda cerca de {Math.max(1, Math.ceil((seleccion.size / TANDA) * 12 / 60))} min; no cierres esta ventana mientras avanza.</li>
            </ul>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setConfirmando(false)}
                      className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
                Cancelar
              </button>
              <button type="button" onClick={() => void ejecutar()}
                      className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white"
                      style={{ backgroundColor: ROJO }}>
                <PowerOff size={16} /> Sí, desactivar {num(seleccion.size)}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
