"use client";

/**
 * Historial: la historia de precio de cada publicación FULL de ML — lo que se
 * cobró de verdad (ingreso ÷ unidades), lo que se ofrecía y lo que el
 * laboratorio recomendó en cada snapshot — junto a unidades y visitas.
 *
 * La lista de la izquierda es la de `/precios` (las publicaciones que el
 * optimizador evalúa); cada renglón pide su `/historial/{id}` sólo cuando se
 * pinta, para dibujar su mini-serie sin descargar 1,900 historias de golpe.
 */
import { useEffect, useMemo, useState } from "react";
import { Encabezado } from "@/components/Marco";
import { Buscador, CajaError, Cargando, Ceja, Paginacion, PuntoCuenta, Segmentado, Tarjeta, useRetrasado, Vacio, Renglon } from "@/components/ui";
import { ChipEstado } from "@/components/Chips";
import { Sparkline } from "@/components/graficas";
import GraficaHistorial from "@/components/GraficaHistorial";
import { cifra, dia, entero, pctFirmado, pesos } from "@/lib/formato";
import { COLOR_CUENTA } from "@/lib/tema";
import type { Historial, Pagina, PuntoHistorial, Recomendacion } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

const POR_PAGINA = 25;

function serieDe(h: Historial | Record<string, Historial> | null, id: string): PuntoHistorial[] {
  if (!h) return [];
  if ("serie" in h && Array.isArray((h as Historial).serie)) return (h as Historial).serie;
  return (h as Record<string, Historial>)[id]?.serie ?? [];
}

