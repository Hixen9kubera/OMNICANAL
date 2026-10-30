"use client";

/**
 * /catalogo — CATÁLOGO (v0.703.0, Brandon, 29-oct-2026): el artifact del catálogo vivo dentro del panel.
 *
 * Los productos de los packing lists con su precio de Mercado Libre y de Amazon, con las mismas cinco vistas del
 * artifact (Productos, Por categoría, Contenedores,
 * Empate y De dónde sale cada dato). Dos diferencias a propósito: cada producto muestra la FOTO DE SU RENGLÓN EN EL
 * PACKING LIST (la de Odoo solo donde no hay), y no hay costos (el repo es público). El artifact no se toca: es el
 * respaldo de los datos. Tampoco van la cuenta del inventario (comprado − salió = queda, lo que dice Odoo) ni el valor
 * total a precio de ML y de Amazon: Brandon (29-oct) — «son datos que se necesitan únicamente en el artifact».
 *
 * Solo lectura. `operador` como Competencia: el backend (core/rbac.py) es la autoridad; aquí solo se esconde.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BookOpen, Loader2, Lock, RotateCw } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { ProveedorFotos } from "@/components/catalogo/Fotos";
import Productos, { type EstadoPublicados } from "@/components/catalogo/Productos";
import { Contenedores, Empate, Metodo, PorCategoria } from "@/components/catalogo/Otras";
import {
  type Catalogo, type Filtros, POR_VALIDAR, SIN_FILTROS, hora, marcarPublicados, nf, preparar, sumar,
} from "@/components/catalogo/logica";
import { ApiError, catalogoDatos, catalogoPublicados, mensajeDeError } from "@/lib/api";
import { quienSoy } from "@/lib/sesion";

const SIN_LOGIN = process.env.NEXT_PUBLIC_AUTH_OFF === "true";

type Pestana = "pro" | "cat" | "con" | "emp" | "met";
const PESTANAS: [Pestana, string][] = [
  ["pro", "Productos"], ["cat", "Por categoría"], ["con", "Contenedores"], ["emp", "Empate SKU y packing list"], ["met", "De dónde sale cada dato"],
];

export default function CatalogoPage() {
  const [acceso, setAcceso] = useState<"revisando" | "si" | "no">(SIN_LOGIN ? "si" : "revisando");
  useEffect(() => {
    if (SIN_LOGIN) return;
    let vivo = true;
    void quienSoy().then((u) => {
      if (vivo) setAcceso(u.autenticado && (u.rol === "admin" || u.rol === "operador") ? "si" : "no");
    });
    return () => { vivo = false; };
  }, []);
  return (
    <>
      <AppNavbar />
      {acceso === "revisando" ? <Cargando texto="Comprobando acceso…" /> : acceso === "no" ? <NoDisponible /> : <Catalogo />}
    </>
  );
}

function Catalogo() {
  const [D, setD] = useState<Catalogo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noDisponible, setNoDisponible] = useState(false);
  const [intento, setIntento] = useState(0);
  const [pestana, setPestana] = useState<Pestana>("pro");
  // Abre en «Por validar» (Brandon, 30-oct-2026); un «Ver productos» de otra vista abre el catálogo completo.
  const [filtros, setFiltros] = useState<Filtros>(POR_VALIDAR);
  // Lo publicado en ML (v0.708.0) llega DESPUÉS del catálogo y no lo frena: es lo único en vivo de la pestaña.
  const [pub, setPub] = useState<EstadoPublicados>({ estado: "cargando" });

  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    setPub({ estado: "cargando" });
    catalogoDatos(ctl.signal)
      .then((d) => {
        if (ctl.signal.aborted) return;
        preparar(d.R);
        setD(d);
        catalogoPublicados(ctl.signal)
          .then((p) => {
            if (ctl.signal.aborted) return;
            marcarPublicados(d.R, p.ml);
            setD({ ...d }); // las filas cambiaron por dentro: un objeto nuevo para que la tabla vuelva a filtrar
            setPub({ estado: "ok", actualizado: p.actualizado });
          })
          .catch(() => { if (!ctl.signal.aborted) setPub({ estado: "error" }); });
      })
      .catch((e) => {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) { setNoDisponible(true); return; }
        setError(mensajeDeError(e, "No se pudo leer el catálogo. Revisa que el backend esté arriba."));
      });
    return () => ctl.abort();
  }, [intento]);

  const total = useMemo(() => (D ? sumar(D.R) : null), [D]);
  const irA = useCallback((f: Partial<Filtros>) => {
    setFiltros({ ...SIN_FILTROS, ...f });
    setPestana("pro");
    window.scrollTo({ top: document.getElementById("pestanas")?.offsetTop ?? 0, behavior: "smooth" });
  }, []);

  if (noDisponible) return <NoDisponible />;
  if (error) {
    return (
      <main className="mx-auto max-w-[1600px] px-4 py-6">
        <div role="alert" className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">{error}</span>
          <button type="button" onClick={() => setIntento((n) => n + 1)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100">
            <RotateCw size={13} aria-hidden /> Reintentar
          </button>
        </div>
      </main>
    );
  }
  if (!D || !total) return <Cargando texto="Leyendo el catálogo (15,000 productos)…" />;

  const f = D.meta.fotos;
  const ml = D.mercado?.ml?.edu;
  const fuentes: [string, string | undefined, string][] = [
    ["Packing lists", D.fuentes?.pl, `${nf(D.resumen.archivos_originales)} originales · ${nf(D.resumen.archivos_validados)} validados`],
    ["Movimientos de Odoo", D.fuentes?.movimientos, "hechos, desde dic-2025"],
    ["Mercado Libre", undefined, ml ? `API, 6 y 7 de oct (Eduardo)${D.v3 ? ` · precios v3 sin marcas en ${nf(D.v3.con_precio)} filas` : ""}` : `${nf(D.mercado?.ml?.produccion)} SKUs medidos`],
    ["Amazon", D.mercado?.amazon?.actualizado, `${nf(D.mercado?.amazon?.palabras)} palabras clave`],
    ["Fotos", undefined, `${nf(f.pl_original + f.pl_artifact)} del packing list · ${nf(f.odoo)} de Odoo`],
  ];

  return (
    <ProveedorFotos version={D.meta.preparado} lado={f.lado} cols={f.cols} porHoja={f.por_hoja}>
      <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6">
        <div className="relative overflow-hidden rounded-3xl p-6 shadow-card" style={{ background: "linear-gradient(120deg, #4F46E5 0%, #818CF8 100%)", color: "#FFFFFF" }}>
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">Centro Omnicanal · Inventario</div>
              <h1 className="mt-1 flex items-center gap-2 text-3xl font-extrabold tracking-tight"><BookOpen size={28} aria-hidden /> Catálogo</h1>
              <p className="mt-1 max-w-3xl text-sm opacity-90">
                Todos los productos de los packing lists, con su precio de Mercado Libre y de Amazon y la foto de su renglón en el
                packing list.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="flex items-center gap-2 rounded-xl bg-white/15 px-3 py-1.5 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur">
                  Armado el {hora(D.meta.generado)} · respaldo: artifact del catálogo vivo
                </span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">{nf(total.sk + total.sin)}</div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">Productos</div>
            </div>
          </div>
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20" style={{ background: "#FFFFFF" }} />
        </div>

        <div className="flex flex-wrap gap-2 text-xs">
          {fuentes.map(([n, cuando, txt]) => (
            <span key={n} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-slate-600 shadow-card">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden /><b className="text-slate-800">{n}</b> {txt}{cuando ? ` · ${hora(cuando)}` : ""}
            </span>
          ))}
        </div>

        <div id="pestanas" className="flex flex-wrap gap-1 rounded-2xl border border-slate-200 bg-white p-1 shadow-card" role="tablist" aria-label="Vistas del catálogo">
          {PESTANAS.map(([id, nom]) => (
            <button key={id} type="button" role="tab" aria-selected={pestana === id} onClick={() => setPestana(id)}
              className={`rounded-xl px-4 py-2 text-sm font-semibold transition ${pestana === id ? "bg-indigo-600 text-white shadow-sm" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
              {nom}
            </button>
          ))}
        </div>

        <section role="tabpanel">
          {pestana === "pro" && <Productos D={D} filtros={filtros} setFiltros={setFiltros} pub={pub} />}
          {pestana === "cat" && <PorCategoria D={D} irA={irA} />}
          {pestana === "con" && <Contenedores D={D} irA={irA} />}
          {pestana === "emp" && <Empate D={D} irA={irA} />}
          {pestana === "met" && <Metodo D={D} />}
        </section>
      </main>
    </ProveedorFotos>
  );
}

function Cargando({ texto }: { texto: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Loader2 size={18} className="animate-spin text-indigo-500" aria-hidden />{texto}
    </div>
  );
}

/** "No te toca": no dice qué hay detrás ni pide datos. */
function NoDisponible() {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center gap-3 px-4 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400"><Lock size={22} aria-hidden /></span>
      <h1 className="text-lg font-semibold text-slate-800">No disponible</h1>
      <p className="text-sm text-slate-500">Esta página no está disponible para tu cuenta.</p>
      <Link href="/omnicanal" className="text-sm font-semibold text-indigo-600 hover:text-indigo-700">Ir al panel</Link>
    </main>
  );
}
