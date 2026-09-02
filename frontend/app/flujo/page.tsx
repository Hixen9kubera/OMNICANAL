"use client";

/**
 * /flujo — la Red viva: el flujo de datos de kubera latiendo en tiempo real.
 *
 * Dos temas, mismas señales, y la elección es del usuario (persistida en
 * localStorage):
 *
 *   A · "sala de control" — el grafo conserva su fondo oscuro como instrumento
 *       embebido: partículas y anillos leen mejor sobre oscuro.
 *   B · "clara" — 100% el vocabulario del panel: tarjeta blanca, flujo índigo,
 *       y la fila de estadísticas arriba como en el dashboard.
 *
 * La página es dueña del sondeo (cada 10 s) y le pasa el pulso al canvas por
 * props: una sola señal alimenta grafo, KPIs, salud y silencios. El detalle
 * de un nodo se pide solo al hacer clic.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Moon, RefreshCw, Sun, X } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import RedViva, { type PulsoRed, type TemaRed } from "@/components/RedViva";

interface Salud { k: string; v: string; estado: "ok" | "aviso" | "mal" | "info" }
interface Silencio { de: string; a: string; min: number | null; cadencia: number; desde_arranque: boolean }
interface Muestra { t: string; epm: number | null; web: number; fan: number }
interface ErrorFila { proceso: string; origen: string; accion: string; cuando: string }
interface Pulso extends PulsoRed {
  ts: string; ventana_min: number; intervalo_s: number | null;
  salud: Salud[]; silencios: Silencio[]; historia: Muestra[]; errores: ErrorFila[];
}
interface Evento { cuando: string; a: string; b: string; c: string }

const PUNTO: Record<string, string> = {
  ok: "bg-emerald-500", aviso: "bg-amber-500", mal: "bg-rose-500", info: "bg-slate-300",
};

// Leyendas de esquema por tema. Las mismas familias; el claro usa tonos 600.
const LEYENDA: Record<TemaRed, [string, string][]> = {
  oscuro: [["core", "#4ADE9B"], ["channel", "#5EB8F0"], ["costing", "#F0B45E"],
           ["enrich", "#C08BF0"], ["ops", "#F07C9B"], ["analytics", "#5ED8D8"]],
  claro:  [["core", "#059669"], ["channel", "#2563eb"], ["costing", "#d97706"],
           ["enrich", "#7c3aed"], ["ops", "#db2777"], ["analytics", "#0891b2"]],
};

function Sparkline({ serie }: { serie: number[] }) {
  if (!serie.length) return null;
  const max = Math.max(...serie, 1);
  const pts = serie.map((v, i) =>
    `${(100 * i / Math.max(serie.length - 1, 1)).toFixed(1)},${(30 - 26 * v / max).toFixed(1)}`).join(" ");
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-8 w-full">
      <polyline points={pts} fill="none" stroke="#4f46e5" strokeWidth="1.5"
        vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ titulo, valor, sufijo, alerta = false }: {
  titulo: string; valor: string; sufijo?: string; alerta?: boolean;
}) {
  return (
    <div className={"rounded-xl p-3 " + (alerta
      ? "bg-amber-50 ring-1 ring-inset ring-amber-200"
      : "bg-white shadow-card")}>
      <p className={"text-[10px] font-semibold uppercase tracking-wide "
        + (alerta ? "text-amber-700" : "text-slate-400")}>{titulo}</p>
      <p className={"text-xl font-bold tabular-nums "
        + (alerta ? "text-amber-800" : "text-slate-900")}>
        {valor}{sufijo && <span className="text-xs font-medium text-slate-400"> {sufijo}</span>}
      </p>
    </div>
  );
}

export default function FlujoPage() {
  const [pulso, setPulso] = useState<Pulso | null>(null);
  const [caido, setCaido] = useState(false);
  const [nodo, setNodo] = useState<{ id: string; eventos: Evento[] | null; nota?: string } | null>(null);
  const [tema, setTema] = useState<TemaRed>("oscuro");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // El tema elegido se recuerda por navegador. Se lee en un efecto y no en el
  // useState inicial para no desincronizar la hidratación de Next.
  useEffect(() => {
    try {
      const t = localStorage.getItem("flujo.tema");
      if (t === "claro" || t === "oscuro") setTema(t);
    } catch { /* modo incógnito estricto: se queda el oscuro */ }
  }, []);
  const cambiarTema = useCallback(() => {
    setTema(t => {
      const nuevo = t === "oscuro" ? "claro" : "oscuro";
      try { localStorage.setItem("flujo.tema", nuevo); } catch { /* da igual */ }
      return nuevo;
    });
  }, []);

  const sondear = useCallback(async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/flujo/pulso`, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      setPulso(await r.json());
      setCaido(false);
    } catch { setCaido(true); }
  }, []);

  useEffect(() => {
    sondear();
    timer.current = setInterval(sondear, 10_000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [sondear]);

  const abrirNodo = useCallback(async (id: string) => {
    setNodo({ id, eventos: [] });
    try {
      const r = await fetchSesion(`${API_BASE}/api/flujo/nodo/${encodeURIComponent(id)}`,
        { cache: "no-store" });
      setNodo(await r.json());
    } catch { setNodo({ id, eventos: null, nota: "no se pudo leer" }); }
  }, []);

  const esc = pulso
    ? Object.values(pulso.tablas).reduce((s, t) => s + (t.escrituras || 0), 0)
    : 0;
  const porMin = pulso?.intervalo_s ? Math.round(esc / pulso.intervalo_s * 60) : 0;
  const eventos = pulso
    ? pulso.flujos.filter(f => (f as { bit?: boolean }).bit).reduce((s, f) => s + (f.n || 0), 0)
    : 0;
  const activas = pulso
    ? Object.values(pulso.tablas).filter(t => (t.escrituras || 0) > 0).length
    : 0;
  const totalTablas = pulso ? Object.keys(pulso.tablas).length : 0;
  // Para la fila de estadísticas del tema claro: dos datos que ya viajan en
  // salud, releídos de ahí para no duplicar consultas en el backend.
  const saludPor = (clave: string) => pulso?.salud.find(s => s.k.startsWith(clave));
  const webhooksPend = saludPor("webhooks sin procesar");
  const ventaML = saludPor("última venta · mercado_libre");

  const claro = tema === "claro";

  const barraLateral = (
    <aside className="flex w-full flex-col gap-3 lg:w-[330px]">
      {!claro && (
        <div className="grid grid-cols-2 gap-3">
          <Kpi titulo="Escrituras / min" valor={String(porMin)} />
          <Kpi titulo={`Eventos · ${pulso?.ventana_min ?? 15} min`}
            valor={eventos.toLocaleString("es-MX")} />
        </div>
      )}

      <div className="rounded-xl bg-white p-4 shadow-card">
        <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-500">Salud</p>
        <div className="flex flex-col gap-2">
          {(pulso?.salud ?? []).map(s => (
            <div key={s.k} className="flex items-center gap-2 text-xs text-slate-700">
              <span className={"h-2 w-2 flex-none rounded-full " + PUNTO[s.estado]} />
              <span className="font-semibold">{s.k}</span>
              <span className="ml-auto text-right tabular-nums text-slate-500">{s.v}</span>
            </div>
          ))}
          {!pulso && <p className="text-xs text-slate-400">cargando…</p>}
        </div>
      </div>

      <div className="rounded-xl bg-white p-4 shadow-card">
        <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-500">Silencios</p>
        {(pulso?.silencios?.length ?? 0) === 0 ? (
          <p className="text-xs text-slate-400">todo suena a su ritmo</p>
        ) : (
          <div className="flex flex-col gap-2">
            {pulso!.silencios.map(s => (
              <div key={s.de + s.a}
                className="border-l-2 border-amber-400 pl-2 text-xs text-slate-600">
                <span className="font-semibold text-slate-800">{s.de} → {s.a}</span>
                <br />
                {s.min === null ? "sin rastro de eventos"
                  : s.desde_arranque ? `sin señal desde el arranque (${s.min} min)`
                  : `callado ${s.min} min · su ritmo es cada ${s.cadencia}`}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-xl bg-white p-4 shadow-card">
        <div className="mb-1 flex items-baseline justify-between">
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-500">Actividad</p>
          <p className="text-[11px] tabular-nums text-slate-400">
            pico {Math.max(...(pulso?.historia ?? []).map(h => h.epm || 0), 0)}
          </p>
        </div>
        <Sparkline serie={(pulso?.historia ?? []).map(h => h.epm || 0)} />
      </div>

      {nodo && (
        <div className="rounded-xl bg-white p-4 shadow-card">
          <div className="mb-2 flex items-center justify-between">
            <p className="font-mono text-[11px] font-bold text-slate-700">{nodo.id}</p>
            <button onClick={() => setNodo(null)}
              className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          {nodo.eventos === null ? (
            <p className="text-xs text-slate-400">{nodo.nota}</p>
          ) : nodo.eventos.length === 0 ? (
            <p className="text-xs text-slate-400">cargando…</p>
          ) : (
            <div className="flex flex-col divide-y divide-slate-100">
              {nodo.eventos.map((e, i) => (
                <div key={i} className="py-1.5 text-xs text-slate-600">
                  <span className="font-semibold text-slate-800">{e.a}</span> · {e.b} · {e.c}
                  <span className="block text-[10px] tabular-nums text-slate-400">
                    {new Date(e.cuando).toLocaleString("es-MX")}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {(pulso?.errores?.length ?? 0) > 0 && (
        <div className="rounded-xl bg-white p-4 shadow-card">
          <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-500">
            Últimos errores
          </p>
          <div className="flex flex-col gap-2">
            {pulso!.errores.slice(0, 3).map((e, i) => (
              <div key={i} className="border-l-2 border-rose-400 pl-2 text-xs text-slate-600">
                <span className="font-semibold text-slate-800">{e.proceso} · {e.origen}</span>
                <br />{e.accion}
                <span className="block text-[10px] tabular-nums text-slate-400">
                  {new Date(e.cuando).toLocaleString("es-MX")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!pulso && !caido && (
        <div className="rounded-xl bg-white p-8 text-center ring-1 ring-slate-200">
          <Activity className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm font-medium text-slate-900">Tomando el pulso…</p>
        </div>
      )}
    </aside>
  );

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">

        <header className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-slate-500">
              Operaciones · tiempo real
            </p>
            <h1 className="mt-1 flex items-baseline gap-3 text-2xl font-bold text-slate-900">
              Red viva
              <span className="text-sm font-normal text-slate-600">
                {claro ? "quién escribe qué en kubera, ahora mismo"
                  : "el flujo de datos entrando y saliendo de kubera"}
              </span>
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 rounded-lg bg-white px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200">
              <span className={"h-2 w-2 rounded-full " + (caido ? "bg-rose-500" : "bg-emerald-500")} />
              {caido ? "sin conexión — reintentando"
                : pulso ? `${new Date(pulso.ts).toLocaleTimeString("es-MX")} · ventana ${pulso.ventana_min} min`
                : "conectando…"}
            </div>
            <button onClick={cambiarTema}
              title={claro ? "cambiar a sala de control" : "cambiar a versión clara"}
              className="flex items-center gap-1.5 rounded-md bg-white px-2.5 py-2 text-xs font-medium text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
              {claro ? <Moon className="h-3.5 w-3.5" /> : <Sun className="h-3.5 w-3.5" />}
              {claro ? "sala de control" : "clara"}
            </button>
            <button onClick={sondear}
              className="rounded-md bg-white p-2 text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
        </header>

        {claro && (
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Kpi titulo="Escrituras / min" valor={String(porMin)} />
            <Kpi titulo="Tablas activas" valor={String(activas)} sufijo={`de ${totalTablas}`} />
            <Kpi titulo={`Eventos · ${pulso?.ventana_min ?? 15} min`}
              valor={eventos.toLocaleString("es-MX")} />
            <Kpi titulo="Webhooks sin procesar"
              valor={webhooksPend ? webhooksPend.v.split(" ")[0] : "—"}
              alerta={webhooksPend?.estado === "aviso" || webhooksPend?.estado === "mal"} />
            <Kpi titulo="Última venta ML"
              valor={ventaML ? ventaML.v.split(" · ")[0].replace("hace ", "") : "—"} />
          </div>
        )}

        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">

          {/* Altura PROPIA y sticky: si la tarjeta se estirara con el flex, cada
              tarjeta que abra la barra lateral (detalle de nodo, errores) haría
              crecer el grafo y la página entera — pasó. Así el grafo llena la
              pantalla y la barra se desplaza por su cuenta. */}
          <div className={"relative h-[62vh] min-h-[480px] flex-1 overflow-hidden rounded-2xl lg:sticky lg:top-4 lg:h-[calc(100vh-190px)] "
            + (claro ? "bg-white shadow-card" : "bg-[#0B0F0E] ring-1 ring-slate-900/60")}>
            <RedViva pulso={pulso} onNodo={abrirNodo} tema={tema} />
            <div className={"pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-center gap-x-4 gap-y-1 border-t px-4 py-2 text-[10px] "
              + (claro ? "border-slate-100 text-slate-500" : "border-[#1F2C2A] text-[#93A09D]")}>
              {LEYENDA[tema].map(([nombre, color]) => (
                <span key={nombre} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-[2px]" style={{ background: color }} />
                  {nombre}
                </span>
              ))}
              <span className={"ml-auto " + (claro ? "text-slate-400" : "text-[#5E6D6A]")}>
                {claro ? "anillo índigo" : "anillo verde"} = escrituras · doble clic = encuadrar · clic en nodo = detalle
              </span>
            </div>
          </div>

          {barraLateral}
        </div>
      </main>
    </div>
  );
}
