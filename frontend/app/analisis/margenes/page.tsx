"use client";

/**
 * /analisis/margenes — Márgenes REALES: 10 SKUs más vendidos por cuenta.
 *
 * Requisito de Eduardo (6-ago): "Definir la estructura de Promedio de precio
 * de venta y sus costos. Margen va sobre el Costo Final (con todos los cobros
 * de Meli)". Aquí los TRES cobros son reales, no estimados:
 *   · Precio prom  = ingreso ÷ unidades de los pedidos (realizado)
 *   · Comisión /u  = sale_fee que ML cobró en esos pedidos
 *   · Envío /u     = cobro por embarque de la API de shipments de ML,
 *                    prorrateado por unidad en carritos mixtos
 * El envío se cachea por orden en el backend (fase 0). Mientras el caché se
 * llena, `pendientes` > 0 y la página refresca sola hasta completar.
 *
 * El estimado viejo de envío se muestra tachado en el tooltip cuando difiere:
 * es la evidencia de por qué esta página existe (Malla Sombra: $349 estimado
 * contra $88 real).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BadgePercent, CircleHelp, RefreshCw, Tag } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { avisoCostoImplausible, costoImplausible } from "@/lib/margen";

interface Fila {
  sku: string; titulo: string | null; uds: number; ingreso: number;
  precio_prom: number | null; costo_base: number | null;
  comision_unit: number | null; envio_unit: number | null;
  envio_estimado: number | null; cobertura_envio_pct: number;
  uds_sin_envio: number; precio_pub: number | null; precio_lista: number | null;
  costo_final: number | null; ganancia_unit: number | null;
  margen_pct: number | null; ganancia_total: number | null;
}
interface Respuesta {
  dias: number; pendientes: number; consultadas: number; nota: string;
  cuentas: { cuenta: string; filas: Fila[] }[];
}

const fMoney = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : `$${Number(v).toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
const fNum = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : Number(v).toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec });

const NOMBRE_CUENTA: Record<string, string> = {
  BEKURA: "Kubera (BEKURA)", SANCORFASHION: "San Corpe (SANCORFASHION)",
};

/* Refrescos automáticos mientras el caché de envíos se llena. Tope de rondas
   para no martillar a ML si algo se atora — con presupuesto 250/carga, 8
   rondas cubren ~2,000 órdenes, más que un mes de las dos cuentas. */
const MAX_RONDAS = 8;

function Margen({ f }: { f: Fila }) {
  if (f.precio_prom != null && costoImplausible(f.precio_prom, f.costo_base)) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-600"
            title={avisoCostoImplausible(f.precio_prom, f.costo_base!)}>
        ⚠ costo?
      </span>
    );
  }
  if (f.margen_pct == null) {
    return (
      <span className="text-slate-300"
            title={f.envio_unit == null
              ? "Aún sin envío real consultado para este SKU — se completa solo"
              : "Falta costo o comisión para calcular el margen"}>—</span>
    );
  }
  return (
    <div>
      <div className={`font-bold tabular-nums ${f.margen_pct < 20 ? "text-red-500" : "text-emerald-600"}`}>
        {fNum(f.margen_pct, 1)}%
      </div>
      <div className="text-[10px] tabular-nums text-slate-400">{fMoney(f.ganancia_unit, 2)}/u</div>
    </div>
  );
}

function Precio({ f }: { f: Fila }) {
  const promo = f.precio_lista != null && f.precio_pub != null
    && f.precio_lista > f.precio_pub * 1.05;
  return (
    <div title={`Promedio realizado del período (${fNum(f.uds)} uds ÷ ${fMoney(f.ingreso)})`
                + (f.precio_pub != null ? `\nPublicación activa hoy: ${fMoney(f.precio_pub, 2)}` : "")
                + (promo ? `\nPrecio de LISTA: ${fMoney(f.precio_lista, 2)} — hay promoción montada` : "")}>
      <div className="font-semibold tabular-nums text-slate-800">{fMoney(f.precio_prom, 2)}</div>
      {promo && (
        <div className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-violet-600">
          <BadgePercent size={10} />
          promo · lista {fMoney(f.precio_lista)}
        </div>
      )}
    </div>
  );
}

function Envio({ f }: { f: Fila }) {
  if (f.envio_unit == null) {
    return <span className="text-[11px] text-slate-400" title="Consultando embarques a Mercado Libre…">…</span>;
  }
  const dif = f.envio_estimado != null && Math.abs(f.envio_estimado - f.envio_unit) > 5;
  return (
    <div title={`Cobro real de ML por embarque, promedio por unidad`
                + (f.cobertura_envio_pct < 100
                   ? `\nCobertura: ${f.cobertura_envio_pct}% de las piezas (el resto sigue consultándose)` : "")
                + (dif ? `\nEl estimado viejo decía ${fMoney(f.envio_estimado, 2)}` : "")}>
      <span className="tabular-nums text-slate-700">{fMoney(f.envio_unit, 2)}</span>
      {f.cobertura_envio_pct < 100 && <span className="text-amber-500">*</span>}
      {dif && (
        <div className="text-[10px] tabular-nums text-slate-400 line-through">{fMoney(f.envio_estimado, 2)}</div>
      )}
    </div>
  );
}

