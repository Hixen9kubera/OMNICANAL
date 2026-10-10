/**
 * Pruebas de la lógica PURA del CHAT y de la TRAZA de una orden de venta, contra
 * el modelo de la 0064/0065 (6-oct-2026).
 *
 *     node frontend/components/ordenes/pruebas/chat_traza.prueba.cjs
 *
 * Van aparte de `ordenes.prueba.cjs` (que prueba el documento, el buscador y las
 * piezas compartidas) porque cuidan otra cosa: que lo que la pantalla DICE de un
 * movimiento coincida con lo que la base permite que pase. Tres maneras en que
 * eso se rompe callado, y que aquí truenan:
 *
 *   · CAT · El catálogo de eventos es un CHECK de la migración. Si la base
 *     aprende un evento y la pantalla no, ese movimiento saldría con un icono
 *     genérico y nadie lo notaría. Por eso la lista esperada NO está escrita
 *     aquí: se LEE del .sql —de la ÚLTIMA migración que define el CHECK—, que
 *     es la verdad. (La 0064 lo creó; la 0071 le agregó `editada`.)
 *   · TAB · La mini-tabla de cada movimiento rotula según el evento. Con el
 *     modelo viejo decía «N de M reservadas»; ahora se aparta todo o nada y se
 *     entrega con 0..cantidad piezas por renglón. Un «salieron 0 de 3» no es lo
 *     mismo que «sin dato», y ninguno de los dos es un 3.
 *   · ED · La corrección de una CONFIRMADA (0071) deja el mensaje `editada`: es
 *     el rastro de quién cambió una orden que ya tenía stock apartado. Si el
 *     chat lo pinta a medias —sin el antes y el después, o sin lo que se movió
 *     el apartado—, ese rastro existe en la base pero nadie lo lee. Y si lo
 *     pinta de más —«Quitado: X», tachado, de un renglón que sólo cambió de
 *     bodega—, la bitácora dice que se quitó un producto que sigue en la orden.
 *   · TRZ · El riel pinta una entrega a medias y la alerta de «el canal
 *     canceló». Las dos salen de `planDe`; si se equivoca, la lista entera
 *     enseña órdenes que piden respuesta como si fueran normales (o al revés).
 *
 * Lo que NO se prueba aquí: el dibujo (lienzo y SVG) y el bucle del worker, que
 * sólo existen en un navegador. Del chat se pinta UN movimiento suelto (con
 * `react-dom/server`, que el frontend ya trae); el hilo en vivo, no.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const cargar = require("./cargar.cjs");

const chat = cargar("ChatOrden");
const traza = cargar("Traza");

const FRONTEND = path.resolve(__dirname, "..", "..", "..");
const React = require(path.join(FRONTEND, "node_modules", "react"));
const { renderToStaticMarkup } = require(path.join(FRONTEND, "node_modules", "react-dom", "server"));

const RAIZ = path.resolve(__dirname, "..", "..", "..", "..");
const MIGRACIONES = path.join(RAIZ, "supabase", "migrations");
const CHECK_EVENTOS = /constraint\s+ov_mensajes_evento_chk\s+check\s*\(\s*evento\s+is\s+null\s+or\s+evento\s+in\s*\(([^)]*)\)/i;

/** Los eventos del CHECK `ov_mensajes_evento_chk` tal como los define UNA migración (`null` si no lo define). */
function eventosDe(archivo) {
  const m = CHECK_EVENTOS.exec(fs.readFileSync(path.join(MIGRACIONES, archivo), "utf8"));
  return m ? Array.from(m[1].matchAll(/'([a-z_]+)'/g), (x) => x[1]).sort() : null;
}

/**
 * Los eventos que admite HOY `ventas.ov_mensajes.evento`: los de la última
 * migración que define el CHECK (se aplican en orden de nombre). Así, la
 * próxima que agregue un evento hace tronar CAT-1 sin que nadie toque esto.
 */
function catalogoDeLaBase() {
  const definen = fs.readdirSync(MIGRACIONES).filter((f) => f.endsWith(".sql")).sort()
    .map((f) => ({ archivo: f, eventos: eventosDe(f) })).filter((x) => x.eventos);
  assert.ok(definen.length, "ninguna migración define el CHECK ov_mensajes_evento_chk");
  return definen[definen.length - 1];
}

// ── CAT · el catálogo de eventos ─────────────────────────────────────────────

test("CAT-1 · EVENTOS cubre EXACTAMENTE el catálogo del CHECK de la base (ni falta ni sobra)", () => {
  const { archivo, eventos: base } = catalogoDeLaBase();
  assert.deepEqual(Object.keys(chat.EVENTOS).sort(), base, `contra el CHECK de ${archivo}`);
  // La 0064 lo creó con 15; la 0071 (editar una confirmada) sólo le agregó `editada`.
  const de0064 = eventosDe("0064_ops_ordenes_venta.sql");
  assert.equal(de0064.length, 15, "el CHECK de la 0064 trae 15 eventos");
  assert.deepEqual(eventosDe("0071_ov_editar_confirmada.sql"), [...de0064, "editada"].sort());
  assert.ok(chat.EVENTOS.editada, "la corrección de una confirmada tiene su icono");
  // Los del modelo del 2-oct ya no existen: no deben quedar pintables. (`editada`
  // volvió con la 0071, y es otra cosa: la corrección de una orden CONFIRMADA.)
  for (const viejo of ["reserva", "regresada", "borrada", "archivo", "archivo_borrado", "devolucion",
                       "entregada_cancelada"]) {
    assert.equal(chat.EVENTOS[viejo], undefined, `«${viejo}» era del modelo viejo`);
  }
});

