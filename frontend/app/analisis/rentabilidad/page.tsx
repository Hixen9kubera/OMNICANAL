"use client";

/**
 * /analisis/rentabilidad — Análisis de Rentabilidad.
 *
 * Dos sub-pestañas: DEVOLUCIONES (viva) y DROP (anunciada, no construida — una
 * pestaña vacía que parece funcionar es peor que una que dice qué falta).
 * FULFILLMENT se retiró el 31-ago.
 *
 * DEVOLUCIONES sale de `channel.returns`, creada el 31-ago-2026. Antes de ella
 * las devoluciones NO existían en kubera: el barrido de las 73 tablas no
 * encontró ninguna columna `return|devol|refund|claim`, y ni `cancelled` ni
 * `partially_refunded` servían de sustituto (cancelar no es devolver, y en el
 * segundo el valor es el total del pedido, no lo reembolsado).
 *
 * DOS COSAS QUE ESTA PANTALLA DICE EN VOZ ALTA, porque callarlas la volvería
 * engañosa:
 *
 *   1. LA COBERTURA. `channel.returns` arranca donde arrancó su backfill; las
 *      ventas vienen desde diciembre. Pedir 30 días cuando hay 7 capturados da
 *      un porcentaje artificialmente bajo — no porque no hubo devoluciones,
 *      sino porque no se capturaron. El banner ámbar aparece con `parcial` y
 *      dice desde cuándo hay datos. (Medido: a 7 días el valor devuelto es
 *      2.75% de los ingresos; a 30 días cae a 0.76% por puro hueco de datos.)
 *
 *   2. LA ASIMETRÍA DE FECHAS, que no es un bug. Las ventas se fechan cuando se
 *      VENDIÓ; las devoluciones, cuando se abrió el reclamo. En una misma
 *      ventana parte de las devoluciones corresponde a ventas anteriores, así
 *      que el % es "devuelto en el período / vendido en el período", no la tasa
 *      de devolución de una cohorte.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, CalendarDays, Info, PackageX, RefreshCw, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";

// ── Contratos ───────────────────────────────────────────────────────────────
interface Fila {
  sku: string; titulo: string | null;
  uds_devueltas: number; valor_devuelto: number; devoluciones: number;
  tipo: "full" | "drop" | "mixto";
  tiendas: string[]; motivos: string[];
  uds_vendidas: number; ingresos: number;
  pct_uds: number | null;
}
interface Resp {
  periodo: { dias: number | null; desde: string | null; hasta: string | null };
  cobertura: { desde: string | null; hasta: string | null; total: number };
  parcial: boolean;
  kpis: {
    ingresos: number; devuelto_valor: number; pct_valor: number | null;
    unidades: number; devuelto_unidades: number; pct_unidades: number | null;
    devoluciones: number; valor_restable: number;
  };
  por_tipo: Record<"full" | "drop", {
    ingresos: number; unidades: number;
    devuelto_valor: number; devuelto_unidades: number; devoluciones: number;
    pct_ingresos: number | null; pct_unidades: number | null;
    pct_devuelto_valor: number | null; pct_devuelto_unidades: number | null;
    tasa_valor: number | null; tasa_unidades: number | null;
  }>;
  por_tienda: { cuenta: string; devoluciones: number; unidades: number; valor: number }[];
  tabla: Fila[];
}

/* FULFILLMENT se retiró (José, 31-ago): la pregunta que importa es DROP —
   TikTok, Temu y Walmart no tienen Full, así que ahí el modelo es el 100% de su
   operación y su tasa de éxito decide si el canal gana o pierde dinero. */
interface CanalDrop {
  canal: string; activa_def: string | null; censo: string | null;
  publicaciones: number; pub_drop: number; pub_activas: number; pub_drop_activas: number;
  pct_drop: number | null; pct_drop_activas: number | null;
  skus_vendidos: number; uds_drop: number; ingresos_drop: number;
  can_lineas: number; can_uds: number; can_valor: number; bruto_drop: number;
  pct_perdido_drop: number | null; pct_perdido_canal: number | null;
  dev_devoluciones: number; dev_piezas: number; dev_valor: number;
  dev_descartadas: number;
  tasa_dev_uds: number | null; tasa_dev_valor: number | null;
}
interface RespDrop {
  periodo: { dias: number | null; desde: string | null; hasta: string | null };
  nota_woo: string;
  totales: {
    pub_drop: number; pub_drop_activas: number; uds_drop: number; ingresos_drop: number;
    can_lineas: number; can_valor: number; pct_perdido_drop: number | null;
    dev_devoluciones: number; dev_piezas: number; dev_valor: number;
    tasa_dev_uds: number | null; tasa_dev_valor: number | null;
  };
  canales: CanalDrop[];
}

