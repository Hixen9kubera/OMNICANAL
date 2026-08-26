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

import { AlertTriangle, ExternalLink, RefreshCw, Tag } from "lucide-react";

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
  const dudoso = costoImplausible(p.precio_vigente, p.costo_unitario);
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
