/**
 * traza.worker.ts — el dibujante de la traza de las órdenes de venta.
 *
 * POR QUÉ UN WORKER (pedido de Brandon, 2-oct-2026): «cada orden deberá tener su
 * pequeña animación de trazabilidad con un worker, para que el servidor de
 * Railway no mande potencia en hardware a esas animaciones, sino directamente
 * del dispositivo del usuario». El servidor no participa: aquí no hay `fetch`,
 * ni token, ni datos de la orden. Llegan seis valores por traza (estado, hasta
 * qué estación llegó, si hay devolución pendiente, si está borrada, cuánto del
 * tramo a DELIVERED ya salió y si el canal canceló) y sale un dibujo. Y corre
 * FUERA del hilo de la interfaz: una lista de 50 órdenes animándose no le quita
 * un cuadro al scroll ni al teclado de quien captura.
 *
 * DOS COSAS DEL MODELO DE LA 0064 (6-oct-2026) QUE AQUÍ SE VEN:
 *  · ENTREGA PARCIAL (`avance`, 0..1). Una confirmada entrega renglón por
 *    renglón: mientras quede alguno por salir sigue CONFIRMADA, pero ya no está
 *    «parada en la estación». El tramo a DELIVERED se pinta recorrido hasta
 *    `avance`, con una cabeza, y el pulso viaja sólo por lo que FALTA.
 *  · EL CANAL CANCELÓ (`alerta`). La orden espera que Bodega conteste «¿salió?»
 *    y ya NO puede pasar a DELIVERED: la estación actual se pinta en ámbar con
 *    un pulso lento, y el pulso que viajaba hacia DELIVERED se apaga (no va
 *    para allá hasta que alguien conteste).
 *  Las dos son de una CONFIRMADA; en cualquier otro estado se ignoran.
 *
 * CADA DECISIÓN DE AHORRO
 *  · UN worker y UN bucle para todas las trazas de la pantalla (no uno por orden).
 *  · El bucle sólo recorre `animadas`: las trazas VIVAS (borrador, confirmada, o
 *    devolución pendiente) que además están A LA VISTA. Esa lista se rehace con
 *    los avisos —que son raros—, no en cada cuadro.
 *  · Los estados terminales (DELIVERED, CANCELADO, sin crear, borrada) se pintan
 *    UNA vez, al cambiar de estado o de medida, y no vuelven a costar nada.
 *  · Cuando `animadas` queda vacía el bucle SE DETIENE: cero temporizadores, cero
 *    CPU. Se reanuda solo cuando un aviso vuelve a meter algo en la lista.
 *  · ~30 cuadros por segundo: un pulso suave no gana nada con 60 y cuesta la mitad.
 *  · Pestaña oculta (`pausa`) = bucle detenido. `prefers-reduced-motion`
 *    (`calma`) = sólo cuadros quietos, con el halo fijo en la estación en curso.
 *  · Nada se asigna por cuadro: las posiciones en píxeles se calculan al dar de
 *    alta o medir, los glifos son `Path2D` hechos una vez, no hay degradados.
 *  · Nitidez con devicePixelRatio (tope 2, lo fija quien manda): más que eso son
 *    píxeles que nadie distingue en un riel de 22 px.
 *
 * LA FORMA NO VIVE AQUÍ. Colores, medidas y glifos llegan en el aviso `base`
 * desde Traza.tsx, que con esos MISMOS números pinta el respaldo en SVG (sin
 * OffscreenCanvas, o si este worker truena). Así el riel animado y el estático
 * no se pueden desalinear. Aquí sólo vive el movimiento.
 *
 * TypeScript: el tsconfig no trae la lib `webworker` (choca con `dom`), así que
 * el ámbito del worker se tipa con un cast local.
 */

import type { EstadoOrden } from "./tipos";

// ── Protocolo (lo importa Traza.tsx sólo como tipos: no arrastra este archivo) ──

/** `nueva` = la orden todavía no se crea (el documento vacío). */
export type EstadoTraza = EstadoOrden | "nueva";
export type ModoTraza = "mini" | "completa";
/** Última estación alcanzada: −1 ninguna, 0 BORRADOR, 1 CONFIRMADA, 2 DELIVERED. */
export type LlegoTraza = -1 | 0 | 1 | 2;

