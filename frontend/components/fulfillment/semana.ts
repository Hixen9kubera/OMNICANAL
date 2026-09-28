/**
 * La SEMANA de FULLFILMENT (Brandon, 28-sep-2026: "la planeación es week over week…
 * detectar qué día es hoy y crear el FULL de la semana… los envíos se deberán poder
 * filtrar week over week, al igual que análisis").
 *
 * Es la semana ISO: de lunes a domingo, en hora de CDMX, con la misma clave que usa el
 * backend (`services/fulfillment_semana.py`) y Análisis: «2026-S40». México ya no cambia
 * de horario, así que el lunes empieza a las 00:00 de CDMX = 06:00 UTC todo el año.
 */

import { rangoSemana } from "./ui";

export interface Semana {
  clave: string;       // «2026-S40»
  semana: string;      // «S40»
  anio: number;
  numero: number;
  lunes: string;       // «2026-09-28»
  domingo: string;
  rango: string;       // «28 sep – 4 oct»
}

const DIA_MS = 86_400_000;
const FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Mexico_City", year: "numeric", month: "numeric", day: "numeric",
});

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** El lunes (UTC, a medianoche) de la semana ISO `numero` de `anio`. */
function lunesDe(anio: number, numero: number): Date {
  const cuatroEne = new Date(Date.UTC(anio, 0, 4));
  const lunes1 = new Date(cuatroEne.getTime() - ((cuatroEne.getUTCDay() + 6) % 7) * DIA_MS);
  return new Date(lunes1.getTime() + (numero - 1) * 7 * DIA_MS);
}

function armar(anio: number, numero: number): Semana {
  const lunes = lunesDe(anio, numero);
  return {
    clave: `${anio}-S${numero}`, semana: `S${numero}`, anio, numero,
    lunes: iso(lunes), domingo: iso(new Date(lunes.getTime() + 6 * DIA_MS)), rango: rangoSemana(iso(lunes)),
  };
}

/** La semana de un instante (por omisión, ahora), en hora de CDMX. */
export function semanaDe(instante: Date | string = new Date()): Semana {
  const p = Object.fromEntries(FMT.formatToParts(new Date(instante)).map((x) => [x.type, x.value]));
  const dia = new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)));
  // La semana ISO es la de su JUEVES: el jueves decide el año y el número.
  const jueves = new Date(dia.getTime() + (3 - ((dia.getUTCDay() + 6) % 7)) * DIA_MS);
  const anio = jueves.getUTCFullYear();
  const numero = 1 + Math.round((jueves.getTime() - lunesDe(anio, 1).getTime() - 3 * DIA_MS) / (7 * DIA_MS));
  return armar(anio, numero);
}

/** «2026-S40» → la semana. null si la clave no es una semana. */
export function semanaPorClave(clave: string | null | undefined): Semana | null {
  const m = /^(\d{4})-S(\d{1,2})$/.exec((clave ?? "").trim());
  if (!m) return null;
  const s = armar(Number(m[1]), Number(m[2]));
  // Una semana 53 que el año no tiene cae en el año siguiente: no es válida.
  return semanaDe(`${s.lunes}T12:00:00-06:00`).clave === s.clave ? s : null;
}

/** La semana `n` semanas antes (n < 0) o después (n > 0). */
export function moverSemana(clave: string, n: number): Semana {
  const s = semanaPorClave(clave) ?? semanaDe();
  return semanaDe(new Date(Date.parse(`${s.lunes}T12:00:00-06:00`) + n * 7 * DIA_MS));
}

/** La semana en curso y las `cuantas - 1` anteriores, de la más nueva a la más vieja. */
export function semanasHasta(actual: Semana, cuantas: number): Semana[] {
  return Array.from({ length: cuantas }, (_, i) => moverSemana(actual.clave, -i));
}

/** ¿Ese instante cae en esa semana? */
export const enSemana = (instante: string | null | undefined, clave: string) =>
  !!instante && semanaDe(instante).clave === clave;
