"use client";

/**
 * FULLFILMENT · CREAR FULL — la PLANEACIÓN SEMANAL por tienda (Brandon, 24-sep-2026).
 *
 *   · TIENDAS: ML Kubera, ML San Corpe, Amazon FBA y Walmart WFS, cada una con SUS
 *     publicaciones y un interruptor: sólo se planea lo de las tiendas activas.
 *     Temu y TikTok no entran (son sólo DROP).
 *   · SALDO: en FULL hoy (las dos cuentas), publicaciones sin FULL, lo que se está
 *     mandando ESTA semana y lo que va en el plan.
 *   · CREAR: vista previa con Odoo y ML releídos, modo prueba, una orden por tienda
 *     y almacén. Detrás del interruptor.
 *
 * WEEK OVER WEEK (v0.583.0, Brandon, 28-sep): "al iniciar una nueva week la planeación
 * estará vacía para usar la IA y conforme al stock del día de hoy se prepararán los nuevos
 * FULLs de la semana".
 *   · Cada semana (lunes a domingo, CDMX) tiene UN plan y UN chat, compartidos y
 *     guardados en la bitácora (services/fulfillment_semana.py). El lunes nacen VACÍOS.
 *     Las semanas anteriores se ven de consulta con el selector de arriba (page.tsx).
 *   · «Crear FULL con IA» y justo debajo el chat, que se despliega con animación
 *     (RevisionIA.tsx). Lo que la IA escribe se pone EN VIVO en la tabla, en violeta y con
 *     su recomendación en el renglón; cada renglón se puede desmarcar, corregir o quitar.
 *   · Lo que se guarda es el PLAN: cantidad, quién lo puso (IA, a mano, propuesta estándar)
 *     y por qué. La propuesta del prompt estándar sigue a la vista como referencia y se
 *     puede usar de un clic.
 *   · Al crear las órdenes se abre el PROMPT para cargar el FULL en Mercado Libre con un
 *     agente (PromptFull.tsx); después sigue en el botón «CARGAR FULL CON PROMPT» hasta que
 *     la orden tenga su guía.
 *
 * Los totales, los ganadores sin existencia con su reemplazo, los títulos que no coinciden
 * con Odoo y las órdenes sin completar se ven en Análisis (`onPlan`). Esta pantalla se
 * queda montada aunque se cambie de pantalla (page.tsx).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, Boxes, CheckCircle2, ClipboardCopy, Download, Eraser, ExternalLink, ListChecks, PackageX,
  Power, RotateCcw, Search, Sparkles, Trash2, Truck,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { PlanAnalisis } from "./AnalisisPlaneacion";
import BuscarSku from "./BuscarSku";
import ConfirmarFull, { GuiaOrden } from "./ConfirmarFull";
import PromptFull from "./PromptFull";
import type { GrupoPrompt } from "./PromptFull";
import ChatSemana, { datosParaIA } from "./RevisionIA";
import type { VivoIA } from "./RevisionIA";
import { claveDe, planear, totalesDe } from "./proponer";
import type { Renglon, Totales } from "./proponer";
import { enSemana, semanaPorClave } from "./semana";
import type { Semana } from "./semana";
import { Ayuda, Ceja, FONDO_RAYADO, PUNTO_CUENTA, Tarjeta, dia, fecha, num, pesos, rangoSemana } from "./ui";
import type {
  AjusteIA, AvanceIA, BorradorFull, EntradaPlan, EstadoSemana, FilaPlan, Interruptor, ParametrosFull, PropuestaFull,
  ReemplazoIA, ResultadoCrear, ResumenPlan, Rol, StockHoy, Tienda, TurnoSemana,
} from "./tipos";
import { TIENDAS } from "./tipos";

type Filtro = "mandar" | "ia" | "recorte" | "pendiente" | "ganadores" | "cubiertos" | "todos" | "quitados";

const FILTROS: { k: Filtro; t: string; titulo: string }[] = [
  { k: "mandar", t: "Por mandar", titulo: "El plan de la semana: lo que puso la IA, lo que agregaste o cambiaste a mano." },
  { k: "ia", t: "De la IA", titulo: "Lo que puso o sacó la IA en el chat de la semana, con su recomendación." },
  { k: "recorte", t: "Recorte", titulo: "Piden más de lo que Odoo tiene libre: va lo que hay." },
  { k: "pendiente", t: "Pendiente", titulo: "Sin dato de Odoo (el SKU no está en Odoo): no es un cero." },
  { k: "ganadores", t: "Ganadores agotados", titulo: "Vendieron bien y no hay ni en Odoo ni en el almacén: se sugiere reemplazo." },
  { k: "cubiertos", t: "Ya cubiertos", titulo: "Lo que hay, lo que va en camino y los borradores alcanzan la cobertura." },
  { k: "todos", t: "Todos", titulo: "Todos los SKUs de la planeación de las tiendas activas." },
  { k: "quitados", t: "Quitados", titulo: "Los SKUs que quitaste de la planeación: se pueden restaurar." },
];

const COLOR_TIENDA: Record<Tienda, string> = {
  "meli:Kubera": PUNTO_CUENTA.Kubera, "meli:San Corpe": PUNTO_CUENTA["San Corpe"], amazon: "#FF9900", walmart: "#0071DC",
};

const NOTA_TIENDA: Partial<Record<Tienda, string>> = {
  amazon: "stock FBA del sync de 15 min",
  walmart: "sin ventas registradas y el stock de WFS no se puede leer (401): sólo manual",
};

const LLAVE_TIENDAS = "fulfillment.tiendas_activas";

function leerActivas(): Record<Tienda, boolean> {
  const base = { "meli:Kubera": true, "meli:San Corpe": true, amazon: false, walmart: false } as Record<Tienda, boolean>;
  try {
    const guardado = JSON.parse(window.localStorage.getItem(LLAVE_TIENDAS) ?? "null");
    return guardado ? { ...base, ...guardado } : base;
  } catch {
    return base;
  }
}

const esperar = (ms: number) => new Promise((res) => setTimeout(res, ms));
const mensaje = (e: unknown) => (e instanceof Error ? e.message : String(e));
/** La huella de lo guardado: si no cambia, no se vuelve a guardar. */
const firmaPlan = (p: Record<string, EntradaPlan>, q: Set<string>) =>
  JSON.stringify([Object.keys(p).sort().map((k) => p[k]), [...q].sort()]);

export interface PedidoReemplazo { id: number; tienda: Tienda; sku: string; de: string }

