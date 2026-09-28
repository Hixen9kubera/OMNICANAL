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
 *
 * v0.581 (Brandon, 28-sep): la fila se abre con un clic en cualquier parte;
 * los atributos se llenan AQUÍ (con las sugerencias de ML y Ferrahome para la
 * marca); la matriz es un apartado por categoría en la misma página, y el
 * selector de semanas marca con palomita las que ya tienen SKUs. El lote de la
 * semana se ve también en el Catálogo Maestro (enlace junto a la semana).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowUpRight, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  ClipboardCheck, Download, ExternalLink, FileSpreadsheet, Loader2, Plus,
  RefreshCw, Ruler, Search, SlidersHorizontal, Trash2, Upload, X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { CamposAtributos, type CampoEditable } from "@/components/EditorAtributos";
import InventarioPestanas from "@/components/InventarioPestanas";
import SelectorSemana from "@/components/SelectorSemana";
import {
  agregarAlChecklist, cargarListaChecklist, descargarChecklist, detalleChecklist,
  guardarAlmacenChecklist, guardarAtributosChecklist, guardarMatrizChecklist,
  importarChecklist, matrizChecklist, mensajeDeError, quitarDelChecklist, tableroChecklist,
} from "@/lib/api";
import { quienSoy } from "@/lib/sesion";
import type {
  CampoChecklist, CampoDetalleChecklist, DetalleChecklist, EstadoChecklist, FilaChecklist,
  FilaEvaluadaChecklist, ImportacionChecklist, ListaChecklist, MatrizChecklist,
  NivelChecklist, SistemaChecklist, TableroChecklist,
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

/** El lunes de la semana de HOY en CDMX (la del equipo). Solo para pintar el
 *  selector mientras el tablero no ha contestado: la semana buena la dice el
 *  backend. */
function lunesHoy(): string {
  const hoy = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Mexico_City" })
    .format(new Date());
  const d = new Date(`${hoy}T12:00:00Z`);
  return masDias(hoy, -((d.getUTCDay() + 6) % 7));
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
  // La semana PEDIDA a la que pertenecen `datos` (undefined = «esta semana»).
  const [datosDe, setDatosDe] = useState<string | undefined>(undefined);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [busqueda, setBusqueda] = useState("");
  const [abierto, setAbierto] = useState<string | null>(null);
  const [modal, setModal] = useState<null | "agregar" | "cargar" | "matriz">(null);
  // Las categorías abiertas en el apartado de la matriz.
  const [matrizAbiertas, setMatrizAbiertas] = useState<Set<string>>(new Set());
  // La categoría a la que llevó el clic en un renglón: el popup se abre ahí.
  const [matrizFoco, setMatrizFoco] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [bajando, setBajando] = useState<null | "excel" | "csv">(null);
  // Sube con cada lectura buena del tablero (tras guardar la matriz, cargar un
  // Excel o las medidas): el detalle abierto se vuelve a leer con ella.
  const [version, setVersion] = useState(0);
  // El rol solo esconde botones: la autoridad es el RBAC del backend.
  const [puedeCapturar, setPuedeCapturar] = useState(true);

  useEffect(() => {
    let vivo = true;
    void quienSoy().then((u) => {
      if (vivo && u?.autenticado) setPuedeCapturar(u.rol !== "lectura");
    }).catch(() => {});
    return () => { vivo = false; };
  }, []);

  // La lectura EN VUELO. Una nueva cancela la anterior (y su reintento): si
  // no, una recarga lenta que llega después de cambiar de semana pintaría la
  // semana vieja encima de la nueva.
  const vuelo = useRef<{ ctrl: AbortController; espera?: ReturnType<typeof setTimeout> } | null>(null);

  const cargar = useCallback((fresco = false) => {
    if (vuelo.current) {
      vuelo.current.ctrl.abort();
      if (vuelo.current.espera) clearTimeout(vuelo.current.espera);
    }
    const ctrl = new AbortController();
    const este: { ctrl: AbortController; espera?: ReturnType<typeof setTimeout> } = { ctrl };
    vuelo.current = este;
    const pedir = (intento: number) => {
      setCargando(true);
      setError(null);
      tableroChecklist(semana, ctrl.signal, fresco)
        .then((d) => {
          setDatos(d);
          setDatosDe(semana);
          setVersion((v) => v + 1);
          // La selección solo vale dentro del lote que se ve.
          setSel((s) => new Set([...s].filter((k) => d.filas.some((f) => f.sku === k))));
          setCargando(false);
        })
        .catch((e: unknown) => {
          if ((e as { name?: string })?.name === "AbortError") return;
          // UN reintento, sin avisar: lo típico es el backend reiniciando tras
          // un deploy (502) o una lectura que se atoró unos segundos.
          if (intento === 0) {
            este.espera = setTimeout(() => { if (!ctrl.signal.aborted) pedir(1); }, 4000);
            return;
          }
          setError(mensajeDeError(e, "El servidor no contestó (puede estar reiniciando tras un deploy)."));
          setCargando(false);
        });
    };
    pedir(0);
    return () => {
      ctrl.abort();
      if (este.espera) clearTimeout(este.espera);
      if (vuelo.current === este) vuelo.current = null;
    };
  }, [semana]);

  useEffect(() => cargar(), [cargar]);

  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(() => setAviso(null), 6000);
    return () => clearTimeout(t);
  }, [aviso]);

  // Lo que se enseña es de la semana PEDIDA: si la nueva no cargó, no se
  // pintan los renglones de la anterior como si fueran de esta.
  const vigente = datos && datosDe === semana ? datos : null;

  const filas = useMemo(() => {
    let items = vigente?.filas ?? [];
    const pruebas: Record<Filtro, (f: FilaChecklist) => boolean> = {
      todos: () => true,
      pendientes: (f) => f.estado !== "completo",
      completos: (f) => f.estado === "completo",
      faltan_ml: (f) => f.faltan_ml.length > 0,
      faltan_almacen: (f) => f.faltan_almacen.length > 0,
      sin_categoria: (f) => f.estado === "sin_categoria" || f.estado === "sin_lista",
    };
    items = items.filter((f) => f.sku === abierto || pruebas[filtro](f));
    const q = busqueda.trim().toLowerCase();
    if (q) {
      items = items.filter((f) => f.sku === abierto || f.sku.toLowerCase().includes(q)
        || (f.titulo ?? "").toLowerCase().includes(q)
        || (f.categoria_nombre ?? "").toLowerCase().includes(q));
    }
    return items;
  }, [vigente, filtro, busqueda, abierto]);

  const semanaVista = vigente?.semana ?? semana ?? lunesHoy();
  const bloqueado = !!datos?.falta_migracion;
  const elegidos = [...sel];

  // El Excel del LOTE COMPLETO no depende de la selección ni del filtro de
  // arriba (Brandon: «con TODOS los SKUs, sin tener que seleccionarlos»). El de
  // la selección es aparte y solo aparece si hay algo seleccionado.
  const bajar = async (formato: "excel" | "csv", soloSeleccion = false) => {
    if (!semanaVista) return;
    setBajando(formato);
    try {
      await descargarChecklist(formato, semanaVista, soloSeleccion ? elegidos : []);
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

  // Guardar atributos devuelve la fila re-evaluada: se parcha esa sola y se
  // recalculan los totales, sin volver a leer todo el lote (~4 s).
  const actualizarFila = (fila: FilaEvaluadaChecklist) => {
    setDatos((d) => {
      if (!d) return d;
      const filas_ = d.filas.map((x) => (x.sku === fila.sku ? { ...x, ...fila } : x));
      return { ...d, filas: filas_, resumen: resumenDe(filas_) };
    });
  };

  // La matriz es un POPUP (Brandon, 28-sep: «mejor si manda la matriz por
  // categoría como un POP»). Desde la categoría de un renglón se abre ya
  // desplegada en ESA categoría.
  const abrirMatriz = (cat?: string) => {
    if (cat) setMatrizAbiertas((m) => new Set(m).add(cat));
    setMatrizFoco(cat ?? null);
    setModal("matriz");
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
        <Banner datos={vigente} cargando={cargando} onRecargar={() => cargar(true)} />

        {/* Si falló con datos de ESTA semana en pantalla (una recarga), se
            quedan y se avisa arriba; sin datos, el aviso va en la tabla. */}
        {error && vigente && (
          <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>{error} Se muestra lo último que se leyó.</span>
            <button type="button" onClick={() => cargar()} disabled={cargando}
                    className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50 disabled:opacity-50">
              <RefreshCw className={`h-3.5 w-3.5 ${cargando ? "animate-spin" : ""}`} /> Reintentar
            </button>
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
          semana={semanaVista} etiqueta={vigente?.etiqueta ?? ""}
          hayLote={!!vigente?.filas.length}
          onCambiar={(s) => { setSemana(s); setSel(new Set()); setAbierto(null); }}
        />

        {vigente && <Kpis datos={vigente} filtro={filtro} setFiltro={setFiltro} />}
        {vigente?.publicados && <Publicado p={vigente.publicados} />}

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
          <Boton icono={SlidersHorizontal} deshabilitado={bloqueado || !vigente?.categorias.length}
                 titulo="Por categoría del lote: qué opcionales se vuelven obligatorios"
                 onClick={() => abrirMatriz()}>
            Matriz por categoría
          </Boton>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Boton icono={bajando === "excel" ? Loader2 : FileSpreadsheet} primario
                   girar={bajando === "excel"}
                   deshabilitado={bloqueado || !vigente?.filas.length || !!bajando}
                   titulo="Todos los SKUs del lote, sin importar la selección ni el filtro"
                   onClick={() => bajar("excel")}>
              Excel del lote{vigente?.filas.length ? ` (${vigente.filas.length})` : ""}
            </Boton>
            <Boton icono={bajando === "csv" ? Loader2 : Download} girar={bajando === "csv"}
                   deshabilitado={bloqueado || !vigente?.filas.length || !!bajando}
                   onClick={() => bajar("csv")}
                   titulo="Todo el lote en formato largo: un renglón por SKU y campo">
              CSV
            </Boton>
            {elegidos.length > 0 && (
              <Boton icono={FileSpreadsheet} deshabilitado={bloqueado || !!bajando}
                     titulo="Solo los SKUs seleccionados"
                     onClick={() => bajar("excel", true)}>
                Excel de {elegidos.length} seleccionado{elegidos.length === 1 ? "" : "s"}
              </Boton>
            )}
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
          onFila={actualizarFila}
          onAviso={setAviso}
          version={version}
          vacioLote={!!vigente && !vigente.filas.length}
          errorCarga={vigente ? null : error}
          onReintentar={() => cargar()}
          onAgregar={() => setModal("agregar")}
          onMatriz={abrirMatriz}
        />


        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          Solo kubera y la API de Mercado Libre — nada de WordPress. Los
          atributos se guardan donde los lee el Publicador; los que ya trae la
          publicación viva cuentan como llenos, pero no se copian solos. Las
          medidas, cajas y piezas alimentan el lado «Bodega» del cotejo de cajas
          del Catálogo Maestro. Una celda vacía en el Excel no borra nada.
        </p>
      </main>

      {modal === "agregar" && semanaVista && (
        <ModalAgregar semana={semanaVista} etiqueta={vigente?.etiqueta ?? ""}
                      cargadas={Object.fromEntries((datos?.semanas ?? []).map((s) => [s.semana, s.skus]))}
                      onCerrar={() => setModal(null)}
                      onListo={(msg, otra) => {
                        setModal(null); setAviso(msg);
                        // La lista trae su propia semana («Week 39»): se salta a ella.
                        if (otra && otra !== semanaVista) { setSemana(otra); setSel(new Set()); }
                        else cargar();
                      }} />
      )}
      {modal === "cargar" && (
        <ModalCargar onCerrar={(cambio) => { setModal(null); if (cambio) cargar(); }} />
      )}
      {modal === "matriz" && vigente && (
        <ModalMatriz
          categorias={vigente.categorias} abiertas={matrizAbiertas} foco={matrizFoco}
          onAlternar={(cat) => setMatrizAbiertas((m) => {
            const nuevo = new Set(m);
            if (nuevo.has(cat)) nuevo.delete(cat); else nuevo.add(cat);
            return nuevo;
          })}
          puedeCapturar={puedeCapturar && !bloqueado}
          // Guardar recalcula el lote (faltantes, exigidos) sin cerrar el
          // popup: se pueden seguir marcando otras categorías.
          onGuardada={(msg) => { setAviso(msg); cargar(); }}
          onCerrar={() => { setModal(null); setMatrizFoco(null); }}
        />
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
            title="Volver a leer kubera y Mercado Libre (también las publicaciones)"
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
  semana, etiqueta, hayLote, onCambiar,
}: {
  semana: string;
  etiqueta: string;
  hayLote: boolean;
  onCambiar: (s: string | undefined) => void;
}) {
  if (!semana) return null;
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
      {/* Por número de semana, con palomita en las que ya tienen SKUs. */}
      <SelectorSemana valor={semana} onElegir={(s) => onCambiar(s)} vacio="Elegir semana" />
      {hayLote && (
        <a href={`/inventario?semana=${semana}`}
           className="ml-auto inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-indigo-600 hover:bg-indigo-50"
           title="Los mismos SKUs en el Catálogo Maestro: existencias, ubicación y cotejo de cajas">
          Ver en Catálogo Maestro <ArrowUpRight className="h-3.5 w-3.5" />
        </a>
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

/** Cuánto de lo exigido ya lo trae la publicación viva de ML, y si ML contestó. */
function Publicado({ p }: { p: NonNullable<TableroChecklist["publicados"]> }) {
  if (!p.publicaciones && !p.error) return null;
  return (
    <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
      <span className="inline-block h-2.5 w-2.5 rounded-sm bg-emerald-200 ring-1 ring-emerald-300" />
      {p.vivas > 0 && (
        <span>
          <b className="text-slate-700">{p.vivas}</b> publicaciones vivas de Mercado Libre
          leídas{p.publicaciones > p.vivas ? ` (de ${p.publicaciones} en kubera)` : ""}: lo que
          ya traen cuenta como lleno y sale en verde en el Excel.
        </span>
      )}
      {p.error && <span className="font-semibold text-amber-700">{p.error}</span>}
    </p>
  );
}

/* ─────────────────────────────── la tabla ─────────────────────────────── */

function Tabla({
  filas, cargando, sel, onSel, todos, onTodos, abierto, onAbrir, puedeCapturar,
  onGuardado, onFila, onAviso, version, vacioLote, errorCarga, onReintentar, onAgregar, onMatriz,
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
  onFila: (fila: FilaEvaluadaChecklist) => void;
  onAviso: (msg: string) => void;
  version: number;
  vacioLote: boolean;
  /** No se pudo leer la semana: se dice eso, no «no tiene SKUs». */
  errorCarga: string | null;
  onReintentar: () => void;
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
              {errorCarga ? (
                <div className="text-slate-500">
                  <AlertTriangle className="mx-auto h-8 w-8 text-rose-300" />
                  <p className="mt-2 font-semibold text-slate-700">No se pudo leer el checklist</p>
                  <p className="mx-auto max-w-md text-xs">
                    {errorCarga} Lo capturado no se perdió: es la lectura la que falló.
                  </p>
                  <button type="button" onClick={onReintentar}
                          className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
                    <RefreshCw className="h-4 w-4" /> Reintentar
                  </button>
                </div>
              ) : vacioLote ? (
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
                       onFila={onFila} onAviso={onAviso} version={version}
                       onMatriz={onMatriz} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilaTabla({
  f, marcada, onSel, abierta, onAbrir, puedeCapturar, onGuardado, onFila, onAviso, version,
  onMatriz,
}: {
  f: FilaChecklist;
  marcada: boolean;
  onSel: () => void;
  abierta: boolean;
  onAbrir: () => void;
  puedeCapturar: boolean;
  onGuardado: (msg: string) => void;
  onFila: (fila: FilaEvaluadaChecklist) => void;
  onAviso: (msg: string) => void;
  version: number;
  onMatriz: (cat: string) => void;
}) {
  const pct = f.exigidos_total ? f.exigidos_llenos / f.exigidos_total : 0;
  const med = medidas(f);
  const a = f.almacen;
  const e = ESTADO[f.estado];
  return (
    <>
      {/* La fila entera abre el detalle (Brandon: «en todo el DIV, no solo con
          el botón de desplegar»). Lo que ya es un control —casilla, enlace a
          ML, categoría, flecha— sigue haciendo lo suyo; y si alguien está
          seleccionando texto (para copiar el SKU) no se abre. */}
      <tr tabIndex={0} aria-expanded={abierta}
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("a,button,input,select,textarea,label")) return;
            if (window.getSelection()?.toString()) return;
            onAbrir();
          }}
          onKeyDown={(e) => {
            if (e.target !== e.currentTarget) return;
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onAbrir(); }
          }}
          className={`cursor-pointer border-b border-slate-100 align-top outline-none transition focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-300 ${
            abierta ? "bg-indigo-50/60" : marcada ? "bg-indigo-50/40" : "hover:bg-slate-50/60"}`}>
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
                    className="text-left hover:underline" title="Ir a la matriz de esta categoría">
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
              {f.exigidos_publicados > 0 && (
                <div className="mt-1 text-[11px] font-medium text-emerald-700"
                     title="Los trae hoy la publicación viva de Mercado Libre; en kubera no están capturados">
                  {f.exigidos_publicados} de la publicación ML
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
          <button type="button" onClick={onAbrir} aria-expanded={abierta}
                  className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  title={abierta ? "Cerrar" : "Ver qué falta y capturar"}>
            <ChevronDown className={`h-4 w-4 transition ${abierta ? "rotate-180" : ""}`} />
          </button>
        </td>
      </tr>
      {abierta && (
        <tr className="border-b border-slate-100 bg-slate-50/60">
          <td />
          <td colSpan={7} className="px-3 pb-4 pt-2">
            <Detalle f={f} puedeCapturar={puedeCapturar} onGuardado={onGuardado}
                     onFila={onFila} onAviso={onAviso} version={version} />
          </td>
        </tr>
      )}
    </>
  );
}

function Detalle({
  f, puedeCapturar, onGuardado, onFila, onAviso, version,
}: {
  f: FilaChecklist;
  puedeCapturar: boolean;
  onGuardado: (msg: string) => void;
  onFila: (fila: FilaEvaluadaChecklist) => void;
  onAviso: (msg: string) => void;
  version: number;
}) {
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
      <AtributosML f={f} puedeCapturar={puedeCapturar} onFila={onFila} onAviso={onAviso}
                   version={version} />

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

/** Los totales de arriba, como los calcula el backend (`_resumen`), para
 *  recalcularlos al parchar UNA fila sin volver a leer el lote. */
function resumenDe(filas: FilaChecklist[]): TableroChecklist["resumen"] {
  return {
    total: filas.length,
    completos: filas.filter((f) => f.estado === "completo").length,
    incompletos: filas.filter((f) => f.estado === "incompleto").length,
    sin_categoria: filas.filter((f) => f.estado === "sin_categoria" || f.estado === "sin_lista").length,
    faltan_ml: filas.filter((f) => f.faltan_ml.length > 0).length,
    faltan_almacen: filas.filter((f) => f.faltan_almacen.length > 0).length,
  };
}

/** Un campo del detalle, en la forma del editor compartido. «Obligatorio» =
 *  lo que EXIGE el checklist (ML + matriz); lo automático no se exige. */
function editable(c: CampoDetalleChecklist): CampoEditable {
  return {
    campo: c.campo, etiqueta: c.etiqueta, tipo: c.tipo, valores: c.valores,
    unidades: c.unidades, unidad_default: c.unidad_default, por_omision: c.por_omision,
    publicado: c.publicado, obligatorio: c.exigido,
  };
}

/**
 * Los atributos de Mercado Libre del SKU, EDITABLES (Brandon, 28-sep: «que sea
 * editable como lo teníamos en el Catálogo Maestro»). Mismas reglas que el
 * Excel: se normaliza en el backend, un campo vacío no borra nada, y lo que ya
 * trae la publicación viva se enseña en verde pero no se copia hasta que
 * alguien lo guarda.
 */
function AtributosML({
  f, puedeCapturar, onFila, onAviso, version,
}: {
  f: FilaChecklist;
  puedeCapturar: boolean;
  onFila: (fila: FilaEvaluadaChecklist) => void;
  onAviso: (msg: string) => void;
  /** Sube con cada lectura del tablero: el detalle se re-lee con ella. */
  version: number;
}) {
  const [d, setD] = useState<DetalleChecklist | null>(null);
  const [valores, setValores] = useState<Record<string, string>>({});
  // Lo que había en kubera la última vez que se leyó: al volver a leer se
  // conserva lo que la persona ya había cambiado y no guardó.
  const baseRef = useRef<Record<string, string> | null>(null);
  const [reintento, setReintento] = useState(0);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errores, setErrores] = useState<Record<string, string>>({});
  const [avisos, setAvisos] = useState<string[]>([]);
  const [guardando, setGuardando] = useState(false);
  const [verOpcionales, setVerOpcionales] = useState(false);
  const [verFiscales, setVerFiscales] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    detalleChecklist(f.sku, ctrl.signal)
      .then((r) => {
        const campos = r.campos ?? [];
        setD({ ...r, campos });
        const base = baseRef.current;
        setValores((antes) => Object.fromEntries(campos.map((c) => {
          const local = antes[c.campo];
          // Lo editado y no guardado se queda; lo demás toma lo de kubera.
          const editado = base !== null && local !== undefined && local !== (base[c.campo] ?? "");
          return [c.campo, editado ? local : c.valor];
        })));
        baseRef.current = Object.fromEntries(campos.map((c) => [c.campo, c.valor]));
        if (!r.ok || r.motivo) setError(r.motivo ?? "No se pudieron leer los atributos.");
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudieron leer los atributos."));
      })
      .finally(() => { if (!ctrl.signal.aborted) setCargando(false); });
    return () => ctrl.abort();
  // Al abrir otro SKU, al reintentar y con cada lectura del tablero (la matriz
  // pudo subir un campo a exigido, o un Excel traer valores nuevos).
  }, [f.sku, version, reintento]);

  // Lo que se manda: solo lo que cambió y NO está vacío (vacío no borra).
  const cambios = useMemo(() => {
    const c: Record<string, string> = {};
    for (const x of d?.campos ?? []) {
      const v = (valores[x.campo] ?? "").trim();
      if (v && v !== (x.valor ?? "").trim()) c[x.campo] = v;
    }
    return c;
  }, [d, valores]);
  const nCambios = Object.keys(cambios).length;
  const vaciados = (d?.campos ?? []).filter((x) => x.valor && !(valores[x.campo] ?? "").trim()).length;

  const guardar = async () => {
    if (!d || !nCambios) return;
    setGuardando(true);
    setErrores({});
    setAvisos([]);
    try {
      const r = await guardarAtributosChecklist(d.sku, cambios);
      if (!r.ok) {
        setErrores(Object.fromEntries((r.errores ?? []).map((e) => [e.campo, e.motivo])));
        setAvisos([r.motivo ?? "No se pudo guardar."]);
        return;
      }
      // Lo guardado es la nueva base (normalizado por el backend: «12» → «12 cm»
      // se ve al volver a abrir; aquí se conserva lo escrito).
      setD({ ...d, campos: d.campos.map((c) => (c.campo in cambios ? { ...c, valor: cambios[c.campo] } : c)) });
      baseRef.current = { ...(baseRef.current ?? {}), ...cambios };
      setAvisos((r.avisos ?? []).map((a) => `${a.etiqueta}: ${a.motivo}`));
      if (r.fila) onFila(r.fila);
      onAviso(`${d.sku}: ${r.guardados ?? 0} atributo(s) guardados.`);
    } catch (e) {
      setAvisos([mensajeDeError(e, "No se pudo guardar.")]);
    } finally {
      setGuardando(false);
    }
  };

  const campos = d?.campos ?? [];
  const exigidos = campos.filter((c) => c.exigido);
  const auto = campos.filter((c) => c.nivel === "auto");
  const opcionales = campos.filter((c) => c.nivel === "principal");
  const fiscales = campos.filter((c) => c.nivel === "secundario");
  const llenosOpc = opcionales.filter((c) => (valores[c.campo] ?? "").trim() || c.publicado).length;

  return (
    <div className="rounded-xl bg-white p-3 ring-1 ring-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          Atributos para Mercado Libre
        </h4>
        {f.faltan_ml.length > 0 ? (
          <span className="text-xs font-bold text-rose-600">
            faltan {f.faltan_ml.length} de {f.exigidos_total} exigidos
          </span>
        ) : f.exigidos_total > 0 ? (
          <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700">
            <CheckCircle2 className="h-3.5 w-3.5" /> exigidos completos
          </span>
        ) : null}
      </div>

      {cargando && !d ? (
        <div className="flex items-center gap-2 py-6 text-xs text-slate-400">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Leyendo lo que pide la categoría…
        </div>
      ) : !d || !d.ok ? (
        <div className="mt-2">
          <p className="text-xs text-rose-600">
            {error ?? "No se pudieron leer los atributos."} Lo capturado no se perdió.
          </p>
          {f.faltan_ml.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {f.faltan_ml.map((x) => (
                <span key={x.campo} style={NIVEL[x.nivel].style}
                      className={`rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ${NIVEL[x.nivel].c}`}
                      title={`${x.campo} · ${NIVEL[x.nivel].t}`}>
                  {x.etiqueta}
                </span>
              ))}
            </div>
          )}
          <button type="button" onClick={() => setReintento((n) => n + 1)}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50">
            <RefreshCw className="h-3.5 w-3.5" /> Reintentar
          </button>
        </div>
      ) : f.estado === "sin_categoria" || !d.categoria ? (
        <p className="mt-2 text-xs text-slate-500">
          Sin categoría de ML no se sabe qué pide. Se asigna en Crear Productos o en
          el Publicador (la del panel manda).
        </p>
      ) : !campos.length ? (
        <p className="mt-2 text-xs text-slate-500">
          {error ?? `Mercado Libre no contestó qué exige la categoría ${f.categoria}. Recarga en un momento.`}
        </p>
      ) : (
        <>
          {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
          {exigidos.length > 0 && (
            <CamposAtributos titulo={`Exigidos · ${exigidos.length}`}
                             campos={exigidos.map(editable)} valores={valores} setValores={setValores}
                             soloLectura={!puedeCapturar} errores={errores} />
          )}
          {auto.length > 0 && (
            <CamposAtributos titulo="Automáticos · si se dejan vacíos los llena el publicador"
                             campos={auto.map(editable)} valores={valores} setValores={setValores}
                             soloLectura={!puedeCapturar} errores={errores} />
          )}
          {opcionales.length > 0 && (
            <div className="mt-3">
              <button type="button" onClick={() => setVerOpcionales((v) => !v)}
                      className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400 hover:text-slate-600">
                {verOpcionales ? "▾" : "▸"} Opcionales del producto · {llenosOpc} de {opcionales.length} llenos
              </button>
              {verOpcionales && (
                <CamposAtributos campos={opcionales.map(editable)} valores={valores}
                                 setValores={setValores} soloLectura={!puedeCapturar} errores={errores} />
              )}
            </div>
          )}
          {fiscales.length > 0 && (
            <div className="mt-2">
              <button type="button" onClick={() => setVerFiscales((v) => !v)}
                      className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-300 hover:text-slate-500">
                {verFiscales ? "▾" : "▸"} Facturación (clave SAT, IVA…) · {fiscales.length}
              </button>
              {verFiscales && (
                <CamposAtributos campos={fiscales.map(editable)} valores={valores}
                                 setValores={setValores} soloLectura={!puedeCapturar} errores={errores} />
              )}
            </div>
          )}

          {avisos.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-[11px] text-amber-800">
              {avisos.map((a) => <li key={a}>{a}</li>)}
            </ul>
          )}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <span className="text-[10px] text-slate-400">
              {vaciados > 0
                ? `Vaciar un campo no lo borra (${vaciados}); para borrar, el cajón del Catálogo Maestro.`
                : "Se guarda donde lo lee el Publicador. En verde: lo que ya trae la publicación de ML."}
            </span>
            {puedeCapturar && (
              <button type="button" onClick={guardar} disabled={guardando || !nCambios}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-700 disabled:opacity-40">
                {guardando && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {nCambios ? `Guardar ${nCambios} cambio(s)` : "Sin cambios"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * LA MATRIZ, en un POPUP por categoría (Brandon, 28-sep). Por cada categoría del
 * lote, sus opcionales con una casilla para volverlos obligatorios. Se guarda por
 * CATEGORÍA (no por semana) en channel.field_requirements, así que vale para los
 * SKUs que caigan después en esa categoría.
 *
 * El encabezado y la búsqueda se quedan fijos; la lista se desplaza dentro del
 * popup (con 57 categorías, el encabezado se perdía al bajar). Esc o un clic
 * fuera lo cierran.
 */
function ModalMatriz({
  categorias, abiertas, foco, onAlternar, puedeCapturar, onGuardada, onCerrar,
}: {
  categorias: TableroChecklist["categorias"];
  abiertas: Set<string>;
  /** La categoría a la que se llegó desde un renglón: se lleva a la vista. */
  foco: string | null;
  onAlternar: (cat: string) => void;
  puedeCapturar: boolean;
  onGuardada: (msg: string) => void;
  onCerrar: () => void;
}) {
  const [filtro, setFiltro] = useState("");
  const lista = useRef<HTMLDivElement>(null);
  const q = filtro.trim().toLowerCase();
  const visibles = categorias.filter((c) => !q
    || (c.nombre ?? "").toLowerCase().includes(q) || c.categoria.toLowerCase().includes(q));

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onCerrar(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onCerrar]);

  useEffect(() => {
    if (!foco) return;
    // Después del render, para que la categoría ya esté desplegada.
    const t = setTimeout(() => lista.current?.querySelector(`[data-cat="${CSS.escape(foco)}"]`)
      ?.scrollIntoView({ block: "start" }), 30);
    return () => clearTimeout(t);
  }, [foco]);

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-slate-900/40 p-4 pt-[5vh]"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCerrar(); }}>
      <div role="dialog" aria-modal="true" aria-label="Matriz de obligatorios por categoría"
           className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-2xl bg-white shadow-2xl">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-lg font-extrabold text-slate-900">
              <SlidersHorizontal className="h-5 w-5 text-orange-500" />
              Matriz de obligatorios · por categoría
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Las {categorias.length} categorías de Mercado Libre de este lote. Marca los
              opcionales que almacén debe llenar: queda guardado para esa categoría y
              aplica también a los SKUs que caigan en ella las semanas siguientes.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input value={filtro} onChange={(e) => setFiltro(e.target.value)} placeholder="Buscar categoría"
                   autoFocus={!foco}
                   className="w-56 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-indigo-300" />
            <button type="button" onClick={onCerrar} title="Cerrar (Esc)"
                    className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
        <div ref={lista} className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto">
          {visibles.map((c) => (
            <div key={c.categoria} data-cat={c.categoria}>
              <button type="button" onClick={() => onAlternar(c.categoria)}
                      aria-expanded={abiertas.has(c.categoria)}
                      className={`flex w-full items-center gap-3 px-5 py-2.5 text-left hover:bg-slate-50 ${
                        c.categoria === foco ? "bg-indigo-50/60" : ""}`}>
                <ChevronDown className={`h-4 w-4 shrink-0 text-slate-400 transition ${
                  abiertas.has(c.categoria) ? "" : "-rotate-90"}`} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-semibold text-slate-800">{c.nombre ?? c.categoria}</div>
                  <div className="text-[11px] text-slate-400">
                    <span className="font-mono">{c.categoria}</span> · {c.skus} SKU(s) del lote
                  </div>
                </div>
                <span className="rounded-md px-2 py-0.5 text-[10px] font-bold text-[#2d3277]" style={{ background: ML }}>
                  {c.obligatorios_ml} ML
                </span>
                <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold ${
                  c.promovidos ? "bg-orange-200 text-orange-900" : "bg-slate-100 text-slate-400"}`}>
                  {c.promovidos} matriz
                </span>
                <span className="w-24 text-right text-[11px] text-slate-500">{c.opcionales} opcionales</span>
              </button>
              {abiertas.has(c.categoria) && (
                <MatrizCategoria cat={c.categoria} puedeCapturar={puedeCapturar} onGuardada={onGuardada} />
              )}
            </div>
          ))}
          {!visibles.length && <p className="px-5 py-6 text-center text-xs text-slate-400">Ninguna categoría con ese nombre.</p>}
        </div>
      </div>
    </div>
  );
}

function MatrizCategoria({
  cat, puedeCapturar, onGuardada,
}: { cat: string; puedeCapturar: boolean; onGuardada: (msg: string) => void }) {
  const [m, setM] = useState<MatrizChecklist | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [marcas, setMarcas] = useState<Record<string, boolean>>({});
  const [guardando, setGuardando] = useState(false);
  const [verFiscales, setVerFiscales] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    matrizChecklist(cat, ctrl.signal)
      .then((d) => {
        setM(d);
        setMarcas(Object.fromEntries(d.campos.map((c) => [c.campo, c.nivel === "matriz"])));
        if (!d.ok || d.motivo) setError(d.motivo);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer la categoría."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [cat]);

  const cambios = useMemo(() => {
    const c: Record<string, boolean> = {};
    for (const x of m?.campos ?? []) {
      if (x.nivel === "ml" || x.nivel === "auto") continue;
      if (!!marcas[x.campo] !== (x.nivel === "matriz")) c[x.campo] = !!marcas[x.campo];
    }
    return c;
  }, [m, marcas]);
  const nCambios = Object.keys(cambios).length;

  const guardar = async () => {
    if (!nCambios) return;
    setGuardando(true);
    setError(null);
    try {
      const r = await guardarMatrizChecklist(cat, cambios);
      if (!r.ok) { setError(r.motivo ?? "No se pudo guardar."); return; }
      const d = await matrizChecklist(cat);
      setM(d);
      setMarcas(Object.fromEntries(d.campos.map((c) => [c.campo, c.nivel === "matriz"])));
      onGuardada(`Matriz de ${d.nombre ?? cat}: ${r.guardados ?? 0} cambio(s) guardados.`);
    } catch (e) {
      setError(mensajeDeError(e, "No se pudo guardar."));
    } finally {
      setGuardando(false);
    }
  };

  if (cargando) {
    return (
      <div className="flex items-center gap-2 px-11 pb-4 text-xs text-slate-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Leyendo los atributos de la categoría…
      </div>
    );
  }
  const campos = m?.campos ?? [];
  const fijos = campos.filter((x) => x.nivel === "ml" || x.nivel === "auto");
  const producto = campos.filter((x) => x.nivel !== "ml" && x.nivel !== "auto" && x.jerarquia !== "ITEM");
  const fiscales = campos.filter((x) => x.nivel !== "ml" && x.nivel !== "auto" && x.jerarquia === "ITEM");

  const casilla = (x: CampoChecklist) => (
    <label key={x.campo}
           className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs ring-1 ${
             marcas[x.campo] ? "bg-orange-50 ring-orange-200" : "ring-slate-200 hover:bg-slate-50"} ${
             puedeCapturar ? "cursor-pointer" : "cursor-default"}`}
           title={`${x.campo}${x.tipo ? ` · ${x.tipo}` : ""}${x.valores.length ? ` · ${x.valores.length} valores sugeridos` : ""}`}>
      <input type="checkbox" checked={!!marcas[x.campo]} disabled={!puedeCapturar}
             onChange={(e) => setMarcas((mm) => ({ ...mm, [x.campo]: e.target.checked }))}
             className="h-3.5 w-3.5 rounded accent-orange-500" />
      <span className="min-w-0 truncate font-semibold text-slate-700">{x.etiqueta}</span>
    </label>
  );

  return (
    <div className="px-4 pb-4 sm:pl-11">
      {error && <p className="mb-2 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 ring-1 ring-amber-200">{error}</p>}
      {fijos.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="font-bold uppercase tracking-[0.06em] text-slate-400">Ya exigidos:</span>
          {fijos.map((x) => x.nivel === "auto" ? (
            <span key={x.campo} className="rounded-md bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 ring-1 ring-emerald-200"
                  title="El publicador lo llena solo si se deja vacío">
              {x.etiqueta}{x.por_omision ? ` · ${x.por_omision}` : ""}
            </span>
          ) : (
            <span key={x.campo} style={{ background: ML }} className="rounded-md px-2 py-0.5 font-semibold text-[#2d3277]">
              {x.etiqueta}
            </span>
          ))}
        </div>
      )}
      {producto.length > 0 ? (
        <>
          <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
            Opcionales del producto · marca los que almacén debe llenar
          </div>
          <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">{producto.map(casilla)}</div>
        </>
      ) : (
        <p className="text-xs text-slate-400">Esta categoría no tiene opcionales del producto.</p>
      )}
      {fiscales.length > 0 && (
        <div className="mt-2">
          <button type="button" onClick={() => setVerFiscales((v) => !v)}
                  className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-300 hover:text-slate-500">
            {verFiscales ? "▾" : "▸"} Facturación (clave SAT, IVA…) · {fiscales.length}
          </button>
          {verFiscales && (
            <div className="mt-1.5 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">{fiscales.map(casilla)}</div>
          )}
        </div>
      )}
      {puedeCapturar && (
        <div className="mt-3 flex items-center justify-end gap-3">
          <span className="text-xs text-slate-500">
            {nCambios ? `${nCambios} cambio(s) sin guardar` : "Sin cambios"}
          </span>
          <button type="button" onClick={guardar} disabled={!nCambios || guardando || !m?.ok}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-orange-500 px-3 py-1.5 text-xs font-bold text-white hover:bg-orange-600 disabled:opacity-50">
            {guardando && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Guardar matriz
          </button>
        </div>
      )}
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
  semana, etiqueta, cargadas, onCerrar, onListo,
}: {
  semana: string;
  etiqueta: string;
  /** {lunes: SKUs} de las semanas que ya tienen lote. */
  cargadas: Record<string, number>;
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
                    {cargadas[h.semana] ? (
                      <span className="ml-1 inline-flex items-center gap-0.5" title={`${h.etiqueta} ya tiene ${cargadas[h.semana]} SKUs cargados`}>
                        <Check className="inline h-3 w-3" />{cargadas[h.semana]}
                      </span>
                    ) : null}
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
