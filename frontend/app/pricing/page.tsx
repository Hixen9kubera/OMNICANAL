"use client";

/**
 * /pricing — PRICING: el «Monitor de precios FULL» (José, 14-oct-2026) dentro
 * del panel. Tomó el lugar de Radar en la barra (Brandon, 15-oct-2026).
 *
 * Las publicaciones FULL de BEKURA y SANCORFASHION con su precio, competencia,
 * stock, ventas, conversión y «Qué hacer». La columna «Qué hacer» lleva las
 * reglas de SIN VENTAS de Brandon antes que las de José (ver
 * `components/pricing/reglas.ts`).
 *
 * SOLO LECTURA: no cambia ningún precio. Oculta, solo admin — tres capas: el
 * backend (`solo_admin` + core/rbac.py) es la autoridad; `Acceso` no monta
 * nada si el rol no es admin; y un 401/403 de la API se pinta como
 * "No disponible".
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Loader2, Lock, RotateCw } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { ProveedorHojas } from "@/components/pricing/Miniaturas";
import Monitor from "@/components/pricing/Monitor";
import { quienSoy } from "@/lib/sesion";
import {
  ApiError,
  mensajeDeError,
  pricingHechos,
  pricingMonitor,
  pricingSalud,
  pricingVivo,
  type PricingFoto,
  type PricingHechos,
  type PricingSalud,
  type PricingVivo,
} from "@/lib/api";

import "./pricing.css";

const SIN_LOGIN = process.env.NEXT_PUBLIC_AUTH_OFF === "true";

export default function PricingPage() {
  const [acceso, setAcceso] = useState<"revisando" | "admin" | "no">(SIN_LOGIN ? "admin" : "revisando");

  useEffect(() => {
    if (SIN_LOGIN) return;
    let vivo = true;
    void quienSoy().then((u) => {
      // Admin o invitado: lo decide el backend (`puede.pricing`, ver core/rbac.py).
      if (vivo) setAcceso(u.autenticado && (u.rol === "admin" || !!u.puede?.pricing) ? "admin" : "no");
    });
    return () => {
      vivo = false;
    };
  }, []);

  return (
    <>
      <AppNavbar />
      {acceso === "revisando" ? (
        <Cargando texto="Comprobando acceso…" />
      ) : acceso === "no" ? (
        <NoDisponible />
      ) : (
        <Pricing />
      )}
    </>
  );
}

function Pricing() {
  const [foto, setFoto] = useState<PricingFoto | null>(null);
  // «Hecho» y «Promoción hoy» llegan aparte: si fallan, la tabla se ve igual
  // y esas dos columnas lo dicen.
  const [hechos, setHechos] = useState<PricingHechos | null | "error">(null);
  // Stock y ventas de AHORA (filtro «Se agota» y low stock); si falla, se usa la foto.
  const [vivo, setVivo] = useState<PricingVivo | null | "error">(null);
  // Calidad y experiencia de ML (v0.659.0): si falla, la columna lo dice y lo demás sigue.
  const [salud, setSalud] = useState<PricingSalud | null | "error">(null);
  const [error, setError] = useState<string | null>(null);
  const [noDisponible, setNoDisponible] = useState(false);
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    pricingMonitor(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setFoto(d);
      })
      .catch((e) => {
        if (ctl.signal.aborted) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          setNoDisponible(true);
          return;
        }
        if (e instanceof ApiError && e.status === 404) {
          setError(mensajeDeError(e, "Todavía no hay foto del monitor de precios."));
          return;
        }
        setError(mensajeDeError(e, "No se pudo leer el monitor de precios. Revisa que el backend esté arriba."));
      });
    pricingVivo(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setVivo(d);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setVivo("error");
      });
    pricingHechos(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setHechos(d);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setHechos("error");
      });
    pricingSalud(ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setSalud(d);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setSalud("error");
      });
    return () => ctl.abort();
  }, [intento]);

  // DATOS FRESCOS (v0.656.0): cada 30 s se pregunta si hubo venta. El backend
  // solo vuelve a leer ML y kubera cuando entró una (el webhook la escribe al
  // momento) o pasaron 5 min; si no, contesta de su caché. Las promociones, cada
  // 5 min (el servidor las relee de ML cada 30). Un fallo deja lo último que se vio.
  useEffect(() => {
    let vivoOk = true;
    const ciclo = window.setInterval(() => {
      if (document.hidden) return;
      pricingVivo()
        .then((d) => {
          if (vivoOk) setVivo(d);
        })
        .catch(() => {});
    }, 30_000);
    const promos = window.setInterval(() => {
      if (document.hidden) return;
      pricingHechos()
        .then((d) => {
          if (vivoOk) setHechos(d);
        })
        .catch(() => {});
      // La calidad se mide cada hora (11 a 23 UTC): con releerla junto a las promociones basta.
      pricingSalud()
        .then((d) => {
          if (vivoOk) setSalud(d);
        })
        .catch(() => {});
    }, 300_000);
    return () => {
      vivoOk = false;
      window.clearInterval(ciclo);
      window.clearInterval(promos);
    };
  }, []);

  if (noDisponible) return <NoDisponible />;
  return (
    <div className="pm">
      {error ? (
        <div className="envoltura">
          <div role="alert" className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">{error}</span>
            <button
              type="button"
              onClick={() => setIntento((n) => n + 1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
            >
              <RotateCw size={13} aria-hidden />
              Reintentar
            </button>
          </div>
        </div>
      ) : !foto ? (
        <Cargando texto="Leyendo el monitor de precios…" />
      ) : (
        <ProveedorHojas version={foto.meta.generado} celda={foto.meta.img.celda} lado={foto.meta.img.lado}>
          <Monitor foto={foto} hechos={hechos} vivo={vivo} salud={salud} />
        </ProveedorHojas>
      )}
    </div>
  );
}

function Cargando({ texto }: { texto: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Loader2 size={18} className="animate-spin text-indigo-500" aria-hidden />
      {texto}
    </div>
  );
}

/** "No te toca": no dice qué hay detrás ni pide datos. */
function NoDisponible() {
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center gap-3 px-4 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
        <Lock size={22} aria-hidden />
      </span>
      <h1 className="text-lg font-semibold text-slate-800">No disponible</h1>
      <p className="text-sm text-slate-500">Esta página no está disponible para tu cuenta.</p>
      <Link href="/omnicanal" className="text-sm font-semibold text-indigo-600 hover:text-indigo-700">
        Ir al panel
      </Link>
    </main>
  );
}
