"use client";

/**
 * Piezas compartidas de Inventario → ÓRDENES DE VENTA: los rótulos y tonos de
 * cada estado, los formatos (dinero, fecha en hora de CDMX), y los dos o tres
 * controles que usan la lista, el documento y el chat.
 *
 * El vocabulario visual es el de las pestañas hermanas de Inventario
 * (rounded-2xl + border-slate-200, índigo para la acción) y la semántica de
 * color la del resto del panel: esmeralda = hecho, índigo = en curso, ámbar =
 * pide atención, rosa = cancelado/error, slate = inerte.
 */

import { useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Loader2 } from "lucide-react";
import { BotonCerrar, Ventana } from "@/components/fulfillment/ui";
import type { Bodega, EstadoModulo, EstadoOrden, Existencia, OrdenResumen, Reserva, Via } from "./tipos";

// ── Estados ───────────────────────────────────────────────────────────────────

/**
 * Como los pidió Brandon: en mayúsculas, y los de entrega en inglés. Es lo que
 * va DENTRO del chip (y en la traza): una palabra, nunca una frase. La frase
 * que lo explica es `AYUDA_ESTADO`, y va al `title`.
 */
export const ROTULO_ESTADO: Record<EstadoOrden, string> = {
  borrador: "BORRADOR",
  confirmada: "CONFIRMADA",
  entregada: "DELIVERED",
  cancelada: "CANCELADO",
  entregada_cancelada: "DELIVERED but CANCELLED",
};

export const AYUDA_ESTADO: Record<EstadoOrden, string> = {
  borrador: "Todavía se puede editar. No aparta stock.",
  confirmada: "Confirmada: el stock de cada renglón quedó apartado en su bodega. Ya no se modifica.",
  entregada: "Almacén ya la entregó a la paquetería.",
  cancelada: "Cancelada antes de salir del almacén. El apartado se soltó.",
  entregada_cancelada: "Se canceló DESPUÉS de entregarse a la paquetería: el producto tiene que regresar.",
};

const TONO_ESTADO: Record<EstadoOrden, string> = {
  borrador: "bg-slate-100 text-slate-600 ring-slate-200",
  confirmada: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  entregada: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  cancelada: "bg-rose-50 text-rose-700 ring-rose-200",
  entregada_cancelada: "bg-amber-50 text-amber-800 ring-amber-300",
};

/** Los colores que usa la traza (canvas) para cada estado. Hex: el worker no lee Tailwind. */
export const COLOR_ESTADO: Record<EstadoOrden, string> = {
  borrador: "#94a3b8",
  confirmada: "#4f46e5",
  entregada: "#059669",
  cancelada: "#e11d48",
  entregada_cancelada: "#d97706",
};

export function ChipEstado({ estado, chico = false }: { estado: EstadoOrden; chico?: boolean }) {
  return (
    <span title={AYUDA_ESTADO[estado]}
          className={`inline-flex items-center whitespace-nowrap rounded-full font-bold ring-1 ${TONO_ESTADO[estado]} ${
            chico ? "px-2 py-0.5 text-[10.5px]" : "px-2.5 py-1 text-[11.5px]"}`}>
      {ROTULO_ESTADO[estado]}
    </span>
  );
}

// ── Reserva (se DERIVA de los renglones; no es una columna) ───────────────────

export function reservaDe(o: { estado: EstadoOrden; renglones_entregados?: number;
                               borrada_at?: string | null }): Reserva {
  if (o.estado === "borrador") return "sin_apartar";
  if (o.borrada_at || o.estado === "cancelada") return "liberada";
  if (o.estado === "entregada" || o.estado === "entregada_cancelada") return "surtida";
  return (o.renglones_entregados ?? 0) > 0 ? "entrega_parcial" : "apartada";
}

export const ROTULO_RESERVA: Record<Reserva, string> = {
  sin_apartar: "Sin apartar",
  apartada: "Stock apartado",
  entrega_parcial: "Entrega parcial",
  surtida: "Surtida",
  liberada: "Apartado liberado",
};

const TONO_RESERVA: Record<Reserva, string> = {
  sin_apartar: "bg-slate-50 text-slate-500 ring-slate-200",
  apartada: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  entrega_parcial: "bg-amber-50 text-amber-800 ring-amber-200",
  surtida: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  liberada: "bg-slate-50 text-slate-500 ring-slate-200",
};

