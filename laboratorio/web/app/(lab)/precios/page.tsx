"use client";

/**
 * Precios óptimos: la propuesta del optimizador para cada publicación FULL de ML
 * (486 activas y 1,258 pausadas FULL el 28-sep), con su curva y su plan de pasos.
 *
 * Se traen TODAS las filas (`traerTodo`, 1,744) porque los KPIs de arriba y los
 * conteos de cada filtro tienen que sumar el universo filtrado, no la página.
 *
 * Punto óptimo (DISENO §5): el MENOR precio con utilidad/día ≥ 90 % de la máxima
 * y margen ≥ piso — «el precio con más visitas y ventas que casi no sacrifica
 * utilidad». Nada de esto se aplica: cada fila sale con autorización PENDIENTE.
 *
 * Se abre en ACTIVAS: son las que venden hoy y donde un cambio se nota. Las
 * pausadas FULL traen un plan «al reactivar» (el precio se fija al volver a
 * publicar) y quedan a un clic con su conteo a la vista.
 */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeftRight, ArrowRight, Clock, Download, ShieldCheck, Tag } from "lucide-react";
import { Encabezado, useEstado } from "@/components/Marco";
import {
  Buscador, CajaError, Cajon, Cargando, Ceja, Chip, Franja, Kpi, MarcoTabla, Miniatura, Paginacion, PuntoCuenta, Renglon, Segmentado, Selector, SinDato, Tarjeta, Th, useRetrasado, Vacio,
} from "@/components/ui";
import { ChipCambio, ChipConfianza, ChipCoordinado, ChipEstado, ChipRazon, ChipRiesgoMercancia, direccionDe } from "@/components/Chips";
import type { Direccion } from "@/components/Chips";
import CurvaPrecio from "@/components/CurvaPrecio";
import { descargarCsvPrecios } from "@/lib/api";
import { cifra, cifraFirmada, dia, pct, pctFirmado, pesos, pesosFirmado, tonoMargen, urlSegura } from "@/lib/formato";
import { COLOR_CUENTA } from "@/lib/tema";
import { AVISO_REC, CLASES_TONO, FUENTE_REF, PLAN_MODO, RAZON, REF_CAMBIO, avisosOrdenados, claveAviso, fuenteCosto, leerAviso, tieneAviso } from "@/lib/vocabulario";
import type { Curva, Pagina, PasoPlan, Recomendacion } from "@/lib/tipos";
import { umbralDe } from "@/lib/useParametros";
import { usePedido } from "@/lib/usePedido";

const POR_PAGINA = 50;

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

/**
 * El precio contra el que se mide el cambio (`cambio_ref`): en las activas el de
 * hoy; en las pausadas lo que de verdad pagaban en su base (su precio publicado
 * está en mediana 1.61× arriba de eso: «−36 %» contra él era subir).
 */
function precioReferencia(r: Recomendacion): number | null {
  return r.cambio_ref === "precio_realizado_base" && r.base?.p_base != null ? r.base.p_base : r.precio_actual;
}

function Cambio({ r, umbral, alinear = "der" }: { r: Recomendacion; umbral: number; alinear?: "der" | "izq" }) {
  if (r.precio_recomendado == null) return <SinDato texto="sin propuesta" />;
  const vsRealizado = r.cambio_ref === "precio_realizado_base";
  const titulo = vsRealizado
    ? `Cambio ${REF_CAMBIO.precio_realizado_base.largo}: ${pesos(r.base?.p_base)}. Contra el precio publicado hoy (${pesos(r.precio_actual)}) sería ${pctFirmado(r.cambio_vs_actual)}.`
    : `Cambio ${REF_CAMBIO.precio_actual.largo}.`;
  return (
    <div className={`flex flex-col gap-0.5 ${alinear === "der" ? "items-end" : "items-start"}`} title={titulo}>
      <div className="flex items-center gap-2 whitespace-nowrap">
        <span className="tabular-nums text-slate-500">{pesos(precioReferencia(r))}</span>
        <ArrowRight size={12} className="text-slate-300" aria-hidden />
        <b className="tabular-nums text-slate-900">{pesos(r.precio_recomendado)}</b>
        <ChipCambio cambio={r.cambio_pct} umbral={umbral} />
      </div>
      {vsRealizado && <span className="whitespace-nowrap text-[10.5px] text-slate-500">vs lo que pagaban · hoy publicada a {pesos(r.precio_actual)}</span>}
    </div>
  );
}

