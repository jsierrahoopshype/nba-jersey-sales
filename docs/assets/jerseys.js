/* HoopsMatic NBA jersey sales: time machine, show-all toggles, season pickers.
   Pages are fully prerendered; this only swaps views. No third-party scripts. */
(function () {
  var ROOT = document.body.getAttribute('data-root') || '';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  // "Show all N" buttons sit right after the table they open.
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

  // Charts open on the most recent seasons; swipe left for older ones.
  document.querySelectorAll('.chart-scroll').forEach(function (c) { c.scrollLeft = c.scrollWidth; });

  // Popularity vs production season picker.
  document.querySelectorAll('.pvp-select').forEach(function (sel) {
    sel.addEventListener('change', function () {
      var box = sel.closest('.pvp');
      box.querySelectorAll('.pvp-pane').forEach(function (p) {
        p.hidden = p.getAttribute('data-season') !== sel.value;
      });
    });
  });

  // Time machine.
  var tm = document.getElementById('tm');
  if (!tm) return;
  var range = tm.querySelector('.tm-range'), select = tm.querySelector('.tm-select'), body = tm.querySelector('.tm-body');
  var DATA = null, loading = null;

  function load() {
    if (DATA) return Promise.resolve(DATA);
    if (!loading) loading = fetch(ROOT + 'data/releases.json').then(function (r) { return r.json(); }).then(function (d) { DATA = d; return d; });
    return loading;
  }

  function moveHtml(m) {
    if (!m) return '<span class="mv dash">&middot;</span>';
    if (m.kind === 'up') return '<span class="mv up" title="Up ' + m.delta + '">&#9650;' + m.delta + '</span>';
    if (m.kind === 'down') return '<span class="mv down" title="Down ' + m.delta + '">&#9660;' + m.delta + '</span>';
    if (m.kind === 'same') return '<span class="mv same" title="No change">=</span>';
    if (m.kind === 'new') return '<span class="mv new">NEW</span>';
    return '<span class="mv back">BACK</span>';
  }

  function teamHtml(e, teams) {
    var names = e.ts.length > 1 ? e.t.split('/').map(function (s) { return s.trim(); }) : [e.t];
    return names.map(function (nm, i) {
      var sl = e.ts[i], t = teams[sl] || {};
      return '<span class="sw" style="background:' + esc(t.color || '#999') + '"></span><a href="' + ROOT + 'team/' + sl + '/">' + esc(nm) + '</a>';
    }).join(' / ');
  }

  function render(i) {
    var r = DATA.releases[i];
    var h = '<div class="tm-head"><h3>' + esc(r.title) + '</h3><span class="sub2">' + esc(r.season + (r.final ? ' season final' : '') + (r.us ? '' : ', international')) +
      '</span><a class="sub2" href="' + ROOT + 'season/' + r.season + '/">Season page</a>' +
      (r.source ? '<a class="src" href="' + esc(r.source) + '" rel="noopener" target="_blank">Source</a>' : '') + '</div>';
    if (r.note) h += '<p class="hint">' + esc(r.note) + '</p>';
    if (r.entries.length) {
      h += '<div class="table-wrap"><table class="lb"><thead><tr><th class="rank">No.</th><th class="name left">Player</th>' +
        (r.us ? '<th class="mvc">Move</th>' : '') + '<th class="team left">Team</th></tr></thead><tbody>';
      r.entries.forEach(function (e) {
        h += '<tr><td class="rank">' + e.r + '</td><td class="name left"><img class="face" src="' + esc(e.img) + '" alt="' + esc(e.p) +
          '" width="28" height="28" loading="lazy"><a href="' + ROOT + 'player/' + e.s + '/">' + esc(e.p) + '</a>' +
          (e.f ? '<span class="note">' + esc(e.f) + '</span>' : '') + '</td>' +
          (r.us ? '<td class="mvc">' + moveHtml(e.m) + '</td>' : '') + '<td class="team left">' + teamHtml(e, DATA.teams) + '</td></tr>';
      });
      h += '</tbody></table></div>';
    }
    if (r.dropped && r.dropped.length) {
      h += '<p class="hint">Dropped off since the previous full U.S. list: ' + r.dropped.map(function (d) {
        return '<a href="' + ROOT + 'player/' + d.s + '/">' + esc(d.p) + '</a> (No. ' + d.r + ')';
      }).join(', ') + '.</p>';
    }
    body.innerHTML = h;
    range.value = i;
    select.value = i;
  }

  function go(i) {
    var n = parseInt(tm.getAttribute('data-count'), 10);
    i = Math.max(0, Math.min(n - 1, i));
    load().then(function () { render(i); }).catch(function () {
      body.insertAdjacentHTML('afterbegin', '<p class="hint">Could not load the lists.</p>');
    });
  }

  range.addEventListener('input', function () { go(parseInt(range.value, 10)); });
  select.addEventListener('change', function () { go(parseInt(select.value, 10)); });
  tm.querySelector('.tm-prev').addEventListener('click', function () { go(parseInt(range.value, 10) - 1); });
  tm.querySelector('.tm-next').addEventListener('click', function () { go(parseInt(range.value, 10) + 1); });
})();
