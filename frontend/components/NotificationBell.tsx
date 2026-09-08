"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Bell, X, ShoppingCart, Package, Truck, RotateCw,
  TrendingDown, AlertTriangle, Check,
} from "lucide-react";
import { notificacionesWebhook } from "@/lib/api";
import type { WebhookEvento } from "@/lib/types";

const LS_KEY = "omnicanal_ult_notif";

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

export default function NotificationBell() {
  const [eventos, setEventos] = useState<WebhookEvento[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [ultVisto, setUltVisto] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

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
                const clase = "flex gap-3 border-b border-slate-50 px-4 py-2.5 hover:bg-slate-50";
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
