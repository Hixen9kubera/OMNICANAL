import type { Config } from "tailwindcss";

/**
 * Misma base que `frontend/tailwind.config.ts` (sombras, animaciones y la fuente
 * declarada), para que el laboratorio se sienta parte del panel. Se agregan los
 * colores de marca como tokens con nombre para no regar hex por el código.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        marca: { DEFAULT: "#4F46E5", acento: "#818CF8", suave: "#EEF0FF" },
        fondo: "#f6f7fb",
        tinta: "#1f2430",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 3px rgba(16,24,40,0.06), 0 1px 2px rgba(16,24,40,0.04)",
        "card-hover": "0 12px 24px -8px rgba(16,24,40,0.18)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out",
        "slide-in": "slide-in 0.28s cubic-bezier(0.22,1,0.36,1)",
      },
    },
  },
  plugins: [],
};

export default config;
