"use client";

/**
 * Packing list: 100 SKUs publicados ubicados en el packing list de SU contenedor
 * y su costo por pieza calculado de cinco maneras, lado a lado (DISENO §3).
 *
 * Regla de Brandon: el costo del producto en el packing list NO aplica; lo que
 * cuesta es el contenedor, 525,000 MXN sin IVA. El recomendado es el volumétrico
 * real — 525,000 × m³ de la pieza ÷ m³ TOTALES del archivo — porque el Σ del
 * índice cuadra exacto con la columna de volumen y, por contenedor, Σ(costo ×
 * piezas) = 525,000. La tarifa de 7,500/m³ del panel equivale a suponer 70 m³: con
 * un contenedor de 58 m³ subestima 18 %; con uno de 81, sobreestima 16 %.
 */
import { useMemo, useState } from "react";
import { Encabezado } from "@/components/Marco";
import { Buscador, CajaError, Cargando, Ceja, Chip, Kpi, MarcoTabla, Selector, SinDato, Tarjeta, Th, useRetrasado, Vacio, Ayuda } from "@/components/ui";
import { ChipEmpate, TagValidado } from "@/components/Chips";
import { cifra, entero, pct, pctFirmado, pesos, tonoMargen } from "@/lib/formato";
import { METODOS_COSTO } from "@/lib/vocabulario";
import type { Contenedor, FilaPacking, Packing } from "@/lib/tipos";
import { aPagina } from "@/lib/api";
import { usePedido } from "@/lib/usePedido";

const TARIFA = 7500;

const MOTIVO_SALTO: Record<string, string> = {
  sin_sku_en_odoo: "El SKU no existe en Odoo",
  sin_container_numbers: "Odoo no dice de qué contenedor vino",
  sin_archivo: "No se encontró el packing list del contenedor",
  sin_renglon: "El archivo existe pero ningún renglón empata",
  ambiguo: "Varios renglones empatan igual de bien",
  padre_con_muchas_variantes: "Es un padre con muchas variantes",
};

