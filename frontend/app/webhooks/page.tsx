"use client";

/**
 * /webhooks — Qué webhooks existen, cómo están funcionando, y qué han recibido.
 *
 * POR QUÉ ESTA PANTALLA. Hasta ahora saber si un webhook estaba vivo exigía
 * leer logs de Railway. Con dos canales por webhook (Mercado Libre y TikTok) y
 * dos por sondeo (Amazon y Temu), la pregunta "¿está entrando algo?" no tenía
 * dónde contestarse.
 *
 * LA DISTINCIÓN QUE IMPORTA. Un canal puede estar en tres situaciones y
 * confundirlas lleva a buscar bugs donde no los hay:
 *   · VIVO            — recibe y procesa
 *   · EN OBSERVACIÓN  — recibe y solo registra (TikTok, fase 1)
 *   · SIN WEBHOOK     — la plataforma NO los ofrece; va por sondeo
 * Amazon y Temu son el tercer caso. NO son un pendiente, y la pantalla lo dice
 * con todas sus letras para que nadie los ande buscando.
 *
 * Solo admin: el log de TikTok trae datos del comprador (TikTok marca el scope
 * `seller.order.info` como "contiene información personal de los clientes").
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, CheckCircle2, Clock, Eye, Radio,
  RefreshCw, Timer, Webhook,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

interface Canal {
  canal: string;
  estado: string;
  url: string | null;
  persistencia?: string;
  procesa_pedidos?: boolean;
  descuenta_stock?: boolean;
  eventos_en_memoria?: number;
  app_configurada?: boolean;
  canal_encendido?: boolean;
}

interface EventoTikTok {
  recibido: string;
  tipo: number | string | null;
  shop_id: string | null;
  firma_ok: boolean | null;
  bytes: number;
  payload: Record<string, unknown>;
}

const NOMBRE: Record<string, string> = {
  mercado_libre: "Mercado Libre",
  tiktok: "TikTok Shop",
  amazon: "Amazon",
  temu: "Temu",
};

/** Los cuatro temas que la consola de TikTok ofrece hoy. */
const TEMA_TIKTOK: Record<string, string> = {
  "2": "Estado de devolución",
  "3": "Dirección del destinatario",
  "4": "Actualización del paquete",
  "5": "Cambio de estado del producto",
};

