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
  );
}
