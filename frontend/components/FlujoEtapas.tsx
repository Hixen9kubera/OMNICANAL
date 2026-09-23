"use client";

/* El STEPPER de /omnicanal: el FLUJO del SKU, de Recibido a Restock, con la
   cifra de ESTA pestaña y ESTA cuenta en cada paso.

   Opción 2 del lienzo (Eduardo, 23-sep): FLECHAS ENCADENADAS. Cada paso es una
   flecha con su número que entra en la siguiente, para que se lea como un
   camino y no como una fila de filtros sueltos. Dos cosas salen del camino a
   propósito: «Todo el catálogo» —es la SALIDA del filtro, no un paso— y
   «Costo validado», que corre en paralelo y lo dice debajo. «Listo para FULL o
   DROP» ya no es parada: se quitó aquí y en el sello de la lista.

   Sustituye a los chips «Costo validado» y «Solo DROP OFF». Tres reglas que
   vienen del backend y no se recalculan aquí:
   · el NÚMERO y el CLIC son independientes (`n` / `clicable`): que el conteo
     por canal se caiga no tiene por qué apagar el filtro de Recibido;
   · `n: null` se pinta «—» (o «…» mientras la foto se arma), NUNCA 0;
   · qué se puede abrir lo dice `clicable`. Si el frontend lo adivinara, un día
     ofrecería un filtro que el servidor no sabe aplicar. */

import { useMemo } from "react";
import { BadgeCheck } from "lucide-react";

import PanelHover from "@/components/PanelHover";
import {
  CANALES_CON_ETAPA, MUESTRA_FLUJO, NOTA_ETAPA, cifra, fotoVencida,
  horaCdmx, textoUnidad, type Muestra,
} from "@/lib/flujo";
import type {
  ClaveFlujo, ConteoCanalFlujo, EtapaCanalFlujo, EtapaOmnicanal,
} from "@/lib/types";

interface Props {
  /** `null` = todavía no hay respuesta (o falló): se pinta el esqueleto. */
  conteos: ConteoCanalFlujo | null;
  etapa: EtapaOmnicanal | null;
  onEtapa: (e: EtapaOmnicanal | null) => void;
  revisado: boolean;
  onRevisado: (v: boolean) => void;
  /** La cifra de «Todo el catálogo». `null` cuando no se puede afirmar. */
  totalTodas: number | null;
  /** Hay búsqueda, SKUs, estados o categoría: las cifras ya no describen la
   *  lista de abajo. Se apagan en vez de mentir. */
  atenuar: boolean;
  /** Hay una etapa puesta: la cifra del carril es la del canal, sin cruzarla. */
  atenuarCarril: boolean;
  canal: string;
  color: string;
  textoColor: string;
}

const NOTA_ATENUAR = "Cifra del canal y la cuenta, sin búsqueda ni filtros.";
const NOTA_CARRIL = "Cifra del canal, sin la etapa.";

const FUENTES: { k: "kubera" | "odoo" | "odoo_drop"; t: string }[] = [
  { k: "kubera", t: "kubera" },
  { k: "odoo", t: "Odoo" },
  { k: "odoo_drop", t: "Odoo DROP" },
];

/** La misma muestra del sello, a 12×8. La nota de cada paso la lleva junto al
 *  título: es la leyenda de los colores del sello de la lista. */
function MuestraChip({ m }: { m: Muestra }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg" width="12" height="8" viewBox="0 0 12 8"
      fill="none" aria-hidden="true"
    >
      <rect
        x="0.5" y="0.5" width="11" height="7" rx="1.5"
        fill={m.fill} stroke={m.stroke} strokeDasharray={m.dash}
      />
    </svg>
  );
}

/** Los cuatro cuadros de bodega: tres verdes y specs en ámbar, como en el sello. */
function MuestraBodega() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg" width="15" height="8" viewBox="0 0 15 8"
      fill="none" aria-hidden="true"
    >
      {[0.5, 4.5, 8.5].map((x) => (
        <rect
          key={x} x={x} y="0.5" width="2" height="7" rx="0.5"
          fill={MUESTRA_FLUJO.bodega.fill} stroke={MUESTRA_FLUJO.bodega.stroke}
        />
      ))}
      <rect
        x="12.5" y="0.5" width="2" height="7" rx="0.5"
        fill={MUESTRA_FLUJO.specs.fill} stroke={MUESTRA_FLUJO.specs.stroke}
      />
    </svg>
  );
}

