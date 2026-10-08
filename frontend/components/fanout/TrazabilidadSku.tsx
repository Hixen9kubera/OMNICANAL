"use client";

/**
 * TrazabilidadSku — panel lateral con la línea de trazabilidad de UN SKU: cada
 * cambio de stock en Woo (vino de Odoo o lo detectó stock_watch en Woo), cada
 * reparto del fan-out con lo que contestó cada canal, y lo que cada canal
 * reportó después. Una lectura del canal que no es lo último que el fan-out le
 * dejó se marca «cambió en el canal». Si el SKU vive en FULL, un carril aparte
 * trae cada aviso de la bodega de ML y la foto de su stock FULL (marcados en
 * cuadro, no en círculo: FULL no toca Woo). El carril de devoluciones trae las de ML y
 * las ventas que Temu canceló con la mercancía ya enviada (en rombo), y liga cada una que
 * llegó a nuestra bodega con la subida de Odoo que probablemente es su reingreso: una
 * coincidencia por fecha, no una liga dura. La arma `GET /api/fanout/historia` (solo lee).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { GrupoFull, Historia, ItemDevolucion, ItemTraza, LigaDevolucion } from "./tipos";
import { CAUSA_CLS, CELDA_CLS, CELDA_MATRIZ_PUNTEADO, CELDA_MATRIZ_SOLIDO, DEVOL_ESTADO_CLS, GRUPO_FULL, TONO_COLOR, conSigno } from "./tipos";

/** Qué parte de la línea se ve: todo, la bodega y Woo, los canales del reparto, la bodega FULL de ML o las devoluciones. */
export type Carril = "todo" | "bodega" | "canales" | "full" | "devoluciones";
const CARRILES: { id: Carril; texto: string }[] = [
  { id: "todo", texto: "Todo" },
  { id: "bodega", texto: "Bodega y Woo" },
  { id: "canales", texto: "Canales" },
  { id: "full", texto: "FULL" },
  { id: "devoluciones", texto: "Devoluciones" },
];
const ORDEN_FULL: GrupoFull[] = ["vendido", "ajuste", "cancelado", "llego", "retiro", "traslado", "cuarentena", "otro"];

function enCarril(it: ItemTraza, c: Carril): boolean {
  if (c === "todo") return true;
  if (c === "full") return it.tipo === "full";
  if (c === "bodega") return it.tipo === "woo";
  // Con las devoluciones van las subidas de Odoo que coinciden con su reingreso.
  if (c === "devoluciones") return it.tipo === "devolucion" || (it.tipo === "woo" && !!it.devoluciones?.length);
  return it.tipo === "reparto" || it.tipo === "canal";
}

const PERIODOS = [7, 14, 30, 60];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const CANAL_VENTA: Record<string, string> = {
  TEMU: "Temu", TIKTOK: "TikTok", ML: "Mercado Libre", MELI: "Mercado Libre", BEKURA: "ML Kubera",
  SANCORFASHION: "ML San Corpe", AMAZON: "Amazon", WALMART: "Walmart", WOO: "la tienda", WC: "la tienda",
};
const COLOR = { woo: "#4f46e5", coincide: "#0ea5e9", su_cuenta: "#f59e0b", sin_escritura: "#94a3b8", estado: "#64748b",
  devolucion: "#db2777" };
/** El tono del texto de la liga de una devolución con Odoo. */
const LIGA_CLS: Partial<Record<LigaDevolucion, string>> = {
  reingreso: "text-emerald-800", sin_reingreso: "text-amber-800", sin_fecha: "text-amber-800",
  fuera_periodo: "text-slate-600", full: "text-sky-800", revisar: "text-amber-800",
};

const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("es-MX"));
const pl = (k: number, uno: string, varios: string) => `${k.toLocaleString("es-MX")} ${k === 1 ? uno : varios}`;

function corto(dia: string): string {
  const [, m, d] = dia.split("-").map(Number);
  return `${d} ${MESES[m - 1]}`;
}

