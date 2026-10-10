/**
 * Un DOM MÍNIMO para MONTAR de verdad el documento de una orden en node
 * (`documento_vivo.prueba.cjs`): sin navegador y sin jsdom, que el frontend no
 * trae y por un módulo no se agrega.
 *
 * Por qué existe: lo que más caro sale del documento pasa AL DAR CLIC y AL
 * CONTESTAR EL SERVIDOR —con qué `rev` viaja una acción, qué queda abierto tras
 * un 409, qué se vuelve a preguntar—, y eso no lo ven ni la lógica pura
 * (`ordenes.prueba.cjs`) ni el pintado en el servidor (`documento_pinta.prueba.cjs`,
 * donde los efectos no corren y nadie da clic). Nació en la tercera revisión
 * (9-oct-2026) para reproducir en pantalla que «Marcar DELIVERED» registraba
 * piezas que quien daba clic no había visto.
 *
 * Qué trae: SÓLO lo que React 18 (react-dom/client, build de producción) y este
 * módulo tocan: árbol de nodos, atributos, estilo, eventos con captura y
 * burbuja, controles de formulario y un `querySelectorAll` para los selectores
 * que usa el módulo. No pinta ni mide nada (los altos valen 0): sirve para
 * saber QUÉ está montado, qué texto dice y qué manda un clic. Si el documento
 * empieza a usar una API del navegador que aquí no está, la prueba truena
 * diciendo cuál: se agrega aquí, no se le quita a la pantalla.
 */
"use strict";

const HTML = "http://www.w3.org/1999/xhtml";

class Evento {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = init.bubbles !== false;
    this.cancelable = init.cancelable !== false;
    this.defaultPrevented = false;
    this.isTrusted = true;
    this.timeStamp = Date.now();
    this.target = null;
    this.currentTarget = null;
    this.eventPhase = 0;
    this._alto = false;
    this._altoYa = false;
    // Lo que React lee de un MouseEvent / KeyboardEvent.
    Object.assign(this, { button: 0, buttons: 0, detail: 1, ctrlKey: false, shiftKey: false, altKey: false,
                          metaKey: false, clientX: 0, clientY: 0, pageX: 0, pageY: 0, screenX: 0, screenY: 0,
                          movementX: 0, movementY: 0, relatedTarget: null, view: globalThis },
                  init.extra || {});
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this._alto = true; }
  stopImmediatePropagation() { this._alto = true; this._altoYa = true; }
  getModifierState() { return false; }
}

class Blanco {
  constructor() { this._oyentes = []; }
  addEventListener(type, fn, op) {
    if (!fn) return;
    const capture = typeof op === "boolean" ? op : !!(op && op.capture);
    if (op && typeof op === "object") void op.passive;   // la prueba de «passive» de React lee esto
    if (this._oyentes.some((o) => o.type === type && o.fn === fn && o.capture === capture)) return;
    this._oyentes.push({ type, fn, capture, once: !!(op && typeof op === "object" && op.once) });
  }
  removeEventListener(type, fn, op) {
    const capture = typeof op === "boolean" ? op : !!(op && op.capture);
    this._oyentes = this._oyentes.filter((o) => !(o.type === type && o.fn === fn && o.capture === capture));
  }
  _padreEvento() { return null; }
  dispatchEvent(ev) {
    ev.target = this;
    const camino = [];
    for (let n = this._padreEvento(); n; n = n._padreEvento()) camino.push(n);
    const llamar = (nodo, fase, soloCaptura) => {
      for (const o of [...nodo._oyentes]) {
        if (o.type !== ev.type) continue;
        if (soloCaptura !== undefined && o.capture !== soloCaptura) continue;
        if (!nodo._oyentes.includes(o)) continue;
        ev.currentTarget = nodo;
        ev.eventPhase = fase;
        if (o.once) nodo.removeEventListener(o.type, o.fn, o.capture);
        if (typeof o.fn === "function") o.fn.call(nodo, ev);
        else if (o.fn && typeof o.fn.handleEvent === "function") o.fn.handleEvent(ev);
        if (ev._altoYa) return;
      }
    };
    for (let i = camino.length - 1; i >= 0 && !ev._alto; i -= 1) llamar(camino[i], 1, true);
    if (!ev._alto) llamar(this, 2, true);
    if (!ev._alto) llamar(this, 2, false);
    if (ev.bubbles) for (let i = 0; i < camino.length && !ev._alto; i += 1) llamar(camino[i], 3, false);
    ev.currentTarget = null;
    ev.eventPhase = 0;
    return !ev.defaultPrevented;
  }
}

