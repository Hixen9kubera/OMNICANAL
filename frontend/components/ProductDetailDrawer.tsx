"use client";

import { useEffect, useRef, useState } from "react";
import {
  X,
  AlertTriangle,
  ExternalLink,
  RefreshCw,
  Truck,
  Boxes,
  Tag,
  ChevronRight,
  ImageIcon,
} from "lucide-react";
import type {
  CanalInfo,
  DetalleCanal,
  Producto,
  Publicacion,
  RefrescoPrecio,
} from "@/lib/types";
import { listarPublicaciones, refrescarCanal } from "@/lib/api";
import { enlacePublicacion } from "@/lib/enlaces";
import { motivoRefresco } from "@/lib/publicaciones";
import { avisoCostoImplausible, costoImplausible, margenBruto,
         precioSinIva } from "@/lib/margen";
import { useDetalleProducto, invalidarDetalle } from "@/lib/useDetalleProducto";
import { ChipMoneda, type Moneda } from "./Moneda";
import PublicacionesDelCanal from "./PublicacionesDelCanal";

interface Props {
  sku: string | null;
  producto?: Producto | null;
  canales: CanalInfo[];
  onClose: () => void;
}

/**
 * Los estados cuyo significado NO se entiende sin la nota del canal: "puede
 * estar activa" (Temu no distingue), "no comprable" (Amazon DISCOVERABLE) y los
 * dos que admiten no saber. Con el resto la nota sobra y solo hace ruido.
 */
const ESTADOS_QUE_PIDEN_NOTA = new Set([
  "puede_estar_activa",
  "no_comprable",
  "sin_estado",
  "desconocido",
]);

/** La cuenta a la que pertenece la tarjeta, cuando el detalle la distingue. */
function cuentaDe(c: DetalleCanal): string | null {
  const v = (c.extra as Record<string, unknown> | undefined)?.cuenta;
  return typeof v === "string" && v ? v : null;
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
  }).format(v);
}

