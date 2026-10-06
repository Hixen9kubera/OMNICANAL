"use client";

/**
 * Traza — el riel de una orden de venta: BORRADOR → CONFIRMADA → DELIVERED.
 *
 * Pedido de Brandon (2-oct-2026): «cada orden de venta deberá tener su pequeña
 * animación de trazabilidad con un worker para que el servidor de Railway no
 * mande potencia en hardware a esas animaciones, sino directamente del
 * dispositivo del usuario; lo más optimizada posible para ver correctamente en
 * qué status se encuentra la orden, hasta su punto de delivered».
 *
 * Por eso la animación NO es de React ni del hilo de la interfaz: cada traza es
 * un `<canvas>` cuyo control se le entrega (OffscreenCanvas) a UN Web Worker
 * (`traza.worker.ts`) que dibuja todas las de la pantalla. El servidor no
 * participa —el worker no pide nada ni conoce el token— y la lista puede traer
 * 50 órdenes vivas sin que el scroll o el teclado pierdan un cuadro.
 *
 * Decisiones de ahorro de ESTE lado (las del bucle están en el worker):
 *  · Un solo worker para todas las trazas: singleton perezoso con conteo de
 *    referencias. Al quedarse sin trazas se termina, con unos segundos de gracia
 *    para que pasar de la lista al documento (o el doble montaje de StrictMode)
 *    no lo mate y lo vuelva a crear.
 *  · Un solo IntersectionObserver para todas: lo que no se ve no se anima.
 *  · Pestaña oculta y `prefers-reduced-motion` se avisan UNA vez al worker, no
 *    por traza.
 *  · A React sólo le llegan los cambios de estado: mientras la orden no cambie,
 *    este componente no vuelve a mandar nada.
 *
 * El `<canvas>` se crea de forma IMPERATIVA dentro del efecto, en un `<div>`
 * que React nunca llena: `transferControlToOffscreen()` sólo se puede llamar
 * una vez por elemento y StrictMode monta dos veces.
 *
 * RESPALDO: el mismo riel, quieto, en SVG. Es lo que se pre-renderiza al
 * compilar, lo que se ve mientras el worker pinta su primer cuadro, y lo que se
 * queda si el navegador no tiene OffscreenCanvas o el worker truena. Los dos
 * salen de los MISMOS números (`BASE`), que este archivo le manda al worker:
 * no hay salto de diseño ni dos rieles que mantener parejos.
 *
 * En modo «completa» los rótulos van DEBAJO, en DOM y no en el lienzo: el texto
 * se ve nítido, se puede seleccionar y lo lee un lector de pantalla.
 *
 * LO QUE TRAJO LA 0064 (6-oct-2026). Siguen siendo tres estaciones, pero una
 * CONFIRMADA ya no es un punto fijo, y hay dos cosas suyas que el riel dice:
 *  · ENTREGA PARCIAL. Se entrega renglón por renglón; mientras quede alguno por
 *    salir la orden sigue confirmada. El tramo a DELIVERED se pinta recorrido en
 *    la proporción de piezas que ya salieron, y la leyenda lo pone en números.
 *  · EL CANAL CANCELÓ con el paquete en camino. La orden espera que Bodega
 *    conteste «¿salió?» y no puede pasar a DELIVERED: la estación actual va en
 *    ámbar, con un pulso lento.
 * Las dos salen de `planDe` (pura, con pruebas) y viajan al worker como
 * `avance` y `alerta`; el respaldo en SVG las pinta con los mismos números.
 */

import { useEffect, useRef, useState } from "react";
import type { OrdenResumen } from "./tipos";
import type {
  AvisoTraza, BaseTraza, EstadoTraza, LlegoTraza, MensajeTraza, ModoTraza,
} from "./traza.worker";
import { AYUDA_ESTADO, COLOR_ESTADO, ROTULO_ESTADO, fechaHora, num, quien } from "./ui";

// ── La forma: una sola verdad para el lienzo y para el SVG ────────────────────

