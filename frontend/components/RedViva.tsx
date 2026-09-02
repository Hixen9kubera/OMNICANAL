"use client";

/**
 * RedViva — el grafo de fuerzas de la Red viva, en un <canvas>.
 *
 * Portado del prototipo local (flujo-vivo/index.html). El componente dibuja y
 * anima; los DATOS los sondea la página y llegan por props (`pulso`), para que
 * la misma señal alimente también las tarjetas de salud sin sondear dos veces.
 *
 * Reglas que ya costaron aprenderse en el prototipo:
 *  - La cámara SIEMPRE tiene camino de vuelta: encuadre al montar, al
 *    redimensionar (si el usuario no la movió) y con doble clic.
 *  - El caudal se comprime con log10 al emitir partículas: 1 evento y 2,000
 *    no pueden diferir 2,000 veces o la arista floja jamás se vería moverse.
 *  - requestAnimationFrame muere en pestañas ocultas; no pasa nada, el estado
 *    vive en refs y revive al volver.
 */

import { useEffect, useRef } from "react";
import { API_BASE, fetchSesion } from "@/lib/api";

export interface NodoRed {
  id: string; etiqueta: string; clase: "externo" | "proceso" | "tabla";
  grupo: string; filas: number | null;
  x?: number; y?: number; vx?: number; vy?: number; ax?: number; ay?: number;
  escrituras?: number | null;
}
export interface AristaRed {
  de: string; a: string; clase: "fk" | "flujo";
  n?: number | null;
}
export interface PulsoRed {
  tablas: Record<string, { vivas: number; escrituras: number | null }>;
  flujos: { de: string; a: string; n: number | null }[];
}

const COLOR: Record<string, string> = {
  core: "#4ADE9B", channel: "#5EB8F0", costing: "#F0B45E", enrich: "#C08BF0",
  ops: "#F07C9B", migration: "#8B93A8", analytics: "#5ED8D8", public: "#A8C060",
  propuestas_retirado: "#4A5654", canal: "#E8E4D8", tienda: "#E8E4D8",
  erp: "#E8E4D8", webhook: "#7FE0D4", "sondeo 5 min": "#7FE0D4",
  "sondeo 10 min": "#7FE0D4", "cada 15 min": "#7FE0D4", "cron 06:15": "#7FE0D4",
  panel: "#7FE0D4", "por venta": "#7FE0D4",
};
// Paleta para el tema claro (propuesta B): tonos 600 de las mismas familias,
// que aguantan sobre blanco. El flujo cambia de verde a indigo — el verde del
// tema oscuro se lava sobre claro y el indigo es el acento del panel.
const COLOR_CLARO: Record<string, string> = {
  core: "#059669", channel: "#2563eb", costing: "#d97706", enrich: "#7c3aed",
  ops: "#db2777", migration: "#64748b", analytics: "#0891b2", public: "#65a30d",
  propuestas_retirado: "#94a3b8", canal: "#475569", tienda: "#475569",
  erp: "#475569", webhook: "#0f766e", "sondeo 5 min": "#0f766e",
  "sondeo 10 min": "#0f766e", "cada 15 min": "#0f766e", "cron 06:15": "#0f766e",
  panel: "#0f766e", "por venta": "#0f766e",
};
const CARRIL: Record<string, number> = { externo: 0.09, proceso: 0.3, tabla: 0.68 };
export type TemaRed = "oscuro" | "claro";