function TablaCuenta({ cuenta, filas }: { cuenta: string; filas: Fila[] }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-card">
      <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
        <Tag size={15} className="text-indigo-500" />
        <h2 className="text-sm font-bold text-slate-800">{NOMBRE_CUENTA[cuenta] ?? cuenta}</h2>
        <span className="text-xs text-slate-400">top {filas.length} por unidades vendidas</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-400">
              <th className="px-3 py-2 text-left">#</th>
              <th className="px-3 py-2 text-left">Producto</th>
              <th className="px-3 py-2 text-right">Uds</th>
              <th className="px-3 py-2 text-right">Venta</th>
              <th className="px-3 py-2 text-right">Precio prom</th>
              <th className="px-3 py-2 text-right">Costo base</th>
              <th className="px-3 py-2 text-right">Comisión /u</th>
              <th className="px-3 py-2 text-right">Envío real /u</th>
              <th className="px-3 py-2 text-right">Costo final</th>
              <th className="px-3 py-2 text-right">Margen</th>
              <th className="px-3 py-2 text-right">Ganancia período</th>
            </tr>
          </thead>
          <tbody>
            {filas.map((f, i) => (
              <tr key={f.sku} className="border-b border-slate-50 hover:bg-slate-50/60">
                <td className="px-3 py-2 text-slate-400">{i + 1}</td>
                <td className="max-w-[260px] px-3 py-2">
                  <div className="font-semibold text-slate-800">{f.sku}</div>
                  <div className="truncate text-[11px] text-slate-400">{f.titulo ?? ""}</div>
                </td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-700">{fNum(f.uds)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fMoney(f.ingreso)}</td>
                <td className="px-3 py-2 text-right"><Precio f={f} /></td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700"
                    title="costos_validados.costo_total (producto + flete marítimo)">
                  {fMoney(f.costo_base, 2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700"
                    title="sale_fee promedio que ML cobró de verdad en los pedidos del período">
                  {fMoney(f.comision_unit, 2)}
                </td>
                <td className="px-3 py-2 text-right"><Envio f={f} /></td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-800">
                  {fMoney(f.costo_final, 2)}
                </td>
                <td className="px-3 py-2 text-right"><Margen f={f} /></td>
                {/* Si el costo no es creíble, la ganancia tampoco: pintar
                    −$179k junto a un "⚠ costo?" sería mentir con decimales. */}
                {f.precio_prom != null && costoImplausible(f.precio_prom, f.costo_base) ? (
                  <td className="px-3 py-2 text-right text-slate-300"
                      title={avisoCostoImplausible(f.precio_prom, f.costo_base!)}>—</td>
                ) : (
                  <td className={`px-3 py-2 text-right font-bold tabular-nums ${
                      f.ganancia_total == null ? "text-slate-300"
                      : f.ganancia_total < 0 ? "text-red-500" : "text-emerald-600"}`}>
                    {fMoney(f.ganancia_total)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function MargenesReales() {
  const [data, setData] = useState<Respuesta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rondas = useRef(0);

  const cargar = useCallback(() => {
    fetchSesion(`${API_BASE}/api/fulfillment/margenes-reales?dias=30`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d: Respuesta) => {
        setData(d); setError(null);
        // El backend consulta hasta `presupuesto` embarques por carga: si aún
        // quedan pendientes, se vuelve a pedir y cada ronda avanza otro tanto.
        if (d.pendientes > 0 && rondas.current < MAX_RONDAS) {
          rondas.current += 1;
          setTimeout(cargar, 2500);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { cargar(); }, [cargar]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-slate-500">
        <CircleHelp size={14} className="text-slate-400" />
        <span>
          Últimos <b>30 días</b> · margen sobre <b>Costo Final</b> = costo base
          + comisión real + <b>envío real por embarque</b> (API de ML). No
          incluye cargos de almacenamiento FULL.
        </span>
        {data && data.pendientes > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-600">
            <RefreshCw size={11} className="animate-spin" />
            consultando envíos a ML — faltan {fNum(data.pendientes)} piezas
          </span>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          No se pudo cargar: {error}
        </div>
      )}
      {!data && !error && (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
          Cargando márgenes…
        </div>
      )}
      {data?.cuentas.map((c) => (
        <TablaCuenta key={c.cuenta} cuenta={c.cuenta} filas={c.filas} />
      ))}
      {data && (
        <p className="text-[11px] text-slate-400">
          * cobertura parcial: el envío promedio sale de las piezas ya
          consultadas. En carritos con varios productos el cobro del embarque
          se prorratea por unidad. Los datos de envío se guardan por orden
          (caché) — la fase 1 los volverá parte del modelo de pedidos.
        </p>
      )}
    </div>
  );
}
