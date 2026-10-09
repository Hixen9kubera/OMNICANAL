"use client";

/**
 * /dashboard/devoluciones — las cajas que Mercado Libre regresa a NUESTRA bodega
 * y si ya entraron a Odoo. Una fila por devolución (Kubera y San Corpe) con su
 * camino según ML y su recepción en Odoo: la liga dura es la guía de ML que Bodega
 * anota en la nota de la recepción (o la orden de ML, o el botón «Devolver»); sin
 * ella queda la «probable por fecha» de la v0.628.0 y lo dice. Arriba la cadena
 * (a nuestra bodega → llegaron → en Odoo por guía, orden o «Devolver» → subieron el stock) y abajo
 * la bandeja, lo que falta primero.
 *
 * Solo una recepción a rack sube el stock que stock_watch copia a Woo; SCRAP no.
 * Lo arma `GET /api/fanout/devoluciones`, cada minuto (Odoo lleva caché de 10 min
 * en el backend; si no contesta, la bandeja sigue con lo que dice ML y lo avisa).
 * Tocar un SKU abre `DevolucionesSku`: cada subida de Odoo con las recepciones que
 * la hicieron. Solo lee; el dinero y las tasas viven en Análisis › Rentabilidad.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleX, Clock, Info, RotateCcw, Search, TriangleAlert } from "lucide-react";
import { ApiError, devolucionesFanout } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import BannerFanout, { ACCION_BANNER } from "@/components/fanout/BannerFanout";
import { Flecha, Nodo } from "@/components/fanout/CadenaFull";
import DevolucionesSku from "@/components/fanout/DevolucionesSku";
import FanoutPestanas from "@/components/fanout/FanoutPestanas";
import RastroCambio from "@/components/fanout/RastroCambio";
import TrazabilidadSku from "@/components/fanout/TrazabilidadSku";
import type { BandejaDevol, CuentaSel, FilaDevol, FiltroDevol } from "@/components/fanout/tipos";
import { CUENTA_CHIP, DEVOL_ML_CLS, GRUPO_DEVOL, NOMBRE_CUENTA, horaCorta } from "@/components/fanout/tipos";

const FILTROS: FiltroDevol[] = ["todas", "buscar", "otro", "atrasada", "odoo", "scrap", "camino", "noregresa"];
const PERIODOS = [14, 30, 60];
const CUENTAS: { id: CuentaSel; texto: string }[] = [
  { id: "ambas", texto: "Ambas" },
  { id: "BEKURA", texto: "Kubera" },
  { id: "SANCORFASHION", texto: "San Corpe" },
];
const COLUMNAS = "112px minmax(170px,196px) 92px 44px minmax(0,1.25fr) minmax(0,1.2fr) 168px";

const n = (v: number) => v.toLocaleString("es-MX");

function textoError(e: unknown): string {
  if (e instanceof ApiError) return e.detail ?? `HTTP ${e.status}`;
  return e instanceof Error ? e.message : "error";
}

/** El periodo con que abre el detalle de una caja: el menor que alcanza su llegada o su despacho. */
function periodoDe(f: FilaDevol): number {
  const d = f.dias ?? 0;
  return [14, 30, 60].find((p) => p >= d + 2) ?? 60;
}

/** Un recuadro de abajo de la cadena: lo que no sigue el camino de arriba. */
function Cuadro({ cifra, texto, tono }: { cifra: string; texto: string; tono: "ambar" | "gris" | "punteado" | "cielo" }) {
  const cls = {
    ambar: "border-amber-300 bg-amber-50 text-amber-900",
    gris: "border-slate-200 bg-slate-50 text-slate-700",
    punteado: "border-dashed border-slate-400 bg-slate-50 text-slate-700",
    cielo: "border-sky-200 bg-sky-50 text-sky-900",
  }[tono];
  return (
    <div className={`flex flex-col gap-0.5 rounded-xl border px-3.5 py-3 ${cls}`}>
      <span className="text-[22px] font-bold tabular-nums">{cifra}</span>
      <span className="text-xs leading-[17px]">{texto}</span>
    </div>
  );
}

