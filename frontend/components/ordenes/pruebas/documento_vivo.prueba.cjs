/**
 * El DOCUMENTO de una orden de venta, MONTADO: qué pasa al dar clic y al
 * contestar el servidor.
 *
 *     node frontend/components/ordenes/pruebas/documento_vivo.prueba.cjs
 *
 * Por qué existe: `ordenes.prueba.cjs` prueba la regla y `documento_pinta.prueba.cjs`
 * lo que se ve, pero ninguna de las dos da clic. Y los tres hallazgos de pantalla
 * de la tercera revisión (9-oct-2026, al poder editar una confirmada) viven
 * justo ahí, entre el clic y la respuesta:
 *
 *   · PANT-1 · con qué `rev` viaja la acción de un DIÁLOGO. El documento sigue
 *     releyendo con el diálogo abierto y éste se repinta solo; con la `rev` de
 *     ahora, «Marcar DELIVERED» abierto sobre 2 piezas registraba 8.
 *   · PANT-2 · la salida de «hay cambios sin guardar» cuando ese guardado es
 *     imposible: la pantalla mandaba a cancelar y su guardia regresaba al mismo
 *     rechazo.
 *   · PANT-3 · el «libre» del renglón que todavía no está guardado, tras un 409
 *     «no alcanzó»: se quedaba con el dato viejo, contradiciendo al servidor.
 *
 * Cómo se monta sin navegador ni dependencias nuevas: con `react-dom/client`
 * (que el frontend ya trae) sobre el DOM mínimo de `dom.cjs`. Lo que NO es el
 * código real, a la vista para que nadie lo adivine:
 *
 *   · El servidor. `fetchSesion` (lib/api.ts) se cambia por `servidor()`: guarda
 *     la orden «como está en la base», anota lo que la pantalla le pide y
 *     contesta lo que cada prueba le dicta. Sus respuestas tienen la forma de
 *     `backend/routers/ordenes_venta.py`; el texto del 409 por `rev` vieja es el
 *     `_MSG_CAMBIO` del servicio. El chat es el de verdad: su petición
 *     «sostenida» se queda esperando hasta que `cambiar()` mueve la orden, que
 *     es como otra persona —otro navegador— le avisa a esta pantalla.
 *   · `Traza`: un <canvas> con un Web Worker. Aquí no pinta nada que importe.
 *
 * Lo que sigue sin probarse aquí: el foco, el teclado, el `datetime-local` a
 * medias y todo lo que sea PINTAR (aquí nada se dibuja ni se mide). Por eso,
 * que un aviso «no quede bajo el velo» no se comprueba por su z-index: se
 * comprueba viendo que el diálogo, con su velo, ya no está montado.
 */
"use strict";

// React elige su build AL CARGARSE: la de producción no pide `act()` ni revisa
// el DOM con más APIs de las que `dom.cjs` trae. Va antes de cualquier `require`.
process.env.NODE_ENV = "production";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

// El DOM también va ANTES de React: react-dom mira `window` al cargarse.
const { instalar, Evento } = require("./dom.cjs");
const documento = instalar();
const cargar = require("./cargar.cjs");

const FRONTEND = path.resolve(__dirname, "..", "..", "..");
const React = require(path.join(FRONTEND, "node_modules", "react"));
const { createRoot } = require(path.join(FRONTEND, "node_modules", "react-dom", "client"));
const lib = require(path.join(FRONTEND, "lib", "api"));

const doc = cargar("OrdenDocumento");
cargar("Traza").Traza = () => null;

// ── Armazones (como los de `documento_pinta.prueba.cjs`) ─────────────────────

const bodega = (codigo, nombre, fuente, admite_ov) => ({
  codigo, nombre, fuente, admite_ov, surte_ventas: false, cuenta_para_woo: false,
});
const TEXCO = bodega("TEXCO", "TEXCO", "odoo", false);
const TEX3 = bodega("TEX3", "TEXCO III", "kubera", true);
const ENSAYO = bodega("ENSAYO", "Bodega de ensayo", "kubera", true);
/** Hoy: sólo ENSAYO admite órdenes. Fase B: también TEX3. */
const HOY = [TEXCO, { ...TEX3, admite_ov: false }, ENSAYO];
const FASE_B = [TEXCO, TEX3, ENSAYO];

const bandera = (encendido) => ({ encendido, persistido: encendido, actualizado_por: null, motivo: null, actualizado_at: null });
const modulo = (bodegas = HOY) => ({
  ok: true, falta_migracion: false, habilitado: true,
  banderas: { ordenes_venta: bandera(true), ov_generacion_auto: bandera(false) },
  bodegas, archivos: { disponible: false, motivo: "Falta crear el bucket «ordenes-venta» en Storage." },
  yo: { actor: "bodega@kubera.mx", nombre: "Bodega", rol: "admin", via: "panel", admin: true, escribe: true },
});
const TODO = {
  editar: true, confirmar: true, entregar: true, cancelar: true, borrar: true, responder_salio: true,
  salio_tarde: true, mensajes: true, subir_archivo: true, bajar_archivo: true, borrar_archivo: true, porque: {},
};

/** El saldo de un SKU en una bodega, como lo dice el catálogo. */
const saldo = (almacen, fisico, apartado) => ({ almacen, fisico, apartado, libre: fisico - apartado });

