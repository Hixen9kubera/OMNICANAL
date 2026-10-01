"use client";

/**
 * CompraAutoTemu — la COMPRA AUTOMÁTICA de guías de Temu (30-sep-2026), en
 * Automatización → Temu.
 *
 * Brandon: "automatízala desde que se genera la orden hasta confirmarla con su
 * guía y sus datos correctos". El backend (services/temu_guias_auto.py) compra
 * sola la guía de cada venta nueva que el planeador da por comprable, dispara
 * el refresco de guías de esa venta para que la orden de Odoo nazca confirmada
 * con su número y su PDF, y lo relee en Odoo. Esta tarjeta dice:
 *
 *   · si está encendida (las dos banderas) y desde qué fecha de venta compra;
 *   · si está en ENSAYO (no compra: dice qué compraría) o en MODO SIMPLE (sólo
 *     compra sola una caja sendType 0), y qué paquetería/tipo elige;
 *   · compras y gasto de HOY contra sus topes;
 *   · si está DETENIDA y por qué — sólo el dinero en duda la detiene ("no sé
 *     si compró", grupo a medias, la orden de Odoo no cuadra con la guía) hasta
 *     que alguien concilie —, con el botón "Conciliar" (admin) por cada compra
 *     abierta y por cada etiqueta que sigue EN APLICACIÓN en Temu (ésa sólo
 *     libera si alguien la ACEPTA tras mirarla en el seller center);
 *   · las que Temu RECHAZÓ (1-oct: un rechazo ya NO detiene el job): quedan
 *     "compra manual · Temu la rechazó: <código y texto>" con el botón
 *     «Reintentar» — solas se reintentan una vez, cuando cambia su plan;
 *   · cuántas ventas nuevas requieren compra manual, y las URGENTES.
 *
 * Por venta, en cada renglón de la lista, `ChipCompraAuto` dice "Guía comprada
 * automáticamente (paquetería, costo, fecha)" o "Requiere compra manual
 * (motivo)". Sin datos del comprador: todo viene de `/api/automatizacion/estado`.
 */

