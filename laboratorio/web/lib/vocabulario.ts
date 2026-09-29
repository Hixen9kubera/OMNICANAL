/**
 * Vocabulario CERRADO del pipeline → texto para humanos. Si llega una llave que
 * no está aquí, se muestra cruda: es justo lo que hay que ver (mismo criterio
 * que `ESTADO_UI` en frontend/lib/publicaciones.ts).
 */
import type { Confianza, EstadoPub, FuenteCosto } from "./tipos";

export type Tono = "indigo" | "emerald" | "rose" | "amber" | "slate" | "sky" | "violet" | "teal";

export const ESTADO: Record<EstadoPub, { label: string; tono: Tono; ayuda: string }> = {
  activa: { label: "Activa", tono: "emerald", ayuda: "Se puede comprar ahora." },
  pausada: { label: "Pausada", tono: "slate", ayuda: "Existe y está apagada (en FULL casi siempre por falta de stock en el almacén de ML)." },
  en_revision: { label: "En revisión", tono: "amber", ayuda: "ML la tiene retenida (under_review): no vende." },
  cerrada: { label: "Cerrada", tono: "slate", ayuda: "Se dio de baja." },
  inactiva: { label: "Inactiva", tono: "slate", ayuda: "El canal la reporta inactiva." },
  otra: { label: "Otra", tono: "slate", ayuda: "Estado que el laboratorio no clasifica." },
};

/**
 * El matiz de un estado (`estado_detalle`). Sobre todo explica «otra»: 1,875
 * publicaciones el 28-sep (1,268 de Amazon no comprables, 512 borradores…).
 */
export const ESTADO_DETALLE: Record<string, { label: string; ayuda: string }> = {
  no_comprable: { label: "No comprable", ayuda: "El canal la lista pero no se puede comprar (Amazon: DISCOVERABLE sin BUYABLE)." },
  borrador: { label: "Borrador", ayuda: "Creada en el canal pero nunca publicada." },
  puede_estar_activa: { label: "¿Activa?", ayuda: "El canal no confirma el estado: puede estar vendiendo. Revisar en el canal." },
  rechazada: { label: "Rechazada", ayuda: "El canal rechazó la publicación (moderación o datos faltantes)." },
  sin_estado: { label: "Sin estado", ayuda: "El canal no devolvió estado para esta publicación." },
  incompleta: { label: "Incompleta", ayuda: "Temu la tiene pausada por datos incompletos." },
};

export const FUENTE_COSTO: Record<FuenteCosto, { label: string; tono: Tono; ayuda: string }> = {
  packing_list_exacto: { label: "exacto", tono: "emerald", ayuda: "Renglón ubicado en el packing list del contenedor: 525,000 × m³ de la pieza ÷ m³ totales del archivo." },
  prorrateo_kubera: { label: "prorrateo", tono: "indigo", ayuda: "525,000 ÷ m³ del contenedor reconstruido con costos_validados de kubera." },
  tarifa_7500: { label: "tarifa", tono: "amber", ayuda: "m³ de la pieza × $7,500 (la referencia del panel; equivale a suponer 70 m³)." },
  sin_costo: { label: "sin costo", tono: "slate", ayuda: "No hay m³ ni costo validado: no se calcula utilidad ni se recomienda precio." },
  sospechoso: { label: "descartado", tono: "rose", ayuda: "El m³ por pieza es más de 3× lo que permite el peso facturable de ML: el costo no se usa y no se recomienda precio hasta corregir las medidas." },
};

/** La fuente de costo con respaldo: una llave nueva del pipeline no debe tumbar la tabla. */
export function fuenteCosto(f: string | null | undefined): { label: string; tono: Tono; ayuda: string } {
  return FUENTE_COSTO[(f ?? "sin_costo") as FuenteCosto] ?? { label: f ?? "sin costo", tono: "slate", ayuda: "Fuente de costo que la web no conoce." };
}

