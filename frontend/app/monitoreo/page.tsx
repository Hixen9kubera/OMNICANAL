"use client";

/**
 * /monitoreo — Quién hizo qué en el panel.
 *
 * REQUISITO PRINCIPAL (Brandon): monitorear qué procesos corre cada persona.
 * Ése es el eje. No es un tablero de KPIs con gente dentro: es un tablero de
 * PERSONAS con su trabajo dentro. Las metas semanales son una CAPA encima.
 *
 * Lo leen el dueño, los admins y los propios KAMs. Eso último manda sobre el
 * tono: no puede sentirse un panóptico.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * 🔴 LA REGLA DE ORO — si sólo se conserva una cosa de este archivo, es ésta
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *   «NO LO HIZO» y «NO LO SABEMOS» no pueden verse iguales.
 *
 * TikTok, Temu y Walmart se publican con scripts de escritorio. Si la pantalla
 * le pinta un 0 a un KAM en TikTok, MIENTE: esa persona quizá publicó 200
 * productos por un camino que no deja firma. El primer KAM que lo note deja de
 * creerle al tablero, y con razón.
 *
 * Por eso hay TRES lecturas y no dos:
 *
 *   498 / 512   en color   →  medido. Éxitos sobre intentos.
 *   0 / 0       en gris    →  «no lo hizo». Medido, y fue cero.
 *   ▨ sin registro         →  «no lo sabemos». El proceso no guarda actor.
 *
 * El tercero es una TEXTURA, no un color. Un color más se leería como otro
 * estado de la misma escala; el rayado se lee como "esto no es una cifra".
 *
 * ⚠️ El backend manda `null` —no `0`— cuando no se puede atribuir. Si alguna vez
 * se colapsa ese `null` a cero, esta pantalla pierde su razón de ser.
 *
 * La LEYENDA de las tres lecturas vive arriba de la tabla, a la vista. No es un
 * detalle de accesibilidad: es lo que vuelve creíble al tablero. No moverla a un
 * tooltip.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * Decisiones que ya costaron un incidente. No revertir.
 * ═══════════════════════════════════════════════════════════════════════════
 * 1. ÉXITOS SOBRE INTENTOS, los dos números siempre. «6 / 7», no «6». Doce de
 *    doce no es lo mismo que doce de cuarenta.
 * 2. Cada proceso con SU verbo: publicado · costo validado · producto creado.
 *    Decir "publicado" de un recálculo de costo es falso, y es lo que hacía
 *    esta pantalla hasta el 1-sep.
 * 3. Los movimientos AUTOMÁTICOS no aparecen: no los hizo nadie.
 * 4. Dos correos de una persona se fusionan ARRIBA, no ABAJO. En el detalle cada
 *    fila conserva el correo real — eso es lo que la vuelve auditable.
 * 5. El estado vacío DICE LA VERDAD en vez de fingir que nadie trabajó.
 * 6. NO expone costos ni márgenes: la ven los KAMs, no sólo los admins.
 *
 * Sin dependencias nuevas: todas las gráficas son SVG a mano.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, CalendarOff, CheckCircle2, ChevronDown, CloudOff,
  Copy, GitMerge, RefreshCw, Terminal,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

// ── Contrato con el backend ─────────────────────────────────────────────────
// `Celda` puede ser null, y ESO es el punto. Ver la regla de oro arriba.
type Celda = { exitos: number; intentos: number } | null;

interface Usuario {
  usuario: string;
  correos: string[];
  total: number;
  exitos: number;
  errores: number;
  ultima: string | null;
  serie: number[];
  celdas: Record<string, Celda>;
  canales: Record<string, { total: number; exitos: number }>;
  canales_sin_registro: string[];
}

interface Cobertura {
  proceso: string; filas: number; con_actor: number; personas: number;
}
interface PubCanal {
  canal: string; nuevas: number; con_actor: number;
  /** La MISMA cuenta, en la semana anterior. Es la mitad de "week over week". */
  previa: number;
  /** Mandadas y sin veredicto del canal. Hoy sólo Walmart las produce. */
  sin_confirmar: number;
  /** Las hizo un script que se declaró como tal (`--como automatico`). */
  por_codigo: number;
  /** Nadie las firmó: no se sabe si fue persona o código. */
  sin_firma: number;
}
interface SinMovs { usuario: string; correo: string; nombre?: string | null }

interface Resumen {
  ok: boolean;
  dias: number;
  usuarios: Usuario[];
  total: number;
  procesos: string[];
  procesos_sin_registro: string[];
  canales_sin_registro: string[];
  cobertura: Cobertura[];
  publicaciones_semana: PubCanal[];
  costos_semana: { actual?: number; previa?: number };
  meta_semanal: number;
  sin_movimientos: SinMovs[];
}

interface Movimiento {
  created_at: string; actor: string; proceso: string; accion: string;
  sku: string | null; estado: string; canal: string | null;
  cuenta: string | null; detalle: Record<string, unknown> | null;
  duracion_s: number | null;
}

// ── Vocabulario ─────────────────────────────────────────────────────────────
/** Cada proceso tiene su verbo. Decir "publicado" de un costo es falso. */
const VERBO: Record<string, string> = {
  publicar: "Publicado", costos: "Costo validado", crear: "Producto creado",
  competencia: "Competencia", precio: "Precio editado", stock: "Stock editado",
};
const COLUMNA: Record<string, string> = {
  crear: "Creados", costos: "Costos val.", publicar: "Publicados",
  competencia: "Competencia", precio: "Precio", stock: "Stock",
};
const NOMBRE_CANAL: Record<string, string> = {
  mercado_libre: "Mercado Libre", amazon: "Amazon", tiktok: "TikTok",
  temu: "Temu", walmart: "Walmart", shein: "Shein", general: "Omnicanal",
};
const CORTO_CANAL: Record<string, string> = {
  mercado_libre: "MELI", amazon: "AMZ", tiktok: "TT", temu: "Tm",
  walmart: "WM", shein: "SH",
};
/**
 * Los mismos hex de `frontend/lib/theme.ts` y `backend/core/marketplaces.py`.
 *
 * `punto` es el color del canal, `fg` el texto legible sobre su fondo suave, y
 * `acento` el SEGUNDO color de la marca.
 *
 * ⚠️ `acento` NO es `fg`, y confundirlos se ve feo: en la primera versión la
 * franja superior de la barra usaba `fg`, así que Mercado Libre salía con una
 * raya AZUL MARINO (#2D3277, que es su color de texto) encima del amarillo. El
 * acento de ML es #3483FA. Se notó en la captura antes que en el código.
 *
 * El acento también es la técnica para separar canales que comparten color:
 * TikTok y Shein son los dos #111827 y sólo los distingue esta franja.
 */
const COLOR_CANAL: Record<string,
  { bg: string; fg: string; punto: string; acento: string }> = {
  mercado_libre: { bg: "#FFFBE0", fg: "#2D3277", punto: "#FFE600", acento: "#3483FA" },
  amazon: { bg: "#FFF4E0", fg: "#131A22", punto: "#FF9900", acento: "#232F3E" },
  tiktok: { bg: "#F1F1F4", fg: "#111827", punto: "#111827", acento: "#FE2C55" },
  temu: { bg: "#FFF0E3", fg: "#7C2D12", punto: "#FB7701", acento: "#FF5000" },
  walmart: { bg: "#E6F1FC", fg: "#0071DC", punto: "#0071DC", acento: "#FFC220" },
  shein: { bg: "#F1F1F4", fg: "#111827", punto: "#111827", acento: "#7C3AED" },
  general: { bg: "#EEF0FF", fg: "#4F46E5", punto: "#4F46E5", acento: "#818CF8" },
};

/** Los cinco canales que tienen meta semanal, en el orden del pliego. */
const CANALES_META = ["mercado_libre", "amazon", "tiktok", "temu", "walmart"];

