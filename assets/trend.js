/* ==========================================================================
   历史趋势查询
   数据来源：data/trends.js（window.__TRENDS__，由 tools/build_trends.py 预计算）
   —— 材料搜索 → 各期各地区含税价折线图 + 明细表（涨红跌绿）
   ========================================================================== */
(function () {
  'use strict';

  var KIND_LABEL = {
    material: '材料指导价', mortar: '干混砂浆', green: '绿色建材',
    labor: '劳务价格', machine: '机械台班'
  };
  var COLORS = ['#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed',
                '#0891b2', '#db2777', '#65a30d', '#ea580c', '#4f46e5'];

  var T = null;              // 趋势索引
  var loading = false;
  var sel = { mid: -1 };     // 当前选中材料
  var chosen = {};           // aid -> true（图表中显示的地区）
  var chart = null;

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmt(n) {
    return (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('zh-CN',
      { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  /* ------------------------------------------------------------ 视图切换 */
  function setView(v) {
    var trend = v === 'trend';
    [].forEach.call(el('viewTabs').children, function (b) {
      b.classList.toggle('on', b.getAttribute('data-view') === v);
    });
    el('trendBar').hidden = !trend;
    el('trendView').hidden = !trend;
    el('content').hidden = trend;
    el('issueField').style.display = trend ? 'none' : '';
    el('btnExport').style.display = trend ? 'none' : '';
    el('btnPrint').style.display = trend ? 'none' : '';
    el('controlbar') && (el('controlbar').style.display = trend ? 'none' : '');
    if (trend) {
      var nm = location.hash.match(/n=(\d+)/);
      try { history.replaceState(null, '', nm ? '#v=trend&n=' + nm[1] : '#v=trend'); } catch (e) {}
      ensureTrends();
    } else {
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    }
  }

  /* ------------------------------------------------------------ 数据加载 */
  function ensureTrends() {
    if (T || loading) return;
    loading = true;
    el('trendStat').innerHTML = '正在加载趋势索引…';
    var s = document.createElement('script');
    s.src = 'data/trends.js';
    s.onload = function () {
      T = window.__TRENDS__;
      loading = false;
      el('trendStat').innerHTML = '共 <b>' + T.mats.length + '</b> 种材料 · ' +
        T.periods.length + ' 期可对比';
      var h = (location.hash || '');
      var m = h.match(/n=(\d+)/);
      if (m && T.mats[+m[1]]) select(+m[1]);
    };
    s.onerror = function () {
      loading = false;
      el('trendStat').innerHTML = '<span style="color:var(--danger,#dc2626)">加载 data/trends.js 失败，请先运行 tools/build_trends.py</span>';
    };
    document.head.appendChild(s);
  }

  /* ------------------------------------------------------------ 联想搜索 */
  var sugItems = [];
  function search(q) {
    q = q.trim().toLowerCase();
    var out = [];
    if (!q) return out;
    for (var i = 0; i < T.mats.length && out.length < 40; i++) {
      var m = T.mats[i];
      var hay = (m[1] + ' ' + m[2]).toLowerCase();
      if (hay.indexOf(q) >= 0) out.push(i);
    }
    // 名称以关键词开头的排前面
    out.sort(function (a, b) {
      var pa = T.mats[a][1].toLowerCase().indexOf(q) === 0 ? 0 : 1;
      var pb = T.mats[b][1].toLowerCase().indexOf(q) === 0 ? 0 : 1;
      return pa - pb || a - b;
    });
    return out;
  }

  function renderSug(ids) {
    var box = el('trendSug');
    sugItems = ids;
    if (!ids.length) { box.hidden = true; box.innerHTML = ''; return; }
    var html = ids.map(function (mid) {
      var m = T.mats[mid];
      var n = countPeriods(mid);
      return '<div class="sug-item" data-mid="' + mid + '">' +
        '<span class="sug-kind">' + esc(KIND_LABEL[m[0]] || m[0]) + '</span>' +
        '<span class="sug-name">' + esc(m[1]) + '</span>' +
        (m[2] ? '<span class="sug-spec">' + esc(m[2]) + '</span>' : '') +
        '<span class="sug-unit">' + esc(m[3]) + '</span>' +
        '<span class="sug-n">' + n + ' 期</span></div>';
    }).join('');
    box.innerHTML = html;
    box.hidden = false;
  }

  function countPeriods(mid) {
    var s = T.series[mid] || {};
    var set = {};
    for (var aid in s) s[aid].forEach(function (x) { set[x[0]] = 1; });
    return Object.keys(set).length;
  }

  /* ------------------------------------------------------------ 选中渲染 */
  function select(mid) {
    sel.mid = mid;
    el('trendSug').hidden = true;
    el('trendInput').value = '';
    // 默认地区：按数据量排序，≤4 个全选，否则取前 4
    chosen = {};
    var list = matAreas(mid);
    list.forEach(function (a, i) { if (list.length <= 4 || i < 4) chosen[a.aid] = true; });
    try { history.replaceState(null, '', '#v=trend&n=' + mid); } catch (e) {}
    render();
  }

  function matAreas(mid) {
    var s = T.series[mid] || {};
    var out = [];
    for (var aid in s) {
      var n = 0, last = null;
      s[aid].forEach(function (x) { n++; last = x[1]; });
      out.push({ aid: +aid, n: n, last: last });
    }
    out.sort(function (a, b) { return b.n - a.n; });
    return out;
  }

  function render() {
    if (sel.mid < 0) return;
    var m = T.mats[sel.mid];
    var host = el('trendView');
    var areas = matAreas(sel.mid);

    var html = '<div class="trend-head">' +
      '<div><h2>' + esc(m[1]) + (m[2] ? ' <small>' + esc(m[2]) + '</small>' : '') + '</h2>' +
      '<p class="trend-sub">' + esc(KIND_LABEL[m[0]] || m[0]) +
      (m[3] ? ' · 单位：' + esc(m[3]) : '') + ' · 含税价（元）</p></div>' +
      '<button id="btnTrendCsv" class="btn">导出 CSV</button></div>';

    html += '<div class="chips" id="trendAreas">';
    areas.forEach(function (a) {
      html += '<div class="chip' + (chosen[a.aid] ? ' on' : '') + '" data-aid="' + a.aid + '">' +
        esc(T.areas[a.aid]) + '<span class="n">' + a.n + '</span></div>';
    });
    html += '</div>';

    html += '<div class="chartbox"><canvas id="trendChart"></canvas></div>';
    html += '<div id="trendTable"></div>';
    host.innerHTML = html;

    renderChart();
    renderTable();
    el('btnTrendCsv').onclick = exportCSV;

    el('trendAreas').addEventListener('click', function (e) {
      var c = e.target.closest('.chip'); if (!c) return;
      var aid = +c.getAttribute('data-aid');
      if (chosen[aid]) delete chosen[aid]; else chosen[aid] = true;
      c.classList.toggle('on');
      renderChart();
      renderTable();
    });
  }

  function matPeriods(mid) {
    // 统一横轴：始终覆盖全部期次（2020年第1期 至 最新一期），
    // 材料缺失的期在图表中留空、明细表中显示「—」，保证不同材料/地区的时间轴可比
    var all = [];
    for (var i = 0; i < T.periods.length; i++) all.push(i);
    return all;
  }

  function renderChart() {
    if (chart) { chart.destroy(); chart = null; }
    var cv = el('trendChart'); if (!cv) return;
    var pis = matPeriods(sel.mid);
    if (!pis.length) return;
    var labels = pis.map(function (pi) {
      var p = T.periods[pi];
      return (p.p || '').replace('年第', '-').replace('期', '');
    });
    var datasets = [], ci = 0;
    for (var aid in chosen) {
      var pts = (T.series[sel.mid] || {})[aid] || [];
      var map = {}; pts.forEach(function (x) { map[x[0]] = x[1]; });
      var color = COLORS[ci++ % COLORS.length];
      datasets.push({
        label: T.areas[+aid],
        data: pis.map(function (pi) { return map[pi] != null ? map[pi] : null; }),
        borderColor: color, backgroundColor: color,
        spanGaps: true, tension: 0.15, pointRadius: 3, pointHoverRadius: 5,
        borderWidth: 2
      });
    }
    if (!datasets.length) return;
    chart = new Chart(cv.getContext('2d'), {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 14, boxHeight: 2 } },
          tooltip: {
            callbacks: {
              title: function (items) {
                var p = T.periods[pis[items[0].dataIndex]];
                return (p.p || '') + (p.d ? '（发布 ' + p.d + '）' : '');
              },
              label: function (c) { return c.dataset.label + '：' + fmt(c.parsed.y); }
            }
          }
        },
        scales: {
          x: { grid: { display: false } },
          y: { ticks: { callback: function (v) { return fmt(v); } } }
        }
      }
    });
  }

  function renderTable() {
    var box = el('trendTable'); if (!box) return;
    var pis = matPeriods(sel.mid);
    var aids = Object.keys(chosen).map(Number);
    if (!pis.length || !aids.length) { box.innerHTML = ''; return; }
    var m = T.mats[sel.mid];
    var hasSpec = !!m[2];

    var html = '<div class="tablewrap"><table><thead><tr><th>期次</th>';
    aids.forEach(function (aid) { html += '<th class="num">' + esc(T.areas[aid]) + '</th>'; });
    html += '</tr></thead><tbody>';

    var prev = {};
    pis.forEach(function (pi) {
      var p = T.periods[pi];
      html += '<tr><td class="name">' + esc(p.p || p.k) +
        (p.d ? ' <small class="trend-date">' + p.d + '</small>' : '') + '</td>';
      aids.forEach(function (aid) {
        var pts = (T.series[sel.mid] || {})[aid] || [];
        var v = null;
        for (var i = 0; i < pts.length; i++) if (pts[i][0] === pi) { v = pts[i][1]; break; }
        var cls = '', arrow = '';
        if (v != null && prev[aid] != null && prev[aid] !== 0) {
          var diff = v - prev[aid];
          if (diff > 0) { cls = 'up'; arrow = '▲'; }
          else if (diff < 0) { cls = 'down'; arrow = '▼'; }
          if (diff) {
            var pct = (diff / prev[aid] * 100).toFixed(1);
            arrow += Math.abs(diff).toFixed(2) + '（' + pct + '%）';
          }
        }
        prev[aid] = v != null ? v : prev[aid];
        html += '<td class="num ' + cls + '">' + (v != null ? fmt(v) : '—') +
          (arrow ? ' <small>' + arrow + '</small>' : '') + '</td>';
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    box.innerHTML = html;
  }

  /* ------------------------------------------------------------ 导出 CSV */
  function exportCSV() {
    if (sel.mid < 0) return;
    var m = T.mats[sel.mid];
    var pis = matPeriods(sel.mid);
    var aids = Object.keys(chosen).map(Number);
    var lines = [['材料名称', '型号规格', '类别', '单位', '期次', '发布日期']
      .concat(aids.map(function (a) { return T.areas[a] + ' 含税价(元)'; })).map(csvCell).join(',')];
    pis.forEach(function (pi) {
      var p = T.periods[pi];
      var arr = [m[1], m[2], KIND_LABEL[m[0]] || m[0], m[3], p.p || p.k, p.d];
      aids.forEach(function (aid) {
        var pts = (T.series[sel.mid] || {})[aid] || [];
        var v = null;
        for (var i = 0; i < pts.length; i++) if (pts[i][0] === pi) { v = pts[i][1]; break; }
        arr.push(v != null ? v : '');
      });
      lines.push(arr.map(csvCell).join(','));
    });
    var blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '价格趋势_' + m[1] + (m[2] ? '_' + m[2] : '') + '.csv';
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }
  function csvCell(v) {
    var s = String(v == null ? '' : v);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  /* ------------------------------------------------------------ 初始化 */
  document.addEventListener('DOMContentLoaded', function () {
    el('viewTabs').addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      setView(b.getAttribute('data-view'));
    });

    var input = el('trendInput'), timer = null;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () { renderSug(T ? search(input.value) : []); }, 120);
    });
    input.addEventListener('focus', function () {
      if (T) renderSug(search(input.value));
    });
    el('trendSug').addEventListener('mousedown', function (e) {
      var it = e.target.closest('.sug-item'); if (!it) return;
      select(+it.getAttribute('data-mid'));
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.trend-search')) el('trendSug').hidden = true;
    });

    if ((location.hash || '').indexOf('v=trend') >= 0) setView('trend');
  });
})();
