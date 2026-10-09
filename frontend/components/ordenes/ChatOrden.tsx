"use client";

/**
 * ChatOrden — los movimientos y mensajes de una orden de venta, en vivo.
 *
 * Pedido de Brandon (2-oct-2026): «como en Odoo, un entorno de chat con mensajes
 * persistentes; se cargan cada vez que se selecciona la orden; tendrá los
 * mensajes de cuándo se creó, cuándo se confirmó y con qué piezas y qué SKU, de
 * manera resumida y clara; guarda quién mandó el mensaje; usando async para que
 * en vivo se vean los mensajes de cada usuario».
 *
 * Dos clases de renglón, del mismo `ventas.ov_mensajes` (que sólo se agrega):
 *   · SISTEMA: la bitácora. La escribe la misma sentencia de cada transición
 *     (creada, confirmada con lo que apartó cada SKU, entregada, cancelada…),
 *     así que no puede faltar ni contradecir a la orden.
 *   · USUARIO: lo que escribe una persona. Burbujas, las propias a la derecha.
 *
 * EL CATÁLOGO DE EVENTOS ES CERRADO (CHECK `ov_mensajes_evento_chk` de la 0064;
 * `EventoOrden` en tipos.ts) y `EVENTOS` lo cubre entero: TypeScript no deja
 * compilar si a la base se le agrega un evento y aquí falta su icono. Tres cosas
 * que cambiaron con el modelo del 6-oct-2026 y que este chat tiene que decir:
 *   · Se aparta TODO O NADA. Si no alcanzó, la orden NO se confirmó: el evento
 *     `no_alcanzo` va en ámbar (alguien tiene que hacer algo), no como un paso más.
 *   · Se entrega renglón por renglón, con 0..cantidad piezas: la mini-tabla dice
 *     «salieron N de M», no «N entregadas».
 *   · `canal_cancelo` es una PREGUNTA a Bodega («¿salió?»), también en ámbar.
 * Un mensaje del sistema SIN evento (hoy, el aviso de un PDF adjuntado o
 * quitado) es un aviso neutro: no es una transición de la orden.
 *
 * POR QUÉ LONG-POLL y no EventSource / WebSocket: ninguno de los dos puede
 * mandar la cabecera `Authorization`, y todo lo que no pasa por `fetchSesion`
 * revienta con 401 desde que se encendió el enforcement. Aquí se pide
 * `GET …/mensajes?desde_id=N&esperar=25` y el backend SOSTIENE la petición
 * hasta que hay algo nuevo: el mensaje de otra persona aparece al instante sin
 * preguntar cada segundo. Al volver —con o sin novedades— se vuelve a pedir.
 *
 * LA BASE ES LA VERDAD; el aviso del backend sólo acorta la espera. Por eso:
 *   · se deduplica por `id` (lo que mandé yo llega también por el long-poll);
 *   · si `total` no cuadra con lo que tengo, se recarga TODO desde cero (un id
 *     menor pudo confirmarse después del que ya vi);
 *   · si `rev` avanzó, alguien movió la orden: se le avisa al documento
 *     (`onCambio`) para que la relea, y se le SIGUE avisando en cada respuesta
 *     mientras no la alcance. Antes se avisaba UNA vez por `rev`: si esa única
 *     relectura llegaba vieja o fallaba, el documento se quedaba atrás para
 *     siempre (borrador en pantalla, confirmada en la base). Quien deduplica
 *     es el documento, que sabe qué `rev` tiene y si ya está releyendo.
 *
 * CADA MENSAJE ESCRITO VIAJA CON SU CLAVE (uuid). Si el servidor lo guardó
 * pero la respuesta se perdió (un relevo de contenedor en pleno deploy), la
 * pantalla dice «inténtalo de nuevo»; con la clave, ese segundo envío del
 * MISMO texto no deja el mensaje dos veces en una bitácora que no se borra.
 *
 * Un solo bucle vivo a la vez (`turno`): StrictMode monta dos veces y cambiar
 * de orden no debe dejar al bucle anterior pintando en la nueva. Con la pestaña
 * oculta la petición sostenida se aborta —el navegador no se queda esperando
 * algo que nadie mira— y al volver se pregunta primero SIN esperar, para
 * ponerse al día de golpe.
 */

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowDown, Ban, CheckCheck, CircleCheck, CircleHelp, FilePlus2, FileX, Info, Loader2, PackageCheck,
  PackageMinus, PackageOpen, PackagePlus, PackageX, Paperclip, Pencil, Send, Trash2, Truck, Undo2,
} from "lucide-react";
import { ApiError, mensajeDeError } from "@/lib/api";
import { enviarMensaje, leerMensajes } from "./api";
import type { EstadoOrden, EventoOrden, Mensaje, RespMensajes } from "./tipos";
import {
  Boton, CLASE_CAMPO, ROTULO_VIA, claveDeIntento, dinero, fechaHora, fechaLarga, num, quien,
  rotuloCanal, type ClaveIntento,
} from "./ui";

const ESPERA_S = 25;          // lo que el backend sostiene la petición (su tope)
const REINTENTO_MS = 3_000;   // primera espera tras un error…
const TOPE_MS = 15_000;       // …que crece hasta aquí
const RESPIRO_MS = 1_500;     // pausa si una petición «sostenida» vuelve vacía al instante
const MAX_CUERPO = 4000;
const CERCA_DEL_FONDO = 40;   // px: a esta distancia del final se considera «al fondo»
const MAX_RENGLONES = 12;
const SIN_FRESCOS: ReadonlySet<number> = new Set<number>();
const SIN_MENSAJES: Mensaje[] = [];

