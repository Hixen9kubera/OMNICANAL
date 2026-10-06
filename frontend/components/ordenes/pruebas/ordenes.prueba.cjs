/**
 * Pruebas de la lógica PURA de Inventario → ÓRDENES DE VENTA (la pantalla).
 *
 *     node frontend/components/ordenes/pruebas/ordenes.prueba.cjs
 *
 * Sin dependencias: `node:test` + el `typescript` que el frontend ya trae (ver
 * `cargar.cjs`). Prueban la REGLA, no el texto: son los casos en que la
 * pantalla se equivocaba CALLADA (guardaba de menos, borraba lo de otro, se
 * quedaba atrás) y nadie se enteraba hasta que almacén surtía mal.
 *
 * Dos generaciones de bloques:
 *   · FE-*, MOV-*, PED-*: los hallazgos de la revisión adversarial del
 *     2-oct-2026, adaptados al modelo nuevo (lo que cuidaban sigue en pie).
 *   · BOD-*, VAL-*, CONF-*, VENTA-*, ENT-*, ACC-*, MOT-*, LEC-*, PDF-*: lo que
 *     trajo el contrato de la 0064/0065 (6-oct-2026): la bodega va por
 *     renglón, se aparta todo o nada, sólo el borrador se edita, se entrega
 *     por renglón, y el canal puede cancelar con el paquete en camino.
 *
 * Lo que aquí NO se puede probar —el foco de los diálogos, «Atrás» del
 * navegador, el `datetime-local` a medias, el 409 de punta a punta— vive en el
 * navegador. El chat y la traza tienen su archivo: `chat_traza.prueba.cjs`.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const cargar = require("./cargar.cjs");

const doc = cargar("OrdenDocumento");
const ui = cargar("ui");
const buscador = cargar("BuscadorSku");
const archivos = cargar("ArchivosOrden");
const ventasMp = cargar("VentasMarketplace");
const chat = cargar("ChatOrden");

// ── Armazones ────────────────────────────────────────────────────────────────

/** El catálogo `ops.almacenes` tal como lo siembra la 0064 (TEX3 nace apagada). */
const bodega = (codigo, nombre, fuente, admite_ov) => ({
  codigo, nombre, fuente, admite_ov, surte_ventas: false, cuenta_para_woo: false,
});
const TEXCO = bodega("TEXCO", "TEXCO", "odoo", false);
const TEX3_APAGADA = bodega("TEX3", "TEXCO III", "kubera", false);
const TEX3 = bodega("TEX3", "TEXCO III", "kubera", true);
const ENSAYO = bodega("ENSAYO", "Bodega de ensayo", "kubera", true);
const REVISION = bodega("REVISION", "Revisión de devoluciones", "kubera", false);
const CATALOGO = [TEXCO, TEX3_APAGADA, ENSAYO, REVISION];
/** Fase B: TEX3 encendida. */
const CATALOGO_B = [TEXCO, TEX3, ENSAYO, REVISION];

const PERMISOS = {
  editar: true, confirmar: true, entregar: false, cancelar: true, borrar: false, responder_salio: false,
  salio_tarde: false, mensajes: true, subir_archivo: false, bajar_archivo: true, borrar_archivo: false, porque: {},
};
const permisos = (mas = {}) => ({ ...PERMISOS, ...mas, porque: { ...(mas.porque ?? {}) } });

const saldo = (almacen, fisico, apartado = 0) => ({ almacen, fisico, apartado, libre: fisico - apartado });

/** Un renglón como lo manda el backend: en ENSAYO, con 50 libres, sin apartar y sin salir. */
const linea = (n, sku, cantidad, precio = 10, mas = {}) => ({
  id: n, linea: n, sku, titulo: sku, imagen: null, cantidad, precio_unitario: precio,
  importe: cantidad * precio, almacen: "ENSAYO", reservado: 0, entregado: null, entregado_at: null,
  entregado_por: null, fisico: 50, apartado: 0, libre: 50, conocido: true, ...mas,
});
/** El mismo renglón cuando todavía nadie le eligió bodega (sin bodega no hay saldo que decir). */
const sinBodega = { almacen: null, fisico: null, apartado: null, libre: null };

const orden = (rev, lineas, mas = {}) => ({
  id: 1, folio: "OV-00001", estado: "borrador", tipo: "venta", rev, cliente: null, canal: null, mp_canal: null,
  mp_cuenta: null, mp_orden: null, full_tienda: null, envio_ref: null, descripcion: null, guia: null,
  paqueteria: null, fecha_venta: null, entrega_limite: null, moneda: "MXN",
  total: lineas.reduce((a, l) => a + l.importe, 0), comision: 0, precio_origen: "manual",
  devolucion_estado: null, canal_cancelo_at: null, canal_cancelo_ref: null, confirmada_at: null,
  borrada_at: null, piezas: lineas.reduce((a, l) => a + l.cantidad, 0), piezas_apartadas: 0,
  piezas_entregadas: 0, renglones_entregados: 0, lineas, archivos: [], permisos: PERMISOS, ...mas,
});

/** Los renglones de una forma, legibles: «AAA-1 x4 @10.00». */
const ver = (f) => f.lineas.map((l) => `${l.sku} x${l.cantidad} @${l.precio}`);
/** Lo mismo, con su bodega: «AAA-1 x4 ENSAYO» («?» = sin elegir). */
const verBodega = (f) => f.lineas.map((l) => `${l.sku} x${l.cantidad} ${l.almacen || "?"}`);

/** La forma de «yo», con una cantidad o una bodega cambiada / un renglón quitado / uno agregado. */
const conCantidad = (f, sku, cantidad) =>
  ({ ...f, lineas: f.lineas.map((l) => (l.sku === sku ? { ...l, cantidad: String(cantidad) } : l)) });
const conBodega = (f, sku, almacen, deBodega) => ({
  ...f,
  lineas: f.lineas.map((l) => (
    l.sku === sku && (deBodega === undefined || l.almacen === deBodega) ? { ...l, almacen } : l)),
});
const sin = (f, sku) => ({ ...f, lineas: f.lineas.filter((l) => l.sku !== sku) });
const mas = (f, sku, cantidad, precio = "10.00", almacen = "ENSAYO") => ({
  ...f,
  lineas: [...f.lineas, doc.renglonNuevo({ sku, titulo: sku, cantidad: String(cantidad), precio, almacen,
                                           conocido: true })],
});

const venta = (masCampos = {}) => ({
  canal: "temu", cuenta: "TEMU", orden: "PO-1", fecha: null, estado_canal: null, estado_wc: null,
  cancelada: false, es_fulfillment: false, total: 300, comision: 30, neto: 270, guia: null,
  paqueteria: null, entrega_limite: null, piezas: 3,
  lineas: [{ sku: "AAA-1", cantidad: 1, precio_unitario: 100, titulo: "Audífonos", imagen: null }],
  ov: null, ...masCampos,
});

// ── FE-2 · fusión a tres bandas de los renglones ─────────────────────────────

test("FE-2 · el caso de la revisión: lo que agregó la otra persona NO se pierde", () => {
  // Ana y Luis abren el mismo borrador (AAA-1 x2). Luis agrega BBB-2 y sube
  // AAA-1 a 3; Ana, sin saberlo, cambia AAA-1 a 4 y da Guardar → 409.
  const baseVieja = doc.formaDe(orden(3, [linea(1, "AAA-1", 2)]));
  const ana = conCantidad(baseVieja, "AAA-1", 4);
  const baseNueva = doc.formaDe(orden(4, [linea(1, "AAA-1", 3), linea(2, "BBB-2", 1, 5)]));

  const fusion = doc.fusionar(baseVieja, ana, baseNueva, PERMISOS);
  assert.deepEqual(ver(fusion), ["AAA-1 x4 @10.00", "BBB-2 x1 @5.00"]);

  // Y el segundo Guardar manda los DOS renglones (con su bodega): antes mandaba
  // sólo AAA-1 y el backend borraba BBB-2.
  const put = doc.cambiosDe(baseNueva, fusion);
  assert.deepEqual(put.lineas.map((l) => `${l.sku} x${l.cantidad} ${l.almacen}`),
                   ["AAA-1 x4 ENSAYO", "BBB-2 x1 ENSAYO"]);
});

