"use client";

/**
 * CajaMasterPanel — captura los datos de la CAJA MASTER y deriva la pieza.
 *
 * Existe porque hoy la gente hace esa conversión a mano y se equivoca de una
 * forma concreta y cara: divide **cada lado** entre las piezas por caja. Eso da
 * volumen ÷ n³ en vez de ÷ n. Con 10 piezas el flete queda 100 veces por debajo
 * del real (un SKU tenía $1.25 donde correspondían $124.74), y hoy hay 270 SKUs
 * en el catálogo con densidades físicamente imposibles por ese motivo.
 *
 * Aquí se reparte el VOLUMEN, no los lados, así que L×W×H de la pieza siempre
 * da exactamente `volumen_caja / piezas_por_caja`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Box, Loader2, X } from "lucide-react";

import { costoPreview } from "@/lib/api";
import type { CostoCalculo } from "@/lib/types";

const TARIFA_CBM = 7500; // $/m³, igual que el backend
/** Arriba de esto, suponer que las piezas van en una fila deja de ser físico. */
const MAX_PIEZAS_EN_FILA = 10;
/** Nada sólido supera al acero (7,850). Arriba de esto el dato está mal. */
const DENSIDAD_IMPOSIBLE = 3000;

const num = (v: string): number => Number(v.trim()) || 0;

const mxn = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("es-MX", {
        style: "currency",
        currency: "MXN",
        maximumFractionDigits: 2,
      }).format(v);

/**
 * Reparte el volumen del cartón entre sus piezas conservándolo exacto.
 *
 * Con pocas piezas (≤10) se divide el lado más largo: modela piezas formadas en
 * fila. Con muchas, raíz cúbica sobre los tres lados — con 120 piezas la regla
 * de la fila daría un lado de 0.38 cm, volumen correcto pero forma imposible, y
 * el peso volumétrico de Mercado Libre saldría mal.
 */
export function dimsPorPieza(
  L: number,
  W: number,
  H: number,
  piezas: number,
): [number, number, number] {
  if (!L || !W || !H || piezas <= 0) return [0, 0, 0];
  if (piezas <= 1) return [L, W, H];
  const lados: [number, number, number] = [L, W, H];
  if (piezas <= MAX_PIEZAS_EN_FILA) {
    const i = lados.indexOf(Math.max(...lados)) as 0 | 1 | 2;
    lados[i] = lados[i] / piezas;
  } else {
    const factor = Math.cbrt(1 / piezas);
    lados[0] *= factor;
    lados[1] *= factor;
    lados[2] *= factor;
  }
  return [
    Math.round(lados[0] * 100) / 100,
    Math.round(lados[1] * 100) / 100,
    Math.round(lados[2] * 100) / 100,
  ];
}

export interface DerivadoCajaMaster {
  largo: number;
  ancho: number;
  alto: number;
  peso: number;
  costoUsd: number;
}