import { useState } from "react";
import {
  AlertTriangle, CheckCircle2, Clock, FlaskConical, Loader2, PackageCheck, Play, ShieldAlert,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";

/* ── Lo que contesta el backend (`temu_compra_auto` de /estado) ─────────── */

export interface VentaCompraAuto {
  /** comprada_auto | manual | compra_<estado de la bitácora> */
  estado: string;
  clase?: string;
  clase_txt?: string;
  urgente?: boolean;
  motivo?: string;
  /** ¿La campana la está avisando? (una transitoria que no se resuelve, una
   *  "ya tiene guía" cuya orden no nace…). El panel cuenta lo mismo. */
  en_campana?: boolean;
  visto?: string | null;
  desde?: string | null;
  estado_bitacora?: string;
  paqueteria?: string[];
  costo_mxn?: number | null;
  fecha_envio?: string | null;
  horas?: number | null;
  package_sn?: string[];
  comprada_at?: string | null;
  verificacion?: "ok" | "pendiente" | "no" | "detenida";
  /** La etiqueta sigue "en aplicación" en Temu (fila 'pendiente'). */
  en_aplicacion?: boolean;
  /** …y una persona la aceptó para liberar el job (se sigue conciliando sola). */
  aceptada?: boolean;
  detalle?: string | null;
  /** Se compró después del límite de envío de Temu (30-sep: sin interruptor). */
  tarde?: boolean;
  /** De dónde salió el peso y la caja: "omnicanal · packing list (media)"… */
  medidas?: string | null;
}

interface CompraEnsayo {
  ventas: string[];
  costo_mxn: number | null;
  paqueteria: string[];
  fecha_envio?: string | null;
  horas?: number | null;
  ajustada?: boolean;
  tarde?: boolean;
  medidas?: string;
  llamadas?: Array<{ send_type: number; cajas: number }>;
}

interface Topes {
  vuelta: number;
  dia: number;
  gasto_dia: number;
  caja: number;
  venta: number;
}

export interface CompraAutoEstado {
  banderas?: {
    compra_enabled: boolean;
    auto: boolean;
    encendida: boolean;
    /** ENSAYO: planea y cotiza pero NO compra (nace encendido). */
    ensayo?: boolean;
    /** MODO SIMPLE: sólo compra sola una caja sendType 0 (nace encendido). */
    solo_simple?: boolean;
    /** TEMU_GUIAS_PAQUETERIA, en orden de prioridad ("J&T,*": J&T primero y,
     *  si no se ofrece, la más barata de las demás). */
    paqueteria?: string;
    /** false = el primer renglón de TEMU_GUIAS_PAQUETERIA NO es J&T (el orden es
     *  prioridad: las guías saldrían por esa otra paquetería). */
    paqueteria_jt_primero?: boolean;
    /** TEMU_COMPRA_GUIAS_AUTO_EXCEDE_JT: ¿se compra sola la caja que excede J&T? */
    excede_jt_compra?: boolean;
    /** TEMU_GUIAS_PESO_GUIA_X1_POR_PIEZAS: ¿la guía de 1 pieza × piezas cuenta en el peso? */
    peso_guia_x1_por_piezas?: boolean;
    jt_ship_company_id?: number;
    /** Límites de J&T (30 kg / 100 cm / 160 cm, volumétrico ÷ 5000) y el sobre de
     *  60×60×40 de sus preguntas frecuentes (sólo aviso). */
    limites_jt?: { peso: number; lado: number; suma: number; divisor: number; aviso: number;
                   aviso_corto?: number };
    peso_min_kg?: number;
    sabado_alterno?: boolean;
    desde: string;
    minutos: number;
    revisar_min?: number;
    verificar_max_min?: number;
    topes: Topes;
    /** Segundos de cada vuelta para EMPEZAR compras (lo demás, la siguiente). */
    presupuesto_s?: number;
    /** Quién opina en el peso y la caja (TEMU_GUIAS_FUENTES_MEDIDA): gana el
     *  menor creíble, el orden ya no manda. */
    fuentes_medida?: string[];
    fuentes_medida_error?: string | null;
  };
  encendida?: boolean;
  detenido?: { desde?: string | null; motivo?: string | null; por?: string | null;
               /** Las ventas de la detención (para "Conciliar" cada una). */
               ventas?: string[] } | null;
  hoy?: { compras: number; gasto_mxn: number; ventas?: string[] };
  abiertas?: Array<{
    parent_order_sn: string;
    estado: string;
    automatica: boolean;
    codigo?: string | null;
    motivo?: string | null;
    desde?: string | null;
  }>;
  /** Etiquetas AUTOMÁTICAS que siguen "en aplicación" en Temu: sin aceptar,
   *  impiden liberar el job. */
  pendientes?: Array<{
    parent_order_sn: string;
    estado: string;
    aceptada: boolean;
    motivo?: string | null;
    desde?: string | null;
  }>;
  /** Ventas cuya compra automática Temu RECHAZÓ y siguen esperando guía: no
   *  detienen el job; se pueden «Reintentar». */
  rechazadas?: Array<{
    parent_order_sn: string;
    codigo?: string | null;
    motivo?: string | null;
    desde?: string | null;
    /** disponible (se reintenta una vez si cambia su plan) | pedido | agotado */
    reintento?: string;
  }>;
  error?: string | null;
  ultima_vuelta?: {
    ts?: string | null;
    estado?: string | null;
    motivo?: string | null;
    candidatas?: number;
    evaluadas?: number;
    manuales?: number;
    compradas?: Array<{ ventas: string[]; costo_mxn: number | null; paqueteria: string[];
                        fecha_envio?: string | null; horas?: number | null;
                        tarde?: boolean; medidas?: string;
                        /** Por qué NO fue por J&T (si no lo fue) y avisos de medidas. */
                        no_jt?: string[]; avisos?: Array<{ de: string; aviso: string }>;
                        reintento?: string | null }>;
    /** Las que Temu rechazó en esta vuelta (el job siguió con las demás). */
    rechazadas?: Array<{ ventas: string[]; codigo: string; motivo: string }>;
    /** Comprables que no cupieron en el tiempo de la vuelta: van en la siguiente. */
    esperan_turno?: number;
    presupuesto_agotado?: boolean;
    /** ENSAYO: lo que la vuelta HABRÍA comprado. */
    ensayo?: CompraEnsayo[];
  };
  ventas?: Record<string, VentaCompraAuto>;
}

/* ── Piezas chicas ────────────────────────────────────────────────────────── */

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function fechaCorta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [a, m, d] = iso.slice(0, 10).split("-");
  return a && m && d ? `${Number(d)}-${MESES[Number(m) - 1] ?? m}` : iso;
}

/** El corte: "28-sep", o "28-sep 12:00" si TEMU_COMPRA_GUIAS_DESDE lleva hora (una venta
 *  del 28 a las 11:33 queda fuera de un corte del 28 a las 12:00). */
function corteTxt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const hora = /[T ](\d{2}):(\d{2})/.exec(iso);
  const dia = fechaCorta(iso);
  return hora && `${hora[1]}:${hora[2]}` !== "00:00" ? `${dia} ${hora[1]}:${hora[2]}` : dia;
}

function hace(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const min = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.floor(min / 60);
  return h < 24 ? `hace ${h} h` : `hace ${Math.floor(h / 24)} d`;
}

