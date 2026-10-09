"use client";

/**
 * INVENTARIO · Órdenes de venta — la orden PROPIA del panel (folio OV-00001…).
 *
 * Brandon, 2-oct-2026 (ClickUp 86bcbfnkw): la orden de venta —el documento que
 * le dice al almacén qué surtir— vivía sólo en Odoo y el panel nada más la
 * mandaba para allá. Aquí nace, se confirma, aparta stock, se entrega a la
 * paquetería y se cancela DENTRO de Omnicanal, sin escribir en Odoo, en
 * WooCommerce ni en ningún marketplace.
 *
 * EL MODELO (0064/0065 de Eduardo, 6-oct-2026; el contrato está en
 * docs/MIGRACION_0064_0065_GUIA_AGENTE.md). Cambió lo que esta lista enseña:
 *   · La orden sólo vive en BODEGAS DE KUBERA y la bodega va POR RENGLÓN. Ya no
 *     hay «foto de stock de Odoo» contra la que reservar: confirmar APARTA, todo
 *     o nada, del saldo de cada bodega. Por eso se fue la pastilla «Stock base»
 *     y en su lugar se dice EN QUÉ BODEGAS se puede capturar.
 *   · Las banderas (`ordenes_venta`, `ov_generacion_auto`) son filas que enciende
 *     un ACTA, no la pantalla: aquí se LEEN, con quién y cuándo. El interruptor
 *     movible de «Generación automática» y su diálogo ya no existen.
 *   · Una confirmada entrega renglón por renglón: «entrega parcial» es un estado
 *     de su apartado, y la fila dice cuántas piezas salieron.
 *   · El canal puede cancelar con el paquete en camino. La orden se queda
 *     confirmada esperando que Bodega conteste «¿salió?»: es lo ÚNICO de la
 *     lista que le pide algo a una persona, y por eso salta a la vista.
 *
 * Esta página es la LISTA y el marco. El documento, las ventas de marketplace y
 * la traza animada son componentes aparte (components/ordenes/).
 *
 * Por qué está armada así:
 *   · La dirección viaja en el # ('' = lista · '#nueva' · '#OV-00012') y no en
 *     `useSearchParams`, que rompe el pre-render de la página. Recargar no te
 *     saca de la orden y la liga de una orden se puede mandar por chat.
 *   · El documento se pinta EN LUGAR de la lista, no encima. Los filtros, la
 *     página y lo ya leído viven en este componente, así que al volver la lista
 *     está como se dejó (y se refresca sola, sin vaciarse).
 *   · Salir de un documento con cambios sin guardar SIEMPRE pregunta. Los
 *     botones del documento tienen su diálogo; lo que no pasa por ellos —«Atrás»
 *     del navegador, las pestañas de Inventario, las ligas del navbar— se ataja
 *     AQUÍ, que es donde llegan esos eventos. `beforeunload` sólo cubre cerrar o
 *     recargar la pestaña: la navegación de cliente de Next no lo dispara.
 *   · Los KPIs son los filtros: se cuenta TODA la tabla, no la página que se ve.
 *   · La lista se relee cada 30 s sólo con la pestaña a la vista y sin documento
 *     abierto. Un refresco nunca vacía la tabla ni interrumpe una lectura que
 *     pidió la persona; si falla, se queda lo último leído y se dice.
 *   · Quién puede qué lo decide el backend (`estado.yo`, `permisos` de cada
 *     orden). Aquí sólo se apagan botones, y cada botón apagado dice por qué.
 *   · Sin las migraciones 0064 y 0065 la pestaña queda en espera con un aviso;
 *     no truena ni arrastra a sus hermanas.
 *   · «Sin dato» nunca se pinta como 0: un conteo que no llegó es «—».
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertTriangle, ChevronRight, ClipboardList, Loader2, MessageSquare, Paperclip, Plus,
  RefreshCw, ScanSearch, Search, ShoppingBag, Warehouse, X, Zap,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import InventarioPestanas from "@/components/InventarioPestanas";
import { OrdenDocumento } from "@/components/ordenes/OrdenDocumento";
import { Traza } from "@/components/ordenes/Traza";
import { VentasMarketplace } from "@/components/ordenes/VentasMarketplace";
import { conciliar, leerEstado, listarOrdenes } from "@/components/ordenes/api";
import type {
  Bandera, Bodega, DevolucionEstado, EstadoModulo, FiltroEstado, ListaOrdenes, Orden, OrdenResumen,
  RespConciliar, VentaMarketplace,
} from "@/components/ordenes/tipos";
import {
  Aviso, Boton, CANALES, COLOR_ESTADO, ChipEstado, ChipReserva, ROTULO_VIA,
  dinero, esperaSalio, fechaHora, moduloSinLeer, num, porqueNoRevisar, quien, reservaDe, rotuloCanal,
  rotuloCuenta,
} from "@/components/ordenes/ui";
import { mensajeDeError } from "@/lib/api";

/* ─────────────────────────────── constantes ─────────────────────────────── */

const POR_PAGINA = 25;
const REFRESCO_MS = 30_000;
const ESPERA_BUSQUEDA_MS = 350;
/** Cada cuánto se reintenta leer el módulo con un documento esperando a abrirse. */
const REINTENTO_ESTADO_MS = 5_000;
/** Las dos van juntas (la 0065 truena si falta la 0064): se nombran juntas. */
const FALTAN_MIGRACIONES = "Faltan las migraciones 0064 y 0065";

// El rol que el equipo llama KAM se guarda como 'operador' (igual que en AppNavbar).
const ETIQUETA_ROL: Record<string, string> = { admin: "Admin", operador: "KAM", lectura: "Lectura" };

/**
 * Qué quiere decir cada `devolucion_estado`. Va aquí y no importado del
 * documento: la lista sólo lo usa de `title`, y así no depende de qué exporte
 * el documento (las devoluciones se capturan en otra pasada; hoy sólo se LEEN).
 */
const AYUDA_DEVOLUCION: Record<DevolucionEstado, string> = {
  pendiente: "El producto ya salió y tiene que regresar: todavía no llega a Revisión.",
  recibida: "El paquete ya regresó y está en Revisión, esperando dictamen.",
  cerrada: "La devolución ya se resolvió.",
};

/** Qué se está viendo. Sale del # de la dirección. */
type Vista = { tipo: "lista" } | { tipo: "nueva" } | { tipo: "orden"; ref: string };
const LISTA: Vista = { tipo: "lista" };

