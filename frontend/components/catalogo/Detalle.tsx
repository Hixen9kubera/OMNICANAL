"use client";

// Detalle.tsx — El detalle de una fila del CATÁLOGO, en tres columnas: la cuenta del producto y lo que dice Odoo;
// su packing list, renglón y título; y su precio en Mercado Libre y en Amazon. Port del artifact (`detalle`,
// `bloqueML`, `bloquePrecio`, `bloqueReferencia`, `notaV3`). Sin costo: el repo es público.

import type { ReactNode } from "react";
import { THEME_FALLBACK } from "@/lib/theme";
import { Foto } from "./Fotos";
import { Etiqueta, Mini } from "./Celdas";
import {
  FML_L, FV3, METODO, ORIGEN, REV3, type Catalogo, type Estimado, type Fila, type Precio, type V3,
  comoSeEstimo, fm, fm0, fuenteV3, nf,
} from "./logica";

const ML = THEME_FALLBACK.mercado_libre;
const AZ = THEME_FALLBACK.amazon;

function Bloque({ titulo, tema, children }: { titulo: string; tema?: typeof ML; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      {tema && <div className="h-1" style={{ background: tema.color }} />}
      <div className="space-y-2 p-3.5">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color: tema ? tema.texto : "#334155" }}>{titulo}</h3>
        {children}
      </div>
    </div>
  );
}

