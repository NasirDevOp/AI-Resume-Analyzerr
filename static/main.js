'use strict';

// ── Safe element getter ───────────────────────────────────────────────────
function el(id) { return document.getElementById(id); }

let currentAnalysisId = null;

// ── Wait for DOM ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {

  const form           = el('analyzeForm');
  const fileInput      = el('resumeFile');
  const dropArea       = el('dropArea');
  const filePreview    = el('filePreview');
  const jdTextarea     = el('jobDescription');
  const charCount      = el('charCount');
  const analyzeBtn     = el('analyzeBtn');
  const loadingOverlay = el('loadingOverlay');
  const resultsSection = el('resultsSection');
  const exportBtn      = el('exportBtn');
  const historyDrawer  = el('historyDrawer');
  const historyBackdrop= el('historyBackdrop');
  const historyList    = el('historyList');

  // ── File Upload ─────────────────────────────────────────────────────────
  if (dropArea) dropArea.addEventListener('click', () => fileInput && fileInput.click());

  if (fileInput) fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) showFilePreview(fileInput.files[0], filePreview, dropArea);
  });

  if (dropArea) {
    dropArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      const z = el('uploadZone'); if (z) z.classList.add('dragover');
    });
    dropArea.addEventListener('dragleave', () => {
      const z = el('uploadZone'); if (z) z.classList.remove('dragover');
    });
    dropArea.addEventListener('drop', (e) => {
      e.preventDefault();
      const z = el('uploadZone'); if (z) z.classList.remove('dragover');
      const f = e.dataTransfer.files[0];
      if (f && f.type === 'application/pdf') {
        const dt = new DataTransfer(); dt.items.add(f); fileInput.files = dt.files;
        showFilePreview(f, filePreview, dropArea);
      } else { showToast('Please drop a PDF file.', 'error'); }
    });
  }

  // ── Char count ──────────────────────────────────────────────────────────
  if (jdTextarea) jdTextarea.addEventListener('input', () => {
    const n = jdTextarea.value.length;
    if (charCount) {
      charCount.textContent = n.toLocaleString() + ' characters';
      charCount.style.color = n < 50 ? 'var(--red)' : n > 300 ? 'var(--green)' : 'var(--text-muted)';
    }
  });

  // ── Form submit ─────────────────────────────────────────────────────────
  if (form) form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!fileInput || !fileInput.files[0]) { showToast('Please upload a PDF resume.', 'error'); return; }
    if (!jdTextarea || jdTextarea.value.trim().length < 50) { showToast('Job description too short (min 50 chars).', 'error'); return; }

    if (analyzeBtn) analyzeBtn.disabled = true;
    if (resultsSection) resultsSection.hidden = true;
    showLoading(loadingOverlay);

    try {
      const res  = await fetch('/analyze', { method: 'POST', body: new FormData(form) });
      const data = await res.json();
      if (!res.ok || data.error) {
        if (res.status === 401) { window.location.href = '/login'; return; }
        throw new Error(data.error || 'Analysis failed');
      }
      hideLoading(loadingOverlay);
      renderResults(data, resultsSection, exportBtn);
    } catch (err) {
      hideLoading(loadingOverlay);
      showToast(err.message || 'Something went wrong.', 'error');
      if (analyzeBtn) analyzeBtn.disabled = false;
    }
  });

  // ── History toggle ──────────────────────────────────────────────────────
  window.toggleHistory = function() {
    if (!historyDrawer || !historyBackdrop) return;
    const open = historyDrawer.classList.toggle('open');
    historyBackdrop.classList.toggle('show', open);
    if (open) loadHistory(historyList);
  };

  // ── Export PDF ──────────────────────────────────────────────────────────
  window.exportPDF = async function() {
    if (!currentAnalysisId) { showToast('Run an analysis first.', 'error'); return; }
    if (exportBtn) { exportBtn.disabled = true; exportBtn.textContent = '⏳ Generating...'; }
    try {
      const res = await fetch('/export/' + currentAnalysisId);
      if (!res.ok) { const d = await res.json().catch(()=>({})); throw new Error(d.error || 'Export failed. Run: pip install fpdf2'); }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = 'ATS_Report_' + currentAnalysisId + '.pdf';
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
      showToast('PDF downloaded! ✓', 'success');
    } catch(err) { showToast(err.message, 'error'); }
    finally { if (exportBtn) { exportBtn.disabled = false; exportBtn.textContent = '📄 Export PDF Report'; } }
  };

  // ── Reset ───────────────────────────────────────────────────────────────
  window.resetForm = function() {
    if (resultsSection) resultsSection.hidden = true;
    if (form) form.reset();
    if (filePreview) { filePreview.classList.remove('visible'); filePreview.innerHTML = ''; }
    const ut = dropArea && dropArea.querySelector('.upload-text');
    if (ut) { ut.textContent = 'Drop your PDF here'; ut.style.color = ''; }
    if (charCount) { charCount.textContent = '0 characters'; charCount.style.color = ''; }
    if (analyzeBtn) analyzeBtn.disabled = false;
    currentAnalysisId = null;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

}); // end DOMContentLoaded

