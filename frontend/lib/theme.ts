// theme.ts — Utilidades de color por marketplace.
// El color principal lo entrega el backend (/api/canales). Aquí derivamos
// variantes (texto legible, color suave de fondo) y un mapa de respaldo por si
// el backend no está disponible.

import type { CSSProperties } from "react";

export interface CanalTheme {
  color: string;
  texto: string;
  acento: string;
  suave: string; // fondo tenue del color principal
}

// Respaldo (coincide con backend/core/marketplaces.py).
export const THEME_FALLBACK: Record<string, CanalTheme> = {
  general: { color: "#4F46E5", texto: "#FFFFFF", acento: "#818CF8", suave: "#EEF0FF" },
  mercado_libre: { color: "#FFE600", texto: "#2D3277", acento: "#3483FA", suave: "#FFFBE0" },
  amazon: { color: "#FF9900", texto: "#131A22", acento: "#232F3E", suave: "#FFF4E0" },
  tiktok: { color: "#111827", texto: "#FFFFFF", acento: "#FE2C55", suave: "#F1F1F4" },
  walmart: { color: "#0071DC", texto: "#FFFFFF", acento: "#FFC220", suave: "#E6F1FC" },
  temu: { color: "#FB7701", texto: "#FFFFFF", acento: "#FF5000", suave: "#FFF0E3" },
  shein: { color: "#111827", texto: "#FFFFFF", acento: "#7C3AED", suave: "#F1F1F4" },
};

/** Convierte un hex a rgba con alpha. */
export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** ¿El color es claro? (para decidir texto oscuro/claro). */
export function esClaro(hex: string): boolean {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  // Luminancia relativa
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.7;
}

/**
 * Devuelve las variables CSS que tematizan toda la interfaz según el canal.
 * Se aplican en un contenedor y los componentes usan bg-mp, text-mp, etc.
 */
export function variablesTema(theme: CanalTheme): CSSProperties {
  return {
    ["--mp-color" as string]: theme.color,
    ["--mp-text" as string]: theme.texto,
    ["--mp-accent" as string]: theme.acento,
    ["--mp-soft" as string]: theme.suave ?? hexToRgba(theme.color, 0.1),
  };
}