/** Razones de la fila: el riesgo de la mercancía va primero; coordinación ya va junto a la cuenta. */
function FilaRazones({ r, piso }: { r: Recomendacion; piso: number }) {
  const riesgo = tieneAviso(r.avisos, "riesgo_si_se_cobra_mercancia");
  const zs = r.razones.filter((z) => z !== "coordinado_otra_cuenta" && z !== "brecha_entre_cuentas");
  const visibles = zs.slice(0, riesgo ? 1 : 2);
  const resto = zs.slice(visibles.length);
  return (
    <div className="flex max-w-[280px] flex-wrap gap-1">
      {riesgo && <ChipRiesgoMercancia compacto margen={r.costo?.margen_costo_panel_recomendado} costoPanel={r.costo?.costo_panel} piso={piso} />}
      {visibles.map((z) => <ChipRazon key={z} razon={z} />)}
      {resto.length > 0 && <Chip tono="slate" titulo={resto.map((z) => RAZON[z]?.label ?? z).join(" · ")}>+{resto.length}</Chip>}
      {!riesgo && zs.length === 0 && <span className="text-[11px] text-slate-300">—</span>}
    </div>
  );
}

function ParCifras({ a, b, fmt }: { a: number | null; b: number | null; fmt: (v: number | null) => string }) {
  return (
    <span className="whitespace-nowrap tabular-nums">
      <span className="text-slate-500">{fmt(a)}</span>
      <span className="mx-1 text-slate-300" aria-hidden>→</span>
      <b className="text-slate-800">{fmt(b)}</b>
    </span>
  );
}

type Filtros = { cuenta: string; estado: string; razon: string; aviso: string; confianza: string; dir: Direccion | ""; qq: string };

function pasa(f: Recomendacion, x: Filtros, umbral: number, ignorar?: keyof Filtros): boolean {
  return (ignorar === "cuenta" || !x.cuenta || f.cuenta === x.cuenta)
    && (ignorar === "estado" || !x.estado || f.estado === x.estado)
    && (ignorar === "razon" || !x.razon || f.razones.includes(x.razon))
    && (ignorar === "aviso" || !x.aviso || tieneAviso(f.avisos, x.aviso))
    && (ignorar === "confianza" || !x.confianza || f.elasticidad.confianza === x.confianza)
    && (ignorar === "dir" || !x.dir || direccionDe(f.cambio_pct, umbral) === x.dir)
    && (!x.qq || (f.sku ?? "").toLowerCase().includes(x.qq) || (f.titulo ?? "").toLowerCase().includes(x.qq) || f.listing_id.toLowerCase().includes(x.qq));
}

/** Conteos de una faceta ignorando su propio filtro (mismo criterio que api.py). */
function contar(filas: Recomendacion[], x: Filtros, umbral: number, faceta: keyof Filtros, clave: (f: Recomendacion) => string[]): Record<string, number> {
  const out: Record<string, number> = {};
  let total = 0;
  for (const f of filas) {
    if (!pasa(f, x, umbral, faceta)) continue;
    total++;
    for (const k of clave(f)) out[k] = (out[k] ?? 0) + 1;
  }
  out[""] = total;
  return out;
}

