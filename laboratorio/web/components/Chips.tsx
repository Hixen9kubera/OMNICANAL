"use client";

/** Chips del vocabulario del laboratorio (estado, fuente de costo, razones…). */
import { AlertTriangle, ArrowDownRight, ArrowLeftRight, ArrowUpRight, BadgeCheck, Minus } from "lucide-react";
import { Chip } from "./ui";
import { CONFIANZA, ESTADO, ESTADO_DETALLE, METODO_EMPATE, RAZON, fuenteCosto } from "@/lib/vocabulario";
import type { Tono } from "@/lib/vocabulario";
import { pct, pctFirmado, pesos } from "@/lib/formato";
import type { Confianza, CostoPub, EstadoPub } from "@/lib/tipos";

/**
 * Estado de una publicación. «otra» se lee por su detalle (no comprable,
 * borrador…): 1,875 filas el 28-sep que como «Otra» no dirían nada.
 */
export function ChipEstado({ estado, sub, detalle }: { estado: EstadoPub | string; sub?: string[] | null; detalle?: string | null }) {
  const e = ESTADO[estado as EstadoPub];
  const d = detalle ? ESTADO_DETALLE[detalle] : undefined;
  const extra = sub && sub.length ? ` · ${sub.join(", ")}` : "";
  let label = e?.label ?? estado;
  let tono: Tono = e?.tono ?? "slate";
  if (detalle && estado === "otra") {
    label = d?.label ?? detalle;
    if (detalle === "puede_estar_activa") tono = "amber";
    if (detalle === "rechazada") tono = "rose";
  } else if (detalle) {
    label = `${label} · ${(d?.label ?? detalle).toLowerCase()}`;
  }
  const titulo = [e?.ayuda ?? estado, d?.ayuda].filter(Boolean).join(" ") + extra;
  return <Chip tono={tono} titulo={titulo}>{label}</Chip>;
}

/** Dirección de una propuesta. `umbral` = parametros.mantener_si_cambio_menor_a (0.01). */
export type Direccion = "subir" | "bajar" | "mantener" | "sin";
export function direccionDe(cambio: number | null | undefined, umbral = 0.01): Direccion {
  if (cambio === null || cambio === undefined || !Number.isFinite(cambio)) return "sin";
  if (cambio > umbral) return "subir";
  if (cambio < -umbral) return "bajar";
  return "mantener";
}

/** «↘ −20.2 %»: flecha + porcentaje. El color acompaña a la flecha, nunca va solo. */
export function ChipCambio({ cambio, umbral = 0.01 }: { cambio: number | null | undefined; umbral?: number }) {
  const d = direccionDe(cambio, umbral);
  if (d === "sin") return null;
  const Icono = d === "subir" ? ArrowUpRight : d === "bajar" ? ArrowDownRight : Minus;
  const tono = d === "subir" ? "text-emerald-700 bg-emerald-50" : d === "bajar" ? "text-sky-700 bg-sky-50" : "text-slate-500 bg-slate-100";
  const texto = d === "subir" ? "Subir" : d === "bajar" ? "Bajar" : "Mantener";
  return (
    <span title={`${texto} ${pctFirmado(cambio, 1)}`}
          className={`relative inline-flex items-center gap-0.5 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[10.5px] font-bold tabular-nums ${tono}`}>
      <Icono size={11} aria-hidden /><span className="sr-only">{texto} </span>{pctFirmado(cambio, 1)}
    </span>
  );
}

export function ChipFuenteCosto({ costo }: { costo: CostoPub | null }) {
  const f = fuenteCosto(costo?.fuente);
  // El texto del aviso de costo va en el tooltip SOLO si pone en duda el número
  // (costo_imposible, costo < $1); el riesgo de la mercancía tiene su propia marca.
  const duda = costo?.aviso && (costo.avisos ?? []).some((a) => a === "costo_imposible" || a === "costo_menor_a_1_peso") ? costo.aviso : "";
  const titulo = [f.ayuda, costo?.contenedor ? `Contenedor ${costo.contenedor}.` : "", duda].filter(Boolean).join(" ");
  return <Chip tono={f.tono} titulo={titulo}>{f.label}</Chip>;
}

/**
 * «Coordinado con la otra cuenta»: el mismo SKU está activo en Kubera y San
 * Corpe; solo baja la que más vende (o se acota la brecha entre las dos).
 */
