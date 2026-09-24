"use client";

/**
 * FULLFILMENT · la ficha de UN SKU — ya no es una pestaña: es la ventana que se
 * abre al tocar un SKU dentro del detalle de un envío (Brandon, 24-sep-2026: "el
 * por SKU o MLM es solamente un POP"). Se abre ENCIMA del detalle y Esc la cierra
 * sin cerrar el envío.
 *
 * Todo es dato (`GET /api/fulfillment/sku/{sku}`): la publicación de ML de cada
 * cuenta con su stock en FULL de hoy, la venta FULL por semana, cada envío de
 * Odoo que llevó el SKU con lo que llegó, cada aviso de llegada de ML y lo libre
 * en Odoo. Lo que no se sabe se dice, no se pinta en cero.
 */

import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { BotonCerrar, Ceja, ChipCuenta, FONDO_RAYADO, PUNTO_CUENTA, Ventana, dia, fecha, num } from "./ui";
import type { Cuenta, FichaSku as Ficha } from "./tipos";

const QUE_PASO: Record<string, { t: string; c: string }> = {
  completo: { t: "completo", c: "text-emerald-700" },
  en_proceso: { t: "en proceso", c: "text-amber-700" },
  llegando: { t: "llegando · sin validar", c: "text-amber-700" },
  rechazo_parcial: { t: "ML no recibió parte", c: "text-rose-700" },
  rechazo_total: { t: "ML no recibió nada", c: "text-rose-700" },
};

