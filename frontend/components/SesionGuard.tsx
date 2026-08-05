"use client";

// SesionGuard — Deja pasar solo a quien tiene sesión.
//
// Va en el layout raíz, así que cubre TODAS las páginas de un golpe en vez de
// tener que acordarse de protegerlas una por una.
//
// MIENTRAS LA AUTENTICACIÓN ESTÉ EN OBSERVACIÓN NO BLOQUEA. El backend arranca
// con AUTH_ENFORCED=false y responde a todos; si el guardia bloqueara antes,
// dejaría fuera al equipo sin que hubiera necesidad. Se apoya en lo que dice el
// propio backend (`auth_activa` de /api/auth/me), no en una suposición del
// frontend: así el panel y la API no pueden quedar desincronizados.

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { cuidarSesion, haySesion, quienSoy } from "@/lib/sesion";

// Rutas que se ven sin sesión (si no, el login se bloquearía a sí mismo).
const ABIERTAS = ["/login"];

export default function SesionGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const ruta = usePathname();
  const [listo, setListo] = useState(false);

  useEffect(() => {
    let vivo = true;

    async function revisar() {
      if (ABIERTAS.some((r) => ruta?.startsWith(r))) {
        if (vivo) setListo(true);
        return;
      }
      // Con sesión, adelante.
      if (haySesion()) {
        if (vivo) setListo(true);
        return;
      }
      // Sin sesión: se le pregunta al BACKEND si ya está exigiendo credencial.
      const yo = await quienSoy();
      if (!vivo) return;
      const exige = (yo as { auth_activa?: boolean }).auth_activa === true;
      if (exige) router.replace("/login");
      else setListo(true);   // modo observación: no se bloquea a nadie
    }

    void revisar();
    return () => {
      vivo = false;
    };
  }, [ruta, router]);

  // Mantenimiento de la sesión: renueva el token antes de que caduque (dura 1
  // hora) y lo recupera al volver de una pestaña dormida. Va aparte del efecto
  // de arriba a propósito: ese depende de la ruta y se re-ejecuta al navegar,
  // y reiniciar el temporizador en cada clic dejaría la renovación sin correr
  // nunca en una sesión de trabajo normal.
  useEffect(() => cuidarSesion(), []);

  if (!listo) {
    return (
      <div className="sesion-cargando">
        <span className="sesion-girito" aria-hidden />
      </div>
    );
  }
  return <>{children}</>;
}