export function ChipCoordinado({ razones, detalle }: { razones: string[] | null | undefined; detalle?: string | null }) {
  const rs = razones ?? [];
  const coord = rs.includes("coordinado_otra_cuenta");
  const brecha = rs.includes("brecha_entre_cuentas");
  if (!coord && !brecha) return null;
  const r = RAZON[coord ? "coordinado_otra_cuenta" : "brecha_entre_cuentas"];
  return (
    <span title={[r?.ayuda, detalle].filter(Boolean).join(" ")}
          className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10.5px] font-semibold text-teal-800">
      <ArrowLeftRight size={10} aria-hidden /> {coord ? "Coordinado con la otra cuenta" : "Brecha entre cuentas"}
    </span>
  );
}

/**
 * «Riesgo si se cobra la mercancía»: con el costo del panel (mercancía + flete)
 * la fila quedaría bajo el piso. Hoy NO aplica — el costo es el prorrateo de los
 * 525,000 por contenedor (regla de Brandon) —, así que es aviso, nunca bloqueo.
 */
export function ChipRiesgoMercancia({ margen, costoPanel, piso = 0.12, compacto, icono, donde }: {
  margen?: number | null; costoPanel?: number | null; piso?: number; compacto?: boolean; icono?: boolean; donde?: string;
}) {
  const detalle = costoPanel != null
    ? `Con el costo del panel (${pesos(costoPanel)}, mercancía + flete) el margen${donde ? ` ${donde}` : ""} sería ${margen == null ? "—" : pct(margen)}, bajo el piso de ${pct(piso, 0)}.`
    : "No hay costo del panel para medirlo.";
  const titulo = `Riesgo si se cobrara la mercancía. ${detalle} Hoy no aplica: el costo es el prorrateo de 525,000 por contenedor (regla de Brandon), así que no bloquea nada.`;
  if (icono) {
    // `relative`: el texto sr-only es absoluto y, sin ancestro posicionado, estiraba la página en móvil.
    return (
      <span title={titulo} className="relative inline-flex items-center text-amber-600">
        <AlertTriangle size={12} aria-hidden /><span className="sr-only">Riesgo si se cobra la mercancía</span>
      </span>
    );
  }
  return (
    <span title={titulo}
          className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10.5px] font-semibold text-amber-800">
      <AlertTriangle size={10} aria-hidden /> {compacto ? "Riesgo mercancía" : "Riesgo si se cobra la mercancía"}
    </span>
  );
}

/** El tag del panel: costo congelado por una persona (candado de COSTO VALIDADO). */
export function TagValidado({ por }: { por?: string | null }) {
  return (
    <span title={`Costo validado${por ? ` por ${por}` : ""} en el panel (costing.costos_validados.revisado_at).`}
          className="inline-flex items-center gap-0.5 rounded bg-emerald-600 px-1.5 py-[1px] text-[9px] font-bold uppercase tracking-wide text-white">
      <BadgeCheck size={10} /> Validado
    </span>
  );
}

export function ChipRazon({ razon }: { razon: string }) {
  const r = RAZON[razon];
  return <Chip tono={r?.tono ?? "slate"} titulo={r?.ayuda}>{r?.label ?? razon}</Chip>;
}

export function ChipConfianza({ confianza }: { confianza: Confianza }) {
  const c = CONFIANZA[confianza] ?? CONFIANZA.baja;
  return <Chip tono={c.tono} titulo={c.ayuda}>{c.label}</Chip>;
}

export function ChipEmpate({ metodo, distancia }: { metodo: string; distancia?: number | null }) {
  const m = METODO_EMPATE[metodo];
  return (
    <Chip tono={m?.tono ?? "slate"} titulo={m?.ayuda}>
      {m?.label ?? metodo}{distancia !== null && distancia !== undefined && metodo === "dhash" ? ` · ${distancia}` : ""}
    </Chip>
  );
}

/** «supuesto»: la comisión/envío del canal no están validados (parametros.canales). */
export function ChipSupuesto() {
  return (
    <span title="Comisión y envío de este canal son SUPUESTOS de parametros.json, no validados con contabilidad ni con el contrato del canal."
          className="inline-flex items-center rounded border border-dashed border-amber-400 bg-amber-50 px-1 py-[1px] text-[9px] font-bold uppercase tracking-wide text-amber-700">
      supuesto
    </span>
  );
}

export function ChipFull() {
  return (
    <span title="Logística FULL (fulfillment): sale del almacén de Mercado Libre."
          className="inline-flex items-center rounded-md bg-slate-900 px-1.5 py-[1px] text-[9.5px] font-extrabold italic tracking-wide text-[#FFE600]">
      FULL
    </span>
  );
}