function Pildora({ texto, clase }: { texto: string; clase: string }) {
  return (
    <span className={`rounded px-1 text-[9px] font-bold uppercase leading-[13.5px] tracking-wide ${clase}`}>
      {texto}
    </span>
  );
}

const ETIQUETA_NOTA = "text-[10px] font-semibold uppercase tracking-wide text-slate-400";

/** La nota de un paso. Mismo molde que la nota de la regla de precios:
 *  título con su muestra de color, una frase, y las preguntas en dos columnas.
 *  `avisos` es lo que depende del momento (foto vencida, conteo caído, filtros
 *  encima) y va en ámbar para que no se lea como parte de la definición. */
function Nota({ clave, titulo, muestra, pildora, detalle, avisos }: {
  clave: string;
  titulo: string;
  muestra?: React.ReactNode;
  pildora?: React.ReactNode;
  detalle?: string;
  avisos?: string[];
}) {
  const n = NOTA_ETAPA[clave];
  if (!n) return null;
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[13px] font-bold leading-5 text-slate-800">
        {muestra}
        <span>{titulo}</span>
        {pildora}
      </div>
      <p className="mt-0.5 text-[11px] leading-snug text-slate-600">{n.que}</p>
      <div className="my-2.5 h-px bg-slate-100" />
      <div className="grid grid-cols-[92px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-2 text-[11px] leading-snug">
        <span className={ETIQUETA_NOTA}>De dónde sale</span>
        <span className="text-slate-700">{n.fuente}</span>
        <span className={ETIQUETA_NOTA}>Al hacer clic</span>
        <span className="text-slate-700">{n.clic}</span>
        {n.ojo && (
          <>
            <span className={ETIQUETA_NOTA}>Ojo</span>
            <span className="text-slate-700">{n.ojo}</span>
          </>
        )}
        {detalle && (
          <>
            <span className={ETIQUETA_NOTA}>Cifras</span>
            <span className="tabular-nums text-slate-700">{detalle}</span>
          </>
        )}
      </div>
      {avisos && avisos.length > 0 && (
        <p className="mt-2.5 rounded-md bg-amber-50 px-2 py-1.5 text-[11px] leading-snug text-amber-800">
          {avisos.join(" ")}
        </p>
      )}
    </div>
  );
}

const CLASE_ETIQUETA =
  "flex items-center gap-1.5 whitespace-nowrap text-[10px] font-bold uppercase leading-3 tracking-wide";
const CLASE_CIFRA =
  "flex items-center gap-1 whitespace-nowrap text-sm font-bold leading-4 tabular-nums";
/** Los dos botones sueltos (Todo el catálogo y Costo validado): rectángulos
 *  sin recorte, así que el anillo de foco de siempre sí se ve entero. */
const CLASE_SUELTO =
  "box-border flex h-10 shrink-0 flex-col justify-center gap-px rounded-lg border px-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/60 focus-visible:ring-offset-1";

/* ── Las flechas ─────────────────────────────────────────────────────────────
   El recorte va en `clip-path` y no en un SVG de fondo: así el ÁREA DE CLIC es
   la flecha misma, y la punta de un paso se puede pulsar aunque caiga dentro
   de la caja de la siguiente (cada una se monta 10 px sobre la anterior para
   que la punta entre en la muesca). Por la misma razón la envoltura de la nota
   es `pointer-events-none`: su caja es rectangular y se comería esa punta.

   Y el recorte NUNCA va en un ancestro de la nota: `clip-path` recorta también
   a los descendientes `fixed`, y la nota se volvería invisible. Por eso en
   Destino —que tiene dos botones, cada uno con su nota— la flecha es un fondo
   aparte y los botones van encima, sin recortar. */
type Forma = "primera" | "media" | "ultima";

const PUNTA = 16;
const CLIP: Record<Forma, string> = {
  primera: `polygon(0 0, calc(100% - ${PUNTA}px) 0, 100% 50%, calc(100% - ${PUNTA}px) 100%, 0 100%)`,
  media: `polygon(0 0, calc(100% - ${PUNTA}px) 0, 100% 50%, calc(100% - ${PUNTA}px) 100%, 0 100%, ${PUNTA}px 50%)`,
  ultima: `polygon(0 0, 100% 0, 100% 100%, 0 100%, ${PUNTA}px 50%)`,
};
const RELLENO: Record<Forma, string> = {
  primera: "rounded-l-lg pl-3 pr-[22px]",
  media: "pl-[26px] pr-[22px]",
  ultima: "rounded-r-lg pl-[26px] pr-3.5",
};
/** Cada flecha se monta sobre la siguiente: la punta entra en la muesca. */
const MONTAJE: Record<Forma, string> = {
  primera: "-mr-2.5",
  media: "-mr-2.5",
  ultima: "",
};

