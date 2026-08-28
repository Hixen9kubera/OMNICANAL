"use client";

/**
 * /analisis — CLON del tablero kubera-fulfillment (José) con el TEMA CLARO
 * de OMNICANAL, alimentado por la BD kubera v4 vía /api/fulfillment/*
 * (primer lector de producción).
 *
 * Equivalencias vs el original (ver routers/fulfillment.py):
 *   STOCK ODOO → STOCK PROPIO · DÍAS ODOO → EDAD S/VENTA ·
 *   TAM → derivado de dimensiones de costos_validados.
 *
 * QUÉ CONTESTA LA TABLA (Eduardo, 10-ago). Dejó de ser "qué reabastecer" para
 * ser "qué deja dinero": salieron COBERTURA, SUGERIDO A FULL y el MARGEN BRUTO
 * (el que no descuenta los cobros del canal — convivía con el neto y obligaba a
 * elegir cuál leer), y entró el bloque de costos del popup de "Productos más
 * vendidos": costo base · comisión /u · envío /u · costo final · margen ·
 * ganancia del período. La fila ahora explica sola de dónde sale su margen.
 *
 * La tabla se COMPACTA en celdas de dos líneas (producto+tam+cuentas,
 * uds+venta, full+propio, envío+origen, margen+ganancia por pieza) para no
 * crecer a lo ancho con seis columnas más.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, BadgePercent, CalendarDays, RefreshCw, X } from "lucide-react";
import MargenesRealesModal from "@/components/MargenesRealesModal";
import { API_BASE, fetchSesion } from "@/lib/api";
import { avisoCostoImplausible, costoImplausible } from "@/lib/margen";
import Ayuda from "@/components/Ayuda";
import PanelHover from "@/components/PanelHover";
import ChipRevision from "@/components/ChipRevision";
import { CANAL_CORTO, CUENTA_DOT, CUENTA_INI } from "@/lib/canales";

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

/* Los cobros del canal, abiertos: una línea por canal/cuenta que de verdad le
   cobró algo a este SKU en el período. */
interface ComisionCanal {
  canal: string; cuenta: string; uds: number; comision_unit: number;
  /* De dónde salió: `pedidos` es el cobro orden por orden, que solo existe
     desde el 15/16-jul; `historico` es el agregado diario, que cubre desde
     abril pero reparte el cobro entre SKUs con menos precisión. */
  origen?: "pedidos" | "historico";
}
/* El envío es por CUENTA (la bodega que despachó), no por canal: `cubiertas`
   son las piezas cuyo embarque ya se consultó a ML — el resto sigue en
   estimado. */
interface EnvioCuenta {
  cuenta: string; uds: number; cubiertas: number; envio_unit: number | null;
}

/* Una publicación del SKU, tal como existe en su cuenta. `situacion` viene
   CRUDA del marketplace y no está normalizada: ML manda minúsculas
   ('active', 'paused', 'under_review') y Amazon MAYÚSCULAS ('DISCOVERABLE',
   'BUYABLE'). Comparar sin bajar a minúsculas falla en silencio. */
interface PublicacionCanal {
  cuenta: string; canal: string; situacion: string | null;
  item: string | null; price: number | null; full: boolean | null;
}

interface Fila {
  sku: string;
  cuentas: string[];
  titulo: string | null;
  tam: string;
  /* Medidas del costeo, en cm y kg. Son columnas propias de la tabla desde el
     14-ago: el chip solo dice la letra, y sin las medidas no hay forma de
     saber si un SKU está en el borde de su categoría. */
  largo: number | null;
  ancho: number | null;
  alto: number | null;
  peso: number | null;
  /* Todas las publicaciones del SKU, con precio o sin él. */
  publicaciones: PublicacionCanal[] | null;
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
  precio: number | null;              // el de la publicación ACTIVA
  precio_cualquiera: number | null;   // fallback cuando no hay ninguna activa
  precios: PrecioCanal[] | null;      // desglose por cuenta/canal
  precio_visto_at: string | null;
  precio_sugerido: number | null;
  // Precio contra el que se juzga el margen: el REALIZADO si hubo ventas en el
  // período, el publicado si no. Lo decide el backend para que la columna de
  // margen y el orden de la tabla hablen del mismo número.
  precio_ref: number | null;
  costo: number | null;
  /* El flete de importación YA está sumado dentro de `costo`. Viaja aparte solo
     para poder avisar cuando vale 0 — ver CostoBase. */
  costo_flete: number | null;
  /* Marca de validación del costeo (0032). Toda la columna de margen sale de
     `costo`: importa si ese costo fue verificado contra el packing list o si
     nadie lo ha mirado. `revision_movida` = se validó y la fila cambió después. */
  revisado_at?: string | null;
  revisado_por?: string | null;
  revision_movida?: boolean;
  // Cobros del marketplace por unidad (Eduardo, 5-ago): la comisión es la REAL
  // de los pedidos del período — por eso puede faltar (SKU sin ventas, o
  // Amazon, que aún la registra en cero). Con ellos sale el margen NETO.
  // `comisiones` y `envios` son el mismo dato ABIERTO por canal y por cuenta:
  // el promedio de la celda es ponderado, y sin el desglose no hay forma de ver
  // qué cuenta lo está jalando. Se leen al pasar el cursor.
  comision_unit: number | null;
  comisiones: ComisionCanal[] | null;
  // ENVÍO: el cobro REAL del embarque cuando ya se consultó a ML, el estimado
  // de costing mientras tanto. `envio_origen` dice cuál de los dos se está
  // viendo y `cobertura_envio_pct` sobre qué parte de las piezas se midió.
  envio_unit: number | null;
  envio_estimado: number | null;
  envio_origen: "real" | "estimado" | "sin dato";
  cobertura_envio_pct: number;
  envios: EnvioCuenta[] | null;
  costo_final: number | null;
  margen_neto_pct: number | null;
  ganancia_unit: number | null;
  ganancia_periodo: number | null;
  crec_7d_pct: number | null;
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

interface TablaResp {
  total: number; items: Fila[]; limit: number; offset: number;
  // Piezas de la página que todavía traen envío ESTIMADO. Mientras sea > 0 el
  // caché de embarques se sigue llenando y el refresco de 60 s lo completa.
  envios_pendientes?: number;
}

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

/* CUENTA_DOT, CUENTA_INI y CANAL_CORTO se mudaron a lib/canales.ts (14-ago):
   el popup de "Productos más vendidos" pinta las mismas cuentas y tenía sus
   propios colores. */
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
  precio: { titulo: "Precio de venta", texto: "Lo que de VERDAD se cobró en promedio durante el período: el dinero vendido dividido entre las piezas. Ya viene ponderado, así que si un producto se vende en dos cuentas a precios distintos, pesa más la que más vendió. Si no hubo ventas en el período se muestra el precio de la publicación activa, por cuenta. Haz clic para ver el desglose por canal." },
  costo: { titulo: "Costo base", texto: "Lo que cuesta traer una pieza: producto más flete de importación, del costeo validado. Es uno solo por producto — no cambia entre cuentas ni entre canales. Vacío significa que a ese SKU todavía no se le captura el costo, y sin él no hay margen que calcular." },
  comision: { titulo: "Comisión por unidad", texto: "Lo que Mercado Libre cobró DE VERDAD por vender una pieza en el período, no una tasa supuesta: sale de la comisión registrada en cada pedido, así que ya trae la del canal donde se vendió. Sale vacía cuando el producto no vendió en el período (sin venta no hay comisión que leer) o cuando solo vende en Amazon, que todavía la reporta en cero." },
  tamano: { titulo: "Tamaño", texto: "La categoría del bulto, del LADO MÁS LARGO del producto: menos de 30 cm es Chico, menos de 60 Mediano, menos de 120 Grande, y de ahí para arriba Extra grande. De ese mismo lado sale el flete de importación, así que un producto que salta de categoría suele saltar también de costo. Vacío significa que no se le han capturado medidas: sin ellas no hay categoría ni flete calculable. Ordena por el lado más largo real, no por la palabra." },
  medidas: { titulo: "Largo · Ancho · Alto", texto: "Las medidas de la PIEZA en centímetros, capturadas en el costeo. De ellas salen dos cosas: la letra del tamaño (S/M/L/XL), que se toma del lado más largo —por eso ese lado va resaltado en el renglón—, y el flete de importación. Un SKU sin medidas no tiene ni categoría de tamaño ni flete calculable: aparece con guiones y su costo de importación se queda corto. Los cortes de la letra son 30, 60 y 120 cm." },
  peso: { titulo: "Peso por pieza", texto: "El peso de UNA pieza en kilos, capturado en el costeo. Es lo que alimenta el envío estimado, así que un peso mal capturado infla el costo de todos los pedidos de ese SKU. Cuando sale en ÁMBAR es que el peso no cuadra con el volumen —más de 1.5 kg por litro—, y casi siempre significa lo mismo: se capturó el peso de la CAJA master como si fuera el de una pieza. Es un defecto conocido del catálogo, ~536 SKUs, y ordenar por esta columna es la forma de irlos sacando." },
  envio: { titulo: "Envío por unidad", texto: "Lo que Mercado Libre te cobró por mandar el paquete, promediado por pieza. Cuando dice REAL es el cobro del embarque consultado a Mercado Libre; cuando dice EST es el estimado por peso y medidas, que se equivoca en las dos direcciones y se va sustituyendo solo conforme se consultan los pedidos. El asterisco avisa que solo una parte de las piezas ya tiene el cobro real. En carritos con varios productos el cobro del paquete se reparte entre las piezas." },
  costo_final: { titulo: "Costo final", texto: "Lo que de verdad cuesta vender una pieza: costo base + comisión + envío. Es el número contra el que se compara el precio para saber si el producto deja dinero. No incluye el almacenamiento en FULL, que Mercado Libre cobra por mes y no por venta. Cuando sale en ÁMBAR con la nota «sin envío capturado», a ese producto le falta el costo de envío —no tiene el estimado por peso y medidas ni ha vendido un pedido del que leer el cobro real—, así que ese costo final es solo costo base + comisión y el margen de al lado sale optimista." },
  margen_neto: { titulo: "Margen", texto: "Lo que queda después de TODO: (precio real − costo final) ÷ precio real, con la comisión y el envío ya descontados. Este es el margen que de verdad queda; abajo va lo que deja cada pieza en pesos. Sale vacío cuando falta el costo del producto o cuando no hubo ventas en el período (sin venta no hay comisión que leer), y también en lo que solo vende en Amazon, que todavía reporta comisión cero. Ordena por esta columna de menor a mayor para ver primero lo que está vendiendo mal. Cuando el porcentaje sale en ÁMBAR con ⚠, el costo capturado es más de 1.5 veces el precio al que se vendió: el margen se muestra igual, pero sale de un costo poco creíble, así que léelo como referencia y no como un hecho — el problema está en el dato, no en la venta. Haz clic para ver el desglose por canal." },
  ganancia: { titulo: "Ganancia del período", texto: "Cuánto dinero dejó ese producto en el período completo: lo que gana una pieza por las piezas que se vendieron. Es la columna para ordenar cuando la pregunta es qué sostiene el negocio en pesos, no en porcentaje — un margen de 60% sobre tres piezas pesa menos que uno de 15% sobre trescientas. Vacía si no hubo ventas en el período." },
};