test("CAT-2 · cada evento trae icono, tono y rótulo; los dos que piden respuesta van en ámbar", () => {
  for (const [evento, e] of Object.entries(chat.EVENTOS)) {
    assert.ok(e.icono && e.tono && e.rotulo, `«${evento}» trae icono, tono y rótulo`);
  }
  const { no_alcanzo: falta, canal_cancelo: canal } = chat.EVENTOS;
  assert.match(falta.rotulo, /no alcanzó el stock/i);
  assert.match(canal.rotulo, /el canal canceló: ¿salió\?/i);
  for (const e of [falta, canal]) {
    assert.match(e.tono, /amber/);
    assert.equal(e.llama, true, "el renglón entero se pinta en ámbar");
  }
  // Y NINGÚN otro «llama»: si todo grita, nada se oye.
  const llaman = Object.entries(chat.EVENTOS).filter(([, e]) => e.llama).map(([k]) => k).sort();
  assert.deepEqual(llaman, ["canal_cancelo", "no_alcanzo"]);
  // Entregar a medias y entregar todo no se confunden (ni por color ni por icono).
  assert.notEqual(chat.EVENTOS.entregada_parcial.icono, chat.EVENTOS.entregada.icono);
  assert.notEqual(chat.EVENTOS.entregada_parcial.tono, chat.EVENTOS.entregada.tono);
});

test("CAT-3 · un mensaje del sistema SIN evento es un aviso neutro; el de un PDF se reconoce por sus datos", () => {
  const neutro = chat.eventoDe({ evento: null, datos: null });
  assert.equal(neutro.rotulo, "Aviso");
  assert.match(neutro.tono, /slate/);
  assert.ok(!neutro.llama);
  // Las dos formas que deja el servicio: al adjuntar (subir_archivo) y al quitar (borrar_archivo).
  const adjunto = chat.eventoDe({ evento: null, datos: { nombre: "comprobante.pdf", tipo: "comprobante", bytes: 1234,
                                                         sha256: "0".repeat(64) } });
  const quitado = chat.eventoDe({ evento: null, datos: { archivo_id: 7, nombre: "comprobante.pdf" } });
  for (const pdf of [adjunto, quitado]) {
    assert.match(pdf.rotulo, /PDF/);
    assert.notEqual(pdf.icono, neutro.icono);
    assert.ok(!pdf.llama, "un PDF no le pide nada a nadie");
  }
  // `datos` con otra cosa no lo vuelve «de PDF» (ni el `nombre` solo).
  assert.equal(chat.eventoDe({ evento: null, datos: { op: "x", nombre: "algo" } }).rotulo, "Aviso");
  // Con evento, manda el evento (aunque traiga datos de archivo).
  assert.equal(chat.eventoDe({ evento: "creada", datos: { archivo_id: 7 } }), chat.EVENTOS.creada);
  // Un evento que esta pantalla no conoce (base más nueva que el código) no truena ni se esconde.
  assert.equal(chat.eventoDe({ evento: "algo_nuevo", datos: null }).rotulo, "Movimiento");
});

// ── TAB · la mini-tabla de `datos.lineas` ────────────────────────────────────
//
// Los renglones de estas pruebas tienen la forma EXACTA que escribe cada
// transición de `backend/services/ordenes_venta.py` (se nombra cuál): si el
// servicio cambia una llave, la prueba se cambia junto con él.

/** Lo que pinta la mini-tabla para los `datos` de un mensaje: «SKU [BODEGA] texto». */
const tabla = (evento, lineas) => chat.lineasDe({ op: "x", lineas })
  .map((l) => `${l.sku}${l.almacen ? ` [${l.almacen}]` : ""} ${chat.detalleLinea(evento, l).texto}`);
const tonos = (evento, lineas) => chat.lineasDe({ lineas }).map((l) => chat.detalleLinea(evento, l).tono);

const VERDE = "text-emerald-700";
const AMBAR = "text-amber-700";
const GRIS = "text-slate-500";

test("TAB-1 · se leen las llaves de la bitácora: bodega por renglón, y `null` no es 0", () => {
  const lineas = chat.lineasDe({
    op: "x",
    lineas: [
      // _lineas_bitacora(..., reservado=True): confirmar.
      { sku: "ZZPRUEBA-1", titulo: "  Caja de prueba ", cantidad: "3", precio_unitario: 10.5, almacen: "ENSAYO", reservado: 3 },
      // entregar: lo que salió de ese renglón.
      { sku: "ZZPRUEBA-2", titulo: "", cantidad: 2, entregado: 0, almacen: null, reservado: 0 },
      // no_alcanzo: `libre` viene, y es null (el SKU no tiene fila de saldo en esa bodega).
      { sku: "ZZPRUEBA-3", titulo: null, cantidad: 1, almacen: "ENSAYO", libre: null, reservado: 0 },
      { sku: "", cantidad: 1 },      // sin SKU: no es un renglón
      null, "basura", 7,
    ],
  });
  assert.deepEqual(lineas, [
    { sku: "ZZPRUEBA-1", titulo: "Caja de prueba", cantidad: 3, almacen: "ENSAYO", reservado: 3, entregado: null,
      libre: null, sinSaldo: false },
    { sku: "ZZPRUEBA-2", titulo: null, cantidad: 2, almacen: null, reservado: 0, entregado: 0,
      libre: null, sinSaldo: false },
    { sku: "ZZPRUEBA-3", titulo: null, cantidad: 1, almacen: "ENSAYO", reservado: 0, entregado: null,
      libre: null, sinSaldo: true },
  ]);
  // Sin lista (o con otra cosa en su lugar) no hay mini-tabla.
  assert.deepEqual(chat.lineasDe(null), []);
  assert.deepEqual(chat.lineasDe({ lineas: "3 renglones" }), []);
  assert.deepEqual(chat.lineasDe({ op: "x", ref_canal: "IN_TRANSIT", motivo: null }), [], "canal_cancelo no trae renglones");
});

