/**
 * Humo del DOCUMENTO de una orden de venta: se pinta en cada estado y se
 * revisa que diga lo que el contrato de la 0064/0065 pide que diga.
 *
 *     node frontend/components/ordenes/pruebas/documento_pinta.prueba.cjs
 *
 * Por qué existe: `ordenes.prueba.cjs` prueba la lógica pura, pero lo que más
 * cambió con el modelo nuevo es QUÉ SE VE (la bodega por renglón, que una
 * confirmada ya no se edita, el «¿salió?», la entrega por renglón) y eso vive
 * en el JSX. Un `tsc` limpio no dice si el aviso sale, ni si el botón quedó
 * apagado, ni si la pantalla truena con una orden cancelada.
 *
 * Cómo se pinta sin navegador ni dependencias nuevas: con `react-dom/server`,
 * que el frontend ya trae. Dos apaños, a la vista para que nadie los adivine:
 *
 *   · En el servidor los efectos no corren, así que el documento nunca
 *     «cargaría» su orden. Se SIEMBRA el estado: durante el pintado,
 *     `React.useState` devuelve la orden (el primer estado que nace en `null`),
 *     «lista» en vez de «cargando», el formulario de esa orden (el primer
 *     estado con inicializador) y, si se pide, el diálogo abierto (el QUINTO
 *     que nace en `null`). Depende del ORDEN de los `useState` de
 *     `OrdenDocumento`: si alguien los mueve, `pintar` truena con un mensaje
 *     que lo dice (no falla callado).
 *   · `Ventana` (los diálogos) se pinta en un portal tras montarse: aquí se
 *     cambia por un `<div>` para que el cuerpo del diálogo salga en el HTML.
 *
 * Lo que NO prueba: nada que pase al hacer clic o al contestar el servidor
 * (guardar, el 409, la fusión en vivo, el foco, el arrastre de un PDF, la
 * pregunta de saldos al catálogo). Eso sólo existe en un navegador.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const cargar = require("./cargar.cjs");

const FRONTEND = path.resolve(__dirname, "..", "..", "..");
const React = require(path.join(FRONTEND, "node_modules", "react"));
const { renderToStaticMarkup } = require(path.join(FRONTEND, "node_modules", "react-dom", "server"));

const doc = cargar("OrdenDocumento");
// Los diálogos, sin portal: su contenido sale en el HTML dentro de un <div data-ventana>.
require(path.join(FRONTEND, "components", "fulfillment", "ui.tsx")).Ventana =
  ({ children, etiqueta }) => React.createElement("div", { "data-ventana": etiqueta }, children);

// ── Armazones ────────────────────────────────────────────────────────────────

const bodega = (codigo, nombre, fuente, admite_ov) => ({
  codigo, nombre, fuente, admite_ov, surte_ventas: false, cuenta_para_woo: false,
});
const TEXCO = bodega("TEXCO", "TEXCO", "odoo", false);
const TEX3 = bodega("TEX3", "TEXCO III", "kubera", true);
const ENSAYO = bodega("ENSAYO", "Bodega de ensayo", "kubera", true);
const REVISION = bodega("REVISION", "Revisión de devoluciones", "kubera", false);
/** Hoy: sólo ENSAYO admite órdenes. Fase B: también TEX3. */
const HOY = [TEXCO, { ...TEX3, admite_ov: false }, ENSAYO, REVISION];
const FASE_B = [TEXCO, TEX3, ENSAYO, REVISION];

const bandera = (encendido) => ({ encendido, persistido: encendido, actualizado_por: null, motivo: null, actualizado_at: null });
const modulo = (mas = {}) => ({
  ok: true, falta_migracion: false, habilitado: true,
  banderas: { ordenes_venta: bandera(true), ov_generacion_auto: bandera(false) },
  bodegas: HOY, archivos: { disponible: false, motivo: "Falta crear el bucket «ordenes-venta» en Storage." },
  yo: { actor: "ana@kubera.mx", nombre: "Ana", rol: "admin", via: "panel", admin: true, escribe: true },
  ...mas,
});

const TODO = {
  editar: true, confirmar: true, entregar: true, cancelar: true, borrar: true, responder_salio: true,
  salio_tarde: true, mensajes: true, subir_archivo: true, bajar_archivo: true, borrar_archivo: true, porque: {},
};
const NADA = Object.fromEntries(Object.keys(TODO).map((k) => [k, k === "porque" ? {} : false]));

const linea = (n, sku, cantidad, mas = {}) => ({
  id: n, linea: n, sku, titulo: `Producto ${sku}`, imagen: null, cantidad, precio_unitario: 10,
  importe: cantidad * 10, almacen: "ENSAYO", reservado: 0, entregado: null, entregado_at: null,
  entregado_por: null, fisico: 50, apartado: 0, libre: 50, conocido: true, ...mas,
});
const salido = (n) => ({ entregado: n, entregado_at: "2026-10-06T16:00:00Z", entregado_por: "ana@kubera.mx" });