function ayerDe(hoy: string): string {
  const [a, m, d] = hoy.split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, d) - 86_400_000).toISOString().slice(0, 10);
}

function etiquetaDia(dia: string, hoy: string): string {
  if (dia === hoy) return `Hoy · ${corto(dia)}`;
  if (dia === ayerDe(hoy)) return `Ayer · ${corto(dia)}`;
  return corto(dia);
}

/** «a las 11:59» si fue el mismo día; si no, «el 30 sep 22:39». */
function cuando(hora: string, delDia: string): string {
  return hora.slice(0, 10) === delDia ? `a las ${hora.slice(11, 16)}` : `el ${corto(hora.slice(0, 10))} ${hora.slice(11, 16)}`;
}

function color(it: ItemTraza): string {
  if (it.tipo === "full") return GRUPO_FULL[it.grupo].color;
  if (it.tipo === "woo") return it.fallo ? TONO_COLOR.mal : COLOR.woo;
  if (it.tipo === "devolucion") return COLOR.devolucion;
  if (it.tipo === "reparto") return TONO_COLOR[it.tono];
  return COLOR[it.relacion];
}

function venta(motivo: string): { canal: string; pedido: string } {
  const m = /^venta\s+(\S+)\s*(.*)$/i.exec(motivo.trim());
  if (!m) return { canal: "", pedido: "" };
  return { canal: CANAL_VENTA[m[1].toUpperCase()] ?? m[1], pedido: m[2] };
}

function Devolucion({ it }: { it: ItemDevolucion }) {
  const liga = it.liga;
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-[13px] font-semibold leading-5 text-slate-900">
          Devolución en {it.nombre} · {pl(it.piezas, "pieza", "piezas")}
        </span>
        <span className={`rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${DEVOL_ESTADO_CLS[it.estado] ?? "bg-slate-100 text-slate-700"}`}>
          {it.estado_txt}
        </span>
        <span className="break-all font-mono text-[11px] text-slate-500">{it.id}</span>
      </div>
      <div className="text-xs leading-[18px] text-slate-600">
        {it.destino ? `Va ${it.destino_txt}` : "Destino: sin dato"}
        {it.motivo ? ` · ${it.motivo}` : ""}
        {it.venta_txt ? ` · ${it.venta_txt}` : ""}
      </div>
      {it.pasos.length > 0 && (
        <ol className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[11px] leading-4 text-slate-600" aria-label="Pasos de la devolución">
          {it.pasos.map((p, i) => (
            <li key={`${p.estado}-${p.hora}-${i}`} className="flex items-center gap-1" title={`Fecha según: ${p.de}`}>
              {i > 0 && <span aria-hidden className="text-slate-400">→</span>}
              <span className="font-semibold text-slate-700">{p.texto}</span>
              <span className="tabular-nums">{corto(p.hora.slice(0, 10))} {p.hora.slice(11, 16)}</span>
            </li>
          ))}
        </ol>
      )}
      {liga && (
        <div className={`mt-0.5 text-xs leading-[18px] ${LIGA_CLS[liga.k] ?? "text-slate-600"}`}>
          {liga.texto} {liga.nota && <span className="text-slate-500">{liga.nota}</span>}
        </div>
      )}
    </>
  );
}

