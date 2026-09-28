"use client";

/**
 * El ícono de la IA en la pestaña FULLFILMENT de la barra (Brandon, 28-sep-2026): "deberá
 * estar trabajando en segundo plano para poder realizar otras actividades, como si fuera un
 * chat de Claude; la pestaña FULLFILMENT tendrá un ícono con luces parecido a Claude: cuando
 * está pensando cambia de color, estilo respiración; cuando termina, color verde".
 *
 *   · pensando  → la chispa respira (coral ↔ violeta), en cualquier pantalla del panel;
 *   · terminó   → verde (rojo si falló) hasta que se abre FULLFILMENT; si ya estaba ahí,
 *                 se queda verde unos segundos y se apaga;
 *   · nada      → no se pinta.
 *
 * Pregunta a `GET /api/fulfillment/crear-full/actividad`, que sólo mira la memoria del
 * backend (no toca la base): cada 4 s mientras piensa, cada 15 s si no, y nunca con la
 * pestaña del navegador escondida. El resultado del turno lo guarda el SERVIDOR en el plan
 * de la semana, así que no importa en qué pantalla esté la persona cuando termine.
 */

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { API_BASE, fetchSesion } from "@/lib/api";

interface Actividad {
  semana: string;
  corriendo: { id: string; quien: string | null; segundos: number; fase?: string | null } | null;
  ultimo: {
    id: string; estado: "listo" | "error"; termino: string; skus: number; piezas: number;
    quien?: string | null; motivo?: string | null;
  } | null;
}

const LLAVE_VISTO = "fulfillment.ia_visto";
const VERDE_EN_PANTALLA_MS = 45_000;

const leerVisto = () => { try { return window.localStorage.getItem(LLAVE_VISTO); } catch { return null; } };
const guardarVisto = (id: string) => { try { window.localStorage.setItem(LLAVE_VISTO, id); } catch { /* sin almacenamiento */ } };

export default function IndicadorIA() {
  const pathname = usePathname();
  const enFull = !!pathname?.startsWith("/fulfillment");
  const [a, setA] = useState<Actividad | null>(null);
  const [visto, setVisto] = useState<string | null>(null);
  useEffect(() => { setVisto(leerVisto()); }, []);

  const pensando = !!a?.corriendo;
  useEffect(() => {
    let vivo = true;
    const pedir = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/actividad`, { cache: "no-store" });
        if (r.ok && vivo) setA(await r.json() as Actividad);
      } catch { /* sin red: se vuelve a preguntar */ }
    };
    void pedir();
    const t = setInterval(pedir, pensando ? 4_000 : 15_000);
    const alVolver = () => { if (document.visibilityState === "visible") void pedir(); };
    document.addEventListener("visibilitychange", alVolver);
    return () => { vivo = false; clearInterval(t); document.removeEventListener("visibilitychange", alVolver); };
  }, [pensando]);

  const ultimo = a?.ultimo ?? null;
  const reciente = ultimo ? Date.now() - Date.parse(ultimo.termino) < VERDE_EN_PANTALLA_MS : false;
  // Visto: estando en FULLFILMENT, pasado un momento de haber terminado.
  useEffect(() => {
    if (!ultimo || pensando || !enFull || reciente || visto === ultimo.id) return;
    guardarVisto(ultimo.id);
    setVisto(ultimo.id);
  }, [ultimo, pensando, enFull, reciente, visto]);

  if (a?.corriendo) {
    const min = Math.max(1, Math.round(a.corriendo.segundos / 60));
    return <Chispa estado="pensando" titulo={`La IA está armando el FULL de la ${a.semana.split("-")[1]} (${min} min). Puedes seguir en otras pantallas: el resultado se guarda solo en el plan.`} />;
  }
  if (ultimo && (visto !== ultimo.id || reciente)) {
    return ultimo.estado === "error"
      ? <Chispa estado="error" titulo={`La IA no pudo terminar el turno de la ${a?.semana.split("-")[1]}: ${ultimo.motivo ?? "error"}. Ábrelo en FULLFILMENT.`} />
      : <Chispa estado="listo" titulo={`La IA terminó el FULL de la ${a?.semana.split("-")[1]}: ${ultimo.skus} SKUs · ${ultimo.piezas.toLocaleString("es-MX")} pzs. Ya está en el plan de FULLFILMENT.`} />;
  }
  return null;
}

// Una chispa de rayos, como la de Claude: el largo de cada rayo varía para que se vea viva.
const RAYOS = [9.5, 7, 8.6, 6.6, 9.5, 7.4, 8.8, 6.8, 9.5, 7.2, 8.6, 7];

function Chispa({ estado, titulo }: { estado: "pensando" | "listo" | "error"; titulo: string }) {
  const color = estado === "pensando" ? "animate-respira text-[#D97757]"
    : estado === "listo" ? "text-emerald-500" : "text-rose-500";
  return (
    <span role="status" aria-label={titulo} title={titulo}
          className={`relative -my-1 inline-flex h-[18px] w-[18px] items-center justify-center ${color}`}>
      {estado !== "pensando" && <span className="absolute inset-0 rounded-full bg-current opacity-20 blur-[3px]" />}
      <svg viewBox="0 0 24 24" className={`relative h-[18px] w-[18px] ${estado === "pensando" ? "animate-[spin_9s_linear_infinite]" : ""}`}
           aria-hidden="true">
        {RAYOS.map((largo, i) => (
          <line key={i} x1="12" y1={12 - 2.4} x2="12" y2={12 - largo} transform={`rotate(${i * 30} 12 12)`}
                stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        ))}
      </svg>
    </span>
  );
}
