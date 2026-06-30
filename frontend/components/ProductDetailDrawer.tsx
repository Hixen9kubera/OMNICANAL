"use client";

import { useEffect, useState } from "react";
import {
  X,
  ExternalLink,
  RefreshCw,
  Truck,
  Boxes,
  Tag,
  ChevronRight,
  ImageIcon,
} from "lucide-react";
import type { CanalInfo, DetalleProducto } from "@/lib/types";
import { detalleProducto, refrescarCanal } from "@/lib/api";

interface Props {
  sku: string | null;
  canales: CanalInfo[];
  onClose: () => void;
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
  }).format(v);
}

export default function ProductDetailDrawer({ sku, canales, onClose }: Props) {
  const [data, setData] = useState<DetalleProducto | null>(null);
  const [cargando, setCargando] = useState(false);
  const [refrescando, setRefrescando] = useState<string | null>(null);

  const cfg = (id: string) => canales.find((c) => c.id === id);

  useEffect(() => {
    if (!sku) return;
    const ctrl = new AbortController();
    setCargando(true);
    setData(null);
    detalleProducto(sku, ctrl.signal)
      .then(setData)
      .catch(() => {})
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [sku]);

  // Cerrar con ESC
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (sku) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sku, onClose]);

  async function refrescar(canal: string) {
    if (!sku) return;
    setRefrescando(canal);
    try {
      await refrescarCanal(canal, sku);
      const fresco = await detalleProducto(sku);
      setData(fresco);
    } catch {
      /* el botón solo aplica a ML/Amazon con publicación */
    } finally {
      setRefrescando(null);
    }
  }

  if (!sku) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Panel */}
      <aside className="relative flex h-full w-full max-w-xl animate-slide-in flex-col bg-slate-50 shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-6 py-5">
          <div className="flex gap-4">
            <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
              {data?.imagen ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={data.imagen} alt="" className="h-full w-full object-contain p-1" />
              ) : (
                <ImageIcon className="text-slate-300" />
              )}
            </div>
            <div>
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-500">
                {sku}
              </span>
              <h2 className="mt-1.5 line-clamp-3 text-base font-bold leading-snug text-slate-900">
                {data?.nombre ?? "Cargando…"}
              </h2>
              {data?.marca && (
                <span className="text-xs text-slate-400">{data.marca}</span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <X size={20} />
          </button>
        </div>

        {/* Contenido */}
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          {cargando && (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-28 animate-pulse rounded-xl bg-white" />
              ))}
            </div>
          )}

          {data?.canales.map((c) => {
            const info = cfg(c.canal);
            const color = info?.color ?? "#64748b";
            const texto = info?.color_texto ?? "#fff";
            const refrescable = c.canal === "mercado_libre" || c.canal === "amazon";

            return (
              <section
                key={c.canal}
                className="overflow-hidden rounded-xl border border-slate-200 bg-white"
              >
                {/* Encabezado del canal */}
                <header
                  className="flex items-center justify-between px-4 py-2.5"
                  style={{ backgroundColor: color, color: texto }}
                >
                  <div className="flex items-center gap-2 font-bold">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: texto }}
                    />
                    {info?.label ?? c.canal}
                  </div>
                  <div className="flex items-center gap-2">
                    {c.publicado ? (
                      <span className="rounded-full bg-white/25 px-2 py-0.5 text-[11px] font-bold">
                        {c.estado ?? "publicado"}
                      </span>
                    ) : (
                      <span className="rounded-full bg-black/20 px-2 py-0.5 text-[11px] font-bold">
                        sin publicar
                      </span>
                    )}
                    {refrescable && (
                      <button
                        onClick={() => refrescar(c.canal)}
                        title="Refrescar en vivo desde la API"
                        className="rounded-full bg-white/20 p-1.5 transition-colors hover:bg-white/35"
                      >
                        <RefreshCw
                          size={13}
                          className={refrescando === c.canal ? "animate-spin" : ""}
                        />
                      </button>
                    )}
                  </div>
                </header>

                {/* Métricas por canal:
                    - Mercado Libre usa FULL (no FBA)
                    - Amazon usa FBA (no FULL)
                    - General solo stock propio */}
                {(() => {
                  const esML = c.canal === "mercado_libre";
                  const esAmazon = c.canal === "amazon";
                  const stockReal = c.stock_real ?? c.stock;
                  return (
                    <>
                      <div
                        className={[
                          "grid divide-x divide-slate-100 border-b border-slate-100",
                          esML || esAmazon ? "grid-cols-3" : "grid-cols-2",
                        ].join(" ")}
                      >
                        <Metric icon={<Tag size={14} />} label="Precio" valor={precioMXN(c.precio)} />
                        <Metric
                          icon={<Boxes size={14} />}
                          label="Stock real"
                          valor={stockReal != null ? `${stockReal} u` : "—"}
                        />
                        {esML && (
                          <Metric
                            icon={<Truck size={14} />}
                            label="FULL"
                            valor={c.stock_full != null ? `${c.stock_full} u` : "—"}
                            destacado={!!c.stock_full}
                          />
                        )}
                        {esAmazon && (
                          <Metric
                            icon={<Truck size={14} />}
                            label="FBA"
                            valor={c.stock_fba != null ? `${c.stock_fba} u` : "—"}
                            destacado={!!c.stock_fba}
                          />
                        )}
                      </div>

                      {/* Total y situación */}
                      {(c.stock_real != null || c.stock_full != null || c.stock_fba != null) && (
                        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-xs">
                          <span className="text-slate-500">
                            Total ={" "}
                            <span className="font-bold text-slate-700">
                              {(c.stock_real ?? 0) + (c.stock_full ?? 0) + (c.stock_fba ?? 0)} u
                            </span>{" "}
                            <span className="text-slate-400">
                              {esML ? "(real + FULL)" : esAmazon ? "(real + FBA)" : ""}
                            </span>
                          </span>
                          {c.situacion && (
                            <span className="rounded-full bg-slate-200 px-2 py-0.5 font-semibold uppercase tracking-wide text-slate-600">
                              {c.situacion}
                            </span>
                          )}
                        </div>
                      )}
                    </>
                  );
                })()}

                {/* Categoría multinivel */}
                <div className="px-4 py-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    Categoría
                  </div>
                  {c.categoria_path.length ? (
                    <div className="cat-breadcrumb text-sm text-slate-700">
                      {c.categoria_path.map((n, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <ChevronRight size={13} className="text-slate-300" />}
                          <span className="font-medium">{n.nombre}</span>
                        </span>
                      ))}
                    </div>
                  ) : c.categoria_id ? (
                    <span className="font-mono text-sm text-slate-600">{String(c.categoria_id)}</span>
                  ) : (
                    <span className="text-sm text-slate-400">Sin categoría</span>
                  )}
                </div>

                {/* Footer: id + link */}
                {(c.item_id || c.url) && (
                  <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50 px-4 py-2.5">
                    <span className="font-mono text-xs text-slate-500">
                      {c.item_id ?? ""}
                    </span>
                    {c.url && (
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 text-xs font-semibold"
                        style={{ color }}
                      >
                        Ver publicación <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </aside>
    </div>
  );
}

function Metric({
  icon,
  label,
  valor,
  destacado,
}: {
  icon: React.ReactNode;
  label: string;
  valor: string;
  destacado?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-2 py-3 text-center">
      <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        {icon} {label}
      </span>
      <span
        className={[
          "text-sm font-bold",
          destacado ? "text-emerald-600" : "text-slate-800",
        ].join(" ")}
      >
        {valor}
      </span>
    </div>
  );
}
