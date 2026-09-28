"use client";

/**
 * «CARGAR FULL CON PROMPT» (Brandon, 28-sep-2026): "una vez que presionaron el botón
 * aparecerá en un POP el prompt para copiar junto con sus SKUs y cantidades a mandar, y
 * una vez cierren el pop este prompt persistirá en un botón llamado CARGAR FULL CON
 * PROMPT… solo se activa en la week over week y cuando se haya creado correctamente la
 * orden de venta en Odoo EN STATUS BORRADOR, hasta que reciban el documento de GUÍA".
 *
 * Mercado Libre no deja crear envíos a Full por API: sólo en su panel. Por eso el panel
 * arma las instrucciones EXACTAS (backend: `fulfillment_full.prompt_ml`, con los
 * renglones releídos de Odoo y la publicación de cada SKU) para pegarlas en un chat NUEVO
 * de Claude con acceso al navegador. El agente crea el envío, descarga sus documentos y
 * los sube aquí como la guía de la orden; con la guía adjunta, el botón se apaga solo.
 */

import { useEffect, useState } from "react";
import { CheckCircle2, ClipboardCopy, ExternalLink, FlaskConical } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { BotonCerrar, Ceja, Ventana, num } from "./ui";
import type { PromptML } from "./tipos";

export interface OrdenPrompt { id: number; orden: string; almacen?: string | null }

/** UNA cuenta con sus órdenes de la semana (una por almacén): un prompt por cuenta. */
export interface GrupoPrompt { tienda: string; nombre: string; ordenes: OrdenPrompt[] }

