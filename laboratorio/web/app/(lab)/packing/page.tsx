"use client";

/**
 * Packing list: 100 SKUs publicados ubicados en el packing list de SU contenedor
 * y su costo por pieza calculado de varias maneras, lado a lado (DISENO §3).
 *
 * Regla de Brandon: el costo del producto en el packing list NO aplica; lo que
 * cuesta es el contenedor, 525,000 MXN sin IVA. El recomendado es el volumétrico
 * real — 525,000 × m³ de la pieza ÷ m³ TOTALES del archivo — porque el Σ del
 * índice cuadra exacto con la columna de volumen y, por contenedor, Σ(costo ×
 * piezas) = 525,000. Excepción: si el contenedor se llenó por PESO (≥ 90 % de la
 * carga útil), el recomendado es el W/M de contenedor completo (kg/m³ = carga
 * útil ÷ 70 m³): ahí los densos consumieron la capacidad y los ligeros no deben
 * pagar por ellos. La tarifa de 7,500/m³ del panel equivale a suponer 70 m³.
 */
import { useMemo, useState } from "react";
import { Encabezado } from "@/components/Marco";
import { Buscador, CajaError, Cargando, Ceja, Chip, Kpi, MarcoTabla, Selector, SinDato, Tarjeta, Th, useRetrasado, Vacio, Ayuda } from "@/components/ui";
import { ChipEmpate, TagValidado } from "@/components/Chips";
import GraficaCostoM3, { claveCont } from "@/components/GraficaCostoM3";
import { cifra, entero, pct, pctFirmado, pesos, tonoMargen } from "@/lib/formato";
import { METODOS_COSTO, MOTIVO_SALTO } from "@/lib/vocabulario";
import type { Contenedor, FilaPacking, Packing } from "@/lib/tipos";
import { aPagina } from "@/lib/api";
import { usePedido } from "@/lib/usePedido";

const TARIFA = 7500;

type ColumnaCosto = { clave: string; label: string; ayuda: string; valor: (f: FilaPacking) => number | null };

/** Las cinco del contrato + el W/M de contenedor lleno, junto al W/M clásico. */
const COLUMNAS_COSTO: ColumnaCosto[] = METODOS_COSTO.flatMap((mt): ColumnaCosto[] => {
  const col: ColumnaCosto = { clave: mt.clave, label: mt.label, ayuda: mt.ayuda, valor: (f) => f.costos?.[mt.clave] ?? null };
  if (mt.clave !== "peso_volumen_wm") return [col];
  return [col, {
    clave: "wm_fcl", label: "W/M lleno",
    ayuda: "W/M de contenedor completo: kg/m³ = carga útil ÷ 70 m³ (26,500 kg ÷ 70). RECOMENDADO cuando el contenedor se llenó por peso.",
    valor: (f) => f.costo_wm_fcl ?? null,
  }];
});

