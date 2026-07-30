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

import { FileText } from "lucide-react";
import FulfillmentPendiente from "@/components/FulfillmentPendiente";

export default function ReportesPage() {
  return (
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
          "ventas.csv: CSV maestro por SKU cruzando ventas + inventario + costos",
          "Aviso en los que dependen del Reporte Semanal (es la base de bodega, posiciones y envío)",
          "Historial de archivos ya generados con su link de descarga",
        ],
        fuentes: [
          {
            nombre: "channel.sales_daily_completa",
            detalle: "ventas por SKU sin hueco",
            listo: true,
          },
          {
            nombre: "channel.listings",
            detalle: "stock FULL / FBA / DROP y precio por canal",
            listo: true,
          },
          {
            nombre: "costing.costos_validados",
            detalle: "costo por variante (contrato de costos)",
            listo: true,
          },
          {
            nombre: "Directorio de reportes",
            detalle: "setting `reportes_dir` + entrada en .gitignore",
            listo: false,
          },
        ],
        bloqueos: [
          "Cerrar la decisión del precio sugerido: precio_base (contrato de José) vs precio_sugerido (semántica de Brandon, v0.33.x)",
          "Definir dónde se guardan los archivos generados en Railway (el disco del contenedor es efímero — ¿storage o generar al vuelo?)",
        ],
      }}
    />
  );
}
