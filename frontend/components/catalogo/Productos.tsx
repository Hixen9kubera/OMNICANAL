"use client";

// Productos.tsx — La pestaña «Productos» del CATÁLOGO: filtros, totales de lo filtrado, la tabla (50 por página,
// ordenable) y el detalle de cada fila. Mismos filtros y cuentas que el artifact.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronLeft, ChevronRight, ClipboardCopy, Search, X } from "lucide-react";

import { THEME_FALLBACK } from "@/lib/theme";
import { Foto } from "./Fotos";
import Detalle from "./Detalle";
import { CeldaPrecio, CeldaValor, Estado, Etiqueta, Mini, SoldOut, TriCab } from "./Celdas";
import {
  CORTO, type Catalogo, type Fila, type Filtros, type Orden, c1, c2, enContenedor, nf, ordenar, pasa, sumar, tsv,
} from "./logica";

const ML = THEME_FALLBACK.mercado_libre;
const AZ = THEME_FALLBACK.amazon;
const POR_PAGINA = 50;

const ESTADOS: [string, string][] = [
  ["", "Todo"], ["queda", "Queda mercancía"], ["so", "SOLD OUT"], ["sin", "Sin SKU en el packing list"], ["est", "SKU sin renglón ubicado"],
  ["nopl", "SKU sin packing list"], ["dm", "Salió más de lo comprado"], ["noodoo", "Comprado y nunca entró a Odoo"], ["uf", "Odoo cuenta en otra unidad"],
];
const PRECIOS: [string, string][] = [
  ["", "Todos"], ["ml", "Con precio de Mercado Libre"], ["az", "Con precio de Amazon"], ["dos", "Con los dos"], ["no", "Sin ninguno"],
  ["c-f", "ML: exacto (mismo producto)"], ["c-n", "ML: nuestro precio publicado"], ["c-r", "ML: revisado a mano"], ["c-k", "ML: mercado sin marcas (v3)"],
  ["v3r", "ML: v3 para revisar"], ["v3m", "ML: v3 revisado a mano"], ["c-b", "ML: banda de categoría"], ["c-e", "ML: estimado aquí"],
  ["az4", "Amazon medido a más de 4× su precio de ML"], ["c-u", "ML: bodega contó distinto al proveedor"],
];
const EMPATES: [string, string][] = [
  ["", "Todos"], ["foto", "Misma foto"], ["texto", "Mismo nombre y cantidad"], ["bodega", "Solo validado de bodega"], ["foto_odoo", "Foto de Odoo"],
  ["foto_parecida", "Foto de Odoo parecida"], ["cantidad", "Cantidad de la compra"], ["ia", "Inferido por IA"],
];

function Selector({ etiqueta, valor, opciones, onChange }: { etiqueta: string; valor: string; opciones: [string, string][]; onChange: (v: string) => void }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
      {etiqueta}
      <span className="relative">
        <select value={valor} onChange={(e) => onChange(e.target.value)}
          className={`w-full appearance-none rounded-xl border bg-white py-2 pl-3 pr-8 text-sm font-medium normal-case tracking-normal text-slate-800 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 ${valor ? "border-indigo-300" : "border-slate-200"}`}>
          {opciones.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
        <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden />
      </span>
    </label>
  );
}

const COLS: [Orden | "", string, string][] = [
  ["", "Foto", ""], ["s", "Producto", ""], ["", "Categoría", ""], ["pc", "Comprado", "d"], ["ps", "Salió", "d"], ["pq", "Queda", "d"],
  ["lb", "Odoo libre", "d"], ["mlm", "Mercado Libre por pieza", "ml"], ["_tm", "Valor a precio de ML", "ml"], ["azm", "Amazon por pieza", "az"],
  ["_ta", "Valor a precio de Amazon", "az"], ["", "Contenedor", ""],
];