export default function FichaSku({ sku, cuenta, onCerrar }: {
  sku: string; cuenta: Cuenta | null; onCerrar: () => void;
}) {
  const [f, setF] = useState<Ficha | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    fetchSesion(`${API_BASE}/api/fulfillment/sku/${encodeURIComponent(sku)}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        return r.json() as Promise<Ficha>;
      })
      .then((d) => { if (vivo) setF(d); })
      .catch((e: unknown) => { if (vivo) setError(e instanceof Error ? e.message : String(e)); });
    return () => { vivo = false; };
  }, [sku]);

  // La cuenta del envío va primero; la otra, si también lo tiene, después.
  const cuentas: Cuenta[] = cuenta === "San Corpe" ? ["San Corpe", "Kubera"] : ["Kubera", "San Corpe"];
  const full = (c: Cuenta) => f?.publicaciones.find((p) => p.cuenta === c && p.is_fulfillment) ?? null;
  const aguanta = (c: Cuenta) => {
    const p = full(c);
    const v = f?.v30[c] ?? 0;
    return p && v > 0 ? p.stock_full / (v / 30) : null;
  };

  return (
    <Ventana etiqueta={`Ficha del SKU ${sku}`} onCerrar={onCerrar} ancho="max-w-[980px]">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
        <div className="min-w-0">
          <Ceja>La vida del SKU en FULL</Ceja>
          <div className="mt-1 flex flex-wrap items-center gap-2.5">
            <span className="font-mono text-lg font-extrabold text-slate-900">{sku}</span>
            {cuenta && <ChipCuenta cuenta={cuenta} />}
          </div>
          <p className="mt-0.5 text-[12.5px] text-slate-500">{f?.nombre ?? (f ? "no está en Odoo" : "leyendo…")}</p>
        </div>
        <BotonCerrar onClick={onCerrar} />
      </div>

      {error && <p className="px-6 py-4 text-[12.5px] text-rose-700">No se pudo leer la ficha: <code>{error}</code></p>}
      {!f && !error && <p className="px-6 py-10 text-center text-sm text-slate-400">Leyendo publicaciones, ventas, envíos y avisos de ML…</p>}

      {f && (
        <>
          {/* ── Por cuenta: publicación, stock y venta ─────────────────────── */}
          <div className="grid gap-3 px-6 pt-4 md:grid-cols-2">
            {cuentas.map((c) => {
              const p = full(c);
              const otras = f.publicaciones.filter((x) => x.cuenta === c && !x.is_fulfillment);
              const a = aguanta(c);
              const semanas = f.ventas_semanas[c] ?? [];
              const max = Math.max(1, ...semanas.map((s) => s.unidades));
              return (
                <div key={c} className={`rounded-xl border px-4 py-3 ${c === cuenta ? "border-indigo-200 bg-indigo-50/40" : "border-slate-200"}`}>
                  <div className="flex items-center justify-between">
                    <span className="inline-flex items-center gap-2 text-[13px] font-bold text-slate-800">
                      <span className="h-2.5 w-2.5 rounded-full" style={{ background: PUNTO_CUENTA[c] }} />{c}
                    </span>
                    {p ? (
                      <a href={p.url ?? "#"} target="_blank" rel="noreferrer"
                         className="inline-flex items-center gap-1 font-mono text-[11.5px] text-indigo-600 hover:underline">
                        {p.listing_id}<ExternalLink className="h-3 w-3" />
                      </a>
                    ) : (
                      <span className="text-[11px] text-slate-400"
                            title="channel.listings no tiene una publicación FULL de este SKU en esta cuenta (a veces falta aunque exista en ML).">
                        sin publicación FULL registrada
                      </span>
                    )}
                  </div>
                  <div className="mt-2.5 grid grid-cols-3 gap-2">
                    <Cifra rotulo="En FULL hoy" valor={p ? num(p.stock_full) : "?"} hueco={!p}
                           tono={p && p.stock_full === 0 ? "text-rose-700" : "text-slate-900"} />
                    <Cifra rotulo="Vende 30 d" valor={num(f.v30[c] ?? 0)} />
                    <Cifra rotulo="Aguanta" valor={a === null ? "—" : a >= 99 ? "99+ d" : `${Math.floor(a)} d`}
                           tono={a === null ? "text-slate-300" : a < 7 ? "text-rose-700" : a < 15 ? "text-amber-700" : "text-slate-900"} />
                  </div>
                  {p && (
                    <div className="mt-1.5 text-[11px] text-slate-400">
                      {p.situacion === "active" ? "activa" : p.situacion === "paused" ? "pausada" : p.situacion ?? "—"}
                      {p.price !== null ? ` · $${num(Math.round(p.price))}` : ""}
                      {otras.length ? ` · además ${otras.length} sin FULL` : ""}
                    </div>
                  )}
                  {/* Venta FULL por semana: 12 semanas seguidas, una sin venta es un cero. */}
                  <div className="mt-3 flex h-[64px] items-end gap-1" title="Ventas FULL por semana (12 semanas).">
                    {semanas.map((s) => (
                      <div key={s.lunes} className="flex min-w-0 flex-1 flex-col items-center justify-end"
                           title={`${s.semana} (desde el ${dia(`${s.lunes}T12:00:00-06:00`)}): ${s.unidades} piezas${s.actual ? " · semana en curso" : ""}`}>
                        {s.unidades > 0
                          ? <div className={`w-full rounded-t ${s.actual ? "bg-emerald-300" : "bg-emerald-600"}`}
                                 style={{ height: Math.max(3, (s.unidades / max) * 52) }} />
                          : <div className="h-[2px] w-full bg-slate-200" />}
                      </div>
                    ))}
                  </div>
                  <div className="mt-0.5 flex justify-between text-[9.5px] text-slate-400">
                    <span>{semanas[0]?.semana}</span><span>venta FULL por semana</span><span>{semanas[semanas.length - 1]?.semana}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="px-6 pt-3 text-[12px] text-slate-500">
            <b className="text-slate-700">Libre en Odoo hoy:</b>{" "}
            {f.odoo === null ? <span className="text-amber-700">Odoo no contestó</span>
              : f.odoo.libre === null ? <span className="text-rose-700">el SKU no está en Odoo</span>
              : Object.entries(f.odoo.libre).map(([alm, n]) => `${alm} ${num(n)}`).join(" · ")}
          </div>

          {/* ── Envíos que llevaron este SKU ─────────────────────────────────── */}
          <div className="px-6 pt-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
              Envíos a FULL con este SKU · {f.envios.length}
            </div>
            <div className="mt-1.5 overflow-x-auto rounded-xl border border-slate-200">
              <table className="w-full min-w-[640px] text-[12px]">
                <thead>
                  <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.05em] text-slate-400">
                    <th className="px-3 py-2">Orden · salida</th><th className="px-3 py-2">Cuenta</th>
                    <th className="px-3 py-2 text-right">Pedidas</th><th className="px-3 py-2 text-right">Enviadas</th>
                    <th className="px-3 py-2 text-right">Llegaron</th><th className="px-3 py-2">Qué pasó</th>
                  </tr>
                </thead>
                <tbody>
                  {f.envios.map((e) => {
                    const q = e.estado_llegada ? QUE_PASO[e.estado_llegada] : null;
                    return (
                      <tr key={`${e.id}-${e.orden}`} className="border-t border-slate-100">
                        <td className="px-3 py-1.5">
                          <span className="font-mono font-bold text-slate-800">{e.orden ?? "—"}</span>
                          <span className="text-slate-400"> · {e.validada ? `salió ${dia(e.validada)}` : "sin validar"}</span>
                          {e.envio && <span className="block font-mono text-[10.5px] text-slate-400">envío {e.envio}</span>}
                        </td>
                        <td className="px-3 py-1.5 text-slate-600">{e.cuenta ?? <span className="text-amber-700">sin cuenta</span>}</td>
                        <td className="px-3 py-1.5 text-right font-mono tabular-nums text-slate-500">{num(e.pedidas)}</td>
                        <td className="px-3 py-1.5 text-right font-mono tabular-nums text-slate-800">
                          {e.enviadas === null ? <span className="text-amber-700">sin validar</span> : num(e.enviadas)}
                        </td>
                        <td className="px-3 py-1.5 text-right font-mono font-bold tabular-nums text-slate-800">
                          {e.llegadas === undefined ? <span className="font-normal text-slate-300">—</span> : num(e.llegadas)}
                        </td>
                        <td className={`px-3 py-1.5 font-semibold ${q?.c ?? "text-slate-300"}`}>
                          {e.enviadas === 0 && e.estado_odoo === "done" ? <span className="text-amber-700">Odoo no la surtió</span>
                            : q ? `${q.t}${e.rechazadas ? ` (${num(e.rechazadas)})` : ""}` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                  {f.envios.length === 0 && (
                    <tr><td colSpan={6} className="px-3 py-4 text-center text-slate-400">Ninguna salida de Odoo a FULL llevó este SKU.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── Avisos de llegada de ML ──────────────────────────────────────── */}
          <div className="px-6 pb-5 pt-4">
            <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
              Avisos de llegada a FULL de Mercado Libre · últimos 120 días
            </div>
            {f.llegadas.length ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {f.llegadas.slice(0, 40).map((l) => (
                  <span key={`${l.ts}-${l.piezas}-${l.cuenta}`}
                        title={`${l.tipo === "INBOUND_RECEPTION" ? "Recepción directa en bodega" : "Llegó desde el CEDIS"} · ${l.cuenta}`}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800">
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: PUNTO_CUENTA[l.cuenta] ?? "#cbd5e1" }} />
                      <span className="font-mono">{fecha({ ts: l.ts })}</span>
                      <b className="font-mono">+{num(l.piezas)}</b>
                    </span>
                  ))}
                {f.llegadas.length > 40 && <span className="text-[11px] text-slate-400">y {f.llegadas.length - 40} más</span>}
              </div>
            ) : (
              <p className="mt-1.5 rounded-lg px-3 py-2 text-[12px] text-slate-500" style={{ background: FONDO_RAYADO }}>
                Ningún aviso de llegada con este SKU. Si su publicación tiene variantes, ML avisa sin SKU y aquí no aparece.
              </p>
            )}
            <p className="mt-3 text-[10.5px] text-slate-400">Fuente: {f.fuente}.</p>
          </div>
        </>
      )}
    </Ventana>
  );
}

function Cifra({ rotulo, valor, tono = "text-slate-900", hueco }: {
  rotulo: string; valor: string; tono?: string; hueco?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5"
         style={hueco ? { background: FONDO_RAYADO } : undefined}>
      <div className="text-[9.5px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
      <div className={`font-mono text-base font-extrabold tabular-nums ${hueco ? "text-slate-400" : tono}`}>{valor}</div>
    </div>
  );
}