export default function PromptFull({ grupos, aviso, onCerrar }: {
  grupos: GrupoPrompt[];
  /** Lo que acaba de pasar (p. ej. «Creadas en Odoo en borrador: S38990, S38991»). */
  aviso?: string | null;
  onCerrar: () => void;
}) {
  const [elegida, setElegida] = useState(grupos[0]?.tienda ?? "");
  const [datos, setDatos] = useState<Record<string, PromptML | { ok: false; motivo: string }>>({});
  const [copiado, setCopiado] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    const panel = encodeURIComponent(window.location.origin);
    for (const g of grupos) {
      const ids = g.ordenes.map((o) => o.id).join(",");
      fetchSesion(`${API_BASE}/api/fulfillment/crear-full/prompt-ml?orden_ids=${ids}&panel=${panel}`, { cache: "no-store" })
        .then(async (r) => (r.ok ? r.json() as Promise<PromptML> : { ok: false as const, motivo: `HTTP ${r.status}` }))
        .catch((e: unknown) => ({ ok: false as const, motivo: e instanceof Error ? e.message : String(e) }))
        .then((d) => { if (vivo) setDatos((x) => ({ ...x, [g.tienda]: d })); });
    }
    return () => { vivo = false; };
  }, [grupos]);

  const d = datos[elegida];
  const p = d && d.ok ? (d as PromptML) : null;
  const copiar = (que: string, texto: string) => {
    void navigator.clipboard?.writeText(texto);
    setCopiado(que);
    setTimeout(() => setCopiado(null), 1800);
  };
  const skus = p?.lineas?.map((l) => `${l.sku}\t${l.cantidad}\t${l.almacen ?? ""}`).join("\n") ?? "";

  return (
    <Ventana etiqueta="Cargar FULL con prompt" onCerrar={onCerrar} ancho="max-w-[980px]">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
        <div>
          <Ceja>Cargar FULL con prompt · Mercado Libre</Ceja>
          <h3 className="mt-1 text-lg font-extrabold text-slate-900">Copia el prompt y pégalo en un chat nuevo de Claude</h3>
          <p className="mt-0.5 max-w-2xl text-[12.5px] text-slate-500">
            Ábrelo en un chat NUEVO de Claude que tenga acceso a tu navegador (Claude in Chrome), con la sesión de
            Mercado Libre de la cuenta correcta abierta. El agente arma el envío a Full con estos SKUs y piezas, te
            pregunta la forma de entrega y la cita, descarga sus documentos y los sube aquí como la guía de la orden.
            Si cierras esta ventana, el prompt sigue en el botón «CARGAR FULL CON PROMPT» hasta que la guía quede adjunta.
          </p>
        </div>
        <BotonCerrar onClick={onCerrar} />
      </div>

      {grupos.length > 1 && (
        <div className="flex flex-wrap gap-1.5 border-b border-slate-100 px-6 py-2">
          {grupos.map((g) => (
            <button key={g.tienda} type="button" onClick={() => setElegida(g.tienda)}
                    className={`rounded-full border px-3 py-1 text-[12px] font-bold ${
                      elegida === g.tienda ? "border-indigo-300 bg-indigo-50 text-indigo-800" : "border-slate-200 text-slate-500 hover:bg-slate-50"}`}>
              {g.nombre} · {g.ordenes.map((o) => o.orden).join(", ")}
            </button>
          ))}
        </div>
      )}

      <div className="max-h-[64vh] overflow-y-auto px-6 py-4">
        {aviso && (
          <p className="mb-3 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12.5px] font-semibold text-emerald-900">
            <CheckCircle2 className="h-4 w-4 shrink-0" /> {aviso}
          </p>
        )}
        {!d && <p className="py-8 text-center text-sm text-slate-400">Releyendo la orden en Odoo y sus publicaciones…</p>}
        {d && !d.ok && (
          <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] text-rose-800">
            No se pudo armar el prompt: {d.motivo}
          </p>
        )}
        {p && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-slate-600">
              <span className="text-[14px] font-extrabold text-slate-900">{p.nombre_tienda}</span>
              <span>
                · {(p.ordenes ?? []).map((o) => `${o.orden} (${o.almacen ?? "—"})`).join(" · ")}
                {" "}· {num(p.lineas?.length ?? 0)} SKUs · {num(p.piezas ?? 0)} pzs
              </span>
              {p.orden?.prueba && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-bold text-amber-800">
                  <FlaskConical className="h-3 w-3" /> PRUEBA: el agente no confirma nada
                </span>
              )}
              {p.url && (
                <a href={p.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-indigo-600 hover:underline">
                  ver en Odoo <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
            {!p.activo && (
              <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">
                El botón ya no va activo para esta cuenta: {p.porque}. El prompt se enseña sólo de consulta.
              </p>
            )}

            <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
              <div className="min-w-0">
                <div className="flex items-center justify-between">
                  <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">SKUs y piezas a mandar</span>
                  <button type="button" onClick={() => copiar("skus", skus)}
                          className="inline-flex items-center gap-1 text-[11.5px] font-semibold text-indigo-600 hover:underline">
                    {copiado === "skus" ? <><CheckCircle2 className="h-3.5 w-3.5" /> copiados</> : <><ClipboardCopy className="h-3.5 w-3.5" /> copiar</>}
                  </button>
                </div>
                <div className="mt-1 max-h-[46vh] overflow-y-auto rounded-xl border border-slate-200">
                  <table className="w-full text-[12px]">
                    <thead className="sticky top-0 bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.05em] text-slate-400">
                      <tr><th className="px-3 py-1.5">SKU · publicación</th><th className="px-3 py-1.5">Sale de</th>
                        <th className="px-3 py-1.5 text-right">Piezas</th></tr>
                    </thead>
                    <tbody>
                      {p.lineas?.map((l) => (
                        <tr key={`${l.orden}|${l.sku}`} className="border-t border-slate-100">
                          <td className="px-3 py-1.5">
                            <span className="font-mono font-bold text-slate-800">{l.sku}</span>
                            <span className="block font-mono text-[10.5px] text-slate-400">{l.listing_id ?? "sin publicación registrada"}</span>
                          </td>
                          <td className="px-3 py-1.5 text-[11px] text-slate-500">{l.almacen ?? "—"}<span className="block font-mono text-[10px] text-slate-400">{l.orden}</span></td>
                          <td className="px-3 py-1.5 text-right font-mono font-bold tabular-nums">{num(l.cantidad)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="min-w-0">
                <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">El prompt</span>
                <textarea readOnly value={p.prompt ?? ""} rows={18} onFocus={(ev) => ev.target.select()}
                          className="mt-1 w-full resize-y rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-700" />
              </div>
            </div>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-2xl border-t border-slate-100 bg-slate-50/60 px-6 py-3">
        <span className="text-[11.5px] text-slate-500">
          El agente trabaja con TU sesión del navegador y te pregunta antes de elegir la entrega y la cita.
        </span>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onCerrar}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Cerrar
          </button>
          <button type="button" onClick={() => p?.prompt && copiar("prompt", p.prompt)} disabled={!p?.prompt}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-40">
            {copiado === "prompt" ? <><CheckCircle2 className="h-4 w-4" /> Prompt copiado</> : <><ClipboardCopy className="h-4 w-4" /> Copiar el prompt</>}
          </button>
        </div>
      </div>
    </Ventana>
  );
}
