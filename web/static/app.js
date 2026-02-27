// SourceCrawler Frontend

const state = {
    ws: null,
    scanId: null,
    results: [],
    isScanning: false,
    sortCol: null,
    sortDir: 'asc',
    searchQuery: '',
    searchMode: 'string',
};

const el = {
    query:        document.getElementById('query'),
    mode:         document.getElementById('mode'),
    searchBtn:    document.getElementById('search-btn'),
    cancelBtn:    document.getElementById('cancel-btn'),
    statusBar:    document.getElementById('status-bar'),
    filter:       document.getElementById('filter'),
    resultCount:  document.getElementById('result-count'),
    resultsBody:  document.getElementById('results-body'),
    exportJson:   document.getElementById('export-json'),
    exportCsv:    document.getElementById('export-csv'),
    emptyState:   document.getElementById('empty-state'),
    debugMode:    document.getElementById('debug-mode'),
    settingsBtn:  document.getElementById('settings-btn'),
    settingsPanel:document.getElementById('settings-panel'),
    settingsClose:document.getElementById('settings-close'),
    settingsBody: document.getElementById('settings-body'),
    clearBtn:     document.getElementById('clear-btn'),
    detailModal:  document.getElementById('detail-modal'),
    modalClose:   document.getElementById('modal-close'),
    modalBody:    document.getElementById('modal-body'),
};

// -- Start Scan --
async function startScan() {
    const query = el.query.value.trim();
    if (!query || state.isScanning) return;

    const resp = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            query,
            mode: el.mode.value,
            debug_mode: el.debugMode.checked,
        }),
    });
    const data = await resp.json();
    state.scanId = data.scan_id;
    state.results = [];
    state.isScanning = true;
    state.sortCol = null;
    state.sortDir = 'asc';
    state.searchQuery = query;
    state.searchMode = el.mode.value;

    el.resultsBody.innerHTML = '';
    el.statusBar.innerHTML = '';
    el.emptyState.classList.add('hidden');
    el.resultCount.textContent = '0 results';
    clearSortIndicators();
    updateUI();

    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    state.ws = new WebSocket(`${wsProto}//${location.host}/ws/results/${state.scanId}`);

    state.ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };

    state.ws.onclose = () => {
        state.isScanning = false;
        updateUI();
    };

    state.ws.onerror = () => {
        state.isScanning = false;
        updateUI();
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'result':
            state.results.push(msg.data);
            appendResultRow(msg.data);
            el.resultCount.textContent = `${state.results.length} results`;
            break;
        case 'status':
            updateProviderStatus(msg.provider, msg.message);
            break;
        case 'complete':
            state.isScanning = false;
            updateUI();
            if (msg.errors && msg.errors.length > 0) {
                updateProviderStatus('errors', msg.errors.join('; '));
            }
            break;
        case 'error':
            updateProviderStatus('system', `error: ${msg.message}`);
            break;
    }
}

function appendResultRow(result) {
    const filter = el.filter.value.toLowerCase();
    const row = createResultRow(result);
    if (filter && !rowMatchesFilter(result, filter)) {
        row.style.display = 'none';
    }
    el.resultsBody.appendChild(row);
}

function createResultRow(r) {
    const tr = document.createElement('tr');
    tr.dataset.provider = r.provider_name;
    tr.dataset.resultIdx = state.results.length - 1;

    const tdProvider = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `provider-badge provider-${r.provider_name}`;
    badge.textContent = r.provider_name;
    tdProvider.appendChild(badge);

    const tdUrl = document.createElement('td');
    const link = document.createElement('a');
    link.href = r.target_url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = truncate(r.target_url, 80);
    link.title = r.target_url;
    link.addEventListener('click', (e) => e.stopPropagation());
    tdUrl.appendChild(link);

    const tdSnippet = document.createElement('td');
    const code = document.createElement('code');
    code.innerHTML = highlightMatch(truncate(r.code_snippet, 200), state.searchQuery, state.searchMode);
    code.title = r.code_snippet;
    tdSnippet.appendChild(code);

    const tdTime = document.createElement('td');
    tdTime.className = 'timestamp';
    tdTime.textContent = new Date(r.timestamp).toLocaleTimeString();

    tr.append(tdProvider, tdUrl, tdSnippet, tdTime);

    // Click row to open detail modal
    tr.addEventListener('click', () => showDetailModal(r));

    return tr;
}

