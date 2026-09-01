"use client";

/**
 * /monitoreo — Quién hizo qué en el panel, cuándo, y en qué canal.
 *
 * POR QUÉ ESTA PANTALLA. `ops.channel_submissions` guardaba 26,104
 * publicaciones sin una sola columna de usuario: se sabía que un SKU se publicó
 * en BEKURA a las 14:32, no si fue Thalía o Andrea. La pregunta "cuántas
 * publicaciones lleva cada quien" no tenía dónde contestarse.
 *
 * SIRVE PARA DOS COSAS A LA VEZ, y por eso muestra ÉXITOS E INTENTOS por
 * separado: 12 de 12 no es lo mismo que 12 de 40. Para productividad basta el
 * primer número; el segundo es el que señala dónde algo está rebotando.
 *
 * NO es solo-admin: el equipo debe poder ver su propio avance. No expone
 * costos ni márgenes, solo autoría.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, CheckCircle2, RefreshCw, User, XCircle,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

interface PorCanal { total: number; exitos: number }

interface Usuario {
  usuario: string;
  total: number;
  exitos: number;
  ultima: string | null;
  correos: string[];
  canales: Record<string, PorCanal>;
  procesos: Record<string, PorCanal>;
}

interface Movimiento {
  created_at: string;
  actor: string;
  proceso: string;
  accion: string;
  sku: string | null;
  estado: string;
  canal: string | null;
  cuenta: string | null;
  duracion_s: number | null;
}

/**
 * Cada proceso tiene su propio verbo. Decir "publicado" de un recalculo de costo
 * es simplemente falso, y era lo que hacia esta pantalla hasta el 1-sep.
 */
const RESULTADO_OK: Record<string, string> = {
  publicar: "publicado",
  costos: "costo validado",
  crear: "producto creado",
  precio: "precio editado",
  stock: "stock editado",
};

/** Lo que se muestra en la columna CANAL cuando el movimiento no es de canal. */
const TIPO: Record<string, string> = {
  costos: "Costos",
  crear: "Crear productos",
  precio: "Precio",
  stock: "Stock",
};

const NOMBRE_CANAL: Record<string, string> = {
  mercado_libre: "Mercado Libre",
  amazon: "Amazon",
  tiktok: "TikTok Shop",
  temu: "Temu",
  walmart: "Walmart",
  "(sin canal)": "Sin canal",
};

/** `mercado_libre·BEKURA` se muestra como `Mercado Libre · BEKURA`. */
function bonito(etiqueta: string): string {
  const partes = etiqueta.split("·");
  const canal = partes[0];
  const cuenta = partes[1];
  const n = NOMBRE_CANAL[canal] ?? canal;
  return cuenta ? n + " · " + cuenta : n;
}

/**
 * El desglose de la tarjeta: los movimientos SIN canal se agrupan por tipo, no
 * bajo un "Sin canal" que no dice nada. Un KAM que valido 18 costos deberia ver
 * "Costos 18", no "Sin canal 18".
 */
function desglose(u: Usuario): Array<[string, PorCanal]> {
  const salida: Record<string, PorCanal> = {};
  for (const [etiqueta, d] of Object.entries(u.canales)) {
    if (etiqueta !== "(sin canal)") {
      salida[bonito(etiqueta)] = d;
    }
  }
  // Lo que no tiene canal se reparte por proceso, que es lo que de verdad es.
  const sinCanal = u.canales["(sin canal)"];
  if (sinCanal) {
    for (const [proc, d] of Object.entries(u.procesos)) {
      if (proc === "publicar") continue;   // publicar siempre trae canal
      const k = TIPO[proc] ?? proc;
      salida[k] = { total: (salida[k]?.total ?? 0) + d.total,
                    exitos: (salida[k]?.exitos ?? 0) + d.exitos };
    }
  }
  return Object.entries(salida).sort((a, b) => b[1].total - a[1].total);
}

function persona(correo: string): string {
  const n = correo.split("@")[0].replace(/[._]/g, " ");
  return n.replace(/\b\w/g, (c) => c.toUpperCase());
}

function cuando(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return "hace " + min + " min";
  if (min < 1440) return "hace " + Math.round(min / 60) + " h";
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}