/* Dirección con la que abre cada columna: la que responde su pregunta útil.
   MARGEN abre de mayor a menor, pero el clic que más se usa es el segundo — el
   que lo invierte y sube lo que está vendiendo en pérdida. Este mapa debe
   coincidir con _ORDEN del backend (routers/fulfillment.py). */
const DIR_NATURAL: Record<string, "asc" | "desc"> = {
  sku: "asc", venta: "desc", stock_full: "desc", edad: "desc",
  costo: "desc", comision: "desc", costo_final: "desc",
  margen_neto: "desc", ganancia: "desc",
};
const ORDEN_LABEL: Record<string, string> = {
  sku: "SKU", venta: "$ venta", stock_full: "stock FULL", edad: "edad sin venta",
  costo: "costo base", comision: "comisión", costo_final: "costo final",
  margen_neto: "margen", ganancia: "ganancia del período",
};

/* Cabecera de tabla: ordena al hacer clic (segundo clic invierte) y lleva su
   "?". La flecha dibuja la dirección REAL — antes ponía ↓ siempre, y en
   Cobertura mentía porque esa columna ordena ascendente.
   VIVE A NIVEL DE MÓDULO a propósito: definida dentro del componente, React la
   ve como un tipo distinto en cada render y desmonta las 12 cabeceras — con el
   contador de "hace Xs" refrescando cada segundo, el tooltip no alcanzaba a
   verse. */
function Th({ id, children, right, info, compacto, orden, dir, onSort }: {
  id?: string; children: React.ReactNode; right?: boolean; info?: string;
  compacto?: boolean;
  orden: string; dir: "asc" | "desc"; onSort: (id: string) => void;
}) {
  const activa = !!id && orden === id;
  /* El "?" solo sale si la columna lo pide con `info`, ya NO por su `id`
     (Eduardo, 8-ago): las columnas cuya celda se explica sola al pasar el
     cursor no necesitan además un signo en el encabezado — eran dos formas de
     contar lo mismo y el "?" es la que estorba. */
  const ayuda = info ? AYUDA[info] : undefined;
  return (
    <th
      onClick={id ? () => onSort(id) : undefined}
      /* Sin `title` nativo: se encimaba con el tooltip del "?" (Eduardo,
         30-jul). Que la columna ordena ya lo dicen el cursor, la flecha y el
         chip "Orden:" del encabezado de la tabla. */
      className={[
        // `compacto` iguala el px-1 de las celdas de medida: el encabezado manda
        // el ancho de la columna, así que apretar solo la celda no sirve.
        `whitespace-nowrap ${compacto ? "px-1" : "px-2"} py-2.5 text-[10px] font-semibold uppercase tracking-wide`,
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
const AYUDA_ACTIVOS_KPI = {
  titulo: "Activos",
  texto: "SKUs con al menos una publicación a la venta HOY, en el alcance elegido. " +
    "Es el estado del listado y NO depende de si vendió: un producto activo sin ventas " +
    "en el período cuenta igual. En Mercado Libre «a la venta» es 'active'; en Amazon es " +
    "'buyable' o 'published' — 'discoverable' no cuenta, porque la publicación se " +
    "encuentra pero no se puede comprar. " +
    "OJO CON SUMAR: General NO es la suma de las cuentas, y no le falta nada. Este KPI " +
    "cuenta SKUs, y un mismo SKU puede estar activo en Bekura, en Sancor y en Amazon a la " +
    "vez; en General se cuenta UNA sola vez. Por eso sumar las tres pestañas siempre da de " +
    "más. Si lo que buscas es cuántas PUBLICACIONES hay —eso sí es aditivo, porque una " +
    "publicación pertenece a una sola cuenta— ése es otro número: una publicación con " +
    "variantes agrupa varios SKUs.",
};

const AYUDA_UDS_KPI = {
  titulo: "Cómo se cuentan las unidades",
  texto: "Cuenta PIEZAS, no pedidos: un pedido de 3 piezas suma 3 — la mayor parte del total viene de compras múltiples. Es la suma exacta de las barras de la gráfica, incluida la venta de publicaciones ya cerradas, y el último día va en curso. ¿No cuadra con el panel de Mercado Libre? Aquí las canceladas se excluyen y los días se cortan con horario de México; ML las incluye y corta su ventana distinto.",
};
const AYUDA_VENTA_KPI = {
  titulo: "Cómo se cuenta la venta",
  texto: "Venta BRUTA del período: no descuenta la comisión del marketplace, ni el costo, ni el envío. Es la suma exacta de las barras de la gráfica, incluida la venta de publicaciones ya cerradas, y el último día va en curso. ¿No cuadra con el panel de Mercado Libre? Aquí las canceladas se excluyen y los días se cortan con horario de México; ML las incluye y corta su ventana distinto.",
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

/* El MARGEN BRUTO (precio contra costo, sin los cobros del canal) se RETIRÓ de
   la tabla el 10-ago: convivía con el neto en columnas contiguas y la pregunta
   "¿cuál de los dos leo?" se repetía en cada revisión. El que decide es el
   neto, y el bruto sigue disponible canal por canal en el modal de precio y
   margen (clic en Precio o en Margen), donde el desglose sí lo justifica. */

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
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Visitas · CR%</span>
          <span className="mt-1 block text-slate-400">
            Sin medición todavía. Las visitas se le piden a Mercado Libre una
            publicación a la vez y se guardan unas horas; al recorrer la tabla se
            van completando solas.
          </span>
        </>
      }>
        <span className="text-slate-300">— · —</span>
      </PanelHover>
    );
  }
  const parcial = fila.visitas_dias != null && fila.visitas_dias < dias - 2;
  const soloParte = fila.uds > fila.uds_ml;
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Visitas y conversión</span>
        <Renglon etiqueta="Visitas en Mercado Libre" valor={fNum(fila.visitas)} />
        <Renglon etiqueta="Unidades vendidas en ML" valor={fNum(fila.uds_ml)} />
        {fila.cr_pct != null && (
          <span className="mt-1 block border-t border-slate-700 pt-1">
            <Renglon etiqueta="Conversión" valor={`${fNum(fila.cr_pct, 1)}%`} />
          </span>
        )}
        {/* La lectura del par, que es para lo que sirve la columna. */}
        <span className="mt-1.5 block text-slate-400">
          {fila.cr_pct == null
            ? "Sin unidades con qué calcular la conversión."
            : fila.cr_pct >= 5
              ? "Convierte bien: de cada 100 personas que la ven, compran "
                + `${fNum(fila.cr_pct, 1)}. Si vende poco, el problema es que no la ven.`
              : "Convierte por debajo de lo sano en ML (5%): mucha gente la ve y no "
                + "compra, así que el problema está en la ficha o en el precio, no en el tráfico."}
        </span>
        {soloParte && (
          <span className="mt-1 block text-amber-300">
            Este producto vendió {fNum(fila.uds)} piezas en total; las otras{" "}
            {fNum(fila.uds - fila.uds_ml)} son de Amazon y no cuentan aquí porque no
            aportan visitas.
          </span>
        )}
        {parcial && (
          <span className="mt-1 block text-amber-300">
            Mercado Libre devolvió {fila.visitas_dias} días de los {dias} pedidos:
            las visitas cubren menos período que las ventas.
          </span>
        )}
      </>
    }>
      <div className="tabular-nums text-slate-700">
        {fNum(fila.visitas)}{parcial && <span className="text-amber-500">*</span>}
      </div>
      <div className={`text-[10px] font-semibold tabular-nums ${
          fila.cr_pct == null ? "text-slate-300"
          : fila.cr_pct >= 5 ? "text-emerald-600" : "text-amber-600"}`}>
        {fila.cr_pct == null ? "—" : `${fNum(fila.cr_pct, 1)}%`}
        {soloParte && <span className="ml-0.5 text-slate-400">ml</span>}
      </div>
    </PanelHover>
  );
}