test("TAB-2 · crear y guardar un borrador dicen sus piezas; confirmar dice «apartadas» (todo o nada)", () => {
  // crear_borrador / guardar: _lineas_bitacora(lineas) → `reservado` viene en 0 (un borrador no aparta).
  const borrador = [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 3, precio_unitario: 10, almacen: "ENSAYO", reservado: 0 },
    { sku: "ZZPRUEBA-2", titulo: "Bolsa", cantidad: 1, precio_unitario: 5, almacen: null, reservado: 0 },
  ];
  for (const evento of ["creada", "borrador_guardado"]) {
    assert.deepEqual(tabla(evento, borrador), ["ZZPRUEBA-1 [ENSAYO] 3 pzs", "ZZPRUEBA-2 1 pza"]);
    assert.deepEqual(tonos(evento, borrador), [GRIS, GRIS]);
  }
  // confirmar: _lineas_bitacora(..., reservado=True) → `reservado` = cantidad.
  const confirmada = borrador.map((l) => ({ ...l, almacen: "ENSAYO", reservado: l.cantidad }));
  assert.deepEqual(tabla("confirmada", confirmada), ["ZZPRUEBA-1 [ENSAYO] 3 apartadas", "ZZPRUEBA-2 [ENSAYO] 1 apartada"]);
  assert.deepEqual(tonos("confirmada", confirmada), [VERDE, VERDE]);
  // crear_auto: la orden de una venta nace CONFIRMADA; su «creada» ya apartó.
  assert.deepEqual(tabla("creada", [{ sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 2, almacen: "ENSAYO", reservado: 2,
                                      precio_unitario: 99.5 }]), ["ZZPRUEBA-1 [ENSAYO] 2 apartadas"]);
  // Lo que la base no permite pero podría llegar: se pinta lo que DICE la bitácora, no lo que «debería».
  assert.deepEqual(chat.detalleLinea("confirmada", chat.lineasDe({ lineas: [{ sku: "X", cantidad: 3, reservado: 1 }] })[0]),
                   { texto: "1 de 3 apartadas", tono: AMBAR });
  assert.equal(chat.detalleLinea("confirmada", chat.lineasDe({ lineas: [{ sku: "X", cantidad: 3 }] })[0]).texto, "3 pzs");
});

test("TAB-3 · entregar dice «salieron N de M»: completo, de menos y cero son tres cosas", () => {
  // entregar: vienen SÓLO los renglones de esa entrega, con `entregado` = lo que salió y `reservado` 0.
  const salen = [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 3, entregado: 3, almacen: "ENSAYO", reservado: 0 },
    { sku: "ZZPRUEBA-2", titulo: "Bolsa", cantidad: 3, entregado: 2, almacen: "ENSAYO", reservado: 0 },
    { sku: "ZZPRUEBA-3", titulo: "Tapa", cantidad: 3, entregado: 1, almacen: "ENSAYO", reservado: 0 },
    // 0 = el renglón se cerró sin que saliera nada (su apartado se soltó). No es «sin dato».
    { sku: "ZZPRUEBA-4", titulo: "Liga", cantidad: 3, entregado: 0, almacen: "ENSAYO", reservado: 0 },
  ];
  for (const evento of ["entregada", "entregada_parcial"]) {
    assert.deepEqual(tabla(evento, salen), [
      "ZZPRUEBA-1 [ENSAYO] salieron 3 de 3", "ZZPRUEBA-2 [ENSAYO] salieron 2 de 3",
      "ZZPRUEBA-3 [ENSAYO] salió 1 de 3", "ZZPRUEBA-4 [ENSAYO] salieron 0 de 3",
    ]);
    assert.deepEqual(tonos(evento, salen), [VERDE, AMBAR, AMBAR, GRIS]);
  }
  // Si algún día llegara un renglón sin `entregado`, no se inventa que salió.
  const pendiente = [{ sku: "ZZPRUEBA-5", cantidad: 3, almacen: "ENSAYO" }];
  assert.deepEqual(tabla("entregada_parcial", pendiente), ["ZZPRUEBA-5 [ENSAYO] 3 pzs · por salir"]);
  assert.deepEqual(tabla("entregada", pendiente), ["ZZPRUEBA-5 [ENSAYO] 3 pzs"]);
});

test("TAB-4 · cancelar y borrar dicen «liberadas»; y lo que ya había salido se dice, en ámbar", () => {
  // _params_cancelar (cancelada) y borrar (borrada_admin): `reservado` = lo que TENÍA apartado.
  const confirmada = [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 3, almacen: "ENSAYO", reservado: 3, entregado: null },
    { sku: "ZZPRUEBA-2", titulo: "Bolsa", cantidad: 1, almacen: "ENSAYO", reservado: 1, entregado: null },
  ];
  for (const evento of ["cancelada", "borrada_admin"]) {
    assert.deepEqual(tabla(evento, confirmada), ["ZZPRUEBA-1 [ENSAYO] 3 liberadas", "ZZPRUEBA-2 [ENSAYO] 1 liberada"]);
    assert.deepEqual(tonos(evento, confirmada), [GRIS, GRIS]);
    // Un borrador no apartaba nada: sólo sus piezas.
    assert.deepEqual(tabla(evento, [{ sku: "ZZPRUEBA-1", cantidad: 3, almacen: null, reservado: 0, entregado: null }]),
                     ["ZZPRUEBA-1 3 pzs"]);
  }
  // Cancelar una confirmada con piezas YA FUERA escribe `devolucion_esperada` (queda DELIVERED but
  // CANCELLED) con TODOS los renglones: el que salió, el que seguía apartado, y el que se cerró en 0.
  const conSalida = [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 3, almacen: "ENSAYO", reservado: 0, entregado: 3 },
    { sku: "ZZPRUEBA-2", titulo: "Bolsa", cantidad: 2, almacen: "ENSAYO", reservado: 2, entregado: null },
    { sku: "ZZPRUEBA-3", titulo: "Tapa", cantidad: 3, almacen: "ENSAYO", reservado: 0, entregado: 0 },
  ];
  assert.deepEqual(tabla("devolucion_esperada", conSalida), [
    "ZZPRUEBA-1 [ENSAYO] salieron 3 de 3", "ZZPRUEBA-2 [ENSAYO] 2 liberadas", "ZZPRUEBA-3 [ENSAYO] salieron 0 de 3",
  ]);
  // Lo que salió de una venta cancelada tiene que REGRESAR: ámbar aunque saliera completo.
  assert.deepEqual(tonos("devolucion_esperada", conSalida), [AMBAR, GRIS, GRIS]);
  // responder_salio («sí salió») y salio_tarde: los renglones que salen, completos.
  assert.deepEqual(tabla("devolucion_esperada", [{ sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 1, entregado: 1,
                                                   almacen: "ENSAYO", reservado: 0 }]),
                   ["ZZPRUEBA-1 [ENSAYO] salió 1 de 1"]);
});

