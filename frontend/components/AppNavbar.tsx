"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Package,
  Share2,
  Store,
  TrendingUp,
  FileText,
  BarChart3,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import NotificationBell from "./NotificationBell";

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  href?: string;          // si tiene href, es navegable
  proximamente?: boolean;
}

// Navegación principal de la app. OMNICANAL y PRODUCTOS están implementados;
// el resto se marca "próximamente".
const ITEMS: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, proximamente: true },
  { id: "productos", label: "Productos", icon: Package, href: "/productos" },
  { id: "omnicanal", label: "Omnicanal", icon: Share2, href: "/omnicanal" },
  { id: "canales", label: "Canales", icon: Store, proximamente: true },
  { id: "ventas", label: "Ventas", icon: TrendingUp, proximamente: true },
  { id: "facturas", label: "Facturas", icon: FileText, proximamente: true },
  { id: "reportes", label: "Reportes", icon: BarChart3, proximamente: true },
  { id: "automatizacion", label: "Automatización", icon: Workflow, proximamente: true },
];

export default function AppNavbar() {
  const pathname = usePathname();

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
            const activo = !!item.href && pathname === item.href;

            if (item.href) {
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  className={[
                    "group relative flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors",
                    activo
                      ? "font-semibold text-indigo-600"
                      : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800",
                  ].join(" ")}
                >
                  <Icon size={17} />
                  {item.label}
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
    </header>
  );
}
