"use client";

// Resumen.tsx — La cuenta del catálogo (COMPRADO − SALIÓ = QUEDA, y al lado lo que Odoo dice tener), por qué Odoo
// tiene menos y el VALOR de lo que queda a precio de Mercado Libre y de Amazon. Mismas cifras que el artifact.

import type { ReactNode } from "react";
import { THEME_FALLBACK } from "@/lib/theme";
import { FML_L, type Catalogo, type Totales, fm0, mill, nf } from "./logica";

const ML = THEME_FALLBACK.mercado_libre;
const AZ = THEME_FALLBACK.amazon;

function Paso({ titulo, cifra, pie, tono, children, punteado }: {
  titulo: string; cifra: string; pie: ReactNode; tono: string; children?: ReactNode; punteado?: boolean;
}) {
  return (
    <div className={`flex min-w-0 flex-col gap-2 rounded-2xl border bg-white p-4 shadow-card ${punteado ? "border-dashed border-slate-300" : "border-slate-200"}`}>
      <div className={`flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] ${tono}`}>
        <span className="h-2 w-2 rounded-full bg-current" aria-hidden />{titulo}
      </div>
      <div className={`text-3xl font-black tabular-nums tracking-tight ${tono}`}>{cifra}</div>
      <div className="text-xs text-slate-500">{pie}</div>
      {children && <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 border-t border-slate-100 pt-2 text-xs text-slate-600">{children}</dl>}
    </div>
  );
}

const Lin = ({ k, v }: { k: ReactNode; v: ReactNode }) => (<><dt>{k}</dt><dd className="text-right font-semibold tabular-nums text-slate-800">{v}</dd></>);
const Op = ({ c }: { c: string }) => (
  <div className="hidden items-center justify-center text-3xl font-light text-slate-300 xl:flex" aria-hidden>{c}</div>
);

export function Cuenta({ D, t }: { D: Catalogo; t: Totales }) {
  const r = D.resumen, rot = D.rot, tot = r.odoo_totales || {};
  const sal = t.ps + t.psx, queda = t.pc - sal, libre = t.lb + t.lbx;
  const demas = Math.max(0, (r.pz_salio || 0) - sal);
  const conv = Math.max(0, (r.pz_salio || 0) - (r.pz_salio_odoo || r.pz_salio || 0));
  const clases = ([["full", r.sal_full], ["parolera", r.sal_parolera], ["proveedor", r.sal_proveedor], ["directa", r.sal_directa],
    ["fba", r.sal_fba], ["canales", r.sal_canales], ["wfs", r.sal_wfs]] as [string, number][]).filter((x) => x[1]);
  let nunca = 0, nsk = 0, sinsku = 0;
  D.R.forEach((x) => {
    if (x.sin) sinsku += x.pq ?? 0;
    else if (x.pc != null && !x.en && !x.lb && !x.ps) { nunca += x.pq ?? 0; nsk++; }
  });
  return (
    <section aria-label="Comprado menos lo que salió, igual a lo que queda" className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[1fr_auto_1fr_auto_1fr_0.9fr]">
        <Paso titulo="Comprado" cifra={nf(t.pc)} tono="text-indigo-700"
          pie={<>piezas en los packing lists de <b>{nf(r.contenedores)}</b> contenedores.</>}>
          <Lin k={`Validado por bodega (${nf(r.contenedores_con_validado)} contenedores)`} v={nf(r.pz_contenido_validado)} />
          <Lin k="Sin validar: lo que declara el proveedor" v={nf(r.pz_contenido_sin_validar)} />
        </Paso>
        <Op c="−" />
        <Paso titulo="Salió de bodega" cifra={nf(sal)} tono="text-rose-600"
          pie="piezas que Odoo movió hacia afuera (movimientos hechos, no pedidos registrados).">
          {clases.map(([k, v]) => <Lin key={k} k={k === "proveedor" ? "Devuelto al proveedor" : rot[k] || k} v={nf(v)} />)}
          {conv > 0 && <Lin k={`Odoo cuenta cartones en ${nf(r.skus_otra_unidad)} SKUs: llevado a piezas`} v={`+${nf(conv)}`} />}
          {demas > 0 && <Lin k="Salió de más que lo comprado (no se resta)" v={`−${nf(demas)}`} />}
        </Paso>
        <Op c="=" />
        <Paso titulo="Queda" cifra={nf(queda)} tono="text-emerald-600" pie="piezas que deberían existir: lo comprado menos lo que salió.">
          <Lin k="SKUs con mercancía" v={nf(t.viv)} />
          <Lin k="SKUs SOLD OUT" v={nf(t.so)} />
          <Lin k="Productos sin SKU" v={nf(t.sin)} />
        </Paso>
        <Paso titulo="Odoo dice que hay" cifra={nf(libre)} tono="text-slate-600" punteado
          pie={<>piezas libres hoy (<code className="text-[11px]">free_qty</code>). La diferencia contra lo que queda son <b>{nf(queda - libre)}</b> piezas.</>} />
      </div>
      <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs leading-relaxed text-slate-600">
        <b className="text-slate-800">Por qué Odoo tiene menos.</b>{" "}
        <b>{nf(sinsku)}</b> piezas son de renglones a los que nadie les ha puesto SKU (casi todas, de los{" "}
        {nf(r.contenedores - r.contenedores_con_validado)} contenedores que bodega no ha validado).{" "}
        <b>{nf(nunca)}</b> piezas son de {nf(nsk)} SKUs que están en un packing list y nunca tuvieron entrada, salida ni existencia en Odoo.{" "}
        Odoo además registra <b>{nf(tot.sale_ajuste || 0)}</b> piezas dadas de baja por ajuste de inventario y <b>{nf(tot.entra_ajuste || 0)}</b>{" "}
        dadas de alta por ajuste; esas no son ventas y aquí no se restan. La historia de Odoo empieza en diciembre de 2025: lo vendido antes no está en sus movimientos.
      </div>
    </section>
  );
}

function Tarjeta({ tema, titulo, que, cifra, pie, children }: {
  tema: typeof ML; titulo: string; que: string; cifra: string; pie: ReactNode; children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
      <div className="h-1.5" style={{ background: tema.color }} />
      <div className="space-y-2 p-5">
        <div className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: tema.texto }}>{titulo}</div>
        <p className="text-xs text-slate-500">{que}</p>
        <div className="text-4xl font-black tabular-nums tracking-tight text-slate-900">{cifra}</div>
        <p className="text-xs leading-relaxed text-slate-600">{pie}</p>
        <ul className="space-y-1.5 rounded-xl p-3 text-xs leading-relaxed text-slate-700" style={{ background: tema.suave }}>{children}</ul>
      </div>
    </div>
  );
}