/** Un renglón como lo manda el backend: en ENSAYO, con 10 físicas y sin apartar. */
const linea = (n, sku, cantidad, mas = {}) => ({
  id: n, linea: n, sku, titulo: `Producto ${sku}`, imagen: null, cantidad, precio_unitario: 10,
  importe: cantidad * 10, almacen: "ENSAYO", reservado: 0, entregado: null, entregado_at: null,
  entregado_por: null, fisico: 10, apartado: 0, libre: 10, conocido: true, ...mas,
});
/** El mismo, en una confirmada: aparta su cantidad completa. */
const apartada = (n, sku, cantidad) =>
  linea(n, sku, cantidad, { reservado: cantidad, apartado: cantidad, libre: 10 - cantidad });

const T = "2026-10-09T15:00:00Z";
const orden = (rev, lineas, mas = {}) => ({
  id: 1, folio: "OV-00012", estado: "borrador", tipo: "venta", rev, cliente: "Temu", canal: "temu",
  mp_canal: null, mp_cuenta: null, mp_orden: null, full_tienda: null, envio_ref: null, descripcion: null,
  guia: "JT1", paqueteria: "J&T", fecha_venta: T, entrega_limite: null, moneda: "MXN",
  total: lineas.reduce((a, l) => a + l.importe, 0), comision: 0, neto: 0, precio_origen: "manual",
  devolucion_estado: null, canal_cancelo_at: null, canal_cancelo_ref: null,
  creado_at: T, creado_por: "ana@kubera.mx", creado_nombre: "Ana", creado_via: "panel",
  confirmada_at: null, confirmada_por: null, confirmada_nombre: null,
  entregada_at: null, entregada_por: null, entregada_nombre: null,
  cancelada_at: null, cancelada_por: null, cancelada_nombre: null, cancelada_origen: null, cancelada_motivo: null,
  borrada_at: null, borrada_por: null, borrada_nombre: null, borrada_motivo: null, actualizado_at: T,
  renglones: lineas.length, piezas: lineas.reduce((a, l) => a + l.cantidad, 0),
  piezas_apartadas: lineas.reduce((a, l) => a + l.reservado, 0),
  piezas_entregadas: lineas.reduce((a, l) => a + (l.entregado ?? 0), 0),
  renglones_entregados: lineas.filter((l) => l.entregado !== null).length,
  skus: lineas.map((l) => l.sku), bodegas: ["ENSAYO"], n_archivos: 0, n_mensajes: 0,
  lineas, archivos: [], permisos: TODO, ...mas,
});
const confirmada = (rev, lineas, mas = {}) => orden(rev, lineas, {
  estado: "confirmada", confirmada_at: T, confirmada_por: "ana@kubera.mx", confirmada_nombre: "Ana", ...mas,
});

/** El 409 del servidor cuando la `rev` que llega no es la de la base (`_MSG_CAMBIO` del servicio). */
const CAMBIO = "La orden cambió mientras tanto; se recargó.";

// ── El servidor de mentira ───────────────────────────────────────────────────

/**
 * Lo que hay «en la base» y lo que la pantalla le pide. `escritura` la pone cada
 * prueba: recibe (método, ruta, cuerpo) y devuelve `{ status, json }`.
 */
