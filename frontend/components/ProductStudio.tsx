"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  X,
  ChevronRight,
  ImageIcon,
  Plus,
  ExternalLink,
  Wand2,
  Loader2,
  Link2,
  TrendingUp,
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import type {
  AtributoProducto,
  CanalInfo,
  CompetenciaResp,
  DetalleCanal,
  Producto,
  PublicarPreview,
  PublicarResultado,
  StudioMetadata,
} from "@/lib/types";
import {
  mejorarIA,
  precioCompetencia,
  publicarConfirmar,
  publicarPreview,
  studioMetadata,
  type ProductoIA,
} from "@/lib/api";
import { useDetalleProducto } from "@/lib/useDetalleProducto";
import {
  getMejora,
  setMejora as saveMejora,
  getCompetencia as getCompStore,
  setCompetencia as saveCompetencia,
} from "@/lib/studioStore";
import { THEME_FALLBACK, hexToRgba, variablesTema, type CanalTheme } from "@/lib/theme";

interface Props {
  sku: string | null;
  producto?: Producto | null;
  canales: CanalInfo[];
  onClose: () => void;
}

const GENERAL = "general";
const AMAZON = "amazon";

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

export default function ProductStudio({ sku, producto, canales, onClose }: Props) {
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
  }, [sku]);

  // ── Metadata del Estudio (postmeta): precios/costo/alibaba/dims ─────
  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    studioMetadata(sku, producto?.wc_id ?? null, ctrl.signal)
      .then((m) => {
        setMeta(m);
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

  const categoriaMLTexto = meta?.categoria_ml?.niveles?.join(" › ") || null;

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

  // ── Mejorar con IA (un botón por canal) ─────────────────────────────
  const mejorarConIA = useCallback(async () => {
    if (!data || !sku) return;
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
      atributos,
    };
    const [mej, comp] = await Promise.allSettled([
      mejorarIA({ canal, producto: ctx }),
      precioCompetencia({ producto: ctx, con_lista: true }),
    ]);

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

  if (!sku) return null;

  const imagenes = data?.imagenes?.length ? data.imagenes : data?.imagen ? [data.imagen] : [];

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

              {/* CATEGORÍA (Mercado Libre con subniveles + WooCommerce) */}
              <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
                {meta?.categoria_ml?.niveles?.length ? (
                  <div>
                    <div className="mb-1.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                      Categoría Mercado Libre
                      {meta.categoria_ml.category_id && <span className="rounded bg-slate-100 px-1.5 font-mono text-[10px] normal-case tracking-normal text-slate-500">{meta.categoria_ml.category_id}</span>}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 text-sm font-semibold">
                      {meta.categoria_ml.niveles.map((n, i, arr) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <ChevronRight size={14} className="text-slate-300" />}
                          <span className={i === arr.length - 1 ? "" : "text-slate-500"} style={i === arr.length - 1 ? { color: tema.acento } : undefined}>{n}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className={meta?.categoria_ml?.niveles?.length ? "border-t border-slate-100 pt-3" : ""}>
                  <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Categoría WooCommerce</div>
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

              {/* Galería */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-3 flex h-64 items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-white">
                  {imagenes[imgActiva] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={imagenes[imgActiva]} alt="" className="h-full w-full object-contain" />
                  ) : (<ImageIcon size={48} className="text-slate-200" />)}
                </div>
                <div className="flex flex-wrap gap-2">
                  {imagenes.map((src, i) => (
                    <button key={i} onClick={() => setImgActiva(i)}
                      className={["h-16 w-16 overflow-hidden rounded-lg border-2 transition-colors", i === imgActiva ? "" : "border-slate-200 hover:border-slate-300"].join(" ")}
                      style={i === imgActiva ? { borderColor: tema.color } : undefined}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={src} alt="" className="h-full w-full object-contain" />
                    </button>
                  ))}
                  <div className="flex h-16 w-16 items-center justify-center rounded-lg border-2 border-dashed border-slate-200 text-slate-300"><Plus size={20} /></div>
                </div>
              </section>

              {/* TÍTULO */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-1.5 flex items-center justify-between">
                  <label className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Título</label>
                  <span className="text-[11px] text-slate-400">{titulo.length} car.</span>
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

              {/* PESO + DIMENSIONES */}
              <section className="grid grid-cols-1 gap-4 sm:grid-cols-[160px,1fr]">
                <Campo label="Peso (kg)" value={campos.peso} onChange={(v) => setCampo("peso", v)} acento={tema.acento} />
                <div>
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Largo × Ancho × Alto (cm)
                    {meta?.dinero?.volumen_m3 != null && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-amber-700">{meta.dinero.volumen_m3} m³</span>}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {(["largo", "ancho", "alto"] as const).map((k) => (
                      <input key={k} value={campos[k]} onChange={(e) => setCampo(k, e.target.value)}
                        className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                    ))}
                  </div>
                </div>
              </section>

              {/* ATRIBUTOS 1×1 (editables) */}
              {atributos.length > 0 && (
                <section className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Atributos</div>
                  <div className="grid gap-2">
                    {atributos.map((a, i) => (
                      <div key={i} className="grid grid-cols-[160px,1fr] items-center gap-3">
                        <span className="truncate text-xs font-semibold uppercase tracking-wide text-slate-400" title={a.nombre}>{a.nombre}</span>
                        <input value={a.valor} onChange={(e) => setAtributo(i, e.target.value)}
                          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: tema.acento }} />
                      </div>
                    ))}
                  </div>
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
                      {resultadoPub.resultados.map((r, i) => (
                        <div key={i} className={["flex items-start gap-2 rounded-lg px-3 py-2 text-sm", r.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"].join(" ")}>
                          {r.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
                          <div>
                            <strong>{r.cuenta}:</strong>{" "}
                            {r.ok
                              ? resultadoPub.modo === "crear"
                                ? `publicado ${r.item_id ?? ""}`
                                : "actualizado"
                              : (r.error || `HTTP ${r.ml_status ?? "?"}`)}
                          </div>
                        </div>
                      ))}
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
