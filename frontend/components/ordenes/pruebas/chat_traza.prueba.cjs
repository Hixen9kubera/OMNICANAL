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
 *     aquí: se LEE del .sql de la 0064, que es la verdad.
 *   · TAB · La mini-tabla de cada movimiento rotula según el evento. Con el
 *     modelo viejo decía «N de M reservadas»; ahora se aparta todo o nada y se
 *     entrega con 0..cantidad piezas por renglón. Un «salieron 0 de 3» no es lo
 *     mismo que «sin dato», y ninguno de los dos es un 3.
 *   · TRZ · El riel pinta una entrega a medias y la alerta de «el canal
 *     canceló». Las dos salen de `planDe`; si se equivoca, la lista entera
 *     enseña órdenes que piden respuesta como si fueran normales (o al revés).
 *
 * Lo que NO se prueba aquí: el dibujo (lienzo y SVG) y el bucle del worker, que
 * sólo existen en un navegador.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const cargar = require("./cargar.cjs");

const chat = cargar("ChatOrden");
const traza = cargar("Traza");

const RAIZ = path.resolve(__dirname, "..", "..", "..", "..");
const MIGRACION = path.join(RAIZ, "supabase", "migrations", "0064_ops_ordenes_venta.sql");

/** Los eventos que admite `ventas.ov_mensajes.evento`, leídos del CHECK de la 0064. */
function catalogoDeLaBase() {
  const sql = fs.readFileSync(MIGRACION, "utf8");
  const m = /constraint\s+ov_mensajes_evento_chk\s+check\s*\(\s*evento\s+is\s+null\s+or\s+evento\s+in\s*\(([^)]*)\)/i.exec(sql);
  assert.ok(m, "no se encontró el CHECK ov_mensajes_evento_chk en la 0064");
  return Array.from(m[1].matchAll(/'([a-z_]+)'/g), (x) => x[1]).sort();
}

// ── CAT · el catálogo de eventos ─────────────────────────────────────────────

test("CAT-1 · EVENTOS cubre EXACTAMENTE el catálogo del CHECK de la 0064 (ni falta ni sobra)", () => {
  const base = catalogoDeLaBase();
  assert.equal(base.length, 15, "el CHECK trae 15 eventos");
  assert.deepEqual(Object.keys(chat.EVENTOS).sort(), base);
  // Los del modelo del 2-oct ya no existen: no deben quedar pintables.
  for (const viejo of ["editada", "reserva", "regresada", "borrada", "archivo", "archivo_borrado", "devolucion",
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