class Nodo extends Blanco {
  constructor(doc) {
    super();
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
  }
  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null; }
  get nextSibling() {
    const p = this.parentNode;
    return p ? (p.childNodes[p.childNodes.indexOf(this) + 1] ?? null) : null;
  }
  get previousSibling() {
    const p = this.parentNode;
    return p ? (p.childNodes[p.childNodes.indexOf(this) - 1] ?? null) : null;
  }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get isConnected() {
    for (let n = this; n; n = n.parentNode) if (n.nodeType === 9) return true;
    return false;
  }
  _padreEvento() { return this.parentNode; }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    this.childNodes.push(c);
    c.parentNode = this;
    return c;
  }
  insertBefore(c, ref) {
    if (ref === null || ref === undefined) return this.appendChild(c);
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) throw new Error("NotFoundError: insertBefore con una referencia que no es hija");
    this.childNodes.splice(i, 0, c);
    c.parentNode = this;
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i < 0) throw new Error("NotFoundError: removeChild de un nodo que no es hijo");
    this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) {
    for (; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    if (v !== null && v !== undefined && v !== "") this.appendChild(new Texto(this.ownerDocument || this, v));
  }
}

class Texto extends Nodo {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 3;
    this.nodeName = "#text";
    this.data = String(data);
  }
  get nodeValue() { return this.data; }
  set nodeValue(v) { this.data = String(v); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

class Comentario extends Nodo {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 8;
    this.nodeName = "#comment";
    this.data = String(data);
  }
  get textContent() { return ""; }
  set textContent(v) { this.data = String(v); }
}

function nuevoEstilo() {
  const s = {};
  Object.defineProperties(s, {
    setProperty: { value(n, v) { s[n] = v; }, enumerable: false },
    removeProperty: { value(n) { delete s[n]; }, enumerable: false },
    getPropertyValue: { value(n) { return s[n] ?? ""; }, enumerable: false },
  });
  return s;
}

