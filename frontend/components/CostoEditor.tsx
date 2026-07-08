"use client";

import { useEffect, useState } from "react";
import {
  Calculator,
  RefreshCw,
  Save,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  X,
} from "lucide-react";
import { costoDetalle, costoPreview, costoGuardar } from "@/lib/api";
import type { CostoCalculo, CostoOverrides, CostoRow } from "@/lib/types";

const COLOR = "#4F46E5";
const ACENTO = "#4338CA";

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(v);
}

const str = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));
const numOrNull = (v: string) => (v.trim() ? Number(v) || null : null);

interface Props {
  sku: string;
  nombre?: string | null;
  seed?: Partial<CostoRow> | null;
  onGuardado?: () => void;
  onClose?: () => void;
}

export default function CostoEditor({ sku, nombre, seed, onGuardado, onClose }: Props) {
  const [costoProducto, setCostoProducto] = useState(str(seed?.costo_producto));
  const [peso, setPeso] = useState(str(seed?.peso));
  const [largo, setLargo] = useState(str(seed?.largo));
  const [ancho, setAncho] = useState(str(seed?.ancho));
  const [alto, setAlto] = useState(str(seed?.alto));
  const [margen, setMargen] = useState("48");
  const [incluirEnvio, setIncluirEnvio] = useState(true);

  const [calc, setCalc] = useState<Partial<CostoCalculo> | null>(
    seed
      ? {
          costo_producto: seed.costo_producto ?? undefined,
          costo_cbm: seed.costo_cbm ?? undefined,
          costo_unitario: seed.costo_unitario ?? undefined,
          precio_base: seed.precio_base ?? undefined,
          precio_sugerido: seed.precio_sugerido ?? undefined,
          volumen_m3: seed.volumen_m3 ?? undefined,
        }
      : null,
  );
  const [fresco, setFresco] = useState(false);
  const [regenerando, setRegenerando] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; texto: string } | null>(null);

  // Semilla desde el backend (rellena lo que no vino en `seed`).
  useEffect(() => {
    const ctrl = new AbortController();
    costoDetalle(sku, ctrl.signal)
      .then((d) => {
        const cv = (d.validados ?? {}) as Record<string, unknown>;
        const cf = (d.finales ?? {}) as Record<string, unknown>;
        const s = (v: unknown) => (v == null || v === "" ? "" : String(v));
        const num = (v: unknown) => (v == null || v === "" ? undefined : Number(v));
        setCostoProducto((p) => p || s(cv.costo_producto ?? cf.costo_producto));
        setPeso((p) => p || s(cv.peso ?? cf.peso));
        setLargo((p) => p || s(cv.largo ?? cf.largo));
        setAncho((p) => p || s(cv.ancho ?? cf.ancho));
        setAlto((p) => p || s(cv.alto ?? cf.alto));
        if (d.constantes?.margen != null) setMargen(String(Math.round(d.constantes.margen * 100)));
        setCalc((prev) =>
          prev ?? {
            costo_producto: num(cv.costo_producto ?? cf.costo_producto),
            costo_cbm: num(cv.costo_cbm ?? cf.costo_cbm),
            costo_unitario: num(cf.costo_unitario ?? cv.costo_total),
            costo_comision: num(cf.costo_comision),
            costo_fee_envio: num(cf.costo_fee_envio),
            precio_base: num(cf.precio_base),
            precio_sugerido: num(cf.precio_sugerido),
            pct_comision: num(cf.pct_comision),
          },
        );
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [sku]);

  const overrides = (): CostoOverrides => ({
    costo_producto: numOrNull(costoProducto),
    largo: numOrNull(largo),
    ancho: numOrNull(ancho),
    alto: numOrNull(alto),
    peso: numOrNull(peso),
    margen: (Number(margen) || 0) / 100,
    incluir_envio: incluirEnvio,
    auto_cbm: true,
  });

  async function regenerar() {
    setRegenerando(true);
    setMsg(null);
    try {
      const r = await costoPreview(sku, overrides());
      setCalc(r.calculo);
      setFresco(true);
    } catch {
      setMsg({ ok: false, texto: "No se pudo calcular (falta costo base o categoría ML)." });
    } finally {
      setRegenerando(false);
    }
  }

  async function guardar() {
    setGuardando(true);
    setMsg(null);
    try {
      const r = await costoGuardar(sku, { ...overrides(), sincronizar_woo: true });
      const f = r.finales as Record<string, unknown>;
      const num = (v: unknown) => (v == null || v === "" ? undefined : Number(v));
      setCalc((prev) => ({
        ...(prev ?? {}),
        costo_producto: num(f.costo_producto),
        costo_cbm: num(f.costo_cbm),
        costo_unitario: num(f.costo_unitario),
        costo_comision: num(f.costo_comision),
        costo_fee_envio: num(f.costo_fee_envio),
        precio_base: num(f.precio_base),
        precio_sugerido: num(f.precio_sugerido),
      }));
      setFresco(true);
      setMsg({
        ok: true,
        texto: r.sincronizado_woo
          ? "Guardado y sincronizado con WooCommerce."
          : "Guardado en la base de datos.",
      });
      onGuardado?.();
    } catch {
      setMsg({ ok: false, texto: "No se pudo guardar el costo." });
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="rounded-2xl border border-indigo-100 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <Calculator size={15} style={{ color: ACENTO }} />
          <span className="text-[11px] font-bold uppercase tracking-[0.15em]" style={{ color: ACENTO }}>Costos</span>
          <span className="truncate font-mono text-xs text-slate-400">{sku}</span>
          {nombre && <span className="hidden truncate text-xs text-slate-500 sm:inline">· {nombre}</span>}
        </div>
        <div className="flex items-center gap-3">
          {calc?.pct_comision != null && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
              comisión ML {Math.round((calc.pct_comision ?? 0) * 100)}%
            </span>
          )}
          {onClose && (
            <button onClick={onClose} className="rounded-lg p-1 text-slate-400 hover:bg-slate-100" title="Cerrar">
              <X size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Entradas */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Campo label="Costo producto" prefijo="$" value={costoProducto} onChange={setCostoProducto} />
        <Campo label="Peso (kg)" value={peso} onChange={setPeso} />
        <Campo label="Margen (%)" value={margen} onChange={setMargen} />
        <div>
          <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">Envío</label>
          <label className="flex h-[42px] cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600">
            <input type="checkbox" checked={incluirEnvio} onChange={(e) => setIncluirEnvio(e.target.checked)} className="h-4 w-4" style={{ accentColor: ACENTO }} />
            Sumar al precio
          </label>
        </div>
      </div>

      {/* Dimensiones de la pieza */}
      <div className="mt-3">
        <div className="mb-1.5 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">
          Dimensiones de la pieza — Largo × Ancho × Alto (cm)
          {calc?.volumen_m3 != null && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-amber-700">{calc.volumen_m3} m³</span>}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Campo value={largo} onChange={setLargo} placeholder="largo" />
          <Campo value={ancho} onChange={setAncho} placeholder="ancho" />
          <Campo value={alto} onChange={setAlto} placeholder="alto" />
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          Flete por pieza (CBM) = volumen × ${(calc?.tarifa_cbm_m3 ?? 7500).toLocaleString("es-MX")}/m³ (contenedor estándar).
        </p>
      </div>

      {/* Regenerar */}
      <button
        onClick={regenerar}
        disabled={regenerando}
        className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border-2 bg-white px-4 py-2 text-sm font-bold transition-all hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
        style={{ borderColor: COLOR, color: ACENTO }}
      >
        {regenerando ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
        {regenerando ? "Calculando…" : "Regenerar costo"}
      </button>

      {/* Resultados */}
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Resultado label="Flete CBM" value={precioMXN(calc?.costo_cbm)} />
        <Resultado label="Costo" value={precioMXN(calc?.costo_unitario)} destacado />
        <Resultado label="Comisión ML" value={precioMXN(calc?.costo_comision)} />
        <Resultado label="Envío" value={precioMXN(calc?.costo_fee_envio)} />
        <Resultado label="Precio regular" value={precioMXN(calc?.precio_base)} />
        <Resultado label="Precio oferta" value={precioMXN(calc?.precio_sugerido)} destacado />
      </div>
      {fresco && calc?.ganancia_neta != null && (
        <div className="mt-2 text-[11px] text-slate-500">
          Ganancia neta <strong>{precioMXN(calc.ganancia_neta)}</strong>
          {calc.roi != null && <> · ROI <strong>{Math.round((calc.roi ?? 0) * 100)}%</strong></>}
        </div>
      )}

      {/* Guardar */}
      <button
        onClick={guardar}
        disabled={guardando}
        className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold text-white shadow-sm transition-all hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
        style={{ background: `linear-gradient(120deg, ${COLOR}, ${ACENTO})` }}
      >
        {guardando ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
        {guardando ? "Guardando…" : "Guardar costo y precios"}
      </button>
      {msg && (
        <div className={["mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-sm", msg.ok ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"].join(" ")}>
          {msg.ok ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" /> : <AlertTriangle size={15} className="mt-0.5 shrink-0" />}
          {msg.texto}
        </div>
      )}
    </div>
  );
}

function Campo({ label, value, onChange, prefijo, placeholder }: {
  label?: string; value: string; onChange: (v: string) => void; prefijo?: string; placeholder?: string;
}) {
  return (
    <div>
      {label && <label className="mb-1.5 block text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">{label}</label>}
      <div className="relative">
        {prefijo && <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-slate-400">{prefijo}</span>}
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
          className={["w-full rounded-lg border border-slate-200 py-2.5 text-sm text-slate-800 outline-none focus:ring-2", prefijo ? "pl-7 pr-3" : "px-3"].join(" ")}
          style={{ outlineColor: ACENTO }} />
      </div>
    </div>
  );
}

function Resultado({ label, value, destacado }: { label: string; value: string; destacado?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400">{label}</div>
      <div className={["mt-0.5 font-bold", destacado ? "text-base" : "text-sm text-slate-700"].join(" ")}
        style={destacado ? { color: ACENTO } : undefined}>
        {value}
      </div>
    </div>
  );
}
