"use client";

/**
 * VENTAS — ventas por hora (00–23) con comparativa contra la semana pasada.
 *
 * · General = todas las cuentas sumadas; Mercado Libre permite elegir cuenta
 *   (Kubera / San Corpe / Todas). El color de TODA la vista cambia según el
 *   canal, igual que en Omnicanal.
 * · La comparativa SIEMPRE es contra el mismo rango de hace 7 días, en %.
 *   Para HOY (día incompleto) se compara contra la misma hora de la semana
 *   pasada — comparar contra el día completo daría un % engañoso.
 * · Los datos salen de las órdenes reales de ML (precio real de cada venta),
 *   no del catálogo. Ver backend services/ventas_ml.py.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  Banknote,
  Boxes,
  CalendarDays,
  Clock,
  PackageCheck,
  Receipt,
  RotateCw,
  ShoppingCart,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import AccountTabs from "@/components/AccountTabs";
import { ventasHorario } from "@/lib/api";
import type { VentasResumen } from "@/lib/types";
import { THEME_FALLBACK, esClaro, hexToRgba, variablesTema } from "@/lib/theme";

/* ── Canales de venta ─────────────────────────────────────────────────
 * Lista propia (no depende de /api/canales): Ventas solo tiene integrado
 * Mercado Libre; el resto se muestra "Pronto" hasta conectar sus órdenes. */
const CANALES_VENTA = [
  { id: "general", label: "General", habilitado: true },
  { id: "mercado_libre", label: "Mercado Libre", habilitado: true },
  { id: "amazon", label: "Amazon", habilitado: false },
  { id: "tiktok", label: "TikTok Shop", habilitado: false },
  { id: "walmart", label: "Walmart", habilitado: false },
  { id: "temu", label: "Temu", habilitado: false },
  { id: "shein", label: "Shein", habilitado: false },
] as const;

const SUBCUENTAS_ML = [
  { id: "BEKURA", label: "Kubera", es_default: true, total_productos: null },
  { id: "SANCORFASHION", label: "San Corpe", es_default: false, total_productos: null },
];

type Metrica = "monto" | "pedidos" | "unidades";

/* ── Formato ── */
const fmtMXN = (n: number, dec = 0) =>
  new Intl.NumberFormat("es-MX", {
    style: "currency", currency: "MXN",
    minimumFractionDigits: dec, maximumFractionDigits: dec,
  }).format(n);
const fmtInt = (n: number) => new Intl.NumberFormat("es-MX").format(n);
const fmtCompacto = (n: number) =>
  n >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `$${Math.round(n)}`;

/** Hoy en Ciudad de México (el backend bucketiza en esa zona). */
function hoyMX(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Mexico_City" });
}
function diasAntes(iso: string, dias: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - dias);
  return d.toISOString().slice(0, 10);
}
function fechaLegible(iso: string): string {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("es-MX", {
    weekday: "short", day: "numeric", month: "short", timeZone: "UTC",
  });
}

/* ── Chip de variación % ── */
function DeltaChip({ valor, invertir = false }: { valor: number | null; invertir?: boolean }) {
  if (valor === null || valor === undefined) {
    return (
      <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-400">
        s/ base
      </span>
    );
  }
  const positivo = invertir ? valor < 0 : valor >= 0;
  const Icono = valor >= 0 ? TrendingUp : TrendingDown;
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold tabular-nums",
        positivo ? "bg-emerald-50 text-emerald-600" : "bg-rose-50 text-rose-600",
      ].join(" ")}
    >
      <Icono size={12} />
      {valor > 0 ? "+" : ""}{valor.toFixed(1)}%
    </span>
  );
}