function servidor(inicial) {
  const s = {
    orden: inicial,
    /** sku → existencias que contesta el buscador del catálogo. */
    catalogo: {},
    /** Todo lo que la pantalla pidió, en orden (sin el sondeo del chat). */
    pedidos: [],
    escritura: (metodo, ruta) => { throw new Error(`la prueba no esperaba ${metodo} ${ruta}`); },
    /** Las peticiones del chat que el «backend» tiene sostenidas. */
    sostenidas: [],
    /** Con una lista aquí, las respuestas del catálogo NO llegan: se quedan en ella hasta `soltarCatalogo()`. */
    enCamino: null,
  };
  const resp = (status, json) => ({ ok: status >= 200 && status < 300, status, json: async () => json });
  const delChat = () => resp(200, { mensajes: [], ultimo_id: 0, total: 0, rev: s.orden.rev, estado: s.orden.estado });

  s.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const ruta = u.pathname.replace(/^.*\/api\/ordenes-venta/, "");
    const metodo = (init.method || "GET").toUpperCase();
    const cuerpo = typeof init.body === "string" ? JSON.parse(init.body) : null;
    if (metodo === "GET" && /^\/\d+\/mensajes$/.test(ruta)) {
      if (!u.searchParams.has("esperar")) return delChat();
      // El backend SOSTIENE la petición hasta que la orden cambie, o hasta que el chat la suelte.
      return new Promise((listo, fallo) => {
        const soltar = () => listo(delChat());
        s.sostenidas.push(soltar);
        init.signal?.addEventListener("abort", () => {
          s.sostenidas = s.sostenidas.filter((x) => x !== soltar);
          fallo(Object.assign(new Error("abortada"), { name: "AbortError" }));
        });
      });
    }
    if (metodo === "GET" && ruta === "/skus") {
      const q = (u.searchParams.get("q") || "").toLowerCase();
      const opciones = Object.entries(s.catalogo).filter(([sku]) => sku.toLowerCase().includes(q))
        .map(([sku, existencias]) => ({ sku, nombre: `Producto ${sku}`, existencias }));
      s.pedidos.push({ metodo, ruta, q: u.searchParams.get("q"), status: 200 });
      // La respuesta ya está armada (con el saldo de ESTE momento); lo que puede tardar es en llegar.
      if (s.enCamino) return new Promise((listo) => { s.enCamino.push(() => listo(resp(200, { opciones }))); });
      return resp(200, { opciones });
    }
    if (metodo === "GET" && /^\/[^/]+$/.test(ruta)) {
      s.pedidos.push({ metodo, ruta, status: 200, rev: s.orden.rev });
      return resp(200, s.orden);
    }
    // Se anota ANTES de contestar: si la prueba no esperaba esa escritura, que se vea cuál fue.
    const pedido = { metodo, ruta, cuerpo, status: 0 };
    s.pedidos.push(pedido);
    const r = s.escritura(metodo, ruta, cuerpo);
    pedido.status = r.status;
    return resp(r.status, r.json);
  };

  /** OTRA persona movió la orden: cambia en la base, y el chat de esta pantalla se entera. */
  s.cambiar = (nueva) => {
    s.orden = nueva;
    const esperando = s.sostenidas;
    s.sostenidas = [];
    for (const soltar of esperando) soltar();
  };
  /** Llegan, por fin, las respuestas del catálogo que venían en camino. */
  s.soltarCatalogo = () => {
    const llegan = s.enCamino ?? [];
    s.enCamino = null;
    for (const llegar of llegan) llegar();
  };
  /** Las escrituras que la pantalla mandó. */
  s.escrituras = () => s.pedidos.filter((p) => p.metodo !== "GET");
  /** Lo pedido desde `desde`, legible: «PUT /1 409», «GET /skus?q=zzprueba-d 200». */
  s.resumen = (desde = 0) => s.pedidos.slice(desde).map((p) => `${p.metodo} ${p.ruta}${p.q ? `?q=${p.q}` : ""} ${p.status}`);
  return s;
}

/** El candado de `rev` del servidor: si no es la de la base, 409 y no pasa nada; si es, `hacer()`. */
const conCandado = (s, hacer) => (metodo, ruta, cuerpo) => {
  if (cuerpo.rev !== s.orden.rev) return { status: 409, json: { detail: CAMBIO } };
  s.orden = hacer(cuerpo);
  return { status: 200, json: { ok: true, orden: s.orden, mensaje: "Hecho." } };
};

/** Lo que deja el servidor al entregar TODO lo pendiente de una confirmada. */
const entregada = (o) => ({
  ...o, estado: "entregada", rev: o.rev + 1, entregada_at: T, piezas_apartadas: 0,
  piezas_entregadas: o.piezas, renglones_entregados: o.lineas.length,
  lineas: o.lineas.map((l) => ({ ...l, reservado: 0, entregado: l.cantidad, entregado_at: T })),
});

// ── La pantalla ──────────────────────────────────────────────────────────────

const pausa = (ms = 15) => new Promise((seguir) => { setTimeout(seguir, ms); });
const limpio = (t) => t.replace(/\s+/g, " ").trim();
const texto = (n) => (n ? limpio(n.textContent) : "");
/** Todo lo montado, incluidos los diálogos (son portales: cuelgan de <body>). */
const todos = () => documento.body._todos();
const clases = (e) => e.getAttribute("class") || "";

/** Espera a que `cond()` sea verdad; si no llega, truena diciendo qué se esperaba y qué hay. */
async function hasta(cond, que, ms = 5000) {
  const t0 = Date.now();
  for (;;) {
    const v = cond();
    if (v) return v;
    if (Date.now() - t0 > ms) {
      throw new Error(`no llegó en ${ms} ms: ${que}\n— pantalla —\n${texto(documento.body)}`);
    }
    await pausa();
  }
}

const dialogo = () => todos().find((e) => e.getAttribute("role") === "dialog") ?? null;
/** El título del diálogo abierto (su `aria-label`), o "" si no hay ninguno. */
const titulo = () => dialogo()?.getAttribute("aria-label") ?? "";
/** El velo que tapa la pantalla mientras hay un diálogo abierto (`Ventana`: un portal en <body>). */
const velo = () => documento.body.children.find((e) => /fixed inset-0/.test(clases(e))) ?? null;
/** El aviso flotante de abajo, o "". */
const flotante = () => texto(todos().find((e) => e.getAttribute("role") === "status" && /fixed bottom-5/.test(clases(e))));

/** El botón cuyo texto (o `aria-label`) casa. Con texto, exacto; con una expresión, lo que diga. */
function boton(rotulo, raiz = documento.body) {
  const casa = (v) => (typeof rotulo === "string" ? v === rotulo : rotulo.test(v));
  return raiz._todos().find((b) => b.localName === "button"
    && (casa(texto(b)) || casa(b.getAttribute("aria-label") || ""))) ?? null;
}
const rotulos = (raiz) => raiz._todos().filter((b) => b.localName === "button").map(texto).filter(Boolean);

/**
 * El aviso ROJO de arriba del documento —el porqué de lo que no se pudo—, o ""
 * si no hay. Se reconoce por su botón de cerrar (el chip «CANCELADO» también es
 * rosa); el texto es el párrafo que va junto a él.
 */