export function ChipReserva({ reserva, detalle }: { reserva: Reserva; detalle?: string }) {
  return (
    <span title={detalle}
          className={`inline-flex items-center whitespace-nowrap rounded px-2 py-0.5 text-[11px] font-bold ring-1 ${TONO_RESERVA[reserva]}`}>
      {ROTULO_RESERVA[reserva]}
    </span>
  );
}

// ── Bodegas (la orden sólo vive en las de kubera, y la bodega va POR RENGLÓN) ──

/**
 * Las bodegas que se pueden elegir en un renglón: de kubera y con `admite_ov`.
 * El catálogo trae también las de Odoo (TEXCO, TEX2, DROP) y las de kubera que
 * no llevan órdenes (REVISION, o TEX3 mientras esté apagada): ésas no se
 * ofrecen, porque la base las rechaza al confirmar.
 */
export function bodegasDeOrdenes(bodegas: Bodega[] | null | undefined): Bodega[] {
  return (bodegas ?? []).filter((b) => b.fuente === "kubera" && b.admite_ov);
}

/** «ENSAYO · Bodega de ensayo». Si el catálogo no la conoce (o el nombre no dice más), el código solo. */
export function rotuloBodega(codigo: string | null | undefined, bodegas: Bodega[] | null | undefined): string {
  if (!codigo) return "—";
  const nombre = (bodegas ?? []).find((b) => b.codigo === codigo)?.nombre?.trim();
  return nombre && nombre.toUpperCase() !== codigo.toUpperCase() ? `${codigo} · ${nombre}` : codigo;
}

/**
 * De qué bodega conviene sacar un SKU: la ELEGIBLE con más piezas libres. Si
 * ninguna tiene libre (o el SKU no tiene saldo en ninguna), no se adivina:
 * queda la única que hay, o vacío para que la persona elija. Los empates los
 * gana el orden del catálogo.
 */
export function bodegaSugerida(existencias: Existencia[] | null | undefined, elegibles: Bodega[]): string {
  let mejor = "";
  let libre = 0;
  for (const b of elegibles) {
    const e = (existencias ?? []).find((x) => x.almacen === b.codigo);
    if (e && e.libre > libre) { mejor = b.codigo; libre = e.libre; }
  }
  if (mejor) return mejor;
  return elegibles.length === 1 ? elegibles[0].codigo : "";
}

// ── Canales y quién ───────────────────────────────────────────────────────────

/** `id` es el valor de `channel.orders.canal`; `cuentas`, las de ese canal. */
export const CANALES: { id: string; rotulo: string; cuentas: string[]; mp: boolean }[] = [
  { id: "temu", rotulo: "Temu", cuentas: ["TEMU"], mp: true },
  { id: "tiktok", rotulo: "TikTok", cuentas: ["TIKTOK"], mp: true },
  { id: "mercado_libre", rotulo: "Mercado Libre", cuentas: ["BEKURA", "SANCORFASHION"], mp: true },
  { id: "amazon", rotulo: "Amazon", cuentas: ["AMAZON"], mp: true },
  { id: "walmart", rotulo: "Walmart", cuentas: ["WALMART"], mp: true },
  { id: "shein", rotulo: "Shein", cuentas: ["SHEIN"], mp: true },
  { id: "directa", rotulo: "Venta directa", cuentas: [], mp: false },
  { id: "otro", rotulo: "Otro", cuentas: [], mp: false },
];

export function rotuloCanal(id: string | null | undefined): string {
  if (!id) return "—";
  return CANALES.find((c) => c.id === id)?.rotulo ?? id;
}

/** Las dos cuentas de Mercado Libre se nombran como las conoce el equipo. */
export function rotuloCuenta(cuenta: string | null | undefined): string {
  if (cuenta === "BEKURA") return "Kubera";
  if (cuenta === "SANCORFASHION") return "San Corpe";
  return cuenta || "";
}


export const ROTULO_VIA: Record<Via, string> = {
  panel: "desde el panel",
  api: "por la API",
  claude: "por Claude",
  automatico: "automático",
};

