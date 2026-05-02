(function() {
  'use strict';

  const API = '';

  // ===== DOM =====
  const $ = id => document.getElementById(id);
  const statusBadge = $('statusBadge');
  const statusText = $('statusText');
  const bugDesc = $('bugDesc');
  const logText = $('logText');
  const logDropZone = $('logDropZone');
  const bugDescDropZone = $('bugDescDropZone');
  const btnAnalyze = $('btnAnalyze');
  const analyzeIcon = $('analyzeIcon');
  const analyzeText = $('analyzeText');
  const analyzeStatus = $('analyzeStatus');
  const llmProviderText = $('llmProviderText');
  const llmModelText = $('llmModelText');
  const llmKeyIcon = $('llmKeyIcon');
  const llmKeyText = $('llmKeyText');
  const analyzeStatusText = $('analyzeStatusText');
  const stepsContainer = $('stepsContainer');
  const searchQuery = $('searchQuery');
  const btnSearch = $('btnSearch');
  const searchCard = $('searchCard');
  const searchCount = $('searchCount');
  const searchBody = $('searchBody');
  const analysisCard = $('analysisCard');
  const analysisContent = $('analysisContent');
  const analysisSplit = $('analysisSplit');
  const thinkingPanel = $('thinkingPanel');
  const thinkingPanelBody = $('thinkingPanelBody');
  const thinkingSpinner = $('thinkingSpinner');
  const searchActions = $('searchActions');
  const placeholderCard = $('placeholderCard');
  const btnSettings = $('btnSettings');
  const settingsModal = $('settingsModal');
  const btnCloseSettings = $('btnCloseSettings');
  const btnCancelSettings = $('btnCancelSettings');
  const btnSaveSettings = $('btnSaveSettings');
  const cfgProvider = $('cfgProvider');
  const cfgBaseUrl = $('cfgBaseUrl');
  const cfgApiKey = $('cfgApiKey');
  const apiKeyGroup = $('apiKeyGroup');
  const apiKeyHint = $('apiKeyHint');
  const cfgModel = $('cfgModel');
  const cfgMaxTokens = $('cfgMaxTokens');
  const cfgTimeout = $('cfgTimeout');
  const modelList = $('modelList');
  const presetBtns = $('presetBtns');

  let currentController = null;
  let isAnalyzing = false;
  let analysisRawText = '';
  let pipelineStartTime = 0;
  let pipelineTimerInterval = null;
  let _contentScrollRaf = null;

  const MAX_TOASTS = 5;

  // ===== RIPPLE EFFECT =====
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.btn');
    if (!btn || btn.disabled) return;
    var rect = btn.getBoundingClientRect();
    var ripple = document.createElement('span');
    ripple.className = 'ripple';
    var size = Math.max(rect.width, rect.height);
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
    ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
    btn.appendChild(ripple);
    setTimeout(function() { ripple.remove(); }, 600);
  });

  // ===== TOAST =====
  function showToast(msg, type) {
    type = type || 'info';
    var container = $('toastContainer');
    // Limit toast count
    while (container.children.length >= MAX_TOASTS) {
      container.firstChild.remove();
    }
    var t = document.createElement('div');
    t.className = 'toast ' + type;
    t.setAttribute('role', 'alert');
    t.setAttribute('aria-live', 'polite');
    t.textContent = msg;
    container.appendChild(t);
    setTimeout(function() {
      t.classList.add('removing');
      setTimeout(function() { t.remove(); }, 300);
    }, 3000);
  }

  // ===== KEYBOARD SHORTCUTS =====
  document.addEventListener('keydown', function(e) {
    // Ctrl+Enter or Cmd+Enter: analyze
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (!isAnalyzing) {
        btnAnalyze.click();
      }
    }
    // Ctrl+K or Cmd+K: focus search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      searchQuery.focus();
    }
    // Escape: close modal
    if (e.key === 'Escape') {
      if (settingsModal.classList.contains('open')) {
        closeSettings();
      }
    }
  });

  // ===== PIPELINE PROGRESS HELPERS =====
  function startPipelineTimer() {
    pipelineStartTime = Date.now();
    $('pipelineTimer').textContent = '';
    pipelineTimerInterval = setInterval(function() {
      var elapsed = Math.floor((Date.now() - pipelineStartTime) / 1000);
      var min = Math.floor(elapsed / 60);
      var sec = elapsed % 60;
      $('pipelineTimer').innerHTML = '⏱ <span>' + (min > 0 ? min + 'm ' : '') + sec + 's</span>';
    }, 1000);
  }
  function stopPipelineTimer() {
    if (pipelineTimerInterval) {
      clearInterval(pipelineTimerInterval);
      pipelineTimerInterval = null;
    }
  }
  function resetPipelineProgress() {
    for (var i = 0; i < 5; i++) {
      var el = $('pStep' + i);
      el.className = 'pipeline-step';
    }
    stopPipelineTimer();
    $('pipelineTimer').textContent = '';
  }
  function setPipelineStep(stepNum, state) {
    var el = $('pStep' + stepNum);
    if (!el) return;
    el.className = 'pipeline-step ' + state;
    // Update aria
    el.setAttribute('aria-label', el.querySelector('.step-label').textContent + ' - ' + state);
    if (state === 'active') {
      for (var i = 0; i < stepNum; i++) {
        var prev = $('pStep' + i);
        if (prev && !prev.classList.contains('done')) {
          prev.className = 'pipeline-step done';
        }
      }
    }
  }

  // ===== HEALTH CHECK =====
  async function checkHealth() {
    try {
      const res = await fetch(API + '/api/health');
      if (res.ok) {
        const data = await res.json();
        statusBadge.className = 'status-badge online';
        statusBadge.setAttribute('aria-label', '後端狀態：線上');
        statusText.textContent = '線上';
        if (data.version) {
          const el = $('appVersion');
          if (el) el.textContent = data.version;
        }
        return true;
      }
    } catch(e) {}
    statusBadge.className = 'status-badge offline';
    statusBadge.setAttribute('aria-label', '後端狀態：離線');
    statusText.textContent = '離線';
    return false;
  }

  // ===== DRAG & DROP =====
  function setupDropZone(zone, textarea) {
    ['dragenter','dragover'].forEach(function(evt) {
      zone.addEventListener(evt, function(e) { e.preventDefault(); zone.classList.add('dragover'); });
    });
    ['dragleave','drop'].forEach(function(evt) {
      zone.addEventListener(evt, function(e) { e.preventDefault(); zone.classList.remove('dragover'); });
    });
    zone.addEventListener('drop', function(e) {
      var file = e.dataTransfer.files[0];
      if (file) {
        var reader = new FileReader();
        reader.onload = function(ev) { textarea.value = ev.target.result; };
        reader.readAsText(file);
        showToast('已載入：' + file.name, 'success');
      }
    });
  }
  setupDropZone(logDropZone, logText);
  setupDropZone(bugDescDropZone, bugDesc);

  // Restore top_k preference from localStorage
  var savedTopK = localStorage.getItem('bugDetective_topK');
  if (savedTopK && $('topKSelect').querySelector('option[value="' + savedTopK + '"]')) {
    $('topKSelect').value = savedTopK;
  }

  // Restore batch_size preference from localStorage
  var savedBatchSize = localStorage.getItem('bugDetective_batchSize');
  if (savedBatchSize && $('batchSizeSelect').querySelector('option[value="' + savedBatchSize + '"]')) {
    $('batchSizeSelect').value = savedBatchSize;
  }

  // Restore font size from localStorage
  var savedFontSize = localStorage.getItem('bugDetective_fontSize');
  if (savedFontSize) {
    document.documentElement.style.fontSize = savedFontSize + 'px';
    var slider = $('cfgFontSize');
    if (slider) { slider.value = savedFontSize; }
    var label = $('fontSizeValue');
    if (label) { label.textContent = savedFontSize + 'px'; }
  }

  // Restore keyword limit from localStorage
  var savedKeywordLimit = localStorage.getItem('bugDetective_keywordLimit');
  if (savedKeywordLimit) {
    var klSlider = $('cfgKeywordLimit');
    if (klSlider) { klSlider.value = savedKeywordLimit; }
    var klLabel = $('keywordLimitValue');
    if (klLabel) { klLabel.textContent = savedKeywordLimit; }
  }

  // Restore temperature from localStorage
  var savedTemperature = localStorage.getItem('bugDetective_temperature');
  if (savedTemperature) {
    var tempSlider = $('cfgTemperature');
    if (tempSlider) { tempSlider.value = savedTemperature; }
    var tempLabel = $('temperatureValue');
    if (tempLabel) { tempLabel.textContent = savedTemperature; }
  }

  // Restore theme from localStorage
  var savedTheme = localStorage.getItem('bugDetective_theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
  function highlightThemeBtn(theme) {
    document.querySelectorAll('#themeBtns .preset-btn').forEach(function(btn) {
      btn.classList.toggle('active', btn.getAttribute('data-theme') === theme);
    });
  }
  highlightThemeBtn(savedTheme);

  // Theme button click — instant preview
  document.querySelectorAll('#themeBtns .preset-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var theme = this.getAttribute('data-theme');
      document.documentElement.setAttribute('data-theme', theme);
      highlightThemeBtn(theme);
    });
  });

  // Font size slider live preview
  var fontSizeSlider = $('cfgFontSize');
  if (fontSizeSlider) {
    fontSizeSlider.addEventListener('input', function() {
      var size = this.value;
      document.documentElement.style.fontSize = size + 'px';
      var label = $('fontSizeValue');
      if (label) { label.textContent = size + 'px'; }
    });
  }

  // Keyword limit slider live preview
  var keywordLimitSlider = $('cfgKeywordLimit');
  if (keywordLimitSlider) {
    keywordLimitSlider.addEventListener('input', function() {
      var klLabel = $('keywordLimitValue');
      if (klLabel) { klLabel.textContent = this.value; }
    });
  }

  // Temperature slider live preview
  var temperatureSlider = $('cfgTemperature');
  if (temperatureSlider) {
    temperatureSlider.addEventListener('input', function() {
      var tempLabel = $('temperatureValue');
      if (tempLabel) { tempLabel.textContent = this.value; }
    });
  }

  // ===== SKELETON HELPERS =====
  function showSearchSkeleton() {
    searchCard.classList.remove('hidden');
    searchCard.classList.add('fade-in');
    searchActions.style.display = 'none';
    searchCount.textContent = '';
    searchBody.innerHTML = '';
    for (var i = 0; i < 5; i++) {
      var row = document.createElement('div');
      row.className = 'skeleton-search-row';
      row.innerHTML =
        '<div class="skeleton" style="width:28px;height:28px;border-radius:50%;flex-shrink:0;"></div>' +
        '<div style="flex:1;display:flex;flex-direction:column;gap:6px;">' +
        '<div class="skeleton-search-bar" style="width:' + (60 + Math.random()*30) + '%;height:12px;"></div>' +
        '<div class="skeleton-search-bar" style="width:' + (30 + Math.random()*40) + '%;height:8px;"></div>' +
        '</div>';
      searchBody.appendChild(row);
    }
  }
  function showAnalysisSkeleton() {
    analysisContent.innerHTML = '';
    for (var i = 0; i < 6; i++) {
      var line = document.createElement('div');
      line.className = 'skeleton';
      line.style.height = '14px';
      line.style.width = (50 + Math.random() * 45) + '%';
      line.style.marginBottom = '8px';
      analysisContent.appendChild(line);
    }
  }

  // API key in sessionStorage only (cleared when browser tab closes)
  const API_KEY_STORAGE='bugDetectiveApiKey';
  let _apiKey = sessionStorage.getItem(API_KEY_STORAGE) || '';

  // Update LLM status bar (provider, model, API key)
  function updateApiKeyStatus() {
    var key = _apiKey || (cfgApiKey && cfgApiKey.value.trim()) || '';
    var provider = cfgProvider ? cfgProvider.value : '';
    var model = cfgModel ? cfgModel.value : '';

    var providerLabels = { glm5: 'z.ai (GLM-5)', openrouter: 'OpenRouter', minimax: 'MiniMax', deepseek: 'DeepSeek', ollama: 'Ollama (Local)' };
    llmProviderText.textContent = providerLabels[provider] || provider || '--';
    llmModelText.textContent = model || '--';

    if (provider === 'ollama') {
      llmKeyIcon.textContent = '⚪';
      llmKeyText.textContent = '不需要 API Key';
      llmKeyText.style.color = 'var(--text-muted)';
    } else if (key) {
      llmKeyIcon.textContent = '🟢';
      llmKeyText.textContent = 'API Key 已設定';
      llmKeyText.style.color = 'var(--green)';
    } else {
      llmKeyIcon.textContent = '🔴';
      llmKeyText.textContent = 'API Key 未設定';
      llmKeyText.style.color = 'var(--red)';
    }
  }

  // ===== ANALYZE (SSE) =====
  btnAnalyze.addEventListener('click', startAnalyze);

  async function startAnalyze() {
    if (isAnalyzing) {
      cancelAnalyze();
      return;
    }
    var desc = bugDesc.value.trim();
    var log = logText.value.trim();
    if (!desc && !log) {
      showToast('請輸入 Bug 描述或日誌內容', 'error');
      bugDesc.focus();
      return;
    }
    var effectiveKey = _apiKey || (cfgApiKey && cfgApiKey.value.trim()) || '';
    var currentProvider = cfgProvider ? cfgProvider.value : '';
    if (currentProvider !== 'ollama' && !effectiveKey) {
      showToast('API Key 未設定！請點擊設定按鈕填入 API Key', 'error');
      openSettings();
      return;
    }
    if (!(await checkHealth())) {
      showToast('後端服務離線，請確認已啟動', 'error');
      return;
    }
    isAnalyzing = true;
    analyzeIcon.innerHTML = '<span class="spinner"></span>';
    analyzeText.textContent = '取消分析';
    analyzeStatus.classList.remove('hidden');
    analyzeStatusText.textContent = '正在分析中...';

    resetPipelineProgress();
    startPipelineTimer();
    stepsContainer.innerHTML = '';
    searchCard.classList.add('hidden');
    searchActions.style.display = 'none';
    analysisCard.classList.remove('hidden');
    analysisCard.classList.add('fade-in');
    analysisRawText = '';
    _contentScrollRaf = null;
    analysisSplit.classList.add('thinking-only');
    thinkingPanel.classList.add('hidden');
    thinkingPanelBody.textContent = '';
    thinkingPanelBody._thinkNode = null;
    thinkingSpinner.style.display = 'none';
    placeholderCard.classList.add('hidden');

    // Show skeleton
    showAnalysisSkeleton();

    currentController = new AbortController();

    try {
      var res = await fetch(API + '/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bug_description: desc, log_text: log, api_key: _apiKey || (cfgApiKey && cfgApiKey.value.trim()) || '', top_k: parseInt($('topKSelect').value) || 100, batch_size: parseInt($('batchSizeSelect').value) || 20, keyword_limit: parseInt($('cfgKeywordLimit').value) || 50, temperature: parseFloat($('cfgTemperature').value) || 0.3, max_tokens: parseInt(cfgMaxTokens.value) || 0, timeout: parseInt(cfgTimeout.value) || 0 }),
        signal: currentController.signal
      });

      localStorage.setItem('bugDetective_topK', $('topKSelect').value);
      localStorage.setItem('bugDetective_batchSize', $('batchSizeSelect').value);
      localStorage.setItem('bugDetective_fontSize', $('cfgFontSize').value);
      localStorage.setItem('bugDetective_keywordLimit', $('cfgKeywordLimit').value);
      localStorage.setItem('bugDetective_temperature', $('cfgTemperature').value);
      var activeTheme = document.documentElement.getAttribute('data-theme') || 'dark';
      localStorage.setItem('bugDetective_theme', activeTheme);

      if (!res.ok) {
        var errBody = '';
        try { errBody = await res.text(); } catch(e) {}
        throw new Error('HTTP ' + res.status + (errBody ? ': ' + errBody.slice(0, 200) : ''));
      }

      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        buffer += decoder.decode(result.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop();

        for (var i = 0; i < lines.length; i++) {
          if (!lines[i].trim()) continue;
          try {
            var evt = JSON.parse(lines[i]);
            handleSSEEvent(evt);
          } catch(e) {}
        }
      }

      if (buffer.trim()) {
        try {
          var evt = JSON.parse(buffer);
          handleSSEEvent(evt);
        } catch(e) {}
      }

    } catch(e) {
      if (e.name === 'AbortError') {
        showToast('已取消分析', 'info');
      } else {
        showToast('分析失敗：' + e.message, 'error');
        analysisContent.innerHTML = '<div class="result-placeholder">分析失敗：' + escapeHtml(e.message) + '</div>';
      }
    } finally {
      isAnalyzing = false;
      currentController = null;
      analyzeIcon.textContent = '🔍';
      analyzeText.textContent = '開始分析';
      analyzeStatus.classList.add('hidden');
      stopPipelineTimer();
      analysisContent.classList.remove('streaming-cursor');
    }
  }

  // Helper: create collapsible block (using CSS classes, not inline styles)
  function makeCollapsibleBlock(label, content) {
    if (!content || (Array.isArray(content) && content.length === 0)) return null;
    var wrap = document.createElement('div');
    wrap.className = 'collapsible-block';
    var toggle = document.createElement('div');
    toggle.className = 'collapsible-toggle';
    toggle.setAttribute('role', 'button');
    toggle.setAttribute('tabindex', '0');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = '<span>' + label + '</span><span class="collapsible-toggle-arrow">▶</span>';
    var box = document.createElement('pre');
    box.className = 'collapsible-body';
    box.textContent = Array.isArray(content) ? content.join('\n') : String(content);
    function doToggle() {
      var open = box.style.display === 'none' || box.style.display === '';
      box.style.display = open ? 'block' : 'none';
      toggle.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', String(open));
    }
    toggle.addEventListener('click', doToggle);
    toggle.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); doToggle(); }
    });
    wrap.appendChild(toggle);
    wrap.appendChild(box);
    return wrap;
  }

  function handleSSEEvent(evt) {
    switch (evt.type) {
      case 'status':
        analyzeStatusText.textContent = evt.text || '處理中...';
        var txt = (evt.text || '');
        if (txt.includes('Step 0') && txt.includes('去重')) setPipelineStep(0, 'active');
        else if (txt.includes('Step 1') || txt.includes('Drain') || txt.includes('Regex 萃取')) setPipelineStep(1, 'active');
        else if (txt.includes('Step 2') || txt.includes('統合')) setPipelineStep(2, 'active');
        else if (txt.includes('Step 3') || txt.includes('Hybrid') || txt.includes('搜尋')) setPipelineStep(3, 'active');
        else if (txt.includes('Step 4') || txt.includes('深度分析') || txt.includes('LLM')) setPipelineStep(4, 'active');
        break;
      case 'pipeline_step':
        setPipelineStep(evt.step, evt.state);
        if (evt.detail && evt.state === 'done') {
          analyzeStatusText.textContent += ' [' + evt.detail + ']';
        }
        break;
      case 'step0_result':
        setPipelineStep(0, 'done');
        setPipelineStep(1, 'active');
        var d = evt.data;
        var badge0 = document.createElement('div');
        badge0.className = 'step-badge step-badge-0';
        badge0.innerHTML = '📝 <b>Step 0</b> — Log 去重：' +
          d.original_lines.toLocaleString() + ' → ' + d.condensed_lines.toLocaleString() +
          ' 行（縮減 <b style="color:var(--green);">' + d.reduction_pct + '%</b>）';
        stepsContainer.appendChild(badge0);
        var blk0 = makeCollapsibleBlock('📄 去重後的 Log 內容', d.condensed_log);
        if (blk0) stepsContainer.appendChild(blk0);
        break;
      case 'step1_result':
        setPipelineStep(1, 'done');
        setPipelineStep(2, 'active');
        var s1 = evt.data;
        var badge1 = document.createElement('div');
        badge1.className = 'step-badge step-badge-1';

        // Drain3 + Regex pipeline
        var drain = s1.drain || {};
        var regex = s1.regex || {};
        var drainAnomalies = drain.anomalies || [];

        badge1.innerHTML = '🧠 <b>Step 1</b> — Drain3 Log 解析：' +
          (drain.total_clusters || 0) + ' 模板' +
          ' / ' + (drain.total_lines || 0) + ' 行' +
          (drainAnomalies.length ? ' / <span style="color:var(--orange)">⚠️ ' + drainAnomalies.length + ' 個異常</span>' : '');
        stepsContainer.appendChild(badge1);

        // Anomaly summary block
        if (drainAnomalies.length) {
          var anomalyParts = drainAnomalies.map(function(a) {
            return '⚠️ <b>' + a.template + '</b>（出現 ' + a.size + ' 次）— ' + (a.reason || '');
          });
          var anomalyHtml = '<div style="padding:8px 12px;margin:6px 0;border-radius:var(--radius-sm);background:var(--orange-bg);color:var(--orange);font-size:0.9rem;">' +
            '<b>🔍 異常模板偵測</b>（罕見的錯誤/警告模板，可能是 Bug 關鍵線索）<br>' +
            anomalyParts.join('<br>') + '</div>';
          var tmpDiv = document.createElement('div');
          tmpDiv.innerHTML = anomalyHtml;
          stepsContainer.appendChild(tmpDiv.firstElementChild || tmpDiv);
        }

        // Collapsible: Drain cluster details
        var drainParts = [];
        var clusters = drain.clusters || [];
        for (var ci = 0; ci < clusters.length; ci++) {
          var c = clusters[ci];
          var lines = [];
          var isAnomaly = drainAnomalies.some(function(a) { return a.template === c.template; });
          lines.push((isAnomaly ? '⚠️ ' : '') + '── 模板 ' + (ci + 1) + '（出現 ' + c.size + ' 次）' + (isAnomaly ? ' ⚠️ 異常' : '') + ' ──');
          lines.push('  ' + c.template);
          if (c.sample_lines && c.sample_lines.length) {
            lines.push('  範例：' + c.sample_lines.slice(0, 2).join('\n         '));
          }
          drainParts.push(lines.join('\n'));
        }
        if (drainParts.length) {
          var blk1 = makeCollapsibleBlock('🧠 Step 1 Drain 模板明細（前 ' + clusters.length + ' 個）', drainParts.join('\n\n'));
          if (blk1) stepsContainer.appendChild(blk1);
        }

        // Collapsible: Regex extraction details
        var regexParts = [];
        if (regex.error_codes && regex.error_codes.length)
          regexParts.push('🔴 錯誤碼（' + regex.error_codes.length + '）：\n' + regex.error_codes.join('\n'));
        if (regex.function_names && regex.function_names.length)
          regexParts.push('🔧 函式名稱（' + regex.function_names.length + '）：\n' + regex.function_names.join('\n'));
        if (regex.file_paths && regex.file_paths.length)
          regexParts.push('📁 檔案路徑（' + regex.file_paths.length + '）：\n' + regex.file_paths.join('\n'));
        if (regex.exceptions && regex.exceptions.length)
          regexParts.push('⚠️ 異常/信號（' + regex.exceptions.length + '）：\n' + regex.exceptions.join('\n'));
        if (regex.memory_addresses && regex.memory_addresses.length)
          regexParts.push('💾 記憶體位址（' + regex.memory_addresses.length + '）：\n' + regex.memory_addresses.join('\n'));
        if (regexParts.length) {
          var blk1r = makeCollapsibleBlock('🔧 Step 1 Regex 萃取結果', regexParts.join('\n\n'));
          if (blk1r) stepsContainer.appendChild(blk1r);
        }
        break;
      case 'step2_result':
        setPipelineStep(2, 'done');
        setPipelineStep(3, 'active');
        var s2 = evt.data;
        var badge2 = document.createElement('div');
        badge2.className = 'step-badge step-badge-2';
        badge2.innerHTML = '🔀 <b>Step 2</b> — 統合關鍵字：精確 ' + (s2.exact||[]).length +
          ' / 語意 ' + (s2.semantic||[]).length;
        stepsContainer.appendChild(badge2);
        var parts2 = [];
        if (s2.summary)
          parts2.push('📝 摘要：\n' + s2.summary);
        if (s2.exact && s2.exact.length)
          parts2.push('🔵 精確關鍵字（' + s2.exact.length + '）：\n' + s2.exact.map(function(k,i){return '  '+(i+1)+'. '+k;}).join('\n'));
        if (s2.semantic && s2.semantic.length)
          parts2.push('🟣 語意關鍵字（' + s2.semantic.length + '）：\n' + s2.semantic.map(function(k,i){return '  '+(i+1)+'. '+k;}).join('\n'));
        if (parts2.length) {
          var blk2 = makeCollapsibleBlock('🔀 Step 2 關鍵字提取結果', parts2.join('\n\n'));
          if (blk2) stepsContainer.appendChild(blk2);
        }
        break;
      case 'step3_result':
        setPipelineStep(3, 'done');
        setPipelineStep(4, 'active');
        // Remove skeleton, render real results
        renderSearchResults(evt.data.fused_results || []);
        if (evt.data.keyword_matches !== undefined) {
          searchCount.textContent =
            (evt.data.fused_results || []).length + ' 筆' +
            ' (Keyword: ' + evt.data.keyword_matches +
            ' / Vector: ' + evt.data.vector_matches + ')';
        }
        var badge3 = document.createElement('div');
        badge3.className = 'step-badge step-badge-3';
        badge3.innerHTML = '🔎 <b>Step 3</b> — 搜尋結果：Keyword ' + (evt.data.keyword_matches||0) +
          ' / Vector ' + (evt.data.vector_matches||0) +
          ' / RRF 融合 ' + (evt.data.fused_results||[]).length + ' 筆';
        stepsContainer.appendChild(badge3);
        break;
      case 'search_results':
        renderSearchResults(evt.data);
        break;
      case 'thinking':
        analysisSplit.classList.remove('thinking-only');
        thinkingPanel.classList.remove('hidden');
        thinkingSpinner.style.display = '';
        // Append incrementally instead of replacing
        if (!thinkingPanelBody._thinkNode) {
          thinkingPanelBody._thinkNode = document.createTextNode(evt.text || '');
          thinkingPanelBody.appendChild(thinkingPanelBody._thinkNode);
        } else {
          thinkingPanelBody._thinkNode.textContent += (evt.text || '');
        }
        thinkingPanelBody.scrollTop = thinkingPanelBody.scrollHeight;
        break;
      case 'clear_thinking':
        // Phase B start: clear Phase A thinking, reset thinking node
        thinkingPanelBody._thinkNode = null;
        thinkingPanelBody._contentNode = null;
        thinkingPanelBody.innerHTML = '';
        thinkingSpinner.style.display = '';
        thinkingPanel.classList.remove('hidden');
        analysisSplit.classList.remove('thinking-only');
        break;
      case 'content':
        // Clear skeleton on first content
        if (analysisRawText === '' && analysisContent.querySelector('.skeleton')) {
          analysisContent.innerHTML = '';
        }
        analysisRawText += (evt.text || '');
        // During streaming: append text incrementally (O(1) per chunk)
        if (!analysisContent.querySelector('.stream-raw-text')) {
          analysisContent.innerHTML = '';
          var pre = document.createElement('pre');
          pre.className = 'stream-raw-text';
          pre.style.cssText = 'white-space:pre-wrap;word-wrap:break-word;font-family:inherit;line-height:1.7;margin:0;padding:1rem;color:inherit;background:transparent;';
          analysisContent.appendChild(pre);
          pre._streamNode = null; // will hold a persistent text node
        }
        var rawPre = analysisContent.querySelector('.stream-raw-text');
        // Append to existing text node (avoid replacing entire textContent)
        if (!rawPre._streamNode) {
          rawPre._streamNode = document.createTextNode(evt.text || '');
          rawPre.appendChild(rawPre._streamNode);
        } else {
          rawPre._streamNode.textContent += (evt.text || '');
        }
        analysisContent.classList.add('streaming-cursor');
        // Also append raw content to thinking panel as live preview (serves as keepalive)
        if (!thinkingPanelBody._contentNode) {
          var contentPreview = document.createElement('div');
          contentPreview.style.cssText = 'border-top:1px solid var(--border);margin-top:8px;padding-top:8px;white-space:pre-wrap;word-wrap:break-word;font-size:0.95em;opacity:0.8;';
          thinkingPanelBody.appendChild(contentPreview);
          thinkingPanelBody._contentNode = document.createTextNode(evt.text || '');
          contentPreview.appendChild(thinkingPanelBody._contentNode);
        } else {
          thinkingPanelBody._contentNode.textContent += (evt.text || '');
        }
        thinkingPanelBody.scrollTop = thinkingPanelBody.scrollHeight;
        // Throttle auto-scroll via rAF
        if (!_contentScrollRaf) {
          _contentScrollRaf = requestAnimationFrame(function() {
            var panel = analysisContent.closest('.result-panel');
            if (panel) panel.scrollTop = panel.scrollHeight;
            _contentScrollRaf = null;
          });
        }
        break;
      case 'done':
        setPipelineStep(4, 'done');
        stopPipelineTimer();
        analyzeStatusText.textContent = '分析完成 ✓';
        // Final render: replace raw text with markdown-formatted HTML (single call)
        if (analysisRawText) {
          analysisContent.innerHTML = renderMarkdown(analysisRawText);
        }
        analysisContent.classList.remove('streaming-cursor');
        thinkingSpinner.style.display = 'none';
        showToast('分析完成', 'success');
        var badge4 = document.createElement('div');
        badge4.className = 'step-badge step-badge-4';
        badge4.innerHTML = '🚀 <b>Step 4</b> — RCA 深度分析完成';
        stepsContainer.appendChild(badge4);
        $('btnExportMd').style.display = '';
        break;
      case 'error':
        showToast('⚠️ ' + evt.text, 'error', 8000);
        // Show error in analysis area but keep existing content
        analysisContent.classList.remove('streaming-cursor');
        thinkingSpinner.style.display = 'none';
        break;
    }
  }

  function cancelAnalyze() {
    if (currentController) {
      currentController.abort();
    }
  }

  // ===== EXPORT ANALYSIS TO MARKDOWN =====
  $('btnExportMd').addEventListener('click', function() {
    if (!analysisRawText.trim()) {
      showToast('沒有分析結果可匯出', 'error');
      return;
    }
    // Collect pipeline step badges
    var badges = stepsContainer.querySelectorAll('.step-badge');
    var steps = [];
    badges.forEach(function(b) {
      steps.push('- ' + b.textContent.trim());
    });

    // Build MD content
    var now = new Date();
    var ts = now.getFullYear() + '-' +
      String(now.getMonth()+1).padStart(2,'0') + '-' +
      String(now.getDate()).padStart(2,'0') + ' ' +
      String(now.getHours()).padStart(2,'0') + ':' +
      String(now.getMinutes()).padStart(2,'0');

    var md = '# Bug-Detective RCA 分析報告\n\n';
    md += '**生成時間：** ' + ts + '\n\n';

    if (steps.length) {
      md += '## Pipeline 步驟\n\n';
      md += steps.join('\n') + '\n\n';
    }

    // Search results summary
    var searchItems = searchBody.querySelectorAll('.search-result-item');
    if (searchItems.length) {
      md += '## 關鍵字搜尋結果\n\n';
      md += '共 ' + searchItems.length + ' 筆匹配\n\n';
      md += '### 匹配檔案\n\n';
      searchItems.forEach(function(item, idx) {
        var path = item.querySelector('.search-result-path');
        var score = item.querySelector('.score-bar-fill');
        if (path) {
          var scoreText = score ? score.parentElement.textContent.trim() : '';
          md += (idx+1) + '. `' + path.textContent + '`' + (scoreText ? ' — ' + scoreText : '') + '\n';
        }
      });
      md += '\n';
    }

    md += '## AI 根因分析\n\n';
    md += analysisRawText + '\n';

    // Download
    var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'RCA_' + now.getFullYear() +
      String(now.getMonth()+1).padStart(2,'0') +
      String(now.getDate()).padStart(2,'0') + '_' +
      String(now.getHours()).padStart(2,'0') +
      String(now.getMinutes()).padStart(2,'0') + '.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('已匯出 Markdown 報告', 'success');
  });

  // ===== RENDER SEARCH RESULTS (collapsible) =====
  function renderSearchResults(results) {
    if (!results || !results.length) return;
    searchCard.classList.remove('hidden');
    searchCard.classList.add('fade-in');
    searchActions.style.display = '';
    searchCount.textContent = results.length + ' 筆';
    searchBody.innerHTML = '';
    results.forEach(function(r, idx) {
      var score = r.score ? (r.score * 100).toFixed(1) : '0';
      var scoreClass = score >= 70 ? 'high' : score >= 40 ? 'medium' : 'low';
      var item = document.createElement('div');
      item.className = 'search-result-item';
      item.dataset.idx = idx;

      var header = document.createElement('div');
      header.className = 'search-result-header';
      header.setAttribute('role', 'button');
      header.setAttribute('tabindex', '0');
      header.setAttribute('aria-expanded', 'false');
      header.setAttribute('aria-label', '展開 ' + (r.file_path || r.file_name || ''));
      header.innerHTML = '<span class="search-result-arrow">▶</span>' +
        '<div class="search-result-header-left">' +
        '<div class="search-result-path">' + escapeHtml(r.file_path || r.file_name || '') + '</div>' +
        '<div class="search-result-meta">' +
        (r.language ? '<span>' + escapeHtml(r.language) + '</span>' : '') +
        '<span class="score-bar">相關度 <span class="score-bar-track"><span class="score-bar-fill ' + scoreClass + '" style="width:' + Math.min(score, 100) + '%"></span></span> ' + score + '%</span>' +
        '</div></div>';
      header.addEventListener('click', function() {
        var open = item.classList.toggle('open');
        header.setAttribute('aria-expanded', String(open));
      });
      header.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); header.click(); }
      });
      item.appendChild(header);

      var body = document.createElement('div');
      body.className = 'search-result-body';
      body.innerHTML = '<div class="search-result-text">' + escapeHtml(r.text || '') + '</div>';
      item.appendChild(body);

      searchBody.appendChild(item);
    });
  }

  // Collapse all / Expand all
  $('btnExpandAll').addEventListener('click', function() {
    searchBody.querySelectorAll('.search-result-item').forEach(function(el) {
      el.classList.add('open');
      var h = el.querySelector('.search-result-header');
      if (h) h.setAttribute('aria-expanded', 'true');
    });
  });
  $('btnCollapseAll').addEventListener('click', function() {
    searchBody.querySelectorAll('.search-result-item').forEach(function(el) {
      el.classList.remove('open');
      var h = el.querySelector('.search-result-header');
      if (h) h.setAttribute('aria-expanded', 'false');
    });
  });

  // ===== QUICK SEARCH =====
  btnSearch.addEventListener('click', doSearch);
  searchQuery.addEventListener('keydown', function(e) { if (e.key === 'Enter') doSearch(); });

  async function doSearch() {
    var q = searchQuery.value.trim();
    if (!q) { searchQuery.focus(); return; }
    btnSearch.disabled = true;
    btnSearch.innerHTML = '<span class="spinner"></span>';
    placeholderCard.classList.add('hidden');
    showSearchSkeleton();
    try {
      var res = await fetch(API + '/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, top_k: 10 })
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      if (data.results && data.results.length) {
        renderSearchResults(data.results);
        showToast('找到 ' + data.count + ' 筆結果', 'success');
      } else {
        searchCard.classList.remove('hidden');
        searchCount.textContent = '0 筆';
        searchBody.innerHTML = '<div class="empty-state">' +
          '<div class="empty-state-icon">🔍</div>' +
          '<div class="empty-state-title">沒有找到相關結果</div>' +
          '<div class="empty-state-desc">嘗試使用不同的關鍵字或更广泛的搜尋詞</div>' +
          '</div>';
        showToast('沒有找到相關結果', 'info');
      }
    } catch(e) {
      showToast('搜尋失敗：' + e.message, 'error');
    } finally {
      btnSearch.disabled = false;
      btnSearch.textContent = '搜尋';
    }
  }

  // ===== SETTINGS MODAL (with Focus Trap) =====
  var focusTrapElements = [];
  var lastFocusedElement = null;

  function getFocusableElements(container) {
    return container.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
  }

  function trapFocus(e) {
    var focusable = getFocusableElements(settingsModal.querySelector('.modal'));
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (e.key === 'Tab') {
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  }

  btnSettings.addEventListener('click', openSettings);
  btnCloseSettings.addEventListener('click', closeSettings);
  btnCancelSettings.addEventListener('click', closeSettings);

  function openSettings() {
    lastFocusedElement = document.activeElement;
    settingsModal.classList.add('open');
    settingsModal.setAttribute('aria-hidden', 'false');
    document.addEventListener('keydown', trapFocus);
    loadLLMConfig();
    // Focus first interactive element
    setTimeout(function() {
      var first = getFocusableElements(settingsModal.querySelector('.modal'));
      if (first.length) first[0].focus();
    }, 100);
  }
  function closeSettings() {
    settingsModal.classList.remove('open');
    settingsModal.setAttribute('aria-hidden', 'true');
    modelList.classList.remove('open');
    document.removeEventListener('keydown', trapFocus);
    if (lastFocusedElement) lastFocusedElement.focus();
  }

  async function loadLLMConfig() {
    try {
      var cfgRes = await fetch(API + '/api/llm-config');
      var presetsRes = await fetch(API + '/api/llm-presets');
      if (cfgRes.ok) {
        var cfg = await cfgRes.json();
        cfgProvider.value = cfg.provider || 'ollama';
        cfgBaseUrl.value = cfg.base_url || '';
        if (_apiKey) {
          var masked = _apiKey.slice(0, 6) + '...' + _apiKey.slice(-4);
          cfgApiKey.value = '';
          cfgApiKey.placeholder = masked + '（瀏覽器已記住）';
        } else if (cfg.api_key_set) {
          cfgApiKey.value = '';
          cfgApiKey.placeholder = 'sk-...（伺服器已設定）';
        } else {
          cfgApiKey.value = '';
        cfgApiKey.placeholder = 'sk-...';
        }
        cfgModel.value = cfg.model || '';
        cfgMaxTokens.value = cfg.max_tokens || '';
        cfgTimeout.value = cfg.timeout || '';
      }
      if (presetsRes.ok) {
        var presets = await presetsRes.json();
        window._presets = presets;
      }
    } catch(e) {
      showToast('無法載入設定', 'error');
    }
    updateApiKeyVisibility();
    updateApiKeyStatus();
  }

  function updateApiKeyVisibility() {
    var provider = cfgProvider.value;
    if (provider === 'ollama') {
      apiKeyGroup.style.display = 'none';
      cfgApiKey.value = '';
    } else {
      apiKeyGroup.style.display = '';
      var hints = { glm5: '（z.ai API Key）', openrouter: '（OpenRouter API Key）', minimax: '（MiniMax API Key）' };
      apiKeyHint.textContent = hints[provider] || '';
    }
  }
  cfgProvider.addEventListener('change', function() { updateApiKeyVisibility(); updateApiKeyStatus(); });
  cfgApiKey.addEventListener('input', updateApiKeyStatus);

  // Preset buttons
  presetBtns.addEventListener('click', async function(e) {
    var btn = e.target.closest('.preset-btn');
    if (!btn) return;
    var provider = btn.dataset.provider;
    presetBtns.querySelectorAll('.preset-btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    try {
      var res = await fetch(API + '/api/llm-config/preset/' + encodeURIComponent(provider), { method: 'POST' });
      if (res.ok) {
        var cfg = await res.json();
        cfgProvider.value = cfg.provider || provider;
        cfgBaseUrl.value = cfg.base_url || '';
        cfgApiKey.value = '';
        cfgModel.value = cfg.model || '';
        cfgMaxTokens.value = cfg.max_tokens || '';
        cfgTimeout.value = cfg.timeout || '';
        updateApiKeyVisibility();
        updateApiKeyStatus();
        showToast('已套用 ' + provider + ' 預設', 'success');
        loadModels();
      }
    } catch(e) {
      showToast('套用預設失敗', 'error');
    }
  });

  // Default settings button — reset all to defaults
  var btnDefaultSettings = $('btnDefaultSettings');
  if (btnDefaultSettings) {
    btnDefaultSettings.addEventListener('click', async function() {
      try {
        var res = await fetch(API + '/api/llm-config/preset/ollama');
        if (res.ok) {
          var cfg = await res.json();
          cfgProvider.value = 'ollama';
          cfgBaseUrl.value = cfg.base_url || '';
          cfgApiKey.value = '';
          cfgModel.value = cfg.model || 'qwen3.6:35b-a3b-200k';
          cfgMaxTokens.value = '16000';
          cfgTimeout.value = '600';
          updateApiKeyVisibility();
          updateApiKeyStatus();
        }
      } catch(e) {}
      // Reset UI-only settings (no backend needed)
      document.documentElement.setAttribute('data-theme', 'gray');
      highlightThemeBtn('gray');
      var fs = $('cfgFontSize');
      if (fs) { fs.value = 16; document.documentElement.style.fontSize = '16px'; }
      var fsLabel = $('fontSizeValue');
      if (fsLabel) { fsLabel.textContent = '16px'; }
      var kl = $('cfgKeywordLimit');
      if (kl) { kl.value = 50; }
      var klLabel = $('keywordLimitValue');
      if (klLabel) { klLabel.textContent = '50'; }
      var temp = $('cfgTemperature');
      if (temp) { temp.value = '0.3'; }
      var tempLabel = $('temperatureValue');
      if (tempLabel) { tempLabel.textContent = '0.3'; }
      // Clear active highlight on provider presets
      presetBtns.querySelectorAll('.preset-btn').forEach(function(b) { b.classList.remove('active'); });
      showToast('已重設為預設值', 'success');
    });
  }

  // Save settings
  btnSaveSettings.addEventListener('click', async function() {
    _apiKey = cfgApiKey.value.trim();
    sessionStorage.setItem(API_KEY_STORAGE, _apiKey);
    updateApiKeyStatus();
    try {
      var res = await fetch(API + '/api/llm-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: cfgBaseUrl.value.trim(),
          model: cfgModel.value.trim(),
          provider: cfgProvider.value,
          max_tokens: parseInt(cfgMaxTokens.value) || 16000,
          timeout: parseInt(cfgTimeout.value) || 600,
        })
      });
      if (res.ok) {
        showToast('設定已儲存', 'success');
        closeSettings();
      } else {
        showToast('儲存失敗：HTTP ' + res.status, 'error');
      }
    } catch(e) {
      showToast('儲存失敗：' + e.message, 'error');
    }
  });

  // Model dropdown
  cfgModel.addEventListener('focus', loadModels);
  cfgModel.addEventListener('input', function() {
    modelList.classList.remove('open');
    updateApiKeyStatus();
  });

  var btnFetchModels = $('btnFetchModels');
  btnFetchModels.addEventListener('click', async function() {
    var baseUrl = cfgBaseUrl.value.trim();
    if (!baseUrl) {
      showToast('請先填入 Base URL', 'error');
      return;
    }
    btnFetchModels.disabled = true;
    btnFetchModels.textContent = '⏳ 抓取中…';
    try {
      var res = await fetch(API + '/api/fetch-models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: baseUrl,
          api_key: cfgApiKey.value.trim()
        })
      });
      var data = await res.json();
      if (data.models && data.models.length > 0) {
        renderModelList(data.models);
        modelList.classList.add('open');
        var sourceLabel = data.source === 'ollama' ? 'Ollama' : 'OpenAI 相容';
        showToast('找到 ' + data.models.length + ' 個模型 (' + sourceLabel + ')', 'success');
      } else {
        modelList.innerHTML = '';
        showToast(data.error || '未找到可用模型', 'error');
      }
    } catch(e) {
      showToast('抓取失敗：' + e.message, 'error');
    } finally {
      btnFetchModels.disabled = false;
      btnFetchModels.textContent = '🔍 抓取模型';
    }
  });

  async function loadModels() {
    var baseUrl = cfgBaseUrl.value.trim();
    if (baseUrl) {
      try {
        var res = await fetch(API + '/api/fetch-models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            base_url: baseUrl,
            api_key: cfgApiKey.value.trim()
          })
        });
        if (res.ok) {
          var data = await res.json();
          if (data.models && data.models.length) {
            renderModelList(data.models);
            return;
          }
        }
      } catch(e) {}
    }
    try {
      var res = await fetch(API + '/api/models');
      if (!res.ok) return;
      var data = await res.json();
      if (data.models && data.models.length) {
        renderModelList(data.models);
      }
    } catch(e) {}
  }

  function renderModelList(models) {
    modelList.innerHTML = '';
    models.forEach(function(m) {
      var div = document.createElement('div');
      div.className = 'model-option';
      div.textContent = m;
      div.setAttribute('role', 'option');
      div.addEventListener('click', function() {
        cfgModel.value = m;
        modelList.classList.remove('open');
      });
      modelList.appendChild(div);
    });
  }

  cfgModel.addEventListener('click', function() {
    if (modelList.children.length > 0) {
      modelList.classList.toggle('open');
    }
  });

  document.addEventListener('click', function(e) {
    if (!e.target.closest('.model-dropdown')) {
      modelList.classList.remove('open');
    }
  });

  // ===== MARKDOWN RENDERER (basic) =====
  function renderMarkdown(text) {
    if (!text) return '';
    var html = text;

    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
      return '<pre><code>' + escapeHtml(code.trim()) + '</code></pre>';
    });
    html = html.replace(/```(\w*)\n([\s\S]*)$/g, function(_, lang, code) {
      return '<pre><code>' + escapeHtml(code.trimEnd()) + '</code></pre>';
    });

    html = html.replace(/`([^`]+)`/g, function(_, code) { return '<code>' + escapeHtml(code) + '</code>'; });

    html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>');
    html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

    // XSS: block javascript:/data:/vbscript: schemes, escape link text & URL
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_, text, url) {
      if (/^\s*(javascript|data|vbscript)\s*:/i.test(url)) return escapeHtml(text);
      return '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + escapeHtml(text) + '</a>';
    });

    html = html.replace(/^>\s+(.+)$/gm, '<blockquote>$1</blockquote>');

    html = html.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, function(match) {
      if (match.indexOf('<ul>') >= 0) return match;
      return '<ol>' + match + '</ol>';
    });

    html = html.replace(/^---$/gm, '<hr>');

    var lines = html.split('\n');
    var result = [];
    for (var i = 0; i < lines.length; i++) {
      var trimmed = lines[i].trim();
      if (trimmed.match(/^<(h[1-6]|ul|ol|li|pre|code|blockquote|hr|p|div)/)) {
        result.push(lines[i]);
      } else if (trimmed === '') {
        result.push('');
      } else {
        result.push(trimmed + '<br>');
      }
    }
    html = result.join('\n');

    html = html.replace(/<br>\s*<br>/g, '</p><p>');
    html = html.replace(/<\/blockquote>\n<blockquote>/g, '\n');

    return html;
  }

  // ===== HELPERS =====
  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ===== INIT =====
  checkHealth();
  setInterval(checkHealth, 30000);
  loadLLMConfig();

})();
