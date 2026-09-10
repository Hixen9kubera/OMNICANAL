/**
 * estadoVariante.ts — el punto de estado del rail de variantes.
 *
 * POR QUÉ EXISTE
 * --------------
 * El diseño pide un punto de 7 px por variante: publicada, pausada, sin stock o
 * falta. Suena a un `if`. No lo es: **cada canal escribe su estado en un campo
 * distinto y con un vocabulario distinto**, y el panel guarda DOS columnas que
 * no significan lo mismo (`status` y `situacion`).
 *
 * Medido en `channel.listings` el 10-sep-2026:
 *
 *   mercado_libre  manda `situacion`  active · paused · under_review · closed
 *                  2,915 published/paused contra 473 activas: lo NORMAL aquí
 *                  es estar pausado, y pintarlo verde sería mentir en el 86%.
 *   tiktok         manda `status`     ACTIVATE · DRAFT · SELLER_DEACTIVATED ·
 *                  PENDING · FAILED · DELETED. `situacion` sólo dice cómo salió
 *                  de la auditoría (APPROVED/NONE), no si se vende.
 *   amazon         `status` PUBLISHED|ACCEPTED|INVALID|DELETED cruzado con
 *                  `situacion` BUYABLE|DISCOVERABLE|closed.
 *   walmart        `status` PUBLISHED · UNPUBLISHED · SYSTEM_PROBLEM
 *   temu           `status` viene como par ("3/2"): sin descifrar. Ver abajo.
 *
 * EL QUINTO ESTADO
 * ----------------
 * El diseño trae cuatro. Falta uno, y no es un detalle de UI: **enviado, sin
 * confirmar**. Walmart y Amazon aceptan y confirman minutos después; ML deja
 * 215 publicaciones en `under_review`. Contarlas como éxito es afirmar algo que
 * todavía no se sabe — el mismo defecto que una paloma verde puesta al enviar.
 */

export type EstadoVariante =
  | "publicada"
  | "pausada"
  | "enviada"
  | "sin_stock"
  | "falta";

export interface PuntoEstado {
  estado: EstadoVariante;
  color: string;
  label: string;
  /** Frase para el `title` del punto: dice qué significa, no cómo se llama. */
  detalle: string;
}

/** Los cuatro del diseño + el quinto. `falta` es también el "no sé". */
export const COLOR_ESTADO: Record<EstadoVariante, string> = {
  publicada: "#10b981",
  pausada: "#f59e0b",
  // No está en el diseño. Índigo, de la familia del primario del panel
  // (#4F46E5), para que se lea como "en camino" y no se confunda ni con el
  // ámbar de pausada ni con el gris de falta.
  enviada: "#6366f1",
  sin_stock: "#e11d48",
  falta: "#cbd5e1",
};

export const LABEL_ESTADO: Record<EstadoVariante, string> = {
  publicada: "Publicada",
  pausada: "Pausada",
  enviada: "Enviada, sin confirmar",
  sin_stock: "Sin stock",
  falta: "Falta",
};

const DETALLE: Record<EstadoVariante, string> = {
  publicada: "Viva y comprable en el canal",
  pausada: "Existe en el canal pero no se vende",
  enviada: "Se mandó al canal y todavía no confirma que quedó publicada",
  sin_stock: "Publicada en 0 pzas — el canal la muestra agotada",
  falta: "Nunca se envió a este canal",
};

/** Presencia de un SKU en UN canal, tal como la manda `presencia_por_sku`. */
export interface PresenciaCanal {
  canal: string;
  publicado?: boolean;
  item_id?: string | null;
  situacion?: string | null;
  estado?: string | null;
  cuentas?: { cuenta: string; item_id?: string | null; situacion?: string | null; estado?: string | null }[];
}

function norm(v: string | null | undefined): string {
  return (v ?? "").trim().toLowerCase();
}

