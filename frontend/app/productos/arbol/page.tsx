"use client";

/**
 * Publicador · vista de ÁRBOL (diseño G, Eduardo 9-sep-2026).
 *
 * EL PROBLEMA QUE RESUELVE. La lista de hoy corre APLANADA: cada variante es
 * una fila suelta mezclada con los productos simples y sin ninguna señal de
 * quién es su padre. En una muestra de 100 filas, 35 eran variantes huérfanas
 * a la vista. Medido en producción: 7,475 variantes de 1,502 padres.
 *
 * LO QUE NO HIZO FALTA CONSTRUIR. El backend ya sabía agrupar: `?aplanar=false`
 * devuelve 2,939 filas donde el padre viene como `tipo: "variable"` con sus
 * variantes COMPLETAS dentro (`variantes[]`, con precio, costo, stock y
 * estado). Lo único que faltaba era la pantalla. Por eso esta vista no agrega
 * ni un endpoint.
 *
 * POR QUÉ CARGA POR CATEGORÍA Y NO TODO. El árbol pide los productos de UNA
 * categoría cuando se abre, no los 2,939 de golpe. `_categorias/lista` trae la
 * jerarquía de WooCommerce (con `parent`), y de ahí cuelgan los padres.
 *
 * EL TOPE DE VARIANTES NO ES COSMÉTICO. `TEC-0377` tiene 103 variantes, una
 * por modelo de teléfono; `CALZ-0058` tiene 90. Volcar 103 renglones al abrir
 * un nodo hace inservible el árbol, así que se muestran las primeras y el
 * resto se pide a mano.
 *
 * LA AGRUPACIÓN TODAVÍA NO SE GUARDA. El interruptor individual/grupo vive en
 * memoria: no hay dónde persistirlo (haría falta una tabla y decidir si la
 * decisión es global o por canal — ver el diseño E). Se deja funcionando para
 * poder evaluarlo, y la pantalla lo dice en voz alta en vez de aparentar que
 * quedó guardado.
 *
 * BUSCAR POR SKU (Eduardo, 10-sep-2026). Tampoco agregó endpoint: `search` con
 * `aplanar=false` ya devuelve al PADRE cuando lo que se escribe es el SKU de
 * una variante —medido: `TEC-1196-NEG` trae a `TEC-1196` con sus dos
 * variantes dentro—, así que el resultado se pinta con el mismo nodo del
 * árbol, con las variantes que coinciden primero y resaltadas.
 *
 * CLIC EN UN SKU ABRE EL ESTUDIO, el mismo de Productos. Si el padre está en
 * «grupo de variantes», la variante se abre en SOLO LECTURA: se puede entrar y
 * revisar, pero sus datos viajan en la publicación del grupo, y editarla o
 * publicarla sola contradiría la agrupación. El padre y los simples se abren
 * editables.
 *
 * LOS CANALES DE UNA VARIANTE MIENTEN POR OMISIÓN. TEC-1196 está en TikTok, ML
 * y Amazon; sus dos variantes traen `canales: []` porque la publicación cuelga
 * del SKU del padre. Pintar "—" diría que no se venden en ningún lado, así que
 * la variante sin publicación propia muestra los del padre, atenuados y
 * marcados como tales.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle, ChevronRight, Folder, Layers, Loader2, Lock, Package, Search, Wand2, X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import ChannelDots from "@/components/ChannelDots";
import ProductStudio from "@/components/ProductStudio";
import { listarCanales, listarCategorias, listarProductos, type CategoriaWC } from "@/lib/api";
import type { CanalInfo, CanalResumen, Producto, VarianteResumen } from "@/lib/types";

const INDIGO = "#4F46E5";
/** Cuántas variantes se pintan al abrir un padre. Ver la nota de arriba. */
const TOPE_VARIANTES = 8;
/** Los padres que se piden al abrir una categoría.
 *
 *  100 y no más porque es el TECHO del endpoint (`PER_PAGE_MAX` en
 *  routers/productos.py): pedir 200 devuelve un 422 de validación, no una
 *  lista recortada. Fue el error de la v0.478.0 — se pidió 200 sin mirar el
 *  límite y la pantalla abrió con una banda roja.
 *
 *  Y alcanza de sobra: medido contra producción, la categoría más grande de
 *  WooCommerce tiene 21 productos ("Fundas y Carcasas"); ninguna de las 300
 *  pasa de 100. Si algún día una creciera, esto la truncaría en silencio —
 *  por eso la pantalla avisa cuando el total supera lo que trajo. */