const SUBTABS = [
  { id: "devoluciones", label: "Devoluciones", listo: true },
  { id: "drop", label: "Drop", listo: true },
] as const;

const PERIODOS = [7, 15, 30, 60, 90];
const TIENDAS = ["BEKURA", "SANCORFASHION"];

const fN = (v: number | null | undefined, dec = 0) =>
  v == null ? "—" : Number(v).toLocaleString("es-MX",
    { minimumFractionDigits: dec, maximumFractionDigits: dec });
const fM = (v: number | null | undefined, dec = 0) =>
  v == null ? "—" : `$${fN(v, dec)}`;
const fP = (v: number | null | undefined) => v == null ? "—" : `${fN(v, 2)}%`;

/* El tipo logístico de la devolución. Casi todo es FULL (59 de 62 medidas):
   el producto sale del almacén de ML y vuelve ahí, no a nuestra bodega. */
const TIPO: Record<string, { txt: string; chip: string }> = {
  full:  { txt: "FULL",  chip: "bg-indigo-50 text-indigo-700" },
  drop:  { txt: "DROP",  chip: "bg-sky-50 text-sky-700" },
  mixto: { txt: "MIXTO", chip: "bg-slate-100 text-slate-600" },
};

/* Los motivos que ML pone al cerrar. Solo los cerrados los traen, así que la
   mayoría de las filas abiertas no muestra ninguno — eso es correcto, no un
   hueco de datos. */
const MOTIVO: Record<string, string> = {
  item_returned: "el producto volvió",
  low_cost: "reembolso sin retorno",
  warehouse_decision: "decisión de bodega ML",
  return_cancelled: "devolución cancelada",
  coverage_decision: "cobertura ML",
};

type Col = "valor_devuelto" | "uds_devueltas" | "pct_uds" | "uds_vendidas" | "sku";