const T = "2026-10-06T15:00:00Z";
const orden = (lineas, mas = {}) => ({
  id: 1, folio: "OV-00012", estado: "borrador", tipo: "venta", rev: 3, cliente: "Temu", canal: "temu",
  mp_canal: "temu", mp_cuenta: "TEMU", mp_orden: "PO-1", full_tienda: null, envio_ref: null, descripcion: null,
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
const confirmada = (lineas, mas = {}) => orden(lineas, {
  estado: "confirmada", confirmada_at: T, confirmada_por: "ana@kubera.mx", confirmada_nombre: "Ana", ...mas,
});
const CANAL_CANCELO = { canal_cancelo_at: "2026-10-06T16:00:00Z", canal_cancelo_ref: "IN_TRANSIT" };

const venta = (mas = {}) => ({
  canal: "temu", cuenta: "TEMU", orden: "PO-1", fecha: null, estado_canal: null, estado_wc: null,
  cancelada: false, es_fulfillment: false, total: 100, comision: 10, neto: 90, guia: null, paqueteria: null,
  entrega_limite: null, piezas: 2, lineas: [{ sku: "AAA-1", cantidad: 2, precio_unitario: 50 }], ov: null, ...mas,
});

/**
 * Pinta el documento y devuelve su HTML. `o = null` es la orden NUEVA (ahí no
 * hay nada que sembrar: nace en blanco o con `prefill`).
 */
function pintar(o, { dialogo = null, mod = modulo(), prefill = null } = {}) {
  const real = React.useState;
  let nulos = 0;
  let inicializadores = 0;
  React.useState = (ini) => {
    if (o) {
      if (ini === null) {
        nulos += 1;
        if (nulos === 1) return real(o);                        // la orden
        if (nulos === 5 && dialogo) return real(dialogo);       // el diálogo abierto
      } else if (ini && typeof ini === "object" && ini.estado === "cargando") {
        return real({ estado: "lista" });
      } else if (typeof ini === "function") {
        inicializadores += 1;
        if (inicializadores === 1) return real(doc.formaDe(o)); // el formulario (base y forma nacen de él)
      }
    }
    return real(ini);
  };
  let html;
  try {
    html = renderToStaticMarkup(React.createElement(doc.OrdenDocumento, {
      refOrden: o ? o.folio : null, prefill, modulo: mod, onCerrar() {}, onCreada() {}, onCambio() {},
    }));
  } finally {
    React.useState = real;
  }
  const guia = "¿cambió el orden de los useState de OrdenDocumento? (ver la cabecera de este archivo)";
  if (o) assert.ok(html.includes("Datos de la orden") && html.includes(o.folio), `la orden sembrada no se pintó: ${guia}`);
  if (dialogo) assert.ok(html.includes("data-ventana"), `el diálogo sembrado no se pintó: ${guia}`);
  return html;
}

/** El texto que se LEE (sin etiquetas ni las opciones de los `<select>`, que no están a la vista). */
const texto = (html) => html.replace(/<option[^>]*>.*?<\/option>/g, " ").replace(/<[^>]+>/g, " ")
  .replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/\s+/g, " ")
  // Quitar una etiqueta deja un espacio antes del signo que la seguía («DELIVERED .»): se junta.
  .replace(/ ([:.,)])/g, "$1");
/** El documento SIN el chat (que tiene su propia caja de texto, y no es captura de la orden). */
const hoja = (html) => html.slice(0, html.indexOf("<aside"));
/** El cuerpo del diálogo abierto. */
const ventana = (html) => texto(html.slice(html.indexOf("data-ventana")));
/** El `<button>` con ese rótulo, o `null`. */
function boton(html, rotulo) {
  const re = new RegExp(`<button[^>]*>(?:(?!</button>).)*?${rotulo}(?:(?!</button>).)*?</button>`);
  return re.exec(html)?.[0] ?? null;
}
const apagado = (b) => / disabled=""/.test(b ?? "");
const titulo = (b) => /title="([^"]*)"/.exec(b ?? "")?.[1] ?? "";
/** El `<select>` de bodega del renglón de ese SKU. */
const selectBodega = (html, sku) =>
  new RegExp(`<select[^>]*aria-label="Bodega de ${sku}"[^>]*>.*?</select>`).exec(html)?.[0] ?? null;
const elegida = (sel) => /<option value="([^"]*)" selected="">/.exec(sel ?? "")?.[1];

// ── La orden nueva ───────────────────────────────────────────────────────────