/** Las medidas de un modo. `x` y `vuelta` son FRACCIONES del ancho. */
export interface GeoTraza {
  alto: number;
  /** Radio de una estación, de la tercera ya entregada (lleva palomita) y de las marcas. */
  r: number;
  rHecha: number;
  rMarca: number;
  grosor: number;
  /** Cuánto crece el halo al respirar. */
  aire: number;
  /** Radio de la cabeza del tramo a medias (entrega parcial). */
  rAvance: number;
  x: [number, number, number];
  /** Dónde va la marca de regreso (DELIVERED but CANCELLED), tras la tercera estación. */
  vuelta: number;
}

export interface BaseTraza {
  colores: Record<EstadoOrden, string>;
  /** `alerta` = el ámbar de «el canal canceló: ¿salió?». */
  tonos: { riel: string; borde: string; punteado: string; tinta: string; alerta: string };
  geo: Record<ModoTraza, GeoTraza>;
  /** Trazos en coordenadas de radio 1 (sintaxis de `<path d>`), y su grosor en esa escala. */
  glifos: { palomita: string; equis: string; vuelta: string; grosor: number };
}

export type MensajeTraza =
  | { t: "base"; base: BaseTraza }
  | { t: "alta"; id: number; canvas: OffscreenCanvas; w: number; h: number; dpr: number; modo: ModoTraza }
  | {
      t: "estado"; id: number; estado: EstadoTraza; llego: LlegoTraza; devolucion: boolean; borrada: boolean;
      /** Entrega parcial: fracción (0..1) del tramo CONFIRMADA → DELIVERED ya recorrida. 0 = nada. */
      avance: number;
      /** El canal canceló y la orden espera el «¿salió?». */
      alerta: boolean;
    }
  | { t: "medida"; id: number; w: number; h: number; dpr: number }
  | { t: "visible"; id: number; v: boolean }
  | { t: "pausa"; v: boolean }
  | { t: "calma"; v: boolean }
  | { t: "baja"; id: number };

/** Lo único que el worker contesta: «ya pinté el primer cuadro» y «aquí no puedo dibujar». */
export type AvisoTraza = { t: "lista"; id: number } | { t: "sin_lienzo" };

// ── Ámbito ────────────────────────────────────────────────────────────────────

const ambito = self as unknown as {
  onmessage: ((e: MessageEvent) => void) | null;
  postMessage: (m: unknown) => void;
  requestAnimationFrame?: (cb: (t: number) => void) => number;
  cancelAnimationFrame?: (h: number) => void;
};

const TAU = Math.PI * 2;
const CUADRO_MS = 1000 / 30;
const PULSO_MS = 2200;     // lo que tarda el pulso en recorrer el tramo que sigue
const RESPIRA_MS = 2600;   // una respiración de la estación en curso
const VUELTA_MS = 3400;    // el pulso ámbar de la devolución pendiente: más lento, no urge igual
const VIGIA_MS = 500;
const SOLIDO: number[] = [];
const PUNTEADO = [2.5, 2.5];

interface TrazaW {
  id: number;
  lienzo: OffscreenCanvas;
  ctx: OffscreenCanvasRenderingContext2D;
  modo: ModoTraza;
  w: number;
  h: number;
  estado: EstadoTraza | null;
  llego: number;
  devolucion: boolean;
  borrada: boolean;
  /** Fracción del tramo a DELIVERED ya recorrida (entrega parcial). */
  avance: number;
  /** El canal canceló: la estación actual va en ámbar. */
  alerta: boolean;
  visible: boolean;
  /** ¿Tiene algo que animar? (se deriva del estado; borrada nunca) */
  viva: boolean;
  avisada: boolean;
  // Posiciones ya en píxeles: se calculan al medir, no en cada cuadro.
  x0: number;
  x1: number;
  x2: number;
  xv: number;
  /** Hasta dónde llega el tramo a medias (= x1 si no hay avance). */
  xa: number;
  y: number;
}

const trazas = new Map<number, TrazaW>();
/** Las que el bucle pinta: vivas Y a la vista. */
const animadas: TrazaW[] = [];

let base: BaseTraza | null = null;
let palomita: Path2D | null = null;
let equis: Path2D | null = null;
let vuelta: Path2D | null = null;