/* ── EL BLOQUE DE COSTOS (Eduardo, 10-ago) ────────────────────────────────
   Las mismas columnas del popup "Productos más vendidos", ahora en la tabla:
   Costo base · Comisión/u · Envío/u · Costo final · Margen · Ganancia. Antes el
   desglose vivía solo en ese popup —diez SKUs por cuenta— y la tabla resumía
   todo en un porcentaje: para saber POR QUÉ un margen estaba flaco había que
   salir de la tabla y buscar el producto en otra lista.

   Los números los arma el backend (_rehacer_costos) para que la celda, el
   tooltip y el ORDEN de la tabla salgan del mismo cálculo. Aquí solo se pintan.

   Regla que gobierna todo el bloque: sin costo base o sin comisión REAL no hay
   costo final — celda vacía antes que un número inventado. La comisión sale de
   los pedidos del período, así que falta en lo que no vendió y en lo que solo
   vende en Amazon (comisión aún en cero, falta Finances API). */

/* La celda simple con `title` nativo se RETIRÓ: las seis columnas de costo
   pasaron a la tarjeta flotante de abajo (Eduardo, 10-ago). Un `title` solo sabe
   pintar texto corrido, y lo que estas columnas tienen que explicar es una
   cuenta —de dónde sale el número y con qué se compara— que en renglones se lee
   de un vistazo y en un párrafo no se lee. */

/* SIN COSTO PERO VENDIENDO. De los 13,475 SKUs listados, 5,493 no tienen costo
   capturado — marcarlos todos sería ruido y se dejaría de ver la marca. Pero
   solo ~126 de ellos VENDIERON en el período, y esos son otra cosa: ahí ya se
   movió dinero sin saber si dejó o quitó. Esa es la fila que hay que traer a la
   vista, no la del SKU dormido al que le falta capturar un dato. */
const sinCostoVendiendo = (f: Fila) =>
  (f.costo == null || Number(f.costo) <= 0) && Number(f.uds || 0) > 0;

/* Renglón del panel: etiqueta a la izquierda, cifra a la derecha. */
function Renglon({ etiqueta, detalle, valor, tenue }: {
  etiqueta: string; detalle?: string; valor: string; tenue?: boolean;
}) {
  return (
    <span className={`mt-1 flex items-baseline justify-between gap-3 ${tenue ? "text-slate-400" : ""}`}>
      <span className="truncate">
        {etiqueta}
        {detalle && <span className="ml-1 text-slate-400">{detalle}</span>}
      </span>
      <span className="shrink-0 tabular-nums">{valor}</span>
    </span>
  );
}

/* FILTRO DE VARIAS OPCIONES. Un <select multiple> nativo se ve mal y se opera
   peor —hay que saber que se sostiene Ctrl—, así que va un botón con su panel
   de casillas. "Todos" no es una opción más: es limpiar, y por eso va como
   botón aparte y no como casilla que habría que desmarcar. */
