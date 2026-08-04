"use client";

/**
 * /analisis/estrellas — Productos estrella (Pareto ALL-TIME).
 *
 * Porta la sección ESTRELLA del tablero de José (docs/fulfillment/
 * prompts_originales_jose.txt, prompt 2) contra la BD kubera. Su RPC original
 * `get_estrella_data` vive en dailytrackMeli, que responde 53100 "No space
 * left on device" — pero su insumo ya está absorbido en
 * channel.sales_daily_completa, así que no dependemos de ese proyecto.
 *
 * Diferencias declaradas vs el original:
 *   · TRES cuentas (BEKURA, SANCORFASHION y AMAZON), no dos.
 *   · PROM/MES divide entre MESES ACTIVOS, no entre los del calendario.
 *   · La curva de Pareto dibuja el TOP 50; los cortes 50/80/90% se calculan
 *     sobre el universo completo y se anuncian abajo (dibujar 1,042 barras de
 *     2 px no se lee).
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, Search, Star } from "lucide-react";
import { API_BASE } from "@/lib/api";
import Ayuda from "@/components/Ayuda";
import MargenesTop10 from "@/components/MargenesTop10";

interface Item {
  sku: string;
  titulo: string | null;
  cuentas: string[];
  uds: number;
  venta: number;
  meses: number;
  prom_mes_uds: number;
  prom_mes_venta: number;
  share_uds: number;
  share_venta: number;
  acum_uds: number;
  acum_venta: number;
  primera: string;
  ultima: string;
}

interface Resp {
  ambiente: string;
  cuenta: string | null;
  periodo: { desde: string | null; hasta: string | null };
  totales: {
    uds: number; venta: number; skus: number;
    skus_80_uds: number; skus_80_venta: number;
  };
  /* Ventas que llegaron sin SKU: no rankean (no son un producto), pero se
     declaran para que los totales cuadren contra la vista de ventas. */
  sin_sku: { uds: number; venta: number };
  items: Item[];
}

type Metrica = "uds" | "venta";

/* Los numeric de Postgres llegan como string: se coercionan SIEMPRE. */
const n = (v: number | string | null | undefined) => (v == null ? 0 : Number(v));
const fMoney = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : `$${n(v).toLocaleString("es-MX", { maximumFractionDigits: dec })}`;
const fNum = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : n(v).toLocaleString("es-MX", { maximumFractionDigits: dec });

const CUENTAS = [
  { id: "", label: "Consolidado" },
  { id: "BEKURA", label: "Bekura" },
  { id: "SANCORFASHION", label: "Sancor" },
  { id: "AMAZON", label: "Amazon" },
];
const CUENTA_CHIP: Record<string, string> = {
  BEKURA: "bg-sky-100 text-sky-700",
  SANCORFASHION: "bg-violet-100 text-violet-700",
  AMAZON: "bg-amber-100 text-amber-700",
};
const CUENTA_INI: Record<string, string> = {
  BEKURA: "BK", SANCORFASHION: "SC", AMAZON: "AMZ",
};

