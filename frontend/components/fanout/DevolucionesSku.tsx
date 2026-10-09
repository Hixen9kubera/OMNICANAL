"use client";

/**
 * DevolucionesSku — panel lateral de la pestaña Devoluciones para UN SKU: ¿por
 * qué subió su stock? Cada subida de Odoo (el renglón `odoo_delta` que dejó
 * stock_watch) con las recepciones a rack validadas antes de ella, la guía que
 * Bodega anotó en la nota y la devolución de kubera que trae esa guía, si la hay.
 * Aparte, las de rack sin subida (¿ya pasó stock_watch?) y las de SCRAP, que no
 * suben el stock. Abajo, las devoluciones de kubera de este SKU que aún no entran.
 * Lo arma
 * `GET /api/fanout/devoluciones/sku/{sku}`. Solo lee. Mismo molde que
 * `TrazabilidadSku`, que se abre desde aquí en su carril «Devoluciones».
 */
import { useEffect, useRef, useState } from "react";
import { Route, X } from "lucide-react";
import { ApiError, devolucionesSku } from "@/lib/api";
import type { CoberturaDevol, DetalleDevol, RecepcionSubida } from "./tipos";
import { CUENTA_CHIP, DESTINO_RECEPCION, NOMBRE_CUENTA, TIPO_RECEPCION, conSigno, horaCorta } from "./tipos";

const PERIODOS = [7, 14, 30, 60];
const DIAS_SEMANA = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("es-MX"));
const pl = (k: number, uno: string, varios: string) => (k === 1 ? uno : varios);

/** «hoy 14:49» · «mar 29 sep 14:49». */
function conDia(hora: string, hoy: string): string {
  const dia = hora.slice(0, 10);
  if (dia === hoy) return `hoy ${hora.slice(11, 16)}`;
  const [a, m, d] = dia.split("-").map(Number);
  return `${DIAS_SEMANA[new Date(Date.UTC(a, m - 1, d)).getUTCDay()]} ${horaCorta(hora, hoy)}`;
}

/** Verde = ligada a una devolución de kubera; ámbar = devolución con una guía que kubera no tiene. */
function tonoRecepcion(r: RecepcionSubida): string {
  if (r.destino === "scrap") return "text-slate-600";
  if (r.devolucion) return "text-emerald-800";
  if (r.tipo === "devolucion" && !r.en_kubera) return "text-amber-800";
  return "text-slate-700";
}

/**
 * Una recepción: picking, hora, a rack o a SCRAP (con las piezas si no es una),
 * la guía de la nota y lo que es. En 375 px se apila; desde `sm`, 5 columnas.
 * `dia` es el día contra el que se acorta la hora (el de su subida, o el corte).
 */
function FilaRecepcion({ rc, dia, conFecha = false }: { rc: RecepcionSubida; dia: string; conFecha?: boolean }) {
  const destino = DESTINO_RECEPCION[rc.destino] ?? { texto: rc.destino, cls: "bg-slate-100 text-slate-700" };
  return (
    <li className={`flex flex-col gap-0.5 border-b border-slate-100 px-4 py-2 text-[13px] leading-[19px] last:border-b-0 sm:grid ${
      conFecha ? "sm:grid-cols-[84px_96px_76px_minmax(0,1fr)_minmax(0,1.2fr)]" : "sm:grid-cols-[84px_54px_76px_minmax(0,1fr)_minmax(0,1.2fr)]"} sm:items-baseline sm:gap-3`}>
      <span className="flex flex-wrap items-baseline gap-2 sm:contents">
        <span className="font-mono text-xs font-semibold text-slate-900">{rc.picking}</span>
        <span className="tabular-nums text-slate-600">{horaCorta(rc.hora, dia)}</span>
        <span className={`justify-self-start rounded px-1.5 text-[11px] font-semibold ${destino.cls}`}>
          {destino.texto}{rc.piezas != null && rc.piezas !== 1 ? ` ×${rc.piezas.toLocaleString("es-MX")}` : ""}
        </span>
      </span>
      <span className="min-w-0 break-all">
        {rc.tipo !== "devolucion" && <span className="text-xs text-slate-600">{TIPO_RECEPCION[rc.tipo] ?? rc.tipo}{rc.guia ? " · " : ""}</span>}
        {rc.guia
          ? <span className="font-mono text-xs text-slate-700">{rc.guia}</span>
          : rc.tipo === "devolucion" && (
            <span className="text-xs text-slate-500">{rc.guia_no_reconocida ? "guía no reconocida en la nota" : "sin guía en la nota"}</span>
          )}
      </span>
      <span className={`font-semibold ${tonoRecepcion(rc)}`}>
        {rc.txt}
        {rc.espera && <span className="block text-xs font-normal text-slate-600">stock_watch aún no pasa: todavía no la copia</span>}
      </span>
    </li>
  );
}

