"use client";

/**
 * FULLFILMENT · Envíos (un renglón por salida de Odoo con su rail de 7 etapas) y
 * el Detalle de un envío (por SKU, con de dónde sale cada dato).
 *
 * Desde v0.523.0 salen de Odoo en vivo: `GET /api/fulfillment/envios` y
 * `GET /api/fulfillment/envios/{id}` (backend/services/fulfillment_envios.py).
 *
 * Reglas que esta vista NO rompe:
 *   · cada renglón enseña su orden de venta, su salida y POR QUÉ tiene esa
 *     cuenta: la regla depende de quién creó la orden y tiene que verse;
 *   · «envío sin enlazar» es un estado de primera: ni 0 ni escondido;
 *   · recibidas y rechazadas van en «sin dato» hasta que exista la lectura; la
 *     segunda jamás se deriva restando;
 *   · nunca «en tránsito»: la etapa es «salida validada en Odoo».
 */

import { useEffect, useState } from "react";
import { ArrowLeft, Copy, Download } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import {
  ChipCanal, ChipFuente, Ceja, FONDO_RAYADO, Rail, Tarjeta, fecha, num, pasosDe, tasaDe,
} from "./ui";
import type { Envio, EnvioConLineas } from "./tipos";

const POR_PAGINA = 40;

