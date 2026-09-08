"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Bell, X, ShoppingCart, Package, Truck, RotateCw,
  TrendingDown, AlertTriangle, Check, ChevronDown, ArrowRight,
} from "lucide-react";
import {
  notificacionesWebhook, alertaMargenNegativo, alertaCostoSinValidar,
  type AlertaMargenResp, type AlertaCostoResp,
} from "@/lib/api";
import type { WebhookEvento } from "@/lib/types";

const LS_KEY = "omnicanal_ult_notif";

// Las dos alertas de costo se DESPLIEGAN en la campana con los SKUs de hoy.
// El resto sigue llevando directo al producto: no tienen lista que abrir.
const DESPLEGABLES = ["margen_negativo", "top_costo_sin_revisar"];

function pesos(v: number | null | undefined): string {
  return v == null ? "—" : new Intl.NumberFormat("es-MX", {
    style: "currency", currency: "MXN", maximumFractionDigits: 0,
  }).format(v);
}

// Las alertas de DINERO se ven distinto del resto, a propósito: son las que
// piden que alguien haga algo hoy. El resto de topics quedan por si vuelve a
// entrar otro canal a la campana.
function iconoTopic(topic: string | null) {
  switch (topic) {
    case "margen_negativo": return <TrendingDown size={15} className="text-rose-500" />;
    case "top_costo_sin_revisar": return <AlertTriangle size={15} className="text-amber-500" />;
    case "orders_v2": return <ShoppingCart size={15} className="text-emerald-500" />;
    case "items":
    case "items_prices": return <Package size={15} className="text-indigo-500" />;
    case "shipments": return <Truck size={15} className="text-sky-500" />;
    default: return <Bell size={15} className="text-slate-400" />;
  }
}

function etiquetaTopic(topic: string | null): string {
  const map: Record<string, string> = {
    margen_negativo: "Margen negativo",
    top_costo_sin_revisar: "Costo sin verificar",
    silencio_ventas: "Silencio de ventas",
    pedidos_duplicados: "Pedidos duplicados",
    tokens_rancios: "Tokens por vencer",
    orders_v2: "Venta",
    items: "Cambio de publicación",
    items_prices: "Cambio de precio",
    shipments: "Envío",
    post_purchase: "Postventa / reclamo",
    questions: "Pregunta",
    messages: "Mensaje",
  };
  return topic ? (map[topic] ?? topic) : "Aviso";
}

function hace(iso: string): string {
  const d = new Date(iso);
  const seg = Math.floor((Date.now() - d.getTime()) / 1000);
  if (seg < 60) return "hace un momento";
  if (seg < 3600) return `hace ${Math.floor(seg / 60)} min`;
  if (seg < 86400) return `hace ${Math.floor(seg / 3600)} h`;
  return d.toLocaleDateString("es-MX");
}

/**
 * El detalle de una alerta de costo: los SKUs que la provocan HOY.
 *
 * Se calcula al vuelo (`/api/alertas/*`), no se guarda. La lista sale de los
 * mismos censos que la alarma diaria, así que no puede contradecirla.
 */