test("FE-2 · cada regla de la fusión, renglón por renglón", () => {
  const viejo = doc.formaDe(orden(1, [linea(1, "AAA", 2), linea(2, "BBB", 1), linea(3, "CCC", 5), linea(4, "DDD", 1)]));
  const caso = (mio, servidor) => ver({ lineas: doc.fusionarLineas(viejo.lineas, mio.lineas, servidor.lineas) });

  // Lo quité yo: se va, aunque la otra persona lo haya cambiado.
  assert.deepEqual(caso(sin(viejo, "BBB"), conCantidad(viejo, "BBB", 9)),
                   ["AAA x2 @10.00", "CCC x5 @10.00", "DDD x1 @10.00"]);
  // Lo quitó la otra persona y yo NO lo toqué: se respeta.
  assert.deepEqual(caso(conCantidad(viejo, "AAA", 7), sin(viejo, "DDD")),
                   ["AAA x7 @10.00", "BBB x1 @10.00", "CCC x5 @10.00"]);
  // Lo quitó la otra persona pero yo lo CAMBIÉ: se queda con lo mío (lo veo y decido).
  assert.deepEqual(caso(conCantidad(viejo, "DDD", 3), sin(viejo, "DDD")),
                   ["AAA x2 @10.00", "BBB x1 @10.00", "CCC x5 @10.00", "DDD x3 @10.00"]);
  // Lo cambió la otra persona y yo no: gana el servidor.
  assert.deepEqual(caso(mas(viejo, "EEE", 1), conCantidad(viejo, "CCC", 8)),
                   ["AAA x2 @10.00", "BBB x1 @10.00", "CCC x8 @10.00", "DDD x1 @10.00", "EEE x1 @10.00"]);
  // Los dos cambiamos LO MISMO (la cantidad): gana lo mío, como en el encabezado.
  assert.deepEqual(caso(conCantidad(viejo, "CCC", 6), conCantidad(viejo, "CCC", 8))[2], "CCC x6 @10.00");
  // Los dos agregamos el MISMO SKU: un solo renglón, con mi cantidad.
  assert.deepEqual(caso(mas(viejo, "ZZZ", 4), mas(viejo, "zzz", 1)).filter((x) => /zzz/i.test(x)),
                   ["zzz x4 @10.00"]);
});

test("FE-2 · mi renglón cambiado conserva el saldo FRESCO del servidor", () => {
  const baseVieja = doc.formaDe(orden(1, [linea(1, "AAA-1", 2)]));
  const fresca = linea(1, "AAA-1", 2, 10, { fisico: 9, apartado: 2, libre: 7 });
  const baseNueva = doc.formaDe(orden(2, [fresca, linea(2, "BBB-2", 1)]));
  const [mia] = doc.fusionar(baseVieja, conCantidad(baseVieja, "AAA-1", 4), baseNueva, PERMISOS).lineas;
  assert.equal(mia.cantidad, "4");
  assert.deepEqual(doc.saldoDe(mia), saldo("ENSAYO", 9, 2));
});

test("FE-2 · si yo no toqué renglones (o ya no puedo), mandan los del servidor", () => {
  const baseVieja = doc.formaDe(orden(1, [linea(1, "AAA-1", 2)]));
  const baseNueva = doc.formaDe(orden(2, [linea(1, "AAA-1", 3), linea(2, "BBB-2", 1)]));
  const soloGuia = { ...baseVieja, guia: "JT-1" };
  const f = doc.fusionar(baseVieja, soloGuia, baseNueva, PERMISOS);
  assert.deepEqual(ver(f), ver(baseNueva));
  assert.equal(f.guia, "JT-1");
  assert.deepEqual(doc.cambiosDe(baseNueva, f), { guia: "JT-1" });
  // Alguien la confirmó: lo mío se descarta entero (no habría cómo guardarlo).
  assert.deepEqual(doc.fusionar(baseVieja, conCantidad(baseVieja, "AAA-1", 9), baseNueva, { editar: false }), baseNueva);
});

test("FE-2 · el aviso del 409 dice que los renglones se fusionaron con los de la otra persona", () => {
  const baseVieja = doc.formaDe(orden(1, [linea(1, "AAA-1", 2)]));
  const ana = conCantidad(baseVieja, "AAA-1", 4);
  const conOtro = doc.formaDe(orden(2, [linea(1, "AAA-1", 2), linea(2, "BBB-2", 1)]));
  const tras409 = doc.avisoDeCambio(baseVieja, ana, conOtro, PERMISOS, true);
  assert.match(tras409, /se recargó/);
  assert.match(tras409, /renglones se fusionaron con los de la otra persona/);
  assert.match(tras409, /vuelve a guardar/);
  // Por el aviso del chat (no venía de un Guardar): mismo aviso, sin «vuelve a guardar».
  assert.match(doc.avisoDeCambio(baseVieja, ana, conOtro, PERMISOS, false), /se fusionaron.*antes de guardar/);

  // Sin mezcla de renglones no se dice que la hubo.
  const soloEncabezado = doc.formaDe(orden(2, [linea(1, "AAA-1", 2)], { guia: "JT-9" }));
  assert.doesNotMatch(doc.avisoDeCambio(baseVieja, ana, soloEncabezado, PERMISOS, true), /fusionaron/);
  assert.match(doc.avisoDeCambio(baseVieja, ana, soloEncabezado, PERMISOS, true), /se conservó/);
  // Sin nada escrito, sólo se avisa que cambió.
  assert.equal(doc.avisoDeCambio(baseVieja, baseVieja, conOtro, PERMISOS, true),
               "La orden cambió mientras tanto; se recargó.");
  // Ya no se puede editar (alguien la confirmó): se dice que lo escrito no se puede guardar.
  assert.match(doc.avisoDeCambio(baseVieja, ana, conOtro, { editar: false }, true), /ya no se puede guardar/);
});

// ── BOD · la bodega va por renglón ───────────────────────────────────────────

test("BOD-1 · una elige bodegas mientras otra corrige cantidades: se conservan LAS DOS cosas", () => {
  // El borrador llegó de una venta, sin bodega. Ana le pone ENSAYO; Luis, a la
  // vez, sube la cantidad a 3. Pisar el renglón entero perdería lo de uno.
  const baseVieja = doc.formaDe(orden(1, [linea(1, "AAA-1", 2, 10, sinBodega)]));
  const ana = conBodega(baseVieja, "AAA-1", "ENSAYO");
  const baseNueva = doc.formaDe(orden(2, [linea(1, "AAA-1", 3, 10, sinBodega)]));
  const fusion = doc.fusionar(baseVieja, ana, baseNueva, PERMISOS);
  assert.deepEqual(verBodega(fusion), ["AAA-1 x3 ENSAYO"]);
  assert.deepEqual(doc.cambiosDe(baseNueva, fusion).lineas.map((l) => [l.sku, l.cantidad, l.almacen]),
                   [["AAA-1", 3, "ENSAYO"]]);
  // Cambiar la bodega NO se lee como «quité un renglón y agregué otro»: sigue siendo uno.
  assert.equal(fusion.lineas.length, 1);
});

test("BOD-2 · elegir (o cambiar) la bodega es un cambio que se guarda; sin bodega viaja `null`", () => {
  const base = doc.formaDe(orden(1, [linea(1, "AAA-1", 2, 10, sinBodega)]));
  assert.deepEqual(doc.cambiosDe(base, base), {}, "sin tocar nada no hay qué guardar");
  assert.equal(doc.datosCompletos(base).lineas[0].almacen, null, "vacío no viaja como texto vacío");
  const elegida = conBodega(base, "AAA-1", "ENSAYO");
  const put = doc.cambiosDe(base, elegida);
  assert.deepEqual(Object.keys(put).sort(), ["lineas", "total"]);
  assert.equal(put.lineas[0].almacen, "ENSAYO");
  // Y ya no existe el «Almacén» del encabezado: ni se lee ni se manda.
  assert.equal("almacen" in doc.formaVacia(), false);
  assert.equal("almacen" in doc.datosCompletos(elegida), false);
});

test("BOD-3 · un SKU repartido en DOS bodegas se fusiona por SKU + bodega (no se mezclan)", () => {
  const dos = [linea(1, "AAA-1", 2), linea(2, "AAA-1", 1, 10, { almacen: "TEX3" })];
  const viejo = doc.formaDe(orden(1, dos));
  // Yo subo el de TEX3 a 5; la otra persona sube el de ENSAYO a 4.
  const mio = { ...viejo, lineas: viejo.lineas.map((l) => (l.almacen === "TEX3" ? { ...l, cantidad: "5" } : l)) };
  const servidor = doc.formaDe(orden(2, [linea(1, "AAA-1", 4), dos[1]]));
  const fusion = doc.fusionar(viejo, mio, servidor, PERMISOS);
  assert.deepEqual(verBodega(fusion), ["AAA-1 x4 ENSAYO", "AAA-1 x5 TEX3"]);
  // Las llaves locales no se repiten (React pintaría mal dos renglones con la misma).
  assert.equal(new Set(fusion.lineas.map((l) => l.uid)).size, fusion.lineas.length);
});