const MINI_ANCHO = 150;
/** Una orden borrada se ve entera, pero apagada. */
const ATENUADA = 0.4;
const GRACIA_MS = 2500;
/**
 * El tramo a medias nunca se pinta ni vacío ni lleno: 1 pieza de 100 son 0.5 px
 * en el riel de la lista (no se vería que algo ya salió) y 99 de 100 se
 * confundirían con una entregada. La proporción manda dentro de estos topes; el
 * número exacto está en la leyenda y en el `title`.
 *
 * El piso es 0.18 y no menos por el riel de la lista: ahí el tramo mide 56 px y
 * el halo de la estación en curso llega a 6.5 px de su centro. Con menos, la
 * cabeza del tramo queda metida en el halo y no se distingue (se vio pintándolo).
 */
const AVANCE_MIN = 0.18;
const AVANCE_MAX = 0.85;

const BASE: BaseTraza = {
  colores: COLOR_ESTADO,
  // slate-200 la vía pendiente, slate-300 el borde de una estación por llegar,
  // slate-400 el contorno punteado de la orden sin crear; amber-600 la alerta
  // («el canal canceló»), el mismo ámbar de DELIVERED but CANCELLED.
  tonos: { riel: "#e2e8f0", borde: "#cbd5e1", punteado: "#94a3b8", tinta: "#ffffff", alerta: "#d97706" },
  geo: {
    // 150×22: cabe el halo de la primera estación a la izquierda y la marca de
    // regreso con su pulso a la derecha, sin recortarse.
    mini: { alto: 22, r: 4, rHecha: 5.5, rMarca: 5.5, grosor: 2, aire: 2.5, rAvance: 2.2,
            x: [10 / 150, 66 / 150, 122 / 150], vuelta: 139 / 150 },
    // Las estaciones caen al centro de cada tercio: justo encima de su rótulo.
    completa: { alto: 64, r: 7, rHecha: 9, rMarca: 9, grosor: 3, aire: 4.5, rAvance: 3.2,
                x: [1 / 6, 1 / 2, 5 / 6], vuelta: 11 / 12 },
  },
  glifos: {
    palomita: "M-0.44 0.02L-0.12 0.34L0.46 -0.3",
    equis: "M-0.36 -0.36L0.36 0.36M0.36 -0.36L-0.36 0.36",
    // Flecha de vuelta (↩): sale a la derecha, da la media vuelta y regresa.
    vuelta: "M-0.05 -0.36L0.12 -0.36A0.32 0.32 0 0 1 0.12 0.28L-0.42 0.28M-0.16 0.02L-0.44 0.28L-0.16 0.54",
    grosor: 0.26,
  },
};

export interface Plan {
  estado: EstadoTraza;
  llego: LlegoTraza;
  /** Devolución PENDIENTE (sólo en DELIVERED but CANCELLED). */
  devolucion: boolean;
  borrada: boolean;
  /**
   * Entrega parcial: qué fracción (0..1) del tramo CONFIRMADA → DELIVERED se
   * pinta recorrida. 0 = no ha salido ningún renglón.
   */
  avance: number;
  /** El canal canceló y la orden sigue esperando el «¿salió?». */
  alerta: boolean;
}

/** Lo que un número del backend vale para dibujar: lo que no es un número finito, 0. */
const cuenta = (v: number | null | undefined): number =>
  (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0);

/**
 * La fracción del tramo a DELIVERED que ya se recorrió. Sólo una CONFIRMADA
 * tiene entrega a medias: cuando sale el último renglón pasa a `entregada` (y
 * si se cancela con piezas fuera, a `entregada_cancelada`), que ya pintan la
 * tercera estación.
 *
 * Manda `renglones_entregados`, no las piezas: un renglón puede «entregarse»
 * con 0 piezas (no salió nada y se soltó su apartado). Ahí la proporción es 0,
 * pero la orden YA ES una entrega parcial y el riel lo tiene que decir: por eso
 * el piso.
 */
export function avanceDe(o: Pick<OrdenResumen, "estado" | "piezas" | "piezas_entregadas" | "renglones_entregados">): number {
  if (o.estado !== "confirmada" || cuenta(o.renglones_entregados) === 0) return 0;
  const piezas = cuenta(o.piezas);
  const proporcion = piezas > 0 ? cuenta(o.piezas_entregadas) / piezas : 0;
  return Math.min(AVANCE_MAX, Math.max(AVANCE_MIN, proporcion));
}