const Item = ({ children }: { children: ReactNode }) => <li className="flex gap-2"><span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-400" />{<span>{children}</span>}</li>;

export function Valor({ D, t }: { D: Catalogo; t: Totales }) {
  const ed = D.mercado?.ml?.edu || {};
  const ref = ed.ref || {};
  const P = t.pq || 1, pz = t.pzm + t.pem, c = t.cm, T = t.tm || 1;
  const ren = (rot: string, x: { v: number; pz: number }) => x.v
    ? <Item key={rot}>{rot}: <b>{fm0(x.v)}</b> · {nf(x.pz)} piezas · {(100 * x.v / T).toFixed(0)}% del valor</Item> : null;
  const am = D.mercado?.amazon || {}, faltan = am.grupos && (am.juzgados ?? 0) < am.grupos;
  const est = t.est;
  const renA = (rot: string, base: string) => est[`ea${base}`]
    ? <Item key={base}>{rot}: <b>{fm0(est[`ea${base}`])}</b> · {nf(est[`pa${base}`])} piezas</Item> : null;
  return (
    <section aria-label="Valor de lo que queda a precio de mercado" className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Tarjeta tema={ML} titulo="Valor de lo que queda, a precio de Mercado Libre" cifra={fm0(t.tm)}
        que={`Piezas que quedan × el precio de Mercado Libre de cada producto.${D.v3 ? " Lo que antes salía de la banda de su categoría usa el precio v3 de Competencia: el de mercado sin marcas de renombre, con la categoría corregida." : ""}`}
        pie={<>Sobre <b>{nf(pz)}</b> de las {nf(t.pq)} piezas que quedan ({(100 * pz / P).toFixed(1)}%). Con la media de publicaciones <b>del mismo producto</b> (o revisado, o nuestro precio): <b>{fm0(t.vmp)}</b>.
          {t.vmk ? <> Con el precio de <b>mercado sin marcas</b> (v3): <b>{fm0(t.vmk)}</b>.</> : null}
          {t.vmb ? <> Con la media de <b>su categoría</b> (banda): <b>{fm0(t.vmb)}</b>.</> : null}
          {t.em ? <> Estimado aquí, para lo que su archivo no trae: <b>{fm0(t.em)}</b>.</> : null}</>}>
        {ren(FML_L.f, c.f)}{ren(FML_L.r, c.r)}{ren(FML_L.n, c.n)}{ren(FML_L.k, c.k)}{ren(FML_L.b, c.b)}{ren(FML_L.p, c.p)}
        {t.em ? <Item>Estimado aquí con su costo y su subcategoría (lo que su archivo no trae): <b>{fm0(t.em)}</b> · {nf(t.pem)} piezas · {(100 * t.em / T).toFixed(0)}% del valor</Item> : null}
        {t.nm ? <Item>Sin valuar: <b>{nf(t.nm)}</b> piezas: los insumos excluidos (empaque y material de tienda) y lo que no tiene precio</Item> : null}
        {D.v3 && <Item><b>Precios v3</b> ({D.v3.version}): {nf(D.v3.con_precio)} filas tomaron el precio de mercado sin marcas de renombre; {nf(D.v3.a_mano)} se revisaron a mano y {nf(D.v3.revisar)} siguen marcadas para revisar. {nf(D.v3.estimado_costo)} no tuvieron ninguna fuente y quedan con su estimado por costo; {nf(D.v3.sin_precio)} quedan sin precio.</Item>}
        <Item><b>Con el precio depurado de Eduardo</b>{D.v3 ? " del 7 de octubre" : ""} (la mediana o nuestro precio publicado, con su tope) lo mismo valdría <b>{fm0(t.vdp)}</b>.</Item>
        <Item>Comprobación contra su archivo: todo lo <b>comprado</b>, a su precio depurado, da <b>{fm0(t.tcd)}</b>; su cifra para sus {nf(ref.contenedores)} contenedores es <b>{fm0(ref.valor_depurado_mxn)}</b>.</Item>
        <Item>{D.v3 ? <b>La banda del 7 de octubre no estaba calibrada.</b> : <b>Ojo con la banda: no está calibrada.</b>}
          {ed.rev?.lineas ? ` Eduardo revisó a mano las ${nf(ed.rev.lineas)} líneas de banda de mayor valor: pasaron de ${mill(ed.rev.antes)} a ${mill(ed.rev.despues)} (−${ed.rev.baja}%).` : ""} Es precio al público con IVA, antes de comisión y envío.</Item>
      </Tarjeta>
      <Tarjeta tema={AZ} titulo="Valor de lo que queda, a precio de Amazon" cifra={fm0(t.ta)}
        que="Todas las piezas que quedan × el precio promedio de la competencia en Amazon México."
        pie={<>Sobre <b>{nf(t.pza + t.pea)}</b> de las {nf(t.pq)} piezas que quedan ({(100 * (t.pza + t.pea) / P).toFixed(1)}%). <b>Medido</b> con publicaciones del mismo producto: <b>{fm0(t.va)}</b> en {nf(t.pza)} piezas ({(100 * t.pza / P).toFixed(1)}%). <b>Estimado</b>: <b>{fm0(t.ea)}</b> en {nf(t.pea)} piezas.
          {faltan ? <> <b>La captura sigue en curso:</b> van {nf(am.juzgados)} de {nf(am.grupos)} grupos de productos.</> : null}
          {D.estimacion?.az_descartados ? ` ${nf(D.estimacion.az_descartados)} precios de Amazon no se tomaron como medidos por estar a más de 8 veces el precio de Mercado Libre del mismo producto.` : ""}</>}>
        {renA("Estimado con su precio de Mercado Libre × lo que Amazon cobra de más por lo mismo", "m")}
        {renA("Estimado con su costo × lo que se vende contra su costo en su subcategoría", "c")}
        {renA("Estimado con su propio indicio (una sola publicación u otro tamaño de paquete)", "i")}
        {renA("Estimado con el precio promedio por pieza de su subcategoría", "p")}
        {renA("Productos sin SKU, al precio de ese tipo de producto", "t")}
        {t.na ? <Item>Sin valuar: <b>{nf(t.na)}</b> piezas: insumos (empaque de otros productos y material de tienda), y granel sin precio de referencia</Item> : null}
        <Item>Medido: búsqueda por palabra clave en la API de Amazon, precio de la caja de compra, una cifra por familia, y solo las publicaciones que el juez marcó como el mismo producto.</Item>
      </Tarjeta>
    </section>
  );
}
