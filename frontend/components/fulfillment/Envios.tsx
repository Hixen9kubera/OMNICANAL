"use client";

/**
 * FULLFILMENT · Envíos (un renglón por salida de Odoo con su rail de 7 etapas) y
 * el Detalle de un envío (por SKU, con de dónde sale cada dato).
 *
 * Salen de Odoo en vivo: `GET /api/fulfillment/envios` y
 * `GET /api/fulfillment/envios/{id}` (backend/services/fulfillment_envios.py).
 * Es la SEGUNDA pantalla de la pestaña (Brandon, 24-sep-2026): "después Envíos,
 * para checar el status de mis envíos". Por eso arriba van los estados como
 * filtros, con cuántos hay en cada uno.
 *
 * Reglas que esta vista NO rompe:
 *   · cada renglón enseña su orden de venta, su salida y POR QUÉ tiene esa
 *     cuenta: la regla depende de quién creó la orden y tiene que verse;
 *   · «envío sin enlazar» es un estado de primera: ni 0 ni escondido;
 *   · la llegada de ML sale de los AVISOS de FULL (fbm_stock_operations): la
 *     suma por SKU contra lo enviado; con el envío cerrado, lo que falta es lo
 *     que ML no recibió (Brandon, 18-sep). FBA sigue con lo OBSERVADO por el sync;
 *   · nunca «en tránsito»: la etapa es «salida validada en Odoo».
 */

import { useEffect, useMemo, useState } from "react";
import { Copy, Search } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import FichaSku from "./FichaSku";
import {
  BotonCerrar, Ceja, ChipCanal, ChipFuente, FONDO_RAYADO, Rail, RailLinea, Tarjeta, Ventana, fecha, num, pasosDe, tasaDe,
} from "./ui";
import type { Envio, EnvioConLineas, LineaOdoo } from "./tipos";

const POR_PAGINA = 40;

/** El estado de un envío como lo pregunta quien lo sigue: ¿ya salió? ¿llegó? ¿llegó todo? */
export type EstadoSeguimiento = "por_validar" | "llegando" | "completo" | "no_recibio" | "sin_medir";

export function seguimientoDe(e: Envio): EstadoSeguimiento {
  if (e.estado_odoo !== undefined && e.estado_odoo !== "done") return "por_validar";
  const c = e.cobertura;
  if (c?.fuente !== "avisos") return "sin_medir";
  if (c.rechazadas) return "no_recibio";
  if (c.cerrado || (c.skus > 0 && c.completos === c.skus)) return "completo";
  return "llegando";
}

const ESTADOS: { k: EstadoSeguimiento | "todos"; t: string; titulo: string; punto: string }[] = [
  { k: "todos", t: "Todos", titulo: "Todas las salidas del filtro de canal y cuenta.", punto: "#cbd5e1" },
  { k: "por_validar", t: "Por validar", titulo: "La salida existe en Odoo y bodega no la ha validado.", punto: "#f59e0b" },
  { k: "llegando", t: "Llegando a FULL", titulo: "Salió y ML todavía no avisa todo lo enviado (el envío cierra 10 días después de la salida).", punto: "#fbbf24" },
  { k: "completo", t: "Completos", titulo: "ML avisó todo lo enviado.", punto: "#059669" },
  { k: "no_recibio", t: "ML no recibió", titulo: "Cerrados (10 días) con piezas que ML nunca avisó.", punto: "#e11d48" },
  { k: "sin_medir", t: "Sin medir", titulo: "FBA, WFS, salidas sin cuenta o anteriores al 12-ago: no hay avisos con qué medir la llegada.", punto: "#94a3b8" },
];

