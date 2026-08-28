"use client";

/**
 * ComoValidarCostosModal — el tutorial de la pantalla de Costos.
 *
 * Existe porque los dos botones de esta pantalla se confunden entre sí, y
 * confundirlos es el riesgo real de aquí: uno arregla DE DÓNDE SALE el costo
 * (contra el packing list) y el otro decide QUÉ PRECIO sale de ese costo (y lo
 * empuja a WooCommerce). Hacerlos en el orden equivocado deja un precio
 * impecable calculado sobre un costo inventado.
 *
 * Es contenido ESTÁTICO a propósito: no pide nada al backend, no toca la
 * selección de la tabla y se puede abrir con el panel a medio trabajo sin
 * perder nada. Las capturas viven en `public/ayuda/costos/` — se sirven como
 * archivos, no embebidas, para no cargar el bundle con 320 KB de PNG que la
 * mayoría de las sesiones nunca abre.
 *
 * Las capturas son anchas y bajitas (filas de tabla): a lo ancho del modal el
 * texto de adentro queda ilegible, así que cada una se puede abrir a tamaño
 * real con un clic. Ese visor es el `<figure>` de abajo, no una librería.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Check,
  Info,
  PackageSearch,
  RefreshCw,
  X,
  ZoomIn,
} from "lucide-react";

import { COLOR } from "@/components/resolver/comunes";

const BASE = "/ayuda/costos";

/** Una captura: se ve encajada y se abre a tamaño real al hacer clic. */
function Captura({
  src,
  alt,
  pie,
}: {
  src: string;
  alt: string;
  pie: string;
}) {
  const [grande, setGrande] = useState(false);

  // Esc cierra el visor. El tutorial tiene su propio Esc y se registró ANTES
  // (monta primero), así que corre primero: ve `data-visor-abierto` y se
  // abstiene. El resultado es el esperado — una tecla, una capa.
  useEffect(() => {
    if (!grande) return;
    const cerrar = (e: KeyboardEvent) => {
      if (e.key === "Escape") setGrande(false);
    };
    document.addEventListener("keydown", cerrar);
    return () => document.removeEventListener("keydown", cerrar);
  }, [grande]);

  return (
    <figure className="m-0">
      {/* overflow-x-auto y min-w: las capturas son de 1,568 px de ancho y unas
          pocas decenas de alto. Escalarlas al ancho del modal las vuelve
          ilegibles, así que por debajo de ~760 px se desplazan en vez de
          encogerse más. */}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <button
          type="button"
          onClick={() => setGrande(true)}
          title="Ver a tamaño real"
          className="block w-full cursor-zoom-in"
        >
          <img src={src} alt={alt} className="block h-auto w-full min-w-[760px]" />
        </button>
      </div>
      <figcaption className="mt-2 flex items-start gap-1.5 text-xs text-slate-500">
        <ZoomIn size={13} className="mt-0.5 flex-none text-slate-400" />
        <span>{pie}</span>
      </figcaption>

      {grande && (
        /* z-[70]: por encima del modal del tutorial (z-50), igual que los
           diálogos de confirmación del validador se ponen sobre el suyo.
           `data-visor-abierto` lo lee el Esc del tutorial para no cerrarse
           por debajo del visor: la tecla cierra primero la capa de encima. */
        <div
          data-visor-abierto=""
          className="fixed inset-0 z-[70] overflow-auto bg-slate-900/90"
          onClick={() => setGrande(false)}
        >
          <button
            type="button"
            onClick={() => setGrande(false)}
            className="fixed right-5 top-4 z-[71] rounded-lg bg-white px-3 py-2 text-xs font-bold text-slate-700 shadow-lg hover:bg-slate-100"
          >
            Cerrar ✕
          </button>
          {/* Centrar algo MÁS GRANDE que su contenedor con scroll tiene una
              trampa conocida: con `justify-center` a secas el navegador recorta
              el inicio y ese trozo queda inalcanzable. Por eso el envoltorio se
              mide con `w-max` (crece hasta la imagen) y `min-w-full`/`min-h-full`
              (nunca menor que la pantalla): cuando la imagen cabe, se centra; y
              cuando no, el envoltorio ya es del tamaño de la imagen y centrar no
              recorta nada. */}
          <div className="flex min-h-full w-max min-w-full items-center justify-center p-6">
            <img
              src={src}
              alt={alt}
              onClick={(e) => e.stopPropagation()}
              className="h-auto max-w-none rounded-lg bg-white shadow-2xl"
            />
          </div>
        </div>
      )}
    </figure>
  );
}

