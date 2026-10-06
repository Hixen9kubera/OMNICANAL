"use client";

/**
 * Las tres pestañas de INVENTARIO: el Catálogo Maestro, el Checklist de
 * almacén y las Órdenes de venta propias (2-oct-2026). Son rutas hermanas y
 * páginas autónomas (cada una trae su navbar), así que la barra vive aquí y la
 * pinta cada página arriba de su banner.
 *
 * Sólo la primera es `exacta`: su ruta es el prefijo de las otras dos y, sin
 * eso, se marcaría siempre. Órdenes de venta navega por # dentro de su ruta
 * (#nueva, #OV-00012), así que el `pathname` no cambia y la pestaña sigue
 * marcada con un documento abierto.
 *
 * Con un documento abierto, el clic en «Órdenes de venta» tiene que REGRESAR A
 * LA LISTA, y el `<Link>` solo no puede: empuja la misma ruta sin el # con
 * `history.pushState`, que no dispara `hashchange` ni `popstate`. Lo ataja la
 * propia página de órdenes (un oyente de clics sobre `a[href]`, que además
 * pregunta si hay cambios sin guardar); por eso aquí no hay `onClick`: el mismo
 * oyente cubre también la entrada del submenú del navbar.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ClipboardCheck, ClipboardList, Warehouse } from "lucide-react";

const PESTANAS = [
  { href: "/inventario", label: "Catálogo Maestro", icon: Warehouse, exacta: true },
  { href: "/inventario/checklist", label: "Checklist", icon: ClipboardCheck, exacta: false },
  { href: "/inventario/ordenes", label: "Órdenes de venta", icon: ClipboardList, exacta: false },
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
