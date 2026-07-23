"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  X,
  ChevronRight,
  ImageIcon,
  ExternalLink,
  Wand2,
  Loader2,
  Link2,
  TrendingUp,
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
  Calculator,
  RefreshCw,
  Save,
  Plus,
  Trash2,
  Sparkles,
  Eraser,
  Languages,
  Stamp,
  UserRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import TipoAmazonPicker from "./TipoAmazonPicker";
import type {
  AtributoProducto,
  CanalInfo,
  CategoriaMLResult,
  CompetenciaResp,
  CostoCalculo,
  CostoOverrides,
  DetalleCanal,
  FlagsImagen,
  GaleriaImagen,
  ImagenProgreso,
  Producto,
  ProgresoImagenes,
  PublicarPreview,
  PublicarResultado,
  StudioMetadata,
} from "@/lib/types";
import {
  costoDetalle,
  costoGuardar,
  costoPreview,
  agregarImagenes,
  eliminarImagenGaleria,
  galeriaProducto,
  guardarCategoriaML,
  guardarContenido,
  mejorarIA,
  precioCompetencia,
  procesarImagenesIA,
  progresoImagenes,
  publicarConfirmar,
  publicarPreview,
  studioMetadata,
  type ProductoIA,
} from "@/lib/api";
import CategoriaMLPicker from "./CategoriaMLPicker";
import { useDetalleProducto } from "@/lib/useDetalleProducto";
import {
  getMejora,
  setMejora as saveMejora,
  getCompetencia as getCompStore,
  setCompetencia as saveCompetencia,
  limpiarBorrador,
} from "@/lib/studioStore";
import { THEME_FALLBACK, hexToRgba, variablesTema, type CanalTheme } from "@/lib/theme";

interface Props {
  sku: string | null;
  producto?: Producto | null;
  canales: CanalInfo[];
  onClose: () => void;
  // Se llama tras guardar costo/precios, para que la lista que abrió el Estudio
  // (Productos/Omnicanal) refresque y no quede con el snapshot viejo.
  onGuardado?: () => void;
}

const GENERAL = "general";
const AMAZON = "amazon";
const DEFAULT_TC = 18.5; // tipo de cambio USD→MXN por defecto (editable en el bloque COSTOS)

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(v);
}

interface Campos {
  precioRegular: string;
  precioOferta: string;
  costo: string;
  alibabaUrl: string;
  alibabaPrecio: string;
  peso: string;
  largo: string;
  ancho: string;
  alto: string;
}

const CAMPOS_VACIOS: Campos = {
  precioRegular: "", precioOferta: "", costo: "", alibabaUrl: "",
  alibabaPrecio: "", peso: "", largo: "", ancho: "", alto: "",
};

const str = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

// Flags de edición de imagen con IA (por imagen). Fondo=quitar fondo,
// Texto=traducir/quitar logos, Modelo=reemplazar persona por una latina.
const FLAGS_IMG: { key: keyof FlagsImagen; label: string; Icon: LucideIcon }[] = [
  { key: "quitar_fondo", label: "Fondo", Icon: Eraser },
  { key: "traducir_texto", label: "Traducir texto", Icon: Languages },
  { key: "quitar_logos", label: "Quitar logos", Icon: Stamp },
  { key: "cambiar_modelo", label: "Modelo", Icon: UserRound },
];

// Límite de caracteres del TÍTULO por canal (para no exceder al publicar).
// Mercado Libre: 60 (límite duro de ML). Amazon: 200 (límite duro de la API;
// el estándar de Kubera es ~75, se marca en rojo al pasar el tope).
const LIMITE_TITULO: Record<string, number> = {
  mercado_libre: 60,
  amazon: 200,
};

