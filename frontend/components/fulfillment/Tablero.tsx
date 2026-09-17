"use client";

/**
 * FULLFILMENT · Tablero — KPIs, la gráfica obligatoria, el embudo, los días de
 * proceso, el agotado en FULL, FBA, WFS y la calidad de captura por KAM.
 *
 * Construido por etapas: lo que ya sale de Odoo (enviado, sin número, días de
 * proceso, captura por KAM, salidas a FBA y WFS) lleva el chip «Odoo en vivo»;
 * lo que sigue siendo del mockup (stock en FULL, agotado, stock FBA) lleva el
 * chip «diseño». Nunca se mezclan en la misma cifra.
 *
 * Dos de los cinco escalones del embudo existen. Pintar 0 en los otros tres
 * sería mentir con una cifra que la gente creería: por eso van rayados.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Boxes, Link2Off, PackageCheck, Truck } from "lucide-react";
import { FBA, FULL_POR_CUENTA } from "./datosDiseno";
import {
  Ceja, ChipFuente, ChipSinRegistro, DIAS_NOMBRE, DIAS_SEMANA, PUNTO_CUENTA, RAYADO, Tarjeta, dia, num,
} from "./ui";
import type { Envio, FiltroCanal, FiltroCuenta, RespuestaEnvios, ResumenGrupo } from "./tipos";

export default function Tablero({
  canal, cuenta, datos,
}: { canal: FiltroCanal; cuenta: FiltroCuenta; datos: RespuestaEnvios | null }) {
  if (canal === "amazon" || canal === "walmart") {
    return <SoloPrograma canal={canal} grupo={datos?.resumen[canal] ?? null}
                         mfn={datos?.excluidas.venta_amazon_mfn ?? null} />;
  }

  const g = datos?.resumen[cuenta === "todas" ? "meli" : `meli:${cuenta}`] ?? null;
  const sinAsignar = datos?.resumen["meli:sin_asignar"] ?? null;
  const cuentas = FULL_POR_CUENTA.filter((c) => cuenta === "todas" || c.cuenta === cuenta);
  const hoy = cuentas.reduce((a, c) => a + c.piezas, 0);
  const enCero = cuentas.reduce((a, c) => a + c.enCero, 0);
  const publicaciones = cuentas.reduce((a, c) => a + c.publicaciones, 0);
  const pctPedido = g && g.piezas_pedidas_hechas
    ? Math.round((g.piezas_enviadas / g.piezas_pedidas_hechas) * 1000) / 10 : null;

  return (
    <>
      {/* ── KPIs ─────────────────────────────────────────────────────────── */}
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi icono={<Truck className="h-3.5 w-3.5" />} rotulo="Enviado a FULL" vivo
             cifra={g ? num(g.piezas_enviadas) : "…"}
             pie={g
               ? `piezas en ${num(g.hechas)} salidas validadas · ${dia(g.desde)} → ${dia(g.hasta)}`
                 + (g.abiertas ? ` · ${g.abiertas} abiertas con ${num(g.piezas_abiertas)} pzs` : "")
               : "leyendo Odoo…"} />
        <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Hoy en FULL"
             cifra={num(hoy)}
             pie={cuentas.map((c) => `${c.cuenta} ${num(c.piezas)}`).join(" · ")} />
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-rose-800">
              <AlertTriangle className="h-3.5 w-3.5" /> Agotado en FULL
            </span>
            <ChipFuente vivo={false} />
          </div>
          <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-rose-800">
            {Math.round((enCero / publicaciones) * 100)}%
          </div>
          <div className="mt-1 text-xs text-rose-800/85">
            {num(enCero)} de {num(publicaciones)} publicaciones FULL en cero
          </div>
        </div>
        <div className="rounded-2xl p-4" style={RAYADO}>
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">
            <PackageCheck className="h-3.5 w-3.5" /> Tasa de recepción
          </div>
          <div className="mt-2"><ChipSinRegistro /></div>
          <div className="mt-2 text-xs text-slate-500">
            las recepciones de FULL todavía no se guardan
          </div>
        </div>
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-amber-700">
              <Link2Off className="h-3.5 w-3.5" /> Envíos sin número
            </span>
            <ChipFuente vivo />
          </div>
          <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-amber-700">
            {g ? num(g.sin_numero) : "…"}
          </div>
          <div className="mt-1 text-xs text-amber-800">
            {g ? `de ${num(g.salidas)} salidas (${Math.round((g.sin_numero / Math.max(1, g.salidas)) * 100)}%) sin número de envío en la referencia` : "leyendo Odoo…"}
          </div>
        </div>
      </div>

      {cuenta === "todas" && sinAsignar && sinAsignar.salidas > 0 && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-2.5 text-[12.5px] text-amber-900">
          <b>{sinAsignar.salidas} salidas a FULL sin cuenta</b> ({num(sinAsignar.piezas_enviadas)} piezas): las creó alguien
          que no es Thalia ni Cinthya, y la regla no adivina. Cuentan en «Todas» y no en ninguna cuenta.
        </div>
      )}

      {/* ── Gráfica obligatoria + embudo ─────────────────────────────────── */}
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Tarjeta>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <Ceja>Requisito · gráfica principal</Ceja>
              <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
                Tasa de éxito del envío — piezas entregadas contra rechazadas
              </h2>
            </div>
            <ChipSinRegistro texto="aún no medido" />
          </div>
          <div className="mt-3.5 flex min-h-[196px] items-center justify-center rounded-xl px-5 py-6 text-center" style={RAYADO}>
            <div className="max-w-[560px]">
              <p className="text-sm font-semibold text-slate-700">
                Las recepciones del almacén FULL todavía no se guardan: el aviso de Mercado Libre se retiene
                3 días y el desglose de lo rechazado se descarga y se desecha.
              </p>
              <p className="mt-2 text-[12.5px] leading-relaxed text-slate-500">
                Ojo para cuando se construya: la llegada a FULL casi nunca se registra como{" "}
                <code className="font-mono text-[11.5px] text-slate-700">INBOUND_RECEPTION</code> (431 contra
                17,496 <code className="font-mono text-[11.5px] text-slate-700">TRANSFER_DELIVERY</code> desde
                enero). Contar solo la primera daría «recibidas» casi en cero. Esto no es un cero: es un hueco.
              </p>
            </div>
          </div>
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3">
            <p className="text-[12.5px] leading-relaxed text-rose-800">
              <b>Regla de la gráfica:</b> mientras el marketplace sigue recibiendo,{" "}
              <i>enviadas − recibidas</i> NO es un rechazo — es trabajo en curso. El rechazo
              se pinta sólo cuando el marketplace lo da como cantidad explícita. La vista separa{" "}
              <b>en recepción</b> de <b>rechazado</b>.
            </p>
          </div>
          <p className="mt-2.5 text-xs text-slate-400">
            Tres formas propuestas de esta gráfica, con datos simulados, en la pantalla <b>Variaciones</b>.
          </p>
        </Tarjeta>

        <Tarjeta>
          <Ceja>Embudo de piezas · lo que ya se puede medir</Ceja>
          <div className="mt-3.5 flex flex-col gap-2">
            <Escalon titulo="Solicitadas" nota="La lista de Andy no vive en ningún sistema: Andy no aparece creando ni validando salidas en Odoo." />
            <Escalon titulo="Validadas por Bodega"
                     nota="La cantidad de Odoo ya trae el recorte: tomarla de ahí pondría la tasa en 100%." />
            <div className="rounded-[10px] border border-emerald-200 bg-emerald-50 px-3 py-2.5">
              <div className="flex items-baseline justify-between">
                <span className="flex items-center gap-2 text-[12.5px] font-bold text-emerald-800">Enviadas <ChipFuente vivo /></span>
                <span className="font-mono text-[15px] font-extrabold tabular-nums text-emerald-800">
                  {g ? num(g.piezas_enviadas) : "…"}
                </span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-emerald-100">
                <div className="h-full rounded-full bg-emerald-600" style={{ width: `${Math.min(100, pctPedido ?? 0)}%` }} />
              </div>
              <p className="mt-1 text-[11.5px] text-emerald-700">
                {pctPedido !== null ? `${pctPedido}% de lo que pedían esas salidas` : "—"} · sólo el paso{" "}
                <code className="font-mono">outgoing</code>, nunca PICK/PACK
              </p>
            </div>
            <Escalon titulo="Recibidas · rechazadas" nota="Empieza el día que se guarden las llegadas del almacén FULL." />
            <div className="rounded-[10px] border border-slate-200 bg-white px-3 py-2.5">
              <div className="flex items-baseline justify-between">
                <span className="text-[12.5px] font-bold text-slate-700">Vendidas desde FULL</span>
                <span className="font-mono text-[10px] font-bold uppercase tracking-[.05em] text-emerald-700">existe</span>
              </div>
              <p className="mt-1 text-[11.5px] text-slate-500">
                Ventas y primera venta por SKU ya se leen; el % del envío vendido necesita el enlace al envío.
              </p>
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Dos de los cinco escalones existen. Pintar 0 en los otros tres sería mentir con una cifra que la gente creería.
          </p>
        </Tarjeta>
      </div>

      {/* ── Días de proceso + agotado + otros programas ──────────────────── */}
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Tarjeta>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="flex items-center gap-2">
                <Ceja>Qué días se procesan los envíos · lo medido, no el calendario</Ceja>
                <ChipFuente vivo />
              </div>
              <h2 className="mt-1 text-base font-extrabold text-slate-900">
                {g ? `${num(g.salidas)} salidas a FULL, por día de la semana` : "Salidas a FULL por día de la semana"}
              </h2>
            </div>
            <span className="text-[11px] text-slate-400">fechas de Odoo convertidas a hora de CDMX</span>
          </div>
          {g ? <DiasProceso g={g} /> : <p className="mt-4 text-sm text-slate-400">Leyendo Odoo…</p>}
          <div className="mt-4 grid gap-2.5 sm:grid-cols-3">
            <Dato rotulo="De orden a salida validada"
                  cifra={g?.orden_a_validacion_dias.mediana != null ? `${g.orden_a_validacion_dias.mediana} días` : "—"}
                  pie={g ? `mediana · ${g.orden_a_validacion_dias.p90 ?? "—"} días en el peor 10% · ${g.orden_a_validacion_dias.n} salidas` : ""} />
            <Dato rotulo="El calendario ideal" cifra="martes pedir · miércoles salir"
                  pie="lo que se espera del proceso" chico />
            <Dato rotulo="Lo que dicen los datos" cifra={g ? picos(g) : "—"}
                  pie="los dos días con más órdenes y más validaciones" chico />
          </div>
        </Tarjeta>

        <div className="flex flex-col gap-3">
          <Tarjeta>
            <div className="flex items-center justify-between gap-2">
              <Ceja>Agotado en FULL · por cuenta</Ceja>
              <ChipFuente vivo={false} />
            </div>
            <div className="mt-3 flex flex-col gap-3.5">
              {cuentas.map((c) => (
                <div key={c.cuenta}>
                  <div className="flex items-baseline justify-between">
                    <span className="inline-flex items-center gap-[7px] text-[13px] font-bold text-slate-700">
                      <span className="h-[9px] w-[9px] rounded-full" style={{ background: PUNTO_CUENTA[c.cuenta] }} />
                      {c.cuenta}
                    </span>
                    <span className="text-xs text-slate-500">
                      <b className="font-mono text-rose-800">{num(c.enCero)}</b> de {num(c.publicaciones)} en cero
                    </span>
                  </div>
                  <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full bg-rose-600" style={{ width: `${(c.enCero / c.publicaciones) * 100}%` }} />
                  </div>
                  <div className="mt-1 text-[11.5px] text-slate-400">
                    {num(c.piezas)} piezas en {c.conStock} publicaciones con stock
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
              Cuando se conecte serán <b>ceros reales</b>: la publicación existe, está marcada FULL y no tiene
              ni una pieza en el almacén del marketplace.
            </p>
          </Tarjeta>

          {canal === "todos" && (
            <Tarjeta>
              <Ceja>Los otros dos programas</Ceja>
              <TarjetaFba grupo={datos?.resumen.amazon ?? null} mfn={datos?.excluidas.venta_amazon_mfn ?? null} />
              <TarjetaWfs grupo={datos?.resumen.walmart ?? null} />
            </Tarjeta>
          )}
        </div>
      </div>

      {/* ── Captura por KAM ──────────────────────────────────────────────── */}
      <Tarjeta className="mt-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <Ceja>El enlace entre Odoo y Mercado Libre · calidad de captura por KAM</Ceja>
            <ChipFuente vivo />
          </div>
          <span className="text-[11px] text-slate-400">el número de envío se teclea a mano en la referencia de la orden de venta</span>
        </div>
        {datos ? <CapturaKam envios={datos.envios} cuenta={cuenta} /> : <p className="mt-3 text-sm text-slate-400">Leyendo Odoo…</p>}
        <p className="mt-3 text-xs text-slate-500">
          Propuesta: que la solicitud <b>capture el número desde el sistema</b> en vez de depender de que
          alguien lo teclee, y dos socios fijos en Odoo (FULL KUBERA y FULL SAN CORPE) en vez de un contacto
          «FULL» nuevo por orden. Mientras no exista, el estado <b>«envío sin enlazar»</b> es un ciudadano de
          primera en la tabla de Envíos.
        </p>
      </Tarjeta>
    </>
  );
}

function picos(g: ResumenGrupo): string {
  const top2 = (xs: number[]) => xs.slice(0, 6).map((v, i) => [v, i] as const)
    .sort((a, b) => b[0] - a[0]).slice(0, 2).map(([, i]) => DIAS_SEMANA[i]);
  return `se crean ${top2(g.dias_orden).join(" y ")} · se validan ${top2(g.dias_validacion).join(" y ")}`;
}

function Kpi({ icono, rotulo, cifra, pie, vivo }: {
  icono: ReactNode; rotulo: string; cifra: string; pie: string; vivo?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">
          {icono} {rotulo}
        </span>
        <ChipFuente vivo={!!vivo} />
      </div>
      <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-slate-900">{cifra}</div>
      <div className="mt-1 text-xs text-slate-500">{pie}</div>
    </div>
  );
}

function Escalon({ titulo, nota }: { titulo: string; nota: string }) {
  return (
    <div className="rounded-[10px] px-3 py-2.5" style={RAYADO}>
      <div className="flex items-baseline justify-between">
        <span className="text-[12.5px] font-bold text-slate-600">{titulo}</span>
        <span className="font-mono text-[10px] font-bold uppercase tracking-[.05em] text-slate-400">sin registro</span>
      </div>
      <p className="mt-0.5 text-[11.5px] text-slate-500">{nota}</p>
    </div>
  );
}

function Dato({ rotulo, cifra, pie, chico }: { rotulo: string; cifra: string; pie: string; chico?: boolean }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-3">
      <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
      <div className={`mt-1 font-extrabold text-slate-900 ${chico ? "text-sm" : "text-xl tabular-nums"}`}>{cifra}</div>
      <div className="mt-0.5 text-[11.5px] text-slate-500">{pie}</div>
    </div>
  );
}

