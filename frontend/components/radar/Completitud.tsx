"use client";

// Completitud.tsx — Qué tan completa es la contribución que pinta el radar, y
// los parámetros con que decide. Las dos cosas se muestran porque el número
// sin su procedencia engaña: mientras falten publicidad y almacenaje Full, la
// contribución es una COTA SUPERIOR, y los umbrales son decisión, no medición.

import { SlidersHorizontal } from "lucide-react";

import type { RadarCompletitud, RadarParametros } from "@/lib/api";
import { ETIQUETA_PARAMETRO, etiquetaCompletitud, porcentaje, valorParametro } from "./formato";

type Tono = "real" | "estimado" | "sin_dato";

const TONO: Record<Tono, { caja: string; texto: string }> = {
  real: { caja: "bg-[#F6F7FB] border border-transparent", texto: "text-[#0B6B58]" },
  estimado: { caja: "bg-[#F6F7FB] border border-transparent", texto: "text-[#8A5A00]" },
  sin_dato: { caja: "bg-white border border-dashed border-[#98A2B3]", texto: "text-[#4A5163]" },
};

/** "Real en 71 % · resto estimado" a partir del % medido que manda el backend. */
function medido(pct: number | null | undefined): { texto: string; tono: Tono } {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return { texto: "Sin dato", tono: "sin_dato" };
  if (pct >= 100) return { texto: "Real", tono: "real" };
  if (pct <= 0) return { texto: "Estimada en todas", tono: "estimado" };
  return { texto: `Real en ${porcentaje(pct)} · resto estimado`, tono: pct >= 50 ? "real" : "estimado" };
}

function textual(v: string | null | undefined, sinDatoTexto: string, estimadoTexto: string): { texto: string; tono: Tono } {
  const e = etiquetaCompletitud(v);
  if (e === null || e === "Sin dato") return { texto: sinDatoTexto, tono: "sin_dato" };
  if (e === "Estimado") return { texto: estimadoTexto, tono: "estimado" };
  if (e === "Real") return { texto: "Real", tono: "real" };
  return { texto: e, tono: "estimado" };
}

function Pieza({ titulo, texto, tono }: { titulo: string; texto: string; tono: Tono }) {
  const t = TONO[tono];
  return (
    <div className={`flex min-w-[128px] flex-col gap-1 rounded-lg px-3 py-2 ${t.caja}`}>
      <span className="text-xs text-[#4A5163]">{titulo}</span>
      <span className={`text-xs font-semibold ${t.texto}`}>{texto}</span>
    </div>
  );
}

export function FranjaCompletitud({ completitud }: { completitud: RadarCompletitud | null | undefined }) {
  const c = completitud ?? null;
  const comision = medido(c?.comision_real_pct);
  const envio = medido(c?.envio_real_pct);
  // Sin peso no hay ni estimación: eso se dice aparte, no se esconde en "estimado".
  if (typeof c?.envio_sin_dato_pct === "number" && c.envio_sin_dato_pct > 0) {
    envio.texto = `${envio.texto} · sin dato en ${porcentaje(c.envio_sin_dato_pct)}`;
  }
  const devolucion = textual(c?.devolucion, "Sin dato", "Tasa × envío estimado");
  const publicidad = textual(c?.publicidad, "Sin dato · llega en F2", "Estimada");
  const full = textual(c?.full, "Sin dato · llega en F2", "Estimado");
  return (
    <section
      aria-label="Qué tan completa es la contribución"
      className="flex flex-col gap-4 rounded-xl border border-[#E4E7EE] bg-white px-5 py-4 lg:flex-row lg:items-center lg:gap-7"
    >
      <div className="flex flex-col gap-1 lg:w-[320px] lg:shrink-0">
        <div className="font-semibold text-slate-900">Contribución por pieza</div>
        <div className="text-[13px] leading-snug text-[#4A5163]">
          Precio sin IVA − comisión − envío − Full − publicidad − devolución esperada.
        </div>
      </div>
      <div className="flex flex-1 flex-wrap gap-2.5">
        <Pieza titulo="Comisión" {...comision} />
        <Pieza titulo="Envío" {...envio} />
        <Pieza titulo="Devoluciones" {...devolucion} />
        <Pieza titulo="Publicidad" {...publicidad} />
        <Pieza titulo="Almacenaje Full" {...full} />
      </div>
      <div className="text-[13px] leading-snug text-[#4A5163] lg:w-[240px] lg:shrink-0">
        Mientras falten publicidad y Full, la contribución es una{" "}
        <strong className="text-slate-900">cota superior</strong>.
      </div>
    </section>
  );
}

/** Los PARAMS del servicio, rotulados como lo que son: decisiones de negocio. */
export function ParametrosRadar({ parametros }: { parametros: RadarParametros | null | undefined }) {
  const claves = Object.keys(parametros ?? {});
  if (!parametros || claves.length === 0) return null;
  // Primero los conocidos, en el orden de la especificación; al final lo nuevo.
  const orden = [
    ...Object.keys(ETIQUETA_PARAMETRO).filter((k) => k in parametros),
    ...claves.filter((k) => !(k in ETIQUETA_PARAMETRO)),
  ];
  return (
    <details className="group rounded-xl border border-[#E4E7EE] bg-white">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-sm font-semibold text-slate-700">
        <SlidersHorizontal size={15} className="text-slate-400" aria-hidden />
        Parámetros del radar
        <span className="rounded-md bg-[#FEF3D6] px-1.5 py-px text-[11px] font-semibold text-[#8A5A00]">
          decisión, no medición
        </span>
        <span className="ml-auto text-xs font-medium text-slate-400 group-open:hidden">Ver</span>
        <span className="ml-auto hidden text-xs font-medium text-slate-400 group-open:inline">Ocultar</span>
      </summary>
      <dl className="grid gap-x-8 gap-y-2 border-t border-[#EEF0F4] px-5 py-4 text-sm sm:grid-cols-2 xl:grid-cols-3">
        {orden.map((k) => (
          <div key={k} className="flex items-baseline justify-between gap-3">
            <dt className="text-[#4A5163]">{ETIQUETA_PARAMETRO[k]?.etiqueta ?? k}</dt>
            <dd className="font-semibold tabular-nums text-slate-900">{valorParametro(k, parametros[k])}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