function mediana(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Diferencia del costo del laboratorio (volumétrico) contra lo que kubera tiene hoy. */
function diferencia(f: FilaPacking): number | null {
  const lab = f.costos?.volumetrico_real; const k = f.kubera?.costo_total;
  if (lab == null || k == null || k === 0) return null;
  return (lab - k) / k;
}

export default function PaginaPacking() {
  const { datos: packing, error, cargando, recargar } = usePedido<Packing>("/packing");
  const { datos: contRaw } = usePedido<unknown>("/contenedores");
  const contenedores = contRaw ? aPagina<Contenedor>(contRaw).filas : [];
  const [cont, setCont] = useState("");
  const [q, setQ] = useState("");
  const [orden, setOrden] = useState("-unidades");
  const qq = useRetrasado(q, 200).trim().toLowerCase();
  const costoContenedor = packing?.contenedor_mxn ?? 525000;

  const filas = useMemo(() => {
    const xs = (packing?.filas ?? []).filter((f) =>
      (!cont || f.archivo?.contenedor === cont || f.odoo?.codigos?.includes(cont))
      && (!qq || f.sku.toLowerCase().includes(qq) || (f.titulo ?? "").toLowerCase().includes(qq)));
    const desc = orden.startsWith("-");
    const campo = desc ? orden.slice(1) : orden;
    const acc = (f: FilaPacking): number | null =>
      campo === "diferencia" ? diferencia(f) : campo === "margen" ? f.margen_con_525k : campo === "cbm" ? f.cbm_pieza
        : campo === "volumetrico" ? f.costos?.volumetrico_real : f.unidades_30d;
    return [...xs].sort((a, b) => {
      const x = acc(a); const y = acc(b);
      if (x == null) return 1;
      if (y == null) return -1;
      return desc ? y - x : x - y;
    });
  }, [packing, cont, qq, orden]);

  const medM3 = mediana(contenedores.map((c) => c.costo_m3).filter((x): x is number => x != null));
  const fueraRango = contenedores.filter((c) => !c.rango_ok).length;
  const validados = (packing?.filas ?? []).filter((f) => f.kubera?.validado).length;
  const difMed = mediana((packing?.filas ?? []).map(diferencia).filter((x): x is number => x != null));

  return (
    <>
      <Encabezado titulo="Packing list"
                  descripcion={<>El costo por pieza sale del contenedor ({pesos(costoContenedor)} sin IVA) repartido por volumen del packing list real, no del precio del proveedor.</>} />

      {error && <div className="mb-3"><CajaError mensaje={error} onReintentar={recargar} /></div>}
      {!packing && cargando && <Cargando />}

      {packing && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="SKUs con renglón ubicado" valor={entero(packing.filas.length)} tono="text-indigo-600" pie={`${entero(validados)} con costo VALIDADO en el panel`} />
            <Kpi label="Contenedores" valor={entero(contenedores.length)} pie={fueraRango ? `${fueraRango} fuera del rango normal de m³` : "todos en rango normal"} />
            <Kpi label="$ / m³ real (mediana)" valor={medM3 == null ? "—" : pesos(Math.round(medM3))}
                 tono={medM3 != null && medM3 > TARIFA ? "text-rose-600" : "text-emerald-600"}
                 pie={medM3 == null ? "" : `vs ${pesos(TARIFA)} del panel (${pctFirmado((medM3 - TARIFA) / TARIFA, 0)})`} />
            <Kpi label="Diferencia vs kubera (mediana)" valor={pctFirmado(difMed, 0)} tono="text-indigo-600"
                 pie="costo volumétrico vs costo_total de kubera" />
          </div>

          <Tarjeta className="mb-4 p-4">
            <Ceja>Método recomendado · volumétrico real</Ceja>
            <p className="mt-2 font-mono text-[12.5px] text-slate-800">costo por pieza = 525,000 × m³ de la pieza ÷ m³ totales del packing list</p>
            <ul className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-[12px] text-slate-500 md:grid-cols-2">
              <li>· Por contenedor, Σ(costo × piezas) = 525,000: el contenedor se reparte completo, ni un peso de más.</li>
              <li>· La tarifa de 7,500/m³ equivale a suponer 70 m³; con otro volumen real se equivoca en proporción.</li>
              <li>· Cajas mixtas: se reparte igual por pieza del grupo (regla vigente de Brandon).</li>
              <li>· W/M (tonelada-flete) corrige productos densos; FOB es sólo informativo (40 % sin USD).</li>
            </ul>
          </Tarjeta>

          {(packing.saltados?.length ?? 0) > 0 && (
            <Tarjeta className="mb-4 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Ceja>Saltados · {entero(packing.saltados!.length)} SKUs que no se pudieron ubicar</Ceja>
                <span className="text-[11px] text-slate-400">se pasó al siguiente más vendido</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(packing.saltados!.reduce<Record<string, number>>((a, s) => { a[s.motivo] = (a[s.motivo] ?? 0) + 1; return a; }, {}))
                  .sort((a, b) => b[1] - a[1])
                  .map(([m, n]) => <Chip key={m} tono="slate" titulo={m}>{MOTIVO_SALTO[m] ?? m} · {n}</Chip>)}
              </div>
              <details className="mt-2">
                <summary className="cursor-pointer text-[11px] font-semibold text-indigo-600">Ver la lista</summary>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-slate-500">
                  {packing.saltados!.map((s) => <span key={s.sku} title={MOTIVO_SALTO[s.motivo] ?? s.motivo}>{s.sku}</span>)}
                </div>
              </details>
            </Tarjeta>
          )}

          <MarcoTabla titulo="Contenedores" derecha={<span className="text-[11px] text-slate-400">rango normal 55–76 m³</span>}>
            {contenedores.length === 0 ? <Vacio texto="Sin contenedores." /> : (
              <table className="tabla-lab min-w-[1000px]">
                <thead>
                  <tr>
                    <Th className="pl-4">Contenedor</Th><Th>Archivo</Th>
                    <Th alinear="der" ayuda="Σ de la columna de volumen del packing list.">m³ totales</Th>
                    <Th alinear="der" ayuda="525,000 ÷ m³ totales. Compárese contra 7,500.">$ / m³ real</Th>
                    <Th alinear="der">Piezas</Th><Th alinear="der">Peso (kg)</Th><Th alinear="der">USD</Th>
                    <Th>Rango</Th>
                    <Th alinear="der" ayuda="Renglones del archivo; entre paréntesis los que no traen m³.">Renglones</Th>
                    <Th alinear="der">Cajas mixtas</Th>
                    <Th alinear="der" lado="der" ayuda="SKUs de esta muestra que salieron de este contenedor.">SKUs</Th>
                  </tr>
                </thead>
                <tbody>
                  {contenedores.map((c) => (
                    <tr key={c.codigo} className="clicable" onClick={() => setCont((x) => (x === c.codigo ? "" : c.codigo))}
                        style={cont === c.codigo ? { background: "#EEF0FF" } : undefined}>
                      <td className="whitespace-nowrap pl-4 font-mono text-[12px] font-semibold text-slate-800">{c.codigo}</td>
                      <td><div className="max-w-[260px] truncate text-[12px] text-slate-600" title={c.archivo ?? ""}>{c.archivo ?? "—"}</div></td>
                      <td className="num font-semibold text-slate-800">{cifra(c.total_cbm)}</td>
                      <td className="num">
                        <div className="font-semibold text-slate-900">{pesos(c.costo_m3 == null ? null : Math.round(c.costo_m3))}</div>
                        {c.costo_m3 != null && <div className={`text-[10.5px] ${c.costo_m3 > TARIFA ? "text-rose-600" : "text-emerald-600"}`}>{pctFirmado((c.costo_m3 - TARIFA) / TARIFA, 0)} vs 7,500</div>}
                      </td>
                      <td className="num text-slate-700">{entero(c.total_piezas)}</td>
                      <td className="num text-slate-700">{entero(c.total_peso_kg)}</td>
                      <td className="num text-slate-700">{c.total_usd == null ? <SinDato /> : `US$${entero(c.total_usd)}`}</td>
                      <td>{c.rango_ok ? <Chip tono="emerald">en rango</Chip> : <Chip tono="amber" titulo="Fuera de 55–76 m³: revisar el archivo antes de confiar en el $/m³.">fuera de rango</Chip>}</td>
                      <td className="num text-slate-700">{entero(c.renglones)}{c.renglones_sin_cbm ? <span className="text-amber-700"> ({c.renglones_sin_cbm})</span> : ""}</td>
                      <td className="num text-slate-700">{entero(c.cajas_mixtas)}</td>
                      <td className="num font-semibold text-slate-800">{entero(c.skus_en_lote)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </MarcoTabla>

          <div className="mb-3 mt-5 flex flex-wrap items-center gap-2">
            <Selector etiqueta="Contenedor" valor={cont} onCambio={setCont}
                      opciones={[{ id: "", label: "Todos" }, ...contenedores.map((c) => ({ id: c.codigo, label: c.codigo }))]} />
            <Selector etiqueta="Orden" valor={orden} onCambio={setOrden} opciones={[
              { id: "-unidades", label: "Más vendidos" }, { id: "-diferencia", label: "Mayor diferencia vs kubera" },
              { id: "diferencia", label: "Menor diferencia vs kubera" }, { id: "margen", label: "Menor margen con 525k" },
              { id: "-volumetrico", label: "Costo volumétrico mayor" }, { id: "-cbm", label: "Más m³ por pieza" },
            ]} />
            <Buscador valor={q} onCambio={setQ} />
          </div>

          <MarcoTabla titulo={`SKUs ubicados · ${entero(filas.length)}`} derecha={<span className="text-[11px] text-slate-400">costos sin IVA · la columna resaltada es la recomendada</span>}>
            {filas.length === 0 ? <Vacio texto="Ningún SKU con esos filtros." /> : (
              <table className="tabla-lab min-w-[1750px]">
                <thead>
                  <tr>
                    <Th className="pl-4">SKU</Th><Th>Producto</Th>
                    <Th ayuda="container_numbers de Odoo.">Contenedor Odoo</Th>
                    <Th ayuda="Archivo del packing list y el renglón donde se encontró.">Archivo · fila</Th>
                    <Th ayuda="Cómo se ubicó el renglón: Ferraforme → foto idéntica → foto parecida (dHash) → IA.">Empate</Th>
                    <Th alinear="der">Cajas</Th>
                    <Th alinear="der" ayuda="Piezas que comparten la caja. En caja mixta se reparte igual por pieza.">Piezas grupo</Th>
                    <Th alinear="der" ayuda="m³ por pieza = m³ de la caja ÷ piezas del grupo.">m³ / pieza</Th>
                    {METODOS_COSTO.map((mt) => (
                      <th key={mt.clave} scope="col"
                          className={`whitespace-nowrap px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider ${mt.clave === "volumetrico_real" ? "bg-indigo-50 text-indigo-700" : "text-slate-500"}`}>
                        <Ayuda texto={mt.ayuda} lado="der">{mt.label}</Ayuda>
                      </th>
                    ))}
                    <Th alinear="der" ayuda="costo_total de kubera hoy (producto + m³ a 7,500).">Costo kubera</Th>
                    <Th alinear="der" ayuda="(volumétrico − costo kubera) ÷ costo kubera.">Diferencia</Th>
                    <Th alinear="der" lado="der" ayuda="Margen al precio cobrado con el costo volumétrico vs con el costo del panel.">Margen 525k · panel</Th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map((f) => {
                    const d = diferencia(f);
                    return (
                      <tr key={f.sku}>
                        <td className="whitespace-nowrap pl-4 font-mono text-[11.5px] font-semibold text-slate-700">{f.sku}</td>
                        <td>
                          <div className="max-w-[240px] truncate text-slate-700" title={f.titulo ?? ""}>{f.titulo ?? "—"}</div>
                          <div className="text-[10.5px] text-slate-400">{pesos(f.precio_cobrado)} · {entero(f.unidades_30d)} ventas 30 d</div>
                        </td>
                        <td className="font-mono text-[11px] text-slate-600" title={f.odoo?.container_numbers ?? ""}>{f.odoo?.codigos?.join(", ") || <SinDato />}</td>
                        <td>
                          <div className="max-w-[200px] truncate text-[11.5px] text-slate-600" title={f.archivo?.nombre ?? ""}>{f.archivo?.nombre ?? "—"}</div>
                          <div className="text-[10.5px] text-slate-400">fila {f.fila ?? "—"}</div>
                        </td>
                        <td><ChipEmpate metodo={f.empate.metodo} distancia={f.empate.distancia} /></td>
                        <td className="num text-slate-700">{entero(f.cajas)}</td>
                        <td className="num text-slate-700">
                          {entero(f.piezas_grupo)}
                          {f.caja_mixta && <div><Chip tono="violet" titulo="Caja con varios SKUs: reparto igual por pieza.">mixta</Chip></div>}
                        </td>
                        <td className="num text-slate-700">{f.cbm_pieza == null ? "—" : f.cbm_pieza.toFixed(4)}</td>
                        {METODOS_COSTO.map((mt) => (
                          <td key={mt.clave} className={`num ${mt.clave === "volumetrico_real" ? "bg-indigo-50/70 font-bold text-indigo-800" : "text-slate-600"}`}>
                            {f.costos?.[mt.clave] == null ? <span className="text-slate-300">—</span> : pesos(f.costos[mt.clave])}
                          </td>
                        ))}
                        <td className="num">
                          <div className="text-slate-800">{pesos(f.kubera?.costo_total ?? null)}</div>
                          {f.kubera?.validado && <div className="mt-0.5"><TagValidado por={f.kubera.revisado_por} /></div>}
                        </td>
                        <td className={`num font-semibold ${d == null ? "text-slate-400" : Math.abs(d) < 0.1 ? "text-slate-600" : d < 0 ? "text-emerald-600" : "text-rose-600"}`}>{pctFirmado(d, 0)}</td>
                        <td className="num">
                          <span className={`font-semibold ${tonoMargen(f.margen_con_525k)}`}>{pct(f.margen_con_525k)}</span>
                          <span className="mx-1 text-slate-300">·</span>
                          <span className={tonoMargen(f.margen_panel)}>{pct(f.margen_panel)}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </MarcoTabla>
        </>
      )}
    </>
  );
}