export function TablaEnvios({
  envios, total, onAbrir,
}: { envios: Envio[]; total: number; onAbrir: (e: Envio) => void }) {
  const [visibles, setVisibles] = useState(POR_PAGINA);
  useEffect(() => setVisibles(POR_PAGINA), [envios]);
  const pagina = envios.slice(0, visibles);

  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Ceja>Un renglón por salida de Odoo · {num(envios.length)} de {num(total)} envíos</Ceja>
            <ChipFuente vivo />
          </div>
          <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
            Envíos a los almacenes del marketplace
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400">Cada etapa lleva su fecha o su estado. Ninguna se deduce de otra.</span>
          <button type="button" disabled
                  title="Pendiente: el CSV sale cuando la historia de recepciones tenga al menos una semana."
                  className="flex cursor-not-allowed items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-300">
            <Download className="h-4 w-4" /> Exportar
          </button>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[1240px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">Envío · orden · salida</th>
              <th className="px-3.5 py-2.5">Canal · cuenta</th>
              <th className="w-[44%] px-3.5 py-2.5">Rail de etapas</th>
              <th className="px-3.5 py-2.5 text-right">Piezas</th>
              <th className="px-3.5 py-2.5 text-right">Tasa de recepción</th>
              <th className="px-3.5 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {pagina.map((e, i) => {
              const tasa = tasaDe(e);
              return (
                <tr key={e.id ?? `${e.orden}-${i}`} className="border-t border-slate-100 align-top">
                  <td className="px-3.5 py-[11px]">
                    <div className={`font-mono text-[13px] font-bold ${e.envio ? "text-slate-900" : "text-amber-700"}`}
                         title={e.referencia ? `Referencia tecleada en Odoo: «${e.referencia}»` : "La orden no trae referencia"}>
                      {e.envio ?? "sin número"}
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-500">
                      {e.orden ?? "—"} · {e.salida ?? "—"}
                    </div>
                    <div className="text-[11px] text-slate-400">armó {e.kam ?? "—"}</div>
                    {e.estado === "sinEnlazar" && (
                      <div className="mt-1.5 inline-flex rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[.04em] text-amber-700">
                        envío sin enlazar
                      </div>
                    )}
                  </td>
                  <td className="px-3.5 py-[11px]">
                    <ChipCanal canal={e.canal} cuenta={e.cuenta} />
                    <div className={`mt-1 text-xs font-semibold ${e.cuenta || e.canal === "walmart" ? "text-slate-600" : "text-amber-700"}`}
                         title={e.cuenta_regla}>
                      {e.cuenta ?? (e.canal === "walmart" ? "Walmart MX" : "sin asignar")}
                    </div>
                    {e.cuenta_regla && (
                      <div className="mt-0.5 max-w-[190px] text-[10.5px] leading-tight text-slate-400">{e.cuenta_regla}</div>
                    )}
                  </td>
                  <td className="px-3.5 py-[11px] align-middle">
                    <Rail pasos={pasosDe(e)} />
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <div className="font-mono text-sm font-extrabold tabular-nums text-slate-900">
                      {e.piezas === null ? <span className="text-amber-700">abierta</span> : num(e.piezas)}
                    </div>
                    <div className="text-[11px] text-slate-400">
                      de {num(e.pedidas)} pedidas{e.n_skus ? ` · ${e.n_skus} SKUs` : ""}
                    </div>
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <div className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-bold ${tasa.clase}`}
                         style={tasa.rayada ? { background: FONDO_RAYADO } : undefined}>
                      {tasa.texto}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-400">{tasa.nota}</div>
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    <button type="button" onClick={() => onAbrir(e)}
                            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50">
                      Detalle
                    </button>
                  </td>
                </tr>
              );
            })}
            {envios.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-sm text-slate-500">
                  Ningún envío con estos filtros. No es un error de lectura: Odoo contestó y no hay salidas de ese canal o cuenta.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {visibles < envios.length && (
        <div className="mt-3 text-center">
          <button type="button" onClick={() => setVisibles((v) => v + POR_PAGINA)}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Mostrar {Math.min(POR_PAGINA, envios.length - visibles)} más · van {visibles} de {envios.length}
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-slate-400">
        <span><b>Salida validada</b> es cuando bodega cierra la salida en Odoo, no la hora del camión: en 16 de 43 envíos medidos ML ya había recibido antes.</span>
        <span>No decimos «en tránsito» hasta que el marketplace confirme que la mercancía viaja.</span>
      </div>
    </Tarjeta>
  );
}

export function DetalleEnvio({ envio: base, onVolver }: { envio: Envio; onVolver: () => void }) {
  const [copiado, setCopiado] = useState(false);
  const [detalle, setDetalle] = useState<EnvioConLineas | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (base.id === undefined) return;
    let vivo = true;
    setDetalle(null);
    setError(null);
    fetchSesion(`${API_BASE}/api/fulfillment/envios/${base.id}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        return r.json() as Promise<EnvioConLineas>;
      })
      .then((d) => { if (vivo) setDetalle(d); })
      .catch((e: unknown) => { if (vivo) setError(e instanceof Error ? e.message : String(e)); });
    return () => { vivo = false; };
  }, [base.id]);

  const e = detalle ?? base;
  const lineas = detalle?.lineas ?? [];
  const hecha = e.estado_odoo === "done";

  const copiar = () => {
    void navigator.clipboard?.writeText(e.envio ?? "sin número de envío");
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  };

  const sinDato = (titulo: string) => (
    <span title={titulo} className="font-mono text-[11px] font-bold text-slate-400">sin dato</span>
  );
  const sReg = (
    <span className="rounded border border-dashed border-slate-300 px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase text-slate-400"
          style={{ background: FONDO_RAYADO }}
          title="La lista de Andy y la validación de Bodega todavía no se guardan en ningún sistema.">s/reg</span>
  );

  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><Ceja>Detalle del envío</Ceja><ChipFuente vivo /></div>
          <h2 className="mt-1 flex flex-wrap items-center gap-2.5 text-[20px] font-extrabold tracking-tight text-slate-900">
            <span className={`font-mono ${e.envio ? "" : "text-amber-700"}`}>{e.envio ?? "sin número"}</span>
            <ChipCanal canal={e.canal} cuenta={e.cuenta} />
            <span className={`text-sm font-semibold ${e.cuenta || e.canal === "walmart" ? "text-slate-500" : "text-amber-700"}`}>
              {e.cuenta ?? (e.canal === "walmart" ? "Walmart MX" : "sin asignar")}
            </span>
          </h2>
          <p className="mt-1 text-[13px] text-slate-500">
            Orden de venta <b className="font-mono text-slate-700">{e.orden ?? "—"}</b> · salida{" "}
            <b className="font-mono text-slate-700">{e.salida ?? "—"}</b> · armó{" "}
            <b className="text-slate-700">{e.kam ?? "—"}</b> ·{" "}
            {hecha ? `${num(e.piezas)} piezas` : `${num(e.pedidas)} piezas pedidas, sin validar`}
            {lineas.length ? ` en ${lineas.length} SKUs` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onVolver}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <ArrowLeft className="h-4 w-4" /> Volver a Envíos
          </button>
          <button type="button" onClick={copiar} disabled={!e.envio}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-600 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:text-slate-300">
            <Copy className="h-4 w-4" /> {copiado ? "Copiado" : "Copiar número"}
          </button>
        </div>
      </div>

      <div className="mt-4">
        <Rail pasos={pasosDe(e)} grande />
      </div>

      {/* De dónde sale cada dato de este renglón (pedido de Brandon, 15-sep). */}
      <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50/70 px-4 py-3">
        <div className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">De dónde sale este envío</div>
        <dl className="mt-2 grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2 xl:grid-cols-3">
          <Dato t="Orden de venta (Odoo sale.order)" v={`${e.orden ?? "—"} · creada por ${e.kam ?? "—"} · ${fecha(e.etapas[2])}`} />
          <Dato t="Salida (Odoo stock.picking)" v={`${e.salida ?? "—"} · ${e.almacen ?? "—"} · estado ${e.estado_odoo ?? "—"}${hecha ? ` · validada ${fecha(e.etapas[3])}` : ""}`} />
          <Dato t="Socio de la orden" v={`«${e.socio ?? "—"}» → ${e.canal === "meli" ? "ML FULL" : e.canal === "amazon" ? "Amazon FBA (≥ 40 piezas)" : "Walmart WFS"}`} />
          <Dato t="Referencia tecleada por la KAM" v={e.referencia ? `«${e.referencia}»` : "vacía"} />
          <Dato t="Número de envío" v={e.envio ? `${e.envio} · sacado de ${e.envio_origen === "socio" ? "el nombre del socio" : "la referencia"}` : "no hay: sin número no se puede cruzar con el marketplace"} />
          <Dato t="Cuenta" v={e.cuenta_regla ?? "—"} />
        </dl>
      </div>

      {e.estado === "sinEnlazar" && (
        <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-amber-900">
            <b>Envío sin enlazar.</b> La referencia de la orden en Odoo no trae número de envío de Mercado Libre,
            así que no hay con qué cruzar lo que salió contra lo que el almacén recibió. Esta orden{" "}
            <b>no puede tener tasa de recepción</b> — y eso no es un cero.
          </p>
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3 text-[12.5px] text-rose-800">
          No se pudieron leer los renglones de esta salida. Error del backend: <code className="font-mono">{error}</code>
        </div>
      )}

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[900px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">SKU · producto</th>
              <th className="px-3.5 py-2.5 text-right">Solicit.</th>
              <th className="px-3.5 py-2.5 text-right">Valid.</th>
              <th className="px-3.5 py-2.5 text-right" title="product_qty de los movimientos de la salida">Pedidas en la orden</th>
              <th className="px-3.5 py-2.5 text-right" title="quantity hecha al validar la salida">Enviadas</th>
              <th className="px-3.5 py-2.5 text-right text-emerald-700">Recibidas</th>
              <th className="px-3.5 py-2.5 text-right text-rose-800">Rechazadas</th>
            </tr>
          </thead>
          <tbody>
            {lineas.map((l) => (
              <tr key={l.sku} className="border-t border-slate-100">
                <td className="px-3.5 py-[9px]">
                  <div className="font-mono text-[12.5px] font-bold text-slate-900">{l.sku}</div>
                  <div className="max-w-[420px] truncate text-[11.5px] text-slate-400" title={l.nombre}>{l.nombre}</div>
                </td>
                <td className="px-3.5 py-[9px] text-right">{sReg}</td>
                <td className="px-3.5 py-[9px] text-right">{sReg}</td>
                <td className="px-3.5 py-[9px] text-right font-mono tabular-nums text-slate-600">{num(l.pedidas)}</td>
                <td className="px-3.5 py-[9px] text-right font-mono font-bold tabular-nums text-slate-900">
                  {l.enviadas === null ? <span className="text-amber-700">sin validar</span> : num(l.enviadas)}
                </td>
                <td className="px-3.5 py-[9px] text-right">{sinDato("Las recepciones del marketplace todavía no se leen.")}</td>
                <td className="px-3.5 py-[9px] text-right">{sinDato("Solo se llena con la cifra explícita del marketplace; nunca restando.")}</td>
              </tr>
            ))}
            {!detalle && !error && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-slate-400">Leyendo los renglones en Odoo…</td></tr>
            )}
          </tbody>
          {lineas.length > 0 && (
            <tfoot>
              <tr className="border-t-2 border-slate-200 bg-slate-50 text-[12.5px] font-bold">
                <td colSpan={3} className="px-3.5 py-2.5 text-slate-600">Total de la salida</td>
                <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-slate-600">{num(e.pedidas)}</td>
                <td className="px-3.5 py-2.5 text-right font-mono tabular-nums text-slate-900">{hecha ? num(e.piezas) : "—"}</td>
                <td className="px-3.5 py-2.5 text-right">{sinDato("Sin lectura de recepciones.")}</td>
                <td className="px-3.5 py-2.5 text-right">{sinDato("Sin lectura de recepciones.")}</td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-amber-900">
            <b>Recibidas y rechazadas todavía no se leen.</b> Cuando se lean, lo que el almacén no ha contado va
            en <b>en recepción</b>, y solo pasa a <b>rechazadas</b> cuando el marketplace lo dice como cantidad
            explícita — nunca restando enviadas menos recibidas.
          </p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3">
          <p className="text-[12.5px] leading-relaxed text-slate-600">
            <b>Sin cajas.</b> Odoo no registra cajas ni usa paquetes, y el factor de caja por SKU aún no se cruza
            aquí. Las cajas del packing list son cartones de <b>importación</b>, no de envío a FULL.
          </p>
        </div>
      </div>
    </Tarjeta>
  );
}

function Dato({ t, v }: { t: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10.5px] font-semibold text-slate-400">{t}</dt>
      <dd className="truncate text-slate-700" title={v}>{v}</dd>
    </div>
  );
}
