"use client";

/* El STEPPER de etapas de /omnicanal: el camino del SKU, con la cifra de ESTA
   pestaña y ESTA cuenta en cada paso.

   Sustituye a los chips «Costo validado» y «Solo DROP OFF». Tres reglas que
   vienen del backend y no se recalculan aquí:
   · el NÚMERO y el CLIC son independientes (`n` / `clicable`): que el conteo
     por canal se caiga no tiene por qué apagar el filtro de Recibido;
   · `n: null` se pinta «—» (o «…» mientras la foto se arma), NUNCA 0;
   · qué se puede abrir lo dice `clicable`. Si el frontend lo adivinara, un día
     ofrecería un filtro que el servidor no sabe aplicar. */

import { useMemo } from "react";
import { BadgeCheck, ChevronRight, Lock } from "lucide-react";

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
  /** El «Todas» del stepper. `null` cuando no se puede afirmar. */
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

/** La misma muestra del sello, a 12×8. El stepper es la leyenda de los colores. */
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

function Chevron() {
  return (
    <span className="flex items-center px-px text-slate-300" aria-hidden="true">
      <ChevronRight size={12} />
    </span>
  );
}

const ETIQUETA_NOTA = "text-[10px] font-semibold uppercase tracking-wide text-slate-400";

/** La nota de una pestaña. Mismo molde que la nota de la regla de precios:
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

const CLASE_SEG =
  "flex flex-col justify-center gap-px border-0 px-2.5 text-left transition-colors";
const CLASE_ETIQUETA =
  "flex items-center gap-1 whitespace-nowrap text-[10px] font-semibold uppercase leading-3 tracking-wide";
const CLASE_CIFRA =
  "flex items-center gap-1 whitespace-nowrap text-sm font-bold leading-4 tabular-nums";

interface PropsSeg {
  etiqueta: string;
  muestra?: React.ReactNode;
  cifraNodo: React.ReactNode;
  /** La nota al pasar el cursor: qué cuenta, de dónde sale y qué hace el clic. */
  nota: React.ReactNode;
  activo?: boolean;
  deshabilitado?: boolean;
  onClick?: () => void;
  /** Fondo propio (bloqueado, por definir). El activo manda sobre él. */
  fondo?: string;
  texto?: string;
  borde?: "izq" | "der";
  color: string;
  textoColor: string;
}

