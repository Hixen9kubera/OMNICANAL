"use client";

/**
 * Piezas visuales compartidas por las vistas de FULLFILMENT.
 *
 * `RAYADO`, `ChipSinRegistro` y `BotonBloqueado` son COPIAS de las funciones
 * locales de `app/monitoreo/page.tsx` y `app/inventario/page.tsx` (allá no se
 * exportan). Si cambia el lenguaje visual del panel, cambia en los tres lados.
 *
 * LAS CUATRO LECTURAS de cada cifra (regla dura del diseño):
 *   dato real        → esmeralda sólido
 *   cero real        → caja blanca con borde: pasó y fue cero
 *   sin dato todavía → RAYADO + chip «sin registro»: no es un cero
 *   en espera        → ámbar: el dato viene y todavía no llega
 */

import type { CSSProperties, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import type { Canal, Cuenta, Envio, Instante } from "./tipos";
import { ETAPAS } from "./tipos";

// ── Las texturas ────────────────────────────────────────────────────────────
export const RAYADO: CSSProperties = {
  background: "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)",
  border: "1px dashed #cbd5e1",
};
export const FONDO_RAYADO = "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)";
export const FONDO_RAYADO_AMBAR = "repeating-linear-gradient(135deg,#FEF3C7 0 5px,#FDE68A 5px 10px)";

// ── Canales y cuentas ───────────────────────────────────────────────────────
// Colores de `lib/theme.ts` (espejo de backend/core/marketplaces.py). `chip` es
// el color de texto del chip sobre `suave`; sobre el amarillo de ML va #2D3277.
export const TEMA_CANAL: Record<Canal | "todos", {
  nombre: string; color: string; texto: string; acento: string; suave: string; borde: string; chip: string;
}> = {
  meli: { nombre: "Mercado Libre", color: "#FFE600", texto: "#2D3277", acento: "#3483FA", suave: "#FFFBE0", borde: "#FFE600", chip: "#2D3277" },
  amazon: { nombre: "Amazon FBA", color: "#FF9900", texto: "#131A22", acento: "#232F3E", suave: "#FFF4E0", borde: "#f1d3a0", chip: "#131A22" },
  walmart: { nombre: "Walmart WFS", color: "#0071DC", texto: "#FFFFFF", acento: "#FFC220", suave: "#E6F1FC", borde: "#bcd9f6", chip: "#0071DC" },
  todos: { nombre: "Todos", color: "#4F46E5", texto: "#FFFFFF", acento: "#818CF8", suave: "#EEF0FF", borde: "#c7d2fe", chip: "#3730a3" },
};

/** Mismo significado que `CUENTA_DOT` de lib/canales.ts (sky = BEKURA, violet = SANCOR). */
export const PUNTO_CUENTA: Record<Cuenta, string> = {
  Kubera: "#38BDF8",
  "San Corpe": "#8B5CF6",
};

export function puntoDe(canal: Canal, cuenta: Cuenta | null): string {
  if (canal === "amazon") return "#FF9900";   // FBA NO va en sky: sky ya es Kubera
  if (canal === "walmart") return "#cbd5e1";
  return cuenta ? PUNTO_CUENTA[cuenta] : "#cbd5e1";
}

// ── Formatos ────────────────────────────────────────────────────────────────
export const num = (n: number | null | undefined, vacio = "—") =>
  n === null || n === undefined ? vacio : n.toLocaleString("es-MX");

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const FMT_CDMX = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Mexico_City", year: "numeric", month: "numeric", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

/** «02 sep 11:02» en hora de CDMX; «31 ago ~11:00» si la hora es observada. */
export function fecha(i: Instante | null | undefined): string {
  if (!i) return "sin dato";
  const partes = Object.fromEntries(
    FMT_CDMX.formatToParts(new Date(i.ts)).map((p) => [p.type, p.value]));
  const hora = `${partes.hour === "24" ? "00" : partes.hour}:${partes.minute}`;
  return `${partes.day} ${MESES[Number(partes.month) - 1]} ${i.aprox ? "~" : ""}${hora}`;
}

// ── Contenedores ────────────────────────────────────────────────────────────
export function Tarjeta({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white p-[18px] shadow-card ${className}`}>
      {children}
    </section>
  );
}

/** El rótulo chico en mono que va arriba de cada título. */
export function Ceja({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">{children}</p>
  );
}

export function ChipSinRegistro({ titulo, texto = "sin registro" }: { titulo?: string; texto?: string }) {
  return (
    <span title={titulo} style={RAYADO}
      className="inline-flex items-center rounded bg-white/75 px-2 py-[3px] font-mono text-[9.5px] font-bold uppercase tracking-[.05em] text-slate-400">
      {texto}
    </span>
  );
}

export function ChipDatosDesde({ desde }: { desde: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[.04em] text-amber-700">
      <span className="h-2.5 w-2.5 rounded-full border-2 border-dashed border-amber-600" />
      Datos desde {desde}
    </span>
  );
}

export function BotonBloqueado({
  icono: Icono, texto, razon, primario,
}: { icono: LucideIcon; texto: string; razon: string; primario?: boolean }) {
  return (
    <button
      type="button" disabled title={`Bloqueado — ${razon}`}
      className={`flex cursor-not-allowed items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold ${
        primario ? "bg-indigo-300 text-white" : "border border-slate-200 bg-white text-slate-300"}`}
    >
      <Icono className="h-4 w-4" /> {texto}
    </button>
  );
}

export function ChipCanal({ canal, cuenta }: { canal: Canal; cuenta: Cuenta | null }) {
  const t = TEMA_CANAL[canal];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11.5px] font-bold"
          style={{ borderColor: t.borde, background: t.suave, color: t.chip }}>
      <span className="h-2 w-2 rounded-full" style={{ background: puntoDe(canal, cuenta) }} />
      {t.nombre}
    </span>
  );
}

// ── El rail de siete etapas ─────────────────────────────────────────────────
export interface Paso {
  t: string; v: string; sub: string; tono: "dato" | "espera" | "hueco"; titulo: string;
}

/** Traduce las etapas de un envío a su lectura. Ninguna se deduce de otra. */
export function pasosDe(e: Envio): Paso[] {
  return ETAPAS.map(({ t, sub }, i) => {
    const inst = e.etapas[i];
    if (inst) {
      return { t, v: fecha(inst), sub, tono: "dato" as const,
               titulo: `${t} · ${fecha(inst)} (hora de CDMX)${inst.aprox ? " — hora observada, no la del evento" : ""}` };
    }
    // «Recibido» en curso es ÁMBAR: el dato viene, no ha llegado. No se resta
    // enviadas − recibidas para inventar un rechazo.
    if (i === 4 && (e.estado === "recepcion" || e.estado === "amazonSinLectura")) {
      const amz = e.estado === "amazonSinLectura";
      const v = amz ? "sin lectura" : "en recepción";
      return { t, v, sub: amz ? "Amazon no se consulta" : "el almacén no ha contado", tono: "espera" as const,
               titulo: `${t} · ${v} — no se resta para inventar un rechazo` };
    }
    return { t, v: "sin dato", sub, tono: "hueco" as const,
             titulo: `${t}: no hay registro todavía. No es un cero.` };
  });
}

const TONO_PASO = {
  dato: { caja: "border-emerald-200 bg-emerald-50", rotulo: "text-emerald-700", valor: "text-emerald-800" },
  espera: { caja: "border-amber-300 bg-amber-50", rotulo: "text-amber-700", valor: "text-amber-800" },
  hueco: { caja: "border-dashed border-slate-300", rotulo: "text-slate-400", valor: "text-slate-400" },
};

export function Rail({ pasos, grande }: { pasos: Paso[]; grande?: boolean }) {
  return (
    <div className={`flex items-stretch ${grande ? "gap-2" : "gap-[3px]"}`}>
      {pasos.map((p) => {
        const tono = TONO_PASO[p.tono];
        return (
          <div key={p.t} title={p.titulo}
               style={p.tono === "hueco" ? { background: FONDO_RAYADO } : undefined}
               className={`min-w-0 flex-1 rounded-lg border ${tono.caja} ${grande ? "p-3" : "px-[7px] py-1.5"}`}>
            <div className={`truncate font-bold uppercase tracking-[.04em] ${tono.rotulo} ${grande ? "text-[10.5px]" : "text-[9.5px]"}`}>
              {p.t}
            </div>
            <div className={`mt-0.5 truncate font-mono font-bold ${tono.valor} ${grande ? "text-[13px]" : "text-[10.5px]"}`}>
              {p.v}
            </div>
            {grande && <div className={`mt-0.5 truncate text-[11px] ${tono.rotulo} opacity-80`}>{p.sub}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** La tasa de recepción de un envío. «no calculable» y «en recepción» NO son 0%. */
export function tasaDe(e: Envio): { texto: string; nota: string; clase: string; rayada?: boolean } {
  switch (e.estado) {
    case "sinEnlazar":
      return { texto: "no calculable", nota: "sin número de envío", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "wfs":
      return { texto: "sin registro", nota: "no hay órdenes WFS", clase: "border-dashed border-slate-300 text-slate-400", rayada: true };
    case "recepcion":
      return { texto: "en recepción", nota: "aún no es un rechazo", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "amazonSinLectura":
      return { texto: "sin registro", nota: "Amazon no se consulta", clase: "border-dashed border-slate-300 text-slate-400", rayada: true };
    default: {
      const alto = (e.tasaPct ?? 0) >= 95;
      return {
        texto: `${e.tasaPct}% recibido`,
        nota: alto ? "rechazo explícito bajo" : "rechazo explícito del canal",
        clase: alto ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-rose-200 bg-rose-50 text-rose-800",
      };
    }
  }
}
