"use client";

/**
 * FULLFILMENT · Tablero — KPIs, la gráfica obligatoria, el embudo, los días de
 * proceso, el agotado en FULL, FBA, WFS y la calidad de captura por KAM.
 *
 * Dos de los cinco escalones del embudo existen. Pintar 0 en los otros tres
 * sería mentir con una cifra que la gente creería: por eso van rayados.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Boxes, Link2Off, PackageCheck, Truck } from "lucide-react";
import {
  CAPTURA_KAM, DIAS, FBA, FECHA_DISENO, FULL_POR_CUENTA, TABLERO,
} from "./datosDiseno";
import {
  Ceja, ChipDatosDesde, ChipSinRegistro, PUNTO_CUENTA, RAYADO, Tarjeta, num,
} from "./ui";
import type { FiltroCanal, FiltroCuenta } from "./tipos";

export default function Tablero({ canal, cuenta }: { canal: FiltroCanal; cuenta: FiltroCuenta }) {
  if (canal === "amazon" || canal === "walmart") return <SoloPrograma canal={canal} />;

  const t = TABLERO;
  const cuentas = FULL_POR_CUENTA.filter((c) => cuenta === "todas" || c.cuenta === cuenta);
  const hoy = cuentas.reduce((a, c) => a + c.piezas, 0);
  const enCero = cuentas.reduce((a, c) => a + c.enCero, 0);
  const publicaciones = cuentas.reduce((a, c) => a + c.publicaciones, 0);

  return (
    <>
      {/* ── KPIs ─────────────────────────────────────────────────────────── */}
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        {/* El socio FULL de Odoo NO distingue cuenta: con el filtro puesto, la
            cifra sigue siendo la de las dos y se dice, en vez de partirla. */}
        <Kpi icono={<Truck className="h-3.5 w-3.5" />} rotulo="Enviado a FULL"
             cifra={num(t.enviadoFull.piezas)}
             pie={cuenta === "todas"
               ? `piezas en ${t.enviadoFull.ordenes} órdenes · ${t.enviadoFull.desde} → ${t.enviadoFull.hasta}`
               : `las dos cuentas: Odoo todavía no separa ${cuenta}`} />
        <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Hoy en FULL"
             cifra={num(hoy)}
             pie={cuentas.map((c) => `${c.cuenta} ${num(c.piezas)}`).join(" · ")} />
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-rose-800">
            <AlertTriangle className="h-3.5 w-3.5" /> Agotado en FULL
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
            el aviso de ML se borra a los 3 días; el registro arranca el {FECHA_DISENO.replace(" 2026", "")}
          </div>
        </div>
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-4">
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-amber-700">
            <Link2Off className="h-3.5 w-3.5" /> Envíos sin enlazar
          </div>
          <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-amber-700">
            {t.sinEnlazar.ordenes}
          </div>
          <div className="mt-1 text-xs text-amber-800">
            de {t.sinEnlazar.de} órdenes ({t.sinEnlazar.pct}%) sin número de envío teclado
          </div>
        </div>
      </div>

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
            <ChipDatosDesde desde={FECHA_DISENO} />
          </div>
          <div className="mt-3.5 flex min-h-[196px] items-center justify-center rounded-xl px-5 py-6 text-center" style={RAYADO}>
            <div className="max-w-[520px]">
              <ChipSinRegistro texto="aún no medido" />
              <p className="mt-3 text-sm font-semibold text-slate-700">
                No hay historia de recepciones: el aviso de Mercado Libre se retiene 3 días
                y el desglose de lo rechazado se descarga y se desecha.
              </p>
              <p className="mt-2 text-[12.5px] leading-relaxed text-slate-500">
                La serie arranca el día que el panel empiece a guardar cada{" "}
                <code className="font-mono text-[11.5px] text-slate-700">INBOUND_RECEPTION</code>{" "}
                con su número de envío, más lo que la API deje recuperar hacia atrás.
                Esto no es un cero: es un hueco, y por eso se pinta rayado.
              </p>
              <p className="mt-2.5 text-xs text-slate-400">
                En los 3 días retenidos no hubo ninguna recepción — sí {t.ventana3Dias.retiros} retiros,{" "}
                {t.ventana3Dias.ventas} ventas y {t.ventana3Dias.ajustes} ajustes.
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
            Tres formas propuestas de esta gráfica, ya con datos simulados, en la pantalla <b>Variaciones</b>.
          </p>
        </Tarjeta>

        <Tarjeta>
          <Ceja>Embudo de piezas · lo que ya se puede medir</Ceja>
          <div className="mt-3.5 flex flex-col gap-2">
            <Escalon hueco titulo="Solicitadas" nota="La lista de Andy no vive en ningún sistema todavía." />
            <Escalon hueco titulo="Validadas por Bodega"
                     nota="La cantidad de Odoo ya trae el recorte: tomarla de ahí pondría la tasa en 100%." />
            <div className="rounded-[10px] border border-emerald-200 bg-emerald-50 px-3 py-2.5">
              <div className="flex items-baseline justify-between">
                <span className="text-[12.5px] font-bold text-emerald-800">Enviadas</span>
                <span className="font-mono text-[15px] font-extrabold tabular-nums text-emerald-800">
                  {num(t.enviadoFull.piezas)}
                </span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-emerald-100">
                <div className="h-full rounded-full bg-emerald-600" style={{ width: `${t.enviadoFull.pctDePedido}%` }} />
              </div>
              <p className="mt-1 text-[11.5px] text-emerald-700">
                {t.enviadoFull.pctDePedido}% de lo pedido en la orden de salida · sólo el paso{" "}
                <code className="font-mono">outgoing</code>, nunca PICK/PACK
              </p>
            </div>
            <Escalon hueco titulo="Recibidas · rechazadas" nota="Empieza el día que se guarde el aviso del almacén FULL." />
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
              <Ceja>Qué días se procesan los envíos · lo medido, no el calendario</Ceja>
              <h2 className="mt-1 text-base font-extrabold text-slate-900">
                {t.enviadoFull.ordenes} órdenes de salida a FULL, por día de la semana
              </h2>
            </div>
            <span className="text-[11px] text-slate-400">fechas de Odoo convertidas a hora de CDMX</span>
          </div>
          <DiasProceso />
          <div className="mt-4 grid gap-2.5 sm:grid-cols-3">
            <Dato rotulo="De orden a salida" cifra={`${t.proceso.medianaDias} días`}
                  pie={`mediana · ${t.proceso.p90Dias} días en el peor 10%`} />
            <Dato rotulo="El calendario ideal" cifra="martes pedir · miércoles salir"
                  pie="el miércoles es el día con MENOS salidas" chico />
            <Dato rotulo="Lo que dicen los datos" cifra="dos ciclos por semana"
                  pie="picos en martes (52) y jueves (60)" chico />
          </div>
        </Tarjeta>

        <div className="flex flex-col gap-3">
          <Tarjeta>
            <Ceja>Agotado en FULL · por cuenta</Ceja>
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
              Estas sí son <b>ceros reales</b>: la publicación existe, está marcada FULL y no tiene
              ni una pieza en el almacén del marketplace.
            </p>
          </Tarjeta>

          {canal === "todos" && (
            <Tarjeta>
              <Ceja>Los otros dos programas</Ceja>
              <TarjetaFba />
              <TarjetaWfs />
            </Tarjeta>
          )}
        </div>
      </div>

      {/* ── Captura por KAM ──────────────────────────────────────────────── */}
      <Tarjeta className="mt-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <Ceja>El enlace entre Odoo y Mercado Libre · calidad de captura por KAM</Ceja>
          <span className="text-[11px] text-slate-400">el número de envío se teclea a mano en la referencia de la orden</span>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {CAPTURA_KAM.map((k) => {
            const pct = (k.conNumero / k.ordenes) * 100;
            return (
              <div key={k.kam} className="rounded-xl border border-slate-200 px-3.5 py-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-[13px] font-bold text-slate-700">{k.kam}</span>
                  <span className="font-mono text-[13px] font-extrabold tabular-nums text-slate-900">
                    {k.conNumero} <span className="text-[11px] font-normal text-slate-400">/ {k.ordenes}</span>
                  </span>
                </div>
                <div className="mt-1.5 h-[5px] overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full rounded-full" style={{ width: `${pct}%`, background: pct >= 75 ? "#059669" : "#f59e0b" }} />
                </div>
                <div className="mt-1.5 text-[11.5px] text-slate-500">
                  escribe <code className="font-mono text-slate-700">{k.formato}</code>
                </div>
              </div>
            );
          })}
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-bold text-slate-700">Vale</span>
              <span className="text-xs text-slate-500">3 órdenes</span>
            </div>
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-500">
              Dos personas, dos formatos. <b>Una de cada cuatro órdenes no trae número</b>: ese envío
              salió de bodega y no se le puede calcular tasa de recepción.
            </p>
          </div>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Propuesta: que la solicitud <b>capture el número desde el sistema</b> en vez de depender de que
          alguien lo teclee. Mientras no exista, el estado <b>«envío sin enlazar»</b> es un ciudadano de
          primera en la tabla de Envíos.
        </p>
      </Tarjeta>
    </>
  );
}