// -- Detail Modal --
function showDetailModal(result) {
    let html = '';

    html += detailField('Source', `<span class="provider-badge provider-${result.provider_name}">${result.provider_name}</span>`);
    html += detailField('URL', `<a href="${escapeHtml(result.target_url)}" target="_blank" rel="noopener">${escapeHtml(result.target_url)}</a>`);
    html += detailField('Snippet', `<code>${highlightMatch(result.code_snippet, state.searchQuery, state.searchMode)}</code>`);
    html += detailField('Timestamp', escapeHtml(new Date(result.timestamp).toLocaleString()));
    html += detailField('Result ID', escapeHtml(result.result_id || 'N/A'));

    // Show all metadata
    if (result.metadata && Object.keys(result.metadata).length > 0) {
        let metaHtml = '<div class="metadata-grid">';
        for (const [key, val] of Object.entries(result.metadata)) {
            const displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);
            metaHtml += `<span class="meta-key">${escapeHtml(key)}</span>`;
            metaHtml += `<span class="meta-val">${escapeHtml(displayVal)}</span>`;
        }
        metaHtml += '</div>';
        html += detailField('Metadata', metaHtml);
    }

    el.modalBody.innerHTML = html;
    el.detailModal.classList.remove('hidden');
}

function detailField(label, value) {
    return `<div class="detail-field"><div class="detail-label">${escapeHtml(label)}</div><div class="detail-value">${value}</div></div>`;
}

function closeDetailModal() {
    el.detailModal.classList.add('hidden');
}

// -- Sorting --
function sortByColumn(col) {
    if (state.sortCol === col) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        state.sortCol = col;
        state.sortDir = 'asc';
    }

    state.results.sort((a, b) => {
        let va = a[col] || '';
        let vb = b[col] || '';
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return state.sortDir === 'asc' ? -1 : 1;
        if (va > vb) return state.sortDir === 'asc' ? 1 : -1;
        return 0;
    });

    renderResults();
    updateSortIndicators();
}

function clearSortIndicators() {
    document.querySelectorAll('.results-table thead th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
    });
}

function updateSortIndicators() {
    clearSortIndicators();
    if (state.sortCol) {
        const th = document.querySelector(`.results-table thead th[data-col="${state.sortCol}"]`);
        if (th) th.classList.add(state.sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
}

function renderResults() {
    el.resultsBody.innerHTML = '';
    const filter = el.filter.value.toLowerCase();
    for (const r of state.results) {
        const row = createResultRow(r);
        if (filter && !rowMatchesFilter(r, filter)) {
            row.style.display = 'none';
        }
        el.resultsBody.appendChild(row);
    }
}

// -- Filter --
el.filter.addEventListener('input', () => {
    const filter = el.filter.value.toLowerCase();
    const rows = el.resultsBody.querySelectorAll('tr');
    let visible = 0;
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        const show = !filter || text.includes(filter);
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    el.resultCount.textContent = filter
        ? `${visible} of ${state.results.length} results`
        : `${state.results.length} results`;
});

// -- Cancel --
function cancelScan() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ action: 'cancel' }));
    }
}

// -- Export --
function exportResults(format) {
    if (state.scanId) {
        window.open(`/api/export/${state.scanId}?format=${format}`, '_blank');
    }
}

// -- Settings --
async function loadSettings() {
    try {
        const resp = await fetch('/api/settings/scanners');
        const data = await resp.json();
        let html = '';
        for (const s of data.scanners) {
            const disabledAttr = s.configured ? '' : 'disabled';
            const checkedAttr = (s.enabled && s.configured) ? 'checked' : '';
            const cls = s.configured ? '' : 'unconfigured';
            const statusText = s.configured ? '' : '(no API key)';
            html += `<label class="scanner-toggle ${cls}">
                <input type="checkbox" data-scanner="${s.name}" ${checkedAttr} ${disabledAttr}>
                <span class="scanner-name">${s.name}</span>
                <span class="scanner-status">${statusText}</span>
            </label>`;
        }
        // Add PublicWWW credentials section
        html += `<div class="settings-separator"></div>`;
        html += `<div class="settings-credentials">
            <div class="cred-header">PublicWWW Login <span class="text-muted">(free account unlocks top 3M sites)</span></div>
            <div class="cred-fields">
                <input type="email" id="pwww-email" placeholder="Email" class="cred-input" autocomplete="email">
                <input type="password" id="pwww-password" placeholder="Password" class="cred-input" autocomplete="current-password">
                <button id="pwww-save" class="btn-small">Save</button>
            </div>
        </div>`;

        el.settingsBody.innerHTML = html;

        // Load existing PublicWWW settings
        try {
            const pwResp = await fetch('/api/settings/publicwww');
            const pwData = await pwResp.json();
            const emailInput = document.getElementById('pwww-email');
            if (emailInput && pwData.email) emailInput.value = pwData.email;
            if (pwData.has_password) {
                const pwInput = document.getElementById('pwww-password');
                if (pwInput) pwInput.placeholder = '••••••••  (saved)';
            }
        } catch (e) { /* ignore */ }

        // Bind scanner toggle change events
        el.settingsBody.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', async () => {
                const scanners = {};
                el.settingsBody.querySelectorAll('input[data-scanner]').forEach(inp => {
                    scanners[inp.dataset.scanner] = inp.checked;
                });
                await fetch('/api/settings/scanners', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scanners }),
                });
            });
        });

        // Bind PublicWWW save button
        const saveBtn = document.getElementById('pwww-save');
        if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
                const email = document.getElementById('pwww-email').value.trim();
                const password = document.getElementById('pwww-password').value;
                const payload = { email };
                if (password) payload.password = password;
                await fetch('/api/settings/publicwww', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                saveBtn.textContent = 'Saved!';
                setTimeout(() => { saveBtn.textContent = 'Save'; }, 1500);
            });
        }
    } catch (e) {
        el.settingsBody.innerHTML = '<p class="text-muted">Failed to load settings.</p>';
    }
}