function Kpi({ label, value, pie, tone }: {
  label: string; value: string; pie?: string; tone?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone ?? "text-slate-900"}`}>{value}</div>
      {pie && <div className="mt-0.5 text-[11px] text-slate-400">{pie}</div>}
    </div>
  );
}

/* ── Curva de Pareto ──────────────────────────────────────────────────────
   Barras = % share de cada SKU (eje izquierdo, escala propia para que se vean)
   Línea  = % acumulado (eje derecho 0-100). Guías en 50 / 80 / 90%.          */
function Pareto({ top, metrica, cortes }: {
  top: Item[];
  metrica: Metrica;
  cortes: { pct: number; skus: number }[];
}) {
  const W = 1200, H = 260, L = 40, R = 42, T = 14, B = 30;
  const share = (it: Item) => n(metrica === "uds" ? it.share_uds : it.share_venta);
  const acum = (it: Item) => n(metrica === "uds" ? it.acum_uds : it.acum_venta);
  const maxShare = Math.max(0.01, ...top.map(share));
  const bw = top.length ? (W - L - R) / top.length : 1;
  const yAcum = (p: number) => T + (1 - p / 100) * (H - T - B);
  const yBar = (s: number) => T + (1 - s / maxShare) * (H - T - B);

  const puntos = top
    .map((it, i) => `${L + i * bw + bw / 2},${yAcum(acum(it))}`)
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-64 w-full">
      {/* Guías del acumulado */}
      {[25, 50, 75, 100].map((p) => (
        <g key={p}>
          <line x1={L} x2={W - R} y1={yAcum(p)} y2={yAcum(p)}
                className="stroke-slate-200" strokeWidth={1} />
          <text x={W - R + 5} y={yAcum(p) + 3} className="fill-slate-400 text-[10px]">{p}%</text>
        </g>
      ))}
      {/* Cortes de Pareto */}
      {cortes.map((c) => (
        <g key={c.pct}>
          <line x1={L} x2={W - R} y1={yAcum(c.pct)} y2={yAcum(c.pct)}
                className={c.pct === 80 ? "stroke-rose-400" : "stroke-slate-300"}
                strokeWidth={c.pct === 80 ? 1.5 : 1} strokeDasharray="5 4" />
          <text x={L + 6} y={yAcum(c.pct) - 5}
                className={`text-[10px] font-semibold ${c.pct === 80 ? "fill-rose-500" : "fill-slate-400"}`}>
            {c.pct}% · {c.skus} SKUs
          </text>
        </g>
      ))}
      {/* Barras de share */}
      {top.map((it, i) => {
        const y = yBar(share(it));
        return (
          <rect key={it.sku} x={L + i * bw + 1} y={y}
                width={Math.max(1.5, bw - 2)} height={H - B - y} rx={1}
                className="fill-indigo-500/80 hover:fill-indigo-400">
            <title>{`#${i + 1} ${it.sku}\n${share(it).toFixed(2)}% del total · acumulado ${acum(it).toFixed(1)}%\n${metrica === "uds" ? `${fNum(it.uds)} uds` : fMoney(it.venta)}`}</title>
          </rect>
        );
      })}
      {/* Línea acumulada */}
      <polyline points={puntos} fill="none"
                className="stroke-rose-500" strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round" />
      <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="stroke-slate-300" />
      <text x={L} y={H - 10} className="fill-slate-400 text-[10px]">#1</text>
      <text x={W - R} y={H - 10} textAnchor="end" className="fill-slate-400 text-[10px]">
        #{top.length}
      </text>
      <text x={L - 6} y={T + 8} textAnchor="end" className="fill-slate-400 text-[10px]">
        {maxShare.toFixed(1)}%
      </text>
    </svg>
  );
}

const COLS: { id: string; label: string; num?: boolean; ayuda: string }[] = [
  { id: "rank", label: "#",
    ayuda: "Posición en el ranking por la métrica elegida arriba. NO cambia al reordenar la tabla: es el lugar del SKU en el Pareto, no el número de fila." },
  { id: "sku", label: "SKU",
    ayuda: "Clave del producto. Une el mismo artículo entre la tienda web, Mercado Libre y Amazon." },
  { id: "titulo", label: "Producto",
    ayuda: "Título del catálogo maestro. Si aparece vacío, el producto vendió pero todavía no está dado de alta en el catálogo." },
  { id: "cuentas", label: "Cuentas",
    ayuda: "Cuentas donde ese SKU registró venta: BK Bekura · SC San Corpe · AMZ Amazon. En la vista Consolidado un mismo SKU fusiona todas." },
  { id: "uds", label: "Uds", num: true,
    ayuda: "Unidades vendidas en todo el histórico disponible (desde el 27-dic-2025), no en un período." },
  { id: "venta", label: "Ingresos", num: true,
    ayuda: "Importe vendido en todo el histórico. Es venta bruta: no descuenta comisión ni costo." },
  { id: "share", label: "% share", num: true,
    ayuda: "Qué tanto del total representa este SKU por sí solo, según la métrica elegida." },
  { id: "acum", label: "% acum", num: true,
    ayuda: "Suma de este SKU y todos los que están por encima en el ranking. En rojo hasta el 80%: ésos son los que sostienen la venta." },
  { id: "prom", label: "Prom/mes", num: true,
    ayuda: "Promedio mensual dividido entre los meses en que ESE SKU vendió, no entre los del calendario: un producto que nació en junio no se castiga con los meses en que no existía." },
  { id: "meses", label: "Meses", num: true,
    ayuda: "En cuántos meses distintos tuvo al menos una venta. Pocos meses con muchas unidades = producto de temporada o recién despegado." },
];