function avisoRojo() {
  const cerrar = boton("Cerrar el aviso");
  return cerrar ? texto(cerrar.parentNode.children.find((e) => e.localName === "p")) : "";
}

/** Clic como el del navegador: un botón apagado no lo recibe (y aquí es un error de la prueba). */
async function clic(rotulo, raiz) {
  const b = boton(rotulo, raiz);
  assert.ok(b, `no hay botón «${rotulo}» en: ${rotulos(raiz ?? documento.body).join(" | ")}`);
  assert.ok(!b.disabled, `el botón «${rotulo}» está apagado: ${b.getAttribute("title") || "(sin porqué)"}`);
  b.click();
  await pausa();
}

/** El control (campo o selector) con ese `aria-label` o ese `placeholder`. */
function control(etiqueta, raiz = documento.body) {
  const casa = (v) => (typeof etiqueta === "string" ? v === etiqueta : etiqueta.test(v));
  const el = raiz._todos().find((e) => ["input", "textarea", "select"].includes(e.localName)
    && (casa(e.getAttribute("aria-label") || "") || casa(e.getAttribute("placeholder") || "")));
  assert.ok(el && !el.disabled, `no hay control «${etiqueta}» encendido`);
  return el;
}

/** Teclear en un campo: pone el valor y despacha `input`, como el navegador. */
async function teclear(etiqueta, valor, raiz) {
  const el = control(etiqueta, raiz);
  el.value = String(valor);
  el.dispatchEvent(new Evento("input"));
  await pausa();
}

/** Elegir en un `<select>`: pone el valor y despacha `change`. */
async function elegir(etiqueta, valor) {
  const el = control(etiqueta);
  el.value = String(valor);
  el.dispatchEvent(new Evento("change"));
  await pausa();
}

/** La fila de la tabla de renglones de ese SKU. */
const fila = (sku) => todos().find((e) => e.localName === "tr" && e.closest("tbody") && texto(e).includes(sku)) ?? null;
/** Lo que dice de su saldo el renglón de ese SKU («libre 10»), si va en ámbar, y su ayuda. */
function libre(sku) {
  const f = fila(sku);
  const nota = f ? f._todos().find((e) => e.localName === "span" && /^libre /.test(texto(e))) : null;
  return nota ? { texto: texto(nota), ambar: /text-amber-700/.test(clases(nota)), ayuda: nota.getAttribute("title") || "" } : null;
}

/** Monta el documento de la orden que hay en `s` (todavía sin cargar). Devuelve cómo desmontarlo. */
function montar(s, mod) {
  const real = lib.fetchSesion;
  lib.fetchSesion = s.fetch;
  const caja = documento.createElement("div");
  documento.body.appendChild(caja);
  const raiz = createRoot(caja);
  raiz.render(React.createElement(doc.OrdenDocumento, {
    refOrden: s.orden.folio, prefill: null, modulo: mod, sucioRef: { current: false },
    onCerrar() {}, onCreada() {}, onCambio() {},
  }));
  return async () => {
    raiz.unmount();
    caja.remove();
    lib.fetchSesion = real;
    await pausa();
  };
}

/**
 * Una prueba con su documento montado y ya cargado. Pase lo que pase se
 * desmonta: si no, sus relojes siguen vivos y su `fetch` se le queda a la
 * siguiente. `con.bodegas` = el catálogo de bodegas del módulo; `con.catalogo`
 * = lo que el buscador sabe de cada SKU ANTES de montar.
 */
function conDocumento(nombre, inicial, cuerpo, con = {}) {
  test(nombre, async () => {
    const s = servidor(inicial());
    Object.assign(s.catalogo, con.catalogo);
    const desmontar = montar(s, modulo(con.bodegas));
    try {
      await hasta(() => todos().some((e) => e.localName === "tbody"), "que cargue la orden");
      // El chat ya pidió su historial y dejó SOSTENIDA la siguiente petición: desde aquí se entera al instante.
      await hasta(() => s.sostenidas.length > 0, "que el chat quede escuchando");
      await cuerpo(s);
    } finally {
      await desmontar();
    }
  });
}

/** Otra persona mueve la orden con ESTA pantalla abierta, y se espera a que el documento la relea. */
async function cambiaPorFuera(s, nueva) {
  const antes = s.pedidos.length;
  s.cambiar(nueva);
  await hasta(() => s.pedidos.slice(antes).some((p) => p.metodo === "GET" && p.rev === nueva.rev),
              "que el documento relea solo la orden");
  await hasta(() => flotante().includes(CAMBIO), "el aviso de que la orden cambió");
}

// ── PANT-1 · la acción de un diálogo viaja con la `rev` con la que se ABRIÓ ──