export default function VentasPage() {
  const [canal, setCanal] = useState<string>("general");
  const [cuenta, setCuenta] = useState<string | null>(null);
  const [desde, setDesde] = useState<string>(hoyMX());
  const [hasta, setHasta] = useState<string>(hoyMX());
  const [metrica, setMetrica] = useState<Metrica>("monto");

  const [data, setData] = useState<VentasResumen | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reintentos = useRef(0);

  const tema = THEME_FALLBACK[canal] ?? THEME_FALLBACK.general;
  const hoy = hoyMX();
  const esHoy = desde === hoy && hasta === hoy;
  const incluyeHoy = hasta >= hoy;

  const preset = useMemo(() => {
    if (esHoy) return "hoy";
    const ayer = diasAntes(hoy, 1);
    if (desde === ayer && hasta === ayer) return "ayer";
    if (desde === diasAntes(hoy, 6) && hasta === hoy) return "7d";
    return "custom";
  }, [desde, hasta, esHoy, hoy]);

  /* ── Carga ── */
  const cargar = useCallback((silencioso = false) => {
    const ctrl = new AbortController();
    if (!silencioso) setCargando(true);
    ventasHorario({ canal, cuenta: canal === "general" ? null : cuenta, desde, hasta }, ctrl.signal)
      .then((r) => {
        setData(r);
        setError(null);
        reintentos.current = 0;
      })
      .catch((exc) => {
        if (exc?.name === "AbortError") return;
        // Arranque en frío del backend (deploy): reintenta solo.
        if (reintentos.current < 20) {
          reintentos.current += 1;
          setTimeout(() => cargar(silencioso), 1500);
          return;
        }
        setError(String(exc?.message ?? exc));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [canal, cuenta, desde, hasta]);

  useEffect(() => cargar(), [cargar]);

  // EN VIVO: si el rango incluye hoy, refresca cada 60 s sin parpadear.
  useEffect(() => {
    if (!incluyeHoy) return;
    const t = setInterval(() => cargar(true), 60_000);
    return () => clearInterval(t);
  }, [incluyeHoy, cargar]);

  // La fuente pedidos no tiene "unidades": si quedó seleccionada, regresar a monto.
  useEffect(() => {
    if (data?.fuente === "pedidos" && metrica === "unidades") setMetrica("monto");
  }, [data?.fuente, metrica]);

  function seleccionarCanal(id: string, habilitado: boolean) {
    if (!habilitado || id === canal) return;
    setCanal(id);
    setCuenta(null); // ML arranca en "Todas"
  }

  function aplicarPreset(p: "hoy" | "ayer" | "7d") {
    if (p === "hoy") { setDesde(hoy); setHasta(hoy); }
    if (p === "ayer") { const a = diasAntes(hoy, 1); setDesde(a); setHasta(a); }
    if (p === "7d") { setDesde(diasAntes(hoy, 6)); setHasta(hoy); }
  }

  /* ── Derivados ── */
  const t = data?.totales;
  // Fuente "pedidos": el tab vive de los pedidos de WooCommerce (no de la API
  // de ML). Los pedidos no traen unidades, así que esa métrica se oculta; la
  // comparativa semanal aparece sola cuando el registro cumpla 7 días.
  const esPedidos = data?.fuente === "pedidos";
  const sinBaseSemanal = esPedidos && !!t && t.prev.monto === 0 && t.prev.pedidos === 0;
  const parcial = esHoy ? t?.parcial ?? null : null;
  // Con HOY la comparativa honesta es "a la misma hora de la semana pasada".
  const deltaMonto = parcial ? parcial.delta.monto : t?.delta.monto ?? null;
  const deltaPedidos = parcial ? parcial.delta.pedidos : t?.delta.pedidos ?? null;
  const deltaUnidades = parcial ? parcial.delta.unidades : t?.delta.unidades ?? null;

  const maxBarra = useMemo(() => {
    if (!data) return 1;
    let m = 0;
    for (const h of data.horas) {
      m = Math.max(m, h[metrica], h[`prev_${metrica}` as const] as number);
    }
    return m || 1;
  }, [data, metrica]);

  const pico = useMemo(() => {
    if (!data) return null;
    let mejor = null as null | { hora: number; valor: number };
    for (const h of data.horas) {
      if (h.monto > (mejor?.valor ?? 0)) mejor = { hora: h.hora, valor: h.monto };
    }
    return mejor && mejor.valor > 0 ? mejor : null;
  }, [data]);

  const horaActualMX = Number(
    new Date().toLocaleString("en-US", { timeZone: "America/Mexico_City", hour: "2-digit", hour12: false }),
  ) % 24;

  const rangoLegible = desde === hasta
    ? fechaLegible(desde)
    : `${fechaLegible(desde)} — ${fechaLegible(hasta)}`;

  const etiquetaComparativa = parcial
    ? `vs semana pasada a la misma hora (hasta ${String(parcial.hora_corte).padStart(2, "0")}:59)`
    : "vs mismo rango de la semana pasada";

  const kpis = t ? [
    {
      icono: Banknote, label: "Ventas brutas",
      valor: fmtMXN(t.monto), delta: deltaMonto,
      sub: parcial
        ? `sem. pasada a esta hora: ${fmtMXN(parcial.prev_monto)} · día completo: ${fmtMXN(t.prev.monto)}`
        : `sem. pasada: ${fmtMXN(t.prev.monto)}`,
    },
    {
      icono: ShoppingCart, label: "Pedidos",
      valor: fmtInt(t.pedidos), delta: deltaPedidos,
      sub: `sem. pasada: ${fmtInt(parcial ? parcial.prev_pedidos : t.prev.pedidos)}`,
    },
    ...(esPedidos ? [] : [{
      icono: Boxes, label: "Unidades",
      valor: fmtInt(t.unidades), delta: deltaUnidades,
      sub: `sem. pasada: ${fmtInt(parcial ? parcial.prev_unidades : t.prev.unidades)}`,
    }]),
    {
      icono: Receipt, label: "Ticket promedio",
      valor: fmtMXN(t.ticket, 2), delta: t.delta.ticket,
      sub: `sem. pasada: ${fmtMXN(t.prev.ticket, 2)}`,
    },
    {
      icono: Ban, label: "Canceladas",
      valor: fmtInt(t.canceladas), delta: null,
      sub: t.monto_cancelado ? `por ${fmtMXN(t.monto_cancelado)}` : "sin monto cancelado",
      alerta: t.canceladas > 0,
    },
  ] : [];

  return (
    <div className="min-h-screen" style={variablesTema(tema)}>
      <AppNavbar />

      <main className="mx-auto max-w-[1600px] px-4 pb-16 pt-6 sm:px-6">
        {/* ── Banner del canal (cambia de color) ── */}
        <div
          className="relative overflow-hidden rounded-3xl p-6 shadow-card transition-colors duration-300"
          style={{
            background: `linear-gradient(120deg, ${tema.color} 0%, ${hexToRgba(tema.acento, 0.92)} 100%)`,
            color: tema.texto,
          }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                Ventas · {CANALES_VENTA.find((c) => c.id === canal)?.label}
                {canal === "mercado_libre" && cuenta
                  ? ` · ${SUBCUENTAS_ML.find((s) => s.id === cuenta)?.label}`
                  : canal === "mercado_libre" ? " · Todas las cuentas" : ""}
              </div>
              <h1 className="mt-1 text-3xl font-extrabold tracking-tight capitalize">
                {rangoLegible}
              </h1>
              <p className="mt-1 flex items-center gap-2 text-sm opacity-90">
                <Clock size={14} />
                {esPedidos ? "pedidos de WooCommerce en tiempo real" : etiquetaComparativa}
                {incluyeHoy && (
                  <span className="ml-1 inline-flex items-center gap-1.5 rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-300" />
                    En vivo
                  </span>
                )}
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {t ? fmtMXN(t.monto) : "—"}
              </div>
              <div className="mt-1 flex items-center justify-end gap-2">
                <DeltaChip valor={deltaMonto} />
                <span className="text-xs font-semibold uppercase tracking-wide opacity-80">
                  vs sem. pasada
                </span>
              </div>
            </div>
          </div>
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
            style={{ background: tema.texto }}
          />
        </div>

        {/* ── Selector de canal ── */}
        <div className="mt-6 flex flex-wrap items-center gap-2">
          {CANALES_VENTA.map((c) => {
            const th = THEME_FALLBACK[c.id] ?? THEME_FALLBACK.general;
            const sel = c.id === canal;
            const borde = esClaro(th.color) ? "#E2E4ED" : th.color;
            return (
              <button
                key={c.id}
                type="button"
                disabled={!c.habilitado}
                onClick={() => seleccionarCanal(c.id, c.habilitado)}
                title={c.habilitado ? `Ventas de ${c.label}` : "Próximamente"}
                style={sel
                  ? { backgroundColor: th.color, color: th.texto, borderColor: th.color, boxShadow: `0 6px 16px -6px ${th.color}` }
                  : c.habilitado ? { borderColor: borde, color: "#374151" } : {}}
                className={[
                  "relative flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition-all",
                  sel ? "scale-[1.02]"
                    : c.habilitado ? "bg-white hover:-translate-y-0.5 hover:shadow-card"
                      : "cursor-not-allowed border-dashed border-slate-200 bg-slate-50 text-slate-400",
                ].join(" ")}
              >
                <span
                  className="h-2.5 w-2.5 rounded-full ring-2 ring-white/40"
                  style={{ backgroundColor: sel ? th.texto : th.color }}
                />
                {c.label}
                {!c.habilitado && (
                  <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                    Pronto
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* ── Cuenta (solo ML) + filtros de fecha ── */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            {canal === "mercado_libre" && (
              <AccountTabs
                subcuentas={SUBCUENTAS_ML}
                activa={cuenta}
                color={tema.color}
                textoColor={tema.texto}
                onSelect={(c) => setCuenta(c)}
              />
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Presets */}
            <div className="inline-flex rounded-lg bg-slate-100 p-1">
              {([["hoy", "Hoy"], ["ayer", "Ayer"], ["7d", "Últimos 7 días"]] as const).map(([id, label]) => {
                const sel = preset === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => aplicarPreset(id)}
                    style={sel ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                    className={[
                      "rounded-md px-3 py-1.5 text-sm font-semibold transition-all",
                      sel ? "shadow-sm" : "text-slate-500 hover:text-slate-800",
                    ].join(" ")}
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            {/* Rango personalizado */}
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
              <CalendarDays size={15} className="text-slate-400" />
              <input
                type="date"
                value={desde}
                max={hoy}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) return;
                  setDesde(v);
                  if (v > hasta) setHasta(v);
                }}
                className="bg-transparent text-sm font-medium text-slate-700 outline-none"
              />
              <span className="text-slate-300">—</span>
              <input
                type="date"
                value={hasta}
                min={desde}
                max={hoy}
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) return;
                  setHasta(v);
                  if (v < desde) setDesde(v);
                }}
                className="bg-transparent text-sm font-medium text-slate-700 outline-none"
              />
            </div>

            <button
              onClick={() => cargar()}
              title="Recargar"
              className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-50"
            >
              <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {/* ── Error ── */}
        {error && !cargando && (
          <div className="mt-6 flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <span>No se pudieron cargar las ventas: {error}</span>
            <button
              onClick={() => { reintentos.current = 0; cargar(); }}
              className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-bold text-white"
            >
              Reintentar
            </button>
          </div>
        )}

        {/* ── KPIs ── */}
        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {cargando && !data
            ? Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-[120px] animate-pulse rounded-2xl bg-slate-100" />
              ))
            : kpis.map((k) => {
                const Icono = k.icono;
                return (
                  <div
                    key={k.label}
                    className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition-shadow hover:shadow-card"
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className="flex h-8 w-8 items-center justify-center rounded-lg"
                        style={{ backgroundColor: hexToRgba(tema.color, 0.12), color: esClaro(tema.color) ? tema.acento : tema.color }}
                      >
                        <Icono size={16} />
                      </span>
                      {"alerta" in k && k.alerta
                        ? <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-bold text-rose-600">{k.sub}</span>
                        : <DeltaChip valor={k.delta} />}
                    </div>
                    <div className="mt-3 text-2xl font-extrabold tabular-nums tracking-tight text-slate-900">
                      {k.valor}
                    </div>
                    <div className="text-xs font-semibold text-slate-500">{k.label}</div>
                    {!("alerta" in k && k.alerta) && (
                      <div className="mt-1 truncate text-[11px] text-slate-400" title={k.sub}>{k.sub}</div>
                    )}
                  </div>
                );
              })}
        </div>

        {/* ── Pedidos registrados en WooCommerce (flujo ML→WC) ── */}
        {data?.pedidos_wc && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
            <div className="flex items-center gap-3">
              <span
                className="flex h-10 w-10 items-center justify-center rounded-xl"
                style={{ backgroundColor: hexToRgba(tema.color, 0.12), color: esClaro(tema.color) ? tema.acento : tema.color }}
              >
                <PackageCheck size={20} />
              </span>
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
                  Pedidos en WooCommerce
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-600">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                    Registro vivo
                  </span>
                </div>
                <div className="text-[11px] text-slate-400">
                  Cada venta de ML se congela como pedido con su precio real · desde el 17 jul 2026
                </div>
              </div>
            </div>

            {data.pedidos_wc.total > 0 ? (
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                <div className="text-right">
                  <div className="text-xl font-extrabold tabular-nums text-slate-900">
                    {fmtInt(data.pedidos_wc.total)} <span className="text-sm font-semibold text-slate-400">pedidos</span>
                  </div>
                  <div className="text-xs font-semibold text-slate-500">{fmtMXN(data.pedidos_wc.monto)}</div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {SUBCUENTAS_ML.filter((s) => data.pedidos_wc!.cuentas[s.id]?.pedidos).map((s) => {
                    const c = data.pedidos_wc!.cuentas[s.id];
                    return (
                      <span
                        key={s.id}
                        className="rounded-full px-2.5 py-1 text-[11px] font-bold"
                        style={{
                          backgroundColor: hexToRgba(THEME_FALLBACK.mercado_libre.color, 0.35),
                          color: THEME_FALLBACK.mercado_libre.texto,
                        }}
                      >
                        {s.label} {fmtInt(c.pedidos)} · {fmtMXN(c.monto)}
                      </span>
                    );
                  })}
                </div>
                <div className="flex items-center gap-3 text-[11px] font-semibold text-slate-500">
                  <span>FULL {fmtInt(data.pedidos_wc.full)}</span>
                  <span>propios {fmtInt(data.pedidos_wc.propios)}</span>
                  {data.pedidos_wc.cancelados > 0 && (
                    <span className="text-rose-500">cancelados {fmtInt(data.pedidos_wc.cancelados)}</span>
                  )}
                </div>
              </div>
            ) : (
              <div className="text-xs text-slate-400">
                Sin pedidos en este rango — el registro existe desde el 17 jul 2026.
              </div>
            )}
          </div>
        )}

        {/* ── Gráfica horaria ── */}
        <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-bold text-slate-900">Ventas por hora</h2>
              <p className="text-xs text-slate-500">
                {etiquetaComparativa}
                {sinBaseSemanal && (
                  <span className="ml-1 text-slate-400">
                    · la comparativa se llenará sola cuando el registro cumpla una semana (24 jul)
                  </span>
                )}
                {pico && (
                  <span className="ml-2 font-semibold" style={{ color: esClaro(tema.color) ? tema.acento : tema.color }}>
                    · Pico {String(pico.hora).padStart(2, "0")}:00 — {fmtMXN(pico.valor)}
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-3">
              {/* Leyenda */}
              <div className="hidden items-center gap-3 text-[11px] font-semibold text-slate-500 sm:flex">
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: esClaro(tema.color) ? tema.acento : tema.color }} />
                  Periodo actual
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-slate-200" />
                  Semana pasada
                </span>
              </div>
              {/* Métrica (unidades no existe en la fuente pedidos) */}
              <div className="inline-flex rounded-lg bg-slate-100 p-1">
                {(esPedidos
                  ? ([["monto", "Monto"], ["pedidos", "Pedidos"]] as const)
                  : ([["monto", "Monto"], ["pedidos", "Pedidos"], ["unidades", "Unidades"]] as const)
                ).map(([id, label]) => {
                  const sel = metrica === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setMetrica(id)}
                      style={sel ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                      className={[
                        "rounded-md px-2.5 py-1 text-xs font-bold transition-all",
                        sel ? "shadow-sm" : "text-slate-500 hover:text-slate-800",
                      ].join(" ")}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {cargando && !data ? (
            <div className="mt-4 h-[260px] animate-pulse rounded-xl bg-slate-50" />
          ) : (
            <div className="relative mt-6">
              {/* líneas guía */}
              <div className="pointer-events-none absolute inset-x-0 bottom-6 top-0">
                {[0, 1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="absolute inset-x-0 border-t border-dashed border-slate-100"
                    style={{ top: `${(i / 4) * 100}%` }}
                  >
                    <span className="absolute -top-2 right-0 bg-white pl-1 text-[10px] font-medium tabular-nums text-slate-300">
                      {metrica === "monto"
                        ? fmtCompacto(maxBarra * (1 - i / 4))
                        : fmtInt(Math.round(maxBarra * (1 - i / 4)))}
                    </span>
                  </div>
                ))}
              </div>

              <div className="flex h-[260px] items-end gap-[3px] pb-6">
                {(data?.horas ?? []).map((h) => {
                  const actual = h[metrica];
                  const previo = h[`prev_${metrica}` as const] as number;
                  const hAct = actual > 0 ? Math.max((actual / maxBarra) * 100, 1.5) : 0;
                  const hPrev = previo > 0 ? Math.max((previo / maxBarra) * 100, 1.5) : 0;
                  const esAhora = incluyeHoy && h.hora === horaActualMX;
                  const futura = esHoy && h.hora > horaActualMX;
                  return (
                    <div key={h.hora} className="group relative flex h-full flex-1 flex-col justify-end">
                      {/* tooltip */}
                      <div className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-44 -translate-x-1/2 rounded-xl border border-slate-200 bg-white p-3 text-left shadow-xl group-hover:block">
                        <div className="text-[11px] font-bold text-slate-900">
                          {String(h.hora).padStart(2, "0")}:00 — {String(h.hora).padStart(2, "0")}:59
                        </div>
                        <div className="mt-1.5 space-y-1 text-[11px]">
                          <div className="flex justify-between">
                            <span className="text-slate-500">Actual</span>
                            <span className="font-bold tabular-nums text-slate-900">
                              {metrica === "monto" ? fmtMXN(h.monto) : fmtInt(actual)}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-slate-500">Sem. pasada</span>
                            <span className="font-semibold tabular-nums text-slate-500">
                              {metrica === "monto" ? fmtMXN(h.prev_monto) : fmtInt(previo)}
                            </span>
                          </div>
                          <div className="flex justify-between border-t border-slate-100 pt-1">
                            <span className="text-slate-500">Pedidos</span>
                            <span className="font-semibold tabular-nums text-slate-700">
                              {fmtInt(h.pedidos)} <span className="text-slate-400">(prev {fmtInt(h.prev_pedidos)})</span>
                            </span>
                          </div>
                          <div className="pt-0.5"><DeltaChip valor={h.delta_monto} /></div>
                        </div>
                      </div>

                      {/* barras (par: semana pasada + actual) */}
                      <div className="flex h-full items-end justify-center gap-[2px] rounded-t group-hover:bg-slate-50">
                        <div
                          className="w-full max-w-[14px] rounded-t bg-slate-200 transition-all"
                          style={{ height: `${hPrev}%` }}
                        />
                        <div
                          className="w-full max-w-[14px] rounded-t transition-all"
                          style={{
                            height: `${hAct}%`,
                            backgroundColor: futura ? "#F1F5F9" : esClaro(tema.color) ? tema.acento : tema.color,
                          }}
                        />
                      </div>

                      {/* eje X */}
                      <div
                        className={[
                          "absolute -bottom-0 left-1/2 -translate-x-1/2 text-[9px] font-semibold tabular-nums",
                          esAhora ? "rounded bg-slate-900 px-1 py-0.5 text-white" : "text-slate-400",
                        ].join(" ")}
                      >
                        {String(h.hora).padStart(2, "0")}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {data && (
            <div className="mt-2 text-right text-[11px] text-slate-400">
              Actualizado {data.actualizado.slice(11, 16)} (CDMX)
              {incluyeHoy ? " · se refresca cada 60 s" : ""}
            </div>
          )}
        </div>

        {/* ── Desglose por cuenta ── */}
        {data && data.cuentas.length > 1 && (
          <div className="mt-6 grid gap-3 md:grid-cols-2">
            {data.cuentas.map((c) => {
              const share = t && t.monto > 0 ? (c.monto / t.monto) * 100 : 0;
              // HOY: delta honesto (misma hora); rangos cerrados: delta normal.
              const delta = esHoy && c.delta_parcial !== undefined ? c.delta_parcial : c.delta_monto;
              const prevRef = esHoy && c.prev_monto_parcial !== undefined ? c.prev_monto_parcial : c.prev_monto;
              return (
                <button
                  key={c.cuenta}
                  type="button"
                  onClick={() => { setCanal("mercado_libre"); setCuenta(c.cuenta); }}
                  title={`Ver solo ${c.label}`}
                  className="rounded-2xl border border-slate-200 bg-white p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-card"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className="flex h-8 w-8 items-center justify-center rounded-lg text-xs font-black"
                        style={{
                          backgroundColor: hexToRgba(THEME_FALLBACK.mercado_libre.color, 0.35),
                          color: THEME_FALLBACK.mercado_libre.texto,
                        }}
                      >
                        {c.label.slice(0, 2).toUpperCase()}
                      </span>
                      <div>
                        <div className="text-sm font-bold text-slate-900">{c.label}</div>
                        <div className="text-[11px] text-slate-400">Mercado Libre · {c.cuenta}</div>
                      </div>
                    </div>
                    <DeltaChip valor={delta} />
                  </div>
                  <div className="mt-3 flex items-end justify-between">
                    <div className="text-xl font-extrabold tabular-nums text-slate-900">{fmtMXN(c.monto)}</div>
                    <div className="text-xs text-slate-500">
                      {fmtInt(c.pedidos)} pedidos · {fmtInt(c.unidades)} unidades
                    </div>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${share}%`,
                        backgroundColor: esClaro(tema.color) ? tema.acento : tema.color,
                      }}
                    />
                  </div>
                  <div className="mt-1 text-[11px] font-semibold text-slate-400">
                    {share.toFixed(1)}% del total · sem. pasada{esHoy ? " a esta hora" : ""} {fmtMXN(prevRef)}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