function Pastilla({ estado }: { estado: string }) {
  const vivo = estado === "vivo";
  const observa = estado === "observacion";
  const sondeo = estado.startsWith("sin webhook");
  const color = vivo
    ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30"
    : observa
    ? "bg-sky-500/15 text-sky-400 ring-sky-500/30"
    : sondeo
    ? "bg-zinc-500/15 text-zinc-400 ring-zinc-500/30"
    : "bg-amber-500/15 text-amber-400 ring-amber-500/30";
  const Icono = vivo ? CheckCircle2 : observa ? Eye : sondeo ? Timer : AlertTriangle;
  const texto = vivo ? "VIVO" : observa ? "OBSERVACIÓN" : sondeo ? "SONDEO" : estado.toUpperCase();
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1
                      text-xs font-medium ring-1 ${color}`}>
      <Icono className="h-3.5 w-3.5" />
      {texto}
    </span>
  );
}

export default function WebhooksPage() {
  const [canales, setCanales] = useState<Canal[]>([]);
  const [eventos, setEventos] = useState<EventoTikTok[]>([]);
  const [porTipo, setPorTipo] = useState<Record<string, number>>({});
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ultima, setUltima] = useState<Date | null>(null);

  const cargar = useCallback(async () => {
    try {
      const [rc, rl] = await Promise.all([
        fetchSesion(`${API_BASE}/api/webhooks/activos`, { cache: "no-store" }),
        fetchSesion(`${API_BASE}/api/webhooks/tiktok/log?limite=50`, { cache: "no-store" }),
      ]);
      if (!rc.ok) throw new Error(`No se pudo leer el estado (HTTP ${rc.status})`);
      const jc = await rc.json();
      setCanales(jc.canales ?? []);
      // El log puede fallar sin que la pantalla deje de servir: se degrada.
      if (rl.ok) {
        const jl = await rl.json();
        setEventos(jl.eventos ?? []);
        setPorTipo(jl.por_tipo ?? {});
      }
      setError(null);
      setUltima(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setCargando(false);
    }
  }, []);

  // Refresco cada 15 s: los webhooks entran solos y la pantalla debe delatarlo
  // sin que nadie recargue.
  useEffect(() => {
    cargar();
    const t = setInterval(cargar, 15_000);
    return () => clearInterval(t);
  }, [cargar]);

  return (
    <>
      <AppNavbar />
      <main className="mx-auto max-w-6xl px-4 py-8">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-semibold">
              <Webhook className="h-6 w-6 text-indigo-400" />
              Webhooks
            </h1>
            <p className="mt-1 text-sm text-zinc-400">
              Qué canales notifican solos, cuáles van por sondeo, y qué ha entrado.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {ultima && (
              <span className="text-xs text-zinc-500">
                actualizado {ultima.toLocaleTimeString("es-MX")}
              </span>
            )}
            <button
              onClick={cargar}
              className="inline-flex items-center gap-2 rounded-lg bg-zinc-800 px-3 py-2
                         text-sm hover:bg-zinc-700"
            >
              <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
              Actualizar
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-amber-500/30
                          bg-amber-500/10 p-4 text-sm text-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <section className="grid gap-4 sm:grid-cols-2">
          {canales.map((c) => (
            <article
              key={c.canal}
              className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5"
            >
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 font-medium">
                  <Radio className="h-4 w-4 text-zinc-500" />
                  {NOMBRE[c.canal] ?? c.canal}
                </h2>
                <Pastilla estado={c.estado} />
              </div>

              {c.url ? (
                <code className="block break-all rounded bg-zinc-950 px-2 py-1.5
                                 text-[11px] text-zinc-400">
                  {c.url}
                </code>
              ) : (
                /* Un canal sin webhook NO es un pendiente: esas plataformas no
                   los ofrecen. Se explica aquí para que nadie los busque. */
                <p className="text-sm text-zinc-500">{c.estado}</p>
              )}

              <dl className="mt-4 space-y-1.5 text-sm">
                {c.persistencia && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">Persistencia</dt>
                    <dd className="text-right text-zinc-300">{c.persistencia}</dd>
                  </div>
                )}
                {c.procesa_pedidos !== undefined && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">Crea pedidos</dt>
                    <dd className={c.procesa_pedidos ? "text-emerald-400" : "text-zinc-400"}>
                      {c.procesa_pedidos ? "sí" : "no"}
                    </dd>
                  </div>
                )}
                {c.descuenta_stock !== undefined && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">Descuenta stock</dt>
                    <dd className={c.descuenta_stock ? "text-emerald-400" : "text-zinc-400"}>
                      {c.descuenta_stock ? "sí" : "no"}
                    </dd>
                  </div>
                )}
                {c.eventos_en_memoria !== undefined && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">Eventos recibidos</dt>
                    <dd className="text-zinc-300">{c.eventos_en_memoria}</dd>
                  </div>
                )}
                {c.canal === "tiktok" && (
                  <div className="flex justify-between gap-3">
                    <dt className="text-zinc-500">Canal encendido</dt>
                    <dd className={c.canal_encendido ? "text-emerald-400" : "text-amber-400"}>
                      {c.canal_encendido ? "sí" : "no (TIKTOK_ENABLED)"}
                    </dd>
                  </div>
                )}
              </dl>
            </article>
          ))}
        </section>

        {/* ── Log de TikTok ───────────────────────────────────────────────── */}
        <section className="mt-10">
          <h2 className="mb-1 flex items-center gap-2 text-lg font-medium">
            <Activity className="h-5 w-5 text-sky-400" />
            Lo que ha mandado TikTok
          </h2>
          <p className="mb-4 text-sm text-zinc-500">
            Fase de observación: se registra en memoria y <strong>no se guarda en
            base de datos</strong>. El catálogo real de eventos se descubre
            viéndolo llegar — la consola ofrece cuatro temas y ninguno es
            &quot;pedido creado&quot;.
          </p>

          {Object.keys(porTipo).length > 0 && (
            <div className="mb-4 flex flex-wrap gap-2">
              {Object.entries(porTipo).map(([t, n]) => (
                <span key={t} className="rounded-full bg-zinc-800 px-3 py-1 text-xs">
                  {TEMA_TIKTOK[t] ?? `tipo ${t}`}: <strong>{n}</strong>
                </span>
              ))}
            </div>
          )}

          {eventos.length === 0 ? (
            <div className="rounded-xl border border-dashed border-zinc-800 p-8 text-center">
              <Clock className="mx-auto mb-2 h-6 w-6 text-zinc-600" />
              <p className="text-sm text-zinc-500">
                Todavía no llega ningún evento.
              </p>
              <p className="mt-1 text-xs text-zinc-600">
                Los eventos viven en memoria: un redeploy los borra.
              </p>
            </div>
          ) : (
            <ul className="space-y-2">
              {eventos.map((e, i) => (
                <li key={i} className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
                  <div className="flex flex-wrap items-center gap-3 text-sm">
                    <span className="font-medium text-sky-300">
                      {TEMA_TIKTOK[String(e.tipo)] ?? `tipo ${e.tipo}`}
                    </span>
                    <span className="text-xs text-zinc-500">
                      {new Date(e.recibido).toLocaleString("es-MX")}
                    </span>
                    {/* firma_ok en null = todavía no se puede evaluar: el
                        algoritmo exacto no está confirmado y por eso hoy solo
                        se observa, no se rechaza. */}
                    <span
                      className={`text-xs ${
                        e.firma_ok === true
                          ? "text-emerald-400"
                          : e.firma_ok === false
                          ? "text-rose-400"
                          : "text-zinc-500"
                      }`}
                    >
                      firma: {e.firma_ok === null ? "sin evaluar" : e.firma_ok ? "ok" : "NO COINCIDE"}
                    </span>
                    <span className="ml-auto text-xs text-zinc-600">{e.bytes} B</span>
                  </div>
                  <pre className="mt-2 max-h-32 overflow-auto rounded bg-zinc-950 p-2
                                  text-[11px] text-zinc-400">
                    {JSON.stringify(e.payload, null, 1)}
                  </pre>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </>
  );
}
