"use client";

/* ── LA TARJETA DEL SELLO ───────────────────────────────────────────────────
   El sello dice EN QUÉ etapa está un SKU. Esta tarjeta dice POR QUÉ, y sobre
   todo qué parte de ese «por qué» nadie guarda. Es la dirección B de la maqueta
   (Eduardo, 17-sep): un clic en el sello, una tarjeta anclada a él, la tabla sin
   moverse.

   Tres decisiones que no son cosméticas:

   · POSICIÓN FIJA calculada del rect del sello, no `absolute`. La tabla vive en
     un contenedor con overflow-x-auto y una tarjeta absoluta se recortaría en su
     borde — la misma razón que en PanelHover.tsx y en Ayuda.tsx.

     Y va en un PORTAL a `document.body`, que PanelHover no necesita: la tarjeta
     del Mosaico lleva `hover:-translate-y-1`, y un ancestro con `transform` deja
     de ser el viewport para lo que cuelga de él en `position: fixed` — medido
     aquí: la tarjeta salía 4 px corrida y anclada a la tarjeta del producto.
     El `stopPropagation` sigue siendo obligatorio: los eventos de un portal de
     React burbujean por el ÁRBOL DE REACT, no por el DOM.

   · DOS VELOCIDADES. Lo que ya viaja en la fila (`producto.flujo`) se pinta al
     instante; la evidencia de /api/inventario tarda ~6 s por SKU y llega a un
     esqueleto. Abrir la tarjeta y esperar 6 s en blanco haría que nadie la
     abriera dos veces.

   · LO QUE NO SE GUARDA SE ESCRIBE. Cada tramo lleva «De dónde sale» y «No se
     guarda» porque el hueco es el hallazgo: el conteo de cajas en piso es el que
     MANDA y no existe en ningún sistema, esta foto todavía no evalúa specs
     —la matriz existe desde v0.540.0, el flujo aún no la lee— y por eso Listo
     da 0 en todo el catálogo, y la validación de bodega no la firma nadie
     — se deduce de Odoo. Un `null` aquí nunca se pinta como 0. */

import Link from "next/link";
import {
  useCallback, useEffect, useRef, useState, useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle, Check, Copy, Warehouse, X,
} from "lucide-react";

import { listarInventario, mensajeDeError } from "@/lib/api";
import {
  MUESTRA_FLUJO, ORDEN_CUADROS, cifra, horaCdmx, muestraCuadro, textoVariantes,
  type ClaveCuadro, type Muestra,
} from "@/lib/flujo";
import type {
  CotejoCajas, FilaInventario, PuntoBodega, SelloFlujo, UbicacionSku,
} from "@/lib/types";

/* ── Quién está abierta ─────────────────────────────────────────────────────
   Vive FUERA de React porque la respuesta es global: solo una tarjeta a la vez,
   y las filas no comparten padre (la Lista y el Mosaico montan cada sello por su
   cuenta). Un contexto obligaría a envolver las dos páginas para un booleano. */
let abiertaActual: string | null = null;
const suscriptores = new Set<() => void>();

function fijarAbierta(id: string | null) {
  abiertaActual = id;
  suscriptores.forEach((avisar) => avisar());
}

function suscribir(avisar: () => void): () => void {
  suscriptores.add(avisar);
  return () => { suscriptores.delete(avisar); };
}

/** El estado de UN sello. `boton` es la referencia a la que vuelve el foco. */
export function useSelloAbierto(id: string) {
  const abierta = useSyncExternalStore(
    suscribir,
    () => abiertaActual === id,
    () => false, // en el servidor no hay ninguna abierta
  );
  const boton = useRef<HTMLButtonElement>(null);

  const alternar = useCallback(() => {
    fijarAbierta(abiertaActual === id ? null : id);
  }, [id]);

  // `devolverFoco` es false al cerrar por clic fuera: ahí el usuario ya está en
  // otra parte de la pantalla y robarle el foco movería el scroll.
  const cerrar = useCallback((devolverFoco = true) => {
    fijarAbierta(null);
    if (devolverFoco) boton.current?.focus({ preventScroll: true });
  }, []);

  return { abierta, alternar, cerrar, boton };
}

/* ── Caché de evidencia ─────────────────────────────────────────────────────
   /api/inventario tarda ~6 s por SKU (medido en el sandbox). Reabrir el mismo
   sello tiene que ser instantáneo, así que lo traído se queda a nivel de módulo
   — sobrevive a que la fila se desmonte al paginar. Tope de 60 SKUs: cada fila
   pesa decenas de KB y una sesión larga los abre de a montones.

   `null` es una respuesta legítima: el SKU no está en /api/inventario. No es lo
   mismo que un error, y no se reintenta. */
const CACHE = new Map<string, FilaInventario | null>();
const TOPE_CACHE = 60;

function guardarEnCache(sku: string, fila: FilaInventario | null) {
  CACHE.set(sku, fila);
  while (CACHE.size > TOPE_CACHE) {
    const masVieja = CACHE.keys().next().value;
    if (masVieja === undefined) break;
    CACHE.delete(masVieja);
  }
}