/** Rótulo de paso: el orden de esta pantalla es información, no adorno. */
function Paso({ n, titulo }: { n: number; titulo: string }) {
  return (
    <div className="flex items-center gap-3">
      <span
        className="flex h-6 w-6 flex-none items-center justify-center rounded-full text-[11px] font-black text-white"
        style={{ background: COLOR }}
      >
        {n}
      </span>
      <h3 className="text-sm font-bold text-slate-800">{titulo}</h3>
      <span className="h-px flex-1 bg-slate-200" />
    </div>
  );
}

/** Un peldaño de la escalera de empate, con el mismo chip que pinta el validador. */
function Peldano({
  chip,
  color,
  texto,
}: {
  chip: string;
  color: string;
  texto: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 border-b border-slate-100 px-4 py-3 last:border-b-0">
      <span
        className={`self-start rounded-full px-2 py-0.5 text-[10px] font-bold ring-1 ${color}`}
      >
        {chip}
      </span>
      <span className="text-xs leading-relaxed text-slate-600">{texto}</span>
    </div>
  );
}

/** Un cuidado. `alto` = puede escribir un dato malo sin avisar. */
function Cuidado({
  alto,
  titulo,
  children,
}: {
  alto?: boolean;
  titulo: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rounded-lg border-l-[3px] bg-white p-4 ring-1 ring-slate-200 ${
        alto ? "border-rose-500" : "border-amber-400"
      }`}
    >
      <div className="mb-1 flex items-center gap-2">
        <AlertTriangle
          size={14}
          className={alto ? "text-rose-500" : "text-amber-500"}
        />
        <h4 className="text-xs font-bold text-slate-800">{titulo}</h4>
        <span
          className={`text-[9px] font-black uppercase tracking-wider ${
            alto ? "text-rose-500" : "text-amber-500"
          }`}
        >
          {alto ? "Crítico" : "Aviso"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-slate-600">{children}</p>
    </div>
  );
}

export default function ComoValidarCostosModal({
  onCerrar,
}: {
  onCerrar: () => void;
}) {
  // Esc cierra: el tutorial se abre a media tarea y tiene que salirse del
  // camino sin buscar el botón.
  const escape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Con una captura abierta a tamaño real, Esc es para ESA capa: cerrar el
      // tutorial por debajo dejaría al usuario mirando una imagen suelta.
      if (document.querySelector("[data-visor-abierto]")) return;
      onCerrar();
    },
    [onCerrar],
  );
  useEffect(() => {
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [escape]);

  return (
    /* El que hace scroll es el CUERPO, no la página: con `sticky` sobre el
       contenedor de afuera la cabecera se despegaba de la tarjeta y flotaba
       encima del contenido, como si se saliera del recuadro. Aquí la tarjeta se
       acota a la altura de la ventana y cabecera y pie quedan fijos DENTRO de
       ella, que es lo que el usuario espera de un tutorial largo: el botón de
       cerrar siempre a la mano. */
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/50 p-4">
      <div className="flex max-h-full w-full max-w-[1100px] flex-col rounded-2xl bg-white shadow-2xl">
        {/* ── Cabecera ── */}
        <div className="flex flex-none items-center gap-3 rounded-t-2xl border-b border-slate-200 bg-white px-6 py-4">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-lg text-white"
            style={{ background: COLOR }}
          >
            <BookOpen size={18} />
          </div>
          <div className="flex-1">
            <h2 className="text-base font-bold text-slate-900">
              Cómo validar costos
            </h2>
            <p className="text-xs text-slate-500">
              Dos botones, dos trabajos distintos. Uno arregla de dónde sale el
              costo; el otro, qué precio sale de ese costo.
            </p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            aria-label="Cerrar el tutorial"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex flex-1 flex-col gap-8 overflow-y-auto px-6 py-6">
          {/* ── Los dos botones ── */}
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-bold text-slate-800">
              Qué hace cada botón
            </h3>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="flex flex-col gap-3 rounded-xl border-t-[3px] border-indigo-600 bg-slate-50 p-4 ring-1 ring-slate-200">
                <span className="flex items-center gap-2 self-start rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700">
                  <PackageSearch size={14} /> Validar costo · publicados en ML
                </span>
                <dl className="flex flex-col gap-2 text-xs">
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Contesta
                    </dt>
                    <dd className="text-slate-600">
                      ¿El costo base de este SKU es el correcto?
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Lee
                    </dt>
                    <dd className="text-slate-600">
                      El <b>packing list</b> del contenedor por el que llegó el
                      producto.
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Escribe
                    </dt>
                    <dd className="text-slate-600">
                      Medidas, peso y costo de proveedor. Y deja el SKU bajo el
                      candado <b>COSTO VALIDADO</b>.
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Aplica a
                    </dt>
                    <dd className="text-slate-600">
                      Solo productos con publicación viva en Mercado Libre.
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="flex flex-col gap-3 rounded-xl border-t-[3px] border-indigo-600 bg-slate-50 p-4 ring-1 ring-slate-200">
                <span
                  className="flex items-center gap-2 self-start rounded-lg px-3 py-1.5 text-xs font-bold text-white"
                  style={{ background: COLOR }}
                >
                  <RefreshCw size={14} /> Regenerar y guardar
                </span>
                <dl className="flex flex-col gap-2 text-xs">
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Contesta
                    </dt>
                    <dd className="text-slate-600">
                      ¿Qué precio corresponde a ese costo?
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Lee
                    </dt>
                    <dd className="text-slate-600">
                      Lo que hay en la fila, más la barra de abajo: TC, margen,
                      comisión y envío.
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Escribe
                    </dt>
                    <dd className="text-slate-600">
                      Flete CBM, comisión, envío, costo final y precio — en la
                      base <b>y en WooCommerce</b>.
                    </dd>
                  </div>
                  <div>
                    <dt className="font-bold uppercase tracking-wide text-slate-400">
                      Aplica a
                    </dt>
                    <dd className="text-slate-600">
                      Cualquier SKU seleccionado.
                    </dd>
                  </div>
                </dl>
              </div>
            </div>

            <div className="flex items-start gap-3 rounded-xl bg-indigo-50 p-4 ring-1 ring-indigo-100">
              <Info size={16} className="mt-0.5 flex-none text-indigo-600" />
              <div className="text-xs leading-relaxed text-slate-700">
                <b className="text-slate-900">
                  Primero validar, luego regenerar.
                </b>{" "}
                Al revés calculas un precio impecable sobre un costo equivocado.
                Y como el candado impide que la regeneración pise el costo ya
                validado, hacerlo en este orden es lo único que deja las dos
                cosas bien al mismo tiempo.
              </div>
            </div>
          </section>

          {/* ── El caso ── */}
          <section className="flex flex-col gap-3">
            <Paso n={1} titulo="Encuentra el SKU y ábrelo" />
            <p className="text-xs leading-relaxed text-slate-600">
              Este es <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[11px]">TEC-0492-MUL</code>{" "}
              antes de tocarlo: <b>20.4 × 19 × 13.5 cm</b>, tamaño{" "}
              <b>Chico</b>, margen <b>−11.1 %</b>. Esas no son las medidas del
              producto — y como el flete sale del volumen, todo lo que viene
              después es ruido.
            </p>
            <Captura
              src={`${BASE}/01-analisis-antes.png`}
              alt="Fila en Análisis antes de validar: 20.4 × 19 × 13.5, tamaño Chico, margen −11.1 %."
              pie="Análisis, antes. El margen en rojo todavía no significa nada: el costo del que sale está mal."
            />
            <p className="text-xs leading-relaxed text-slate-600">
              En <b>Costos</b>, busca el SKU y marca su casilla. Al
              seleccionarlo, la fila se abre con los campos editables y el
              desglose de seis tarjetas: costo producto, flete (CBM ×
              $7,500/m³), comisión ML, envío real, costo final y margen neto.
            </p>
            <Captura
              src={`${BASE}/02-fila-abierta.png`}
              alt="Pestaña Costos con el SKU seleccionado, el desglose de seis tarjetas y la barra de acciones."
              pie="La fila abierta, con el desglose y la barra de acciones al pie."
            />
            <p className="text-xs leading-relaxed text-slate-600">
              Puedes corregir medidas y costo a mano aquí mismo. Pero si el
              producto llegó en un contenedor con packing list,{" "}
              <b>no adivines</b>: pasa al paso 2.
            </p>
          </section>

          {/* ── Validar ── */}
          <section className="flex flex-col gap-3">
            <Paso n={2} titulo="Valida el costo contra el packing list" />
            <p className="text-xs leading-relaxed text-slate-600">
              Con los SKUs seleccionados, aprieta{" "}
              <b>Validar costo · publicados en ML</b>. A cada uno se le busca{" "}
              <b>su renglón</b> en el packing list que le toca, subiendo una
              escalera que se detiene en el primer peldaño que resuelve.
            </p>
            <Captura
              src={`${BASE}/03-ventana-validar.png`}
              alt="Ventana de Validar costo: chips por peldaño, pestañas y la comparación de costo nuevo contra costo de hoy."
              pie="La ventana busca el renglón de cada SKU. Nada se escribe hasta que aprietas Guardar."
            />

            <div className="rounded-xl bg-white ring-1 ring-slate-200">
              <Peldano
                chip="foto de Odoo · exacta"
                color="bg-emerald-50 text-emerald-700 ring-emerald-200"
                texto={
                  <>
                    La foto de Odoo y la del packing list son{" "}
                    <b>el mismo archivo</b>. Es el empate más fuerte que hay.
                  </>
                }
              />
              <Peldano
                chip="foto de Odoo · dHash"
                color="bg-teal-50 text-teal-700 ring-teal-200"
                texto={
                  <>
                    Las fotos se parecen lo suficiente y con margen sobre el
                    segundo candidato. Es una distancia medida: repetir la
                    corrida da lo mismo.
                  </>
                }
              />
              <Peldano
                chip="foto de ML + IA"
                color="bg-violet-50 text-violet-700 ring-violet-200"
                texto={
                  <>
                    La foto de Odoo no sirvió, así que se comparó la foto de{" "}
                    <b>la publicación</b> contra las candidatas y dictaminó la
                    IA. <b>Míralo antes de aprobarlo.</b>
                  </>
                }
              />
              <Peldano
                chip="sin empate"
                color="bg-amber-50 text-amber-800 ring-amber-200"
                texto={
                  <>
                    Ningún peldaño resolvió. Abajo quedan los candidatos con sus
                    fotos: eliges a mano o lo dejas fuera.
                  </>
                }
              />
              <Peldano
                chip="sin insumos"
                color="bg-slate-100 text-slate-600 ring-slate-200"
                texto={
                  <>
                    Faltó con qué trabajar: sin contenedor conocido, sin foto en
                    Odoo o sin packing list localizado. Puedes pegarle la liga
                    de Drive a mano.
                  </>
                }
              />
            </div>

            <ul className="flex flex-col gap-2 text-xs leading-relaxed text-slate-600">
              <li className="flex gap-2">
                <Check size={13} className="mt-0.5 flex-none text-emerald-500" />
                <span>
                  <b>Marcar los seguros</b> marca únicamente los deterministas
                  —exacta y dHash, sin confianza baja—.{" "}
                  <b>Los de IA nunca entran en lote.</b>
                </span>
              </li>
              <li className="flex gap-2">
                <Check size={13} className="mt-0.5 flex-none text-emerald-500" />
                <span>
                  La pestaña <b>Necesitan tu ojo</b> te deja solo los que hay
                  que decidir mirando las fotos.
                </span>
              </li>
              <li className="flex gap-2">
                <Check size={13} className="mt-0.5 flex-none text-emerald-500" />
                <span>
                  Compara <b>COSTO NUEVO</b> contra <b>COSTO HOY</b> antes de
                  aprobar: ahí ves cuánto se mueve.
                </span>
              </li>
              <li className="flex gap-2">
                <Check size={13} className="mt-0.5 flex-none text-emerald-500" />
                <span>
                  Nada se guarda hasta que aprietas{" "}
                  <b>Guardar N aprobados</b>. El análisis vive unas{" "}
                  <b>3 horas</b> y solo en memoria: si cierras la ventana sin
                  guardar, se pierde.
                </span>
              </li>
            </ul>
          </section>

          {/* ── Validado ── */}
          <section className="flex flex-col gap-3">
            <Paso n={3} titulo="La fila queda marcada VALIDADO" />
            <p className="text-xs leading-relaxed text-slate-600">
              De vuelta en Costos, el SKU ya trae las medidas reales del packing
              list: <b>4.4 × 41 × 29 cm</b>, <b>0.0052 m³</b>, <b>1.515 kg</b>,
              y el costo de proveedor <b>$45 USD → $855 MXN</b>.
            </p>
            <Captura
              src={`${BASE}/04-fila-validada.png`}
              alt="Fila con la insignia VALIDADO y las medidas corregidas del packing list."
              pie="La insignia VALIDADO es un candado, no una etiqueta."
            />
            <p className="text-xs leading-relaxed text-slate-600">
              Desde ese momento, <b>Regenerar y guardar</b> ya no reescribe el
              costo base de este SKU. Para cambiarlo hay que liberar el candado
              desde la misma ventana de validación, marcándolo explícitamente.
            </p>
          </section>

          {/* ── Regenerar ── */}
          <section className="flex flex-col gap-3">
            <Paso n={4} titulo="Regenera para que el precio se ponga al día" />
            <p className="text-xs leading-relaxed text-slate-600">
              Validar arregló el costo, pero el precio publicado sigue siendo el
              viejo. Con la fila ya validada, selecciónala y aprieta{" "}
              <b>Regenerar y guardar</b>: recalcula flete, comisión, envío,
              costo final y precio con los valores de la barra, y lo escribe en
              la base y en WooCommerce. El candado protege el costo base; el
              precio sí se rehace.
            </p>
            <Captura
              src={`${BASE}/05-analisis-despues.png`}
              alt="Fila en Análisis después: VALIDADO, tamaño Mediano, costo base $894.24, costo final $1,082.78."
              pie="Análisis, después. VALIDADO, el tamaño pasó a Mediano y el costo final se recalculó."
            />
            <div className="flex items-start gap-3 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-200">
              <ArrowRight size={16} className="mt-0.5 flex-none text-slate-400" />
              <p className="text-xs leading-relaxed text-slate-600">
                El margen sigue en rojo, y <b>eso ahora es información</b>. Antes
                el −11.1 % no quería decir nada, porque salía de un costo
                inventado. El −11.4 % de ahora sí: con el costo real, ese precio
                de venta no da. Es una decisión de precio, no un dato roto.
              </p>
            </div>
          </section>

          {/* ── Cuidados ── */}
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-bold text-slate-800">
              Lo que se rompe si no lo miras
            </h3>
            <div className="grid gap-3 md:grid-cols-2">
              <Cuidado alto titulo="El TC manda sobre toda la tanda">
                El costo de producto se captura en dólares y{" "}
                <b>se guarda en pesos</b>. La conversión usa el TC de la barra
                al regenerar. Si ese número está mal, quedan mal <b>todos</b>{" "}
                los SKUs seleccionados, no solo el que estabas mirando.
              </Cuidado>
              <Cuidado alto titulo="Regenerar reemplaza un precio puesto a mano">
                Si alguien fijó el precio manualmente en el Estudio, la
                regeneración lo sustituye por el precio derivado del{" "}
                <b>Margen %</b> de la barra. Revísalo antes de regenerar en lote
                sobre SKUs con precio negociado.
              </Cuidado>
              <Cuidado alto titulo="Nunca apruebes en lote lo que resolvió la IA">
                Está medido: dos corridas seguidas del mismo SKU eligieron
                renglones distintos —<b>$54 de diferencia</b> y dimensiones que
                no se parecen— y en ambas la IA se declaró de confianza alta.
                Por eso <b>Marcar los seguros</b> la deja fuera a propósito.
              </Cuidado>
              <Cuidado titulo="Un SKU ya validado se salta el guardado">
                Si vuelves a validar algo que ya tenía candado y no lo marcaste
                para liberarlo, aparecerá en la lista de <b>saltados</b> con ese
                motivo. No es un error: es el candado haciendo su trabajo.
              </Cuidado>
            </div>
          </section>
        </div>

        {/* ── Pie ── */}
        <div className="flex flex-none items-center justify-between gap-3 rounded-b-2xl border-t border-slate-200 bg-slate-50 px-6 py-4">
          <span className="text-xs text-slate-500">
            Haz clic en cualquier captura para verla a tamaño real.
          </span>
          <button
            type="button"
            onClick={onCerrar}
            className="rounded-lg px-4 py-2 text-sm font-bold text-white"
            style={{ background: COLOR }}
          >
            Entendido
          </button>
        </div>
      </div>
    </div>
  );
}
