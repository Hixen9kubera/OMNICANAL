"use client";

/**
 * /analisis — CLON del tablero kubera-fulfillment (José) con el TEMA CLARO
 * de OMNICANAL, alimentado por la BD kubera v4 vía /api/fulfillment/*
 * (primer lector de producción).
 *
 * Equivalencias vs el original (ver routers/fulfillment.py):
 *   STOCK ODOO → STOCK PROPIO · DÍAS ODOO → EDAD S/VENTA ·
 *   DÍAS VENTA → COBERTURA · VISITAS/CR% → sin dato (fuera de alcance) ·
 *   TAM → derivado de dimensiones de costos_validados.
 * Columna extra NUESTRA: SUGERIDO (Bollinger de channel.restock_panel).
 *
 * La tabla se COMPACTA en celdas de dos líneas (producto+tam+cuentas,
 * uds+venta, full+propio, precio+sug+costo) para verse completa sin
 * desplazamiento horizontal.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, BadgePercent, CalendarDays, RefreshCw, X } from "lucide-react";
import MargenesRealesModal from "@/components/MargenesRealesModal";
import { API_BASE, fetchSesion } from "@/lib/api";
import { avisoCostoImplausible, costoImplausible } from "@/lib/margen";
import Ayuda from "@/components/Ayuda";

/* ── Tipos ─────────────────────────────────────────────────────────────── */

interface Dashboard {
  ambiente: string;
  dias: number;
  skus: { skus_catalogo: number; skus_listados: number; pct_activas: number; pct_sin_stock: number };
  kpis: {
    productos: number; activos: number; activos_full: number;
    stock_full: number; stock_propio: number; uds_periodo: number; venta_periodo: number;
  };
  cuentas: { cuenta: string; listings: number }[];
  serie: { date: string; unidades: number; venta: number }[];
}

interface PrecioCanal {
  cuenta: string;
  canal: string;
  situacion: string | null;
  price: number | null;
}

interface Fila {
  sku: string;
  cuentas: string[];
  titulo: string | null;
  tam: string;
  estado: "activa" | "pausada" | "no_venta";
  // situación de las publicaciones, independiente de la venta: "NO VENTA" la
  // tapa en `estado` (caso real: 15 de 17 SKUs de una captura eran pausados y
  // el chip no lo decía) — este campo la rescata para mostrarla al lado
  situacion_chip: "activa" | "pausada" | "otra";
  tipo: "full" | "no_full" | "mixto";
  uds: number;
  venta: number;
  stock_full: number;
  stock_propio: number;
  edad_sin_venta_d: number | null;
  cobertura_d: number | null;
  precio: number | null;              // el de la publicación ACTIVA
  precio_cualquiera: number | null;   // fallback cuando no hay ninguna activa
  precios: PrecioCanal[] | null;      // desglose por cuenta/canal
  precio_visto_at: string | null;
  precio_sugerido: number | null;
  costo: number | null;
  margen_pct: number | null;
  // Cobros del marketplace por unidad (Eduardo, 5-ago): la comisión es la REAL
  // de los pedidos del período — por eso puede faltar (SKU sin ventas, o
  // Amazon, que aún la registra en cero). Con ellos sale el margen NETO.
  comision_unit: number | null;
  envio_unit: number | null;
  costo_final: number | null;
  margen_neto_pct: number | null;
  crec_7d_pct: number | null;
  sugerido_full: number;
  spark: number[];
  // Visitas a las publicaciones de MERCADO LIBRE en el período y conversión.
  // `uds_ml` es el numerador de esa conversión: las unidades totales incluyen
  // Amazon, que no aporta visitas, y mezclarlas inflaría el porcentaje.
  visitas: number | null;
  visitas_dias: number | null;
  cr_pct: number | null;
  uds_ml: number;
  // Presente solo cuando ML pesó el producto en las DOS cuentas y los pesos no
  // coinciden: el SKU tiene dos productos distintos debajo (ver ficha_ml.py).
  peso_divergente: {
    ratio: number; min_g: number; max_g: number;
    detalle: { cuenta: string; peso_g: number; titulo: string | null }[];
  } | null;
}

interface TablaResp { total: number; items: Fila[]; limit: number; offset: number }

interface DetalleDia { date: string; uds: number; venta: number }

interface Detalle {
  sku: string;
  dias: number;
  serie: DetalleDia[];
  por_cuenta: { cuenta: string; uds: number; venta: number }[];
  resumen: {
    total_uds: number;
    total_venta: number;
    venta_diaria: number;
    dias_con_venta: number;
    mejor_dia: DetalleDia | null;
    ultima_venta: string | null;
  };
}

/* ── Formatos y estilos (paleta OMNICANAL, tema claro) ─────────────────── */

const fMoney = (n: number | null | undefined, dec = 0) =>
  n == null ? "—" : `$${Number(n).toLocaleString("es-MX", { maximumFractionDigits: dec })}`;
const fNum = (n: number | null | undefined, dec = 0) =>
  n == null ? "—" : Number(n).toLocaleString("es-MX", { maximumFractionDigits: dec });

const CUENTA_DOT: Record<string, string> = {
  BEKURA: "bg-sky-500",
  SANCORFASHION: "bg-violet-500",
  AMAZON: "bg-amber-500",
};
const ESTADO_CHIP: Record<string, string> = {
  activa: "bg-emerald-100 text-emerald-700",
  pausada: "bg-amber-100 text-amber-700",
  no_venta: "bg-slate-100 text-slate-500",
};
const ESTADO_LABEL: Record<string, string> = {
  activa: "ACTIVA", pausada: "PAUSADA", no_venta: "NO VENTA",
};
const TIPO_LABEL: Record<string, string> = { full: "FULL", no_full: "DROP", mixto: "MIXTO" };

// is_fulfillment = "bodega del marketplace", genérico. En bodega del canal:
// FULL (ML) / FBA (Amazon, cuando la fila es solo-Amazon). Desde bodega
// propia: SIEMPRE "DROP" — término de la casa; se entiende que en Amazon
// eso es FBM (decisión Eduardo 2026-07-28).
const tipoLabel = (f: Fila) => {
  if (f.tipo === "full" && f.cuentas.length === 1 && f.cuentas[0] === "AMAZON")
    return "FBA";
  return TIPO_LABEL[f.tipo];
};
const TIPO_CHIP: Record<string, string> = {
  full: "bg-indigo-100 text-indigo-700",
  no_full: "bg-slate-100 text-slate-600",
  mixto: "bg-fuchsia-100 text-fuchsia-700",
};

/* Qué significa cada columna. Vive aquí y no en el backend porque es lenguaje
   de negocio para el humano que mira la tabla, no parte del contrato de datos. */
