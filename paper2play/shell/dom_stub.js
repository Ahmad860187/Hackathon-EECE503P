/* Minimal DOM stub used ONLY by the Python checks (quickjs) to smoke-test kit.js + RENDER.
   Covers the DOM APIs listed in CONTRACT §4 plus a few harmless browser globals.
   It is never included in the generated page. */
(function (G) {
  'use strict';
  var __created = 0;

  function StyleDecl() {}
  StyleDecl.prototype.setProperty = function (k, v) { this[k] = String(v); };
  StyleDecl.prototype.removeProperty = function (k) { var v = this[k]; delete this[k]; return v || ''; };
  StyleDecl.prototype.getPropertyValue = function (k) { return this[k] || ''; };

  function ClassList(node) { this._n = node; this._s = []; }
  ClassList.prototype.add = function () { for (var i = 0; i < arguments.length; i++) if (this._s.indexOf(String(arguments[i])) < 0) this._s.push(String(arguments[i])); };
  ClassList.prototype.remove = function () { for (var i = 0; i < arguments.length; i++) { var j = this._s.indexOf(String(arguments[i])); if (j >= 0) this._s.splice(j, 1); } };
  ClassList.prototype.toggle = function (c, force) { var has = this._s.indexOf(String(c)) >= 0; var want = force === undefined ? !has : !!force; if (want) this.add(c); else this.remove(c); return want; };
  ClassList.prototype.contains = function (c) { return this._s.indexOf(String(c)) >= 0; };

  function Node(tag, ns, type) {
    __created++;
    this.nodeType = type || 1;
    this.tagName = this.nodeName = String(tag || '').toUpperCase();
    this.localName = String(tag || '').toLowerCase();
    this.namespaceURI = ns || 'http://www.w3.org/1999/xhtml';
    this.attributes = {};
    this.childNodes = [];
    this.parentNode = null;
    this.style = new StyleDecl();
    this.classList = new ClassList(this);
    this.dataset = {};
    this._text = '';
    this._html = '';
    this._listeners = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.id = '';
    this.title = '';
  }
  var NP = Node.prototype;
  function adopt(parent, child) {
    if (child === null || child === undefined) return child;
    if (typeof child !== 'object') child = new TextNode(String(child));
    if (!(child instanceof Node)) throw new TypeError('appendChild: argument is not a Node');
    if (child.nodeType === 11) { var kids = child.childNodes.slice(); child.childNodes = []; for (var i = 0; i < kids.length; i++) adopt(parent, kids[i]); return child; }
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = parent;
    parent.childNodes.push(child);
    return child;
  }
  NP.appendChild = function (c) { if (!(c instanceof Node)) throw new TypeError("Failed to execute 'appendChild': parameter 1 is not of type 'Node'."); return adopt(this, c); };
  NP.append = function () { for (var i = 0; i < arguments.length; i++) adopt(this, arguments[i]); };
  NP.prepend = function () { var old = this.childNodes; this.childNodes = []; for (var i = 0; i < arguments.length; i++) adopt(this, arguments[i]); for (var j = 0; j < old.length; j++) this.childNodes.push(old[j]); };
  NP.insertBefore = function (c, ref) { adopt(this, c); if (ref) { var arr = this.childNodes; arr.pop(); var k = arr.indexOf(ref); if (k < 0) arr.push(c); else arr.splice(k, 0, c); } return c; };
  NP.removeChild = function (c) { var k = this.childNodes.indexOf(c); if (k >= 0) { this.childNodes.splice(k, 1); c.parentNode = null; } return c; };
  NP.replaceChild = function (n, o) { var k = this.childNodes.indexOf(o); if (k >= 0) { adopt(this, n); this.childNodes.pop(); this.childNodes[k] = n; o.parentNode = null; } return o; };
  NP.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };
  NP.replaceChildren = function () { for (var i = 0; i < this.childNodes.length; i++) this.childNodes[i].parentNode = null; this.childNodes = []; this._text = ''; this._html = ''; for (var j = 0; j < arguments.length; j++) adopt(this, arguments[j]); };
  NP.setAttribute = function (k, v) { this.attributes[String(k)] = String(v); if (k === 'id') this.id = String(v); if (k === 'class') this.classList._s = String(v).split(/\s+/).filter(Boolean); };
  NP.setAttributeNS = function (ns, k, v) { this.setAttribute(k, v); };
  NP.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; };
  NP.hasAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attributes, k); };
  NP.removeAttribute = function (k) { delete this.attributes[k]; };
  NP.addEventListener = function (t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); };
  NP.removeEventListener = function (t, f) { var a = this._listeners[t] || []; var k = a.indexOf(f); if (k >= 0) a.splice(k, 1); };
  NP.dispatchEvent = function (ev) { var a = (this._listeners[ev && ev.type] || []).slice(); for (var i = 0; i < a.length; i++) a[i].call(this, ev); return true; };
  NP.getBoundingClientRect = function () { return { x: 0, y: 0, left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; };
  NP.getBBox = function () { return { x: 0, y: 0, width: 0, height: 0 }; };
  NP.getTotalLength = function () { return 0; };
  NP.getPointAtLength = function () { return { x: 0, y: 0 }; };
  NP.getScreenCTM = function () { return null; };
  NP.createSVGPoint = function () { return { x: 0, y: 0, matrixTransform: function () { return { x: 0, y: 0 }; } }; };
  NP.focus = NP.blur = NP.click = NP.scrollIntoView = NP.setPointerCapture = NP.releasePointerCapture = function () {};
  NP.contains = function (n) { if (n === this) return true; for (var i = 0; i < this.childNodes.length; i++) if (this.childNodes[i].contains && this.childNodes[i].contains(n)) return true; return false; };
  NP.closest = function () { return null; };
  NP.querySelector = function () { return null; };
  NP.querySelectorAll = function () { return []; };
  NP.getElementsByTagName = function () { return []; };
  NP.cloneNode = function (deep) { var c = new Node(this.localName, this.namespaceURI, this.nodeType); for (var k in this.attributes) c.attributes[k] = this.attributes[k]; c._text = this._text; if (deep) for (var i = 0; i < this.childNodes.length; i++) adopt(c, this.childNodes[i].cloneNode(true)); return c; };
  NP.getContext = function () {
    var noop = function () { return ctx; };
    var store = {};
    var ctx = new Proxy(store, {
      get: function (t, k) { if (k in t) return t[k]; if (k === 'canvas') return null; if (k === 'measureText') return function (s) { return { width: String(s).length * 6 }; }; if (k === 'getImageData' || k === 'createImageData') return function () { return { data: [] }; }; if (k === 'createLinearGradient' || k === 'createRadialGradient' || k === 'createPattern') return function () { return { addColorStop: function () {} }; }; return noop; },
      set: function (t, k, v) { t[k] = v; return true; }
    });
    return ctx;
  };
  Object.defineProperty(NP, 'children', { get: function () { return this.childNodes.filter(function (n) { return n.nodeType === 1; }); } });
  Object.defineProperty(NP, 'firstChild', { get: function () { return this.childNodes[0] || null; } });
  Object.defineProperty(NP, 'lastChild', { get: function () { return this.childNodes[this.childNodes.length - 1] || null; } });
  Object.defineProperty(NP, 'firstElementChild', { get: function () { return this.children[0] || null; } });
  Object.defineProperty(NP, 'lastElementChild', { get: function () { var c = this.children; return c[c.length - 1] || null; } });
  Object.defineProperty(NP, 'childElementCount', { get: function () { return this.children.length; } });
  Object.defineProperty(NP, 'nextSibling', { get: function () { var p = this.parentNode; if (!p) return null; var k = p.childNodes.indexOf(this); return p.childNodes[k + 1] || null; } });
  Object.defineProperty(NP, 'previousSibling', { get: function () { var p = this.parentNode; if (!p) return null; var k = p.childNodes.indexOf(this); return k > 0 ? p.childNodes[k - 1] : null; } });
  Object.defineProperty(NP, 'parentElement', { get: function () { return this.parentNode; } });
  Object.defineProperty(NP, 'ownerDocument', { get: function () { return G.document; } });
  Object.defineProperty(NP, 'isConnected', { get: function () { return false; } });
  ['clientWidth', 'offsetWidth', 'scrollWidth'].forEach(function (k) { Object.defineProperty(NP, k, { get: function () { return 640; } }); });
  ['clientHeight', 'offsetHeight', 'scrollHeight'].forEach(function (k) { Object.defineProperty(NP, k, { get: function () { return 400; } }); });
  Object.defineProperty(NP, 'className', {
    get: function () { return this.classList._s.join(' '); },
    set: function (v) { this.classList._s = String(v).split(/\s+/).filter(Boolean); }
  });
  Object.defineProperty(NP, 'textContent', {
    get: function () { if (this.nodeType === 3) return this._text; return this._text + this.childNodes.map(function (c) { return c.textContent; }).join(''); },
    set: function (v) { for (var i = 0; i < this.childNodes.length; i++) this.childNodes[i].parentNode = null; this.childNodes = []; this._html = ''; this._text = v === null || v === undefined ? '' : String(v); }
  });
  Object.defineProperty(NP, 'innerText', {
    get: function () { return this.textContent; },
    set: function (v) { this.textContent = v; }
  });
  Object.defineProperty(NP, 'innerHTML', {
    get: function () { return this._html; },
    set: function (v) { for (var i = 0; i < this.childNodes.length; i++) this.childNodes[i].parentNode = null; this.childNodes = []; this._text = ''; this._html = String(v); }
  });
  Object.defineProperty(NP, 'nodeValue', { get: function () { return this.nodeType === 3 ? this._text : null; }, set: function (v) { if (this.nodeType === 3) this._text = String(v); } });
  Object.defineProperty(NP, 'data', { get: function () { return this._text; }, set: function (v) { this._text = String(v); } });

  function TextNode(t) { Node.call(this, '#text', null, 3); this._text = String(t); }
  TextNode.prototype = Object.create(NP);
  TextNode.prototype.constructor = TextNode;

  var body = new Node('body');
  var docEl = new Node('html');
  docEl.appendChild(body);
  var document = {
    nodeType: 9,
    body: body,
    documentElement: docEl,
    head: new Node('head'),
    createElement: function (t) { return new Node(t); },
    createElementNS: function (ns, t) { return new Node(t, ns); },
    createTextNode: function (t) { return new TextNode(t); },
    createDocumentFragment: function () { return new Node('#fragment', null, 11); },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getElementsByTagName: function () { return []; },
    addEventListener: function () {},
    removeEventListener: function () {}
  };

  var noop = function () {};
  var ids = 0;
  G.window = G;
  G.self = G;
  G.document = document;
  G.Node = Node;
  G.Element = Node;
  G.HTMLElement = Node;
  G.SVGElement = Node;
  G.navigator = { userAgent: 'quickjs-stub' };
  G.location = { href: 'about:blank', hash: '', search: '' };
  G.innerWidth = 1024; G.innerHeight = 768; G.devicePixelRatio = 1;
  G.console = G.console || { log: noop, warn: noop, error: noop, info: noop, debug: noop };
  ['log', 'warn', 'error', 'info', 'debug'].forEach(function (k) { if (typeof G.console[k] !== 'function') G.console[k] = noop; });
  G.requestAnimationFrame = function () { return ++ids; };
  G.cancelAnimationFrame = noop;
  G.setTimeout = function () { return ++ids; };
  G.clearTimeout = noop;
  G.setInterval = function () { return ++ids; };
  G.clearInterval = noop;
  G.addEventListener = noop;
  G.removeEventListener = noop;
  G.matchMedia = function () { return { matches: false, addEventListener: noop, removeEventListener: noop, addListener: noop, removeListener: noop }; };
  G.getComputedStyle = function () { return new StyleDecl(); };
  G.performance = G.performance || { now: function () { return 0; } };
  G.Event = function (type) { this.type = type; };
  G.CustomEvent = function (type, o) { this.type = type; this.detail = o && o.detail; };

  /* helpers for the checks */
  G.__stubCount = function (n) { var c = 0; (function walk(x) { for (var i = 0; i < x.childNodes.length; i++) { c++; walk(x.childNodes[i]); } })(n); if (c === 0 && (n._html || n._text)) c = 1; return c; };
  G.__stubCreated = function () { return __created; };
})(globalThis);
