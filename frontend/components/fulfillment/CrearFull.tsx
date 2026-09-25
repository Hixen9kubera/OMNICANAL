"use client";

/**
 * FULLFILMENT · CREAR FULL — la PLANEACIÓN SEMANAL por tienda (Brandon, 24-sep-2026).
 *
 *   · TIENDAS: ML Kubera, ML San Corpe, Amazon FBA y Walmart WFS, cada una con SUS
 *     publicaciones y un interruptor: sólo se planea lo de las tiendas activas.
 *     Temu y TikTok no entran (son sólo DROP).
 *   · SALDO: en FULL hoy (las dos cuentas), publicaciones sin FULL, lo que se está
 *     mandando ESTA semana y lo que hay por mandar según la planeación.
 *   · PLANEACIÓN: las reglas del prompt estándar (`proponer.ts`), editable renglón
 *     por renglón; buscar y agregar SKUs publicados; el AGENTE de planeación (IA con
 *     instrucciones libres y seguimiento, sobre la planeación completa con precios).
 *   · CREAR: vista previa con Odoo y ML releídos, modo prueba, una orden por tienda
 *     y almacén, y la guía del marketplace después. Detrás del interruptor.
 *
 * SÓLO PARA CREAR FULLs (Brandon, 24-sep, v0.570.0): los totales, los ganadores sin
 * existencia con su reemplazo, los títulos que no coinciden con Odoo y las órdenes
 * sin completar se ven en Análisis. Esta pantalla se los pasa con `onPlan` y se
 * queda montada aunque se cambie de pantalla (page.tsx), para no perder lo editado.
 * Un SKU se puede QUITAR de la planeación (y restaurar), y un reemplazo agregado
 * desde Análisis o la IA queda marcado «REEMPLAZO de …».
 *
 * Los insumos: `GET /api/fulfillment/crear-full` (backend/services/fulfillment_full.py).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, Boxes, ClipboardCopy, Download, ExternalLink, PackageX, Power, RotateCcw, Search,
  Sparkles, Trash2, Truck,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import type { PlanAnalisis } from "./AnalisisPlaneacion";
import BuscarSku from "./BuscarSku";
import ConfirmarFull, { GuiaOrden } from "./ConfirmarFull";
import PanelIA, { datosParaIA } from "./RevisionIA";
import type { TurnoIA } from "./RevisionIA";
import { claveDe, planear, totalesDe } from "./proponer";
import type { Renglon, Totales } from "./proponer";
import { Ayuda, Ceja, FONDO_RAYADO, PUNTO_CUENTA, Tarjeta, dia, num, pesos, rangoSemana } from "./ui";
import type {
  FilaPlan, Interruptor, ParametrosFull, PropuestaFull, RevisionIA, Rol, StockHoy, Tienda,
} from "./tipos";
import { TIENDAS } from "./tipos";

type Filtro = "mandar" | "recorte" | "pendiente" | "ganadores" | "cubiertos" | "todos" | "quitados";

const FILTROS: { k: Filtro; t: string; titulo: string }[] = [
  { k: "mandar", t: "Por mandar", titulo: "Lo que la planeación manda, más lo que agregaste o cambiaste a mano." },
  { k: "recorte", t: "Recorte", titulo: "Piden más de lo que Odoo tiene libre: va lo que hay." },
  { k: "pendiente", t: "Pendiente", titulo: "Sin dato de Odoo (el SKU no está en Odoo): no es un cero." },
  { k: "ganadores", t: "Ganadores agotados", titulo: "Vendieron bien y no hay ni en Odoo ni en el almacén: se sugiere reemplazo." },
  { k: "cubiertos", t: "Ya cubiertos", titulo: "Lo que hay, lo que va en camino y los borradores alcanzan la cobertura." },
  { k: "todos", t: "Todos", titulo: "Todos los SKUs de la planeación de las tiendas activas." },
  { k: "quitados", t: "Quitados", titulo: "Los SKUs que quitaste de la planeación: se pueden restaurar." },
];

const COLOR_TIENDA: Record<Tienda, string> = {
  "meli:Kubera": PUNTO_CUENTA.Kubera, "meli:San Corpe": PUNTO_CUENTA["San Corpe"], amazon: "#FF9900", walmart: "#0071DC",
};

const NOTA_TIENDA: Partial<Record<Tienda, string>> = {
  amazon: "stock FBA del sync de 15 min",
  walmart: "sin ventas registradas y el stock de WFS no se puede leer (401): sólo manual",
};

const LLAVE_TIENDAS = "fulfillment.tiendas_activas";

function leerActivas(): Record<Tienda, boolean> {
  const base = { "meli:Kubera": true, "meli:San Corpe": true, amazon: false, walmart: false } as Record<Tienda, boolean>;
  try {
    const guardado = JSON.parse(window.localStorage.getItem(LLAVE_TIENDAS) ?? "null");
    return guardado ? { ...base, ...guardado } : base;
  } catch {
    return base;
  }
}

export interface PedidoReemplazo { id: number; tienda: Tienda; sku: string; de: string }

export default function CrearFull({ stock, rol, recarga, onEstado, onPlan, reemplazoPedido, onReemplazoHecho }: {
  stock: StockHoy | null | undefined;
  rol: Rol;
  recarga: number;
  onEstado?: (e: { skus: number; piezas: number; tiendas: number } | null) => void;
  /** Lo que se ve en Análisis · «Planeación de la semana». */
  onPlan?: (p: PlanAnalisis | null) => void;
  /** Un reemplazo pedido desde Análisis: se marca aquí y se avisa con `onReemplazoHecho`. */
  reemplazoPedido?: PedidoReemplazo | null;
  onReemplazoHecho?: (id: number) => void;
}) {
  const [datos, setDatos] = useState<PropuestaFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [ventana, setVentana] = useState(30);
  const [params, setParams] = useState<ParametrosFull | null>(null);
  const [activas, setActivas] = useState<Record<Tienda, boolean>>({ "meli:Kubera": true, "meli:San Corpe": true,
                                                                   amazon: false, walmart: false });
  const [vista, setVista] = useState<Tienda | "todas">("todas");
  const [editadas, setEditadas] = useState<Record<string, number>>({});
  const [agregadas, setAgregadas] = useState<FilaPlan[]>([]);
  // Quitados de la planeación (se pueden restaurar) y los que entraron como REEMPLAZO de otro.
  const [quitadas, setQuitadas] = useState<Set<string>>(new Set());
  const [reemplazoDe, setReemplazoDe] = useState<Record<string, string>>({});
  const [filtro, setFiltro] = useState<Filtro>("mandar");
  const [busca, setBusca] = useState("");
  const [revisar, setRevisar] = useState(false);
  // El agente de planeación: abierto o no, y la conversación (se conserva al cerrarlo).
  const [agente, setAgente] = useState(false);
  const [turnos, setTurnos] = useState<TurnoIA[]>([]);
  const [aviso, setAviso] = useState<string | null>(null);
  const iaVivo = useRef(0);

  useEffect(() => { setActivas(leerActivas()); }, []);
  const cambiarTienda = (t: Tienda) => setActivas((a) => {
    const n = { ...a, [t]: !a[t] };
    try { window.localStorage.setItem(LLAVE_TIENDAS, JSON.stringify(n)); } catch { /* sin almacenamiento */ }
    return n;
  });

  const cargar = useCallback(async (refrescar: boolean, v: number) => {
    setCargando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full?ventana=${v}${refrescar ? "&refrescar=true" : ""}`,
                                  { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
      const d = await r.json() as PropuestaFull;
      setDatos(d);
      setParams((p) => (p ? { ...p, ventana_dias: d.ventana.dias } : d.parametros));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, []);
  useEffect(() => { void cargar(recarga > 0, ventana); }, [cargar, recarga, ventana]);

  const tiendasActivas = TIENDAS.filter((t) => activas[t] && datos?.tiendas[t]);
  const todos = useMemo(() => {
    if (!datos || !params) return [];
    const filas = tiendasActivas.flatMap((t) => datos.tiendas[t].filas);
    const ya = new Set(filas.map((f) => claveDe(f.tienda, f.sku)));
    const extra = agregadas.filter((f) => activas[f.tienda] && !ya.has(claveDe(f.tienda, f.sku)));
    return planear(filas.concat(extra), params, new Set(extra.map((f) => claveDe(f.tienda, f.sku))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datos, params, agregadas, activas]);
  // Lo quitado no se planea, no se crea, no va al Excel ni a la IA.
  const renglones = useMemo(() => todos.filter((r) => !quitadas.has(r.clave)), [todos, quitadas]);
  const quitados = useMemo(() => todos.filter((r) => quitadas.has(r.clave)), [todos, quitadas]);
  const cantidad = useCallback((r: Renglon) => editadas[r.clave] ?? r.propuesta, [editadas]);
  const porTienda = useMemo(() => Object.fromEntries(tiendasActivas.map((t) => [t, totalesDe(
    renglones.filter((r) => r.tienda === t), cantidad)])) as Record<Tienda, Totales>,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [renglones, cantidad, activas]);
  const total = totalesDe(renglones, cantidad);

  useEffect(() => {
    onEstado?.(datos ? { skus: total.skus_a_mandar, piezas: total.a_mandar, tiendas: tiendasActivas.length } : null);
  }, [datos, total.skus_a_mandar, total.a_mandar, tiendasActivas.length, onEstado]);

  const enFiltro = (r: Renglon, f: Filtro) => {
    switch (f) {
      case "mandar": return cantidad(r) > 0 || (r.agregado ?? false) || r.clave in editadas;
      case "recorte": return r.estado === "recorte";
      case "pendiente": return r.estado === "pendiente";
      case "ganadores": return r.ganador_agotado;
      case "cubiertos": return r.estado === "cubierto";
      default: return true;
    }
  };
  const enVista = renglones.filter((r) => vista === "todas" || r.tienda === vista);
  const quitadosEnVista = quitados.filter((r) => vista === "todas" || r.tienda === vista);
  const q = busca.trim().toUpperCase();
  const visibles = (filtro === "quitados" ? quitadosEnVista : enVista.filter((r) => enFiltro(r, filtro)))
    .filter((r) => !q || r.sku.toUpperCase().includes(q) || (r.nombre ?? "").toUpperCase().includes(q))
    // El orden NO depende de lo tecleado: si dependiera, el renglón saltaría de lugar a media captura.
    .sort((a, b) => Number(b.clave in reemplazoDe) - Number(a.clave in reemplazoDe)
      || Number(b.agregado ?? false) - Number(a.agregado ?? false) || b.propuesta - a.propuesta
      || (a.aguanta ?? -1) - (b.aguanta ?? -1) || b.vv - a.vv || a.sku.localeCompare(b.sku));

  const fijar = (clave: string, v: string) => {
    const n = Math.max(0, Math.min(100_000, parseInt(v.replace(/[^\d]/g, ""), 10) || 0));
    setEditadas((e) => ({ ...e, [clave]: n }));
  };
  const hayCambios = Object.keys(editadas).length > 0 || agregadas.length > 0 || quitadas.size > 0;
  const enPlan = useMemo(() => new Set(renglones.map((r) => r.clave)), [renglones]);
  const enTodos = useMemo(() => new Set(todos.map((r) => r.clave)), [todos]);

  const quitar = (r: Renglon) => {
    setQuitadas((q) => new Set(q).add(r.clave));
    setAviso(`Quitaste ${r.sku} de ${datos?.tiendas[r.tienda]?.nombre ?? r.tienda}. Está en «Quitados» por si lo quieres de vuelta.`);
  };
  const restaurar = (r: Renglon) => {
    setQuitadas((q) => { const n = new Set(q); n.delete(r.clave); return n; });
    setAviso(`${r.sku} regresó a la planeación.`);
  };
  const agregar = (filas: FilaPlan[]) => {
    // Lo que se había quitado y se vuelve a pedir, se restaura.
    setQuitadas((q) => {
      const n = new Set(q);
      filas.forEach((f) => n.delete(claveDe(f.tienda, f.sku)));
      return n;
    });
    setAgregadas((a) => [...a, ...filas.filter((f) => !enTodos.has(claveDe(f.tienda, f.sku)))]);
    setAviso(`Agregado${filas.length > 1 ? "s" : ""}: ${filas.map((f) => f.sku).join(", ")}. Escribe cuántas piezas mandar.`);
    setFiltro("mandar");
  };
  const agregarPorSku = async (tienda: Tienda, sku: string, cant?: number, de?: string) => {
    setAviso(`Buscando ${sku} en ${datos?.tiendas[tienda]?.nombre ?? tienda} (Mercado Libre en vivo)…`);
    const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/buscar?tienda=${encodeURIComponent(tienda)}`
      + `&q=${encodeURIComponent(sku)}&ventana=${ventana}`, { cache: "no-store" });
    const d = r.ok ? await r.json() as { filas: FilaPlan[] } : { filas: [] };
    const f = d.filas.find((x) => x.sku === sku);
    if (!f) { setAviso(`${sku} no está publicado en ${datos?.tiendas[tienda]?.nombre ?? tienda}.`); return; }
    agregar([f]);
    if (cant !== undefined) setEditadas((e) => ({ ...e, [claveDe(tienda, sku)]: cant }));
    if (de) setReemplazoDe((m) => ({ ...m, [claveDe(tienda, sku)]: de }));
  };
  /**
   * Un REEMPLAZO: como vende en esa tienda, casi siempre ya es renglón de la
   * planeación; se marca «REEMPLAZO de …» y se pone en «Por mandar» para que se le
   * escriba cantidad. Si no estuviera, se busca y se agrega.
   */
  const marcarReemplazo = (tienda: Tienda, sku: string, de: string) => {
    const clave = claveDe(tienda, sku);
    if (!enTodos.has(clave)) { void agregarPorSku(tienda, sku, undefined, de); return; }
    setQuitadas((q) => { const n = new Set(q); n.delete(clave); return n; });
    setReemplazoDe((m) => ({ ...m, [clave]: de }));
    const r = todos.find((x) => x.clave === clave);
    setEditadas((e) => (clave in e ? e : { ...e, [clave]: r?.propuesta ?? 0 }));
    setAviso(`${sku} entra como REEMPLAZO de ${de} (${de} no se puede surtir: no hay existencia). Escribe cuántas mandar.`);
    setFiltro("mandar");
  };
  // Lo que se pidió desde Análisis.
  useEffect(() => {
    if (!reemplazoPedido || !datos) return;
    marcarReemplazo(reemplazoPedido.tienda, reemplazoPedido.sku, reemplazoPedido.de);
    onReemplazoHecho?.(reemplazoPedido.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reemplazoPedido?.id, datos]);

  // ── Excel, SKUs y la IA ──────────────────────────────────────────────────
  const planParaExcel = () => ({
    semana: datos ? `${datos.semana.semana} · ${rangoSemana(datos.semana.lunes)}` : "",
    ventana: datos ? `${datos.ventana.dias} días (${dia(`${datos.ventana.desde}T12:00:00-06:00`)} a ${dia(`${datos.ventana.hasta}T12:00:00-06:00`)})` : "",
    parametros_texto: params ? `cobertura ${params.cobertura_dias} d · mínimo por renglón ${params.min_piezas} · ganador desde ${params.min_ventas} · dejar en bodega ${params.dejar_en_bodega}` : "",
    en_vivo: "Mercado Libre verificado en vivo · Odoo (libre) en vivo · ventas de kubera · Walmart en vivo sin stock de WFS",
    tiendas: tiendasActivas.map((t) => ({
      tienda: t, nombre: datos!.tiendas[t].nombre, totales: porTienda[t],
      renglones: renglones.filter((r) => r.tienda === t && (cantidad(r) > 0 || r.pidio > 0)).map((r) => ({
        sku: r.sku, nombre: r.nombre, destino: datos!.tiendas[t].destino, vv: r.vv, v7: r.v7, stock: r.stock,
        en_camino: r.en_camino, borrador: r.borrador, libre: r.libre_total, pidio: r.pidio, bodega_puede: r.bodega,
        propuesta: r.propuesta, a_mandar: cantidad(r), estado: r.estado, caja: r.caja, listing_id: r.listing_id,
        precio: r.precio, reemplazo_de: reemplazoDe[r.clave] ?? null,
      })),
    })),
    ganadores: renglones.filter((r) => r.ganador_agotado).map((r) => ({
      tienda: datos!.tiendas[r.tienda].nombre, sku: r.sku, nombre: r.nombre, vv: r.vv, candidatos: r.reemplazos })),
    alertas: alertasDe(renglones, datos),
  });
  const descargar = async () => {
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/excel`,
                                  { method: "POST", body: JSON.stringify(planParaExcel()) },
                                  { "Content-Type": "application/json" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `planeacion_full_${datos?.semana.semana ?? "semana"}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Liberar después: revocar en el acto a veces cancela la descarga.
      setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
    } catch (e: unknown) {
      setAviso(`No se pudo descargar: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const copiarSkus = (t?: Tienda) => {
    const skus = renglones.filter((r) => (!t || r.tienda === t) && cantidad(r) > 0).map((r) => r.sku);
    void navigator.clipboard?.writeText(skus.join(", "));
    setAviso(`${skus.length} SKUs copiados${t ? ` de ${datos?.tiendas[t].nombre}` : ""}.`);
  };
  const ponTurno = (id: number, cambios: Partial<TurnoIA>) =>
    setTurnos((ts) => ts.map((t) => (t.id === id ? { ...t, ...cambios } : t)));
  const pedirIA = async (instruccion: string) => {
    if (!datos || !params) return;
    const vuelta = iaVivo.current;
    const id = Date.now();
    // Lo que ya contestó la IA, compacto: el servidor no guarda conversaciones.
    const historial = turnos.filter((t) => t.resultado).map((t) => ({
      instruccion: t.instruccion,
      respuesta: { respuesta: t.resultado!.respuesta, resumen: t.resultado!.resumen,
                   ajustes: t.resultado!.ajustes.map((a) => ({ tienda: a.tienda, sku: a.sku, cantidad: a.cantidad })) },
    }));
    setTurnos((ts) => [...ts, { id, instruccion, estado: "corriendo", segundos: 0 }]);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/ia`, {
        method: "POST",
        body: JSON.stringify({ datos: datosParaIA(renglones, cantidad, params, datos, tiendasActivas),
                               instrucciones: instruccion, historial }),
      }, { "Content-Type": "application/json" });
      const d = await r.json() as { ok: boolean; id?: string; motivo?: string };
      if (!r.ok || !d.ok || !d.id) throw new Error(d.motivo ?? `HTTP ${r.status}`);
      // Hasta 15 minutos: lo mismo que espera el backend a Claude.
      for (let i = 0; i < 300 && vuelta === iaVivo.current; i++) {
        await new Promise((res) => setTimeout(res, 3000));
        const e = await (await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/ia/${d.id}`, { cache: "no-store" })).json();
        if (vuelta !== iaVivo.current) return;
        if (e.estado === "corriendo") { ponTurno(id, { segundos: e.segundos }); continue; }
        if (e.estado === "listo") { ponTurno(id, { estado: "listo", resultado: e.resultado }); return; }
        throw new Error(e.motivo ?? "la revisión se perdió");
      }
    } catch (e: unknown) {
      if (vuelta === iaVivo.current) ponTurno(id, { estado: "error", motivo: e instanceof Error ? e.message : String(e) });
    }
  };
  const aplicarIA = (ajustes: RevisionIA["ajustes"]) => {
    const validos = ajustes.filter((a) => !quitadas.has(claveDe(a.tienda, a.sku)));
    for (const a of validos) {
      if (enPlan.has(claveDe(a.tienda, a.sku))) setEditadas((e) => ({ ...e, [claveDe(a.tienda, a.sku)]: a.cantidad }));
      else void agregarPorSku(a.tienda, a.sku, a.cantidad);
    }
    const saltados = ajustes.length - validos.length;
    setAviso(`${validos.length} ajuste${validos.length === 1 ? "" : "s"} de la IA aplicado${validos.length === 1 ? "" : "s"}`
      + (saltados ? `; ${saltados} no, porque quitaste esos SKUs.` : "."));
    setFiltro("mandar");
  };

  // ── Lo que se ve en Análisis · «Planeación de la semana» ─────────────────
  // Ganadores y títulos salen de las CUATRO tiendas (no dependen de lo editado);
  // los totales, de la planeación que se está armando.
  const paraAnalisis = useMemo(() => (datos && params
    ? planear(TIENDAS.filter((t) => datos.tiendas[t]).flatMap((t) => datos.tiendas[t].filas), params) : []),
  [datos, params]);
  const plan = useMemo<PlanAnalisis | null>(() => {
    if (!datos) return null;
    const nombres = Object.fromEntries(TIENDAS.map((t) => [t, datos.tiendas[t]?.nombre ?? t])) as Record<Tienda, string>;
    const almacen = Object.fromEntries(TIENDAS.map((t) => [t, datos.tiendas[t]?.almacen ?? "almacén"])) as Record<Tienda, string>;
    return {
      semana: `${datos.semana.semana} · ${rangoSemana(datos.semana.lunes)}`,
      generado: datos.generado, nombres, almacen,
      totales: tiendasActivas.map((t) => ({ tienda: t, t: porTienda[t] })).filter((x) => x.t),
      total,
      ganadores: paraAnalisis.filter((r) => r.ganador_agotado).sort((a, b) => b.vv - a.vv).map((r) => ({
        tienda: r.tienda, sku: r.sku, nombre: r.nombre, vv: r.vv, v7: r.v7, precio: r.precio, stock: r.stock,
        url: r.url, reemplazos: r.reemplazos })),
      titulos: paraAnalisis.filter((r) => r.alertas.includes("reciclado")).map((r) => ({
        tienda: r.tienda, sku: r.sku, nombre: r.nombre, nombre_odoo: r.nombre_odoo, titulo_mkt: r.titulo_mkt,
        imagen_mkt: r.imagen_mkt, url: r.url, listing_id: r.listing_id, parecido: r.parecido,
        urgente: r.titulo_urgente })),
      otras: alertasDe(paraAnalisis.filter((r) => r.alertas.some((a) => a !== "reciclado")), datos)
        .filter((a) => a.tipo !== "reciclado")
        .map((a) => ({ tienda: a.tienda_llave, sku: a.sku, tipo: a.tipo, detalle: a.detalle })),
      borradores: datos.borradores, abiertas: datos.abiertas ?? [],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datos, paraAnalisis, porTienda, total.a_mandar, total.pedidas, total.propuestas, activas]);
  useEffect(() => { onPlan?.(plan); }, [plan, onPlan]);

  // ── Saldo ────────────────────────────────────────────────────────────────
  const full = (["Kubera", "San Corpe"] as const).map((c) => ({ c, s: stock?.full[c] }));
  const enFull = full.every((x) => x.s) ? full.reduce((a, x) => a + (x.s?.piezas ?? 0), 0) : null;
  const sinFull = full.every((x) => x.s) ? full.reduce((a, x) => a + (x.s?.en_cero ?? 0), 0) : null;
  const pubFull = full.reduce((a, x) => a + (x.s?.publicaciones ?? 0), 0);
  const semana = tiendasActivas.map((t) => ({ t, s: datos?.esta_semana[t] }));
  const semPzs = semana.reduce((a, x) => a + (x.s?.piezas ?? 0), 0);
  const semSkus = semana.reduce((a, x) => a + (x.s?.skus ?? 0), 0);
  const semEnvios = semana.reduce((a, x) => a + (x.s?.envios ?? 0), 0);

  return (
    <div className="mt-4 flex flex-col gap-3">
      {/* ── Encabezado, tiendas y saldo ──────────────────────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Ceja>
              Planeación semanal {datos ? `${datos.semana.semana} · ${rangoSemana(datos.semana.lunes)}` : "…"}
              {datos ? ` · ventas del ${dia(`${datos.ventana.desde}T12:00:00-06:00`)} al ${dia(`${datos.ventana.hasta}T12:00:00-06:00`)}` : ""} · hora de CDMX
            </Ceja>
            <h2 className="mt-1 text-[22px] font-extrabold tracking-tight text-slate-900">Crear FULL</h2>
            <p className="mt-0.5 max-w-2xl text-[13px] text-slate-500">
              La planeación del prompt estándar con datos en vivo: cada tienda con sus publicaciones. Corrige, agrega
              SKUs, pídele a la IA lo que necesites y crea las órdenes en Odoo; después adjuntas la guía del marketplace.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            {datos && <EstadoInterruptor interruptor={datos.interruptor} rol={rol} onCambio={() => void cargar(false, ventana)} />}
            {datos?.ia_disponible && (
              <button type="button" onClick={() => setAgente(true)} disabled={!renglones.length}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-sm font-bold text-white shadow-sm hover:bg-violet-700 disabled:opacity-50">
                <Sparkles className="h-4 w-4" />
                {turnos.some((t) => t.estado === "corriendo") ? "La IA está pensando…" : "Planear con IA"}
              </button>
            )}
          </div>
        </div>

        {/* Las tiendas: prender y apagar, y cuánto va a cada una. */}
        <div className="mt-4 grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          {TIENDAS.map((t) => {
            const d = datos?.tiendas[t];
            const on = activas[t];
            const tot = porTienda[t];
            return (
              <button key={t} type="button" onClick={() => cambiarTienda(t)} aria-pressed={on}
                      className={`rounded-xl border px-3.5 py-2.5 text-left transition ${
                        on ? "border-indigo-200 bg-white shadow-sm" : "border-dashed border-slate-200 bg-slate-50 opacity-70"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-2 text-[13px] font-extrabold text-slate-800">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: COLOR_TIENDA[t] }} />
                    {d?.nombre ?? t}
                  </span>
                  <span className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition ${on ? "bg-indigo-600" : "bg-slate-300"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition ${on ? "left-[18px]" : "left-0.5"}`} />
                  </span>
                </div>
                <div className="mt-1 font-mono text-[15px] font-extrabold tabular-nums text-slate-900">
                  {on ? (tot ? `${num(tot.a_mandar)} pzs` : "…") : "apagada"}
                  {on && tot ? <span className="ml-1 text-[11px] font-semibold text-slate-400">{num(tot.skus_a_mandar)} SKUs</span> : null}
                </div>
                <div className="text-[11px] text-slate-500">
                  {d ? `${num(d.publicadas)} publicadas${d.verificadas ? ` · ${num(d.verificadas)} verificadas en vivo` : ""}` : "leyendo…"}
                </div>
                {NOTA_TIENDA[t] && <div className="mt-0.5 text-[10.5px] text-amber-700">{NOTA_TIENDA[t]}</div>}
              </button>
            );
          })}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          <Saldo icono={<Boxes className="h-3.5 w-3.5" />} rotulo="En FULL hoy"
                 ayuda="Piezas que hay HOY en FULL de Mercado Libre, sumando las dos cuentas."
                 cifra={num(enFull)}
                 pie={full.every((x) => x.s) ? full.map((x) => `${x.c} ${num(x.s!.piezas)}`).join(" · ") : "leyendo…"} />
          <Saldo icono={<PackageX className="h-3.5 w-3.5" />} rotulo="Publicaciones sin FULL" tono="rosa"
                 ayuda="Publicaciones FULL de las dos cuentas que hoy tienen 0 piezas en el almacén de Mercado Libre."
                 cifra={num(sinFull)}
                 pie={sinFull !== null ? `de ${num(pubFull)} publicaciones FULL · ${full.map((x) => `${x.c} ${num(x.s!.en_cero)}`).join(" · ")}` : "leyendo…"} />
          <Saldo icono={<Truck className="h-3.5 w-3.5" />} rotulo="En camino a FULL · esta semana" tono="ambar"
                 ayuda="Lo que se está mandando ESTA semana a las tiendas activas: salidas validadas esta semana más las que están por validar."
                 cifra={datos ? `${num(semPzs)} pzs` : "—"}
                 pie={datos ? `de ${num(semSkus)} SKUs en ${num(semEnvios)} envíos · ${semana.filter((x) => x.s?.piezas).map((x) => `${datos.tiendas[x.t].nombre} ${num(x.s!.piezas)}`).join(" · ") || "nada todavía"}` : "leyendo…"} />
          <Saldo icono={<ArrowRight className="h-3.5 w-3.5" />} rotulo="Por mandar · planeación" tono="indigo"
                 ayuda="La propuesta de la planeación semanal para las tiendas activas, con tus cambios y lo que aplicaste de la IA."
                 cifra={datos ? `${num(total.a_mandar)} pzs` : "—"}
                 pie={datos ? `${num(total.skus_a_mandar)} SKUs · ${tiendasActivas.map((t) => `${datos.tiendas[t].nombre} ${num(porTienda[t]?.a_mandar ?? 0)}`).join(" · ")}` : "calculando…"} />
        </div>
      </Tarjeta>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12.5px] text-rose-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span><b>No se pudo armar la planeación.</b> <code className="font-mono">{error}</code></span>
        </div>
      )}

      {agente && datos && (
        <PanelIA turnos={turnos} datos={datos} onAplicar={aplicarIA} onEnviar={(t) => void pedirIA(t)}
                 onAgregarReemplazo={(t, s, de) => marcarReemplazo(t, s, de)}
                 onNueva={() => { iaVivo.current++; setTurnos([]); }}
                 onCerrar={() => setAgente(false)} />
      )}

      {/* ── La planeación ────────────────────────────────────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-2.5">
            {params && (
              <>
                <Parametro t="Cobertura" unidad="días" valor={params.cobertura_dias} max={120}
                           ayuda="Cuántos días de venta debe aguantar el almacén del marketplace."
                           onCambio={(n) => setParams({ ...params, cobertura_dias: n })} />
                <label className="flex flex-col gap-1">
                  <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">
                    <Ayuda texto="Los días de venta con que se mide la velocidad. Cambiarla vuelve a leer las ventas." lado="izq">Ventana</Ayuda>
                  </span>
                  <select value={ventana} onChange={(ev) => setVentana(Number(ev.target.value))}
                          className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 font-mono text-sm font-bold text-slate-900">
                    {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} días</option>)}
                  </select>
                </label>
                <Parametro t="Mínimo por renglón" unidad="pzs" valor={params.min_piezas} max={500}
                           ayuda="Si a un SKU le faltan menos piezas que esto, no se propone."
                           onCambio={(n) => setParams({ ...params, min_piezas: n })} />
                <Parametro t="Ganador desde" unidad="ventas" valor={params.min_ventas} max={500}
                           ayuda="Ganador agotado = vendió al menos esto en la ventana, sin libre en Odoo y sin stock en el almacén (el prompt dice 3)."
                           onCambio={(n) => setParams({ ...params, min_ventas: n })} />
                <Parametro t="Dejar en bodega" unidad="pzs/SKU" valor={params.dejar_en_bodega} max={5000}
                           ayuda="Colchón por SKU que se queda en Odoo para los canales DROP (TikTok, Temu, Walmart S2H, web)."
                           onCambio={(n) => setParams({ ...params, dejar_en_bodega: n })} />
              </>
            )}
            {datos && params && (hayCambios || JSON.stringify({ ...params, ventana_dias: 0 }) !== JSON.stringify({ ...datos.parametros, ventana_dias: 0 })) && (
              <button type="button"
                      onClick={() => {
                        setParams({ ...datos.parametros, ventana_dias: datos.ventana.dias });
                        setEditadas({}); setAgregadas([]); setQuitadas(new Set()); setReemplazoDe({});
                      }}
                      className="mb-0.5 inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100">
                <RotateCcw className="h-3.5 w-3.5" /> volver a la propuesta
              </button>
            )}
          </div>
          <p className="max-w-sm text-[11px] leading-snug text-slate-400">
            Parámetros de la corrida (prompt estándar). Cambian la propuesta al instante; nada se guarda hasta crear.
          </p>
        </div>

        {datos && tiendasActivas.length > 0 && (
          <div className="mt-3">
            <BuscarSku tiendas={tiendasActivas.map((t) => ({ t, d: datos.tiendas[t] }))} ventana={ventana}
                       enPlan={enPlan} onAgregar={agregar} />
          </div>
        )}
        {aviso && (
          <p className="mt-2 rounded-lg bg-indigo-50 px-3 py-1.5 text-[12px] text-indigo-800">
            {aviso} <button type="button" onClick={() => setAviso(null)} className="ml-2 font-semibold underline">ok</button>
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="flex max-w-full overflow-x-auto rounded-lg border border-slate-200 bg-white">
            {(["todas", ...tiendasActivas] as (Tienda | "todas")[]).map((t) => (
              <button key={t} type="button" onClick={() => setVista(t)}
                      className={`shrink-0 whitespace-nowrap px-3 py-1.5 text-xs font-bold ${vista === t ? "bg-indigo-50 text-indigo-800" : "text-slate-500 hover:bg-slate-50"}`}>
                {t === "todas" ? "Todas las activas" : datos?.tiendas[t].nombre}
              </button>
            ))}
          </div>
          {FILTROS.filter((f) => f.k !== "quitados" || quitados.length > 0).map((f) => (
            <button key={f.k} type="button" title={f.titulo} onClick={() => setFiltro(f.k)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${
                      filtro === f.k ? "border-indigo-200 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-500"}`}>
              {f.t}<span className="font-mono text-[11px] opacity-70">
                {f.k === "quitados" ? quitadosEnVista.length : enVista.filter((r) => enFiltro(r, f.k)).length}
              </span>
            </button>
          ))}
          <label className="ml-auto flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
            <Search className="h-3.5 w-3.5 text-slate-400" />
            <input value={busca} onChange={(ev) => setBusca(ev.target.value)} placeholder="Filtrar la tabla"
                   className="w-36 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
          </label>
        </div>

        <div className="mt-3 max-h-[70vh] overflow-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[1440px] border-collapse text-[12.5px]">
            <thead className="sticky top-0 z-10">
              <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-500">
                <th className="px-3 py-2.5"><Ayuda lado="izq" texto="El SKU con el nombre de Omnicanal. Abajo, su publicación en el marketplace; «en vivo» = Mercado Libre la confirmó al armar la planeación.">SKU · producto</Ayuda></th>
                {vista === "todas" && <th className="px-3 py-2.5"><Ayuda lado="izq" texto="A qué almacén va: FULL de Kubera, FULL de San Corpe, FBA de Amazon o WFS de Walmart.">Tienda</Ayuda></th>}
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Precio de venta HOY en esa tienda (Mercado Libre en vivo; Amazon y Walmart, el de su publicación). Sirve para planear por ticket.">Precio</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Piezas vendidas en esa tienda en la ventana y su ritmo por día. ↑ = la última semana vende más de 1.5 veces ese ritmo.">Vende</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que hay HOY en el almacén del marketplace (FULL, FBA o WFS). En Mercado Libre se lee en vivo. «?» = no se sabe, no es 0.">En almacén</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Cuántos días alcanza lo que hay en el almacén al ritmo de la ventana.">Aguanta</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que ya va hacia el almacén: salidas por validar, lo que el marketplace todavía no recibe y los borradores en Odoo. Se resta para no mandar dos veces.">En camino</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo libre en Odoo (free_qty) en TEXCO y TEXCO II: lo que se puede surtir. Odoo es el master.">Libre Odoo</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Faltante = objetivo de cobertura − lo que hay en el almacén − lo que va en camino.">Pidió</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo libre que le toca a esta tienda, ya repartido si otra tienda activa pide el mismo SKU.">Bodega puede</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda texto="Lo que conviene mandar: el menor entre lo que pidió y lo que bodega puede (0 si queda debajo del mínimo por renglón).">Propuesta</Ayuda></th>
                <th className="px-3 py-2.5 text-right"><Ayuda lado="der" texto="Lo que se va a crear en Odoo. Cámbialo aquí; en azul lo que editaste.">A mandar</Ayuda></th>
                <th className="px-3 py-2.5"><Ayuda lado="der" texto="Aprobado: bodega cubre lo pedido. Recorte: Odoo no alcanza, va lo que hay. Pendiente: sin dato de Odoo (no es un cero). Cubierto: ya alcanza.">Estado</Ayuda></th>
                <th className="px-2 py-2.5"><span className="sr-only">Quitar</span></th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((r) => (
                <FilaUI key={r.clave} r={r} valor={filtro === "quitados" ? 0 : cantidad(r)} editada={r.clave in editadas}
                        conTienda={vista === "todas"} nombreTienda={datos?.tiendas[r.tienda].nombre ?? r.tienda}
                        quitada={filtro === "quitados"} reemplazoDe={reemplazoDe[r.clave]}
                        onCambio={(v) => fijar(r.clave, v)} onQuitar={() => quitar(r)} onRestaurar={() => restaurar(r)} />
              ))}
              {!datos && (
                <tr><td colSpan={14} className="px-4 py-12 text-center text-sm text-slate-500" style={{ background: FONDO_RAYADO }}>
                  {cargando ? "Leyendo ventas, publicaciones (verificando en vivo con Mercado Libre), envíos y lo libre en Odoo… tarda unos 25 segundos."
                            : "Sin planeación: revisa el error de arriba."}
                </td></tr>
              )}
              {datos && visibles.length === 0 && (
                <tr><td colSpan={14} className="px-4 py-10 text-center text-sm text-slate-500">
                  {tiendasActivas.length ? "Nada en este filtro." : "Prende al menos una tienda."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3">
          <span className="text-sm text-indigo-900">
            <b className="font-mono tabular-nums">{num(total.skus_a_mandar)}</b> SKUs ·{" "}
            <b className="font-mono tabular-nums">{num(total.a_mandar)}</b> piezas en {tiendasActivas.length} tienda{tiendasActivas.length === 1 ? "" : "s"}
            {datos ? <span className="text-indigo-700/70"> · semana {datos.semana.semana}</span> : null}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => copiarSkus()} disabled={!total.skus_a_mandar}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40">
              <ClipboardCopy className="h-4 w-4" /> Copiar SKUs
            </button>
            <button type="button" onClick={() => void descargar()} disabled={!renglones.length}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40">
              <Download className="h-4 w-4" /> Descargar Excel
            </button>
            <button type="button" onClick={() => setRevisar(true)} disabled={!total.skus_a_mandar}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-40">
              Revisar y crear <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </Tarjeta>

      {datos && <CreadasAqui datos={datos} rol={rol} />}

      <p className="text-xs leading-relaxed text-slate-400">
        {datos ? `Fuente: ${datos.fuente}. Leído ${new Date(datos.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.` : ""}
      </p>

      {revisar && params && datos && (
        <ConfirmarFull
          pedidos={tiendasActivas.map((t) => ({
            tienda: t, lineas: renglones.filter((r) => r.tienda === t && cantidad(r) > 0)
              .map((r) => ({ sku: r.sku, cantidad: cantidad(r), sugerido: r.propuesta })),
          })).filter((p) => p.lineas.length)}
          semana={`${datos.semana.semana} · ${rangoSemana(datos.semana.lunes)}`} params={params} rol={rol}
          onDescargar={() => void descargar()} onCerrar={() => setRevisar(false)}
          onCreado={() => { void cargar(true, ventana); }} />
      )}
    </div>
  );
}

function alertasDe(renglones: Renglon[], datos: PropuestaFull | null) {
  const texto: Record<string, (r: Renglon) => string> = {
    sin_categoria: () => "la publicación no tiene categoría",
    reciclado: (r) => `el título del marketplace («${(r.titulo_mkt ?? "").slice(0, 60)}») no se parece al de Odoo («${(r.nombre_odoo ?? r.nombre ?? "").slice(0, 60)}»)`,
    medidas: () => "caja master sospechosa: peso ≤ 0.5 kg con medidas ~60×41×41",
    cerrada_en_ml: () => "la publicación está cerrada o inactiva en Mercado Libre",
  };
  const salida: { tienda: string; tienda_llave: Tienda; sku: string; tipo: string; detalle: string }[] =
    renglones.flatMap((r) => r.alertas.map((a) => ({
      tienda: datos?.tiendas[r.tienda].nombre ?? r.tienda, tienda_llave: r.tienda, sku: r.sku, tipo: a,
      detalle: texto[a]?.(r) ?? a })));
  for (const r of renglones) {
    if (r.tienda.startsWith("meli") && r.publicada && !r.verificada) {
      salida.push({ tienda: datos?.tiendas[r.tienda].nombre ?? r.tienda, tienda_llave: r.tienda, sku: r.sku,
                    tipo: "sin_verificar", detalle: "Mercado Libre no la confirmó en vivo: el dato es del sync" });
    }
  }
  return salida;
}

function Parametro({ t, unidad, valor, max, ayuda, onCambio }: {
  t: string; unidad: string; valor: number; max: number; ayuda: string; onCambio: (n: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400"><Ayuda texto={ayuda} lado="izq">{t}</Ayuda></span>
      <span className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1">
        <input type="text" inputMode="numeric" value={valor}
               onChange={(ev) => onCambio(Math.max(0, Math.min(max, parseInt(ev.target.value.replace(/[^\d]/g, ""), 10) || 0)))}
               className="w-12 bg-transparent text-right font-mono text-sm font-bold tabular-nums text-slate-900 focus:outline-none" />
        <span className="text-[11px] text-slate-400">{unidad}</span>
      </span>
    </label>
  );
}

function Saldo({ icono, rotulo, cifra, pie, ayuda, tono = "slate" }: {
  icono: ReactNode; rotulo: string; cifra: string; pie: string; ayuda: string; tono?: "slate" | "rosa" | "ambar" | "indigo";
}) {
  const c = {
    slate: "border-slate-200 bg-white text-slate-900",
    rosa: "border-rose-200 bg-rose-50 text-rose-800",
    ambar: "border-amber-200 bg-amber-50 text-amber-800",
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-900",
  }[tono];
  return (
    <div className={`rounded-xl border px-3.5 py-3 ${c}`}>
      <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[.06em]">
        <span className="opacity-80">{icono}</span><Ayuda texto={ayuda} lado="izq">{rotulo}</Ayuda>
      </div>
      <div className="mt-1 text-2xl font-extrabold leading-none tracking-tight tabular-nums">{cifra}</div>
      <div className="mt-1 text-[11.5px] opacity-80">{pie}</div>
    </div>
  );
}

const ESTADO: Record<Renglon["estado"], { t: string; c: string }> = {
  aprobado: { t: "aprobado", c: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  recorte: { t: "recorte", c: "border-amber-300 bg-amber-50 text-amber-800" },
  pendiente: { t: "pendiente", c: "border-dashed border-slate-300 text-slate-500" },
  cubierto: { t: "cubierto", c: "border-slate-200 bg-white text-slate-400" },
};

function FilaUI({ r, valor, editada, conTienda, nombreTienda, quitada, reemplazoDe, onCambio, onQuitar, onRestaurar }: {
  r: Renglon; valor: number; editada: boolean; conTienda: boolean; nombreTienda: string; quitada: boolean;
  reemplazoDe?: string; onCambio: (v: string) => void; onQuitar: () => void; onRestaurar: () => void;
}) {
  const e = ESTADO[r.estado];
  const tonoAguanta = r.aguanta === null ? "text-slate-300"
    : r.aguanta < 7 ? "text-rose-700" : r.aguanta < 15 ? "text-amber-700" : "text-slate-600";
  return (
    <tr className={`border-t border-slate-100 align-top ${quitada ? "opacity-60" : valor > 0 ? "" : "bg-slate-50/40"}`}>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12.5px] font-bold text-slate-900">{r.sku}</span>
          {r.stock === 0 && <span className="rounded border border-rose-200 bg-rose-50 px-1.5 text-[9.5px] font-bold uppercase text-rose-700">sin stock</span>}
          {r.ganador_agotado && <span className="rounded border border-violet-200 bg-violet-50 px-1.5 text-[9.5px] font-bold uppercase text-violet-700">ganador agotado</span>}
          {reemplazoDe && (
            <span className="rounded bg-violet-600 px-1.5 text-[9.5px] font-extrabold uppercase text-white"
                  title={`${reemplazoDe} no se puede surtir: no hay existencia en Odoo ni en el almacén`}>
              reemplazo de {reemplazoDe}
            </span>
          )}
          {r.agregado && !reemplazoDe && <span className="rounded border border-indigo-200 bg-indigo-50 px-1.5 text-[9.5px] font-bold uppercase text-indigo-700">agregado</span>}
          {r.alertas.includes("reciclado") && r.titulo_urgente && (
            <span className="rounded border border-amber-300 bg-amber-50 px-1.5 text-[9.5px] font-bold uppercase text-amber-700"
                  title={`El título de la publicación («${r.titulo_mkt}») no se parece al de Odoo («${r.nombre_odoo}») ni al del catálogo. Compara las fotos en Análisis.`}>
              ¿reciclado?
            </span>
          )}
        </div>
        <div className="max-w-[330px] truncate text-[11px] text-slate-500" title={r.nombre ?? ""}>{r.nombre ?? "—"}</div>
        <div className="flex items-center gap-1.5 text-[10.5px]">
          {r.listing_id ? (
            <a href={r.url ?? "#"} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-mono text-indigo-500 hover:underline">
              {r.listing_id}<ExternalLink className="h-2.5 w-2.5" />
            </a>
          ) : <span className="text-amber-700">sin publicación registrada</span>}
          {r.verificada && <span className="text-emerald-600">· en vivo</span>}
          {r.en_almacen === false && r.tienda.startsWith("meli") && <span className="text-slate-400">· aún no es FULL</span>}
          {r.caja ? <span className="text-slate-400">· caja de {r.caja}</span> : null}
        </div>
      </td>
      {conTienda && <td className="px-3 py-2 text-[11.5px] font-semibold text-slate-600">{nombreTienda}</td>}
      <td className="px-3 py-2 text-right font-mono text-[12px] tabular-nums text-slate-700">{pesos(r.precio)}</td>
      <td className="px-3 py-2 text-right">
        <div className="font-mono font-bold tabular-nums text-slate-800">{num(r.vv)}</div>
        <div className="text-[10.5px] text-slate-400" title={`Últimos 7 días: ${r.v7}`}>
          {r.velocidad.toFixed(1)}/día{r.sube ? <span className="font-bold text-emerald-600"> ↑</span> : null}
        </div>
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {r.stock === null
          ? <span className="rounded px-1.5 text-slate-400" style={{ background: FONDO_RAYADO }} title="No se sabe: no es 0">?</span>
          : <span className={r.stock === 0 ? "font-bold text-rose-700" : "text-slate-700"}>{num(r.stock)}</span>}
      </td>
      <td className={`px-3 py-2 text-right font-mono text-[12px] tabular-nums ${tonoAguanta}`}>
        {r.aguanta === null ? "—" : r.aguanta >= 99 ? "99+ d" : `${Math.floor(r.aguanta)} d`}
      </td>
      <td className="px-3 py-2 text-right">
        {r.en_camino > 0
          ? <div className="font-mono tabular-nums text-amber-700" title={r.camino.join("\n")}>{num(r.en_camino)}</div>
          : <div className="font-mono text-slate-300">0</div>}
        {r.borrador > 0 && <div className="text-[10.5px] text-indigo-600" title={r.borradores.join(", ")}>+{num(r.borrador)} borrador</div>}
      </td>
      <td className="px-3 py-2 text-right text-[11.5px]">
        {r.libre ? Object.entries(r.libre).map(([alm, n]) => (
          <div key={alm} className={`font-mono tabular-nums ${n > 0 ? "text-slate-700" : "text-slate-300"}`}>
            <span className="text-[10px] text-slate-400">{alm}</span> {num(n)}
          </div>
        )) : <span className="text-slate-400">no está en Odoo</span>}
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-700">{num(r.pidio)}</td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-700" title={r.repartido}>
        {r.bodega === null ? <span className="text-slate-400">?</span> : num(r.bodega)}
        {r.repartido && <div className="text-[10px] text-violet-700">repartido</div>}
      </td>
      <td className="px-3 py-2 text-right font-mono font-bold tabular-nums text-indigo-700">{r.propuesta ? num(r.propuesta) : "—"}</td>
      <td className="px-3 py-2 text-right">
        <input type="text" inputMode="numeric" value={valor ? String(valor) : ""} placeholder="0" disabled={quitada}
               onChange={(ev) => onCambio(ev.target.value)}
               className={`w-20 rounded-md border px-2 py-1 text-right font-mono text-[13px] font-bold tabular-nums focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 ${
                 editada ? "border-indigo-300 bg-indigo-50 text-indigo-900" : "border-slate-200 text-slate-900"}`} />
        {r.bodega !== null && valor > r.bodega && (
          <div className="mt-0.5 text-[10px] font-semibold text-amber-700">más de lo libre: se recortará</div>
        )}
      </td>
      <td className="px-3 py-2">
        <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-bold ${e.c}`}
              style={r.estado === "pendiente" ? { background: FONDO_RAYADO } : undefined}>{e.t}</span>
      </td>
      <td className="px-2 py-2 text-right">
        {quitada ? (
          <button type="button" onClick={onRestaurar} title="Regresar este SKU a la planeación"
                  className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-2 py-1 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50">
            <RotateCcw className="h-3.5 w-3.5" /> Restaurar
          </button>
        ) : (
          <button type="button" onClick={onQuitar} title="Quitar este SKU de la planeación (se puede restaurar en «Quitados»)"
                  aria-label={`Quitar ${r.sku}`}
                  className="rounded-md p-1.5 text-slate-300 hover:bg-rose-50 hover:text-rose-600">
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </td>
    </tr>
  );
}

/**
 * Lo que se creó DESDE AQUÍ y sigue en borrador: el último paso de crear un FULL es
 * adjuntar la guía del marketplace. Cuánto lleva cada orden sin completarse (todas,
 * no sólo las del panel) se ve en Análisis.
 */
function CreadasAqui({ datos, rol }: { datos: PropuestaFull; rol: Rol }) {
  const [guia, setGuia] = useState<number | null>(null);
  const propias = datos.borradores.filter((b) => b.panel);
  if (!propias.length) return null;
  return (
    <Tarjeta>
      <Ceja>Creadas desde aquí · en borrador · {propias.length}</Ceja>
      <p className="mt-1 text-[12px] text-slate-500">
        Adjunta el número del envío y la guía del marketplace. Cuánto lleva cada orden sin completarse está en Análisis.
      </p>
      <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
        {propias.map((b) => (
          <div key={b.id} className="px-3 py-2 text-[12.5px]">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <a href={b.url} target="_blank" rel="noreferrer" className="hover:underline">
                <span className="font-mono font-bold text-slate-800">{b.orden}</span>
                <span className="text-slate-400"> · {b.tienda ? datos.tiendas[b.tienda]?.nombre : "sin tienda"}
                  {b.almacen ? ` · ${b.almacen}` : ""} · {num(b.piezas)} pzs</span>
              </a>
              <span className="flex items-center gap-1.5 text-[11.5px]">
                {b.prueba && <span className="rounded bg-amber-100 px-1.5 text-[10px] font-bold text-amber-800">PRUEBA</span>}
                {b.referencia && <span className="text-slate-400">ref «{b.referencia}»</span>}
              </span>
            </div>
            {rol === "admin" && (guia === b.id
              ? <div className="mt-1"><GuiaOrden orden={{ id: b.id, orden: b.orden }} compacta /></div>
              : <button type="button" onClick={() => setGuia(b.id)}
                        className="mt-1 text-[11px] font-semibold text-indigo-600 hover:underline">Adjuntar guía del marketplace</button>)}
          </div>
        ))}
      </div>
    </Tarjeta>
  );
}

/** El interruptor que deja a «Crear FULL» escribir en Odoo. Sólo lo mueve un admin. */
function EstadoInterruptor({ interruptor, rol, onCambio }: {
  interruptor: Interruptor; rol: Rol; onCambio: () => void;
}) {
  const [preguntar, setPreguntar] = useState(false);
  const [moviendo, setMoviendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const encendido = interruptor.encendido;
  const mover = async (valor: boolean) => {
    setMoviendo(true);
    setError(null);
    try {
      const motivo = encodeURIComponent(valor ? "encendido desde Crear FULL" : "apagado desde Crear FULL");
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/interruptor?encendido=${valor}&motivo=${motivo}`,
                                  { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.motivo ?? d.detail ?? `HTTP ${r.status}`);
      setPreguntar(false);
      onCambio();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMoviendo(false);
    }
  };
  return (
    <div className="flex max-w-[400px] flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        <span title={interruptor.actualizado_por ? `Lo movió ${interruptor.actualizado_por} el ${dia(interruptor.actualizado_at)}` : "Nadie lo ha movido: apagado por omisión."}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${
                encendido ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
          <Power className="h-3 w-3" /> Crear en Odoo: {encendido ? "encendido" : "apagado"}
        </span>
        {rol === "admin" && !preguntar && (
          <button type="button" onClick={() => (encendido ? void mover(false) : setPreguntar(true))} disabled={moviendo}
                  className="text-[11px] font-semibold text-indigo-600 hover:underline disabled:opacity-50">
            {encendido ? "apagar" : "encender"}
          </button>
        )}
      </div>
      {preguntar && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11.5px] leading-relaxed text-amber-900">
          Al encender, «Crear FULL» escribe en Odoo cotizaciones en <b>borrador</b> (una por tienda y almacén, con el socio
          fijo de cada tienda, precio 0 y sin impuestos) y adjunta la guía que subas. <b>No confirma ni reserva stock.</b>
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setPreguntar(false)} className="rounded-md px-2 py-1 font-semibold text-amber-800 hover:bg-amber-100">
              Cancelar
            </button>
            <button type="button" onClick={() => void mover(true)} disabled={moviendo}
                    className="rounded-md bg-amber-600 px-2.5 py-1 font-bold text-white hover:bg-amber-700 disabled:opacity-50">
              Sí, encender
            </button>
          </div>
        </div>
      )}
      {error && <span className="text-[11px] text-rose-700">No se pudo: {error}</span>}
    </div>
  );
}
