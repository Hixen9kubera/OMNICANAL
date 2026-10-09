"use client";

/**
 * /dashboard/bodegas — ¿Cuadra el inventario propio de kubera (las bodegas de la
 * 0064/0065, en `almacen.*` desde la 0068) con Odoo y con lo que stock_watch copia
 * a Woo? Una fila por SKU: Odoo por bodega (TEXCO, TEX2, DROP: libre = físico −
 * reservado), kubera por bodega (`almacen.stock_almacen`, sólo las que tienen
 * saldo), el «Woo esperado» calculado igual que stock_watch, el Woo de su foto y
 * si coinciden.
 *
 * Arriba, las bodegas de kubera, las cuatro banderas (sin fila = su variable o
 * apagada) y la salud de la sincronización. Ya no hay formatos ni «puerta»: eran
 * de la mudanza a TEX3, que no existirá (limpieza de la Fase 1). Es una FOTO:
 * `GET /api/fanout/bodegas` se pide cada minuto (Odoo lleva caché de 10 min en el
 * backend; si no contesta, la página sigue con kubera y Woo). Tocar un SKU abre
 * su libro (`LibroSku`); la búsqueda abre cualquier SKU. Solo lee.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Boxes, CircleX, Clock, Info, Search, TriangleAlert } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import BannerFanout, { ACCION_BANNER } from "@/components/fanout/BannerFanout";
import EstadoBodegas from "@/components/fanout/EstadoBodegas";
import FanoutPestanas from "@/components/fanout/FanoutPestanas";
import LibroSku from "@/components/fanout/LibroSku";
import RastroCambio from "@/components/fanout/RastroCambio";
import TrazabilidadSku from "@/components/fanout/TrazabilidadSku";
import type { FilaBodega, ResumenBodegas } from "@/components/fanout/tipos";
import { COINCIDE_BODEGA, conSigno, haceSegundos, horaCorta } from "@/components/fanout/tipos";

const FILTROS = [
  { id: "todo", texto: "Todo" },
  { id: "kubera", texto: "Con saldo en kubera" },
  { id: "no_coincide", texto: "No coincide con Woo" },
];

const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("es-MX"));

function Leyenda({ cls, texto }: { cls: string; texto: string }) {
  return <span className="flex items-center gap-1.5"><span className={`h-3 w-3 rounded-[3px] ${cls}`} />{texto}</span>;
}

function Encabezado({ titulo, sub }: { titulo: string; sub?: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{titulo}</span>
      {sub && <span className="text-[11px] text-slate-500">{sub}</span>}
    </span>
  );
}

/** La celda de una bodega de Odoo: el libre grande y, si hay reservado, el físico. */
function CeldaOdoo({ f, codigo }: { f: FilaBodega; codigo: string }) {
  if (f.odoo == null) return <span className="px-2 text-[13px] text-slate-500" title="Odoo no respondió">—</span>;
  if (!f.odoo_existe) return <span className="px-2 text-[13px] text-slate-500" title="El código no existe en Odoo">—</span>;
  const c = f.odoo[codigo];
  const libre = c?.libre ?? 0;
  return (
    <span className="flex flex-col px-2" title={c ? `${codigo}: físico ${n(c.fisico)} − reservado ${n(c.reservado)} = libre ${n(libre)}` : undefined}>
      <span className={`text-[13px] font-semibold tabular-nums ${libre ? "text-slate-900" : "text-slate-500"}`}>{n(libre)}</span>
      {c && c.reservado !== 0 && (
        <span className="text-[11px] leading-[14px] text-slate-600">fís {n(c.fisico)} · res {n(c.reservado)}</span>
      )}
    </span>
  );
}

