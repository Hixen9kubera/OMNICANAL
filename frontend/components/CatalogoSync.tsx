"use client";

import { useEffect } from "react";
import { refrescarCatalogo } from "@/lib/api";

/**
 * Al abrir la app (una vez por carga), fuerza el refresco del índice de
 * WooCommerce (catálogo + drafts) leyendo en vivo de la DB de WordPress, para
 * que los drafts nuevos aparezcan al instante sin esperar el TTL. No bloquea la
 * UI: el backend refresca en segundo plano.
 */
export default function CatalogoSync() {
  useEffect(() => {
    refrescarCatalogo().catch(() => {});
  }, []);
  return null;
}
