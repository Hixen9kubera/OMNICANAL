"use client";

/**
 * RastroCambio — panel lateral con un cambio salto por salto: cuándo cambió
 * Woo, cuánto esperó su turno, cuánto tardó en escribirse y qué contestó cada
 * canal. El registro guarda UN tiempo por cambio (no uno por canal), así que
 * el tramo de cada canal va rayado: se sabe que pasó ahí dentro, no cuánto duró.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { Destino, Rastro } from "./tipos";
import { CELDA_CLS, TONO_COLOR } from "./tipos";

const RAYA = "repeating-linear-gradient(135deg, #a5b4fc 0 3px, #eef2ff 3px 7px)";
const RAYA_MAL = "repeating-linear-gradient(135deg, #fb7185 0 3px, #fff1f2 3px 7px)";

function lista(nombres: string[]): string {
  if (nombres.length <= 1) return nombres.join("");
  return `${nombres.slice(0, -1).join(", ")} y ${nombres[nombres.length - 1]}`;
}

function diagnostico(r: Rastro): { tono: "mal" | "ok" | "full"; titulo: string; texto: string } {
  const de = (k: Destino["k"]) => r.destinos.filter((d) => !d.fuera && d.k === k);
  const ok = de("ok").map((d) => d.nombre);
  const mal = de("mal");
  const full = de("full").map((d) => d.nombre);
  const omit = de("omit");
  const esc = (r.ms / 1000).toLocaleString("es-MX", { maximumFractionDigits: 1 });
  const partes: string[] = [];
  if (r.woo_hora && r.espera_s != null) {
    partes.push(`Woo cambió a las ${r.woo_hora}. Esperó ${Math.round(r.espera_s)} s su turno y se resolvió en ${esc} s.`);
  } else {
    partes.push(`Se resolvió en ${esc} s${r.origen === "venta" ? "; vino de una venta, no de una pasada de stock_watch" : ""}.`);
  }
  if (mal.length) {
    const permiso = mal.some((d) => (d.detalle || "").includes("PolicyAgent") || (d.detalle || "").includes("PA_UNAUTHORIZED"));
    partes.push(`${lista(mal.map((d) => d.nombre))} respondió ${mal[0].texto.split(" ")[0]}`
      + (permiso ? ": la app que escribe no está autorizada en esa cuenta, así que la publicación no cambia." : "."));
  }
  omit.forEach((d) => partes.push(`En ${d.nombre}: ${d.texto}.`));
  if (full.length) partes.push(`${lista(full)} es FULL: ese stock lo surte la bodega de Mercado Libre.`);

  let titulo: string;
  let tono: "mal" | "ok" | "full" = "ok";
  if (mal.length && ok.length) {
    titulo = `Llegó a ${lista(ok)}; ${lista(mal.map((d) => d.nombre))} lo rechazó.`;
    tono = "mal";
  } else if (mal.length) {
    titulo = `${lista(mal.map((d) => d.nombre))} lo rechazó.`;
    tono = "mal";
  } else if (ok.length) {
    titulo = `Llegó a ${lista(ok)}${r.total_s != null ? ` en ${Math.round(r.total_s)} s` : ""}.`;
  } else if (full.length && !omit.length) {
    titulo = "No había nada que escribir: es FULL.";
    tono = "full";
  } else {
    titulo = "No llegó a ningún canal.";
    tono = "full";
  }
  return { tono, titulo, texto: partes.join(" ") };
}

export default function RastroCambio({ sel, onCerrar, onIr, onTrazabilidad }: {
  sel: { sku: string; fin: string } | null;
  onCerrar: () => void;
  onIr: (sku: string, fin: string) => void;
  onTrazabilidad?: (sku: string) => void;
}) {
  const [r, setR] = useState<Rastro | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cerrarRef = useRef<HTMLButtonElement>(null);

  const cargar = useCallback(async (s: { sku: string; fin: string }) => {
    setR(null);
    setError(null);
    try {
      const q = new URLSearchParams({ sku: s.sku, fin: s.fin });
      const res = await fetchSesion(`${API_BASE}/api/fanout/rastro?${q}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = (await res.json()) as Rastro;
      if (!d.ok) throw new Error("No encontré ese cambio en la bitácora.");
      setR(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    if (sel) {
      void cargar(sel);
      cerrarRef.current?.focus();
    }
  }, [sel, cargar]);

  if (!sel) return null;

  const total = r ? (r.total_s ?? r.ms / 1000) : 0;
  const eje = Math.max(20, Math.ceil(total / 20) * 20);
  const pct = (s: number) => `${Math.min(100, (s / eje) * 100)}%`;
  const espera = r?.espera_s ?? 0;
  const escritura = r ? r.ms / 1000 : 0;
  const diag = r ? diagnostico(r) : null;
  const marcas = Array.from({ length: Math.floor(eje / 20) + 1 }, (_, i) => i * 20);
  const dts = r?.fila.map((f) => f.dt) ?? [];
  const dmin = Math.min(0, ...dts);
  const dmax = Math.max(1, ...dts);
  const posFila = (dt: number) => `${((dt - dmin) / (dmax - dmin || 1)) * 100}%`;

  const fila = (etiqueta: string, sub: string, barra: ReactNode, resultado: ReactNode, sangria = false) => (
    <div className="grid min-h-[42px] items-center gap-3 border-t border-slate-100" style={{ gridTemplateColumns: "172px minmax(0,1fr) 150px" }}>
      <span className={`flex min-w-0 flex-col ${sangria ? "pl-4" : ""}`}>
        <span className={`truncate text-[13px] leading-[18px] text-slate-900 ${sangria ? "font-medium" : "font-semibold"}`}>{etiqueta}</span>
        <span className="truncate text-[11px] leading-[15px] text-slate-500">{sub}</span>
      </span>
      <div className="relative h-[42px] border-r border-slate-200"
        style={{ backgroundImage: `repeating-linear-gradient(90deg, #e2e8f0 0, #e2e8f0 1px, transparent 1px, transparent ${100 / (marcas.length - 1)}%)` }}>
        {barra}
      </div>
      <span className="justify-self-start">{resultado}</span>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Rastro del cambio"
      onKeyDown={(e) => { if (e.key === "Escape") onCerrar(); }}>
      <button type="button" aria-label="Cerrar" className="absolute inset-0 cursor-default bg-slate-900/30" onClick={onCerrar} />
      <div className="relative flex h-full w-full max-w-[780px] flex-col overflow-y-auto bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
          <div className="min-w-0">
            <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">Rastro del cambio</div>
            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="break-all font-mono text-base font-bold text-slate-900">{sel.sku}</span>
              {r && <span className="text-sm text-slate-700">{r.cambio}</span>}
            </div>
            {r && (
              <div className="mt-0.5 text-[13px] text-slate-600">
                {r.woo_hora ? `Woo ${r.woo_hora} → terminó ${r.hora}` : `Terminó ${r.hora}`}
                {r.total_s != null ? ` · ${Math.round(r.total_s)} s en total` : ""}
              </div>
            )}
            {onTrazabilidad && (
              <button type="button" onClick={() => onTrazabilidad(sel.sku)}
                className="mt-1.5 text-[13px] font-semibold text-indigo-700 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
                Ver toda la trazabilidad del SKU
              </button>
            )}
          </div>
          <button ref={cerrarRef} type="button" onClick={onCerrar}
            className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500">
            <X size={18} aria-hidden /><span className="sr-only">Cerrar</span>
          </button>
        </div>

        <div className="flex flex-col gap-4 px-6 py-5">
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
          {!r && !error && <p className="text-sm text-slate-500">Leyendo la bitácora…</p>}

          {r && diag && (
            <div className={`flex gap-3 rounded-xl border px-4 py-3.5 ${
              diag.tono === "mal" ? "border-rose-200 bg-rose-50" : diag.tono === "ok" ? "border-emerald-200 bg-emerald-50" : "border-sky-200 bg-sky-50"}`}>
              {diag.tono === "mal" && <XCircle size={20} className="mt-0.5 shrink-0 text-rose-600" aria-hidden />}
              {diag.tono === "ok" && <CheckCircle2 size={20} className="mt-0.5 shrink-0 text-emerald-600" aria-hidden />}
              {diag.tono === "full" && <Info size={20} className="mt-0.5 shrink-0 text-sky-600" aria-hidden />}
              <div className="min-w-0">
                <div className="text-[15px] font-bold leading-[22px] text-slate-900">{diag.titulo}</div>
                <div className="mt-1 text-[13px] leading-5 text-slate-700">{diag.texto}</div>
              </div>
            </div>
          )}

          {r && (
            <div className="overflow-x-auto">
              <div className="min-w-[640px]">
                <div className="grid items-end gap-3 pb-1.5" style={{ gridTemplateColumns: "172px minmax(0,1fr) 150px" }}>
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Salto</span>
                  <div className="relative h-4 text-[11px] leading-4 text-slate-500">
                    {marcas.map((m, i) => (
                      <span key={m} className="absolute whitespace-nowrap"
                        style={i === 0 ? { left: 0 } : i === marcas.length - 1 ? { right: 0 } : { left: pct(m), transform: "translateX(-50%)" }}>
                        {m} s
                      </span>
                    ))}
                  </div>
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Resultado</span>
                </div>
                {r.woo_hora && fila("Woo cambia", `stock_watch · ${r.woo_hora}`,
                  <span className="absolute top-[14px] -ml-[7px] h-3.5 w-3.5 rounded-full border-[3px] border-white bg-indigo-600 shadow-[0_0_0_1px_#4f46e5]" style={{ left: 0 }} />,
                  <span className="rounded-lg bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-700 ring-1 ring-inset ring-slate-200">{r.cambio}</span>)}
                {r.woo_hora && fila("Esperando turno", "de Woo al fan-out",
                  <span className="absolute top-[13px] h-4 min-w-1 rounded bg-slate-300" style={{ left: 0, width: pct(espera) }} />,
                  <span className="rounded-lg bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-700 ring-1 ring-inset ring-slate-200">{Math.round(espera)} s</span>)}
                {fila("Escribe en los canales", "un solo tiempo para todos",
                  <span className="absolute top-[13px] h-4 min-w-1 rounded bg-indigo-500" style={{ left: pct(espera), width: pct(escritura) }} />,
                  <span className="rounded-lg bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-700 ring-1 ring-inset ring-slate-200">
                    {escritura.toLocaleString("es-MX", { maximumFractionDigits: 1 })} s
                  </span>)}
                {r.destinos.map((d) => {
                  // `apag` = el seguro de stock 0 la sacó de la venta; el 0 pudo quedar escrito igual.
                  const escribe = d.k === "ok" || d.k === "mal" || (d.k === "apag" && d.escrito === true);
                  return (
                    <div key={`${d.canal}-${d.nombre}`}>
                      {fila(d.nombre, d.fuera ? "fuera del reparto" : escribe ? (d.k === "mal" ? "rechazado" : "escrito") : "no se escribe",
                        escribe
                          ? <span className="absolute top-[13px] h-4 min-w-1 rounded border" style={{ left: pct(espera), width: pct(escritura),
                              background: d.k === "mal" ? RAYA_MAL : RAYA, borderColor: d.k === "mal" ? "#e11d48" : "#818cf8" }} />
                          : <span className="absolute top-[13px] h-4 w-0.5 bg-slate-500" style={{ left: pct(espera) }} />,
                        <span title={d.detalle || undefined}
                          className={`inline-block max-w-[150px] truncate rounded-lg px-2 py-1 text-xs font-semibold ${d.fuera ? "border border-dashed border-slate-400 bg-white text-slate-600" : CELDA_CLS[d.k]}`}>
                          {d.fuera ? "fuera del reparto" : d.texto}
                        </span>, true)}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {r && (
            <>
              <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-600">
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-[18px] rounded-sm bg-slate-300" />esperando turno</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-[18px] rounded-sm bg-indigo-500" />escribiendo</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-[18px] rounded-sm border border-indigo-400" style={{ background: RAYA }} />por canal: aún sin medir</span>
                <span className="flex items-center gap-1.5"><span className="h-3 w-0.5 bg-slate-500" />no se escribe</span>
              </div>
              <p className="text-xs leading-[18px] text-slate-600">
                El rayado es lo que el registro todavía no separa: hoy guarda un solo tiempo por cambio, no uno por canal.
              </p>

              <div className="rounded-xl border border-slate-200 p-4">
                <div className="text-sm font-bold text-slate-900">Lo que contestó cada canal</div>
                <div className="mt-2 flex flex-col divide-y divide-slate-100">
                  {r.destinos.map((d) => (
                    <div key={`${d.canal}-${d.nombre}-x`} className="flex flex-col gap-0.5 py-2 sm:flex-row sm:items-baseline sm:gap-3">
                      <span className="w-28 shrink-0 text-xs font-semibold text-slate-800">{d.nombre}</span>
                      <span className="break-all font-mono text-[11px] leading-4 text-slate-600">{d.detalle || d.texto}</span>
                    </div>
                  ))}
                </div>
              </div>

              {r.fila.length > 1 && (
                <div className="rounded-xl border border-slate-200 p-4">
                  <div className="text-sm font-bold text-slate-900">La fila de la misma pasada</div>
                  <p className="mt-0.5 text-xs leading-[18px] text-slate-600">
                    {r.fila.length} cambios salieron de uno en uno entre {r.fila[0].hora} y {r.fila[r.fila.length - 1].hora}. Toca un punto para ver su rastro.
                  </p>
                  <div className="relative mx-2 mt-3 h-10">
                    <div className="absolute inset-x-0 top-[13px] h-0.5 bg-slate-200" />
                    {r.fila.map((f) => (
                      <button key={`${f.sku}-${f.fin}`} type="button" onClick={() => onIr(f.sku, f.fin)}
                        title={`${f.sku} · ${f.hora}`} aria-label={`${f.sku} a las ${f.hora}`}
                        className="absolute -ml-[7px] h-3.5 w-3.5 rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
                        style={{ left: posFila(f.dt), top: 7, background: TONO_COLOR[f.tono],
                          boxShadow: f.este ? "0 0 0 3px #ffffff, 0 0 0 5px #1e293b" : undefined }} />
                    ))}
                    <span className="absolute left-0 top-6 text-[11px] text-slate-500">{r.fila[0].hora}</span>
                    <span className="absolute right-0 top-6 text-[11px] text-slate-500">{r.fila[r.fila.length - 1].hora}</span>
                  </div>
                  <p className="mt-1 text-[11px] text-slate-500">Rojo: ML lo rechazó · verde: llegó · azul: FULL · gris: no tenía a quién escribirle.</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
