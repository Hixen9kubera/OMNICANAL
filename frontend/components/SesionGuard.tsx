"use client";

// SesionGuard — Deja pasar solo a quien tiene sesión.
//
// Va en el layout raíz, así que cubre TODAS las páginas de un golpe en vez de
// tener que acordarse de protegerlas una por una.
//
// SIN SESIÓN NO SE ENTRA A NINGUNA PARTE (Brandon, 5-ago, con el enforcement
// ya encendido). Antes esto era permisivo: no bloqueaba mientras la API
// estuviera en observación. Esa versión tenía un agujero — `quienSoy()` devolvía
// "no sé" ante un 401, el guardia lo leía como "enforcement apagado" y dejaba
// pasar, justo cuando la API empezó a exigir credencial. El panel se veía
// abierto aunque los datos ya no cargaran.
//
// Ahora el orden es al revés: se entra SOLO con sesión comprobada.
//   sin token           → /login
//   token que no vale   → se intenta renovar; si no, se cierra y → /login
//   token bueno         → adelante
//
// Bloquear es seguro aunque el backend esté caído: la pantalla de login habla
// DIRECTO con Supabase, así que se puede entrar aunque nuestra API no responda.

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { cerrarSesion, cuidarSesion, haySesion, quienSoy, refrescar } from "@/lib/sesion";

// Rutas que se ven sin sesión (si no, el login se bloquearía a sí mismo).
const ABIERTAS = ["/login"];

export default function SesionGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const ruta = usePathname();
  const [listo, setListo] = useState(false);

  useEffect(() => {
    let vivo = true;

    async function alLogin() {
      cerrarSesion();
      router.replace("/login");
    }

    async function revisar() {
      if (ABIERTAS.some((r) => ruta?.startsWith(r))) {
        if (vivo) setListo(true);
        return;
      }
      // Sin token ni se pregunta: a la puerta.
      if (!haySesion()) {
        if (vivo) void alLogin();
        return;
      }
      // Con token, se COMPRUEBA contra el backend. Tener un token guardado no
      // prueba nada: pudo caducar, o revocarse al dar de baja a la persona.
      let yo = await quienSoy();
      if (!vivo) return;
      // Un token vencido se renueva y se reintenta UNA vez — el caso normal de
      // volver al día siguiente sin haber cerrado la pestaña.
      if (!yo.autenticado && (await refrescar())) {
        yo = await quienSoy();
      }
      if (!vivo) return;
      if (yo.autenticado) {
        setListo(true);
        return;
      }
      // El backend NO reconoce esta sesión (401/403), o la persona ya no está
      // dada de alta. Si en cambio fue un 5xx o falta de red, `auth_activa` no
      // viene y no se le echa de un panel que quizá sí puede usar.
      if (yo.auth_activa) void alLogin();
      else setListo(true);
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