export function TablaEnvios({
  envios, total, onAbrir,
}: { envios: Envio[]; total: number; onAbrir: (e: Envio) => void }) {
  const [estado, setEstado] = useState<EstadoSeguimiento | "todos">("todos");
  const [busca, setBusca] = useState("");
  const [visibles, setVisibles] = useState(POR_PAGINA);

  const conteos = useMemo(() => {
    const c: Record<string, number> = { todos: envios.length };
    for (const e of envios) c[seguimientoDe(e)] = (c[seguimientoDe(e)] ?? 0) + 1;
    return c;
  }, [envios]);
  const q = busca.trim().toUpperCase();
  const filtrados = useMemo(() => envios.filter((e) =>
    (estado === "todos" || seguimientoDe(e) === estado)
    && (!q || [e.orden, e.salida, e.envio, e.referencia, e.kam].some((x) => (x ?? "").toUpperCase().includes(q)))),
  [envios, estado, q]);
  useEffect(() => setVisibles(POR_PAGINA), [envios, estado, q]);
  const pagina = filtrados.slice(0, visibles);

  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Ceja>Un renglón por salida de Odoo · {num(filtrados.length)} de {num(total)} envíos</Ceja>
            <ChipFuente />
          </div>
          <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
            ¿En qué va cada envío?
          </h2>
        </div>
        <label className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
          <Search className="h-3.5 w-3.5 text-slate-400" />
          <input value={busca} onChange={(ev) => setBusca(ev.target.value)} placeholder="Orden, salida o número de envío"
                 className="w-56 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
        </label>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {ESTADOS.map((s) => (
          <button key={s.k} type="button" title={s.titulo} onClick={() => setEstado(s.k)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${
                    estado === s.k ? "border-indigo-200 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-500"}`}>
            <span className="h-2 w-2 rounded-full" style={{ background: s.punto }} />
            {s.t}<span className="font-mono text-[11px] opacity-70">{num(conteos[s.k] ?? 0)}</span>
          </button>
        ))}
      </div>

      <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[1240px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">Envío · orden · salida</th>
              <th className="px-3.5 py-2.5">Canal · cuenta</th>
              <th className="w-[50%] px-3.5 py-2.5">Rail de etapas</th>
              <th className="px-3.5 py-2.5 text-right">Piezas</th>
              <th className="px-3.5 py-2.5 text-right" title="Mercado Libre: suma de los avisos de FULL (webhook fbm_stock_operations) por SKU contra lo enviado. Diez días después de la salida, lo que falta es lo que ML no recibió.">Llegada a FULL</th>
              <th className="px-3.5 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {pagina.map((e, i) => {
              const tasa = tasaDe(e);
              return (
                <tr key={e.id ?? `${e.orden}-${i}`} className="cursor-pointer border-t border-slate-100 align-top hover:bg-slate-50/60"
                    onClick={() => onAbrir(e)}>
                  <td className="px-3.5 py-[11px]">
                    <div className={`font-mono text-[13px] font-bold ${e.envio ? "text-slate-900" : "text-amber-700"}`}
                         title={e.referencia ? `Referencia tecleada en Odoo: «${e.referencia}»` : "La orden no trae referencia"}>
                      {e.envio ?? "sin número"}
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-500">
                      {e.orden ?? "—"} · {e.salida ?? "—"}
                    </div>
                    <div className="text-[11px] text-slate-400">armó {e.kam ?? "—"}</div>
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
                    {!!e.faltante_odoo && (
                      <div className="text-[11px] font-semibold text-amber-700"
                           title="Bodega validó la salida sin tener todo: Odoo canceló esos renglones y la orden quedó con Entregado menor a lo pedido.">
                        Odoo no surtió {num(e.faltante_odoo)}
                      </div>
                    )}
                  </td>
                  <td className="px-3.5 py-[11px] text-right">
                    {e.cobertura?.fuente === "avisos" ? (
                      <Llegada c={e.cobertura} />
                    ) : e.canal === "meli" && !e.cuenta ? (
                      <>
                        <div className="inline-flex rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] font-bold text-amber-700"
                             title={e.cuenta_regla}>
                          sin cuenta
                        </div>
                        <div className="mt-1 text-[11px] text-slate-400">no se sabe qué bodega mirar</div>
                      </>
                    ) : e.cobertura && e.cobertura.llegaron > 0 ? (
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
                    <button type="button" onClick={(ev) => { ev.stopPropagation(); onAbrir(e); }}
                            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50">
                      Detalle
                    </button>
                  </td>
                </tr>
              );
            })}
            {filtrados.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-sm text-slate-500">
                  Ningún envío con estos filtros. No es un error de lectura: Odoo contestó y no hay salidas así.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {visibles < filtrados.length && (
        <div className="mt-3 text-center">
          <button type="button" onClick={() => setVisibles((v) => v + POR_PAGINA)}
                  className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Mostrar {Math.min(POR_PAGINA, filtrados.length - visibles)} más · van {visibles} de {filtrados.length}
          </button>
        </div>
      )}

      <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs text-slate-400">
        <span><b>Salida validada</b> es cuando bodega cierra la salida en Odoo, no la hora del camión: en 16 de 43 envíos medidos ML ya había recibido antes.</span>
        <span><b>Las dos primeras etapas piden acción, no son un dato faltante</b>: la solicitud nace en «Crear FULL» y el
          recorte lo hace Bodega. Hasta que se guarden no se puede saber cuánto se pidió ni cuánto recortó Bodega.</span>
      </div>
    </Tarjeta>
  );
}

/**
 * El detalle de un envío en una VENTANA EMERGENTE sobre la tabla (Brandon,
 * 17-sep). Se cierra con la ✕, con Esc o haciendo clic fuera. Minimalista a
 * propósito: el rail C2 arriba, los SKUs en medio y el origen de cada dato, en
 * chico, abajo. Tocar un SKU abre su ficha ENCIMA (24-sep).
 */
export function DetalleEnvioModal({ envio: base, onCerrar }: { envio: Envio; onCerrar: () => void }) {
  const [copiado, setCopiado] = useState(false);
  const [detalle, setDetalle] = useState<EnvioConLineas | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sku, setSku] = useState<string | null>(null);

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
  const avisos = e.cobertura?.fuente === "avisos";
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
    <Ventana etiqueta={`Detalle del envío ${e.envio ?? e.orden ?? ""}`} onCerrar={onCerrar}>
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
            {lineas.length ? ` · ${lineas.filter((l) => l.pedidas > 0 || (l.enviadas ?? 0) > 0).length} SKUs` : ""}
            {!!e.faltante_odoo && (
              <span className="font-semibold text-amber-700"> · Odoo no surtió {num(e.faltante_odoo)} de {num(e.pedidas)}</span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button type="button" onClick={copiar} disabled={!e.envio} title="Copiar el número del envío"
                  className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40">
            {copiado ? <span className="text-xs font-semibold text-emerald-600">copiado</span> : <Copy className="h-4 w-4" />}
          </button>
          <BotonCerrar onClick={onCerrar} />
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
        {e.cobertura?.sin_sku && (
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-center text-[11.5px] text-amber-800">
            Ojo: entre la orden y el cierre llegaron a esta cuenta <b>{num(e.cobertura.sin_sku.piezas)} piezas</b> que
            ML no deja ligar a un SKU (publicaciones con variantes: el SKU vive en cada variante). Si este envío
            llevaba productos con variantes, lo que aparece como no recibido puede estar ahí.
          </p>
        )}
      </div>

      {/* ── los SKUs ───────────────────────────────────────────── */}
      <div className="border-t border-slate-100 px-6 py-4">
        {error ? (
          <p className="text-[12.5px] text-rose-700">No se pudieron leer los renglones: <code className="font-mono">{error}</code></p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] border-collapse text-[12.5px]">
              <thead>
                <tr className="text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-400">
                  <th className="pb-2" title="Toca un SKU para ver su ficha: publicación, stock en FULL, ventas y todos sus envíos.">SKU · ver ficha</th>
                  <th className="pb-2 text-right" title="Lo que pidió la orden de venta en Odoo.">Pedidas</th>
                  <th className="pb-2 text-right" title="Lo que bodega entregó al validar la salida (Entregado en Odoo). Sólo cuando la salida ya se validó.">Enviadas</th>
                  <th className="pb-2 text-right"
                      title={avisos ? "Suma de los avisos de FULL de Mercado Libre para este SKU desde que se creó la orden."
                                    : `Primera subida de stock en ${almacen} tras la salida (sync cada 15 min).`}>
                    Llegaron a {almacen}
                  </th>
                  <th className="pb-2 pl-4">Qué pasó</th>
                  <th className="pb-2 text-right" title="Primera tanda que quedó vendible.">1ª llegada</th>
                  <th className="pb-2 text-right">1ª venta</th>
                </tr>
              </thead>
              <tbody>
                {lineas.map((l) => (
                  <tr key={l.sku} className={`border-t border-slate-100 ${l.pedidas <= 0 && !l.enviadas ? "opacity-50" : ""}`}>
                    <td className="py-2 pr-3">
                      <button type="button" onClick={() => setSku(l.sku)} disabled={e.canal !== "meli"}
                              title={e.canal === "meli" ? "Ver la ficha de este SKU" : "La ficha es de FULL de Mercado Libre"}
                              className="text-left disabled:cursor-default">
                        <span className={`font-mono font-bold ${e.canal === "meli" ? "text-indigo-700 underline decoration-indigo-200 underline-offset-2 hover:decoration-indigo-500" : "text-slate-800"}`}>
                          {l.sku}
                        </span>
                        <span className="block max-w-[360px] truncate text-[11px] text-slate-400" title={l.nombre}>{l.nombre}</span>
                      </button>
                    </td>
                    <td className="py-2 text-right font-mono tabular-nums text-slate-500">{num(l.pedidas)}</td>
                    <td className={`py-2 text-right font-mono font-bold tabular-nums ${
                      l.faltante_odoo ? "text-amber-700" : "text-slate-800"}`}
                        title={l.faltante_odoo ? `Odoo no surtió ${l.faltante_odoo} de ${l.pedidas}.` : undefined}>
                      {l.enviadas === null ? <span className="font-normal text-amber-700">sin validar</span> : num(l.enviadas)}
                    </td>
                    <td className="py-2 text-right font-mono font-bold tabular-nums text-slate-800">
                      {avisos
                        ? (l.llegadas ? num(l.llegadas) : (l.pedidas > 0 || l.enviadas ? <span className="font-normal text-slate-300">0</span> : null))
                        : (l.piezas_llegadas ? num(l.piezas_llegadas) : sinDato("—"))}
                    </td>
                    <td className="py-2 pl-4 text-[11.5px]"><EstadoLinea l={l} /></td>
                    <td className="py-2 text-right font-mono text-[11.5px] text-emerald-700"
                        title={l.llegada_ultima && l.llegada_ultima !== l.llegada ? `última tanda: ${fecha({ ts: l.llegada_ultima })}` : undefined}>
                      {l.llegada ? fecha({ ts: l.llegada, aprox: !avisos }) : sinDato(`Sin llegada a ${almacen}.`)}
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
          {avisos
            ? <>Llegaron = avisos de FULL de Mercado Libre (webhook fbm_stock_operations) de esta cuenta, por SKU, desde que
                se creó la orden hasta la siguiente orden del mismo SKU. Diez días después de la salida, lo que falta se
                cuenta como no recibido por ML; lo que llega de más (+N) es ML moviendo piezas entre sus bodegas. Lo
                declarado y los motivos de rechazo sólo viven en su panel.</>
            : <>Llegó, activo y 1ª venta son observados por nuestro sync (~ = hora en que se vio): {porQue}.</>}
        </p>
      </div>

      {sku && <FichaSku sku={sku} cuenta={e.cuenta} onCerrar={() => setSku(null)} />}
    </Ventana>
  );
}

/** La celda de llegada de ML: completo, en proceso o lo que ML no recibió. */
function Llegada({ c }: { c: NonNullable<Envio["cobertura"]> }) {
  const env = c.piezas_enviadas ?? null;
  const ambar = "border-amber-300 bg-amber-50 text-amber-700";
  const [clase, texto, nota] = c.rechazadas
    ? ["border-rose-200 bg-rose-50 text-rose-700", `ML no recibió ${num(c.rechazadas)} pzs`,
       `${c.completos ?? 0} de ${c.skus} SKUs completos`]
    : env !== null && c.skus > 0 && c.completos === c.skus
      ? ["border-emerald-200 bg-emerald-50 text-emerald-700", `${c.completos} de ${c.skus} SKUs`, "completos en FULL"]
      : env === null
        // ML a veces recibe ANTES de que bodega valide: se enseña, sin juzgar.
        ? (c.llegaron > 0 ? [ambar, `${num(c.piezas_llegadas)} pzs llegaron`, "bodega no ha validado"]
                          : [ambar, "aún no sale", "salida sin validar"])
        : [ambar, `${num(c.piezas_llegadas)} de ${num(env)} pzs`,
           c.cierre ? `llegando · cierra ${fecha({ ts: c.cierre, dia: true })}` : "llegando"];
  return (
    <>
      <div className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-bold ${clase}`}>{texto}</div>
      <div className="mt-1 text-[11px] text-slate-400">{nota}</div>
    </>
  );
}

/** Qué pasó con un SKU: lo dice en palabras, con su color. */
function EstadoLinea({ l }: { l: LineaOdoo }) {
  if (l.pedidas <= 0 && !l.enviadas) return <span className="text-slate-300">en 0 en la orden</span>;
  if (l.enviadas === 0) {
    return <span className="font-semibold text-amber-700" title="Bodega validó sin esta pieza: Odoo canceló el renglón.">Odoo no la surtió</span>;
  }
  switch (l.estado_llegada) {
    case "completo":
      return <span className="font-semibold text-emerald-700">completo{l.llegadas_extra ? ` · +${l.llegadas_extra}` : ""}</span>;
    case "en_proceso":
      return <span className="font-semibold text-amber-700">en proceso</span>;
    case "llegando":
      return <span className="font-semibold text-amber-700" title="ML ya avisó piezas y bodega no ha validado la salida en Odoo.">llegando · sin validar</span>;
    case "rechazo_parcial":
      return <span className="font-semibold text-rose-700">ML no recibió {num(l.rechazadas)}</span>;
    case "rechazo_total":
      return <span className="font-semibold text-rose-700">ML no recibió nada ({num(l.rechazadas)})</span>;
    default:
      return <span className="text-slate-300">—</span>;
  }
}

function Dato({ t, v }: { t: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10.5px] font-semibold text-slate-400">{t}</dt>
      <dd className="truncate text-slate-700" title={v}>{v}</dd>
    </div>
  );
}
