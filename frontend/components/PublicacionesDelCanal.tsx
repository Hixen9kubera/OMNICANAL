"use client";

/**
 * PublicacionesDelCanal — el precio vigente y el margen de CADA publicación,
 * dentro de la tarjeta del canal en el cajón del producto.
 *
 * POR QUÉ ESTÁ AQUÍ Y NO EN UNA PESTAÑA APARTE (Eduardo, 24-ago). La decisión
 * —¿subo el precio?, ¿bajo esta publicación?— se toma mirando UN producto. Una
 * tabla en otra pantalla parte la información en dos lugares y obliga a
 * cruzarla de memoria.
 *
 * POR QUÉ POR PUBLICACIÓN Y NO POR SKU. Un mismo SKU vive varias veces en el
 * mismo canal y a precios distintos: `ACC-0001-AZL` tiene dos de Mercado Libre,
 * una en $382.00 pausada y otra en $229.00 activa. Un solo renglón por canal
 * tendría que elegir una y callar la otra — y la que calla puede ser la que
 * está vendiendo.
 *
 * LO QUE ESTE ARCHIVO NO HACE. No calcula. Estado, oferta y margen llegan
 * resueltos de `GET /api/publicaciones` (`services/publicaciones_panel.py`),
 * que es donde vive el criterio de cada canal. Aquí se eligen palabras y
 * colores; `lib/publicaciones.ts` pone el vocabulario y `lib/margen.ts` la
 * regla de cuándo un costo no se puede creer.
 */

import { useState, type ReactNode } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, ChevronDown, ExternalLink, Info, RefreshCw, Tag, Target } from "lucide-react";

import PanelHover from "@/components/PanelHover";
import { costoImplausible, avisoCostoImplausible } from "@/lib/margen";
import { enlacePublicacion } from "@/lib/enlaces";
import {
  ESTADO_UI,
  MOTIVO_CORTO,
  MOTIVO_TEXTO,
  OFERTA_DIAS_AMBAR,
  OFERTA_UI,
  fmtAntiguedad,
  fmtMoneda,
  fmtPct,
  fmtPctFirmado,
  labelTienda,
} from "@/lib/publicaciones";
import type { MargenMotivo, Publicacion } from "@/lib/types";

/** Chip del estado NORMALIZADO. El crudo solo cuando nadie lo mapeó. */
function ChipEstado({ p }: { p: Publicacion }) {
  const ui = ESTADO_UI[p.estado] ?? ESTADO_UI.desconocido;
  const clases: Record<string, string> = {
    vivo: "bg-emerald-50 text-emerald-700 border-emerald-200",
    vivo_matizado: "bg-emerald-50 text-emerald-700 border-emerald-200",
    ambar: "bg-amber-50 text-amber-800 border-amber-300",
    gris: "bg-slate-100 text-slate-600 border-slate-200",
  };
  return (
    <span className="flex items-center gap-1">
      <span
        title={ui.ayuda}
        className={[
          "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold",
          clases[ui.tono],
        ].join(" ")}
      >
        {ui.label}
        {/* El asterisco NO es decorativo: dice que el canal no distingue. */}
        {ui.tono === "vivo_matizado" && <span aria-hidden>*</span>}
      </span>
      {p.estado === "desconocido" && p.estado_crudo && (
        <span className="font-mono text-[10px] text-slate-500">{p.estado_crudo}</span>
      )}
    </span>
  );
}

/**
 * El precio que ESTA publicación cobra hoy.
 *
 * Tres estados de oferta, no dos. Solo `con_oferta` tacha el de lista y pinta
 * el descuento; `desconocida` NO dice "sin oferta" —nadie ha preguntado— y por
 * eso lleva su propia etiqueta gris con la explicación en el tooltip.
 */
