"use client";

// Acceso.tsx — Guard DE PÁGINA del radar de precios.
//
// El radar está oculto: solo admin (Brandon, José y Eduardo). La autoridad es
// el backend —cada ruta lleva `Depends(solo_admin)` y una regla explícita en
// core/rbac.py—; esto es la segunda capa, para que a quien no le toca no vea ni
// el cascarón de la pantalla. Mientras no se confirme el rol NO se pide ningún
// dato del radar: los hijos ni se montan.
//
//   NEXT_PUBLIC_AUTH_OFF=true (solo el sandbox local) → se muestra.
//   rol admin confirmado por /api/auth/me              → se muestra.
//   cualquier otra cosa (sin sesión, KAM, lectura, API caída) → "No disponible".
//
// Next.js sustituye NEXT_PUBLIC_* al compilar: en el build de producción la
// variable no existe y el atajo queda como `false` literal.

import { useEffect, useState } from "react";

import { quienSoy } from "@/lib/sesion";
import { Cargando, NoDisponible } from "./ui";

const SIN_LOGIN = process.env.NEXT_PUBLIC_AUTH_OFF === "true";

export default function AccesoRadar({ children }: { children: React.ReactNode }) {
  const [estado, setEstado] = useState<"revisando" | "admin" | "no">(
    SIN_LOGIN ? "admin" : "revisando",
  );

  useEffect(() => {
    if (SIN_LOGIN) return;
    let vivo = true;
    void quienSoy().then((u) => {
      if (!vivo) return;
      setEstado(u.autenticado && u.rol === "admin" ? "admin" : "no");
    });
    return () => {
      vivo = false;
    };
  }, []);

  // Sin rótulo del radar mientras se revisa: no se anuncia lo que quizá no toca.
  if (estado === "revisando") return <Cargando texto="Comprobando acceso…" />;
  if (estado === "no") return <NoDisponible />;
  return <>{children}</>;
}