/** Mapa de calor lun–sáb: orden creada (esmeralda) contra salida validada (azul). */
function DiasProceso({ g }: { g: ResumenGrupo }) {
  const ordenes = g.dias_orden.slice(0, 6);
  const salidas = g.dias_validacion.slice(0, 6);
  const maxO = Math.max(1, ...ordenes);
  const maxS = Math.max(1, ...salidas);
  // El mínimo se busca en días HÁBILES: el sábado es medio turno y siempre
  // "ganaría", escondiendo el hallazgo (el miércoles del proceso ideal es el
  // día hábil que menos valida).
  const minS = Math.min(...salidas.slice(0, 5));
  const celda = (valor: number, alpha: number, rgb: string, titulo: string, marca?: boolean) => (
    <div title={titulo}
         className="flex h-[52px] items-center justify-center rounded-lg text-sm font-extrabold tabular-nums"
         style={{
           background: `rgba(${rgb},${alpha.toFixed(2)})`,
           color: alpha < 0.3 ? (rgb.startsWith("5") ? "#065f46" : "#0369a1") : "#fff",
           border: marca ? "2px solid #e11d48" : undefined,
         }}>
      {valor}
    </div>
  );
  const domingo = g.dias_orden[6] + g.dias_validacion[6];
  return (
    <>
      <div className="mt-4 flex items-end gap-3">
        <div className="flex flex-col gap-1.5 pb-[22px]">
          <span className="flex h-[52px] items-center text-[11px] font-bold text-emerald-700">orden creada</span>
          <span className="flex h-[52px] items-center text-[11px] font-bold text-sky-700">salida validada</span>
        </div>
        <div className="grid flex-1 grid-cols-6 gap-1.5">
          {ordenes.map((v, i) => (
            <div key={`o-${i}`}>
              {celda(v, Math.max(0.2, v / maxO), "5,150,105",
                `${DIAS_NOMBRE[i]} · ${v} órdenes creadas${v === maxO ? " — el pico" : ""}`)}
            </div>
          ))}
          {salidas.map((v, i) => (
            <div key={`s-${i}`}>
              {celda(v, Math.max(0.22, (v / maxS) * 0.83), "3,105,161",
                `${DIAS_NOMBRE[i]} · ${v} salidas validadas${i < 5 && v === minS ? " — el día hábil con MENOS validaciones" : ""}`,
                i < 5 && v === minS)}
            </div>
          ))}
          {DIAS_SEMANA.slice(0, 6).map((d) => (
            <div key={`d-${d}`} className="text-center text-[11px] font-semibold text-slate-500">{d}</div>
          ))}
        </div>
      </div>
      {domingo > 0 && (
        <p className="mt-1 text-[11px] text-slate-400">Además {domingo} registros en domingo (no se dibujan).</p>
      )}
    </>
  );
}