export default function ProductDetailDrawer({ sku, producto, canales, onClose }: Props) {
  // #2/#3: pinta al instante lo que ya trae la lista y cachea el detalle (SWR).
  const { data, cargando, recargar } = useDetalleProducto(sku, producto);
  const [refrescando, setRefrescando] = useState<string | null>(null);

  // Las publicaciones de ESTE SKU, una por listing y no una por canal: el
  // precio vigente, la oferta y el margen se miden por publicación en
  // `services/publicaciones_panel.py`. Lectura pura (GET), sin recálculo aquí.
  const [pubs, setPubs] = useState<Publicacion[]>([]);
  const [avisoMargen, setAvisoMargen] = useState<string>("");
  const [notas, setNotas] = useState<Record<string, string | null>>({});
  const [pubsFallaron, setPubsFallaron] = useState(false);

  // Abrir el cajón CONFIRMA el precio de ML contra ML antes de pintarlo (el
  // encargo de Eduardo: "debe tener el mismo precio en tienda"). Lo hace el
  // backend —`refrescar=true`, v0.267.0—; de este lado sólo se dispara, se
  // espera sin bloquear el resto, y se dice cuándo NO se pudo.
  const [refresco, setRefresco] = useState<RefrescoPrecio | null>(null);
  const [pubsCargando, setPubsCargando] = useState(false);
  const [confirmandoPrecio, setConfirmandoPrecio] = useState(false);
  /** El SKU cuya apertura ya pidió confirmación. Ver el candado en el efecto. */
  const refrescadoPara = useRef<string | null>(null);

  useEffect(() => {
    if (!sku) {
      // Cerrar el cajón cierra la apertura: volver a abrir ESTE mismo SKU
      // vuelve a pedir confirmación, y allá el piso de 5 minutos decide si de
      // verdad hay que molestar a Mercado Libre.
      refrescadoPara.current = null;
      // Y no se deja encendido el indicador de una apertura que ya terminó.
      setPubsCargando(false);
      setConfirmandoPrecio(false);
      return;
    }
    const ctrl = new AbortController();
    setPubs([]);
    setPubsFallaron(false);
    setRefresco(null);
    setPubsCargando(true);

    // UNA confirmación por apertura. Hoy ya lo garantiza el arreglo de
    // dependencias (`[sku]`: un re-render no vuelve a entrar aquí); el candado
    // explícito es para que siga siendo verdad si alguien le suma una
    // dependencia al efecto. El piso de 5 min del backend cuida el GASTO, no
    // el parpadeo del indicador — eso se cuida aquí.
    const pedirRefresco = refrescadoPara.current !== sku;
    refrescadoPara.current = sku;
    setConfirmandoPrecio(pedirRefresco);

    // `q` busca por SUBCADENA en sku/título/listing_id: un SKU que es prefijo
    // de otro traería publicaciones ajenas (693 SKUs del catálogo lo son). Se
    // filtra por igualdad exacta aquí; el backend no tiene un filtro por SKU.
    // `refrescar` NO usa esa búsqueda: del otro lado el objetivo se arma con
    // `sku = q` exacto, y sale de aquí y sólo de aquí — nunca de la rejilla.
    listarPublicaciones({ q: sku, perPage: 500, refrescar: pedirRefresco }, ctrl.signal)
      .then((r) => {
        const mio = sku.trim().toUpperCase();
        setPubs(r.items.filter((p) => (p.sku ?? "").trim().toUpperCase() === mio));
        setAvisoMargen(r.cobertura?.aviso ?? "");
        // Sin `refrescar` el bloque no viene, y entonces no hay nada que decir.
        setRefresco(r.refresco ?? null);
        setNotas(
          Object.fromEntries(
            (r.cobertura?.canales ?? []).map((c) => [c.canal, c.nota]),
          ),
        );
      })
      .catch((exc) => {
        if (exc?.name === "AbortError") return;
        // Si esto falla, la tarjeta vuelve a mostrar el precio del detalle. Lo
        // que NO se hace es dejar el hueco callado: sin precio ni aviso, el
        // cajón se leería como "esta publicación no tiene precio".
        //
        // Ojo: esto es que se caiga la PETICIÓN ENTERA. Que la confirmación
        // contra ML no se lograra NO entra aquí — eso llega en `refresco` con
        // la respuesta buena y se pinta como aviso, no como error de carga.
        setPubsFallaron(true);
      })
      .finally(() => {
        // Si el cajón ya cambió de SKU, esta promesa es la VIEJA: apagar aquí
        // apagaría el indicador de la apertura nueva.
        if (ctrl.signal.aborted) return;
        setPubsCargando(false);
        setConfirmandoPrecio(false);
      });
    return () => ctrl.abort();
  }, [sku]);

  const cfg = (id: string) => canales.find((c) => c.id === id);

  // Reparto de publicaciones por tarjeta. El detalle trae UNA tarjeta por
  // cuenta en Mercado Libre (`extra.cuenta`), así que ahí el reparto va por
  // canal Y cuenta; en el resto, por canal.
  const pubsDe = (c: DetalleCanal): Publicacion[] => {
    const cta = cuentaDe(c);
    return pubs.filter(
      (p) => p.canal === c.canal && (!cta || p.tienda === cta),
    );
  };

  // Publicaciones que ninguna tarjeta reclamó. Existen: el detalle de ML
  // muestra UNA por cuenta (`cuentas_vistas` en el backend), así que un SKU con
  // dos listados en la MISMA cuenta tenía uno invisible. Se pintan aparte en
  // vez de desaparecer.
  //
  // Mientras el detalle sigue en camino NO se marca ninguna: al abrir el cajón
  // `data` es el parcial de la lista y trae 0 canales, así que TODAS parecerían
  // huérfanas por un instante. Etiquetar de "otra publicación" a una normal es
  // afirmar algo falso, aunque dure un segundo.
  const reclamadas = new Set<Publicacion>();
  for (const c of data?.canales ?? []) for (const p of pubsDe(c)) reclamadas.add(p);
  const huerfanas = cargando ? [] : pubs.filter((p) => !reclamadas.has(p));
  const gruposHuerfanos = huerfanas.reduce<Record<string, Publicacion[]>>((acc, p) => {
    (acc[p.canal] ||= []).push(p);
    return acc;
  }, {});

  /** La nota del canal, solo cuando explica un estado que sin ella no se entiende. */
  const notaSi = (canal: string, lista: Publicacion[]): string | null =>
    lista.some((p) => ESTADOS_QUE_PIDEN_NOTA.has(p.estado))
      ? notas[canal] ?? null
      : null;

  // Suma de piezas en bodegas de marketplace del SKU (FULL de cada cuenta ML +
  // FBA de Amazon). Se muestra junto al stock real en la tarjeta General.
  const fullFbaTotal = (data?.canales ?? []).reduce(
    (s, c) => s + (c.stock_full ?? 0) + (c.stock_fba ?? 0),
    0
  );

  // Cerrar con ESC
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (sku) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sku, onClose]);

  async function refrescar(canal: string) {
    if (!sku) return;
    setRefrescando(canal);
    try {
      await refrescarCanal(canal, sku);
      invalidarDetalle(sku); // forzamos datos frescos tras el refresco en vivo
      await recargar();
    } catch {
      /* el botón solo aplica a ML/Amazon con publicación */
    } finally {
      setRefrescando(null);
    }
  }

  if (!sku) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Panel */}
      <aside className="relative flex h-full w-full max-w-xl animate-slide-in flex-col bg-slate-50 shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-6 py-5">
          <div className="flex gap-4">
            <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
              {data?.imagen ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={data.imagen} alt="" className="h-full w-full object-contain p-1" />
              ) : (
                <ImageIcon className="text-slate-300" />
              )}
            </div>
            <div>
              <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-500">
                {sku}
              </span>
              <h2 className="mt-1.5 line-clamp-3 text-base font-bold leading-snug text-slate-900">
                {data?.nombre ?? "Cargando…"}
              </h2>
              {data?.marca && (
                <span className="text-xs text-slate-400">{data.marca}</span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
          >
            <X size={20} />
          </button>
        </div>

        {/* Contenido */}
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          {/* Skeleton solo cuando no hay NADA que mostrar todavía */}
          {!data && cargando && (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-28 animate-pulse rounded-xl bg-white" />
              ))}
            </div>
          )}
          {/* Barra sutil mientras se completa el detalle sobre datos parciales */}
          {data && cargando && (
            <div className="flex items-center gap-2 rounded-lg bg-white px-3 py-1.5 text-xs text-slate-400">
              <RefreshCw size={12} className="animate-spin" /> Actualizando…
            </div>
          )}
          {/* Decirlo es obligatorio: sin este aviso, un canal sin su bloque de
              precio y margen se lee como "esta publicación no tiene precio". */}
          {pubsFallaron && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                No se pudieron leer las publicaciones por tienda: abajo va el
                precio del detalle, <strong>sin oferta ni margen</strong>. No
                significa que no haya descuento.
              </span>
            </div>
          )}
          {/* El precio de ML se pidió confirmar y NO se pudo. Se muestra lo
              guardado DICIENDO que es lo guardado: ámbar y ⚠, el mismo trato
              que el costo implausible, nunca rojo/verde que se lee como un
              hecho. Un cajón que no abre es peor que uno con el dato de hace
              una hora, así que esto jamás es un error de carga.
              `al_dia` es lo ÚNICO que se mira: no se deduce de `estado`. */}
          {refresco && !refresco.al_dia && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                Precios de Mercado Libre <strong>sin confirmar</strong>:{" "}
                {motivoRefresco(refresco)}. Abajo va lo último guardado, que
                puede no ser lo que la tienda cobra en este momento.
              </span>
            </div>
          )}

          {data?.canales.map((c, idx) => {
            const info = cfg(c.canal);
            const color = info?.color ?? "#64748b";
            const texto = info?.color_texto ?? "#fff";
            const refrescable = c.canal === "mercado_libre" || c.canal === "amazon";
            const misPubs = pubsDe(c);

            return (
              <section
                // La llave lleva la cuenta: Mercado Libre trae UNA tarjeta por
                // cuenta y con `c.canal` a secas React veía dos hijos con la
                // misma llave.
                key={`${c.canal}-${cuentaDe(c) ?? c.item_id ?? idx}`}
                className="overflow-hidden rounded-xl border border-slate-200 bg-white"
              >
                {/* Encabezado del canal */}
                <header
                  className="flex items-center justify-between px-4 py-2.5"
                  style={{ backgroundColor: color, color: texto }}
                >
                  <div className="flex items-center gap-2 font-bold">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: texto }}
                    />
                    {info?.label ?? c.canal}
                  </div>
                  <div className="flex items-center gap-2">
                    {c.publicado ? (
                      <span className="rounded-full bg-white/25 px-2 py-0.5 text-[11px] font-bold">
                        {c.estado ?? "publicado"}
                      </span>
                    ) : (
                      <span className="rounded-full bg-black/20 px-2 py-0.5 text-[11px] font-bold">
                        sin publicar
                      </span>
                    )}
                    {refrescable && (
                      <button
                        onClick={() => refrescar(c.canal)}
                        title="Refrescar en vivo desde la API"
                        className="rounded-full bg-white/20 p-1.5 transition-colors hover:bg-white/35"
                      >
                        <RefreshCw
                          size={13}
                          className={refrescando === c.canal ? "animate-spin" : ""}
                        />
                      </button>
                    )}
                  </div>
                </header>

                {/* Métricas por canal:
                    - Mercado Libre usa FULL (no FBA)
                    - Amazon usa FBA (no FULL)
                    - General solo stock propio */}
                {(() => {
                  const esML = c.canal === "mercado_libre";
                  const esAmazon = c.canal === "amazon";
                  // TikTok Shop MX trabaja con almacenes DEL VENDEDOR: no tiene
                  // equivalente a FULL ni a FBA, así que no lleva tercera
                  // columna. Decirlo es mejor que dejar un "—" que se lee como
                  // "no sabemos".
                  const esTikTok = c.canal === "tiktok";
                  // En General, junto al stock real (que NO se toca), va la suma
                  // de FULL/FBA del producto — solo si tiene piezas en bodegas.
                  const esGeneral = c.canal === "general";
                  const conFullFba = esGeneral && fullFbaTotal > 0;
                  const stockReal = c.stock_real ?? c.stock;
                  // El precio sale del bloque de publicaciones cuando lo hay:
                  // ahí es UNO POR PUBLICACIÓN, con su oferta. Un "Precio" de
                  // canal al lado tendría que elegir uno de los dos de un SKU
                  // con dos listados y contradecir al bloque de abajo.
                  const conPrecio = misPubs.length === 0;
                  const columnas =
                    (conPrecio ? 1 : 0) + 1 + (esML || esAmazon || conFullFba ? 1 : 0);
                  return (
                    <>
                      <div
                        className={[
                          "grid divide-x divide-slate-100 border-b border-slate-100",
                          columnas >= 3 ? "grid-cols-3" : columnas === 2 ? "grid-cols-2" : "grid-cols-1",
                        ].join(" ")}
                      >
                        {conPrecio && (
                          <Metric icon={<Tag size={14} />} label="Precio" moneda="MXN" valor={precioMXN(c.precio)} />
                        )}
                        <Metric
                          icon={<Boxes size={14} />}
                          label="Stock real"
                          valor={stockReal != null ? `${stockReal} u` : "—"}
                        />
                        {conFullFba && (
                          <Metric
                            icon={<Truck size={14} />}
                            label="FULL/FBA"
                            valor={`${fullFbaTotal} u`}
                            destacado
                          />
                        )}
                        {esML && (
                          <Metric
                            icon={<Truck size={14} />}
                            label="FULL"
                            valor={c.stock_full != null ? `${c.stock_full} u` : "—"}
                            destacado={!!c.stock_full}
                          />
                        )}
                        {esAmazon && (
                          <Metric
                            icon={<Truck size={14} />}
                            label="FBA"
                            valor={c.stock_fba != null ? `${c.stock_fba} u` : "—"}
                            destacado={!!c.stock_fba}
                          />
                        )}
                      </div>

                      {/* Total y situación */}
                      {(c.stock_real != null || c.stock_full != null || c.stock_fba != null) && (
                        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-xs">
                          <span className="text-slate-500">
                            Total ={" "}
                            <span className="font-bold text-slate-700">
                              {(c.stock_real ?? 0) + (c.stock_full ?? 0) + (c.stock_fba ?? 0)} u
                            </span>{" "}
                            <span className="text-slate-400">
                              {esML ? "(real + FULL)"
                                : esAmazon ? "(real + FBA)"
                                : esTikTok ? "(nuestra bodega — TikTok no tiene FULL/FBA)"
                                : ""}
                            </span>
                          </span>
                          {c.situacion && (
                            <span className="rounded-full bg-slate-200 px-2 py-0.5 font-semibold uppercase tracking-wide text-slate-600">
                              {c.situacion}
                            </span>
                          )}
                        </div>
                      )}

                      {/* MARGEN BRUTO — precio del canal contra el costo del SKU,
                          SIN comisión ni envío (pedido de Eduardo). El neto ya
                          vive en Análisis; aquí interesa "cuánto deja el producto
                          antes de que el marketplace cobre lo suyo".

                          Misma fórmula que el `margen_pct` de Análisis —
                          (precio − costo) / precio— a propósito: si aquí se
                          dividiera el precio entre 1.16 para descontar el IVA, el
                          MISMO SKU mostraría dos márgenes distintos en dos
                          pantallas y nadie sabría cuál creer. La advertencia del
                          IVA va en el tooltip, que es donde no estorba.

                          El costo es del SKU, no del canal: la misma cifra en
                          todas las tarjetas, y solo cambia el precio. */}
                      {(() => {
                        const costo = data?.costo ?? null;
                        const precio = c.precio;
                        if (!(costo && costo > 0) || !(precio && precio > 0)) return null;
                        const dudoso = costoImplausible(precio, costo);
                        // SIN IVA: el precio lo trae y el costo no. Ver lib/margen.
                        const neto = precioSinIva(precio);
                        const pct = margenBruto(precio, costo) ?? 0;
                        const deja = neto - costo;
                        const ayuda = dudoso
                          ? avisoCostoImplausible(precio, costo)
                          : `De los ${precioMXN(precio)} que paga el cliente, `
                            + `${precioMXN(precio - neto)} son IVA que va al SAT.
`
                            + `Entra a Kubera: ${precioMXN(neto)} — costo `
                            + `${precioMXN(costo)} (producto + flete) = ${precioMXN(deja)}.
`
                            + `NO descuenta comisión del canal ni envío: para eso está el `
                            + `margen neto de Análisis.`;
                        return (
                          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2 text-xs"
                               title={ayuda}>
                            <span className="text-slate-500">
                              Margen bruto{" "}
                              <span className="text-slate-400">(sin IVA ni comisiones)</span>
                            </span>
                            <span className="flex items-center gap-2">
                              <span className="text-slate-400">{precioMXN(deja)} / u</span>
                              <span className={[
                                "rounded px-1.5 py-0.5 font-bold tabular-nums",
                                dudoso ? "bg-amber-50 text-amber-700"
                                       : pct >= 0 ? "bg-emerald-50 text-emerald-700"
                                                  : "bg-rose-50 text-rose-700",
                              ].join(" ")}>
                                {dudoso && "⚠ "}{pct.toFixed(1)}%
                              </span>
                            </span>
                          </div>
                        );
                      })()}
                    </>
                  );
                })()}

                {/* Lo que cobra HOY cada publicación de este canal, y lo que
                    deja si se vende una ahora. Por publicación, no por canal. */}
                <PublicacionesDelCanal
                  pubs={misPubs}
                  aviso={avisoMargen}
                  nota={notaSi(c.canal, misPubs)}
                  color={color}
                  cargando={pubsCargando}
                  // El pie de esta sección ya trae el mismo enlace junto al id.
                  conEnlace={false}
                  // La confirmación es contra Mercado Libre y sólo de ML: decir
                  // "confirmando precio" en la tarjeta de Amazon sería afirmar
                  // algo que no está pasando.
                  confirmando={confirmandoPrecio && c.canal === "mercado_libre"}
                />

                {/* Categoría multinivel */}
                <div className="px-4 py-3">
                  <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    Categoría
                  </div>
                  {c.categoria_path.length ? (
                    <div className="cat-breadcrumb text-sm text-slate-700">
                      {c.categoria_path.map((n, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <ChevronRight size={13} className="text-slate-300" />}
                          <span className="font-medium">{n.nombre}</span>
                        </span>
                      ))}
                    </div>
                  ) : c.categoria_id ? (
                    <span className="font-mono text-sm text-slate-600">{String(c.categoria_id)}</span>
                  ) : (
                    <span className="text-sm text-slate-400">Sin categoría</span>
                  )}
                </div>

                {/* Footer: id + link */}
                {(c.item_id || c.url) && (
                  <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50 px-4 py-2.5">
                    <span className="font-mono text-xs text-slate-500">
                      {c.item_id ?? ""}
                    </span>
                    {enlacePublicacion(c.canal, c.item_id, c.url) && (
                      <a
                        href={enlacePublicacion(c.canal, c.item_id, c.url)!}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 text-xs font-semibold"
                        style={{ color }}
                      >
                        Ver publicación <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                )}
              </section>
            );
          })}

          {/* Publicaciones que el detalle 360° no trae. El detalle muestra una
              tarjeta por CUENTA de Mercado Libre, así que un segundo listado en
              la misma cuenta no tenía dónde verse — y puede ser justo el que
              está vendiendo. Se pinta aparte antes que esconderlo. */}
          {Object.entries(gruposHuerfanos).map(([canal, lista]) => {
            const info = cfg(canal);
            const color = info?.color ?? "#64748b";
            const texto = info?.color_texto ?? "#fff";
            return (
              <section
                key={`huerfanas-${canal}`}
                className="overflow-hidden rounded-xl border border-slate-200 bg-white"
              >
                <header
                  className="flex items-center justify-between px-4 py-2.5"
                  style={{ backgroundColor: color, color: texto }}
                >
                  <div className="flex items-center gap-2 font-bold">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: texto }}
                    />
                    {info?.label ?? canal}
                  </div>
                  <span
                    className="rounded-full bg-black/20 px-2 py-0.5 text-[11px] font-bold"
                    title="Esta publicación existe en el canal, pero el detalle 360° no la lista (muestra una por cuenta)."
                  >
                    otra publicación
                  </span>
                </header>
                <PublicacionesDelCanal
                  pubs={lista}
                  aviso={avisoMargen}
                  nota={notaSi(canal, lista)}
                  color={color}
                />
              </section>
            );
          })}
        </div>
      </aside>
    </div>
  );
}

function Metric({
  icon,
  label,
  valor,
  destacado,
  moneda,
}: {
  icon: React.ReactNode;
  label: string;
  valor: string;
  destacado?: boolean;
  /** Marca la divisa cuando el dato es dinero. Sin esto un "$1.00" no dice si
      son pesos o dólares, y en este panel conviven las dos. */
  moneda?: Moneda;
}) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-2 py-3 text-center">
      <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        {icon} {label}
        {moneda && <ChipMoneda moneda={moneda} />}
      </span>
      <span
        className={[
          "text-sm font-bold",
          destacado ? "text-emerald-600" : "text-slate-800",
        ].join(" ")}
      >
        {valor}
      </span>
    </div>
  );
}
