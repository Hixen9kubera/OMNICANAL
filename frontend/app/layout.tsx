import type { Metadata } from "next";
import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}
