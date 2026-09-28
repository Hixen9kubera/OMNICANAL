/**
 * Colores de la casa. Canales: espejo de `backend/core/marketplaces.py`
 * (vía `frontend/lib/theme.ts`). Cuentas: `CUENTA_DOT` de `frontend/lib/canales.ts`
 * — BEKURA sky-500, SANCORFASHION violet-500. El mapa de frontend notó que
 * `fulfillment/ui.tsx` usa sky-400 para BEKURA: aquí va el de `CUENTA_DOT`.
 *
 * Paletas de gráfica validadas con el validador de dataviz (modo claro):
 *   cuentas  #0EA5E9 · #8B5CF6  → CVD ΔE 10.9, visión normal 19.8. El sky queda
 *            a 2.7:1 sobre blanco: por eso cada gráfica de cuentas trae leyenda y
 *            su tabla (la regla de «alivio»).
 *   series   #4F46E5 · #EA580C · #0D9488 → CVD ΔE 13.8, normal 28.8, todo ≥ 3:1.
 */
import type { Canal } from "./tipos";

export interface TemaCanal { nombre: string; corto: string; color: string; texto: string; acento: string; suave: string }

export const TEMA_CANAL: Record<Canal, TemaCanal> = {
  mercado_libre: { nombre: "Mercado Libre", corto: "ML", color: "#FFE600", texto: "#2D3277", acento: "#3483FA", suave: "#FFFBE0" },
  amazon: { nombre: "Amazon", corto: "AMZ", color: "#FF9900", texto: "#131A22", acento: "#232F3E", suave: "#FFF4E0" },
  walmart: { nombre: "Walmart", corto: "WMT", color: "#0071DC", texto: "#FFFFFF", acento: "#FFC220", suave: "#E6F1FC" },
  temu: { nombre: "Temu", corto: "TEMU", color: "#FB7701", texto: "#FFFFFF", acento: "#FF5000", suave: "#FFF1E6" },
  tiktok: { nombre: "TikTok Shop", corto: "TT", color: "#000000", texto: "#FFFFFF", acento: "#FE2C55", suave: "#F1F5F9" },
};

export const COLOR_CUENTA: Record<string, string> = {
  BEKURA: "#0EA5E9",
  SANCORFASHION: "#8B5CF6",
};

export const NOMBRE_CUENTA: Record<string, string> = {
  BEKURA: "Kubera",
  SANCORFASHION: "San Corpe",
  AMAZON: "Amazon",
  WALMART: "Walmart",
  TEMU: "Temu",
  KUBERA: "TikTok Kubera",
};

/** Punto de una publicación: la cuenta si es ML; el color del canal si no. */
export function puntoDe(canal: Canal, cuenta: string): string {
  if (canal === "mercado_libre") return COLOR_CUENTA[cuenta] ?? "#cbd5e1";
  return TEMA_CANAL[canal]?.color ?? "#cbd5e1";
}

/** Series de las gráficas (orden fijo: el color sigue a la serie, nunca al rango). */
export const SERIE = {
  principal: "#4F46E5",   // precio realizado / curvas
  recomendado: "#EA580C", // todo lo que es «propuesta»
  secundaria: "#0D9488",  // precio ofrecido
  tinta: "#0F172A",       // marcador «actual»
  referencia: "#64748B",  // competencia
  perdida: "#E11D48",     // zona bajo el equilibrio
  delgada: "#D97706",     // zona entre equilibrio y piso
  rejilla: "#EEF0F4",
  eje: "#CBD5E1",
};

/** El sombreado rayado de «sin dato ≠ 0» (copia de fulfillment/ui.tsx). */
export const RAYADO = {
  background: "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)",
  border: "1px dashed #cbd5e1",
} as const;
