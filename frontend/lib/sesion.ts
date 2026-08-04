// sesion.ts — Sesión del panel contra Supabase Auth.
//
// POR QUÉ ASÍ
// -----------
// La contraseña la guarda Supabase, nunca nosotros — igual que WordPress con
// los usuarios de WooCommerce. La tabla `core.usuarios` de la BD kubera solo
// guarda el PERFIL y el ROL, atada al mismo id por una llave foránea con
// ON DELETE CASCADE: al borrar el usuario, sus permisos se van con él.
//
// El token vive en localStorage. Cada llamada a la API lo manda en
// `Authorization: Bearer`, y el backend lo verifica contra Supabase y resuelve
// el rol desde core.usuarios (ver backend/core/identidad.py).

const URL_SUPABASE = (process.env.NEXT_PUBLIC_SUPABASE_URL || "").replace(/\/$/, "");
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

const LLAVE_TOKEN = "omnicanal.token";
const LLAVE_REFRESH = "omnicanal.refresh";

export interface Usuario {
  autenticado: boolean;
  usuario: string | null;
  rol: "admin" | "operador" | "lectura" | null;
  tipo?: string;
  puede?: Record<string, boolean>;
}

/** El token de la sesión actual, o "" si no hay. */
export function token(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(LLAVE_TOKEN) || "";
}

export function haySesion(): boolean {
  return token().length > 0;
}

function guardar(acceso: string, refresco: string): void {
  window.localStorage.setItem(LLAVE_TOKEN, acceso);
  if (refresco) window.localStorage.setItem(LLAVE_REFRESH, refresco);
}

export function cerrarSesion(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LLAVE_TOKEN);
  window.localStorage.removeItem(LLAVE_REFRESH);
}

/**
 * Entra con correo y contraseña.
 * Devuelve un mensaje de error legible, o null si todo salió bien.
 */
export async function entrar(correo: string, contrasena: string): Promise<string | null> {
  if (!URL_SUPABASE || !ANON) {
    return "El panel no tiene configurado el servicio de sesión. Avísale a soporte.";
  }
  let res: Response;
  try {
    res = await fetch(`${URL_SUPABASE}/auth/v1/token?grant_type=password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", apikey: ANON },
      body: JSON.stringify({ email: correo.trim(), password: contrasena }),
    });
  } catch {
    return "No se pudo conectar. Revisa tu conexión a internet.";
  }
  if (!res.ok) {
    // Se devuelve SIEMPRE el mismo mensaje para correo inexistente y contraseña
    // equivocada: decir cuál de los dos falló le confirmaría a un atacante qué
    // correos existen.
    if (res.status === 400 || res.status === 401) {
      return "Correo o contraseña incorrectos.";
    }
    return "No se pudo iniciar sesión. Inténtalo de nuevo en un momento.";
  }
  const d = await res.json();
  if (!d?.access_token) return "La respuesta del servidor no trae sesión.";
  guardar(d.access_token, d.refresh_token || "");
  return null;
}

/** Quién soy, según el backend (no según el token). */
export async function quienSoy(): Promise<Usuario> {
  const base =
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";
  const t = token();
  const anon: Usuario = { autenticado: false, usuario: null, rol: null };
  if (!t) return anon;
  try {
    const res = await fetch(`${base}/api/auth/me`, {
      headers: { Authorization: `Bearer ${t}` },
      cache: "no-store",
    });
    if (!res.ok) return anon;
    return (await res.json()) as Usuario;
  } catch {
    return anon;
  }
}
