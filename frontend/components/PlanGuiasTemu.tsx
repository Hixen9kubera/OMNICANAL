"use client";

/**
 * PlanGuiasTemu — VISTA PREVIA de la compra de guías de Temu. NO COMPRA NADA.
 *
 * Brandon (30-sep-2026): "antes de que confirmes algo me mandas el payload de
 * lo que vas a enviar a Temu y de cómo estás gestionando el envío de los
 * paquetes divididos". Esto es eso: por cada venta que espera su guía, qué
 * caja sale de qué almacén (TEXCO = "IFULL NAVE 2", TEXCO II = "Dirección
 * texco 2"), con cuántas piezas, qué día se entrega al repartidor, con qué
 * peso y caja (y DE DÓNDE salen), la paquetería y su costo, y el payload
 * EXACTO que se mandaría a `bg.logistics.shipment.create`, copiable.
 *
 * NO HAY BOTÓN DE COMPRAR, a propósito. La compra vive en el backend detrás de
 * `TEMU_COMPRA_GUIAS_ENABLED` (apagada), de la APROBACIÓN del grupo (su huella
 * firma payloads + fecha + costo y vence) y de la bitácora durable
 * `ops.temu_guias_compras` (migración 0061).
 *
 * MEDIR UNA CAJA: la caja con varios SKUs (el combinado del mismo almacén) no
 * tiene historial ni medición: sólo se puede comprar si alguien la pesa y la
 * mide y lo captura aquí. La medida viaja en la petición (POST), entra a la
 * huella de la aprobación y NO se guarda en ningún lado.
 *
 * Se calcula A PEDIDO, no al abrir la pestaña: cada cálculo hace lecturas a
 * Temu (detalle, combinado, cotización por caja) con la cuota de producción.
 * Es de admin; a quien le conteste 401/403 se le dice y no se insiste.
 */

