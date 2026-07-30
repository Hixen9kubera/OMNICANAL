"use client";

/**
 * Placeholder HONESTO de una sección de Fulfillment todavía sin construir.
 *
 * No es un "próximamente" vacío: dice qué va a mostrar, DE DÓNDE saldrán los
 * datos y qué falta para poder construirla. Así el esqueleto sirve como
 * documentación viva mientras la sección se implementa — y si alguien entra
 * por la URL, entiende qué está viendo en vez de una página en blanco.
 */

import type { LucideIcon } from "lucide-react";
import { CircleDashed } from "lucide-react";

export interface Pendiente {
  titulo: string;
  icono: LucideIcon;
  resumen: string;
  /** Qué va a mostrar la sección, en bullets cortos. */
  contenido: string[];
  /** Tabla/vista → estado. `listo` pinta verde; el resto, ámbar. */
  fuentes: { nombre: string; detalle: string; listo: boolean }[];
  /** Lo que hace falta ANTES de poder construirla (vacío = nada). */
  bloqueos?: string[];
}

export default function FulfillmentPendiente({ p }: { p: Pendiente }) {
  const Icono = p.icono;
  const listas = p.fuentes.filter((f) => f.listo).length;
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-indigo-50 p-2.5 text-indigo-600">
            <Icono size={22} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-bold text-slate-900">{p.titulo}</h2>
              <span className="flex items-center gap-1.5 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
                <CircleDashed size={11} /> Por construir
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-500">{p.resumen}</p>
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Qué va a mostrar
          </div>
          <ul className="space-y-1.5 text-sm text-slate-600">
            {p.contenido.map((c) => (
              <li key={c} className="flex gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
                {c}
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Datos que necesita
            </span>
            <span className="text-[11px] font-semibold text-slate-400">
              {listas} de {p.fuentes.length} listas
            </span>
          </div>
          <ul className="space-y-2">
            {p.fuentes.map((f) => (
              <li key={f.nombre} className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-bold ${f.listo ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                  {f.listo ? "LISTA" : "FALTA"}
                </span>
                <span className="min-w-0">
                  <code className="text-[12px] font-semibold text-slate-700">{f.nombre}</code>
                  <span className="text-slate-500"> — {f.detalle}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {p.bloqueos && p.bloqueos.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-amber-700">
            Antes de construirla hace falta
          </div>
          <ul className="space-y-1.5 text-sm text-amber-900">
            {p.bloqueos.map((b) => (
              <li key={b} className="flex gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-amber-500" />
                {b}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
