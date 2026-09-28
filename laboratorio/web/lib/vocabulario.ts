/**
 * Vocabulario CERRADO del pipeline → texto para humanos. Si llega una llave que
 * no está aquí, se muestra cruda: es justo lo que hay que ver (mismo criterio
 * que `ESTADO_UI` en frontend/lib/publicaciones.ts).
 */
import type { Confianza, EstadoPub, FuenteCosto } from "./tipos";

export type Tono = "indigo" | "emerald" | "rose" | "amber" | "slate" | "sky" | "violet";

export const ESTADO: Record<EstadoPub, { label: string; tono: Tono; ayuda: string }> = {
  activa: { label: "Activa", tono: "emerald", ayuda: "Se puede comprar ahora." },
  pausada: { label: "Pausada", tono: "slate", ayuda: "Existe y está apagada (en FULL casi siempre por falta de stock en el almacén de ML)." },
  en_revision: { label: "En revisión", tono: "amber", ayuda: "ML la tiene retenida (under_review): no vende." },
  cerrada: { label: "Cerrada", tono: "slate", ayuda: "Se dio de baja." },
  inactiva: { label: "Inactiva", tono: "slate", ayuda: "El canal la reporta inactiva." },
  otra: { label: "Otra", tono: "slate", ayuda: "Estado que el laboratorio no clasifica." },
};

export const FUENTE_COSTO: Record<FuenteCosto, { label: string; tono: Tono; ayuda: string }> = {
  packing_list_exacto: { label: "exacto", tono: "emerald", ayuda: "Renglón ubicado en el packing list del contenedor: 525,000 × m³ de la pieza ÷ m³ totales del archivo." },
  prorrateo_kubera: { label: "prorrateo", tono: "indigo", ayuda: "525,000 ÷ m³ del contenedor reconstruido con costos_validados de kubera." },
  tarifa_7500: { label: "tarifa", tono: "amber", ayuda: "m³ de la pieza × $7,500 (la referencia del panel; equivale a suponer 70 m³)." },
  sin_costo: { label: "sin costo", tono: "slate", ayuda: "No hay m³ ni costo validado: no se calcula utilidad ni se recomienda precio." },
};

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
};

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
  walmart_sin_cambios_desde_17_ago: "Walmart sin cambios desde el 17-ago (caché)",
  tiktok_precio_puede_venir_sin_iva: "TikTok: el precio guardado puede venir sin IVA",
  sin_costo: "Sin costo aterrizado",
};

export const CLASES_TONO: Record<Tono, string> = {
  indigo: "border-indigo-200 bg-indigo-50 text-indigo-700",
  emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
  rose: "border-rose-200 bg-rose-50 text-rose-700",
  amber: "border-amber-200 bg-amber-50 text-amber-800",
  slate: "border-slate-200 bg-slate-50 text-slate-600",
  sky: "border-sky-200 bg-sky-50 text-sky-700",
  violet: "border-violet-200 bg-violet-50 text-violet-700",
};