test("BOD-4 · qué bodegas se ofrecen y cuál se sugiere", () => {
  // Sólo las de kubera con `admite_ov`: ni las de Odoo, ni REVISION, ni TEX3 apagada.
  assert.deepEqual(ui.bodegasDeOrdenes(CATALOGO).map((b) => b.codigo), ["ENSAYO"]);
  assert.deepEqual(ui.bodegasDeOrdenes(CATALOGO_B).map((b) => b.codigo), ["TEX3", "ENSAYO"]);
  assert.deepEqual(ui.bodegasDeOrdenes(undefined), [], "un backend que no manda el catálogo no truena");

  const elegibles = ui.bodegasDeOrdenes(CATALOGO_B);
  // La que tiene MÁS LIBRE (no más físico): 9 − 8 = 1 contra 4 − 0 = 4.
  assert.equal(ui.bodegaSugerida([saldo("TEX3", 9, 8), saldo("ENSAYO", 4)], elegibles), "ENSAYO");
  // Lo que hay en una bodega que no lleva órdenes no cuenta, aunque sea más.
  assert.equal(ui.bodegaSugerida([saldo("REVISION", 99), saldo("TEX3", 2)], elegibles), "TEX3");
  // Sin libre en ninguna y con dos donde elegir: no se adivina.
  assert.equal(ui.bodegaSugerida([saldo("TEX3", 3, 3)], elegibles), "");
  assert.equal(ui.bodegaSugerida([], elegibles), "");
  // Con UNA sola bodega no hay nada que decidir: queda preseleccionada, haya o no saldo.
  assert.equal(ui.bodegaSugerida([], ui.bodegasDeOrdenes(CATALOGO)), "ENSAYO");
  assert.equal(ui.bodegaSugerida(null, []), "");
  assert.equal(ui.rotuloBodega("ENSAYO", CATALOGO), "ENSAYO · Bodega de ensayo");
  assert.equal(ui.rotuloBodega("TEXCO", CATALOGO), "TEXCO", "si el nombre no dice más, el código solo");
  assert.equal(ui.rotuloBodega("OTRA", CATALOGO), "OTRA");
});

test("BOD-5 · el saldo tiene TRES caras y ninguna se pinta como cero", () => {
  const saldos = doc.saldosDe([saldo("ENSAYO", 5, 1)], CATALOGO);
  // El catálogo contestó: hay en ENSAYO; en las demás de kubera NO hay fila; las de Odoo ni figuran.
  assert.deepEqual(saldos, { TEX3: null, ENSAYO: saldo("ENSAYO", 5, 1), REVISION: null });
  assert.deepEqual(doc.saldoDe({ almacen: "ENSAYO", saldos }), saldo("ENSAYO", 5, 1));   // se sabe
  assert.equal(doc.saldoDe({ almacen: "TEX3", saldos }), null);            // se sabe que no hay fila
  assert.equal(doc.saldoDe({ almacen: "TEX3", saldos: {} }), undefined);   // todavía no se sabe
  assert.equal(doc.saldoDe({ almacen: "", saldos }), undefined);           // sin bodega no hay saldo que decir

  // Lo que manda el backend: el saldo en la bodega DEL renglón; `libre: null` = sin fila de saldo.
  const [conSaldo, sinFila, sinElegir] = doc.formaDe(orden(1, [
    linea(1, "AAA-1", 1, 10, { fisico: 8, apartado: 3, libre: 5 }),
    linea(2, "BBB-2", 1, 10, { fisico: null, apartado: null, libre: null }),
    linea(3, "CCC-3", 1, 10, sinBodega),
  ])).lineas;
  assert.deepEqual(doc.saldoDe(conSaldo), saldo("ENSAYO", 8, 3));
  assert.equal(doc.saldoDe(sinFila), null);
  assert.equal(doc.saldoDe(sinElegir), undefined);
  assert.equal(sinElegir.almacen, "");
});

test("BOD-6 · sólo se le pregunta al catálogo lo que de verdad falta saber", () => {
  const una = ui.bodegasDeOrdenes(CATALOGO);
  const dos = ui.bodegasDeOrdenes(CATALOGO_B);
  const guardado = doc.formaDe(orden(1, [linea(1, "AAA-1", 1)])).lineas;
  // Con una sola bodega y su saldo ya dicho por el servidor, no falta nada.
  assert.deepEqual(doc.skusSinSaldo(guardado, una), []);
  // Con dos, falta el de la otra (para poder compararlas al elegir).
  assert.deepEqual(doc.skusSinSaldo(guardado, dos), ["aaa-1"]);
  // Lo que vino de una venta no trae saldo: se pregunta, una vez por SKU.
  const deVenta = doc.aplicarVenta(doc.formaVacia(), venta({ lineas: [
    { sku: "AAA-1", cantidad: 1, precio_unitario: 1 }, { sku: "BBB-2", cantidad: 1, precio_unitario: 1 },
  ] }), una).lineas;
  assert.deepEqual(doc.skusSinSaldo(deVenta, una), ["aaa-1", "bbb-2"]);
  // Lo que el catálogo ya dijo que NO conoce no se vuelve a preguntar; y sin bodegas no hay qué preguntar.
  const fuera = [doc.renglonNuevo({ sku: "NUEVO-1", conocido: false })];
  assert.deepEqual(doc.skusSinSaldo(fuera, una), []);
  assert.deepEqual(doc.skusSinSaldo(deVenta, []), []);
});

// ── VAL · qué se puede guardar ───────────────────────────────────────────────

test("VAL-1 · la orden de marketplace son TRES piezas o ninguna, y la define el id", () => {
  const con = (mp) => ({ ...doc.formaVacia(), ...mp });
  // Con id, faltan canal y cuenta: se dice cuál, en orden.
  assert.match(doc.validar(con({ mp_orden: "PO-1" })), /canal/);
  assert.match(doc.validar(con({ mp_orden: "PO-1", mp_canal: "mercado_libre" })), /cuenta/);
  assert.equal(doc.validar(con({ mp_orden: "PO-1", mp_canal: "mercado_libre", mp_cuenta: "BEKURA" })), null);
  // Canal y cuenta SIN id (siguen al «Canal» de arriba): no es una orden de
  // marketplace. Se puede guardar, y viajan los tres en `null`, no dos de tres.
  const sinId = con({ canal: "temu", mp_canal: "temu", mp_cuenta: "TEMU" });
  assert.equal(doc.validar(sinId), null);
  assert.deepEqual(doc.mpDe(sinId), [null, null, null]);
  const alta = doc.datosCompletos(sinId);
  assert.deepEqual([alta.mp_canal, alta.mp_cuenta, alta.mp_orden], [null, null, null]);
  // Y por lo mismo no cuenta como cambio contra una orden que no tiene venta ligada.
  const base = doc.formaDe(orden(1, [], { canal: "temu" }));
  assert.deepEqual(doc.cambiosDe(base, { ...base, mp_canal: "temu", mp_cuenta: "TEMU" }), {});
  // Al escribir el id sí: viajan LOS TRES juntos.
  assert.deepEqual(doc.cambiosDe(base, { ...base, mp_canal: "temu", mp_cuenta: "TEMU", mp_orden: " PO-9 " }),
                   { mp_canal: "temu", mp_cuenta: "TEMU", mp_orden: "PO-9" });
  // Y al borrarlo, los tres en `null` (no se queda la cuenta colgando).
  const ligada = doc.formaDe(orden(1, [], { mp_canal: "temu", mp_cuenta: "TEMU", mp_orden: "PO-9" }));
  assert.deepEqual(doc.cambiosDe(ligada, { ...ligada, mp_orden: "" }),
                   { mp_canal: null, mp_cuenta: null, mp_orden: null });
});

test("VAL-2 · SKU + bodega no se repite; el mismo SKU en otra bodega sí vale", () => {
  const f = mas(mas(doc.formaVacia(), "AAA-1", 1), "aaa-1", 2);
  assert.match(doc.validar(f), /AAA-1 está dos veces en la bodega ENSAYO/i);
  assert.equal(doc.validar(mas(mas(doc.formaVacia(), "AAA-1", 1), "AAA-1", 2, "10.00", "TEX3")), null);
  assert.match(doc.validar(mas(mas(doc.formaVacia(), "AAA-1", 1, "10.00", ""), "AAA-1", 2, "10.00", "")),
               /dos veces sin bodega/);
  // Un borrador se guarda SIN bodega: se exige al confirmar, no antes.
  assert.equal(doc.validar(mas(doc.formaVacia(), "AAA-1", 1, "10.00", "")), null);
  // Lo de siempre sigue: cantidades enteras y positivas, precio no negativo.
  assert.match(doc.validar(mas(doc.formaVacia(), "AAA-1", 0)), /entero mayor que cero/);
  assert.match(doc.validar(mas(doc.formaVacia(), "AAA-1", 1.5)), /entero mayor que cero/);
  assert.match(doc.validar(mas(doc.formaVacia(), "AAA-1", 1, "-1")), /precio no puede ser negativo/);
});

// ── CONF · confirmar y apartar ───────────────────────────────────────────────