export default function RentabilidadPage() {
  const [sub, setSub] = useState<string>("devoluciones");
  const [dias, setDias] = useState(7);
  // Rango explícito del calendario. Vacío = manda el preset de días. Los dos
  // conviven a propósito: el preset es el atajo diario y el rango sirve para
  // cuadrar contra un corte concreto.
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const rango = !!(desde && hasta);
  const [cuenta, setCuenta] = useState<string | null>(null);
  const [data, setData] = useState<Resp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [orden, setOrden] = useState<Col>("valor_devuelto");
  const [asc, setAsc] = useState(false);
  const [drop, setDrop] = useState<RespDrop | null>(null);
  const [errDrop, setErrDrop] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true); setErr(null);
    try {
      const q = new URLSearchParams({ dias: String(dias) });
      if (cuenta) q.set("cuenta", cuenta);
      if (desde && hasta) { q.set("desde", desde); q.set("hasta", hasta); }
      const r = await fetchSesion(
        `${API_BASE}/api/fulfillment/rentabilidad/devoluciones?${q}`,
        { cache: "no-store" });
      if (!r.ok) throw new Error(`API ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, [dias, cuenta, desde, hasta]);

  useEffect(() => { void cargar(); }, [cargar]);

  // DROP tiene su propio endpoint: comparte el período pero no la cuenta (su
  // corte es por CANAL, y las cuentas de un canal no son comparables entre sí).
  const cargarDrop = useCallback(async () => {
    setErrDrop(null);
    try {
      const q = new URLSearchParams({ dias: String(dias) });
      if (desde && hasta) { q.set("desde", desde); q.set("hasta", hasta); }
      const r = await fetchSesion(
        `${API_BASE}/api/fulfillment/rentabilidad/drop?${q}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`API ${r.status}`);
      setDrop(await r.json());
    } catch (e) {
      setErrDrop(e instanceof Error ? e.message : String(e));
    }
  }, [dias, desde, hasta]);
  useEffect(() => { if (sub === "drop") void cargarDrop(); }, [sub, cargarDrop]);

  const filas = useMemo(() => {
    const f = [...(data?.tabla ?? [])];
    f.sort((a, b) => {
      const va = a[orden], vb = b[orden];
      // Los nulos van SIEMPRE al final, ordene como ordene: un SKU sin % no es
      // "el menor", es uno del que no se sabe.
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp = typeof va === "string"
        ? String(va).localeCompare(String(vb))
        : Number(va) - Number(vb);
      return asc ? cmp : -cmp;
    });
    return f;
  }, [data, orden, asc]);

  const ordenarPor = (c: Col) => {
    if (c === orden) { setAsc(!asc); return; }
    setOrden(c);
    // Al cambiar de columna se arranca por lo más alto (o A→Z en texto), que es
    // la pregunta útil: "qué se devuelve más".
    setAsc(c === "sku");
  };

  const Th = ({ c, children, ayuda }: { c: Col; children: React.ReactNode; ayuda?: string }) => (
    <th title={ayuda}
        onClick={() => ordenarPor(c)}
        className="cursor-pointer select-none whitespace-nowrap px-2 py-2 text-right text-[11px]
                   font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-800">
      <span className="inline-flex items-center gap-1">
        {children}
        {orden === c && (asc ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
      </span>
    </th>
  );

  const k = data?.kpis;

  return (
    <div className="space-y-4">
      {/* ── Sub-pestañas ───────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-1.5 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm">
        {SUBTABS.map((s) => (
          <button key={s.id}
            onClick={() => s.listo && setSub(s.id)}
            disabled={!s.listo}
            title={s.listo ? undefined : "Todavía no construida"}
            className={`rounded-xl px-3.5 py-1.5 text-xs font-semibold transition ${
              sub === s.id
                ? "bg-indigo-600 text-white shadow-sm"
                : s.listo
                  ? "text-slate-600 hover:bg-slate-100"
                  : "cursor-not-allowed text-slate-300"}`}>
            {s.label}{!s.listo && " ·"}
          </button>
        ))}
      </div>

      {sub === "drop" ? (
        <>
          <FiltroPeriodo {...{ dias, setDias, desde, setDesde, hasta, setHasta, rango,
                               cuenta: null, setCuenta: null, cargando, cargar: cargarDrop }} />
          {errDrop && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {errDrop}
            </div>
          )}
          {drop && <BloqueDrop d={drop} />}
        </>
      ) : sub !== "devoluciones" ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-sm">
          <p className="font-semibold text-slate-800">
            {SUBTABS.find((s) => s.id === sub)?.label} — todavía no construida
          </p>
        </div>
      ) : (
        <>
          <FiltroPeriodo {...{ dias, setDias, desde, setDesde, hasta, setHasta,
                               rango, cuenta, setCuenta, cargando, cargar }} />

          {err && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {err}
            </div>
          )}

          {/* ── Aviso de cobertura: lo más importante de esta pantalla ── */}
          {data?.parcial && (
            <div className="flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-[13px] text-amber-900">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>
                <b>El porcentaje está subestimado.</b> Las devoluciones
                capturadas empiezan el <b>{data.cobertura.desde}</b>, pero el
                período pedido arranca el <b>{data.periodo.desde}</b>. Los días
                sin captura cuentan ventas pero cero devoluciones, así que el %
                sale más bajo de lo real. Para un número comparable, usa un
                período dentro de la cobertura
                {data.cobertura.desde && data.cobertura.hasta &&
                  <> ({data.cobertura.desde} → {data.cobertura.hasta})</>}.
              </span>
            </div>
          )}

          {k && (
            <>
              {/* ── KPIs: dinero arriba, unidades abajo ──────────────── */}
              <div className="grid gap-3 sm:grid-cols-3">
                <Kpi titulo="Ingresos totales" valor={fM(k.ingresos, 2)}
                     ayuda="Ventas de Mercado Libre en el período (canal_ventas sin cancelados). Mismo universo que las devoluciones: si se compararan contra todos los canales, el % saldría diluido." />
                <Kpi titulo="Devoluciones" valor={fM(k.devuelto_valor, 2)}
                     tono="rojo"
                     pie={`${fN(k.devoluciones)} devoluciones`}
                     ayuda="Valor devuelto = unidades devueltas × precio congelado de la venta. La API de ML no da el monto reembolsado; sale del precio real al que se vendió." />
                <Kpi titulo="% de ingresos" valor={fP(k.pct_valor)}
                     tono={k.pct_valor != null && k.pct_valor >= 3 ? "rojo" : "neutro"}
                     ayuda="Valor devuelto ÷ ingresos del período. Las ventas se fechan cuando se vendió y las devoluciones cuando se abrió el reclamo, así que parte de lo devuelto corresponde a ventas anteriores al período." />
              </div>
              {data.por_tipo && (
                <Desglose
                  tipo={data.por_tipo}
                  fmt={(v) => fM(v, 2)}
                  campo="ingresos" campoDev="devuelto_valor"
                  pct="pct_ingresos" pctDev="pct_devuelto_valor" tasa="tasa_valor"
                />
              )}
              <div className="grid gap-3 sm:grid-cols-3">
                <Kpi titulo="Unidades totales" valor={fN(k.unidades)}
                     ayuda="Piezas vendidas en Mercado Libre en el período." />
                <Kpi titulo="Unidades devueltas" valor={fN(k.devuelto_unidades)}
                     tono="rojo"
                     ayuda="Piezas devueltas (return_quantity de la API, no derivadas del pedido)." />
                <Kpi titulo="% de unidades" valor={fP(k.pct_unidades)}
                     tono={k.pct_unidades != null && k.pct_unidades >= 3 ? "rojo" : "neutro"}
                     ayuda="Unidades devueltas ÷ unidades vendidas. Suele ser MENOR que el % de dinero: lo que se devuelve es más caro que el promedio del catálogo." />
              </div>
              {data.por_tipo && (
                <Desglose
                  tipo={data.por_tipo}
                  fmt={(v) => fN(v)}
                  campo="unidades" campoDev="devuelto_unidades"
                  pct="pct_unidades" pctDev="pct_devuelto_unidades" tasa="tasa_unidades"
                />
              )}

              {/* Lo restable: el matiz que evita restar de más */}
              <div className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[12px] text-slate-600 shadow-sm">
                <Info size={14} className="mt-0.5 shrink-0 text-slate-400" />
                <span>
                  De los {fM(k.devuelto_valor, 2)} devueltos, solo{" "}
                  <b className="text-slate-800">{fM(k.valor_restable, 2)}</b>{" "}
                  son restables de las ventas: el resto corresponde a órdenes que
                  ya estaban canceladas, y su valor ya se había descontado.
                  Restar el total sería contarlo dos veces.
                </span>
              </div>

              {/* ── Por tienda ───────────────────────────────────────── */}
              {data.por_tienda.length > 1 && (
                <div className="grid gap-3 sm:grid-cols-2">
                  {data.por_tienda.map((t) => (
                    <div key={t.cuenta}
                         className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
                      <p className="text-xs font-bold text-slate-700">{t.cuenta}</p>
                      <p className="mt-1 flex items-baseline gap-3">
                        <span className="text-xl font-extrabold tabular-nums text-slate-800">
                          {fM(t.valor, 2)}
                        </span>
                        <span className="text-[12px] tabular-nums text-slate-500">
                          {fN(t.unidades)} uds · {fN(t.devoluciones)} devoluciones
                        </span>
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {/* ── Tabla por SKU ────────────────────────────────────── */}
              <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                  <p className="text-sm font-bold text-slate-800">
                    Devoluciones por SKU
                    <span className="ml-2 text-[11px] font-normal text-slate-400">
                      {fN(filas.length)} productos · clic en la cabecera para ordenar
                    </span>
                  </p>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-[12px]">
                    <thead className="bg-slate-50">
                      <tr>
                        <Th c="sku" ayuda="SKU devuelto">SKU</Th>
                        <th className="px-2 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500">Producto</th>
                        <th className="px-2 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500">Tienda</th>
                        <th className="px-2 py-2 text-center text-[11px] font-semibold uppercase tracking-wide text-slate-500">Tipo</th>
                        <Th c="uds_devueltas" ayuda="Piezas devueltas en el período">Uds dev.</Th>
                        <Th c="valor_devuelto" ayuda="Unidades devueltas × precio de venta congelado">Valor dev.</Th>
                        <Th c="uds_vendidas" ayuda="Piezas vendidas del mismo SKU en el mismo período">Uds vend.</Th>
                        <Th c="pct_uds" ayuda="Devueltas ÷ vendidas del período. Sin esta columna, 3 devoluciones de 500 ventas se ven igual que 3 de 4.">% dev.</Th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                      {filas.map((f) => {
                        const alto = f.pct_uds != null && f.pct_uds >= 20;
                        return (
                          <tr key={f.sku} className="hover:bg-slate-50/60">
                            <td className="whitespace-nowrap px-2 py-1.5 font-semibold text-slate-700">
                              {f.sku}
                            </td>
                            <td className="max-w-[260px] truncate px-2 py-1.5 text-slate-500"
                                title={f.titulo ?? undefined}>
                              {f.titulo ?? "—"}
                            </td>
                            <td className="whitespace-nowrap px-2 py-1.5 text-[11px] text-slate-500">
                              {f.tiendas.join(", ")}
                            </td>
                            <td className="px-2 py-1.5 text-center">
                              <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${TIPO[f.tipo]?.chip ?? ""}`}>
                                {TIPO[f.tipo]?.txt ?? f.tipo}
                              </span>
                              {f.motivos?.length > 0 && (
                                <span className="ml-1 text-[10px] text-slate-400"
                                      title={f.motivos.map((m) => MOTIVO[m] ?? m).join(" · ")}>
                                  ⓘ
                                </span>
                              )}
                            </td>
                            <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-slate-800">
                              {fN(f.uds_devueltas)}
                            </td>
                            <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-red-600">
                              {fM(f.valor_devuelto, 2)}
                            </td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
                              {fN(f.uds_vendidas)}
                            </td>
                            <td className={`px-2 py-1.5 text-right font-semibold tabular-nums ${
                              alto ? "text-red-600" : "text-slate-600"}`}>
                              {fP(f.pct_uds)}
                            </td>
                          </tr>
                        );
                      })}
                      {!filas.length && !cargando && (
                        <tr>
                          <td colSpan={8} className="px-4 py-8 text-center text-slate-400">
                            <PackageX size={20} className="mx-auto mb-2 opacity-50" />
                            Sin devoluciones en el período
                            {data.cobertura.desde
                              ? <> — hay datos capturados del {data.cobertura.desde} al {data.cobertura.hasta}</>
                              : <> — todavía no se ha capturado ninguna devolución</>}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}



/* ── DROP por canal ────────────────────────────────────────────────────────
   Tres bloques SEPARADOS a propósito (José, 31-ago): el dinero que genera, las
   CANCELACIONES y las DEVOLUCIONES. Son tres fracasos distintos —una venta que
   nunca se completó no es lo mismo que una que se completó y se deshizo— y
   mezclarlos esconde los tres.

   El % de cancelación se muestra con DOS denominadores porque una sola cifra
   miente: sobre el bruto DROP dice la salud del modelo; sobre el bruto del
   canal dice el impacto en el negocio. En Mercado Libre, DROP es ~1% de la
   venta, así que sus cancelaciones medidas contra el canal completo se diluyen
   a 1.3% y parece que no pasa nada; contra su propio DROP son 59.9%. */
function BloqueDrop({ d }: { d: RespDrop }) {
  const t = d.totales;
  const NOMBRE: Record<string, string> = {
    mercado_libre: "Mercado Libre", amazon: "Amazon",
    tiktok: "TikTok", temu: "Temu", walmart: "Walmart",
  };
  const censoViejo = (c: CanalDrop) =>
    !!c.censo && (Date.now() - new Date(`${c.censo}T12:00:00`).getTime()) > 3 * 864e5;
  const hay = d.canales.filter((c) => c.publicaciones > 0 || c.uds_drop > 0 || c.can_lineas > 0);

  return (
    <div className="space-y-4">
      {/* Lo que genera */}
      <div className="grid gap-3 sm:grid-cols-4">
        <Kpi titulo="Publicaciones en Drop" valor={fN(t.pub_drop)}
             pie={`${fN(t.pub_drop_activas)} activas`}
             ayuda="Publicaciones con modelo DROP (surtimos nosotros). Woo queda fuera: es catálogo interno, no publicaciones de marketplace." />
        <Kpi titulo="Unidades vendidas" valor={fN(t.uds_drop)}
             ayuda="Piezas vendidas por DROP en el período (sin contar las canceladas)." />
        <Kpi titulo="Ingresos Drop" valor={fM(t.ingresos_drop, 2)}
             ayuda="Ventas efectivas de DROP en el período." />
        <Kpi titulo="Perdido por cancelación" valor={fP(t.pct_perdido_drop)}
             tono={t.pct_perdido_drop != null && t.pct_perdido_drop >= 10 ? "rojo" : "neutro"}
             pie={`${fM(t.can_valor, 2)} de ${fM(t.ingresos_drop + t.can_valor, 2)}`}
             ayuda="Valor cancelado ÷ bruto intentado (efectivo + cancelado). Es la proporción de lo que se vendió por DROP que nunca llegó a cobrarse." />
      </div>

      {/* Cancelaciones y devoluciones, en cajas separadas */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl border border-red-200 bg-red-50/40 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-red-700">
            Cancelaciones · fallos nuestros
          </p>
          <p className="mt-1 flex items-baseline gap-3">
            <span className="text-2xl font-extrabold tabular-nums text-red-700">
              {fN(t.can_lineas)}
            </span>
            <span className="text-sm font-semibold tabular-nums text-red-600">
              {fM(t.can_valor, 2)}
            </span>
          </p>
          <p className="mt-1 text-[11px] text-red-700/80">
            Hoy no se puede distinguir un fallo de bodega de una cancelación del
            comprador: la API no da el motivo. Se cuentan todas como fallo
            nuestro, que es el criterio conservador.
          </p>
        </div>
        <div className="rounded-2xl border border-amber-200 bg-amber-50/40 px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-800">
            Devoluciones de Drop
          </p>
          <p className="mt-1 flex items-baseline gap-3">
            <span className="text-2xl font-extrabold tabular-nums text-amber-800">
              {fN(t.dev_piezas)}
            </span>
            <span className="text-sm font-semibold tabular-nums text-amber-700">
              {fM(t.dev_valor, 2)}
            </span>
            <span className="text-[12px] tabular-nums text-amber-700">
              tasa {fP(t.tasa_dev_uds)} uds · {fP(t.tasa_dev_valor)} valor
            </span>
          </p>
          <p className="mt-1 text-[11px] text-amber-800/80">
            Cosa distinta de la cancelación: aquí la venta SÍ se completó y luego
            se deshizo. No cuentan los reclamos cancelados ni los fallidos — esos
            existieron como reclamo, no como devolución.
          </p>
        </div>
      </div>

      {/* Tabla por canal */}
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-4 py-2.5">
          <p className="text-sm font-bold text-slate-800">Drop por canal</p>
          <p className="text-[11px] text-slate-400">{d.nota_woo}</p>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-[12px]">
            <thead className="bg-slate-50">
              <tr className="text-[10px] uppercase tracking-wide text-slate-400">
                <th className="px-2 py-1.5 text-left" />
                <th className="border-l border-slate-200 px-2 py-1.5 text-center" colSpan={3}>Publicaciones</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-center" colSpan={3}>Vendido</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-center text-red-500" colSpan={4}>Cancelado</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-center text-amber-600" colSpan={3}>Devuelto</th>
              </tr>
              <tr className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                <th className="px-2 py-1.5 text-left">Canal</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-right">total</th>
                <th className="px-2 py-1.5 text-right">drop</th>
                <th className="px-2 py-1.5 text-right">activas</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-right">SKUs</th>
                <th className="px-2 py-1.5 text-right">uds</th>
                <th className="px-2 py-1.5 text-right">ingresos</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-right">líneas</th>
                <th className="px-2 py-1.5 text-right">valor</th>
                <th className="px-2 py-1.5 text-right" title="Valor cancelado ÷ bruto DROP: la salud del modelo DROP en este canal">% s/drop</th>
                <th className="px-2 py-1.5 text-right" title="Valor cancelado ÷ bruto del CANAL completo: el impacto en el negocio">% s/canal</th>
                <th className="border-l border-slate-200 px-2 py-1.5 text-right">piezas</th>
                <th className="px-2 py-1.5 text-right">valor</th>
                <th className="px-2 py-1.5 text-right">tasa uds</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {hay.map((c) => (
                <tr key={c.canal} className="hover:bg-slate-50/60">
                  <td className="whitespace-nowrap px-2 py-1.5 font-semibold text-slate-700">
                    {NOMBRE[c.canal] ?? c.canal}
                    {censoViejo(c) && (
                      <span className="ml-1 text-amber-600"
                            title={`El censo de publicaciones es del ${c.censo}: estos conteos son una foto vieja`}>
                        ⚠
                      </span>
                    )}
                  </td>
                  <td className="border-l border-slate-100 px-2 py-1.5 text-right tabular-nums text-slate-500">{fN(c.publicaciones)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fN(c.pub_drop)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800"
                      title={c.activa_def ? `"Activa" en este canal = ${c.activa_def}` : undefined}>
                    {fN(c.pub_drop_activas)}
                  </td>
                  <td className="border-l border-slate-100 px-2 py-1.5 text-right tabular-nums text-slate-500">{fN(c.skus_vendidos)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fN(c.uds_drop)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">{fM(c.ingresos_drop, 2)}</td>
                  <td className="border-l border-slate-100 px-2 py-1.5 text-right tabular-nums text-red-600">{fN(c.can_lineas)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-red-600">{fM(c.can_valor, 2)}</td>
                  <td className={`px-2 py-1.5 text-right font-semibold tabular-nums ${
                    c.pct_perdido_drop != null && c.pct_perdido_drop >= 20 ? "text-red-700" : "text-slate-600"}`}>
                    {fP(c.pct_perdido_drop)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-400">{fP(c.pct_perdido_canal)}</td>
                  <td className="border-l border-slate-100 px-2 py-1.5 text-right tabular-nums text-amber-700">
                    {fN(c.dev_piezas)}
                    {c.dev_descartadas > 0 && (
                      <span className="ml-1 text-[9px] text-slate-400"
                            title={`${c.dev_descartadas} reclamo(s) cancelado(s) o fallido(s), excluidos de la tasa`}>
                        (−{c.dev_descartadas})
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-amber-700">{fM(c.dev_valor, 2)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-slate-600">{fP(c.tasa_dev_uds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          "Activa" no significa lo mismo en cada canal (pasa el cursor por la
          columna): ML usa <code>active</code>, Amazon{" "}
          <code>discoverable/buyable</code>, TikTok <code>ACTIVATE</code>,
          Walmart <code>PUBLISHED</code>, y Temu manda códigos numéricos sin
          traducir — ahí se aproxima con "tiene stock".
        </p>
      </div>
    </div>
  );
}

/* El filtro de período, compartido por las dos sub-pestañas. Vive en un solo
   componente a propósito: duplicado, la de Drop y la de Devoluciones acabarían
   con calendarios que se comportan distinto. `setCuenta` en null oculta el
   toggle de tienda — Drop corta por CANAL, y las cuentas de un canal no son
   comparables entre sí. */
function FiltroPeriodo({ dias, setDias, desde, setDesde, hasta, setHasta, rango,
                         cuenta, setCuenta, cargando, cargar }: {
  dias: number; setDias: (n: number) => void;
  desde: string; setDesde: (s: string) => void;
  hasta: string; setHasta: (s: string) => void;
  rango: boolean;
  cuenta: string | null; setCuenta: ((c: string | null) => void) | null;
  cargando: boolean; cargar: () => void | Promise<void>;
}) {
  return (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Período</span>
              {PERIODOS.map((d) => (
                <button key={d}
                  onClick={() => { setDias(d); setDesde(""); setHasta(""); }}
                  className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${
                    dias === d && !rango
                      ? "bg-slate-800 text-white"
                      : "text-slate-500 hover:bg-slate-100"}`}>
                  {d}d
                </button>
              ))}
              {/* Calendario: el datepicker NATIVO del navegador (con su Clear
                  y su Today). Mismo patrón que Métricas, Categorías y
                  Reportes — un date picker propio aquí sería una cuarta forma
                  de elegir fechas en el mismo panel. */}
              <span className="mx-1 h-4 w-px bg-slate-200" />
              <div className={`flex items-center gap-1.5 rounded-xl border px-1.5 py-0.5 ${
                rango ? "border-slate-800 bg-slate-50" : "border-slate-200"}`}>
                <CalendarDays size={13} className="text-slate-400" />
                <input type="date" value={desde} max={hasta || undefined}
                       onChange={(e) => setDesde(e.target.value)}
                       className="rounded-lg border-0 bg-transparent px-1 py-1 text-sm text-slate-700 focus:outline-none" />
                <span className="text-slate-400">–</span>
                <input type="date" value={hasta} min={desde || undefined}
                       onChange={(e) => setHasta(e.target.value)}
                       className="rounded-lg border-0 bg-transparent px-1 py-1 text-sm text-slate-700 focus:outline-none" />
                {(desde || hasta) && (
                  <button onClick={() => { setDesde(""); setHasta(""); }}
                          title="Quitar el rango y volver al período en días"
                          className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700">
                    <X size={13} />
                  </button>
                )}
              </div>
              {/* Media fecha no pide nada: se avisa en vez de mostrar un
                  número que no corresponde a lo que el usuario cree. */}
              {(desde || hasta) && !rango && (
                <span className="text-[11px] font-medium text-amber-600">
                  falta la {desde ? "fecha final" : "fecha inicial"}
                </span>
              )}
            </div>
            {setCuenta && <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Tienda</span>
              <button onClick={() => setCuenta(null)}
                className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${
                  cuenta === null ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                Todas
              </button>
              {TIENDAS.map((t) => (
                <button key={t} onClick={() => setCuenta(t)}
                  className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${
                    cuenta === t ? "bg-slate-800 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
                  {t}
                </button>
              ))}
            </div>}
          </div>
          <button onClick={() => void cargar()} disabled={cargando}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50"
            title="Refrescar">
            <RefreshCw size={13} className={cargando ? "animate-spin" : ""} />
          </button>
        </div>
  );
}

/* Desglose FULL / DROP debajo de una fila de KPIs, alineado con sus tres
   columnas: participación en el total, participación en lo devuelto, y la TASA
   de devolución de cada tipo.

   La tasa es la columna que importa y la única que no se deduce de las otras
   dos: FULL puede ser el 99% de la venta y aun así devolverse menos, en
   proporción, que un DROP chico. Con volúmenes bajos es frágil (3 de 20 son
   15%), así que por debajo de 50 unidades vendidas se marca con ~ y lo dice el
   tooltip — un porcentaje sobre 20 piezas no sostiene una decisión. */
function Desglose({ tipo, fmt, campo, campoDev, pct, pctDev, tasa }: {
  tipo: Resp["por_tipo"];
  fmt: (v: number) => string;
  campo: "ingresos" | "unidades";
  campoDev: "devuelto_valor" | "devuelto_unidades";
  pct: "pct_ingresos" | "pct_unidades";
  pctDev: "pct_devuelto_valor" | "pct_devuelto_unidades";
  tasa: "tasa_valor" | "tasa_unidades";
}) {
  const CARAS = [
    { k: "full" as const, txt: "FULL", chip: "bg-indigo-50 text-indigo-700" },
    { k: "drop" as const, txt: "DROP", chip: "bg-sky-50 text-sky-700" },
  ];
  const Celda = ({ titulo, render, ayuda }: {
    titulo: string; ayuda?: string;
    render: (t: Resp["por_tipo"]["full"]) => React.ReactNode;
  }) => (
    <div className="px-3 py-2" title={ayuda}>
      <p className="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">{titulo}</p>
      <div className="mt-1 space-y-0.5">
        {CARAS.map((c) => (
          <div key={c.k} className="flex items-center justify-between gap-2">
            <span className={`rounded px-1 py-px text-[9px] font-bold ${c.chip}`}>{c.txt}</span>
            <span className="tabular-nums text-[11.5px] text-slate-700">{render(tipo[c.k])}</span>
          </div>
        ))}
      </div>
    </div>
  );
  const frágil = (t: Resp["por_tipo"]["full"]) => t.unidades > 0 && t.unidades < 50;
  return (
    <div className="grid divide-y divide-slate-100 rounded-xl border border-slate-200 bg-slate-50/60
                    sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      <Celda titulo="vendido por tipo"
             ayuda="Cuánto de la venta del período salió de cada modelo logístico"
             render={(t) => <>{fmt(t[campo])}
               <span className="ml-1 text-slate-400">{t[pct] == null ? "" : `· ${fN(t[pct], 1)}%`}</span></>} />
      <Celda titulo="devuelto por tipo"
             ayuda="Cómo se reparte lo devuelto entre FULL y DROP"
             render={(t) => <><span className="font-semibold text-red-600">{fmt(t[campoDev])}</span>
               <span className="ml-1 text-slate-400">{t[pctDev] == null ? "" : `· ${fN(t[pctDev], 1)}%`}</span></>} />
      <Celda titulo="tasa de devolución"
             ayuda="Devuelto ÷ vendido DEL MISMO TIPO. Es la comparación que dice si FULL se devuelve más que DROP; no se puede deducir de las otras dos columnas. Con menos de 50 unidades vendidas se marca ~ porque el porcentaje es frágil."
             render={(t) => (
               <span className={`font-semibold ${
                 t[tasa] != null && t[tasa]! >= 5 ? "text-red-600" : "text-slate-700"}`}>
                 {frágil(t) && <span className="text-slate-400">~</span>}
                 {fP(t[tasa])}
                 {frágil(t) && <span className="ml-1 text-[9px] font-normal text-slate-400">
                   ({fN(t.unidades)}u)</span>}
               </span>
             )} />
    </div>
  );
}

/* Tarjeta de KPI. El `ayuda` no es decorativo: cada número de esta pantalla
   tiene una definición que no se adivina (qué universo, qué fecha, qué se
   resta), y sin el tooltip la cifra invita a conclusiones equivocadas. */
function Kpi({ titulo, valor, pie, ayuda, tono = "neutro" }: {
  titulo: string; valor: string; pie?: string; ayuda?: string;
  tono?: "neutro" | "rojo";
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm" title={ayuda}>
      <p className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        {titulo}
        {ayuda && <Info size={11} className="opacity-50" />}
      </p>
      <p className={`mt-1 text-2xl font-extrabold tabular-nums ${
        tono === "rojo" ? "text-red-600" : "text-slate-800"}`}>
        {valor}
      </p>
      {pie && <p className="text-[11px] tabular-nums text-slate-400">{pie}</p>}
    </div>
  );
}
