/* kit.js - Paper to Playground drawing kit (generic; nothing paper-specific).
 * Vanilla ES2019, no dependencies. Never touches the DOM at load time, so it can be
 * evaluated inside quickjs with a DOM stub. Every drawing function appends an SVG to
 * `el`, returns it, and never throws: bad or empty data gives an informative empty state.
 *
 * Visual principles implemented here (multimedia-learning / videography):
 *  - continuity: a faint dashed ghost of the previous state + 250 ms tweens
 *    (prefers-reduced-motion respected)
 *  - signalling: one accent colour reserved for the highlighted / watched element
 *  - contiguity: direct labels at line ends, numbers on bars and cells
 *  - consistent colour semantics: a series name always maps to the same colour
 *  - learner-paced segmenting: kit.timeline has play/pause/step/scrubber
 */
var kit = (function () {
  'use strict';

  var SVGNS = 'http' + '://www.w3.org/2000/svg';
  var NCOL = 8;
  var TWEEN_MS = 250;
  var BURST_MS = 700;
  var FONT = 12;
  /* Ink class for text drawn on a shaded cell of fill-opacity a: the light and dark
     themes flip to the contrasting ink at different shades (CSS maps the classes). */
  function onCls(a) { return a > 0.86 ? ' k-on-d k-on-l' : (a > 0.66 ? ' k-on-d' : ''); }

  var st = {
    n: 0, store: {}, names: {}, nNames: 0, watch: null, width: 0, noMotion: false,
    onSet: null, tl: {}, vec: {}, drag: null, dragInit: false, uid: 0
  };

  /* ------------------------------------------------------------------ helpers */
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function num(x) {
    if (typeof x === 'number') return x;
    if (typeof x === 'boolean') return x ? 1 : 0;
    if (x === null || x === undefined || x === '') return NaN;
    var v = parseFloat(x);
    return isNaN(v) ? NaN : v;
  }
  function clamp(x, a, b) { return x < a ? a : (x > b ? b : x); }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return (x === undefined || x === null) ? '' : String(x); }
  function norm(s) { return str(s).toLowerCase().replace(/[^a-z0-9]+/g, ''); }
  function r2(v) { return Math.round(v * 100) / 100; }
  function s2(v) { return String(isFinite(v) ? Math.round(v * 100) / 100 : 0); }
  function tw(s, px) { return str(s).length * (px || FONT) * 0.58; }
  function trunc(s, n) {
    s = str(s); n = Math.max(1, Math.floor(n));
    return s.length > n ? s.slice(0, Math.max(1, n - 1)) + '…' : s;
  }
  function errMsg(e) { return e && e.message ? e.message : str(e); }
  function warn(a, b) {
    try { if (typeof console !== 'undefined' && console.warn) console.warn('[kit]', a, b === undefined ? '' : b); } catch (e) { /* ignore */ }
  }
  function now() {
    try { if (typeof performance !== 'undefined' && performance.now) return performance.now(); } catch (e) { /* ignore */ }
    return Date.now();
  }
  function reducedMotion() {
    try {
      return typeof window !== 'undefined' && !!window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { return false; }
  }
  function uid(p) { st.uid += 1; return (p || 'k') + st.uid; }
  function wrapWords(s, maxChars, maxLines) {
    var words = str(s).split(/\s+/).filter(Boolean), lines = [], cur = '';
    maxChars = Math.max(4, Math.floor(maxChars));
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (!cur) cur = w;
      else if ((cur + ' ' + w).length <= maxChars) cur += ' ' + w;
      else { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    lines = lines.map(function (l) { return trunc(l, maxChars); });
    if (maxLines && lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines[maxLines - 1] = trunc(lines[maxLines - 1] + ' …', maxChars);
    }
    return lines;
  }
  function decimalsOf(step) {
    if (!isNum(step) || step <= 0) return 2;
    var s = String(step);
    if (s.indexOf('e-') >= 0) return parseInt(s.split('e-')[1], 10) || 2;
    var i = s.indexOf('.');
    return i < 0 ? 0 : s.length - i - 1;
  }
  function is2D(x) { return Array.isArray(x) && x.length > 0 && Array.isArray(x[0]); }
  function to2D(x) {
    if (is2D(x)) return x.map(function (row) { return arr(row).map(num); });
    if (Array.isArray(x)) return [x.map(num)];
    if (x !== null && x !== undefined) return [[num(x)]];
    return [];
  }

  /* ----------------------------------------------------------------- numbers */
  function fmtNum(x, d) {
    if (typeof x !== 'number') x = num(x);
    if (isNaN(x)) return 'NaN';
    if (!isFinite(x)) return x > 0 ? '∞' : '-∞';
    if (x === 0) return '0';
    var a = Math.abs(x);
    if (Math.floor(x) === x && a < 1e15) return String(x);
    if (a >= 1e6 || a < Math.pow(10, -d) * 0.5 || (d === 0 && a < 0.5)) {
      return x.toExponential(Math.max(1, Math.min(d, 6) - 1)).replace('e+', 'e');
    }
    var s = x.toFixed(d);
    if (/^-0(\.0*)?$/.test(s)) s = s.slice(1);
    return s;
  }
  function fmt(x, d) {
    d = (d === undefined || d === null || !isNum(num(d))) ? 3 : clamp(Math.round(num(d)), 0, 10);
    try {
      if (x === null || x === undefined) return '—';
      if (typeof x === 'boolean') return x ? 'true' : 'false';
      if (typeof x === 'number') return fmtNum(x, d);
      if (typeof x === 'string') return x;
      if (Array.isArray(x)) {
        if (is2D(x)) {
          var rows = x.slice(0, 8).map(function (row) {
            var cells = arr(row).slice(0, 8).map(function (v) { return fmt(v, d); });
            if (arr(row).length > 8) cells.push('…');
            return '[' + cells.join(', ') + ']';
          });
          if (x.length > 8) rows.push('…');
          return '[' + rows.join(', ') + ']';
        }
        var items = x.slice(0, 12).map(function (v) { return fmt(v, d); });
        if (x.length > 12) items.push('… (' + x.length + ' items)');
        return '[' + items.join(', ') + ']';
      }
      if (typeof x === 'object') {
        return '{' + Object.keys(x).slice(0, 8).map(function (k) { return k + ': ' + fmt(x[k], d); }).join(', ') + '}';
      }
      return String(x);
    } catch (e) { return '?'; }
  }
  function niceStep(span, n) {
    if (!(span > 0) || !isFinite(span)) return 1;
    var raw = span / Math.max(1, n);
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / mag;
    var nf = f < 1.5 ? 1 : (f < 3 ? 2 : (f < 7 ? 5 : 10));
    return nf * mag;
  }
  function tickFmt(v, step) {
    if (Math.abs(v) < step * 1e-6) return '0';
    var a = Math.abs(v);
    if (a >= 1e5 || a < 1e-4) {
      return v.toExponential(2).replace(/\.?0+e/, 'e').replace('e+', 'e');
    }
    var dec = clamp(Math.ceil(-Math.log10(step) - 1e-9), 0, 6);
    return v.toFixed(dec);
  }
  var SUP = { '-': '⁻', '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹' };
  function logTickFmt(v) {
    var k = Math.round(Math.log10(v));
    if (Math.abs(v - Math.pow(10, k)) < 1e-9 * v && (k < -3 || k > 4)) {
      return '10' + String(k).split('').map(function (c) { return SUP[c] || c; }).join('');
    }
    return tickFmt(v, Math.pow(10, Math.floor(Math.log10(v))));
  }

  /* ------------------------------------------------------------------ colours */
  function colorIndex(name) {
    var k = norm(name) || '_';
    if (!Object.prototype.hasOwnProperty.call(st.names, k)) { st.names[k] = st.nNames % NCOL; st.nNames += 1; }
    return st.names[k];
  }
  function color(i) {
    if (typeof i === 'string' && !isNum(num(i))) return 'var(--c' + colorIndex(i) + ')';
    var n = Math.floor(num(i)); if (!isNum(n)) n = 0;
    return 'var(--c' + (((n % NCOL) + NCOL) % NCOL) + ')';
  }
  function colorFor(name) { return 'var(--c' + colorIndex(name) + ')'; }
  function isWatched(name) {
    if (!st.watch || name === undefined || name === null || name === '') return false;
    var n = norm(name);
    return !!n && (n === norm(st.watch.key) || n === norm(st.watch.label));
  }

  /* ----------------------------------------------------------------- SVG core */
  var STYLE_ATTRS = { fill: 1, stroke: 1, color: 1, 'stop-color': 1 };
  function svgEl(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    var style = '';
    if (attrs) {
      for (var k in attrs) {
        if (!Object.prototype.hasOwnProperty.call(attrs, k)) continue;
        var v = attrs[k];
        if (v === undefined || v === null || v === false) continue;
        if (k === 'text') { e.textContent = str(v); continue; }
        if (k === 'style') { style += str(v); if (style && style.slice(-1) !== ';') style += ';'; continue; }
        if (STYLE_ATTRS[k] && typeof v === 'string' && v.indexOf('var(') >= 0) { style += k + ':' + v + ';'; continue; }
        e.setAttribute(k, typeof v === 'number' ? String(isFinite(v) ? r2(v) : 0) : String(v));
      }
    }
    if (style) e.setAttribute('style', style);
    if (parent && typeof parent.appendChild === 'function') parent.appendChild(e);
    return e;
  }
  function attach(el, node) { if (el && typeof el.appendChild === 'function') el.appendChild(node); return node; }
  function makeSvg(el, w, h, label, natural) {
    var s = svgEl('svg', {
      viewBox: '0 0 ' + Math.round(w) + ' ' + Math.round(h),
      width: natural ? Math.round(w) : '100%',
      'class': 'kit-svg', role: 'img', 'aria-label': str(label) || 'figure',
      preserveAspectRatio: 'xMinYMin meet'
    });
    if (label) svgEl('title', { text: label }, s);
    attach(el, s);
    return s;
  }
  function widthOf(el) {
    var w = 0;
    try {
      var r = el && typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect() : null;
      w = r && r.width ? r.width : 0;
    } catch (e) { w = 0; }
    if (!(w > 60)) w = st.width > 60 ? st.width : 640;
    return clamp(Math.round(w), 260, 1040);
  }
  function txt(parent, x, y, s, cls, extra) {
    var a = { x: x, y: y, text: s, 'class': cls || null };
    if (extra) for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) a[k] = extra[k];
    return svgEl('text', a, parent);
  }
  /* Panel titles wrap (up to 3 lines) instead of truncating. titleLines is pure so
     each primitive can reserve the extra height (titleExtra) before drawing. */
  var TITLE_LH = 17;
  function ghostText(g, W) { return typeof g === 'string' ? g : (W < 520 ? 'previous' : 'before last change'); }
  function titleLines(title, W, x, ghostNote) {
    title = str(title);
    if (!title) return [];
    var full = W - (x || 0) - 6;
    var first = full - (ghostNote ? tw(ghostText(ghostNote, W)) + 44 : 0);
    if (first < 140) first = full;
    // the first line shares its row with the ghost note; later lines use the full width
    var c1 = Math.max(4, first / 7.2), c2 = Math.max(4, full / 7.2);
    if (title.length <= c1) return [title];
    var words = title.split(/\s+/).filter(Boolean), cur = '', k = 0;
    for (; k < words.length; k++) {
      var nx = cur ? cur + ' ' + words[k] : words[k];
      if (nx.length > c1 && cur) break;
      cur = nx;
    }
    if (cur.length > c1) cur = trunc(cur, c1);
    return [cur].concat(k < words.length ? wrapWords(words.slice(k).join(' '), c2, 2) : []);
  }
  function titleExtra(title, W, x, ghostNote) {
    var n = titleLines(title, W, x, ghostNote).length;
    return n > 1 ? (n - 1) * TITLE_LH : 0;
  }
  /* reserve: keep room for the ghost note even before a ghost exists, so the layout
     does not jump after the first change */
  function titleRow(svg, title, x, W, ghostNote, reserve) {
    titleLines(title, W, x, reserve || ghostNote).forEach(function (l, k) { txt(svg, x, 17 + k * TITLE_LH, l, 'k-title'); });
    if (ghostNote) {
      var gx = W - 6, gt = ghostText(ghostNote, W);
      txt(svg, gx, 17, gt, 'k-muted', { 'text-anchor': 'end' });
      if (ghostNote === true) svgEl('line', { x1: gx - tw(gt) - 30, x2: gx - tw(gt) - 8, y1: 13, y2: 13, 'class': 'k-ghost' }, svg);
    }
  }
  function emptyState(el, title, message, W) {
    W = W || widthOf(el);
    var tEx = titleExtra(title, W, 8, false);
    var H = (title ? 120 : 96) + tEx;
    var s = makeSvg(el, W, H, (title ? title + ': ' : '') + message);
    s.classList.add('kit-empty');
    titleRow(s, title, 8, W, false);
    var y0 = (title ? 28 : 6) + tEx;
    svgEl('rect', { x: 4, y: y0, width: W - 8, height: H - y0 - 4, rx: 8, 'class': 'k-empty-box' }, s);
    var lines = wrapWords(message, (W - 40) / 7, 3);
    lines.forEach(function (l, i) {
      txt(s, W / 2, y0 + (H - y0) / 2 - (lines.length - 1) * 8 + i * 16 + 4, l, 'k-muted', { 'text-anchor': 'middle' });
    });
    return s;
  }
  function safe(kind, el, o, fn) {
    o = (o && typeof o === 'object') ? o : {};
    try { return fn(el, o); } catch (e) {
      warn('could not draw ' + kind, e);
      try { return emptyState(el, o.title, 'Could not draw this ' + kind + ': ' + errMsg(e), widthOf(el)); } catch (e2) { return null; }
    }
  }

  /* --------------------------------------------- memory: ghosts and tweens */
  function slot(kind, title) {
    if (st.noMotion) return { tmp: true };
    var key = kind + '#' + st.n + ':' + str(title).slice(0, 40);
    st.n += 1;
    if (!st.store[key]) st.store[key] = { sig: undefined, cur: null, at: 0, ghost: null, ghostSig: undefined };
    return st.store[key];
  }
  /* Returns {ghost, prev}. ghost = state shown before the current burst of changes
     (a burst = changes less than BURST_MS apart, e.g. one slider drag); prev = state
     drawn immediately before this one (tween origin). */
  function remember(s, data) {
    var out = { ghost: null, prev: null };
    if (!s || s.tmp || st.noMotion) return out;
    var sig;
    try { sig = JSON.stringify(data); } catch (e) { sig = String(now()); }
    var t = now();
    if (s.sig === undefined) { s.cur = data; s.sig = sig; s.at = t; }
    else if (sig !== s.sig) {
      if (t - s.at > BURST_MS || s.ghost === null) { s.ghost = s.cur; s.ghostSig = s.sig; }
      out.prev = s.cur; s.cur = data; s.sig = sig; s.at = t;
    }
    if (s.ghost !== null && s.ghostSig !== sig) out.ghost = s.ghost;
    return out;
  }
  function tween(fn) {
    fn(1); // final state first: correct even if animation frames never run
    if (st.noMotion || reducedMotion() || typeof requestAnimationFrame !== 'function') return;
    var t0 = null;
    function step(ts) {
      if (t0 === null) t0 = ts;
      var a = clamp((ts - t0) / TWEEN_MS, 0, 1);
      var e = a < 0.5 ? 2 * a * a : 1 - Math.pow(-2 * a + 2, 2) / 2;
      try { fn(e); } catch (err) { return; }
      if (a < 1) requestAnimationFrame(step);
    }
    try { requestAnimationFrame(step); } catch (e) { /* ignore */ }
  }
  function lerp(a, b, t) { return isNum(a) && isNum(b) ? a + (b - a) * t : b; }

  /* ----------------------------------------------------------------- axes */
  function axisDomain(vals, fmin, fmax, log, nT) {
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < vals.length; i++) {
      var v = vals[i];
      if (isNum(v) && (!log || v > 0)) { if (v < lo) lo = v; if (v > hi) hi = v; }
    }
    var fixLo = isNum(fmin) && (!log || fmin > 0), fixHi = isNum(fmax) && (!log || fmax > 0);
    if (fixLo) lo = fmin;
    if (fixHi) hi = fmax;
    if (!isNum(lo) || !isNum(hi)) {
      if (fixLo && !fixHi) hi = log ? lo * 10 : lo + 1;
      else if (fixHi && !fixLo) lo = log ? hi / 10 : hi - 1;
      else return null;
    }
    if (lo > hi) { var tmp = lo; lo = hi; hi = tmp; }
    if (lo === hi) {
      if (log) { if (!fixLo) lo = lo / 10; if (!fixHi) hi = hi * 10; }
      else { var d = Math.abs(lo) * 0.1 || 1; if (!fixLo) lo -= d; if (!fixHi) hi += d; if (lo === hi) hi = lo + 1; }
    }
    if (log) {
      if (!fixLo) lo = Math.pow(10, Math.floor(Math.log10(lo) + 1e-9));
      if (!fixHi) hi = Math.pow(10, Math.ceil(Math.log10(hi) - 1e-9));
      if (lo === hi) hi = lo * 10;
      return { lo: lo, hi: hi, log: true, ticks: logTicks(lo, hi) };
    }
    var step = niceStep(hi - lo, nT);
    if (!fixLo) lo = Math.floor(lo / step + 1e-9) * step;
    if (!fixHi) hi = Math.ceil(hi / step - 1e-9) * step;
    step = niceStep(hi - lo, nT);
    var ticks = [];
    for (var t = Math.ceil(lo / step - 1e-9) * step, k = 0; t <= hi + step * 1e-9 && k < 60; t += step, k++) {
      ticks.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    }
    return { lo: lo, hi: hi, log: false, step: step, ticks: ticks };
  }
  function logTicks(lo, hi) {
    var a = Math.floor(Math.log10(lo) + 1e-9), b = Math.ceil(Math.log10(hi) - 1e-9), out = [];
    var every = Math.max(1, Math.ceil((b - a) / 8));
    for (var k = a; k <= b; k += every) {
      var v = Math.pow(10, k);
      if (v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9)) out.push(v);
      if (b - a <= 1) {
        [2, 5].forEach(function (m) { var w = m * Math.pow(10, k); if (w > lo && w < hi) out.push(w); });
      }
    }
    return out.sort(function (x, y) { return x - y; });
  }
  function scaleOf(dom, r0, r1) {
    if (dom.log) {
      var l0 = Math.log10(dom.lo), l1 = Math.log10(dom.hi), kl = (r1 - r0) / ((l1 - l0) || 1);
      return function (v) { return v > 0 ? r0 + (Math.log10(v) - l0) * kl : NaN; };
    }
    var k = (r1 - r0) / ((dom.hi - dom.lo) || 1);
    return function (v) { return r0 + (v - dom.lo) * k; };
  }
  function invScale(dom, r0, r1) {
    if (dom.log) {
      var l0 = Math.log10(dom.lo), l1 = Math.log10(dom.hi);
      return function (px) { return Math.pow(10, l0 + (px - r0) / ((r1 - r0) || 1) * (l1 - l0)); };
    }
    return function (px) { return dom.lo + (px - r0) / ((r1 - r0) || 1) * (dom.hi - dom.lo); };
  }
  function tickLabel(dom, v) { return dom.log ? logTickFmt(v) : tickFmt(v, dom.step || 1); }

  /* ------------------------------------------------------------------- plot */
  function normSeries(o, xlog, ylog) {
    var src = arr(o.series);
    if (!src.length && (Array.isArray(o.points))) src = [{ name: o.name || '', points: o.points }];
    return src.filter(function (s) { return s && typeof s === 'object'; }).map(function (s, i) {
      var pts = arr(s.points);
      if (!pts.length && Array.isArray(s.y)) {
        pts = s.y.map(function (y, j) { return [Array.isArray(s.x) ? s.x[j] : j, y]; });
      }
      var clean = pts.map(function (p) {
        var x, y;
        if (Array.isArray(p)) { x = num(p[0]); y = num(p[1]); }
        else if (p && typeof p === 'object') { x = num(p.x); y = num(p.y); }
        else { x = NaN; y = NaN; }
        if (xlog && !(x > 0)) x = NaN;
        if (ylog && !(y > 0)) y = NaN;
        return [isNum(x) ? r2v(x) : NaN, isNum(y) ? r2v(y) : NaN];
      });
      return {
        name: str(s.name), pts: clean, dashed: !!s.dashed, area: !!s.area,
        highlight: !!s.highlight || isWatched(s.name), color: s.color ? str(s.color) : null, idx: i
      };
    });
  }
  function r2v(v) { return Math.abs(v) < 1e-300 ? 0 : parseFloat(v.toPrecision(10)); }
  function pathD(pts, X, Y) {
    var d = '', pen = false;
    for (var i = 0; i < pts.length; i++) {
      var px = X(pts[i][0]), py = Y(pts[i][1]);
      if (!isNum(px) || !isNum(py)) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + r2(clamp(px, -1e4, 1e4)) + ',' + r2(clamp(py, -1e4, 1e4));
      pen = true;
    }
    return d;
  }
  function lastFinite(pts) {
    for (var i = pts.length - 1; i >= 0; i--) if (isNum(pts[i][0]) && isNum(pts[i][1])) return pts[i];
    return null;
  }
  function declutter(items, minGap, lo, hi) {
    items.sort(function (a, b) { return a.y - b.y; });
    for (var i = 0; i < items.length; i++) {
      items[i].ly = clamp(items[i].y, lo, hi);
      if (i > 0 && items[i].ly < items[i - 1].ly + minGap) items[i].ly = items[i - 1].ly + minGap;
    }
    for (var j = items.length - 1; j >= 0; j--) {
      var cap = j === items.length - 1 ? hi : items[j + 1].ly - minGap;
      if (items[j].ly > cap) items[j].ly = cap;
    }
    return items;
  }

  function plot(el, o) {
    return safe('plot', el, o, function (el, o) {
      var W = widthOf(el);
      var H = Math.round(clamp(W * 0.58, 230, 420));
      var xo = (o.x && typeof o.x === 'object') ? o.x : {}, yo = (o.y && typeof o.y === 'object') ? o.y : {};
      var xlog = !!xo.log, ylog = !!yo.log;
      var series = normSeries(o, xlog, ylog);
      var mem = remember(slot('plot', o.title), series.map(function (s) { return [s.name, s.pts]; }));
      var ghost = mem.ghost ? mem.ghost.map(function (g) { return { name: g[0], pts: g[1] }; }) : [];
      var prevByName = {};
      if (mem.prev) mem.prev.forEach(function (g, i) { prevByName[g[0] + '#' + i] = g[1]; });
      var markers = arr(o.markers).filter(function (m) { return m && typeof m === 'object'; });
      var vlines = arr(o.vlines).filter(function (m) { return m && typeof m === 'object'; });
      var hlines = arr(o.hlines).filter(function (m) { return m && typeof m === 'object'; });

      var xs = [], ys = [];
      series.concat(ghost).forEach(function (s) { s.pts.forEach(function (p) { if (isNum(p[0]) && isNum(p[1])) { xs.push(p[0]); ys.push(p[1]); } }); });
      if (!xs.length && !markers.length) {
        return emptyState(el, o.title, series.length ? 'No finite points to plot for these settings (values are NaN, infinite, or outside a log axis).' : 'Nothing to plot yet: no data series were provided.', W);
      }
      markers.forEach(function (m) { xs.push(num(m.x)); ys.push(num(m.y)); });
      vlines.forEach(function (m) { xs.push(num(m.x)); });
      hlines.forEach(function (m) { ys.push(num(m.y)); });

      var title = str(o.title);
      var tEx = titleExtra(title, W, 4, true);
      var top = (title || ghost.length ? 30 : 12) + tEx;
      H += tEx;
      var labelled = series.filter(function (s) { return s.name && lastFinite(s.pts); });
      var xDataMin = Math.min.apply(null, xs.filter(isNum).concat([Infinity])), xDataMax = Math.max.apply(null, xs.filter(isNum).concat([-Infinity]));
      var xEnd = isNum(num(xo.max)) ? num(xo.max) : xDataMax, xSpan = Math.abs(xEnd - xDataMin) || 1;
      var maxLab = 0;
      labelled.forEach(function (s) { if (lastFinite(s.pts)[0] >= xEnd - 0.08 * xSpan) maxLab = Math.max(maxLab, s.name.length); });
      var labChars = Math.min(maxLab, W < 480 ? 10 : 18);
      var right = maxLab ? clamp(labChars * 7 + 16, 30, W * 0.3) : 16;
      var bottom = 28 + (xo.label ? 18 : 0);
      var guessH = H - top - bottom;
      var dy = axisDomain(ys, num(yo.min), num(yo.max), ylog, Math.max(2, Math.round(guessH / 48)));
      if (!dy) return emptyState(el, title, 'No finite y values to plot.', W);
      var tickW = 0;
      dy.ticks.forEach(function (t) { tickW = Math.max(tickW, tw(tickLabel(dy, t))); });
      var left = Math.round(tickW + 12 + (yo.label ? 20 : 0));
      var pw = W - left - right, ph = H - top - bottom;
      var dx = axisDomain(xs, num(xo.min), num(xo.max), xlog, Math.max(2, Math.round(pw / 95)));
      if (!dx) return emptyState(el, title, 'No finite x values to plot.', W);
      var X = scaleOf(dx, left, left + pw), Y = scaleOf(dy, top + ph, top);

      var aria = (title || 'Plot') + (xo.label ? '; x: ' + xo.label : '') + (yo.label ? '; y: ' + yo.label : '') +
        (series.length ? '; series: ' + series.map(function (s) { return s.name; }).filter(Boolean).join(', ') : '');
      var svg = makeSvg(el, W, H, aria);
      if (series.some(function (s) { return s.highlight; }) || isWatched(title)) svg.classList.add('kit-has-focus');
      if (isWatched(title)) svg.classList.add('kit-watched');
      titleRow(svg, title, 4, W, ghost.length > 0, true);

      // grid + ticks
      var g = svgEl('g', { 'class': 'k-axes' }, svg);
      dy.ticks.forEach(function (t) {
        var y = Y(t); if (!isNum(y)) return;
        svgEl('line', { x1: left, x2: left + pw, y1: y, y2: y, 'class': 'k-grid' }, g);
        txt(g, left - 6, y + 4, tickLabel(dy, t), 'k-tick', { 'text-anchor': 'end' });
      });
      var lastTx = -1e9;
      dx.ticks.forEach(function (t) {
        var x = X(t); if (!isNum(x)) return;
        svgEl('line', { x1: x, x2: x, y1: top, y2: top + ph, 'class': 'k-grid' }, g);
        var lab = tickLabel(dx, t);
        if (x - tw(lab) / 2 > lastTx + 4) {
          txt(g, x, top + ph + 16, lab, 'k-tick', { 'text-anchor': 'middle' });
          lastTx = x + tw(lab) / 2;
        }
      });
      svgEl('rect', { x: left, y: top, width: pw, height: ph, 'class': 'k-frame' }, g);
      if (!dy.log && dy.lo < 0 && dy.hi > 0) svgEl('line', { x1: left, x2: left + pw, y1: Y(0), y2: Y(0), 'class': 'k-zero' }, g);
      if (!dx.log && dx.lo < 0 && dx.hi > 0) svgEl('line', { x1: X(0), x2: X(0), y1: top, y2: top + ph, 'class': 'k-zero' }, g);
      if (xo.label) txt(svg, left + pw / 2, H - 6, trunc(xo.label, pw / 7), 'k-axis-label', { 'text-anchor': 'middle' });
      if (yo.label) {
        txt(svg, 0, 0, trunc(yo.label, ph / 7), 'k-axis-label', { 'text-anchor': 'middle', transform: 'translate(' + 13 + ',' + r2(top + ph / 2) + ') rotate(-90)' });
      }

      var clipId = uid('clip');
      var defs = svgEl('defs', null, svg);
      var cp = svgEl('clipPath', { id: clipId }, defs);
      svgEl('rect', { x: left - 2, y: top - 2, width: pw + 4, height: ph + 4 }, cp);
      var body = svgEl('g', { 'clip-path': 'url(#' + clipId + ')' }, svg);

      // reference lines
      hlines.forEach(function (m) {
        var y = Y(num(m.y)); if (!isNum(y)) return;
        svgEl('line', { x1: left, x2: left + pw, y1: y, y2: y, 'class': 'k-ref' }, body);
        if (m.label) txt(svg, left + pw - 4, y - 5, trunc(m.label, pw / 8), 'k-ref-label k-halo', { 'text-anchor': 'end' });
      });
      vlines.forEach(function (m) {
        var x = X(num(m.x)); if (!isNum(x)) return;
        svgEl('line', { x1: x, x2: x, y1: top, y2: top + ph, 'class': 'k-ref' }, body);
        if (m.label) {
          var rightSide = x < left + pw * 0.66;
          txt(svg, x + (rightSide ? 5 : -5), top + 14, trunc(m.label, pw / 9), 'k-ref-label k-halo', { 'text-anchor': rightSide ? 'start' : 'end' });
        }
      });

      var anyHi = series.some(function (s) { return s.highlight; });
      var baseY = dy.log ? top + ph : Y(clamp(0, dy.lo, dy.hi));

      // ghost of previous state
      ghost.forEach(function (s) {
        var d = pathD(s.pts, X, Y);
        if (d) svgEl('path', { d: d, 'class': 'k-ghost' }, body);
      });

      var colOf = function (s) { return s.highlight ? 'var(--accent)' : (s.color || colorFor(s.name || ('series' + s.idx))); };
      var ordered = series.filter(function (s) { return !s.highlight; }).concat(series.filter(function (s) { return s.highlight; }));
      ordered.forEach(function (s) {
        var c = colOf(s);
        var finite = s.pts.filter(function (p) { return isNum(p[0]) && isNum(p[1]); });
        var prev = prevByName[s.name + '#' + s.idx];
        var canTween = prev && prev.length === s.pts.length;
        var interp = function (t) {
          if (!canTween || t >= 1) return s.pts;
          return s.pts.map(function (p, i) { return [lerp(prev[i][0], p[0], t), lerp(prev[i][1], p[1], t)]; });
        };
        var op = anyHi && !s.highlight ? 0.45 : 1;
        if (s.area && finite.length > 1) {
          var ap = svgEl('path', { fill: c, 'class': 'k-area', opacity: op * 0.16 }, body);
          var setArea = function (t) {
            var pts = interp(t).filter(function (p) { return isNum(p[0]) && isNum(p[1]); });
            var d = pathD(pts, X, Y);
            if (d) d += 'L' + r2(X(pts[pts.length - 1][0])) + ',' + r2(baseY) + 'L' + r2(X(pts[0][0])) + ',' + r2(baseY) + 'Z';
            ap.setAttribute('d', d);
          };
          tween(setArea);
        }
        if (finite.length === 1) {
          svgEl('circle', { cx: X(finite[0][0]), cy: Y(finite[0][1]), r: s.highlight ? 5.5 : 4.5, fill: c, opacity: op }, body);
          return;
        }
        var path = svgEl('path', {
          stroke: c, 'class': 'k-line' + (s.highlight ? ' k-line-hi' : ''), opacity: op,
          'stroke-dasharray': s.dashed ? '7 5' : null
        }, body);
        if (s.name) svgEl('title', { text: s.name }, path);
        tween(function (t) { path.setAttribute('d', pathD(interp(t), X, Y)); });
      });

      // direct labels at line ends
      var labs = [];
      labelled.forEach(function (s) {
        var lp = lastFinite(s.pts);
        var px = clamp(X(lp[0]), left, left + pw), py = clamp(Y(lp[1]), top, top + ph);
        if (!isNum(px) || !isNum(py)) return;
        labs.push({ s: s, x: px, y: py });
      });
      var atEnd = function (l) { return lastFinite(l.s.pts)[0] >= xEnd - 0.08 * xSpan; };
      var outside = labs.filter(atEnd);
      var inside = labs.filter(function (l) { return !atEnd(l); });
      declutter(outside, 15, top + 4, top + ph);
      outside.forEach(function (l) {
        var c = colOf(l.s);
        if (Math.abs(l.ly - l.y) > 5) svgEl('line', { x1: l.x + 2, y1: l.y, x2: left + pw + 4, y2: l.ly, stroke: c, 'class': 'k-leader' }, svg);
        var t = txt(svg, left + pw + 6, l.ly + 4, trunc(l.s.name, labChars), 'k-series-label' + (l.s.highlight ? ' k-strong' : ''), { fill: c });
        if (l.s.name.length > labChars) svgEl('title', { text: l.s.name }, t);
      });
      declutter(inside, 15, top + 10, top + ph - 4);
      inside.forEach(function (l) {
        var c = colOf(l.s);
        if (Math.abs(l.ly - l.y) > 5) svgEl('line', { x1: l.x + 2, y1: l.y, x2: l.x + 6, y2: l.ly, stroke: c, 'class': 'k-leader' }, svg);
        var nm = trunc(l.s.name, 16), flip = l.x + 7 + tw(nm) > W - 2;
        txt(svg, flip ? l.x - 7 : l.x + 7, l.ly + 4, nm, 'k-series-label k-halo' + (l.s.highlight ? ' k-strong' : ''), { fill: c, 'text-anchor': flip ? 'end' : null });
      });

      // markers
      markers.forEach(function (m) {
        var mx = X(num(m.x)), my = Y(num(m.y));
        if (!isNum(mx) || !isNum(my)) return;
        var hi = !!m.highlight || isWatched(m.label);
        svgEl('circle', { cx: mx, cy: my, r: hi ? 6 : 5, fill: hi ? 'var(--accent)' : 'var(--text)', 'class': 'k-marker' }, svg);
        if (m.label) {
          var lab = trunc(m.label, 26), lw = tw(lab);
          var toLeft = mx + 8 + lw > W - 2;
          var ly = my - 9 < top + 10 ? my + 18 : my - 9;
          txt(svg, toLeft ? mx - 8 : mx + 8, ly, lab, 'k-marker-label k-halo' + (hi ? ' k-accent' : ''), { 'text-anchor': toLeft ? 'end' : 'start' });
        }
      });

      if (o.note) note(el, o.note);
      return svg;
    });
  }
  function note(el, text) {
    var d = document.createElement('div');
    d.setAttribute('class', 'kit-note');
    d.textContent = str(text);
    attach(el, d);
    return d;
  }

  /* ------------------------------------------------------------------- bars */
  function bars(el, o) {
    return safe('bar chart', el, o, function (el, o) {
      var labels = arr(o.labels).map(str);
      var vals = arr(o.values).map(function (v) { return num(v); });
      var sec = (o.secondary && Array.isArray(o.secondary.values)) ? { name: str(o.secondary.name) || 'secondary', values: o.secondary.values.map(num) } : null;
      var n = Math.max(labels.length, vals.length, sec ? sec.values.length : 0);
      var W = widthOf(el);
      var finite = vals.concat(sec ? sec.values : []).filter(isNum);
      if (!n || !finite.length) return emptyState(el, o.title, n ? 'All values are missing or not finite for these settings.' : 'No values to show yet.', W);
      while (labels.length < n) labels.push(String(labels.length + 1));
      var d = isNum(num(o.fmt)) ? clamp(Math.round(num(o.fmt)), 0, 8) : 2;
      var unit = str(o.unit);
      var hiSet = {};
      arr(o.highlight).forEach(function (i) { hiSet[Math.round(num(i))] = true; });
      labels.forEach(function (l, i) { if (isWatched(l)) hiSet[i] = true; });
      var anyHi = Object.keys(hiSet).length > 0;
      var mem = remember(slot('bars', o.title), { v: vals, s: sec ? sec.values : null });
      var gv = mem.ghost ? arr(mem.ghost.v) : null, gs = mem.ghost ? arr(mem.ghost.s) : null;
      var pv = mem.prev ? arr(mem.prev.v) : [], ps = mem.prev ? arr(mem.prev.s) : [];
      var all = finite.concat(gv ? gv.filter(isNum) : []).concat(gs ? gs.filter(isNum) : []);
      var lo = Math.min(0, Math.min.apply(null, all)), hi = Math.max(0, Math.max.apply(null, all));
      var fmax = num(o.max), fmin = num(o.min);
      if (isNum(fmax) && fmax >= hi) hi = fmax;
      if (isNum(fmin) && fmin <= lo) lo = fmin;
      var title = str(o.title);
      var tEx = titleExtra(title, W, 4, true);
      var legY = ((title || mem.ghost) ? 44 : 22) + tEx;
      var top = (title || mem.ghost ? 30 : 10) + (sec ? 20 : 0) + 12 + tEx;
      var maxLab = 0; labels.forEach(function (l) { maxLab = Math.max(maxLab, l.length); });
      var valStr = function (v) { return isNum(v) ? fmt(v, d) + (unit && unit.length <= 3 ? unit : '') : '—'; };

      // orientation
      var leftV = 52 + (unit ? 0 : 0);
      var band = (W - leftV - 10) / n;
      var horiz = band < 30 || (maxLab * 7 > band * 1.9 && n <= 40);
      var H, svg, X, Y, dom, base;
      var primaryColor = colorFor(o.name || '__bars');
      var secColor = sec ? colorFor(sec.name) : null;
      // restraint: the accent is for one focal element; a large highlighted set is
      // shown by muting the rest instead of painting many bars orange
      var nHi = Object.keys(hiSet).length, focusSet = nHi > 3 && nHi > n * 0.25;
      var hiCol = focusSet ? primaryColor : 'var(--accent)', hiTick = focusSet ? ' k-strong' : ' k-accent';
      var aria = (title || 'Bar chart') + ': ' + labels.map(function (l, i) { return l + ' ' + valStr(vals[i]); }).join(', ');

      if (!horiz) {
        H = Math.round(clamp(W * 0.5, 210, 340)) + (sec ? 20 : 0) + tEx;
        var bottom = 38;
        var ph = H - top - bottom;
        dom = axisDomain([lo, hi], isNum(fmin) ? lo : NaN, isNum(fmax) ? hi : NaN, false, Math.max(2, Math.round(ph / 46)));
        var tickW = 0; dom.ticks.forEach(function (t) { tickW = Math.max(tickW, tw(tickLabel(dom, t))); });
        var left = Math.round(tickW + 12 + (unit ? 18 : 0));
        var pw = W - left - 10;
        band = pw / n;
        Y = scaleOf(dom, top + ph, top);
        base = Y(clamp(0, dom.lo, dom.hi));
        svg = makeSvg(el, W, H, aria);
        titleRow(svg, title, 4, W, !!mem.ghost, true);
        if (sec) legendRow(svg, 4, legY, [[o.name || 'values', primaryColor], [sec.name, secColor]]);
        dom.ticks.forEach(function (t) {
          svgEl('line', { x1: left, x2: left + pw, y1: Y(t), y2: Y(t), 'class': 'k-grid' }, svg);
          txt(svg, left - 6, Y(t) + 4, tickLabel(dom, t), 'k-tick', { 'text-anchor': 'end' });
        });
        if (unit) txt(svg, 0, 0, trunc(unit, ph / 7), 'k-axis-label', { 'text-anchor': 'middle', transform: 'translate(12,' + r2(top + ph / 2) + ') rotate(-90)' });
        var bw = band * (sec ? 0.4 : 0.66), gap = band * (sec ? 0.08 : 0.17);
        var labChars = Math.max(3, Math.floor(band / 6.9));
        for (var i = 0; i < n; i++) {
          var x0 = left + i * band + gap;
          drawVBar(svg, x0, bw, vals[i], pv[i], gv ? gv[i] : undefined, hiSet[i] ? hiCol : primaryColor, anyHi && !hiSet[i], Y, base, valStr, band >= 26 || hiSet[i], labels[i]);
          if (sec) drawVBar(svg, x0 + bw + band * 0.04, bw, sec.values[i], ps[i], gs ? gs[i] : undefined, secColor, anyHi && !hiSet[i], Y, base, valStr, band >= 52, labels[i] + ' (' + sec.name + ')');
          var lines = wrapWords(labels[i], labChars, 2);
          lines.forEach(function (ln, k) {
            txt(svg, left + (i + 0.5) * band, top + ph + 15 + k * 13, ln, 'k-tick' + (hiSet[i] ? hiTick : ''), { 'text-anchor': 'middle' });
          });
        }
        svgEl('line', { x1: left, x2: left + pw, y1: base, y2: base, 'class': 'k-baseline' }, svg);
      } else {
        var rowH = sec ? 34 : 26;
        var labW = Math.min(W * 0.36, maxLab * 7 + 12);
        var valW = 0;
        vals.concat(sec ? sec.values : []).forEach(function (v) { valW = Math.max(valW, tw(valStr(v))); });
        var leftH = labW + 8, rightH = valW + 12;
        var pwH = Math.max(60, W - leftH - rightH);
        var tickTop = top;
        var plotTop = tickTop + 16;
        H = plotTop + n * rowH + 8 + (unit ? 18 : 0);
        dom = axisDomain([lo, hi], isNum(fmin) ? lo : NaN, isNum(fmax) ? hi : NaN, false, Math.max(2, Math.round(pwH / 90)));
        X = scaleOf(dom, leftH, leftH + pwH);
        base = X(clamp(0, dom.lo, dom.hi));
        svg = makeSvg(el, W, H, aria);
        titleRow(svg, title, 4, W, !!mem.ghost, true);
        if (sec) legendRow(svg, 4, legY, [[o.name || 'values', primaryColor], [sec.name, secColor]]);
        dom.ticks.forEach(function (t) {
          svgEl('line', { x1: X(t), x2: X(t), y1: plotTop, y2: plotTop + n * rowH, 'class': 'k-grid' }, svg);
          txt(svg, X(t), tickTop + 10, tickLabel(dom, t), 'k-tick', { 'text-anchor': 'middle' });
        });
        if (unit) txt(svg, leftH + pwH / 2, H - 4, unit, 'k-axis-label', { 'text-anchor': 'middle' });
        for (var j = 0; j < n; j++) {
          var y0 = plotTop + j * rowH + 4;
          var bh = sec ? (rowH - 10) / 2 : rowH - 8;
          drawHBar(svg, y0, bh, vals[j], pv[j], gv ? gv[j] : undefined, hiSet[j] ? hiCol : primaryColor, anyHi && !hiSet[j], X, base, valStr, labels[j]);
          if (sec) drawHBar(svg, y0 + bh + 2, bh, sec.values[j], ps[j], gs ? gs[j] : undefined, secColor, anyHi && !hiSet[j], X, base, valStr, labels[j] + ' (' + sec.name + ')');
          var tl = txt(svg, leftH - 8, y0 + (rowH - 8) / 2 + 4, trunc(labels[j], (labW - 4) / 7), 'k-tick' + (hiSet[j] ? hiTick : ''), { 'text-anchor': 'end' });
          if (labels[j].length > (labW - 4) / 7) svgEl('title', { text: labels[j] }, tl);
        }
        svgEl('line', { x1: base, x2: base, y1: plotTop, y2: plotTop + n * rowH, 'class': 'k-baseline' }, svg);
      }
      if (anyHi) svg.classList.add('kit-has-focus');
      if (isWatched(title)) svg.classList.add('kit-watched');
      if (o.note) note(el, o.note);
      return svg;
    });
  }
  function legendRow(svg, x, y, items) {
    var cx = x;
    items.forEach(function (it) {
      svgEl('rect', { x: cx, y: y - 10, width: 12, height: 12, rx: 2, fill: it[1] }, svg);
      var lab = trunc(it[0], 24);
      txt(svg, cx + 17, y, lab, 'k-legend');
      cx += 17 + tw(lab) + 18;
    });
  }
  function drawVBar(svg, x, w, v, pv, gv, c, muted, Y, base, valStr, showVal, label) {
    if (isNum(gv) && gv !== v) {
      var gy = Y(gv);
      svgEl('rect', { x: x, y: Math.min(gy, base), width: w, height: Math.max(1, Math.abs(gy - base)), 'class': 'k-ghost-bar' }, svg);
    }
    if (!isNum(v)) {
      txt(svg, x + w / 2, base - 5, '—', 'k-muted', { 'text-anchor': 'middle' });
      return;
    }
    var rect = svgEl('rect', { x: x, width: w, rx: Math.min(3, w / 4), fill: c, opacity: muted ? 0.5 : 1, 'class': 'k-bar' }, svg);
    svgEl('title', { text: str(label) + ': ' + valStr(v) }, rect);
    var lab = showVal ? txt(svg, x + w / 2, 0, valStr(v), 'k-value' + (c === 'var(--accent)' ? ' k-accent k-strong' : ''), { 'text-anchor': 'middle' }) : null;
    tween(function (t) {
      var vv = lerp(isNum(pv) ? pv : v, v, t), y = Y(vv);
      rect.setAttribute('y', s2(Math.min(y, base)));
      rect.setAttribute('height', s2(Math.max(vv === 0 ? 0 : 1, Math.abs(y - base))));
      if (lab) lab.setAttribute('y', s2(vv >= 0 ? y - 5 : y + 14));
    });
  }
  function drawHBar(svg, y, h, v, pv, gv, c, muted, X, base, valStr, label) {
    if (isNum(gv) && gv !== v) {
      var gx = X(gv);
      svgEl('rect', { x: Math.min(gx, base), y: y, width: Math.max(1, Math.abs(gx - base)), height: h, 'class': 'k-ghost-bar' }, svg);
    }
    if (!isNum(v)) { txt(svg, base + 4, y + h / 2 + 4, '—', 'k-muted'); return; }
    var rect = svgEl('rect', { y: y, height: h, rx: Math.min(3, h / 4), fill: c, opacity: muted ? 0.5 : 1, 'class': 'k-bar' }, svg);
    svgEl('title', { text: str(label) + ': ' + valStr(v) }, rect);
    var lab = txt(svg, 0, y + h / 2 + 4, valStr(v), 'k-value' + (c === 'var(--accent)' ? ' k-accent k-strong' : ''), { 'text-anchor': v >= 0 ? 'start' : 'end' });
    tween(function (t) {
      var vv = lerp(isNum(pv) ? pv : v, v, t), x = X(vv);
      rect.setAttribute('x', s2(Math.min(x, base)));
      rect.setAttribute('width', s2(Math.max(vv === 0 ? 0 : 1, Math.abs(x - base))));
      lab.setAttribute('x', s2(vv >= 0 ? Math.max(x, base) + 5 : Math.min(x, base) - 5));
    });
  }

  /* ------------------------------------------------------ cells (matrix etc.) */
  function cellStats(M, scale) {
    var lo = Infinity, hi = -Infinity;
    M.forEach(function (row) { row.forEach(function (v) { if (isNum(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }); });
    if (!isNum(lo)) { lo = 0; hi = 1; }
    var mode = scale === 'div' || scale === 'seq' ? scale : (lo < 0 && hi > 0 ? 'div' : 'seq');
    var m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
    return {
      lo: lo, hi: hi, mode: mode,
      t: function (v) {
        if (!isNum(v)) return 0;
        if (mode === 'div') return clamp(Math.abs(v) / m, 0, 1);
        return hi > lo ? clamp((v - lo) / (hi - lo), 0, 1) : 0.5;
      },
      fill: function (v) { return mode === 'div' && v < 0 ? 'var(--neg)' : 'var(--seq)'; }
    };
  }
  function fitNum(v, d, maxChars) {
    if (!isNum(v)) return isNaN(v) ? 'NaN' : (v > 0 ? '∞' : '-∞');
    var s = fmt(v, d);
    while (s.length > maxChars && d > 0) { d -= 1; s = fmt(v, d); }
    if (s.length > maxChars) s = v.toExponential(0).replace('e+', 'e');
    return s.length > maxChars ? '' : s;
  }
  /* draws a block of cells; opt: {fmt, scale, hi(i,j), prev (2D, tween origin), base (2D, ghost baseline), label} */
  function drawCells(parent, x0, y0, M, cs, opt) {
    var stats = cellStats(M, opt.scale);
    var d = isNum(num(opt.fmt)) ? clamp(Math.round(num(opt.fmt)), 0, 8) : 2;
    var big = cs >= 46;
    var maxChars = Math.floor((cs - 4) / (big ? 8 : 6.9));
    var showNums = maxChars >= 3;
    var numCls = 'k-cell-num' + (big ? ' k-cell-num-l' : '');
    M.forEach(function (row, i) {
      row.forEach(function (v, j) {
        var x = x0 + j * cs, y = y0 + i * cs;
        svgEl('rect', { x: x + 0.5, y: y + 0.5, width: cs - 1, height: cs - 1, 'class': 'k-cell-bg' }, parent);
        var t = stats.t(v);
        var pv = opt.prev && opt.prev[i] ? opt.prev[i][j] : undefined;
        var t0 = isNum(pv) ? stats.t(pv) : t;
        var cell = svgEl('rect', { x: x + 0.5, y: y + 0.5, width: cs - 1, height: cs - 1, fill: stats.fill(v), 'class': 'k-cell' }, parent);
        svgEl('title', { text: (opt.label ? opt.label + ' ' : '') + '[' + (i + 1) + ',' + (j + 1) + '] = ' + fmt(v, Math.max(d, 3)) }, cell);
        tween(function (a) { cell.setAttribute('fill-opacity', s2(0.06 + 0.86 * lerp(t0, t, a))); });
        if (!isNum(v)) svgEl('line', { x1: x + 4, y1: y + cs - 4, x2: x + cs - 4, y2: y + 4, 'class': 'k-nan' }, parent);
        if (showNums) {
          txt(parent, x + cs / 2, y + cs / 2 + (big ? 5 : 4), fitNum(v, d, maxChars), numCls + onCls(0.06 + 0.86 * t), { 'text-anchor': 'middle' });
        }
        var bv = opt.base && opt.base[i] ? opt.base[i][j] : undefined;
        if (isNum(bv) && isNum(v) && Math.abs(bv - v) > 1e-12 * Math.max(1, Math.abs(v)) && cs >= 16) {
          var up = v > bv, s = Math.min(5, cs / 6);
          var px = x + cs - s - 3, py = y + s + 2;
          svgEl('path', { d: up ? 'M' + r2(px - s) + ',' + r2(py + s / 2) + 'L' + r2(px) + ',' + r2(py - s) + 'L' + r2(px + s) + ',' + r2(py + s / 2) + 'Z'
            : 'M' + r2(px - s) + ',' + r2(py - s / 2) + 'L' + r2(px) + ',' + r2(py + s) + 'L' + r2(px + s) + ',' + r2(py - s / 2) + 'Z', 'class': 'k-delta' + onCls(0.06 + 0.86 * t) }, parent);
        }
        if (opt.hi && opt.hi(i, j)) svgEl('rect', { x: x + 1.5, y: y + 1.5, width: cs - 3, height: cs - 3, 'class': 'k-cell-hi' }, parent);
      });
    });
    return stats;
  }

  function matrix(el, o) {
    return safe('matrix', el, o, function (el, o) {
      var W = widthOf(el);
      var M = to2D(o.data);
      var C = 0; M.forEach(function (r) { C = Math.max(C, r.length); });
      if (!M.length || !C) return emptyState(el, o.title, 'No matrix data to show yet.', W);
      M = M.map(function (r) { var c = r.slice(); while (c.length < C) c.push(NaN); return c; });
      var R = M.length;
      var rl = arr(o.rowLabels).map(str), cl = arr(o.colLabels).map(str);
      var rlw = 0; rl.forEach(function (l) { rlw = Math.max(rlw, l.length); });
      var labW = rl.length ? Math.min(W * 0.3, rlw * 7 + 10) : 0;
      var cs = Math.floor(clamp((W - labW - 8) / C, 12, 64));
      if (R * cs > 560) cs = Math.max(8, Math.floor(560 / R));
      var title = str(o.title);
      var mem = remember(slot('matrix', title), M);
      var gridW = labW + C * cs + 8;
      var w = Math.max(gridW, Math.min(W, Math.max(240, Math.min(tw(title, 13.5) + 12, Math.max(gridW, 340)), tw('colour: low 0.000 → high 0.000') + labW + 8)));
      var top = (title || mem.ghost ? 28 : 6) + (cl.length ? 18 : 0) + titleExtra(title, w, 2, false);
      var st0 = cellStats(M, o.scale);
      var legend = 'colour: ' + (st0.mode === 'div' ? 'negative ← 0 → positive (max |v| ' + fmt(Math.max(Math.abs(st0.lo), Math.abs(st0.hi)), 3) + ')' : 'low ' + fmt(st0.lo, 3) + ' → high ' + fmt(st0.hi, 3));
      var legChars = Math.max(16, (w - labW - 2) / 6.5);
      var legN = wrapWords(legend + ' · ▲▼ changed', legChars, 2).length; // reserve: no jump when ▲▼ appears
      var H = top + R * cs + 12 + legN * 15;
      var svg = makeSvg(el, w, H, (title || 'Matrix') + ' (' + R + '×' + C + ')', true);
      titleRow(svg, title, 2, w, false);
      var hiMap = {};
      arr(o.highlight).forEach(function (p) { if (Array.isArray(p)) hiMap[Math.round(num(p[0])) + ',' + Math.round(num(p[1]))] = true; });
      var colChars = Math.max(1, Math.floor((cs - 2) / 6.9));
      cl.slice(0, C).forEach(function (l, j) {
        var t = txt(svg, labW + j * cs + cs / 2, top - 6, trunc(l, colChars), 'k-tick', { 'text-anchor': 'middle' });
        if (l.length > colChars) svgEl('title', { text: l }, t);
      });
      rl.slice(0, R).forEach(function (l, i) {
        if (cs >= 12) txt(svg, labW - 6, top + i * cs + cs / 2 + 4, trunc(l, (labW - 6) / 7), 'k-tick', { 'text-anchor': 'end' });
      });
      var stats = drawCells(svg, labW, top, M, cs, {
        fmt: o.fmt, scale: o.scale, label: title,
        hi: function (i, j) { return !!hiMap[i + ',' + j]; },
        prev: mem.prev, base: mem.ghost
      });
      if (mem.ghost) legend += ' · ▲▼ changed';
      wrapWords(legend, legChars, 2).forEach(function (l, k) { txt(svg, labW, top + R * cs + 17 + k * 15, l, 'k-muted k-small-note'); });
      if (Object.keys(hiMap).length) svg.classList.add('kit-has-focus');
      if (isWatched(title)) svg.classList.add('kit-watched');
      if (o.note) note(el, o.note);
      return svg;
    });
  }

  /* --------------------------------------------------------------- pipeline */
  function stageKind(s) {
    var k = str(s.kind);
    if (k === 'matrix' || k === 'vector' || k === 'scalar' || k === 'text') return k;
    if (is2D(s.data)) return 'matrix';
    if (Array.isArray(s.data)) return 'vector';
    if (typeof s.data === 'number') return 'scalar';
    return 'text';
  }
  function pipeline(el, o) {
    return safe('pipeline', el, o, function (el, o) {
      var W = widthOf(el);
      var stages = arr(o.stages).filter(function (s) { return s && typeof s === 'object'; });
      var ops = arr(o.ops).map(str);
      if (!stages.length) return emptyState(el, o.title, 'No pipeline stages to show yet.', W);
      var mem = remember(slot('pipeline', o.title), stages.map(function (s) { return s.data === undefined ? null : s.data; }));
      var maxStageW = Math.min(W - 16, 340);
      var units = stages.map(function (s, i) {
        var kind = stageKind(s), u = { s: s, kind: kind, i: i };
        if (kind === 'matrix' || kind === 'vector') {
          var M = to2D(s.data);
          if (kind === 'vector') {
            var v = M[0] || [];
            M = v.length > 8 ? v.slice(0, 24).map(function (x) { return [x]; }) : [v];
            u.trunc = v.length > 24;
          }
          var C = 0; M.forEach(function (r) { C = Math.max(C, r.length); });
          M = M.map(function (r) { var c = r.slice(); while (c.length < C) c.push(NaN); return c; });
          u.M = M;
          u.cs = C ? Math.floor(clamp(maxStageW / C, 16, 50)) : 30;
          if (M.length * u.cs > 420) u.cs = Math.max(12, Math.floor(420 / M.length));
          u.bw = Math.max(C * u.cs, 24); u.bh = Math.max(M.length * u.cs, 24) + (u.trunc ? 14 : 0);
          if (!C) { u.kind = 'text'; u.lines = ['(empty)']; }
        }
        if (u.kind === 'scalar') {
          u.label = typeof s.data === 'number' ? fmt(s.data, isNum(num(o.fmt)) ? num(o.fmt) : 3) : str(s.data);
          u.bw = Math.max(64, tw(u.label, 20) + 26); u.bh = 46;
        }
        if (u.kind === 'text') {
          u.lines = u.lines || wrapWords(typeof s.data === 'string' ? s.data : fmt(s.data, 3), 26, 5);
          if (!u.lines.length) u.lines = ['—'];
          var mw = 0; u.lines.forEach(function (l) { mw = Math.max(mw, tw(l)); });
          u.bw = Math.min(maxStageW, mw + 20); u.bh = u.lines.length * 16 + 14;
        }
        u.sw = Math.max(u.bw, Math.min(maxStageW, Math.max(80, tw(s.title || '', 13) + 4)));
        u.tl = wrapWords(str(s.title) || ('stage ' + (i + 1)), u.sw / 7.4, 3);
        u.cap = s.caption ? wrapWords(s.caption, u.sw / 6.6, 3) : [];
        return u;
      });
      var HH = 8;
      units.forEach(function (u) { HH = Math.max(HH, 8 + u.tl.length * 14); });
      units.forEach(function (u, i) {
        u.h = HH + u.bh + (u.cap.length ? u.cap.length * 15 + 6 : 0);
        var op = i > 0 ? (ops[i - 1] || '') : '';
        u.op = op;
        u.aw = i > 0 ? Math.max(48, tw(op, 13) + 18) : 0;
        u.w = u.aw + u.sw;
      });
      // flow layout
      var gText = '▲▼ = changed since last edit', gNote = mem.ghost ? gText : false;
      var pad = 6, gapY = 22, x = pad, y = ((o.title || mem.ghost) ? 30 : 6) + titleExtra(o.title, W, 2, gText), rowH = 0, rows = [[]];
      units.forEach(function (u) {
        if (x + u.w > W - pad && x > pad) { y += rowH + gapY; x = pad; rowH = 0; rows.push([]); }
        u.x = x; u.y = y; x += u.w + 4; rowH = Math.max(rowH, u.h);
        rows[rows.length - 1].push(u);
      });
      var H = y + rowH + 8;
      var title = str(o.title);
      var svg = makeSvg(el, W, H, (title || 'Pipeline') + ': ' + units.map(function (u) { return (u.op ? u.op + ' → ' : '') + str(u.s.title); }).join(' '));
      titleRow(svg, title, 2, W, gNote, gText);
      var anyHi = false;
      units.forEach(function (u) {
        var sx = u.x + u.aw;
        var hi = !!u.s.highlight || isWatched(u.s.title);
        if (hi) anyHi = true;
        if (u.aw) {
          var ay = u.y + HH + Math.min(u.bh, 60) / 2;
          svgEl('line', { x1: u.x + 4, x2: u.x + u.aw - 10, y1: ay, y2: ay, 'class': 'k-arrow' }, svg);
          svgEl('path', { d: 'M' + r2(u.x + u.aw - 4) + ',' + r2(ay) + 'l-8,-5v10z', 'class': 'k-arrowhead' }, svg);
          if (u.op) txt(svg, u.x + u.aw / 2 - 3, ay - 8, u.op, 'k-op', { 'text-anchor': 'middle' });
        }
        u.tl.forEach(function (l, k) { txt(svg, sx, u.y + 14 + k * 14, l, 'k-stage-title' + (hi ? ' k-accent' : '')); });
        var by = u.y + HH;
        var bx = sx + (u.sw - u.bw) / 2;
        if (u.kind === 'matrix' || u.kind === 'vector') {
          var prev = mem.prev ? mem.prev[u.i] : undefined, base = mem.ghost ? mem.ghost[u.i] : undefined;
          var shape = function (d) {
            if (d === undefined || d === null) return null;
            var M = to2D(d);
            if (u.kind === 'vector') { var v = M[0] || []; M = v.length > 8 ? v.slice(0, 24).map(function (q) { return [q]; }) : [v]; }
            return M;
          };
          drawCells(svg, bx, by, u.M, u.cs, { fmt: isNum(num(u.s.fmt)) ? u.s.fmt : o.fmt, scale: u.s.scale, prev: shape(prev), base: shape(base), label: u.s.title });
          if (u.trunc) txt(svg, bx, by + u.bh - 2, '… (first 24 shown)', 'k-muted');
        } else if (u.kind === 'scalar') {
          svgEl('rect', { x: bx, y: by, width: u.bw, height: u.bh, rx: 8, 'class': 'k-scalar-box' }, svg);
          txt(svg, bx + u.bw / 2, by + u.bh / 2 + 7, u.label, 'k-big' + (hi ? ' k-accent' : ''), { 'text-anchor': 'middle' });
        } else {
          svgEl('rect', { x: bx, y: by, width: u.bw, height: u.bh, rx: 6, 'class': 'k-text-box' }, svg);
          u.lines.forEach(function (l, k) { txt(svg, bx + 10, by + 20 + k * 16, l, 'k-stage-text'); });
        }
        if (hi) svgEl('rect', { x: bx - 4, y: by - 4, width: u.bw + 8, height: u.bh + 8, rx: 6, 'class': 'k-focus-ring' }, svg);
        u.cap.forEach(function (l, k) { txt(svg, sx, by + u.bh + 17 + k * 15, l, 'k-caption'); });
      });
      if (anyHi) svg.classList.add('kit-has-focus');
      if (o.note) note(el, o.note);
      return svg;
    });
  }

  /* ---------------------------------------------------------------- timeline */
  function button(text, aria, onClick) {
    var b = document.createElement('button');
    b.setAttribute('type', 'button');
    b.setAttribute('class', 'kit-btn');
    b.setAttribute('aria-label', aria);
    b.textContent = text;
    b.addEventListener('click', onClick);
    return b;
  }
  function timeline(el, o) {
    o = (o && typeof o === 'object') ? o : {};
    try {
      var len = Math.max(1, Math.floor(num(o.length)) || 1);
      var key = 'tl#' + st.n; st.n += 1;
      var s = st.tl[key];
      if (!s) {
        var t0 = num(o.t0);
        s = st.tl[key] = { t: isNum(t0) ? t0 : len - 1, playing: false, inst: 0 };
      }
      s.inst += 1;
      var inst = s.inst;
      s.t = clamp(Math.round(isNum(s.t) ? s.t : 0), 0, len - 1);
      var fps = clamp(num(o.fps) || 12, 1, 60);
      var unit = str(o.label) || 'step';
      var draw = typeof o.draw === 'function' ? o.draw : null;

      var wrap = document.createElement('div');
      wrap.setAttribute('class', 'kit-timeline');
      var bar = document.createElement('div');
      bar.setAttribute('class', 'kit-tl-bar');
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', 'Time controls');
      var sub = document.createElement('div');
      sub.setAttribute('class', 'kit-tl-frame');
      var scrub = document.createElement('input');
      scrub.setAttribute('type', 'range');
      scrub.setAttribute('min', '0');
      scrub.setAttribute('max', String(len - 1));
      scrub.setAttribute('step', '1');
      scrub.setAttribute('class', 'kit-tl-scrub');
      scrub.setAttribute('aria-label', 'Scrub through time (' + unit + ')');
      var read = document.createElement('span');
      read.setAttribute('class', 'kit-tl-read');
      read.setAttribute('aria-live', 'off');

      var playBtn;
      function setPlayLabel() {
        playBtn.textContent = s.playing ? '⏸ Pause' : '▶ Play';
        playBtn.setAttribute('aria-label', s.playing ? 'Pause' : 'Play');
        playBtn.setAttribute('aria-pressed', s.playing ? 'true' : 'false');
      }
      function show(t) {
        s.t = clamp(Math.round(t), 0, len - 1);
        scrub.value = String(s.t);
        read.textContent = unit + ' ' + s.t + ' / ' + (len - 1);
        sub.replaceChildren();
        if (!draw) { emptyState(sub, '', 'kit.timeline needs a draw(t, sub) function.'); return; }
        var savedN = st.n, savedM = st.noMotion;
        st.noMotion = true; st.n = 100000;
        try { draw(s.t, sub); } catch (e) {
          warn('timeline draw failed', e);
          emptyState(sub, '', 'Could not draw ' + unit + ' ' + s.t + ': ' + errMsg(e));
        } finally { st.noMotion = savedM; st.n = savedN; }
      }
      var hasTimer = typeof setTimeout === 'function';
      function tick() {
        if (s.inst !== inst || !s.playing) return;
        if (s.t >= len - 1) { s.playing = false; setPlayLabel(); return; }
        show(s.t + 1);
        if (s.t >= len - 1) { s.playing = false; setPlayLabel(); return; }
        setTimeout(tick, 1000 / fps);
      }
      function play() {
        if (!hasTimer) return;
        if (s.t >= len - 1) show(0);
        s.playing = true; setPlayLabel();
        setTimeout(tick, 1000 / fps);
      }
      function pause() { s.playing = false; setPlayLabel(); }
      var resetBtn = button('⏮', 'Back to the start', function () { pause(); show(0); });
      var backBtn = button('◂', 'Step back one ' + unit, function () { pause(); show(s.t - 1); });
      playBtn = button('▶ Play', 'Play', function () { if (s.playing) pause(); else play(); });
      playBtn.classList.add('kit-btn-play');
      var fwdBtn = button('▸', 'Step forward one ' + unit, function () { pause(); show(s.t + 1); });
      scrub.addEventListener('input', function () { pause(); show(num(scrub.value)); });
      bar.append(resetBtn, backBtn, playBtn, fwdBtn, scrub, read);
      wrap.append(bar, sub);
      attach(el, wrap);
      setPlayLabel();
      show(s.t);
      if (s.playing && hasTimer) setTimeout(tick, 1000 / fps);
      return wrap;
    } catch (e) {
      warn('timeline failed', e);
      return emptyState(el, o.title, 'Could not build the timeline: ' + errMsg(e));
    }
  }

  /* -------------------------------------------------------------------- grid */
  function grid(el, o) {
    return safe('grid', el, o, function (el, o) {
      var W = widthOf(el);
      var cells = arr(o.cells).map(function (r) { return arr(r); });
      var R = cells.length, C = 0;
      cells.forEach(function (r) { C = Math.max(C, r.length); });
      if (!R || !C) return emptyState(el, o.title, 'No grid cells to show yet.', W);
      var title = str(o.title);
      var cs = isNum(num(o.cellSize)) ? clamp(num(o.cellSize), 2, 60) : Math.floor(clamp((W - 4) / C, 2, 40));
      if (R * cs > 440 && !isNum(num(o.cellSize))) cs = Math.max(2, Math.floor(440 / R));
      var palette = (o.palette && typeof o.palette === 'object') ? o.palette : {};
      var labels = (o.labels && typeof o.labels === 'object') ? o.labels : {};
      var counts = {}, distinct = [], numeric = true, nonInt = false, lo = Infinity, hi = -Infinity;
      cells.forEach(function (r) {
        r.forEach(function (v) {
          var k = str(v);
          if (!Object.prototype.hasOwnProperty.call(counts, k)) { counts[k] = 0; distinct.push(v); }
          counts[k] += 1;
          var nv = num(v);
          if (!isNum(nv)) numeric = false; else { if (nv !== Math.round(nv)) nonInt = true; lo = Math.min(lo, nv); hi = Math.max(hi, nv); }
        });
      });
      var continuous = numeric && (nonInt || distinct.length > 10) && !Object.keys(palette).length;
      var catColor = {};
      if (!continuous) {
        distinct.sort(function (a, b) { var x = num(a), y = num(b); return isNum(x) && isNum(y) ? x - y : str(a) < str(b) ? -1 : 1; });
        var ci = 0;
        distinct.forEach(function (v) {
          var k = str(v);
          if (Object.prototype.hasOwnProperty.call(palette, k)) catColor[k] = str(palette[k]);
          else if ((v === 0 || v === '0' || v === false || v === null || v === '' || v === undefined) &&
            (!labels[k] || /^(empty|none|vacant|dead|background|blank|off|nothing|free)$/i.test(str(labels[k])))) catColor[k] = 'var(--empty)';
          else { catColor[k] = labels[k] ? colorFor(labels[k]) : color(ci); ci += 1; }
        });
      }
      var gw = C * cs, gh = R * cs;
      var legendItems = continuous ? [] : distinct.map(function (v) { return v; });
      var legendRows = continuous ? 1 : Math.ceil(legendItems.length / Math.max(1, Math.floor((Math.max(gw, 240)) / 150)));
      var w = Math.max(gw + 4, Math.min(W, Math.max(260, Math.min(tw(title, 13.5) + 12, Math.max(gw + 4, 340)))));
      var top = (title ? 26 : 4) + titleExtra(title, w, 2, false);
      var H = top + gh + 10 + legendRows * 18 + 4;
      var svg = makeSvg(el, w, H, (title || 'Grid') + ' (' + R + '×' + C + ')', true);
      titleRow(svg, title, 2, w, false);
      svgEl('rect', { x: 2, y: top, width: gw, height: gh, 'class': 'k-grid-bg' }, svg);
      var gap = cs >= 8 ? 1 : 0;
      var hiMap = {};
      arr(o.highlight).forEach(function (p) { if (Array.isArray(p)) hiMap[Math.round(num(p[0])) + ',' + Math.round(num(p[1]))] = true; });
      cells.forEach(function (r, i) {
        r.forEach(function (v, j) {
          var k = str(v), a = { x: 2 + j * cs + gap / 2, y: top + i * cs + gap / 2, width: cs - gap, height: cs - gap };
          if (continuous) {
            var nv = num(v);
            a.fill = 'var(--seq)';
            a['fill-opacity'] = isNum(nv) ? r2(0.06 + 0.88 * (hi > lo ? (nv - lo) / (hi - lo) : 0.5)) : 0;
          } else a.fill = catColor[k] || 'var(--empty)';
          svgEl('rect', a, svg);
        });
      });
      Object.keys(hiMap).forEach(function (k) {
        var p = k.split(',').map(Number);
        svgEl('rect', { x: 2 + p[1] * cs - 1, y: top + p[0] * cs - 1, width: cs + 2, height: cs + 2, 'class': 'k-cell-hi' }, svg);
      });
      var ly = top + gh + 22, total = R * C;
      if (continuous) {
        txt(svg, 2, ly, 'shade: low ' + fmt(lo, 3) + ' → high ' + fmt(hi, 3), 'k-muted');
      } else {
        var lx = 2, perRow = Math.max(1, Math.floor(Math.max(gw, 240) / 150)), col = 0;
        legendItems.forEach(function (v) {
          var k = str(v);
          var name = Object.prototype.hasOwnProperty.call(labels, k) ? str(labels[k]) : k;
          var lab = trunc(name, 13) + ' ' + Math.round(100 * counts[k] / total) + '%';
          svgEl('rect', { x: lx, y: ly - 10, width: 12, height: 12, rx: 2, fill: catColor[k], 'class': 'k-swatch' }, svg);
          txt(svg, lx + 16, ly, lab, 'k-legend');
          col += 1; lx += 150;
          if (col >= perRow) { col = 0; lx = 2; ly += 18; }
        });
      }
      if (isWatched(title)) svg.classList.add('kit-watched');
      if (o.note) note(el, o.note);
      return svg;
    });
  }

  /* ------------------------------------------------------------------- graph */
  function graph(el, o) {
    return safe('graph', el, o, function (el, o) {
      var W = widthOf(el);
      var nodes = arr(o.nodes).filter(function (n) { return n && n.id !== undefined && n.id !== null; });
      if (!nodes.length) return emptyState(el, o.title, 'No nodes to show yet.', W);
      var edges = arr(o.edges).filter(function (e) { return e && e.from !== undefined && e.to !== undefined; });
      var title = str(o.title);
      var tEx = titleExtra(title, W, 2, false);
      var H = Math.round(clamp(W * 0.6, 240, 440)) + tEx;
      var rad = clamp(Math.min(W, H) / 16, 14, 24);
      var top = (title ? 26 : 4) + tEx;
      var pad = rad + 26;
      var byId = {};
      nodes.forEach(function (n, i) {
        var x = num(n.x), y = num(n.y);
        if (!isNum(x) || !isNum(y)) { var a = 2 * Math.PI * i / nodes.length - Math.PI / 2; x = 0.5 + 0.42 * Math.cos(a); y = 0.5 + 0.42 * Math.sin(a); }
        byId[str(n.id)] = { n: n, x: pad + clamp(x, 0, 1) * (W - 2 * pad), y: top + pad + clamp(y, 0, 1) * (H - top - 2 * pad) };
      });
      var hiSet = {};
      arr(o.highlight).forEach(function (id) { hiSet[str(id)] = true; });
      nodes.forEach(function (n) { if (isWatched(n.label) || isWatched(n.id)) hiSet[str(n.id)] = true; });
      var maxW = 0;
      edges.forEach(function (e) { var w = Math.abs(num(e.weight)); if (isNum(w)) maxW = Math.max(maxW, w); });
      var vals = nodes.map(function (n) { return num(n.value); }).filter(isNum);
      var vmax = vals.length ? Math.max.apply(null, vals.map(Math.abs)) : 0;
      var svg = makeSvg(el, W, H, (title || 'Graph') + ': ' + nodes.length + ' nodes, ' + edges.length + ' edges');
      titleRow(svg, title, 2, W, false);
      var pairs = {};
      edges.forEach(function (e) { pairs[str(e.from) + '>' + str(e.to)] = true; });
      var eg = svgEl('g', null, svg), lg = svgEl('g', null, svg);
      edges.forEach(function (e) {
        var a = byId[str(e.from)], b = byId[str(e.to)];
        if (!a || !b) return;
        var w = Math.abs(num(e.weight));
        var frac = maxW > 0 && isNum(w) ? w / maxW : 0.5;
        var hi = !!e.highlight;
        var cls = 'k-edge' + (hi ? ' k-edge-hi' : '');
        var sw = r2(1 + 4.5 * frac);
        var op = r2(0.35 + 0.65 * frac);
        var lab = e.label !== undefined && e.label !== null ? str(e.label) : (isNum(num(e.weight)) ? fmt(num(e.weight), 2) : '');
        var lx, ly;
        if (a === b) {
          var cx = a.x, cy = a.y - rad;
          svgEl('path', { d: 'M' + r2(cx - rad * 0.6) + ',' + r2(cy + 4) + 'C' + r2(cx - rad * 1.6) + ',' + r2(cy - rad * 1.9) + ' ' + r2(cx + rad * 1.6) + ',' + r2(cy - rad * 1.9) + ' ' + r2(cx + rad * 0.6) + ',' + r2(cy + 4), 'class': cls, 'stroke-width': sw, opacity: op }, eg);
          arrowHead(eg, cx + rad * 0.6, cy + 4, cx + rad * 1.0, cy - rad * 0.6, hi, op);
          lx = cx; ly = cy - rad * 1.5;
        } else {
          var dx = b.x - a.x, dy = b.y - a.y, L = Math.sqrt(dx * dx + dy * dy) || 1;
          var ux = dx / L, uy = dy / L;
          var bend = pairs[str(e.to) + '>' + str(e.from)] ? 18 : 0;
          var mx = (a.x + b.x) / 2 - uy * bend, my = (a.y + b.y) / 2 + ux * bend;
          var sx = a.x + ux * rad, sy = a.y + uy * rad;
          var tdx = b.x - mx, tdy = b.y - my, tl = Math.sqrt(tdx * tdx + tdy * tdy) || 1;
          var ex = b.x - tdx / tl * (rad + 2), ey = b.y - tdy / tl * (rad + 2);
          svgEl('path', { d: 'M' + r2(sx) + ',' + r2(sy) + 'Q' + r2(mx) + ',' + r2(my) + ' ' + r2(ex) + ',' + r2(ey), 'class': cls, 'stroke-width': sw, opacity: op }, eg);
          arrowHead(eg, mx, my, ex, ey, hi, op);
          lx = (a.x + b.x) / 2 - uy * bend * 0.9; ly = (a.y + b.y) / 2 + ux * bend * 0.9;
        }
        if (lab) txt(lg, lx, ly + 4, trunc(lab, 14), 'k-edge-label k-halo' + (hi ? ' k-accent' : ''), { 'text-anchor': 'middle' });
      });
      nodes.forEach(function (n) {
        var p = byId[str(n.id)], hi = !!hiSet[str(n.id)];
        var v = num(n.value);
        var g = svgEl('g', { 'class': 'k-node' + (hi ? ' k-node-hi' : '') }, svg);
        svgEl('circle', { cx: p.x, cy: p.y, r: rad, 'class': 'k-node-bg' }, g);
        if (isNum(v) && vmax > 0) svgEl('circle', { cx: p.x, cy: p.y, r: rad, fill: v < 0 ? 'var(--neg)' : 'var(--seq)', 'fill-opacity': r2(0.08 + 0.8 * Math.abs(v) / vmax) }, g);
        svgEl('circle', { cx: p.x, cy: p.y, r: rad, 'class': 'k-node-ring' }, g);
        var label = str(n.label !== undefined ? n.label : n.id);
        var inside = tw(label) <= rad * 1.8;
        if (inside) txt(g, p.x, p.y + 4, label, 'k-node-label' + (isNum(v) && vmax > 0 ? onCls(0.08 + 0.8 * Math.abs(v) / vmax) : ''), { 'text-anchor': 'middle' });
        var below = (inside ? '' : trunc(label, 18)) + (isNum(v) ? (inside ? '' : ' ') + '= ' + fmt(v, isNum(num(o.fmt)) ? num(o.fmt) : 2) : '');
        if (below) txt(g, p.x, p.y + rad + 15, below, 'k-node-sub k-halo' + (hi ? ' k-accent' : ''), { 'text-anchor': 'middle' });
        svgEl('title', { text: label + (isNum(v) ? ' = ' + fmt(v, 4) : '') }, g);
      });
      if (Object.keys(hiSet).length) svg.classList.add('kit-has-focus');
      if (o.note) note(el, o.note);
      return svg;
    });
  }
  function arrowHead(parent, fx, fy, tx, ty, hi, op) {
    var dx = tx - fx, dy = ty - fy, L = Math.sqrt(dx * dx + dy * dy) || 1;
    var ux = dx / L, uy = dy / L, s = 9;
    var bx = tx - ux * s, by = ty - uy * s;
    svgEl('path', { d: 'M' + r2(tx) + ',' + r2(ty) + 'L' + r2(bx - uy * s * 0.5) + ',' + r2(by + ux * s * 0.5) + 'L' + r2(bx + uy * s * 0.5) + ',' + r2(by - ux * s * 0.5) + 'Z', 'class': 'k-edge-head' + (hi ? ' k-edge-hi' : ''), opacity: op }, parent);
  }

  /* ------------------------------------------------------------------- vec2d */
  function installDrag() {
    if (st.dragInit || typeof window === 'undefined' || !window.addEventListener) return;
    st.dragInit = true;
    window.addEventListener('pointermove', function (ev) {
      var d = st.drag; if (!d) return;
      var reg = st.vec[d.key]; if (!reg || !reg.svg) return;
      var rect = reg.svg.getBoundingClientRect();
      if (!rect || !rect.width) return;
      var vx = (ev.clientX - rect.left) / rect.width * reg.W, vy = (ev.clientY - rect.top) / rect.height * reg.H;
      var x = clamp(reg.ix(vx), reg.dom.lo, reg.dom.hi), y = clamp(reg.iy(vy), reg.dom.lo, reg.dom.hi);
      var dec = clamp(Math.ceil(-Math.log10(reg.dom.step || 0.1)) + 1, 0, 6);
      set(d.id + '_x', parseFloat(x.toFixed(dec)));
      set(d.id + '_y', parseFloat(y.toFixed(dec)));
      if (ev.preventDefault) ev.preventDefault();
    });
    var end = function () { st.drag = null; };
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  }
  function vec2d(el, o) {
    return safe('vector plot', el, o, function (el, o) {
      var Wf = widthOf(el);
      var vectors = arr(o.vectors).filter(function (v) { return v && typeof v === 'object'; });
      var handles = arr(o.handles).filter(function (h) { return h && h.id !== undefined; });
      var title = str(o.title);
      var R;
      if (Array.isArray(o.range)) R = { lo: num(o.range[0]), hi: num(o.range[1]) };
      else if (isNum(num(o.range)) && num(o.range) > 0) R = { lo: -num(o.range), hi: num(o.range) };
      else {
        var m = 1;
        vectors.forEach(function (v) {
          var f = arr(v.from);
          var fx = isNum(num(f[0])) ? num(f[0]) : 0, fy = isNum(num(f[1])) ? num(f[1]) : 0;
          [fx, fy, fx + num(v.x), fy + num(v.y)].forEach(function (q) { if (isNum(q)) m = Math.max(m, Math.abs(q)); });
        });
        handles.forEach(function (h) { [num(h.x), num(h.y)].forEach(function (q) { if (isNum(q)) m = Math.max(m, Math.abs(q)); }); });
        R = { lo: -m * 1.15, hi: m * 1.15 };
      }
      if (!isNum(R.lo) || !isNum(R.hi) || R.lo >= R.hi) R = { lo: -1, hi: 1 };
      var S = Math.round(Math.min(Wf, 480));
      var top = (title ? 26 : 6) + titleExtra(title, S, 2, true), pad = 30;
      var side = S - pad - 10;
      var W = S, H = top + side + 26 + (handles.length ? 18 : 0);
      var dom = axisDomain([R.lo, R.hi], R.lo, R.hi, false, Math.max(2, Math.round(side / 60)));
      var X = scaleOf(dom, pad, pad + side), Y = scaleOf(dom, top + side, top);
      var mem = remember(slot('vec2d', title), vectors.map(function (v) { return [str(v.label), num(v.x), num(v.y), arr(v.from).map(num)]; }));
      var svg = makeSvg(el, W, H, (title || 'Vector plot') + ': ' + vectors.map(function (v) { return str(v.label) + ' (' + fmt(num(v.x), 2) + ', ' + fmt(num(v.y), 2) + ')'; }).join('; '), true);
      svg.style.touchAction = 'none';
      titleRow(svg, title, 2, W, !!mem.ghost, true);
      dom.ticks.forEach(function (t) {
        svgEl('line', { x1: X(t), x2: X(t), y1: top, y2: top + side, 'class': 'k-grid' }, svg);
        svgEl('line', { x1: pad, x2: pad + side, y1: Y(t), y2: Y(t), 'class': 'k-grid' }, svg);
        txt(svg, X(t), top + side + 15, tickLabel(dom, t), 'k-tick', { 'text-anchor': 'middle' });
        txt(svg, pad - 5, Y(t) + 4, tickLabel(dom, t), 'k-tick', { 'text-anchor': 'end' });
      });
      svgEl('rect', { x: pad, y: top, width: side, height: side, 'class': 'k-frame' }, svg);
      if (dom.lo < 0 && dom.hi > 0) {
        svgEl('line', { x1: X(0), x2: X(0), y1: top, y2: top + side, 'class': 'k-zero' }, svg);
        svgEl('line', { x1: pad, x2: pad + side, y1: Y(0), y2: Y(0), 'class': 'k-zero' }, svg);
      }
      var xl = o.x && o.x.label ? o.x.label : '', yl = o.y && o.y.label ? o.y.label : '';
      if (xl) txt(svg, pad + side - 2, Y(clamp(0, dom.lo, dom.hi)) - 6, xl, 'k-axis-label k-halo', { 'text-anchor': 'end' });
      if (yl) txt(svg, X(clamp(0, dom.lo, dom.hi)) + 6, top + 13, yl, 'k-axis-label k-halo');
      var arrow = function (parent, fx, fy, vx, vy, cls, col) {
        var x1 = X(fx), y1 = Y(fy), x2 = X(fx + vx), y2 = Y(fy + vy);
        if (![x1, y1, x2, y2].every(isNum)) return null;
        svgEl('line', { x1: x1, y1: y1, x2: x2, y2: y2, 'class': cls, stroke: col }, parent);
        var L = Math.sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1));
        if (L > 4) {
          var ux = (x2 - x1) / L, uy = (y2 - y1) / L, s = 10, bx = x2 - ux * s, by = y2 - uy * s;
          svgEl('path', { d: 'M' + r2(x2) + ',' + r2(y2) + 'L' + r2(bx - uy * 5) + ',' + r2(by + ux * 5) + 'L' + r2(bx + uy * 5) + ',' + r2(by - ux * 5) + 'Z', fill: col, 'class': cls + '-head' }, parent);
        }
        return [x2, y2, x1, y1];
      };
      if (mem.ghost) mem.ghost.forEach(function (g) {
        var f = arr(g[3]);
        arrow(svg, isNum(f[0]) ? f[0] : 0, isNum(f[1]) ? f[1] : 0, g[1], g[2], 'k-ghost', 'var(--ghost)');
      });
      var anyHi = false;
      vectors.forEach(function (v, i) {
        var f = arr(v.from), fx = isNum(num(f[0])) ? num(f[0]) : 0, fy = isNum(num(f[1])) ? num(f[1]) : 0;
        var hi = !!v.highlight || isWatched(v.label);
        if (hi) anyHi = true;
        var col = hi ? 'var(--accent)' : (v.color ? str(v.color) : colorFor(v.label || ('vector' + i)));
        var end = arrow(svg, fx, fy, num(v.x), num(v.y), 'k-vec' + (hi ? ' k-vec-hi' : ''), col);
        if (end && v.label) {
          var dx = end[0] - end[2], dy = end[1] - end[3], L = Math.sqrt(dx * dx + dy * dy) || 1;
          txt(svg, end[0] + dx / L * 10, end[1] + dy / L * 10 + 4, trunc(v.label, 18), 'k-series-label k-halo' + (hi ? ' k-strong' : ''), { fill: col, 'text-anchor': dx < -1 ? 'end' : 'start' });
        }
      });
      var key = 'vec2d#' + (st.n - 1);
      st.vec[key] = { svg: svg, W: W, H: H, dom: dom, ix: invScale(dom, pad, pad + side), iy: invScale(dom, top + side, top) };
      handles.forEach(function (h) {
        var hx = X(num(h.x)), hy = Y(num(h.y));
        if (!isNum(hx) || !isNum(hy)) return;
        var id = str(h.id);
        var g = svgEl('g', { 'class': 'kit-handle', tabindex: '0', role: 'slider', 'aria-label': 'Drag point ' + (h.label || id) + ' (arrow keys move it)', 'aria-valuetext': '(' + fmt(num(h.x), 2) + ', ' + fmt(num(h.y), 2) + ')' }, svg);
        svgEl('circle', { cx: hx, cy: hy, r: 18, 'class': 'k-handle-hit' }, g);
        svgEl('circle', { cx: hx, cy: hy, r: 8, 'class': 'k-handle' }, g);
        if (h.label) txt(g, hx + 12, hy - 10, trunc(h.label, 14), 'k-tick k-halo');
        g.addEventListener('pointerdown', function (ev) {
          installDrag();
          st.drag = { key: key, id: id };
          if (ev && ev.preventDefault) ev.preventDefault();
        });
        g.addEventListener('keydown', function (ev) {
          var stp = (dom.step || 0.1) / 2, x = num(h.x), y = num(h.y), k = ev.key;
          if (k === 'ArrowLeft') x -= stp; else if (k === 'ArrowRight') x += stp;
          else if (k === 'ArrowUp') y += stp; else if (k === 'ArrowDown') y -= stp; else return;
          if (ev.preventDefault) ev.preventDefault();
          st.refocus = key + '|' + id;
          set(id + '_x', parseFloat(clamp(x, dom.lo, dom.hi).toFixed(6)));
          set(id + '_y', parseFloat(clamp(y, dom.lo, dom.hi).toFixed(6)));
        });
        if (st.refocus === key + '|' + id && typeof g.focus === 'function') {
          var gg = g;
          if (typeof requestAnimationFrame === 'function') requestAnimationFrame(function () { try { gg.focus(); } catch (e) { /* ignore */ } });
          st.refocus = null;
        }
      });
      if (handles.length) txt(svg, pad, H - 4, 'drag the round handle' + (handles.length > 1 ? 's' : '') + ' (or focus + arrow keys)', 'k-muted');
      if (anyHi) svg.classList.add('kit-has-focus');
      if (isWatched(title)) svg.classList.add('kit-watched');
      if (o.note) note(el, o.note);
      return svg;
    });
  }

  /* -------------------------------------------------------------------- flow */
  /* Cause -> effect / block diagram. Boxes carry live values; arrows carry polarity
     (+ blue "more causes more", − purple "more causes less", shown by colour AND a
     +/− badge) and thickness by |weight|. x,y in 0..1; missing positions get a
     left-to-right layered layout from the edges. */
  function polarity(e) {
    var s = e.sign;
    if (typeof s === 'number') return s > 0 ? 1 : (s < 0 ? -1 : 0);
    s = str(s).trim().toLowerCase();
    if (s === '+' || s === 'pos' || s === 'positive' || s === '+1' || s === 'up') return 1;
    if (s === '-' || s === '−' || s === 'neg' || s === 'negative' || s === '-1' || s === 'down') return -1;
    var w = num(e.weight);
    return isNum(w) && w < 0 ? -1 : 0;
  }
  function layered(nodes, edges) {
    var n = nodes.length, idx = {}, out = [], indeg = [], depth = [];
    nodes.forEach(function (nd, i) { idx[str(nd.id)] = i; out.push([]); indeg.push(0); depth.push(-1); });
    edges.forEach(function (e) {
      var a = idx[str(e.from)], b = idx[str(e.to)];
      if (a === undefined || b === undefined || a === b) return;
      out[a].push(b); indeg[b] += 1;
    });
    var order = [];
    for (var i = 0; i < n; i++) if (indeg[i] === 0) order.push(i);
    for (var j = 0; j < n; j++) order.push(j);
    order.forEach(function (s) {
      if (depth[s] >= 0) return;
      depth[s] = 0;
      var q = [s];
      while (q.length) {
        var u = q.shift();
        out[u].forEach(function (v) { if (depth[v] < 0) { depth[v] = depth[u] + 1; q.push(v); } });
      }
    });
    var maxD = 0; depth.forEach(function (d) { maxD = Math.max(maxD, d); });
    var pos = [];
    if (maxD === 0) {
      var cols = Math.min(n, 4), rows = Math.ceil(n / cols);
      nodes.forEach(function (nd, k) {
        pos.push([cols > 1 ? (k % cols) / (cols - 1) : 0.5, rows > 1 ? Math.floor(k / cols) / (rows - 1) : 0.5]);
      });
      return { pos: pos, rows: rows };
    }
    var layers = {}, maxRows = 1;
    depth.forEach(function (d, k) { (layers[d] = layers[d] || []).push(k); });
    Object.keys(layers).forEach(function (d) { maxRows = Math.max(maxRows, layers[d].length); });
    depth.forEach(function (d, k) {
      var L = layers[d], r = L.indexOf(k);
      pos[k] = [d / maxD, L.length > 1 ? r / (L.length - 1) : 0.5];
    });
    return { pos: pos, rows: maxRows };
  }
  function rectExit(cx, cy, hw, hh, tx, ty) {
    var dx = tx - cx, dy = ty - cy;
    if (!dx && !dy) return [cx, cy];
    var t = Math.min(dx ? hw / Math.abs(dx) : Infinity, dy ? hh / Math.abs(dy) : Infinity);
    return [cx + dx * t, cy + dy * t];
  }
  function flow(el, o) {
    return safe('flow diagram', el, o, function (el, o) {
      var W = widthOf(el);
      var seen = {}, nodes = [];
      arr(o.nodes).forEach(function (nd) {
        if (!nd || typeof nd !== 'object' || nd.id === undefined || nd.id === null || str(nd.id) === '') return;
        if (seen[str(nd.id)]) return;
        seen[str(nd.id)] = 1; nodes.push(nd);
      });
      if (!nodes.length) return emptyState(el, o.title, 'No boxes to show yet.', W);
      var edges = arr(o.edges).filter(function (e) { return e && typeof e === 'object' && seen[str(e.from)] && seen[str(e.to)]; });
      var title = str(o.title);
      var d = isNum(num(o.fmt)) ? clamp(Math.round(num(o.fmt)), 0, 8) : 2;
      var valStr = function (nd) {
        var v = nd.value;
        if (v === undefined || v === null || v === '') return '';
        var u = nd.unit ? ' ' + str(nd.unit) : '';
        return (typeof v === 'number' ? fmt(v, d) : (isNum(num(v)) && /^\s*-?[\d.]+(e-?\d+)?\s*$/i.test(str(v)) ? fmt(num(v), d) : trunc(str(v), 18))) + u;
      };
      var mem = remember(slot('flow', title), { v: nodes.map(function (nd) { return isNum(num(nd.value)) ? num(nd.value) : null; }), w: edges.map(function (e) { return isNum(num(e.weight)) ? num(e.weight) : null; }) });
      var gv = mem.ghost ? arr(mem.ghost.v) : [], pw = mem.prev ? arr(mem.prev.w) : [];

      // positions (0..1); tolerate pixel-ish or percentage inputs by normalising
      var xs = nodes.map(function (nd) { return num(nd.x); }), ys = nodes.map(function (nd) { return num(nd.y); });
      var given = xs.every(isNum) && ys.every(isNum);
      var auto = given ? null : layered(nodes, edges);
      var norm01 = function (a) {
        var mx = Math.max.apply(null, a.filter(isNum).concat([0])), mn = Math.min.apply(null, a.filter(isNum).concat([0]));
        if (mx <= 1.0001 && mn >= -0.0001) return a;
        var span = (mx - mn) || 1;
        return a.map(function (v) { return isNum(v) ? (v - mn) / span : v; });
      };
      xs = norm01(xs); ys = norm01(ys);
      var P = nodes.map(function (nd, i) {
        if (given) return [clamp(xs[i], 0, 1), clamp(ys[i], 0, 1)];
        return auto.pos[i];
      });
      var distinct = function (k) { var m = {}; P.forEach(function (p) { m[Math.round(p[k] * 20)] = 1; }); return Math.max(1, Object.keys(m).length); };
      var nCols = distinct(0), nRows = distinct(1);
      if (W < 560 && nCols > nRows) {
        // narrow screen: turn a left-to-right diagram into a top-to-bottom one
        P = P.map(function (p) { return [p[1], p[0]]; });
        var tmpN = nCols; nCols = nRows; nRows = tmpN;
      }

      // box sizes
      var maxBoxW = clamp(Math.floor((W - 12) / nCols - 22), 64, 200);
      var anyVal = nodes.some(function (nd) { return valStr(nd) !== ''; });
      var labelOf = function (nd) { return str(nd.label !== undefined && nd.label !== null ? nd.label : nd.id); };
      var longWord = 0;
      nodes.forEach(function (nd) { labelOf(nd).split(/\s+/).forEach(function (w) { longWord = Math.max(longWord, w.length); }); });
      var lineChars = Math.max((maxBoxW - 16) / 7, Math.min(longWord, ((W - 12) / nCols - 10) / 7));
      maxBoxW = Math.max(maxBoxW, Math.min(lineChars * 7 + 16, (W - 12) / nCols - 6));
      var boxes = nodes.map(function (nd) {
        var label = labelOf(nd);
        var lines = wrapWords(label, lineChars, 3);
        var vs = valStr(nd);
        var bw = 0;
        lines.forEach(function (l) { bw = Math.max(bw, tw(l, 12.5)); });
        bw = clamp(Math.max(bw, tw(vs, 15) + 14) + 22, 60, maxBoxW);
        var bh = 12 + lines.length * 15 + (anyVal ? 22 : 0);
        return { nd: nd, label: label, lines: lines, vs: vs, bw: bw, bh: bh };
      });
      var mbw = 0, mbh = 0;
      boxes.forEach(function (b) { mbw = Math.max(mbw, b.bw); mbh = Math.max(mbh, b.bh); });
      var signed = edges.some(function (e) { return polarity(e) !== 0; });
      var weighted = edges.some(function (e) { return isNum(num(e.weight)); });
      var legendH = signed || weighted ? 24 : 0;
      var tEx = titleExtra(title, W, 2, false);
      var top = (title ? 28 : 6) + tEx;
      var rowsNeeded = Math.max(nRows, auto && W >= 560 ? auto.rows : 1);
      var H = Math.round(Math.max(clamp(W * 0.5, 220, 400), rowsNeeded * (mbh + 34) + 20)) + tEx + legendH;
      var padX = mbw / 2 + 8, padY = mbh / 2 + 10;
      var areaB = H - legendH - 4;
      boxes.forEach(function (b, i) {
        b.cx = padX + P[i][0] * Math.max(0, W - 2 * padX);
        b.cy = top + padY + P[i][1] * Math.max(0, areaB - top - 2 * padY);
      });
      var byId = {};
      boxes.forEach(function (b) { byId[str(b.nd.id)] = b; });
      var svg = makeSvg(el, W, H, (title || 'Flow diagram') + ': ' + edges.map(function (e) {
        var pz = polarity(e);
        return str(e.from) + (pz > 0 ? ' increases ' : pz < 0 ? ' decreases ' : ' → ') + str(e.to);
      }).join('; '));
      titleRow(svg, title, 2, W, false);

      var maxW = 0;
      edges.forEach(function (e) { var w = Math.abs(num(e.weight)); if (isNum(w)) maxW = Math.max(maxW, w); });
      var pairs = {};
      edges.forEach(function (e) { pairs[str(e.from) + '>' + str(e.to)] = true; });
      var eg = svgEl('g', null, svg), lg = svgEl('g', null, svg);
      var anyHi = false;
      edges.forEach(function (e, k) {
        var a = byId[str(e.from)], b = byId[str(e.to)];
        var pz = polarity(e);
        var hi = !!e.highlight || isWatched(e.label);
        if (hi) anyHi = true;
        var col = hi ? 'var(--accent)' : (pz > 0 ? 'var(--pos)' : pz < 0 ? 'var(--neg)' : 'var(--axis)');
        var w = Math.abs(num(e.weight));
        var widthFor = function (ww) { return isNum(ww) && maxW > 0 ? 1.4 + 4.6 * Math.abs(ww) / maxW : 2; };
        var sw = widthFor(w), sw0 = widthFor(pw[k]);
        var off = isNum(w) && w === 0;
        var path, hx, hy, fx, fy, mx, my, bx, by;
        if (a === b) {
          var cx = a.cx, cy = a.cy - a.bh / 2;
          path = 'M' + r2(cx - 14) + ',' + r2(cy) + 'C' + r2(cx - 30) + ',' + r2(cy - 40) + ' ' + r2(cx + 30) + ',' + r2(cy - 40) + ' ' + r2(cx + 14) + ',' + r2(cy - 2);
          fx = cx + 22; fy = cy - 22; hx = cx + 14; hy = cy - 2; mx = cx; my = cy - 34; bx = cx + 26; by = cy - 26;
        } else {
          var bend = pairs[str(e.to) + '>' + str(e.from)] ? 26 : 0;
          var dx = b.cx - a.cx, dy = b.cy - a.cy, L = Math.sqrt(dx * dx + dy * dy) || 1;
          var nx = -dy / L, ny = dx / L;
          var qx = (a.cx + b.cx) / 2 + nx * bend, qy = (a.cy + b.cy) / 2 + ny * bend;
          var s = rectExit(a.cx, a.cy, a.bw / 2 + 3, a.bh / 2 + 3, qx, qy);
          var t = rectExit(b.cx, b.cy, b.bw / 2 + 4, b.bh / 2 + 4, qx, qy);
          path = 'M' + r2(s[0]) + ',' + r2(s[1]) + 'Q' + r2(qx) + ',' + r2(qy) + ' ' + r2(t[0]) + ',' + r2(t[1]);
          fx = qx; fy = qy; hx = t[0]; hy = t[1];
          // midpoint of the quadratic curve, and a point 72% along it for the polarity badge
          var qp = function (u) { return [(1 - u) * (1 - u) * s[0] + 2 * (1 - u) * u * qx + u * u * t[0], (1 - u) * (1 - u) * s[1] + 2 * (1 - u) * u * qy + u * u * t[1]]; };
          var segL = Math.sqrt((t[0] - s[0]) * (t[0] - s[0]) + (t[1] - s[1]) * (t[1] - s[1]));
          var m = qp(0.5), bpt = qp(segL < 70 ? 0.45 : 0.72);
          mx = m[0] + nx * 13; my = m[1] + ny * 13; bx = bpt[0]; by = bpt[1];
          if (segL < 60) mx = NaN; // too short for a readable label: keep it in the tooltip only
        }
        var pe = svgEl('path', { d: path, 'class': 'k-flow-edge' + (hi ? ' k-edge-hi' : ''), stroke: col, 'stroke-dasharray': off ? '4 4' : null, opacity: off ? 0.55 : 1 }, eg);
        svgEl('title', { text: str(e.from) + (pz > 0 ? ' increases ' : pz < 0 ? ' decreases ' : ' → ') + str(e.to) + (isNum(num(e.weight)) ? ' (weight ' + fmt(num(e.weight), 3) + ')' : '') + (e.label ? ': ' + str(e.label) : '') }, pe);
        tween(function (tt) { pe.setAttribute('stroke-width', s2(lerp(sw0, sw, tt))); });
        var hs = 7 + Math.min(4, sw * 0.6);
        var ddx = hx - fx, ddy = hy - fy, LL = Math.sqrt(ddx * ddx + ddy * ddy) || 1, ux = ddx / LL, uy = ddy / LL;
        var abx = hx - ux * hs * 1.3, aby = hy - uy * hs * 1.3;
        svgEl('path', { d: 'M' + r2(hx) + ',' + r2(hy) + 'L' + r2(abx - uy * hs * 0.6) + ',' + r2(aby + ux * hs * 0.6) + 'L' + r2(abx + uy * hs * 0.6) + ',' + r2(aby - ux * hs * 0.6) + 'Z', fill: col, opacity: off ? 0.55 : 1 }, eg);
        if (pz !== 0) {
          svgEl('circle', { cx: bx, cy: by, r: 8.5, 'class': 'k-pol', stroke: col }, lg);
          txt(lg, bx, by + 4.5, pz > 0 ? '+' : '−', 'k-pol-sign', { 'text-anchor': 'middle', fill: col });
        }
        var lab = e.label !== undefined && e.label !== null && str(e.label) !== '' ? str(e.label) : '';
        if (lab && isNum(mx)) txt(lg, mx, my + 4, trunc(lab, 22), 'k-edge-label k-halo' + (hi ? ' k-accent' : ''), { 'text-anchor': 'middle' });
      });
      boxes.forEach(function (b, i) {
        var nd = b.nd;
        var hi = !!nd.highlight || isWatched(nd.label) || isWatched(nd.id);
        if (hi) anyHi = true;
        var g = svgEl('g', { 'class': 'k-flow-node' + (hi ? ' k-flow-hi' : '') }, svg);
        var x0 = b.cx - b.bw / 2, y0 = b.cy - b.bh / 2;
        svgEl('rect', { x: x0, y: y0, width: b.bw, height: b.bh, rx: 9, 'class': 'k-flow-box' }, g);
        b.lines.forEach(function (l, k) { txt(g, b.cx, y0 + 18 + k * 15, l, 'k-flow-label', { 'text-anchor': 'middle' }); });
        if (b.vs) {
          var vy = y0 + 18 + b.lines.length * 15 + 5;
          txt(g, b.cx, vy, b.vs, 'k-flow-val' + (hi ? ' k-accent' : ''), { 'text-anchor': 'middle' });
          var v = num(nd.value), g0 = gv[i];
          if (isNum(v) && isNum(g0) && Math.abs(v - g0) > 1e-12 * Math.max(1, Math.abs(v))) {
            txt(g, b.cx + tw(b.vs, 15) / 2 + 5, vy - 1, v > g0 ? '▲' : '▼', 'k-flow-delta');
          }
        }
        svgEl('title', { text: b.label + (b.vs ? ' = ' + b.vs : '') }, g);
      });
      if (legendH) {
        var lx = 4, ly = H - 8;
        var item = function (colr, sign, text) {
          svgEl('line', { x1: lx, x2: lx + 30, y1: ly - 4, y2: ly - 4, stroke: colr, 'class': 'k-flow-edge', 'stroke-width': 2.5 }, svg);
          svgEl('circle', { cx: lx + 15, cy: ly - 4, r: 7.5, 'class': 'k-pol', stroke: colr }, svg);
          txt(svg, lx + 15, ly, sign, 'k-pol-sign k-small', { 'text-anchor': 'middle', fill: colr });
          txt(svg, lx + 36, ly, text, 'k-legend');
          lx += 36 + tw(text) + 18;
        };
        if (signed) {
          item('var(--pos)', '+', 'more causes more');
          item('var(--neg)', '−', 'more causes less');
        }
        if (weighted && lx + tw('thicker = stronger') < W) txt(svg, lx, ly, 'thicker = stronger', 'k-muted');
      }
      if (anyHi) svg.classList.add('kit-has-focus');
      if (isWatched(title)) svg.classList.add('kit-watched');
      if (o.note) note(el, o.note);
      return svg;
    });
  }

  /* ----------------------------------------------------------------- compare */
  /* Side-by-side "without vs with" / "before vs after" panes (stacked on narrow
     screens). Each side's draw(sub) fills its pane with other kit calls. */
  function div(cls, text) {
    var d = document.createElement('div');
    d.setAttribute('class', cls);
    if (text !== undefined) d.textContent = str(text);
    return d;
  }
  function compare(el, o) {
    o = (o && typeof o === 'object') ? o : {};
    try {
      var title = str(o.title);
      var wrap = div('kit-compare');
      wrap.setAttribute('role', 'group');
      wrap.setAttribute('aria-label', title || 'Side-by-side comparison');
      if (title) wrap.appendChild(div('kit-cmp-title', title));
      var grid2 = div('kit-cmp-grid');
      wrap.appendChild(grid2);
      attach(el, wrap);
      // build both panes before drawing either, so each measures its final width
      var todo = [];
      ['left', 'right'].forEach(function (side, i) {
        var spec = (o[side] && typeof o[side] === 'object') ? o[side] : (typeof o[side] === 'function' ? { draw: o[side] } : {});
        var pane = div('kit-cmp-pane kit-cmp-' + side);
        var head = div('kit-cmp-head');
        var tag = document.createElement('span');
        tag.setAttribute('class', 'kit-cmp-tag');
        tag.setAttribute('aria-hidden', 'true');
        tag.textContent = i ? 'B' : 'A';
        var name = document.createElement('span');
        name.textContent = str(spec.title) || (i ? 'After' : 'Before');
        head.append(tag, name);
        var body = div('kit-cmp-body');
        pane.append(head, body);
        grid2.appendChild(pane);
        if (spec.highlight || isWatched(spec.title)) pane.classList.add('kit-cmp-hi');
        todo.push([side, spec, body]);
      });
      todo.forEach(function (t) {
        var spec = t[1], body = t[2];
        if (typeof spec.draw !== 'function') { emptyState(body, '', 'Nothing to draw on this side.'); return; }
        try { spec.draw(body); } catch (e) {
          warn('compare ' + t[0] + ' draw failed', e);
          emptyState(body, '', 'Could not draw this side: ' + errMsg(e));
        }
      });
      if (o.note) note(wrap, o.note);
      return wrap;
    } catch (e) {
      warn('compare failed', e);
      try { return emptyState(el, o.title, 'Could not build the comparison: ' + errMsg(e)); } catch (e2) { return null; }
    }
  }

  /* ---------------------------------------------------------- misc helpers */
  function callout(el, text, kind) {
    try {
      var k = kind === 'warn' || kind === 'key' ? kind : 'info';
      var d = document.createElement('div');
      d.setAttribute('class', 'kit-callout kit-callout-' + k);
      d.setAttribute('role', k === 'warn' ? 'note' : 'note');
      d.textContent = str(text);
      attach(el, d);
      return d;
    } catch (e) { warn('callout failed', e); return null; }
  }
  function svgRoot(el, w, h) {
    w = num(w); h = num(h);
    if (!isNum(w) || w <= 0) w = 400;
    if (!isNum(h) || h <= 0) h = 300;
    var s = svgEl('svg', { viewBox: '0 0 ' + w + ' ' + h, width: '100%', 'class': 'kit-svg kit-custom', role: 'img', preserveAspectRatio: 'xMidYMid meet' });
    try { s.style.maxWidth = Math.round(w * 1.6) + 'px'; } catch (e) { /* ignore */ }
    attach(el, s);
    return s;
  }
  function rng(seed) {
    var a;
    if (typeof seed === 'string') {
      a = 2166136261;
      for (var i = 0; i < seed.length; i++) { a ^= seed.charCodeAt(i); a = Math.imul(a, 16777619); }
    } else a = Math.floor(num(seed)) || 1;
    a = a >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function set(id, value) {
    if (typeof st.onSet === 'function') {
      try { return st.onSet(str(id), value); } catch (e) { warn('kit.set failed', e); }
    }
    return value;
  }

  return {
    plot: plot, bars: bars, matrix: matrix, pipeline: pipeline, timeline: timeline, grid: grid,
    graph: graph, vec2d: vec2d, flow: flow, compare: compare, callout: callout, svg: svgRoot, svgEl: svgEl, set: set, fmt: fmt,
    color: color, accent: 'var(--accent)', rng: rng,
    isWatched: isWatched,
    /* internal hooks used by app.js (not part of the generated-code API) */
    _begin: function (opts) { st.n = 0; st.width = opts && isNum(opts.width) ? opts.width : st.width; },
    _setWatch: function (key, label) { st.watch = key ? { key: str(key), label: str(label) } : null; },
    _onSet: function (fn) { st.onSet = fn; },
    _niceStep: niceStep
  };
})();