/**
 * Lo que hay que dibujar. Una cancelada se corta donde iba: si alcanzó a
 * confirmarse conserva `confirmada_at` (fuera de borrador ya nada se reescribe),
 * así que ese dato basta para saber en qué estación estaba.
 */
export function planDe(o: OrdenResumen | null): Plan {
  if (!o) return { estado: "nueva", llego: -1, devolucion: false, borrada: false, avance: 0, alerta: false };
  const llego: LlegoTraza = o.estado === "borrador" ? 0
    : o.estado === "confirmada" ? 1
    : o.estado === "cancelada" ? (o.confirmada_at ? 1 : 0)
    : 2;
  const borrada = !!o.borrada_at;
  return {
    estado: o.estado,
    llego,
    devolucion: o.estado === "entregada_cancelada" && o.devolucion_estado === "pendiente",
    borrada,
    avance: avanceDe(o),
    // La marca del canal se queda puesta para siempre (cancelada, DELIVERED but
    // CANCELLED): la ALERTA es sólo mientras alguien tiene que contestar, o sea
    // en una confirmada viva.
    alerta: o.estado === "confirmada" && !!o.canal_cancelo_at && !borrada,
  };
}

/** El nombre accesible del riel: el estado, y lo que el dibujo dice de más. */
function rotuloDe(o: OrdenResumen | null, plan: Plan): string {
  if (plan.estado === "nueva" || !o) return "Orden sin crear";
  let r = ROTULO_ESTADO[plan.estado];
  if (plan.avance > 0) r += ` · entrega parcial: salieron ${num(o.piezas_entregadas)} de ${num(o.piezas)} pzs`;
  if (plan.alerta) r += " · el canal canceló: ¿salió?";
  if (plan.borrada) r += " · borrada";
  return r;
}

// ── El motor: un worker para todas las trazas ─────────────────────────────────

interface Motor {
  refs: number;
  enviar: (m: MensajeTraza, lienzo?: OffscreenCanvas) => void;
  ojo: IntersectionObserver | null;
  /** Elemento observado → id de su traza (el ojo es uno solo para todas). */
  deElemento: WeakMap<Element, number>;
  /** id → qué hacer cuando el worker pintó su primer cuadro. */
  alListo: Map<number, () => void>;
  apagar: () => void;
}

let motor: Motor | null = null;
/** El worker no se pudo crear o tronó: de aquí en adelante, todas en SVG. */
let roto = false;
let gracia: ReturnType<typeof setTimeout> | null = null;
let serie = 0;
const alRomperse = new Set<() => void>();

function hayConQue(): boolean {
  return typeof window !== "undefined"
    && typeof Worker !== "undefined"
    && typeof OffscreenCanvas !== "undefined"
    && typeof HTMLCanvasElement !== "undefined"
    && typeof HTMLCanvasElement.prototype.transferControlToOffscreen === "function";
}

function nitidez(): number {
  return Math.min(window.devicePixelRatio || 1, 2);
}

function romper(): void {
  if (roto) return;
  roto = true;
  const m = motor;
  motor = null;
  if (gracia !== null) {
    clearTimeout(gracia);
    gracia = null;
  }
  m?.apagar();
  // Copia: cada traza se da de baja de la lista al atender el aviso.
  Array.from(alRomperse).forEach((avisar) => avisar());
}

function crearMotor(): Motor | null {
  let worker: Worker;
  try {
    worker = new Worker(new URL("./traza.worker.ts", import.meta.url));
  } catch {
    return null;
  }
  const enviar = (m: MensajeTraza, lienzo?: OffscreenCanvas) => {
    if (lienzo) worker.postMessage(m, [lienzo]);
    else worker.postMessage(m);
  };
  const deElemento = new WeakMap<Element, number>();
  const alListo = new Map<number, () => void>();

  worker.onmessage = (ev: MessageEvent<AvisoTraza>) => {
    const a = ev.data;
    if (a.t === "lista") alListo.get(a.id)?.();
    else if (a.t === "sin_lienzo") romper();
  };
  worker.onerror = () => romper();

  // Pestaña oculta = bucle detenido; «reducir movimiento» = sólo cuadros quietos.
  const alCambiarPestana = () => enviar({ t: "pausa", v: document.visibilityState !== "visible" });
  document.addEventListener("visibilitychange", alCambiarPestana);
  const quiereCalma = typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const alCalmar = () => enviar({ t: "calma", v: !!quiereCalma?.matches });
  quiereCalma?.addEventListener?.("change", alCalmar);

  const ojo = typeof IntersectionObserver === "undefined" ? null
    : new IntersectionObserver((entradas) => {
        for (const en of entradas) {
          const id = deElemento.get(en.target);
          if (id !== undefined) enviar({ t: "visible", id, v: en.isIntersecting });
        }
      });

  enviar({ t: "base", base: BASE });
  alCambiarPestana();
  alCalmar();

  return {
    refs: 0, enviar, ojo, deElemento, alListo,
    apagar: () => {
      document.removeEventListener("visibilitychange", alCambiarPestana);
      quiereCalma?.removeEventListener?.("change", alCalmar);
      ojo?.disconnect();
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    },
  };
}