function Precio({ p }: { p: Publicacion }) {
  const oferta = OFERTA_UI[p.oferta_estado];

  if (p.oferta_estado === "con_oferta") {
    // La antigüedad es OBLIGATORIA al lado de la oferta: quien la escribe
    // (`precios_venta.py`) está dormido, y una oferta sin fecha se lee como de
    // hoy cuando puede llevar días muerta.
    const vieja = p.oferta_dias === null || p.oferta_dias > OFERTA_DIAS_AMBAR;
    return (
      <div className="leading-tight">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-xs text-slate-400 line-through tabular-nums">
            {fmtMoneda(p.precio_lista, p.moneda)}
          </span>
          <span className="text-base font-black tabular-nums text-slate-900">
            {fmtMoneda(p.precio_vigente, p.moneda)}
          </span>
          {p.oferta_desc_pct !== null && (
            <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-black text-rose-700">
              −{fmtPct(p.oferta_desc_pct, 0)}
            </span>
          )}
        </div>
        <div
          className={[
            "mt-1 flex items-center gap-1 text-[11px] font-semibold",
            vieja ? "text-amber-700" : "text-slate-500",
          ].join(" ")}
          title={
            p.oferta_vista_at
              ? `Promoción observada el ${new Date(p.oferta_vista_at).toLocaleString("es-MX")}.`
              : "El backend no mandó la fecha de la observación."
          }
        >
          {vieja && <AlertTriangle size={11} className="shrink-0" />}
          <Tag size={11} className="shrink-0" />
          oferta vista {fmtAntiguedad(p.oferta_dias)}
        </div>
      </div>
    );
  }

  return (
    <div className="leading-tight">
      <div className="text-base font-black tabular-nums text-slate-900">
        {fmtMoneda(p.precio_vigente, p.moneda)}
      </div>
      <div className="mt-1 text-[11px] text-slate-400" title={oferta.ayuda}>
        {oferta.label}
      </div>
    </div>
  );
}

/**
 * EL PISO DE RENTABILIDAD (Eduardo, 9-sep-2026). Aparece SOLO cuando la
 * publicación cae por debajo del 20% de margen sobre el precio; mientras esté
 * por encima, la tarjeta no dice nada.
 *
 * Se decidió como PISO y no como meta porque el catálogo ya vive arriba: la
 * fórmula de la casa apunta al 48% sobre el COSTO, que es ~20.7% sobre el
 * precio. Un sugerido que dijera "baja un poco" en cientos de tarjetas no se
 * leería en la única donde importa.
 *
 * Y TIENE DOS CARAS, que es lo que evita que haga daño. Medido el 9-sep: de
 * 498 publicaciones de ML evaluables solo 31 tienen el costo verificado, y
 * bajo el piso hay 14 con costo confiable contra 270 sin él. Esas 270 no
 * tienen un precio malo — tienen un costo del que no nos podemos fiar. A esas
 * el backend NO les manda precio (`precio_piso` viene null y `piso_aviso`
 * dice por qué): el renglón manda a revisar el costeo en vez de empujar a
 * subir el precio 20 veces por un dato de captura.
 */
/** Una etiqueta sobre la pista de la regla: dónde va (0–100) y qué dice. */
type Pieza = { p: number; nodo: ReactNode };

const TONO_REGLA = {
  rose: { big: "text-rose-600", sub: "text-rose-500" },
  amber: { big: "text-amber-700", sub: "text-amber-600" },
  emerald: { big: "text-emerald-700", sub: "text-slate-500" },
} as const;

/**
 * Hasta dos etiquetas sobre la misma pista sin encimarse: centradas si hay
 * aire, abiertas hacia fuera si están cerca, y en un solo renglón si ni así
 * caben (los dos marcadores pegados a una orilla). `aire` es la distancia, en
 * puntos de la escala, desde la que ya caben centradas.
 */
function ParEtiquetas({ piezas, top, aire }: { piezas: Pieza[]; top: string; aire: number }) {
  const base = `absolute ${top} flex items-baseline gap-1 whitespace-nowrap`;
  const centrada = (p: number) => (p < 12 ? "" : p > 88 ? "-translate-x-full" : "-translate-x-1/2");
  const orden = [...piezas].sort((x, y) => x.p - y.p);
  const sueltas = (
    <>
      {orden.map((x, i) => (
        <div key={i} className={`${base} ${centrada(x.p)}`} style={{ left: `${x.p}%` }}>
          {x.nodo}
        </div>
      ))}
    </>
  );
  if (orden.length < 2) return sueltas;
  const a = orden[0]!;
  const b = orden[1]!;
  if (b.p - a.p >= aire) return sueltas;
  if (a.p >= 20 && b.p <= 80) {
    return (
      <>
        <div className={`${base} -translate-x-full pr-1.5`} style={{ left: `${a.p}%` }}>{a.nodo}</div>
        <div className={`${base} pl-1.5`} style={{ left: `${b.p}%` }}>{b.nodo}</div>
      </>
    );
  }
  const medio = (a.p + b.p) / 2;
  return (
    <div
      className={`${base} gap-1.5 ${medio < 25 || medio > 75 ? "" : "-translate-x-1/2"}`}
      style={medio < 25 ? { left: "0%" } : medio > 75 ? { right: "0%" } : { left: `${medio}%` }}
    >
      {a.nodo}
      <span className="text-slate-300">·</span>
      {b.nodo}
    </div>
  );
}

