"use client";

// /crear — «Crear Productos» se APAGÓ (2-nov-2026, Eduardo: saltarse por completo
// el estado draft). Los borradores ya no esperan en una pestaña aparte: aparecen
// directo en el Publicador (/productos), y ahí se les pone contenido, categoría,
// costo y fotos, y se publican. El alta Odoo→Woo que llenaba esta lista murió con
// Odoo (15-oct), y sus rutas del backend contestan 410 (routers/crear.py).
//
// La ruta se queda SOLO para que los enlaces guardados no caigan en un 404: avisa
// en una línea y manda al Publicador con el filtro «Solo borradores» puesto
// (`?borradores=1`): la misma cola de trabajo que había aquí. `replace` y no
// `push`: «Atrás» no debe regresar aquí para rebotar otra vez.

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Package } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";

const DESTINO = "/productos?borradores=1";
const ESPERA_MS = 2500; // lo justo para leer el aviso

export default function CrearRetirado() {
  const router = useRouter();

  useEffect(() => {
    const t = window.setTimeout(() => router.replace(DESTINO), ESPERA_MS);
    return () => window.clearTimeout(t);
  }, [router]);

  return (
    <div className="min-h-screen">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 pb-32 pt-6 sm:px-6">
        <div className="mx-auto mt-10 max-w-xl rounded-2xl border border-indigo-100 bg-white p-6 shadow-card">
          <div className="flex items-center gap-2 text-sm font-bold text-indigo-700">
            <Package size={18} /> «Crear Productos» ya no existe
          </div>
          <p className="mt-2 text-sm text-slate-600">
            Los borradores ahora viven en el Publicador: ahí aparecen directo y ahí
            se trabajan y se publican. Para ver solo lo que falta trabajar, usa el
            filtro «Solo borradores» (te llevamos con él puesto); la etiqueta de cada
            fila dice qué le falta. Te llevamos en un momento.
          </p>
          <Link
            href={DESTINO}
            replace
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700"
          >
            Ir al Publicador <ArrowRight size={16} />
          </Link>
        </div>
      </main>
    </div>
  );
}