test("PINTA-1 · la orden nueva: ya no hay «Almacén» de encabezado; la bodega es una columna", () => {
  const html = pintar(null);
  const t = texto(html);
  assert.match(t, /Nueva orden SIN GUARDAR/);
  assert.ok(boton(html, "Crear borrador") && !apagado(boton(html, "Crear borrador")));
  assert.doesNotMatch(t, /Almacén/);
  assert.match(t, /Producto Bodega Cantidad Precio unit\. Importe/);
  // Sin orden no hay columnas de apartado ni de salida, ni nada que confirmar.
  assert.doesNotMatch(t, /Apartado|Salieron|Confirmar y apartar/);
  assert.match(t, /Anótala antes de confirmar: después la orden ya no se modifica/);
});

test("PINTA-2 · nace de una venta: con UNA bodega queda puesta; con dos, se pide elegirla", () => {
  const una = pintar(null, { prefill: venta() });
  assert.equal(elegida(selectBodega(una, "AAA-1")), "ENSAYO");
  assert.match(texto(una), /Salen de ENSAYO, la única bodega con órdenes/);
  // TEX3 apagada no se ofrece; las de Odoo y REVISION, tampoco.
  assert.doesNotMatch(selectBodega(una, "AAA-1"), /TEX3|TEXCO|REVISION/);

  const dos = pintar(null, { prefill: venta(), mod: modulo({ bodegas: FASE_B }) });
  assert.equal(elegida(selectBodega(dos, "AAA-1")), "");
  assert.match(selectBodega(dos, "AAA-1"), /value="TEX3".*value="ENSAYO"/);
  assert.match(texto(dos), /Falta elegir la bodega de cada renglón/);
  assert.match(texto(dos), /elige la bodega/);
});

test("PINTA-3 · la cuenta se ve SIEMPRE que hay canal de venta (no sólo donde hay dos)", () => {
  const cuenta = (html) => /Cuenta<\/span><div[^>]*><select[^>]*>(.*?)<\/select>/.exec(html)?.[1] ?? null;
  // Temu tiene una sola cuenta: el selector está, y ya trae la suya.
  assert.match(cuenta(pintar(null, { prefill: venta() })), /<option value="TEMU" selected="">/);
  // Mercado Libre tiene dos: si la venta no la dice, queda sin elegir y con las dos a la mano.
  const ml = cuenta(pintar(null, { prefill: venta({ canal: "mercado_libre", cuenta: "" }) }));
  assert.match(ml, /<option value="" selected="">/);
  assert.match(ml, /Kubera.*San Corpe/);
  // Sin canal de venta no hay cuenta que pedir.
  assert.equal(cuenta(pintar(null)), null);
});

// ── El borrador ──────────────────────────────────────────────────────────────

test("PINTA-4 · borrador: «Confirmar y apartar», y el saldo de cada renglón EN SU BODEGA", () => {
  const html = pintar(orden([
    linea(1, "AAA-1", 2),                                                          // alcanza
    linea(2, "BBB-2", 60),                                                         // pide 60 y hay 50
    linea(3, "CCC-3", 1, { almacen: null, fisico: null, apartado: null, libre: null }),   // sin bodega
    linea(4, "DDD-4", 1, { fisico: null, apartado: null, libre: null, conocido: false }), // sin fila de saldo
  ]));
  const t = texto(html);
  assert.ok(boton(html, "Confirmar y apartar"));
  // Lo del modelo viejo no está ni apagado.
  assert.doesNotMatch(t, /Confirmar y reservar|Reintentar reserva|Regresar a borrador|Disponible|Reservado/);
  const saldoDe = (sku) => /<div class="mt-1[^"]*">(<span.*?<\/span>)<\/div>/.exec(html.slice(html.indexOf(`Bodega de ${sku}`)))?.[1] ?? "";
  assert.match(saldoDe("AAA-1"), /text-slate-500[^>]*>libre 50</);
  assert.match(saldoDe("BBB-2"), /text-amber-700[^>]*no alcanza para las 60 piezas[^>]*>libre 50</, "en ámbar si no alcanza");
  assert.match(saldoDe("CCC-3"), /text-amber-700[^>]*>elige la bodega</);
  assert.match(saldoDe("DDD-4"), /No es un cero: no se sabe[^>]*>sin dato</, "sin fila de saldo NO es «libre 0»");
  assert.doesNotMatch(saldoDe("DDD-4"), /libre 0/);
  assert.match(t, /SKU fuera del catálogo/);
  // En borrador todavía no hay apartado ni salida que enseñar.
  assert.doesNotMatch(t, /Apartado|Salieron/);
});

test("PINTA-5 · borrador de quien no puede escribir: sólo lectura, con el porqué del backend", () => {
  const html = pintar(orden([linea(1, "AAA-1", 2)], {
    permisos: { ...NADA, bajar_archivo: true, porque: { editar: "Tu usuario es de sólo lectura." } },
  }), { mod: modulo({ yo: { actor: "luis@kubera.mx", nombre: "Luis", rol: "lector", via: "panel", admin: false, escribe: false } }) });
  assert.match(texto(html), /Tu usuario es de sólo lectura\./);
  assert.equal(selectBodega(html, "AAA-1"), null);
  assert.doesNotMatch(html, /type="number"/);
  assert.equal(boton(html, "Traer venta"), null);
});