/** El tinte de cada paso: el tono de su muestra del sello, en claro. */
const TINTE = {
  recibido: "#e0f2fe",
  bodega: "#d1fae5",
  destino: "#e0e7ff",
  restock: "#f1f5f9",
} as const;
const GRIS_POR_DEFINIR = "#94a3b8";
const TEXTO_PASO = "#334155";
const ETIQUETA_PASO = "#475569";

/** El número del paso. Va `aria-hidden`: quien lee con lector oye «Paso N»
 *  del texto oculto de al lado, no un «1» suelto pegado a la etiqueta. */
function NumeroPaso({ n, fondo, texto }: { n: number; fondo: string; texto: string }) {
  return (
    <>
      <span
        aria-hidden="true"
        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-extrabold leading-4"
        style={{ backgroundColor: fondo, color: texto }}
      >
        {n}
      </span>
      <span className="sr-only">{`Paso ${n}: `}</span>
    </>
  );
}

/** El anillo de foco de una flecha. El `outline` lo corta el `clip-path` —en la
 *  punta y en la muesca no se vería—, así que se dibuja ADENTRO, en la parte
 *  recta de la flecha, con el color del texto para que se vea también sobre el
 *  color del canal. */
function FocoFlecha({ forma }: { forma: Forma }) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute inset-y-1 hidden rounded-md border-2 border-current group-focus-visible:block"
      style={{
        left: forma === "primera" ? 4 : PUNTA + 4,
        right: forma === "ultima" ? 4 : PUNTA + 4,
      }}
    />
  );
}

interface PropsFlecha {
  forma: Forma;
  paso: number;
  etiqueta: string;
  tinte: string;
  cifraNodo: React.ReactNode;
  /** La nota al pasar el cursor: qué cuenta, de dónde sale y qué hace el clic. */
  nota: React.ReactNode;
  activo?: boolean;
  deshabilitado?: boolean;
  /** Restock: el número va en gris, como su muestra punteada. */
  porDefinir?: boolean;
  onClick?: () => void;
  color: string;
  textoColor: string;
}

/** Un paso de una sola lista. `activo` pinta el color del canal, como la
 *  pestaña; el número se invierte para seguir viéndose sobre él. */
function Flecha({
  forma, paso, etiqueta, tinte, cifraNodo, nota, activo, deshabilitado,
  porDefinir, onClick, color, textoColor,
}: PropsFlecha) {
  const numero = activo
    ? { fondo: textoColor, texto: color }
    : porDefinir
      ? { fondo: GRIS_POR_DEFINIR, texto: "#ffffff" }
      : { fondo: color, texto: textoColor };
  return (
    <PanelHover
      claro bloque ancho={340} alto={300} panel={nota}
      envoltura={`pointer-events-none flex shrink-0 ${MONTAJE[forma]}`}
    >
      <button
        type="button"
        disabled={deshabilitado}
        onClick={onClick}
        aria-pressed={activo}
        className={[
          "group pointer-events-auto relative flex h-14 flex-col justify-center gap-1 border-0 text-left transition-[filter] focus-visible:outline-none",
          RELLENO[forma],
          deshabilitado ? "cursor-not-allowed" : "cursor-pointer hover:brightness-95",
        ].join(" ")}
        style={{
          clipPath: CLIP[forma],
          backgroundColor: activo ? color : tinte,
          color: activo ? textoColor : TEXTO_PASO,
        }}
      >
        <FocoFlecha forma={forma} />
        <span
          className={CLASE_ETIQUETA}
          style={activo ? { color: textoColor, opacity: 0.85 } : { color: ETIQUETA_PASO }}
        >
          <NumeroPaso n={paso} {...numero} />
          {etiqueta}
        </span>
        <span className={CLASE_CIFRA} style={activo ? { fontWeight: 800 } : undefined}>
          {cifraNodo}
        </span>
      </button>
    </PanelHover>
  );
}

