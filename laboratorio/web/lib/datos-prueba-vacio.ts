/**
 * Sustituto de `datos-prueba.ts` en todo build que no sea `next dev` con
 * fixtures (lo decide next.config.mjs). Existe para que ningún JSON de prueba
 * llegue al export que se publica: la web publicada SIEMPRE pregunta a /api/lab.
 */
import type { Params } from "./api";

export async function responder(_camino: string, _params: Params, _metodo: string, _cuerpo?: unknown): Promise<unknown | null> {
  return null;
}

export async function csvPrecios(): Promise<string | null> {
  return null;
}