test("TAB-5 · «no alcanzó» dice qué se pidió y qué había: «sin existencias» no es «0 libres»", () => {
  // _no_alcanzo: SÓLO los renglones que no alcanzaron; `libre` = lo libre en su bodega, o null sin fila de saldo.
  const faltantes = [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 3, almacen: "ENSAYO", libre: 1, reservado: 0 },
    { sku: "ZZPRUEBA-2", titulo: "Bolsa", cantidad: 3, almacen: "ENSAYO", libre: 0, reservado: 0 },
    { sku: "ZZPRUEBA-3", titulo: "Tapa", cantidad: 3, almacen: "ENSAYO", libre: null, reservado: 0 },
  ];
  assert.deepEqual(tabla("no_alcanzo", faltantes), [
    "ZZPRUEBA-1 [ENSAYO] pide 3 · hay 1 libre", "ZZPRUEBA-2 [ENSAYO] pide 3 · hay 0 libres",
    "ZZPRUEBA-3 [ENSAYO] pide 3 · sin existencias",
  ]);
  assert.deepEqual(tonos("no_alcanzo", faltantes), [AMBAR, AMBAR, AMBAR]);
  // Si un renglón que SÍ alcanzaba llegara en la lista, no se pinta como problema.
  assert.deepEqual(tonos("no_alcanzo", [{ sku: "X", cantidad: 3, libre: 9 }]), [GRIS]);
  // Sin la llave `libre` no se sabe: se dice lo pedido y nada más.
  assert.deepEqual(tabla("no_alcanzo", [{ sku: "X", cantidad: 3 }]), ["X pide 3"]);
  // Sin cantidad no hay nada que afirmar, en ningún evento.
  assert.deepEqual(tabla("entregada", [{ sku: "X", entregado: 2 }]), ["X sin dato"]);
  // Un evento sin regla propia dice las piezas, traiga lo que traiga.
  assert.deepEqual(tabla("devolucion_recibida", [{ sku: "X", cantidad: 3, reservado: 3, entregado: 3 }]), ["X 3 pzs"]);
});

// ── ED · la corrección de una confirmada (`editada`, 0071) ───────────────────
//
// Los `datos` son los del contrato de la 0071 (`_editar_confirmada` del
// servicio): `cambios` y `renglones` con la misma forma de `borrador_guardado`,
// `lineas` = los renglones POR ENTREGAR como quedaron (ya apartados), y
// `apartado` = lo que el cambio movió el saldo (sólo los delta ≠ 0).

const EDITADA = {
  op: "x",
  cambios: { guia: [null, "JT123"], paqueteria: ["J&T", "Estafeta"] },
  renglones: {
    agregados: ["ZZPRUEBA-3"], quitados: ["ZZPRUEBA-2"],
    cambiados: [{ sku: "ZZPRUEBA-1", cantidad: [3, 5], precio_unitario: [10, 12.5] }],
  },
  lineas: [
    { sku: "ZZPRUEBA-1", titulo: "Caja", cantidad: 5, precio_unitario: 12.5, almacen: "ENSAYO", reservado: 5 },
    { sku: "ZZPRUEBA-3", titulo: "Tapa", cantidad: 3, precio_unitario: 4, almacen: "ENSAYO", reservado: 3 },
  ],
  apartado: [
    { sku: "ZZPRUEBA-1", almacen: "ENSAYO", delta: 2 },
    { sku: "ZZPRUEBA-2", almacen: "ENSAYO", delta: -2 },
    { sku: "ZZPRUEBA-3", almacen: "ENSAYO", delta: 3 },
  ],
};

test("ED-1 · `editada` tiene su icono y no grita; sus renglones se leen como los de la confirmación", () => {
  const e = chat.EVENTOS.editada;
  assert.match(e.rotulo, /editada/i);
  assert.ok(!e.llama, "corregir una confirmada no le pide nada a nadie");
  assert.doesNotMatch(e.tono, /amber|rose/);
  // No se confunde con guardar un borrador (papeleo) ni con confirmar.
  assert.notEqual(e.icono, chat.EVENTOS.borrador_guardado.icono);
  assert.notEqual(e.icono, chat.EVENTOS.confirmada.icono);
  assert.notEqual(e.tono, chat.EVENTOS.borrador_guardado.tono);
  assert.equal(chat.eventoDe({ evento: "editada", datos: EDITADA }), e);
  // `datos.lineas` = los renglones POR ENTREGAR como quedaron, ya con su apartado.
  assert.deepEqual(tabla("editada", EDITADA.lineas),
                   ["ZZPRUEBA-1 [ENSAYO] 5 apartadas", "ZZPRUEBA-3 [ENSAYO] 3 apartadas"]);
  assert.deepEqual(tonos("editada", EDITADA.lineas), [VERDE, VERDE]);
  // El encabezado se lee con el mismo lector de `borrador_guardado`, y con sus rótulos.
  assert.deepEqual(chat.cambiosDe(EDITADA).map((c) => [chat.ROTULO_CAMPO[c.campo], c.antes, c.despues]),
                   [["Guía", null, "JT123"], ["Paquetería", "J&T", "Estafeta"]]);
});