export default function CrearFull({
  stock, rol, recarga, semana, semanaActual, onEstado, onPlan, reemplazoPedido, onReemplazoHecho, onAbrirEnvio,
}: {
  stock: StockHoy | null | undefined;
  rol: Rol;
  recarga: number;
  /** La semana elegida en el selector de arriba («2026-S40»). */
  semana: string;
  /** La semana en curso: la única que se planea. */
  semanaActual: Semana;
  onEstado?: (e: { skus: number; piezas: number; tiendas: number } | null) => void;
  /** Lo que se ve en Análisis · «Planeación de la semana». */
  onPlan?: (p: PlanAnalisis | null) => void;
  /** Un reemplazo pedido desde Análisis: se marca aquí y se avisa con `onReemplazoHecho`. */
  reemplazoPedido?: PedidoReemplazo | null;
  onReemplazoHecho?: (id: number) => void;
  /** Abre la ventana de una orden en Envíos (su trazabilidad), encima de esta pantalla. */
  onAbrirEnvio?: (orden: string) => void;
}) {
  const esActual = semana === semanaActual.clave;
  const [datos, setDatos] = useState<PropuestaFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [ventana, setVentana] = useState(30);
  const [params, setParams] = useState<ParametrosFull | null>(null);
  const [activas, setActivas] = useState<Record<Tienda, boolean>>({ "meli:Kubera": true, "meli:San Corpe": true,
                                                                   amazon: false, walmart: false });
  const [vista, setVista] = useState<Tienda | "todas">("todas");
  const [agregadas, setAgregadas] = useState<FilaPlan[]>([]);
  const [filtro, setFiltro] = useState<Filtro>("mandar");
  const [busca, setBusca] = useState("");
  const [revisar, setRevisar] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);

  // ── El plan y el chat de la SEMANA EN CURSO (guardados en la bitácora) ─────────────
  const [plan, setPlan] = useState<Record<string, EntradaPlan>>({});
  const planRef = useRef<Record<string, EntradaPlan>>({});
  const [quitadas, setQuitadas] = useState<Set<string>>(new Set());
  const quitadasRef = useRef<Set<string>>(new Set());
  quitadasRef.current = quitadas;
  const [listo, setListo] = useState(false);               // el plan guardado ya se leyó
  const [errorSemana, setErrorSemana] = useState<string | null>(null);
  const [turnos, setTurnos] = useState<TurnoSemana[]>([]);
  const [vivo, setVivo] = useState<VivoIA | null>(null);
  const [chatAbierto, setChatAbierto] = useState(false);
  const [resaltados, setResaltados] = useState<Set<string>>(new Set());
  const [guardado, setGuardado] = useState<{ cuando: string | null; quien: string | null; error?: string } | null>(null);
  const version = useRef(0);
  const ultimaFirma = useRef("");
  const iaVivo = useRef(0);
  const siguiendo = useRef<string | null>(null);         // el turno que esta pantalla va siguiendo
  const aplicados = useRef<Map<string, string>>(new Map());
  const pidiendo = useRef<Set<string>>(new Set());
  const [vaciar, setVaciar] = useState(false);
  // El prompt de «CARGAR FULL CON PROMPT» y la semana que se está consultando (si no es la en curso).
  const [prompt, setPrompt] = useState<{ grupos: GrupoPrompt[]; aviso: string | null } | null>(null);
  const [consulta, setConsulta] = useState<EstadoSemana | null>(null);

  const cambiarPlan = useCallback((fn: (p: Record<string, EntradaPlan>) => Record<string, EntradaPlan>) => {
    const n = fn(planRef.current);
    planRef.current = n;
    setPlan(n);
  }, []);

  useEffect(() => { setActivas(leerActivas()); }, []);
  const cambiarTienda = (t: Tienda) => setActivas((a) => {
    const n = { ...a, [t]: !a[t] };
    try { window.localStorage.setItem(LLAVE_TIENDAS, JSON.stringify(n)); } catch { /* sin almacenamiento */ }
    return n;
  });

  const cargar = useCallback(async (refrescar: boolean, v: number) => {
    setCargando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full?ventana=${v}${refrescar ? "&refrescar=true" : ""}`,
                                  { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
      const d = await r.json() as PropuestaFull;
      setDatos(d);
      setParams((p) => (p ? { ...p, ventana_dias: d.ventana.dias } : d.parametros));
    } catch (e: unknown) {
      setError(mensaje(e));
    } finally {
      setCargando(false);
    }
  }, []);
  useEffect(() => { void cargar(recarga > 0, ventana); }, [cargar, recarga, ventana]);

  const leerSemana = useCallback(async (clave: string): Promise<EstadoSemana> => {
    const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/semana?clave=${encodeURIComponent(clave)}`,
                                { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
    return await r.json() as EstadoSemana;
  }, []);

  // ── Los renglones ─────────────────────────────────────────────────────────
  const tiendasActivas = TIENDAS.filter((t) => activas[t] && datos?.tiendas[t]);
  const todos = useMemo(() => {
    if (!datos || !params) return [];
    const filas = tiendasActivas.flatMap((t) => datos.tiendas[t].filas);
    const ya = new Set(filas.map((f) => claveDe(f.tienda, f.sku)));
    const extra = agregadas.filter((f) => activas[f.tienda] && !ya.has(claveDe(f.tienda, f.sku)));
    return planear(filas.concat(extra), params, new Set(extra.map((f) => claveDe(f.tienda, f.sku))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datos, params, agregadas, activas]);
  const enTodosRef = useRef<Set<string>>(new Set());
  const enTodos = useMemo(() => new Set(todos.map((r) => r.clave)), [todos]);
  enTodosRef.current = enTodos;
  // Lo quitado no se planea, no se crea, no va al Excel ni a la IA.
  const renglones = useMemo(() => todos.filter((r) => !quitadas.has(r.clave)), [todos, quitadas]);
  const quitados = useMemo(() => todos.filter((r) => quitadas.has(r.clave)), [todos, quitadas]);
  const cantidad = useCallback((r: Renglon) => {
    const e = plan[r.clave];
    return e && e.incluido ? e.cantidad : 0;
  }, [plan]);
  const porTienda = useMemo(() => Object.fromEntries(tiendasActivas.map((t) => [t, totalesDe(
    renglones.filter((r) => r.tienda === t), cantidad)])) as Record<Tienda, Totales>,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [renglones, cantidad, activas]);
  const total = totalesDe(renglones, cantidad);
  const origen = useMemo(() => almacenesDelPlan(renglones, cantidad, datos?.almacenes ?? []), [renglones, cantidad, datos]);
  const resumen: ResumenPlan = useMemo(() => {
    const van = renglones.filter((r) => cantidad(r) > 0);
    return {
      skus: van.length, piezas: van.reduce((a, r) => a + cantidad(r), 0),
      reemplazos: van.filter((r) => plan[r.clave]?.reemplazo_de).length,
      de_ia: van.filter((r) => plan[r.clave]?.origen === "ia").length,
    };
  }, [renglones, cantidad, plan]);

  useEffect(() => {
    onEstado?.(datos ? { skus: total.skus_a_mandar, piezas: total.a_mandar, tiendas: tiendasActivas.length } : null);
  }, [datos, total.skus_a_mandar, total.a_mandar, tiendasActivas.length, onEstado]);

  // ── Traer los renglones que no vienen en la planeación (búsqueda, IA, plan guardado) ─
  const traerFilas = useCallback(async (faltan: { tienda: Tienda; sku: string }[]) => {
    const porTienda = new Map<Tienda, string[]>();
    for (const f of faltan) {
      const k = claveDe(f.tienda, f.sku);
      if (enTodosRef.current.has(k) || pidiendo.current.has(k)) continue;
      pidiendo.current.add(k);
      porTienda.set(f.tienda, [...(porTienda.get(f.tienda) ?? []), f.sku]);
    }
    for (const [t, skus] of porTienda) {
      for (let i = 0; i < skus.length; i += 25) {
        const lote = skus.slice(i, i + 25);
        try {
          const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/buscar?tienda=${encodeURIComponent(t)}`
            + `&q=${encodeURIComponent(lote.join(","))}&ventana=${ventana}`, { cache: "no-store" });
          const d = r.ok ? await r.json() as { filas: FilaPlan[] } : { filas: [] };
          const buenas = d.filas.filter((f) => lote.includes(f.sku));
          setAgregadas((a) => [...a, ...buenas.filter((f) => !a.some((x) => x.tienda === f.tienda && x.sku === f.sku))]);
        } catch { /* se queda en el plan; el renglón aparece al volver a leer */ }
      }
    }
  }, [ventana]);

  // ── La IA: aplicar en vivo y seguir el turno ──────────────────────────────
  /**
   * Pone en el plan lo que la IA propone. Se llama con lo que lleva escrito (cada 2 s) y
   * con la respuesta final: un valor ya aplicado en este turno no se vuelve a poner, así
   * que si la persona lo corrige mientras la IA sigue escribiendo, su cambio se respeta.
   */
  const aplicarIA = useCallback((turno: string, ajustes: AjusteIA[], reemplazos: ReemplazoIA[]) => {
    const n = { ...planRef.current };
    const nuevos: string[] = [];
    const faltan: { tienda: Tienda; sku: string }[] = [];
    const poner = (tienda: Tienda, sku: string, cant: number, motivo: string, de: string | null) => {
      const k = claveDe(tienda, sku);
      if (quitadasRef.current.has(k)) return;
      const huella = `${cant}|${de ?? ""}`;
      if (aplicados.current.get(k) === huella) return;
      aplicados.current.set(k, huella);
      const prev = n[k];
      n[k] = { tienda, sku, cantidad: cant, origen: "ia", motivo: motivo || prev?.motivo || null, turno,
               reemplazo_de: de ?? prev?.reemplazo_de ?? null, incluido: true };
      nuevos.push(k);
      if (!enTodosRef.current.has(k)) faltan.push({ tienda, sku });
    };
    for (const a of ajustes) poner(a.tienda, a.sku, a.cantidad, a.motivo, null);
    for (const r of reemplazos) poner(r.tienda, r.reemplazo, r.cantidad, r.motivo, r.agotado);
    if (!nuevos.length) return;
    planRef.current = n;
    setPlan(n);
    setResaltados((s) => new Set([...s, ...nuevos]));
    if (faltan.length) void traerFilas(faltan);
  }, [traerFilas]);

  const seguir = useCallback(async (id: string, instruccion: string, modelo?: string, quien?: string | null) => {
    const vuelta = ++iaVivo.current;
    siguiendo.current = id;
    aplicados.current = new Map();
    setResaltados(new Set());
    setVivo({ id, instruccion, modelo, quien, avance: null });
    setChatAbierto(true);
    setFiltro("mandar");
    const terminar = () => { if (siguiendo.current === id) siguiendo.current = null; setVivo(null); };
    for (let i = 0; i < 900 && vuelta === iaVivo.current; i++) {
      await esperar(2000);
      let e: AvanceIA;
      try {
        e = await (await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/ia/${id}`, { cache: "no-store" })).json();
      } catch {
        continue;
      }
      if (vuelta !== iaVivo.current) return;
      if (e.estado === "corriendo") {
        setVivo((v) => (v && v.id === id ? { ...v, avance: e } : v));
        if (e.parcial) aplicarIA(id, e.parcial.ajustes, e.parcial.reemplazos);
        continue;
      }
      if (e.estado === "listo" && e.resultado) {
        aplicarIA(id, e.resultado.ajustes, e.resultado.reemplazos);
        const r = e.resultado;
        setTurnos((ts) => [...ts.filter((t) => t.id !== id), {
          id, instruccion, modelo_nombre: r.modelo_nombre, estado: "listo", resultado: r, segundos: e.segundos,
          creado: new Date().toISOString(), quien: quien ?? null }]);
        if (e.sin_guardar) setAviso("El turno no quedó en la bitácora: se ve aquí, pero no sobrevive a recargar la página.");
      } else {
        setTurnos((ts) => [...ts, { id, instruccion, estado: "error", creado: new Date().toISOString(), quien: quien ?? null,
                                    error: e.motivo ?? "el turno se perdió (el servidor pudo reiniciarse)" }]);
      }
      terminar();
      return;
    }
    if (vuelta === iaVivo.current) terminar();
  }, [aplicarIA]);

  // ── La semana en curso: su plan y su chat, UNA vez (y al pedir «actualizar») ─────────
  useEffect(() => {
    let vivoEfecto = true;
    const sucio = listo && firmaPlan(planRef.current, quitadasRef.current) !== ultimaFirma.current;
    leerSemana(semanaActual.clave).then((e) => {
      if (!vivoEfecto) return;
      setErrorSemana(null);
      setTurnos(e.turnos);
      // Si hay cambios sin guardar, lo de la pantalla manda: no se pisa con lo guardado.
      if (!sucio) {
        const entradas = Object.fromEntries((e.plan?.entradas ?? []).map((x) => [claveDe(x.tienda, x.sku), x]));
        const q = new Set(e.plan?.quitados ?? []);
        planRef.current = entradas;
        setPlan(entradas);
        setQuitadas(q);
        version.current = e.plan?.version ?? 0;
        ultimaFirma.current = firmaPlan(entradas, q);
        setGuardado(e.plan ? { cuando: e.plan.guardado ?? null, quien: e.plan.quien ?? null } : null);
        // Lo que puso el ÚLTIMO turno de la IA se resalta: si terminó mientras la persona estaba en
        // otra pantalla, al volver se ve qué cambió (el servidor ya lo guardó en el plan).
        const listos = e.turnos.filter((t) => t.estado === "listo");
        const ultimoTurno = listos.length ? listos[listos.length - 1].id : null;
        if (ultimoTurno && !e.corriendo) {
          setResaltados(new Set(Object.entries(entradas).filter(([, x]) => x.turno === ultimoTurno).map(([k]) => k)));
        }
      }
      setListo(true);
      if (e.turnos.length || e.corriendo) setChatAbierto(true);
      // Un turno que ya estaba corriendo (esta persona recargó, u otra lo mandó): se sigue en vivo.
      if (e.corriendo && siguiendo.current !== e.corriendo.id) {
        void seguir(e.corriendo.id, e.corriendo.instruccion, e.corriendo.modelo, e.corriendo.quien);
      }
    }).catch((err: unknown) => { if (vivoEfecto) setErrorSemana(mensaje(err)); });
    return () => { vivoEfecto = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [semanaActual.clave, recarga, leerSemana]);
  useEffect(() => () => { iaVivo.current++; }, []);

  // Los renglones del plan guardado que no vienen en la planeación (agregados a mano, de la IA).
  useEffect(() => {
    if (!listo || !datos || !params) return;
    const faltan = Object.values(planRef.current)
      .filter((e) => activas[e.tienda] && !enTodosRef.current.has(claveDe(e.tienda, e.sku)))
      .map((e) => ({ tienda: e.tienda, sku: e.sku }));
    if (faltan.length) void traerFilas(faltan);
  }, [listo, datos, params, activas, traerFilas]);

  // Guardar el plan: 1.5 s después del último cambio y sólo si cambió. Mientras la IA escribe
  // no: el plan cambia cada 2 s y serían decenas de guardados por turno; se guarda al terminar.
  useEffect(() => {
    if (!listo || vivo) return;
    const f = firmaPlan(plan, quitadas);
    if (f === ultimaFirma.current) return;
    const t = setTimeout(async () => {
      const v = version.current + 1;
      try {
        const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/semana/plan`, {
          method: "POST",
          body: JSON.stringify({ semana: semanaActual.clave, plan: {
            version: v, entradas: Object.values(planRef.current), quitados: [...quitadasRef.current],
            activas: TIENDAS.filter((x) => activas[x]), parametros: params ?? {} } }),
        }, { "Content-Type": "application/json" });
        const d = await r.json().catch(() => ({})) as { ok?: boolean; motivo?: string; detail?: string };
        if (!r.ok || d.ok === false) throw new Error(d.motivo ?? d.detail ?? `HTTP ${r.status}`);
        version.current = v;
        ultimaFirma.current = f;
        setGuardado({ cuando: new Date().toISOString(), quien: null });
      } catch (e: unknown) {
        setGuardado((g) => ({ cuando: g?.cuando ?? null, quien: g?.quien ?? null, error: mensaje(e) }));
      }
    }, 1500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, quitadas, listo, !!vivo]);

  // Una semana anterior: sólo consulta (su chat, su plan y sus órdenes).
  useEffect(() => {
    if (esActual) { setConsulta(null); return; }
    let vivoEfecto = true;
    setConsulta(null);
    leerSemana(semana).then((e) => { if (vivoEfecto) setConsulta(e); })
      .catch((err: unknown) => { if (vivoEfecto) setErrorSemana(mensaje(err)); });
    return () => { vivoEfecto = false; };
  }, [semana, esActual, leerSemana]);

  const pedirIA = async (instruccion: string, modelo: string) => {
    if (!datos || !params || !esActual) return;
    const nombreModelo = datos.ia_modelos?.find((m) => m.id === modelo)?.nombre ?? modelo;
    const ahora = Object.values(planRef.current).filter((e) => e.incluido && e.cantidad > 0)
      .map((e) => [e.tienda, e.sku, e.cantidad]);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/ia`, {
        method: "POST",
        body: JSON.stringify({ semana: semanaActual.clave, instrucciones: instruccion, modelo, plan: ahora,
                               datos: datosParaIA(renglones, params, datos, tiendasActivas) }),
      }, { "Content-Type": "application/json" });
      const d = await r.json().catch(() => ({})) as { ok?: boolean; id?: string; motivo?: string; detail?: string };
      if (r.ok && d.ok && d.id) { void seguir(d.id, instruccion, nombreModelo, null); return; }
      // Otra persona tiene un turno corriendo en el chat de la semana: se sigue ése.
      if (d.id) void seguir(d.id, "(turno de otra persona)", undefined, null);
      throw new Error(d.motivo ?? d.detail ?? `HTTP ${r.status}`);
    } catch (e: unknown) {
      setTurnos((ts) => [...ts, { id: `local-${Date.now()}`, instruccion, estado: "error", error: mensaje(e),
                                  creado: new Date().toISOString() }]);
      setChatAbierto(true);
    }
  };

  // ── Editar el plan a mano ─────────────────────────────────────────────────
  const entradaDe = (r: Pick<Renglon, "tienda" | "sku">, cant = 0): EntradaPlan => ({
    tienda: r.tienda, sku: r.sku, cantidad: cant, origen: "manual", motivo: null, turno: null, reemplazo_de: null,
    incluido: true,
  });
  const fijar = (r: Renglon, v: string) => {
    const n = Math.max(0, Math.min(100_000, parseInt(v.replace(/[^\d]/g, ""), 10) || 0));
    cambiarPlan((p) => ({ ...p, [r.clave]: { ...(p[r.clave] ?? entradaDe(r)), cantidad: n, origen: "manual", incluido: true } }));
  };
  const alternar = (r: Renglon) => cambiarPlan((p) => (p[r.clave]
    ? { ...p, [r.clave]: { ...p[r.clave], incluido: !p[r.clave].incluido } } : p));
  const quitar = (r: Renglon) => {
    cambiarPlan((p) => { const n = { ...p }; delete n[r.clave]; return n; });
    setQuitadas((q) => new Set(q).add(r.clave));
    setAviso(`Quitaste ${r.sku} de ${datos?.tiendas[r.tienda]?.nombre ?? r.tienda}. Está en «Quitados» por si lo quieres de vuelta.`);
  };
  const restaurar = (r: Renglon) => {
    setQuitadas((q) => { const n = new Set(q); n.delete(r.clave); return n; });
    setAviso(`${r.sku} regresó a la planeación.`);
  };
  const agregar = (filas: FilaPlan[]) => {
    // Lo que se había quitado y se vuelve a pedir, se restaura.
    setQuitadas((q) => {
      const n = new Set(q);
      filas.forEach((f) => n.delete(claveDe(f.tienda, f.sku)));
      return n;
    });
    setAgregadas((a) => [...a, ...filas.filter((f) => !enTodos.has(claveDe(f.tienda, f.sku)))]);
    cambiarPlan((p) => {
      const n = { ...p };
      for (const f of filas) n[claveDe(f.tienda, f.sku)] ??= entradaDe(f);
      return n;
    });
    setAviso(`Agregado${filas.length > 1 ? "s" : ""}: ${filas.map((f) => f.sku).join(", ")}. Escribe cuántas piezas mandar.`);
    setFiltro("mandar");
  };
  const agregarPorSku = async (tienda: Tienda, sku: string, cant?: number, de?: string) => {
    setAviso(`Buscando ${sku} en ${datos?.tiendas[tienda]?.nombre ?? tienda} (Mercado Libre en vivo)…`);
    const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/buscar?tienda=${encodeURIComponent(tienda)}`
      + `&q=${encodeURIComponent(sku)}&ventana=${ventana}`, { cache: "no-store" });
    const d = r.ok ? await r.json() as { filas: FilaPlan[] } : { filas: [] };
    const f = d.filas.find((x) => x.sku === sku);
    if (!f) { setAviso(`${sku} no está publicado en ${datos?.tiendas[tienda]?.nombre ?? tienda}.`); return; }
    agregar([f]);
    cambiarPlan((p) => ({ ...p, [claveDe(tienda, sku)]: { ...(p[claveDe(tienda, sku)] ?? entradaDe(f)),
                                                         cantidad: cant ?? 0, reemplazo_de: de ?? null } }));
  };
  /**
   * Un REEMPLAZO pedido desde Análisis: como vende en esa tienda, casi siempre ya es
   * renglón de la planeación; entra al plan marcado «REEMPLAZO de …» con su propuesta.
   */
  const marcarReemplazo = (tienda: Tienda, sku: string, de: string) => {
    const clave = claveDe(tienda, sku);
    if (!enTodos.has(clave)) { void agregarPorSku(tienda, sku, undefined, de); return; }
    setQuitadas((q) => { const n = new Set(q); n.delete(clave); return n; });
    const r = todos.find((x) => x.clave === clave);
    cambiarPlan((p) => ({ ...p, [clave]: { ...(p[clave] ?? entradaDe({ tienda, sku }, r?.propuesta ?? 0)),
                                         reemplazo_de: de, incluido: true } }));
    setAviso(`${sku} entra como REEMPLAZO de ${de} (${de} no se puede surtir: no hay existencia). Revisa cuántas mandar.`);
    setFiltro("mandar");
  };
  // Lo que se pidió desde Análisis.
  useEffect(() => {
    if (!reemplazoPedido || !datos || !listo) return;
    marcarReemplazo(reemplazoPedido.tienda, reemplazoPedido.sku, reemplazoPedido.de);
    onReemplazoHecho?.(reemplazoPedido.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reemplazoPedido?.id, datos, listo]);

  /** «Aplicar al plan» en un turno del chat: vuelve a poner lo que propuso (lo quitado no regresa). */
  const aplicarTurno = (t: TurnoSemana) => {
    if (!t.resultado || vivo) return;
    aplicados.current = new Map();          // aplicar de nuevo aunque ya se hubiera aplicado antes
    aplicarIA(t.id, t.resultado.ajustes, t.resultado.reemplazos);
    setAviso(`Se aplicó al plan lo que propuso la IA en ese turno: ${t.resultado.ajustes.length} ajustes y `
      + `${t.resultado.reemplazos.length} reemplazos, en violeta en la tabla.`);
    setFiltro("ia");
  };

  const llenarEstandar = () => {
    let n = 0;
    cambiarPlan((p) => {
      const x = { ...p };
      for (const r of renglones) {
        if (r.propuesta > 0 && !x[r.clave]) { x[r.clave] = { ...entradaDe(r, r.propuesta), origen: "estandar" }; n++; }
      }
      return x;
    });
    setAviso(n ? `Se agregaron ${n} SKUs con la propuesta estándar. Afínalos a mano o pídele cambios a la IA.`
      : "La propuesta estándar no agrega nada que no esté ya en el plan.");
    setFiltro("mandar");
  };
  const vaciarPlan = () => {
    cambiarPlan(() => ({}));
    setVaciar(false);
    setAviso("El plan de la semana quedó vacío. El chat sigue: puedes pedirle a la IA que lo vuelva a armar.");
  };

  // ── Filtros y orden ───────────────────────────────────────────────────────
  const enFiltro = (r: Renglon, f: Filtro) => {
    const e = plan[r.clave];
    switch (f) {
      case "mandar": return !!e && (e.cantidad > 0 || e.origen !== "ia");
      case "ia": return e?.origen === "ia";
      case "recorte": return r.estado === "recorte";
      case "pendiente": return r.estado === "pendiente";
      case "ganadores": return r.ganador_agotado;
      case "cubiertos": return r.estado === "cubierto";
      default: return true;
    }
  };
  const enVista = renglones.filter((r) => vista === "todas" || r.tienda === vista);
  const quitadosEnVista = quitados.filter((r) => vista === "todas" || r.tienda === vista);
  const q = busca.trim().toUpperCase();
  const visibles = (filtro === "quitados" ? quitadosEnVista : enVista.filter((r) => enFiltro(r, filtro)))
    .filter((r) => !q || r.sku.toUpperCase().includes(q) || (r.nombre ?? "").toUpperCase().includes(q))
    // El orden NO depende de lo tecleado: si dependiera, el renglón saltaría de lugar a media captura.
    .sort((a, b) => Number(!!plan[b.clave]?.reemplazo_de) - Number(!!plan[a.clave]?.reemplazo_de)
      || Number(b.agregado ?? false) - Number(a.agregado ?? false) || b.propuesta - a.propuesta
      || (a.aguanta ?? -1) - (b.aguanta ?? -1) || b.vv - a.vv || a.sku.localeCompare(b.sku));

  // ── Excel y SKUs ──────────────────────────────────────────────────────────
  const planParaExcel = () => ({
    semana: datos ? `${semanaActual.semana} · ${semanaActual.rango}` : "",
    ventana: datos ? `${datos.ventana.dias} días (${dia(`${datos.ventana.desde}T12:00:00-06:00`)} a ${dia(`${datos.ventana.hasta}T12:00:00-06:00`)})` : "",
    parametros_texto: params ? `cobertura ${params.cobertura_dias} d · mínimo por renglón ${params.min_piezas} · ganador desde ${params.min_ventas} · dejar en bodega ${params.dejar_en_bodega}` : "",
    en_vivo: "Mercado Libre verificado en vivo · Odoo (libre) en vivo · ventas de kubera · Walmart en vivo sin stock de WFS",
    tiendas: tiendasActivas.map((t) => ({
      tienda: t, nombre: datos!.tiendas[t].nombre, totales: porTienda[t],
      renglones: renglones.filter((r) => r.tienda === t && (cantidad(r) > 0 || r.pidio > 0)).map((r) => ({
        sku: r.sku, nombre: r.nombre, destino: datos!.tiendas[t].destino, vv: r.vv, v7: r.v7, stock: r.stock,
        en_camino: r.en_camino, borrador: r.borrador, libre: r.libre_total, pidio: r.pidio, bodega_puede: r.bodega,
        propuesta: r.propuesta, a_mandar: cantidad(r), estado: r.estado, caja: r.caja, listing_id: r.listing_id,
        precio: r.precio, reemplazo_de: plan[r.clave]?.reemplazo_de ?? null,
      })),
    })),
    ganadores: renglones.filter((r) => r.ganador_agotado).map((r) => ({
      tienda: datos!.tiendas[r.tienda].nombre, sku: r.sku, nombre: r.nombre, vv: r.vv, candidatos: r.reemplazos })),
    alertas: alertasDe(renglones, datos),
  });
  const descargar = async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/excel`,
                                  { method: "POST", body: JSON.stringify(planParaExcel()) },
                                  { "Content-Type": "application/json" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `planeacion_full_${semanaActual.semana}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Liberar después: revocar en el acto a veces cancela la descarga.
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
    } catch (e: unknown) {
      setAviso(`No se pudo descargar: ${mensaje(e)}`);
    }
  };
  const copiarSkus = () => {
    const skus = renglones.filter((r) => cantidad(r) > 0).map((r) => r.sku);
    void navigator.clipboard?.writeText(skus.join(", "));
    setAviso(`${skus.length} SKUs copiados.`);
  };

  // ── Las órdenes de la semana y «CARGAR FULL CON PROMPT» ───────────────────
  const ordenesDe = (clave: string) => (datos?.borradores ?? []).filter((b) => b.panel && enSemana(b.creada, clave));
  const porCargar = ordenesDe(semanaActual.clave).filter((b) => b.tienda?.startsWith("meli") && !b.guia_pdf);
  // UN prompt por CUENTA con todas sus órdenes de la semana (Brandon, 28-sep: "cargar FULL con
  // prompt es por cada cuenta y cada cuenta deberá de tener su lista de SKUs").
  const abrirPrompt = (bs: Pick<BorradorFull, "id" | "orden" | "tienda" | "almacen">[], avisoTxt: string | null = null) => {
    const grupos = new Map<string, GrupoPrompt>();
    for (const b of bs) {
      const t = b.tienda ?? "sin tienda";
      const g = grupos.get(t) ?? { tienda: t, nombre: (b.tienda && datos?.tiendas[b.tienda]?.nombre) || t, ordenes: [] };
      if (!g.ordenes.some((o) => o.id === b.id)) g.ordenes.push({ id: b.id, orden: b.orden, almacen: b.almacen ?? null });
      grupos.set(t, g);
    }
    setPrompt({ grupos: [...grupos.values()], aviso: avisoTxt });
  };
  /** Las órdenes pendientes de la misma cuenta que `b`: el prompt de esa cuenta las lleva todas. */
  const deLaCuenta = (b: Pick<BorradorFull, "id" | "orden" | "tienda" | "almacen">) => {
    const mismas = porCargar.filter((x) => x.tienda === b.tienda);
    return mismas.some((x) => x.id === b.id) ? mismas : [...mismas, b];
  };
  const cuentasPorCargar = new Set(porCargar.map((b) => b.tienda)).size;
  const alCrear = (r: ResultadoCrear) => {
    void cargar(true, ventana);
    const ml = (r.tiendas ?? []).filter((t) => t.tienda.startsWith("meli"))
      .flatMap((t) => (t.ordenes ?? []).map((o) => ({ id: o.id, orden: o.orden, tienda: t.tienda, almacen: o.almacen })));
    if (!ml.length) return;          // sin órdenes de ML, la confirmación se queda abierta con su guía
    setRevisar(false);
    // Cada cuenta con TODAS sus órdenes pendientes de la semana, no sólo las que se acaban de crear.
    const cuentas = new Set(ml.map((o) => o.tienda));
    abrirPrompt([...porCargar.filter((b) => cuentas.has(b.tienda as Tienda)), ...ml],
                `${r.accion === "ya_existia" ? "Ya estaban creadas" : "Creadas en Odoo en borrador"}: `
      + `${ml.map((o) => o.orden).join(", ")}${r.prueba ? " (PRUEBA)" : ""}. Ahora cárgalas en Mercado Libre con el prompt.`);
  };

  // ── Lo que se ve en Análisis · «Planeación de la semana» ─────────────────
  // Ganadores y títulos salen de las CUATRO tiendas (no dependen de lo editado);
  // los totales, del plan que se está armando.
  const paraAnalisis = useMemo(() => (datos && params
    ? planear(TIENDAS.filter((t) => datos.tiendas[t]).flatMap((t) => datos.tiendas[t].filas), params) : []),
  [datos, params]);
  const planAnalisis = useMemo<PlanAnalisis | null>(() => {
    if (!datos) return null;
    const nombres = Object.fromEntries(TIENDAS.map((t) => [t, datos.tiendas[t]?.nombre ?? t])) as Record<Tienda, string>;
    const almacen = Object.fromEntries(TIENDAS.map((t) => [t, datos.tiendas[t]?.almacen ?? "almacén"])) as Record<Tienda, string>;
    return {
      semana: `${semanaActual.semana} · ${semanaActual.rango}`,
      generado: datos.generado, nombres, almacen,
      totales: tiendasActivas.map((t) => ({ tienda: t, t: porTienda[t] })).filter((x) => x.t),
      total,
      ganadores: paraAnalisis.filter((r) => r.ganador_agotado).sort((a, b) => b.vv - a.vv).map((r) => ({
        tienda: r.tienda, sku: r.sku, nombre: r.nombre, vv: r.vv, v7: r.v7, precio: r.precio, stock: r.stock,
        url: r.url, reemplazos: r.reemplazos })),
      titulos: paraAnalisis.filter((r) => r.alertas.includes("reciclado")).map((r) => ({
        tienda: r.tienda, sku: r.sku, nombre: r.nombre, nombre_odoo: r.nombre_odoo, titulo_mkt: r.titulo_mkt,
        imagen_mkt: r.imagen_mkt, url: r.url, listing_id: r.listing_id, parecido: r.parecido,
        urgente: r.titulo_urgente })),
      otras: alertasDe(paraAnalisis.filter((r) => r.alertas.some((a) => a !== "reciclado")), datos)
        .filter((a) => a.tipo !== "reciclado")
        .map((a) => ({ tienda: a.tienda_llave, sku: a.sku, tipo: a.tipo, detalle: a.detalle })),
      borradores: datos.borradores, abiertas: datos.abiertas ?? [],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datos, paraAnalisis, porTienda, total.a_mandar, total.pedidas, total.propuestas, activas, semanaActual.clave]);
  useEffect(() => { onPlan?.(planAnalisis); }, [planAnalisis, onPlan]);

  // ── Saldo ────────────────────────────────────────────────────────────────
  const full = (["Kubera", "San Corpe"] as const).map((c) => ({ c, s: stock?.full[c] }));
  const enFull = full.every((x) => x.s) ? full.reduce((a, x) => a + (x.s?.piezas ?? 0), 0) : null;
  const sinFull = full.every((x) => x.s) ? full.reduce((a, x) => a + (x.s?.en_cero ?? 0), 0) : null;
  const pubFull = full.reduce((a, x) => a + (x.s?.publicaciones ?? 0), 0);
  const estaSemana = tiendasActivas.map((t) => ({ t, s: datos?.esta_semana[t] }));
  const semPzs = estaSemana.reduce((a, x) => a + (x.s?.piezas ?? 0), 0);
  const semSkus = estaSemana.reduce((a, x) => a + (x.s?.skus ?? 0), 0);
  const semEnvios = estaSemana.reduce((a, x) => a + (x.s?.envios ?? 0), 0);

  const bloqueo = !esActual ? "Sólo se planea la semana en curso."
    : !datos?.ia_disponible ? "La IA no está configurada en este ambiente (falta DEEPSEEK_API_KEY)."
      : !renglones.length ? (cargando ? "Leyendo la planeación…" : "Prende al menos una tienda con SKUs.")
        : !listo ? "Leyendo el plan guardado de la semana…" : null;

  // ── Una semana ANTERIOR: sólo consulta ────────────────────────────────────
  if (!esActual) {
    const s = semanaPorClave(semana);
    return (
      <SemanaPasada semana={s} consulta={consulta} error={errorSemana} datos={datos} ordenes={ordenesDe(semana)} rol={rol} />
    );
  }

  return (
    <div className="mt-4 flex flex-col gap-3">
      {/* ── Encabezado, chat con la IA, tiendas y saldo ──────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Ceja>
              Planeación semanal {semanaActual.semana} · {semanaActual.rango}
              {datos ? ` · ventas del ${dia(`${datos.ventana.desde}T12:00:00-06:00`)} al ${dia(`${datos.ventana.hasta}T12:00:00-06:00`)}` : ""} · hora de CDMX
            </Ceja>
            <h2 className="mt-1 text-[22px] font-extrabold tracking-tight text-slate-900">Crear FULL</h2>
            <p className="mt-0.5 max-w-2xl text-[13px] text-slate-500">
              La semana empieza vacía: pídele a la IA que arme el FULL con los datos de hoy (se pone en la tabla en
              vivo), afínalo y crea las órdenes en Odoo. Después carga el FULL en Mercado Libre con el prompt.
            </p>
          </div>
          {datos && <EstadoInterruptor interruptor={datos.interruptor} rol={rol} onCambio={() => void cargar(false, ventana)} />}
        </div>

        <ChatSemana semana={semanaActual} esActual turnos={turnos} vivo={vivo} datos={datos} totales={resumen}
                    abierto={chatAbierto} onAbrir={setChatAbierto} onEnviar={(t, m) => void pedirIA(t, m)}
                    onVerIA={() => { setFiltro("ia"); document.getElementById("plan-semana")?.scrollIntoView({ behavior: "smooth" }); }}
                    bloqueo={bloqueo} onAplicarTurno={listo ? aplicarTurno : undefined} />
        {errorSemana && (
          <p className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-[12px] text-rose-800">
            No se pudo leer el plan y el chat guardados de la semana: {errorSemana}. Lo que hagas no se va a guardar hasta
            que se pueda leer; recarga en un momento.
          </p>
        )}

        {/* Las tiendas: prender y apagar, y cuánto va a cada una. */}
        <div className="mt-4 grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          {TIENDAS.map((t) => {
            const d = datos?.tiendas[t];
            const on = activas[t];
            const tot = porTienda[t];
            return (
              <button key={t} type="button" onClick={() => cambiarTienda(t)} aria-pressed={on}
                      className={`rounded-xl border px-3.5 py-2.5 text-left transition ${
                        on ? "border-indigo-200 bg-white shadow-sm" : "border-dashed border-slate-200 bg-slate-50 opacity-70"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-2 text-[13px] font-extrabold text-slate-800">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR_TIENDA[t] }} />
                    {d?.nombre ?? t}
                  </span>
                  <span className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition ${on ? "bg-indigo-600" : "bg-slate-300"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition ${on ? "left-[18px]" : "left-0.5"}`} />
                  </span>
                </div>
                <div className="mt-1 font-mono text-[15px] font-extrabold tabular-nums text-slate-900">
                  {on ? (tot ? `${num(tot.a_mandar)} pzs` : "…") : "apagada"}
                  {on && tot ? <span className="ml-1 text-[11px] font-semibold text-slate-400">{num(tot.skus_a_mandar)} SKUs</span> : null}
                </div>
                <div className="text-[11px] text-slate-500">
                  {d ? `${num(d.publicadas)} publicadas${d.verificadas ? ` · ${num(d.verificadas)} verificadas en vivo` : ""}` : "leyendo…"}
                </div>
                {NOTA_TIENDA[t] && <div className="mt-0.5 text-[10.5px] text-amber-700">{NOTA_TIENDA[t]}</div>}
              </button>
            );
          })}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          <Saldo icono={<Boxes className="h-3.5 w-3.5" />} rotulo="En FULL hoy"
                 ayuda="Piezas que hay HOY en FULL de Mercado Libre, sumando las dos cuentas."
                 cifra={num(enFull)}
                 pie={full.every((x) => x.s) ? full.map((x) => `${x.c} ${num(x.s!.piezas)}`).join(" · ") : "leyendo…"} />
          <Saldo icono={<PackageX className="h-3.5 w-3.5" />} rotulo="Publicaciones sin FULL" tono="rosa"
                 ayuda="Publicaciones FULL de las dos cuentas que hoy tienen 0 piezas en el almacén de Mercado Libre."
                 cifra={num(sinFull)}
                 pie={sinFull !== null ? `de ${num(pubFull)} publicaciones FULL · ${full.map((x) => `${x.c} ${num(x.s!.en_cero)}`).join(" · ")}` : "leyendo…"} />
          <Saldo icono={<Truck className="h-3.5 w-3.5" />} rotulo="En camino a FULL · esta semana" tono="ambar"
                 ayuda="Lo que se está mandando ESTA semana a las tiendas activas: salidas validadas esta semana más las que están por validar."
                 cifra={datos ? `${num(semPzs)} pzs` : "—"}
                 pie={datos ? `de ${num(semSkus)} SKUs en ${num(semEnvios)} envíos · ${estaSemana.filter((x) => x.s?.piezas).map((x) => `${datos.tiendas[x.t].nombre} ${num(x.s!.piezas)}`).join(" · ") || "nada todavía"}` : "leyendo…"} />
          <Saldo icono={<ArrowRight className="h-3.5 w-3.5" />} rotulo={`Plan de la ${semanaActual.semana}`} tono="indigo"
                 ayuda="Lo que va en el plan de la semana (lo que puso la IA, lo que cambiaste a mano y lo que agregaste) para las tiendas activas."
                 cifra={datos ? `${num(total.a_mandar)} pzs` : "—"}
                 pie={datos ? `${num(total.skus_a_mandar)} SKUs · ${num(resumen.reemplazos)} reemplazos · ${tiendasActivas.map((t) => `${datos.tiendas[t].nombre} ${num(porTienda[t]?.a_mandar ?? 0)}`).join(" · ")}` : "calculando…"} />
        </div>
      </Tarjeta>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12.5px] text-rose-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span><b>No se pudo armar la planeación.</b> <code className="font-mono">{error}</code></span>
        </div>
      )}

      {/* ── El plan de la semana ─────────────────────────────────────────── */}
      <Tarjeta>
        <div id="plan-semana" className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-2.5">
            {params && (
              <>
                <Parametro t="Cobertura" unidad="días" valor={params.cobertura_dias} max={120}
                           ayuda="Cuántos días de venta debe aguantar el almacén del marketplace."
                           onCambio={(n) => setParams({ ...params, cobertura_dias: n })} />
                <label className="flex flex-col gap-1">
                  <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
                    <Ayuda texto="Los días de venta con que se mide la velocidad. Cambiarla vuelve a leer las ventas." lado="izq">Ventana</Ayuda>
                  </span>
                  <select value={ventana} onChange={(ev) => setVentana(Number(ev.target.value))}
                          className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 font-mono text-sm font-bold text-slate-900">
                    {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} días</option>)}
                  </select>
                </label>
                <Parametro t="Mínimo por renglón" unidad="pzs" valor={params.min_piezas} max={500}
                           ayuda="Si a un SKU le faltan menos piezas que esto, la propuesta estándar no lo sugiere."
                           onCambio={(n) => setParams({ ...params, min_piezas: n })} />
                <Parametro t="Ganador desde" unidad="ventas" valor={params.min_ventas} max={500}
                           ayuda="Ganador agotado = vendió al menos esto en la ventana, sin libre en Odoo y sin stock en el almacén (el prompt dice 3)."
                           onCambio={(n) => setParams({ ...params, min_ventas: n })} />
                <Parametro t="Dejar en bodega" unidad="pzs/SKU" valor={params.dejar_en_bodega} max={5000}
                           ayuda="Colchón por SKU que se queda en Odoo para los canales DROP (TikTok, Temu, Walmart S2H, web)."
                           onCambio={(n) => setParams({ ...params, dejar_en_bodega: n })} />
                {datos && JSON.stringify({ ...params, ventana_dias: 0 }) !== JSON.stringify({ ...datos.parametros, ventana_dias: 0 }) && (
                  <button type="button" onClick={() => setParams({ ...datos.parametros, ventana_dias: datos.ventana.dias })}
                          className="mb-0.5 inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100">
                    <RotateCcw className="h-3.5 w-3.5" /> parámetros del prompt
                  </button>
                )}
              </>
            )}
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <button type="button" onClick={llenarEstandar} disabled={!renglones.length || !listo}
                      title="Pone en el plan lo que sugiere el prompt estándar (la columna «Propuesta») para lo que todavía no está"
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                <ListChecks className="h-3.5 w-3.5" /> Llenar con la propuesta estándar
              </button>
              {vaciar ? (
                <span className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-800">
                  ¿Vaciar el plan de la semana?
                  <button type="button" onClick={vaciarPlan} className="rounded bg-rose-600 px-2 py-0.5 font-bold text-white">sí</button>
                  <button type="button" onClick={() => setVaciar(false)} className="px-1 font-semibold">no</button>
                </span>
              ) : (
                <button type="button" onClick={() => setVaciar(true)} disabled={!Object.keys(plan).length}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                  <Eraser className="h-3.5 w-3.5" /> Vaciar
                </button>
              )}
            </div>
            <GuardadoUI guardado={guardado} listo={listo} sucio={listo && firmaPlan(plan, quitadas) !== ultimaFirma.current}
                        iaEscribiendo={!!vivo} />
          </div>
        </div>

        {datos && tiendasActivas.length > 0 && (
          <div className="mt-3">
            <BuscarSku tiendas={tiendasActivas.map((t) => ({ t, d: datos.tiendas[t] }))} ventana={ventana}
                       enPlan={new Set(renglones.map((r) => r.clave))} onAgregar={agregar} />
          </div>
        )}
        {aviso && (
          <p className="mt-2 rounded-lg bg-indigo-50 px-3 py-1.5 text-[12px] text-indigo-800">
            {aviso} <button type="button" onClick={() => setAviso(null)} className="ml-2 font-semibold underline">ok</button>
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="flex max-w-full overflow-x-auto rounded-lg border border-slate-200 bg-white">
            {(["todas", ...tiendasActivas] as (Tienda | "todas")[]).map((t) => (
              <button key={t} type="button" onClick={() => setVista(t)}
                      className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-xs font-bold ${vista === t ? "bg-indigo-50 text-indigo-800" : "text-slate-500 hover:bg-slate-50"}`}>
                {t === "todas" ? "Todas las activas" : datos?.tiendas[t].nombre}
              </button>
            ))}
          </div>
          {FILTROS.filter((f) => (f.k !== "quitados" || quitados.length > 0) && (f.k !== "ia" || Object.values(plan).some((e) => e.origen === "ia"))).map((f) => (
            <button key={f.k} type="button" title={f.titulo} onClick={() => setFiltro(f.k)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${
                      filtro === f.k ? (f.k === "ia" ? "border-violet-300 bg-violet-50 text-violet-800" : "border-indigo-200 bg-indigo-50 text-indigo-800")
                        : f.k === "ia" ? "border-violet-200 bg-white text-violet-600" : "border-slate-200 bg-white text-slate-500"}`}>
              {f.k === "ia" && <Sparkles className="h-3 w-3" />}
              {f.t}<span className="font-mono text-[11px] opacity-70">
                {f.k === "quitados" ? quitadosEnVista.length : enVista.filter((r) => enFiltro(r, f.k)).length}
              </span>
            </button>
          ))}
          <label className="ml-auto flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
            <Search className="h-3.5 w-3.5 text-slate-400" />
            <input value={busca} onChange={(ev) => setBusca(ev.target.value)} placeholder="Filtrar la tabla"
                   className="w-36 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
          </label>
        </div>

        <div className="mt-3 max-h-[70vh] overflow-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[1440px] border-collapse text-[12.5px]">
            <thead className="sticky top-0 z-10">
              <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-500">
                <th className="px-3 py-2.5"><Ayuda lado="izq" texto="El SKU con el nombre de Omnicanal. Abajo, su publicación en el marketplace y, en violeta, la recomendación de la IA.">SKU · producto</Ayuda></th>
                {vista === "todas" && <th className="px-3 py-2.5"><Ayuda lado="izq" texto="A qué almacén va: FULL de Kubera, FULL de San Corpe, FBA de Amazon o WFS de Walmart.">Tienda</Ayuda></th>}
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Precio de venta HOY en esa tienda (Mercado Libre en vivo; Amazon y Walmart, el de su publicación). Sirve para planear por ticket.">Precio</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Piezas vendidas en esa tienda en la ventana y su ritmo por día. ↑ = la última semana vende más de 1.5 veces ese ritmo.">Vende</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que hay HOY en el almacén FULL del marketplace (FBA en Amazon, WFS en Walmart). En Mercado Libre se lee en vivo. «?» = no se sabe, no es 0.">Almacén FULL</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Cuántos días alcanza lo que hay en el almacén al ritmo de la ventana.">Aguanta</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que ya va hacia el almacén: salidas por validar, lo que el marketplace todavía no recibe y los borradores en Odoo. Se resta para no mandar dos veces. Toca una orden para abrirla en Envíos con su trazabilidad (los borradores abren Odoo).">En camino</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo libre en Odoo (free_qty) en CADA almacén, con su barra: el que tiene más va resaltado. «sale» marca de dónde saldría la orden: de UN almacén si ahí cabe todo (TEXCO primero); si no, se parte.">Libre Odoo</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Faltante = objetivo de cobertura − lo que hay en el almacén − lo que va en camino.">Pidió</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo libre en Odoo sumando TEXCO y TEXCO II. Si otra tienda activa pide el mismo SKU o dejas colchón para DROP, abajo dice cuánto le toca a esta tienda.">Stock total bodegas</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que sugiere el prompt estándar: el menor entre lo que pidió y lo que bodega puede (0 si queda debajo del mínimo por renglón). Es referencia: el plan lo arma la IA o tú.">Propuesta</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda lado="der" texto="Lo que va en el plan de la semana y se creará en Odoo. La casilla lo incluye o lo deja fuera sin perder la cantidad. En violeta, lo que puso la IA; en azul, lo que cambiaste.">A mandar</Ayuda></th>
                <th className="px-3 py-2.5"><Ayuda lado="der" texto="Aprobado: bodega cubre lo pedido. Recorte: Odoo no alcanza, va lo que hay. Pendiente: sin dato de Odoo (no es un cero). Cubierto: ya alcanza.">Estado</Ayuda></th>
                <th className="px-2 py-2.5"><span className="sr-only">Quitar</span></th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((r) => (
                <FilaUI key={r.clave} r={r} valor={filtro === "quitados" ? 0 : cantidad(r)} entrada={plan[r.clave]}
                        origen={origen.get(r.clave) ?? null} minimo={params?.min_piezas ?? 0} onAbrirEnvio={onAbrirEnvio}
                        resaltada={resaltados.has(r.clave)}
                        conTienda={vista === "todas"} nombreTienda={datos?.tiendas[r.tienda].nombre ?? r.tienda}
                        quitada={filtro === "quitados"}
                        onCambio={(v) => fijar(r, v)} onAlternar={() => alternar(r)}
                        onQuitar={() => quitar(r)} onRestaurar={() => restaurar(r)} />
              ))}
              {!datos && (
                <tr><td colSpan={14} className="px-4 py-12 text-center text-sm text-slate-500" style={{ background: FONDO_RAYADO }}>
                  {cargando ? "Leyendo ventas, publicaciones (verificando en vivo con Mercado Libre), envíos y lo libre en Odoo… tarda unos 25 segundos."
                            : "Sin planeación: revisa el error de arriba."}
                </td></tr>
              )}
              {datos && visibles.length === 0 && (
                <tr><td colSpan={14} className="px-4 py-10 text-center text-sm text-slate-500">
                  {!tiendasActivas.length ? "Prende al menos una tienda."
                    : filtro === "mandar" && !Object.keys(plan).length
                      ? <>El plan de la {semanaActual.semana} está vacío. Pídele a la IA que lo arme (arriba, «Crear FULL con IA»)
                          o <button type="button" onClick={llenarEstandar} className="font-semibold text-indigo-600 underline">llénalo con la propuesta estándar</button>.</>
                      : "Nada en este filtro."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3">
          <span className="text-sm text-indigo-900">
            <b className="font-mono tabular-nums">{num(total.skus_a_mandar)}</b> SKUs a FULL ·{" "}
            <b className="font-mono tabular-nums">{num(total.a_mandar)}</b> piezas ·{" "}
            <b className="font-mono tabular-nums">{num(resumen.reemplazos)}</b> reemplazos en {tiendasActivas.length} tienda{tiendasActivas.length === 1 ? "" : "s"}
            <span className="text-indigo-700/70"> · {semanaActual.semana}{resumen.de_ia ? ` · ${num(resumen.de_ia)} de la IA` : ""}</span>
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={copiarSkus} disabled={!total.skus_a_mandar}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40">
              <ClipboardCopy className="h-4 w-4" /> Copiar SKUs
            </button>
            <button type="button" onClick={() => void descargar()} disabled={!renglones.length}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40">
              <Download className="h-4 w-4" /> Descargar Excel
            </button>
            <button type="button" onClick={() => abrirPrompt(porCargar)} disabled={!porCargar.length}
                    title={porCargar.length ? `${porCargar.map((b) => b.orden).join(", ")}: en borrador y sin guía`
                      : "Se activa cuando esta semana hay una orden de Mercado Libre creada en Odoo (en borrador) que todavía no tiene su guía."}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-violet-300 bg-white px-3 py-2 text-sm font-extrabold uppercase tracking-wide text-violet-700 hover:bg-violet-50 disabled:opacity-40">
              <Sparkles className="h-4 w-4" /> Cargar FULL con prompt{cuentasPorCargar > 1 ? ` (${cuentasPorCargar} cuentas)` : ""}
            </button>
            <button type="button" onClick={() => setRevisar(true)} disabled={!total.skus_a_mandar}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-40">
              Revisar y crear <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </Tarjeta>

      {datos && (
        <OrdenesSemana semana={semanaActual} ordenes={ordenesDe(semanaActual.clave)} datos={datos} rol={rol} esActual
                       onPrompt={(b) => abrirPrompt(deLaCuenta(b))} onGuia={() => void cargar(true, ventana)} />
      )}

      <p className="text-xs leading-relaxed text-slate-400">
        {datos ? `Fuente: ${datos.fuente}. Leído ${new Date(datos.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.` : ""}
        {" "}El plan y el chat de la semana se guardan en la bitácora de acciones (ops.process_log); no tocan Odoo.
      </p>

      {revisar && params && datos && (
        <ConfirmarFull
          pedidos={tiendasActivas.map((t) => ({
            tienda: t, lineas: renglones.filter((r) => r.tienda === t && cantidad(r) > 0)
              .map((r) => ({ sku: r.sku, cantidad: cantidad(r), sugerido: r.propuesta })),
          })).filter((p) => p.lineas.length)}
          semana={`${semanaActual.semana} · ${semanaActual.rango}`} params={params} rol={rol}
          onDescargar={() => void descargar()} onCerrar={() => setRevisar(false)} onCreado={alCrear} />
      )}
      {prompt && <PromptFull grupos={prompt.grupos} aviso={prompt.aviso} onCerrar={() => setPrompt(null)} />}
    </div>
  );
}

function GuardadoUI({ guardado, listo, sucio, iaEscribiendo }: {
  guardado: { cuando: string | null; quien: string | null; error?: string } | null; listo: boolean; sucio: boolean;
  iaEscribiendo?: boolean;
}) {
  if (!listo) return <span className="text-[11px] text-slate-400">leyendo el plan guardado…</span>;
  if (guardado?.error) return <span className="text-[11px] font-semibold text-rose-700">no se pudo guardar: {guardado.error}</span>;
  if (sucio && iaEscribiendo) return <span className="text-[11px] text-violet-700">la IA está escribiendo: se guarda al terminar</span>;
  if (sucio) return <span className="text-[11px] text-slate-500">guardando…</span>;
  if (!guardado?.cuando) return <span className="text-[11px] text-slate-400">plan vacío: nada que guardar todavía</span>;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-700">
      <CheckCircle2 className="h-3.5 w-3.5" /> plan guardado · {fecha({ ts: guardado.cuando })}
      {guardado.quien ? ` · ${guardado.quien.split("@")[0]}` : ""}
    </span>
  );
}

function alertasDe(renglones: Renglon[], datos: PropuestaFull | null) {
  const texto: Record<string, (r: Renglon) => string> = {
    sin_categoria: () => "la publicación no tiene categoría",
    reciclado: (r) => `el título del marketplace («${(r.titulo_mkt ?? "").slice(0, 60)}») no se parece al de Odoo («${(r.nombre_odoo ?? r.nombre ?? "").slice(0, 60)}»)`,
    medidas: () => "caja master sospechosa: peso ≤ 0.5 kg con medidas ~60×41×41",
    cerrada_en_ml: () => "la publicación está cerrada o inactiva en Mercado Libre",
    publicacion_de_otro_sku: (r) => `su publicación ${r.listing_id ?? ""} la declara Mercado Libre como ${r.sku_publicacion ?? "otro SKU"}: el stock FULL se cuenta allá y aquí va en 0`,
  };
  const salida: { tienda: string; tienda_llave: Tienda; sku: string; tipo: string; detalle: string }[] =
    renglones.flatMap((r) => r.alertas.map((a) => ({
      tienda: datos?.tiendas[r.tienda].nombre ?? r.tienda, tienda_llave: r.tienda, sku: r.sku, tipo: a,
      detalle: texto[a]?.(r) ?? a })));
  for (const r of renglones) {
    if (r.tienda.startsWith("meli") && r.publicada && !r.verificada) {
      salida.push({ tienda: datos?.tiendas[r.tienda].nombre ?? r.tienda, tienda_llave: r.tienda, sku: r.sku,
                    tipo: "sin_verificar", detalle: "Mercado Libre no la confirmó en vivo: el dato es del sync" });
    }
  }
  return salida;
}

function Parametro({ t, unidad, valor, max, ayuda, onCambio }: {
  t: string; unidad: string; valor: number; max: number; ayuda: string; onCambio: (n: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400"><Ayuda texto={ayuda} lado="izq">{t}</Ayuda></span>
      <span className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1">
        <input type="text" inputMode="numeric" value={valor}
               onChange={(ev) => onCambio(Math.max(0, Math.min(max, parseInt(ev.target.value.replace(/[^\d]/g, ""), 10) || 0)))}
               className="w-12 bg-transparent text-right font-mono text-sm font-bold tabular-nums text-slate-900 focus:outline-none" />
        <span className="text-[11px] text-slate-400">{unidad}</span>
      </span>
    </label>
  );
}

function Saldo({ icono, rotulo, cifra, pie, ayuda, tono = "slate" }: {
  icono: ReactNode; rotulo: string; cifra: string; pie: string; ayuda: string; tono?: "slate" | "rosa" | "ambar" | "indigo";
}) {
  const c = {
    slate: "border-slate-200 bg-white text-slate-900",
    rosa: "border-rose-200 bg-rose-50 text-rose-800",
    ambar: "border-amber-200 bg-amber-50 text-amber-800",
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-900",
  }[tono];
  return (
    <div className={`rounded-xl border px-3.5 py-3 ${c}`}>
      <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[.06em]">
        <span className="opacity-80">{icono}</span><Ayuda texto={ayuda} lado="izq">{rotulo}</Ayuda>
      </div>
      <div className="mt-1 text-2xl font-extrabold leading-none tracking-tight tabular-nums">{cifra}</div>
      <div className="mt-1 text-[11.5px] opacity-80">{pie}</div>
    </div>
  );
}

const ESTADO: Record<Renglon["estado"], { t: string; c: string }> = {
  aprobado: { t: "aprobado", c: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  recorte: { t: "recorte", c: "border-amber-300 bg-amber-50 text-amber-800" },
  pendiente: { t: "pendiente", c: "border-dashed border-slate-300 text-slate-500" },
  cubierto: { t: "cubierto", c: "border-slate-200 bg-white text-slate-400" },
};

/**
 * De qué almacén saldría cada renglón del plan: la MISMA regla que usa Odoo al crear
 * (`fulfillment_full.repartir_almacenes` y `vista_previa`): tienda por tienda, UN almacén si
 * ahí cabe todo lo de esa tienda (TEXCO primero); si no, cada renglón completo al primero que
 * lo cubra; si ninguno, se parte. Lo que toma una tienda ya no lo tiene la siguiente.
 */
function almacenesDelPlan(renglones: Renglon[], cantidad: (r: Renglon) => number, almacenes: string[]) {
  const salida = new Map<string, string>();
  const restante = new Map<number, Record<string, number>>();
  const libre = (r: Renglon, a: string) => Math.max(0, (restante.get(r.product_id!) ?? r.libre ?? {})[a] ?? 0);
  const tomar = (r: Renglon, partes: Record<string, number>) => {
    const actual = { ...(restante.get(r.product_id!) ?? r.libre ?? {}) };
    for (const [a, n] of Object.entries(partes)) actual[a] = (actual[a] ?? 0) - n;
    restante.set(r.product_id!, actual);
    salida.set(r.clave, Object.keys(partes).join(" + "));
  };
  for (const t of TIENDAS) {
    const van = renglones.filter((r) => r.tienda === t && r.product_id !== null && r.libre && cantidad(r) > 0)
      .map((r) => ({ r, n: Math.min(cantidad(r), almacenes.reduce((s, a) => s + libre(r, a), 0)) }))
      .filter((x) => x.n > 0);
    const unico = almacenes.find((a) => van.length > 0 && van.every((x) => libre(x.r, a) >= x.n));
    for (const x of van) {
      const entero = unico ?? almacenes.find((a) => libre(x.r, a) >= x.n);
      if (entero) { tomar(x.r, { [entero]: x.n }); continue; }
      let falta = x.n;
      const partes: Record<string, number> = {};
      for (const a of almacenes) {
        const toma = Math.min(libre(x.r, a), falta);
        if (toma > 0) { partes[a] = toma; falta -= toma; }
      }
      tomar(x.r, partes);
    }
  }
  return salida;
}

function FilaUI({
  r, valor, entrada, resaltada, conTienda, nombreTienda, quitada, origen, minimo, onAbrirEnvio,
  onCambio, onAlternar, onQuitar, onRestaurar,
}: {
  r: Renglon; valor: number; entrada: EntradaPlan | undefined; resaltada: boolean; conTienda: boolean;
  nombreTienda: string; quitada: boolean;
  /** De qué almacén saldría («TEXCO», «TEXCO II» o los dos). */
  origen: string | null;
  minimo: number;
  onAbrirEnvio?: (orden: string) => void;
  onCambio: (v: string) => void; onAlternar: () => void; onQuitar: () => void; onRestaurar: () => void;
}) {
  // Lo más que pide la cobertura: el faltante, redondeado a cajas completas si hay caja.
  const cobertura = r.pidio > 0 ? (r.caja ? Math.ceil(r.pidio / r.caja) * r.caja : r.pidio) : 0;
  const totalBodegas = r.libre ? Object.values(r.libre).reduce((a, n) => a + n, 0) : null;
  const mayor = r.libre ? Math.max(0, ...Object.values(r.libre)) : 0;
  const e = ESTADO[r.estado];
  const tonoAguanta = r.aguanta === null ? "text-slate-300"
    : r.aguanta < 7 ? "text-rose-700" : r.aguanta < 15 ? "text-amber-700" : "text-slate-600";
  const deIA = entrada?.origen === "ia";
  const fuera = !!entrada && !entrada.incluido;
  const reemplazoDe = entrada?.reemplazo_de;
  return (
    <tr className={`border-t border-slate-100 align-top ${quitada || fuera ? "opacity-60" : valor > 0 ? "" : "bg-slate-50/40"} ${
      resaltada ? "animate-resalta shadow-[inset_3px_0_0_#7c3aed]" : ""}`}>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12.5px] font-bold text-slate-900">{r.sku}</span>
          {r.stock === 0 && <span className="rounded border border-rose-200 bg-rose-50 px-1.5 text-[9.5px] font-bold uppercase text-rose-700">sin stock</span>}
          {r.ganador_agotado && <span className="rounded border border-violet-200 bg-violet-50 px-1.5 text-[9.5px] font-bold uppercase text-violet-700">ganador agotado</span>}
          {reemplazoDe && (
            <span className="rounded bg-violet-600 px-1.5 text-[9.5px] font-extrabold uppercase text-white"
                  title={`${reemplazoDe} no se puede surtir: no hay existencia en Odoo ni en el almacén`}>
              reemplazo de {reemplazoDe}
            </span>
          )}
          {deIA && <span className="inline-flex items-center gap-0.5 rounded border border-violet-200 bg-violet-50 px-1.5 text-[9.5px] font-bold uppercase text-violet-700"><Sparkles className="h-2.5 w-2.5" />IA</span>}
          {entrada?.origen === "estandar" && <span className="rounded border border-slate-200 bg-white px-1.5 text-[9.5px] font-bold uppercase text-slate-500">estándar</span>}
          {r.agregado && !reemplazoDe && <span className="rounded border border-indigo-200 bg-indigo-50 px-1.5 text-[9.5px] font-bold uppercase text-indigo-700">agregado</span>}
          {r.alertas.includes("reciclado") && r.titulo_urgente && (
            <span className="rounded border border-amber-300 bg-amber-50 px-1.5 text-[9.5px] font-bold uppercase text-amber-700"
                  title={`El título de la publicación («${r.titulo_mkt}») no se parece al de Odoo («${r.nombre_odoo}») ni al del catálogo. Compara las fotos en Análisis.`}>
              ¿reciclado?
            </span>
          )}
        </div>
        <div className="max-w-[330px] truncate text-[11px] text-slate-500" title={r.nombre ?? ""}>{r.nombre ?? "—"}</div>
        {entrada?.motivo && (
          <div className="max-w-[360px] text-[11px] font-medium leading-snug text-violet-700" title={entrada.motivo}>
            IA: {entrada.motivo}
          </div>
        )}
        <div className="flex items-center gap-1.5 text-[10.5px]">
          {r.listing_id ? (
            <a href={r.url ?? "#"} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-mono text-indigo-500 hover:underline">
              {r.listing_id}<ExternalLink className="h-2.5 w-2.5" />
            </a>
          ) : <span className="text-amber-700">sin publicación registrada</span>}
          {r.verificada && <span className="text-emerald-600">· en vivo</span>}
          {r.en_almacen === false && r.tienda.startsWith("meli") && <span className="text-slate-400">· aún no es FULL</span>}
          {r.caja ? <span className="text-slate-400">· caja de {r.caja}</span> : null}
        </div>
      </td>
      {conTienda && <td className="px-3 py-2 text-[11.5px] font-semibold text-slate-600">{nombreTienda}</td>}
      <td className="px-3 py-2 text-right font-mono text-[12px] tabular-nums text-slate-700">{pesos(r.precio)}</td>
      <td className="px-3 py-2 text-right">
        <div className="font-mono font-bold tabular-nums text-slate-800">{num(r.vv)}</div>
        <div className="text-[10.5px] text-slate-400" title={`Últimos 7 días: ${r.v7}`}>
          {r.velocidad.toFixed(1)}/día{r.sube ? <span className="font-bold text-emerald-600"> ↑</span> : null}
        </div>
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {r.stock === null
          ? <span className="rounded px-1.5 text-slate-400" style={{ background: FONDO_RAYADO }} title="No se sabe: no es 0">?</span>
          : <span className={r.stock === 0 ? "font-bold text-rose-700" : "text-slate-700"}>{num(r.stock)}</span>}
      </td>
      <td className={`px-3 py-2 text-right font-mono text-[12px] tabular-nums ${tonoAguanta}`}>
        {r.aguanta === null ? "—" : r.aguanta >= 99 ? "99+ d" : `${Math.floor(r.aguanta)} d`}
      </td>
      <td className="px-3 py-2 text-right">
        {r.en_camino > 0
          ? <div className="font-mono tabular-nums text-amber-700" title={r.camino.join("\n")}>{num(r.en_camino)}</div>
          : <div className="font-mono text-slate-300">0</div>}
        {(r.camino_ordenes ?? []).map((o, i) => (
          <button key={`c${i}`} type="button" disabled={!o.orden || !onAbrirEnvio}
                  onClick={() => o.orden && onAbrirEnvio?.(o.orden)}
                  title={`Abrir ${o.orden} en Envíos: ${o.estado}, ${o.piezas} pzs`}
                  className="mt-0.5 block w-full whitespace-nowrap text-right text-[10.5px] text-indigo-600 hover:underline disabled:text-slate-500 disabled:no-underline">
            <span className="font-mono font-bold">{o.orden}</span> · {num(o.piezas)} <span className="text-slate-400">{o.estado}</span>
          </button>
        ))}
        {r.borrador > 0 && <div className="mt-0.5 text-[10.5px] text-indigo-600" title={r.borradores.join(", ")}>+{num(r.borrador)} borrador</div>}
        {(r.borradores_ordenes ?? []).map((o, i) => (
          <a key={`b${i}`} href={o.url ?? "#"} target="_blank" rel="noreferrer" title={`Abrir el borrador ${o.orden} en Odoo`}
             className="block whitespace-nowrap text-right text-[10.5px] text-indigo-500 hover:underline">
            <span className="font-mono">{o.orden}</span> · {num(o.piezas)} <span className="text-slate-400">borrador</span>
          </a>
        ))}
      </td>
      <td className="px-3 py-2 text-right text-[11.5px]">
        {r.libre ? (
          <div className="ml-auto flex w-[140px] flex-col gap-1">
            {Object.entries(r.libre).map(([alm, n]) => {
              const esMayor = n > 0 && n === mayor;
              const sale = valor > 0 && !!origen?.split(" + ").includes(alm);
              return (
                <div key={alm} title={sale ? `La orden de este SKU saldría de ${alm}` : undefined}
                     className={`rounded-md px-1.5 py-0.5 ${sale ? "bg-emerald-50 ring-1 ring-emerald-300" : ""}`}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className={`text-[10px] font-semibold ${esMayor ? "text-slate-700" : "text-slate-400"}`}>
                      {alm}{sale && <span className="ml-1 font-bold text-emerald-700">· sale</span>}
                    </span>
                    <span className={`font-mono tabular-nums ${n === 0 ? "text-slate-300" : esMayor ? "font-extrabold text-slate-900" : "text-slate-600"}`}>
                      {num(n)}
                    </span>
                  </div>
                  <div className="mt-0.5 h-1 rounded bg-slate-100">
                    <div className={`h-1 rounded ${esMayor ? "bg-emerald-500" : "bg-slate-300"}`}
                         style={{ width: `${mayor ? Math.round((n / mayor) * 100) : 0}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        ) : <span className="text-slate-400">no está en Odoo</span>}
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-700">{num(r.pidio)}</td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-700" title={r.repartido}>
        {totalBodegas === null ? <span className="text-slate-400">?</span> : num(totalBodegas)}
        {r.bodega !== null && totalBodegas !== null && r.bodega !== totalBodegas && (
          <div className="text-[10px] text-violet-700">{r.repartido ? "repartido: " : "sin el colchón: "}le tocan {num(r.bodega)}</div>
        )}
      </td>
      <td className="px-3 py-2 text-right font-mono font-bold tabular-nums text-indigo-700">{r.propuesta ? num(r.propuesta) : "—"}</td>
      <td className="px-3 py-2 text-right">
        <div className="flex items-center justify-end gap-1.5">
          {entrada && !quitada && (
            <input type="checkbox" checked={entrada.incluido} onChange={onAlternar}
                   title={entrada.incluido ? "Va en el plan: desmárcalo para dejarlo fuera sin perder la cantidad" : "Fuera del plan: márcalo para incluirlo"}
                   aria-label={`Incluir ${r.sku}`} className="h-4 w-4 accent-violet-600" />
          )}
          <input type="text" inputMode="numeric" value={entrada ? String(entrada.cantidad || "") : ""} placeholder="0"
                 disabled={quitada} onChange={(ev) => onCambio(ev.target.value)}
                 className={`w-20 rounded-md border px-2 py-1 text-right font-mono text-[13px] font-bold tabular-nums focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 ${
                   deIA ? "border-violet-300 bg-violet-50 text-violet-900"
                     : entrada?.origen === "manual" ? "border-indigo-300 bg-indigo-50 text-indigo-900" : "border-slate-200 text-slate-900"}`} />
        </div>
        {r.bodega !== null && valor > r.bodega && (
          <div className="mt-0.5 text-[10px] font-semibold text-amber-700">más de lo libre: se recortará</div>
        )}
        {valor > 0 && r.estado === "cubierto" && (
          <div className="mt-0.5 text-[10px] font-semibold text-amber-700"
               title="Lo que hay en FULL, en camino y en borradores ya alcanza la cobertura: la propuesta es 0.">ya está cubierto</div>
        )}
        {valor > 0 && r.pidio > 0 && valor > cobertura && (
          <div className="mt-0.5 text-[10px] font-semibold text-amber-700">más de lo que pide la cobertura ({num(cobertura)})</div>
        )}
        {valor > 0 && valor < minimo && (
          <div className="mt-0.5 text-[10px] font-semibold text-amber-700">debajo del mínimo por renglón ({num(minimo)})</div>
        )}
      </td>
      <td className="px-3 py-2">
        <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-bold ${e.c}`}
              style={r.estado === "pendiente" ? { background: FONDO_RAYADO } : undefined}>{e.t}</span>
      </td>
      <td className="px-2 py-2 text-right">
        {quitada ? (
          <button type="button" onClick={onRestaurar} title="Regresar este SKU a la planeación"
                  className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-2 py-1 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50">
            <RotateCcw className="h-3.5 w-3.5" /> Restaurar
          </button>
        ) : (
          <button type="button" onClick={onQuitar} title="Quitar este SKU de la planeación (se puede restaurar en «Quitados»)"
                  aria-label={`Quitar ${r.sku}`}
                  className="rounded-md p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-600">
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </td>
    </tr>
  );
}

/**
 * Las órdenes que creó el panel ESA semana y siguen en borrador: el último paso es
 * cargarlas en el marketplace («CARGAR FULL CON PROMPT», sólo Mercado Libre y sólo la
 * semana en curso) y adjuntar su guía. Cuánto lleva cada orden sin completarse (todas,
 * no sólo las del panel) se ve en Análisis.
 */
function OrdenesSemana({ semana, ordenes, datos, rol, esActual, onPrompt, onGuia }: {
  semana: Semana; ordenes: BorradorFull[]; datos: PropuestaFull; rol: Rol; esActual: boolean;
  onPrompt?: (b: BorradorFull) => void; onGuia?: () => void;
}) {
  const [guia, setGuia] = useState<number | null>(null);
  return (
    <Tarjeta>
      <Ceja>Órdenes de la semana {semana.semana} · creadas desde aquí · en borrador · {ordenes.length}</Ceja>
      {!ordenes.length ? (
        <p className="mt-1 text-[12px] text-slate-500">
          {esActual ? "Todavía no se crea ninguna esta semana: «Revisar y crear» las crea en Odoo en borrador."
            : "Ninguna orden de esa semana sigue en borrador. Las que ya se confirmaron están en Envíos, con la misma semana."}
        </p>
      ) : (
        <>
          <p className="mt-1 text-[12px] text-slate-500">
            Carga cada una en Mercado Libre con el prompt: el agente arma el envío a Full y sube aquí su guía. Mientras no
            tenga guía, el botón sigue activo.
          </p>
          <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
            {ordenes.map((b) => {
              const ml = !!b.tienda?.startsWith("meli");
              return (
                <div key={b.id} className="px-3 py-2 text-[12.5px]">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <a href={b.url} target="_blank" rel="noreferrer" className="hover:underline">
                      <span className="font-mono font-bold text-slate-800">{b.orden}</span>
                      <span className="text-slate-400"> · {b.tienda ? datos.tiendas[b.tienda]?.nombre : "sin tienda"}
                        {b.almacen ? ` · ${b.almacen}` : ""} · {num(b.piezas)} pzs · {b.creada ? fecha({ ts: b.creada }) : ""}</span>
                    </a>
                    <span className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
                      {b.prueba && <span className="rounded bg-amber-100 px-1.5 text-[10px] font-bold text-amber-800">PRUEBA</span>}
                      {b.guia_pdf
                        ? <span className="inline-flex items-center gap-1 font-semibold text-emerald-700"><CheckCircle2 className="h-3.5 w-3.5" /> guía adjunta · {b.guia_pdf}</span>
                        : <span className="text-amber-700">sin guía</span>}
                      {b.referencia && <span className="text-slate-400">ref «{b.referencia}»</span>}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-3">
                    {ml && esActual && !b.guia_pdf && onPrompt && (
                      <button type="button" onClick={() => onPrompt(b)}
                              className="inline-flex items-center gap-1 rounded-md border border-violet-300 bg-violet-50 px-2 py-1 text-[11px] font-extrabold uppercase tracking-wide text-violet-700 hover:bg-violet-100">
                        <Sparkles className="h-3 w-3" /> Cargar FULL con prompt
                      </button>
                    )}
                    {rol === "admin" && !b.guia_pdf && (guia === b.id
                      ? <GuiaOrden orden={{ id: b.id, orden: b.orden }} compacta onAdjuntada={onGuia} />
                      : <button type="button" onClick={() => setGuia(b.id)}
                                className="text-[11px] font-semibold text-indigo-600 hover:underline">Adjuntar guía</button>)}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </Tarjeta>
  );
}

/** Una semana anterior: su chat, su plan tal como quedó y sus órdenes. Sólo consulta. */
function SemanaPasada({ semana, consulta, error, datos, ordenes, rol }: {
  semana: Semana | null; consulta: EstadoSemana | null; error: string | null; datos: PropuestaFull | null;
  ordenes: BorradorFull[]; rol: Rol;
}) {
  const [abierto, setAbierto] = useState(true);
  if (!semana) return null;
  const entradas = (consulta?.plan?.entradas ?? []).filter((e) => e.incluido && e.cantidad > 0);
  const nombre = (t: Tienda) => datos?.tiendas[t]?.nombre ?? t;
  return (
    <div className="mt-4 flex flex-col gap-3">
      <Tarjeta>
        <Ceja>Planeación semanal {semana.semana} · {rangoSemana(semana.lunes)} · semana cerrada</Ceja>
        <h2 className="mt-1 text-[22px] font-extrabold tracking-tight text-slate-900">Crear FULL · {semana.semana}</h2>
        <p className="mt-0.5 max-w-2xl text-[13px] text-slate-500">
          Así quedó el plan y el chat de esa semana. Sólo se planea la semana en curso: vuelve a ella con el selector de arriba.
        </p>
        {error && <p className="mt-2 text-[12px] text-rose-700">No se pudo leer esa semana: {error}</p>}
        {!consulta && !error && <p className="mt-3 text-sm text-slate-400">Leyendo la semana…</p>}
        {consulta && (
          <ChatSemana semana={consulta.semana} esActual={false} turnos={consulta.turnos} vivo={null} datos={datos}
                      totales={consulta.resumen} abierto={abierto} onAbrir={setAbierto} onEnviar={() => undefined}
                      onVerIA={() => document.getElementById("plan-pasado")?.scrollIntoView({ behavior: "smooth" })} />
        )}
      </Tarjeta>
      {consulta && (
        <Tarjeta>
          <div id="plan-pasado">
            <Ceja>
              El plan de la {semana.semana} · {num(consulta.resumen.skus)} SKUs · {num(consulta.resumen.piezas)} pzs
              {consulta.plan?.guardado ? ` · guardado ${fecha({ ts: consulta.plan.guardado })}` : ""}
              {consulta.plan?.quien ? ` por ${consulta.plan.quien.split("@")[0]}` : ""}
            </Ceja>
          </div>
          {!entradas.length ? (
            <p className="mt-2 text-[12.5px] text-slate-500">Esa semana no se guardó ningún plan en el panel.</p>
          ) : (
            <div className="mt-2 max-h-[60vh] overflow-auto rounded-xl border border-slate-200">
              <table className="w-full min-w-[720px] text-[12.5px]">
                <thead className="sticky top-0 bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-500">
                  <tr>
                    <th className="px-3 py-2">SKU</th><th className="px-3 py-2">Tienda</th>
                    <th className="px-3 py-2 text-right">Piezas</th><th className="px-3 py-2">Quién · por qué</th>
                  </tr>
                </thead>
                <tbody>
                  {entradas.sort((a, b) => b.cantidad - a.cantidad).map((e) => (
                    <tr key={`${e.tienda}|${e.sku}`} className="border-t border-slate-100 align-top">
                      <td className="px-3 py-1.5 font-mono font-bold text-slate-800">
                        {e.sku}
                        {e.reemplazo_de && <span className="ml-1.5 rounded bg-violet-600 px-1.5 text-[9.5px] font-extrabold uppercase text-white">reemplazo de {e.reemplazo_de}</span>}
                      </td>
                      <td className="px-3 py-1.5 text-slate-600">{nombre(e.tienda)}</td>
                      <td className="px-3 py-1.5 text-right font-mono font-bold tabular-nums">{num(e.cantidad)}</td>
                      <td className="px-3 py-1.5 text-[11.5px] text-slate-500">
                        {e.origen === "ia" ? <span className="font-semibold text-violet-700">IA</span> : e.origen === "estandar" ? "propuesta estándar" : "a mano"}
                        {e.motivo ? ` · ${e.motivo}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Tarjeta>
      )}
      {datos && <OrdenesSemana semana={semana} ordenes={ordenes} datos={datos} rol={rol} esActual={false} />}
    </div>
  );
}

/** El interruptor que deja a «Crear FULL» escribir en Odoo. Sólo lo mueve un admin. */
function EstadoInterruptor({ interruptor, rol, onCambio }: {
  interruptor: Interruptor; rol: Rol; onCambio: () => void;
}) {
  const [preguntar, setPreguntar] = useState(false);
  const [moviendo, setMoviendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const encendido = interruptor.encendido;
  const mover = async (valor: boolean) => {
    setMoviendo(true);
    setError(null);
    try {
      const motivo = encodeURIComponent(valor ? "encendido desde Crear FULL" : "apagado desde Crear FULL");
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/interruptor?encendido=${valor}&motivo=${motivo}`,
                                  { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.motivo ?? d.detail ?? `HTTP ${r.status}`);
      setPreguntar(false);
      onCambio();
    } catch (e: unknown) {
      setError(mensaje(e));
    } finally {
      setMoviendo(false);
    }
  };
  return (
    <div className="flex max-w-[400px] flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        <span title={interruptor.actualizado_por ? `Lo movió ${interruptor.actualizado_por} el ${dia(interruptor.actualizado_at)}` : "Nadie lo ha movido: apagado por omisión."}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${
                encendido ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
          <Power className="h-3 w-3" /> Crear en Odoo: {encendido ? "encendido" : "apagado"}
        </span>
        {rol === "admin" && !preguntar && (
          <button type="button" onClick={() => (encendido ? void mover(false) : setPreguntar(true))} disabled={moviendo}
                  className="text-[11px] font-semibold text-indigo-600 hover:underline disabled:opacity-50">
            {encendido ? "apagar" : "encender"}
          </button>
        )}
      </div>
      {preguntar && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11.5px] leading-relaxed text-amber-900">
          Al encender, «Crear FULL» escribe en Odoo cotizaciones en <b>borrador</b> (una por tienda y almacén, con el socio
          fijo de cada tienda, precio 0 y sin impuestos) y adjunta la guía que subas. <b>No confirma ni reserva stock.</b>
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setPreguntar(false)} className="rounded-md px-2 py-1 font-semibold text-amber-800 hover:bg-amber-100">
              Cancelar
            </button>
            <button type="button" onClick={() => void mover(true)} disabled={moviendo}
                    className="rounded-md bg-amber-600 px-2.5 py-1 font-bold text-white hover:bg-amber-700 disabled:opacity-50">
              Sí, encender
            </button>
          </div>
        </div>
      )}
      {error && <span className="text-[11px] text-rose-700">No se pudo: {error}</span>}
    </div>
  );
}
