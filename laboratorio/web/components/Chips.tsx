"use client";

/** Chips del vocabulario del laboratorio (estado, fuente de costo, razones…). */
import { BadgeCheck } from "lucide-react";
import { Chip } from "./ui";
import { CONFIANZA, ESTADO, FUENTE_COSTO, METODO_EMPATE, RAZON } from "@/lib/vocabulario";
import type { Confianza, CostoPub, EstadoPub } from "@/lib/tipos";

export function ChipEstado({ estado, sub }: { estado: EstadoPub | string; sub?: string[] | null }) {
  const e = ESTADO[estado as EstadoPub];
  const extra = sub && sub.length ? ` · ${sub.join(", ")}` : "";
  return <Chip tono={e?.tono ?? "slate"} titulo={(e?.ayuda ?? estado) + extra}>{e?.label ?? estado}</Chip>;
}

export function ChipFuenteCosto({ costo }: { costo: CostoPub | null }) {
  const f = FUENTE_COSTO[costo?.fuente ?? "sin_costo"];
  const titulo = [f.ayuda, costo?.contenedor ? `Contenedor ${costo.contenedor}.` : ""].filter(Boolean).join(" ");
  return <Chip tono={f.tono} titulo={titulo}>{f.label}</Chip>;
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