/**
 * El color del avatar de cada persona.
 *
 * Parece aleatorio y NO lo es, a propósito: sale de un hash del correo, así que
 * una persona conserva SIEMPRE el mismo color — entre recargas, entre rangos y
 * entre pantallas. Un color de verdad aleatorio cambiaría en cada render y
 * destruiría lo único que aporta: que reconozcas a alguien de un vistazo antes
 * de leer su nombre.
 *
 * La paleta esquiva a propósito el verde, el ámbar y el rojo: esos tres ya
 * significan algo en esta pantalla (acierto, parcial, error) y reutilizarlos en
 * un avatar los volvería ruido.
 */
const PALETA_PERSONA = [
  { bg: "#EDE9FE", fg: "#5B21B6" },   // violeta
  { bg: "#DBEAFE", fg: "#1E40AF" },   // azul
  { bg: "#CFFAFE", fg: "#155E75" },   // cian
  { bg: "#FCE7F3", fg: "#9D174D" },   // rosa
  { bg: "#E0E7FF", fg: "#3730A3" },   // índigo
  { bg: "#F3E8FF", fg: "#6B21A8" },   // púrpura
  { bg: "#E2E8F0", fg: "#334155" },   // pizarra
  { bg: "#CCFBF1", fg: "#115E59" },   // teal
  { bg: "#FAE8FF", fg: "#86198F" },   // fucsia
  { bg: "#E0F2FE", fg: "#075985" },   // celeste
  { bg: "#EDE9FE", fg: "#4C1D95" },   // violeta oscuro
  { bg: "#F1F5F9", fg: "#0F172A" },   // tinta
];

