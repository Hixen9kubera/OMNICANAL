"use client";

/**
 * Precios óptimos: la propuesta del optimizador para cada publicación FULL de ML
 * (activas y pausadas FULL), con su curva y su plan de pasos.
 *
 * Se traen TODAS las filas (`traerTodo`, ~1,900 como máximo) porque los KPIs de
 * arriba tienen que sumar el universo filtrado, no la página que se ve.
 *
 * Punto óptimo (DISENO §5): el MENOR precio con utilidad/día ≥ 90 % de la máxima
 * y margen ≥ piso — «el precio con más visitas y ventas que casi no sacrifica
 * utilidad». Nada de esto se aplica: cada fila sale con autorización PENDIENTE.
 */
import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Clock, Download, Minus } from "lucide-react";
import { Encabezado, useEstado } from "@/components/Marco";
import {
  Buscador, CajaError, Cajon, Cargando, Ceja, Chip, Kpi, MarcoTabla, Paginacion, PuntoCuenta, Renglon, Segmentado, Selector, SinDato, Tarjeta, Th, useRetrasado, Vacio,
} from "@/components/ui";
import { ChipConfianza, ChipEstado, ChipRazon } from "@/components/Chips";
import CurvaPrecio from "@/components/CurvaPrecio";
import { descargarCsvPrecios } from "@/lib/api";
import { cifra, cifraFirmada, dia, pct, pctFirmado, pesos, pesosFirmado, tonoMargen } from "@/lib/formato";
import { COLOR_CUENTA } from "@/lib/tema";
import { RAZON } from "@/lib/vocabulario";
import type { Curva, Pagina, Recomendacion } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

const POR_PAGINA = 50;
const UMBRAL = 0.005; // ±0.5 %: por debajo se lee «mantener»

type Direccion = "" | "subir" | "bajar" | "mantener" | "sin";
function direccion(r: Recomendacion): Direccion {
  if (r.precio_recomendado == null || r.cambio_pct == null) return "sin";
  if (r.cambio_pct > UMBRAL) return "subir";
  if (r.cambio_pct < -UMBRAL) return "bajar";
  return "mantener";
}

const ORDENES: Record<string, (r: Recomendacion) => number | string | null> = {
  sku: (r) => r.sku,
  cambio_pct: (r) => r.cambio_pct,
  precio_actual: (r) => r.precio_actual,
  delta_utilidad: (r) => (r.utilidad_dia.recomendado != null && r.utilidad_dia.actual != null ? r.utilidad_dia.recomendado - r.utilidad_dia.actual : null),
  unidades: (r) => r.unidades_dia.actual,
  visitas: (r) => r.visitas_dia.actual,
  margen: (r) => r.margen.actual,
  beta: (r) => r.elasticidad.beta,
};