/** Los mensajes que se pintan, y de QUÉ orden son (para no enseñar los de la anterior). */
interface Hilo {
  orden: number | null;
  mensajes: Mensaje[];
  cargado: boolean;
}
const HILO_VACIO: Hilo = { orden: null, mensajes: SIN_MENSAJES, cargado: false };

/** Mezcla una respuesta con lo que ya hay. Dice cuántos eran nuevos y si `total` cuadra. */
type Absorber = (r: RespMensajes, todo: boolean, propio: boolean) => { nuevos: number; cuadra: boolean };

export function ChatOrden({ ordenId, rev, puedeEscribir, porque, yo, onCambio }: {
  ordenId: number | null;
  rev: number;
  puedeEscribir: boolean;
  porque?: string;
  yo: string;
  onCambio: (c: { rev: number; estado: EstadoOrden }) => void;
}): JSX.Element {
  const [hilo, setHilo] = useState<Hilo>(HILO_VACIO);
  const [conectado, setConectado] = useState(false);
  const [falla, setFalla] = useState<string | null>(null);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [fallaEnvio, setFallaEnvio] = useState<string | null>(null);
  /** Llegaron mientras se leía más arriba: se anuncian, no se arrastra la vista. */
  const [nuevos, setNuevos] = useState(0);
  const [frescos, setFrescos] = useState<ReadonlySet<number>>(SIN_FRESCOS);
  /** Sube con cada mensaje que mando: lo mío siempre me lleva al fondo. */
  const [salto, setSalto] = useState(0);
  /** «Hoy» y «Ayer» en CDMX. Se calculan tras montar: la página se pre-renderiza. */
  const [dias, setDias] = useState<{ hoy: string; ayer: string } | null>(null);

  const lista = useRef<HTMLDivElement>(null);
  const campo = useRef<HTMLTextAreaElement>(null);
  const alFondo = useRef(true);
  /** Mandé un mensaje: el siguiente repintado baja al fondo, esté donde esté. */
  const bajar = useRef(false);
  const porId = useRef(new Map<number, Mensaje>());
  const ultimoId = useRef(0);
  /** El candado: cada bucle recuerda su turno y se retira cuando ya no es el suyo. */
  const turno = useRef(0);
  const absorber = useRef<Absorber | null>(null);
  /** La clave del mensaje que se está mandando, atada a su TEXTO (ver `enviar`). */
  const claveEnvio = useRef<ClaveIntento | null>(null);
  // Entran por ref para que el bucle no se reinicie cada vez que el documento se repinta.
  const revActual = useRef(rev);
  revActual.current = rev;
  const avisar = useRef(onCambio);
  avisar.current = onCambio;
  const yoActual = useRef(yo);
  yoActual.current = yo;

  useEffect(() => {
    porId.current = new Map();
    ultimoId.current = 0;
    alFondo.current = true;
    bajar.current = false;
    setConectado(false);
    setFalla(null);
    setNuevos(0);
    setFrescos(SIN_FRESCOS);
    // El borrador de un mensaje es de UNA orden: no se arrastra a la siguiente.
    setTexto("");
    setFallaEnvio(null);
    claveEnvio.current = null;
    if (ordenId === null) return;

    const mio = ++turno.current;
    const sigo = () => turno.current === mio;
    let ctl: AbortController | null = null;
    let reloj: ReturnType<typeof setTimeout> | null = null;
    let despertar: (() => void) | null = null;
    let enCurso = false;
    let sostenidaEnVuelo = false;   // la petición en vuelo es de las que el backend sostiene
    let cargado = false;   // ya tengo el historial completo
    let desfase = 0;       // si `total` cuenta distinto que yo tras recargar todo, no insisto

    const dormir = (ms: number) => new Promise<void>((seguir) => {
      despertar = seguir;
      reloj = setTimeout(seguir, ms);
    });

    const absorberAqui: Absorber = (r, todo, propio) => {
      if (!sigo()) return { nuevos: 0, cuadra: true };
      // ¿Estaba al fondo? Se MIDE aquí, antes de pintar lo nuevo, y no se confía
      // sólo en el último evento de scroll: el navegador los entrega con el
      // siguiente cuadro, y un mensaje puede llegar antes que ese cuadro.
      const el = lista.current;
      if (el) alFondo.current = el.scrollHeight - el.scrollTop - el.clientHeight < CERCA_DEL_FONDO;
      const mapa = porId.current;
      if (todo) mapa.clear();
      const llegaron: Mensaje[] = [];
      for (const m of r.mensajes) {
        if (!mapa.has(m.id)) llegaron.push(m);
        mapa.set(m.id, m);
        // Lo que devuelve MI envío no mueve el cursor: lo trae también el
        // long-poll, junto con lo que otro haya escrito justo antes.
        if (!propio && m.id > ultimoId.current) ultimoId.current = m.id;
      }
      if (!propio && r.ultimo_id > ultimoId.current) ultimoId.current = r.ultimo_id;

      if (todo || llegaron.length) {
        const orden = Array.from(mapa.values()).sort((a, b) => a.id - b.id);
        setHilo({ orden: ordenId, mensajes: orden, cargado: true });
      }
      if (!todo && llegaron.length) {
        setFrescos((antes) => new Set([...antes, ...llegaron.map((m) => m.id)]));
        // Lo que escribí yo no se me anuncia como «nuevo» (puede llegar antes
        // por el long-poll que por la respuesta de mi propio envío).
        const yoMin = yoActual.current.trim().toLowerCase();
        const ajenos = propio ? 0 : llegaron.filter((m) =>
          !(m.tipo === "usuario" && !!yoMin && m.autor.trim().toLowerCase() === yoMin)).length;
        if (ajenos && !alFondo.current) setNuevos((n) => n + ajenos);
      }

      // Alguien movió la orden. `rev` sólo crece: una respuesta que salió antes
      // de un cambio que el documento ya conoce no lo hace recargar. Se avisa
      // SIEMPRE que el documento vaya atrás, no una vez por `rev`: si su
      // relectura se quedó corta o falló, la siguiente respuesta lo vuelve a
      // empujar. No hay tormenta: el documento ignora lo que ya sabe, comparte
      // la lectura en vuelo y no relee con una acción suya en curso.
      if (r.rev > revActual.current) avisar.current({ rev: r.rev, estado: r.estado });

      if (todo) {
        desfase = r.total - mapa.size;
        return { nuevos: llegaron.length, cuadra: true };
      }
      return { nuevos: llegaron.length, cuadra: r.total - desfase === mapa.size };
    };
    absorber.current = absorberAqui;

    const bucle = async () => {
      if (enCurso || !sigo()) return;
      enCurso = true;
      let espera = REINTENTO_MS;
      // La primera petición de cada arranque contesta al instante: al montar es
      // el historial; al volver a la pestaña, lo que pasó mientras no miraba.
      let sinEsperar = true;
      try {
        while (sigo() && document.visibilityState === "visible") {
          ctl = new AbortController();
          const senal = ctl.signal;
          const todo = !cargado;
          const sostenida = !todo && !sinEsperar;
          const t0 = Date.now();
          sostenidaEnVuelo = sostenida;
          try {
            const r = await leerMensajes(ordenId, todo ? 0 : ultimoId.current,
                                         sostenida ? ESPERA_S : 0, senal);
            if (!sigo() || senal.aborted) return;
            const { nuevos: trajo, cuadra } = absorberAqui(r, todo, false);
            // Si no cuadra, la siguiente vuelta recarga todo desde cero.
            cargado = todo ? true : cuadra;
            sinEsperar = false;
            espera = REINTENTO_MS;
            setConectado(true);
            setFalla(null);
            // Una petición «sostenida» que vuelve al instante y sin nada que yo
            // no tuviera es un backend que no está sosteniendo: sin este
            // respiro, el bucle le pegaría sin parar. (No se pierde nada: lo
            // que llegue mientras tanto ya está en la base.)
            if (sostenida && cuadra && trajo === 0 && Date.now() - t0 < RESPIRO_MS) {
              await dormir(RESPIRO_MS);
            }
          } catch (e) {
            if (!sigo() || senal.aborted) return;
            setConectado(false);
            if (!cargado) setFalla(mensajeDeError(e, "No se pudieron leer los movimientos de la orden."));
            // Sin permiso o sin orden: insistir no lo arregla.
            if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return;
            await dormir(espera);
            espera = Math.min(TOPE_MS, Math.round(espera * 1.7));
            sinEsperar = true;
          }
        }
      } finally {
        enCurso = false;
      }
    };

    const alCambiarPestana = () => {
      if (document.visibilityState === "visible") {
        if (enCurso) despertar?.();
        else void bucle();
      } else {
        // Sólo se corta la petición SOSTENIDA: nadie mira, y no tiene caso ocupar
        // una conexión 25 s. Una petición corta (el historial, la puesta al día)
        // se deja terminar: contesta en milisegundos, y cortarla dejaría el chat
        // en blanco en un navegador que parpadea entre visible y oculto.
        if (sostenidaEnVuelo) ctl?.abort();
        despertar?.();
      }
    };
    document.addEventListener("visibilitychange", alCambiarPestana);
    void bucle();

    return () => {
      turno.current++;
      absorber.current = null;
      document.removeEventListener("visibilitychange", alCambiarPestana);
      ctl?.abort();
      if (reloj !== null) clearTimeout(reloj);
      despertar?.();
    };
  }, [ordenId]);

  const actual = hilo.orden === ordenId && ordenId !== null ? hilo : HILO_VACIO;
  const mensajes = actual.mensajes;
  const cuantos = mensajes.length;
  const ultimo = cuantos ? mensajes[cuantos - 1].id : 0;

  // Lo nuevo queda a la vista SÓLO si ya se estaba al fondo (o si lo mandé yo).
  // `bajar` va aparte de `alFondo` a propósito: mi mensaje puede llegar también
  // por el long-poll, y esa segunda respuesta vuelve a MEDIR —todavía sin el
  // mensaje pintado— y diría «no estaba al fondo».
  useEffect(() => {
    const el = lista.current;
    if (!el || !(alFondo.current || bajar.current)) return;
    bajar.current = false;
    alFondo.current = true;
    el.scrollTop = el.scrollHeight;
  }, [cuantos, ultimo, salto]);

  // «Hoy» / «Ayer»: se recalculan con cada mensaje, por si la pestaña cruzó la medianoche.
  useEffect(() => {
    const ahora = Date.now();
    const hoy = fechaLarga(new Date(ahora).toISOString());
    const ayer = fechaLarga(new Date(ahora - 86_400_000).toISOString());
    setDias((d) => (d && d.hoy === hoy ? d : { hoy, ayer }));
  }, [cuantos]);

  // Lo recién llegado se resalta unos segundos, no hasta que llegue lo siguiente.
  useEffect(() => {
    if (!frescos.size) return;
    const t = setTimeout(() => setFrescos(SIN_FRESCOS), 2_600);
    return () => clearTimeout(t);
  }, [frescos]);

  const alDesplazar = () => {
    const el = lista.current;
    if (!el) return;
    const fondo = el.scrollHeight - el.scrollTop - el.clientHeight < CERCA_DEL_FONDO;
    alFondo.current = fondo;
    if (fondo) setNuevos((n) => (n ? 0 : n));
  };

  const irAlFondo = () => {
    const el = lista.current;
    if (!el) return;
    alFondo.current = true;
    setNuevos(0);
    // De golpe y no «suave»: a media animación un mensaje nuevo mediría «no está
    // al fondo» y se quedaría fuera de la vista.
    el.scrollTop = el.scrollHeight;
  };

  const enviar = async () => {
    const cuerpo = texto.trim();
    if (!cuerpo || enviando || ordenId === null || !puedeEscribir) return;
    const t = turno.current;
    // La clave se genera AQUÍ, al mandar (no al pintar: la página se pre-renderiza),
    // y va atada al TEXTO EXACTO. Reintentar lo mismo tras un fallo reusa la
    // clave —si el primer envío sí se guardó, el servidor contesta ese mensaje
    // y no lo duplica—; si la persona corrige el texto, es otro mensaje y lleva
    // otra clave (con la vieja, el servidor devolvería el original y la
    // corrección se perdería callada).
    const intento = claveDeIntento(claveEnvio.current, cuerpo);
    claveEnvio.current = intento;
    setEnviando(true);
    setFallaEnvio(null);
    try {
      const r = await enviarMensaje(ordenId, cuerpo, intento.clave);
      // Cambió de orden mientras viajaba: el mensaje quedó en la suya, aquí no se pinta.
      if (turno.current !== t) return;
      // Entregado: el siguiente mensaje, aunque diga lo mismo, es otro.
      claveEnvio.current = null;
      setTexto("");
      absorber.current?.(r, false, true);
      bajar.current = true;
      setNuevos(0);
      setSalto((n) => n + 1);
    } catch (e) {
      if (turno.current === t) {
        setFallaEnvio(mensajeDeError(e, "No se pudo enviar el mensaje. Tu texto sigue aquí: inténtalo de nuevo."));
      }
    } finally {
      setEnviando(false);
      campo.current?.focus();
    }
  };

  const alTeclear = (ev: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter envía; Shift+Enter es salto de línea. Mientras se compone un acento
    // o un carácter con IME, Enter es del teclado y no nuestro.
    if (ev.key !== "Enter" || ev.shiftKey || ev.nativeEvent.isComposing) return;
    ev.preventDefault();
    void enviar();
  };

  // ── Los renglones, con un separador por día ─────────────────────────────────
  const renglones: ReactNode[] = [];
  let diaPrevio: string | null = null;
  const yoMin = yo.trim().toLowerCase();
  for (const m of mensajes) {
    const dia = fechaLarga(m.creado_at, "Sin fecha");
    if (dia !== diaPrevio) {
      diaPrevio = dia;
      renglones.push(
        <SeparadorDia key={`dia-${m.id}`}
                      texto={dias && dia === dias.hoy ? "Hoy" : dias && dia === dias.ayer ? "Ayer" : dia} />);
    }
    const fresco = frescos.has(m.id);
    renglones.push(m.tipo === "sistema"
      ? <RenglonSistema key={m.id} m={m} fresco={fresco} />
      : <Burbuja key={m.id} m={m} fresco={fresco}
                 mia={!!yoMin && m.autor.trim().toLowerCase() === yoMin} />);
  }

  const estadoVivo = ordenId === null ? null
    : conectado ? { punto: "bg-emerald-500", texto: "en vivo", tono: "text-emerald-700",
                    titulo: "Lo que escriban los demás aparece aquí al instante." }
    : actual.cargado ? { punto: "bg-slate-300", texto: "reconectando…", tono: "text-slate-400",
                         titulo: "Se perdió la conexión; se reintenta sola." }
    : { punto: "bg-slate-300", texto: "conectando…", tono: "text-slate-400", titulo: "Leyendo los movimientos." };

  return (
    <section aria-label="Movimientos y mensajes de la orden"
             className="flex h-full min-h-0 flex-col rounded-2xl border border-slate-200 bg-white">
      <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <h3 className="text-sm font-extrabold tracking-tight text-slate-900">Movimientos y mensajes</h3>
        {estadoVivo && (
          <span title={estadoVivo.titulo} role="status"
                className={`inline-flex items-center gap-1.5 text-[11px] font-semibold ${estadoVivo.tono}`}>
            <span className={`h-2 w-2 rounded-full ${estadoVivo.punto}`} />
            {estadoVivo.texto}
          </span>
        )}
      </header>

      {ordenId === null ? (
        <p className="m-4 rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-[13px] leading-relaxed text-slate-400">
          Los movimientos y mensajes de la orden aparecerán aquí cuando se cree.
        </p>
      ) : (
        <>
          {/* El alto lo pone quien lo monta (el documento le da el de la ventana).
              `flex-basis: 0px` y no `flex-1`: con un padre SIN alto, un 0% se
              vuelve «lo que mida el contenido» y la lista crecería sin fin en
              vez de desplazarse; así se queda en su mínimo y se desplaza. */}
          <div className="relative flex min-h-[260px] flex-[1_1_0px] flex-col">
            <div ref={lista} onScroll={alDesplazar} role="log" aria-live="polite"
                 className="flex min-h-0 flex-[1_1_0px] flex-col gap-2.5 overflow-y-auto px-4 py-3 [&>*]:shrink-0">
              {!actual.cargado && !falla && (
                <p className="m-auto inline-flex items-center gap-2 text-[12.5px] text-slate-400">
                  <Loader2 className="h-4 w-4 animate-spin" /> Cargando los movimientos…
                </p>
              )}
              {!actual.cargado && falla && (
                <p className="m-auto max-w-[85%] rounded-xl bg-rose-50 px-3 py-2 text-center text-[12.5px] text-rose-700 ring-1 ring-rose-200">
                  {falla}
                </p>
              )}
              {actual.cargado && cuantos === 0 && (
                <p className="m-auto text-[12.5px] text-slate-400">Todavía no hay movimientos.</p>
              )}
              {renglones}
            </div>
            {nuevos > 0 && (
              <button type="button" onClick={irAlFondo}
                      className="absolute bottom-2 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full bg-indigo-600 px-3 py-1 text-[11.5px] font-bold text-white shadow-card-hover transition hover:bg-indigo-700">
                {num(nuevos)} {nuevos === 1 ? "nuevo" : "nuevos"} <ArrowDown className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          <div className="border-t border-slate-100 p-3">
            {puedeEscribir ? (
              <>
                <div className="flex items-end gap-2">
                  <textarea ref={campo} value={texto} rows={2} maxLength={MAX_CUERPO} readOnly={enviando}
                            onChange={(ev) => setTexto(ev.target.value)} onKeyDown={alTeclear}
                            aria-label="Mensaje para la orden" placeholder="Escribe un mensaje…"
                            className={`${CLASE_CAMPO} max-h-40 min-h-[44px] resize-none`} />
                  <Boton icono={Send} tono="primario" onClick={() => void enviar()} ocupado={enviando}
                         deshabilitado={!texto.trim()} porque="Escribe un mensaje">
                    Enviar
                  </Boton>
                </div>
                <p className={`mt-1.5 text-[11px] ${fallaEnvio ? "text-rose-600" : "text-slate-400"}`}>
                  {fallaEnvio ?? "Enter envía · Shift+Enter hace un salto de línea"}
                  {!fallaEnvio && texto.length > MAX_CUERPO - 400 ? ` · ${num(texto.length)} de ${num(MAX_CUERPO)}` : ""}
                </p>
              </>
            ) : (
              <textarea disabled rows={2} aria-label="Mensaje para la orden" title={porque}
                        placeholder={porque || "No puedes escribir en esta orden."}
                        className={`${CLASE_CAMPO} resize-none`} />
            )}
          </div>
        </>
      )}
    </section>
  );
}

// ── Piezas ────────────────────────────────────────────────────────────────────

function SeparadorDia({ texto }: { texto: string }) {
  return (
    <div className="flex items-center gap-3 py-1" role="separator">
      <span className="h-px flex-1 bg-slate-100" />
      <span className="text-[10.5px] font-bold uppercase tracking-[0.06em] text-slate-400">{texto}</span>
      <span className="h-px flex-1 bg-slate-100" />
    </div>
  );
}

/** «12:09»: el día ya lo dice el separador. */
function hora(iso: string): string {
  const f = fechaHora(iso, "");
  return f ? f.slice(-5) : "—";
}

/**
 * Quién firma. Si entró por el panel, la persona. Si no, la vía; y cuando el
 * nombre ya ES la vía («Automático», «Claude», «API») no se dice dos veces.
 */
export function firma(m: Mensaje): string {
  const nombre = quien(m.autor_nombre, m.autor);
  if (m.via === "panel") return nombre;
  const via = ROTULO_VIA[m.via] ?? m.via;
  const n = nombre.toLowerCase();
  if (nombre === "—" || via.toLowerCase().includes(n)) {
    return m.via === "automatico" ? "Automático" : via;
  }
  return `${nombre} · ${via}`;
}

function Burbuja({ m, mia, fresco }: { m: Mensaje; mia: boolean; fresco: boolean }) {
  return (
    <div className={`flex flex-col rounded-xl ${mia ? "items-end" : "items-start"} ${
      fresco && !mia ? "animate-resalta" : ""}`}>
      <span className="mb-0.5 px-1 text-[10.5px] text-slate-400">
        <b className="font-semibold text-slate-500">{mia ? "Tú" : firma(m)}</b> · {hora(m.creado_at)}
      </span>
      <div className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
        mia ? "rounded-br-sm bg-indigo-600 text-white"
            : "rounded-bl-sm border border-slate-200 bg-white text-slate-800"}`}>
        {m.cuerpo}
      </div>
    </div>
  );
}

const TONO_SLATE = "bg-slate-100 text-slate-500 ring-slate-200";
const TONO_INDIGO = "bg-indigo-50 text-indigo-600 ring-indigo-200";
const TONO_ESMERALDA = "bg-emerald-50 text-emerald-600 ring-emerald-200";
const TONO_AMBAR = "bg-amber-50 text-amber-700 ring-amber-200";
const TONO_ROSA = "bg-rose-50 text-rose-600 ring-rose-200";

export interface Evento {
  icono: LucideIcon;
  tono: string;
  rotulo: string;
  /** Le pide algo a una persona: el renglón ENTERO va en ámbar, no sólo su icono. */
  llama?: boolean;
}

/**
 * Cada evento de la bitácora: su icono, su tono y cómo se llama. El `rotulo`
 * no se pinta junto al texto (el `cuerpo` ya lo dice): es el nombre accesible
 * del icono, para quien no lo ve.
 *
 * Es `Record<EventoOrden, …>` a propósito: el catálogo es cerrado en la base y
 * aquí tiene que estar completo. Los tonos son los del panel: slate = papeleo,
 * índigo = la orden avanza, esmeralda = hecho, ámbar = pide atención, rosa = se
 * acabó.
 */
export const EVENTOS: Record<EventoOrden, Evento> = {
  creada: { icono: FilePlus2, tono: TONO_SLATE, rotulo: "Creada" },
  borrador_guardado: { icono: Pencil, tono: TONO_SLATE, rotulo: "Borrador guardado" },
  // Está en el catálogo de la 0064, pero la guía todavía no dice qué transición
  // lo escribe (§5.3, punto 23): se pinta neutro, sin inventarle un sentido.
  descartada: { icono: FileX, tono: TONO_SLATE, rotulo: "Descartada" },
  confirmada: { icono: CircleCheck, tono: TONO_INDIGO, rotulo: "Confirmada: stock apartado" },
  // No es un paso de la orden: es el intento que NO pasó. Sigue en borrador.
  no_alcanzo: { icono: PackageX, tono: TONO_AMBAR, rotulo: "No alcanzó el stock", llama: true },
  entregada_parcial: { icono: PackageCheck, tono: TONO_INDIGO, rotulo: "Entrega parcial" },
  entregada: { icono: Truck, tono: TONO_ESMERALDA, rotulo: "Entregada" },
  cancelada: { icono: Ban, tono: TONO_ROSA, rotulo: "Cancelada" },
  borrada_admin: { icono: Trash2, tono: TONO_ROSA, rotulo: "Borrada por un administrador" },
  canal_cancelo: { icono: CircleHelp, tono: TONO_AMBAR, rotulo: "El canal canceló: ¿salió?", llama: true },
  devolucion_esperada: { icono: Undo2, tono: TONO_AMBAR, rotulo: "Se espera la devolución" },
  devolucion_recibida: { icono: PackageOpen, tono: TONO_AMBAR, rotulo: "Devolución recibida" },
  devolucion_aprobada: { icono: PackagePlus, tono: TONO_ESMERALDA, rotulo: "Devolución aprobada: vuelve al stock" },
  devolucion_merma: { icono: PackageMinus, tono: TONO_ROSA, rotulo: "Devolución a merma" },
  devolucion_cerrada: { icono: CheckCheck, tono: TONO_SLATE, rotulo: "Devolución cerrada" },
};

/** Un mensaje del sistema SIN evento: un aviso, no una transición de la orden. */
const AVISO_NEUTRO: Evento = { icono: Info, tono: TONO_SLATE, rotulo: "Aviso" };
/** El aviso de hoy: se adjuntó o se quitó un PDF. Se reconoce por sus `datos`. */
const AVISO_PDF: Evento = { icono: Paperclip, tono: TONO_SLATE, rotulo: "Aviso de un PDF" };
/** Un evento que esta pantalla no conoce (una base más nueva que el código): no se esconde. */
const EVENTO_OTRO: Evento = { icono: Info, tono: TONO_SLATE, rotulo: "Movimiento" };

/**
 * ¿Los `datos` hablan de un PDF? El servicio deja dos formas (subir_archivo y
 * borrar_archivo de `services/ordenes_venta.py`): al adjuntar,
 * {nombre, tipo, bytes, sha256}; al quitar, {archivo_id, nombre}.
 */
function esDePdf(datos: Record<string, unknown> | null): boolean {
  return !!datos && ("sha256" in datos || Object.keys(datos).some((k) => k.startsWith("archivo")));
}

/** Con qué se pinta un mensaje del sistema: su evento, o un aviso neutro si no trae. */
export function eventoDe(m: Pick<Mensaje, "evento" | "datos">): Evento {
  if (m.evento) return (EVENTOS as Record<string, Evento | undefined>)[m.evento] ?? EVENTO_OTRO;
  return esDePdf(m.datos) ? AVISO_PDF : AVISO_NEUTRO;
}

function RenglonSistema({ m, fresco }: { m: Mensaje; fresco: boolean }) {
  const ev = eventoDe(m);
  const Icono = ev.icono;
  const lineas = lineasDe(m.datos);
  const cambios = cambiosDe(m.datos);
  // Un aviso pesa menos que una transición; lo que pide una respuesta, más.
  const letra = !m.evento ? "font-medium text-slate-600"
    : ev.llama ? "font-semibold text-amber-900" : "font-semibold text-slate-800";
  return (
    <div className={`mx-auto w-full max-w-[94%] rounded-xl px-3 py-2 ${
      ev.llama ? "bg-amber-50 ring-1 ring-amber-200" : "bg-slate-50/70"} ${fresco ? "animate-resalta" : ""}`}>
      <div className="flex items-start gap-2.5">
        <span role="img" aria-label={ev.rotulo} title={ev.rotulo}
              className={`mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full ring-1 ${ev.tono}`}>
          <Icono className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className={`whitespace-pre-wrap break-words text-[12.5px] leading-snug ${letra}`}>{m.cuerpo}</p>
          <p className={`mt-0.5 text-[11px] ${ev.llama ? "text-amber-800/70" : "text-slate-400"}`}>
            {firma(m)} · <span className="font-mono tabular-nums">{fechaHora(m.creado_at)}</span>
          </p>
          {lineas.length > 0 && (
            <ul className="mt-1.5 divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 bg-white">
              {lineas.slice(0, MAX_RENGLONES).map((l, i) => {
                const d = detalleLinea(m.evento, l);
                return (
                  // El mismo SKU puede venir dos veces (una por bodega): la llave lleva las dos.
                  <li key={`${l.sku}|${l.almacen ?? ""}|${i}`} className="flex items-center gap-2 px-2.5 py-1.5 text-[11.5px]">
                    <span className="max-w-[40%] shrink-0 truncate font-mono font-semibold text-slate-700" title={l.sku}>
                      {l.sku}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-slate-500" title={l.titulo ?? undefined}>
                      {l.titulo ?? "—"}
                    </span>
                    {l.almacen && (
                      <span className="shrink-0 rounded bg-slate-50 px-1 font-mono text-[10px] font-semibold text-slate-400 ring-1 ring-slate-200"
                            title={`Bodega ${l.almacen}`}>
                        {l.almacen}
                      </span>
                    )}
                    <span className={`shrink-0 font-semibold tabular-nums ${d.tono}`}>{d.texto}</span>
                  </li>
                );
              })}
              {lineas.length > MAX_RENGLONES && (
                <li className="px-2.5 py-1.5 text-[11px] text-slate-400">
                  y {num(lineas.length - MAX_RENGLONES)} {lineas.length - MAX_RENGLONES === 1 ? "renglón" : "renglones"} más
                </li>
              )}
            </ul>
          )}
          {cambios.length > 0 && (
            <ul className="mt-1.5 space-y-0.5 text-[11.5px] text-slate-600">
              {cambios.map((c) => (
                <li key={c.campo} className="break-words">
                  <span className="font-semibold text-slate-700">{ROTULO_CAMPO[c.campo] ?? c.campo}:</span>{" "}
                  <span className="text-slate-400 line-through decoration-slate-300">{valorCambio(c.campo, c.antes)}</span>
                  {" → "}
                  <span className="font-semibold text-slate-800">{valorCambio(c.campo, c.despues)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ── `datos` de un mensaje del sistema (jsonb: se lee sin confiar en la forma) ──

/**
 * Un renglón de `datos.lineas`, como lo deja cada transición de
 * `services/ordenes_venta.py` (léase junto con ese archivo). Todas traen `sku`,
 * `titulo`, `cantidad` y `almacen`; lo demás depende del evento:
 *   · `reservado` viene SIEMPRE, y vale 0 donde no aplica. Es lo que el renglón
 *     apartó (confirmada; y `creada` sólo en la orden automática, que nace
 *     confirmada) o lo que TENÍA apartado y se soltó (cancelada, borrada_admin,
 *     devolucion_esperada).
 *   · `entregado` es lo que salió del renglón: en `entregada`/`entregada_parcial`
 *     vienen sólo los renglones de ESA entrega; al cancelar vienen todos, con
 *     `null` en los que nunca salieron.
 *   · `libre` sólo viene en `no_alcanzo` (que lista sólo los que no alcanzaron):
 *     lo libre en su bodega, o `null` si el SKU no tiene fila de saldo ahí.
 * Lo que no viene se lee como `null` («no se dijo»), nunca como 0.
 */
export interface LineaDato {
  sku: string;
  titulo: string | null;
  cantidad: number | null;
  /** Bodega de kubera del renglón. */
  almacen: string | null;
  reservado: number | null;
  entregado: number | null;
  libre: number | null;
  /**
   * La bitácora trae `libre` y es `null`: el SKU no tiene existencias
   * REGISTRADAS en esa bodega. No es «0 libres» ni «no se dijo».
   */
  sinSaldo: boolean;
}

/** Un número que pudo llegar como texto (los `numeric` de Postgres). `null` = sin dato, no cero. */
function numero(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim()) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Un texto de la bitácora: recortado, y `null` si vino vacío o no es texto. */
const cadena = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

export function lineasDe(datos: Record<string, unknown> | null): LineaDato[] {
  const crudas = datos?.lineas;
  if (!Array.isArray(crudas)) return [];
  const salida: LineaDato[] = [];
  for (const x of crudas) {
    if (!x || typeof x !== "object") continue;
    const o = x as Record<string, unknown>;
    if (typeof o.sku !== "string" || !o.sku) continue;
    salida.push({
      sku: o.sku,
      titulo: cadena(o.titulo),
      cantidad: numero(o.cantidad),
      almacen: cadena(o.almacen),
      reservado: numero(o.reservado),
      entregado: numero(o.entregado),
      libre: numero(o.libre),
      sinSaldo: "libre" in o && o.libre === null,
    });
  }
  return salida;
}

const VERDE = "text-emerald-700";
const AMBAR = "text-amber-700";
const GRIS = "text-slate-500";

/**
 * Lo que le pasó a ESE renglón en ese movimiento, con la palabra del evento:
 * «apartadas» al confirmar, «salieron N de M» al entregar, «liberadas» al
 * cancelar o borrar. Lo que no se sabe se dice; nunca se pinta un 0 inventado.
 */
export function detalleLinea(evento: string | null, l: LineaDato): { texto: string; tono: string } {
  const c = l.cantidad;
  const r = l.reservado;
  const e = l.entregado;
  if (c === null) return { texto: "sin dato", tono: "text-slate-400" };
  const piezas = `${num(c)} ${c === 1 ? "pza" : "pzs"}`;
  const salieron = (n: number) => `${n === 1 ? "salió" : "salieron"} ${num(n)} de ${num(c)}`;
  switch (evento) {
    case "creada":
      // Un borrador no aparta (`reservado` 0): sólo sus piezas. La orden
      // AUTOMÁTICA de una venta nace confirmada, y ahí «creada» sí apartó.
      return r !== null && r > 0
        ? { texto: `${num(r)} ${r === 1 ? "apartada" : "apartadas"}`, tono: VERDE }
        : { texto: piezas, tono: GRIS };
    case "confirmada":
      // Se aparta todo o nada: en una confirmada lo apartado ES la cantidad.
      // Si la bitácora dijera otra cosa, se pinta lo que dice, en ámbar.
      if (r === null) return { texto: piezas, tono: GRIS };
      return r >= c ? { texto: `${num(r)} ${r === 1 ? "apartada" : "apartadas"}`, tono: VERDE }
        : { texto: `${num(r)} de ${num(c)} apartadas`, tono: AMBAR };
    case "no_alcanzo":
      // Vienen sólo los renglones que NO alcanzaron. «Sin existencias» (el SKU
      // no tiene fila de saldo en esa bodega) no es lo mismo que «hay 0 libres».
      if (l.sinSaldo) return { texto: `pide ${num(c)} · sin existencias`, tono: AMBAR };
      if (l.libre === null) return { texto: `pide ${num(c)}`, tono: GRIS };
      return { texto: `pide ${num(c)} · hay ${num(l.libre)} ${l.libre === 1 ? "libre" : "libres"}`,
               tono: l.libre >= c ? GRIS : AMBAR };
    case "entregada":
    case "entregada_parcial":
      // Vienen los renglones de ESTA entrega, cada uno con lo que salió. (Si
      // alguno llegara sin `entregado`, se dice que sigue por salir.)
      if (e === null) return { texto: evento === "entregada_parcial" ? `${piezas} · por salir` : piezas, tono: GRIS };
      // Salió completo, salió de menos, o no salió (0: se soltó su apartado).
      return { texto: salieron(e), tono: e >= c ? VERDE : e > 0 ? AMBAR : GRIS };
    case "cancelada":
    case "borrada_admin":
    case "devolucion_esperada": {
      // Los tres cierran la orden y dicen de cada renglón lo mismo: qué salió
      // (si salió algo, tiene que regresar: ámbar) y qué tenía apartado y se
      // soltó. `devolucion_esperada` es también el evento de CANCELAR una orden
      // con piezas ya fuera, y de «sí salió»: por eso va con las otras dos.
      const partes: string[] = [];
      if (e !== null) partes.push(salieron(e));
      if (r !== null && r > 0) partes.push(`${num(r)} ${r === 1 ? "liberada" : "liberadas"}`);
      if (!partes.length) return { texto: piezas, tono: GRIS };
      return { texto: partes.join(" · "), tono: e !== null && e > 0 ? AMBAR : GRIS };
    }
    default:
      return { texto: piezas, tono: GRIS };
  }
}

interface Cambio {
  campo: string;
  antes: unknown;
  despues: unknown;
}

/**
 * `datos.cambios` de un mensaje (hoy, el de `borrador_guardado`): el antes y el
 * después de cada campo del encabezado. Se aceptan las tres formas razonables de
 * un jsonb —lista de {campo, antes, despues}, objeto {campo: {antes, despues}} u
 * objeto {campo: [antes, después]}— porque la bitácora sólo se agrega: lo que ya
 * se escribió con una forma no se reescribe con otra.
 */
export function cambiosDe(datos: Record<string, unknown> | null): Cambio[] {
  const crudos = datos?.cambios;
  if (!crudos || typeof crudos !== "object") return [];
  const par = (campo: string, v: unknown): Cambio | null => {
    if (Array.isArray(v)) return v.length >= 2 ? { campo, antes: v[0], despues: v[1] } : null;
    if (!v || typeof v !== "object") return null;
    const o = v as Record<string, unknown>;
    if (!("antes" in o) && !("despues" in o) && !("después" in o) && !("ahora" in o)) return null;
    return { campo, antes: o.antes, despues: o.despues ?? o["después"] ?? o.ahora };
  };
  const salida: Cambio[] = [];
  if (Array.isArray(crudos)) {
    for (const x of crudos) {
      if (!x || typeof x !== "object") continue;
      const campo = (x as Record<string, unknown>).campo;
      const c = typeof campo === "string" && campo ? par(campo, x) : null;
      if (c) salida.push(c);
    }
    return salida;
  }
  for (const [campo, v] of Object.entries(crudos as Record<string, unknown>)) {
    const c = par(campo, v);
    if (c) salida.push(c);
  }
  return salida;
}

/**
 * Las columnas de `ventas.ov_ordenes` que un borrador puede cambiar, como se
 * llaman en la pantalla. Ya no está «Almacén»: la bodega dejó de ser del
 * encabezado y va por renglón (se ve en la mini-tabla de cada movimiento).
 */
export const ROTULO_CAMPO: Record<string, string> = {
  cliente: "Cliente",
  canal: "Canal",
  mp_canal: "Canal de la venta",
  mp_cuenta: "Cuenta",
  mp_orden: "Orden de marketplace",
  descripcion: "Descripción",
  guia: "Guía",
  paqueteria: "Paquetería",
  fecha_venta: "Fecha de venta",
  entrega_limite: "Entrega límite",
  moneda: "Moneda",
  total: "Total",
  comision: "Comisión",
  precio_origen: "Origen del precio",
  devolucion_estado: "Devolución",
};

function valorCambio(campo: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (campo === "total" || campo === "comision") {
    const n = numero(v);
    if (n !== null) return dinero(n);
  }
  if ((campo === "fecha_venta" || campo === "entrega_limite") && typeof v === "string") return fechaHora(v, v);
  if ((campo === "canal" || campo === "mp_canal") && typeof v === "string") return rotuloCanal(v);
  if (typeof v === "number") return num(v);
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return "—";
  }
}
