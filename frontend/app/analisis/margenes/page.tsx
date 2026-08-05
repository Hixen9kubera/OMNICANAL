"use client";

/**
 * /analisis/margenes — casa de los dos requerimientos de márgenes (Eduardo).
 *
 * Estaba en Omnicanal y estorbaba: la tarjeta ocupaba toda la vista antes de
 * llegar a las publicaciones, que es a lo que se entra ahí. Aquí tiene espacio
 * propio, y el reporte por venta vive al lado del top — son el mismo tema y el
 * mismo cálculo, solo que uno agregado por SKU y el otro línea por línea.
 *
 * Definiciones (las mismas del backend):
 *   Costo Base  = producto + flete de importación
 *   Costo Final = Costo Base + comisión REAL de ML + envío estimado
 *   Margen      = (precio − Costo Final) ÷ precio
 */

import { useState } from "react";
import { Download, Info } from "lucide-react";
import { API_BASE } from "@/lib/api";
import MargenesTop10 from "@/components/MargenesTop10";

function hace(dias: number): string {
  return new Date(Date.now() - dias * 86400_000).toISOString().slice(0, 10);
}

function ReporteMargenes() {
  const [desde, setDesde] = useState(hace(30));
  const [hasta, setHasta] = useState(hace(0));
  const [cuenta, setCuenta] = useState("");

  const url = () => {
    const q = new URLSearchParams({ desde, hasta });
    if (cuenta) q.set("cuenta", cuenta);
    return `${API_BASE}/api/fulfillment/reporte-margenes?${q.toString()}`;
  };

  const inputCls =
    "rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 focus:border-indigo-400 focus:outline-none";

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center gap-2">
        <Download size={15} className="text-indigo-500" />
        <h3 className="text-sm font-bold text-slate-800">Reporte completo — una fila por venta</h3>
      </div>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        Cada línea vendida con su ingreso, la comisión REAL que cobró Mercado Libre,
        el envío estimado y las dos columnas de costo (<b>Base</b> y <b>Final</b>),
        más ganancia y margen. El archivo se genera al momento; no se guarda nada en
        el servidor.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
          Desde
          <input type="date" value={desde} onChange={(e) => setDesde(e.target.value)}
                 className={`${inputCls} mt-1 block`} />
        </label>
        <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
          Hasta
          <input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)}
                 className={`${inputCls} mt-1 block`} />
        </label>
        <label className="text-[10px] font-bold uppercase tracking-wide text-slate-400">
          Cuenta
          <select value={cuenta} onChange={(e) => setCuenta(e.target.value)}
                  className={`${inputCls} mt-1 block`}>
            <option value="">Todas</option>
            <option value="BEKURA">BEKURA</option>
            <option value="SANCORFASHION">SANCORFASHION</option>
            <option value="AMAZON">AMAZON</option>
          </select>
        </label>
        <a href={url()} download
           className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-bold text-white transition-colors hover:bg-indigo-700">
          <Download size={13} /> Descargar CSV
        </a>
      </div>
    </div>
  );
}

export default function MargenesPage() {
  return (
    <div className="space-y-5">
      {/* Cómo se arma el costo — lo que hay que entender antes de leer nada */}
      <div className="rounded-2xl border border-indigo-100 bg-indigo-50/40 p-4">
        <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-indigo-700">
          <Info size={14} /> Cómo se calcula
        </div>
        <div className="grid gap-3 text-xs text-slate-600 sm:grid-cols-3">
          <div>
            <div className="font-bold text-slate-800">Costo Base</div>
            Lo que costó traer el producto: precio de compra + flete de importación.
          </div>
          <div>
            <div className="font-bold text-slate-800">Costo Final</div>
            El Base más lo que cobra Mercado Libre por vender: comisión (la real de
            cada venta) y envío. Es el costo verdadero de poner el producto en manos
            del cliente.
          </div>
          <div>
            <div className="font-bold text-slate-800">Margen</div>
            Cuánto queda sobre el precio de venta, después del Costo Final. El precio
            es el promedio REALIZADO: lo que de verdad se cobró, no lo publicado.
          </div>
        </div>
      </div>

      <MargenesTop10 />
      <ReporteMargenes />
    </div>
  );
}
