"use client";

/**
 * Piezas visuales compartidas por las vistas de FULLFILMENT.
 *
 * `RAYADO` y `ChipSinRegistro` son COPIAS de las funciones locales de
 * `app/monitoreo/page.tsx` y `app/inventario/page.tsx` (allá no se exportan).
 * Si cambia el lenguaje visual del panel, cambia en los tres lados.
 *
 * LAS CUATRO LECTURAS de cada cifra (regla dura del diseño):
 *   dato real        → esmeralda sólido
 *   cero real        → caja blanca con borde: pasó y fue cero
 *   sin dato todavía → RAYADO + chip «sin registro»: no es un cero
 *   en espera        → ámbar: el dato viene y todavía no llega
 */

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import type { Canal, Cuenta, Envio, Instante } from "./tipos";
import { ETAPAS, ETAPAS_POR_CAPTURAR } from "./tipos";

// ── Las texturas ────────────────────────────────────────────────────────────
export const RAYADO: CSSProperties = {
  background: "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)",
  border: "1px dashed #cbd5e1",
};
export const FONDO_RAYADO = "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)";
export const FONDO_RAYADO_AMBAR = "repeating-linear-gradient(135deg,#FEF3C7 0 5px,#FDE68A 5px 10px)";
export const RAYADO_ROSA = "repeating-linear-gradient(135deg,#fecdd3 0 4px,#fb7185 4px 8px)";

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
  // Un dato por DÍA no lleva hora: inventarle "18:00" sería mentir.
  if (i.dia) return `${partes.day} ${MESES[Number(partes.month) - 1]}`;
  const hora = `${partes.hour === "24" ? "00" : partes.hour}:${partes.minute}`;
  return `${partes.day} ${MESES[Number(partes.month) - 1]} ${i.aprox ? "~" : ""}${hora}`;
}

