/* NAS / Docker 版 SQLite 全库检索 */
(function () {
  'use strict';
  var ready = false;
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmt(v) {
    return v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  function message(text) { el('dbView').innerHTML = '<div class="placeholder">' + text + '</div>'; }

  async function search() {
    var q = el('dbInput').value.trim();
    if (!q) { message('请输入材料名称、规格或备注关键词。'); return; }
    el('dbStat').textContent = '查询中…';
    try {
      var r = await fetch('/api/search?q=' + encodeURIComponent(q) + '&limit=300');
      if (!r.ok || !(r.headers.get('content-type') || '').includes('application/json')) throw new Error('API unavailable');
      var d = await r.json();
      render(d.rows || []);
      el('dbStat').innerHTML = '找到 <b>' + (d.count || 0) + '</b> 条（最多显示 300 条）';
    } catch (e) {
      el('dbStat').textContent = '';
      message('当前是 GitHub Pages 静态版。数据库全文检索请使用 NAS / Docker 版；当期查询和历史趋势仍可正常使用。');
    }
  }
  function render(rows) {
    if (!rows.length) { message('没有匹配记录。'); return; }
    var html = '<section class="section"><div class="tablewrap db-results"><table><thead><tr>' +
      '<th>期次</th><th>地区</th><th>类型</th><th>名称</th><th>规格</th><th>单位</th>' +
      '<th>计价区域</th><th class="num">含税价</th><th class="num">除税价</th><th>备注</th>' +
      '</tr></thead><tbody>';
    rows.forEach(function (x) {
      html += '<tr><td>' + esc(x.issue_key) + '</td><td>' + esc(x.region) + '</td><td>' + esc(x.kind) +
        '</td><td class="name">' + esc(x.name) + '</td><td>' + esc(x.spec) + '</td><td>' + esc(x.unit) +
        '</td><td>' + esc(x.area) + '</td><td class="num">' + fmt(x.incl_num) +
        '</td><td class="num">' + fmt(x.excl_num) + '</td><td class="note">' + esc(x.note) + '</td></tr>';
    });
    el('dbView').innerHTML = html + '</tbody></table></div></section>';
  }
  function activate() {
    if (ready) return;
    ready = true;
    el('btnDbSearch').onclick = search;
    el('dbInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') search(); });
  }
  window.DatabaseView = { activate: activate };
})();
