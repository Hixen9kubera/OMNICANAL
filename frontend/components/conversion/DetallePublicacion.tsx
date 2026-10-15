"use client";

/**
 * Detalle de una publicación en Análisis › Conversión — las burbujas del
 * Monitor de precios FULL (sesión COMPETENCIA, 14-oct-2026), ahora para
 * cualquier publicación de Mercado Libre:
 *
 *   arriba  · cada venta: una burbuja por (día, precio, pagada/cancelada);
 *             radio = 4 + 3·√piezas, hueca y gris si se canceló
 *   en medio· visitas por día
 *   abajo   · conversión por semana (piezas pagadas ÷ visitas) contra la meta
 *
 * Las visitas por día salen de kubera si la 0062 (`market.item_mes`) está
 * aplicada; si no, el backend se las pide a ML en el momento (con 6 h de
 * caché). Si ML no contesta, se pintan las ventas solas y se dice por qué.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Loader2, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import {
  ALERTAS, CUADRANTES, type Reglas, type RespDetalle, fechaCorta, metricas, num, pct, pesos, urlML,
} from "@/lib/conversion";

interface Props {
  listingId: string;
  reglas: Reglas;
  onCerrar: () => void;
}

const DIA = 864e5;

export default function DetallePublicacion({ listingId, reglas, onCerrar }: Props) {
  const [dias, setDias] = useState(90);
  const [datos, setDatos] = useState<RespDetalle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cerrarRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { cerrarRef.current?.focus(); }, []);
  useEffect(() => {
    let vivo = true;
    setDatos(null); setError(null);
    fetchSesion(`${API_BASE}/api/analisis/conversion/${encodeURIComponent(listingId)}?dias=${dias}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((d: RespDetalle) => { if (vivo) setDatos(d); })
      .catch((e: Error) => { if (vivo) setError(e.message); });
    return () => { vivo = false; };
  }, [listingId, dias]);

  const g = useMemo(() => (datos ? armarGrafica(datos, reglas) : null), [datos, reglas]);
  const p = datos?.publicacion;
  const m = p ? metricas(p, reglas) : null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/40" onClick={onCerrar}>
      <div role="dialog" aria-modal="true" aria-labelledby="det-titulo"
        className="flex h-full w-full max-w-[1180px] flex-col overflow-y-auto bg-slate-50 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onCerrar(); }}>
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
          <div className="min-w-0">
            <h2 id="det-titulo" className="text-lg font-bold leading-snug text-slate-900">{p?.titulo ?? listingId}</h2>
            {p && (
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                <span className={`rounded-full px-2.5 py-0.5 font-semibold ${p.situacion === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>
                  {p.situacion === "active" ? "Activa" : p.situacion ?? "—"}
                </span>
                {p.full && <span className="rounded-full bg-indigo-100 px-2.5 py-0.5 font-semibold text-indigo-800">FULL</span>}
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-slate-700">{p.cuenta}</span>
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-slate-700">{p.sku}</span>
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-slate-700">{p.listing_id}</span>
                {p.categoria && <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-slate-700">{p.categoria_raiz} › {p.categoria}</span>}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <a href={urlML(listingId)} target="_blank" rel="noreferrer"
              className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 text-[13px] font-semibold text-indigo-700 hover:bg-slate-50">
              Abrir en Mercado Libre <ExternalLink size={14} />
            </a>
            <button ref={cerrarRef} type="button" onClick={onCerrar} aria-label="Cerrar el detalle"
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-600 hover:bg-slate-50">
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-4 p-6">
          {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">No se pudo leer el detalle: {error}</div>}
          {!datos && !error && (
            <div className="flex items-center gap-2 py-16 text-sm text-slate-500"><Loader2 size={16} className="animate-spin" /> Leyendo ventas y visitas…</div>
          )}

          {p && m && g && (
            <>
              <section aria-label="Resumen" className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
                <Kpi titulo="Visitas 30 d" valor={num(p.visitas)} sub={p.visitas_ant != null ? `antes ${num(p.visitas_ant)} (${fechaCorta(p.visitas_ant_at)})` : "sin medición anterior"} />
                <Kpi titulo="Piezas 30 d" valor={num(p.piezas)} sub={p.piezas_ant != null ? `antes ${num(p.piezas_ant)}` : `${num(p.canceladas)} canceladas`} />
                <Kpi titulo="Conversión" valor={pct(m.conv)} sub={`antes ${pct(m.convAnt)} · meta ${pct(reglas.convMeta)}`}
                  alerta={m.conv != null && m.conv < reglas.convMeta} />
                <Kpi titulo="Precio" valor={pesos(p.precio)} sub={p.precio_lista && p.precio_lista !== p.precio ? `lista ${pesos(p.precio_lista)}` : "precio de lista"} />
                <Kpi titulo={p.full ? "Stock FULL" : "Stock"} valor={`${num(p.stock)} pzs`}
                  sub={Number.isFinite(m.dias) ? `alcanza ${Math.round(m.dias)} días` : "sin ventas recientes"} />
              </section>

              <div className="flex flex-wrap items-start gap-4">
                <section aria-label="Ventas y visitas día por día" className="min-w-0 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" style={{ flex: "999 1 600px" }}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-[15px] font-semibold text-slate-900">Cada venta, las visitas y la conversión</h3>
                    <div className="flex gap-1">
                      {[30, 90, 150].map((d) => (
                        <button key={d} type="button" aria-pressed={dias === d} onClick={() => setDias(d)}
                          className={`min-h-[36px] rounded-lg border px-3 text-[13px] ${dias === d ? "border-indigo-200 bg-indigo-50 font-semibold text-indigo-800" : "border-slate-200 bg-white text-slate-600"}`}>
                          {d} d
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-700">
                    <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full bg-indigo-600" />venta pagada · el número son las piezas</span>
                    <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full border-[1.5px] border-slate-500 bg-white" />cancelada</span>
                    <span className="flex items-center gap-1.5"><span className="h-2.5 w-3 rounded-sm bg-indigo-200" />visitas del día</span>
                  </div>

                  <div className="mt-5 flex flex-col gap-6" style={{ margin: "20px 76px 0 56px" }}>
                    <div className="relative h-[230px]">
                      <span className="absolute -top-5 text-xs font-semibold text-slate-700" style={{ left: -56 }}>Precio de cada venta</span>
                      {g.lineasPrecio.map((l) => (
                        <div key={l.precio}>
                          <div className="absolute inset-x-0 border-t border-slate-100" style={{ top: `${l.top}%` }} />
                          <span className="absolute w-12 text-right text-[11px] text-slate-500" style={{ left: -56, top: `calc(${l.top}% - 8px)` }}>{pesos(l.precio)}</span>
                        </div>
                      ))}
                      {g.precioHoy != null && (
                        <>
                          <div className="absolute inset-x-0 border-t-2 border-slate-900" style={{ top: `${g.precioHoy}%` }} />
                          <span className="absolute w-[72px] text-[11px] font-bold text-slate-900" style={{ right: -76, top: `calc(${g.precioHoy}% - 8px)` }}>hoy {pesos(p.precio)}</span>
                        </>
                      )}
                      {g.burbujas.map((b) => (
                        <div key={b.clave} title={b.titulo}
                          className="absolute flex items-center justify-center rounded-full text-[10px] font-semibold text-white"
                          style={{
                            left: `${b.x}%`, top: `${b.y}%`, width: 2 * b.r, height: 2 * b.r, marginLeft: -b.r, marginTop: -b.r,
                            background: b.cancelada ? "#ffffff" : "rgba(79,70,229,.85)",
                            border: b.cancelada ? "1.5px solid #64748b" : "1.5px solid #ffffff",
                          }}>
                          {!b.cancelada && b.piezas > 1 ? b.piezas : ""}
                        </div>
                      ))}
                      {!g.burbujas.length && <p className="absolute inset-0 flex items-center justify-center text-sm text-slate-500">Sin ventas en estos {dias} días.</p>}
                    </div>

                    {g.barras ? (
                      <>
                        <div className="relative h-[90px]">
                          <span className="absolute -top-5 text-xs font-semibold text-slate-700" style={{ left: -56 }}>Visitas por día</span>
                          <div className="absolute inset-0 flex items-end gap-px border-b border-slate-200">
                            {g.barras.map((b) => (
                              <div key={b.i} title={b.titulo} className="min-w-0 flex-1 rounded-t-[1px] bg-indigo-200"
                                style={{ height: `${b.alto}%`, opacity: b.sinDato ? 0 : 1 }} />
                            ))}
                          </div>
                          <span className="absolute w-12 text-right text-[11px] text-slate-500" style={{ left: -56, top: -2 }}>{num(g.maxVisitas)}</span>
                        </div>
                        <div className="relative h-[90px]">
                          <span className="absolute -top-5 text-xs font-semibold text-slate-700" style={{ left: -56 }}>Conversión por semana</span>
                          <div className="absolute inset-x-0 border-t-[1.5px] border-dashed border-indigo-500" style={{ top: `${g.metaTop}%` }} />
                          <span className="absolute w-[72px] text-[11px] font-semibold text-indigo-700" style={{ right: -76, top: `calc(${g.metaTop}% - 8px)` }}>meta {pct(reglas.convMeta)}</span>
                          <svg aria-hidden="true" viewBox="0 0 1000 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
                            <polyline points={g.semanas.map((s) => `${s.x * 10},${s.y}`).join(" ")} fill="none" stroke="#0f172a" strokeWidth={2} vectorEffect="non-scaling-stroke" />
                          </svg>
                          {g.semanas.map((s) => (
                            <div key={s.x}>
                              <div className="absolute h-2 w-2 rounded-full border-[1.5px] border-white"
                                style={{ left: `${s.x}%`, top: `${s.y}%`, marginLeft: -4, marginTop: -4, background: s.ok ? "#4f46e5" : "#ea580c" }} />
                              <span className={`absolute w-12 text-center text-[10px] font-semibold ${s.ok ? "text-indigo-800" : "text-orange-800"}`}
                                style={{ left: `calc(${s.x}% - 24px)`, top: `calc(${s.y}% - 20px)` }}>{pct(s.conv)}</span>
                            </div>
                          ))}
                          <div className="absolute inset-x-0 bottom-0 border-t border-slate-200" />
                        </div>
                      </>
                    ) : (
                      <p className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-[13px] text-slate-600">
                        {datos.visitas_aviso ?? "Sin visitas por día."} Las ventas de arriba sí son completas.
                      </p>
                    )}

                    <div className="relative h-4">
                      {g.fechas.map((f) => (
                        <span key={f.x} className="absolute w-16 text-center text-[11px] text-slate-500" style={{ left: `calc(${f.x}% - 32px)` }}>{f.texto}</span>
                      ))}
                    </div>
                  </div>
                  {datos.visitas_fuente && (
                    <p className="mt-4 text-[11px] text-slate-500">
                      Visitas por día: {datos.visitas_fuente === "kubera" ? "guardadas en kubera" : "pedidas a Mercado Libre al abrir (se guardan 6 h)"}.
                    </p>
                  )}
                </section>

                <aside aria-label="Por qué está en alerta" className="flex min-w-0 flex-col gap-3" style={{ flex: "1 1 300px" }}>
                  <div className={`rounded-2xl border p-4 ${m.alertas.length ? "border-orange-200 bg-orange-50" : "border-slate-200 bg-white"}`}>
                    <h3 className={`text-[15px] font-bold ${m.alertas.length ? "text-orange-900" : "text-slate-900"}`}>
                      {m.alertas.length ? "Por qué está en alerta" : "Sin alertas"}
                    </h3>
                    <span className="mt-2 inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold text-white"
                      style={{ background: m.cuadrante === "gan" ? "#4338ca" : m.cuadrante === "traf" ? "#c2410c" : m.cuadrante === "joya" ? "#0f766e" : "#64748b" }}>
                      {CUADRANTES[m.cuadrante].nombre}
                    </span>
                    <ul className="mt-3 flex flex-col gap-2">
                      {m.alertas.map((a) => (
                        <li key={a.k} className="text-[13px] leading-snug text-orange-950">
                          <span className="font-semibold">{a.texto}.</span> {ALERTAS[a.k].revisar}
                        </li>
                      ))}
                      {!m.alertas.length && <li className="text-[13px] text-slate-600">{CUADRANTES[m.cuadrante].hacer}</li>}
                    </ul>
                  </div>
                  <div className="rounded-2xl border border-slate-200 bg-white p-4">
                    <h3 className="text-[15px] font-semibold text-slate-900">La publicación</h3>
                    <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[13px]">
                      <dt className="text-slate-500">Calidad en ML</dt>
                      <dd className="text-slate-900">{datos.calidad?.valor != null ? `${num(datos.calidad.valor)} de 100` : "sin medir"}</dd>
                      <dt className="text-slate-500">Ingreso 30 d</dt><dd className="text-slate-900">{pesos(p.ingreso)}</dd>
                      <dt className="text-slate-500">Canceladas 30 d</dt><dd className="text-slate-900">{num(p.canceladas)} pzs</dd>
                      <dt className="text-slate-500">Visitas medidas</dt><dd className="text-slate-900">{fechaCorta(p.visitas_at)}</dd>
                    </dl>
                  </div>
                </aside>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Kpi({ titulo, valor, sub, alerta }: { titulo: string; valor: string; sub: string; alerta?: boolean }) {
  return (
    <div className={`rounded-2xl border px-4 py-3 ${alerta ? "border-orange-200 bg-orange-50" : "border-slate-200 bg-white"}`}>
      <div className={`text-xs font-semibold ${alerta ? "text-orange-800" : "text-slate-600"}`}>{titulo}</div>
      <div className={`mt-0.5 text-[22px] font-bold ${alerta ? "text-orange-950" : "text-slate-900"}`}>{valor}</div>
      <div className={`text-xs ${alerta ? "text-orange-800" : "text-slate-500"}`}>{sub}</div>
    </div>
  );
}

function armarGrafica(d: RespDetalle, R: Reglas) {
  const d0 = new Date(`${d.desde}T12:00:00`).getTime();
  const n = d.dias;
  const idx = (dia: string) => Math.round((new Date(`${dia}T12:00:00`).getTime() - d0) / DIA);
  const X = (i: number) => ((i + 0.5) / (n + 1)) * 100;
  const etiqueta = (i: number) => new Date(d0 + i * DIA).toLocaleDateString("es-MX", { day: "numeric", month: "short" });

  // Precio: escala con todas las ventas y el precio de hoy, con aire arriba y abajo.
  const precios = [...d.ventas.map((v) => v.precio ?? 0), d.publicacion.precio ?? 0].filter((x) => x > 0);
  let pMin = precios.length ? Math.min(...precios) * 0.94 : 0;
  let pMax = precios.length ? Math.max(...precios) * 1.05 : 1;
  if (pMax - pMin < 2) { pMin -= 1; pMax += 1; }
  const Y = (precio: number) => ((pMax - precio) / (pMax - pMin)) * 100;
  const burbujas = d.ventas.filter((v) => v.precio != null && v.piezas > 0).map((v) => ({
    clave: `${v.dia}|${v.precio}|${v.cancelada}`, x: X(idx(v.dia)), y: Y(v.precio as number),
    r: 4 + 3 * Math.sqrt(v.piezas), piezas: v.piezas, cancelada: v.cancelada,
    titulo: `${fechaCorta(v.dia)}: ${v.piezas} pzs a ${pesos(v.precio)}${v.cancelada ? " (cancelada)" : ""}`,
  }));
  const lineasPrecio = [0.2, 0.5, 0.8].map((f) => {
    const precio = pMin + (pMax - pMin) * (1 - f);
    return { precio, top: Y(precio) };
  });

  const fechas = Array.from({ length: 6 }, (_, k) => Math.round((k * (n - 1)) / 5)).map((i) => ({ x: X(i), texto: etiqueta(i) }));
  const base = { burbujas, lineasPrecio, fechas, precioHoy: d.publicacion.precio ? Y(d.publicacion.precio) : null };
  if (!d.visitas || !d.visitas.length) return { ...base, barras: null, semanas: [], maxVisitas: 0, metaTop: 0 };

  const vis = new Array<number | null>(n + 1).fill(null);
  d.visitas.forEach((v) => { const i = idx(v.fecha); if (i >= 0 && i <= n) vis[i] = v.visitas; });
  const pagadas = new Array<number>(n + 1).fill(0);
  d.ventas.forEach((v) => { const i = idx(v.dia); if (!v.cancelada && i >= 0 && i <= n) pagadas[i] += v.piezas; });
  const maxVisitas = Math.max(1, ...vis.map((v) => v ?? 0));
  const barras = vis.map((v, i) => ({
    i, alto: ((v ?? 0) / maxVisitas) * 100, sinDato: v == null,
    titulo: `${etiqueta(i)}: ${v == null ? "sin dato" : `${num(v)} visitas`}`,
  }));

  // Semanas de 7 días contadas hacia atrás desde el último día con visitas, para
  // que la semana más reciente esté completa.
  const ultimo = vis.reduce<number>((u, v, i) => (v != null ? i : u), -1);
  const semanas: { x: number; y: number; conv: number; ok: boolean }[] = [];
  const convs: { mid: number; conv: number }[] = [];
  for (let fin = ultimo; fin - 6 >= 0; fin -= 7) {
    let v = 0, u = 0;
    for (let i = fin - 6; i <= fin; i++) { v += vis[i] ?? 0; u += pagadas[i]; }
    if (v > 0) convs.unshift({ mid: fin - 3, conv: u / v });
  }
  const cMax = Math.max(R.convMeta * 1.6, ...convs.map((c) => c.conv)) * 1.1;
  convs.forEach((c) => semanas.push({ x: X(c.mid), y: 100 - (c.conv / cMax) * 88, conv: c.conv, ok: c.conv >= R.convMeta }));
  return { ...base, barras, semanas, maxVisitas, metaTop: 100 - (R.convMeta / cMax) * 88 };
}