function toggleSettings() {
    const isHidden = el.settingsPanel.classList.contains('hidden');
    if (isHidden) {
        loadSettings();
        el.settingsPanel.classList.remove('hidden');
    } else {
        el.settingsPanel.classList.add('hidden');
    }
}

// -- Clear All --
async function clearAllData() {
    if (!confirm('Clear all scan data and results?')) return;
    await fetch('/api/clear', { method: 'POST' });
    state.results = [];
    state.scanId = null;
    state.isScanning = false;
    state.sortCol = null;
    el.resultsBody.innerHTML = '';
    el.statusBar.innerHTML = '';
    el.resultCount.textContent = '0 results';
    el.emptyState.classList.remove('hidden');
    clearSortIndicators();
    updateUI();
    if (state.ws) {
        state.ws.close();
        state.ws = null;
    }
}

// -- UI State --
function updateUI() {
    el.searchBtn.disabled = state.isScanning;
    el.cancelBtn.disabled = !state.isScanning;
    el.exportJson.disabled = state.results.length === 0;
    el.exportCsv.disabled = state.results.length === 0;

    if (state.isScanning) {
        el.searchBtn.classList.add('scanning');
        el.searchBtn.textContent = 'Scanning...';
    } else {
        el.searchBtn.classList.remove('scanning');
        el.searchBtn.textContent = 'Scan';
    }
}

function updateProviderStatus(provider, message) {
    let chip = document.getElementById(`status-${provider}`);
    if (!chip) {
        chip = document.createElement('div');
        chip.id = `status-${provider}`;
        chip.className = 'status-chip';
        el.statusBar.appendChild(chip);
    }
    chip.textContent = `${provider}: ${message}`;

    chip.classList.remove('active', 'done', 'error');
    if (message.includes('error')) {
        chip.classList.add('error');
    } else if (message.includes('completed') || message.includes('cancelled')) {
        chip.classList.add('done');
    } else if (message.includes('started')) {
        chip.classList.add('active');
    }
}

// -- Utilities --
function truncate(str, len) {
    str = str || '';
    return str.length > len ? str.substring(0, len) + '\u2026' : str;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function highlightMatch(text, query, mode) {
    if (!query || !text) return escapeHtml(text);
    try {
        let pattern;
        if (mode === 'regex') {
            pattern = new RegExp(`(${query})`, 'gi');
        } else {
            // Escape regex special chars for literal string matching
            const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            pattern = new RegExp(`(${escaped})`, 'gi');
        }
        // Split on matches, escape each part, wrap matches in <mark>
        const parts = text.split(pattern);
        return parts.map(part => {
            if (pattern.test(part)) {
                pattern.lastIndex = 0; // reset regex state
                return `<mark class="highlight">${escapeHtml(part)}</mark>`;
            }
            pattern.lastIndex = 0;
            return escapeHtml(part);
        }).join('');
    } catch (e) {
        return escapeHtml(text);
    }
}

function rowMatchesFilter(result, filter) {
    return (result.provider_name + ' ' + result.target_url + ' ' + result.code_snippet)
        .toLowerCase().includes(filter);
}

// -- Event Bindings --
el.searchBtn.addEventListener('click', startScan);
el.cancelBtn.addEventListener('click', cancelScan);
el.exportJson.addEventListener('click', () => exportResults('json'));
el.exportCsv.addEventListener('click', () => exportResults('csv'));
el.query.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !state.isScanning) startScan();
});
el.settingsBtn.addEventListener('click', toggleSettings);
el.settingsClose.addEventListener('click', () => el.settingsPanel.classList.add('hidden'));
el.clearBtn.addEventListener('click', clearAllData);
el.modalClose.addEventListener('click', closeDetailModal);
el.detailModal.addEventListener('click', (e) => {
    if (e.target === el.detailModal) closeDetailModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDetailModal();
});

// Sortable headers
document.querySelectorAll('.results-table thead th.sortable').forEach(th => {
    th.addEventListener('click', () => sortByColumn(th.dataset.col));
});