const AYUDA: Record<string, { titulo: string; texto: string }> = {
  sku: { titulo: "Producto", texto: "SKU, título y las cuentas donde está publicado: BK Bekura · SC San Corpe · AMZ Amazon. La letra gris es la categoría de tamaño (S/M/L/XL) que sale de sus dimensiones." },
  visitas: { titulo: "Visitas · CR%", texto: "Cuánta gente vio las publicaciones del SKU en Mercado Libre durante el período, y qué porcentaje de esas visitas terminó en compra. Sirve para separar dos problemas distintos: pocas visitas es falta de visibilidad (precio, posición, publicidad); muchas visitas con conversión baja es un problema de la ficha o del precio. Solo Mercado Libre — Amazon no publica este dato, así que la conversión se calcula únicamente con las unidades vendidas en ML. El asterisco avisa que ML devolvió menos días que el período pedido." },
  estado: { titulo: "Estado", texto: "Si tiene publicación viva: ACTIVA se puede comprar, PAUSADA existe pero no vende, NO VENTA no está publicado. La segunda etiqueta dice desde qué bodega sale: FULL, FBA, DROP o MIXTO." },
  venta: { titulo: "Uds · $Venta", texto: "Unidades vendidas e importe en el período elegido arriba, sumando todas las cuentas de ese SKU." },
  stock_full: { titulo: "FULL · Propio", texto: "Piezas en la bodega del marketplace (FULL en Mercado Libre, FBA en Amazon) y piezas en tu bodega propia (DROP). Son inventarios separados y no se suman: reponer significa mover de Propio a FULL." },
  edad: { titulo: "Edad sin venta", texto: "Días desde la última venta registrada. Un número alto con stock encima es dinero detenido." },
  cobertura: { titulo: "Cobertura", texto: "Cuántos días te dura el stock al ritmo de venta del período. Es la columna más accionable: por debajo de 10 días (lo que tarda un envío a FULL) ya vas tarde." },
  precio: { titulo: "Precio de venta", texto: "Lo que de VERDAD se cobró en promedio durante el período: el dinero vendido dividido entre las piezas. Ya viene ponderado, así que si un producto se vende en dos cuentas a precios distintos, pesa más la que más vendió. Si no hubo ventas en el período se muestra el precio de la publicación activa, por cuenta. Haz clic para ver el desglose por canal." },
  margen: { titulo: "Margen bruto", texto: "Cuánto deja el producto sobre lo que de verdad se cobró: (precio real − costo) ÷ precio real. El costo es uno solo por producto, así que el margen cambia según a qué precio se vendió. NO descuenta la comisión del marketplace ni el envío — para eso está la columna de al lado. Si no hubo ventas en el período se calcula sobre el precio publicado, una línea por cuenta. Haz clic para ver el desglose por canal." },
  margen_neto: { titulo: "Margen neto", texto: "El mismo margen pero ya con los cobros de Mercado Libre encima: costo del producto + comisión + envío. La comisión es la REAL que cobró el marketplace en las ventas del período, no una tasa supuesta, así que ya viene con la comisión de cada canal. Este es el margen que de verdad queda. Sale vacío cuando el producto no vendió en el período (sin venta no hay comisión que leer) o cuando solo vende en Amazon, que todavía reporta comisión cero. Ordena por esta columna de menor a mayor para ver primero lo que está vendiendo mal. Cuando el porcentaje sale en ÁMBAR con ⚠, el costo capturado supera 3 veces el precio al que se vendió: el margen se muestra igual, pero sale de un costo poco creíble, así que léelo como referencia y no como un hecho — el problema está en el dato, no en la venta. Haz clic para ver el desglose por canal." },
  crec: { titulo: "Crecimiento 7 días", texto: "Unidades de los últimos 7 días contra los 7 anteriores. Sirve para cazar lo que despegó antes de que se acabe." },
  spark: { titulo: "Últimos 14 días", texto: "Una barra por día de los últimos 14. Haz clic en la miniatura para abrir el detalle día por día con el desglose por cuenta." },
  sugerido: { titulo: "Sugerido a FULL", texto: "Cuántas piezas conviene mandar a la bodega del marketplace. Se calcula con el ritmo de venta de los últimos 45 días considerando también sus picos (no solo el promedio), para cubrir 24 días: 14 de colchón más 10 que tarda el envío en llegar." },
};

/* Dirección con la que abre cada columna: la que responde su pregunta útil.
   COBERTURA abre ascendente porque lo urgente es lo que MENOS dura. Este mapa
   debe coincidir con _ORDEN del backend (routers/fulfillment.py). */
const DIR_NATURAL: Record<string, "asc" | "desc"> = {
  sku: "asc", venta: "desc", stock_full: "desc", edad: "desc",
  cobertura: "asc", margen: "desc", margen_neto: "desc", crec: "desc",
  sugerido: "desc",
};
const ORDEN_LABEL: Record<string, string> = {
  sku: "SKU", venta: "$ venta", stock_full: "stock FULL", edad: "edad sin venta",
  cobertura: "cobertura", margen: "margen bruto", margen_neto: "margen neto",
  crec: "crecimiento 7d", sugerido: "sugerido",
};

/* Cabecera de tabla: ordena al hacer clic (segundo clic invierte) y lleva su
   "?". La flecha dibuja la dirección REAL — antes ponía ↓ siempre, y en
   Cobertura mentía porque esa columna ordena ascendente.
   VIVE A NIVEL DE MÓDULO a propósito: definida dentro del componente, React la
   ve como un tipo distinto en cada render y desmonta las 12 cabeceras — con el
   contador de "hace Xs" refrescando cada segundo, el tooltip no alcanzaba a
   verse. */
function Th({ id, children, right, info, orden, dir, onSort }: {
  id?: string; children: React.ReactNode; right?: boolean; info?: string;
  orden: string; dir: "asc" | "desc"; onSort: (id: string) => void;
}) {
  const activa = !!id && orden === id;
  const ayuda = AYUDA[info ?? id ?? ""];
  return (
    <th
      onClick={id ? () => onSort(id) : undefined}
      /* Sin `title` nativo: se encimaba con el tooltip del "?" (Eduardo,
         30-jul). Que la columna ordena ya lo dicen el cursor, la flecha y el
         chip "Orden:" del encabezado de la tabla. */
      className={[
        "whitespace-nowrap px-2 py-2.5 text-[10px] font-semibold uppercase tracking-wide",
        right ? "text-right" : "text-left",
        id ? "cursor-pointer select-none hover:text-slate-700" : "",
        activa ? "text-indigo-600" : "text-slate-500",
      ].join(" ")}
    >
      <span className={`inline-flex items-center ${right ? "justify-end" : ""}`}>
        {children}
        {ayuda && <Ayuda titulo={ayuda.titulo} texto={ayuda.texto} />}
        {activa && (
          <span className="ml-0.5 text-indigo-500">{dir === "asc" ? "↑" : "↓"}</span>
        )}
      </span>
    </th>
  );
}

function Kpi({ label, value, tone, ayuda }: {
  label: string; value: string; tone?: string;
  ayuda?: { titulo: string; texto: string };
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">
        <span className="inline-flex items-center">
          {label}
          {ayuda && <Ayuda titulo={ayuda.titulo} texto={ayuda.texto} />}
        </span>
      </div>
      <div className={`mt-1 text-2xl font-bold ${tone ?? "text-slate-900"}`}>{value}</div>
    </div>
  );
}

/* La respuesta a "¿por qué no cuadra con Mercado Libre?" vive en el propio
   KPI: es la pregunta que va a volver cada vez que alguien compare paneles. */
/* Dos textos, no uno compartido: "cuenta piezas, no pedidos" es cierto de las
   UNIDADES y no dice nada de los pesos; "es venta bruta" es al revés. Un solo
   tooltip para ambos KPIs sería medio correcto en cada uno. */
const AYUDA_UDS_KPI = {
  titulo: "Cómo se cuentan las unidades",
  texto: "Cuenta PIEZAS, no pedidos: un pedido de 3 piezas suma 3 — la mayor parte del total viene de compras múltiples. Es la suma exacta de las barras de la gráfica, incluida la venta de publicaciones ya cerradas, y el último día va en curso. ¿No cuadra con el panel de Mercado Libre? Aquí las canceladas se excluyen y los días se cortan con horario de México; ML las incluye y corta su ventana distinto.",
};
const AYUDA_VENTA_KPI = {
  titulo: "Cómo se cuenta la venta",
  texto: "Venta BRUTA del período: no descuenta la comisión del marketplace, ni el costo, ni el envío. Es la suma exacta de las barras de la gráfica, incluida la venta de publicaciones ya cerradas, y el último día va en curso. ¿No cuadra con el panel de Mercado Libre? Aquí las canceladas se excluyen y los días se cortan con horario de México; ML las incluye y corta su ventana distinto.",
};

/* Iniciales de cuenta para las etiquetas compactas de precio */
const CUENTA_INI: Record<string, string> = {
  BEKURA: "BK", SANCORFASHION: "SC", AMAZON: "AMZ", GENERAL: "WOO",
};

/* PRECIO DE VENTA por canal. Solo cuenta la publicación ACTIVA (contrato de
   José): si varias activas comparten precio se muestra uno con sus etiquetas;
   si difieren, una línea por cuenta. Sin ninguna activa NO hay precio de venta
   vigente → se muestra el de la pausada en gris y marcado. */
/* Renglones por cuenta que COMPARTEN las columnas de PRECIO y MARGEN: el
   segundo renglón de una es el segundo de la otra, para poder leerlas en
   pareja. Devuelve las activas ya deduplicadas y ordenadas; si no hay ninguna,
   el precio de respaldo (la pausada más barata) para pintarlo en gris. */
function preciosDeVenta(fila: Fila): { lineas: PrecioCanal[]; pausado: number | null } {
  const todos = (fila.precios ?? []).filter((p) => p.price != null && p.canal !== "general");
  const activos = todos.filter((p) => (p.situacion ?? "").toLowerCase() === "active");
  const vistos = new Set<string>();
  const lineas = activos
    .filter((p) => {
      const k = `${p.cuenta}|${Number(p.price)}`;
      if (vistos.has(k)) return false;
      vistos.add(k);
      return true;
    })
    .sort((a, b) => (CUENTA_INI[a.cuenta] ?? a.cuenta).localeCompare(CUENTA_INI[b.cuenta] ?? b.cuenta)
                    || Number(b.price) - Number(a.price));
  if (lineas.length) return { lineas, pausado: null };
  return { lineas: [], pausado: todos.length ? Math.min(...todos.map((p) => Number(p.price))) : null };
}