/**
 * LA REGLA DE PRECIOS (Eduardo, 14-sep-2026) — la opción B del lienzo «Margen
 * a precio de mercado». Pone NUESTRO precio y el de la competencia sobre las
 * zonas de margen de ESTA publicación: rojo pierdes, ámbar menos del piso,
 * verde el piso o más. Se lee sin leer números: basta ver en qué color cae
 * cada marcador.
 *
 * SE PINTA SIEMPRE QUE HAYA MEDIANA. Hasta v0.508.0 solo salía cuando nuestro
 * costo ya la superaba, en una línea gris de 11 px dentro del aviso del piso:
 * en la mayoría de las tarjetas no aparecía, y cuando el mercado vende MÁS caro
 * que nosotros —la señal para subir el precio— no aparecía nunca.
 *
 * Lo que sigue igual, y por eso va en el tooltip: la mediana es por TÉRMINO de
 * búsqueda, no por producto exacto (`_adjuntar_mercado`). Es una referencia, no
 * un precio sugerido. Por la misma razón, con el costo SIN verificar las
 * fronteras se dibujan pero sin monto: "20 % · $4,209" sobre un costo mal
 * capturado es justo el precio sugerido que `Piso` se niega a dar.
 */
function ReglaMercado({ p }: { p: Publicacion }) {
  const m = p.mercado;
  if (!m) return null;

  const objetivo = p.piso_objetivo ?? 0.2;
  const meta = `${Math.round(objetivo * 100)} %`;
  const margen = m.margen_pct ?? null;
  const eq = m.precio_equilibrio ?? null;
  const piso = m.precio_para_piso ?? null;
  const tu = p.precio_vigente;
  const verificado = Boolean(p.revisado_at);
  const dudoso = margen !== null && costoImplausible(p.precio_vigente, p.costo_unitario, p.revisado_at);
  const conZonas = margen !== null && eq !== null && piso !== null;

  // EL PROMEDIO MANDA (Eduardo, 14-sep-2026): el precio de mercado es la media
  // de la búsqueda, no la mediana. `mediana` sigue llegando y es el respaldo
  // mientras conteste un backend anterior a v0.518.0.
  const precioMercado = m.promedio ?? m.mediana;
  const estadistico = m.promedio != null ? "promedio" : "mediana";
  const cuando = m.dias == null ? null : m.dias === 0 ? "hoy" : `hace ${m.dias} d`;

  // La escala va de 0 al mayor de los cuatro precios, con aire a la derecha
  // para que el último marcador no quede pegado a la orilla.
  const tope = Math.max(precioMercado, tu ?? 0, eq ?? 0, piso ?? 0) * 1.12;
  const pos = (v: number) => Math.min(100, Math.max(0, (v / tope) * 100));
  const pMe = pos(precioMercado);
  const pTu = tu != null ? pos(tu) : null;

  const tono =
    margen === null ? null
      : dudoso ? TONO_REGLA.amber
        : margen < 0 ? TONO_REGLA.rose
          : margen < objetivo ? TONO_REGLA.amber
            : TONO_REGLA.emerald;

  const rel = tu ? tu / precioMercado - 1 : null;
  // "67 % abajo" — `null` cuando es el mismo precio (menos de medio punto).
  const relCorto =
    rel === null || Math.abs(rel) < 0.005 ? null
      : `${Math.round(Math.abs(rel) * 100)} % ${rel > 0 ? "arriba" : "abajo"}`;
  const relTexto =
    rel === null ? null : relCorto ? `tu precio está ${relCorto}` : "tu precio es el del mercado";

  // LA NOTA al dejar el cursor (Eduardo, 14-sep-2026) — la opción A del lienzo
  // «Nota de la regla de precios». Antes era un `title` nativo: cinco párrafos
  // de texto corrido en el globo del sistema. Ahora son bloques con título: qué
  // pasaría a precio de mercado, de dónde sale, tu precio y las zonas.
  const etiqueta = "text-[10px] font-semibold uppercase tracking-wide text-slate-400";
  const zonas: [string, string][] = [
    ["bg-rose-200", "pierdes"],
    ["bg-amber-200", `menos del ${meta}`],
    ["bg-emerald-200", `${meta} o más`],
  ];
  const nota = (
    <div>
      <div className={etiqueta}>Si vendieras al precio de mercado</div>
      {margen !== null && tono ? (
        <>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className={`inline-flex items-center gap-1 text-xl font-black tabular-nums ${tono.big}`}>
              {dudoso && <AlertTriangle size={15} className="shrink-0" />}
              {fmtPctFirmado(margen)}
            </span>
            <span className={`text-xs font-semibold tabular-nums ${tono.sub}`}>
              {fmtMoneda(m.ganancia_neta)} por venta
            </span>
          </div>
          <p className="text-[11px] leading-snug text-slate-500">
            Cobrando {fmtMoneda(precioMercado)}, con la misma comisión, envío y costo que el margen de hoy.
          </p>
        </>
      ) : (
        <p className="mt-0.5 text-[11px] leading-snug text-slate-500">
          Cobrando {fmtMoneda(precioMercado)}. El margen no se puede calcular:{" "}
          {MOTIVO_CORTO[(p.margen_motivo ?? "desconocido") as MargenMotivo] ?? "falta un dato del costeo"}.
        </p>
      )}

      <div className="my-2.5 h-px bg-slate-100" />

      <div className="grid grid-cols-[70px_minmax(0,1fr)] items-baseline gap-x-2.5 gap-y-2 text-[11px] leading-snug">
        <span className={etiqueta}>Mercado</span>
        <span className="text-slate-700">
          {estadistico === "promedio" ? "Promedio" : "Mediana"} de {m.n} publicaciones al buscar{" "}
          {m.termino ? (
            <span className="whitespace-nowrap rounded bg-indigo-50 px-1.5 py-px font-semibold text-indigo-700">
              {m.termino}
            </span>
          ) : (
            "el término de este SKU"
          )}
          {cuando ? ` · ${cuando}` : ""}
        </span>
        {tu != null && (
          <>
            <span className={etiqueta}>Tu precio</span>
            <span className="flex items-center gap-1 font-semibold tabular-nums text-slate-700">
              {relCorto && rel !== null && rel > 0 && <ArrowUp size={12} className="shrink-0 text-indigo-500" />}
              {relCorto && rel !== null && rel < 0 && <ArrowDown size={12} className="shrink-0 text-indigo-500" />}
              {fmtMoneda(tu, p.moneda)} · {relCorto ? `${relCorto} del mercado` : "igual al mercado"}
            </span>
          </>
        )}
        {conZonas && (
          <>
            <span className={etiqueta}>Zonas</span>
            <span className="flex flex-wrap gap-x-2.5 gap-y-1 text-slate-600">
              {zonas.map(([color, texto]) => (
                <span key={texto} className="flex items-center gap-1">
                  <span className={`h-2.5 w-2.5 rounded-[3px] ${color}`} />
                  {texto}
                </span>
              ))}
            </span>
          </>
        )}
        {conZonas && !verificado && (
          <>
            <span className={etiqueta}>Costo</span>
            <span className="text-amber-700">
              Sin verificar contra el packing list: por eso las fronteras no llevan precio.
            </span>
          </>
        )}
      </div>

      {dudoso && p.precio_vigente && p.costo_unitario ? (
        <p className="mt-2.5 rounded-md bg-amber-50 px-2 py-1.5 text-[10px] leading-relaxed text-amber-800">
          {avisoCostoImplausible(p.precio_vigente, p.costo_unitario)}
        </p>
      ) : null}

      <div className="mt-2.5 flex gap-1.5 rounded-md bg-slate-50 px-2 py-1.5 text-[10px] leading-relaxed text-slate-500">
        <Info size={12} className="mt-px shrink-0 text-slate-400" />
        <span>
          Es {estadistico === "promedio" ? "el promedio" : "la mediana"} de esa búsqueda, no de este producto: una
          referencia, no un precio sugerido. Si el
          término no describe el producto, corrígelo en Competencia.
        </span>
      </div>
    </div>
  );

  const marcadores: Pieza[] = [
    {
      p: pMe,
      nodo: (
        <>
          <span className="text-[10px] font-bold uppercase tracking-wide text-indigo-600">Mercado</span>
          <span className="text-xs font-black tabular-nums text-slate-900">{fmtMoneda(precioMercado)}</span>
        </>
      ),
    },
  ];
  if (pTu !== null) {
    marcadores.push({
      p: pTu,
      nodo: (
        <>
          <span className="text-[10px] font-bold uppercase tracking-wide text-slate-600">Tú</span>
          <span className="text-xs font-black tabular-nums text-slate-900">{fmtMoneda(tu, p.moneda)}</span>
        </>
      ),
    });
  }

  return (
    <PanelHover panel={nota} ancho={372} alto={330} claro bloque>
    <div className="mt-2.5 border-t border-slate-100 pt-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <div className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500">
            {margen !== null ? "A precio de mercado" : "Precio de mercado"}
          </span>
          {margen !== null && tono ? (
            <>
              <span className={`inline-flex items-center gap-1 text-base font-black tabular-nums ${tono.big}`}>
                {dudoso && <AlertTriangle size={13} className="shrink-0" />}
                {fmtPctFirmado(margen)}
              </span>
              <span className={`text-[11px] font-semibold tabular-nums ${tono.sub}`}>
                {dudoso ? "costo dudoso" : `${fmtMoneda(m.ganancia_neta)} por venta`}
              </span>
            </>
          ) : (
            <>
              <span className="text-base font-black tabular-nums text-slate-800">{fmtMoneda(precioMercado)}</span>
              {relTexto && <span className="text-[11px] font-semibold text-slate-500">{relTexto}</span>}
            </>
          )}
        </div>
        {/* EL TÉRMINO, a la vista (Eduardo, 14-sep): la mediana es tan buena
            como la búsqueda. «set de sartenes» mezclaba baterías de 25 piezas y
            subía el mercado de COC-0159-NEG a $964 con su competidor directo
            en $599; nombrado aquí, un término malo se nota sin abrir Competencia. */}
        <span className="text-[10px] text-slate-400">
          {estadistico} de {m.n}
          {m.termino && (
            <>
              {" · "}
              <span className="font-semibold text-slate-500">«{m.termino}»</span>
            </>
          )}
          {cuando ? ` · ${cuando}` : ""}
          {conZonas && !verificado ? " · costo sin verificar" : ""}
        </span>
      </div>

      <div className={`relative mt-1.5 ${conZonas ? "h-[60px]" : "h-[42px]"}`}>
        <ParEtiquetas piezas={marcadores} top="top-0" aire={22} />
        <div className="absolute inset-x-0 top-6 flex h-2 overflow-hidden rounded-full bg-slate-200">
          {conZonas && (
            <>
              <div className="bg-rose-200" style={{ width: `${pos(eq!)}%` }} />
              <div className="bg-amber-200" style={{ width: `${pos(piso!) - pos(eq!)}%` }} />
              <div className="flex-1 bg-emerald-200" />
            </>
          )}
        </div>
        <div
          className="absolute top-[17px] h-[22px] w-[3px] -translate-x-1/2 rounded-sm bg-indigo-600 ring-2 ring-white"
          style={{ left: `${pMe}%` }}
        />
        {pTu !== null && (
          <div
            className="absolute top-[17px] h-[22px] w-[3px] -translate-x-1/2 rounded-sm bg-slate-900 ring-2 ring-white"
            style={{ left: `${pTu}%` }}
          />
        )}
        {conZonas && (
          <ParEtiquetas
            top="top-[44px]"
            aire={18}
            piezas={[
              {
                p: pos(eq!),
                nodo: (
                  <span className="text-[10px] tabular-nums text-slate-500">
                    {verificado ? `0 % · ${fmtMoneda(eq)}` : "0 %"}
                  </span>
                ),
              },
              {
                p: pos(piso!),
                nodo: (
                  <span className="text-[10px] tabular-nums text-slate-500">
                    {verificado ? `${meta} · ${fmtMoneda(piso)}` : meta}
                  </span>
                ),
              },
            ]}
          />
        )}
      </div>
    </div>
    </PanelHover>
  );
}