// ── Fuera de borrador: nada se edita ─────────────────────────────────────────

const NOTA = /Una orden confirmada ya no se modifica: si tiene un error, un administrador la borra y se captura de nuevo\./;

test("PINTA-6 · confirmada: sólo lectura para TODOS (también admin), con su nota y sus tres columnas", () => {
  // El permiso de editar llega encendido a propósito: la pantalla no se fía de él fuera de borrador.
  const html = pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })]));
  const t = texto(html);
  assert.match(t, NOTA);
  assert.match(t, /CONFIRMADA Stock apartado/);
  assert.match(t, /Producto Bodega Cantidad Apartado Salieron Precio unit\. Importe/);
  assert.match(t, /ENSAYO libre 50 2 2 — \$10\.00/, "cantidad 2 · apartado 2 · todavía no sale");
  // Ni un control de captura: ni cantidades, ni bodegas, ni buscador, ni «Traer venta», ni «Guardar».
  assert.doesNotMatch(hoja(html), /type="number"|type="datetime-local"|<textarea|role="combobox"/);
  assert.equal(selectBodega(html, "AAA-1"), null);
  assert.equal(boton(html, "Traer venta"), null);
  assert.equal(boton(html, "Guardar cambios"), null);
  assert.doesNotMatch(t, /Regresar a borrador|Reintentar reserva/);
  assert.ok(boton(html, "Marcar DELIVERED") && !apagado(boton(html, "Marcar DELIVERED")));
});

test("PINTA-7 · entrega parcial: sigue CONFIRMADA, con su chip, y lo que ya salió dice cuánto", () => {
  const html = pintar(confirmada([linea(1, "AAA-1", 2, salido(1)), linea(2, "BBB-2", 3, { reservado: 3 })]));
  const t = texto(html);
  assert.match(t, /CONFIRMADA Entrega parcial/);
  assert.match(t, /AAA-1 Producto AAA-1 ENSAYO libre 50 2 0 1 de 2/, "salió 1 de 2: su apartado quedó en 0");
  assert.match(t, /BBB-2 Producto BBB-2 ENSAYO libre 50 3 3 —/, "éste sigue apartado y sin salir");
  // Lo que falta todavía se puede entregar.
  assert.ok(!apagado(boton(html, "Marcar DELIVERED")));
});

test("PINTA-8 · cancelada, entregada y borrada: tampoco se editan, y no ofrecen lo que ya no existe", () => {
  const cancelada = pintar(orden([linea(1, "AAA-1", 2)], {
    estado: "cancelada", confirmada_at: T, confirmada_por: "ana@kubera.mx", cancelada_at: T,
    cancelada_por: "ana@kubera.mx", cancelada_origen: "manual", cancelada_motivo: "duplicada",
  }));
  assert.match(texto(cancelada), /CANCELADO Apartado liberado/);
  assert.match(texto(cancelada), /Una orden cancelada ya no se modifica/);
  assert.match(texto(cancelada), /Cancelada por ana \(a mano\).*motivo: «duplicada»/);
  assert.doesNotMatch(cancelada, /type="number"/);

  const entregada = pintar(orden([linea(1, "AAA-1", 2, salido(2))], {
    estado: "entregada", confirmada_at: T, confirmada_por: "ana@kubera.mx", entregada_at: T, entregada_por: "ana@kubera.mx",
  }));
  assert.match(texto(entregada), /DELIVERED Surtida/);
  // Lo que ya salió no se arregla con «borrar y capturar de nuevo» (saldría dos veces).
  assert.match(texto(entregada), /Esta orden ya salió de la bodega: no se modifica\. Borrarla no regresa las piezas al saldo/);
  assert.doesNotMatch(texto(entregada), NOTA);
  assert.match(texto(entregada), /ENSAYO libre 50 2 0 2 \$10\.00/, "salieron las 2");
  // Una entregada ya no se cancela a mano: eso lo hace el canal.
  assert.equal(boton(entregada, "Cancelar"), null);
  // Sin devolución registrada, una entregada no habla de devoluciones.
  assert.doesNotMatch(texto(entregada), /Devolución/);

  const borrada = pintar(confirmada([linea(1, "AAA-1", 2)], {
    borrada_at: T, borrada_por: "ana@kubera.mx", borrada_motivo: "capturada con error", permisos: NADA,
  }));
  assert.match(texto(borrada), /BORRADA Orden borrada por ana .* Motivo: «capturada con error»/);
  assert.match(texto(borrada), /Apartado liberado/);
  assert.equal(boton(borrada, "Marcar DELIVERED"), null);
  assert.doesNotMatch(texto(borrada), NOTA, "de la borrada ya habla su propio aviso");
});

