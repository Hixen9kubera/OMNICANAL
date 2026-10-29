"use client";

// Otras.tsx — Las otras cuatro pestañas del CATÁLOGO: «Por categoría», «Contenedores», «Empate SKU y packing list»
// y «De dónde sale cada dato». Port del artifact (`categorias`, `subLista`, `contenedoresTabla`, `empate`, `metodo`).

import { Fragment, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ChevronDown } from "lucide-react";

import { THEME_FALLBACK } from "@/lib/theme";
import { Foto } from "./Fotos";
import { useAncho } from "./Productos";
import { Etiqueta, Mini, SoldOut, Tri, TriCab } from "./Celdas";
import {
  FML, type Catalogo, type Fila, type Filtros, type Grupo, type Sub, type Totales, agrupar, fm, fm0, mill, nf, partesML,
} from "./logica";

const ML = THEME_FALLBACK.mercado_libre;
const AZ = THEME_FALLBACK.amazon;

const Th = ({ children, d, tema, className = "" }: { children: ReactNode; d?: boolean; tema?: typeof ML; className?: string }) => (
  <th className={`border-b border-slate-200 px-3 py-2.5 align-bottom text-[10.5px] font-semibold uppercase tracking-wide ${d ? "text-right" : "text-left"} ${className}`}
    style={tema ? { boxShadow: `inset 0 3px 0 ${tema.color}`, color: tema.texto } : { color: "#64748b" }}>{children}</th>
);
const Num = ({ children, fuerte, className = "" }: { children: ReactNode; fuerte?: boolean; className?: string }) => (
  <td className={`px-3 py-2.5 text-right tabular-nums ${fuerte ? "font-bold text-slate-900" : "text-slate-700"} ${className}`}>{children}</td>
);
const Ver = ({ onClick, children = "Ver productos" }: { onClick: () => void; children?: ReactNode }) => (
  <button type="button" onClick={onClick}
    className="inline-flex items-center gap-1 whitespace-nowrap rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-100">
    {children} <ArrowRight size={12} aria-hidden />
  </button>
);

// ── Por categoría ────────────────────────────────────────────────────────────

function Rango({ x, que = " medido" }: { x: Totales["xm"]; que?: string }) {
  if (!x.n) return <td className="px-3 py-2.5 text-right text-slate-300">—</td>;
  return <td className="min-w-[190px] px-3 py-2.5"><Tri a={fm0(x.lo)} b={fm0(x.s / x.n)} c={fm0(x.hi)} /><Mini className="text-right">{nf(x.n)} productos con precio{que}</Mini></td>;
}

function FilaCat({ t, nombre, boton, ver, sub }: { t: Totales; nombre: ReactNode; boton?: ReactNode; ver: ReactNode; sub?: boolean }) {
  return (
    <tr className={`border-b border-slate-100 align-top ${sub ? "bg-slate-50/60" : "hover:bg-slate-50/50"}`}>
      <td className={`px-3 py-2.5 ${sub ? "pl-8" : ""}`}>{boton || nombre}</td>
      <Num>{nf(t.sk)}{t.sin ? <span className="ml-1 text-[11px] font-normal text-slate-400">+{nf(t.sin)} sin SKU</span> : null}</Num>
      <Num>{nf(t.viv)}</Num>
      <Num>{nf(t.so)}</Num>
      <Num>{nf(t.pc)}</Num>
      <Num className="text-rose-600">{nf(t.ps)}</Num>
      <Num fuerte className="!text-emerald-600">{nf(t.pq)}</Num>
      <Rango x={t.xm} que=" de ML" />
      <td className="px-3 py-2.5 text-right" style={{ background: ML.suave }}>
        <div className="font-bold tabular-nums text-slate-900">{fm0(t.tm)}</div><Mini>{nf(t.pzm + t.pem)} pzas · {partesML(t, " · ")}</Mini>
      </td>
      <Rango x={t.xa} />
      <td className="px-3 py-2.5 text-right" style={{ background: AZ.suave }}>
        <div className="font-bold tabular-nums text-slate-900">{fm0(t.ta)}</div><Mini>{nf(t.pza + t.pea)} pzas · medido {mill(t.va)}</Mini>
      </td>
      <td className="px-3 py-2.5">{ver}</td>
    </tr>
  );
}

