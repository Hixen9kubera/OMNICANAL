"use client";

/* El SELLO: los cinco pasos del flujo de un SKU en 109×10 px.
   Se pinta igual en la Lista y en el Mosaico porque es la MISMA lectura, y con
   las mismas muestras que el stepper para que el filtro sirva de leyenda.

   Dos reglas que no se pueden relajar:
   · `espera` y `na` se dibujan como `falta` (blanco), decisión aprobada en la
     maqueta; /inventario conserva su ámbar y no se toca.
   · `sin_dato` tiene su propio dibujo punteado. Pintar «no sé» como «no lo
     cumple» es la mentira que este componente existe para evitar. */

import {
  MUESTRA_FLUJO, ORDEN_CUADROS, muestraCuadro, sufijoCuenta,
  textoVariantes, tituloSello, type Muestra,
} from "@/lib/flujo";
import type { SelloFlujo } from "@/lib/types";
import { TarjetaSello, useSelloAbierto } from "./TarjetaSello";

interface DatosFila {
  sello: SelloFlujo | null | undefined;
  /** La foto se está armando: sin sello todavía, pero viene en camino. */
  calentando?: boolean;
  canal: string;
  /** La cuenta de ESTA fila (solo Mercado Libre la tiene). */
  cuenta?: string | null;
  /** id de cuenta → nombre visible, para «· por San Corpe». */
  etiquetasCuenta?: Record<string, string>;
  /** El SKU tiene costo validado. No es un paso del flujo: se usa para decirlo. */
  revisado?: boolean;
  /** SKU y nombre: la cabecera de la tarjeta dice de qué fila salió. */
  sku: string;
  nombre: string;
  /** Distingue dos publicaciones del MISMO SKU en un canal (el `item_id` de la
   *  fila). Sin esto las dos tarjetas se abrirían a la vez. */
  clave?: string | number | null;
}

function Cuadro({ m, x, w, rx, opacidad }: {
  m: Muestra; x: number; w: number; rx: number; opacidad?: number;
}) {
  return (
    <rect
      x={x} y={0.5} width={w} height={9} rx={rx}
      fill={m.fill} stroke={m.stroke} strokeDasharray={m.dash}
      opacity={opacidad}
    />
  );
}

/** La pista de 5 pasos. Geometría fija de la maqueta: no se reescala. */
export function SelloPista({ sello }: { sello: SelloFlujo }) {
  const p = sello.pasos;
  const padre = sello.etapa === "padre";
  // Dos casos en que Recibido y bodega NO aplican, y por eso se dibujan
  // apagados en vez de mentir con cuadros vacíos —que se leerían como «no
  // cumple»—: un padre (no se costea ni se recibe por pieza) y un SKU fuera de
  // la lista de Inventario, donde nadie lo ha puesto a validar.
  const sinValidar = !sello.en_piloto && !padre;
  const tenue = padre || sinValidar ? 0.55 : undefined;

  const recibido =
    padre || sinValidar || p.recibido.estado === "sin_dato" ? MUESTRA_FLUJO.sin_dato
      : p.recibido.estado === "si" ? MUESTRA_FLUJO.recibido
        : MUESTRA_FLUJO.falta;

  const listo =
    p.listo.estado === "si" ? MUESTRA_FLUJO.bodega
      : p.listo.estado === "sin_dato" ? MUESTRA_FLUJO.sin_dato
        : MUESTRA_FLUJO.listo;

  const destino =
    p.destino.sin_dato ? MUESTRA_FLUJO.sin_dato
      : (p.destino.full || p.destino.drop) ? MUESTRA_FLUJO.destino
        : MUESTRA_FLUJO.falta;

  // ubicación, stock, foto, specs — el mismo orden que el title y que la tabla
  // de /inventario.
  const X_CUADRO = [20.5, 28, 35.5, 43];

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="109" height="10" viewBox="0 0 109 10" fill="none"
      className="shrink-0"
      aria-hidden="true"
    >
      <Cuadro m={recibido} x={0.5} w={15} rx={2} />
      {ORDEN_CUADROS.map((c, i) => (
        <Cuadro
          key={c}
          m={padre || sinValidar ? MUESTRA_FLUJO.sin_dato : muestraCuadro(p.bodega[c], c)}
          x={X_CUADRO[i]} w={5} rx={1.5} opacidad={tenue}
        />
      ))}
      <Cuadro m={listo} x={53.5} w={15} rx={2} />
      <Cuadro m={destino} x={73.5} w={15} rx={2} />
      <Cuadro m={MUESTRA_FLUJO.restock} x={93.5} w={15} rx={2} />
    </svg>
  );
}

