"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  Loader2,
  AlertTriangle,
  ExternalLink,
  Eye,
  Info,
  Check,
  X,
  Search,
  Target,
  Crown,
  ChevronDown,
  ChevronRight,
  Store,
  Flame,
  Sparkles,
  TrendingUp,
  Ban,
  Pause,
  RefreshCw,
  BookOpen,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import ComoLeerCompetenciaModal from "@/components/ComoLeerCompetenciaModal";
import {
  detalleSkuCompetencia,
  estadoCompetencia,
  skusSubcategoria,
  sugerirSku,
  sugerirSubcategoria,
  terminosSubcategoria,
  vistaCompetencia,
  capturarRankingsCompetencia,
  mensajeDeError,
} from "@/lib/api";
import type {
  CompetenciaDetalleSku,
  CompetenciaEstado,
  CompetenciaNicho,
  CompetenciaRaiz,
  CompetenciaResultado,
  CompetenciaSkusSub,
  CompetenciaSkuVista,
  CompetenciaSubcategoria,
  CompetenciaSugerenciaSku,
  CompetenciaSugerenciaSub,
  CompetenciaTerminosSub,
  RankingCategoria,
} from "@/lib/types";

const mxn = (v: number | null | undefined) =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("es-MX", {
        style: "currency",
        currency: "MXN",
        maximumFractionDigits: 0,
      }).format(v);

const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : new Intl.NumberFormat("es-MX").format(v);

/**
 * Los "+50mil vendidos" de ML son una COTA INFERIOR: la plataforma redondea, así
 * que 84,000 no es una cifra exacta sino "al menos 84 mil". Se marca con el `+`
 * para que nadie la sume como si fuera precisa.
 */
const cota = (v: number | null | undefined) => (v ? `+${num(v)}` : "—");

/**
 * URL de una fila del ranking de la competencia.
 *
 * El SEGMENTO depende del tipo de entrada de /highlights, y las tres formas están
 * verificadas contra los permalinks que ML mismo generó al raspar:
 *   USER_PRODUCT → /up/{MLMU…}      PRODUCT → /p/{MLM…}
 *   ITEM         → articulo…/MLM-<dígitos>-_JM
 *
 * Se construye porque `url` viene vacía cuando la captura fue SOLO por API (sin
 * navegador no hay raspado y por tanto no hay permalink), y entonces la tarjeta
 * enlazaba a "#" y no llevaba a ninguna parte.
 */
const urlRanking = (r: Partial<RankingCategoria>): string | null => {
  if (r.url) return r.url;
  const idp = r.id_pagina || r.externo_id;
  if (!idp) return null;
  if (r.tipo === "USER_PRODUCT" || idp.startsWith("MLMU"))
    return `https://www.mercadolibre.com.mx/up/${idp}`;
  if (r.tipo === "PRODUCT") return `https://www.mercadolibre.com.mx/p/${idp}`;
  if (/^MLM\d{9,12}$/.test(idp))
    return `https://articulo.mercadolibre.com.mx/MLM-${idp.slice(3)}-_JM`;
  return null;
};

/**
 * URL de una publicación nuestra.
 *
 * Se CONSTRUYE del item id y la url guardada queda como respaldo. Dos razones:
 * `channel.listings` no tiene fila para todas las publicaciones (le faltan las de
 * MUE-0163-TEL, el SKU con la mayor parte del tráfico) y cuando la tiene viene
 * truncada, sin el sufijo `-_JM`. La forma con `-_JM` es la que ML mismo genera y
 * redirige al permalink con slug. Sin esto la UI pintaba un enlace a "#".
 */
const urlPub = (id: string | null | undefined, url: string | null | undefined) => {
  if (id && /^MLM\d{9,12}$/.test(id))
    return `https://articulo.mercadolibre.com.mx/MLM-${id.slice(3)}-_JM`;
  return url || null;
};

/** La brecha de precio: es la columna que manda, y su color es el diagnóstico. */
function Brecha({ x }: { x: number | null }) {
  if (x === null || x === undefined)
    return (
      <span className="text-xs text-slate-400" title="Sin ranking de la subcategoría: no hay mercado con qué comparar">
        —
      </span>
    );
  const tono =
    x >= 3
      ? "bg-rose-100 text-rose-800"
      : x >= 1.5
        ? "bg-amber-100 text-amber-800"
        : "bg-emerald-100 text-emerald-800";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-bold tabular-nums ${tono}`}>
      {x.toFixed(1)}×
    </span>
  );
}

/** La posición como medalla: es lo primero que el ojo busca. */
function Medalla({ pos }: { pos: number | null }) {
  if (!pos) return <span className="text-xs text-slate-300">—</span>;
  const tono =
    pos <= 3
      ? "bg-emerald-100 text-emerald-800"
      : pos <= 10
        ? "bg-amber-100 text-amber-800"
        : "bg-slate-100 text-slate-600";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-bold tabular-nums ${tono}`}>
      #{pos}
    </span>
  );
}

/** Foto del producto. Sin `next/image` a propósito: son dominios de ML y de Woo. */
function Foto({ src, alt, size = 40 }: { src: string | null; alt: string; size?: number }) {
  if (!src)
    return (
      <div
        className="shrink-0 rounded bg-slate-100 ring-1 ring-slate-200"
        style={{ width: size, height: size }}
      />
    );
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className="shrink-0 rounded object-cover ring-1 ring-slate-200"
      style={{ width: size, height: size }}
      loading="lazy"
    />
  );
}