// ── Show file preview ─────────────────────────────────────────────────────
function showFilePreview(file, filePreview, dropArea) {
  if (!filePreview) return;
  const kb = (file.size / 1024).toFixed(1);
  filePreview.innerHTML = '📄 ' + file.name + ' (' + kb + ' KB)';
  filePreview.classList.add('visible');
  const ut = dropArea && dropArea.querySelector('.upload-text');
  if (ut) { ut.textContent = '✓ Resume selected'; ut.style.color = 'var(--accent)'; }
}

// ── Loading ───────────────────────────────────────────────────────────────
let stepTimer;
function showLoading(overlay) {
  if (overlay) overlay.hidden = false;
  const ids = ['step1','step2','step3','step4','step5'];
  const existing = ids.filter(id => document.getElementById(id));
  existing.forEach(id => { document.getElementById(id).className = 'loading-step'; });
  if (existing.length === 0) return;
  document.getElementById(existing[0]).classList.add('active');
  let cur = 0;
  stepTimer = setInterval(() => {
    if (cur < existing.length - 1) {
      document.getElementById(existing[cur]).className = 'loading-step done';
      cur++;
      document.getElementById(existing[cur]).classList.add('active');
    }
  }, 900);
}
function hideLoading(overlay) {
  clearInterval(stepTimer);
  if (overlay) overlay.hidden = true;
}