import { useCallback, useState } from "react";
import {
  AlertTriangle, CalendarDays, CheckCircle2, ChevronDown, ChevronRight, Copy, Loader2,
  Package, Ruler, RotateCw, ShieldCheck, Truck, X,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";

/* ── Lo que contesta /api/automatizacion/temu/plan-guias ─────────────────── */

interface Renglon {
  parentOrderSn: string;
  orderSn: string;
  sku: string;
  quantity: number;
}

interface Empaque {
  ok: boolean;
  peso_kg: number | null;
  largo_cm: number | null;
  ancho_cm: number | null;
  alto_cm: number | null;
  fuente: string | null;
  confianza: "alta" | "media" | "ninguna";
  muestras: number;
  detalle: string;
  motivo: string | null;
  aviso?: string | null;
}

interface Canal {
  channelId: number | null;
  shipCompanyId: number | null;
  shippingCompanyName: string;
  shipLogisticsType: string;
  estimatedAmount: string;
  estimatedText: string;
  monto: number | null;
}

interface Caja {
  clave: string;
  almacen: string;
  warehouse_id: string;
  warehouse_nombre: string;
  piezas: number;
  renglones: Renglon[];
  empaque: Empaque;
  cotizacion: {
    elegido: Canal | null;
    opciones?: Canal[];
    mas_barata?: Canal | null;
    motivo: string | null;
  } | null;
}

interface Llamada {
  send_type: 0 | 1 | 2;
  explica: string;
  ventas: string[];
  paquetes: Caja[];
  payload: Record<string, unknown> | null;
  huella: string | null;
  motivos: string[];
  comprable: boolean;
}

interface Aprobacion {
  huella: string;
  emitida: number;
  vence: string;
  fecha_envio: string;
  dia_envio: string;
  horas: number;
  llamadas: number;
  cajas: number;
  costo_mxn: number;
}

interface Grupo {
  ventas: string[];
  a_comprar?: string[];
  hechas?: string[];
  combinado: boolean;
  comprable: boolean;
  apartado?: boolean;
  motivos: string[];
  avisos: string[];
  regla: { id: string; texto: string } | null;
  limites: Record<string, string | null>;
  llamadas: Llamada[];
  aprobacion: Aprobacion | null;
}

interface FechaEnvio {
  valida: boolean;
  motivos: string[];
  compra_local: string;
  compra_dia: string;
  fecha_envio: string;
  dia_envio: string;
  horas: number;
  saltados: Array<{ fecha: string; por: string }>;
}

interface Plan {
  ok: boolean;
  error?: string;
  modo?: string;
  compra_habilitada?: boolean;
  generado_at?: string;
  emitida?: number;
  aprobacion_min?: number;
  aprobacion_vence?: string;
  de_cache?: boolean;
  paqueteria_preferida?: string[];
  fecha_envio?: FechaEnvio;
  almacenes?: Array<{ odoo_id: number; almacen: string; warehouse_id: string; nombre_temu: string }>;
  combinado?: { consultado: boolean; error: string | null; grupos: number };
  historial?: { fuente: string | null; candidatos: number; muestras: number };
  bitacora?: { leida: boolean; error: string | null; abiertas: string[]; hechas: string[] };
  demanda_ajena?: Record<string, number>;
  esperando?: number;
  leidas?: number;
  no_leidas?: number;
  cola_truncada?: boolean;
  llamadas_temu?: number;
  resumen?: { grupos: number; ventas: number; comprables: number; bloqueados: number;
              cajas: number; costo_estimado_mxn: number };
  grupos: Grupo[];
}

interface Medida {
  peso_kg: number;
  largo_cm: number;
  ancho_cm: number;
  alto_cm: number;
}

/* ── Piezas chicas ────────────────────────────────────────────────────────── */

const FUENTE_TXT: Record<string, string> = {
  manual: "medida en el panel",
  almacen: "medido por almacén",
  historial_temu: "historial de guías",
  interpolado_historial: "interpolado (propuesta)",
  suma_estimada: "suma estimada",
};

const CONFIANZA: Record<Empaque["confianza"], { bg: string; fg: string }> = {
  alta: { bg: "#ECFDF5", fg: "#047857" },
  media: { bg: "#FFFBEB", fg: "#92400E" },
  ninguna: { bg: "#FFF1F2", fg: "#9F1239" },
};

function fechaCorta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  return `${Number(d)}-${MESES[Number(m) - 1] ?? m}-${a}`;
}

function horaCorta(iso: string | null | undefined): string {
  return iso ? iso.replace("T", " ").slice(0, 16) : "—";
}

function Pastilla({ bien, txt }: { bien: boolean; txt: string }) {
  return (
    <span className="inline-flex items-center gap-[5px] rounded-full px-[9px] py-[3px] text-[11px] font-extrabold"
          style={{ background: bien ? "#ECFDF5" : "#FFFBEB", color: bien ? "#047857" : "#92400E" }}>
      {bien ? <CheckCircle2 className="h-[12px] w-[12px]" /> : <AlertTriangle className="h-[12px] w-[12px]" />}
      {txt}
    </span>
  );
}

function Payload({ ll }: { ll: Llamada }) {
  const [ver, setVer] = useState(false);
  const [copiado, setCopiado] = useState(false);
  if (!ll.payload) return null;
  const texto = JSON.stringify(ll.payload, null, 2);
  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1600);
    } catch {
      setCopiado(false);
    }
  };
  return (
    <div className="mt-2 rounded-[10px] border" style={{ borderColor: "#e6e9f2", background: "#fbfcfe" }}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button type="button" onClick={() => setVer((v) => !v)}
                className="inline-flex items-center gap-1 text-[12px] font-bold text-slate-600">
          {ver ? <ChevronDown className="h-[13px] w-[13px]" /> : <ChevronRight className="h-[13px] w-[13px]" />}
          Payload de <code className="font-mono">bg.logistics.shipment.create</code>
        </button>
        {ll.huella && (
          <span className="font-mono text-[11px] text-slate-400" title={ll.huella}>
            payload {ll.huella.slice(0, 12)}…
          </span>
        )}
        <button type="button" onClick={() => void copiar()}
                className="ml-auto inline-flex items-center gap-1 rounded-[8px] border bg-white px-2 py-[3px] text-[11.5px] font-bold text-slate-600 hover:text-indigo-600"
                style={{ borderColor: "#e6e9f2" }}>
          <Copy className="h-[12px] w-[12px]" />{copiado ? "Copiado" : "Copiar"}
        </button>
      </div>
      {ver && (
        <pre className="max-h-[360px] overflow-auto border-t px-3 py-2 font-mono text-[11.5px] leading-[1.5] text-slate-700"
             style={{ borderColor: "#eef1f6" }}>
          {texto}
        </pre>
      )}
    </div>
  );
}

