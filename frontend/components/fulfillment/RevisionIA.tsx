"use client";

/**
 * El AGENTE de planeación (Brandon, 24-sep: "que pueda comentarle como si fuera un
 * AGENTE para determinar la planeación de la semana según los datos que tenemos…
 * por ejemplo, toma todos los que tengan ticket menor de 300"). La persona escribe
 * instrucciones y sigue la conversación; Claude lee la planeación COMPLETA de las
 * tiendas activas, con el precio de cada SKU, y contesta qué hizo, qué ajustes
 * propone y qué recomienda (backend/services/fulfillment_ia.py).
 *
 * Nada se aplica solo: el backend descarta lo que no esté en la planeación o pase
 * de lo libre, y aquí cada ajuste se acepta con un clic.
 */

import { useState } from "react";
import { CheckCircle2, MessageSquarePlus, Send, Sparkles, X } from "lucide-react";
import type { Renglon } from "./proponer";
import { Ceja, num } from "./ui";
import type { ParametrosFull, PropuestaFull, RevisionIA as Revision, Tienda } from "./tipos";

export interface TurnoIA {
  id: number;
  /** Lo que escribió la persona. Vacío = revisión con el prompt estándar. */
  instruccion: string;
  estado: "corriendo" | "listo" | "error";
  segundos?: number;
  resultado?: Revision;
  motivo?: string;
}

const EJEMPLOS = [
  "Toma sólo los SKUs con precio menor a $300",
  "Prioriza lo que más vendió en los últimos 7 días",
  "Redondea a cajas completas cuando se pueda",
  "No mandes reciclados ni publicaciones con alertas",
  "¿Qué me recomiendas comprar esta semana?",
];

// El orden de las columnas es el contrato con el backend (fulfillment_ia.renglones_de).
const COLUMNAS = ["sku", "nombre", "precio", "vendio", "vendio_7d", "en_almacen", "en_camino", "borrador",
  "libre", "pidio", "propuesta", "a_mandar", "estado", "caja", "alertas", "titulo_mkt"];

/**
 * Lo que se le manda a la IA: la planeación COMPLETA de cada tienda activa en tabla
 * compacta (columnas + filas). Como objetos pesaba tres veces más.
 */
