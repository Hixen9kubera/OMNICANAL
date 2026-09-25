"use client";

/**
 * /fulfillment — Pestaña FULLFILMENT: el circuito de mercancía a los almacenes
 * de los marketplaces (FULL de ML en Kubera y San Corpe, FBA de Amazon, WFS de
 * Walmart).
 *
 * ORDEN DE APP (Brandon, 24-sep-2026): "como las aplicaciones de banco —
 * consultar el saldo y ejecutar una transacción—: primero CREAR FULL, después
 * ENVÍOS para checar los status, después ANÁLISIS". Tres pantallas y nada más:
 *   · Crear FULL  — el saldo y la PLANEACIÓN SEMANAL por tienda (ML Kubera, ML San
 *                   Corpe, Amazon FBA, Walmart WFS; Temu y TikTok son sólo DROP), con
 *                   IA, búsqueda de SKUs y órdenes en Odoo por tienda
 *                   (components/fulfillment/CrearFull.tsx, v0.566.0);
 *   · Envíos      — en qué va cada salida, con su detalle en ventana
 *                   (components/fulfillment/Envios.tsx);
 *   · Análisis    — enviado contra recibido, POR SEMANA
 *                   (components/fulfillment/Analisis.tsx).
 * La ficha de un SKU/MLM dejó de ser pestaña: es la ventana que se abre al tocar
 * un SKU dentro del detalle de un envío (FichaSku.tsx). Las pantallas de diseño
 * (Planeación con datos simulados, Variaciones) se retiraron en v0.556.0.
 *
 * v0.570.0 (Brandon: "se deja Crear FULL únicamente para crear FULLs"): los
 * totales, los ganadores con su reemplazo, los títulos contra Odoo y las órdenes
 * sin completar se ven en Análisis · «Planeación de la semana». Crear FULL se
 * queda MONTADO aunque se cambie de pantalla —lo editado y la conversación con la
 * IA no se pierden— y le pasa esos datos a Análisis con `onPlan`.
 *
 * La carpeta ES la ruta; `SesionGuard` ya lo monta `app/layout.tsx`, así que
 * aquí NO va — pero `AppNavbar` sí, porque el layout no lo pinta.
 *
 * LA REGLA DE LA PESTAÑA: «no lo sabemos» (rayado) nunca se ve igual que
 * «fue cero» (caja blanca), y ninguna etapa se deduce restando otra.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ComponentType } from "react";
import { AlertTriangle, BarChart3, PlusCircle, RefreshCw, Truck } from "lucide-react";
import AppNavbar from "@/components/AppNavbar";
import { API_BASE, fetchSesion } from "@/lib/api";
import { quienSoy } from "@/lib/sesion";
import Analisis from "@/components/fulfillment/Analisis";
import CrearFull from "@/components/fulfillment/CrearFull";
import type { PedidoReemplazo } from "@/components/fulfillment/CrearFull";
import type { PlanAnalisis } from "@/components/fulfillment/AnalisisPlaneacion";
import { DetalleEnvioModal, TablaEnvios, seguimientoDe } from "@/components/fulfillment/Envios";
import { FONDO_RAYADO, PUNTO_CUENTA, TEMA_CANAL, num } from "@/components/fulfillment/ui";
import type { Envio, FiltroCanal, FiltroCuenta, RespuestaEnvios, Rol, Tienda } from "@/components/fulfillment/tipos";

/** El rótulo se escribió así en la petición. Se cambia aquí y en AppNavbar. */
const ROTULO = "FULLFILMENT";

type Pantalla = "crear" | "envios" | "analisis";

const PANTALLAS: { k: Pantalla; t: string; icono: ComponentType<{ className?: string }> }[] = [
  { k: "crear", t: "Crear FULL", icono: PlusCircle },
  { k: "envios", t: "Envíos", icono: Truck },
  { k: "analisis", t: "Análisis", icono: BarChart3 },
];

const CANALES: { k: FiltroCanal; t: string; punto: string; titulo?: string }[] = [
  { k: "todos", t: "Todos", punto: "#818CF8" },
  { k: "meli", t: "Mercado Libre", punto: "#FFE600" },
  { k: "amazon", t: "Amazon FBA", punto: "#FF9900" },
  { k: "walmart", t: "Walmart WFS", punto: "#0071DC", titulo: "Un solo envío a WFS en toda la historia." },
];