export default function PaginaBodegas() {
  const [d, setD] = useState<ResumenBodegas | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filtro, setFiltro] = useState("todo");
  const [busca, setBusca] = useState("");
  const [libro, setLibro] = useState<string | null>(null);
  const [traza, setTraza] = useState<string | null>(null);
  const [rastro, setRastro] = useState<{ sku: string; fin: string } | null>(null);

  useEffect(() => {
    const f = new URLSearchParams(window.location.search).get("filtro");
    if (f && FILTROS.some((x) => x.id === f)) setFiltro(f);
  }, []);

  const cargar = useCallback(async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/bodegas`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as ResumenBodegas;
      if (!j.ok) throw new Error(j.motivo || "sin respuesta");
      setD(j);
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

  const cuenta = (id: string) => (d ? d.filas.filter((f) => id === "todo" || f.tags.includes(id)).length : 0);
  const filas = useMemo(() => {
    if (!d) return [];
    const q = busca.trim().toUpperCase();
    return d.filas.filter((f) => (filtro === "todo" || f.tags.includes(filtro)) && (!q || f.sku.toUpperCase().includes(q)));
  }, [d, filtro, busca]);
  const nk = d?.columnas_kubera.length ?? 0;
  const plantilla = `minmax(210px,1.5fr) repeat(3, minmax(84px,0.6fr)) ${nk ? `repeat(${nk}, minmax(96px,0.7fr)) ` : ""}minmax(124px,0.9fr) minmax(96px,0.7fr) minmax(118px,0.8fr)`;
  const buscado = busca.trim().toUpperCase();

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-6 sm:px-6">
        <FanoutPestanas />
        <BannerFanout
          icono={<Boxes size={28} aria-hidden />}
          titulo="Bodegas"
          texto="¿Cuadra el inventario de kubera con Odoo y con lo que stock_watch copia a Woo? Una fila por SKU: Odoo por bodega, kubera por bodega y el Woo esperado contra el de la foto."
          acciones={
            <span className={ACCION_BANNER}>
              <Clock size={14} aria-hidden />
              {d ? `Foto de ${d.ahora.slice(11, 16)} · se rehace cada minuto · ${d.odoo.ok ? `Odoo leído ${haceSegundos(d.odoo.edad_s)}` : "Odoo no respondió"}` : "Armando la foto…"}
            </span>
          }
          cifra={d ? d.conteo.no_coincide.toLocaleString("es-MX") : "—"}
          cifraTexto="SKUs que no coinciden con Woo"
        />

        {error && !d && (
          <p className="rounded-2xl bg-white p-6 text-sm text-rose-800 shadow-sm">No se pudo leer Bodegas ({error}). Se reintenta en un minuto.</p>
        )}
        {!d && !error && <p className="rounded-2xl bg-white p-6 text-sm text-slate-600 shadow-sm">Leyendo kubera, Odoo y la foto de stock_watch…</p>}

        {d && (
          <>
            <p className="flex items-start gap-2.5 rounded-2xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-[13px] leading-5 text-indigo-950">
              <Info size={18} className="mt-px shrink-0 text-indigo-700" aria-hidden />
              <span>{d.formula.texto}</span>
            </p>
            {!d.tablas.ok && (
              <p role="status" className="flex items-start gap-2.5 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] leading-5 text-amber-950">
                <TriangleAlert size={18} className="mt-px shrink-0 text-amber-700" aria-hidden />
                <span>
                  <b>Faltan las tablas de la 0064/0065</b> en esta base ({d.tablas.faltan.join(", ")}). No hay bodegas de kubera ni
                  libro que leer; la tabla sigue comparando Odoo con la foto de stock_watch.
                </span>
              </p>
            )}
            {(!d.odoo.ok || d.odoo.viejo) && (
              <p role="status" className="flex items-start gap-2.5 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] leading-5 text-amber-950">
                <TriangleAlert size={18} className="mt-px shrink-0 text-amber-700" aria-hidden />
                <span>
                  {d.odoo.ok
                    ? <><b>{d.odoo.motivo || "Odoo no respondió"}</b>: las columnas de Odoo son de su última lectura, {haceSegundos(d.odoo.edad_s)}
                      {!d.odoo.tras_pasada ? ", anterior a la última pasada de stock_watch, así que no se compara el Odoo de hoy contra la foto" : ""}.</>
                    : <><b>{d.odoo.motivo || "Odoo no respondió"}</b>: se muestran kubera y Woo. El Woo esperado sale de la foto de stock_watch y no
                      depende de Odoo{d.universo_parcial ? "; sin Odoo no se sabe qué hay en TEX2, así que la tabla sólo trae lo que tiene kubera" : ""}.</>}
                </span>
              </p>
            )}

            <EstadoBodegas d={d} />

            <section aria-label="Inventario por SKU" className="flex flex-col gap-3 rounded-2xl bg-white pb-2 pt-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-6">
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filtrar filas">
                  {FILTROS.map((f) => (
                    <button key={f.id} type="button" aria-pressed={filtro === f.id} onClick={() => setFiltro(f.id)}
                      className={`h-[34px] rounded-full border px-3 text-[13px] font-semibold ${
                        filtro === f.id ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                      {f.texto} · {cuenta(f.id).toLocaleString("es-MX")}
                    </button>
                  ))}
                  <label className="ml-1 flex h-[34px] items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 text-[13px] text-slate-700 focus-within:border-indigo-500">
                    <Search size={14} className="text-slate-500" aria-hidden />
                    <span className="sr-only">Buscar SKU</span>
                    <input value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar SKU"
                      onKeyDown={(e) => { if (e.key === "Enter" && buscado.length >= 3) setLibro(buscado); }}
                      className="w-32 bg-transparent font-mono text-xs outline-none placeholder:font-sans placeholder:text-slate-500" />
                  </label>
                  {buscado.length >= 3 && !d.filas.some((f) => f.sku.toUpperCase() === buscado) && (
                    <button type="button" onClick={() => setLibro(buscado)}
                      className="h-[34px] rounded-full border border-indigo-200 bg-indigo-50 px-3 text-[13px] font-semibold text-indigo-800 hover:bg-indigo-100">
                      Ver libro de {buscado}
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-3.5 gap-y-1.5 text-xs text-slate-600">
                  <Leyenda cls={COINCIDE_BODEGA.igual.cls} texto="Woo coincide" />
                  <Leyenda cls={COINCIDE_BODEGA.mas.cls} texto="Woo ofrece de más" />
                  <Leyenda cls={COINCIDE_BODEGA.menos.cls} texto="Woo ofrece de menos" />
                  <Leyenda cls={COINCIDE_BODEGA.por_copiar.cls} texto={`por copiar en la próxima pasada · ${n(d.conteo.por_copiar)}`} />
                  <Leyenda cls={COINCIDE_BODEGA.no_toca.cls} texto="sin comparar" />
                </div>
              </div>
              <div className="overflow-x-auto">
                <div className="flex min-w-[1100px] flex-col">
                  <div className="grid items-end gap-2 border-b border-slate-200 px-6 py-2" style={{ gridTemplateColumns: plantilla }}>
                    <Encabezado titulo="SKU" sub={`${n(d.conteo.filas)} · los peores primero`} />
                    {d.columnas_odoo.map((c) => <Encabezado key={c.codigo} titulo={`Odoo ${c.codigo}`} sub="libre" />)}
                    {d.columnas_kubera.map((c) => (
                      <Encabezado key={c.codigo} titulo={`kubera ${c.codigo}`} sub={c.cuenta_para_woo ? "libre · cuenta para Woo" : "libre · no cuenta para Woo"} />
                    ))}
                    <Encabezado titulo="Woo esperado" sub="como stock_watch" />
                    <Encabezado titulo="Woo hoy" sub="foto de stock_watch" />
                    <Encabezado titulo="Coincide" />
                  </div>
                  {filas.length === 0 && (
                    <p className="px-6 py-6 text-sm text-slate-600">
                      {d.filas.length === 0
                        ? d.universo_parcial
                          ? "Odoo no respondió y kubera no tiene saldo: no hay SKUs que mostrar todavía."
                          : "Sin SKUs que mostrar: Odoo no trajo nada de TEX2 y kubera no tiene saldo."
                        : buscado
                          ? `Ningún SKU con «${buscado}» en este filtro.`
                          : filtro === "kubera" ? "Kubera todavía no tiene saldo de ningún SKU."
                            : filtro === "no_coincide" ? "Todo lo que se puede comparar coincide con Woo." : "Nada con ese filtro."}
                    </p>
                  )}
                  {filas.map((f) => {
                    const co = COINCIDE_BODEGA[f.coincide];
                    // Lo que de verdad entra en el esperado: kubera sólo si stock_watch la suma.
                    const sumas = [f.odoo_base != null ? `Odoo ${n(f.odoo_base)}` : null,
                      f.kubera_base != null ? `+ kubera ${n(f.kubera_base)}` : null,
                      f.pend ? `− ${n(f.pend)} sin orden` : null,
                      !d.formula.suma_kubera && f.libre_kubera ? `(kubera ${n(f.libre_kubera)} no suma)` : null].filter(Boolean).join(" ");
                    return (
                      <div key={f.sku} className="grid items-center gap-2 border-b border-slate-100 px-6 py-1.5" style={{ gridTemplateColumns: plantilla }}>
                        <button type="button" onClick={() => setLibro(f.sku)} title="Ver su libro"
                          className="group flex min-w-0 flex-col rounded-md text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                          <span className="break-all font-mono text-xs font-semibold leading-[17px] text-indigo-800 underline-offset-2 group-hover:underline">{f.sku}</span>
                          {f.nombre && <span className="truncate text-xs leading-[17px] text-slate-600">{f.nombre}</span>}
                          {f.avisos.length > 0 && (
                            <span className="truncate text-[11px] leading-[15px] text-amber-800" title={f.avisos.join(". ")}>{f.avisos[0]}</span>
                          )}
                        </button>
                        {d.columnas_odoo.map((c) => <CeldaOdoo key={c.codigo} f={f} codigo={c.codigo} />)}
                        {d.columnas_kubera.map((c) => {
                          const k = f.kubera[c.codigo];
                          return k ? (
                            <span key={c.codigo} className="flex flex-col rounded-lg bg-indigo-50 px-2 py-1"
                              title={`${c.codigo}: físico ${n(k.fisico)} − apartado ${n(k.apartado)} = libre ${n(k.libre)}`}>
                              <span className="text-[13px] font-semibold tabular-nums text-indigo-950">{n(k.libre)}</span>
                              <span className="text-[11px] leading-[14px] text-indigo-900">fís {n(k.fisico)} · apart {n(k.apartado)}</span>
                            </span>
                          ) : <span key={c.codigo} className="px-2 text-[13px] text-slate-400" aria-label="sin saldo">—</span>;
                        })}
                        <span className="flex cursor-help flex-col px-2" title={f.esperado_d} aria-label={`Woo esperado: ${f.esperado_d}`}>
                          <span className="text-[13px] font-bold tabular-nums text-slate-900">{n(f.esperado)}</span>
                          <span className="truncate text-[11px] leading-[14px] text-slate-600">
                            {f.esperado == null ? f.coincide_t : sumas || "—"}
                          </span>
                          {f.otras ? <span className="text-[11px] leading-[14px] text-slate-600">fuera de las 3: {conSigno(f.otras)}</span> : null}
                        </span>
                        <span className="flex flex-col px-2">
                          <span className="text-[13px] font-bold tabular-nums text-slate-900">{f.woo == null ? "sin número" : n(f.woo)}</span>
                          {f.woo_de && <span className="text-[11px] leading-[14px] text-slate-600">foto {horaCorta(f.woo_de, d.hoy)}</span>}
                        </span>
                        <span className={`flex min-h-11 cursor-help flex-col justify-center rounded-lg px-2 py-1 ${co.cls}`}
                          title={`${f.coincide_t}. ${f.esperado_d}`} aria-label={`${co.texto}. ${f.coincide_t}`}>
                          <span className="text-[13px] font-semibold leading-[18px]">{f.dif ? `${conSigno(f.dif)} ` : ""}{co.texto}</span>
                          {(f.coincide === "no_toca" || f.coincide === "por_copiar") && (
                            <span className="truncate text-[11px] leading-[15px]">{f.coincide_t}</span>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <p className="px-6 pb-2 pt-1 text-xs leading-[18px] text-slate-600">
                «Odoo» por bodega es libre = físico − reservado en sus ubicaciones internas, leído en vivo (caché de 10 min, y se
                relee si hay una pasada más nueva); «fuera de las 3» es lo que Odoo tiene hoy en otras bodegas, de esa misma lectura.
                «Woo esperado» es lo que stock_watch copia: el libre TOTAL de Odoo de su última pasada menos las vendidas sin orden.
                «Woo hoy» sale de la misma foto, y foto contra foto casi siempre coincide: lo que delata una escritura fallida, un
                freno o el modo solo registro es «Por copiar» —el Odoo de hoy ya no es el de la foto— que sigue ahí después de la
                próxima pasada. También queda «Por copiar» un SKU con una venta u orden posterior a la foto. «De más» o «de menos»
                con el Odoo de hoy igual al de la foto es lo que una pasada guardó sin copiar (pendientes sin medir). Las columnas
                «kubera» sólo aparecen para las bodegas de kubera con saldo. Toca un SKU para ver su libro y sus OV.
              </p>
            </section>
          </>
        )}
      </main>
      <LibroSku sku={libro} onCerrar={() => setLibro(null)}
        onTrazabilidad={(sku) => { setLibro(null); setTraza(sku); }} />
      <TrazabilidadSku sku={traza} onCerrar={() => setTraza(null)}
        onRastro={(sku, fin) => { setTraza(null); setRastro({ sku, fin }); }} />
      <RastroCambio sel={rastro} onCerrar={() => setRastro(null)} onIr={(sku, fin) => setRastro({ sku, fin })}
        onTrazabilidad={(sku) => { setRastro(null); setTraza(sku); }} />
      {error && d && (
        <div role="status" className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full bg-amber-50 px-4 py-2 text-[13px] text-amber-900 shadow-lg ring-1 ring-amber-200">
          <CircleX size={16} aria-hidden /> Se perdió la conexión; se muestra el último dato y se reintenta solo.
        </div>
      )}
    </div>
  );
}
