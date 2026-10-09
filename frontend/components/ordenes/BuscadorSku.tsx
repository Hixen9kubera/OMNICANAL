"use client";

/**
 * El buscador de productos del documento de la orden: se escribe un SKU o un
 * nombre y sale la lista del catálogo con las EXISTENCIAS de cada uno POR
 * BODEGA, para agregar el renglón sin salir del teclado (flechas, Enter, Esc).
 *
 * Por qué las existencias van por bodega y no en un solo número: la orden sólo
 * vive en bodegas de kubera y cada renglón sale de UNA. «Hay 12» no dice nada
 * si 10 están en una bodega que no lleva órdenes; lo que sirve para capturar
 * es cuánto hay LIBRE en cada una. Las que no admiten órdenes se pintan
 * apagadas: se ven (el producto existe), pero de ahí no se puede apartar.
 *
 * Por qué deja agregar un SKU que el catálogo no conoce: una venta de
 * marketplace puede traer un SKU que `core.products` todavía no tiene, y la
 * orden tiene que poder existir para que alguien lo vea (por eso
 * `ventas.ov_lineas.sku` no lleva llave foránea). Ese renglón entra MARCADO
 * —segundo argumento de `onElegir`— y, como no tiene saldo en ninguna bodega,
 * la orden no se podrá confirmar hasta que lo tenga: falla cerrado, pero a la
 * vista.
 *
 * Tres trampas resueltas aquí:
 *   · Enter antes de que conteste la búsqueda (quien teclea rápido o usa un
 *     lector de códigos) NO agrega «fuera del catálogo» a ciegas: espera la
 *     respuesta de ESE texto y decide con ella.
 *   · Enter con opciones a la vista elige la opción RESALTADA —la primera, si
 *     nadie movió las flechas, y por eso se pinta resaltada desde que llega—.
 *     Antes agregaba el TEXTO tecleado: quien escribía «funda», veía cinco
 *     productos y daba Enter se llevaba un renglón «funda» fuera del catálogo,
 *     que nunca iba a poder apartarse. El texto «tal cual» sólo entra desde su
 *     fila explícita, o cuando la búsqueda no encontró nada.
 *   · Sin existencias registradas NO es cero: se pinta «sin existencias», no
 *     «libre 0». Un SKU sin fila de saldo es «no se sabe», y se dice así.
 */

import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Loader2, Plus, Search } from "lucide-react";
import { mensajeDeError } from "@/lib/api";
import { buscarSkus } from "./api";
import { num } from "./ui";
import type { Bodega, SkuOpcion } from "./tipos";

/** Lo que se espera a que el usuario deje de teclear antes de preguntar. */
const ESPERA_MS = 300;
const MIN_LETRAS = 2;

/** Lo que hace un Enter, decidido sin tocar el DOM (lo ejercitan las pruebas). */
export type AccionEnter =
  | { tipo: "nada" }
  /** La búsqueda de ESE texto no ha contestado: se resuelve cuando conteste. */
  | { tipo: "esperar" }
  /** Una opción del catálogo, por su posición en la lista. */
  | { tipo: "elegir"; indice: number }
  /** Hay opciones que la persona no ha visto: se le enseñan y NO se agrega nada. */
  | { tipo: "mostrar" }
  /** El texto tal cual, marcado «fuera del catálogo». */
  | { tipo: "manual" };

/** La fila que se pinta resaltada: la de las flechas o el cursor; si no, la exacta; si no, la primera. */
export function filaResaltada(e: { activo: number; alDia: boolean; opciones: SkuOpcion[]; q: string }): number {
  if (e.activo >= 0) return e.activo;
  if (!e.alDia || !e.opciones.length) return -1;
  const exacta = e.opciones.findIndex((o) => o.sku.toLowerCase() === e.q.toLowerCase());
  return exacta >= 0 ? exacta : 0;
}

/**
 * Enter con la lista a la vista. Gana lo que la persona resaltó; si no resaltó
 * nada, la coincidencia exacta o la primera opción (que es la que se ve
 * resaltada). El texto tal cual sólo entra desde SU fila o cuando no hay opciones.
 *
 * Con la lista CERRADA (se cerró con Esc y el texto se quedó) no hay nada a la
 * vista que elegir: Enter la vuelve a abrir, y sólo la coincidencia exacta
 * entra sin más.
 */
