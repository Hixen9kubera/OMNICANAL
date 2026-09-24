"use client";

/**
 * FULLFILMENT · ANÁLISIS — todo POR SEMANA (Brandon, 24-sep-2026: "hacer las
 * métricas semanalmente: cuánto se envía a FULL cada semana y cuánto se recibe
 * cada semana"). Reemplaza al Tablero de v0.546.0, que mezclaba totales desde
 * enero con fotos de hoy.
 *
 * La semana es la ISO de la SALIDA VALIDADA en Odoo (hora de CDMX), y lo
 * recibido es lo que ML avisó DE ESOS ENVÍOS, llegue cuando llegue: de lo que
 * salió el viernes, ML recibe el lunes y sigue siendo de su semana. Así «enviado»
 * y «recibido» hablan de las mismas piezas y la tasa no se pasa de 100%.
 *
 *   · un envío CERRADO (10 días tras la salida) aporta recibidas y NO recibidas;
 *   · uno abierto, recibidas y «en recepción» (ámbar): todavía no es rechazo;
 *   · lo que salió sin con qué medirlo (antes del 12-ago, sin cuenta, FBA y WFS)
 *     cuenta como enviado y va en gris: no es "no recibido".
 *
 * Los datos: `semanas` de `GET /api/fulfillment/envios`
 * (fulfillment_etapas.resumir_semanas); los envíos de la semana se filtran aquí.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { seguimientoDe } from "./Envios";
import {
  Ceja, ChipCanal, ChipFuente, FONDO_RAYADO, FONDO_RAYADO_AMBAR, PUNTO_CUENTA, RAYADO, RAYADO_ROSA, Tarjeta,
  num, rangoSemana, semanaIso,
} from "./ui";
import type { Cuenta, Envio, FiltroCanal, FiltroCuenta, RespuestaEnvios, SemanaAnalisis } from "./tipos";

const TIT_AVISOS = "Avisos de FULL de Mercado Libre (webhook fbm_stock_operations) resueltos a tipo, piezas y SKU; "
  + "cada operación cuenta una vez.";
const EN_GRAFICA = 12;

const llave = (s: Pick<SemanaAnalisis, "anio" | "semana">) => `${s.anio}-${s.semana}`;

export default function Analisis({ canal, cuenta, datos, onAbrir }: {
  canal: FiltroCanal; cuenta: FiltroCuenta; datos: RespuestaEnvios | null; onAbrir: (e: Envio) => void;
}) {
  const grupo = canal === "amazon" || canal === "walmart" ? canal : cuenta === "todas" ? "meli" : `meli:${cuenta}`;
  const serie = useMemo(() => datos?.semanas?.[grupo]?.semanas ?? [], [datos, grupo]);
  const porValidar = datos?.semanas?.[grupo]?.por_validar;
  const medible = grupo.startsWith("meli");

  // Arranca en la semana en curso si ya tiene salidas; si no, en la última que sí.
  const inicial = useMemo(() => {
    const actual = serie.find((s) => s.actual);
    if (actual && actual.envios > 0) return llave(actual);
    const ultima = [...serie].reverse().find((s) => s.envios > 0);
    return ultima ? llave(ultima) : actual ? llave(actual) : null;
  }, [serie]);
  const [elegida, setElegida] = useState<string | null>(null);
  useEffect(() => { setElegida(inicial); }, [inicial]);
  const i = serie.findIndex((s) => llave(s) === elegida);
  const s = i >= 0 ? serie[i] : null;

  const envios = useMemo(() => (datos?.envios ?? []).filter((e) => {
    const salida = e.etapas[1]?.ts;
    if (!s || !salida || e.estado_odoo !== "done") return false;
    if (grupo === "amazon" || grupo === "walmart") { if (e.canal !== grupo) return false; }
    else if (e.canal !== "meli" || (grupo !== "meli" && `meli:${e.cuenta}` !== grupo)) return false;
    return semanaIso(salida) === llave(s);
  }).sort((a, b) => ((b.cobertura?.rechazadas ?? 0) - (a.cobertura?.rechazadas ?? 0)) || ((b.piezas ?? 0) - (a.piezas ?? 0))),
  [datos, s, grupo]);

  if (!datos) {
    return <div className="mt-4 rounded-2xl px-6 py-12 text-center text-sm text-slate-500" style={RAYADO}>Leyendo Odoo y los avisos de ML…</div>;
  }
  if (!serie.length || !s) {
    return <div className="mt-4 rounded-2xl px-6 py-12 text-center text-sm text-slate-500" style={RAYADO}>
      No hay salidas validadas para este programa y cuenta.
    </div>;
  }

  const programa = grupo === "amazon" ? "Amazon FBA" : grupo === "walmart" ? "Walmart WFS"
    : `Mercado Libre FULL · ${cuenta === "todas" ? "las dos cuentas" : cuenta}`;
  const almacen = grupo === "amazon" ? "FBA" : grupo === "walmart" ? "WFS" : "FULL";
  const sinMedir = s.enviadas - s.enviadas_medidas;

  return (
    <div className="mt-4 flex flex-col gap-3">
      {/* ── La semana elegida ────────────────────────────────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Ceja>Análisis semanal · {programa}</Ceja>
              <ChipFuente texto={medible ? "Odoo + ML en vivo" : "Odoo en vivo"}
                          titulo={medible ? `Enviado: salidas validadas en Odoo. Recibido: ${TIT_AVISOS}` : undefined} />
            </div>
            <div className="mt-1 flex items-center gap-2">
              <button type="button" onClick={() => i > 0 && setElegida(llave(serie[i - 1]))} disabled={i <= 0}
                      className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30">
                <ChevronLeft className="h-4 w-4" />
              </button>
              <h2 className="text-[22px] font-extrabold tracking-tight text-slate-900">
                Semana {s.semana.replace("S", "")} <span className="font-semibold text-slate-400">· {rangoSemana(s.lunes)}</span>
              </h2>
              <button type="button" onClick={() => i < serie.length - 1 && setElegida(llave(serie[i + 1]))}
                      disabled={i >= serie.length - 1}
                      className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30">
                <ChevronRight className="h-4 w-4" />
              </button>
              <EstadoSemana s={s} medible={medible} />
            </div>
          </div>
          {porValidar && porValidar.envios > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900">
              Hoy hay <b>{porValidar.envios}</b>{" "}
              {porValidar.envios === 1 ? "salida por validar" : "salidas por validar"} ({num(porValidar.pedidas)} pzs
              pedidas): todavía no cuentan en ninguna semana.
            </div>
          )}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          <Kpi rotulo={`Enviado a ${almacen}`} cifra={num(s.enviadas)} tono="slate"
               pie={`piezas en ${s.envios} ${s.envios === 1 ? "salida validada" : "salidas validadas"}`
                 + (s.no_surtidas ? ` · Odoo no surtió ${num(s.no_surtidas)} de ${num(s.pedidas)} pedidas` : "")} />
          {!medible ? (
            <KpiHueco rotulo="Recibido" nota={grupo === "amazon" ? "la app de Amazon no tiene permiso de Inbound (403)" : "falta leer la API de WFS"} />
          ) : s.medidos === 0 ? (
            <KpiHueco rotulo="Recibido por ML" nota={s.envios ? "salidas sin avisos que medir (antes del 12-ago o sin cuenta)" : "ninguna salida esta semana"} />
          ) : (
            <Kpi rotulo="Recibido por ML" tono="verde" cifra={num(s.recibidas)}
                 pie={`de ${num(s.enviadas_medidas)} piezas medidas${sinMedir > 0 ? ` · ${num(sinMedir)} sin medir` : ""}`}
                 grande={s.tasa !== null ? `${s.tasa}%` : undefined} />
          )}
          {medible && s.medidos > 0 ? (
            s.abiertos > 0 ? (
              <Kpi rotulo="En recepción" tono="ambar" cifra={num(s.en_recepcion)}
                   pie={`faltan por avisar en ${s.abiertos} ${s.abiertos === 1 ? "envío abierto" : "envíos abiertos"}: todavía no es rechazo`
                     + (s.no_recibidas ? ` · ya cerrado: ML no recibió ${num(s.no_recibidas)}` : "")} />
            ) : (
              <Kpi rotulo="ML no recibió" tono={s.no_recibidas ? "rosa" : "verde"} cifra={num(s.no_recibidas)}
                   pie={s.no_recibidas
                     ? `piezas en envíos cerrados${s.dudosas ? ` · ${num(s.dudosas)} pueden ser publicaciones con variantes` : ""}`
                     : "todos los envíos de la semana llegaron completos"} />
            )
          ) : (
            <KpiHueco rotulo="No recibido" nota="sin medición" />
          )}
          <Kpi rotulo="Tiempos de la semana" tono="slate" chica
               cifra={s.salida_a_primera_llegada_dias.mediana !== null ? `${s.salida_a_primera_llegada_dias.mediana} d a la 1ª llegada` : "—"}
               pie={[s.salida_a_completo_dias.mediana !== null ? `${s.salida_a_completo_dias.mediana} d a completo` : null,
                     s.orden_a_salida_dias.mediana !== null ? `${s.orden_a_salida_dias.mediana} d de orden a salida` : null]
                 .filter(Boolean).join(" · ") || "sin tiempos medidos"} />
        </div>

        {grupo === "meli" && <PorCuenta datos={datos} llaveSemana={llave(s)} />}
      </Tarjeta>

      {/* ── Las semanas: gráfica y tabla ─────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        {/* `min-w-0`: sin él, un elemento de grid no se encoge por debajo de su
            contenido y la tabla de semanas desbordaba la pantalla del celular. */}
        <Tarjeta className="min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <Ceja>Enviado contra recibido · últimas {Math.min(EN_GRAFICA, serie.length)} semanas</Ceja>
              <h3 className="mt-1 text-[16px] font-extrabold tracking-tight text-slate-900">
                {medible ? "Cada barra es lo que salió esa semana; lo verde, lo que ML recibió de eso"
                         : `Cada barra es lo que salió a ${almacen} esa semana; lo recibido todavía no se puede leer`}
              </h3>
            </div>
            <span className="text-[11px] text-slate-400">toca una barra para ver su semana</span>
          </div>
          <Grafica semanas={serie.slice(-EN_GRAFICA)} elegida={llave(s)} onElegir={setElegida} medible={medible} />
        </Tarjeta>
        <Tarjeta className="min-w-0">
          <Ceja>Todas las semanas · la más reciente arriba</Ceja>
          <TablaSemanas semanas={serie} elegida={llave(s)} onElegir={setElegida} medible={medible} />
        </Tarjeta>
      </div>

      {/* ── Los envíos de la semana ──────────────────────────────────────── */}
      <Tarjeta>
        <Ceja>Los envíos de la semana {s.semana.replace("S", "")} · {envios.length}</Ceja>
        <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
          {envios.map((e) => <RenglonEnvio key={e.id ?? e.orden} e={e} onAbrir={onAbrir} />)}
          {envios.length === 0 && <p className="px-3 py-4 text-[12.5px] text-slate-400">Ninguna salida validada esta semana (cero real).</p>}
        </div>
      </Tarjeta>

      <p className="text-xs leading-relaxed text-slate-400">
        Semana ISO de la salida validada en Odoo, hora de CDMX. Lo recibido es lo que ML avisó de esos envíos, llegue
        cuando llegue; un envío cierra 10 días después de su salida y lo que falta entonces es lo que ML no recibió.
        Lo que salió antes del 12-ago, sin cuenta o a FBA/WFS no se puede medir y va en gris: no es un cero.
      </p>
    </div>
  );
}

