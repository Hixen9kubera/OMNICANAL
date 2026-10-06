"use client";

/**
 * La ventana «Ventas de marketplace»: las ventas DROP recientes que todavía no
 * tienen orden propia, y un buscador por id de venta. De aquí nace una orden
 * con el PRECIO REAL de la venta (total y comisión) en vez de teclearlo.
 *
 * Por qué cada chip manda sobre el botón:
 *   · «Ya tiene OV-000xx»: una venta = UNA orden viva (`ov_ordenes_mp_uq`). En
 *     vez de dejar crear la segunda y que truene el índice, se abre la que hay.
 *   · «FULL»: sale del almacén del marketplace (FULL / FBA / WFS); bodega no la
 *     surte, así que NO lleva orden propia y el botón ni aparece.
 *   · «CANCELADA»: se puede documentar, pero el barrido la cancelaría en la
 *     siguiente pasada; el botón lo avisa antes de dar el clic.
 *   · «N sin SKU»: la venta trae renglones SIN SKU. Cuentan en sus piezas pero
 *     no pueden entrar a la orden (no hay qué surtir): se dice ANTES de crearla,
 *     porque la orden nacería con menos piezas que la venta.
 *
 * Lo que la venta NO trae y la orden SÍ necesita: la BODEGA. La orden propia
 * sólo vive en bodegas de kubera y la bodega va por renglón; la venta no sabe
 * de cuál sale. Por eso de aquí nace siempre un BORRADOR: sus renglones entran
 * sin bodega (o con la única que haya) y se confirma después, ya elegida.
 *
 * La CUENTA sí la trae, y ahora importa: la llave de la venta en la orden es
 * canal + cuenta + id, los tres o ninguno (`ov_ordenes_mp_chk`). Una venta sin
 * cuenta no se puede ligar; se dice en su fila y el documento pedirá elegirla.
 *
 * `FilaVenta` se exporta porque el documento de la orden pinta EXACTAMENTE la
 * misma fila cuando «Traer venta» contesta varias: una venta se lee igual en
 * los dos sitios.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Loader2, RefreshCw, Search, ShoppingBag, X } from "lucide-react";
import { mensajeDeError } from "@/lib/api";
import { BotonCerrar, Ventana } from "@/components/fulfillment/ui";
import { buscarVenta, ventasPendientes } from "./api";
import {
  Aviso, Boton, CANALES, CLASE_CAMPO, dinero, fechaHora, num, rotuloCanal, rotuloCuenta,
} from "./ui";
import type { VentaMarketplace as Venta } from "./tipos";

const VENTANAS = [7, 14, 30] as const;
type Dias = (typeof VENTANAS)[number];

/** Canal y, sólo si ese canal tiene más de una cuenta, cuál («Mercado Libre · San Corpe»). */
export function origenDeVenta(v: Pick<Venta, "canal" | "cuenta">): string {
  const varias = (CANALES.find((c) => c.id === v.canal)?.cuentas.length ?? 0) > 1;
  return varias && v.cuenta ? `${rotuloCanal(v.canal)} · ${rotuloCuenta(v.cuenta)}` : rotuloCanal(v.canal);
}

/**
 * ¿A esta venta le falta la cuenta y no hay cómo deducirla? Con el id van
 * canal y cuenta (los tres o ninguno): donde el canal sólo tiene una cuenta,
 * es ésa y no falta nada; donde tiene varias (Mercado Libre) hay que elegirla.
 */
export function faltaCuenta(v: Pick<Venta, "canal" | "cuenta">): boolean {
  if (v.cuenta?.trim()) return false;
  return (CANALES.find((c) => c.id === v.canal)?.cuentas.length ?? 0) !== 1;
}

const CHIP = "inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[10.5px] font-bold ring-1";

function Dato({ rotulo, children, titulo }: { rotulo: string; children: ReactNode; titulo?: string }) {
  return (
    <div className="min-w-0" title={titulo}>
      <dt className="text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">{rotulo}</dt>
      <dd className="mt-0.5 truncate text-[13px] text-slate-700">{children}</dd>
    </div>
  );
}