export default function EstrellasPage() {
  const [cuenta, setCuenta] = useState("");
  const [metrica, setMetrica] = useState<Metrica>("uds");
  const [datos, setDatos] = useState<Resp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState<{ campo: string; asc: boolean } | null>(null);
  const [limite, setLimite] = useState(50);

  useEffect(() => {
    let vivo = true;
    setCargando(true);
    setError(null);
    fetch(`${API_BASE}/api/fulfillment/estrellas${cuenta ? `?cuenta=${cuenta}` : ""}`,
          { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail ?? r.status))))
      .then((d: Resp) => { if (vivo) { setDatos(d); setLimite(50); } })
      .catch((e) => { if (vivo) setError(String(e)); })
      .finally(() => { if (vivo) setCargando(false); });
    return () => { vivo = false; };
  }, [cuenta]);

  /* Ranking y cortes SIEMPRE por la métrica elegida, independientes del orden
     que el usuario pida en la tabla: el "#" es la posición del SKU en el
     Pareto, no la fila en pantalla. */
  const { arr, rank, cortes } = useMemo(() => {
    const items = datos?.items ?? [];
    const val = (it: Item) => n(metrica === "uds" ? it.uds : it.venta);
    const a = [...items].sort((x, y) => val(y) - val(x));
    const r = new Map(a.map((it, i) => [it.sku, i + 1]));
    const campoAcum = metrica === "uds" ? "acum_uds" : "acum_venta";
    const c = [50, 80, 90].map((pct) => {
      let skus = 0;
      for (const it of a) { skus++; if (n(it[campoAcum as keyof Item] as number) >= pct) break; }
      return { pct, skus: a.length ? skus : 0 };
    });
    return { arr: a, rank: r, cortes: c };
  }, [datos, metrica]);

  const visibles = useMemo(() => {
    let r = arr;
    if (q.trim()) {
      const t = q.trim().toLowerCase();
      r = r.filter((it) => (it.sku ?? "").toLowerCase().includes(t) ||
                           (it.titulo ?? "").toLowerCase().includes(t));
    }
    if (orden) {
      const v = (it: Item): number | string => {
        switch (orden.campo) {
          case "rank": return rank.get(it.sku) ?? 0;
          case "sku": return it.sku;
          case "titulo": return (it.titulo ?? "").toLowerCase();
          case "cuentas": return it.cuentas.length;
          case "uds": return n(it.uds);
          case "venta": return n(it.venta);
          case "share": return n(metrica === "uds" ? it.share_uds : it.share_venta);
          case "acum": return n(metrica === "uds" ? it.acum_uds : it.acum_venta);
          case "prom": return n(metrica === "uds" ? it.prom_mes_uds : it.prom_mes_venta);
          case "meses": return it.meses;
          default: return 0;
        }
      };
      r = [...r].sort((x, y) => {
        const a1 = v(x), b1 = v(y);
        const cmp = typeof a1 === "string" ? a1.localeCompare(b1 as string)
                                           : (a1 as number) - (b1 as number);
        return orden.asc ? cmp : -cmp;
      });
    }
    return r;
  }, [arr, q, orden, metrica, rank]);

  const ordenar = (campo: string) =>
    setOrden((o) => (o?.campo === campo ? { campo, asc: !o.asc } : { campo, asc: false }));

  const t = datos?.totales;
  const skus80 = metrica === "uds" ? t?.skus_80_uds : t?.skus_80_venta;
  const pct80 = t?.skus ? Math.round(((skus80 ?? 0) / t.skus) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* Márgenes con costo final de los más vendidos — mismo componente que
          en Omnicanal (decidir cuál de los dos montajes queda al publicar) */}
      <MargenesTop10 />

      {/* Controles */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {CUENTAS.map((c) => (
            <button
              key={c.id}
              onClick={() => setCuenta(c.id)}
              className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                cuenta === c.id
                  ? "bg-indigo-600 font-semibold text-white"
                  : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}
            >
              {c.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {([["uds", "Unidades"], ["venta", "$ Ingresos"]] as const).map(([id, label]) => (
            <button
              key={id}
              onClick={() => { setMetrica(id); setOrden(null); }}
              className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                metrica === id
                  ? "bg-slate-900 font-semibold text-white"
                  : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="relative ml-auto">
          <Search size={15} className="absolute left-2.5 top-2.5 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="SKU o producto…"
            className="w-56 rounded-xl border border-slate-200 bg-white py-2 pl-8 pr-3 text-sm shadow-sm outline-none placeholder:text-slate-400 focus:border-indigo-400"
          />
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          No se pudo leer el análisis: {error}
        </div>
      )}

      {cargando && !datos && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 shadow-sm">
          <Loader2 size={16} className="animate-spin" /> Leyendo el histórico completo…
        </div>
      )}

      {datos && (
        <>
          {/* KPIs */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="Unidades vendidas" value={fNum(t?.uds)}
                 pie={`${datos.periodo.desde} → ${datos.periodo.hasta}`} />
            <Kpi label="Ingresos" value={fMoney(t?.venta)} tone="text-emerald-600" />
            <Kpi label="SKUs con venta" value={fNum(t?.skus)}
                 pie={cuenta ? CUENTAS.find((c) => c.id === cuenta)?.label : "las 3 cuentas fusionadas por SKU"} />
            <Kpi label={`SKUs que hacen el 80%`} value={fNum(skus80)}
                 tone="text-rose-600"
                 pie={`${pct80}% del catálogo con venta · por ${metrica === "uds" ? "unidades" : "ingresos"}`} />
          </div>

          {/* Pareto */}
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                <Star size={15} className="text-amber-500" />
                Curva de Pareto · top 50 por {metrica === "uds" ? "unidades" : "ingresos"}
              </h2>
              <div className="flex items-center gap-3 text-[11px] text-slate-500">
                <span className="flex items-center gap-1">
                  <span className="h-2 w-3 rounded-sm bg-indigo-500/80" /> % del total
                </span>
                <span className="flex items-center gap-1">
                  <span className="h-0.5 w-4 bg-rose-500" /> % acumulado
                </span>
              </div>
            </div>
            <Pareto top={arr.slice(0, 50)} metrica={metrica} cortes={cortes} />
            <p className="mt-1 text-[11px] text-slate-500">
              {cortes.map((c) => `${c.skus} SKUs = ${c.pct}%`).join(" · ")} de{" "}
              {metrica === "uds" ? "las unidades" : "los ingresos"}, sobre los{" "}
              {fNum(t?.skus)} con venta.
            </p>
          </div>

          {/* Tabla */}
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full table-fixed text-sm">
              <colgroup>
                <col className="w-12" /><col className="w-36" /><col />
                <col className="w-24" /><col className="w-20" /><col className="w-28" />
                <col className="w-20" /><col className="w-20" /><col className="w-24" />
                <col className="w-16" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                  {COLS.map((c) => (
                    <th
                      key={c.id}
                      onClick={() => ordenar(c.id)}
                      className={`cursor-pointer select-none px-3 py-2.5 font-semibold hover:text-slate-800 ${
                        c.num ? "text-right" : "text-left"}`}
                    >
                      <span className={`inline-flex items-center ${c.num ? "justify-end" : ""}`}>
                        {c.label}
                        <Ayuda titulo={c.label === "#" ? "Posición" : c.label} texto={c.ayuda} />
                        {orden?.campo === c.id && (
                          <span className="ml-0.5 text-indigo-500">{orden.asc ? "↑" : "↓"}</span>
                        )}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibles.slice(0, limite).map((it) => {
                  const pos = rank.get(it.sku) ?? 0;
                  const share = n(metrica === "uds" ? it.share_uds : it.share_venta);
                  const acum = n(metrica === "uds" ? it.acum_uds : it.acum_venta);
                  const dentro80 = acum <= 80;
                  return (
                    <tr key={it.sku}
                        className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                      <td className="px-3 py-2 text-[11px] font-semibold text-slate-400">
                        {pos <= 3
                          ? <span className="text-amber-500">★{pos}</span>
                          : pos}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-slate-700">{it.sku}</td>
                      <td className="truncate px-3 py-2 text-[12px] text-slate-600"
                          title={it.titulo ?? ""}>
                        {it.titulo ?? <span className="text-slate-300">sin título</span>}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex gap-1">
                          {it.cuentas.map((c) => (
                            <span key={c}
                                  title={c}
                                  className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${CUENTA_CHIP[c] ?? "bg-slate-100 text-slate-500"}`}>
                              {CUENTA_INI[c] ?? c}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums ${
                        metrica === "uds" ? "font-semibold text-slate-900" : "text-slate-500"}`}>
                        {fNum(it.uds)}
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums ${
                        metrica === "venta" ? "font-semibold text-emerald-700" : "text-slate-500"}`}>
                        {fMoney(it.venta)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                        {share.toFixed(2)}%
                      </td>
                      <td className={`px-3 py-2 text-right tabular-nums ${
                        dentro80 ? "font-semibold text-rose-600" : "text-slate-400"}`}>
                        {acum.toFixed(1)}%
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                        {metrica === "uds"
                          ? fNum(it.prom_mes_uds, 1)
                          : fMoney(it.prom_mes_venta)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-400">
                        {it.meses}
                      </td>
                    </tr>
                  );
                })}
                {visibles.length === 0 && (
                  <tr>
                    <td colSpan={COLS.length} className="px-3 py-8 text-center text-sm text-slate-400">
                      Ningún SKU coincide con “{q}”.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-[11px] text-slate-500">
              <span>
                Mostrando {Math.min(limite, visibles.length)} de {fNum(visibles.length)}
                {q ? " coincidencias" : " SKUs con venta"}
                {" · "}
                <span className="text-rose-500">en rojo</span> = dentro del 80% que sostiene la venta
              </span>
              {n(datos.sin_sku?.uds) > 0 && (
                <span className="text-amber-600" title="Ventas registradas sin SKU: no se pueden rankear ni reabastecer">
                  ⚠ {fNum(datos.sin_sku.uds)} uds ({fMoney(datos.sin_sku.venta)}) sin SKU, fuera del ranking
                </span>
              )}
              {limite < visibles.length && (
                <button
                  onClick={() => setLimite((l) => l + 100)}
                  className="rounded-lg border border-slate-200 px-3 py-1 font-medium text-slate-600 hover:bg-slate-50"
                >
                  Ver 100 más
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
