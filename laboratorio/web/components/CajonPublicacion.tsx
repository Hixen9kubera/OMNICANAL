"use client";

/**
 * Cajón de una publicación: desglose por unidad, propuesta (ML FULL) o precio de
 * paridad (otros canales), demanda, stock, competencia e historia de precio. Todo
 * sale de la fila de `/publicaciones` salvo la historia (`/historial/{id}`), que
 * se pide al abrir. Fuera de ML no hay historia: el 404 se lee como «sin historia».
 */
import Link from "next/link";
import { AlertTriangle, ArrowRight, Clock, ExternalLink } from "lucide-react";
import { Cajon, Ceja, Chip, Franja, Miniatura, Renglon, SinDato, Tarjeta, PuntoCuenta, Cargando, CajaError } from "./ui";
import { ChipCambio, ChipCoordinado, ChipEstado, ChipFuenteCosto, ChipFull, ChipRazon, ChipSupuesto, TagValidado } from "./Chips";
import GraficaHistorial from "./GraficaHistorial";
import { ruta } from "@/lib/api";
import { cifra, dia, diaHora, entero, pct, pctFirmado, pesos, tonoMargen, urlSegura } from "@/lib/formato";
import { AVISO, tonoAvisoPub } from "@/lib/vocabulario";
import { SERIE, TEMA_CANAL } from "@/lib/tema";
import type { Historial, Publicacion } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

/** URL pública de un competidor de ML a partir de su id (producto, user product o artículo). */
export function urlCompetidor(id: string | null | undefined): string | null {
  if (!id) return null;
  if (/^MLMU\d+$/.test(id)) return `https://www.mercadolibre.com.mx/up/${id}`;
  const m = /^MLM(\d+)$/.exec(id);
  if (!m) return null;
  return m[1].length >= 10 ? `https://articulo.mercadolibre.com.mx/MLM-${m[1]}` : `https://www.mercadolibre.com.mx/p/${id}`;
}

/** Barra 100 % de a dónde se va el precio (≤ 5 segmentos, 2 px de hueco entre ellos). */
function BarraDesglose({ p }: { p: Publicacion }) {
  const precio = p.precio_cobrado;
  if (!precio || p.costo?.unitario == null) return null;
  const partes = [
    { nombre: "IVA", valor: p.iva ?? 0, color: "#CBD5E1" },
    { nombre: "Comisión", valor: p.comision ?? 0, color: "#94A3B8" },
    { nombre: "Envío", valor: p.envio ?? 0, color: "#64748B" },
    { nombre: "Costo", valor: p.costo.unitario, color: "#334155" },
    { nombre: "Utilidad", valor: Math.max(0, p.utilidad ?? 0), color: SERIE.principal },
  ].filter((x) => x.valor > 0);
  const total = partes.reduce((a, x) => a + x.valor, 0);
  return (
    <div className="mt-3">
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full" role="img"
           aria-label={partes.map((x) => `${x.nombre} ${pct(x.valor / precio, 0)}`).join(", ")}>
        {partes.map((x) => (
          <span key={x.nombre} title={`${x.nombre}: ${pesos(x.valor)} (${pct(x.valor / precio)})`}
                style={{ width: `${(x.valor / total) * 100}%`, background: x.color }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
        {partes.map((x) => (
          <span key={x.nombre} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: x.color }} aria-hidden />{x.nombre}
            <b className="font-semibold tabular-nums text-slate-700">{pct(x.valor / precio, 0)}</b>
          </span>
        ))}
        {(p.utilidad ?? 0) < 0 && <span className="font-semibold text-rose-600">Pierde {pesos(-(p.utilidad ?? 0))} por unidad</span>}
      </div>
    </div>
  );
}

function FranjaCompetencia({ p }: { p: Publicacion }) {
  const c = p.competencia;
  if (!c || c.minimo == null || c.maximo == null) return null;
  const nuestro = p.precio_cobrado;
  const lo = Math.min(c.minimo, nuestro ?? c.minimo);
  const hi = Math.max(c.maximo, nuestro ?? c.maximo);
  const x = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  return (
    <div className="mt-4 px-2">
      <div className="relative h-8">
        <div className="absolute top-3.5 h-[3px] rounded-full bg-slate-200" style={{ left: x(c.minimo), right: `calc(100% - ${x(c.maximo)})` }} />
        {c.mediana != null && (
          <div className="absolute top-1.5 h-5 w-[2px] -translate-x-1/2 bg-slate-500" style={{ left: x(c.mediana) }} title={`Mediana ${pesos(c.mediana)}`} />
        )}
        {nuestro != null && (
          <div className="absolute top-2 h-4 w-4 -translate-x-1/2 rounded-full border-2 border-white shadow" style={{ left: x(nuestro), background: SERIE.principal }}
               title={`Nuestro precio ${pesos(nuestro)}`} />
        )}
      </div>
      <div className="flex justify-between text-[10.5px] tabular-nums text-slate-500">
        <span>mín {pesos(c.minimo)}</span>
        <span className="inline-flex items-center gap-3">
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: SERIE.principal }} aria-hidden />nuestro</span>
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-[2px] bg-slate-500" aria-hidden />mediana</span>
        </span>
        <span>máx {pesos(c.maximo)}</span>
      </div>
    </div>
  );
}