conDocumento("REV3 PANT-1 · «Marcar DELIVERED» abierto sobre 2 piezas NO registra las 8 que otra persona dejó después",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  // Bodega abre «Marcar DELIVERED»: ve X × 2.
  await clic("Marcar DELIVERED");
  assert.equal(titulo(), "Marcar OV-00012 como DELIVERED");
  assert.match(texto(dialogo()), /Salen 2 piezas: la orden queda DELIVERED\./);

  // Con el diálogo abierto, otra persona corrige la confirmada (0071): X × 5 y agrega Z × 3. Es la rev 3.
  await cambiaPorFuera(s, confirmada(3, [apartada(1, "ZZPRUEBA-X", 5), apartada(2, "ZZPRUEBA-Z", 3)]));
  // El documento releyó solo y el diálogo SIGUE ABIERTO, repintado con lo que Bodega no vio al abrirlo.
  assert.equal(titulo(), "Marcar OV-00012 como DELIVERED", "un diálogo abierto no detiene al documento");
  assert.match(texto(dialogo()), /Salen 8 piezas: la orden queda DELIVERED\./);

  // El servidor de verdad: entrega TODO lo pendiente si la `rev` es la suya; si no, 409 y nada.
  s.escritura = conCandado(s, () => entregada(s.orden));
  await clic("Sí, marcar DELIVERED", dialogo());
  await hasta(() => s.escrituras().length === 1, "la entrega");
  // LO QUE SE CUIDA: viaja la rev con la que se ABRIÓ el diálogo (2), no la de ahora (3).
  assert.deepEqual(s.escrituras()[0], { metodo: "POST", ruta: "/1/entregar", cuerpo: { rev: 2 }, status: 409 });
  // No salió nada: la orden sigue confirmada, con sus 8 piezas apartadas.
  assert.deepEqual([s.orden.estado, s.orden.rev, s.orden.piezas_entregadas], ["confirmada", 3, 0]);

  // Y el aviso de ese 409 queda A LA VISTA: el diálogo se cerró (ya no hay velo que lo tape) y el
  // porqué del servidor está arriba del documento.
  await hasta(() => !dialogo(), "que el diálogo se cierre tras el rechazo");
  assert.equal(velo(), null);
  assert.equal(avisoRojo(), CAMBIO);

  // Bodega vuelve a abrirlo, ya sobre lo que hay: ahora sí confirma 8 piezas, y con la rev de ahora.
  await clic("Marcar DELIVERED");
  assert.match(texto(dialogo()), /Salen 8 piezas/);
  await clic("Sí, marcar DELIVERED", dialogo());
  await hasta(() => s.escrituras().length === 2, "la segunda entrega");
  assert.deepEqual(s.escrituras()[1], { metodo: "POST", ruta: "/1/entregar", cuerpo: { rev: 3 }, status: 200 });
  assert.deepEqual([s.orden.estado, s.orden.piezas_entregadas], ["entregada", 8]);
  await hasta(() => !dialogo(), "que el diálogo se cierre");
});

conDocumento("REV3 PANT-1 · si el documento todavía NO releía, el mismo 409 se avisa abajo, ya sin el diálogo encima",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  await clic("Marcar DELIVERED");
  // La otra persona guarda su edición, pero el aviso del chat todavía no llega a esta pantalla
  // (la petición sostenida sigue sostenida): el diálogo sigue enseñando lo de antes.
  s.orden = confirmada(3, [apartada(1, "ZZPRUEBA-X", 5), apartada(2, "ZZPRUEBA-Z", 3)]);
  assert.match(texto(dialogo()), /Salen 2 piezas/);

  s.escritura = conCandado(s, () => entregada(s.orden));
  await clic("Sí, marcar DELIVERED", dialogo());
  await hasta(() => s.escrituras().length === 1 && !dialogo(), "el rechazo, y que el diálogo se cierre");
  assert.deepEqual(s.escrituras()[0], { metodo: "POST", ruta: "/1/entregar", cuerpo: { rev: 2 }, status: 409 });
  // Aquí el 409 es el de siempre («la orden cambió»: la relectura trae otra rev): va en el aviso
  // flotante, que con el diálogo ya cerrado no tiene velo encima. Y el documento ya enseña lo nuevo.
  assert.equal(velo(), null);
  assert.equal(flotante(), CAMBIO);
  assert.ok(fila("ZZPRUEBA-Z"), "se recargó con lo que dejó la otra persona");
  assert.deepEqual([s.orden.estado, s.orden.piezas_entregadas], ["confirmada", 0]);
});

conDocumento("REV3 PANT-1 · tras «Guardar y continuar», el diálogo se abre con la rev que dejó ESE guardado (no rebota solo)",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  // Corrige la guía y, sin guardar, va a marcar DELIVERED: se le pregunta, y elige guardar y seguir.
  await teclear("Número de guía", "JT-NUEVA");
  await clic("Marcar DELIVERED");
  assert.equal(titulo(), "Hay cambios sin guardar");
  s.escritura = conCandado(s, (cuerpo) => ({ ...s.orden, rev: 3, guia: cuerpo.guia }));
  await clic("Guardar y continuar", dialogo());
  await hasta(() => titulo() === "Marcar OV-00012 como DELIVERED", "el diálogo de la entrega, ya guardado");
  assert.deepEqual(s.escrituras()[0], { metodo: "PUT", ruta: "/1", cuerpo: { guia: "JT-NUEVA", rev: 2 }, status: 200 });

  // La entrega viaja con la rev 3 —la que la persona tiene delante al abrirse ESTE diálogo—, y pasa.
  s.escritura = conCandado(s, () => entregada(s.orden));
  await clic("Sí, marcar DELIVERED", dialogo());
  await hasta(() => s.escrituras().length === 2, "la entrega");
  assert.deepEqual(s.escrituras()[1], { metodo: "POST", ruta: "/1/entregar", cuerpo: { rev: 3 }, status: 200 });
  assert.equal(s.orden.estado, "entregada");
  await hasta(() => !dialogo(), "que el diálogo se cierre");
  assert.equal(avisoRojo(), "");
});

