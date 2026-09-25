"use client";

/**
 * INVENTARIO · Checklist — la validación de ALMACÉN.
 *
 * Brandon, 24-sep-2026: una pestaña de almacén que determine si cada SKU tiene
 * los atributos que Mercado Libre exige y que además pida dimensiones, cajas y
 * piezas. Cada semana se eligen ~100 SKUs; se descarga un Excel con lo que
 * falta, almacén lo llena y se vuelve a cargar. De los opcionales se elige
 * cuáles se vuelven obligatorios: la MATRIZ.
 *
 * Cómo se lee la pantalla:
 *   · El LOTE es por semana (lunes a domingo). Las flechas cambian de semana.
 *   · «Completo» = todos los atributos EXIGIDOS (los de ML + los de la matriz)
 *     y los seis datos de almacén.
 *   · El amarillo es el de Mercado Libre; el naranja, lo que exige el equipo;
 *     el azul, lo que mide almacén. Los mismos colores salen en el Excel.
 *
 * Lo que se captura aquí va al MISMO sitio que el Publicador (los atributos) y
 * al lado «Bodega» del cotejo de cajas del Catálogo Maestro (medidas y cajas).
 * Ver la cabecera de backend/services/checklist.py.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  ClipboardCheck, Download, ExternalLink, FileSpreadsheet, Loader2, Plus,
  RefreshCw, Ruler, Search, SlidersHorizontal, Trash2, Upload, X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import InventarioPestanas from "@/components/InventarioPestanas";
import {
  agregarAlChecklist, cargarListaChecklist, descargarChecklist, guardarAlmacenChecklist,
  guardarMatrizChecklist, importarChecklist, matrizChecklist, mensajeDeError,
  quitarDelChecklist, tableroChecklist,
} from "@/lib/api";
import { quienSoy } from "@/lib/sesion";
import type {
  CampoChecklist, EstadoChecklist, FilaChecklist, ImportacionChecklist,
  ListaChecklist, MatrizChecklist, NivelChecklist, SistemaChecklist, TableroChecklist,
} from "@/lib/types";

/* ─────────────────────────────── estilos ─────────────────────────────── */

// El amarillo de Mercado Libre (el mismo que sale en el Excel).
const ML = "#FFE600";

