/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Las imágenes de producto vienen de dominios externos (WooCommerce, ML, Amazon).
  // Usamos <img> normal en los componentes, así que desactivamos la optimización
  // para evitar configurar cada dominio. Si más adelante se quiere next/image,
  // añadir aquí los remotePatterns.
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
