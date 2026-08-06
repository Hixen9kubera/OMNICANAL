"use client";

/**
 * MargenesRealesModal — "Productos más vendidos": popup sobre la pestaña
 * Análisis (Eduardo, 6-ago: "que sea un filtro en la propia pestaña al lado
 * de período, en vez de ser una pestaña aparte").
 *
 * Es la fase 0 de Márgenes con los TRES cobros de Meli reales:
 *   · Precio prom  = ingreso ÷ unidades de los pedidos (realizado)
 *   · Comisión /u  = sale_fee que ML cobró en esos pedidos
 *   · Envío /u     = cobro real por embarque (API de shipments de ML),
 *                    prorrateado por unidad en carritos mixtos
 * El estimado viejo de envío aparece TACHADO cuando difiere del real — la
 * evidencia de por qué esto existe (Malla Sombra: $349 estimado vs $88 real).
 *
 * La columna Visitas·CR% va de adorno a propósito (pedido explícito): la
 * fuente de visitas aún no está conectada, pero el lugar ya está apartado
 * para que al conectarla no haya que mover la tabla.
 *
 * El backend consulta los embarques por tandas (presupuesto) y cachea por
 * orden; mientras falten, `pendientes > 0` y el modal refresca solo.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BadgePercent, RefreshCw, Tag, X } from "lucide-react";
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
const FILTRO_CUENTAS = [
  { id: "TODAS", label: "Ambas" },
  { id: "BEKURA", label: "Kubera" },
  { id: "SANCORFASHION", label: "San Corpe" },
] as const;
type FiltroCuenta = (typeof FILTRO_CUENTAS)[number]["id"];

/* Tope de refrescos automáticos mientras el caché de envíos se llena: con
   presupuesto 250/carga, 8 rondas cubren ~2,000 órdenes — más que un mes. */
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
    <section className="rounded-2xl border border-slate-200 bg-white">
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
              <th className="px-3 py-2 text-right"
                  title="Visitas de la publicación y conversión — pendiente de conectar la fuente">
                Visitas · CR%
              </th>
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
                <td className="max-w-[240px] px-3 py-2">
                  <div className="font-semibold text-slate-800">{f.sku}</div>
                  <div className="truncate text-[11px] text-slate-400">{f.titulo ?? ""}</div>
                </td>
                {/* De adorno a propósito: el lugar queda apartado para cuando
                    se conecte la fuente de visitas. */}
                <td className="px-3 py-2 text-right tabular-nums text-slate-300"
                    title="Pendiente de conectar la fuente de visitas">— · —</td>
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

export default function MargenesRealesModal({ cerrar }: { cerrar: () => void }) {
  const [visible, setVisible] = useState(false);
  const [dias, setDias] = useState(30);
  const [cuenta, setCuenta] = useState<FiltroCuenta>("TODAS");
  const [data, setData] = useState<Respuesta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rondas = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 20);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar(); };
    window.addEventListener("keydown", esc);
    return () => { clearTimeout(t); window.removeEventListener("keydown", esc); };
  }, [cerrar]);

  const cargar = useCallback((d: number) => {
    fetchSesion(`${API_BASE}/api/fulfillment/margenes-reales?dias=${d}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((res: Respuesta) => {
        setData(res); setError(null);
        // El backend consulta hasta `presupuesto` embarques por carga: si aún
        // quedan pendientes, se vuelve a pedir y cada ronda avanza otro tanto.
        if (res.pendientes > 0 && rondas.current < MAX_RONDAS) {
          rondas.current += 1;
          setTimeout(() => cargar(d), 2500);
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    rondas.current = 0;
    setData(null);
    cargar(dias);
  }, [dias, cargar]);

  const cuentasVisibles = (data?.cuentas ?? []).filter(
    (c) => cuenta === "TODAS" || c.cuenta === cuenta);

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
      onClick={cerrar}
    >
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" />
      <div
        onClick={(e) => e.stopPropagation()}
        className={`relative max-h-[90vh] w-full max-w-6xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl transition-all duration-200 ${visible ? "translate-y-0 scale-100 opacity-100" : "translate-y-3 scale-95 opacity-0"}`}
      >
        {/* Encabezado: título + filtros de cuenta y período */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <BadgePercent size={18} className="text-indigo-600" />
            <span className="text-sm font-bold text-slate-800">Productos más vendidos</span>
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
              cobros reales de Meli
            </span>
            {data && data.pendientes > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-600">
                <RefreshCw size={11} className="animate-spin" />
                consultando envíos — faltan {fNum(data.pendientes)} piezas
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
              {FILTRO_CUENTAS.map((c) => (
                <button key={c.id} onClick={() => setCuenta(c.id)}
                        className={`px-2.5 py-1.5 transition-colors ${cuenta === c.id ? "bg-indigo-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                  {c.label}
                </button>
              ))}
            </div>
            <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
              {[7, 30, 60, 90].map((d) => (
                <button key={d} onClick={() => setDias(d)}
                        className={`px-2.5 py-1.5 transition-colors ${dias === d ? "bg-slate-700 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                  {d}d
                </button>
              ))}
            </div>
            <button onClick={cerrar} aria-label="Cerrar"
                    className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700">
              <X size={18} />
            </button>
          </div>
        </div>

        <p className="mb-3 text-[12px] text-slate-500">
          Margen sobre <b>Costo Final</b> = costo base + comisión real +{" "}
          <b>envío real por embarque</b> (API de ML). No incluye cargos de
          almacenamiento FULL.
        </p>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            No se pudo cargar: {error}
          </div>
        )}
        {!data && !error && (
          <div className="flex h-40 items-center justify-center text-sm text-slate-400">
            Cargando márgenes…
          </div>
        )}
        <div className="space-y-4">
          {cuentasVisibles.map((c) => (
            <TablaCuenta key={c.cuenta} cuenta={c.cuenta} filas={c.filas} />
          ))}
        </div>
        {data && (
          <p className="mt-3 text-[11px] text-slate-400">
            * cobertura parcial: el envío promedio sale de las piezas ya
            consultadas. En carritos con varios productos el cobro del embarque
            se prorratea por unidad.
          </p>
        )}
      </div>
    </div>
  );
}
