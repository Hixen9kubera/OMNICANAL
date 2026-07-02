"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  X,
  ChevronRight,
  ImageIcon,
  Plus,
  ExternalLink,
  Copy,
  Check,
  Wand2,
  Loader2,
  Type,
  Sparkles,
  List,
  AlignLeft,
  Tags,
  type LucideIcon,
} from "lucide-react";
import type {
  CanalInfo,
  DetalleCanal,
  DetalleProducto,
  GeneradorDef,
  GenerarIAResp,
} from "@/lib/types";
import { detalleProducto, generadoresIA, generarIA } from "@/lib/api";
import { THEME_FALLBACK, hexToRgba, variablesTema, type CanalTheme } from "@/lib/theme";

interface Props {
  sku: string | null;
  canales: CanalInfo[];
  onClose: () => void;
}

const GENERAL = "general";

const ICONOS: Record<string, LucideIcon> = {
  type: Type,
  sparkles: Sparkles,
  list: List,
  "align-left": AlignLeft,
  tags: Tags,
  image: ImageIcon,
};

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
  }).format(v);
}

interface Resultado extends GenerarIAResp {
  key: string;
}

export default function ProductStudio({ sku, canales, onClose }: Props) {
  const [data, setData] = useState<DetalleProducto | null>(null);
  const [cargando, setCargando] = useState(false);
  const [canal, setCanal] = useState<string>(GENERAL);

  // Campos editables (prototipo): se precargan de WooCommerce.
  const [titulo, setTitulo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [imgActiva, setImgActiva] = useState(0);

  // IA
  const [generadores, setGeneradores] = useState<GeneradorDef[]>([]);
  const [generando, setGenerando] = useState<string | null>(null);
  const [resultados, setResultados] = useState<Resultado[]>([]);
  const [copiado, setCopiado] = useState<string | null>(null);

  // ── Tema del canal seleccionado ────────────────────────────────────
  const canalInfo = useMemo(() => canales.find((c) => c.id === canal), [canales, canal]);
  const tema: CanalTheme = useMemo(() => {
    const fb = THEME_FALLBACK[canal] ?? THEME_FALLBACK.general;
    if (!canalInfo) return fb;
    return {
      color: canalInfo.color,
      texto: canalInfo.color_texto,
      acento: canalInfo.acento,
      suave: fb.suave ?? hexToRgba(canalInfo.color, 0.1),
    };
  }, [canalInfo, canal]);

  // ── Carga del detalle ──────────────────────────────────────────────
  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    setCargando(true);
    setData(null);
    setCanal(GENERAL);
    setResultados([]);
    detalleProducto(sku, ctrl.signal)
      .then((d) => {
        setData(d);
        setTitulo(d.nombre ?? "");
        setDescripcion(d.descripcion ?? "");
        setImgActiva(0);
      })
      .catch(() => {})
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [sku]);

  // ── Generadores del canal activo ───────────────────────────────────
  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    generadoresIA(canal, ctrl.signal)
      .then((r) => setGeneradores(r.generadores))
      .catch(() => setGeneradores([]));
    return () => ctrl.abort();
  }, [canal, sku]);

  // Cerrar con ESC
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (sku) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sku, onClose]);

  // ── Datos del canal seleccionado ───────────────────────────────────
  const datosCanal: DetalleCanal | undefined = useMemo(
    () => data?.canales.find((c) => c.canal === canal),
    [data, canal],
  );
  const esGeneral = canal === GENERAL;

  // Categoría: la del canal activo si existe, si no la general (WooCommerce).
  const categoria = useMemo(() => {
    const general = data?.canales.find((c) => c.canal === GENERAL);
    const path = datosCanal?.categoria_path?.length
      ? datosCanal.categoria_path
      : general?.categoria_path ?? [];
    return path;
  }, [data, datosCanal]);

  const categoriaTexto = categoria.map((c) => c.nombre).join(" › ");

  const generar = useCallback(
    async (g: GeneradorDef) => {
      if (!data) return;
      setGenerando(g.id);
      try {
        const res = await generarIA({
          canal,
          generador: g.id,
          producto: {
            nombre: titulo || data.nombre,
            marca: data.marca,
            categoria: categoriaTexto || null,
            descripcion: descripcion || data.descripcion,
            precio: datosCanal?.precio ?? data.precio_base ?? null,
            atributos: data.atributos,
          },
        });
        setResultados((prev) => [
          { ...res, key: `${g.id}-${prev.length}` },
          ...prev,
        ]);
      } catch {
        setResultados((prev) => [
          {
            ok: false,
            motivo: "No se pudo generar. Revisa la conexión o la clave de IA.",
            canal,
            generador: g.id,
            label: g.label,
            tipo: "texto",
            key: `${g.id}-err-${prev.length}`,
          },
          ...prev,
        ]);
      } finally {
        setGenerando(null);
      }
    },
    [data, canal, titulo, descripcion, categoriaTexto, datosCanal],
  );

  function copiar(texto: string, key: string) {
    navigator.clipboard?.writeText(texto).then(() => {
      setCopiado(key);
      setTimeout(() => setCopiado(null), 1500);
    });
  }

  function usar(res: Resultado) {
    if (res.generador === "titulo") setTitulo(res.texto ?? "");
    else if (res.generador === "descripcion") setDescripcion(res.texto ?? "");
  }

  if (!sku) return null;

  const imagenes = data?.imagenes?.length ? data.imagenes : data?.imagen ? [data.imagen] : [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={variablesTema(tema)}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <aside className="relative flex h-full w-full max-w-[960px] animate-slide-in flex-col bg-slate-50 shadow-2xl">
        {/* Header temático por canal */}
        <div
          className="flex items-start justify-between gap-3 px-6 py-4 transition-colors duration-300"
          style={{
            background: `linear-gradient(120deg, ${tema.color} 0%, ${hexToRgba(tema.acento, 0.92)} 100%)`,
            color: tema.texto,
          }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-white/90">
              {data?.imagen ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={data.imagen} alt="" className="h-full w-full object-contain p-1" />
              ) : (
                <ImageIcon className="text-slate-300" />
              )}
            </div>
            <div className="min-w-0">
              <span className="rounded-md bg-black/15 px-1.5 py-0.5 font-mono text-[11px] font-semibold">
                {sku}
              </span>
              <h2 className="mt-1 line-clamp-2 text-base font-bold leading-snug">
                {data?.nombre ?? "Cargando…"}
              </h2>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 transition-colors hover:bg-black/15"
          >
            <X size={20} />
          </button>
        </div>

        {/* Selector de canal a editar (recolorea todo el panel) */}
        <div className="border-b border-slate-200 bg-white px-6 py-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
            Selecciona canal a editar
          </div>
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
                  style={
                    sel
                      ? { backgroundColor: c.color, color: c.color_texto, borderColor: c.color }
                      : undefined
                  }
                  className={[
                    "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-semibold transition-all",
                    sel
                      ? "scale-[1.03] shadow-sm"
                      : off
                        ? "cursor-not-allowed border-dashed border-slate-200 bg-slate-50 text-slate-400"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: sel ? c.color_texto : c.color }}
                  />
                  {c.label}
                  {off && (
                    <span className="rounded-full bg-slate-200 px-1.5 text-[9px] font-bold uppercase text-slate-500">
                      Pronto
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Cuerpo */}
        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {cargando && (
            <div className="space-y-4">
              <div className="h-6 w-1/3 animate-pulse rounded bg-white" />
              <div className="h-52 animate-pulse rounded-2xl bg-white" />
              <div className="h-32 animate-pulse rounded-2xl bg-white" />
            </div>
          )}

          {data && !cargando && (
            <>
              {/* Estado en el canal seleccionado (solo marketplaces) */}
              {!esGeneral && (
                <div
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3"
                  style={{ borderColor: hexToRgba(tema.color, 0.4), background: tema.suave }}
                >
                  {datosCanal?.publicado ? (
                    <>
                      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
                        <span className="font-bold" style={{ color: tema.acento }}>
                          Publicado en {canalInfo?.label}
                        </span>
                        <span className="text-slate-600">
                          Precio: <strong>{precioMXN(datosCanal.precio)}</strong>
                        </span>
                        <span className="text-slate-600">
                          Stock: <strong>{datosCanal.stock ?? datosCanal.stock_real ?? "—"}</strong>
                        </span>
                        {datosCanal.full && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-bold text-emerald-700">
                            {datosCanal.full_label ?? "FULL"}
                          </span>
                        )}
                      </div>
                      {datosCanal.url && (
                        <a
                          href={datosCanal.url}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1 text-xs font-semibold"
                          style={{ color: tema.acento }}
                        >
                          Ver publicación <ExternalLink size={12} />
                        </a>
                      )}
                    </>
                  ) : (
                    <span className="text-sm font-medium text-slate-500">
                      Este producto <strong>no está publicado</strong> en {canalInfo?.label}. Puedes
                      generar el contenido optimizado para publicarlo.
                    </span>
                  )}
                </div>
              )}

              {/* CATEGORÍA (primero, como pediste) */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-1.5 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                  Categoría {esGeneral ? "· WooCommerce" : `· ${canalInfo?.label}`}
                </div>
                {categoria.length ? (
                  <div className="flex flex-wrap items-center gap-1 text-sm font-semibold text-slate-800">
                    {categoria.map((n, i) => (
                      <span key={i} className="flex items-center gap-1">
                        {i > 0 && <ChevronRight size={14} className="text-slate-300" />}
                        <span
                          className={i === categoria.length - 1 ? "" : "text-slate-500"}
                          style={i === categoria.length - 1 ? { color: tema.acento } : undefined}
                        >
                          {n.nombre}
                        </span>
                      </span>
                    ))}
                  </div>
                ) : (
                  <span className="text-sm text-slate-400">Sin categoría asignada</span>
                )}
                {datosCanal?.categoria_id && !esGeneral && (
                  <span className="mt-1 inline-block font-mono text-[11px] text-slate-400">
                    {String(datosCanal.categoria_id)}
                  </span>
                )}
              </section>

              {/* Galería de imágenes */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-3 flex h-64 items-center justify-center overflow-hidden rounded-xl border border-slate-100 bg-white">
                  {imagenes[imgActiva] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={imagenes[imgActiva]} alt="" className="h-full w-full object-contain" />
                  ) : (
                    <ImageIcon size={48} className="text-slate-200" />
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {imagenes.map((src, i) => (
                    <button
                      key={i}
                      onClick={() => setImgActiva(i)}
                      className={[
                        "h-16 w-16 overflow-hidden rounded-lg border-2 transition-colors",
                        i === imgActiva ? "" : "border-slate-200 hover:border-slate-300",
                      ].join(" ")}
                      style={i === imgActiva ? { borderColor: tema.color } : undefined}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={src} alt="" className="h-full w-full object-contain" />
                    </button>
                  ))}
                  <div className="flex h-16 w-16 items-center justify-center rounded-lg border-2 border-dashed border-slate-200 text-slate-300">
                    <Plus size={20} />
                  </div>
                </div>
              </section>

              {/* TÍTULO */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-1.5 flex items-center justify-between">
                  <label className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Título
                  </label>
                  <span className="text-[11px] text-slate-400">{titulo.length} car.</span>
                </div>
                <input
                  value={titulo}
                  onChange={(e) => setTitulo(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-800 outline-none focus:ring-2"
                  style={{ outlineColor: tema.acento }}
                />
              </section>

              {/* DESCRIPCIÓN */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4">
                <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                  Descripción
                </label>
                <textarea
                  value={descripcion}
                  onChange={(e) => setDescripcion(e.target.value)}
                  rows={6}
                  className="w-full resize-y rounded-lg border border-slate-200 px-3 py-2.5 text-sm leading-relaxed text-slate-700 outline-none focus:ring-2"
                  style={{ outlineColor: tema.acento }}
                />
              </section>

              {/* PRECIO */}
              <section className="grid grid-cols-2 gap-4">
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    {esGeneral ? "Precio regular" : `Precio en ${canalInfo?.label}`}
                  </div>
                  <div className="mt-1 text-xl font-black text-slate-800">
                    {precioMXN(esGeneral ? data.precio_base : datosCanal?.precio)}
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Precio oferta
                  </div>
                  <div className="mt-1 text-xl font-black text-emerald-600">
                    {precioMXN(data.precio_oferta)}
                  </div>
                </div>
              </section>

              {/* ATRIBUTOS */}
              {data.atributos.length > 0 && (
                <section className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Atributos
                  </div>
                  <div className="grid gap-1.5">
                    {data.atributos.map((a, i) => (
                      <div key={i} className="flex items-center gap-3 text-sm">
                        <span className="w-40 shrink-0 truncate font-semibold uppercase tracking-wide text-slate-400">
                          {a.nombre}
                        </span>
                        <span className="flex-1 truncate text-slate-700">{a.valor || "—"}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* GENERADORES IA — actualizar contenido para el canal */}
              <section
                className="rounded-2xl border-2 p-4"
                style={{ borderColor: hexToRgba(tema.color, 0.5), background: tema.suave }}
              >
                <div className="mb-1 flex items-center gap-2">
                  <Wand2 size={18} style={{ color: tema.acento }} />
                  <h3 className="text-sm font-bold text-slate-800">
                    Actualizar contenido para {canalInfo?.label}
                  </h3>
                </div>
                <p className="mb-3 text-xs text-slate-500">
                  Cada canal tiene su propio agente y prompt. Genera solo lo que necesites.
                </p>

                {generadores.length === 0 ? (
                  <div className="rounded-lg bg-white px-3 py-4 text-center text-sm text-slate-400">
                    Este canal aún no tiene generadores configurados.
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {generadores.map((g) => {
                      const Icon = ICONOS[g.icono] ?? Sparkles;
                      const activo = generando === g.id;
                      return (
                        <button
                          key={g.id}
                          onClick={() => generar(g)}
                          disabled={!!generando}
                          title={g.descripcion}
                          className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-card disabled:cursor-not-allowed disabled:opacity-60"
                          style={{ color: tema.acento }}
                        >
                          {activo ? (
                            <Loader2 size={16} className="animate-spin" />
                          ) : (
                            <Icon size={16} />
                          )}
                          {g.label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </section>

              {/* RESULTADOS IA */}
              {resultados.length > 0 && (
                <section className="space-y-3">
                  <div className="text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
                    Resultados de IA
                  </div>
                  {resultados.map((r) => (
                    <div
                      key={r.key}
                      className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
                    >
                      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
                        <div className="flex items-center gap-2">
                          <span
                            className="rounded-full px-2 py-0.5 text-[11px] font-bold text-white"
                            style={{ backgroundColor: tema.color, color: tema.texto }}
                          >
                            {r.label}
                          </span>
                          {r.proveedor && (
                            <span className="text-[10px] uppercase tracking-wide text-slate-400">
                              {r.proveedor}
                            </span>
                          )}
                        </div>
                        {r.ok && r.texto && (
                          <div className="flex items-center gap-1">
                            {(r.generador === "titulo" || r.generador === "descripcion") && (
                              <button
                                onClick={() => usar(r)}
                                className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100"
                              >
                                Usar
                              </button>
                            )}
                            <button
                              onClick={() => copiar(r.texto!, r.key)}
                              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100"
                            >
                              {copiado === r.key ? (
                                <>
                                  <Check size={13} className="text-emerald-500" /> Copiado
                                </>
                              ) : (
                                <>
                                  <Copy size={13} /> Copiar
                                </>
                              )}
                            </button>
                          </div>
                        )}
                      </div>
                      <div className="px-4 py-3">
                        {r.ok ? (
                          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-700">
                            {r.texto}
                          </pre>
                        ) : (
                          <p className="text-sm text-red-500">{r.motivo}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