export function datosParaIA(renglones: Renglon[], cantidad: (r: Renglon) => number, p: ParametrosFull,
                            datos: PropuestaFull, activas: Tienda[]) {
  const tiendas: Record<string, unknown> = {};
  for (const t of activas) {
    const rs = renglones.filter((r) => r.tienda === t);
    tiendas[t] = {
      nombre: datos.tiendas[t].nombre,
      destino: datos.tiendas[t].destino,
      columnas: COLUMNAS,
      filas: rs.slice(0, 1500).map((r) => [
        r.sku, (r.nombre ?? "").slice(0, 60), r.precio, r.vv, r.v7, r.stock, r.en_camino, r.borrador,
        r.bodega, r.pidio, r.propuesta, cantidad(r), r.estado, r.caja, r.alertas.join(",") || null,
        r.alertas.includes("reciclado") ? (r.titulo_mkt ?? "").slice(0, 70) : null,
      ]),
      ganadores_agotados: rs.filter((r) => r.ganador_agotado).slice(0, 120).map((r) => ({
        sku: r.sku, nombre: (r.nombre ?? "").slice(0, 60), vendio: r.vv, candidatos: r.reemplazos,
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
      en_vivo: "Mercado Libre verificado en vivo (stock, estado y precio); Odoo (libre) en vivo; ventas y stock FBA "
        + "del sync de 15 min; Walmart sin ventas registradas y sin stock de WFS legible",
      renglones_pendientes: renglones.filter((r) => r.estado === "pendiente").length,
    },
    tiendas,
  };
}

export default function PanelIA({ turnos, datos, onEnviar, onAplicar, onAgregarReemplazo, onNueva, onCerrar }: {
  turnos: TurnoIA[];
  datos: PropuestaFull;
  onEnviar: (instruccion: string) => void;
  onAplicar: (ajustes: Revision["ajustes"]) => void;
  onAgregarReemplazo: (tienda: Tienda, sku: string) => void;
  onNueva: () => void;
  onCerrar: () => void;
}) {
  const [texto, setTexto] = useState("");
  const corriendo = turnos.some((t) => t.estado === "corriendo");
  const enviar = () => {
    if (corriendo) return;
    onEnviar(texto.trim());
    setTexto("");
  };
  return (
    <section className="rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-50 to-indigo-50 p-[18px]">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-600" />
          <Ceja>Agente de planeación · prompt estándar</Ceja>
        </div>
        <div className="flex items-center gap-1">
          {turnos.length > 0 && (
            <button type="button" onClick={onNueva} disabled={corriendo}
                    className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11.5px] font-semibold text-violet-700 hover:bg-white disabled:opacity-40">
              <MessageSquarePlus className="h-3.5 w-3.5" /> Nueva conversación
            </button>
          )}
          <button type="button" onClick={onCerrar} title="Cerrar el agente"
                  className="rounded-lg p-1 text-slate-400 hover:bg-white hover:text-slate-700">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {turnos.length === 0 && (
        <p className="mt-2 max-w-3xl text-[12.5px] leading-relaxed text-slate-600">
          Escríbele como a un planeador: qué quieres que haga con la planeación de esta semana. Ve todos los SKUs de
          las tiendas activas con su precio, venta, stock en el almacén, lo que va en camino y lo libre en Odoo.
          Contesta qué hizo, qué ajustes propone y qué recomienda; tú decides qué aplicar.
        </p>
      )}

      <div className="mt-3 flex flex-col gap-3">
        {turnos.map((t, i) => (
          <Turno key={t.id} turno={t} ultimo={i === turnos.length - 1} datos={datos}
                 onAplicar={onAplicar} onAgregarReemplazo={onAgregarReemplazo} />
        ))}
      </div>

      <div className="mt-3 rounded-xl border border-violet-200 bg-white/90 p-3">
        <label htmlFor="instruccion-ia" className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
          {turnos.length ? "Sigue la conversación" : "Instrucciones para la IA (opcional)"}
        </label>
        <textarea id="instruccion-ia" value={texto} rows={3} maxLength={2000}
                  onChange={(ev) => setTexto(ev.target.value)}
                  onKeyDown={(ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) enviar(); }}
                  placeholder="Ej.: toma todos los SKUs con precio menor a $300 y dime cuántas piezas quedan por cuenta"
                  className="mt-1 w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-[13px] text-slate-800 placeholder:text-slate-400 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100" />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5">
            {EJEMPLOS.map((e) => (
              <button key={e} type="button" onClick={() => setTexto(e)}
                      className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-0.5 text-[11px] font-semibold text-violet-700 hover:bg-violet-100">
                {e}
              </button>
            ))}
          </div>
          <button type="button" onClick={enviar} disabled={corriendo}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3.5 py-2 text-sm font-bold text-white shadow-sm hover:bg-violet-700 disabled:opacity-50">
            <Send className="h-4 w-4" />
            {corriendo ? "La IA está pensando…" : turnos.length ? "Enviar" : texto.trim() ? "Enviar a la IA" : "Revisar sin instrucciones"}
          </button>
        </div>
        <p className="mt-1.5 text-[10.5px] text-slate-500">
          La primera respuesta tarda unos 3 minutos; las siguientes, menos de uno. Ctrl+Enter envía. Los ajustes no
          se aplican solos: tú los aceptas.
        </p>
      </div>
    </section>
  );
}

function Turno({ turno, ultimo, datos, onAplicar, onAgregarReemplazo }: {
  turno: TurnoIA; ultimo: boolean; datos: PropuestaFull;
  onAplicar: (ajustes: Revision["ajustes"]) => void;
  onAgregarReemplazo: (tienda: Tienda, sku: string) => void;
}) {
  const r = turno.resultado;
  const nombre = (t: string) => datos.tiendas[t as Tienda]?.nombre ?? t;
  // Lo ya aplicado o agregado se marca: la lista es larga y se pierde la cuenta.
  const [hechos, setHechos] = useState<Set<string>>(new Set());
  const [abierto, setAbierto] = useState(true);
  const marcar = (claves: string[]) => setHechos((h) => new Set([...h, ...claves]));
  const hecho = (texto: string) => (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700">
      <CheckCircle2 className="h-3.5 w-3.5" /> {texto}
    </span>
  );
  const verDetalle = ultimo || abierto;

  return (
    <div className="flex flex-col gap-2">
      <div className="self-end rounded-2xl rounded-br-sm bg-violet-600 px-3.5 py-2 text-[12.5px] text-white shadow-sm sm:max-w-[75%]">
        {turno.instruccion || "Revisa la planeación con el prompt estándar."}
      </div>

      {turno.estado === "corriendo" && (
        <p className="rounded-xl bg-white/80 px-3.5 py-2.5 text-[13px] text-violet-900">
          La IA está leyendo la planeación… {turno.segundos ? `${turno.segundos} s` : ""}
          <span className="block text-[11.5px] text-violet-700/80">Puedes seguir editando mientras tanto.</span>
        </p>
      )}
      {turno.estado === "error" && (
        <p className="rounded-xl border border-rose-200 bg-white px-3.5 py-2 text-[12.5px] text-rose-800">
          No se pudo: {turno.motivo}
        </p>
      )}

      {r && (
        <div className="flex flex-col gap-2 rounded-xl bg-white/85 px-3.5 py-3">
          {r.respuesta && <p className="whitespace-pre-line text-[13px] leading-relaxed text-slate-800">{r.respuesta}</p>}
          {!ultimo && (
            <button type="button" onClick={() => setAbierto((a) => !a)}
                    className="self-start text-[11.5px] font-semibold text-violet-700 hover:underline">
              {abierto ? "Ocultar el detalle" : `Ver el detalle (${r.ajustes.length} ajustes)`}
            </button>
          )}

          {verDetalle && (
            <>
              {r.recomendaciones.length > 0 && (
                <div>
                  <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Recomendaciones</div>
                  <ul className="mt-1 list-inside list-disc text-[12.5px] leading-relaxed text-slate-700">
                    {r.recomendaciones.map((x, i) => <li key={i}>{x}</li>)}
                  </ul>
                </div>
              )}

              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                    Ajustes propuestos · {r.ajustes.length}
                  </div>
                  {r.ajustes.length > 1 && (
                    <button type="button"
                            onClick={() => { onAplicar(r.ajustes); marcar(r.ajustes.map((a) => `${a.tienda}|${a.sku}`)); }}
                            className="rounded-lg bg-violet-600 px-3 py-1 text-[11.5px] font-bold text-white hover:bg-violet-700">
                      Aplicar todos
                    </button>
                  )}
                </div>
                <div className="mt-1 max-h-[360px] divide-y divide-slate-100 overflow-y-auto">
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
                      {r.descartados.length ? "Ninguna sugerencia pasó la validación: abajo, por qué." : "Sin cambios a lo que va."}
                    </p>
                  )}
                </div>
              </div>

              {r.reemplazos.length > 0 && (
                <div>
                  <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">Reemplazos de ganadores agotados</div>
                  <div className="mt-1 max-h-[260px] divide-y divide-slate-100 overflow-y-auto">
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
                <details className="text-[12px] text-slate-700">
                  <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                    Alertas de la IA · {r.alertas.length}
                  </summary>
                  <ul className="mt-1 list-inside list-disc">
                    {r.alertas.map((a, i) => <li key={i}><span className="font-mono">{a.sku}</span> · {a.tipo}: {a.detalle}</li>)}
                  </ul>
                </details>
              )}

              <details className="text-[12px] text-slate-700">
                <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                  Confirmación y resumen
                </summary>
                <p className="mt-1 whitespace-pre-line leading-relaxed">{r.confirmacion}</p>
                <p className="mt-1.5 whitespace-pre-line leading-relaxed">{r.resumen}</p>
              </details>

              {r.descartados.length > 0 && (
                <details className="text-[12px] text-slate-600">
                  <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                    Descartadas por el panel · {r.descartados.length}
                  </summary>
                  <ul className="mt-1 list-inside list-disc">
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
                {r.modelo ?? "Claude"} · {num(r.tokens?.entrada ?? null)} tokens de entrada
                {r.tokens?.cache ? ` (${num(r.tokens.cache)} releídos de la caché)` : ""}, {num(r.tokens?.salida ?? null)} de salida.
                {" "}Lo que la IA propone se valida contra la planeación: fuera de ella o por encima de lo libre no pasa.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
