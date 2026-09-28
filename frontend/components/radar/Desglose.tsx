"use client";

// Desglose.tsx — De qué se compone la contribución por pieza, por cuenta.
//
// Cada renglón lleva su procedencia (real / estimado / sin dato). Full y
// publicidad llegan en F2: hasta entonces NO se restan, y se rotulan como
// "sin dato", nunca como $0. El total es el `valor` que calculó el backend;
// las barras sólo lo dibujan.

import { useState } from "react";

import type { RadarCuenta, RadarParametros } from "@/lib/api";
import { etiquetaCuenta, fraccionComoPct, num, pesos, porcentaje } from "./formato";
import { EtiquetaProcedencia, SinDato } from "./ui";
import type { Procedencia } from "./formato";

interface Renglon {
  etiqueta: string;
  procedencia: Procedencia | null;
  nota: string;
  monto: number | null;
  signo: 1 | -1;
}

/** Un descuento se lee con "−"; si el backend manda un monto negativo (un abono), con "+". */
function signoDe(r: Renglon): string {
  const m = r.monto ?? 0;
  if (r.signo === 1) return m < 0 ? "−" : "";
  if (m > 0) return "−";
  if (m < 0) return "+";
  return "";
}

export default function Desglose({
  cuentas,
  inicial,
  parametros,
}: {
  cuentas: RadarCuenta[];
  inicial: string | null;
  parametros: RadarParametros | null | undefined;
}) {
  const [elegida, setElegida] = useState<string | null>(inicial ?? cuentas[0]?.cuenta ?? null);
  const c = cuentas.find((x) => x.cuenta === elegida) ?? cuentas[0] ?? null;
  if (!c) return <SinDato texto="Sin publicaciones que desglosar" />;

  const d = c.contribucion?.desglose ?? null;
  const iva = num(parametros?.iva);
  const comisionEstimada = fraccionComoPct(num(parametros?.comision_estimada));
  const precio = c.precio_cobrado ?? c.precio;

  const renglones: Renglon[] = [
    {
      etiqueta: "Precio sin IVA",
      procedencia: null,
      nota: iva !== null && precio !== null ? `${pesos(precio)} ÷ ${(1 + iva).toFixed(2)}` : "precio cobrado sin IVA",
      monto: d?.precio_sin_iva ?? null,
      signo: 1,
    },
    {
      etiqueta: "Comisión",
      procedencia: d?.comision === null || d?.comision === undefined ? "sin_dato" : d.comision_estado ?? "estimado",
      nota:
        d?.comision_estado === "real"
          ? "tasa media de sus pedidos (60 días)"
          : comisionEstimada
            ? `estimada: ${comisionEstimada} del precio`
            : "estimada",
      monto: d?.comision ?? null,
      signo: -1,
    },
    {
      etiqueta: "Envío",
      procedencia: d?.envio === null || d?.envio === undefined ? "sin_dato" : d.envio_estado ?? "estimado",
      nota: d?.envio_estado === "real" ? "promedio de sus envíos (60 días)" : "tarifa estimada por peso",
      monto: d?.envio ?? null,
      signo: -1,
    },
    {
      etiqueta: "Almacenaje Full",
      procedencia: d?.full === null || d?.full === undefined ? "sin_dato" : null,
      nota: d?.full === null || d?.full === undefined ? "llega en F2" : "",
      monto: d?.full ?? null,
      signo: -1,
    },
    {
      etiqueta: "Publicidad",
      procedencia: d?.publicidad === null || d?.publicidad === undefined ? "sin_dato" : null,
      nota: d?.publicidad === null || d?.publicidad === undefined ? "llega en F2" : "",
      monto: d?.publicidad ?? null,
      signo: -1,
    },
    {
      etiqueta: "Devolución esperada",
      procedencia: d?.devolucion === null || d?.devolucion === undefined ? "sin_dato" : "estimado",
      nota:
        d?.devolucion_tasa_pct !== null && d?.devolucion_tasa_pct !== undefined
          ? `tasa ${porcentaje(d.devolucion_tasa_pct)} × envío de retorno`
          : "sin tasa de devolución",
      monto: d?.devolucion ?? null,
      signo: -1,
    },
  ];

  // Sólo para DIBUJAR la cascada: de dónde a dónde va cada barra.
  const base = d?.precio_sin_iva ?? null;
  let restante = base ?? 0;
  const barras = renglones.map((r) => {
    if (base === null || base <= 0 || r.monto === null) return null;
    if (r.signo === 1) return { izq: 0, ancho: 100 };
    const hasta = restante - r.monto;
    const izq = (Math.max(0, Math.min(restante, hasta)) / base) * 100;
    const ancho = (Math.abs(r.monto) / base) * 100;
    restante = hasta;
    return { izq, ancho: Math.max(0.6, Math.min(100 - izq, ancho)) };
  });
  const valor = c.contribucion?.valor ?? null;
  const barraTotal = base !== null && base > 0 && valor !== null && valor > 0 ? Math.min(100, (valor / base) * 100) : 0;

  return (
    <div className="flex flex-col gap-3">
      {cuentas.length > 1 && (
        <div role="group" aria-label="Cuenta del desglose" className="inline-flex self-start rounded-lg bg-[#F6F7FB] p-1">
          {cuentas.map((x) => {
            const on = x.cuenta === c.cuenta;
            return (
              <button
                key={x.cuenta}
                type="button"
                aria-pressed={on}
                onClick={() => setElegida(x.cuenta)}
                className={[
                  "rounded-md px-3 py-1 text-[13px] font-semibold",
                  on ? "border border-[#D0D5DD] bg-white text-[#3730A3] shadow-sm" : "border border-transparent text-[#4A5163]",
                ].join(" ")}
              >
                {etiquetaCuenta(x.cuenta)}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        {renglones.map((r, k) => {
          const sinDato = r.monto === null;
          const b = barras[k];
          return (
            <div key={r.etiqueta} className="flex flex-col gap-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[13px]">
                <span className="font-medium text-slate-800">{r.etiqueta}</span>
                {r.procedencia && <EtiquetaProcedencia estado={r.procedencia} />}
                <span className="text-xs text-[#4A5163]">{r.nota}</span>
                <span className="flex-1" />
                {sinDato ? (
                  <span className="text-xs font-medium text-slate-500">sin restar</span>
                ) : (
                  <span className="font-semibold tabular-nums text-slate-900">
                    {signoDe(r)}
                    {pesos(Math.abs(r.monto as number), 2)}
                  </span>
                )}
              </div>
              <span
                className={[
                  "relative block h-2 overflow-hidden rounded-full",
                  sinDato ? "border border-dashed border-[#98A2B3] bg-white" : "bg-[#F6F7FB]",
                ].join(" ")}
                aria-hidden
              >
                {b && (
                  <span
                    className="absolute top-0 h-full rounded-full"
                    style={{ left: `${b.izq}%`, width: `${b.ancho}%`, background: r.signo === 1 ? "#4F46E5" : "#98A2B3" }}
                  />
                )}
              </span>
            </div>
          );
        })}

        <div className="mt-1 flex flex-col gap-1 border-t border-[#EEF0F4] pt-3">
          <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-semibold text-slate-900">Contribución por pieza</span>
            <EtiquetaProcedencia estado={valor === null ? "sin_dato" : c.contribucion?.estado ?? "estimado"} />
            <span className="text-xs text-[#4A5163]">cota superior</span>
            <span className="flex-1" />
            {valor === null ? (
              <SinDato />
            ) : (
              <span className={`text-base font-bold tabular-nums ${valor < 0 ? "text-rose-700" : "text-slate-900"}`}>
                {pesos(valor, 2)}
              </span>
            )}
          </div>
          <span className="relative block h-2 overflow-hidden rounded-full bg-[#F6F7FB]" aria-hidden>
            <span className="absolute left-0 top-0 h-full rounded-full bg-[#0B6B58]" style={{ width: `${barraTotal}%` }} />
          </span>
        </div>
      </div>
    </div>
  );
}
