"use client";

// login — La puerta del panel.
//
// La animación de salida no es adorno: el panel tarda en cargar el catálogo, y
// sin una transición el usuario ve la pantalla congelada y vuelve a apretar
// "Entrar". La cortina cubre ese hueco y de paso confirma que el acceso fue
// correcto.

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  entrar,
  entrarConGoogle,
  haySesion,
  quienSoy,
  recogerSesionDeURL,
} from "@/lib/sesion";

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

  // Tres casos al abrir: venimos de Google, ya hay sesión, o hay que pedirla.
  useEffect(() => {
    // Google devuelve la sesión en el # de la URL. Va PRIMERO: en ese momento
    // todavía no hay sesión guardada, así que la revisión de abajo fallaría.
    const deGoogle = recogerSesionDeURL();
    if (deGoogle === "ok") {
      void pasar();
      return;
    }
    if (deGoogle) {
      setError(deGoogle);
      campoCorreo.current?.focus();
      return;
    }
    if (haySesion()) router.replace("/productos");
    else campoCorreo.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  /** Saluda por nombre y corre la cortina hasta el panel. */
  async function pasar() {
    const yo = await quienSoy();
    // Google ya dijo quién es; que le toque entrar lo decide el backend contra
    // core.usuarios. Si contesta que no, lo más probable es que su correo no
    // esté dado de alta — pero `quienSoy` devuelve lo MISMO si falló la red, y
    // no se puede distinguir desde aquí. Por eso el mensaje no afirma la causa:
    // decirle "no tienes acceso" a alguien que sí lo tiene manda a soporte a
    // buscar donde no es.
    if (!yo.autenticado) {
      setError(
        "No pudimos confirmar tu acceso al panel. Si vuelve a pasar, pídele " +
        "a tu administrador que dé de alta tu correo.",
      );
      setFase("formulario");
      return;
    }
    setSaludo(yo.usuario?.split("@")[0] || "");
    setFase("saliendo");
    window.setTimeout(() => router.replace("/productos"), 1150);
  }

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
    await pasar();
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

        {/* Google va ARRIBA y con más peso visual: es el camino que queremos
            que tomen. Con Workspace no teclean contraseña, y dar de baja a
            alguien se resuelve cerrando su cuenta de Google. */}
        <button
          type="button"
          className="login-google"
          onClick={entrarConGoogle}
          disabled={ocupado}
        >
          <svg viewBox="0 0 18 18" width="18" height="18" aria-hidden>
            <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
            <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
            <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z" />
            <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
          </svg>
          Continuar con Google
        </button>

        <div className="login-o"><span>o con tu correo</span></div>

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
