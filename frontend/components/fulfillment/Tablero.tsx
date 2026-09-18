"use client";

/**
 * FULLFILMENT · Tablero — KPIs, la gráfica obligatoria, el embudo, los días de
 * proceso, el agotado en FULL, FBA, WFS y la calidad de captura por KAM.
 *
 * Desde v0.546.0 todo lo que tiene fuente es REAL (Brandon, 18-sep: "ya con
 * estos datos llena de datos reales el tablero"). Cada cifra lleva su chip:
 *   · «Odoo en vivo»  — enviado, validado, lo no surtido, sin número, días y
 *                       captura por KAM;
 *   · «ML en vivo»    — la llegada a FULL por los avisos de Mercado Libre
 *                       (fbm_stock_operations): recibido, no recibido, en
 *                       recepción, tiempos y lo vendido desde que llegó; y el
 *                       stock de hoy (channel.listings, que el sync y los avisos
 *                       actualizan en minutos);
 *   · «Amazon en vivo» — lo disponible en FBA.
 * Lo que sigue sin fuente —la lista de Andy, lo reservado y en camino de FBA,
 * WFS— va rayado: pintar 0 ahí sería mentir con una cifra que la gente creería.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Boxes, Link2Off, PackageCheck, Truck } from "lucide-react";
import {
  Ceja, ChipFuente, ChipSinRegistro, DIAS_NOMBRE, DIAS_SEMANA, FONDO_RAYADO_AMBAR, PUNTO_CUENTA, RAYADO,
  Tarjeta, dia, num,
} from "./ui";
import type {
  Cuenta, Envio, FiltroCanal, FiltroCuenta, Mediana, RespuestaEnvios, ResumenGrupo, ResumenRecepcion,
  SemanaRecepcion, StockCuenta, StockHoy,
} from "./tipos";

const TIT_AVISOS = "Avisos de FULL de Mercado Libre (webhook fbm_stock_operations) que el backend resuelve a "
  + "tipo, piezas y SKU. Cada operación cuenta una vez.";
const TIT_STOCK = "channel.listings de kubera: el sync de canales y los avisos de ML lo actualizan en minutos. "
  + "Cuadra con la API de ML (18-sep: 1,010 contra 996 publicaciones FULL en Kubera, 867 contra 866 en San Corpe).";
const RAYADO_ROSA = "repeating-linear-gradient(135deg,#fecdd3 0 4px,#fb7185 4px 8px)";

export default function Tablero({
  canal, cuenta, datos,
}: { canal: FiltroCanal; cuenta: FiltroCuenta; datos: RespuestaEnvios | null }) {
  if (canal === "amazon" || canal === "walmart") {
    return <SoloPrograma canal={canal} grupo={datos?.resumen[canal] ?? null}
                         mfn={datos?.excluidas.venta_amazon_mfn ?? null} fba={datos?.stock?.fba ?? null} />;
  }

  const clave = cuenta === "todas" ? "meli" : `meli:${cuenta}`;
  const g = datos?.resumen[clave] ?? null;
  const r = datos?.recepcion?.[clave] ?? null;
  const sinAsignar = datos?.resumen["meli:sin_asignar"] ?? null;
  const stock: StockHoy | null = datos?.stock ?? null;
  const cuentas = (["Kubera", "San Corpe"] as Cuenta[])
    .filter((c) => cuenta === "todas" || c === cuenta)
    .flatMap((c) => (stock?.full[c] ? [{ cuenta: c, s: stock.full[c] as StockCuenta }] : []));
  const hoy = cuentas.reduce((a, c) => a + c.s.piezas, 0);
  const enCero = cuentas.reduce((a, c) => a + c.s.en_cero, 0);
  const publicaciones = cuentas.reduce((a, c) => a + c.s.publicaciones, 0);
  const conStock = cuentas.reduce((a, c) => a + c.s.con_stock, 0);
  const pctPedido = g && g.piezas_pedidas_hechas
    ? Math.round((g.piezas_enviadas / g.piezas_pedidas_hechas) * 1000) / 10 : null;
  const leyendo = datos ? "kubera no contestó" : "leyendo…";

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
        {cuentas.length ? (
          <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Hoy en FULL" vivo fuente="ML en vivo" tituloFuente={TIT_STOCK}
               cifra={num(hoy)}
               pie={`${cuentas.map((c) => `${c.cuenta} ${num(c.s.piezas)}`).join(" · ")} · en ${num(conStock)} publicaciones con stock`} />
        ) : (
          <KpiHueco rotulo="Hoy en FULL" nota={leyendo} />
        )}
        {publicaciones ? (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-rose-800">
                <AlertTriangle className="h-3.5 w-3.5" /> Agotado en FULL
              </span>
              <ChipFuente vivo texto="ML en vivo" titulo={TIT_STOCK} />
            </div>
            <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-rose-800">
              {Math.round((enCero / publicaciones) * 100)}%
            </div>
            <div className="mt-1 text-xs text-rose-800/85">
              {num(enCero)} de {num(publicaciones)} publicaciones FULL en cero
            </div>
          </div>
        ) : (
          <KpiHueco rotulo="Agotado en FULL" nota={leyendo} />
        )}
        <KpiTasa r={r} cargando={!datos} />
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
          que no es Thalia ni Cinthya, y la regla no adivina. Cuentan en «Todas» y no en ninguna cuenta, y su llegada
          no se mide: sin cuenta no se sabe qué almacén de ML mirar.
        </div>
      )}

      {/* ── Gráfica obligatoria + embudo ─────────────────────────────────── */}
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Tarjeta>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <div className="flex items-center gap-2">
                <Ceja>Requisito · gráfica principal</Ceja>
                <ChipFuente vivo texto="ML en vivo" titulo={TIT_AVISOS} />
              </div>
              <h2 className="mt-1 text-[17px] font-extrabold tracking-tight text-slate-900">
                Tasa de éxito del envío — piezas recibidas contra no recibidas
              </h2>
            </div>
            <span className="text-[11px] text-slate-400">por semana de la salida validada · hora de CDMX</span>
          </div>
          {r ? <GraficaSemanas semanas={r.semanas} />
             : <div className="mt-3.5 flex min-h-[190px] items-center justify-center rounded-xl text-sm text-slate-500" style={RAYADO}>
                 {datos ? "Todavía no hay salidas validadas con avisos de ML en esta vista." : "Leyendo Odoo y los avisos de ML…"}
               </div>}
          {r && <ResumenGrafica r={r} />}
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3">
            <p className="text-[12.5px] leading-relaxed text-rose-800">
              <b>Regla (Brandon, 18-sep):</b> lo que salió de Odoo y <b>10 días después</b> no llegó a FULL cuenta como{" "}
              <b>no recibido por ML</b>. Antes de eso va en ámbar: <b>en recepción</b>, no es rechazo. ML no publica
              la cantidad rechazada ni el motivo: es lo enviado que no llegó.
            </p>
          </div>
          {r && r.peores.length > 0 && <Peores peores={r.peores} />}
        </Tarjeta>

        <Tarjeta>
          <Ceja>Embudo de piezas · dónde se pierde la mercancía</Ceja>
          <div className="mt-3.5 flex flex-col gap-2">
            <Escalon titulo="Solicitadas" nota="La lista de Andy no vive en ningún sistema: Andy no aparece creando ni validando salidas en Odoo." />
            <EscalonDato titulo="Validadas por Bodega" cifra={g ? num(g.piezas_pedidas_hechas) : "…"}
                         nota="Lo que pide la orden de venta: ya trae el recorte de Bodega. Sin la lista de Andy no se sabe cuánto recortó." />
            <EscalonDato titulo="Enviadas" cifra={g ? num(g.piezas_enviadas) : "…"} pct={pctPedido}
                         nota={g
                           ? `${pctPedido ?? "—"}% de lo validado · Odoo no surtió ${num(g.piezas_no_surtidas ?? 0)} piezas en `
                             + `${g.salidas_con_faltante ?? 0} salidas (bodega no las tenía al validar)`
                           : "—"} />
            {r && r.enviadas_cerradas ? (
              <EscalonDato titulo="Recibidas por ML" fuente="ML en vivo" tituloFuente={TIT_AVISOS}
                           cifra={num(r.recibidas_cerradas)} pct={r.tasa_recepcion}
                           nota={`${r.tasa_recepcion}% de ${num(r.enviadas_cerradas)} en ${r.cerrados} envíos cerrados desde el `
                             + `${dia(r.desde)} · ML no recibió ${num(r.no_recibidas)}`
                             + (r.en_proceso ? ` · ${num(r.enviadas_en_proceso - r.recibidas_en_proceso)} en recepción` : "")} />
            ) : (
              <Escalon titulo="Recibidas por ML" nota="Todavía no cierra ningún envío con avisos (10 días tras la salida)." />
            )}
            {r && r.recibidas ? (
              <EscalonDato titulo="Vendidas desde FULL" fuente="ML en vivo" tituloFuente={TIT_AVISOS}
                           cifra={`~${num(r.vendidas)}`} pct={Math.round((r.vendidas / r.recibidas) * 1000) / 10}
                           nota={`~${Math.round((r.vendidas / r.recibidas) * 100)}% de ${num(r.recibidas)} recibidas · aprox.: ventas FULL `
                             + "de cada SKU desde que llegó, topadas a lo que llegó (si ya había piezas de antes, una venta pudo salir de ésas)"} />
            ) : (
              <Escalon titulo="Vendidas desde FULL" nota="Sin llegadas medidas en esta vista." />
            )}
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Ojo con las bases: Validadas y Enviadas cuentan toda la historia de Odoo{g ? ` (desde el ${dia(g.desde)})` : ""};
            Recibidas y Vendidas, sólo las salidas con avisos de ML{r ? ` (desde el ${dia(r.desde)})` : ""}. Solicitadas sigue
            sin fuente.
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
          <div className="mt-2.5 grid gap-2.5 sm:grid-cols-2">
            <Dato rotulo="De salida validada a la 1ª llegada a FULL" cifra={dias(r?.salida_a_primera_llegada_dias)}
                  pie={pieMediana(r?.salida_a_primera_llegada_dias, "envíos",
                                  "puede ser negativa: ML a veces recibe antes de que bodega valide")}
                  fuente="ML en vivo" />
            <Dato rotulo="De salida validada a envío completo" cifra={dias(r?.salida_a_completo_dias)}
                  pie={pieMediana(r?.salida_a_completo_dias, "envíos completos",
                                  "hasta la tanda con la que el último SKU alcanzó lo enviado")}
                  fuente="ML en vivo" />
          </div>
        </Tarjeta>

        <div className="flex flex-col gap-3">
          <Tarjeta>
            <div className="flex items-center justify-between gap-2">
              <Ceja>Agotado en FULL · por cuenta</Ceja>
              <ChipFuente vivo texto="ML en vivo" titulo={TIT_STOCK} />
            </div>
            {cuentas.length ? (
              <div className="mt-3 flex flex-col gap-3.5">
                {cuentas.map(({ cuenta: c, s }) => (
                  <div key={c}>
                    <div className="flex items-baseline justify-between">
                      <span className="inline-flex items-center gap-[7px] text-[13px] font-bold text-slate-700">
                        <span className="h-[9px] w-[9px] rounded-full" style={{ background: PUNTO_CUENTA[c] }} />
                        {c}
                      </span>
                      <span className="text-xs text-slate-500">
                        <b className="font-mono text-rose-800">{num(s.en_cero)}</b> de {num(s.publicaciones)} en cero
                      </span>
                    </div>
                    <div className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full bg-rose-600" style={{ width: `${(s.en_cero / Math.max(1, s.publicaciones)) * 100}%` }} />
                    </div>
                    <div className="mt-1 text-[11.5px] text-slate-400">
                      {num(s.piezas)} piezas en {num(s.con_stock)} publicaciones con stock
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-slate-400">{datos ? "Sin lectura del stock: kubera no contestó." : "Leyendo…"}</p>
            )}
            <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
              <b>Ceros reales</b>: la publicación existe, está marcada FULL y no tiene ni una pieza en el almacén de
              Mercado Libre.
            </p>
          </Tarjeta>

          {canal === "todos" && (
            <Tarjeta>
              <Ceja>Los otros dos programas</Ceja>
              <TarjetaFba grupo={datos?.resumen.amazon ?? null} mfn={datos?.excluidas.venta_amazon_mfn ?? null}
                          fba={datos?.stock?.fba ?? null} />
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

function dias(m: Mediana | undefined): string {
  return m?.mediana != null ? `${m.mediana} días` : "—";
}

function pieMediana(m: Mediana | undefined, unidad: string, nota: string): string {
  if (!m || !m.n) return `sin ${unidad} medidos todavía · ${nota}`;
  return `mediana · ${m.p90} días en el peor 10% · ${m.n} ${unidad} · ${nota}`;
}

function Kpi({ icono, rotulo, cifra, pie, vivo, fuente, tituloFuente }: {
  icono: ReactNode; rotulo: string; cifra: string; pie: string; vivo?: boolean; fuente?: string; tituloFuente?: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">
          {icono} {rotulo}
        </span>
        <ChipFuente vivo={!!vivo} texto={fuente} titulo={tituloFuente} />
      </div>
      <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-slate-900">{cifra}</div>
      <div className="mt-1 text-xs text-slate-500">{pie}</div>
    </div>
  );
}

/** La tasa de recepción: sólo envíos CERRADOS. Verde ≥97%, ámbar ≥90%, rosa abajo. */
function KpiTasa({ r, cargando }: { r: ResumenRecepcion | null; cargando: boolean }) {
  if (!r || r.tasa_recepcion === null) {
    return <KpiHueco rotulo="Tasa de recepción"
                     nota={cargando ? "leyendo avisos de ML…" : "todavía no cierra ningún envío (10 días tras la salida)"} />;
  }
  const t = r.tasa_recepcion;
  const tono = t >= 97 ? "border-emerald-200 bg-emerald-50 text-emerald-800"
    : t >= 90 ? "border-amber-300 bg-amber-50 text-amber-800" : "border-rose-200 bg-rose-50 text-rose-800";
  return (
    <div className={`rounded-2xl border p-4 ${tono}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em]">
          <PackageCheck className="h-3.5 w-3.5" /> Tasa de recepción
        </span>
        <ChipFuente vivo texto="ML en vivo" titulo={TIT_AVISOS} />
      </div>
      <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums">{t}%</div>
      <div className="mt-1 text-xs opacity-85">
        {num(r.recibidas_cerradas)} de {num(r.enviadas_cerradas)} pzs en {r.cerrados} envíos cerrados · ML no recibió{" "}
        {num(r.no_recibidas)}
      </div>
    </div>
  );
}

/** La gráfica obligatoria (forma A1 de Variaciones): barras apiladas por semana, con datos reales. */
function GraficaSemanas({ semanas }: { semanas: SemanaRecepcion[] }) {
  const max = Math.max(1, ...semanas.map((s) => s.enviadas));
  const alto = (v: number) => (v / max) * 150;
  return (
    <>
      <div className="mt-4 flex h-[200px] items-end gap-2.5">
        {semanas.map((s) => {
          const noDudosas = s.no_recibidas - s.dudosas;
          const cerrada = s.en_recepcion === 0 && s.recibidas + s.no_recibidas > 0;
          const tasa = cerrada ? Math.round((s.recibidas / (s.recibidas + s.no_recibidas)) * 1000) / 10 : null;
          const titulo = s.envios === 0
            ? `Semana del ${dia(s.lunes)}: ninguna salida a FULL validada (cero real)`
            : `Semana del ${dia(s.lunes)} · ${s.envios} envíos · ${num(s.enviadas)} enviadas: ${num(s.recibidas)} recibidas`
              + (s.no_recibidas ? `, ${num(s.no_recibidas)} no recibidas` : "")
              + (s.dudosas ? ` (${num(s.dudosas)} podrían ser publicaciones con variantes)` : "")
              + (s.en_recepcion ? `, ${num(s.en_recepcion)} en recepción` : "");
          return (
            <div key={s.lunes} className="flex min-w-0 flex-1 flex-col justify-end" title={titulo}>
              <div className={`mb-1 text-center font-mono text-[10.5px] font-bold tabular-nums ${
                tasa === null ? "text-amber-700" : tasa >= 97 ? "text-emerald-700" : tasa >= 90 ? "text-amber-700" : "text-rose-700"}`}>
                {s.envios === 0 ? "" : tasa !== null ? `${tasa}%` : "en curso"}
              </div>
              {s.envios === 0 ? (
                <div className="h-[3px] rounded bg-slate-200" />
              ) : (
                <div className="flex flex-col justify-end overflow-hidden rounded-t-[4px]">
                  {noDudosas > 0 && <div className="bg-rose-600" style={{ height: Math.max(3, alto(noDudosas)) }} />}
                  {s.dudosas > 0 && <div style={{ height: Math.max(3, alto(s.dudosas)), background: RAYADO_ROSA }} />}
                  {s.en_recepcion > 0 && (
                    <div className="border border-amber-300"
                         style={{ height: Math.max(3, alto(s.en_recepcion)), background: FONDO_RAYADO_AMBAR }} />
                  )}
                  {s.recibidas > 0 && <div className="bg-emerald-600" style={{ height: Math.max(3, alto(s.recibidas)) }} />}
                </div>
              )}
              <div className="mt-1.5 text-center text-[10.5px] font-semibold text-slate-600">{s.semana}</div>
              <div className="text-center text-[10px] text-slate-400">{dia(s.lunes)}</div>
            </div>
          );
        })}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-3 text-[11px] text-slate-500">
        <Muestra fondo="#059669" texto="recibido por ML" />
        <Muestra fondo="#e11d48" texto="no recibido (envío cerrado)" />
        <Muestra fondo={RAYADO_ROSA} texto="no recibido, con avisos sin SKU (variantes)" />
        <Muestra fondo={FONDO_RAYADO_AMBAR} borde="1px solid #FCD34D" texto="en recepción (aún no cierra)" />
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

function ResumenGrafica({ r }: { r: ResumenRecepcion }) {
  const enRecepcion = r.enviadas_en_proceso - r.recibidas_en_proceso;
  const celda = (rotulo: string, cifra: string, pie: string, clase: string) => (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5">
      <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
      <div className={`mt-0.5 font-mono text-lg font-extrabold tabular-nums ${clase}`}>{cifra}</div>
      <div className="text-[11px] text-slate-500">{pie}</div>
    </div>
  );
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
      {celda("Enviadas", num(r.enviadas), `${r.envios} envíos desde el ${dia(r.desde)}`, "text-slate-900")}
      {celda("Recibidas por ML", num(r.recibidas), `${r.skus_completos} de ${r.skus_cerrados} SKUs completos (cerrados)`, "text-emerald-700")}
      {celda("No recibidas", num(r.no_recibidas),
             r.no_recibidas_dudosas ? `${num(r.no_recibidas_dudosas)} podrían ser variantes` : `${r.envios_con_faltante} envíos con faltante`,
             "text-rose-700")}
      {celda("En recepción", num(enRecepcion), `${r.en_proceso} envíos aún sin cerrar`, "text-amber-700")}
    </div>
  );
}

function Peores({ peores }: { peores: ResumenRecepcion["peores"] }) {
  return (
    <div className="mt-3">
      <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
        Los envíos cerrados con más piezas que ML no recibió
      </div>
      <div className="mt-1.5 divide-y divide-slate-100 rounded-xl border border-slate-200">
        {peores.map((p) => (
          <div key={`${p.orden}-${p.salida}`} className="px-3 py-2 text-[12.5px]">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-mono font-bold text-slate-800">
                {p.orden}
                <span className="font-normal text-slate-400"> · {p.salida} · {p.cuenta} · salió {dia(p.validada)}</span>
              </span>
              <span className="font-semibold text-rose-700">no recibió {num(p.no_recibidas)} de {num(p.enviadas)}</span>
            </div>
            {(p.vieja || p.dudosa) && (
              <div className="mt-0.5 text-[11px] text-amber-700">
                {p.vieja ? `Orden del ${dia(p.creada)} validada el ${dia(p.validada)}: revisar si la salida fue física. ` : ""}
                {p.dudosa ? "Hubo avisos sin SKU (publicaciones con variantes) antes de su cierre: lo no recibido puede estar ahí." : ""}
              </div>
            )}
          </div>
        ))}
      </div>
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

function EscalonDato({ titulo, cifra, nota, pct, fuente, tituloFuente }: {
  titulo: string; cifra: string; nota: string; pct?: number | null; fuente?: string; tituloFuente?: string;
}) {
  return (
    <div className="rounded-[10px] border border-emerald-200 bg-emerald-50 px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-center gap-2 text-[12.5px] font-bold text-emerald-800">
          {titulo} <ChipFuente vivo texto={fuente} titulo={tituloFuente} />
        </span>
        <span className="font-mono text-[15px] font-extrabold tabular-nums text-emerald-800">{cifra}</span>
      </div>
      {pct != null && (
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-emerald-100">
          <div className="h-full rounded-full bg-emerald-600" style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
      )}
      <p className="mt-1 text-[11.5px] text-emerald-700">{nota}</p>
    </div>
  );
}

function Dato({ rotulo, cifra, pie, chico, fuente }: {
  rotulo: string; cifra: string; pie: string; chico?: boolean; fuente?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">{rotulo}</div>
        {fuente && <ChipFuente vivo texto={fuente} titulo={TIT_AVISOS} />}
      </div>
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

function TarjetaFba({ grupo, mfn, fba }: {
  grupo: ResumenGrupo | null; mfn: number | null; fba: StockHoy["fba"] | null;
}) {
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
      <div className="mt-2 flex items-center gap-2">
        <ChipFuente vivo texto="Amazon en vivo" titulo="channel.listings de kubera: el sync de Amazon. Sólo lo disponible." />
        <span className="text-[10.5px] text-[#7c5a1e]">stock en FBA</span>
      </div>
      <div className="mt-1.5 grid grid-cols-3 gap-2">
        <div>
          <div className="font-mono text-[17px] font-extrabold tabular-nums text-[#131A22]">{fba ? num(fba.piezas) : "—"}</div>
          <div className="text-[10.5px] text-[#7c5a1e]">disponibles{fba ? ` · ${fba.con_stock} publicaciones` : ""}</div>
        </div>
        {(["reservadas", "en camino"] as const).map((r) => (
          <div key={r} className="rounded-md px-1.5 py-1" style={RAYADO}>
            <div className="font-mono text-[10px] font-bold uppercase text-slate-400">sin registro</div>
            <div className="text-[10.5px] text-slate-500">{r}</div>
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
function SoloPrograma({ canal, grupo, mfn, fba }: {
  canal: "amazon" | "walmart"; grupo: ResumenGrupo | null; mfn: number | null; fba: StockHoy["fba"] | null;
}) {
  const amazon = canal === "amazon";
  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi icono={<Truck className="h-3.5 w-3.5" />} rotulo={amazon ? "Enviado a FBA" : "Enviado a WFS"} vivo
             cifra={grupo ? num(grupo.piezas_enviadas) : "…"}
             pie={grupo ? `piezas en ${grupo.hechas} salidas validadas${grupo.abiertas ? ` · ${grupo.abiertas} abiertas` : ""}` : "leyendo Odoo…"} />
        {amazon ? (
          fba ? (
            <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Disponibles en FBA" vivo fuente="Amazon en vivo"
                 tituloFuente="channel.listings de kubera: el sync de Amazon. Sólo lo disponible."
                 cifra={num(fba.piezas)} pie={`en ${fba.con_stock} publicaciones · reservadas y en camino: sin registro`} />
          ) : (
            <KpiHueco rotulo="Disponibles en FBA" nota="kubera no contestó" />
          )
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
          {amazon ? <TarjetaFba grupo={grupo} mfn={mfn} fba={fba} /> : <TarjetaWfs grupo={grupo} />}
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
