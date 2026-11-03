"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Search, RotateCw, ImageIcon, Wand2, ChevronRight, Pencil, Layers, Loader2, Warehouse, Split, PowerOff, ArrowDownUp, FilePen, Archive } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import ChannelDots from "@/components/ChannelDots";
import ProductStudio from "@/components/ProductStudio";
import CostoEditor from "@/components/CostoEditor";
import DesactivarPorCanal from "@/components/DesactivarPorCanal";
import FiltroCategoria from "@/components/FiltroCategoria";
import { ChipModo, esPadre, TipoBadge, VariantesBoton, VariantesTabla } from "@/components/Variantes";
import { TituloMoneda } from "@/components/Moneda";

import {
  ApiError, configEstudio, leerModosPublicacion, listarCanales, listarCategorias, listarProductos,
  type CategoriaWC,
} from "@/lib/api";
import type { CanalInfo, EstudioConfig, ModoPublicacion, Paginacion, Producto, VarianteResumen } from "@/lib/types";

const PER_PAGE = 40;
const INDIGO = "#4F46E5";

/**
 * Cómo se lista el catálogo. «Por padre» = la familia en una fila. «Por
 * variante» = cada hija es su propia fila. Antes «Por variante» era la ÚNICA
 * forma de ver una variante ya procesada cuyo padre seguía en borrador; desde
 * el 2-nov-2026 el Publicador lista también los borradores (Eduardo: el estado
 * draft se salta y Crear Productos se apaga), así que las dos formas enseñan el
 * catálogo entero. La pantalla manda `aplanar` siempre: sin él decidía
 * LISTADO_APLANADO, apagado en Railway (ROP-0246 y 13 familias más, 30-sep).
 */
type ModoLista = "padre" | "variante";
const LS_MODO = "omnicanal:publicador:modo";

function leerModoGuardado(): ModoLista {
  try {
    return localStorage.getItem(LS_MODO) === "variante" ? "variante" : "padre";
  } catch {
    return "padre"; // ventana privada / almacenamiento bloqueado
  }
}

/**
 * Orden de la lista (2-nov-2026). Con los ~3,700 borradores nacidos de Odoo
 * dentro, «más reciente primero» —el orden de siempre— llenaba las primeras
 * páginas de altas que nadie ha trabajado. Por omisión va primero lo que tiene
 * stock: es lo que se puede vender hoy. Los valores son los que ya entiende
 * `GET /api/productos?orden=`.
 */
type OrdenLista = "stock_desc" | "reciente" | "precio_desc" | "precio_asc" | "stock_asc";
const ORDEN_POR_OMISION: OrdenLista = "stock_desc";
const LS_ORDEN = "omnicanal:publicador:orden";
const ORDENES: { valor: OrdenLista; texto: string }[] = [
  { valor: "stock_desc", texto: "Con stock primero" },
  { valor: "reciente", texto: "Más recientes" },
  { valor: "precio_desc", texto: "Precio: mayor a menor" },
  { valor: "precio_asc", texto: "Precio: menor a mayor" },
  { valor: "stock_asc", texto: "Menos stock primero" },
];

function leerOrdenGuardado(): OrdenLista {
  try {
    const v = localStorage.getItem(LS_ORDEN);
    return ORDENES.some((o) => o.valor === v) ? (v as OrdenLista) : ORDEN_POR_OMISION;
  } catch {
    return ORDEN_POR_OMISION;
  }
}

/**
 * SEMÁFORO DE COMPLETITUD (2-nov-2026). Lo que Crear decía al terminar
 * («falta: categoría, precio…») ya no lo dice nadie, y los borradores llegan
 * aquí solo con su identidad. El backend juzga cada fila con lo que ya trae
 * (`woocommerce.faltantes_para_publicar`): `[]` = datos completos;
 * `null`/ausente = no se pudo juzgar, y entonces no se pinta nada (ausente no
 * es «listo»).
 *
 * UNA FAMILIA no se juzga con las metas del padre (el padre no se publica en
 * ningún canal): trae `faltantes: null` y `variantes_listas` = {listas, total},
 * y cada variante sus propios `faltantes`. El chip de la fila es ese resumen.
 *
 * «Listo para publicar» solo donde todavía no hay ningún canal (`publicado` =
 * presencia en al menos uno, routers/productos.py); con canales el mismo
 * semáforo dice «Datos completos»: no hay nada que publicar por primera vez.
 *
 * AVISOS (`avisos_publicar`, 2-nov-2026): lo que NO falta pero hay que revisar.
 * Hoy solo `medidas_relleno`: 1 kg y 30×20×15 cm, el relleno que se inventaba
 * cuando faltaban (`publicar_ready.medidas_de_relleno_ml`). Pasa el candado de
 * Mercado Libre y la vista previa lo avisa sin frenar. Sin faltantes, el chip
 * es «¿Medidas reales?» con contorno ámbar y sin relleno (`CHIP_AVISO`: no se
 * confunde con el ámbar sólido de «Falta: …», que sí frena) en lugar del verde
 * o el gris; con faltantes, manda el de faltantes y el aviso va en su `title`.
 * Ya publicado, la ayuda cambia: corregir en Costos NO toca la publicación viva
 * de Mercado Libre (actualizar no manda peso ni medidas). En una familia, por
 * variante, y `relleno_del_padre` dice si el relleno sale del padre.
 *
 * Estos campos todavía no están en el tipo compartido `Producto`: se declaran aquí.
 */