test("CONF-1 · antes de confirmar, cada renglón tiene que tener bodega (y se dice cuál no)", () => {
  const elegibles = ui.bodegasDeOrdenes(CATALOGO);
  assert.equal(doc.faltaParaConfirmar([linea(1, "AAA-1", 1), linea(2, "BBB-2", 3)], elegibles), null);
  const falta = doc.faltaParaConfirmar([linea(1, "AAA-1", 1), linea(2, "BBB-2", 3, 10, sinBodega)], elegibles);
  assert.match(falta, /Falta elegir la bodega de un renglón \(BBB-2\)/);
  assert.match(falta, /guarda y vuelve a confirmar/);
  assert.match(doc.faltaParaConfirmar([linea(1, "A", 1, 1, sinBodega), linea(2, "B", 1, 1, sinBodega)], elegibles),
               /de 2 renglones \(A, B\)/);
  // Una bodega que no lleva órdenes (REVISION, o TEX3 apagada): la base la rechazaría al apartar.
  assert.match(doc.faltaParaConfirmar([linea(1, "AAA-1", 1, 10, { almacen: "TEX3" })], elegibles),
               /TEX3 del renglón AAA-1 no admite órdenes/);
  assert.match(doc.faltaParaConfirmar([], elegibles), /no tiene renglones/);
  // Que el stock ALCANCE no se decide aquí: eso sólo lo sabe la base al apartar (y lo dice el 409).
  assert.equal(doc.faltaParaConfirmar([linea(1, "AAA-1", 99, 10, { libre: 1 })], elegibles), null);
  // Si el backend no mandó el catálogo, no se inventa un rechazo: decide él.
  assert.equal(doc.faltaParaConfirmar([linea(1, "AAA-1", 1, 10, { almacen: "TEX3" })], []), null);
});

// ── VENTA · prellenar con una venta de marketplace ───────────────────────────

test("VENTA-1 · los renglones de la venta entran SIN bodega, o con la única que hay", () => {
  const v = venta({ lineas: [{ sku: "AAA-1", cantidad: 2, precio_unitario: 100 },
                             { sku: "BBB-2", cantidad: 1, precio_unitario: 50 }] });
  assert.deepEqual(verBodega(doc.aplicarVenta(doc.formaVacia(), v, ui.bodegasDeOrdenes(CATALOGO))),
                   ["AAA-1 x2 ENSAYO", "BBB-2 x1 ENSAYO"]);
  // Con dos bodegas la decide la persona: no se le elige una a ciegas.
  assert.deepEqual(verBodega(doc.aplicarVenta(doc.formaVacia(), v, ui.bodegasDeOrdenes(CATALOGO_B))),
                   ["AAA-1 x2 ?", "BBB-2 x1 ?"]);
  assert.deepEqual(verBodega(doc.aplicarVenta(doc.formaVacia(), v)), ["AAA-1 x2 ?", "BBB-2 x1 ?"]);
  // Volver a traer la venta no le quita la bodega al renglón que ya la tenía.
  const previa = mas(doc.formaVacia(), "AAA-1", 9, "1.00", "TEX3");
  assert.deepEqual(verBodega(doc.aplicarVenta(previa, v, ui.bodegasDeOrdenes(CATALOGO_B))),
                   ["AAA-1 x2 TEX3", "BBB-2 x1 ?"]);
  // Un SKU dos veces en la venta sigue siendo un renglón con la suma.
  const repetido = venta({ lineas: [{ sku: "AAA-1", cantidad: 2, precio_unitario: 100 },
                                    { sku: "aaa-1", cantidad: 3, precio_unitario: 100 }] });
  assert.deepEqual(verBodega(doc.aplicarVenta(doc.formaVacia(), repetido, [ENSAYO])), ["AAA-1 x5 ENSAYO"]);
});

test("VENTA-2 · la venta liga canal + CUENTA + id; la cuenta que falta se pide, no se inventa", () => {
  const f = doc.aplicarVenta(doc.formaVacia(), venta(), [ENSAYO]);
  assert.deepEqual(doc.mpDe(f), ["temu", "TEMU", "PO-1"]);
  assert.equal(doc.validar(f), null);
  // El canal sólo tiene una cuenta: aunque la venta no la diga, es ésa.
  assert.equal(doc.aplicarVenta(doc.formaVacia(), venta({ cuenta: "" }), [ENSAYO]).mp_cuenta, "TEMU");
  assert.equal(ventasMp.faltaCuenta(venta({ cuenta: "" })), false);
  // Mercado Libre tiene dos: sin cuenta no se guarda, y la fila de la venta lo avisa.
  const ml = venta({ canal: "mercado_libre", cuenta: "" });
  const sinCuenta = doc.aplicarVenta(doc.formaVacia(), ml, [ENSAYO]);
  assert.equal(sinCuenta.mp_cuenta, "");
  assert.match(doc.validar(sinCuenta), /cuenta/);
  assert.equal(ventasMp.faltaCuenta(ml), true);
  assert.equal(ventasMp.faltaCuenta(venta({ canal: "mercado_libre", cuenta: "SANCORFASHION" })), false);
  assert.equal(ventasMp.origenDeVenta({ canal: "mercado_libre", cuenta: "SANCORFASHION" }), "Mercado Libre · San Corpe");
});

// ── ENT · marcar DELIVERED, por renglón ──────────────────────────────────────

/** Las filas del diálogo: por omisión, todo marcado y completo. */
const filas = (...f) => f.map(([id, cantidad, n = cantidad, marcado = true]) => ({
  id, sku: `SKU-${id}`, cantidad, marcado, n: String(n),
}));

test("ENT-1 · si todo sale completo NO se manda la lista: el servidor entrega «lo que falta»", () => {
  const plan = doc.planDeEntrega(filas([11, 2], [12, 3]), 0);
  assert.deepEqual(plan, { ok: true, lineas: undefined, salen: 5, sueltan: 0, quedan: 0, cierra: true });
});

test("ENT-2 · menos piezas en un renglón: sale con ésas y lo demás se suelta; la orden SÍ queda DELIVERED", () => {
  const plan = doc.planDeEntrega(filas([11, 2], [12, 3, 1]), 0);
  assert.equal(plan.ok, true);
  // Se mandan TODOS los marcados con su número, no sólo el que cambió.
  assert.deepEqual(plan.lineas, [{ id: 11, n: 2 }, { id: 12, n: 1 }]);
  assert.deepEqual([plan.salen, plan.sueltan, plan.quedan, plan.cierra], [3, 2, 0, true]);
});

test("ENT-3 · un renglón SIN MARCAR no sale ahora: sigue apartado y la orden queda en entrega parcial", () => {
  const plan = doc.planDeEntrega(filas([11, 2], [12, 3, 3, false]), 0);
  assert.deepEqual(plan.lineas, [{ id: 11, n: 2 }], "el que no se marcó no viaja: el servidor no lo toca");
  assert.deepEqual([plan.salen, plan.sueltan, plan.quedan, plan.cierra], [2, 0, 1, false]);
  // Soltar un renglón entero (0 piezas) mientras otro sigue pendiente es legítimo.
  const suelta = doc.planDeEntrega(filas([11, 2, 0], [12, 3, 3, false]), 0);
  assert.deepEqual(suelta.lineas, [{ id: 11, n: 0 }]);
  assert.deepEqual([suelta.salen, suelta.sueltan, suelta.cierra], [0, 2, false]);
});

test("ENT-4 · lo que no es una entrega no se manda", () => {
  const error = (plan) => { assert.equal(plan.ok, false); return plan.error; };
  assert.match(error(doc.planDeEntrega(filas([11, 2, 2, false], [12, 3, 3, false]), 0)), /Marca al menos un renglón/);
  assert.match(error(doc.planDeEntrega([], 0)), /No queda ningún renglón/);
  // Fuera de 0..cantidad, con decimales o vacío.
  for (const n of [3, -1, 1.5, ""]) {
    assert.match(error(doc.planDeEntrega(filas([11, 2, n]), 0)), /SKU-11: las piezas que salieron van de 0 a 2/);
  }
  // Cerrar la orden sin que haya salido NADA no es entregarla: es cancelarla.
  assert.match(error(doc.planDeEntrega(filas([11, 2, 0], [12, 3, 0]), 0)), /No salió ninguna pieza.*cancélala/);
  // Pero si ya había salido algo antes, soltar lo que quedaba sí cierra la orden.
  const cierre = doc.planDeEntrega(filas([12, 3, 0]), 2);
  assert.deepEqual([cierre.ok, cierre.cierra, cierre.sueltan], [true, true, 3]);
  assert.deepEqual(cierre.lineas, [{ id: 12, n: 0 }]);
});