/** Una tarjeta del top de más vendidos de la categoría padre. */
function Tarjeta({ r }: { r: RankingCategoria }) {
  return (
    <a
      href={urlRanking(r) ?? "#"}
      target="_blank"
      rel="noreferrer"
      className="group flex w-[168px] shrink-0 flex-col gap-1.5 rounded-lg bg-white p-2 ring-1 ring-slate-200 transition hover:ring-indigo-300"
    >
      <div className="flex items-start justify-between gap-1">
        <Medalla pos={r.posicion} />
        {r.es_nuestro ? (
          <span className="rounded bg-indigo-600 px-1 py-0.5 text-[9px] font-bold text-white">
            NUESTRO
          </span>
        ) : null}
      </div>
      <Foto src={r.imagen} alt={r.titulo ?? r.externo_id} size={148} />
      <div className="line-clamp-2 text-[11px] leading-tight text-slate-700 group-hover:text-indigo-700">
        {r.titulo ?? r.externo_id}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-sm font-bold text-slate-900">{mxn(r.precio)}</span>
        {r.precio_lista && r.precio && r.precio_lista > r.precio ? (
          <span className="text-[10px] text-slate-400 line-through">{mxn(r.precio_lista)}</span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-slate-500">
        <span title="Visitas en 30 días (API de Mercado Libre)">
          <Eye size={9} className="mr-0.5 inline" />
          {num(r.visitas_30d)}
        </span>
        <span title="Unidades vendidas: ML redondea, es una cota inferior">
          {cota(r.vendidos)} vend.
        </span>
        {r.rating ? <span>★ {r.rating}</span> : null}
      </div>
      {r.item_categoria_nombre ? (
        <div
          className="truncate rounded bg-slate-50 px-1 py-0.5 text-[9px] text-slate-500"
          title={`Subcategoría: ${r.item_categoria_nombre}`}
        >
          {r.item_categoria_nombre}
        </div>
      ) : null}
    </a>
  );
}

/**
 * Selector con buscador. Reemplaza al `<select>` de subcategorías porque en
 * producción son ~988: una lista sin filtro es inservible a esa escala.
 */
function SelectorBuscable({
  valor,
  opciones,
  placeholder,
  vacio,
  onCambio,
}: {
  valor: string;
  opciones: { id: string; label: string; n?: number }[];
  placeholder: string;
  vacio: string;
  onCambio: (id: string) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [q, setQ] = useState("");
  const caja = useRef<HTMLDivElement>(null);

  // Cerrar al hacer clic fuera: sin esto el panel se queda abierto tapando la tabla.
  useEffect(() => {
    if (!abierto) return;
    const fuera = (e: MouseEvent) => {
      if (caja.current && !caja.current.contains(e.target as Node)) setAbierto(false);
    };
    document.addEventListener("mousedown", fuera);
    return () => document.removeEventListener("mousedown", fuera);
  }, [abierto]);

  const elegida = opciones.find((o) => o.id === valor);
  const filtradas = q.trim()
    ? opciones.filter((o) => o.label.toLowerCase().includes(q.trim().toLowerCase()))
    : opciones;

  return (
    <div ref={caja} className="relative">
      <button
        onClick={() => {
          setAbierto((v) => !v);
          setQ("");
        }}
        className="flex min-w-[240px] items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-left text-sm text-slate-700 hover:bg-slate-50"
      >
        <span className="min-w-0 flex-1 truncate">
          {elegida ? `${elegida.label}${elegida.n ? ` (${elegida.n})` : ""}` : vacio}
        </span>
        <ChevronDown size={13} className="shrink-0 text-slate-400" />
      </button>

      {abierto ? (
        <div className="absolute z-20 mt-1 w-[320px] overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-slate-200">
          <div className="relative border-b border-slate-100 p-2">
            <Search
              size={13}
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={placeholder}
              autoFocus
              className="w-full rounded border border-slate-200 py-1 pl-7 pr-2 text-sm"
            />
          </div>
          <div className="max-h-[280px] overflow-y-auto py-1">
            <button
              onClick={() => {
                onCambio("");
                setAbierto(false);
              }}
              className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-50 ${
                valor === "" ? "font-medium text-indigo-700" : "text-slate-600"
              }`}
            >
              {valor === "" ? <Check size={12} /> : <span className="w-3" />}
              {vacio}
            </button>
            {filtradas.length === 0 ? (
              <div className="px-3 py-3 text-center text-xs text-slate-400">
                Nada coincide con «{q}».
              </div>
            ) : (
              filtradas.map((o) => (
                <button
                  key={o.id}
                  onClick={() => {
                    onCambio(o.id);
                    setAbierto(false);
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-50 ${
                    valor === o.id ? "font-medium text-indigo-700" : "text-slate-600"
                  }`}
                >
                  {valor === o.id ? <Check size={12} /> : <span className="w-3" />}
                  <span className="min-w-0 flex-1 truncate">{o.label}</span>
                  {o.n ? (
                    <span className="shrink-0 text-[10px] tabular-nums text-slate-400">
                      {o.n}
                    </span>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Cuántas columnas tiene la tabla de SKUs. Lo usa el colSpan del detalle. */
const COLS = 13;

type Orden =
  | "visitas_desc"
  | "visitas_asc"
  | "unidades_desc"
  | "unidades_asc";

/** Cómo ordenar las SUBCATEGORÍAS. Distinto del orden de los SKUs: aquí se busca
 *  dónde hay ticket alto y demanda, no qué producto nuestro rinde. */
type OrdenSub =
  | "defecto"
  | "mediana_desc"
  | "mediana_asc"
  | "visitas_desc"
  | "visitas_asc"
  | "volumen_desc"
  | "skus_desc";

const ORDENES_SUB: { id: OrdenSub; label: string }[] = [
  { id: "defecto", label: "Orden por oportunidad" },
  { id: "mediana_desc", label: "Ticket más alto" },
  { id: "mediana_asc", label: "Ticket más bajo" },
  { id: "visitas_desc", label: "Más visitas del nicho" },
  { id: "visitas_asc", label: "Menos visitas del nicho" },
  { id: "volumen_desc", label: "Más unidades vendidas" },
  { id: "skus_desc", label: "Donde más SKUs tenemos" },
];

/**
 * Ordena las subcategorías. Los `null` van SIEMPRE al final: una categoría sin
 * mediana es una que no hemos capturado, y colocarla como "la más barata" haría
 * que el orden mintiera.
 */
function ordenarSubs(
  subs: CompetenciaSubcategoria[],
  orden: OrdenSub,
): CompetenciaSubcategoria[] {
  if (orden === "defecto") return subs;
  const campo: Record<string, keyof CompetenciaSubcategoria> = {
    mediana_desc: "mediana",
    mediana_asc: "mediana",
    visitas_desc: "visitas_mercado",
    visitas_asc: "visitas_mercado",
    volumen_desc: "volumen_mercado",
    skus_desc: "n_skus",
  };
  const k = campo[orden];
  const asc = orden.endsWith("_asc");
  return [...subs].sort((a, b) => {
    const x = a[k] as number | null;
    const y = b[k] as number | null;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return asc ? x - y : y - x;
  });
}

// El orden de NUESTROS SKUs dentro de cada subcategoría es fijo: más visitas
// primero. El selector se retiró — quien compara contra el mercado quiere ver
// arriba lo que más tráfico tiene, y las otras tres opciones no se usaban. El
// orden de las SUBCATEGORÍAS sí se elige, y ése es el que decide dónde mirar.
const ORDEN_SKUS: Orden = "visitas_desc";

/**
 * Ordena nuestros SKUs por visitas o unidades.
 *
 * Los `null` (sin medir) van SIEMPRE al final, en las dos direcciones. Tratarlos
 * como 0 los pondría arriba en el orden ascendente y se leería como "estos son
 * los que nadie ve", cuando la verdad es que nadie los midió.
 */
function ordenar<T extends { visitas_30d: number | null; unidades_30d: number | null }>(
  filas: T[],
  orden: Orden,
): T[] {
  const campo = orden.startsWith("visitas") ? "visitas_30d" : "unidades_30d";
  const asc = orden.endsWith("_asc");
  return [...filas].sort((a, b) => {
    const x = a[campo as keyof T] as number | null;
    const y = b[campo as keyof T] as number | null;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return asc ? x - y : y - x;
  });
}

/**
 * Un SKU nuestro = VARIAS filas, una por publicación.
 *
 * El desglose por tienda va en la tabla y no escondido en el detalle porque es
 * donde está la decisión: cada tienda tiene su propio TÍTULO, su precio, sus
 * visitas y sus ventas, y con títulos distintos una vende y la otra no. Las
 * celdas del SKU (foto, producto, subcategoría, brecha) llevan `rowSpan`.
 */
function FilasSku({
  s,
  abierto,
  onToggle,
}: {
  s: CompetenciaSkuVista;
  abierto: boolean;
  onToggle: () => void;
}) {
  const tiendas = s.tiendas.length ? s.tiendas : [null];
  const span = tiendas.length;
  const fondo = abierto ? "bg-indigo-50/40" : "";

  return (
    <>
      {tiendas.map((t, i) => (
        <tr
          key={t ? `${t.cuenta}-${t.ml_item_id}` : `${s.sku}-vacio`}
          onClick={onToggle}
          className={`cursor-pointer transition hover:bg-slate-50 ${fondo} ${
            i === 0 ? "border-t border-slate-200" : "border-t border-slate-100"
          }`}
        >
          {i === 0 ? (
            <>
              <td rowSpan={span} className="py-2 pl-2 pr-1 align-top">
                {abierto ? (
                  <ChevronDown size={14} className="text-slate-400" />
                ) : (
                  <ChevronRight size={14} className="text-slate-300" />
                )}
              </td>
              <td rowSpan={span} className="py-2 pr-2 align-top">
                <Foto src={s.imagen} alt={s.sku} />
              </td>
              <td rowSpan={span} className="py-2 pr-3 align-top">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-xs font-medium text-slate-900">{s.sku}</span>
                  {/* Corona: estamos entre los más vendidos de nuestro nicho. Es el
                      único logro que la tabla puede celebrar, así que se ve primero. */}
                  {s.en_top ? (
                    <span
                      className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1 py-0.5 text-[10px] font-bold text-amber-800"
                      title={`Estamos en el top de la subcategoría, en la posición #${s.posicion_top}`}
                    >
                      <Crown size={9} />#{s.posicion_top}
                    </span>
                  ) : null}
                </div>
                <div className="max-w-[220px] text-[11px] leading-snug text-slate-500">
                  {s.nombre}
                </div>
                {s.pausada_es_la_que_vende ? (
                  <span
                    className="mt-1 inline-flex items-center gap-1 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-medium text-rose-800"
                    title="La publicación PAUSADA tiene más visitas que la activa: está pausada la que vende"
                  >
                    <Pause size={9} /> pausada vende
                  </span>
                ) : null}
              </td>
              <td rowSpan={span} className="py-2 pr-3 align-top text-xs text-slate-600">
                {s.categoria_nombre}
                {s.pos_en_raiz ? (
                  <span
                    className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold text-amber-800"
                    title={`Su subcategoría es la #${s.pos_en_raiz} más vendida de toda la categoría`}
                  >
                    raíz #{s.pos_en_raiz}
                  </span>
                ) : null}
                {s.sin_datos_ml ? (
                  <span
                    className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-slate-100 px-1 py-0.5 text-[9px] text-slate-500"
                    title="ML no publica ni ranking ni términos de esta categoría. No es un fallo de la captura."
                  >
                    <Ban size={8} /> sin datos ML
                  </span>
                ) : null}
              </td>
              <td
                rowSpan={span}
                className="py-2 pr-3 align-top text-right text-xs tabular-nums text-slate-500"
              >
                {mxn(s.mediana_mercado)}
              </td>
              <td rowSpan={span} className="py-2 pr-3 align-top text-right">
                <Brecha x={s.brecha} />
              </td>
            </>
          ) : null}

          {/* ── Por publicación ── */}
          <td className="py-2 pr-2 text-[11px] font-medium text-slate-700">
            {t?.cuenta ?? "—"}
          </td>
          <td className="py-2 pr-3">
            {/* El título es POR TIENDA y suele diferir: es lo que explica por qué
                una publicación recibe tráfico y la otra no. */}
            <div className="max-w-[260px] text-[11px] leading-snug text-slate-600">
              {t?.titulo ?? "—"}
            </div>
          </td>
          <td className="py-2 pr-3">
            {t?.ml_item_id ? (
              <a
                href={urlPub(t.ml_item_id, t.url) ?? "#"}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-0.5 font-mono text-[11px] text-indigo-600 hover:underline"
              >
                {t.ml_item_id}
                <ExternalLink size={9} />
              </a>
            ) : (
              <span className="text-[11px] text-slate-400">sin publicar</span>
            )}
            {t?.estado ? (
              <span
                className={`ml-1.5 rounded px-1 py-0.5 text-[9px] ${
                  t.estado === "active"
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-amber-100 text-amber-700"
                }`}
              >
                {t.estado}
              </span>
            ) : null}
          </td>
          <td className="py-2 pr-3 text-right text-xs tabular-nums">
            {/* El precio que se PAGA. El de lista va tachado cuando hay descuento:
                mostrar el de lista solo falseaba la brecha contra el mercado. */}
            <div className="font-medium text-slate-900">{mxn(t?.precio)}</div>
            {t?.precio_lista && t?.precio && t.precio_lista > t.precio ? (
              <div className="text-[10px] text-slate-400 line-through">
                {mxn(t.precio_lista)}
              </div>
            ) : null}
            {t?.precio != null ? (
              <PrecioConfirmado iso={t.precio_confirmado_en} />
            ) : null}
          </td>
          <td className="py-2 pr-3 text-right text-xs tabular-nums text-slate-600">
            {num(t?.visitas_30d)}
          </td>
          <td className="py-2 pr-3 text-right text-xs tabular-nums text-slate-600">
            {num(t?.unidades_30d)}
          </td>
          <td className="py-2 pr-2 text-right text-xs tabular-nums text-slate-500">
            {/* Con 0 visitas la conversión es INDEFINIDA, no 0%: mostrarla como
                0% se lee como "convierte mal" cuando en realidad nadie la vio. */}
            {t?.conversion_30d === null || t?.conversion_30d === undefined
              ? "—"
              : `${t.conversion_30d}%`}
          </td>
        </tr>
      ))}
    </>
  );
}
/** Los resultados de una búsqueda: posición ORGÁNICA, ficha y enlace. */
function ResultadosBusqueda({ filas }: { filas: CompetenciaResultado[] }) {
  return (
    <div className="space-y-1">
      {filas.map((r) => (
        <a
          key={r.externo_id}
          href={urlRanking({ url: r.url, externo_id: r.externo_id }) ?? "#"}
          target="_blank"
          rel="noreferrer"
          className={`flex items-center gap-2 rounded p-1 hover:bg-slate-50 ${
            r.es_nuestro ? "bg-amber-50" : ""
          }`}
        >
          <Medalla pos={r.posicion} />
          <Foto src={r.imagen} alt={r.titulo ?? ""} size={30} />
          <span className="min-w-0 flex-1 truncate text-[11px] text-slate-600">
            {r.titulo ?? r.externo_id}
          </span>
          {r.es_nuestro ? <Crown size={11} className="shrink-0 text-amber-600" /> : null}
          {/* Las VISITAS de cada resultado: sin ellas la lista dice a qué precio
              compite cada uno pero no cuánto tráfico se lleva, que es la mitad
              de la decisión. Se piden por API al capturar (gratis). */}
          <span
            className="w-16 shrink-0 text-right text-[11px] tabular-nums text-slate-500"
            title="Visitas de 30 días de esa publicación"
          >
            {r.visitas_30d != null ? `${num(r.visitas_30d)} vis` : "—"}
          </span>
          <span className="w-16 text-right text-xs font-medium tabular-nums text-slate-900">
            {mxn(r.precio)}
          </span>
        </a>
      ))}
    </div>
  );
}

/**
 * El detalle de un SKU: quién lidera su nicho y con qué palabras.
 *
 * Solo dos bloques. "Nuestras publicaciones" se fue porque ya está en la fila de
 * arriba —la misma barra por tienda, con título, precio, visitas y ventas— y
 * repetirla no agregaba nada. El generador de título también: lo que se pidió aquí
 * son los términos y las palabras clave del nicho.
 *
 * Tampoco se muestra el largo en caracteres de los títulos ajenos: era ruido.
 */
function DetalleSku({
  d,
  cargando,
}: {
  d: CompetenciaDetalleSku | null;
  cargando: boolean;
}) {
  const [sug, setSug] = useState<CompetenciaSugerenciaSub | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const [fallo, setFallo] = useState<string | null>(null);

  // Al cambiar de SKU la sugerencia anterior deja de aplicar.
  useEffect(() => {
    setSug(null);
    setFallo(null);
  }, [d?.sku]);

  if (cargando)
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-sm text-slate-500">
        <Loader2 size={15} className="animate-spin" /> Cargando…
      </div>
    );
  if (!d) return null;

  // Términos más buscados que se pintan: 10 en tres columnas. ML da hasta 50 por
  // categoría (medido: 37 categorías topan en 50, promedio 39.8), así que esto es
  // una muestra de la cabeza, no la lista completa — el resto queda guardado.
  const TOPE = 10;
  // Las palabras clave sugeridas siguen en 5: ahí más es ruido.
  const TOPE_PALABRAS = 5;

  return (
    <div className="space-y-3 border-y border-indigo-100 bg-indigo-50/30 px-4 py-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-700">
          <Target size={13} /> Competencia directa
        </span>
        <span className="text-[11px] text-slate-500">{d.categoria_nombre}</span>
        {d.ruta ? <span className="text-[10px] text-slate-400">{d.ruta}</span> : null}
      </div>

      {d.aviso ? (
        <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
          <Ban size={14} className="mt-0.5 shrink-0 text-slate-400" />
          {d.aviso}
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ── La búsqueda general: lo que el detalle debe responder ──
             Se guarda POR TÉRMINO y no por SKU: varios SKUs comparten término
             general, así que se mide y se paga una sola vez y los demás lo
             reusan gratis. La búsqueda por título completo se retiró — era el
             doble de consultas para un dato que se solapa con esta. */}
        <div className="rounded-lg bg-white p-3 ring-1 ring-slate-200">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              <Search size={11} /> Búsqueda general
            </span>
            {d.termino_general ? (
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700">
                {d.termino_general}
              </span>
            ) : null}
            <span className="text-[10px] text-slate-400">
              {d.termino_origen === "manual" ? "corregido a mano" : "propuesto por IA"}
            </span>
          </div>
          {d.busqueda_general.length === 0 ? (
            /* El mensaje viejo explicaba por qué la API no sirve
               (`/sites/MLM/search` → 403). Cierto, pero inútil: no dice qué
               hacer. La búsqueda se mide con Apify y cuesta ~$0.007 por término,
               así que lo accionable es que ESE término todavía no se ha corrido. */
            <div className="py-3 text-center text-xs text-slate-400">
              {d.termino_general ? (
                <>
                  Este término todavía no se ha medido. La posición orgánica se
                  captura con el buscador de Apify (~$0.007 por término) y se
                  guarda una sola vez: los demás SKUs con el mismo término la
                  reusan sin costo.
                </>
              ) : (
                <>
                  Este SKU no tiene término general, así que no hay nada que
                  buscar. Asígnale uno para poder medir su posición orgánica.
                </>
              )}
            </div>
          ) : (
            <ResultadosBusqueda filas={d.busqueda_general} />
          )}
        </div>

        {/* ── Los 5 términos más buscados y las palabras a usar ── */}
        <div className="rounded-lg bg-white p-3 ring-1 ring-slate-200">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              Top {TOPE} términos más buscados de {d.categoria_nombre}
            </span>
            {d.terminos_total ? (
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                  d.terminos_cubiertos === 0
                    ? "bg-rose-100 text-rose-800"
                    : "bg-amber-100 text-amber-800"
                }`}
                title="De todos los términos que ML publica, cuántos cubre nuestro título"
              >
                {d.terminos_cubiertos} de {d.terminos_total} cubiertos
              </span>
            ) : null}
          </div>

          {d.terminos.length === 0 ? (
            <div className="py-3 text-center text-xs text-slate-400">
              Mercado Libre no publica términos de búsqueda de esta categoría.
            </div>
          ) : (
            /* Tres columnas: 15 términos en una sola lista obligaban a hacer
               scroll y el bloque perdía de vista el resto del detalle. */
            <ol className="grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2 lg:grid-cols-3">
              {d.terminos.slice(0, TOPE).map((t) => (
                <li key={t.termino} className="flex min-w-0 items-center gap-1.5 text-xs">
                  <span className="w-4 shrink-0 text-right tabular-nums text-slate-300">
                    {t.posicion}
                  </span>
                  {t.cubierto ? (
                    <Check size={12} className="shrink-0 text-emerald-600" />
                  ) : (
                    <X size={12} className="shrink-0 text-rose-400" />
                  )}
                  <span
                    title={t.termino}
                    className={`truncate ${t.cubierto ? "text-slate-700" : "text-slate-500"}`}
                  >
                    {t.termino}
                  </span>
                  {/* Los títulos difieren por tienda: una puede ser encontrable y
                      la otra no con el mismo SKU. */}
                  {t.cubierto_por?.length &&
                  t.cubierto_por.length < d.publicaciones.length ? (
                    <span className="rounded bg-emerald-50 px-1 text-[9px] font-medium text-emerald-700">
                      solo {t.cubierto_por.join(" · ")}
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          )}

          <div className="mt-1.5 text-[10px] leading-snug text-slate-400">
            Mercado Libre no publica el volumen: el número es el ORDEN en que los
            busca la gente, del más buscado al menos.
          </div>

          <div className="mt-2 border-t border-slate-100 pt-2">
            <button
              onClick={async () => {
                if (!d.categoria_id) return;
                setPidiendo(true);
                setFallo(null);
                try {
                  setSug(await sugerirSubcategoria(d.categoria_id));
                } catch (e) {
                  setFallo(mensajeDeError(e, "La IA no pudo sugerir."));
                } finally {
                  setPidiendo(false);
                }
              }}
              disabled={pidiendo || !d.categoria_id}
              className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2 py-1 text-[11px] font-medium text-indigo-700 ring-1 ring-indigo-200 hover:bg-indigo-50 disabled:opacity-50"
            >
              {pidiendo ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <Sparkles size={11} />
              )}
              {sug ? "Otra sugerencia" : "Sugerir palabras clave"}
            </button>

            {fallo ? (
              <div className="mt-1.5 text-[11px] text-rose-700">{fallo}</div>
            ) : null}

            {sug ? (
              <div className="mt-2 space-y-1">
                {sug.palabras.slice(0, TOPE_PALABRAS).map((p) => (
                  <div key={p.palabra} className="flex items-start gap-2 text-[11px]">
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 font-semibold ${
                        p.respaldada
                          ? "bg-indigo-50 text-indigo-800"
                          : "bg-amber-100 text-amber-900"
                      }`}
                      title={
                        p.respaldada
                          ? "Sale de los términos buscados o de los títulos líderes"
                          : "No aparece en la demanda medida ni en los títulos líderes: la IA la propuso sola"
                      }
                    >
                      {p.palabra}
                      {p.respaldada ? "" : " ⚠"}
                    </span>
                    <span className="text-slate-600">{p.porque}</span>
                  </div>
                ))}
                {sug.evitar_descartados.length > 0 ? (
                  <div className="flex items-start gap-1.5 pt-1 text-[10px] text-amber-800">
                    <AlertTriangle size={10} className="mt-0.5 shrink-0" />
                    La IA sugirió evitar {sug.evitar_descartados.join(", ")}, pero son
                    términos que la gente SÍ busca. Se descartaron.
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}



/**
 * Nuestros SKUs de una categoría con su barra completa por tienda.
 *
 * Distingue dos cosas que se ven parecido y no lo son: un SKU MEDIDO trae visitas,
 * ventas y conversión; uno solo publicado trae su MLM y su link pero las métricas
 * en "—" porque nadie lo está midiendo. Pintarle 0 diría "no lo ven".
 */
function SkusDeCategoria({ categoriaId }: { categoriaId: string }) {
  const [datos, setDatos] = useState<CompetenciaSkusSub | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    skusSubcategoria(categoriaId, ac.signal).then(setDatos).catch(() => {});
    return () => ac.abort();
  }, [categoriaId]);

  if (!datos)
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-[11px] text-slate-400">
        <Loader2 size={12} className="animate-spin" /> Cargando SKUs…
      </div>
    );
  // Un FALLO no es un "no tenemos". El endpoint devuelve {skus: [], error} cuando
  // no pudo leer, y esto pintaba las dos cosas igual: durante meses dijo "No
  // tenemos SKUs en esta categoría" mientras la insignia de arriba decía "46 SKUs
  // en catálogo" y el backend reventaba con UnboundLocalError. Un hueco se dice;
  // no se disfraza de dato.
  if (datos.error)
    return (
      <div className="px-3 py-3 text-[11px] text-rose-700">
        No se pudieron leer nuestros SKUs de esta categoría: {datos.error}
      </div>
    );
  if (datos.skus.length === 0)
    return (
      <div className="px-3 py-3 text-[11px] text-slate-400">
        No tenemos SKUs en esta categoría.
      </div>
    );

  return (
    <div className="px-3 py-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-semibold uppercase tracking-wide text-slate-500">
          Nuestros SKUs
        </span>
        <span className="text-slate-400">
          {datos.n_publicados} publicados · {datos.n_vigilados} medidos
          {datos.n_sin_publicar
            ? ` · ${datos.n_sin_publicar} sin publicar, fuera de la lista`
            : ""}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-slate-400">
              <th className="w-12" />
              <th className="pb-1 text-left font-medium">Producto</th>
              <th className="pb-1 text-left font-medium">Tienda</th>
              <th className="pb-1 text-left font-medium">Título de la tienda</th>
              <th className="pb-1 text-left font-medium">Publicación</th>
              <th className="pb-1 text-right font-medium">Precio</th>
              <th className="pb-1 text-right font-medium">Visitas 30d</th>
              <th className="pb-1 text-right font-medium">Ventas 30d</th>
              <th className="pb-1 text-right font-medium">Conversión</th>
            </tr>
          </thead>
          <tbody>
            {datos.skus.map((s) => {
              const tiendas = s.tiendas.length ? s.tiendas : [null];
              return (
                <Fragment key={s.sku}>
                  {tiendas.map((t, i) => (
                    <tr
                      key={t ? `${t.cuenta}-${t.ml_item_id}` : `${s.sku}-vacio`}
                      className={i === 0 ? "border-t border-slate-200" : "border-t border-slate-100"}
                    >
                      {i === 0 ? (
                        <>
                          <td rowSpan={tiendas.length} className="py-2 pr-2 align-top">
                            <Foto src={s.imagen} alt={s.sku} size={34} />
                          </td>
                          <td rowSpan={tiendas.length} className="py-2 pr-3 align-top">
                            <div className="flex items-center gap-1.5">
                              <span className="font-mono text-xs font-medium text-slate-900">
                                {s.sku}
                              </span>
                              {s.vigilado ? (
                                <span
                                  className="rounded bg-indigo-100 px-1 py-0.5 text-[9px] font-bold text-indigo-700"
                                  title="Bajo observación: sus visitas y ventas se miden"
                                >
                                  medido
                                </span>
                              ) : s.publicado ? (
                                <span
                                  className="rounded bg-amber-100 px-1 py-0.5 text-[9px] text-amber-800"
                                  title="Está publicado pero nadie mide sus visitas ni sus ventas"
                                >
                                  sin medir
                                </span>
                              ) : (
                                <span
                                  className="rounded bg-slate-100 px-1 py-0.5 text-[9px] text-slate-500"
                                  title="No hay publicación de este SKU en Mercado Libre"
                                >
                                  sin publicar
                                </span>
                              )}
                            </div>
                            <div className="max-w-[240px] text-[11px] leading-snug text-slate-500">
                              {s.nombre ?? "—"}
                            </div>
                          </td>
                        </>
                      ) : null}
                      <td className="py-2 pr-2 text-[11px] font-medium text-slate-700">
                        {t?.cuenta ?? "—"}
                      </td>
                      <td className="py-2 pr-3">
                        <div className="max-w-[260px] text-[11px] leading-snug text-slate-600">
                          {t?.titulo ?? "—"}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        {t?.ml_item_id ? (
                          <a
                            href={urlPub(t.ml_item_id, t.url) ?? "#"}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-0.5 font-mono text-[11px] text-indigo-600 hover:underline"
                          >
                            {t.ml_item_id}
                            <ExternalLink size={9} />
                          </a>
                        ) : (
                          <span className="text-[11px] text-slate-400">—</span>
                        )}
                        {t?.estado ? (
                          <span
                            className={`ml-1.5 rounded px-1 py-0.5 text-[9px] ${
                              t.estado === "active"
                                ? "bg-emerald-100 text-emerald-700"
                                : "bg-amber-100 text-amber-700"
                            }`}
                          >
                            {t.estado}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3 text-right text-xs font-medium tabular-nums text-slate-900">
                        {mxn(t?.precio)}
                        {t?.precio != null ? (
                          <PrecioConfirmado iso={t.precio_confirmado_en} />
                        ) : null}
                      </td>
                      <td className="py-2 pr-3 text-right text-xs tabular-nums text-slate-600">
                        {num(t?.visitas_30d)}
                      </td>
                      <td className="py-2 pr-3 text-right text-xs tabular-nums text-slate-600">
                        {num(t?.unidades_30d)}
                      </td>
                      <td className="py-2 pr-2 text-right text-xs tabular-nums text-slate-500">
                        {t?.conversion_30d === null || t?.conversion_30d === undefined
                          ? "—"
                          : `${t.conversion_30d}%`}
                      </td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {datos.aviso ? (
        <div className="mt-1.5 text-[10px] leading-snug text-slate-400">{datos.aviso}</div>
      ) : null}
    </div>
  );
}

/**
 * La barra de términos de una subcategoría: qué se busca en ese nicho, qué de eso
 * cubrimos, y qué palabras conviene agregar.
 *
 * Los términos son demanda MEDIDA (lo que ML publica en /trends). Las palabras las
 * propone la IA a partir de esos términos y de los títulos de los líderes, y cada
 * una viene marcada con `respaldada`: si no aparece en ninguno de los dos, la IA la
 * inventó y se muestra distinto en vez de pasarla como dato.
 */
function BarraTerminos({ categoriaId }: { categoriaId: string }) {
  const [datos, setDatos] = useState<CompetenciaTerminosSub | null>(null);
  const [sug, setSug] = useState<CompetenciaSugerenciaSub | null>(null);
  const [pidiendo, setPidiendo] = useState(false);
  const [fallo, setFallo] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    terminosSubcategoria(categoriaId, ac.signal).then(setDatos).catch(() => {});
    return () => ac.abort();
  }, [categoriaId]);

  if (!datos) return null;
  if (datos.terminos.length === 0)
    return (
      <div className="flex items-center gap-1.5 px-3 py-2 text-[11px] text-slate-400">
        <Ban size={11} /> {datos.aviso}
      </div>
    );

  return (
    <div className="border-b border-slate-100 bg-white px-3 py-2.5">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          <Search size={11} /> Términos más buscados
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
            datos.cubiertos === 0
              ? "bg-rose-100 text-rose-800"
              : "bg-amber-100 text-amber-800"
          }`}
          title="Cuántos de los términos publicados por ML cubren nuestros títulos de este nicho"
        >
          {datos.cubiertos} de {datos.total} cubiertos
        </span>
        <button
          onClick={async () => {
            setPidiendo(true);
            setFallo(null);
            try {
              setSug(await sugerirSubcategoria(categoriaId));
            } catch (e) {
              setFallo(mensajeDeError(e, "La IA no pudo sugerir."));
            } finally {
              setPidiendo(false);
            }
          }}
          disabled={pidiendo}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-white px-2 py-1 text-[11px] font-medium text-indigo-700 ring-1 ring-indigo-200 hover:bg-indigo-50 disabled:opacity-50"
        >
          {pidiendo ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Sparkles size={11} />
          )}
          Sugerir palabras
        </button>
      </div>

      {/* Los términos, en orden de volumen. Verde = nuestro título lo cubre. */}
      <div className="flex flex-wrap gap-1">
        {datos.terminos.map((t) => (
          <span
            key={t.termino}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] ${
              t.cubierto
                ? "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200"
                : "bg-slate-50 text-slate-500 ring-1 ring-slate-200"
            }`}
            title={
              t.cubierto
                ? `Lo cubre: ${t.cubierto_por.join(", ")}`
                : "Nadie lo cubre: es tráfico que no nos puede encontrar"
            }
          >
            <span className="tabular-nums text-slate-400">{t.posicion}</span>
            {t.termino}
            {t.cubierto ? <Check size={9} /> : null}
          </span>
        ))}
      </div>

      {fallo ? (
        <div className="mt-2 text-[11px] text-rose-700">{fallo}</div>
      ) : null}

      {sug ? (
        <div className="mt-2.5 rounded-lg bg-indigo-50/60 p-2.5 ring-1 ring-indigo-100">
          <div className="mb-1.5 inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
            <Sparkles size={11} /> Palabras a usar en este nicho
          </div>
          <div className="space-y-1">
            {sug.palabras.map((p) => (
              <div key={p.palabra} className="flex items-start gap-2 text-[11px]">
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 font-semibold ${
                    p.respaldada
                      ? "bg-white text-indigo-800 ring-1 ring-indigo-200"
                      : "bg-amber-100 text-amber-900"
                  }`}
                  title={
                    p.respaldada
                      ? "Sale de los términos buscados o de los títulos líderes"
                      : "No aparece en la demanda medida ni en los títulos líderes: la IA la propuso sola"
                  }
                >
                  {p.palabra}
                  {p.respaldada ? "" : " ⚠"}
                </span>
                <span className="text-slate-600">{p.porque}</span>
              </div>
            ))}
          </div>
          {sug.evitar.length > 0 ? (
            <div className="mt-2 text-[10px] text-slate-500">
              evitar: {sug.evitar.join(" · ")}
            </div>
          ) : null}
          {sug.evitar_descartados.length > 0 ? (
            <div className="mt-1 flex items-start gap-1.5 text-[10px] text-amber-800">
              <AlertTriangle size={10} className="mt-0.5 shrink-0" />
              La IA sugirió evitar {sug.evitar_descartados.join(", ")}, pero son
              términos que la gente SÍ busca. Se descartaron.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Un nicho del top del padre. Se despliega y muestra NUESTROS SKUs de ese nicho
 * con la barra completa por tienda: título, MLM con link, precio, visitas, ventas
 * y conversión — la misma barra de siempre, para los que están publicados.
 */
function FilaNicho({ n }: { n: CompetenciaNicho }) {
  const [abierto, setAbierto] = useState(false);
  return (
    <div
      className={`overflow-hidden rounded-lg ring-1 ${
        n.tenemos ? "bg-slate-50 ring-slate-200" : "bg-rose-50/50 ring-rose-200"
      }`}
    >
      <button
        onClick={() => n.tenemos && setAbierto((v) => !v)}
        disabled={!n.tenemos}
        className={`flex w-full flex-wrap items-center gap-3 p-2 text-left ${
          n.tenemos ? "hover:bg-white/60" : "cursor-default"
        }`}
      >
        {n.tenemos ? (
          abierto ? (
            <ChevronDown size={14} className="shrink-0 text-slate-400" />
          ) : (
            <ChevronRight size={14} className="shrink-0 text-slate-300" />
          )
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        <Medalla pos={n.posicion} />
        <Foto src={n.lider.imagen ?? null} alt={n.categoria_nombre} size={44} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-slate-900">
            {n.categoria_nombre}
            {n.otras_posiciones.length > 0 ? (
              <span
                className="ml-1.5 text-[10px] font-normal text-slate-400"
                title="Otras publicaciones del top que caen en este mismo nicho"
              >
                también #{n.otras_posiciones.join(", #")}
              </span>
            ) : null}
          </div>
          <div className="truncate text-[11px] text-slate-500">
            {n.lider.titulo ? (
              <>líder: {n.lider.titulo}</>
            ) : (
              /* La posición y la categoría son API; el título y la foto del líder
                 solo salen del raspado. Decirlo es mejor que dejar la línea vacía. */
              <span className="text-slate-400">
                líder sin ficha — falta raspar esta categoría
              </span>
            )}
          </div>
        </div>
        <div className="text-right text-[11px] tabular-nums text-slate-600">
          <div className="font-medium text-slate-900">{mxn(n.lider.precio)}</div>
          <div>
            <Eye size={9} className="mr-0.5 inline" />
            {num(n.lider.visitas_30d)}
          </div>
        </div>
        <div className="w-[210px] shrink-0">
          {n.tenemos ? (
            <>
              <div className="flex items-center gap-1.5">
                {/* PUBLICADOS, no el catálogo entero: un SKU sin publicar no
                    compite en este nicho, y contarlo decía que estábamos mejor
                    posicionados de lo que estamos. En Mochilas la insignia decía
                    "20 SKUs en catálogo" y el publicado era UNO; en Tenis decía
                    363 y compiten 127. */}
                <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800">
                  {n.n_catalogo}{" "}
                  {n.solo_publicados === false
                    ? "en catálogo"
                    : n.n_catalogo === 1
                      ? "publicado"
                      : "publicados"}
                </span>
                {n.sin_vigilancia ? (
                  <span
                    className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800"
                    title="Tenemos producto en este nicho pero no está bajo observación"
                  >
                    sin medir
                  </span>
                ) : null}
              </div>
              {/* Los que se quedaron fuera se dicen, no se esconden: recortar
                  en silencio se lee como "no hay más". */}
              {n.n_sin_publicar ? (
                <div className="mt-0.5 text-[10px] text-slate-400">
                  y {n.n_sin_publicar} sin publicar
                </div>
              ) : null}
              <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500">
                {n.skus_catalogo.slice(0, 3).join(" · ")}
                {n.n_catalogo > 3 ? ` +${n.n_catalogo - 3}` : ""}
              </div>
            </>
          ) : (
            <>
              {/* DOS huecos distintos, y confundirlos manda a la acción
                  equivocada: "sin publicar" se cierra prendiendo lo que ya
                  tenemos; "no tenemos" se cierra consiguiendo producto. */}
              <span
                className="inline-flex items-center gap-1 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold text-rose-800"
                title={
                  n.n_sin_publicar
                    ? "Tenemos producto en catálogo pero ninguno publicado aquí"
                    : "No tenemos ningún SKU en esta categoría: es un hueco de catálogo"
                }
              >
                <Ban size={9} /> {n.n_sin_publicar ? "sin publicar" : "no tenemos"}
              </span>
              {n.n_sin_publicar ? (
                <div className="mt-0.5 text-[10px] text-slate-400">
                  {n.n_sin_publicar} en catálogo, ninguno publicado
                </div>
              ) : null}
            </>
          )}
        </div>
      </button>
      {abierto ? (
        <div className="border-t border-slate-200 bg-white">
          <SkusDeCategoria categoriaId={n.categoria_id} />
        </div>
      ) : null}
    </div>
  );
}

/** Encabezado de las columnas de la tabla de SKUs. Se repite en cada bloque. */
function Cabeza() {
  return (
    <thead>
      <tr className="text-[10px] uppercase tracking-wide text-slate-400">
        <th className="w-6" />
        <th className="w-12" />
        <th className="pb-1 text-left font-medium">Producto</th>
        <th className="pb-1 text-left font-medium">Subcategoría</th>
        <th className="pb-1 text-right font-medium">Mediana</th>
        <th className="pb-1 text-right font-medium">Brecha</th>
        <th className="pb-1 text-left font-medium">Tienda</th>
        <th className="pb-1 text-left font-medium">Título de la tienda</th>
        <th className="pb-1 text-left font-medium">Publicación</th>
        <th className="pb-1 text-right font-medium">Precio</th>
        <th className="pb-1 text-right font-medium">Visitas 30d</th>
        <th className="pb-1 text-right font-medium">Ventas 30d</th>
        <th className="pb-1 text-right font-medium">Conversión</th>
      </tr>
    </thead>
  );
}

/** Una fila de subcategoría: se despliega y muestra el nicho y luego lo nuestro. */
/**
 * El botón que vuelve a raspar UNA subcategoría.
 *
 * Vive aquí —en el bloque de "más vendidos"— y no en cada SKU, porque **Apify
 * cobra por PÁGINA y la página es la de la categoría**. Un botón por producto
 * pagaría la misma página varias veces: hay 2.21 SKUs por categoría, y en la más
 * grande son 127. Puesto aquí se ve desde el producto —el bloque está abierto—
 * y se cobra una sola vez.
 *
 * Dice cuándo se actualizó y por qué no se puede volver a pedir, en vez de
 * quedarse mudo: un botón que no hace nada sin explicar se reporta como bug.
 */
function BotonRefrescar({
  categoriaId,
  capturadoEn,
  onListo,
}: {
  categoriaId: string | null;
  capturadoEn: string | null;
  onListo: () => void;
}) {
  const [corriendo, setCorriendo] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [fallo, setFallo] = useState(false);

  if (!categoriaId) return null;

  const dias = capturadoEn
    ? Math.floor((Date.now() - new Date(capturadoEn).getTime()) / 86400000)
    : null;

  async function pedir() {
    setCorriendo(true);
    setMsg(null);
    setFallo(false);
    try {
      const r = await capturarRankingsCompetencia({ solo: [categoriaId!] });
      const desc = r.descartadas?.length ? r.descartadas.join(" ") : null;
      setMsg(desc ?? `Listo: ${r.con_datos ?? 0} publicaciones.`);
      setFallo(Boolean(desc));
      if (!desc) onListo();
    } catch (e) {
      setFallo(true);
      // El backend explica el motivo (recién capturada, ML no publica ranking,
      // no es nuestra) y viene en `detail`. Hay que sacarlo con `mensajeDeError`:
      // el `message` de un ApiError es "API 422: /api/competencia/rankings" —
      // la ruta y el código, no la razón.
      //
      // PASÓ: el 2-sep-2026 se reportó como bug del botón. No lo era. El backend
      // contestaba "se actualizó hace 0 día(s). Se puede volver a pedir en 3" —
      // el candado de DIAS_CANDADO haciendo su trabajo— y la pantalla enseñaba
      // el error crudo. Este archivo ya advertía que "un botón que no hace nada
      // sin explicar se reporta como bug"; explicaba, y la explicación se tiraba.
      setMsg(mensajeDeError(e, "No se pudo actualizar."));
    } finally {
      setCorriendo(false);
    }
  }

  // El mensaje va en su PROPIA línea, no dentro de la fila del encabezado.
  // Puesto en línea, a 768 px partía el título en tres renglones y se cortaba a
  // la mitad — se vio en la captura del 1-sep antes de soltarlo.
  return (
    <>
    <span className="ml-auto flex items-center gap-2 normal-case">
      {dias !== null ? (
        <span className="font-normal text-slate-400">
          actualizado hace {dias} {dias === 1 ? "día" : "días"}
        </span>
      ) : null}
      <button
        type="button"
        onClick={pedir}
        disabled={corriendo}
        title="Vuelve a leer los más vendidos de esta subcategoría. Cuesta ~$0.007."
        className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
      >
        {corriendo ? (
          <Loader2 size={10} className="animate-spin" />
        ) : (
          <RefreshCw size={10} />
        )}
        {corriendo ? "Actualizando…" : "Actualizar"}
      </button>
    </span>
    {msg ? (
      <span
        className={`mt-1 w-full basis-full text-[11px] font-normal normal-case ${
          fallo ? "text-amber-600" : "text-emerald-600"
        }`}
      >
        {msg}
      </span>
    ) : null}
    </>
  );
}


function BloqueSubcategoria({
  sub,
  abiertoSku,
  detalle,
  cargandoDetalle,
  onAbrirSku,
  onRefrescado,
  filtrando = false,
}: {
  sub: CompetenciaSubcategoria;
  abiertoSku: string | null;
  detalle: CompetenciaDetalleSku | null;
  cargandoDetalle: boolean;
  onAbrirSku: (sku: string) => void;
  /** Tras raspar una subcategoría hay que releer la vista: el ranking cambió. */
  onRefrescado: () => void;
  /** Hay algún filtro activo: se abre solo, si no el resultado queda escondido. */
  filtrando?: boolean;
}) {
  // Con un filtro puesto, dejar el bloque cerrado hace ver el filtro como roto:
  // el SKU que sí pasa queda dentro de una fila colapsada. Se abre solo, y el
  // usuario puede cerrarlo a mano después.
  const [abierta, setAbierta] = useState(filtrando);
  useEffect(() => {
    if (filtrando) setAbierta(true);
  }, [filtrando]);

  return (
    <div className="overflow-hidden rounded-lg bg-white ring-1 ring-slate-200">
      <button
        onClick={() => setAbierta((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition hover:bg-slate-50"
      >
        {abierta ? (
          <ChevronDown size={15} className="shrink-0 text-slate-400" />
        ) : (
          /* slate-400, no 300: el estado CERRADO es el que tiene que invitar
             a hacer clic, y era el más tenue de los dos. Se confundía con una
             categoría que no se despliega. */
          <ChevronRight size={15} className="shrink-0 text-slate-400" />
        )}
        {sub.pos_en_raiz ? (
          <span
            className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
            title={`El #${sub.pos_en_raiz} más vendido de toda la categoría vive en esta subcategoría`}
          >
            raíz #{sub.pos_en_raiz}
          </span>
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">
              {sub.categoria_nombre}
            </span>
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-700">
              {sub.n_skus} {sub.n_skus === 1 ? "SKU" : "SKUs"}
            </span>
            {sub.sin_datos_ml ? (
              <span
                className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500"
                title="Todavía no hemos capturado esta categoría. No quiere decir que ML no la tenga."
              >
                <Ban size={9} /> sin capturar
              </span>
            ) : null}
          </div>
          {/* El path COMPLETO, que es lo que ubica la subcategoría en el árbol,
              y el PADRE inmediato con su id: sin el id no se puede ir a verificar
              en ML de qué cuelga realmente esta subcategoría. */}
          {sub.ruta ? (
            <div className="truncate text-[10px] text-slate-400">{sub.ruta}</div>
          ) : null}
          {sub.padre_nombre ? (
            <div className="text-[10px] text-slate-400">
              padre:{" "}
              <span className="font-medium text-slate-500">{sub.padre_nombre}</span>
              {sub.padre_id ? (
                <code className="ml-1 text-slate-400">{sub.padre_id}</code>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="hidden shrink-0 items-center gap-4 text-[11px] text-slate-500 sm:flex">
          <span title="Mediana de precio del top de la subcategoría">
            med. {mxn(sub.mediana)}
          </span>
          {/* Visitas del nicho: la DEMANDA. Junto con la mediana es lo que
              permite buscar categorías de ticket alto y mucho tráfico. */}
          <span title="Visitas de 30 días sumadas del top: el tamaño de la demanda">
            <Eye size={10} className="mr-0.5 inline" />
            {num(sub.visitas_mercado)}
          </span>
          <span title="Unidades del top: cuánto se vende en el nicho (cota inferior)">
            <TrendingUp size={10} className="mr-0.5 inline" />
            {cota(sub.volumen_mercado)}
          </span>
          <span title="Términos de búsqueda que ML publica de esta categoría">
            {sub.n_terminos} términos
          </span>
        </div>
      </button>

      {abierta ? (
        <div className="border-t border-slate-100">
          {/* PRIMERO el mercado: los más vendidos de la subcategoría. */}
          <div className="bg-slate-50/60 px-3 py-3">
            <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              <Crown size={12} /> Más vendidos de {sub.categoria_nombre}
              {sub.n_ranking ? (
                <span className="font-normal normal-case text-slate-400">
                  · {sub.n_ranking} publicaciones
                </span>
              ) : null}
              <CapturaDeCategoria
                iso={sub.top?.[0]?.capturado_en ?? null}
                movido={sub.top_movido}
                movidas={sub.top_movidas}
                comparadas={sub.top_comparadas}
                primeroCambio={sub.top_primero_cambio}
              />
              <BotonRefrescar
                categoriaId={sub.categoria_id}
                capturadoEn={sub.top?.[0]?.capturado_en ?? null}
                onListo={onRefrescado}
              />
            </div>
            {sub.top.length === 0 ? (
              <div className="py-3 text-center text-xs text-slate-400">
                {/* `sin_datos_ml` ya NO significa "no lo hemos capturado": desde
                    que el sondeo de /highlights corre a diario significa que ML
                    CONTESTÓ y no publica ranking ahí. Son acciones opuestas —una
                    es "córrele la captura", la otra es "no gastes"— y el texto
                    tenía que cambiar con el dato. */}
                {sub.sin_datos_ml
                  ? "Mercado Libre no publica ranking de esta categoría. Raspar aquí no traería nada."
                  : "Sin ranking guardado. Corre «Más vendidos»."}
              </div>
            ) : (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {sub.top.map((r) => (
                  <Tarjeta key={r.externo_id} r={r} />
                ))}
              </div>
            )}
          </div>

          {/* DESPUÉS los nuestros. */}
          <div className="px-3 py-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Nuestros SKUs en {sub.categoria_nombre}
            </div>
            <table className="w-full">
              <Cabeza />
              <tbody>
                {sub.skus.map((s) => (
                  <Fragment key={s.sku}>
                    <FilasSku
                      s={s}
                      abierto={abiertoSku === s.sku}
                      onToggle={() => onAbrirSku(s.sku)}
                    />
                    {abiertoSku === s.sku ? (
                      <tr>
                        <td colSpan={COLS} className="p-0">
                          <DetalleSku
                            d={detalle}
                            cargando={cargandoDetalle}
                          />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Una categoría raíz completa: su top, nuestros 5 con más chance, sus nichos. */
function BloqueRaiz({
  r,
  abiertoSku,
  detalle,
  cargandoDetalle,
  onAbrirSku,
  onRefrescado,
  filtrando = false,
}: {
  r: CompetenciaRaiz;
  abiertoSku: string | null;
  detalle: CompetenciaDetalleSku | null;
  cargandoDetalle: boolean;
  onAbrirSku: (sku: string) => void;
  onRefrescado: () => void;
  filtrando?: boolean;
}) {
  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-bold text-slate-900">{r.raiz_nombre}</h2>
        <span className="text-xs text-slate-500">
          {r.n_skus} SKUs · {r.n_publicaciones} publicaciones ·{" "}
          {r.subcategorias.length} subcategorías
        </span>
      </div>

      {/* ── Nivel 1: el top de la categoría padre ── */}
      <div className="mb-4 rounded-lg bg-white p-3 ring-1 ring-slate-200">
        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          <Crown size={12} /> Más vendidos de {r.raiz_nombre}
          <CapturaDeCategoria
            iso={r.top?.[0]?.capturado_en ?? null}
            movido={r.top_movido}
            movidas={r.top_movidas}
            comparadas={r.top_comparadas}
            primeroCambio={r.top_primero_cambio}
          />
        </div>
        {r.top.length === 0 ? (
          <div className="py-4 text-center text-xs text-slate-400">
            Sin ranking guardado. Corre «Más vendidos».
          </div>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {r.top.map((x) => (
              <Tarjeta key={x.externo_id} r={x} />
            ))}
          </div>
        )}
      </div>

      {/* ── Nivel 2: los 5 nichos del top, y si tenemos con qué pelearlos ── */}
      {r.nichos.length > 0 ? (
        <div className="mb-4 rounded-lg bg-white p-3 ring-1 ring-slate-200">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            <Flame size={12} /> Los {r.nichos.length} nichos con más chance de competir
          </div>
          <div className="mb-3 text-[10px] leading-snug text-slate-400">
            Los dicta el top de arriba, no nuestro inventario: se recorre #1, #2, #3… y
            se dice si tenemos producto con qué pelear cada uno. El conteo es sobre el
            catálogo completo, no sobre los SKUs vigilados.
          </div>
          <div className="space-y-2">
            {r.nichos.map((n) => (
              <FilaNicho key={n.categoria_id} n={n} />
            ))}
          </div>
        </div>
      ) : null}

      {/* ── Nivel 3: las subcategorías, cada una desplegable ── */}
      <div className="space-y-2">
        {r.subcategorias.map((s) => (
          <BloqueSubcategoria
            key={s.categoria_id ?? s.categoria_nombre}
            sub={s}
            abiertoSku={abiertoSku}
            detalle={detalle}
            cargandoDetalle={cargandoDetalle}
            onAbrirSku={onAbrirSku}
            onRefrescado={onRefrescado}
            filtrando={filtrando}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * `ventas_hasta` llega como fecha SIN hora ("2026-09-01"). `new Date()` la lee
 * como medianoche UTC, que en México son las 18:00 del día ANTERIOR: la caja
 * diría "31 de agosto" teniendo datos del 1 de septiembre. Se le pega la hora
 * local para que se lea como el día que es.
 */
function aFecha(iso: string): Date {
  return new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
}

/**
 * Las VISTAS de trabajo: cada una es una PREGUNTA de negocio, no un filtro.
 *
 * ── POR QUÉ EXISTEN ─────────────────────────────────────────────────────────
 * La barra tenía seis controles sueltos y ninguno contestaba lo que se le
 * preguntaba al tab. Encontrar "dónde nos ven y no nos compran" exigía saber de
 * antemano que eso se arma cruzando visitas contra unidades — o sea, saber la
 * respuesta antes de preguntarla.
 *
 * Todas se calculan EN EL NAVEGADOR sobre el árbol ya cargado: ni una petición
 * más. Los datos ya venían, sólo que no había cómo pedirlos.
 *
 * ⚠️ "Ranking vencido" estaba en el boceto y SE CAYÓ al medirla: agarraba 627 de
 * 1,218 subcategorías, el 51% del árbol. Una vista que selecciona la mitad de
 * todo no es una vista. La mayoría eran categorías que no venden nada y que el
 * barrido salta a propósito — el hueco real ya lo dice `ML ya se movió`.
 */
type VistaId =
  | "todas"
  | "sin_venta"
  | "sin_visitas"
  | "movido"
  | "sin_publicar"
  | "sin_ranking";

/** Suma un campo sobre los SKUs de una subcategoría. */
function sumaSub(
  s: CompetenciaSubcategoria,
  campo: "visitas_30d" | "unidades_30d",
): number {
  return (s.skus ?? []).reduce((a, k) => a + (k[campo] ?? 0), 0);
}

/**
 * Visitas que llegaron a una publicación que se PODÍA COMPRAR.
 *
 * ⚠️ No es lo mismo que `sumaSub(s, "visitas_30d")`, y la diferencia invalida la
 * vista: ese total incluye las visitas de publicaciones PAUSADAS, donde el
 * visitante no es que no comprara — es que no podía. Medido el 1-sep-2026: de
 * las 74 subcategorías que la vista daba como "nos ven y no compran", **29 (el
 * 39%) tenían TODO su tráfico en pausadas** (Bongs, Smartbands, Grúas para
 * Pacientes). Sólo 45 eran el hallazgo de verdad.
 *
 * `under_review` tampoco cuenta: tampoco se puede comprar ahí.
 */
function visitasComprables(s: CompetenciaSubcategoria): number {
  return (s.skus ?? []).reduce(
    (a, k) =>
      a +
      (k.tiendas ?? []).reduce(
        (b, t) =>
          b + ((t.estado ?? "").toLowerCase() === "active" ? (t.visitas_30d ?? 0) : 0),
        0,
      ),
    0,
  );
}

/**
 * Cuántas publicaciones ACTIVAS de la subcategoría se midieron y dieron CERO.
 *
 * ── EL `=== 0` ES ESTRICTO A PROPÓSITO: null NO ES CERO ─────────────────────
 * `visitas_30d` vacío significa "nunca se midió", no "nadie la vio". Tratarlos
 * igual es el defecto que costó tres días de cron en v0.366.0 (un 429 se leía
 * como "el token está roto") y que v0.359.0 arregló en la sonda de highlights.
 * Aquí sería peor: la vista existe para señalar publicaciones invisibles, así
 * que confundir "no la ve nadie" con "no preguntamos" la vuelve un generador
 * de trabajo falso.
 *
 * **Y se midió, no se supuso.** El 2-sep-2026 había 217 activas sin medir
 * nunca. Preguntándole a ML por las 215 que seguían vivas, una por una:
 *
 *    206  con visitas  (mediana 99)
 *      9  EN CERO de verdad
 *      0  no se pudo medir
 *
 * O sea que contarlas como ceros habría señalado 217 publicaciones
 * "invisibles" donde hay 9 — **un error de 23x**, y del lado caro.
 *
 * ── EL HUECO YA ESTÁ TAPADO ────────────────────────────────────────────────
 * Venía de `competencia_visitas.objetivo()`, que arrancaba desde
 * `market_listing_metrics` y por eso sólo refrescaba lo que ya conocía. Desde
 * v0.367.0 arranca desde `channel.listings`. Tras la siembra del 2-sep quedan
 * 702 activas con **1** sin medir (eran 217) y **16** con cero medido (eran 4).
 *
 * La prueba de que la distinción era la correcta: antes, exigir medición daba
 * 3 subcategorías y no exigirla daba 83. Ahora dan 7 y 8 — convergieron solas
 * al desaparecer los nulos.
 */
function activasEnCero(s: CompetenciaSubcategoria): number {
  let n = 0;
  for (const k of s.skus ?? [])
    for (const tienda of k.tiendas ?? [])
      if ((tienda.estado ?? "").toLowerCase() === "active" && tienda.visitas_30d === 0)
        n += 1;
  return n;
}

const VISTAS: {
  id: VistaId;
  titulo: string;
  detalle: string;
  tono: "neutro" | "ambar";
  /**
   * `true` = escrita y medida, pero NO se dibuja todavía.
   *
   * Se sueltan de a una, no las cinco de golpe: cada vista cambia cómo se lee el
   * tab, y con cinco puestas al mismo tiempo no hay forma de saber cuál ayudó.
   * La lógica y el conteo ya funcionan — quitar esta línea es todo lo que hace
   * falta para encender cualquiera.
   */
  propuesta?: boolean;
  cumple: (s: CompetenciaSubcategoria) => boolean;
}[] = [
  {
    id: "sin_venta",
    titulo: "Nos ven y no compran",
    detalle: "Publicación activa con tráfico y cero ventas. Aquí conviene mirar al de enfrente.",
    tono: "neutro",
    cumple: (s) =>
      sumaSub(s, "unidades_30d") === 0 && visitasComprables(s) > 0,
  },
  {
    id: "sin_visitas",
    titulo: "Publicada y nadie la ve",
    detalle:
      "Activa, ya medida, y el contador de visitas en cero. No es que no compren: no llegan.",
    tono: "ambar",
    /**
     * ALGUNA activa en cero, no todas.
     *
     * La primera versión pedía que TODAS las activas de la subcategoría
     * estuvieran en cero, y **escondía más de la mitad de lo que se buscaba**:
     * de las 16 publicaciones invisibles sólo asomaban 7, porque las otras 9
     * conviven con una publicación que sí recibe tráfico. La unidad de la
     * pregunta es la PUBLICACIÓN; la subcategoría es sólo donde se dibuja.
     *
     * Con "alguna" salen 15 subcategorías de 1,226 (el 1%) y no se pierde
     * ninguna de las 16 — sigue siendo una vista, no un cajón.
     *
     * El `=== 0` estricto es lo que mantiene la distinción: una publicación sin
     * medir tiene `null` y NO cuenta. Ver `activasEnCero`.
     */
    cumple: (s) => activasEnCero(s) > 0,
  },
  {
    id: "movido",
    titulo: "ML ya se movió",
    propuesta: true,
    detalle: "El top cambió después de nuestra captura: lo que ves ya no es lo que hay.",
    tono: "ambar",
    cumple: (s) => s.top_movido === true,
  },
  {
    id: "sin_publicar",
    titulo: "Producto apagado",
    propuesta: true,
    detalle: "Tenemos SKUs aquí y alguno no está publicado en ninguna tienda.",
    tono: "neutro",
    cumple: (s) => (s.skus ?? []).some((k) => (k.tiendas ?? []).length === 0),
  },
  {
    id: "sin_ranking",
    titulo: "ML no publica ranking",
    propuesta: true,
    detalle: "Mercado Libre no lista más vendidos ahí: raspar sería tirar dinero.",
    tono: "neutro",
    cumple: (s) => s.ml_publica === false,
  },
];

/** Días de CALENDARIO entre `iso` y hoy. Null si no hay fecha. */
function diasDesde(iso: string | null): number | null {
  if (!iso) return null;
  const d = aFecha(iso);
  const hoy = new Date();
  const soloDia = (x: Date) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  return Math.round((soloDia(hoy) - soloDia(d)) / 86400000);
}

/** "hoy" / "ayer" / "hace N días". */
function textoRelativo(dias: number): string {
  return dias <= 0 ? "hoy" : dias === 1 ? "ayer" : `hace ${dias} días`;
}

/** El día y el mes, sin año: "13 de agosto". */
function diaYMes(iso: string): string {
  return aFecha(iso).toLocaleDateString("es-MX", { day: "numeric", month: "long" });
}

/**
 * De cuándo es el precio que se está mostrando.
 *
 * ── POR QUÉ HACE FALTA ──────────────────────────────────────────────────────
 * Un precio sin fecha se lee como "el de ahora", y muchos no lo son. Medido el
 * 1-sep-2026 sobre las 4,828 publicaciones del tab:
 *
 *   751  confirmadas en las últimas 6 h
 *   198  hoy (6-24 h)
 *    69  1-3 días
 *   240  3-7 días
 * 2,180  MÁS DE 7 DÍAS
 * 1,390  NUNCA confirmadas
 *
 * O sea que sólo el 21% tiene un precio confirmado en 24 h.
 *
 * ── LOS TRES ESTADOS NO SON DOS ─────────────────────────────────────────────
 * `null` NO es "viejo": es que ese precio nunca se confirmó contra ML y lo que
 * se muestra viene de `channel.listings.price`, que **no es lo que se cobra**
 * (`price_sale` sí lo es — ver `precio_al_abrir.py`). Presentarlo igual que uno
 * confirmado hace tres días sería mentir por omisión, que es justo lo que la
 * migración 0042 vino a corregir.
 *
 * Ámbar pasado UN día: el barrido de precios tarda ~9.3 h por vuelta y los
 * webhooks cubren el 72% de las activas, así que algo sano se confirma dentro
 * del día.
 */
function PrecioConfirmado({ iso }: { iso?: string | null }) {
  if (!iso)
    return (
      <div
        className="text-[10px] text-amber-700"
        title="Este precio nunca se confirmó contra Mercado Libre: sale del sync de 15 min, que NO refleja promociones. El que se cobra puede ser más bajo."
      >
        sin confirmar
      </div>
    );
  const dias = diasDesde(iso)!;
  return (
    <div
      className={`text-[10px] ${dias > 1 ? "text-amber-700" : "text-slate-400"}`}
      title={`Precio confirmado contra Mercado Libre el ${diaYMes(iso)}`}
    >
      conf. {textoRelativo(dias)}
    </div>
  );
}

/**
 * Cuándo se capturó el ranking DE ESTA CATEGORÍA, junto a su encabezado.
 *
 * ── POR QUÉ NO ALCANZA CON LA FECHA DE ARRIBA ───────────────────────────────
 * El encabezado de la página muestra una fecha para todo el árbol, y eso siempre
 * pinta más fresco de lo que está. El 1-sep-2026 decía "hace 14 días (18 de
 * agosto)" mientras el top de Deportes y Fitness era del **13** — 19 días.
 *
 * Y esos 19 días no son un detalle: cotejado ese mismo día contra `/highlights`
 * en vivo, el top se había renovado ENTERO — 0 de 5 posiciones coincidían, ni en
 * la misma posición ni en ninguna otra. La fecha del árbol no sirve para decidir
 * si lo que estás viendo todavía vale. La de la categoría sí.
 *
 * Ámbar a los 40 días porque el ranking se raspa una vez al mes; con el umbral de
 * las visitas (2 días) estaría en alerta permanente siendo lo normal.
 */
function CapturaDeCategoria({
  iso,
  movido,
  movidas,
  comparadas,
  primeroCambio,
}: {
  iso: string | null;
  /**
   * El sondeo GRATIS de /highlights vio que ML movió su top DESPUÉS de nuestra
   * captura. Es la única forma de saber si lo que estás viendo sigue vigente sin
   * volver a pagar por raspar — y el dato ya estaba guardado, sólo que la
   * pantalla no lo decía.
   *
   * Medido el 1-sep-2026 en Cables de Audio y Video: capturado el 18-ago y el
   * top había cambiado ESE MISMO DÍA; sólo 2 de 20 posiciones seguían iguales.
   * En todo el tab, 423 de 1,218 subcategorías estaban en esa situación.
   */
  movido?: boolean | null;
  /**
   * CUÁNTO se movió, no sólo que se movió.
   *
   * "ML ya se movió" no distingue un empujón de un vuelco: que el #7 y el #8 se
   * intercambien y que cambie el #1 pintaban la misma pastilla, y llevan a
   * decisiones opuestas. Con 198 de 470 categorías movidas el mismo día de su
   * captura, un aviso que no gradúa se vuelve ruido y se deja de mirar.
   *
   * Sale de comparar posición por posición contra el sondeo diario, que ya está
   * guardado: ni una llamada más.
   */
  movidas?: number | null;
  comparadas?: number | null;
  primeroCambio?: boolean | null;
}) {
  const dias = diasDesde(iso);
  if (dias === null)
    return (
      <span className="font-normal normal-case text-slate-400">· sin capturar</span>
    );
  return (
    <>
      <span
        className={`font-normal normal-case ${
          dias > 40 ? "text-amber-700" : "text-slate-400"
        }`}
      >
        · capturado {textoRelativo(dias)} ({diaYMes(iso!)})
      </span>
      {/* Se prefiere la CUENTA sobre la fecha: `movido` sale de comparar
          `cambio_en` contra nuestra captura, y eso puede prenderse por un cambio
          que después se revirtió o por una posición fuera del top 10. La cuenta
          compara lo que hay HOY contra lo que mostramos. Cuando no se puede
          comparar, se cae a la señal vieja en vez de callar. */}
      {(movidas ?? 0) > 0 || (movidas === null && movido) ? (
        <span
          className={`rounded px-1.5 py-0.5 text-[9px] font-bold normal-case ${
            primeroCambio
              ? "bg-amber-200 text-amber-900"
              : "bg-amber-100 text-amber-800"
          }`}
          title={
            movidas != null && comparadas != null
              ? `Comparado hoy contra Mercado Libre: ${movidas} de las ${comparadas} posiciones del top son distintas${primeroCambio ? ", incluido el #1" : ""}.`
              : "El sondeo diario detectó que este top cambió después de nuestra captura."
          }
        >
          {primeroCambio
            ? "el #1 cambió"
            : movidas != null && comparadas != null
              ? `${movidas} de ${comparadas} movidas`
              : "ML ya se movió"}
        </span>
      ) : null}
    </>
  );
}

/**
 * Una de las tres fechas del encabezado.
 *
 * Cada fuente se refresca a un ritmo DISTINTO, así que cada una trae su propio
 * umbral de "esto ya está viejo": las visitas corren a diario, las ventas
 * entran por webhook en segundos y el ranking se raspa una vez al mes. Un solo
 * umbral pintaría el ranking en ámbar todos los meses siendo lo normal.
 *
 * Sin dato dice "sin medir", NO una fecha inventada: un hueco honesto vale más
 * que un número que miente.
 */
function Frescura({
  etiqueta,
  verbo,
  iso,
  viejoEnDias,
  sufijo,
}: {
  etiqueta: string;
  verbo: string;
  iso: string | null;
  viejoEnDias: number;
  /** Aclaración corta al final, p. ej. "el más viejo". */
  sufijo?: string | null;
}) {
  if (!iso) {
    return (
      <span className="text-slate-400">
        {etiqueta}: <span className="italic">sin medir</span>
      </span>
    );
  }
  const dias = diasDesde(iso)!;
  const relativo = textoRelativo(dias);
  const viejo = dias > viejoEnDias;
  return (
    <span className={viejo ? "text-amber-700" : undefined}>
      {etiqueta} {verbo}{" "}
      <span
        className={`font-medium ${viejo ? "text-amber-800" : "text-slate-800"}`}
      >
        {relativo}
      </span>
      <span className="text-slate-400">
        {" "}
        ({diaYMes(iso)}){sufijo ? ` ${sufijo}` : ""}
      </span>
    </span>
  );
}

export default function CompetenciaPage() {
  const [canal, setCanal] = useState("mercado_libre");
  const [raices, setRaices] = useState<CompetenciaRaiz[]>([]);
  // Los tres en ISO CRUDO: <Frescura> necesita la fecha para calcular "hace N
  // días", así que formatear aquí obligaba a guardar el dato dos veces.
  const [capturado, setCapturado] = useState<string | null>(null);
  const [capturadoDesde, setCapturadoDesde] = useState<string | null>(null);
  const [ventasHasta, setVentasHasta] = useState<string | null>(null);
  const [visitasMedidas, setVisitasMedidas] = useState<string | null>(null);
  const [puedeRefrescar, setPuedeRefrescar] = useState(true);
  const [estado, setEstado] = useState<CompetenciaEstado | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [avisos, setAvisos] = useState<string[]>([]);

  // Filtros: raíz, subcategoría y SKUs (varios, separados por coma).
  const [fRaiz, setFRaiz] = useState("");
  const [fSub, setFSub] = useState("");
  const [fSku, setFSku] = useState("");
  // Rango de visitas: dos extremos, como el de precios de Airbnb. `null` = abierto.
  const [vMin, setVMin] = useState<number | null>(null);
  const [vMax, setVMax] = useState<number | null>(null);
  const [ordenSub, setOrdenSub] = useState<OrdenSub>("defecto");
  // Solo los SKUs que aparecen en el top de su subcategoría.
  const [soloTop, setSoloTop] = useState(false);
  // La VISTA de trabajo. Entra ANTES que los filtros finos: primero la pregunta.
  const [vista, setVista] = useState<VistaId>("todas");
  const [comoAbierto, setComoAbierto] = useState(false);

  const [abiertoSku, setAbiertoSku] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<CompetenciaDetalleSku | null>(null);
  const [cargandoDetalle, setCargandoDetalle] = useState(false);

  const cargar = useCallback(
    async (signal?: AbortSignal) => {
      setCargando(true);
      setError(null);
      try {
        const r = await vistaCompetencia(canal, signal);
        setRaices(r.raices);
        setCapturado(r.capturado_en);
        setCapturadoDesde(r.capturado_desde);
        setVentasHasta(r.ventas_hasta);
        setVisitasMedidas(r.visitas_medidas);
        setPuedeRefrescar(r.puede_refrescar);
        if (r.aviso) setAvisos([r.aviso]);
      } catch (e) {
        if (!signal?.aborted)
          setError(mensajeDeError(e, "No se pudo cargar la vista."));
      } finally {
        setCargando(false);
      }
    },
    [canal],
  );

  useEffect(() => {
    const ac = new AbortController();
    void cargar(ac.signal);
    return () => ac.abort();
  }, [cargar]);

  useEffect(() => {
    const ac = new AbortController();
    estadoCompetencia(ac.signal).then(setEstado).catch(() => {});
    return () => ac.abort();
  }, []);

  const abrirSku = async (sku: string) => {
    if (abiertoSku === sku) {
      setAbiertoSku(null);
      return;
    }
    setAbiertoSku(sku);
    setDetalle(null);
    setCargandoDetalle(true);
    try {
      setDetalle(await detalleSkuCompetencia(sku));
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo cargar el detalle."));
    } finally {
      setCargandoDetalle(false);
    }
  };

  /**
   * Los tres filtros. Se aplican en el navegador sobre el árbol ya cargado: la
   * vista viene completa en un GET, así que filtrar no cuesta otra petición.
   */
  const raicesFiltradas = raices
    .filter((r) => !fRaiz || r.raiz_id === fRaiz)
    .map((r) => {
      // El filtro de SKUs acepta varios separados por coma, como en Productos y
      // Omnicanal, y hace match por fragmento: "TEC-15" trae TEC-1539 y TEC-1567.
      const trozos = fSku
        .split(",")
        .map((x) => x.trim().toUpperCase())
        .filter(Boolean);
      const pasa = (sku: string) =>
        trozos.length === 0 || trozos.some((t) => sku.toUpperCase().includes(t));

      // Rango de visitas. Un SKU sin medir (null) solo se esconde si se movió el
      // extremo inferior: pedir "de 100 visitas para arriba" excluye lo que no
      // tiene dato, pero el rango abierto no debe desaparecerlo.
      const enRango = (v: number | null) => {
        if (v === null || v === undefined) return vMin === null || vMin === 0;
        if (vMin !== null && v < vMin) return false;
        if (vMax !== null && v > vMax) return false;
        return true;
      };
      const admite = (x: CompetenciaSkuVista) =>
        pasa(x.sku) && enRango(x.visitas_30d) && (!soloTop || x.en_top);

      const enVista =
        vista === "todas"
          ? () => true
          : VISTAS.find((v) => v.id === vista)!.cumple;

      const subs = r.subcategorias
        .filter((s) => !fSub || s.categoria_id === fSub)
        // La vista se evalúa sobre la subcategoría COMPLETA, antes de podarle los
        // SKUs: si se evaluara después, filtrar por SKU cambiaría qué vistas
        // aplican y el conteo del botón dejaría de cuadrar con lo que se ve.
        .filter(enVista)
        .map((s) => ({ ...s, skus: ordenar(s.skus.filter(admite), ORDEN_SKUS) }))
        // Una subcategoría sin SKUs que pasen el filtro se conserva SOLO cuando no
        // hay filtro que justifique esconderla — así el árbol completo sigue
        // navegable. Pero `soloTop` sí es un filtro: si se pide "solo donde
        // estamos en el top", una subcategoría donde NO estamos no tiene nada que
        // hacer ahí. Sin esta condición el botón no filtraba nada.
        .filter(
          (s) =>
            s.skus.length > 0 ||
            (!soloTop && trozos.length === 0 && vMin === null && vMax === null),
        );

      return {
        ...r,
        subcategorias: ordenarSubs(subs, ordenSub),
        skus: ordenar(
          r.skus.filter((x) => admite(x) && (!fSub || x.categoria_id === fSub)),
          ORDEN_SKUS,
        ),
      };
    })
    .filter((r) => r.subcategorias.length > 0 || r.skus.length > 0);

  /**
   * La escala del deslizador.
   *
   * El techo se redondea HACIA ARRIBA en vez de usar el máximo exacto. Con el
   * máximo exacto el SKU más grande queda al borde: la lona tiene 9,924 visitas y
   * arrastrar la manija a 9,900 la excluía, aunque a ojo se ve como "hasta el
   * final". Con techo 10,000 el extremo derecho sí incluye todo.
   */
  const maxVisitas = Math.max(
    0,
    ...raices.flatMap((r) => r.skus.map((s) => s.visitas_30d ?? 0)),
  );
  const topeVisitas = (() => {
    if (maxVisitas <= 100) return 100;
    const escala = 10 ** (Math.floor(Math.log10(maxVisitas)) - 1);
    return Math.ceil(maxVisitas / escala) * escala;
  })();

  // Los conteos salen del árbol CRUDO, no del filtrado: si cambiaran al filtrar,
  // el número dejaría de decir cuántas hay y pasaría a decir cuántas quedan.
  const subsTodas = raices.flatMap((r) => r.subcategorias ?? []);
  const conteoVista = (id: VistaId) =>
    id === "todas"
      ? subsTodas.length
      : subsTodas.filter(VISTAS.find((v) => v.id === id)!.cumple).length;

  const hayFiltro = Boolean(
    fRaiz || fSub || fSku || vMin !== null || vMax !== null || soloTop ||
      vista !== "todas",
  );
  // Cuántos están en el top, para que el botón diga si vale la pena apretarlo.
  const nEnTop = raices.reduce(
    (a, r) => a + r.skus.filter((s) => s.en_top).length,
    0,
  );

  /**
   * El árbol con SOLO la vista aplicada. De aquí salen las OPCIONES de los dos
   * desplegables.
   *
   * ── POR QUÉ NO DEL ÁRBOL CRUDO ────────────────────────────────────────────
   * Salían de `raices`, así que con una vista puesta seguían ofreciendo
   * categorías que la vista ya había descartado, y con sus conteos SIN filtrar:
   * "Nos ven y no compran" dejaba 74 subcategorías y el desplegable seguía
   * diciendo "Hogar, Muebles y Jardín (415)". Elegir esa opción daba una lista
   * vacía sin explicar por qué.
   *
   * ── Y POR QUÉ NO DE `raicesFiltradas` ─────────────────────────────────────
   * Porque ése ya aplicó `fRaiz` y `fSub`: cada desplegable se borraría a sí
   * mismo — al elegir una raíz, el desplegable de raíces se quedaría con esa
   * sola opción y no habría cómo volver. Las opciones reflejan la vista, no la
   * selección propia.
   */
  const raicesEnVista =
    vista === "todas"
      ? raices
      : raices
          .map((r) => ({
            ...r,
            subcategorias: (r.subcategorias ?? []).filter(
              VISTAS.find((v) => v.id === vista)!.cumple,
            ),
          }))
          .filter((r) => r.subcategorias.length > 0);

  /**
   * Cuántos SKUs quedan bajo esa raíz. Con la vista en "todas" es el número de
   * siempre (`n_skus`) para no mover lo que ya se leía; con una vista puesta se
   * suma sólo lo que sobrevive, en la MISMA unidad — un desplegable que cambia
   * de unidad según el estado es peor que uno que no filtra.
   */
  const skusDeRaiz = (r: CompetenciaRaiz) =>
    vista === "todas"
      ? r.n_skus
      : (r.subcategorias ?? []).reduce((a, s) => a + s.n_skus, 0);

  // Las subcategorías que se pueden elegir dependen de la raíz seleccionada.
  const subsDisponibles = raicesEnVista
    .filter((r) => !fRaiz || r.raiz_id === fRaiz)
    .flatMap((r) => r.subcategorias);

  return (
    <>
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">
        {/* BANNER — el mismo molde que Crear Productos, Costos y Omnicanal:
            gradiente 120° del índigo de la casa (#4F46E5 → #818CF8), rounded-3xl,
            el círculo blanco al 20% arriba a la derecha y el contador grande.
            Competencia era la ÚNICA página del panel sin él. */}
        <div
          className="relative overflow-hidden rounded-3xl p-6 shadow-card"
          style={{
            background: "linear-gradient(120deg, #4F46E5 0%, #818CF8 100%)",
            color: "#FFFFFF",
          }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                Centro Omnicanal · Competencia
              </div>
              <h1 className="mt-1 flex items-center gap-2 text-3xl font-extrabold tracking-tight">
                <Target size={28} /> Competencia
              </h1>
              <p className="mt-1 max-w-2xl text-sm opacity-90">
                Dónde estamos frente al mercado, por categoría y por subcategoría.
                El ranking se refresca cada quincena; las visitas y las ventas, a diario.
              </p>
              {/* El tutorial va del lado del TEXTO, no junto a los controles: es
                  lo que se lee, no una acción más. Y va aquí arriba porque la
                  pregunta que contesta —«¿esto de cuándo es?»— se hace antes de
                  tocar nada. */}
              <button
                onClick={() => setComoAbierto(true)}
                className="mt-3 flex items-center gap-2 rounded-xl bg-white/15 px-3 py-2 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur hover:bg-white/25"
              >
                <BookOpen size={14} /> Cómo leer Competencia
              </button>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {num(subsTodas.length)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                subcategorías
              </div>
            </div>
          </div>
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
            style={{ background: "#FFFFFF" }}
          />
        </div>

        {/* LAS VISTAS. Van ARRIBA de los filtros a propósito: la pregunta primero,
            los acotadores después. Cada una lleva su conteo para que se vea si
            vale la pena entrar antes de entrar. */}
        <div className="mt-5 flex flex-wrap items-stretch gap-2.5">
          {[
            {
              id: "todas" as VistaId,
              titulo: "Todas",
              detalle: "El árbol completo, sin acotar.",
              tono: "neutro" as const,
            },
            // Sólo las que NO están en propuesta. Ver el comentario de VISTAS.
            ...VISTAS.filter((v) => !v.propuesta),
          ].map(
            (v) => {
              const activa = vista === v.id;
              const n = conteoVista(v.id);
              return (
                <button
                  key={v.id}
                  onClick={() => {
                    const nueva: VistaId = activa ? "todas" : v.id;
                    setVista(nueva);
                    // Si la raíz o la subcategoría elegidas no sobreviven a la
                    // vista nueva, se sueltan: dejarlas puestas mostraba un árbol
                    // vacío sin decir que la culpa era de un filtro invisible.
                    if (nueva !== "todas") {
                      const cumple = VISTAS.find((x) => x.id === nueva)!.cumple;
                      const vivas = raices
                        .map((r) => ({
                          raiz: r.raiz_id ?? "",
                          subs: (r.subcategorias ?? []).filter(cumple),
                        }))
                        .filter((x) => x.subs.length > 0);
                      if (fRaiz && !vivas.some((x) => x.raiz === fRaiz)) setFRaiz("");
                      if (
                        fSub &&
                        !vivas.some((x) =>
                          x.subs.some((s) => s.categoria_id === fSub),
                        )
                      )
                        setFSub("");
                    }
                  }}
                  className={`flex min-w-[190px] flex-1 flex-col gap-0.5 rounded-2xl border p-3.5 text-left transition ${
                    activa
                      ? "border-indigo-600 bg-indigo-50"
                      : "border-slate-200 bg-white hover:border-indigo-200"
                  }`}
                  title={v.detalle}
                >
                  <div
                    className={`text-2xl font-extrabold tabular-nums leading-tight ${
                      activa
                        ? "text-indigo-700"
                        : v.tono === "ambar"
                          ? "text-amber-700"
                          : "text-slate-900"
                    }`}
                  >
                    {num(n)}
                  </div>
                  <div
                    className={`text-[13.5px] font-bold ${
                      activa ? "text-indigo-900" : "text-slate-900"
                    }`}
                  >
                    {v.titulo}
                  </div>
                  <div
                    className={`text-[11.5px] leading-snug ${
                      activa ? "text-indigo-600" : "text-slate-400"
                    }`}
                  >
                    {v.detalle}
                  </div>
                </button>
              );
            },
          )}
        </div>

        <div className="mt-5" />

        {/* Canal: la categoría manda las búsquedas de competencia, y cada canal
            tiene su propia taxonomía — por eso el filtro va arriba de todo. */}
        <div className="mb-4 flex w-fit items-center gap-1 rounded-lg bg-white p-1 ring-1 ring-slate-200">
          {[
            { id: "mercado_libre", label: "Mercado Libre" },
            { id: "amazon", label: "Amazon" },
          ].map((c) => (
            <button
              key={c.id}
              onClick={() => setCanal(c.id)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                canal === c.id ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              <Store size={13} />
              {c.label}
            </button>
          ))}
        </div>

        {/* Filtros: raíz, subcategoría y SKUs. Se aplican en el navegador sobre
            el árbol ya cargado — no cuestan otra petición. */}
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select
            value={fRaiz}
            onChange={(e) => {
              setFRaiz(e.target.value);
              // La subcategoría elegida puede no existir en la nueva raíz.
              setFSub("");
            }}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700"
          >
            <option value="">Todas las categorías principales</option>
            {raicesEnVista.map((r) => (
              <option key={r.raiz_id ?? r.raiz_nombre} value={r.raiz_id ?? ""}>
                {r.raiz_nombre} ({num(skusDeRaiz(r))})
              </option>
            ))}
          </select>

          <SelectorBuscable
            valor={fSub}
            vacio="Todas las subcategorías"
            placeholder="Buscar subcategoría…"
            opciones={subsDisponibles.map((s) => ({
              id: s.categoria_id ?? "",
              label: s.categoria_nombre,
              n: s.n_skus,
            }))}
            onCambio={setFSub}
          />

          <div className="relative">
            <Search
              size={13}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={fSku}
              onChange={(e) => setFSku(e.target.value)}
              placeholder="Filtrar SKUs (coma para varios)"
              className="w-[260px] rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-2.5 text-sm text-slate-700 placeholder:text-slate-400"
            />
          </div>

          <select
            value={ordenSub}
            onChange={(e) => setOrdenSub(e.target.value as OrdenSub)}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700"
            title="Ordena las SUBCATEGORÍAS: para encontrar nichos de ticket alto y mucha demanda"
          >
            {ORDENES_SUB.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>

          <button
            onClick={() => setSoloTop((v) => !v)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition ${
              soloTop
                ? "bg-amber-500 text-white"
                : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
            }`}
            title="Solo los SKUs que aparecen entre los más vendidos de su subcategoría"
          >
            <Crown size={13} />
            Solo top
            <span className={soloTop ? "text-amber-100" : "text-slate-400"}>
              ({nEnTop})
            </span>
          </button>

          {hayFiltro ? (
            <button
              onClick={() => {
                setFRaiz("");
                setFSub("");
                setFSku("");
                setVMin(null);
                setVMax(null);
                setSoloTop(false);
                setVista("todas");
              }}
              className="rounded-lg bg-white px-2.5 py-1.5 text-sm text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
            >
              Limpiar
            </button>
          ) : null}
        </div>

        {/* Rango de visitas: dos deslizadores superpuestos, como el de precios de
            Airbnb. Se cruzan a propósito — arrastrar el mínimo por encima del
            máximo los intercambia en vez de trabarse. */}
        <div className="mb-4 w-fit rounded-lg bg-white px-3 py-2.5 ring-1 ring-slate-200">
          <div className="mb-1 flex items-baseline gap-2">
            <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              <Eye size={11} /> Visitas 30 días
            </span>
            <span className="text-xs tabular-nums text-slate-700">
              {vMin === null && vMax === null
                ? "todas"
                : `${num(vMin ?? 0)} – ${vMax === null ? num(topeVisitas) + "+" : num(vMax)}`}
            </span>
          </div>
          <div className="relative h-6 w-[320px]">
            {/* Riel y tramo seleccionado */}
            <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 rounded bg-slate-200" />
            <div
              className="absolute top-1/2 h-1 -translate-y-1/2 rounded bg-indigo-500"
              style={{
                left: `${((vMin ?? 0) / topeVisitas) * 100}%`,
                right: `${100 - ((vMax ?? topeVisitas) / topeVisitas) * 100}%`,
              }}
            />
            <input
              type="range"
              min={0}
              max={topeVisitas}
              step={Math.max(1, Math.round(topeVisitas / 200))}
              value={vMin ?? 0}
              onChange={(e) => {
                const v = Number(e.target.value);
                setVMin(v === 0 ? null : v);
                if (vMax !== null && v > vMax) setVMax(v);
              }}
              className="pointer-events-none absolute inset-0 h-6 w-full appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow [&::-webkit-slider-thumb]:ring-2 [&::-webkit-slider-thumb]:ring-indigo-500"
              aria-label="Visitas mínimas"
            />
            <input
              type="range"
              min={0}
              max={topeVisitas}
              step={Math.max(1, Math.round(topeVisitas / 200))}
              value={vMax ?? topeVisitas}
              onChange={(e) => {
                const v = Number(e.target.value);
                setVMax(v >= topeVisitas ? null : v);
                if (vMin !== null && v < vMin) setVMin(v);
              }}
              className="pointer-events-none absolute inset-0 h-6 w-full appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow [&::-webkit-slider-thumb]:ring-2 [&::-webkit-slider-thumb]:ring-indigo-500"
              aria-label="Visitas máximas"
            />
          </div>
          {/* Los números, para poder ser exacto sin pelearse con el deslizador. */}
          <div className="mt-1 flex items-center gap-2">
            <input
              type="number"
              min={0}
              value={vMin ?? ""}
              onChange={(e) => setVMin(e.target.value === "" ? null : Number(e.target.value))}
              placeholder="mín"
              className="w-[92px] rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
            />
            <span className="text-xs text-slate-400">–</span>
            <input
              type="number"
              min={0}
              value={vMax ?? ""}
              onChange={(e) => setVMax(e.target.value === "" ? null : Number(e.target.value))}
              placeholder="máx"
              className="w-[92px] rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
            />
            {vMin !== null && vMin > 0 ? (
              <span className="text-[10px] text-slate-400">
                los SKUs sin medir quedan fuera
              </span>
            ) : null}
          </div>
        </div>

        {/* De cuándo son los datos. Antes había aquí una advertencia de "falta el
            navegador" que se contradecía sola: la página está llena de títulos,
            fotos y precios de la competencia. El navegador no hace falta para
            MOSTRAR —los datos ya están capturados— sino para REFRESCAR, y eso pasa
            una vez al mes desde una máquina con Chrome. Lo accionable es la fecha. */}
        {capturado || ventasHasta || visitasMedidas ? (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
            <Info size={14} className="mt-0.5 shrink-0 text-slate-400" />
            <div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <Frescura
                  etiqueta="Visitas"
                  verbo="medidas"
                  iso={visitasMedidas}
                  viejoEnDias={2}
                />
                <span className="text-slate-300">·</span>
                <Frescura
                  etiqueta="Ventas"
                  verbo="hasta"
                  iso={ventasHasta}
                  viejoEnDias={2}
                />
                <span className="text-slate-300">·</span>
                {/* La punta VIEJA, no la fresca. Con el máximo, el encabezado
                    decía "hace 14 días" mientras la categoría abierta era de
                    hace 19 — y en esos 19 días el top de ML se había renovado
                    entero. Cada bloque trae además la suya. */}
                <Frescura
                  etiqueta="Ranking"
                  verbo="capturado"
                  iso={capturadoDesde ?? capturado}
                  viejoEnDias={40}
                  sufijo={
                    capturadoDesde &&
                    capturado &&
                    diaYMes(capturadoDesde) !== diaYMes(capturado)
                      ? "el más viejo"
                      : null
                  }
                />
              </div>
              {!puedeRefrescar ? (
                <div className="mt-1">
                  El ranking no se puede refrescar desde aquí: falta la API key de
                  Apify. Se obtiene raspando —la API de ML responde 403 en
                  publicaciones ajenas— y eso corre en la infraestructura de Apify,
                  no en este servidor.
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
            {error}
          </div>
        ) : null}

        {avisos.length > 0 ? (
          <details className="mb-4 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
            <summary className="cursor-pointer font-medium text-slate-700">
              {avisos.length} avisos de la última captura
            </summary>
            <div className="mt-2 space-y-1">
              {avisos.map((a, i) => (
                <div key={i} className="flex items-start gap-1.5">
                  <Info size={13} className="mt-0.5 shrink-0 text-slate-400" />
                  {a}
                </div>
              ))}
            </div>
          </details>
        ) : null}

        {cargando ? (
          <div className="flex items-center gap-2 py-12 text-sm text-slate-500">
            <Loader2 size={16} className="animate-spin" /> Cargando…
          </div>
        ) : raicesFiltradas.length === 0 ? (
          <div className="py-12 text-center text-sm text-slate-400">
            {raices.length === 0
              ? "No hay SKUs vigilados en este canal."
              : "Ningún SKU coincide con el filtro."}
          </div>
        ) : (
          raicesFiltradas.map((r) => (
            <BloqueRaiz
              key={r.raiz_id ?? r.raiz_nombre}
              r={r}
              abiertoSku={abiertoSku}
              detalle={detalle}
              cargandoDetalle={cargandoDetalle}
              onAbrirSku={abrirSku}
              onRefrescado={() => cargar()}
              filtrando={hayFiltro}
            />
          ))
        )}
      </main>

      {comoAbierto && (
        <ComoLeerCompetenciaModal onCerrar={() => setComoAbierto(false)} />
      )}
    </>
  );
}