let pausa = false;
let calma = false;
let corriendo = false;
let conRaf = typeof ambito.requestAnimationFrame === "function";
let manija = 0;
let reloj: ReturnType<typeof setTimeout> | null = null;
let vigia: ReturnType<typeof setTimeout> | null = null;
let ultimo = 0;
let cuadros = 0;

// ── Medida ────────────────────────────────────────────────────────────────────

function situar(tr: TrazaW): void {
  if (base === null) return;
  const g = base.geo[tr.modo];
  tr.x0 = g.x[0] * tr.w;
  tr.x1 = g.x[1] * tr.w;
  tr.x2 = g.x[2] * tr.w;
  tr.xv = g.vuelta * tr.w;
  tr.xa = tr.x1 + (tr.x2 - tr.x1) * tr.avance;
  tr.y = tr.h / 2;
}

function ajustar(tr: TrazaW, w: number, h: number, dpr: number): void {
  tr.w = w;
  tr.h = h;
  // Cambiar el tamaño del lienzo borra el dibujo y reinicia el contexto: por eso
  // el `setTransform` va aquí, en cada alta y cada medida.
  tr.lienzo.width = Math.max(1, Math.round(w * dpr));
  tr.lienzo.height = Math.max(1, Math.round(h * dpr));
  tr.ctx.setTransform(w >= 1 ? tr.lienzo.width / w : dpr, 0, 0, h >= 1 ? tr.lienzo.height / h : dpr, 0, 0);
  situar(tr);
}

function esViva(tr: TrazaW): boolean {
  if (tr.estado === null || tr.borrada) return false;
  return tr.estado === "borrador" || tr.estado === "confirmada"
    || (tr.estado === "entregada_cancelada" && tr.devolucion);
}

// ── Dibujo ────────────────────────────────────────────────────────────────────

function trazo(c: OffscreenCanvasRenderingContext2D, xa: number, xb: number, y: number,
               grosor: number, color: string): void {
  c.lineWidth = grosor;
  c.strokeStyle = color;
  c.beginPath();
  c.moveTo(xa, y);
  c.lineTo(xb, y);
  c.stroke();
}

function disco(c: OffscreenCanvasRenderingContext2D, x: number, y: number, r: number, color: string): void {
  c.fillStyle = color;
  c.beginPath();
  c.arc(x, y, r, 0, TAU);
  c.fill();
}

function anillo(c: OffscreenCanvasRenderingContext2D, x: number, y: number, r: number, color: string): void {
  c.lineWidth = 1.5;
  c.strokeStyle = color;
  c.beginPath();
  c.arc(x, y, r, 0, TAU);
  c.stroke();
}

/** Un glifo de radio 1 llevado a su sitio y a su tamaño. `save/restore` no asignan nada. */
function glifo(c: OffscreenCanvasRenderingContext2D, p: Path2D, x: number, y: number, r: number,
               color: string, grosor: number): void {
  c.save();
  c.translate(x, y);
  c.scale(r, r);
  c.lineWidth = grosor;
  c.strokeStyle = color;
  c.stroke(p);
  c.restore();
}

/**
 * Un cuadro de una traza. `mov = false` es el cuadro QUIETO: lo que se ve en un
 * estado terminal, con `calma`, y lo mismo que pinta el respaldo en SVG.
 *
 * Se limpia y se redibuja el lienzo entero: son una docena de trazos sobre
 * 150×22 px. Recortar sólo la zona que se mueve costaría más en código (y en
 * bordes mal suavizados) que lo que ahorra.
 */