/** Las demás acciones con diálogo: cómo se llega a cada uno, qué botón lo confirma y qué manda. */
const marcada = { canal_cancelo_at: "2026-10-09T16:00:00Z", canal_cancelo_ref: "IN_TRANSIT" };
const DIALOGOS = [
  { accion: "cancelar", de: () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]),
    abrir: ["Cancelar orden"], titulo: "Cancelar OV-00012",
    motivo: "El cliente ya no la quiso", confirma: "Cancelar la orden", manda: ["POST", "/1/cancelar"] },
  { accion: "borrar", de: () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]),
    abrir: ["Más acciones", "Borrar la orden"], titulo: "Borrar OV-00012",
    motivo: "Se capturó dos veces por error", confirma: "Borrar la orden", manda: ["DELETE", "/1"] },
  { accion: "confirmar", de: () => orden(2, [linea(1, "ZZPRUEBA-X", 2)]),
    abrir: ["Confirmar y apartar"], titulo: "Confirmar OV-00012",
    confirma: "Confirmar y apartar", manda: ["POST", "/1/confirmar"] },
  { accion: "«sí salió»", de: () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)], marcada),
    abrir: ["Sí salió"], titulo: "El paquete de OV-00012 SÍ salió",
    confirma: "Sí salió", manda: ["POST", "/1/salio"], cuerpo: { salio: true } },
  { accion: "«no salió»", de: () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)], marcada),
    abrir: ["No salió"], titulo: "El paquete de OV-00012 NO salió",
    confirma: "No salió", manda: ["POST", "/1/salio"], cuerpo: { salio: false } },
  { accion: "«salió tarde»",
    de: () => orden(2, [linea(1, "ZZPRUEBA-X", 2)], { estado: "cancelada", confirmada_at: T, cancelada_at: T,
                                                       cancelada_por: "ana@kubera.mx", cancelada_origen: "manual",
                                                       cancelada_motivo: "duplicada" }),
    abrir: ["Más acciones", /^Salió tarde/], titulo: "OV-00012: el paquete sí había salido",
    confirma: "Sí, registrar la salida", manda: ["POST", "/1/salio-tarde"] },
];

for (const d of DIALOGOS) {
  conDocumento(`REV3 PANT-1 · ${d.accion}: su diálogo también manda la rev con la que se abrió, y el 409 queda a la vista`,
               d.de, async (s) => {
    for (const paso of d.abrir) await clic(paso);
    assert.equal(titulo(), d.titulo);
    const alAbrir = s.orden.rev;

    // Otra persona mueve la orden (corrige la guía) con el diálogo abierto: el documento relee debajo.
    await cambiaPorFuera(s, { ...s.orden, rev: alAbrir + 1, guia: "JT-OTRA" });
    assert.equal(titulo(), d.titulo, "el diálogo sigue abierto");

    // El candado del servidor: con la rev de ahora la acción PASARÍA (y la prueba lo diría: un 200).
    s.escritura = conCandado(s, () => ({ ...s.orden, rev: s.orden.rev + 1 }));
    if (d.motivo) await teclear(/Por qué se hace esto/, d.motivo, dialogo());
    await clic(d.confirma, dialogo());
    await hasta(() => s.escrituras().length === 1, `que viaje ${d.manda.join(" ")}`);
    const mandado = s.escrituras()[0];
    assert.deepEqual([mandado.metodo, mandado.ruta, mandado.status], [...d.manda, 409]);
    // LO QUE SE CUIDA: la rev de cuando se abrió el diálogo, no la de ahora (y lo demás, lo de siempre).
    assert.deepEqual(mandado.cuerpo, { rev: alAbrir, ...(d.motivo ? { motivo: d.motivo } : {}), ...(d.cuerpo ?? {}) });
    // No pasó nada, y se dice a la vista: sin velo, y con el porqué del servidor arriba.
    await hasta(() => !dialogo(), "que el diálogo se cierre tras el rechazo");
    assert.equal(velo(), null);
    assert.equal(avisoRojo(), CAMBIO);
    assert.equal(s.orden.rev, alAbrir + 1, "la orden quedó como la dejó la otra persona");
  });
}

// ── PANT-2 · «hay cambios sin guardar» tiene salida cuando guardar es imposible ──

