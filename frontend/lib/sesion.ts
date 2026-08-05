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
const LLAVE_EXPIRA = "omnicanal.expira";   // epoch en ms

// Cuánto ANTES de que caduque se renueva. El token de Supabase dura 1 hora;
// renovar con 5 minutos de sobra evita la carrera de que caduque justo entre
// que se arma la petición y el backend la valida.
const MARGEN_MS = 5 * 60 * 1000;

export interface Usuario {
  autenticado: boolean;
  usuario: string | null;
  rol: "admin" | "operador" | "lectura" | null;
  tipo?: string;
  puede?: Record<string, boolean>;
  /** ¿La API ya está exigiendo credencial? Lo dice el backend, no el panel. */
  auth_activa?: boolean;
}

/** El token de la sesión actual, o "" si no hay. */
export function token(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(LLAVE_TOKEN) || "";
}

export function haySesion(): boolean {
  return token().length > 0;
}

function guardar(acceso: string, refresco: string, duraSeg?: number): void {
  window.localStorage.setItem(LLAVE_TOKEN, acceso);
  if (refresco) window.localStorage.setItem(LLAVE_REFRESH, refresco);
  const dura = (duraSeg && duraSeg > 0 ? duraSeg : 3600) * 1000;
  window.localStorage.setItem(LLAVE_EXPIRA, String(Date.now() + dura));
}

export function cerrarSesion(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LLAVE_TOKEN);
  window.localStorage.removeItem(LLAVE_REFRESH);
  window.localStorage.removeItem(LLAVE_EXPIRA);
  if (temporizador !== null) {
    window.clearTimeout(temporizador);
    temporizador = null;
  }
}

/** Milisegundos que le quedan de vida al token (0 si ya venció o no hay). */
export function vidaRestante(): number {
  if (typeof window === "undefined") return 0;
  const t = Number(window.localStorage.getItem(LLAVE_EXPIRA) || 0);
  if (!t) return 0;
  return Math.max(0, t - Date.now());
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
  guardar(d.access_token, d.refresh_token || "", d.expires_in);
  programarRefresco();
  return null;
}

// ── Entrar con Google ───────────────────────────────────────────────────────
//
// POR QUÉ ES MEJOR QUE LA CONTRASEÑA
// El correo de Kubera está en Google Workspace (MX → smtp.google.com), así que
// cada dirección @kubera.mx YA es una cuenta de Google. Nadie teclea una
// contraseña en nuestro panel, no hay claves que repartir, y dar de baja a
// alguien se vuelve un solo movimiento: se cierra su cuenta de Workspace.
//
// QUIÉN DECIDE QUÉ
//   Google  → prueba QUIÉN eres.
//   nosotros→ si además te toca entrar, y con qué rol (core.usuarios).
// Un correo del dominio que no esté dado de alta se rechaza en el backend
// (`core/identidad.py`), no aquí: el frontend no es autoridad de nada.

/** Manda al usuario a Google. No regresa: la página se reemplaza. */
export function entrarConGoogle(): void {
  if (typeof window === "undefined") return;
  const destino = `${window.location.origin}/login`;
  window.location.href =
    `${URL_SUPABASE}/auth/v1/authorize?provider=google` +
    `&redirect_to=${encodeURIComponent(destino)}`;
}

/**
 * Al volver de Google, Supabase pega la sesión en el **fragmento** de la URL
 * (`#access_token=…`). Se recoge, se guarda y se BORRA de la barra.
 *
 * Borrarlo no es cosmética: el fragmento queda en el historial del navegador y
 * se lo lleva cualquier captura de pantalla. Un token en el historial es una
 * sesión prestada a quien tome la laptop.
 *
 * Devuelve "ok" si entró, el mensaje de error si Google lo rechazó, o null si
 * esta carga no viene de Google.
 */
export function recogerSesionDeURL(): "ok" | string | null {
  if (typeof window === "undefined") return null;
  const bruto = window.location.hash;
  if (!bruto || bruto.length < 2) return null;
  const p = new URLSearchParams(bruto.slice(1));

  const limpiar = () =>
    window.history.replaceState({}, "", window.location.pathname);

  if (p.get("error")) {
    limpiar();
    // `access_denied` = la persona canceló en la pantalla de Google.
    if (p.get("error") === "access_denied") return "Cancelaste el acceso con Google.";
    return p.get("error_description") || "Google rechazó el acceso.";
  }

  const acceso = p.get("access_token") || "";
  if (!acceso) return null;
  guardar(acceso, p.get("refresh_token") || "", Number(p.get("expires_in") || 0));
  limpiar();
  programarRefresco();
  return "ok";
}