/** «13 ene» en hora de CDMX. */
export function dia(iso: string | null | undefined): string {
  if (!iso) return "—";
  const partes = Object.fromEntries(FMT_CDMX.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${Number(partes.day)} ${MESES[Number(partes.month) - 1]}`;
}

/** «14–20 sep» para el lunes de una semana (fecha sin hora, «2026-09-14»). */
export function rangoSemana(lunes: string): string {
  const [a, m, d] = lunes.split("-").map(Number);
  const ini = new Date(Date.UTC(a, m - 1, d));
  const fin = new Date(Date.UTC(a, m - 1, d + 6));
  const mi = MESES[ini.getUTCMonth()];
  const mf = MESES[fin.getUTCMonth()];
  return mi === mf ? `${ini.getUTCDate()}–${fin.getUTCDate()} ${mf}` : `${ini.getUTCDate()} ${mi} – ${fin.getUTCDate()} ${mf}`;
}

/** Semana ISO de un instante, en hora de CDMX: «2026-S38». */
export function semanaIso(iso: string): string {
  const p = Object.fromEntries(FMT_CDMX.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  // La semana ISO es la de su JUEVES: el jueves decide el año y el número.
  const jueves = (f: Date) => {
    const x = new Date(f);
    x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7) + 3);
    return x;
  };
  const j = jueves(new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day))));
  const j1 = jueves(new Date(Date.UTC(j.getUTCFullYear(), 0, 4)));
  return `${j.getUTCFullYear()}-S${1 + Math.round((j.getTime() - j1.getTime()) / (7 * 86_400_000))}`;
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

/** De dónde sale la cifra: «Odoo en vivo», «ML en vivo»… El `title` dice cómo se lee. */
export function ChipFuente({ titulo, texto }: { titulo?: string; texto?: string }) {
  return (
    <span title={titulo ?? "Se lee de Odoo en cada carga (caché de 2 min)."}
          className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-[.05em] text-emerald-700">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> {texto ?? "Odoo en vivo"}
    </span>
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

export function ChipCuenta({ cuenta }: { cuenta: Cuenta }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-[3px] text-[11.5px] font-bold text-slate-600">
      <span className="h-2 w-2 rounded-full" style={{ background: PUNTO_CUENTA[cuenta] }} />
      {cuenta}
    </span>
  );
}

// ── La ventana emergente ────────────────────────────────────────────────────
// Se cierra con la ✕, con Esc o con clic fuera, y se pueden APILAR: la ficha
// del SKU se abre ENCIMA del detalle del envío (Brandon, 24-sep: "el por SKU es
// solamente un POP"). Esc cierra sólo la de arriba, y la página de atrás no se
// desplaza mientras quede alguna abierta.
//
// Va en un portal a <body>: el cuadro de la ventana se anima con `transform`, y
// un `position: fixed` DENTRO de algo transformado deja de medirse contra la
// pantalla — la ventana de encima quedaría encerrada en la de abajo.
const pila: number[] = [];
let consecutivo = 0;
let abiertas = 0;
let overflowOriginal = "";

export function Ventana({
  onCerrar, etiqueta, ancho = "max-w-[1080px]", children,
}: { onCerrar: () => void; etiqueta: string; ancho?: string; children: ReactNode }) {
  const cerrar = useRef(onCerrar);
  cerrar.current = onCerrar;
  const [id] = useState(() => ++consecutivo);
  const [montada, setMontada] = useState(false);

  useEffect(() => {
    pila.push(id);
    if (abiertas++ === 0) {
      overflowOriginal = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    const alTeclear = (ev: KeyboardEvent) => {
      if (ev.key === "Escape" && pila[pila.length - 1] === id) cerrar.current();
    };
    window.addEventListener("keydown", alTeclear);
    setMontada(true);
    return () => {
      window.removeEventListener("keydown", alTeclear);
      const i = pila.indexOf(id);
      if (i >= 0) pila.splice(i, 1);
      if (--abiertas === 0) document.body.style.overflow = overflowOriginal;
    };
  }, [id]);

  if (!montada || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 px-3 py-8 backdrop-blur-[2px] sm:px-4 sm:py-10"
         onClick={(ev) => { ev.stopPropagation(); cerrar.current(); }}>
      <div role="dialog" aria-modal="true" aria-label={etiqueta}
           onClick={(ev) => ev.stopPropagation()}
           className={`w-full ${ancho} animate-fade-in rounded-2xl bg-white shadow-2xl`}>
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** El botón ✕ de la esquina de una ventana. */
export function BotonCerrar({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} title="Cerrar (Esc)"
            className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
      <X className="h-5 w-5" />
    </button>
  );
}

// ── El rail de siete etapas ─────────────────────────────────────────────────
export interface Paso {
  t: string; corto: string; v: string;
  /** Valor para la celda angosta de la tabla, donde el texto largo se corta. */
  vc?: string;
  sub: string;
  /** Cuántos SKUs llegaron a esa etapa; va debajo de la fecha en el rail C2. */
  conteo?: string;
  /** El conteo trae una mala noticia (ML no recibió piezas): se pinta en rosa. */
  alerta?: boolean;
  // `pendiente` es un hueco que además PIDE ALGO: lleva el verbo y a quién le toca.
  tono: "dato" | "espera" | "hueco" | "pendiente";
  titulo: string;
}

/** Por qué la etapa «Recibido» no tiene dato, según el estado del envío real. */
const SIN_LECTURA: Partial<Record<Envio["estado"], { sub: string; titulo: string }>> = {
  salio: { sub: "sin avisos que leer",
           titulo: "Este envío no tiene avisos de FULL de Mercado Libre que medir (salió antes del 12-ago o sin cuenta)." },
  sinEnlazar: { sub: "sin número, no se cruza",
                titulo: "Sin número de envío ni cuenta no hay con qué cruzar lo que salió contra lo que llegó." },
  fbaSinLectura: { sub: "Amazon: sin permiso",
                   titulo: "La app de Amazon no tiene el permiso de Inbound (responde 403): no se pueden leer los envíos." },
  wfsSinLectura: { sub: "falta leer WFS",
                   titulo: "La API de WFS en México sí responde; falta conectarla al panel." },
};

/** Traduce las etapas de un envío a su lectura. Ninguna se deduce de otra. */
export function pasosDe(e: Envio): Paso[] {
  const abierta = e.estado_odoo !== undefined && e.estado_odoo !== "done";
  const c = e.cobertura;
  // Las tres últimas etapas son por SKU: el rail enseña la primera fecha y
  // CUÁNTOS de los SKUs del envío llegaron ahí. Sin eso, una sola pieza de un
  // SKU parecería el envío entero.
  // ML (avisos): el Recibido dice cuántos SKUs llegaron COMPLETOS y, con el
  // envío cerrado, cuánto no recibió ML. Antes del cierre es "en proceso".
  const avisos = c?.fuente === "avisos";
  const recibido = !c ? undefined
    : !avisos ? `${c.llegaron} de ${c.skus} SKUs`
    : c.rechazadas ? `${c.completos ?? 0} de ${c.skus} completos · ML no recibió ${num(c.rechazadas)} pzs`
    : c.cerrado || c.completos === c.skus ? `${c.completos ?? 0} de ${c.skus} SKUs completos`
    : `${num(c.piezas_llegadas)} de ${num(c.piezas_enviadas ?? null)} pzs · en proceso`;
  const cobertura = [undefined, undefined, recibido,
                     c && `${c.activos} de ${c.skus} SKUs`,
                     c && `${c.vendieron} de ${c.skus} SKUs`];
  // Primero los dos pasos que todavía nadie captura: en vez de "sin dato" dicen
  // a quién le toca. Así el rail pide lo que falta en lugar de sólo lamentarlo.
  const porCapturar: Paso[] = ETAPAS_POR_CAPTURAR.map(({ t, corto, accion, sub, porque }) => ({
    t, corto, v: accion, vc: "por capturar", sub, tono: "pendiente" as const,
    titulo: `${t}: ${porque}`,
  }));
  return porCapturar.concat(ETAPAS.map(({ t, corto, sub }, i) => {
    const inst = e.etapas[i];
    if (inst) {
      const conteo = cobertura[i];
      const salida = e.etapas[1];
      const antes = i === 2 && salida && Date.parse(inst.ts) < Date.parse(salida.ts);
      return { t, corto, v: fecha(inst), sub: conteo ?? sub, conteo, tono: "dato" as const,
               alerta: i === 2 && avisos && !!c?.rechazadas,
               titulo: `${t} · ${fecha(inst)} (${inst.dia ? "día" : "hora"} de CDMX)${conteo ? ` · ${conteo}` : ""}`
                 + (inst.aprox ? " — hora en que se OBSERVÓ, no la del evento" : "")
                 + (inst.dia ? " — se cuenta por día: no hay hora" : "")
                 + (i === 2 && avisos ? " — primer aviso de FULL de Mercado Libre con piezas de este envío" : "")
                 + (antes ? " — ML recibió ANTES de que bodega validara la salida en Odoo" : "") };
    }
    // Salida real todavía sin validar: el dato viene (ámbar), no es un hueco.
    if (i === 1 && abierta) {
      return { t, corto, v: "en espera", sub: "bodega no ha validado", tono: "espera" as const,
               titulo: `${t}: la salida existe en Odoo (${e.salida ?? ""}) y bodega aún no la valida.` };
    }
    // ML con avisos: el número de envío ya no hace falta para saber si llegó.
    if (i === 2 && avisos && c && !abierta) {
      if (c.cerrado) {
        return { t, corto, v: "no llegó", sub: `ML no recibió ${num(c.rechazadas ?? 0)} pzs`,
                 tono: "hueco" as const, alerta: true,
                 titulo: `${t}: diez días después de la salida no llegó ningún aviso de FULL de ML para este envío.` };
      }
      return { t, corto, v: "en espera", vc: "llegando",
               sub: c.cierre ? `cierra ${fecha({ ts: c.cierre, dia: true })}` : "sin avisos todavía",
               tono: "espera" as const,
               titulo: `${t}: todavía no llega ningún aviso de FULL de ML con piezas de este envío. `
                 + "Las tandas suelen empezar 1 a 3 días después de la salida." };
    }
    if (i === 2 && !abierta && SIN_LECTURA[e.estado]) {
      const s = SIN_LECTURA[e.estado]!;
      return { t, corto, v: "sin lectura", sub: s.sub, tono: "hueco" as const, titulo: `${t}: ${s.titulo}` };
    }
    return { t, corto, v: "sin dato", sub, tono: "hueco" as const,
             titulo: `${t}: no hay registro todavía. No es un cero.` };
  }));
}

const TONO_PASO = {
  dato: { caja: "border-emerald-200 bg-emerald-50", rotulo: "text-emerald-700", valor: "text-emerald-800" },
  espera: { caja: "border-amber-300 bg-amber-50", rotulo: "text-amber-700", valor: "text-amber-800" },
  hueco: { caja: "border-dashed border-slate-300", rotulo: "text-slate-400", valor: "text-slate-400" },
  // Rayado igual que un hueco —porque dato NO hay—, pero con el texto en índigo:
  // no es "no lo sabemos", es "falta que alguien lo haga".
  pendiente: { caja: "border-dashed border-indigo-300", rotulo: "text-indigo-500", valor: "text-indigo-600" },
};

export function Rail({ pasos }: { pasos: Paso[] }) {
  return (
    <div className="flex items-stretch gap-[3px]">
      {pasos.map((p) => {
        const tono = TONO_PASO[p.tono];
        return (
          <div key={p.t} title={p.titulo}
               style={p.tono === "hueco" ? { background: FONDO_RAYADO } : undefined}
               className={`min-w-0 flex-1 rounded-lg border px-[7px] py-1.5 ${tono.caja}`}>
            <div className={`truncate text-[9.5px] font-bold uppercase tracking-[.04em] ${tono.rotulo}`}>{p.corto}</div>
            <div className={`mt-0.5 truncate font-mono text-[10.5px] font-bold ${tono.valor}`}>{p.vc ?? p.v}</div>
          </div>
        );
      })}
    </div>
  );
}

/** La celda de llegada de un envío sin avisos de ML. «Sin registro» y «aún no sale» NO son 0%. */
export function tasaDe(e: Envio): { texto: string; nota: string; clase: string; rayada?: boolean } {
  const hueco = "border-dashed border-slate-300 text-slate-400";
  switch (e.estado) {
    case "sinEnlazar":
      return { texto: "no calculable", nota: "sin número de envío", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "abierta":
      return { texto: "aún no sale", nota: "salida sin validar", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "fbaSinLectura":
      return { texto: "sin registro", nota: "Amazon no deja leer (403)", clase: hueco, rayada: true };
    case "wfsSinLectura":
      return { texto: "sin registro", nota: "falta leer la API de WFS", clase: hueco, rayada: true };
    default:
      return { texto: "sin registro", nota: "sin avisos de FULL que leer", clase: hueco, rayada: true };
  }
}

// ── El rail C2: línea con nodos y el tiempo entre etapas ───────────────────
// Elegido por Brandon (17-sep) para el detalle: contesta «qué tramo alarga el
// envío» de un vistazo. La primera etapa con fecha dice la fecha; las demás,
// cuánto tardaron desde la anterior, con la fecha en chico debajo.

/** Número de día de calendario en CDMX (para restar días sin inventar horas). */
function diaCdmx(ts: string): number {
  const p = Object.fromEntries(FMT_CDMX.formatToParts(new Date(ts)).map((x) => [x.type, x.value]));
  return Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)) / 86_400_000;
}

/** «+7 d», «~+3 h», «mismo día». Un dato por DÍA sólo se resta en días. */
function tramo(desde: Instante, hasta: Instante): string {
  if (desde.dia || hasta.dia) {
    const d = diaCdmx(hasta.ts) - diaCdmx(desde.ts);
    return d === 0 ? "mismo día" : `${d > 0 ? "+" : "−"}${Math.abs(d)} d`;
  }
  const ms = Date.parse(hasta.ts) - Date.parse(desde.ts);
  const a = Math.abs(ms);
  const h = a / 3_600_000;
  // «+7 d 15 h», no «+8 d»: redondear días enteros escondería medio día.
  const horas = Math.round(h);
  const dias = Math.floor(horas / 24);
  const resto = horas - dias * 24;
  if (a < 60_000) return "mismo minuto";
  const cuanto = h < 1 ? `${Math.round(a / 60_000)} min`
    : h < 48 ? `${Math.round(h)} h` : `${dias} d${resto ? ` ${resto} h` : ""}`;
  // Una hora OBSERVADA hace aproximado el tramo entero.
  return `${desde.aprox || hasta.aprox ? "~" : ""}${ms < 0 ? "−" : "+"}${cuanto}`;
}

const NODO: Record<Paso["tono"], { punto: string; rotulo: string; valor: string }> = {
  dato: { punto: "bg-emerald-600 ring-1 ring-emerald-600 border-[3px] border-white",
          rotulo: "text-slate-700", valor: "text-slate-900" },
  espera: { punto: "bg-amber-500 ring-1 ring-amber-500 border-[3px] border-white",
            rotulo: "text-amber-700", valor: "text-amber-700" },
  hueco: { punto: "bg-white border-2 border-dashed border-slate-300", rotulo: "text-slate-400", valor: "text-slate-400" },
  pendiente: { punto: "bg-white border-2 border-dashed border-indigo-300", rotulo: "text-indigo-500", valor: "text-indigo-600" },
};

export function RailLinea({ envio }: { envio: Envio }) {
  const pasos = pasosDe(envio);
  // Las etapas que piden captura van primero y no tienen instante.
  const desfase = pasos.length - envio.etapas.length;
  const instantes = pasos.map((_, i) => (i >= desfase ? envio.etapas[i - desfase] ?? null : null));
  const n = pasos.length;
  const col = 100 / n;
  let previo: Instante | null = null;
  // La 1ª venta se mide desde la PRIMERA llegada: «Activo» es cuando quedó
  // activo el ÚLTIMO SKU, y otro SKU del mismo envío pudo venderse antes.
  const recibido = instantes[desfase + 2];

  return (
    <div className="relative">
      {/* la vía, de centro a centro */}
      <div className="absolute top-[8px] h-[3px] rounded-full bg-slate-100"
           style={{ left: `${col / 2}%`, right: `${col / 2}%` }} />
      {/* los tramos recorridos: sólo entre dos etapas con dato (o hacia la que viene) */}
      {pasos.slice(0, -1).map((p, i) => {
        const sig = pasos[i + 1].tono;
        if (p.tono !== "dato" || (sig !== "dato" && sig !== "espera")) return null;
        return (
          <div key={`tramo-${p.t}`}
               className={`absolute top-[8px] h-[3px] ${sig === "dato" ? "bg-emerald-600" : "bg-amber-400"}`}
               style={{ left: `${col * (i + 0.5)}%`, width: `${col}%` }} />
        );
      })}
      <div className="relative grid" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
        {pasos.map((p, i) => {
          const inst = instantes[i];
          const estilo = NODO[p.tono];
          let valor = p.v;
          let debajo = p.sub;
          if (p.tono === "dato" && inst) {
            const base = i === desfase + 4 && recibido ? recibido : previo;
            valor = base ? tramo(base, inst) : fecha(inst);
            // Debajo: la fecha (si arriba va el tramo) y cuántos SKUs llegaron ahí.
            debajo = [previo ? fecha(inst) : null, p.conteo].filter(Boolean).join(" · ") || p.sub;
            previo = inst;
          }
          return (
            <div key={p.t} className="flex flex-col items-center px-1 text-center" title={p.titulo}>
              <span className={`h-[18px] w-[18px] rounded-full ${estilo.punto}`} />
              <span className={`mt-2.5 text-[10px] font-bold uppercase tracking-[.05em] ${estilo.rotulo}`}>{p.t}</span>
              <span className={`mt-0.5 font-mono text-[12.5px] font-bold ${estilo.valor}`}>{valor}</span>
              <span className={`mt-0.5 text-[10.5px] leading-tight ${p.alerta ? "font-semibold text-rose-600" : "text-slate-400"}`}>
                {debajo}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
