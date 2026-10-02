"use client";

/**
 * CuadreFull — el libro diario de FULL: lo que avisó ML contra lo que movió la
 * foto del sync, por día y por cuenta. Si cuadran, la sincronización está sana.
 *
 * Por omisión NO cuenta ajustes ni retiros (medido el 2-oct: los ajustes siguen a
 * las ventas y los retiros salen de piezas ya apartadas; con ellos, el libro no
 * cuadra casi nunca). El interruptor los suma para ver la diferencia.
 */
import { useState } from "react";
import type { CuentaSel, DiaLibro, EstadoCuadre } from "./tipos";
import { CUADRE_CLS, conSigno } from "./tipos";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function etiqueta(dia: string, parcial: boolean, hora: string, hoy: string): string {
  const [a, m, d] = dia.split("-").map(Number);
  const corto = `${d} ${MESES[m - 1]}`;
  if (parcial) return `Hoy · ${corto} (hasta las ${hora})`;
  const ayer = new Date(Date.UTC(a, m - 1, d) + 86_400_000).toISOString().slice(0, 10) === hoy;
  return ayer ? `Ayer · ${corto}` : corto;
}

const COLS = "minmax(150px,1.3fr) repeat(6, minmax(64px,1fr)) minmax(80px,1fr) minmax(70px,1fr) minmax(80px,1fr) 104px";

