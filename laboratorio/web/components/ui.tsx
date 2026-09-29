"use client";

/**
 * Piezas visuales del laboratorio. Copian el lenguaje del panel
 * (`frontend/components/fulfillment/ui.tsx`, `analisis/metricas/page.tsx`):
 * tarjetas blancas con borde slate-200, KPIs con rótulo de 10 px en mayúsculas,
 * índigo como acento y el RAYADO para «sin dato» (que no es un cero).
 */
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ImageOff, Loader2, Search, X } from "lucide-react";
import { CLASES_TONO } from "@/lib/vocabulario";
import type { Tono } from "@/lib/vocabulario";
import { RAYADO, puntoDe, NOMBRE_CUENTA } from "@/lib/tema";
import type { Canal } from "@/lib/tipos";

export function Tarjeta({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-slate-200 bg-white shadow-card ${className}`}>{children}</section>;
}

/** Rótulo chico en mono arriba de un título. */
export function Ceja({ children }: { children: ReactNode }) {
  return <p className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">{children}</p>;
}

export function Chip({ tono = "slate", children, titulo, className = "" }: { tono?: Tono; children: ReactNode; titulo?: string; className?: string }) {
  return (
    <span title={titulo}
          className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${CLASES_TONO[tono]} ${className}`}>
      {children}
    </span>
  );
}

/** «Sin dato» rayado: nunca un cero. */
export function SinDato({ texto = "sin dato", titulo }: { texto?: string; titulo?: string }) {
  return (
    <span title={titulo ?? "No hay dato. No es un cero."} style={RAYADO}
          className="inline-flex items-center whitespace-nowrap rounded px-1.5 py-[2px] font-mono text-[9.5px] font-bold uppercase tracking-[.05em] text-slate-500">
      {texto}
    </span>
  );
}

export function Kpi({ label, valor, pie, tono, activo, onClick, ayuda }: {
  label: string; valor: ReactNode; pie?: ReactNode; tono?: string; activo?: boolean; onClick?: () => void; ayuda?: string;
}) {
  const clase = `rounded-xl border px-4 py-3 text-left shadow-sm transition-colors ${
    activo ? "border-indigo-500 bg-indigo-50/70 ring-1 ring-indigo-500" : "border-slate-200 bg-white"
  } ${onClick ? "hover:border-indigo-300 cursor-pointer" : ""}`;
  const cuerpo = (
    <>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500" title={ayuda}>{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tono ?? "text-slate-900"}`}>{valor}</div>
      {pie && <div className="mt-0.5 text-[11px] text-slate-500">{pie}</div>}
    </>
  );
  return onClick
    ? <button type="button" onClick={onClick} className={clase} aria-pressed={activo}>{cuerpo}</button>
    : <div className={clase}>{cuerpo}</div>;
}

/**
 * Ayuda de encabezado (Brandon, 24-sep: "al pasar el cursor por el header me
 * indique brevemente a qué se refiere cada columna"). CSS puro, con teclado.
 */
export function Ayuda({ texto, children, lado = "centro" }: { texto: string; children: ReactNode; lado?: "centro" | "izq" | "der" }) {
  const pos = lado === "izq" ? "left-0" : lado === "der" ? "right-0" : "left-1/2 -translate-x-1/2";
  return (
    <span tabIndex={0} className="group/ayuda relative inline-flex cursor-help items-center gap-1 outline-none">
      <span className="border-b border-dotted border-slate-300 group-hover/ayuda:border-indigo-400">{children}</span>
      <span role="tooltip"
            className={`pointer-events-none absolute top-full z-30 mt-2 hidden w-60 max-w-[calc(100vw-2rem)] rounded-lg bg-indigo-950 px-3 py-2 text-left text-[11.5px] font-medium normal-case leading-snug tracking-normal text-indigo-50 shadow-xl ring-1 ring-indigo-400/30 group-hover/ayuda:block group-focus/ayuda:block ${pos}`}>
        {texto}
      </span>
    </span>
  );
}

