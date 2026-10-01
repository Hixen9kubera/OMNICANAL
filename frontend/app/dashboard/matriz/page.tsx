"use client";

/**
 * /dashboard/matriz — ¿Coincide cada canal con Woo? Una fila por SKU (los
 * peores primero) y una columna por canal, coloreada contra Woo. Cada celda dice
 * de cuándo es su dato: «censo» (lo leyó el canal), «escrito» (lo mandó el
 * fan-out) o «igual desde» (la última vez que ese dato cambió en el canal); se
 * queda con el más reciente. Es una FOTO: se rehace cada minuto.
 *
 * Cada celda que no coincide trae su CAUSA (rechazo 403, cambio perdido, sin
 * alinear, cambió el canal, omitida a propósito…), sacada de la bitácora del
 * fan-out por `fanout_vivo._causas`; el detalle sale al pasar el cursor.
 *
 * «Todo» son TODOS los SKUs con algo a la venta en el reparto, más los que piden
 * revisión aunque no estén a la venta. Tocar un SKU abre su línea de
 * trazabilidad (`TrazabilidadSku`); la búsqueda abre cualquier SKU, esté o no en
 * la tabla.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Clock, LayoutGrid, Search } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import BannerFanout, { ACCION_BANNER } from "@/components/fanout/BannerFanout";
import FanoutPestanas from "@/components/fanout/FanoutPestanas";
import RastroCambio from "@/components/fanout/RastroCambio";
import TrazabilidadSku from "@/components/fanout/TrazabilidadSku";
import type { Causa, Matriz } from "@/components/fanout/tipos";
import { CAUSA_CLS, CAUSA_NOMBRE, CELDA_MATRIZ_PUNTEADO as PUNTEADO, CELDA_MATRIZ_SOLIDO as SOLIDO } from "@/components/fanout/tipos";

const FILTROS = [
  { id: "todo", texto: "Todo" },
  { id: "distinto", texto: "Algo distinto" },
  { id: "demas", texto: "Ofrece de más" },
  { id: "sin_orden", texto: "Vendidas sin orden en Odoo" },
];
const ORDEN_CAUSAS: Causa["c"][] = ["403", "perdido", "tarde", "canal", "omitida", "fuera", "error", "camino"];

function Leyenda({ cls, texto }: { cls: string; texto: string }) {
  return <span className="flex items-center gap-1.5"><span className={`h-3 w-3 rounded-[3px] ${cls}`} />{texto}</span>;
}

export default function MatrizCoincidencia() {
  const [m, setM] = useState<Matriz | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filtro, setFiltro] = useState("todo");
  const [busca, setBusca] = useState("");
  const [traza, setTraza] = useState<string | null>(null);
  const [rastro, setRastro] = useState<{ sku: string; fin: string } | null>(null);

  useEffect(() => {
    const f = new URLSearchParams(window.location.search).get("filtro");
    if (f && (FILTROS.some((x) => x.id === f) || f.startsWith("causa:"))) setFiltro(f);
  }, []);

  const cargar = useCallback(async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/matriz`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setM(await r.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 60_000);
    return () => clearInterval(t);
  }, [cargar]);

  const cuenta = (id: string) => (m ? m.filas.filter((f) => id === "todo" || f.tags.includes(id)).length : 0);
  const filas = useMemo(() => {
    if (!m) return [];
    const q = busca.trim().toUpperCase();
    return m.filas.filter((f) => (filtro === "todo" || f.tags.includes(filtro)) && (!q || f.sku.toUpperCase().includes(q)));
  }, [m, filtro, busca]);
  const causas = useMemo(() => {
    if (!m) return [];
    return ORDEN_CAUSAS.map((c) => ({ c, n: m.filas.filter((f) => f.tags.includes(`causa:${c}`)).length }))
      .filter((x) => x.n > 0);
  }, [m]);
  const plantilla = m ? `minmax(230px,1.5fr) 76px 76px repeat(${m.columnas.length}, minmax(156px,1fr))` : "";

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-6 sm:px-6">
        <FanoutPestanas />
        <BannerFanout
          icono={<LayoutGrid size={28} aria-hidden />}
          titulo="Coincidencia por SKU"
          texto="¿Coincide cada canal con Woo? Una fila por SKU y una columna por canal: el color lo compara contra Woo, debajo va de cuándo es el dato y, si no coincide, por qué."
          acciones={
            <span className={ACCION_BANNER}>
              <Clock size={14} aria-hidden />{m ? `Foto de ${m.ahora} · se rehace cada minuto` : "Armando la foto…"}
            </span>
          }
          cifra={m ? cuenta("distinto").toLocaleString("es-MX") : "—"}
          cifraTexto="SKUs con algo distinto"
        />

        {error && !m && <p className="rounded-2xl bg-white p-6 text-sm text-rose-800 shadow-sm">No se pudo armar la matriz ({error}). Se reintenta en un minuto.</p>}

        {m && (
          <>
            <section aria-label="Coincidencia por canal" className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))" }}>
              {m.barras.map((b) => {
                const pctIgual = b.vivas ? (b.iguales / b.vivas) * 100 : 0;
                const pctMas = b.vivas ? (b.de_mas / b.vivas) * 100 : 0;
                const pctMenos = b.vivas ? (b.de_menos / b.vivas) * 100 : 0;
                return (
                  <div key={b.canal} className="flex flex-col gap-2.5 rounded-2xl bg-white px-5 py-4 shadow-sm">
                    <span className="text-[15px] font-bold text-slate-900">{b.nombre}</span>
                    <div className="flex items-baseline gap-2">
                      <span className="text-[28px] font-bold leading-[34px] text-slate-900">{b.iguales.toLocaleString("es-MX")}</span>
                      <span className="text-sm text-slate-600">de {b.vivas.toLocaleString("es-MX")} a la venta coinciden con Woo</span>
                    </div>
                    <div role="img" aria-label={`${b.iguales} iguales, ${b.de_mas} de más, ${b.de_menos} de menos`}
                      className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
                      <div className="bg-emerald-600" style={{ width: `${pctIgual}%` }} />
                      <div className="bg-rose-600" style={{ width: `${pctMas}%` }} />
                      <div className="bg-amber-500" style={{ width: `${pctMenos}%` }} />
                    </div>
                    <div className="text-xs text-slate-600">
                      {b.de_mas ? `${b.de_mas} ${b.de_mas === 1 ? "ofrece" : "ofrecen"} de más` : "ninguna ofrece de más"}{b.de_menos ? ` · ${b.de_menos} de menos` : ""}
                    </div>
                    {b.causas && b.causas.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
                        <span>Por qué:</span>
                        {b.causas.map((c) => (
                          <span key={c.c} className={`rounded px-1.5 py-px text-[11px] font-semibold ${CAUSA_CLS[c.c as Causa["c"]] ?? "bg-slate-100 text-slate-700"}`}>
                            {CAUSA_NOMBRE[c.c as Causa["c"]] ?? c.t} · {c.n}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </section>
            <p className="-mt-1 text-xs text-slate-600">
              Amazon y Walmart no están en el reparto (decisión del 18-ago), así que no se comparan aquí. FULL lo surte la bodega de Mercado Libre.
            </p>

            <section className="flex flex-col gap-3 rounded-2xl bg-white pb-2 pt-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-6">
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filtrar filas">
                  {FILTROS.map((f) => (
                    <button key={f.id} type="button" aria-pressed={filtro === f.id} onClick={() => setFiltro(f.id)}
                      className={`h-[34px] rounded-full border px-3 text-[13px] font-semibold ${
                        filtro === f.id ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                      {f.texto} · {cuenta(f.id)}
                    </button>
                  ))}
                  <label className="ml-1 flex h-[34px] items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 text-[13px] text-slate-700 focus-within:border-indigo-500">
                    <Search size={14} className="text-slate-500" aria-hidden />
                    <span className="sr-only">Buscar SKU</span>
                    <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar SKU"
                      onKeyDown={(e) => { if (e.key === "Enter" && busca.trim().length >= 3) setTraza(busca.trim().toUpperCase()); }}
                      className="w-32 bg-transparent font-mono text-xs outline-none placeholder:font-sans placeholder:text-slate-500" />
                  </label>
                  {busca.trim().length >= 3 && !m.filas.some((f) => f.sku.toUpperCase() === busca.trim().toUpperCase()) && (
                    <button type="button" onClick={() => setTraza(busca.trim().toUpperCase())}
                      className="h-[34px] rounded-full border border-indigo-200 bg-indigo-50 px-3 text-[13px] font-semibold text-indigo-800 hover:bg-indigo-100">
                      Ver trazabilidad de {busca.trim().toUpperCase()}
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-3.5 gap-y-1.5 text-xs text-slate-600">
                  <Leyenda cls="border border-emerald-300 bg-emerald-50" texto="igual a Woo" />
                  <Leyenda cls="border border-rose-400 bg-rose-50" texto="ofrece de más" />
                  <Leyenda cls="border border-amber-400 bg-amber-50" texto="ofrece de menos" />
                  <Leyenda cls="bg-rose-600" texto="rechazado" />
                  <Leyenda cls="border border-sky-300 bg-sky-50" texto="FULL" />
                  <Leyenda cls="border border-dashed border-slate-500 bg-white" texto="no está a la venta" />
                </div>
              </div>
              {causas.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 px-6" role="group" aria-label="Filtrar por causa">
                  <span className="text-xs font-semibold text-slate-600">Por causa:</span>
                  {causas.map(({ c, n }) => {
                    const id = `causa:${c}`;
                    const activo = filtro === id;
                    return (
                      <button key={c} type="button" aria-pressed={activo} onClick={() => setFiltro(activo ? "todo" : id)}
                        className={`flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-semibold ${
                          activo ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                        <span className={`h-2.5 w-2.5 rounded-full ${CAUSA_CLS[c].split(" ")[0]}`} />
                        {CAUSA_NOMBRE[c]} · {n}
                      </button>
                    );
                  })}
                  <span className="text-xs text-slate-500">Pasa el cursor sobre la causa de una celda para ver el detalle.</span>
                </div>
              )}
              <div className="overflow-x-auto">
                <div className="flex min-w-[1100px] flex-col">
                  <div className="grid items-end gap-2 border-b border-slate-200 px-6 py-2" style={{ gridTemplateColumns: plantilla }}>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">SKU</span>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Odoo</span>
                    <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Woo</span>
                    {m.columnas.map((c) => (
                      <span key={c.id} className="flex flex-col">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{c.nombre}</span>
                        <span className="text-[11px] text-slate-500">
                          {c.canal === "tiktok" ? "censo cada 2 h" : c.canal === "temu" ? "censo cada 4 h" : "lectura por rotación"}
                        </span>
                      </span>
                    ))}
                  </div>
                  {filas.length === 0 && <p className="px-6 py-6 text-sm text-slate-500">Nada con ese filtro.</p>}
                  {filas.map((f) => (
                    <div key={f.sku} className="grid items-center gap-2 border-b border-slate-100 px-6 py-1.5" style={{ gridTemplateColumns: plantilla }}>
                      <button type="button" onClick={() => setTraza(f.sku)} title="Ver su trazabilidad"
                        className="group flex min-w-0 flex-col rounded-md text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                        <span className="break-all font-mono text-xs font-semibold leading-[17px] text-indigo-800 underline-offset-2 group-hover:underline">{f.sku}</span>
                        <span className="text-xs leading-[17px] text-slate-600">{f.que}</span>
                      </button>
                      <span className="flex flex-col items-start">
                        <span className={`rounded-lg px-2 py-1 text-[13px] font-semibold ${f.dif ? "border border-amber-400 bg-amber-50 text-amber-800" : "text-slate-700"}`}>{f.odoo}</span>
                        {f.sin_orden && (
                          <span title={`${f.sin_orden.piezas} ${f.sin_orden.piezas === 1 ? "pieza" : "piezas"} en ${f.sin_orden.ventas} ${f.sin_orden.ventas === 1 ? "venta" : "ventas"} de ${f.sin_orden.canales} que esperan su orden en Odoo; la más vieja hace ${f.sin_orden.edad}. Woo las descuenta hasta que la orden nace.`}
                            className="cursor-help px-2 text-[11px] leading-[14px] text-slate-600">−{f.sin_orden.piezas} sin orden</span>
                        )}
                      </span>
                      <span className="justify-self-start px-2 py-1 text-[13px] font-bold text-slate-900">{f.woo}</span>
                      {m.columnas.map((col) => {
                        const c = f.celdas[col.id];
                        if (!c) return <span key={col.id} />;
                        const cls = (c.p && PUNTEADO[c.k]) || SOLIDO[c.k];
                        return (
                          <span key={col.id} className={`flex min-h-11 flex-col justify-center rounded-lg px-2 py-1 ${cls}`}>
                            <span className="text-[13px] font-semibold leading-[18px]">
                              {c.v} {c.d && <span className="font-medium">{c.d}</span>}
                            </span>
                            <span className="truncate text-[11px] leading-[15px]">{c.s}</span>
                            {c.causa && (
                              <span title={c.causa.d} aria-label={`Causa: ${c.causa.t}. ${c.causa.d}`}
                                className={`mt-1 max-w-full cursor-help self-start truncate rounded px-1.5 py-px text-[10px] font-semibold uppercase leading-4 tracking-wide ${
                                  c.k === "rech" ? "bg-white text-rose-700" : CAUSA_CLS[c.causa.c]}`}>
                                {c.causa.t}
                              </span>
                            )}
                          </span>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
              <p className="px-6 pb-2 pt-1 text-xs leading-[18px] text-slate-600">
                «censo» es lo que leyó el canal; «escrito» es lo que mandó el fan-out; «igual desde» es la última vez que ese dato cambió en el canal. Cada celda se queda con el más reciente. «−N sin orden» bajo Odoo son piezas ya vendidas cuya orden todavía no nace en Odoo (nace al comprar la guía): Woo las descuenta mientras tanto, así que si Odoo y Woo difieren justo eso, no hay nada desfasado. Toca un SKU para ver su línea de trazabilidad.
              </p>
            </section>
          </>
        )}
      </main>
      <TrazabilidadSku sku={traza} onCerrar={() => setTraza(null)}
        onRastro={(sku, fin) => { setTraza(null); setRastro({ sku, fin }); }} />
      <RastroCambio sel={rastro} onCerrar={() => setRastro(null)} onIr={(sku, fin) => setRastro({ sku, fin })}
        onTrazabilidad={(sku) => { setRastro(null); setTraza(sku); }} />
    </div>
  );
}
