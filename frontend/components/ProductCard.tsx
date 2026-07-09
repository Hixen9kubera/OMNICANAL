"use client";

import { useEffect, useRef, useState } from "react";
import { ImageIcon, PackageCheck, PackageX, Truck, X } from "lucide-react";
import type { Producto } from "@/lib/types";
import ChannelDots from "./ChannelDots";
import { esPadre, TipoBadge, VariantesBoton, VariantesTabla } from "./Variantes";

interface Props {
  producto: Producto;
  canal: string;
  esGeneral: boolean;
  color: string;
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
  onClick: () => void;
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    maximumFractionDigits: 0,
  }).format(v);
}

export default function ProductCard({
  producto,
  esGeneral,
  color,
  colorMap,
  labelMap,
  onClick,
}: Props) {
  const cat = producto.categoria_path;
  const stockNum = producto.stock ?? 0;
  const sinStock = producto.stock !== null && stockNum <= 0;

  const padre = esGeneral && esPadre(producto);
  const [verVariantes, setVerVariantes] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  // Cerrar el recuadro al hacer clic fuera o con Escape. No se usa una capa
  // `fixed` porque el `hover:-translate-y-1` de la tarjeta crea un containing
  // block y la confinaría al tamaño de la tarjeta. El ref va en la tarjeta (no
  // en el recuadro) para que el mousedown sobre el propio botón de variantes no
  // cierre y reabra en el mismo clic.
  useEffect(() => {
    if (!verVariantes) return;
    const fuera = (e: MouseEvent) => {
      if (cardRef.current && !cardRef.current.contains(e.target as Node)) {
        setVerVariantes(false);
      }
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setVerVariantes(false);
    };
    document.addEventListener("mousedown", fuera);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fuera);
      document.removeEventListener("keydown", esc);
    };
  }, [verVariantes]);

  return (
    // `relative` sin `overflow-hidden`: el recuadro de variantes debe poder
    // sobresalir de la tarjeta. El recorte vive ahora en el contenedor de imagen.
    // No es un <button> porque adentro van otros botones (variantes); es un div
    // con rol de botón y los controles internos frenan la propagación del clic.
    <div
      ref={cardRef}
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      className="group relative flex cursor-pointer flex-col rounded-2xl border border-slate-200 bg-white text-left shadow-card transition-all hover:-translate-y-1 hover:border-slate-300 hover:shadow-card-hover focus:outline-none focus:ring-2"
      style={{ outlineColor: color }}
    >
      {/* Imagen */}
      <div className="relative aspect-square w-full overflow-hidden rounded-t-2xl bg-slate-50">
        {producto.imagen ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={producto.imagen}
            alt={producto.nombre}
            loading="lazy"
            className="h-full w-full object-contain p-3 transition-transform duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-slate-300">
            <ImageIcon size={42} strokeWidth={1.4} />
          </div>
        )}

        {/* Badge de estado del canal */}
        <div className="absolute left-2.5 top-2.5 flex gap-1.5">
          {producto.publicado ? (
            <span
              className="flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold text-white shadow-sm"
              style={{ backgroundColor: color }}
            >
              <PackageCheck size={11} /> Publicado
            </span>
          ) : (
            <span className="flex items-center gap-1 rounded-full bg-slate-900/70 px-2 py-1 text-[10px] font-bold text-white">
              <PackageX size={11} /> Sin publicar
            </span>
          )}
        </div>

        {/* FULL / FBA */}
        {!esGeneral && producto.full && (
          <span className="absolute right-2.5 top-2.5 flex items-center gap-1 rounded-full bg-emerald-500 px-2 py-1 text-[10px] font-extrabold uppercase text-white shadow-sm">
            <Truck size={11} /> {producto.full_label ?? "FULL"}
          </span>
        )}

        {/* Tipo: Padre / Único — solo en GENERAL */}
        {esGeneral && (
          <div className="absolute right-2.5 top-2.5 z-20 scale-90 origin-top-right">
            <TipoBadge padre={padre} />
          </div>
        )}
      </div>

      {/* Cuerpo */}
      <div className="relative flex flex-1 flex-col gap-2 p-3.5">
        {/* SKU + marca */}
        <div className="flex items-center justify-between gap-2">
          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-500">
            {producto.sku}
          </span>
          {producto.marca && (
            <span className="truncate text-[11px] font-medium text-slate-400">
              {producto.marca}
            </span>
          )}
        </div>

        {/* Nombre */}
        <h3 className="line-clamp-2 min-h-[2.5rem] text-sm font-semibold leading-snug text-slate-800">
          {producto.nombre}
        </h3>

        {/* Categoría (último nivel) */}
        {cat.length > 0 && (
          <div className="truncate text-[11px] text-slate-400">
            {cat.map((c) => c.nombre).join(" › ")}
          </div>
        )}

        {/* Variantes: botón que despliega el recuadro */}
        {padre && (
          <div className="relative z-20">
            <VariantesBoton
              n={producto.variantes.length}
              abierto={verVariantes}
              onClick={(e) => {
                e.stopPropagation();
                setVerVariantes((v) => !v);
              }}
            />
          </div>
        )}

        <div className="mt-auto flex items-end justify-between gap-2 pt-1">
          <div>
            <div className="text-lg font-extrabold tracking-tight text-slate-900">
              {precioMXN(producto.precio)}
            </div>
            {producto.precio_base &&
              producto.precio &&
              producto.precio_base > producto.precio && (
                <div className="text-xs text-slate-400 line-through">
                  {precioMXN(producto.precio_base)}
                </div>
              )}
          </div>

          {/* Stock o puntos de canal */}
          {esGeneral ? (
            <div className="flex flex-col items-end gap-1">
              <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">
                Canales
              </span>
              <ChannelDots
                canales={producto.canales}
                colorMap={colorMap}
                labelMap={labelMap}
              />
            </div>
          ) : (
            <div className="flex flex-col items-end gap-1">
              {/* stock real (lo que se sincroniza) */}
              <span
                title="Stock propio (se sincroniza entre canales)"
                className={[
                  "rounded-lg px-2 py-1 text-xs font-bold",
                  sinStock ? "bg-red-50 text-red-600" : "bg-emerald-50 text-emerald-600",
                ].join(" ")}
              >
                {producto.stock_real ?? producto.stock ?? "—"} u
              </span>
              {/* FULL / FBA (inventario en bodega del marketplace) */}
              <div className="flex gap-1">
                {!!producto.stock_full && producto.stock_full > 0 && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-700" title="En bodega de Mercado Libre (FULL)">
                    FULL {producto.stock_full}
                  </span>
                )}
                {!!producto.stock_fba && producto.stock_fba > 0 && (
                  <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-bold text-sky-700" title="En bodega de Amazon (FBA)">
                    FBA {producto.stock_fba}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Recuadro de variantes: sobresale de la tarjeta */}
      {padre && verVariantes && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="absolute left-0 top-full z-40 mt-2 w-[min(30rem,85vw)] rounded-xl border border-violet-200 bg-white p-1 shadow-card-hover"
        >
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setVerVariantes(false);
            }}
            title="Cerrar"
            className="absolute right-2 top-2 z-10 rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            <X size={14} />
          </button>
          <VariantesTabla
            variantes={producto.variantes}
            colorMap={colorMap}
            labelMap={labelMap}
          />
        </div>
      )}
    </div>
  );
}
