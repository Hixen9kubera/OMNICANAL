"use client";

/**
 * /fulfillment — Pestaña FULLFILMENT: el circuito de mercancía a los almacenes
 * de los marketplaces (FULL de ML en Kubera y San Corpe, FBA de Amazon, WFS de
 * Walmart). Lo que se pidió, lo que salió, lo que el marketplace recibió — y lo
 * que todavía no se mide.
 *
 * ESTADO: POR ETAPAS. v0.521.0 subió el diseño completo con datos del mockup;
 * v0.523.0 conecta la primera lectura real, `GET /api/fulfillment/envios`
 * (salidas de Odoo a FULL/FBA/WFS clasificadas por canal y cuenta, reglas en
 * backend/services/fulfillment_envios.py). Lo que aún es mockup vive en
 * `components/fulfillment/datosDiseno.ts` y cada tarjeta que lo usa lleva el
 * chip «diseño». El prefijo `/api/fulfillment` es el de Análisis: sus GET
 * heredan `operador` (backend/core/rbac.py).
 *
 * La carpeta ES la ruta; `SesionGuard` ya lo monta `app/layout.tsx`, así que
 * aquí NO va — pero `AppNavbar` sí, porque el layout no lo pinta.
 *
 * LA REGLA DE LA PESTAÑA: «no lo sabemos» (rayado) nunca se ve igual que
 * «fue cero» (caja blanca), y ninguna etapa se deduce restando otra.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowUpDown, CircleDashed, Clock, Database, RefreshCw } from "lucide-react";
import AppNavbar from "@/components/AppNavbar";
import { API_BASE, fetchSesion } from "@/lib/api";
import { quienSoy } from "@/lib/sesion";
import { FECHA_DISENO, PLAN, SKU_EJEMPLO } from "@/components/fulfillment/datosDiseno";
import Tablero from "@/components/fulfillment/Tablero";
import { DetalleEnvioModal, TablaEnvios } from "@/components/fulfillment/Envios";
import Planeacion from "@/components/fulfillment/Planeacion";
import PorSku from "@/components/fulfillment/PorSku";
import Variaciones from "@/components/fulfillment/Variaciones";
import { FONDO_RAYADO, PUNTO_CUENTA, TEMA_CANAL, dia, num } from "@/components/fulfillment/ui";
import type { Envio, FiltroCanal, FiltroCuenta, RespuestaEnvios, Rol } from "@/components/fulfillment/tipos";

/** El rótulo se escribió así en la petición. Se cambia aquí y en AppNavbar. */
const ROTULO = "FULLFILMENT";

type Pantalla = "tablero" | "envios" | "planeacion" | "sku" | "variaciones";

const PANTALLAS: { k: Pantalla; t: string }[] = [
  { k: "tablero", t: "Tablero" },
  { k: "envios", t: "Envíos" },
  { k: "planeacion", t: "Planeación semanal" },
  { k: "sku", t: "Por SKU / MLM" },
  { k: "variaciones", t: "Variaciones" },
];

const CANALES: { k: FiltroCanal; t: string; punto: string; titulo?: string }[] = [
  { k: "todos", t: "Todos", punto: "#818CF8" },
  { k: "meli", t: "Mercado Libre", punto: "#FFE600" },
  { k: "amazon", t: "Amazon FBA", punto: "#FF9900" },
  { k: "walmart", t: "Walmart WFS", punto: "#0071DC", titulo: "Un solo envío a WFS en toda la historia." },
];