function FiltroMultiple({ etiqueta, opciones, valor, onChange }: {
  etiqueta: string;
  opciones: [string, string][];
  valor: string[];
  onChange: (v: string[]) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const caja = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!abierto) return;
    const fuera = (e: MouseEvent) => {
      if (caja.current && !caja.current.contains(e.target as Node)) setAbierto(false);
    };
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") setAbierto(false); };
    document.addEventListener("mousedown", fuera);
    document.addEventListener("keydown", tecla);
    return () => {
      document.removeEventListener("mousedown", fuera);
      document.removeEventListener("keydown", tecla);
    };
  }, [abierto]);

  const alternar = (v: string) =>
    onChange(valor.includes(v) ? valor.filter((x) => x !== v) : [...valor, v]);

  // Con una o dos se nombran; con más, el conteo — "S · M · L · XL" no cabe en
  // el botón y se leería peor que "4 de 5".
  const resumen = valor.length === 0 ? "Todos"
    : valor.length <= 2 ? valor.join(" · ")
    : `${valor.length} de ${opciones.length}`;

  return (
    <div ref={caja} className="relative">
      <button type="button" onClick={() => setAbierto((a) => !a)}
              aria-expanded={abierto} aria-label={etiqueta}
              className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 shadow-sm transition-colors ${
                valor.length
                  ? "border-indigo-300 bg-indigo-50 font-semibold text-indigo-700"
                  : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"}`}>
        {resumen}
        <span className="text-[9px] text-slate-400">▼</span>
      </button>
      {abierto && (
        <div className="absolute left-0 top-full z-30 mt-1 min-w-[150px] rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
          <button type="button" onClick={() => onChange([])}
                  className={`block w-full rounded px-2 py-1 text-left ${
                    valor.length === 0
                      ? "bg-indigo-50 font-semibold text-indigo-700"
                      : "text-slate-600 hover:bg-slate-50"}`}>
            Todos
          </button>
          <div className="my-1 border-t border-slate-100" />
          {opciones.map(([v, l]) => (
            <label key={v}
                   className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50">
              <input type="checkbox" checked={valor.includes(v)} onChange={() => alternar(v)}
                     className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500" />
              <span className="text-slate-700">{l}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}


/* UDS · $VENTA. Las dos cifras de la celda son del PERÍODO elegido arriba y
   suman todas las cuentas, que es justo lo que no se ve mirando el número.

   SIN el "precio promedio" (Eduardo, 11-ago): importe ÷ piezas es exactamente
   la columna Precio venta —el SQL la arma con el mismo `venta / uds`—, así que
   el panel repetía un dato que ya está en la tabla, dos columnas a la derecha.
   Mismo criterio que quitó el "× N piezas vendidas" del Costo base en v0.87.0:
   una cifra derivada que compite con la columna que ya la muestra. */
function VentaUds({ fila, dias }: { fila: Fila; dias: number }) {
  const uds = Number(fila.uds || 0);
  const venta = fila.venta == null ? null : Number(fila.venta);
  if (uds <= 0)
    return (
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Uds · $Venta</span>
          <span className="mt-1 block text-slate-400">
            Este producto no vendió una sola pieza en los últimos {dias} días.
            No habla de la publicación —puede estar activa— sino del período.
          </span>
        </>
      }>
        <div>
          <div className="font-semibold tabular-nums text-slate-800">0</div>
          <div className="text-[11px] tabular-nums text-slate-300">—</div>
        </div>
      </PanelHover>
    );
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Uds · $Venta</span>
        <Renglon etiqueta="Piezas vendidas" valor={fNum(uds)} />
        <Renglon etiqueta="Importe" valor={fMoney(venta)} />
        <span className="mt-1.5 block text-slate-400">
          Últimos {dias} días, sumando TODAS las cuentas donde se vende este
          SKU. Es venta bruta: todavía no se le descuenta comisión ni envío.
        </span>
      </>
    }>
      <div>
        <div className="font-semibold tabular-nums text-slate-800">{fNum(uds)}</div>
        <div className="text-[11px] tabular-nums text-emerald-600">{fMoney(fila.venta)}</div>
      </div>
    </PanelHover>
  );
}

/* FULL · PROPIO. Son DOS bodegas distintas y la confusión más cara del panel es
   sumarlas: tener 300 piezas propias no evita quedarse sin vender si FULL está
   en cero, porque una publicación FULL solo surte de la bodega de Meli. */
function StockFullPropio({ fila }: { fila: Fila }) {
  const full = Number(fila.stock_full || 0);
  const propio = Number(fila.stock_propio || 0);
  const quiebre = full === 0 && fila.uds > 0;
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">FULL · Propio</span>
        <Renglon etiqueta="En bodega del marketplace" detalle="FULL / FBA"
                 valor={fNum(full)} />
        <Renglon etiqueta="En bodega propia" detalle="DROP" valor={fNum(propio)} />
        <span className="mt-1.5 block text-slate-400">
          Son inventarios SEPARADOS y no se suman: una publicación FULL solo
          surte de la bodega de Meli. Reponer significa mover piezas de Propio
          a FULL, no comprar más.
        </span>
        {quiebre && (
          <span className="mt-1 block text-amber-300">
            {propio > 0
              ? `Vendió en el período y FULL está en cero teniendo ${fNum(propio)} ${propio === 1 ? "pieza propia" : "piezas propias"}: hay con qué reponer, pero mientras tanto no vende.`
              : "Vendió en el período y no queda stock en ninguna de las dos bodegas."}
          </span>
        )}
      </>
    }>
      <div>
        <div className={`font-semibold tabular-nums ${quiebre ? "text-red-500" : "text-slate-800"}`}>
          {fNum(full)}
        </div>
        <div className={`text-[11px] tabular-nums ${propio > 0 ? "text-amber-600" : "text-slate-300"}`}>
          {fNum(propio)}
        </div>
      </div>
    </PanelHover>
  );
}

/* Cómo se lee el estado CRUDO de una publicación, por marketplace.

   No se puede normalizar a "activa/pausada" sin perder la verdad: en Amazon
   DISCOVERABLE es la mayoría (1,258 de 1,501) y significa que la publicación
   existe y se encuentra, pero NO que se pueda comprar. Tratarla como activa
   inflaría el conteo de vivas; tratarla como pausada diría que está detenida,
   que tampoco es cierto. Se nombra tal cual y se dice si vende o no. */
function estadoPublicacion(canal: string, situacion: string | null) {
  const s = (situacion ?? "").toLowerCase();
  if (canal === "amazon") {
    if (s === "buyable") return { texto: "a la venta", vende: true };
    if (s === "published") return { texto: "publicada", vende: true };
    if (s === "discoverable") return { texto: "visible, no comprable", vende: false };
  } else {
    if (s === "active") return { texto: "activa", vende: true };
    if (s === "paused") return { texto: "pausada", vende: false };
    if (s === "under_review") return { texto: "en revisión por ML", vende: false };
    if (s === "inactive") return { texto: "inactiva", vende: false };
  }
  return { texto: s ? s : "sin estado", vende: false };
}

/* TAMAÑO EN PALABRAS. La categoría sale del LADO MÁS LARGO (menos de 30 cm es
   chico, menos de 60 mediano, menos de 120 grande, de ahí para arriba extra
   grande) y desde el 18-ago vive en su PROPIA COLUMNA, no como etiqueta pegada
   al nombre (Eduardo): junto al SKU competía por espacio con los puntos de
   cuenta y no se podía ordenar por ella.

   Las LETRAS siguen siendo el formato de la API —el filtro y el orden viajan
   como S/M/L/XL— y las palabras viven solo aquí, en la pantalla. Cambiar el
   código en el backend habría roto el filtro y los enlaces guardados. */
/* EL COLOR ES UNA ESCALA, NO UN SEMÁFORO (Eduardo, 18-ago: "ponlo por colores").
   Se evitan a propósito el verde, el ámbar y el rojo: en esta tabla ya
   significan margen sano, aviso y pérdida, y un tamaño en rojo se leería como
   un problema — ser grande no es un defecto, es un costo de flete más alto.
   La rampa va de neutro a intenso (gris → celeste → índigo → fucsia): se
   entiende como "cada vez más", que es justo lo que la columna mide. */
const TAMANO: Record<string, { txt: string; clase: string }> = {
  S:     { txt: "Chico",        clase: "bg-slate-100 text-slate-600" },
  M:     { txt: "Mediano",      clase: "bg-sky-100 text-sky-800" },
  L:     { txt: "Grande",       clase: "bg-indigo-100 text-indigo-800" },
  XL:    { txt: "Extra grande", clase: "bg-fuchsia-100 text-fuchsia-800" },
  "S/C": { txt: "—",            clase: "" },
};

function CeldaTamano({ fila }: { fila: Fila }) {
  const t = TAMANO[fila.tam] ?? TAMANO["S/C"];
  const l = Number(fila.largo ?? 0), a = Number(fila.ancho ?? 0), h = Number(fila.alto ?? 0);
  const mayor = Math.max(l, a, h);
  // Sin medidas NO lleva chip: pintar un vacío de color lo haría parecer una
  // categoría más, y es la ausencia del dato.
  if (!(mayor > 0))
    return (
      <span className="text-[11px] text-slate-300"
            title="Sin medidas capturadas: no hay de dónde sacar la categoría">—</span>
    );
  return (
    <span className={`whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-semibold ${t.clase}`}
          title={`Su lado más largo mide ${fNum(mayor, 1)} cm. Los cortes son 30, 60 y 120 cm.`}>
      {t.txt}
    </span>
  );
}

/* DENSIDAD. El peso de la CAJA master capturado como peso de la PIEZA es un
   defecto conocido del catálogo (~536 SKUs) y se delata solo: arriba de
   1.5 kg/L no hay producto de esas medidas que pese eso. Con la tarjeta fuera,
   el aviso se muda a la celda de Peso — si no, al pasar las medidas a columnas
   se perdería la única señal que teníamos de un flete inflado. */
function densidadRara(fila: Fila): number | null {
  const l = Number(fila.largo ?? 0), a = Number(fila.ancho ?? 0), h = Number(fila.alto ?? 0);
  const peso = fila.peso == null ? null : Number(fila.peso);
  const litros = (l * a * h) / 1000;
  if (!peso || litros <= 0) return null;
  const d = peso / litros;
  return d > 1.5 ? d : null;
}

/* CELDA DE MEDIDA. `decide` marca el lado del que sale la letra: sin eso, un
   SKU de 29 cm y otro de 31 caen en categorías distintas sin que se vea por
   qué, que es justo lo que explicaba la tarjeta vieja. */
function CeldaMedida({ v, decide }: { v: number | string | null; decide?: boolean }) {
  /* `px-1` y no `px-2`: son cuatro columnas nuevas de números cortos y con el
     acolchado normal empujaban Ganancia fuera del borde. */
  if (v == null || Number(v) <= 0)
    return <td className="px-1 py-1.5 text-right text-slate-300">—</td>;
  return (
    <td className={`whitespace-nowrap px-1 py-1.5 text-right tabular-nums ${
      decide ? "font-semibold text-slate-700" : "text-slate-500"}`}
        title={decide ? "El lado más largo: de este sale la letra del tamaño" : undefined}>
      {fNum(Number(v), 1)}
    </td>
  );
}

/* PUNTOS DE CUENTA. Los puntitos dicen EN CUÁNTAS cuentas está el SKU, pero no
   cuáles ni cómo — y "está en tres cuentas" con las tres pausadas se lee igual
   que con las tres vendiendo. La tarjeta abre el censo. */
function PuntosCuenta({ fila }: { fila: Fila }) {
  const pubs = fila.publicaciones ?? [];
  const puntos = (
    <span className="flex items-center gap-0.5">
      {fila.cuentas.map((c) => (
        <span key={c} className={`h-2 w-2 rounded-full ${CUENTA_DOT[c] ?? "bg-slate-400"}`} />
      ))}
    </span>
  );
  if (!pubs.length) return puntos;

  const venden = pubs.filter((p) => estadoPublicacion(p.canal, p.situacion).vende).length;
  return (
    <PanelHover ancho={320} panel={
      <>
        <span className="block font-semibold text-white">
          Publicado en {pubs.length} {pubs.length === 1 ? "lugar" : "lugares"}
        </span>
        {pubs.map((p, i) => {
          const e = estadoPublicacion(p.canal, p.situacion);
          return (
            <span key={`${p.cuenta}|${p.item ?? i}`}
                  className="mt-1 flex items-baseline justify-between gap-3">
              <span className="truncate">
                <span className={e.vende ? "text-emerald-300" : "text-slate-400"}>●</span>
                {" "}{CANAL_CORTO[p.canal] ?? p.canal} · {CUENTA_INI[p.cuenta] ?? p.cuenta}
                {/* El espacio va DENTRO del texto y no solo como margen: el
                    panel se copia y pegar "SCFULL" no se entiende. */}
                {p.full && <span className="text-slate-400">{" · FULL"}</span>}
              </span>
              <span className={`shrink-0 ${e.vende ? "text-emerald-300" : "text-slate-400"}`}>
                {e.texto}
              </span>
            </span>
          );
        })}
        <span className="mt-1.5 block text-slate-400">
          {venden === 0
            ? "Ninguna puede comprarse ahora mismo: el SKU existe en el catálogo pero no está vendiendo en ningún lado."
            : `${venden} de ${pubs.length} se puede comprar; el resto existe pero no vende.`}
        </span>
      </>
    }>{puntos}</PanelHover>
  );
}

/* EDAD S/V. La trampa de esta columna es que NO respeta el período de arriba:
   Uds y $Venta miran los días elegidos, pero la edad recorre TODO el historial
   (el SQL saca max(date) sin filtro de fechas). Por eso un producto puede tener
   0 piezas vendidas en el período y aun así una edad de 3 días — vendió justo
   antes de que empezara la ventana. Leído sin saber eso, el número se
   contradice con la celda de al lado. */
function EdadSinVenta({ fila, dias }: { fila: Fila; dias: number }) {
  const d = fila.edad_sin_venta_d;
  const stock = Number(fila.stock_full || 0) + Number(fila.stock_propio || 0);
  const viejo = d != null && d > 30;

  if (d == null)
    return (
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Edad sin venta</span>
          <span className="mt-1 block text-slate-400">
            No hay NINGUNA venta de este SKU en todo el historial, así que no hay
            desde cuándo contar. El guion no es un dato que falte: es que nunca
            ha vendido.
          </span>
          {stock > 0 && (
            <span className="mt-1 block text-amber-300">
              Y tiene {fNum(stock)} {stock === 1 ? "pieza" : "piezas"} en bodega
              ocupando lugar.
            </span>
          )}
        </>
      }>
        <span className="text-slate-500">—</span>
      </PanelHover>
    );

  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Edad sin venta</span>
        <Renglon etiqueta="Días desde la última venta" valor={`${fNum(d)} d`} />
        {stock > 0 && (
          <Renglon etiqueta="Piezas en bodega" detalle="FULL + propio"
                   valor={fNum(stock)} tenue />
        )}
        <span className="mt-1.5 block text-slate-400">
          Cuenta desde el último día CON venta, mirando todo el historial — no
          solo los {dias} días del filtro de arriba. Por eso puede haber 0 piezas
          vendidas en el período y aun así una edad chica.
        </span>
        {viejo && (
          <span className="mt-1 block text-amber-300">
            {stock > 0
              ? `Más de un mes sin vender con ${fNum(stock)} ${stock === 1 ? "pieza" : "piezas"} encima: eso es dinero detenido.`
              : "Más de un mes sin vender, pero no queda stock: no hay dinero detenido que rescatar."}
          </span>
        )}
      </>
    }>
      <span className={viejo ? "text-red-500" : "text-slate-500"}>{fNum(d)}d</span>
    </PanelHover>
  );
}