function textoError(e: unknown): string {
  if (e instanceof ApiError) return e.detail ?? `HTTP ${e.status}`;
  return e instanceof Error ? e.message : "error";
}

function Cifra({ valor, texto, tono = "base" }: { valor: string; texto: string; tono?: "base" | "verde" | "ambar" }) {
  const caja = tono === "verde" ? "border-emerald-200 bg-emerald-50" : tono === "ambar" ? "border-amber-300 bg-amber-50" : "border-slate-300 bg-white";
  const cifra = tono === "verde" ? "text-emerald-950" : tono === "ambar" ? "text-amber-950" : "text-slate-900";
  const linea = tono === "verde" ? "text-emerald-800" : tono === "ambar" ? "text-amber-900" : "text-slate-600";
  return (
    <div className={`flex flex-col gap-0.5 rounded-xl border p-3 ${caja}`}>
      <span className={`text-[26px] font-bold leading-8 tabular-nums ${cifra}`}>{valor}</span>
      <span className={`text-xs leading-[17px] ${linea}`}>{texto}</span>
    </div>
  );
}

export default function DevolucionesSku({ sku, diasInicial, hoy, cobertura, onCerrar, onTrazabilidad }: {
  sku: string | null;
  /** El periodo con que abre: el que alcanza a ver la caja que se tocó. */
  diasInicial: number;
  /** 'YYYY-MM-DD' (CDMX) del corte de la bandeja, para las horas cortas. */
  hoy: string;
  /** Lo que Odoo recibió como «DEVOLUCIONES» en el periodo de la bandeja contra lo que kubera captura. */
  cobertura?: { c: CoberturaDevol; dias: number } | null;
  onCerrar: () => void;
  /** Abre la trazabilidad del fan-out del mismo SKU, en su carril «Devoluciones». */
  onTrazabilidad: (sku: string, dias: number) => void;
}) {
  const [dias, setDias] = useState(diasInicial);
  const [intento, setIntento] = useState(0);
  const [d, setD] = useState<DetalleDevol | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cerrarRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!sku) return;
    // Un periodo nuevo cancela la lectura anterior: la lenta no pisa a la buena.
    const c = new AbortController();
    setD(null);
    setError(null);
    devolucionesSku(sku, dias, c.signal)
      .then((j) => { if (!c.signal.aborted) setD(j); })
      .catch((e: unknown) => { if (!c.signal.aborted) setError(textoError(e)); });
    return () => c.abort();
  }, [sku, dias, intento]);

  useEffect(() => {
    if (sku) cerrarRef.current?.focus();
  }, [sku]);

  if (!sku) return null;
  const r = d?.resumen;
  const cob = cobertura && cobertura.c.recepciones > 0 ? cobertura : null;
  // Sin Odoo las cifras de recepciones no se midieron: «—», no un cero.
  const sinOdoo = !!d && !d.odoo_ok;
  const sinSubida = d?.sin_subida ?? [];
  const esperan = sinSubida.filter((x) => x.espera).length;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-labelledby="t-devol-sku"
      onKeyDown={(e) => { if (e.key === "Escape") onCerrar(); }}>
      <button type="button" aria-label="Cerrar" className="absolute inset-0 cursor-default bg-slate-900/30" onClick={onCerrar} />
      <div className="relative flex h-full w-full max-w-[780px] flex-col overflow-y-auto bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">¿Por qué subió el stock?</div>
            <h2 id="t-devol-sku" className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="break-all font-mono text-base font-bold text-slate-900">{d?.sku ?? sku}</span>
              {d?.titulo && <span className="text-sm font-normal text-slate-700">{d.titulo}</span>}
            </h2>
            {d && d.odoo_de != null && d.odoo_a != null && (
              <div className="mt-0.5 text-[13px] leading-5 text-slate-600">
                Odoo <span className="font-bold tabular-nums text-slate-900">{n(d.odoo_de)} → {n(d.odoo_a)}</span> en {d.dias} días · horas de CDMX
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button type="button" onClick={() => onTrazabilidad(d?.sku ?? sku, dias)}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs font-semibold text-indigo-800 hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
              <Route size={15} aria-hidden />Abrir la trazabilidad
            </button>
            <button ref={cerrarRef} type="button" onClick={onCerrar}
              className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
              <X size={18} aria-hidden /><span className="sr-only">Cerrar</span>
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="max-w-md text-[13px] leading-5 text-slate-600">
              Cada subida de Odoo con las recepciones que la hicieron y la devolución de ML que trae cada una.
            </p>
            <div className="flex items-center gap-1.5" role="group" aria-label="Periodo">
              {PERIODOS.map((p) => (
                <button key={p} type="button" aria-pressed={dias === p} onClick={() => setDias(p)}
                  className={`h-8 rounded-full border px-3 text-xs font-semibold ${
                    dias === p ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
                  {p} días
                </button>
              ))}
            </div>
          </div>

          {error && (
            <p className="flex flex-wrap items-center gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">
              No se pudo leer el SKU ({error}).
              <button type="button" onClick={() => setIntento((i) => i + 1)}
                className="font-semibold text-rose-900 underline underline-offset-2">Reintentar</button>
            </p>
          )}
          {!d && !error && <p className="text-sm text-slate-500" role="status">Leyendo las subidas de Odoo y sus recepciones…</p>}

          {d && !d.odoo_ok && (
            <p role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-amber-50 px-3 py-2 text-[13px] leading-5 text-amber-950 ring-1 ring-amber-300">
              <span>
                <b>Odoo no respondió:</b> las subidas salen de la bitácora del fan-out, pero sin sus recepciones ni las guías.
                {d.odoo_error && <span className="block text-xs text-amber-900">{d.odoo_error}</span>}
              </span>
              <button type="button" onClick={() => setIntento((i) => i + 1)}
                className="font-semibold text-amber-900 underline underline-offset-2">Reintentar</button>
            </p>
          )}

          {d && r && (
            <>
              <section aria-labelledby="t-devol-resumen" className="flex flex-col gap-2">
                <h3 id="t-devol-resumen" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Este SKU en {d.dias} días</h3>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Cifra valor={sinOdoo ? "—" : conSigno(r.entraron)}
                    texto={`${pl(r.entraron, "pieza entró", "piezas entraron")} a rack por devoluciones`} />
                  <Cifra valor={sinOdoo ? "—" : n(r.scrap)} texto={`a SCRAP: ${pl(r.scrap, "no cuenta", "no cuentan")} para el stock`} />
                  <Cifra valor={sinOdoo ? "—" : n(r.ligadas)} tono={sinOdoo ? "base" : "verde"}
                    texto={pl(r.ligadas, "devolución de kubera ligada (guía, orden o «Devolver»)",
                      "devoluciones de kubera ligadas (guía, orden o «Devolver»)")} />
                  <Cifra valor={sinOdoo ? "—" : n(r.sin_kubera)} tono={!sinOdoo && r.sin_kubera ? "ambar" : "base"}
                    texto={pl(r.sin_kubera, "guía en las notas que kubera no tiene", "guías en las notas que kubera no tiene")} />
                </div>
                {!sinOdoo && (r.retiro_full ?? 0) > 0 && (
                  <p className="text-xs leading-[18px] text-slate-600">
                    Además, {conSigno(r.retiro_full ?? 0)} {pl(r.retiro_full ?? 0, "pieza entró", "piezas entraron")} por retiros
                    de FULL (cajas de la bodega de ML, sin guía): no son devoluciones de clientes.
                  </p>
                )}
              </section>

              {d.pendientes.length > 0 && (
                <section aria-labelledby="t-devol-pend" className="flex flex-col gap-1.5 rounded-xl border border-slate-200 px-4 py-3">
                  <h3 id="t-devol-pend" className="text-[13px] font-semibold text-slate-900">Devoluciones de kubera que aún no entran</h3>
                  <ul className="flex flex-col gap-1 text-[13px] leading-[19px] text-slate-700">
                    {d.pendientes.map((p) => (
                      <li key={`${p.cuenta}-${p.id}`} className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-mono text-xs font-semibold text-slate-900">{p.id}</span>
                        <span className={`rounded-full px-2 py-px text-[11px] font-semibold ${CUENTA_CHIP[p.cuenta] ?? "bg-slate-100 text-slate-700"}`}>
                          {NOMBRE_CUENTA[p.cuenta] ?? p.cuenta}
                        </span>
                        <span>{p.txt}</span>
                        {p.guia && !p.txt.includes(p.guia) && <span className="font-mono text-xs text-slate-600">guía {p.guia}</span>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section aria-labelledby="t-devol-subidas" className="flex flex-col gap-3">
                <h3 id="t-devol-subidas" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Subidas de Odoo</h3>
                {d.subidas.length === 0 ? (
                  <div className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">
                    Odoo no subió el stock de este SKU en los últimos {d.dias} días.
                    {d.dias < 60 && (
                      <button type="button" onClick={() => setDias(60)}
                        className="ml-2 font-semibold text-indigo-700 underline-offset-2 hover:underline">Ver 60 días</button>
                    )}
                  </div>
                ) : d.subidas.map((s, i) => (
                  <article key={`${s.hora}-${i}`} aria-label={`Subida de Odoo, ${conDia(s.hora, hoy)}`}
                    className="overflow-hidden rounded-xl border border-slate-200">
                    <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-slate-200 bg-slate-50 px-4 py-2.5">
                      <span className="text-[13px] font-semibold tabular-nums text-slate-700">{conDia(s.hora, hoy)}</span>
                      <span className="text-[15px] font-bold tabular-nums text-slate-900">Odoo {n(s.de)} → {n(s.a)}</span>
                      <span className="rounded-full bg-indigo-100 px-2.5 py-px text-xs font-bold tabular-nums text-indigo-900">{conSigno(s.sube)}</span>
                      {s.reparto_txt && <span className="basis-full text-xs leading-[18px] text-slate-600 sm:ml-auto sm:basis-auto">{s.reparto_txt}</span>}
                    </header>
                    {s.recepciones.length === 0 ? (
                      <p className="px-4 py-2.5 text-[13px] leading-5 text-slate-600">
                        {d.odoo_ok ? "Ninguna recepción validada en Odoo antes de esta subida." : "Sin recepciones: Odoo no respondió."}
                      </p>
                    ) : (
                      <ul>
                        {/* Un picking con piezas a rack y a SCRAP llega como dos recepciones: la llave lleva el destino. */}
                        {s.recepciones.map((rc) => (
                          <FilaRecepcion key={rc.clave ?? `${rc.picking}-${rc.destino}`} rc={rc} dia={s.hora.slice(0, 10)} />
                        ))}
                      </ul>
                    )}
                  </article>
                ))}
              </section>

              {sinSubida.length > 0 && (
                <section aria-labelledby="t-devol-sin-subida" className="overflow-hidden rounded-xl border border-dashed border-slate-300">
                  <header className="border-b border-slate-200 bg-slate-50 px-4 py-2.5">
                    <h3 id="t-devol-sin-subida" className="text-[13px] font-semibold text-slate-900">A rack, sin subida de Odoo</h3>
                    <p className="text-xs leading-[18px] text-slate-600">
                      {esperan === sinSubida.length
                        ? `Se validaron después de la última pasada de stock_watch${d.pasada ? ` (${horaCorta(d.pasada, hoy)})` : ""}: todavía no las copia a Woo.`
                        : `Ninguna subida de Odoo en las 3 h siguientes${esperan ? " (salvo las que dicen que stock_watch aún no pasa)" : ""}: una salida en la misma pasada pudo compensarlas. Las de devoluciones cuentan en «entraron» de arriba.`}
                    </p>
                  </header>
                  <ul>
                    {sinSubida.map((rc) => (
                      <FilaRecepcion key={rc.clave ?? `${rc.picking}-${rc.destino}`} rc={rc} dia={hoy} conFecha />
                    ))}
                  </ul>
                </section>
              )}

              {(d.a_scrap?.length ?? 0) > 0 && (
                <section aria-labelledby="t-devol-a-scrap" className="overflow-hidden rounded-xl border border-dashed border-slate-300">
                  <header className="border-b border-slate-200 bg-slate-50 px-4 py-2.5">
                    <h3 id="t-devol-a-scrap" className="text-[13px] font-semibold text-slate-900">A SCRAP: no suben el stock</h3>
                    <p className="text-xs leading-[18px] text-slate-600">
                      SCRAP no cuenta en lo que copia stock_watch, así que no hacen subida. Las de devoluciones cuentan en «a SCRAP» de arriba.
                    </p>
                  </header>
                  <ul>
                    {d.a_scrap!.map((rc) => (
                      <FilaRecepcion key={rc.clave ?? `${rc.picking}-${rc.destino}`} rc={rc} dia={hoy} conFecha />
                    ))}
                  </ul>
                </section>
              )}

              {cob && (
                <section aria-labelledby="t-devol-cob"
                  className="flex flex-col gap-1.5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] leading-5 text-amber-950">
                  <h3 id="t-devol-cob" className="text-sm font-bold text-amber-900">
                    {cob.c.guias_sin_kubera * 2 > cob.c.guias_en_recepciones ? "Kubera no ve la mayoría de las cajas" : "Cajas que kubera no ve"}
                  </h3>
                  <p>
                    En {cob.dias} días Odoo tiene {n(cob.c.recepciones)} recepciones de «DEVOLUCIONES»
                    {cob.c.retiros_full ? ` (${n(cob.c.retiros_full)} son retiros de FULL)` : ""} con {n(cob.c.guias_en_recepciones)} guías
                    anotadas; {n(cob.c.guias_sin_kubera)} no están en las devoluciones que captura kubera.
                  </p>
                  <p>Hasta que se capturen, una subida puede venir de una caja que esta pestaña no conoce.</p>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