export const RAZON: Record<string, { label: string; tono: Tono; ayuda: string }> = {
  sobre_competencia: { label: "Sobre competencia", tono: "amber", ayuda: "El precio actual está más de 15 % arriba de la referencia de competencia." },
  bajo_competencia: { label: "Bajo competencia", tono: "sky", ayuda: "El precio actual está más de 15 % abajo de la referencia: hay espacio para subir." },
  escalon_envio_299: { label: "Escalón $299", tono: "violet", ayuda: "Desde $299 el envío gratis completo lo paga el vendedor: cruzar ese escalón cambia la utilidad de golpe." },
  tramo_comision_500: { label: "Tramo $500", tono: "violet", ayuda: "Desde $500 la comisión de ML baja de tramo." },
  stock_excesivo: { label: "Stock excesivo", tono: "amber", ayuda: "Cobertura mayor a 120 días: se acepta sacrificar más utilidad para mover volumen." },
  stock_escaso: { label: "Stock escaso", tono: "rose", ayuda: "Cobertura menor a 10 días: no se sacrifica utilidad por volumen." },
  sin_costo: { label: "Sin costo", tono: "slate", ayuda: "No hay costo aterrizado: no hay recomendación." },
  sin_peso: { label: "Sin peso", tono: "slate", ayuda: "No hay peso facturable: el envío no se puede calcular y no se recomienda precio." },
  perdiendo_dinero: { label: "Pierde dinero", tono: "rose", ayuda: "Al precio actual la utilidad por unidad es negativa." },
  elasticidad_baja_confianza: { label: "β poco confiable", tono: "amber", ayuda: "La elasticidad viene de la categoría global o del prior: el plan empieza con una prueba de −5 %." },
  promo_vigente: { label: "Promo vigente", tono: "indigo", ayuda: "El precio actual viene de una promoción con fecha de fin." },
  pausada_sin_stock: { label: "Pausada sin stock", tono: "slate", ayuda: "FULL pausada por falta de stock en el almacén de ML: el precio no es su freno." },
  // ── Por qué el recomendado queda donde queda (fase 3) ──
  piso_margen: { label: "Piso de margen", tono: "rose", ayuda: "El piso de margen (12 %) sube el recomendado: más abajo la publicación no deja lo mínimo." },
  fuera_de_banda_por_piso: { label: "Arriba de la competencia por el piso", tono: "amber", ayuda: "Queda arriba de la banda de competencia porque dentro de la banda no se alcanza el piso de margen." },
  fuera_de_banda_por_escasez: { label: "Arriba de la competencia por escasez", tono: "amber", ayuda: "Queda arriba de la banda de competencia porque hay poco stock: no se baja para no agotarlo." },
  precio_reactivacion: { label: "Precio al reactivar", tono: "slate", ayuda: "Pausada: es el precio para cuando vuelva a publicarse, medido contra lo que pagaban en su último periodo con oferta." },
  escalon_demanda_299: { label: "Efecto $299 en la demanda", tono: "violet", ayuda: "Estar abajo de $299 mueve la demanda (efecto medido y significativo): entra en la curva." },
  // ── Guardarraíles ──
  sin_evidencia_cambio_acotado: { label: "Cambio acotado a ±10 %", tono: "amber", ayuda: "Sin elasticidad propia confiable, ventas y dos semanas de base, el cambio se limita a ±10 % y el plan empieza con una prueba." },
  stock_no_alcanza_al_recomendado: { label: "No baja: el stock no alcanza", tono: "rose", ayuda: "Al precio más bajo, FULL no cubriría 21 días y Odoo no repone: se mantiene el precio." },
  coordinado_otra_cuenta: { label: "Coordinado con la otra cuenta", tono: "teal", ayuda: "El mismo SKU está activo en Kubera y San Corpe: solo baja una cuenta, la que más vende de las que pueden bajar sin abrir la brecha entre cuentas; esta se mantiene para no competir entre nuestras cuentas." },
  brecha_entre_cuentas: { label: "Brecha entre cuentas", tono: "teal", ayuda: "Se sube para que la diferencia con la otra cuenta no pase de 10 % (o de la diferencia de hoy, si ya era mayor). Una bajada a lo más se cancela: nunca se vuelve subida." },
  tope_precio_regular: { label: "Tope: precio regular", tono: "indigo", ayuda: "En promoción el recomendado no pasa del precio regular: subir más sería quitar la promoción, otra decisión." },
  baja_bloqueada_costo_sin_confirmar: { label: "Bajada bloqueada por costo", tono: "rose", ayuda: "Con el costo del panel (mercancía + flete) la bajada quedaría bajo el piso. Solo aplica si se cobra la mercancía; hoy no." },
  costo_sospechoso_volumen: { label: "Costo descartado", tono: "rose", ayuda: "El m³ por pieza es imposible frente al peso facturable de ML: sin costo confiable no se recomienda precio." },
};