type VarianteConSemaforo = VarianteResumen & {
  faltantes?: string[] | null;
  avisos_publicar?: string[] | null;
};
type ProductoPublicador = Producto & {
  faltantes?: string[] | null;
  avisos_publicar?: string[] | null;
  variantes_listas?: {
    listas: number;
    total: number;
    con_relleno?: number;
    relleno_del_padre?: boolean;
  } | null;
  categoria_kubera?: boolean;
};

// La misma frase que la vista previa de ML (`publicar_ready.MSG_MEDIDAS_RELLENO_ML`).
const AYUDA_RELLENO =
  "Mercado Envíos cobrará el envío con 1 kg y 30×20×15 cm: son el peso y las medidas de relleno que se usaban cuando faltaban. Si no son las reales, captúralas en Costos del Estudio (kg y cm) antes de publicar.";
// Ya publicado: actualizar una publicación viva de Mercado Libre solo manda
// título, atributos y descripción (`publicar._update_ml_una`), nunca el paquete.
const AYUDA_RELLENO_PUBLICADO =
  "Si ya está en Mercado Libre, corregir aquí no cambia esa publicación: Mercado Envíos puede seguir cobrando el envío con 1 kg y 30×20×15 cm, el peso y las medidas de relleno que se usaban cuando faltaban. Esa se corrige en Mercado Libre. Capturar las reales en Costos del Estudio (kg y cm) sirve para las próximas altas.";
const ayudaRelleno = (publicado: boolean) => (publicado ? AYUDA_RELLENO_PUBLICADO : AYUDA_RELLENO);
const conRelleno = (avisos: string[] | null | undefined) =>
  Array.isArray(avisos) && avisos.includes("medidas_relleno");

const BORRADORES = new Set(["draft", "inprogress"]);
const esBorrador = (estado: string | null | undefined) => !!estado && BORRADORES.has(estado);

const NOMBRE_FALTANTE: Record<string, string> = {
  categoria_ml: "categoría ML",
  precio: "precio",
  stock: "stock",
  fotos: "fotos",
  medidas: "medidas",
};

/** Ayuda de cada faltante. La de categoría depende de si hay una sugerida. */
function ayudaFaltante(f: string, conSugerida: boolean): string {
  switch (f) {
    case "categoria_ml":
      return conSugerida
        ? "Sin categoría de Mercado Libre guardada en el producto. Hay una sugerida por kubera (la ruta gris): confírmala con «Usar esta» en la vista previa de Mercado Libre, o elígela en el selector de categoría del Estudio."
        : "Sin categoría de Mercado Libre guardada en el producto: elígela en el selector de categoría del Estudio. (Al publicar, la vista previa a veces propone una por la categoría de la tienda; aquí solo cuenta la guardada.)";
    case "precio":
      return "Sin precio regular ni de oferta mayor a 0.";
    case "stock":
      return "Sin existencias (stock menor a 1).";
    case "fotos":
      return "Sin ninguna foto.";
    case "medidas":
      return "Sin peso y las tres medidas reales (o si no cuadran entre sí) Mercado Libre NO da de alta. Se capturan en Costos del Estudio (kg y cm).";
    default:
      return f;
  }
}

// El chip se encoge y se recorta (el detalle completo va en el `title`): en una
// ventana angosta la columna del título mide ~160–240 px y un chip rígido se
// pintaba encima de Tipo y Variantes.
const CHIP_BASE =
  "inline-block min-w-0 max-w-full truncate rounded px-1.5 py-0.5 text-[10px] font-bold ring-1";
const CHIP_VERDE = `${CHIP_BASE} bg-emerald-50 text-emerald-700 ring-emerald-200`;
const CHIP_AMBAR = `${CHIP_BASE} bg-amber-50 text-amber-700 ring-amber-200`;
const CHIP_GRIS = `${CHIP_BASE} bg-slate-50 text-slate-500 ring-slate-200`;
// Aviso que NO frena (medidas de relleno): contorno ámbar sin relleno, para no
// leerse como el ámbar sólido de «Falta: …». La etiqueta empieza por «¿», que
// sobrevive al recorte.
const CHIP_AVISO = `${CHIP_BASE} bg-white text-amber-700 ring-amber-300`;
const ETIQUETA_RELLENO = "¿Medidas reales?";