/**
 * Lo que pone en duda el costo, o lo que pasaría si se cobrara la mercancía.
 * Hoy el costo es el prorrateo de $525,000 por contenedor (regla de Brandon): el
 * riesgo es AVISO, nunca bloqueo.
 */
function AvisosCosto({ p }: { p: Publicacion }) {
  const c = p.costo;
  if (!c) return null;
  const av = c.avisos ?? [];
  const r = c.riesgo_mercancia;
  const descartado = av.includes("costo_imposible") || av.includes("costo_menor_a_1_peso");
  const riesgo = av.includes("riesgo_si_se_cobra_mercancia") && r;
  if (!descartado && !riesgo) return null;
  const alRec = r?.al_recomendado != null;
  return (
    <div className="mt-3 space-y-2">
      {descartado && c.aviso && <Franja tono="rose" icono={<AlertTriangle size={14} className="text-rose-600" />}>{c.aviso}</Franja>}
      {riesgo && (
        <Franja tono="amber" icono={<AlertTriangle size={14} className="text-amber-600" />}>
          <b className="font-semibold">Riesgo si se cobra la mercancía.</b>{" "}
          Con el costo del panel ({pesos(r.costo_panel)}: mercancía + flete) el margen {alRec ? `al recomendado (${pesos(r.precio_recomendado)})` : `a hoy (${pesos(p.precio_cobrado)})`} sería{" "}
          <b className="whitespace-nowrap tabular-nums">{pct(alRec ? r.al_recomendado : r.al_precio_cobrado)}</b>, bajo el piso de {pct(r.piso ?? 0.12, 0)}.
          Hoy no aplica: el costo es el prorrateo de $525,000 por contenedor, sin la mercancía (regla de Brandon).
        </Franja>
      )}
    </div>
  );
}