test("ENT-5 · sólo se ofrecen los renglones que todavía no salen (uno que ya salió no se vuelve a tocar)", () => {
  const lineas = [
    linea(3, "CCC-3", 1, 10, { reservado: 1 }),
    linea(1, "AAA-1", 2, 10, { entregado: 2, entregado_at: "2026-10-06T15:00:00Z", entregado_por: "ana@kubera.mx" }),
    // Salió con CERO piezas (se soltó): también ya salió.
    linea(4, "DDD-4", 2, 10, { entregado: 0, entregado_at: "2026-10-06T15:00:00Z", entregado_por: "ana@kubera.mx" }),
    linea(2, "BBB-2", 3, 10, { reservado: 3 }),
  ];
  assert.deepEqual(doc.renglonesPendientes(lineas).map((l) => l.sku), ["BBB-2", "CCC-3"]);
  // Y en pantalla el renglón recuerda cuánto salió (0 no es «todavía no sale»).
  const [, , , soltado] = doc.formaDe(orden(5, lineas, { estado: "confirmada" })).lineas;
  assert.deepEqual([soltado.sku, soltado.entregado], ["DDD-4", 0]);
});

// ── ACC · qué botones hay ────────────────────────────────────────────────────

const nombres = (lista) => lista.map((a) => a.accion);

test("ACC-1 · borrador: «Confirmar y apartar» y cancelar; lo del modelo viejo ya no existe", () => {
  const a = doc.accionesDe(orden(1, [linea(1, "AAA-1", 1)]));
  assert.deepEqual(nombres(a.barra), ["confirmar", "cancelar"]);
  assert.equal(a.barra[0].rotulo, "Confirmar y apartar");
  assert.deepEqual(nombres(a.menu), ["borrar"]);
  // En NINGÚN estado: regresar a borrador, reintentar la reserva, mover la devolución.
  for (const estado of ["borrador", "confirmada", "entregada", "cancelada", "entregada_cancelada"]) {
    const todo = doc.accionesDe(orden(2, [linea(1, "AAA-1", 1)], {
      estado, confirmada_at: estado === "borrador" ? null : "2026-10-06T15:00:00Z",
      permisos: permisos({ entregar: true, borrar: true, salio_tarde: true, responder_salio: true }),
    }));
    for (const accion of nombres([...todo.barra, ...todo.menu])) {
      assert.ok(["confirmar", "entregar", "cancelar", "borrar", "salio_tarde"].includes(accion),
                `«${accion}» no es una acción del modelo nuevo (${estado})`);
    }
  }
});

test("ACC-2 · con la marca «el canal canceló», DELIVERED va apagado y dice por qué", () => {
  const marcada = orden(4, [linea(1, "AAA-1", 1, 10, { reservado: 1 })], {
    estado: "confirmada", confirmada_at: "2026-10-06T15:00:00Z",
    canal_cancelo_at: "2026-10-06T16:00:00Z", canal_cancelo_ref: "IN_TRANSIT",
    // Aunque el permiso llegara encendido: la base ya no deja pasar a DELIVERED.
    permisos: permisos({ entregar: true }),
  });
  const [entregar] = doc.accionesDe(marcada).barra;
  assert.equal(entregar.accion, "entregar");
  assert.equal(entregar.puede, false);
  assert.match(entregar.porque, /contestar si el paquete salió/);
  // Si el backend da su propio porqué, se dice el suyo.
  const conPorque = { ...marcada, permisos: permisos({ porque: { entregar: "Esperando el «¿salió?» de Bodega." } }) };
  assert.equal(doc.accionesDe(conPorque).barra[0].porque, "Esperando el «¿salió?» de Bodega.");
  // Sin la marca, manda el permiso.
  const [libre] = doc.accionesDe({ ...marcada, canal_cancelo_at: null, canal_cancelo_ref: null }).barra;
  assert.equal(libre.puede, true);
});

test("ACC-3 · cancelar una confirmada es de admin (si no, al menú y apagado); una entregada ya no se cancela a mano", () => {
  const confirmada = (p) => orden(3, [linea(1, "AAA-1", 1)], {
    estado: "confirmada", confirmada_at: "2026-10-06T15:00:00Z", permisos: permisos(p),
  });
  assert.deepEqual(nombres(doc.accionesDe(confirmada({ cancelar: true })).barra), ["entregar", "cancelar"]);
  const operador = doc.accionesDe(confirmada({ cancelar: false, porque: { cancelar: "Sólo un administrador." } }));
  assert.deepEqual(nombres(operador.barra), ["entregar"]);
  assert.deepEqual(nombres(operador.menu), ["cancelar", "borrar"]);
  assert.equal(operador.menu[0].porque, "Sólo un administrador.");
  const entregada = doc.accionesDe(orden(5, [linea(1, "AAA-1", 1)], {
    estado: "entregada", confirmada_at: "2026-10-06T15:00:00Z", permisos: permisos({ cancelar: true }),
  }));
  assert.deepEqual([nombres(entregada.barra), nombres(entregada.menu)], [[], ["borrar"]]);
});

test("ACC-4 · «Salió tarde» sólo en una CANCELADA que estuvo confirmada, y en el menú", () => {
  const cancelada = (mas2) => orden(6, [linea(1, "AAA-1", 1)], { estado: "cancelada", ...mas2 });
  const estuvo = doc.accionesDe(cancelada({ confirmada_at: "2026-10-06T15:00:00Z",
                                            permisos: permisos({ salio_tarde: true }) }));
  assert.deepEqual([nombres(estuvo.barra), nombres(estuvo.menu)], [[], ["salio_tarde", "borrar"]]);
  assert.equal(estuvo.menu[0].rotulo, "Salió tarde");
  assert.equal(estuvo.menu[0].puede, true);
  // Quien no es admin la ve apagada (no escondida).
  const operador = doc.accionesDe(cancelada({ confirmada_at: "2026-10-06T15:00:00Z" }));
  assert.equal(operador.menu[0].puede, false);
  // La que se canceló en BORRADOR nunca apartó nada: no pudo salir.
  assert.deepEqual(nombres(doc.accionesDe(cancelada({ permisos: permisos({ salio_tarde: true }) })).menu), ["borrar"]);
  // Una borrada no ofrece nada.
  assert.deepEqual(doc.accionesDe(cancelada({ borrada_at: "2026-10-06T17:00:00Z" })), { barra: [], menu: [] });
});

// ── MOT · el motivo que exige la base ────────────────────────────────────────

test("MOT-1 · cancelar una confirmada pide 5 caracteres; borrar, 10; y no se manda de menos", () => {
  const borrador = orden(1, []);
  const confirmada = orden(2, [], { estado: "confirmada", confirmada_at: "2026-10-06T15:00:00Z" });
  assert.equal(doc.minimoDeMotivo("cancelar", borrador), ui.MOTIVO_MINIMO);
  assert.equal(doc.minimoDeMotivo("cancelar", confirmada), 5);
  assert.equal(doc.minimoDeMotivo("borrar", borrador), 10);
  assert.equal(doc.minimoDeMotivo("borrar", confirmada), 10);
  // Lo que cuenta es el texto SIN los espacios de las orillas (es lo que viaja).
  assert.equal(ui.faltaDeMotivo("   error   ", 10), 5);
  assert.equal(ui.faltaDeMotivo("duplicada!", 10), 0);
  assert.equal(ui.faltaDeMotivo("          ", 10), 10, "puros espacios no son un motivo");
  assert.equal(ui.faltaDeMotivo("mal", 5), 2);
  assert.equal(ui.faltaDeMotivo("ya"), 1);
});

// ── LEC · sólo el borrador se edita ──────────────────────────────────────────

test("LEC-1 · fuera de borrador NADIE edita, ni con el permiso encendido", () => {
  const con = (mas2) => orden(1, [], { permisos: permisos({ editar: true }), ...mas2 });
  assert.equal(doc.seEdita(con({})), true);
  assert.equal(doc.seEdita(con({ permisos: permisos({ editar: false }) })), false);
  for (const estado of ["confirmada", "entregada", "cancelada", "entregada_cancelada"]) {
    assert.equal(doc.seEdita(con({ estado })), false, estado);
  }
  assert.equal(doc.seEdita(con({ borrada_at: "2026-10-06T17:00:00Z" })), false);
  // El envío a FULL se captura en Crear FULL: aquí es de sólo lectura aun en borrador.
  assert.equal(doc.seEdita(con({ tipo: "full" })), false);

  assert.equal(doc.notaDeLectura(con({})), null);
  assert.match(doc.notaDeLectura(con({ estado: "confirmada" })),
               /Una orden confirmada ya no se modifica: si tiene un error, un administrador la borra y se captura de nuevo/);
  // Lo que YA SALIÓ tiene su propia nota: borrar no regresa las piezas, y no se recaptura.
  for (const estado of ["entregada", "entregada_cancelada"]) {
    assert.match(doc.notaDeLectura(con({ estado })), /ya salió de la bodega: no se modifica\. Borrarla no regresa las piezas al saldo/, estado);
    assert.doesNotMatch(doc.notaDeLectura(con({ estado })), /la borra y se captura de nuevo/, estado);
  }
  assert.match(doc.notaDeLectura(con({ estado: "cancelada" })), /cancelada ya no se modifica/);
  assert.match(doc.notaDeLectura(con({ tipo: "full" })), /envío a FULL/);
  assert.equal(doc.notaDeLectura(con({ estado: "confirmada", borrada_at: "2026-10-06T17:00:00Z" })), null,
               "de la borrada ya habla su propio aviso");
});

