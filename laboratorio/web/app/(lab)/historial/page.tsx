"use client";

/**
 * Historial: la historia de precio de cada publicación FULL de ML — lo que se
 * cobró de verdad (ingreso ÷ unidades), lo que se ofrecía y lo que el
 * laboratorio recomendó en cada snapshot — junto a unidades y visitas.
 *
 * La lista de la izquierda es la de `/precios` (las publicaciones que el
 * optimizador evalúa). Las mini-series de la página visible salen de UNA sola
 * llamada al lote `GET /historial?ids=…&dias=90&campos=…` (≤ 100 ids; ~45 KB con
 * gzip para 100); la gráfica grande pide `/historial/{id}` completo.
 */
import { useEffect, useMemo, useState } from "react";
import { Encabezado } from "@/components/Marco";
import { Buscador, CajaError, Cargando, Ceja, Miniatura, Paginacion, PuntoCuenta, Segmentado, Tarjeta, useRetrasado, Vacio, Renglon } from "@/components/ui";
import { ChipCambio, ChipEstado } from "@/components/Chips";
import { Sparkline } from "@/components/graficas";
import GraficaHistorial from "@/components/GraficaHistorial";
import { aPagina } from "@/lib/api";
import { cifra, dia, entero, pctFirmado, pesos } from "@/lib/formato";
import { COLOR_CUENTA } from "@/lib/tema";
import { RAZON } from "@/lib/vocabulario";
import type { Historial, HistorialLote, Pagina, Publicacion, PuntoHistorial, Recomendacion } from "@/lib/tipos";
import { umbralDe } from "@/lib/useParametros";
import { usePedido } from "@/lib/usePedido";

const POR_PAGINA = 25;
const DIAS_MINI = 90;

function serieDe(h: Historial | Record<string, Historial> | null, id: string): PuntoHistorial[] {
  if (!h) return [];
  if ("serie" in h && Array.isArray((h as Historial).serie)) return (h as Historial).serie;
  return (h as Record<string, Historial>)[id]?.serie ?? [];
}

/** La mini-serie usa el precio ofrecido (continuo); si falta, el cobrado del snapshot o el realizado. */
function valoresMini(serie: Partial<PuntoHistorial>[] | undefined): (number | null)[] {
  return (serie ?? []).map((p) => p.precio_ofrecido ?? p.precio_cobrado ?? p.precio_realizado ?? null);
}

