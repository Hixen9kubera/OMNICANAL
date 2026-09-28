"use client";

/**
 * Métricas del catálogo de ML por cuenta (BEKURA = Kubera, SANCORFASHION = San
 * Corpe) y el estado de los demás canales. Sumar las dos cuentas esconde la mitad
 * de la historia: por eso la comparativa va lado a lado y el consolidado sólo
 * suma lo que se puede sumar (una mediana no se suma).
 *
 * La palanca más grande medida el 28-sep no es el precio sino la disponibilidad:
 * 1,412 FULL pausadas casi sin stock en FULL. La tabla de «palancas» las ordena por
 * ventas perdidas al día cuando Odoo sí tiene piezas libres.
 */
import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";
import { Encabezado } from "@/components/Marco";
import { CajaError, Cargando, Ceja, Kpi, MarcoTabla, PuntoCuenta, Segmentado, Tarjeta, Th, Vacio } from "@/components/ui";
import { Columnas, Leyenda, TablaGemela } from "@/components/graficas";
import { cifra, dia, diasDesde, entero, pct, pesos, pesosCorto, tonoMargen } from "@/lib/formato";
import { COLOR_CUENTA, NOMBRE_CUENTA, SERIE, TEMA_CANAL } from "@/lib/tema";
import type { Canal, Metricas, MetricasCuenta } from "@/lib/tipos";
import { usePedido } from "@/lib/usePedido";

const CUENTAS = ["BEKURA", "SANCORFASHION"] as const;

function consolidar(m: Metricas): MetricasCuenta {
  const xs = CUENTAS.map((c) => m.por_cuenta?.[c]).filter(Boolean) as MetricasCuenta[];
  const suma = (k: keyof MetricasCuenta) => xs.reduce((a, x) => a + (Number(x[k]) || 0), 0);
  const visitas = suma("visitas_dia"); const unidades = suma("unidades_dia");
  return {
    publicaciones: suma("publicaciones"), activas: suma("activas"), activas_full: suma("activas_full"), pausadas_full: suma("pausadas_full"),
    pausadas_full_sin_stock: suma("pausadas_full_sin_stock"), pausadas_full_con_stock_odoo: suma("pausadas_full_con_stock_odoo"),
    visitas_dia: visitas, unidades_dia: unidades, conversion: visitas ? unidades / visitas : null, ingreso_30d: suma("ingreso_30d"),
    margen_mediano: null, perdiendo_dinero: suma("perdiendo_dinero"), con_costo: suma("con_costo"), con_competencia: suma("con_competencia"),
  };
}

const FILAS_COMPARATIVA: { k: keyof MetricasCuenta; label: string; fmt: (v: number | null) => string }[] = [
  { k: "publicaciones", label: "Publicaciones", fmt: (v) => entero(v) },
  { k: "activas", label: "Activas", fmt: (v) => entero(v) },
  { k: "activas_full", label: "Activas FULL", fmt: (v) => entero(v) },
  { k: "pausadas_full", label: "Pausadas FULL", fmt: (v) => entero(v) },
  { k: "pausadas_full_con_stock_odoo", label: "Pausadas FULL con piezas en Odoo", fmt: (v) => entero(v) },
  { k: "visitas_dia", label: "Visitas / día", fmt: (v) => cifra(v) },
  { k: "unidades_dia", label: "Unidades / día", fmt: (v) => cifra(v) },
  { k: "conversion", label: "Conversión", fmt: (v) => pct(v, 2) },
  { k: "ingreso_30d", label: "Ingreso 30 d", fmt: (v) => pesosCorto(v) },
  { k: "margen_mediano", label: "Margen mediano", fmt: (v) => pct(v) },
  { k: "perdiendo_dinero", label: "Perdiendo dinero", fmt: (v) => entero(v) },
  { k: "con_costo", label: "Con costo", fmt: (v) => entero(v) },
  { k: "con_competencia", label: "Con competencia medida", fmt: (v) => entero(v) },
];

