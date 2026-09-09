"use client";

import { Fragment, useState } from "react";
import { ImageIcon, PackageCheck, PackageX, Truck, PackageSearch, Loader2 } from "lucide-react";
import type { Producto } from "@/lib/types";
import ChannelDots from "./ChannelDots";
import { esPadre, TipoBadge, VariantesBoton, VariantesTabla } from "./Variantes";

interface Props {
  productos: Producto[];
  esGeneral: boolean;
  cargando: boolean;
  preparando?: boolean;
  color: string;
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
  onSelect: (p: Producto) => void;
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    minimumFractionDigits: 2, maximumFractionDigits: 2,   // precios SIEMPRE con 2 decimales (Brandon, 29-jul)
  }).format(v);
}

export default function ProductList({
  productos,
  esGeneral,
  cargando,
  preparando,
  color,
  colorMap,
  labelMap,
  onSelect,
}: Props) {
  // Padres con su lista de variantes desplegada (mecánica de Crear Productos)
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());

  function toggleExpandido(sku: string) {
    setExpandidos((prev) => {
      const s = new Set(prev);
      if (s.has(sku)) s.delete(sku);
      else s.add(sku);
      return s;
    });
  }

  if (cargando) {
    return (
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="flex animate-pulse items-center gap-4 border-b border-slate-100 px-4 py-3">
            <div className="h-12 w-12 rounded-lg bg-slate-100" />
            <div className="h-4 w-24 rounded bg-slate-100" />
            <div className="h-4 flex-1 rounded bg-slate-100" />
            <div className="h-4 w-20 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    );
  }

  if (productos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-200 bg-white py-24 text-center">
        {preparando ? (
          <>
            <Loader2 size={40} className="animate-spin text-slate-300" strokeWidth={1.3} />
            <p className="text-base font-semibold text-slate-600">
              Preparando el catálogo de WooCommerce…
            </p>
          </>
        ) : (
          <>
            <PackageSearch size={48} className="text-slate-300" strokeWidth={1.3} />
            <p className="text-base font-semibold text-slate-600">
              No se encontraron productos
            </p>
            <p className="text-sm text-slate-400">
              Prueba con otra búsqueda o cambia de canal.
            </p>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-card">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
            <th className="px-4 py-3 font-semibold">Producto</th>
            {esGeneral && <th className="px-3 py-3 text-center font-semibold">Tipo</th>}
            {esGeneral && <th className="px-3 py-3 text-center font-semibold">Variantes</th>}
            <th className="px-3 py-3 font-semibold">Categoría</th>
            {/* Mismo reparto que la tarjeta (v0.438.0–v0.447.0): General es el
                catálogo y enseña el COSTO; los canales, el precio de la
                publicación. */}
            <th className="px-3 py-3 text-right font-semibold">{esGeneral ? "Costo" : "Precio"}</th>
            <th className="px-3 py-3 text-center font-semibold">Stock</th>
            <th className="px-3 py-3 text-center font-semibold">Estado</th>
            <th className="px-4 py-3 text-center font-semibold">
              {esGeneral ? "Canales" : "Logística"}
            </th>
          </tr>
        </thead>
        <tbody>
          {productos.map((p) => {
            const sinStock = p.stock_real !== null && (p.stock_real ?? 0) <= 0;
            const padre = esGeneral && esPadre(p);
            const abierto = padre && expandidos.has(p.sku);
            return (
              <Fragment key={`${p.sku}-${p.item_id ?? ""}`}>
              <tr
                onClick={() => onSelect(p)}
                className="cursor-pointer border-b border-slate-100 transition-colors hover:bg-slate-50"
              >
                {/* Producto */}
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
                      {p.imagen ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.imagen} alt="" loading="lazy" className="h-full w-full object-contain" />
                      ) : (
                        <ImageIcon size={20} className="text-slate-300" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate font-semibold text-slate-800">{p.nombre}</div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="font-mono text-[11px] text-slate-400">{p.sku}</span>
                        {/* DROP OFF: mismo distintivo y mismo violeta que en el
                            mosaico y en la pestaña Inventario. */}
                        {p.drop_off && (
                          <span
                            title="Tiene existencias en el almacén DROP OFF de Odoo — el almacén del que salen los envíos a marketplaces chinos."
                            className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700"
                          >
                            DROP OFF
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </td>
                {/* Tipo: Padre o Único */}
                {esGeneral && (
                  <td className="px-3 py-2.5 text-center">
                    <TipoBadge padre={padre} />
                  </td>
                )}
                {/* Variantes: número + ver variantes */}
                {esGeneral && (
                  <td
                    className="px-3 py-2.5 text-center"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {padre ? (
                      <VariantesBoton
                        n={p.variantes.length}
                        abierto={abierto}
                        onClick={() => toggleExpandido(p.sku)}
                      />
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </td>
                )}
                {/* Categoría */}
                <td className="px-3 py-2.5 text-xs text-slate-500">
                  <span className="line-clamp-1">
                    {p.categoria_path.map((c) => c.nombre).join(" › ") || "—"}
                  </span>
                </td>
                {/* Costo (General) o precio de la publicación (canales), con
                    las mismas reglas que la tarjeta:
                    · General: costo unitario; padre sin costeo propio → rango
                      de sus variantes; sin costeo → "Sin costo".
                    · Canales: lo que cobra con su lista tachada; padre → rango
                      de lo que cobran sus variantes en esa cuenta. */}
                <td className="px-3 py-2.5 text-right font-bold text-slate-900">
                  {esGeneral ? (
                    p.costo_rango ? (
                      <span title={`Costo de sus variantes (${p.costo_rango.n} de ${p.costo_rango.total} con costeo en Costos). El padre no tiene costeo propio.`}>
                        {p.costo_rango.min === p.costo_rango.max
                          ? precioMXN(p.costo_rango.min)
                          : `${precioMXN(p.costo_rango.min)} – ${precioMXN(p.costo_rango.max)}`}
                        <span className="ml-1 text-[10px] font-normal uppercase tracking-wide text-slate-400">variantes</span>
                      </span>
                    ) : p.costo != null && p.costo > 0 ? (
                      <span title="Costo unitario de la pieza (producto + flete), el validado en Costos.">
                        {precioMXN(p.costo)}
                      </span>
                    ) : (
                      <span className="font-normal text-slate-300" title="Este SKU no tiene costeo en Costos.">Sin costo</span>
                    )
                  ) : p.precio_rango ? (
                    <span title={`Lo que cobran sus variantes en esta cuenta (${p.precio_rango.n} de ${p.precio_rango.total} con publicación viva). La publicación del padre cobra ${precioMXN(p.precio)}.`}>
                      {p.precio_rango.min === p.precio_rango.max
                        ? precioMXN(p.precio_rango.min)
                        : `${precioMXN(p.precio_rango.min)} – ${precioMXN(p.precio_rango.max)}`}
                      <span className="ml-1 text-[10px] font-normal uppercase tracking-wide text-slate-400">
                        {p.precio_rango.n} variante{p.precio_rango.n === 1 ? "" : "s"}
                      </span>
                    </span>
                  ) : (
                    <>
                      {precioMXN(p.precio)}
                      {p.precio_base && p.precio && p.precio_base > p.precio && (
                        <div className="text-[11px] font-normal text-slate-400 line-through">
                          {precioMXN(p.precio_base)}
                        </div>
                      )}
                    </>
                  )}
                </td>
                {/* Stock */}
                <td className="px-3 py-2.5 text-center">
                  <div className="flex flex-col items-center gap-0.5">
                    <span className={sinStock ? "font-bold text-red-600" : "font-bold text-emerald-600"}>
                      {p.stock_real ?? p.stock ?? "—"}
                    </span>
                    <div className="flex gap-1">
                      {!!p.stock_full && p.stock_full > 0 && (
                        <span className="rounded bg-amber-100 px-1 text-[9px] font-bold text-amber-700">
                          FULL {p.stock_full}
                        </span>
                      )}
                      {!!p.stock_fba && p.stock_fba > 0 && (
                        <span className="rounded bg-sky-100 px-1 text-[9px] font-bold text-sky-700">
                          FBA {p.stock_fba}
                        </span>
                      )}
                    </div>
                  </div>
                </td>
                {/* Estado */}
                <td className="px-3 py-2.5 text-center">
                  {p.publicado ? (
                    <span
                      className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold text-white"
                      style={{ backgroundColor: color }}
                    >
                      <PackageCheck size={11} /> Publicado
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600">
                      <PackageX size={11} /> {p.situacion ?? "Sin publicar"}
                    </span>
                  )}
                </td>
                {/* Canales / Logística */}
                <td className="px-4 py-2.5">
                  <div className="flex justify-center">
                    {esGeneral ? (
                      <ChannelDots canales={p.canales} colorMap={colorMap} labelMap={labelMap} />
                    ) : p.full ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                        <Truck size={11} /> {p.full_label}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </div>
                </td>
              </tr>

              {/* Variantes del padre (desplegable) */}
              {abierto && (
                <tr className="border-b border-slate-100 bg-violet-50/40">
                  <td colSpan={8} className="px-4 pb-4 pt-1">
                    <VariantesTabla
                      variantes={p.variantes}
                      colorMap={colorMap}
                      labelMap={labelMap}
                    />
                  </td>
                </tr>
              )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
