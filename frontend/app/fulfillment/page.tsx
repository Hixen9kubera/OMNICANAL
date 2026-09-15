"use client";

/**
 * /fulfillment — Pestaña FULLFILMENT: el circuito de mercancía a los almacenes
 * de los marketplaces (FULL de ML en Kubera y San Corpe, FBA de Amazon, WFS de
 * Walmart). Lo que se pidió, lo que salió, lo que el marketplace recibió — y lo
 * que todavía no se mide.
 *
 * ESTADO: VISTA DE DISEÑO (14-sep-2026). Se construyó el frontend primero, a
 * partir del mockup `FULLFILMENT.dc.html` y su handoff, para ver cómo queda
 * antes de hacer el backend. Los datos salen de
 * `components/fulfillment/datosDiseno.ts`; la franja ámbar de arriba lo dice.
 * El API irá bajo `/api/fulfillment/envios/…` (el prefijo `/api/fulfillment`
 * es el de Análisis; ver `backend/core/rbac.py:136`).
 *
 * La carpeta ES la ruta; `SesionGuard` ya lo monta `app/layout.tsx`, así que
 * aquí NO va — pero `AppNavbar` sí, porque el layout no lo pinta.
 *
 * LA REGLA DE LA PESTAÑA: «no lo sabemos» (rayado) nunca se ve igual que
 * «fue cero» (caja blanca), y ninguna etapa se deduce restando otra.
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowUpDown, CircleDashed, Clock, Database, RefreshCw } from "lucide-react";
import AppNavbar from "@/components/AppNavbar";
import { quienSoy } from "@/lib/sesion";
import { ENVIOS, FBA, FECHA_DISENO, PLAN, SKU_EJEMPLO, TABLERO } from "@/components/fulfillment/datosDiseno";
import Tablero from "@/components/fulfillment/Tablero";
import { DetalleEnvio, TablaEnvios } from "@/components/fulfillment/Envios";
import Planeacion from "@/components/fulfillment/Planeacion";
import PorSku from "@/components/fulfillment/PorSku";
import Variaciones from "@/components/fulfillment/Variaciones";
import { FONDO_RAYADO, PUNTO_CUENTA, TEMA_CANAL, num } from "@/components/fulfillment/ui";
import type { Envio, FiltroCanal, FiltroCuenta, Rol } from "@/components/fulfillment/tipos";

/** El rótulo se escribió así en la petición. Se cambia aquí y en AppNavbar. */
const ROTULO = "FULLFILMENT";

type Pantalla = "tablero" | "envios" | "detalle" | "planeacion" | "sku" | "variaciones";

const PANTALLAS: { k: Pantalla; t: string }[] = [
  { k: "tablero", t: "Tablero" },
  { k: "envios", t: "Envíos" },
  { k: "detalle", t: "Detalle de un envío" },
  { k: "planeacion", t: "Planeación semanal" },
  { k: "sku", t: "Por SKU / MLM" },
  { k: "variaciones", t: "Variaciones" },
];

const CANALES: { k: FiltroCanal; t: string; punto: string; titulo?: string }[] = [
  { k: "todos", t: "Todos", punto: "#818CF8" },
  { k: "meli", t: "Mercado Libre", punto: "#FFE600" },
  { k: "amazon", t: "Amazon FBA", punto: "#FF9900" },
  { k: "walmart", t: "Walmart WFS", punto: "#0071DC", titulo: "Sin una sola orden en Odoo: toda la vista queda rayada." },
];

