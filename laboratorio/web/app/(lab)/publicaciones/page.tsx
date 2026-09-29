"use client";

/**
 * Publicaciones: TODAS las de ML (BEKURA y SANCORFASHION), Amazon, Walmart, Temu
 * y TikTok, con su economía por unidad al precio que se cobra hoy (8,840 el 28-sep).
 *
 * La tabla se pagina en el SERVIDOR (`/publicaciones?canal&cuenta&estado&full&fuente_costo&q&orden&page&per_page`).
 * Los conteos de las tarjetas salen de `conteos` y los de cada filtro de `facetas`
 * (api.py cuenta cada faceta ignorando su propio filtro: el número dice cuántas
 * filas verías al elegirla).
 *
 * «Recomendado»: sólo ML FULL, viene de precios.json. Es PROPUESTA: todas las
 * filas salen con autorización pendiente.
 *
 * «supuesto»: en Amazon, Walmart, Temu y TikTok la comisión y el envío salen de
 * `parametros.canales` sin validar — la columna lo dice en cada fila.
 */
import { useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Encabezado } from "@/components/Marco";
import {
  Buscador, CajaError, Cargando, MarcoTabla, Miniatura, Paginacion, PuntoCuenta, Segmentado, Selector, SinDato, Th, useRetrasado, Vacio,
} from "@/components/ui";
import { ChipCambio, ChipCoordinado, ChipEstado, ChipFuenteCosto, ChipFull, ChipRiesgoMercancia, ChipSupuesto, TagValidado } from "@/components/Chips";
import CajonPublicacion from "@/components/CajonPublicacion";
import { aPagina } from "@/lib/api";
import { cifra, entero, pct, pctFirmado, pesos, tonoMargen, urlSegura } from "@/lib/formato";
import { COLOR_CUENTA, TEMA_CANAL } from "@/lib/tema";
import { FUENTE_REF, RAZON } from "@/lib/vocabulario";
import type { Canal, Metricas, Publicacion } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";
import { useUmbralMantener } from "@/lib/useParametros";

interface Vista { id: string; canal: Canal | ""; cuenta: string; nombre: string; sub: string; punto: string }

const VISTAS: Vista[] = [
  { id: "todas", canal: "", cuenta: "", nombre: "Todas", sub: "5 canales · 6 cuentas", punto: "#4F46E5" },
  { id: "ml-bekura", canal: "mercado_libre", cuenta: "BEKURA", nombre: "Kubera", sub: "ML · BEKURA", punto: COLOR_CUENTA.BEKURA },
  { id: "ml-sancor", canal: "mercado_libre", cuenta: "SANCORFASHION", nombre: "San Corpe", sub: "ML · SANCORFASHION", punto: COLOR_CUENTA.SANCORFASHION },
  { id: "amazon", canal: "amazon", cuenta: "", nombre: "Amazon", sub: "San Corpe", punto: TEMA_CANAL.amazon.color },
  { id: "walmart", canal: "walmart", cuenta: "", nombre: "Walmart", sub: "MX", punto: TEMA_CANAL.walmart.color },
  { id: "tiktok", canal: "tiktok", cuenta: "", nombre: "TikTok Shop", sub: "Kubera", punto: TEMA_CANAL.tiktok.color },
  { id: "temu", canal: "temu", cuenta: "", nombre: "Temu", sub: "Kubera", punto: TEMA_CANAL.temu.color },
];

const ESTADOS = [
  { id: "", label: "Todos" }, { id: "activa", label: "Activas" }, { id: "pausada", label: "Pausadas" },
  { id: "en_revision", label: "En revisión" }, { id: "cerrada", label: "Cerradas" }, { id: "inactiva", label: "Inactivas" },
  { id: "otra", label: "Otras" },
];

const COSTOS = [
  { id: "", label: "Todos" },
  { id: "packing_list_exacto,prorrateo_kubera,tarifa_7500", label: "Con costo" },
  { id: "packing_list_exacto", label: "Exacto (packing list)" },
  { id: "prorrateo_kubera", label: "Prorrateo 525k" },
  { id: "tarifa_7500", label: "Tarifa 7,500/m³" },
  { id: "sin_costo", label: "Sin costo" },
  { id: "sospechoso", label: "Descartado (m³ imposible)" },
];

const POR_PAGINA = 50;