function ChipCompletitud({ p }: { p: ProductoPublicador }) {
  // FAMILIA: el resumen de sus variantes.
  if (p.variantes_listas) {
    const { listas, total } = p.variantes_listas;
    const variantes = p.variantes as VarianteConSemaforo[];
    const recorta = (xs: string[]) => (xs.length > 8 ? [...xs.slice(0, 8), `… y ${xs.length - 8} más`] : xs);
    const pendientes = variantes
      .filter((v) => Array.isArray(v.faltantes) && v.faltantes.length > 0)
      .map((v) => `${v.sku}: ${v.faltantes!.map((f) => NOMBRE_FALTANTE[f] ?? f).join(", ")}`);
    const rellenas = variantes.filter((v) => conRelleno(v.avisos_publicar)).map((v) => v.sku);
    const nRelleno = p.variantes_listas.con_relleno ?? rellenas.length;
    const todasListas = listas === total && total > 0;
    const delPadre = !!p.variantes_listas.relleno_del_padre;
    const title = [
      "Una familia se publica por variante: este semáforo resume el de sus variantes (cada una con lo que hereda del padre).",
      ...(todasListas && nRelleno > 0 ? ["", `${listas} de ${total} ${total === 1 ? "variante lista" : "variantes listas"}.`] : []),
      ...(pendientes.length ? ["", "Les falta:", ...recorta(pendientes)] : []),
      ...(rellenas.length
        ? [
            "",
            "Con 1 kg y 30×20×15 cm (el relleno de cuando faltaban):",
            ...recorta(rellenas),
            ...(delPadre
              ? [`El padre (${p.sku}) tiene guardado ese mismo relleno y las variantes sin peso ni medidas propias lo toman de él: corregirlo en el padre las corrige a todas; si no, captura las de cada variante.`]
              : []),
            ayudaRelleno(!!p.publicado),
          ]
        : []),
    ].join("\n");
    // Todas listas pero alguna con el relleno: no es verde (Mercado Envíos cobraría
    // con el relleno si no son sus medidas). Con variantes a medias manda el conteo.
    if (todasListas && nRelleno > 0) {
      return (
        <span title={title} className={CHIP_AVISO}>
          {ETIQUETA_RELLENO}
        </span>
      );
    }
    return (
      <span title={title} className={todasListas ? CHIP_VERDE : CHIP_AMBAR}>
        {listas} de {total} {total === 1 ? "variante lista" : "variantes listas"}
      </span>
    );
  }
  const faltantes = p.faltantes;
  if (faltantes === null || faltantes === undefined) return null;
  const relleno = conRelleno(p.avisos_publicar);
  if (faltantes.length === 0 && relleno) {
    // Datos completos pero con el relleno: aviso, no verde ni gris. Ya publicado,
    // la ayuda dice que corregir aquí no cambia lo que está en Mercado Libre.
    return (
      <span title={ayudaRelleno(!!p.publicado)} className={CHIP_AVISO}>
        {ETIQUETA_RELLENO}
      </span>
    );
  }
  if (faltantes.length === 0) {
    return p.publicado ? (
      <span
        title="Ya está en al menos un canal y tiene categoría ML, precio, stock, fotos y medidas."
        className={CHIP_GRIS}
      >
        Datos completos
      </span>
    ) : (
      <span
        title="Todavía no está en ningún canal y tiene categoría ML, precio, stock, fotos y medidas. Cada canal valida lo suyo al publicar."
        className={CHIP_VERDE}
      >
        Listo para publicar
      </span>
    );
  }
  const conSugerida = !!p.categoria_kubera && p.categoria_path.length > 0;
  return (
    <span
      title={[
        ...faltantes.map((f) => ayudaFaltante(f, conSugerida)),
        ...(relleno ? ["", `Además: ${ayudaRelleno(!!p.publicado)}`] : []),
      ].join("\n")}
      className={CHIP_AMBAR}
    >
      {faltantes.length > 2
        ? `Faltan ${faltantes.length}: ${faltantes.map((f) => NOMBRE_FALTANTE[f] ?? f).join(" · ")}`
        : `Falta: ${faltantes.map((f) => NOMBRE_FALTANTE[f] ?? f).join(" · ")}`}
    </span>
  );
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
  }).format(v);
}