/**
 * Una venta en un renglón: origen, id, qué trae, cuándo, cuánto y su guía.
 * `children` es la acción de la derecha (la decide quien la pinta).
 */
export function FilaVenta({ venta, onAbrirOrden, children }: {
  venta: Venta;
  /** Si se da, el chip «Ya tiene OV-000xx» abre esa orden. */
  onAbrirOrden?: (folio: string) => void;
  children?: ReactNode;
}) {
  const skus = venta.lineas.slice(0, 2).map((l) => `${l.sku} ×${l.cantidad}`).join(" · ");
  const mas = venta.lineas.length - 2;
  const ov = venta.ov;
  const sinSku = venta.renglones_sin_sku ?? 0;
  const sinCuenta = faltaCuenta(venta);
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1 basis-56">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-500">
            {origenDeVenta(venta)}
          </span>
          {venta.cancelada && (
            <span className={`${CHIP} bg-rose-50 text-rose-700 ring-rose-200`}
                  title="El marketplace canceló esta venta">CANCELADA</span>
          )}
          {venta.es_fulfillment && (
            <span className={`${CHIP} bg-slate-100 text-slate-600 ring-slate-200`}
                  title="Sale del almacén del marketplace (FULL / FBA / WFS): no lleva orden propia">FULL</span>
          )}
          {sinSku > 0 && (
            <span className={`${CHIP} bg-amber-50 text-amber-800 ring-amber-200`}
                  title={`La venta trae ${sinSku} ${sinSku === 1 ? "renglón" : "renglones"} sin SKU que no se ${sinSku === 1 ? "puede" : "pueden"} traer a la orden: ${sinSku === 1 ? "agrégalo" : "agrégalos"} a mano.`}>
              {num(sinSku)} sin SKU
            </span>
          )}
          {sinCuenta && (
            <span className={`${CHIP} bg-amber-50 text-amber-800 ring-amber-200`}
                  title="El panel no registró de qué cuenta es esta venta. En la orden, canal, cuenta e id van juntos: habrá que elegir la cuenta para poder guardarla.">
              sin cuenta
            </span>
          )}
          {ov && (onAbrirOrden ? (
            <button type="button" onClick={() => onAbrirOrden(ov.folio)}
                    title={`Abrir ${ov.folio}`}
                    className={`${CHIP} bg-indigo-50 text-indigo-700 ring-indigo-200 hover:bg-indigo-100`}>
              Ya tiene {ov.folio}
            </button>
          ) : (
            <span className={`${CHIP} bg-indigo-50 text-indigo-700 ring-indigo-200`}>Ya tiene {ov.folio}</span>
          ))}
        </div>
        <div className="mt-0.5 truncate font-mono text-[13px] font-bold text-slate-900" title={venta.orden}>
          {venta.orden}
        </div>
        <div className="mt-0.5 truncate font-mono text-[11.5px] text-slate-500">
          {skus || <span className="font-sans text-slate-400">{sinSku > 0 ? "sin renglones con SKU" : "sin renglones"}</span>}
          {mas > 0 && <span className="font-sans text-slate-400"> · +{mas} más</span>}
        </div>
        {sinSku > 0 && (
          <div className="mt-0.5 text-[11.5px] font-semibold leading-snug text-amber-700">
            Trae {num(sinSku)} {sinSku === 1 ? "renglón" : "renglones"} sin SKU que no se{" "}
            {sinSku === 1 ? "puede" : "pueden"} traer: {sinSku === 1 ? "agrégalo" : "agrégalos"} a mano.
          </div>
        )}
      </div>
      <dl className="grid w-full grid-cols-2 gap-x-4 gap-y-2 sm:w-auto sm:shrink-0 sm:grid-cols-[92px_56px_96px_132px]">
        <Dato rotulo="Fecha">{fechaHora(venta.fecha, "sin fecha")}</Dato>
        <Dato rotulo="Piezas"><span className="tabular-nums">{num(venta.piezas)}</span></Dato>
        <Dato rotulo="Total" titulo={venta.comision === null ? undefined : `Comisión ${dinero(venta.comision)}`}>
          <span className="font-semibold tabular-nums text-slate-900">{dinero(venta.total, "MXN", "sin dato")}</span>
        </Dato>
        <Dato rotulo="Guía" titulo={[venta.guia, venta.paqueteria].filter(Boolean).join(" · ") || undefined}>
          {venta.guia
            ? <span className="font-mono text-[12px]">{venta.guia}</span>
            : <span className="text-slate-400">sin guía</span>}
        </Dato>
      </dl>
      {children ? <div className="ml-auto flex shrink-0 justify-end sm:min-w-[132px]">{children}</div> : null}
    </div>
  );
}