function pintar(tr: TrazaW, t: number, mov: boolean): void {
  const b = base;
  const e = tr.estado;
  if (b === null || e === null || tr.w < 2) return;
  const c = tr.ctx;
  const g = b.geo[tr.modo];
  const col = b.colores;
  const ton = b.tonos;
  const y = tr.y;
  const llego = tr.llego;
  const xl = llego >= 2 ? tr.x2 : llego === 1 ? tr.x1 : tr.x0;
  const cancelada = e === "cancelada";
  const hecha = e === "entregada" || e === "entregada_cancelada";
  // Lo recorrido lleva el color del estado. DELIVERED but CANCELLED conserva el
  // riel esmeralda —se entregó, eso es un hecho— y lo ámbar es sólo el regreso.
  const color = e === "nueva" ? ton.borde : hecha ? col.entregada : col[e];
  const enCurso = tr.viva && (e === "borrador" || e === "confirmada");
  // Las dos novedades de la 0064 son de una CONFIRMADA y de nada más. No
  // dependen de `enCurso`: se pintan también en el cuadro quieto, y el tramo a
  // medias también en una borrada (lo que salió, salió). Lo que sí depende de
  // `enCurso` es el MOVIMIENTO. Para una borrada la alerta ni llega: eso lo
  // decide quien manda el estado (`planDe`, en Traza.tsx).
  const parcial = e === "confirmada" && tr.avance > 0;
  const alerta = e === "confirmada" && tr.alerta;
  // Con la alerta la estación no respira: lo que se mueve es su anillo ámbar.
  const s = enCurso && mov && !alerta ? 0.5 - 0.5 * Math.cos(((t % RESPIRA_MS) / RESPIRA_MS) * TAU) : 0;

  c.clearRect(0, 0, tr.w, tr.h);
  c.globalAlpha = 1;
  c.lineCap = "round";
  c.lineJoin = "round";

  // 1 · Los tramos. En CANCELADO el riel SE CORTA: después de la marca no hay vía.
  if (!cancelada && llego < 2) trazo(c, xl, tr.x2, y, g.grosor, ton.riel);
  if (llego >= 1) trazo(c, tr.x0, xl, y, g.grosor, color);
  const xm = cancelada ? (xl + (llego >= 1 ? tr.x2 : tr.x1)) / 2 : 0;
  if (cancelada) trazo(c, xl, xm, y, g.grosor, color);
  if (e === "entregada_cancelada") trazo(c, tr.x2, tr.xv, y, g.grosor, col.entregada_cancelada);
  // Entrega parcial: lo que ya salió es vía recorrida, hasta su cabeza.
  if (parcial) trazo(c, tr.x1, tr.xa, y, g.grosor, color);

  // 2 · El pulso que viaja por el tramo que sigue. Va ANTES de las estaciones:
  //     sale de debajo de la actual y se mete debajo de la siguiente. En una
  //     entrega parcial recorre sólo lo que FALTA (sale de la cabeza), y con la
  //     alerta del canal no viaja: la orden no va a DELIVERED hasta el «¿salió?».
  if (enCurso && mov && !alerta) {
    const xi = parcial ? tr.xa : xl;
    const xn = llego >= 1 ? tr.x2 : tr.x1;
    const f = (t % PULSO_MS) / PULSO_MS;
    const xp = xi + (xn - xi) * (f * f * (3 - 2 * f));
    const a = Math.sin(f * Math.PI);
    c.globalAlpha = 0.35 * a;
    trazo(c, Math.max(xi, xp - (xn - xi) * 0.2), xp, y, g.grosor, color);
    c.globalAlpha = 0.95 * a;
    disco(c, xp, y, g.grosor * 0.5 + 1.1, color);
    c.globalAlpha = 1;
  }
  // La cabeza del tramo a medias va ENCIMA del pulso: el pulso sale de ella.
  if (parcial) disco(c, tr.xa, y, g.rAvance, color);

  // 3 · La estación en curso respira (o lleva el halo fijo, en el cuadro quieto).
  //     Con la alerta del canal el anillo es ámbar y se abre DESPACIO, como el de
  //     la devolución pendiente: pide una respuesta, no corre.
  if (alerta) {
    if (mov && enCurso) {
      const f = (t % VUELTA_MS) / VUELTA_MS;
      c.globalAlpha = 0.45 * (1 - f);
      anillo(c, xl, y, g.r + 1.5 + f * g.aire * 1.1, ton.alerta);
    } else {
      c.globalAlpha = 0.3;
      anillo(c, xl, y, g.r + 2.5, ton.alerta);
    }
    c.globalAlpha = 1;
  } else if (enCurso) {
    c.globalAlpha = mov ? 0.14 + 0.26 * (1 - s) : 0.3;
    anillo(c, xl, y, mov ? g.r + 2 + s * g.aire : g.r + 2.5, color);
    c.globalAlpha = 1;
  }

  // 4 · Las tres estaciones: llenas las recorridas, huecas las pendientes.
  for (let i = 0; i < 3; i++) {
    const x = i === 0 ? tr.x0 : i === 1 ? tr.x1 : tr.x2;
    if (i <= llego) {
      disco(c, x, y, i === 2 && hecha ? g.rHecha : i === llego && enCurso ? g.r + 0.6 * s : g.r,
            alerta && i === llego ? ton.alerta : color);
      continue;
    }
    // Orden sin crear: la primera estación va en contorno punteado.
    const punteada = e === "nueva" && i === 0;
    disco(c, x, y, g.r - 0.75, ton.tinta);
    c.lineWidth = 1.5;
    c.strokeStyle = punteada ? ton.punteado : cancelada ? ton.riel : ton.borde;
    if (punteada) {
      c.lineCap = "butt";   // con punta redonda, los guiones de 2.5 px se cierran solos
      c.setLineDash(PUNTEADO);
    }
    c.stroke();
    if (punteada) {
      c.setLineDash(SOLIDO);
      c.lineCap = "round";
    }
  }

  // 5 · Las marcas.
  if (hecha && palomita) glifo(c, palomita, tr.x2, y, g.rHecha, ton.tinta, b.glifos.grosor);
  if (cancelada) {
    disco(c, xm, y, g.rMarca, color);
    if (equis) glifo(c, equis, xm, y, g.rMarca, ton.tinta, b.glifos.grosor);
  }
  if (e === "entregada_cancelada") {
    const ambar = col.entregada_cancelada;
    if (tr.devolucion) {
      if (mov && tr.viva) {
        const f = (t % VUELTA_MS) / VUELTA_MS;
        c.globalAlpha = 0.45 * (1 - f);
        anillo(c, tr.xv, y, g.rMarca + 1.5 + f * g.aire * 1.1, ambar);
      } else {
        c.globalAlpha = 0.3;
        anillo(c, tr.xv, y, g.rMarca + 2.5, ambar);
      }
      c.globalAlpha = 1;
    }
    disco(c, tr.xv, y, g.rMarca, ambar);
    if (vuelta) glifo(c, vuelta, tr.xv, y, g.rMarca, ton.tinta, b.glifos.grosor);
  }

  // El primer cuadro se avisa: hasta entonces la página deja puesto el respaldo
  // en SVG, para que no haya un parpadeo en blanco al montar.
  if (!tr.avisada) {
    tr.avisada = true;
    ambito.postMessage({ t: "lista", id: tr.id });
  }
}

