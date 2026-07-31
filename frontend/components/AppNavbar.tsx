"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Package,
  Share2,
  PackagePlus,
  TrendingUp,
  LineChart,
  FileText,
  BarChart3,
  Boxes,
  Star,
  ChevronDown,
  Workflow,
  Calculator,
  Database,
  Tags,
  type LucideIcon,
} from "lucide-react";
import NotificationBell from "./NotificationBell";

interface SubItem {
  label: string;
  href: string;
  icon: LucideIcon;
  descripcion: string;
}

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  href?: string;          // si tiene href, es navegable
  proximamente?: boolean;
  submenu?: SubItem[];    // despliega al pasar el cursor (VARIANTE B)
}

// Navegación principal de la app. OMNICANAL, PRODUCTOS y CREAR PRODUCTOS están
// implementados; el resto se marca "próximamente".
const ITEMS: NavItem[] = [
  // ANÁLISIS abre la barra (Eduardo, 31-jul) y absorbió a VENTAS como entrada
  // de su submenú — ya no es pestaña propia. La ruta /ventas no cambia: esa
  // página es autónoma (trae su propio navbar) y solo se cuelga de aquí.
  // Navegación DEFINITIVA: submenú al pasar el cursor (Eduardo, 30-jul). Al
  // sumar una sección basta agregarla aquí; el banner del layout se rotula
  // solo con la ruta activa.
  {
    id: "analisis", label: "Análisis", icon: LineChart, href: "/analisis",
    submenu: [
      { label: "Análisis", href: "/analisis", icon: Boxes,
        descripcion: "Stock, ventas y sugerido de reabasto" },
      { label: "Ventas", href: "/ventas", icon: TrendingUp,
        descripcion: "Pedidos por canal y comparativa semanal" },
      { label: "Categorías", href: "/analisis/categorias", icon: Tags,
        descripcion: "Ventas por categoría de ML y por cuenta" },
      { label: "Estrellas", href: "/analisis/estrellas", icon: Star,
        descripcion: "Pareto histórico: qué SKUs sostienen la venta" },
      { label: "Amazon FBA", href: "/analisis/fba", icon: BarChart3,
        descripcion: "Capacidad de bodega y plan de envío" },
      { label: "Reportes", href: "/analisis/reportes", icon: FileText,
        descripcion: "Descargas en CSV y Excel" },
    ],
  },
  { id: "productos", label: "Productos", icon: Package, href: "/productos" },
  { id: "omnicanal", label: "Omnicanal", icon: Share2, href: "/omnicanal" },
  { id: "crear", label: "Crear Productos", icon: PackagePlus, href: "/crear" },
  { id: "costos", label: "Costos", icon: Calculator, href: "/costos" },
  { id: "dashboard", label: "Operaciones", icon: LayoutDashboard, href: "/dashboard" },
  { id: "migracion", label: "Migración", icon: Database, href: "/migracion" },
  { id: "facturas", label: "Facturas", icon: FileText, proximamente: true },
  // "Reportes" se retiró del navbar (Eduardo, 29-jul): ahora vive dentro de
  // Análisis como /analisis/reportes.
  { id: "automatizacion", label: "Automatización", icon: Workflow, proximamente: true },
];

