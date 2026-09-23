// flujo.ts — Reglas PURAS del flujo del SKU dentro de /omnicanal.
//
// Vive en `lib/` y no dentro de una página a propósito: el stepper, el sello de
// la lista y el de la tarjeta tienen que pintar el MISMO vocabulario, o el
// filtro deja de servir de leyenda de los colores. Nada de aquí llama al
// backend ni toca el DOM; la regla de qué se puede pulsar la manda el servidor
// (`clicable`), no este archivo.
//
// Los helpers de /inventario NO se mudaron aquí: esa página está en WIP y un
// traslado mecánico se comería el diff de esta entrega.

import type {
  ConteoCanalFlujo,
  EstadoCuadroFlujo,
  ResumenVariantesFlujo,
  SelloFlujo,
  CriterioConteo,
} from "./types";

/* ── Paleta ────────────────────────────────────────────────────────────────
   Sólida, no los tintes de tarjeta de /inventario (sky-50, indigo-50…): en un
   cuadro de 5×9 px un tinte claro se lee como vacío. Es la misma muestra que
   lleva la nota de cada paso del stepper; las flechas usan el tinte claro del
   mismo tono (cielo, esmeralda, índigo, gris), que ahí sí se lee. */
export interface Muestra {
  fill: string;
  stroke: string;
  dash?: string;
}

export const MUESTRA_FLUJO = {
  recibido: { fill: "#38bdf8", stroke: "#38bdf8" },
  bodega: { fill: "#10b981", stroke: "#10b981" },
  specs: { fill: "#fde68a", stroke: "#fcd34d" },
  destino: { fill: "#6366f1", stroke: "#6366f1" },
  restock: { fill: "none", stroke: "#94a3b8", dash: "1.5 1.5" },
  /** Blanco con trazo gris: el producto NO lo cumple. */
  falta: { fill: "#ffffff", stroke: "#cbd5e1" },
  /** Punteado apagado: NO SE SABE. Nunca se confunde con `falta`. */
  sin_dato: { fill: "#f8fafc", stroke: "#cbd5e1", dash: "1.5 1.5" },
} as const satisfies Record<string, Muestra>;

/** El orden de los cuadros de bodega, el mismo de /inventario y de la maqueta. */
export const ORDEN_CUADROS = ["ubicacion", "stock", "foto", "specs"] as const;
export type ClaveCuadro = (typeof ORDEN_CUADROS)[number];

/** Canales que saben filtrar por etapa. Shein se pinta con datos de ejemplo:
 *  no tiene SKUs reales que cruzar, así que el backend responde 503. */
export const CANALES_CON_ETAPA = new Set([
  "general", "mercado_libre", "amazon", "tiktok", "temu", "walmart",
]);

const NUM = new Intl.NumberFormat("es-MX");

/** «1,505», «≈1,505» o «—». `null` es «no se pudo contar», nunca cero. */
export function cifra(n: number | null | undefined, aprox = false): string {
  if (n === null || n === undefined) return "—";
  return (aprox ? "≈" : "") + NUM.format(n);
}

/** La hora de la foto en CDMX. La maqueta pide hora ABSOLUTA («foto 12:30»):
 *  con la edad relativa nadie sabe si el dato es de hoy o de anoche. */
export function horaCdmx(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("es-MX", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/Mexico_City",
  }).format(d);
}

/** Con qué criterio pagina HOY la lista. `solo_activas` MANDA sobre
 *  `solo_publicados` en el backend, así que aquí gana igual; en General no hay
 *  chips (Woo no es canal de venta) y siempre es `todas`. */
export function criterioDe(
  esGeneral: boolean,
  soloActivas: boolean,
  soloPublicados: boolean,
): CriterioConteo {
  if (esGeneral) return "todas";
  if (soloActivas) return "activas";
  if (soloPublicados) return "publicados";
  return "todas";
}

/* ── Textos del sello ─────────────────────────────────────────────────────── */

const RECIBIDO_TEXTO: Record<string, string> = {
  si: "sí",
  na: "no aplica",
  sin_dato: "sin dato de kubera o de Odoo",
};

/** De dónde salió el «sí». Son las dos columnas de cajas de /inventario, y no
 *  valen lo mismo: el packing list es la foto del embarque —congelada— y el
 *  empaque de Odoo dice cómo viene empacado, no que haya llegado. */
const FUENTE_RECIBIDO: Record<string, string> = {
  packing_list: " · por el packing list",
  odoo: " · por el empaque de Odoo",
  ambas: " · por las dos columnas",
};

