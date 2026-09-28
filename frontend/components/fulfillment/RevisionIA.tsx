"use client";

/**
 * «CREAR FULL CON IA»: el CHAT DE LA SEMANA (Brandon, 28-sep-2026).
 *
 *   · "En el apartado de Crear FULL directamente deberá aparecer Crear FULL con IA, justo
 *     por debajo el chat… se despliega con animación y aparece el chat para escribirle el
 *     prompt de la planeación."
 *   · "El chat deberá respetar el tiempo week over week para el reset": UN chat por
 *     semana, compartido por el equipo y guardado (services/fulfillment_semana.py).
 *     Entrar a la pantalla ya no empieza una conversación nueva ni llama a la IA; el
 *     lunes nace el chat de la semana nueva.
 *   · "Los ajustes propuestos se verán en vivo reflejados en el apartado de abajo": la
 *     IA arma el plan y cada SKU que escribe se pone en la tabla mientras sigue
 *     escribiendo (streaming). Aquí sólo va lo que contestó y cuánto movió; los totales
 *     los calcula el panel. Recomendaciones, alertas, confirmación y resumen ya no
 *     existen: la recomendación viaja dentro de cada ajuste (se ve en su renglón).
 *
 * Lo que la IA propone lo valida el backend (fuera de la planeación o por encima de lo
 * libre no pasa) y en la tabla cada renglón se puede desmarcar o corregir.
 */

import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Sparkles, X } from "lucide-react";
import type { Renglon } from "./proponer";
import { fecha, num } from "./ui";
import type {
  AvanceIA, ModeloIA, ParametrosFull, PropuestaFull, ResumenPlan, RespuestaIA, SemanaInfo, Tienda, TurnoSemana,
} from "./tipos";

export interface VivoIA {
  id: string;
  instruccion: string;
  modelo?: string;
  quien?: string | null;
  avance: AvanceIA | null;
}

const EJEMPLOS = [
  "Arma el FULL de la semana con el prompt estándar",
  "Toma sólo los SKUs con precio menor a $300",
  "Prioriza lo que más vendió en los últimos 7 días",
  "Redondea a cajas completas cuando se pueda",
  "No mandes reciclados ni publicaciones con alertas",
];

// El orden de las columnas es el contrato con el backend (fulfillment_ia.renglones_de). Sin
// «a_mandar»: el plan de ese momento viaja en el mensaje de cada turno, no en la tabla, para
// que la tabla (el prefijo de la conversación) no cambie y DeepSeek la relea de su caché.
const COLUMNAS = ["sku", "nombre", "precio", "vendio", "vendio_7d", "en_almacen", "en_camino", "borrador",
  "libre", "pidio", "propuesta", "estado", "caja", "alertas", "titulo_mkt"];

/**
 * Lo que se le da a la IA: la planeación COMPLETA de cada tienda activa en tabla
 * compacta (columnas + filas). El backend la guarda una vez al día.
 */
