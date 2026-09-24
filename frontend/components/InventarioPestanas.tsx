"use client";

/**
 * Las dos pestañas de INVENTARIO: el Catálogo Maestro y el Checklist de
 * almacén. Son rutas hermanas y páginas autónomas (cada una trae su navbar),
 * así que la barra vive aquí y la pinta cada página arriba de su banner.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ClipboardCheck, Warehouse } from "lucide-react";

const PESTANAS = [
  { href: "/inventario", label: "Catálogo Maestro", icon: Warehouse, exacta: true },
  { href: "/inventario/checklist", label: "Checklist", icon: ClipboardCheck, exacta: false },
];

export default function InventarioPestanas() {
  const pathname = usePathname() ?? "/inventario";
  return (
    <nav className="mb-4 flex w-fit gap-1 rounded-xl bg-white p-1 ring-1 ring-slate-200">
      {PESTANAS.map((p) => {
        const activa = p.exacta ? pathname === p.href : pathname.startsWith(p.href);
        return (
          <Link
            key={p.href}
            href={p.href}
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
