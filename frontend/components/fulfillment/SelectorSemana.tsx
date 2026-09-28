"use client";

/**
 * El selector de SEMANA de FULLFILMENT: uno solo para las tres pantallas (Brandon,
 * 28-sep: "poder seleccionar la week en la que estamos indicando los días
 * correspondientes"). Crear FULL planea la semana en curso y enseña las anteriores de
 * consulta; Envíos filtra por la semana de la orden; Análisis, por la de la salida.
 */

import type { ReactNode } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { moverSemana, semanaPorClave, semanasHasta } from "./semana";
import type { Semana } from "./semana";

const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «lun 28 sep» de una fecha sin hora («2026-09-28»). */
function diaLargo(fecha: string): string {
  const [a, m, d] = fecha.split("-").map(Number);
  const f = new Date(Date.UTC(a, m - 1, d));
  return `${DIAS[f.getUTCDay()]} ${d} ${MESES[m - 1]}`;
}

export default function SelectorSemana({ valor, actual, onCambio, nota, extra }: {
  valor: string;
  actual: Semana;
  onCambio: (clave: string) => void;
  /** Qué quiere decir la semana en la pantalla de abajo. */
  nota?: string;
  extra?: ReactNode;
}) {
  const s = semanaPorClave(valor) ?? actual;
  const esActual = s.clave === actual.clave;
  const opciones = semanasHasta(actual, 26);
  if (!opciones.some((o) => o.clave === s.clave)) opciones.push(s);
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-slate-200 bg-white px-4 py-2.5 shadow-card">
      <span className="inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">
        <CalendarDays className="h-3.5 w-3.5" /> Semana
      </span>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onCambio(moverSemana(s.clave, -1).clave)} title="Semana anterior"
                className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <select value={s.clave} onChange={(ev) => onCambio(ev.target.value)} aria-label="Elegir la semana"
                className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[13px] font-bold text-slate-800">
          {opciones.map((o) => (
            <option key={o.clave} value={o.clave}>
              {o.semana} · {o.rango}{o.clave === actual.clave ? " · esta semana" : ""}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => onCambio(moverSemana(s.clave, 1).clave)} disabled={esActual}
                title="Semana siguiente" className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
      <span className="text-[12.5px] text-slate-600">
        <b className="font-semibold">{diaLargo(s.lunes)}</b> a <b className="font-semibold">{diaLargo(s.domingo)}</b>
      </span>
      {esActual ? (
        <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-[11px] font-bold text-indigo-700">
          semana en curso
        </span>
      ) : (
        <button type="button" onClick={() => onCambio(actual.clave)}
                className="rounded-full border border-slate-200 px-2.5 py-0.5 text-[11px] font-semibold text-indigo-600 hover:bg-indigo-50">
          ir a esta semana ({actual.semana})
        </button>
      )}
      {nota && <span className="text-[11.5px] text-slate-400">{nota}</span>}
      {extra && <span className="ml-auto flex items-center gap-2">{extra}</span>}
    </div>
  );
}