function DetalleAlerta({ topic, datos, error, onIr }: {
  topic: string;
  datos: AlertaMargenResp | AlertaCostoResp | null;
  error: string | null;
  onIr: () => void;
}) {
  if (error) {
    return <p className="bg-slate-50 px-4 py-3 text-xs text-rose-600">{error}</p>;
  }
  if (!datos) {
    return (
      <p className="bg-slate-50 px-4 py-3 text-xs text-slate-400">
        Calculando el estado de hoy…
      </p>
    );
  }
  if (!datos.items.length) {
    return (
      <p className="bg-slate-50 px-4 py-3 text-xs text-slate-500">
        Ya no queda ninguna. La alerta es de una corrida anterior.
      </p>
    );
  }

  const esMargen = topic === "margen_negativo";
  // A DÓNDE lleva cada alerta (Eduardo, 7-sep): el costo sin verificar se
  // revisa en ANÁLISIS, que es donde el costo y el margen están lado a lado y
  // desde donde se abre el costeo; Omnicanal es para la publicación. El margen
  // negativo sigue yendo a la publicación, que es lo que hay que mover.
  const esCosto = topic === "top_costo_sin_revisar";
  const destino = esCosto ? "/analisis" : "/omnicanal";
  const pantalla = esCosto ? "Análisis" : "Omnicanal";
  return (
    <div className="border-b border-slate-100 bg-slate-50 px-4 py-2">
      {/* Un solo clic para verlas TODAS: las dos pantallas aceptan `?skus=`
          con la lista separada por comas. Es la acción principal — la lista
          de abajo es para atacar una en concreto. */}
      <Link
        href={`${destino}?skus=${encodeURIComponent(datos.skus.join(","))}`}
        onClick={onIr}
        className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-indigo-600 hover:text-indigo-700"
      >
        Ver {datos.items.length === 1 ? (esCosto ? "el costo" : "la publicación") : `las ${datos.items.length}`} en {pantalla}
        <ArrowRight size={12} />
      </Link>

      <ul className="space-y-1">
        {datos.items.map((it) => (
          <li key={`${it.sku}-${"canal" in it ? it.canal : it.rank}`}>
            <Link
              href={`${destino}?skus=${encodeURIComponent(it.sku)}`}
              onClick={onIr}
              className="flex items-baseline justify-between gap-2 rounded px-1.5 py-1 hover:bg-white"
            >
              <span className="truncate font-mono text-[11px] text-slate-600">{it.sku}</span>
              {"margen_pct" in it ? (
                <span className="flex shrink-0 items-baseline gap-1.5 text-[11px]">
                  {/* El COSTO DUDOSO se marca aparte porque pide lo contrario:
                      ahí se revisa el costeo, no se baja la publicación. */}
                  {it.dudoso && (
                    <span className="rounded bg-amber-100 px-1 text-[9px] font-semibold text-amber-700">
                      costo {it.veces_precio}×
                    </span>
                  )}
                  <span className="text-slate-400">{pesos(it.precio)}</span>
                  <span className="font-semibold text-rose-600">{it.margen_pct}%</span>
                </span>
              ) : (
                <span className="flex shrink-0 items-baseline gap-1.5 text-[11px]">
                  <span className="text-slate-400">{it.unidades} uds</span>
                  <span className="font-semibold text-amber-600">#{it.rank} {it.donde}</span>
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>

      {esMargen && "evaluadas" in datos && (
        // El universo va SIEMPRE: "8 en negativo" sobre 2 evaluadas de 781 no
        // significa lo mismo que sobre 781 de 781.
        <p className="mt-2 text-[10px] leading-snug text-slate-400">
          {datos.perdida_real} pérdida real · {datos.costo_dudoso} costo dudoso ·
          evaluadas {datos.evaluadas} de {datos.universo} publicaciones comprables
        </p>
      )}
    </div>
  );
}

export default function NotificationBell() {
  const [eventos, setEventos] = useState<WebhookEvento[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [ultVisto, setUltVisto] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  // Qué alerta está desplegada y su detalle. `null` en `detalle` = cargando.
  const [expandida, setExpandida] = useState<number | null>(null);
  const [detalle, setDetalle] = useState<AlertaMargenResp | AlertaCostoResp | null>(null);
  const [errDetalle, setErrDetalle] = useState<string | null>(null);

  const desplegar = useCallback((e: WebhookEvento) => {
    if (expandida === e.id) { setExpandida(null); return; }
    setExpandida(e.id);
    setDetalle(null);
    setErrDetalle(null);
    const p = e.topic === "margen_negativo"
      ? alertaMargenNegativo() : alertaCostoSinValidar();
    p.then((r) => setDetalle(r))
     .catch(() => setErrDetalle("No se pudo leer el detalle."));
  }, [expandida]);

  const cargar = useCallback(() => {
    notificacionesWebhook()
      .then((r) => {
        setEventos(r.eventos);
      })
      .catch(() => {});
  }, []);

  // Poll cada 30 s
  useEffect(() => {
    const v = Number(localStorage.getItem(LS_KEY) || 0);
    setUltVisto(v);
    cargar();
    const t = setInterval(cargar, 30000);
    return () => clearInterval(t);
  }, [cargar]);

  // Cerrar al hacer click fuera
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setAbierto(false);
    };
    if (abierto) document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [abierto]);

  const noLeidas = eventos.filter((e) => e.id > ultVisto).length;

  function abrir() {
    setAbierto((v) => !v);
    if (!abierto && eventos.length) {
      const maxId = Math.max(...eventos.map((e) => e.id));
      localStorage.setItem(LS_KEY, String(maxId));
      setUltVisto(maxId);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={abrir}
        title="Alertas"
        className="relative flex h-9 w-9 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
      >
        <Bell size={19} />
        {noLeidas > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {noLeidas > 9 ? "9+" : noLeidas}
          </span>
        )}
      </button>

      {abierto && (
        <div className="absolute right-0 z-50 mt-2 w-96 animate-fade-in overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card-hover">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            {/* "Alertas" y no "Notificaciones": desde el 7-sep aquí solo entra
                lo que pide que alguien haga algo. Y sin el contador de "N hoy",
                que sobre una lista casi siempre vacía solo decía "0". */}
            <div>
              <h4 className="text-sm font-bold text-slate-800">Alertas</h4>
              <span className="text-[11px] text-slate-400">
                Avisos que piden acción
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button onClick={cargar} title="Actualizar" className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <RotateCw size={14} />
              </button>
              <button onClick={() => setAbierto(false)} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {/* El vacío es el estado SANO, no un error: la campana pasa la
                mayor parte del tiempo así. Por eso dice "todo en orden" y no
                "sin notificaciones todavía", que se lee como si algo fallara. */}
            {eventos.length === 0 ? (
              <div className="px-6 py-10 text-center">
                <Check size={22} className="mx-auto mb-2 text-emerald-500" />
                <p className="text-sm font-medium text-slate-600">Todo en orden.</p>
                <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                  Aquí aparecen los márgenes en negativo, los costos sin
                  verificar de lo más vendido y los avisos del sistema.
                </p>
              </div>
            ) : (
              eventos.map((e) => {
                const fila = (
                  <>
                    <div className="mt-0.5">{iconoTopic(e.topic)}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-slate-700">
                          {etiquetaTopic(e.topic)}
                        </span>
                        <span className="shrink-0 text-[10px] text-slate-400">{hace(e.recibido)}</span>
                      </div>
                      {e.resultado && (
                        <p className="text-xs leading-snug text-slate-500">{e.resultado}</p>
                      )}
                      {e.sku && (
                        <span className="mt-0.5 inline-block rounded bg-slate-100 px-1.5 font-mono text-[10px] text-slate-500">
                          {e.sku}
                        </span>
                      )}
                    </div>
                  </>
                );
                const clase = "flex w-full gap-3 border-b border-slate-50 px-4 py-2.5 text-left hover:bg-slate-50";

                // Las dos de costo se DESPLIEGAN: traen una lista de SKUs y el
                // aviso solo nombra al peor. Abrirlas en el sitio evita la
                // pregunta de siempre — "¿cuáles son las otras siete?".
                if (e.topic && DESPLEGABLES.includes(e.topic)) {
                  const abierta = expandida === e.id;
                  return (
                    <div key={e.id}>
                      <button type="button" onClick={() => desplegar(e)} className={clase}>
                        {fila}
                        <ChevronDown
                          size={14}
                          className={`mt-1 shrink-0 text-slate-400 transition-transform ${abierta ? "rotate-180" : ""}`}
                        />
                      </button>
                      {abierta && (
                        <DetalleAlerta
                          topic={e.topic}
                          datos={detalle}
                          error={errDetalle}
                          onIr={() => setAbierto(false)}
                        />
                      )}
                    </div>
                  );
                }

                // Con SKU la alerta LLEVA al producto: el aviso dice que algo
                // está mal, y el clic es el camino a arreglarlo. Sin SKU (un
                // aviso del sistema) no hay a dónde ir y no se hace clicable.
                return e.sku ? (
                  <Link
                    key={e.id}
                    href={`/omnicanal?skus=${encodeURIComponent(e.sku)}`}
                    onClick={() => setAbierto(false)}
                    className={clase}
                  >
                    {fila}
                  </Link>
                ) : (
                  <div key={e.id} className={clase}>{fila}</div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
