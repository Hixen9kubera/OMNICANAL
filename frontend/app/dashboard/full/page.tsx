"use client";

/**
 * /dashboard/full — FULL: el inventario que vive en las bodegas de Mercado Libre
 * (Kubera y San Corpe). La cadena (salidas de Odoo → en camino → FULL vendible →
 * vendido hoy), los avisos de ML de las últimas 24 h, la salud de la
 * sincronización y el libro diario que cuadra los avisos contra la foto del sync.
 *
 * FULL NO TOCA WOO: aquí solo se mira. Lo arman `GET /api/fanout/full` (kubera,
 * cada minuto) y `GET /api/fanout/full/camino` (las salidas de Odoo, cada 5 min,
 * del mismo caché que la pestaña Fulfillment). Tocar un SKU abre su
 * trazabilidad en el carril FULL.
 */

import { useCallback, useEffect, useState } from "react";
import { Clock, Info, Warehouse } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";
import BannerFanout, { ACCION_BANNER } from "@/components/fanout/BannerFanout";
import CadenaFull from "@/components/fanout/CadenaFull";
import CuadreFull from "@/components/fanout/CuadreFull";
import FanoutPestanas from "@/components/fanout/FanoutPestanas";
import HorarioFull from "@/components/fanout/HorarioFull";
import RastroCambio from "@/components/fanout/RastroCambio";
import SaludFull, { PorHoraFull } from "@/components/fanout/SaludFull";
import TrazabilidadSku from "@/components/fanout/TrazabilidadSku";
import type { CaminoFull, CuentaSel, ResumenFull } from "@/components/fanout/tipos";

export default function PaginaFull() {
  const [d, setD] = useState<ResumenFull | null>(null);
  const [camino, setCamino] = useState<CaminoFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cuenta, setCuenta] = useState<CuentaSel>("ambas");
  const [traza, setTraza] = useState<string | null>(null);
  const [rastro, setRastro] = useState<{ sku: string; fin: string } | null>(null);

  const cargar = useCallback(async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/full`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setD(await r.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  const cargarCamino = useCallback(async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fanout/full/camino`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setCamino(await r.json());
    } catch (e) {
      setCamino({ ok: false, motivo: e instanceof Error ? e.message : "no contestó" });
    }
  }, []);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 60_000);
    return () => clearInterval(t);
  }, [cargar]);

  useEffect(() => {
    void cargarCamino();
    const t = setInterval(() => void cargarCamino(), 300_000);
    return () => clearInterval(t);
  }, [cargarCamino]);

  const total = d ? d.cuentas.reduce((s, c) => s + c.piezas, 0) : null;
  const ultimo = d?.salud.ultimo_aviso;

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-6 sm:px-6">
        <FanoutPestanas />
        <BannerFanout
          icono={<Warehouse size={28} aria-hidden />}
          titulo="FULL"
          texto="Las bodegas de Mercado Libre: lo que hay, lo que va llegando y lo que sale, aviso por aviso."
          acciones={
            <span className={ACCION_BANNER}>
              <Clock size={14} aria-hidden />
              {d ? `Último aviso de ML ${ultimo && ultimo.slice(0, 10) === d.hoy ? `a las ${ultimo.slice(11, 16)}` : "hace más de un día"} · se actualiza cada minuto` : "Leyendo FULL…"}
            </span>
          }
          cifra={total != null ? total.toLocaleString("es-MX") : "—"}
          cifraTexto="piezas vendibles en FULL"
        />

        <p className="flex items-start gap-2.5 rounded-2xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-[13px] leading-5 text-indigo-950">
          <Info size={18} className="mt-px shrink-0 text-indigo-700" aria-hidden />
          <span>
            <b>FULL no toca Woo.</b> Es inventario de Mercado Libre: la venta FULL nace con su stock ya descontado y Woo sigue
            copiando a Odoo. Con nuestra bodega solo cruzan dos cosas: las <b>salidas de Odoo a FULL</b>, que restan en Odoo al
            validarse, y los <b>retiros</b>, que regresan con una recepción en Odoo.
          </span>
        </p>

        {error && !d && (
          <p className="rounded-2xl bg-white p-6 text-sm text-rose-800 shadow-sm">No se pudo leer FULL ({error}). Se reintenta en un minuto.</p>
        )}
        {!d && !error && <p className="rounded-2xl bg-white p-6 text-sm text-slate-600 shadow-sm">Leyendo los avisos de FULL…</p>}

        {d && (
          <>
            <CadenaFull d={d} camino={camino} cuenta={cuenta} onCuenta={setCuenta} />
            <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]">
              <HorarioFull avisos={d.avisos} hoy={d.hoy} cuenta={cuenta} onSku={setTraza} />
              <div className="flex flex-col gap-4">
                <PorHoraFull porHora={d.por_hora} />
                <SaludFull d={d} camino={camino} />
              </div>
            </div>
            <CuadreFull libro={d.libro} umbral={d.umbral} cuenta={cuenta} hora={d.hora} hoy={d.hoy} />
          </>
        )}
      </main>
      <TrazabilidadSku sku={traza} carrilInicial="full" onCerrar={() => setTraza(null)}
        onRastro={(sku, fin) => { setTraza(null); setRastro({ sku, fin }); }} />
      <RastroCambio sel={rastro} onCerrar={() => setRastro(null)} onIr={(sku, fin) => setRastro({ sku, fin })}
        onTrazabilidad={(sku) => { setRastro(null); setTraza(sku); }} />
    </div>
  );
}