/** Un segmento del grupo. `activo` pinta el color del canal, como la pestaña. */
function Seg({
  etiqueta, muestra, cifraNodo, nota, activo, deshabilitado, onClick,
  fondo, texto, borde, color, textoColor,
}: PropsSeg) {
  const estilo: React.CSSProperties = activo
    ? { backgroundColor: color, color: textoColor, paddingLeft: 12, paddingRight: 12 }
    : { backgroundColor: fondo ?? "#ffffff", color: texto ?? "#334155" };
  // La nota va AFUERA del botón: un `title` nativo tarda en salir, pinta texto
  // corrido y el equipo no lo encontraba. El panel sale al instante y trae las
  // cuatro preguntas de cada pestaña.
  return (
    <PanelHover claro bloque envoltura="flex" ancho={340} alto={300} panel={nota}>
    <button
      type="button"
      disabled={deshabilitado}
      onClick={onClick}
      aria-pressed={activo}
      className={[
        CLASE_SEG,
        borde === "izq" ? "border-l border-slate-200" : "",
        borde === "der" ? "border-r border-slate-200" : "",
        deshabilitado ? "cursor-not-allowed" : "cursor-pointer hover:brightness-95",
      ].join(" ")}
      style={estilo}
    >
      <span
        className={CLASE_ETIQUETA}
        style={activo ? { color: textoColor, opacity: 0.8 } : { color: texto ?? "#94a3b8" }}
      >
        {muestra}
        {etiqueta}
      </span>
      <span className={CLASE_CIFRA} style={activo ? { fontWeight: 800 } : undefined}>
        {cifraNodo}
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
  // «Todas» en General NO sale del conteo —ahí `total` viaja en null— sino del
  // total de la propia lista, que es EXACTO y es el mismo número que enseña el
  // hero. Marcarlo «≈» lo ponía a contradecir a la cifra grande de arriba.
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
   *  = etapa bloqueada o por definir: el backend manda en `motivo` y `n_motivo`
   *  el MISMO texto que la nota ya explica, y se repetía. */
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

  /** Clic en la etapa activa = volver a Todas. Un segundo clic no puede dejar
   *  la vista en el mismo sitio sin manera obvia de salir. */
  function elegir(e: EtapaOmnicanal) {
    onEtapa(etapa === e ? null : e);
  }

  const estiloCifra = atenuar ? { color: "#94a3b8" } : undefined;
  const tema = { color, textoColor };

  const eRecibido = porClave.get("recibido") ?? null;
  const eBodega = porClave.get("bodega_3de4") ?? null;
  const eListo = porClave.get("listo_envio") ?? null;
  const eFull = porClave.get("en_full") ?? null;
  const eDrop = porClave.get("en_drop") ?? null;
  const eRestock = porClave.get("restock") ?? null;
  const carril = conteos?.carril ?? null;

  // «En FULL» es de Mercado Libre: en las otras pestañas hay que decirlo o la
  // cifra se lee como FBA/WFS, que no entran.
  const etiquetaFull =
    canal === "mercado_libre" || canal === "general" ? "En FULL" : "En FULL (ML)";

  const ayudaListo = [
    eListo?.n_sin_specs != null
      ? `Si specs no bloqueara, en esta cuenta: ${cifra(eListo.n_sin_specs, aprox)}.`
      : "",
    conteos?.catalogo?.recibido_y_3de4 != null
      ? `En todo el catálogo: ${cifra(conteos.catalogo.recibido_y_3de4)}.`
      : "",
  ].filter(Boolean).join(" ");

  const ayudaRecibido = conteos?.catalogo?.recibido_fuera_de_odoo != null
    && conteos?.catalogo?.recibido != null
    ? `En todo el catálogo, ${cifra(conteos.catalogo.recibido_fuera_de_odoo)} de los `
      + `${cifra(conteos.catalogo.recibido)} no existen en Odoo activo.`
    : "";

  const tituloRotulo =
    `Etapas del flujo por SKU: ${textoUnidad(conteos)}. `
    + (conteos?.generado
      ? `Foto de las ${hora} (hora de CDMX); se rearma cada ${Math.round((conteos.ttl_s ?? 1800) / 60)} min.`
      : "La foto del flujo todavía no está lista.")
    + (conteos?.criterio_efectivo && conteos.criterio_efectivo !== conteos.criterio
      ? ` Este canal no sabe filtrar «${conteos.criterio}»: se contó con «${conteos.criterio_efectivo}».`
      : "");

  return (
    <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
      {/* Rótulo: qué se está contando y de cuándo es el dato. */}
      <div title={tituloRotulo} className="flex flex-col justify-center gap-0.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Etapa
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

      {/* El grupo mide ~900 px y la fila envuelve: el desplazamiento es SUYO,
          para que a 400 px no se desplace la página entera. */}
      <div className="max-w-full overflow-x-auto">
        <div
          role="group"
          aria-label="Etapa del flujo"
          className="inline-flex h-[38px] items-stretch overflow-hidden rounded-lg border border-slate-200 bg-white"
        >
          {/* Todas — siempre pulsable: es la SALIDA del filtro, no una etapa.
              Por eso no se pinta con el color del canal ni cuando no hay etapa
              puesta: ese color significa «hay un filtro aplicado». */}
          <Seg
            {...tema}
            etiqueta="Todas"
            cifraNodo={
              <span style={estiloCifra}>
                {totalTodas === null ? (pendiente ? "…" : "—") : cifra(totalTodas, aproxTodas)}
              </span>
            }
            nota={<Nota clave="todas" titulo="Todas" avisos={avisos(null)} />}
            onClick={() => onEtapa(null)}
            borde="der"
          />

          <Seg
            {...tema}
            etiqueta="Recibido"
            muestra={<MuestraChip m={MUESTRA_FLUJO.recibido} />}
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

          <Chevron />

          <Seg
            {...tema}
            etiqueta="Validado bodega"
            muestra={<MuestraBodega />}
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

          <Chevron />

          {/* Listo: 0 por construcción mientras specs no tenga definición. No se
              deshabilita «porque no hay dato» — se deshabilita porque no hay
              lista que filtrar, y el title lo dice. */}
          <Seg
            {...tema}
            etiqueta="Listo para FULL o DROP"
            muestra={<MuestraChip m={MUESTRA_FLUJO.listo} />}
            cifraNodo={
              <span className="flex items-center gap-1 text-xs font-bold leading-4">
                <Lock size={12} /> bloqueado
              </span>
            }
            nota={
              <Nota
                clave="listo_envio" titulo="Listo para FULL o DROP"
                muestra={<MuestraChip m={MUESTRA_FLUJO.listo} />}
                pildora={<Pildora texto="bloqueado" clase="bg-amber-50 text-amber-700 ring-1 ring-amber-200" />}
                detalle={ayudaListo} avisos={avisos(eListo, true)}
              />
            }
            deshabilitado
            fondo="#fffbeb"
            texto="#b45309"
          />

          <Chevron />

          <Seg
            {...tema}
            etiqueta={etiquetaFull}
            muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />}
            cifraNodo={<span style={estiloCifra}>{numero(eFull)}</span>}
            nota={
              <Nota
                clave="en_full" titulo={etiquetaFull}
                muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />} avisos={avisos(eFull)}
              />
            }
            activo={etapa === "en_full"}
            deshabilitado={!clicable(eFull, false)}
            onClick={() => elegir("en_full")}
          />

          {/* En DROP no lleva chevron: es el OTRO destino, no el paso siguiente. */}
          <Seg
            {...tema}
            etiqueta="En DROP"
            muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />}
            cifraNodo={<span style={estiloCifra}>{numero(eDrop)}</span>}
            nota={
              <Nota
                clave="en_drop" titulo="En DROP"
                muestra={<MuestraChip m={MUESTRA_FLUJO.destino} />} avisos={avisos(eDrop)}
              />
            }
            activo={etapa === "en_drop"}
            deshabilitado={!clicable(eDrop, puedeFiltrar)}
            onClick={() => elegir("en_drop")}
            borde="izq"
          />

          <Chevron />

          <Seg
            {...tema}
            etiqueta="Restock"
            muestra={<MuestraChip m={MUESTRA_FLUJO.restock} />}
            cifraNodo={<span className="text-xs font-semibold leading-4">por definir</span>}
            nota={
              <Nota
                clave="restock" titulo="Restock"
                muestra={<MuestraChip m={MUESTRA_FLUJO.restock} />}
                pildora={<Pildora texto="por definir" clase="bg-slate-100 text-slate-500" />}
                avisos={avisos(eRestock, true)}
              />
            }
            deshabilitado
            fondo="#f8fafc"
            texto="#94a3b8"
          />
        </div>
      </div>

      {/* Costo validado va APARTE del grupo: corre en paralelo al camino, no es
          un paso de él. Se suma con AND a la etapa. */}
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
        disabled={conteos ? !carril?.clicable : !puedeFiltrar}
        onClick={() => onRevisado(!revisado)}
        aria-pressed={revisado}
        className={[
          "box-border flex h-[38px] flex-col justify-center gap-px rounded-lg border px-2.5 text-left transition-colors",
          revisado ? "border-transparent" : "border-slate-200 bg-white",
          (conteos ? !carril?.clicable : !puedeFiltrar) ? "cursor-not-allowed" : "cursor-pointer hover:brightness-95",
        ].join(" ")}
        style={revisado ? { backgroundColor: color, color: textoColor } : { color: "#334155" }}
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
    </div>
  );
}
