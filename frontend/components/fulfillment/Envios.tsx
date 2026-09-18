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
 *   · lo que se pinta como llegada es OBSERVADO por el sync (ML no publica los
 *     envíos a Full por API): jamás se rotula como el conteo del marketplace;
 *   · nunca «en tránsito»: la etapa es «salida validada en Odoo».
 */

import { useEffect, useRef, useState } from "react";
import { Copy, Download, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import {
  ChipCanal, ChipFuente, Ceja, FONDO_RAYADO, Rail, RailLinea, Tarjeta, fecha, num, pasosDe, tasaDe,
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
              <th className="w-[50%] px-3.5 py-2.5">Rail de etapas</th>
              <th className="px-3.5 py-2.5 text-right">Piezas</th>
              <th className="px-3.5 py-2.5 text-right" title="Cuántos SKUs del envío subieron su stock en FULL después de la salida. Observado por el sync cada 15 min: Mercado Libre no publica los envíos a Full por API.">Llegada observada</th>
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
                    {e.cobertura && e.cobertura.llegaron > 0 ? (
                      <>
                        <div className="inline-flex rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700">
                          {e.cobertura.llegaron} de {e.cobertura.skus} SKUs
                        </div>
                        <div className="mt-1 text-[11px] text-slate-400">llegaron a FULL</div>
                      </>
                    ) : (
                      <>
                        <div className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-bold ${tasa.clase}`}
                             style={tasa.rayada ? { background: FONDO_RAYADO } : undefined}>
                          {tasa.texto}
                        </div>
                        <div className="mt-1 text-[11px] text-slate-400">{tasa.nota}</div>
                      </>
                    )}
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
        <span><b>Las dos primeras etapas piden acción, no son un dato faltante</b>: la lista de Andy y el recorte
          de Bodega se capturan en Planeación semanal. Hasta que se capturen no se puede saber cuánto se pidió
          ni cuánto recortó Bodega.</span>
      </div>
    </Tarjeta>
  );
}

/**
 * El detalle de un envío en una VENTANA EMERGENTE sobre la tabla (Brandon,
 * 17-sep): ya no es una pestaña a la que hay que irse y volver. Se cierra con
 * la ✕, con Esc o haciendo clic fuera. Minimalista a propósito: el rail C2
 * arriba, los SKUs en medio y el origen de cada dato, en chico, abajo.
 */
export function DetalleEnvioModal({ envio: base, onCerrar }: { envio: Envio; onCerrar: () => void }) {
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

  // Esc cierra, y la página de atrás no se desplaza mientras la ventana está abierta.
  // `onCerrar` va en una ref: la página lo recrea en cada render y no queremos
  // reenganchar el teclado (ni soltar el bloqueo del scroll) cada vez.
  const cerrar = useRef(onCerrar);
  cerrar.current = onCerrar;
  useEffect(() => {
    const alTeclear = (ev: KeyboardEvent) => { if (ev.key === "Escape") cerrar.current(); };
    window.addEventListener("keydown", alTeclear);
    const antes = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", alTeclear);
      document.body.style.overflow = antes;
    };
  }, []);

  const e = detalle ?? base;
  const lineas = detalle?.lineas ?? [];
  const hecha = e.estado_odoo === "done";
  const almacen = e.canal === "amazon" ? "FBA" : e.canal === "walmart" ? "WFS" : "FULL";
  // Por qué lo que se ve en el almacén es OBSERVADO y no el conteo del canal.
  const porQue = e.canal === "amazon"
    ? "la app de Amazon no tiene permiso de Inbound (403), así que sus envíos no se leen"
    : e.canal === "walmart"
      ? "la API de WFS en México responde, pero falta conectarla al panel"
      : "Mercado Libre no publica los envíos a Full, así que lo declarado, las diferencias y sus motivos sólo viven en su panel";

  const copiar = () => {
    void navigator.clipboard?.writeText(e.envio ?? "sin número de envío");
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1500);
  };
  const sinDato = (titulo: string) => (
    <span title={titulo} className="text-slate-300">—</span>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 px-4 py-10 backdrop-blur-[2px]"
         onClick={onCerrar}>
      <div role="dialog" aria-modal="true" aria-label={`Detalle del envío ${e.envio ?? e.orden ?? ""}`}
           onClick={(ev) => ev.stopPropagation()}
           className="w-full max-w-[1080px] animate-fade-in rounded-2xl bg-white shadow-2xl">
        {/* ── encabezado ─────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <span className={`font-mono text-lg font-extrabold ${e.envio ? "text-slate-900" : "text-amber-700"}`}>
                {e.envio ?? "sin número"}
              </span>
              <ChipCanal canal={e.canal} cuenta={e.cuenta} />
              <span className={`text-sm font-semibold ${e.cuenta || e.canal === "walmart" ? "text-slate-500" : "text-amber-700"}`}
                    title={e.cuenta_regla}>
                {e.cuenta ?? (e.canal === "walmart" ? "Walmart MX" : "sin asignar")}
              </span>
            </div>
            <p className="mt-0.5 text-[12.5px] text-slate-500">
              <span className="font-mono">{e.orden ?? "—"}</span> · <span className="font-mono">{e.salida ?? "—"}</span>
              {" "}· armó {e.kam ?? "—"} ·{" "}
              {hecha ? `${num(e.piezas)} piezas` : `${num(e.pedidas)} pedidas, sin validar`}
              {lineas.length ? ` · ${lineas.length} SKUs` : ""}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button type="button" onClick={copiar} disabled={!e.envio} title="Copiar el número del envío"
                    className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40">
              {copiado ? <span className="text-xs font-semibold text-emerald-600">copiado</span> : <Copy className="h-4 w-4" />}
            </button>
            <button type="button" onClick={onCerrar} title="Cerrar (Esc)"
                    className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* ── el rail C2 ─────────────────────────────────────────── */}
        <div className="px-6 pb-5 pt-6">
          {/* En pantallas angostas el rail se desliza: siete etapas no caben en 390 px. */}
          <div className="overflow-x-auto">
            <div className="min-w-[680px]">
              <RailLinea envio={e} />
            </div>
          </div>
          {e.estado === "sinEnlazar" && (
            <p className="mt-4 text-center text-[11.5px] text-amber-700">
              Sin número de envío en Odoo: no se puede cruzar con el conteo de Mercado Libre, así que la diferencia
              entre declarado y recibido no se puede calcular.
            </p>
          )}
        </div>

        {/* ── los SKUs ───────────────────────────────────────────── */}
        <div className="border-t border-slate-100 px-6 py-4">
          {error ? (
            <p className="text-[12.5px] text-rose-700">No se pudieron leer los renglones: <code className="font-mono">{error}</code></p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse text-[12.5px]">
                <thead>
                  <tr className="text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-400">
                    <th className="pb-2">SKU</th>
                    <th className="pb-2 text-right">Pedidas</th>
                    <th className="pb-2 text-right">Enviadas</th>
                    <th className="pb-2 text-right" title={`Primera subida de stock en ${almacen} tras la salida (sync cada 15 min).`}>Llegó a {almacen}</th>
                    <th className="pb-2 text-right" title="Pueden incluir piezas de otro envío del mismo SKU.">Entraron</th>
                    <th className="pb-2 text-right">Activo</th>
                    <th className="pb-2 text-right">1ª venta</th>
                  </tr>
                </thead>
                <tbody>
                  {lineas.map((l) => (
                    <tr key={l.sku} className="border-t border-slate-100">
                      <td className="py-2 pr-3">
                        <div className="font-mono font-bold text-slate-800">{l.sku}</div>
                        <div className="max-w-[360px] truncate text-[11px] text-slate-400" title={l.nombre}>{l.nombre}</div>
                      </td>
                      <td className="py-2 text-right font-mono tabular-nums text-slate-500">{num(l.pedidas)}</td>
                      <td className="py-2 text-right font-mono font-bold tabular-nums text-slate-800">
                        {l.enviadas === null ? <span className="font-normal text-amber-700">sin validar</span> : num(l.enviadas)}
                      </td>
                      <td className="py-2 text-right font-mono text-[11.5px] text-emerald-700">
                        {l.llegada ? fecha({ ts: l.llegada, aprox: true }) : sinDato(`Sin movimiento de stock ${almacen} tras la salida.`)}
                      </td>
                      <td className="py-2 text-right font-mono tabular-nums text-slate-500">
                        {l.piezas_llegadas ? num(l.piezas_llegadas) : sinDato("—")}
                      </td>
                      <td className="py-2 text-right font-mono text-[11.5px] text-slate-500">
                        {l.activacion ? fecha({ ts: l.activacion, aprox: true }) : sinDato(`No se prendió en ${almacen} en la ventana.`)}
                      </td>
                      <td className="py-2 text-right font-mono text-[11.5px] text-slate-500">
                        {l.primera_venta
                          ? fecha({ ts: `${l.primera_venta}T12:00:00-06:00`, dia: true })
                          : sinDato(`Sin ventas ${almacen} de este SKU tras la salida.`)}
                      </td>
                    </tr>
                  ))}
                  {!detalle && (
                    <tr><td colSpan={7} className="py-6 text-center text-slate-400">Leyendo los renglones en Odoo…</td></tr>
                  )}
                  {detalle && lineas.length === 0 && (
                    <tr><td colSpan={7} className="py-6 text-center text-slate-400">Esta salida no tiene renglones en Odoo.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── de dónde sale, en chico ────────────────────────────── */}
        <div className="rounded-b-2xl border-t border-slate-100 bg-slate-50/60 px-6 py-3">
          <dl className="grid gap-x-6 gap-y-1 text-[11.5px] sm:grid-cols-2 lg:grid-cols-3">
            <Dato t="Orden de venta" v={`${e.orden ?? "—"} · ${e.kam ?? "—"} · ${fecha(e.etapas[0])}`} />
            <Dato t="Salida" v={`${e.salida ?? "—"} · ${e.estado_odoo ?? "—"}${hecha ? ` · ${fecha(e.etapas[1])}` : ""}`} />
            <Dato t="Socio" v={`«${e.socio ?? "—"}»`} />
            <Dato t="Referencia en Odoo" v={e.referencia ? `«${e.referencia}»` : "vacía"} />
            <Dato t="Número de envío" v={e.envio ? `${e.envio} (de ${e.envio_origen === "socio" ? "el socio" : "la referencia"})` : "no hay"} />
            <Dato t="Cuenta" v={e.cuenta_regla ?? "—"} />
          </dl>
          <p className="mt-2 text-[10.5px] text-slate-400">
            Llegó, activo y 1ª venta son observados por nuestro sync (~ = hora en que se vio): {porQue}.
          </p>
        </div>
      </div>
    </div>
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