/* Margen POR CUENTA (Eduardo, 30-jul). El costo es UNO por SKU
   (costos_validados es por variante, no por canal), así que lo único que
   cambia entre cuentas es el precio — y con precios distintos el margen
   distinto es real. Antes se mostraba un solo número calculado con el precio
   ACTIVO MÁS BARATO: en TEC-0977-NEG-800W decía 73.8% (SANCOR $1,199) y
   callaba el 88.3% de BEKURA ($2,678.81). Sigue siendo margen de CATÁLOGO: no
   descuenta la comisión del marketplace, que además varía por canal. */
function MargenVenta({ fila }: { fila: Fila }) {
  const costo = fila.costo == null ? null : Number(fila.costo);
  const { lineas, pausado } = preciosDeVenta(fila);
  const pct = (precio: number) => ((precio - costo!) / precio) * 100;
  const tono = (m: number) => (m < 20 ? "text-red-500" : "text-emerald-600");

  if (costo == null || costo <= 0) {
    return (
      <div className="text-slate-300" title="Sin costo validado para este SKU">—</div>
    );
  }
  const titulo = `calculado con costo ${fMoney(costo, 2)} (el mismo para todos los canales)`;

  // Espejo de PrecioVenta: si hubo venta, el margen se calcula contra el precio
  // REALIZADO y sale un solo número. Antes se pintaba uno por cuenta (6.1% y
  // 70.3% en el mismo renglón) y no había forma de leer "cómo va este SKU".
  const real = precioRealizado(fila);
  if (real != null && real > 0) {
    const m = pct(real);
    const detalle = lineas
      .map((p) => `${CUENTA_INI[p.cuenta] ?? p.cuenta} ${fNum(pct(Number(p.price)), 1)}%`)
      .join(" · ");
    return (
      <div
        title={`${titulo}\nSobre el precio real de venta del período (${fMoney(real, 2)})`
               + (detalle ? `\nSobre el precio publicado — ${detalle}` : "")}
      >
        <span className={`font-semibold tabular-nums ${tono(m)}`}>{fNum(m, 1)}%</span>
      </div>
    );
  }

  if (lineas.length === 0) {
    if (pausado == null || pausado <= 0) return <div className="text-slate-300">—</div>;
    return (
      <div className="font-semibold tabular-nums text-slate-400"
           title={`${titulo} · sobre el precio de una publicación pausada`}>
        {fNum(pct(pausado), 1)}%
      </div>
    );
  }
  return (
    <div className="space-y-0.5" title={titulo}>
      {lineas.map((p) => {
        const m = pct(Number(p.price));
        return (
          <div key={`${p.cuenta}${p.canal}${p.price}`}
               className="flex items-center justify-end gap-1">
            {/* Etiqueta en gris a propósito: el color de esta columna significa
                margen sano o flaco, y una insignia verde lo contradiría. */}
            <span className="rounded bg-slate-100 px-1 text-[9px] font-bold text-slate-500">
              {CUENTA_INI[p.cuenta] ?? p.cuenta}
            </span>
            <span className={`font-semibold tabular-nums ${tono(m)}`}>{fNum(m, 1)}%</span>
          </div>
        );
      })}
    </div>
  );
}

/* MARCA "2 PRODUCTOS": el mismo SKU pesa distinto en cada cuenta, según la
   báscula de la bodega de ML. No es un detalle de catálogo — significa que las
   dos publicaciones comparten un costo, un inventario y un margen que no le
   corresponden a una de ellas.

   Solo aparece cuando ML pesó AMBAS publicaciones. Comparar su báscula contra
   un peso capturado por nosotros detecta capturas malas (que ya sabemos que
   abundan) y no dice nada sobre si son dos productos: mezclando las dos fuentes
   el censo pasaba de 26 hallazgos sólidos a 462, casi todos falsos. */
function MarcaDosProductos({ div }: { div: Fila["peso_divergente"] }) {
  if (!div) return null;
  const lineas = div.detalle
    .map((d) => `  · ${d.cuenta}: ${d.peso_g} g — ${d.titulo ?? "sin título"}`)
    .join("\n");
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded bg-rose-50 px-1 py-px text-[9px] font-bold text-rose-600"
      title={`ESTE SKU TIENE DOS PRODUCTOS DISTINTOS\n\n`
             + `La bodega de Mercado Libre pesó cada publicación y no coinciden `
             + `(${div.min_g} g contra ${div.max_g} g, ${div.ratio}× de diferencia):\n${lineas}\n\n`
             + `Comparten un solo costo, un inventario y un margen, así que esos `
             + `números están mal para al menos uno de los dos. Separa el SKU antes `
             + `de tomar decisiones de precio o de reabasto.`}
    >
      <AlertTriangle size={9} />
      2 productos
    </span>
  );
}

/* VISITAS y CONVERSIÓN de Mercado Libre. El par responde la pregunta que ni las
   unidades ni el margen contestan: si algo vende poco, ¿es que nadie lo ve o es
   que quien lo ve no compra? Sin visitas el problema es de visibilidad (precio,
   posición, publicidad); con muchas visitas y poca conversión, el problema está
   en la ficha o el precio.

   El color va sobre la CONVERSIÓN, no sobre las visitas: juntar tráfico no es
   un logro si no se traduce en venta.

   Dos avisos que la celda no puede callar: ML a veces devuelve menos días que
   el período pedido (asterisco), y Amazon no aporta visitas — por eso la
   conversión se calcula solo con las unidades vendidas EN ML. */
function VisitasCR({ fila, dias }: { fila: Fila; dias: number }) {
  if (fila.visitas == null) {
    return (
      <span className="text-slate-300"
            title="Sin medición de visitas todavía. Se consultan a Mercado Libre por publicación y se guardan unas horas; al recorrer la tabla se van completando.">
        — · —
      </span>
    );
  }
  const parcial = fila.visitas_dias != null && fila.visitas_dias < dias - 2;
  const soloParte = fila.uds > fila.uds_ml;
  return (
    <div
      title={`${fNum(fila.visitas)} visitas a las publicaciones de Mercado Libre`
             + (fila.visitas_dias ? `\nVentana devuelta por ML: ${fila.visitas_dias} de ${dias} días` : "")
             + (fila.cr_pct != null
                ? `\nConversión ${fNum(fila.cr_pct, 1)}%: ${fNum(fila.uds_ml)} uds vendidas en ML ÷ ${fNum(fila.visitas)} visitas`
                : "")
             + (soloParte
                ? `\nOJO: este SKU vendió ${fNum(fila.uds)} uds en total; las otras ${fNum(fila.uds - fila.uds_ml)} son de Amazon y no cuentan aquí porque no aportan visitas.`
                : "")}
    >
      <div className="tabular-nums text-slate-700">
        {fNum(fila.visitas)}{parcial && <span className="text-amber-500">*</span>}
      </div>
      <div className={`text-[10px] font-semibold tabular-nums ${
          fila.cr_pct == null ? "text-slate-300"
          : fila.cr_pct >= 5 ? "text-emerald-600" : "text-amber-600"}`}>
        {fila.cr_pct == null ? "—" : `${fNum(fila.cr_pct, 1)}%`}
        {soloParte && <span className="ml-0.5 text-slate-400">ml</span>}
      </div>
    </div>
  );
}

/* MARGEN NETO (Eduardo, 5-ago): el margen de al lado pero con los cobros del
   marketplace ya descontados — Costo Final = costo + comisión + envío.

   La comisión NO es una tasa supuesta: es la que Mercado Libre cobró de verdad
   en los pedidos del período (channel.order_items.comision), promediada por
   unidad, así que ya trae la comisión de cada canal donde se vendió. Por eso la
   celda se queda vacía cuando no hubo ventas: sin venta no hay comisión que
   leer, e inventar una sería peor que no decir nada. Amazon la reporta en cero
   hasta que exista Finances API y también queda fuera a propósito. */