test("ED-2 · qué renglones entraron, salieron o cambiaron: con su antes → después, y sin inventar medio cambio", () => {
  const r = chat.renglonesDe(EDITADA);
  assert.deepEqual(r, {
    agregados: ["ZZPRUEBA-3"], quitados: ["ZZPRUEBA-2"],
    cambiados: [{ sku: "ZZPRUEBA-1", cantidad: [3, 5], precio_unitario: [10, 12.5] }],
  });
  assert.deepEqual(chat.cambiosDeRenglon(r.cambiados[0]), [
    { rotulo: "cantidad", antes: "3", despues: "5" },
    { rotulo: "precio unit.", antes: "$10.00", despues: "$12.50" },
  ]);
  // Sólo lo que cambió: si sólo vino la cantidad, del precio no se dice nada.
  assert.deepEqual(chat.cambiosDeRenglon({ sku: "X", cantidad: [1, 2] }),
                   [{ rotulo: "cantidad", antes: "1", despues: "2" }]);
  // Es la misma forma que deja `borrador_guardado` (la arma `_dif_lineas`): se lee igual.
  assert.deepEqual(chat.renglonesDe({ op: "x", cambios: {}, renglones: { agregados: ["A"], quitados: [], cambiados: [] } }),
                   { agregados: ["A"], quitados: [], cambiados: [] });
  // Los `numeric` de Postgres pueden llegar como texto.
  assert.deepEqual(chat.renglonesDe({ renglones: { cambiados: [{ sku: "X", precio_unitario: ["10.00", "12.5"] }] } }).cambiados,
                   [{ sku: "X", precio_unitario: [10, 12.5] }]);
  // Lo que no es un cambio completo no se pinta: sin SKU, medio par, un par sin números, o nada que cambió.
  assert.equal(chat.renglonesDe({ renglones: { agregados: [], quitados: [], cambiados: [
    { cantidad: [1, 2] }, { sku: "X", cantidad: [1] }, { sku: "Y", cantidad: ["uno", 2] }, { sku: "Z" }, null, "basura",
  ] } }), null);
  // Sin `renglones` (o sólo reordenados, o con otra cosa en su lugar) no hay nada que decir.
  for (const datos of [null, { op: "x" }, { renglones: null }, { renglones: "3 con cambios" }, { renglones: [] },
                       { renglones: { agregados: [], quitados: [], cambiados: [] } }]) {
    assert.equal(chat.renglonesDe(datos), null);
  }
  // Un SKU vacío, o que no es texto, no entra a las listas.
  assert.deepEqual(chat.renglonesDe({ renglones: { agregados: ["A", "", null, 7, "  B "], quitados: "X" } }),
                   { agregados: ["A", "B"], quitados: [], cambiados: [] });
});

test("ED-3 · la línea del apartado: el signo se dice SIEMPRE, y lo que no se movió no se pinta", () => {
  assert.deepEqual(chat.apartadoDe(EDITADA).map(chat.textoApartado),
                   ["+2 ZZPRUEBA-1 en ENSAYO", "−2 ZZPRUEBA-2 en ENSAYO", "+3 ZZPRUEBA-3 en ENSAYO"]);
  // Un delta 0, sin número o sin SKU no dice nada. (Un `numeric` puede llegar como texto.)
  assert.deepEqual(chat.apartadoDe({ apartado: [
    { sku: "X", almacen: "ENSAYO", delta: 0 }, { sku: "Y", almacen: "ENSAYO" }, { almacen: "ENSAYO", delta: 2 },
    { sku: "Z", almacen: "ENSAYO", delta: "3" }, null, 5,
  ] }), [{ sku: "Z", almacen: "ENSAYO", delta: 3 }]);
  // Sin bodega (no debería pasar) se dice lo que se sabe, sin inventarla.
  assert.equal(chat.textoApartado({ sku: "X", almacen: "", delta: -1 }), "−1 X");
  assert.equal(chat.textoApartado({ sku: "X", almacen: "TEX3", delta: 1200 }), "+1,200 X en TEX3");
  // Corregir sólo el encabezado no mueve el apartado: no hay línea.
  for (const datos of [null, { op: "x", cambios: { guia: [null, "JT1"] } }, { apartado: [] }, { apartado: "nada" }]) {
    assert.deepEqual(chat.apartadoDe(datos), []);
  }
});

/** El texto que se LEE de un movimiento del sistema ya pintado (sin etiquetas). */
const leer = (m) => renderToStaticMarkup(React.createElement(chat.RenglonSistema, { m, fresco: false }))
  .replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ").trim();

const movimiento = (mas = {}) => ({
  id: 12, orden_id: 1, tipo: "sistema", evento: "editada",
  cuerpo: "Orden editada · guía, paquetería; renglones: 1 agregado(s), 1 quitado(s), 1 con cambios · se movió el apartado",
  datos: EDITADA, autor: "gabriela@kubera.mx", autor_nombre: "Gabriela Ramírez", via: "panel",
  creado_at: "2026-10-09T18:00:00Z", ...mas,
});