function tomarMotor(): Motor | null {
  if (roto || !hayConQue()) return null;
  if (gracia !== null) {
    clearTimeout(gracia);
    gracia = null;
  }
  if (!motor) motor = crearMotor();
  if (!motor) {
    roto = true;
    return null;
  }
  motor.refs++;
  return motor;
}

function soltarMotor(m: Motor): void {
  m.refs--;
  if (m !== motor || m.refs > 0) return;
  // Sin trazas el worker ya no gasta (su bucle se detuvo solo); se termina tras
  // una gracia para no matarlo y recrearlo en cada cambio de vista.
  gracia = setTimeout(() => {
    gracia = null;
    if (motor === m && m.refs <= 0) {
      motor = null;
      m.apagar();
    }
  }, GRACIA_MS);
}

// ── El componente ─────────────────────────────────────────────────────────────

export function Traza({ orden, modo }: { orden: OrdenResumen | null; modo: "mini" | "completa" }): JSX.Element {
  const caja = useRef<HTMLDivElement>(null);
  const vivo = useRef<{ m: Motor; id: number; cv: HTMLCanvasElement } | null>(null);
  /** El worker ya pintó: se quita el SVG de respaldo. */
  const [lienzo, setLienzo] = useState(false);
  const plan = planDe(orden);
  const { estado, llego, devolucion, borrada, avance, alerta } = plan;

  // Alta y baja del lienzo. Va ANTES que el efecto del estado: en cada montaje
  // (y en el doble de StrictMode) primero existe la traza y luego se le dice qué
  // dibujar.
  useEffect(() => {
    const el = caja.current;
    const m = el ? tomarMotor() : null;
    if (!el || !m) return;
    const g = BASE.geo[modo];

    const cv = document.createElement("canvas");
    cv.setAttribute("aria-hidden", "true");
    cv.style.cssText = "display:block;width:100%;height:100%";
    let fuera: OffscreenCanvas;
    try {
      fuera = cv.transferControlToOffscreen();
    } catch {
      soltarMotor(m);
      return;
    }
    el.appendChild(cv);

    const id = ++serie;
    // La mini mide siempre lo mismo; no se le pregunta al DOM (una tabla oculta
    // contestaría 0 y la traza no se pintaría nunca).
    const ancho = () => (modo === "mini" ? MINI_ANCHO : el.clientWidth);
    let w = ancho();
    let dpr = nitidez();
    m.enviar({ t: "alta", id, canvas: fuera, w, h: g.alto, dpr, modo }, fuera);
    vivo.current = { m, id, cv };

    // El SVG se quita un cuadro DESPUÉS del aviso: el primer dibujo del worker
    // llega a la pantalla por su cuenta y así no se ve un hueco entre los dos.
    let cuadro = 0;
    m.alListo.set(id, () => {
      cuadro = requestAnimationFrame(() => setLienzo(true));
    });

    const alRomper = () => {
      alRomperse.delete(alRomper);
      cancelAnimationFrame(cuadro);
      if (vivo.current?.id === id) vivo.current = null;
      cv.remove();
      setLienzo(false);
    };
    alRomperse.add(alRomper);

    if (m.ojo) {
      m.deElemento.set(el, id);
      m.ojo.observe(el);
    } else {
      m.enviar({ t: "visible", id, v: true });
    }

    let medidor: ResizeObserver | null = null;
    if (modo === "completa" && typeof ResizeObserver !== "undefined") {
      medidor = new ResizeObserver(() => {
        const w2 = ancho();
        const d2 = nitidez();
        if (w2 === w && d2 === dpr) return;
        w = w2;
        dpr = d2;
        m.enviar({ t: "medida", id, w, h: g.alto, dpr });
      });
      medidor.observe(el);
    }

    return () => {
      alRomperse.delete(alRomper);
      cancelAnimationFrame(cuadro);
      medidor?.disconnect();
      if (m.ojo) {
        m.ojo.unobserve(el);
        m.deElemento.delete(el);
      }
      m.alListo.delete(id);
      if (vivo.current?.id === id) vivo.current = null;
      if (motor === m) m.enviar({ t: "baja", id });
      cv.remove();
      setLienzo(false);
      soltarMotor(m);
    };
  }, [modo]);

  // El estado. Sólo viaja cuando cambia algo de lo que se dibuja.
  useEffect(() => {
    const v = vivo.current;
    if (!v) return;
    // Atenuar es cosa del elemento, no del dibujo: con transparencia dentro del
    // lienzo, donde un tramo pasa bajo una estación se vería más oscuro.
    v.cv.style.opacity = borrada ? String(ATENUADA) : "";
    v.m.enviar({ t: "estado", id: v.id, estado, llego, devolucion, borrada, avance, alerta });
  }, [estado, llego, devolucion, borrada, avance, alerta, modo]);

  const g = BASE.geo[modo];
  const rotulo = rotuloDe(orden, plan);
  const riel = (
    <div role="img" aria-label={rotulo}
         title={modo === "mini" || estado === "nueva" ? rotulo : `${rotulo} — ${AYUDA_ESTADO[estado]}`}
         className="relative shrink-0"
         style={{ width: modo === "mini" ? MINI_ANCHO : "100%", height: g.alto }}>
      {!lienzo && <RielQuieto plan={plan} modo={modo} />}
      <div ref={caja} className="absolute inset-0" />
    </div>
  );
  if (modo === "mini") return riel;
  return (
    <div className="w-full">
      {riel}
      <Leyenda orden={orden} />
    </div>
  );
}