/* COMISIÓN: el promedio de la celda es PONDERADO por unidades, así que sin el
   desglose no hay forma de saber qué cuenta lo está jalando. */
function ComisionUnit({ fila }: { fila: Fila }) {
  const v = fila.comision_unit == null ? null : Number(fila.comision_unit);
  const lineas = fila.comisiones ?? [];
  if (v == null)
    return (
      <div className="text-slate-300"
           title="Sin comisión que leer: el producto no vendió en el período, o solo vende en Amazon (comisión aún en cero)">
        —
      </div>
    );
  // Piezas del período que NO aportaron comisión: hoy son las de Amazon, que
  // la reporta en cero. Decirlo evita leer el promedio como si cubriera todo.
  const conComision = lineas.reduce((s, l) => s + l.uds, 0);
  const sinComision = Math.max(0, fila.uds - conComision);
  return (
    <PanelHover
      panel={
        <>
          <span className="block font-semibold text-white">Comisión por canal</span>
          {lineas.map((l) => (
            <Renglon key={`${l.canal}|${l.cuenta}`}
                     etiqueta={`${CANAL_CORTO[l.canal] ?? l.canal} · ${CUENTA_INI[l.cuenta] ?? l.cuenta}`}
                     detalle={`${fNum(l.uds)} uds`}
                     valor={`${fMoney(l.comision_unit, 2)}/u`} />
          ))}
          {lineas.length > 1 && (
            <Renglon etiqueta="Promedio ponderado" valor={`${fMoney(v, 2)}/u`} />
          )}
          {/* Este renglón culpaba a Amazon de TODAS las piezas sin comisión, y
              casi nunca era cierto: en TEC-1284-NEG-27" eran 156 piezas, y no
              son de Amazon sino de junio, cuando order_items todavía no
              capturaba. Como desde el payload no se distingue una causa de la
              otra, se nombran las dos en vez de afirmar la equivocada. */}
          {sinComision > 0 && (
            <span className="mt-1.5 block text-slate-400">
              {fNum(sinComision)} piezas del período no traen comisión: o son de
              Amazon, que aún la reporta en cero, o son anteriores al 15 de
              julio, cuando todavía no se guardaba el detalle de cada pedido.
            </span>
          )}
          {/* Los dos orígenes NO valen lo mismo y la celda no puede callarlo:
              el cobro orden por orden es exacto; el agregado diario reparte
              bien el total del mes pero con ruido al bajarlo a cada SKU. */}
          {lineas.some((l) => l.origen === "historico") ? (
            <span className="mt-1.5 block text-amber-300">
              Sale del histórico diario, no de los pedidos: antes del 15 de
              julio no hay detalle orden por orden. El total del mes es
              confiable; el reparto entre productos es aproximado.
              {/* La edad va AQUÍ y no solo en su columna porque las dos señales
                  se leen juntas o no se leen: de los 283 SKUs cuyo margen sale
                  del histórico, 280 no tienen una sola venta reciente —mediana
                  de 66 días sin vender, contra 7 de los demás—. El riesgo es
                  ordenar por margen, ver un −141% y tratarlo como una fuga
                  activa de un producto que lleva mes y medio quieto. */}
              {fila.edad_sin_venta_d != null && fila.edad_sin_venta_d > 30 && (
                <> Y no vende desde hace {fNum(fila.edad_sin_venta_d)} días: este
                margen es un cierre de cuentas de algo que ya paró, no una señal
                de cómo va hoy.</>
              )}
            </span>
          ) : (
            <span className="mt-1.5 block text-slate-400">
              Es la comisión REAL de los pedidos, no una tasa estimada.
            </span>
          )}
        </>
      }
    >
      <div className="tabular-nums text-slate-700">{fMoney(v, 2)}</div>
      {lineas.length > 1 && (
        <div className="text-[9px] uppercase tracking-wide text-slate-400">
          {lineas.length} canales
        </div>
      )}
    </PanelHover>
  );
}

/* COSTO BASE. La tarjeta señala al culpable cuando el costo no es creíble —
   ese aviso vivía en Margen y en Ganancia, que son las víctimas, no el origen.

   SIN el "× N piezas vendidas" (Eduardo, 8-ago): multiplicar el costo por las
   unidades daba una cifra que no es de esta columna. El costo del período ya
   lo cuentan Costo final y Ganancia, cada uno con los cobros que le tocan; aquí
   sobraba y se leía como si fuera un total propio. */
function CostoBase({ fila, dias }: { fila: Fila; dias: number }) {
  const v = fila.costo == null ? null : Number(fila.costo);
  const precio = fila.precio_ref == null ? null : Number(fila.precio_ref);
  if (v == null || v <= 0)
    return (
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Costo base</span>
          <span className="mt-1 block text-slate-400">
            A este producto no se le ha capturado el costo. Sin él no hay costo
            final ni margen que calcular — por eso esas dos columnas salen vacías.
          </span>
          {sinCostoVendiendo(fila) && (
            <span className="mt-1 block text-amber-300">
              Y sí está vendiendo: {fNum(fila.uds)} {fila.uds === 1 ? "pieza" : "piezas"}
              {fila.venta != null && ` y ${fMoney(fila.venta)}`} en {dias} días se
              movieron sin saber cuánto costaron. Por eso el renglón va marcado.
            </span>
          )}
        </>
      }>
        {/* El guion ya no va en gris casi invisible: en esta columna un vacío no
            es "no aplica", es un dato que falta. Ámbar cuando además vendió. */}
        <div className={sinCostoVendiendo(fila) ? "font-semibold text-amber-500" : "text-slate-400"}>—</div>
      </PanelHover>
    );
  const dudoso = precio != null && costoImplausible(precio, v, fila.revisado_at);
  // SIN FLETE. El costo base ES producto + flete de importación, así que un
  // flete en cero no es "no aplica": es un costo al que le falta un pedazo, y
  // uno grande —31% del total en promedio—. El margen sale optimista y nada lo
  // decía (Eduardo, 14-ago).
  const flete = fila.costo_flete == null ? null : Number(fila.costo_flete);
  const sinFlete = flete != null && flete <= 0;
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Costo base</span>
        <Renglon etiqueta="Por pieza" valor={fMoney(v, 2)} />
        {flete != null && flete > 0 && (
          <Renglon etiqueta="…del cual, flete" valor={fMoney(flete, 2)}
                   detalle={`${fNum(100 * flete / v, 0)}%`} tenue />
        )}
        <span className="mt-1.5 block text-slate-400">
          Producto + flete de importación, del costeo validado. Es uno solo por
          producto: no cambia entre cuentas ni entre canales.
        </span>
        {sinFlete && (
          <span className="mt-1 block text-amber-300">
            SIN FLETE DE IMPORTACIÓN: este costo es solo el del producto. El
            flete pesa 31% del costo en promedio, así que el margen y la
            ganancia de este renglón salen mejores de lo que son. Se captura en
            el costeo — no es que no se pueda calcular.
          </span>
        )}
        {dudoso && (
          <span className="mt-1 block text-amber-300">
            Ojo: es {fNum(v / precio!, 1)}× el precio al que se vendió
            ({fMoney(precio, 2)}). El margen y la ganancia salen de aquí, así que
            léelos como referencia — el problema está en el costo, no en la venta.
          </span>
        )}
      </>
    }>
      <div className={`tabular-nums ${sinFlete ? "text-amber-600" : "text-slate-700"}`}>
        {fMoney(v, 2)}
        {sinFlete && <span className="ml-0.5 font-bold">⚠</span>}
      </div>
    </PanelHover>
  );
}

