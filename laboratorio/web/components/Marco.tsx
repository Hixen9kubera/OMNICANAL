"use client";

/**
 * El marco del laboratorio: barra superior propia (no la del panel), sub-pestañas
 * y la franja de avisos.
 *
 * Al montar pregunta `/api/lab/estado`: un 401 manda a /login (lo hace `pedir`) y
 * la respuesta alimenta la fecha de los datos y los avisos de frescura. La franja
 * SIEMPRE recuerda que ningún precio se aplica sin autorización: el laboratorio
 * propone y nada más (DISENO §0.3).
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle, BarChart3, Boxes, CheckCircle2, Download, FlaskConical, History, Loader2, Lock, LogOut, RefreshCw, ShieldCheck, Tags, TrendingUp,
} from "lucide-react";
import { cerrarSesion, descargarCsvPrecios, pedir, recalcular, USA_FIXTURES } from "@/lib/api";
import { diaHora, diasDesde, dia } from "@/lib/formato";
import type { Estado } from "@/lib/tipos";
import { olvidarCache, usePedido } from "@/lib/usePedido";
import { Dialogo } from "./ui";

const CtxEstado = createContext<Estado | null>(null);
export const useEstado = () => useContext(CtxEstado);

const PESTANAS = [
  { href: "/publicaciones", label: "Publicaciones", icono: Tags },
  { href: "/precios", label: "Precios óptimos", icono: TrendingUp },
  { href: "/metricas", label: "Métricas", icono: BarChart3 },
  { href: "/packing", label: "Packing list", icono: Boxes },
  { href: "/historial", label: "Historial", icono: History },
];

const SEG_SONDEO = 5;

/**
 * «Recalcular datos»: sólo si `/estado` lo permite (`servidor.recalcular.permitido`,
 * api.py). Pide confirmación, hace POST /recalcular y, mientras el pipeline corre,
 * vuelve a leer /estado cada 5 s. Al terminar no recarga solo: avisa y ofrece
 * recargar (lo que hay en pantalla es de la corrida anterior hasta entonces).
 */
function useRecalculo(inicial: Estado | null) {
  const [vivo, setVivo] = useState<Estado | null>(null);
  const [lanzado, setLanzado] = useState(false);
  const [terminado, setTerminado] = useState<null | { ok: boolean; error?: string | null; duracion?: number | null }>(null);
  const estado = vivo ?? inicial;
  const corriendo = !!estado?.corriendo || lanzado;
  const visto = useRef(false);

  useEffect(() => {
    if (!corriendo) return;
    visto.current = true;
    const t = setInterval(async () => {
      try {
        const e = await pedir<Estado>("/estado");
        setVivo(e);
        if (e.corriendo) setLanzado(false);
        else if (visto.current) {
          visto.current = false;
          setLanzado(false);
          const u = e.servidor?.pipeline?.ultima;
          setTerminado({ ok: u?.ok !== false, error: u?.error ?? null, duracion: u?.duracion_s ?? null });
        }
      } catch { /* sin red: se reintenta en el siguiente ciclo */ }
    }, SEG_SONDEO * 1000);
    return () => clearInterval(t);
  }, [corriendo]);

  async function lanzar(sinMl: boolean): Promise<string | null> {
    setTerminado(null);
    const motivo = await recalcular(sinMl);
    if (motivo === null || motivo === "Ya hay un recálculo en curso.") setLanzado(true);
    return motivo;
  }
  return { estado, corriendo, terminado, lanzar, cerrarAviso: () => setTerminado(null) };
}