function EstadoSemana({ s, medible }: { s: SemanaAnalisis; medible: boolean }) {
  const [t, c] = s.actual ? ["semana en curso", "border-indigo-200 bg-indigo-50 text-indigo-700"]
    : !medible || s.medidos === 0 ? ["sin medición", "border-slate-200 bg-slate-50 text-slate-500"]
    : s.abiertos > 0 ? ["en recepción", "border-amber-300 bg-amber-50 text-amber-700"]
    : ["cerrada", "border-emerald-200 bg-emerald-50 text-emerald-700"];
  return <span className={`ml-1 rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${c}`}>{t}</span>;
}

function Kpi({ rotulo, cifra, pie, tono, grande, chica }: {
  rotulo: string; cifra: string; pie: string; tono: "slate" | "verde" | "ambar" | "rosa"; grande?: string; chica?: boolean;
}) {
  const c = {
    slate: "border-slate-200 bg-white text-slate-900",
    verde: "border-emerald-200 bg-emerald-50 text-emerald-800",
    ambar: "border-amber-300 bg-amber-50 text-amber-800",
    rosa: "border-rose-200 bg-rose-50 text-rose-800",
  }[tono];
  return (
    <div className={`rounded-xl border px-3.5 py-3 ${c}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10.5px] font-bold uppercase tracking-[.06em] opacity-70">{rotulo}</span>
        {grande && <span className="font-mono text-sm font-extrabold">{grande}</span>}
      </div>
      <div className={`mt-1 font-extrabold leading-none tracking-tight tabular-nums ${chica ? "text-base" : "text-2xl"}`}>{cifra}</div>
      <div className="mt-1 text-[11.5px] opacity-80">{pie}</div>
    </div>
  );
}

function KpiHueco({ rotulo, nota }: { rotulo: string; nota: string }) {
  return (
    <div className="rounded-xl px-3.5 py-3" style={RAYADO}>
      <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
      <div className="mt-1 font-mono text-[11px] font-bold uppercase text-slate-400">sin registro</div>
      <div className="mt-1 text-[11.5px] text-slate-500">{nota}</div>
    </div>
  );
}

/** Con «todas las cuentas»: la semana elegida partida por cuenta. */
function PorCuenta({ datos, llaveSemana }: { datos: RespuestaEnvios; llaveSemana: string }) {
  const filas = (["Kubera", "San Corpe"] as Cuenta[]).map((c) => ({
    c, s: datos.semanas?.[`meli:${c}`]?.semanas.find((x) => llave(x) === llaveSemana) ?? null,
  }));
  const sin = datos.semanas?.["meli:sin_asignar"]?.semanas.find((x) => llave(x) === llaveSemana) ?? null;
  return (
    <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {filas.map(({ c, s }) => (
        <div key={c} className="flex items-center justify-between rounded-xl border border-slate-200 px-3 py-2 text-[12.5px]">
          <span className="inline-flex items-center gap-2 font-bold text-slate-700">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: PUNTO_CUENTA[c] }} />{c}
          </span>
          <span className="text-slate-600">
            {s && s.envios ? <>
              <b className="font-mono">{num(s.enviadas)}</b> enviadas · <b className="font-mono text-emerald-700">{num(s.recibidas)}</b> recibidas
              {s.tasa !== null ? <b className="ml-1 font-mono">{s.tasa}%</b> : s.abiertos ? <span className="ml-1 text-amber-700">en recepción</span> : null}
            </> : <span className="text-slate-400">sin salidas</span>}
          </span>
        </div>
      ))}
      {sin && sin.envios > 0 && (
        <div className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-2 text-[12.5px]"
             title="Salidas a FULL que creó alguien que no es Thalia ni Cinthya y sin socio fijo: no se sabe de qué cuenta son, y su llegada no se mide.">
          <span className="font-bold text-amber-800">Sin cuenta</span>
          <span className="text-amber-900"><b className="font-mono">{num(sin.enviadas)}</b> enviadas · sin medir</span>
        </div>
      )}
    </div>
  );
}

/** Barras apiladas por semana: recibido, no recibido, en recepción y sin medir. Total = lo enviado. */
function Grafica({ semanas, elegida, onElegir, medible }: {
  semanas: SemanaAnalisis[]; elegida: string; onElegir: (k: string) => void; medible: boolean;
}) {
  const max = Math.max(1, ...semanas.map((s) => s.enviadas));
  const alto = (v: number) => (v / max) * 170;
  return (
    <>
      <div className="mt-4 flex h-[230px] items-end gap-1.5 sm:gap-2.5">
        {semanas.map((s) => {
          const k = llave(s);
          const sinMedir = s.enviadas - s.enviadas_medidas;
          const noDudosas = s.no_recibidas - s.dudosas;
          const titulo = s.envios === 0
            ? `${s.semana} (${rangoSemana(s.lunes)}): ninguna salida validada (cero real)`
            : `${s.semana} (${rangoSemana(s.lunes)}) · ${s.envios} salidas · ${num(s.enviadas)} enviadas`
              + (medible ? `: ${num(s.recibidas)} recibidas` : "")
              + (s.no_recibidas ? `, ${num(s.no_recibidas)} no recibidas` : "")
              + (s.dudosas ? ` (${num(s.dudosas)} podrían ser variantes)` : "")
              + (s.en_recepcion ? `, ${num(s.en_recepcion)} en recepción` : "")
              + (sinMedir > 0 ? `, ${num(sinMedir)} sin medir` : "");
          return (
            <button key={k} type="button" onClick={() => onElegir(k)} title={titulo}
                    className={`flex min-w-0 flex-1 flex-col justify-end rounded-md px-0.5 pb-0.5 ${k === elegida ? "bg-indigo-50 ring-2 ring-indigo-300" : "hover:bg-slate-50"}`}>
              <div className={`mb-1 text-center font-mono text-[10px] font-bold tabular-nums ${
                s.tasa === null ? "text-slate-400" : s.tasa >= 97 ? "text-emerald-700" : s.tasa >= 90 ? "text-amber-700" : "text-rose-700"}`}>
                {s.envios === 0 ? "" : s.tasa !== null ? `${s.tasa}%` : s.abiertos ? "…" : ""}
              </div>
              {s.envios === 0 ? (
                <div className="h-[3px] rounded bg-slate-200" />
              ) : (
                <div className="flex flex-col justify-end overflow-hidden rounded-t-[4px]">
                  {sinMedir > 0 && <div style={{ height: Math.max(3, alto(sinMedir)), background: FONDO_RAYADO }} className="border border-slate-200" />}
                  {noDudosas > 0 && <div className="bg-rose-600" style={{ height: Math.max(3, alto(noDudosas)) }} />}
                  {s.dudosas > 0 && <div style={{ height: Math.max(3, alto(s.dudosas)), background: RAYADO_ROSA }} />}
                  {s.en_recepcion > 0 && (
                    <div className="border border-amber-300" style={{ height: Math.max(3, alto(s.en_recepcion)), background: FONDO_RAYADO_AMBAR }} />
                  )}
                  {s.recibidas > 0 && <div className="bg-emerald-600" style={{ height: Math.max(3, alto(s.recibidas)) }} />}
                </div>
              )}
              <div className="mt-1.5 text-center text-[10.5px] font-semibold text-slate-600">{s.semana}</div>
              <div className="hidden text-center text-[9.5px] text-slate-400 sm:block">{num(s.enviadas)}</div>
            </button>
          );
        })}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-3 text-[11px] text-slate-500">
        {medible && <>
          <Muestra fondo="#059669" texto="recibido por ML" />
          <Muestra fondo="#e11d48" texto="no recibido (envío cerrado)" />
          <Muestra fondo={RAYADO_ROSA} texto="no recibido con avisos sin SKU (variantes)" />
          <Muestra fondo={FONDO_RAYADO_AMBAR} borde="1px solid #FCD34D" texto="en recepción" />
        </>}
        <Muestra fondo={FONDO_RAYADO} borde="1px solid #e2e8f0" texto={medible ? "sin medir" : "enviado, sin lectura de lo recibido"} />
      </div>
    </>
  );
}

function Muestra({ fondo, borde, texto }: { fondo: string; borde?: string; texto: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-3 rounded-sm" style={{ background: fondo, border: borde }} />
      {texto}
    </span>
  );
}

function TablaSemanas({ semanas, elegida, onElegir, medible }: {
  semanas: SemanaAnalisis[]; elegida: string; onElegir: (k: string) => void; medible: boolean;
}) {
  const filas = [...semanas].reverse();
  return (
    <div className="mt-2 max-h-[330px] overflow-auto rounded-xl border border-slate-200">
      <table className="w-full min-w-[520px] text-[12px]">
        <thead className="sticky top-0 bg-slate-50">
          <tr className="text-left text-[10px] font-bold uppercase tracking-[.05em] text-slate-400">
            <th className="px-3 py-2">Semana</th>
            <th className="px-2 py-2 text-right">Salidas</th>
            <th className="px-2 py-2 text-right">Enviado</th>
            <th className="px-2 py-2 text-right">Recibido</th>
            <th className="px-2 py-2 text-right" title="Envíos cerrados: lo que ML no avisó.">No recibido</th>
            <th className="px-2 py-2 text-right" title="Envíos abiertos: lo que falta todavía NO es rechazo.">En recepción</th>
            <th className="px-3 py-2 text-right">%</th>
          </tr>
        </thead>
        <tbody>
          {filas.map((s) => {
            const k = llave(s);
            const nada = s.envios === 0;
            return (
              <tr key={k} onClick={() => onElegir(k)}
                  className={`cursor-pointer border-t border-slate-100 ${k === elegida ? "bg-indigo-50" : "hover:bg-slate-50"} ${nada ? "text-slate-300" : ""}`}>
                <td className="px-3 py-1.5">
                  <span className="font-bold text-slate-700">{s.semana}</span>
                  <span className="text-slate-400"> · {rangoSemana(s.lunes)}</span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums">{s.envios}</td>
                <td className="px-2 py-1.5 text-right font-mono font-bold tabular-nums text-slate-800">{nada ? "0" : num(s.enviadas)}</td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums text-emerald-700">
                  {!medible || (s.medidos === 0 && !nada) ? <span className="text-slate-300">—</span> : num(s.recibidas)}
                </td>
                <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${s.no_recibidas ? "text-rose-700" : "text-slate-300"}`}>
                  {s.no_recibidas ? num(s.no_recibidas) : "—"}
                </td>
                <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${s.en_recepcion ? "text-amber-700" : "text-slate-300"}`}>
                  {s.en_recepcion ? num(s.en_recepcion) : "—"}
                </td>
                <td className={`px-3 py-1.5 text-right font-mono font-bold tabular-nums ${
                  s.tasa === null ? "text-slate-300" : s.tasa >= 97 ? "text-emerald-700" : s.tasa >= 90 ? "text-amber-700" : "text-rose-700"}`}>
                  {s.tasa !== null ? `${s.tasa}%` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RenglonEnvio({ e, onAbrir }: { e: Envio; onAbrir: (e: Envio) => void }) {
  const c = e.cobertura;
  const seg = seguimientoDe(e);
  const [t, cls] = seg === "no_recibio" ? [`ML no recibió ${num(c?.rechazadas ?? 0)} pzs`, "text-rose-700"]
    : seg === "completo" ? ["completo", "text-emerald-700"]
    : seg === "llegando" ? [`${num(c?.piezas_llegadas ?? 0)} de ${num(c?.piezas_enviadas ?? null)} pzs · llegando`, "text-amber-700"]
    : ["sin medir", "text-slate-400"];
  return (
    <button type="button" onClick={() => onAbrir(e)}
            className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left text-[12.5px] hover:bg-slate-50">
      <span className="flex items-center gap-2">
        <ChipCanal canal={e.canal} cuenta={e.cuenta} />
        <span className="font-mono font-bold text-slate-800">{e.orden}</span>
        <span className="text-slate-400">· {e.envio ? `envío ${e.envio}` : "sin número"} · {e.cuenta ?? "sin cuenta"}</span>
      </span>
      <span className="flex items-center gap-3">
        <span className="font-mono tabular-nums text-slate-700">{num(e.piezas)} pzs</span>
        <span className={`font-semibold ${cls}`}>{t}</span>
        <span className="text-[11px] font-semibold text-indigo-600">Detalle →</span>
      </span>
    </button>
  );
}