interface PropsDestino {
  etiqueta: string;
  cifraTexto: string;
  estiloCifra?: React.CSSProperties;
  nota: React.ReactNode;
  activo: boolean;
  deshabilitado: boolean;
  onClick: () => void;
  color: string;
  textoColor: string;
}

/** Uno de los dos destinos DENTRO de la flecha 3. Es un botón propio, con su
 *  nota y su `aria-pressed`: son dos listas distintas y la flecha no elige. */
function BotonDestino({
  etiqueta, cifraTexto, estiloCifra, nota, activo, deshabilitado, onClick,
  color, textoColor,
}: PropsDestino) {
  return (
    <PanelHover
      claro bloque ancho={340} alto={300} panel={nota}
      envoltura="pointer-events-auto flex"
    >
      <button
        type="button"
        disabled={deshabilitado}
        onClick={onClick}
        aria-pressed={activo}
        className={[
          "flex items-baseline gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-semibold leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600 focus-visible:ring-offset-1",
          deshabilitado ? "cursor-not-allowed" : "cursor-pointer hover:bg-white/70",
        ].join(" ")}
        style={activo ? { backgroundColor: color, color: textoColor } : { color: TEXTO_PASO }}
      >
        {etiqueta}
        {/* `estiloCifra` va AL FINAL y gana también activo, igual que en la
            Flecha: con filtros la cifra no describe la lista de abajo, y el
            paso pulsado no la vuelve verdad. Se apaga en vez de mentir. */}
        <span
          className="text-sm font-bold tabular-nums"
          style={{
            ...(activo ? { color: textoColor, fontWeight: 800 } : { color: "#0f172a" }),
            ...(estiloCifra ?? {}),
          }}
        >
          {cifraTexto}
        </span>
      </button>
    </PanelHover>
  );
}