export default function Productos({ D, filtros, setFiltros }: { D: Catalogo; filtros: Filtros; setFiltros: (f: Filtros) => void }) {
  const [busca, setBusca] = useState(filtros.q);
  const [orden, setOrden] = useState<{ k: Orden; d: number }>({ k: "pq", d: -1 });
  const [pag, setPag] = useState(0);
  const [abiertas, setAbiertas] = useState<Record<number, boolean>>({});
  const [copiado, setCopiado] = useState<string | null>(null);
  // El detalle va dentro de una tabla de 1,700 px que se desplaza de lado: se pega a la izquierda con el ancho
  // VISIBLE de la caja, como en el artifact, para que sus cifras no queden fuera de la vista.
  const caja = useRef<HTMLDivElement>(null);
  const ancho = useAncho(caja);

  // El filtro busca en minúsculas, pero la caja conserva lo que se escribió: solo se reescribe cuando el filtro
  // cambia desde fuera (un «Ver productos» de otra pestaña o «Quitar filtros»).
  const enviado = useRef(filtros.q);
  useEffect(() => {
    if (filtros.q !== enviado.current) { enviado.current = filtros.q; setBusca(filtros.q); }
  }, [filtros.q]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      const q = busca.trim().toLowerCase();
      if (q !== filtros.q) { enviado.current = q; setFiltros({ ...filtros, q }); }
    }, 250);
    return () => window.clearTimeout(t);
  }, [busca, filtros, setFiltros]);
  useEffect(() => setPag(0), [filtros, orden]);

  const categorias = useMemo(() => Array.from(new Set(D.R.map((r) => c1(D, r)))).sort(), [D]);
  const subcategorias = useMemo(() => Array.from(new Set(D.R.filter((r) => !filtros.cat || c1(D, r) === filtros.cat).map((r) => c2(D, r)).filter(Boolean))).sort(), [D, filtros.cat]);
  const vista = useMemo(() => {
    let v = D.R.filter((r) => pasa(D, r, filtros));
    if (filtros.con) v = v.map((r) => enContenedor(r, filtros.con));
    return ordenar(v, orden.k, orden.d);
  }, [D, filtros, orden]);
  const t = useMemo(() => sumar(vista), [vista]);
  const pags = Math.max(1, Math.ceil(vista.length / POR_PAGINA));
  const trozo = vista.slice(pag * POR_PAGINA, pag * POR_PAGINA + POR_PAGINA);
  const hayFiltros = Object.values(filtros).some(Boolean);

  const cambiar = (k: keyof Filtros) => (v: string) => setFiltros({ ...filtros, [k]: v, ...(k === "cat" ? { sub: "" } : {}) });
  const ordenarPor = (k: Orden) => setOrden((o) => (o.k === k ? { k, d: -o.d } : { k, d: k === "s" ? 1 : -1 }));
  const copiar = () => {
    const texto = tsv(D, vista);
    const ok = () => { setCopiado(`Copiado (${nf(vista.length)} filas)`); window.setTimeout(() => setCopiado(null), 1800); };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(texto).then(ok, () => setCopiado("No se pudo copiar"));
    else setCopiado("No se pudo copiar");
  };

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <label className="flex min-w-0 flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:col-span-2 xl:col-span-1">
            Buscar
            <span className="relative">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden />
              <input type="search" value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="SKU, título o contenedor" autoComplete="off"
                className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm font-medium normal-case tracking-normal text-slate-800 outline-none transition placeholder:font-normal placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
            </span>
          </label>
          <Selector etiqueta="Categoría" valor={filtros.cat} onChange={cambiar("cat")} opciones={[["", "Todas"], ...categorias.map((c): [string, string] => [c, c])]} />
          <Selector etiqueta="Subcategoría" valor={filtros.sub} onChange={cambiar("sub")} opciones={[["", "Todas"], ...subcategorias.map((c): [string, string] => [c, c])]} />
          <Selector etiqueta="Estado" valor={filtros.est} onChange={cambiar("est")} opciones={ESTADOS} />
          <Selector etiqueta="Contenedor" valor={filtros.con} onChange={cambiar("con")}
            opciones={[["", "Todos"], ...D.C.map((c): [string, string] => [c.clave, c.n != null ? `Contenedor ${c.n}` : c.clave])]} />
          <Selector etiqueta="Precio de mercado" valor={filtros.pre} onChange={cambiar("pre")} opciones={PRECIOS} />
          <Selector etiqueta="Empate" valor={filtros.emp} onChange={cambiar("emp")} opciones={EMPATES} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {hayFiltros && (
            <button type="button" onClick={() => setFiltros({ q: "", cat: "", sub: "", est: "", con: "", pre: "", emp: "" })}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
              <X size={13} aria-hidden /> Quitar filtros
            </button>
          )}
          <button type="button" onClick={copiar}
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100">
            {copiado ? <Check size={13} aria-hidden /> : <ClipboardCopy size={13} aria-hidden />} {copiado || "Copiar para Excel"}
          </button>
        </div>
      </div>

      <div aria-live="polite" className="flex flex-wrap gap-2 text-xs">
        <Chip><b>{nf(t.sk)}</b> SKUs y <b>{nf(t.sin)}</b> productos sin SKU</Chip>
        <Chip>SOLD OUT <b>{nf(t.so)}</b></Chip>
        {t.nest + t.nopl > 0 && <Chip>{nf(t.nest + t.nopl)} SKUs sin renglón, no suman</Chip>}
        {filtros.con && <Chip>Solo las piezas que llegaron en este contenedor.</Chip>}
      </div>

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
        <div ref={caja} className="overflow-x-auto">
          <table className="w-full min-w-[1700px] border-collapse text-sm">
            <thead className="bg-slate-50 text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                {COLS.map(([k, nom, cl]) => {
                  const tema = cl === "ml" ? ML : cl === "az" ? AZ : null;
                  return (
                    <th key={nom} className={`border-b border-slate-200 px-3 py-2.5 align-bottom ${cl ? "text-right" : "text-left"}`}
                      style={tema ? { boxShadow: `inset 0 3px 0 ${tema.color}`, color: tema.texto } : undefined}
                      aria-sort={k && orden.k === k ? (orden.d < 0 ? "descending" : "ascending") : undefined}>
                      {k ? (
                        <button type="button" onClick={() => ordenarPor(k)} className="inline-flex items-center gap-1 uppercase hover:text-indigo-600">
                          {nom}{orden.k === k ? (orden.d < 0 ? " ▾" : " ▴") : ""}
                        </button>
                      ) : nom}
                      {(nom === "Mercado Libre por pieza" || nom === "Amazon por pieza") && <TriCab />}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {trozo.length === 0 && <tr><td colSpan={12} className="py-14 text-center text-sm text-slate-400">Nada con esos filtros.</td></tr>}
              {trozo.map((r) => {
                const ab = !!abiertas[r._i];
                return (
                  <Fragment key={`${r._i}-${filtros.con}`}>
                    <tr className={`align-top transition-colors ${ab ? "bg-indigo-50/40" : "hover:bg-slate-50/70"} border-b border-slate-100`}>
                      <td className="px-3 py-2.5"><Foto r={r} px={52} /></td>
                      <td className="max-w-[340px] px-3 py-2.5">
                        <button type="button" onClick={() => setAbiertas((a) => ({ ...a, [r._i]: !a[r._i] }))} aria-expanded={ab}
                          className="inline-flex items-center gap-1 font-mono text-xs font-bold text-indigo-700 hover:underline">
                          <ChevronDown size={13} className={`transition-transform ${ab ? "" : "-rotate-90"}`} aria-hidden />{r.sin ? "Sin SKU" : r.s}
                        </button>
                        <div className="mt-0.5 text-[13px] leading-snug text-slate-800">{r.t}</div>
                        {r.n && <div className="text-[11px] text-slate-400">{r.n}</div>}
                        <div className="mt-1 flex flex-wrap gap-1"><Estado r={r} />{r.td ? <Etiqueta>título por confirmar</Etiqueta> : null}</div>
                      </td>
                      <td className="max-w-[180px] px-3 py-2.5">
                        <div className="text-xs font-semibold text-slate-700">{c1(D, r)}</div>
                        <div className="text-[11px] text-slate-500">{c2(D, r)}{r.kq === "r" ? " · predicha" : r.kq === "p" ? " · estimada" : ""}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {r.pc != null ? (
                          <>
                            <div className="text-[15px] font-bold tabular-nums text-slate-900">{nf(r.pc)}</div>
                            {r.pp != null && <Mini>proveedor <b>{nf(r.pp)}</b></Mini>}
                            {r._parte && <Mini>de <b>{nf(r._parte)}</b> con los otros contenedores</Mini>}
                          </>
                        ) : r.pe != null ? <><div className="tabular-nums text-slate-400">≈ {nf(r.pe)}</div><Mini>según costos</Mini></> : <div className="text-slate-300">—</div>}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {r.ps ? (
                          <>
                            <div className="text-[15px] font-bold tabular-nums text-rose-600">{nf(r.pz || r.ps)}</div>
                            {r.pz ? <Mini>{nf(r.ps)} en Odoo × {r.uf}</Mini> : null}
                            <Mini>{Object.keys(r.cl || {}).slice(0, 2).map((k) => `${CORTO[k] || k} ${nf(r.cl![k])}`).join(" · ")}</Mini>
                          </>
                        ) : <div className="tabular-nums text-slate-300">0</div>}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {r.pc == null ? <div className="text-slate-300">—</div> : r.so ? <SoldOut /> : <div className="text-[15px] font-bold tabular-nums text-emerald-600">{nf(r.pq)}</div>}
                        {r.dm ? <Mini className="text-rose-600">salió {nf(r.dm)} de más</Mini> : null}
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{r.lb != null ? nf(r.lb) : "—"}</td>
                      <td className="min-w-[200px] px-3 py-2.5"><CeldaPrecio p={r.ml} firme={r._fm} est={r.em} /></td>
                      <td className="min-w-[150px] px-3 py-2.5" style={r._tm != null ? { background: ML.suave } : undefined}>
                        <CeldaValor medido={r._vm} estimado={r._em} r={r} precio={r._pm} p={r.ml} conReferencia />
                      </td>
                      <td className="min-w-[200px] px-3 py-2.5"><CeldaPrecio p={r.az} firme={r._fa} est={r.ea} /></td>
                      <td className="min-w-[150px] px-3 py-2.5" style={r._ta != null ? { background: AZ.suave } : undefined}>
                        <CeldaValor medido={r._va} estimado={r._ea} r={r} precio={r._pa} />
                      </td>
                      <td className="px-3 py-2.5 text-[11px] text-slate-500">
                        {Object.keys(r.ct || {}).length
                          ? <>{Object.keys(r.ct || {}).slice(0, 3).map((k) => <div key={k}>cont. {k}</div>)}{Object.keys(r.ct || {}).length > 3 && <div>+{Object.keys(r.ct || {}).length - 3}</div>}</>
                          : r.ec != null ? `costos: cont. ${r.ec}` : "—"}
                      </td>
                    </tr>
                    {ab && (
                      <tr>
                        <td colSpan={12} className="border-b border-slate-200 p-0">
                          <div className="sticky left-0" style={{ width: ancho || undefined }}><Detalle D={D} r={r} /></div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          <span>{vista.length ? `${nf(pag * POR_PAGINA + 1)}–${nf(Math.min((pag + 1) * POR_PAGINA, vista.length))} de ${nf(vista.length)}` : "0"}</span>
          <div className="flex items-center gap-2">
            <button type="button" disabled={pag <= 0} onClick={() => setPag((p) => p - 1)}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40">
              <ChevronLeft size={14} aria-hidden /> Anterior
            </button>
            <span className="tabular-nums">{pag + 1} / {pags}</span>
            <button type="button" disabled={pag >= pags - 1} onClick={() => setPag((p) => p + 1)}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40">
              Siguiente <ChevronRight size={14} aria-hidden />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** El ancho visible de una caja que se desplaza de lado (cambia con la ventana). */
export function useAncho(ref: React.RefObject<HTMLDivElement | null>): number {
  const [ancho, setAncho] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => setAncho(el.clientWidth);
    medir();
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return ancho;
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full border border-slate-200 bg-white px-3 py-1 tabular-nums text-slate-600">{children}</span>;
}