interface Evidencia {
  fila: FilaInventario | null;
  cargando: boolean;
  error: string | null;
}

function useEvidencia(sku: string) {
  const [intento, setIntento] = useState(0);
  const [estado, setEstado] = useState<Evidencia>(() => (
    CACHE.has(sku)
      ? { fila: CACHE.get(sku) ?? null, cargando: false, error: null }
      : { fila: null, cargando: true, error: null }
  ));

  useEffect(() => {
    if (intento === 0 && CACHE.has(sku)) {
      setEstado({ fila: CACHE.get(sku) ?? null, cargando: false, error: null });
      return;
    }
    const control = new AbortController();
    setEstado({ fila: null, cargando: true, error: null });
    listarInventario([sku], control.signal)
      .then((r) => {
        const fila = r.items.find((i) => i.sku === sku) ?? null;
        guardarEnCache(sku, fila);
        setEstado({ fila, cargando: false, error: null });
      })
      .catch((e: unknown) => {
        // Abortar es lo NORMAL al cerrar la tarjeta: no es un error que mostrar.
        if (control.signal.aborted) return;
        setEstado({
          fila: null,
          cargando: false,
          error: mensajeDeError(e, "no se pudo leer la evidencia"),
        });
      });
    // Cierre de la tarjeta o cambio de SKU: la petición se aborta sola.
    return () => { control.abort(); };
  }, [sku, intento]);

  return { ...estado, reintentar: () => setIntento((n) => n + 1) };
}

/* ── Piezas sueltas ─────────────────────────────────────────────────────────*/

const FECHA_CDMX = new Intl.DateTimeFormat("es-MX", {
  day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  hour12: false, timeZone: "America/Mexico_City",
});

function fechaCdmx(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : FECHA_CDMX.format(d);
}

/** El mismo cuadro del sello, a escala de texto: la muestra es la leyenda. */
function Muestrita({ m, ancho = 11 }: { m: Muestra; ancho?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={ancho} height={7} viewBox={`0 0 ${ancho + 1} 8`} fill="none"
      className="shrink-0" aria-hidden="true"
    >
      <rect
        x={0.5} y={0.5} width={ancho} height={7} rx={1.5}
        fill={m.fill} stroke={m.stroke} strokeDasharray={m.dash}
      />
    </svg>
  );
}

function Rotulo({ children, muestra }: { children: React.ReactNode; muestra?: Muestra }) {
  return (
    <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase leading-[14px] tracking-wider text-slate-400">
      {muestra && <Muestrita m={muestra} />}
      {children}
    </span>
  );
}

/** Qué columna de /inventario dio el «sí» de Recibido, en dos palabras. */
const FUENTE_CORTA: Record<string, string> = {
  packing_list: "packing list",
  odoo: "empaque Odoo",
  ambas: "las dos",
};

function Pildora({ texto, clase }: { texto: string; clase: string }) {
  return (
    <span className={`rounded px-1.5 text-[9px] font-bold uppercase leading-[14px] tracking-wide ${clase}`}>
      {texto}
    </span>
  );
}