/**
 * api.py manda `conteos` con llaves «canal» y «canal:cuenta» (ignoran el filtro de
 * canal/cuenta pero respetan los demás). Se suman sólo las llaves de un nivel para
 * no contar dos veces. Sin conteos, se cae a `/metricas` (ML por cuenta; el resto
 * por canal SIN `mercado_libre`, que ya está en las cuentas).
 */
function conteoDe(v: Vista, conteos: Record<string, number> | undefined, m: Metricas | null): number | null {
  if (conteos && Object.keys(conteos).length) {
    const canales = Object.entries(conteos).filter(([k]) => !k.includes(":"));
    const pares = Object.entries(conteos).filter(([k]) => k.includes(":"));
    if (!v.canal) return (canales.length ? canales : pares).reduce((a, [, n]) => a + n, 0);
    if (v.cuenta) return conteos[`${v.canal}:${v.cuenta}`] ?? 0;
    return conteos[v.canal] ?? pares.filter(([k]) => k.startsWith(`${v.canal}:`)).reduce((a, [, n]) => a + n, 0);
  }
  if (!m) return null;
  if (v.canal === "mercado_libre") return m.por_cuenta?.[v.cuenta]?.publicaciones ?? null;
  if (v.canal) return m.por_canal?.[v.canal]?.publicaciones ?? null;
  const ml = Object.values(m.por_cuenta ?? {}).reduce((a, x) => a + (x.publicaciones ?? 0), 0);
  const otros = Object.entries(m.por_canal ?? {}).filter(([c]) => c !== "mercado_libre").reduce((a, [, x]) => a + (x.publicaciones ?? 0), 0);
  return ml + otros || null;
}

function activasDe(v: Vista, m: Metricas | null): number | null {
  if (!m) return null;
  if (v.canal === "mercado_libre") return m.por_cuenta?.[v.cuenta]?.activas ?? null;
  if (v.canal) return m.por_canal?.[v.canal]?.activas ?? null;
  const ml = Object.values(m.por_cuenta ?? {}).reduce((a, x) => a + (x.activas ?? 0), 0);
  const otros = Object.entries(m.por_canal ?? {}).filter(([c]) => c !== "mercado_libre").reduce((a, [, x]) => a + (x.activas ?? 0), 0);
  return ml + otros;
}

/** Celda «Recomendado»: precio + flecha y %; sin propuesta dice por qué. */
function CeldaRecomendado({ p, umbral }: { p: Publicacion; umbral: number }) {
  if (p.precio_recomendado != null) {
    const vsRealizado = p.cambio_ref === "precio_realizado_base";
    return (
      <div className="flex flex-col items-end gap-0.5"
           title={`Propuesta del laboratorio: pendiente de autorización.${vsRealizado ? ` Pausada: el cambio es contra lo que pagaban en su último periodo con oferta; contra el precio publicado hoy sería ${pctFirmado(p.cambio_vs_actual)}.` : ""}`}>
        <b className="font-semibold text-slate-900">{pesos(p.precio_recomendado)}</b>
        <ChipCambio cambio={p.cambio_pct} umbral={umbral} />
        {vsRealizado && <span className="whitespace-nowrap text-[10px] text-slate-500">vs lo que pagaban</span>}
        <ChipCoordinado razones={p.razones} />
      </div>
    );
  }
  if (p.canal === "mercado_libre" && p.es_full) {
    const por = (p.razones ?? []).map((r) => RAZON[r]?.label ?? r).join(", ");
    return <SinDato texto="sin propuesta" titulo={por ? `Sin propuesta: ${por}.` : "El optimizador no propuso precio."} />;
  }
  return <span className="text-slate-300" title="El optimizador sólo evalúa Mercado Libre FULL.">—</span>;
}

