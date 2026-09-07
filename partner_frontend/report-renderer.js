(function (global) {
  'use strict';

  const STATUS = {
    verified: { icon: '✓', text: '已通过' },
    partial: { icon: '!', text: '部分通过' },
    missing: { icon: '×', text: '未通过' },
    unsupported: { icon: '—', text: '不支持' },
    overclaimed: { icon: '!', text: '疑似多报' }
  };
  const RECOMMENDATION = {
    VERIFIED: '全部通过',
    PARTIAL: '部分通过',
    MISSING: '证据不足'
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function absoluteUrl(url, baseUrl) {
    if (!url) return '';
    if (/^(https?:|data:|blob:)/i.test(url)) return url;
    if (!baseUrl) return url;
    return baseUrl.replace(/\/$/, '') + '/' + url.replace(/^\//, '');
  }

  function photoMap(report) {
    const map = new Map();
    asArray(report.photo_mappings).forEach(function (photo) { map.set(photo.photo_id, photo); });
    asArray(report.photos).forEach(function (photo) {
      map.set(photo.photo_id, Object.assign({}, map.get(photo.photo_id) || {}, photo));
    });
    return map;
  }

  function evidenceFor(item, photos) {
    const result = new Map();
    function add(entry) {
      const detail = typeof entry === 'string' ? { photo_id: entry } : (entry || {});
      const id = detail.photo_id;
      if (!id) return;
      const mapped = photos.get(id) || {};
      const previous = result.get(id) || {};
      result.set(id, Object.assign({}, mapped, previous, detail, {
        photo_id: id,
        photosId: detail.photosId != null ? detail.photosId : mapped.photosId,
        filename: detail.photo_name || detail.filename || mapped.filename || id,
        photo_url: detail.photo_url || mapped.photo_url || '',
        labels: asArray(detail.labels).length ? detail.labels : asArray(previous.labels)
      }));
    }
    ['matched_photos_detail', 'core_photos_detail', 'photo_evidence', 'reference_photos',
      'matched_photos', 'core_photos'].forEach(function (key) { asArray(item[key]).forEach(add); });
    return Array.from(result.values());
  }

  function renderPhoto(photo, options) {
    const imageUrl = absoluteUrl(photo.photo_url, options.imageBaseUrl || '');
    const clientId = photo.photosId || '未提供客户照片 ID';
    const confidence = typeof photo.confidence === 'number'
      ? '<div class="confidence">置信度 ' + Math.round(photo.confidence * 100) + '%</div>' : '';
    const labels = asArray(photo.labels);
    return '<article class="photo-card">' +
      (imageUrl
        ? '<img loading="lazy" src="' + escapeHtml(imageUrl) + '" alt="' + escapeHtml(photo.filename) + '">'
        : '<div class="photo-placeholder">暂无图片地址</div>') +
      '<div class="photo-name" title="' + escapeHtml(photo.filename) + '">' + escapeHtml(photo.filename) + '</div>' +
      '<div class="photo-id">客户 photosId：' + escapeHtml(clientId) + '</div>' +
      '<div class="photo-id">系统 photo_id：' + escapeHtml(photo.photo_id) + '</div>' +
      confidence +
      (labels.length ? '<div class="labels">' + labels.map(function (label) {
        return '<span class="label">' + escapeHtml(label) + '</span>';
      }).join('') + '</div>' : '') +
      '</article>';
  }

  function renderItem(item, photos, options) {
    const statusKey = item.verification_status || 'missing';
    const status = STATUS[statusKey] || { icon: '?', text: statusKey };
    const evidence = evidenceFor(item, photos);
    const matchText = item.strong_match
      ? '强匹配·' + (item.match_source === 'exact' ? '精确' : '模糊')
      : (item.match_source && item.match_source !== 'none' ? item.match_source : '—');
    const damage = [item.damage_code, item.damage_name].filter(Boolean).join(' · ') || '—';
    const note = item.auditor_notes || (item.strong_match ? '照片标记与清单位置匹配' : '暂无补充说明');
    return '<article class="verification-card status-' + escapeHtml(statusKey) + '">' +
      '<div class="item-head"><div><div class="item-title">Item ' + escapeHtml(item.item_no) +
      ' · ' + escapeHtml(item.component || '未识别') + ' · ' + escapeHtml(item.damage_code || '—') + '</div>' +
      '<div class="facts"><span><b>部件：</b>' + escapeHtml(item.component_name || item.component || '—') + '</span>' +
      '<span><b>损伤：</b>' + escapeHtml(damage) + '</span>' +
      '<span><b>位置：</b>' + escapeHtml(item.location_code || '—') + '</span>' +
      '<span><b>匹配：</b>' + escapeHtml(matchText) + '</span></div></div>' +
      '<div class="result-badge">' + escapeHtml(status.icon + ' ' + status.text) + '</div></div>' +
      '<div class="item-text"><div class="text-row"><span class="text-label">维修说明</span><span>' + escapeHtml(item.description || '—') + '</span></div>' +
      '<div class="text-row"><span class="text-label">鉴定依据</span><span>' + escapeHtml(note) + '</span></div></div>' +
      '<div class="evidence"><div class="evidence-title">证据照片（' + evidence.length + '）</div>' +
      (evidence.length ? '<div class="photos">' + evidence.map(function (photo) {
        return renderPhoto(photo, options);
      }).join('') + '</div>' : '<div class="empty">本项未匹配到证据照片</div>') + '</div></article>';
  }

  function render(container, report, options) {
    options = options || {};
    const summary = report.verification_summary || {};
    const items = asArray(report.item_verifications);
    const photos = photoMap(report);
    const recommendation = report.final_recommendation || '—';
    container.className = '';
    container.innerHTML = '<header class="report-head"><div><h1>集装箱维修稽核报告</h1>' +
      '<div class="report-meta"><span>集装箱号：' + escapeHtml(report.container_number || '未识别') + '</span>' +
      '<span>Task ID：' + escapeHtml(report.task_id || '—') + '</span><span>共 ' + items.length + ' 项</span></div></div>' +
      '<div class="overall-result"><span>总体结论</span><strong>' + escapeHtml(RECOMMENDATION[recommendation] || recommendation) + '</strong></div></header>' +
      '<section class="summary-grid">' +
      [['verified', '已通过'], ['partial', '部分通过'], ['missing', '未通过'], ['unsupported', '不支持'], ['overclaimed', '疑似多报']]
        .map(function (entry) { return '<div class="summary-card ' + entry[0] + '"><b>' + Number(summary[entry[0]] || 0) + '</b><span>' + entry[1] + '</span></div>'; }).join('') +
      '</section><h2 class="section-heading">逐项鉴定明细</h2><section class="verification-list">' +
      items.map(function (item) { return renderItem(item, photos, options); }).join('') + '</section>';
  }

  async function load(container, config, directReport) {
    try {
      if (directReport) return render(container, directReport, config || {});
      if (!config || !config.reportUrl) throw new Error('未配置 reportUrl');
      const response = await fetch(config.reportUrl, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('报告接口返回 HTTP ' + response.status);
      render(container, await response.json(), config);
    } catch (error) {
      container.className = 'error-state';
      container.textContent = '鉴定结果加载失败：' + (error && error.message ? error.message : String(error));
    }
  }

  global.AuditReportRenderer = { load: load, render: render };
})(window);
