"use client";

/**
 * Márgenes de los SKUs más vendidos (requerimiento 1, Eduardo 4-ago).
 *
 * La estructura por SKU: precio de venta PROMEDIO realizado (ingreso ÷
 * unidades de los pedidos), Costo Base (producto + flete de importación),
 * los cobros de Meli (comisión REAL promedio + envío estimado) y el COSTO
 * FINAL. El margen se calcula sobre el Costo Final. Colapsable para no
 * estorbar a quien viene a la tabla de publicaciones.
 */

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, TrendingUp } from "lucide-react";
import { API_BASE } from "@/lib/api";

interface FilaTop {
  sku: string;
  titulo: string | null;
  uds: number;
  ingreso: number;
  precio_prom: number | null;
  costo_base: number | null;
  comision_prom: number | null;
  envio_prom: number | null;
  costo_final: number | null;
  ganancia_unit: number | null;
  margen_pct: number | null;
}

const fM = (v: number | string | null | undefined, dec = 2) =>
  v == null ? "—" : new Intl.NumberFormat("es-MX", {
    style: "currency", currency: "MXN",
    minimumFractionDigits: dec, maximumFractionDigits: dec,
  }).format(Number(v));

export default function MargenesTop10() {
  const [abierto, setAbierto] = useState(true);
  const [dias, setDias] = useState(30);
  const [filas, setFilas] = useState<FilaTop[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setFilas(null);
    fetch(`${API_BASE}/api/fulfillment/margenes-top?dias=${dias}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`API ${r.status}`); return r.json(); })
      .then((d) => setFilas(d.items))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [dias]);

  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between px-5 py-3">
        <button onClick={() => setAbierto(!abierto)}
                className="flex items-center gap-2 text-sm font-bold text-slate-800">
          {abierto ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <TrendingUp size={15} className="text-indigo-500" />
          Márgenes · 10 más vendidos
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500">
            margen sobre costo final
          </span>
        </button>
        <div className="flex items-center gap-1.5">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDias(d)}
                    className={`rounded-lg px-2 py-1 text-xs font-bold transition-colors ${dias === d ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
              {d}d
            </button>
          ))}
        </div>
      </div>

      {abierto && (
        <div className="border-t border-slate-100 px-5 pb-4 pt-2">
          {err && <div className="py-3 text-sm text-red-600">Error: {err}</div>}
          {!err && !filas && <div className="py-6 text-center text-sm text-slate-400">Cargando…</div>}
          {filas && (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[880px] text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                      <th className="py-2 pr-3 font-semibold">Producto</th>
                      <th className="py-2 pr-3 text-right font-semibold">Uds</th>
                      <th className="py-2 pr-3 text-right font-semibold" title="Ingreso ÷ unidades: lo que de verdad se cobró en promedio">Precio prom.</th>
                      <th className="py-2 pr-3 text-right font-semibold" title="Producto + flete de importación">Costo base</th>
                      <th className="py-2 pr-3 text-right font-semibold" title="Comisión REAL de ML promedio por unidad">Comisión</th>
                      <th className="py-2 pr-3 text-right font-semibold" title="Estimado por peso y dimensiones (el real llega en fase 2)">Envío est.</th>
                      <th className="py-2 pr-3 text-right font-semibold" title="Costo base + comisión + envío">Costo final</th>
                      <th className="py-2 pr-3 text-right font-semibold">Ganancia/u</th>
                      <th className="py-2 text-right font-semibold">Margen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filas.map((f) => (
                      <tr key={f.sku} className="border-t border-slate-50">
                        <td className="max-w-[300px] py-1.5 pr-3">
                          <div className="font-mono text-[11px] font-bold text-indigo-600">{f.sku}</div>
                          <div className="truncate text-slate-500" title={f.titulo ?? ""}>{f.titulo ?? "—"}</div>
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-slate-700">{f.uds}</td>
                        <td className="py-1.5 pr-3 text-right font-semibold tabular-nums text-slate-800">{fM(f.precio_prom)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fM(f.costo_base)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-slate-600">{fM(f.comision_prom)}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-slate-400">{fM(f.envio_prom)}</td>
                        <td className="py-1.5 pr-3 text-right font-semibold tabular-nums text-slate-800">{fM(f.costo_final)}</td>
                        <td className={`py-1.5 pr-3 text-right tabular-nums ${f.ganancia_unit != null && f.ganancia_unit < 0 ? "text-red-500" : "text-slate-700"}`}>
                          {fM(f.ganancia_unit)}
                        </td>
                        <td className={`py-1.5 text-right font-bold tabular-nums ${f.margen_pct == null ? "text-slate-300" : f.margen_pct < 20 ? "text-red-500" : "text-emerald-600"}`}>
                          {f.margen_pct == null ? "—" : `${f.margen_pct}%`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[10px] text-slate-400">
                Comisión: la real cobrada por Mercado Libre en las ventas del período.
                Envío: estimado por peso/dimensiones. Quedan fuera los cargos de bodega
                FULL (se facturan por mes, no por venta). Un margen negativo en rojo
                puede ser un costo mal capturado — verifica antes de actuar.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
