/* app.js - builds the page from SPEC / BUILD and wires the interaction loop
 * (CONTRACT section 3). Runs after kit.js, compute() and render() are defined.
 * Defensive throughout: a missing field hides a section, a failing compute/render
 * shows an inline message, never a blank page. */
(function () {
  'use strict';

  var S = (typeof SPEC !== 'undefined' && SPEC && typeof SPEC === 'object') ? SPEC : {};
  var B = (typeof BUILD !== 'undefined' && BUILD && typeof BUILD === 'object') ? BUILD : {};
  var K = (typeof kit !== 'undefined' && kit) ? kit : null;
  var root = document.getElementById('app') || document.body;

  /* ------------------------------------------------------------- helpers */
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function num(x) { if (typeof x === 'number') return x; if (x === null || x === undefined || x === '') return NaN; var v = parseFloat(x); return isNaN(v) ? NaN : v; }
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return x === undefined || x === null ? '' : String(x); }
  function has(o, k) { return !!o && typeof o === 'object' && Object.prototype.hasOwnProperty.call(o, k); }
  function clone(v) { if (v === null || typeof v !== 'object') return v; try { return JSON.parse(JSON.stringify(v)); } catch (e) { return v; } }
  function clamp(x, a, b) { return x < a ? a : (x > b ? b : x); }
  function errMsg(e) { return e && e.message ? e.message : str(e) || 'unknown error'; }
  function fmt(v, d) { if (K && K.fmt) return K.fmt(v, d); return str(v); }
  function digitsOf(f, dflt) {
    if (isNum(f)) return clamp(Math.round(f), 0, 10);
    var m = /(\d+)/.exec(str(f));
    return m ? clamp(parseInt(m[1], 10), 0, 10) : dflt;
  }
  function decimalsOf(step) {
    if (!isNum(step) || step <= 0) return 2;
    var s = String(step);
    if (s.indexOf('e-') >= 0) return parseInt(s.split('e-')[1], 10) || 2;
    var i = s.indexOf('.');
    return i < 0 ? 0 : Math.min(6, s.length - i - 1);
  }
  function niceStep(span, n) {
    if (K && K._niceStep) return K._niceStep(span, n);
    var raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  }
  function safeId(s) { return str(s).replace(/[^A-Za-z0-9_-]/g, '_'); }
  function reduced() { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } }
  function h(tag, a, kids) {
    var e = document.createElement(tag);
    if (a) {
      Object.keys(a).forEach(function (k) {
        var v = a[k];
        if (v === undefined || v === null || v === false) return;
        if (k === 'text') e.textContent = str(v);
        else if (k === 'class') e.className = v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') e.addEventListener(k.slice(2), v);
        else e.setAttribute(k, v === true ? '' : str(v));
      });
    }
    if (kids !== undefined && kids !== null) {
      (Array.isArray(kids) ? kids : [kids]).forEach(function (c) {
        if (c === undefined || c === null || c === false) return;
        e.append(c instanceof Node ? c : document.createTextNode(str(c)));
      });
    }
    return e;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === undefined || c === null || c === false) continue;
      parent.append(c instanceof Node ? c : document.createTextNode(str(c)));
    }
    return parent;
  }
  function flash(el) {
    if (!el) return;
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }

  /* ------------------------------------------------------------ grounding */
  var origin = str(B.source_origin).toLowerCase() || 'none';
  var hasExcerpt = origin === 'inline' || origin === 'file' || origin === 'fetched';
  function badge(support) {
    var s = str(support).toLowerCase();
    if (s === 'excerpt' && hasExcerpt) return h('span', { 'class': 'badge badge-excerpt', title: 'Checked against the source excerpt the agent was given' }, '📄 From the excerpt');
    if (s === 'excerpt' || s === 'paper') {
      return h('span', { 'class': 'badge badge-paper', title: s === 'excerpt' ? 'Marked as from the excerpt, but no excerpt was available to this run' : 'From the cited paper (model knowledge), not verified against an excerpt' }, '🧠 From the paper, not verified against an excerpt');
    }
    if (s === 'illustration') return h('span', { 'class': 'badge badge-illustration', title: 'A simplification or example made for teaching' }, '🧪 Our illustration');
    return null;
  }

  /* ------------------------------------------------------------- controls */
  function normControl(c) {
    var o = {};
    Object.keys(c).forEach(function (k) { o[k] = c[k]; });
    o.id = str(c.id);
    o.label = str(c.label) || o.id;
    var t = str(c.type).toLowerCase();
    if (t === 'slider') t = 'range';
    if (t === 'checkbox' || t === 'boolean' || t === 'bool' || t === 'switch') t = 'toggle';
    if (['range', 'number', 'select', 'toggle', 'matrix', 'vector'].indexOf(t) < 0) {
      if (typeof c.default === 'boolean') t = 'toggle';
      else if (Array.isArray(c.options)) t = 'select';
      else if (Array.isArray(c.default)) t = Array.isArray(c.default[0]) ? 'matrix' : 'vector';
      else if (isNum(num(c.min)) && isNum(num(c.max))) t = 'range';
      else t = 'number';
    }
    o.type = t;
    if (t === 'range' || t === 'number') {
      var mn = num(c.min), mx = num(c.max), d = num(c.default);
      if (t === 'range') {
        if (!isNum(mn)) mn = isNum(d) ? Math.min(0, d) : 0;
        if (!isNum(mx)) mx = isNum(d) ? Math.max(mn + 1, d * 2) : mn + 1;
        if (mx < mn) { var tmp = mn; mn = mx; mx = tmp; }
        if (mx === mn) mx = mn + 1;
      }
      o.min = mn; o.max = mx;
      o.step = num(c.step);
      if (!(o.step > 0)) o.step = isNum(mn) && isNum(mx) ? niceStep(mx - mn, 100) : NaN;
      if (!isNum(d)) d = isNum(mn) ? mn : 0;
      o.default = clampNum(o, d);
    } else if (t === 'select') {
      var opts = arr(c.options).map(function (op) {
        if (op && typeof op === 'object' && has(op, 'value')) return { value: op.value, label: str(op.label !== undefined ? op.label : op.value) };
        return { value: op, label: str(op) };
      });
      if (!opts.length) opts = [{ value: c.default, label: str(c.default) }];
      o.opts = opts;
      var hit = opts.filter(function (op) { return op.value === c.default || str(op.value) === str(c.default); })[0];
      o.default = hit ? hit.value : opts[0].value;
    } else if (t === 'toggle') {
      o.default = c.default === 'false' ? false : !!c.default;
    } else if (t === 'matrix') {
      var D = Array.isArray(c.default) ? c.default : [];
      var R = Math.max(1, Math.round(num(c.rows)) || D.length || 2);
      var C = Math.max(1, Math.round(num(c.cols)) || (Array.isArray(D[0]) ? D[0].length : 0) || 2);
      o.rows = R; o.cols = C;
      o.default = [];
      for (var i = 0; i < R; i++) {
        var row = [];
        for (var j = 0; j < C; j++) { var v = Array.isArray(D[i]) ? num(D[i][j]) : NaN; row.push(isNum(v) ? v : 0); }
        o.default.push(row);
      }
    } else if (t === 'vector') {
      var V = Array.isArray(c.default) ? c.default : [];
      var L = Math.max(1, Math.round(num(c.length)) || V.length || 3);
      o.length = L;
      o.default = [];
      for (var k = 0; k < L; k++) { var x = num(V[k]); o.default.push(isNum(x) ? x : 0); }
    }
    return o;
  }
  function clampNum(c, v) {
    if (isNum(c.min)) v = Math.max(c.min, v);
    if (isNum(c.max)) v = Math.min(c.max, v);
    return parseFloat(v.toPrecision(12));
  }
  function coerce(c, value) {
    var t = c.type;
    if (t === 'range' || t === 'number') { var v = num(value); return isNum(v) ? clampNum(c, v) : undefined; }
    if (t === 'select') {
      var hit = c.opts.filter(function (op) { return op.value === value || str(op.value) === str(value); })[0];
      return hit ? hit.value : undefined;
    }
    if (t === 'toggle') return value === 'false' ? false : !!value;
    if (t === 'matrix') {
      var cur = clone(p[c.id]) || clone(c.default);
      for (var i = 0; i < c.rows; i++) for (var j = 0; j < c.cols; j++) {
        var x = Array.isArray(value) && Array.isArray(value[i]) ? num(value[i][j]) : NaN;
        if (isNum(x)) cur[i][j] = elemClamp(c, x);
      }
      return cur;
    }
    if (t === 'vector') {
      var cv = clone(p[c.id]) || clone(c.default);
      for (var k = 0; k < c.length; k++) { var y = Array.isArray(value) ? num(value[k]) : NaN; if (isNum(y)) cv[k] = elemClamp(c, y); }
      return cv;
    }
    return value;
  }
  function elemClamp(c, x) {
    var mn = num(c.min), mx = num(c.max);
    if (isNum(mn)) x = Math.max(mn, x);
    if (isNum(mx)) x = Math.min(mx, x);
    return x;
  }
  function fmtVal(c, v) {
    if (c.type === 'range' || c.type === 'number') {
      if (!isNum(v)) return str(v);
      var dec = isNum(c.step) ? decimalsOf(c.step) : 3;
      if (Math.abs(v) >= 1e5 || (Math.abs(v) < 1e-3 && v !== 0)) return fmt(v, 3);
      return v.toFixed(dec);
    }
    if (c.type === 'toggle') return v ? 'on' : 'off';
    if (c.type === 'select') { var hit = c.opts.filter(function (op) { return op.value === v; })[0]; return hit ? hit.label : str(v); }
    return fmt(v, 3);
  }

  var controls = arr(S.controls).filter(function (c) { return c && typeof c === 'object' && c.id !== undefined && c.id !== null && str(c.id) !== ''; }).map(normControl);
  var byId = {};
  controls.forEach(function (c) { byId[c.id] = c; });
  function defaults() { var o = {}; controls.forEach(function (c) { o[c.id] = clone(c.default); }); return o; }
  var p = defaults();
  var widgets = {};

  function buildControl(c) {
    var id = 'ctl-' + safeId(c.id);
    var wrap = h('div', { 'class': 'ctl ctl-' + c.type, 'data-id': c.id });
    var help = c.help ? h('p', { 'class': 'ctl-help', id: id + '-help', text: c.help }) : null;
    var helpId = help ? id + '-help' : null;
    var w = { c: c, el: wrap, set: function () {} };
    var unit = c.unit ? ' ' + str(c.unit) : '';
    if (c.type === 'range') {
      var out = h('output', { id: id + '-out', 'for': id });
      var inp = h('input', { type: 'range', id: id, min: c.min, max: c.max, step: isNum(c.step) ? c.step : 'any', 'aria-describedby': helpId });
      inp.addEventListener('input', function () {
        var v = num(inp.value);
        if (!isNum(v)) return;
        p[c.id] = clampNum(c, v);
        out.textContent = fmtVal(c, p[c.id]) + unit;
        inp.setAttribute('aria-valuetext', fmtVal(c, p[c.id]) + unit);
        changed();
      });
      add(wrap, h('div', { 'class': 'ctl-head' }, [h('label', { 'for': id, text: c.label }), out]), inp,
        h('div', { 'class': 'ctl-scale', 'aria-hidden': 'true' }, [h('span', { text: fmtVal(c, c.min) }), h('span', { text: fmtVal(c, c.max) })]), help);
      w.set = function (v) { inp.value = String(v); out.textContent = fmtVal(c, v) + unit; inp.setAttribute('aria-valuetext', fmtVal(c, v) + unit); };
    } else if (c.type === 'number') {
      var ni = h('input', { type: 'number', id: id, min: isNum(c.min) ? c.min : null, max: isNum(c.max) ? c.max : null, step: isNum(c.step) ? c.step : 'any', inputmode: 'decimal', 'aria-describedby': helpId });
      ni.addEventListener('input', function () {
        var v = num(ni.value);
        if (!isNum(v)) return;
        if ((isNum(c.min) && v < c.min) || (isNum(c.max) && v > c.max)) return;
        p[c.id] = v; changed();
      });
      ni.addEventListener('change', function () {
        var v = coerce(c, ni.value);
        if (v === undefined) v = p[c.id];
        ni.value = String(v);
        if (v !== p[c.id]) { p[c.id] = v; changed(); }
      });
      add(wrap, h('div', { 'class': 'ctl-head' }, [h('label', { 'for': id, text: c.label + unit })]), ni, help);
      w.set = function (v) { ni.value = String(v); };
    } else if (c.type === 'select') {
      var sel = h('select', { id: id, 'aria-describedby': helpId });
      c.opts.forEach(function (op, i) { sel.append(h('option', { value: String(i), text: op.label })); });
      sel.addEventListener('change', function () { var op = c.opts[parseInt(sel.value, 10)]; if (op) { p[c.id] = op.value; changed(); } });
      add(wrap, h('div', { 'class': 'ctl-head' }, [h('label', { 'for': id, text: c.label })]), sel, help);
      w.set = function (v) { c.opts.forEach(function (op, i) { if (op.value === v) sel.value = String(i); }); };
    } else if (c.type === 'toggle') {
      var cb = h('input', { type: 'checkbox', id: id, role: 'switch', 'aria-describedby': helpId });
      cb.addEventListener('change', function () { p[c.id] = !!cb.checked; changed(); });
      add(wrap, h('label', { 'class': 'switch', 'for': id }, [cb, h('span', { text: c.label })]), help);
      w.set = function (v) { cb.checked = !!v; cb.setAttribute('aria-checked', v ? 'true' : 'false'); };
    } else if (c.type === 'matrix' || c.type === 'vector') {
      var isM = c.type === 'matrix';
      var R = isM ? c.rows : 1, C = isM ? c.cols : c.length;
      var perRow = isM ? C : Math.min(C, 6);
      var g = h('div', { 'class': 'cells-edit', role: 'group', 'aria-label': c.label });
      var rowL = arr(c.rowLabels), colL = arr(c.colLabels || c.labels);
      g.style.gridTemplateColumns = (isM ? 'auto ' : '') + 'repeat(' + perRow + ', minmax(3.6em, 1fr))';
      if (isM) {
        g.append(h('span', { 'class': 'ce-h', 'aria-hidden': 'true' }));
        for (var j0 = 0; j0 < C; j0++) g.append(h('span', { 'class': 'ce-h', 'aria-hidden': 'true', text: colL[j0] !== undefined ? str(colL[j0]) : String(j0 + 1) }));
      }
      var inputs = [];
      for (var i = 0; i < R; i++) {
        if (isM) g.append(h('span', { 'class': 'ce-r', 'aria-hidden': 'true', text: rowL[i] !== undefined ? str(rowL[i]) : String(i + 1) }));
        inputs.push([]);
        for (var j = 0; j < C; j++) {
          (function (i, j) {
            var lab = isM ? c.label + ', row ' + (i + 1) + ', column ' + (j + 1) : c.label + ', ' + (colL[j] !== undefined ? str(colL[j]) : 'element ' + (j + 1));
            var e = h('input', { type: 'number', step: isNum(num(c.step)) ? num(c.step) : 'any', min: isNum(num(c.min)) ? num(c.min) : null, max: isNum(num(c.max)) ? num(c.max) : null, inputmode: 'decimal', 'aria-label': lab, title: lab });
            e.addEventListener('input', function () {
              var v = num(e.value);
              if (!isNum(v)) return;
              var cur = clone(p[c.id]);
              if (isM) cur[i][j] = elemClamp(c, v); else cur[j] = elemClamp(c, v);
              p[c.id] = cur; changed();
            });
            e.addEventListener('change', function () { var cur = p[c.id]; e.value = String(isM ? cur[i][j] : cur[j]); });
            inputs[i].push(e);
            g.append(e);
          })(i, j);
        }
      }
      add(wrap, h('div', { 'class': 'ctl-head' }, [h('span', { 'class': 'ctl-label', text: c.label + (isM ? ' (' + R + '×' + C + ')' : '') })]), g, help);
      w.set = function (v) {
        for (var i2 = 0; i2 < R; i2++) for (var j2 = 0; j2 < C; j2++) {
          var e2 = inputs[i2][j2], val = isM ? v[i2][j2] : v[j2];
          if (document.activeElement === e2 && num(e2.value) === val) continue;
          e2.value = String(val);
        }
      };
    }
    w.set(p[c.id]);
    widgets[c.id] = w;
    return wrap;
  }

  function setControl(id, value) {
    var c = byId[id];
    if (c) {
      var v = coerce(c, value);
      if (v === undefined) return p[id];
      p[id] = v;
      widgets[id].set(v);
    } else {
      p[id] = clone(value);
    }
    changed();
    return p[id];
  }
  if (K && K._onSet) K._onSet(setControl);

  /* ------------------------------------------------------------------ run */
  var pending = false;
  function changed() {
    if (pending) return;
    pending = true;
    Promise.resolve().then(function () { pending = false; run(); });
  }

  var els = {};
  var lastR = null, lastWidth = 0;
  var watchKey = null;

  function callCompute(params) {
    if (typeof compute !== 'function') throw new Error('compute(p) is not defined (the generated calculation code did not load).');
    var r = compute(params);
    if (r === null || typeof r !== 'object') throw new Error('compute(p) returned ' + (r === null ? 'null' : typeof r) + ' instead of an object.');
    return r;
  }

  function run() {
    var r = null, err = null;
    try { r = callCompute(clone(p)); } catch (e) { err = e; }
    els.err.classList.remove('on');
    els.err.replaceChildren();
    els.viz.replaceChildren();
    lastWidth = els.viz.clientWidth;
    if (K && K._begin) K._begin({ width: lastWidth });
    if (r) {
      if (typeof render !== 'function') showError('render', new Error('render(r, p, kit, el) is not defined (the generated drawing code did not load).'));
      else if (!K) showError('render', new Error('The drawing kit did not load.'));
      else {
        try { render(r, clone(p), K, els.viz); } catch (e) { showError('render', e); }
        if (!els.viz.childNodes.length && !els.err.classList.contains('on')) {
          K.callout(els.viz, 'Nothing to draw for these settings. The values on the right are still live.', 'info');
        }
      }
    } else {
      showError('compute', err);
    }
    lastR = r;
    updateValues(r);
    updateEquations(r);
    updateInvariants(r);
    updateWatch(r);
  }
  function showError(stage, e) {
    els.err.classList.add('on');
    els.err.replaceChildren(
      h('strong', { text: stage === 'compute' ? 'The calculation failed for these settings. ' : 'The visual could not be drawn. ' }),
      h('code', { text: errMsg(e) }),
      h('br'),
      stage === 'compute' ? 'Try another value or press Reset.' : 'The numbers on the right are still live.'
    );
    try { if (window.console) console.error('[' + stage + ']', e); } catch (x) { /* ignore */ }
  }

  /* ------------------------------------------------------- intermediates */
  var inter = arr(S.intermediates).filter(function (it) { return it && it.key !== undefined; }).map(function (it) {
    return { key: str(it.key), label: str(it.label) || str(it.key), d: digitsOf(it.fmt, 3), unit: str(it.unit) };
  });
  var interAuto = !inter.length;
  var valRows = {};
  function labelFor(key) {
    var it = inter.filter(function (x) { return x.key === key; })[0];
    if (it) return it.label;
    if (byId[key]) return byId[key].label;
    return str(key);
  }
  function buildValueRows() {
    els.vals.replaceChildren();
    valRows = {};
    inter.forEach(function (it) {
      var vSpan = h('span', { 'class': 'val' });
      var dSpan = h('span', { 'class': 'delta', 'aria-hidden': 'true' });
      var td = h('td', { 'class': 'v' }, [dSpan, vSpan, it.unit ? h('span', { 'class': 'unit', text: it.unit }) : null]);
      var tr = h('tr', { 'data-key': it.key }, [h('td', { title: it.key, text: it.label }), td]);
      els.vals.append(tr);
      valRows[it.key] = { tr: tr, td: td, v: vSpan, d: dSpan, prev: undefined, prevNum: undefined };
    });
    els.valsCard.style.display = inter.length ? '' : 'none';
  }
  function updateValues(r) {
    if (interAuto && r && !inter.length) {
      inter = Object.keys(r).filter(function (k) { return isNum(r[k]); }).slice(0, 8).map(function (k) { return { key: k, label: k, d: 3, unit: '' }; });
      buildValueRows();
    }
    inter.forEach(function (it) {
      var row = valRows[it.key]; if (!row) return;
      var v = r ? r[it.key] : undefined;
      var s = r ? fmt(v, it.d) : '—';
      row.td.classList.toggle('long', s.length > 18);
      if (s !== row.prev) {
        row.v.textContent = s;
        if (row.prev !== undefined) {
          flash(row.td);
          if (isNum(v) && isNum(row.prevNum)) row.d.textContent = v > row.prevNum ? '▲' : (v < row.prevNum ? '▼' : '');
          else row.d.textContent = '';
        }
        row.prev = s;
        row.prevNum = isNum(v) ? v : undefined;
      }
    });
  }

  /* ------------------------------------------------------- live equations */
  function getPath(obj, path) {
    var parts = path.replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
      if (cur === null || cur === undefined) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }
  /* Splits a live template into text / value / missing parts. An unresolved {key}
     renders as a quiet "—" (never "?"), so a template slip does not look like an error. */
  var TPL_RE = /\{\s*([A-Za-z_$][\w$]*(?:\.[\w$]+|\[\d+\])*)\s*(?::\s*(\d+))?\s*\}/g;
  function fillParts(tpl, r) {
    var parts = [], last = 0, src = str(tpl), m;
    TPL_RE.lastIndex = 0;
    while ((m = TPL_RE.exec(src)) !== null) {
      if (m.index > last) parts.push({ k: 'txt', t: src.slice(last, m.index) });
      last = TPL_RE.lastIndex;
      var key = m[1], d = m[2];
      var v = r ? getPath(r, key) : undefined;
      if (v === undefined) v = getPath(p, key);
      var base = key.split(/[.[]/)[0];
      if (v === undefined || (typeof v === 'number' && isNaN(v)) || typeof v === 'function') { parts.push({ k: 'miss', t: '—', key: base }); continue; }
      var it = inter.filter(function (x) { return x.key === base; })[0];
      var digits = d !== undefined ? parseInt(d, 10) : (it ? it.d : (byId[base] && isNum(byId[base].step) ? decimalsOf(byId[base].step) : 3));
      var t = (byId[base] && v === p[base] && typeof v !== 'number') ? fmtVal(byId[base], v) : fmt(v, digits);
      parts.push({ k: 'val', t: t, key: base });
    }
    if (last < src.length) parts.push({ k: 'txt', t: src.slice(last) });
    return parts;
  }
  var liveEqs = [];
  function updateEquations(r) {
    liveEqs.forEach(function (le) {
      var parts = fillParts(le.tpl, r);
      var s = parts.map(function (q) { return q.t; }).join('') + '|' + watchKey;
      if (s === le.prev) return;
      le.el.replaceChildren.apply(le.el, parts.map(function (q) {
        if (q.k === 'txt') return document.createTextNode(q.t);
        if (q.k === 'miss') return h('span', { 'class': 'lv-miss', title: 'not available for these settings', 'aria-label': 'not available', text: '—' });
        return h('span', { 'class': 'lv' + (watchKey && q.key === watchKey ? ' lv-watch' : ''), text: q.t });
      }));
      if (le.prev !== undefined) flash(le.el);
      le.prev = s;
    });
  }

  /* ----------------------------------------------------------- invariants */
  function compileExpr(expr) {
    try { return { fn: new Function('r', 'p', '"use strict"; return (' + str(expr) + ');') }; }
    catch (e) { return { err: 'could not parse: ' + errMsg(e) }; }
  }
  var invs = arr(S.invariants).filter(function (x) { return x && x.expr; }).map(function (x) {
    var c = compileExpr(x.expr);
    return { label: str(x.label) || str(x.expr), expr: str(x.expr), fn: c.fn, perr: c.err, prev: undefined };
  });
  function updateInvariants(r) {
    var ok = 0;
    invs.forEach(function (iv) {
      var good = false, detail = '';
      if (iv.perr) detail = iv.perr;
      else if (!r) detail = 'not evaluated: the calculation failed';
      else {
        try { good = !!iv.fn(r, clone(p)); if (!good) detail = 'does not hold for the current settings'; }
        catch (e) { detail = 'error: ' + errMsg(e); }
      }
      if (good) ok += 1;
      iv.mark.className = 'mark ' + (good ? 'ok' : 'bad');
      iv.mark.textContent = good ? '✓' : '✗';
      iv.mark.setAttribute('aria-label', good ? 'holds' : 'fails');
      iv.detail.textContent = detail;
      if (iv.prev !== undefined && iv.prev !== good) flash(iv.li);
      iv.prev = good;
    });
    if (els.invSummary) {
      els.invSummary.textContent = invs.length ? (ok === invs.length ? 'All ' + invs.length + ' invariant' + (invs.length > 1 ? 's' : '') + ' hold for the current settings.' : ok + ' of ' + invs.length + ' invariants hold for the current settings.') : 'No invariants were declared.';
    }
  }

  /* ---------------------------------------------------------------- watch */
  function setWatch(key) {
    watchKey = key ? str(key) : null;
    if (K && K._setWatch) K._setWatch(watchKey, watchKey ? labelFor(watchKey) : '');
    Object.keys(valRows).forEach(function (k) { valRows[k].tr.classList.toggle('watched', k === watchKey); });
  }
  function updateWatch(r) {
    if (!watchKey) { els.chip.classList.remove('on'); return; }
    els.chip.classList.add('on');
    var v = r ? getPath(r, watchKey) : undefined;
    var it = inter.filter(function (x) { return x.key === watchKey; })[0];
    els.chipLabel.textContent = 'Watching ' + labelFor(watchKey) + ' = ';
    els.chipVal.textContent = v === undefined ? (getPath(p, watchKey) !== undefined ? fmt(getPath(p, watchKey), 3) : '—') : fmt(v, it ? it.d : 3) + (it && it.unit ? ' ' + it.unit : '');
  }

  /* ------------------------------------------------------------ page build */
  var sections = [];
  function section(id, title, body) {
    var n = sections.length + 1;
    var s = h('section', { 'class': 'section', id: id, 'aria-labelledby': id + '-h' }, [
      h('h2', { id: id + '-h' }, [h('span', { 'class': 'num', 'aria-hidden': 'true', text: String(n) }), title])
    ].concat(body));
    sections.push({ id: id, title: title, el: s });
    return s;
  }

  function buildHeader() {
    var paper = (S.paper && typeof S.paper === 'object') ? S.paper : {};
    var themeBtn = h('button', { type: 'button', 'class': 'theme-btn', 'aria-label': 'Switch colour theme' });
    var modes = ['auto', 'light', 'dark'], mode = 'auto';
    try { mode = localStorage.getItem('p2p-theme') || 'auto'; } catch (e) { mode = 'auto'; }
    if (modes.indexOf(mode) < 0) mode = 'auto';
    function apply() {
      if (mode === 'auto') document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', mode);
      themeBtn.textContent = 'Theme: ' + mode;
    }
    themeBtn.addEventListener('click', function () {
      mode = modes[(modes.indexOf(mode) + 1) % modes.length];
      try { localStorage.setItem('p2p-theme', mode); } catch (e) { /* ignore */ }
      apply();
    });
    apply();
    var cite = [];
    if (paper.title) cite.push(h('em', { text: paper.title }));
    var who = [str(paper.authors), paper.year ? '(' + str(paper.year) + ')' : ''].filter(Boolean).join(' ');
    if (who) cite.push((cite.length ? ' — ' : '') + who);
    if (paper.section) cite.push(' · ' + str(paper.section));
    var toc = h('ul', { 'class': 'toc' });
    var head = h('header', { 'class': 'site-head wrap' }, [
      h('div', { 'class': 'eyebrow' }, [h('span', { text: 'Paper to Playground · interactive explainer' }), themeBtn]),
      h('h1', { text: str(S.title) || 'Interactive explainer' }),
      cite.length ? h('p', { 'class': 'cite-line' }, ['Based on '].concat(cite)) : null,
      h('nav', { 'aria-label': 'Sections' }, toc)
    ]);
    return { el: head, toc: toc };
  }

  function buildIdea() {
    var body = h('div', { 'class': 'card' }, [
      S.idea ? h('p', { 'class': 'lead', text: S.idea }) : h('p', { 'class': 'lead', text: 'No summary was provided.' }),
      S.why ? h('div', { 'class': 'why' }, [h('strong', { text: 'Why it matters' }), h('span', { text: S.why })]) : null
    ]);
    return section('idea', 'The idea', [body]);
  }

  function buildNotation() {
    var syms = arr(S.symbols).filter(function (s) { return s && (s.sym || s.meaning); });
    var eqs = arr(S.equations).filter(function (e) { return e && (e.expr || e.label); });
    if (!syms.length && !eqs.length) return null;
    var parts = [];
    if (eqs.length) {
      parts.push(h('div', { 'class': 'card' }, [h('h3', { text: 'Key equation' + (eqs.length > 1 ? 's' : '') })].concat(eqs.map(function (e) {
        return h('div', { 'class': 'eq' }, [
          h('div', { 'class': 'eq-label' }, [h('span', { text: str(e.label) }), badge(e.support)]),
          e.expr ? h('div', { 'class': 'eq-expr', role: 'math', 'aria-label': str(e.expr), text: str(e.expr) }) : null
        ]);
      }))));
    }
    if (syms.length) {
      var tb = h('tbody');
      syms.forEach(function (s) {
        tb.append(h('tr', null, [h('td', { 'class': 'sym', text: str(s.sym) }), h('td', { text: str(s.meaning) + (s.unit ? ' (' + str(s.unit) + ')' : '') })]));
      });
      parts.push(h('div', { 'class': 'card' }, [h('h3', { text: 'Symbols' }),
        h('table', { 'class': 'sym-table' }, [h('thead', null, h('tr', null, [h('th', { scope: 'col', text: 'Symbol' }), h('th', { scope: 'col', text: 'Meaning' })])), tb])]));
    }
    return section('notation', 'Notation', [h('div', { 'class': parts.length > 1 ? 'two' : '' }, parts)]);
  }

  function buildPlayground() {
    var vis = (S.visual && typeof S.visual === 'object') ? S.visual : {};
    els.viz = h('div', { 'class': 'viz', id: 'viz', 'aria-live': 'off' });
    els.err = h('div', { 'class': 'viz-error', role: 'alert' });
    els.chipLabel = h('span');
    els.chipVal = h('span', { 'class': 'wv' });
    els.chip = h('div', { 'class': 'watch-chip', role: 'status' }, [
      h('span', { 'aria-hidden': 'true', text: '◉' }), h('span', null, [els.chipLabel, els.chipVal]),
      h('button', { type: 'button', 'aria-label': 'Stop highlighting', title: 'Stop highlighting', text: '×', onclick: function () { setWatch(null); run(); } })
    ]);
    var establish = h('div', { 'class': 'establish' }, [
      vis.caption ? h('p', { 'class': 'caption', text: vis.caption }) : null,
      vis.how_to_read ? h('p', { 'class': 'howto' }, [h('b', { text: 'How to read it: ' }), vis.how_to_read]) : null,
      h('p', { 'class': 'howto meta', text: 'Dashed grey shapes and ▲▼ marks show the state before your last change, so you can see what moved.' }),
      els.chip
    ]);
    els.vizCard = h('div', { 'class': 'card viz-card', id: 'visual', tabindex: '-1' }, [establish, els.err, els.viz]);

    var ctlBox = h('div', null, controls.map(buildControl));
    if (!controls.length) ctlBox.append(h('p', { 'class': 'ctl-help', text: 'This page has no adjustable inputs.' }));
    var resetBtn = h('button', { type: 'button', 'class': 'btn btn-small', text: '↺ Reset', 'aria-label': 'Reset all controls to their defaults', onclick: reset });
    var ctlCard = h('div', { 'class': 'card', id: 'controls' }, [h('div', { 'class': 'card-head' }, [h('h3', { text: 'Controls' }), resetBtn]), ctlBox]);

    els.vals = h('tbody');
    els.valsCard = h('div', { 'class': 'card', id: 'values' }, [
      h('div', { 'class': 'card-head' }, [h('h3', { text: 'Values (live)' })]),
      h('table', { 'class': 'vals', 'aria-label': 'Intermediate values' }, els.vals)
    ]);
    buildValueRows();

    var eqLive = arr(S.equations).filter(function (e) { return e && e.live; });
    var eqCard = null;
    if (eqLive.length) {
      eqCard = h('div', { 'class': 'card', id: 'live-equations' }, [h('div', { 'class': 'card-head' }, [h('h3', { text: 'Equations with current numbers' })])]);
      eqLive.forEach(function (e) {
        var ex = h('div', { 'class': 'expr' });
        eqCard.append(h('div', { 'class': 'live-eq' }, [e.label ? h('div', { 'class': 'lab', text: str(e.label) }) : null, ex]));
        liveEqs.push({ tpl: str(e.live), el: ex, prev: undefined });
      });
    }
    var side = h('div', { 'class': 'side' }, [ctlCard, els.valsCard, eqCard]);
    return section('playground', 'Playground', [h('div', { 'class': 'play' }, [els.vizCard, side])]);
  }

  var exCards = [];
  function describePreset(preset) {
    return Object.keys(preset || {}).map(function (k) {
      var c = byId[k];
      return (c ? c.label : k) + ' = ' + (c ? fmtVal(c, coerce(c, preset[k]) !== undefined ? coerce(c, preset[k]) : preset[k]) : fmt(preset[k], 3));
    }).join(', ');
  }
  function buildExplore() {
    var exs = arr(S.explorations).filter(function (e) { return e && typeof e === 'object'; });
    if (!exs.length) return null;
    var grid = h('div', { 'class': 'explore-grid' });
    exs.forEach(function (ex, i) {
      var preset = (ex.preset && typeof ex.preset === 'object') ? ex.preset : {};
      var revealId = 'reveal-' + i;
      var status = h('p', { 'class': 'status', role: 'status' });
      var reveal = h('div', { 'class': 'reveal', id: revealId, hidden: true }, [
        ex.observe ? h('p', null, [h('b', { text: 'What you should see' }), str(ex.observe)]) : null,
        ex.why ? h('p', null, [h('b', { text: 'Why' }), str(ex.why)]) : null
      ]);
      var whyBtn = h('button', { type: 'button', 'class': 'btn', 'aria-expanded': 'false', 'aria-controls': revealId, text: 'Show why' });
      whyBtn.addEventListener('click', function () {
        var open = reveal.hasAttribute('hidden');
        if (open) reveal.removeAttribute('hidden'); else reveal.setAttribute('hidden', '');
        whyBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        whyBtn.textContent = open ? 'Hide why' : 'Show why';
      });
      var card = h('article', { 'class': 'card ex', id: 'explore-' + (i + 1), 'aria-labelledby': 'ex-h-' + i });
      var tryBtn = h('button', { type: 'button', 'class': 'btn btn-primary', text: '▶ Try it' });
      tryBtn.addEventListener('click', function () { tryIt(ex, preset, card, status, tryBtn); });
      var desc = describePreset(preset);
      add(card, 
        h('div', { 'class': 'ex-num', text: 'Exploration ' + (i + 1) }),
        h('h3', { id: 'ex-h-' + i, text: str(ex.title) || 'What happens if…' }),
        h('p', { 'class': 'predict' }, [h('b', { text: 'Predict first' }), str(ex.predict)]),
        h('label', { 'class': 'guess' }, ['Your prediction (optional, stays on this page)', h('input', { type: 'text', autocomplete: 'off', id: 'guess-' + (i + 1), name: 'guess-' + (i + 1) })]),
        desc || ex.watch ? h('p', { 'class': 'preset' }, [
          desc ? 'Try it sets ' : null, desc ? h('code', { text: desc }) : null,
          desc ? ' (other controls return to their defaults).' : null,
          ex.watch ? (desc ? ' Then watch ' : 'Watch ') : null, ex.watch ? h('code', { text: labelFor(str(ex.watch)) }) : null, ex.watch ? '.' : null
        ]) : null,
        h('div', { 'class': 'actions' }, [tryBtn, whyBtn]),
        status, reveal
      );
      exCards.push(card);
      grid.append(card);
    });
    return section('explore', 'Explore', [grid]);
  }
  function tryIt(ex, preset, card, status, btn) {
    var before = clone(p);
    controls.forEach(function (c) { setControl(c.id, has(preset, c.id) ? clone(preset[c.id]) : clone(c.default)); });
    controls.forEach(function (c) {
      if (JSON.stringify(before[c.id]) !== JSON.stringify(p[c.id]) && widgets[c.id]) flash(widgets[c.id].el);
    });
    if (btn) {
      btn.textContent = '✓ Applied';
      btn.classList.add('done');
      clearTimeout(btn._t);
      btn._t = setTimeout(function () { btn.textContent = '▶ Try it again'; btn.classList.remove('done'); }, 1800);
    }
    Object.keys(preset).forEach(function (k) { if (!byId[k]) setControl(k, clone(preset[k])); });
    setWatch(ex.watch ? str(ex.watch) : null);
    exCards.forEach(function (cd) { cd.classList.toggle('active', cd === card); });
    status.textContent = ex.watch ? 'Applied. The orange highlight marks ' + labelFor(str(ex.watch)) + '. Compare it with your prediction, then press Show why.' : 'Applied. Compare the visual with your prediction, then press Show why.';
    try { els.vizCard.scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'start' }); } catch (e) { els.vizCard.scrollIntoView(); }
    els.vizCard.classList.remove('pulse');
    void els.vizCard.offsetWidth;
    els.vizCard.classList.add('pulse');
  }
  function reset() {
    controls.forEach(function (c) { setControl(c.id, clone(c.default)); });
    setWatch(null);
    exCards.forEach(function (cd) { cd.classList.remove('active'); });
  }

  function buildLimitation() {
    var L = S.limitation;
    if (!L) return null;
    if (typeof L === 'string') L = { kind: 'limitation', text: L };
    if (!L.text) return null;
    var kinds = { limitation: 'Limitation', assumption: 'Assumption', misconception: 'Common misconception' };
    var k = str(L.kind).toLowerCase();
    return section('limitation', 'Limits of this picture', [h('div', { 'class': 'card limit', role: 'note' }, [
      h('div', { 'class': 'kind', text: '⚠ ' + (kinds[k] || 'Limitation') }), h('p', { text: str(L.text) })
    ])]);
  }

  function buildSource() {
    var paper = (S.paper && typeof S.paper === 'object') ? S.paper : {};
    var url = str(paper.url).trim();
    var safeUrl = /^https?:\/\//i.test(url) ? url : '';
    var cite = h('div', { 'class': 'cite' }, [
      paper.title ? h('strong', { text: str(paper.title) }) : h('strong', { text: 'Source paper not specified' }),
      paper.authors || paper.year ? h('div', { text: [str(paper.authors), paper.year ? '(' + str(paper.year) + ')' : ''].filter(Boolean).join(' ') }) : null,
      paper.section ? h('div', { text: 'Section: ' + str(paper.section) }) : null,
      safeUrl ? h('div', null, [h('a', { href: safeUrl, rel: 'noopener noreferrer', target: '_blank', text: safeUrl })]) : (url ? h('div', { text: url }) : null)
    ]);
    var notes = {
      inline: 'The agent was given an excerpt of the paper as text. Statements marked 📄 were written against that excerpt.',
      file: 'The agent read an excerpt of the paper from a local file. Statements marked 📄 were written against that excerpt.',
      fetched: 'The agent fetched text from the paper’s URL. Statements marked 📄 were written against that text.',
      none: 'No paper text was available to the agent, so nothing here is marked 📄. Content comes from the model’s knowledge of the cited paper and should be checked against the original.'
    };
    var claims = arr(S.claims).filter(function (c) { return c && c.text; });
    var list = claims.length ? h('ul', { 'class': 'claims' }, claims.map(function (c) {
      return h('li', null, [h('span', { text: str(c.text) }), h('span', null, badge(c.support))]);
    })) : h('p', { 'class': 'ctl-help', text: 'No individual claims were listed.' });
    var legend = h('div', { 'class': 'legend-badges', 'aria-label': 'Badge legend' }, [badge('excerpt') || badge('paper'), hasExcerpt ? badge('paper') : null, badge('illustration')]);
    return section('source', 'Source & grounding', [h('div', { 'class': 'card' }, [
      cite, h('p', { 'class': 'origin-note', text: notes[origin] || notes.none }),
      h('h3', { text: 'Claims on this page' }), list, legend
    ])]);
  }

  function resultClass(res) {
    var s = str(res).toLowerCase();
    if (s === 'pass' || s === 'ok' || s === 'passed' || s === 'true') return 'ok';
    if (s === 'fail' || s === 'error' || s === 'failed' || s === 'false') return 'bad';
    return 'skip';
  }
  function buildChecks() {
    var invList = h('ul', { 'class': 'checks' });
    invs.forEach(function (iv) {
      iv.mark = h('span', { 'class': 'mark skip', text: '–' });
      iv.detail = h('span', { 'class': 'detail' });
      iv.li = h('li', null, [iv.mark, h('span', null, [h('span', { text: iv.label }), h('code', { 'class': 'detail', text: iv.expr }), iv.detail])]);
      invList.append(iv.li);
    });
    els.invSummary = h('p', { 'class': 'summary-line' });
    var testsBox = null;
    var tests = arr(S.tests).filter(function (t) { return t && (t.expr || t.assert); });
    if (tests.length) {
      var tl = h('ul', { 'class': 'checks' });
      var passed = 0;
      tests.forEach(function (t) {
        var params = defaults();
        var tp = (t.params && typeof t.params === 'object') ? t.params : {};
        Object.keys(tp).forEach(function (k) { params[k] = byId[k] ? (coerce(byId[k], tp[k]) !== undefined ? coerce(byId[k], tp[k]) : tp[k]) : tp[k]; });
        var ok = false, detail = '';
        var c = compileExpr(t.expr || t.assert);
        if (c.err) detail = c.err;
        else {
          try { var r = callCompute(clone(params)); ok = !!c.fn(r, params); if (!ok) detail = 'assertion is false'; }
          catch (e) { detail = 'error: ' + errMsg(e); }
        }
        if (ok) passed += 1;
        tl.append(h('li', null, [h('span', { 'class': 'mark ' + (ok ? 'ok' : 'bad'), text: ok ? '✓' : '✗' }),
          h('span', null, [h('span', { text: str(t.name) || 'test' }), h('code', { 'class': 'detail', text: describePreset(tp) ? 'with ' + describePreset(tp) : 'with default settings' }), detail ? h('span', { 'class': 'detail', text: detail }) : null])]));
      });
      testsBox = h('div', null, [h('h3', { text: 'Worked-example tests (run in your browser at load)' }),
        h('p', { 'class': 'summary-line', text: passed + ' of ' + tests.length + ' pass.' }),
        h('p', { 'class': 'ctl-help', text: 'These are consistency checks written by the generator, not independent proof.' }), tl]);
    }
    var bchecks = arr(B.checks).filter(function (c) { return c && typeof c === 'object'; });
    var nOk = bchecks.filter(function (c) { return resultClass(c.result) === 'ok'; }).length;
    var nBad = bchecks.filter(function (c) { return resultClass(c.result) === 'bad'; }).length;
    var originText = { inline: 'excerpt given inline', file: 'excerpt read from a local file', fetched: 'text fetched from the paper URL', none: 'no paper text available' };
    var built = h('details', { 'class': 'built' }, [
      h('summary', { text: 'How this page was built and checked' + (bchecks.length ? ' (' + nOk + ' of ' + bchecks.length + ' automatic checks passed' + (nBad ? ', ' + nBad + ' failed' : '') + ')' : '') }),
      h('dl', null, [
        h('dt', { text: 'Generator model' }), h('dd', { text: str(B.model) || 'not recorded' }),
        h('dt', { text: 'Source text' }), h('dd', { text: originText[origin] || origin }),
        h('dt', { text: 'Generated at' }), h('dd', { text: str(B.generated_at) || 'not recorded' })
      ]),
      h('p', { 'class': 'ctl-help', text: 'An AI agent wrote the page content, the calculation and the drawing code; this fixed shell laid them out. Before the page was written, the code was executed automatically with the checks below.' }),
      bchecks.length ? h('ul', { 'class': 'checks' }, bchecks.map(function (c) {
        var cls = resultClass(c.result);
        return h('li', null, [h('span', { 'class': 'mark ' + cls, text: cls === 'ok' ? '✓' : (cls === 'bad' ? '✗' : '–') }),
          h('span', null, [h('strong', { text: str(c.id) }), ' ', h('span', { text: str(c.result) }), c.detail ? h('span', { 'class': 'detail', text: str(c.detail) }) : null])]);
      })) : h('p', { 'class': 'ctl-help', text: 'No build-check record was attached to this page.' })
    ]);
    return section('checks', 'Checks', [h('div', { 'class': 'card' }, [
      h('h3', { text: 'Live invariants (re-checked on every change)' }), els.invSummary, invList,
      testsBox, built
    ])]);
  }

  /* ----------------------------------------------------------------- init */
  function init() {
    document.title = (str(S.title) || 'Interactive explainer') + ' · Paper to Playground';
    var head = buildHeader();
    var main = h('main', { 'class': 'wrap', id: 'main' });
    [buildIdea(), buildNotation(), buildPlayground(), buildExplore(), buildLimitation(), buildSource(), buildChecks()].forEach(function (s) { if (s) main.append(s); });
    sections.forEach(function (s) { head.toc.append(h('li', null, h('a', { href: '#' + s.id, text: s.title }))); });
    var foot = h('footer', { 'class': 'site-foot wrap', text: 'Generated by Paper to Playground · a single offline page: everything runs in your browser.' });
    root.replaceChildren(head.el, main, foot);
    run();
    if (location.hash && location.hash.length > 1) {
      var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      if (target) target.scrollIntoView();
    }
    var t = null;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(function () { if (Math.abs(els.viz.clientWidth - lastWidth) > 24) run(); }, 160);
    });
    window.addEventListener('error', function (ev) {
      if (els.err && !els.err.classList.contains('on')) showError('render', ev.error || ev.message);
    });
  }
  try { init(); } catch (e) {
    root.replaceChildren(h('div', { 'class': 'wrap', style: 'padding:24px' }, [h('h1', { text: str(S.title) || 'Interactive explainer' }),
      h('p', { text: 'This page could not be assembled: ' + errMsg(e) }), S.idea ? h('p', { text: str(S.idea) }) : null]));
    try { console.error(e); } catch (x) { /* ignore */ }
  }
})();
