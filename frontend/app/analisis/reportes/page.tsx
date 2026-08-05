"use client";

/**
 * /analisis/reportes — catálogo de reportes descargables.
 *
 * PRIMER REPORTE VIVO (Eduardo, 05-ago): "Ventas por categoría" en Excel (la
 * réplica del xlsx de José) se descarga desde AQUÍ — se quitó el botón de la
 * página de Categorías. Filtros propios: cuenta, período relativo o absoluto
 * (desde → hasta manda sobre los botones). El resto del catálogo (porta de
 * routers/reports.py + scripts CLI del fulfillment de José, ver
 * docs/fulfillment/prompts_originales_jose.txt, prompt 3) sigue PENDIENTE en
 * la tarjeta de abajo; `reporte_semanal` es la BASE de bodega_k, posiciones_k
 * y envío.
 *
 * OJO con el contrato de costos que declara ese prompt "sin excepciones":
 * costo = costos_validados.costo_total (ya aplicado en Reabastecimiento) y
 * precio sugerido = costos_finales.precio_base — esto último NO adoptado
 * todavía porque choca con la semántica de Brandon (v0.33.x), donde
 * precio_base es el precio de lista antes del descuento. Decisión pendiente.
 */

import { useState } from "react";
import { Download, FileSpreadsheet, FileText, X } from "lucide-react";
import { API_BASE } from "@/lib/api";
import FulfillmentPendiente from "@/components/FulfillmentPendiente";

const CUENTAS = [
  { id: "", label: "Consolidado" },
  { id: "BEKURA", label: "Bekura" },
  { id: "SANCORFASHION", label: "Sancor" },
  { id: "AMAZON", label: "Amazon" },
];
const PERIODOS = [
  { dias: 7, label: "7 días" },
  { dias: 30, label: "30 días" },
  { dias: 60, label: "60 días" },
  { dias: 90, label: "90 días" },
  { dias: 400, label: "Histórico" },
];

/* Tarjeta del reporte VIVO: Excel de ventas por categoría (tipo José). */
function TarjetaVentasCategoria() {
  const [cuenta, setCuenta] = useState("");
  const [dias, setDias] = useState(60);
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const rangoActivo = Boolean(desde || hasta);

  const q = new URLSearchParams({ dias: String(dias) });
  if (cuenta) q.set("cuenta", cuenta);
  if (desde) q.set("desde", desde);
  if (hasta) q.set("hasta", hasta);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-emerald-50 p-2.5 text-emerald-600">
          <FileSpreadsheet size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-800">
            Ventas por categoría (Excel)
          </h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            El árbol completo de categorías de ML con sus publicaciones
            (plegable con los botones +/− de Excel, abre compacto) + hoja de
            resumen por categoría principal. Venta REAL del período; margen:
            pendiente.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          cuenta === c.id
                            ? "bg-indigo-600 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {PERIODOS.map((p) => (
                <button key={p.dias}
                        onClick={() => { setDias(p.dias); setDesde(""); setHasta(""); }}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          dias === p.dias && !rangoActivo
                            ? "bg-slate-900 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className={`flex items-center gap-1.5 rounded-xl border p-1.5 shadow-sm ${
              rangoActivo ? "border-slate-900 bg-white" : "border-slate-200 bg-white"}`}>
              <input type="date" value={desde} max={hasta || undefined}
                     onChange={(e) => setDesde(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
              <span className="text-xs text-slate-400">a</span>
              <input type="date" value={hasta} min={desde || undefined}
                     onChange={(e) => setHasta(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
              {rangoActivo && (
                <button onClick={() => { setDesde(""); setHasta(""); }}
                        title="Volver a los períodos relativos"
                        className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                  <X size={14} />
                </button>
              )}
            </div>
            <a href={`${API_BASE}/api/fulfillment/categorias/excel?${q.toString()}`}
               className="flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100">
              <Download size={15} /> Descargar
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

/* Reporte de MÁRGENES: una fila por venta, con las dos columnas de costo que
   pidió Eduardo (Base y Final). Vive aquí desde que se retiró la pestaña
   propia de Márgenes — el margen ya se lee en la tabla de Análisis, y lo único
   que quedaba suelto era este descargable, que es justo lo que Reportes es. */
function TarjetaMargenes() {
  const hace = (d: number) => new Date(Date.now() - d * 86400_000).toISOString().slice(0, 10);
  const [desde, setDesde] = useState(hace(30));
  const [hasta, setHasta] = useState(hace(0));
  const [cuenta, setCuenta] = useState("");

  const q = new URLSearchParams({ desde, hasta });
  if (cuenta) q.set("cuenta", cuenta);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start gap-3">
        <div className="rounded-xl bg-indigo-50 p-2.5 text-indigo-600">
          <FileText size={22} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-800">
            Márgenes por venta (CSV)
          </h2>
          <p className="mt-0.5 text-[13px] text-slate-500">
            Una fila por cada línea vendida con su ingreso, la comisión REAL que
            cobró Mercado Libre y el envío estimado, más las dos columnas de
            costo: <b>Base</b> (producto + flete de importación) y <b>Final</b>
            {" "}(el Base con los cobros del canal encima). El archivo se genera
            al momento; no se guarda nada en el servidor.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
              {CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                          cuenta === c.id
                            ? "bg-indigo-600 font-semibold text-white"
                            : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-1.5 shadow-sm">
              <input type="date" value={desde} max={hasta || undefined}
                     onChange={(e) => setDesde(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
              <span className="text-xs text-slate-400">a</span>
              <input type="date" value={hasta} min={desde || undefined}
                     onChange={(e) => setHasta(e.target.value)}
                     className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
            </div>
            <a href={`${API_BASE}/api/fulfillment/reporte-margenes?${q.toString()}`}
               className="flex items-center gap-1.5 rounded-xl border border-indigo-200 bg-indigo-50 px-3.5 py-2 text-sm font-medium text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100">
              <Download size={15} /> Descargar
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ReportesPage() {
  return (
    <div className="space-y-4">
      <TarjetaVentasCategoria />
      <TarjetaMargenes />
      <FulfillmentPendiente
        p={{
          titulo: "Más reportes descargables",
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
    </div>
  );
}