/* ENVÍO: real o estimado, dicho en la celda. El estimado de costing miente en
   las dos direcciones ($349 contra $88 reales en Malla Sombra, y 141 SKUs con
   flete en $0), así que un margen calculado con él no es el margen — pero
   tampoco se puede esperar a tener todos los embarques consultados para mostrar
   algo. Se muestra lo que hay y se declara cuál es. */
function EnvioUnit({ fila }: { fila: Fila }) {
  const v = fila.envio_unit == null ? null : Number(fila.envio_unit);
  const real = fila.envio_origen === "real";
  const parcial = real && fila.cobertura_envio_pct < 100;
  const est = fila.envio_estimado == null ? null : Number(fila.envio_estimado);
  const difiere = real && est != null && v != null && Math.abs(est - v) > 5;
  const cuentas = fila.envios ?? [];

  // El desglose vale aunque el número aún no exista: dice qué cuenta vendió y
  // cuántas piezas están esperando su cobro real.
  const panel = (
    <>
      <span className="block font-semibold text-white">Envío por cuenta</span>
      {cuentas.length === 0 && (
        <span className="mt-1 block text-slate-400">
          Sin ventas de Mercado Libre en el período: no hay embarque del que leer
          un cobro real.
        </span>
      )}
      {cuentas.map((c) => (
        <Renglon key={c.cuenta}
                 etiqueta={CUENTA_INI[c.cuenta] ?? c.cuenta}
                 detalle={c.envio_unit == null
                   ? `${fNum(c.uds)} uds · pendiente`
                   : c.cubiertas < c.uds
                     ? `${fNum(c.cubiertas)} de ${fNum(c.uds)} uds`
                     : `${fNum(c.uds)} uds`}
                 valor={c.envio_unit == null ? "—" : `${fMoney(c.envio_unit, 2)}/u`}
                 tenue={c.envio_unit == null} />
      ))}
      {est != null && (
        <Renglon etiqueta="Estimado por peso" valor={fMoney(est, 2)} tenue={real} />
      )}
      {/* Cuatro situaciones distintas, cada una con su frase: decir "todavía se
          muestra el estimado" cuando NO hay estimado es exactamente el tipo de
          texto que hace desconfiar de toda la tabla. */}
      <span className={`mt-1.5 block ${est == null && !real ? "text-amber-300" : "text-slate-400"}`}>
        {real
          ? "Se está usando el cobro REAL de Mercado Libre por cada embarque. "
            + "En un pedido con varios productos, el cobro se reparte entre las piezas."
          : est != null
            ? (cuentas.some((c) => c.uds > 0)
                ? "Todavía se muestra el estimado: los cobros reales de esos pedidos "
                  + "siguen consultándose y aparecen solos en el siguiente refresco."
                : "Estimado por peso y medidas. Se equivoca en las dos direcciones, "
                  + "pero es lo único que hay hasta que el producto venda.")
            : (cuentas.some((c) => c.uds > 0)
                ? "A este producto no se le capturó el estimado por peso y medidas: "
                  + "hasta que se consulten los cobros reales de esos pedidos, su costo "
                  + "final va SIN envío y el margen sale optimista."
                : "Sin envío por ningún lado: no tiene estimado capturado ni ventas en "
                  + "el período de las que leer un cobro real. Su costo final va sin envío.")}
      </span>
      {difiere && (
        <span className="mt-1 block text-amber-300">
          El estimado se equivocaba por {fMoney(Math.abs(est! - v!), 2)} por pieza.
        </span>
      )}
    </>
  );

  return (
    <PanelHover panel={panel}>
      {v == null ? (
        <div className="text-slate-300">—</div>
      ) : (
        <>
          <div className="tabular-nums text-slate-700">
            {fMoney(v, 2)}{parcial && <span className="text-amber-500">*</span>}
          </div>
          <div className={`text-[9px] font-bold uppercase tracking-wide ${
              real ? "text-emerald-600" : "text-slate-400"}`}>
            {real ? "real" : "est"}
          </div>
        </>
      )}
    </PanelHover>
  );
}

/* COSTO FINAL. Cuando la fila no tiene envío —ni el real del embarque ni el
   estimado por peso y medidas— el número se arma solo con costo base +
   comisión: es un costo final INCOMPLETO, y el margen que sale de él es
   optimista porque le falta restar el envío. Son 248 SKUs en producción, y sin
   marca se leían igual de firmes que los completos (Eduardo, 10-ago).

   La etiqueta nombra el costo que falta —"sin envío capturado"— y no un genérico
   "sin costo": la fila de al lado ES una columna de Costo base, y un lector que
   vea "sin costo capturado" ahí va a creer que le falta esa otra cosa. */
function CostoFinal({ fila }: { fila: Fila }) {
  const v = fila.costo_final == null ? null : Number(fila.costo_final);
  const costo = fila.costo == null ? null : Number(fila.costo);
  const com = fila.comision_unit == null ? null : Number(fila.comision_unit);
  const envio = fila.envio_unit == null ? null : Number(fila.envio_unit);
  if (v == null)
    return (
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Costo final</span>
          <span className="mt-1 block text-slate-400">
            {costo == null
              ? "Falta el costo base de este producto."
              : "Falta la comisión: sale de los pedidos reales del período, así que "
                + "no existe si el producto no vendió o si solo vende en Amazon."}
            {" "}Sin ella no hay costo final ni margen.
          </span>
        </>
      }>
        <div className="text-slate-300">—</div>
      </PanelHover>
    );
  const sinEnvio = envio == null;
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Costo final por pieza</span>
        <Renglon etiqueta="Costo base" valor={fMoney(costo, 2)} />
        <Renglon etiqueta="+ Comisión" valor={fMoney(com, 2)} />
        <Renglon etiqueta={`+ Envío${sinEnvio ? "" : fila.envio_origen === "real" ? " (real)" : " (estimado)"}`}
                 valor={sinEnvio ? "—" : fMoney(envio, 2)}
                 tenue={sinEnvio} />
        <span className="mt-1 block border-t border-slate-700 pt-1">
          <Renglon etiqueta="Costo final" valor={fMoney(v, 2)} />
        </span>
        {sinEnvio ? (
          <span className="mt-1.5 block text-amber-300">
            A este producto no se le capturó el costo de ENVÍO: no tiene el estimado
            por peso y medidas ni ha vendido un pedido del que leer el cobro real.
            Este total va sin envío, así que el margen sale optimista.
          </span>
        ) : (
          <span className="mt-1.5 block text-slate-400">
            No incluye el almacenamiento en FULL, que Mercado Libre cobra por mes y
            no por venta.
          </span>
        )}
      </>
    }>
      <div className={`font-semibold tabular-nums ${sinEnvio ? "text-amber-600" : "text-slate-800"}`}>
        {fMoney(v, 2)}
      </div>
      {sinEnvio && (
        <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">
          sin envío capturado
        </div>
      )}
    </PanelHover>
  );
}

/* MARGEN: el único que queda en la tabla — el que ya trae los cobros del canal
   descontados. Abajo, en pesos, lo que deja cada pieza: el porcentaje solo dice
   qué tan eficiente es la venta, no si vale la pena. */
