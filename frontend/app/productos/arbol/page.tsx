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
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight, Folder, Layers, Loader2, Package, Search, AlertTriangle, Wand2,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { listarCategorias, listarProductos, type CategoriaWC } from "@/lib/api";
import type { Producto, VarianteResumen } from "@/lib/types";

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

type Modo = "individual" | "grupo";

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

  useEffect(() => {
    listarCategorias()
      .then(setCats)
      .catch((e: Error) => setError(e.message || "No se pudieron leer las categorías."));
  }, []);

  // Solo las RAÍCES cuelgan del árbol; las hijas se agrupan bajo su padre. Woo
  // devuelve la jerarquía plana con `parent`, así que se arma aquí.
  const raices = useMemo(() => cats.filter((c) => !c.parent), [cats]);
  const hijasDe = useMemo(() => {
    const m: Record<number, CategoriaWC[]> = {};
    cats.forEach((c) => { if (c.parent) (m[c.parent] ||= []).push(c); });
    return m;
  }, [cats]);

  const abrirCategoria = useCallback((id: number) => {
    setAbiertas((a) => ({ ...a, [id]: !a[id] }));
    if (productos[id] || cargando[id]) return;
    setCargando((c) => ({ ...c, [id]: true }));
    // `aplanar: false` es TODO el truco: así el padre llega con sus variantes
    // dentro en vez de desperdigadas como filas hermanas.
    listarProductos({ canal: "general", categoria: id, perPage: POR_CATEGORIA, aplanar: false })
      .then((r) => {
        setProductos((p) => ({ ...p, [id]: r.items }));
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
  }, [productos, cargando]);

  const padre = useMemo(() => {
    if (!sel) return null;
    for (const lista of Object.values(productos)) {
      const p = lista.find((x) => x.sku === sel);
      if (p) return p;
    }
    return null;
  }, [sel, productos]);

  const modoDe = (p: Producto): Modo => modo[p.sku] ?? modoSugerido(p);
  const variantesDe = (p: Producto): VarianteResumen[] => p.variantes ?? [];

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
              El catálogo como árbol: categoría, producto padre y sus variantes. Abre una
              categoría para traer sus productos.
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
                  value={filtro}
                  onChange={(e) => setFiltro(e.target.value)}
                  placeholder="Filtrar categorías…"
                  className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
                />
              </div>
              <div className="mt-2 text-[11px] text-slate-400">
                {cats.length} categorías con productos
              </div>
            </div>

            <div className="flex-1 overflow-auto p-2">
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
                    abiertoPadre={abiertoPadre}
                    onPadre={(sku) => {
                      setAbiertoPadre((a) => ({ ...a, [sku]: !a[sku] }));
                      setSel(sku);
                    }}
                    tope={tope}
                    onMasVariantes={(sku, n) => setTope((t) => ({ ...t, [sku]: n }))}
                    modoDe={modoDe}
                    sel={sel}
                  />
                ))}
            </div>
          </section>

          {/* ── DETALLE ────────────────────────────────────────────────── */}
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
            {!padre ? (
              <div className="flex h-[760px] flex-col items-center justify-center gap-2 text-slate-300">
                <Package size={34} />
                <p className="text-sm">Elige un producto en el árbol</p>
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
                variantes={variantesDe(padre)}
              />
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

/* ── Una rama del árbol: categoría → (subcategorías) → padres → variantes ── */
function Rama({
  cat, hijas, abiertas, productos, cargando, onCategoria, truncadas,
  abiertoPadre, onPadre, tope, onMasVariantes, modoDe, sel, nivel = 0,
}: {
  cat: CategoriaWC;
  hijas: CategoriaWC[];
  abiertas: Record<number, boolean>;
  productos: Record<number, Producto[]>;
  cargando: Record<number, boolean>;
  onCategoria: (id: number) => void;
  truncadas: Record<number, number>;
  abiertoPadre: Record<string, boolean>;
  onPadre: (sku: string) => void;
  tope: Record<string, number>;
  onMasVariantes: (sku: string, n: number) => void;
  modoDe: (p: Producto) => Modo;
  sel: string | null;
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
              cargando={cargando} onCategoria={onCategoria} truncadas={truncadas} abiertoPadre={abiertoPadre}
              onPadre={onPadre} tope={tope} onMasVariantes={onMasVariantes}
              modoDe={modoDe} sel={sel} nivel={nivel + 1}
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

          {lista.map((p) => {
            const vars = p.variantes ?? [];
            const esPadre = vars.length > 0;
            const abierto = !!abiertoPadre[p.sku];
            const n = tope[p.sku] ?? TOPE_VARIANTES;
            const grupo = modoDe(p) === "grupo";
            return (
              <div key={p.sku}>
                <button
                  onClick={() => onPadre(p.sku)}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-slate-50 ${
                    sel === p.sku ? "bg-indigo-50 font-semibold text-indigo-700" : "text-slate-700"
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
                  {esPadre && <span className="shrink-0 text-[10px] text-slate-300">{vars.length}</span>}
                </button>

                {abierto && esPadre && (
                  <div className="ml-[11px] border-l border-slate-100 pl-3">
                    {vars.slice(0, n).map((v) => (
                      <div key={v.sku} className="flex items-center gap-2 px-2 py-1 text-[11px] text-slate-500">
                        <span className="font-mono text-indigo-500">{v.sku}</span>
                        <span className="truncate">{v.nombre}</span>
                      </div>
                    ))}
                    {vars.length > n && (
                      <button
                        onClick={() => onMasVariantes(p.sku, n + 25)}
                        className="px-2 py-1 text-[11px] font-semibold text-indigo-600 hover:underline"
                      >
                        +{vars.length - n} variantes
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── El panel derecho: el padre elegido y su agrupación ──────────────── */
function Detalle({
  p, modo, fuera, onModo, onAlternar, variantes,
}: {
  p: Producto;
  modo: Modo;
  fuera: Record<string, true>;
  onModo: (m: Modo) => void;
  onAlternar: (sku: string) => void;
  variantes: VarianteResumen[];
}) {
  const grupo = modo === "grupo";
  const dentro = variantes.filter((v) => !fuera[v.sku]).length;

  return (
    <div className="flex h-[760px] flex-col">
      <div className="border-b border-slate-100 p-5">
        <div className="text-[11px] text-slate-400">
          {(p.categoria_path ?? []).map((c) => c.nombre).join(" › ") || "Sin categoría"}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-bold text-slate-900">{p.nombre}</h2>
          <span className="rounded bg-slate-100 px-1.5 font-mono text-[10px] text-slate-400">{p.sku}</span>
        </div>
        <div className="mt-1 text-xs text-slate-500">
          {variantes.length} variantes · {pesos(p.precio)}
        </div>
      </div>

      {/* Agrupación */}
      <div className="border-b border-slate-100 bg-slate-50/60 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">Cómo se publica</div>
            <p className="mt-1 max-w-md text-xs text-slate-500">
              {grupo
                ? "Una publicación por canal, con las variantes marcadas dentro. El comprador elige en la publicación."
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

      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-2.5 text-[11px] text-slate-400">
        <Layers size={13} /> Variantes
      </div>

      <div className="flex-1 overflow-auto">
        {variantes.map((v) => {
          const excluida = grupo && fuera[v.sku];
          return (
            <div
              key={v.sku}
              className={`grid grid-cols-[28px_1fr_110px_92px_80px] items-center gap-2 border-b border-slate-50 px-5 py-2 text-xs ${
                excluida ? "opacity-40" : ""
              }`}
            >
              <div>
                {grupo && (
                  <input
                    type="checkbox"
                    checked={!fuera[v.sku]}
                    onChange={() => onAlternar(v.sku)}
                    className="h-3.5 w-3.5 accent-indigo-600"
                  />
                )}
              </div>
              <div className="flex min-w-0 items-center gap-2">
                <span className="shrink-0 rounded bg-indigo-50 px-1.5 font-mono text-[10px] text-indigo-500">{v.sku}</span>
                <span className="truncate text-slate-600">{v.nombre}</span>
              </div>
              <div className="text-right tabular-nums text-slate-700">{pesos(v.precio)}</div>
              <div className="text-right tabular-nums text-slate-400">{pesos(v.costo)}</div>
              <div className="text-right tabular-nums text-slate-400">
                {v.stock ?? "—"} <span className="text-slate-300">u</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