/** Selector mínimo: `tag`, `tag[attr]`, `[attr]`, `tag:not(:disabled)`, `[attr="v"]`, `#id`. */
function casa(el, sel) {
  const m = /^([a-zA-Z0-9]*)(?:#([\w:-]+))?(?:\[([\w-]+)(?:="([^"]*)")?\])?(:not\(:disabled\))?$/.exec(sel.trim());
  if (!m) throw new Error(`selector no soportado por el DOM de prueba: ${sel}`);
  const [, tag, id, attr, valor, noApagado] = m;
  if (tag && el.localName !== tag.toLowerCase()) return false;
  if (id && el.getAttribute("id") !== id) return false;
  if (attr && !el.hasAttribute(attr)) return false;
  if (attr && valor !== undefined && el.getAttribute(attr) !== valor) return false;
  if (noApagado && el.hasAttribute("disabled")) return false;
  return true;
}

class Elemento extends Nodo {
  constructor(doc, tag, ns) {
    super(doc);
    this.nodeType = 1;
    this.namespaceURI = ns || HTML;
    this.localName = this.namespaceURI === HTML ? tag.toLowerCase() : tag;
    this.tagName = this.namespaceURI === HTML ? tag.toUpperCase() : tag;
    this.nodeName = this.tagName;
    this._at = new Map();
    this.style = nuevoEstilo();
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.offsetHeight = 0;
    this.onclick = null;
  }
  getAttribute(n) { return this._at.has(n) ? this._at.get(n) : null; }
  setAttribute(n, v) { this._at.set(n, String(v)); }
  removeAttribute(n) { this._at.delete(n); }
  hasAttribute(n) { return this._at.has(n); }
  setAttributeNS(_ns, n, v) { this.setAttribute(n, v); }
  removeAttributeNS(_ns, n) { this.removeAttribute(n); }
  get id() { return this.getAttribute("id") || ""; }
  get className() { return this.getAttribute("class") || ""; }
  get disabled() { return this._at.has("disabled"); }
  set disabled(v) { if (v) this._at.set("disabled", ""); else this._at.delete("disabled"); }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get dataset() {
    const d = {};
    for (const [k, v] of this._at) {
      if (k.startsWith("data-")) d[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    return d;
  }
  set innerHTML(v) { this.textContent = ""; this._html = String(v); }
  get innerHTML() { return this._html || ""; }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  click() {
    if (this.disabled) return;
    this.dispatchEvent(new Evento("click"));
  }
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; }
  scrollIntoView() {}
  /** Todos los elementos que cuelgan de éste, en orden de documento. */
  _todos(salida = []) {
    for (const c of this.childNodes) {
      if (c.nodeType !== 1) continue;
      salida.push(c);
      c._todos(salida);
    }
    return salida;
  }
  querySelectorAll(sel) {
    const partes = sel.split(",").map((s) => s.trim()).filter(Boolean);
    return this._todos().filter((el) => partes.some((p) => casa(el, p)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
  closest(sel) {
    for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (casa(n, sel)) return n;
    return null;
  }
  matches(sel) { return casa(this, sel); }
}

class Entrada extends Elemento {
  constructor(doc, tag) {
    super(doc, tag);
    // Propiedades PROPIAS y planas: así React no instala su rastreador de valor y cada evento
    // `input`/`change` que se despacha cuenta como un cambio de la persona.
    this.value = "";
    this.defaultValue = "";
    this.checked = false;
    this.defaultChecked = false;
    this.name = "";
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.validity = { badInput: false, valid: true };
  }
  get type() { return this.getAttribute("type") || (this.localName === "textarea" ? "textarea" : "text"); }
  set type(v) { this.setAttribute("type", v); }
  click() {
    if (this.disabled) return;
    if (this.type === "checkbox") this.checked = !this.checked;   // como el navegador: antes del evento
    this.dispatchEvent(new Evento("click"));
  }
}

class Opcion extends Elemento {
  constructor(doc, tag) {
    super(doc, tag);
    this._sel = false;
    this.defaultSelected = false;
  }
  get value() { return this.hasAttribute("value") ? this.getAttribute("value") : this.textContent; }
  set value(v) { this.setAttribute("value", v); }
  get text() { return this.textContent; }
  get selected() { return this._sel; }
  set selected(v) {
    const sel = this.closest("select");
    if (v && sel && !sel.multiple) for (const o of sel.options) o._sel = false;
    this._sel = !!v;
  }
}

class Selector extends Elemento {
  constructor(doc, tag) {
    super(doc, tag);
    this.multiple = false;
    this.name = "";
  }
  get type() { return this.multiple ? "select-multiple" : "select-one"; }
  get options() { return this.querySelectorAll("option"); }
  get value() {
    const o = this.options;
    const elegida = o.find((x) => x._sel) ?? o.find((x) => !x.disabled) ?? null;
    return elegida ? elegida.value : "";
  }
  set value(v) {
    for (const o of this.options) o._sel = false;
    const o = this.options.find((x) => x.value === String(v));
    if (o) o._sel = true;
  }
}

class Boton extends Elemento {
  get type() { return this.getAttribute("type") || "submit"; }
}

class Marco extends Elemento {}

class Documento extends Nodo {
  constructor() {
    super(null);
    this.nodeType = 9;
    this.nodeName = "#document";
    this.visibilityState = "visible";
    this.hidden = false;
    this.oninput = null;          // «'oninput' in document»: React usa el evento `input` nativo
    this.documentElement = this.appendChild(this.createElement("html"));
    this.head = this.documentElement.appendChild(this.createElement("head"));
    this.body = this.documentElement.appendChild(this.createElement("body"));
    this.activeElement = this.body;
    this.defaultView = globalThis;
  }
  _padreEvento() { return this.defaultView && this.defaultView.__blanco ? this.defaultView.__blanco : null; }
  createElement(tag) {
    const t = String(tag).toLowerCase();
    if (t === "input" || t === "textarea") return new Entrada(this, tag);
    if (t === "select") return new Selector(this, tag);
    if (t === "option") return new Opcion(this, tag);
    if (t === "button") return new Boton(this, tag);
    if (t === "iframe") return new Marco(this, tag);
    return new Elemento(this, tag);
  }
  createElementNS(ns, tag) { return ns === HTML ? this.createElement(tag) : new Elemento(this, tag, ns); }
  createTextNode(t) { return new Texto(this, t); }
  createComment(t) { return new Comentario(this, t); }
  getElementById(id) { return this.documentElement._todos().find((e) => e.getAttribute("id") === id) ?? null; }
  querySelectorAll(sel) { return this.documentElement.querySelectorAll(sel); }
  querySelector(sel) { return this.documentElement.querySelector(sel); }
  hasFocus() { return true; }
}

/**
 * Instala `window`, `document` y compañía en el global de node, y devuelve el
 * documento. Con `defineProperty` y no con una asignación: algunos de estos
 * nombres ya existen en node como propiedades de sólo lectura (`navigator`,
 * `localStorage` según la versión) y asignarlos tronaría, o no haría nada.
 */
function instalar() {
  const g = globalThis;
  const poner = (nombre, valor) =>
    Object.defineProperty(g, nombre, { value: valor, configurable: true, writable: true, enumerable: true });
  const blancoVentana = new Blanco();
  poner("__blanco", blancoVentana);
  poner("window", g);
  poner("self", g);
  poner("top", g);
  poner("addEventListener", blancoVentana.addEventListener.bind(blancoVentana));
  poner("removeEventListener", blancoVentana.removeEventListener.bind(blancoVentana));
  poner("dispatchEvent", (ev) => blancoVentana.dispatchEvent(ev));
  const doc = new Documento();
  poner("document", doc);
  poner("location", { hash: "", pathname: "/inventario/ordenes", search: "", origin: "http://ov.test",
                      href: "http://ov.test/inventario/ordenes" });
  poner("history", { pushState() {}, replaceState() {} });
  const memoria = new Map();
  poner("localStorage", { getItem: (k) => (memoria.has(k) ? memoria.get(k) : null),
                          setItem: (k, v) => { memoria.set(k, String(v)); },
                          removeItem: (k) => { memoria.delete(k); } });
  poner("scrollTo", () => {});
  poner("scrollY", 0);
  poner("confirm", () => true);
  poner("devicePixelRatio", 1);
  poner("requestAnimationFrame", (f) => setTimeout(() => f(Date.now()), 16));
  poner("cancelAnimationFrame", (t) => clearTimeout(t));
  poner("getComputedStyle", () => ({ getPropertyValue: () => "" }));
  poner("Node", Nodo);
  poner("Element", Elemento);
  poner("HTMLElement", Elemento);
  poner("HTMLInputElement", Entrada);
  poner("HTMLTextAreaElement", Entrada);
  poner("HTMLSelectElement", Selector);
  poner("HTMLButtonElement", Boton);
  poner("HTMLIFrameElement", Marco);
  poner("Event", Evento);
  return doc;
}

module.exports = { instalar, Evento };