export default function RedViva({ pulso, onNodo, compacto = false, tema = "oscuro" }: {
  pulso: PulsoRed | null;
  onNodo?: (id: string) => void;
  compacto?: boolean;
  tema?: TemaRed;
}) {
  const cvRef = useRef<HTMLCanvasElement>(null);
  // `compacto` entra a los efectos por ref: asi TODOS los efectos llevan deps
  // [] y Fast Refresh nunca ve arrays de tamano distinto entre versiones (el
  // sintoma fue un lienzo con closures viejas y medida congelada en cero).
  const compactoRef = useRef(compacto); compactoRef.current = compacto;
  const temaRef = useRef(tema); temaRef.current = tema;
  const st = useRef({
    nodos: [] as NodoRed[],
    aristas: [] as AristaRed[],
    porId: new Map<string, NodoRed>(),
    particulas: [] as { e: AristaRed; t: number; v: number }[],
    camara: { x: 0, y: 0, z: 1 },
    camaraTocada: false,
    arrastre: null as null | { nodo?: NodoRed; pan?: boolean; x?: number; y?: number; sx: number; sy: number },
    fijado: null as NodoRed | null,
    encima: null as NodoRed | null,
    W: 0, H: 0,
    necesitaAcomodo: false,
    ultimaPintada: 0,
  });

  const radio = (n: NodoRed) => {
    const base = compactoRef.current ? 0.62 : 1;
    if (n.clase !== "tabla") return 9 * base;
    return (4 + Math.min(13, Math.log10(Math.max(n.filas ?? 0, 1) + 1) * 3.1)) * base;
  };

  const medir = () => {
    const cv = cvRef.current; if (!cv) return;
    const s = st.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    s.W = cv.clientWidth; s.H = cv.clientHeight;
    cv.width = s.W * dpr; cv.height = s.H * dpr;
    cv.getContext("2d")!.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  const acomodar = () => {
    const s = st.current;
    const esquemas = [...new Set(s.nodos.filter(n => n.clase === "tabla").map(n => n.grupo))].sort();
    const banda: Record<string, number> = {};
    esquemas.forEach((e, i) => { banda[e] = (i + 0.5) / esquemas.length; });
    for (const n of s.nodos) {
      n.ax = s.W * (CARRIL[n.clase] ?? 0.5);
      if (n.clase === "tabla") n.ay = s.H * (0.08 + 0.84 * banda[n.grupo]);
      else {
        const pares = s.nodos.filter(m => m.clase === n.clase);
        n.ay = s.H * (0.1 + 0.8 * (pares.indexOf(n) + 0.5) / pares.length);
      }
      if (n.x === undefined) { n.x = n.ax + (Math.random() - 0.5) * 60; n.y = n.ay! + (Math.random() - 0.5) * 60; }
      n.vx = n.vy = 0;
    }
  };

  const encuadrar = () => {
    const s = st.current;
    if (!s.nodos.length || !s.W || !s.H) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of s.nodos) {
      x0 = Math.min(x0, n.x ?? n.ax!); y0 = Math.min(y0, n.y ?? n.ay!);
      x1 = Math.max(x1, n.x ?? n.ax!); y1 = Math.max(y1, n.y ?? n.ay!);
    }
    const cp = compactoRef.current;
    const m = cp ? 24 : 70;
    // Tope en 1.3: radios y letras estan pensados a escala ~1. Ampliar mas
    // solo agranda un layout roto.
    const z = Math.max(0.25, Math.min(cp ? 2.5 : 1.3,
      Math.min(s.W / (x1 - x0 + 2 * m), s.H / (y1 - y0 + 2 * m))));
    s.camara = { z, x: s.W / 2 - z * (x0 + x1) / 2, y: s.H / 2 - z * (y0 + y1) / 2 };
  };

  useEffect(() => {
    let vivo = true;
    (async () => {
      try {
        const r = await fetchSesion(`${API_BASE}/api/flujo/topologia`, { cache: "no-store" });
        const t = await r.json();
        if (!vivo) return;
        const s = st.current;
        s.nodos = t.nodos; s.aristas = t.aristas;
        s.porId = new Map(s.nodos.map((n: NodoRed) => [n.id, n]));
        s.necesitaAcomodo = true;
      } catch { /* la página muestra su propio estado de conexión */ }
    })();
    return () => { vivo = false; };
  }, []);

  useEffect(() => {
    const cv = cvRef.current;
    if (!cv || compactoRef.current) return;
    // React registra los onWheel de JSX como PASIVOS: su preventDefault no
    // detiene el scroll y la pagina se desplazaba junto con el zoom del
    // grafo. Nativo y con passive:false, igual que en el prototipo local.
    const alRodar = (e: WheelEvent) => {
      e.preventDefault();
      const s = st.current;
      const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      const k = Math.exp(-e.deltaY * 0.0013), z = Math.max(0.3, Math.min(3.2, s.camara.z * k));
      s.camara.x = mx - (mx - s.camara.x) * (z / s.camara.z);
      s.camara.y = my - (my - s.camara.y) * (z / s.camara.z);
      s.camara.z = z; s.camaraTocada = true;
    };
    cv.addEventListener("wheel", alRodar, { passive: false });
    return () => cv.removeEventListener("wheel", alRodar);
  }, []);

  useEffect(() => {
    if (!pulso) return;
    const s = st.current;
    for (const n of s.nodos) {
      const t = pulso.tablas[n.id];
      if (t) { n.filas = t.vivas; n.escrituras = t.escrituras; }
    }
    const mapa = new Map(pulso.flujos.map(f => [f.de + ">" + f.a, f.n]));
    for (const e of s.aristas) if (e.clase === "flujo") e.n = mapa.get(e.de + ">" + e.a) ?? null;
  }, [pulso]);

  useEffect(() => {
    const cv = cvRef.current; if (!cv) return;
    const cx = cv.getContext("2d")!;
    let raf = 0;

    const paso = () => {
      const s = st.current;
      const K_REP = 1400, K_ANCLA = 0.014, AMORT = 0.86;
      for (let i = 0; i < s.nodos.length; i++) {
        const a = s.nodos[i];
        if (a === s.fijado) continue;
        for (let j = i + 1; j < s.nodos.length; j++) {
          const b = s.nodos[j];
          let dx = b.x! - a.x!, dy = b.y! - a.y!, d2 = dx * dx + dy * dy;
          if (d2 < 1) d2 = 1;
          if (d2 > 40000) continue;
          const f = K_REP / d2, d = Math.sqrt(d2), fx = f * dx / d, fy = f * dy / d;
          a.vx! -= fx; a.vy! -= fy; b.vx! += fx; b.vy! += fy;
        }
        a.vx! += (a.ax! - a.x!) * K_ANCLA;
        a.vy! += (a.ay! - a.y!) * K_ANCLA;
      }
      for (const e of s.aristas) {
        if (e.clase !== "flujo") continue;
        const a = s.porId.get(e.de), b = s.porId.get(e.a);
        if (!a || !b) continue;
        const dx = b.x! - a.x!, dy = b.y! - a.y!, d = Math.hypot(dx, dy) || 1;
        const f = (d - 190) * 0.0016;
        a.vx! += f * dx / d; a.vy! += f * dy / d; b.vx! -= f * dx / d; b.vy! -= f * dy / d;
      }
      for (const n of s.nodos) {
        if (n === s.fijado) { n.vx = n.vy = 0; continue; }
        n.vx! *= AMORT; n.vy! *= AMORT;
        n.x! += Math.max(-6, Math.min(6, n.vx!));
        n.y! += Math.max(-6, Math.min(6, n.vy!));
      }
    };

    const emitir = () => {
      const s = st.current;
      for (const e of s.aristas) {
        if (e.clase !== "flujo" || !(e.n && e.n > 0)) continue;
        const tasa = Math.min(0.42, 0.02 + Math.log10(e.n + 1) * 0.09);
        if (Math.random() < tasa) s.particulas.push({ e, t: 0, v: 0.01 + Math.random() * 0.008 });
      }
      if (s.particulas.length > 900) s.particulas.splice(0, s.particulas.length - 900);
    };

    const pintar = () => {
      const s = st.current;
      s.ultimaPintada = performance.now();
      const claro = temaRef.current === "claro";
      const paleta = claro ? COLOR_CLARO : COLOR;
      cx.clearRect(0, 0, s.W, s.H);
      cx.save(); cx.translate(s.camara.x, s.camara.y); cx.scale(s.camara.z, s.camara.z);

      cx.strokeStyle = claro ? "rgba(148,163,184,0.32)" : "rgba(120,150,145,0.13)";
      cx.lineWidth = 0.6;
      cx.beginPath();
      for (const e of s.aristas) {
        if (e.clase !== "fk") continue;
        const a = s.porId.get(e.de), b = s.porId.get(e.a);
        if (!a || !b) continue;
        cx.moveTo(a.x!, a.y!);
        cx.quadraticCurveTo((a.x! + b.x!) / 2, (a.y! + b.y!) / 2 - 24, b.x!, b.y!);
      }
      cx.stroke();

      for (const e of s.aristas) {
        if (e.clase !== "flujo") continue;
        const a = s.porId.get(e.de), b = s.porId.get(e.a);
        if (!a || !b) continue;
        const vivoE = !!(e.n && e.n > 0);
        cx.strokeStyle = vivoE
          ? (claro ? "rgba(79,70,229,0.40)" : "rgba(74,222,155,0.30)")
          : (claro ? "rgba(148,163,184,0.22)" : "rgba(120,150,145,0.11)");
        cx.lineWidth = vivoE ? 1.3 : 0.7;
        cx.beginPath(); cx.moveTo(a.x!, a.y!); cx.lineTo(b.x!, b.y!); cx.stroke();
      }

      for (const p of s.particulas) {
        const a = s.porId.get(p.e.de), b = s.porId.get(p.e.a);
        if (!a || !b) continue;
        const x = a.x! + (b.x! - a.x!) * p.t, y = a.y! + (b.y! - a.y!) * p.t;
        cx.fillStyle = claro
          ? `rgba(79,70,229,${0.8 * (1 - Math.abs(p.t - 0.5) * 1.1)})`
          : `rgba(120,255,190,${0.85 * (1 - Math.abs(p.t - 0.5) * 1.1)})`;
        cx.beginPath(); cx.arc(x, y, 1.9, 0, 6.284); cx.fill();
      }

      for (const n of s.nodos) {
        const r = radio(n), col = paleta[n.grupo] || (claro ? "#64748b" : "#8B93A8");
        if (n.escrituras && n.escrituras > 0) {
          const fase = (performance.now() / 620) % 1;
          cx.strokeStyle = claro
            ? `rgba(79,70,229,${0.55 * (1 - fase)})`
            : `rgba(74,222,155,${0.5 * (1 - fase)})`; cx.lineWidth = 1.4;
          cx.beginPath(); cx.arc(n.x!, n.y!, r + 3 + fase * 13, 0, 6.284); cx.stroke();
        }
        cx.fillStyle = col + (n.clase === "tabla" ? "26" : "33");
        cx.strokeStyle = col; cx.lineWidth = n === s.encima ? 2 : 1.1;
        cx.beginPath();
        if (n.clase === "proceso") cx.rect(n.x! - r, n.y! - r * 0.72, r * 2, r * 1.44);
        else if (n.clase === "externo") {
          cx.moveTo(n.x!, n.y! - r); cx.lineTo(n.x! + r, n.y!);
          cx.lineTo(n.x!, n.y! + r); cx.lineTo(n.x! - r, n.y!); cx.closePath();
        } else cx.arc(n.x!, n.y!, r, 0, 6.284);
        cx.fill(); cx.stroke();

        const etiquetar = !compactoRef.current && (n.clase !== "tabla" || n === s.encima);
        if (etiquetar && s.camara.z > 0.55) {
          cx.fillStyle = n === s.encima
            ? (claro ? "#0f172a" : "#E4E8E6")
            : (claro ? "rgba(71,85,105,0.85)" : "rgba(180,195,192,0.72)");
          cx.font = (n === s.encima ? "600 " : "") + "10.5px ui-sans-serif, system-ui, sans-serif";
          cx.textAlign = "left"; cx.fillText(n.etiqueta, n.x! + r + 5, n.y! + 3.5);
        }
      }
      cx.restore();
    };

    const avanzar = () => {
      const s = st.current;
      // La UNICA fuente de verdad del tamano es este chequeo: si el canvas
      // cambio (montaje, flex, resize, doble montaje de dev), se mide y se
      // reacomoda aqui mismo. Nada de observers ni de adivinar tiempos.
      if (cv.clientWidth !== s.W || cv.clientHeight !== s.H || s.necesitaAcomodo) {
        medir();
        if (s.W > 2 && s.H > 2 && s.nodos.length) {
          acomodar();
          if (!s.camaraTocada) encuadrar();
          s.necesitaAcomodo = false;
        }
      }
      paso(); emitir();
      for (let i = s.particulas.length - 1; i >= 0; i--) {
        s.particulas[i].t += s.particulas[i].v;
        if (s.particulas[i].t >= 1) s.particulas.splice(i, 1);
      }
    };
    // La fisica va en un interval y el pintado en rAF, SEPARADOS a proposito:
    // en pestanas ocultas u ocluidas el navegador congela el rAF (o lo baja a
    // ~1 cuadro/seg), y con todo dentro del rAF el grafo nunca se asentaba —
    // volvia uno a la pestana y lo encontraba a medio acomodar. El interval
    // sigue corriendo (estrangulado a 1 Hz, suficiente para asentar) y el rAF
    // pinta lo que haya cuando haya cuadro.
    const sim = setInterval(() => {
      avanzar();
      // Respaldo de pintado: hay entornos (paneles embebidos, ventanas
      // ocluidas) donde el rAF no dispara NUNCA. Si lleva >250 ms sin pintar,
      // pinta el interval — 1 fps de respaldo; el rAF manda cuando existe.
      if (performance.now() - (st.current.ultimaPintada || 0) > 250) pintar();
    }, 33);
    const cuadro = () => { pintar(); raf = requestAnimationFrame(cuadro); };
    raf = requestAnimationFrame(cuadro);
    return () => { clearInterval(sim); cancelAnimationFrame(raf); };
  }, []);

  const aMundo = (ev: { clientX: number; clientY: number }) => {
    const cv = cvRef.current!, s = st.current;
    const r = cv.getBoundingClientRect();
    return { x: (ev.clientX - r.left - s.camara.x) / s.camara.z, y: (ev.clientY - r.top - s.camara.y) / s.camara.z };
  };
  const bajoElCursor = (ev: { clientX: number; clientY: number }) => {
    const m = aMundo(ev), s = st.current;
    return s.nodos.find(n => Math.hypot(n.x! - m.x, n.y! - m.y) < radio(n) + 4) || null;
  };

  if (compacto) {
    return <canvas ref={cvRef} style={{ width: "100%", height: "100%", display: "block" }} />;
  }
  return (
    <canvas
      ref={cvRef}
      style={{ width: "100%", height: "100%", display: "block", cursor: "grab" }}
      onMouseDown={e => {
        const s = st.current, n = bajoElCursor(e);
        if (n) { s.fijado = n; s.arrastre = { nodo: n, sx: e.clientX, sy: e.clientY }; }
        else s.arrastre = { pan: true, x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY };
      }}
      onMouseMove={e => {
        const s = st.current, m = aMundo(e);
        if (s.arrastre?.nodo) { s.arrastre.nodo.x = m.x; s.arrastre.nodo.y = m.y; }
        else if (s.arrastre?.pan) {
          s.camara.x += e.clientX - s.arrastre.x!; s.camara.y += e.clientY - s.arrastre.y!;
          s.arrastre.x = e.clientX; s.arrastre.y = e.clientY; s.camaraTocada = true;
        } else s.encima = bajoElCursor(e);
      }}
      onMouseUp={e => {
        const s = st.current;
        if (s.arrastre?.nodo && Math.hypot(e.clientX - s.arrastre.sx, e.clientY - s.arrastre.sy) < 5)
          onNodo?.(s.arrastre.nodo.id);
        s.arrastre = null; s.fijado = null;
      }}
      onDoubleClick={() => { st.current.camaraTocada = false; encuadrar(); }}
    />
  );
}
