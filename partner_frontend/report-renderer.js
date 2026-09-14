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
    const keys = asArray(item.evidence_photos_detail).length
      ? ['evidence_photos_detail']
      : ['matched_photos_detail', 'photo_evidence', 'matched_photos'];
    keys.forEach(function (key) { asArray(item[key]).forEach(add); });
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

  function renderManualReview(item, taskId, options) {
    if (options.manualReview === false) return '';
    const review = item.manual_review;
    const saved = review && typeof review.is_ai_correct === 'boolean';
    const correct = saved && review.is_ai_correct === true;
    const incorrect = saved && review.is_ai_correct === false;
    const name = 'manual-review-' + taskId + '-' + item.item_no;
    return '<section class="manual-review" data-task-id="' + escapeHtml(taskId) + '" data-item-no="' + escapeHtml(item.item_no) + '">' +
      '<div class="manual-review-head"><b>人工复核：AI 审核结果是否正确？</b>' +
      (saved ? '<span>已复核</span>' : '') + '</div>' +
      '<div class="manual-review-options"><label><input type="radio" name="' + escapeHtml(name) + '" value="correct"' + (correct ? ' checked' : '') + '> AI 审核正确</label>' +
      '<label><input type="radio" name="' + escapeHtml(name) + '" value="incorrect"' + (incorrect ? ' checked' : '') + '> AI 审核错误</label></div>' +
      '<div class="manual-review-reason"' + (incorrect ? '' : ' hidden') + '><label>错误原因（必填）</label>' +
      '<textarea maxlength="1000" placeholder="请说明 AI 判断错误的地方，方便后续优化">' + escapeHtml(review && review.reason || '') + '</textarea></div>' +
      '<div class="manual-review-actions"><button type="button">' + (saved ? '更新复核' : '提交复核') + '</button><small></small></div></section>';
  }

  function bindManualReviews(container, options) {
    container.onchange = function (event) {
      const input = event.target.closest('.manual-review input[type="radio"]');
      if (!input) return;
      input.closest('.manual-review').querySelector('.manual-review-reason').hidden = input.value !== 'incorrect';
    };
    container.onclick = async function (event) {
      const button = event.target.closest('.manual-review-actions button');
      if (!button) return;
      const panel = button.closest('.manual-review');
      const selected = panel.querySelector('input[type="radio"]:checked');
      const textarea = panel.querySelector('textarea');
      const feedback = panel.querySelector('small');
      feedback.className = '';
      if (!selected) {
        feedback.textContent = '请选择审核结果';
        feedback.className = 'error';
        return;
      }
      const payload = {
        task_id: panel.dataset.taskId,
        item_no: Number(panel.dataset.itemNo),
        is_ai_correct: selected.value === 'correct',
        reason: selected.value === 'correct' ? null : textarea.value.trim()
      };
      if (!payload.is_ai_correct && !payload.reason) {
        feedback.textContent = '请填写错误原因';
        feedback.className = 'error';
        textarea.focus();
        return;
      }
      button.disabled = true;
      feedback.textContent = '正在保存…';
      try {
        if (typeof options.onReviewSubmit === 'function') {
          await options.onReviewSubmit(payload);
        } else if (typeof options.reviewUrlBuilder === 'function') {
          const response = await fetch(options.reviewUrlBuilder(payload.task_id, payload.item_no), {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_ai_correct: payload.is_ai_correct, reason: payload.reason })
          });
          if (!response.ok) throw new Error('人工复核接口返回 HTTP ' + response.status);
        } else {
          throw new Error('请配置 onReviewSubmit 或 reviewUrlBuilder');
        }
        let saved = panel.querySelector('.manual-review-head span');
        if (!saved) {
          saved = document.createElement('span');
          saved.textContent = '已复核';
          panel.querySelector('.manual-review-head').appendChild(saved);
        }
        button.textContent = '更新复核';
        feedback.textContent = '保存成功';
        feedback.className = 'success';
      } catch (error) {
        feedback.textContent = '保存失败：' + (error.message || String(error));
        feedback.className = 'error';
      } finally {
        button.disabled = false;
      }
    };
  }

  function renderItem(item, photos, options, taskId) {
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
      }).join('') + '</div>' : '<div class="empty">本项未匹配到证据照片</div>') + '</div>' +
      renderManualReview(item, taskId, options) + '</article>';
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
      items.map(function (item) { return renderItem(item, photos, options, report.task_id || ''); }).join('') + '</section>';
    bindManualReviews(container, options);
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