// ── Render results ────────────────────────────────────────────────────────
function renderResults(data, resultsSection, exportBtn) {
  currentAnalysisId = data.analysis_id || null;

  const meta = document.getElementById('resultsMeta');
  if (meta) meta.textContent =
    (data.resume_word_count || 0) + ' words · ' +
    ((data.stats && data.stats.sections_present) || 0) + ' sections · ' +
    (data.matched_keywords || []).length + ' keyword matches';

  const badge = document.getElementById('aiBadge');
  if (badge) {
    badge.textContent = data.suggestion_source === 'claude'
      ? '🤖 Suggestions powered by Claude AI'
      : '⚙️ Rule-based suggestions (set ANTHROPIC_API_KEY for Claude AI)';
    badge.className = 'ai-badge ' + (data.suggestion_source === 'claude' ? 'claude' : 'rules');
    badge.hidden = false;
  }

  if (exportBtn) exportBtn.disabled = !currentAnalysisId;

  renderScoreRing(data.total_score || 0);
  renderBreakdown(data.breakdown || {});
  renderSpacy(data.spacy_entities, data.spacy_chunks, data.stats && data.stats.spacy_available);
  renderKeywords('matchedKeywords', data.matched_keywords || [], 'matched');
  renderKeywords('missingKeywords', data.missing_keywords || [], 'missing');
  renderSections(data.sections_found || {});
  renderSuggestions(data.suggestions || []);

  if (resultsSection) {
    resultsSection.hidden = false;
    setTimeout(() => resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
  }

  loadHistory(document.getElementById('historyList'));
}

// ── Score ring ────────────────────────────────────────────────────────────
function renderScoreRing(score) {
  const ring      = document.getElementById('ringFill');
  const numEl     = document.getElementById('scoreNumber');
  const verdictEl = document.getElementById('scoreVerdict');

  let ringColor, verdictText, verdictClass;
  if      (score >= 80) { ringColor='var(--green)';  verdictText='🚀 Excellent Match'; verdictClass='verdict-excellent'; }
  else if (score >= 65) { ringColor='var(--accent)'; verdictText='✅ Good Match';      verdictClass='verdict-good'; }
  else if (score >= 45) { ringColor='var(--yellow)'; verdictText='⚠️ Fair Match';      verdictClass='verdict-fair'; }
  else                  { ringColor='var(--red)';    verdictText='❌ Needs Work';       verdictClass='verdict-poor'; }

  if (ring) ring.style.stroke = ringColor;
  if (verdictEl) { verdictEl.className = 'score-verdict ' + verdictClass; verdictEl.textContent = verdictText; }

  if (numEl) {
    const dur = 1800, start = performance.now();
    (function anim(now) {
      const p = Math.min((now - start) / dur, 1);
      numEl.textContent = Math.round((1 - Math.pow(1 - p, 3)) * score);
      if (p < 1) requestAnimationFrame(anim);
    })(performance.now());
  }

  if (ring) setTimeout(() => { ring.style.strokeDashoffset = 427 - (score / 100) * 427; }, 80);
}

// ── Breakdown ─────────────────────────────────────────────────────────────
function renderBreakdown(bd) {
  const list = document.getElementById('breakdownList');
  if (!list) return;
  const items = [
    { label:'Keyword Match',   score:bd.keyword_match||0,   max:bd.keyword_max||40,         color:'bar-accent' },
    { label:'Resume Sections', score:bd.sections||0,        max:bd.sections_max||20,         color:'bar-blue' },
    { label:'Contact Info',    score:bd.contact||0,         max:bd.contact_max||10,          color:'bar-green' },
    { label:'Action Verbs',    score:bd.action_verbs||0,    max:bd.action_verbs_max||10,     color:'bar-yellow' },
    { label:'Quantification',  score:bd.quantification||0,  max:bd.quantification_max||10,   color:'bar-purple' },
    { label:'Length & Format', score:bd.length||0,          max:bd.length_max||10,           color:'bar-orange' },
  ];
  list.innerHTML = items.map(item => {
    const pct = ((item.score / item.max) * 100).toFixed(1);
    return '<div class="breakdown-item">' +
      '<div class="breakdown-header">' +
        '<span class="breakdown-name">' + item.label + '</span>' +
        '<span class="breakdown-score">' + item.score + ' / ' + item.max + '</span>' +
      '</div>' +
      '<div class="breakdown-bar-bg">' +
        '<div class="breakdown-bar-fill ' + item.color + '" data-pct="' + pct + '"></div>' +
      '</div></div>';
  }).join('');
  setTimeout(() => {
    list.querySelectorAll('.breakdown-bar-fill').forEach(b => { b.style.width = b.dataset.pct + '%'; });
  }, 150);
}

// ── spaCy ─────────────────────────────────────────────────────────────────
function renderSpacy(entities, chunks, available) {
  const card = document.getElementById('spacyCard');
  if (!card) return;
  if (!available || ((!entities || !entities.length) && (!chunks || !chunks.length))) { card.hidden = true; return; }
  card.hidden = false;
  const grid = document.getElementById('spacyGrid');
  if (!grid) return;
  const eTags = (entities||[]).map(e => '<span class="spacy-tag ' + e.label + '" title="' + e.label + '">' + e.text + ' <small>' + e.label + '</small></span>').join('');
  const cTags = (chunks||[]).slice(0,15).map(c => '<span class="spacy-tag CHUNK">' + c + '</span>').join('');
  grid.innerHTML = eTags + cTags;
}

// ── Keywords ──────────────────────────────────────────────────────────────
function renderKeywords(containerId, keywords, type) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!keywords || keywords.length === 0) {
    container.innerHTML = '<p style="color:var(--text-dim);font-size:13px">None found.</p>'; return;
  }
  container.innerHTML = keywords.map((k, i) =>
    '<span class="kw-tag ' + type + '" style="animation-delay:' + (i*35) + 'ms">' +
      k.keyword + '<span class="kw-freq">' + k.frequency + '</span>' +
    '</span>'
  ).join('');
}

// ── Sections ──────────────────────────────────────────────────────────────
function renderSections(sections) {
  const grid = document.getElementById('sectionsGrid');
  if (!grid) return;
  const labels = { experience:'💼 Experience', education:'🎓 Education', skills:'⚡ Skills',
    projects:'🔧 Projects', certifications:'🏅 Certifications', summary:'📝 Summary',
    achievements:'🏆 Achievements', contact:'📞 Contact' };
  grid.innerHTML = Object.entries(sections).map(([k, v]) =>
    '<div class="section-item ' + (v ? 'found' : 'missing-sec') + '">' +
      '<div class="section-dot"></div><span>' + (labels[k]||k) + '</span>' +
    '</div>'
  ).join('');
}