/** «De dónde sale» / «No se guarda»: los dos renglones fijos de cada tramo. */
function Procedencia({ sale, noSeGuarda }: { sale: React.ReactNode; noSeGuarda?: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[76px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-1.5">
      <span className="text-[10px] font-semibold uppercase leading-[15px] tracking-wide text-slate-400">
        De dónde sale
      </span>
      <span className="text-[11px] leading-[15px] text-slate-600">{sale}</span>
      {noSeGuarda && (
        <>
          <span className="text-[10px] font-semibold uppercase leading-[15px] tracking-wide text-slate-400">
            No se guarda
          </span>
          <span className="text-[11px] leading-[15px] text-slate-500">{noSeGuarda}</span>
        </>
      )}
    </div>
  );
}

function Tramo({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5 border-t border-slate-100 px-4 py-2.5">
      {children}
    </section>
  );
}

/** Barras grises mientras la evidencia viaja. Nunca un 0 de relleno. */
function Esqueleto({ lineas = 2 }: { lineas?: number }) {
  return (
    <div className="flex animate-pulse flex-col gap-1.5" aria-hidden="true">
      {Array.from({ length: lineas }, (_, i) => (
        <div
          key={i}
          className="h-2.5 rounded bg-slate-100"
          style={{ width: i === lineas - 1 ? "62%" : "100%" }}
        />
      ))}
    </div>
  );
}

/** El tramo no se pudo leer. La tarjeta NO se rompe: ofrece volver a pedirlo. */
function NoSePudo({ error, reintentar }: { error: string; reintentar: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-[11px] leading-[15px] text-slate-500">
      <span>No se pudo leer la evidencia de este SKU: {error}.</span>
      <button
        type="button"
        onClick={reintentar}
        className="rounded border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-600 transition-colors hover:border-indigo-300 hover:text-indigo-600"
      >
        Reintentar
      </button>
    </div>
  );
}

/* ── Tramo 1 · RECIBIDO ─────────────────────────────────────────────────────*/

/** Cajas × piezas por caja, cuando las dos cifras existen. `null` si no. */
function piezasDe(cajas: number | null, porCaja: number | null): number | null {
  if (cajas === null || porCaja === null) return null;
  return Math.round(cajas * porCaja * 100) / 100;
}

function TextoFuentePl({ c, contenedorDeOdoo }: { c: CotejoCajas; contenedorDeOdoo: boolean }) {
  const contenedor = contenedorDeOdoo
    ? " El contenedor, de Odoo."
    : " El contenedor sale de la misma copia de costos validados.";

  if (c.pl_fuente === "costos_validados") {
    return (
      <>
        Costos validados en kubera: copia congelada de las cargas del 21-may y
        del 3-jun. Nada la actualiza, así que un SKU creado después sale sin
        recibir aunque haya llegado.{contenedor}
      </>
    );
  }
  if (c.pl_fuente === "renglon") {
    const renglones = c.pl_renglones?.length
      ? (c.pl_renglones.length === 1
        ? `renglón ${c.pl_renglones[0]}`
        : `${c.pl_renglones.length} renglones (del ${c.pl_renglones[0]} al ${c.pl_renglones[c.pl_renglones.length - 1]})`)
      : "sin renglón anotado";
    const como = c.pl_origen_renglon === "registrado"
      ? " Lo registró la validación de costos."
      : c.pl_origen_renglon === "foto"
        ? " Lo empató la foto de Odoo, no lo capturó una persona."
        : "";
    return (
      <>
        Packing list{" "}
        {c.pl_archivo
          ? <span className="font-medium text-slate-700">{c.pl_archivo}</span>
          : "(archivo no registrado)"}
        , {renglones}.{como}{contenedor}
      </>
    );
  }
  if (c.pl_leyendo) {
    return <>El packing list se está leyendo: todavía no se puede afirmar que este SKU no tenga renglón.{contenedor}</>;
  }
  return <>Ningún renglón de packing list empató con este SKU, ni en el archivo ni en la copia congelada.{contenedor}</>;
}

function TextoNoSeGuardaPl({ c }: { c: CotejoCajas }) {
  const huecos: string[] = [];
  // La que MANDA y no existe: es el hallazgo, no un detalle.
  huecos.push("el conteo de cajas en piso, que es justo el que manda");
  if (!c.pl_archivo) huecos.push("de qué archivo salió");
  if (!c.pl_origen_renglon && c.pl_fuente === "renglon") huecos.push("cómo se encontró el renglón");
  if (c.pl_fuente === "costos_validados") huecos.push("el renglón del packing list");
  return <>No se registra {huecos.join(", ni ")}.</>;
}

/** El SKU no está en la lista de la pestaña Inventario: el dato de abajo puede
 *  existir, pero nadie lo ha validado. Se dice ANTES de las cifras para que no
 *  se lean como una validación. */
function SinValidar({ que }: { que: string }) {
  return (
    <p className="rounded-md bg-slate-100 px-2 py-1.5 text-[11px] leading-[15px] text-slate-600">
      Sin validar: este SKU no está en la lista de Inventario, así que no cuenta
      en {que}. Lo de abajo es el dato que existe hoy, no una revisión.
    </p>
  );
}

function TramoRecibido({
  sello, fila, cargando, error, reintentar,
}: {
  sello: SelloFlujo; fila: FilaInventario | null; cargando: boolean;
  error: string | null; reintentar: () => void;
}) {
  const sinValidar = !sello.en_piloto && sello.etapa !== "padre";
  const c = fila?.cotejo_cajas;
  const piezasPl = c ? piezasDe(c.packing_list, c.piezas_por_caja_pl) : null;
  const piezasOdoo = c ? piezasDe(c.odoo, c.piezas_por_caja_odoo) : null;
  const discrepan = piezasPl !== null && piezasOdoo !== null
    && Math.abs(piezasPl - piezasOdoo) > 0.5;

  return (
    <Tramo>
      <div className="flex items-start justify-between gap-2">
        <Rotulo muestra={MUESTRA_FLUJO.recibido}>Recibido</Rotulo>
        <span className="flex min-w-0 items-center gap-1.5">
          {fila?.contenedor && (
            <span className="truncate text-[10px] leading-[14px] text-slate-500">
              {fila.contenedor}
              {fila.embarque && ` · embarque ${fila.embarque}`}
              {fila.contenedor_es_booking && " (booking)"}
            </span>
          )}
          {/* Cuál de las dos columnas lo dio. Sin esto, la tabla de abajo enseña
              tres cifras y no se sabe cuál fue la que contó. */}
          {sello.pasos.recibido.estado === "si" && sello.pasos.recibido.fuente && (
            <Pildora
              texto={FUENTE_CORTA[sello.pasos.recibido.fuente]}
              clase="bg-slate-100 text-slate-600"
            />
          )}
          {/* En un padre no hay recepción que aproximar: la insignia sobraría. */}
          {sello.pasos.recibido.estado !== "na" && (
            <Pildora texto="aprox." clase="bg-sky-50 text-sky-700 ring-1 ring-sky-200" />
          )}
        </span>
      </div>

      {sello.etapa === "padre" ? (
        <p className="text-[11px] leading-[15px] text-slate-500">
          No aplica a un padre: se recibe por variante.
          {sello.variantes && ` ${textoVariantes(sello.variantes)}.`}
        </p>
      ) : cargando ? (
        <Esqueleto lineas={3} />
      ) : error ? (
        <NoSePudo error={error} reintentar={reintentar} />
      ) : !c ? (
        <p className="text-[11px] leading-[15px] text-slate-500">
          Este SKU no trae cotejo de cajas en /inventario.
        </p>
      ) : (
        <>
          {sinValidar && <SinValidar que="Recibido" />}
          <div className="grid grid-cols-[88px_repeat(3,minmax(0,1fr))] items-center gap-x-2 gap-y-1.5 rounded-xl border border-slate-200 bg-slate-50 p-2.5">
            <span />
            <span className="text-[9px] font-bold uppercase leading-3 tracking-wide text-slate-400">Packing list</span>
            <span className="text-[9px] font-bold uppercase leading-3 tracking-wide text-slate-400">Odoo</span>
            <span className="text-[9px] font-bold uppercase leading-3 tracking-wide text-slate-400">Bodega</span>

            <span className="text-[11px] font-semibold leading-4 text-slate-600">Cajas</span>
            <Cifra n={c.packing_list} />
            <Cifra n={c.odoo} />
            {/* `bodega` es la que MANDA (Brandon, 8-sep) y viene null en todo el
                catálogo: no existe el canal para capturarla. Escribirlo como 0
                afirmaría que se contó y dio cero. */}
            <span
              title="La cifra que manda según el cotejo, y ningún sistema la tiene: no hay canal para capturar el conteo de piso."
              className="row-span-2 flex items-center text-[10px] leading-[14px] text-slate-400"
            >
              no se captura
            </span>

            <span className="text-[11px] font-semibold leading-4 text-slate-600">Piezas por caja</span>
            <Cifra n={c.piezas_por_caja_pl} />
            <Cifra n={c.piezas_por_caja_odoo} />
          </div>

          {discrepan && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-[15px] text-amber-800">
              {cifra(c.packing_list)} × {cifra(c.piezas_por_caja_pl)} = {cifra(piezasPl)} piezas ·{" "}
              {cifra(c.odoo)} × {cifra(c.piezas_por_caja_odoo)} = {cifra(piezasOdoo)}. Dos
              fuentes del mismo embarque que no dan lo mismo; ninguna corrige a la otra.
            </p>
          )}
          {!discrepan && c.pl_piezas !== null && (
            <p className="text-[11px] leading-[15px] text-slate-500">
              El packing list declara {cifra(c.pl_piezas)} piezas para este SKU
              {c.pl_compartida && " · el cartón se comparte con otros renglones, la caja no es toda suya"}.
            </p>
          )}

          <Procedencia
            sale={<TextoFuentePl c={c} contenedorDeOdoo={fila?.contenedor_fuente === "odoo"} />}
            noSeGuarda={
              <>
                <TextoNoSeGuardaPl c={c} />{" "}
                {fila?.contenedor && (
                  <>Un SKU sí llega en varios contenedores y la base guarda uno: {fila.contenedor} es el último, no la lista.</>
                )}
              </>
            }
          />
        </>
      )}
    </Tramo>
  );
}