function Piso({ p }: { p: Publicacion }) {
  const [abierto, setAbierto] = useState(false);
  const objetivo = p.piso_objetivo ?? 0.2;
  const meta = `${Math.round(objetivo * 100)}%`;

  if (p.piso_aviso === "canal_sin_costo") {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-x-2 border-t border-slate-100 pt-2 text-[11px] text-slate-400"
           title={`El precio sugerido necesita la comisión del canal y su tarifa de envío para calcularse, y hoy solo Mercado Libre las tiene cargadas. No es que esta publicación esté bien o mal: todavía no se puede saber.`}>
        <Target size={13} className="shrink-0 text-slate-300" />
        <span>Precio sugerido: todavía no disponible en este canal</span>
      </div>
    );
  }

  if (p.piso_aviso === "costo_sin_verificar") {
    return (
      <div className="mt-2 border-t border-slate-100 pt-2 text-[11px]"
           title={`Esta publicación está por debajo del ${meta} de margen, pero su costo no está verificado contra el packing list. Verificar el costo primero: si está mal capturado, el precio que haría falta también estaría mal.`}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <AlertTriangle size={13} className="shrink-0 text-amber-600" />
          <span className="font-semibold text-amber-700">Bajo el {meta} de margen</span>
          <span className="text-slate-500">— el costo no está verificado, revísalo antes de mover el precio</span>
        </div>
      </div>
    );
  }

  if (p.precio_piso == null) return null;   // por encima del piso: silencio

  const veces = p.precio_vigente ? p.precio_piso / p.precio_vigente : null;
  const d = p.piso_desglose;
  return (
    <div className="mt-2 border-t border-rose-100 bg-rose-50/60 -mx-3 px-3 pt-2 pb-1">
      <button
        type="button"
        onClick={() => setAbierto((v) => !v)}
        disabled={!d}
        className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 text-left text-[11px]"
      >
        <Target size={13} className="shrink-0 text-rose-600" />
        <span className="text-rose-700">Para {meta} de margen:</span>
        <span className="font-black tabular-nums text-rose-700">{fmtMoneda(p.precio_piso)}</span>
        {veces !== null && veces >= 1.15 && (
          <span className="text-rose-500">{veces.toFixed(1)}× lo que cobras</span>
        )}
        {d && (
          <span className="ml-auto flex items-center gap-0.5 text-rose-500">
            {abierto ? "ocultar" : "ver la cuenta"}
            <ChevronDown size={12} className={abierto ? "rotate-180 transition" : "transition"} />
          </span>
        )}
      </button>

      {/* LA CUENTA, para que el número no haya que creerlo (Eduardo, 9-sep).
          Es la misma resta que produce el margen de arriba, con los dos datos
          que normalmente no se ven: el precio sin IVA —sobre el que ML cobra
          su comisión, no sobre el precio de venta— y el peso EFECTIVO, que es
          el mayor entre el real y el volumétrico. Sin ellos el fee y la
          comisión parecen salidos de la nada. */}
      {abierto && d && (
        <div className="mb-1 mt-2 rounded-lg border border-rose-200 bg-white px-3 py-2">
          <table className="w-full text-[11px] tabular-nums">
            <tbody className="text-slate-600">
              <tr>
                <td className="py-0.5">Precio sugerido</td>
                <td className="py-0.5 text-right font-semibold text-slate-800">{fmtMoneda(d.precio)}</td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-500">Precio sin IVA</td>
                <td className="py-0.5 text-right text-slate-500">
                  {fmtMoneda(d.precio_sin_iva)}
                  <span className="ml-1 text-slate-400">= {fmtMoneda(d.precio)} ÷ {(1 + d.iva_rate).toFixed(2)}</span>
                </td>
              </tr>
              <tr><td className="py-0.5">− IVA</td>
                  <td className="py-0.5 text-right">{fmtMoneda(d.iva)}</td></tr>
              <tr><td className="py-0.5">− Comisión de Mercado Libre</td>
                  <td className="py-0.5 text-right">
                    {fmtMoneda(d.comision)}
                    <span className="ml-1 text-slate-400">
                      = {fmtMoneda(d.precio_sin_iva)} × {(d.pct_comision * 100).toFixed(2)}%
                    </span>
                  </td></tr>
              <tr><td className="py-0.5">− Envío</td>
                  <td className="py-0.5 text-right">
                    {fmtMoneda(d.fee_envio)}
                    <span className="ml-1 text-slate-400">
                      tarifa de {d.peso_efectivo} kg
                      {d.peso_real != null && d.peso_efectivo > d.peso_real
                        ? ` (volumétrico; el real son ${d.peso_real} kg)`
                        : ""}
                    </span>
                  </td></tr>
              <tr><td className="py-0.5">− Costo del producto</td>
                  <td className="py-0.5 text-right">{fmtMoneda(d.costo)}</td></tr>
              <tr className="border-t border-slate-200">
                <td className="pt-1 font-semibold text-slate-800">= Ganancia</td>
                <td className="pt-1 text-right font-semibold text-emerald-700">{fmtMoneda(d.ganancia)}</td>
              </tr>
              <tr>
                <td className="py-0.5 text-slate-500">Margen sobre el precio</td>
                <td className="py-0.5 text-right text-slate-500">
                  {fmtPctFirmado(d.margen_pct)}
                  <span className="ml-1 text-slate-400">
                    = {fmtMoneda(d.ganancia)} ÷ {fmtMoneda(d.precio)}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mt-2 border-t border-slate-100 pt-1.5 text-[10px] leading-relaxed text-slate-400">
            El envío sube por tramos de precio, así que al subir el precio ML también
            cobra más envío. Por eso el número se busca por aproximación y no con una
            fórmula: es el precio más bajo que llega al {meta}.
          </p>
        </div>
      )}
    </div>
  );
}