/** Captura de peso y medidas de UNA caja (la pesaron y midieron en almacén). */
function MedirCaja({ caja, actual, onGuardar, onQuitar }: {
  caja: Caja;
  actual: Medida | undefined;
  onGuardar: (clave: string, m: Medida) => void;
  onQuitar: (clave: string) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [valores, setValores] = useState<Record<keyof Medida, string>>({
    peso_kg: actual ? String(actual.peso_kg) : "",
    largo_cm: actual ? String(actual.largo_cm) : "",
    ancho_cm: actual ? String(actual.ancho_cm) : "",
    alto_cm: actual ? String(actual.alto_cm) : "",
  });
  const campos: Array<[keyof Medida, string, number]> = [
    ["peso_kg", "kg", 70], ["largo_cm", "largo cm", 300], ["ancho_cm", "ancho cm", 300],
    ["alto_cm", "alto cm", 300],
  ];
  const num = (k: keyof Medida) => Number(valores[k].replace(",", "."));
  const valida = campos.every(([k, , tope]) => num(k) > 0 && num(k) <= tope)
    && num("peso_kg") >= 0.01;
  if (!abierto) {
    return (
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setAbierto(true)}
                className="inline-flex items-center gap-1 rounded-[8px] border bg-white px-2 py-[2px] text-[11px] font-bold text-slate-600 hover:text-indigo-600"
                style={{ borderColor: "#e6e9f2" }}>
          <Ruler className="h-[11px] w-[11px]" />{actual ? "Cambiar medida" : "Medir esta caja"}
        </button>
        {actual && (
          <button type="button" onClick={() => onQuitar(caja.clave)}
                  className="inline-flex items-center gap-1 text-[11px] font-bold text-slate-400 hover:text-rose-600">
            <X className="h-[11px] w-[11px]" />quitar medida
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="mt-1 rounded-[8px] border px-2 py-2" style={{ borderColor: "#e6e9f2", background: "#fbfcfe" }}>
      <div className="flex flex-wrap items-center gap-1">
        {campos.map(([k, etiqueta]) => (
          <label key={k} className="inline-flex items-center gap-1 text-[11px] text-slate-500">
            <input value={valores[k]} inputMode="decimal"
                   onChange={(e) => setValores((v) => ({ ...v, [k]: e.target.value }))}
                   className="w-[58px] rounded-[6px] border px-1 py-[2px] font-mono text-[11.5px] text-slate-800"
                   style={{ borderColor: "#e6e9f2" }} />
            {etiqueta}
          </label>
        ))}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <button type="button" disabled={!valida}
                onClick={() => {
                  onGuardar(caja.clave, { peso_kg: num("peso_kg"), largo_cm: num("largo_cm"),
                                          ancho_cm: num("ancho_cm"), alto_cm: num("alto_cm") });
                  setAbierto(false);
                }}
                className="rounded-[8px] px-2 py-[2px] text-[11px] font-bold text-white disabled:opacity-50"
                style={{ background: "#4F46E5" }}>
          Usar y recalcular
        </button>
        <button type="button" onClick={() => setAbierto(false)}
                className="text-[11px] font-bold text-slate-400">Cancelar</button>
        <span className="text-[10.5px] text-slate-400">No se guarda: entra a la huella de la aprobación.</span>
      </div>
    </div>
  );
}

function TablaCajas({ ll, medidas, onGuardar, onQuitar }: {
  ll: Llamada;
  medidas: Record<string, Medida>;
  onGuardar: (clave: string, m: Medida) => void;
  onQuitar: (clave: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-[12.5px]">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-slate-400">
            <th className="py-1 pr-3 font-bold">Sale de</th>
            <th className="py-1 pr-3 font-bold">Lleva</th>
            <th className="py-1 pr-3 font-bold">Peso · caja</th>
            <th className="py-1 font-bold">Paquetería</th>
          </tr>
        </thead>
        <tbody>
          {ll.paquetes.map((c, i) => {
            const e = c.empaque;
            const tono = CONFIANZA[e.confianza] ?? CONFIANZA.ninguna;
            const el = c.cotizacion?.elegido;
            const barata = c.cotizacion?.mas_barata;
            const otraMasBarata = !!(el && barata && barata.channelId !== el.channelId
              && (barata.monto ?? Infinity) < (el.monto ?? Infinity));
            return (
              <tr key={i} className="border-t align-top" style={{ borderColor: "#f1f3f8" }}>
                <td className="py-2 pr-3">
                  <div className="font-bold text-slate-800">{c.almacen}</div>
                  <div className="text-[11.5px] text-slate-500">{c.warehouse_nombre}</div>
                  <div className="font-mono text-[10.5px] text-slate-400">{c.warehouse_id}</div>
                </td>
                <td className="py-2 pr-3">
                  {c.renglones.map((r, j) => (
                    <div key={j} className="font-mono text-[12px] text-slate-700">
                      <span className="font-bold">{r.sku}</span> × {r.quantity}
                      <span className="text-slate-400"> · {r.orderSn}</span>
                    </div>
                  ))}
                  <div className="text-[11px] text-slate-400">{c.piezas} pieza{c.piezas === 1 ? "" : "s"}</div>
                </td>
                <td className="py-2 pr-3">
                  <div className="font-mono text-[12px] text-slate-800">
                    {e.peso_kg != null ? `${e.peso_kg.toFixed(2)} kg` : "— kg"}
                    {e.largo_cm != null && e.ancho_cm != null && e.alto_cm != null
                      ? ` · ${e.largo_cm}×${e.ancho_cm}×${e.alto_cm} cm` : " · sin caja"}
                  </div>
                  <span className="mt-1 inline-block rounded-full px-[7px] py-[1px] text-[10.5px] font-bold"
                        style={{ background: tono.bg, color: tono.fg }} title={e.detalle}>
                    {e.fuente ? FUENTE_TXT[e.fuente] ?? e.fuente : "sin fuente"}
                    {e.muestras ? ` · ${e.muestras} muestra${e.muestras === 1 ? "" : "s"}` : ""}
                    {` · confianza ${e.confianza}`}
                  </span>
                  {e.aviso && (
                    <div className="mt-1 text-[11px] font-semibold" style={{ color: "#92400E" }}>{e.aviso}</div>
                  )}
                  <MedirCaja key={c.clave} caja={c} actual={medidas[c.clave]}
                             onGuardar={onGuardar} onQuitar={onQuitar} />
                </td>
                <td className="py-2">
                  {el ? (
                    <>
                      <div className="font-bold text-slate-800">
                        {el.shippingCompanyName} · {el.shipLogisticsType}
                      </div>
                      <div className="text-[11.5px] text-slate-500">{el.estimatedText || el.estimatedAmount}</div>
                      <div className="font-mono text-[10.5px] text-slate-400">
                        channel {el.channelId} · company {el.shipCompanyId}
                      </div>
                      {otraMasBarata && barata && (
                        <div className="mt-1 text-[11px] text-slate-500"
                             title="La regla de paquetería (TEMU_GUIAS_PAQUETERIA) la deja fuera">
                          La más barata de todas: {barata.shippingCompanyName} · {barata.shipLogisticsType}{" "}
                          {barata.estimatedAmount}
                        </div>
                      )}
                    </>
                  ) : (
                    <span className="text-[12px] text-slate-400">
                      {c.cotizacion?.motivo ?? "no se cotizó (falta peso o caja)"}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function BloqueAprobacion({ ap }: { ap: Aprobacion }) {
  const [copiado, setCopiado] = useState(false);
  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(ap.huella);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1600);
    } catch {
      setCopiado(false);
    }
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[10px] px-3 py-2 text-[11.5px]"
         style={{ background: "#EEF2FF", color: "#3730A3" }}>
      <ShieldCheck className="h-[13px] w-[13px]" />
      <span className="font-bold">Aprobación del grupo</span>
      <span className="font-mono" title={ap.huella}>{ap.huella.slice(0, 16)}…</span>
      <span>· entrega {ap.dia_envio} {fechaCorta(ap.fecha_envio)} ({ap.horas} h)</span>
      <span>· {ap.llamadas} llamada{ap.llamadas === 1 ? "" : "s"}, {ap.cajas} caja{ap.cajas === 1 ? "" : "s"}</span>
      <span>· ~MX${ap.costo_mxn.toFixed(2)}</span>
      <span>· vence {horaCorta(ap.vence)}</span>
      <button type="button" onClick={() => void copiar()}
              className="ml-auto inline-flex items-center gap-1 rounded-[8px] border bg-white px-2 py-[2px] text-[11px] font-bold text-slate-600"
              style={{ borderColor: "#c7d2fe" }}>
        <Copy className="h-[11px] w-[11px]" />{copiado ? "Copiada" : "Copiar huella"}
      </button>
    </div>
  );
}

function TarjetaGrupo({ g, medidas, onGuardar, onQuitar }: {
  g: Grupo;
  medidas: Record<string, Medida>;
  onGuardar: (clave: string, m: Medida) => void;
  onQuitar: (clave: string) => void;
}) {
  const hechas = g.hechas ?? [];
  return (
    <div className="rounded-[14px] border bg-white px-4 py-3"
         style={{ borderColor: g.comprable ? "#A7F3D0" : "#FDE68A",
                  boxShadow: `inset 3px 0 0 ${g.comprable ? "#10B981" : "#F59E0B"}` }}>
      <div className="flex flex-wrap items-center gap-2">
        {g.ventas.map((v) => (
          <code key={v} className="rounded-[6px] bg-slate-50 px-[6px] py-[1px] font-mono text-[12px] font-bold text-slate-700"
                title={hechas.includes(v) ? "Su guía ya la compró el panel" : undefined}
                style={hechas.includes(v) ? { textDecoration: "line-through", color: "#94a3b8" } : undefined}>
            {v}
          </code>
        ))}
        {g.combinado && (
          <span className="rounded-full px-[8px] py-[2px] text-[11px] font-bold"
                style={{ background: "#EEF0FF", color: "#4338CA" }}>
            Temu las quiere juntas · {g.ventas.length} órdenes
          </span>
        )}
        <span className="ml-auto">
          <Pastilla bien={g.comprable}
                    txt={g.comprable ? "Lista para comprar (con aprobación)"
                      : g.regla?.id === "hecha" ? "Ya comprada por el panel" : "No se compra"} />
        </span>
      </div>
      {g.regla && <div className="mt-1 text-[12.5px] text-slate-600">{g.regla.texto}</div>}
      {Object.values(g.limites ?? {}).some(Boolean) && (
        <div className="mt-1 text-[11.5px] text-slate-400">
          Límite de envío de Temu:{" "}
          {Object.entries(g.limites).map(([po, l]) => `${po.slice(-6)} ${horaCorta(l)}`).join(" · ")}
        </div>
      )}
      {g.motivos.length > 0 && (
        <ul className="mt-2 space-y-[2px] rounded-[10px] px-3 py-2 text-[12px]"
            style={{ background: "#FFFBEB", color: "#92400E" }}>
          {g.motivos.map((m, i) => <li key={i}>· {m}</li>)}
        </ul>
      )}
      {g.apartado && (
        <div className="mt-1 text-[11.5px] text-slate-400">
          Sus piezas se apartan del lote aunque no se compre: la venta sigue viva.
        </div>
      )}
      {g.avisos.length > 0 && (
        <div className="mt-1 text-[11.5px] text-slate-500">{g.avisos.join(" · ")}</div>
      )}
      {g.aprobacion && <BloqueAprobacion ap={g.aprobacion} />}
      {g.llamadas.map((ll, i) => (
        <div key={i} className="mt-3 border-t pt-2" style={{ borderColor: "#f1f3f8" }}>
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <Package className="h-[14px] w-[14px] text-slate-400" />
            <span className="text-[12.5px] font-bold text-slate-700">{ll.explica}</span>
            {g.llamadas.length > 1 && (
              <span className="text-[11.5px] text-slate-400">llamada {i + 1} de {g.llamadas.length}</span>
            )}
          </div>
          <TablaCajas ll={ll} medidas={medidas} onGuardar={onGuardar} onQuitar={onQuitar} />
          {ll.motivos.length > 0 && (
            <ul className="mt-1 text-[11.5px]" style={{ color: "#92400E" }}>
              {ll.motivos.map((m, j) => <li key={j}>· {m}</li>)}
            </ul>
          )}
          <Payload ll={ll} />
        </div>
      ))}
    </div>
  );
}

/* ── La sección ───────────────────────────────────────────────────────────── */

export default function PlanGuiasTemu() {
  const [abierto, setAbierto] = useState(false);
  const [datos, setDatos] = useState<Plan | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sinPermiso, setSinPermiso] = useState(false);
  const [limite, setLimite] = useState(20);
  const [soloComprables, setSoloComprables] = useState(false);
  const [medidas, setMedidas] = useState<Record<string, Medida>>({});

  const calcular = useCallback(async (med: Record<string, Medida> = medidas) => {
    setCargando(true);
    setError(null);
    try {
      const url = `${API_BASE}/api/automatizacion/temu/plan-guias`;
      const r = Object.keys(med).length === 0
        ? await fetchSesion(`${url}?limite=${limite}`, { cache: "no-store" })
        : await fetchSesion(url, { method: "POST", cache: "no-store",
                                   body: JSON.stringify({ limite, medidas: med }) },
                            { "Content-Type": "application/json" });
      if (r.status === 401 || r.status === 403) {
        setSinPermiso(true);
        return;
      }
      if (!r.ok) {
        let detalle = `HTTP ${r.status}`;
        try {
          const j = (await r.json()) as { detail?: unknown };
          if (typeof j.detail === "string") detalle = j.detail;
        } catch { /* sin cuerpo legible */ }
        throw new Error(detalle);
      }
      const j: Plan = await r.json();
      setDatos(j);
      if (!j.ok) setError(j.error ?? "el plan no se pudo calcular");
    } catch (err) {
      setError(err instanceof Error ? err.message : "no se pudo calcular el plan");
    } finally {
      setCargando(false);
    }
  }, [limite, medidas]);

  const guardarMedida = useCallback((clave: string, m: Medida) => {
    const nuevas = { ...medidas, [clave]: m };
    setMedidas(nuevas);
    void calcular(nuevas);
  }, [medidas, calcular]);

  const quitarMedida = useCallback((clave: string) => {
    const nuevas = { ...medidas };
    delete nuevas[clave];
    setMedidas(nuevas);
    void calcular(nuevas);
  }, [medidas, calcular]);

  const f = datos?.fecha_envio;
  const grupos = (datos?.grupos ?? []).filter((g) => !soloComprables || g.comprable);
  const ajena = Object.entries(datos?.demanda_ajena ?? {});

  return (
    <section className="mt-[14px] rounded-[18px] border bg-white"
             style={{ borderColor: "#d9dcec", boxShadow: "0 1px 2px rgba(16,24,40,.04)" }}>
      <button type="button" onClick={() => setAbierto((v) => !v)}
              className="flex w-full flex-wrap items-center gap-[10px] px-[20px] py-[14px] text-left">
        {abierto ? <ChevronDown className="h-[15px] w-[15px] text-slate-400" />
                 : <ChevronRight className="h-[15px] w-[15px] text-slate-400" />}
        <Truck className="h-[17px] w-[17px]" style={{ color: "#4F46E5" }} />
        <span className="text-[14.5px] font-extrabold text-slate-900">
          Plan de guías <span className="font-semibold text-slate-400">(vista previa · no compra)</span>
        </span>
        <span className="rounded-full px-[9px] py-[3px] text-[11px] font-extrabold"
              style={{ background: "#F1F5F9", color: "#475569" }}>
          {datos?.compra_habilitada ? "Compra encendida · sólo con aprobación" : "Compra APAGADA"}
        </span>
        <span className="text-[12px] text-slate-400">
          Cómo se comprarían las guías de Temu que esperan: cajas por almacén, fecha y payload exacto
        </span>
      </button>

      {abierto && (
        <div className="border-t px-[20px] pb-[18px] pt-[14px]" style={{ borderColor: "#eef1f6" }}>
          {sinPermiso ? (
            <p className="text-[13px] text-slate-500">Esta vista es de admin.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-[10px]">
                <button type="button" onClick={() => void calcular()} disabled={cargando}
                        className="inline-flex items-center gap-2 rounded-[10px] px-[14px] py-[8px] text-[13px] font-bold text-white disabled:opacity-60"
                        style={{ background: "#4F46E5" }}>
                  {cargando ? <Loader2 className="h-[15px] w-[15px] animate-spin" />
                            : <RotateCw className="h-[15px] w-[15px]" />}
                  {datos ? "Recalcular" : "Calcular plan"}
                </button>
                <select value={limite} onChange={(e) => setLimite(Number(e.target.value))}
                        className="rounded-[10px] border bg-white px-3 py-[8px] text-[13px] font-semibold text-slate-600"
                        style={{ borderColor: "#e6e9f2" }}>
                  <option value={10}>10 ventas</option>
                  <option value={20}>20 ventas</option>
                  <option value={40}>40 ventas</option>
                  <option value={60}>60 ventas</option>
                </select>
                <label className="inline-flex items-center gap-2 text-[12.5px] font-semibold text-slate-600">
                  <input type="checkbox" checked={soloComprables}
                         onChange={(e) => setSoloComprables(e.target.checked)} />
                  Sólo las que se podrían comprar
                </label>
                {Object.keys(medidas).length > 0 && (
                  <span className="rounded-full px-[9px] py-[3px] text-[11px] font-bold"
                        style={{ background: "#EEF2FF", color: "#3730A3" }}>
                    {Object.keys(medidas).length} caja{Object.keys(medidas).length === 1 ? "" : "s"} medida
                    {Object.keys(medidas).length === 1 ? "" : "s"} en el panel
                  </span>
                )}
                <span className="text-[11.5px] text-slate-400">
                  Sólo lee: detalle, combinado y una cotización por caja en Temu; stock en Odoo.
                </span>
              </div>

              {error && (
                <p className="mt-3 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[12.5px]"
                   style={{ background: "#FFF1F2", color: "#9F1239" }}>
                  <AlertTriangle className="h-4 w-4 shrink-0" />{error}
                </p>
              )}

              {datos?.ok && datos.bitacora && !datos.bitacora.leida && (
                <p className="mt-3 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[12.5px]"
                   style={{ background: "#FFFBEB", color: "#92400E" }}>
                  <AlertTriangle className="h-4 w-4 shrink-0" />{datos.bitacora.error}
                </p>
              )}

              {datos?.ok && f && (
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  <div className="rounded-[12px] border px-3 py-2" style={{ borderColor: "#e6e9f2" }}>
                    <div className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-wide text-slate-400">
                      <CalendarDays className="h-[12px] w-[12px]" />Fecha del envío
                    </div>
                    <div className="text-[15px] font-extrabold text-slate-900">
                      {f.dia_envio} {fechaCorta(f.fecha_envio)}
                    </div>
                    <div className="text-[11.5px] text-slate-500">
                      comprando hoy ({f.compra_dia}) · <code className="font-mono">shipLaterLimitTime</code> = {f.horas} h
                      {f.saltados.length > 0 && ` · se brincó ${f.saltados.map((s) => `${fechaCorta(s.fecha)} (${s.por})`).join(", ")}`}
                    </div>
                    {!f.valida && (
                      <div className="mt-1 text-[11.5px] font-semibold" style={{ color: "#9F1239" }}>
                        {f.motivos.join(" · ")}
                      </div>
                    )}
                    {datos.aprobacion_vence && (
                      <div className="mt-1 text-[11px] text-slate-400">
                        Las aprobaciones de este cálculo vencen {horaCorta(datos.aprobacion_vence)}
                        {datos.aprobacion_min ? ` (${datos.aprobacion_min} min)` : ""}.
                      </div>
                    )}
                  </div>
                  <div className="rounded-[12px] border px-3 py-2" style={{ borderColor: "#e6e9f2" }}>
                    <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Almacenes y paquetería</div>
                    {(datos.almacenes ?? []).map((a) => (
                      <div key={a.odoo_id} className="text-[12px] text-slate-700">
                        <span className="font-bold">{a.almacen}</span> → {a.nombre_temu}
                      </div>
                    ))}
                    <div className="text-[11.5px] text-slate-500">
                      Regla: {(datos.paqueteria_preferida ?? []).join(", ").replace("*", "cualquiera")}
                      {" "}(la más barata; empate → la más rápida)
                    </div>
                    {ajena.length > 0 && (
                      <div className="mt-1 text-[11px] text-slate-500"
                           title="Ventas de otros canales que esperan guía y órdenes de Odoo en borrador: se restan de los dos almacenes">
                        Prometido fuera de Temu (restado de los dos almacenes):{" "}
                        {ajena.slice(0, 6).map(([s, n]) => `${s} ×${n}`).join(", ")}
                        {ajena.length > 6 ? ` y ${ajena.length - 6} más` : ""}
                      </div>
                    )}
                  </div>
                  <div className="rounded-[12px] border px-3 py-2" style={{ borderColor: "#e6e9f2" }}>
                    <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Resumen</div>
                    <div className="text-[12.5px] text-slate-700">
                      <b>{datos.resumen?.comprables ?? 0}</b> de {datos.resumen?.grupos ?? 0} grupos se podrían comprar
                      · {datos.resumen?.cajas ?? 0} cajas · ~MX${(datos.resumen?.costo_estimado_mxn ?? 0).toFixed(2)}
                    </div>
                    <div className="text-[11.5px] text-slate-500">
                      {datos.esperando ?? 0} ventas esperando
                      {datos.no_leidas ? ` · ${datos.no_leidas} más nuevas fuera de este cálculo` : ""}
                      {datos.cola_truncada ? " · ⚠ cola truncada" : ""}
                      {" · "}{datos.llamadas_temu ?? 0} lecturas a Temu
                      {datos.de_cache ? " · (de hace unos segundos)" : ""}
                    </div>
                    <div className="text-[11.5px] text-slate-500">
                      Combinado: {datos.combinado?.consultado
                        ? `${datos.combinado.grupos} grupo(s) de Temu`
                        : `sin consultar — ${datos.combinado?.error ?? "?"}`}
                      {" · "}Historial: {datos.historial?.muestras ?? 0} guías medidas
                    </div>
                    {datos.bitacora?.leida && (
                      <div className="text-[11.5px] text-slate-500">
                        Bitácora de compras: {datos.bitacora.hechas.length} comprada(s) por el panel
                        {datos.bitacora.abiertas.length > 0
                          ? ` · ${datos.bitacora.abiertas.length} ABIERTA(S) por conciliar` : ""}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {datos?.ok && (
                <div className="mt-3 space-y-3">
                  {grupos.length === 0 ? (
                    <p className="text-[13px] text-slate-500">
                      {datos.grupos.length ? "Ninguna se podría comprar hoy." : "No hay ventas de Temu esperando su guía."}
                    </p>
                  ) : grupos.map((g) => (
                    <TarjetaGrupo key={g.ventas.join("+")} g={g} medidas={medidas}
                                  onGuardar={guardarMedida} onQuitar={quitarMedida} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
