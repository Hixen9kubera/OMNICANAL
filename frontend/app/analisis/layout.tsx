"use client";

/**
 * Layout de la sección ANÁLISIS (ruta /analisis).
 *
 * Se llama "Análisis" desde el 29-jul (Eduardo): la sección dejó de ser solo
 * reabastecimiento — dentro viven Estrellas, Amazon FBA y Reportes. La ruta se
 * renombró junto con la etiqueta; el API conserva el nombre técnico
 * (/api/fulfillment) porque es interno y renombrarlo no aporta nada visible.
 *
 * Decisión (Eduardo, 2026-07-29): las secciones son RUTAS ANIDADAS, no items
 * del navbar ni estado de React. Razones:
 *   · el navbar ya desbordaba 591 px con 11 items — 4 más lo volvían inusable;
 *   · cada sección tiene URL propia y se puede compartir/marcar;
 *   · cada una vive en su archivo (la de Reabastecimiento ya iba en ~700 líneas).
 *
 * Aquí vive SOLO lo común a las cuatro: el navbar, el banner con el nombre de
 * la sección y la barra de sub-pestañas. Los KPIs y los filtros (cuenta,
 * período) los pone cada página, porque no son los mismos en todas.
 */

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { BarChart3, Boxes, Clock, FileText, Gauge, PackageX, Star, TriangleAlert } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

const SECCIONES = [
  // La primera es la vista general (stock + ventas + sugerido de reabasto) y
  // se llama igual que la sección: es la que abre por defecto.
  { href: "/analisis", label: "Análisis", icon: Boxes, exacta: true },
  { href: "/analisis/categorias", label: "Categorías", icon: Boxes },
  { href: "/analisis/estrellas", label: "Estrellas", icon: Star },
  { href: "/analisis/fba", label: "Amazon FBA", icon: BarChart3 },
  { href: "/analisis/metricas", label: "Métricas", icon: Gauge },
  { href: "/analisis/rentabilidad", label: "Rentabilidad", icon: PackageX },
  { href: "/analisis/reportes", label: "Reportes", icon: FileText },
];
// La entrada VENTAS del submenú vive en /ventas (página autónoma con su propio
// navbar): no pasa por este layout y por eso no está en SECCIONES.

export default function FulfillmentLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/analisis";
  const activa = SECCIONES.find((s) =>
    s.exacta ? pathname === s.href : pathname.startsWith(s.href));

  const [ambiente, setAmbiente] = useState<string | null>(null);
  useEffect(() => {
    fetchSesion(`${API_BASE}/api/fulfillment/meta`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setAmbiente(d.ambiente))
      .catch(() => setAmbiente(null));
  }, []);

  // Fecha solo en el cliente y en horario de México: calcularla en el render
  // la evalúa también en el servidor con otra zona → error de hidratación.
  const [hoy, setHoy] = useState("");
  useEffect(() => {
    const tz = { timeZone: "America/Mexico_City" } as const;
    const d = new Date();
    setHoy(
      `${d.toLocaleDateString("es-MX", { ...tz, weekday: "short" })} ` +
      `${d.toLocaleDateString("es-MX", { ...tz, day: "numeric" })} de ` +
      d.toLocaleDateString("es-MX", { ...tz, month: "short" }),
    );
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      {/* 1800 y no 1600 desde el 14-ago: la tabla de Análisis creció a 17
          columnas con las medidas y Ganancia se salía del borde. Va parejo con
          el ancho de AppNavbar — si uno crece y el otro no, la barra queda
          angosta respecto a la tarjeta y se nota. */}
      <main className="mx-auto max-w-[1800px] px-4 py-5 sm:px-6">
        {/* Banner común */}
        <div
          className="relative overflow-hidden rounded-3xl p-6 shadow-card"
          style={{ background: "linear-gradient(120deg, #6366F1 0%, #7C3AED 100%)", color: "#fff" }}
        >
          <div className="relative z-10">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
              {/* En la vista general no se repite el nombre ("Análisis ·
                  Análisis"); en las demás sí se indica la sub-sección. */}
              Análisis{activa && !activa.exacta ? ` · ${activa.label}` : ""}
            </div>
            <h1 className="mt-1 text-3xl font-extrabold capitalize tracking-tight">{hoy}</h1>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-sm opacity-90">
              <Clock size={14} />
              stock y ventas en tiempo real
              <span className="ml-1 inline-flex items-center gap-1.5 rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300" />
                En vivo
              </span>
              {ambiente && ambiente !== "production" && (
                <span className="inline-flex items-center rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
                  {ambiente} · sandbox
                </span>
              )}
            </p>
          </div>
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white opacity-20" />
        </div>

        {/* Aviso de sección nueva (Eduardo, 31-jul): vive en el LAYOUT para
            cubrir las cinco vistas de /analisis/* con un solo texto. La
            pestaña Ventas queda fuera a propósito — es la de siempre, solo se
            recolocó en el submenú. */}
        <div className="mt-3 flex items-center gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-[13px] text-amber-800">
          <TriangleAlert size={15} className="shrink-0" />
          <span>
            <span className="font-semibold">Sección en desarrollo.</span>{" "}
            Si alguna cifra no cuadra con lo que esperas, avísale al equipo de
            tecnología para revisarla.
          </span>
        </div>

        {/* Aquí NO va una barra de sub-pestañas: las secciones se navegan desde
            el submenú del navbar (pasar el cursor sobre "Análisis"), decisión
            definitiva de Eduardo el 30-jul. SECCIONES sobrevive solo para
            rotular el banner con la sub-sección activa. */}
        <div className="pt-5">{children}</div>
      </main>
    </div>
  );
}