export default function PaginaDevoluciones() {
  const [d, setD] = useState<BandejaDevol | null>(null);
  // De qué cuenta es la bandeja que se ve: mientras llega la otra, los textos no mienten.
  const [dCuenta, setDCuenta] = useState<CuentaSel>("ambas");
  const [error, setError] = useState<string | null>(null);
  const [dias, setDias] = useState(60);
  const [cuenta, setCuenta] = useState<CuentaSel>("ambas");
  const [filtro, setFiltro] = useState<FiltroDevol>("todas");
  const [busca, setBusca] = useState("");
  const [detalle, setDetalle] = useState<{ sku: string; dias: number } | null>(null);
  const [traza, setTraza] = useState<{ sku: string; dias: number } | null>(null);
  const [rastro, setRastro] = useState<{ sku: string; fin: string } | null>(null);
  const pedido = useRef<AbortController | null>(null);
  // El botón del SKU que abrió el cajón: al cerrarlo, el foco vuelve ahí (con 59 filas, quien
  // usa teclado no pierde su lugar).
  const abridor = useRef<HTMLElement | null>(null);
  const cerrarDetalle = () => {
    setDetalle(null);
    if (abridor.current?.isConnected) abridor.current.focus();
  };

  useEffect(() => {
    const f = new URLSearchParams(window.location.search).get("filtro");
    if (f && (FILTROS as string[]).includes(f)) setFiltro(f as FiltroDevol);
  }, []);

  const elegirFiltro = (id: FiltroDevol) => {
    setFiltro(id);
    try {
      const u = new URL(window.location.href);
      if (id === "todas") u.searchParams.delete("filtro");
      else u.searchParams.set("filtro", id);
      window.history.replaceState(null, "", u);
    } catch {
      // sin URL que tocar: el filtro igual se aplica
    }
  };

  const cargar = useCallback(async () => {
    // Cambiar periodo o cuenta cancela la lectura anterior: la lenta no pisa a la nueva.
    pedido.current?.abort();
    const c = new AbortController();
    pedido.current = c;
    try {
      const j = await devolucionesFanout(dias, cuenta === "ambas" ? null : cuenta, c.signal);
      if (c.signal.aborted) return;
      setD(j);
      setDCuenta(cuenta);
      setError(null);
    } catch (e) {
      if (c.signal.aborted) return;
      setError(textoError(e));
    }
  }, [dias, cuenta]);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 60_000);
    return () => {
      clearInterval(t);
      pedido.current?.abort();
    };
  }, [cargar]);

  const hoy = d ? d.ahora.slice(0, 10) : "";
  const conteo = useMemo(() => {
    const m = new Map<FiltroDevol, number>((d?.filtros ?? []).map((f) => [f.id, f.n]));
    return (id: FiltroDevol) =>
      m.get(id) ?? (d ? d.filas.filter((f) => id === "todas" || f.grupo === id).length : 0);
  }, [d]);
  const buscado = busca.trim().toUpperCase();
  const filas = useMemo(() => {
    if (!d) return [];
    return d.filas.filter((f) => (filtro === "todas" || f.grupo === filtro) && (!buscado || [
      f.sku, f.guia, f.id, f.odoo?.picking, f.odoo?.sku_recibido,
    ].some((x) => x && x.toUpperCase().includes(buscado))));
  }, [d, filtro, buscado]);

  const leyendo = !!d && (d.dias !== dias || dCuenta !== cuenta);
  const etiqueta = (id: FiltroDevol) => (id === "buscar" && d && !d.odoo_ok ? "Llegaron · sin verificar" : GRUPO_DEVOL[id].texto);
  const r = d?.resumen;
  const nombreCuenta = dCuenta === "ambas" ? "Kubera y San Corpe" : NOMBRE_CUENTA[dCuenta];
  const pctGuia = r && r.a_bodega ? Math.round((r.en_odoo_guia * 100) / r.a_bodega) : 0;
  const desglose = r ? `${n(r.por_guia ?? 0)} por guía · ${n(r.por_venta ?? 0)} por la venta` : "";

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-6 sm:px-6">
        <FanoutPestanas />
        <BannerFanout
          icono={<RotateCcw size={28} aria-hidden />}
          titulo="Devoluciones"
          texto="Las cajas que regresan a nuestra bodega y si ya entraron a Odoo. Solo una recepción en Odoo sube el stock que stock_watch copia a Woo y el fan-out reparte."
          acciones={
            <span className={ACCION_BANNER}>
              <Clock size={14} aria-hidden />
              {d
                ? `Corte de las ${d.ahora.slice(11, 16)} · se actualiza cada minuto · ${
                  d.odoo_ok ? `Odoo leído ${d.odoo_leido ? `a las ${horaCorta(d.odoo_leido, hoy)}` : "en este corte"}` : "Odoo no respondió"}`
                : "Leyendo devoluciones…"}
            </span>
          }
          cifra={r ? n(r.buscar) : "—"}
          cifraTexto={d && !d.odoo_ok ? "llegaron, sin verificar en Odoo" : "cajas por encontrar en Bodega"}
        />

        {error && !d && (
          <p className="rounded-2xl bg-white p-6 text-sm text-rose-800 shadow-sm">No se pudieron leer las devoluciones ({error}). Se reintenta en un minuto.</p>
        )}
        {!d && !error && (
          <p className="rounded-2xl bg-white p-6 text-sm text-slate-600 shadow-sm" role="status">Leyendo las devoluciones de ML y las recepciones de Odoo…</p>
        )}

        {d && r && (
          <>
            {d.odoo_ok ? (
              <p className="flex items-start gap-2.5 rounded-2xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-[13px] leading-5 text-indigo-950">
                <Info size={18} className="mt-px shrink-0 text-indigo-700" aria-hidden />
                <span>
                  Cada caja se liga con su recepción en Odoo por la guía de ML que Bodega anota en la nota de la recepción, o por
                  la venta (la orden de ML o el botón «Devolver»): hoy <b>{n(r.en_odoo_guia)} de las {n(r.a_bodega)}</b> ({pctGuia} %:{" "}
                  {desglose}). Sin ninguna, la liga queda como «probable por fecha» y lo dice.
                </span>
              </p>
            ) : (
              <p role="status" className="flex items-start gap-2.5 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] leading-5 text-amber-950">
                <TriangleAlert size={18} className="mt-px shrink-0 text-amber-700" aria-hidden />
                <span>
                  <b>Odoo no respondió.</b> Se muestra solo lo que dice Mercado Libre: las cajas que ML da por entregadas quedan en
                  «{etiqueta("buscar")}» porque no se pudo buscar su recepción, y las cifras de Odoo no se cuentan.
                  {d.odoo_leido ? ` Última lectura buena: ${horaCorta(d.odoo_leido, hoy)}.` : ""} Se reintenta cada minuto.
                  {d.odoo_error && <span className="block text-xs text-amber-900">{d.odoo_error}</span>}
                </span>
              </p>
            )}
            {/* Odoo falló pero hay una lectura buena de menos de 2 h: se usa, y se dice de cuándo es. */}
            {d.odoo_ok && d.odoo_error && (
              <p role="status" className="flex items-start gap-2.5 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] leading-5 text-amber-950">
                <TriangleAlert size={18} className="mt-px shrink-0 text-amber-700" aria-hidden />
                <span>{d.odoo_error}. Las cifras de Odoo pueden ir atrasadas; se reintenta cada minuto.</span>
              </p>
            )}

            <section aria-labelledby="t-devol-cadena" className="flex flex-col gap-4 rounded-2xl bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 id="t-devol-cadena" className="text-[17px] font-bold text-slate-900">Cajas de ML a nuestra bodega · últimos {d.dias} días</h2>
                  <p className="text-xs text-slate-600">
                    {nombreCuenta} · corte de las {d.ahora.slice(11, 16)} (CDMX){leyendo ? " · leyendo el periodo nuevo…" : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div role="group" aria-label="Cuenta de Mercado Libre" className="flex gap-1 rounded-xl bg-white p-1 ring-1 ring-slate-200">
                    {CUENTAS.map((o) => (
                      <button key={o.id} type="button" aria-pressed={cuenta === o.id} onClick={() => setCuenta(o.id)}
                        className={`h-9 rounded-lg px-3.5 text-sm font-semibold ${cuenta === o.id ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
                        {o.texto}
                      </button>
                    ))}
                  </div>
                  <div role="group" aria-label="Periodo" className="flex items-center gap-1.5">
                    {PERIODOS.map((p) => (
                      <button key={p} type="button" aria-pressed={dias === p} onClick={() => setDias(p)}
                        className={`h-8 rounded-full border px-3 text-xs font-semibold ${
                          dias === p ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                        {p} días
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-stretch gap-2">
                <Nodo eyebrow="Mercado Libre" titulo="A nuestra bodega" cifra={n(r.a_bodega)} unidad="devoluciones"
                  lineas={[`${n(r.piezas)} ${r.piezas === 1 ? "pieza" : "piezas"} · abiertas o llegadas en ${d.dias} días`]} />
                <Flecha />
                <Nodo eyebrow="ML u Odoo" titulo="Llegaron" cifra={n(r.llegaron)} unidad="cajas"
                  lineas={[d.odoo_ok ? `${n(r.llegaron_ml)} según ML y ${n(r.solo_odoo)} que solo Odoo ve` : "según ML; sin Odoo no se ven las que solo él registra"]} />
                <Flecha />
                <Nodo destacado eyebrow="Odoo · guía, orden o «Devolver»" titulo="En Odoo" cifra={d.odoo_ok ? n(r.en_odoo_guia) : "—"}
                  unidad="cajas" lineas={[d.odoo_ok ? desglose : "Odoo no respondió"]} />
                <Flecha />
                <Nodo eyebrow="Odoo → Woo" titulo="Subieron el stock" cifra={d.odoo_ok ? n(r.subieron) : "—"} unidad="a rack"
                  lineas={[d.odoo_ok ? `${n(r.scrap)} a SCRAP · ${n(r.otro_sku)} con otro SKU` : "Odoo no respondió"]} />
              </div>
              <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
                <Cuadro tono="ambar" cifra={n(r.buscar)}
                  texto={d.odoo_ok ? "llegaron sin recepción con su guía en Odoo" : "llegaron según ML, sin verificar en Odoo"} />
                <Cuadro tono="gris" cifra={n(r.en_camino)} texto="en camino o por enviar" />
                <Cuadro tono="punteado" cifra={n(r.no_regresan)} texto="no regresan (vencidas, canceladas o no entregadas)" />
                <Cuadro tono="cielo" cifra={n(r.a_full)}
                  texto={`abiertas en ${d.dias} días van a la bodega de ML: no tocan Odoo${
                    r.sin_destino ? ` · ${n(r.sin_destino)} más sin destino en ML` : ""}`} />
              </div>
            </section>

            <section aria-labelledby="t-devol-cajas" className="flex flex-col rounded-2xl bg-white shadow-sm">
              <div className="flex flex-col gap-3 px-6 pb-3 pt-5">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="t-devol-cajas" className="text-[17px] font-bold text-slate-900">Caja por caja</h2>
                  <span className="text-xs text-slate-600">Lo que falta primero. El SKU abre sus subidas de Odoo.</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filtrar cajas">
                    {FILTROS.map((id) => (
                      <button key={id} type="button" aria-pressed={filtro === id} onClick={() => elegirFiltro(id)}
                        className={`h-[34px] rounded-full border px-3 text-[13px] font-semibold ${
                          filtro === id ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                        {etiqueta(id)} · {n(conteo(id))}
                      </button>
                    ))}
                  </div>
                  <label className="flex h-[34px] items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 text-[13px] text-slate-700 focus-within:border-indigo-500 sm:ml-auto">
                    <Search size={14} className="text-slate-500" aria-hidden />
                    <span className="sr-only">Buscar por SKU, guía, devolución o recepción</span>
                    <input type="search" value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="SKU o guía"
                      className="w-36 bg-transparent font-mono text-xs outline-none placeholder:font-sans placeholder:text-slate-500" />
                  </label>
                </div>
                <p className="text-xs text-slate-600" aria-live="polite">
                  {n(filas.length)} de {n(d.filas.length)} {d.filas.length === 1 ? "caja" : "cajas"}
                  {filtro !== "todas" ? ` · ${etiqueta(filtro)}` : ""}{buscado ? ` · «${buscado}»` : ""}
                </p>
              </div>

              {/* Enfocable: con teclado también se desplaza a los lados (en 375 px la tabla mide 1180). */}
              <div className="overflow-x-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
                tabIndex={0} role="region" aria-label="Caja por caja (se desplaza a los lados)">
                <div role="table" aria-labelledby="t-devol-cajas" className="flex min-w-[1180px] flex-col">
                  <div role="row" className="grid gap-3 border-y border-slate-200 px-6 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500"
                    style={{ gridTemplateColumns: COLUMNAS }}>
                    <span role="columnheader">Abierta</span>
                    <span role="columnheader">SKU</span>
                    <span role="columnheader">Cuenta</span>
                    <span role="columnheader">Pzs</span>
                    <span role="columnheader">Camino según ML</span>
                    <span role="columnheader">En Odoo</span>
                    <span role="columnheader">Qué hacer</span>
                  </div>
                  {filas.length === 0 && (
                    <div role="row">
                      <p role="cell" className="px-6 py-6 text-[13px] text-slate-600">
                        {buscado ? `Ninguna caja con «${buscado}» en este filtro.` : "No hay cajas con este filtro en el periodo."}
                      </p>
                    </div>
                  )}
                  {filas.map((f) => {
                    // Un grupo que el backend estrene no tumba la tabla: se pinta neutro.
                    const g = GRUPO_DEVOL[f.grupo] ?? GRUPO_DEVOL.todas;
                    const probable = f.probable && !f.odoo_sub.includes(f.probable.texto) ? f.probable : null;
                    return (
                      <div key={`${f.cuenta}-${f.id}-${f.sku}`} role="row"
                        className="grid items-start gap-3 border-b border-slate-100 px-6 py-2.5 text-[13px] leading-[19px]"
                        style={{ gridTemplateColumns: COLUMNAS }}>
                        <span role="cell" className="flex flex-col">
                          <span className="tabular-nums text-slate-800">{horaCorta(f.abierta, hoy)}</span>
                          <span className="font-mono text-[11px] text-slate-500">{f.id}</span>
                        </span>
                        <span role="cell" className="flex min-w-0 flex-col">
                          <button type="button"
                            onClick={(e) => { abridor.current = e.currentTarget; setDetalle({ sku: f.sku, dias: periodoDe(f) }); }}
                            title="Ver sus subidas de Odoo y las recepciones que las hicieron"
                            className="break-all text-left font-mono text-xs font-semibold leading-[19px] text-indigo-800 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                            {f.sku}
                          </button>
                          {f.titulo && <span className="truncate text-xs text-slate-600" title={f.titulo}>{f.titulo}</span>}
                        </span>
                        <span role="cell">
                          <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${CUENTA_CHIP[f.cuenta] ?? "bg-slate-100 text-slate-700"}`}>
                            {NOMBRE_CUENTA[f.cuenta] ?? f.cuenta}
                          </span>
                        </span>
                        <span role="cell" className="tabular-nums text-slate-800">{n(f.piezas)}</span>
                        <span role="cell" className="flex flex-col items-start gap-[3px]">
                          <span className={`rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${DEVOL_ML_CLS[f.estado_ml] ?? "bg-slate-100 text-slate-700"}`}>
                            {f.estado_txt}
                          </span>
                          <span className="text-slate-600" title={f.llegada ? `Llegada ${f.llegada}${f.llegada_de ? ` (${f.llegada_de})` : ""}` : undefined}>
                            {f.pasos_txt}
                          </span>
                          {/* De dónde sale el día de llegada, a la vista: «en vivo» no se dice. */}
                          {f.llegada_de && f.llegada_de !== "en vivo" && (
                            <span className="text-[11px] text-slate-500">día de llegada: {f.llegada_de}</span>
                          )}
                          {f.guia && !f.odoo_sub.includes(f.guia) && <span className="font-mono text-[11px] text-slate-500">Envío {f.guia}</span>}
                        </span>
                        <span role="cell" className="flex flex-col gap-px">
                          <span className={`font-semibold ${g.odoo}`}>{f.odoo_txt}</span>
                          {f.odoo_sub && <span className="text-xs text-slate-600">{f.odoo_sub}</span>}
                          {probable && (
                            <span className="text-xs text-slate-600">Probable por fecha · {horaCorta(probable.hora, hoy)} · {probable.texto}</span>
                          )}
                        </span>
                        <span role="cell" className={`font-semibold ${g.accion}`}
                          title={f.dias != null ? `${f.dias} ${f.dias === 1 ? "día" : "días"} desde ${f.llegada ? "la llegada" : "el despacho"}` : undefined}>
                          {f.accion}
                          {f.dias != null && (
                            <span className="sr-only">{` · ${f.dias} ${f.dias === 1 ? "día" : "días"} desde ${f.llegada ? "la llegada" : "el despacho"}`}</span>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-col gap-1 px-6 pb-4 pt-3 text-xs leading-[18px] text-slate-600">
                <p>
                  Horas de CDMX. «En Odoo» sale de la nota de la recepción (la guía de ML que anota Bodega) o de la venta
                  (la orden de ML o el botón «Devolver»); SCRAP no sube el stock.
                  Las devoluciones a la bodega de ML se ven en{" "}
                  <Link href="/dashboard/full" className="font-semibold text-indigo-800 underline-offset-2 hover:underline">FULL</Link>; el dinero y
                  las tasas, en{" "}
                  <Link href="/analisis/rentabilidad" className="font-semibold text-indigo-800 underline-offset-2 hover:underline">Análisis › Rentabilidad</Link>.
                </p>
                {d.odoo_ok && d.cobertura.recepciones > 0 && (
                  <p>
                    En {d.dias} días Odoo recibió {n(d.cobertura.recepciones)} recepciones de «DEVOLUCIONES»
                    {d.cobertura.retiros_full ? ` (${n(d.cobertura.retiros_full)} son retiros de FULL: cajas de la bodega de ML, sin guía)` : ""} con{" "}
                    {n(d.cobertura.guias_en_recepciones)} guías anotadas; {n(d.cobertura.guias_sin_kubera)} no están en las
                    devoluciones que captura kubera.
                  </p>
                )}
              </div>
            </section>
          </>
        )}
      </main>
      <DevolucionesSku key={detalle ? `${detalle.sku}·${detalle.dias}` : "cerrado"} sku={detalle?.sku ?? null}
        diasInicial={detalle?.dias ?? 14} hoy={hoy}
        cobertura={d?.odoo_ok ? { c: d.cobertura, dias: d.dias } : null}
        onCerrar={cerrarDetalle}
        onTrazabilidad={(sku, dd) => { setDetalle(null); setTraza({ sku, dias: dd }); }} />
      <TrazabilidadSku sku={traza?.sku ?? null} carrilInicial="devoluciones" diasInicial={traza?.dias ?? dias}
        onCerrar={() => setTraza(null)}
        onRastro={(sku, fin) => { setTraza(null); setRastro({ sku, fin }); }} />
      <RastroCambio sel={rastro} onCerrar={() => setRastro(null)} onIr={(sku, fin) => setRastro({ sku, fin })}
        onTrazabilidad={(sku) => { setRastro(null); setTraza({ sku, dias }); }} />
      {error && d && (
        <div role="status" className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full bg-amber-50 px-4 py-2 text-[13px] text-amber-900 shadow-lg ring-1 ring-amber-200">
          <CircleX size={16} aria-hidden /> Se perdió la conexión; se muestra el último dato y se reintenta solo.
        </div>
      )}
    </div>
  );
}
