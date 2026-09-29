"use client";

/**
 * Entrada con la llave compartida del laboratorio (`LAB_ACCESS_KEY`).
 *
 * La llave se manda UNA vez a `POST /api/lab/sesion`; el servidor responde con la
 * cookie httpOnly `lab_sesion` (HMAC de la llave) y la web nunca la guarda: ni
 * localStorage ni memoria más allá de este formulario.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, FlaskConical, Loader2, ShieldCheck } from "lucide-react";
import { estadoSesion, iniciarSesion, USA_FIXTURES } from "@/lib/api";

/**
 * Sólo rutas internas: un `volver` a otro dominio sería una redirección abierta.
 * Revisar el texto no basta (`/\example.com` o `/<TAB>/example.com`: el navegador
 * los normaliza a `//example.com`), así que se RESUELVE contra el origen actual,
 * se exige el mismo origen y se navega con la ruta ya resuelta, nunca con el texto.
 */
function destinoSeguro(v: string | null): string {
  const porOmision = "/publicaciones";
  if (!v || !v.startsWith("/")) return porOmision;
  try {
    const u = new URL(v, window.location.origin);
    if (u.origin !== window.location.origin) return porOmision;
    if (u.pathname.startsWith("//") || u.pathname.startsWith("/login")) return porOmision;
    return `${u.pathname}${u.search}${u.hash}`;
  } catch {
    return porOmision;
  }
}

export default function Login() {
  const router = useRouter();
  const [llave, setLlave] = useState("");
  const [ver, setVer] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [volver, setVolver] = useState("/publicaciones");

  useEffect(() => {
    const destino = destinoSeguro(new URLSearchParams(window.location.search).get("volver"));
    setVolver(destino);
    // Con sesión viva (o el laboratorio abierto en local) no se pide la llave otra vez.
    void estadoSesion().then((s) => { if (s?.autenticado) router.replace(destino); });
  }, [router]);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    if (!llave.trim()) { setError("Escribe la llave de acceso."); return; }
    setEnviando(true);
    setError(null);
    try {
      const motivo = await iniciarSesion(llave.trim());
      if (motivo) { setError(motivo); setEnviando(false); return; }
      setLlave("");
      router.replace(volver);
    } catch {
      setError("No se pudo contactar al laboratorio.");
      setEnviando(false);
    }
  }

  return (
    <main className="relative grid min-h-screen place-items-center overflow-hidden bg-gradient-to-br from-[#f6f7fb] to-[#eef0ff] px-4 py-10">
      <div className="pointer-events-none absolute h-[560px] w-[560px] max-w-[140vw] rounded-full bg-[radial-gradient(circle,rgba(79,70,229,0.12)_0%,rgba(79,70,229,0)_68%)]" />
      <form onSubmit={entrar}
            className="relative w-full max-w-[400px] animate-fade-in rounded-[18px] bg-white px-6 pb-6 pt-9 shadow-[0_18px_48px_rgba(31,36,48,0.12),0_2px_6px_rgba(31,36,48,0.05)] sm:px-8">
        <div className="mb-7 text-center">
          <div className="mx-auto mb-3.5 flex h-[52px] w-[52px] items-center justify-center rounded-[15px] bg-gradient-to-br from-indigo-600 to-indigo-400 text-white shadow-[0_6px_18px_rgba(79,70,229,0.32)]">
            <FlaskConical size={24} />
          </div>
          <h1 className="text-[19px] font-bold tracking-tight text-slate-900">Laboratorio de precios</h1>
          <p className="mt-1 text-[13px] text-slate-500">Kubera · Omnicanal</p>
        </div>

        <label className="block">
          <span className="mb-1.5 block text-[12.5px] font-semibold text-slate-600">Llave de acceso</span>
          <span className="relative block">
            <input type={ver ? "text" : "password"} value={llave} onChange={(e) => setLlave(e.target.value)}
                   autoComplete="current-password" autoFocus disabled={enviando} placeholder="••••••••••"
                   className="h-[43px] w-full rounded-[10px] border-[1.5px] border-[#e3e6ef] bg-[#fbfbfd] pl-3.5 pr-11 text-[14.5px] text-slate-900 outline-none transition placeholder:text-slate-300 focus:border-indigo-600 focus:bg-white focus:shadow-[0_0_0_3.5px_rgba(79,70,229,0.13)] disabled:opacity-60" />
            <button type="button" onClick={() => setVer((v) => !v)} aria-label={ver ? "Ocultar llave" : "Mostrar llave"}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-slate-500 hover:bg-indigo-50 hover:text-indigo-600">
              {ver ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </span>
        </label>

        {error && (
          <p className="mt-3 rounded-r-md border-l-[3px] border-rose-600 bg-rose-50 px-3 py-2 text-[13px] text-rose-800" role="alert">{error}</p>
        )}

        <button type="submit" disabled={enviando}
                className="mt-5 inline-flex h-[45px] w-full items-center justify-center gap-2 rounded-[10px] bg-gradient-to-br from-indigo-600 to-indigo-400 text-[14.5px] font-semibold text-white shadow-[0_5px_14px_rgba(79,70,229,0.3)] transition hover:-translate-y-px hover:shadow-[0_8px_20px_rgba(79,70,229,0.36)] disabled:cursor-wait disabled:opacity-80">
          {enviando ? <Loader2 size={16} className="animate-spin" /> : null}
          {enviando ? "Entrando…" : "Entrar"}
        </button>

        <p className="mt-5 flex items-center justify-center gap-1.5 border-t border-slate-100 pt-4 text-center text-[12px] text-slate-500">
          <ShieldCheck size={13} /> Solo lectura · ningún precio se aplica sin autorización
        </p>
        {USA_FIXTURES && (
          <p className="mt-2 text-center text-[11px] text-amber-700">Modo fixtures: cualquier llave entra.</p>
        )}
      </form>
    </main>
  );
}
