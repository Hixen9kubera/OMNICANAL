/**
 * Enlace público de una publicación, con respaldo cuando no viene guardado.
 *
 * `channel.listings.url` sólo está lleno en las publicaciones que salieron por
 * el publicador viejo, que lo guardaba al publicar. Las que el sync fue
 * descubriendo después lo tienen vacío: el 26-ago-2026 eran 222 de las 774
 * publicaciones ACTIVAS de Mercado Libre, y Amazon, Temu y Walmart enteros.
 * El panel escondía el botón "Ver publicación" en todas ellas, y se leía como
 * si la publicación no existiera.
 *
 * Pero el valor guardado no aporta nada que no esté ya en el id — lo que hay en
 * la base es `https://articulo.mercadolibre.com.mx/MLM-3167708699`, o sea el id
 * con un guion —, así que cuando falta lo armamos en vez de esconder el botón.
 *
 * Y el id MANDA sobre el url guardado, no al revés. Al comparar los 3,511
 * enlaces guardados de ML contra el id de su propia fila, 496 apuntaban a OTRA
 * publicación (p. ej. la fila `MLM2648520635` guardaba un url a
 * `MLM-5540995904`). Se explica solo: el sync refresca `listing_id` cada 15
 * minutos desde la API de ML, mientras que `url` se escribió una única vez al
 * publicar y nadie lo volvió a tocar — cuando una publicación se elimina y se
 * re-crea, el id cambia y el url queda apuntando a la muerta. Por eso el url
 * guardado es el ÚLTIMO recurso: sólo se usa en los canales cuyo formato no
 * sabemos construir.
 *
 * Los formatos salieron de los enlaces que YA están guardados, no de adivinar:
 * en Mercado Libre reproducen 3,015/3,511 exacto (las 496 restantes son las
 * stale de arriba) y en TikTok 902/902. Por eso Temu y Walmart se quedan fuera:
 * no hay ni una fila con url de la cual sacar el formato, y un enlace inventado
 * que lleva a un 404 es peor que no tener botón. Cuando aparezca el primero, se
 * agrega aquí.
 */
export function enlacePublicacion(
  canal: string | null | undefined,
  itemId: string | null | undefined,
  url?: string | null,
): string | null {
  const id = (itemId ?? "").trim();

  switch (canal) {
    case "mercado_libre":
      // El sitio va pegado y el número separado: MLM3167708699 → MLM-3167708699.
      // Si el id no tiene esa forma no arriesgamos un enlace roto.
      return /^[A-Z]{3}\d+$/.test(id)
        ? `https://articulo.mercadolibre.com.mx/${id.slice(0, 3)}-${id.slice(3)}`
        : url || null;
    case "amazon":
      // El listing_id de Amazon ES el ASIN (B0H1DYSK71).
      return /^[A-Z0-9]{10}$/.test(id) ? `https://www.amazon.com.mx/dp/${id}` : url || null;
    case "tiktok":
      return /^\d+$/.test(id) ? `https://shop.tiktok.com/view/product/${id}` : url || null;
    default:
      // Canal cuyo formato no conocemos (Temu, Walmart): si el publicador viejo
      // dejó un url, es lo único que hay.
      return url || null;
  }
}