/**
 * Avisos de una recomendación (`precios.json → avisos`). Llegan como
 * «clave: detalle»; la clave se normaliza (`promo_vence_en_3d` → `promo_vence_en`).
 * `informativo` = no cambia la decisión: se muestra en gris y al final.
 */
export const AVISO_REC: Record<string, { label: string; tono: Tono; informativo?: boolean }> = {
  riesgo_si_se_cobra_mercancia: { label: "Riesgo si se cobra la mercancía (al bajar del precio de hoy)", tono: "amber" },
  promo_vence_en: { label: "La promoción vence pronto", tono: "indigo" },
  stock_no_alcanza_al_recomendado: { label: "El stock no alcanza al precio más bajo", tono: "rose" },
  coordinado_otra_cuenta: { label: "Coordinado con la otra cuenta", tono: "teal" },
  brecha_entre_cuentas: { label: "Brecha entre cuentas acotada", tono: "teal" },
  baja_bloqueada_costo_sin_confirmar: { label: "Bajada bloqueada por el costo del panel", tono: "rose" },
  paso_minimo_excede_tope: { label: "Paso mínimo de $1 pasa el tope", tono: "amber" },
  piso_sobre_precio_regular: { label: "El piso queda arriba del precio regular", tono: "rose" },
  descuento_mayor_al_70pct_del_regular: { label: "Descuento mayor a 70 % del regular", tono: "amber" },
  sin_ventas_en_base: { label: "Sin ventas en la base: el recomendado no depende de las unidades de hoy", tono: "amber" },
  base_corta: { label: "Base corta", tono: "amber" },
  piso_fuera_de_rejilla: { label: "El piso queda fuera de la curva: recomendado = piso redondeado", tono: "amber" },
  recomendado_sobre_rejilla_por_piso: { label: "El piso obliga a recomendar arriba de la curva", tono: "amber" },
  recomendado_en_suelo_de_rejilla: { label: "En el límite inferior de la curva (0.55 × precio actual)", tono: "amber" },
  costo_sospechoso_volumen: { label: "Costo descartado: m³ imposible para su peso", tono: "rose" },
  costo_sospechoso: { label: "Costo sospechoso", tono: "rose" },
  costo_menor_a_1_peso: { label: "Costo menor a $1: m³ sospechoso", tono: "rose" },
  costo_densidad_alta: { label: "m³ quizá chico para su peso: costo posiblemente subestimado", tono: "amber" },
  ref_no_comparable: { label: "La referencia de competencia no es comparable", tono: "slate", informativo: true },
  sin_panel_de_elasticidad: { label: "Sin historia para estimar la elasticidad", tono: "amber" },
  sin_precio_actual: { label: "Sin precio actual", tono: "rose" },
  costo_bajo_fob: { label: "Costo de 525k menor que la mercancía (informativo)", tono: "slate", informativo: true },
};

/** `promo_vence_en_3d` → `promo_vence_en`; `ref_serp_no_comparable` → `ref_no_comparable`. */
export function claveAviso(a: string): string {
  const k = a.split(":")[0].trim();
  if (/^promo_vence_en_\d+d$/.test(k)) return "promo_vence_en";
  if (/^paso_minimo_excede_tope_(diario|semanal)$/.test(k)) return "paso_minimo_excede_tope";
  if (/^ref_[a-z_]+?_no_comparable$/.test(k)) return "ref_no_comparable";
  return k;
}

export interface AvisoLeido { clave: string; label: string; detalle: string; tono: Tono; informativo: boolean }

