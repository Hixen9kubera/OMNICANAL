"use client";

// login — La puerta del panel.
//
// La animación de salida no es adorno: el panel tarda en cargar el catálogo, y
// sin una transición el usuario ve la pantalla congelada y vuelve a apretar
// "Entrar". La cortina cubre ese hueco y de paso confirma que el acceso fue
// correcto.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { entrar, haySesion, quienSoy } from "@/lib/sesion";

type Fase = "formulario" | "verificando" | "saliendo";

export default function Login() {
  const router = useRouter();
  const [correo, setCorreo] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [verContrasena, setVerContrasena] = useState(false);
  const [fase, setFase] = useState<Fase>("formulario");
  const [error, setError] = useState<string | null>(null);
  const [saludo, setSaludo] = useState("");
  const campoCorreo = useRef<HTMLInputElement>(null);

  // Con sesión viva no tiene sentido pedir contraseña otra vez.
  useEffect(() => {
    if (haySesion()) router.replace("/productos");
    else campoCorreo.current?.focus();
  }, [router]);

  async function enviar(e: React.FormEvent) {
    e.preventDefault();
    if (fase !== "formulario") return;
    setError(null);
    setFase("verificando");

    const fallo = await entrar(correo, contrasena);
    if (fallo) {
      setError(fallo);
      setFase("formulario");
      return;
    }
    // Se saluda por nombre: confirma al usuario que entró con la cuenta correcta.
    const yo = await quienSoy();
    setSaludo(yo.usuario?.split("@")[0] || "");
    setFase("saliendo");
    window.setTimeout(() => router.replace("/productos"), 1150);
  }

  const ocupado = fase !== "formulario";

  return (
    <main className="login-fondo">
      <div className="login-halo" aria-hidden />

      <section className={`login-tarjeta ${fase === "saliendo" ? "login-se-va" : ""}`}>
        <header className="login-marca">
          <div className="login-logo" aria-hidden>K</div>
          <h1>OMNICANAL</h1>
          <p>Panel de Kubera</p>
        </header>

        <form onSubmit={enviar} noValidate>
          <label className="login-campo">
            <span>Correo</span>
            <input
              ref={campoCorreo}
              type="email"
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              placeholder="tu@kubera.mx"
              autoComplete="username"
              required
              disabled={ocupado}
            />
          </label>

          <label className="login-campo">
            <span>Contraseña</span>
            <div className="login-secreto">
              <input
                type={verContrasena ? "text" : "password"}
                value={contrasena}
                onChange={(e) => setContrasena(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                required
                disabled={ocupado}
              />
              <button
                type="button"
                onClick={() => setVerContrasena((v) => !v)}
                disabled={ocupado}
                aria-label={verContrasena ? "Ocultar contraseña" : "Mostrar contraseña"}
              >
                {verContrasena ? "Ocultar" : "Ver"}
              </button>
            </div>
          </label>

          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="login-entrar" disabled={ocupado}>
            {fase === "verificando" ? (
              <>
                <span className="login-girito" aria-hidden />
                Verificando…
              </>
            ) : (
              "Entrar"
            )}
          </button>
        </form>

        <footer className="login-pie">
          ¿No tienes acceso? Pídeselo a tu administrador.
        </footer>
      </section>

      {/* Cortina de transición: cubre el tiempo que el panel tarda en cargar. */}
      <div className={`login-cortina ${fase === "saliendo" ? "login-cortina-va" : ""}`}>
        <div className="login-palomita" aria-hidden>
          <svg viewBox="0 0 52 52" width="52" height="52">
            <circle className="login-palomita-aro" cx="26" cy="26" r="23" />
            <path className="login-palomita-tache" d="M15 27 l8 8 l15 -16" />
          </svg>
        </div>
        <p>{saludo ? `Hola, ${saludo}` : "Listo"}</p>
        <span>Abriendo tu panel…</span>
      </div>
    </main>
  );
}
