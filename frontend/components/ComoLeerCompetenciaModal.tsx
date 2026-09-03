"use client";

/**
 * ComoLeerCompetenciaModal — el tutorial de la pantalla de Competencia.
 *
 * Existe por una pregunta que se repite y que la pantalla no contesta sola:
 * **¿esto de cuándo es?** El tab mezcla cinco datos con cinco cadencias
 * distintas —visitas diarias, precio cada 15 minutos, ventas al instante,
 * ranking quincenal, sondeo diario— y los muestra juntos, como si fueran la
 * misma foto. No lo son, y decidir con la mezcla es lo que se quiere evitar.
 *
 * El orden del cuerpo NO es el de Costos, y es a propósito. Allá el riesgo es
 * confundir dos botones, así que abre con "qué hace cada botón" y sigue en
 * pasos. Aquí el riesgo es creer que todo está igual de fresco, así que abre
 * con la TABLA DE CADENCIAS y sólo después habla de controles. Se consulta más
 * de lo que se aprende.
 *
 * Es contenido ESTÁTICO: no pide nada al backend, no toca los filtros y se
 * puede abrir con el tab a medio trabajo sin perder nada. Las capturas viven en
 * `public/ayuda/competencia/` —se sirven como archivos, no embebidas— y son del
 * tab de verdad, tomadas a 2x.
 *
 * `Captura` y `Cuidado` son gemelos de los de `ComoValidarCostosModal`. Se
 * duplican a propósito en vez de extraerse: sacarlos obligaría a tocar una
 * pantalla que ya funciona, y el ahorro son 70 líneas. Si aparece un tercer
 * tutorial, ahí sí conviene el módulo común.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Clock,
  DollarSign,
  Eye,
  Filter,
  Search,
  X,
  ZoomIn,
} from "lucide-react";

import { COLOR } from "@/components/resolver/comunes";

const BASE = "/ayuda/competencia";

/** Una captura: se ve encajada y se abre a tamaño real al hacer clic. */
function Captura({ src, alt, pie }: { src: string; alt: string; pie: string }) {
  const [grande, setGrande] = useState(false);

  // Esc cierra el visor. El tutorial tiene su propio Esc y se registró ANTES
  // (monta primero), así que corre primero: ve `data-visor-abierto` y se
  // abstiene. Una tecla, una capa.
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
      {/* Las capturas de este tab son ANCHAS Y BAJITAS —tiras de filtros, una
          fila de tabla—: escalarlas al ancho del modal deja el texto ilegible,
          así que por debajo de ~760 px se desplazan en vez de encogerse más. */}
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
          {/* `w-max` + `min-w-full`: cuando la imagen cabe se centra, y cuando
              no, el envoltorio ya es del tamaño de la imagen y centrar no
              recorta el inicio (la trampa clásica de `justify-center`). */}
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

/** Un cuidado. `alto` = puede costar dinero o llevar a una decisión mala. */
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
        <AlertTriangle size={14} className={alto ? "text-rose-500" : "text-amber-500"} />
        <h4 className="text-xs font-bold text-slate-800">{titulo}</h4>
        <span
          className={`text-[9px] font-black uppercase tracking-wider ${
            alto ? "text-rose-500" : "text-amber-500"
          }`}
        >
          {alto ? "Cuesta dinero" : "Aviso"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-slate-600">{children}</p>
    </div>
  );
}

/** Un renglón de la tabla de cadencias. */
function Cadencia({
  que,
  detalle,
  cuando,
  cuandoDetalle,
  fuente,
  fuenteDetalle,
  cuesta,
}: {
  que: string;
  detalle: string;
  cuando: string;
  cuandoDetalle?: string;
  fuente: string;
  fuenteDetalle?: string;
  cuesta?: boolean;
}) {
  return (
    <div
      className={`grid grid-cols-[1.5fr_1.1fr_1fr] items-center gap-2 border-b border-slate-100 px-4 py-3 last:border-b-0 ${
        cuesta ? "bg-amber-50" : ""
      }`}
    >
      <div>
        <div className="text-xs font-semibold text-slate-900">{que}</div>
        <div className="text-[11px] text-slate-500">{detalle}</div>
      </div>
      <div>
        <div className={`text-xs ${cuesta ? "font-semibold text-slate-700" : "text-slate-700"}`}>
          {cuando}
        </div>
        {cuandoDetalle && <div className="text-[11px] text-slate-500">{cuandoDetalle}</div>}
      </div>
      <div>
        <div className={`text-xs font-semibold ${cuesta ? "text-amber-700" : "text-emerald-700"}`}>
          {fuente}
        </div>
        {fuenteDetalle && (
          <div className={`text-[11px] ${cuesta ? "text-amber-700" : "text-slate-500"}`}>
            {fuenteDetalle}
          </div>
        )}
      </div>
    </div>
  );
}

