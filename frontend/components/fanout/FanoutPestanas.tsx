"use client";

/**
 * Las pestañas de Operaciones › Fan-out: la sincronización en vivo, la
 * coincidencia por SKU, FULL (las bodegas de Mercado Libre), Bodegas (el
 * inventario propio de kubera contra Odoo y Woo) y Devoluciones (las cajas que
 * regresan a nuestra bodega y su recepción en Odoo). Son rutas hermanas y páginas
 * autónomas (cada una trae su navbar), así que la barra vive aquí y la pinta cada
 * página arriba de su banner — el mismo molde que `InventarioPestanas`.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Boxes, LayoutGrid, RotateCcw, Warehouse } from "lucide-react";

const PESTANAS = [
  { href: "/dashboard", label: "En vivo", icon: Activity, exacta: true },
  { href: "/dashboard/matriz", label: "Coincidencia por SKU", icon: LayoutGrid, exacta: false },
  { href: "/dashboard/full", label: "FULL", icon: Warehouse, exacta: false },
  { href: "/dashboard/bodegas", label: "Bodegas", icon: Boxes, exacta: false },
  { href: "/dashboard/devoluciones", label: "Devoluciones", icon: RotateCcw, exacta: false },
];

export default function FanoutPestanas() {
  const pathname = usePathname() ?? "/dashboard";
  return (
    <nav aria-label="Vistas del fan-out" className="flex w-fit max-w-full flex-wrap gap-1 rounded-xl bg-white p-1 ring-1 ring-slate-200">
      {PESTANAS.map((p) => {
        const activa = p.exacta ? pathname === p.href : pathname.startsWith(p.href);
        return (
          <Link
            key={p.href}
            href={p.href}
            aria-current={activa ? "page" : undefined}
            className={[
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition",
              activa ? "bg-indigo-600 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50 hover:text-slate-800",
            ].join(" ")}
          >
            <p.icon className="h-4 w-4" />
            {p.label}
          </Link>
        );
      })}
    </nav>
  );
}