export default function FlujoEtapas({
  conteos, etapa, onEtapa, revisado, onRevisado, totalTodas,
  atenuar, atenuarCarril, canal, color, textoColor,
}: Props) {
  const porClave = useMemo(() => {
    const m = new Map<ClaveFlujo, EtapaCanalFlujo>();
    conteos?.etapas.forEach((e) => m.set(e.clave, e));
    return m;
  }, [conteos]);

  // Sin respuesta todavía la cifra dice «…» y no «—»: el dato viene en camino,
  // no falta. Es la misma distinción del resto de la barra.
  const pendiente = !conteos || conteos.estado === "calentando";
  const aprox = !!conteos?.unidad_aprox;
  // «Todo el catálogo» en General NO sale del conteo —ahí `total` viaja en
  // null— sino del total de la propia lista, que es EXACTO y es el mismo número
  // que enseña el hero. Marcarlo «≈» lo ponía a contradecir a la cifra grande.
  const aproxTodas = aprox && conteos?.total != null;
  // Shein se pinta con datos de ejemplo: no hay SKU real que cruzar, así que ni
  // el esqueleto ofrece En DROP ni el carril.
  const puedeFiltrar = CANALES_CON_ETAPA.has(canal);
  const vencida = fotoVencida(conteos);
  const hora = horaCdmx(conteos?.generado);

  function numero(e: EtapaCanalFlujo | null): string {
    const n = e?.n ?? null;
    if (n === null) return pendiente ? "…" : "—";
    return cifra(n, aprox);
  }

  /** Sin conteos mandan las reglas mínimas: En DROP y el carril no dependen de
   *  la foto (Odoo y costos se leen en vivo), así que sobreviven al esqueleto. */
  function clicable(e: EtapaCanalFlujo | null, porOmision: boolean): boolean {
    return conteos ? !!e?.clicable : porOmision;
  }

  /** Lo que depende del momento y la nota no puede decir de antemano. `fija`
   *  = paso por definir: el backend manda en `motivo` y `n_motivo` el MISMO
   *  texto que la nota ya explica, y se repetía. */
  function avisos(e: EtapaCanalFlujo | null, fija = false): string[] {
    const partes: string[] = [];
    if (!fija && e && (e.n === null || e.n === undefined)) {
      if (e.n_motivo) partes.push(e.n_motivo);
      else if (pendiente) partes.push("La foto del flujo se está armando.");
    }
    if (!fija && e && !e.clicable && e.motivo) partes.push(e.motivo);
    if (e?.vieja || vencida) partes.push(`Cifra de la foto de las ${hora}, vencida.`);
    if (atenuar) partes.push(NOTA_ATENUAR);
    return Array.from(new Set(partes.filter(Boolean)));
  }

  /** Clic en el paso activo = volver a todo el catálogo. Un segundo clic no
   *  puede dejar la vista en el mismo sitio sin manera obvia de salir. */
  function elegir(e: EtapaOmnicanal) {
    onEtapa(etapa === e ? null : e);
  }

  const estiloCifra = atenuar ? { color: "#94a3b8" } : undefined;
  const tema = { color, textoColor };

  const eRecibido = porClave.get("recibido") ?? null;
  const eBodega = porClave.get("bodega_3de4") ?? null;
  // LA BODEGA DEL MARKETPLACE ES DE CADA CANAL. El backend manda en `etapas`
  // la que aplica —`en_full` en General y ML, `en_fba` en Amazon— y NINGUNA en
  // los canales que no tienen (TikTok y Temu despachan de nuestro almacén, y de
  // Walmart WFS no hay dato). Aquí solo se pinta lo que llegó: adivinarlo sería
  // ofrecer un filtro que el servidor no sabe aplicar.
  const claveMarketplace: EtapaOmnicanal | null =
    porClave.has("en_full") ? "en_full" : porClave.has("en_fba") ? "en_fba" : null;
  const eMarketplace = claveMarketplace ? porClave.get(claveMarketplace) ?? null : null;
  const eDrop = porClave.get("en_drop") ?? null;
  const eRestock = porClave.get("restock") ?? null;
  const carril = conteos?.carril ?? null;

  // «En FULL» es de Mercado Libre: en General —que mezcla canales— hay que
  // decirlo o la cifra se lee como la bodega de cualquiera.
  const etiquetaMarketplace = eMarketplace
    ? (claveMarketplace === "en_full" && canal === "general" ? "En FULL (ML)" : eMarketplace.titulo)
    : "";

  const ayudaRecibido = conteos?.catalogo?.recibido_fuera_de_odoo != null
    && conteos?.catalogo?.recibido != null
    ? `En todo el catálogo, ${cifra(conteos.catalogo.recibido_fuera_de_odoo)} de los `
      + `${cifra(conteos.catalogo.recibido)} no existen en Odoo activo.`
    : "";

  const tituloRotulo =
    `Flujo del SKU, de Recibido a Restock: ${textoUnidad(conteos)}. `
    + (conteos?.generado
      ? `Foto de las ${hora} (hora de CDMX); se rearma cada ${Math.round((conteos.ttl_s ?? 1800) / 60)} min.`
      : "La foto del flujo todavía no está lista.")
    + (conteos?.criterio_efectivo && conteos.criterio_efectivo !== conteos.criterio
      ? ` Este canal no sabe filtrar «${conteos.criterio}»: se contó con «${conteos.criterio_efectivo}».`
      : "");

  const carrilDeshabilitado = conteos ? !carril?.clicable : !puedeFiltrar;

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-x-3 gap-y-2">
      {/* Rótulo: qué se está contando y de cuándo es el dato. */}
      <div title={tituloRotulo} className="flex shrink-0 flex-col justify-center gap-0.5">
        <span className="whitespace-nowrap text-xs font-semibold uppercase tracking-wide text-slate-400">
          Flujo del SKU
        </span>
        <span className="flex items-center gap-1.5 whitespace-nowrap text-[10px] leading-3 text-slate-400">
          <span className="flex items-center gap-[3px]">
            {FUENTES.map(({ k, t }) => {
              const f = conteos?.fuentes?.[k];
              // NO se pinta rojo mientras nadie la haya leído. El backend manda
              // SIEMPRE las cuatro llaves, también sin foto, y ahí la fuente
              // llega `ok:false` con `error` y `generado` en null: eso no es
              // «no respondió», es «no se le preguntó». Mirar solo `ok` dejaba
              // los tres puntos en rojo justo mientras la foto se arma.
              const estado = !f || (!f.ok && !f.error && !f.generado)
                ? "pendiente"
                : !f.ok ? "caida" : f.vieja ? "vieja" : "ok";
              const fondo = estado === "ok" ? "#10b981"
                : estado === "vieja" ? "#f59e0b"
                  : estado === "caida" ? "#f87171" : "#cbd5e1";
              const ayudaFuente = estado === "ok" ? `${t} respondió`
                : estado === "vieja" ? `${t}: dato vencido`
                  : estado === "caida" ? `${t} no respondió` : `${t}: sin leer todavía`;
              return (
                <span
                  key={k}
                  title={ayudaFuente}
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: fondo }}
                />
              );
            })}
          </span>
          <span
            className="tabular-nums"
            style={vencida ? { color: "#b45309" } : undefined}
          >
            foto {conteos?.generado ? hora : "—"}
          </span>
        </span>
      </div>

      {/* Todo el catálogo — siempre pulsable: es la SALIDA del filtro, no un
          paso, y por eso vive fuera de las flechas. No se pinta con el color
          del canal ni cuando no hay etapa puesta: ese color significa «hay un
          filtro aplicado». */}
      <PanelHover
        claro bloque envoltura="flex shrink-0" ancho={340} alto={300}
        panel={<Nota clave="todas" titulo="Todo el catálogo" avisos={avisos(null)} />}
      >
        <button
          type="button"
          onClick={() => onEtapa(null)}
          className={`${CLASE_SUELTO} cursor-pointer border-slate-200 bg-white hover:brightness-95`}
          style={{ color: TEXTO_PASO }}
        >
          <span className={CLASE_ETIQUETA} style={{ color: "#94a3b8" }}>
            Todo el catálogo
          </span>
          <span className={CLASE_CIFRA}>
            <span style={estiloCifra}>
              {totalTodas === null ? (pendiente ? "…" : "—") : cifra(totalTodas, aproxTodas)}
            </span>
          </span>
        </button>
      </PanelHover>

      {/* Las flechas miden ~800 px y la fila envuelve: el desplazamiento es
          SUYO, para que a 400 px no se desplace la página entera. */}
      <div className="max-w-full overflow-x-auto">
        <div
          role="group"
          aria-label="Pasos del flujo, de Recibido a Restock"
          className="flex w-max items-center"
        >
          <Flecha
            {...tema}
            forma="primera"
            paso={1}
            etiqueta="Recibido"
            tinte={TINTE.recibido}
            cifraNodo={
              <>
                <span style={estiloCifra}>{numero(eRecibido)}</span>
                <Pildora texto="aprox." clase="bg-sky-50 text-sky-700 ring-1 ring-sky-200" />
              </>
            }
            nota={
              <Nota
                clave="recibido" titulo="Recibido"
                muestra={<MuestraChip m={MUESTRA_FLUJO.recibido} />}
                pildora={<Pildora texto="aprox." clase="bg-sky-50 text-sky-700 ring-1 ring-sky-200" />}
                detalle={ayudaRecibido} avisos={avisos(eRecibido)}
              />
            }
            activo={etapa === "recibido"}
            deshabilitado={!clicable(eRecibido, false)}
            onClick={() => elegir("recibido")}
          />

          <Flecha
            {...tema}
            forma="media"
            paso={2}
            etiqueta="Validado bodega"
            tinte={TINTE.bodega}
            cifraNodo={
              <>
                <span style={estiloCifra}>{numero(eBodega)}</span>
                <Pildora texto="3 de 4 sin specs" clase="bg-amber-50 text-amber-700 ring-1 ring-amber-200" />
              </>
            }
            nota={
              <Nota
                clave="bodega_3de4" titulo="Validado bodega" muestra={<MuestraBodega />}
                pildora={<Pildora texto="3 de 4 sin specs" clase="bg-amber-50 text-amber-700 ring-1 ring-amber-200" />}
                avisos={avisos(eBodega)}
              />
            }
            activo={etapa === "bodega_3de4"}
            deshabilitado={!clicable(eBodega, false)}
            onClick={() => elegir("bodega_3de4")}
          />

          {/* 3 · Destino: DOS listas en una sola flecha, separadas por «o». No
              son pasos seguidos sino los dos caminos de salida del SKU. */}
          <div
            role="group"
            aria-label="Paso 3: Destino"
            className={`pointer-events-none relative flex h-14 shrink-0 flex-col justify-center gap-0.5 ${RELLENO.media} ${MONTAJE.media}`}
          >
            <span
              aria-hidden="true"
              className="absolute inset-0"
              style={{ clipPath: CLIP.media, backgroundColor: TINTE.destino }}
            />
            {/* El rótulo lo dice ya el `aria-label` del grupo: aquí se oculta
                para que el lector no lo lea dos veces. */}
            <span aria-hidden="true" className={`relative ${CLASE_ETIQUETA}`} style={{ color: ETIQUETA_PASO }}>
              <NumeroPaso n={3} fondo={color} texto={textoColor} />
              Destino
            </span>
            <span className="relative -ml-1.5 flex items-center gap-1">
              {claveMarketplace && eMarketplace && (
                <>
                  <BotonDestino
                    {...tema}
                    etiqueta={etiquetaMarketplace}
                    cifraTexto={numero(eMarketplace)}
                    estiloCifra={estiloCifra}
                    nota={
                      <Nota
                        clave={claveMarketplace} titulo={etiquetaMarketplace}
                        muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />} avisos={avisos(eMarketplace)}
                      />
                    }
                    activo={etapa === claveMarketplace}
                    deshabilitado={!clicable(eMarketplace, false)}
                    onClick={() => elegir(claveMarketplace)}
                  />
                  <span aria-hidden="true" className="text-[11px] font-semibold text-slate-400">o</span>
                </>
              )}
              <BotonDestino
                {...tema}
                etiqueta="En DROP"
                cifraTexto={numero(eDrop)}
                estiloCifra={estiloCifra}
                nota={
                  <Nota
                    clave="en_drop" titulo="En DROP"
                    muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />} avisos={avisos(eDrop)}
                  />
                }
                activo={etapa === "en_drop"}
                deshabilitado={!clicable(eDrop, puedeFiltrar)}
                onClick={() => elegir("en_drop")}
              />
            </span>
          </div>

          <Flecha
            {...tema}
            forma="ultima"
            paso={4}
            etiqueta="Restock"
            tinte={TINTE.restock}
            porDefinir
            cifraNodo={<span className="text-xs font-semibold leading-4 text-slate-500">por definir</span>}
            nota={
              <Nota
                clave="restock" titulo="Restock"
                muestra={<MuestraChip m={MUESTRA_FLUJO.restock} />}
                pildora={<Pildora texto="por definir" clase="bg-slate-100 text-slate-500" />}
                avisos={avisos(eRestock, true)}
              />
            }
            deshabilitado
          />
        </div>
      </div>

      {/* Costo validado va APARTE de las flechas: corre en paralelo al camino,
          no es un paso de él, y lo dice debajo. Se suma con AND a la etapa. */}
      <div className="flex shrink-0 flex-col gap-0.5">
        <PanelHover
          claro bloque envoltura="flex" ancho={340} alto={300}
          panel={
            <Nota
              clave="costo_validado" titulo="Costo validado"
              muestra={<BadgeCheck size={12} className="shrink-0" color="#059669" />}
              pildora={<Pildora texto="aparte" clase="bg-slate-100 text-slate-500" />}
              avisos={[
                carril?.n_motivo ?? "",
                carril?.vieja || vencida ? `Cifra de la foto de las ${hora}, vencida.` : "",
                atenuarCarril ? NOTA_CARRIL : (atenuar ? NOTA_ATENUAR : ""),
              ].filter(Boolean)}
            />
          }
        >
          <button
            type="button"
            disabled={carrilDeshabilitado}
            onClick={() => onRevisado(!revisado)}
            aria-pressed={revisado}
            className={[
              CLASE_SUELTO,
              revisado ? "border-transparent" : "border-slate-200 bg-white",
              carrilDeshabilitado ? "cursor-not-allowed" : "cursor-pointer hover:brightness-95",
            ].join(" ")}
            style={revisado ? { backgroundColor: color, color: textoColor } : { color: TEXTO_PASO }}
          >
            <span
              className={CLASE_ETIQUETA}
              style={revisado ? { color: textoColor, opacity: 0.8 } : { color: "#94a3b8" }}
            >
              <BadgeCheck size={12} className="shrink-0" color={revisado ? textoColor : "#059669"} />
              Costo validado
            </span>
            <span className={CLASE_CIFRA}>
              <span style={atenuarCarril || atenuar ? { color: revisado ? textoColor : "#94a3b8" } : undefined}>
                {carril?.n === null || carril?.n === undefined
                  ? (pendiente ? "…" : "—")
                  : cifra(carril.n, aprox)}
              </span>
              <Pildora texto="aparte" clase="bg-slate-100 text-slate-500" />
            </span>
          </button>
        </PanelHover>
        <span className="whitespace-nowrap text-[10px] leading-3 text-slate-400">
          carril aparte · no es un paso
        </span>
      </div>
    </div>
  );
}