function TresPrecios({ p, usado, medido, nota }: { p?: Fila["ml"]; usado: number | null; medido: boolean; nota: string }) {
  if (usado == null) return <><td className="px-2 py-1.5 text-right text-slate-300">—</td><td className="px-2 py-1.5 text-right text-slate-300">—</td><td className="px-2 py-1.5 text-right text-slate-300">—</td></>;
  const con = medido && p && p.lo != null;
  return (
    <>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{con ? fm0(p!.lo) : "—"}</td>
      <td className="px-2 py-1.5 text-right"><b className="tabular-nums text-slate-900">{fm(usado)}</b>{nota && <Mini>{nota}</Mini>}</td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{con ? fm0(p!.hi) : "—"}</td>
    </>
  );
}

function SubLista({ s }: { s: Sub }) {
  const l = s.l.slice().sort((a, b) => (b.pq || 0) - (a.pq || 0));
  const m = l.slice(0, 200);
  return (
    <div className="overflow-x-auto border-y border-slate-200 bg-white">
      <table className="w-full min-w-[1500px] text-xs">
        <thead>
          <tr>
            <Th className="!normal-case">Producto</Th><Th d>Comprado</Th><Th d>Salió</Th><Th d>Queda</Th>
            <Th d tema={ML}>ML mín</Th><Th d tema={ML}>ML media</Th><Th d tema={ML}>ML máx</Th><Th d tema={ML}>Total a precio de ML</Th>
            <Th d tema={AZ}>Amazon mín</Th><Th d tema={AZ}>Amazon media</Th><Th d tema={AZ}>Amazon máx</Th><Th d tema={AZ}>Total a precio de Amazon</Th>
          </tr>
        </thead>
        <tbody>
          {m.map((r) => {
            const pm = r.ml;
            const notaM = r.nv ? "" : r._cm ? `${FML[r._cm] || ""}${pm && pm.tp ? " · topado" : ""}` : r.em ? "estimado" : "";
            const notaA = r.nv ? "" : r._fa ? `${r.az!.n} publicaciones` : r.ea ? "estimado" : "";
            return (
              <tr key={r._i} className="border-b border-slate-100 align-top">
                <td className="px-2 py-1.5">
                  <div className="flex items-center gap-2">
                    <Foto r={r} px={36} />
                    <div className="min-w-0"><span className="font-mono font-bold text-slate-900">{r.sin ? "Sin SKU" : r.s}</span> <span className="text-slate-700">{r.t}</span>
                      {r.nv ? <> <Etiqueta>insumo, no se valúa</Etiqueta></> : null}</div>
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{nf(r.pc)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{nf(r.pz || r.ps || 0)}</td>
                <td className="px-2 py-1.5 text-right">{r.so ? <SoldOut /> : <b className="tabular-nums">{nf(r.pq)}</b>}</td>
                <TresPrecios p={pm} usado={r._pm} medido={r._fm && !(pm && pm.tp)} nota={notaM} />
                <td className="px-2 py-1.5 text-right tabular-nums" style={r._tm != null ? { background: ML.suave } : undefined}>{r._tm != null ? fm0(r._tm) : "—"}</td>
                <TresPrecios p={r.az} usado={r._pa} medido={r._fa} nota={notaA} />
                <td className="px-2 py-1.5 text-right tabular-nums" style={r._ta != null ? { background: AZ.suave } : undefined}>{r._ta != null ? fm0(r._ta) : "—"}</td>
              </tr>
            );
          })}
          {l.length > m.length && <tr><td colSpan={12} className="px-3 py-2 text-slate-400">y {nf(l.length - m.length)} productos más: usa «Ver productos».</td></tr>}
          <tr className="border-t-2 border-slate-200 font-semibold">
            <td className="px-2 py-2">Total de la subcategoría ({nf(l.length)} productos)</td>
            <td className="px-2 py-2 text-right tabular-nums">{nf(s.t.pc)}</td>
            <td className="px-2 py-2 text-right tabular-nums">{nf(s.t.ps)}</td>
            <td className="px-2 py-2 text-right tabular-nums">{nf(s.t.pq)}</td>
            <td colSpan={3} className="px-2 py-2 text-right text-[11px] font-normal text-slate-500">piezas × media de cada producto</td>
            <td className="px-2 py-2 text-right tabular-nums" style={{ background: ML.suave }}>{fm0(s.t.tm)}</td>
            <td colSpan={3} className="px-2 py-2 text-right text-[11px] font-normal text-slate-500">piezas × media de cada producto</td>
            <td className="px-2 py-2 text-right tabular-nums" style={{ background: AZ.suave }}>{fm0(s.t.ta)}</td>
          </tr>
        </tbody>
      </table>
      <p className="px-3 py-2 text-[11px] leading-relaxed text-slate-500">
        <b>Cómo leerlo.</b> Mínimo, media y máximo son de las publicaciones de la competencia: en Mercado Libre, las del archivo de Eduardo (del
        mismo producto, o de su categoría cuando dice «media de su categoría»); en Amazon, las que el juez marcó como el mismo producto al buscar
        por palabra clave. <b>Total = piezas que quedan × la media.</b> «Estimado» quiere decir que no hubo publicaciones para ese producto.
      </p>
    </div>
  );
}

export function PorCategoria({ D, irA }: { D: Catalogo; irA: (f: Partial<Filtros>) => void }) {
  const grupos: Grupo[] = useMemo(() => agrupar(D), [D]);
  const [abiertas, setAbiertas] = useState<Record<string, boolean>>({});
  const [subs, setSubs] = useState<Record<string, boolean>>({});
  const caja = useRef<HTMLDivElement>(null);
  const ancho = useAncho(caja);
  return (
    <div className="space-y-3">
      <p className="max-w-[100ch] text-sm text-slate-500">
        Cada categoría compara los SKUs que traían los packing lists contra los que todavía tienen mercancía. Abre una categoría para ver sus
        subcategorías, y una subcategoría para ver ahí mismo cada producto con sus piezas, su precio en Mercado Libre y en Amazon (mínimo, media y
        máximo) y el total de sus piezas a la media de cada uno.
      </p>
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
        <div ref={caja} className="overflow-x-auto">
          <table className="w-full min-w-[1600px] text-sm">
            <thead className="bg-slate-50">
              <tr>
                <Th>Categoría</Th><Th d>SKUs en packing lists</Th><Th d>Con mercancía hoy</Th><Th d>SOLD OUT</Th><Th d>Comprado</Th>
                <Th d className="!text-rose-600">Salió</Th><Th d className="!text-emerald-600">Queda</Th>
                <Th d tema={ML}>Mercado Libre por pieza<TriCab /></Th><Th d tema={ML}>Valor a precio de ML</Th>
                <Th d tema={AZ}>Amazon por pieza<TriCab /></Th><Th d tema={AZ}>Valor a precio de Amazon</Th><Th> </Th>
              </tr>
            </thead>
            <tbody>
              {grupos.map((g, i) => {
                const ab = !!abiertas[g.n];
                return (
                  <Fragment key={g.n}>
                    <FilaCat t={g.t} nombre={g.n}
                      boton={<button type="button" aria-expanded={ab} onClick={() => setAbiertas((a) => ({ ...a, [g.n]: !a[g.n] }))}
                        className="inline-flex items-center gap-1.5 text-left font-semibold text-slate-900 hover:text-indigo-700">
                        <ChevronDown size={14} className={`shrink-0 transition-transform ${ab ? "" : "-rotate-90"}`} aria-hidden />{g.n}</button>}
                      ver={<Ver onClick={() => irA({ cat: g.n })} />} />
                    {ab && g.sub.map((s, j) => {
                      const id = `${i}:${j}`, as = !!subs[id];
                      return (
                        <Fragment key={id}>
                          <FilaCat t={s.t} sub nombre={s.n}
                            boton={<button type="button" aria-expanded={as} onClick={() => setSubs((a) => ({ ...a, [id]: !a[id] }))}
                              className="inline-flex items-center gap-1.5 text-left text-slate-700 hover:text-indigo-700">
                              <ChevronDown size={13} className={`shrink-0 transition-transform ${as ? "" : "-rotate-90"}`} aria-hidden />{s.n}</button>}
                            ver={<Ver onClick={() => irA({ cat: g.n, sub: s.n === "(sin subcategoría)" ? "" : s.n })} />} />
                          {as && <tr><td colSpan={12} className="p-0"><div className="sticky left-0" style={{ width: ancho || undefined }}><SubLista s={s} /></div></td></tr>}
                        </Fragment>
                      );
                    })}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Contenedores ─────────────────────────────────────────────────────────────

export function Contenedores({ D, irA }: { D: Catalogo; irA: (f: Partial<Filtros>) => void }) {
  return (
    <div className="space-y-3">
      <p className="max-w-[90ch] text-sm text-slate-500">
        Un renglón por contenedor. «Proveedor» es lo que declara el packing list original; «bodega» es lo que se contó al validarlo. Cuando hay
        validado, ese conteo es el que vale.
      </p>
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-sm">
            <thead className="bg-slate-50">
              <tr>
                <Th>Contenedor</Th><Th>Archivos</Th><Th d>Proveedor declaró</Th><Th d>Bodega contó</Th><Th d>Piezas que cuentan</Th>
                <Th d>De productos sin SKU</Th><Th d>SKUs</Th><Th d className="!text-rose-600">Salió</Th><Th d className="!text-emerald-600">Queda</Th><Th> </Th>
              </tr>
            </thead>
            <tbody>
              {D.C.map((c) => (
                <tr key={c.clave} className="border-b border-slate-100 hover:bg-slate-50/50">
                  <td className="px-3 py-2.5">
                    <div className="font-semibold text-slate-900">{c.n != null ? `Contenedor ${c.n}` : c.clave}</div>
                    <div className="font-mono text-[11px] text-slate-400">{(c.codigos || []).join(" · ")}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {c.val.length ? <Etiqueta tono="emerald">validado</Etiqueta> : <Etiqueta tono="amber">sin validar</Etiqueta>}
                      {!c.orig.length && <Etiqueta>sin original</Etiqueta>}
                    </div>
                  </td>
                  <Num>{nf(c.pz_orig)}</Num>
                  <Num>{c.val.length ? nf(c.pz_val) : "—"}</Num>
                  <Num fuerte>{nf(c.pz)}</Num>
                  <Num>{nf(c.sin)}</Num>
                  <Num>{nf(c.skus)}</Num>
                  <Num className="!text-rose-600">{nf(c.sal)}</Num>
                  <Num fuerte className="!text-emerald-600">{nf(c.pz - c.sal)}</Num>
                  <td className="px-3 py-2.5"><Ver onClick={() => irA({ con: c.clave })} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Empate SKU y packing list ────────────────────────────────────────────────

export function Empate({ D, irA }: { D: Catalogo; irA: (f: Partial<Filtros>) => void }) {
  const n: Record<string, number> = { foto: 0, texto: 0, bodega: 0, foto_odoo: 0, foto_parecida: 0, cantidad: 0, ia: 0 };
  const pz: Record<string, number> = { ...n };
  let est = 0, nopl = 0, sin = 0, pzsin = 0;
  D.R.forEach((r) => {
    if (r.sin) { sin++; pzsin += r.pc || 0; return; }
    if (r.pc != null) { const m = r._m || "bodega"; n[m]++; pz[m] += r.pc; }
    else if (r.pe != null) est++;
    else nopl++;
  });
  const fp = D.meta.fotos;
  const cajas: [string, string, string, Partial<Filtros>][] = [
    [nf(n.foto), "SKUs empatados por la foto", `El renglón del packing list original trae el mismo archivo de imagen que el renglón donde bodega anotó el SKU. ${nf(pz.foto)} piezas.`, { emp: "foto" }],
    [nf(n.texto), "SKUs empatados por nombre y cantidad", `Sin foto en común, pero el original tiene un renglón con el mismo nombre y la misma cantidad. ${nf(pz.texto)} piezas.`, { emp: "texto" }],
    [nf(n.foto_odoo), "SKUs empatados con la foto de Odoo", `En contenedores sin validar: la foto del producto en Odoo es la de un renglón de ESE contenedor. ${nf(pz.foto_odoo)} piezas.`, { emp: "foto_odoo" }],
    [nf(n.foto_parecida), "SKUs empatados por foto parecida", `La misma imagen de Odoo, vuelta a guardar: casi idéntica a la de un renglón de su contenedor. ${nf(pz.foto_parecida)} piezas.`, { emp: "foto_parecida" }],
    [nf(n.cantidad), "SKUs empatados por la orden de compra", `Lo recibido en su orden de compra coincide con un solo renglón del contenedor. ${nf(pz.cantidad)} piezas.`, { emp: "cantidad" }],
    [nf(n.ia), "SKUs empatados por la IA", `En contenedores sin validar: la IA emparejó el nombre del SKU con el nombre del renglón (inglés o chino) y las piezas recibidas. Conviene confirmarlo al validar el contenedor. ${nf(pz.ia)} piezas.`, { emp: "ia" }],
    [nf(n.bodega), "SKUs solo con el validado de bodega", `Bodega anotó el SKU y contó las piezas, pero no se ubicó el renglón en el packing list original. Las piezas son confiables; falta el número de renglón. ${nf(pz.bodega)} piezas.`, { emp: "bodega" }],
    [nf(est), "SKUs sin renglón ubicado", "Están en Odoo y la base de costos dice en qué contenedor llegaron, pero ese contenedor no tiene validado de bodega y su foto no aparece en el original. Sus piezas están entre los productos sin SKU.", { est: "est" }],
    [nf(nopl), "SKUs sin packing list", "Tienen movimiento o existencia en Odoo y no aparecen en ningún packing list leído.", { est: "nopl" }],
    [nf(sin), "Productos de packing list sin SKU", `${nf(pzsin)} piezas en renglones a los que nadie les ha puesto SKU. Es lo que falta empatar: casi todo viene de contenedores que bodega no ha validado.`, { est: "sin" }],
  ];
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/60 p-4 text-sm text-emerald-900">
        <b>Fotos de esta pestaña.</b> {nf(fp.pl_original + fp.pl_artifact)} productos muestran la foto de su renglón en el packing list
        ({nf(fp.pl_original)} sacadas del archivo original del proveedor); solo {nf(fp.odoo)} muestran la de Odoo, porque no tienen renglón con
        foto, y {nf(fp.sin)} no tienen foto.
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {cajas.map(([v, t, d, f]) => (
          <div key={t} className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
            <div className="text-3xl font-black tabular-nums text-indigo-700">{v}</div>
            <div className="text-sm font-semibold text-slate-900">{t}</div>
            <p className="flex-1 text-xs leading-relaxed text-slate-500">{d}</p>
            <div><Ver onClick={() => irA(f)}>Ver la lista</Ver></div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── De dónde sale cada dato ──────────────────────────────────────────────────

export function Metodo({ D }: { D: Catalogo }) {
  const r = D.resumen, RZ = D.estimacion?.az?.razon_ml || {};
  const b: [string, ReactNode][] = [
    ["La cuenta", <>Comprado − salió = queda. <b>Comprado</b> son las piezas de los packing lists de todos los contenedores. <b>Salió</b> son los movimientos HECHOS de Odoo de la bodega hacia afuera. <b>Queda</b> es la resta. Un producto está <b>SOLD OUT</b> cuando salió todo lo que se compró.</>],
    ["Qué cuenta como comprado", <>Cada contenedor tiene el packing list original del proveedor y, si bodega ya lo recibió, el mismo archivo validado: con el SKU de cada renglón y lo que se contó en físico. Cuando hay validado, vale el conteo de bodega, porque esa es la unidad en que Odoo mueve. Cuando no lo hay, vale lo que declara el proveedor. Los proveedores declararon {nf(r.pz_original)} piezas en total.</>],
    ["Qué cuenta como salida", "Solo movimientos en estado hecho que cruzan la frontera de la bodega: entregas a cliente (que en Odoo incluyen los envíos a Mercado Libre Full, a Amazon y a Walmart, y las ventas directas), menos lo que el cliente devolvió, más lo devuelto al proveedor. Lo enviado a Full cuenta como salida aunque todavía no se haya vendido ahí. Los ajustes de inventario no se restan: son correcciones, no ventas, y se ven en el detalle de cada producto."],
    ["Productos sin SKU", "El packing list original no trae SKU. En los contenedores que bodega no ha validado, un renglón solo tiene SKU si la foto del producto en Odoo es la de ese renglón, o si lo recibido en su orden de compra coincide con un solo renglón. Lo demás aparece como «Sin SKU», agrupado por nombre: es mercancía comprada y cuenta en el total, pero no se le puede restar ninguna salida."],
    ["Fotos", <>Cada producto muestra la <b>foto de su renglón en el packing list</b>, sacada del archivo original del proveedor (la más grande si tiene varios renglones). Solo donde la fila no tiene renglón con foto se usa la de Odoo. El artifact del catálogo vivo, que es el respaldo de estos datos, sigue mostrando la de Odoo.</>],
    ["Títulos", "Todos los productos llevan un título al estilo de Mercado Libre México. Si el producto está publicado en Mercado Libre, es el de su publicación. Si no, lo escribió la IA con las mismas instrucciones del panel; cuando el nombre no describía el producto, la IA lo escribió viendo la foto. Cada fila dice cuál fue su caso."],
    ["Categoría", "La de Mercado Libre. Sale de la publicación o de lo que el panel guardó; si no hay, la da el predictor de categorías de Mercado Libre a partir del título (marcada «predicha»); como último recurso, el prefijo del SKU (marcada «estimada»)."],
    ["Precio de Mercado Libre", <>Viene del archivo de Eduardo (corte 7-oct-2026): cada línea de los packing lists originales, cotizada por la API de Mercado Libre, con el mínimo, la media y el máximo de sus publicaciones. El valor se calcula con <b>la media</b>. <b>Del mismo producto</b>: las publicaciones que el juez marcó como iguales. <b>Revisado a mano</b>: las líneas de mayor valor corregidas una por una. <b>Nuestro precio</b>: cuando no hubo publicaciones que promediar.{D.v3 ? <> <b>Desde el 13 de octubre (precios v3)</b> la banda de categoría, inflada por las marcas de renombre, se sustituyó por el <b>mercado sin marcas</b> de Competencia: el ranking real de más vendidos, con la categoría corregida y las filas de más valor revisadas a mano.</> : null}</>],
    ["Precio de Amazon", "Por palabra clave: se busca el término en la API de Amazon México, se lee el precio de la caja de compra de cada publicación, se cuenta una vez cada familia de variantes, y un juez (IA) deja solo las que son el mismo producto. El dato principal es la media; al lado van la mediana y el rango central."],
    ["Valor a precio de mercado", <>Todas las piezas que quedan × el precio promedio (la media) por pieza, por separado para cada canal. Es precio al público, antes de comisiones, envíos e impuestos. Cada producto dice si su precio es <b>medido</b> o <b>estimado</b>, y cada total dice cuánto es de cada uno.</>],
    ["Precio estimado", <>Para lo que no tiene precio medido: en Amazon, su precio de Mercado Libre × lo que Amazon cobra de más por lo mismo en su subcategoría (hoy {RZ.p ?? "—"} veces cuando el precio de ML es del producto y {RZ.b ?? "—"} cuando es de mercado sin marcas); su costo × el múltiplo al que se venden los productos de su subcategoría; en productos sin SKU, el precio de ese tipo de producto; su propio indicio; o el precio promedio por pieza de su subcategoría. Ningún estimado pasa del 90% más caro de lo medido en su subcategoría.</>],
    ["Sin costos", "Esta pestaña no muestra costos ni precios de compra: el repositorio del panel es público. El artifact del catálogo vivo sí los trae."],
    ["Lo que NO es esta página", <>No escribe en ningún sistema. Es una foto del artifact armada el {new Date(D.meta.generado).toLocaleDateString("es-MX", { day: "numeric", month: "long", year: "numeric" })}: «Queda» es lo que debería existir según lo comprado y lo que salió; lo que Odoo dice tener se muestra al lado para comparar.</>],
  ];
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {b.map(([t, x]) => (
        <div key={t} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="text-sm font-bold text-slate-900">{t}</h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-slate-600">{x}</p>
        </div>
      ))}
    </div>
  );
}