function Margen({ fila }: { fila: Fila }) {
  const m = fila.margen_neto_pct == null ? null : Number(fila.margen_neto_pct);
  const costo = fila.costo == null ? null : Number(fila.costo);
  const precio = fila.precio_ref == null ? null : Number(fila.precio_ref);
  if (m == null || costo == null || precio == null)
    return (
      <div className="text-slate-300"
           title={fila.costo == null
             ? "Sin costo capturado para este SKU: no hay con qué calcular el margen"
             : "Sin comisión que leer en el período: sale de los pedidos reales, no de "
               + "una tasa estimada. Pasa cuando el producto no vendió, o cuando solo "
               + "vende en Amazon (comisión aún en cero, falta Finances API)."}>
        —
      </div>
    );
  // Costo poco creíble: el margen SÍ se pinta, pero en ámbar y con ⚠ (Eduardo,
  // 6-ago). Ocultarlo sacaba al SKU del análisis junto con la señal de que algo
  // pasa ahí; el ámbar dice "esto está en duda" sin fingir un veredicto.
  const dudoso = costoImplausible(precio, costo, fila.revisado_at);
  const final = fila.costo_final == null ? null : Number(fila.costo_final);
  const com = fila.comision_unit == null ? 0 : Number(fila.comision_unit);
  const envio = fila.envio_unit == null ? 0 : Number(fila.envio_unit);
  return (
    <div
      title={(dudoso ? avisoCostoImplausible(precio, costo) + "\n\n" : "")
             + `Costo final ${fMoney(final, 2)} = producto ${fMoney(costo, 2)}`
             + ` + comisión ${fMoney(com, 2)}`
             + (envio ? ` + envío ${fMoney(envio, 2)}`
                        + (fila.envio_origen === "real" ? " (real)" : " (estimado)")
                      : " (sin envío)")
             + `\nSobre el precio ${fila.uds > 0 ? "real de venta" : "publicado"}`
             + ` (${fMoney(precio, 2)})`}
    >
      <div className={`flex items-center justify-end gap-1 font-semibold tabular-nums ${
          dudoso ? "text-amber-600" : m < 20 ? "text-red-500" : "text-emerald-600"}`}>
        {dudoso && <AlertTriangle size={11} className="shrink-0" />}
        {fNum(m, 1)}%
      </div>
      <div className={`text-[10px] tabular-nums ${dudoso ? "text-amber-500" : "text-slate-400"}`}>
        {dudoso ? "costo dudoso"
          : fila.ganancia_unit == null ? "" : `${fMoney(fila.ganancia_unit, 2)}/u`}
      </div>
    </div>
  );
}

/* GANANCIA DEL PERÍODO: el margen en pesos y ya multiplicado por lo que se
   vendió. Es la columna que ordena por lo que sostiene el negocio — un 60%
   sobre tres piezas pesa menos que un 15% sobre trescientas. */