/**
 * Traduce el estado CRUDO de un canal a uno de los cinco.
 *
 * Devuelve `null` cuando el canal contesta algo que no reconocemos. Eso NO se
 * pinta como "falta": no saber y no existir son cosas distintas, y confundirlas
 * es justo lo que hizo que una tabla congelada respondiera "esta orden no
 * existe" 964 veces. El llamador decide, y hoy decide mostrar "publicada" si
 * hay id — porque un id es prueba de que la publicación existe.
 */
function traducir(canal: string, situacion: string, estado: string): EstadoVariante | null {
  if (situacion === "closed" || estado === "deleted") return "falta";

  switch (canal) {
    case "mercado_libre":
      if (situacion === "active") return "publicada";
      if (situacion === "paused" || situacion === "inactive") return "pausada";
      if (situacion === "under_review") return "enviada";
      return null;

    case "tiktok":
      // Manda `status`. `situacion` (APPROVED/NONE) es la auditoría, no la venta:
      // leerla en su lugar ya hizo que el fan-out descartara el canal entero.
      if (estado === "activate") return "publicada";
      if (estado === "draft" || estado === "seller_deactivated") return "pausada";
      if (estado === "pending") return "enviada";
      if (estado === "failed") return "falta";
      return null;

    case "amazon":
      if (estado === "invalid") return "falta";
      if (estado === "accepted") return "enviada"; // aceptado ≠ publicado
      if (estado === "published") {
        if (situacion === "buyable" || situacion === "discoverable") return "publicada";
        if (situacion === "closed") return "falta";
        return "publicada";
      }
      return null;

    case "walmart":
      if (estado === "published") return "publicada";
      if (estado === "unpublished") return "pausada";
      if (estado === "system_problem") return "enviada";
      return null;

    case "temu":
      // `status` llega como par ("3/2", "4/7"). El significado NO está
      // verificado contra Temu, así que no se inventa: si hay id, existe.
      return null;

    default:
      return null;
  }
}

/**
 * El punto de una variante en un canal.
 *
 * `stock` es el de la variante en Woo: una publicación viva en 0 piezas se ve
 * agotada en el canal, y ése es el aviso que el diseño quiere dar.
 */
export function puntoEstado(
  canal: string,
  presencia: PresenciaCanal | undefined,
  stock: number | null | undefined,
  /** `post_status` de la variación en Woo. Sólo lo usa el canal General. */
  estadoWoo?: string | null,
): PuntoEstado {
  const arma = (e: EstadoVariante): PuntoEstado => ({
    estado: e, color: COLOR_ESTADO[e], label: LABEL_ESTADO[e], detalle: DETALLE[e],
  });

  // GENERAL NO ES UN MARKETPLACE: es la ficha de WooCommerce. Ahí "falta
  // publicar" sería falso —el producto está en la tienda, por eso aparece en
  // la lista—, así que el punto lee el estado de Woo, no el de un canal.
  if (canal === "general") {
    const st = norm(estadoWoo);
    if ((stock ?? null) === 0) return arma("sin_stock");
    if (st === "publish") return arma("publicada");
    if (st === "draft" || st === "pending" || st === "ready") return arma("pausada");
    return arma("publicada");
  }

  if (!presencia || (!presencia.publicado && !presencia.item_id)) return arma("falta");

  const traducido = traducir(canal, norm(presencia.situacion), norm(presencia.estado));

  // Sin stock sólo aplica a lo que SÍ está vivo: una pausada en 0 es pausada,
  // y una que nunca se envió no está agotada — no existe.
  if ((traducido === "publicada" || traducido === null) && (stock ?? null) === 0) {
    return arma("sin_stock");
  }
  if (traducido) return arma(traducido);

  // No reconocemos el vocabulario del canal, pero hay un id: la publicación
  // existe. Se dice eso y se anota que el detalle fino no se pudo leer.
  return {
    ...arma("publicada"),
    detalle: `Existe en el canal (id ${presencia.item_id}); su estado exacto no se pudo interpretar`,
  };
}

/** Presencia de un SKU en un canal concreto, desde la lista que trae la fila. */
export function presenciaDe(
  canales: PresenciaCanal[] | undefined,
  canal: string,
): PresenciaCanal | undefined {
  return (canales ?? []).find((c) => c.canal === canal);
}