export default function FulfillmentPage() {
  const [pantalla, setPantalla] = useState<Pantalla>("tablero");
  const [canal, setCanal] = useState<FiltroCanal>("todos");
  const [cuenta, setCuenta] = useState<FiltroCuenta>("todas");
  const [abierto, setAbierto] = useState<Envio>(ENVIOS[0]);

  // El rol real decide qué acciones se ven habilitadas. El cambiador «Vista
  // previa como» existe sólo mientras la pestaña es diseño: deja ver lo que ve
  // un KAM sin cambiar de cuenta. El candado de verdad es core/rbac.py.
  const [rolReal, setRolReal] = useState<Rol | null>(null);
  const [rolVista, setRolVista] = useState<Rol | null>(null);
  useEffect(() => {
    let vivo = true;
    void quienSoy().then((u) => {
      if (vivo && u.autenticado) setRolReal(u.rol === "admin" ? "admin" : "kam");
    });
    return () => { vivo = false; };
  }, []);
  const rol: Rol = rolVista ?? rolReal ?? "admin";

  const tema = TEMA_CANAL[canal];
  const envios = useMemo(() => ENVIOS
    .filter((e) => canal === "todos" || e.canal === canal)
    .filter((e) => cuenta === "todas" || e.canal !== "meli" || e.cuenta === cuenta),
  [canal, cuenta]);

  // La cifra grande sigue al canal: bajo el chip de Amazon no puede ir la de FULL.
  const heroTablero = canal === "amazon"
    ? { cifra: num(FBA.piezas), pie: "piezas enviadas a FBA", nota: `${FBA.ordenes} órdenes de salida · ${FBA.cuenta}` }
    : canal === "walmart"
      ? { cifra: "—", pie: "piezas enviadas a WFS", nota: "sin registro: no hay órdenes con socio Walmart" }
      : { cifra: num(TABLERO.enviadoFull.piezas), pie: "piezas enviadas a FULL",
          nota: `${TABLERO.enviadoFull.ordenes} órdenes · ${TABLERO.enviadoFull.desde} → ${FECHA_DISENO}` };
  const hero = {
    tablero: heroTablero,
    envios: { cifra: String(envios.length), pie: "envíos en la vista",
              nota: `de ${TABLERO.enviadoFull.ordenes} órdenes de salida medidas` },
    detalle: { cifra: num(abierto.piezas), pie: "piezas del envío", nota: `orden ${abierto.orden ?? "—"} · ${abierto.kam ?? "—"}` },
    planeacion: { cifra: num(PLAN.reduce((a, r) => a + r.pidio, 0)), pie: "piezas que pidió Andy",
                  nota: `${PLAN.length} renglones en la lista de la semana` },
    sku: { cifra: String(SKU_EJEMPLO.enFullHoy), pie: "piezas en FULL hoy",
           nota: `${SKU_EJEMPLO.sku} · cobertura ${SKU_EJEMPLO.coberturaDias} días` },
    variaciones: { cifra: "4", pie: "componentes con opciones", nota: "gráfica, embudo, rail y días" },
  }[pantalla];

  const pastilla = "inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-2.5 py-1 text-[11px] font-semibold opacity-90";
  const verCuentas = canal === "todos" || canal === "meli";

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 pb-10 pt-[22px] sm:px-6">

        {/* Nadie debe confundir la maqueta con una lectura en vivo. */}
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-[12.5px] text-amber-900">
          <CircleDashed className="h-4 w-4 shrink-0 text-amber-600" />
          <b>Vista de diseño.</b>
          <span>
            Todavía no hay backend: las cifras agregadas son las que midió el diseño el {FECHA_DISENO} y los
            envíos, la planeación y la ficha de SKU son ejemplos simulados. Ningún botón escribe en ninguna parte.
          </span>
        </div>

        {/* ── Hero ───────────────────────────────────────────────────────── */}
        <section
          className={`relative overflow-hidden rounded-2xl px-6 py-5 shadow-[0_2px_8px_rgba(79,70,229,.25)] ${
            canal === "todos" ? "bg-gradient-to-br from-indigo-600 via-indigo-600 to-violet-700" : ""}`}
          style={{ background: canal === "todos" ? undefined : tema.color, color: tema.texto }}>
          <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-white/10" />
          <div className="relative flex flex-wrap items-start justify-between gap-6">
            <div className="min-w-0">
              <p className="text-[11px] font-bold uppercase tracking-[.08em] opacity-70">
                Circuito de mercancía a los almacenes del marketplace
              </p>
              <h1 className="mt-1 flex flex-wrap items-center gap-2.5 text-3xl font-extrabold tracking-tight">
                {ROTULO}
                <span className="rounded-full bg-white/20 px-2.5 py-1 text-xs font-bold">
                  {canal === "todos" ? "3 programas · 2 cuentas de ML" : tema.nombre}
                </span>
              </h1>
              <p className="mt-1.5 max-w-2xl text-sm opacity-85">
                FULL de Mercado Libre (Kubera y San Corpe), FBA de Amazon y WFS de Walmart. Lo que se pidió, lo
                que salió, lo que el marketplace recibió — y lo que todavía no se mide.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className={pastilla}><ArrowUpDown className="h-3.5 w-3.5" />Odoo → paquetería del canal → almacén del marketplace</span>
                <span className={pastilla}><Clock className="h-3.5 w-3.5" />Semana ISO · hora de Ciudad de México</span>
                <span className={pastilla}><Database className="h-3.5 w-3.5" />Recepciones: registro desde {FECHA_DISENO}</span>
              </div>
            </div>
            <div className="flex items-start gap-4">
              <div className="text-right">
                <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums">{hero.cifra}</div>
                <div className="mt-1 text-[11px] font-bold uppercase tracking-[.06em] opacity-70">{hero.pie}</div>
                <div className="mt-2 text-xs opacity-85">{hero.nota}</div>
              </div>
              <button type="button" disabled
                      title="Vista de diseño: no hay nada que volver a leer todavía."
                      className="cursor-not-allowed rounded-lg bg-white/15 p-2 opacity-60">
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          </div>
        </section>

        {/* ── Pantallas y filtros ────────────────────────────────────────── */}
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex overflow-hidden rounded-[10px] border border-slate-200 bg-white">
            {PANTALLAS.map((p) => {
              const on = pantalla === p.k;
              return (
                <button key={p.k} type="button" onClick={() => setPantalla(p.k)}
                        className={`border-r border-slate-100 px-3.5 py-2 text-[13px] last:border-r-0 ${
                          on ? "bg-indigo-50 font-bold text-indigo-800" : "font-medium text-slate-500 hover:bg-slate-50"}`}>
                  {p.t}
                </button>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">Canal</span>
            {CANALES.map((c) => {
              const on = canal === c.k;
              const th = TEMA_CANAL[c.k];
              return (
                <button key={c.k} type="button" title={c.titulo ?? c.t}
                        onClick={() => { setCanal(c.k); setCuenta("todas"); }}
                        className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-bold"
                        style={{ borderColor: on ? th.borde : "#e2e8f0", background: on ? th.suave : "#fff", color: on ? th.chip : "#64748b" }}>
                  <span className="h-2 w-2 rounded-full" style={{ background: c.punto }} />{c.t}
                </button>
              );
            })}
          </div>

          {verCuentas && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">Cuenta</span>
              {(["todas", "Kubera", "San Corpe"] as FiltroCuenta[]).map((k) => {
                const on = cuenta === k;
                return (
                  <button key={k} type="button" onClick={() => setCuenta(k)}
                          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${
                            on ? "border-indigo-200 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-500"}`}>
                    <span className="h-2 w-2 rounded-full"
                          style={{ background: k === "todas" ? "#cbd5e1" : PUNTO_CUENTA[k] }} />
                    {k === "todas" ? "Todas" : k}
                  </button>
                );
              })}
            </div>
          )}

          <div className="ml-auto flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400"
                  title="Sólo mientras la pestaña es diseño: deja ver lo que ve cada rol.">
              Vista previa como
            </span>
            <div className="flex overflow-hidden rounded-lg border border-slate-200 bg-white">
              {(["kam", "admin"] as Rol[]).map((r) => (
                <button key={r} type="button" onClick={() => setRolVista(r)}
                        className={`px-3 py-1.5 text-xs font-semibold ${
                          rol === r ? "bg-indigo-50 text-indigo-800" : "text-slate-500 hover:bg-slate-50"}`}>
                  {r === "kam" ? "KAM" : "Admin"}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* ── Leyenda fija: vive bajo los filtros, no en un tooltip ──────── */}
        <div className="mt-3 flex flex-wrap items-center gap-x-[18px] gap-y-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5">
          <span className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">Cómo leer esta pestaña</span>
          <span className="inline-flex items-center gap-[7px] text-xs text-slate-600">
            <span className="h-3 w-[26px] rounded bg-emerald-600" />dato real
          </span>
          <span className="inline-flex items-center gap-[7px] text-xs text-slate-600">
            <span className="h-3 w-[26px] rounded border border-slate-200 bg-white" />cero real — pasó y fue cero
          </span>
          <span className="inline-flex items-center gap-[7px] text-xs text-slate-600">
            <span className="h-3 w-[26px] rounded border border-dashed border-slate-300" style={{ background: FONDO_RAYADO }} />
            sin dato todavía — no es un cero
          </span>
          <span className="inline-flex items-center gap-[7px] text-xs text-amber-700">
            <span className="h-3 w-[26px] rounded border border-amber-300 bg-amber-100" />en espera — el dato viene, aún no llega
          </span>
          <span className="ml-auto text-[11px] text-slate-400">Nunca se resta una etapa de otra para inventar la siguiente.</span>
        </div>

        {pantalla === "tablero" && <Tablero canal={canal} cuenta={cuenta} />}
        {pantalla === "envios" && (
          <TablaEnvios envios={envios} total={TABLERO.enviadoFull.ordenes}
                       onAbrir={(e) => { setAbierto(e); setPantalla("detalle"); }} />
        )}
        {pantalla === "detalle" && <DetalleEnvio envio={abierto} onVolver={() => setPantalla("envios")} />}
        {pantalla === "planeacion" && <Planeacion rol={rol} />}
        {pantalla === "sku" && <PorSku />}
        {pantalla === "variaciones" && <Variaciones />}

        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          Mockup de diseño. Las cifras de FULL, FBA, Odoo y días de proceso son las medidas el {FECHA_DISENO}; todo lo
          rayado es un hueco real de datos, no un cero. Las recepciones, los rechazos y las tasas de validado/enviado
          empiezan a existir el día que el panel las guarde.
        </p>
      </main>
    </div>
  );
}
