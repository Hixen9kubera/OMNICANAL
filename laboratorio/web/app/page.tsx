"use client";

/**
 * La raíz manda a Publicaciones. Es un componente de cliente a propósito: en
 * export estático `redirect()` del servidor no existe, y así el salto respeta
 * el `trailingSlash` del export.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Inicio() {
  const router = useRouter();
  useEffect(() => { router.replace("/publicaciones"); }, [router]);
  return (
    <main className="grid min-h-screen place-items-center text-sm text-slate-400">
      <a href="/publicaciones" className="text-indigo-600 underline underline-offset-2">Ir al laboratorio</a>
    </main>
  );
}