function Comparativa({ m }: { m: Metricas }) {
  const a = m.por_cuenta?.BEKURA; const b = m.por_cuenta?.SANCORFASHION;
  if (!a || !b) return null;
  return (
    <Tarjeta className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Ceja>Kubera vs San Corpe</Ceja>
        <Leyenda items={[{ nombre: "Kubera (BEKURA)", color: COLOR_CUENTA.BEKURA, tipo: "barra" }, { nombre: "San Corpe (SANCORFASHION)", color: COLOR_CUENTA.SANCORFASHION, tipo: "barra" }]} />
      </div>
      <p className="mt-1 text-[11px] text-slate-400">Cada renglón se escala contra el mayor de los dos: se compara la forma, no la magnitud entre renglones.</p>
      <div className="mt-3 space-y-1.5">
        {FILAS_COMPARATIVA.map((f) => {
          const va = a[f.k] as number | null; const vb = b[f.k] as number | null;
          const max = Math.max(Math.abs(va ?? 0), Math.abs(vb ?? 0)) || 1;
          return (
            <div key={f.k} className="grid grid-cols-[minmax(0,1fr)_84px_minmax(0,1fr)] items-center gap-2 text-[12px] sm:grid-cols-[minmax(0,1fr)_190px_minmax(0,1fr)]">
              <div className="flex min-w-0 items-center justify-end gap-2">
                <span className="shrink-0 tabular-nums font-semibold text-slate-800">{f.fmt(va)}</span>
                <span className="h-2.5 rounded-l-sm" style={{ width: `${(Math.abs(va ?? 0) / max) * 60}%`, background: COLOR_CUENTA.BEKURA, minWidth: va ? 2 : 0 }} />
              </div>
              <div className="text-center text-[11px] leading-tight text-slate-500">{f.label}</div>
              <div className="flex min-w-0 items-center gap-2">
                <span className="h-2.5 rounded-r-sm" style={{ width: `${(Math.abs(vb ?? 0) / max) * 60}%`, background: COLOR_CUENTA.SANCORFASHION, minWidth: vb ? 2 : 0 }} />
                <span className="shrink-0 tabular-nums font-semibold text-slate-800">{f.fmt(vb)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </Tarjeta>
  );
}

/** El histograma acepta contenedores {desde,hasta,n} o una lista de betas (el contrato no fija la forma). */
function contenedoresHistograma(h: Metricas["elasticidad"]["histograma"]): { desde: number; hasta: number; n: number }[] {
  if (!h?.length) return [];
  if (typeof h[0] === "object") return h as { desde: number; hasta: number; n: number }[];
  const betas = h as number[];
  const out: { desde: number; hasta: number; n: number }[] = [];
  for (let lo = -6; lo < -0.3; lo += 0.5) out.push({ desde: lo, hasta: Math.min(-0.3, lo + 0.5), n: betas.filter((b) => b >= lo && b < lo + 0.5).length });
  return out;
}

export default function PaginaMetricas() {
  const { datos: m, error, cargando, recargar } = usePedido<Metricas>("/metricas");
  const [cuenta, setCuenta] = useState<"" | "BEKURA" | "SANCORFASHION">("");

  const k = useMemo(() => (m ? (cuenta ? m.por_cuenta?.[cuenta] ?? null : consolidar(m)) : null), [m, cuenta]);

  const serie = useMemo(() => {
    if (!m) return null;
    const fechas = [...new Set(m.serie_diaria.map((x) => x.fecha))].sort();
    const cuentas = cuenta ? [cuenta] : [...CUENTAS];
    const idx = new Map(m.serie_diaria.map((x) => [`${x.fecha}|${x.cuenta}`, x]));
    return {
      fechas,
      unidades: cuentas.map((c) => ({ nombre: NOMBRE_CUENTA[c], color: COLOR_CUENTA[c], valores: fechas.map((f) => idx.get(`${f}|${c}`)?.unidades ?? null) })),
      ingreso: cuentas.map((c) => ({ nombre: NOMBRE_CUENTA[c], color: COLOR_CUENTA[c], valores: fechas.map((f) => idx.get(`${f}|${c}`)?.ingreso ?? null) })),
      visitas: cuentas.map((c) => fechas.map((f) => idx.get(`${f}|${c}`)?.visitas ?? null)),
    };
  }, [m, cuenta]);

  const histo = useMemo(() => (m ? contenedoresHistograma(m.elasticidad?.histograma) : []), [m]);
  const palancas = useMemo(() => (m?.palancas ?? [])
    .filter((p) => !cuenta || p.cuenta === cuenta)
    .sort((a, b) => (b.ventas_perdidas_dia ?? 0) - (a.ventas_perdidas_dia ?? 0)), [m, cuenta]);

  if (!m) return (
    <>
      <Encabezado titulo="Métricas" />
      {cargando ? <Cargando /> : error ? <CajaError mensaje={error} onReintentar={recargar} /> : null}
    </>
  );

  const pausadasSinStock = k?.pausadas_full_sin_stock ?? null;
  const perdidas = palancas.reduce((a, p) => a + (p.ventas_perdidas_dia ?? 0), 0);

  return (
    <>
      <Encabezado titulo="Métricas"
                  descripcion="Mercado Libre por cuenta y el estado de los demás canales. Visitas y unidades son promedios diarios de los últimos 30 días."
                  derecha={
                    <Segmentado valor={cuenta} onCambio={setCuenta} opciones={[
                      { id: "", label: "Consolidado" },
                      { id: "BEKURA", label: "Kubera", punto: COLOR_CUENTA.BEKURA },
                      { id: "SANCORFASHION", label: "San Corpe", punto: COLOR_CUENTA.SANCORFASHION },
                    ]} />
                  } />

      {k && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <Kpi label="Activas" valor={entero(k.activas)} tono="text-indigo-600" pie={`de ${entero(k.publicaciones)} publicaciones`} />
          <Kpi label="Activas FULL" valor={entero(k.activas_full)} pie={`${entero(k.pausadas_full)} FULL pausadas`} />
          <Kpi label="FULL pausadas sin stock" valor={entero(pausadasSinStock)} tono="text-rose-600" pie="se pausan por falta de piezas en ML" />
          <Kpi label="…con piezas en Odoo" valor={entero(k.pausadas_full_con_stock_odoo)} tono="text-amber-700" pie="se podrían reactivar enviando a FULL" />
          <Kpi label="Visitas / día" valor={cifra(k.visitas_dia)} />
          <Kpi label="Unidades / día" valor={cifra(k.unidades_dia)} tono="text-emerald-600" />
          <Kpi label="Conversión" valor={pct(k.conversion, 2)} pie="unidades ÷ visitas" />
          <Kpi label="Ingreso 30 d" valor={pesosCorto(k.ingreso_30d)} pie="con IVA" />
          <Kpi label="Margen mediano" valor={pct(k.margen_mediano)} tono={tonoMargen(k.margen_mediano)}
               pie={cuenta ? "de las activas con costo" : `Kubera ${pct(m.por_cuenta?.BEKURA?.margen_mediano)} · San Corpe ${pct(m.por_cuenta?.SANCORFASHION?.margen_mediano)}`} />
          <Kpi label="Perdiendo dinero" valor={entero(k.perdiendo_dinero)} tono="text-rose-600" pie="utilidad < 0 al precio actual" />
        </div>
      )}

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {serie && (
          <Tarjeta className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Ceja>Unidades por día</Ceja>
              {serie.unidades.length > 1 && <Leyenda items={serie.unidades.map((s) => ({ nombre: s.nombre, color: s.color, tipo: "barra" as const }))} />}
            </div>
            <div className="mt-3">
              <Columnas etiquetas={serie.fechas.map((f) => dia(f))} series={serie.unidades}
                        formato={(v) => entero(v)} formatoEje={(v) => (v >= 1000 ? `${v / 1000}k` : `${v}`)}
                        etiquetaLarga={(i) => serie.fechas[i]} />
            </div>
            <TablaGemela columnas={["Fecha", ...serie.unidades.map((s) => s.nombre), "Visitas"]}
                         filas={serie.fechas.map((f, i) => [f, ...serie.unidades.map((s) => entero(s.valores[i])),
                           entero(serie.visitas.reduce((a, v) => a + (v[i] ?? 0), 0))]).reverse()} />
          </Tarjeta>
        )}
        {serie && (
          <Tarjeta className="p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Ceja>Ingreso por día (con IVA)</Ceja>
              {serie.ingreso.length > 1 && <Leyenda items={serie.ingreso.map((s) => ({ nombre: s.nombre, color: s.color, tipo: "barra" as const }))} />}
            </div>
            <div className="mt-3">
              <Columnas etiquetas={serie.fechas.map((f) => dia(f))} series={serie.ingreso}
                        formato={(v) => pesos(Math.round(v))} formatoEje={(v) => pesosCorto(v)}
                        etiquetaLarga={(i) => serie.fechas[i]} />
            </div>
            <TablaGemela columnas={["Fecha", ...serie.ingreso.map((s) => s.nombre)]}
                         filas={serie.fechas.map((f, i) => [f, ...serie.ingreso.map((s) => pesos(s.valores[i]))]).reverse()} />
          </Tarjeta>
        )}
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Comparativa m={m} />
        <div className="space-y-4">
          <Tarjeta className="p-4">
            <Ceja>Embudo · 30 días (ML)</Ceja>
            <div className="mt-2 flex items-end gap-3">
              <div><div className="text-2xl font-bold text-slate-900">{entero(m.embudo?.visitas_30d)}</div><div className="text-[11px] text-slate-400">visitas</div></div>
              <ArrowRight className="mb-3 text-slate-300" size={18} />
              <div><div className="text-2xl font-bold text-emerald-600">{entero(m.embudo?.unidades_30d)}</div><div className="text-[11px] text-slate-400">unidades</div></div>
              <div className="mb-1 ml-auto rounded-lg bg-slate-50 px-2.5 py-1 text-right">
                <div className="text-sm font-bold text-slate-800">{m.embudo?.visitas_30d ? pct((m.embudo.unidades_30d ?? 0) / m.embudo.visitas_30d, 2) : "—"}</div>
                <div className="text-[10px] text-slate-400">conversión</div>
              </div>
            </div>
          </Tarjeta>
          <MarcoTabla titulo="Otros canales">
            <table className="tabla-lab">
              <thead><tr><Th className="pl-4">Canal</Th><Th alinear="der">Publicaciones</Th><Th alinear="der">Activas</Th><Th alinear="der" lado="der">Último dato</Th></tr></thead>
              <tbody>
                {Object.entries(m.por_canal ?? {}).map(([c, x]) => {
                  const d = diasDesde(x.frescura);
                  const tema = TEMA_CANAL[c as Canal];
                  return (
                    <tr key={c}>
                      <td className="pl-4"><span className="inline-flex items-center gap-1.5 font-medium text-slate-700"><span className="h-2 w-2 rounded-full" style={{ background: tema?.color ?? "#cbd5e1" }} />{tema?.nombre ?? c}</span></td>
                      <td className="num">{entero(x.publicaciones)}</td>
                      <td className="num">{entero(x.activas)}</td>
                      <td className={`num ${d !== null && d > 3 ? "font-semibold text-amber-700" : "text-slate-500"}`} title={d !== null && d > 3 ? "Congelado: es lo que hay en caché." : undefined}>
                        {dia(x.frescura)}{d !== null && d > 3 ? ` · hace ${d} d` : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </MarcoTabla>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Tarjeta className="p-4">
          <div className="flex items-center justify-between">
            <Ceja>Elasticidades de las publicaciones</Ceja>
            <span className="text-[11px] text-slate-500">global <b className="tabular-nums text-slate-800">{m.elasticidad?.global?.toFixed(2) ?? "—"}</b></span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">β de unidades al precio. Más negativo = más sensible: bajar el precio mueve más volumen.</p>
          <div className="mt-3">
            {histo.length ? (
              <Columnas etiquetas={histo.map((h) => h.desde.toFixed(1))} series={[{ nombre: "Publicaciones", color: SERIE.principal, valores: histo.map((h) => h.n) }]}
                        formato={(v) => entero(v)} formatoEje={(v) => `${v}`} alto={140} cadaCuanto={2}
                        etiquetaLarga={(i) => `β entre ${histo[i].desde.toFixed(1)} y ${histo[i].hasta.toFixed(1)}`}
                        destacarIndice={m.elasticidad?.global != null ? histo.findIndex((h) => m.elasticidad.global! >= h.desde && m.elasticidad.global! < h.hasta) : null} />
            ) : <Vacio texto="Sin histograma." />}
          </div>
        </Tarjeta>
        <MarcoTabla titulo="β por categoría raíz">
          <div className="max-h-72 overflow-y-auto">
            <table className="tabla-lab">
              <thead><tr><Th className="pl-4">Categoría</Th><Th alinear="der">β</Th><Th alinear="der" lado="der">Items</Th></tr></thead>
              <tbody>
                {[...(m.elasticidad?.por_categoria ?? [])].sort((a, b) => (a.beta ?? 0) - (b.beta ?? 0)).map((c) => (
                  <tr key={c.categoria}>
                    <td className="pl-4"><div className="text-slate-700">{c.nombre ?? c.categoria}</div><div className="font-mono text-[10.5px] text-slate-400">{c.categoria}</div></td>
                    <td className="num font-semibold text-slate-800">{c.beta?.toFixed(2) ?? "—"}</td>
                    <td className="num text-slate-500">{entero(c.n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </MarcoTabla>
      </div>

      <MarcoTabla
        titulo={<span>Palancas · reactivar FULL <span className="font-normal text-slate-400">— pausadas sin stock en FULL con piezas libres en Odoo</span></span>}
        derecha={<span className="text-[11px] text-slate-500">≈ <b className="tabular-nums text-rose-600">{cifra(perdidas)}</b> ventas perdidas / día</span>}>
        {palancas.length === 0 ? <Vacio texto="Sin palancas para esta cuenta." /> : (
          <div className="max-h-[28rem] overflow-y-auto">
            <table className="tabla-lab min-w-[760px]">
              <thead>
                <tr>
                  <Th className="pl-4">SKU</Th><Th>Publicación</Th><Th>Cuenta</Th>
                  <Th alinear="der" ayuda="Unidades por día que vendía antes de pausarse (base sin censura).">Ventas perdidas / día</Th>
                  <Th alinear="der" ayuda="free_qty de Odoo: físico menos reservado.">Libre en Odoo</Th>
                  <Th lado="der">Motivo</Th>
                </tr>
              </thead>
              <tbody>
                {palancas.map((p) => (
                  <tr key={`${p.cuenta}-${p.listing_id}`}>
                    <td className="whitespace-nowrap pl-4 font-mono text-[11.5px] font-semibold text-slate-700">
                      {p.id ? <Link href={`/historial?id=${encodeURIComponent(p.id)}`} className="hover:text-indigo-600 hover:underline">{p.sku ?? "—"}</Link> : (p.sku ?? "—")}
                    </td>
                    <td><div className="max-w-[300px] truncate text-slate-700" title={p.titulo ?? ""}>{p.titulo ?? <span className="font-mono text-slate-400">{p.listing_id}</span>}</div></td>
                    <td><PuntoCuenta canal="mercado_libre" cuenta={p.cuenta} /></td>
                    <td className="num font-semibold text-rose-600">{cifra(p.ventas_perdidas_dia)}</td>
                    <td className="num text-slate-800">{entero(p.stock_odoo)}</td>
                    <td className="text-[11.5px] text-slate-500">{p.motivo ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </MarcoTabla>
    </>
  );
}
