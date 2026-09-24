/**
 * guiasDelDia.ts — lo que la ventana "Guías del día" recibe del backend
 * (`GET /api/automatizacion/guias-del-dia`, services/guias_del_dia.py) y la
 * comprobación del PDF que llega.
 *
 * Vive aparte de la página, y sin imports, para que `test_guias_dia.py` lo
 * pueda correr con node contra las cabeceras REALES del endpoint.
 */

export interface GuiaGrupo {
  /** "…2532": el final de la guía, el mismo nombre que en la pestaña y el Excel. */
  codigo: string;
  /** Relleno claro (el mismo de la fila en Excel). */
  color: string;
  /** Tinta fuerte para borde y letra. */
  tinta: string;
  n: number;
  piezas: number;
  companeras: string[];
  /** Otros días (AAAA-MM-DD) cuyo PDF también trae esta etiqueta. */
  etiqueta_tambien_en: string[];
}

export interface GuiaOrden {
  orden: string;
  venta: string;
  canal: string;
  /** Cuándo NACIÓ la orden en Odoo: el día que se eligió arriba. */
  fecha: string | null;
  fecha_dia: string;
  /** Cuándo entró la VENTA. Desde la creación diferida (23-sep) puede ser de
   *  un día anterior: la orden nace cuando aparece la guía. */
  vendida_at: string | null;
  vendida_dia: string;
  almacen: string;
  guia: string;
  paqueteria: string;
  lineas: { sku: string; piezas: number }[];
  piezas_total: number;
  grupo: GuiaGrupo | null;
  otro_dia: boolean;
  /** null = Odoo no respondió y no se sabe. */
  tiene_pdf: boolean | null;
  cancelada: boolean;
  nota: string;
}

export interface GuiasDia {
  fecha: string;
  canal: string;
  odoo_ok: boolean;
  /** Por dónde se contó el día: `odoo` = por `create_date` de la orden (el
   *  hecho); `bitacora` = por la fecha de la VENTA, porque Odoo no contestó. */
  dia_por?: "odoo" | "bitacora";
  /** false = el día se contó por la bitácora y bajo la creación diferida eso no
   *  es una aproximación: es OTRO día. El archivo sale igual (falla hacia
   *  mostrar) pero no se puede empacar por él. */
  dia_confiable?: boolean;
  aviso_dia?: string | null;
  /** Ventas que comparten guía con una orden de este día y todavía NO tienen
   *  orden en Odoo: esa caja va incompleta. No son renglones de la tabla —de
   *  ellas no hay nada que empacar— sino un aviso. */
  hermanas_sin_orden?: { canal: string; venta: string; guia: string }[];
  aviso_hermana_sin_orden?: string | null;
  ordenes: GuiaOrden[];
  resumen: {
    total: number; del_dia: number; de_otro_dia: number; con_guia: number; sin_guia: number;
    sin_pdf: number; combinados: number; canceladas: number; piezas: number;
    etiquetas: number; sin_etiqueta: number;
    /** De `del_dia`, las que nacieron de una venta de un día anterior. */
    de_venta_anterior?: number;
    /** Órdenes de este día cuya caja lleva además una venta sin orden todavía. */
    con_hermana_sin_orden?: number;
    /** De `etiquetas`, las que también salen en el PDF de otro día / de uno anterior. */
    etiquetas_otro_dia: number; etiquetas_dia_anterior: number;
  };
  /** Las guías que NO van a salir en el PDF porque Odoo no tiene su archivo. */
  faltantes_pdf: { ordenes: string[]; guia: string; canal: string }[];
  /** Etiquetas que también salen en el PDF de otro día (envío combinado entre días). */
  etiquetas_repetidas: {
    ordenes: string[]; guia: string; codigo: string; canal: string;
    tambien_en: string[]; anterior: boolean;
  }[];
}

/** Lo que el PDF descargado NO trajo y la vista previa no había avisado. */
export interface AvisoPdf {
  dia: string;
  /** El canal del PDF: el aviso se queda en pantalla aunque se cambie de vista. */
  canal: string;
  /** null = no se pudo leer la cabecera: no se sabe cuántas trae. */
  salieron: number | null;
  esperadas: number;
  ordenes: string[];
}

/**
 * Compara el PDF que llegó (sus cabeceras) contra lo que prometió la vista
 * previa. `faltantes_pdf` sólo conoce las órdenes SIN archivo en Odoo; un
 * archivo que no es PDF o está dañado se descubre al armarlo y viaja en
 * `X-Guias-Faltantes-Ordenes`. Sin esto el almacén imprimía N-1 etiquetas
 * creyendo que eran N.
 */
export function revisarPdf(cab: Headers, datos: GuiasDia, omitidas: boolean): AvisoPdf | null {
  const r = datos.resumen;
  const esperadas = r.etiquetas - (omitidas ? r.etiquetas_dia_anterior : 0);
  const n = Number.parseInt(cab.get("X-Guias-Etiquetas") ?? "", 10);
  const salieron = Number.isFinite(n) ? n : null;
  const avisadas = new Set(datos.faltantes_pdf.map((f) => f.ordenes.join(" + ")));
  const ordenes = (cab.get("X-Guias-Faltantes-Ordenes") ?? "")
    .split(",").map((s) => s.trim())
    .filter((s) => s && s !== "..." && !avisadas.has(s));
  if (!ordenes.length && salieron !== null && salieron >= esperadas) return null;
  return { dia: datos.fecha, canal: datos.canal, salieron, esperadas, ordenes };
}

/** "12-09" a partir de "2026-09-12". */
export const ddmm = (iso: string) => (iso.length >= 10 ? `${iso.slice(8, 10)}-${iso.slice(5, 7)}` : iso);