/** El nombre a mostrar: el de core.usuarios, o lo de antes de la arroba. */
export function quien(nombre: string | null | undefined, correo: string | null | undefined): string {
  if (nombre && nombre.trim()) return nombre.trim();
  if (!correo) return "—";
  if (correo === "automatico") return "Automático";
  if (correo === "servicio") return "API";
  return correo.split("@")[0];
}

// ── Formatos ──────────────────────────────────────────────────────────────────

export function dinero(n: number | null | undefined, moneda = "MXN", vacio = "—"): string {
  if (n === null || n === undefined || Number.isNaN(n)) return vacio;
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: moneda || "MXN",
                                          minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

export const num = (n: number | null | undefined, vacio = "—"): string =>
  n === null || n === undefined ? vacio : n.toLocaleString("es-MX");

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const FMT_CDMX = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Mexico_City", year: "numeric", month: "numeric", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

function partes(iso: string): Record<string, string> | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return Object.fromEntries(FMT_CDMX.formatToParts(d).map((p) => [p.type, p.value]));
}

/** «02 oct 12:09» en hora de CDMX. */
export function fechaHora(iso: string | null | undefined, vacio = "—"): string {
  if (!iso) return vacio;
  const p = partes(iso);
  if (!p) return vacio;
  return `${p.day} ${MESES[Number(p.month) - 1]} ${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
}

/** «02 oct 2026» en hora de CDMX. */
export function fechaLarga(iso: string | null | undefined, vacio = "—"): string {
  if (!iso) return vacio;
  const p = partes(iso);
  if (!p) return vacio;
  return `${p.day} ${MESES[Number(p.month) - 1]} ${p.year}`;
}

/** «hace 5 min», «hace 3 h», «hace 2 d». Se llama dentro de un efecto o tras montar. */
export function haceCuanto(iso: string | null | undefined, ahora: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((ahora - t) / 1000));
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}

/** ISO → valor de un `<input type="datetime-local">` en hora de CDMX, y de vuelta. */
export function aInputLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const p = partes(iso);
  if (!p) return "";
  const mm = String(p.month).padStart(2, "0");
  return `${p.year}-${mm}-${p.day}T${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
}

/** CDMX no tiene horario de verano desde 2022: UTC−6 fijo. */
export function deInputLocal(valor: string): string | null {
  if (!valor) return null;
  const d = new Date(`${valor}:00-06:00`);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function pesoArchivo(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ── Claves de idempotencia (el alta de la orden y cada mensaje del chat) ──────

/** uuid v4. `crypto.randomUUID` no existe fuera de https/localhost: va con respaldo. */
export function nuevaClave(): string {
  const c: Partial<Crypto> | undefined = typeof crypto === "undefined" ? undefined : crypto;
  if (c?.randomUUID) return c.randomUUID();
  const b = new Uint8Array(16);
  if (c?.getRandomValues) c.getRandomValues(b);
  else for (let i = 0; i < 16; i += 1) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** La clave de un envío, atada a la HUELLA de lo que se mandó con ella. */
export interface ClaveIntento {
  huella: string;
  clave: string;
}

/**
 * Qué clave lleva ESTE intento. La clave sirve para que el reintento de lo
 * MISMO (se perdió la respuesta, doble clic) no cree dos veces; por eso sólo se
 * reusa si lo que se manda es idéntico a lo del intento anterior. Si la
 * persona corrigió algo tras el fallo, es otro envío y lleva clave nueva: con
 * la vieja el servidor contestaría lo del primer intento y la corrección se
 * perdería en silencio.
 */
export function claveDeIntento(previa: ClaveIntento | null, huella: string,
                               generar: () => string = nuevaClave): ClaveIntento {
  return previa && previa.huella === huella ? previa : { huella, clave: generar() };
}

// ── Controles ─────────────────────────────────────────────────────────────────

export const CLASE_CAMPO =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none " +
  "placeholder:text-slate-400 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100 " +
  "disabled:cursor-not-allowed disabled:border-transparent disabled:bg-slate-50 disabled:text-slate-600";

export const CLASE_ROTULO = "text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400";

type Tono = "primario" | "secundario" | "peligro" | "exito" | "fantasma";

const TONO_BOTON: Record<Tono, string> = {
  primario: "border-indigo-600 bg-indigo-600 text-white hover:bg-indigo-700",
  secundario: "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
  peligro: "border-rose-200 bg-white text-rose-600 hover:bg-rose-50",
  exito: "border-emerald-600 bg-emerald-600 text-white hover:bg-emerald-700",
  fantasma: "border-transparent bg-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-800",
};

/**
 * El botón del módulo. `porque` explica por qué está apagado (va al `title`):
 * un botón gris sin motivo es un botón roto para quien lo ve.
 */
export function Boton({
  children, icono: Icono, onClick, tono = "secundario", deshabilitado, porque, ocupado, chico, tipo = "button",
}: {
  children?: ReactNode;
  icono?: LucideIcon;
  onClick?: () => void;
  tono?: Tono;
  deshabilitado?: boolean;
  porque?: string;
  ocupado?: boolean;
  chico?: boolean;
  tipo?: "button" | "submit";
}) {
  const apagado = !!deshabilitado || !!ocupado;
  return (
    <button type={tipo} onClick={onClick} disabled={apagado} title={deshabilitado ? porque : undefined}
            className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
              chico ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm"} ${TONO_BOTON[tono]}`}>
      {ocupado ? <Loader2 className="h-4 w-4 animate-spin" /> : Icono ? <Icono className="h-4 w-4" /> : null}
      {children}
    </button>
  );
}

/** Rótulo + control, uno arriba del otro. */
export function Campo({ rotulo, children, ayuda, ancho = "" }: {
  rotulo: string; children: ReactNode; ayuda?: string; ancho?: string;
}) {
  return (
    <label className={`block ${ancho}`}>
      <span className={CLASE_ROTULO}>{rotulo}</span>
      <div className="mt-1">{children}</div>
      {ayuda ? <p className="mt-1 text-[11.5px] leading-snug text-slate-400">{ayuda}</p> : null}
    </label>
  );
}

export type TonoAviso = "info" | "ambar" | "error" | "ok";

const TONO_AVISO: Record<TonoAviso, string> = {
  info: "bg-indigo-50 text-indigo-800 ring-indigo-200",
  ambar: "bg-amber-50 text-amber-900 ring-amber-200",
  error: "bg-rose-50 text-rose-700 ring-rose-200",
  ok: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export function Aviso({ tono = "info", icono: Icono, children }: {
  tono?: TonoAviso; icono?: LucideIcon; children: ReactNode;
}) {
  return (
    <div className={`flex items-start gap-2 rounded-xl p-3 text-sm ring-1 ${TONO_AVISO[tono]}`}>
      {Icono ? <Icono className="mt-0.5 h-4 w-4 shrink-0" /> : null}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** Lo mínimo que se pide de motivo cuando nadie dice otra cosa. */
export const MOTIVO_MINIMO = 3;

/** ¿Cuántos caracteres le faltan al motivo? 0 = ya se puede mandar. Cuenta sin los espacios de las orillas. */
export function faltaDeMotivo(motivo: string, minimo: number = MOTIVO_MINIMO): number {
  return Math.max(0, minimo - motivo.trim().length);
}

// ── Lo que decide la lista ───────────────────────────────────────────────────
// Vive aquí y no en la página porque un `page.tsx` de Next sólo puede exportar
// su componente: así las pruebas lo ejercitan sin montar nada.

/**
 * ¿El canal canceló con el paquete en camino y NADIE ha contestado si salió?
 * La marca del canal se queda puesta después (cancelada, DELIVERED but
 * CANCELLED), pero la pregunta sólo está abierta en una confirmada viva.
 */
export function esperaSalio(o: Pick<OrdenResumen, "estado" | "borrada_at" | "canal_cancelo_at">): boolean {
  return !o.borrada_at && o.estado === "confirmada" && !!o.canal_cancelo_at;
}

/**
 * `ok: false` sin ser por las migraciones = el backend contestó, pero SIN poder
 * leer kubera (una pausa, un reinicio): sus banderas vienen apagadas y sus
 * bodegas vacías porque no se leyeron. No es un estado: es «todavía no sé».
 */
export function moduloSinLeer(e: Pick<EstadoModulo, "ok" | "falta_migracion"> | null): boolean {
  return !!e && e.ok === false && !e.falta_migracion;
}

/**
 * Por qué NO se puede pedir «Revisar cancelaciones» (`null` = sí se puede). El
 * barrido sólo corre con la bandera encendida: en modo prueba el backend
 * contesta `ok: false` sin revisar nada, y un botón encendido haría creer que
 * revisó y no había cancelaciones.
 */
export function porqueNoRevisar(e: Pick<EstadoModulo, "ok" | "falta_migracion" | "habilitado"> | null): string | null {
  if (!e) return null;   // sin estado ya lo dice quien llama («leyendo…», «no se pudo leer»)
  if (moduloSinLeer(e)) return "No se pudo leer el estado del módulo: no se sabe si el barrido está encendido.";
  if (!e.habilitado) return "Modo prueba: el barrido de cancelaciones sólo corre con la bandera «ordenes_venta» encendida.";
  return null;
}

/**
 * La confirmación de una acción que no tiene vuelta fácil (cancelar, borrar):
 * dice qué va a pasar y pide el MOTIVO, que queda escrito en la bitácora de la
 * orden junto con quién lo hizo.
 *
 * `minimo` es el largo que EXIGE LA BASE para ese motivo (5 al cancelar una
 * confirmada, 10 al borrar: `ov_ordenes_canc_m_chk`, `ov_ordenes_borrada_m_chk`).
 * Se dice junto al campo y el botón no se enciende antes: mandar de menos sólo
 * serviría para que el servidor lo rechace después de hacer esperar.
 */
export function DialogoMotivo({
  titulo, texto, accion, tono = "peligro", requerido = true, minimo = MOTIVO_MINIMO, ocupado,
  onConfirmar, onCerrar,
}: {
  titulo: string;
  texto: ReactNode;
  /** El verbo del botón: «Cancelar la orden», «Borrar». */
  accion: string;
  tono?: "peligro" | "primario";
  requerido?: boolean;
  /** Caracteres mínimos del motivo (sólo cuenta si es `requerido`). */
  minimo?: number;
  ocupado?: boolean;
  onConfirmar: (motivo: string) => void;
  onCerrar: () => void;
}) {
  const [motivo, setMotivo] = useState("");
  const faltan = requerido ? faltaDeMotivo(motivo, minimo) : 0;
  const exigente = requerido && minimo > MOTIVO_MINIMO;
  return (
    <Ventana onCerrar={onCerrar} etiqueta={titulo} ancho="max-w-lg">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <h2 className="text-base font-extrabold tracking-tight text-slate-900">{titulo}</h2>
        <BotonCerrar onClick={onCerrar} />
      </div>
      <div className="space-y-3 px-5 py-4">
        <div className="text-sm leading-relaxed text-slate-600">{texto}</div>
        <Campo rotulo={requerido ? "Motivo (queda en la bitácora)" : "Motivo (opcional)"}>
          {/* `autoFocus` y no un efecto con ref: `Ventana` pinta a sus hijos un
              render DESPUÉS de montarse, y un efecto de este componente corre
              cuando la caja todavía no existe (el foco se quedaba detrás). */}
          <textarea autoFocus value={motivo} onChange={(e) => setMotivo(e.target.value)} rows={3}
                    maxLength={500} className={CLASE_CAMPO}
                    placeholder="Por qué se hace esto…" />
        </Campo>
        {exigente && (
          <p className={`text-[11.5px] leading-snug ${faltan > 0 ? "text-slate-500" : "text-emerald-700"}`}
             aria-live="polite">
            El motivo lleva al menos {minimo} caracteres.{" "}
            {faltan > 0 ? (faltan === 1 ? "Falta 1." : `Faltan ${faltan}.`) : "Listo."}
          </p>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
        <Boton onClick={onCerrar} tono="fantasma">No, dejarla como está</Boton>
        <Boton onClick={() => onConfirmar(motivo.trim())} tono={tono} deshabilitado={faltan > 0}
               porque={exigente ? `Escribe el motivo: al menos ${minimo} caracteres` : "Escribe el motivo"}
               ocupado={ocupado}>
          {accion}
        </Boton>
      </div>
    </Ventana>
  );
}
