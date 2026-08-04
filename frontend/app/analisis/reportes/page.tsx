"use client";

/**
 * /analisis/reportes — catálogo de reportes descargables.
 *
 * Porta routers/reports.py + los scripts CLI del fulfillment de José (ver
 * docs/fulfillment/prompts_originales_jose.txt, prompt 3). `reporte_semanal`
 * es la BASE: bodega_k, posiciones_k y el de envío dependen de su CSV.
 *
 * OJO con el contrato de costos que declara ese prompt "sin excepciones":
 * costo = costos_validados.costo_total (ya aplicado en Reabastecimiento) y
 * precio sugerido = costos_finales.precio_base — esto último NO adoptado
 * todavía porque choca con la semántica de Brandon (v0.33.x), donde
 * precio_base es el precio de lista antes del descuento. Decisión pendiente.
 */

import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { API_BASE } from "@/lib/api";
import FulfillmentPendiente from "@/components/FulfillmentPendiente";

/* Primer reporte REAL de la sección. El CSV se genera y baja al momento
   (streaming): no hay archivos guardados en el servidor, así que el bloqueo
   de "carpeta de descargas" no aplica a esta tarjeta. */
function hace(dias: number): string {
  const d = new Date(Date.now() - dias * 86400_000);
  return d.toISOString().slice(0, 10);
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
    <div className="mb-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center gap-2">
        <Download size={15} className="text-indigo-500" />
        <h3 className="text-sm font-bold text-slate-800">Reporte de márgenes — una fila por venta</h3>
      </div>
      <p className="mb-3 max-w-3xl text-xs text-slate-500">
        Cada línea vendida con su ingreso, la comisión REAL que cobró Mercado Libre,
        el envío estimado, y las dos columnas de costo: <b>Costo Base</b> (producto +
        flete de importación) y <b>Costo Final</b> (base + todos los cobros del
        marketplace). El margen se calcula sobre el Costo Final. Las ventas sin costo
        cargado van con esas columnas vacías; Amazon aún no reporta comisión.
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

export default function ReportesPage() {
  return (
    <>
      <ReporteMargenes />
      <FulfillmentPendiente
      p={{
        titulo: "Reportes descargables",
        icono: FileText,
        resumen:
          "Catálogo de reportes en CSV/Excel generados desde el panel, sin scripts " +
          "en la máquina de nadie: ventas, publicaciones, sin ventas, bodega y envío.",
        contenido: [
          "Tarjeta por reporte con su descripción, formato y parámetros (p. ej. umbral de 'sin ventas', default 5)",
          "Botón Generar con estado por tarjeta y descarga al terminar",
          "Reporte maestro por producto cruzando ventas, inventario y costos (CSV)",
          "Aviso en los que dependen del Reporte Semanal (es la base de bodega, posiciones y envío)",
          "Historial de archivos ya generados con su link de descarga",
        ],
        fuentes: [
          {
            nombre: "Historial de ventas por producto",
            detalle: "completo y al día, sin huecos",
            listo: true,
          },
          {
            nombre: "Inventario y precios por canal",
            detalle: "stock FULL / FBA / DROP y precio de cada publicación",
            listo: true,
          },
          {
            nombre: "Costos por producto",
            detalle: "el costo validado de cada variante",
            listo: true,
          },
          {
            nombre: "Carpeta de descargas",
            detalle: "definir dónde se guardan los archivos generados",
            listo: false,
          },
        ],
        bloqueos: [
          "Decidir cuál de los dos precios guardados en el sistema es el precio sugerido oficial (hoy conviven dos criterios)",
          "Decidir dónde se guardan los reportes generados: el servidor no conserva archivos entre reinicios",
        ],
      }}
      />
    </>
  );
}
