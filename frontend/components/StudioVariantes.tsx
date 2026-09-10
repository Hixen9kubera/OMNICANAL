"use client";

import {
  AlertTriangle, CheckCircle2, ImageIcon, ImageOff, Info, Layers, Lock,
  ShieldAlert, Split, XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { CanalTheme } from "@/lib/theme";
import type { ModoPublicacion, VarianteResumen } from "@/lib/types";
import { partesVariante } from "@/lib/coloresVariante";
import { ChipMoneda } from "./Moneda";
import {
  COLOR_ESTADO, LABEL_ESTADO, presenciaDe, puntoEstado, type EstadoVariante,
} from "@/lib/estadoVariante";

/**
 * StudioVariantes.tsx — el rail, la banda de modo y el diálogo de desagrupar.
 *
 * Las medidas salen del handoff de diseño y son finales: rail de 236 px,
 * renglón de 7×8 px con gap 9, miniatura 32, muestra de color 9, punto de 7.
 *
 * EL VIOLETA NO ES EL PRIMARIO DEL PANEL. Es el color del vocabulario
 * padre/variante (y del chip DROP OFF), heredado de `Variantes.tsx`. El naranja
 * quemado (#c2410c) es el del modo individual. El color del CANAL manda dentro
 * de la ficha, no aquí.
 */

// ── Qué le falta a una variante para poder publicarse ─────────────────────────
//
// "Lista" NO es "publicada": es "se puede mandar". El diseño enseña
// «5 / 6 listas» y al pie el porqué de la que falta ("sin GTIN ni imagen"), así
// que la cuenta y el aviso salen del MISMO cálculo. Separarlos haría que el
// contador dijera 5 y el pie no supiera explicar cuál.

export function faltantesDe(v: VarianteResumen): string[] {
  const falta: string[] = [];
  if (v.precio === null || v.precio === undefined) falta.push("precio");
  if (!v.imagen) falta.push("imagen");
  // ⚠️ EL GTIN NO CUENTA COMO FALTANTE, aunque el mockup lo enseñe así
  // («sin GTIN ni imagen»). Medido el 10-sep-2026: prácticamente ninguna
  // variante del catálogo tiene `_barcode` —CLAUDE.md registra 2 SKUs con GTIN
  // real en toda la cuenta BEKURA— así que contarlo dejaría el contador clavado
  // en «0 / 8 listas» para todas las familias, y un aviso que sale siempre deja
  // de leerse. Además sólo lo EXIGEN algunas categorías, y quién lo exige ya lo
  // sabe el semáforo por canal (`/canal/{canal}/faltantes`), que sí pregunta.
  // En la tabla POR VARIANTE se sigue mostrando "falta": ahí es información,
  // no un bloqueo.
  return falta;
}

function frase(partes: string[]): string {
  if (partes.length === 0) return "";
  if (partes.length === 1) return partes[0];
  return partes.slice(0, -1).join(", ") + " ni " + partes[partes.length - 1];
}

// ══════════════════════════════════════════════════════════════════════════════
// Banda de modo
// ══════════════════════════════════════════════════════════════════════════════

export function BandaModo({
  modo, onModo, canalLabel, nVariantes, nListas, nPublicadas, agrupadaHabilitada,
}: {
  modo: ModoPublicacion;
  onModo: (m: ModoPublicacion) => void;
  canalLabel: string;
  nVariantes: number;
  nListas: number;
  nPublicadas: number;
  /** false = el publicador todavía no sabe mandar `variations` a este canal. */
  agrupadaHabilitada: boolean;
}) {
  const agrupada = modo === "agrupada";
  return (
    <div
      className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-2"
      style={{ minHeight: 51 }}
    >
      <div
        className="flex shrink-0 items-center gap-0.5 rounded-lg border p-0.5"
        style={{ borderColor: agrupada ? "#ddd6fe" : "#ffdcc0" }}
      >
        <BotonModo
          activo={agrupada}
          onClick={() => onModo("agrupada")}
          color="#6d28d9"
          Icono={Layers}
          label="Agrupada"
          // El modo se puede MIRAR aunque no se pueda ejecutar: el diseño está
          // aprobado y esconderlo dejaría la pantalla a medias. Lo que no se
          // puede es publicar ni guardarlo, y de eso avisa AvisoAgrupadaPronto.
          pronto={!agrupadaHabilitada}
        />
        <BotonModo
          activo={!agrupada}
          onClick={() => onModo("individual")}
          color="#c2410c"
          Icono={Split}
          label="Individual"
        />
      </div>

      <p className="min-w-0 flex-1 text-[11.5px] leading-snug text-slate-500">
        {agrupada ? (
          <>
            Una publicación con selector de color.{" "}
            <strong className="text-slate-700">Se edita sólo el primer producto</strong>,
            como si fuera el padre;{" "}
            {nVariantes === 2
              ? "la otra hereda su ficha."
              : nVariantes > 2
                ? "las otras " + (nVariantes - 1) + " heredan su ficha."
                : "no hay otras que hereden su ficha."}
          </>
        ) : (
          <>
            {nVariantes} publicaciones separadas.{" "}
            <strong className="text-slate-700">Cada variante se edita por completo</strong>:
            su título, sus imágenes, sus atributos y su categoría.
          </>
        )}
      </p>

      <span
        className="shrink-0 font-mono text-[11px] font-bold tabular-nums"
        style={{ color: "#b45309" }}
        title={agrupada
          ? "Variantes con precio, imagen y GTIN: las que se pueden mandar al canal"
          : "Variantes ya publicadas en " + canalLabel}
      >
        {agrupada
          ? nListas + " / " + nVariantes + " listas"
          : nPublicadas + " / " + nVariantes + " publicadas"}
      </span>
    </div>
  );
}

function BotonModo({
  activo, onClick, color, Icono, label, pronto,
}: {
  activo: boolean;
  onClick: () => void;
  color: string;
  Icono: typeof Layers;
  label: string;
  pronto?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={pronto ? label + ": se puede revisar, todavía no publicar" : undefined}
      style={activo ? { backgroundColor: color, color: "#fff" } : undefined}
      className={[
        "flex items-center gap-1.5 rounded-[7px] px-2.5 py-1.5 text-xs font-bold transition-colors",
        activo ? "" : "text-slate-500 hover:bg-slate-50",
      ].join(" ")}
    >
      <Icono size={13} />
      {label}
      {pronto && (
        <span
          className="rounded-full px-1.5 text-[8.5px] font-bold uppercase tracking-wide"
          style={activo
            ? { backgroundColor: "rgba(255,255,255,.25)", color: "#fff" }
            : { backgroundColor: "#e2e8f0", color: "#64748b" }}
        >
          Pronto
        </span>
      )}
    </button>
  );
}

/**
 * El aviso de que el modo agrupado se ve pero no se ejecuta.
 *
 * No es decoración: sin él, alguien elige «Agrupada», le da a Publicar y el
 * canal recibe una ficha PLANA con el precio mínimo de la familia — que es
 * exactamente lo que hoy deja 2,104 variantes incomprables en Mercado Libre.
 */
export function AvisoAgrupadaPronto({ canalLabel }: { canalLabel: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>
        <strong>Vista previa.</strong> El modo agrupado todavía no se puede publicar en{" "}
        {canalLabel}: el publicador manda una ficha plana, sin selector de variante.
        Puedes revisar cómo quedaría; <strong>Publicar y Guardar están apagados</strong> en
        este modo.
      </span>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Rail de variantes — 236 px
// ══════════════════════════════════════════════════════════════════════════════

export function RailVariantes({
  variantes, canal, canalLabel, modo, skuSel, onSel, tema,
}: {
  variantes: VarianteResumen[];
  canal: string;
  canalLabel: string;
  modo: ModoPublicacion;
  skuSel: string | null;
  onSel: (sku: string) => void;
  tema: CanalTheme;
}) {
  const agrupada = modo === "agrupada";
  const esGeneral = canal === "general";
  const primero = variantes[0]?.sku ?? null;
  // La leyenda del punto: cinco colores de 7 px no se explican solos, y sin
  // una puerta para leerla el 2c del diseño se queda sin usar.
  const [verLeyenda, setVerLeyenda] = useState(false);

  const filas = useMemo(() => variantes.map((v) => {
    const { color, etiqueta } = partesVariante(v.nombre);
    const punto = puntoEstado(canal, presenciaDe(v.canales, canal), v.stock, v.estado);
    return { v, color, etiqueta: etiqueta || v.sku, punto, falta: faltantesDe(v) };
  }), [variantes, canal]);

  // El pie dice UNA cosa, la más útil: en agrupada, cuál variante no se podría
  // mandar y por qué; en individual, cuántas faltan por publicar en el canal.
  const noListas = filas.filter((f) => f.falta.length > 0);
  const sinPublicar = filas.filter((f) => f.punto.estado === "falta");

  return (
    <div className="flex w-[236px] shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center justify-between px-3 pb-1.5 pt-3">
        <span className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">
          Variantes
        </span>
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setVerLeyenda((v) => !v)}
            title="Qué significa cada color del punto"
            className="rounded p-0.5 text-slate-300 transition-colors hover:bg-slate-100 hover:text-slate-500"
          >
            <Info size={12} />
          </button>
          <span className="font-mono text-[11px] font-bold tabular-nums text-slate-400">
            {variantes.length}
          </span>
        </span>
      </div>

      {verLeyenda && (
        <div className="px-2 pb-2">
          <LeyendaEstado compacta />
        </div>
      )}

      <div className="flex-1 space-y-1 overflow-y-auto overscroll-contain px-2 pb-2">
        {filas.map(({ v, color, etiqueta, punto, falta }) => {
          const sel = v.sku === skuSel;
          // En agrupada sólo el primero se edita: los demás heredan su ficha.
          const bloqueada = agrupada && v.sku !== primero;
          const acento = agrupada ? "#6d28d9" : tema.acento;
          const suave = agrupada ? "#f5f3ff" : tema.suave;
          const nota = agrupada || esGeneral
            ? v.sku
            : punto.estado === "falta"
              ? (sel ? "editando · falta publicar" : "falta publicar")
              : (sel ? "editando · " + punto.label.toLowerCase() : punto.label.toLowerCase());
          return (
            <button
              key={v.sku}
              type="button"
              onClick={() => !bloqueada && onSel(v.sku)}
              title={bloqueada
                ? "Hereda del padre — en modo agrupada sólo se edita el primer producto"
                : etiqueta + " · " + v.sku}
              style={sel
                ? { borderColor: acento, backgroundColor: suave, borderWidth: 1.5 }
                : { borderColor: "transparent", borderWidth: 1.5 }}
              className={[
                "flex w-full items-center gap-[9px] rounded-lg px-2 py-[7px] text-left transition-colors",
                bloqueada ? "cursor-default opacity-60" : sel ? "" : "hover:bg-slate-50",
              ].join(" ")}
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-100 bg-slate-50">
                {v.imagen ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={v.imagen} alt="" className="h-full w-full object-contain" />
                ) : (
                  <ImageOff size={13} className="text-slate-300" />
                )}
              </span>

              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  {color && (
                    <span
                      className="h-[9px] w-[9px] shrink-0 rounded-[3px]"
                      style={{ background: color.background, border: "1px solid rgba(15,23,42,.14)" }}
                      title={color.etiqueta}
                    />
                  )}
                  <span className={[
                    "truncate text-xs",
                    sel ? "font-bold text-slate-800" : "font-medium text-slate-600",
                  ].join(" ")}>
                    {etiqueta}
                  </span>
                  {bloqueada && <Lock size={10} className="shrink-0 text-slate-400" />}
                </span>
                <span className="mt-px block truncate font-mono text-[9.5px] text-slate-400">
                  {nota}
                </span>
              </span>

              <span
                className="h-[7px] w-[7px] shrink-0 rounded-full"
                style={{ backgroundColor: punto.color }}
                title={punto.label + " — " + punto.detalle
                  + (falta.length ? " · le falta " + frase(falta) : "")}
              />
            </button>
          );
        })}
      </div>

      <div className="border-t border-slate-100 px-3 py-2">
        {agrupada && noListas.length > 0 ? (
          <p className="flex items-start gap-1.5 text-[10.5px] leading-snug" style={{ color: "#92400e" }}>
            <AlertTriangle size={12} className="mt-px shrink-0" />
            <span>
              <strong>{noListas[0].etiqueta}</strong> falta publicar: sin {frase(noListas[0].falta)}
              {noListas.length > 1 ? " · y " + (noListas.length - 1) + " más" : ""}
            </span>
          </p>
        ) : !agrupada && !esGeneral && sinPublicar.length > 0 ? (
          <p className="flex items-start gap-1.5 text-[10.5px] leading-snug" style={{ color: "#92400e" }}>
            <AlertTriangle size={12} className="mt-px shrink-0" />
            <span>
              <strong>{sinPublicar.length} faltan</strong> por publicar en {canalLabel}
            </span>
          </p>
        ) : (
          <p className="text-[10.5px] text-slate-400">
            {esGeneral
              ? variantes.length + " variantes en la ficha de WooCommerce."
              : "Todas al día en " + canalLabel + "."}
          </p>
        )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Leyenda del punto de estado
// ══════════════════════════════════════════════════════════════════════════════

const ORDEN: EstadoVariante[] = ["publicada", "pausada", "enviada", "sin_stock", "falta"];

const GLOSA: Record<EstadoVariante, string> = {
  publicada: "viva y comprable en el canal",
  pausada: "existe en el canal pero no se vende",
  enviada: "se mandó y el canal no ha confirmado",
  sin_stock: "publicada en 0 pzas — el canal la muestra agotada",
  falta: "nunca se envió a este canal",
};

export function LeyendaEstado({ compacta }: { compacta?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50">
      {!compacta && (
        <div className="border-b border-slate-100 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">
          Punto de estado en el rail
        </div>
      )}
      <div className={compacta ? "space-y-1 px-2 py-2" : "space-y-1.5 px-3 py-2.5"}>
        {ORDEN.map((e) => (
          <div key={e} className={compacta ? "flex items-start gap-1.5 text-[10px]" : "flex items-center gap-2 text-[11px]"}>
            <span
              className="mt-1 h-[7px] w-[7px] shrink-0 rounded-full"
              style={{ backgroundColor: COLOR_ESTADO[e] }}
            />
            <span className={compacta ? "min-w-0" : "flex items-center gap-2"}>
              <span className="font-semibold text-slate-700">{LABEL_ESTADO[e]}</span>
              <span className="text-slate-400">{compacta ? " · " : ""}{GLOSA[e]}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Desagrupar — sólo si la publicación YA existe en el canal
// ══════════════════════════════════════════════════════════════════════════════

/**
 * ⚠️ Este diálogo está CONSTRUIDO Y DORMIDO a propósito.
 *
 * Sólo puede aparecer al pasar de agrupada a individual sobre una publicación
 * VIVA con variantes, y hoy no existe ninguna: el publicador arma una ficha
 * plana y hay 0 publicaciones con `variations` en las dos cuentas de ML.
 *
 * Se deja escrito porque su contenido no es de UI, es de negocio: separar da de
 * baja una publicación con preguntas, reseñas, antigüedad y posición de
 * búsqueda. El día que exista el agrupado, esa advertencia tiene que estar ya
 * pensada, no inventarse con prisa.
 */
export function DialogoDesagrupar({
  listingId, canalLabel, nVariantes, nPreguntas, nResenas, onCancelar, onConfirmar,
}: {
  listingId: string;
  canalLabel: string;
  nVariantes: number;
  nPreguntas?: number | null;
  nResenas?: number | null;
  onCancelar: () => void;
  onConfirmar: () => void;
}) {
  const [texto, setTexto] = useState("");
  const ok = texto.trim().toUpperCase() === "DESAGRUPAR";
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/50 p-4">
      <div className="w-full max-w-[430px] overflow-hidden rounded-xl border border-rose-200 bg-white shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-rose-100 bg-rose-50 px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-bold text-rose-900">
            <ShieldAlert size={16} /> Desagrupar en {canalLabel}
          </span>
          <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-rose-700">
            No se deshace
          </span>
        </div>

        <div className="space-y-2.5 px-4 py-3.5">
          <p className="text-xs leading-relaxed text-slate-600">
            La publicación <strong className="font-mono">{listingId}</strong> ya está viva
            con las {nVariantes} variantes. Separarla obliga a relistar.
          </p>
          <Consecuencia mala>
            Se cierra la ficha actual y se crean {nVariantes} nuevas.
          </Consecuencia>
          <Consecuencia mala>
            Se pierden
            {nPreguntas != null ? " " + nPreguntas + " preguntas," : " las preguntas,"}
            {nResenas != null ? " " + nResenas + " reseñas" : " las reseñas"} y la antigüedad.
          </Consecuencia>
          <Consecuencia>El padre y el stock no cambian en Kubera ni en Woo.</Consecuencia>

          <input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            placeholder="Escribe DESAGRUPAR"
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-xs uppercase tracking-wide outline-none focus:border-rose-300"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onCancelar}
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            >
              Mantener agrupada
            </button>
            <button
              type="button"
              disabled={!ok}
              onClick={onConfirmar}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Split size={13} /> Desagrupar
            </button>
          </div>
          <p className="text-[10px] leading-snug text-slate-400">
            Si aún no existe en el canal, cambiar de modo es libre y este diálogo no aparece.
          </p>
        </div>
      </div>
    </div>
  );
}

function Consecuencia({ mala, children }: { mala?: boolean; children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs leading-snug text-slate-600">
      {mala
        ? <XCircle size={13} className="mt-0.5 shrink-0 text-rose-500" />
        : <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-500" />}
      <span>{children}</span>
    </p>
  );
}

// ══════════════════════════════════════════════════════════════════════════════
// Tabla POR VARIANTE — lo único que NO se hereda en modo agrupada
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Precio, stock, GTIN e imagen de cada color, en una tabla compacta al final de
 * la ficha (el diseño evita cambiar de pantalla para esto).
 *
 * ⚠️ ES DE SÓLO LECTURA, y es la única desviación del diseño. Los mockups
 * pintan casillas editables, pero escribir precios es un FLUJO DE NEGOCIO VIVO
 * (regla 3 de la casa) y en modo agrupada Guardar está apagado de todas formas
 * — dejar casillas que aceptan un número y lo tiran sería peor que no tenerlas.
 * El lápiz de la lista del Publicador (`CostoEditor`) ya edita costo y precios
 * en línea y SÍ guarda; a eso apunta la nota del pie.
 *
 * ⚠️ EL DINERO NO SE TIÑE CON EL COLOR DEL CANAL. Lleva el índigo de MXN de
 * `Moneda.tsx`: ése es justamente el trabajo del componente, porque en Costos
 * conviven dos monedas con 19× de diferencia.
 */
export function TablaPorVariante({ variantes }: { variantes: VarianteResumen[] }) {
  return (
    <section className="overflow-hidden rounded-xl border border-orange-100 bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-orange-100 bg-orange-50 px-3 py-2">
        <span className="flex items-center gap-2">
          <span className="rounded-full bg-white px-2 py-0.5 text-[9.5px] font-bold uppercase tracking-[0.05em] text-orange-800">
            Por variante
          </span>
          <span className="text-xs font-semibold text-slate-600">
            Precio, stock, GTIN e imagen de cada color
          </span>
        </span>
        <span className="text-[10.5px] text-orange-700">Lo único que no se hereda</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-slate-100 text-[9.5px] font-bold uppercase tracking-[0.05em] text-slate-400">
              <th className="px-3 py-2">Color</th>
              <th className="px-3 py-2 text-right">
                P. Regular <ChipMoneda moneda="MXN" />
              </th>
              <th className="px-3 py-2 text-right">
                P. Oferta <ChipMoneda moneda="MXN" />
              </th>
              <th className="px-3 py-2 text-right">Stock</th>
              <th className="px-3 py-2 text-center">Imagen</th>
              <th className="px-3 py-2">GTIN</th>
            </tr>
          </thead>
          <tbody>
            {variantes.map((v) => {
              const { color, etiqueta } = partesVariante(v.nombre);
              const sinStock = (v.stock ?? null) === 0;
              return (
                <tr key={v.sku} className="border-b border-slate-50 last:border-0">
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-1.5">
                      {color && (
                        <span
                          className="h-[9px] w-[9px] shrink-0 rounded-[3px]"
                          style={{ background: color.background, border: "1px solid rgba(15,23,42,.14)" }}
                        />
                      )}
                      <span className="text-xs font-medium text-slate-700">{etiqueta || v.sku}</span>
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs tabular-nums text-slate-500">
                    {v.precio != null ? v.precio.toLocaleString("es-MX", { minimumFractionDigits: 2 }) : <Falta>sin precio</Falta>}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs font-bold tabular-nums text-slate-800">
                    {v.precio != null ? v.precio.toLocaleString("es-MX", { minimumFractionDigits: 2 }) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span
                      className="inline-block rounded px-2 py-0.5 font-mono text-xs font-bold tabular-nums"
                      style={sinStock
                        ? { backgroundColor: "#fff1f2", color: "#9f1239" }
                        : { color: "#334155" }}
                    >
                      {v.stock ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    {v.imagen
                      ? <ImageIcon size={14} className="mx-auto text-slate-400" />
                      : <ImageOff size={14} className="mx-auto text-amber-500" />}
                  </td>
                  <td className="px-3 py-2">
                    {v.gtin
                      ? <span className="font-mono text-[11px] tabular-nums text-slate-500">{v.gtin}</span>
                      : <Falta>falta</Falta>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="border-t border-slate-100 px-3 py-2 text-[10.5px] text-slate-400">
        Se muestran como están en WooCommerce. Para cambiarlos, el lápiz de la lista
        del Publicador edita costo y precios en línea.
      </p>
    </section>
  );
}

function Falta({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-dashed border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
      {children}
    </span>
  );
}

/** Cuántas variantes están publicadas en un canal (contador de la banda). */
export function contarPublicadas(variantes: VarianteResumen[], canal: string): number {
  return variantes.filter(
    (v) => puntoEstado(canal, presenciaDe(v.canales, canal), v.stock, v.estado).estado !== "falta",
  ).length;
}
