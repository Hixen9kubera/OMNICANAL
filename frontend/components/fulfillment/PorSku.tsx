"use client";

/**
 * FULLFILMENT · Por SKU / MLM — la vida de un producto en FULL: recepción,
 * activación, primera venta, visitas, sugerido, venta desde la activación,
 * precio y calificaciones.
 *
 * Cada evento dice de dónde sale su hora: el historial de cambios llega por
 * lotes (la hora es cuándo se OBSERVÓ) y el histórico de ventas empieza el
 * 27-dic-2025 (antes de eso, «primera venta» no es la primera de verdad).
 */

import { SKU_EJEMPLO } from "./datosDiseno";
import { Ceja, ChipSinRegistro, PUNTO_CUENTA, RAYADO, Tarjeta, fecha, num } from "./ui";

export default function PorSku() {
  const s = SKU_EJEMPLO;
  return (
    <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
      <Tarjeta>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Ceja>La vida del producto en FULL</Ceja>
            <h2 className="mt-1 flex flex-wrap items-center gap-2.5 text-[20px] font-extrabold tracking-tight text-slate-900">
              <span className="font-mono">{s.sku}</span>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-[3px] text-xs font-bold text-slate-600">
                <span className="h-2 w-2 rounded-full" style={{ background: PUNTO_CUENTA[s.cuenta] }} />
                {s.cuenta}
              </span>
            </h2>
            <p className="mt-1 text-[13px] text-slate-500">
              {s.nombre} · publicación{" "}
              {/* Sin enlace mientras sea dato de diseño: el MLM de ejemplo no existe. */}
              <span className="font-mono text-indigo-600">{s.publicacion}</span>{" "}
              · {s.tipo}
            </p>
          </div>
          <div className="text-right">
            <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums text-emerald-700">{s.enFullHoy}</div>
            <div className="mt-1 text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">piezas en FULL hoy</div>
            <div className="mt-1 text-xs text-slate-500">
              cobertura <b>{s.coberturaDias} días</b> · {s.piezasDia} piezas/día
            </div>
          </div>
        </div>

        <div className="relative mt-5 flex flex-col gap-3.5 border-l-2 border-slate-100 pl-3.5">
          {/* Recepción */}
          <Evento cuando={fecha(s.eventos[0].cuando)} titulo={s.eventos[0].titulo}>
            {s.eventos[0].enviadas} enviadas · <b>{s.eventos[0].recibidas} recibidas</b> ·{" "}
            <b className="text-rose-800">{s.eventos[0].rechazadas} rechazadas</b> (dato simulado: la serie real arranca el 14 sep)
          </Evento>
          <Evento cuando={fecha(s.eventos[1].cuando)} titulo={s.eventos[1].titulo}>
            El historial de cambios llega por lotes: la hora es <b>cuándo se observó</b>, no cuándo ocurrió.
            Retraso de minutos a horas.
          </Evento>
          <Evento cuando={fecha(s.eventos[2].cuando)} titulo={s.eventos[2].titulo}>
            A <b>{s.eventos[2].horasDesdeActivacion} h</b> de la activación. El histórico arranca el 27 dic 2025:
            para SKUs anteriores esa fecha no es una primera venta de verdad, y se rotula.
          </Evento>
          <div className="rounded-[10px] px-3 py-2.5" style={RAYADO}>
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-bold text-slate-600">{s.eventos[3].titulo}</span>
              <ChipSinRegistro />
            </div>
            <p className="mt-0.5 text-xs text-slate-500">
              Las visitas existen ({num(s.visitas30d)} en 30 días) pero no se guardan con fecha: la cifra de hoy no
              es la de aquel día. Hay que empezar a registrarla cuando ocurre cada venta.
            </p>
          </div>
          <Evento cuando={fecha(s.eventos[4].cuando)} titulo={s.eventos[4].titulo}>
            {s.coberturaDias} días de cobertura y 8 días de mediana entre orden y salida: si la solicitud no entra
            este martes, el SKU llega a cero antes de que aterrice el envío.
          </Evento>
        </div>
      </Tarjeta>

      <div className="flex flex-col gap-3">
        <Tarjeta>
          <Ceja>Venta desde la activación</Ceja>
          <div className="mt-3 flex flex-col gap-3">
            {s.ventaDesdeActivacion.map((v) => (
              <div key={v.dias}>
                <div className="flex items-baseline justify-between text-xs text-slate-500">
                  <span className="font-semibold text-slate-700">a {v.dias} días</span>
                  <span><b className="font-mono text-emerald-700">{v.pct}%</b> del envío</span>
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full rounded-full bg-emerald-600" style={{ width: `${v.pct}%` }} />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11.5px] leading-relaxed text-slate-500">
            Se calcula sobre las <b>recibidas</b>, no sobre las enviadas: lo que no entró al almacén nunca pudo venderse.
          </p>
        </Tarjeta>

        <Tarjeta>
          <Ceja>Precio y sus cambios</Ceja>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-3xl font-extrabold tabular-nums text-slate-900">${num(s.precio.vigente)}</span>
            <span className="text-xs text-slate-400">precio vigente</span>
          </div>
          <div className="mt-3 flex flex-col divide-y divide-slate-100 rounded-xl border border-slate-200">
            {s.precio.cambios.map((c) => (
              <div key={c.cuando.ts} className="flex items-center justify-between px-3 py-2 text-xs">
                <span className="font-mono text-slate-500">{fecha(c.cuando)}</span>
                <span className="font-mono text-slate-600">${num(c.antes)} → <b className="text-slate-900">${num(c.despues)}</b></span>
              </div>
            ))}
            <div className="flex items-center justify-between px-3 py-2 text-xs" style={{ background: "#f8fafc" }}>
              <span className="font-mono text-slate-400">{s.precio.historialDesde} 00:00</span>
              <span className="text-slate-400">arranca el historial</span>
            </div>
          </div>
          <p className="mt-2.5 text-[11.5px] leading-relaxed text-slate-500">
            Antes del {s.precio.historialDesde} no hay historia de precio: no se dibuja una línea plana donde no había medición.
          </p>
        </Tarjeta>

        <Tarjeta>
          <Ceja>Calificación del listing y de operaciones</Ceja>
          <div className="mt-2.5 flex h-16 items-center justify-center rounded-xl" style={RAYADO}>
            <ChipSinRegistro texto="sin definir" />
          </div>
          <p className="mt-2.5 text-[11.5px] leading-relaxed text-slate-500">
            Pendiente de acordar cuáles son exactamente. Mercado Libre ya entrega{" "}
            <code className="font-mono">seller_reputation</code> y la calidad del ítem, y hoy las dos se descartan.
          </p>
        </Tarjeta>
      </div>
    </div>
  );
}

function Evento({ cuando, titulo, children }: { cuando: string; titulo: string; children: React.ReactNode }) {
  return (
    <div className="relative">
      <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-emerald-600 ring-1 ring-emerald-600" />
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-xs font-bold text-emerald-700">{cuando}</span>
        <span className="text-[13px] font-bold text-slate-900">{titulo}</span>
      </div>
      <p className="mt-0.5 text-xs text-slate-500">{children}</p>
    </div>
  );
}