function RenglonLista({ r, activo, onElegir, valores, cargandoMini, umbral }: {
  r: Recomendacion; activo: boolean; onElegir: () => void; valores: (number | null)[] | null; cargandoMini: boolean; umbral: number;
}) {
  return (
    <button type="button" onClick={onElegir} aria-pressed={activo}
            className={`flex w-full items-center gap-3 border-b border-slate-100 px-3 py-2.5 text-left transition-colors focus-visible:bg-indigo-50 focus-visible:outline-none ${activo ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
      <Miniatura src={r.thumbnail} alt="" tam={34} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-mono text-[11.5px] font-semibold text-slate-800">{r.sku ?? "—"}</span>
          <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} conNombre={false} />
          {r.estado !== "activa" && <span className="text-[10px] font-medium text-slate-500">pausada</span>}
        </div>
        <div className="truncate text-[11.5px] text-slate-500" title={r.titulo ?? ""}>{r.titulo ?? r.listing_id}</div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5">
        {valores ? <Sparkline valores={valores} ancho={84} alto={24} marca={r.precio_recomendado} />
          : <span className={`block h-6 w-[84px] rounded ${cargandoMini ? "animate-pulse bg-slate-100" : ""}`} aria-hidden />}
        <div className="flex items-center gap-1 text-[10.5px] tabular-nums text-slate-500">
          {pesos(r.precio_actual)}
          {r.precio_recomendado != null && <ChipCambio cambio={r.cambio_pct} umbral={umbral} />}
        </div>
      </div>
    </button>
  );
}

export default function PaginaHistorial() {
  const { datos, error, cargando, recargar } = usePedido<Pagina<Recomendacion>>("/precios", {}, { todo: true });
  const [q, setQ] = useState("");
  const [cuenta, setCuenta] = useState("");
  const [estado, setEstado] = useState("");
  const [page, setPage] = useState(1);
  const [elegido, setElegido] = useState<string | null>(null);
  const qq = useRetrasado(q, 200).trim().toLowerCase();
  const umbral = umbralDe(datos?.parametros);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setElegido(id);
  }, []);

  const todas = useMemo(() => datos?.filas ?? [], [datos]);
  const porBusqueda = useMemo(() => todas.filter((r) =>
    !qq || (r.sku ?? "").toLowerCase().includes(qq) || (r.titulo ?? "").toLowerCase().includes(qq) || r.listing_id.toLowerCase().includes(qq)), [todas, qq]);
  const filas = useMemo(() => porBusqueda.filter((r) => (!cuenta || r.cuenta === cuenta) && (!estado || r.estado === estado)), [porBusqueda, cuenta, estado]);
  const nCuenta = (c: string) => (datos ? porBusqueda.filter((r) => (!c || r.cuenta === c) && (!estado || r.estado === estado)).length : null);
  const nEstado = (e: string) => (datos ? porBusqueda.filter((r) => (!cuenta || r.cuenta === cuenta) && (!e || r.estado === e)).length : null);

  useEffect(() => {
    if (!elegido && filas.length) setElegido(filas[0].id);
  }, [filas, elegido]);

  const pagina = filas.slice((page - 1) * POR_PAGINA, page * POR_PAGINA);
  const ids = pagina.map((r) => r.id).join(",");
  const { datos: lote, cargando: cargLote } = usePedido<HistorialLote>(ids ? "/historial" : null,
    { ids, dias: DIAS_MINI, campos: "precio_ofrecido,precio_cobrado,precio_realizado" });

  const rec = todas.find((r) => r.id === elegido) ?? null;
  // Un id que no está en /precios (p. ej. una palanca «pierde dinero» que no es FULL): se busca su ficha.
  const listingElegido = elegido && !rec ? elegido.split(":").pop() ?? null : null;
  const { datos: fichaRaw } = usePedido<unknown>(listingElegido && datos ? "/publicaciones" : null, { q: listingElegido ?? "", per_page: 1 });
  const ficha = !rec && fichaRaw ? aPagina<Publicacion>(fichaRaw).filas.find((p) => p.id === elegido) ?? null : null;

  const { datos: hist, error: errH, cargando: cargH } = usePedido<Historial>(elegido ? `/historial/${encodeURIComponent(elegido)}` : null, undefined, { nulo404: true });
  const serie = elegido ? serieDe(hist, elegido) : [];

  const resumen = useMemo(() => {
    if (!serie.length) return null;
    const ult30 = serie.slice(-30);
    const prev30 = serie.slice(-60, -30);
    const suma = (xs: PuntoHistorial[], k: "unidades" | "visitas") => xs.reduce((a, p) => a + (p[k] ?? 0), 0);
    const ingreso = ult30.reduce((a, p) => a + (p.precio_realizado ?? 0) * (p.unidades ?? 0), 0);
    const u30 = suma(ult30, "unidades");
    const u60 = suma(prev30, "unidades");
    const precios = serie.map((p) => p.precio_ofrecido).filter((x): x is number => x != null);
    const sinOferta = ult30.filter((p) => p.sin_oferta).length;
    return {
      u30, v30: suma(ult30, "visitas"), precioMedio: u30 ? ingreso / u30 : null,
      cambioU: u60 ? (u30 - u60) / u60 : null, sinOferta,
      min: precios.length ? Math.min(...precios) : null, max: precios.length ? Math.max(...precios) : null,
      desde: serie[0].fecha, hasta: serie[serie.length - 1].fecha,
    };
  }, [serie]);

  const titulo = rec?.titulo ?? ficha?.titulo ?? "";
  const sku = rec?.sku ?? ficha?.sku ?? elegido;
  const cuentaSel = rec?.cuenta ?? ficha?.cuenta ?? null;

  return (
    <>
      <Encabezado titulo="Historial"
                  descripcion="150 días de precio realizado, precio ofrecido y el recomendado de cada snapshot del laboratorio, con las unidades y visitas de cada día. Las mini-gráficas muestran el precio ofrecido de los últimos 90 días; la línea punteada naranja es el recomendado." />
      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
        <Tarjeta className="flex min-w-0 flex-col overflow-hidden">
          <div className="space-y-2 border-b border-slate-100 p-3">
            <Buscador valor={q} onCambio={(x) => { setQ(x); setPage(1); }} placeholder="Buscar SKU, título o ID" />
            <div className="flex flex-wrap gap-2">
              <Segmentado etiqueta="Cuenta" valor={cuenta} onCambio={(x) => { setCuenta(x); setPage(1); }} opciones={[
                { id: "", label: "Ambas", n: nCuenta("") },
                { id: "BEKURA", label: "Kubera", punto: COLOR_CUENTA.BEKURA, n: nCuenta("BEKURA") },
                { id: "SANCORFASHION", label: "San Corpe", punto: COLOR_CUENTA.SANCORFASHION, n: nCuenta("SANCORFASHION") },
              ]} />
              <Segmentado etiqueta="Estado" valor={estado} onCambio={(x) => { setEstado(x); setPage(1); }} oscuro opciones={[
                { id: "", label: "Todas", n: nEstado("") },
                { id: "activa", label: "Activas", n: nEstado("activa") },
                { id: "pausada", label: "Pausadas", n: nEstado("pausada") },
              ]} />
            </div>
          </div>
          <div className="min-h-0 flex-1 lg:max-h-[calc(100vh-320px)] lg:overflow-y-auto">
            {!datos && cargando ? <Cargando /> : pagina.length === 0 ? <Vacio texto="Sin publicaciones con esa búsqueda." /> : (
              pagina.map((r) => (
                <RenglonLista key={r.id} r={r} activo={r.id === elegido} umbral={umbral}
                              valores={lote?.series[r.id] ? valoresMini(lote.series[r.id].serie) : cargLote || !lote ? null : []}
                              cargandoMini={cargLote}
                              onElegir={() => {
                                setElegido(r.id);
                                if (window.innerWidth < 1024) document.getElementById("grafica-historial")?.scrollIntoView({ behavior: "smooth", block: "start" });
                              }} />
              ))
            )}
          </div>
          {datos && filas.length > 0 && <Paginacion page={page} total={filas.length} perPage={POR_PAGINA} onPage={setPage} cargando={cargLote} />}
        </Tarjeta>

        <div id="grafica-historial" className="min-w-0 scroll-mt-28 space-y-4">
          <Tarjeta className="p-4 sm:p-5">
            {!elegido ? <Vacio texto="Elige una publicación de la lista." /> : (
              <>
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <Miniatura src={rec?.thumbnail ?? ficha?.thumbnail} alt="" tam={48} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm font-bold text-slate-900">{sku}</span>
                        {cuentaSel && <PuntoCuenta canal="mercado_libre" cuenta={cuentaSel} />}
                        {(rec || ficha) && <ChipEstado estado={(rec ?? ficha)!.estado} />}
                      </div>
                      <div className="mt-0.5 text-[13px] text-slate-600">{titulo}</div>
                    </div>
                  </div>
                  {rec && rec.precio_recomendado == null && (
                    <div className="text-right">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Precio actual</div>
                      <div className="text-lg font-bold tabular-nums text-slate-900">{pesos(rec.precio_actual)}</div>
                      <div className="mt-0.5 text-[10.5px] text-slate-500" title={rec.razones.map((z) => RAZON[z]?.label ?? z).join(" · ")}>
                        sin propuesta{rec.razones.length ? ` · ${rec.razones.map((z) => RAZON[z]?.label ?? z).join(", ")}` : ""}
                      </div>
                    </div>
                  )}
                  {rec && rec.precio_recomendado != null && (
                    <div className="text-right">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Actual → recomendado</div>
                      <div className="text-lg font-bold tabular-nums text-slate-900">
                        {pesos(rec.precio_actual)} <span className="text-slate-300" aria-hidden>→</span> <span className="text-orange-700">{pesos(rec.precio_recomendado)}</span>
                      </div>
                      {rec.cambio_pct != null && <div className="mt-0.5 flex justify-end"><ChipCambio cambio={rec.cambio_pct} umbral={umbral} /></div>}
                      <div className="mt-0.5 text-[10.5px] text-slate-500">propuesta · pendiente de autorización</div>
                    </div>
                  )}
                </div>
                {cargH ? <Cargando texto="Leyendo historia…" /> : errH ? <CajaError mensaje={errH} /> : <GraficaHistorial serie={serie} />}
              </>
            )}
          </Tarjeta>

          {resumen && (
            <Tarjeta className="p-4">
              <Ceja>Resumen · {dia(resumen.desde)} – {dia(resumen.hasta)}</Ceja>
              <div className="mt-1 grid grid-cols-1 gap-x-8 sm:grid-cols-2">
                <Renglon etiqueta="Unidades últimos 30 días" valor={entero(resumen.u30)} />
                <Renglon etiqueta="vs 30 días anteriores" valor={pctFirmado(resumen.cambioU, 0)}
                         tono={resumen.cambioU == null ? undefined : resumen.cambioU >= 0 ? "text-emerald-600" : "text-rose-600"} />
                <Renglon etiqueta="Visitas últimos 30 días" valor={entero(resumen.v30)} />
                <Renglon etiqueta="Días sin oferta (30 d)" valor={entero(resumen.sinOferta)} ayuda="Días en que la publicación no tenía oferta (pausada o sin stock): no son demanda cero." />
                <Renglon etiqueta="Precio realizado medio (30 d)" valor={pesos(resumen.precioMedio == null ? null : Math.round(resumen.precioMedio * 100) / 100)} />
                <Renglon etiqueta="Precio ofrecido mín. – máx." valor={`${pesos(resumen.min)} – ${pesos(resumen.max)}`} />
                {rec && <Renglon etiqueta="Elasticidad (β)" valor={`${rec.elasticidad.beta?.toFixed(2) ?? "—"} · ${rec.elasticidad.confianza}`} />}
                {rec && <Renglon etiqueta="Unidades/día al recomendado" valor={`${cifra(rec.unidades_dia.actual)} → ${cifra(rec.unidades_dia.recomendado)}`} />}
              </div>
            </Tarjeta>
          )}
        </div>
      </div>
    </>
  );
}