/** El cuadro que toca AHORA: con movimiento si el bucle la va a seguir, quieto si no. */
function alDia(tr: TrazaW): void {
  const mov = tr.viva && tr.visible && !pausa && !calma;
  pintar(tr, mov ? performance.now() : 0, mov);
}

function quieta(tr: TrazaW): void {
  pintar(tr, 0, false);
}

// ── El bucle ──────────────────────────────────────────────────────────────────

function programar(): void {
  if (conRaf && ambito.requestAnimationFrame) manija = ambito.requestAnimationFrame(cuadro);
  else reloj = setTimeout(alReloj, CUADRO_MS);
}

function alReloj(): void {
  reloj = null;
  cuadro(performance.now());
}

function cuadro(t: number): void {
  if (!corriendo) return;
  cuadros++;
  if (animadas.length === 0 || pausa || calma) {
    corriendo = false;
    return;
  }
  // El rAF dispara a la tasa del monitor (60, 120, 144 Hz): se deja pasar lo que
  // no toca para quedarse en ~30 cuadros.
  if (t - ultimo >= CUADRO_MS - 4) {
    ultimo = t;
    for (let i = 0; i < animadas.length; i++) pintar(animadas[i], t, true);
  }
  programar();
}

/**
 * Lección de la Red viva: hay entornos (paneles embebidos, ventanas tapadas)
 * donde el rAF no dispara NUNCA. Si medio segundo después de arrancar no llegó
 * ni un cuadro, el bucle se pasa al reloj y ahí se queda.
 */
function vigilar(): void {
  vigia = null;
  if (!corriendo || !conRaf || cuadros > 0) return;
  ambito.cancelAnimationFrame?.(manija);
  conRaf = false;
  programar();
}