// ── Suggestions ───────────────────────────────────────────────────────────
function renderSuggestions(suggestions) {
  const list  = document.getElementById('suggestionsList');
  const intro = document.getElementById('suggestionsIntro');
  if (!list) return;

  if (!suggestions || suggestions.length === 0) {
    if (intro) intro.textContent = '🎉 Your resume looks excellent!';
    list.innerHTML = '<div style="text-align:center;padding:24px;color:var(--green);font-size:20px">🎉 No critical issues found!</div>';
    return;
  }

  const crits = suggestions.filter(s => s.category === 'Critical').length;
  if (intro) intro.textContent = 'Found ' + suggestions.length + ' suggestion' + (suggestions.length > 1 ? 's' : '') + ' (' + crits + ' critical). Fixing these will boost your score.';

  list.innerHTML = suggestions.map((s, i) => {
    const cc = s.category === 'Critical' ? 'cat-critical' : s.category === 'Important' ? 'cat-important' : 'cat-nicetohave';
    return '<div class="suggestion-item ' + cc + '" style="animation-delay:' + (i*70) + 'ms">' +
      '<div class="suggestion-icon">' + (s.icon||'💡') + '</div>' +
      '<div>' +
        '<div class="suggestion-category">' + (s.category||'') + '</div>' +
        '<div class="suggestion-title">' + (s.title||'') + '</div>' +
        '<div class="suggestion-detail">' + (s.detail||'') + '</div>' +
      '</div></div>';
  }).join('');
}

// ── History ───────────────────────────────────────────────────────────────
async function loadHistory(historyList) {
  if (!historyList) return;
  historyList.innerHTML = '<div class="history-loading">Loading...</div>';
  try {
    const res  = await fetch('/api/history');
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json();
    if (!data.length) { historyList.innerHTML = '<div class="history-empty">No analyses yet.<br/>Run your first analysis above!</div>'; return; }
    historyList.innerHTML = data.map(item => {
      const sc = item.ats_score >= 80 ? 'score-exc' : item.ats_score >= 65 ? 'score-good-c' : item.ats_score >= 45 ? 'score-fair' : 'score-poor';
      const d  = new Date(item.created).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'});
      const t  = (item.job_title || 'Resume Analysis') + (item.company ? ' @ ' + item.company : '');
      return '<div class="history-item" onclick="loadHistoryItem(' + item.id + ')">' +
        '<button class="history-delete" onclick="deleteHistoryItem(event,' + item.id + ')" title="Delete">✕</button>' +
        '<div class="history-item-header"><span class="history-item-title">' + t + '</span>' +
        '<span class="history-item-score ' + sc + '">' + item.ats_score + '/100</span></div>' +
        '<div class="history-item-meta">' + d + '</div>' +
        '<div class="history-item-tags">' +
          '<span class="history-tag">✓ ' + item.matched_kw + ' matched</span>' +
          '<span class="history-tag">✗ ' + item.missing_kw + ' missing</span>' +
          '<span class="history-tag">' + item.suggestions + ' tips</span>' +
        '</div></div>';
    }).join('');
  } catch(e) { historyList.innerHTML = '<div class="history-empty">Failed to load history.</div>'; }
}

window.loadHistoryItem = async function(id) {
  try {
    const res  = await fetch('/api/history/' + id);
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    window.toggleHistory();
    currentAnalysisId = id;
    renderResults(data, document.getElementById('resultsSection'), document.getElementById('exportBtn'));
    showToast('Loaded from history ✓', 'success');
  } catch(e) { showToast('Failed to load analysis.', 'error'); }
};

window.deleteHistoryItem = async function(e, id) {
  e.stopPropagation();
  if (!confirm('Delete this analysis?')) return;
  try {
    await fetch('/api/history/' + id, { method: 'DELETE' });
    if (currentAnalysisId === id) { currentAnalysisId = null; }
    loadHistory(document.getElementById('historyList'));
    showToast('Deleted.', 'success');
  } catch(e) { showToast('Failed to delete.', 'error'); }
};

// ── Logout ────────────────────────────────────────────────────────────────
window.doLogout = async function() {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
};

// ── Toast ─────────────────────────────────────────────────────────────────
function showToast(msg, type) {
  document.querySelector('.toast') && document.querySelector('.toast').remove();
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  const isErr = type==='error', isOk = type==='success';
  Object.assign(t.style, {
    position:'fixed', bottom:'28px', left:'50%', transform:'translateX(-50%)',
    background: isErr ? 'rgba(248,113,113,0.12)' : isOk ? 'rgba(74,222,128,0.12)' : 'rgba(255,255,255,0.08)',
    border: '1px solid ' + (isErr ? 'rgba(248,113,113,0.3)' : isOk ? 'rgba(74,222,128,0.3)' : 'rgba(255,255,255,0.15)'),
    color: isErr ? '#f87171' : isOk ? '#4ade80' : '#e8e8f0',
    padding:'12px 22px', borderRadius:'100px', fontFamily:'var(--font-m)',
    fontSize:'13px', zIndex:'9999', backdropFilter:'blur(12px)', maxWidth:'90vw', textAlign:'center',
  });
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity='0'; t.style.transition='opacity 0.3s'; setTimeout(()=>t.remove(),300); }, 3500);
}