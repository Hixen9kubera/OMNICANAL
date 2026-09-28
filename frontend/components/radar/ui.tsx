"use client";

// ui.tsx — Piezas chicas del radar de precios: chips, etiquetas y estados.

import Link from "next/link";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Ban,
  CircleHelp,
  Equal,
  EyeOff,
  Info,
  Loader2,
  Lock,
  RotateCw,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import type { RadarDireccion, RadarExperiencia } from "@/lib/api";
import {
  META_DIRECCION,
  META_EXPERIENCIA,
  META_PROCEDENCIA,
  type Procedencia,
} from "./formato";

export const ICONO_DIRECCION: Record<RadarDireccion, LucideIcon> = {
  subir: ArrowUp,
  bajar: ArrowDown,
  mantener: Equal,
  caro_justificado: ShieldCheck,
  no_competir: Ban,
  sin_referencia: CircleHelp,
};

/** Chip de dirección: icono + texto, nunca solo color. */
export function ChipDireccion({ direccion, grande = false }: { direccion: RadarDireccion; grande?: boolean }) {
  const meta = META_DIRECCION[direccion] ?? META_DIRECCION.sin_referencia;
  const Icono = ICONO_DIRECCION[direccion] ?? CircleHelp;
  return (
    <span
      className={[
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full font-semibold",
        grande ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs",
        meta.chip,
      ].join(" ")}
    >
      <Icono size={grande ? 15 : 12} strokeWidth={2.4} aria-hidden />
      {meta.etiqueta}
    </span>
  );
}

/** Real / Parcial / Estimado / Sin dato. */
export function EtiquetaProcedencia({ estado }: { estado: Procedencia | null | undefined }) {
  const meta = META_PROCEDENCIA[estado ?? "sin_dato"] ?? META_PROCEDENCIA.sin_dato;
  return (
    <span className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-px text-[11px] font-semibold ${meta.clase}`}>
      {meta.etiqueta}
    </span>
  );
}

/** Lo que se pinta cuando el dato NO existe. Nunca un 0. */
export function SinDato({ texto = "Sin dato" }: { texto?: string }) {
  return <span className="text-xs font-medium italic text-slate-400">{texto}</span>;
}

/** Un valor ya formateado, o "Sin dato" si el formateador devolvió null. */
export function Valor({ texto, className = "" }: { texto: string | null; className?: string }) {
  if (texto === null) return <SinDato />;
  return <span className={className}>{texto}</span>;
}

export function PuntoExperiencia({ experiencia }: { experiencia: RadarExperiencia | null | undefined }) {
  const meta = META_EXPERIENCIA[experiencia ?? "sin_datos"] ?? META_EXPERIENCIA.sin_datos;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2 w-2 shrink-0 rounded-full ${meta.punto}`} aria-hidden />
      {experiencia ? meta.etiqueta : "Sin datos"}
    </span>
  );
}

/** Barra de sub-pestañas del radar. Contenedores y Propuestas llegan después. */
export function SubnavRadar({ ambiente }: { ambiente?: string | null }) {
  const esProduccion = !ambiente || /^prod/i.test(ambiente);
  return (
    <div className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-[1800px] flex-wrap items-stretch gap-x-6 gap-y-2 px-4 sm:px-6">
        <nav aria-label="Secciones del radar" className="flex items-stretch gap-6">
          <Link
            href="/radar"
            aria-current="page"
            className="flex items-center border-b-2 border-indigo-500 py-3 text-sm font-semibold text-slate-900"
          >
            Precios
          </Link>
          <PestanaFutura etiqueta="Contenedores" fase="F4" />
          <PestanaFutura etiqueta="Propuestas" fase="F6" />
        </nav>
        <div className="flex flex-1 flex-wrap items-center justify-end gap-2 py-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#EEF0FF] px-2.5 py-1 text-xs font-semibold text-[#3730A3]">
            <EyeOff size={13} aria-hidden />
            Oculta · solo admin
          </span>
          {!esProduccion && (
            <span className="rounded-full bg-[#FEF3D6] px-2.5 py-1 text-xs font-semibold text-[#7A4A00]">
              Ambiente: {ambiente}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function PestanaFutura({ etiqueta, fase }: { etiqueta: string; fase: string }) {
  return (
    <span
      aria-disabled="true"
      title={`Llega en ${fase}`}
      className="flex cursor-not-allowed items-center gap-1.5 border-b-2 border-transparent py-3 text-sm font-medium text-slate-400"
    >
      {etiqueta}
      <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
        Llega en {fase}
      </span>
    </span>
  );
}

export function AvisoSoloLectura() {
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-[#4A5163]">
      <Info size={16} aria-hidden />
      Solo lectura: esta pantalla no cambia ningún precio
    </span>
  );
}

export function Cargando({ texto = "Cargando…" }: { texto?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Loader2 size={18} className="animate-spin text-indigo-500" aria-hidden />
      {texto}
    </div>
  );
}

export function CajaError({ mensaje, onReintentar }: { mensaje: string; onReintentar?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
      <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">{mensaje}</span>
      {onReintentar && (
        <button
          type="button"
          onClick={onReintentar}
          className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
        >
          <RotateCw size={13} aria-hidden />
          Reintentar
        </button>
      )}
    </div>
  );
}

/**
 * La pantalla de "no te toca". No dice qué hay detrás ni pide datos: a quien no
 * es admin no se le confirma siquiera que la sección existe.
 */
export function NoDisponible() {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center gap-3 px-4 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
        <Lock size={22} aria-hidden />
      </span>
      <h1 className="text-lg font-semibold text-slate-800">No disponible</h1>
      <p className="text-sm text-slate-500">Esta página no está disponible para tu cuenta.</p>
      <Link href="/omnicanal" className="text-sm font-semibold text-indigo-600 hover:text-indigo-700">
        Ir al panel
      </Link>
    </main>
  );
}