const POR_CATEGORIA = 100;
/** El buscador espera a que se deje de teclear, y no busca con menos de dos
 *  caracteres: una sola letra traería medio catálogo. */
const ESPERA_BUSQUEDA = 350;
const MIN_BUSQUEDA = 2;
/** Columnas de la tabla del detalle —casilla, SKU, costo, regular, oferta,
 *  stock, canales—: una sola constante para que encabezado y filas no se
 *  desalineen. */
const COLUMNAS = "grid-cols-[28px_minmax(0,1fr)_100px_100px_100px_56px_120px]";

type Modo = "individual" | "grupo";
interface Mapas { color: Record<string, string>; label: Record<string, string> }
/** Lo que se abre en el Estudio: el SKU, su `Producto` para pintarlo al
 *  instante, y si entra en solo lectura (con el porqué). */
interface Apertura { sku: string; producto: Producto; soloLectura: boolean; aviso?: string }

function pesos(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency", currency: "MXN", minimumFractionDigits: 2,
  }).format(v);
}

/** El default no es arbitrario: publicar 103 fundas por separado es justo lo
 *  que la pantalla existe para evitar. Abajo de 8 variantes casi siempre son
 *  colores o tallas de un mismo artículo y publicarlas sueltas es razonable. */
function modoSugerido(p: Producto): Modo {
  return (p.variantes?.length ?? 0) > 8 ? "grupo" : "individual";
}

/** ¿El SKU contiene lo que se buscó? Sin búsqueda, nada coincide. */
function coincide(sku: string, q: string): boolean {
  return q.length >= MIN_BUSQUEDA && sku.toLowerCase().includes(q.toLowerCase());
}

/** Las variantes que coinciden con la búsqueda van primero: con 103 fundas, la
 *  que se buscó no puede quedar escondida detrás del tope. `sort` es estable,
 *  así que el resto conserva el orden de Woo. */
function primeroLasQueCoinciden(vars: VarianteResumen[], q: string): VarianteResumen[] {
  if (q.length < MIN_BUSQUEDA) return vars;
  return [...vars].sort((a, b) => Number(coincide(b.sku, q)) - Number(coincide(a.sku, q)));
}

/** La variante vestida de `Producto`, para que el Estudio la pinte al instante
 *  mientras trae su detalle. Lo delicado es el wc_id: el Estudio escribe y
 *  PUBLICA con `producto.wc_id` por encima del que resuelve solo, así que
 *  heredar el del padre haría que una variante editada le escribiera encima al
 *  padre. Va el de la variación, o null para que el Estudio lo busque por SKU. */
function comoProducto(p: Producto, v: VarianteResumen): Producto {
  const canales = v.canales ?? [];
  return {
    ...p,
    sku: v.sku,
    wc_id: v.wc_id ?? null,
    odoo_id: null,
    nombre: v.nombre ? `${p.nombre} - ${v.nombre}` : p.nombre,
    precio: v.precio,
    precio_base: v.precio_base ?? null,
    precio_oferta: v.precio_oferta ?? null,
    stock: v.stock,
    stock_real: null,
    stock_full: null,
    stock_fba: null,
    situacion: null,
    estado: v.estado,
    full: null,
    full_label: null,
    publicado: canales.some((c) => c.publicado),
    item_id: null,
    url: null,
    cuenta: null,
    canales,
    costo: v.costo,
    valor: v.valor,
    costo_rango: null,
    precio_rango: null,
    contenedor: v.contenedor,
    tipo: "variation",
    variantes: [],
    revisado_at: v.revisado_at,
    revisado_por: v.revisado_por,
    revision_variantes: null,
  };
}

/** Lo que comparten el árbol y los resultados de búsqueda para pintar un nodo. */
interface Ctx {
  abiertoPadre: Record<string, boolean>;
  tope: Record<string, number>;
  modoDe: (p: Producto) => Modo;
  sel: string | null;
  resaltar: string;
  onPadre: (sku: string) => void;
  onMasVariantes: (sku: string, n: number) => void;
  onVariante: (p: Producto, v: VarianteResumen) => void;
}

