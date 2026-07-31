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
            nombre: "Medidas y peso por producto",
            detalle: "para calcular el volumen y la tarifa de cada envío",
            listo: true,
          },
          {
            nombre: "Inventario FBA por producto",
            detalle: "1,590 piezas en la bodega de Amazon hoy",
            listo: true,
          },
          {
            nombre: "ASIN de Amazon",
            detalle: "el código con el que Amazon identifica cada publicación: hoy no se guarda en ninguna",
            listo: false,
          },
          {
            nombre: "Capacidad contratada en FBA",
            detalle: "cuánto espacio tenemos contratado con Amazon: hoy no está registrado en el sistema",
            listo: false,
          },
        ],
        bloqueos: [
          "Empezar a guardar el ASIN al publicar en Amazon (o recuperarlo de su API) — sin él no se puede enlazar cada publicación con su inventario FBA",
          "Definir de dónde sale la capacidad contratada: capturarla a mano o leerla de la API de Amazon",
          "Registrar la tabla de tarifas por peso de Amazon (hoy no está en el sistema)",
        ],
      }}
    />
  );
}