/** El detalle que escribe el pipeline, con los nombres de cuenta del panel y mayúscula inicial. */
function humanizar(d: string): string {
  const t = d.replace(/\bSANCORFASHION\b/g, "San Corpe").replace(/\bBEKURA\b/g, "Kubera");
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/** Un aviso del optimizador para humanos: etiqueta + el detalle que trae («a $1,549 el margen…»). */
export function leerAviso(a: string): AvisoLeido {
  const clave = claveAviso(a);
  const i = a.indexOf(":");
  const detalle = i >= 0 ? humanizar(a.slice(i + 1).trim()) : "";
  const v = AVISO_REC[clave];
  return { clave, label: v?.label ?? clave, detalle, tono: v?.tono ?? "amber", informativo: !!v?.informativo };
}

/** Los avisos de una fila: primero los que cambian la decisión, al final los informativos. */
export function avisosOrdenados(avisos: string[] | null | undefined): AvisoLeido[] {
  const xs = (avisos ?? []).map(leerAviso);
  return [...xs.filter((x) => !x.informativo), ...xs.filter((x) => x.informativo)];
}

export const tieneAviso = (avisos: string[] | null | undefined, clave: string) => (avisos ?? []).some((a) => claveAviso(a) === clave);

export const CONFIANZA: Record<Confianza, { label: string; tono: Tono; ayuda: string }> = {
  alta: { label: "alta", tono: "emerald", ayuda: "Elasticidad propia del item (≥ 6 semanas válidas, error estándar < 0.5)." },
  media: { label: "media", tono: "amber", ayuda: "Heredada de la categoría raíz (≥ 5 items) con encogimiento empírico-bayesiano." },
  baja: { label: "baja", tono: "rose", ayuda: "Global o prior de parametros.json: el plan propone una prueba antes de moverse." },
};

export const METODO_EMPATE: Record<string, { label: string; tono: Tono; ayuda: string }> = {
  ferraforme: { label: "Ferraforme", tono: "emerald", ayuda: "El código del proveedor en Odoo apunta directo al renglón." },
  sha256: { label: "Foto idéntica", tono: "emerald", ayuda: "La foto de Odoo es byte a byte la del packing list (sha256)." },
  dhash: { label: "Foto parecida", tono: "indigo", ayuda: "Huella perceptual (dHash) a distancia ≤ 8 de 64, con margen de 4 sobre la segunda." },
  ia_titulo: { label: "IA · título", tono: "amber", ayuda: "La IA empató por título: último peldaño, revisar." },
  ia_foto: { label: "IA · foto", tono: "amber", ayuda: "La IA empató por foto: último peldaño, revisar." },
};

export const METODOS_COSTO: { clave: "volumetrico_real" | "tarifa_fija_7500" | "peso_volumen_wm" | "valor_fob" | "hibrido_70_30"; label: string; ayuda: string }[] = [
  { clave: "volumetrico_real", label: "Volumétrico", ayuda: "RECOMENDADO. 525,000 × m³ de la pieza ÷ m³ totales del packing list. Σ por contenedor = 525,000." },
  { clave: "tarifa_fija_7500", label: "7,500/m³", ayuda: "m³ de la pieza × 7,500: la referencia actual del panel (supone 70 m³)." },
  { clave: "peso_volumen_wm", label: "W/M", ayuda: "Tonelada-flete: 525,000 × max(m³, t) ÷ Σ max(m³, t). Corrige productos densos." },
  { clave: "valor_fob", label: "FOB", ayuda: "525,000 × USD de la pieza ÷ Σ USD. Informativo: 40 % de los renglones no trae USD." },
  { clave: "hibrido_70_30", label: "70/30", ayuda: "0.7 × volumétrico + 0.3 × FOB. Propuesta si el 525k incluye aranceles ad valorem." },
];

/** Avisos del pipeline con texto. Los que no estén aquí se muestran crudos. */
export const AVISO: Record<string, string> = {
  amazon_sin_cambios_desde_18_sep: "Amazon sin cambios desde el 18-sep (caché)",
  amazon_precio_regular_woo_sin_cambios_desde_18_sep: "Amazon: precio regular de Woo, sin cambios desde el 18-sep",
  walmart_sin_cambios_desde_17_ago: "Walmart sin cambios desde el 17-ago (caché)",
  tiktok_precio_puede_venir_sin_iva: "TikTok: el precio guardado puede venir sin IVA",
  temu_basePrice_es_neto_que_paga_temu: "Temu: el precio es el neto que paga Temu (basePrice), no lo que paga el comprador",
  sin_costo: "Sin costo aterrizado",
  sin_peso: "Sin peso: no se calcula el envío",
  sin_precio: "Sin precio",
  sin_sku: "Sin SKU",
  sin_listing_id: "Sin ID de publicación",
  sin_fila_en_kubera: "No está en kubera (sólo en la API de ML)",
  precio_regular_de_woo: "Precio regular de Woo (no el del canal)",
  precio_sospechoso: "Precio sospechoso: revisar",
  costo_menor_a_1_peso: "Costo menor a $1: m³ sospechoso",
  comision_y_envio_con_tabla_full: "Comisión y envío con la tabla de FULL (no es FULL)",
  sku_con_varias_publicaciones_en_la_cuenta: "El SKU tiene varias publicaciones en la cuenta",
  listing_con_dos_skus_en_kubera: "La publicación tiene dos SKUs en kubera",
  ingreso_30d_parcial_lineas_sin_precio: "Ingreso 30 d parcial: hay líneas sin precio",
  costo_imposible: "Costo descartado: m³ imposible para su peso",
  riesgo_si_se_cobra_mercancia: "Riesgo si se cobra la mercancía (a su precio de hoy o en la bajada recomendada)",
  costo_bajo_fob: "Costo de 525k menor que la mercancía (informativo)",
  envio_con_columna_del_regular: "Envío con la tarifa del precio regular (promoción)",
  precio_cobrado_sin_medir: "Precio cobrado sin medir",
  ventas_del_sku_asignadas_a_otra_publicacion: "Las ventas del SKU se asignaron a otra publicación",
};

/** Avisos de publicación que no cambian nada: van en gris, sin alarma. */
export const AVISO_INFORMATIVO = new Set(["costo_bajo_fob", "comision_y_envio_con_tabla_full", "precio_regular_de_woo"]);

/** Tono del chip de un aviso de publicación. */
export function tonoAvisoPub(a: string): Tono {
  if (AVISO_INFORMATIVO.has(a)) return "slate";
  if (a === "costo_imposible" || a === "costo_menor_a_1_peso" || a === "precio_sospechoso") return "rose";
  return "amber";
}

/** Cómo se ejecuta el plan de ajuste. */
export const PLAN_MODO: Record<string, { label: string; ayuda: string }> = {
  semanal: { label: "semanal", ayuda: "Un paso por semana, ninguno mayor a 5 %." },
  diario: { label: "diario", ayuda: "Un paso por día, ninguno mayor a 2 % (el paso mínimo es $1)." },
  al_reactivar: { label: "al reactivar", ayuda: "Pausada: el precio se fija una sola vez, al volver a publicarla." },
  renovar_promo: { label: "renovar promoción", ayuda: "La promoción vence en 7 días o menos: el plan empieza ese día como una promoción nueva sobre el precio regular. Si no se renueva, el precio sube al regular." },
};

/**
 * Contra qué se mide el cambio (`cambio_ref`, DISENO §6). En las pausadas es el
 * precio REALIZADO de su base: su precio publicado está en mediana 1.61× arriba
 * de lo que de verdad pagaban.
 */
export const REF_CAMBIO: Record<string, { corto: string; largo: string }> = {
  precio_actual: { corto: "vs hoy", largo: "contra el precio de hoy" },
  precio_realizado_base: { corto: "vs lo que pagaban", largo: "contra lo que pagaban en su último periodo con oferta (precio realizado)" },
};

/** De dónde sale la referencia de competencia del optimizador. */
export const FUENTE_REF: Record<string, string> = {
  serp: "búsqueda",
  bestsellers: "más vendidos",
  sugerido_ml: "sugerido ML",
  price_to_win: "precio ganador",
};

/** Por qué un SKU de la muestra de packing no se pudo ubicar. */
export const MOTIVO_SALTO: Record<string, string> = {
  sin_sku_en_odoo: "El SKU no existe en Odoo",
  sin_container_numbers: "Odoo no dice de qué contenedor vino",
  sin_archivo: "No se encontró el packing list",
  sin_renglon: "Ningún renglón empata",
  ambiguo: "Varios renglones empatan igual",
  padre_con_muchas_variantes: "Padre con muchas variantes",
  pospuesto_archivo_grande: "Archivo grande: se pospuso",
};

export const CLASES_TONO: Record<Tono, string> = {
  indigo: "border-indigo-200 bg-indigo-50 text-indigo-700",
  emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
  rose: "border-rose-200 bg-rose-50 text-rose-700",
  amber: "border-amber-200 bg-amber-50 text-amber-800",
  slate: "border-slate-200 bg-slate-50 text-slate-600",
  sky: "border-sky-200 bg-sky-50 text-sky-700",
  violet: "border-violet-200 bg-violet-50 text-violet-700",
  teal: "border-teal-200 bg-teal-50 text-teal-800",
};