export default function PublicadorArbolPage() {
  const [cats, setCats] = useState<CategoriaWC[]>([]);
  const [abiertas, setAbiertas] = useState<Record<number, boolean>>({});
  const [productos, setProductos] = useState<Record<number, Producto[]>>({});
  const [cargando, setCargando] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [truncadas, setTruncadas] = useState<Record<number, number>>({});

  const [abiertoPadre, setAbiertoPadre] = useState<Record<string, boolean>>({});
  const [tope, setTope] = useState<Record<string, number>>({});
  const [modo, setModo] = useState<Record<string, Modo>>({});
  const [fuera, setFuera] = useState<Record<string, Record<string, true>>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [filtro, setFiltro] = useState("");

  const [canales, setCanales] = useState<CanalInfo[]>([]);
  const [vistos, setVistos] = useState<Record<string, Producto>>({});
  const [busqueda, setBusqueda] = useState("");
  const [resultados, setResultados] = useState<Producto[] | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [recarga, setRecarga] = useState(0);
  const [estudio, setEstudio] = useState<Apertura | null>(null);

  useEffect(() => {
    listarCategorias()
      .then(setCats)
      .catch((e: Error) => setError(e.message || "No se pudieron leer las categorías."));
    // Los mismos canales que usa Productos: dan color a los puntos y las
    // pestañas al Estudio.
    listarCanales().then(setCanales).catch(() => setCanales([]));
  }, []);

  const mapas = useMemo<Mapas>(() => ({
    color: Object.fromEntries(canales.map((c) => [c.id, c.color])),
    label: Object.fromEntries(canales.map((c) => [c.id, c.label])),
  }), [canales]);

  // Solo las RAÍCES cuelgan del árbol; las hijas se agrupan bajo su padre. Woo
  // devuelve la jerarquía plana con `parent`, así que se arma aquí.
  const raices = useMemo(() => cats.filter((c) => !c.parent), [cats]);
  const hijasDe = useMemo(() => {
    const m: Record<number, CategoriaWC[]> = {};
    cats.forEach((c) => { if (c.parent) (m[c.parent] ||= []).push(c); });
    return m;
  }, [cats]);

  // Todo producto que llega —de una categoría o de una búsqueda— se indexa por
  // SKU: así el detalle no depende de DÓNDE se encontró y sobrevive a limpiar
  // la búsqueda.
  const recordar = useCallback((lista: Producto[]) => {
    setVistos((v) => {
      const n = { ...v };
      lista.forEach((p) => { n[p.sku] = p; });
      return n;
    });
  }, []);

  const traerCategoria = useCallback((id: number) => {
    setCargando((c) => ({ ...c, [id]: true }));
    // `aplanar: false` es TODO el truco: así el padre llega con sus variantes
    // dentro en vez de desperdigadas como filas hermanas.
    listarProductos({ canal: "general", categoria: id, perPage: POR_CATEGORIA, aplanar: false })
      .then((r) => {
        setProductos((p) => ({ ...p, [id]: r.items }));
        recordar(r.items);
        // ¿Se truncó? Se mira si vino la página COMPLETA, no si `total` supera
        // a `items`: con `aplanar=false` el total se cuenta ANTES de agrupar,
        // así que una categoría sana devuelve 29 items sobre un total de 37 —
        // los 8 de diferencia son variantes que se metieron dentro de su padre.
        // Comparar contra el total marcaría truncadas TODAS las categorías con
        // variantes, que son justo las que esta pantalla existe para mostrar.
        if (r.items.length >= POR_CATEGORIA) {
          setTruncadas((t) => ({ ...t, [id]: r.items.length }));
        }
      })
      .catch((e: Error) => setError(e.message || "No se pudieron leer los productos."))
      .finally(() => setCargando((c) => ({ ...c, [id]: false })));
  }, [recordar]);

  const abrirCategoria = useCallback((id: number) => {
    setAbiertas((a) => ({ ...a, [id]: !a[id] }));
    if (productos[id] || cargando[id]) return;
    traerCategoria(id);
  }, [productos, cargando, traerCategoria]);

  // ── Búsqueda por SKU ───────────────────────────────────────────────────
  const q = busqueda.trim();
  const enBusqueda = q.length >= MIN_BUSQUEDA;
  useEffect(() => {
    if (!enBusqueda) {
      setResultados(null);
      setBuscando(false);
      return;
    }
    const ctrl = new AbortController();
    setBuscando(true);
    const t = setTimeout(() => {
      listarProductos({ canal: "general", search: q, perPage: POR_CATEGORIA, aplanar: false }, ctrl.signal)
        .then((r) => {
          setResultados(r.items);
          recordar(r.items);
          // Un solo resultado se abre en el detalle sin pedir otro clic: el
          // caso típico es pegar el SKU exacto de una variante.
          if (r.items.length === 1) {
            const unico = r.items[0].sku;
            setSel(unico);
            setAbiertoPadre((a) => ({ ...a, [unico]: true }));
          }
        })
        .catch((e: Error) => {
          if (!ctrl.signal.aborted) setError(e.message || "No se pudo buscar.");
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setBuscando(false);
        });
    }, ESPERA_BUSQUEDA);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q, enBusqueda, recarga, recordar]);

  const padre = sel ? vistos[sel] ?? null : null;
  const modoDe = (p: Producto): Modo => modo[p.sku] ?? modoSugerido(p);

  const abrirPadre = (p: Producto) => setEstudio({ sku: p.sku, producto: p, soloLectura: false });
  const abrirVariante = (p: Producto, v: VarianteResumen) => {
    const grupo = modoDe(p) === "grupo";
    setSel(p.sku);
    setEstudio({
      sku: v.sku,
      producto: comoProducto(p, v),
      soloLectura: grupo,
      aviso: grupo
        ? `Solo lectura · ${v.sku} se publica dentro del grupo ${p.sku}, así que sus datos viajan en la publicación del padre. Para editarla sola, pasa ${p.sku} a «Publicación individual».`
        : undefined,
    });
  };

  // Tras guardar en el Estudio (costo, precios) la fila ya cambió: se vuelve a
  // pedir lo que la contiene — la búsqueda vigente y las categorías abiertas
  // donde vive el padre.
  const recargar = useCallback(() => {
    if (enBusqueda) setRecarga((n) => n + 1);
    if (!sel) return;
    Object.entries(productos).forEach(([id, lista]) => {
      if (lista.some((x) => x.sku === sel)) traerCategoria(Number(id));
    });
  }, [enBusqueda, sel, productos, traerCategoria]);

  const ctx: Ctx = {
    abiertoPadre, tope, modoDe, sel, resaltar: "",
    onPadre: (sku) => {
      setAbiertoPadre((a) => ({ ...a, [sku]: !a[sku] }));
      setSel(sku);
    },
    onMasVariantes: (sku, n) => setTope((t) => ({ ...t, [sku]: n })),
    onVariante: abrirVariante,
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">

        <div
          className="relative overflow-hidden rounded-3xl p-6 text-white shadow-card"
          style={{ background: `linear-gradient(120deg, ${INDIGO} 0%, #7C6CF0 100%)` }}
        >
          <div className="relative z-10">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
              <Wand2 size={14} /> Estudio de producto
            </div>
            <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Publicador</h1>
            <p className="mt-1 max-w-2xl text-sm opacity-90">
              El catálogo como árbol: categoría, producto padre y sus variantes. Busca un SKU o
              abre una categoría; clic en cualquier SKU lo abre en el Estudio.
            </p>
          </div>
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/20" />
        </div>

        {error && (
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <AlertTriangle size={16} /> {error}
          </div>
        )}

        <div className="mt-5 grid gap-4 lg:grid-cols-[380px_1fr]">

          {/* ── EL ÁRBOL ───────────────────────────────────────────────── */}
          <section className="flex max-h-[760px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
            <div className="border-b border-slate-100 p-3">
              <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={busqueda}
                  onChange={(e) => setBusqueda(e.target.value)}
                  placeholder="Buscar por SKU…"
                  className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-8 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
                />
                {busqueda && (
                  <button
                    onClick={() => setBusqueda("")}
                    title="Limpiar la búsqueda y volver al árbol"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
              {!enBusqueda && (
                <div className="relative mt-2">
                  <Folder size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-300" />
                  <input
                    value={filtro}
                    onChange={(e) => setFiltro(e.target.value)}
                    placeholder="Filtrar categorías…"
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs text-slate-600 outline-none placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-indigo-200"
                  />
                </div>
              )}
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-400">
                {!enBusqueda ? (
                  `${cats.length} categorías con productos`
                ) : buscando ? (
                  <><Loader2 size={11} className="animate-spin" /> buscando «{q}»…</>
                ) : (
                  `${resultados?.length ?? 0} ${resultados?.length === 1 ? "producto" : "productos"} con «${q}»`
                )}
              </div>
            </div>

            <div className="flex-1 overflow-auto p-2">
              {enBusqueda ? (
                <>
                  {resultados && !resultados.length && !buscando && (
                    <div className="p-4 text-sm text-slate-400">
                      Nada con «{q}». Los borradores de Crear no aparecen aquí.
                    </div>
                  )}
                  {(resultados ?? []).map((p) => (
                    <NodoProducto key={p.sku} p={p} ctx={{ ...ctx, resaltar: q }} />
                  ))}
                  {resultados && resultados.length >= POR_CATEGORIA && (
                    <div className="px-2 py-1 text-[11px] text-amber-600">
                      solo los primeros {POR_CATEGORIA} — afina la búsqueda
                    </div>
                  )}
                </>
              ) : (
                <>
                  {!cats.length && !error && (
                    <div className="flex items-center gap-2 p-4 text-sm text-slate-400">
                      <Loader2 size={15} className="animate-spin" /> Leyendo categorías…
                    </div>
                  )}
                  {raices
                    .filter((c) => !filtro || c.nombre.toLowerCase().includes(filtro.toLowerCase()))
                    .map((cat) => (
                      <Rama
                        key={cat.id}
                        cat={cat}
                        hijas={hijasDe[cat.id] ?? []}
                        abiertas={abiertas}
                        productos={productos}
                        cargando={cargando}
                        onCategoria={abrirCategoria}
                        truncadas={truncadas}
                        ctx={ctx}
                      />
                    ))}
                </>
              )}
            </div>
          </section>

          {/* ── DETALLE ────────────────────────────────────────────────── */}
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
            {!padre ? (
              <div className="flex h-[760px] flex-col items-center justify-center gap-2 text-slate-300">
                <Package size={34} />
                <p className="text-sm">Busca un SKU o elige un producto en el árbol</p>
              </div>
            ) : (
              <Detalle
                p={padre}
                modo={modoDe(padre)}
                fuera={fuera[padre.sku] ?? {}}
                onModo={(m) => setModo((x) => ({ ...x, [padre.sku]: m }))}
                onAlternar={(sku) =>
                  setFuera((f) => {
                    const actual = { ...(f[padre.sku] ?? {}) };
                    if (actual[sku]) delete actual[sku];
                    else actual[sku] = true;
                    return { ...f, [padre.sku]: actual };
                  })
                }
                mapas={mapas}
                resaltar={enBusqueda ? q : ""}
                onAbrirPadre={abrirPadre}
                onAbrirVariante={abrirVariante}
              />
            )}
          </section>
        </div>
      </main>

      {/* El Estudio de siempre (overlay); en solo lectura si la variante vive en un grupo. */}
      <ProductStudio
        sku={estudio?.sku ?? null}
        producto={estudio?.producto ?? null}
        canales={canales}
        onClose={() => setEstudio(null)}
        onGuardado={recargar}
        soloLectura={estudio?.soloLectura}
        avisoSoloLectura={estudio?.aviso}
      />
    </div>
  );
}

/* ── Una rama del árbol: categoría → (subcategorías) → padres → variantes ── */
function Rama({
  cat, hijas, abiertas, productos, cargando, onCategoria, truncadas, ctx, nivel = 0,
}: {
  cat: CategoriaWC;
  hijas: CategoriaWC[];
  abiertas: Record<number, boolean>;
  productos: Record<number, Producto[]>;
  cargando: Record<number, boolean>;
  onCategoria: (id: number) => void;
  truncadas: Record<number, number>;
  ctx: Ctx;
  nivel?: number;
}) {
  const abierta = !!abiertas[cat.id];
  const lista = productos[cat.id] ?? [];

  return (
    <div style={{ paddingLeft: nivel ? 14 : 0 }}>
      <button
        onClick={() => onCategoria(cat.id)}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-50"
      >
        <ChevronRight size={13} className={`shrink-0 text-slate-400 transition-transform ${abierta ? "rotate-90" : ""}`} />
        <Folder size={14} className="shrink-0 text-slate-400" />
        <span className="flex-1 truncate text-slate-700">{cat.nombre}</span>
        <span className="shrink-0 text-[10px] text-slate-300">{cat.count}</span>
      </button>

      {abierta && (
        <div className="ml-[13px] border-l border-slate-100 pl-2">
          {hijas.map((h) => (
            <Rama
              key={h.id} cat={h} hijas={[]} abiertas={abiertas} productos={productos}
              cargando={cargando} onCategoria={onCategoria} truncadas={truncadas}
              ctx={ctx} nivel={nivel + 1}
            />
          ))}

          {cargando[cat.id] && (
            <div className="flex items-center gap-2 px-2 py-2 text-[11px] text-slate-400">
              <Loader2 size={12} className="animate-spin" /> trayendo productos…
            </div>
          )}

          {!cargando[cat.id] && !lista.length && !hijas.length && (
            <div className="px-2 py-2 text-[11px] text-slate-300">sin productos aquí</div>
          )}

          {truncadas[cat.id] ? (
            <div className="px-2 py-1 text-[11px] text-amber-600">
              solo los primeros {truncadas[cat.id]} — esta categoría creció y ya no cabe de una vez
            </div>
          ) : null}

          {lista.map((p) => <NodoProducto key={p.sku} p={p} ctx={ctx} />)}
        </div>
      )}
    </div>
  );
}

/* ── Un producto, en el árbol o en los resultados: clic en el nombre lo lleva
      al detalle; clic en una variante la abre en el Estudio. ────────────── */
function NodoProducto({ p, ctx }: { p: Producto; ctx: Ctx }) {
  const vars = primeroLasQueCoinciden(p.variantes ?? [], ctx.resaltar);
  const esPadre = vars.length > 0;
  const abierto = !!ctx.abiertoPadre[p.sku];
  const n = ctx.tope[p.sku] ?? TOPE_VARIANTES;
  const grupo = ctx.modoDe(p) === "grupo";

  return (
    <div>
      <button
        onClick={() => ctx.onPadre(p.sku)}
        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-slate-50 ${
          ctx.sel === p.sku ? "bg-indigo-50 font-semibold text-indigo-700" : "text-slate-700"
        }`}
      >
        {esPadre ? (
          <ChevronRight size={12} className={`shrink-0 text-slate-400 transition-transform ${abierto ? "rotate-90" : ""}`} />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {/* El cuadrito dice de un vistazo cómo se publicaría: indigo
            grupo, ámbar individual, gris sin variantes. */}
        <span
          className="h-2 w-2 shrink-0 rounded-[2px]"
          style={{ background: !esPadre ? "#cbd5e1" : grupo ? INDIGO : "#f59e0b" }}
        />
        <span className="flex-1 truncate">{p.nombre}</span>
        {coincide(p.sku, ctx.resaltar) && (
          <span className="shrink-0 rounded bg-amber-100 px-1 font-mono text-[9px] font-normal text-amber-700">{p.sku}</span>
        )}
        {esPadre && <span className="shrink-0 text-[10px] text-slate-300">{vars.length}</span>}
      </button>

      {abierto && esPadre && (
        <div className="ml-[11px] border-l border-slate-100 pl-3">
          {vars.slice(0, n).map((v) => (
            <button
              key={v.sku}
              onClick={() => ctx.onVariante(p, v)}
              title={grupo ? "Abrir en solo lectura: se publica dentro del grupo" : "Abrir en el Estudio para editar y publicar"}
              className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[11px] text-slate-500 hover:bg-slate-50 ${
                coincide(v.sku, ctx.resaltar) ? "bg-amber-50" : ""
              }`}
            >
              <span className="font-mono text-indigo-500">{v.sku}</span>
              <span className="min-w-0 flex-1 truncate">{v.nombre}</span>
              {grupo && <Lock size={10} className="shrink-0 text-slate-300" />}
            </button>
          ))}
          {vars.length > n && (
            <button
              onClick={() => ctx.onMasVariantes(p.sku, n + 25)}
              className="px-2 py-1 text-[11px] font-semibold text-indigo-600 hover:underline"
            >
              +{vars.length - n} variantes
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ── El panel derecho: el padre elegido, su agrupación y sus variantes ───── */
function Detalle({
  p, modo, fuera, onModo, onAlternar, mapas, resaltar, onAbrirPadre, onAbrirVariante,
}: {
  p: Producto;
  modo: Modo;
  fuera: Record<string, true>;
  onModo: (m: Modo) => void;
  onAlternar: (sku: string) => void;
  mapas: Mapas;
  resaltar: string;
  onAbrirPadre: (p: Producto) => void;
  onAbrirVariante: (p: Producto, v: VarianteResumen) => void;
}) {
  const variantes = primeroLasQueCoinciden(p.variantes ?? [], resaltar);
  const esPadre = variantes.length > 0;
  const grupo = esPadre && modo === "grupo";
  const dentro = variantes.filter((v) => !fuera[v.sku]).length;
  const canalesPadre = p.canales ?? [];
  // El padre no se costea (el packing list trae variantes): si sus variantes
  // tienen costo en kubera, el backend manda el rango.
  const rango = p.costo_rango;
  const costo = rango && rango.min !== rango.max
    ? `${pesos(rango.min)} – ${pesos(rango.max)}`
    : pesos(rango?.min ?? p.costo);
  // Cuántas variantes respaldan ese costo cuando no son todas: "$17.88" a secas
  // se leería como el costo de TEC-1196 entero, y solo lo tiene la negra.
  const respaldo = rango && rango.n < rango.total ? ` (${rango.n} de ${rango.total})` : "";
  // En un padre, regular y oferta son el MÍNIMO entre sus variantes.
  const desde = esPadre ? "desde " : "";

  return (
    <div className="flex h-[760px] flex-col">
      <div className="border-b border-slate-100 p-5">
        <div className="text-[11px] text-slate-400">
          {(p.categoria_path ?? []).map((c) => c.nombre).join(" › ") || "Sin categoría"}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-bold text-slate-900">{p.nombre}</h2>
          <BotonSku
            sku={p.sku}
            onClick={() => onAbrirPadre(p)}
            titulo={esPadre ? "Abrir el padre en el Estudio" : "Abrir en el Estudio para editar y publicar"}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-500">
          {esPadre && <span><b className="font-semibold text-slate-700">{variantes.length}</b> variantes</span>}
          <span>Costo <b className="font-semibold text-slate-700">{costo}</b>{respaldo}</span>
          <span>Regular {desde}<b className="font-semibold text-slate-700">{pesos(p.precio_base)}</b></span>
          <span>Oferta {desde}<b className="font-semibold text-slate-700">{pesos(p.precio_oferta)}</b></span>
          <span className="flex items-center gap-1.5">
            Canales <ChannelDots canales={canalesPadre} colorMap={mapas.color} labelMap={mapas.label} />
          </span>
        </div>
      </div>

      {/* Agrupación: solo existe la pregunta si hay variantes que agrupar. */}
      {esPadre && (
        <div className="border-b border-slate-100 bg-slate-50/60 p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">Cómo se publica</div>
              <p className="mt-1 max-w-md text-xs text-slate-500">
                {grupo
                  ? "Una publicación por canal, con las variantes marcadas dentro. El comprador elige en la publicación. Las variantes se abren en solo lectura: se editan en el padre."
                  : `Cada variante se publica por separado: serían ${variantes.length} publicaciones por canal.`}
              </p>
            </div>
            <div className="flex overflow-hidden rounded-lg border border-slate-200 bg-white">
              {(["individual", "grupo"] as Modo[]).map((m) => (
                <button
                  key={m}
                  onClick={() => onModo(m)}
                  className={`px-4 py-1.5 text-xs font-semibold transition-colors ${
                    modo === m ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {m === "individual" ? "Publicación individual" : "Grupo de variantes"}
                </button>
              ))}
            </div>
          </div>
          {grupo && (
            <div className="mt-3 text-xs text-slate-600">
              <b className="text-indigo-600">{dentro}</b> de {variantes.length} entran al grupo
            </div>
          )}
          {/* Se dice, no se disimula: el interruptor no persiste todavía. */}
          <div className="mt-3 flex items-center gap-1.5 text-[11px] text-amber-700">
            <AlertTriangle size={12} />
            La agrupación aún no se guarda — falta decidir dónde vive y si es por canal.
          </div>
        </div>
      )}

      <div className={`grid ${COLUMNAS} items-center gap-2 border-b border-slate-100 px-5 py-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400`}>
        <span />
        <span className="flex items-center gap-1.5"><Layers size={12} /> {esPadre ? "Variante" : "Producto"}</span>
        <span className="text-right">Costo unitario</span>
        <span className="text-right">Precio regular</span>
        <span className="text-right">Precio oferta</span>
        <span className="text-right">Stock</span>
        <span>Canales</span>
      </div>

      <div className="flex-1 overflow-auto">
        {esPadre ? (
          variantes.map((v) => (
            <Fila
              key={v.sku}
              sku={v.sku}
              nombre={v.nombre}
              costo={v.costo}
              heredado={v.costo_propio === false && v.costo != null}
              base={v.precio_base ?? null}
              oferta={v.precio_oferta ?? null}
              stock={v.stock}
              propios={v.canales ?? []}
              delPadre={canalesPadre}
              mapas={mapas}
              casilla={grupo ? (
                <input
                  type="checkbox"
                  checked={!fuera[v.sku]}
                  onChange={() => onAlternar(v.sku)}
                  className="h-3.5 w-3.5 accent-indigo-600"
                />
              ) : null}
              excluida={grupo && !!fuera[v.sku]}
              resaltada={coincide(v.sku, resaltar)}
              bloqueada={grupo}
              onAbrir={() => onAbrirVariante(p, v)}
            />
          ))
        ) : (
          <Fila
            sku={p.sku}
            nombre="Producto simple, sin variantes"
            costo={p.costo}
            heredado={false}
            base={p.precio_base}
            oferta={p.precio_oferta}
            stock={p.stock}
            propios={canalesPadre}
            delPadre={[]}
            mapas={mapas}
            casilla={null}
            excluida={false}
            resaltada={coincide(p.sku, resaltar)}
            bloqueada={false}
            onAbrir={() => onAbrirPadre(p)}
          />
        )}
      </div>
    </div>
  );
}

/* ── El SKU como botón: es la puerta al Estudio en toda la pantalla ──────── */
function BotonSku({
  sku, onClick, titulo, bloqueada = false, chico = false,
}: {
  sku: string;
  onClick: () => void;
  titulo: string;
  bloqueada?: boolean;
  chico?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={titulo}
      className={`inline-flex shrink-0 items-center gap-1 rounded bg-indigo-50 font-mono text-indigo-600 transition-colors hover:bg-indigo-100 hover:text-indigo-800 hover:underline ${
        chico ? "px-1.5 text-[10px]" : "px-2 py-0.5 text-[11px]"
      }`}
    >
      {bloqueada && <Lock size={9} />}
      {sku}
    </button>
  );
}

/* ── Un renglón de la tabla del detalle (variante o producto simple) ─────── */
function Fila({
  sku, nombre, costo, heredado, base, oferta, stock, propios, delPadre, mapas,
  casilla, excluida, resaltada, bloqueada, onAbrir,
}: {
  sku: string;
  nombre: string | null;
  costo: number | null;
  heredado: boolean;
  base: number | null;
  oferta: number | null;
  stock: number | null;
  propios: CanalResumen[];
  delPadre: CanalResumen[];
  mapas: Mapas;
  casilla: ReactNode;
  excluida: boolean;
  resaltada: boolean;
  bloqueada: boolean;
  onAbrir: () => void;
}) {
  return (
    <div
      className={`grid ${COLUMNAS} items-center gap-2 border-b border-slate-50 px-5 py-2 text-xs ${
        excluida ? "opacity-40" : ""
      } ${resaltada ? "bg-amber-50/70" : ""}`}
    >
      <div>{casilla}</div>
      <div className="flex min-w-0 items-center gap-2">
        <BotonSku
          sku={sku}
          onClick={onAbrir}
          chico
          bloqueada={bloqueada}
          titulo={bloqueada ? "Abrir en solo lectura: se publica dentro del grupo" : "Abrir en el Estudio para editar y publicar"}
        />
        <span className="truncate text-slate-600">{nombre}</span>
      </div>
      <div
        className={`text-right tabular-nums ${heredado ? "text-slate-300" : "text-slate-500"}`}
        title={heredado ? "Sin costo propio: es el del padre" : undefined}
      >
        {pesos(costo)}
      </div>
      <div className="text-right tabular-nums text-slate-700">{pesos(base)}</div>
      <div className="text-right tabular-nums text-slate-700">{pesos(oferta)}</div>
      <div className="text-right tabular-nums text-slate-400">
        {stock ?? "—"} <span className="text-slate-300">u</span>
      </div>
      <Canales propios={propios} delPadre={delPadre} mapas={mapas} />
    </div>
  );
}

/* ── Dónde está publicado. Si la variante no tiene publicación con su propio
      SKU pero el padre sí, se muestran los del padre, atenuados: la
      publicación cuelga del padre y "—" diría que no se vende en ningún lado. */
function Canales({ propios, delPadre, mapas }: { propios: CanalResumen[]; delPadre: CanalResumen[]; mapas: Mapas }) {
  if (propios.some((c) => c.publicado)) {
    return <ChannelDots canales={propios} colorMap={mapas.color} labelMap={mapas.label} />;
  }
  const vivos = delPadre.filter((c) => c.publicado);
  if (vivos.length) {
    return (
      <span
        className="flex items-center gap-1.5 opacity-60"
        title="Sin publicación con su propio SKU; el padre sí está publicado en estos canales. Si esa publicación lleva variaciones, esta variante se vende ahí."
      >
        <ChannelDots canales={vivos} colorMap={mapas.color} labelMap={mapas.label} />
        <span className="text-[9px] font-semibold uppercase text-slate-400">padre</span>
      </span>
    );
  }
  return <span className="text-xs text-slate-300">—</span>;
}