conDocumento("REV3 PANT-2 · quitó todos los renglones y la mandan a cancelar: «Descartar cambios y continuar» sí llega",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  const SIN_RENGLONES = "Una orden confirmada necesita al menos un renglón por entregar. Si ya no va, cancélala.";
  // La orden ya no va: quita su único renglón y da «Guardar cambios». La pantalla dice qué hacer.
  await clic("Quitar ZZPRUEBA-X");
  assert.equal(fila("ZZPRUEBA-X"), null);
  await clic("Guardar cambios");
  assert.equal(avisoRojo(), SIN_RENGLONES);

  // Hace lo que dice: «Cancelar orden». Como hay cambios sin guardar, se pregunta; ahora con TRES salidas.
  await clic("Cancelar orden");
  assert.equal(titulo(), "Hay cambios sin guardar");
  assert.match(texto(dialogo()), /hay que guardar lo que cambiaste, o descartarlo: la acción se aplica sobre lo que está guardado\./);
  assert.deepEqual(rotulos(dialogo()), ["Seguir editando", "Descartar cambios y continuar", "Guardar y continuar"]);

  // «Guardar y continuar» sigue regresando al mismo rechazo (ese guardado es imposible): no se llega a cancelar.
  await clic("Guardar y continuar", dialogo());
  assert.equal(dialogo(), null);
  assert.equal(avisoRojo(), SIN_RENGLONES);
  assert.deepEqual(s.escrituras(), [], "nada viajó: la validación lo dice antes de mandar");

  // La otra salida: soltar los cambios y seguir. El renglón vuelve, el rechazo se va, y se abre el de cancelar.
  await clic("Cancelar orden");
  await clic("Descartar cambios y continuar", dialogo());
  assert.equal(titulo(), "Cancelar OV-00012");
  assert.match(texto(dialogo()), /Se cancela la orden y se suelta su apartado \(2 piezas vuelven a quedar libres en su bodega\)/);
  assert.ok(fila("ZZPRUEBA-X"), "el formulario volvió a lo guardado");
  assert.equal(avisoRojo(), "");
  assert.equal(boton("Guardar cambios"), null, "ya no hay cambios pendientes");

  // Y la cancelación entra, sobre lo GUARDADO (la rev de la base, que nadie movió).
  s.escritura = conCandado(s, () => ({ ...s.orden, estado: "cancelada", rev: 3, cancelada_at: T, piezas_apartadas: 0,
                                       lineas: s.orden.lineas.map((l) => ({ ...l, reservado: 0 })) }));
  await teclear(/Por qué se hace esto/, "El cliente ya no la quiso", dialogo());
  await clic("Cancelar la orden", dialogo());
  await hasta(() => s.escrituras().length === 1, "la cancelación");
  assert.deepEqual(s.escrituras()[0], { metodo: "POST", ruta: "/1/cancelar",
                                        cuerpo: { rev: 2, motivo: "El cliente ya no la quiso" }, status: 200 });
  assert.equal(s.orden.estado, "cancelada");
  await hasta(() => !dialogo(), "que el diálogo se cierre");
});

conDocumento("REV3 PANT-2 · «Deshacer» no cambió, y para SALIR con cambios el botón de en medio sigue siendo «Guardar y salir»",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async () => {
  // «Deshacer» sigue haciendo lo de siempre: comparte su código con «Descartar cambios y continuar».
  await teclear("Cantidad de ZZPRUEBA-X", "9");
  await clic("Deshacer");
  assert.equal(control("Cantidad de ZZPRUEBA-X").value, "2");
  assert.equal(boton("Guardar cambios"), null);

  await teclear("Cantidad de ZZPRUEBA-X", "3");
  await clic("Órdenes");
  assert.equal(titulo(), "Hay cambios sin guardar");
  assert.match(texto(dialogo()), /Si sales ahora, lo que cambiaste en esta orden se pierde\./);
  assert.deepEqual(rotulos(dialogo()), ["Seguir editando", "Guardar y salir", "Salir sin guardar"]);
});

// ── PANT-3 · tras un 409 «no alcanzó», el renglón que no está guardado relee su saldo ──

conDocumento("REV3 PANT-3 · el renglón NUEVO no se queda con el «libre» viejo junto al aviso que dice otra cosa",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  // Agrega D desde el buscador cuando D tenía 10 libres en ENSAYO, y pide 3.
  s.catalogo["ZZPRUEBA-D"] = [saldo("ENSAYO", 10, 0)];
  await teclear("Agregar producto por SKU o nombre", "ZZPRUEBA-D");
  await hasta(() => boton(/^ZZPRUEBA-D/), "la opción del buscador");
  await clic(/^ZZPRUEBA-D/);
  await teclear("Cantidad de ZZPRUEBA-D", "3");
  assert.deepEqual([libre("ZZPRUEBA-D").texto, libre("ZZPRUEBA-D").ambar], ["libre 10", false]);

  // Mientras tanto otra orden aparta 9 de D: queda 1 libre. Guardar ya no alcanza, y el servidor dice cuál.
  s.catalogo["ZZPRUEBA-D"] = [saldo("ENSAYO", 10, 9)];
  const NO_ALCANZO = "No alcanzó el stock para guardar el cambio: ZZPRUEBA-D necesita 3 más y hay 1 libre en ENSAYO. No se guardó nada.";
  s.escritura = () => ({ status: 409, json: { detail: NO_ALCANZO } });   // la rev NO se mueve
  const antes = s.pedidos.length;
  await clic("Guardar cambios");
  await hasta(() => avisoRojo() === NO_ALCANZO, "el porqué del servidor");

  // LO QUE SE CUIDA: tras el rechazo se le vuelve a preguntar al catálogo por D, y su «libre» ya es el de ahora.
  await hasta(() => libre("ZZPRUEBA-D").texto === "libre 1", "el saldo fresco del renglón nuevo");
  assert.equal(libre("ZZPRUEBA-D").ambar, true, "y va en ámbar: no alcanza para lo que pide");
  assert.match(libre("ZZPRUEBA-D").ayuda, /no alcanza para las 3 piezas que le faltan por apartar/);
  // En orden: el guardado que rebota, la relectura de la orden, y la pregunta al catálogo (sólo por D:
  // el saldo de X, que sí está guardado, ya vino fresco en la relectura).
  assert.deepEqual(s.resumen(antes), ["PUT /1 409", "GET /1 200", "GET /skus?q=zzprueba-d 200"]);
  // Lo escrito sigue ahí (no se guardó nada, no se perdió nada).
  assert.ok(fila("ZZPRUEBA-D") && boton("Guardar cambios"));
});

