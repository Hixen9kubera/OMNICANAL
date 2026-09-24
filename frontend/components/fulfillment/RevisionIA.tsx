"use client";

/**
 * La planeación CON IA (Brandon, 24-sep: "el por mandar es la propuesta de la
 * planeación semanal con IA y con capacidad de modificación"). Claude lee la
 * planeación ya calculada con el prompt estándar y devuelve ajustes, reemplazos y
 * alertas (backend/services/fulfillment_ia.py). Nada se aplica solo: el backend
 * descarta lo que no esté en la planeación o pase de lo libre, y aquí cada ajuste
 * se acepta con un clic.
 */

import { useState } from "react";
import { CheckCircle2, Sparkles, X } from "lucide-react";
import type { Renglon } from "./proponer";
import { Ceja, num } from "./ui";
import type { ParametrosFull, PropuestaFull, RevisionIA as Revision, Tienda } from "./tipos";

export interface EstadoIA {
  estado: "corriendo" | "listo" | "error";
  segundos?: number;
  resultado?: Revision;
  motivo?: string;
}

/** Lo que se le manda a la IA: compacto y sólo lo que importa (≤250 renglones por tienda). */
export function datosParaIA(renglones: Renglon[], cantidad: (r: Renglon) => number, p: ParametrosFull,
                            datos: PropuestaFull, activas: Tienda[]) {
  const tiendas: Record<string, unknown> = {};
  for (const t of activas) {
    const rs = renglones.filter((r) => r.tienda === t);
    const importantes = rs
      .filter((r) => r.pidio > 0 || cantidad(r) > 0 || r.estado === "pendiente" || r.alertas.length > 0)
      .sort((a, b) => cantidad(b) - cantidad(a) || b.pidio - a.pidio)
      .slice(0, 250);
    tiendas[t] = {
      nombre: datos.tiendas[t].nombre,
      destino: datos.tiendas[t].destino,
      renglones: importantes.map((r) => ({
        sku: r.sku, nombre: r.nombre, titulo_marketplace: r.titulo_mkt, vendio: r.vv, vendio_7d: r.v7,
        en_almacen: r.stock, en_camino: r.en_camino, borrador: r.borrador, libre: r.bodega ?? 0,
        pidio: r.pidio, propuesta: r.propuesta, a_mandar: cantidad(r), estado: r.estado, caja: r.caja,
        alertas: r.alertas, verificada_en_vivo: r.verificada,
      })),
      ganadores_agotados: rs.filter((r) => r.ganador_agotado).slice(0, 80).map((r) => ({
        sku: r.sku, nombre: r.nombre, vendio: r.vv, candidatos: r.reemplazos,
      })),
    };
  }
  return {
    corrida: {
      semana: `${datos.semana.semana} · ${datos.semana.lunes} a ${datos.semana.domingo}`,
      tiendas: activas.map((t) => datos.tiendas[t].nombre),
      ventana: `${datos.ventana.dias} días (${datos.ventana.desde} a ${datos.ventana.hasta})`,
      cobertura_dias: p.cobertura_dias, min_piezas: p.min_piezas, ganador_desde: p.min_ventas,
      dejar_en_bodega: p.dejar_en_bodega,
      en_vivo: "Mercado Libre verificado en vivo; Odoo (libre) en vivo; ventas y stock FBA del sync de 15 min; "
        + "Walmart sin ventas registradas y sin stock de WFS legible",
      renglones_pendientes: renglones.filter((r) => r.estado === "pendiente").length,
    },
    tiendas,
  };
}