test("ED-4 · el movimiento `editada` se PINTA entero: quién, qué campo, qué renglón y cuánto se movió el apartado", () => {
  const t = leer(movimiento());
  assert.match(t, /Orden editada · guía, paquetería; renglones: 1 agregado\(s\)/);
  // Quién y cuándo (hora de CDMX): es el rastro que antes cuidaba el «ya no se modifica».
  assert.match(t, /Gabriela Ramírez · 09 oct 12:00/);
  // El encabezado: campo, antes → después.
  assert.match(t, /Guía: — → JT123/);
  assert.match(t, /Paquetería: J&T → Estafeta/);
  // Los renglones: lo que entró, lo que se quitó, y el que cambió con su antes → después.
  assert.match(t, /Agregado: ZZPRUEBA-3/);
  assert.match(t, /Quitado: ZZPRUEBA-2/);
  assert.match(t, /ZZPRUEBA-1: cantidad 3 → 5 · precio unit\. \$10\.00 → \$12\.50/);
  // El apartado, en UNA línea corta.
  assert.match(t, /apartado: \+2 ZZPRUEBA-1 en ENSAYO · −2 ZZPRUEBA-2 en ENSAYO · \+3 ZZPRUEBA-3 en ENSAYO/);
  // Y cómo quedaron los renglones por entregar.
  assert.match(t, /ZZPRUEBA-1 Caja ENSAYO 5 apartadas/);
  assert.match(t, /ZZPRUEBA-3 Tapa ENSAYO 3 apartadas/);

  // Corregir SÓLO la guía: no hay renglones ni apartado que decir, y no se inventan.
  const soloGuia = leer(movimiento({ cuerpo: "Orden editada · guía", datos: { op: "x", cambios: { guia: ["JT1", "JT2"] } } }));
  assert.match(soloGuia, /Guía: JT1 → JT2/);
  assert.doesNotMatch(soloGuia, /apartado:|Agregado|Quitado|apartadas/);
  // Varios agregados van en una sola línea, en plural.
  const varios = leer(movimiento({ datos: { op: "x", renglones: { agregados: ["A-1", "B-2"], quitados: ["C-3", "D-4"], cambiados: [] } } }));
  assert.match(varios, /Agregados: A-1, B-2/);
  assert.match(varios, /Quitados: C-3, D-4/);
  // Un movimiento sin `datos` no truena ni pinta nada de esto.
  assert.doesNotMatch(leer(movimiento({ evento: "confirmada", cuerpo: "Orden confirmada", datos: null })),
                      /apartado:|Agregado|Quitado/);
});

// ── Tercera revisión (9-oct-2026) ─────────────────────────────────────────────

/** El `<li>` ya pintado que contiene ese texto (con sus etiquetas: para ver qué va tachado). */
const renglonPintado = (m, contiene) =>
  renderToStaticMarkup(React.createElement(chat.RenglonSistema, { m, fresco: false }))
    .split("<li").find((li) => li.includes(contiene)) ?? "";

test("REV3 PANT-4 · el SKU que viene como «agregado» Y «quitado» sólo CAMBIÓ DE BODEGA: se dice así, y sale de las dos listas", () => {
  // `_dif_lineas` casa los renglones por SKU + bodega: mover uno —o elegirle bodega al que no
  // tenía— llega como quitado y agregado a la vez.
  assert.deepEqual(chat.repartoDe({ agregados: ["ZZPRUEBA-B"], quitados: ["ZZPRUEBA-B"] }),
                   { movidos: ["ZZPRUEBA-B"], agregados: [], quitados: [] });
  // Junto con altas y bajas de verdad, sólo se aparta el que está en las dos.
  assert.deepEqual(chat.repartoDe({ agregados: ["ZZPRUEBA-B", "ZZPRUEBA-C"], quitados: ["ZZPRUEBA-A", "ZZPRUEBA-B"] }),
                   { movidos: ["ZZPRUEBA-B"], agregados: ["ZZPRUEBA-C"], quitados: ["ZZPRUEBA-A"] });
  // Sin distinguir mayúsculas (la llave del servicio va en minúsculas).
  assert.deepEqual(chat.repartoDe({ agregados: ["zzprueba-b"], quitados: ["ZZPRUEBA-B"] }),
                   { movidos: ["zzprueba-b"], agregados: [], quitados: [] });
  // UNO A UNO: de un SKU repartido en bodegas, lo que sobra de un lado sigue siendo un alta o una baja.
  assert.deepEqual(chat.repartoDe({ agregados: ["X", "X"], quitados: ["X"] }), { movidos: ["X"], agregados: ["X"], quitados: [] });
  assert.deepEqual(chat.repartoDe({ agregados: ["X"], quitados: ["X", "X"] }), { movidos: ["X"], agregados: [], quitados: ["X"] });
  // Sin coincidencias no cambia nada, y lo que se leyó de la bitácora no se toca.
  const leido = chat.renglonesDe(EDITADA);
  assert.deepEqual(chat.repartoDe(leido), { movidos: [], agregados: ["ZZPRUEBA-3"], quitados: ["ZZPRUEBA-2"] });
  assert.deepEqual([leido.agregados, leido.quitados], [["ZZPRUEBA-3"], ["ZZPRUEBA-2"]]);

  // PINTADO. Un BORRADOR al que sólo se le eligió la bodega: no trae línea de apartado que lo
  // explique, y decía «Agregado: B · Quitado: B» de un producto que seguía en la orden.
  const borrador = movimiento({
    evento: "borrador_guardado", cuerpo: "Borrador guardado · renglones: 1 agregado(s), 1 quitado(s)",
    datos: { op: "x", cambios: {}, renglones: { agregados: ["ZZPRUEBA-B"], quitados: ["ZZPRUEBA-B"], cambiados: [] },
             lineas: [{ sku: "ZZPRUEBA-B", titulo: "Bocina", cantidad: 2, precio_unitario: 10, almacen: "ENSAYO", reservado: 0 }] },
  });
  assert.match(leer(borrador), /Cambió de bodega: ZZPRUEBA-B/);
  assert.doesNotMatch(leer(borrador), /Agregado|Quitado/);
  assert.match(leer(borrador), /ZZPRUEBA-B Bocina ENSAYO 2 pzs/, "a cuál se fue lo dice la mini-tabla del mismo movimiento");
  // Sigue en la orden: NO va tachado (tachado es lo que ya no está).
  assert.ok(renglonPintado(borrador, "Cambió de bodega"));
  assert.doesNotMatch(renglonPintado(borrador, "Cambió de bodega"), /line-through/);

  // En `editada`, mezclado: B se mudó a TEX3, C entró y A se quitó. Cada cosa en su renglón.
  const editada = movimiento({
    cuerpo: "Orden editada · renglones: 2 agregado(s), 2 quitado(s) · se movió el apartado",
    datos: { op: "x", cambios: {},
             renglones: { agregados: ["ZZPRUEBA-B", "ZZPRUEBA-C"], quitados: ["ZZPRUEBA-B", "ZZPRUEBA-A"], cambiados: [] },
             lineas: [{ sku: "ZZPRUEBA-B", titulo: "Bocina", cantidad: 2, precio_unitario: 10, almacen: "TEX3", reservado: 2 },
                      { sku: "ZZPRUEBA-C", titulo: "Cable", cantidad: 1, precio_unitario: 5, almacen: "ENSAYO", reservado: 1 }],
             apartado: [{ sku: "ZZPRUEBA-B", almacen: "TEX3", delta: 2 }, { sku: "ZZPRUEBA-B", almacen: "ENSAYO", delta: -2 },
                        { sku: "ZZPRUEBA-C", almacen: "ENSAYO", delta: 1 }, { sku: "ZZPRUEBA-A", almacen: "ENSAYO", delta: -1 }] },
  });
  const t = leer(editada);
  assert.match(t, /Cambió de bodega: ZZPRUEBA-B Agregado: ZZPRUEBA-C Quitado: ZZPRUEBA-A/);
  assert.doesNotMatch(t, /(Agregado|Quitado)s?: [^:]*ZZPRUEBA-B/, "B ya no sale ni como alta ni como baja");
  assert.match(renglonPintado(editada, "Quitado:"), /line-through[^>]*>ZZPRUEBA-A</, "lo que sí se quitó sigue tachado");
  assert.match(t, /apartado: \+2 ZZPRUEBA-B en TEX3 · −2 ZZPRUEBA-B en ENSAYO/, "y el apartado lo sigue contando bodega por bodega");
  // Varios: en plural, en una línea.
  assert.match(leer(movimiento({ datos: { op: "x", renglones: { agregados: ["A-1", "B-2"], quitados: ["b-2", "A-1"], cambiados: [] } } })),
               /Cambiaron de bodega: A-1, B-2/);
});