// ── El respaldo: el mismo riel, quieto, en SVG ────────────────────────────────

/** Las posiciones van en porcentaje: el SVG no necesita conocer su ancho. */
const pc = (f: number) => `${+(f * 100).toFixed(3)}%`;

function RielQuieto({ plan, modo }: { plan: Plan; modo: ModoTraza }) {
  const g = BASE.geo[modo];
  const ton = BASE.tonos;
  const col = BASE.colores;
  const { estado: e, llego, devolucion, borrada } = plan;
  const y = g.alto / 2;
  const cancelada = e === "cancelada";
  const hecha = e === "entregada" || e === "entregada_cancelada";
  const color = e === "nueva" ? ton.borde : hecha ? col.entregada : col[e];
  const enCurso = !borrada && (e === "borrador" || e === "confirmada");
  // Igual que en el worker: las dos son de una CONFIRMADA y de nada más.
  const parcial = e === "confirmada" && plan.avance > 0;
  const alerta = e === "confirmada" && plan.alerta;
  const fl = g.x[Math.max(0, llego)];
  const fm = cancelada ? (fl + g.x[llego >= 1 ? 2 : 1]) / 2 : 0;
  const fa = g.x[1] + (g.x[2] - g.x[1]) * Math.min(1, Math.max(0, plan.avance));

  const tramo = (a: number, b: number, c: string) => (
    <line x1={pc(a)} x2={pc(b)} y1={y} y2={y} stroke={c} strokeWidth={g.grosor} strokeLinecap="round" />
  );
  const glifo = (d: string, r: number) => (
    <path d={d} transform={`scale(${r})`} fill="none" stroke={ton.tinta}
          strokeWidth={BASE.glifos.grosor} strokeLinecap="round" strokeLinejoin="round" />
  );
  // Un `<svg>` anidado acepta `x` en porcentaje; un `<g transform>` no.
  const marca = (f: number, c: string, d: string) => (
    <svg x={pc(f)} y={y} style={{ overflow: "visible" }}>
      <circle r={g.rMarca} fill={c} />
      {glifo(d, g.rMarca)}
    </svg>
  );

  return (
    <svg width="100%" height={g.alto} aria-hidden="true" className="absolute inset-0 block"
         style={borrada ? { opacity: ATENUADA } : undefined}>
      {!cancelada && llego < 2 && tramo(fl, g.x[2], ton.riel)}
      {llego >= 1 && tramo(g.x[0], fl, color)}
      {cancelada && tramo(fl, fm, color)}
      {e === "entregada_cancelada" && tramo(g.x[2], g.vuelta, col.entregada_cancelada)}
      {parcial && tramo(g.x[1], fa, color)}
      {parcial && <circle cx={pc(fa)} cy={y} r={g.rAvance} fill={color} />}
      {(alerta || enCurso) && (
        <circle cx={pc(fl)} cy={y} r={g.r + 2.5} fill="none" stroke={alerta ? ton.alerta : color}
                strokeWidth={1.5} opacity={0.3} />
      )}
      {g.x.map((f, i) => i <= llego ? (
        <circle key={i} cx={pc(f)} cy={y} r={i === 2 && hecha ? g.rHecha : g.r}
                fill={alerta && i === llego ? ton.alerta : color} />
      ) : (
        <circle key={i} cx={pc(f)} cy={y} r={g.r - 0.75} fill={ton.tinta} strokeWidth={1.5}
                stroke={e === "nueva" && i === 0 ? ton.punteado : cancelada ? ton.riel : ton.borde}
                strokeDasharray={e === "nueva" && i === 0 ? "2.5 2.5" : undefined} />
      ))}
      {hecha && (
        <svg x={pc(g.x[2])} y={y} style={{ overflow: "visible" }}>{glifo(BASE.glifos.palomita, g.rHecha)}</svg>
      )}
      {cancelada && marca(fm, color, BASE.glifos.equis)}
      {e === "entregada_cancelada" && devolucion && (
        <circle cx={pc(g.vuelta)} cy={y} r={g.rMarca + 2.5} fill="none"
                stroke={col.entregada_cancelada} strokeWidth={1.5} opacity={0.3} />
      )}
      {e === "entregada_cancelada" && marca(g.vuelta, col.entregada_cancelada, BASE.glifos.vuelta)}
    </svg>
  );
}