export default function AppNavbar() {
  const pathname = usePathname();

  // Submenú al pasar el cursor. Se posiciona FIJO calculando el rect del
  // disparador: el <nav> tiene overflow-x-auto y un menú `absolute` quedaría
  // recortado por ese contenedor. El cierre lleva 180 ms de gracia para poder
  // bajar el cursor del botón al menú sin que se desvanezca.
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const cierre = useRef<ReturnType<typeof setTimeout> | null>(null);

  const abrir = useCallback((id: string, el: HTMLElement) => {
    if (cierre.current) clearTimeout(cierre.current);
    const r = el.getBoundingClientRect();
    setMenu({ id, x: r.left, y: r.bottom + 6 });
  }, []);

  const cerrarConGracia = useCallback(() => {
    if (cierre.current) clearTimeout(cierre.current);
    cierre.current = setTimeout(() => setMenu(null), 180);
  }, []);

  const mantener = useCallback(() => {
    if (cierre.current) clearTimeout(cierre.current);
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1600px] items-center gap-6 px-4 sm:px-6">
        {/* Logo */}
        <Link href="/omnicanal" className="flex items-center gap-2.5 pr-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-sm">
            <Share2 size={18} />
          </div>
          <div className="leading-tight">
            <div className="text-[15px] font-bold tracking-tight text-slate-900">
              Kubera
            </div>
            <div className="-mt-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-indigo-500">
              Omnicanal
            </div>
          </div>
        </Link>

        {/* Navegación */}
        <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
          {ITEMS.map((item) => {
            const Icon = item.icon;
            // Activo también cuando la ruta pertenece a una entrada del
            // submenú que vive FUERA del prefijo (p. ej. /ventas bajo
            // Análisis): sin esto, en /ventas ninguna pestaña se marcaría.
            const activo = (!!item.href && !!pathname?.startsWith(item.href))
              || !!item.submenu?.some((s) => !!pathname?.startsWith(s.href));

            if (item.href) {
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  onMouseEnter={(e) =>
                    item.submenu ? abrir(item.id, e.currentTarget) : setMenu(null)}
                  onMouseLeave={item.submenu ? cerrarConGracia : undefined}
                  // Accesible por teclado: al enfocar con Tab también abre.
                  onFocus={(e) => item.submenu && abrir(item.id, e.currentTarget)}
                  aria-haspopup={item.submenu ? "menu" : undefined}
                  aria-expanded={item.submenu ? menu?.id === item.id : undefined}
                  className={[
                    "group relative flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors",
                    activo
                      ? "font-semibold text-indigo-600"
                      : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800",
                  ].join(" ")}
                >
                  <Icon size={17} />
                  {item.label}
                  {item.submenu && (
                    <ChevronDown
                      size={13}
                      className={`transition-transform ${menu?.id === item.id ? "rotate-180" : ""}`}
                    />
                  )}
                  {activo && (
                    <span className="absolute inset-x-2 -bottom-[9px] h-[3px] rounded-full bg-indigo-500" />
                  )}
                </Link>
              );
            }

            return (
              <span
                key={item.id}
                title="Próximamente"
                className="group relative flex shrink-0 cursor-not-allowed items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-slate-400"
              >
                <Icon size={17} />
                {item.label}
                <span className="ml-1 hidden rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-slate-400 group-hover:inline lg:inline">
                  Pronto
                </span>
              </span>
            );
          })}
        </nav>

        {/* Usuario */}
        <div className="flex shrink-0 items-center gap-3">
          <NotificationBell />
          <div className="hidden text-right sm:block">
            <div className="text-xs font-semibold text-slate-700">Kubera</div>
            <div className="text-[11px] text-slate-400">admin</div>
          </div>
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-slate-800 text-sm font-bold text-white">
            K
          </div>
        </div>
      </div>

      {/* Panel del submenú — FIJO (fuera del <nav> con overflow) */}
      {menu && (() => {
        const item = ITEMS.find((i) => i.id === menu.id);
        if (!item?.submenu) return null;
        return (
          <div
            role="menu"
            onMouseEnter={mantener}
            onMouseLeave={cerrarConGracia}
            style={{ left: menu.x, top: menu.y }}
            className="fixed z-50 w-72 origin-top overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl"
          >
            {item.submenu.map((s) => {
              const SubIcon = s.icon;
              // El primero es la vista general: solo está activo con la ruta
              // exacta, si no se marcaría siempre.
              const esRaiz = s.href === item.href;
              const activa = esRaiz ? pathname === s.href : pathname?.startsWith(s.href);
              return (
                <Link
                  key={s.href}
                  href={s.href}
                  role="menuitem"
                  onClick={() => setMenu(null)}
                  className={[
                    "flex items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors",
                    activa ? "bg-indigo-50" : "hover:bg-slate-50",
                  ].join(" ")}
                >
                  <SubIcon
                    size={16}
                    className={`mt-0.5 shrink-0 ${activa ? "text-indigo-600" : "text-slate-400"}`}
                  />
                  <span className="min-w-0">
                    <span className={`block text-sm ${activa ? "font-semibold text-indigo-700" : "font-medium text-slate-700"}`}>
                      {s.label}
                    </span>
                    <span className="block text-[11px] leading-tight text-slate-400">
                      {s.descripcion}
                    </span>
                  </span>
                </Link>
              );
            })}
          </div>
        );
      })()}
    </header>
  );
}
