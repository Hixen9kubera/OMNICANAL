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
import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle, BarChart3, Boxes, Download, FlaskConical, History, Loader2, Lock, LogOut, RefreshCw, ShieldCheck, Tags, TrendingUp,
} from "lucide-react";
import { cerrarSesion, descargarCsvPrecios, USA_FIXTURES } from "@/lib/api";
import { diaHora, diasDesde, dia } from "@/lib/formato";
import type { Estado } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

const CtxEstado = createContext<Estado | null>(null);
export const useEstado = () => useContext(CtxEstado);

const PESTANAS = [
  { href: "/publicaciones", label: "Publicaciones", icono: Tags },
  { href: "/precios", label: "Precios óptimos", icono: TrendingUp },
  { href: "/metricas", label: "Métricas", icono: BarChart3 },
  { href: "/packing", label: "Packing list", icono: Boxes },
  { href: "/historial", label: "Historial", icono: History },
];

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
    const d = diasDesde(iso);
    if (d !== null && d > f.limite) {
      out.push({ texto: `${f.nombre}: último dato del ${dia(iso)} (hace ${d} d)`, titulo: "Es lo que hay en caché; no es lo que el canal muestra hoy." });
    }
  }
  for (const [nombre, et] of Object.entries(e.etapas ?? {})) {
    if (!et.ok) out.push({ texto: `La etapa «${nombre}» falló en la última corrida`, titulo: (et.avisos ?? []).join(" · ") });
  }
  return out;
}

export default function Marco({ children }: { children: ReactNode }) {
  const ruta = usePathname() ?? "";
  const { datos: estado, error } = usePedido<Estado>("/estado");
  const [bajando, setBajando] = useState(false);
  const avisos = useMemo(() => avisosDe(estado), [estado]);

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
              {bajando ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
              <span className="hidden sm:inline">Exportar CSV</span>
            </button>
            {estado?.servidor?.modo_auth !== "abierto" && (
              <button type="button" onClick={() => void cerrarSesion()} title="Cerrar sesión del laboratorio"
                      className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Cerrar sesión">
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
          {estado && (
            <span className="text-slate-500 lg:hidden">Datos al <b className="font-semibold text-slate-700">{diaHora(estado.generado_at, "sin corrida")}</b></span>
          )}
          {error && (
            <span className="inline-flex items-center gap-1.5 text-rose-700"><AlertTriangle size={12} /> {error}</span>
          )}
          {estado?.corriendo && (
            <span className="inline-flex items-center gap-1.5 text-indigo-600" title="El pipeline está recalculando; al terminar, recarga la página.">
              <RefreshCw size={12} className="animate-spin" /> Recalculando…
            </span>
          )}
          {avisos.map((a) => (
            <span key={a.texto} title={a.titulo} className="inline-flex items-center gap-1.5 text-amber-800">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> {a.texto}
            </span>
          ))}
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