/** El margen de ESTA publicación, contra el precio que cobra hoy. */
function Margen({ p, aviso }: { p: Publicacion; aviso?: string }) {
  // SIN DATO. Ni 0 %, ni un guion mudo: el motivo es la mitad del mensaje.
  if (p.margen_pct === null) {
    const motivo = (p.margen_motivo ?? "desconocido") as MargenMotivo;
    return (
      <div className="leading-tight" title={MOTIVO_TEXTO[motivo] ?? MOTIVO_TEXTO.desconocido}>
        <div className="text-base font-bold text-slate-300">sin dato</div>
        <div className="mt-1 text-[11px] text-slate-500">
          {MOTIVO_CORTO[motivo] ?? motivo}
        </div>
      </div>
    );
  }

  const perdida = p.margen_pct < 0;
  // Regla de la casa (`lib/margen.ts`, 1.5×): cuando el costo capturado supera
  // al precio por más de ese factor, el número se pinta en ÁMBAR con ⚠ en vez
  // del rojo que se lee como un hecho. Aquí pesa más que en Análisis, porque
  // esta tarjeta es donde alguien decide bajar una publicación — y el 30% del
  // catálogo trae como costo un precio en dólares redondeado (×19).
  const dudoso = costoImplausible(p.precio_vigente, p.costo_unitario, p.revisado_at);
  const desglose =
    p.roi !== null
      ? `ROI sobre el costo: ${fmtPctFirmado(p.roi)} · costo ${fmtMoneda(
          p.costo_unitario,
        )} · comisión ${fmtMoneda(p.costo_comision)} · envío ${fmtMoneda(
          p.costo_fee_envio,
        )} · IVA ${fmtMoneda(p.iva_mnt)}`
      : "";
  const tooltip = [
    dudoso && p.precio_vigente && p.costo_unitario
      ? avisoCostoImplausible(p.precio_vigente, p.costo_unitario)
      : "",
    desglose,
    aviso ?? "",
  ]
    .filter(Boolean)
    .join("\n\n");

  return (
    <div className="leading-tight" title={tooltip || undefined}>
      <div
        className={[
          "flex items-center gap-1 text-base font-black tabular-nums",
          dudoso ? "text-amber-600" : perdida ? "text-rose-600" : "text-emerald-700",
        ].join(" ")}
      >
        {dudoso && <AlertTriangle size={13} className="shrink-0" />}
        {fmtPctFirmado(p.margen_pct)}
      </div>
      <div
        className={[
          "mt-1 text-[11px] font-semibold tabular-nums",
          dudoso ? "text-amber-600" : perdida ? "text-rose-500" : "text-slate-500",
        ].join(" ")}
      >
        {dudoso
          ? "costo dudoso"
          : `${fmtMoneda(p.ganancia_neta, p.moneda)} de ganancia`}
      </div>
    </div>
  );
}

