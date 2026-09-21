/* ==========================================================================
   青海工程造价管理信息 · 信息价查询
   数据来源：data/index.js（期数索引） + data/<期号>.js（该期数据）
   —— 新增一期只需把新的 <期号>.js 放进 data/ 并更新 index.js，
      本页面自动出现新期数，无需改动本文件。
   ========================================================================== */
(function () {
  'use strict';

  var KIND_LABEL = {
    material: '材料指导价',
    mortar: '干混砂浆',
    green: '绿色建材',
    labor: '劳务价格',
    machine: '机械台班'
  };
  var KIND_ORDER = ['material', 'mortar', 'green', 'labor', 'machine'];

  var state = {
    issueKey: null,
    region: '',
    kind: 'all',
    mode: 'incl',       // incl | excl | both
    q: '',
    collapse: false
  };

  var index = window.__ISSUE_INDEX__ || { issues: [] };
  var issues = (index.issues || []).slice().sort(function (a, b) {
    return String(b.period + b.issueNo).localeCompare(String(a.period + a.issueNo));
  });
  var data = null;                     // 当前期完整数据
  var loaded = {};                     // 已加载的期缓存

  /* ---------------------------------------------------------------- 工具 */
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function hi(text) {
    var t = esc(text);
    if (!state.q) return t;
    var q = state.q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try { return t.replace(new RegExp(q, 'gi'), function (m) { return '<mark>' + m + '</mark>'; }); }
    catch (e) { return t; }
  }
  function uniq(arr) {
    var out = [], seen = {};
    arr.forEach(function (x) { if (x && !seen[x]) { seen[x] = 1; out.push(x); } });
    return out;
  }

  /* ------------------------------------------------------------ 数据加载 */
  function loadIssue(key, cb) {
    if (loaded[key]) { data = loaded[key]; return cb(); }
    var s = document.createElement('script');
    s.src = 'data/' + key + '.js';
    s.onload = function () {
      var d = (window.__ISSUE_DATA__ || {})[key];
      if (!d) return cb(new Error('数据文件未包含期号：' + key));
      loaded[key] = d; data = d; cb();
    };
    s.onerror = function () { cb(new Error('无法加载 data/' + key + '.js（文件不存在或路径错误）')); };
    document.head.appendChild(s);
  }

  /* ------------------------------------------------------------ 列定义 */
  function uniqAreas(rows) {
    var out = [], seen = {};
    rows.forEach(function (r) {
      (r.prices || []).forEach(function (p) {
        if (p.area && !seen[p.area]) { seen[p.area] = 1; out.push(p.area); }
      });
    });
    return out;
  }

  function buildColumns(section, mode) {
    var rows = section.rows || [];
    var anyExcl = rows.some(function (r) {
      return (r.prices || []).some(function (p) { return p.excl; });
    });
    var areas = (section.areas && section.areas.length) ? section.areas : uniqAreas(rows);

    if (areas.length <= 1) {
      if (anyExcl) {
        return [{ k: 'incl', label: '含税价格(元)' }, { k: 'excl', label: '除税价格(元)' }];
      }
      return [{ k: 'incl', label: '单价(元)' }];
    }

    var cols = [];
    areas.forEach(function (a) {
      if (mode === 'both' && anyExcl) {
        cols.push({ k: 'incl', area: a, label: a + ' 含税' });
        cols.push({ k: 'excl', area: a, label: a + ' 除税' });
      } else {
        var kk = (mode === 'excl' && anyExcl) ? 'excl' : 'incl';
        cols.push({ k: kk, area: a, label: a + (anyExcl ? (kk === 'excl' ? ' 除税' : ' 含税') : '') });
      }
    });
    return cols;
  }

  // 表头文案随表类型自适应；无型号规格的表自动隐藏该列
  var KIND_HEADS = {
    material: { name: '材料名称', spec: '型号规格' },
    mortar: { name: '材料名称', spec: '强度等级' },
    green: { name: '材料名称', spec: '型号规格' },
    labor: { name: '工种名称', spec: null },
    machine: { name: '机械名称', spec: '型号规格' }
  };
  function columnMeta(section) {
    var h = KIND_HEADS[section.kind] || { name: '名称', spec: '型号规格' };
    var hasSpec = (section.rows || []).some(function (r) { return !!(r.spec || '').trim(); });
    return { name: h.name, spec: hasSpec ? h.spec : null };
  }

  function cellValue(row, col, multi) {
    if (!multi) {
      var p0 = (row.prices || [])[0];
      return (p0 && p0[col.k]) ? p0[col.k].raw : '';
    }
    var p = (row.prices || []).filter(function (x) { return x.area === col.area; })[0];
    return (p && p[col.k]) ? p[col.k].raw : '';
  }

  /* ------------------------------------------------------------ 筛选 */
  function currentSections() {
    if (!data) return [];
    return (data.sections || []).filter(function (s) {
      if (state.region && state.region !== 'ALL' && s.region !== state.region) return false;
      if (state.kind !== 'all' && s.kind !== state.kind) return false;
      return true;
    });
  }

  function filterRows(section) {
    if (!state.q) return section.rows;
    var q = state.q.toLowerCase();
    return section.rows.filter(function (r) {
      return ((r.name || '') + ' ' + (r.spec || '') + ' ' + (r.note || '')).toLowerCase().indexOf(q) >= 0;
    });
  }

  /* ------------------------------------------------------------ 渲染 */
  function renderChips() {
    // 地区
    var regions = state.issueKey && data ? uniq((data.sections || []).map(function (s) { return s.region; })) : [];
    var rc = el('regionChips');
    rc.innerHTML = '';
    var allChip = document.createElement('div');
    allChip.className = 'chip' + (state.region === 'ALL' ? ' on' : '');
    allChip.innerHTML = '全部地区<span class="n">' + regions.length + '</span>';
    allChip.onclick = function () { state.region = 'ALL'; render(); };
    rc.appendChild(allChip);

    regions.forEach(function (rg) {
      var n = (data.sections || []).filter(function (s) { return s.region === rg; })
        .reduce(function (a, s) { return a + (s.rows ? s.rows.length : 0); }, 0);
      var c = document.createElement('div');
      c.className = 'chip' + (state.region === rg ? ' on' : '');
      c.innerHTML = esc(rg) + '<span class="n">' + n + '</span>';
      c.onclick = function () { state.region = rg; render(); };
      rc.appendChild(c);
    });

    // 类型
    var kinds = uniq((data.sections || []).map(function (s) { return s.kind; }))
      .sort(function (a, b) { return KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b); });
    var kc = el('kindChips');
    kc.innerHTML = '';
    var kAll = document.createElement('div');
    kAll.className = 'chip' + (state.kind === 'all' ? ' on' : '');
    kAll.textContent = '全部';
    kAll.onclick = function () { state.kind = 'all'; render(); };
    kc.appendChild(kAll);
    kinds.forEach(function (k) {
      var n = (data.sections || []).filter(function (s) { return s.kind === k; })
        .reduce(function (a, s) { return a + s.rows.length; }, 0);
      var c = document.createElement('div');
      c.className = 'chip' + (state.kind === k ? ' on' : '');
      c.innerHTML = esc(KIND_LABEL[k] || k) + '<span class="n">' + n + '</span>';
      c.onclick = function () { state.kind = k; render(); };
      kc.appendChild(c);
    });
  }

  function renderSection(section) {
    var rows = filterRows(section);
    var cols = buildColumns(section, state.mode);
    var multi = cols.length && cols[0].area !== undefined;
    var meta = columnMeta(section);

    var box = document.createElement('section');
    box.className = 'section';

    var head = document.createElement('div');
    head.className = 'section-head';
    head.innerHTML =
      '<h2>' + esc(section.region + ' · ' + (KIND_LABEL[section.kind] || section.label)) + '</h2>' +
      '<span class="badge">第 ' + (section.pages ? section.pages[0] : '?') +
      (section.pages && section.pages[1] !== section.pages[0] ? '-' + section.pages[1] : '') + ' 页</span>' +
      '<span class="meta">显示 ' + rows.length + ' / ' + section.rows.length + ' 条' +
      (cols.length > 3 ? '　列：' + cols.length + ' 个地区价格' : '') + '</span>';
    box.appendChild(head);

    var wrap = document.createElement('div');
    wrap.className = 'tablewrap';

    var html = ['<table><thead><tr>',
      '<th class="idx">序号</th>',
      '<th>' + esc(meta.name) + '</th>'];
    if (meta.spec) html.push('<th>' + esc(meta.spec) + '</th>');
    html.push('<th>单位</th>');
    cols.forEach(function (c) { html.push('<th class="num">' + esc(c.label) + '</th>'); });
    html.push('<th>备注</th></tr></thead><tbody>');

    var baseCols = 3 + (meta.spec ? 1 : 0) + 1;   // 序号 + 名称 + (规格) + 单位 + 备注

    var lastGroup = null, counter = 0;
    rows.forEach(function (r) {
      if (lastGroup !== r.group) {
        lastGroup = r.group;
        if (r.group) {
          html.push('<tr class="group-row' + (state.collapse ? ' collapsed' : '') + '" data-group="1">' +
            '<td colspan="' + (baseCols + cols.length) + '"><span class="caret">▾</span>' + esc(r.group) + '</td></tr>');
        }
      }
      counter++;
      html.push('<tr>');
      html.push('<td class="idx">' + counter + '</td>');
      html.push('<td class="name">' + hi(r.name) + '</td>');
      if (meta.spec) html.push('<td class="spec">' + hi(r.spec) + '</td>');
      html.push('<td class="unit">' + esc(r.unit) + '</td>');
      cols.forEach(function (c) {
        var v = cellValue(r, c, multi);
        html.push('<td class="num">' + (v ? esc(v) : '<span class="price-empty">—</span>') + '</td>');
      });
      html.push('<td class="note">' + hi(r.note) + '</td>');
      html.push('</tr>');
    });
    html.push('</tbody></table>');
    wrap.innerHTML = html.join('');
    box.appendChild(wrap);

    if (section.notes && section.notes.length) {
      var n = document.createElement('div');
      n.style.cssText = 'padding:9px 16px;font-size:11.5px;color:var(--txt-3);border-top:1px dashed var(--line)';
      n.innerHTML = section.notes.map(function (t) { return '※ ' + esc(t); }).join('<br>');
      box.appendChild(n);
    }

    // 分组折叠交互
    wrap.addEventListener('click', function (e) {
      var tr = e.target.closest ? e.target.closest('tr.group-row') : null;
      if (!tr) return;
      tr.classList.toggle('collapsed');
      var hide = tr.classList.contains('collapsed');
      var node = tr.nextElementSibling;
      while (node && !node.classList.contains('group-row')) {
        node.style.display = hide ? 'none' : '';
        node = node.nextElementSibling;
      }
    });

    return { node: box, shown: rows.length, total: section.rows.length, cols: cols };
  }

  /* -------------------------------------------------------- URL 深链接 */
  // 支持 #r=海东地区&k=material&m=excl&q=钢筋 这样的可分享/可收藏地址
  function applyHash() {
    var h = (location.hash || '').replace(/^#/, '');
    if (!h) return;
    h.split('&').forEach(function (kv) {
      var p = kv.split('=');
      if (p.length !== 2) return;
      var v = decodeURIComponent(p[1]);
      if (p[0] === 'r') state.region = v;
      else if (p[0] === 'k') state.kind = v;
      else if (p[0] === 'm' && ['incl', 'excl', 'both'].indexOf(v) >= 0) state.mode = v;
      else if (p[0] === 'q') { state.q = v; var i = el('searchInput'); if (i) i.value = v; }
    });
  }
  function syncHash() {
    if (/v=trend/.test(location.hash)) return;   // 趋势视图有自己的 hash，不覆盖
    var h = '#r=' + encodeURIComponent(state.region) +
            '&k=' + encodeURIComponent(state.kind) + '&m=' + state.mode;
    if (state.q) h += '&q=' + encodeURIComponent(state.q);
    try { history.replaceState(null, '', h); } catch (e) { /* file:// 下忽略 */ }
  }

  function render() {
    if (!data) return;
    renderChips();

    var host = el('content');
    host.innerHTML = '';
    var sections = currentSections();
    var shown = 0, total = 0;

    if (!sections.length) {
      host.innerHTML = '<div class="placeholder">没有匹配的章节</div>';
    } else {
      sections.forEach(function (s) {
        var r = renderSection(s);
        host.appendChild(r.node);
        shown += r.shown; total += r.total;
      });
    }

    el('stat').innerHTML = '共 <b>' + shown + '</b> 条' +
      (shown !== total ? '（筛选前 ' + total + ' 条）' : '');

    el('footMeta').textContent = data.meta
      ? (data.meta.title + ' ' + data.meta.period + '（总第' + data.meta.issueNo + '期）　' +
        '发布：' + (data.meta.publishDate || '—') + '　' +
        '数据抽取时间：' + (data.meta.extractedAt || '—'))
      : '';
    syncHash();
  }

  /* ------------------------------------------------------------ 导出 CSV */
  function exportCSV() {
    var sections = currentSections();
    if (!sections.length) { alert('当前无数据可导出'); return; }
    var lines = [];
    var BOM = '\ufeff';
    sections.forEach(function (s) {
      var rows = filterRows(s);
      var cols = buildColumns(s, state.mode);
      var multi = cols.length && cols[0].area !== undefined;
      var meta = columnMeta(s);
      lines.push(['# ' + s.region + ' ' + (KIND_LABEL[s.kind] || s.label) +
        (s.pages ? '  p' + s.pages[0] + '-' + s.pages[1] : '')].join(','));
      var head = ['序号', meta.name].concat(meta.spec ? [meta.spec] : [], ['单位'],
        cols.map(function (c) { return c.label; }), ['备注']);
      lines.push(head.map(csvCell).join(','));
      var i = 0;
      rows.forEach(function (r) {
        i++;
        var arr = [i, r.name].concat(meta.spec ? [r.spec] : [], [r.unit],
          cols.map(function (c) { return cellValue(r, c, multi); }), [r.note]);
        lines.push(arr.map(csvCell).join(','));
      });
      lines.push('');
    });
    var blob = new Blob([BOM + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    var name = '信息价_' + (state.region || '全部') + '_' +
      (state.kind === 'all' ? '全部' : (KIND_LABEL[state.kind] || state.kind)) + '_' +
      (state.issueKey || '') + '.csv';
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }
  function csvCell(v) {
    var s = String(v == null ? '' : v);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  /* ------------------------------------------------------------ 初始化 */
  function initIssueSelect() {
    var sel = el('issueSelect');
    sel.innerHTML = '';
    if (!issues.length) {
      sel.innerHTML = '<option>无数据</option>';
      el('content').innerHTML = '<div class="placeholder">未找到期数索引 data/index.js，' +
        '请先运行抽取脚本生成数据。</div>';
      return false;
    }
    issues.forEach(function (it) {
      var o = document.createElement('option');
      o.value = it.key;
      o.textContent = it.label + '（' + it.items + ' 条）';
      sel.appendChild(o);
    });
    sel.value = issues[0].key;
    sel.onchange = function () { switchIssue(sel.value); };
    return true;
  }

  function switchIssue(key) {
    state.issueKey = key;
    el('content').innerHTML = '<div class="placeholder">正在加载 ' + esc(key) + ' …</div>';
    loadIssue(key, function (err) {
      if (err) {
        el('content').innerHTML = '<div class="placeholder">' + esc(err.message) + '</div>';
        return;
      }
      // 默认地区
      var regions = uniq((data.sections || []).map(function (s) { return s.region; }));
      if (regions.indexOf(state.region) < 0) state.region = regions[0] || 'ALL';
      applyHash();
      render();
    });
  }

  function bindUI() {
    var t = null;
    el('searchInput').addEventListener('input', function (e) {
      clearTimeout(t);
      var v = e.target.value.trim();
      t = setTimeout(function () { state.q = v; render(); }, 180);
    });
    el('modeSeg').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      state.mode = b.getAttribute('data-mode');
      [].forEach.call(el('modeSeg').children, function (x) { x.classList.toggle('on', x === b); });
      render();
    });
    el('collapseGroups').addEventListener('change', function (e) {
      state.collapse = e.target.checked;
      // 直接对现有分组行生效
      var rows = document.querySelectorAll('tr.group-row');
      [].forEach.call(rows, function (tr) {
        tr.classList.toggle('collapsed', state.collapse);
        var hide = state.collapse, node = tr.nextElementSibling;
        while (node && !node.classList.contains('group-row')) {
          node.style.display = hide ? 'none' : '';
          node = node.nextElementSibling;
        }
      });
    });
    el('btnExport').onclick = exportCSV;
    el('btnPrint').onclick = function () { window.print(); };
  }

  document.addEventListener('DOMContentLoaded', function () {
    bindUI();
    if (initIssueSelect()) switchIssue(issues[0].key);
  });
})();