function MargenNeto({ fila }: { fila: Fila }) {
  const costo = fila.costo == null ? null : Number(fila.costo);
  const com = fila.comision_unit == null ? null : Number(fila.comision_unit);
  const envio = fila.envio_unit == null ? 0 : Number(fila.envio_unit);
  const real = precioRealizado(fila);
  const precio = real ?? (fila.precio == null ? null : Number(fila.precio));

  if (costo == null || costo <= 0)
    return <div className="text-slate-300" title="Sin costo validado para este SKU">—</div>;
  if (com == null)
    return (
      <div className="text-slate-300"
           title="Sin comisión que leer en el período: la comisión sale de los pedidos reales, no de una tasa estimada. Pasa cuando el producto no vendió, o cuando solo vende en Amazon (comisión aún en cero, falta Finances API).">
        —
      </div>
    );
  if (precio == null || precio <= 0)
    return <div className="text-slate-300" title="Sin precio con el que comparar">—</div>;
  // Costo poco creíble: el margen SÍ se pinta, pero en ámbar y con ⚠ (Eduardo,
  // 6-ago). Ocultarlo sacaba al SKU del análisis junto con la señal de que algo
  // pasa ahí; el ámbar dice "esto está en duda" sin fingir un veredicto.
  const dudoso = costoImplausible(precio, costo);
  const costoFinal = costo + com + envio;
  const m = ((precio - costoFinal) / precio) * 100;
  const cobros = com + envio;
  return (
    <div
      title={(dudoso ? avisoCostoImplausible(precio, costo) + "\n\n" : "")
             + `Costo final ${fMoney(costoFinal, 2)} = producto ${fMoney(costo, 2)}`
             + ` + comisión ${fMoney(com, 2)}`
             + (envio ? ` + envío ${fMoney(envio, 2)}` : " (sin envío estimado)")
             + `\nSobre el precio ${real != null ? "real de venta" : "publicado"} (${fMoney(precio, 2)})`
             + `\nQuedan ${fMoney(precio - costoFinal, 2)} por pieza`}
    >
      <div className={`flex items-center justify-end gap-1 font-semibold tabular-nums ${
          dudoso ? "text-amber-600" : m < 20 ? "text-red-500" : "text-emerald-600"}`}>
        {dudoso && <AlertTriangle size={11} className="shrink-0" />}
        {fNum(m, 1)}%
      </div>
      {/* Cuánto se lleva el canal por pieza: el dato que explica la caída
          contra la columna de la izquierda sin tener que abrir nada. */}
      <div className={`text-[10px] tabular-nums ${dudoso ? "text-amber-500" : "text-slate-400"}`}>
        {dudoso ? "costo dudoso" : `−${fMoney(cobros, 0)}`}
      </div>
    </div>
  );
}

/* Precio REALIZADO promedio del período: ingreso ÷ unidades de los pedidos.
   Es un promedio PONDERADO por lo vendido, no el promedio simple de los precios
   publicados — con BK a $949 y SC a $2,999, promediar a secas daría $1,974
   aunque el 95% se venda en BK. Devuelve null si no hubo venta en el período
   (sin unidades no hay precio realizado que calcular). */
function precioRealizado(fila: Fila): number | null {
  if (!fila.uds || fila.uds <= 0 || fila.venta == null) return null;
  return fila.venta / fila.uds;
}

/* Etiquetas de las cuentas donde el SKU tiene publicación activa — el contexto
   que se pierde al colapsar las líneas en un solo número. */
function cuentasDe(fila: Fila): string[] {
  return [...new Set(preciosDeVenta(fila).lineas.map((p) => p.cuenta))];
}

/* Detalle por cuenta para el tooltip: "BK $949 · SC $2,999". */
function detallePrecios(fila: Fila): string {
  const { lineas } = preciosDeVenta(fila);
  return lineas
    .map((p) => `${CUENTA_INI[p.cuenta] ?? p.cuenta} ${fMoney(p.price)}`)
    .join(" · ");
}

function PrecioVenta({ fila }: { fila: Fila }) {
  const { lineas, pausado } = preciosDeVenta(fila);
  const distintos = [...new Set(lineas.map((p) => Number(p.price)))];
  const real = precioRealizado(fila);

  // Con venta en el período manda el precio REALIZADO: un solo número que ya
  // pondera cuánto se vendió en cada cuenta. El desglose por cuenta no se
  // pierde — vive en el tooltip y, completo, en la ventana por canal.
  if (real != null) {
    const detalle = detallePrecios(fila);
    return (
      <div
        title={`Promedio real de venta del período (${fNum(fila.uds)} uds ÷ ${fMoney(fila.venta)})`
               + (detalle ? `\nPrecio publicado — ${detalle}` : "")}
      >
        <div className="font-semibold tabular-nums text-slate-800">{fMoney(real, 2)}</div>
        {distintos.length > 1 && (
          <div className="text-[10px] uppercase tracking-wide text-slate-400">promedio</div>
        )}
      </div>
    );
  }

  if (lineas.length === 0) {
    return (
      <div title="Ninguna publicación activa: no hay precio de venta vigente">
        <div className="font-semibold tabular-nums text-slate-400">
          {pausado == null ? "—" : fMoney(pausado)}
        </div>
        <div className="text-[10px] uppercase tracking-wide text-slate-300">sin activa</div>
      </div>
    );
  }
  // SIEMPRE una línea por cuenta (Eduardo, 30-jul). Antes, cuando todas las
  // activas coincidían, se colapsaba en un solo precio con las etiquetas al
  // pie — obligaba a leer dos formatos distintos y a deducir que "$989 BK SC"
  // significaba el mismo precio en ambas. El orden lo fija preciosDeVenta()
  // para que este renglón y el de MARGEN hablen de la misma cuenta.
  return (
    <div className="space-y-0.5"
         title={distintos.length > 1 ? "Precio distinto por cuenta" : undefined}>
      {lineas.map((p) => (
        <div key={`${p.cuenta}${p.canal}${p.price}`}
             className="flex items-center justify-end gap-1">
          <span className="rounded bg-emerald-50 px-1 text-[9px] font-bold text-emerald-700">
            {CUENTA_INI[p.cuenta] ?? p.cuenta}
          </span>
          <span className="font-semibold tabular-nums text-slate-800">{fMoney(p.price)}</span>
        </div>
      ))}
    </div>
  );
}

function Spark({ datos }: { datos: number[] }) {
  const max = Math.max(1, ...datos);
  return (
    <svg width={56} height={18} className="inline-block">
      {datos.map((v, i) => {
        const h = Math.max(1, Math.round((v / max) * 16));
        return (
          <rect key={i} x={i * 4} y={18 - h} width={3} height={h} rx={0.5}
                className={v > 0 ? "fill-emerald-500" : "fill-slate-200"} />
        );
      })}
    </svg>
  );
}

function Grafica({ serie }: { serie: Dashboard["serie"] }) {
  const W = 1200, H = 190, PAD = 46;
  const max = Math.max(1, ...serie.map((s) => Number(s.venta)));
  const bw = serie.length ? (W - PAD - 8) / serie.length : 1;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-48 w-full">
      {ticks.map((t, i) => {
        const y = H - 22 - (t / max) * (H - 40);
        return (
          <g key={i}>
            <line x1={PAD} x2={W - 4} y1={y} y2={y} className="stroke-slate-200" strokeWidth={1} />
            <text x={PAD - 6} y={y + 3} textAnchor="end" className="fill-slate-400 text-[9px]">
              ${t >= 1000 ? `${Math.round(t / 1000)}k` : Math.round(t)}
            </text>
          </g>
        );
      })}
      {serie.map((s, i) => {
        const h = (Number(s.venta) / max) * (H - 40);
        return (
          <rect key={s.date} x={PAD + i * bw + 1} y={H - 22 - h}
                width={Math.max(1.5, bw - 2)} height={h} rx={1}
                className="fill-indigo-500/85 hover:fill-indigo-400">
            <title>{`${s.date}: ${fMoney(s.venta)} · ${s.unidades} uds`}</title>
          </rect>
        );
      })}
      {serie.length > 0 && (
        <>
          <text x={PAD} y={H - 8} className="fill-slate-400 text-[9px]">{serie[0].date}</text>
          <text x={W - 4} y={H - 8} textAnchor="end" className="fill-slate-400 text-[9px]">
            {serie[serie.length - 1].date}
          </text>
        </>
      )}
    </svg>
  );
}

/* Gráfica del modal: la serie completa del período, con eje y mejor día */
function GraficaDetalle({ serie, metrica }: { serie: DetalleDia[]; metrica: "uds" | "venta" }) {
  const W = 720, H = 220, PAD = 46;
  const vals = serie.map((s) => (metrica === "uds" ? s.uds : s.venta));
  const max = Math.max(1, ...vals);
  const bw = serie.length ? (W - PAD - 8) / serie.length : 1;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const cadaX = Math.max(1, Math.ceil(serie.length / 8));
  const idxMejor = vals.indexOf(Math.max(...vals));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-56 w-full">
      {ticks.map((t, i) => {
        const y = H - 26 - (t / max) * (H - 48);
        return (
          <g key={i}>
            <line x1={PAD} x2={W - 4} y1={y} y2={y} className="stroke-slate-200" strokeWidth={1} />
            <text x={PAD - 6} y={y + 3} textAnchor="end" className="fill-slate-400 text-[9px]">
              {metrica === "venta"
                ? `$${t >= 1000 ? `${Math.round(t / 1000)}k` : Math.round(t)}`
                : Math.round(t)}
            </text>
          </g>
        );
      })}
      {serie.map((s, i) => {
        const v = metrica === "uds" ? s.uds : s.venta;
        const h = (v / max) * (H - 48);
        return (
          <rect key={s.date} x={PAD + i * bw + 0.5} y={H - 26 - h}
                width={Math.max(1.5, bw - 1.5)} height={Math.max(v > 0 ? 2 : 0, h)} rx={1}
                className={i === idxMejor && v > 0
                  ? "fill-amber-400"
                  : "fill-indigo-500/85 hover:fill-indigo-400"}>
            <title>{`${s.date}: ${s.uds} uds · ${fMoney(s.venta)}`}</title>
          </rect>
        );
      })}
      {serie.map((s, i) =>
        i % cadaX === 0 ? (
          <text key={`x${s.date}`} x={PAD + i * bw} y={H - 10}
                className="fill-slate-400 text-[9px]">
            {s.date.slice(5)}
          </text>
        ) : null,
      )}
    </svg>
  );
}