export default function ProductosPage() {
  const [canales, setCanales] = useState<CanalInfo[]>([]);
  const [productos, setProductos] = useState<ProductoPublicador[]>([]);
  const [pag, setPag] = useState<Paginacion>({
    page: 1, per_page: PER_PAGE, total: 0, total_pages: 1,
    tiene_anterior: false, tiene_siguiente: false,
  });
  const [page, setPage] = useState(1);
  const [busquedaInput, setBusquedaInput] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [skusInput, setSkusInput] = useState("");
  const [skusFiltro, setSkusFiltro] = useState("");
  // "Solo DROP OFF": los SKUs con existencias en el almacén DROP OFF de Odoo.
  // Mismo filtro que en Omnicanal (v0.456.0) y por la misma razón: el almacén
  // es del SKU, no de la publicación, así que la pregunta vale en las dos
  // pantallas. Lo resuelve el backend con UNA consulta a Odoo, cacheada.
  const [dropOff, setDropOff] = useState(false);
  // LA BAJA DEL CATÁLOGO (0080, diseño de Eduardo del 2-nov). Por omisión el
  // backend quita de la página lo archivado; este chip muestra SOLO lo archivado
  // que sigue publicado en algún canal: lo que hay que bajar o desarchivar.
  const [archivadosVivos, setArchivadosVivos] = useState(false);
  const [archivadosOcultos, setArchivadosOcultos] = useState(0);
  // Lo que contestó el servidor cuando el chip no se pudo resolver (p. ej. 503
  // antes de la migración 0080): se muestra, y la lista queda VACÍA. Un chip
  // encendido sobre el catálogo normal diría algo falso.
  const [errorArchivados, setErrorArchivados] = useState<string | null>(null);
  // Categoría de WooCommerce (id). El mismo filtro que Omnicanal, y como allá
  // se acumula con la búsqueda, "Filtrar SKUs" y DROP OFF: el backend lo
  // resuelve en la misma consulta, así que el total y las páginas son del filtro.
  const [categoria, setCategoria] = useState<number | null>(null);
  const [categorias, setCategorias] = useState<CategoriaWC[]>([]);
  // `null` hasta leer localStorage en el cliente (mismo patrón que Crear): así
  // no se pide la lista dos veces ni se pinta un instante el modo equivocado.
  const [modo, setModo] = useState<ModoLista | null>(null);
  useEffect(() => setModo(leerModoGuardado()), []);
  // Mismo patrón que el modo: `null` hasta leer localStorage, para no pedir la
  // lista dos veces (una por omisión y otra con lo guardado).
  const [orden, setOrden] = useState<OrdenLista | null>(null);
  // «Solo borradores» (2-nov-2026): la cola de trabajo que daba Crear Productos
  // (draft + inprogress, `estados=borrador` en el backend) ahora que los
  // borradores viven revueltos con lo publicado. `/crear` manda aquí con
  // `?borradores=1`. Se lee en el MISMO efecto que el orden para que la primera
  // petición ya lleve el filtro (las dos actualizaciones se agrupan).
  const [soloBorradores, setSoloBorradores] = useState(false);
  useEffect(() => {
    try {
      if (new URLSearchParams(window.location.search).get("borradores") === "1") {
        setSoloBorradores(true);
      }
    } catch { /* sin filtro */ }
    setOrden(leerOrdenGuardado());
  }, []);
  const [cargando, setCargando] = useState(true);
  // Arranque en frío del backend: el índice de WooCommerce puede tardar varios
  // segundos en construirse. Mientras tanto, "0 resultados" no significa que
  // no haya productos — reintentamos en silencio en vez de mostrar vacío.
  const [preparando, setPreparando] = useState(false);
  const reintentos = useRef(0);
  // true hasta que la PRIMERA carga de esta página termine (con o sin filtro).
  // Evita que se vea "No se encontraron productos" en el instante antes de que
  // llegue la respuesta real, incluso si por alguna razón cargando ya es false.
  const primeraCarga = useRef(true);
  const [sel, setSel] = useState<Producto | null>(null);
  // Variante que abre seleccionada en el rail del Estudio (clic en un renglón
  // de la tabla desplegada). null = abre en el padre / la primera.
  const [varianteSel, setVarianteSel] = useState<string | null>(null);
  // Config del Estudio. Mientras no llegue, el Estudio se comporta como el de
  // siempre: `studio_variantes` ausente = cajón derecho, sin rail.
  const [estudioConfig, setEstudioConfig] = useState<EstudioConfig | null>(null);
  // { sku_padre: {canal: modo} } — sólo las familias con modo GUARDADO.
  const [modos, setModos] = useState<Record<string, Record<string, ModoPublicacion>>>({});
  const [editCosto, setEditCosto] = useState<string | null>(null);
  // «Publicaciones por canal»: escoger un canal, seleccionar un lote o todas y
  // sacarlas de la venta (13-oct-2026). Vive encima de la página, como el Estudio.
  const [lotePorCanal, setLotePorCanal] = useState(false);
  // Padres con su lista de variantes desplegada (misma mecánica que Crear Productos)
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());

  function toggleExpandido(sku: string) {
    setExpandidos((prev) => {
      const s = new Set(prev);
      if (s.has(sku)) s.delete(sku);
      else s.add(sku);
      return s;
    });
  }

  const topRef = useRef<HTMLDivElement>(null);

  const colorMap = Object.fromEntries(canales.map((c) => [c.id, c.color]));
  const labelMap = Object.fromEntries(canales.map((c) => [c.id, c.label]));

  useEffect(() => {
    listarCanales().then(setCanales).catch(() => setCanales([]));
    // Falla en silencio: sin config, el Estudio es el de siempre.
    configEstudio().then(setEstudioConfig).catch(() => setEstudioConfig(null));
    // TODAS las categorías con productos en esta vista (1,224, por SQL), no
    // las 300 con más publicados que recibe Omnicanal. Por nombre, que es como
    // se buscan. Sin categorías (falla) el filtro no se pinta.
    listarCategorias(undefined, "productos")
      .then((cs) => setCategorias([...cs].sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))))
      .catch(() => setCategorias([]));
  }, []);

  // El modo de las familias VISIBLES, en una sola llamada por página.
  useEffect(() => {
    const padres = productos.filter(esPadre).map((p) => p.sku);
    if (!padres.length) { setModos({}); return; }
    let vivo = true;
    leerModosPublicacion(padres)
      .then((r) => { if (vivo) setModos(r.modo ?? {}); })
      .catch(() => { /* sin chip */ });
    return () => { vivo = false; };
  }, [productos]);

  useEffect(() => {
    const t = setTimeout(() => {
      setBusqueda(busquedaInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [busquedaInput]);

  useEffect(() => {
    const t = setTimeout(() => {
      setSkusFiltro(skusInput.trim());
      setPage(1);
    }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    // Aún no se sabe el modo o el orden (se leen de localStorage al montar).
    if (modo === null || orden === null) return () => ctrl.abort();
    setCargando(true);
    listarProductos(
      {
        canal: "general", page, perPage: PER_PAGE,
        search: busqueda || undefined, skus: skusFiltro || undefined,
        dropOff: dropOff || undefined,
        archivados: archivadosVivos ? "vivos" : undefined,
        categoria,
        estados: soloBorradores ? ["borrador"] : undefined,
        // El Publicador: lo publicado, pendiente o listo Y los borradores
        // (draft / inprogress) desde el 2-nov-2026 — lo que falta trabajar se
        // trabaja aquí; qué le falta a cada uno lo dice el semáforo de la fila.
        vista: "productos",
        orden,
        // SIEMPRE explícito: sin valor el backend decide por LISTADO_APLANADO y
        // la pantalla dejaría de mostrar lo que dice el control.
        aplanar: modo === "variante",
      },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
        setArchivadosOcultos(r.archivados_ocultos ?? 0);
        setErrorArchivados(null);
        // Sin búsqueda/filtro y 0 resultados → probablemente el índice de
        // WooCommerce todavía se está construyendo (arranque en frío). Reintenta
        // en vez de mostrar "no encontrados".
        if (!busqueda && !skusFiltro && !categoria && !soloBorradores && !archivadosVivos && r.paginacion.total === 0 && reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          setTimeout(() => cargar(), 1000);
          return;
        }
        reintentos.current = 0;
        setPreparando(false);
        primeraCarga.current = false;
      })
      .catch((exc) => {
        // El backend puede tardar en levantarse (deploy/reinicio) y rechazar la
        // conexión: reintentamos igual que con 0 resultados, en vez de dejar la
        // pantalla en "no encontrados" por un error de red silencioso.
        if (exc?.name === "AbortError") return;
        // El servidor SÍ contestó, con un error, a «Archivados vivos»: no es un
        // backend levantándose. Reintentar dejaría el catálogo normal bajo el chip.
        if (archivadosVivos && exc instanceof ApiError) {
          setProductos([]);
          setPag({ page: 1, per_page: PER_PAGE, total: 0, total_pages: 0,
                   tiene_anterior: false, tiene_siguiente: false });
          setErrorArchivados(exc.detail || exc.message);
          reintentos.current = 0;
          setPreparando(false);
          primeraCarga.current = false;
          return;
        }
        if (reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          setTimeout(() => cargar(), 1000);
        } else {
          primeraCarga.current = false;
        }
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda, skusFiltro, dropOff, archivadosVivos, categoria, modo, orden, soloBorradores]);

  useEffect(() => cargar(), [cargar]);

  function cambiarModo(nuevo: ModoLista) {
    if (nuevo === modo) return;
    try { localStorage.setItem(LS_MODO, nuevo); } catch { /* sin recordar */ }
    setExpandidos(new Set());
    setPage(1);
    setModo(nuevo);
  }

  function cambiarOrden(nuevo: OrdenLista) {
    if (nuevo === orden) return;
    try { localStorage.setItem(LS_ORDEN, nuevo); } catch { /* sin recordar */ }
    setPage(1);
    setOrden(nuevo);
  }

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />

      <main className="mx-auto max-w-[1400px] px-4 pb-16 pt-6 sm:px-6">
        {/* Encabezado */}
        <div
          ref={topRef}
          className="relative overflow-hidden rounded-3xl p-6 text-white shadow-card"
          style={{ background: `linear-gradient(120deg, ${INDIGO} 0%, #7C6CF0 100%)` }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                <Wand2 size={14} /> Estudio de producto
              </div>
              <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Publicador</h1>
              <p className="mt-1 max-w-xl text-sm opacity-90">
                Tu catálogo de WooCommerce, borradores incluidos. Haz clic en un producto para ver su
                ficha completa, completar lo que le falte y generar contenido optimizado por canal con IA.
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {new Intl.NumberFormat("es-MX").format(pag.total)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                productos
              </div>
            </div>
          </div>
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/20" />
        </div>

        {/* Buscador */}
        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          {/* PUBLICACIONES POR CANAL — escoger un canal (las pestañas de
              Omnicanal), seleccionar un lote o todas y sacarlas de la venta. Va a
              la izquierda y en rojo: no es un filtro de esta lista, es una acción
              sobre lo que ya está publicado. */}
          <button
            type="button"
            onClick={() => setLotePorCanal(true)}
            title="Ver los SKUs publicados de un canal, seleccionar un lote o todos y desactivarlos (se inactivan, no se borran)"
            className="mr-auto inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition-colors hover:bg-rose-50"
          >
            <PowerOff size={15} className="shrink-0" />
            Desactivar por canal
          </button>
          {/* Por padre / Por variante. */}
          <div
            role="group"
            aria-label="Cómo listar los productos"
            className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5"
          >
            {([
              { valor: "padre", texto: "Por padre", Icono: Layers,
                ayuda: "Una fila por familia, con sus variantes adentro. Incluye los borradores. El semáforo de una familia resume el de sus variantes («3 de 5 variantes listas»): se publica por variante" },
              { valor: "variante", texto: "Por variante", Icono: Split,
                ayuda: "Una fila por variante, cada una con su SKU, su precio y su stock. Incluye las de padres en borrador" },
            ] as const).map(({ valor, texto, Icono, ayuda }) => {
              const activo = modo === valor;
              return (
                <button
                  key={valor}
                  type="button"
                  onClick={() => cambiarModo(valor)}
                  aria-pressed={activo}
                  title={ayuda}
                  className={[
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-semibold transition-colors",
                    activo ? "text-white shadow-sm" : "text-slate-500 hover:bg-slate-50 hover:text-slate-700",
                  ].join(" ")}
                  style={activo ? { backgroundColor: INDIGO } : undefined}
                >
                  <Icono size={15} />
                  {texto}
                </button>
              );
            })}
          </div>
          {/* ORDEN. Por omisión, lo que tiene stock primero: con los borradores
              dentro, el orden por fecha enterraba lo ya trabajado. */}
          <label className="relative">
            <span className="sr-only">Ordenar la lista</span>
            <ArrowDownUp size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <select
              value={orden ?? ORDEN_POR_OMISION}
              onChange={(e) => cambiarOrden(e.target.value as OrdenLista)}
              title="Orden de la lista. A igual valor, va primero lo más reciente"
              className="appearance-none rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm font-medium text-slate-600 outline-none focus:ring-2 focus:ring-indigo-300"
            >
              {ORDENES.map((o) => (
                <option key={o.valor} value={o.valor}>{o.texto}</option>
              ))}
            </select>
          </label>
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={busquedaInput}
              onChange={(e) => setBusquedaInput(e.target.value)}
              placeholder="SKU o nombre…"
              className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          <div className="relative">
            <Layers size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={skusInput}
              onChange={(e) => setSkusInput(e.target.value)}
              placeholder="Filtrar SKUs: TEC-0001, ORG-0885, caminadora…"
              title="Términos separados por coma: filtra y busca a la vez (SKU completo, parcial o palabra del nombre)"
              className="w-80 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 font-mono text-xs text-slate-700 outline-none transition-shadow placeholder:font-sans placeholder:text-sm placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          {/* CATEGORÍA de WooCommerce, con sus subcategorías (como Woo).
              Buscable: son 1,224. Sin conteo junto al nombre a propósito: el
              total de arriba ya es el del filtro. */}
          {categorias.length > 0 && (
            <FiltroCategoria
              categorias={categorias}
              valor={categoria}
              onCambio={(id) => { setCategoria(id); setPage(1); }}
            />
          )}
          {/* SOLO DROP OFF — el almacén del que salen los envíos a los
              marketplaces chinos. Mismo chip, mismo violeta y mismo distintivo
              que en Omnicanal, para que signifiquen lo mismo en las dos
              pantallas. Se acumula con la búsqueda y con "Filtrar SKUs". */}
          <button
            onClick={() => { setDropOff(!dropOff); setPage(1); }}
            title={dropOff
              ? "Mostrando SOLO los productos con existencias en el almacén DROP OFF de Odoo."
              : "Filtrar a los productos con existencias en DROP OFF — el almacén del que salen los envíos a marketplaces chinos."}
            className={[
              "inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium",
              dropOff
                ? "border-violet-300 bg-violet-50 text-violet-700"
                : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
            ].join(" ")}
          >
            <Warehouse size={15} className="shrink-0" />
            Solo DROP OFF
          </button>
          {/* SOLO BORRADORES — la cola de trabajo que antes vivía en Crear
              Productos. Se acumula con todo lo demás; el semáforo de cada fila
              dice qué le falta. */}
          <button
            onClick={() => { setSoloBorradores(!soloBorradores); setPage(1); }}
            title={soloBorradores
              ? "Mostrando SOLO los borradores (draft / en proceso en WooCommerce): lo que antes se trabajaba en Crear Productos."
              : "Filtrar a los borradores (draft / en proceso): lo que antes se trabajaba en Crear Productos."}
            className={[
              "inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium",
              soloBorradores
                ? "border-amber-300 bg-amber-50 text-amber-700"
                : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
            ].join(" ")}
          >
            <FilePen size={15} className="shrink-0" />
            Solo borradores
          </button>
          {/* ARCHIVADOS VIVOS — dados de baja en kubera y todavía publicados
              (activa o pausada). Sin el chip, lo archivado no aparece aquí. */}
          <button
            onClick={() => { setArchivadosVivos(!archivadosVivos); setErrorArchivados(null); setPage(1); }}
            title={archivadosVivos
              ? "Mostrando SOLO lo archivado que sigue publicado en algún canal: hay que bajarlo del canal o desarchivarlo."
              : "Lo archivado no aparece en el Publicador. Este filtro muestra lo archivado que todavía está publicado (activa o pausada)."}
            className={[
              "inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-medium",
              archivadosVivos
                ? "border-rose-300 bg-rose-50 text-rose-700"
                : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
            ].join(" ")}
          >
            <Archive size={15} className="shrink-0" />
            Archivados vivos
          </button>
          {archivadosVivos && errorArchivados && (
            <span role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700">
              No se pudo leer lo archivado: {errorArchivados}
            </span>
          )}
          {archivadosOcultos > 0 && !archivadosVivos && (
            <span className="text-xs text-slate-500" title="Dados de baja en kubera (archivado). El total de la paginación todavía los cuenta.">
              {archivadosOcultos} archivado{archivadosOcultos === 1 ? "" : "s"} oculto{archivadosOcultos === 1 ? "" : "s"} en esta página
            </span>
          )}
          <button
            onClick={cargar}
            title="Recargar"
            className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-50"
          >
            <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Paginación superior */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={INDIGO} textoColor="#fff" onPage={irPagina} />
        </div>

        {/* Lista */}
        <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
          {/* Encabezado de columnas (alineado con las filas de abajo) */}
          <div className="hidden items-center gap-4 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500 md:flex">
            <span className="min-w-0 flex-1">Producto</span>
            <span className="w-20 shrink-0 text-center">Tipo</span>
            <span className="w-36 shrink-0 text-center">Variantes</span>
            <span className="flex shrink-0 items-center gap-4">
              <span className="hidden w-16 text-center sm:block">Canales</span>
              {/* Las tres columnas de dinero son PESOS. Se declara aquí porque
                  en el Estudio, a un clic de distancia, hay campos en dólares
                  con el mismo aspecto (Eduardo, 6-ago). */}
              <span className="hidden w-20 justify-end text-right sm:flex">
                <TituloMoneda moneda="MXN">Costo</TituloMoneda>
              </span>
              <span className="hidden w-24 justify-end text-right sm:flex">
                <TituloMoneda moneda="MXN">P. Regular</TituloMoneda>
              </span>
              <span className="flex w-24 justify-end text-right">
                <TituloMoneda moneda="MXN">P. Oferta</TituloMoneda>
              </span>
            </span>
            <span className="w-8 shrink-0" />
          </div>

          {cargando || (productos.length === 0 && primeraCarga.current) ? (
            Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="flex animate-pulse items-center gap-4 border-b border-slate-100 px-4 py-4">
                <div className="h-16 w-16 rounded-lg bg-slate-100" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-1/2 rounded bg-slate-100" />
                  <div className="h-3 w-3/4 rounded bg-slate-100" />
                </div>
                <div className="h-5 w-20 rounded bg-slate-100" />
              </div>
            ))
          ) : productos.length === 0 ? (
            <div className="px-4 py-16 text-center text-sm text-slate-400">
              {preparando ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 size={16} className="animate-spin" />
                  Preparando el catálogo de WooCommerce…
                </span>
              ) : (
                "No se encontraron productos."
              )}
            </div>
          ) : (
            productos.map((p) => {
              const padre = esPadre(p);
              const abierto = padre && expandidos.has(p.sku);
              return (
              <div key={p.sku} className="border-b border-slate-100">
                <div className="flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-indigo-50/40">
                  {/* Zona clickeable: abre el Estudio */}
                  <button
                    onClick={() => setSel(p)}
                    className="flex min-w-0 flex-1 items-center gap-4 text-left"
                  >
                    {/* Imagen */}
                    <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
                      {p.imagen ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.imagen} alt="" loading="lazy" className="h-full w-full object-contain" />
                      ) : (
                        <ImageIcon size={22} className="text-slate-300" />
                      )}
                    </div>

                    {/* Título + descripción + categoría */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-semibold text-slate-800">{p.nombre}</span>
                        <span className="shrink-0 rounded bg-slate-100 px-1.5 font-mono text-[10px] text-slate-400">
                          {p.sku}
                        </span>
                        {/* BORRADOR: el estado de WooCommerce, que la fila no
                            pintaba. Desde que Crear se apagó es lo que distingue
                            lo que falta trabajar de lo ya resuelto. */}
                        {esBorrador(p.estado) && (
                          <span
                            title={`Borrador en WooCommerce (${p.estado}): todavía no se trabajó. Antes vivía en Crear Productos.`}
                            className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
                          >
                            Borrador
                          </span>
                        )}
                        {/* DROP OFF: mismo distintivo, mismo violeta y mismo
                            significado que en Omnicanal y en Inventario. Se
                            pinta con el filtro puesto o sin el. */}
                        {p.drop_off && (
                          <span
                            title="Tiene existencias en el almacén DROP OFF de Odoo — el almacén del que salen los envíos a marketplaces chinos."
                            className="shrink-0 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700"
                          >
                            DROP OFF
                          </span>
                        )}
                      </div>
                      {p.descripcion_corta && (
                        <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{p.descripcion_corta}</p>
                      )}
                      {/* Semáforo + categoría en un renglón: el chip primero,
                          porque la ruta se trunca y el chip no debe perderse
                          (el chip también se recorta, con el detalle en su title).
                          Con «Falta: categoría ML» la ruta de kubera NO está
                          guardada en el producto: se pinta como SUGERIDA, no
                          como si ya la tuviera. */}
                      {(p.categoria_path.length > 0 || Array.isArray(p.faltantes) || !!p.variantes_listas) && (
                        <div className="mt-1 flex min-w-0 items-center gap-2 overflow-hidden">
                          <ChipCompletitud p={p} />
                          {p.categoria_path.length > 0 && (
                            <div className="flex min-w-0 items-center gap-1 overflow-hidden text-[11px] text-slate-400">
                              {p.categoria_kubera && p.faltantes?.includes("categoria_ml") && (
                                <span
                                  title="La categoría de Mercado Libre que kubera tiene curada para este SKU. No está guardada en el producto: confírmala con «Usar esta» en la vista previa de Mercado Libre o elígela en el Estudio."
                                  className="shrink-0 whitespace-nowrap font-semibold italic text-slate-500"
                                >
                                  Sugerida por kubera:
                                </span>
                              )}
                              {p.categoria_path.map((c, i) => (
                                <span key={i} className="flex shrink-0 items-center gap-1 whitespace-nowrap">
                                  {i > 0 && <ChevronRight size={10} />}
                                  {c.nombre}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </button>

                  {/* Columna TIPO */}
                  <div className="hidden w-20 shrink-0 justify-center md:flex">
                    <TipoBadge padre={padre} onClick={() => { setVarianteSel(null); setSel(p); }} />
                  </div>

                  {/* Columna VARIANTES */}
                  <div className="hidden w-36 shrink-0 justify-center md:flex">
                    {padre ? (
                      <span className="flex flex-col items-center">
                        <VariantesBoton
                          n={p.variantes.length}
                          abierto={abierto}
                          onClick={() => toggleExpandido(p.sku)}
                        />
                        {Object.entries(modos[p.sku] ?? {}).map(([c, m]) => (
                          <ChipModo key={c} modo={m} canalLabel={labelMap[c] ?? c} />
                        ))}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </div>

                  {/* Canales + precio (también abren el Estudio) */}
                  <button
                    onClick={() => setSel(p)}
                    className="flex shrink-0 items-center gap-4 text-left"
                  >
                    <div className="hidden w-16 shrink-0 justify-center sm:flex">
                      <ChannelDots canales={p.canales} colorMap={colorMap} labelMap={labelMap} />
                    </div>
                    <div className="hidden w-20 shrink-0 text-right sm:block">
                      <div className="text-sm font-semibold text-slate-600">{precioMXN(p.costo)}</div>
                    </div>
                    <div className="hidden w-24 shrink-0 text-right sm:block">
                      <div className="text-sm text-slate-500">{precioMXN(p.precio_base)}</div>
                    </div>
                    <div className="w-24 shrink-0 text-right">
                      <div className="font-bold text-slate-900">{precioMXN(p.precio_oferta ?? p.precio)}</div>
                      <div className="text-[11px] text-slate-400">{p.stock ?? "—"} en stock</div>
                    </div>
                  </button>

                  {/* Editar costo — icono de lápiz junto al precio (sin modal) */}
                  <button
                    onClick={() => setEditCosto((s) => (s === p.sku ? null : p.sku))}
                    title="Editar costo y precios"
                    className={[
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors",
                      editCosto === p.sku
                        ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                        : "border-slate-200 bg-white text-slate-400 hover:bg-slate-50 hover:text-indigo-600",
                    ].join(" ")}
                  >
                    <Pencil size={15} />
                  </button>
                </div>

                {/* Variantes del padre (desplegable) */}
                {abierto && (
                  <div className="bg-violet-50/40 px-4 pb-4 pt-1">
                    <VariantesTabla
                      variantes={p.variantes}
                      colorMap={colorMap}
                      labelMap={labelMap}
                      onVariante={(sku) => { setVarianteSel(sku); setSel(p); }}
                    />
                  </div>
                )}

                {/* Panel de costos que se despliega inline */}
                {editCosto === p.sku && (
                  <div className="bg-slate-50/60 px-4 pb-4 pt-1">
                    <CostoEditor sku={p.sku} nombre={p.nombre} onClose={() => setEditCosto(null)} onGuardado={cargar} />
                  </div>
                )}
              </div>
              );
            })
          )}
        </div>

        {/* Paginación inferior */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={INDIGO} textoColor="#fff" onPage={irPagina} />
        </div>
      </main>

      {/* Estudio de producto (overlay) */}
      {/* La llave cambia con el padre Y con la variante inicial: cada apertura
          monta un Estudio nuevo, así su primer render ya nace en la variante
          elegida en vez de en variantes[0] (que disparaba una carga de más cuya
          respuesta podía pisar la buena). */}
      <ProductStudio
        key={`${sel?.sku ?? ""}::${varianteSel ?? ""}`}
        sku={sel?.sku ?? null}
        producto={sel}
        canales={canales}
        estudioConfig={estudioConfig}
        varianteInicial={varianteSel}
        onClose={() => { setSel(null); setVarianteSel(null); }}
        onGuardado={cargar}
      />

      {lotePorCanal && (
        <DesactivarPorCanal canales={canales} onCerrar={() => setLotePorCanal(false)} />
      )}
    </div>
  );
}