/**
 * Las dos líneas del sello, ya repartidas.
 *
 * En un PADRE el resumen de variantes viaja DENTRO de `etapa_texto` («Padre ·
 * 1 En FULL · 2 Recibido de 5»): lo arma el backend porque es él quien manda
 * ese texto. Pintarlo entero arriba y otra vez abajo escribía la misma frase
 * dos veces, y en los 330 px de la columna las dos se cortaban. Se parte por
 * el primer separador: arriba la etapa, abajo el detalle.
 */
function lineas(sello: SelloFlujo): { titulo: string; detalle: string | null } {
  if (!sello.variantes) return { titulo: sello.etapa_texto, detalle: null };
  const corte = sello.etapa_texto.indexOf(" · ");
  if (corte < 0) {
    // El backend cambió el formato: se arma el resumen aquí antes que perderlo.
    return { titulo: sello.etapa_texto, detalle: textoVariantes(sello.variantes) };
  }
  return {
    titulo: sello.etapa_texto.slice(0, corte),
    detalle: sello.etapa_texto.slice(corte + 3),
  };
}

/**
 * El sello, clicable, y su tarjeta.
 *
 * Es un hook y no un componente porque el `title` NATIVO vive en el contenedor
 * de la celda: con la tarjeta abierta hay que quitarlo (dos ayudas encimadas se
 * leen como un error), y para eso quien pinta el contenedor necesita saber si
 * está abierta.
 *
 * El clic se detiene aquí: la fila y la tarjeta del Mosaico abren el cajón del
 * producto, y pedir la evidencia del flujo no es pedir el cajón.
 */
function useSelloClicable({
  sello, sku, nombre, canal, cuenta, etiquetasCuenta = {}, revisado, clave,
}: DatosFila & { sello: SelloFlujo }) {
  const { abierta, alternar, cerrar, boton } = useSelloAbierto(
    `${canal}:${cuenta ?? ""}:${clave ?? ""}:${sku}`,
  );
  const pista = <SelloPista sello={sello} />;

  return {
    abierta,
    // Con la tarjeta abierta el title desaparece; lo demás lo sigue explicando.
    titulo: abierta ? undefined : tituloSello(sello, { revisado, etiquetas: etiquetasCuenta }),
    nodo: (
      <>
        <button
          ref={boton}
          type="button"
          onClick={(e) => { e.stopPropagation(); alternar(); }}
          aria-expanded={abierta}
          aria-label={`Ver la evidencia del flujo de ${sku}`}
          className={[
            "flex shrink-0 rounded transition-shadow",
            abierta
              ? "ring-4 ring-indigo-500/20"
              : "hover:ring-4 hover:ring-indigo-500/10 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-indigo-500/30",
          ].join(" ")}
        >
          {pista}
        </button>
        {abierta && (
          <TarjetaSello
            sello={sello}
            sku={sku}
            nombre={nombre}
            pista={pista}
            etiquetasCuenta={etiquetasCuenta}
            boton={boton}
            cerrar={cerrar}
          />
        )}
      </>
    ),
  };
}