const dinero = (n: number | null | undefined) =>
  n == null ? "—" : `MX$${n.toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Lo que dice cada estado de la vuelta, en palabras. */
const ESTADO_VUELTA: Record<string, string> = {
  apagado: "apagada",
  compro: "compró",
  sin_compras: "revisó y no había qué comprar",
  sin_candidatas: "sin ventas nuevas por comprar",
  detenido: "DETENIDA",
  verificando: "verificando una compra",
  tope_dia: "tope del día alcanzado",
  tope_gasto: "tope de gasto alcanzado",
  tope_vuelta: "tope por vuelta en 0",
  cadena_incompleta: "no compra: la orden no nacería",
  sin_bitacora: "sin bitácora de compras",
  config_invalida: "configuración inválida",
  plan_incompleto: "plan a medias (se reintenta)",
  sin_plan: "no salió el plan",
  otro_proceso: "otra instancia en su vuelta",
  otra_compra_en_curso: "otra compra en curso",
  en_curso: "vuelta en curso",
  ensayo: "ENSAYO: dijo qué compraría (no compró)",
  sin_cola: "no se pudo leer la cola",
  cola_larga: "candidatas fuera del alcance del plan",
  sin_turno: "no se pudo tomar el turno",
  turno_perdido: "el turno venció a media vuelta",
  rechazos: "Temu rechazó compras (quedan manuales; siguió con las demás)",
  rechazos_seguidos: "rechazos seguidos de Temu: la vuelta dejó de comprar (no está detenida; la siguiente prueba UNA venta)",
  pasarela: "la pasarela de Temu rechazó la llamada (se reintenta sola)",
  error: "error",
};

/**
 * El estado de la compra de guía de UNA venta, para su renglón de la lista.
 * Verde: la compró el job; ámbar: requiere compra manual (no es un error);
 * rojo: urgente, o una compra que hay que conciliar.
 */
export function ChipCompraAuto({ v, compacto = false }: { v: VentaCompraAuto; compacto?: boolean }) {
  let fondo = "#FFFBEB";
  let tinta = "#92400E";
  let borde = "#FCD34D";
  let texto = "";
  let titulo = v.motivo ?? v.detalle ?? "";
  let icono = <AlertTriangle className="h-3 w-3 shrink-0" />;
  if (v.estado === "comprada_auto") {
    const verif = v.en_aplicacion
      ? (v.aceptada ? "etiqueta en aplicación en Temu (aceptada)" : "etiqueta en aplicación en Temu")
      : v.verificacion === "ok" ? "orden verificada"
      : v.verificacion === "no" ? "orden NO verificada"
      : v.verificacion === "detenida" ? "DETUVO la compra automática" : "orden por verificar";
    const partes = [
      (v.paqueteria ?? []).join(" + ") || null,
      v.costo_mxn != null ? dinero(v.costo_mxn) : null,
      v.fecha_envio ? `entrega ${fechaCorta(v.fecha_envio)}${v.horas ? ` (${v.horas} h)` : ""}` : null,
      v.tarde ? "comprada TARDE (límite de Temu ya vencido)" : null,
    ].filter(Boolean);
    texto = `Guía comprada automáticamente · ${partes.join(" · ")}${compacto ? "" : ` · ${verif}`}`;
    titulo = [v.detalle ?? verif, v.medidas ? `medidas: ${v.medidas}` : null].filter(Boolean).join(" · ");
    if (v.verificacion === "no" || v.verificacion === "detenida") {
      fondo = "#FFF1F2"; tinta = "#9F1239"; borde = "#FDA4AF";
    } else if (v.verificacion === "ok") {
      fondo = "#ECFDF5"; tinta = "#047857"; borde = "#6EE7B7";
      icono = <CheckCircle2 className="h-3 w-3 shrink-0" />;
    } else {
      fondo = "#F0F9FF"; tinta = "#075985"; borde = "#7DD3FC";
      icono = <Clock className="h-3 w-3 shrink-0" />;
    }
  } else if (v.estado.startsWith("compra_")) {
    fondo = "#FFF1F2"; tinta = "#9F1239"; borde = "#FDA4AF";
    icono = <ShieldAlert className="h-3 w-3 shrink-0" />;
    texto = `Compra automática '${v.estado_bitacora ?? v.estado.slice(7)}': hay que conciliarla`;
  } else if (v.clase === "ya_comprada" && v.en_campana) {
    // Lleva horas "con guía" y su orden no nace: la etiqueta pudo fallar o
    // anularse en Temu. Nadie la compra sola: que alguien la mire.
    fondo = "#FFF1F2"; tinta = "#9F1239"; borde = "#FDA4AF";
    icono = <AlertTriangle className="h-3 w-3 shrink-0" />;
    texto = `Tiene etiqueta en Temu pero su orden no nace: revísala${compacto || !v.motivo ? "" : ` · ${v.motivo}`}`;
  } else if (v.clase === "ya_comprada") {
    fondo = "#F8FAFC"; tinta = "#475569"; borde = "#CBD5E1";
    icono = <CheckCircle2 className="h-3 w-3 shrink-0" />;
    texto = "Ya tiene guía (comprada fuera del job): la orden nace con el trabajo de guías";
  } else if (v.clase === "ensayo") {
    // ENSAYO: no es "compra manual" — es lo que el job compraría si comprara.
    fondo = "#F0F9FF"; tinta = "#075985"; borde = "#7DD3FC";
    icono = <FlaskConical className="h-3 w-3 shrink-0" />;
    const m = (v.motivo ?? "").replace(/^ENSAYO: la compraría —\s*/i, "");
    texto = `Ensayo: la compraría${compacto || !m ? "" : ` · ${m}`}`;
  } else if (v.clase === "rechazo" || v.clase === "rechazo_final") {
    // Temu RECHAZÓ su compra (seguro que no compró): no detiene el job. El
    // motivo ya dice "compra manual · Temu la rechazó: <código y texto>".
    fondo = "#FFF1F2"; tinta = "#9F1239"; borde = "#FDA4AF";
    icono = <ShieldAlert className="h-3 w-3 shrink-0" />;
    const m = (v.motivo ?? "").replace(/^compra manual · /i, "");
    texto = `Compra manual · ${compacto || !m ? "Temu la rechazó" : m}`;
  } else if (v.urgente) {
    fondo = "#FFF1F2"; tinta = "#9F1239"; borde = "#FDA4AF";
    const m = (v.motivo ?? "").replace(/^compra manual URGENTE:\s*/i, "");
    texto = `Compra manual URGENTE · ${compacto || !m ? "límite de envío de Temu" : m}`;
  } else {
    if (v.clase === "anterior_corte") {
      fondo = "#F8FAFC"; tinta = "#64748B"; borde = "#CBD5E1";
    }
    texto = `Requiere compra manual · ${v.clase_txt ?? v.clase ?? "motivo"}`
      + (!compacto && v.motivo ? `: ${v.motivo}` : "");
  }
  return (
    <span className="inline-flex max-w-full items-center gap-[5px] rounded-full px-[8px] py-[2px] text-[10.5px] font-extrabold"
          title={titulo || undefined}
          style={{ background: fondo, color: tinta, boxShadow: `inset 0 0 0 1px ${borde}` }}>
      {icono}
      <span className="truncate">{texto}</span>
    </span>
  );
}

/* ── La tarjeta ──────────────────────────────────────────────────────────── */

type Aviso = { tono: "ok" | "mal"; txt: string };

export default function CompraAutoTemu({ datos, puedeMover, onCambio }: {
  datos: CompraAutoEstado;
  /** Admin: puede conciliar y correr una vuelta (el RBAC manda de todos modos). */
  puedeMover: boolean;
  onCambio: () => void;
}) {
  const [ocupado, setOcupado] = useState<string | null>(null);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [sinRastro, setSinRastro] = useState<Record<string, boolean>>({});

  const b = datos.banderas;
  const t = b?.topes;
  const encendida = Boolean(datos.encendida ?? b?.encendida);
  const detenido = datos.detenido;
  const abiertas = datos.abiertas ?? [];
  const pendientes = datos.pendientes ?? [];
  const sinAceptar = pendientes.filter((p) => !p.aceptada);
  const hoy = datos.hoy ?? { compras: 0, gasto_mxn: 0 };
  const u = datos.ultima_vuelta ?? {};
  const ensayo = Boolean(b?.ensayo);
  // Las anteriores al corte SÍ cuentan (30-sep: ninguna venta se queda sin guía
  // en silencio); lo transitorio se reintenta solo la vuelta siguiente. Cuenta
  // lo MISMO que la campana (`en_campana`): una transitoria que no se resuelve
  // y una "ya tiene guía" cuya orden no nace también.
  const manuales = Object.entries(datos.ventas ?? {}).filter(
    ([, v]) => v.estado === "manual" && (v.en_campana ?? (v.clase !== "ya_comprada"
      && v.clase !== "transitorio" && v.clase !== "ensayo")));
  const urgentes = manuales.filter(([, v]) => v.urgente).map(([po]) => po);
  const porClase: Record<string, number> = {};
  for (const [, v] of manuales) {
    const k = v.clase_txt ?? v.clase ?? "otro";
    porClase[k] = (porClase[k] ?? 0) + 1;
  }

  const llamar = async (clave: string, url: string, alTerminar?: (j: Record<string, unknown>) => void) => {
    setOcupado(clave);
    setAviso(null);
    try {
      const r = await fetchSesion(url, { method: "POST", cache: "no-store" });
      if (r.status === 401 || r.status === 403) {
        setAviso({ tono: "mal", txt: "Esto es de admin." });
        return;
      }
      let j: Record<string, unknown> = {};
      try { j = (await r.json()) as Record<string, unknown>; } catch { /* sin cuerpo */ }
      if (!r.ok) {
        setAviso({ tono: "mal", txt: typeof j.detail === "string" ? j.detail : `HTTP ${r.status}` });
        return;
      }
      alTerminar?.(j);
      onCambio();
    } catch (err) {
      setAviso({ tono: "mal", txt: err instanceof Error ? err.message : "no se pudo" });
    } finally {
      setOcupado(null);
    }
  };

  const conciliar = (po: string | null, liberar = false, reintentar = false) => {
    const qs = new URLSearchParams();
    if (po) qs.set("po", po);
    if (liberar) qs.set("liberar", "true");
    if (reintentar) qs.set("reintentar", "true");
    void llamar(`${reintentar ? "reintentar" : "conciliar"}:${po ?? "-"}:${liberar}`,
      `${API_BASE}/api/automatizacion/temu/compra-auto/conciliar?${qs.toString()}`, (j) => {
        const c = (j.conciliacion ?? {}) as { accion?: string; estado?: string; motivo?: string };
        if (po && c.accion === "sin_rastro") setSinRastro((s) => ({ ...s, [po]: true }));
        const partes = [
          c.accion ? `${po}: ${c.accion}${c.estado ? ` (${c.estado})` : ""}` : null,
          reintentar && typeof j.reintento_motivo === "string" ? j.reintento_motivo : null,
          j.liberado ? "compra automática LIBERADA"
            : (typeof j.motivo === "string" && j.motivo !== j.reintento_motivo ? j.motivo : null),
        ].filter(Boolean);
        const bien = reintentar ? j.reintento === true : Boolean(j.ok);
        setAviso({ tono: bien ? "ok" : "mal", txt: partes.join(" · ") || "listo" });
      });
  };

  const correr = () => void llamar("vuelta", `${API_BASE}/api/automatizacion/temu/compra-auto/vuelta`,
    (j) => setAviso({ tono: j.ok === false ? "mal" : "ok",
                      txt: typeof j.motivo === "string" ? j.motivo : "vuelta lanzada" }));

  const pastilla = detenido
    ? { txt: "DETENIDA", bg: "#FFF1F2", fg: "#9F1239" }
    : encendida && ensayo
      ? { txt: `ENSAYO · no compra · cada ${b?.minutos ?? "?"} min`, bg: "#F0F9FF", fg: "#075985" }
      : encendida
        ? { txt: `Encendida · cada ${b?.minutos ?? "?"} min`, bg: "#ECFDF5", fg: "#047857" }
        : { txt: "APAGADA", bg: "#F1F5F9", fg: "#475569" };
  // J&T SIEMPRE primero (1-oct); la más barata sólo si J&T no se ofrece. Entre
  // los servicios de J&T gana el más barato —hoy el DROP OFF: alguien lleva la
  // caja a un punto de entrega—; se dice aquí porque nadie revisa el tipo antes
  // de pagar. "J&T:Pickup,J&T,*" fija la recolección.
  const paq = (b?.paqueteria ?? "J&T,*").trim();
  const paqTxt = (b?.paqueteria_jt_primero === false ? "⚠ J&T NO va primero — " : "")
    + (paq === "J&T,*" || paq === "*"
      ? "J&T primero (su servicio más barato: recolección o DROP OFF); si J&T no se ofrece, la más barata"
      : `${paq} (en orden de prioridad)`);
  const rechazadas = datos.rechazadas ?? [];
  const lj = b?.limites_jt;

  return (
    <section className="mt-[14px] rounded-[18px] border bg-white px-[20px] py-[14px]"
             style={{ borderColor: detenido ? "#FDA4AF" : "#d9dcec",
                      boxShadow: "0 1px 2px rgba(16,24,40,.04)" }}>
      <div className="flex flex-wrap items-center gap-[10px]">
        <PackageCheck className="h-[17px] w-[17px]" style={{ color: "#FB7701" }} />
        <span className="text-[14.5px] font-extrabold text-slate-900">Compra automática de guías</span>
        <span className="rounded-full px-[9px] py-[3px] text-[11px] font-extrabold"
              style={{ background: pastilla.bg, color: pastilla.fg }}>{pastilla.txt}</span>
        <span className="text-[12px] text-slate-400">
          {encendida
            ? `Ventas desde el ${corteTxt(b?.desde)} · de la compra a la orden confirmada con su guía y PDF`
            : !b?.compra_enabled
              ? "TEMU_COMPRA_GUIAS_ENABLED apagada"
              : "TEMU_COMPRA_GUIAS_AUTO apagada"}
        </span>
        {puedeMover && encendida && !detenido && (
          <button type="button" onClick={correr} disabled={ocupado !== null}
                  className="ml-auto inline-flex items-center gap-1 rounded-[9px] border bg-white px-2 py-[4px] text-[11.5px] font-bold text-slate-600 hover:text-indigo-600 disabled:opacity-50"
                  style={{ borderColor: "#e6e9f2" }}
                  title="Corre una vuelta ya, con sus banderas y topes (no espera los minutos del job)">
            {ocupado === "vuelta" ? <Loader2 className="h-[12px] w-[12px] animate-spin" />
                                  : <Play className="h-[12px] w-[12px]" />}
            {ensayo ? "Ensayar una vuelta ahora" : "Correr una vuelta ahora"}
          </button>
        )}
      </div>

      {encendida && (
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-slate-500">
          {ensayo && (
            <span className="font-bold" style={{ color: "#075985" }}>
              ENSAYO: planea, cotiza y aplica topes, pero NO compra (TEMU_COMPRA_GUIAS_AUTO_ENSAYO)
            </span>
          )}
          {b?.solo_simple && (
            <span className="font-bold" style={{ color: "#92400E" }}>
              Modo simple: sólo compra sola UNA caja sendType 0 (partidos y combinados, a mano)
            </span>
          )}
          <span style={b?.paqueteria_jt_primero === false ? { color: "#B91C1C", fontWeight: 700 } : undefined}>
            Paquetería: {paqTxt}
          </span>
          <span>Vencidas: se compran TARDE (el plazo más corto)</span>
          <span>Rechazos de Temu: no detienen (esa venta queda manual y se sigue)</span>
          {(b?.fuentes_medida ?? []).length > 0 && (
            <span title={b?.fuentes_medida_error ?? "TEMU_GUIAS_FUENTES_MEDIDA"}>
              Peso y caja: el MENOR creíble entre {(b?.fuentes_medida ?? []).join(" · ")}
              {b?.fuentes_medida_error ? " (⚠ variable mal escrita: sólo almacén y guías de Temu)" : ""}
            </span>
          )}
          {lj && (
            <span title="TEMU_GUIAS_JT_*: con ellos se acomodan las piezas; una caja que no cabe se cotiza igual, con aviso">
              Límites de J&T: {lj.peso} kg · {lj.lado} cm por lado · {lj.suma} cm sumando los tres
              {b?.excede_jt_compra === false ? " (la caja que los excede queda manual)" : ""}
            </span>
          )}
          {b?.peso_guia_x1_por_piezas === false && (
            <span>Peso: la guía de 1 pieza NO se multiplica por las piezas</span>
          )}
        </div>
      )}

      {datos.error && (
        <p className="mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[12px]"
           style={{ background: "#FFFBEB", color: "#92400E" }}>
          <AlertTriangle className="h-4 w-4 shrink-0" />{datos.error}
        </p>
      )}

      {detenido && (
        <div className="mt-2 rounded-[12px] px-3 py-2 text-[12.5px]" style={{ background: "#FFF1F2", color: "#9F1239" }}>
          <div className="flex items-center gap-2 font-extrabold">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            Detenida {detenido.desde ? hace(detenido.desde) : ""}: no compra nada hasta que alguien concilie
          </div>
          <div className="mt-1 text-[12px]">{detenido.motivo ?? "sin motivo"}</div>
          {puedeMover && abiertas.length === 0 && sinAceptar.length === 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {/* Sin compras abiertas ni etiquetas en aplicación: la detención es
                  por una orden de Odoo que no quedó como dice su guía.
                  "Conciliar" mira Temu otra vez y, si todo está cerrado, libera
                  el job; queda registrado quién. */}
              {(detenido.ventas ?? []).map((po) => (
                <button key={po} type="button" onClick={() => conciliar(po)} disabled={ocupado !== null}
                        className="rounded-[8px] px-3 py-[4px] text-[11.5px] font-bold text-white disabled:opacity-50"
                        style={{ background: "#9F1239" }}
                        title="Ya revisé esta venta (su guía y su orden de Odoo): concilia y libera la compra automática">
                  {ocupado === `conciliar:${po}:false` ? "Conciliando…" : `Conciliar ${po}`}
                </button>
              ))}
              {(detenido.ventas ?? []).length === 0 && (
                <button type="button" onClick={() => conciliar(null)} disabled={ocupado !== null}
                        className="rounded-[8px] px-3 py-[4px] text-[11.5px] font-bold text-white disabled:opacity-50"
                        style={{ background: "#9F1239" }}
                        title="Sin compras abiertas: libera la compra automática (queda registrado quién)">
                  {ocupado?.startsWith("conciliar:-") ? "Liberando…" : "Ya lo revisé: liberar"}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {abiertas.length > 0 && (
        <div className="mt-2 space-y-1">
          {abiertas.map((a) => (
            <div key={a.parent_order_sn}
                 className="flex flex-wrap items-center gap-2 rounded-[10px] border px-3 py-[6px] text-[12px]"
                 style={{ borderColor: "#FDA4AF" }}>
              <code className="font-mono font-bold text-slate-800">{a.parent_order_sn}</code>
              <span className="rounded-full px-[7px] py-[1px] text-[10.5px] font-extrabold"
                    style={{ background: "#FFF1F2", color: "#9F1239" }}>{a.estado}</span>
              {a.codigo && <span className="font-mono text-[11px] text-slate-400">{a.codigo}</span>}
              <span className="min-w-0 flex-1 truncate text-slate-500" title={a.motivo ?? undefined}>
                {a.motivo ?? ""} {a.desde ? `· ${hace(a.desde)}` : ""}
              </span>
              {puedeMover && (
                <>
                  <button type="button" onClick={() => conciliar(a.parent_order_sn)} disabled={ocupado !== null}
                          className="rounded-[8px] px-2 py-[3px] text-[11px] font-bold text-white disabled:opacity-50"
                          style={{ background: "#4F46E5" }}
                          title="Mira en Temu si la guía se compró y lo anota; si ya no queda nada abierto, libera el job">
                    {ocupado === `conciliar:${a.parent_order_sn}:false` ? "Conciliando…" : "Conciliar"}
                  </button>
                  {sinRastro[a.parent_order_sn] && (
                    <button type="button" onClick={() => conciliar(a.parent_order_sn, true)} disabled={ocupado !== null}
                            className="rounded-[8px] border bg-white px-2 py-[3px] text-[11px] font-bold disabled:opacity-50"
                            style={{ borderColor: "#FDA4AF", color: "#9F1239" }}
                            title="Temu no muestra guía de esta venta. Sólo si ya lo verificaste en el seller center (pasados 30 min)">
                      Liberar sin rastro (ya verifiqué en Temu)
                    </button>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {rechazadas.length > 0 && (
        <div className="mt-2 space-y-1">
          {rechazadas.map((x) => (
            <div key={x.parent_order_sn}
                 className="flex flex-wrap items-center gap-2 rounded-[10px] border px-3 py-[6px] text-[12px]"
                 style={{ borderColor: "#FDA4AF" }}>
              <code className="font-mono font-bold text-slate-800">{x.parent_order_sn}</code>
              <span className="rounded-full px-[7px] py-[1px] text-[10.5px] font-extrabold"
                    style={{ background: "#FFF1F2", color: "#9F1239" }}>
                Temu la rechazó{x.codigo ? ` · ${x.codigo}` : ""}
              </span>
              <span className="min-w-0 flex-1 truncate text-slate-500" title={x.motivo ?? undefined}>
                {x.motivo ?? ""} {x.desde ? `· ${hace(x.desde)}` : ""}
              </span>
              {x.reintento === "pedido" ? (
                <span className="text-[11px] font-bold" style={{ color: "#075985" }}>
                  se reintenta en la vuelta siguiente
                </span>
              ) : puedeMover && (
                <button type="button" onClick={() => conciliar(x.parent_order_sn, false, true)}
                        disabled={ocupado !== null}
                        className="rounded-[8px] px-2 py-[3px] text-[11px] font-bold text-white disabled:opacity-50"
                        style={{ background: "#4F46E5" }}
                        title="Mira en Temu que no tenga guía y deja que la vuelta siguiente la vuelva a comprar (una vez). No detiene ni compra aquí.">
                  {ocupado === `reintentar:${x.parent_order_sn}:false` ? "Mirando Temu…" : "Reintentar"}
                </button>
              )}
            </div>
          ))}
          <div className="text-[11px] text-slate-400">
            Un rechazo de Temu no detiene la compra automática: esa venta queda para compra manual y las
            demás se siguen comprando. Sola se reintenta una vez, cuando cambia su plan (medidas,
            paquetería o fecha).
          </div>
        </div>
      )}

      {pendientes.length > 0 && (
        <div className="mt-2 space-y-1">
          {pendientes.map((p) => (
            <div key={p.parent_order_sn}
                 className="flex flex-wrap items-center gap-2 rounded-[10px] border px-3 py-[6px] text-[12px]"
                 style={{ borderColor: p.aceptada ? "#CBD5E1" : "#FCD34D" }}>
              <code className="font-mono font-bold text-slate-800">{p.parent_order_sn}</code>
              <span className="rounded-full px-[7px] py-[1px] text-[10.5px] font-extrabold"
                    style={{ background: "#FFFBEB", color: "#92400E" }}>
                etiqueta en aplicación{p.aceptada ? " · aceptada" : ""}
              </span>
              <span className="min-w-0 flex-1 truncate text-slate-500" title={p.motivo ?? undefined}>
                {p.aceptada ? "se sigue conciliando sola; si Temu la marca fallida, detiene"
                            : "impide liberar la compra automática hasta que Temu la resuelva o alguien la acepte"}
                {p.desde ? ` · ${hace(p.desde)}` : ""}
              </span>
              {puedeMover && !p.aceptada && (
                <>
                  <button type="button" onClick={() => conciliar(p.parent_order_sn)} disabled={ocupado !== null}
                          className="rounded-[8px] px-2 py-[3px] text-[11px] font-bold text-white disabled:opacity-50"
                          style={{ background: "#4F46E5" }}
                          title="Mira en Temu cómo va la etiqueta y lo anota (fallida = sigue detenida)">
                    {ocupado === `conciliar:${p.parent_order_sn}:false` ? "Conciliando…" : "Conciliar"}
                  </button>
                  <button type="button" onClick={() => conciliar(p.parent_order_sn, true)} disabled={ocupado !== null}
                          className="rounded-[8px] border bg-white px-2 py-[3px] text-[11px] font-bold disabled:opacity-50"
                          style={{ borderColor: "#FCD34D", color: "#92400E" }}
                          title="Sólo si ya la revisaste en el seller center: la compra automática se libera y esta etiqueta se sigue vigilando sola">
                    Aceptar y liberar (ya revisé en Temu)
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {ensayo && (u.ensayo ?? []).length > 0 && (
        <div className="mt-2 rounded-[10px] px-3 py-2 text-[12px]" style={{ background: "#F0F9FF", color: "#075985" }}>
          <div className="font-extrabold">La última vuelta HABRÍA comprado (ensayo, no compró):</div>
          {(u.ensayo ?? []).map((e) => (
            <div key={e.ventas.join(",")} className="mt-[2px]">
              <code className="font-mono font-bold">{e.ventas.join(" + ")}</code>
              {" · "}{(e.paqueteria ?? []).join(" + ") || "?"} · {dinero(e.costo_mxn)}
              {e.fecha_envio ? ` · entrega ${fechaCorta(e.fecha_envio)}${e.horas ? ` (${e.horas} h)` : ""}` : ""}
              {e.tarde ? " · comprada TARDE (límite de Temu ya vencido)"
                : e.ajustada ? " · fecha adelantada por el límite de Temu" : ""}
              {e.medidas ? ` · medidas: ${e.medidas}` : ""}
              {(e.llamadas ?? []).length > 0
                ? ` · ${(e.llamadas ?? []).map((l) => `sendType ${l.send_type} · ${l.cajas} caja${l.cajas === 1 ? "" : "s"}`).join(" / ")}`
                : ""}
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-600">
        <span>
          Hoy: <b>{hoy.compras}</b>{t ? ` / ${t.dia}` : ""} compra{hoy.compras === 1 ? "" : "s"} ·{" "}
          <b>{dinero(hoy.gasto_mxn)}</b>{t ? ` / ${dinero(t.gasto_dia)}` : ""}
        </span>
        {t && (
          <span className="text-slate-400">
            topes: {t.vuelta} por vuelta · caja {dinero(t.caja)} · venta {dinero(t.venta)}
          </span>
        )}
        <span className="text-slate-400" title={u.motivo ?? undefined}>
          Última vuelta: {u.ts ? `${hace(u.ts)} · ` : "todavía no corre en este arranque"}
          {u.estado ? (ESTADO_VUELTA[u.estado] ?? u.estado) : ""}
          {u.motivo && u.estado !== "compro" ? ` — ${u.motivo}` : ""}
          {u.esperan_turno ? ` · ${u.esperan_turno} lista(s) para la vuelta siguiente` : ""}
        </span>
      </div>

      {(manuales.length > 0 || urgentes.length > 0) && (
        <div className="mt-2 rounded-[10px] px-3 py-2 text-[12px]"
             style={{ background: urgentes.length ? "#FFF1F2" : "#FFFBEB",
                      color: urgentes.length ? "#9F1239" : "#92400E" }}>
          <b>{manuales.length}</b> venta{manuales.length === 1 ? "" : "s"} nueva{manuales.length === 1 ? "" : "s"}{" "}
          requiere{manuales.length === 1 ? "" : "n"} compra manual de guía
          {Object.keys(porClase).length > 0 && ` (${Object.entries(porClase).map(([k, n]) => `${k}: ${n}`).join(", ")})`}
          {urgentes.length > 0 && (
            <div className="mt-1 font-bold">
              URGENTES (límite de envío de Temu vencido y la compra automática no puede comprarlas): {urgentes.join(", ")}
            </div>
          )}
          <div className="mt-1 text-[11px] opacity-80">
            No es un error: el motivo de cada una sale en su renglón de la lista.
          </div>
        </div>
      )}

      {aviso && (
        <p className="mt-2 text-[12px] font-semibold" style={{ color: aviso.tono === "ok" ? "#047857" : "#9F1239" }}>
          {aviso.txt}
        </p>
      )}
    </section>
  );
}