export default function CajaMasterPanel({
  sku,
  tipoCambio,
  margen,
  incluirEnvio,
  onAplicar,
  onCerrar,
}: {
  sku: string;
  tipoCambio: number;
  margen: number;
  incluirEnvio: boolean;
  onAplicar: (d: DerivadoCajaMaster) => void;
  onCerrar: () => void;
}) {
  const [L, setL] = useState("");
  const [W, setW] = useState("");
  const [H, setH] = useState("");
  const [pesoBruto, setPesoBruto] = useState("");
  const [piezas, setPiezas] = useState("");
  const [costoUsd, setCostoUsd] = useState("");
  const [calc, setCalc] = useState<CostoCalculo | null>(null);
  const [cargando, setCargando] = useState(false);

  const d = useMemo(() => {
    const n = num(piezas);
    const [lp, ap, hp] = dimsPorPieza(num(L), num(W), num(H), n);
    const volCaja = (num(L) * num(W) * num(H)) / 1_000_000;
    const volPieza = n > 0 ? volCaja / n : 0;
    const pesoPieza = n > 0 ? num(pesoBruto) / n : 0;
    return {
      lp, ap, hp, volCaja, volPieza, pesoPieza,
      flete: volPieza * TARIFA_CBM,
      densidad: volPieza > 0 ? pesoPieza / volPieza : 0,
      regla: n <= 1 ? "la pieza es la caja"
           : n <= MAX_PIEZAS_EN_FILA ? "se parte el lado mayor"
           : "raíz cúbica (muchas piezas)",
    };
  }, [L, W, H, pesoBruto, piezas]);

  const listo = d.lp > 0 && d.pesoPieza > 0 && num(costoUsd) > 0;

  // Desglose completo: lo calcula el backend con la comisión real de la
  // categoría de ML, no una aproximación local.
  useEffect(() => {
    if (!listo) {
      setCalc(null);
      return;
    }
    const t = setTimeout(async () => {
      setCargando(true);
      try {
        setCalc(
          (
            await costoPreview(sku, {
              largo: d.lp, ancho: d.ap, alto: d.hp, peso: d.pesoPieza,
              costo_producto: Math.round(num(costoUsd) * tipoCambio * 100) / 100,
              margen, incluir_envio: incluirEnvio, auto_cbm: true,
            })
          ).calculo,
        );
      } catch {
        setCalc(null);
      } finally {
        setCargando(false);
      }
    }, 400);
    return () => clearTimeout(t);
  }, [listo, sku, d.lp, d.ap, d.hp, d.pesoPieza, costoUsd, tipoCambio, margen, incluirEnvio]);

  const aplicar = useCallback(() => {
    onAplicar({
      largo: d.lp, ancho: d.ap, alto: d.hp,
      peso: Math.round(d.pesoPieza * 1000) / 1000,
      costoUsd: num(costoUsd),
    });
    onCerrar();
  }, [d, costoUsd, onAplicar, onCerrar]);

  const campo = (
    label: string, valor: string, set: (v: string) => void, ancho = "w-20",
  ) => (
    <label className="block">
      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <input
        value={valor}
        onChange={(e) => set(e.target.value)}
        inputMode="decimal"
        className={`${ancho} rounded-lg border border-slate-200 px-2 py-1.5 text-sm tabular-nums focus:border-indigo-400 focus:outline-none`}
      />
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4">
      <div className="my-10 w-full max-w-3xl rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 text-white">
            <Box size={16} />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-bold text-slate-900">Capturar por caja master</h3>
            <p className="font-mono text-[11px] text-slate-500">{sku}</p>
          </div>
          <button onClick={onCerrar} className="rounded p-1.5 text-slate-400 hover:bg-slate-100">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4">
          <div className="flex flex-wrap items-end gap-3">
            {campo("Largo (cm)", L, setL)}
            {campo("Ancho (cm)", W, setW)}
            {campo("Alto (cm)", H, setH)}
            {campo("Peso bruto (kg)", pesoBruto, setPesoBruto, "w-28")}
            {campo("Piezas por caja", piezas, setPiezas, "w-28")}
            {campo("Costo USD /pz", costoUsd, setCostoUsd, "w-24")}
          </div>

          {/* Derivación */}
          {d.lp > 0 && (
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Por pieza · {d.regla}
              </div>
              <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
                <span>
                  <b className="tabular-nums">{d.lp}×{d.ap}×{d.hp}</b> cm
                </span>
                <span className="text-slate-600">
                  volumen <b className="tabular-nums">{d.volPieza.toFixed(6)}</b> m³
                </span>
                <span className="text-slate-600">
                  peso <b className="tabular-nums">{d.pesoPieza.toFixed(3)}</b> kg
                </span>
                <span className="text-slate-600">
                  flete <b className="tabular-nums">{mxn(d.flete)}</b>
                </span>
              </div>
              <p className="mt-2 text-[11px] text-slate-500">
                El volumen se reparte entre las piezas, no los lados:{" "}
                {d.volCaja.toFixed(6)} ÷ {num(piezas)} = {d.volPieza.toFixed(6)} m³.
                Dividir cada lado daría {(d.volCaja / Math.pow(num(piezas) || 1, 3)).toFixed(8)} m³
                — el error que dejó 270 SKUs con el flete mal.
              </p>

              {d.densidad > DENSIDAD_IMPOSIBLE && (
                <div className="mt-2 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>
                    Densidad de <b>{Math.round(d.densidad).toLocaleString("es-MX")} kg/m³</b> —
                    más que el acero (7,850). Revisa si el peso bruto es de la caja
                    o si las piezas por caja están bien.
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Desglose */}
          {listo && (
            <div className="mt-4">
              <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Desglose
                {cargando && <Loader2 size={12} className="animate-spin" />}
              </div>
              {calc ? (
                <table className="w-full text-sm">
                  <thead className="border-b border-slate-200 text-[10px] uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-1.5 text-left font-semibold">Precio base</th>
                      <th className="py-1.5 text-right font-semibold">Costo base</th>
                      <th className="py-1.5 text-right font-semibold">Comisión /u</th>
                      <th className="py-1.5 text-right font-semibold">Envío real /u</th>
                      <th className="py-1.5 text-right font-semibold">Costo final</th>
                      <th className="py-1.5 text-right font-semibold">Margen</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="tabular-nums">
                      <td className="py-2 font-semibold text-slate-900">
                        {mxn(calc.precio_base)}
                      </td>
                      <td className="py-2 text-right">{mxn(calc.costo_unitario)}</td>
                      <td className="py-2 text-right">
                        {mxn(calc.costo_comision)}
                        {calc.comision_estimada && (
                          <span
                            className="ml-1 text-[9px] text-amber-600"
                            title="Salió del fallback: sin token de ML o sin categoría"
                          >
                            est.
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-right">{mxn(calc.costo_fee_envio)}</td>
                      <td className="py-2 text-right font-semibold">
                        {mxn(calc.precio_sugerido)}
                      </td>
                      <td className="py-2 text-right font-semibold text-emerald-600">
                        {mxn(calc.ganancia_neta)}
                        <div className="text-[10px] font-normal text-slate-400">
                          ROI {(calc.roi * 100).toFixed(0)}%
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              ) : (
                !cargando && (
                  <p className="text-xs text-slate-400">
                    No se pudo calcular el desglose (revisa la categoría de ML del SKU).
                  </p>
                )
              )}
            </div>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={onCerrar}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancelar
            </button>
            <button
              onClick={aplicar}
              disabled={!listo}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              Aplicar a la fila
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
