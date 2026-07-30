"use client";

/**
 * /analisis/fba — Amazon FBA: capacidad y plan de envío.
 *
 * Porta routers/amazon_fba.py del fulfillment de José (ver
 * docs/fulfillment/prompts_originales_jose.txt, prompt 1): tiers por peso,
 * parseo de CBM, capacidad contratada y plan de envío.
 *
 * Es la sección con más piezas faltantes de nuestro lado: Amazon hoy no guarda
 * ASIN (0 de 1,666 filas) ni precio en el 63%, y `stock_real` (FBM) viene
 * vacío. La capacidad contratada no vive en ninguna tabla todavía.
 */

import { BarChart3 } from "lucide-react";
import FulfillmentPendiente from "@/components/FulfillmentPendiente";

export default function FbaPage() {
  return (
    <FulfillmentPendiente
      p={{
        titulo: "Amazon FBA · capacidad y plan de envío",
        icono: BarChart3,
        resumen:
          "Cuánto cabe en la bodega de Amazon y qué conviene mandar: ocupación " +
          "contra capacidad contratada y plan de envío por SKU con tiers de peso y CBM.",
        contenido: [
          "Ocupación actual vs capacidad contratada (volumen y unidades)",
          "Tier por peso de cada SKU y su CBM, derivados de las dimensiones",
          "Plan de envío: qué mandar, cuántas piezas y cuánto volumen ocupa",
          "Contraste FBA vs bodega propia para decidir qué se surte desde dónde",
        ],
        fuentes: [
          {
            nombre: "costing.costos_validados",
            detalle: "largo / alto / ancho / peso → CBM y tier",
            listo: true,
          },
          {
            nombre: "channel.listings (canal amazon)",
            detalle: "stock FBA por SKU — 1,590 piezas hoy",
            listo: true,
          },
          {
            nombre: "ASIN de Amazon",
            detalle: "0 de 1,666 filas lo tienen; amazon_progress tampoco lo guarda",
            listo: false,
          },
          {
            nombre: "Capacidad contratada en FBA",
            detalle: "no existe en ninguna tabla; hay que capturarla o leerla de SP-API",
            listo: false,
          },
        ],
        bloqueos: [
          "Capturar el ASIN al publicar en Amazon (o recuperarlo con SP-API) — sin él no se puede enlazar la publicación con su inventario FBA",
          "Definir de dónde sale la capacidad contratada: captura manual en pricing_params o la API de Amazon (FBA Inventory / Storage)",
          "Decidir la tabla de tiers por peso (la de José estaba en su código, no en base de datos)",
        ],
      }}
    />
  );
}