function arrancar(): void {
  if (corriendo || pausa || calma || animadas.length === 0) return;
  corriendo = true;
  ultimo = 0;
  cuadros = 0;
  programar();
  if (conRaf && vigia === null) vigia = setTimeout(vigilar, VIGIA_MS);
}

function detener(): void {
  if (vigia !== null) {
    clearTimeout(vigia);
    vigia = null;
  }
  if (!corriendo) return;
  corriendo = false;
  if (reloj !== null) {
    clearTimeout(reloj);
    reloj = null;
  }
  ambito.cancelAnimationFrame?.(manija);
}

function apuntar(tr: TrazaW): void {
  if (tr.viva && tr.visible) animadas.push(tr);
}

/** Rehace la lista de lo que se anima y deja el bucle como corresponde. */
function rehacer(): void {
  animadas.length = 0;
  trazas.forEach(apuntar);
  if (animadas.length === 0) detener();
  else arrancar();
}

function resituar(tr: TrazaW): void {
  situar(tr);
  alDia(tr);
}

function aquietar(tr: TrazaW): void {
  if (tr.viva) quieta(tr);
}

// ── Avisos de la página ───────────────────────────────────────────────────────

ambito.onmessage = (ev: MessageEvent) => {
  const m = ev.data as MensajeTraza;
  switch (m.t) {
    case "base": {
      base = m.base;
      try {
        palomita = new Path2D(m.base.glifos.palomita);
        equis = new Path2D(m.base.glifos.equis);
        vuelta = new Path2D(m.base.glifos.vuelta);
      } catch {
        // Sin Path2D no hay palomita ni ✕: mejor el respaldo en SVG que un riel a medias.
        ambito.postMessage({ t: "sin_lienzo" });
        return;
      }
      trazas.forEach(resituar);
      return;
    }
    case "alta": {
      let ctx: OffscreenCanvasRenderingContext2D | null = null;
      try {
        ctx = m.canvas.getContext("2d");
      } catch {
        ctx = null;
      }
      if (ctx === null) {
        ambito.postMessage({ t: "sin_lienzo" });
        return;
      }
      const tr: TrazaW = {
        id: m.id, lienzo: m.canvas, ctx, modo: m.modo, w: 0, h: 0,
        estado: null, llego: -1, devolucion: false, borrada: false, avance: 0, alerta: false,
        visible: false, viva: false, avisada: false,
        x0: 0, x1: 0, x2: 0, xv: 0, xa: 0, y: 0,
      };
      ajustar(tr, m.w, m.h, m.dpr);
      trazas.set(m.id, tr);
      return;
    }
    case "estado": {
      const tr = trazas.get(m.id);
      if (!tr) return;
      tr.estado = m.estado;
      tr.llego = m.llego;
      tr.devolucion = m.devolucion;
      tr.borrada = m.borrada;
      // Lo que llegue fuera de 0..1 (o que no sea un número) se acota aquí, una
      // vez: el dibujo no vuelve a preguntar.
      tr.avance = m.avance > 0 ? Math.min(1, m.avance) : 0;
      tr.alerta = m.alerta === true;
      tr.viva = esViva(tr);
      // La cabeza del tramo a medias es una posición en píxeles: se recalcula
      // aquí (cambió el avance) y al medir, nunca en cada cuadro.
      situar(tr);
      alDia(tr);
      rehacer();
      return;
    }
    case "medida": {
      const tr = trazas.get(m.id);
      if (!tr) return;
      ajustar(tr, m.w, m.h, m.dpr);
      alDia(tr);
      return;
    }
    case "visible": {
      const tr = trazas.get(m.id);
      if (!tr || tr.visible === m.v) return;
      tr.visible = m.v;
      rehacer();
      return;
    }
    case "pausa": {
      pausa = m.v;
      if (pausa) detener();
      else arrancar();
      return;
    }
    case "calma": {
      calma = m.v;
      if (calma) {
        detener();
        // Que ningún pulso se quede congelado a medio tramo.
        trazas.forEach(aquietar);
      } else {
        arrancar();
      }
      return;
    }
    case "baja": {
      if (trazas.delete(m.id)) rehacer();
      return;
    }
  }
};

export {};
