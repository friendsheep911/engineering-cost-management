/* 项目材料调差：Excel 导入、自动匹配、计算与成果导出 */
(function () {
  'use strict';
  var ready = false;
  var options = { issues: [], regions: [], area_regions: {} };
  var projects = [];
  var selectedId = null;
  var apiBase = window.__PROJECT_API_BASE__ || '';
  if (!apiBase && location.port === '8088') {
    apiBase = location.protocol + '//' + location.hostname + ':8091';
  }

  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmt(value) {
    return value == null ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  function pct(value) { return value == null ? '—' : (value > 0 ? '+' : '') + Number(value).toFixed(2) + '%'; }
  function dateText(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '—'; }

  function apiUrl(path) { return apiBase + path; }
  async function api(url, config) {
    var response = await fetch(apiUrl(url), config);
    var type = response.headers.get('content-type') || '';
    var data = type.indexOf('application/json') >= 0 ? await response.json() : null;
    if (!response.ok) throw new Error((data && data.error) || '请求失败：' + response.status);
    return data;
  }

  function setStat(message, error) {
    el('projectStat').innerHTML = error ? '<span class="error-text">' + esc(message) + '</span>' : esc(message || '');
  }

  function optionHtml(rows, valueKey, labelFn, selected, blankLabel) {
    var html = blankLabel == null ? '' : '<option value="">' + esc(blankLabel) + '</option>';
    rows.forEach(function (row) {
      var value = typeof row === 'string' ? row : row[valueKey];
      var label = labelFn ? labelFn(row) : value;
      html += '<option value="' + esc(value) + '"' + (String(value) === String(selected) ? ' selected' : '') + '>' + esc(label) + '</option>';
    });
    return html;
  }

  function fillDialog() {
    var issues = options.issues;
    var issueLabel = function (x) { return x.issue_key + (x.publish_date ? ' · ' + x.publish_date : ''); };
    var defaultRegion = options.regions.indexOf('西宁地区') >= 0 ? '西宁地区' : (options.regions[0] || '');
    el('projectRegion').innerHTML = optionHtml(options.regions, null, null, defaultRegion, '全部地区（不建议）');
    fillAreaSelect(el('projectArea'), defaultRegion, '');
    el('projectRegion').onchange = function () { fillAreaSelect(el('projectArea'), this.value, ''); };
    el('projectCurrentIssue').innerHTML = optionHtml(issues, 'issue_key', issueLabel, issues[0] && issues[0].issue_key);
    el('projectBaseIssue').innerHTML = optionHtml(issues, 'issue_key', issueLabel, issues[1] && issues[1].issue_key);
  }

  function areasForRegion(region) {
    if (region && options.area_regions && options.area_regions[region]) return options.area_regions[region];
    var all = [], seen = {};
    Object.keys(options.area_regions || {}).forEach(function (key) {
      (options.area_regions[key] || []).forEach(function (area) { if (!seen[area]) { seen[area] = 1; all.push(area); } });
    });
    return all.sort();
  }
  function fillAreaSelect(select, region, selected) {
    select.innerHTML = optionHtml(areasForRegion(region), null, null, selected, '自动选择');
  }

  async function loadProjects(keepSelection) {
    var data = await api('/api/projects');
    projects = data.rows || [];
    el('projectCount').textContent = projects.length;
    renderList();
    if (keepSelection && selectedId && projects.some(function (x) { return x.id === selectedId; })) {
      await openProject(selectedId);
    } else if (projects.length) {
      await openProject(projects[0].id);
    } else {
      selectedId = null;
      renderEmpty();
    }
  }

  function renderList() {
    if (!projects.length) {
      el('projectList').innerHTML = '<div class="placeholder small">暂无项目</div>';
      return;
    }
    el('projectList').innerHTML = projects.map(function (p) {
      var progress = (p.matched_count || 0) + ' / ' + (p.item_count || 0);
      return '<button class="project-item' + (p.id === selectedId ? ' on' : '') + '" data-project-id="' + p.id + '">' +
        '<strong>' + esc(p.name) + '</strong><span>' + esc(p.region || '全部地区') + ' · ' + esc(p.current_issue) + '</span>' +
        '<small>已匹配 ' + progress + '　' + esc(dateText(p.updated_at)) + '</small></button>';
    }).join('');
  }

  function renderEmpty() {
    el('projectWorkspace').innerHTML = '<div class="empty-project"><h2>项目材料调差</h2>' +
      '<p>新建项目后导入 Excel 材料清单，系统会自动匹配基期与当前期信息价并计算调差。</p>' +
      '<button class="btn" data-new-project>新建第一个项目</button></div>';
  }

  async function openProject(projectId) {
    selectedId = Number(projectId);
    renderList();
    el('projectWorkspace').innerHTML = '<div class="placeholder">正在读取项目…</div>';
    try {
      renderProject(await api('/api/projects/' + selectedId));
    } catch (error) {
      el('projectWorkspace').innerHTML = '<div class="placeholder error-text">' + esc(error.message) + '</div>';
    }
  }

  function renderProject(data) {
    var p = data.project, s = data.summary, items = data.items || [];
    var issues = options.issues;
    var issueLabel = function (x) { return x.issue_key; };
    var statusClass = function (x) { return x === '已匹配' ? 'ok' : (x === '待复核' ? 'warn' : 'bad'); };
    var rows = items.map(function (x, i) {
      return '<tr><td class="idx">' + (i + 1) + '</td><td class="name">' + esc(x.name) + '</td><td>' + esc(x.spec) +
        '</td><td>' + esc(x.unit) + '</td><td class="num">' + fmt(x.quantity) + '</td>' +
        '<td><span class="match-status ' + statusClass(x.match_status) + '">' + esc(x.match_status || '未计算') + '</span></td>' +
        '<td class="name">' + esc(x.matched_name || '') + '</td><td>' + esc(x.matched_spec || '') + '</td>' +
        '<td>' + esc(x.matched_area || '') + '</td><td class="num">' + fmt(x.base_price) + '</td>' +
        '<td class="num">' + fmt(x.current_price) + '</td><td class="num">' + fmt(x.difference) + '</td>' +
        '<td class="num ' + (x.change_rate > 0 ? 'up' : (x.change_rate < 0 ? 'down' : '')) + '">' + pct(x.change_rate) + '</td>' +
        '<td class="num">' + fmt(x.match_score) + '</td><td class="risk-cell">' + esc(x.risk || '') + '</td></tr>';
    }).join('');
    el('projectWorkspace').innerHTML =
      '<div class="project-head"><div><h2>' + esc(p.name) + '</h2><p>创建：' + esc(dateText(p.created_at)) + '　更新：' + esc(dateText(p.updated_at)) +
      '</p></div><div class="project-actions"><button class="btn ghost danger" id="btnDeleteProject">删除项目</button>' +
      '<a class="btn" href="' + esc(apiUrl('/api/projects/' + p.id + '/export.xlsx')) + '">导出 Excel</a></div></div>' +
      '<div class="summary-cards"><div><span>清单行</span><strong>' + s.total + '</strong></div><div><span>已匹配</span><strong>' + s.matched +
      '</strong></div><div><span>待复核 / 未匹配</span><strong>' + s.review + ' / ' + s.unmatched + '</strong></div><div><span>基期合价</span><strong>' +
      fmt(s.base_amount) + '</strong></div><div><span>当前合价</span><strong>' + fmt(s.current_amount) + '</strong></div><div class="difference-card"><span>调差金额</span><strong>' +
      fmt(s.difference) + '</strong></div></div>' +
      '<section class="project-settings"><div class="settings-grid"><label><span>地区</span><select id="editRegion">' +
      optionHtml(options.regions, null, null, p.region, '全部地区') + '</select></label><label><span>计价区域</span><select id="editArea">' +
      optionHtml(areasForRegion(p.region), null, null, p.price_area, '自动选择') + '</select></label><label><span>基期</span><select id="editBaseIssue">' +
      optionHtml(issues, 'issue_key', issueLabel, p.base_issue) + '</select></label><label><span>当前期</span><select id="editCurrentIssue">' +
      optionHtml(issues, 'issue_key', issueLabel, p.current_issue) + '</select></label><label><span>价格口径</span><select id="editTaxMode"><option value="incl"' +
      (p.tax_mode === 'incl' ? ' selected' : '') + '>含税价</option><option value="excl"' + (p.tax_mode === 'excl' ? ' selected' : '') +
      '>除税价</option></select></label><label><span>风险阈值</span><div class="input-suffix"><input id="editThreshold" type="number" min="0" step="0.1" value="' +
      esc(p.threshold) + '"><b>%</b></div></label></div><button class="btn" id="btnCalculate">保存并重新计算</button></section>' +
      '<section class="import-box"><div><h3>导入 Excel 材料清单</h3><p>支持自动识别材料名称、规格、单位、数量和备注列。重新导入会替换本项目当前清单。</p></div>' +
      '<label class="file-button"><input id="projectFile" type="file" accept=".xlsx">选择 .xlsx 文件</label><button class="btn" id="btnImport" disabled>导入并匹配</button><span id="fileName"></span></section>' +
      '<section class="section project-table"><div class="section-head"><h2>材料匹配与调差明细</h2><span class="meta">风险项 ' + s.risk + ' 条</span></div><div class="tablewrap"><table><thead><tr>' +
      '<th>#</th><th>材料名称</th><th>规格</th><th>单位</th><th class="num">数量</th><th>状态</th><th>匹配材料</th><th>匹配规格</th><th>区域</th>' +
      '<th class="num">基期单价</th><th class="num">当前单价</th><th class="num">调差金额</th><th class="num">涨跌幅</th><th class="num">匹配度</th><th>风险提示</th>' +
      '</tr></thead><tbody>' + (rows || '<tr><td colspan="15" class="placeholder small">尚未导入材料清单</td></tr>') + '</tbody></table></div></section>';
    bindProjectActions(p.id);
  }

  function bindProjectActions(projectId) {
    el('editRegion').onchange = function () { fillAreaSelect(el('editArea'), this.value, ''); };
    var file = el('projectFile');
    file.onchange = function () {
      el('btnImport').disabled = !file.files.length;
      el('fileName').textContent = file.files.length ? file.files[0].name : '';
    };
    el('btnImport').onclick = async function () {
      if (!file.files.length) return;
      var form = new FormData();
      form.append('file', file.files[0]);
      this.disabled = true;
      setStat('正在导入并自动匹配…');
      try {
        var result = await api('/api/projects/' + projectId + '/import', { method: 'POST', body: form });
        renderProject(result);
        setStat('已导入 ' + result.import.count + ' 行并完成匹配');
        await refreshListOnly();
      } catch (error) {
        this.disabled = false;
        setStat(error.message, true);
      }
    };
    el('btnCalculate').onclick = async function () {
      this.disabled = true;
      setStat('正在重新计算…');
      var payload = {
        region: el('editRegion').value, price_area: el('editArea').value,
        base_issue: el('editBaseIssue').value, current_issue: el('editCurrentIssue').value,
        tax_mode: el('editTaxMode').value, threshold: Number(el('editThreshold').value || 0)
      };
      try {
        renderProject(await api('/api/projects/' + projectId + '/calculate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        }));
        setStat('计算完成');
        await refreshListOnly();
      } catch (error) {
        this.disabled = false;
        setStat(error.message, true);
      }
    };
    el('btnDeleteProject').onclick = async function () {
      if (!confirm('确认删除这个项目及其已导入清单？')) return;
      await api('/api/projects/' + projectId, { method: 'DELETE' });
      selectedId = null;
      setStat('项目已删除');
      await loadProjects(false);
    };
  }

  async function refreshListOnly() {
    projects = (await api('/api/projects')).rows || [];
    el('projectCount').textContent = projects.length;
    renderList();
  }

  function showDialog() {
    el('projectName').value = '';
    if (el('projectDialog').showModal) el('projectDialog').showModal();
    else el('projectDialog').setAttribute('open', '');
  }

  async function createProject(event) {
    event.preventDefault();
    var payload = {
      name: el('projectName').value.trim(), region: el('projectRegion').value,
      price_area: el('projectArea').value, base_issue: el('projectBaseIssue').value,
      current_issue: el('projectCurrentIssue').value, tax_mode: el('projectTaxMode').value,
      threshold: Number(el('projectThreshold').value || 5)
    };
    if (!payload.name) { el('projectName').focus(); return; }
    el('btnCreateProject').disabled = true;
    try {
      var result = await api('/api/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      });
      el('projectDialog').close();
      selectedId = result.project.id;
      await refreshListOnly();
      renderProject(result);
      setStat('项目已创建，可以导入清单');
    } catch (error) {
      setStat(error.message, true);
    } finally {
      el('btnCreateProject').disabled = false;
    }
  }

  async function activate() {
    if (ready) return;
    ready = true;
    el('projectTemplateLink').href = apiUrl('/api/projects/template.xlsx');
    el('btnNewProject').onclick = showDialog;
    el('btnRefreshProjects').onclick = function () { loadProjects(true).catch(function (e) { setStat(e.message, true); }); };
    el('projectForm').addEventListener('submit', createProject);
    el('projectList').onclick = function (event) {
      var item = event.target.closest('[data-project-id]');
      if (item) openProject(item.getAttribute('data-project-id'));
    };
    document.addEventListener('click', function (event) {
      if (event.target.closest('[data-new-project]')) showDialog();
    });
    try {
      options = await api('/api/project-options');
      fillDialog();
      await loadProjects(false);
      setStat('项目数据保存在 NAS 持久化目录');
    } catch (error) {
      el('projectWorkspace').innerHTML = '<div class="empty-project"><h2>项目调差需要 NAS / Docker 版</h2>' +
        '<p>当前页面无法连接项目数据库。请通过 NAS 地址打开系统；GitHub Pages 静态版仍可查询信息价与趋势。</p></div>';
      el('projectList').innerHTML = '<div class="placeholder small">服务未连接</div>';
      setStat(error.message, true);
    }
  }

  window.ProjectView = { activate: activate };
})();