export default function PaginaPrecios() {
  const estadoLab = useEstado();
  const { datos, error, cargando, recargar } = usePedido<Pagina<Recomendacion>>("/precios", {}, { todo: true });
  const [cuenta, setCuenta] = useState("");
  const [estado, setEstado] = useState("activa");
  const [razon, setRazon] = useState("");
  const [aviso, setAviso] = useState("");
  const [confianza, setConfianza] = useState("");
  const [dir, setDir] = useState<Direccion | "">("");
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState("-delta_utilidad");
  const [page, setPage] = useState(1);
  const [abierta, setAbierta] = useState<Recomendacion | null>(null);
  const qq = useRetrasado(q, 200).trim().toLowerCase();
  const filas = useMemo(() => datos?.filas ?? [], [datos]);
  const umbral = umbralDe(datos?.parametros);
  const piso = Number((datos?.parametros as { piso_margen?: number } | undefined)?.piso_margen ?? 0.12);

  // ?q=MLM… (viene del cajón de Publicaciones): se busca en activas y pausadas.
  useEffect(() => {
    const q0 = new URLSearchParams(window.location.search).get("q");
    if (q0) { setQ(q0); setEstado(""); }
  }, []);
  // Si la búsqueda deja una sola fila, se abre su cajón (el enlace desde Publicaciones).
  const [autoAbierto, setAutoAbierto] = useState(false);

  const x: Filtros = { cuenta, estado, razon, aviso, confianza, dir, qq };
  const base = useMemo(() => filas.filter((f) => pasa(f, x, umbral, "dir")),
    [filas, cuenta, estado, razon, aviso, confianza, qq, umbral]); // eslint-disable-line react-hooks/exhaustive-deps

  const facetas = useMemo(() => ({
    cuenta: contar(filas, x, umbral, "cuenta", (f) => [f.cuenta]),
    estado: contar(filas, x, umbral, "estado", (f) => [f.estado]),
    confianza: contar(filas, x, umbral, "confianza", (f) => [f.elasticidad.confianza]),
    razon: contar(filas, x, umbral, "razon", (f) => f.razones),
    aviso: contar(filas, x, umbral, "aviso", (f) => [...new Set((f.avisos ?? []).map(claveAviso))]),
  }), [filas, cuenta, estado, razon, aviso, confianza, dir, qq, umbral]); // eslint-disable-line react-hooks/exhaustive-deps

  const razones = useMemo(() => [...new Set(filas.flatMap((f) => f.razones))].sort(
    (a, b) => (RAZON[a]?.label ?? a).localeCompare(RAZON[b]?.label ?? b, "es")), [filas]);
  // Avisos que cambian la decisión primero (el riesgo de la mercancía, arriba); los informativos al final.
  const avisos = useMemo(() => [...new Set(filas.flatMap((f) => (f.avisos ?? []).map(claveAviso)))].sort((a, b) => {
    const pa = a === "riesgo_si_se_cobra_mercancia" ? -1 : AVISO_REC[a]?.informativo ? 1 : 0;
    const pb = b === "riesgo_si_se_cobra_mercancia" ? -1 : AVISO_REC[b]?.informativo ? 1 : 0;
    return pa - pb || (AVISO_REC[a]?.label ?? a).localeCompare(AVISO_REC[b]?.label ?? b, "es");
  }), [filas]);

  const visibles = useMemo(() => {
    const v = dir ? base.filter((f) => direccionDe(f.cambio_pct, umbral) === dir) : base;
    const desc = orden.startsWith("-");
    const acc = ORDENES[desc ? orden.slice(1) : orden] ?? ORDENES.cambio_pct;
    return [...v].sort((a, b) => {
      const p = acc(a); const s = acc(b);
      if (p === null || p === undefined) return 1;
      if (s === null || s === undefined) return -1;
      const c = typeof p === "number" && typeof s === "number" ? p - s : String(p).localeCompare(String(s), "es");
      return desc ? -c : c;
    });
  }, [base, dir, orden, umbral]);

  useEffect(() => {
    if (!autoAbierto && qq && datos && visibles.length === 1) { setAbierta(visibles[0]); setAutoAbierto(true); }
  }, [autoAbierto, qq, datos, visibles]);

  const kpi = useMemo(() => {
    let con = 0, subir = 0, bajar = 0, mantener = 0, dU = 0, dUn = 0, activas = 0;
    for (const f of base) {
      const d = direccionDe(f.cambio_pct, umbral);
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
  }, [base, umbral]);

  const pagina = visibles.slice((page - 1) * POR_PAGINA, page * POR_PAGINA);
  const reiniciar = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };
  const alternarDir = (d: Direccion) => { setDir((v) => (v === d ? "" : d)); setPage(1); };
  const hay = !!datos && !datos.sin_datos;
  const k = (n: number) => (hay ? cifra(n) : "—");
  const n = (m: Record<string, number>, id: string) => (hay ? m[id] ?? 0 : null);

  return (
    <>
      <Encabezado
        titulo="Precios óptimos"
        descripcion={<>Para cada publicación FULL de Mercado Libre: el menor precio que conserva ≥ 90 % de la utilidad máxima con margen ≥ {pct(piso, 0)}.</>}
        derecha={
          <button type="button" onClick={() => void descargarCsvPrecios({ cuenta, estado, razon, confianza, q: q.trim() })}
                  title="Baja las propuestas con los filtros de esta vista (sin paginar)."
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600">
            <Download size={14} aria-hidden /> Exportar propuestas (CSV)
          </button>
        } />

      <div className="mb-4">
        <Franja icono={<ShieldCheck size={16} className="text-indigo-600" />}>
          <b className="font-semibold">Propuesta. Ningún precio se aplica sin autorización.</b>{" "}
          <span className="text-indigo-800/80">El laboratorio sólo lee y calcula; cada fila sale «pendiente» y el cambio, si se autoriza, se hace fuera de aquí.</span>
        </Franja>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Con propuesta" valor={k(kpi.con)} tono="text-indigo-600" pie={hay ? `de ${cifra(kpi.total)} FULL en esta vista` : "cargando…"} />
        <Kpi label="Subir" valor={k(kpi.subir)} tono="text-emerald-600" onClick={() => alternarDir("subir")} activo={dir === "subir"} pie="clic para filtrar" />
        <Kpi label="Bajar" valor={k(kpi.bajar)} tono="text-sky-700" onClick={() => alternarDir("bajar")} activo={dir === "bajar"} pie="clic para filtrar" />
        <Kpi label="Mantener" valor={k(kpi.mantener)} onClick={() => alternarDir("mantener")} activo={dir === "mantener"} pie={`cambio ≤ ±${pct(umbral, 0)}`} />
        <Kpi label="Δ utilidad / día" valor={hay ? pesosFirmado(Math.round(kpi.dU)) : "—"} tono={kpi.dU >= 0 ? "text-emerald-600" : "text-rose-600"}
             pie={hay ? `esperado · ${cifra(kpi.activas)} activas` : "sólo activas"} ayuda="Suma de (utilidad/día al recomendado − actual) en las activas con propuesta. Las pausadas no venden hasta reponer FULL." />
        <Kpi label="Δ unidades / día" valor={hay ? cifraFirmada(kpi.dUn) : "—"} tono={kpi.dUn >= 0 ? "text-emerald-600" : "text-rose-600"} pie="esperado · activas" />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmentado etiqueta="Estado" valor={estado} onCambio={reiniciar(setEstado)} oscuro opciones={[
          { id: "activa", label: "Activas", n: n(facetas.estado, "activa") },
          { id: "pausada", label: "Pausadas", n: n(facetas.estado, "pausada") },
          { id: "", label: "Todas", n: n(facetas.estado, "") },
        ]} />
        <Segmentado etiqueta="Cuenta" valor={cuenta} onCambio={reiniciar(setCuenta)} opciones={[
          { id: "", label: "Ambas", n: n(facetas.cuenta, "") },
          { id: "BEKURA", label: "Kubera", punto: COLOR_CUENTA.BEKURA, n: n(facetas.cuenta, "BEKURA") },
          { id: "SANCORFASHION", label: "San Corpe", punto: COLOR_CUENTA.SANCORFASHION, n: n(facetas.cuenta, "SANCORFASHION") },
        ]} />
        <Segmentado etiqueta="Confianza de la elasticidad" valor={confianza} onCambio={reiniciar(setConfianza)} opciones={[
          { id: "", label: "Toda confianza" },
          { id: "alta", label: "Alta", n: n(facetas.confianza, "alta") },
          { id: "media", label: "Media", n: n(facetas.confianza, "media") },
          { id: "baja", label: "Baja", n: n(facetas.confianza, "baja") },
        ]} />
        <Selector etiqueta="Razón" valor={razon} onCambio={reiniciar(setRazon)}
                  opciones={[{ id: "", label: "Todas", n: n(facetas.razon, "") }, ...razones.map((r) => ({ id: r, label: RAZON[r]?.label ?? r, n: n(facetas.razon, r) }))]} />
        <Selector etiqueta="Aviso" valor={aviso} onCambio={reiniciar(setAviso)}
                  opciones={[{ id: "", label: "Todos", n: n(facetas.aviso, "") }, ...avisos.map((a) => ({ id: a, label: AVISO_REC[a]?.label ?? a, n: n(facetas.aviso, a) }))]} />
        <Buscador valor={q} onCambio={(v) => { setQ(v); setPage(1); }} />
      </div>

      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}

      <MarcoTabla
        titulo={dir ? `Propuestas · ${dir}` : "Propuestas de precio"}
        derecha={<span className="text-[11px] text-slate-500">Clic en una fila para ver la curva y el plan{estadoLab?.generado_at ? ` · corrida del ${dia(estadoLab.generado_at)}` : ""}</span>}
        pie={hay && visibles.length > 0 && <Paginacion page={page} total={visibles.length} perPage={POR_PAGINA} onPage={setPage} />}>
        {!datos && cargando ? <Cargando texto="Leyendo 1,744 propuestas…" />
          : datos?.sin_datos ? <Vacio texto="Todavía no hay propuestas: el pipeline no ha corrido." />
          : !datos ? <Vacio texto="Sin datos." />
          : visibles.length === 0 ? <Vacio texto="Ninguna propuesta con esos filtros." /> : (
          <table className="tabla-lab min-w-[1680px]">
            <thead>
              <tr>
                <Th className="pl-4" campo="sku" orden={orden} onOrden={setOrden}>SKU</Th>
                <Th>Publicación</Th>
                <Th campo="cambio_pct" orden={orden} onOrden={setOrden} alinear="der" ayuda="Precio de referencia → precio recomendado (punto óptimo) y el cambio. Activas: contra el precio de hoy. Pausadas: contra lo que pagaban en su último periodo con oferta (su precio publicado suele estar muy arriba de eso).">Actual → recomendado</Th>
                <Th alinear="der" ayuda={`Precio con margen = piso (${pct(piso, 0)}). Abajo de aquí no se recomienda.`}>Piso</Th>
                <Th alinear="der" ayuda="Precio con utilidad = 0.">Equilibrio</Th>
                <Th alinear="der" ayuda="Precio que maximiza la utilidad por día.">Máx. utilidad</Th>
                <Th alinear="der" ayuda="Mediana de búsqueda filtrada > sugerido de ML > mediana de más vendidos de la categoría. «no confiable» = no acotó la propuesta.">Ref. competencia</Th>
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
                      <div className="flex min-w-[240px] max-w-[320px] items-center gap-2.5">
                        <Miniatura src={r.thumbnail} alt="" />
                        <div className="min-w-0">
                          <div className="truncate text-[12.5px] text-slate-800" title={r.titulo ?? ""}>{r.titulo ?? "—"}</div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                            <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} />
                            <ChipEstado estado={r.estado} />
                            <ChipCoordinado razones={r.razones} />
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="num"><Cambio r={r} umbral={umbral} /></td>
                    <td className="num text-slate-600">{pesos(r.precio_piso)}</td>
                    <td className="num text-slate-600">{pesos(r.precio_equilibrio)}</td>
                    <td className="num text-slate-600">{pesos(r.precio_max_utilidad)}</td>
                    <td className="num">
                      {r.precio_ref_competencia != null ? (
                        <>
                          <div className="text-slate-800">{pesos(r.precio_ref_competencia)}</div>
                          <div className="text-[10.5px] text-slate-500">{FUENTE_REF[r.fuente_ref ?? ""] ?? r.fuente_ref ?? ""}{r.ref_confiable === false ? " · no confiable" : ""}</div>
                        </>
                      ) : <SinDato texto="sin ref." />}
                    </td>
                    <td className="num"><ParCifras a={r.unidades_dia.actual} b={r.unidades_dia.recomendado} fmt={(v) => cifra(v)} /></td>
                    <td className="num"><ParCifras a={r.visitas_dia.actual} b={r.visitas_dia.recomendado} fmt={(v) => cifra(v)} /></td>
                    <td className="num"><ParCifras a={r.conversion.actual} b={r.conversion.recomendado} fmt={(v) => pct(v, 1)} /></td>
                    <td className="num">
                      <span className={`${tonoMargen(r.margen.actual, piso)} tabular-nums`}>{pct(r.margen.actual)}</span>
                      <span className="mx-1 text-slate-300" aria-hidden>→</span>
                      <b className={`${tonoMargen(r.margen.recomendado, piso)} tabular-nums`}>{pct(r.margen.recomendado)}</b>
                    </td>
                    <td className={`num font-semibold ${dU == null ? "text-slate-500" : dU >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{pesosFirmado(dU)}</td>
                    <td className="num">
                      <div className="flex items-center justify-end gap-1.5">
                        <span className="tabular-nums text-slate-800">{r.elasticidad.beta == null ? "—" : r.elasticidad.beta.toFixed(2)}</span>
                        <ChipConfianza confianza={r.elasticidad.confianza} />
                      </div>
                    </td>
                    <td>
                      <FilaRazones r={r} piso={piso} />
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-slate-300 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-slate-600"
                            title="El laboratorio sólo propone. Aplicar un precio requiere autorización y se hace fuera de aquí.">
                        <Clock size={10} aria-hidden /> {r.autorizacion === "pendiente" ? "Pendiente" : r.autorizacion}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </MarcoTabla>

      <CajonPrecio rec={abierta} onCerrar={() => setAbierta(null)} piso={piso} umbral={umbral} />
    </>
  );
}

/**
 * Pasos del plan: «Hoy» + cada fecha. `al_reactivar` no tiene fecha real: se fija
 * al volver a publicar, y su cambio se mide contra lo que pagaban (`cambio_ref`).
 * `renovar_promo`: cada paso es una promoción sobre el precio regular.
 */
function TablaPasos({ r, pasos }: { r: Recomendacion; pasos: PasoPlan[] }) {
  const vsRealizado = r.cambio_ref === "precio_realizado_base" && r.base?.p_base != null;
  return (
    <div className="mt-2 max-h-72 overflow-auto">
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-slate-500">
            <th scope="col" className="py-1.5 text-left font-semibold">Cuándo</th>
            <th scope="col" className="py-1.5 text-right font-semibold">Precio</th>
            <th scope="col" className="py-1.5 text-right font-semibold">Cambio</th>
            <th scope="col" className="py-1.5 pl-4 text-left font-semibold">Nota</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-t border-slate-100 text-slate-500">
            <td className="py-1.5">Hoy</td><td className="py-1.5 text-right tabular-nums">{pesos(r.precio_actual)}</td><td /><td className="py-1.5 pl-4">{r.estado === "pausada" ? "precio publicado (pausada)" : "precio actual"}</td>
          </tr>
          {vsRealizado && (
            <tr className="border-t border-slate-100 text-slate-500">
              <td className="py-1.5">Base</td><td className="py-1.5 text-right tabular-nums">{pesos(r.base?.p_base)}</td><td />
              <td className="py-1.5 pl-4">lo que pagaban{r.base?.desde ? ` (${dia(r.base.desde)} – ${dia(r.base.hasta)})` : ""}: el cambio se mide contra esto</td>
            </tr>
          )}
          {pasos.map((p, i) => {
            const antes = i === 0 ? (vsRealizado ? r.base!.p_base : r.precio_actual) : pasos[i - 1].precio;
            return (
              <tr key={`${p.fecha}-${i}`} className="border-t border-slate-100">
                <td className="py-1.5 text-slate-700">{p.tipo === "al_reactivar" ? "Al reactivar" : dia(p.fecha)}</td>
                <td className="py-1.5 text-right font-semibold tabular-nums text-slate-900">{pesos(p.precio)}</td>
                <td className="py-1.5 text-right tabular-nums text-slate-500">{antes ? pctFirmado((p.precio - antes) / antes) : "—"}</td>
                <td className="py-1.5 pl-4 text-[11.5px] text-slate-500">
                  <span className="inline-flex flex-wrap items-center gap-1.5">
                    {p.tipo === "prueba" && <Chip tono="amber">prueba</Chip>}
                    {p.precio_regular != null && <Chip tono="indigo" titulo="El paso es una promoción a este precio sobre el precio regular.">promo · regular {pesos(p.precio_regular)}</Chip>}
                    {p.excede_tope && <Chip tono="amber" titulo="El tope (2 % diario / 5 % semanal) no alcanza un peso a este precio: el paso mínimo es $1.">paso mínimo $1</Chip>}
                    {p.nota ?? ""}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CajonPrecio({ rec, onCerrar, piso, umbral }: { rec: Recomendacion | null; onCerrar: () => void; piso: number; umbral: number }) {
  const { datos: curva, error, cargando } = usePedido<Curva>(rec ? `/curva/${encodeURIComponent(rec.id)}` : null, undefined, { nulo404: true });
  const [modo, setModo] = useState<"semanal" | "diario">("semanal");
  if (!rec) return null;
  const r = rec;
  const dU = r.utilidad_dia.recomendado != null && r.utilidad_dia.actual != null ? r.utilidad_dia.recomendado - r.utilidad_dia.actual : null;
  const dUn = r.unidades_dia.recomendado != null && r.unidades_dia.actual != null ? r.unidades_dia.recomendado - r.unidades_dia.actual : null;
  const tieneDiario = !!r.plan?.diario?.pasos?.length;
  const pasos = (modo === "diario" && tieneDiario ? r.plan?.diario?.pasos : r.plan?.pasos) ?? [];
  const claveModo = r.plan?.modo === "al_reactivar" || r.plan?.modo === "renovar_promo" ? r.plan.modo : (modo === "diario" && tieneDiario ? "diario" : r.plan?.modo);
  const modoPlan = claveModo ? PLAN_MODO[claveModo]?.label ?? claveModo : "—";
  const ritmo = modo === "diario" && tieneDiario ? "diario" : "semanal";
  const riesgo = tieneAviso(r.avisos, "riesgo_si_se_cobra_mercancia");
  const coord = (r.avisos ?? []).map(leerAviso).find((a) => a.clave === "coordinado_otra_cuenta" || a.clave === "brecha_entre_cuentas");
  const guardarrailes = r.recomendado_sin_guardarrailes ?? (r.recomendado_modelo != null && r.recomendado_modelo !== r.precio_recomendado ? r.recomendado_modelo : null);

  return (
    <Cajon abierto={!!rec} onCerrar={onCerrar} ancho="max-w-3xl"
           titulo={<span className="flex items-start gap-3"><Miniatura src={r.thumbnail} alt="" tam={44} /><span>{r.titulo ?? r.sku ?? r.id}</span></span>}
           subtitulo={
             <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
               <span className="font-mono text-slate-700">{r.sku}</span>
               <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} />
               {urlSegura(r.url) ? <a href={urlSegura(r.url)!} target="_blank" rel="noopener noreferrer" className="font-mono text-indigo-600 hover:underline">{r.listing_id}</a> : <span className="font-mono">{r.listing_id}</span>}
               <ChipEstado estado={r.estado} />
               <ChipCoordinado razones={r.razones} />
             </span>
           }>
      <div className="space-y-4">
        <Franja icono={<ShieldCheck size={15} className="text-indigo-600" />}>
          <b className="font-semibold">Propuesta. Ningún precio se aplica sin autorización.</b>
        </Franja>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-[minmax(0,1.7fr)_repeat(3,minmax(0,1fr))]">
          <div className="col-span-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 sm:col-span-1">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Precio</div>
            <div className="mt-1 flex justify-start text-[15px]"><Cambio r={r} umbral={umbral} alinear="izq" /></div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Δ utilidad / día</div>
            <div className={`mt-1 text-lg font-bold tabular-nums ${dU == null ? "text-slate-500" : dU >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{pesosFirmado(dU)}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Δ unidades / día</div>
            <div className={`mt-1 text-lg font-bold tabular-nums ${dUn == null ? "text-slate-500" : dUn >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{cifraFirmada(dUn)}</div>
          </div>
          <div className="col-span-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 sm:col-span-1">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Margen</div>
            <div className="mt-1 whitespace-nowrap text-base font-bold tabular-nums">
              <span className={tonoMargen(r.margen.actual, piso)}>{pct(r.margen.actual)}</span>
              <span className="mx-1 text-slate-300" aria-hidden>→</span>
              <span className={tonoMargen(r.margen.recomendado, piso)}>{pct(r.margen.recomendado)}</span>
            </div>
          </div>
        </div>

        {r.estado === "pausada" && (
          <Franja tono="amber">
            Está <b>pausada</b>: no vende hasta reponer stock en FULL. Las cifras «actuales» son de su base
            {r.base?.desde ? ` (${dia(r.base.desde)} – ${dia(r.base.hasta)})` : ""}, no de hoy.
            {r.cambio_ref === "precio_realizado_base" && r.precio_recomendado != null && (
              <> El cambio se mide contra lo que pagaban entonces (<b className="tabular-nums">{pesos(r.base?.p_base)}</b>);
                contra el precio publicado hoy (<span className="tabular-nums">{pesos(r.precio_actual)}</span>) sería <b className="whitespace-nowrap tabular-nums">{pctFirmado(r.cambio_vs_actual)}</b>.</>
            )}
          </Franja>
        )}

        {riesgo && (
          <Franja tono="amber" icono={<AlertTriangle size={15} className="text-amber-600" />}>
            <b className="font-semibold">Riesgo si se cobra la mercancía.</b>{" "}
            Con el costo del panel ({pesos(r.costo?.costo_panel)}: mercancía + flete) el margen al recomendado sería{" "}
            <b className="whitespace-nowrap tabular-nums">{pct(r.costo?.margen_costo_panel_recomendado)}</b>, bajo el piso de {pct(piso, 0)}.
            Hoy no aplica: el costo es el prorrateo de $525,000 por contenedor (regla de Brandon), así que la bajada no se bloquea.
          </Franja>
        )}

        {coord && (
          <Franja icono={<ArrowLeftRight size={15} className="text-teal-700" />}>
            <b className="font-semibold">{coord.clave === "coordinado_otra_cuenta" ? "Coordinado con la otra cuenta." : "Brecha entre cuentas acotada."}</b>{" "}
            <span className="text-indigo-900/80">{coord.detalle}</span>
          </Franja>
        )}

        <Tarjeta className="p-4">
          <Ceja>Curva de precio</Ceja>
          <div className="mt-2">
            {cargando ? <Cargando texto="Leyendo la curva…" /> : error ? <CajaError mensaje={error} />
              : curva ? <CurvaPrecio curva={{ ...curva, rejilla: curva.rejilla ?? r.rejilla }} />
              : <p className="text-sm text-slate-500">Sin curva para esta publicación.</p>}
          </div>
        </Tarjeta>

        <Tarjeta className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Ceja>Plan de ajuste · {modoPlan}{r.plan?.modo === "renovar_promo" ? ` · ${ritmo}` : ""}</Ceja>
            <div className="flex items-center gap-2">
              {tieneDiario && r.plan?.modo !== "al_reactivar" && (
                <Segmentado etiqueta="Ritmo del plan" valor={modo} onCambio={setModo} opciones={[
                  { id: "semanal", label: "Semanal", n: r.plan?.pasos.length ?? 0 },
                  { id: "diario", label: "Diario", n: r.plan?.diario?.pasos.length ?? 0 },
                ]} />
              )}
              <Chip tono="slate" titulo="Propuesta, no ejecución: cada paso requiere autorización."><Clock size={10} aria-hidden /> Pendiente</Chip>
            </div>
          </div>
          {r.plan?.modo === "renovar_promo" && (
            <div className="mt-2">
              <Franja icono={<Tag size={14} className="text-indigo-600" />}>
                La promoción vence el <b>{dia(r.plan.promo_fin)}</b>. El plan empieza ese día como una <b>promoción nueva</b> sobre
                el precio regular de <b className="tabular-nums">{pesos(r.plan.precio_regular ?? r.precio_regular)}</b>; si no se renueva, el precio sube al regular.
              </Franja>
            </div>
          )}
          {r.plan?.nota && <p className="mt-2 text-[12px] text-slate-500">{r.plan.nota}</p>}
          {pasos.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">{r.precio_recomendado == null ? "Sin propuesta: no hay plan." : "El precio actual ya está en el punto óptimo: no hay pasos."}</p>
          ) : <TablaPasos r={r} pasos={pasos} />}
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
            <p className="mt-2 text-[11px] leading-snug text-slate-500">β = −1.6 quiere decir: 10 % más barato → ~16 % más unidades.</p>
          </Tarjeta>
          <Tarjeta className="p-4">
            <Ceja>Base, costo y stock</Ceja>
            <div className="mt-1">
              {r.base && (
                <Renglon etiqueta="Base" ayuda="Los días NO censurados (con oferta y stock) que definen unidades y visitas actuales."
                         valor={`${cifra(r.base.dias)} d · ${cifra(r.base.unidades)} u a ${pesos(r.base.p_base)}`} />
              )}
              <Renglon etiqueta="Costo (sin IVA)" ayuda="Prorrateo de $525,000 por contenedor según el m³ de la pieza. La mercancía no se incluye (regla de Brandon)."
                       valor={r.costo?.unitario != null ? `${pesos(r.costo.unitario)} · ${fuenteCosto(r.costo.fuente).label}` : <SinDato texto={r.costo?.fuente === "sospechoso" ? "descartado" : "sin costo"} />} />
              {r.costo?.costo_panel != null && (
                <Renglon etiqueta="Costo del panel" ayuda="Mercancía + flete, como lo calcula el panel hoy. Solo informativo: no entra en el cálculo." valor={<span className="text-slate-500">{pesos(r.costo.costo_panel)}</span>} />
              )}
              <Renglon etiqueta="Peso facturable" valor={r.peso?.kg != null ? `${cifra(r.peso.kg)} kg` : <SinDato texto="sin peso" />} />
              <Renglon etiqueta="Stock FULL" valor={cifra(r.stock.full)} />
              <Renglon etiqueta="Libre en Odoo" valor={r.stock.odoo == null ? <SinDato /> : cifra(r.stock.odoo)} />
              <Renglon etiqueta="Cobertura" valor={r.stock.cobertura_dias == null ? "—" : `${cifra(r.stock.cobertura_dias)} días`}
                       tono={r.stock.cobertura_dias != null && r.stock.cobertura_dias < 10 ? "text-rose-600" : undefined} />
              <Renglon etiqueta="Precio máx. volumen" valor={pesos(r.precio_max_volumen)} ayuda="El menor precio de la curva con margen ≥ piso." />
              {guardarrailes != null && guardarrailes !== r.precio_recomendado && (
                <Renglon etiqueta="Sin guardarraíles" ayuda="Lo que proponía el modelo antes de los guardarraíles (evidencia, stock, cuentas, promoción)."
                         valor={<span className="text-slate-500">{pesos(guardarrailes)}</span>} />
              )}
            </div>
          </Tarjeta>
        </div>

        {(r.razones.length > 0 || (r.avisos?.length ?? 0) > 0) && (
          <Tarjeta className="p-4">
            <Ceja>Por qué</Ceja>
            <ul className="mt-2 space-y-1.5">
              {r.razones.map((z) => (
                <li key={z} className="flex items-start gap-2 text-[12.5px] text-slate-600">
                  <ChipRazon razon={z} /><span className="pt-0.5">{RAZON[z]?.ayuda ?? ""}</span>
                </li>
              ))}
              {avisosOrdenados(r.avisos).map((a, i) => (
                <li key={`${a.clave}-${i}`} className={`flex items-start gap-2 text-[12.5px] ${a.informativo ? "text-slate-500" : "text-slate-600"}`}>
                  <span className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${CLASES_TONO[a.tono]}`}>
                    {a.informativo ? "dato" : "aviso"}
                  </span>
                  <span className="min-w-0 pt-0.5">
                    <span className="font-medium text-slate-700">{a.label}</span>
                    {a.detalle && <span className="block text-[11.5px] leading-snug text-slate-500">{a.detalle}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </Tarjeta>
        )}
      </div>
    </Cajon>
  );
}