// ── PDF · adjuntar ───────────────────────────────────────────────────────────

test("PDF-1 · sin bucket no se sube, y el porqué es el del módulo (no «tu usuario no puede»)", () => {
  const o = (p) => ({ permisos: permisos(p) });
  const sinBucket = { archivos: { disponible: false, motivo: "Falta crear el bucket ordenes-venta." } };
  const conBucket = { archivos: { disponible: true, motivo: null } };
  assert.deepEqual(archivos.subidaDe(o({ subir_archivo: true }), sinBucket),
                   { puede: false, porque: "Falta crear el bucket ordenes-venta." });
  assert.match(archivos.subidaDe(o({ subir_archivo: true }), { archivos: { disponible: false, motivo: null } }).porque,
               /falta crear el almacenamiento/);
  assert.deepEqual(archivos.subidaDe(o({ subir_archivo: false, porque: { subir_archivo: "Sólo lectura." } }), conBucket),
                   { puede: false, porque: "Sólo lectura." });
  assert.deepEqual(archivos.subidaDe(o({ subir_archivo: true }), conBucket), { puede: true, porque: "" });
});

test("PDF-2 · el tipo es un catálogo cerrado y sin valor por omisión; y sólo entran PDF de hasta 15 MB", () => {
  assert.deepEqual(archivos.TIPOS_ARCHIVO.map((t) => t.id).sort(), ["comprobante", "envio_full", "factura"]);
  assert.equal(archivos.rotuloTipoArchivo("envio_full"), "Envío a FULL");
  assert.equal(archivos.rotuloTipoArchivo("guia"), "guia", "un tipo desconocido se enseña, no se esconde");
  assert.equal(archivos.rechazo({ name: "factura.pdf", type: "application/pdf", size: 1024 }), null);
  assert.match(archivos.rechazo({ name: "foto.png", type: "image/png", size: 1024 }), /no es un PDF/);
  assert.match(archivos.rechazo({ name: "disfraz.pdf", type: "image/png", size: 1024 }), /no es un PDF de verdad/);
  assert.match(archivos.rechazo({ name: "vacio.pdf", type: "", size: 0 }), /está vacío/);
  assert.match(archivos.rechazo({ name: "enorme.pdf", type: "application/pdf", size: 16 * 1024 * 1024 }), /máximo es 15 MB/);
});

// ── FE-1 · renglones de la venta sin SKU ─────────────────────────────────────

test("FE-1 · la venta con renglones sin SKU deja la advertencia ESCRITA en la orden", () => {
  const v = venta({ renglones_sin_sku: 1 });
  const f = doc.aplicarVenta(doc.formaVacia(), v, [ENSAYO]);
  assert.deepEqual(ver(f), ["AAA-1 x1 @100.00"]);
  assert.equal(f.descripcion, doc.notaSinSku(1));
  assert.match(f.descripcion, /1 renglón\(es\) SIN SKU/);
  // La nota viaja en el alta: es lo que lee almacén cuando el aviso de pantalla ya no está.
  assert.equal(doc.datosCompletos(f).descripcion, doc.notaSinSku(1));
});

test("FE-1 · no pisa una descripción capturada, ni inventa la nota si todo trae SKU", () => {
  const escrita = { ...doc.formaVacia(), descripcion: "Entregar en mostrador" };
  assert.equal(doc.aplicarVenta(escrita, venta({ renglones_sin_sku: 2 }), [ENSAYO]).descripcion, "Entregar en mostrador");
  assert.equal(doc.aplicarVenta(doc.formaVacia(), venta(), [ENSAYO]).descripcion, "");
  assert.equal(doc.aplicarVenta(doc.formaVacia(), venta({ renglones_sin_sku: 0 }), [ENSAYO]).descripcion, "");
});

test("FE-1 · una venta SÓLO con renglones sin SKU no toca los renglones capturados, pero avisa", () => {
  const previa = mas(doc.formaVacia(), "BBB-2", 7, "5.00");
  const f = doc.aplicarVenta(previa, venta({ lineas: [], piezas: 2, renglones_sin_sku: 2 }), [ENSAYO]);
  assert.deepEqual(ver(f), ["BBB-2 x7 @5.00"]);
  assert.equal(f.descripcion, doc.notaSinSku(2));
});

// ── FE-6 y MOV-3 · la clave de idempotencia va atada a lo que se manda ───────

test("FE-6 · reintentar LO MISMO reusa la clave; corregir el formulario estrena otra", () => {
  let n = 0;
  const generar = () => `clave-${++n}`;
  const forma = mas({ ...doc.formaVacia(), cliente: "Temu" }, "AAA-1", 3);
  const huella = (f) => JSON.stringify(doc.datosCompletos(f));

  const primero = ui.claveDeIntento(null, huella(forma), generar);
  // Falló (o se perdió la respuesta) y la persona vuelve a dar «Crear borrador» sin tocar nada.
  const reintento = ui.claveDeIntento(primero, huella(forma), generar);
  assert.equal(reintento.clave, primero.clave);
  // Ahora corrige la cantidad: es OTRO envío. Con la clave vieja el servidor
  // contestaría la orden del primer intento y la corrección se perdería.
  const corregida = conCantidad(forma, "AAA-1", 9);
  const tercero = ui.claveDeIntento(reintento, huella(corregida), generar);
  assert.notEqual(tercero.clave, primero.clave);
  // Y reintentar la corregida vuelve a reusar la suya.
  assert.equal(ui.claveDeIntento(tercero, huella(corregida), generar).clave, tercero.clave);
  assert.equal(n, 2);
  // Cambiar sólo el cliente también cuenta como otro envío. Y cambiar sólo la BODEGA, igual.
  assert.notEqual(ui.claveDeIntento(primero, huella({ ...forma, cliente: "Amazon" }), generar).clave, primero.clave);
  assert.notEqual(ui.claveDeIntento(primero, huella(conBodega(forma, "AAA-1", "TEX3")), generar).clave, primero.clave);
  // Lo que NO viaja no cambia la huella: la llave local del renglón ni lo que se sabe de su saldo.
  const mismoEnvio = { ...forma, lineas: forma.lineas.map((l) => ({ ...l, uid: "otra", saldos: { ENSAYO: saldo("ENSAYO", 3) } })) };
  assert.equal(huella(mismoEnvio), huella(forma));
});

test("MOV-3 · el mismo texto reintentado lleva la MISMA clave; el texto corregido, otra", () => {
  const a = ui.claveDeIntento(null, "ya salió la guía");
  assert.match(a.clave, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(ui.claveDeIntento(a, "ya salió la guía").clave, a.clave);
  assert.notEqual(ui.claveDeIntento(a, "ya salió la guía JT-1").clave, a.clave);
  // Tras un envío logrado el chat suelta la clave (null): repetir el texto es un mensaje NUEVO.
  assert.notEqual(ui.claveDeIntento(null, "ya salió la guía").clave, a.clave);
});

test("MOV-3 · `nuevaClave` no depende de `crypto.randomUUID` (no existe fuera de https)", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
    const vistas = new Set(Array.from({ length: 50 }, () => ui.nuevaClave()));
    assert.equal(vistas.size, 50);
    for (const c of vistas) assert.match(c, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  } finally {
    if (original) Object.defineProperty(globalThis, "crypto", original);
  }
});

// ── MOV-1 · la relectura insiste hasta alcanzar la rev avisada ───────────────

/** Un documento de mentira: `lecturas` es lo que contesta cada relectura (una rev, o "fallo"). */
function simulacro(revInicial, lecturas, { ocupadoTrasLaEspera = false, seCierraEnLaLectura = 0 } = {}) {
  const s = { rev: revInicial, abierto: true, leidas: 0, esperas: [], avisos: 0, apuntada: 0 };
  const args = {
    releer: async () => {
      const r = lecturas[Math.min(s.leidas, lecturas.length - 1)];
      s.leidas += 1;
      if (s.leidas === seCierraEnLaLectura) s.abierto = false;
      if (r === "fallo") return "fallo";
      if (r <= s.rev) return "igual";
      s.rev = r;
      return "cambio";
    },
    rev: () => (s.abierto ? s.rev : null),
    ocupado: () => ocupadoTrasLaEspera,
    apuntar: (rev) => { s.apuntada = rev; },
    alCambio: () => { s.avisos += 1; },
    esperar: async (ms) => { s.esperas.push(ms); },
  };
  return { s, args };
}

test("MOV-1 · la carrera: la lectura en vuelo llega vieja y se vuelve a leer hasta la rev avisada", async () => {
  // El chat avisa rev 7; la relectura que ya iba en vuelo trae la 6.
  const { s, args } = simulacro(5, [6, 7]);
  await doc.releerHasta({ objetivo: 7, intentos: 3, ...args });
  assert.equal(s.rev, 7, "el documento no se queda en la rev intermedia");
  assert.equal(s.leidas, 2);
  assert.deepEqual(s.esperas, [0], "si sólo se quedó corta, se relee enseguida");
  assert.equal(s.avisos, 2);
});