export default function FulfillmentPage() {
  const [pantalla, setPantalla] = useState<Pantalla>("tablero");
  const [canal, setCanal] = useState<FiltroCanal>("todos");
  const [cuenta, setCuenta] = useState<FiltroCuenta>("todas");
  const [abierto, setAbierto] = useState<Envio | null>(null);

  // La lectura real: una sola para toda la pestaña (Tablero, Envíos y Detalle
  // salen de la misma respuesta; el backend la guarda 2 min).
  const [datos, setDatos] = useState<RespuestaEnvios | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const cargar = useCallback(async (refrescar = false) => {
    setCargando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/envios${refrescar ? "?refrescar=true" : ""}`,
                                  { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
      setDatos(await r.json() as RespuestaEnvios);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, []);
  useEffect(() => { void cargar(); }, [cargar]);

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
  const todos = useMemo(() => datos?.envios ?? [], [datos]);
  const envios = useMemo(() => todos
    .filter((e) => canal === "todos" || e.canal === canal)
    // Con una cuenta elegida, los envíos de ML sin cuenta NO entran: no se sabe de cuál son.
    .filter((e) => cuenta === "todas" || e.canal !== "meli" || e.cuenta === cuenta),
  [todos, canal, cuenta]);

  // La cifra grande sigue al canal: bajo el chip de Amazon no puede ir la de FULL.
  const grupo = datos?.resumen[
    canal === "amazon" ? "amazon" : canal === "walmart" ? "walmart" : cuenta === "todas" ? "meli" : `meli:${cuenta}`];
  const heroTablero = grupo
    ? { cifra: num(grupo.piezas_enviadas),
        pie: `piezas enviadas a ${canal === "amazon" ? "FBA" : canal === "walmart" ? "WFS" : "FULL"}`,
        nota: `${num(grupo.hechas)} salidas validadas en Odoo · ${dia(grupo.desde)} → ${dia(grupo.hasta)}` }
    : { cifra: "…", pie: cargando ? "leyendo Odoo" : "sin lectura", nota: "" };
  const hero = {
    tablero: heroTablero,
    envios: { cifra: num(envios.length), pie: "envíos en la vista",
              nota: `de ${num(todos.length)} salidas a FULL, FBA y WFS en Odoo` },
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

        {/* Conviven datos en vivo y del mockup: nadie debe confundir unos con otros. */}
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-[12.5px] text-amber-900">
          <CircleDashed className="h-4 w-4 shrink-0 text-amber-600" />
          <b>En construcción por etapas.</b>
          <span>
            <b>En vivo</b>: los envíos y su detalle salen de Odoo; la <b>llegada a FULL</b> sale de los avisos de
            FULL de Mercado Libre (con lo que no recibió) y la <b>primera venta</b> de kubera (ML no publica los envíos
            a Full por API, así que no hay declaradas ni motivos). <b>Diseño</b> (mockup del {FECHA_DISENO}, con su chip): stock en FULL,
            agotado, stock FBA, planeación, ficha de SKU y variaciones. Ningún botón escribe en ninguna parte.
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
                <span className={pastilla}><Database className="h-3.5 w-3.5" />Envíos: Odoo en vivo · llegadas: avisos de FULL de ML</span>
              </div>
            </div>
            <div className="flex items-start gap-4">
              <div className="text-right">
                <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums">{hero.cifra}</div>
                <div className="mt-1 text-[11px] font-bold uppercase tracking-[.06em] opacity-70">{hero.pie}</div>
                <div className="mt-2 text-xs opacity-85">{hero.nota}</div>
              </div>
              <button type="button" onClick={() => void cargar(true)} disabled={cargando}
                      title={`Volver a leer Odoo${datos?._cache ? ` · la lectura actual tiene ${datos._cache.edad_s} s` : ""}`}
                      className="rounded-lg bg-white/15 p-2 transition hover:bg-white/25 disabled:opacity-50">
                <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
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

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12.5px] text-rose-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <b>No se pudo leer Odoo.</b> Lo que depende de la lectura en vivo queda en blanco — no en cero.
              Error del backend: <code className="font-mono">{error}</code>
            </span>
          </div>
        )}

        {pantalla === "tablero" && <Tablero canal={canal} cuenta={cuenta} datos={datos} />}
        {pantalla === "envios" && (datos
          ? <TablaEnvios envios={envios} total={todos.length}
                         onAbrir={setAbierto} />
          : <Espera cargando={cargando} />)}
        {pantalla === "planeacion" && <Planeacion rol={rol} />}
        {pantalla === "sku" && <PorSku />}
        {pantalla === "variaciones" && <Variaciones />}

        {/* El detalle de un envío se abre ENCIMA de la tabla, no en otra pestaña. */}
        {abierto && <DetalleEnvioModal envio={abierto} onCerrar={() => setAbierto(null)} />}

        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          {datos
            ? `Fuente en vivo: ${datos.fuente}. Lectura de ${new Date(datos.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.`
            : "Sin lectura de Odoo todavía."}{" "}
          Canal por el nombre del socio; cuenta por quien creó la orden de venta (Thalia = San Corpe, Cinthya = Kubera;
          evidencia orden por orden en docs/FULLFILMENT_EVIDENCIA_ORDENES.md). Todo lo rayado es un hueco real de
          datos, no un cero.
        </p>
      </main>
    </div>
  );
}

function Espera({ cargando }: { cargando: boolean }) {
  return (
    <div className="mt-4 rounded-2xl border border-dashed border-slate-300 px-6 py-12 text-center text-sm text-slate-500"
         style={{ background: FONDO_RAYADO }}>
      {cargando ? "Leyendo las salidas de Odoo…" : "Sin lectura de Odoo: revisa el error de arriba y vuelve a intentar."}
    </div>
  );
}