function Renglon({ it, dia, onRastro, sku }: {
  it: ItemTraza; dia: string; sku: string; onRastro?: (sku: string, fin: string) => void;
}) {
  if (it.tipo === "woo") {
    const cambio = it.de != null && it.a != null ? `: ${n(it.de)} → ${n(it.a)}` : "";
    // Lo que hizo ODOO (la foto del delta), que no siempre es lo que se movió en Woo.
    const odoo = it.origen !== "odoo" || it.odoo_de == null || it.odoo_a == null ? ""
      : it.odoo_de === it.odoo_a ? ` Odoo no cambió (${n(it.odoo_a)}): Woo vuelve a lo que ya tenía Odoo.`
      : ` Odoo ${n(it.odoo_de)} → ${n(it.odoo_a)}.`;
    return (
      <>
        <div className="text-[13px] font-semibold leading-5 text-slate-900">
          {it.origen === "odoo" ? `Odoo movió Woo${cambio}` : `Woo cambió${cambio}`}
        </div>
        <div className="text-xs leading-[18px] text-slate-600">
          {it.fallo ? <span className="font-semibold text-rose-700">La escritura en Woo falló. </span> : null}
          {it.origen === "odoo" ? `stock_watch copió lo que dice Odoo.${odoo}` : "stock_watch lo vio en Woo: una venta, una cancelación o una edición."}
        </div>
        {it.liga_txt && (
          <div className="mt-0.5 text-xs leading-[18px] text-pink-800">
            {it.liga_txt} {it.liga_nota && <span className="text-slate-500">{it.liga_nota}</span>}
          </div>
        )}
      </>
    );
  }
  if (it.tipo === "devolucion") return <Devolucion it={it} />;
  if (it.tipo === "full") {
    if (it.grupo === "foto") {
      return (
        <>
          <div className="text-[13px] font-semibold leading-5 text-slate-900">
            {it.nombre}: el sync leyó {n(it.a)}{it.de != null ? <span className="font-normal text-slate-600"> (antes {n(it.de)})</span> : null}
          </div>
          <div className="text-xs leading-[18px] text-slate-600">Foto del stock FULL · {it.sig}</div>
        </>
      );
    }
    const x = it.x ?? 0;
    return (
      <>
        <div className="flex flex-wrap items-baseline gap-x-2 text-[13px] leading-5">
          <span className="font-semibold text-slate-900">{it.nombre} · {it.texto}</span>
          <span className={`font-bold tabular-nums ${x > 0 ? "text-sky-800" : x < 0 ? "text-orange-800" : "text-slate-500"}`}>{conSigno(x)}</span>
        </div>
        <div className="text-xs leading-[18px] text-slate-600">
          {it.sig} · <span className="font-mono text-[11px] text-slate-500">{it.ml_tipo}</span>
        </div>
      </>
    );
  }
  if (it.tipo === "reparto") {
    const v = it.origen === "venta" ? venta(it.motivo) : null;
    const titulo = v ? (v.canal ? `Venta en ${v.canal}` : "Venta")
      : it.origen === "recuperado" ? "Reparto recuperado"
      : it.origen === "excedente" ? "Bajada: el canal ofrecía de más"
      : it.origen === "seguro" ? "Seguro de stock 0" : it.origen === "reenvio" ? "Reenvío manual" : "Reparto a los canales";
    const sub = v ? v.pedido : it.origen === "cambio" ? "" : it.motivo;
    return (
      <>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[13px] font-semibold leading-5 text-slate-900">{titulo}</span>
          {sub && <span className="break-all font-mono text-[11px] text-slate-500">{sub}</span>}
          {onRastro && (
            <button type="button" onClick={() => onRastro(sku, it.fin)}
              className="text-[11px] font-semibold text-indigo-700 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
              Ver rastro
            </button>
          )}
        </div>
        {it.sin_destinos || it.destinos.length === 0 ? (
          <div className="text-xs leading-[18px] text-slate-600">No tenía a quién escribirle.</div>
        ) : (
          <div className="mt-1 flex flex-wrap gap-1">
            {it.destinos.map((d) => (
              <span key={`${d.canal}-${d.nombre}`} title={d.detalle || undefined}
                className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold leading-4 ${
                  d.fuera ? "border border-dashed border-slate-400 bg-white text-slate-600" : CELDA_CLS[d.k]}`}>
                {d.nombre} · {d.texto}
              </span>
            ))}
          </div>
        )}
      </>
    );
  }
  if (it.relacion === "estado") {
    return (
      <>
        <div className="text-[13px] font-semibold leading-5 text-slate-900">
          {it.nombre}: {it.campo === "situacion" ? "situación" : "estado"} {it.de_txt} → {it.a_txt}
        </div>
        <div className="text-xs leading-[18px] text-slate-600">{it.via}</div>
      </>
    );
  }
  const ref = it.ref;
  const sub = it.relacion === "coincide" && ref ? `Es lo que le dejó el fan-out ${cuando(ref.hora, dia)}.`
    : it.relacion === "su_cuenta" && ref ? `Cambió en el canal: lo último que le dejamos fue ${n(ref.valor)}, ${cuando(ref.hora, dia)}.`
    : "No hubo repartos a este canal en el periodo.";
  return (
    <>
      <div className="text-[13px] font-semibold leading-5 text-slate-900">
        {it.nombre} reportó {n(it.a)}{it.de != null ? <span className="font-normal text-slate-600"> (antes {n(it.de)})</span> : null}
      </div>
      <div className={`text-xs leading-[18px] ${it.relacion === "su_cuenta" ? "text-amber-800" : "text-slate-600"}`}>
        {sub} <span className="text-slate-500">· {it.via}</span>
      </div>
    </>
  );
}

export default function TrazabilidadSku({ sku, onCerrar, onRastro, carrilInicial = "todo" }: {
  sku: string | null;
  onCerrar: () => void;
  onRastro?: (sku: string, fin: string) => void;
  /** Con qué carril abre (la pestaña FULL abre en «FULL»). */
  carrilInicial?: Carril;
}) {
  const [dias, setDias] = useState(14);
  const [carril, setCarril] = useState<Carril>(carrilInicial);
  const [h, setH] = useState<Historia | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cerrarRef = useRef<HTMLButtonElement>(null);

  const cargar = useCallback(async (s: string, d: number) => {
    setH(null);
    setError(null);
    try {
      const q = new URLSearchParams({ sku: s, dias: String(d) });
      const r = await fetchSesion(`${API_BASE}/api/fanout/historia?${q}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setH((await r.json()) as Historia);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    if (sku) void cargar(sku, dias);
  }, [sku, dias, cargar]);

  useEffect(() => {
    if (sku) cerrarRef.current?.focus();
    setCarril(carrilInicial);
  }, [sku, carrilInicial]);

  const tieneFull = !!h?.full || (h?.resumen.full ?? 0) > 0;
  const dv = h?.resumen.devoluciones;
  const tieneDevol = (dv?.n ?? 0) > 0;
  const carriles = CARRILES.filter((c) => (c.id !== "full" || tieneFull) && (c.id !== "devoluciones" || tieneDevol));
  const carrilVisto: Carril = carriles.some((c) => c.id === carril) ? carril : "todo";
  const porDia = useMemo(() => {
    const grupos: [string, ItemTraza[]][] = [];
    for (const it of (h?.items ?? []).filter((x) => enCarril(x, carrilVisto))) {
      const dia = it.hora.slice(0, 10);
      const ultimo = grupos[grupos.length - 1];
      if (ultimo && ultimo[0] === dia) ultimo[1].push(it);
      else grupos.push([dia, [it]]);
    }
    return grupos;
  }, [h, carrilVisto]);

  if (!sku) return null;
  const causas = h ? h.columnas.map((c) => ({ c, causa: h.celdas[c.id]?.causa })).filter((x) => x.causa) : [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Trazabilidad de ${sku}`}
      onKeyDown={(e) => { if (e.key === "Escape") onCerrar(); }}>
      <button type="button" aria-label="Cerrar" className="absolute inset-0 cursor-default bg-slate-900/30" onClick={onCerrar} />
      <div className="relative flex h-full w-full max-w-[780px] flex-col overflow-y-auto bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">Trazabilidad del SKU</div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="break-all font-mono text-base font-bold text-slate-900">{sku}</span>
              {h && (
                <span className="text-sm text-slate-700">
                  Odoo {h.odoo} · Woo <span className="font-bold">{h.woo}</span>
                  {h.woo_de && <span className="text-[12px] text-slate-500"> ({h.woo_de})</span>}
                  {h.full?.cuentas.map((c) => (c.de_otro ? (
                    <span key={c.cuenta}> · FULL {c.nombre}: <span className="text-[12px] text-amber-800">es de {c.de_otro.sku}</span></span>
                  ) : (
                    <span key={c.cuenta}> · FULL {c.nombre} <span className="font-bold">{n(c.stock)}</span>
                      {c.situacion && c.situacion !== "a la venta" ? <span className="text-[12px] text-slate-500"> ({c.situacion})</span> : null}
                    </span>
                  )))}
                </span>
              )}
            </div>
            {h?.sin_orden && (
              <div className="mt-0.5 text-[13px] leading-5 text-slate-600">
                {pl(h.sin_orden.piezas, "vendida", "vendidas")} sin orden en Odoo ({pl(h.sin_orden.ventas, "venta", "ventas")} de {h.sin_orden.canales}; la más vieja hace {h.sin_orden.edad})
                {h.sin_orden.explica ? ": es toda la diferencia entre Odoo y Woo." : "."}
              </div>
            )}
          </div>
          <button ref={cerrarRef} type="button" onClick={onCerrar}
            className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
            <X size={18} aria-hidden /><span className="sr-only">Cerrar</span>
          </button>
        </div>

        <div className="flex flex-col gap-4 px-5 py-5 sm:px-6">
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">No se pudo leer la bitácora ({error}).</p>}
          {!h && !error && <p className="text-sm text-slate-500">Leyendo la bitácora…</p>}
          {h && !h.existe && (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
              No encontré «{h.sku}» ni en la bitácora ni en los canales. Revisa que el SKU esté bien escrito.
            </p>
          )}

          {h && h.existe && (
            <>
              <section aria-label="Hoy en cada canal" className="flex flex-col gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Hoy en cada canal</h3>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {h.columnas.map((col) => {
                    const c = h.celdas[col.id];
                    if (!c) return null;
                    const cls = (c.p && CELDA_MATRIZ_PUNTEADO[c.k]) || CELDA_MATRIZ_SOLIDO[c.k];
                    return (
                      <div key={col.id} className={`flex min-h-[58px] flex-col justify-center rounded-lg px-2.5 py-1.5 ${cls}`}>
                        <span className="text-[11px] font-semibold uppercase tracking-wide opacity-80">{col.nombre}</span>
                        <span className="text-[14px] font-semibold leading-5">
                          {c.v} {c.d && <span className="font-medium">{c.d}</span>}
                        </span>
                        <span className="truncate text-[11px] leading-[15px]">{c.s}</span>
                      </div>
                    );
                  })}
                </div>
                {causas.map(({ c, causa }) => causa && (
                  <p key={c.id} className="text-xs leading-[18px] text-slate-700">
                    <span className={`mr-1.5 rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${CAUSA_CLS[causa.c]}`}>{causa.t}</span>
                    <span className="font-semibold">{c.nombre}:</span> {causa.d}
                  </p>
                ))}
              </section>

              {(tieneFull || tieneDevol) && (
                <div className="flex w-fit flex-wrap gap-1 rounded-xl bg-slate-100 p-1" role="group" aria-label="Carril">
                  {carriles.map((c) => (
                    <button key={c.id} type="button" aria-pressed={carril === c.id} onClick={() => setCarril(c.id)}
                      className={`h-8 rounded-lg px-3 text-xs font-semibold ${carril === c.id ? "bg-indigo-600 text-white shadow-sm" : "text-slate-600 hover:bg-white"}`}>
                      {c.texto}
                    </button>
                  ))}
                </div>
              )}

              {carrilVisto === "full" && h.full && (
                <section aria-label="FULL en el periodo" className="flex flex-col gap-1 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                  <span className="text-[13px] font-bold text-slate-900">En FULL, últimos {h.dias} días</span>
                  <span className="text-xs leading-[18px] text-slate-700">
                    {ORDEN_FULL.filter((g) => h.full?.grupos[g]).map((g) => {
                      const v = h.full?.grupos[g];
                      return `${GRUPO_FULL[g].texto} ${g === "vendido" ? n(Math.abs(v?.piezas ?? 0)) : conSigno(v?.piezas ?? 0)}`;
                    }).join(" · ") || "Sin avisos de la bodega en el periodo."}
                  </span>
                  {h.full.cuentas.map((c) => (c.de_otro ? (
                    <span key={c.cuenta} className="text-xs leading-[18px] text-amber-900">
                      <span className="font-semibold">{c.nombre}:</span> su publicación {c.de_otro.listing} la declara Mercado Libre
                      como {c.de_otro.sku} ({n(c.de_otro.stock)} en FULL); la fila de este SKU quedó con un número viejo y no cuenta.
                    </span>
                  ) : (
                    <span key={c.cuenta} className="text-xs leading-[18px] text-slate-700">
                      <span className="font-semibold">{c.nombre}:</span> {n(c.stock)} en FULL
                      {c.cobertura != null ? ` · le alcanza ${c.cobertura.toLocaleString("es-MX")} días (${n(c.vendidas_14d)} vendidas en 14 días)`
                        : " · sin ventas FULL en 14 días"}
                    </span>
                  )))}
                  {h.full.grupos.ajuste && h.full.grupos.vendido && (
                    <span className="text-xs leading-[18px] text-slate-600">
                      Los ajustes de ML siguen a las ventas: no mueven lo vendible (lo muestra el libro diario de la pestaña FULL).
                    </span>
                  )}
                </section>
              )}

              {carrilVisto === "devoluciones" && dv && (
                <section aria-label="Devoluciones en el periodo" className="flex flex-col gap-1 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                  <span className="text-[13px] font-bold text-slate-900">Devoluciones, últimos {h.dias} días</span>
                  <span className="text-xs leading-[18px] text-slate-700">
                    {pl(dv.n, "devolución", "devoluciones")} · {pl(dv.piezas, "pieza", "piezas")}
                    {" · "}{dv.a_bodega.toLocaleString("es-MX")} ya en nuestra bodega
                    {dv.a_bodega ? `, ${dv.reingresaron.toLocaleString("es-MX")} con reingreso en Odoo` : ""}
                    {dv.en_camino ? ` · ${dv.en_camino.toLocaleString("es-MX")} en camino` : ""}
                    {dv.a_full ? ` · ${dv.a_full.toLocaleString("es-MX")} al almacén de ML (regresan a FULL)` : ""}
                  </span>
                  <span className="text-xs leading-[18px] text-slate-600">
                    El reingreso es la primera subida de Odoo (su foto, no lo escrito en Woo) en los días siguientes a que
                    la caja llega: una coincidencia probable por fecha, no una liga dura (Bodega recibe en Odoo sin ligarlo
                    a la venta).
                  </span>
                  {h.truncado && (
                    <span className="text-xs leading-[18px] text-amber-800">
                      El resumen cuenta todas las del periodo; la línea muestra solo los {h.items.length} movimientos más recientes.
                    </span>
                  )}
                </section>
              )}
              {h.devoluciones_ok === false && (
                <p className="text-xs leading-[18px] text-amber-800">
                  No se pudieron leer {(h.devoluciones_fallas ?? ["ml", "temu"]).map((f) => (f === "ml" ? "las devoluciones de ML" : "las cancelaciones de Temu")).join(" ni ")} de
                  este SKU; el resto de la línea está completo.
                </p>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
                <div className="flex items-center gap-1.5" role="group" aria-label="Periodo">
                  {PERIODOS.map((p) => (
                    <button key={p} type="button" aria-pressed={dias === p} onClick={() => setDias(p)}
                      className={`h-8 rounded-full border px-3 text-xs font-semibold ${
                        dias === p ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                      {p} días
                    </button>
                  ))}
                </div>
                <p className="text-xs leading-[18px] text-slate-600">
                  {pl(h.resumen.cambios_woo, "cambio", "cambios")} en Woo · {pl(h.resumen.repartos, "reparto", "repartos")}
                  {h.resumen.con_rechazo ? ` (${h.resumen.con_rechazo} con rechazo)` : ""}
                  {" · "}{pl(h.resumen.su_cuenta, "vez", "veces")} el canal reportó algo que no le escribimos
                  {h.resumen.full ? ` · ${pl(h.resumen.full, "aviso", "avisos")} de FULL` : ""}
                  {dv?.n ? ` · ${pl(dv.n, "devolución", "devoluciones")}` : ""}
                </p>
              </div>

              {h.items.length === 0 ? (
                <div className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">
                  Sin movimientos{carrilVisto !== "todo" ? " en este carril" : ""} en los últimos {h.dias} días.
                  {h.dias < 60 && (
                    <button type="button" onClick={() => setDias(60)}
                      className="ml-2 font-semibold text-indigo-700 underline-offset-2 hover:underline">Ver 60 días</button>
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-4">
                  {porDia.map(([dia, its]) => (
                    <section key={dia} aria-label={etiquetaDia(dia, h.hoy)}>
                      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{etiquetaDia(dia, h.hoy)}</h3>
                      <ol>
                        {its.map((it, i) => (
                          <li key={`${it.tipo}-${it.ts}-${i}`} className="grid gap-x-2" style={{ gridTemplateColumns: "40px 16px minmax(0,1fr)" }}>
                            <time dateTime={it.ts} className="pt-[3px] text-right text-xs tabular-nums text-slate-500">{it.hora.slice(11, 16)}</time>
                            <span className="relative flex justify-center">
                              {i < its.length - 1 && <span className="absolute bottom-0 top-3 w-0.5 bg-slate-200" aria-hidden />}
                              <span className={`relative mt-[5px] h-3 w-3 ${it.tipo === "full" ? "rounded-[3px]" : it.tipo === "devolucion" ? "rotate-45 rounded-[2px]" : "rounded-full"} ${it.tipo === "canal" && it.relacion === "estado" ? "border-2 bg-white" : ""}`}
                                style={it.tipo === "canal" && it.relacion === "estado" ? { borderColor: color(it) } : { background: color(it) }} aria-hidden />
                            </span>
                            <div className="min-w-0 pb-3">
                              <Renglon it={it} dia={dia} sku={h.sku} onRastro={onRastro} />
                            </div>
                          </li>
                        ))}
                      </ol>
                    </section>
                  ))}
                  {h.truncado && (
                    <p className="text-xs text-slate-600">Se muestran los {h.items.length} movimientos más recientes de {h.total.toLocaleString("es-MX")}. Elige un periodo más corto para ver uno completo.</p>
                  )}
                </div>
              )}

              <div className="flex flex-wrap gap-x-4 gap-y-1.5 border-t border-slate-100 pt-3 text-xs text-slate-600">
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR.woo }} />cambio en Woo</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: TONO_COLOR.ok }} />reparto que llegó</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: TONO_COLOR.mal }} />reparto con rechazo</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: TONO_COLOR.omit }} />sin escrituras</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR.coincide }} />el canal reporta lo que le escribimos</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR.su_cuenta }} />cambió en el canal</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 bg-white" style={{ borderColor: COLOR.estado }} />cambio de estado</span>
                {tieneDevol && (
                  <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 rounded-[2px]" style={{ background: COLOR.devolucion }} />devolución</span>
                )}
                {tieneFull && (["vendido", "llego", "ajuste", "retiro", "foto"] as const).map((g) => (
                  <span key={g} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: GRUPO_FULL[g].color }} />FULL: {GRUPO_FULL[g].texto.toLowerCase()}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