test("PINTA-9 · un envío a FULL se enseña de sólo lectura, con su etiqueta y sus dos campos", () => {
  const html = pintar(orden([linea(1, "AAA-1", 2)], {
    tipo: "full", canal: "mercado_libre", mp_canal: null, mp_cuenta: null, mp_orden: null,
    full_tienda: "meli:Kubera", envio_ref: "ENV-00077",
  }));
  const t = texto(html);
  assert.match(t, /BORRADOR Sin apartar Envío a FULL/);
  assert.match(t, /Es un envío a FULL: se captura en Crear FULL y aquí sólo se consulta/);
  assert.match(html, /Tienda de FULL<\/span><div[^>]*><input[^>]*value="meli:Kubera"/);
  assert.match(html, /Número de envío<\/span><div[^>]*><input[^>]*value="ENV-00077"/);
  // Aunque sea borrador y el permiso diga que sí: aquí no se captura.
  assert.doesNotMatch(html, /type="number"|role="combobox"/);
  assert.equal(selectBodega(html, "AAA-1"), null);
});

// ── «¿Salió?» ────────────────────────────────────────────────────────────────

test("PINTA-10 · el canal canceló con el paquete en camino: la pregunta, sus dos botones y DELIVERED apagado", () => {
  const html = pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })], CANAL_CANCELO));
  const t = texto(html);
  assert.match(t, /El canal canceló esta venta cuando el paquete ya iba en camino \(estado del canal: IN_TRANSIT\)\. ¿El paquete salió de la bodega\?/);
  assert.ok(boton(html, "Sí salió") && boton(html, "No salió"));
  assert.match(html, /ring-2 ring-amber-300/, "el aviso va destacado");
  const delivered = boton(html, "Marcar DELIVERED");
  assert.ok(apagado(delivered), "con la marca puesta no se marca DELIVERED");
  assert.match(titulo(delivered), /contestar si el paquete salió/);

  // Quien no puede contestar ve la pregunta, no los botones, y por qué.
  const lector = pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })], {
    ...CANAL_CANCELO,
    permisos: { ...TODO, responder_salio: false, entregar: false,
                porque: { responder_salio: "Sólo Bodega contesta si salió.", entregar: "Falta contestar el «¿salió?»." } },
  }));
  assert.match(texto(lector), /¿El paquete salió de la bodega\?/);
  assert.equal(boton(lector, "Sí salió"), null);
  assert.match(texto(lector), /Sólo Bodega contesta si salió\./);
  assert.equal(titulo(boton(lector, "Marcar DELIVERED")), "Falta contestar el «¿salió?».", "el porqué del backend manda");

  // Ya contestada (la marca se queda de recuerdo): no se vuelve a preguntar.
  const resuelta = pintar(orden([linea(1, "AAA-1", 2)], {
    estado: "cancelada", confirmada_at: T, confirmada_por: "ana@kubera.mx", cancelada_at: T,
    cancelada_por: "ana@kubera.mx", cancelada_origen: "marketplace", cancelada_motivo: "no salió", ...CANAL_CANCELO,
  }));
  assert.doesNotMatch(texto(resuelta), /¿El paquete salió de la bodega\?/);
  assert.match(texto(resuelta), /El canal avisó la cancelación con el paquete en camino \(estado del canal: IN_TRANSIT\)/);
});

// ── La devolución: sólo se muestra ───────────────────────────────────────────

test("PINTA-11 · la devolución se MUESTRA con su nota; no hay botones para moverla", () => {
  const base = { confirmada_at: T, confirmada_por: "ana@kubera.mx", entregada_at: T, entregada_por: "ana@kubera.mx" };
  const cancelada = (devolucion_estado) => pintar(orden([linea(1, "AAA-1", 2, salido(2))], {
    ...base, estado: "entregada_cancelada", cancelada_at: T, cancelada_por: "automatico",
    cancelada_origen: "marketplace", cancelada_motivo: "el canal canceló", devolucion_estado,
  }));
  for (const estado of ["pendiente", "recibida", "cerrada"]) {
    const html = cancelada(estado);
    assert.match(texto(html), new RegExp(`Devolución: ${estado}\\. El proceso de devoluciones se define aparte\\.`));
    assert.equal(boton(html, "Devolución recibida"), null);
    assert.equal(boton(html, "Cerrar devolución"), null);
  }
  // Mientras el producto no regresa, grita; cerrada, ya no.
  assert.match(cancelada("pendiente"), /ring-2 ring-amber-300/);
  assert.doesNotMatch(cancelada("cerrada"), /ring-2 ring-amber-300/);
  assert.match(texto(cancelada(null)), /Devolución: sin registrar\./);
  // También en una ENTREGADA (sin cancelar) que tiene devolución.
  const entregada = pintar(orden([linea(1, "AAA-1", 2, salido(2))], { ...base, estado: "entregada", devolucion_estado: "recibida" }));
  assert.match(texto(entregada), /Devolución El producto ya regresó al almacén\. Devolución: recibida\. El proceso de devoluciones se define aparte\./);
});