export default function PanelIA({ ia, datos, onAplicar, onAgregarReemplazo, onCerrar }: {
  ia: EstadoIA;
  datos: PropuestaFull;
  onAplicar: (ajustes: Revision["ajustes"]) => void;
  onAgregarReemplazo: (tienda: Tienda, sku: string) => void;
  onCerrar: () => void;
}) {
  const r = ia.resultado;
  const nombre = (t: string) => datos.tiendas[t as Tienda]?.nombre ?? t;
  // Lo ya aplicado o agregado se marca: la lista es larga y se pierde la cuenta.
  const [hechos, setHechos] = useState<Set<string>>(new Set());
  const marcar = (claves: string[]) => setHechos((h) => new Set([...h, ...claves]));
  const hecho = (texto: string) => (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700">
      <CheckCircle2 className="h-3.5 w-3.5" /> {texto}
    </span>
  );
  return (
    <section className="rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-50 to-indigo-50 p-[18px]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-600" />
          <Ceja>Revisión con IA · prompt estándar de planeación semanal</Ceja>
        </div>
        <button type="button" onClick={onCerrar} title="Cerrar la revisión"
                className="rounded-lg p-1 text-slate-400 hover:bg-white hover:text-slate-700">
          <X className="h-4 w-4" />
        </button>
      </div>

      {ia.estado === "corriendo" && (
        <p className="mt-3 text-[13px] text-violet-900">
          La IA está leyendo la planeación con el prompt estándar… {ia.segundos ? `${ia.segundos} s` : ""}
          <span className="block text-[11.5px] text-violet-700/80">
            Suele tardar de 2 a 4 minutos: razona renglón por renglón antes de contestar. Puedes seguir editando.
          </span>
        </p>
      )}
      {ia.estado === "error" && (
        <p className="mt-3 rounded-lg border border-rose-200 bg-white px-3 py-2 text-[12.5px] text-rose-800">
          La revisión no se pudo hacer: {ia.motivo}
        </p>
      )}

      {r && (
        <div className="mt-3 flex flex-col gap-3">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-xl bg-white/80 px-3.5 py-3">
              <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Confirmación</div>
              <p className="mt-1 whitespace-pre-line text-[12.5px] leading-relaxed text-slate-700">{r.confirmacion}</p>
            </div>
            <div className="rounded-xl bg-white/80 px-3.5 py-3">
              <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Resumen</div>
              <p className="mt-1 whitespace-pre-line text-[12.5px] leading-relaxed text-slate-700">{r.resumen}</p>
            </div>
          </div>

          <div className="rounded-xl bg-white/80 px-3.5 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                Ajustes sugeridos · {r.ajustes.length}
              </div>
              {r.ajustes.length > 1 && (
                <button type="button" onClick={() => { onAplicar(r.ajustes); marcar(r.ajustes.map((a) => `${a.tienda}|${a.sku}`)); }}
                        className="rounded-lg bg-violet-600 px-3 py-1 text-[11.5px] font-bold text-white hover:bg-violet-700">
                  Aplicar todos
                </button>
              )}
            </div>
            <div className="mt-1.5 divide-y divide-slate-100">
              {r.ajustes.map((a) => (
                <div key={`${a.tienda}|${a.sku}`} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-[12.5px]">
                  <span className="min-w-0">
                    <span className="font-mono font-bold text-slate-800">{a.sku}</span>
                    <span className="text-slate-400"> · {nombre(a.tienda)} → </span>
                    <b className="font-mono">{num(a.cantidad)}</b>
                    <span className="block text-[11.5px] text-slate-500">{a.motivo}{a.nota ? ` (${a.nota})` : ""}</span>
                  </span>
                  {hechos.has(`${a.tienda}|${a.sku}`) ? hecho("aplicado") : (
                    <button type="button" onClick={() => { onAplicar([a]); marcar([`${a.tienda}|${a.sku}`]); }}
                            className="rounded-md border border-violet-200 px-2 py-1 text-[11px] font-bold text-violet-700 hover:bg-violet-50">
                      Aplicar
                    </button>
                  )}
                </div>
              ))}
              {r.ajustes.length === 0 && (
                <p className="py-1.5 text-[12px] text-slate-500">
                  {r.descartados.length ? "Ninguna sugerencia pasó la validación: abajo, por qué." : "Sin cambios: la propuesta le parece bien."}
                </p>
              )}
            </div>
          </div>

          {r.reemplazos.length > 0 && (
            <div className="rounded-xl bg-white/80 px-3.5 py-3">
              <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Reemplazos de ganadores agotados</div>
              <div className="mt-1.5 divide-y divide-slate-100">
                {r.reemplazos.map((x) => (
                  <div key={`${x.tienda}|${x.agotado}`} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-[12.5px]">
                    <span className="min-w-0">
                      <span className="font-mono text-slate-500 line-through">{x.agotado}</span> →{" "}
                      <span className="font-mono font-bold text-slate-800">{x.reemplazo}</span>
                      <span className="text-slate-400"> · {nombre(x.tienda)} · {x.tipo_match}</span>
                      <span className="block text-[11.5px] text-slate-500">{x.motivo}</span>
                    </span>
                    {hechos.has(`r|${x.tienda}|${x.reemplazo}`) ? hecho("agregado") : (
                      <button type="button"
                              onClick={() => { onAgregarReemplazo(x.tienda, x.reemplazo); marcar([`r|${x.tienda}|${x.reemplazo}`]); }}
                              className="rounded-md border border-violet-200 px-2 py-1 text-[11px] font-bold text-violet-700 hover:bg-violet-50">
                        Agregar a la planeación
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {r.alertas.length > 0 && (
            <div className="rounded-xl bg-white/80 px-3.5 py-3">
              <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Alertas de la IA</div>
              <ul className="mt-1 list-inside list-disc text-[12px] text-slate-700">
                {r.alertas.map((a, i) => (
                  <li key={i}><span className="font-mono">{a.sku}</span> · {a.tipo}: {a.detalle}</li>
                ))}
              </ul>
            </div>
          )}

          {r.descartados.length > 0 && (
            <details className="rounded-xl bg-white/80 px-3.5 py-2.5 text-[12px] text-slate-600">
              <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                Descartadas por el panel · {r.descartados.length}
              </summary>
              <ul className="mt-1.5 list-inside list-disc">
                {r.descartados.map((d, i) => (
                  <li key={i}>
                    <span className="font-mono">{String(d.sku ?? d.reemplazo ?? "—")}</span>
                    {d.tienda ? ` · ${nombre(String(d.tienda))}` : ""} · {String(d.porque ?? "")}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className="text-[10.5px] text-slate-500">
            {r.modelo ?? "Claude"} · {num(r.tokens?.entrada ?? null)} tokens de entrada, {num(r.tokens?.salida ?? null)} de salida.
            {" "}Lo que la IA propone se valida contra la planeación: fuera de ella o por encima de lo libre no pasa.
          </p>
        </div>
      )}
    </section>
  );
}
