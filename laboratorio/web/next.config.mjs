/**
 * Laboratorio de precios · configuración de Next.
 *
 * Dos modos, y la diferencia importa:
 *
 *   LAB_EXPORT=1  → export ESTÁTICO a `out/`. Es lo que sirve `sandbox_precios/api.py`
 *                   junto a `/api/lab/*`, en el mismo origen: por eso la cookie
 *                   `lab_sesion` (httpOnly) viaja sola y no hay CORS que configurar.
 *   sin LAB_EXPORT → desarrollo: `/api/lab/*` se reenvía al api.py local (puerto
 *                   8010). NUNCA al backend de producción: el laboratorio no sabe
 *                   hablar con él y no debe.
 *
 * Las fixtures (datos de prueba en `fixtures/`) sólo existen en `next dev` con
 * NEXT_PUBLIC_LAB_FIXTURES=1. En cualquier otro caso el módulo que las carga se
 * sustituye por uno vacío al empaquetar, así que un build de producción no las
 * trae ni aunque alguien deje la variable puesta por error.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants.js";

const aqui = path.dirname(fileURLToPath(import.meta.url));

export default function configurar(fase) {
  const exportar = process.env.LAB_EXPORT === "1";
  const fixtures = fase === PHASE_DEVELOPMENT_SERVER && !exportar
    && process.env.NEXT_PUBLIC_LAB_FIXTURES === "1";

  /** @type {import('next').NextConfig} */
  const base = {
    reactStrictMode: true,
    // Se inyecta en el cliente: con export estático las rutas llevan "/" final
    // (out/publicaciones/index.html) y los enlaces duros deben pedirla así.
    env: {
      NEXT_PUBLIC_LAB_EXPORT: exportar ? "1" : "0",
      NEXT_PUBLIC_LAB_FIXTURES_ACTIVAS: fixtures ? "1" : "0",
    },
    webpack(config, { webpack }) {
      if (!fixtures) {
        // El cargador de fixtures se cambia por uno que no importa nada: así ningún
        // JSON de prueba entra al bundle que se publica.
        config.plugins.push(
          new webpack.NormalModuleReplacementPlugin(/[\\/]datos-prueba$/, (recurso) => {
            recurso.request = path.join(aqui, "lib", "datos-prueba-vacio.ts");
          }),
        );
      }
      return config;
    },
  };

  if (exportar) {
    return { ...base, output: "export", trailingSlash: true, images: { unoptimized: true } };
  }
  return {
    ...base,
    images: { unoptimized: true },
    async rewrites() {
      return [{ source: "/api/lab/:path*", destination: "http://127.0.0.1:8010/api/lab/:path*" }];
    },
  };
}