const MOTIVO_RECIBIDO: Record<string, string> = {
  sin_renglon: "no, sin renglón en costos validados ni empaque en Odoo",
  sin_cajas: "no, sin cajas en costos validados ni empaque en Odoo",
  fuera_piloto: "no aplica: fuera de la lista de Inventario",
};

const NOMBRE_CUADRO: Record<ClaveCuadro, string> = {
  ubicacion: "ubicación",
  stock: "stock",
  foto: "foto de bodega",
  specs: "specs",
};

/** Une con comas y una «y» final: «ubicación, stock y foto». */
function enumerar(partes: string[]): string {
  if (partes.length <= 1) return partes.join("");
  return `${partes.slice(0, -1).join(", ")} y ${partes[partes.length - 1]}`;
}

/**
 * El `title` del sello: los cuatro pasos en una línea, con el mismo orden y el
 * mismo vocabulario que los cuadros. Es lo único que explica POR QUÉ un SKU
 * cayó en su etapa, así que no se recorta.
 *
 * `revisado` no sale del sello (el costo validado no es un paso del flujo):
 * llega de la fila para poder decir justamente eso.
 *
 * `etiquetas` traduce el id de cuenta al nombre visible, igual que
 * `sufijoCuenta`. `full_cuentas` trae el `legacy_code` de core.accounts
 * ("SANCORFASHION"), así que sin esto la misma celda decía dos nombres
 * distintos de la misma cuenta: «por San Corpe» arriba y «SANCORFASHION» en el
 * tooltip.
 */
export function tituloSello(
  s: SelloFlujo,
  opciones?: { revisado?: boolean; etiquetas?: Record<string, string> },
): string {
  const p = s.pasos;
  const partes: string[] = [];

  // 1. Recibido — siempre «aprox.»: ninguna de las dos columnas dice que la
  // mercancía llegó. El packing list es un congelado del embarque y el empaque
  // de Odoo describe la caja, no la recepción.
  const r = p.recibido;
  const rTexto = r.motivo === "fuera_piloto"
    ? MOTIVO_RECIBIDO.fuera_piloto
    : r.estado === "no"
      ? (r.motivo ? MOTIVO_RECIBIDO[r.motivo] : "no")
      : (RECIBIDO_TEXTO[r.estado] ?? r.estado);
  const rFuente = r.estado === "si" && r.fuente ? (FUENTE_RECIBIDO[r.fuente] ?? "") : "";
  partes.push(`Recibido (aprox.): ${rTexto}${rFuente}`);

  // 2. Los cuatro cuadros de bodega.
  const b = p.bodega;
  if (!s.en_piloto && s.etapa !== "padre") {
    // Fuera de la lista de Inventario no hay validación que contar, y el dato
    // que sí existe (Odoo, costos) se enseña en la tarjeta, no aquí.
    partes.push("Validado bodega: sin validar, fuera de la lista de Inventario");
  } else if (s.etapa === "padre") {
    partes.push("Validado bodega: no aplica a un padre");
  } else if (b.n_listo === null) {
    partes.push("Validado bodega: sin dato de Odoo");
  } else if (b.en_odoo === false) {
    partes.push("Validado bodega: no existe en Odoo");
  } else {
    const listos = ORDEN_CUADROS
      .filter((c) => b[c] === "listo")
      .map((c) => NOMBRE_CUADRO[c]);
    const detalle = [
      listos.length ? enumerar(listos) : "",
      "specs no se evalúa en esta foto",
    ].filter(Boolean).join("; ");
    const archivado = b.archivado ? ", archivado en Odoo" : "";
    partes.push(`Validado bodega: ${b.n_listo} de 4 (${detalle})${archivado}`);
  }
  if (b.escritura_distinta && b.codigo_odoo) {
    partes.push(`Odoo lo escribe ${b.codigo_odoo}`);
  }

  // 3. Destino. Las cuentas solo se nombran si la fuente de canales contestó:
  // una lista vacía sería «ninguna cuenta», que es otra afirmación.
  const d = p.destino;
  const etiquetas = opciones?.etiquetas ?? {};
  const cuentas = d.full && d.full_cuentas?.length
    ? `, con FULL en ${enumerar(d.full_cuentas.map((c) => etiquetas[c] ?? c))}`
    : "";
  if (d.full && d.drop) partes.push(`En FULL y En DROP: sí${cuentas}`);
  else if (d.full) partes.push(`En FULL: sí${cuentas}`);
  else if (d.drop) partes.push("En DROP: sí");
  else if (d.full === false && d.drop === false) partes.push("En FULL o DROP: no");
  else partes.push("En FULL o DROP: sin dato");
  if (d.sin_dato) partes.push("destino sin dato");

  // 4. Restock.
  partes.push("Restock: por definir");

  // La hora manda: un sello vencido sigue siendo útil, pero hay que decirlo.
  const vencida = [r.vieja, b.vieja, d.vieja].some(Boolean);
  if (vencida) {
    const hora = horaCdmx(b.generado ?? r.generado);
    partes.push(`dato de las ${hora}, vencido`);
  }

  if (opciones?.revisado) partes.push("El costo validado no mueve el flujo");

  return partes.join(" · ");
}

