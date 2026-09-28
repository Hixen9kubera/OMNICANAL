"use client";

/**
 * Cajón de una publicación: desglose por unidad, demanda, stock, competencia e
 * historia de precio. Todo sale de la fila de `/publicaciones` salvo la historia
 * (`/historial/{id}`), que se pide al abrir.
 */
import { ExternalLink } from "lucide-react";
import { Cajon, Ceja, Chip, Miniatura, Renglon, SinDato, Tarjeta, PuntoCuenta, Cargando, CajaError } from "./ui";
import { ChipEstado, ChipFuenteCosto, ChipFull, ChipSupuesto, TagValidado } from "./Chips";
import GraficaHistorial from "./GraficaHistorial";
import { cifra, dia, diaHora, entero, pct, pesos, tonoMargen } from "@/lib/formato";
import { AVISO } from "@/lib/vocabulario";
import { SERIE, TEMA_CANAL } from "@/lib/tema";
import type { Historial, Publicacion } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

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
      <div className="flex h-3 w-full gap-[2px] overflow-hidden rounded-full">
        {partes.map((x) => (
          <span key={x.nombre} title={`${x.nombre}: ${pesos(x.valor)} (${pct(x.valor / precio)})`}
                style={{ width: `${(x.valor / total) * 100}%`, background: x.color }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
        {partes.map((x) => (
          <span key={x.nombre} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: x.color }} />{x.nombre}
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
    <div className="mt-4 px-1">
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
      <div className="flex justify-between text-[10.5px] tabular-nums text-slate-400">
        <span>mín {pesos(c.minimo)}</span>
        <span className="inline-flex items-center gap-3">
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: SERIE.principal }} />nuestro</span>
          <span className="inline-flex items-center gap-1"><span className="h-2.5 w-[2px] bg-slate-500" />mediana</span>
        </span>
        <span>máx {pesos(c.maximo)}</span>
      </div>
    </div>
  );
}

export default function CajonPublicacion({ pub, onCerrar }: { pub: Publicacion | null; onCerrar: () => void }) {
  const esML = pub?.canal === "mercado_libre";
  const { datos: hist, error, cargando } = usePedido<Historial | Record<string, Historial>>(
    pub ? `/historial/${encodeURIComponent(pub.id)}` : null);
  const serie = hist ? ("serie" in hist ? (hist as Historial).serie : (hist as Record<string, Historial>)[pub?.id ?? ""]?.serie ?? []) : [];
  if (!pub) return null;
  const p = pub;
  const tema = TEMA_CANAL[p.canal];
  const c = p.competencia;

  return (
    <Cajon abierto={!!pub} onCerrar={onCerrar}
           titulo={<span className="flex items-start gap-3"><Miniatura src={p.thumbnail} alt={p.titulo ?? ""} tam={44} /><span>{p.titulo ?? <SinDato texto="sin título" />}</span></span>}
           subtitulo={
             <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
               <span className="font-mono text-slate-700">{p.sku ?? "sin SKU"}</span>
               <span className="inline-flex items-center gap-1 rounded px-1.5 py-[1px] text-[10px] font-bold" style={{ background: tema.suave, color: tema.texto === "#FFFFFF" ? tema.color : tema.texto }}>{tema.nombre}</span>
               <PuntoCuenta canal={p.canal} cuenta={p.cuenta} />
               {p.url ? (
                 <a href={p.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-mono text-indigo-600 hover:underline">
                   {p.listing_id}<ExternalLink size={11} />
                 </a>
               ) : <span className="font-mono">{p.listing_id}</span>}
             </span>
           }>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <ChipEstado estado={p.estado} sub={p.sub_status} />
          {p.es_full && <ChipFull />}
          {!p.es_full && p.logistica && <Chip>{p.logistica}</Chip>}
          {p.promo && <Chip tono="indigo" titulo="Precio de promoción con fecha de fin.">Promo {p.promo.tipo}{p.promo.fin ? ` · hasta ${dia(p.promo.fin)}` : ""}</Chip>}
          {p.supuesto_canal && <ChipSupuesto />}
          {(p.tags_calidad ?? []).map((t) => <Chip key={t} tono="amber">{t}</Chip>)}
          {(p.avisos ?? []).map((a) => <Chip key={a} tono="amber">{AVISO[a] ?? a}</Chip>)}
        </div>

        <Tarjeta className="p-4">
          <Ceja>Economía por unidad</Ceja>
          <div className="mt-2">
            <Renglon etiqueta="Precio cobrado (con IVA)" valor={
              <span>
                {p.precio_lista != null && p.precio_cobrado != null && p.precio_lista > p.precio_cobrado && (
                  <span className="mr-2 text-xs text-slate-400 line-through">{pesos(p.precio_lista)}</span>
                )}
                <b>{pesos(p.precio_cobrado)}</b>
              </span>} />
            <Renglon etiqueta="− IVA (16 %)" valor={pesos(p.iva)} />
            <Renglon etiqueta={<>− Comisión {p.comision_pct != null && <span className="text-slate-400">({pct(p.comision_pct)})</span>} {p.supuesto_canal && <ChipSupuesto />}</>}
                     valor={pesos(p.comision)} ayuda="ML cobra la comisión sobre el precio CON IVA (medido)." />
            <Renglon etiqueta={<>− Envío {p.supuesto_canal && <ChipSupuesto />}</>} valor={pesos(p.envio)}
                     ayuda="En FULL el vendedor paga envío también bajo $299 (tarifa reducida); desde $299, el envío gratis completo." />
            <Renglon etiqueta={<span className="inline-flex flex-wrap items-center gap-1.5">− Costo aterrizado (sin IVA) <ChipFuenteCosto costo={p.costo} />{p.costo?.validado && <TagValidado por={p.costo.revisado_por} />}</span>}
                     valor={p.costo?.unitario != null ? pesos(p.costo.unitario) : <SinDato texto="sin costo" />} />
            <Renglon fuerte etiqueta="= Utilidad por unidad" valor={pesos(p.utilidad)} tono={tonoMargen(p.margen_pct)} />
            <Renglon etiqueta="Margen sobre precio" valor={pct(p.margen_pct)} tono={tonoMargen(p.margen_pct)} />
          </div>
          <BarraDesglose p={p} />
          {p.costo?.costo_panel != null && p.costo.unitario != null && (
            <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[11.5px] text-slate-500">
              El panel usa hoy <b className="tabular-nums text-slate-700">{pesos(p.costo.costo_panel)}</b> de costo
              ({p.costo.costo_panel > p.costo.unitario ? "+" : ""}{pct((p.costo.costo_panel - p.costo.unitario) / p.costo.unitario, 0)} vs el del laboratorio)
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
          <div className="flex items-center justify-between gap-2">
            <Ceja>Competencia</Ceja>
            {c && <span className="text-[11px] text-slate-400">{c.fuente} · {diaHora(c.capturado_en)}</span>}
          </div>
          {!c ? (
            <p className="mt-2 text-sm text-slate-400">Sin medición de competencia para esta publicación.</p>
          ) : (
            <>
              <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-5">
                {[
                  ["Mediana", pesos(c.mediana)], ["Promedio", pesos(c.promedio)], ["Mínimo", pesos(c.minimo)],
                  ["Máximo", pesos(c.maximo)], ["Muestras", entero(c.n)],
                ].map(([k, v]) => (
                  <div key={k}>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">{k}</div>
                    <div className="text-sm font-bold tabular-nums text-slate-800">{v}</div>
                  </div>
                ))}
              </div>
              {c.sugerido_ml != null && (
                <p className="mt-2 text-[11.5px] text-slate-500">Precio sugerido por ML: <b className="tabular-nums text-slate-700">{pesos(c.sugerido_ml)}</b></p>
              )}
              <FranjaCompetencia p={p} />
              {c.lista && c.lista.length > 0 && (
                <div className="mt-3 max-h-56 overflow-auto rounded-lg border border-slate-100">
                  <table className="w-full text-[11.5px]">
                    <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr><th className="px-2.5 py-1.5 text-left">Competidor</th><th className="px-2.5 py-1.5 text-right">Precio</th><th className="px-2.5 py-1.5 text-right">Vendidos</th></tr>
                    </thead>
                    <tbody>
                      {c.lista.map((x, i) => (
                        <tr key={i} className="border-t border-slate-50">
                          <td className="max-w-[16rem] truncate px-2.5 py-1 text-slate-600">
                            {x.url ? <a href={x.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{x.titulo ?? x.vendedor ?? "—"}</a> : (x.titulo ?? x.vendedor ?? "—")}
                          </td>
                          <td className="px-2.5 py-1 text-right font-semibold tabular-nums text-slate-800">{pesos(x.precio)}</td>
                          <td className="px-2.5 py-1 text-right tabular-nums text-slate-500">{x.vendidos == null ? "—" : cifra(x.vendidos)}</td>
                        </tr>
                      ))}
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
            {cargando && !hist ? <Cargando texto="Leyendo historia…" />
              : error ? <CajaError mensaje={error} />
              : <GraficaHistorial serie={serie} compacta />}
          </div>
          {!esML && <p className="mt-2 text-[11px] text-slate-400">Fuera de Mercado Libre la historia depende de lo que el canal deja leer.</p>}
        </Tarjeta>
      </div>
    </Cajon>
  );
}