function Cambio({ r }: { r: Recomendacion }) {
  const d = direccion(r);
  if (d === "sin") return <SinDato texto="sin propuesta" />;
  const Icono = d === "subir" ? ArrowUpRight : d === "bajar" ? ArrowDownRight : Minus;
  const tono = d === "subir" ? "text-emerald-700 bg-emerald-50" : d === "bajar" ? "text-sky-700 bg-sky-50" : "text-slate-500 bg-slate-100";
  return (
    <div className="flex items-center justify-end gap-2 whitespace-nowrap">
      <span className="text-slate-500 tabular-nums">{pesos(r.precio_actual)}</span>
      <ArrowRight size={12} className="text-slate-300" />
      <b className="tabular-nums text-slate-900">{pesos(r.precio_recomendado)}</b>
      <span className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-[10.5px] font-bold tabular-nums ${tono}`}>
        <Icono size={11} />{pctFirmado(r.cambio_pct, 1)}
      </span>
    </div>
  );
}

function ParCifras({ a, b, fmt }: { a: number | null; b: number | null; fmt: (v: number | null) => string }) {
  return (
    <span className="whitespace-nowrap tabular-nums">
      <span className="text-slate-500">{fmt(a)}</span>
      <span className="mx-1 text-slate-300">→</span>
      <b className="text-slate-800">{fmt(b)}</b>
    </span>
  );
}

export default function PaginaPrecios() {
  const estadoLab = useEstado();
  const { datos, error, cargando, recargar } = usePedido<Pagina<Recomendacion>>("/precios", {}, { todo: true });
  const [cuenta, setCuenta] = useState("");
  const [estado, setEstado] = useState("");
  const [razon, setRazon] = useState("");
  const [confianza, setConfianza] = useState("");
  const [dir, setDir] = useState<Direccion>("");
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState("-delta_utilidad");
  const [page, setPage] = useState(1);
  const [abierta, setAbierta] = useState<Recomendacion | null>(null);
  const qq = useRetrasado(q, 200).trim().toLowerCase();
  const filas = datos?.filas ?? [];

  const razones = useMemo(() => [...new Set(filas.flatMap((f) => f.razones))].sort(), [filas]);

  // Todo lo que NO es la dirección: los KPIs de subir/bajar se cuentan sobre esto.
  const base = useMemo(() => filas.filter((f) =>
    (!cuenta || f.cuenta === cuenta)
    && (!estado || f.estado === estado)
    && (!razon || f.razones.includes(razon))
    && (!confianza || f.elasticidad.confianza === confianza)
    && (!qq || (f.sku ?? "").toLowerCase().includes(qq) || (f.titulo ?? "").toLowerCase().includes(qq) || f.listing_id.toLowerCase().includes(qq))),
  [filas, cuenta, estado, razon, confianza, qq]);

  const visibles = useMemo(() => {
    const v = dir ? base.filter((f) => direccion(f) === dir) : base;
    const desc = orden.startsWith("-");
    const acc = ORDENES[desc ? orden.slice(1) : orden] ?? ORDENES.cambio_pct;
    return [...v].sort((a, b) => {
      const x = acc(a); const y = acc(b);
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "es");
      return desc ? -c : c;
    });
  }, [base, dir, orden]);

  const kpi = useMemo(() => {
    let con = 0, subir = 0, bajar = 0, mantener = 0, dU = 0, dUn = 0, activas = 0;
    for (const f of base) {
      const d = direccion(f);
      if (d !== "sin") con++;
      if (d === "subir") subir++; else if (d === "bajar") bajar++; else if (d === "mantener") mantener++;
      // Sólo las ACTIVAS pueden realizar el cambio: una pausada FULL no vende hasta reponer.
      if (f.estado === "activa" && d !== "sin") {
        activas++;
        if (f.utilidad_dia.recomendado != null && f.utilidad_dia.actual != null) dU += f.utilidad_dia.recomendado - f.utilidad_dia.actual;
        if (f.unidades_dia.recomendado != null && f.unidades_dia.actual != null) dUn += f.unidades_dia.recomendado - f.unidades_dia.actual;
      }
    }
    return { con, subir, bajar, mantener, dU, dUn, activas, total: base.length };
  }, [base]);

  const pagina = visibles.slice((page - 1) * POR_PAGINA, page * POR_PAGINA);
  const reiniciar = <T,>(set: (x: T) => void) => (x: T) => { set(x); setPage(1); };
  const alternarDir = (d: Direccion) => { setDir((x) => (x === d ? "" : d)); setPage(1); };
  const piso = Number((datos?.parametros as { piso_margen?: number } | undefined)?.piso_margen ?? 0.12);

  return (
    <>
      <Encabezado
        titulo="Precios óptimos"
        descripcion={<>Para cada publicación FULL de Mercado Libre: el menor precio que conserva ≥ 90 % de la utilidad máxima con margen ≥ {pct(piso, 0)}. Son <b className="text-slate-700">propuestas</b>: todas quedan pendientes de autorización.</>}
        derecha={
          <button type="button" onClick={() => void descargarCsvPrecios({ cuenta, estado, razon, confianza, q: q.trim() })}
                  title="Baja las propuestas con los filtros de esta vista (sin paginar)."
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700">
            <Download size={14} /> Exportar lista de precios (CSV)
          </button>
        } />

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Con propuesta" valor={cifra(kpi.con)} tono="text-indigo-600" pie={`de ${cifra(kpi.total)} FULL (activas + pausadas)`} />
        <Kpi label="Subir" valor={cifra(kpi.subir)} tono="text-emerald-600" onClick={() => alternarDir("subir")} activo={dir === "subir"} pie="clic para filtrar" />
        <Kpi label="Bajar" valor={cifra(kpi.bajar)} tono="text-sky-600" onClick={() => alternarDir("bajar")} activo={dir === "bajar"} pie="clic para filtrar" />
        <Kpi label="Mantener" valor={cifra(kpi.mantener)} onClick={() => alternarDir("mantener")} activo={dir === "mantener"} pie="cambio < ±0.5 %" />
        <Kpi label="Δ utilidad / día" valor={pesosFirmado(Math.round(kpi.dU))} tono={kpi.dU >= 0 ? "text-emerald-600" : "text-rose-600"}
             pie={`esperado · ${cifra(kpi.activas)} activas`} ayuda="Suma de (utilidad/día al recomendado − actual) en las activas con propuesta. Las pausadas no venden hasta reponer FULL." />
        <Kpi label="Δ unidades / día" valor={cifraFirmada(kpi.dUn)} tono={kpi.dUn >= 0 ? "text-emerald-600" : "text-rose-600"} pie="esperado · activas" />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmentado valor={cuenta} onCambio={reiniciar(setCuenta)} opciones={[
          { id: "", label: "Ambas cuentas" },
          { id: "BEKURA", label: "Kubera", punto: COLOR_CUENTA.BEKURA },
          { id: "SANCORFASHION", label: "San Corpe", punto: COLOR_CUENTA.SANCORFASHION },
        ]} />
        <Segmentado valor={estado} onCambio={reiniciar(setEstado)} oscuro opciones={[
          { id: "", label: "Activas y pausadas" }, { id: "activa", label: "Activas" }, { id: "pausada", label: "Pausadas" },
        ]} />
        <Segmentado valor={confianza} onCambio={reiniciar(setConfianza)} opciones={[
          { id: "", label: "Toda confianza" }, { id: "alta", label: "Alta" }, { id: "media", label: "Media" }, { id: "baja", label: "Baja" },
        ]} />
        <Selector etiqueta="Razón" valor={razon} onCambio={reiniciar(setRazon)}
                  opciones={[{ id: "", label: "Todas" }, ...razones.map((r) => ({ id: r, label: RAZON[r]?.label ?? r }))]} />
        <Buscador valor={q} onCambio={(x) => { setQ(x); setPage(1); }} />
      </div>

      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}

      <MarcoTabla
        titulo={dir ? `Propuestas: ${dir}` : "Propuestas de precio"}
        derecha={<span className="text-[11px] text-slate-400">Clic en una fila para ver la curva y el plan{estadoLab?.generado_at ? ` · corrida del ${dia(estadoLab.generado_at)}` : ""}</span>}
        pie={datos && <Paginacion page={page} total={visibles.length} perPage={POR_PAGINA} onPage={setPage} />}>
        {!datos && cargando ? <Cargando /> : visibles.length === 0 ? <Vacio texto="Ninguna propuesta con esos filtros." /> : (
          <table className="tabla-lab min-w-[1600px]">
            <thead>
              <tr>
                <Th className="pl-4" campo="sku" orden={orden} onOrden={setOrden}>SKU</Th>
                <Th>Publicación</Th>
                <Th campo="cambio_pct" orden={orden} onOrden={setOrden} alinear="der" ayuda="Precio cobrado hoy → precio recomendado (punto óptimo).">Actual → recomendado</Th>
                <Th alinear="der" ayuda="Precio con margen = piso (12 %). Abajo de aquí no se recomienda.">Piso</Th>
                <Th alinear="der" ayuda="Precio con utilidad = 0.">Equilibrio</Th>
                <Th alinear="der" ayuda="Precio que maximiza la utilidad por día.">Máx. utilidad</Th>
                <Th alinear="der" ayuda="Mediana SERP filtrada > sugerido de ML > mediana de más vendidos.">Ref. competencia</Th>
                <Th campo="unidades" orden={orden} onOrden={setOrden} alinear="der" ayuda="Unidades por día: base de los últimos 28 días sin censura → esperado al recomendado.">Unid./día</Th>
                <Th campo="visitas" orden={orden} onOrden={setOrden} alinear="der">Visitas/día</Th>
                <Th alinear="der">Conversión</Th>
                <Th campo="margen" orden={orden} onOrden={setOrden} alinear="der">Margen</Th>
                <Th campo="delta_utilidad" orden={orden} onOrden={setOrden} alinear="der" ayuda="Utilidad por día esperada al recomendado menos la actual.">Δ util./día</Th>
                <Th campo="beta" orden={orden} onOrden={setOrden} alinear="der" ayuda="Elasticidad de unidades al precio (β) y su confianza: alta = propia del item; media = categoría; baja = global o prior.">Elasticidad</Th>
                <Th>Razones</Th>
                <Th lado="der">Autorización</Th>
              </tr>
            </thead>
            <tbody>
              {pagina.map((r) => {
                const dU = r.utilidad_dia.recomendado != null && r.utilidad_dia.actual != null ? r.utilidad_dia.recomendado - r.utilidad_dia.actual : null;
                return (
                  <tr key={r.id} className="clicable" tabIndex={0} onClick={() => setAbierta(r)} onKeyDown={(e) => { if (e.key === "Enter") setAbierta(r); }}>
                    <td className="whitespace-nowrap pl-4 font-mono text-[11.5px] font-semibold text-slate-700">{r.sku ?? "—"}</td>
                    <td>
                      <div className="max-w-[280px]">
                        <div className="truncate text-[12.5px] text-slate-800" title={r.titulo ?? ""}>{r.titulo ?? "—"}</div>
                        <div className="mt-0.5 flex items-center gap-2">
                          <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} />
                          <ChipEstado estado={r.estado} />
                        </div>
                      </div>
                    </td>
                    <td className="num"><Cambio r={r} /></td>
                    <td className="num text-slate-600">{pesos(r.precio_piso)}</td>
                    <td className="num text-slate-600">{pesos(r.precio_equilibrio)}</td>
                    <td className="num text-slate-600">{pesos(r.precio_max_utilidad)}</td>
                    <td className="num">
                      {r.precio_ref_competencia != null ? (
                        <><div className="text-slate-800">{pesos(r.precio_ref_competencia)}</div><div className="text-[10.5px] text-slate-400">{r.fuente_ref ?? ""}</div></>
                      ) : <SinDato texto="sin ref." />}
                    </td>
                    <td className="num"><ParCifras a={r.unidades_dia.actual} b={r.unidades_dia.recomendado} fmt={(v) => cifra(v)} /></td>
                    <td className="num"><ParCifras a={r.visitas_dia.actual} b={r.visitas_dia.recomendado} fmt={(v) => cifra(v)} /></td>
                    <td className="num"><ParCifras a={r.conversion.actual} b={r.conversion.recomendado} fmt={(v) => pct(v, 1)} /></td>
                    <td className="num">
                      <span className={`${tonoMargen(r.margen.actual, piso)} tabular-nums`}>{pct(r.margen.actual)}</span>
                      <span className="mx-1 text-slate-300">→</span>
                      <b className={`${tonoMargen(r.margen.recomendado, piso)} tabular-nums`}>{pct(r.margen.recomendado)}</b>
                    </td>
                    <td className={`num font-semibold ${dU == null ? "text-slate-400" : dU >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{pesosFirmado(dU)}</td>
                    <td className="num">
                      <div className="flex items-center justify-end gap-1.5">
                        <span className="tabular-nums text-slate-800">{r.elasticidad.beta == null ? "—" : r.elasticidad.beta.toFixed(2)}</span>
                        <ChipConfianza confianza={r.elasticidad.confianza} />
                      </div>
                    </td>
                    <td>
                      <div className="flex max-w-[260px] flex-wrap gap-1">
                        {r.razones.length ? r.razones.map((x) => <ChipRazon key={x} razon={x} />) : <span className="text-[11px] text-slate-300">—</span>}
                      </div>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-slate-300 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-slate-500"
                            title="El laboratorio sólo propone. Aplicar un precio requiere autorización y se hace fuera de aquí.">
                        <Clock size={10} /> {r.autorizacion === "pendiente" ? "Pendiente" : r.autorizacion}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </MarcoTabla>

      <CajonPrecio rec={abierta} onCerrar={() => setAbierta(null)} piso={piso} />
    </>
  );
}

function CajonPrecio({ rec, onCerrar, piso }: { rec: Recomendacion | null; onCerrar: () => void; piso: number }) {
  const { datos: curva, error, cargando } = usePedido<Curva>(rec ? `/curva/${encodeURIComponent(rec.id)}` : null);
  if (!rec) return null;
  const r = rec;
  const dU = r.utilidad_dia.recomendado != null && r.utilidad_dia.actual != null ? r.utilidad_dia.recomendado - r.utilidad_dia.actual : null;
  const dUn = r.unidades_dia.recomendado != null && r.unidades_dia.actual != null ? r.unidades_dia.recomendado - r.unidades_dia.actual : null;
  const pasos = r.plan?.pasos ?? [];

  return (
    <Cajon abierto={!!rec} onCerrar={onCerrar} ancho="max-w-3xl" titulo={r.titulo ?? r.sku ?? r.id}
           subtitulo={
             <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
               <span className="font-mono text-slate-700">{r.sku}</span>
               <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} />
               <span className="font-mono">{r.listing_id}</span>
               <ChipEstado estado={r.estado} />
             </span>
           }>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Precio</div>
            <div className="mt-1"><Cambio r={r} /></div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Δ utilidad / día</div>
            <div className={`mt-1 text-lg font-bold ${dU == null ? "text-slate-400" : dU >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{pesosFirmado(dU)}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Δ unidades / día</div>
            <div className={`mt-1 text-lg font-bold ${dUn == null ? "text-slate-400" : dUn >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{cifraFirmada(dUn)}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Margen</div>
            <div className="mt-1 text-lg font-bold">
              <span className={tonoMargen(r.margen.actual, piso)}>{pct(r.margen.actual)}</span>
              <span className="mx-1 text-slate-300">→</span>
              <span className={tonoMargen(r.margen.recomendado, piso)}>{pct(r.margen.recomendado)}</span>
            </div>
          </div>
        </div>

        <Tarjeta className="p-4">
          <Ceja>Curva de precio</Ceja>
          <div className="mt-2">
            {cargando && !curva ? <Cargando texto="Calculando la curva…" /> : error ? <CajaError mensaje={error} /> : curva && <CurvaPrecio curva={curva} />}
          </div>
        </Tarjeta>

        <Tarjeta className="p-4">
          <div className="flex items-center justify-between">
            <Ceja>Plan de ajuste · {r.plan?.modo ?? "—"}</Ceja>
            <Chip tono="slate" titulo="Propuesta, no ejecución: cada paso requiere autorización."><Clock size={10} /> Pendiente</Chip>
          </div>
          {pasos.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">{r.precio_recomendado == null ? "Sin propuesta: no hay plan." : "El precio actual ya está en el punto óptimo: no hay pasos."}</p>
          ) : (
            <table className="mt-2 w-full text-[12.5px]">
              <thead>
                <tr className="text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="py-1.5 text-left font-semibold">Fecha</th>
                  <th className="py-1.5 text-right font-semibold">Precio</th>
                  <th className="py-1.5 text-right font-semibold">Cambio</th>
                  <th className="py-1.5 pl-4 text-left font-semibold">Nota</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-slate-100 text-slate-400">
                  <td className="py-1.5">Hoy</td><td className="py-1.5 text-right tabular-nums">{pesos(r.precio_actual)}</td><td /><td className="py-1.5 pl-4">precio actual</td>
                </tr>
                {pasos.map((p, i) => {
                  const antes = i === 0 ? r.precio_actual : pasos[i - 1].precio;
                  return (
                    <tr key={`${p.fecha}-${i}`} className="border-t border-slate-100">
                      <td className="py-1.5 text-slate-700">{dia(p.fecha)}</td>
                      <td className="py-1.5 text-right font-semibold tabular-nums text-slate-900">{pesos(p.precio)}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-500">{antes ? pctFirmado((p.precio - antes) / antes) : "—"}</td>
                      <td className="py-1.5 pl-4 text-[11.5px] text-slate-500">{p.nota ?? ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Tarjeta>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Tarjeta className="p-4">
            <Ceja>Elasticidad</Ceja>
            <div className="mt-1">
              <Renglon etiqueta="β unidades" valor={<span className="inline-flex items-center gap-1.5">{r.elasticidad.beta?.toFixed(2) ?? "—"} <ChipConfianza confianza={r.elasticidad.confianza} /></span>} />
              <Renglon etiqueta="β visitas" valor={r.elasticidad.beta_visitas?.toFixed(2) ?? "—"} />
              <Renglon etiqueta="β conversión" valor={r.elasticidad.beta_conversion?.toFixed(2) ?? "—"} />
              <Renglon etiqueta="Fuente" valor={r.elasticidad.fuente} />
              <Renglon etiqueta="Semanas válidas" valor={cifra(r.elasticidad.n_semanas)} />
            </div>
            <p className="mt-2 text-[11px] leading-snug text-slate-400">β = −1.6 quiere decir: 10 % más barato → ~16 % más unidades.</p>
          </Tarjeta>
          <Tarjeta className="p-4">
            <Ceja>Stock y cobertura</Ceja>
            <div className="mt-1">
              <Renglon etiqueta="Stock FULL" valor={cifra(r.stock.full)} />
              <Renglon etiqueta="Libre en Odoo" valor={r.stock.odoo == null ? <SinDato /> : cifra(r.stock.odoo)} />
              <Renglon etiqueta="Cobertura" valor={r.stock.cobertura_dias == null ? "—" : `${cifra(r.stock.cobertura_dias)} días`}
                       tono={r.stock.cobertura_dias != null && r.stock.cobertura_dias < 10 ? "text-rose-600" : undefined} />
              <Renglon etiqueta="Precio máx. volumen" valor={pesos(r.precio_max_volumen)} ayuda="El menor precio con margen ≥ piso." />
            </div>
          </Tarjeta>
        </div>

        {r.razones.length > 0 && (
          <Tarjeta className="p-4">
            <Ceja>Por qué</Ceja>
            <ul className="mt-2 space-y-1.5">
              {r.razones.map((x) => (
                <li key={x} className="flex items-start gap-2 text-[12.5px] text-slate-600">
                  <ChipRazon razon={x} /><span className="pt-0.5">{RAZON[x]?.ayuda ?? ""}</span>
                </li>
              ))}
            </ul>
          </Tarjeta>
        )}
      </div>
    </Cajon>
  );
}