export function decidirEnter(e: {
  buscable: boolean; abierto: boolean; alDia: boolean; activo: number; opciones: SkuOpcion[]; q: string;
  ofreceManual: boolean;
}): AccionEnter {
  if (!e.buscable) return { tipo: "nada" };
  // 1 · Lo que la persona resaltó con las flechas o el cursor, con la lista a la vista.
  if (e.abierto && e.activo >= 0) {
    if (e.activo < e.opciones.length) return { tipo: "elegir", indice: e.activo };
    if (e.activo === e.opciones.length && e.ofreceManual) return { tipo: "manual" };
  }
  // 2 · Todavía no contesta la búsqueda de ESTE texto: se decide cuando conteste.
  if (!e.alDia) return { tipo: "esperar" };
  // 3 · Lista cerrada: sólo la exacta; lo demás se enseña primero.
  if (!e.abierto) {
    const d = decidirEnterPendiente(e);
    return d.tipo === "elegir" ? d : { tipo: "mostrar" };
  }
  // 4 · Nadie resaltó nada: la que se ve resaltada por omisión (la exacta o la primera).
  const resaltada = filaResaltada({ ...e, activo: -1 });
  return resaltada >= 0 ? { tipo: "elegir", indice: resaltada } : { tipo: "manual" };
}

/**
 * El Enter que llegó ANTES que la respuesta (quien teclea rápido, o un lector
 * de códigos). Aquí la persona no vio la lista, así que no se le elige «la
 * primera» a ciegas: sólo entra sola la coincidencia EXACTA. Si hay otras
 * opciones se le enseñan (y el siguiente Enter ya es con la lista a la vista);
 * si no hay ninguna, entra el texto tal cual, marcado.
 */
export function decidirEnterPendiente(e: { opciones: SkuOpcion[]; q: string }): AccionEnter {
  const exacta = e.opciones.findIndex((o) => o.sku.toLowerCase() === e.q.toLowerCase());
  if (exacta >= 0) return { tipo: "elegir", indice: exacta };
  return e.opciones.length ? { tipo: "mostrar" } : { tipo: "manual" };
}

/**
 * Lo que hay de un SKU, bodega por bodega. `elegibles` son los códigos de las
 * bodegas donde SÍ se puede hacer una orden; las demás salen apagadas.
 */
function Existencias({ opcion, elegibles }: { opcion: SkuOpcion; elegibles: ReadonlySet<string> | null }) {
  const existencias = opcion.existencias ?? [];
  if (!existencias.length) {
    return (
      <span className="text-slate-400"
            title="Este SKU no tiene saldo registrado en ninguna bodega de kubera. No es un cero: no se sabe.">
        sin existencias
      </span>
    );
  }
  return (
    <span className="flex flex-wrap justify-end gap-x-2.5 gap-y-0.5">
      {existencias.map((e) => {
        const admite = !elegibles || elegibles.has(e.almacen);
        return (
          <span key={e.almacen}
                title={admite
                  ? `${e.almacen}: ${num(e.fisico)} físicas − ${num(e.apartado)} apartadas = ${num(e.libre)} libres`
                  : `${e.almacen} no admite órdenes de venta: de ahí no se puede apartar`}
                className={!admite ? "text-slate-400" : e.libre > 0 ? "text-emerald-700" : "text-rose-600"}>
            <span className="font-mono text-[11px]">{e.almacen}</span>{" "}
            {admite ? <>libre <b className="tabular-nums">{num(e.libre)}</b></>
              : <span className="tabular-nums">{num(e.libre)}</span>}
          </span>
        );
      })}
    </span>
  );
}