test("REV3 PANT-4 · el renglón al que le cambió el TÍTULO o la IMAGEN se dice en una línea corta, y no desaparece si es lo único", () => {
  // Lo que deja `_dif_lineas` desde que compara título e imagen: [antes, después], con `null` = no tenía.
  const datos = { op: "x", cambios: {}, renglones: { agregados: [], quitados: [], cambiados: [
    { sku: "ZZPRUEBA-1", titulo: ["Audífonos", "OTRA COSA DISTINTA"] },
    { sku: "ZZPRUEBA-2", imagen: [null, "https://cdn.prueba.test/b.jpg"] },
    { sku: "ZZPRUEBA-3", cantidad: [3, 5], titulo: ["Cable", null], imagen: ["https://cdn.prueba.test/c.jpg", null] },
  ] } };
  const r = chat.renglonesDe(datos);
  // Los tres se leen: antes, el que SÓLO traía título o imagen se perdía sin dejar nada.
  assert.deepEqual(r.cambiados, datos.renglones.cambiados);
  // En la línea va una nota corta; el antes y el después, en su detalle (el `title`).
  assert.deepEqual(chat.notasDeRenglon(r.cambiados[0]),
                   [{ texto: "título cambiado", detalle: "Audífonos → OTRA COSA DISTINTA" }]);
  assert.deepEqual(chat.notasDeRenglon(r.cambiados[1]),
                   [{ texto: "imagen cambiada", detalle: "sin imagen → https://cdn.prueba.test/b.jpg" }]);
  assert.deepEqual(chat.notasDeRenglon(r.cambiados[2]), [
    { texto: "título cambiado", detalle: "Cable → sin título" },
    { texto: "imagen cambiada", detalle: "https://cdn.prueba.test/c.jpg → sin imagen" },
  ]);
  // No son números: no entran a los «antes → después» de cantidad y precio, ni al revés.
  assert.deepEqual(chat.cambiosDeRenglon(r.cambiados[0]), []);
  assert.deepEqual(chat.cambiosDeRenglon(r.cambiados[2]), [{ rotulo: "cantidad", antes: "3", despues: "5" }]);
  assert.deepEqual(chat.notasDeRenglon({ sku: "X", cantidad: [1, 2], precio_unitario: [10, 12] }), []);

  // PINTADO: una línea por renglón, y la nota después de los números.
  const m = movimiento({ cuerpo: "Orden editada · renglones: 3 con cambios", datos });
  const t = leer(m);
  assert.match(t, /ZZPRUEBA-1: título cambiado/);
  assert.match(t, /ZZPRUEBA-2: imagen cambiada/);
  assert.match(t, /ZZPRUEBA-3: cantidad 3 → 5 · título cambiado · imagen cambiada/);
  // El título entero (y la dirección de la imagen) no se vacían en la bitácora: quedan al pasar el cursor.
  assert.doesNotMatch(t, /OTRA COSA DISTINTA|cdn\.prueba/);
  assert.match(renglonPintado(m, "ZZPRUEBA-1"), /title="Audífonos → OTRA COSA DISTINTA"[^>]*> título cambiado</);

  // Lo que NO es un cambio de texto: medio par, dos iguales (vacío y nulo son lo mismo), o algo que no es un par.
  assert.equal(chat.renglonesDe({ renglones: { cambiados: [
    { sku: "X", titulo: ["Uno"] }, { sku: "Y", titulo: ["Igual", " Igual "] }, { sku: "Z", imagen: [null, ""] },
    { sku: "W", titulo: "Uno → Dos" }, { titulo: ["Uno", "Dos"] },
  ] } }), null);
  // Y lo de siempre no cambió: sólo cantidad y precio se siguen leyendo igual (ED-2).
  assert.deepEqual(chat.renglonesDe(EDITADA).cambiados, [{ sku: "ZZPRUEBA-1", cantidad: [3, 5], precio_unitario: [10, 12.5] }]);
});

