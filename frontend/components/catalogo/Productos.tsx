"use client";

// Productos.tsx — La pestaña «Productos» del CATÁLOGO: filtros, totales de lo filtrado, la tabla (50 por página,
// ordenable) y el detalle de cada fila. Mismos filtros y cuentas que el artifact.
//
// POR VALIDAR (v0.705.0, Brandon, 30-oct-2026): «filtrar para que muestre lo que necesitamos validar primero que nada
// y ordenar de mayor retorno, mayor ticket, mayor stock». La pestaña abre en «Por validar» (los motivos salen de
// `logica.ts::motivos`) ordenada por mayor retorno; «Todo el catálogo» quita ese filtro.
//
// SKU Y PUBLICADO (v0.708.0, Brandon, 30-oct-2026): «sepárame los productos que tienen SKU de los que NO tienen SKU
// explícitamente», «un filtro de los que ya están publicados vs los que no» y «una columna nueva para que podamos
// diferenciarlos a vista». Publicado = en Mercado Libre HOY (activa o pausada), lo único en vivo de la pestaña.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDownWideNarrow, Check, ChevronDown, ChevronLeft, ChevronRight, ClipboardCheck, ClipboardCopy, Search, X } from "lucide-react";

import { THEME_FALLBACK } from "@/lib/theme";
import { Foto } from "./Fotos";
import Detalle from "./Detalle";
import { CeldaPrecio, CeldaValor, Estado, Etiqueta, Mini, Publicacion, SoldOut, TriCab } from "./Celdas";
import {
  CORTO, MOTIVOS, PRIORIDADES, SIN_FILTROS, type Catalogo, type Fila, type Filtros, type Orden, c1, c2, enContenedor, hora, nf,
  ordenar, pasa, publicadoEnML, sumar, tsv,
} from "./logica";

/** Cómo va la lectura de lo publicado en ML (la página la pide después del catálogo). */
export type EstadoPublicados = { estado: "cargando" | "ok" | "error"; actualizado?: string | null };

const CORTO_MOTIVO: Record<string, string> = Object.fromEntries(MOTIVOS.map(([k, n]) => [k, n]));

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