/**
 * « · por San Corpe»: el SKU está En FULL por la OTRA cuenta y la fila que se
 * está viendo no tiene FULL. Solo en Mercado Libre — En FULL es del SKU y de
 * ML, así que en las demás pestañas nombrar una cuenta de ML confundiría.
 */
export function sufijoCuenta(
  s: SelloFlujo,
  canal: string,
  cuentaFila: string | null | undefined,
  etiquetas: Record<string, string>,
): string {
  if (canal !== "mercado_libre") return "";
  const d = s.pasos.destino;
  if (!d.full || !d.full_cuentas?.length) return "";
  if (!cuentaFila || d.full_cuentas.includes(cuentaFila)) return "";
  const nombres = d.full_cuentas.map((c) => etiquetas[c] ?? c);
  return ` · por ${enumerar(nombres)}`;
}

/** Resumen de variantes de un padre, omitiendo los ceros: «1 En FULL · 2
 *  Recibido de 5». Un «0 En DROP» no informa y roba el ancho de la celda. */
export function textoVariantes(v: ResumenVariantesFlujo): string {
  const partes: string[] = [];
  if (v.en_full) partes.push(`${v.en_full} En FULL`);
  if (v.en_fba) partes.push(`${v.en_fba} En FBA`);
  if (v.en_drop) partes.push(`${v.en_drop} En DROP`);
  if (v.bodega_3de4) partes.push(`${v.bodega_3de4} 3 de 4`);
  if (v.recibido) partes.push(`${v.recibido} Recibido`);
  if (v.sin_dato) partes.push(`${v.sin_dato} sin dato`);
  // Mismo texto que `inventario_flujo.texto_variantes`: el sello del padre lo
  // trae ya armado en `etapa_texto` y las dos líneas tienen que coincidir.
  if (!partes.length) return `${v.total} sin etapa`;
  return `${partes.join(" · ")} de ${v.total}`;
}

/** Cómo se pinta cada cuadro. `espera` y `na` van como `falta` (blanco), que es
 *  lo aprobado en la maqueta; /inventario conserva su ámbar. */
export function muestraCuadro(estado: EstadoCuadroFlujo, clave: ClaveCuadro): Muestra {
  if (estado === "sin_dato") return MUESTRA_FLUJO.sin_dato;
  if (estado === "listo") return MUESTRA_FLUJO.bodega;
  if (clave === "specs") return MUESTRA_FLUJO.specs;
  return MUESTRA_FLUJO.falta;
}

/* ── Textos del stepper ───────────────────────────────────────────────────── */

/** La nota de cada pestaña del stepper, al pasar el cursor (Eduardo, 17-sep:
 *  «agrega notas para describir qué hace cada pestaña»). Cuatro preguntas,
 *  siempre en el mismo orden, porque el equipo lee estas cifras como medidas y
 *  cada etapa tiene su trampa: qué cuenta, de dónde sale el dato, qué hace el
 *  clic y qué NO significa. Lo que depende del momento (foto vencida, conteo
 *  caído, filtros encima) no va aquí: lo agrega el componente como aviso. */
export interface NotaEtapa {
  que: string;
  fuente: string;
  clic: string;
  ojo?: string;
}