interface Props {
  /** Las publicaciones de ESTE canal (y de esta cuenta, si la tarjeta la tiene). */
  pubs: Publicacion[];
  /** `cobertura.aviso`: el margen es prospectivo, no realizado. Va al tooltip. */
  aviso?: string;
  /** `cobertura.canales[].nota`: por qué este canal cuenta lo que cuenta. */
  nota?: string | null;
  /** Color del canal, para el enlace. */
  color: string;
  /** Las publicaciones vienen en camino. Pinta esqueleto en vez de nada. */
  cargando?: boolean;
  /**
   * Además, a ESTE canal se le está confirmando el precio contra el canal
   * mismo. Sólo Mercado Libre, y sólo al abrir el cajón.
   */
  confirmando?: boolean;
  /**
   * Dibujar el "Ver esta publicación" de cada tarjeta. Se apaga en las tarjetas
   * por canal, donde el pie de la sección YA trae el mismo enlace junto al id:
   * Mercado Libre da UNA tarjeta por cuenta y ninguna cuenta tiene dos
   * publicaciones del mismo SKU (0 pares en producción, 26-ago-2026), así que
   * los dos enlaces llevaban exactamente al mismo lado.
   *
   * Se queda ENCENDIDO —el default— en las publicaciones huérfanas, que se
   * pintan sin pie: ahí este enlace es el único camino a la publicación.
   */
  conEnlace?: boolean;
}