function BotonRecalcular({ corriendo, onLanzar }: { corriendo: boolean; onLanzar: (sinMl: boolean) => Promise<string | null> }) {
  const [abierto, setAbierto] = useState(false);
  const [conMl, setConMl] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirmar() {
    setEnviando(true); setError(null);
    const motivo = await onLanzar(!conMl);
    setEnviando(false);
    if (motivo && motivo !== "Ya hay un recálculo en curso.") { setError(motivo); return; }
    setAbierto(false); setConMl(false);
  }
  return (
    <>
      <button type="button" onClick={() => { setError(null); setAbierto(true); }} disabled={corriendo}
              title={corriendo ? "El pipeline está recalculando." : "Volver a calcular costos, elasticidades y propuestas con los datos de hoy."}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:border-indigo-300 hover:text-indigo-700 disabled:cursor-wait disabled:opacity-70 sm:px-3">
        <RefreshCw size={14} className={corriendo ? "animate-spin text-indigo-600" : ""} aria-hidden />
        <span className="hidden sm:inline">{corriendo ? "Recalculando…" : "Recalcular datos"}</span>
        <span className="sr-only sm:hidden">{corriendo ? "Recalculando" : "Recalcular datos"}</span>
      </button>
      <Dialogo abierto={abierto} onCerrar={() => !enviando && setAbierto(false)} titulo="¿Recalcular los datos del laboratorio?"
               acciones={
                 <>
                   <button type="button" onClick={() => setAbierto(false)} disabled={enviando}
                           className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50">Cancelar</button>
                   <button type="button" onClick={() => void confirmar()} disabled={enviando}
                           className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-70">
                     {enviando ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <RefreshCw size={14} aria-hidden />} Recalcular
                   </button>
                 </>
               }>
        <p>Vuelve a leer kubera y a calcular costos, elasticidades, precios propuestos y métricas. Es <b className="font-semibold text-slate-800">sólo lectura</b>: no cambia ningún precio ni escribe en ningún canal.</p>
        <p className="mt-2">Sin volver a leer Mercado Libre tarda cerca de 1.5 minutos. Mientras corre, las pestañas siguen mostrando la corrida anterior.</p>
        <label className="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
          <input type="checkbox" checked={conMl} onChange={(e) => setConMl(e.target.checked)} className="mt-0.5 h-4 w-4 accent-indigo-600" />
          <span className="text-[12.5px] text-slate-700">También volver a leer la API de Mercado Libre <span className="text-slate-500">(≈ 8,500 consultas; tarda mucho más)</span></span>
        </label>
        {error && <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[12.5px] text-rose-700" role="alert">{error}</p>}
      </Dialogo>
    </>
  );
}

/**
 * La regla del costo, siempre a la vista: Brandon (28-sep) — el costo del producto
 * del packing list NO aplica; cada contenedor cuesta 525,000 y se prorratea por m³
 * (`contenedor.incluye_mercancia` = true, llega en estado.json). Con false el
 * optimizador bloquea las bajadas que quedarían bajo el piso con el costo del panel.
 */
function BannerCosto({ estado }: { estado: Estado | null }) {
  if (!estado || estado.incluye_mercancia == null) return null;
  const pc = estado.parametros_clave ?? {};
  const monto = typeof pc.contenedor_mxn === "number" ? pc.contenedor_mxn : 525000;
  const piso = typeof pc.piso_margen === "number" ? pc.piso_margen : null;
  const detalle = [
    `Contenedor: $${monto.toLocaleString("es-MX")} ${pc.contenedor_incluye_iva === false ? "sin IVA" : ""}`.trim(),
    typeof pc.metodo_costo === "string" ? `método ${pc.metodo_costo === "volumetrico_real" ? "volumétrico (m³ de la pieza ÷ m³ del contenedor)" : pc.metodo_costo}` : "",
    piso != null ? `piso de margen ${Math.round(piso * 100)} %` : "",
  ].filter(Boolean).join(" · ");
  if (estado.incluye_mercancia) {
    return (
      <span className="inline-flex items-center gap-1.5 text-slate-600" title={`${detalle}. Si la mercancía se cobrara, las bajadas que quedarían bajo el piso con el costo del panel llevan el aviso «riesgo si se cobra la mercancía»; hoy no se bloquea ninguna.`}>
        <Boxes size={13} className="text-indigo-500" aria-hidden />
        <span>Costo = prorrateo de <b className="font-semibold tabular-nums text-slate-800">${monto.toLocaleString("es-MX")}</b> por contenedor (regla de Brandon); el producto no se incluye</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 font-semibold text-amber-800" title={detalle}>
      <Boxes size={13} aria-hidden /> El costo incluye la mercancía: se bloquean las bajadas que quedarían bajo el piso con el costo del panel
    </span>
  );
}

/** Qué fuente, con qué nombre y a partir de cuántos días se avisa que está vieja. */
const FRESCURA: { clave: string; nombre: string; limite: number }[] = [
  { clave: "ml_listings", nombre: "Publicaciones de ML", limite: 1 },
  { clave: "amazon_listings", nombre: "Amazon", limite: 3 },
  { clave: "walmart_listings", nombre: "Walmart", limite: 3 },
  { clave: "ventas", nombre: "Ventas", limite: 1 },
  { clave: "visitas_api", nombre: "Visitas (API ML)", limite: 2 },
  { clave: "competencia_serp", nombre: "Competencia (búsqueda)", limite: 30 },
  { clave: "competencia_best", nombre: "Competencia (más vendidos)", limite: 30 },
];

function avisosDe(e: Estado | null): { texto: string; titulo?: string }[] {
  if (!e) return [];
  const out: { texto: string; titulo?: string }[] = [];
  if (e.modo_local) out.push({ texto: "Modo local: el laboratorio corre sin llave de acceso", titulo: e.servidor?.aviso ?? "api.py sin LAB_ACCESS_KEY fuera de Railway (DISENO §7)." });
  if (!e.generado_at) out.push({ texto: "Todavía no hay una corrida del pipeline: las tablas saldrán vacías" });
  for (const f of FRESCURA) {
    const iso = e.frescura?.[f.clave];
    if (iso === undefined) continue;
    if (iso === null) { out.push({ texto: `${f.nombre}: sin fecha de captura` }); continue; }
    if (typeof iso !== "string") continue;
    const d = diasDesde(iso);
    if (d !== null && d > f.limite) {
      out.push({ texto: `${f.nombre}: último dato del ${dia(iso)} (hace ${d} d)`, titulo: "Es lo que hay en caché; no es lo que el canal muestra hoy." });
    }
  }
  for (const [nombre, et] of Object.entries(e.etapas ?? {})) {
    if (!et.ok) out.push({ texto: `La etapa «${nombre}» falló en la última corrida`, titulo: [et.error, ...(et.avisos ?? [])].filter(Boolean).join(" · ") });
  }
  return out;
}

export default function Marco({ children }: { children: ReactNode }) {
  const ruta = usePathname() ?? "";
  const { datos: estadoInicial, error } = usePedido<Estado>("/estado");
  const { estado, corriendo, terminado, lanzar, cerrarAviso } = useRecalculo(estadoInicial);
  const [bajando, setBajando] = useState(false);
  const avisos = useMemo(() => avisosDe(estado), [estado]);
  const puedeRecalcular = !!estado?.servidor?.recalcular?.permitido;

  async function exportar() {
    setBajando(true);
    try { await descargarCsvPrecios(); } finally { setTimeout(() => setBajando(false), 800); }
  }

  return (
    <CtxEstado.Provider value={estado}>
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-4 sm:h-16 sm:gap-4 sm:px-6">
          <Link href="/publicaciones" className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-600 to-indigo-400 text-white shadow-sm">
              <FlaskConical size={18} />
            </div>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-[15px] font-bold tracking-tight text-slate-900">Laboratorio de precios</div>
              <div className="-mt-0.5 hidden text-[10px] font-medium uppercase tracking-[0.18em] text-indigo-500 sm:block">Kubera · Omnicanal</div>
            </div>
          </Link>
          <span title="Solo lectura: SELECT en kubera, GET en las APIs. Nada se escribe ni se publica desde aquí."
                className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-semibold text-indigo-700 md:px-2.5">
            <Lock size={11} /><span className="hidden md:inline">Sandbox · solo lectura</span>
          </span>
          {USA_FIXTURES && (
            <span className="hidden rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wide text-amber-800 sm:inline">
              Fixtures
            </span>
          )}
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <span className="hidden text-right text-[11px] leading-tight text-slate-500 lg:block" title={estado?.generado_at ?? ""}>
              Datos al<br />
              <b className="font-semibold text-slate-700">{estado ? diaHora(estado.generado_at, "sin corrida") : error ? "sin conexión" : "…"}</b>
            </span>
            <button type="button" onClick={exportar} disabled={bajando}
                    title="Lista de precios recomendados (CSV). Es una PROPUESTA: todas salen con autorización pendiente."
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-60 sm:px-3">
              {bajando ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} aria-hidden />}
              <span className="hidden sm:inline">Exportar CSV</span>
              <span className="sr-only sm:hidden">Exportar CSV</span>
            </button>
            {puedeRecalcular && <BotonRecalcular corriendo={corriendo} onLanzar={lanzar} />}
            {estado?.servidor?.modo_auth === "llave" && (
              <button type="button" onClick={() => void cerrarSesion()} title="Cerrar sesión del laboratorio"
                      className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-700" aria-label="Cerrar sesión">
                <LogOut size={15} />
              </button>
            )}
          </div>
        </div>
        <nav className="sin-barra mx-auto flex max-w-[1600px] items-center gap-1 overflow-x-auto px-3 sm:px-5" aria-label="Secciones del laboratorio">
          {PESTANAS.map((p) => {
            const activo = ruta.startsWith(p.href);
            const Icono = p.icono;
            return (
              <Link key={p.href} href={p.href} aria-current={activo ? "page" : undefined}
                    className={`relative flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2.5 text-[13px] transition-colors ${
                      activo ? "font-semibold text-indigo-600" : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                    }`}>
                <Icono size={15} />
                {p.label}
                {activo && <span className="absolute inset-x-2 -bottom-px h-[3px] rounded-full bg-indigo-500" />}
              </Link>
            );
          })}
        </nav>
      </header>

      <div className="border-b border-slate-200 bg-white/60">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2 text-[11.5px] sm:px-6">
          <span className="inline-flex items-center gap-1.5 font-semibold text-indigo-700">
            <ShieldCheck size={13} /> Ningún precio se aplica sin autorización
          </span>
          <BannerCosto estado={estado} />
          {estado && (
            <span className="text-slate-500 lg:hidden">Datos al <b className="font-semibold text-slate-700">{diaHora(estado.generado_at, "sin corrida")}</b></span>
          )}
          {error && (
            <span className="inline-flex items-center gap-1.5 text-rose-700"><AlertTriangle size={12} /> {error}</span>
          )}
          {corriendo && (
            <span className="inline-flex items-center gap-1.5 font-semibold text-indigo-600" role="status"
                  title="El pipeline está recalculando; las pestañas muestran la corrida anterior hasta que termine.">
              <RefreshCw size={12} className="animate-spin" aria-hidden /> Recalculando…
              {estado?.servidor?.pipeline?.inicio && <span className="font-normal text-slate-500">desde {diaHora(estado.servidor.pipeline.inicio)}{estado.servidor.pipeline.sin_ml ? " · sin volver a leer ML" : " · con ML"}</span>}
            </span>
          )}
          {terminado && (
            <span className={`inline-flex items-center gap-1.5 font-semibold ${terminado.ok ? "text-emerald-700" : "text-rose-700"}`} role="status">
              {terminado.ok ? <CheckCircle2 size={13} aria-hidden /> : <AlertTriangle size={13} aria-hidden />}
              {terminado.ok ? `Recálculo terminado${terminado.duracion ? ` en ${Math.round(terminado.duracion)} s` : ""}.` : `El recálculo falló${terminado.error ? `: ${terminado.error}` : "."}`}
              {terminado.ok && (
                <button type="button" onClick={() => { olvidarCache(); window.location.reload(); }}
                        className="rounded-md bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-emerald-700">Recargar datos</button>
              )}
              <button type="button" onClick={cerrarAviso} className="text-[11px] font-normal text-slate-500 underline underline-offset-2">cerrar</button>
            </span>
          )}
          {/* En pantallas chicas los avisos de frescura se pliegan: ocupaban media pantalla. */}
          <span className="hidden sm:contents">
            {avisos.map((a) => (
              <span key={a.texto} title={a.titulo} className="inline-flex items-center gap-1.5 text-amber-800">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden /> {a.texto}
              </span>
            ))}
          </span>
          {avisos.length > 0 && (
            <details className="w-full sm:hidden">
              <summary className="cursor-pointer text-amber-800">
                <span className="font-semibold">{avisos.length} aviso{avisos.length === 1 ? "" : "s"}</span> de datos
              </summary>
              <ul className="mt-1 space-y-1">
                {avisos.map((a) => (
                  <li key={a.texto} className="flex items-start gap-1.5 text-amber-800">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden /> {a.texto}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </div>

      <main className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6">{children}</main>
    </CtxEstado.Provider>
  );
}

/** Encabezado de sección: título + una línea que dice qué se ve y de dónde sale. */
export function Encabezado({ titulo, descripcion, derecha }: { titulo: string; descripcion?: ReactNode; derecha?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-bold tracking-tight text-slate-900 sm:text-[22px]">{titulo}</h1>
        {descripcion && <p className="mt-0.5 max-w-3xl text-[13px] text-slate-500">{descripcion}</p>}
      </div>
      {derecha}
    </div>
  );
}