function Kpi({ icono, rotulo, cifra, pie }: { icono: ReactNode; rotulo: string; cifra: string; pie: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">
        {icono} {rotulo}
      </div>
      <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-slate-900">{cifra}</div>
      <div className="mt-1 text-xs text-slate-500">{pie}</div>
    </div>
  );
}

function Escalon({ titulo, nota, hueco }: { titulo: string; nota: string; hueco?: boolean }) {
  return (
    <div className="rounded-[10px] px-3 py-2.5" style={hueco ? RAYADO : undefined}>
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

/** Mapa de calor lun–sáb: orden creada (esmeralda) contra salida hecha (azul). */
function DiasProceso() {
  const maxO = Math.max(...DIAS.map((d) => d.ordenes));
  const maxS = Math.max(...DIAS.map((d) => d.salidas));
  // El mínimo se busca en días HÁBILES: el sábado es medio turno y siempre
  // "ganaría", escondiendo el hallazgo (el miércoles del proceso ideal es el
  // día hábil que menos saca).
  const minS = Math.min(...DIAS.filter((d) => d.dia !== "sáb").map((d) => d.salidas));
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
  return (
    <div className="mt-4 flex items-end gap-3">
      <div className="flex flex-col gap-1.5 pb-[22px]">
        <span className="flex h-[52px] items-center text-[11px] font-bold text-emerald-700">orden creada</span>
        <span className="flex h-[52px] items-center text-[11px] font-bold text-sky-700">salida hecha</span>
      </div>
      <div className="grid flex-1 grid-cols-6 gap-1.5">
        {DIAS.map((d) => (
          <div key={`o-${d.dia}`}>
            {celda(d.ordenes, Math.max(0.2, d.ordenes / maxO), "5,150,105",
              `${d.nombre} · ${d.ordenes} órdenes creadas${d.ordenes === maxO ? " — el pico" : ""}`)}
          </div>
        ))}
        {DIAS.map((d) => (
          <div key={`s-${d.dia}`}>
            {celda(d.salidas, Math.max(0.22, d.salidas / maxS * 0.83), "3,105,161",
              `${d.nombre} · ${d.salidas} salidas validadas${d.salidas === minS ? " — el día hábil con MENOS salidas" : ""}`,
              d.salidas === minS)}
          </div>
        ))}
        {DIAS.map((d) => (
          <div key={`d-${d.dia}`} className="text-center text-[11px] font-semibold text-slate-500">{d.dia}</div>
        ))}
      </div>
    </div>
  );
}

function TarjetaFba() {
  return (
    <div className="mt-3 rounded-xl border border-[#f1e0c0] bg-[#FFF4E0] px-3.5 py-3">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-[7px] text-[13px] font-extrabold text-[#131A22]">
          <span className="h-[9px] w-[9px] rounded-full bg-[#FF9900]" /> FBA · Amazon
        </span>
        <span className="text-[11px] font-bold text-amber-800">{FBA.cuenta}</span>
      </div>
      <div className="mt-2.5 grid grid-cols-3 gap-2">
        {([["disponibles", FBA.disponibles], ["reservadas", FBA.reservadas], ["en camino", FBA.enCamino]] as const).map(([r, v]) => (
          <div key={r}>
            <div className="font-mono text-[17px] font-extrabold tabular-nums text-[#131A22]">{num(v)}</div>
            <div className="text-[10.5px] text-[#7c5a1e]">{r}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-[#7c5a1e]">
        {num(FBA.enPreparacion)} en preparación · {FBA.enviadas} enviadas · {FBA.recibiendose} recibiéndose.{" "}
        {FBA.ordenes} órdenes de salida, {num(FBA.piezas)} piezas, {FBA.pctDePedido}% de lo pedido.{" "}
        <b>Recibidas y rechazadas: sin dato</b> — Amazon no se consulta todavía.
      </p>
    </div>
  );
}

function TarjetaWfs() {
  return (
    <div className="mt-3 rounded-xl px-3.5 py-3" style={RAYADO}>
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-[7px] text-[13px] font-extrabold text-slate-600">
          <span className="h-[9px] w-[9px] rounded-full bg-[#0071DC]" /> WFS · Walmart
        </span>
        <ChipSinRegistro />
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-slate-500">
        Ni una orden en Odoo con socio Walmart, ni lectura de inventario, ni de envíos.
        Todo el programa está rayado a propósito: el hueco es el dato.
      </p>
      <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-500">
        Pendiente con el equipo: <b>quién opera WFS</b> — la lista dice «WFS (Cin)» y
        Cinthya creó 90 órdenes con socio FULL.
      </p>
    </div>
  );
}

/**
 * El tablero con el canal Amazon o Walmart elegido. No reusa los KPIs de FULL:
 * pintar las cifras de Mercado Libre bajo el chip de Amazon sería mentir con
 * el color. Lo que no tiene fuente va rayado entero.
 */
function SoloPrograma({ canal }: { canal: "amazon" | "walmart" }) {
  const amazon = canal === "amazon";
  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {amazon ? (
          <>
            <Kpi icono={<Truck className="h-3.5 w-3.5" />} rotulo="Enviado a FBA" cifra={num(FBA.piezas)}
                 pie={`piezas en ${FBA.ordenes} órdenes de salida · ${FBA.pctDePedido}% de lo pedido`} />
            <Kpi icono={<Boxes className="h-3.5 w-3.5" />} rotulo="Disponibles en FBA" cifra={num(FBA.disponibles)}
                 pie={`${num(FBA.reservadas)} reservadas · ${num(FBA.enCamino)} en camino`} />
          </>
        ) : (
          <>
            <KpiHueco rotulo="Enviado a WFS" nota="ni una orden en Odoo con socio Walmart" />
            <KpiHueco rotulo="Stock en WFS" nota="no se lee el inventario de Walmart" />
          </>
        )}
        <KpiHueco rotulo="Tasa de recepción"
                  nota={amazon ? "la Inbound API (getShipments) aún no se consulta" : "sin fuente verificada en MX"} />
        <KpiHueco rotulo="Recibidas · rechazadas"
                  nota={amazon ? "saldrán del Inventory Ledger y de los envíos cerrados" : "falta medir inbound-shipments con un envío real"} />
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
                ? "Amazon da recibidas y discrepancias por envío cuando se cierra; el panel todavía no lo lee. Mientras tanto no se pinta ni una barra."
                : "No hay un solo envío a WFS registrado en ningún sistema. El programa entero es un hueco, y así se pinta."}
            </p>
          </div>
        </Tarjeta>
        <Tarjeta>
          <Ceja>Programa</Ceja>
          {amazon ? <TarjetaFba /> : <TarjetaWfs />}
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