const ESTADO: Record<EstadoChecklist, { t: string; c: string }> = {
  completo: { t: "Completo", c: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  incompleto: { t: "Incompleto", c: "bg-amber-50 text-amber-800 ring-amber-200" },
  sin_categoria: { t: "Sin categoría ML", c: "bg-slate-100 text-slate-600 ring-slate-200" },
  sin_lista: { t: "ML no contestó", c: "bg-slate-100 text-slate-600 ring-slate-200" },
};

const NIVEL: Record<NivelChecklist, { t: string; c: string; style?: React.CSSProperties }> = {
  ml: { t: "Obligatorio ML", c: "text-[#2d3277] ring-[#e6cf00]", style: { background: ML } },
  matriz: { t: "Obligatorio (matriz)", c: "bg-orange-200 text-orange-900 ring-orange-300" },
  auto: { t: "Automático", c: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  principal: { t: "Opcional", c: "bg-slate-100 text-slate-600 ring-slate-200" },
  secundario: { t: "Opcional · facturación", c: "bg-slate-50 text-slate-400 ring-slate-200" },
};

type Filtro = "todos" | "pendientes" | "completos" | "faltan_ml" | "faltan_almacen" | "sin_categoria";

/* ─────────────────────────────── utilidades ─────────────────────────────── */

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** '2026-09-21' → '21 sep'. Se ancla al mediodía UTC para que la zona horaria
 *  del navegador no la mueva de día. */
function dia(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  return `${d.getUTCDate()} ${MESES[d.getUTCMonth()]}`;
}

function masDias(iso: string, n: number): string {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function haceCuanto(iso: string | null): string {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace un momento";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} d`;
}

const n = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("es-MX", { maximumFractionDigits: 2 });

/** La referencia de costos_validados en una línea (NO son medidas). */
function textoSistema(s: SistemaChecklist | null): string {
  if (!s) return "sin datos en el sistema";
  const partes: string[] = [];
  if (s.largo && s.ancho && s.alto) partes.push(`${n(s.largo)}×${n(s.ancho)}×${n(s.alto)} cm`);
  if (s.peso) partes.push(`${n(s.peso)} kg`);
  if (s.cajas_pl !== null || s.piezas_por_caja_pl !== null)
    partes.push(`PL ${n(s.cajas_pl)} cajas × ${n(s.piezas_por_caja_pl)} pzs`);
  return partes.join(" · ") || "sin datos en el sistema";
}

function medidas(f: FilaChecklist): string | null {
  const a = f.almacen;
  if (a.largo_cm === null && a.ancho_cm === null && a.alto_cm === null && a.peso_kg === null) return null;
  return `${n(a.largo_cm)}×${n(a.ancho_cm)}×${n(a.alto_cm)} cm · ${n(a.peso_kg)} kg`;
}

/* ─────────────────────────────── la página ─────────────────────────────── */

export default function ChecklistPage() {
  const [semana, setSemana] = useState<string | undefined>(undefined);
  const [datos, setDatos] = useState<TableroChecklist | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [busqueda, setBusqueda] = useState("");
  const [abierto, setAbierto] = useState<string | null>(null);
  const [modal, setModal] = useState<null | "agregar" | "matriz" | "cargar">(null);
  const [matrizCat, setMatrizCat] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [bajando, setBajando] = useState<null | "excel" | "csv">(null);
  // El rol solo esconde botones: la autoridad es el RBAC del backend.
  const [puedeCapturar, setPuedeCapturar] = useState(true);

  useEffect(() => {
    let vivo = true;
    void quienSoy().then((u) => {
      if (vivo && u?.autenticado) setPuedeCapturar(u.rol !== "lectura");
    }).catch(() => {});
    return () => { vivo = false; };
  }, []);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    tableroChecklist(semana, ctrl.signal)
      .then((d) => {
        setDatos(d);
        // La selección solo vale dentro del lote que se ve.
        setSel((s) => new Set([...s].filter((k) => d.filas.some((f) => f.sku === k))));
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer el checklist."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [semana]);

  useEffect(() => cargar(), [cargar]);

  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(() => setAviso(null), 6000);
    return () => clearTimeout(t);
  }, [aviso]);

  const filas = useMemo(() => {
    let items = datos?.filas ?? [];
    const pruebas: Record<Filtro, (f: FilaChecklist) => boolean> = {
      todos: () => true,
      pendientes: (f) => f.estado !== "completo",
      completos: (f) => f.estado === "completo",
      faltan_ml: (f) => f.faltan_ml.length > 0,
      faltan_almacen: (f) => f.faltan_almacen.length > 0,
      sin_categoria: (f) => f.estado === "sin_categoria" || f.estado === "sin_lista",
    };
    items = items.filter(pruebas[filtro]);
    const q = busqueda.trim().toLowerCase();
    if (q) {
      items = items.filter((f) => f.sku.toLowerCase().includes(q)
        || (f.titulo ?? "").toLowerCase().includes(q)
        || (f.categoria_nombre ?? "").toLowerCase().includes(q));
    }
    return items;
  }, [datos, filtro, busqueda]);

  const semanaVista = datos?.semana ?? semana ?? "";
  const bloqueado = !!datos?.falta_migracion;
  const elegidos = [...sel];

  const bajar = async (formato: "excel" | "csv") => {
    if (!semanaVista) return;
    setBajando(formato);
    try {
      await descargarChecklist(formato, semanaVista, elegidos);
    } catch (e) {
      setAviso(mensajeDeError(e, "No se pudo generar el archivo."));
    } finally {
      setBajando(null);
    }
  };

  const quitar = async () => {
    if (!elegidos.length || !semanaVista) return;
    if (!window.confirm(`¿Quitar ${elegidos.length} SKU(s) del lote de esta semana? `
      + "Lo capturado NO se borra: solo salen de la lista.")) return;
    try {
      const r = await quitarDelChecklist(semanaVista, elegidos);
      setAviso(r.ok ? `${r.quitados ?? 0} SKU(s) fuera del lote.` : r.motivo ?? "No se pudo.");
      setSel(new Set());
      cargar();
    } catch (e) {
      setAviso(mensajeDeError(e, "No se pudo quitar."));
    }
  };

  const todosVisibles = filas.length > 0 && filas.every((f) => sel.has(f.sku));
  const alternarTodos = () => {
    setSel((s) => {
      const nuevo = new Set(s);
      if (todosVisibles) filas.forEach((f) => nuevo.delete(f.sku));
      else filas.forEach((f) => nuevo.add(f.sku));
      return nuevo;
    });
  };

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">
        <InventarioPestanas />
        <Banner datos={datos} cargando={cargando} onRecargar={cargar} />

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        {bloqueado && (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              La pestaña está lista, pero kubera todavía no tiene sus tablas
              (<b>migración 0058</b>, <code>ops.checklist_*</code>). En cuanto se
              aplique, aquí aparece el lote de la semana.
            </span>
          </div>
        )}

        <Semana
          semana={semanaVista} etiqueta={datos?.etiqueta ?? ""} semanas={datos?.semanas ?? []}
          onCambiar={(s) => { setSemana(s); setSel(new Set()); setAbierto(null); }}
        />

        {datos && <Kpis datos={datos} filtro={filtro} setFiltro={setFiltro} />}

        {/* ─── barra de herramientas ─── */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={busqueda} onChange={(e) => setBusqueda(e.target.value)}
              placeholder="Buscar SKU, producto o categoría"
              className="w-72 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none focus:border-indigo-300"
            />
          </div>
          <Boton icono={Plus} onClick={() => setModal("agregar")}
                 deshabilitado={bloqueado || !puedeCapturar}
                 titulo={puedeCapturar ? "Agregar los SKUs de la semana" : "Tu rol es de solo lectura"}>
            Agregar SKUs
          </Boton>
          <Boton icono={SlidersHorizontal} deshabilitado={bloqueado}
                 onClick={() => { setMatrizCat(datos?.categorias[0]?.categoria ?? null); setModal("matriz"); }}>
            Matriz de obligatorios
          </Boton>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500">
              {elegidos.length
                ? <><b className="text-slate-800">{elegidos.length}</b> seleccionados</>
                : "Sin selección: se baja todo el lote"}
            </span>
            <Boton icono={bajando === "excel" ? Loader2 : FileSpreadsheet} primario
                   girar={bajando === "excel"}
                   deshabilitado={bloqueado || !datos?.filas.length || !!bajando}
                   onClick={() => bajar("excel")}>
              Descargar Excel
            </Boton>
            <Boton icono={bajando === "csv" ? Loader2 : Download} girar={bajando === "csv"}
                   deshabilitado={bloqueado || !datos?.filas.length || !!bajando}
                   onClick={() => bajar("csv")} titulo="Formato largo: un renglón por SKU y campo">
              CSV
            </Boton>
            <Boton icono={Upload} deshabilitado={bloqueado || !puedeCapturar}
                   onClick={() => setModal("cargar")}
                   titulo={puedeCapturar ? "Subir el Excel o CSV ya llenado" : "Tu rol es de solo lectura"}>
              Cargar Excel / CSV
            </Boton>
            {elegidos.length > 0 && puedeCapturar && (
              <Boton icono={Trash2} peligro onClick={quitar}>Quitar</Boton>
            )}
          </div>
        </div>

        <Tabla
          filas={filas} cargando={cargando} sel={sel}
          onSel={(sku) => setSel((s) => {
            const nuevo = new Set(s);
            if (nuevo.has(sku)) nuevo.delete(sku); else nuevo.add(sku);
            return nuevo;
          })}
          todos={todosVisibles} onTodos={alternarTodos}
          abierto={abierto} onAbrir={(sku) => setAbierto((a) => (a === sku ? null : sku))}
          puedeCapturar={puedeCapturar && !bloqueado}
          onGuardado={(msg) => { setAviso(msg); cargar(); }}
          vacioLote={!datos?.filas.length}
          onAgregar={() => setModal("agregar")}
          onMatriz={(cat) => { setMatrizCat(cat); setModal("matriz"); }}
        />

        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          Solo kubera y la API pública de Mercado Libre — nada de WordPress. Los
          atributos se guardan donde los lee el Publicador; las medidas, cajas y
          piezas alimentan el lado «Bodega» del cotejo de cajas del Catálogo
          Maestro. Una celda vacía en el Excel no borra nada.
        </p>
      </main>

      {modal === "agregar" && semanaVista && (
        <ModalAgregar semana={semanaVista} etiqueta={datos?.etiqueta ?? ""}
                      onCerrar={() => setModal(null)}
                      onListo={(msg, otra) => {
                        setModal(null); setAviso(msg);
                        // La lista trae su propia semana («Week 39»): se salta a ella.
                        if (otra && otra !== semanaVista) { setSemana(otra); setSel(new Set()); }
                        else cargar();
                      }} />
      )}
      {modal === "matriz" && (
        <ModalMatriz
          categorias={datos?.categorias ?? []} inicial={matrizCat}
          puedeCapturar={puedeCapturar}
          onCerrar={(cambio) => { setModal(null); if (cambio) cargar(); }}
        />
      )}
      {modal === "cargar" && (
        <ModalCargar onCerrar={(cambio) => { setModal(null); if (cambio) cargar(); }} />
      )}

      {aviso && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg">
          {aviso}
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────── piezas ─────────────────────────────── */

function Boton({
  children, icono: Icono, onClick, deshabilitado, primario, peligro, titulo, girar,
}: {
  children: React.ReactNode;
  icono: typeof Plus;
  onClick: () => void;
  deshabilitado?: boolean;
  primario?: boolean;
  peligro?: boolean;
  titulo?: string;
  girar?: boolean;
}) {
  const tono = primario
    ? "bg-indigo-600 text-white hover:bg-indigo-700 border-indigo-600"
    : peligro
      ? "bg-white text-rose-600 border-rose-200 hover:bg-rose-50"
      : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50";
  return (
    <button
      type="button" onClick={onClick} disabled={deshabilitado} title={titulo}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${tono}`}
    >
      <Icono className={`h-4 w-4 ${girar ? "animate-spin" : ""}`} />
      {children}
    </button>
  );
}

function Banner({
  datos, cargando, onRecargar,
}: { datos: TableroChecklist | null; cargando: boolean; onRecargar: () => void }) {
  const r = datos?.resumen;
  const pastilla =
    "inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-2.5 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm";
  return (
    <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-600 to-violet-700 px-6 py-5 text-white shadow-[0_2px_8px_rgba(79,70,229,.25)]">
      <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-white/10" />
      <div className="relative flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-white/70">
            Inventario · Almacén
          </p>
          <h1 className="mt-1 flex items-center gap-2.5 text-3xl font-extrabold tracking-tight">
            <ClipboardCheck className="h-7 w-7" /> Checklist
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-white/80">
            Por cada SKU del lote de la semana: los atributos que exige Mercado
            Libre para su categoría y lo que almacén mide y cuenta — medidas,
            cajas y piezas por caja.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className={pastilla}>
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: ML }} />
              Mercado Libre obligatorio
            </span>
            <span className={pastilla}>
              <span className="h-2.5 w-2.5 rounded-full bg-orange-300" />
              Matriz del equipo
            </span>
            <span className={pastilla}>
              <Ruler className="h-3.5 w-3.5" /> Medidas · cajas · piezas
            </span>
          </div>
        </div>
        <div className="flex items-start gap-4">
          {r && (
            <div className="text-right">
              <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums">
                {r.completos}<span className="text-2xl text-white/60">/{r.total}</span>
              </div>
              <div className="mt-1 text-[11px] font-bold uppercase tracking-[0.06em] text-white/70">
                completos esta semana
              </div>
            </div>
          )}
          <button
            type="button" onClick={onRecargar} disabled={cargando}
            title="Volver a leer kubera y Mercado Libre"
            className="rounded-lg bg-white/15 p-2 text-white backdrop-blur-sm transition hover:bg-white/25 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>
    </section>
  );
}

function Semana({
  semana, etiqueta, semanas, onCambiar,
}: {
  semana: string;
  etiqueta: string;
  semanas: { semana: string; etiqueta: string; skus: number }[];
  onCambiar: (s: string | undefined) => void;
}) {
  if (!semana) return null;
  const otras = semanas.filter((s) => s.semana !== semana);
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <div className="inline-flex items-center rounded-xl bg-white ring-1 ring-slate-200">
        <button type="button" onClick={() => onCambiar(masDias(semana, -7))}
                className="rounded-l-xl p-2 text-slate-500 hover:bg-slate-50" title="Semana anterior">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="px-3 text-sm font-bold text-slate-800">
          {etiqueta || "Semana"}
          <span className="ml-1.5 font-medium text-slate-400">
            · {dia(semana)} al {dia(masDias(semana, 6))}
          </span>
        </span>
        <button type="button" onClick={() => onCambiar(masDias(semana, 7))}
                className="rounded-r-xl p-2 text-slate-500 hover:bg-slate-50" title="Semana siguiente">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
      <button type="button" onClick={() => onCambiar(undefined)}
              className="rounded-lg px-2.5 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50">
        Esta semana
      </button>
      {otras.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
          <span>Otros lotes:</span>
          {otras.slice(0, 6).map((s) => (
            <button key={s.semana} type="button" onClick={() => onCambiar(s.semana)}
                    className="rounded-full bg-white px-2.5 py-1 font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50">
              {s.etiqueta} · {s.skus}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Kpis({
  datos, filtro, setFiltro,
}: { datos: TableroChecklist; filtro: Filtro; setFiltro: (f: Filtro) => void }) {
  const r = datos.resumen;
  const tarjetas: { k: Filtro; t: string; v: number; p: string; tono: string }[] = [
    { k: "todos", t: "En el lote", v: r.total, p: "SKUs de esta semana", tono: "" },
    { k: "completos", t: "Completos", v: r.completos, p: "ML + almacén al 100%", tono: "ok" },
    { k: "faltan_ml", t: "Faltan atributos", v: r.faltan_ml, p: "de lo que exige ML o la matriz", tono: r.faltan_ml ? "ml" : "" },
    { k: "faltan_almacen", t: "Faltan medidas/cajas", v: r.faltan_almacen, p: "largo, ancho, alto, peso, cajas, piezas", tono: r.faltan_almacen ? "alm" : "" },
    { k: "sin_categoria", t: "Sin categoría ML", v: r.sin_categoria, p: "no se sabe qué pide ML", tono: "" },
  ];
  const marco = (tono: string, on: boolean) => {
    const base = tono === "ok" ? "border-emerald-200 bg-emerald-50"
      : tono === "ml" ? "border-[#e6cf00] bg-[#fffbd6]"
        : tono === "alm" ? "border-sky-200 bg-sky-50"
          : "border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]";
    return `${base} ${on ? "ring-2 ring-indigo-400 ring-offset-1" : "hover:brightness-[.98]"}`;
  };
  return (
    <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {tarjetas.map((c) => (
        <button key={c.k} type="button"
                onClick={() => setFiltro(filtro === c.k ? "todos" : c.k)}
                className={`rounded-2xl border p-4 text-left transition ${marco(c.tono, filtro === c.k && c.k !== "todos")}`}>
          <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">{c.t}</div>
          <div className="mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums text-slate-900">
            {c.v}
          </div>
          <div className="mt-1 text-xs text-slate-500">{c.p}</div>
        </button>
      ))}
    </div>
  );
}

/* ─────────────────────────────── la tabla ─────────────────────────────── */

function Tabla({
  filas, cargando, sel, onSel, todos, onTodos, abierto, onAbrir, puedeCapturar,
  onGuardado, vacioLote, onAgregar, onMatriz,
}: {
  filas: FilaChecklist[];
  cargando: boolean;
  sel: Set<string>;
  onSel: (sku: string) => void;
  todos: boolean;
  onTodos: () => void;
  abierto: string | null;
  onAbrir: (sku: string) => void;
  puedeCapturar: boolean;
  onGuardado: (msg: string) => void;
  vacioLote: boolean;
  onAgregar: () => void;
  onMatriz: (cat: string) => void;
}) {
  const th = "px-3 py-2.5 text-left text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400";
  return (
    <div className="mt-3 overflow-x-auto rounded-2xl border border-slate-200 bg-white">
      <table className="w-full min-w-[1100px] text-sm">
        <thead className="border-b border-slate-100 bg-slate-50/60">
          <tr>
            <th className="w-10 px-3 py-2.5">
              <input type="checkbox" checked={todos} onChange={onTodos}
                     className="h-4 w-4 rounded border-slate-300 accent-indigo-600" />
            </th>
            <th className={th}>SKU · producto</th>
            <th className={th}>Categoría ML</th>
            <th className={th}>Atributos ML</th>
            <th className={th}>Medidas empacado</th>
            <th className={th}>Cajas × piezas</th>
            <th className={th}>Estado</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {cargando && !filas.length && (
            <tr><td colSpan={8} className="px-3 py-10 text-center text-slate-400">
              <Loader2 className="mx-auto h-5 w-5 animate-spin" />
            </td></tr>
          )}
          {!cargando && !filas.length && (
            <tr><td colSpan={8} className="px-3 py-12 text-center">
              {vacioLote ? (
                <div className="text-slate-500">
                  <ClipboardCheck className="mx-auto h-8 w-8 text-slate-300" />
                  <p className="mt-2 font-semibold text-slate-700">Esta semana no tiene SKUs todavía</p>
                  <p className="text-xs">Pega la lista de ~100 SKUs a procesar y aparecen aquí.</p>
                  {puedeCapturar && (
                    <button type="button" onClick={onAgregar}
                            className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
                      <Plus className="h-4 w-4" /> Agregar SKUs
                    </button>
                  )}
                </div>
              ) : <span className="text-slate-400">Ningún SKU con ese filtro.</span>}
            </td></tr>
          )}
          {filas.map((f) => (
            <FilaTabla key={f.sku} f={f} marcada={sel.has(f.sku)} onSel={() => onSel(f.sku)}
                       abierta={abierto === f.sku} onAbrir={() => onAbrir(f.sku)}
                       puedeCapturar={puedeCapturar} onGuardado={onGuardado}
                       onMatriz={onMatriz} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilaTabla({
  f, marcada, onSel, abierta, onAbrir, puedeCapturar, onGuardado, onMatriz,
}: {
  f: FilaChecklist;
  marcada: boolean;
  onSel: () => void;
  abierta: boolean;
  onAbrir: () => void;
  puedeCapturar: boolean;
  onGuardado: (msg: string) => void;
  onMatriz: (cat: string) => void;
}) {
  const pct = f.exigidos_total ? f.exigidos_llenos / f.exigidos_total : 0;
  const med = medidas(f);
  const a = f.almacen;
  const e = ESTADO[f.estado];
  return (
    <>
      <tr className={`border-b border-slate-100 align-top transition ${marcada ? "bg-indigo-50/40" : "hover:bg-slate-50/60"}`}>
        <td className="px-3 py-3">
          <input type="checkbox" checked={marcada} onChange={onSel}
                 className="h-4 w-4 rounded border-slate-300 accent-indigo-600" />
        </td>
        <td className="max-w-[320px] px-3 py-3">
          <div className="flex items-center gap-1.5">
            <span className="font-mono text-[13px] font-bold text-slate-900">{f.sku}</span>
            {f.url_ml && (
              <a href={f.url_ml} target="_blank" rel="noreferrer" title="Ver la publicación en Mercado Libre"
                 className="text-slate-400 hover:text-indigo-600">
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
          <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{f.titulo ?? "—"}</div>
          {f.comentario && (
            <div className="mt-1 rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800">
              {f.comentario}
            </div>
          )}
        </td>
        <td className="max-w-[220px] px-3 py-3">
          {f.categoria ? (
            <button type="button" onClick={() => onMatriz(f.categoria!)}
                    className="text-left hover:underline" title="Abrir la matriz de esta categoría">
              <div className="text-xs font-semibold text-slate-700">{f.categoria_nombre ?? f.categoria}</div>
              <div className="font-mono text-[10px] text-slate-400">{f.categoria}</div>
            </button>
          ) : <span className="text-xs text-slate-400">sin categoría</span>}
        </td>
        <td className="px-3 py-3">
          {f.exigidos_total ? (
            <div className="w-44">
              <div className="flex items-baseline justify-between text-xs">
                <span className="font-bold tabular-nums text-slate-800">
                  {f.exigidos_llenos}/{f.exigidos_total}
                </span>
                <span className="text-[10px] text-slate-400">
                  +{f.opcionales_llenos} opcionales
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-full" style={{ width: `${pct * 100}%`, background: pct === 1 ? "#10b981" : ML }} />
              </div>
              {f.faltan_ml.length > 0 && (
                <div className="mt-1 line-clamp-1 text-[11px] text-slate-500">
                  Falta: {f.faltan_ml.map((x) => x.etiqueta).join(", ")}
                </div>
              )}
            </div>
          ) : <span className="text-xs text-slate-400">—</span>}
        </td>
        <td className="px-3 py-3 text-xs">
          {med
            ? <span className={f.faltan_almacen.some((k) => k !== "cajas" && k !== "piezas_por_caja") ? "text-amber-700" : "text-slate-700"}>{med}</span>
            : <span className="rounded bg-sky-50 px-1.5 py-0.5 font-semibold text-sky-700">por medir</span>}
        </td>
        <td className="px-3 py-3 text-xs">
          {a.cajas !== null || a.piezas_por_caja !== null ? (
            <div>
              <span className="font-bold tabular-nums text-slate-800">{n(a.cajas)}</span>
              <span className="text-slate-400"> × </span>
              <span className="tabular-nums text-slate-700">{n(a.piezas_por_caja)} pzs</span>
              {f.piezas_total !== null && (
                <div className="text-[11px] text-slate-400">= {n(f.piezas_total)} piezas</div>
              )}
            </div>
          ) : <span className="rounded bg-sky-50 px-1.5 py-0.5 font-semibold text-sky-700">por contar</span>}
        </td>
        <td className="px-3 py-3">
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ring-1 ${e.c}`}>
            {f.estado === "completo" && <CheckCircle2 className="h-3 w-3" />}
            {e.t}
          </span>
        </td>
        <td className="px-2 py-3">
          <button type="button" onClick={onAbrir} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  title={abierta ? "Cerrar" : "Ver qué falta y capturar"}>
            <ChevronDown className={`h-4 w-4 transition ${abierta ? "rotate-180" : ""}`} />
          </button>
        </td>
      </tr>
      {abierta && (
        <tr className="border-b border-slate-100 bg-slate-50/60">
          <td />
          <td colSpan={7} className="px-3 pb-4 pt-2">
            <Detalle f={f} puedeCapturar={puedeCapturar} onGuardado={onGuardado} />
          </td>
        </tr>
      )}
    </>
  );
}

function Detalle({
  f, puedeCapturar, onGuardado,
}: { f: FilaChecklist; puedeCapturar: boolean; onGuardado: (msg: string) => void }) {
  const campos: { k: keyof FilaChecklist["almacen"]; t: string; paso: string }[] = [
    { k: "largo_cm", t: "Largo (cm)", paso: "0.1" },
    { k: "ancho_cm", t: "Ancho (cm)", paso: "0.1" },
    { k: "alto_cm", t: "Alto (cm)", paso: "0.1" },
    { k: "peso_kg", t: "Peso (kg)", paso: "0.001" },
    { k: "cajas", t: "Cajas", paso: "1" },
    { k: "piezas_por_caja", t: "Piezas por caja", paso: "1" },
  ];
  const [val, setVal] = useState<Record<string, string>>(() =>
    Object.fromEntries(campos.map((c) => [c.k, f.almacen[c.k] === null ? "" : String(f.almacen[c.k])])));
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const guardar = async () => {
    setGuardando(true);
    setError(null);
    try {
      const r = await guardarAlmacenChecklist(f.sku, val);
      if (!r.ok) setError(r.motivo ?? "No se pudo guardar.");
      else onGuardado(`${f.sku}: ${r.guardados ?? 0} dato(s) de almacén guardados.`);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo guardar."));
    } finally {
      setGuardando(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200">
        <h4 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          Lo que falta para Mercado Libre
        </h4>
        {f.estado === "sin_categoria" ? (
          <p className="mt-2 text-xs text-slate-500">
            Sin categoría de ML no se sabe qué pide. Se asigna en Crear Productos o
            en el Publicador (la del panel manda).
          </p>
        ) : f.estado === "sin_lista" ? (
          <p className="mt-2 text-xs text-slate-500">
            Mercado Libre no contestó qué exige la categoría {f.categoria}. Recarga en
            un momento.
          </p>
        ) : f.faltan_ml.length ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {f.faltan_ml.map((x) => (
              <span key={x.campo} style={NIVEL[x.nivel].style}
                    className={`rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ${NIVEL[x.nivel].c}`}
                    title={`${x.campo} · ${NIVEL[x.nivel].t}`}>
                {x.etiqueta}
              </span>
            ))}
          </div>
        ) : (
          <p className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
            <CheckCircle2 className="h-4 w-4" /> Todo lo exigido está capturado.
          </p>
        )}
        <p className="mt-3 text-[11px] text-slate-400">
          {f.opcionales_llenos} de {f.opcionales_total} opcionales capturados. Se
          llenan en el Excel o, uno por uno, en el cajón del SKU en el Catálogo Maestro.
        </p>
      </div>

      <div className="rounded-xl bg-white p-3 ring-1 ring-sky-200">
        <h4 className="text-[11px] font-bold uppercase tracking-[0.06em] text-sky-700">
          Almacén · producto empacado
        </h4>
        <p className="mt-1 rounded-lg bg-slate-50 px-2 py-1 text-[11px] text-slate-500"
           title="costing.costos_validados: el volumen de flete y las cajas del packing list">
          <b className="text-slate-600">En sistema (no es medida):</b> {textoSistema(f.almacen.sistema)}
        </p>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {campos.map((c) => (
            <label key={c.k} className="text-[11px] font-semibold text-slate-500">
              {c.t}
              <input
                type="number" min="0" step={c.paso} value={val[c.k]}
                disabled={!puedeCapturar}
                onChange={(e) => setVal((v) => ({ ...v, [c.k]: e.target.value }))}
                className={`mt-0.5 w-full rounded-lg border px-2 py-1.5 text-sm tabular-nums text-slate-800 outline-none focus:border-sky-400 disabled:bg-slate-50 ${
                  val[c.k] ? "border-slate-200" : "border-sky-300 bg-sky-50/60"}`}
              />
            </label>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-[11px] text-slate-400">
            {f.almacen.capturado_en
              ? `Última captura ${haceCuanto(f.almacen.capturado_en)}${
                  f.almacen.capturado_por ? ` por ${f.almacen.capturado_por.split("@")[0]}` : ""}`
              : "Sin capturar todavía"}
          </span>
          {puedeCapturar && (
            <button type="button" onClick={guardar} disabled={guardando}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-700 disabled:opacity-50">
              {guardando && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Guardar
            </button>
          )}
        </div>
        {error && <p className="mt-1.5 text-xs text-rose-600">{error}</p>}
      </div>
    </div>
  );
}

/* ─────────────────────────────── modales ─────────────────────────────── */

function Modal({
  titulo, sub, onCerrar, children, ancho = "max-w-xl",
}: {
  titulo: string; sub?: string; onCerrar: () => void; children: React.ReactNode; ancho?: string;
}) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onCerrar(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onCerrar]);
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[6vh]"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCerrar(); }}>
      <div className={`w-full ${ancho} rounded-2xl bg-white shadow-2xl`}>
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-lg font-extrabold text-slate-900">{titulo}</h2>
            {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
          </div>
          <button type="button" onClick={onCerrar} className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

function ModalAgregar({
  semana, etiqueta, onCerrar, onListo,
}: {
  semana: string;
  etiqueta: string;
  onCerrar: () => void;
  /** `otra` = la semana a la que se agregó, si la lista traía la suya. */
  onListo: (msg: string, otra?: string) => void;
}) {
  const [modo, setModo] = useState<"lista" | "pegar">("lista");
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [desconocidos, setDesconocidos] = useState<string[]>([]);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [previa, setPrevia] = useState<ListaChecklist | null>(null);
  // Mismo corte que el backend: la coma separa salvo entre dos dígitos. Sin
  // lookbehind: Safari anterior a 16.4 no lo entiende y tiraba la página.
  const cuantos = texto.replace(/(\d),(?=\d)/g, "$1\u0001")
    .split(/[\s;,]+/).filter(Boolean).length;

  const leerLista = async (f: File, hoja: string | null) => {
    setArchivo(f);
    setError(null);
    setEnviando(true);
    try {
      const r = await cargarListaChecklist(f, semana, hoja, false);
      if (!r.ok) { setError(r.motivo ?? "No se pudo leer la lista."); setPrevia(null); }
      else setPrevia(r);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo leer la lista."));
    } finally {
      setEnviando(false);
    }
  };

  const agregarLista = async () => {
    if (!archivo || !previa) return;
    setEnviando(true);
    setError(null);
    try {
      const r = await cargarListaChecklist(archivo, semana, previa.elegida, true);
      if (!r.ok || !r.aplicado) { setError(r.motivo ?? "No se pudo agregar."); return; }
      onListo(`${r.etiqueta}: ${r.agregados ?? 0} agregados${
        r.ya_estaban ? `, ${r.ya_estaban} ya estaban` : ""}${
        r.desconocidos.length ? `, ${r.desconocidos.length} no existen en kubera` : ""}.`,
        r.semana);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo agregar."));
    } finally {
      setEnviando(false);
    }
  };

  const enviar = async () => {
    setEnviando(true);
    setError(null);
    try {
      const r = await agregarAlChecklist(semana, texto);
      if (!r.ok) { setError(r.motivo ?? "No se pudo."); return; }
      const d = r.desconocidos ?? [];
      const msg = `${r.agregados ?? 0} agregados${r.ya_estaban ? `, ${r.ya_estaban} ya estaban` : ""}.`;
      if (d.length) {
        setDesconocidos(d);
        setTexto("");
        setError(`${msg} Pero ${d.length} no existen en kubera y no se agregaron:`);
      } else {
        onListo(msg);
      }
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo agregar."));
    } finally {
      setEnviando(false);
    }
  };

  const pestaña = (m: "lista" | "pegar", t: string) => (
    <button type="button" onClick={() => { setModo(m); setError(null); }}
            className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
              modo === m ? "bg-indigo-600 text-white" : "text-slate-500 hover:bg-slate-100"}`}>
      {t}
    </button>
  );

  return (
    <Modal titulo="Agregar SKUs al lote"
           sub={`${etiqueta || "Semana"} · del ${dia(semana)} al ${dia(masDias(semana, 6))}`}
           onCerrar={onCerrar}>
      <div className="mb-3 flex w-fit gap-1 rounded-xl bg-slate-50 p-1 ring-1 ring-slate-200">
        {pestaña("lista", "Lista de la semana (Excel)")}
        {pestaña("pegar", "Pegar SKUs")}
      </div>

      {modo === "lista" ? (
        <>
          <p className="text-sm text-slate-600">
            El Excel de validación tal cual: una hoja <b>«Week NN»</b> con columna
            <b> SKU</b> (con o sin corchetes) y, si quieres, <b>Comentarios</b>. Lo
            demás (contenedor, tarima…) se ignora. La semana sale del nombre de la hoja.
          </p>
          <label className="mt-3 flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/60 px-4 py-5 text-center hover:border-indigo-300"
                 onDragOver={(e) => e.preventDefault()}
                 onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) void leerLista(f, null); }}>
            {enviando && !previa ? <Loader2 className="h-5 w-5 animate-spin text-indigo-500" />
              : <FileSpreadsheet className="h-5 w-5 text-slate-400" />}
            <span className="text-sm font-semibold text-slate-700">
              {archivo ? archivo.name : "Arrastra el Excel de la semana o haz clic"}
            </span>
            <input type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void leerLista(f, null); }} />
          </label>
          {previa && (
            <div className="mt-3 space-y-2">
              <div className="flex flex-wrap gap-1.5">
                {previa.hojas.map((h) => (
                  <button key={h.hoja} type="button"
                          onClick={() => archivo && void leerLista(archivo, h.hoja)}
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 transition ${
                            h.hoja === previa.elegida
                              ? "bg-indigo-600 text-white ring-indigo-600"
                              : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"}`}>
                    {h.hoja} · {h.skus}
                  </button>
                ))}
              </div>
              <div className="rounded-xl bg-indigo-50 p-3 text-sm text-indigo-900 ring-1 ring-indigo-200">
                Hoja <b>{previa.elegida}</b> → <b>{previa.etiqueta}</b> (del {dia(previa.semana)} al{" "}
                {dia(masDias(previa.semana, 6))}): <b>{previa.skus}</b> SKUs
                {previa.comentarios > 0 && <>, {previa.comentarios} con comentario</>}.
                {previa.semana !== semana && (
                  <div className="mt-1 text-xs text-indigo-700">
                    Es otra semana que la que estás viendo: al agregar se abre {previa.etiqueta}.
                  </div>
                )}
                {previa.desconocidos.length > 0 && (
                  <div className="mt-1 text-xs text-amber-800">
                    {previa.desconocidos.length} no existen en kubera y no se agregarán:{" "}
                    <span className="font-mono">{previa.desconocidos.join(", ")}</span>
                  </div>
                )}
                {!!previa.descartados?.length && (
                  <div className="mt-1 text-xs text-slate-500">
                    {previa.descartados_total ?? previa.descartados.length} celda(s) de la columna
                    SKU no tienen forma de SKU y se ignoran:{" "}
                    <span className="font-mono">{previa.descartados.slice(0, 8).join(", ")}
                      {previa.descartados.length > 8 ? "…" : ""}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-sm text-slate-600">
            Pega la columna de SKUs tal cual la copias de Excel (uno por renglón, o
            separados por coma). Los repetidos se ignoran.
          </p>
          <textarea
            value={texto} onChange={(e) => setTexto(e.target.value)} rows={10} autoFocus
            placeholder={"TEC-0370-NEG\nORG-0863-ROS\nACC-0907-MET\n…"}
            className="mt-3 w-full rounded-xl border border-slate-200 p-3 font-mono text-sm text-slate-800 outline-none focus:border-indigo-300"
          />
        </>
      )}

      {error && (
        <div className="mt-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-900 ring-1 ring-amber-200">
          {error}
          {desconocidos.length > 0 && (
            <div className="mt-1 font-mono">{desconocidos.join(", ")}</div>
          )}
        </div>
      )}
      <div className="mt-4 flex items-center justify-between">
        <span className="text-xs text-slate-400">
          {modo === "pegar" ? `${cuantos} SKU(s) en el texto` : ""}
        </span>
        <div className="flex gap-2">
          <button type="button" onClick={desconocidos.length ? () => onListo("Lote actualizado.") : onCerrar}
                  className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100">
            {desconocidos.length ? "Listo" : "Cancelar"}
          </button>
          {modo === "lista" ? (
            <button type="button" onClick={agregarLista} disabled={!previa || enviando}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {enviando && previa && <Loader2 className="h-4 w-4 animate-spin" />}
              {previa ? `Agregar ${previa.skus} a ${previa.etiqueta}` : "Agregar"}
            </button>
          ) : (
            <button type="button" onClick={enviar} disabled={!cuantos || enviando}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {enviando && <Loader2 className="h-4 w-4 animate-spin" />}
              Agregar
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

function ModalMatriz({
  categorias, inicial, puedeCapturar, onCerrar,
}: {
  categorias: TableroChecklist["categorias"];
  inicial: string | null;
  puedeCapturar: boolean;
  onCerrar: (cambio: boolean) => void;
}) {
  const [cat, setCat] = useState<string | null>(inicial ?? categorias[0]?.categoria ?? null);
  const [otra, setOtra] = useState("");
  const [m, setM] = useState<MatrizChecklist | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [marcas, setMarcas] = useState<Record<string, boolean>>({});
  const [filtro, setFiltro] = useState("");
  const [guardando, setGuardando] = useState(false);
  const huboCambio = useRef(false);

  useEffect(() => {
    if (!cat) return;
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    matrizChecklist(cat, ctrl.signal)
      .then((d) => {
        setM(d);
        setMarcas(Object.fromEntries(d.campos.map((c) => [c.campo, c.nivel === "matriz"])));
        if (!d.ok) setError(d.motivo);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer la categoría."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [cat]);

  const cambios = useMemo(() => {
    if (!m) return {};
    const c: Record<string, boolean> = {};
    for (const x of m.campos) {
      if (x.nivel === "ml") continue;
      const antes = x.nivel === "matriz";
      if (!!marcas[x.campo] !== antes) c[x.campo] = !!marcas[x.campo];
    }
    return c;
  }, [m, marcas]);
  const nCambios = Object.keys(cambios).length;

  const guardar = async () => {
    if (!cat || !nCambios) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await guardarMatrizChecklist(cat, cambios);
      if (!r.ok) { setError(r.motivo ?? "No se pudo guardar."); return; }
      huboCambio.current = true;
      const d = await matrizChecklist(cat);
      setM(d);
      setMarcas(Object.fromEntries(d.campos.map((c) => [c.campo, c.nivel === "matriz"])));
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo guardar."));
    } finally {
      setGuardando(false);
    }
  };

  const visibles = (m?.campos ?? []).filter((x) => {
    const q = filtro.trim().toLowerCase();
    return !q || x.etiqueta.toLowerCase().includes(q) || x.campo.toLowerCase().includes(q);
  });
  const grupos: { t: string; items: CampoChecklist[] }[] = [
    { t: "Obligatorios de Mercado Libre", items: visibles.filter((x) => x.nivel === "ml") },
    { t: "Automáticos · los llena el publicador", items: visibles.filter((x) => x.nivel === "auto") },
    { t: "Opcionales · del producto", items: visibles.filter((x) => x.nivel !== "ml" && x.nivel !== "auto" && x.jerarquia !== "ITEM") },
    { t: "Opcionales · facturación (clave SAT, IVA…)", items: visibles.filter((x) => x.nivel !== "ml" && x.nivel !== "auto" && x.jerarquia === "ITEM") },
  ];

  return (
    <Modal titulo="Matriz de obligatorios"
           sub="Por categoría de Mercado Libre: qué opcionales se vuelven obligatorios para almacén. Los de ML no se pueden bajar."
           onCerrar={() => onCerrar(huboCambio.current)} ancho="max-w-5xl">
      <div className="grid gap-4 md:grid-cols-[260px_1fr]">
        <aside>
          <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
            Categorías del lote
          </div>
          <div className="mt-2 max-h-[52vh] space-y-1 overflow-y-auto pr-1">
            {categorias.length === 0 && (
              <p className="text-xs text-slate-400">El lote no tiene categorías todavía.</p>
            )}
            {categorias.map((c) => (
              <button key={c.categoria} type="button" onClick={() => setCat(c.categoria)}
                      className={`w-full rounded-lg px-2.5 py-2 text-left transition ${
                        cat === c.categoria ? "bg-indigo-50 ring-1 ring-indigo-200" : "hover:bg-slate-50"}`}>
                <div className="text-xs font-semibold text-slate-800">{c.nombre ?? c.categoria}</div>
                <div className="mt-0.5 text-[10px] text-slate-400">
                  <span className="font-mono">{c.categoria}</span> · {c.skus} SKU(s) · {c.obligatorios_ml} ML
                  {c.promovidos > 0 && <span className="font-semibold text-orange-600"> · +{c.promovidos} matriz</span>}
                </div>
              </button>
            ))}
          </div>
          <div className="mt-3 flex gap-1.5">
            <input value={otra} onChange={(e) => setOtra(e.target.value.toUpperCase())}
                   placeholder="Otra: MLM1234"
                   className="w-full rounded-lg border border-slate-200 px-2 py-1.5 font-mono text-xs outline-none focus:border-indigo-300" />
            <button type="button" disabled={!/^MLM\d+$/.test(otra)} onClick={() => setCat(otra)}
                    className="rounded-lg bg-slate-800 px-2.5 text-xs font-semibold text-white disabled:opacity-40">
              Ver
            </button>
          </div>
        </aside>

        <section className="min-w-0">
          {!cat ? (
            <p className="text-sm text-slate-400">Elige una categoría.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-sm font-bold text-slate-900">{m?.nombre ?? cat}</div>
                  <div className="text-[11px] text-slate-400">{m?.ruta ?? cat}</div>
                </div>
                <input value={filtro} onChange={(e) => setFiltro(e.target.value)} placeholder="Filtrar atributos"
                       className="w-48 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-indigo-300" />
              </div>
              {error && (
                <p className="mt-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 ring-1 ring-amber-200">{error}</p>
              )}
              <div className="mt-3 max-h-[52vh] overflow-y-auto rounded-xl ring-1 ring-slate-200">
                {cargando ? (
                  <div className="p-8 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-slate-400" /></div>
                ) : grupos.map((g) => g.items.length > 0 && (
                  <div key={g.t}>
                    <div className="sticky top-0 z-10 bg-slate-50 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.06em] text-slate-500">
                      {g.t} · {g.items.length}
                    </div>
                    {g.items.map((x) => {
                      // Los de ML y los automáticos no se tocan: los primeros
                      // los exige el canal, los segundos los llena el publicador.
                      const esMl = x.nivel === "ml" || x.nivel === "auto";
                      const on = esMl || !!marcas[x.campo];
                      return (
                        <label key={x.campo}
                               className={`flex cursor-pointer items-center gap-3 border-t border-slate-100 px-3 py-2 ${
                                 esMl ? "cursor-default" : "hover:bg-slate-50"}`}>
                          <input type="checkbox" checked={on} disabled={esMl || !puedeCapturar}
                                 onChange={(e) => setMarcas((mm) => ({ ...mm, [x.campo]: e.target.checked }))}
                                 className="h-4 w-4 rounded accent-orange-500 disabled:opacity-60" />
                          <div className="min-w-0 flex-1">
                            <div className="text-sm font-semibold text-slate-800">{x.etiqueta}</div>
                            <div className="text-[10px] text-slate-400">
                              <span className="font-mono">{x.campo}</span>
                              {x.tipo && <> · {x.tipo}</>}
                              {x.unidades.length > 0 && <> · {x.unidades.slice(0, 4).join("/")}</>}
                              {x.valores.length > 0 && <> · {x.valores.length} valores sugeridos</>}
                            </div>
                          </div>
                          {x.nivel === "auto" ? (
                            <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700 ring-1 ring-emerald-200"
                                  title="El publicador lo llena solo si se deja vacío">
                              Automático{x.por_omision ? `: ${x.por_omision}` : ""}
                            </span>
                          ) : esMl ? (
                            <span style={{ background: ML }} className="rounded-md px-2 py-0.5 text-[10px] font-bold text-[#2d3277]">
                              Obligatorio ML
                            </span>
                          ) : on ? (
                            <span className="rounded-md bg-orange-200 px-2 py-0.5 text-[10px] font-bold text-orange-900">
                              Obligatorio (matriz)
                            </span>
                          ) : (
                            <span className="text-[10px] font-semibold text-slate-400">Opcional</span>
                          )}
                        </label>
                      );
                    })}
                  </div>
                ))}
              </div>
              <div className="mt-3 flex items-center justify-between">
                <span className="text-xs text-slate-500">
                  {nCambios ? `${nCambios} cambio(s) sin guardar` : "Sin cambios"}
                </span>
                {puedeCapturar && (
                  <button type="button" onClick={guardar} disabled={!nCambios || guardando || !m?.ok}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50">
                    {guardando && <Loader2 className="h-4 w-4 animate-spin" />}
                    Guardar matriz
                  </button>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </Modal>
  );
}

function ModalCargar({ onCerrar }: { onCerrar: (cambio: boolean) => void }) {
  const [archivo, setArchivo] = useState<File | null>(null);
  const [previa, setPrevia] = useState<ImportacionChecklist | null>(null);
  const [trabajando, setTrabajando] = useState<null | "leyendo" | "guardando">(null);
  const [error, setError] = useState<string | null>(null);
  const aplicado = !!previa?.aplicado;

  const leer = async (f: File) => {
    setArchivo(f);
    setPrevia(null);
    setError(null);
    setTrabajando("leyendo");
    try {
      const r = await importarChecklist(f, false);
      if (!r.ok) setError(r.motivo ?? "No se pudo leer.");
      else setPrevia(r);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo leer el archivo."));
    } finally {
      setTrabajando(null);
    }
  };

  const aplicar = async () => {
    if (!archivo) return;
    setTrabajando("guardando");
    setError(null);
    try {
      const r = await importarChecklist(archivo, true);
      if (!r.ok) setError(r.motivo ?? "No se pudo guardar.");
      else setPrevia(r);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo guardar."));
    } finally {
      setTrabajando(null);
    }
  };

  const ml = previa?.cambios.filter((c) => c.tipo === "ml").length ?? 0;
  const alm = previa?.cambios.filter((c) => c.tipo === "almacen").length ?? 0;

  return (
    <Modal titulo="Cargar Excel / CSV"
           sub="Primero se enseña qué va a cambiar; nada se guarda hasta que lo confirmes."
           onCerrar={() => onCerrar(aplicado)} ancho="max-w-4xl">
      {!aplicado && (
        <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/60 px-4 py-6 text-center hover:border-indigo-300"
               onDragOver={(e) => e.preventDefault()}
               onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) void leer(f); }}>
          {trabajando === "leyendo"
            ? <Loader2 className="h-6 w-6 animate-spin text-indigo-500" />
            : <Upload className="h-6 w-6 text-slate-400" />}
          <span className="text-sm font-semibold text-slate-700">
            {archivo ? archivo.name : "Arrastra aquí el Excel (.xlsx) o CSV, o haz clic"}
          </span>
          <span className="text-xs text-slate-400">El que descargaste de esta pestaña, ya llenado.</span>
          <input type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) void leer(f); }} />
        </label>
      )}

      {error && (
        <p className="mt-3 rounded-lg bg-rose-50 p-2.5 text-sm text-rose-700 ring-1 ring-rose-200">{error}</p>
      )}

      {previa && (
        <div className="mt-4 space-y-3">
          {aplicado ? (
            <div className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900 ring-1 ring-emerald-200">
              <div className="flex items-center gap-1.5 font-bold">
                <CheckCircle2 className="h-4 w-4" /> Guardado
              </div>
              <div className="mt-1">
                {previa.guardados?.ml ?? 0} SKU(s) con atributos de ML y {previa.guardados?.almacen ?? 0} con
                datos de almacén.
              </div>
              {!!previa.guardados?.fallidos.length && (
                <ul className="mt-1.5 list-disc pl-5 text-rose-700">
                  {previa.guardados.fallidos.map((x, i) => <li key={i}><b>{x.sku}</b>: {x.motivo}</li>)}
                </ul>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Cifra t="Cambios" v={previa.cambios.length} p={`en ${previa.skus} SKU(s)`} />
              <Cifra t="Atributos ML" v={ml} p="al Publicador" tono="ml" />
              <Cifra t="Almacén" v={alm} p="medidas, cajas, piezas" tono="alm" />
              <Cifra t="Iguales" v={previa.sin_cambios} p="ya estaban así" />
            </div>
          )}

          {previa.errores.length > 0 && (
            <Lista titulo={`${previa.errores.length} error(es) — esas celdas NO se guardan`} tono="rose"
                   items={previa.errores} />
          )}
          {previa.avisos.length > 0 && (
            <Lista titulo={`${previa.avisos.length} aviso(s)`} tono="amber" items={previa.avisos} />
          )}

          {previa.cambios.length > 0 && (
            <div className="max-h-[36vh] overflow-y-auto rounded-xl ring-1 ring-slate-200">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">
                  <tr>
                    <th className="px-3 py-2">SKU</th>
                    <th className="px-3 py-2">Campo</th>
                    <th className="px-3 py-2">Antes</th>
                    <th className="px-3 py-2">Queda</th>
                  </tr>
                </thead>
                <tbody>
                  {previa.cambios.slice(0, 400).map((c, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="px-3 py-1.5 font-mono font-semibold text-slate-800">{c.sku}</td>
                      <td className="px-3 py-1.5">
                        <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${c.tipo === "ml" ? "" : "bg-sky-400"}`}
                              style={c.tipo === "ml" ? { background: ML } : undefined} />
                        {c.etiqueta}
                      </td>
                      <td className="px-3 py-1.5 text-slate-400 line-through decoration-slate-300">{c.antes || "—"}</td>
                      <td className="px-3 py-1.5 font-semibold text-slate-800">{c.despues}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {previa.cambios.length > 400 && (
                <p className="px-3 py-2 text-[11px] text-slate-400">…y {previa.cambios.length - 400} más.</p>
              )}
            </div>
          )}

          <div className="flex items-center justify-end gap-2">
            <button type="button" onClick={() => onCerrar(aplicado)}
                    className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100">
              {aplicado ? "Cerrar" : "Cancelar"}
            </button>
            {!aplicado && (
              <button type="button" onClick={aplicar}
                      disabled={!previa.cambios.length || trabajando !== null}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
                {trabajando === "guardando" && <Loader2 className="h-4 w-4 animate-spin" />}
                Guardar {previa.cambios.length} cambio(s)
              </button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function Cifra({ t, v, p, tono }: { t: string; v: number; p: string; tono?: "ml" | "alm" }) {
  const marco = tono === "ml" ? "border-[#e6cf00] bg-[#fffbd6]"
    : tono === "alm" ? "border-sky-200 bg-sky-50" : "border-slate-200 bg-white";
  return (
    <div className={`rounded-xl border p-3 ${marco}`}>
      <div className="text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">{t}</div>
      <div className="mt-1 text-2xl font-extrabold tabular-nums text-slate-900">{v}</div>
      <div className="text-[11px] text-slate-500">{p}</div>
    </div>
  );
}

function Lista({
  titulo, tono, items,
}: { titulo: string; tono: "rose" | "amber"; items: ImportacionChecklist["errores"] }) {
  const c = tono === "rose"
    ? "bg-rose-50 text-rose-800 ring-rose-200" : "bg-amber-50 text-amber-900 ring-amber-200";
  return (
    <details className={`rounded-xl p-3 text-xs ring-1 ${c}`} open={tono === "rose"}>
      <summary className="cursor-pointer font-bold">{titulo}</summary>
      <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-y-auto">
        {items.slice(0, 200).map((x, i) => (
          <li key={i}>
            {x.sku && <b className="font-mono">{x.sku}</b>}
            {x.hoja && <span className="opacity-70"> · {x.hoja}{x.fila ? ` renglón ${x.fila}` : ""}</span>}
            {" — "}{x.motivo}
          </li>
        ))}
      </ul>
    </details>
  );
}
