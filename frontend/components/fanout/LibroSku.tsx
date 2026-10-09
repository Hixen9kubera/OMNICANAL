"use client";

/**
 * LibroSku — panel lateral de la pestaña Bodegas para UN SKU (esté o no en la
 * tabla): cómo está hoy en Odoo por bodega, en kubera por bodega y contra Woo; su
 * libro (`almacen.stock_mov`, los últimos movimientos, en línea de tiempo) y sus
 * órdenes de venta (`ventas.ov_lineas` + `ventas.ov_ordenes`, sin datos del
 * comprador). Lo arma `GET /api/fanout/bodegas/sku`.
 * Solo lee. Mismo molde que `TrazabilidadSku`, que se puede abrir desde aquí.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Route, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { DetalleBodega, MovLibro } from "./tipos";
import { COINCIDE_BODEGA, MOTIVO_MOV, conSigno, horaCorta } from "./tipos";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("es-MX"));

function corto(dia: string): string {
  const [, m, d] = dia.split("-").map(Number);
  return `${d} ${MESES[m - 1]}`;
}

function etiquetaDia(dia: string, hoy: string): string {
  if (dia === hoy) return `Hoy · ${corto(dia)}`;
  const [a, m, d] = hoy.split("-").map(Number);
  const ayer = new Date(Date.UTC(a, m - 1, d) - 86_400_000).toISOString().slice(0, 10);
  return dia === ayer ? `Ayer · ${corto(dia)}` : corto(dia);
}

const ESTADO_OV: Record<string, string> = {
  borrador: "bg-slate-100 text-slate-700",
  confirmada: "bg-indigo-100 text-indigo-900",
  entregada: "bg-emerald-50 text-emerald-800",
  cancelada: "bg-slate-200 text-slate-800",
  entregada_cancelada: "bg-amber-100 text-amber-900",
};

function Cifra({ titulo, valor, linea, cls = "bg-slate-50 text-slate-900" }: {
  titulo: string; valor: string; linea?: string; cls?: string;
}) {
  return (
    <div className={`flex min-h-[58px] flex-col justify-center rounded-lg px-2.5 py-1.5 ${cls}`}>
      <span className="text-[11px] font-semibold uppercase tracking-wide opacity-80">{titulo}</span>
      <span className="text-[15px] font-bold leading-5 tabular-nums">{valor}</span>
      {linea && <span className="truncate text-[11px] leading-[15px]">{linea}</span>}
    </div>
  );
}

export default function LibroSku({ sku, onCerrar, onTrazabilidad }: {
  sku: string | null;
  onCerrar: () => void;
  /** Abre la línea de trazabilidad del fan-out (Woo → canales) del mismo SKU. */
  onTrazabilidad?: (sku: string) => void;
}) {
  const [d, setD] = useState<DetalleBodega | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cerrarRef = useRef<HTMLButtonElement>(null);
  // El último SKU pedido: una respuesta lenta de uno anterior (fuera de la caché,
  // lo que tarde Odoo) no puede pisar la del que se abrió después.
  const pedido = useRef<string | null>(null);

  const cargar = useCallback(async (s: string) => {
    pedido.current = s;
    setD(null);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/bodegas/sku?${new URLSearchParams({ sku: s })}`, { cache: "no-store" });
      if (pedido.current !== s) return;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as DetalleBodega;
      if (pedido.current !== s) return;
      if (!j.ok) throw new Error(j.motivo || "sin respuesta");
      setD(j);
    } catch (e) {
      if (pedido.current !== s) return;
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    if (sku) {
      void cargar(sku);
      cerrarRef.current?.focus();
    } else {
      pedido.current = null;
    }
  }, [sku, cargar]);

  const porDia = useMemo(() => {
    const grupos: [string, MovLibro[]][] = [];
    for (const m of d?.libro ?? []) {
      const dia = m.hora.slice(0, 10);
      const ultimo = grupos[grupos.length - 1];
      if (ultimo && ultimo[0] === dia) ultimo[1].push(m);
      else grupos.push([dia, [m]]);
    }
    return grupos;
  }, [d]);

  if (!sku) return null;
  const f = d?.fila;
  const cBodegas = Object.keys(f?.kubera ?? {});

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Bodegas de ${sku}`}
      onKeyDown={(e) => { if (e.key === "Escape") onCerrar(); }}>
      <button type="button" aria-label="Cerrar" className="absolute inset-0 cursor-default bg-slate-900/30" onClick={onCerrar} />
      <div className="relative flex h-full w-full max-w-[780px] flex-col overflow-y-auto bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">Bodegas del SKU</div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="break-all font-mono text-base font-bold text-slate-900">{d?.sku ?? sku}</span>
              {f?.nombre && <span className="text-sm text-slate-700">{f.nombre}</span>}
            </div>
            {f && (
              <div className="mt-0.5 text-[13px] leading-5 text-slate-600">
                Woo esperado <span className="font-bold text-slate-900">{n(f.esperado)}</span> · Woo hoy{" "}
                <span className="font-bold text-slate-900">{f.woo == null ? "sin número" : n(f.woo)}</span>
                {f.woo_de && d && <> (foto {horaCorta(f.woo_de, d.hoy)})</>}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {onTrazabilidad && (
              <button type="button" onClick={() => onTrazabilidad(d?.sku ?? sku)}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs font-semibold text-indigo-800 hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                <Route size={15} aria-hidden />Trazabilidad del fan-out
              </button>
            )}
            <button ref={cerrarRef} type="button" onClick={onCerrar}
              className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
              <X size={18} aria-hidden /><span className="sr-only">Cerrar</span>
            </button>
          </div>
        </div>

        <div className="flex flex-col gap-5 px-5 py-5 sm:px-6">
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">No se pudo leer el SKU ({error}).</p>}
          {!d && !error && <p className="text-sm text-slate-500">Leyendo el libro…</p>}
          {d && !d.existe && (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
              {d.odoo.ok
                ? <>No encontré «{d.sku}» ni en Odoo, ni en la foto de stock_watch, ni en kubera. Revisa que el SKU esté bien escrito.</>
                : <>«{d.sku}» no está en la foto de stock_watch ni en kubera. Odoo no respondió, así que no se sabe si existe allá.</>}
            </p>
          )}
          {d && !d.tablas.ok && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
              Faltan las tablas de la 0064/0065 ({d.tablas.faltan.join(", ")}): no hay libro ni OV que leer.
            </p>
          )}

          {d && f && d.existe && (
            <>
              <section aria-labelledby="t-hoy-sku" className="flex flex-col gap-2">
                <h3 id="t-hoy-sku" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Hoy</h3>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {d.columnas_odoo.map((c) => {
                    const o = f.odoo?.[c.codigo];
                    return (
                      <Cifra key={c.codigo} titulo={`Odoo ${c.codigo}`}
                        valor={f.odoo == null || !f.odoo_existe ? "—" : n(o?.libre ?? 0)}
                        linea={f.odoo == null ? "Odoo no respondió" : !f.odoo_existe ? "no existe en Odoo"
                          : `físico ${n(o?.fisico ?? 0)} · reservado ${n(o?.reservado ?? 0)}`} />
                    );
                  })}
                  <Cifra titulo="Odoo total (foto)" valor={n(f.odoo_total)}
                    linea={[f.odoo_hoy != null && f.odoo_hoy !== f.odoo_total ? `hoy ${n(f.odoo_hoy)}` : null,
                      f.otras ? `fuera de las 3: ${conSigno(f.otras)}` : null].filter(Boolean).join(" · ") || "lo que leyó stock_watch"} />
                  {cBodegas.map((c) => (
                    <Cifra key={c} titulo={`kubera ${c}`} valor={n(f.kubera[c].libre)} cls="bg-indigo-50 text-indigo-950"
                      linea={`físico ${n(f.kubera[c].fisico)} · apartado ${n(f.kubera[c].apartado)}`} />
                  ))}
                  <Cifra titulo="Woo esperado" valor={n(f.esperado)}
                    linea={[f.kubera_base != null ? `+ kubera ${n(f.kubera_base)}` : null,
                      f.pend ? `− ${n(f.pend)} sin orden` : null].filter(Boolean).join(" ") || undefined} />
                  <Cifra titulo="Coincide" cls={COINCIDE_BODEGA[f.coincide].cls}
                    valor={f.dif ? conSigno(f.dif) : COINCIDE_BODEGA[f.coincide].texto} linea={f.coincide_t} />
                </div>
                <p className="text-xs leading-[18px] text-slate-700">{f.esperado_d}</p>
                {f.avisos.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-700">
                    {f.avisos.map((a) => <span key={a} className="rounded bg-amber-50 px-1.5 py-px text-amber-900">{a}</span>)}
                  </div>
                )}
                {!d.odoo.ok && d.odoo.motivo && <p className="text-xs text-amber-900">{d.odoo.motivo}</p>}
              </section>

              {/* Sin las tablas de la 0064/0065 no hay libro ni OV que leer: no se afirma que estén vacíos. */}
              {d.tablas.ok && (<>
              <section aria-labelledby="t-libro-sku" className="flex flex-col gap-2">
                <h3 id="t-libro-sku" className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Libro de kubera{d.libro_total > d.libro.length ? ` · los ${d.libro.length} más recientes de ${n(d.libro_total)}` : ""}
                </h3>
                {d.libro.length === 0 ? (
                  <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">Sin movimientos en el libro: kubera nunca ha tenido este SKU.</p>
                ) : (
                  <div className="flex flex-col gap-3">
                    {porDia.map(([dia, movs]) => (
                      <section key={dia} aria-label={etiquetaDia(dia, d.hoy)}>
                        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">{etiquetaDia(dia, d.hoy)}</h4>
                        <ol>
                          {movs.map((m, i) => (
                            <li key={m.id} className="grid gap-x-2" style={{ gridTemplateColumns: "40px 16px minmax(0,1fr)" }}>
                              <time dateTime={m.hora.replace(" ", "T")} className="pt-[3px] text-right text-xs tabular-nums text-slate-500">{m.hora.slice(11, 16)}</time>
                              <span className="relative flex justify-center">
                                {i < movs.length - 1 && <span className="absolute bottom-0 top-3 w-0.5 bg-slate-200" aria-hidden />}
                                <span className={`relative mt-[5px] h-3 w-3 rounded-full ${m.delta > 0 ? "bg-emerald-600" : "bg-rose-600"}`} aria-hidden />
                              </span>
                              <div className="min-w-0 pb-3 text-[13px] leading-5 text-slate-800">
                                <span className="font-semibold">{MOTIVO_MOV[m.motivo] ?? m.motivo}</span>{" "}
                                <span className="font-semibold tabular-nums">{conSigno(m.delta)}</span> en {m.almacen} → queda{" "}
                                <span className="font-semibold tabular-nums">{n(m.saldo_despues)}</span>
                                {m.ref && <span className="font-mono text-xs text-slate-600"> · {m.ref}</span>}
                                <div className="text-xs text-slate-600">
                                  {[m.quien, m.via, m.nota].filter(Boolean).join(" · ")}
                                </div>
                              </div>
                            </li>
                          ))}
                        </ol>
                      </section>
                    ))}
                  </div>
                )}
              </section>

              <section aria-labelledby="t-ov-sku" className="flex flex-col gap-2">
                <h3 id="t-ov-sku" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Órdenes de venta</h3>
                {d.ov.length === 0 ? (
                  <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700">Ninguna OV trae este SKU.</p>
                ) : (
                  <ul className="flex flex-col divide-y divide-slate-100 rounded-lg ring-1 ring-slate-200">
                    {d.ov.map((o) => (
                      <li key={`${o.folio}-${o.linea}`} className="flex flex-col gap-0.5 px-3 py-2 text-[13px] text-slate-800">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono font-semibold">{o.folio}</span>
                          <span className={`rounded px-1.5 py-px text-[11px] font-semibold ${ESTADO_OV[o.estado] ?? "bg-slate-100 text-slate-700"}`}>
                            {o.estado.replace("_", " ")}{o.borrada ? " · borrada" : ""}
                          </span>
                          <span>{o.tipo === "full" ? `FULL${o.full_tienda ? ` ${o.full_tienda}` : ""}` : o.canal ?? "venta"} · renglón {o.linea}:{" "}
                            <span className="font-semibold tabular-nums">{n(o.cantidad)}</span> pzs de {o.almacen ?? "sin bodega"}
                            {o.reservado ? ` · ${n(o.reservado)} apartadas` : ""}{o.entregado != null ? ` · ${n(o.entregado)} entregadas` : ""}</span>
                        </div>
                        <div className="text-xs text-slate-600">
                          {[o.creada && `creada ${horaCorta(o.creada, d.hoy)}`, o.confirmada && `confirmada ${horaCorta(o.confirmada, d.hoy)}`,
                            o.entregada && `entregada ${horaCorta(o.entregada, d.hoy)}`, o.cancelada && `cancelada ${horaCorta(o.cancelada, d.hoy)}`]
                            .filter(Boolean).join(" · ")}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              </>)}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