/** Un grupo de opciones excluyentes con su conteo (como «Por validar / Todo el catálogo»). */
function Segmentos({ etiqueta, valor, opciones, onChange, apagado }: {
  etiqueta: string; valor: string; opciones: [string, string, number | null][]; onChange: (v: string) => void; apagado?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{etiqueta}</span>
      <div className="inline-flex flex-wrap gap-0.5 rounded-xl bg-slate-100 p-1" role="radiogroup" aria-label={etiqueta} title={apagado}>
        {opciones.map(([v, nom, n]) => {
          const on = valor === v;
          return (
            <button key={v || "todos"} type="button" role="radio" aria-checked={on} disabled={!!apagado && !!v} onClick={() => onChange(v)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${on ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>
              {nom}
              {n != null && (
                <span className={`rounded-full px-1.5 py-0.5 text-[10.5px] tabular-nums ${on ? "bg-indigo-100 text-indigo-700" : "bg-slate-200 text-slate-600"}`}>{nf(n)}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

const COLS: [Orden | "", string, string][] = [
  ["", "Foto", ""], ["s", "Producto", ""], ["", "SKU y Mercado Libre", ""], ["", "Categoría", ""], ["pc", "Comprado", "d"], ["ps", "Salió", "d"], ["pq", "Queda", "d"],
  ["lb", "Odoo libre", "d"], ["mlm", "Mercado Libre por pieza", "ml"], ["_tm", "Valor a precio de ML", "ml"], ["azm", "Amazon por pieza", "az"],
  ["_ta", "Valor a precio de Amazon", "az"], ["", "Contenedor", ""],
];

export default function Productos({ D, filtros, setFiltros, pub }: {
  D: Catalogo; filtros: Filtros; setFiltros: (f: Filtros) => void; pub: EstadoPublicados;
}) {
  const [busca, setBusca] = useState(filtros.q);
  const [orden, setOrden] = useState<{ k: Orden; d: number }>({ k: "_tm", d: -1 });
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
  const hayFiltros = Object.entries(filtros).some(([k, v]) => k !== "val" && !!v);
  // Cuántos productos hay que validar, y por cada motivo (sobre todo el catálogo, no sobre lo filtrado).
  const porMotivo = useMemo(() => {
    const c: Record<string, number> = { "*": 0 };
    D.R.forEach((r) => {
      if (!r._mv.length) return;
      c["*"]++;
      r._mv.forEach((m) => { c[m] = (c[m] || 0) + 1; });
    });
    return c;
  }, [D]);
  // Los conteos de «SKU» y «Mercado Libre» respetan todos los demás filtros (cada uno sin el suyo).
  const conteos = useMemo(() => {
    const sinSku = D.R.filter((r) => pasa(D, r, { ...filtros, sku: "" }));
    const sinPub = D.R.filter((r) => pasa(D, r, { ...filtros, pub: "" }));
    const con = sinSku.filter((r) => !r.sin).length;
    const pubs = sinPub.filter(publicadoEnML).length;
    return { sku: [sinSku.length, con, sinSku.length - con], pub: [sinPub.length, pubs, sinPub.length - pubs] };
  }, [D, filtros]);
  const pubOk = pub.estado === "ok";
  const prioridad = PRIORIDADES.find((p) => p[0] === orden.k && orden.d < 0);

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
      <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="inline-flex rounded-xl bg-slate-100 p-1" role="radiogroup" aria-label="Qué productos ver">
            {([["*", "Por validar", porMotivo["*"]], ["", "Todo el catálogo", D.R.length]] as [string, string, number][]).map(([v, nom, n]) => {
              const on = v ? !!filtros.val : !filtros.val;
              return (
                <button key={nom} type="button" role="radio" aria-checked={on} onClick={() => setFiltros({ ...filtros, val: v })}
                  className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-semibold transition ${on ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>
                  {v && <ClipboardCheck size={15} aria-hidden />}{nom}
                  <span className={`rounded-full px-2 py-0.5 text-[11px] tabular-nums ${on ? "bg-indigo-100 text-indigo-700" : "bg-slate-200 text-slate-600"}`}>{nf(n)}</span>
                </button>
              );
            })}
          </div>
          <label className="flex min-w-[240px] flex-col gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            <span className="inline-flex items-center gap-1"><ArrowDownWideNarrow size={13} aria-hidden /> Ordenar por</span>
            <span className="relative">
              <select value={prioridad ? prioridad[0] : ""} onChange={(e) => e.target.value && setOrden({ k: e.target.value as Orden, d: -1 })}
                className="w-full appearance-none rounded-xl border border-indigo-300 bg-white py-2 pl-3 pr-8 text-sm font-semibold normal-case tracking-normal text-slate-800 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100">
                {!prioridad && <option value="">La columna que elegiste en la tabla</option>}
                {PRIORIDADES.map(([k, nom, que]) => <option key={k} value={k}>{nom} · {que}</option>)}
              </select>
              <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden />
            </span>
          </label>
        </div>
        {filtros.val && (
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Motivo">
            {([["*", "Todos los motivos", "Cualquier motivo"], ...MOTIVOS] as [string, string, string][]).map(([k, nom, que]) => {
              const on = filtros.val === k;
              return (
                <button key={k} type="button" role="radio" aria-checked={on} title={que} onClick={() => setFiltros({ ...filtros, val: k })}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${on ? "border-amber-400 bg-amber-50 text-amber-900" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"}`}>
                  {nom} <span className="tabular-nums opacity-70">{nf(porMotivo[k] || 0)}</span>
                </button>
              );
            })}
          </div>
        )}
        <div className="flex flex-wrap gap-4">
          <Segmentos etiqueta="SKU" valor={filtros.sku} onChange={cambiar("sku")}
            opciones={[["", "Todos", conteos.sku[0]], ["con", "Con SKU", conteos.sku[1]], ["sin", "Sin SKU", conteos.sku[2]]]} />
          <Segmentos etiqueta="Mercado Libre" valor={filtros.pub} onChange={cambiar("pub")}
            apagado={pubOk ? undefined : pub.estado === "cargando" ? "Leyendo lo publicado en Mercado Libre…" : "No se pudo leer lo publicado en Mercado Libre."}
            opciones={[["", "Todos", conteos.pub[0]], ["si", "Publicados", pubOk ? conteos.pub[1] : null], ["no", "Sin publicar", pubOk ? conteos.pub[2] : null]]} />
        </div>
        <p className="text-[11px] leading-relaxed text-slate-500">
          {filtros.val
            ? "Productos con mercancía que el catálogo marca para validar. "
            : "Todo el catálogo, con o sin motivos para validar. "}
          {prioridad ? <>Orden: <b>{prioridad[1].toLowerCase()}</b> ({prioridad[2]}); empatan las otras dos prioridades. El retorno es venta esperada a precio de ML: la pestaña no trae costos. </> : null}
          {pubOk
            ? <>Publicado = en Mercado Libre <b>hoy</b>, activa o pausada, en cualquiera de las dos cuentas{pub.actualizado ? <> (sync de ML del {hora(pub.actualizado)})</> : null}.</>
            : pub.estado === "cargando" ? "Leyendo lo publicado en Mercado Libre…" : <span className="text-amber-700">No se pudo leer lo publicado en Mercado Libre: el filtro y la columna no lo muestran.</span>}
        </p>
      </div>

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
            <button type="button" onClick={() => setFiltros({ ...SIN_FILTROS, val: filtros.val })}
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
          <table className="w-full min-w-[1840px] border-collapse text-sm">
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
              {trozo.length === 0 && <tr><td colSpan={COLS.length} className="py-14 text-center text-sm text-slate-400">Nada con esos filtros.</td></tr>}
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
                        <div className="mt-1 flex flex-wrap gap-1"><Estado r={r} />{r.td && !r._mv.length ? <Etiqueta>título por confirmar</Etiqueta> : null}
                          {r._mv.filter((m) => m !== "sin_sku").map((m) => <Etiqueta key={m} tono="amber">{CORTO_MOTIVO[m]}</Etiqueta>)}</div>
                      </td>
                      <td className="min-w-[140px] px-3 py-2.5"><Publicacion r={r} /></td>
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
                        <td colSpan={COLS.length} className="border-b border-slate-200 p-0">
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
