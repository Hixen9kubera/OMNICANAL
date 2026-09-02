"use client";

/**
 * RedVivaWidget — la miniatura de la Red viva para el dashboard de Operaciones.
 *
 * No sustituye a /flujo: es el semáforo con tres números que te dice desde el
 * dashboard si vale la pena entrar. Sondea más lento que la página (30 s) —
 * para un vistazo alcanza y no duplica la carga cuando ambas están abiertas.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Network } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import RedViva, { type PulsoRed } from "@/components/RedViva";

interface PulsoWidget extends PulsoRed {
  intervalo_s: number | null;
  silencios: { de: string; a: string }[];
  salud: { estado: string }[];
}

export default function RedVivaWidget() {
  const [pulso, setPulso] = useState<PulsoWidget | null>(null);

  useEffect(() => {
    let vivo = true;
    const sondear = async () => {
      try {
        const r = await fetchSesion(`${API_BASE}/api/flujo/pulso`, { cache: "no-store" });
        if (vivo && r.ok) setPulso(await r.json());
      } catch { /* el widget simplemente se queda quieto */ }
    };
    sondear();
    const t = setInterval(sondear, 30_000);
    return () => { vivo = false; clearInterval(t); };
  }, []);

  const esc = pulso
    ? Object.values(pulso.tablas).reduce((s, t) => s + (t.escrituras || 0), 0)
    : 0;
  const porMin = pulso?.intervalo_s ? Math.round(esc / pulso.intervalo_s * 60) : 0;
  const eventos = pulso
    ? pulso.flujos.filter(f => (f as { bit?: boolean }).bit).reduce((s, f) => s + (f.n || 0), 0)
    : 0;
  const silencios = pulso?.silencios?.length ?? 0;
  const roto = (pulso?.salud ?? []).some(s => s.estado === "mal");
  const estado = roto
    ? { punto: "bg-rose-500", texto: "algo está en rojo", tono: "text-rose-700" }
    : silencios > 0
    ? { punto: "bg-amber-500", texto: `${silencios} flujo${silencios > 1 ? "s" : ""} callado${silencios > 1 ? "s" : ""}`, tono: "text-amber-700" }
    : { punto: "bg-emerald-500", texto: "todo suena a su ritmo", tono: "text-emerald-700" };

  return (
    <section className="mb-6 rounded-2xl bg-white p-4 shadow-card">
      <div className="flex flex-wrap gap-4">
        <div className="relative h-44 w-72 flex-none overflow-hidden rounded-xl bg-[#0B0F0E]">
          <RedViva pulso={pulso} compacto />
          <span className="absolute bottom-1.5 left-2.5 text-[10px] text-[#5E6D6A]">
            miniatura en vivo · 30 s
          </span>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <Network size={17} className="text-indigo-600" />
            <h2 className="text-[15px] font-bold text-slate-900">Red viva</h2>
            <span className={"ml-auto flex items-center gap-1.5 text-[11px] font-semibold " + estado.tono}>
              <span className={"h-1.5 w-1.5 rounded-full " + estado.punto} />
              {estado.texto}
            </span>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-lg bg-slate-50 px-2.5 py-2">
              <p className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">
                Escrituras/min
              </p>
              <p className="text-lg font-bold tabular-nums text-slate-900">{porMin}</p>
            </div>
            <div className="rounded-lg bg-slate-50 px-2.5 py-2">
              <p className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">
                Eventos · 15 min
              </p>
              <p className="text-lg font-bold tabular-nums text-slate-900">
                {eventos.toLocaleString("es-MX")}
              </p>
            </div>
            <div className={"rounded-lg px-2.5 py-2 "
              + (silencios ? "bg-amber-50 ring-1 ring-inset ring-amber-200" : "bg-slate-50")}>
              <p className={"text-[9px] font-semibold uppercase tracking-wide "
                + (silencios ? "text-amber-700" : "text-slate-400")}>
                Silencios
              </p>
              <p className={"text-lg font-bold tabular-nums "
                + (silencios ? "text-amber-800" : "text-slate-900")}>
                {silencios}
              </p>
            </div>
          </div>

          <div className="mt-auto">
            <Link href="/flujo"
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-semibold text-white hover:bg-indigo-700">
              Abrir la red <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
