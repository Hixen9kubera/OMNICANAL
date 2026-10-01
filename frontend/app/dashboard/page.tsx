"use client";

/**
 * /dashboard — Sincronización de inventario EN VIVO (Operaciones › Fan-out).
 *
 * Responde dos preguntas, en este orden:
 *   1. ¿Mi stock llega a los canales?  → el veredicto y las tarjetas de canal.
 *   2. ¿Qué se está moviendo ahora?    → la cadena Odoo → stock_watch → Woo →
 *      fan-out → canales y el horario de cambios, que se llena solo.
 * El detalle vive a un clic: la matriz SKU × canal (/dashboard/matriz) y el
 * rastro de cada cambio (panel lateral al tocar una fila).
 *
 * Sondea `GET /api/fanout/vivo?desde_id=` cada 4 s: los cambios nuevos llegan
 * frescos y los agregados salen de una caché de 20 s en el backend. Solo lee.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, Copy, Info, XCircle } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import BannerFanout, { ACCION_BANNER } from "@/components/fanout/BannerFanout";
import CadenaViva from "@/components/fanout/CadenaViva";
import FanoutPestanas from "@/components/fanout/FanoutPestanas";
import HorarioTrenes from "@/components/fanout/HorarioTrenes";
import RastroCambio from "@/components/fanout/RastroCambio";
import PulsoCanales from "@/components/fanout/PulsoCanales";
import SerieDias from "@/components/fanout/SerieDias";
import TrazabilidadSku from "@/components/fanout/TrazabilidadSku";
import type { Atender, Evento, Vivo } from "@/components/fanout/tipos";

const SONDEO_MS = 4000;
const clave = (e: { sku: string; fin: string }) => `${e.sku}|${e.fin}`;

const NIVEL: Record<Atender["nivel"], { texto: string; cls: string; fondo: string }> = {
  urgente: { texto: "Urgente", cls: "bg-rose-600 text-white", fondo: "border-rose-200 bg-rose-50/50" },
  hoy: { texto: "Hoy", cls: "bg-rose-50 text-rose-800", fondo: "border-slate-200" },
  semana: { texto: "Esta semana", cls: "bg-amber-50 text-amber-800", fondo: "border-slate-200" },
  despues: { texto: "Cuando se pueda", cls: "bg-slate-100 text-slate-700", fondo: "border-slate-200" },
};

interface TipoObservado {
  tipo: string;
  n: number;
  efecto_declarado: string | null;
  ejemplo: { sku: string; cuenta: string; woo: string; detalle: string; ts: string } | null;
}
interface Observacion {
  modo_solo_registro: boolean;
  vigilante_encendido: boolean;
  eventos: number;
  tipos_vistos: TipoObservado[];
  TIPOS_DESCONOCIDOS: TipoObservado[];
}

function ObservacionFull({ abierto }: { abierto: boolean }) {
  const [obs, setObs] = useState<Observacion | null>(null);
  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    const cargar = async () => {
      try {
        const r = await fetchSesion(`${API_BASE}/api/fanout/full/observacion?horas=24`, { cache: "no-store" });
        if (r.ok && vivo) setObs(await r.json());
      } catch { /* la sección es secundaria: si falla, se queda como estaba */ }
    };
    void cargar();
    const t = setInterval(() => void cargar(), 60_000);
    return () => { vivo = false; clearInterval(t); };
  }, [abierto]);
  if (!obs) return <p className="px-5 pb-5 text-sm text-slate-500">Leyendo los movimientos de bodega…</p>;
  return (
    <div className="flex flex-col gap-3 px-5 pb-5">
      <p className="text-[13px] text-slate-600">
        {obs.eventos.toLocaleString("es-MX")} movimientos en 24 h · vigilante {obs.vigilante_encendido ? "encendido" : "apagado"}
        {obs.modo_solo_registro ? " · solo registra, no toca inventario" : ""}
      </p>
      {obs.TIPOS_DESCONOCIDOS.length > 0 && (
        <p className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span>No encender la escritura todavía: Mercado Libre mandó tipos sin regla ({obs.TIPOS_DESCONOCIDOS.map((t) => t.tipo).join(", ")}).</span>
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="text-[11px] uppercase tracking-wide text-slate-500">
            <tr><th className="py-1.5 pr-3 font-semibold">Tipo (Mercado Libre)</th><th className="py-1.5 pr-3 font-semibold">Veces</th>
              <th className="py-1.5 pr-3 font-semibold">Qué haría con Woo</th><th className="py-1.5 font-semibold">Último ejemplo</th></tr>
          </thead>
          <tbody>
            {obs.tipos_vistos.map((t) => (
              <tr key={t.tipo} className="border-t border-slate-100">
                <td className="py-1.5 pr-3 font-mono text-slate-800">{t.tipo}</td>
                <td className="py-1.5 pr-3 text-slate-700">{t.n}</td>
                <td className={`py-1.5 pr-3 ${t.efecto_declarado === "DESCONOCIDO" ? "font-semibold text-amber-800" : "text-slate-700"}`}>{t.efecto_declarado}</td>
                <td className="py-1.5 font-mono text-slate-600">{t.ejemplo ? `${t.ejemplo.sku} · ${t.ejemplo.woo}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SincronizacionInventario() {
  const [datos, setDatos] = useState<Vivo | null>(null);
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [nuevos, setNuevos] = useState<Evento[]>([]);
  const [nuevasClaves, setNuevasClaves] = useState<Set<string>>(new Set());
  const [tanda, setTanda] = useState(0);
  const [ultimaOk, setUltimaOk] = useState<number | null>(null);
  const [falla, setFalla] = useState(false);
  const [reloj, setReloj] = useState(() => Date.now());
  const [sel, setSel] = useState<{ sku: string; fin: string } | null>(null);
  const [traza, setTraza] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);
  const [fullAbierto, setFullAbierto] = useState(false);
  const ultimoId = useRef(0);
  const cargado = useRef(false);
  const enCurso = useRef(false);
  const vistas = useRef<Set<string>>(new Set());

  const sondear = useCallback(async () => {
    // Un sondeo a la vez. La primera respuesta puede tardar más que el
    // intervalo; si salía otro con `desde_id=0`, la página tomaba los cambios de
    // la carga inicial como nuevos y los volvía a animar.
    if (enCurso.current) return;
    enCurso.current = true;
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/vivo?desde_id=${ultimoId.current}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = (await r.json()) as Vivo;
      // Solo se anima lo que la página nunca había visto.
      const frescos = d.eventos.filter((e) => !vistas.current.has(clave(e)));
      d.eventos.forEach((e) => vistas.current.add(clave(e)));
      if (!cargado.current) {
        setEventos(d.eventos);
        cargado.current = true;
      } else if (d.eventos.length) {
        // Un cambio puede llegar en dos sondeos (sus filas se escriben una por
        // canal): se reemplaza por su clave, nunca se duplica.
        const llegan = new Set(d.eventos.map(clave));
        setEventos((prev) => [...d.eventos, ...prev.filter((e) => !llegan.has(clave(e)))].slice(0, 60));
        if (frescos.length) {
          setNuevos(frescos);
          setNuevasClaves(new Set(frescos.map(clave)));
          setTanda((t) => t + 1);
        }
      }
      ultimoId.current = Math.max(ultimoId.current, d.ultimo_id);
      setDatos(d);
      setUltimaOk(Date.now());
      setFalla(false);
    } catch {
      setFalla(true);
    } finally {
      enCurso.current = false;
    }
  }, []);

  useEffect(() => {
    void sondear();
    const t = setInterval(() => void sondear(), SONDEO_MS);
    return () => clearInterval(t);
  }, [sondear]);
  useEffect(() => {
    const t = setInterval(() => setReloj(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  // Una fila es «nueva» unos segundos, no hasta que llegue la siguiente: sin esto
  // el último cambio se quedaba resaltado indefinidamente.
  useEffect(() => {
    if (!nuevasClaves.size) return;
    const t = setTimeout(() => setNuevasClaves(new Set()), 6000);
    return () => clearTimeout(t);
  }, [nuevasClaves]);

  const visibles = useMemo(() => eventos.filter((e) => e.toca).slice(0, 12), [eventos]);
  const nuevosIds = useMemo(() => new Set(eventos.filter((e) => nuevasClaves.has(clave(e))).map((e) => e.id)), [eventos, nuevasClaves]);
  const llegaEn = useMemo(() => {
    const t = eventos.filter((e) => e.tono === "ok" && e.total_s != null && (e.origen === "odoo" || e.origen === "woo"))
      .map((e) => e.total_s as number);
    return t.length ? Math.max(1, Math.ceil(Math.max(...t) / 60)) : null;
  }, [eventos]);

  const copiar = async () => {
    if (!datos) return;
    try {
      await navigator.clipboard.writeText(`${datos.veredicto.frase} ${datos.veredicto.detalle}`);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1800);
    } catch { /* sin portapapeles: no pasa nada */ }
  };

  const hace = ultimaOk ? Math.max(0, Math.round((reloj - ultimaOk) / 1000)) : null;
  const alDia = datos?.canales.filter((c) => c.estado !== "rechaza").map((c) => c.nombre) ?? [];

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-6 sm:px-6">
        <FanoutPestanas />
        <BannerFanout
          icono={<Activity size={28} aria-hidden />}
          titulo="Sincronización de inventario"
          texto="Cada cambio de stock sale de Odoo, pasa por Woo y llega a los canales. Así va ahora mismo."
          acciones={
            <>
              <span className={ACCION_BANNER} aria-live="polite">
                <span className={`h-2 w-2 rounded-full ${falla ? "bg-amber-300" : "bg-emerald-300 motion-safe:animate-pulse"}`} />
                {falla ? "Sin conexión · reintentando" : hace == null ? "Conectando…" : hace <= 1 ? "En vivo · al momento" : `En vivo · hace ${hace} s`}
              </span>
              <button type="button" onClick={() => void copiar()} disabled={!datos}
                className={`${ACCION_BANNER} hover:bg-white/25 disabled:opacity-60`}>
                <Copy size={14} aria-hidden />{copiado ? "Copiado" : "Copiar resumen"}
              </button>
            </>
          }
          cifra={datos ? datos.canales.reduce((a, c) => a + c.ok_24h, 0).toLocaleString("es-MX") : "—"}
          cifraTexto="cambios llegaron · 24 h"
        />

        {!datos && !falla && <p className="rounded-2xl bg-white p-6 text-sm text-slate-500 shadow-sm">Leyendo la bitácora del fan-out…</p>}
        {!datos && falla && (
          <p className="flex items-center gap-2 rounded-2xl bg-white p-6 text-sm text-rose-800 shadow-sm">
            <AlertTriangle size={16} aria-hidden /> No se pudo leer el estado del fan-out. Se reintenta solo.
          </p>
        )}

        {datos && (
          <>
            <section aria-label="Veredicto"
              className={`flex flex-wrap items-center justify-between gap-x-10 gap-y-6 rounded-2xl border bg-white px-5 py-6 shadow-sm sm:px-8 sm:py-7 ${
                datos.veredicto.grave ? "border-rose-200" : "border-emerald-200"}`}>
              <div className="flex min-w-0 flex-[1_1_440px] flex-col gap-2">
                <div className="text-[28px] font-bold leading-9 text-slate-900 sm:text-[32px] sm:leading-10">{datos.veredicto.frase}</div>
                <div className="text-lg leading-7 text-slate-700">{datos.veredicto.detalle}</div>
                {llegaEn && alDia.length > 0 && (
                  <div className="text-sm text-slate-600">
                    Lo que cambia en Odoo llega a {alDia.join(" y ")} en menos de {llegaEn} {llegaEn === 1 ? "minuto" : "minutos"}.
                  </div>
                )}
              </div>
              <PulsoCanales pulso={datos.pulso} canales={datos.canales} />
            </section>

            <section className="flex flex-col gap-3 rounded-2xl bg-white px-6 pb-6 pt-5 shadow-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
                <h2 className="text-base font-bold text-slate-800">Recorrido del stock</h2>
                <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-600">
                  <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-indigo-500 shadow-[0_0_0_2px_#fff,0_0_6px_2px_#6366f1]" />cambio real</span>
                  <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-indigo-400" />pulso: ritmo de las últimas 24 h</span>
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-500" />llegó al canal</span>
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-600" />rechazado</span>
                  <span className="flex items-center gap-1.5"><span className="w-4 border-t-2 border-dashed border-slate-400" />fuera del reparto</span>
                </div>
              </div>
              <CadenaViva datos={datos} nuevos={nuevos} tanda={tanda} ultimaHora={visibles[0]?.hora ?? null} conectado={!falla} />
            </section>

            <div className="flex flex-wrap items-start gap-4">
              <section className="flex min-w-0 flex-[1_1_720px] flex-col gap-3 rounded-2xl bg-white pb-3 pt-5 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-6">
                  <div>
                    <h2 className="text-base font-bold text-slate-800">Cambios en camino</h2>
                    <p className="mt-0.5 text-[13px] text-slate-600">Cada fila es un cambio de stock y lo que contestó cada canal. Toca una para ver su rastro.</p>
                  </div>
                  <Link href="/dashboard/matriz" className="flex items-center gap-1 whitespace-nowrap text-[13px] font-semibold text-indigo-700 hover:text-indigo-800">
                    Coincidencia por SKU <ArrowRight size={14} aria-hidden />
                  </Link>
                </div>
                <HorarioTrenes eventos={visibles} columnas={datos.columnas} nuevos={nuevosIds} ocultos={datos.sin_reparto_1h}
                  onAbrir={(e) => setSel({ sku: e.sku, fin: e.fin })} />
              </section>

              <aside className="flex min-w-0 flex-[1_1_360px] flex-col gap-4 xl:max-w-[460px]">
                <section className="flex flex-col gap-2.5 rounded-2xl bg-white p-5 shadow-sm">
                  <h2 className="text-base font-bold text-slate-800">Qué atender</h2>
                  {datos.atender.length === 0 && <p className="text-sm text-slate-600">Nada pendiente. Todo llega.</p>}
                  {datos.atender.map((a, i) => (
                    <div key={i} className={`flex items-start gap-3 rounded-xl border px-3.5 py-3 ${NIVEL[a.nivel].fondo}`}>
                      <span className={`mt-0.5 w-[104px] shrink-0 rounded-full py-0.5 text-center text-xs font-semibold ${NIVEL[a.nivel].cls}`}>{NIVEL[a.nivel].texto}</span>
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <div className="text-sm font-semibold leading-5 text-slate-900">{a.titulo}</div>
                        {a.texto && <div className="text-[13px] leading-[19px] text-slate-600">{a.texto}</div>}
                        <div className="flex flex-wrap gap-x-4">
                          {a.rastro && (
                            <button type="button" onClick={() => setSel(a.rastro ?? null)}
                              className="text-left text-[13px] font-semibold text-indigo-700 hover:text-indigo-800">Ver el rastro →</button>
                          )}
                          {a.matriz && (
                            <Link href={`/dashboard/matriz?filtro=${a.nivel === "hoy" ? "demas" : "distinto"}`}
                              className="text-[13px] font-semibold text-indigo-700 hover:text-indigo-800">Ver en la matriz →</Link>
                          )}
                          {a.full && (
                            <a href="#full" onClick={() => setFullAbierto(true)}
                              className="text-[13px] font-semibold text-indigo-700 hover:text-indigo-800">Ver movimientos FULL →</a>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </section>
                {datos.bien.length > 0 && (
                  <section className="flex flex-col gap-2.5 rounded-2xl bg-white p-5 shadow-sm">
                    <h2 className="text-base font-bold text-slate-800">Lo que va bien</h2>
                    {datos.bien.map((b, i) => (
                      <div key={i} className="flex items-start gap-2.5 text-sm leading-5 text-slate-700">
                        <CheckCircle2 size={18} className="mt-px shrink-0 text-emerald-600" aria-hidden />{b}
                      </div>
                    ))}
                  </section>
                )}
              </aside>
            </div>

            <section className="flex flex-col gap-4 rounded-2xl bg-white px-6 py-5 shadow-sm">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
                <div>
                  <h2 className="text-base font-bold text-slate-800">Cambios por día</h2>
                  <p className="mt-0.5 text-[13px] text-slate-600">Últimos {datos.serie.dias.length} días, misma escala en todos. Hoy va a medias.</p>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                  <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm bg-emerald-600" />llegó al canal</span>
                  <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-sm bg-rose-500" />rechazado</span>
                </div>
              </div>
              <SerieDias serie={datos.serie} canales={datos.canales} />
            </section>

            <details id="full" open={fullAbierto} onToggle={(e) => setFullAbierto((e.target as HTMLDetailsElement).open)}
              className="group rounded-2xl bg-white shadow-sm">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4">
                <span className="flex items-center gap-2 text-base font-bold text-slate-800">
                  Movimientos de bodega FULL / FBA
                  {datos.full_sin_regla.length > 0 && (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800">
                      {datos.full_sin_regla.length} sin regla
                    </span>
                  )}
                </span>
                <ChevronDown size={18} className="text-slate-500 transition-transform group-open:rotate-180" aria-hidden />
              </summary>
              <ObservacionFull abierto={fullAbierto} />
            </details>

            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-slate-200 bg-white px-5 py-3.5">
              <p className="flex items-start gap-2 text-xs leading-[18px] text-slate-600">
                <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
                <span>
                  Este proceso: fan-out {datos.fanout.habilitado ? (datos.fanout.dry_run ? "en simulación" : "encendido y escribiendo") : "apagado"}
                  {" · "}reparto: {datos.canales.map((c) => c.nombre).join(", ")}
                  {" · "}reserva {datos.fanout.reserva} · espera {datos.fanout.debounce_s} s
                  {datos.stock_watch.modo ? ` · stock_watch en modo ${datos.stock_watch.modo}` : ""}
                  {" · "}actualizado {datos.ahora}
                </span>
              </p>
              <Link href="/dashboard/matriz" className="flex items-center gap-1 text-[13px] font-semibold text-indigo-700 hover:text-indigo-800">
                Coincidencia por SKU <ArrowRight size={14} aria-hidden />
              </Link>
            </div>
          </>
        )}
      </main>

      <RastroCambio sel={sel} onCerrar={() => setSel(null)} onIr={(sku, fin) => setSel({ sku, fin })}
        onTrazabilidad={(sku) => { setSel(null); setTraza(sku); }} />
      <TrazabilidadSku sku={traza} onCerrar={() => setTraza(null)}
        onRastro={(sku, fin) => { setTraza(null); setSel({ sku, fin }); }} />
      {falla && datos && (
        <div className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 items-center gap-2 rounded-full bg-amber-50 px-4 py-2 text-[13px] text-amber-900 shadow-lg ring-1 ring-amber-200">
          <XCircle size={16} aria-hidden /> Se perdió la conexión; se muestra el último dato y se reintenta solo.
        </div>
      )}
    </div>
  );
}
