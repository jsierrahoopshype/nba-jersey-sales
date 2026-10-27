/* HoopsMatic NBA jersey sales: tooltips, toggles, player chart and compare, time machine, search.
   Pages are fully prerendered; this only swaps views. No third-party scripts. */
(function () {
  var ROOT = document.body.getAttribute('data-root') || '';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function qs() { return new URLSearchParams(location.search); }
  function setQs(params) {
    var q = qs();
    Object.keys(params).forEach(function (k) {
      if (params[k] === '' || params[k] == null) q.delete(k); else q.set(k, params[k]);
    });
    var s = q.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : '') + location.hash);
  }
  var cache = {};
  function getJSON(path) {
    if (!cache[path]) cache[path] = fetch(ROOT + path).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    return cache[path];
  }

  // ---- "Show all N" buttons sit right after the table they open.
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('.showall');
    if (!b) return;
    var wrap = b.previousElementSibling;
    var t = wrap && wrap.querySelector('table.lb');
    if (!t) return;
    var open = t.classList.toggle('open');
    if (!b.dataset.txt) b.dataset.txt = b.textContent;
    b.textContent = open ? 'Show fewer' : b.dataset.txt;
  });

  // ---- Tooltips: tap, click or keyboard focus on anything with data-tip.
  var tip = document.getElementById('tip'), tipFor = null;
  function showTip(el) {
    if (!tip) return;
    tip.textContent = el.getAttribute('data-tip');
    tip.hidden = false;
    var r = el.getBoundingClientRect(), w = Math.min(280, window.innerWidth - 24);
    tip.style.maxWidth = w + 'px';
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    var left = Math.max(12, Math.min(window.innerWidth - tw - 12, r.left + r.width / 2 - tw / 2));
    var top = r.top - th - 8;
    if (top < 8) top = r.bottom + 8;
    tip.style.left = (left + window.scrollX) + 'px';
    tip.style.top = (top + window.scrollY) + 'px';
    tipFor = el;
    if (el.classList.contains('since')) el.setAttribute('aria-expanded', 'true');
  }
  function hideTip() {
    if (tip) tip.hidden = true;
    if (tipFor && tipFor.classList.contains('since')) tipFor.setAttribute('aria-expanded', 'false');
    tipFor = null;
  }
  document.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-tip]');
    if (el && el.tagName.toLowerCase() !== 'a') {
      if (tipFor === el) hideTip(); else showTip(el);
      return;
    }
    if (!ev.target.closest('#tip')) hideTip();
  });
  document.addEventListener('focusin', function (ev) {
    var el = ev.target.closest && ev.target.closest('[data-tip]');
    if (el) showTip(el); else hideTip();
  });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') hideTip(); });
  // A focused point scrolls its chart into view: keep the tip on it rather than closing it.
  document.addEventListener('scroll', function (ev) {
    if (!tipFor || !ev.target.contains || !ev.target.contains(tipFor)) return;
    showTip(tipFor);
  }, {passive: true, capture: true});

  // Charts open on the most recent seasons; swipe left for older ones.
  function scrollRight() { document.querySelectorAll('.chart-scroll').forEach(function (c) { c.scrollLeft = c.scrollWidth; }); }
  scrollRight();

  // ---- Segmented buttons that switch panes (team points basis).
  document.querySelectorAll('.seg-btns').forEach(function (g) {
    var btns = g.querySelectorAll('.segb[data-pane]');
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        btns.forEach(function (x) {
          var on = x === b;
          x.setAttribute('aria-pressed', on ? 'true' : 'false');
          var pane = document.getElementById(x.getAttribute('data-pane'));
          if (pane) pane.hidden = !on;
        });
      });
    });
  });

  // ---- Ordinal sparkline for the list tables. Mirrors sparkline() in build_jerseys.py.
  // t: [[rank or 0, state initial (r ranked, o out, u unknown), 1 if a No. 1 only list], ...]
  function spark(t) {
    var n = t.length, mx = 15;
    t.forEach(function (v) { if (v[0]) mx = Math.max(mx, v[0]); });
    mx = mx > 20 ? 25 : (mx > 15 ? 20 : 15);
    var w = 68, h = 22, x = function (i) { return 4 + (8 - n + i) * 8; }, y = function (v) { return (3 + (v - 1) * (h - 6) / (mx - 1)).toFixed(1); };
    var words = t.map(function (v) { return v[0] ? String(v[0]) : (v[1] === 'o' ? 'out' : 'unknown'); });
    var label = 'Rank over last ' + (n === 1 ? 'list' : n + ' lists') + ': ' + words.join(', ');
    var P = ['<svg class="spark" width="68" height="22" viewBox="0 0 68 22" role="img" aria-label="' + esc(label) + '"><title>' + esc(label) + '</title>',
      '<line class="sp-base" x1="2" x2="66" y1="3" y2="3"/>'];
    for (var i = 1; i < n; i++) if (t[i - 1][0] && t[i][0]) P.push('<line class="sp-l" x1="' + x(i - 1) + '" y1="' + y(t[i - 1][0]) + '" x2="' + x(i) + '" y2="' + y(t[i][0]) + '"/>');
    t.forEach(function (v, i) {
      if (!v[0]) return;
      var last = i === n - 1;
      P.push('<circle class="' + (last ? 'sp-c' : (v[2] ? 'sp-h' : 'sp-d')) + '" cx="' + x(i) + '" cy="' + y(v[0]) + '" r="' + (last ? '2.8' : '2') + '"/>');
    });
    P.push('</svg>');
    return P.join('');
  }

  // ---- List tables on phones: the number columns move into a row that opens on tap (or Enter/Space).
  var phone = window.matchMedia('(max-width: 640px)');
  function armRows(scope) {
    (scope || document).querySelectorAll('table.lt tr.xr').forEach(function (tr) {
      if (phone.matches) {
        tr.setAttribute('tabindex', '0');
        tr.setAttribute('aria-expanded', tr.nextElementSibling && !tr.nextElementSibling.hidden ? 'true' : 'false');
      } else {
        tr.removeAttribute('tabindex');
        tr.removeAttribute('aria-expanded');
      }
    });
  }
  function toggleRow(tr) {
    var d = tr.nextElementSibling;
    if (!d || !d.classList.contains('xrow')) return;
    d.hidden = !d.hidden;
    tr.setAttribute('aria-expanded', d.hidden ? 'false' : 'true');
  }
  document.addEventListener('click', function (ev) {
    var tr = ev.target.closest && ev.target.closest('table.lt tr.xr');
    if (!tr || !phone.matches || ev.target.closest('a,button,[data-tip]')) return;
    toggleRow(tr);
  });
  document.addEventListener('keydown', function (ev) {
    var tr = ev.target.closest && ev.target.closest('table.lt tr.xr');
    if (!tr || !phone.matches || ev.target !== tr || (ev.key !== 'Enter' && ev.key !== ' ')) return;
    ev.preventDefault();
    toggleRow(tr);
  });
  armRows(document);
  if (phone.addEventListener) phone.addEventListener('change', function () { armRows(document); });

  // ---- Teams index: sort the grid by points (default) or name.
  document.querySelectorAll('.segb[data-sort]').forEach(function (b) {
    b.addEventListener('click', function () {
      var grid = document.querySelector('.tgrid');
      if (!grid) return;
      var by = b.getAttribute('data-sort'), items = Array.prototype.slice.call(grid.children);
      items.sort(function (x, y) {
        return by === 'name' ? x.getAttribute('data-name').localeCompare(y.getAttribute('data-name'))
          : (+x.getAttribute('data-rank')) - (+y.getAttribute('data-rank'));
      });
      items.forEach(function (li) { grid.appendChild(li); });
      b.parentNode.querySelectorAll('.segb').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
    });
  });

  // ---- Popularity vs awards season picker.
  document.querySelectorAll('.pva-select').forEach(function (sel) {
    var show = function () {
      sel.closest('.pva').querySelectorAll('.pva-pane').forEach(function (p) {
        p.hidden = p.getAttribute('data-season') !== sel.value;
      });
    };
    sel.addEventListener('change', show);
    // ?pva=<season> (from an Awards cell in a list table) opens that season.
    var want = qs().get('pva');
    if (want && sel.querySelector('option[value="' + want + '"]')) { sel.value = want; show(); }
  });

  // ---- Search (hub).
  var find = document.querySelector('.find');
  if (find) {
    var idx = JSON.parse(find.closest('.search').querySelector('.find-data').textContent);
    var res = find.closest('.search').querySelector('.find-res');
    var norm = function (s) { return s.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[.'’]/g, '').toLowerCase(); };
    idx.forEach(function (x) { x.k = norm(x.n); });
    var run = function () {
      var q = norm(find.value.trim());
      if (!q) { res.innerHTML = ''; return; }
      var words = q.split(/\s+/);
      var hits = idx.filter(function (x) { return words.every(function (w) { return x.k.indexOf(w) >= 0; }); });
      hits.sort(function (a, b) { return (a.k.indexOf(words[0]) - b.k.indexOf(words[0])) || a.n.localeCompare(b.n); });
      res.innerHTML = hits.slice(0, 8).map(function (x) {
        var pic = x.t === 'Player' ? '<img class="face" src="' + esc(x.i) + '" alt="" width="28" height="28" loading="lazy">' :
          (x.i ? '<img class="logo" src="' + esc(x.i) + '" alt="" width="16" height="16" loading="lazy">' : '<span class="sw" style="background:' + esc(x.c) + '"></span>');
        return '<li>' + pic + '<a class="nm" href="' + ROOT + x.u + '">' + esc(x.n) + '</a><span class="tag">' + esc(x.t) + '</span></li>';
      }).join('') || '<li class="dash">No match</li>';
    };
    find.addEventListener('input', run);
    find.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { var a = res.querySelector('a'); if (a) { ev.preventDefault(); location.href = a.href; } }
    });
  }

  // ---- Player chart: season rankings or every list, with an optional second player (?vs=slug).
  var pc = document.querySelector('.pchart');
  if (pc) initPlayerChart(pc);

  function colState(col, v) {
    if (v) return 'ranked';
    if (col.kind === 'full') return 'outside';
    if (col.kind === 'partial' || col.kind === 'no1') return 'unknown';
    return 'nodata';
  }
  function joinNames(a) { return a.length <= 2 ? a.join(' and ') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1]; }
  function tipText(mode, c, v, who) {
    if (mode === 'season') {
      var b = who + ', ' + c.lab;
      if (v) return b + ': No. ' + v + ' in the season ranking (from ' + joinNames(c.fed) + ')';
      if (c.kind === 'full') return b + ': outside the season top ' + c.depth;
      if (c.kind === 'no1') return b + ': unknown, only the No. 1 is known';
      return b + ': no data';
    }
    var t = who + ', ' + c.title;
    if (v) return t + ': No. ' + v;
    if (c.kind === 'full') return t + ': not on this ' + c.depth + '-player list';
    if (c.kind === 'partial') return t + ': unknown, incomplete list (only the No. 1)';
    return t + ': no data, empty list';
  }

  // Mirrors render_chart() in build_jerseys.py.
  function drawChart(cols, mode, series, markers) {
    var cs = cols[mode], key = mode.charAt(0), idx = [];
    cs.forEach(function (c, i) { if (series.some(function (s) { return s[key][c.k]; })) idx.push(i); });
    if (!idx.length) return {axis: '', plot: ''};
    var i0 = Math.max(0, Math.min.apply(null, idx) - 1), i1 = Math.min(cs.length - 1, Math.max.apply(null, idx) + 1);
    var win = cs.slice(i0, i1 + 1), maxr = 15;
    win.forEach(function (c) {
      if (c.kind === 'full') maxr = Math.max(maxr, c.depth);
      series.forEach(function (s) { if (s[key][c.k]) maxr = Math.max(maxr, s[key][c.k]); });
    });
    maxr = maxr > 20 ? 25 : (maxr > 15 ? 20 : 15);
    // x positions are percentages so the chart fills its container; min-width keeps columns >= 28px
    // (MIN_COL in build_jerseys.py) and the scroller takes over below that.
    var top = 26, h = 200, band = top + h + 22, height = band + 70, n = win.length, cp = 100 / n;
    var y = function (rk) { return top + (rk - 1) * h / (maxr - 1); };
    var f = function (v) { return (Math.round(v * 10) / 10).toString(); };
    var pc = function (v) { return v.toFixed(3) + '%'; };
    var x = function (i) { return pc((i + 0.5) * cp); };
    var A = ['<svg class="yax" width="30" height="' + height + '" viewBox="0 0 30 ' + height + '" aria-hidden="true">'];
    var P = ['<svg class="rchart fluid" width="100%" height="' + height + '" style="min-width:' + (n * 28 + 8) + 'px" role="img" aria-label="' +
      esc(series.map(function (s) { return s.name; }).join(' and ') + ' rank by ' + (mode === 'season' ? 'season' : 'list') + ', No. 1 at the top') + '">'];
    [1, 5, 10, 15, 20, 25].forEach(function (rk) {
      if (rk <= maxr) {
        P.push('<line class="grid" x1="0" x2="100%" y1="' + f(y(rk)) + '" y2="' + f(y(rk)) + '"/>');
        A.push('<text class="ytk" x="26" y="' + f(y(rk) + 4) + '">' + rk + '</text>');
      }
    });
    A.push('<text class="ytk" x="26" y="' + f(band + 4) + '">Out</text></svg>');
    win.forEach(function (c, i) {
      var bx = pc((i + 0.07) * cp), bw = pc(0.86 * cp);
      if (c.kind === 'empty' || c.kind === 'none') P.push('<rect class="nodata" x="' + bx + '" y="' + (top - 6) + '" width="' + bw + '" height="' + (band - top + 14) + '"/>');
      else if (c.kind === 'partial' || c.kind === 'no1') P.push('<rect class="gap" x="' + bx + '" y="' + (top - 6) + '" width="' + bw + '" height="' + (band - top + 14) + '"/>');
      else if (c.depth < maxr) P.push('<rect class="short" x="' + bx + '" y="' + f(y(c.depth) + 6) + '" width="' + bw + '" height="' + f(Math.max(0, top + h - y(c.depth) - 6)) + '"/>');
      P.push('<svg x="' + x(i) + '" y="0" width="1" height="1" overflow="visible"><text class="xtk" transform="translate(4,' + (band + 16) + ') rotate(-60)">' + esc(c.lab) + '</text></svg>');
    });
    series.forEach(function (s, si) {
      var cls = 's' + si, prev = null;
      win.forEach(function (c, i) {
        var v = s[key][c.k], st = colState(c, v);
        if (st === 'ranked') {
          if (prev) P.push('<line class="seg ' + cls + (prev[2] ? ' bridge' : '') + '" x1="' + x(prev[0]) + '" y1="' + f(y(prev[1])) + '" x2="' + x(i) + '" y2="' + f(y(v)) + '"/>');
          prev = [i, v, false];
        } else if (st === 'outside') prev = null;
        else if (prev) prev = [prev[0], prev[1], true];
      });
      win.forEach(function (c, i) {
        var v = s[key][c.k], st = colState(c, v), t = esc(tipText(mode, c, v, s.name));
        if (st === 'ranked') P.push('<g class="pt ' + cls + (v === 1 ? ' one' : '') + '" tabindex="0" data-tip="' + t + '"><circle cx="' + x(i) + '" cy="' + f(y(v)) + '" r="5"/><title>' + t + '</title></g>');
        else if (si === 0 && st === 'outside') P.push('<g class="pt out" tabindex="0" data-tip="' + t + '"><circle cx="' + x(i) + '" cy="' + f(band) + '" r="3.5"/><title>' + t + '</title></g>');
        else if (si === 0 && st === 'unknown') P.push('<g class="pt unk" tabindex="0" data-tip="' + t + '"><text x="' + x(i) + '" y="' + f(band + 4) + '">?</text><title>' + t + '</title></g>');
      });
    });
    if (markers) {
      var k2i = {};
      win.forEach(function (c, i) { k2i[c.k] = i; });
      markers.forEach(function (mk, n) {
        if (!(mk.k in k2i)) return;
        var cx = x(k2i[mk.k]);
        P.push('<g class="mk' + (n >= 4 ? ' mx' : '') + '" data-tip="' + esc(mk.t) + '"><line x1="' + cx + '" x2="' + cx + '" y1="' + (top - 12) + '" y2="' + band + '"/><circle cx="' + cx + '" cy="' + (top - 16) + '" r="7"/><text x="' + cx + '" y="' + (top - 12) + '">' + (n + 1) + '</text></g>');
      });
    }
    P.push('</svg>');
    return {axis: A.join(''), plot: P.join('')};
  }

  function initPlayerChart(box) {
    var data = JSON.parse(box.querySelector('.chart-data').textContent);
    var scroll = box.querySelector('.chart-scroll'), yaxis = box.querySelector('.yaxis'), key = box.querySelector('.mkey'), vsKey = box.querySelector('.vs-key');
    var sel = box.querySelector('.vs-select'), btns = box.querySelectorAll('.segb[data-mode]');
    var q = qs(), state = {mode: q.get('basis') === 'list' ? 'list' : 'season', vs: q.get('vs') || ''};
    var other = null;

    function markerKey(list) {
      return list.map(function (mk, n) { return '<li' + (n >= 4 ? ' class="mx"' : '') + '>' + (mk.h || esc(mk.t)) + '</li>'; }).join('');
    }
    function render() {
      var series = [data.me];
      if (other) series.push(other);
      var ch = drawChart(data.cols, state.mode, series, data.markers[state.mode]);
      scroll.innerHTML = ch.plot;
      yaxis.innerHTML = ch.axis;
      if (key) key.innerHTML = markerKey(data.markers[state.mode]);
      btns.forEach(function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-mode') === state.mode ? 'true' : 'false'); });
      if (other) {
        vsKey.hidden = false;
        vsKey.innerHTML = '<span><i class="lg-s0"></i>' + esc(data.me.name) + '</span><span><i class="lg-s1"></i><a class="nm" href="' + ROOT + 'player/' + esc(other.slug) + '/">' + esc(other.name) + '</a></span>';
      } else {
        vsKey.hidden = true;
        vsKey.innerHTML = '';
      }
      scroll.scrollLeft = scroll.scrollWidth;
    }
    function loadVs(slug) {
      if (!slug) { other = null; render(); return; }
      getJSON('data/players.json').then(function (all) {
        var p = all.filter(function (x) { return x.slug === slug; })[0];
        other = p ? {name: p.name, slug: p.slug, s: p.s || {}, l: p.l || {}} : null;
        if (!p) { state.vs = ''; sel.value = ''; setQs({vs: ''}); }
        render();
      }).catch(function () { other = null; render(); });
    }
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        state.mode = b.getAttribute('data-mode');
        setQs({basis: state.mode === 'list' ? 'list' : ''});
        render();
      });
    });
    sel.addEventListener('change', function () {
      state.vs = sel.value;
      setQs({vs: state.vs});
      loadVs(state.vs);
    });
    if (state.vs && state.vs !== data.me.slug) {
      sel.value = state.vs;
      if (sel.value !== state.vs) { state.vs = ''; setQs({vs: ''}); }
    } else state.vs = '';
    if (state.vs) loadVs(state.vs);
    else if (state.mode !== 'season') render();
  }

  // ---- Time machine (hub): year, U.S./International filter and list, all kept in the URL query.
  var tm = document.getElementById('tm');
  if (tm) initTimeMachine(tm);

  function moveHtml(m) {
    if (!m) return '<span class="mv dash">&middot;</span>';
    if (m.kind === 'up') return '<span class="mv up" title="Up ' + m.delta + ' from No. ' + m.from + '">&#9650;' + m.delta + '</span>';
    if (m.kind === 'down') return '<span class="mv down" title="Down ' + m.delta + ' from No. ' + m.from + '">&#9660;' + m.delta + '</span>';
    if (m.kind === 'same') return '<span class="mv same" title="Same rank">=</span>';
    if (m.kind === 'new') return '<span class="mv new">First appearance</span>';
    if (m.kind === 'back') return '<span class="mv back">Re-entry</span>';
    return '<span class="mv dash" title="' + esc('Not compared: ' + (m.why || '')) + '">&middot;</span>';
  }
  function teamHtml(e, teams) {
    return e.tn.map(function (nm, i) {
      var sl = e.ts[i], t = teams[sl] || {}, lg = e.lg[i];
      var mark = lg ? '<img class="logo" src="' + esc(lg) + '" alt="" width="16" height="16" loading="lazy">' :
        '<span class="sw" style="background:' + esc(t.color || '#999') + '"></span>';
      return mark + '<a class="nm" href="' + ROOT + 'team/' + sl + '/">' + esc(nm) + '</a>';
    }).join(' / ');
  }

  function initTimeMachine(tm) {
    var yearSel = tm.querySelector('.tm-year'), typeSel = tm.querySelector('.tm-type'), emptyCb = tm.querySelector('.tm-empty');
    var select = tm.querySelector('.tm-select'), body = tm.querySelector('.tm-body');
    var DATA = null, current = tm.getAttribute('data-latest');
    var q = qs();

    function visible() {
      return DATA.releases.filter(function (r) {
        if (r.status === 'empty' && !emptyCb.checked) return false;
        if (typeSel.value === 'us' && !r.us) return false;
        if (typeSel.value === 'intl' && r.us) return false;
        if (yearSel.value && r.year !== yearSel.value) return false;
        return true;
      });
    }
    function optLabel(r) {
      return r.title + (r.us ? '' : ' (international)') + (r.status === 'partial' ? ' (incomplete)' : '') + (r.status === 'empty' ? ' (empty)' : '');
    }
    function rebuild() {
      var vis = visible();
      if (!vis.some(function (r) { return r.id === current; }) && vis.length) {
        // Nearest visible list at or after the current one, else the last one.
        var ids = DATA.releases.map(function (r) { return r.id; }), at = ids.indexOf(current);
        var next = vis.filter(function (r) { return ids.indexOf(r.id) >= at; })[0];
        current = (next || vis[vis.length - 1]).id;
      }
      select.innerHTML = vis.map(function (r) { return '<option value="' + r.id + '">' + esc(optLabel(r)) + '</option>'; }).join('');
      select.value = current;
    }
    function render() {
      var r = DATA.releases.filter(function (x) { return x.id === current; })[0];
      if (!r) return;
      var tags = (r.us ? '' : '<span class="tag">International</span>') + (r.status === 'partial' ? '<span class="tag warn">Incomplete</span>' : '') + (r.status === 'empty' ? '<span class="tag warn">Empty</span>' : '');
      var h = '<div class="tm-head"><h3>' + esc(r.title) + '</h3><span class="sub2">' + esc(r.season + (r.final ? ' season final' : '')) + '</span>' + tags +
        '<a class="sub2" href="' + ROOT + 'season/' + r.season + '/">Season page</a>' +
        (r.source ? '<a class="src" href="' + esc(r.source) + '" rel="noopener" target="_blank">Source</a>' : '') + '</div>';
      if (r.note) h += '<p class="hint">' + esc(r.note) + '</p>';
      if (r.entries.length) {
        var since = 'Change';
        if (r.compare) {
          var full = 'Compared with the ' + r.compare.type + ' list (' + r.compare.label + '). This list: ' + r.label + '. Exact move for every player on both lists.';
          since = '<button type="button" class="since" data-tip="' + esc(full) + '" title="' + esc(full) + '">Change since ' + esc(r.compare.my) + '</button>';
        }
        var tip = function (lab, k) { return '<span class="th-tip" tabindex="0" data-tip="' + esc(DATA.tips[k]) + '">' + lab + '</span>'; };
        var ncol = r.us ? 8 : 7;
        h += '<div class="table-wrap"><table class="lb lt"><thead><tr><th class="rank">No.</th><th class="name left">Player</th>' +
          '<th class="trend">' + tip('Trend', 'trend') + '</th><th class="num dx">' + tip('Streak', 'streak') + '</th><th class="num dx">' + tip('Lists', 'lists') +
          '</th><th class="num dx best">' + tip('Best', 'best') + '</th><th class="num dx">' + tip('Awards', 'awards') + '</th>' +
          (r.us ? '<th class="mvc">' + since + '</th>' : '') + '</tr></thead><tbody>';
        r.entries.forEach(function (e) {
          var k = e.m && e.m.kind, cls = e.r === 1 ? ' lead' : ((k === 'same' || k === 'none') ? ' quiet' : '');
          var x = e.x, best = x.b ? 'No. ' + x.b + ' (' + x.by + ')' : '-';
          var aw = x.aw == null ? '<span class="dash">-</span>' : (x.al ? '<a class="aw" href="' + ROOT + '?pva=' + r.season + '#pva">No. ' + x.aw + '</a>' : 'No. ' + x.aw);
          h += '<tr class="xr' + cls + '"><td class="rank">' + e.r + '</td><td class="name left"><img class="face" src="' + esc(e.img) + '" alt="' + esc(e.p) +
            '" width="28" height="28" loading="lazy"><span class="pwrap"><a class="nm" href="' + ROOT + 'player/' + e.s + '/">' + esc(e.p) + '</a><span class="tline">' + teamHtml(e, DATA.teams) + '</span>' +
            (e.fh ? '<span class="note">' + e.fh + '</span>' : '') + '</span></td>' +
            '<td class="trend">' + spark(x.t) + '</td><td class="num dx">' + x.st + '</td><td class="num dx">' + x.ls + '</td><td class="num dx best">' + esc(best) +
            '</td><td class="num dx">' + aw + '</td>' +
            (r.us ? '<td class="mvc">' + moveHtml(e.m) + '</td>' : '') + '</tr>' +
            '<tr class="xrow" hidden><td colspan="' + ncol + '"><span><b>Streak</b> ' + x.st + '</span><span><b>Lists</b> ' + x.ls + '</span><span><b>Best</b> ' +
            esc(best) + '</span><span><b>Awards</b> ' + aw + '</span></td></tr>';
        });
        h += '</tbody></table></div>';
      }
      if (r.dropped && r.dropped.length) {
        h += '<p class="hint">Dropped out since the previous complete U.S. list: ' + r.dropped.map(function (d) {
          return '<a class="nm" href="' + ROOT + 'player/' + d.s + '/">' + esc(d.p) + '</a> (No. ' + d.r + ')';
        }).join(', ') + '.</p>';
      }
      if (r.not_compared && r.not_compared.length) {
        h += '<p class="hint">Not compared (ranked deeper than this list\'s ' + r.entries.length + ' spots before): ' + r.not_compared.map(function (d) {
          return '<a class="nm" href="' + ROOT + 'player/' + d.s + '/">' + esc(d.p) + '</a> (No. ' + d.r + ')';
        }).join(', ') + '.</p>';
      }
      body.innerHTML = h;
      armRows(body);
      select.value = current;
    }
    function sync() {
      setQs({list: current === tm.getAttribute('data-latest') ? '' : current, year: yearSel.value, type: typeSel.value, empty: emptyCb.checked ? '1' : ''});
    }
    function withData(fn) {
      if (DATA) { fn(); return; }
      getJSON('data/releases.json').then(function (d) { DATA = d; fn(); }).catch(function () {
        body.insertAdjacentHTML('afterbegin', '<p class="hint">Could not load the lists.</p>');
      });
    }
    function step(d) {
      withData(function () {
        var vis = visible(), i = vis.map(function (r) { return r.id; }).indexOf(current);
        i = Math.max(0, Math.min(vis.length - 1, i + d));
        current = vis[i].id; rebuild(); render(); sync();
      });
    }
    function onFilter() { withData(function () { rebuild(); render(); sync(); }); }
    yearSel.addEventListener('change', onFilter);
    typeSel.addEventListener('change', onFilter);
    emptyCb.addEventListener('change', onFilter);
    select.addEventListener('change', function () { current = select.value; withData(function () { render(); sync(); }); });
    tm.querySelector('.tm-prev').addEventListener('click', function () { step(-1); });
    tm.querySelector('.tm-next').addEventListener('click', function () { step(1); });
    tm.querySelectorAll('.ms').forEach(function (b) {
      b.addEventListener('click', function () {
        current = b.getAttribute('data-list');
        yearSel.value = ''; typeSel.value = '';
        withData(function () { rebuild(); render(); sync(); });
      });
    });
    if (q.get('list') || q.get('year') || q.get('type') || q.get('empty')) {
      if (q.get('year')) yearSel.value = q.get('year');
      if (q.get('type')) typeSel.value = q.get('type');
      if (q.get('empty') === '1') emptyCb.checked = true;
      if (q.get('list')) current = q.get('list');
      withData(function () {
        if (!DATA.releases.some(function (r) { return r.id === current; })) current = tm.getAttribute('data-latest');
        var r = DATA.releases.filter(function (x) { return x.id === current; })[0];
        if (r && r.status === 'empty') emptyCb.checked = true;
        rebuild(); render();
      });
    }
  }
})();