// ── Los diálogos ─────────────────────────────────────────────────────────────

test("PINTA-12 · «Marcar DELIVERED» abre los renglones PENDIENTES, con todas sus piezas por omisión", () => {
  const html = pintar(confirmada([linea(1, "AAA-1", 2, salido(2)), linea(2, "BBB-2", 3, { reservado: 3 }),
                                  linea(3, "CCC-3", 1, { reservado: 1 })]), { dialogo: { tipo: "entregar" } });
  const v = ventana(html);
  assert.match(v, /Marcar OV-00012 como DELIVERED/);
  assert.match(v, /Lo que no salga se suelta y vuelve a quedar libre en la bodega\./);
  // Sólo los que faltan: el que ya salió no se vuelve a tocar.
  assert.doesNotMatch(html.slice(html.indexOf("data-ventana")), /Piezas de AAA-1 que salieron/);
  for (const [sku, n] of [["BBB-2", 3], ["CCC-3", 1]]) {
    assert.match(html, new RegExp(`<input type="number" min="0" max="${n}"[^>]*aria-label="Piezas de ${sku} que salieron"[^>]*value="${n}"`));
    assert.match(html, new RegExp(`<input type="checkbox"[^>]*aria-label="${sku} sale ahora"[^>]*checked=""`));
  }
  assert.match(v, /ya salieron 2 piezas en una entrega anterior/);
  assert.match(v, /Salen 4 piezas: la orden queda DELIVERED\./);
  assert.ok(!apagado(boton(html, "Sí, marcar DELIVERED")));
});

test("PINTA-13 · cancelar pide motivo (5 si estuvo confirmada); borrar, 10; y el botón no se enciende antes", () => {
  const o = confirmada([linea(1, "AAA-1", 2, { reservado: 2 })]);
  const cancelar = pintar(o, { dialogo: { tipo: "cancelar" } });
  assert.match(ventana(cancelar), /Cancelar OV-00012 Se cancela la orden y se suelta su apartado \(2 piezas vuelven a quedar libres en su bodega\)/);
  assert.match(ventana(cancelar), /El motivo lleva al menos 5 caracteres\. Faltan 5\./);
  assert.ok(apagado(boton(cancelar, "Cancelar la orden")), "sin motivo no se manda");
  // Un borrador también pide motivo, sin el mínimo de la confirmada.
  const borrador = pintar(orden([linea(1, "AAA-1", 2)]), { dialogo: { tipo: "cancelar" } });
  assert.match(ventana(borrador), /Se cancela el borrador\. No tenía stock apartado\./);
  assert.doesNotMatch(ventana(borrador), /al menos 5 caracteres/);
  assert.ok(apagado(boton(borrador, "Cancelar la orden")));
  // Con piezas ya entregadas, cancelar es DELIVERED but CANCELLED, y se dice antes.
  const parcial = pintar(confirmada([linea(1, "AAA-1", 2, salido(2)), linea(2, "BBB-2", 3, { reservado: 3 })]),
                         { dialogo: { tipo: "cancelar" } });
  assert.match(ventana(parcial), /ya salieron 2 piezas\. Si la cancelas queda como DELIVERED but CANCELLED: lo que seguía apartado \(3 piezas\) se suelta/);
  assert.ok(boton(parcial, "Cancelar y pedir devolución"));

  const borrar = pintar(o, { dialogo: { tipo: "borrar" } });
  assert.match(ventana(borrar), /Borrar OV-00012 .* no se elimina: .* Se suelta su apartado \(2 piezas\)\./);
  assert.match(ventana(borrar), /El motivo lleva al menos 10 caracteres\. Faltan 10\./);
  assert.ok(apagado(boton(borrar, "Borrar la orden")));
  assert.match(titulo(boton(borrar, "Borrar la orden")), /al menos 10 caracteres/);
});

test("PINTA-14 · «Sí salió», «No salió» y «Salió tarde» se confirman diciendo la consecuencia", () => {
  const marcada = confirmada([linea(1, "AAA-1", 2, { reservado: 2 })], CANAL_CANCELO);
  const si = ventana(pintar(marcada, { dialogo: { tipo: "salio_si" } }));
  assert.match(si, /SÍ salió La orden pasa a DELIVERED but CANCELLED: sus piezas apartadas \(2\) se dan por salidas —bajan del físico de la bodega— y se queda esperando la devolución\. No se puede deshacer\./);
  const no = ventana(pintar(marcada, { dialogo: { tipo: "salio_no" } }));
  assert.match(no, /NO salió La orden se cancela \(la canceló el marketplace\) y su apartado se suelta: 2 piezas vuelven a quedar libres en su bodega\./);

  const cancelada = orden([linea(1, "AAA-1", 2)], {
    estado: "cancelada", confirmada_at: T, confirmada_por: "ana@kubera.mx", cancelada_at: T,
    cancelada_por: "ana@kubera.mx", cancelada_origen: "manual", cancelada_motivo: "duplicada",
  });
  const tarde = pintar(cancelada, { dialogo: { tipo: "salio_tarde" } });
  assert.match(ventana(tarde), /el paquete sí había salido .* pasa a DELIVERED but CANCELLED: se anota la salida de sus 2 piezas — baja el físico de la bodega — y se queda esperando la devolución\. No se puede deshacer\./);
  assert.ok(boton(tarde, "Sí, registrar la salida"));
  // Es discreta: vive en el menú «…», no en la barra.
  assert.equal(boton(pintar(cancelada), "Salió tarde"), null);
});