const DIAS = [
  { v: 1, t: "Hoy" },
  { v: 7, t: "7 días" },
  { v: 30, t: "30 días" },
  { v: 90, t: "90 días" },
];

export default function MonitoreoPage() {
  const [usuarios, setUsuarios] = useState<Usuario[]>([]);
  const [movs, setMovs] = useState<Movimiento[]>([]);
  const [dias, setDias] = useState(30);
  const [foco, setFoco] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const q = foco ? "&usuario=" + encodeURIComponent(foco) : "";
      const [r1, r2] = await Promise.all([
        fetchSesion(API_BASE + "/api/monitoreo/resumen?dias=" + dias,
          { cache: "no-store" }),
        fetchSesion(
          API_BASE + "/api/monitoreo/movimientos?dias=" + dias + "&limite=80" + q,
          { cache: "no-store" }),
      ]);
      if (!r1.ok) throw new Error("HTTP " + r1.status);
      const d1 = await r1.json();
      setUsuarios(d1.usuarios ?? []);
      setMovs(r2.ok ? (await r2.json()).movimientos ?? [] : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "no se pudo leer el monitoreo");
    } finally {
      setCargando(false);
    }
  }, [dias, foco]);

  useEffect(() => { void cargar(); }, [cargar]);

  const totalGeneral = usuarios.reduce((s, u) => s + u.total, 0);
  const exitosGeneral = usuarios.reduce((s, u) => s + u.exitos, 0);

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto max-w-6xl px-4 py-8">

        <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-slate-500">
              Monitoreo &middot; actividad del equipo
            </p>
            <h1 className="mt-1 text-2xl font-bold text-slate-900">
              Qui&eacute;n hizo qu&eacute;
            </h1>
            <p className="mt-1 max-w-xl text-sm text-slate-600">
              Publicaciones, costos validados y productos creados, por persona.
              Los movimientos autom&aacute;ticos &mdash;fan-out, sondeos, Odoo&mdash; no
              aparecen: no los hizo nadie.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {DIAS.map((d) => (
              <button
                key={d.v}
                onClick={() => setDias(d.v)}
                className={
                  "rounded-md px-3 py-1.5 text-sm font-medium transition " +
                  (dias === d.v
                    ? "bg-violet-600 text-white"
                    : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50")
                }
              >
                {d.t}
              </button>
            ))}
            <button
              onClick={() => void cargar()}
              className="rounded-md bg-white p-2 text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              aria-label="Actualizar"
            >
              <RefreshCw className={"h-4 w-4 " + (cargando ? "animate-spin" : "")} />
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-6 flex items-start gap-2 rounded-lg bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />
            <span>No se pudieron leer los movimientos: {error}</span>
          </div>
        )}

        {!cargando && !error && usuarios.length === 0 && (
          <div className="rounded-lg bg-white p-8 text-center ring-1 ring-slate-200">
            <Activity className="mx-auto h-8 w-8 text-slate-300" />
            <p className="mt-3 font-medium text-slate-900">
              Todav&iacute;a no hay movimientos registrados
            </p>
            <p className="mx-auto mt-1 max-w-md text-sm text-slate-600">
              El registro empez&oacute; el 1 de septiembre de 2026. Las publicaciones
              anteriores existen, pero no se guard&oacute; qui&eacute;n las hizo, y eso
              no se puede reconstruir.
            </p>
          </div>
        )}

        {usuarios.length > 0 && (
          <>
            <div className="mb-6 grid gap-px overflow-hidden rounded-lg bg-slate-200 ring-1 ring-slate-200 sm:grid-cols-3">
              <div className="bg-white p-4">
                <p className="font-mono text-xs uppercase tracking-wider text-slate-500">
                  Personas activas
                </p>
                <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900">
                  {usuarios.length}
                </p>
              </div>
              <div className="bg-white p-4">
                <p className="font-mono text-xs uppercase tracking-wider text-slate-500">
                  Completados
                </p>
                <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900">
                  {exitosGeneral}
                </p>
              </div>
              <div className="bg-white p-4">
                <p className="font-mono text-xs uppercase tracking-wider text-slate-500">
                  Intentos
                </p>
                <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900">
                  {totalGeneral}
                </p>
                {totalGeneral > exitosGeneral && (
                  <p className="text-xs text-amber-700">
                    {totalGeneral - exitosGeneral} no llegaron al canal
                  </p>
                )}
              </div>
            </div>

            <div className="mb-8 space-y-3">
              {usuarios.map((u) => {
                const fallidos = u.total - u.exitos;
                const activo = foco === u.usuario;
                return (
                  <div
                    key={u.usuario}
                    className={
                      "rounded-lg bg-white p-5 ring-1 transition " +
                      (activo ? "ring-2 ring-violet-400" : "ring-slate-200")
                    }
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 rounded-full bg-violet-100 p-2">
                          <User className="h-4 w-4 text-violet-700" />
                        </div>
                        <div>
                          <p className="font-semibold text-slate-900">
                            {persona(u.usuario)}
                          </p>
                          <p className="font-mono text-xs text-slate-500">{u.usuario}</p>
                          {u.correos.length > 1 && (
                            <p className="mt-1 text-xs text-amber-700">
                              Dos cuentas fusionadas: {u.correos.join(", ")}
                            </p>
                          )}
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-2xl font-bold tabular-nums text-slate-900">
                          {u.exitos}
                          {fallidos > 0 && (
                            <span className="text-base font-normal text-slate-400">
                              {" / " + u.total}
                            </span>
                          )}
                        </p>
                        <p className="text-xs text-slate-500">
                          {"última " + cuando(u.ultima)}
                        </p>
                      </div>
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      {desglose(u).map(([etiqueta, d]) => (
                          <span
                            key={etiqueta}
                            className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs text-slate-700"
                          >
                            {etiqueta}
                            <b className="tabular-nums">{d.exitos}</b>
                            {d.total > d.exitos && (
                              <span className="text-amber-700">{"de " + d.total}</span>
                            )}
                          </span>
                        ))}
                    </div>

                    <button
                      onClick={() => setFoco(activo ? null : u.usuario)}
                      className="mt-3 text-xs font-medium text-violet-700 hover:underline"
                    >
                      {activo ? "Ver todos los movimientos" : "Ver solo sus movimientos"}
                    </button>
                  </div>
                );
              })}
            </div>

            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">
              {foco ? "Movimientos de " + persona(foco) : "Movimientos"}
            </h2>
            <div className="overflow-x-auto rounded-lg ring-1 ring-slate-200">
              <table className="w-full min-w-[42rem] border-collapse bg-white text-sm">
                <thead>
                  <tr className="bg-slate-50 text-left font-mono text-xs uppercase tracking-wider text-slate-500">
                    <th className="px-3 py-2 font-semibold">Cu&aacute;ndo</th>
                    <th className="px-3 py-2 font-semibold">Qui&eacute;n</th>
                    <th className="px-3 py-2 font-semibold">SKU</th>
                    <th className="px-3 py-2 font-semibold">Canal / tipo</th>
                    <th className="px-3 py-2 font-semibold">Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {movs.map((m, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                        {new Date(m.created_at).toLocaleString("es-MX", {
                          day: "2-digit", month: "short",
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-slate-700">
                        {persona(m.actor)}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-900">
                        {m.sku ?? "—"}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                        {m.canal ? (
                          <>
                            {NOMBRE_CANAL[m.canal] ?? m.canal}
                            {m.cuenta && (
                              <span className="text-slate-400">{" · " + m.cuenta}</span>
                            )}
                          </>
                        ) : (
                          /* Sin canal no es un hueco: es que la accion no es de
                             canal. Decir QUE fue vale mas que un guion. */
                          <span className="text-slate-500">
                            {TIPO[m.proceso] ?? m.proceso}
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2">
                        {["ok", "completado", "succeeded"].includes(m.estado) ? (
                          <span className="inline-flex items-center gap-1 text-emerald-700">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            {" " + (RESULTADO_OK[m.proceso] ?? "hecho")}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-rose-700">
                            <XCircle className="h-3.5 w-3.5" /> {m.estado}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {movs.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-slate-500">
                        Sin movimientos en este periodo
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