/** «le falta: …», o el resumen de variantes si la fila es un padre. */
function Falta({ sello, detalle }: { sello: SelloFlujo; detalle: string | null }) {
  if (detalle) return <span className="text-slate-500">{detalle}</span>;
  if (!sello.le_falta.length) return null;
  return (
    <>
      <span className="text-slate-400">le falta:</span>{" "}
      {sello.le_falta.join(", ")}
    </>
  );
}

/** La columna «Flujo» de la Lista. Sin sello no hay hook que llamar: el caso
 *  vacío se resuelve antes de entrar a la celda con sello. */
export function SelloCeldaLista(props: DatosFila) {
  if (!props.sello) {
    return (
      <td className="px-3 py-2.5 text-xs text-slate-300">
        {props.calentando ? "calentando…" : "—"}
      </td>
    );
  }
  return <CeldaConSello {...props} sello={props.sello} />;
}

function CeldaConSello(props: DatosFila & { sello: SelloFlujo }) {
  const { sello, canal, cuenta, etiquetasCuenta = {} } = props;
  const { titulo: tituloNativo, nodo } = useSelloClicable(props);
  const sufijo = sufijoCuenta(sello, canal, cuenta, etiquetasCuenta);
  const apagada = sello.etapa === "ninguna" || sello.etapa === "sin_dato";
  const { titulo, detalle } = lineas(sello);

  return (
    <td className="px-3 py-2.5">
      <div title={tituloNativo} className="flex items-center gap-3">
        {nodo}
        <div className="min-w-0 flex-1">
          <div className={[
            "truncate text-xs font-semibold leading-4",
            apagada ? "text-slate-500" : "text-slate-800",
          ].join(" ")}>
            {titulo}
            {sufijo && <span className="font-normal text-slate-400">{sufijo}</span>}
          </div>
          <div className="truncate text-[11px] leading-4 text-slate-500">
            <Falta sello={sello} detalle={detalle} />
          </div>
        </div>
      </div>
    </td>
  );
}

/** El sello dentro de la tarjeta del Mosaico. Sin sello no se pinta nada: la
 *  tarjeta conserva su badge DROP OFF y no queda un hueco sin explicar. */
export function SelloTarjeta(props: DatosFila) {
  if (!props.sello) return null;
  return <TarjetaConSello {...props} sello={props.sello} />;
}

function TarjetaConSello(props: DatosFila & { sello: SelloFlujo }) {
  const { sello, canal, cuenta, etiquetasCuenta = {} } = props;
  const { titulo: tituloNativo, nodo } = useSelloClicable(props);
  const sufijo = sufijoCuenta(sello, canal, cuenta, etiquetasCuenta);
  const apagada = sello.etapa === "ninguna" || sello.etapa === "sin_dato";
  const { titulo, detalle } = lineas(sello);
  const b = sello.pasos.bodega;
  const bodega = sello.variantes
    ? "padre"
    : b.n_listo === null ? "bodega sin dato" : `bodega ${b.n_listo} de 4`;

  return (
    <div
      title={tituloNativo}
      className="flex flex-col gap-1 border-t border-slate-100 pt-2"
    >
      <div className={[
        "truncate text-xs font-semibold leading-4",
        apagada ? "text-slate-500" : "text-slate-800",
      ].join(" ")}>
        {titulo}
        {sufijo && <span className="font-normal text-slate-400">{sufijo}</span>}
      </div>
      <div className="flex items-center justify-between gap-2">
        {nodo}
        <span className="whitespace-nowrap text-[10px] font-semibold leading-[15px] tabular-nums text-slate-400">
          {bodega}
        </span>
      </div>
      {/* Alto fijo: sin él las tarjetas de una misma hilera se desalinean según
          cuánto le falte a cada SKU. */}
      <div className="line-clamp-2 min-h-[33px] text-[11px] leading-[16.5px] text-slate-500">
        <Falta sello={sello} detalle={detalle} />
      </div>
    </div>
  );
}