/** ML FULL: la propuesta del optimizador. Otros canales: el precio de paridad. */
function BloquePropuesta({ p, umbral }: { p: Publicacion; umbral: number }) {
  if (p.canal === "mercado_libre" && p.es_full) {
    const razones = p.razones ?? [];
    return (
      <Tarjeta className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Ceja>Propuesta del laboratorio</Ceja>
          <Chip tono="slate" titulo="El laboratorio sólo propone. Aplicar un precio requiere autorización y se hace fuera de aquí.">
            <Clock size={10} aria-hidden /> Pendiente de autorización
          </Chip>
        </div>
        {p.precio_recomendado != null ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold tabular-nums text-slate-500">{pesos(p.precio_cobrado)}</span>
            <ArrowRight size={16} className="text-slate-300" aria-hidden />
            <span className="text-2xl font-bold tabular-nums text-slate-900">{pesos(p.precio_recomendado)}</span>
            <ChipCambio cambio={p.cambio_pct} umbral={umbral} />
            <ChipCoordinado razones={razones} />
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">Sin propuesta para esta publicación.</p>
        )}
        {p.precio_recomendado != null && p.cambio_ref === "precio_realizado_base" && (
          <p className="mt-1 text-[11.5px] text-slate-500">
            Pausada: el cambio se mide contra lo que pagaban en su último periodo con oferta; contra el precio publicado hoy sería{" "}
            <b className="whitespace-nowrap tabular-nums text-slate-700">{pctFirmado(p.cambio_vs_actual)}</b>.
          </p>
        )}
        {razones.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">{razones.filter((r) => r !== "coordinado_otra_cuenta" && r !== "brecha_entre_cuentas").map((r) => <ChipRazon key={r} razon={r} />)}</div>
        )}
        <Link href={`${ruta("/precios")}?q=${encodeURIComponent(p.listing_id)}`}
              className="mt-3 inline-flex items-center gap-1 text-[12px] font-semibold text-indigo-600 hover:text-indigo-800 hover:underline">
          Ver la curva y el plan de ajuste <ArrowRight size={12} aria-hidden />
        </Link>
      </Tarjeta>
    );
  }
  if (p.precio_paridad == null && !p.paridad) return null;
  const par = p.paridad;
  const fuente = par?.fuente === "ml_recomendado" ? "el margen del precio recomendado en ML" : par?.fuente === "piso_canal" ? "el piso de margen del canal" : par?.fuente ?? "—";
  return (
    <Tarjeta className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Ceja>Precio de paridad</Ceja>
        {p.supuesto_canal && <ChipSupuesto />}
      </div>
      <div className="mt-1">
        <Renglon etiqueta="Precio que iguala el margen objetivo" valor={p.precio_paridad == null ? <SinDato /> : <b>{pesos(p.precio_paridad)}</b>} />
        <Renglon etiqueta="Margen objetivo" valor={pct(par?.margen_objetivo)} ayuda={`Sale de ${fuente}.`} />
        {par?.precio_ml_recomendado != null && <Renglon etiqueta="Recomendado en ML (mismo SKU)" valor={pesos(par.precio_ml_recomendado)} />}
        {par?.diferencia_pct != null && (
          <Renglon etiqueta="Precio de hoy vs paridad"
                   valor={par.diferencia_pct > 2 ? `${cifra(1 + par.diferencia_pct)}× la paridad` : pctFirmado(par.diferencia_pct, 0)}
                   tono={par.diferencia_pct < 0 ? "text-rose-600" : "text-slate-800"} />
        )}
      </div>
      <p className="mt-2 text-[11px] leading-snug text-slate-500">Referencia, no propuesta: comisión y envío del canal son supuestos de parametros.json.</p>
    </Tarjeta>
  );
}