// ── TRZ · el plan del riel ───────────────────────────────────────────────────

const orden = (mas = {}) => ({
  id: 1, folio: "OV-00001", estado: "confirmada", confirmada_at: "2026-10-06T15:00:00Z",
  devolucion_estado: null, canal_cancelo_at: null, canal_cancelo_ref: null, borrada_at: null,
  renglones: 2, piezas: 4, piezas_apartadas: 4, piezas_entregadas: 0, renglones_entregados: 0, ...mas,
});

test("TRZ-1 · sólo una CONFIRMADA con renglones ya entregados lleva el tramo a medias", () => {
  assert.equal(traza.planDe(null).avance, 0);
  assert.equal(traza.planDe(orden({ estado: "borrador", confirmada_at: null })).avance, 0);
  assert.equal(traza.planDe(orden()).avance, 0, "confirmada sin entregas: en la estación");
  const media = traza.planDe(orden({ renglones_entregados: 1, piezas_entregadas: 2, piezas_apartadas: 2 }));
  assert.equal(media.avance, 0.5);
  assert.deepEqual([media.estado, media.llego], ["confirmada", 1], "sigue confirmada: la tercera estación no se llena");
  // Al salir el último renglón ya es otra cosa (la tercera estación), no «un avance del 100%».
  for (const estado of ["entregada", "entregada_cancelada", "cancelada"]) {
    const p = traza.planDe(orden({ estado, renglones_entregados: 2, piezas_entregadas: 4 }));
    assert.equal(p.avance, 0, estado);
  }
  assert.equal(traza.planDe(orden({ estado: "entregada", renglones_entregados: 2, piezas_entregadas: 4 })).llego, 2);
});

test("TRZ-2 · el tramo a medias nunca se ve vacío ni lleno (los topes), y sin dato no se inventa avance", () => {
  const con = (entregadas, piezas, renglones = 1) =>
    traza.avanceDe({ estado: "confirmada", piezas, piezas_entregadas: entregadas, renglones_entregados: renglones });
  assert.equal(con(1, 100), 0.18, "1 de 100 serían 0.5 px: se ve que algo ya salió");
  assert.equal(con(99, 100), 0.85, "99 de 100 no se confunde con una entregada");
  assert.equal(con(3, 4), 0.75);
  // Un renglón «entregado» con 0 piezas (no salió nada, se soltó): YA es entrega parcial.
  assert.equal(con(0, 4), 0.18);
  // Sin renglones entregados no hay avance, digan lo que digan las piezas.
  assert.equal(con(2, 4, 0), 0);
  // Lo que no llegó (o llegó mal) no dibuja nada, ni truena. (Directo, sin `con`:
  // su valor por omisión convertiría el `undefined` en un 1.)
  assert.equal(traza.avanceDe({ estado: "confirmada", piezas: 4, piezas_entregadas: 2 }), 0);
  assert.equal(con(2, 4, null), 0);
  assert.equal(con(2, 4, Number.NaN), 0);
  assert.equal(con(undefined, 4), 0.18);
  assert.equal(con(2, 0), 0.18);
  assert.equal(con(Number.NaN, Number.NaN), 0.18);
  for (const v of [con(1, 100), con(99, 100), con(0, 4), con(7, 4)]) assert.ok(v > 0 && v < 1);
});

test("TRZ-3 · la alerta «el canal canceló» es sólo mientras alguien tiene que contestar", () => {
  const marca = { canal_cancelo_at: "2026-10-06T16:00:00Z", canal_cancelo_ref: "IN_TRANSIT" };
  assert.equal(traza.planDe(orden()).alerta, false);
  assert.equal(traza.planDe(orden(marca)).alerta, true);
  // Ya contestaron (sí salió / no salió): la marca se queda en la orden, la alerta no.
  assert.equal(traza.planDe(orden({ ...marca, estado: "cancelada" })).alerta, false);
  assert.equal(traza.planDe(orden({ ...marca, estado: "entregada_cancelada", devolucion_estado: "pendiente" })).alerta, false);
  // Una borrada no le pide nada a nadie.
  assert.equal(traza.planDe(orden({ ...marca, borrada_at: "2026-10-06T17:00:00Z" })).alerta, false);
  // Entrega parcial Y canal cancelado a la vez: las dos cosas se dicen.
  const ambas = traza.planDe(orden({ ...marca, renglones_entregados: 1, piezas_entregadas: 1 }));
  assert.deepEqual([ambas.avance, ambas.alerta], [0.25, true]);
});

test("TRZ-4 · lo de siempre no cambió: hasta dónde llegó, la devolución pendiente y la borrada", () => {
  assert.deepEqual(traza.planDe(null),
                   { estado: "nueva", llego: -1, devolucion: false, borrada: false, avance: 0, alerta: false });
  assert.equal(traza.planDe(orden({ estado: "borrador", confirmada_at: null })).llego, 0);
  // Una cancelada se corta donde iba: confirmada o todavía borrador.
  assert.equal(traza.planDe(orden({ estado: "cancelada" })).llego, 1);
  assert.equal(traza.planDe(orden({ estado: "cancelada", confirmada_at: null })).llego, 0);
  const regreso = traza.planDe(orden({ estado: "entregada_cancelada", devolucion_estado: "pendiente" }));
  assert.deepEqual([regreso.llego, regreso.devolucion], [2, true]);
  assert.equal(traza.planDe(orden({ estado: "entregada_cancelada", devolucion_estado: "recibida" })).devolucion, false);
  assert.equal(traza.planDe(orden({ borrada_at: "2026-10-06T17:00:00Z" })).borrada, true);
});