// ── Renovación del token ────────────────────────────────────────────────────
//
// EL TOKEN DE SUPABASE DURA 1 HORA. Sin esto, quien entrara a las 9:00 vería el
// panel dejar de responder a las 10:00 sin explicación: guardábamos el token de
// refresco desde el día uno pero nunca lo usábamos.
//
// Hay dos redes, porque una sola no basta:
//   1. Un temporizador que renueva 5 min ANTES de vencer (el caso normal).
//   2. Un reintento cuando una llamada devuelve 401 (ver `lib/api.ts`), para
//      cuando el temporizador no corrió — la laptop durmió, la pestaña estuvo
//      congelada, o el navegador ahorró batería.

let enVuelo: Promise<boolean> | null = null;
let temporizador: number | null = null;

/**
 * Cambia el token de refresco por uno nuevo. Devuelve si lo logró.
 *
 * El candado `enVuelo` es la misma cura que el del backend: al despertar la
 * laptop, TODAS las peticiones pendientes fallan con 401 a la vez y pedirían
 * renovar en paralelo. La primera renovación INVALIDA el token de refresco
 * (Supabase los rota), así que las demás fallarían y cerrarían la sesión de una
 * persona que sí la tenía. Con el candado, una renueva y las otras esperan.
 */
export async function refrescar(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (enVuelo) return enVuelo;

  enVuelo = (async () => {
    const refresco = window.localStorage.getItem(LLAVE_REFRESH) || "";
    if (!refresco || !URL_SUPABASE || !ANON) return false;
    try {
      const res = await fetch(`${URL_SUPABASE}/auth/v1/token?grant_type=refresh_token`, {
        method: "POST",
        headers: { "Content-Type": "application/json", apikey: ANON },
        body: JSON.stringify({ refresh_token: refresco }),
      });
      if (!res.ok) {
        // 400/401 = el refresco ya no vale (caducó o se revocó la sesión).
        // Cualquier otro código puede ser un tropiezo de red: NO se cierra
        // sesión por eso, se reintentará en la siguiente llamada.
        if (res.status === 400 || res.status === 401) cerrarSesion();
        return false;
      }
      const d = await res.json();
      if (!d?.access_token) return false;
      guardar(d.access_token, d.refresh_token || refresco, d.expires_in);
      programarRefresco();
      return true;
    } catch {
      return false;   // sin red: se reintenta después, no se cierra sesión
    }
  })();

  try {
    return await enVuelo;
  } finally {
    enVuelo = null;
  }
}

/** Programa la próxima renovación. Idempotente: reemplaza la anterior. */
export function programarRefresco(): void {
  if (typeof window === "undefined" || !haySesion()) return;
  if (temporizador !== null) window.clearTimeout(temporizador);
  // Mínimo 5 s para no entrar en bucle si el reloj viene raro.
  const espera = Math.max(5000, vidaRestante() - MARGEN_MS);
  temporizador = window.setTimeout(() => { void refrescar(); }, espera);
}

/**
 * Arranca el mantenimiento de la sesión. Lo llama SesionGuard al montar.
 * Devuelve la función para desmontarlo.
 */
export function cuidarSesion(): () => void {
  if (typeof window === "undefined") return () => {};
  // Si la pestaña estuvo dormida, al volver puede que el token ya haya vencido
  // sin que el temporizador llegara a correr.
  const alVolver = () => {
    if (!haySesion()) return;
    if (vidaRestante() <= MARGEN_MS) void refrescar();
    else programarRefresco();
  };
  window.addEventListener("focus", alVolver);
  document.addEventListener("visibilitychange", alVolver);
  alVolver();
  return () => {
    window.removeEventListener("focus", alVolver);
    document.removeEventListener("visibilitychange", alVolver);
    if (temporizador !== null) {
      window.clearTimeout(temporizador);
      temporizador = null;
    }
  };
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
    // UN 401 NO ES "NO SÉ": es la API diciendo que YA exige credencial. Tratarlo
    // como desconocido fue el bug del 5-ago — el guardián lo leía como
    // "enforcement apagado" y dejaba entrar sin sesión justo cuando se encendió.
    if (res.status === 401 || res.status === 403) {
      return { ...anon, auth_activa: true };
    }
    if (!res.ok) return anon;   // 5xx o red: de verdad no sabemos
    return (await res.json()) as Usuario;
  } catch {
    return anon;
  }
}