/* Modal de detalle de ventas (clic en el sparkline). Entrada/salida suaves.
   Abre en 14 días — la MISMA ventana de la miniatura que se clicó — y tiene su
   propio selector para expandir a 30/60/90 sin cerrar. */
function ModalDetalle({ fila, cuenta, onClose }: {
  fila: Fila; cuenta: string | null; onClose: () => void;
}) {
  const [datos, setDatos] = useState<Detalle | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [metrica, setMetrica] = useState<"uds" | "venta">("uds");
  const [diasModal, setDiasModal] = useState(14);
  const [visible, setVisible] = useState(false);

  const cerrar = useCallback(() => {
    setVisible(false);
    setTimeout(onClose, 200);           // deja terminar la transición de salida
  }, [onClose]);

  useEffect(() => {
    // setTimeout y NO requestAnimationFrame: rAF no dispara en pestañas que no
    // están componiendo frames (pestaña de fondo) y el modal abriría invisible.
    const t = setTimeout(() => setVisible(true), 20);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar(); };
    window.addEventListener("keydown", esc);
    return () => { clearTimeout(t); window.removeEventListener("keydown", esc); };
  }, [cerrar]);

  useEffect(() => {
    const q = new URLSearchParams({ sku: fila.sku, dias: String(diasModal) });
    if (cuenta) q.set("cuenta", cuenta);
    fetchSesion(`${API_BASE}/api/fulfillment/detalle?${q}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`API ${r.status}`); return r.json(); })
      .then(setDatos)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [fila.sku, diasModal, cuenta]);

  const r = datos?.resumen;
  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
      onClick={cerrar}
    >
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" />
      <div
        onClick={(e) => e.stopPropagation()}
        className={`relative w-full max-w-3xl rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl transition-all duration-200 ${visible ? "translate-y-0 scale-100 opacity-100" : "translate-y-3 scale-95 opacity-0"}`}
      >
        {/* Encabezado */}
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-indigo-600">{fila.sku}</span>
              <span className="flex items-center gap-0.5">
                {fila.cuentas.map((c) => (
                  <span key={c} title={c} className={`h-2 w-2 rounded-full ${CUENTA_DOT[c] ?? "bg-slate-400"}`} />
                ))}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${TIPO_CHIP[fila.tipo]}`}>{tipoLabel(fila)}</span>
            </div>
            <div className="truncate text-sm text-slate-500" title={fila.titulo ?? ""}>
              {fila.titulo ?? "—"}
            </div>
          </div>
          <button onClick={cerrar} aria-label="Cerrar"
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700">
            <X size={18} />
          </button>
        </div>

        {err && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            Error al cargar el detalle: {err}
          </div>
        )}

        {!err && !datos && (
          <div className="flex h-64 items-center justify-center text-sm text-slate-400">Cargando…</div>
        )}

        {datos && r && (
          <>
            {/* Resumen del período */}
            <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
              {[
                ["Unidades", fNum(r.total_uds)],
                ["$Venta", fMoney(r.total_venta)],
                ["Prom / día", fNum(r.venta_diaria, 2)],
                ["Días con venta", `${r.dias_con_venta} de ${datos.dias}`],
                ["Última venta", r.ultima_venta ?? "—"],
              ].map(([l, v]) => (
                <div key={l} className="rounded-lg bg-slate-50 px-3 py-2">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{l}</div>
                  <div className="text-sm font-bold text-slate-800">{v}</div>
                </div>
              ))}
            </div>

            {/* Toggle de métrica + ventana de días + mejor día */}
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
                  {(["uds", "venta"] as const).map((m) => (
                    <button key={m} onClick={() => setMetrica(m)}
                            className={`px-3 py-1.5 transition-colors ${metrica === m ? "bg-indigo-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                      {m === "uds" ? "Unidades" : "$ Venta"}
                    </button>
                  ))}
                </div>
                <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs font-semibold">
                  {[14, 30, 60, 90].map((d) => (
                    <button key={d} onClick={() => setDiasModal(d)}
                            className={`px-2.5 py-1.5 transition-colors ${diasModal === d ? "bg-slate-700 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>
                      {d}d
                    </button>
                  ))}
                </div>
              </div>
              {r.mejor_dia && (
                <span className="flex items-center gap-1.5 text-xs text-slate-500">
                  <CalendarDays size={13} className="text-amber-500" />
                  Mejor día: <b className="text-slate-700">{r.mejor_dia.date}</b>
                  ({r.mejor_dia.uds} uds · {fMoney(r.mejor_dia.venta)})
                </span>
              )}
            </div>

            <GraficaDetalle serie={datos.serie} metrica={metrica} />

            {/* Desglose por cuenta (solo con filtro en Todas) */}
            {datos.por_cuenta.length > 1 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {datos.por_cuenta.map((c) => (
                  <span key={c.cuenta}
                        className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-600">
                    <span className={`h-2 w-2 rounded-full ${CUENTA_DOT[c.cuenta] ?? "bg-slate-400"}`} />
                    <b>{c.cuenta}</b> {fNum(c.uds)} uds · {fMoney(c.venta)}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Modal de PRECIO/MARGEN por canal (clic en esas columnas) ─────────────
   La diferencia clave con las columnas de la tabla: aquí el precio es el
   REALIZADO — lo que de verdad se cobró en los pedidos del período (ingreso ÷
   unidades) — no el precio de lista de la publicación, que con las promos de
   Mercado Libre puede estar muy arriba de lo que entra. Incluye la línea de
   tiempo de cambios de precio (registrada desde el 17-jul-2026: antes de esa
   fecha no hay historia que mostrar). */
interface ResumenCanal {
  canal: string; cuenta: string; uds: number; ingreso: number;
  precio_prom: number | null; ultima_venta: string | null;
  costo: number | null; ganancia: number | null; margen_pct: number | null;
  // cobros del canal: aquí es donde se ve que el mismo producto deja distinto
  // según dónde se venda, porque la comisión la cobra cada canal a su manera
  comision_unit: number | null; envio_unit: number | null;
  costo_final: number | null; ganancia_neta: number | null;
  margen_neto_pct: number | null;
}
interface CambioPrecio {
  canal: string; cuenta: string;
  valor_anterior: string | null; valor_nuevo: string | null; fecha: string;
}
interface ResumenCanales {
  sku: string; dias: number; canales: ResumenCanal[];
  global: { uds: number; ingreso: number; precio_prom: number | null;
            costo: number | null; margen_prom: number | null; ganancia: number | null;
            comision_unit: number | null; envio_unit: number | null;
            costo_final: number | null; margen_neto: number | null;
            ganancia_neta: number | null };
  cambios_precio: CambioPrecio[]; historia_desde: string;
}

const CANAL_LBL: Record<string, string> = {
  mercado_libre: "Mercado Libre", amazon: "Amazon", general: "Web",
};

function ModalCanales({ fila, onClose }: { fila: Fila; onClose: () => void }) {
  const [datos, setDatos] = useState<ResumenCanales | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [dias, setDias] = useState(30);
  const [visible, setVisible] = useState(false);

  const cerrar = useCallback(() => {
    setVisible(false);
    setTimeout(onClose, 200);
  }, [onClose]);

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 20);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cerrar(); };
    window.addEventListener("keydown", esc);
    return () => { clearTimeout(t); window.removeEventListener("keydown", esc); };
  }, [cerrar]);

  useEffect(() => {
    const q = new URLSearchParams({ sku: fila.sku, dias: String(dias) });
    // fetchSesion y NO fetch: manda el token. Un fetch pelón respondía 401
    // desde que la API exige credencial (auditoría de Brandon, afb6421) —
    // esta llamada nació después de ese barrido y se le habría escapado.
    fetchSesion(`${API_BASE}/api/fulfillment/canales?${q}`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`API ${r.status}`); return r.json(); })
      .then(setDatos)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [fila.sku, dias]);

  const g = datos?.global;
  const tonoM = (m: number | null) =>
    m == null ? "text-slate-300" : m < 20 ? "text-red-500" : "text-emerald-600";

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${visible ? "opacity-100" : "opacity-0"}`}
      onClick={cerrar}
    >
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" />
      <div
        onClick={(e) => e.stopPropagation()}
        className={`relative max-h-[88vh] w-full max-w-4xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl transition-all duration-200 ${visible ? "translate-y-0 scale-100 opacity-100" : "translate-y-3 scale-95 opacity-0"}`}
      >
        {/* Encabezado */}
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-indigo-600">{fila.sku}</span>
              <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
                Precio y margen por canal
              </span>
            </div>
            <div className="truncate text-sm text-slate-500" title={fila.titulo ?? ""}>
              {fila.titulo ?? "—"}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {[14, 30, 60, 90].map((d) => (
              <button key={d} onClick={() => setDias(d)}
                      className={`rounded-lg px-2 py-1 text-xs font-bold transition-colors ${dias === d ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                {d}d
              </button>
            ))}
            <button onClick={cerrar} aria-label="Cerrar"
                    className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700">
              <X size={18} />
            </button>
          </div>
        </div>

        {err && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            Error al cargar: {err}
          </div>
        )}
        {!err && !datos && (
          <div className="flex h-48 items-center justify-center text-sm text-slate-400">Cargando…</div>
        )}

        {datos && g && (
          <>
            {/* Promedio de TODOS los canales */}
            <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-6">
              {[
                ["Unidades", fNum(g.uds), "", ""],
                ["Ingreso", fMoney(g.ingreso), "", ""],
                ["Precio prom.", g.precio_prom == null ? "—" : fMoney(g.precio_prom, 2), "",
                 "Lo que de verdad se cobró: ingreso ÷ unidades"],
                ["Margen bruto", g.margen_prom == null ? "—" : `${fNum(g.margen_prom, 1)}%`,
                 tonoM(g.margen_prom), "Solo contra el costo del producto, sin los cobros del canal"],
                ["Margen neto", g.margen_neto == null ? "—" : `${fNum(g.margen_neto, 1)}%`,
                 tonoM(g.margen_neto),
                 g.costo_final == null ? "Sin comisión que leer en el período"
                   : `Costo final ${fMoney(g.costo_final, 2)} = producto ${fMoney(g.costo, 2)}`
                     + ` + comisión ${fMoney(g.comision_unit, 2)}`
                     + (g.envio_unit ? ` + envío ${fMoney(g.envio_unit, 2)}` : "")],
                ["Ganancia neta", g.ganancia_neta == null ? "—" : fMoney(g.ganancia_neta),
                 g.ganancia_neta != null && g.ganancia_neta < 0 ? "text-red-500" : "",
                 "Ingreso menos el costo final de todas las piezas vendidas"],
              ].map(([l, v, tone, ayuda]) => (
                <div key={l as string} title={ayuda as string}
                     className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="text-[10px] font-bold uppercase tracking-wide text-slate-400">{l}</div>
                  <div className={`text-sm font-bold tabular-nums ${tone || "text-slate-800"}`}>{v}</div>
                </div>
              ))}
            </div>

            <div className="mb-1 text-[11px] text-slate-400">
              Precio <b>realizado</b>: lo que de verdad se cobró en los pedidos del período
              (ingreso ÷ unidades), no el precio publicado. El <b>margen neto</b> le resta
              además la comisión REAL que cobró cada canal y el envío estimado.
            </div>

            {/* Una fila por canal/cuenta */}
            <div className="mb-4 overflow-x-auto rounded-xl border border-slate-100">
              <table className="w-full min-w-[720px] text-xs">
                <thead>
                  <tr className="bg-slate-50 text-left text-[10px] uppercase tracking-wide text-slate-400">
                    <th className="px-3 py-2 font-semibold">Canal · Cuenta</th>
                    <th className="px-3 py-2 text-right font-semibold">Uds</th>
                    <th className="px-3 py-2 text-right font-semibold">Ingreso</th>
                    <th className="px-3 py-2 text-right font-semibold">Precio prom.</th>
                    <th className="px-3 py-2 text-right font-semibold"
                        title="Sin los cobros del canal: solo precio contra costo del producto">
                      Margen
                    </th>
                    <th className="px-3 py-2 text-right font-semibold"
                        title="Comisión REAL cobrada por el canal, por pieza (+ envío estimado si lo hay)">
                      Cobros/u
                    </th>
                    <th className="px-3 py-2 text-right font-semibold"
                        title="Producto + comisión + envío">Costo final</th>
                    <th className="px-3 py-2 text-right font-semibold"
                        title="Lo que de verdad queda después de los cobros del canal">
                      Margen neto
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {datos.canales.length === 0 && (
                    <tr><td colSpan={8} className="px-3 py-4 text-center text-slate-400">
                      Sin ventas en el período
                    </td></tr>
                  )}
                  {datos.canales.map((c) => (
                    <tr key={`${c.canal}|${c.cuenta}`} className="border-t border-slate-100">
                      <td className="whitespace-nowrap px-3 py-1.5">
                        <span className="font-semibold text-slate-700">{CANAL_LBL[c.canal] ?? c.canal}</span>
                        {c.cuenta && (
                          <span className="ml-1.5 rounded bg-slate-100 px-1 text-[9px] font-bold text-slate-500">
                            {CUENTA_INI[c.cuenta] ?? c.cuenta}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{fNum(c.uds)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{fMoney(c.ingreso)}</td>
                      <td className="px-3 py-1.5 text-right font-semibold tabular-nums text-slate-800">
                        {c.precio_prom == null ? "—" : fMoney(c.precio_prom, 2)}
                      </td>
                      <td className={`px-3 py-1.5 text-right tabular-nums ${tonoM(c.margen_pct)}`}>
                        {c.margen_pct == null ? "—" : `${fNum(c.margen_pct, 1)}%`}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-500"
                          title={c.comision_unit == null ? "Sin comisión registrada en el período"
                                 : `Comisión ${fMoney(c.comision_unit, 2)}`
                                   + (c.envio_unit ? ` + envío ${fMoney(c.envio_unit, 2)}` : " (sin envío estimado)")}>
                        {c.comision_unit == null ? "—"
                          : `−${fMoney(Number(c.comision_unit) + Number(c.envio_unit ?? 0), 2)}`}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">
                        {c.costo_final == null ? "—" : fMoney(c.costo_final, 2)}
                      </td>
                      <td className={`px-3 py-1.5 text-right font-bold tabular-nums ${tonoM(c.margen_neto_pct)}`}
                          title={c.ganancia_neta == null ? "" : `Ganancia neta del canal: ${fMoney(c.ganancia_neta)}`}>
                        {c.margen_neto_pct == null ? "—" : `${fNum(c.margen_neto_pct, 1)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Línea de tiempo de precios (trazabilidad de temporadas) */}
            <div className="mb-1 flex items-baseline justify-between">
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">
                Cambios de precio de la publicación
              </div>
              <div className="text-[10px] text-slate-400">
                registro desde el {datos.historia_desde}
              </div>
            </div>
            {datos.cambios_precio.length === 0 ? (
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-400">
                Sin cambios de precio registrados — el precio no se ha movido desde que hay registro.
              </div>
            ) : (
              <div className="max-h-44 overflow-y-auto rounded-xl border border-slate-100">
                {datos.cambios_precio.map((c, i) => {
                  const ant = c.valor_anterior == null ? null : Number(c.valor_anterior);
                  const nue = c.valor_nuevo == null ? null : Number(c.valor_nuevo);
                  const delta = ant && nue ? ((nue - ant) / ant) * 100 : null;
                  return (
                    <div key={i} className="flex items-center gap-2 border-t border-slate-50 px-3 py-1.5 text-xs first:border-t-0">
                      <span className="w-20 shrink-0 tabular-nums text-slate-400">{c.fecha}</span>
                      {c.cuenta && (
                        <span className="rounded bg-slate-100 px-1 text-[9px] font-bold text-slate-500">
                          {CUENTA_INI[c.cuenta] ?? c.cuenta}
                        </span>
                      )}
                      <span className="tabular-nums text-slate-500">{ant == null ? "—" : fMoney(ant, 2)}</span>
                      <span className="text-slate-300">→</span>
                      <span className="font-semibold tabular-nums text-slate-800">{nue == null ? "—" : fMoney(nue, 2)}</span>
                      {delta != null && Number.isFinite(delta) && (
                        <span className={`ml-auto tabular-nums ${delta < 0 ? "text-red-500" : "text-emerald-600"}`}>
                          {delta > 0 ? "+" : ""}{fNum(delta, 0)}%
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Página ────────────────────────────────────────────────────────────── */

export default function FulfillmentPage() {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [tabla, setTabla] = useState<TablaResp | null>(null);
  const [dias, setDias] = useState(60);
  const [cuenta, setCuenta] = useState<string | null>(null);
  const [estado, setEstado] = useState("");
  const [tipo, setTipo] = useState("");
  const [tam, setTam] = useState("");
  const [q, setQ] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [orden, setOrden] = useState("venta");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const [limit, setLimit] = useState(50);
  const [pagina, setPagina] = useState(0);
  const [cargando, setCargando] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [detalle, setDetalle] = useState<Fila | null>(null);
  // Modal de precio/margen por canal (clic en las columnas Precio o Margen)
  const [canalesDe, setCanalesDe] = useState<Fila | null>(null);
  // Popup "Productos más vendidos" (márgenes con cobros reales de Meli) —
  // vive como botón junto al período, no como sub-pestaña (Eduardo, 6-ago).
  const [verMargenes, setVerMargenes] = useState(false);

  const cargar = useCallback(async () => {
    setCargando(true); setErr(null);
    try {
      const qd = new URLSearchParams({ dias: String(dias) });
      if (cuenta) qd.set("cuenta", cuenta);
      const qt = new URLSearchParams(qd);
      if (estado) qt.set("estado", estado);
      if (tipo) qt.set("tipo", tipo);
      if (tam) qt.set("tam", tam);
      if (busqueda) qt.set("q", busqueda);
      qt.set("orden", orden);
      qt.set("dir", dir);
      qt.set("limit", String(limit));
      qt.set("offset", String(pagina * limit));
      const [d, t] = await Promise.all([
        fetchSesion(`${API_BASE}/api/fulfillment/dashboard?${qd}`, { cache: "no-store" }).then((r) => r.json()),
        fetchSesion(`${API_BASE}/api/fulfillment/tabla?${qt}`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      setDash(d); setTabla(t);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, [dias, cuenta, estado, tipo, tam, busqueda, orden, dir, limit, pagina]);

  useEffect(() => { void cargar(); }, [cargar]);

  // EN VIVO: el precio y el stock de ML llegan por webhook en segundos
  // (topics items / items_prices / stock-locations refrescan el listing), así
  // que la página se re-consulta sola cada 60 s — mismo patrón que el tab
  // Ventas. No parpadea: `cargando` solo mueve el ícono de Actualizar.
  useEffect(() => {
    const t = setInterval(() => { void cargar(); }, 60_000);
    return () => clearInterval(t);
  }, [cargar]);

  // "hace Xs" del último refresco, para que se vea que está vivo
  const [ahora, setAhora] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setAhora(Date.now()), 5_000);
    return () => clearInterval(t);
  }, []);
  const [ultimo, setUltimo] = useState<number | null>(null);
  useEffect(() => { if (tabla) setUltimo(Date.now()); }, [tabla]);
  const haceSeg = ultimo ? Math.max(0, Math.round((ahora - ultimo) / 1000)) : null;

  const totalPag = tabla ? Math.max(1, Math.ceil(tabla.total / limit)) : 1;

  /* Mismo campo → invierte; campo nuevo → abre en su dirección natural.
     Los setDir van FUERA de cualquier updater: anidarlos dentro del de setOrden
     los volvía impuros y React los corría dos veces en desarrollo, con lo que
     la inversión se aplicaba dos veces y parecía que el clic no hacía nada. */
  const ordenarPor = useCallback((id: string) => {
    if (orden === id) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setOrden(id); setDir(DIR_NATURAL[id] ?? "desc"); }
    setPagina(0);
  }, [orden]);
  const th = { orden, dir, onSort: ordenarPor };

  return (
    <>
        {/* Fila SKUs + actualizar (el banner y las sub-pestañas viven en el
            layout de la sección — ver app/analisis/layout.tsx) */}
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            {/* `dash?.skus.x` protegía solo a `dash`: cuando la API responde un
                error, el JSON existe pero sin `skus` y la página ENTERA se caía
                con "cannot read properties of undefined". Basta un hipo del
                backend para tumbar Análisis, así que el opcional va también en
                el nivel de adentro. */}
            <Kpi label="SKUs catálogo" value={fNum(dash?.skus?.skus_catalogo)} />
            <Kpi label="SKUs listados" value={fNum(dash?.skus?.skus_listados)} />
            <Kpi label="% activas" value={dash?.skus ? `${dash.skus.pct_activas}%` : "—"}
                 tone={dash?.skus && dash.skus.pct_activas < 50 ? "text-red-500" : "text-emerald-600"} />
            <Kpi label="% sin stock" value={dash?.skus ? `${dash.skus.pct_sin_stock}%` : "—"}
                 tone={dash?.skus && dash.skus.pct_sin_stock > 30 ? "text-red-500" : "text-emerald-600"} />
          </div>
          <button onClick={() => void cargar()}
                  title="Se actualiza solo cada 60 s (el precio y el stock de ML llegan por webhook)"
                  className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 shadow-sm hover:bg-slate-100">
            <RefreshCw size={14} className={cargando ? "animate-spin" : ""} />
            {haceSeg == null ? "Actualizar" : haceSeg < 10 ? "Al día" : `hace ${haceSeg}s`}
          </button>
        </div>

        {/* Cuenta */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Cuenta</span>
          <button onClick={() => { setCuenta(null); setPagina(0); }}
                  className={`rounded-lg border px-3 py-1.5 text-sm font-medium shadow-sm ${!cuenta ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-slate-200 bg-white text-slate-500 hover:text-slate-800"}`}>
            Todas
          </button>
          {(dash?.cuentas ?? []).map((c) => (
            <button key={c.cuenta} onClick={() => { setCuenta(c.cuenta); setPagina(0); }}
                    className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium shadow-sm ${cuenta === c.cuenta ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-slate-200 bg-white text-slate-500 hover:text-slate-800"}`}>
              <span className={`h-2 w-2 rounded-full ${CUENTA_DOT[c.cuenta] ?? "bg-slate-400"}`} />
              {c.cuenta} ({fNum(c.listings)})
            </button>
          ))}
        </div>

        {/* Fila 2: KPIs del período */}
        <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <Kpi label="Productos" value={fNum(dash?.kpis.productos)} />
          <Kpi label="Activos" value={fNum(dash?.kpis.activos)} tone="text-emerald-600" />
          <Kpi label="Activos FULL" value={fNum(dash?.kpis.activos_full)} tone="text-emerald-600" />
          <Kpi label="Stock FULL" value={fNum(dash?.kpis.stock_full)} />
          <Kpi label="Stock propio" value={fNum(dash?.kpis.stock_propio)} tone="text-violet-600" />
          <Kpi label={`Uds ${dias}d`} value={fNum(dash?.kpis.uds_periodo)} tone="text-emerald-600"
               ayuda={AYUDA_UDS_KPI} />
          <Kpi label={`$Venta ${dias}d`} value={fMoney(dash?.kpis.venta_periodo)} tone="text-emerald-600"
               ayuda={AYUDA_VENTA_KPI} />
        </div>

        {/* Período */}
        <div className="mb-3 flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Período</span>
          {[7, 30, 60, 90].map((d) => (
            <button key={d} onClick={() => { setDias(d); setPagina(0); }}
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium shadow-sm ${dias === d ? "bg-indigo-600 text-white" : "border border-slate-200 bg-white text-slate-500 hover:text-slate-800"}`}>
              {d} días
            </button>
          ))}
          <button onClick={() => setVerMargenes(true)}
                  title="Top por cuenta con margen sobre Costo Final: comisión y envío REALES de Meli"
                  className="ml-2 inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 shadow-sm transition-colors hover:bg-indigo-100">
            <BadgePercent size={15} />
            Productos más vendidos
          </button>
        </div>

        {/* Gráfica */}
        <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            Ventas diarias — {dias} días {cuenta ? `· ${cuenta}` : ""}
          </div>
          {dash ? <Grafica serie={dash.serie} /> :
            <div className="flex h-48 items-center justify-center text-slate-400">Cargando…</div>}
        </div>

        {/* Filtros */}
        <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
          {([
            ["Estado", estado, setEstado, [["", "Todos"], ["activa", "Activa"], ["pausada", "Pausada"], ["no_venta", "No venta"]]],
            ["Tipo", tipo, setTipo, [["", "Todos"], ["full", "FULL / FBA"], ["no_full", "DROP"], ["mixto", "Mixto"]]],
            ["Tamaño", tam, setTam, [["", "Todos"], ["S", "S"], ["M", "M"], ["L", "L"], ["XL", "XL"], ["S/C", "S/C"]]],
          ] as [string, string, (v: string) => void, [string, string][]][]).map(([lbl, val, set, opts]) => (
            <label key={lbl} className="flex items-center gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{lbl}</span>
              <select value={val} onChange={(e) => { set(e.target.value); setPagina(0); }}
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-slate-700 shadow-sm">
                {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          ))}
          <label className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Mostrar</span>
            <select value={limit} onChange={(e) => { setLimit(Number(e.target.value)); setPagina(0); }}
                    className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-slate-700 shadow-sm">
              {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <form className="ml-auto" onSubmit={(e) => { e.preventDefault(); setBusqueda(q.trim()); setPagina(0); }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar SKU o título…"
                   className="w-56 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-slate-700 shadow-sm outline-none focus:border-indigo-400" />
          </form>
          <span className="text-slate-500">{tabla ? `${fNum(tabla.total)} SKUs` : ""}</span>
          {/* Qué orden está aplicado, en palabras: la flecha sola no alcanzaba
              para saber qué estaba haciendo el clic (Eduardo, 30-jul). */}
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600">
            <span className="font-semibold">Orden:</span>
            {ORDEN_LABEL[orden] ?? orden}
            <span className="text-slate-400">
              ({dir === "asc" ? "menor primero" : "mayor primero"})
            </span>
            <Ayuda titulo="Ordenar la tabla"
                   texto="Haz clic en cualquier cabecera subrayable para ordenar por esa columna; un segundo clic invierte el sentido. Los SKUs sin dato en esa columna siempre quedan al final, se ordene como se ordene." />
          </span>
        </div>

        {err && (
          <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            Error al cargar: {err}
          </div>
        )}

        {/* Tabla compacta: celdas de dos líneas para caber sin scroll horizontal */}
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50/70">
                <Th id="sku" {...th}>Producto</Th>
                <Th info="estado" {...th}>Estado</Th>
                <Th right info="visitas" {...th}>Visitas · CR%</Th>
                <Th id="venta" right {...th}>Uds · $Venta</Th>
                <Th id="stock_full" right {...th}>FULL · Propio</Th>
                <Th id="edad" right {...th}>Edad s/v</Th>
                <Th id="cobertura" right {...th}>Cobertura</Th>
                <Th right info="precio" {...th}>Precio venta</Th>
                <Th id="margen" right {...th}>Margen</Th>
                <Th id="margen_neto" right info="margen_neto" {...th}>Margen neto</Th>
                <Th id="crec" right {...th}>Crec. 7d</Th>
                <Th info="spark" {...th}>14d</Th>
                <Th id="sugerido" right {...th}>Sugerido</Th>
              </tr>
            </thead>
            <tbody>
              {(tabla?.items ?? []).map((f) => (
                <tr key={f.sku} className="border-b border-slate-50 align-middle hover:bg-slate-50/60">
                  {/* Producto: SKU + dots + tam / título */}
                  <td className="max-w-[270px] px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[12px] font-semibold text-indigo-600">{f.sku}</span>
                      <span className="flex items-center gap-0.5">
                        {f.cuentas.map((c) => (
                          <span key={c} title={c} className={`h-1.5 w-1.5 rounded-full ${CUENTA_DOT[c] ?? "bg-slate-400"}`} />
                        ))}
                      </span>
                      <span className="rounded bg-slate-100 px-1 py-px text-[9px] font-bold text-slate-500">{f.tam}</span>
                      <MarcaDosProductos div={f.peso_divergente} />
                    </div>
                    <div className="truncate text-[11px] text-slate-500" title={f.titulo ?? ""}>{f.titulo ?? "—"}</div>
                  </td>
                  {/* Estado + tipo */}
                  <td className="whitespace-nowrap px-2 py-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${ESTADO_CHIP[f.estado]}`}>{ESTADO_LABEL[f.estado]}</span>
                    {/* NO VENTA habla de la VENTA, no de la publicación: el
                        mini-chip dice cómo está la publicación para no tener
                        que adivinarla (pedido de Eduardo, 5-ago) */}
                    {f.estado === "no_venta" && f.situacion_chip !== "otra" && (
                      <span className={`ml-1 rounded px-1 py-0.5 text-[9px] font-semibold ${f.situacion_chip === "activa" ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400"}`}
                            title="Situación de la publicación (la etiqueta NO VENTA habla del período, no de la publicación)">
                        {f.situacion_chip}
                      </span>
                    )}
                    <span className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-bold ${TIPO_CHIP[f.tipo]}`}>{tipoLabel(f)}</span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <VisitasCR fila={f} dias={dias} />
                  </td>
                  {/* Uds + venta */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <div className="font-semibold tabular-nums text-slate-800">{fNum(f.uds)}</div>
                    <div className="text-[11px] tabular-nums text-emerald-600">{fMoney(f.venta)}</div>
                  </td>
                  {/* Stock full + propio */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <div className={`font-semibold tabular-nums ${f.stock_full === 0 && f.uds > 0 ? "text-red-500" : "text-slate-800"}`}>
                      {fNum(f.stock_full)}
                    </div>
                    <div className={`text-[11px] tabular-nums ${f.stock_propio > 0 ? "text-amber-600" : "text-slate-300"}`}>
                      {fNum(f.stock_propio)}
                    </div>
                  </td>
                  <td className={`whitespace-nowrap px-2 py-1.5 text-right tabular-nums ${f.edad_sin_venta_d != null && f.edad_sin_venta_d > 30 ? "text-red-500" : "text-slate-500"}`}>
                    {f.edad_sin_venta_d == null ? "—" : `${f.edad_sin_venta_d}d`}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-slate-600">
                    {f.cobertura_d == null ? "—" : `${fNum(f.cobertura_d, 1)}d`}
                  </td>
                  {/* PRECIO DE VENTA por canal (solo publicaciones activas).
                      Clic → modal con el precio REALIZADO por canal. */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <button onClick={() => setCanalesDe(f)} title="Ver precio y margen por canal"
                            className="w-full rounded-md px-1 py-0.5 text-right transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <PrecioVenta fila={f} />
                    </button>
                  </td>
                  {/* Margen POR CUENTA, alineado renglón a renglón con la
                      columna de precio. El precio sugerido y el costo se
                      quitaron de la tabla (Eduardo, 29-jul): el margen resume
                      ambos y el costo viaja en el tooltip. */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <button onClick={() => setCanalesDe(f)} title="Ver precio y margen por canal"
                            className="w-full rounded-md px-1 py-0.5 text-right transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <MargenVenta fila={f} />
                    </button>
                  </td>
                  {/* Margen NETO: el mismo margen ya con la comisión real del
                      canal y el envío encima. Abre el mismo modal, donde se ve
                      canal por canal cuánto se lleva cada uno. */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <button onClick={() => setCanalesDe(f)} title="Ver el desglose de cobros por canal"
                            className="w-full rounded-md px-1 py-0.5 text-right transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <MargenNeto fila={f} />
                    </button>
                  </td>
                  <td className={`whitespace-nowrap px-2 py-1.5 text-right tabular-nums ${f.crec_7d_pct == null ? "text-slate-300" : f.crec_7d_pct >= 0 ? "text-emerald-600" : "text-red-500"}`}>
                    {f.crec_7d_pct == null ? "—" : `${f.crec_7d_pct > 0 ? "+" : ""}${fNum(f.crec_7d_pct)}%`}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    <button onClick={() => setDetalle(f)} title="Ver detalle de ventas"
                            className="rounded-md p-1 transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <Spark datos={f.spark ?? []} />
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right font-bold tabular-nums text-indigo-600">
                    {f.sugerido_full > 0 ? fNum(f.sugerido_full) : "—"}
                  </td>
                </tr>
              ))}
              {tabla && tabla.items.length === 0 && (
                <tr><td colSpan={12} className="px-3 py-10 text-center text-slate-400">Sin resultados con estos filtros.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Paginación */}
        {tabla && tabla.total > limit && (
          <div className="mt-4 flex items-center justify-center gap-3 text-sm">
            <button disabled={pagina === 0} onClick={() => setPagina((p) => p - 1)}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-600 shadow-sm disabled:opacity-40">← Anterior</button>
            <span className="text-slate-500">Página {pagina + 1} de {totalPag}</span>
            <button disabled={pagina + 1 >= totalPag} onClick={() => setPagina((p) => p + 1)}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-600 shadow-sm disabled:opacity-40">Siguiente →</button>
          </div>
        )}

        <p className="mt-4 text-center text-[11px] text-slate-400">
          Visitas y CR% son de Mercado Libre (Amazon no publica ese dato) · Stock propio = lo que hay
          en tu bodega (se actualiza cada 20 minutos) · Sugerido = piezas a mandar a FULL según el
          ritmo de venta de los últimos 45 días.
        </p>

      {detalle && (
        <ModalDetalle fila={detalle} cuenta={cuenta}
                      onClose={() => setDetalle(null)} />
      )}
      {verMargenes && (
        <MargenesRealesModal cerrar={() => setVerMargenes(false)} />
      )}
      {canalesDe && (
        <ModalCanales fila={canalesDe} onClose={() => setCanalesDe(null)} />
      )}
    </>
  );
}