export default function CajonPublicacion({ pub, onCerrar, umbral = 0.01 }: { pub: Publicacion | null; onCerrar: () => void; umbral?: number }) {
  const esML = pub?.canal === "mercado_libre";
  // Fuera de ML no hay historia reconstruida, pero sí un punto por snapshot diario del laboratorio.
  const { datos: hist, error, cargando } = usePedido<Historial | Record<string, Historial>>(
    pub ? `/historial/${encodeURIComponent(pub.id)}` : null, undefined, { nulo404: true });
  const serie = hist ? ("serie" in hist ? (hist as Historial).serie : (hist as Record<string, Historial>)[pub?.id ?? ""]?.serie ?? []) : [];
  if (!pub) return null;
  const p = pub;
  const tema = TEMA_CANAL[p.canal];
  const c = p.competencia;
  const lista = c?.lista ?? [];

  return (
    <Cajon abierto={!!pub} onCerrar={onCerrar}
           titulo={<span className="flex items-start gap-3"><Miniatura src={p.thumbnail} alt="" tam={44} /><span>{p.titulo ?? <SinDato texto="sin título" />}</span></span>}
           subtitulo={
             <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
               <span className="font-mono text-slate-700">{p.sku ?? "sin SKU"}</span>
               <span className="inline-flex items-center gap-1 rounded px-1.5 py-[1px] text-[10px] font-bold" style={{ background: tema.suave, color: tema.texto === "#FFFFFF" ? tema.color : tema.texto }}>{tema.nombre}</span>
               {p.canal === "mercado_libre" && <PuntoCuenta canal={p.canal} cuenta={p.cuenta} />}
               {urlSegura(p.url) ? (
                 <a href={urlSegura(p.url)!} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-mono text-indigo-600 hover:underline">
                   {p.listing_id}<ExternalLink size={11} aria-hidden />
                 </a>
               ) : <span className="font-mono">{p.listing_id}</span>}
             </span>
           }>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <ChipEstado estado={p.estado} sub={p.sub_status} detalle={p.estado_detalle} />
          {p.es_full && <ChipFull />}
          {!p.es_full && p.logistica && <Chip>{p.logistica}</Chip>}
          {p.promo && <Chip tono="indigo" titulo="Precio de promoción.">Promo {p.promo.tipo}{p.promo.fin ? ` · hasta ${dia(p.promo.fin)}` : ""}</Chip>}
          {p.supuesto_canal && <ChipSupuesto />}
          {(p.tags_calidad ?? []).map((t) => <Chip key={t} tono="amber">{t}</Chip>)}
          {(p.avisos ?? []).filter((a) => a !== "riesgo_si_se_cobra_mercancia" && a !== "costo_imposible")
            .map((a) => <Chip key={a} tono={tonoAvisoPub(a)} titulo={a}>{AVISO[a] ?? a}</Chip>)}
        </div>

        <BloquePropuesta p={p} umbral={umbral} />

        <Tarjeta className="p-4">
          <Ceja>Economía por unidad</Ceja>
          <div className="mt-2">
            <Renglon etiqueta="Precio cobrado (con IVA)" valor={
              <span>
                {p.precio_lista != null && p.precio_cobrado != null && p.precio_lista > p.precio_cobrado + 0.5 && (
                  <span className="mr-2 text-xs text-slate-500 line-through">{pesos(p.precio_lista)}</span>
                )}
                <b>{pesos(p.precio_cobrado)}</b>
              </span>} />
            <Renglon etiqueta="− IVA (16 %)" valor={pesos(p.iva)} />
            <Renglon etiqueta={<>− Comisión {p.comision_pct != null && <span className="text-slate-500">({pct(p.comision_pct)})</span>} {p.supuesto_canal && <ChipSupuesto />}</>}
                     valor={pesos(p.comision)} ayuda={`ML cobra la comisión sobre el precio CON IVA (medido).${p.fuente_comision ? ` Fuente: ${p.fuente_comision}.` : ""}`} />
            <Renglon etiqueta={<>− Envío {p.supuesto_canal && <ChipSupuesto />}</>} valor={pesos(p.envio)}
                     ayuda={`En FULL el vendedor paga envío también bajo $299 (tarifa reducida); desde $299, el envío gratis completo.${p.peso?.kg != null ? ` Peso facturable ${cifra(p.peso.kg)} kg (${p.peso.fuente ?? "—"}).` : ""}`} />
            <Renglon etiqueta={<span className="inline-flex flex-wrap items-center gap-1.5">− Costo aterrizado (sin IVA) <ChipFuenteCosto costo={p.costo} />{p.costo?.validado && <TagValidado por={p.costo.revisado_por} />}</span>}
                     valor={p.costo?.unitario != null ? pesos(p.costo.unitario) : <SinDato texto="sin costo" />} />
            <Renglon fuerte etiqueta="= Utilidad por unidad" valor={pesos(p.utilidad)} tono={tonoMargen(p.margen_pct)} />
            <Renglon etiqueta="Margen sobre precio" valor={pct(p.margen_pct)} tono={tonoMargen(p.margen_pct)} />
          </div>
          <BarraDesglose p={p} />
          <AvisosCosto p={p} />
          {p.costo?.costo_panel != null && p.costo.unitario != null && p.costo.unitario > 0 && (
            <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[11.5px] text-slate-500">
              El panel usa hoy <b className="tabular-nums text-slate-700">{pesos(p.costo.costo_panel)}</b> de costo
              ({p.costo.costo_panel / p.costo.unitario > 3
                ? `${cifra(p.costo.costo_panel / p.costo.unitario)}× el del laboratorio`
                : `${pctFirmado((p.costo.costo_panel - p.costo.unitario) / p.costo.unitario, 0)} vs el del laboratorio`})
              {p.costo.contenedor ? <> · contenedor <span className="font-mono">{p.costo.contenedor}</span></> : null}.
            </p>
          )}
        </Tarjeta>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            ["Visitas 30 d", entero(p.visitas_30d)],
            ["Ventas 30 d", entero(p.unidades_30d)],
            ["Conversión", pct(p.conversion_30d, 2)],
            ["Ingreso 30 d", pesos(p.ingreso_30d)],
          ].map(([k, v]) => (
            <div key={k} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{k}</div>
              <div className="mt-0.5 text-base font-bold tabular-nums text-slate-900">{v}</div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-3">
          {[
            ["Stock FULL", p.stock_full],
            ["Stock propio", p.stock_propio],
            ["Libre en Odoo", p.stock_odoo],
          ].map(([k, v]) => (
            <div key={k as string} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{k}</div>
              <div className="mt-0.5 text-base font-bold tabular-nums text-slate-900">{v == null ? <SinDato /> : entero(v as number)}</div>
            </div>
          ))}
        </div>

        <Tarjeta className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Ceja>Competencia</Ceja>
            {c && <span className="text-[11px] text-slate-500">{c.fuente}{c.termino ? ` · «${c.termino}»` : ""} · {diaHora(c.capturado_en)}{c.edad_dias != null ? ` (hace ${c.edad_dias} d)` : ""}</span>}
          </div>
          {!c ? (
            <p className="mt-2 text-sm text-slate-500">Sin medición de competencia para esta publicación.</p>
          ) : (
            <>
              <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-5">
                {[
                  ["Mediana", pesos(c.mediana)], ["Promedio", pesos(c.promedio)], ["Mínimo", pesos(c.minimo)],
                  ["Máximo", pesos(c.maximo)], ["Muestras", entero(c.n)],
                ].map(([k, v]) => (
                  <div key={k}>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{k}</div>
                    <div className="text-sm font-bold tabular-nums text-slate-800">{v}</div>
                  </div>
                ))}
              </div>
              {c.nota && <p className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11.5px] text-amber-900">{c.nota}</p>}
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-slate-500">
                {c.mediana_filtrada != null && c.mediana_filtrada !== c.mediana && <span>Mediana filtrada <b className="tabular-nums text-slate-700">{pesos(c.mediana_filtrada)}</b>{c.n_filtrado != null ? ` (n ${c.n_filtrado})` : ""}</span>}
                {c.sugerido_ml != null && <span>Sugerido por ML <b className="tabular-nums text-slate-700">{pesos(c.sugerido_ml)}</b></span>}
                {c.price_to_win?.precio != null && <span>Precio para ganar catálogo <b className="tabular-nums text-slate-700">{pesos(c.price_to_win.precio)}</b>{c.price_to_win.status ? ` · ${c.price_to_win.status}` : ""}</span>}
                {c.bestsellers?.mediana != null && <span>Más vendidos de la categoría <b className="tabular-nums text-slate-700">{pesos(c.bestsellers.mediana)}</b> (n {c.bestsellers.n})</span>}
              </div>
              <FranjaCompetencia p={p} />
              {lista.length > 0 && (
                <div className="mt-3 max-h-56 overflow-auto rounded-lg border border-slate-100">
                  <table className="w-full text-[11.5px]">
                    <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr><th scope="col" className="px-2.5 py-1.5 text-left">Competidor</th><th scope="col" className="px-2.5 py-1.5 text-right">Precio</th><th scope="col" className="px-2.5 py-1.5 text-right">Visitas 30 d</th></tr>
                    </thead>
                    <tbody>
                      {[...lista].sort((a, b) => a.precio - b.precio).map((x, i) => {
                        const url = urlSegura(x.url) ?? urlCompetidor(x.id);
                        const nombre = x.titulo ?? x.vendedor ?? x.id ?? "—";
                        return (
                          <tr key={`${x.id ?? i}`} className="border-t border-slate-50">
                            <td className="max-w-[16rem] px-2.5 py-1 text-slate-600">
                              <div className="truncate" title={nombre}>
                                {url ? <a href={url} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-700 hover:underline">{nombre}</a> : nombre}
                              </div>
                              {x.vendedor && <div className="truncate text-[10.5px] text-slate-500">{x.vendedor}</div>}
                            </td>
                            <td className="px-2.5 py-1 text-right font-semibold tabular-nums text-slate-800">{pesos(x.precio)}</td>
                            <td className="px-2.5 py-1 text-right tabular-nums text-slate-500">{x.visitas_30d != null ? entero(x.visitas_30d) : x.vendidos != null ? `${cifra(x.vendidos)} vend.` : "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </Tarjeta>

        <Tarjeta className="p-4">
          <Ceja>Historia de precio · 150 días</Ceja>
          <div className="mt-2">
            {cargando ? <Cargando texto="Leyendo historia…" />
              : error ? <CajaError mensaje={error} />
              : !esML && serie.length < 2 ? (
                <p className="rounded-lg border border-dashed border-slate-300 px-3 py-5 text-center text-xs leading-relaxed text-slate-500">
                  Fuera de Mercado Libre el laboratorio no reconstruye la historia: sólo guarda el precio de cada snapshot diario.
                  {serie.length === 1 && <> Hasta hoy hay uno: <b className="tabular-nums text-slate-700">{pesos(serie[0].precio_cobrado ?? serie[0].precio_ofrecido)}</b> el {dia(serie[0].fecha)}.</>}
                </p>
              )
              : <GraficaHistorial serie={serie} compacta />}
          </div>
        </Tarjeta>
      </div>
    </Cajon>
  );
}