conDocumento("REV3 PANT-3 · el renglón que CAMBIÓ DE BODEGA tampoco: lo guardado es la otra bodega, y de ésta hay que volver a preguntar",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  // Con dos bodegas donde elegir, el documento pregunta de entrada cuánto hay de X en la otra (5 libres en TEX3).
  await hasta(() => /TEX3 · libre 5/.test(texto(control("Bodega de ZZPRUEBA-X"))), "el saldo de X en TEX3");
  await elegir("Bodega de ZZPRUEBA-X", "TEX3");
  assert.deepEqual([libre("ZZPRUEBA-X").texto, libre("ZZPRUEBA-X").ambar], ["libre 5", false]);

  // Otra orden aparta 4 de las 5 de TEX3: ya no alcanza para mudar las 2 de X.
  s.catalogo["ZZPRUEBA-X"] = [saldo("ENSAYO", 10, 2), saldo("TEX3", 5, 4)];
  const NO_ALCANZO = "No alcanzó el stock para guardar el cambio: ZZPRUEBA-X necesita 2 más y hay 1 libre en TEX3. No se guardó nada.";
  s.escritura = () => ({ status: 409, json: { detail: NO_ALCANZO } });
  const antes = s.pedidos.length;
  await clic("Guardar cambios");
  await hasta(() => avisoRojo() === NO_ALCANZO, "el porqué del servidor");
  // La relectura de la orden trae el saldo de X en ENSAYO (donde está guardado), no en TEX3 (donde lo puse).
  await hasta(() => libre("ZZPRUEBA-X").texto === "libre 1", "el saldo fresco de la bodega nueva");
  assert.equal(libre("ZZPRUEBA-X").ambar, true);
  assert.deepEqual(s.resumen(antes), ["PUT /1 409", "GET /1 200", "GET /skus?q=zzprueba-x 200"]);
}, { bodegas: FASE_B, catalogo: { "ZZPRUEBA-X": [saldo("ENSAYO", 10, 2), saldo("TEX3", 5, 0)] } });

conDocumento("REV3 PANT-3 · la respuesta del catálogo que llega DESPUÉS de un guardado que sí pasó no pisa el saldo que trajo ese guardado",
             () => confirmada(2, [apartada(1, "ZZPRUEBA-X", 2)]), async (s) => {
  // Agrega D × 3 con 10 libres; para cuando guarda queda 1: 409 «no alcanzó».
  await teclear("Agregar producto por SKU o nombre", "ZZPRUEBA-D");
  await hasta(() => boton(/^ZZPRUEBA-D/), "la opción del buscador");
  await clic(/^ZZPRUEBA-D/);
  await teclear("Cantidad de ZZPRUEBA-D", "3");
  s.catalogo["ZZPRUEBA-D"] = [saldo("ENSAYO", 10, 9)];
  s.escritura = () => ({ status: 409, json: { detail: "No alcanzó el stock para guardar el cambio." } });
  // La red está lenta: la pregunta al catálogo SALE (ya lleva «1 libre»), pero su respuesta tarda en llegar.
  s.enCamino = [];
  await clic("Guardar cambios");
  await hasta(() => s.enCamino.length === 1, "la pregunta al catálogo tras el rechazo");
  assert.equal(libre("ZZPRUEBA-D").texto, "libre 10", "todavía con el dato viejo: la respuesta no ha llegado");

  // Sin esperarla, baja D a 1 pieza y guarda: ahora sí pasa, y el servidor contesta el saldo de D ya
  // con ESA pieza apartada (10 físicas, 10 apartadas: 0 libres).
  await teclear("Cantidad de ZZPRUEBA-D", "1");
  const guardada = confirmada(3, [apartada(1, "ZZPRUEBA-X", 2),
                                  linea(2, "ZZPRUEBA-D", 1, { reservado: 1, apartado: 10, libre: 0 })]);
  s.escritura = conCandado(s, () => guardada);
  await clic("Guardar cambios");
  await hasta(() => libre("ZZPRUEBA-D").texto === "libre 0", "el saldo que trajo el guardado");
  assert.equal(boton("Guardar cambios"), null, "ya no hay cambios pendientes");

  // Y entonces llega la respuesta vieja («1 libre», de antes del guardado): D ya está guardado, no se pinta.
  s.soltarCatalogo();
  await pausa(60);
  assert.equal(libre("ZZPRUEBA-D").texto, "libre 0");
}, { catalogo: { "ZZPRUEBA-D": [saldo("ENSAYO", 10, 0)] } });