test("MOV-1 · la relectura que FALLA se reintenta a 1.5 s (antes nadie reintentaba)", async () => {
  const { s, args } = simulacro(3, ["fallo", 4]);
  await doc.releerHasta({ objetivo: 4, intentos: 3, ...args });
  assert.equal(s.rev, 4);
  assert.deepEqual(s.esperas, [1500]);
});

test("MOV-1 · se rinde tras 3 intentos más (el chat volverá a avisar), y no relee de más", async () => {
  const caido = simulacro(3, ["fallo"]);
  await doc.releerHasta({ objetivo: 4, intentos: 3, ...caido.args });
  assert.equal(caido.s.leidas, 4, "la primera + 3 intentos");
  assert.equal(caido.s.rev, 3);

  // Llegó a la primera: una sola lectura, sin esperas.
  const alDia = simulacro(3, [4]);
  await doc.releerHasta({ objetivo: 4, intentos: 3, ...alDia.args });
  assert.deepEqual([alDia.s.leidas, alDia.s.esperas.length], [1, 0]);
  // Sin objetivo (0) se comporta como antes: una relectura y ya.
  const sinObjetivo = simulacro(3, ["fallo"]);
  await doc.releerHasta({ objetivo: 0, intentos: 3, ...sinObjetivo.args });
  assert.equal(sinObjetivo.s.leidas, 1);
});

test("MOV-1 · con una acción mía en vuelo no se relee: se apunta; y un documento cerrado no se toca", async () => {
  const ocupado = simulacro(5, [5, 7], { ocupadoTrasLaEspera: true });
  await doc.releerHasta({ objetivo: 7, intentos: 3, ...ocupado.args });
  assert.deepEqual([ocupado.s.leidas, ocupado.s.apuntada], [1, 7]);

  const cerrado = simulacro(5, [6, 7], { seCierraEnLaLectura: 1 });
  await doc.releerHasta({ objetivo: 7, intentos: 3, ...cerrado.args });
  assert.deepEqual([cerrado.s.leidas, cerrado.s.avisos], [1, 0]);
});

// ── FE-7 · el chip del apartado ──────────────────────────────────────────────

test("FE-7 · una confirmada que se BORRÓ dice «liberado»; el chip del apartado se deriva del estado", () => {
  // Lo que contesta el backend para una confirmada que se borró: ya no aparta nada.
  const borrada = { estado: "confirmada", renglones_entregados: 0, borrada_at: "2026-10-02T18:00:00Z" };
  assert.equal(ui.reservaDe(borrada), "liberada");
  assert.equal(ui.ROTULO_RESERVA[ui.reservaDe(borrada)], "Apartado liberado");
  // La misma, viva: su stock está apartado (se aparta todo o nada: ya no hay «parcial» ni «sin stock»).
  assert.equal(ui.reservaDe({ ...borrada, borrada_at: null }), "apartada");
  // Ya salieron algunos renglones y otros siguen apartados: entrega parcial.
  assert.equal(ui.reservaDe({ estado: "confirmada", renglones_entregados: 1 }), "entrega_parcial");
  assert.equal(ui.ROTULO_RESERVA.entrega_parcial, "Entrega parcial");
  assert.equal(ui.reservaDe({ estado: "borrador" }), "sin_apartar");
  assert.equal(ui.reservaDe({ estado: "borrador", borrada_at: "2026-10-02T18:00:00Z" }), "sin_apartar");
  assert.equal(ui.reservaDe({ estado: "entregada" }), "surtida");
  assert.equal(ui.reservaDe({ estado: "entregada_cancelada" }), "surtida");
  assert.equal(ui.reservaDe({ estado: "cancelada" }), "liberada");
  // Los cinco estados que existen, y ninguno del modelo viejo.
  assert.deepEqual(Object.keys(ui.ROTULO_RESERVA).sort(),
                   ["apartada", "entrega_parcial", "liberada", "sin_apartar", "surtida"]);
});

test("FE-7 · lo que va DENTRO del chip de estado es una palabra, no la frase que lo explica", () => {
  // El 6-oct la frase de ayuda de «confirmada» y «cancelada» acabó en el rótulo:
  // el chip (y la traza) habrían pintado un párrafo.
  assert.deepEqual(ui.ROTULO_ESTADO, {
    borrador: "BORRADOR", confirmada: "CONFIRMADA", entregada: "DELIVERED", cancelada: "CANCELADO",
    entregada_cancelada: "DELIVERED but CANCELLED",
  });
  for (const [estado, ayuda] of Object.entries(ui.AYUDA_ESTADO)) {
    assert.ok(ayuda.length > ui.ROTULO_ESTADO[estado].length, `la ayuda de «${estado}» dice más que su rótulo`);
    assert.doesNotMatch(ayuda, /reserv/i, `«${estado}»: ya no se «reserva», se aparta`);
  }
});

// ── FE-8 · qué hace Enter en el buscador de productos ────────────────────────

const op = (sku, nombre = sku) => ({ sku, nombre, existencias: [saldo("ENSAYO", 5)] });

test("FE-8 · Enter con opciones a la vista elige la RESALTADA (la primera), no el texto tecleado", () => {
  const opciones = [op("TEC-0377-S24P", "Funda magnética"), op("TEC-0378-S24", "Funda rígida")];
  const base = { buscable: true, abierto: true, alDia: true, activo: -1, opciones, q: "funda", ofreceManual: true };
  // Lo que se VE resaltado es lo que Enter elige.
  assert.equal(buscador.filaResaltada(base), 0);
  assert.deepEqual(buscador.decidirEnter(base), { tipo: "elegir", indice: 0 });
  // Medio SKU: igual, nunca «TEC-03» fuera del catálogo.
  assert.deepEqual(buscador.decidirEnter({ ...base, q: "TEC-03" }), { tipo: "elegir", indice: 0 });
  // Con las flechas o el cursor en otra, gana ésa.
  assert.deepEqual(buscador.decidirEnter({ ...base, activo: 1 }), { tipo: "elegir", indice: 1 });
  // La coincidencia EXACTA se resalta aunque no sea la primera.
  const exacta = { ...base, q: "tec-0378-s24", ofreceManual: false };
  assert.equal(buscador.filaResaltada(exacta), 1);
  assert.deepEqual(buscador.decidirEnter(exacta), { tipo: "elegir", indice: 1 });
});

test("FE-8 · el texto «tal cual» sólo entra desde su fila o cuando no hay opciones", () => {
  const opciones = [op("TEC-0377-S24P")];
  const base = { buscable: true, abierto: true, alDia: true, activo: -1, opciones, q: "funda", ofreceManual: true };
  assert.deepEqual(buscador.decidirEnter({ ...base, activo: 1 }), { tipo: "manual" }, "su fila, resaltada");
  assert.deepEqual(buscador.decidirEnter({ ...base, opciones: [] }), { tipo: "manual" }, "sin opciones");
  assert.equal(buscador.filaResaltada({ ...base, opciones: [] }), -1);
  // Menos de dos letras: no hace nada. Sin respuesta todavía: espera.
  assert.deepEqual(buscador.decidirEnter({ ...base, buscable: false }), { tipo: "nada" });
  assert.deepEqual(buscador.decidirEnter({ ...base, alDia: false, opciones: [] }), { tipo: "esperar" });
  assert.equal(buscador.filaResaltada({ ...base, alDia: false }), -1);
  // Lista CERRADA (Esc) con el texto todavía escrito: Enter la reabre, no elige lo que no se ve…
  assert.deepEqual(buscador.decidirEnter({ ...base, abierto: false }), { tipo: "mostrar" });
  assert.deepEqual(buscador.decidirEnter({ ...base, abierto: false, opciones: [] }), { tipo: "mostrar" });
  // …salvo la coincidencia exacta, que no tiene pierde.
  assert.deepEqual(buscador.decidirEnter({ ...base, abierto: false, q: "tec-0377-s24p", ofreceManual: false }),
                   { tipo: "elegir", indice: 0 });
});

test("FE-8 · el Enter que llegó ANTES que la respuesta no elige a ciegas", () => {
  const opciones = [op("VIA-0024-NEG"), op("VIA-0024-BLA")];
  // Un lector de códigos: SKU completo + Enter → la exacta entra sola.
  assert.deepEqual(buscador.decidirEnterPendiente({ opciones, q: "via-0024-bla" }), { tipo: "elegir", indice: 1 });
  // Sin exacta pero con opciones que nadie vio: se enseñan, no se agrega nada.
  assert.deepEqual(buscador.decidirEnterPendiente({ opciones, q: "VIA-0024" }), { tipo: "mostrar" });
  // Nada en el catálogo: entra tal cual (marcado), como siempre.
  assert.deepEqual(buscador.decidirEnterPendiente({ opciones: [], q: "NUEVO-1" }), { tipo: "manual" });
});