export const NOTA_ETAPA: Record<string, NotaEtapa> = {
  todas: {
    que: "El catálogo de esta pestaña y esta cuenta, sin filtro de etapa.",
    fuente: "La lista de abajo: la cifra es su total, con el criterio elegido "
      + "(Todas, Solo publicados o Solo activas).",
    clic: "Quita el filtro de etapa y vuelve al catálogo completo.",
  },
  recibido: {
    que: "De los SKUs que están en la pestaña Inventario, los que traen cajas: "
      + "por el packing list o por el empaque de Odoo.",
    fuente: "Solo se cuentan los SKUs de la lista de Inventario, que hoy son 14 "
      + "y están fijos en el código. De ésos: cajas y piezas por caja en costos "
      + "validados (congelado de mayo y junio) o piezas por caja en Odoo.",
    clic: "Filtra el catálogo a esos SKUs.",
    ojo: "El resto del catálogo NO se cuenta aunque tenga el dato: nadie lo ha "
      + "puesto a validar. Y ninguna de las dos columnas dice que la mercancía "
      + "llegó — el packing list es la foto del embarque y el empaque de Odoo "
      + "describe la caja.",
  },
  bodega_3de4: {
    que: "De los SKUs de la pestaña Inventario, los que cumplen 3 de los 4 "
      + "requisitos: ubicación, stock y foto. El cuarto, specs, no se evalúa "
      + "en esta foto.",
    fuente: "Odoo, leído en una foto que se rearma cada 30 min, con la misma "
      + "regla que la columna Validado bodega de Inventario, y solo sobre los "
      + "SKUs de esa lista (hoy 14).",
    clic: "Filtra el catálogo a los que cumplen 3 de 4.",
    ojo: "Validado bodega completo (4 de 4) da 0 mientras esta foto no evalúe "
      + "specs: la matriz por categoría ya existe y la ficha del producto sí la "
      + "usa, pero el flujo todavía no la lee. Y los SKUs fuera de la lista de "
      + "Inventario no se cuentan aquí aunque Odoo tenga sus datos.",
  },
  en_fba: {
    que: "SKUs con stock en FBA, la bodega de Amazon.",
    fuente: "Publicaciones de Amazon en kubera (sincronizadas cada 15 min): "
      + "stock FBA mayor a 0.",
    clic: "Filtra el catálogo a esos SKUs.",
    ojo: "FBA no es FULL: son bodegas distintas y se cuentan aparte. Walmart "
      + "WFS no aparece porque no hay dato para contarlo, y TikTok y Temu "
      + "despachan desde nuestro almacén.",
  },
  en_full: {
    que: "SKUs con stock en FULL de Mercado Libre, en cualquiera de las dos "
      + "cuentas.",
    fuente: "Publicaciones de Mercado Libre en kubera (sincronizadas cada 15 "
      + "min): stock FULL mayor a 0.",
    clic: "Filtra el catálogo a esos SKUs.",
    ojo: "Es del SKU, no de la cuenta: uno que está en FULL por San Corpe "
      + "también aparece en la pestaña de Kubera. FBA de Amazon y WFS de "
      + "Walmart no cuentan.",
  },
  en_drop: {
    que: "SKUs con existencias en el almacén DROP OFF de Odoo.",
    fuente: "Odoo: existencias mayores a 0 en las ubicaciones internas del "
      + "almacén DROP OFF, leídas como mucho cada 30 min.",
    clic: "Filtra el catálogo a esos SKUs. Sustituye al chip Solo DROP OFF.",
    ojo: "Incluye ubicaciones no vendibles.",
  },
  restock: {
    que: "Los SKUs que hay que volver a surtir.",
    fuente: "Ninguna todavía.",
    clic: "No filtra: la regla está por definir.",
    ojo: "Hoy se decide a mano cada semana; falta acordar la regla antes de "
      + "contarlo aquí.",
  },
  costo_validado: {
    que: "SKUs cuyo costo ya revisó una persona.",
    fuente: "Costos validados en kubera: tienen fecha de revisión "
      + "(revisado_at).",
    clic: "Filtra el catálogo y se combina con cualquier etapa. Sustituye al "
      + "chip Costo validado.",
    ojo: "Corre aparte: no es requisito para enviar. Tampoco es lo contrario "
      + "de «Sin costo», que significa que no hay renglón de costo.",
  },
};

/** Qué unidad cuenta el stepper en esta pestaña, para el title del rótulo. */
export function textoUnidad(c: ConteoCanalFlujo | null): string {
  if (!c) return "publicaciones de la cuenta activa";
  if (c.unidad === "producto_woo") {
    return "productos de Woo contados desde core.products (aprox.)";
  }
  if (c.unidad === "fila_woo") {
    return "filas de Woo (listado aplanado) contadas desde core.products (aprox.)";
  }
  return "publicaciones de la cuenta activa";
}

/** La foto pasó su TTL: las cifras siguen sirviendo, pero con la hora en ámbar. */
export function fotoVencida(c: ConteoCanalFlujo | null): boolean {
  if (!c || c.edad_s === null || c.edad_s === undefined) return false;
  return c.edad_s > c.ttl_s;
}