// ── Los rótulos del modo completo ─────────────────────────────────────────────

/** `ov_ordenes.cancelada_origen`: quién canceló (la 0064 agregó `sistema`). */
const ORIGEN_CANCELACION: Record<"manual" | "marketplace" | "sistema", string> = {
  manual: "manual",
  marketplace: "por el marketplace",
  sistema: "por el sistema",
};

/**
 * El separador de la leyenda: espacio DURO (U+00A0) + punto medio. Pegado a lo
 * que le precede, al partirse el renglón ninguna línea empieza con «·».
 */
const PUNTO = `${String.fromCharCode(0xa0)}· `;

function Leyenda({ orden: o }: { orden: OrdenResumen | null }) {
  // «Pendiente» sólo donde todavía puede pasar; en una orden que ya no avanza
  // (cancelada, borrada) la estación que no se alcanzó va con raya, no con promesa.
  const avanza = !!o && !o.borrada_at && (o.estado === "borrador" || o.estado === "confirmada");
  const pasos: { rotulo: string; por: string | null; cuando: string | null; falta: string }[] = [
    { rotulo: "Creada", por: o ? quien(o.creado_nombre, o.creado_por) : null,
      cuando: o?.creado_at ?? null, falta: "al guardar" },
    { rotulo: "Confirmada", por: o?.confirmada_at ? quien(o.confirmada_nombre, o.confirmada_por) : null,
      cuando: o?.confirmada_at ?? null, falta: avanza ? "pendiente" : "—" },
    { rotulo: "Delivered", por: o?.entregada_at ? quien(o.entregada_nombre, o.entregada_por) : null,
      cuando: o?.entregada_at ?? null, falta: avanza ? "pendiente" : "—" },
  ];
  const cancelada = !!o && (o.estado === "cancelada" || o.estado === "entregada_cancelada");
  const regresa = o?.estado === "entregada_cancelada";
  // Las mismas dos reglas del dibujo, para que el texto no diga otra cosa.
  const plan = planDe(o);
  const parcial = !!o && plan.avance > 0;
  const renglonesFuera = o?.renglones_entregados ?? 0;

  return (
    <>
      <ol className="mt-1 grid grid-cols-3">
        {pasos.map((p) => (
          <li key={p.rotulo} className="min-w-0 px-1 text-center">
            <p className={`text-[10px] font-bold uppercase tracking-[0.06em] ${p.cuando ? "text-slate-700" : "text-slate-400"}`}>
              {p.rotulo}
            </p>
            {p.cuando ? (
              <>
                <p className="truncate text-[12px] font-semibold text-slate-800" title={p.por ?? undefined}>{p.por}</p>
                <p className="font-mono text-[11px] tabular-nums text-slate-500">{fechaHora(p.cuando)}</p>
              </>
            ) : (
              <p className="text-[12px] text-slate-400">{p.falta}</p>
            )}
          </li>
        ))}
      </ol>
      {/* El punto medio va pegado (espacio duro) a lo que le precede: al partirse
          el renglón en una tarjeta angosta, ninguna línea empieza con «·». */}
      {parcial && o && (
        <p className="mt-2 text-center text-[12px] leading-relaxed text-indigo-700"
           title="Ya salieron algunos renglones. La orden sigue confirmada hasta que salga (o se suelte) el último.">
          <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-indigo-500 align-middle" />
          <b>Entrega parcial</b>{PUNTO}
          <span className="tabular-nums">{num(o.piezas_entregadas)} de {num(o.piezas)} pzs</span>
          {renglonesFuera > 0 && o.renglones > 0 ? (
            <span className="text-indigo-700/70">{PUNTO}{num(renglonesFuera)} de {num(o.renglones)} renglones</span>
          ) : null}
        </p>
      )}
      {plan.alerta && o && (
        <p className="mt-2 text-center text-[12px] leading-relaxed text-amber-800"
           title={"El canal canceló la venta con el paquete ya en camino"
             + (o.canal_cancelo_ref ? ` (estado del envío en el canal: ${o.canal_cancelo_ref})` : "")
             + ". Bodega tiene que contestar si salió; mientras tanto no se puede marcar DELIVERED."}>
          <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 align-middle" />
          <b>El canal canceló</b>{PUNTO}¿salió?{PUNTO}
          <span className="whitespace-nowrap font-mono text-[11px] tabular-nums">{fechaHora(o.canal_cancelo_at)}</span>
        </p>
      )}
      {/* Una DELIVERED sin cancelar también puede traer devolución (0064): se dice. */}
      {o?.estado === "entregada" && o.devolucion_estado && !o.borrada_at && (
        <p className={`mt-2 text-center text-[12px] leading-relaxed ${
          o.devolucion_estado === "pendiente" ? "text-amber-800" : "text-slate-500"}`}>
          <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
            o.devolucion_estado === "pendiente" ? "bg-amber-500" : "bg-slate-400"}`} />
          <b>Devolución</b>{PUNTO}{o.devolucion_estado}
        </p>
      )}
      {cancelada && o && (
        <p title={o.cancelada_motivo ?? undefined}
           className={`mt-2 text-center text-[12px] leading-relaxed ${regresa ? "text-amber-800" : "text-rose-700"}`}>
          <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${regresa ? "bg-amber-500" : "bg-rose-500"}`} />
          <b>Cancelada</b>{PUNTO}
          {quien(o.cancelada_nombre, o.cancelada_por)}{PUNTO}
          <span className="whitespace-nowrap font-mono text-[11px] tabular-nums">{fechaHora(o.cancelada_at)}</span>{PUNTO}
          {ORIGEN_CANCELACION[o.cancelada_origen ?? "manual"]}
          {regresa && o.devolucion_estado ? `${PUNTO}devolución ${o.devolucion_estado}` : ""}
        </p>
      )}
      {o?.borrada_at && (
        <p title={o.borrada_motivo ?? undefined} className="mt-2 text-center text-[12px] leading-relaxed text-slate-500">
          <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-slate-400 align-middle" />
          <b>Borrada</b>{PUNTO}
          {quien(o.borrada_nombre, o.borrada_por)}{PUNTO}
          <span className="whitespace-nowrap font-mono text-[11px] tabular-nums">{fechaHora(o.borrada_at)}</span>
        </p>
      )}
    </>
  );
}