/** '' → lista · 'nueva' → documento vacío · 'OV-00012' (o el id) → esa orden. */
function vistaDe(hash: string): Vista {
  let h = hash.replace(/^#/, "");
  try { h = decodeURIComponent(h); } catch { /* un % suelto: se queda como vino */ }
  h = h.trim();
  if (!h) return LISTA;
  if (h.toLowerCase() === "nueva") return { tipo: "nueva" };
  if (/^ov-\d+$/i.test(h)) return { tipo: "orden", ref: h.toUpperCase() };
  if (/^\d+$/.test(h)) return { tipo: "orden", ref: h };
  // Cualquier otra cosa en el # no es de esta pantalla: se queda en la lista.
  return LISTA;
}

const misma = (a: Vista, b: Vista) =>
  a.tipo === b.tipo && (a.tipo !== "orden" || (b.tipo === "orden" && a.ref === b.ref));

/** El # que le toca a una vista (lo contrario de `vistaDe`). */
const hashDe = (v: Vista) =>
  (v.tipo === "lista" ? "" : `#${encodeURIComponent(v.tipo === "nueva" ? "nueva" : v.ref)}`);

const PREGUNTA_SALIR = "Hay cambios sin guardar en la orden. ¿Salir y perderlos?";

const plural = (n: number, uno: string, varios: string) => `${num(n)} ${n === 1 ? uno : varios}`;

/**
 * Las bodegas donde se puede CAPTURAR una orden: de kubera y con `admite_ov`.
 * Es la misma regla que la base vuelve a exigir al confirmar; aquí sólo se
 * enseña. (Hoy es sólo la de ensayo: TEX3 no existirá, decisión del 8-oct-2026.)
 *
 * Ni ésta ni `avisoConciliar` se exportan: Next sólo admite en un `page.tsx`
 * sus exportaciones de página, y una de más rompe el build.
 */
function bodegasDeCaptura(bodegas: Bodega[] | null | undefined): Bodega[] {
  return (bodegas ?? []).filter((b) => b.fuente === "kubera" && b.admite_ov);
}

/**
 * El aviso de «Revisar cancelaciones». El barrido ya no genera ni rellena
 * órdenes: sólo cacha lo que el CANAL canceló. Dos desenlaces distintos, y se
 * dicen por separado porque piden cosas distintas: una cancelada ya quedó
 * resuelta; una marcada espera que Bodega conteste si el paquete salió.
 */
function avisoConciliar(r: Pick<RespConciliar, "canceladas" | "marcadas">): string {
  const c = r.canceladas?.length ?? 0;
  const m = r.marcadas?.length ?? 0;
  const hecho: string[] = [];
  if (c) hecho.push(`${plural(c, "cancelada", "canceladas")} por el canal`);
  if (m) hecho.push(`${num(m)} ${m === 1 ? "espera" : "esperan"} el ¿salió?`);
  return hecho.length ? hecho.join(" · ") : "Sin cambios";
}

/** Quién movió una bandera y cuándo; o por qué no hay a quién nombrar. */
function huellaBandera(b: Bandera | null | undefined, encendida: boolean): ReactNode {
  if (!b) return "Sin dato de la bandera.";
  if (!b.persistido) {
    // Sin fila manda la variable de respaldo, que vale false. Si aun así está
    // encendida, es la variable: se dice, porque eso no deja huella de quién.
    return encendida
      ? "Sin fila en la base: la tiene encendida la variable de respaldo."
      : "Sin fila en la base: ningún acta la ha encendido.";
  }
  return (
    <>
      La {encendida ? "encendió" : "apagó"}{" "}
      <b className="font-semibold text-slate-700">{quien(null, b.actualizado_por)}</b>
      {b.actualizado_at ? <> el {fechaHora(b.actualizado_at)}</> : null}
      {b.motivo ? <> · «{b.motivo}»</> : null}.
    </>
  );
}

/* ─────────────────────────────── la página ─────────────────────────────── */

export default function OrdenesVentaPage() {
  // ── Dónde estoy (el # de la dirección) ──
  const [vista, setVista] = useState<Vista>(LISTA);
  const vistaRef = useRef<Vista>(LISTA);
  // Sube con cada NAVEGACIÓN a un documento: es su `key`. No sube al crear la
  // orden (#nueva → #OV-…): es el mismo documento, que ya tiene la orden en la
  // mano, y desmontarlo lo haría parpadear y volver a leerla.
  const [sesionDoc, setSesionDoc] = useState(0);
  const [prefill, setPrefill] = useState<VentaMarketplace | null>(null);
  const scrollLista = useRef(0);
  // ¿El documento abierto tiene cambios sin guardar? Lo escribe el documento
  // (que es quien lo sabe) y se lee aquí, dentro de los eventos de navegación.
  const sucioDoc = useRef(false);
  /** Pregunta antes de tirar lo capturado. `true` = se puede salir. */
  const puedeSalir = useCallback(() => {
    if (!sucioDoc.current) return true;
    if (!window.confirm(PREGUNTA_SALIR)) return false;
    // Ya contestó que sí: que `beforeunload` y el siguiente evento no repregunten.
    sucioDoc.current = false;
    return true;
  }, []);

  const poner = useCallback((v: Vista, mismoDocumento = false) => {
    const antes = vistaRef.current;
    // `hashchange` y `popstate` llegan juntos al ir atrás: el segundo no hace nada.
    if (misma(antes, v)) return;
    if (antes.tipo === "lista") scrollLista.current = window.scrollY;
    vistaRef.current = v;
    setVista(v);
    if (!mismoDocumento) {
      setSesionDoc((n) => n + 1);
      // La venta con la que se prellenó sólo vale para ESE documento nuevo.
      if (v.tipo !== "nueva") setPrefill(null);
    }
  }, []);

  useEffect(() => {
    // La ruta de ESTA página: «Atrás» puede llevar a otra, y ahí ya no hay nada
    // que reponer (la desmonta Next).
    const ruta = window.location.pathname;
    const leer = () => {
      const v = vistaDe(window.location.hash);
      const a = vistaRef.current;
      // «Atrás» (o un # tecleado) con un documento a medio capturar: la
      // dirección YA cambió, así que si la persona se arrepiente se le repone la
      // del documento. Sin esto, `poner` lo desmontaba sin preguntar y
      // «Adelante» regresaba a un formulario en blanco.
      if (!misma(a, v) && a.tipo !== "lista" && window.location.pathname === ruta && !puedeSalir()) {
        window.history.pushState(null, "", ruta + window.location.search + hashDe(a));
        return;
      }
      poner(v);
    };
    leer();
    window.addEventListener("hashchange", leer);
    window.addEventListener("popstate", leer);
    return () => {
      window.removeEventListener("hashchange", leer);
      window.removeEventListener("popstate", leer);
    };
  }, [poner, puedeSalir]);

  /** Cambia el # (con su entrada en el historial: «atrás» regresa a la lista). */
  const ir = useCallback((destino: string, op: { reemplazar?: boolean; mismoDocumento?: boolean } = {}) => {
    // Sin destino se quita el # entero: `location.hash = ""` dejaría un «#» colgando.
    const url = destino ? `#${encodeURIComponent(destino)}`
      : window.location.pathname + window.location.search;
    if (op.reemplazar) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
    poner(vistaDe(destino), op.mismoDocumento);
  }, [poner]);

  const enLista = vista.tipo === "lista";

  // Con un documento abierto, los clics en LIGAS (`<a href>`) pasan primero por
  // aquí. Dos cosas que un `<Link>` de Next no hace solo:
  //   · La pestaña «Órdenes de venta» y su entrada en el navbar apuntan a esta
  //     misma ruta sin #. Next la empuja con `history.pushState`, que no dispara
  //     `hashchange` ni `popstate`: la dirección perdía el # y el documento
  //     seguía en pantalla. Aquí ese clic REGRESA A LA LISTA.
  //   · Cualquier otra liga (Catálogo Maestro, Checklist, el navbar) es
  //     navegación de cliente: desmonta el documento sin pasar por
  //     `beforeunload`. Si hay cambios sin guardar, se pregunta.
  // Va en la fase de CAPTURA del documento para llegar antes que el `onClick`
  // de React (que es quien navega) y poder detenerlo.
  useEffect(() => {
    if (enLista) return;
    const alClic = (ev: MouseEvent) => {
      // Sólo el clic «normal»: con Ctrl/⌘/Shift o el botón de en medio se abre
      // otra pestaña y aquí no se pierde nada.
      if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      const a = ev.target instanceof Element ? ev.target.closest<HTMLAnchorElement>("a[href]") : null;
      if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
      let url: URL;
      try { url = new URL(a.href, window.location.href); } catch { return; }
      // Otro sitio: es una carga completa, y de ésa ya se encarga `beforeunload`.
      if (url.origin !== window.location.origin) return;
      const aqui = url.pathname.replace(/\/+$/, "") === window.location.pathname.replace(/\/+$/, "");
      if (aqui) {
        // Esta misma página: se navega por el #, sin pasar por Next.
        ev.preventDefault();
        ev.stopPropagation();
        let destino = url.hash.replace(/^#/, "");
        try { destino = decodeURIComponent(destino); } catch { /* un % suelto: se queda como vino */ }
        if (misma(vistaDe(destino), vistaRef.current) || !puedeSalir()) return;
        ir(destino);
        return;
      }
      if (!puedeSalir()) {
        ev.preventDefault();
        ev.stopPropagation();
      }
    };
    document.addEventListener("click", alClic, true);
    return () => document.removeEventListener("click", alClic, true);
  }, [enLista, ir, puedeSalir]);

  // Al abrir un documento se empieza arriba; al volver, la lista está donde se dejó.
  useEffect(() => {
    if (!enLista) { window.scrollTo(0, 0); return; }
    const y = scrollLista.current;
    if (y <= 0) return;
    const r = requestAnimationFrame(() => window.scrollTo(0, y));
    return () => cancelAnimationFrame(r);
  }, [enLista, sesionDoc]);

  // ── El módulo: las banderas (sólo lectura), las bodegas y quién soy ──
  const [estado, setEstado] = useState<EstadoModulo | null>(null);
  const [errorEstado, setErrorEstado] = useState<string | null>(null);

  const leerModulo = useCallback((signal?: AbortSignal) =>
    leerEstado(signal)
      .then((e) => {
        // `ok: false` sin ser por las migraciones es «no pude leer kubera» (una
        // pausa, un reinicio): el backend contesta, pero con banderas apagadas y
        // sin bodegas PORQUE NO LAS LEYÓ. Es la misma clase de fallo que la de
        // abajo: si ya había una lectura buena, se conserva; no se cambia por
        // un «todo apagado» que nadie midió.
        const sinLeer = e.ok === false && !e.falta_migracion;
        setEstado((antes) => (sinLeer && antes?.ok ? antes : e));
        setErrorEstado(null);
      })
      .catch((e: unknown) => {
        if (signal?.aborted) return;
        // Si ya había un estado leído, se conserva: un refresco que falla no
        // apaga los botones de quien está trabajando.
        setErrorEstado(mensajeDeError(e, "El servidor no contestó (puede estar reiniciando tras un deploy)."));
      }), []);

  useEffect(() => {
    const ctrl = new AbortController();
    void leerModulo(ctrl.signal);
    return () => ctrl.abort();
  }, [leerModulo]);

  // ── La lista ──
  const [filtro, setFiltro] = useState<FiltroEstado>("todas");
  const [busqueda, setBusqueda] = useState("");
  const [q, setQ] = useState("");            // la búsqueda ya asentada (350 ms)
  const [canal, setCanal] = useState("");
  const [pagina, setPagina] = useState(1);

  const [lista, setLista] = useState<ListaOrdenes | null>(null);
  // A qué filtros pertenece `lista`: si no son los de ahora, sus renglones se
  // ven atenuados mientras llega la lectura nueva, nunca como si fueran de esta.
  const [listaDe, setListaDe] = useState("");
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [desfasada, setDesfasada] = useState(false);
  const [leidaAt, setLeidaAt] = useState<string | null>(null);

  useEffect(() => {
    const limpia = busqueda.trim();
    if (limpia === q) return;
    const t = setTimeout(() => { setQ(limpia); setPagina(1); }, ESPERA_BUSQUEDA_MS);
    return () => clearTimeout(t);
  }, [busqueda, q]);

  const clave = `${filtro}|${q}|${canal}|${pagina}`;

  // La lectura EN VUELO. Una que pidió la persona (filtro, página, recargar)
  // cancela la anterior; un refresco SUAVE no se encima a nada: si ya hay una
  // lectura en curso, se salta ese turno.
  const vuelo = useRef<{ ctrl: AbortController } | null>(null);

  const cargar = useCallback((suave = false) => {
    if (suave && vuelo.current) return () => {};
    vuelo.current?.ctrl.abort();
    const ctrl = new AbortController();
    const este = { ctrl };
    vuelo.current = este;
    if (!suave) { setCargando(true); setError(null); }
    const de = `${filtro}|${q}|${canal}|${pagina}`;
    listarOrdenes({ estado: filtro, q, canal, pagina, por_pagina: POR_PAGINA }, ctrl.signal)
      .then((d) => {
        if (ctrl.signal.aborted) return;
        setLista(d);
        setListaDe(de);
        setError(null);
        setDesfasada(false);
        setLeidaAt(new Date().toISOString());
        // Se quedó en una página que ya no existe (se borraron o cambiaron de estado).
        if (d.paginas >= 1 && pagina > d.paginas) setPagina(d.paginas);
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        if (suave) setDesfasada(true);
        else setError(mensajeDeError(e, "El servidor no contestó (puede estar reiniciando tras un deploy)."));
      })
      .finally(() => {
        if (vuelo.current !== este) return;
        vuelo.current = null;
        if (!suave) setCargando(false);
      });
    return () => {
      ctrl.abort();
      if (vuelo.current === este) vuelo.current = null;
    };
  }, [filtro, q, canal, pagina]);

  useEffect(() => cargar(), [cargar]);
  useEffect(() => () => vuelo.current?.ctrl.abort(), []);

  const cargarRef = useRef(cargar);
  cargarRef.current = cargar;

  // El refresco suave: cada 30 s, al volver a la pestaña del navegador y al
  // regresar de un documento. Nunca con la pestaña escondida ni con un documento
  // abierto (ahí manda el documento, que tiene su propio chat en vivo).
  const yaMontada = useRef(false);
  useEffect(() => {
    if (!enLista) return;
    const refrescar = () => {
      if (document.visibilityState !== "visible") return;
      cargarRef.current(true);
      void leerModulo();
    };
    if (yaMontada.current) refrescar();
    yaMontada.current = true;
    const t = setInterval(refrescar, REFRESCO_MS);
    document.addEventListener("visibilitychange", refrescar);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", refrescar);
    };
  }, [enLista, leerModulo]);

  // Con un documento por abrir y un `/estado` que vino sin leer kubera, el
  // refresco de arriba no corre (es de la lista): se reintenta aquí, cada pocos
  // segundos, hasta tener un estado de verdad con el cual montar el documento.
  const sinLeerModulo = moduloSinLeer(estado);
  useEffect(() => {
    if (enLista || !sinLeerModulo) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void leerModulo();
    }, REINTENTO_ESTADO_MS);
    return () => clearInterval(t);
  }, [enLista, sinLeerModulo, leerModulo]);

  // ── Avisos y ventanas ──
  const [aviso, setAviso] = useState<string | null>(null);
  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(() => setAviso(null), 6000);
    return () => clearTimeout(t);
  }, [aviso]);

  const [ventasAbiertas, setVentasAbiertas] = useState(false);
  const [conciliando, setConciliando] = useState(false);

  const recargar = () => { cargar(); void leerModulo(); };

  const elegirFiltro = (f: FiltroEstado) => { setFiltro(f); setPagina(1); };
  const quitarFiltros = () => { setFiltro("todas"); setBusqueda(""); setQ(""); setCanal(""); setPagina(1); };

  const abrirNueva = (venta: VentaMarketplace | null) => { setPrefill(venta); ir("nueva"); };

  // El documento avisa de cada cambio: el renglón se parcha al instante (si está
  // en la página que se ve) y los conteos se releen al volver a la lista.
  const alCambiar = useCallback((o: Orden) => {
    setLista((l) => (l && l.ordenes?.some((x) => x.id === o.id)
      ? { ...l, ordenes: l.ordenes.map((x) => (x.id === o.id ? o : x)) } : l));
  }, []);

  const revisarCancelaciones = async () => {
    setConciliando(true);
    try {
      const r = await conciliar();
      if (!r.ok) {
        // `ok: false` = el barrido NO corrió. Se dice primero: el motivo solo
        // («…están en modo prueba…») se leía como «revisé y no había nada».
        setAviso(r.motivo ? `No se revisó: ${r.motivo}` : "No se pudo revisar las cancelaciones.");
        return;
      }
      setAviso(avisoConciliar(r));
      // Algo se movió (una cancelada, o una que ahora espera el «¿salió?»): la
      // lista y sus conteos se releen.
      if ((r.canceladas?.length ?? 0) + (r.marcadas?.length ?? 0) > 0) cargar();
    } catch (e) {
      setAviso(mensajeDeError(e, "No se pudo revisar las cancelaciones."));
    } finally {
      setConciliando(false);
    }
  };

  // ── Lo que se deriva ──
  const faltaMigracion = !!estado?.falta_migracion || !!lista?.falta_migracion;
  const motivoMigracion = faltaMigracion ? (estado?.motivo ?? lista?.motivo ?? null) : null;
  const yo = estado?.yo ?? null;
  const admin = !!yo?.admin;
  // Código → nombre, para el `title` del chip de bodega de cada fila.
  const nombresBodega: Record<string, string> = {};
  for (const b of estado?.bodegas ?? []) nombresBodega[b.codigo] = b.nombre;

  // Por qué NO se puede crear ni mover (null = sí se puede).
  const porqueNo = faltaMigracion ? `${FALTAN_MIGRACIONES} en la base.`
    : !estado ? (errorEstado ? "No se pudo leer el estado del módulo." : "Leyendo el estado del módulo…")
      : !yo?.escribe ? "Tu rol es de sólo lectura: crear y mover órdenes es de operador o admin."
        : null;

  // «Revisar cancelaciones» además necesita la bandera encendida: sin ella el
  // backend contesta `ok: false` sin revisar nada.
  const porqueNoBarrido = porqueNo ?? porqueNoRevisar(estado);

  const conteo = (k: FiltroEstado): number | null => {
    const v = lista?.conteos?.[k];
    return typeof v === "number" ? v : null;
  };

  const vigente = lista && listaDe === clave ? lista : null;
  // Con error y sin lectura de ESTOS filtros no se pintan los renglones de otros.
  const filas: OrdenResumen[] = error && !vigente ? [] : (lista?.ordenes ?? []);
  const atenuada = !vigente && cargando && filas.length > 0;
  // Cuántas de las que se ven esperan el «¿Salió?» (sólo esta página: ver el aviso).
  const enEspera = filas.filter(esperaSalio).length;
  const hayFiltros = filtro !== "todas" || !!q || !!canal;

  let vacio: ReactNode = null;
  if (!filas.length) {
    if (error && !vigente) {
      vacio = (
        <Vacio icono={AlertTriangle} tono="error" titulo="No se pudo leer las órdenes"
               texto={`${error} Lo guardado no se perdió: es la lectura la que falló.`}>
          <Boton icono={RefreshCw} tono="primario" onClick={recargar}>Reintentar</Boton>
        </Vacio>
      );
    } else if (faltaMigracion) {
      vacio = (
        <Vacio icono={ClipboardList} titulo="La pestaña está en espera"
               texto="En cuanto se apliquen las migraciones 0064 y 0065, aquí aparecen las órdenes." />
      );
    } else if (vigente && !hayFiltros) {
      vacio = (
        <Vacio icono={ClipboardList} titulo="Aún no hay órdenes. Crea la primera."
               texto="Captúrala a mano o pártela de una venta de marketplace, con su precio y sus renglones.">
          <Boton icono={Plus} tono="primario" onClick={() => abrirNueva(null)}
                 deshabilitado={!!porqueNo} porque={porqueNo ?? undefined}>
            Nueva orden
          </Boton>
        </Vacio>
      );
    } else if (vigente) {
      vacio = (
        <Vacio icono={Search} titulo="Ninguna orden con ese filtro"
               texto={filtro === "borradas" ? "No hay órdenes borradas." : "Prueba con otro estado, otro canal u otra búsqueda."}>
          <Boton onClick={quitarFiltros}>Quitar filtros</Boton>
        </Vacio>
      );
    }
  }

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">
        <InventarioPestanas />

        {!enLista ? (
          /* ─── el documento, EN LUGAR de la lista ─── */
          faltaMigracion ? (
            <div className="space-y-3">
              <AvisoMigracion motivo={motivoMigracion} />
              <Boton onClick={() => ir("")}>Volver a la lista</Boton>
            </div>
          ) : !estado ? (
            errorEstado ? (
              <div className="space-y-3">
                <Aviso tono="error" icono={AlertTriangle}>
                  <b>No se pudo abrir la orden.</b> {errorEstado}
                </Aviso>
                <div className="flex gap-2">
                  <Boton icono={RefreshCw} tono="primario" onClick={() => void leerModulo()}>Reintentar</Boton>
                  <Boton onClick={() => ir("")}>Volver a la lista</Boton>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-6 py-16 text-sm text-slate-400"
                   role="status">
                <Loader2 className="h-4 w-4 animate-spin" /> Abriendo…
              </div>
            )
          ) : sinLeerModulo ? (
            // El backend contestó, pero SIN poder leer kubera: sus banderas
            // vienen apagadas y sus bodegas vacías porque no se leyeron. Montar
            // el documento con eso sería afirmar «modo prueba» y «no hay bodega»
            // sin haberlo medido, y nadie lo volvería a leer. Se espera (y se
            // reintenta sola, ver `REINTENTO_ESTADO_MS`).
            <div className="space-y-3">
              <Aviso tono="ambar" icono={AlertTriangle}>
                <b>No se pudo leer el estado del módulo.</b>{" "}
                {estado?.motivo ? `${estado.motivo} ` : ""}
                La orden se abre en cuanto se pueda leer: se reintenta sola cada pocos segundos.
              </Aviso>
              <div className="flex gap-2">
                <Boton icono={RefreshCw} tono="primario" onClick={() => void leerModulo()}>Reintentar</Boton>
                <Boton onClick={() => ir("")}>Volver a la lista</Boton>
              </div>
            </div>
          ) : (
            <OrdenDocumento
              key={sesionDoc}
              refOrden={vista.tipo === "orden" ? vista.ref : null}
              prefill={vista.tipo === "nueva" ? prefill : null}
              modulo={estado}
              sucioRef={sucioDoc}
              onCerrar={() => ir("")}
              // Tras el alta la dirección pasa a ser la de la orden, sin dejar el
              // «#nueva» en el historial: «atrás» regresa a la lista, no a un
              // formulario vacío.
              onCreada={(o: Orden) => { alCambiar(o); ir(o.folio, { reemplazar: true, mismoDocumento: true }); }}
              onCambio={alCambiar}
            />
          )
        ) : (
          /* ─── la lista ─── */
          <>
            <Hero estado={estado} sinEstado={!estado && !!errorEstado} vivas={conteo("todas")}
                  cargando={cargando} onRecargar={recargar} />

            {faltaMigracion && <div className="mt-4"><AvisoMigracion motivo={motivoMigracion} /></div>}
            {!estado && errorEstado && (
              <div className="mt-4">
                <Aviso tono="error" icono={AlertTriangle}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>No se pudo leer el estado del módulo: {errorEstado} Crear y mover órdenes queda apagado hasta saberlo.</span>
                    <span className="ml-auto"><Boton chico icono={RefreshCw} onClick={() => void leerModulo()}>Reintentar</Boton></span>
                  </div>
                </Aviso>
              </div>
            )}
            {error && vigente && (
              <div className="mt-4">
                <Aviso tono="error" icono={AlertTriangle}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{error} Se muestra lo último que se leyó.</span>
                    <span className="ml-auto"><Boton chico icono={RefreshCw} onClick={recargar} ocupado={cargando}>Reintentar</Boton></span>
                  </div>
                </Aviso>
              </div>
            )}

            {/* El backend contestó, pero sin poder leer kubera: sus banderas vienen
                apagadas y sus bodegas vacías porque NO se leyeron. Pintarlas sería
                afirmar «nunca se encendió» y «ninguna bodega» sin haberlo medido. */}
            {sinLeerModulo && (
              <div className="mt-4">
                <Aviso tono="ambar" icono={AlertTriangle}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>
                      <b>No se pudo leer el estado del módulo.</b>{" "}
                      {estado?.motivo ? `${estado.motivo} ` : ""}
                      Mientras tanto se toma como modo prueba; las banderas y las bodegas
                      se muestran en cuanto se puedan leer.
                    </span>
                    <span className="ml-auto"><Boton chico icono={RefreshCw} onClick={() => void leerModulo()}>Reintentar</Boton></span>
                  </div>
                </Aviso>
              </div>
            )}
            {estado && !faltaMigracion && estado.ok && <Banderas estado={estado} />}

            <Kpis conteo={conteo} filtro={filtro} onFiltro={elegirFiltro} />

            {/* ─── barra de herramientas ─── */}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  value={busqueda} onChange={(e) => setBusqueda(e.target.value)}
                  aria-label="Buscar órdenes" maxLength={200}
                  placeholder="Buscar folio, orden de marketplace, SKU o cliente"
                  className="w-[23rem] max-w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-8 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100"
                />
                {busqueda && (
                  <button type="button" onClick={() => { setBusqueda(""); setQ(""); setPagina(1); }}
                          aria-label="Borrar la búsqueda"
                          className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <select value={canal} onChange={(e) => { setCanal(e.target.value); setPagina(1); }}
                      aria-label="Filtrar por canal"
                      className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100">
                <option value="">Todos los canales</option>
                {CANALES.map((c) => <option key={c.id} value={c.id}>{c.rotulo}</option>)}
              </select>
              {/* Las borradas no se eliminan (queda quién y por qué), pero sólo
                  las consulta un admin: no son un estado más de la orden. */}
              {admin && !faltaMigracion && (filtro === "borradas" ? (
                <button type="button" onClick={() => elegirFiltro("todas")}
                        className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-slate-700">
                  Viendo las borradas <X className="h-3.5 w-3.5" />
                </button>
              ) : (
                <button type="button" onClick={() => elegirFiltro("borradas")}
                        className="rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-400 hover:bg-slate-100 hover:text-slate-700">
                  Ver borradas{conteo("borradas") ? ` (${num(conteo("borradas"))})` : ""}
                </button>
              ))}

              <div className="ml-auto flex flex-wrap items-center gap-2">
                <Boton icono={Plus} tono="primario" onClick={() => abrirNueva(null)}
                       deshabilitado={!!porqueNo} porque={porqueNo ?? undefined}>
                  Nueva orden
                </Boton>
                <Boton icono={ShoppingBag} onClick={() => setVentasAbiertas(true)}
                       deshabilitado={!!porqueNo} porque={porqueNo ?? undefined}>
                  Desde venta de marketplace
                </Boton>
                <Boton icono={ScanSearch} onClick={() => void revisarCancelaciones()}
                       deshabilitado={!!porqueNoBarrido} porque={porqueNoBarrido ?? undefined} ocupado={conciliando}>
                  Revisar cancelaciones
                </Boton>
              </div>
            </div>

            {/* Lo único de la lista que le pide algo a una persona. El backend no
                da (todavía) ni conteo ni filtro para esto, así que se cuenta lo
                que TRAE ESTA PÁGINA y se dice así: las que caen en otra página
                no se ven desde aquí. Todas son confirmadas: ese filtro acota. */}
            {vigente && enEspera > 0 && (
              <div className="mt-3">
                <Aviso tono="ambar" icono={AlertTriangle}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span>
                      <b>{enEspera === 1 ? "1 orden de esta página espera" : `${num(enEspera)} órdenes de esta página esperan`} el «¿Salió?».</b>{" "}
                      El canal canceló la venta con el paquete en camino: su stock sigue apartado y no se puede
                      marcar DELIVERED hasta que Bodega conteste.
                      {(vigente.paginas ?? 1) > 1
                        ? " Sólo se cuentan las de esta página: puede haber más en las otras."
                        : ""}
                    </span>
                    {filtro !== "confirmada" && (
                      <span className="ml-auto"><Boton chico onClick={() => elegirFiltro("confirmada")}>Ver sólo confirmadas</Boton></span>
                    )}
                  </div>
                </Aviso>
              </div>
            )}

            <Tabla filas={filas} esqueleto={cargando && !vigente && !filas.length && !error}
                   atenuada={atenuada} vacio={vacio} nombresBodega={nombresBodega}
                   onAbrir={(o) => ir(o.folio)} />

            {/* Sin la migración no hay total que contar: «0 órdenes» sería un cero inventado. */}
            <Paginacion pagina={pagina} total={vigente?.paginas ?? 0}
                        ordenes={faltaMigracion ? null : vigente?.total ?? null}
                        onPagina={setPagina} />

            <p className="mt-4 text-xs leading-relaxed text-slate-400">
              {leidaAt && (
                <span className={desfasada ? "font-semibold text-amber-700" : ""}>
                  {desfasada ? "No se pudo refrescar; se muestra lo leído el " : "Leída el "}
                  {fechaHora(leidaAt)}.{" "}
                </span>
              )}
              Se refresca sola cada 30 s con la pestaña a la vista. Estas órdenes viven
              en kubera: no escriben en Odoo, en WooCommerce ni en ningún marketplace.
              Confirmar aparta el stock de cada renglón en su bodega, todo o nada; de
              ahí en adelante la orden ya no se edita: se entrega, se cancela o se borra.
            </p>
          </>
        )}
      </main>

      {ventasAbiertas && (
        <VentasMarketplace
          onCerrar={() => setVentasAbiertas(false)}
          onElegir={(v: VentaMarketplace) => { setVentasAbiertas(false); abrirNueva(v); }}
          onAbrirOrden={(folio: string) => { setVentasAbiertas(false); ir(folio); }}
        />
      )}

      {/* Por encima de las ventanas (z-50): el aviso se lee aunque haya una abierta. */}
      {aviso && (
        <div role="status"
             className="fixed bottom-5 left-1/2 z-[60] max-w-[90vw] -translate-x-1/2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm text-white shadow-lg">
          {aviso}
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────── piezas ─────────────────────────────── */

function AvisoMigracion({ motivo }: { motivo: string | null }) {
  return (
    <Aviso tono="ambar" icono={AlertTriangle}>
      <b>{FALTAN_MIGRACIONES}.</b> La pestaña queda en espera hasta que se apliquen
      en la base; nada más se ve afectado.
      {motivo ? <span className="mt-1 block text-xs text-amber-800/80">{motivo}</span> : null}
    </Aviso>
  );
}

function Hero({
  estado, sinEstado, vivas, cargando, onRecargar,
}: {
  estado: EstadoModulo | null;
  /** El estado del módulo no se pudo leer (no es lo mismo que «todavía no llega»). */
  sinEstado: boolean;
  vivas: number | null;
  cargando: boolean;
  onRecargar: () => void;
}) {
  const pastilla =
    "inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-2.5 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm";
  // Sobre el morado, el ámbar de «pide atención» va translúcido y con su anillo.
  const atencion =
    "inline-flex items-center gap-1.5 rounded-lg bg-amber-300/25 px-2.5 py-1 text-[11px] font-semibold text-amber-50 ring-1 ring-amber-200/60 backdrop-blur-sm";

  const yo = estado?.yo ?? null;
  const captura = bodegasDeCaptura(estado?.bodegas);

  return (
    <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-600 to-violet-700 px-6 py-5 text-white shadow-[0_2px_8px_rgba(79,70,229,.25)]">
      <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-white/10" />
      <div className="relative flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-white/70">
            Inventario · Órdenes de venta
          </p>
          <h1 className="mt-1 flex items-center gap-2.5 text-3xl font-extrabold tracking-tight">
            <ClipboardList className="h-7 w-7" /> Órdenes de venta
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-white/80">
            El documento que le dice al almacén qué surtir, ahora dentro de
            Omnicanal: borrador, confirmar y apartar, entregar a la paquetería.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {estado ? (
              <>
                {estado.habilitado ? (
                  <span className={pastilla}
                        title="La bandera «ordenes_venta» está encendida: se puede confirmar (apartar stock) y entregar, y el barrido cacha las cancelaciones del canal.">
                    <span className="h-2 w-2 rounded-full bg-emerald-300" /> Órdenes de venta ENCENDIDAS
                  </span>
                ) : (
                  <span className={atencion}
                        title="La bandera «ordenes_venta» está apagada: se pueden crear, editar y cancelar borradores, y chatear. Confirmar y entregar se habilitan cuando un acta la encienda.">
                    <span className="h-2 w-2 rounded-full bg-amber-300" />
                    MODO PRUEBA · sólo borradores
                  </span>
                )}
                {/* Sin las migraciones no hay catálogo de bodegas que leer, y si
                    kubera no contestó tampoco: decir «ninguna admite órdenes»
                    sería inventar un dato. Lo primero ya lo dice su aviso. */}
                {!estado.falta_migracion && !estado.ok ? (
                  <span className={atencion} title={estado.motivo ?? "No se pudo leer el catálogo de bodegas."}>
                    <Warehouse className="h-3.5 w-3.5" aria-hidden="true" />
                    Bodegas: sin dato
                  </span>
                ) : !estado.falta_migracion && (captura.length ? (
                  <span className={pastilla}
                        title={"Bodegas de kubera que admiten órdenes de venta ("
                          + captura.map((b) => b.codigo).join(", ")
                          + "). La bodega se elige en cada renglón, y ahí se aparta su stock al confirmar."}>
                    <Warehouse className="h-3.5 w-3.5" aria-hidden="true" />
                    Se captura en: {captura.map((b) => b.nombre || b.codigo).join(" · ")}
                  </span>
                ) : (
                  <span className={atencion}
                        title="Ninguna bodega de kubera tiene «admite_ov» encendido: se pueden guardar borradores, pero ningún renglón tiene de dónde apartar y no se podrá confirmar. La enciende un acta.">
                    <Warehouse className="h-3.5 w-3.5" aria-hidden="true" />
                    Ninguna bodega admite órdenes todavía
                  </span>
                ))}
                {yo && (
                  <span className={pastilla} title={`${yo.actor} · ${ROTULO_VIA[yo.via] ?? yo.via}`}>
                    {quien(yo.nombre, yo.actor)} · {ETIQUETA_ROL[yo.rol] ?? yo.rol}
                  </span>
                )}
              </>
            ) : (
              <span className={sinEstado ? atencion : pastilla}>
                {sinEstado ? "Estado del módulo: sin dato" : "Leyendo el estado del módulo…"}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-start gap-4">
          <div className="text-right" title="Todas las órdenes que no están borradas.">
            <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums">
              {num(vivas)}
            </div>
            <div className="mt-1 text-[11px] font-bold uppercase tracking-[0.06em] text-white/70">
              órdenes vivas
            </div>
          </div>
          <button
            type="button" onClick={onRecargar} disabled={cargando}
            title="Volver a leer las órdenes" aria-label="Volver a leer las órdenes"
            className="rounded-lg bg-white/15 p-2 text-white backdrop-blur-sm transition hover:bg-white/25 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>
    </section>
  );
}

/**
 * Las dos banderas del módulo, de SÓLO LECTURA.
 *
 * Antes aquí había un interruptor que un admin movía desde la pantalla. Ya no:
 * `ordenes_venta` y `ov_generacion_auto` son filas de `ops.automatizacion_flags`
 * que enciende un ACTA (con su motivo y sus firmas), porque lo que gobiernan
 * —apartar stock, y que el planeador genere órdenes solas— es un
 * flujo de negocio vivo. La pantalla dice cómo están, quién las movió y cuándo;
 * no ofrece nada que apretar, y por eso no parece un control.
 */
function Banderas({ estado }: { estado: EstadoModulo }) {
  const ov = estado.banderas?.ordenes_venta ?? null;
  const auto = estado.banderas?.ov_generacion_auto ?? null;
  const encendidas = estado.habilitado;
  const autoEncendida = !!auto?.encendido;

  return (
    <section aria-label="Banderas del módulo (sólo lectura)"
             className="mt-4 rounded-2xl border border-slate-200 bg-white px-4 py-3">
      <div className="grid gap-x-8 gap-y-3 md:grid-cols-2">
        <FilaBandera
          icono={ClipboardList} activa={encendidas}
          titulo="Órdenes de venta"
          chip={encendidas ? "ENCENDIDAS" : "MODO PRUEBA (sólo borradores)"}
          tono={encendidas ? "ok" : "prueba"}>
          {huellaBandera(ov, ov?.encendido ?? encendidas)}
          {!encendidas ? " Confirmar y entregar quedan apagados." : null}
        </FilaBandera>
        <FilaBandera
          icono={Zap} activa={autoEncendida}
          titulo="Generación automática"
          chip={autoEncendida ? "ENCENDIDA" : "apagada"}
          tono={autoEncendida ? "ok" : "apagada"}>
          {autoEncendida ? huellaBandera(auto, true) : (
            <>
              La enciende un acta.
              {auto?.persistido ? <> {huellaBandera(auto, false)}</> : null}
            </>
          )}
        </FilaBandera>
      </div>
      <p className="mt-2.5 border-t border-slate-100 pt-2 text-[11px] leading-snug text-slate-400">
        Sólo lectura: estas banderas no se mueven desde la pantalla. Las enciende o apaga un acta,
        y aquí queda quién y cuándo.
      </p>
    </section>
  );
}

const TONO_BANDERA: Record<"ok" | "prueba" | "apagada", string> = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  prueba: "bg-amber-50 text-amber-800 ring-amber-200",
  apagada: "bg-slate-100 text-slate-600 ring-slate-200",
};

function FilaBandera({
  icono: Icono, activa, titulo, chip, tono, children,
}: {
  icono: typeof Plus;
  activa: boolean;
  titulo: string;
  chip: string;
  tono: "ok" | "prueba" | "apagada";
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
        activa ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400"}`}>
        <Icono className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-bold text-slate-800">{titulo}</h2>
          <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-bold ring-1 ${TONO_BANDERA[tono]}`}>
            {chip}
          </span>
        </div>
        <p className="mt-0.5 text-xs leading-snug text-slate-500">{children}</p>
      </div>
    </div>
  );
}

/** Los KPIs son los filtros. `punto` es el color del estado en la traza y en su chip. */
const TARJETAS: { k: FiltroEstado; t: string; p: string; punto: string | null }[] = [
  { k: "todas", t: "Todas", p: "sin contar las borradas", punto: null },
  { k: "borrador", t: "Borrador", p: "se pueden editar; no apartan stock", punto: COLOR_ESTADO.borrador },
  { k: "confirmada", t: "Confirmadas", p: "con stock apartado, por entregar", punto: COLOR_ESTADO.confirmada },
  { k: "entregada", t: "Delivered", p: "entregadas a la paquetería", punto: COLOR_ESTADO.entregada },
  { k: "cancelada", t: "Canceladas", p: "antes de salir del almacén", punto: COLOR_ESTADO.cancelada },
  { k: "por_devolver", t: "Por devolver", p: "DELIVERED but CANCELLED · devolución pendiente",
    punto: COLOR_ESTADO.entregada_cancelada },
];

function Kpis({
  conteo, filtro, onFiltro,
}: {
  conteo: (k: FiltroEstado) => number | null;
  filtro: FiltroEstado;
  onFiltro: (f: FiltroEstado) => void;
}) {
  return (
    <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
      {TARJETAS.map((c) => {
        const v = conteo(c.k);
        const activo = filtro === c.k;
        // Lo único que pide atención: producto que ya salió y tiene que regresar.
        const atencion = c.k === "por_devolver" && !!v;
        return (
          <button key={c.k} type="button" aria-pressed={activo}
                  onClick={() => onFiltro(activo ? "todas" : c.k)}
                  className={`rounded-2xl border p-4 text-left outline-none transition focus-visible:ring-2 focus-visible:ring-indigo-300 ${
                    atencion ? "border-amber-200 bg-amber-50"
                      : "border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]"} ${
                    activo ? "ring-2 ring-indigo-400 ring-offset-1" : "hover:brightness-[.98]"}`}>
            <div className={`flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] ${
              atencion ? "text-amber-700" : "text-slate-400"}`}>
              {c.punto && <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: c.punto }} />}
              {c.t}
            </div>
            <div className={`mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums ${
              v === null ? "text-slate-300" : atencion ? "text-amber-900" : "text-slate-900"}`}>
              {num(v)}
            </div>
            <div className={`mt-1 text-xs ${atencion ? "text-amber-800/80" : "text-slate-500"}`}>{c.p}</div>
          </button>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────── la tabla ─────────────────────────────── */

const COLUMNAS = 8;

function Tabla({
  filas, esqueleto, atenuada, vacio, nombresBodega, onAbrir,
}: {
  filas: OrdenResumen[];
  /** Primera lectura: todavía no hay nada que enseñar. */
  esqueleto: boolean;
  /** Los renglones son de OTROS filtros y la lectura nueva viene en camino. */
  atenuada: boolean;
  /** Qué decir cuando no hay renglones (vacío, error, en espera). */
  vacio: ReactNode;
  /** Código de bodega → su nombre (del catálogo del módulo), para el `title` del chip. */
  nombresBodega: Record<string, string>;
  onAbrir: (o: OrdenResumen) => void;
}) {
  const th = "px-3 py-2.5 text-left text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400";
  return (
    <div className="mt-3 overflow-x-auto rounded-2xl border border-slate-200 bg-white" aria-busy={esqueleto || atenuada}>
      <table className="w-full min-w-[1120px] text-sm">
        <thead className="border-b border-slate-100 bg-slate-50/60">
          <tr>
            <th className={th}>Folio · estado</th>
            <th className={th}>Avance</th>
            <th className={th}>Cliente · canal</th>
            <th className={th}>Orden de marketplace</th>
            <th className={th}>Piezas · apartado</th>
            <th className={`${th} text-right`}>Total</th>
            <th className={th}>Creada</th>
            <th className="w-24" />
          </tr>
        </thead>
        <tbody className={atenuada ? "pointer-events-none opacity-50 transition-opacity" : "transition-opacity"}>
          {esqueleto && Array.from({ length: 6 }, (_, i) => <FilaEsqueleto key={i} />)}
          {!esqueleto && !filas.length && (
            <tr><td colSpan={COLUMNAS} className="px-3 py-12 text-center">{vacio}</td></tr>
          )}
          {filas.map((o) => (
            <Fila key={o.id} o={o} nombresBodega={nombresBodega} onAbrir={() => onAbrir(o)} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilaEsqueleto() {
  const barra = "h-3 rounded bg-slate-100";
  return (
    <tr className="animate-pulse border-b border-slate-100 last:border-b-0" aria-hidden="true">
      <td className="px-3 py-3"><div className={`${barra} w-20`} /><div className={`${barra} mt-2 w-24`} /></td>
      <td className="px-3 py-3"><div className={`${barra} w-[150px]`} /></td>
      <td className="px-3 py-3"><div className={`${barra} w-36`} /><div className={`${barra} mt-2 w-20`} /></td>
      <td className="px-3 py-3"><div className={`${barra} w-28`} /></td>
      <td className="px-3 py-3"><div className={`${barra} w-24`} /><div className={`${barra} mt-2 w-28`} /></td>
      <td className="px-3 py-3"><div className={`${barra} ml-auto w-16`} /></td>
      <td className="px-3 py-3"><div className={`${barra} w-24`} /><div className={`${barra} mt-2 w-16`} /></td>
      <td className="px-3 py-3" />
    </tr>
  );
}

function Vacio({
  icono: Icono, titulo, texto, tono = "neutro", children,
}: {
  icono: typeof Plus;
  titulo: string;
  texto: string;
  tono?: "neutro" | "error";
  children?: ReactNode;
}) {
  return (
    <div className="text-slate-500">
      <Icono className={`mx-auto h-8 w-8 ${tono === "error" ? "text-rose-300" : "text-slate-300"}`} />
      <p className="mt-2 font-semibold text-slate-700">{titulo}</p>
      <p className="mx-auto max-w-md text-xs">{texto}</p>
      {children ? <div className="mt-3 flex justify-center">{children}</div> : null}
    </div>
  );
}

/** Lo que dice el chip del apartado al pasar el cursor: los números detrás del rótulo. */
function detalleReserva(o: OrdenResumen, reserva: ReturnType<typeof reservaDe>, borrada: boolean): string {
  if (reserva === "sin_apartar") return "Un borrador no aparta stock: se aparta al confirmar.";
  if (borrada) return "La orden se borró: lo que tenía apartado se soltó.";
  if (reserva === "liberada") return "Cancelada: lo que tenía apartado se soltó.";
  if (reserva === "apartada") {
    return `${num(o.piezas_apartadas)} de ${num(o.piezas)} piezas apartadas en su bodega.`;
  }
  const salieron = `Salieron ${num(o.piezas_entregadas)} de ${num(o.piezas)} piezas`
    + ` (${num(o.renglones_entregados)} de ${num(o.renglones)} renglones)`;
  return reserva === "entrega_parcial"
    ? `${salieron}. Siguen apartadas ${num(o.piezas_apartadas)}, por entregar.`
    : `${salieron}.`;
}

function Fila({ o, nombresBodega, onAbrir }: {
  o: OrdenResumen;
  nombresBodega: Record<string, string>;
  onAbrir: () => void;
}) {
  const borrada = !!o.borrada_at;
  // `reservaDe` mira `borrada_at`: una confirmada que se BORRÓ soltó su apartado
  // («liberada»); y mira los renglones que ya salieron («entrega parcial»).
  const reserva = reservaDe(o);
  const bodegas = o.bodegas ?? [];
  // El canal canceló con el paquete en camino y NADIE ha contestado si salió.
  // La marca del canal se queda puesta después (cancelada, DELIVERED but
  // CANCELLED), pero la pregunta sólo está abierta en una confirmada viva.
  const enEspera = esperaSalio(o);
  // Una DELIVERED puede haber salido con menos piezas de las pedidas (cada
  // renglón se entrega con 0..cantidad): se dice, no se esconde tras «Surtida».
  const salioDeMenos = reserva === "surtida" && typeof o.piezas_entregadas === "number"
    && o.piezas_entregadas < o.piezas;
  const canal = o.canal ?? o.mp_canal;
  const rotulo = rotuloCanal(canal);
  // «Temu · TEMU» no dice nada: la cuenta sólo se nombra si no repite al canal
  // (las dos de Mercado Libre sí: Kubera y San Corpe).
  const cuenta = rotuloCuenta(o.mp_cuenta);
  const conCuenta = !!cuenta && cuenta.toLowerCase() !== rotulo.toLowerCase();
  const skus = o.skus ?? [];
  const creo = quien(o.creado_nombre, o.creado_por);
  const via = ROTULO_VIA[o.creado_via] ?? o.creado_via;
  // «Automático · automático» sobra: la vía se dice sólo cuando agrega algo al nombre.
  const conVia = o.creado_via !== "panel" && !via.toLowerCase().includes(creo.toLowerCase());

  return (
    // La fila entera abre la orden (molde del Checklist). El folio es además un
    // botón de verdad, para el teclado; y si alguien está seleccionando texto
    // (para copiar la orden de marketplace) no se abre.
    <tr
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("a,button,input,select,textarea,label")) return;
        if (window.getSelection()?.toString()) return;
        onAbrir();
      }}
      className={`cursor-pointer border-b border-slate-100 align-top transition last:border-b-0 ${
        enEspera ? "bg-amber-50/50 hover:bg-amber-50" : "hover:bg-slate-50/60"} ${
        borrada ? "opacity-60" : ""}`}
    >
      {/* La raya ámbar va como sombra interior: un borde movería las columnas. */}
      <td className={`px-3 py-3 ${enEspera ? "shadow-[inset_3px_0_0_0_#f59e0b]" : ""}`}>
        <button type="button" onClick={onAbrir} aria-label={`Abrir la orden ${o.folio}`}
                className={`rounded font-mono text-[13px] font-bold text-slate-900 outline-none hover:text-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-300 ${
                  borrada ? "line-through" : ""}`}>
          {o.folio}
        </button>
        <div className="mt-1"><ChipEstado estado={o.estado} chico /></div>
        {enEspera && (
          // Lo único de la lista que le pide algo a una persona: por eso es un
          // botón (lleva a contestar), en ámbar lleno y con un punto que late.
          <button type="button" onClick={onAbrir}
                  aria-label={`El canal canceló la orden ${o.folio} con el paquete en camino: abrir para contestar si salió`}
                  title={`El canal canceló esta venta el ${fechaHora(o.canal_cancelo_at)} con el paquete ya en camino`
                    + (o.canal_cancelo_ref ? ` (estado del envío en el canal: ${o.canal_cancelo_ref})` : "")
                    + ". Bodega tiene que contestar si salió; mientras tanto no se puede marcar DELIVERED."}
                  className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-extrabold text-amber-900 outline-none ring-1 ring-amber-300 transition hover:bg-amber-200 focus-visible:ring-2 focus-visible:ring-amber-500">
            <span className="relative flex h-2 w-2" aria-hidden="true">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-75 motion-reduce:hidden" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
            </span>
            ¿Salió?
          </button>
        )}
        {borrada ? (
          <div className="mt-1 text-[11px] font-semibold text-rose-600" title={o.borrada_motivo ?? undefined}>
            Borrada por {quien(o.borrada_nombre, o.borrada_por)}
          </div>
        ) : o.estado === "entregada_cancelada" || o.devolucion_estado ? (
          // El estado REAL de la devolución; sin registro se dice, no se inventa
          // «pendiente». Desde la 0064 también una DELIVERED sin cancelar puede traerla.
          <div className={`mt-1 text-[11px] font-semibold ${
            o.devolucion_estado === "recibida" || o.devolucion_estado === "cerrada" ? "text-slate-500" : "text-amber-700"}`}
               title={o.devolucion_estado ? AYUDA_DEVOLUCION[o.devolucion_estado]
                 : "La devolución de esta orden no quedó registrada."}>
            Devolución {o.devolucion_estado ?? "sin registrar"}
          </div>
        ) : o.cancelada_origen === "marketplace" || o.cancelada_origen === "sistema" ? (
          <div className="mt-1 text-[11px] text-slate-400" title={o.cancelada_motivo ?? undefined}>
            {o.cancelada_origen === "marketplace" ? "por el marketplace" : "por el sistema"}
          </div>
        ) : null}
      </td>
      <td className="px-3 py-3">
        <Traza orden={o} modo="mini" />
      </td>
      <td className="max-w-[240px] px-3 py-3">
        <div className={`truncate text-sm ${borrada ? "line-through" : ""}`}>
          {o.cliente?.trim()
            ? <span className="font-semibold text-slate-800">{o.cliente}</span>
            : <span className="text-slate-400">sin cliente</span>}
        </div>
        <div className="mt-0.5 truncate text-xs text-slate-500">
          {rotulo}{conCuenta ? ` · ${cuenta}` : ""}
        </div>
      </td>
      <td className="max-w-[200px] px-3 py-3">
        {o.mp_orden
          ? <span className="break-all font-mono text-xs text-slate-700">{o.mp_orden}</span>
          : <span className="text-slate-300">—</span>}
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="whitespace-nowrap text-xs text-slate-500">
            <b className="tabular-nums text-slate-800">{num(o.piezas)}</b> pzs
            <span className="text-slate-400"> · {num(o.renglones)} {o.renglones === 1 ? "renglón" : "renglones"}</span>
          </span>
          <ChipReserva reserva={reserva} detalle={detalleReserva(o, reserva, borrada)} />
        </div>
        {(reserva === "entrega_parcial" || salioDeMenos) && (
          <div className={`mt-1 whitespace-nowrap text-[11px] font-semibold tabular-nums ${
            reserva === "entrega_parcial" ? "text-amber-700" : "text-slate-500"}`}
               title={reserva === "entrega_parcial"
                 ? "Ya salieron algunos renglones. La orden sigue confirmada hasta que salga (o se suelte) el último."
                 : "Se entregó con menos piezas de las pedidas: lo que no salió se soltó."}>
            salieron {num(o.piezas_entregadas)} de {num(o.piezas)} pzs
          </div>
        )}
        {(bodegas.length > 0 || skus.length > 0) && (
          // De dónde sale y qué lleva, en UN renglón: la(s) bodega(s) en chip y
          // los SKUs detrás. Así la fila no crece por decir la bodega.
          <div className="mt-1 flex max-w-[280px] items-center gap-1.5">
            {bodegas.length > 0 && <span className="sr-only">{bodegas.length === 1 ? "Bodega:" : "Bodegas:"}</span>}
            {bodegas.map((b) => (
              <span key={b} title={nombresBodega[b] ? `Sale de ${nombresBodega[b]} (${b})` : `Sale de la bodega ${b}`}
                    className="inline-flex shrink-0 items-center rounded bg-slate-50 px-1.5 py-px font-mono text-[10.5px] font-semibold text-slate-500 ring-1 ring-slate-200">
                {b}
              </span>
            ))}
            {skus.length > 0 && (
              // El «+2» va fuera del truncado: si no, es lo primero que se come la elipsis.
              <div className="flex min-w-0 items-baseline gap-1 font-mono text-[11px] text-slate-400"
                   title={skus.join(" · ")}>
                <span className="truncate">{skus.join(" · ")}</span>
                {o.renglones > skus.length && <span className="shrink-0">+{o.renglones - skus.length}</span>}
              </div>
            )}
          </div>
        )}
      </td>
      <td className="px-3 py-3 text-right">
        <div className={`font-bold tabular-nums text-slate-900 ${borrada ? "line-through" : ""}`}>
          {dinero(o.total, o.moneda)}
        </div>
        {o.precio_origen === "marketplace" && (
          <div className="text-[11px] text-slate-400" title="El total es el precio real de la venta en el canal">
            precio de la venta
          </div>
        )}
      </td>
      <td className="px-3 py-3 text-xs">
        <div className="font-semibold text-slate-700">
          {creo}
          {conVia && <span className="font-normal text-slate-400"> · {via}</span>}
        </div>
        <div className="mt-0.5 tabular-nums text-slate-400">{fechaHora(o.creado_at)}</div>
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center justify-end gap-2.5 text-[11px] font-semibold tabular-nums text-slate-400">
          {o.n_archivos > 0 && (
            <span className="inline-flex items-center gap-0.5"
                  title={`${plural(o.n_archivos, "PDF adjunto", "PDF adjuntos")}`}>
              <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />{o.n_archivos}
              <span className="sr-only"> PDF adjuntos</span>
            </span>
          )}
          {o.n_mensajes > 0 && (
            <span className="inline-flex items-center gap-0.5"
                  title={`${plural(o.n_mensajes, "mensaje", "mensajes")} entre el chat y la bitácora`}>
              <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />{o.n_mensajes}
              <span className="sr-only"> mensajes</span>
            </span>
          )}
          <ChevronRight className="h-4 w-4 text-slate-300" aria-hidden="true" />
        </div>
      </td>
    </tr>
  );
}

/** La misma del Catálogo Maestro: primera, última y las vecinas de la actual. */
function Paginacion({
  pagina, total, ordenes, onPagina,
}: { pagina: number; total: number; ordenes: number | null; onPagina: (p: number) => void }) {
  if (ordenes === null) return null;
  if (total <= 1) {
    return <p className="mt-3 text-xs text-slate-400">{plural(ordenes, "orden", "órdenes")}</p>;
  }
  const paginas = Array.from({ length: total }, (_, i) => i + 1)
    .filter((p) => p === 1 || p === total || Math.abs(p - pagina) <= 1);
  return (
    <nav className="mt-3 flex flex-wrap items-center gap-2" aria-label="Páginas de la lista">
      <span className="text-xs text-slate-400">
        Página <b className="text-slate-600">{pagina}</b> de {total} · {plural(ordenes, "orden", "órdenes")}
      </span>
      <div className="ml-auto flex items-center gap-1">
        {paginas.map((p, i) => (
          <span key={p} className="flex items-center gap-1">
            {i > 0 && p - paginas[i - 1] > 1 && <span className="px-1 text-slate-300">…</span>}
            <button
              type="button" onClick={() => onPagina(p)} aria-current={p === pagina ? "page" : undefined}
              className={`h-8 min-w-8 rounded-lg px-2 text-xs font-bold ${
                p === pagina ? "bg-indigo-600 text-white"
                  : "border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"}`}
            >
              {p}
            </button>
          </span>
        ))}
      </div>
    </nav>
  );
}