/** Por KAM: cuántas de sus salidas a FULL traen número de envío, en total y en los últimos 30 días. */
function CapturaKam({ envios, cuenta }: { envios: Envio[]; cuenta: FiltroCuenta }) {
  const hace30 = Date.now() - 30 * 86400_000;
  const porKam = new Map<string, { total: number; con: number; t30: number; c30: number; ejemplo: string | null }>();
  for (const e of envios) {
    if (e.canal !== "meli" || (cuenta !== "todas" && e.cuenta !== cuenta)) continue;
    const k = e.kam ?? "—";
    const r = porKam.get(k) ?? { total: 0, con: 0, t30: 0, c30: 0, ejemplo: null };
    r.total += 1;
    if (e.envio) {
      r.con += 1;
      // El número a veces NO está en la referencia sino en el nombre del socio.
      if (!r.ejemplo) r.ejemplo = e.envio_origen === "socio" ? `socio «${e.socio}»` : e.referencia ?? null;
    }
    const creada = e.etapas[0]?.ts ? Date.parse(e.etapas[0].ts) : 0;
    if (creada >= hace30) {
      r.t30 += 1;
      if (e.envio) r.c30 += 1;
    }
    porKam.set(k, r);
  }
  const filas = [...porKam.entries()].sort((a, b) => b[1].total - a[1].total);
  return (
    <div className="mt-3 grid gap-3 md:grid-cols-3">
      {filas.map(([kam, r]) => {
        const pct = (r.con / Math.max(1, r.total)) * 100;
        return (
          <div key={kam} className="rounded-xl border border-slate-200 px-3.5 py-3">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-bold text-slate-700">{kam}</span>
              <span className="font-mono text-[13px] font-extrabold tabular-nums text-slate-900">
                {r.con} <span className="text-[11px] font-normal text-slate-400">/ {r.total}</span>
              </span>
            </div>
            <div className="mt-1.5 h-[5px] overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full" style={{ width: `${pct}%`, background: pct >= 75 ? "#059669" : "#f59e0b" }} />
            </div>
            <div className="mt-1.5 text-[11.5px] text-slate-500">
              {r.ejemplo ? <>escribe <code className="font-mono text-slate-700">{r.ejemplo}</code></> : "ninguna de sus salidas trae número"}
            </div>
            <div className={`mt-0.5 text-[11.5px] ${r.t30 && r.c30 === 0 ? "font-semibold text-amber-700" : "text-slate-400"}`}>
              últimos 30 días: {r.t30 ? `${r.c30} de ${r.t30} con número` : "sin salidas"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TarjetaFba({ grupo, mfn }: { grupo: ResumenGrupo | null; mfn: number | null }) {
  return (
    <div className="mt-3 rounded-xl border border-[#f1e0c0] bg-[#FFF4E0] px-3.5 py-3">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-[7px] text-[13px] font-extrabold text-[#131A22]">
          <span className="h-[9px] w-[9px] rounded-full bg-[#FF9900]" /> FBA · Amazon
        </span>
        <span className="text-[11px] font-bold text-amber-800">San Corpe</span>
      </div>
      <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-[#7c5a1e]">
        <ChipFuente vivo />
        {grupo
          ? <><b>{num(grupo.piezas_enviadas)}</b> piezas en {grupo.hechas} salidas validadas{grupo.abiertas ? ` · ${grupo.abiertas} abiertas (${num(grupo.piezas_abiertas)} pzs)` : ""}</>
          : "leyendo Odoo…"}
      </p>
      <div className="mt-2 flex items-center gap-2"><ChipFuente vivo={false} /><span className="text-[10.5px] text-[#7c5a1e]">stock en FBA</span></div>
      <div className="mt-1.5 grid grid-cols-3 gap-2">
        {([["disponibles", FBA.disponibles], ["reservadas", FBA.reservadas], ["en camino", FBA.enCamino]] as const).map(([r, v]) => (
          <div key={r}>
            <div className="font-mono text-[17px] font-extrabold tabular-nums text-[#131A22]">{num(v)}</div>
            <div className="text-[10.5px] text-[#7c5a1e]">{r}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-[#7c5a1e]">
        <b>Recibidas y rechazadas: sin dato</b> — la app de Amazon no tiene permiso de Inbound (403) y el número
        de envío FBA no se captura en Odoo.{mfn ? ` Las ${mfn} salidas AMAZON de menos de 40 piezas son ventas MFN y no cuentan aquí.` : ""}
      </p>
    </div>
  );
}

function TarjetaWfs({ grupo }: { grupo: ResumenGrupo | null }) {
  return (
    <div className="mt-3 rounded-xl border border-[#bcd9f6] bg-[#E6F1FC] px-3.5 py-3">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-[7px] text-[13px] font-extrabold text-[#0b3d73]">
          <span className="h-[9px] w-[9px] rounded-full bg-[#0071DC]" /> WFS · Walmart
        </span>
        <ChipFuente vivo />
      </div>
      <p className="mt-2 text-[12px] leading-relaxed text-[#0b3d73]">
        {grupo
          ? <><b>{grupo.salidas}</b> {grupo.salidas === 1 ? "envío" : "envíos"} en toda la historia: {num(grupo.piezas_enviadas)} piezas validadas en Odoo.</>
          : "leyendo Odoo…"}
      </p>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-500">
        Recibidas: <b>sin lectura</b> — la API de WFS en México sí responde, falta conectarla. Los socios
        «WALMART #…» son ventas surtidas desde bodega, no envíos a WFS.
      </p>
    </div>
  );
}

/**
 * El tablero con el canal Amazon o Walmart elegido. No reusa los KPIs de FULL:
 * pintar las cifras de Mercado Libre bajo el chip de Amazon sería mentir con
 * el color. Lo que no tiene fuente va rayado entero.
 */
function SoloPrograma({ canal, grupo, mfn }: {
  canal: "amazon" | "walmart"; grupo: ResumenGrupo | null; mfn: number | null;
}) {
  const amazon = canal === "amazon";
  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi icono={<Truck className="h-3.5 w-3.5" />} rotulo={amazon ? "Enviado a FBA" : "Enviado a WFS"} vivo
             cifra={grupo ? num(grupo.piezas_enviadas) : "…"}
             pie={grupo ? `piezas en ${grupo.hechas} salidas validadas${grupo.abiertas ? ` · ${grupo.abiertas} abiertas` : ""}` : "leyendo Odoo…"} />
        {amazon ? (
          <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Disponibles en FBA" cifra={num(FBA.disponibles)}
               pie={`${num(FBA.reservadas)} reservadas · ${num(FBA.enCamino)} en camino`} />
        ) : (
          <KpiHueco rotulo="Stock en WFS" nota="la API de inventario WFS responde 401" />
        )}
        <KpiHueco rotulo="Tasa de recepción"
                  nota={amazon ? "la app no tiene permiso de Inbound (403)" : "falta leer los envíos de WFS"} />
        <KpiHueco rotulo="Recibidas · rechazadas"
                  nota={amazon ? "saldrán del Inventory Ledger y de los envíos cerrados" : "la API da recibidas por SKU; falta conectarla"} />
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Tarjeta>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <Ceja>Requisito · gráfica principal</Ceja>
              <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
                Tasa de éxito del envío a {amazon ? "FBA" : "WFS"}
              </h2>
            </div>
            <ChipSinRegistro texto="aún no medido" />
          </div>
          <div className="mt-3.5 flex min-h-[196px] items-center justify-center rounded-xl px-5 py-6 text-center" style={RAYADO}>
            <p className="max-w-[520px] text-sm font-semibold text-slate-600">
              {amazon
                ? "Amazon da recibidas y discrepancias por envío cuando se cierra, pero la app actual no tiene el permiso de Inbound. Mientras tanto no se pinta ni una barra."
                : "El único envío a WFS ya tiene recibidas en la API de Walmart; falta conectarla. Mientras tanto no se pinta ni una barra."}
            </p>
          </div>
        </Tarjeta>
        <Tarjeta>
          <Ceja>Programa</Ceja>
          {amazon ? <TarjetaFba grupo={grupo} mfn={mfn} /> : <TarjetaWfs grupo={grupo} />}
        </Tarjeta>
      </div>
    </>
  );
}

function KpiHueco({ rotulo, nota }: { rotulo: string; nota: string }) {
  return (
    <div className="rounded-2xl p-4" style={RAYADO}>
      <div className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
      <div className="mt-2"><ChipSinRegistro /></div>
      <div className="mt-2 text-xs text-slate-500">{nota}</div>
    </div>
  );
}
