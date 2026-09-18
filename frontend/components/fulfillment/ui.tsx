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
import { ETAPAS, ETAPAS_POR_CAPTURAR } from "./tipos";

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
  // Un dato por DÍA no lleva hora: inventarle "18:00" sería mentir.
  if (i.dia) return `${partes.day} ${MESES[Number(partes.month) - 1]}`;
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

/**
 * De dónde sale la cifra de esa tarjeta. Mientras la pestaña se construye por
 * etapas conviven datos en vivo y datos del mockup: cada tarjeta dice cuál es.
 */
export function ChipFuente({ vivo, titulo }: { vivo: boolean; titulo?: string }) {
  return vivo ? (
    <span title={titulo ?? "Se lee de Odoo en cada carga (caché de 2 min)."}
          className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-[.05em] text-emerald-700">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Odoo en vivo
    </span>
  ) : (
    <span title={titulo ?? "Cifra del mockup de diseño (14-sep-2026): todavía no se lee de ningún sistema."}
          className="inline-flex items-center rounded-full border border-dashed border-slate-300 bg-white px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-[.05em] text-slate-400">
      diseño
    </span>
  );
}

/** «13 ene» en hora de CDMX. */
export function dia(iso: string | null | undefined): string {
  if (!iso) return "—";
  const partes = Object.fromEntries(FMT_CDMX.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${Number(partes.day)} ${MESES[Number(partes.month) - 1]}`;
}

export const DIAS_SEMANA = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"] as const;
export const DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"] as const;

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
  salio: { sub: "sin movimiento aún",
           titulo: "Ningún SKU de este envío subió su stock en FULL después de la salida. Mercado Libre no tiene API de envíos a Full: esto es lo que ve el sync cada 15 min." },
  sinEnlazar: { sub: "sin número, no se cruza",
                titulo: "Sin número de envío no hay con qué cruzar lo que salió contra lo que llegó." },
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
    // «Recibido» en curso es ÁMBAR: el dato viene, no ha llegado. No se resta
    // enviadas − recibidas para inventar un rechazo.
    if (i === 2 && (e.estado === "recepcion" || e.estado === "amazonSinLectura")) {
      const amz = e.estado === "amazonSinLectura";
      const v = amz ? "sin lectura" : "en recepción";
      return { t, corto, v, sub: amz ? "Amazon no se consulta" : "el almacén no ha contado", tono: "espera" as const,
               titulo: `${t} · ${v} — no se resta para inventar un rechazo` };
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
              {grande ? p.t : p.corto}
            </div>
            <div className={`mt-0.5 truncate font-mono font-bold ${tono.valor} ${grande ? "text-[13px]" : "text-[10.5px]"}`}>
              {grande ? p.v : p.vc ?? p.v}
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
  const hueco = "border-dashed border-slate-300 text-slate-400";
  switch (e.estado) {
    case "sinEnlazar":
      return { texto: "no calculable", nota: "sin número de envío", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "abierta":
      return { texto: "aún no sale", nota: "salida sin validar", clase: "border-amber-300 bg-amber-50 text-amber-700" };
    case "salio":
      return { texto: "sin registro", nota: "sin avisos de FULL que leer", clase: hueco, rayada: true };
    case "fbaSinLectura":
      return { texto: "sin registro", nota: "Amazon no deja leer (403)", clase: hueco, rayada: true };
    case "wfsSinLectura":
      return { texto: "sin registro", nota: "falta leer la API de WFS", clase: hueco, rayada: true };
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