export default function PublicacionesDelCanal({
  pubs,
  aviso,
  nota,
  color,
  cargando,
  confirmando,
  conEnlace = true,
}: Props) {
  // Mientras la respuesta viene en camino el bloque NO se desvanece. Confirmar
  // el precio contra Mercado Libre agrega ~1 s a la apertura, y un hueco mudo
  // justo donde va el precio se lee como "esta publicación no tiene precio".
  // El resto del cajón ya está pintado: esto NO bloquea nada más.
  if (!pubs.length) {
    if (!cargando && !confirmando) return null;
    return (
      <div className="border-b border-slate-100 bg-slate-50/40 px-4 py-3">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          <RefreshCw size={11} className="shrink-0 animate-spin" />
          {confirmando ? "Confirmando precio con el canal…" : "Cargando publicaciones…"}
        </div>
        <div
          className="h-[4.5rem] animate-pulse rounded-lg border border-slate-200 bg-white"
          aria-hidden
        />
      </div>
    );
  }

  return (
    <div className="border-b border-slate-100 bg-slate-50/40 px-4 py-3">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
          {pubs.length === 1
            ? "Publicación"
            : `${pubs.length} publicaciones en este canal`}
        </span>
        {pubs.length > 1 && (
          <span
            className="text-[10px] font-medium text-slate-400"
            title="Cada publicación tiene su propio precio y su propio margen. Un solo número por canal tendría que elegir una y callar el resto."
          >
            precio y margen de cada una
          </span>
        )}
      </div>

      <div className="space-y-2">
        {pubs.map((p, i) => (
          <div
            key={`${p.canal}-${p.tienda ?? ""}-${p.listing_id ?? i}`}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2.5"
          >
            <div className="mb-2 flex flex-wrap items-center justify-between gap-1.5">
              <span className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500">
                {labelTienda(p.tienda)}
                {p.listing_id && (
                  <span className="font-mono text-[10px] text-slate-400">
                    {p.listing_id}
                  </span>
                )}
              </span>
              <ChipEstado p={p} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                  Precio que cobra hoy
                </div>
                <Precio p={p} />
              </div>
              <div>
                <div
                  className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400"
                  title={
                    aviso ??
                    "Margen PROSPECTIVO: contra el precio que la publicación cobra hoy, no contra lo que ya se vendió."
                  }
                >
                  Margen si vendo una hoy
                </div>
                <Margen p={p} aviso={aviso} />
              </div>
            </div>

            {/* La regla y el piso van DEBAJO de las dos columnas, no como una
                tercera: con tres cifras a lo ancho del cajón ninguna se lee de
                un vistazo. La regla primero, porque es la referencia (dónde
                está el mercado); el piso, cuando aparece, es la acción. */}
            <ReglaMercado p={p} />
            <Piso p={p} />

            {conEnlace && enlacePublicacion(p.canal, p.listing_id, p.url) && (
              <div className="mt-2 flex justify-end">
                <a
                  href={enlacePublicacion(p.canal, p.listing_id, p.url)!}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-[11px] font-semibold"
                  style={{ color }}
                >
                  Ver esta publicación <ExternalLink size={11} />
                </a>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* La nota del canal explica un 0 o un "sin dato" que si no se lee como bug. */}
      {nota && (
        <p className="mt-2 text-[11px] leading-snug text-slate-500">{nota}</p>
      )}
    </div>
  );
}