export function datosParaIA(renglones: Renglon[], p: ParametrosFull, datos: PropuestaFull, activas: Tienda[]) {
  const tiendas: Record<string, unknown> = {};
  for (const t of activas) {
    const rs = renglones.filter((r) => r.tienda === t);
    tiendas[t] = {
      nombre: datos.tiendas[t].nombre,
      destino: datos.tiendas[t].destino,
      columnas: COLUMNAS,
      filas: rs.slice(0, 1500).map((r) => [
        r.sku, (r.nombre ?? "").slice(0, 60), r.precio, r.vv, r.v7, r.stock, r.en_camino, r.borrador,
        r.bodega, r.pidio, r.propuesta, r.estado, r.caja, r.alertas.join(",") || null,
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

/** US$ con los decimales que hacen falta: los turnos de DeepSeek cuestan centavos. */
const dolares = (n: number) => `US$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
const duracion = (s?: number) => (s === undefined ? "" : s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`);
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** «lun 5 oct»: el lunes en que nace el chat de la semana siguiente. */
function siguienteLunes(domingo: string): string {
  const [a, m, d] = domingo.split("-").map(Number);
  const f = new Date(Date.UTC(a, m - 1, d + 1));
  return `lunes ${f.getUTCDate()} ${MESES[f.getUTCMonth()]}`;
}

/** Cuánto movió la respuesta de un turno. */
function movio(r: Pick<RespuestaIA, "ajustes" | "reemplazos">) {
  const puso = r.ajustes.filter((a) => a.cantidad > 0);
  return {
    skus: puso.length,
    piezas: puso.reduce((s, a) => s + a.cantidad, 0) + r.reemplazos.reduce((s, x) => s + x.cantidad, 0),
    reemplazos: r.reemplazos.length,
    sacados: r.ajustes.length - puso.length,
  };
}

export default function ChatSemana({
  semana, esActual, turnos, vivo, datos, totales, abierto, onAbrir, onEnviar, onVerIA, bloqueo, onAplicarTurno,
}: {
  semana: SemanaInfo;
  esActual: boolean;
  turnos: TurnoSemana[];
  vivo: VivoIA | null;
  datos: PropuestaFull | null;
  /** Lo que lleva el plan de la semana (lo calcula el panel, no la IA). */
  totales: ResumenPlan;
  abierto: boolean;
  onAbrir: (abierto: boolean) => void;
  onEnviar: (instruccion: string, modelo: string) => void;
  /** Enseña en la tabla lo que puso la IA. */
  onVerIA: () => void;
  /** Por qué no se puede escribir ahora (semana pasada, IA sin configurar, planeación leyéndose). */
  bloqueo?: string | null;
  /** Vuelve a poner en el plan lo que propuso un turno (lo que se quitó a mano no regresa). */
  onAplicarTurno?: (t: TurnoSemana) => void;
}) {
  const [texto, setTexto] = useState("");
  const modelos: ModeloIA[] = datos?.ia_modelos ?? [];
  const [modelo, setModelo] = useState<string>(datos?.ia_modelo ?? modelos[0]?.id ?? "deepseek-v4-pro");
  useEffect(() => { if (datos?.ia_modelo) setModelo(datos.ia_modelo); }, [datos?.ia_modelo]);
  const campo = useRef<HTMLTextAreaElement>(null);
  const lista = useRef<HTMLDivElement>(null);
  const gastado = turnos.reduce((a, t) => a + (t.resultado?.costo_usd ?? 0), 0);
  const puede = esActual && !bloqueo && !vivo;

  // Al desplegarse, el cursor va directo a escribir.
  useEffect(() => {
    if (!abierto || !esActual) return;
    const t = setTimeout(() => campo.current?.focus(), 420);
    return () => clearTimeout(t);
  }, [abierto, esActual]);
  // Lo nuevo del chat queda a la vista.
  useEffect(() => {
    lista.current?.scrollTo({ top: lista.current.scrollHeight, behavior: "smooth" });
  }, [turnos.length, vivo?.id, vivo?.avance?.fase]);

  const enviar = (t = texto) => {
    if (!puede) return;
    onEnviar(t.trim(), modelo);
    setTexto("");
  };

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-violet-200 bg-gradient-to-r from-violet-50 to-indigo-50 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[16px] font-extrabold text-violet-900">
            <Sparkles className="h-4 w-4 text-violet-600" /> Crear FULL con IA
          </div>
          <p className="text-[12px] text-violet-900/70">
            Chat de la {semana.semana} ({semana.rango}) · {turnos.length ? `${turnos.length} turno${turnos.length === 1 ? "" : "s"}` : "todavía vacío"}
            {esActual ? ` · se reinicia el ${siguienteLunes(semana.domingo)}` : " · semana cerrada: sólo consulta"}
            {gastado > 0 ? ` · ≈ ${dolares(gastado)} esta semana` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-lg bg-white/80 px-2.5 py-1 text-[12px] text-slate-700">
            <b className="font-mono tabular-nums">{num(totales.skus)}</b> SKUs a FULL ·{" "}
            <b className="font-mono tabular-nums">{num(totales.piezas)}</b> pzs ·{" "}
            <b className="font-mono tabular-nums">{num(totales.reemplazos)}</b> reemplazos
          </span>
          <button type="button" onClick={() => onAbrir(!abierto)} aria-expanded={abierto}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3.5 py-2 text-sm font-bold text-white shadow-sm transition hover:bg-violet-700">
            {vivo ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            {abierto ? "Ocultar el chat" : vivo ? "La IA está trabajando…" : turnos.length ? "Abrir el chat de la semana" : "Crear FULL con IA"}
          </button>
        </div>
      </div>

      {/* Se despliega con animación: la altura pasa de 0 a la del chat (grid 0fr → 1fr). */}
      <div className={`grid transition-[grid-template-rows,opacity] duration-500 ease-out ${
        abierto ? "grid-rows-[1fr] opacity-100" : "pointer-events-none grid-rows-[0fr] opacity-0"}`}>
        <div className="min-h-0 overflow-hidden">
          <section className="mt-2 rounded-2xl border border-violet-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="max-w-3xl text-[12.5px] leading-relaxed text-slate-600">
                {turnos.length || vivo
                  ? <>Sigue la conversación: la IA recuerda lo que ya hizo esta semana y el plan de la tabla.</>
                  : <>La {semana.semana} empieza <b>vacía</b>: pídele a la IA que arme el FULL. Ve todos los SKUs de las tiendas
                     activas con su precio, venta, stock en el almacén, lo que va en camino y lo libre en Odoo. Lo que
                     proponga se pone en la tabla de abajo <b>en vivo</b>, con su recomendación en cada renglón; tú lo
                     afinas o lo desmarcas.</>}
              </p>
              <button type="button" onClick={() => onAbrir(false)} title="Ocultar el chat"
                      className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                <X className="h-4 w-4" />
              </button>
            </div>

            {(turnos.length > 0 || vivo) && (
              <div ref={lista} className="mt-3 flex max-h-[460px] flex-col gap-3 overflow-y-auto pr-1">
                {turnos.map((t) => (
                  <TurnoUI key={t.id} t={t} onVerIA={onVerIA}
                           onAplicar={esActual && !vivo && onAplicarTurno ? () => onAplicarTurno(t) : undefined} />
                ))}
                {vivo && <EnCurso vivo={vivo} />}
              </div>
            )}

            {esActual ? (
              <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50/40 p-3">
                <label htmlFor="instruccion-ia" className="text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                  {turnos.length ? "Pídele un cambio o pregúntale" : "¿Qué armamos esta semana?"}
                </label>
                <textarea id="instruccion-ia" ref={campo} value={texto} rows={2} maxLength={2000}
                          onChange={(ev) => setTexto(ev.target.value)}
                          onKeyDown={(ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) enviar(); }}
                          placeholder={turnos.length ? "Ej.: quita lo que tenga precio mayor a $1,000 y dime qué reemplazos me recomiendas"
                            : "Ej.: arma el FULL de la semana; toma sólo SKUs con precio menor a $300"}
                          className="mt-1 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-800 placeholder:text-slate-400 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap gap-1.5">
                    {EJEMPLOS.map((e) => (
                      <button key={e} type="button" onClick={() => setTexto(e)}
                              className="rounded-full border border-violet-200 bg-white px-2.5 py-0.5 text-[11px] font-semibold text-violet-700 hover:bg-violet-100">
                        {e}
                      </button>
                    ))}
                  </div>
                  <button type="button" onClick={() => enviar()} disabled={!puede}
                          title={bloqueo ?? (vivo ? "Espera a que la IA termine este turno" : undefined)}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3.5 py-2 text-sm font-bold text-white shadow-sm hover:bg-violet-700 disabled:opacity-50">
                    <Send className="h-4 w-4" />
                    {vivo ? "La IA está trabajando…" : texto.trim() ? "Enviar a la IA" : turnos.length ? "Enviar" : "Armar el FULL de la semana"}
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
                  {modelos.length > 0 && <span className="font-bold uppercase tracking-[.06em] text-slate-400">Modelo</span>}
                  {modelos.map((m) => (
                    <button key={m.id} type="button" onClick={() => setModelo(m.id)} disabled={!m.disponible}
                            aria-pressed={modelo === m.id} title={m.disponible ? m.nota : "No está configurado en este ambiente"}
                            className={`rounded-full border px-2.5 py-0.5 font-semibold disabled:opacity-40 ${
                              modelo === m.id ? "border-violet-400 bg-violet-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>
                      {m.nombre} <span className="font-normal opacity-80">· {m.nota}</span>
                    </button>
                  ))}
                  <span className="ml-auto text-slate-500">
                    {bloqueo ?? "La respuesta tarda unos minutos; lo que escribe se ve en la tabla mientras tanto. Ctrl+Enter envía."}
                  </span>
                </div>
              </div>
            ) : (
              <p className="mt-3 rounded-xl bg-slate-50 px-3 py-2 text-[12px] text-slate-500">
                Es una semana cerrada: el chat y el plan se guardaron tal como quedaron. Sólo se planea la semana en curso.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function Chips({ m }: { m: ReturnType<typeof movio> }) {
  const chip = "rounded-full border border-violet-200 bg-white px-2 py-0.5 text-[11.5px] font-semibold text-violet-800";
  if (!m.skus && !m.sacados && !m.reemplazos) return <span className="text-[11.5px] text-slate-500">sin cambios al plan</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {m.skus > 0 && <span className={chip}>puso {num(m.skus)} SKUs · {num(m.piezas)} pzs</span>}
      {m.reemplazos > 0 && <span className={chip}>{num(m.reemplazos)} reemplazo{m.reemplazos === 1 ? "" : "s"}</span>}
      {m.sacados > 0 && <span className={chip}>sacó {num(m.sacados)}</span>}
    </span>
  );
}

function Burbuja({ instruccion, pie }: { instruccion: string; pie: string }) {
  return (
    <div className="self-end rounded-2xl rounded-br-sm bg-violet-600 px-3.5 py-2 text-[12.5px] text-white shadow-sm sm:max-w-[75%]">
      {instruccion || "Arma el FULL de la semana con el prompt estándar."}
      <span className="mt-0.5 block text-[10.5px] text-violet-200">{pie}</span>
    </div>
  );
}

function TurnoUI({ t, onVerIA, onAplicar }: { t: TurnoSemana; onVerIA: () => void; onAplicar?: () => void }) {
  const r = t.resultado;
  const quien = (t.quien ?? "").split("@")[0];
  const [hecho, setHecho] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <Burbuja instruccion={t.instruccion}
               pie={[quien, t.creado ? fecha({ ts: t.creado }) : null, t.modelo_nombre].filter(Boolean).join(" · ")} />
      {t.estado === "error" && (
        <p className="rounded-xl border border-rose-200 bg-white px-3.5 py-2 text-[12.5px] text-rose-800">No se pudo: {t.error}</p>
      )}
      {r && (
        <div className="flex flex-col gap-2 rounded-xl bg-violet-50/60 px-3.5 py-3">
          <p className="whitespace-pre-line text-[13px] leading-relaxed text-slate-800">{r.respuesta || "Listo."}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Chips m={movio(r)} />
            {(r.ajustes.length > 0 || r.reemplazos.length > 0) && (
              <button type="button" onClick={onVerIA} className="text-[11.5px] font-semibold text-violet-700 hover:underline">
                ver en la tabla ↓
              </button>
            )}
            {onAplicar && (r.ajustes.length > 0 || r.reemplazos.length > 0) && (
              <button type="button" onClick={() => { onAplicar(); setHecho(true); }}
                      title="Vuelve a poner en la tabla lo que propuso este turno. Lo que quitaste a mano no regresa."
                      className="rounded-md border border-violet-200 bg-white px-2 py-0.5 text-[11px] font-bold text-violet-700 hover:bg-violet-50">
                {hecho ? "aplicado ✓" : "Aplicar al plan"}
              </button>
            )}
          </div>
          {r.descartados.length > 0 && (
            <details className="text-[12px] text-slate-600">
              <summary className="cursor-pointer text-[10.5px] font-bold uppercase tracking-[.06em] text-violet-700">
                Descartadas por el panel · {r.descartados.length}
              </summary>
              <ul className="mt-1 list-inside list-disc">
                {r.descartados.map((d, i) => (
                  <li key={i}>
                    <span className="font-mono">{String(d.sku ?? d.reemplazo ?? "—")}</span> · {String(d.porque ?? "")}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="text-[10.5px] text-slate-500">
            {r.modelo_nombre ?? r.modelo ?? "IA"} · {num(r.tokens?.entrada ?? null)} tokens de entrada
            {r.tokens?.cache ? ` (${num(r.tokens.cache)} releídos de la caché)` : ""}, {num(r.tokens?.salida ?? null)} de salida
            {r.tokens?.razonamiento ? ` (${num(r.tokens.razonamiento)} de razonamiento)` : ""}
            {r.costo_usd !== undefined && r.costo_usd !== null ? ` · costó ≈ ${dolares(r.costo_usd)}` : ""}
            {t.segundos ? ` · ${duracion(t.segundos)}` : ""}
            {r.datos_nuevos ? " · le dio la planeación de hoy" : ""}.
          </p>
        </div>
      )}
    </div>
  );
}

function EnCurso({ vivo }: { vivo: VivoIA }) {
  const a = vivo.avance;
  const p = a?.parcial;
  const m = p ? movio(p) : null;
  const fase = a?.fase === "escribiendo" ? "Escribiendo el plan — cada SKU se pone en la tabla en cuanto lo escribe"
    : a?.fase === "pensando" ? "Pensando" : "Leyendo la planeación";
  return (
    <div className="flex flex-col gap-2">
      <Burbuja instruccion={vivo.instruccion}
               pie={[(vivo.quien ?? "").split("@")[0], "ahora", vivo.modelo].filter(Boolean).join(" · ")} />
      <div className="rounded-xl border border-violet-200 bg-white px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-violet-900">
          <Loader2 className="h-4 w-4 animate-spin" />
          {fase}
          <span className="font-normal text-violet-700/80">
            · {duracion(a?.segundos ?? 0)}{a?.razonamiento ? ` · ~${num(a.razonamiento)} tokens de razonamiento` : ""}
          </span>
        </div>
        {p?.respuesta && <p className="mt-2 text-[13px] leading-relaxed text-slate-800">{p.respuesta}</p>}
        {m && (m.skus > 0 || m.reemplazos > 0 || m.sacados > 0) && <div className="mt-2"><Chips m={m} /></div>}
        <p className="mt-2 text-[11px] text-slate-500">
          Tarda unos minutos. Puedes seguir en la pantalla: lo que escribe ya está en la tabla, marcado en violeta.
        </p>
      </div>
    </div>
  );
}