/**
 * Botones de un solo valor. `n` = conteo de la faceta (cuántas filas verías al
 * elegirla con el resto de filtros vigentes); `null` = aún no se sabe.
 */
export function Segmentado<T extends string>({ opciones, valor, onCambio, oscuro, etiqueta }: {
  opciones: { id: T; label: ReactNode; punto?: string; n?: number | null }[]; valor: T; onCambio: (v: T) => void; oscuro?: boolean;
  /** Nombre del grupo para lectores de pantalla («Estado», «Cuenta»…). */
  etiqueta?: string;
}) {
  return (
    <div role="group" aria-label={etiqueta}
         className="sin-barra inline-flex max-w-full overflow-x-auto rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
      {opciones.map((o) => {
        const act = o.id === valor;
        const vacio = o.n === 0 && !act;
        return (
          <button key={o.id} type="button" onClick={() => onCambio(o.id)} aria-pressed={act}
                  className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
                    act ? (oscuro ? "bg-slate-900 text-white" : "bg-indigo-600 text-white")
                      : vacio ? "text-slate-500 hover:bg-slate-50" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`}>
            {o.punto && <span className="h-2 w-2 rounded-full" style={{ background: o.punto }} aria-hidden />}
            {o.label}
            {o.n !== undefined && o.n !== null && (
              <span className={`rounded-full px-1.5 py-[1px] text-[10px] font-semibold tabular-nums ${
                act ? "bg-white/20 text-white" : "bg-slate-100 text-slate-500"}`}>
                {o.n.toLocaleString("es-MX")}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function Selector({ valor, onCambio, opciones, etiqueta }: {
  valor: string; onCambio: (v: string) => void; opciones: { id: string; label: string; n?: number | null }[]; etiqueta: string;
}) {
  return (
    <label className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500 shadow-sm focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100">
      <span className="shrink-0 font-semibold">{etiqueta}</span>
      <select value={valor} onChange={(e) => onCambio(e.target.value)}
              className="min-w-0 max-w-[14rem] bg-transparent text-xs font-semibold text-slate-800 outline-none">
        {opciones.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}{o.n !== undefined && o.n !== null ? ` · ${o.n.toLocaleString("es-MX")}` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Franja de aviso dentro de una página (p. ej. «Propuesta. Ningún precio se aplica sin autorización.»). */
export function Franja({ icono, children, tono = "indigo" }: { icono?: ReactNode; children: ReactNode; tono?: "indigo" | "amber" | "rose" | "emerald" }) {
  const clase = {
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    rose: "border-rose-200 bg-rose-50 text-rose-800",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
  }[tono];
  return (
    <div role="note" className={`flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-snug ${clase}`}>
      {icono && <span className="mt-[1px] shrink-0" aria-hidden>{icono}</span>}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/**
 * Diálogo modal centrado (confirmaciones). Portal a <body>, Esc y clic en el
 * velo cierran, el foco entra al primer control.
 */
export function Dialogo({ abierto, onCerrar, titulo, children, acciones }: {
  abierto: boolean; onCerrar: () => void; titulo: ReactNode; children: ReactNode; acciones: ReactNode;
}) {
  const cerrar = useRef(onCerrar);
  cerrar.current = onCerrar;
  const caja = useRef<HTMLDivElement>(null);
  const [montado, setMontado] = useState(false);
  useEffect(() => setMontado(true), []);
  useEffect(() => {
    if (!abierto) return;
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar.current(); };
    window.addEventListener("keydown", tecla);
    const t = setTimeout(() => caja.current?.querySelector<HTMLElement>("input, button")?.focus(), 30);
    return () => { window.removeEventListener("keydown", tecla); clearTimeout(t); };
  }, [abierto]);
  if (!montado || !abierto) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-[1px]" onClick={() => cerrar.current()} />
      <div ref={caja} role="dialog" aria-modal="true" aria-labelledby="dialogo-titulo"
           className="relative w-full max-w-md animate-fade-in rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl">
        <h2 id="dialogo-titulo" className="text-[15px] font-bold text-slate-900">{titulo}</h2>
        <div className="mt-2 text-[13px] leading-relaxed text-slate-600">{children}</div>
        <div className="mt-5 flex flex-wrap justify-end gap-2">{acciones}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Buscador({ valor, onCambio, placeholder = "Buscar SKU o título" }: { valor: string; onCambio: (v: string) => void; placeholder?: string }) {
  return (
    <label className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 shadow-sm focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100 sm:max-w-xs">
      <Search size={14} className="shrink-0 text-slate-500" />
      <input value={valor} onChange={(e) => onCambio(e.target.value)} placeholder={placeholder} aria-label={placeholder} type="search"
             className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-500" />
      {valor && (
        <button type="button" onClick={() => onCambio("")} className="text-slate-500 hover:text-slate-700" aria-label="Limpiar búsqueda">
          <X size={14} />
        </button>
      )}
    </label>
  );
}

export function useRetrasado<T>(v: T, ms = 300): T {
  const [x, setX] = useState(v);
  useEffect(() => { const t = setTimeout(() => setX(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return x;
}

export function Paginacion({ page, total, perPage, onPage, cargando }: {
  page: number; total: number; perPage: number; onPage: (p: number) => void; cargando?: boolean;
}) {
  const paginas = Math.max(1, Math.ceil(total / Math.max(1, perPage)));
  const desde = total === 0 ? 0 : (page - 1) * perPage + 1;
  const hasta = Math.min(total, page * perPage);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
      <span className="tabular-nums">
        {desde.toLocaleString("es-MX")}–{hasta.toLocaleString("es-MX")} de <b className="text-slate-700">{total.toLocaleString("es-MX")}</b>
        {cargando && <Loader2 size={12} className="ml-2 inline animate-spin text-indigo-500" />}
      </span>
      <div className="flex items-center gap-1">
        <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}
                className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40" aria-label="Página anterior">
          <ChevronLeft size={14} />
        </button>
        <span className="px-2 tabular-nums">{page} / {paginas}</span>
        <button type="button" disabled={page >= paginas} onClick={() => onPage(page + 1)}
                className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-600 hover:bg-slate-50 disabled:opacity-40" aria-label="Página siguiente">
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

/** Encabezado de columna ordenable. `orden` va como "campo" o "-campo". */
export function Th({ children, campo, orden, onOrden, ayuda, alinear = "izq", className = "", lado }: {
  children: ReactNode; campo?: string; orden?: string; onOrden?: (o: string) => void; ayuda?: string;
  alinear?: "izq" | "der" | "centro"; className?: string; lado?: "centro" | "izq" | "der";
}) {
  const activo = campo && orden && (orden === campo || orden === `-${campo}`);
  const desc = activo && orden!.startsWith("-");
  const contenido = ayuda ? <Ayuda texto={ayuda} lado={lado ?? (alinear === "der" ? "der" : "izq")}>{children}</Ayuda> : children;
  const al = alinear === "der" ? "text-right" : alinear === "centro" ? "text-center" : "text-left";
  return (
    <th scope="col" className={`whitespace-nowrap px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 ${al} ${className}`}>
      {campo && onOrden ? (
        <span className={`inline-flex items-center gap-1 ${alinear === "der" ? "flex-row-reverse" : ""}`}>
          {contenido}
          <button type="button" onClick={() => onOrden(activo && desc ? campo : `-${campo}`)}
                  className={`rounded p-0.5 ${activo ? "text-indigo-600" : "text-slate-300 hover:text-slate-500"}`}
                  aria-label={`Ordenar por ${typeof children === "string" ? children : campo}`}>
            {activo && !desc ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
          </button>
        </span>
      ) : contenido}
    </th>
  );
}

export function PuntoCuenta({ canal, cuenta, conNombre = true }: { canal: Canal; cuenta: string; conNombre?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-slate-600">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: puntoDe(canal, cuenta) }} />
      {conNombre && (NOMBRE_CUENTA[cuenta] ?? cuenta)}
    </span>
  );
}

export function Miniatura({ src, alt, tam = 36 }: { src?: string | null; alt: string; tam?: number }) {
  const [roto, setRoto] = useState(false);
  if (!src || roto) {
    return (
      <span className="flex shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-slate-300"
            style={{ width: tam, height: tam }} aria-hidden>
        <ImageOff size={Math.round(tam * 0.42)} />
      </span>
    );
  }
  // ML todavía devuelve miniaturas en http://; la CSP de api.py sólo deja imágenes https.
  const seguro = src.replace(/^http:\/\//, "https://");
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={seguro} alt={alt} width={tam} height={tam} loading="lazy" onError={() => setRoto(true)}
              className="shrink-0 rounded-lg border border-slate-200 bg-white object-contain" style={{ width: tam, height: tam }} />;
}

export function Cargando({ texto = "Cargando…" }: { texto?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Loader2 size={16} className="animate-spin text-indigo-500" /> {texto}
    </div>
  );
}

export function CajaError({ mensaje, onReintentar }: { mensaje: string; onReintentar?: () => void }) {
  return (
    <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
      {mensaje}
      {onReintentar && (
        <button type="button" onClick={onReintentar} className="ml-3 font-semibold underline underline-offset-2">Reintentar</button>
      )}
    </div>
  );
}

export function Vacio({ texto }: { texto: string }) {
  return <div className="px-4 py-12 text-center text-sm text-slate-500">{texto}</div>;
}

/**
 * Cajón lateral (el `aside` del panel: max-w-xl, slide-in, fondo slate-50). En
 * un portal a <body>, se cierra con Esc, con la ✕ o con clic en el velo, y la
 * página de atrás no se desplaza mientras está abierto.
 */
export function Cajon({ abierto, onCerrar, titulo, subtitulo, ancho = "max-w-2xl", children }: {
  abierto: boolean; onCerrar: () => void; titulo: ReactNode; subtitulo?: ReactNode; ancho?: string; children: ReactNode;
}) {
  const cerrar = useRef(onCerrar);
  cerrar.current = onCerrar;
  const [montado, setMontado] = useState(false);
  useEffect(() => setMontado(true), []);
  useEffect(() => {
    if (!abierto) return;
    const antes = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar.current(); };
    window.addEventListener("keydown", tecla);
    return () => { document.body.style.overflow = antes; window.removeEventListener("keydown", tecla); };
  }, [abierto]);
  if (!montado || !abierto) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/30 backdrop-blur-[1px]" onClick={() => cerrar.current()} />
      <aside role="dialog" aria-modal="true"
             className={`relative flex h-full w-full min-w-0 ${ancho} animate-slide-in flex-col bg-slate-50 shadow-2xl`}>
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4">
          <div className="min-w-0">
            <div className="text-[15px] font-bold leading-snug text-slate-900">{titulo}</div>
            {subtitulo && <div className="mt-1 text-xs text-slate-500">{subtitulo}</div>}
          </div>
          <button type="button" onClick={() => cerrar.current()} title="Cerrar (Esc)" aria-label="Cerrar"
                  className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-700">
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

/** Marco de tabla: título, scroll horizontal PROPIO (la página nunca se desborda). */
export function MarcoTabla({ titulo, derecha, children, pie }: { titulo?: ReactNode; derecha?: ReactNode; children: ReactNode; pie?: ReactNode }) {
  return (
    <Tarjeta className="overflow-hidden">
      {(titulo || derecha) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <div className="text-xs font-semibold text-slate-700">{titulo}</div>
          {derecha}
        </div>
      )}
      <div className="overflow-x-auto">{children}</div>
      {pie}
    </Tarjeta>
  );
}

/** Una fila «etiqueta — valor» para los cajones. */
export function Renglon({ etiqueta, valor, fuerte, tono, ayuda }: { etiqueta: ReactNode; valor: ReactNode; fuerte?: boolean; tono?: string; ayuda?: string }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 py-1.5 text-sm ${fuerte ? "border-t border-slate-200 pt-2 font-semibold" : ""}`}>
      <span className="text-slate-500" title={ayuda}>{etiqueta}</span>
      <span className={`tabular-nums ${tono ?? "text-slate-800"}`}>{valor}</span>
    </div>
  );
}
