"use client";

/**
 * FULLFILMENT · Envíos (un renglón por envío con su rail de 7 etapas) y el
 * Detalle de un envío (por SKU, con las siete columnas de cantidad).
 *
 * Reglas que esta vista NO rompe:
 *   · «envío sin enlazar» es un estado de primera: ni 0 ni escondido;
 *   · «en recepción» (ámbar) y «rechazadas» (rosa) son columnas distintas y
 *     la segunda jamás se deriva restando;
 *   · las cajas llevan «~» y «estimadas»: Odoo no registra cajas;
 *   · nunca «en tránsito»: «Recolectado» es la validación del picking.
 */

import { useState } from "react";
import { ArrowLeft, Copy, Download } from "lucide-react";
import { lineasDe } from "./datosDiseno";
import {
  ChipCanal, Ceja, FONDO_RAYADO, Rail, Tarjeta, num, pasosDe, tasaDe,
} from "./ui";
import type { Envio } from "./tipos";

export function TablaEnvios({
  envios, total, onAbrir,
}: { envios: Envio[]; total: number; onAbrir: (e: Envio) => void }) {
  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Ceja>Un renglón por envío · {envios.length} de {total} órdenes de salida</Ceja>
          <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
            Envíos a los almacenes del marketplace
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">Cada etapa lleva su fecha o su estado. Ninguna se deduce de otra.</span>
          <button type="button" disabled
                  title="Pendiente: el CSV sale cuando la historia de recepciones tenga al menos una semana."
                  className="flex cursor-not-allowed items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-300">
            <Download className="h-4 w-4" /> Exportar
          </button>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[1180px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">Envío · orden</th>
              <th className="px-3.5 py-2.5">Canal · cuenta</th>
              <th className="w-[46%] px-3.5 py-2.5">Rail de etapas</th>
              <th className="px-3.5 py-2.5 text-right">Piezas</th>
              <th className="px-3.5 py-2.5 text-right"
                  title="Odoo no registra cajas: se derivan de las piezas y del factor de caja.">Cajas est.</th>
              <th className="px-3.5 py-2.5 text-right">Tasa de recepción</th>
              <th className="px-3.5 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {envios.map((e, i) => {
              const tasa = tasaDe(e);
              return (
                <tr key={`${e.orden}-${i}`} className="border-t border-slate-100 align-top">
                  <td className="px-3.5 py-[11px]">
                    <div className={`font-mono text-[13px] font-bold ${e.envio ? "text-slate-900" : "text-amber-700"}`}>
                      {e.envio ?? "sin número"}
                    </div>
                    <div className="mt-0.5 text-[11.5px] text-slate-400">
                      Odoo {e.orden ?? "—"} · {e.kam ?? "—"}
                    </div>
                    {e.estado === "sinEnlazar" && (
                      <div className="mt-1.5 inline-flex rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[.04em] text-amber-700">
                        envío sin enlazar
                      </div>
                    )}
                  </td>
                  <td className="px-3.5 py-[11px]">
                    <ChipCanal canal={e.canal} cuenta={e.cuenta} />
                    <div className="mt-1 text-xs font-semibold text-slate-600">
                      {e.canal === "amazon" && e.cuenta ? `${e.cuenta} (FBA)` : e.cuenta ?? "—"}
                    </div>
                  </td>
                  <td className="px-3.5 py-[11px] align-middle">
                    <Rail pasos={pasosDe(e)} />
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <div className="font-mono text-sm font-extrabold tabular-nums text-slate-900">{num(e.piezas)}</div>
                    <div className="text-[11px] text-slate-400">de {num(e.pedidas)} pedidas</div>
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <div className="font-mono text-[13px] font-bold tabular-nums text-slate-500">
                      {e.cajas === null ? "—" : `~${e.cajas}`}
                    </div>
                    <div className="text-[11px] text-slate-300">estimadas</div>
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <div className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-bold ${tasa.clase}`}
                         style={tasa.rayada ? { background: FONDO_RAYADO } : undefined}>
                      {tasa.texto}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400">{tasa.nota}</div>
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <button type="button" onClick={() => onAbrir(e)} disabled={e.estado === "wfs"}
                            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-white">
                      Detalle
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-slate-400">
        <span><b>Recolectado</b> es la validación del picking en Odoo, no la recolección física — Odoo no guarda esa hora.</span>
        <span>No decimos «en tránsito» hasta que el marketplace confirme que la mercancía viaja.</span>
      </div>
    </Tarjeta>
  );
}

export function DetalleEnvio({ envio: e, onVolver }: { envio: Envio; onVolver: () => void }) {
  const [copiado, setCopiado] = useState(false);
  const lineas = lineasDe(e);
  const cerrado = e.estado === "cerrado" || e.estado === "sinVenta";
  const suma = (f: (l: (typeof lineas)[number]) => number | null) =>
    lineas.reduce<number | null>((a, l) => {
      const v = f(l);
      return v === null || a === null ? null : a + v;
    }, 0);
  const totEnviadas = suma((l) => l.enviadas);
  const totRecibidas = suma((l) => l.recibidas);
  const totRechazadas = suma((l) => l.rechazadas);
  const totCajas = suma((l) => l.cajas);

  const copiar = () => {
    void navigator.clipboard?.writeText(e.envio ?? "sin número de envío");
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  };

  const sinDato = <span className="font-mono text-[11px] font-bold text-slate-400">sin dato</span>;
  const sReg = (
    <span className="rounded border border-dashed border-slate-300 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase text-slate-400"
          style={{ background: FONDO_RAYADO }}>s/reg</span>
  );

  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Ceja>Detalle del envío</Ceja>
          <h2 className="mt-1 flex flex-wrap items-center gap-2.5 text-[20px] font-extrabold tracking-tight text-slate-900">
            <span className={`font-mono ${e.envio ? "" : "text-amber-700"}`}>{e.envio ?? "sin número"}</span>
            <ChipCanal canal={e.canal} cuenta={e.cuenta} />
            <span className="text-sm font-semibold text-slate-500">{e.cuenta ?? "—"}</span>
          </h2>
          <p className="mt-1 text-[13px] text-slate-500">
            Orden de salida Odoo <b className="text-slate-700">{e.orden ?? "—"}</b> · armó{" "}
            <b className="text-slate-700">{e.kam ?? "—"}</b> · {num(e.piezas)} piezas en {lineas.length} SKUs
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onVolver}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <ArrowLeft className="h-4 w-4" /> Volver a Envíos
          </button>
          <button type="button" onClick={copiar}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-600 hover:bg-indigo-50">
            <Copy className="h-4 w-4" /> {copiado ? "Copiado" : "Copiar número"}
          </button>
        </div>
      </div>

      <div className="mt-4">
        <Rail pasos={pasosDe(e)} grande />
      </div>

      {e.estado === "sinEnlazar" && (
        <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-amber-900">
            <b>Envío sin enlazar.</b> La referencia de la orden de Odoo no trae número de envío de Mercado
            Libre, así que no hay con qué cruzar lo que salió contra lo que el almacén recibió. Esta orden{" "}
            <b>no puede tener tasa de recepción</b> — y eso no es un cero.
          </p>
        </div>
      )}

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[980px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">SKU · producto</th>
              <th className="px-3.5 py-2.5">{e.canal === "amazon" ? "ASIN" : "MLM"}</th>
              <th className="px-3.5 py-2.5 text-right">Solicit.</th>
              <th className="px-3.5 py-2.5 text-right">Valid.</th>
              <th className="px-3.5 py-2.5 text-right">Enviadas</th>
              <th className="px-3.5 py-2.5 text-right text-amber-700">En recepción</th>
              <th className="px-3.5 py-2.5 text-right text-emerald-700">Recibidas</th>
              <th className="px-3.5 py-2.5 text-right text-rose-800">Rechazadas</th>
              <th className="px-3.5 py-2.5 text-right">Cajas est.</th>
            </tr>
          </thead>
          <tbody>
            {lineas.map((l) => (
              <tr key={l.sku} className="border-t border-slate-100">
                <td className="px-3.5 py-[11px]">
                  <div className="font-mono text-[12.5px] font-bold text-slate-900">{l.sku}</div>
                  <div className="text-[11.5px] text-slate-400">{l.nombre}</div>
                </td>
                <td className="px-3.5 py-[11px] font-mono text-xs text-indigo-600">{l.publicacion ?? "—"}</td>
                <td className="px-3.5 py-[11px] text-right">{l.solicitadas === null ? sReg : num(l.solicitadas)}</td>
                <td className="px-3.5 py-[11px] text-right">{l.validadas === null ? sReg : num(l.validadas)}</td>
                <td className="px-3.5 py-[11px] text-right font-mono font-bold tabular-nums text-slate-900">{num(l.enviadas)}</td>
                <td className="px-3.5 py-[11px] text-right">
                  <span className={`inline-flex rounded border px-1.5 py-0.5 font-mono text-xs font-bold tabular-nums ${
                    cerrado ? "border-slate-200 bg-white text-slate-400" : "border-amber-300 bg-amber-50 text-amber-800"}`}>
                    {cerrado ? 0 : num(l.enviadas)}
                  </span>
                </td>
                <td className="px-3.5 py-[11px] text-right font-mono font-bold tabular-nums text-emerald-700">
                  {l.recibidas === null ? sinDato : num(l.recibidas)}
                </td>
                <td className="px-3.5 py-[11px] text-right font-mono font-bold tabular-nums text-rose-800">
                  {l.rechazadas === null ? sinDato : num(l.rechazadas)}
                </td>
                <td className="px-3.5 py-[11px] text-right font-mono text-slate-500">{l.cajas === null ? "—" : `~${l.cajas}`}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-slate-200 bg-slate-50 text-[12.5px] font-bold">
              <td colSpan={4} className="px-3.5 py-2.5 text-slate-600">Total del envío</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-slate-900">{num(totEnviadas)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-amber-800">{cerrado ? 0 : num(totEnviadas)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-emerald-700">{totRecibidas === null ? sinDato : num(totRecibidas)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-rose-800">{totRechazadas === null ? sinDato : num(totRechazadas)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-slate-500">~{num(totCajas)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-amber-900">
            <b>En recepción no es rechazo.</b> Las piezas de la columna ámbar salieron de bodega y el almacén
            del marketplace todavía no las ha contado. Sólo se mueven a <b>rechazadas</b> cuando el marketplace
            lo dice como cantidad explícita — nunca por resta.
          </p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-slate-600">
            <b>Las cajas son estimadas.</b> Odoo no registra cajas ni usa paquetes: se derivan de las piezas y
            del factor de caja del producto. Por eso llevan <code className="font-mono">~</code> en todas las
            columnas. Las cajas del packing list son cartones de <b>importación</b>, no de envío a FULL.
          </p>
        </div>
      </div>
    </Tarjeta>
  );
}