export default function PaginaPublicaciones() {
  const [vista, setVista] = useState("todas");
  const [estado, setEstado] = useState("");
  const [full, setFull] = useState("");
  const [costo, setCosto] = useState("");
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState("-unidades_30d");
  const [page, setPage] = useState(1);
  const [abierta, setAbierta] = useState<Publicacion | null>(null);
  const qq = useRetrasado(q);
  const umbral = useUmbralMantener();
  const v = VISTAS.find((x) => x.id === vista) ?? VISTAS[0];

  const params = useMemo(() => ({
    canal: v.canal, cuenta: v.cuenta, estado, full, fuente_costo: costo, q: qq.trim(), orden, page, per_page: POR_PAGINA,
  }), [v.canal, v.cuenta, estado, full, costo, qq, orden, page]);
  const { datos, error, cargando, recargar } = usePedido<unknown>("/publicaciones", params);
  const { datos: metricas } = usePedido<Metricas>("/metricas");
  const pagina = datos ? aPagina<Publicacion>(datos) : null;
  // Se conservan los últimos conteos mientras llega la página siguiente (el marco no parpadea).
  const [conteos, setConteos] = useState<Record<string, number> | undefined>(undefined);
  const [facetas, setFacetas] = useState<Record<string, Record<string, number>> | undefined>(undefined);
  if (pagina?.conteos && pagina.conteos !== conteos) setConteos(pagina.conteos);
  if (pagina?.facetas && pagina.facetas !== facetas) setFacetas(pagina.facetas);

  const reiniciar = <T,>(set: (x: T) => void) => (x: T) => { set(x); setPage(1); };
  const muestraML = !v.canal || v.canal === "mercado_libre";

  const fEstado = facetas?.estado;
  const nEstado = (id: string) => (!fEstado ? null : id ? fEstado[id] ?? 0 : Object.values(fEstado).reduce((a, n) => a + n, 0));
  const opcionesEstado = ESTADOS
    .filter((o) => !o.id || o.id === estado || !fEstado || (fEstado[o.id] ?? 0) > 0)
    .map((o) => ({ ...o, n: nEstado(o.id) }));
  const fFull = facetas?.full;
  const fCosto = facetas?.fuente_costo;
  const nCosto = (id: string) => {
    if (!fCosto) return null;
    const ids = id ? id.split(",") : Object.keys(fCosto);
    return ids.reduce((a, k) => a + (fCosto[k] ?? 0), 0);
  };

  return (
    <>
      <Encabezado titulo="Publicaciones"
                  descripcion="Cada publicación con lo que cobra hoy, lo que cuesta (contenedor de $525,000 prorrateado) y lo que deja. En ML FULL, junto al precio, la propuesta del laboratorio. Clic en una fila para el desglose, la competencia y su historia de precio." />

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
        {VISTAS.map((x) => {
          const act = x.id === vista;
          const n = conteoDe(x, conteos, metricas);
          const act2 = activasDe(x, metricas);
          return (
            <button key={x.id} type="button" onClick={() => { setVista(x.id); setPage(1); if (x.canal && x.canal !== "mercado_libre") setFull(""); }}
                    aria-pressed={act}
                    className={`min-w-0 rounded-2xl border p-3 text-left transition-colors ${act ? "border-indigo-600 bg-indigo-50" : "border-slate-200 bg-white hover:border-indigo-300"}`}>
              <div className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: x.punto }} aria-hidden />
                <span className="truncate text-[13px] font-semibold text-slate-800">{x.nombre}</span>
              </div>
              <div className="mt-1 text-xl font-bold tabular-nums text-slate-900">{n === null ? "—" : entero(n)}</div>
              <div className="text-[11px] tabular-nums text-slate-600">{act2 !== null ? `${entero(act2)} activas` : " "}</div>
              <div className="truncate text-[10.5px] text-slate-500" title={x.sub}>{x.sub}</div>
            </button>
          );
        })}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmentado etiqueta="Estado" valor={estado} onCambio={reiniciar(setEstado)} opciones={opcionesEstado} />
        {muestraML && (
          <Segmentado etiqueta="Logística FULL" valor={full} onCambio={reiniciar(setFull)} oscuro opciones={[
            { id: "", label: "FULL y no FULL", n: fFull ? (fFull.true ?? 0) + (fFull.false ?? 0) : null },
            { id: "1", label: "Solo FULL", n: fFull ? fFull.true ?? 0 : null },
            { id: "0", label: "Sin FULL", n: fFull ? fFull.false ?? 0 : null },
          ]} />
        )}
        <Selector etiqueta="Costo" valor={costo} onCambio={reiniciar(setCosto)}
                  opciones={COSTOS.map((o) => ({ ...o, n: nCosto(o.id) }))} />
        <Buscador valor={q} onCambio={(x) => { setQ(x); setPage(1); }} placeholder="SKU, título o ID" />
      </div>

      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}

      <MarcoTabla
        titulo={<span>{v.id === "todas" ? "Todas las publicaciones" : `${v.nombre} · ${v.sub}`}</span>}
        derecha={<span className="text-[11px] text-slate-500">Montos con IVA · costo sin IVA</span>}
        pie={pagina && pagina.total > 0 && <Paginacion page={page} total={pagina.total} perPage={pagina.per_page || POR_PAGINA} onPage={setPage} cargando={cargando} />}>
        {!pagina && cargando ? <Cargando texto="Leyendo publicaciones…" />
          : pagina?.sin_datos ? <Vacio texto="Todavía no hay publicaciones: el pipeline no ha corrido." />
          : pagina && pagina.filas.length === 0 ? <Vacio texto="Ninguna publicación con esos filtros." />
          : pagina && (
          <table className={`tabla-lab min-w-[1620px] transition-opacity ${cargando ? "opacity-60" : ""}`}>
            <thead>
              <tr>
                <Th className="pl-4" campo="sku" orden={orden} onOrden={setOrden}>SKU</Th>
                <Th>Publicación</Th>
                <Th>Cuenta</Th>
                <Th>Estado</Th>
                <Th ayuda="FULL = sale del almacén de Mercado Libre.">Logística</Th>
                <Th campo="precio_cobrado" orden={orden} onOrden={setOrden} alinear="der" ayuda="Lo que paga el comprador hoy (price_sale). Tachado: el precio de lista.">Precio</Th>
                <Th campo="cambio_pct" orden={orden} onOrden={setOrden} alinear="der" ayuda="Sólo ML FULL: el punto óptimo del laboratorio y cuánto se mueve. Activas: contra el precio de hoy. Pausadas: contra lo que pagaban en su último periodo con oferta. Es una propuesta: ningún precio se aplica sin autorización.">Recomendado</Th>
                <Th campo="costo.unitario" orden={orden} onOrden={setOrden} alinear="der" ayuda="Costo aterrizado SIN IVA: el prorrateo de $525,000 por contenedor (la mercancía no se incluye, regla de Brandon). Fuente: exacto (packing list), prorrateo (525k ÷ m³ reconstruidos), tarifa (7,500/m³), descartado (m³ imposible) o sin costo. ⚠ = quedaría bajo el piso si se cobrara la mercancía.">Costo</Th>
                <Th alinear="der" ayuda="Comisión del canal sobre el precio con IVA.">Comisión</Th>
                <Th alinear="der" ayuda="Envío que paga el vendedor por unidad.">Envío</Th>
                <Th campo="utilidad" orden={orden} onOrden={setOrden} alinear="der" ayuda="Precio − IVA − comisión − envío − costo, por unidad.">Utilidad</Th>
                <Th campo="margen_pct" orden={orden} onOrden={setOrden} alinear="der" ayuda="Utilidad ÷ precio. Rojo: pierde. Ámbar: bajo el piso de 12 %.">Margen</Th>
                <Th campo="stock_odoo" orden={orden} onOrden={setOrden} alinear="der" ayuda="Arriba: stock en FULL. Abajo: libre en Odoo (free_qty: físico menos reservado).">Stock FULL / Odoo</Th>
                <Th campo="visitas_30d" orden={orden} onOrden={setOrden} alinear="der">Visitas 30 d</Th>
                <Th campo="unidades_30d" orden={orden} onOrden={setOrden} alinear="der">Ventas 30 d</Th>
                <Th campo="conversion_30d" orden={orden} onOrden={setOrden} alinear="der">Conv.</Th>
                <Th campo="competencia.mediana" orden={orden} onOrden={setOrden} alinear="der" lado="der" ayuda="Mediana de precios de la competencia y cuántas muestras (n).">Competencia</Th>
              </tr>
            </thead>
            <tbody>
              {pagina.filas.map((p) => (
                <tr key={p.id} className="clicable" tabIndex={0} onClick={() => setAbierta(p)}
                    onKeyDown={(e) => { if (e.key === "Enter") setAbierta(p); }}>
                  <td className="whitespace-nowrap pl-4 font-mono text-[11.5px] font-semibold text-slate-700">{p.sku ?? <SinDato texto="sin sku" />}</td>
                  <td>
                    <div className="flex min-w-[260px] max-w-[340px] items-center gap-2.5">
                      <Miniatura src={p.thumbnail} alt="" />
                      <div className="min-w-0">
                        <div className="line-clamp-2 text-[12.5px] leading-snug text-slate-800" title={p.titulo ?? ""}>{p.titulo ?? "—"}</div>
                        <div className="mt-0.5 flex items-center gap-1 font-mono text-[10.5px] text-slate-500">
                          {p.listing_id}
                          {urlSegura(p.url) && (
                            <a href={urlSegura(p.url)!} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="text-slate-500 hover:text-indigo-600" aria-label={`Abrir ${p.listing_id} en el canal`}>
                              <ExternalLink size={11} />
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td><PuntoCuenta canal={p.canal} cuenta={p.cuenta} /></td>
                  <td><ChipEstado estado={p.estado} sub={p.sub_status} detalle={p.estado_detalle} /></td>
                  <td>{p.es_full ? <ChipFull /> : <span className="text-[11px] text-slate-500">{p.logistica ?? "—"}</span>}</td>
                  <td className="num">
                    <div className="font-semibold text-slate-900">{pesos(p.precio_cobrado)}</div>
                    {p.precio_lista != null && p.precio_cobrado != null && p.precio_lista > p.precio_cobrado + 0.5 && (
                      <div className="text-[10.5px] text-slate-500 line-through">{pesos(p.precio_lista)}</div>
                    )}
                  </td>
                  <td className="num"><CeldaRecomendado p={p} umbral={umbral} /></td>
                  <td className="num">
                    {p.costo?.unitario != null ? <div className="text-slate-800">{pesos(p.costo.unitario)}</div> : null}
                    <div className="mt-0.5 flex items-center justify-end gap-1">
                      {(p.costo?.avisos ?? []).includes("riesgo_si_se_cobra_mercancia") && (
                        <ChipRiesgoMercancia icono costoPanel={p.costo?.riesgo_mercancia?.costo_panel ?? p.costo?.costo_panel}
                                             margen={p.costo?.riesgo_mercancia?.al_recomendado ?? p.costo?.riesgo_mercancia?.al_precio_cobrado}
                                             donde={p.costo?.riesgo_mercancia?.al_recomendado != null ? "al recomendado" : "a hoy"} />
                      )}
                      <ChipFuenteCosto costo={p.costo} />
                      {p.costo?.validado && <TagValidado por={p.costo.revisado_por} />}
                    </div>
                  </td>
                  <td className="num">
                    <div className="text-slate-700">{pesos(p.comision)}</div>
                    <div className="flex items-center justify-end gap-1 text-[10.5px] text-slate-500">{p.supuesto_canal && <ChipSupuesto />}{pct(p.comision_pct)}</div>
                  </td>
                  <td className="num text-slate-700">{pesos(p.envio)}</td>
                  <td className={`num font-semibold ${tonoMargen(p.margen_pct)}`}>{p.utilidad == null ? <SinDato /> : pesos(p.utilidad)}</td>
                  <td className={`num font-semibold ${tonoMargen(p.margen_pct)}`}>{pct(p.margen_pct)}</td>
                  <td className="num">
                    <div className={p.es_full && !p.stock_full ? "text-rose-600" : "text-slate-800"} title="Stock en FULL">{p.es_full ? entero(p.stock_full) : "—"}</div>
                    <div className="text-[10.5px] text-slate-500" title="Libre en Odoo">{p.stock_odoo == null ? "sin dato" : `Odoo ${entero(p.stock_odoo)}`}</div>
                  </td>
                  <td className="num text-slate-700">{entero(p.visitas_30d)}</td>
                  <td className="num font-semibold text-slate-800">{entero(p.unidades_30d)}</td>
                  <td className="num text-slate-700">{pct(p.conversion_30d, 1)}</td>
                  <td className="num">
                    {p.competencia?.mediana != null ? (
                      <>
                        <div className="text-slate-800">{pesos(p.competencia.mediana)}</div>
                        <div className="text-[10.5px] text-slate-500">{p.competencia.n != null ? `n ${cifra(p.competencia.n)} · ` : ""}{FUENTE_REF[p.competencia.fuente] ?? p.competencia.fuente}</div>
                      </>
                    ) : <SinDato texto="sin medir" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </MarcoTabla>

      <CajonPublicacion pub={abierta} onCerrar={() => setAbierta(null)} umbral={umbral} />
    </>
  );
}