// ── PED-3 y PED-4 · la bitácora en el chat ───────────────────────────────────

// (El catálogo de eventos, la mini-tabla de renglones y la traza se prueban en
// `chat_traza.prueba.cjs`, contra el CHECK de la 0064.)
const mensaje = (masCampos = {}) => ({
  id: 9, orden_id: 1, tipo: "sistema", evento: "borrador_guardado", cuerpo: "Borrador guardado · guía, paquetería",
  datos: { op: "x", cambios: { guia: [null, "JT123"], paqueteria: [null, "J&T"] } },
  autor: "automatico", autor_nombre: null, via: "automatico", creado_at: "2026-10-02T18:00:00Z", ...masCampos,
});

test("PED-3 · la bitácora se lee bien: firma «Automático» y cada campo con su antes → después", () => {
  const m = mensaje();
  assert.equal(chat.firma(m), "Automático", "ni «automatico» ni «Automático · automático»");
  const cambios = chat.cambiosDe(m.datos);
  assert.deepEqual(cambios.map((c) => [chat.ROTULO_CAMPO[c.campo], c.antes, c.despues]),
                   [["Guía", null, "JT123"], ["Paquetería", null, "J&T"]]);
  // La bodega ya no es del encabezado (va por renglón): no hay «Almacén» que rotular.
  assert.equal(chat.ROTULO_CAMPO.almacen, undefined);
  // Una persona por el panel firma con su nombre; por la API, con la vía.
  assert.equal(chat.firma(mensaje({ autor: "ana@kubera.mx", autor_nombre: "Ana", via: "panel" })), "Ana");
  assert.equal(chat.firma(mensaje({ autor: "ana@kubera.mx", autor_nombre: "Ana", via: "claude" })), "Ana · por Claude");
});

test("PED-4 · los eventos de devolución tienen cada uno su icono y su rótulo, como todos", () => {
  // El «devolucion» único del 2-oct se partió en cinco (catálogo de la 0064).
  const devoluciones = ["devolucion_esperada", "devolucion_recibida", "devolucion_aprobada",
                        "devolucion_merma", "devolucion_cerrada"];
  for (const d of devoluciones) assert.ok(chat.EVENTOS[d], `«${d}» está en el mapa`);
  assert.equal(new Set(devoluciones.map((d) => chat.EVENTOS[d].icono)).size, 5, "ninguna comparte icono");
  assert.equal(chat.EVENTOS.devolucion, undefined);
  for (const [evento, e] of Object.entries(chat.EVENTOS)) {
    assert.ok(e.icono && e.tono && e.rotulo, `«${evento}» trae icono, tono y rótulo`);
  }
  // Los tres estados de la devolución tienen su explicación (la usan el documento y la lista).
  assert.deepEqual(Object.keys(doc.AYUDA_DEVOLUCION).sort(), ["cerrada", "pendiente", "recibida"]);
});

// ── Segunda revisión (6-oct-2026) ─────────────────────────────────────────────

test("REV2 FE-01 · «ya salió algo»: entregada, DELIVERED but CANCELLED y la entrega parcial", () => {
  const con = (mas2) => orden(1, [linea(1, "AAA-1", 2)], mas2);
  assert.equal(doc.yaSalioAlgo(con({ estado: "confirmada" })), false);
  assert.equal(doc.yaSalioAlgo(con({ estado: "cancelada" })), false);
  assert.equal(doc.yaSalioAlgo(con({ estado: "entregada" })), true);
  assert.equal(doc.yaSalioAlgo(con({ estado: "entregada_cancelada" })), true);
  assert.equal(doc.yaSalioAlgo(con({ estado: "confirmada", piezas_entregadas: 1 })), true, "entrega parcial");
});

test("REV2 FE-03 · con la marca del canal, «Cancelar orden» va al menú y APAGADO (también para admin)", () => {
  const marcada = orden(4, [linea(1, "AAA-1", 1, 10, { reservado: 1 })], {
    estado: "confirmada", confirmada_at: "2026-10-06T15:00:00Z",
    canal_cancelo_at: "2026-10-06T16:00:00Z", canal_cancelo_ref: "IN_TRANSIT",
    permisos: permisos({ entregar: true, cancelar: true, responder_salio: true }),
  });
  const a = doc.accionesDe(marcada);
  assert.deepEqual([nombres(a.barra), nombres(a.menu)], [["entregar"], ["cancelar", "borrar"]]);
  assert.equal(a.menu[0].puede, false, "el permiso llegó encendido y aun así no se ofrece");
  assert.match(a.menu[0].porque, /primero hay que contestar si el paquete salió/);
  // Sin la marca, el admin la conserva en la barra.
  const libre = doc.accionesDe({ ...marcada, canal_cancelo_at: null, canal_cancelo_ref: null });
  assert.deepEqual(nombres(libre.barra), ["entregar", "cancelar"]);
  assert.equal(libre.barra[1].puede, true);
});

test("REV2 FE-04 · «Salió tarde» cuenta sólo los renglones que seguían pendientes", () => {
  // 3 + 2 piezas; el renglón A se cerró con 0 en una entrega parcial y luego se canceló.
  const lineas = [
    linea(1, "AAA-1", 3, 10, { entregado: 0, entregado_at: "2026-10-06T16:00:00Z" }),
    linea(2, "BBB-2", 2),
  ];
  assert.equal(doc.piezasPorSalir(lineas), 2, "no las 5 de `orden.piezas`");
  assert.equal(doc.piezasPorSalir([linea(1, "AAA-1", 3), linea(2, "BBB-2", 2)]), 5);
  assert.equal(doc.piezasPorSalir([]), 0);
});

test("REV2 FE-02 / FE-05 / FE-06 · lo que decide la lista: quién espera el «¿salió?», el módulo sin leer y el barrido", () => {
  const fila = (mas2) => ({ estado: "confirmada", borrada_at: null, canal_cancelo_at: "2026-10-06T16:00:00Z", ...mas2 });
  assert.equal(ui.esperaSalio(fila({})), true);
  assert.equal(ui.esperaSalio(fila({ canal_cancelo_at: null })), false);
  // La marca se queda de recuerdo, pero la pregunta ya no está abierta.
  assert.equal(ui.esperaSalio(fila({ estado: "cancelada" })), false);
  assert.equal(ui.esperaSalio(fila({ estado: "entregada_cancelada" })), false);
  assert.equal(ui.esperaSalio(fila({ borrada_at: "2026-10-06T17:00:00Z" })), false);

  // `ok: false` sin ser por las migraciones es «no pude leer», no «todo apagado».
  assert.equal(ui.moduloSinLeer(null), false);
  assert.equal(ui.moduloSinLeer({ ok: true, falta_migracion: false }), false);
  assert.equal(ui.moduloSinLeer({ ok: false, falta_migracion: true }), false, "eso lo dice el aviso de migraciones");
  assert.equal(ui.moduloSinLeer({ ok: false, falta_migracion: false }), true);

  // «Revisar cancelaciones» sólo con la bandera encendida (en modo prueba el backend no revisa nada).
  const estado = (mas2) => ({ ok: true, falta_migracion: false, habilitado: true, ...mas2 });
  assert.equal(ui.porqueNoRevisar(estado({})), null);
  assert.equal(ui.porqueNoRevisar(null), null);
  assert.match(ui.porqueNoRevisar(estado({ habilitado: false })), /Modo prueba: el barrido .* sólo corre con la bandera «ordenes_venta» encendida/);
  // Sin haber leído, no se afirma «modo prueba».
  assert.match(ui.porqueNoRevisar(estado({ ok: false, habilitado: false })), /No se pudo leer el estado del módulo/);
});

test("REV2 FE-07 · borrar manda `rev` y `motivo` en el CUERPO (JSON), no en la dirección", async () => {
  const lib = require(require("path").join(__dirname, "..", "..", "..", "lib", "api"));
  const real = lib.fetchSesion;
  const vistas = [];
  lib.fetchSesion = async (...args) => { vistas.push(args); return { ok: true, json: async () => ({ ok: true }) }; };
  try {
    await cargar("api").borrarOrden(31, 4, "la pidió duplicada el cliente, tel. 55…");
  } finally {
    lib.fetchSesion = real;
  }
  assert.equal(vistas.length, 1);
  const [url, init, cabeceras] = vistas[0];
  assert.match(url, /\/api\/ordenes-venta\/31$/, "sin query string: el motivo no queda en el registro de accesos");
  assert.equal(init.method, "DELETE");
  assert.deepEqual(JSON.parse(init.body), { rev: 4, motivo: "la pidió duplicada el cliente, tel. 55…" });
  // El Content-Type va en el TERCER argumento: `fetchSesion` pisa `init.headers`.
  assert.deepEqual(cabeceras, { "Content-Type": "application/json" });
});