export default function CuadreFull({ libro, umbral, cuenta, hora, hoy }: {
  libro: DiaLibro[];
  umbral: { cuadra: number; revisar: number };
  cuenta: CuentaSel;
  hora: string;
  hoy: string;
}) {
  const [contar, setContar] = useState(false);
  const filas = libro.filter((f) => cuenta === "ambas" || f.cuenta === cuenta);
  const estado = (f: DiaLibro): EstadoCuadre => (contar ? f.estado_todo : f.estado_vendible);
  const cuadran = filas.filter((f) => estado(f) === "cuadra").length;
  const cuentas = Array.from(new Set(filas.map((f) => f.cuenta)));

  return (
    <section id="libro" aria-labelledby="t-libro" className="flex scroll-mt-4 flex-col gap-4 rounded-2xl bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <h2 id="t-libro" className="text-[17px] font-bold text-slate-900">Libro diario: avisos de ML contra la foto del sync</h2>
          <p className="mt-0.5 text-[13px] leading-5 text-slate-600">
            Cada día, lo que avisó Mercado Libre contra lo que movió el stock FULL que lee el sync. Si cuadran, la sincronización está sana.
          </p>
        </div>
        <button type="button" role="switch" aria-checked={contar} onClick={() => setContar((v) => !v)}
          className="flex h-10 items-center gap-2.5 rounded-xl border border-slate-300 bg-white px-3.5 text-sm font-semibold text-slate-800 hover:bg-slate-50">
          <span className={`relative h-5 w-9 rounded-full transition ${contar ? "bg-indigo-600" : "bg-slate-300"}`} aria-hidden>
            <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${contar ? "left-[18px]" : "left-0.5"}`} />
          </span>
          Contar ajustes y retiros
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl bg-slate-50 px-4 py-3">
        <span className="flex items-baseline gap-2">
          <span className={`text-[26px] font-bold tabular-nums ${cuadran * 3 >= filas.length * 2 ? "text-indigo-800" : "text-orange-800"}`}>
            {cuadran} de {filas.length}
          </span>
          <span className="text-sm text-slate-700">días cuadran a ±{umbral.cuadra} pzs</span>
        </span>
        <span className="min-w-[280px] flex-1 text-[13px] leading-5 text-slate-600">
          {contar
            ? "Contando ajustes y retiros, los avisos bajan mucho más que la foto: así el libro no sirve para vigilar."
            : "Sin ajustes ni retiros, lo que avisa ML explica la foto casi al día. Lo que no cuadra son días que vale la pena abrir."}
        </span>
        <span className="flex flex-wrap gap-1.5 text-xs font-semibold">
          <span className={`rounded-full px-2.5 py-0.5 ${CUADRE_CLS.cuadra.chip}`}>Cuadra · hasta {umbral.cuadra}</span>
          <span className={`rounded-full px-2.5 py-0.5 ${CUADRE_CLS.revisar.chip}`}>Revisar · hasta {umbral.revisar}</span>
          <span className={`rounded-full px-2.5 py-0.5 ${CUADRE_CLS.no_cuadra.chip}`}>No cuadra · más de {umbral.revisar}</span>
        </span>
      </div>

      {cuentas.map((c) => {
        const delDia = filas.filter((f) => f.cuenta === c).slice().reverse();
        return (
          <div key={c} className="overflow-x-auto">
            <h3 className="mb-1.5 text-sm font-bold text-slate-900">{delDia[0]?.nombre}</h3>
            <div className="min-w-[1000px]">
              <div className="grid items-end gap-2 border-b border-slate-200 px-2 pb-2 text-right text-[11px] font-semibold uppercase tracking-wide text-slate-500"
                style={{ gridTemplateColumns: COLS }}>
                <span className="text-left">Día</span><span>Llegó</span><span>Vendido</span><span>Canceló</span><span>Traslado y cuarent.</span>
                <span className={contar ? "" : "opacity-50"}>Ajustes ML</span><span className={contar ? "" : "opacity-50"}>Retiros</span>
                <span className="text-indigo-800">Neto avisos</span><span className="text-indigo-800">Δ foto</span><span>Diferencia</span>
                <span className="text-center">Estado</span>
              </div>
              {delDia.map((f) => {
                const neto = contar ? f.todo : f.vendible;
                const dif = contar ? f.dif_todo : f.dif_vendible;
                const e = CUADRE_CLS[estado(f)];
                return (
                  <div key={f.dia} className={`grid items-center gap-2 border-b border-slate-100 px-2 py-1.5 text-right text-[13px] tabular-nums ${e.fila}`}
                    style={{ gridTemplateColumns: COLS }}>
                    <span className="text-left font-semibold text-slate-700">{etiqueta(f.dia, f.parcial, hora, hoy)}</span>
                    <span className="text-sky-800">{conSigno(f.grupos.llego)}</span>
                    <span className="text-orange-800">{conSigno(f.grupos.vendido)}</span>
                    <span className="text-slate-600">{conSigno(f.grupos.cancelado)}</span>
                    <span className="text-slate-600">{conSigno(f.grupos.traslado + f.grupos.cuarentena)}</span>
                    <span className={`text-amber-800 ${contar ? "" : "opacity-50"}`}>{conSigno(f.grupos.ajuste)}</span>
                    <span className={`text-orange-800 ${contar ? "" : "opacity-50"}`}>{conSigno(f.grupos.retiro)}</span>
                    <span className="font-bold text-slate-900">{conSigno(neto)}</span>
                    <span className="font-bold text-slate-900" title={`${f.cambios_foto} cambios de stock FULL anotados por el sync`}>{conSigno(f.foto)}</span>
                    <span className={`font-bold ${e.dif}`}>{conSigno(dif)}</span>
                    <span className="text-center"><span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${e.chip}`}>{e.texto}</span></span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      <p className="text-xs leading-[18px] text-slate-600">
        Por qué no cuentan por omisión: los <b>ajustes de ML</b> siguen a las ventas (uno a uno en los SKUs más ajustados) y no mueven lo vendible;
        los <b>retiros</b> salen de piezas que ML ya había apartado. Avisos: <span className="font-mono">fbm_stock_operations</span> de las dos cuentas,
        cada operación una vez. Foto: los cambios de stock FULL que anota el sync, sin las filas padre de las publicaciones con variantes.
        Una «Diferencia» positiva es que la foto bajó menos (o subió más) de lo que avisó ML.
      </p>
    </section>
  );
}