// ── PDF ──────────────────────────────────────────────────────────────────────

test("PINTA-15 · PDF: el aviso fijo, la zona apagada sin bucket, y el tipo de cada archivo", () => {
  const AVISO = /No subas guías con la dirección del comprador: no se guardan aquí\./;
  const sinBucket = pintar(orden([linea(1, "AAA-1", 2)]));
  assert.match(texto(sinBucket), AVISO);
  assert.match(texto(sinBucket), /No se pueden adjuntar PDF Falta crear el bucket «ordenes-venta» en Storage\./);
  assert.doesNotMatch(sinBucket, /type="file"/, "apagada: ni siquiera hay por dónde elegir un archivo");

  const archivo = { id: 5, orden_id: 1, tipo: "factura", nombre: "F-0001.pdf", bytes: 2048, sha256: "a".repeat(64),
                    subido_at: T, subido_por: "ana@kubera.mx", subido_nombre: "Ana" };
  const conBucket = pintar(orden([linea(1, "AAA-1", 2)], { archivos: [archivo] }),
                           { mod: modulo({ archivos: { disponible: true, motivo: null } }) });
  assert.match(texto(conBucket), AVISO);
  assert.match(texto(conBucket), /Factura F-0001\.pdf 2 KB · Ana/, "la lista dice qué es cada PDF");
  // El tipo se elige antes de subir: los tres, y NINGUNO preelegido.
  const radios = Array.from(conBucket.matchAll(/<button[^>]*role="radio" aria-checked="(true|false)"[^>]*>([^<]*)</g),
                            (m) => `${m[2]}=${m[1]}`);
  assert.deepEqual(radios, ["Comprobante=false", "Factura=false", "Envío a FULL=false"]);
  assert.match(texto(conBucket), /Elige arriba qué es el PDF y después arrástralo aquí\./);

  // Con bucket pero sin permiso: apagada, y el porqué es el de la persona.
  const sinPermiso = pintar(orden([linea(1, "AAA-1", 2)], {
    permisos: { ...TODO, subir_archivo: false, porque: { subir_archivo: "Tu usuario es de sólo lectura." } },
  }), { mod: modulo({ archivos: { disponible: true, motivo: null } }) });
  assert.match(texto(sinPermiso), /No se pueden adjuntar PDF Tu usuario es de sólo lectura\./);
  // La orden nueva tampoco promete «guarda y adjunta» si no hay dónde.
  assert.match(texto(pintar(null)), /Todavía no se pueden adjuntar PDF Falta crear el bucket/);
});

// ── Modo prueba y sin bodegas ────────────────────────────────────────────────

test("PINTA-16 · con la bandera apagada se avisa «modo prueba»; sin bodegas, que no se podrá confirmar", () => {
  const apagada = modulo({ habilitado: false, banderas: { ordenes_venta: bandera(false), ov_generacion_auto: bandera(false) } });
  assert.match(texto(pintar(null, { mod: apagada })), /Modo prueba: .* hasta que se encienda la bandera ordenes_venta/);
  assert.doesNotMatch(texto(pintar(null)), /Modo prueba/);
  const sinBodegas = modulo({ bodegas: [TEXCO, REVISION] });
  assert.match(texto(pintar(null, { mod: sinBodegas })), /No hay bodega para órdenes\./);
  // Un backend que todavía no manda el catálogo no truena la pantalla.
  assert.match(texto(pintar(null, { mod: modulo({ bodegas: undefined }) })), /No hay bodega para órdenes\./);
});

// ── Segunda revisión (6-oct-2026) ─────────────────────────────────────────────

