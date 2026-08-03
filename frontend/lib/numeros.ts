/**
 * Lectura tolerante de números escritos por una persona.
 *
 * El parseo anterior era `v.trim() ? Number(v) || null : null`, y eso descarta
 * EN SILENCIO lo que cualquiera escribe de forma natural en español:
 *   "1,625.84" → NaN → null    (separador de millar)
 *   "$1,842.86" → NaN → null   (símbolo de moneda)
 *   "1 625.84" → NaN → null    (espacio)
 * El campo se enviaba vacío, el backend recalculaba con el costo viejo y la
 * pantalla decía "guardado" sin haber guardado el número tecleado. Caso real:
 * los costos de los colchones CAM-0030, dos intentos perdidos así.
 *
 * De paso arregla el `0`: `Number("0") || null` daba null, o sea que un cero
 * explícito era indistinguible de un campo vacío.
 */
export function aNumero(v: string | null | undefined): number | null {
  const s = (v ?? "").trim();
  if (!s) return null;
  // fuera moneda, espacios (incluido el no-rompible) y el signo de porcentaje
  let t = s.replace(/[\s $%]/g, "");
  if (t.includes(",") && t.includes(".")) {
    // conviven los dos: manda el ÚLTIMO como separador decimal
    t =
      t.lastIndexOf(",") > t.lastIndexOf(".")
        ? t.replace(/\./g, "").replace(",", ".")
        : t.replace(/,/g, "");
  } else if (t.includes(",")) {
    // "1,625" es millar; "1,5" es decimal — decide por los dígitos tras la coma
    const dec = t.slice(t.lastIndexOf(",") + 1);
    t = dec.length === 3 ? t.replace(/,/g, "") : t.replace(",", ".");
  }
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}
