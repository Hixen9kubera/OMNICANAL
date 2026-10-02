"use client";

/**
 * HorarioFull — los avisos de la bodega de ML de las últimas 24 h, del más
 * reciente al más viejo, uno por operación (ML reenvía; aquí cuenta una vez).
 * Se filtran por tipo; tocar un SKU abre su trazabilidad en el carril FULL.
 */
import { useMemo, useState } from "react";
import type { AvisoFull, CuentaSel, GrupoFull } from "./tipos";
import { CUENTA_CHIP, GRUPO_FULL, conSigno } from "./tipos";

const FILTROS: { id: string; texto: string; grupos: GrupoFull[] | null }[] = [
  { id: "todos", texto: "Todos", grupos: null },
  { id: "vendido", texto: "Ventas", grupos: ["vendido"] },
  { id: "llego", texto: "Llegadas", grupos: ["llego"] },
  { id: "ajuste", texto: "Ajustes de ML", grupos: ["ajuste"] },
  { id: "retiro", texto: "Retiros", grupos: ["retiro"] },
  { id: "otros", texto: "Traslados y otros", grupos: ["traslado", "cuarentena", "cancelado", "otro"] },
];
const PASO = 40;

export default function HorarioFull({ avisos, hoy, cuenta, onSku }: {
  avisos: AvisoFull[];
  hoy: string;
  cuenta: CuentaSel;
  onSku: (sku: string) => void;
}) {
  const [filtro, setFiltro] = useState("todos");
  const [limite, setLimite] = useState(14);
  const deCuenta = useMemo(() => avisos.filter((a) => cuenta === "ambas" || a.cuenta === cuenta), [avisos, cuenta]);
  const cuenta_ = (f: (typeof FILTROS)[number]) => deCuenta.filter((a) => !f.grupos || f.grupos.includes(a.grupo)).length;
  const activo = FILTROS.find((f) => f.id === filtro) ?? FILTROS[0];
  const filas = deCuenta.filter((a) => !activo.grupos || activo.grupos.includes(a.grupo));

  return (
    <section aria-labelledby="t-horario-full" className="flex min-w-0 flex-col gap-3 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="t-horario-full" className="text-[17px] font-bold text-slate-900">Horario de FULL</h2>
        <span className="text-xs text-slate-600">Últimas 24 h · cada operación de ML una sola vez</span>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtrar avisos">
        {FILTROS.map((f) => (
          <button key={f.id} type="button" aria-pressed={filtro === f.id} onClick={() => { setFiltro(f.id); setLimite(14); }}
            className={`h-[34px] rounded-full border px-3 text-[13px] font-semibold ${
              filtro === f.id ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
            {f.texto} · {cuenta_(f).toLocaleString("es-MX")}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="grid gap-2.5 border-b border-slate-200 px-2 pb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500"
            style={{ gridTemplateColumns: "64px 92px minmax(0,1.2fr) minmax(0,1.2fr) 48px minmax(0,1.5fr)" }}>
            <span>Hora</span><span>Cuenta</span><span>SKU</span><span>Movimiento</span><span className="text-right">Pzs</span><span>Qué significa</span>
          </div>
          {filas.length === 0 && <p className="px-2 py-5 text-sm text-slate-500">Sin avisos de este tipo en las últimas 24 h.</p>}
          {filas.slice(0, limite).map((a, i) => (
            <div key={`${a.hora}-${a.sku}-${i}`} className="grid items-center gap-2.5 border-b border-slate-100 px-2 py-2 text-[13px]"
              style={{ gridTemplateColumns: "64px 92px minmax(0,1.2fr) minmax(0,1.2fr) 48px minmax(0,1.5fr)" }}>
              <span className="tabular-nums text-slate-600">
                {a.dia === hoy ? a.hora.slice(11, 16) : <><span className="text-[11px]">ayer </span>{a.hora.slice(11, 16)}</>}
              </span>
              <span><span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${CUENTA_CHIP[a.cuenta] ?? "bg-slate-100 text-slate-700"}`}>{a.nombre}</span></span>
              {a.sku ? (
                <button type="button" onClick={() => onSku(a.sku as string)} title="Ver su trazabilidad"
                  className="truncate text-left font-mono text-xs font-semibold text-indigo-800 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                  {a.sku}
                </button>
              ) : <span className="text-xs text-rose-700">sin SKU</span>}
              <span className="flex min-w-0 items-center gap-2 font-semibold text-slate-800">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: GRUPO_FULL[a.grupo].color }} aria-hidden />
                <span className="truncate" title={a.tipo}>{a.texto}</span>
              </span>
              <span className={`text-right font-bold tabular-nums ${a.x > 0 ? "text-sky-800" : a.x < 0 ? "text-orange-800" : "text-slate-500"}`}>{conSigno(a.x)}</span>
              <span className="truncate text-xs text-slate-600" title={a.sig}>{a.sig}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
        <span>Mostrando {Math.min(limite, filas.length).toLocaleString("es-MX")} de {filas.length.toLocaleString("es-MX")}.</span>
        {filas.length > limite && (
          <button type="button" onClick={() => setLimite((l) => l + PASO)}
            className="h-8 rounded-full border border-slate-300 px-3 font-semibold text-slate-700 hover:bg-slate-50">
            Ver {Math.min(PASO, filas.length - limite)} más
          </button>
        )}
      </div>
    </section>
  );
}