export function VentasMarketplace({ onCerrar, onElegir, onAbrirOrden }: {
  onCerrar: () => void;
  onElegir: (v: Venta) => void;
  onAbrirOrden: (folio: string) => void;
}): JSX.Element {
  const [dias, setDias] = useState<Dias>(7);
  const [canal, setCanal] = useState("");
  const [texto, setTexto] = useState("");
  /** El id que se está buscando. Vacío = se listan las pendientes. */
  const [consulta, setConsulta] = useState("");
  const [vuelta, setVuelta] = useState(0);
  const [ventas, setVentas] = useState<Venta[] | null>(null);
  const [motivo, setMotivo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);

  // Una lectura a la vez: cambiar de canal o de ventana cancela la anterior,
  // para que una respuesta lenta no pinte encima de la que sí se pidió.
  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    const pedir = consulta
      ? buscarVenta(consulta, canal || undefined, ctrl.signal)
      : ventasPendientes(dias, canal || undefined, ctrl.signal);
    pedir
      .then((r) => {
        setVentas(r.ventas ?? []);
        setMotivo(r.ok ? null : (r.motivo || "El servidor no pudo leer las ventas."));
        setCargando(false);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setVentas(null);
        setMotivo(null);
        setError(mensajeDeError(e, "No se pudieron leer las ventas (el servidor no contestó)."));
        setCargando(false);
      });
    return () => ctrl.abort();
  }, [consulta, canal, dias, vuelta]);

  const buscar = () => {
    const id = texto.trim();
    if (id === consulta) setVuelta((v) => v + 1);
    else setConsulta(id);
  };
  const limpiar = () => { setTexto(""); setConsulta(""); };

  const pastilla = (on: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-bold transition ${
      on ? "bg-indigo-600 text-white" : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"}`;

  return (
    <Ventana onCerrar={onCerrar} etiqueta="Ventas de marketplace" ancho="max-w-4xl">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-base font-extrabold tracking-tight text-slate-900">
            <ShoppingBag className="h-4 w-4 text-indigo-600" /> Ventas de marketplace
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Ventas DROP que todavía no tienen orden propia. La orden nace como borrador, con el precio real
            de la venta; la bodega de cada renglón se elige en la orden.
          </p>
        </div>
        <BotonCerrar onClick={onCerrar} />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-5 py-3">
        <form className="flex min-w-[240px] flex-1 items-center gap-2"
              onSubmit={(ev) => { ev.preventDefault(); buscar(); }}>
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input type="text" value={texto} onChange={(ev) => setTexto(ev.target.value)}
                   placeholder="Buscar por id de la venta (PO-…, 2000…)"
                   aria-label="Id de la venta en el marketplace" spellCheck={false} autoComplete="off"
                   className={`${CLASE_CAMPO} pl-9 font-mono`} />
          </div>
          <Boton tipo="submit" tono="secundario" deshabilitado={!texto.trim()} porque="Escribe el id de la venta">
            Buscar
          </Boton>
        </form>
        <select value={canal} onChange={(ev) => setCanal(ev.target.value)} aria-label="Canal"
                className={`${CLASE_CAMPO} !w-auto`}>
          <option value="">Todos los canales</option>
          {CANALES.filter((c) => c.mp).map((c) => <option key={c.id} value={c.id}>{c.rotulo}</option>)}
        </select>
        <div className={`flex items-center gap-0.5 rounded-lg bg-white p-0.5 ring-1 ring-slate-200 ${consulta ? "opacity-40" : ""}`}
             title={consulta ? "La búsqueda por id no depende de la ventana de días" : "Ventas de los últimos días"}>
          {VENTANAS.map((d) => (
            <button key={d} type="button" disabled={!!consulta} onClick={() => setDias(d)}
                    aria-pressed={!consulta && dias === d} className={pastilla(!consulta && dias === d)}>
              {d} d
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setVuelta((v) => v + 1)} disabled={cargando} title="Volver a leer"
                className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex items-center justify-between gap-3 px-5 pt-3 text-xs text-slate-500">
        <span>
          {consulta
            ? <>Resultado de <span className="font-mono font-semibold text-slate-700">{consulta}</span></>
            : <>Sin orden propia · últimos <b className="text-slate-700">{dias}</b> días</>}
          {ventas && !cargando ? <> · <b className="text-slate-700">{num(ventas.length)}</b> {ventas.length === 1 ? "venta" : "ventas"}</> : null}
        </span>
        {consulta && (
          <button type="button" onClick={limpiar}
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-1 font-semibold text-indigo-600 hover:bg-indigo-50">
            <X className="h-3.5 w-3.5" /> Quitar la búsqueda
          </button>
        )}
      </div>

      <div className="px-5 pb-5 pt-2">
        {motivo && <div className="mb-2"><Aviso tono="ambar">{motivo}</Aviso></div>}
        {error ? (
          <div className="rounded-xl border border-slate-200 px-4 py-10 text-center">
            <p className="text-sm font-semibold text-slate-700">No se pudieron leer las ventas</p>
            <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">{error}</p>
            <div className="mt-3 flex justify-center">
              <Boton icono={RefreshCw} tono="primario" onClick={() => setVuelta((v) => v + 1)}>Reintentar</Boton>
            </div>
          </div>
        ) : !ventas ? (
          <div className="rounded-xl border border-slate-200 px-4 py-12 text-center text-slate-400">
            <Loader2 className="mx-auto h-5 w-5 animate-spin" />
            <p className="mt-2 text-xs">Leyendo las ventas…</p>
          </div>
        ) : !ventas.length ? (
          <div className="rounded-xl border border-dashed border-slate-200 px-4 py-12 text-center">
            <ShoppingBag className="mx-auto h-7 w-7 text-slate-300" />
            <p className="mt-2 text-sm font-semibold text-slate-700">
              {consulta ? "No se encontró esa venta" : "No hay ventas pendientes de orden"}
            </p>
            <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">
              {consulta
                ? <>Nada con el id <span className="font-mono">{consulta}</span>{canal ? ` en ${rotuloCanal(canal)}` : ""}. Revisa el id o quita el filtro de canal.</>
                : <>Todas las ventas DROP de los últimos {dias} días{canal ? ` de ${rotuloCanal(canal)}` : ""} ya tienen su orden.</>}
            </p>
          </div>
        ) : (
          <div className={`max-h-[58vh] divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200 transition-opacity ${
            cargando ? "opacity-50" : ""}`}>
            {ventas.map((v) => {
              const folio = v.ov?.folio;
              return (
              <FilaVenta key={`${v.canal}|${v.cuenta}|${v.orden}`} venta={v} onAbrirOrden={onAbrirOrden}>
                {folio ? (
                  <Boton chico tono="secundario" onClick={() => onAbrirOrden(folio)}>Abrir {folio}</Boton>
                ) : v.es_fulfillment ? (
                  <span className="text-right text-[11.5px] leading-snug text-slate-400">no lleva<br />orden propia</span>
                ) : (
                  <Boton chico tono={v.cancelada ? "secundario" : "primario"} onClick={() => onElegir(v)}>
                    {v.cancelada ? "Crear de todos modos" : "Crear orden"}
                  </Boton>
                )}
              </FilaVenta>
              );
            })}
          </div>
        )}
        <p className="mt-3 text-[11.5px] leading-relaxed text-slate-400">
          Sólo lectura de las ventas que ya registró el panel: aquí no se consulta ni se escribe en ningún
          marketplace. Las ventas FULL salen del almacén del marketplace y no llevan orden propia.
        </p>
      </div>
    </Ventana>
  );
}
