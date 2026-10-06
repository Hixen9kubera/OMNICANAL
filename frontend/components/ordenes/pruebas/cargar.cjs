/**
 * Cargador de las pruebas de Inventario → ÓRDENES DE VENTA.
 *
 * El frontend no tiene corredor de pruebas (ni jest ni vitest) y no se agregan
 * dependencias por un módulo. Lo que sí hay es `typescript` (ya es dependencia
 * de desarrollo) y `node`: este archivo le enseña a `require` a leer `.ts` y
 * `.tsx` —transpilándolos al vuelo, sin revisar tipos: eso es de `tsc`— y a
 * resolver el alias `@/` del tsconfig. Con eso las pruebas importan los
 * módulos REALES, no una copia recortada que se desfasa.
 *
 * Sólo sirve para lógica PURA (lo que no toca el DOM ni la red): los
 * componentes se cargan pero no se montan.
 *
 * `import.meta.url` (el worker de la traza) no existe en CommonJS: se cambia
 * por un texto fijo. Nada de lo que se prueba aquí lo usa.
 */
const fs = require("fs");
const path = require("path");
const Module = require("module");

const FRONTEND = path.resolve(__dirname, "..", "..", "..");
const ts = require(path.join(FRONTEND, "node_modules", "typescript"));

const OPCIONES = {
  module: ts.ModuleKind.CommonJS,
  target: ts.ScriptTarget.ES2020,
  jsx: ts.JsxEmit.ReactJSX,
  esModuleInterop: true,
};

function compilar(modulo, archivo) {
  const fuente = fs.readFileSync(archivo, "utf8").replace(/import\.meta\.url/g, '"file:///prueba"');
  const js = ts.transpileModule(fuente, { compilerOptions: OPCIONES, fileName: archivo }).outputText;
  modulo._compile(js, archivo);
}
Module._extensions[".ts"] = compilar;
Module._extensions[".tsx"] = compilar;

// El alias `@/…` del tsconfig apunta a la raíz del frontend.
const resolver = Module._resolveFilename;
Module._resolveFilename = function resolverConAlias(pedido, ...resto) {
  const real = pedido.startsWith("@/") ? path.join(FRONTEND, pedido.slice(2)) : pedido;
  return resolver.call(this, real, ...resto);
};

/** Carga un módulo de `components/ordenes/` por su nombre («OrdenDocumento», «ui»…). */
module.exports = (nombre) => require(path.join(__dirname, "..", nombre));