function mediana(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function nSkus(c: Contenedor): number {
  return Array.isArray(c.skus_en_lote) ? c.skus_en_lote.length : Number(c.skus_en_lote ?? 0);
}

export default function PaginaPacking() {
  const { datos: packing, error, cargando, recargar } = usePedido<Packing>("/packing", undefined, { nulo404: true });
  const { datos: contRaw, listo: contListo } = usePedido<unknown>("/contenedores", undefined, { nulo404: true });
  const contenedores = useMemo(() => (contRaw ? aPagina<Contenedor>(contRaw).filas : []), [contRaw]);
  const [cont, setCont] = useState("");
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState("-unidades");
  const qq = useRetrasado(q, 200).trim().toLowerCase();
  const costoContenedor = packing?.contenedor_mxn ?? 525000;

  /** código (y sus alias) → contenedor, para saber si el de cada SKU va lleno por peso. */
  const porCodigo = useMemo(() => {
    const m = new Map<string, Contenedor>();
    for (const c of contenedores) for (const k of [claveCont(c), c.codigo, ...(c.codigos ?? [])]) if (!m.has(k)) m.set(k, c);
    return m;
  }, [contenedores]);
  // El archivo (file_id) primero: hay códigos de contenedor con dos packing lists.
  const contDe = (f: FilaPacking): Contenedor | undefined =>
    (f.archivo?.file_id ? porCodigo.get(f.archivo.file_id) : undefined)
    ?? (f.archivo?.contenedor ? porCodigo.get(f.archivo.contenedor) : undefined)
    ?? f.odoo?.codigos?.map((k) => porCodigo.get(k)).find(Boolean);
  const nombreCont = (clave: string) => porCodigo.get(clave)?.codigo ?? clave;
  const porPeso = (f: FilaPacking) => !!contDe(f)?.limitado_por_peso && f.costo_wm_fcl != null;
  const recomendada = (f: FilaPacking) => (porPeso(f) ? "wm_fcl" : "volumetrico_real");
  const costoRec = (f: FilaPacking): number | null => (porPeso(f) ? f.costo_wm_fcl ?? null : f.costos?.volumetrico_real ?? null);
  const diferencia = (f: FilaPacking, base: "total" | "cbm" = "total"): number | null => {
    const lab = costoRec(f); const k = base === "total" ? f.kubera?.costo_total : f.kubera?.costo_cbm;
    if (lab == null || k == null || k === 0) return null;
    return (lab - k) / k;
  };

  const filas = useMemo(() => {
    const xs = (packing?.filas ?? []).filter((f) =>
      (!cont || (f.archivo?.file_id ? f.archivo.file_id === cont : f.archivo?.contenedor === cont || !!f.odoo?.codigos?.includes(cont)))
      && (!qq || f.sku.toLowerCase().includes(qq) || (f.titulo ?? "").toLowerCase().includes(qq)));
    const desc = orden.startsWith("-");
    const campo = desc ? orden.slice(1) : orden;
    const acc = (f: FilaPacking): number | null =>
      campo === "diferencia" ? diferencia(f) : campo === "margen" ? f.margen_con_525k : campo === "cbm" ? f.cbm_pieza
        : campo === "costo" ? costoRec(f) : f.unidades_30d;
    return [...xs].sort((a, b) => {
      const x = acc(a); const y = acc(b);
      if (x == null) return 1;
      if (y == null) return -1;
      return desc ? y - x : x - y;
    });
  }, [packing, cont, qq, orden, porCodigo]); // eslint-disable-line react-hooks/exhaustive-deps

  const medM3 = mediana(contenedores.map((c) => c.costo_m3).filter((x): x is number => x != null));
  const rangoM3 = contenedores.map((c) => c.costo_m3).filter((x): x is number => x != null);
  const fueraRango = contenedores.filter((c) => !c.rango_ok).length;
  const llenosPorPeso = contenedores.filter((c) => c.limitado_por_peso);
  const validados = (packing?.filas ?? []).filter((f) => f.kubera?.validado).length;
  const difMed = mediana((packing?.filas ?? []).map((f) => diferencia(f)).filter((x): x is number => x != null));
  const difMedCbm = mediana((packing?.filas ?? []).map((f) => diferencia(f, "cbm")).filter((x): x is number => x != null));
  const res = packing?.resumen;
  const saltados = packing?.saltados ?? [];

  return (
    <>
      <Encabezado titulo="Packing list"
                  descripcion={<>El costo por pieza sale del contenedor ({pesos(costoContenedor)} sin IVA) repartido por el volumen del packing list real, no del precio del proveedor.{packing ? ` ${entero(packing.filas.length)} SKUs publicados, ubicados renglón por renglón.` : ""}</>} />

      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}
      {!packing && cargando && <Cargando texto="Leyendo packing lists…" />}
      {!packing && !cargando && !error && <Tarjeta><Vacio texto="Todavía no hay packing: la etapa packing100 no ha corrido." /></Tarjeta>}

      {packing && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="SKUs con renglón ubicado" valor={entero(packing.filas.length)} tono="text-indigo-600" pie={`${entero(validados)} con costo VALIDADO en el panel`} />
            <Kpi label="Contenedores" valor={entero(contenedores.length)}
                 pie={[fueraRango ? `${fueraRango} fuera de rango de m³` : "todos en rango de m³", llenosPorPeso.length ? `${llenosPorPeso.length} llenos por peso` : ""].filter(Boolean).join(" · ")} />
            <Kpi label="$ / m³ real (mediana)" valor={medM3 == null ? "—" : pesos(Math.round(medM3))}
                 pie={medM3 == null ? "" : `vs ${pesos(TARIFA)} del panel (${pctFirmado((medM3 - TARIFA) / TARIFA, 1)})`} />
            <Kpi label="Margen mediano" valor={pct(res?.margen_mediano_525k ?? null)} tono={tonoMargen(res?.margen_mediano_525k ?? null)}
                 ayuda="Al precio cobrado hoy, con la comisión real de cada publicación y el PEOR caso de envío: en promociones abajo de $299 con regular de $299 o más, ML cobra la tarifa del precio regular (pasa en ~30 % de esas ventas). El costo del panel incluye la mercancía; el del laboratorio no (regla de Brandon)."
                 pie={[
                   typeof res?.margen_mediano_525k_sin_ancla === "number" ? `peor caso de envío · ${pct(res.margen_mediano_525k_sin_ancla)} sin la tarifa del regular` : "con 525k",
                   res?.margen_mediano_panel != null ? `${pct(res.margen_mediano_panel)} con el costo del panel` : "",
                 ].filter(Boolean).join(" · ")} />
          </div>

          <Tarjeta className="mb-4 p-4 sm:p-5">
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
              <div className="min-w-0">
                <Ceja>Método recomendado</Ceja>
                <h2 className="mt-1 text-[15px] font-bold text-slate-900">Volumétrico real</h2>
                <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 font-mono text-[12.5px] leading-relaxed text-slate-800">
                  costo por pieza = 525,000 × m³ de la pieza ÷ m³ totales del contenedor
                </p>
                <ul className="mt-3 space-y-1.5 text-[12.5px] leading-snug text-slate-600">
                  <li><b className="font-semibold text-slate-800">El contenedor se reparte completo.</b> Por contenedor, Σ(costo × piezas) = 525,000: ni un peso de más ni de menos.</li>
                  <li><b className="font-semibold text-slate-800">Los m³ son los del archivo.</b> El Σ del índice cuadra con la columna de volumen del packing list; la tarifa de 7,500/m³ equivale a suponer 70 m³ y se equivoca en proporción cuando el contenedor trae otro volumen.</li>
                  <li><b className="font-semibold text-slate-800">Si el contenedor va lleno por peso</b> (≥ 90 % de la carga útil de 26,500 kg), se usa el <b className="font-semibold text-slate-800">W/M de contenedor completo</b>: cada pieza paga por max(m³, kg ÷ 378.6), porque ahí los productos densos agotaron la capacidad antes que el volumen.</li>
                  <li>Cajas mixtas: se reparte igual por pieza del grupo (regla vigente de Brandon). FOB y 70/30 son informativos.</li>
                </ul>
                {llenosPorPeso.length > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11.5px] text-slate-500">
                    Llenos por peso:
                    {llenosPorPeso.map((c) => (
                      <button key={claveCont(c)} type="button" onClick={() => setCont((x) => (x === claveCont(c) ? "" : claveCont(c)))}
                              className="rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 font-mono text-[10.5px] font-semibold text-teal-800 hover:border-teal-400"
                              title={`${pct(c.peso_vs_carga_util, 0)} de la carga útil · W/M lleno ${pesos(Math.round(c.costo_m3_wm_fcl ?? 0))}/m³ vs ${pesos(Math.round(c.costo_m3 ?? 0))}/m³ volumétrico`}>
                        {c.codigo}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <Ceja>$ / m³ real de cada contenedor</Ceja>
                  {rangoM3.length > 0 && <span className="text-[11px] tabular-nums text-slate-500">de {pesos(Math.round(Math.min(...rangoM3)))} a {pesos(Math.round(Math.max(...rangoM3)))} · mediana {pesos(Math.round(medM3 ?? 0))}</span>}
                </div>
                <div className="mt-2">
                  {!contListo ? <Cargando texto="Leyendo contenedores…" />
                    : <GraficaCostoM3 contenedores={contenedores} elegido={cont} onElegir={(k) => setCont((x) => (x === k ? "" : k))} />}
                </div>
                <p className="mt-1 text-[11px] text-slate-500">Clic en un punto para filtrar sus SKUs. La tabla de contenedores de abajo trae los mismos números.</p>
              </div>
            </div>
          </Tarjeta>

          {saltados.length > 0 && (
            <Tarjeta className="mb-4 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Ceja>Saltados · {entero(saltados.length)} SKUs que no se pudieron ubicar</Ceja>
                <span className="text-[11px] text-slate-500">se pasó al siguiente más vendido</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(saltados.reduce<Record<string, number>>((a, s) => { a[s.motivo] = (a[s.motivo] ?? 0) + 1; return a; }, {}))
                  .sort((a, b) => b[1] - a[1])
                  .map(([m, n]) => <Chip key={m} tono="slate" titulo={m}>{MOTIVO_SALTO[m] ?? m} · {n}</Chip>)}
              </div>
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] font-semibold text-indigo-600">Ver la lista</summary>
                <div className="mt-2 max-h-56 overflow-auto rounded-lg border border-slate-100">
                  <table className="w-full text-[11.5px]">
                    <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                      <tr><th scope="col" className="px-2.5 py-1.5 text-left">SKU</th><th scope="col" className="px-2.5 py-1.5 text-left">Motivo</th><th scope="col" className="px-2.5 py-1.5 text-left">Detalle</th></tr>
                    </thead>
                    <tbody>
                      {saltados.map((s) => (
                        <tr key={s.sku} className="border-t border-slate-50">
                          <td className="whitespace-nowrap px-2.5 py-1 font-mono text-slate-700">{s.sku}</td>
                          <td className="whitespace-nowrap px-2.5 py-1 text-slate-600">{MOTIVO_SALTO[s.motivo] ?? s.motivo}</td>
                          <td className="px-2.5 py-1 text-slate-500">{s.detalle ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </Tarjeta>
          )}

          <MarcoTabla titulo={`Contenedores · ${entero(contenedores.length)}`} derecha={<span className="text-[11px] text-slate-500">rango normal 55–76 m³ · * peso incompleto en el archivo · clic para filtrar sus SKUs</span>}>
            {!contListo ? <Cargando /> : contenedores.length === 0 ? <Vacio texto="Sin contenedores." /> : (
              <table className="tabla-lab min-w-[1180px]">
                <thead>
                  <tr>
                    <Th className="pl-4">Contenedor</Th><Th>Archivo</Th>
                    <Th alinear="der" ayuda="Σ de la columna de volumen del packing list.">m³ totales</Th>
                    <Th alinear="der" ayuda="525,000 ÷ m³ totales. Compárese contra 7,500.">$ / m³ real</Th>
                    <Th alinear="der" ayuda="525,000 ÷ Σ max(m³, kg ÷ 378.6): el W/M de contenedor completo. Aplica cuando el contenedor va lleno por peso.">$ / m³ W/M lleno</Th>
                    <Th alinear="der" ayuda="Peso total ÷ carga útil de un 40' HC (26,500 kg). ≥ 90 % = lleno por peso.">Carga útil</Th>
                    <Th alinear="der">Piezas</Th>
                    <Th alinear="der">USD</Th>
                    <Th>Rango</Th>
                    <Th alinear="der" ayuda="Renglones del archivo; entre paréntesis los que no traen m³.">Renglones</Th>
                    <Th alinear="der">Cajas mixtas</Th>
                    <Th alinear="der" lado="der" ayuda="SKUs de esta muestra que salieron de este contenedor.">SKUs</Th>
                  </tr>
                </thead>
                <tbody>
                  {contenedores.map((c) => {
                    const skus = Array.isArray(c.skus_en_lote) ? c.skus_en_lote : [];
                    const k = claveCont(c);
                    return (
                      <tr key={k} className="clicable" tabIndex={0} aria-selected={cont === k}
                          onClick={() => setCont((x) => (x === k ? "" : k))}
                          onKeyDown={(e) => { if (e.key === "Enter") setCont((x) => (x === k ? "" : k)); }}
                          style={cont === k ? { background: "#EEF0FF" } : undefined}>
                        <td className="whitespace-nowrap pl-4 font-mono text-[12px] font-semibold text-slate-800">{c.codigo}</td>
                        <td><div className="max-w-[240px] truncate text-[12px] text-slate-600" title={c.archivo ?? ""}>{c.archivo ?? "—"}</div></td>
                        <td className="num font-semibold text-slate-800">{cifra(c.total_cbm)}</td>
                        <td className="num">
                          <div className="font-semibold text-slate-900">{pesos(c.costo_m3 == null ? null : Math.round(c.costo_m3))}</div>
                          {c.costo_m3 != null && <div className="text-[10.5px] text-slate-500">{pctFirmado((c.costo_m3 - TARIFA) / TARIFA, 1)} vs 7,500</div>}
                        </td>
                        <td className={`num ${c.limitado_por_peso ? "font-semibold text-teal-800" : "text-slate-500"}`}>{pesos(c.costo_m3_wm_fcl == null ? null : Math.round(c.costo_m3_wm_fcl))}</td>
                        <td className="num" title={c.cobertura_peso != null ? `${pct(c.cobertura_peso, 0)} del volumen trae peso en el archivo` : undefined}>
                          <div className={c.cobertura_peso != null && c.cobertura_peso < 0.8 ? "text-slate-500" : "text-slate-700"}>
                            {pct(c.peso_vs_carga_util ?? null, 0)}{c.cobertura_peso != null && c.cobertura_peso < 0.8 ? " *" : ""}
                          </div>
                          {c.limitado_por_peso && <div className="mt-0.5"><Chip tono="teal" titulo="≥ 90 % de la carga útil: el recomendado es el W/M de contenedor lleno.">lleno por peso</Chip></div>}
                        </td>
                        <td className="num text-slate-700">{entero(c.total_piezas)}</td>
                        <td className="num text-slate-700">{c.total_usd == null ? <SinDato /> : `US$${entero(c.total_usd)}`}</td>
                        <td>{c.rango_ok ? <Chip tono="slate">en rango</Chip> : <Chip tono="amber" titulo="Fuera de 55–76 m³: revisar el archivo antes de confiar en el $/m³.">fuera de rango</Chip>}</td>
                        <td className="num text-slate-700">{entero(c.renglones)}{c.renglones_sin_cbm ? <span className="text-amber-700"> ({c.renglones_sin_cbm})</span> : ""}</td>
                        <td className="num text-slate-700">{entero(c.cajas_mixtas)}</td>
                        <td className="num font-semibold text-slate-800" title={skus.join(", ")}>{entero(nSkus(c))}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </MarcoTabla>

          <div className="mb-3 mt-5 flex flex-wrap items-center gap-2">
            <Selector etiqueta="Contenedor" valor={cont} onCambio={setCont}
                      opciones={[{ id: "", label: "Todos", n: packing.filas.length }, ...contenedores.map((c) => ({
                        id: claveCont(c), label: contenedores.filter((o) => o.codigo === c.codigo).length > 1 ? `${c.codigo} · ${c.archivo ?? ""}`.slice(0, 48) : c.codigo, n: nSkus(c) }))]} />
            <Selector etiqueta="Orden" valor={orden} onCambio={setOrden} opciones={[
              { id: "-unidades", label: "Más vendidos" }, { id: "-diferencia", label: "Mayor diferencia vs kubera" },
              { id: "diferencia", label: "Menor diferencia vs kubera" }, { id: "margen", label: "Menor margen con 525k" },
              { id: "-costo", label: "Costo recomendado mayor" }, { id: "-cbm", label: "Más m³ por pieza" },
            ]} />
            <Buscador valor={q} onCambio={setQ} placeholder="Buscar SKU o título" />
            {cont && <button type="button" onClick={() => setCont("")} className="text-[12px] font-semibold text-indigo-600 hover:underline">Quitar filtro ({nombreCont(cont)})</button>}
          </div>

          <MarcoTabla titulo={`SKUs ubicados · ${entero(filas.length)}`}
                      derecha={<span className="text-[11px] text-slate-500">costos sin IVA · <span className="rounded bg-indigo-50 px-1 font-semibold text-indigo-800">resaltado</span> = el recomendado para ese contenedor</span>}>
            {filas.length === 0 ? <Vacio texto="Ningún SKU con esos filtros." /> : (
              <table className="tabla-lab min-w-[1880px]">
                <thead>
                  <tr>
                    <Th className="pl-4">SKU</Th><Th>Producto</Th>
                    <Th ayuda="container_numbers de Odoo.">Contenedor Odoo</Th>
                    <Th ayuda="Archivo del packing list y el renglón donde se encontró.">Archivo · fila</Th>
                    <Th ayuda="Cómo se ubicó el renglón: Ferraforme → foto idéntica → foto parecida (dHash) → IA.">Empate</Th>
                    <Th alinear="der">Cajas</Th>
                    <Th alinear="der" ayuda="Piezas que comparten la caja. En caja mixta se reparte igual por pieza.">Piezas grupo</Th>
                    <Th alinear="der" ayuda="m³ por pieza = m³ de la caja ÷ piezas del grupo.">m³ / pieza</Th>
                    {COLUMNAS_COSTO.map((mt) => (
                      <th key={mt.clave} scope="col"
                          className={`whitespace-nowrap px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider ${mt.clave === "volumetrico_real" || mt.clave === "wm_fcl" ? "text-indigo-700" : "text-slate-500"}`}>
                        <Ayuda texto={mt.ayuda} lado="der">{mt.label}</Ayuda>
                      </th>
                    ))}
                    <Th alinear="der" ayuda="costo_total de kubera hoy (producto + m³ a 7,500).">Costo kubera</Th>
                    <Th alinear="der" ayuda="(costo recomendado − costo_total de kubera) ÷ costo_total de kubera.">Diferencia</Th>
                    <Th alinear="der" lado="der" ayuda="Margen al precio cobrado con el costo volumétrico (525k) vs con el costo del panel.">Margen 525k · panel</Th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map((f) => {
                    const d = diferencia(f);
                    const rec = recomendada(f);
                    const lleno = porPeso(f);
                    return (
                      <tr key={f.sku}>
                        <td className="whitespace-nowrap pl-4 font-mono text-[11.5px] font-semibold text-slate-700">{f.sku}</td>
                        <td>
                          <div className="max-w-[240px] truncate text-slate-700" title={f.titulo ?? ""}>{f.titulo ?? "—"}</div>
                          <div className="text-[10.5px] text-slate-500">{pesos(f.precio_cobrado)} · {entero(f.unidades_30d)} ventas 30 d</div>
                        </td>
                        <td className="font-mono text-[11px] text-slate-600" title={f.odoo?.container_numbers ?? ""}>
                          {f.odoo?.codigos?.join(", ") || <SinDato />}
                          {lleno && <div className="mt-0.5"><Chip tono="teal" titulo="El contenedor se llenó por peso: se recomienda el W/M lleno.">lleno por peso</Chip></div>}
                        </td>
                        <td>
                          <div className="max-w-[200px] truncate text-[11.5px] text-slate-600" title={f.archivo?.nombre ?? ""}>{f.archivo?.nombre ?? "—"}</div>
                          <div className="text-[10.5px] text-slate-500" title={f.texto_fila ?? ""}>fila {f.fila ?? "—"}</div>
                        </td>
                        <td><span title={[f.empate.detalle, f.empate.segunda_opinion].filter(Boolean).join(" · ")}><ChipEmpate metodo={f.empate.metodo} distancia={f.empate.distancia} /></span></td>
                        <td className="num text-slate-700">{entero(f.cajas)}</td>
                        <td className="num text-slate-700">
                          {entero(f.piezas_grupo)}
                          {f.caja_mixta && <div><Chip tono="violet" titulo="Caja con varios SKUs: reparto igual por pieza.">mixta</Chip></div>}
                        </td>
                        <td className="num text-slate-700">{f.cbm_pieza == null ? "—" : f.cbm_pieza.toFixed(4)}</td>
                        {COLUMNAS_COSTO.map((mt) => {
                          const v = mt.valor(f);
                          const esRec = mt.clave === rec;
                          return (
                            <td key={mt.clave} className={`num ${esRec ? "bg-indigo-50/80 font-bold text-indigo-800" : "text-slate-600"}`}>
                              {v == null ? <span className="text-slate-300">—</span> : pesos(v)}
                            </td>
                          );
                        })}
                        <td className="num">
                          <div className="text-slate-800">{pesos(f.kubera?.costo_total ?? null)}</div>
                          {f.kubera?.validado && <div className="mt-0.5"><TagValidado por={f.kubera.revisado_por} /></div>}
                        </td>
                        <td className={`num font-semibold ${d == null ? "text-slate-500" : Math.abs(d) < 0.1 ? "text-slate-600" : d < 0 ? "text-emerald-600" : "text-rose-600"}`}>{pctFirmado(d, 0)}</td>
                        <td className="num">
                          <span className={`font-semibold ${tonoMargen(f.margen_con_525k)}`}>{pct(f.margen_con_525k)}</span>
                          <span className="mx-1 text-slate-300" aria-hidden>·</span>
                          <span className={tonoMargen(f.margen_panel)}>{pct(f.margen_panel)}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </MarcoTabla>

          <p className="mt-3 text-[11.5px] text-slate-500">
            Diferencia mediana del costo recomendado contra kubera: <b className="tabular-nums text-slate-700">{pctFirmado(difMed, 0)}</b> vs su costo total (producto + flete a 7,500/m³)
            {difMedCbm != null && <> y <b className="tabular-nums text-slate-700">{pctFirmado(difMedCbm, 0)}</b> vs sólo su flete</>}.
            {res?.perdiendo_con_525k != null && <> Pierden dinero {entero(res.perdiendo_con_525k)} con 525k y {entero(res.perdiendo_con_panel ?? null)} con el costo del panel.</>}
          </p>
        </>
      )}
    </>
  );
}