export function BuscadorSku({ onElegir, deshabilitado, bodegas }: {
  /** `fueraDeCatalogo` = el SKU se escribió a mano y la búsqueda no lo encontró. */
  onElegir: (o: SkuOpcion, fueraDeCatalogo: boolean) => void;
  deshabilitado?: boolean;
  /**
   * Las bodegas donde se puede hacer una orden (kubera con `admite_ov`). Sin
   * este dato no se apaga ninguna: se pintan todas las existencias igual.
   */
  bodegas?: Bodega[];
}) {
  const [texto, setTexto] = useState("");
  const [abierto, setAbierto] = useState(false);
  // `q` va pegado a sus opciones: una respuesta lenta de un texto viejo no se
  // pinta debajo de lo que el usuario ya escribió después.
  const [res, setRes] = useState<{ q: string; opciones: SkuOpcion[] } | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activo, setActivo] = useState(-1);
  const caja = useRef<HTMLDivElement>(null);
  const campo = useRef<HTMLInputElement>(null);
  const enterPendiente = useRef(false);
  const idLista = useId();
  // Son dos o tres bodegas: armar el conjunto en cada repintado no cuesta nada.
  const elegibles = bodegas ? new Set(bodegas.map((b) => b.codigo)) : null;

  const q = texto.trim();
  const buscable = q.length >= MIN_LETRAS;
  const alDia = !!res && res.q === q && !cargando;
  const opciones = res && res.q === q ? res.opciones : [];
  const exacta = opciones.find((o) => o.sku.toLowerCase() === q.toLowerCase()) ?? null;
  // La fila «agregar tal cual» sólo aparece cuando YA se sabe que no está.
  const ofreceManual = buscable && alDia && !exacta;
  const total = opciones.length + (ofreceManual ? 1 : 0);
  // Lo que Enter va a elegir se VE antes de darlo: la fila resaltada.
  const resaltada = filaResaltada({ activo, alDia, opciones, q });

  useEffect(() => {
    setActivo(-1);
    if (q.length < MIN_LETRAS) {
      setRes(null);
      setCargando(false);
      setError(null);
      enterPendiente.current = false;
      return;
    }
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    const t = setTimeout(() => {
      buscarSkus(q, ctrl.signal)
        .then((r) => {
          setRes({ q, opciones: r.opciones ?? [] });
          setCargando(false);
        })
        .catch((e: unknown) => {
          if ((e as { name?: string })?.name === "AbortError") return;
          // Sin respuesta no se sabe si el SKU existe: la lista queda vacía y
          // la fila de «agregar tal cual» lo dice con todas sus letras.
          setRes({ q, opciones: [] });
          setError(mensajeDeError(e, "No se pudo buscar en el catálogo."));
          setCargando(false);
        });
    }, ESPERA_MS);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q]);

  const elegir = useCallback((o: SkuOpcion, fuera: boolean) => {
    onElegir(o, fuera);
    enterPendiente.current = false;
    setTexto("");
    setRes(null);
    setActivo(-1);
    setAbierto(false);
    // El cursor se queda en el buscador: lo normal es agregar varios seguidos.
    campo.current?.focus();
  }, [onElegir]);

  const elegirManual = useCallback(() => {
    if (q.length < MIN_LETRAS) return;
    // Fuera del catálogo no hay saldo que mostrar: `existencias` vacío = «no se sabe».
    elegir({ sku: q, nombre: null, existencias: [] }, true);
  }, [elegir, q]);

  // El Enter que llegó antes que la respuesta se resuelve cuando ésta llega.
  useEffect(() => {
    if (!enterPendiente.current || !alDia) return;
    enterPendiente.current = false;
    const d = decidirEnterPendiente({ opciones, q });
    if (d.tipo === "elegir") elegir(opciones[d.indice], false);
    else if (d.tipo === "manual") elegirManual();
    else setAbierto(true);
  }, [alDia, opciones, q, elegir, elegirManual]);

  // Clic fuera: se cierra la lista (el texto se queda).
  useEffect(() => {
    if (!abierto) return;
    const fuera = (ev: MouseEvent) => {
      if (caja.current && !caja.current.contains(ev.target as Node)) setAbierto(false);
    };
    document.addEventListener("mousedown", fuera);
    return () => document.removeEventListener("mousedown", fuera);
  }, [abierto]);

  // La opción resaltada con las flechas siempre queda a la vista.
  useEffect(() => {
    if (activo < 0) return;
    document.getElementById(`${idLista}-${activo}`)?.scrollIntoView({ block: "nearest" });
  }, [activo, idLista]);

  const alTeclear = (ev: KeyboardEvent<HTMLInputElement>) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      // Con la lista cerrada, la flecha sólo la abre: la primera ya sale resaltada.
      if (!abierto) { setAbierto(true); return; }
      if (!total) return;
      const paso = ev.key === "ArrowDown" ? 1 : -1;
      // Las flechas parten de la fila que ya se ve resaltada (la primera, de entrada).
      setActivo(resaltada < 0 ? (paso > 0 ? 0 : total - 1) : (resaltada + paso + total) % total);
      return;
    }
    if (ev.key === "Enter") {
      // Nunca manda un formulario que esté alrededor.
      ev.preventDefault();
      const d = decidirEnter({ buscable, abierto, alDia, activo, opciones, q, ofreceManual });
      if (d.tipo === "elegir") elegir(opciones[d.indice], false);
      else if (d.tipo === "manual") elegirManual();
      else if (d.tipo === "esperar") enterPendiente.current = true;
      else if (d.tipo === "mostrar") setAbierto(true);
      return;
    }
    if (ev.key === "Escape" && (abierto || texto)) {
      ev.preventDefault();
      if (abierto) setAbierto(false);
      else setTexto("");
    }
  };

  const fila = (on: boolean) =>
    `flex w-full items-center gap-3 px-3 py-2 text-left text-sm ${on ? "bg-indigo-50" : "hover:bg-slate-50"}`;

  return (
    <div ref={caja} className="relative">
      <div className={`flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 focus-within:border-indigo-300 focus-within:ring-2 focus-within:ring-indigo-100 ${
        deshabilitado ? "bg-slate-50" : "bg-white"}`}>
        <Search className="h-4 w-4 shrink-0 text-slate-400" />
        <input
          ref={campo} type="text" value={texto} disabled={deshabilitado}
          onChange={(ev) => { setTexto(ev.target.value); setAbierto(true); }}
          onFocus={() => setAbierto(true)}
          onKeyDown={alTeclear}
          role="combobox" aria-expanded={abierto && buscable} aria-controls={idLista}
          aria-autocomplete="list" aria-label="Agregar producto por SKU o nombre"
          aria-activedescendant={abierto && resaltada >= 0 ? `${idLista}-${resaltada}` : undefined}
          autoComplete="off" spellCheck={false} maxLength={200}
          placeholder="Agregar producto: SKU o nombre…"
          className="w-full bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
        />
        {cargando && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" />}
      </div>

      {abierto && buscable && !deshabilitado && (
        <div id={idLista} role="listbox" aria-label="Productos del catálogo"
             className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
          {opciones.map((o, i) => (
            <button
              key={o.sku} id={`${idLista}-${i}`} type="button" role="option" aria-selected={resaltada === i}
              // mousedown sin foco: el input no pierde el cursor al elegir.
              onMouseDown={(ev) => ev.preventDefault()}
              onMouseEnter={() => setActivo(i)}
              onClick={() => elegir(o, false)}
              className={fila(resaltada === i)}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[13px] font-bold text-slate-800">{o.sku}</span>
                <span className="block truncate text-xs text-slate-500">{o.nombre || "sin nombre en el catálogo"}</span>
              </span>
              <span className="max-w-[55%] shrink-0 text-xs"><Existencias opcion={o} elegibles={elegibles} /></span>
              {resaltada === i && <span className="shrink-0 text-[11px] text-slate-400">Enter</span>}
            </button>
          ))}

          {!opciones.length && (
            <p className="px-3 py-2 text-xs text-slate-400">
              {error ? <span className="text-rose-600">{error}</span>
                : alDia ? <>Nada en el catálogo con «{q}».</>
                  : "Buscando…"}
            </p>
          )}

          {ofreceManual && (
            <button
              id={`${idLista}-${opciones.length}`} type="button" role="option"
              aria-selected={resaltada === opciones.length}
              onMouseDown={(ev) => ev.preventDefault()}
              onMouseEnter={() => setActivo(opciones.length)}
              onClick={elegirManual}
              className={`${fila(resaltada === opciones.length)} border-t border-slate-100`}
            >
              <Plus className="h-4 w-4 shrink-0 text-amber-600" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-semibold text-slate-700">
                  Agregar <span className="font-mono">{q}</span> tal cual
                </span>
                <span className="block text-xs text-amber-700">
                  {error ? "No se pudo verificar contra el catálogo" : "No está en el catálogo"}: el
                  renglón queda marcado y, sin saldo en una bodega, no se podrá apartar.
                </span>
              </span>
              {/* «Enter» sólo donde Enter de verdad hace eso: sin opciones, o con esta fila resaltada. */}
              {(!opciones.length || resaltada === opciones.length) && (
                <span className="shrink-0 text-[11px] text-slate-400">Enter</span>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
