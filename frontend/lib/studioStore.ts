// studioStore.ts — Persistencia EN MEMORIA de lo mejorado por IA en el Estudio.
//
// Guarda por (sku, canal) el contenido mejorado y por sku el precio de
// competencia. Sobrevive a cerrar/reabrir el panel del Estudio dentro de la
// misma sesión, pero SE PIERDE al recargar la página (no se guarda en la DB),
// tal como se pidió.

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

const store = new Map<string, EstudioSku>();

function _sku(sku: string): EstudioSku {
  let e = store.get(sku);
  if (!e) {
    e = { porCanal: {} };
    store.set(sku, e);
  }
  return e;
}

export function getMejora(sku: string, canal: string): MejoraCanal | undefined {
  return store.get(sku)?.porCanal[canal];
}

export function setMejora(sku: string, canal: string, m: MejoraCanal): void {
  _sku(sku).porCanal[canal] = m;
}

export function getCompetencia(sku: string): CompetenciaResp | undefined {
  return store.get(sku)?.competencia;
}

export function setCompetencia(sku: string, comp: CompetenciaResp): void {
  _sku(sku).competencia = comp;
}
