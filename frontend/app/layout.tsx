import type { Metadata } from "next";
import "./globals.css";
import CatalogoSync from "@/components/CatalogoSync";
import SesionGuard from "@/components/SesionGuard";

export const metadata: Metadata = {
  title: "OMNICANAL · Kubera",
  description:
    "Panel omnicanal: visualiza tus publicaciones de WooCommerce y su estado en cada marketplace (Mercado Libre, Amazon, TikTok, Walmart, Temu, Shein).",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body>
        {/* Envuelve TODAS las páginas: así no hay que acordarse de proteger
            cada una. Mientras la autenticación esté en observación no bloquea
            a nadie — lo decide el backend, no el frontend. */}
        <SesionGuard>
          <CatalogoSync />
          {children}
        </SesionGuard>
      </body>
    </html>
  );
}