function Ganancia({ fila }: { fila: Fila }) {
  const g = fila.ganancia_periodo == null ? null : Number(fila.ganancia_periodo);
  const precio = fila.precio_ref == null ? null : Number(fila.precio_ref);
  if (g == null)
    return (
      <PanelHover panel={
        <>
          <span className="block font-semibold text-white">Ganancia del período</span>
          <span className="mt-1 block text-slate-400">
            {fila.uds > 0
              ? "Falta el costo base o la comisión, así que no hay costo final del "
                + "que restar: sin eso no se puede saber cuánto dejó."
              : "Este producto no vendió en el período. Sin piezas vendidas no hay "
                + "ganancia que contar — no es cero, es que no hay."}
          </span>
        </>
      }>
        <div className="text-slate-300">—</div>
      </PanelHover>
    );
  const dudoso = precio != null && costoImplausible(precio, fila.costo, fila.revisado_at);
  return (
    <PanelHover panel={
      <>
        <span className="block font-semibold text-white">Ganancia del período</span>
        <Renglon etiqueta="Precio real de venta" valor={fMoney(precio, 2)} />
        <Renglon etiqueta="− Costo final" valor={fMoney(fila.costo_final, 2)} />
        <span className="mt-1 block border-t border-slate-700 pt-1">
          <Renglon etiqueta="Deja por pieza" valor={fMoney(fila.ganancia_unit, 2)} />
        </span>
        <Renglon etiqueta={`× ${fNum(fila.uds)} piezas vendidas`} valor={fMoney(g)} />
        <span className="mt-1.5 block text-slate-400">
          Es la columna para ordenar cuando la pregunta es qué sostiene el negocio
          en pesos: un 60% sobre tres piezas pesa menos que un 15% sobre trescientas.
        </span>
        {dudoso && (
          <span className="mt-1 block text-amber-300">
            Sale de un costo poco creíble ({fNum(Number(fila.costo) / precio!, 1)}× el
            precio de venta): tómalo como referencia, no como un hecho.
          </span>
        )}
      </>
    }>
      <div className={`font-bold tabular-nums ${
          dudoso ? "text-amber-600" : g < 0 ? "text-red-500" : "text-emerald-600"}`}>
        {fMoney(g)}
      </div>
    </PanelHover>
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
  const [tam, setTam] = useState<string[]>([]);
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

  /* Carrera de respuestas. `cargar` no cancelaba nada: si el usuario cambia dos
     filtros seguidos salen dos peticiones y gana la que RESPONDA última, que no
     siempre es la última pedida — la tabla acaba mostrando un filtro que ya no
     está seleccionado. Con el filtro de una sola opción casi no se notaba;
     con casillas se marcan dos o tres seguidas y es fácil de provocar.
     Cada carga se queda con su número; si al volver ya no es la vigente, su
     resultado se tira. */
  const cargaVigente = useRef(0);

  const cargar = useCallback(async () => {
    const mia = ++cargaVigente.current;
    setCargando(true); setErr(null);
    try {
      const qd = new URLSearchParams({ dias: String(dias) });
      if (cuenta) qd.set("cuenta", cuenta);
      const qt = new URLSearchParams(qd);
      if (estado) qt.set("estado", estado);
      if (tipo) qt.set("tipo", tipo);
      if (tam.length) qt.set("tam", tam.join(","));
      if (busqueda) qt.set("q", busqueda);
      qt.set("orden", orden);
      qt.set("dir", dir);
      qt.set("limit", String(limit));
      qt.set("offset", String(pagina * limit));
      // `r.ok` ANTES de leer el cuerpo, como ya hacen `detalle` y `canales` en
      // este mismo archivo. Estas dos eran las únicas sin la guarda, y cuando la
      // API contestaba con error el cuerpo entraba igual al estado: un 400 o un
      // 502 acababan en "Cannot read properties of undefined" y la PÁGINA ENTERA
      // en blanco, sin decir qué pasó. Un error de red debe pintar el aviso, no
      // tumbar la vista.
      const leer = async (url: string) => {
        const r = await fetchSesion(url, { cache: "no-store" });
        if (!r.ok) throw new Error(`API ${r.status}`);
        return r.json();
      };
      const [d, t] = await Promise.all([
        leer(`${API_BASE}/api/fulfillment/dashboard?${qd}`),
        leer(`${API_BASE}/api/fulfillment/tabla?${qt}`),
      ]);
      if (mia !== cargaVigente.current) return;   // llegó tarde: ya hay otra
      setDash(d); setTabla(t);
    } catch (e) {
      if (mia !== cargaVigente.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      // El spinner solo lo apaga la carga vigente: si lo apagara una vieja, la
      // tabla se vería "lista" mientras la buena sigue en camino.
      if (mia === cargaVigente.current) setCargando(false);
    }
  }, [dias, cuenta, estado, tipo, tam, busqueda, orden, dir, limit, pagina]);

  /* Un respiro antes de consultar. La consulta de la tabla es CARA: medida en
     vivo tarda de 3.6 a 11.5 s, y empeora cuando varias se enciman porque se
     estorban entre ellas. Sin esto, marcar tres tamaños seguidos lanzaba tres
     consultas completas —y escribir "TEC-1284" en el buscador, ocho—, cuando
     solo interesa el resultado de la última.
     Cada cambio cancela el temporizador anterior, así que una ráfaga se colapsa
     en UNA consulta. 350 ms no se perciben al cambiar un filtro suelto. */
  useEffect(() => {
    const t = setTimeout(() => { void cargar(); }, 350);
    return () => clearTimeout(t);
  }, [cargar]);

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
          <Kpi label="Activos" value={fNum(dash?.kpis.activos)} tone="text-emerald-600"
               ayuda={AYUDA_ACTIVOS_KPI} />
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
          ] as [string, string, (v: string) => void, [string, string][]][]).map(([lbl, val, set, opts]) => (
            <label key={lbl} className="flex items-center gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{lbl}</span>
              <select value={val} onChange={(e) => { set(e.target.value); setPagina(0); }}
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-slate-700 shadow-sm">
                {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          ))}
          {/* Tamaño acepta VARIOS: filtrar por tamaño casi siempre es preguntar
              por un rango (lo chico que cabe en un sobre, lo grande que paga
              flete caro), y con una sola opción había que mirar la tabla dos
              veces para comparar S contra M. */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Tamaño</span>
            <FiltroMultiple
              etiqueta="Tamaño"
              valor={tam}
              onChange={(v) => { setTam(v); setPagina(0); }}
              opciones={[["S", "Chico"], ["M", "Mediano"], ["L", "Grande"],
                         ["XL", "Extra grande"], ["S/C", "Sin medidas"]]}
            />
          </div>
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
          {/* El envío real se consulta a Mercado Libre por tandas y se guarda:
              mientras queden piezas con el estimado se avisa, porque un margen
              con envío estimado y uno con el real son números distintos y el
              lector tiene derecho a saber cuál está viendo. La página se
              refresca sola cada 60 s y cada vuelta avanza otro tanto. */}
          {!!tabla?.envios_pendientes && (
            <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-600"
                  title="Cada pedido se consulta una sola vez y queda guardado; mientras tanto esas piezas usan el envío estimado.">
              <RefreshCw size={11} className="animate-spin" />
              consultando envíos — faltan {fNum(tabla.envios_pendientes)} piezas
            </span>
          )}
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

        {/* Tabla compacta: celdas de dos líneas para caber sin scroll horizontal.

            LA TABLA SE VE OCUPADA MIENTRAS CARGA (Eduardo, 13-ago: "coloco el
            filtro pero no pasa nada"). No estaba roto: la consulta tarda de 3.6
            a 11.5 s —medido instrumentando fetch— y durante esos segundos la
            tabla seguía mostrando el resultado ANTERIOR sin ninguna señal. El
            botón ya decía "L · XL" y las filas eran las de antes, así que se
            leía como que el filtro no servía.

            El ícono de Actualizar giraba, pero vive arriba, lejos de donde está
            mirando quien acaba de mover un filtro. Aquí se atenúa la tabla y se
            pone el aviso ENCIMA de ella. Se conserva la intención original de
            no parpadear con el auto-refresco de 60 s: la tabla vieja sigue
            legible debajo, no se vacía ni se reemplaza por un esqueleto. */}
        <div className="relative overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          {cargando && (
            <div className="pointer-events-none absolute inset-0 z-20 flex items-start justify-center bg-white/60 pt-16">
              <span className="flex items-center gap-2 rounded-full border border-indigo-200 bg-white px-3 py-1.5 text-xs font-semibold text-indigo-700 shadow-sm">
                <RefreshCw size={13} className="animate-spin" />
                Actualizando…
              </span>
            </div>
          )}
          <table className={`w-full text-[13px] transition-opacity ${cargando ? "opacity-40" : ""}`}>
            <thead>
              {/* El borde izquierdo transparente iguala el ancho de la franja
                  ámbar de los renglones: sin él, la cabecera quedaría corrida
                  4px respecto al cuerpo. */}
              <tr className="border-b border-l-4 border-slate-100 border-l-transparent bg-slate-50/70">
                {/* `info` = mostrar el "?" del encabezado. Solo lo llevan las
                    columnas SIN panel al pasar el cursor sobre la celda; donde
                    hay panel, el "?" repetía lo mismo (Eduardo, 8-ago). */}
                <Th id="sku" info="sku" {...th}>Producto</Th>
                <Th info="estado" {...th}>Estado</Th>
                {/* Las medidas van pegadas al producto y ANTES de las columnas
                    de dinero: describen el bulto, no el resultado del período.
                    Cada una ordena por su cuenta — es la forma de barrer el
                    catálogo por tamaño, que la tarjeta vieja no permitía. */}
                <Th id="tam" compacto info="tamano" {...th}>Tamaño</Th>
                <Th id="largo" right compacto info="medidas" {...th}>Largo</Th>
                <Th id="ancho" right compacto {...th}>Ancho</Th>
                <Th id="alto" right compacto {...th}>Alto</Th>
                <Th id="peso" right compacto info="peso" {...th}>Peso</Th>
                <Th right {...th}>Visitas · CR%</Th>
                <Th id="venta" right {...th}>Uds · $Venta</Th>
                <Th id="stock_full" right {...th}>FULL · Propio</Th>
                <Th id="edad" right {...th}>Edad s/v</Th>
                <Th right info="precio" {...th}>Precio venta</Th>
                <Th id="costo" right {...th}>Costo base</Th>
                <Th id="comision" right {...th}>Comisión /u</Th>
                <Th right {...th}>Envío /u</Th>
                <Th id="costo_final" right {...th}>Costo final</Th>
                <Th id="margen_neto" right info="margen_neto" {...th}>Margen</Th>
                <Th id="ganancia" right {...th}>Ganancia</Th>
              </tr>
            </thead>
            <tbody>
              {(tabla?.items ?? []).map((f) => (
                /* Franja ámbar a la izquierda cuando el renglón VENDIÓ sin costo
                   capturado: su margen, su costo final y su ganancia salen
                   vacíos, así que ordenar por cualquiera de esas columnas lo
                   esconde justo cuando más habría que verlo. La franja no
                   depende del orden ni del scroll horizontal. */
                <tr key={f.sku}
                    className={`border-b border-slate-50 align-middle hover:bg-slate-50/60 ${
                      sinCostoVendiendo(f)
                        ? "border-l-4 border-l-amber-400 bg-amber-50/40"
                        : "border-l-4 border-l-transparent"
                    }`}>
                  {/* Producto: SKU + dots + tam / título */}
                  <td className="max-w-[185px] px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[12px] font-semibold text-indigo-600">{f.sku}</span>
                      {/* Los puntos abren su tarjeta. Van en un contenedor
                          propio porque PanelHover ocupa todo el ancho de su
                          padre y sin esto se comerían el renglón. */}
                      <span className="shrink-0"><PuntosCuenta fila={f} /></span>
                      <MarcaDosProductos div={f.peso_divergente} />
                      <ChipRevision revisadoAt={f.revisado_at}
                                    revisadoPor={f.revisado_por}
                                    movida={f.revision_movida} />
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
                  <td className="whitespace-nowrap px-1 py-1.5">
                    <CeldaTamano fila={f} />
                  </td>
                  {/* MEDIDAS. `mayor` decide la categoría; se resalta para que
                      la clasificación se pueda comprobar de un vistazo. */}
                  {(() => {
                    const l = Number(f.largo ?? 0), an = Number(f.ancho ?? 0), al = Number(f.alto ?? 0);
                    const mayor = Math.max(l, an, al);
                    const dens = densidadRara(f);
                    return (
                      <>
                        <CeldaMedida v={f.largo} decide={mayor > 0 && l === mayor} />
                        <CeldaMedida v={f.ancho} decide={mayor > 0 && an === mayor} />
                        <CeldaMedida v={f.alto} decide={mayor > 0 && al === mayor} />
                        {f.peso == null ? (
                          <td className="px-1 py-1.5 text-right text-slate-300">—</td>
                        ) : (
                          <td className={`whitespace-nowrap px-1 py-1.5 text-right tabular-nums ${
                            dens ? "font-semibold text-amber-600" : "text-slate-500"}`}
                              title={dens
                                ? `${fNum(dens, 1)} kg por litro: casi seguro es el peso de la CAJA `
                                  + "capturado como si fuera el de una pieza. Con él, el envío estimado sale muy alto."
                                : undefined}>
                            {fNum(Number(f.peso), 3)}
                          </td>
                        )}
                      </>
                    );
                  })()}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <VisitasCR fila={f} dias={dias} />
                  </td>
                  {/* Uds + venta */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <VentaUds fila={f} dias={dias} />
                  </td>
                  {/* Stock full + propio */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <StockFullPropio fila={f} />
                  </td>
                  {/* Edad sin venta */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums">
                    <EdadSinVenta fila={f} dias={dias} />
                  </td>
                  {/* PRECIO DE VENTA por canal (solo publicaciones activas).
                      Clic → modal con el precio REALIZADO por canal. */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <button onClick={() => setCanalesDe(f)} title="Ver precio y margen por canal"
                            className="w-full rounded-md px-1 py-0.5 text-right transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <PrecioVenta fila={f} />
                    </button>
                  </td>
                  {/* EL BLOQUE DE COSTOS, en el mismo orden en que se suman:
                      costo base + comisión + envío = costo final. Leído de
                      izquierda a derecha, la fila explica sola de dónde sale el
                      margen — que es justo lo que antes obligaba a abrir el
                      popup de "Productos más vendidos". */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <CostoBase fila={f} dias={dias} />
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <ComisionUnit fila={f} />
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <EnvioUnit fila={f} />
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <CostoFinal fila={f} />
                  </td>
                  {/* MARGEN: el único que queda: ya con los cobros del canal
                      descontados. Abre el modal por canal, donde se ve cuánto se
                      lleva cada uno y el margen bruto para comparar. */}
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <button onClick={() => setCanalesDe(f)} title="Ver el desglose de cobros por canal"
                            className="w-full rounded-md px-1 py-0.5 text-right transition-all hover:bg-indigo-50 hover:ring-1 hover:ring-indigo-200">
                      <Margen fila={f} />
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    <Ganancia fila={f} />
                  </td>
                </tr>
              ))}
              {tabla && tabla.items.length === 0 && (
                <tr><td colSpan={18} className="px-3 py-10 text-center text-slate-400">Sin resultados con estos filtros.</td></tr>
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
          en tu bodega (se actualiza cada 20 minutos) · Costo final = costo base + comisión real +
          envío, y el margen sale de ahí — no incluye el almacenamiento en FULL, que Mercado Libre
          cobra por mes y no por venta.
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