/** Una cifra del cotejo. `null` se escribe «—», nunca 0. */
function Cifra({ n }: { n: number | null }) {
  if (n === null) {
    return <span className="text-[10px] leading-[14px] text-slate-400" title="Sin dato: no es cero.">sin dato</span>;
  }
  return (
    <span className="text-[15px] font-bold leading-[18px] tabular-nums text-slate-900">
      {cifra(n)}
    </span>
  );
}

/* ── Tramo 2 · VALIDADO BODEGA ──────────────────────────────────────────────*/

const NOMBRE_PUNTO: Record<ClaveCuadro, string> = {
  ubicacion: "Ubicación",
  stock: "Stock",
  foto: "Foto",
  specs: "Specs",
};

const PILDORA_PUNTO: Record<string, { texto: string; clase: string }> = {
  listo: { texto: "listo", clase: "bg-emerald-50 text-emerald-700" },
  falta: { texto: "falta", clase: "bg-slate-100 text-slate-500" },
  espera: { texto: "en espera", clase: "bg-amber-50 text-amber-700" },
  na: { texto: "no aplica", clase: "bg-slate-100 text-slate-400" },
  sin_dato: { texto: "sin dato", clase: "bg-slate-100 text-slate-400" },
};

function TramoBodega({
  sello, fila, cargando, error,
}: {
  sello: SelloFlujo; fila: FilaInventario | null; cargando: boolean;
  error: string | null;
}) {
  const b = sello.pasos.bodega;
  const padre = sello.etapa === "padre";
  const sinValidar = !sello.en_piloto && !padre;
  const vb = fila?.validacion_bodega;
  // El conteo lo manda la evidencia cuando llega; mientras, el del sello, que ya
  // viaja en la fila. Los dos salen de la misma foto de Odoo. En un PADRE no se
  // usa ninguno: /inventario contesta 0 de 4 porque mide el registro del padre,
  // y ese 0 se leería como «no cumple» cuando la pregunta no aplica.
  const cumplidos = vb?.cumplidos ?? b.n_listo;
  const porClave = new Map<string, PuntoBodega>((vb?.puntos ?? []).map((p) => [p.clave, p]));

  return (
    <Tramo>
      <div className="flex items-center justify-between gap-2">
        <Rotulo muestra={MUESTRA_FLUJO.bodega}>Validado bodega</Rotulo>
        <span className="text-[11px] font-bold leading-[14px] text-slate-600">
          {padre || sinValidar ? "no aplica"
            : cumplidos === null ? "sin dato de Odoo" : `${cumplidos} de 4`}
        </span>
      </div>

      {padre ? (
        <p className="text-[11px] leading-[15px] text-slate-500">
          No aplica a un padre: los cuatro requisitos se miden por variante.
        </p>
      ) : sinValidar ? (
        <>
          <SinValidar que="Validado bodega" />
          {vb?.puntos?.length ? (
            <div className="grid grid-cols-2 gap-x-2.5 gap-y-2 opacity-70">
              {vb.puntos.map((punto) => (
                <div key={punto.clave} className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-[11px] font-semibold leading-4 text-slate-600">
                    {punto.titulo}
                  </span>
                  <span className="text-[10px] leading-[14px] text-slate-500">
                    {punto.etiqueta}
                    {punto.detalle ? ` · ${punto.detalle}` : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-2.5 gap-y-2">
            {ORDEN_CUADROS.map((clave) => {
              const estado = b[clave];
              const punto = porClave.get(clave);
              const p = PILDORA_PUNTO[punto?.estado ?? estado] ?? PILDORA_PUNTO.sin_dato;
              return (
                <div key={clave} className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex items-center gap-1.5">
                    <Muestrita m={muestraCuadro(estado, clave)} ancho={9} />
                    <span className="text-[11px] font-semibold leading-4 text-slate-700">
                      {NOMBRE_PUNTO[clave]}
                    </span>
                    <Pildora texto={p.texto} clase={p.clase} />
                  </div>
                  {/* El detalle es lo único que espera a la evidencia: el estado y
                      la muestra ya estaban en la fila. */}
                  {punto ? (
                    <span className="truncate text-[10px] leading-[14px] text-slate-500" title={punto.detalle}>
                      {punto.detalle}
                    </span>
                  ) : cargando ? (
                    <span className="h-2.5 w-3/4 animate-pulse rounded bg-slate-100" aria-hidden="true" />
                  ) : (
                    <span className="text-[10px] leading-[14px] text-slate-300">—</span>
                  )}
                </div>
              );
            })}
          </div>

          {/* Es la MISMA petición que falló arriba: el aviso con su botón vive en
              Recibido y aquí solo se dice qué falta, sin repetir el reintento. */}
          {error && (
            <p className="text-[11px] leading-[15px] text-slate-400">
              El detalle de cada punto no se pudo leer; los estados de arriba salen
              del sello, que ya viajaba en la fila.
            </p>
          )}

          {b.escritura_distinta && b.codigo_odoo && (
            <p className="text-[11px] leading-[15px] text-slate-500">
              Odoo lo escribe <span className="font-mono text-slate-700">{b.codigo_odoo}</span>:
              cumple los tres cuadros pero queda fuera de la lista, que distingue mayúsculas.
            </p>
          )}

          <Procedencia
            sale={
              <>
                Foto de Odoo de las {horaCdmx(b.generado)} h: stock.quant,
                qty_available / free_qty, image_256. Specs no tiene fuente, y por eso
                4 de 4 —y Listo— dan 0 en todo el catálogo.
              </>
            }
            noSeGuarda={
              <>
                Quién validó en bodega y cuándo: los cuatro requisitos se deducen de
                Odoo, nadie firma.
                {fila?.ultimo_paso
                  ? <> Lo único firmado es el último paso: {fila.ultimo_paso.accion}
                    {fila.ultimo_paso.actor ? `, ${fila.ultimo_paso.actor}` : ""},
                    {" "}{fechaCdmx(fila.ultimo_paso.fecha)}.</>
                  : <> Este SKU no tiene ningún paso en la bitácora.</>}
              </>
            }
          />
        </>
      )}
    </Tramo>
  );
}

/* ── Tramo 3 · DESTINO ──────────────────────────────────────────────────────*/

function textoDestino(sello: SelloFlujo): string {
  const d = sello.pasos.destino;
  if (d.full && d.drop) return "En FULL (ML) y DROP";
  if (d.full) return "En FULL (ML)";
  if (d.drop) return "En DROP";
  if (d.full === false && d.drop === false) return "Ni FULL ni DROP";
  return "sin dato";
}

function piezasDrop(ubicaciones: UbicacionSku[] | undefined): number | null {
  if (!ubicaciones?.length) return null;
  const drop = ubicaciones.filter((u) => u.bodega.toUpperCase().includes("DROP"));
  if (!drop.length) return null;
  return drop.reduce((s, u) => s + u.piezas, 0);
}

function TramoDestino({
  sello, fila, cargando, etiquetasCuenta,
}: {
  sello: SelloFlujo; fila: FilaInventario | null; cargando: boolean;
  etiquetasCuenta: Record<string, string>;
}) {
  const d = sello.pasos.destino;
  const enDrop = piezasDrop(fila?.ubicaciones);
  const rackDrop = fila?.ubicaciones?.find((u) => u.bodega.toUpperCase().includes("DROP"));
  const cuentas = d.full_cuentas?.length
    ? d.full_cuentas.map((c) => etiquetasCuenta[c] ?? c).join(" y ")
    : null;
  const stage = fila?.piezas_en_stage;

  return (
    <Tramo>
      <div className="flex items-center justify-between gap-2">
        <Rotulo muestra={MUESTRA_FLUJO.destino}>Destino</Rotulo>
        <span className="text-[11px] font-bold leading-[14px] text-slate-600">
          {textoDestino(sello)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-2.5 gap-y-2">
        <Destino
          titulo="En FULL"
          piezas={d.full ? fila?.stock_full ?? null : null}
          cargando={cargando && !!d.full}
          activo={!!d.full}
          /* `full === false` es una respuesta; `full === null` es que la fuente
             de canales no contestó. Decir lo segundo por lo primero convierte un
             «no» medido en un «no sé». */
          detalle={
            d.full === false ? "no está en FULL"
              : d.full === null ? "la fuente de canales no contestó"
                : cuentas ? `${cuentas}, en la bodega del marketplace`
                  : "en FULL, sin saber por qué cuenta"
          }
        />
        <Destino
          titulo="En DROP"
          piezas={enDrop}
          cargando={cargando && !!d.drop}
          activo={!!d.drop}
          detalle={
            rackDrop ? `${rackDrop.bodega}${rackDrop.rack ? `, rack ${rackDrop.rack}` : ""}`
              : d.drop === false ? "sin piezas en el almacén DROP OFF"
                : d.drop === null ? "Odoo no contestó por el almacén DROP OFF"
                  : "en DROP, sin ubicación en la foto"
          }
        />
        {/* FBA es la bodega de Amazon: ni FULL ni DROP. Solo se pinta si hay
            piezas, para no sumar una columna vacía a los 452 px. */}
        {!!fila?.stock_fba && (
          <Destino
            titulo="En FBA"
            piezas={fila.stock_fba}
            cargando={false}
            activo
            detalle="bodega de Amazon: no es FULL y se cuenta aparte"
          />
        )}
      </div>

      <Procedencia
        sale={
          <>
            channel.listings.is_fulfillment para FULL y FBA, stock.quant para DROP.
            {stage ? ` Otras ${cifra(stage)} piezas están en zona de paso: ni rack ni destino.` : ""}
            {d.sin_dato && " La fuente de canales no está utilizable: el destino va sin dato."}
          </>
        }
      />
    </Tramo>
  );
}

function Destino({
  titulo, piezas, cargando, activo, detalle,
}: {
  titulo: string; piezas: number | null; cargando: boolean; activo: boolean; detalle: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className={`text-[11px] font-semibold leading-4 ${activo ? "text-slate-700" : "text-slate-400"}`}>
        {titulo}
        {cargando
          ? <span className="ml-1 inline-block h-2 w-8 animate-pulse rounded bg-slate-100 align-middle" aria-hidden="true" />
          : piezas !== null && <> · {cifra(piezas)} piezas</>}
      </span>
      <span className="truncate text-[10px] leading-[14px] text-slate-500" title={detalle}>
        {detalle}
      </span>
    </div>
  );
}

/* ── Tramo 4 · QUÉ LE FALTA ─────────────────────────────────────────────────*/

/** Por qué falta cada cosa. Specs va a mano porque su explicación no vive en
 *  ningún campo: es una decisión que nadie ha tomado todavía. */
const PORQUE_FALTA: Record<string, string> = {
  specs: "matriz por categoría: falta definir el formato del Excel y cómo llega. "
    + "Sin eso, «Listo para FULL o DROP» sigue bloqueado para todo el catálogo.",
};

function TramoFalta({ sello, fila }: { sello: SelloFlujo; fila: FilaInventario | null }) {
  const puntos = fila?.validacion_bodega?.puntos ?? [];

  return (
    <Tramo>
      <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase leading-[14px] tracking-wider text-slate-400">
        <AlertTriangle size={12} className="text-amber-600" aria-hidden="true" />
        Qué le falta
      </span>
      {sello.etapa === "padre" ? (
        <p className="text-[11px] leading-[15px] text-slate-500">
          Un padre no tiene camino propio a Listo: le falta lo que les falte a sus
          variantes, y cada una lo dice en su propia fila.
        </p>
      ) : sello.le_falta.length === 0 ? (
        <p className="text-[11px] leading-[15px] text-slate-500">
          Nada en el camino a Listo. El destino, el costo y el restock corren aparte.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {sello.le_falta.map((falta) => {
            const clave = falta.toLowerCase();
            const punto = puntos.find((p) => p.titulo.toLowerCase() === clave);
            const porque = PORQUE_FALTA[clave] ?? punto?.detalle ?? null;
            return (
              <li key={falta} className="text-[11px] leading-[15px] text-slate-600">
                <span className="font-bold text-slate-800">{falta}</span>
                {porque && <> · {porque}</>}
              </li>
            );
          })}
        </ul>
      )}
    </Tramo>
  );
}

/* ── La tarjeta ─────────────────────────────────────────────────────────────*/

const ANCHO = 452;
/** Alto de referencia para decidir si cabe abajo. No recorta: el cuerpo scrollea. */
const ALTO_COMODO = 560;
const MARGEN = 12;
/** El navbar es sticky y mide 64 px: volcada hacia arriba, la tarjeta se para
 *  antes de meterse debajo de él en vez de quedar a medias. */
const GUARDA_NAVBAR = 72;

/** `y` es `top` cuando cuelga hacia abajo y `bottom` cuando se voltea. Anclar
 *  por `bottom` en vez de `transform: translateY(-100%)` NO es un capricho: la
 *  animación de entrada anima el `transform` y se comería el desplazamiento —
 *  la tarjeta arrancaría 500 px más abajo de donde va. */
interface Sitio { x: number; y: number; w: number; alto: number; flecha: number; arriba: boolean }

function sitioDe(boton: HTMLElement): Sitio {
  const r = boton.getBoundingClientRect();
  const w = Math.min(ANCHO, window.innerWidth - 16);
  const abajo = window.innerHeight - r.bottom - MARGEN;
  const arribaLibre = Math.max(0, r.top - MARGEN - GUARDA_NAVBAR);
  // Se voltea solo si arriba hay MÁS sitio: una tarjeta que salta de lado sin
  // ganar nada se lee como un parpadeo.
  const arriba = abajo < Math.min(ALTO_COMODO, arribaLibre);
  // El borde izquierdo se ancla al sello, pero nunca se sale de la ventana.
  const x = Math.min(Math.max(r.left, 8), Math.max(8, window.innerWidth - w - 8));
  // La flecha apunta al centro del sello, sin montarse en las esquinas.
  const flecha = Math.min(Math.max(r.left + r.width / 2 - x, 18), w - 28);
  return {
    x,
    y: arriba ? window.innerHeight - r.top + MARGEN : r.bottom + MARGEN,
    w,
    // El alto es EXACTAMENTE el sitio que hay, sin mínimo: un piso de 240 px
    // dejaba la tarjeta colgando fuera de la pantalla cuando el sello caía a un
    // palmo del borde, y el pie —que dice cómo cerrarla— era lo que se salía.
    // Los 2 px de más son por el subpíxel: el rect del sello viene fraccionado y
    // sin ellos el borde de abajo se pasaba por décimas.
    alto: Math.floor(arriba ? arribaLibre : abajo) - 2,
    flecha,
    arriba,
  };
}

export function TarjetaSello({
  sello, sku, nombre, pista, etiquetasCuenta = {}, boton, cerrar,
}: {
  sello: SelloFlujo;
  sku: string;
  nombre: string;
  /** La pista de 5 cuadros, ya dibujada. Llega como prop y no importada de
   *  SelloFlujo.tsx: ese módulo importa ÉSTE, y el ciclo lo pagaríamos con un
   *  `undefined` en tiempo de carga. */
  pista: React.ReactNode;
  etiquetasCuenta?: Record<string, string>;
  boton: React.RefObject<HTMLButtonElement>;
  cerrar: (devolverFoco?: boolean) => void;
}) {
  const caja = useRef<HTMLDivElement>(null);
  const cerrarBoton = useRef<HTMLButtonElement>(null);
  const [sitio, setSitio] = useState<Sitio | null>(null);
  const [copiado, setCopiado] = useState(false);
  const { fila, cargando, error, reintentar } = useEvidencia(sku);

  // La posición se recalcula del rect VIVO del sello: la tabla scrollea en los
  // dos ejes y la ventana cambia de tamaño con la tarjeta abierta.
  useEffect(() => {
    const recolocar = () => { if (boton.current) setSitio(sitioDe(boton.current)); };
    recolocar();
    window.addEventListener("resize", recolocar);
    // `capture`: el scroll de un contenedor interno no burbujea hasta window.
    window.addEventListener("scroll", recolocar, true);
    return () => {
      window.removeEventListener("resize", recolocar);
      window.removeEventListener("scroll", recolocar, true);
    };
  }, [boton]);

  // En el portal la tarjeta deja de estar junto al sello en el orden del DOM, y
  // con Tab ya no se entraría en ella. Se le da el foco al abrir — así Tab
  // recorre la tarjeta y Escape devuelve el foco al sello.
  useEffect(() => {
    if (sitio) cerrarBoton.current?.focus({ preventScroll: true });
    // Solo al colocarse la primera vez: recolocar por scroll no re-enfoca.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!sitio]);

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar(); };
    // `pointerdown` y no `click`: la fila abre el cajón del producto al hacer
    // clic, y así la tarjeta ya está cerrada cuando ese clic llega.
    const fuera = (e: PointerEvent) => {
      const t = e.target as Node;
      if (caja.current?.contains(t) || boton.current?.contains(t)) return;
      cerrar(false);
    };
    document.addEventListener("keydown", tecla);
    document.addEventListener("pointerdown", fuera);
    return () => {
      document.removeEventListener("keydown", tecla);
      document.removeEventListener("pointerdown", fuera);
    };
  }, [cerrar, boton]);

  const copiar = useCallback(() => {
    navigator.clipboard?.writeText(sku).then(
      () => { setCopiado(true); window.setTimeout(() => setCopiado(false), 1500); },
      () => { /* sin portapapeles (contexto no seguro): el SKU sigue visible arriba */ },
    );
  }, [sku]);

  if (!sitio || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={caja}
      role="dialog"
      aria-label={`Evidencia del flujo de ${sku}`}
      onClick={(e) => e.stopPropagation()}
      style={{
        left: sitio.x,
        width: sitio.w,
        maxHeight: sitio.alto,
        ...(sitio.arriba ? { bottom: sitio.y } : { top: sitio.y }),
      }}
      /* La flecha sobresale de la caja, así que el recorte (`overflow`) va en la
         caja de adentro y no aquí. */
      className="fixed z-50 flex animate-fade-in cursor-default flex-col"
    >
      <span
        aria-hidden="true"
        style={{ left: sitio.flecha, ...(sitio.arriba ? { bottom: -6 } : { top: -6 }) }}
        className={[
          "absolute z-10 h-[10px] w-[10px] rotate-45 border-slate-200 bg-white",
          sitio.arriba ? "border-b border-r" : "border-l border-t",
        ].join(" ")}
      />

      {/* whitespace-normal / normal-case / tracking-normal deshacen lo que la
          celda o la cabecera de la tabla heredan (misma razón que en Ayuda.tsx). */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden whitespace-normal rounded-2xl border border-slate-200 bg-white text-left font-normal normal-case tracking-normal shadow-xl">
        {/* Solo los tramos scrollean. El pie se queda clavado: con la evidencia
            completa la tarjeta pasa de 700 px y en una fila a media pantalla no
            cabe entera — sin esto, cómo se cierra queda debajo del corte. */}
        <div className="relative flex min-h-0 flex-1 flex-col">
        {/* El degradado avisa de que el tramo sigue abajo. Cuando el contenido
            cabe, se funde blanco sobre blanco y no se ve. */}
        <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-4 bg-gradient-to-t from-white to-transparent" />
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain">
        {/* Cabecera: el mismo sello, en grande, para no perder de vista de qué
            fila salió la tarjeta. */}
        <header className="flex items-start justify-between gap-3 px-4 py-3">
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex items-center gap-2">
              {pista}
              <span className="truncate text-[13px] font-bold leading-[18px] text-slate-800">
                {sello.etapa_texto}
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] leading-4 text-slate-500">
                {sku}
              </span>
              <span className="truncate text-[12px] leading-4 text-slate-500">{nombre}</span>
            </div>
          </div>
          <button
            ref={cerrarBoton}
            type="button"
            onClick={() => cerrar()}
            aria-label="Cerrar la tarjeta del flujo"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            <X size={16} />
          </button>
        </header>

        <TramoRecibido sello={sello} fila={fila} cargando={cargando} error={error} reintentar={reintentar} />
        <TramoBodega sello={sello} fila={fila} cargando={cargando} error={error} />
        <TramoDestino sello={sello} fila={fila} cargando={cargando} etiquetasCuenta={etiquetasCuenta} />
        <TramoFalta sello={sello} fila={fila} />
        </div>
        </div>

        <footer className="flex shrink-0 flex-col gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/inventario"
              title="Inventario abre completo: hoy no acepta un SKU en la dirección, así que hay que buscarlo ahí."
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] font-semibold leading-4 text-slate-600 transition-colors hover:border-indigo-300 hover:text-indigo-600"
            >
              <Warehouse size={14} /> Ver en Inventario
            </Link>
            <button
              type="button"
              onClick={copiar}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] font-semibold leading-4 text-slate-600 transition-colors hover:border-indigo-300 hover:text-indigo-600"
            >
              {copiado ? <Check size={14} className="text-emerald-600" /> : <Copy size={14} />}
              {copiado ? "SKU copiado" : "Copiar SKU"}
            </button>
          </div>
          <p className="flex flex-wrap items-center gap-1.5 text-[10px] leading-[14px] text-slate-400">
            Se cierra con la
            <span className="flex h-[14px] w-[14px] items-center justify-center rounded-[3px] border border-slate-200 bg-white text-slate-500">
              <X size={9} strokeWidth={3} />
            </span>
            , con
            <span className="rounded-[3px] border border-slate-200 bg-white px-1 text-[9px] font-bold leading-[13px] text-slate-500">Esc</span>
            o al hacer clic fuera de la tarjeta.
          </p>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