function KV({ filas }: { filas: [ReactNode, ReactNode, boolean?][] }) {
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-xs">
      {filas.map(([k, v, fuerte], i) => (
        <div key={i} className="contents">
          <dt className="text-slate-500">{k}</dt>
          <dd className={`text-right tabular-nums ${fuerte ? "font-bold text-slate-900" : "text-slate-700"}`}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function NotaV3({ v }: { v: V3 }) {
  const fs = v.fs ? Object.keys(v.fs).map((k) => `${FV3[k] || k} ${fm(v.fs![k])}`) : [];
  return (
    <>
      {fs.length > 0 && <Mini>Fuentes, por pieza y sin marcas de renombre: {fs.join(" · ")}.</Mini>}
      {v.fu && <Mini>Cómo salió: {fuenteV3(v.fu)}{v.cc ? " (con la categoría corregida)" : ""}.</Mini>}
      {v.an != null && <Mini>Precio anterior, con la banda de su categoría: {fm(v.an)}.</Mini>}
      {v.m && <Mini><b>Revisado a mano:</b> {fm(v.m[0] as number)} · {REV3[String(v.m[1])] || v.m[1]} · confianza {v.m[2] || "—"}
        {v.m[4] ? ` · ${v.m[4]} por un segundo revisor` : ""}{v.m[5] ? " · era precio de paquete contra pieza" : ""}{v.m[3] ? `. ${v.m[3]}` : ""}.</Mini>}
      {v.mq && v.mq.length > 0 && <Mini>Marcas que se sacaron de su banda: {v.mq.join(", ")}.</Mini>}
      {v.nt && v.nt.length > 0 && <Mini>Notas: {v.nt.join("; ")}.</Mini>}
      {v.rv ? <Mini><b>Para revisar:</b> las fuentes difieren mucho, la única fuente es parcial o no hubo fuentes.</Mini> : null}
    </>
  );
}

function BloqueML({ r }: { r: Fila }) {
  const p = r.ml!;
  const filas: [ReactNode, ReactNode, boolean?][] = [
    ["Precio usado (la media)", fm(p.m), true],
    ["De qué es la media", <span key="q" className="text-left">{FML_L[p.cl || ""]}</span>],
    ["Valor de lo que queda", r._tm != null ? fm0(r._tm) : "—", true],
  ];
  if (p.lo != null) {
    filas.push(["Mínimo · media · máximo", `${fm0(p.lo)} · ${fm(p.me)} · ${fm0(p.hi)}`]);
    if (p.d != null) filas.push(["Mediana", fm(p.d)], ["Rango central (P25–P75)", `${fm0(p.a)} – ${fm0(p.b)}`]);
    if (p.n) filas.push(["Publicaciones", `${p.n}${p.vd ? ` de ${p.vd} vendedores` : ""}`]);
  }
  if (p.cg) filas.push(["Categoría de Mercado Libre", p.cg]);
  if (p.pn) filas.push(["Nuestro precio publicado", fm(p.pn)]);
  if (p.dp != null) filas.push([`Precio depurado de Eduardo${p.cl === "k" ? " (7-oct)" : ""}`, `${fm(p.dp)}${r._q != null ? ` → ${fm0(r._q * p.dp)}` : ""}`]);
  const notas: string[] = [];
  if (p.cl === "k") notas.push("Es el precio de mercado de productos como este en Mercado Libre, sin contar marcas de renombre (precios v3 de Competencia). No son publicaciones del mismo producto.");
  if (p.cl === "f") notas.push(`La media es de las publicaciones que el juez marcó como el mismo producto${p.nv === "x" ? " (las de una variante hermana del mismo modelo)" : ""}.`);
  if (p.cl === "b") notas.push(`La media es de los más vendidos de su categoría${p.nv === "a" ? " (una categoría más amplia)" : ""}: una referencia, no publicaciones del mismo producto.`);
  if (p.cl === "n") notas.push("No hubo publicaciones que promediar: se usa nuestro propio precio publicado en Mercado Libre.");
  if (p.cl === "r") notas.push(`Revisado a mano entre las líneas de banda de mayor valor${p.rq ? `: ${p.rq}` : ""}.`);
  if (p.dp != null) notas.push(`El precio depurado de Eduardo es el de su regla: ${p.f === "f" ? "la mediana del mismo producto" : p.f === "n" ? "nuestro precio publicado" : p.f === "r" ? "el revisado a mano" : "la mediana de la banda de su categoría"}${p.aj ? ", bajado por su tope" : ""}.`);
  if (p.pk) notas.push(`El precio de Mercado Libre es de un paquete de ${p.pk} y aquí se cuentan piezas sueltas: está partido entre ${p.pk}.`);
  if (p.ue) notas.push(`El packing list del proveedor cuenta ${nf(p.ue)} unidades y aquí se cuentan ${nf(r.pc)} (bodega). Se usa el conteo de bodega${p.tp ? ", y el precio queda topado" : ""}.`);
  if (p.pa) notas.push(r.sin ? "Sus renglones están en el validado de bodega: el precio es el de un renglón con el mismo nombre en su contenedor." : "El archivo de Eduardo no trae este SKU: el precio es el del renglón del packing list que aquí se le empató, cotizado por la descripción de ese renglón.");
  if (p.ni) notas.push(`Esta fila junta ${p.ni} productos que Eduardo cotizó por separado (renglones con el mismo nombre): la media es su promedio por unidades; el mínimo y el máximo, los extremos.`);
  if (p.sg) notas.push("Cotizado en la segunda pasada, con la descripción del packing list traducida, porque el SKU no tenía título en el catálogo.");
  if (p.rz) notas.push("Para revisar: el precio de mercado difiere más de 2× de nuestro precio publicado.");
  return (
    <Bloque titulo="Mercado Libre · precio por pieza" tema={ML}>
      <KV filas={filas} />
      {notas.map((n, i) => <Mini key={i} className="mt-1">{n}</Mini>)}
      {p.v3 && <NotaV3 v={p.v3} />}
    </Bloque>
  );
}

function Referencia({ nombre, p, tema }: { nombre: string; p?: Precio; tema: typeof ML }) {
  if (!p) {
    return <Bloque titulo={nombre} tema={tema}><Mini>Sin precio de referencia: no se encontraron publicaciones del mismo producto.</Mini></Bloque>;
  }
  return (
    <Bloque titulo={nombre} tema={tema}>
      <KV filas={[
        ["Media (el dato principal)", fm(p.m), true], ["Mediana", fm(p.d)], ["Rango central (P25–P75)", `${fm0(p.a)} – ${fm0(p.b)}`],
        ["Mínimo – máximo", `${fm0(p.lo)} – ${fm0(p.hi)}`], ["Publicaciones comparables", p.n],
        ["De dónde sale", <span key="o" className="text-left">{ORIGEN[p.o || ""] || ""}{p.q ? ` «${p.q}»` : ""}</span>],
      ]} />
      {p.pk ? <Mini>Las publicaciones venden un paquete de {p.pk} y el inventario cuenta piezas sueltas: el precio está partido entre {p.pk}.</Mini> : null}
      {p.dz ? <Mini>No cuenta como precio medido: está a {p.dz} veces su precio de Mercado Libre. Casi siempre es otro producto con el mismo nombre, o un paquete contra una pieza.</Mini> : null}
      {(p.n ?? 0) < 2 && <Mini>Una sola publicación comparable: es un indicio, no cuenta como precio medido.</Mini>}
      {p.o === "c" && <Mini>Sale del catálogo de Mercado Libre, que es de productos de marca: es una referencia, no cuenta como precio medido.</Mini>}
      {p.x ? <Mini>No cuenta como precio medido: no hubo dos publicaciones con el mismo tamaño de paquete.</Mini> : null}
      {p.ej && p.ej.length > 0 && (
        <ul className="mt-1 space-y-1 border-t border-slate-100 pt-2 text-xs text-slate-600">
          {p.ej.map((e, i) => (
            <li key={i} className="flex justify-between gap-3">
              <a href={`https://www.amazon.com.mx/dp/${e[2]}`} target="_blank" rel="noreferrer" className="min-w-0 truncate hover:text-indigo-600 hover:underline">{e[0]}</a>
              <span className="shrink-0 tabular-nums">{fm0(e[1])}</span>
            </li>
          ))}
        </ul>
      )}
    </Bloque>
  );
}

function BloquePrecio({ nombre, p, tema, firme, est, valor, r }: {
  nombre: string; p?: Precio; tema: typeof ML; firme: boolean; est?: Estimado; valor: number | null; r: Fila;
}) {
  if (p && p.o === "e") return <BloqueML r={r} />;
  const esML = nombre.startsWith("Mercado Libre");
  return (
    <div className="space-y-2">
      {!firme && est && (
        <Bloque titulo={`${nombre.split(" · ")[0]} · precio estimado`} tema={tema}>
          <KV filas={[["Precio por pieza", fm(est[0]), true], ["Valor de lo que queda", fm0(valor), true]]} />
          <Mini>No tiene precio medido, así que se estima con {comoSeEstimo(est)}.</Mini>
        </Bloque>
      )}
      {firme && <Mini>Precio medido. Valor de lo que queda: <b>{fm0(valor)}</b></Mini>}
      {!firme && !est && r.nv ? (
        <>
          <Mini>No se valúa: es un insumo (empaque de otro producto o material de tienda), no mercancía para vender.</Mini>
          {r.mr && esML && <Mini>Referencia por pieza en Mercado Libre (revisada a mano el 7-oct; no se suma al valor): mínimo {fm(r.mr.lo)} · media {fm(r.mr.me)} · máximo {fm(r.mr.hi)}. {r.mr.rq || ""}</Mini>}
        </>
      ) : null}
      {r.v3 && esML && (
        <Bloque titulo="Mercado Libre · precios v3" tema={tema}>
          <Mini>El paquete de precios v3 no encontró ninguna fuente de precio para este producto{r.em ? ": queda con su estimado por costo." : ": queda sin precio de Mercado Libre."}</Mini>
          <NotaV3 v={r.v3} />
        </Bloque>
      )}
      <Referencia nombre={nombre} p={p} tema={tema} />
    </div>
  );
}

const TITULO_DE: Record<string, string> = {
  ml: "Es el de su publicación en Mercado Libre.", foto: "Lo escribió la IA viendo la foto del producto.", actual: "Se dejó el que tenía.",
};

export default function Detalle({ D, r }: { D: Catalogo; r: Fila }) {
  const cuenta: [ReactNode, ReactNode, boolean?][] = [];
  if (r.pc != null) {
    cuenta.push(["Comprado (packing list)", nf(r.pc), true]);
    if (r.pp != null) cuenta.push(["Según el proveedor", nf(r.pp)]);
  } else if (r.pe != null) cuenta.push(["Comprado, según la base de costos", `≈ ${nf(r.pe)}`]);
  cuenta.push(["Salió de bodega (Odoo)", nf(r.pz || r.ps || 0), true]);
  if (r.pz) cuenta.push([<span key="u" className="pl-3">en la unidad de Odoo</span>, `${nf(r.ps)} × ${r.uf}`]);
  for (const k of Object.keys(r.cl || {})) cuenta.push([<span key={k} className="pl-3">{k === "proveedor" ? "Devuelto al proveedor" : D.rot[k] || k}</span>, nf(r.cl![k])]);
  if (r.pc != null) cuenta.push(["Queda", r.so ? "0 · SOLD OUT" : nf(r.pq), true]);

  return (
    <div className="grid grid-cols-1 gap-3 bg-slate-50/80 p-4 lg:grid-cols-3">
      <div className="space-y-3">
        <div className="flex gap-3 rounded-xl border border-slate-200 bg-white p-3">
          <Foto r={r} px={120} />
          <div className="min-w-0 text-xs text-slate-600">
            <div className="font-mono font-bold text-slate-900">{r.sin ? "Sin SKU" : r.s}</div>
            <div className="mt-1 text-[13px] text-slate-800">{r.t}</div>
            <div className="mt-2">{r.fo === "pl" ? <Etiqueta tono="emerald">foto del packing list</Etiqueta> : r.fo === "odoo" ? <Etiqueta>foto de Odoo</Etiqueta> : <Etiqueta>sin foto</Etiqueta>}</div>
          </div>
        </div>
        <Bloque titulo="La cuenta de este producto">
          <KV filas={cuenta} />
          {r.uf ? <Mini>Odoo recibió exactamente 1 de cada {r.uf} piezas del packing list: cuenta en otra unidad (cartones o paquetes de {r.uf}). {r.uf >= 5 ? `Sus salidas se multiplicaron por ${r.uf} antes de restar.` : "Con un factor tan chico puede ser media recepción: no se ajustó."}</Mini> : null}
          {r.dm ? <Mini className="text-rose-600">Odoo sacó {nf(r.dm)} piezas más de las que dice el packing list: el SKU llegó también en otro embarque, o se cuenta en otra unidad.</Mini> : null}
          {r.pe != null && <Mini>No se ubicó su renglón: sus piezas ya están contadas entre los productos sin SKU del contenedor {String(r.ec)}, por eso aquí no suman.</Mini>}
          {r.v1 && <Mini>Primera salida {r.v1} · última {r.v2}</Mini>}
        </Bloque>
        <Bloque titulo="Lo que dice Odoo">
          <KV filas={[["Recibió (proveedor y traslados)", nf(r.en || 0)], ["Ajustes de inventario, neto", r.aj ? `${r.aj > 0 ? "+" : ""}${nf(r.aj)}` : "0"], ["Stock libre hoy (free_qty)", nf(r.lb || 0), true]]} />
          {r.v != null && <Mini>Nuestro precio de lista: <b>{fm(r.v)}</b></Mini>}
        </Bloque>
      </div>
      <div className="space-y-3">
        <Bloque titulo="Packing list y renglón">
          {r.rn && r.rn.length ? (
            <ul className="space-y-1.5 text-xs text-slate-700">
              {r.rn.map((x, i) => (
                <li key={i} className="leading-snug">
                  Contenedor {String(x.c)}
                  {x.av != null && <> · validado «{D.A[String(x.av)] || x.av}» renglón {String(x.fv)}</>}
                  {x.ao != null && <>{x.av != null ? " → " : " · "}original «{D.A[String(x.ao)] || x.ao}» renglón {(x.fo || []).join(", ")}</>}
                  {" "}{x.m ? <Etiqueta tono={x.m === "foto" ? "emerald" : "slate"}>{METODO[x.m] || x.m}</Etiqueta>
                    : x.av != null && x.ao == null ? <Etiqueta>{METODO.bodega}</Etiqueta> : null}
                </li>
              ))}
              {r.rm ? <li><Mini>y {r.rm} renglones más</Mini></li> : null}
              {(r.nr ?? 0) > r.rn.length && <li><Mini>{nf(r.nr)} renglones con este mismo nombre</Mini></li>}
            </ul>
          ) : (
            <Mini>{r.pe != null ? `Renglón no ubicado. La base de costos lo manda al contenedor ${String(r.ec)}.` : "Este SKU no aparece en ningún packing list leído."}</Mini>
          )}
        </Bloque>
        <Bloque titulo="Título">
          <div className="text-[13px] text-slate-800">{r.t}</div>
          <Mini>{TITULO_DE[r.tf || ""] || "Lo mejoró la IA a partir del nombre del producto."}{r.td ? " La IA avisó que lo está suponiendo." : ""}</Mini>
          {r.n && <Mini>Nombre original: {r.n}</Mini>}
          {r.tg && (
            <Mini className="mt-1"><b>Título propuesto</b> (IA viendo la foto; el color sale de la foto): {r.tg}
              {r.tv === "n" ? <> · <b>la foto no corresponde al título actual</b></> : r.tv === "p" ? " · el título actual no alcanza" : ""}
              {r.tfm === "n" ? " · la foto del catálogo y la del packing list son productos distintos" : ""}
              {r.trv ? ` · revisar con una persona${r.trm ? `: ${r.trm}` : ""}` : ""}</Mini>
          )}
        </Bloque>
      </div>
      <div className="space-y-3">
        <BloquePrecio nombre="Mercado Libre · competencia por pieza" p={r.ml} tema={ML} firme={r._fm} est={r.em} valor={r._tm} r={r} />
        <BloquePrecio nombre="Amazon México · competencia por pieza" p={r.az} tema={AZ} firme={r._fa} est={r.ea} valor={r._ta} r={r} />
      </div>
    </div>
  );
}
