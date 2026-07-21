// studioStore.ts — Borradores del Estudio persistidos en localStorage.
//
// Guarda por (sku, canal) el contenido editado/mejorado (título, descripción,
// atributos, highlights, bullets) y por sku el precio de competencia.
//
// A diferencia de la versión anterior (solo memoria), ahora se respalda en
// localStorage: los borradores SOBREVIVEN al recargar la página (son borradores
// de trabajo, por navegador). No tocan WooCommerce; para persistir en Woo está
// el botón "Guardar contenido" del canal General.

import type { AtributoProducto, CompetenciaResp } from "./types";

export interface MejoraCanal {
  titulo?: string;
  descripcion?: string;
  highlights?: string;
  bullets?: string[];
  atributos?: AtributoProducto[];
}

interface EstudioSku {
  porCanal: Record<string, MejoraCanal>;
  competencia?: CompetenciaResp;
}

// v2 (2026-07-21): se invalidaron TODOS los borradores v1 — la condición de
// carrera de "Mejorar con IA" (arreglada en v0.12.1) dejó borradores con
// contenido de OTRO producto (binoculares en ACC-0653). Subir la versión
// huerfana los borradores viejos en TODOS los navegadores de un golpe: los
// campos recargan desde WooCommerce. Lo guardado/publicado no se toca.
const KEY = (sku: string) => `omnicanal:studio:v2:${sku}`;

// Cache en memoria para no leer/parsear localStorage en cada acceso dentro de
// la sesión. Se hidrata desde localStorage la primera vez que se toca un sku.
const store = new Map<string, EstudioSku>();

function _load(sku: string): EstudioSku {
  const cached = store.get(sku);
  if (cached) return cached;
  let e: EstudioSku = { porCanal: {} };
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(KEY(sku));
      if (raw) {
        const parsed = JSON.parse(raw) as EstudioSku;
        if (parsed && typeof parsed === "object" && parsed.porCanal) e = parsed;
      }
    } catch {
      /* JSON inválido / acceso denegado → borrador vacío */
    }
  }
  store.set(sku, e);
  return e;
}

function _persist(sku: string, e: EstudioSku): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY(sku), JSON.stringify(e));
  } catch {
    /* cuota llena / modo privado → se queda solo en memoria */
  }
}

export function getMejora(sku: string, canal: string): MejoraCanal | undefined {
  return _load(sku).porCanal[canal];
}

export function setMejora(sku: string, canal: string, m: MejoraCanal): void {
  const e = _load(sku);
  e.porCanal[canal] = m;
  _persist(sku, e);
}

export function getCompetencia(sku: string): CompetenciaResp | undefined {
  return _load(sku).competencia;
}

export function setCompetencia(sku: string, comp: CompetenciaResp): void {
  const e = _load(sku);
  e.competencia = comp;
  _persist(sku, e);
}

// Borra el borrador local de un sku (todos sus canales). Útil, por ejemplo,
// después de guardar el contenido en WooCommerce si se quiere partir de limpio.
export function limpiarBorrador(sku: string): void {
  store.delete(sku);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(KEY(sku));
    } catch {
      /* no-op */
    }
  }
}