/** Una de las tres tarjetas que desarman los sellos del encabezado. */
function Sello({
  titulo,
  ambar,
  children,
}: {
  titulo: string;
  ambar?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`rounded-xl border-t-[3px] p-4 ring-1 ${
        ambar
          ? "border-amber-500 bg-amber-50 ring-amber-200"
          : "border-indigo-600 bg-slate-50 ring-slate-200"
      }`}
    >
      <div className={`text-xs font-bold ${ambar ? "text-amber-800" : "text-indigo-700"}`}>
        {titulo}
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-slate-600">{children}</p>
    </div>
  );
}

export default function ComoLeerCompetenciaModal({ onCerrar }: { onCerrar: () => void }) {
  // Esc cierra: el tutorial se abre a media tarea y tiene que salirse del
  // camino sin buscar el botón.
  const escape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Con una captura abierta a tamaño real, Esc es para ESA capa.
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
    /* El que hace scroll es el CUERPO, no la página: la tarjeta se acota a la
       altura de la ventana y cabecera y pie quedan fijos DENTRO de ella, con el
       botón de cerrar siempre a la mano. */
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
            <h2 className="text-base font-bold text-slate-900">Cómo leer Competencia</h2>
            <p className="text-xs text-slate-500">
              Casi todo se actualiza solo. Sólo dos botones cobran, y cobran cosas
              distintas.
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
          {/* ── 1 · Las cadencias. Va PRIMERO: es lo que se viene a consultar ── */}
          <section className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <Clock size={15} className="text-slate-400" />
              Qué se actualiza solo, y cada cuánto
            </h3>
            <p className="max-w-[78ch] text-xs leading-relaxed text-slate-600">
              No hay que apretar nada para que estos cuatro estén al día. La única fila
              con costo es la última.
            </p>

            <div className="overflow-hidden rounded-xl ring-1 ring-slate-200">
              <div className="grid grid-cols-[1.5fr_1.1fr_1fr] gap-2 border-b border-slate-200 bg-slate-50 px-4 py-2 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                <div>Qué</div>
                <div>Cada cuánto</div>
                <div>De dónde / costo</div>
              </div>
              <Cadencia
                que="Visitas de 30 días"
                detalle="de nuestras 4,702 publicaciones vivas"
                cuando="Diario · 6:00 a.m."
                fuente="API de ML · gratis"
              />
              <Cadencia
                que="Estado y precio"
                detalle="activa / pausada, y cuánto cuesta"
                cuando="Cada 15 minutos"
                fuente="Sync de inventario · gratis"
              />
              <Cadencia
                que="Ventas de 30 días"
                detalle="unidades vendidas por publicación"
                cuando="Al momento de vender"
                fuente="Webhook · gratis"
              />
              <Cadencia
                que="¿ML publica ranking aquí?"
                detalle="y ¿el top ya se movió desde que lo capturamos?"
                cuando="Diario · 6:00 a.m."
                fuente="API de ML · gratis"
              />
              <Cadencia
                que="Términos más buscados"
                detalle="lo que la gente escribe en el buscador de esa categoría"
                cuando="Diario · 6:00 a.m."
                fuente="API de ML · gratis"
              />
              <Cadencia
                cuesta
                que="Ranking «más vendidos»"
                detalle="el top real de cada categoría"
                cuando="Quincenal"
                cuandoDetalle="los días 1 y 16, 7:00 a.m."
                fuente="Raspado · ~$0.0067 por categoría"
                fuenteDetalle="unos $3.20 por barrido"
              />
              <Cadencia
                cuesta
                que="Búsqueda general"
                detalle="qué sale en ML al buscar el término de un SKU"
                cuando="Sólo si lo pides"
                cuandoDetalle="con el botón «Medir»"
                fuente="Raspado · ~$0.007 por término"
              />
            </div>
          </section>

          {/* ── 2 · Los tres sellos ── */}
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-bold text-slate-800">
              Los tres sellos del encabezado no significan lo mismo
            </h3>
            <Captura
              src={`${BASE}/03-sellos.png`}
              alt="La tira de frescura del encabezado de Competencia"
              pie="Están juntos y parecen la misma cosa. No lo son."
            />
            <div className="grid gap-3 md:grid-cols-3">
              <Sello titulo="Visitas medidas">
                Cuándo se <b>midieron</b>. Si dice «hoy», el número es de esta mañana.
              </Sello>
              <Sello titulo="Ranking capturado">
                Cuándo se <b>raspó</b>. Entre quincena y quincena, ML sigue moviéndose —
                y el aviso «ML ya se movió» avisa cuándo.
              </Sello>
              <Sello ambar titulo="Ventas hasta">
                Hasta qué día <b>cubre</b>, no cuándo se trajo. Un día sin ventas no
                genera fila, así que «ayer» puede ser correcto y estar al día.
              </Sello>
            </div>
          </section>

          {/* ── 3 · Lo que cuesta. Eran dos afirmaciones falsas desde v0.377.0:
                 el subtítulo y este título decían "lo único", y desde que existe
                 el botón «Medir» de la búsqueda general son DOS. Una guía que
                 miente sobre dónde se gasta es peor que no tenerla. ── */}
          <section className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <DollarSign size={15} className="text-slate-400" />
              Los dos botones que cuestan dinero
            </h3>
            <Captura
              src={`${BASE}/04-mas-vendidos.png`}
              alt="La cabecera MÁS VENDIDOS de una subcategoría, con el botón Actualizar a la derecha"
              pie="Vive en la cabecera MÁS VENDIDOS de cada subcategoría, a la derecha."
            />
            <Cuidado alto titulo="Cada apretón raspa esa categoría otra vez">
              Vuelve a pedirle el ranking a Apify en el momento, y cuesta alrededor de{" "}
              <b>$0.0067</b>. Vale la pena cuando el aviso dice que el top ya se movió y
              esa categoría te importa. No vale la pena por curiosidad: el barrido del
              día 1 o el 16 ya la va a cubrir.
            </Cuidado>
            <Cuidado titulo="«Medir» y «Actualizar» no son el mismo botón">
              <b>Actualizar</b> vuelve a raspar el <b>ranking de la categoría</b>;{" "}
              <b>Medir</b> vuelve a buscar <b>un término</b>. Cuestan casi lo mismo
              —~$0.0067 y ~$0.007— pero traen cosas distintas, y apretar uno no
              refresca lo del otro.
            </Cuidado>
            <Cuidado titulo="Una vez al día por categoría">
              Si ya se raspó hoy, el botón <b>no vuelve a cobrar</b>: contesta cuándo se
              actualizó y te deja pedirlo mañana. Dos personas mirando productos
              distintos de la misma categoría pagarían la misma página dos veces, y eso
              es lo que evita.
              <br />
              <br />
              Ese mensaje aparece en gris debajo del botón. <b>No es un error</b> — es la
              respuesta: ya está fresco.
            </Cuidado>
          </section>

          {/* ── 4 · Vistas ── */}
          <section className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <Eye size={15} className="text-slate-400" />
              Las vistas: cada botón es una pregunta
            </h3>
            <Captura
              src={`${BASE}/01-vistas.png`}
              alt="Los tres botones de vista de Competencia"
              pie="Se activan y se apagan con un clic. El número es cuántas subcategorías caen en cada pregunta."
            />
            <div className="grid gap-3 text-xs leading-relaxed text-slate-600 md:grid-cols-3">
              <div>
                <b className="text-slate-900">Todas</b> — el árbol completo. Ese número
                es cuántas subcategorías hay, no cuántas tienen problema.
              </div>
              <div>
                <b className="text-slate-900">Nos ven y no compran</b> — llega gente y no
                compra. Mira al de enfrente: precio, fotos, título.
              </div>
              <div>
                <b className="text-slate-900">Publicada y nadie la ve</b> — ni siquiera
                llegan. Es un problema de visibilidad, no de oferta.
              </div>
            </div>
            <Cuidado titulo="Las dos últimas sólo miran publicaciones ACTIVAS">
              Una pausada no cuenta: quien no pudo comprar no es un cliente perdido. Y
              una publicación <b>sin medir</b> tampoco entra en «nadie la ve» — no saber
              si la vieron no es lo mismo que saber que no.
            </Cuidado>
          </section>

          {/* ── 5 · Filtros ── */}
          <section className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <Filter size={15} className="text-slate-400" />
              Los filtros acotan lo que ya está en pantalla
            </h3>
            <Captura
              src={`${BASE}/02-filtros.png`}
              alt="La barra de filtros de Competencia"
              pie="Ninguno pide nada al servidor ni gasta: el árbol completo ya viene cargado."
            />
            <div className="grid gap-x-6 gap-y-2 text-xs leading-relaxed text-slate-600 md:grid-cols-2">
              <div>
                <b className="text-slate-900">Solo top</b> — deja únicamente las
                subcategorías donde tenemos producto dentro del top de ML.
              </div>
              <div>
                <b className="text-slate-900">Visitas 30 días</b> — la barra de mín/máx
                recorta por tráfico: sube el mínimo para quedarte con lo que sí se mueve.
              </div>
              <div>
                <b className="text-slate-900">Filtrar SKUs</b> — acepta varios separados
                por coma.
              </div>
              <div>
                <b className="text-slate-900">Limpiar</b> — suelta todos los filtros de
                un golpe. Sólo aparece si hay alguno puesto.
              </div>
            </div>
          </section>

          {/* ── 6 · La tabla ── */}
          <section className="flex flex-col gap-3">
            <h3 className="text-sm font-bold text-slate-800">Una subcategoría por dentro</h3>
            <Captura
              src={`${BASE}/05-tabla-skus.png`}
              alt="Tabla NUESTROS SKUS con producto, tienda, publicación, precio, visitas y ventas"
              pie="Un renglón por SKU, y debajo una línea por tienda: el mismo producto puede estar en las dos cuentas con precios distintos."
            />
            <div className="flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex-none rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                  active
                </span>
                <p className="text-xs leading-relaxed text-slate-600">
                  La publicación está viva y se puede comprar. Una <b>pausada</b> sigue
                  apareciendo porque el histórico importa, pero no cuenta para las vistas
                  de arriba.
                </p>
              </div>
              <div className="flex items-start gap-3">
                <span className="mt-0.5 flex-none rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
                  sin confirmar
                </span>
                <p className="text-xs leading-relaxed text-slate-600">
                  Ese precio nunca se verificó contra Mercado Libre: sale del sync de 15
                  minutos, que <b>no ve las promociones</b>. El que se cobra puede ser más
                  bajo. Cuando sí se confirmó, en su lugar dice cuándo.
                </p>
              </div>
            </div>
          </section>

          {/* ── 7 · Competencia directa. Nunca estuvo en la guía, y es la
                 parte que más confunde: dos mitades que parecen una y tienen
                 economías opuestas —una gratis y diaria, la otra de pago y a
                 demanda—. ── */}
          <section className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-sm font-bold text-slate-800">
              <Search size={15} className="text-slate-400" />
              Competencia directa: dos mitades que no se parecen
            </h3>
            <Captura
              src={`${BASE}/06-competencia-directa.png`}
              alt="La sección Competencia directa: búsqueda general a la izquierda y los términos más buscados a la derecha"
              pie="Izquierda: lo que se paga. Derecha: lo que se refresca solo."
            />
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-xl border-t-[3px] border-amber-500 bg-amber-50 p-4 ring-1 ring-amber-200">
                <div className="text-xs font-bold text-amber-800">
                  Izquierda · Búsqueda general
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
                  Qué sale en Mercado Libre al buscar el término de ese SKU. Se paga{" "}
                  <b>por término, no por SKU</b>: 80 términos cubren 522 SKUs, así que
                  medir uno sirve a todos los que lo comparten. Botón <b>«Medir»</b>,
                  ~$0.007, una vez al día por término. Tarda entre uno y dos minutos —
                  es el raspado, no la pantalla.
                </p>
              </div>
              <div className="rounded-xl border-t-[3px] border-indigo-600 bg-slate-50 p-4 ring-1 ring-slate-200">
                <div className="text-xs font-bold text-indigo-700">
                  Derecha · Top 10 términos más buscados
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
                  Lo que la gente escribe en el buscador de esa categoría, en orden de
                  volumen. Se refresca solo, a diario, y <b>no cuesta</b>. Ojo: ML
                  publica el <b>ORDEN, no el número</b> — el #1 se busca más que el #2,
                  pero no se sabe cuántas veces.
                </p>
              </div>
            </div>
            <Cuidado titulo="Un «0 de 40 cubiertos» no siempre es culpa del título">
              Ese contador dice cuántos de esos términos aparecen <b>completos</b> en
              alguno de nuestros títulos. Pero si el SKU está en la <b>categoría
              equivocada</b>, se está comparando contra las búsquedas de otro producto
              y ahí nunca va a haber un ✓ por más que se mejore el título. Caso real:
              un chispero clasificado en «Kits de Seguridad», midiéndose contra
              búsquedas de cámaras de vigilancia.
            </Cuidado>
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