function RenglonLista({ r, activo, onElegir }: { r: Recomendacion; activo: boolean; onElegir: () => void }) {
  const { datos } = usePedido<Historial>(`/historial/${encodeURIComponent(r.id)}`);
  const serie = serieDe(datos, r.id);
  // La mini-serie usa el precio ofrecido (continuo); el realizado tiene huecos los días sin venta.
  const valores = serie.slice(-90).map((p) => p.precio_ofrecido ?? p.precio_realizado);
  return (
    <button type="button" onClick={onElegir} aria-pressed={activo}
            className={`flex w-full items-center gap-3 border-b border-slate-100 px-3 py-2.5 text-left transition-colors ${activo ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11.5px] font-semibold text-slate-800">{r.sku ?? "—"}</span>
          <PuntoCuenta canal="mercado_libre" cuenta={r.cuenta} conNombre={false} />
        </div>
        <div className="truncate text-[11.5px] text-slate-500" title={r.titulo ?? ""}>{r.titulo ?? r.listing_id}</div>
      </div>
      <div className="shrink-0 text-right">
        <Sparkline valores={valores} ancho={92} alto={26} marca={r.precio_recomendado} />
        <div className="text-[10.5px] tabular-nums text-slate-500">{pesos(r.precio_actual)}</div>
      </div>
    </button>
  );
}

export default function PaginaHistorial() {
  const { datos, error, cargando, recargar } = usePedido<Pagina<Recomendacion>>("/precios", {}, { todo: true });
  const [q, setQ] = useState("");
  const [cuenta, setCuenta] = useState("");
  const [page, setPage] = useState(1);
  const [elegido, setElegido] = useState<string | null>(null);
  const qq = useRetrasado(q, 200).trim().toLowerCase();

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("id");
    if (id) setElegido(id);
  }, []);

  const filas = useMemo(() => (datos?.filas ?? []).filter((r) =>
    (!cuenta || r.cuenta === cuenta)
    && (!qq || (r.sku ?? "").toLowerCase().includes(qq) || (r.titulo ?? "").toLowerCase().includes(qq) || r.listing_id.toLowerCase().includes(qq))),
  [datos, cuenta, qq]);

  useEffect(() => {
    if (!elegido && filas.length) setElegido(filas[0].id);
  }, [filas, elegido]);

  const rec = (datos?.filas ?? []).find((r) => r.id === elegido) ?? null;
  const { datos: hist, error: errH, cargando: cargH } = usePedido<Historial>(elegido ? `/historial/${encodeURIComponent(elegido)}` : null);
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
    return {
      u30, v30: suma(ult30, "visitas"), precioMedio: u30 ? ingreso / u30 : null,
      cambioU: u60 ? (u30 - u60) / u60 : null,
      min: precios.length ? Math.min(...precios) : null, max: precios.length ? Math.max(...precios) : null,
      desde: serie[0].fecha, hasta: serie[serie.length - 1].fecha,
    };
  }, [serie]);

  const pagina = filas.slice((page - 1) * POR_PAGINA, page * POR_PAGINA);

  return (
    <>
      <Encabezado titulo="Historial"
                  descripcion="150 días de precio realizado, precio ofrecido y el recomendado de cada snapshot del laboratorio, con las unidades y visitas de cada día." />
      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Tarjeta className="flex min-w-0 flex-col overflow-hidden">
          <div className="space-y-2 border-b border-slate-100 p-3">
            <Buscador valor={q} onCambio={(x) => { setQ(x); setPage(1); }} />
            <Segmentado valor={cuenta} onCambio={(x) => { setCuenta(x); setPage(1); }} opciones={[
              { id: "", label: "Ambas" },
              { id: "BEKURA", label: "Kubera", punto: COLOR_CUENTA.BEKURA },
              { id: "SANCORFASHION", label: "San Corpe", punto: COLOR_CUENTA.SANCORFASHION },
            ]} />
          </div>
          <div className="min-h-0 flex-1 lg:max-h-[calc(100vh-300px)] lg:overflow-y-auto">
            {!datos && cargando ? <Cargando /> : pagina.length === 0 ? <Vacio texto="Sin publicaciones con esa búsqueda." /> : (
              pagina.map((r) => (
                <RenglonLista key={r.id} r={r} activo={r.id === elegido}
                              onElegir={() => {
                                setElegido(r.id);
                                if (window.innerWidth < 1024) document.getElementById("grafica-historial")?.scrollIntoView({ behavior: "smooth", block: "start" });
                              }} />
              ))
            )}
          </div>
          {datos && <Paginacion page={page} total={filas.length} perPage={POR_PAGINA} onPage={setPage} />}
        </Tarjeta>

        <div id="grafica-historial" className="min-w-0 scroll-mt-28 space-y-4">
          <Tarjeta className="p-4 sm:p-5">
            {!elegido ? <Vacio texto="Elige una publicación de la lista." /> : (
              <>
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-bold text-slate-900">{rec?.sku ?? elegido}</span>
                      {rec && <PuntoCuenta canal="mercado_libre" cuenta={rec.cuenta} />}
                      {rec && <ChipEstado estado={rec.estado} />}
                    </div>
                    <div className="mt-0.5 text-[13px] text-slate-600">{rec?.titulo ?? ""}</div>
                  </div>
                  {rec && (
                    <div className="text-right">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Actual → recomendado</div>
                      <div className="text-lg font-bold tabular-nums text-slate-900">
                        {pesos(rec.precio_actual)} <span className="text-slate-300">→</span> <span className="text-orange-600">{pesos(rec.precio_recomendado)}</span>
                      </div>
                      {rec.cambio_pct != null && <div className="text-[11px] text-slate-500">{pctFirmado(rec.cambio_pct)}</div>}
                    </div>
                  )}
                </div>
                {cargH && !hist ? <Cargando texto="Leyendo historia…" /> : errH ? <CajaError mensaje={errH} /> : <GraficaHistorial serie={serie} />}
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
                <Renglon etiqueta="Precio realizado medio (30 d)" valor={pesos(resumen.precioMedio == null ? null : Math.round(resumen.precioMedio * 100) / 100)} />
                <Renglon etiqueta="Precio ofrecido mínimo" valor={pesos(resumen.min)} />
                <Renglon etiqueta="Precio ofrecido máximo" valor={pesos(resumen.max)} />
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