function _hash(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * Asigna un color a cada persona de la lista. Devuelve un mapa correo → color.
 *
 * EL HASH SOLO NO ALCANZA, y se midió: con 12 colores y 11 personas las
 * colisiones son frecuentes —el problema del cumpleaños— y en la primera versión
 * **Cinthya y Eduardo salían del mismo color estando los dos en la tabla**. Eso
 * rompe justo lo único que el color aporta: reconocer a alguien de un vistazo
 * antes de leer su nombre.
 *
 * Tampoco sirve repartir por posición en la lista: quedarían siempre distintos,
 * pero el color de una persona cambiaría cada vez que otra entra o sale, y
 * entonces no hay nada que aprender.
 *
 * Solución: el hash propone y, si el lugar ya está tomado, se corre al siguiente
 * libre. La lista se recorre en orden alfabético para que el resultado no
 * dependa de cómo vengan ordenados los datos. Así son SIEMPRE distintos dentro
 * de la pantalla, y sólo cambia de color quien de verdad chocaba.
 *
 * Nota: la paleta esquiva el verde, el ámbar y el rojo a propósito — esos tres
 * ya significan algo aquí (acierto, parcial, error) y en un avatar serían ruido.
 */
function coloresDe(correos: string[]): Record<string, { bg: string; fg: string }> {
  const n = PALETA_PERSONA.length;
  const tomados = new Set<number>();
  const out: Record<string, { bg: string; fg: string }> = {};
  for (const correo of [...correos].sort()) {
    let i = _hash(correo) % n;
    // Si hay más personas que colores, se permite repetir en vez de colgarse.
    for (let k = 0; k < n && tomados.has(i); k++) i = (i + 1) % n;
    tomados.add(i);
    out[correo] = PALETA_PERSONA[i];
  }
  return out;
}

/**
 * Cómo se llama cada cuenta para una persona. Los códigos internos no se
 * enseñan: nadie dice "publiqué en SANCORFASHION".
 */
const NOMBRE_CUENTA: Record<string, string> = {
  BEKURA: "Kubera", SANCORFASHION: "San Corpe",
  AMAZON: "San Corpe", KUBERA: "Kubera", TEMU: "Temu", WALMART: "Walmart",
};

/**
 * Los canales de una persona, UNO POR CANAL Y CUENTA.
 *
 * Mercado Libre tiene DOS cuentas —Kubera y San Corpe— y saber en cuál se
 * publicó es media pregunta. La primera versión mostraba sólo el canal y tiraba
 * la cuenta, así que salían tres chips seguidos que decían los tres «MELI», con
 * 3, 2 y 1: ilegible, y parecía un error de datos.
 *
 * ⚠️ El chip SIN cuenta no es un error, y no hay que esconderlo: publicar en ML
 * manda a las dos cuentas de una vez, así que la petición no tiene *una* cuenta
 * y hasta v0.395.0 no se guardaba ninguna. Esas filas viejas se muestran sin
 * etiqueta y lo dicen en el título — inventarles una cuenta sería peor.
 */
function porCanalYCuenta(canales: Usuario["canales"]) {
  return Object.entries(canales)
    .filter(([k]) => k !== "(sin canal)")
    .map(([clave, v]) => {
      const [canal, cuenta] = clave.split("·");
      return { clave, canal, cuenta: cuenta || "", ...v };
    })
    .sort((a, b) => b.exitos - a.exitos);
}

const RANGOS = [
  { v: 30, t: "30 días" }, { v: 7, t: "Semana" }, { v: 1, t: "Hoy" },
];

// ── Utilidades ──────────────────────────────────────────────────────────────
function persona(correo: string): string {
  const n = correo.split("@")[0].replace(/[._]/g, " ");
  return n.replace(/\b\w/g, (c) => c.toUpperCase());
}
function iniciales(correo: string): string {
  const p = persona(correo).split(" ");
  return ((p[0]?.[0] ?? "") + (p[1]?.[0] ?? "")).toUpperCase() || "??";
}
function cuando(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  if (min < 1440) return `hace ${Math.round(min / 60)} h`;
  const dias = Math.round(min / 1440);
  if (dias === 1) return "ayer";
  if (dias < 30) return `hace ${dias} d`;
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
}
/** El color de la cifra sale de la TASA DE ACIERTO, no del volumen. */
function tono(c: Exclude<Celda, null>): { texto: string; barra: string } {
  if (c.intentos === 0) return { texto: "#94A3B8", barra: "transparent" };
  const r = c.exitos / c.intentos;
  if (r >= 1) return { texto: "#047857", barra: "#10B981" };
  if (r >= 0.9) return { texto: "#0f172a", barra: "#4F46E5" };
  return { texto: "#B45309", barra: "#F59E0B" };
}

// ── El rayado: «no lo sabemos» ──────────────────────────────────────────────
const RAYADO: React.CSSProperties = {
  background: "repeating-linear-gradient(135deg,#f8fafc 0 5px,#eef2f7 5px 10px)",
  border: "1px dashed #cbd5e1",
};

function ChipSinRegistro({ titulo }: { titulo: string }) {
  return (
    <span title={titulo} style={RAYADO}
      className="inline-flex items-center rounded px-2 py-[3px] font-mono
                 text-[9.5px] font-bold uppercase tracking-[.05em] text-slate-400">
      sin registro
    </span>
  );
}

/** Una celda de proceso. Aquí vive la diferencia entre las tres lecturas. */
function CeldaProceso({ c, proceso }: { c: Celda; proceso: string }) {
  if (c === null) {
    return <ChipSinRegistro
      titulo={`${COLUMNA[proceso] ?? proceso}: este proceso no guarda quién lo hizo. `
        + `No es un cero — es que no lo sabemos.`} />;
  }
  const t = tono(c);
  const pct = c.intentos ? (c.exitos / c.intentos) * 100 : 0;
  return (
    <div title={`${c.exitos} de ${c.intentos} intentos`}>
      <div className="tabular-nums">
        <span className="font-mono text-[14.5px] font-extrabold"
              style={{ color: t.texto }}>{c.exitos}</span>
        <span className="ml-1 font-mono text-[11px] text-slate-400">/ {c.intentos}</span>
      </div>
      <div className="mt-1 h-[3px] w-full rounded-full bg-slate-100">
        <div className="h-full rounded-full transition-all"
             style={{ width: `${pct}%`, background: t.barra }} />
      </div>
    </div>
  );
}

/** Sparkline. Sin librería: `<polyline>` y su eje. */
function Spark({ vals, sinHistoria }: { vals: number[]; sinHistoria?: boolean }) {
  const w = 104, h = 26, pad = 2;
  if (sinHistoria || !vals?.length || vals.every((v) => v === 0)) {
    return <div style={RAYADO} className="h-[26px] w-[104px] rounded" />;
  }
  const max = Math.max(...vals, 1);
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w;
    const y = h - pad - (v / max) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const total = vals.reduce((a, b) => a + b, 0);
  return (
    <svg width={w} height={h} role="img" className="overflow-visible">
      <title>{`${total} movimientos en la ventana · pico de ${max} en un día`}</title>
      <line x1="0" y1={h - 1} x2={w} y2={h - 1} stroke="#eef1f6" strokeWidth="1" />
      <polyline points={pts} fill="none" stroke="#4F46E5" strokeWidth="1.6"
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export default function MonitoreoPage() {
  const [datos, setDatos] = useState<Resumen | null>(null);
  const [movs, setMovs] = useState<Movimiento[]>([]);
  const [dias, setDias] = useState(30);
  const [proceso, setProceso] = useState("todos");
  const [canal, setCanal] = useState("todos");
  const [soloErrores, setSoloErrores] = useState(false);
  const [abierta, setAbierta] = useState<string | null>(null);
  const [errorAbierto, setErrorAbierto] = useState<string | null>(null);
  const [inactivosAbierto, setInactivosAbierto] = useState(false);
  const [estado, setEstado] = useState<"cargando" | "ok" | "error" | "vacio">("cargando");
  const [detalleError, setDetalleError] = useState<string>("");

  const cargar = useCallback(async () => {
    setEstado("cargando");
    setDetalleError("");
    const url = `${API_BASE}/api/monitoreo/resumen?dias=${dias}`;
    const t0 = Date.now();
    try {
      const r = await fetchSesion(url, { cache: "no-store" });
      if (!r.ok) {
        // El cuerpo crudo del servidor va a la pantalla: es lo que sirve para
        // depurar, y esconderlo obliga a abrir los logs de Railway.
        const cuerpo = await r.text().catch(() => "");
        setDetalleError(
          `GET /api/monitoreo/resumen?dias=${dias}\n`
          + `${r.status} ${r.statusText} · ${(Date.now() - t0).toLocaleString("es-MX")} ms\n\n`
          + (cuerpo.slice(0, 2000) || "(sin cuerpo en la respuesta)"));
        setEstado("error");
        return;
      }
      const d: Resumen = await r.json();
      setDatos(d);
      setEstado(d.usuarios?.length ? "ok" : "vacio");
    } catch (e) {
      setDetalleError(`GET /api/monitoreo/resumen?dias=${dias}\n`
        + `${(Date.now() - t0).toLocaleString("es-MX")} ms\n\n`
        + (e instanceof Error ? `${e.name}: ${e.message}` : String(e)));
      setEstado("error");
    }
  }, [dias]);

  useEffect(() => { void cargar(); }, [cargar]);

  // Los movimientos se piden SOLO al abrir a alguien: son la parte cara.
  useEffect(() => {
    if (!abierta) { setMovs([]); return; }
    let vivo = true;
    void (async () => {
      const q = new URLSearchParams({ dias: String(dias), limite: "80",
                                      usuario: abierta });
      if (canal !== "todos") q.set("canal", canal);
      try {
        const r = await fetchSesion(`${API_BASE}/api/monitoreo/movimientos?${q}`,
                                    { cache: "no-store" });
        if (vivo && r.ok) setMovs((await r.json()).movimientos ?? []);
      } catch { /* el renglón se queda sin detalle; la tabla sigue viva */ }
    })();
    return () => { vivo = false; };
  }, [abierta, dias, canal]);

  const usuarios = useMemo(() => {
    let u = datos?.usuarios ?? [];
    if (proceso !== "todos") u = u.filter((x) => x.celdas[proceso]?.intentos);
    if (soloErrores) u = u.filter((x) => x.errores > 0);
    if (canal !== "todos") u = u.filter((x) => x.canales[canal]?.total);
    return u;
  }, [datos, proceso, canal, soloErrores]);

  const procesos = datos?.procesos ?? [];
  // Con TODAS las personas, no sólo las filtradas: si se calculara sobre la
  // lista visible, marcar "Sólo con errores" le cambiaría el color a la gente.
  const colores = useMemo(() => coloresDe([
    ...(datos?.usuarios ?? []).map((u) => u.usuario),
    ...(datos?.sin_movimientos ?? []).map((s) => s.usuario),
  ]), [datos]);
  const totalErrores = (datos?.usuarios ?? []).reduce((s, u) => s + u.errores, 0);

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto max-w-[1318px] px-6 pb-8 pt-[22px]">
        <Encabezado
          dias={dias} setDias={setDias}
          proceso={proceso} setProceso={setProceso}
          canal={canal} setCanal={setCanal}
          soloErrores={soloErrores} setSoloErrores={setSoloErrores}
          totalErrores={totalErrores} procesos={procesos}
          canales={datos?.publicaciones_semana ?? []}
          recargar={() => void cargar()} cargando={estado === "cargando"} />

        {datos && <BandaMetas d={datos} />}

        <Leyenda />

        {estado === "cargando" && <Esqueleto dias={dias} />}
        {estado === "error" && <ErrorDeCarga detalle={detalleError}
                                             reintentar={() => void cargar()}
                                             a7dias={() => setDias(7)} />}
        {estado === "vacio" && <Vacio verDesde={() => setDias(30)} />}

        {estado === "ok" && datos && (
          <>
            <TablaPersonas
              usuarios={usuarios} procesos={procesos} datos={datos} colores={colores}
              abierta={abierta} setAbierta={setAbierta}
              movs={movs} errorAbierto={errorAbierto} setErrorAbierto={setErrorAbierto}
              inactivosAbierto={inactivosAbierto}
              setInactivosAbierto={setInactivosAbierto} />
            <div className="mt-4 grid grid-cols-2 gap-4">
              <TarjetaCobertura c={datos.cobertura} />
              <TarjetaCanales p={datos.publicaciones_semana}
                              mudos={datos.canales_sin_registro}
                              meta={datos.meta_semanal ?? 10} />
            </div>
          </>
        )}
      </main>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
function Encabezado(p: {
  dias: number; setDias: (n: number) => void;
  proceso: string; setProceso: (s: string) => void;
  canal: string; setCanal: (s: string) => void;
  soloErrores: boolean; setSoloErrores: (b: boolean) => void;
  totalErrores: number; procesos: string[]; canales: PubCanal[];
  recargar: () => void; cargando: boolean;
}) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="font-mono text-xs uppercase tracking-wider text-slate-500">
          Quién hizo qué
        </p>
        <h1 className="mt-1 text-[26px] font-extrabold tracking-[-.02em] text-slate-900">
          Monitoreo
        </h1>
        <p className="mt-1 max-w-2xl text-[13.5px] text-slate-500">
          Procesos ejecutados por persona. Los movimientos automáticos no
          aparecen: no los hizo nadie.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-md ring-1 ring-slate-200">
          {RANGOS.map((r) => (
            <button key={r.v} onClick={() => p.setDias(r.v)}
              className={"px-3 py-1.5 text-sm font-medium transition first:rounded-l-md "
                + "last:rounded-r-md "
                + (p.dias === r.v ? "bg-[#4F46E5] text-white"
                                  : "bg-white text-slate-700 hover:bg-slate-50")}>
              {r.t}
            </button>
          ))}
        </div>
        <select value={p.proceso} onChange={(e) => p.setProceso(e.target.value)}
          className="rounded-md bg-white px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-200">
          <option value="todos">Todos los procesos</option>
          {p.procesos.map((x) => <option key={x} value={x}>{COLUMNA[x] ?? x}</option>)}
        </select>
        <select value={p.canal} onChange={(e) => p.setCanal(e.target.value)}
          className="rounded-md bg-white px-3 py-1.5 text-sm text-slate-700 ring-1 ring-slate-200">
          <option value="todos">Todos los canales</option>
          {p.canales.map((c) => (
            <option key={c.canal} value={c.canal}>
              {NOMBRE_CANAL[c.canal] ?? c.canal}
            </option>))}
        </select>
        <label className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-1.5
                          text-sm font-medium"
          style={{ border: "1px solid #FECDD3", background: "#FFF1F2", color: "#9F1239" }}>
          <input type="checkbox" checked={p.soloErrores}
                 onChange={(e) => p.setSoloErrores(e.target.checked)}
                 className="h-3.5 w-3.5 accent-rose-600" />
          Sólo con errores
          <span className="font-mono text-xs tabular-nums">{p.totalErrores}</span>
        </label>
        <button onClick={p.recargar} aria-label="Actualizar"
          className="rounded-md bg-white p-2 text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
          <RefreshCw className={"h-4 w-4 " + (p.cargando ? "animate-spin" : "")} />
        </button>
      </div>
    </header>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
/**
 * La capa secundaria: las metas de la semana. ~90 px — no le roba el
 * protagonismo a la tabla de personas, que es el eje.
 *
 * LOS CINCO CANALES SON METAS DE PRIMERA, no una nota al margen. Hasta el
 * 4-sep aquí sólo cabían tres (Costos, MELI, Amazon) y TikTok, Temu y Walmart
 * vivían amontonados en un aviso de texto. Dejó de tener sentido en cuanto la
 * instrumentación de v0.398–v0.402 los hizo capturables: ahora cada uno tiene
 * su tarjeta y su color.
 *
 * Que hoy tres estén rayadas no es un estado permanente, es el estado de HOY —
 * y se mide solo. En cuanto entre la primera publicación firmada de cualquiera
 * de ellos, esa tarjeta se pinta con su color sin que nadie toque este archivo.
 */
function BandaMetas({ d }: { d: Resumen }) {
  // La meta la manda el backend (META_SEMANAL): 10 productos publicados por
  // canal, del EQUIPO. Antes se leía "10 por KAM" y se multiplicaba por 9 → 90,
  // que contra lo medido (Amazon 12, TikTok 1) no era una meta sino un reproche.
  const META = d.meta_semanal ?? 10;

  const metas = [
    { clave: "general", titulo: "Costos validados",
      v: d.costos_semana?.actual ?? 0, previa: d.costos_semana?.previa ?? 0,
      pendientes: 0, persona: d.costos_semana?.actual ?? 0, codigo: 0,
      sinFirma: 0, mudo: false },
    ...CANALES_META.map((canal) => {
      const p = d.publicaciones_semana.find((x) => x.canal === canal);
      return {
        clave: canal,
        titulo: NOMBRE_CANAL[canal] ?? canal,
        v: p?.nuevas ?? 0,
        previa: p?.previa ?? 0,
        pendientes: p?.sin_confirmar ?? 0,
        persona: p?.con_actor ?? 0,
        codigo: p?.por_codigo ?? 0,
        sinFirma: p?.sin_firma ?? 0,
        mudo: d.canales_sin_registro.includes(canal),
      };
    }),
  ];

  return (
    <section className="mb-3 rounded-lg bg-white px-[18px] py-[14px] ring-1 ring-slate-200">
      <div className="mb-3 flex items-baseline justify-between">
        <p className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">
          Semana en curso · metas del equipo
        </p>
        <span className="text-[11px] text-slate-400">
          mínimo {META} publicados por canal · vs semana previa
        </span>
      </div>
      <div className="grid grid-cols-6 gap-4">
        {metas.map((m) => {
          const c = COLOR_CANAL[m.clave] ?? COLOR_CANAL.general;
          const pct = Math.min(100, (m.v / META) * 100);
          const delta = m.v - m.previa;
          return (
            <div key={m.clave}>
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: c.punto,
                               boxShadow: `inset 0 0 0 1px ${c.acento}` }} />
                <span className="truncate text-[12.5px] font-semibold text-slate-700">
                  {m.titulo}
                </span>
              </div>

              {m.mudo ? (
                <>
                  {/* Sin firma todavía: NO se pinta un cero. Un cero diría "no
                      lo hizo" y sería mentira — ese canal sí publicó. */}
                  <div className="mt-1.5 h-2 rounded-full" style={RAYADO}
                       title={`${m.titulo} publicó esta semana, pero ninguna de `
                         + `esas publicaciones guardó quién la hizo.`} />
                  <p className="mt-1 flex items-center gap-1 font-mono text-[9.5px]
                                font-bold uppercase tracking-[.05em] text-slate-400">
                    <Terminal className="h-2.5 w-2.5" />sin registro
                  </p>
                </>
              ) : (
                <>
                  <div className="mt-[3px] flex items-baseline gap-1">
                    <span className="font-mono text-[15px] font-extrabold tabular-nums"
                          style={{ color: m.v >= META ? "#047857" : "#B45309" }}>
                      {m.v}
                    </span>
                    <span className="font-mono text-[11px] tabular-nums text-slate-400">
                      / {META}
                    </span>
                    {/* SEMANA CONTRA SEMANA. Sin el "previa" al lado, un +91 no
                        dice nada: podría ser una gran semana o una previa mala. */}
                    <span className="ml-auto font-mono text-[10.5px] tabular-nums"
                      title={`${m.v} esta semana contra ${m.previa} la previa`}
                      style={{ color: delta > 0 ? "#047857"
                             : delta < 0 ? "#B91C1C" : "#94A3B8" }}>
                      {delta > 0 ? "▲" : delta < 0 ? "▼" : "="}
                      {delta !== 0 && ` ${Math.abs(delta)}`}
                      <span className="ml-1 text-slate-400">({m.previa})</span>
                    </span>
                  </div>
                  <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full"
                         style={{ width: `${pct}%`, background: c.punto,
                                  borderTop: `2px solid ${c.acento}` }} />
                  </div>
                  {/* Walmart contesta con un feedId y juzga MINUTOS DESPUÉS. Se
                      cuentan —el trabajo se hizo— pero no se dan por buenas: de
                      sus veredictos resueltos, sólo 7 de 66 salieron bien. */}
                  {/* DE DÓNDE VINO CADA UNA. Brandon publicó UNA a Walmart y
                      vio 3: la pregunta "¿qué hice yo y qué fue de código?" no
                      se puede contestar si el total va suelto. */}
                  <p className="mt-1 flex flex-wrap gap-x-2 text-[10px] text-slate-400">
                    {m.persona > 0 && (
                      <span title="Las hizo una persona desde el panel">
                        {m.persona} de persona
                      </span>)}
                    {m.codigo > 0 && (
                      <span title="Las hizo un script que se declaró como automático">
                        {m.codigo} de código
                      </span>)}
                    {m.sinFirma > 0 && (
                      <span title="Nadie las firmó: no se sabe si fue persona o script"
                            className="text-slate-400">
                        {m.sinFirma} sin firma
                      </span>)}
                    {m.pendientes > 0 && (
                      <span title="El canal todavía no ha dicho si las acepta">
                        · {m.pendientes} sin confirmar
                      </span>)}
                  </p>
                </>
              )}
            </div>
          );
        })}
      </div>

      {d.canales_sin_registro.length > 0 && (
        <p className="mt-3 flex items-start gap-1.5 border-t border-slate-100 pt-2.5
                      text-[11.5px] leading-relaxed text-slate-400">
          <Terminal className="mt-[2px] h-3 w-3 shrink-0" />
          <span>
            {d.canales_sin_registro.map((c) => NOMBRE_CANAL[c] ?? c).join(", ")}{" "}
            {d.canales_sin_registro.length === 1 ? "publicó" : "publicaron"} esta
            semana, pero por un camino que no guarda quién lo hizo.{" "}
            <strong className="text-slate-500">No se les asigna avance a nadie</strong>
            {" "}— y en cuanto llegue la primera publicación firmada desde el panel,
            su tarjeta se pinta sola.
          </span>
        </p>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
/** Las tres lecturas, a la vista. Es lo que vuelve creíble al tablero. */
function Leyenda() {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg
                    bg-white px-[18px] py-[11px]"
         style={{ border: "1px dashed #cbd5e1" }}>
      <span className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">
        Cómo leer las cifras
      </span>
      <span className="flex items-center gap-2 text-[12px] text-slate-500">
        <span className="rounded bg-emerald-50 px-2 py-[3px] font-mono text-[12px]
                         font-bold tabular-nums text-emerald-700">498 / 512</span>
        éxitos sobre intentos
      </span>
      <span className="flex items-center gap-2 text-[12px] text-slate-500">
        <span className="rounded px-2 py-[3px] font-mono text-[12px] font-bold
                         tabular-nums text-slate-400 ring-1 ring-slate-200">0 / 0</span>
        <strong className="font-semibold text-slate-600">no lo hizo</strong>
        — medido, y fue cero
      </span>
      <span className="flex items-center gap-2 text-[12px] text-slate-500">
        <ChipSinRegistro titulo="El proceso no guarda quién lo hizo" />
        <strong className="font-semibold text-slate-600">no lo sabemos</strong>
        — el proceso no guarda actor
      </span>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// La tabla de personas — el corazón de la pantalla
// ═══════════════════════════════════════════════════════════════════════════
function TablaPersonas(p: {
  usuarios: Usuario[]; procesos: string[]; datos: Resumen;
  colores: Record<string, { bg: string; fg: string }>;
  abierta: string | null; setAbierta: (s: string | null) => void;
  movs: Movimiento[];
  errorAbierto: string | null; setErrorAbierto: (s: string | null) => void;
  inactivosAbierto: boolean; setInactivosAbierto: (b: boolean) => void;
}) {
  const conActor = p.datos.cobertura.reduce((s, c) => s + c.con_actor, 0);
  const todos = p.datos.cobertura.reduce((s, c) => s + c.filas, 0);
  const cols = `236px repeat(${p.procesos.length}, 92px) 1fr 112px 84px 62px 22px`;

  return (
    <section className="overflow-hidden rounded-lg bg-white ring-1 ring-slate-200">
      <div className="grid items-center gap-[10px] bg-slate-50 px-5 py-[11px]
                      font-mono text-[10px] uppercase tracking-[.09em] text-slate-500"
           style={{ gridTemplateColumns: cols }}>
        <span>Persona</span>
        {p.procesos.map((x) => <span key={x}>{COLUMNA[x] ?? x}</span>)}
        <span>Canales con actor</span>
        <span>Actividad {p.datos.dias} d</span>
        <span>Último</span>
        <span className="text-right">Errores</span>
        <span />
      </div>

      {p.usuarios.map((u) => {
        const abierto = p.abierta === u.usuario;
        return (
          <div key={u.usuario} className="border-t border-slate-100">
            <div role="button" tabIndex={0}
              onClick={() => p.setAbierta(abierto ? null : u.usuario)}
              onKeyDown={(e) => { if (e.key === "Enter") p.setAbierta(abierto ? null : u.usuario); }}
              className="grid cursor-pointer items-center gap-[10px] px-5 py-[11px]
                         transition hover:bg-slate-50/60"
              style={{ gridTemplateColumns: cols, minHeight: 57 }}>
              <div className="flex items-center gap-2.5">
                <div className="flex h-[34px] w-[34px] shrink-0 items-center
                                justify-center rounded-xl text-[12px] font-bold"
                     style={{ background: p.colores[u.usuario]?.bg ?? "#E2E8F0",
                              color: p.colores[u.usuario]?.fg ?? "#334155" }}>
                  {iniciales(u.usuario)}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[13.5px] font-bold text-slate-900">
                      {persona(u.usuario)}
                    </span>
                    {u.correos.length > 1 && (
                      <span title={u.correos.join(" · ")}
                        className="inline-flex shrink-0 items-center gap-1 rounded bg-slate-100
                                   px-1.5 py-[1px] text-[10px] text-slate-500">
                        <GitMerge className="h-3 w-3" />{u.correos.length} correos
                      </span>
                    )}
                  </div>
                  <div className="truncate font-mono text-[10.5px] text-slate-400">
                    {u.correos[0]}
                  </div>
                </div>
              </div>

              {p.procesos.map((x) => (
                <CeldaProceso key={x} c={u.celdas[x] ?? null} proceso={x} />
              ))}

              <div className="flex flex-wrap gap-1">
                {porCanalYCuenta(u.canales).map((v) => {
                  const c = COLOR_CANAL[v.canal] ?? COLOR_CANAL.general;
                  const cta = v.cuenta ? (NOMBRE_CUENTA[v.cuenta] ?? v.cuenta) : "";
                  return (
                    <span key={v.clave}
                      title={`${NOMBRE_CANAL[v.canal] ?? v.canal}`
                        + (cta ? ` · cuenta ${cta}` : " · sin cuenta registrada")
                        + `: ${v.exitos} de ${v.total}`}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-[2px]
                                 text-[10.5px] font-semibold"
                      style={{ background: c.bg, color: c.fg,
                               boxShadow: `inset 0 -2px 0 ${c.acento}33` }}>
                      <span className="h-1.5 w-1.5 rounded-full"
                            style={{ background: c.punto }} />
                      {CORTO_CANAL[v.canal] ?? v.canal}
                      {/* La cuenta, que es la mitad de la respuesta en ML. */}
                      {cta && <span className="font-normal opacity-75">{cta}</span>}
                      <span className="font-mono tabular-nums">{v.exitos}</span>
                    </span>
                  );
                })}
                {u.canales_sin_registro.length > 0 && (
                  <span style={RAYADO}
                    title={u.canales_sin_registro.map((c) => NOMBRE_CANAL[c] ?? c).join(", ")
                      + ": se publican con scripts de escritorio. No es un cero — es que no lo sabemos."}
                    className="inline-flex items-center gap-1 rounded px-1.5 py-[2px]
                               font-mono text-[10px] font-bold text-slate-400">
                    <Terminal className="h-3 w-3" />
                    {u.canales_sin_registro.map((c) => CORTO_CANAL[c] ?? c).join(" · ")}
                  </span>
                )}
              </div>

              <Spark vals={u.serie} />
              <span className="text-[11.5px] text-slate-500">{cuando(u.ultima)}</span>
              <span className="text-right">
                {u.errores > 0
                  ? <span className="rounded px-1.5 py-[2px] font-mono text-[11px]
                                     font-bold tabular-nums"
                          style={{ background: "#FFF1F2", color: "#9F1239" }}>
                      {u.errores}
                    </span>
                  : <span className="text-slate-300">—</span>}
              </span>
              <ChevronDown className={"h-[15px] w-[15px] text-slate-300 transition "
                + (abierto ? "rotate-180" : "")} />
            </div>

            {abierto && <Movimientos u={u} movs={p.movs} color={p.colores[u.usuario]}
                                     errorAbierto={p.errorAbierto}
                                     setErrorAbierto={p.setErrorAbierto} />}
          </div>
        );
      })}

      {p.datos.sin_movimientos.length > 0 && (
        <div className="border-t border-slate-100 bg-[#fbfcfe] px-5 py-3">
          <button onClick={() => p.setInactivosAbierto(!p.inactivosAbierto)}
            className="flex w-full items-center gap-3 text-left">
            <ChevronDown className={"h-4 w-4 shrink-0 text-slate-400 transition "
              + (p.inactivosAbierto ? "rotate-180" : "-rotate-90")} />
            <div className="flex -space-x-2">
              {p.datos.sin_movimientos.slice(0, 3).map((s) => (
                <span key={s.correo}
                  className="flex h-6 w-6 items-center justify-center rounded-full
                             border-2 border-[#fbfcfe] text-[9px] font-bold"
                  style={{ background: p.colores[s.usuario]?.bg ?? "#E2E8F0",
                           color: p.colores[s.usuario]?.fg ?? "#64748B" }}>
                  {iniciales(s.correo)}
                </span>))}
            </div>
            <span className="shrink-0 text-[12.5px] text-slate-600">
              <strong>{p.datos.sin_movimientos.length} personas sin movimientos
              registrados</strong> en esta ventana
            </span>
            <span className="text-[11.5px] text-slate-400">
              No hay tabla que asigne KAM a canal o categoría: un cero aquí puede
              ser inactividad real o trabajo por un camino sin registro.
            </span>
          </button>
          {p.inactivosAbierto && (
            <ul className="mt-3 grid grid-cols-3 gap-2 pl-11">
              {p.datos.sin_movimientos.map((s) => (
                <li key={s.correo} className="font-mono text-[11px] text-slate-400">
                  {s.correo}
                </li>))}
            </ul>
          )}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-slate-100
                      px-5 py-2.5 text-[11.5px] text-slate-400">
        <span>
          {p.usuarios.length} personas con actividad ·{" "}
          <span className="tabular-nums">{conActor.toLocaleString("es-MX")}</span>{" "}
          movimientos con actor de{" "}
          <span className="tabular-nums">{todos.toLocaleString("es-MX")}</span> totales
        </span>
        <span>Clic en una persona abre sus movimientos</span>
      </div>
    </section>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// El detalle de una persona, con EL ERROR CRUDO — requisito explícito
// ═══════════════════════════════════════════════════════════════════════════
const ESTADO_COLOR: Record<string, { bg: string; fg: string }> = {
  ok: { bg: "#ECFDF5", fg: "#047857" },
  completado: { bg: "#ECFDF5", fg: "#047857" },
  succeeded: { bg: "#ECFDF5", fg: "#047857" },
  reintento: { bg: "#FFFBEB", fg: "#92400E" },
  fallido: { bg: "#FFF1F2", fg: "#9F1239" },
  error: { bg: "#FFF1F2", fg: "#9F1239" },
  rechazado: { bg: "#FFF1F2", fg: "#9F1239" },
};

/**
 * El texto crudo del canal, tal cual llegó.
 *
 * Requisito de Brandon, textual: *"el error debe mostrarse explícito"*. Por eso:
 * NUNCA un tooltip (tiene que poderse seleccionar), NUNCA se resume ni se
 * trunca, y SÓLO uno abierto a la vez — los éxitos son la mayoría y no pueden
 * quedar sepultados bajo el ruido de los errores.
 */
function textoDelError(m: Movimiento): string {
  const d = (m.detalle ?? {}) as Record<string, unknown>;
  const partes: string[] = [];
  for (const llave of ["excepcion", "mensaje", "motivo", "error"]) {
    const v = d[llave];
    if (typeof v === "string" && v.trim()) partes.push(v);
  }
  // El desglose por cuenta de Mercado Libre: contesta "falló en BEKURA pero
  // entró en SANCOR", que es la pregunta que se hace quien lo lee.
  const filas = d.resultados;
  if (Array.isArray(filas) && filas.length) {
    partes.push("\nPor cuenta:");
    for (const f of filas as Record<string, unknown>[]) {
      partes.push(`  ${f.cuenta ?? "?"} — ${f.ok ? "ok" : "FALLÓ"}`
        + (f.error ? `: ${f.error}` : ""));
    }
  }
  if (!partes.length) partes.push(JSON.stringify(d, null, 2));
  return partes.join("\n");
}

function Movimientos(p: {
  u: Usuario; movs: Movimiento[]; color?: { bg: string; fg: string };
  errorAbierto: string | null; setErrorAbierto: (s: string | null) => void;
}) {
  const [verbo, setVerbo] = useState("todo");
  const fallo = (m: Movimiento) =>
    !["ok", "completado", "succeeded"].includes(m.estado);

  const chips = useMemo(() => {
    const c: Record<string, number> = {};
    for (const m of p.movs) c[m.proceso] = (c[m.proceso] ?? 0) + 1;
    return c;
  }, [p.movs]);

  const lista = p.movs.filter((m) =>
    verbo === "todo" ? true : verbo === "errores" ? fallo(m) : m.proceso === verbo);

  return (
    <div className="border-t border-slate-100 bg-slate-50/50 px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-[38px] w-[38px] items-center justify-center
                          rounded-xl text-[13px] font-bold"
               style={{ background: p.color?.bg ?? "#E2E8F0",
                        color: p.color?.fg ?? "#334155" }}>
            {iniciales(p.u.usuario)}
          </div>
          <div>
            <div className="text-[14px] font-bold text-slate-900">
              {persona(p.u.usuario)}
            </div>
            {/* Los correos REALES: la fusión es de presentación, no del registro. */}
            <div className="font-mono text-[10.5px] text-slate-400">
              {p.u.correos.join(" · ")}
              {p.u.correos.length > 1 && " — cada fila conserva el correo real"}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Chip activo={verbo === "todo"} onClick={() => setVerbo("todo")}
                texto="Todo" n={p.movs.length} />
          {Object.entries(chips).map(([k, n]) => (
            <Chip key={k} activo={verbo === k} onClick={() => setVerbo(k)}
                  texto={VERBO[k] ?? k} n={n} />
          ))}
          <Chip activo={verbo === "errores"} onClick={() => setVerbo("errores")}
                texto="Sólo errores" n={p.movs.filter(fallo).length} peligro />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg bg-white ring-1 ring-slate-200">
        <div className="grid gap-3 bg-slate-50 px-4 py-2 font-mono text-[10px]
                        uppercase tracking-[.09em] text-slate-500"
             style={{ gridTemplateColumns: "128px 170px 1fr 200px 96px 150px" }}>
          <span>Cuándo</span><span>Verbo del proceso</span><span>SKU</span>
          <span>Canal · cuenta</span><span>Estado</span><span>Correo real</span>
        </div>
        {lista.length === 0 && (
          <p className="px-4 py-6 text-center text-[12.5px] text-slate-400">
            Sin movimientos con ese filtro.
          </p>
        )}
        {lista.map((m, i) => {
          const id = `${m.created_at}-${i}`;
          const col = ESTADO_COLOR[m.estado] ?? { bg: "#F1F5F9", fg: "#475569" };
          const malo = fallo(m);
          return (
            <div key={id} className="border-t border-slate-100"
                 style={malo ? { background: "#FFFBFB" } : undefined}>
              <div className="grid items-center gap-3 px-4 py-2.5 text-[12.5px]"
                   style={{ gridTemplateColumns: "128px 170px 1fr 200px 96px 150px" }}>
                <span className="font-mono text-[11.5px] text-slate-400">
                  {cuando(m.created_at)}
                </span>
                <span className={"font-semibold "
                  + (malo ? "text-rose-700" : "text-slate-700")}>
                  {VERBO[m.proceso] ?? m.proceso}
                </span>
                <span className="truncate font-mono text-[11.5px] text-slate-600">
                  {m.sku ?? <span className="text-slate-300">varios SKUs</span>}
                </span>
                <span>
                  {m.canal ? (() => {
                    const c = COLOR_CANAL[m.canal] ?? COLOR_CANAL.general;
                    return (
                      <span className="inline-flex items-center gap-1 rounded px-1.5
                                       py-[2px] text-[10.5px] font-semibold"
                            style={{ background: c.bg, color: c.fg }}>
                        <span className="h-1.5 w-1.5 rounded-full"
                              style={{ background: c.punto }} />
                        {NOMBRE_CANAL[m.canal] ?? m.canal}
                      </span>);
                  })() : <span className="text-slate-300">—</span>}
                  {m.cuenta && (
                    <span className="ml-1.5 font-mono text-[10px] text-slate-400">
                      {m.cuenta}
                    </span>)}
                </span>
                <span className="inline-flex w-fit items-center gap-1.5 rounded px-2
                                 py-[2px] text-[11px] font-semibold"
                      style={{ background: col.bg, color: col.fg }}>
                  <span className="h-1.5 w-1.5 rounded-full"
                        style={{ background: col.fg }} />
                  {m.estado}
                </span>
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[10.5px] text-slate-400">
                    {m.actor}
                  </span>
                  {malo && (
                    <button onClick={(e) => { e.stopPropagation();
                        p.setErrorAbierto(p.errorAbierto === id ? null : id); }}
                      className="shrink-0 rounded px-1.5 py-[2px] text-[10.5px] font-bold"
                      style={{ background: p.errorAbierto === id ? "#9F1239" : "#FFF1F2",
                               color: p.errorAbierto === id ? "#fff" : "#9F1239",
                               border: "1px solid #FECDD3" }}>
                      Error
                    </button>)}
                </span>
              </div>

              {p.errorAbierto === id && <CajaError m={m} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Chip(p: { activo: boolean; onClick: () => void; texto: string;
                   n: number; peligro?: boolean }) {
  return (
    <button onClick={p.onClick}
      className={"rounded-md px-2.5 py-1 text-[11.5px] font-semibold transition "
        + (p.activo
          ? (p.peligro ? "bg-rose-600 text-white" : "bg-[#4F46E5] text-white")
          : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50")}>
      {p.texto} <span className="font-mono tabular-nums opacity-70">{p.n}</span>
    </button>
  );
}

function CajaError({ m }: { m: Movimiento }) {
  const texto = textoDelError(m);
  const [copiado, setCopiado] = useState(false);
  const lineas = texto.split("\n").length;
  return (
    <div className="px-4 pb-3 pl-[58px]">
      <div className="overflow-hidden rounded-lg" style={{ border: "1px solid #FECDD3" }}>
        <div className="flex items-center justify-between px-3 py-1.5"
             style={{ background: "#FFF1F2" }}>
          <span className="font-mono text-[10px] uppercase tracking-[.09em]"
                style={{ color: "#9F1239" }}>
            Mensaje del canal · sin editar
            <span className="ml-2 normal-case tracking-normal opacity-70">
              {m.canal ?? "—"}{m.cuenta ? ` · ${m.cuenta}` : ""} ·{" "}
              {new Date(m.created_at).toLocaleString("es-MX")}
            </span>
          </span>
          <button
            onClick={() => { void navigator.clipboard?.writeText(texto)
              .then(() => { setCopiado(true); setTimeout(() => setCopiado(false), 1500); }); }}
            className="inline-flex items-center gap-1 rounded bg-white px-2 py-[3px]
                       text-[10.5px] font-semibold"
            style={{ color: "#9F1239", border: "1px solid #FECDD3" }}>
            {copiado ? <CheckCircle2 className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            {copiado ? "Copiado" : "Copiar"}
          </button>
        </div>
        {/* `select-text` y `overflow-auto`: el texto TIENE que poder seleccionarse
            y no se recorta. Un tooltip aquí sería inservible. */}
        <pre className="max-h-[210px] select-text overflow-auto bg-white px-3 py-2
                        font-mono text-[11.5px] leading-[1.65] text-slate-700"
             style={{ whiteSpace: "pre" }}>{texto}</pre>
        <div className="px-3 py-1.5 text-[10.5px] text-slate-400"
             style={{ background: "#FFFBFB" }}>
          Texto seleccionable y completo · {lineas} línea{lineas === 1 ? "" : "s"} ·
          no se resume ni se recorta
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Las dos tarjetas de contexto
// ═══════════════════════════════════════════════════════════════════════════
function TarjetaCobertura({ c }: { c: Cobertura[] }) {
  return (
    <section className="rounded-lg bg-white p-[18px] ring-1 ring-slate-200">
      <p className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">
        Cobertura del registro
        <span className="ml-2 normal-case tracking-normal text-slate-400">
          qué parte de cada proceso sabemos atribuir
        </span>
      </p>
      <div className="mt-3 space-y-3">
        {c.map((x) => {
          const pct = x.filas ? Math.round((x.con_actor / x.filas) * 100) : 0;
          return (
            <div key={x.proceso}>
              <div className="flex items-baseline justify-between text-[12.5px]">
                <span className="font-semibold text-slate-700">
                  {VERBO[x.proceso] ?? x.proceso}
                </span>
                <span className="text-slate-400">
                  <strong className="font-mono tabular-nums text-slate-600">{pct}%</strong>
                  {" "}con persona ·{" "}
                  <span className="tabular-nums">{x.filas.toLocaleString("es-MX")}</span> filas ·{" "}
                  {x.personas} personas
                </span>
              </div>
              {/* El track ES el rayado: lo que no sabemos es el fondo. */}
              <div className="mt-1 h-2 overflow-hidden rounded-full" style={RAYADO}>
                <div className="h-full rounded-full bg-[#4F46E5]" style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-slate-400">
        <AlertTriangle className="mt-[2px] h-3 w-3 shrink-0" />
        <span>
          La parte rayada <strong>no es inactividad</strong>: son movimientos sin
          actor guardado. <strong>Editar precio</strong> y{" "}
          <strong>cambiar stock</strong> no aparecen porque ningún botón los
          registra todavía.
        </span>
      </p>
    </section>
  );
}

function TarjetaCanales({ p, mudos, meta }:
  { p: PubCanal[]; mudos: string[]; meta: number }) {
  const filas = CANALES_META.map((canal) => {
    const f = p.find((x) => x.canal === canal);
    return { canal, n: f?.nuevas ?? 0, previa: f?.previa ?? 0,
             mudo: mudos.includes(canal) };
  });
  const W = 106, H = 130;
  /**
   * ⚠️ EL TECHO DEL EJE ES 3× LA META, NO EL VALOR MÁS ALTO.
   *
   * Con la meta en 10 y Mercado Libre en 195, un eje pegado al máximo dejaría la
   * línea de meta al 5% de la altura —invisible— y a Amazon y TikTok como dos
   * rayas de un píxel. La meta dejaría de significar algo justo en el gráfico
   * que existe para medirla contra ella.
   *
   * Con el techo en 3× la meta, la línea queda a un tercio de la altura y los
   * canales chicos se leen. Lo que se pasa se dibuja al tope CON EL BORDE
   * CORTADO, y su número real va debajo siempre: no se esconde nada, se elige
   * dónde poner la resolución.
   */
  const TECHO = Math.max(meta * 3, 3);
  const alto = (v: number) => Math.max(6, (Math.min(v, TECHO) / TECHO) * H);
  const yMeta = H - alto(meta);

  return (
    <section className="rounded-lg bg-white p-[18px] ring-1 ring-slate-200">
      <div className="flex items-baseline justify-between">
        <p className="font-mono text-[10px] uppercase tracking-[.09em] text-slate-500">
          Publicaciones nuevas por canal · semana
        </p>
        <span className="text-[11px] text-slate-400">
          mínimo {meta} por canal
        </span>
      </div>
      <svg width="100%" viewBox={`0 0 ${W * filas.length} ${H + 26}`}
           className="mt-3" role="img">
        <defs>
          <pattern id="rayado" width="8" height="8" patternTransform="rotate(135)"
                   patternUnits="userSpaceOnUse">
            <rect width="8" height="8" fill="#f8fafc" />
            <line x1="0" y1="0" x2="0" y2="8" stroke="#dde3ec" strokeWidth="4" />
          </pattern>
        </defs>
        {/* La linea arranca despues del rotulo: con x1=0 el texto quedaba
            pisado por la propia linea y cortado por el borde del viewBox. */}
        <line x1="30" y1={yMeta} x2={W * filas.length} y2={yMeta}
              stroke="#cbd5e1" strokeDasharray="4 4" strokeWidth="1" />
        <text x="0" y={yMeta + 3} className="fill-slate-400"
              style={{ fontSize: 9, fontFamily: "ui-monospace,monospace" }}>meta</text>
        {filas.map((f, i) => {
          const c = COLOR_CANAL[f.canal];
          const h = f.mudo ? H * 0.42 : alto(f.n);
          const x = i * W + 8, y = H - h;
          return (
            <g key={f.canal}>
              <title>
                {f.mudo
                  ? `${NOMBRE_CANAL[f.canal]}: sin registro. Se publica con scripts `
                    + `de escritorio; el sistema no guarda quién los corrió.`
                  : `${NOMBRE_CANAL[f.canal]}: ${f.n} esta semana, `
                    + `${f.previa} la previa. Mínimo ${meta}.`}
              </title>
              <rect x={x} y={y} width={72} height={h} rx="3"
                    fill={f.mudo ? "url(#rayado)" : c.punto}
                    stroke={f.mudo ? "#cbd5e1" : "none"}
                    strokeDasharray={f.mudo ? "4 3" : undefined} />
              {/* Se pasó del techo: borde superior cortado, para que se vea que
                  la barra sigue más allá del lienzo. El número real va abajo. */}
              {!f.mudo && f.n > TECHO && (
                <line x1={x - 2} y1={y} x2={x + 74} y2={y}
                      stroke="#fff" strokeWidth="3" strokeDasharray="5 4" />
              )}
              {/* El acento sólo si la barra pasa de 14 px: si no, tapa la barra
                  entera y Amazon (3 publicaciones) se ve como una raya negra. */}
              {!f.mudo && h > 14 && (
                <rect x={x} y={y} width={72} height={4} rx="2" fill={c.acento} />
              )}
              <text x={x + 36} y={H + 12} textAnchor="middle"
                    className="fill-slate-500"
                    style={{ fontSize: 10, fontWeight: 600 }}>
                {CORTO_CANAL[f.canal]}
              </text>
              <text x={x + 36} y={H + 23} textAnchor="middle"
                    className="fill-slate-400"
                    style={{ fontSize: 9, fontFamily: "ui-monospace,monospace" }}>
                {f.mudo ? "sin registro"
                        : `${f.n}  ${f.n - f.previa > 0 ? "▲" : f.n - f.previa < 0 ? "▼" : "="}`
                          + `${f.n - f.previa !== 0 ? Math.abs(f.n - f.previa) : ""}`}
              </text>
            </g>
          );
        })}
      </svg>
      {mudos.length > 0 && (
        <p className="mt-2 flex items-start gap-1.5 text-[11.5px] leading-relaxed text-slate-400">
          <Terminal className="mt-[2px] h-3 w-3 shrink-0" />
          <span>
            Las barras rayadas <strong>no son ceros</strong>: esos canales sí
            publicaron, pero ninguna de esas publicaciones guardó quién la hizo.
          </span>
        </p>
      )}
    </section>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Los tres estados que se ven seguido. Dibujados, no improvisados.
// ═══════════════════════════════════════════════════════════════════════════
function Esqueleto({ dias }: { dias: number }) {
  return (
    <section className="overflow-hidden rounded-lg bg-white ring-1 ring-slate-200">
      {[1, 0.8, 0.6, 0.4].map((op, i) => (
        <div key={i} className="flex items-center gap-3 border-t border-slate-100 px-5"
             style={{ minHeight: 57, opacity: op }}>
          <div className="h-[34px] w-[34px] shrink-0 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-3 w-40 animate-pulse rounded bg-slate-100" />
          <div className="ml-auto flex gap-3">
            {[70, 70, 70, 104, 60].map((w, j) => (
              <div key={j} className="h-3 animate-pulse rounded bg-slate-100"
                   style={{ width: w }} />))}
          </div>
        </div>))}
      <p className="border-t border-slate-100 px-5 py-2.5 text-[11.5px] text-slate-400">
        Consultando movimientos de los últimos {dias} días…
      </p>
    </section>
  );
}

function ErrorDeCarga(p: { detalle: string; reintentar: () => void; a7dias: () => void }) {
  return (
    <section className="rounded-lg bg-white p-5" style={{ boxShadow: "0 0 0 1px #FECDD3" }}>
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
             style={{ background: "#FFF1F2" }}>
          <CloudOff className="h-4 w-4" style={{ color: "#9F1239" }} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-bold text-slate-900">
            No se pudieron cargar los movimientos
          </h2>
          <p className="text-[12.5px] text-slate-500">
            El resto de la pestaña sigue disponible. Nada de lo que ves quedó a medias.
          </p>
          <div className="mt-3 overflow-hidden rounded-lg"
               style={{ border: "1px solid #FECDD3" }}>
            <div className="px-3 py-1.5 font-mono text-[10px] uppercase
                            tracking-[.09em]"
                 style={{ background: "#FFF1F2", color: "#9F1239" }}>
              Respuesta del servidor
            </div>
            <pre className="max-h-40 select-text overflow-auto bg-white px-3 py-2
                            font-mono text-[11.5px] leading-relaxed text-slate-700"
                 style={{ whiteSpace: "pre-wrap" }}>{p.detalle}</pre>
          </div>
          <div className="mt-3 flex gap-2">
            <button onClick={p.reintentar}
              className="rounded-md bg-[#4F46E5] px-3 py-1.5 text-sm font-semibold text-white">
              Reintentar
            </button>
            <button onClick={p.a7dias}
              className="rounded-md bg-white px-3 py-1.5 text-sm font-semibold
                         text-slate-700 ring-1 ring-slate-200">
              Probar con 7 días
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

/** El vacío DICE LA VERDAD en vez de fingir que nadie trabajó. */
function Vacio({ verDesde }: { verDesde: () => void }) {
  return (
    <section className="rounded-lg bg-white p-5 ring-1 ring-slate-200">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
             style={{ background: "#E0F2FE" }}>
          <CalendarOff className="h-4 w-4" style={{ color: "#075985" }} />
        </div>
        <div className="flex-1">
          <h2 className="text-[15px] font-bold text-slate-900">
            Sin movimientos en esta ventana
          </h2>
          <p className="text-[12.5px] text-slate-500">
            No es que nadie trabajara: en esas fechas el sistema aún no guardaba
            quién hacía qué.
          </p>
          <div className="mt-3 rounded-lg p-3" style={{ background: "#F0F9FF" }}>
            <p className="font-mono text-[10px] uppercase tracking-[.09em]"
               style={{ color: "#075985" }}>Inicio del registro</p>
            <ul className="mt-2 space-y-1 text-[12px] text-slate-600">
              <li className="flex justify-between">
                <span>crear · costos · competencia</span>
                <span className="font-mono text-slate-400">activo</span></li>
              <li className="flex justify-between">
                <span>publicar <span className="text-slate-400">(con actor)</span></span>
                <span className="font-mono text-slate-400">1-sep-2026</span></li>
              <li className="flex justify-between">
                <span>TikTok · Temu · Walmart por script</span>
                <span className="font-mono text-slate-400">sin registro</span></li>
            </ul>
          </div>
          <p className="mt-2 text-[11.5px] text-slate-400">
            Lo anterior al 1-sep-2026 <strong>no se puede reconstruir</strong>: los
            movimientos existieron, el actor no se guardó. Preferimos decirlo a
            rellenarlo con ceros.
          </p>
          <button onClick={verDesde}
            className="mt-3 rounded-md bg-[#4F46E5] px-3 py-1.5 text-sm font-semibold text-white">
            Ver 30 días
          </button>
        </div>
      </div>
    </section>
  );
}
