"use client";

/**
 * FULLFILMENT · Planeación semanal — lista de Andy → validación de Bodega →
 * reajuste (IA o a mano) → generación de la orden.
 *
 * Guardar la lista ANTES del recorte de Bodega es lo único que hace posible la
 * tasa de validado: la cantidad de la orden de Odoo ya trae el recorte, y
 * tomar «solicitado» de ahí la pondría en 100%.
 *
 * Las dos acciones que mueven mercancía (orden de salida, carga al
 * marketplace) se PINTAN y van DESHABILITADAS con su motivo en el `title`:
 * encienden un flujo vivo y esperan el dale de Brandon. Al KAM no se le
 * esconden ni le dan error al tocarlas.
 *
 * El «Reajustar con IA» de esta vista es SIMULADO: no llama a ningún modelo.
 */

import { useMemo, useState } from "react";
import { FilePlus2, Shield, Ship, Sparkles } from "lucide-react";
import { PLAN, SEMANA_PLAN } from "./datosDiseno";
import { BotonBloqueado, Ceja, FONDO_RAYADO, RAYADO, Tarjeta, fecha, num } from "./ui";
import type { EstadoRenglonPlan, Rol } from "./tipos";

const ESTADO: Record<EstadoRenglonPlan, { texto: string; nota: string; clase: string }> = {
  aprobado: { texto: "Aprobado completo", nota: "sale completo", clase: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  recorte: { texto: "Bodega recortó", nota: "free_qty por almacén no alcanza", clase: "border-rose-200 bg-rose-50 text-rose-800" },
  pendiente: { texto: "Bodega no ha revisado", nota: "el renglón sigue en espera", clase: "border-dashed border-slate-300 text-slate-400" },
};

export default function Planeacion({ rol }: { rol: Rol }) {
  const esAdmin = rol === "admin";
  const inicial = () => PLAN.map((r) => (r.ia === null ? "" : String(r.ia)));
  const [finales, setFinales] = useState<string[]>(inicial);
  const [iaCorrida, setIaCorrida] = useState(false);

  const tot = useMemo(() => {
    const suma = (f: (r: (typeof PLAN)[number]) => number | null) => PLAN.reduce((a, r) => a + (f(r) ?? 0), 0);
    const pidio = suma((r) => r.pidio);
    // La tasa de validado sólo mira lo que Bodega ya revisó: un renglón en
    // espera no es un recorte a cero.
    const pidioRevisado = suma((r) => (r.bodega === null ? null : r.pidio));
    const bodega = suma((r) => r.bodega);
    const ia = suma((r) => r.ia);
    const final = finales.reduce((a, v) => a + (parseInt(v, 10) || 0), 0);
    return { pidio, pidioRevisado, bodega, ia, final };
  }, [finales]);

  const razonOrden = esAdmin
    ? "Flujo vivo: generar la orden de salida en Odoo mueve mercancía. Requiere el dale explícito de Brandon antes de encenderse."
    : "Sólo admin. Generar la orden de salida mueve mercancía; tu rol KAM ve la pestaña completa pero no dispara el flujo.";
  const razonCarga = esAdmin
    ? "Flujo vivo: cargar el envío al marketplace mueve mercancía y consume el número de envío. Requiere el dale explícito de Brandon."
    : "Sólo admin. Cargar el envío al marketplace mueve mercancía; tu rol KAM no dispara el flujo.";

  const revisados = PLAN.filter((r) => r.estado !== "pendiente").length;

  return (
    <Tarjeta className="mt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <Ceja>Semana ISO {SEMANA_PLAN.iso} · {SEMANA_PLAN.rango} · hora de CDMX</Ceja>
          <h2 className="mt-1 text-[20px] font-extrabold tracking-tight text-slate-900">
            Planeación semanal — aquí nace la lista
          </h2>
          <p className="mt-1 text-[13px] leading-relaxed text-slate-500">
            Los tres pasos que hoy viven en un archivo y en WhatsApp: la lista de Andy, la validación de
            Bodega y el reajuste. Guardar la lista <b>antes</b> del recorte es lo único que hace posible la
            tasa de validado.
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-[11.5px] font-bold text-indigo-800">
          <Shield className="h-3.5 w-3.5" />
          {esAdmin ? "Admin · ves las acciones que mueven mercancía" : "KAM · ves todo, las acciones van deshabilitadas con su motivo"}
        </span>
      </div>

      {/* ── Los tres pasos ─────────────────────────────────────────────── */}
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-[.06em] text-emerald-700">Paso 1 · martes</span>
            <span className="font-mono text-[11px] text-emerald-700">{fecha(SEMANA_PLAN.solicitud.cuando)}</span>
          </div>
          <div className="mt-1 text-[15px] font-extrabold text-emerald-800">Solicitud de Andy</div>
          <div className="mt-0.5 text-xs text-emerald-700">
            {PLAN.length} SKUs · {num(tot.pidio)} piezas solicitadas · destino {SEMANA_PLAN.solicitud.destino}
          </div>
        </div>
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-3">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-[.06em] text-amber-700">Paso 2 · martes</span>
            <span className="font-mono text-[11px] text-amber-700">en espera</span>
          </div>
          <div className="mt-1 text-[15px] font-extrabold text-amber-800">Validación de Bodega</div>
          <div className="mt-0.5 text-xs text-amber-800">
            {revisados} de {PLAN.length} renglones revisados · prevalidados con{" "}
            <code className="font-mono text-[11px]">free_qty</code> por almacén
          </div>
        </div>
        <div className="rounded-xl px-3.5 py-3" style={RAYADO}>
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">Paso 3 · miércoles</span>
            <span className="font-mono text-[10px] font-bold uppercase text-slate-400">sin correr</span>
          </div>
          <div className="mt-1 text-[15px] font-extrabold text-slate-600">Orden de salida en Odoo</div>
          <div className="mt-0.5 text-xs text-slate-500">Se genera desde aquí. Mueve mercancía: acción de admin.</div>
        </div>
      </div>

      {/* ── La lista ───────────────────────────────────────────────────── */}
      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
        <table className="w-full min-w-[1180px] border-collapse text-[13px]">
          <thead>
            <tr className="bg-slate-50 text-left text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-500">
              <th className="px-3.5 py-2.5">SKU · producto</th>
              <th className="px-3.5 py-2.5">Destino</th>
              <th className="px-3.5 py-2.5 text-right" title="Free to use por almacén, lo vendible. Odoo es el maestro.">Libre Odoo</th>
              <th className="px-3.5 py-2.5 text-right">Pidió Andy</th>
              <th className="px-3.5 py-2.5 text-right">Bodega puede</th>
              <th className="px-3.5 py-2.5 text-right">Propuesta IA</th>
              <th className="px-3.5 py-2.5 text-right">Final</th>
              <th className="px-3.5 py-2.5">Estado del renglón</th>
            </tr>
          </thead>
          <tbody>
            {PLAN.map((r, i) => {
              const est = ESTADO[r.estado];
              const pendiente = r.estado === "pendiente";
              return (
                <tr key={r.sku} className={`border-t border-slate-100 ${pendiente ? "bg-[#fcfdff]" : ""}`}>
                  <td className="px-3.5 py-[11px]">
                    <div className="font-mono text-[12.5px] font-bold text-slate-900">{r.sku}</div>
                    <div className="text-[11.5px] text-slate-400">{r.nombre}</div>
                  </td>
                  <td className="px-3.5 py-[11px]">
                    <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11.5px] text-slate-700">{r.destino}</code>
                  </td>
                  <td className="px-3.5 py-[11px] text-right font-mono tabular-nums text-slate-600">{num(r.libre)}</td>
                  <td className="px-3.5 py-[11px] text-right font-mono font-bold tabular-nums text-slate-900">{num(r.pidio)}</td>
                  <td className={`px-3.5 py-[11px] text-right font-mono font-bold tabular-nums ${
                    r.bodega === null ? "text-slate-400" : r.estado === "recorte" ? "text-rose-800" : "text-amber-700"}`}>
                    {r.bodega === null ? "s/reg" : num(r.bodega)}
                  </td>
                  <td className="px-3.5 py-[11px] text-right font-mono tabular-nums text-indigo-700">{num(r.ia)}</td>
                  <td className="px-3.5 py-[11px] text-right">
                    <input
                      type="text" inputMode="numeric" value={finales[i]} disabled={pendiente}
                      placeholder="—"
                      onChange={(ev) => {
                        const v = ev.target.value.replace(/[^\d]/g, "");
                        setFinales((f) => f.map((x, j) => (j === i ? v : x)));
                      }}
                      className="w-20 rounded-md border border-slate-200 px-2 py-1 text-right font-mono text-[13px] font-bold tabular-nums text-slate-900 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50 disabled:text-slate-300"
                    />
                  </td>
                  <td className="px-3.5 py-[11px]">
                    <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-bold ${est.clase}`}
                          style={pendiente ? { background: FONDO_RAYADO } : undefined}>
                      {est.texto}
                    </span>
                    <div className="mt-0.5 text-[11px] text-slate-400">{est.nota}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-slate-200 bg-slate-50 text-[12.5px] font-bold">
              <td colSpan={3} className="px-3.5 py-2.5 text-slate-600">Tasas de la semana · se guardan las tres cifras</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums">{num(tot.pidio)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums">{num(tot.bodega)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums">{num(tot.ia)}</td>
              <td className="px-3.5 py-2.5 text-right font-mono tabular-nums">{num(tot.final)}</td>
              <td className="px-3.5 py-2.5 text-xs font-normal text-slate-600">
                Tasa de validado <b>{Math.round((tot.bodega / tot.pidioRevisado) * 100)}%</b>{" "}
                <span className="text-slate-400">({revisados} renglones revisados)</span> · lo final contra lo pedido{" "}
                <b>{Math.round((tot.final / tot.pidio) * 100)}%</b>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      {/* ── Acciones ───────────────────────────────────────────────────── */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setIaCorrida(true)}
                title="Simulado en esta vista de diseño: no llama a ningún modelo."
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
          <Sparkles className="h-4 w-4" />
          {iaCorrida ? "Propuesta recalculada · simulada" : "Reajustar con IA"}
        </button>
        <button type="button" onClick={() => setFinales(inicial())}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
          Aceptar la propuesta en todos los renglones
        </button>
        <button type="button"
                onClick={() => setFinales(PLAN.map((r) => (r.estado === "pendiente" ? "" : String(r.pidio))))}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
          Volver a lo que pidió Andy
        </button>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="text-right">
            <div className="text-[11px] font-bold uppercase tracking-[.06em] text-slate-400">Ver no es mover</div>
            <div className="text-xs text-slate-500">
              {esAdmin ? "flujo vivo, pendiente del dale de Brandon" : "tu rol no dispara estas acciones"}
            </div>
          </div>
          <BotonBloqueado icono={FilePlus2} texto="Generar orden de salida" razon={razonOrden} primario />
          <BotonBloqueado icono={Ship} texto="Cargar envío al marketplace" razon={razonCarga} />
        </div>
      </div>

      <div className={`mt-3 rounded-xl border px-3.5 py-3 text-[12.5px] leading-relaxed ${
        esAdmin ? "border-amber-300 bg-amber-50 text-amber-800" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
        {esAdmin
          ? "Los dos botones están pintados porque son parte del diseño y deshabilitados porque encienden un flujo vivo: generar la orden en Odoo y cargar el envío al marketplace mueven mercancía de verdad. Se encienden con el dale explícito de Brandon, no antes. Pasa el cursor para ver el motivo completo."
          : "Como KAM ves la pestaña completa — nada se esconde. Las dos acciones que mueven mercancía aparecen deshabilitadas y con su motivo, en vez de desaparecer o de darte un error al tocarlas."}
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-slate-200 px-3.5 py-3">
          <div className="text-[13px] font-bold text-slate-800">Se guardan las tres listas, no sólo la última</div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
            La original de Andy, la propuesta (de la IA o del ajuste a mano) y la final que se manda. Sin las
            tres no existe la <b>precisión de la solicitud</b>: cuánto recortó Bodega, cuánto ajustó la IA y si
            el ajuste acertó contra lo que se vendió después.
          </p>
        </div>
        <div className="rounded-xl border border-slate-200 px-3.5 py-3">
          <div className="text-[13px] font-bold text-slate-800">El número del envío se captura aquí</div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500">
            Al cargar el envío, el marketplace devuelve su número y el panel lo guarda en la referencia de la
            orden. Es lo que hoy se teclea a mano y falta en una de cada cuatro órdenes.
          </p>
        </div>
      </div>
    </Tarjeta>
  );
}