function pantallaDeLaUrl(): Pantalla {
  if (typeof window === "undefined") return "crear";
  const h = window.location.hash.replace("#", "");
  return h === "envios" || h === "analisis" ? h : "crear";
}

export default function FulfillmentPage() {
  const [pantalla, setPantalla] = useState<Pantalla>("crear");
  const [canal, setCanal] = useState<FiltroCanal>("todos");
  const [cuenta, setCuenta] = useState<FiltroCuenta>("todas");
  const [abierto, setAbierto] = useState<Envio | null>(null);
  const [recarga, setRecarga] = useState(0);
  const [porMandar, setPorMandar] = useState<{ skus: number; piezas: number; tiendas: number } | null>(null);
  // Lo que Crear FULL le pasa a Análisis, y los reemplazos que Análisis le pide a Crear FULL.
  const [plan, setPlan] = useState<PlanAnalisis | null>(null);
  const [reemplazo, setReemplazo] = useState<PedidoReemplazo | null>(null);
  const pedirReemplazo = useCallback((tienda: Tienda, sku: string, de: string) =>
    setReemplazo({ id: Date.now(), tienda, sku, de }), []);
  const reemplazoHecho = useCallback((id: number) =>
    setReemplazo((r) => (r && r.id === id ? null : r)), []);

  // La pantalla viaja en el #: recargar no te regresa al inicio y se puede
  // mandar la liga de «Envíos» o «Análisis».
  useEffect(() => {
    setPantalla(pantallaDeLaUrl());
    const alCambiar = () => setPantalla(pantallaDeLaUrl());
    window.addEventListener("hashchange", alCambiar);
    return () => window.removeEventListener("hashchange", alCambiar);
  }, []);
  const ir = (p: Pantalla) => {
    setPantalla(p);
    window.history.replaceState(null, "", p === "crear" ? window.location.pathname : `#${p}`);
  };

  // La lectura de envíos: una sola para Envíos, Análisis y el saldo de Crear FULL
  // (el backend la guarda 2 min).
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
  const actualizar = () => { void cargar(true); setRecarga((n) => n + 1); };

  // El rol real decide si «Crear en Odoo» se puede tocar. El candado de verdad es
  // core/rbac.py: crear y el interruptor son de admin.
  const [rol, setRol] = useState<Rol>("kam");
  useEffect(() => {
    let vivo = true;
    void quienSoy().then((u) => { if (vivo && u.autenticado) setRol(u.rol === "admin" ? "admin" : "kam"); });
    return () => { vivo = false; };
  }, []);

  const todos = useMemo(() => datos?.envios ?? [], [datos]);
  const envios = useMemo(() => todos
    .filter((e) => canal === "todos" || e.canal === canal)
    // Con una cuenta elegida, los envíos de ML sin cuenta NO entran: no se sabe de cuál son.
    .filter((e) => cuenta === "todas" || e.canal !== "meli" || e.cuenta === cuenta),
  [todos, canal, cuenta]);
  const enCurso = useMemo(() => {
    let porValidar = 0;
    let llegando = 0;
    for (const e of envios) {
      const s = seguimientoDe(e);
      if (s === "por_validar") porValidar += 1;
      if (s === "llegando") llegando += 1;
    }
    return { porValidar, llegando };
  }, [envios]);

  const verCuentas = canal === "todos" || canal === "meli";
  const edad = datos?._cache?.edad_s;

  // Lo que dice cada botón debajo de su nombre: el "saldo" de esa pantalla.
  const sub: Record<Pantalla, string> = {
    crear: porMandar ? `${num(porMandar.piezas)} pzs de ${num(porMandar.skus)} SKUs por mandar · ${porMandar.tiendas} tienda${porMandar.tiendas === 1 ? "" : "s"}` : "la planeación de la semana",
    envios: datos ? `${enCurso.porValidar} por validar · ${enCurso.llegando} llegando` : "leyendo Odoo…",
    analisis: plan ? `por semana · ${num(plan.ganadores.length)} ganadores sin existencia` : "enviado contra recibido, por semana",
  };

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 pb-10 pt-[22px] sm:px-6">

        {/* ── Encabezado y las tres pantallas ──────────────────────────────── */}
        <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-600 to-violet-700 px-5 py-4 text-white shadow-[0_2px_8px_rgba(79,70,229,.25)] sm:px-6">
          <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-white/10" />
          <div className="relative flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-[.08em] opacity-70">
                Mercado Libre FULL (Kubera y San Corpe) · Amazon FBA · Walmart WFS
              </p>
              <h1 className="mt-0.5 text-2xl font-extrabold tracking-tight sm:text-3xl">{ROTULO}</h1>
            </div>
            <div className="flex items-center gap-2 text-[11.5px] opacity-90">
              <span className="hidden sm:inline">
                {cargando ? "leyendo Odoo…" : edad !== undefined ? `Odoo en vivo · leído hace ${edad < 60 ? `${edad} s` : `${Math.round(edad / 60)} min`}` : ""}
              </span>
              <button type="button" onClick={actualizar} disabled={cargando}
                      title="Volver a leer Odoo, los avisos de ML y la propuesta"
                      className="rounded-lg bg-white/15 p-2 transition hover:bg-white/25 disabled:opacity-50">
                <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
              </button>
            </div>
          </div>

          <nav className="relative mt-4 grid grid-cols-3 gap-2" aria-label="Pantallas de FULLFILMENT">
            {PANTALLAS.map((p, n) => {
              const on = pantalla === p.k;
              const Icono = p.icono;
              return (
                <button key={p.k} type="button" onClick={() => ir(p.k)} aria-current={on ? "page" : undefined}
                        className={`flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left transition sm:px-4 ${
                          on ? "bg-white text-indigo-800 shadow-md" : "bg-white/10 text-white hover:bg-white/20"}`}>
                  <span className={`hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg sm:flex ${on ? "bg-indigo-600 text-white" : "bg-white/15"}`}>
                    <Icono className="h-5 w-5" />
                  </span>
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[15px] font-extrabold leading-tight">
                      <span className={`text-[11px] font-bold ${on ? "text-indigo-400" : "opacity-60"}`}>{n + 1}</span>{p.t}
                    </span>
                    <span className={`block truncate text-[11px] ${on ? "text-indigo-600/80" : "opacity-75"}`}>{sub[p.k]}</span>
                  </span>
                </button>
              );
            })}
          </nav>
        </section>

        {/* ── Filtros: sólo donde se ven envíos (Crear FULL tiene sus propias tiendas) ── */}
        {pantalla !== "crear" && (
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
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
            <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
              <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded bg-emerald-600" />dato real</span>
              <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded border border-slate-200 bg-white" />cero real</span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-5 rounded border border-dashed border-slate-300" style={{ background: FONDO_RAYADO }} />sin dato, no es cero
              </span>
              <span className="inline-flex items-center gap-1.5 text-amber-700">
                <span className="h-2.5 w-5 rounded border border-amber-300 bg-amber-100" />en espera
              </span>
            </div>
          </div>
        )}

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12.5px] text-rose-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <b>No se pudo leer Odoo.</b> Lo que depende de la lectura en vivo queda en blanco — no en cero.
              Error del backend: <code className="font-mono">{error}</code>
            </span>
          </div>
        )}

        {/* Siempre montado: cambiar de pantalla no borra lo editado ni la conversación con la IA. */}
        <div className={pantalla === "crear" ? "" : "hidden"}>
          <CrearFull stock={datos?.stock} rol={rol} recarga={recarga} onEstado={setPorMandar}
                     onPlan={setPlan} reemplazoPedido={reemplazo} onReemplazoHecho={reemplazoHecho} />
        </div>
        {pantalla === "envios" && (datos
          ? <TablaEnvios envios={envios} total={todos.length} onAbrir={setAbierto} />
          : <Espera cargando={cargando} />)}
        {pantalla === "analisis" && <Analisis canal={canal} cuenta={cuenta} datos={datos} onAbrir={setAbierto}
                                              plan={plan} onAgregarReemplazo={pedirReemplazo} />}

        {/* El detalle de un envío se abre ENCIMA, y la ficha de un SKU encima de él. */}
        {abierto && <DetalleEnvioModal envio={abierto} onCerrar={() => setAbierto(null)} />}

        {pantalla !== "crear" && (
          <p className="mt-4 text-xs leading-relaxed text-slate-400">
            {datos
              ? `Fuente en vivo: ${datos.fuente}. Lectura de ${new Date(datos.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.`
              : "Sin lectura de Odoo todavía."}{" "}
            Canal por el nombre del socio; cuenta por el socio fijo (FULL KUBERA / FULL SAN CORPE) o por quien creó la orden
            de venta (Thalia = San Corpe, Cinthya = Kubera; evidencia en docs/FULLFILMENT_EVIDENCIA_ORDENES.md).
          </p>
        )}
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