test("REV2 FE-01 · borrar una orden que YA SALIÓ no promete «se captura de nuevo», y avisa de la devolución", () => {
  const entregada = orden([linea(1, "AAA-1", 4, salido(4))], {
    estado: "entregada", confirmada_at: T, confirmada_por: "ana@kubera.mx", entregada_at: T, entregada_por: "ana@kubera.mx",
  });
  const v = ventana(pintar(entregada, { dialogo: { tipo: "borrar" } }));
  assert.match(v, /Lo que ya salió de la bodega NO regresa al saldo \(4 piezas\): borrarla no deshace la salida, y no hay que capturarla de nuevo/);
  assert.doesNotMatch(v, /se borra y se captura de nuevo|deja de contarse/);

  const porDevolver = ventana(pintar({ ...entregada, estado: "entregada_cancelada", devolucion_estado: "pendiente" },
                                     { dialogo: { tipo: "borrar" } }));
  assert.match(porDevolver, /NO regresa al saldo/);
  assert.match(porDevolver, /Su devolución está pendiente: al borrarla deja de contarse en «Por devolver»/);
  // La confirmada que no ha salido conserva su texto (PINTA-13 lo revisa completo).
  const sinSalir = ventana(pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })]), { dialogo: { tipo: "borrar" } }));
  assert.match(sinSalir, /se borra y se captura de nuevo/);
  assert.doesNotMatch(sinSalir, /NO regresa al saldo/);
});

test("REV2 FE-03 · con la marca del canal no hay «Cancelar orden» en la barra, ni para el admin", () => {
  const html = pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })], CANAL_CANCELO));
  assert.equal(boton(html, "Cancelar orden"), null, "queda en el menú «…», apagado");
  assert.ok(boton(pintar(confirmada([linea(1, "AAA-1", 2, { reservado: 2 })])), "Cancelar orden"), "sin la marca, sí");
});

test("REV2 FE-04 · «No salió» y «Salió tarde» dicen lo que el servidor hace tras una entrega parcial", () => {
  // «No salió» con 2 piezas ya salidas: no queda cancelada, queda DELIVERED but CANCELLED.
  const parcial = confirmada([linea(1, "AAA-1", 2, salido(2)), linea(2, "BBB-2", 3, { reservado: 3 })], CANAL_CANCELO);
  const no = ventana(pintar(parcial, { dialogo: { tipo: "salio_no" } }));
  assert.match(no, /ya salieron 2 piezas en una entrega anterior: queda como DELIVERED but CANCELLED \(la canceló el marketplace\)\. Lo que seguía apartado \(3 piezas\) se suelta y lo que salió tiene que regresar/);
  assert.doesNotMatch(no, /La orden se cancela/);

  // «Salió tarde»: 3 + 2 piezas, el primer renglón se cerró con 0 → sólo salen las 2 pendientes.
  const cancelada = orden([linea(1, "AAA-1", 3, salido(0)), linea(2, "BBB-2", 2)], {
    estado: "cancelada", confirmada_at: T, confirmada_por: "ana@kubera.mx", cancelada_at: T,
    cancelada_por: "ana@kubera.mx", cancelada_origen: "manual", cancelada_motivo: "duplicada",
  });
  const tarde = ventana(pintar(cancelada, { dialogo: { tipo: "salio_tarde" } }));
  assert.match(tarde, /se anota la salida de sus 2 piezas —/);
  assert.doesNotMatch(tarde, /sus 5 piezas/);
});

test("REV2 FE-05 · con un `/estado` que no se leyó no se afirma «modo prueba» ni «no hay bodega», y la bodega guardada se ve", () => {
  const sinLeer = modulo({ ok: false, habilitado: false, bodegas: [],
                           banderas: { ordenes_venta: bandera(false), ov_generacion_auto: bandera(false) } });
  const html = pintar(orden([linea(1, "AAA-1", 2)]), { mod: sinLeer });
  assert.doesNotMatch(texto(html), /Modo prueba|No hay bodega para órdenes/);
  // El renglón tiene ENSAYO guardada: el select la enseña, no «—».
  assert.equal(elegida(selectBodega(html, "AAA-1")), "ENSAYO");
  // Y cuando el catálogo SÍ se leyó y no trae ninguna, se sigue diciendo (PINTA-16).
  assert.match(texto(pintar(orden([linea(1, "AAA-1", 2)]), { mod: modulo({ bodegas: [TEXCO, REVISION] }) })),
               /No hay bodega para órdenes\./);
});

test("REV2 FE-08 · «Confirmar y apartar» pregunta antes: qué se aparta, dónde, la guía y que ya no se edita", () => {
  const o = orden([linea(1, "AAA-1", 2), linea(2, "BBB-2", 3, { almacen: "TEX3" })]);
  const html = pintar(o, { dialogo: { tipo: "confirmar" } });
  const v = ventana(html);
  assert.match(v, /Confirmar OV-00012 Se apartan 5 piezas en 2 renglones \(ENSAYO · TEX3\), todo o nada\. Guía: JT1\. Después de confirmar la orden ya no se puede editar \(ni la guía\) ?; deshacerlo requiere a un administrador\./);
  // Sin guía se dice, que es justo lo que ya no se va a poder anotar.
  assert.match(ventana(pintar({ ...o, guia: null }, { dialogo: { tipo: "confirmar" } })), /No tiene guía capturada\./);
  // El botón del diálogo es el segundo «Confirmar y apartar» (el primero es el de la barra).
  assert.equal(html.match(/Confirmar y apartar/g).length, 2);
});