export default function ProductStudio({ sku, producto, canales, onClose, onGuardado }: Props) {
  const { data, cargando, recargar } = useDetalleProducto(sku, producto);
  const [canal, setCanal] = useState<string>(GENERAL);

  // Al abrir el Studio (edición), SIEMPRE recargar en vivo de Woo: el cache de
  // la lista puede estar viejo (ej. descripción recién actualizada). Así los
  // datos del modal quedan sincronizados con WooCommerce.
  useEffect(() => {
    if (sku) recargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  const [meta, setMeta] = useState<StudioMetadata | null>(null);

  // Campos de metadata NO tocados por la IA (editables a mano).
  const [campos, setCampos] = useState<Campos>(CAMPOS_VACIOS);
  const [imgActiva, setImgActiva] = useState(0);

  // ── Editor de imágenes (galería WooCommerce + IA por flags) ──────────
  const [galeria, setGaleria] = useState<GaleriaImagen[] | null>(null);
  const [flagsImg, setFlagsImg] = useState<Record<number, FlagsImagen>>({});
  const [progresoImg, setProgresoImg] = useState<Record<number, ImagenProgreso>>({});
  const [jobImg, setJobImg] = useState<ProgresoImagenes | null>(null);
  const [procesandoIA, setProcesandoIA] = useState(false);
  const [eliminandoId, setEliminandoId] = useState<number | null>(null);
  const [agregandoImg, setAgregandoImg] = useState(false);
  const [dragImg, setDragImg] = useState(false);
  const pollImgRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Guardar contenido (título/descripción/atributos) a WooCommerce — canal General.
  const [guardandoContenido, setGuardandoContenido] = useState(false);
  const [contenidoMsg, setContenidoMsg] = useState<{ ok: boolean; texto: string } | null>(null);

  // Al abrir/cambiar de producto: cargar la galería (con ids) y limpiar estado.
  useEffect(() => {
    setGaleria(null);
    setFlagsImg({});
    setProgresoImg({});
    setJobImg(null);
    setProcesandoIA(false);
    setImgActiva(0);
    if (pollImgRef.current) {
      clearInterval(pollImgRef.current);
      pollImgRef.current = null;
    }
    if (!sku) return;
    let vivo = true;
    galeriaProducto(sku)
      .then((g) => { if (vivo) setGaleria(g.imagenes ?? []); })
      .catch(() => { if (vivo) setGaleria([]); });
    return () => {
      vivo = false;
      if (pollImgRef.current) {
        clearInterval(pollImgRef.current);
        pollImgRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sku]);

  // Campos que SÍ mejora la IA (por canal, persisten en memoria).
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [atributos, setAtributos] = useState<AtributoProducto[]>([]);
  const [highlights, setHighlights] = useState("");
  const [bullets, setBullets] = useState<string[]>([]);

  // IA
  const [mejorando, setMejorando] = useState(false);
  const [competencia, setCompetencia] = useState<CompetenciaResp | null>(null);

  // Publicar (paso 4)
  const [previewPub, setPreviewPub] = useState<PublicarPreview | null>(null);
  const [cargandoPreview, setCargandoPreview] = useState(false);
  const [publicando, setPublicando] = useState(false);
  const [resultadoPub, setResultadoPub] = useState<PublicarResultado | null>(null);
  const [amazonPublicadoOk, setAmazonPublicadoOk] = useState(false);

  // ── Bloque COSTOS (dims de pieza → CBM → costo → precios) ───────────
  // costo_producto se ingresa en USD (Alibaba/Odoo) y se convierte a MXN con el
  // tipo de cambio antes de calcular. El resto (CBM, precios) ya es MXN.
  const [costoProducto, setCostoProducto] = useState(""); // USD
  const [tipoCambio, setTipoCambio] = useState(String(DEFAULT_TC));
  const [catMlId, setCatMlId] = useState(""); // categoría ML editable (para el costo)
  // Niveles de la categoría ML VIGENTE (recién elegida o cargada): alimentan el
  // breadcrumb y, sobre todo, el contexto de "Mejorar con IA". Sin esto, la IA
  // leía los niveles GUARDADOS (viejos) y regeneraba con la categoría anterior
  // (caso ACC-0653: seguía escribiendo "binoculares" tras cambiar la categoría).
  const [catMlNiveles, setCatMlNiveles] = useState<string[] | null>(null);
  const [guardandoCat, setGuardandoCat] = useState(false);
  const [comision, setComision] = useState(""); // comisión ML % (vacío = ML/fallback)
  const [margen, setMargen] = useState("48");
  const [incluirEnvio, setIncluirEnvio] = useState(true);
  const [costoCalc, setCostoCalc] = useState<Partial<CostoCalculo> | null>(null);
  const [costoFresco, setCostoFresco] = useState(false); // true tras Regenerar/Guardar
  const [regenerando, setRegenerando] = useState(false);
  const [guardandoCosto, setGuardandoCosto] = useState(false);
  const [costoMsg, setCostoMsg] = useState<{ ok: boolean; texto: string } | null>(null);

  const cargandoCampos = useRef(false);

  // ── Tema del canal seleccionado ────────────────────────────────────
  const canalInfo = useMemo(() => canales.find((c) => c.id === canal), [canales, canal]);
  const tema: CanalTheme = useMemo(() => {
    const fb = THEME_FALLBACK[canal] ?? THEME_FALLBACK.general;
    if (!canalInfo) return fb;
    return { color: canalInfo.color, texto: canalInfo.color_texto, acento: canalInfo.acento, suave: fb.suave ?? hexToRgba(canalInfo.color, 0.1) };
  }, [canalInfo, canal]);

  // ── Reset al cambiar de SKU ─────────────────────────────────────────
  useEffect(() => {
    setCanal(GENERAL);
    setImgActiva(0);
    setMeta(null);
    setCampos(CAMPOS_VACIOS);
    setCompetencia(sku ? getCompStore(sku) ?? null : null);
    setPreviewPub(null);
    setResultadoPub(null);
    setAmazonPublicadoOk(false);
    setCostoProducto("");
    setTipoCambio(String(DEFAULT_TC));
    setCatMlId("");
    setCatMlNiveles(null);
    setComision("");
    setMargen("48");
    setIncluirEnvio(true);
    setCostoCalc(null);
    setCostoFresco(false);
    setCostoMsg(null);
  }, [sku]);

  // ── Semilla del bloque COSTOS: costo_producto + desglose actual ─────
  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    costoDetalle(sku, ctrl.signal)
      .then((d) => {
        const cv = (d.validados ?? {}) as Record<string, unknown>;
        const cf = (d.finales ?? {}) as Record<string, unknown>;
        const num = (v: unknown) => (v == null || v === "" ? undefined : Number(v));
        // Guardado en MXN → mostrar en USD (÷ tipo de cambio) para editar.
        const cp = num(cv.costo_producto) ?? num(cf.costo_producto);
        if (cp != null) setCostoProducto(String(Math.round((cp / DEFAULT_TC) * 100) / 100));
        // Categoría usada para costear (costos_finales) — respaldo si el postmeta
        // de Woo aún no la tiene (ej. la escritura no llegó o el producto es nuevo).
        if (cf.ml_cat_id) setCatMlId((c) => c || String(cf.ml_cat_id));
        // Comisión ya guardada (costos_finales.pct_comision) — antes solo se
        // reflejaba tras Regenerar/Guardar; al abrir el Estudio quedaba vacía
        // aunque el badge ya mostrara el % correcto.
        const pctGuardado = num(cf.pct_comision);
        if (pctGuardado != null) setComision((c) => c || String(Math.round(pctGuardado * 1000) / 10));
        // No hay un booleano guardado de "incluir envío"; se infiere de
        // costo_fee_envio: la tabla de tarifas nunca da $0, así que un $0
        // guardado solo puede venir de haber desmarcado "Sumar al precio".
        const feeGuardado = num(cf.costo_fee_envio);
        if (feeGuardado === 0) setIncluirEnvio(false);
        if (d.constantes?.margen != null) setMargen(String(Math.round(d.constantes.margen * 100)));
        // Desglose actual (sin recalcular) para que el bloque no salga vacío.
        setCostoCalc({
          costo_producto: cp,
          costo_cbm: num(cv.costo_cbm) ?? num(cf.costo_cbm),
          costo_unitario: num(cf.costo_unitario) ?? num(cv.costo_total),
          costo_comision: num(cf.costo_comision),
          costo_fee_envio: num(cf.costo_fee_envio),
          precio_base: num(cf.precio_base),
          precio_sugerido: num(cf.precio_sugerido),
          pct_comision: num(cf.pct_comision) ?? undefined,
        });
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [sku]);

  // ── Metadata del Estudio (postmeta): precios/costo/alibaba/dims ─────
  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    studioMetadata(sku, producto?.wc_id ?? null, ctrl.signal)
      .then((m) => {
        setMeta(m);
        setCatMlId((c) => c || (m.categoria_ml?.category_id ?? ""));
        setCatMlNiveles((n) => n ?? (m.categoria_ml?.niveles?.length ? m.categoria_ml.niveles : null));
        const d = m.dinero || ({} as StudioMetadata["dinero"]);
        setCampos({
          precioRegular: str(d.precio_regular),
          precioOferta: str(d.precio_oferta),
          costo: str(d.costo),
          alibabaUrl: m.alibaba_url ?? "",
          alibabaPrecio: str(m.alibaba_precio),
          peso: str(d.peso), largo: str(d.largo), ancho: str(d.ancho), alto: str(d.alto),
        });
      })
      .catch(() => setMeta(null));
    return () => ctrl.abort();
  }, [sku, producto?.wc_id]);

  // Fallback: si el postmeta no trajo precios, usa los del detalle (WooCommerce).
  useEffect(() => {
    if (!data) return;
    setCampos((c) => ({
      ...c,
      precioRegular: c.precioRegular || (data.precio_base != null ? String(data.precio_base) : ""),
      precioOferta: c.precioOferta || (data.precio_oferta != null ? String(data.precio_oferta) : ""),
    }));
  }, [data]);

  // ── Cargar campos editables (mejora guardada por canal, o base) ─────
  useEffect(() => {
    if (!sku) return;
    cargandoCampos.current = true;
    const stored = getMejora(sku, canal);
    // `||` (no `??`): un guardado VACÍO (ej. del detalle parcial inicial con
    // descripcion=null) NO debe ocultar el dato real de Woo. Así el modal
    // siempre muestra la descripción/título actuales de WooCommerce.
    setTitulo(stored?.titulo || data?.nombre || "");
    setDescripcion(stored?.descripcion || data?.descripcion || "");
    setAtributos((stored?.atributos && stored.atributos.length ? stored.atributos : meta?.atributos) ?? []);
    setHighlights(stored?.highlights ?? "");
    setBullets(stored?.bullets ?? []);
    const id = setTimeout(() => { cargandoCampos.current = false; }, 0);
    return () => clearTimeout(id);
  }, [sku, canal, data?.nombre, data?.descripcion, meta]);

  // ── Persistir en memoria lo mejorado/editado (por sku+canal) ────────
  useEffect(() => {
    if (!sku || cargandoCampos.current) return;
    saveMejora(sku, canal, { titulo, descripcion, atributos, highlights, bullets });
  }, [sku, canal, titulo, descripcion, atributos, highlights, bullets]);

  // Cerrar con ESC
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (sku) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sku, onClose]);

  const datosCanal: DetalleCanal | undefined = useMemo(
    () => data?.canales.find((c) => c.canal === canal),
    [data, canal],
  );
  const esGeneral = canal === GENERAL;
  const esAmazon = canal === AMAZON;
  const esML = canal === "mercado_libre";

  // Estado REAL de publicación (fuente de verdad en DB: ml_progress / amazon_progress).
  const mlCuentas = useMemo(
    () => (meta?.estado?.ml ?? []).map((p) => p.cuenta),
    [meta],
  );
  const mlPublicado = mlCuentas.length > 0;
  const amazonPublicadoReal = !!meta?.estado?.amazon?.publicado;
  const estaPublicado = esML ? mlPublicado : esAmazon ? amazonPublicadoReal : !!datosCanal?.publicado;

  const categoriaWC = useMemo(() => {
    const general = data?.canales.find((c) => c.canal === GENERAL);
    return datosCanal?.categoria_path?.length ? datosCanal.categoria_path : general?.categoria_path ?? [];
  }, [data, datosCanal]);

  // Prefiere los niveles VIGENTES (recién elegidos en el picker) sobre los
  // guardados: así "Mejorar con IA" usa la categoría actual, no la anterior.
  const categoriaMLTexto =
    (catMlNiveles?.length ? catMlNiveles.join(" › ") : null) ||
    meta?.categoria_ml?.niveles?.join(" › ") ||
    null;

  // Elegir categoría ML en el picker: actualiza el estado VIGENTE (breadcrumb +
  // contexto de IA) y PERSISTE en WooCommerce (ml_categoria_id — la elección
  // humana que MANDA al publicar). Antes solo cambiaba estado local y se perdía.
  const elegirCategoriaML = useCallback(
    async (c: CategoriaMLResult) => {
      const niveles = c.path ? c.path.split(/\s*[>›]\s*/).filter(Boolean) : [c.name];
      setCatMlId(c.category_id);
      setCatMlNiveles(niveles);
      const wcId = meta?.wc_id ?? producto?.wc_id ?? null;
      if (!wcId) return;
      setGuardandoCat(true);
      try {
        const r = await guardarCategoriaML(wcId, c.category_id);
        // Refleja lo guardado en el meta para que sobreviva a recargas y lo lea
        // el publicador; usa los niveles que devolvió el backend (canónicos).
        const nivelesSrv = r.niveles?.map((n) => n.name) ?? niveles;
        setCatMlNiveles(nivelesSrv);
        setMeta((m) =>
          m
            ? { ...m, categoria_ml: { category_id: r.category_id, ruta: r.path, niveles: nivelesSrv } }
            : m,
        );
      } catch {
        /* el estado local ya cambió; un fallo de red no bloquea la edición */
      } finally {
        setGuardandoCat(false);
      }
    },
    [meta?.wc_id, producto?.wc_id],
  );

  const setCampo = (k: keyof Campos, v: string) => setCampos((c) => ({ ...c, [k]: v }));
  const setAtributo = (i: number, valor: string) =>
    setAtributos((a) => a.map((x, j) => (j === i ? { ...x, valor } : x)));
  const setBullet = (i: number, v: string) =>
    setBullets((b) => {
      const n = [...b];
      while (n.length <= i) n.push("");
      n[i] = v;
      return n;
    });

  // Atributos SIN las filas basura de dimensiones/peso (esas viven en COSTOS).
  // Conserva el índice original para que setAtributo edite el elemento correcto.
  const atributosVisibles = useMemo(
    () =>
      atributos
        .map((a, i) => ({ a, i }))
        .filter(({ a }) =>
          !/^\s*(peso|dimensi|medida|largo|ancho|alto|tama|volumen|weight|length|width|height|size)/i.test(
            a.nombre || "",
          ),
        ),
    [atributos],
  );

  // ── Mejorar con IA (un botón por canal) ─────────────────────────────
  // CANDADO anti-contaminación: la respuesta de "Mejorar con IA" tarda ~20-30 s;
  // si el usuario cambió de producto o canal mientras tanto, la respuesta vieja
  // se DESCARTA. Sin esto, el texto de un producto aterrizaba en los campos del
  // siguiente y el autosave lo persistía en su borrador (caso real: los
  // binoculares HO392 aparecieron en ACC-0653, faros de niebla).
  const pedidoVigente = useRef<string>("");
  useEffect(() => {
    pedidoVigente.current = `${sku ?? ""}:${canal}`;
  }, [sku, canal]);

  const mejorarConIA = useCallback(async () => {
    if (!data || !sku) return;
    const pedido = `${sku}:${canal}`;
    setMejorando(true);
    const modelo = atributos.find((a) => /model|modelo/i.test(a.nombre))?.valor || null;
    const ctx: ProductoIA = {
      nombre: titulo || data.nombre,
      marca: data.marca,
      modelo,
      categoria: categoriaMLTexto || categoriaWC.map((c) => c.nombre).join(" › ") || null,
      descripcion: descripcion || data.descripcion,
      precio: Number(campos.precioRegular) || null,
      costo: Number(campos.costo) || null,
      ml_cat_id: catMlId || meta?.categoria_ml?.category_id || null,
      sku,
      atributos,
    };
    const [mej, comp] = await Promise.allSettled([
      mejorarIA({ canal, producto: ctx }),
      precioCompetencia({ producto: ctx, con_lista: true }),
    ]);

    // ¿El usuario sigue en el mismo producto+canal? Si no, descartar TODO.
    if (pedidoVigente.current !== pedido) return;

    if (mej.status === "fulfilled" && mej.value.ok && mej.value.campos) {
      const c = mej.value.campos;
      if (c.titulo != null) setTitulo(c.titulo);
      if (c.descripcion != null) setDescripcion(c.descripcion);
      if (c.atributos?.length) setAtributos(c.atributos);
      if (c.highlights != null) setHighlights(c.highlights);
      if (c.bullets?.length) setBullets(c.bullets);
    }
    setMejorando(false);

    if (comp.status === "fulfilled") {
      setCompetencia(comp.value);
      if (comp.value.ok) saveCompetencia(sku, comp.value);
    } else {
      setCompetencia({ ok: false, motivo: "No se pudo consultar la competencia." });
    }
  }, [data, sku, canal, titulo, descripcion, atributos, campos.precioRegular, campos.costo, categoriaMLTexto, categoriaWC]);

  // ── Publicar / actualizar en el canal (paso 4) ──────────────────────
  const itemIdSel = datosCanal?.item_id ?? null;
  const cuentaSel =
    (datosCanal?.extra as { cuenta?: string } | undefined)?.cuenta ?? producto?.cuenta ?? null;
  // ML y Amazon: botón siempre disponible → "Publicar" si NO está publicado
  // (crea nuevo), "Actualizar" si ya está.
  const amazonPublicado = amazonPublicadoReal || amazonPublicadoOk;
  const puedeActualizar = esML || esAmazon;
  const accionLabel =
    (esML && mlPublicado) || (esAmazon && amazonPublicado) ? "Actualizar en" : "Publicar a";

  const numOrNull = (v: string) => (v.trim() ? Number(v) || null : null);

  // ── COSTOS: regenerar (preview) / guardar (persistir + Woo) ─────────
  // costo_producto (USD) → MXN con el tipo de cambio antes de enviar.
  const costoProductoMXN = (): number | null => {
    const usd = numOrNull(costoProducto);
    const tc = Number(tipoCambio) || 0;
    return usd != null && tc > 0 ? Math.round(usd * tc * 100) / 100 : usd;
  };

  const overridesCosto = (): CostoOverrides => ({
    costo_producto: costoProductoMXN(),
    largo: numOrNull(campos.largo),
    ancho: numOrNull(campos.ancho),
    alto: numOrNull(campos.alto),
    peso: numOrNull(campos.peso),
    margen: (Number(margen) || 0) / 100,
    incluir_envio: incluirEnvio,
    auto_cbm: true,
    // Categoría ML editable (default del postmeta). El cálculo la necesita
    // (en costos_finales puede no existir aún, ej. draft).
    ml_cat_id: catMlId || meta?.categoria_ml?.category_id || null,
    // Comisión ML manual (%). Vacío = la resuelve el backend (ML o fallback).
    pct_comision: comision.trim() ? (Number(comision) || 0) / 100 : null,
  });

  // Tras calcular, refleja la comisión usada en el campo (si estaba vacío).
  const reflejarComision = (c: Partial<CostoCalculo>) => {
    if (!comision.trim() && c.pct_comision != null) {
      setComision(String(Math.round(c.pct_comision * 1000) / 10));
    }
  };

  // Refleja el cálculo en los campos que usa el resto del modal (publicar).
  const sincronizarCampos = (c: Partial<CostoCalculo>) => {
    setCampos((p) => ({
      ...p,
      costo: c.costo_unitario != null ? String(c.costo_unitario) : p.costo,
      precioRegular: c.precio_base != null ? String(c.precio_base) : p.precioRegular,
      precioOferta: c.precio_sugerido != null ? String(c.precio_sugerido) : p.precioOferta,
    }));
  };

  async function regenerarCosto() {
    if (!sku) return;
    setRegenerando(true);
    setCostoMsg(null);
    try {
      const r = await costoPreview(sku, overridesCosto());
      setCostoCalc(r.calculo);
      setCostoFresco(true);
      sincronizarCampos(r.calculo);
      reflejarComision(r.calculo);
    } catch {
      setCostoMsg({ ok: false, texto: "No se pudo calcular: revisa el costo, o ingresa la Comisión ML (%) — no se encontró la de la categoría." });
    } finally {
      setRegenerando(false);
    }
  }

  async function guardarCosto() {
    if (!sku) return;
    setGuardandoCosto(true);
    setCostoMsg(null);
    try {
      const r = await costoGuardar(sku, { ...overridesCosto(), sincronizar_woo: true });
      const f = r.finales as Record<string, unknown>;
      const num = (v: unknown) => (v == null || v === "" ? undefined : Number(v));
      const merged: Partial<CostoCalculo> = {
        ...(costoCalc ?? {}),
        costo_producto: num(f.costo_producto),
        costo_cbm: num(f.costo_cbm),
        costo_unitario: num(f.costo_unitario),
        costo_comision: num(f.costo_comision),
        costo_fee_envio: num(f.costo_fee_envio),
        precio_base: num(f.precio_base),
        precio_sugerido: num(f.precio_sugerido),
        pct_comision: num(f.pct_comision) ?? undefined,
      };
      setCostoCalc(merged);
      setCostoFresco(true);
      sincronizarCampos(merged);
      reflejarComision(merged);
      setCostoMsg({
        ok: true,
        texto: r.sincronizado_woo
          ? "Guardado y sincronizado con WooCommerce."
          : "Guardado en la base de datos.",
      });
      onGuardado?.();
    } catch {
      setCostoMsg({ ok: false, texto: "No se pudo guardar el costo." });
    } finally {
      setGuardandoCosto(false);
    }
  }

  function reqPublicar() {
    return {
      canal,
      cuenta: cuentaSel,
      sku,
      wc_id: producto?.wc_id ?? meta?.wc_id ?? null,
      item_id: itemIdSel,
      campos: {
        titulo, descripcion, highlights, bullets, atributos,
        precio_regular: numOrNull(campos.precioRegular),
        peso: numOrNull(campos.peso),
        largo: numOrNull(campos.largo),
        ancho: numOrNull(campos.ancho),
        alto: numOrNull(campos.alto),
      },
    };
  }

  async function abrirPreview() {
    setCargandoPreview(true);
    setResultadoPub(null);
    setPreviewPub(null);
    try {
      setPreviewPub(await publicarPreview(reqPublicar()));
    } catch {
      setPreviewPub({ ok: false, canal, motivo: "No se pudo generar la vista previa." });
    } finally {
      setCargandoPreview(false);
    }
  }

  async function confirmarPublicar() {
    setPublicando(true);
    try {
      const r = await publicarConfirmar(reqPublicar());
      setResultadoPub(r);
      if (r.ok && canal === "amazon") setAmazonPublicadoOk(true);
    } catch {
      setResultadoPub({ ok: false, error: "Error de conexión al publicar." });
    } finally {
      setPublicando(false);
    }
  }

  function cerrarPreview() {
    setPreviewPub(null);
    setResultadoPub(null);
  }

  // ── Editor de imágenes: helpers + acciones ──────────────────────────
  const wcId = data?.wc_id ?? null;
  const flagsDe = (id: number): FlagsImagen =>
    flagsImg[id] ?? { quitar_fondo: false, traducir_texto: false, quitar_logos: false, cambiar_modelo: false };
  const hasFlags = (id: number) => {
    const f = flagsImg[id];
    return !!f && (f.quitar_fondo || f.traducir_texto || f.quitar_logos || f.cambiar_modelo);
  };
  const countFlags = (id: number) =>
    [flagsDe(id).quitar_fondo, flagsDe(id).traducir_texto, flagsDe(id).quitar_logos, flagsDe(id).cambiar_modelo].filter(Boolean).length;

  function toggleFlag(imgId: number, key: keyof FlagsImagen) {
    setFlagsImg((prev) => {
      const cur = prev[imgId] ?? { quitar_fondo: false, traducir_texto: false, quitar_logos: false, cambiar_modelo: false };
      return { ...prev, [imgId]: { ...cur, [key]: !cur[key] } };
    });
  }

  function iniciarPollingImg() {
    if (pollImgRef.current) clearInterval(pollImgRef.current);
    const tick = async () => {
      try {
        const j = await progresoImagenes(sku!);
        setJobImg(j);
        const m: Record<number, ImagenProgreso> = {};
        (j.imagenes ?? []).forEach((it) => {
          if (it.wc_image_id) m[it.wc_image_id] = it;
        });
        setProgresoImg(m);
        if (j.estado === "completado" || j.estado === "sin_datos") {
          if (pollImgRef.current) {
            clearInterval(pollImgRef.current);
            pollImgRef.current = null;
          }
          setProcesandoIA(false);
          // Re-sincronizar la galería con WooCommerce (nuevos ids/urls) y limpiar flags.
          try {
            const g = await galeriaProducto(sku!, wcId);
            setGaleria(g.imagenes ?? []);
          } catch {
            /* se conserva la galería previa */
          }
          setFlagsImg({});
        }
      } catch {
        /* reintenta en el próximo tick */
      }
    };
    pollImgRef.current = setInterval(tick, 2500);
    tick();
  }

  async function procesarIA() {
    if (!galeria) return;
    const seleccion = galeria
      .filter((img) => img.id && hasFlags(img.id))
      .map((img) => ({ wc_image_id: img.id, src: img.src, ...flagsDe(img.id) }));
    if (!seleccion.length) return;
    setProcesandoIA(true);
    setProgresoImg({});
    setJobImg(null);
    try {
      await procesarImagenesIA(sku!, { wc_id: wcId, imagenes: seleccion });
      iniciarPollingImg();
    } catch {
      setProcesandoIA(false);
    }
  }

  async function eliminarImg(img: GaleriaImagen) {
    if (!img.id) return;
    if (!window.confirm("¿Quitar esta imagen del producto en WooCommerce?")) return;
    setEliminandoId(img.id);
    try {
      await eliminarImagenGaleria(sku!, { wc_id: wcId, image_id: img.id });
      setGaleria((prev) => (prev ?? []).filter((x) => x.id !== img.id));
      setFlagsImg((prev) => {
        const n = { ...prev };
        delete n[img.id];
        return n;
      });
    } catch {
      window.alert("No se pudo eliminar la imagen.");
    } finally {
      setEliminandoId(null);
    }
  }

  async function guardarContenidoWoo() {
    if (!sku) return;
    setGuardandoContenido(true);
    setContenidoMsg(null);
    try {
      await guardarContenido(sku, {
        wc_id: data?.wc_id ?? null,
        titulo,
        descripcion,
        // Todos los atributos custom (los editados + los de peso/dims sin tocar):
        // el backend preserva los de variación y reemplaza estos custom.
        atributos,
      });
      setContenidoMsg({ ok: true, texto: "Contenido guardado en WooCommerce." });
      onGuardado?.();
    } catch {
      setContenidoMsg({ ok: false, texto: "No se pudo guardar el contenido." });
    } finally {
      setGuardandoContenido(false);
    }
  }

  function descartarBorrador() {
    if (!sku) return;
    limpiarBorrador(sku);
    // Recarga el contenido desde WooCommerce/postmeta (descarta ediciones locales).
    cargandoCampos.current = true;
    setTitulo(data?.nombre || "");
    setDescripcion(data?.descripcion || "");
    setAtributos(meta?.atributos ?? []);
    setHighlights("");
    setBullets([]);
    setContenidoMsg({ ok: true, texto: "Borrador descartado · contenido recargado de WooCommerce." });
    setTimeout(() => { cargandoCampos.current = false; }, 0);
  }

  function leerBase64(f: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const res = String(reader.result || "");
        const coma = res.indexOf(",");
        resolve(coma >= 0 ? res.slice(coma + 1) : res);
      };
      reader.onerror = reject;
      reader.readAsDataURL(f);
    });
  }

  async function agregarArchivos(files: FileList | null) {
    if (!files || !files.length || !sku) return;
    const lista = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!lista.length) return;
    setAgregandoImg(true);
    try {
      const imagenes = await Promise.all(
        lista.map(async (f) => ({
          filename: f.name,
          mime: f.type || "image/jpeg",
          data_b64: await leerBase64(f),
        })),
      );
      const r = await agregarImagenes(sku, { wc_id: wcId, imagenes });
      if (r.imagenes?.length) setGaleria(r.imagenes);
    } catch {
      window.alert("No se pudieron agregar las imágenes.");
    } finally {
      setAgregandoImg(false);
      setDragImg(false);
    }
  }

  if (!sku) return null;

  const imagenes = data?.imagenes?.length ? data.imagenes : data?.imagen ? [data.imagen] : [];
  const limiteTitulo = LIMITE_TITULO[canal];

  // Galería del editor: usa la galería con ids si ya cargó; si no, cae a las urls
  // del detalle (solo lectura hasta que lleguen los ids desde el backend).
  const galItems: GaleriaImagen[] =
    galeria ?? imagenes.map((src, i) => ({ id: 0, src, position: i }));
  const galEditable = galeria !== null;
  const galIdxActiva = galItems.length ? Math.min(imgActiva, galItems.length - 1) : 0;
  const galActiva = galItems.length ? galItems[galIdxActiva] : null;
  const totalConFlags = galItems.filter((im) => im.id && hasFlags(im.id)).length;
  const jobActivo = procesandoIA || jobImg?.estado === "procesando";
  const pctImg = jobImg && jobImg.total ? Math.round((jobImg.procesadas / jobImg.total) * 100) : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={variablesTema(tema)}>
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" onClick={onClose} />

      <aside className="relative flex h-full w-full max-w-[960px] animate-slide-in flex-col bg-slate-50 shadow-2xl">
        {/* Header temático por canal */}
        <div
          className="flex items-start justify-between gap-3 px-6 py-4 transition-colors duration-300"
          style={{ background: `linear-gradient(120deg, ${tema.color} 0%, ${hexToRgba(tema.acento, 0.92)} 100%)`, color: tema.texto }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-white/90">
              {data?.imagen ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={data.imagen} alt="" className="h-full w-full object-contain p-1" />
              ) : (<ImageIcon className="text-slate-300" />)}
            </div>
            <div className="min-w-0">
              <span className="rounded-md bg-black/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold">{sku}</span>
              <h2 className="mt-1 line-clamp-2 text-base font-bold leading-snug">{data?.nombre ?? "Cargando…"}</h2>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-2 transition-colors hover:bg-black/15"><X size={20} /></button>
        </div>

        {/* Selector de canal + botón Mejorar con IA */}
        <div className="border-b border-slate-200 bg-white px-6 py-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Selecciona canal a editar</div>
          <div className="flex flex-wrap items-center gap-2">
            {canales.map((c) => {
              const sel = c.id === canal;
              const off = !c.habilitado && c.id !== GENERAL;
              return (
                <button
                  key={c.id}
                  disabled={off}
                  onClick={() => !off && setCanal(c.id)}
                  title={off ? "Próximamente" : c.descripcion}
                  style={sel ? { backgroundColor: c.color, color: c.color_texto, borderColor: c.color } : undefined}
                  className={[
                    "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-semibold transition-all",
                    sel ? "scale-[1.03] shadow-sm"
                      : off ? "cursor-not-allowed border-dashed border-slate-200 bg-slate-50 text-slate-400"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                >
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: sel ? c.color_texto : c.color }} />
                  {c.label}
                  {off && <span className="rounded-full bg-slate-200 px-1.5 text-[9px] font-bold uppercase text-slate-500">Pronto</span>}
                </button>
              );
            })}
          </div>

          {/* PUBLICAR A {canal} — acción principal (arriba de Mejorar con IA) */}
          {puedeActualizar && (
            <button
              onClick={abrirPreview}
              disabled={cargandoPreview || !data}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold shadow-sm transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
              style={{ background: `linear-gradient(120deg, ${tema.color}, ${tema.acento})`, color: tema.texto }}
            >
              {cargandoPreview ? <Loader2 size={17} className="animate-spin" /> : <UploadCloud size={17} />}
              {accionLabel} {canalInfo?.label ?? canal}
              {canal === "mercado_libre" ? " · ambas cuentas" : cuentaSel ? ` · ${cuentaSel}` : ""}
            </button>
          )}

          {/* MEJORAR CON IA — secundario */}
          <button
            onClick={mejorarConIA}
            disabled={mejorando || !data}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl border-2 bg-white px-4 py-2 text-sm font-bold transition-all hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
            style={{ borderColor: tema.color, color: tema.acento }}
          >
            {mejorando ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
            {mejorando ? "Mejorando…" : `Mejorar con IA · ${canalInfo?.label ?? canal}`}
          </button>
          <p className="mt-1.5 text-center text-[11px] text-slate-400">
            <strong>Publicar</strong> envía los datos actuales al canal (revisas antes).{" "}
            <strong>Mejorar con IA</strong> optimiza título, descripción y atributos{esAmazon ? " + highlights y bullets" : ""} y sugiere precio de competencia (no toca precio/costo/dimensiones).
          </p>
        </div>

        {/* Cuerpo */}
        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {!data && cargando && (
            <div className="space-y-4">
              <div className="h-6 w-1/3 animate-pulse rounded bg-white" />
              <div className="h-52 animate-pulse rounded-2xl bg-white" />
              <div className="h-32 animate-pulse rounded-2xl bg-white" />
            </div>
          )}

          {data && (
            <>
              {/* Estado en el canal (marketplaces) */}
              {!esGeneral && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3" style={{ borderColor: hexToRgba(tema.color, 0.4), background: tema.suave }}>
                  {estaPublicado ? (
                    <>
                      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
                        <span className="font-bold" style={{ color: tema.acento }}>
                          Publicado en {canalInfo?.label}
                          {esML && mlCuentas.length ? ` · ${mlCuentas.join(" + ")}` : ""}
                        </span>
                        {esML && mlCuentas.length === 1 && (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-bold text-amber-700">
                            solo 1 cuenta
                          </span>
                        )}
                        {esML && mlCuentas.length >= 2 && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-700">
                            ambas cuentas
                          </span>
                        )}
                        {esAmazon && meta?.estado?.amazon?.status && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-700">
                            {meta.estado.amazon.status}
                          </span>
                        )}
                        {esAmazon && meta?.estado?.amazon?.asin && (
                          <span className="font-mono text-[11px] text-slate-500">{meta.estado.amazon.asin}</span>
                        )}
                        {datosCanal && (
                          <>
                            <span className="text-slate-600">Precio: <strong>{precioMXN(datosCanal.precio)}</strong></span>
                            <span className="text-slate-600">Stock: <strong>{datosCanal.stock ?? datosCanal.stock_real ?? "—"}</strong></span>
                          </>
                        )}
                        {datosCanal?.full && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-700">{datosCanal.full_label ?? "FULL"}</span>}
                      </div>
                      {datosCanal?.url && <a href={datosCanal.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-semibold" style={{ color: tema.acento }}>Ver publicación <ExternalLink size={12} /></a>}
                    </>
                  ) : (
                    <span className="text-sm font-medium text-slate-500">Este producto <strong>no está publicado</strong> en {canalInfo?.label}. Puedes generar el contenido optimizado.</span>
                  )}
                </div>
              )}

              {/* TIPO DE PRODUCTO AMAZON (la "categoría" de Amazon; el panel manda) */}
              {esAmazon && wcId != null && (
                <TipoAmazonPicker sku={sku!} wcId={wcId} />
              )}

              {/* PRECIO DE COMPETENCIA */}
              {competencia && (
                <section className="overflow-hidden rounded-2xl border-2 border-amber-200 bg-amber-50/50">
                  <header className="flex items-center gap-2 border-b border-amber-200 bg-amber-100/60 px-4 py-2">
                    <TrendingUp size={16} className="text-amber-600" />
                    <span className="text-sm font-bold text-amber-800">Precio de competencia sugerido</span>
                    {competencia.proveedor && <span className="text-[10px] uppercase tracking-wide text-amber-500">{competencia.proveedor}</span>}
                  </header>
                  {competencia.ok ? (
                    <div className="space-y-2 px-4 py-3">
                      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                        <span className="text-2xl font-black text-slate-800">
                          {competencia.precio_sugerido != null ? precioMXN(competencia.precio_sugerido) : "—"}
                        </span>
                        {competencia.rango && (
                          <span className="text-xs text-slate-500">
                            rango {precioMXN(competencia.rango.min)}–{precioMXN(competencia.rango.max)} · mediana {precioMXN(competencia.rango.mediana)}
                          </span>
                        )}
                        {competencia.precio_sugerido != null && (
                          <button
                            onClick={() => setCampo("precioRegular", String(competencia.precio_sugerido))}
                            className="rounded-lg bg-amber-500 px-2.5 py-1 text-xs font-bold text-white hover:bg-amber-600"
                          >
                            Usar como precio
                          </button>
                        )}
                      </div>
                      {competencia.razonamiento && <p className="text-xs leading-relaxed text-slate-600">{competencia.razonamiento}</p>}
                      {competencia.aviso && <p className="text-[11px] italic text-amber-600">⚠ {competencia.aviso}</p>}
                      {!!competencia.fuentes?.length && (
                        <details className="text-xs">
                          <summary className="cursor-pointer font-semibold text-slate-500">Fuentes ({competencia.fuentes_encontradas ?? competencia.fuentes.length})</summary>
                          <ul className="mt-1 space-y-0.5">
                            {competencia.fuentes.slice(0, 10).map((f, i) => (
                              <li key={i} className="flex items-center justify-between gap-2 text-slate-500">
                                <span className="truncate">{f.marketplace} · {f.titulo}</span>
                                <span className="shrink-0 font-semibold text-slate-700">{precioMXN(f.precio)}</span>
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                      <p className="text-[11px] text-slate-400">Solo informativo — no cambia el precio salvo que pulses “Usar como precio”.</p>
                    </div>
                  ) : (
                    <div className="px-4 py-3 text-sm text-amber-700">{competencia.motivo ?? "No se pudo calcular."}</div>
                  )}
                </section>
              )}

              {/* CATEGORÍA (Mercado Libre editable + WooCommerce) */}
              <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
                {/* Categoría ML — buscador por nombre. ES LA QUE SE ENVÍA a Mercado
                    Libre al publicar (define además la comisión del costo). */}
                <div className="flex items-center justify-between">
                  <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-indigo-600">
                    Se envía a Mercado Libre
                  </span>
                  {guardandoCat && (
                    <span className="flex items-center gap-1 text-[10px] font-medium text-slate-400">
                      <Loader2 size={11} className="animate-spin" /> guardando…
                    </span>
                  )}
                </div>
                <CategoriaMLPicker
                  value={catMlId}
                  pathInicial={catMlNiveles ?? meta?.categoria_ml?.niveles}
                  onChange={elegirCategoriaML}
                  acento={tema.acento}
                />
                <div className="border-t border-slate-100 pt-3">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Categoría WooCommerce</span>
                    <span className="text-[10px] font-medium text-slate-400">(solo tienda web — no se envía a ML)</span>
                  </div>
                  {categoriaWC.length ? (
                    <div className="flex flex-wrap items-center gap-1 text-sm text-slate-700">
                      {categoriaWC.map((n, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <ChevronRight size={13} className="text-slate-300" />}
                          <span className="font-medium">{n.nombre}</span>
                        </span>
                      ))}
                    </div>
                  ) : (<span className="text-sm text-slate-400">Sin categoría asignada</span>)}
                </div>
              </section>

              {/* Galería / Editor de imágenes */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Imágenes</span>
                  <span className="text-[11px] text-slate-400">
                    {galEditable ? `${galItems.length} en galería` : "cargando…"}
                  </span>
                </div>

                {/* Preview grande de la imagen activa */}
                <div className="relative mb-3 flex h-64 items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-white">
                  {galActiva ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={(galActiva.id && progresoImg[galActiva.id]?.nueva_url) || galActiva.src}
                      alt=""
                      className="h-full w-full object-contain"
                    />
                  ) : (<ImageIcon size={48} className="text-slate-200" />)}
                  {galActiva?.id && progresoImg[galActiva.id]?.estado === "procesando" && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-white/75">
                      <Loader2 size={26} className="animate-spin" style={{ color: tema.color }} />
                      <span className="text-xs font-semibold text-slate-500">{progresoImg[galActiva.id]?.paso}</span>
                    </div>
                  )}
                </div>

                {/* Label de carga: paso global, avance y errores por imagen */}
                {jobActivo && (
                  <div className="mb-3 rounded-xl border px-3 py-2" style={{ borderColor: hexToRgba(tema.color, 0.4), background: tema.suave }}>
                    <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: tema.acento }}>
                      <Loader2 size={15} className="animate-spin" />
                      <span className="truncate">{jobImg?.paso_global || "Procesando imágenes…"}</span>
                      <span className="ml-auto shrink-0 text-xs font-normal text-slate-500">{jobImg?.procesadas ?? 0}/{jobImg?.total ?? 0}</span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
                      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pctImg}%`, backgroundColor: tema.color }} />
                    </div>
                    {(jobImg?.imagenes ?? []).some((x) => x.estado === "error") && (
                      <ul className="mt-2 space-y-0.5">
                        {(jobImg?.imagenes ?? []).filter((x) => x.estado === "error").map((x) => (
                          <li key={x.indice} className="flex items-start gap-1.5 text-[11px] text-red-600">
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            <span>Imagen {x.indice + 1}: {x.error ?? "error"}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}

                {/* Miniaturas con controles al hover (flags + eliminar) */}
                <div className="flex flex-wrap gap-2">
                  {galItems.map((img, i) => {
                    const prog = img.id ? progresoImg[img.id] : undefined;
                    const activa = i === galIdxActiva;
                    return (
                      <div key={img.id || `u${i}`} className="group relative">
                        <button
                          onClick={() => setImgActiva(i)}
                          className={["relative block h-16 w-16 overflow-hidden rounded-lg border-2 transition-colors", activa ? "" : "border-slate-200 hover:border-slate-300"].join(" ")}
                          style={activa ? { borderColor: tema.color } : undefined}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={prog?.nueva_url || img.src} alt="" className="h-full w-full object-contain" />
                          {prog?.estado === "procesando" && (
                            <div className="absolute inset-0 flex items-center justify-center bg-white/70">
                              <Loader2 size={16} className="animate-spin" style={{ color: tema.color }} />
                            </div>
                          )}
                          {prog?.estado === "error" && (
                            <div className="absolute inset-0 flex items-center justify-center bg-red-500/25" title={prog.error ?? "Error"}>
                              <AlertTriangle size={15} className="text-red-600" />
                            </div>
                          )}
                          {prog?.estado === "listo" && (
                            <div className="absolute right-0 top-0 rounded-bl-md bg-emerald-500 p-0.5">
                              <CheckCircle2 size={11} className="text-white" />
                            </div>
                          )}
                          {galEditable && !!img.id && !prog && hasFlags(img.id) && (
                            <div className="absolute left-0 top-0 rounded-br-md px-1 text-[9px] font-bold text-white" style={{ backgroundColor: tema.color }}>
                              {countFlags(img.id)}
                            </div>
                          )}
                        </button>

                        {/* Popover al hover: seleccionar flags + eliminar */}
                        {galEditable && !!img.id && (
                          <div className="absolute bottom-full left-1/2 z-30 hidden -translate-x-1/2 pb-2 group-hover:block">
                            <div className="w-52 rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
                              <div className="mb-1 px-1 text-[10px] font-bold uppercase tracking-wide text-slate-400">Editar con IA</div>
                              <div className="space-y-1">
                                {FLAGS_IMG.map(({ key, label, Icon }) => {
                                  const on = !!flagsImg[img.id]?.[key];
                                  return (
                                    <button
                                      key={key}
                                      onClick={() => toggleFlag(img.id, key)}
                                      className={["flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs font-semibold transition-colors", on ? "" : "bg-slate-50 text-slate-600 hover:bg-slate-100"].join(" ")}
                                      style={on ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                                    >
                                      <Icon size={13} /> {label}
                                      {on && <CheckCircle2 size={12} className="ml-auto" />}
                                    </button>
                                  );
                                })}
                              </div>
                              <button
                                onClick={() => eliminarImg(img)}
                                disabled={eliminandoId === img.id}
                                className="mt-1.5 flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-2 py-1.5 text-xs font-bold text-red-600 transition-colors hover:bg-red-100 disabled:opacity-50"
                              >
                                {eliminandoId === img.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                Eliminar
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {galEditable && (
                    <label
                      onDragOver={(e) => { e.preventDefault(); setDragImg(true); }}
                      onDragLeave={() => setDragImg(false)}
                      onDrop={(e) => { e.preventDefault(); setDragImg(false); void agregarArchivos(e.dataTransfer.files); }}
                      title="Agregar imágenes (clic o arrastra aquí)"
                      className={["flex h-16 w-16 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed transition-colors", dragImg ? "" : "border-slate-200 text-slate-300 hover:border-slate-300 hover:text-slate-400"].join(" ")}
                      style={dragImg ? { borderColor: tema.color, color: tema.color } : undefined}
                    >
                      {agregandoImg ? <Loader2 size={18} className="animate-spin" style={{ color: tema.color }} /> : <Plus size={20} />}
                      <input type="file" accept="image/*" multiple className="hidden"
                        onChange={(e) => { void agregarArchivos(e.target.files); e.currentTarget.value = ""; }} />
                    </label>
                  )}
                </div>

                {/* Procesar con IA (on-demand) */}
                {galEditable && (
                  <>
                    <button
                      onClick={procesarIA}
                      disabled={jobActivo || totalConFlags === 0}
                      className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold shadow-sm transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                      style={{ background: `linear-gradient(120deg, ${tema.color}, ${tema.acento})`, color: tema.texto }}
                    >
                      {jobActivo ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
                      {jobActivo
                        ? "Procesando imágenes…"
                        : totalConFlags
                          ? `Procesar con IA · ${totalConFlags} imagen${totalConFlags > 1 ? "es" : ""}`
                          : "Procesar con IA"}
                    </button>
                    <p className="mt-1.5 text-center text-[11px] text-slate-400">
                      Pasa el mouse sobre una imagen para elegir <strong>Fondo</strong> (quitar fondo), <strong>Traducir texto</strong>, <strong>Quitar logos</strong> o <strong>Modelo</strong> (cambiar persona), o eliminarla. Al procesar, la imagen editada <strong>reemplaza</strong> a la anterior en WooCommerce.
                    </p>
                  </>
                )}
              </section>

              {/* TÍTULO */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <label className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Título</label>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={descartarBorrador}
                      title="Descarta tus ediciones locales y recarga el contenido desde WooCommerce"
                      className="flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-bold text-rose-600 transition-colors hover:bg-rose-100"
                    >
                      <RefreshCw size={11} /> Descartar borrador
                    </button>
                    {limiteTitulo ? (
                      <span className={["text-[11px] font-semibold tabular-nums", titulo.length > limiteTitulo ? "text-red-500" : "text-slate-400"].join(" ")}>
                        {titulo.length}/{limiteTitulo}{titulo.length > limiteTitulo ? " · excede" : ""}
                      </span>
                    ) : (
                      <span className="text-[11px] text-slate-400">{titulo.length} car.</span>
                    )}
                  </div>
                </div>
                <input value={titulo} onChange={(e) => setTitulo(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-800 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
              </section>

              {/* DESCRIPCIÓN */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Descripción</label>
                <textarea value={descripcion} onChange={(e) => setDescripcion(e.target.value)} rows={6}
                  className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2.5 text-sm leading-relaxed text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
              </section>

              {/* AMAZON: HIGHLIGHTS + BULLETS (desbloqueado en Amazon) */}
              {esAmazon && (
                <section className="space-y-4 rounded-2xl border-2 p-4" style={{ borderColor: hexToRgba(tema.color, 0.5), background: tema.suave }}>
                  <div className="text-[11px] font-bold uppercase tracking-[0.15em]" style={{ color: tema.acento }}>Campos Amazon</div>
                  <div>
                    <div className="mb-1.5 flex items-center justify-between">
                      <label className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500">Item Highlights</label>
                      <span className="text-[11px] text-slate-400">{highlights.length}/125</span>
                    </div>
                    <input value={highlights} onChange={(e) => setHighlights(e.target.value)} placeholder="Se llena al Mejorar con IA…"
                      className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                  </div>
                  <div>
                    <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-500">Bullet Points (5)</label>
                    <div className="space-y-2">
                      {Array.from({ length: Math.max(bullets.length, 5) }).map((_, i) => (
                        <div key={i} className="flex items-start gap-2">
                          <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: tema.acento }} />
                          <textarea
                            value={bullets[i] ?? ""}
                            onChange={(e) => setBullet(i, e.target.value)}
                            rows={2}
                            placeholder={`Bullet ${i + 1} (se llena al Mejorar con IA)`}
                            className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:ring-2"
                            style={{ outlineColor: tema.acento }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                </section>
              )}

              {/* PRECIOS + COSTO + STOCK (solo lectura) */}
              <section className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Campo label="Precio regular" prefijo="$" value={campos.precioRegular} onChange={(v) => setCampo("precioRegular", v)} acento={tema.acento} />
                <Campo label="Precio oferta" prefijo="$" value={campos.precioOferta} onChange={(v) => setCampo("precioOferta", v)} acento={tema.acento} />
                <Campo label="Costo" prefijo="$" value={campos.costo} onChange={(v) => setCampo("costo", v)} acento={tema.acento} />
                <div>
                  <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Stock <span className="normal-case text-slate-300">(solo lectura)</span>
                  </label>
                  <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm font-bold text-slate-700">
                    {meta?.stock != null ? meta.stock : "—"}
                    <span className="text-xs font-normal text-slate-400">u</span>
                  </div>
                </div>
              </section>

              {/* COSTOS — dims de pieza → CBM → costo → precios */}
              <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em]" style={{ color: tema.acento }}>
                    <Calculator size={14} /> Costos
                  </div>
                  {costoCalc?.pct_comision != null && (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                      comisión ML {Math.round((costoCalc.pct_comision ?? 0) * 100)}%
                    </span>
                  )}
                </div>

                {/* Entradas editables */}
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div>
                    <Campo label="Costo producto (USD)" prefijo="$" value={costoProducto} onChange={setCostoProducto} acento={tema.acento} />
                    {(() => {
                      const usd = Number(costoProducto) || 0;
                      const tc = Number(tipoCambio) || 0;
                      return usd > 0 && tc > 0
                        ? <p className="mt-1 text-[10px] text-slate-400">≈ {precioMXN(Math.round(usd * tc * 100) / 100)} MXN</p>
                        : null;
                    })()}
                  </div>
                  <Campo label="Tipo de cambio USD→MXN" value={tipoCambio} onChange={setTipoCambio} acento={tema.acento} />
                  <Campo label="Peso (kg)" value={campos.peso} onChange={(v) => setCampo("peso", v)} acento={tema.acento} />
                  <Campo label="Margen (%)" value={margen} onChange={setMargen} acento={tema.acento} />
                  <div>
                    <Campo label="Comisión ML (%)" value={comision} onChange={setComision} acento={tema.acento} />
                    {costoCalc?.comision_estimada && !comision.trim() && (
                      <p className="mt-1 text-[10px] text-amber-600">estimada · por categoría (histórico)</p>
                    )}
                  </div>
                  <div>
                    <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Envío</label>
                    <label className="flex h-[42px] cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600">
                      <input type="checkbox" checked={incluirEnvio} onChange={(e) => setIncluirEnvio(e.target.checked)} className="h-4 w-4" style={{ accentColor: tema.acento }} />
                      Sumar al precio
                    </label>
                  </div>
                </div>

                {/* Dimensiones de la PIEZA (el flete se calcula por pieza) */}
                <div>
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Dimensiones de la pieza — Largo × Ancho × Alto (cm)
                    {costoCalc?.volumen_m3 != null && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-amber-700">{costoCalc.volumen_m3} m³</span>}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {(["largo", "ancho", "alto"] as const).map((k) => (
                      <input key={k} value={campos[k]} onChange={(e) => setCampo(k, e.target.value)} placeholder={k}
                        className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                    ))}
                  </div>
                  <p className="mt-1 text-[11px] text-slate-400">
                    Flete por pieza (CBM) = volumen × ${(costoCalc?.tarifa_cbm_m3 ?? 7500).toLocaleString("es-MX")}/m³ (contenedor estándar).
                  </p>
                </div>

                {/* Regenerar (preview) */}
                <button
                  onClick={regenerarCosto}
                  disabled={regenerando || !data}
                  className="flex w-full items-center justify-center gap-2 rounded-xl border-2 bg-white px-4 py-2 text-sm font-bold transition-all hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ borderColor: tema.color, color: tema.acento }}
                >
                  {regenerando ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
                  {regenerando ? "Calculando…" : "Regenerar costo"}
                </button>

                {/* Resultados */}
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  <Resultado label="Flete CBM" value={precioMXN(costoCalc?.costo_cbm)} />
                  <Resultado label="Costo" value={precioMXN(costoCalc?.costo_unitario)} destacado acento={tema.acento} />
                  <Resultado label="Comisión ML" value={precioMXN(costoCalc?.costo_comision)} />
                  <Resultado label="Envío" value={precioMXN(costoCalc?.costo_fee_envio)} />
                  <Resultado label="Precio regular" value={precioMXN(costoCalc?.precio_base)} />
                  <Resultado label="Precio oferta" value={precioMXN(costoCalc?.precio_sugerido)} destacado acento={tema.acento} />
                </div>
                {costoFresco && costoCalc?.ganancia_neta != null && (
                  <div className="text-[11px] text-slate-500">
                    Ganancia neta <strong>{precioMXN(costoCalc.ganancia_neta)}</strong>
                    {costoCalc.roi != null && <> · ROI <strong>{Math.round((costoCalc.roi ?? 0) * 100)}%</strong></>}
                  </div>
                )}

                {/* Guardar */}
                <button
                  onClick={guardarCosto}
                  disabled={guardandoCosto || !data}
                  className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold shadow-sm transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ background: `linear-gradient(120deg, ${tema.color}, ${tema.acento})`, color: tema.texto }}
                >
                  {guardandoCosto ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                  {guardandoCosto ? "Guardando…" : "Guardar costo y precios"}
                </button>
                {costoMsg && (
                  <div className={["flex items-start gap-2 rounded-lg px-3 py-2 text-sm", costoMsg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"].join(" ")}>
                    {costoMsg.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
                    {costoMsg.texto}
                  </div>
                )}
                <p className="text-[11px] text-slate-400">
                  <strong>Regenerar</strong> recalcula sin escribir. <strong>Guardar</strong> persiste en la base y actualiza WooCommerce (precio regular/oferta, costo, peso y dimensiones).
                </p>
              </section>

              {/* ALIBABA */}
              <section className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr,180px]">
                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400"><Link2 size={13} /> URL Alibaba</label>
                  <div className="flex items-center gap-2">
                    <input value={campos.alibabaUrl} onChange={(e) => setCampo("alibabaUrl", e.target.value)} placeholder="https://www.alibaba.com/…"
                      className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                    {campos.alibabaUrl && <a href={campos.alibabaUrl} target="_blank" rel="noreferrer" className="shrink-0 rounded-lg border border-slate-200 p-2 text-slate-400 hover:bg-slate-50"><ExternalLink size={15} /></a>}
                  </div>
                </div>
                <Campo label="Precio Alibaba" prefijo="$" value={campos.alibabaPrecio} onChange={(v) => setCampo("alibabaPrecio", v)} acento={tema.acento} />
              </section>

              {/* ATRIBUTOS 1×1 (editables) — sin las filas basura de dimensiones/peso */}
              {atributosVisibles.length > 0 && (
                <section className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Atributos</div>
                  <div className="grid gap-2">
                    {atributosVisibles.map(({ a, i }) => (
                      <div key={i} className="grid grid-cols-[160px,1fr] items-center gap-3">
                        <span className="truncate text-xs font-semibold uppercase tracking-wide text-slate-400" title={a.nombre}>{a.nombre}</span>
                        <input value={a.valor} onChange={(e) => setAtributo(i, e.target.value)}
                          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* GUARDAR CONTENIDO — solo canal General (persiste a WooCommerce) */}
              {esGeneral && (
                <section className="rounded-2xl border border-slate-200 bg-white p-4">
                  <button
                    onClick={guardarContenidoWoo}
                    disabled={guardandoContenido || !data}
                    className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold text-white shadow-sm transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                    style={{ background: `linear-gradient(120deg, ${tema.color}, ${tema.acento})` }}
                  >
                    {guardandoContenido ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    {guardandoContenido ? "Guardando…" : "Guardar contenido"}
                  </button>
                  {contenidoMsg && (
                    <div className={["mt-2 flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium", contenidoMsg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"].join(" ")}>
                      {contenidoMsg.ok ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                      {contenidoMsg.texto}
                    </div>
                  )}
                  <p className="mt-1.5 text-center text-[11px] text-slate-400">
                    Guarda <strong>título, descripción y atributos</strong> en WooCommerce. Los borradores por canal se guardan solos y sobreviven al recargar la página.
                  </p>
                </section>
              )}
            </>
          )}
        </div>
      </aside>

      {/* Modal: vista previa del payload + confirmar (paso 4) */}
      {previewPub && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/60" onClick={cerrarPreview} />
          <div className="relative flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3" style={{ background: tema.suave }}>
              <div className="flex items-center gap-2">
                <UploadCloud size={18} style={{ color: tema.acento }} />
                <h3 className="text-sm font-bold text-slate-800">
                  {accionLabel} {canalInfo?.label}
                  {canal === "mercado_libre" ? " · ambas cuentas" : cuentaSel ? ` · ${cuentaSel}` : ""}
                </h3>
              </div>
              <button onClick={cerrarPreview} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
              {!previewPub.ok ? (
                <div className="rounded-lg bg-amber-50 px-3 py-3 text-sm text-amber-700">{previewPub.motivo}</div>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    {previewPub.item_id && <span className="font-mono rounded bg-slate-100 px-1.5 py-0.5">{previewPub.item_id}</span>}
                    {previewPub.product_type && <span className="font-mono rounded bg-slate-100 px-1.5 py-0.5">{previewPub.product_type}</span>}
                    <span>se enviará a {canalInfo?.label}</span>
                  </div>

                  {!!previewPub.cuentas?.length && (
                    <div className="flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="text-slate-500">Cuentas:</span>
                      {previewPub.cuentas.map((c) => (
                        <span key={c} className="rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-600">{c}</span>
                      ))}
                    </div>
                  )}

                  {previewPub.titulo && (
                    <div>
                      <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Título</div>
                      <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800">{previewPub.titulo}</div>
                    </div>
                  )}

                  {!!previewPub.cambios?.length && (
                    <div>
                      <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                        {esAmazon ? "Bullets" : "Atributos"} ({previewPub.cambios.length})
                      </div>
                      <div className="max-h-40 overflow-y-auto rounded-lg border border-slate-200">
                        {previewPub.cambios.map((c, i) => (
                          <div key={i} className="flex items-start justify-between gap-3 border-b border-slate-50 px-3 py-1.5 text-sm last:border-0">
                            <span className="shrink-0 font-mono text-[11px] uppercase text-slate-400">{c.etiqueta}</span>
                            <span className="text-right text-slate-700">{c.valor}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {previewPub.descripcion && (
                    <div>
                      <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Descripción (texto plano)</div>
                      <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 px-3 py-2 font-sans text-xs text-slate-600">{previewPub.descripcion}</pre>
                    </div>
                  )}

                  {previewPub.payload && (
                    <div>
                      <div className="mb-1 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                        Payload · {esAmazon ? "PUT /listings/2021-08-01/items" : "POST /items"}
                        <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold normal-case tracking-normal text-emerald-700">
                          {esAmazon ? "Amazon SP-API" : "publicaciones_ready"}
                        </span>
                      </div>
                      <pre className="max-h-64 overflow-auto rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-600">
                        {JSON.stringify(previewPub.payload, null, 2)}
                      </pre>
                    </div>
                  )}

                  {!!previewPub.avisos?.length && (
                    <div className="space-y-1 rounded-lg bg-amber-50 px-3 py-2">
                      {previewPub.avisos.map((a, i) => (
                        <div key={i} className="flex items-start gap-1.5 text-xs text-amber-700">
                          <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {a}
                        </div>
                      ))}
                    </div>
                  )}

                  {resultadoPub && resultadoPub.resultados?.length ? (
                    <div className="space-y-1.5">
                      {resultadoPub.resultados.map((r, i) => {
                        // Creada pero NO pausada: no es un éxito limpio, hay que avisar.
                        const activa = r.ok && r.pausado === false;
                        const tono = !r.ok
                          ? "bg-red-50 text-red-600"
                          : activa
                            ? "bg-amber-50 text-amber-700"
                            : "bg-emerald-50 text-emerald-700";
                        return (
                        <div key={i} className={["flex items-start gap-2 rounded-lg px-3 py-2 text-sm", tono].join(" ")}>
                          {r.ok && !activa ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
                          <div>
                            <strong>{r.cuenta}:</strong>{" "}
                            {!r.ok
                              ? (r.error || `HTTP ${r.ml_status ?? "?"}`)
                              : (r.modo ?? resultadoPub.modo) === "crear"
                                ? (
                                  <>
                                    publicado {r.item_id ?? ""}{" "}
                                    {r.pausado ? (
                                      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold uppercase">
                                        pausada
                                      </span>
                                    ) : (
                                      <span className="rounded bg-amber-200 px-1.5 py-0.5 text-[10px] font-bold uppercase">
                                        {r.estado_ml ?? "activa"}
                                      </span>
                                    )}
                                  </>
                                )
                                : "actualizado"}
                            {r.aviso && <div className="mt-0.5 text-xs">{r.aviso}</div>}
                          </div>
                        </div>
                        );
                      })}
                      <p className="text-[11px] text-slate-400">Registrado en <code>{resultadoPub.registrado_en}</code>.</p>
                    </div>
                  ) : resultadoPub ? (
                    <div className={["flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm", resultadoPub.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"].join(" ")}>
                      {resultadoPub.ok ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> : <AlertTriangle size={16} className="mt-0.5 shrink-0" />}
                      <div>
                        {resultadoPub.ok ? (
                          <span><strong>Publicado en {canalInfo?.label}.</strong> Registrado en <code>{resultadoPub.registrado_en}</code>.</span>
                        ) : (
                          <span><strong>No se pudo publicar.</strong> {resultadoPub.error || resultadoPub.motivo}{resultadoPub.ml_status ? ` (HTTP ${resultadoPub.ml_status})` : resultadoPub.status ? ` (${resultadoPub.status})` : ""}</span>
                        )}
                      </div>
                    </div>
                  ) : null}
                </>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
              {resultadoPub?.ok ? (
                <button onClick={cerrarPreview} className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-bold text-white hover:bg-slate-900">Cerrar</button>
              ) : (
                <>
                  <button onClick={cerrarPreview} className="rounded-lg px-4 py-2 text-sm font-semibold text-slate-500 hover:bg-slate-100">Cancelar</button>
                  {previewPub.ok && (
                    <button
                      onClick={confirmarPublicar}
                      disabled={publicando}
                      className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-sm disabled:opacity-60"
                      style={{ background: `linear-gradient(120deg, ${tema.color}, ${tema.acento})`, color: tema.texto }}
                    >
                      {publicando ? <Loader2 size={15} className="animate-spin" /> : <UploadCloud size={15} />}
                      {publicando ? (esML ? "Actualizando…" : "Publicando…") : `Confirmar y ${esML ? "actualizar" : "publicar"}`}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Campo({ label, value, onChange, acento, prefijo }: {
  label: string; value: string; onChange: (v: string) => void; acento: string; prefijo?: string;
}) {
  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">{label}</label>
      <div className="relative">
        {prefijo && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">{prefijo}</span>}
        <input value={value} onChange={(e) => onChange(e.target.value)}
          className={["w-full rounded-lg border border-slate-200 py-2.5 text-sm text-slate-800 outline-none focus:ring-2", prefijo ? "pl-7 pr-3" : "px-3"].join(" ")}
          style={{ outlineColor: acento }} />
      </div>
    </div>
  );
}

// Celda de resultado (solo lectura) del bloque COSTOS.
function Resultado({ label, value, destacado, acento }: {
  label: string; value: string; destacado?: boolean; acento?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">{label}</div>
      <div
        className={["mt-0.5 font-bold", destacado ? "text-base" : "text-sm text-slate-700"].join(" ")}
        style={destacado ? { color: acento } : undefined}
      >
        {value}
      </div>
    </div>
  );
}
