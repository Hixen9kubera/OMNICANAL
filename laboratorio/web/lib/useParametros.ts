"use client";

/**
 * Parámetros del último cálculo (`/parametros` → `_usados_en_ultimo_calculo`,
 * que es `precios.json.parametros`). La web los lee en vez de repetir números:
 * si el optimizador cambia el umbral de «mantener», las flechas cambian con él.
 */
import { usePedido } from "./usePedido";

interface Parametros {
  _usados_en_ultimo_calculo?: Record<string, unknown> | null;
  [k: string]: unknown;
}

export function useParametros(): Record<string, unknown> | null {
  const { datos } = usePedido<Parametros>("/parametros");
  return (datos?._usados_en_ultimo_calculo as Record<string, unknown> | null | undefined) ?? null;
}

/** |cambio| ≤ umbral = «mantener» (optimizador.MANTENER_PCT, 1 % el 28-sep). */
export function umbralDe(p: Record<string, unknown> | null | undefined): number {
  const u = Number(p?.mantener_si_cambio_menor_a);
  return Number.isFinite(u) && u > 0 ? u : 0.01;
}

export function useUmbralMantener(): number {
  return umbralDe(useParametros());
}
