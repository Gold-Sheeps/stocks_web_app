(function () {
  const state = {
    latest: null,
    running: false,
    runs: [],
    selectedRunId: null,
    compareRunId: null,
    comparePayload: null,
    runCache: {}
  };

  function $(id) { return document.getElementById(id); }

  function fmtNum(v, d = 2) {
    if (v === null || v === undefined || v === '') return '--';
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    return n.toFixed(d);
  }

  function fmtDate(v) {
    if (!v) return '--';
    try { return new Date(v).toLocaleDateString('ja-JP'); } catch { return String(v); }
  }

  function fmtDateTime(v) {
    if (!v) return '--';
    try { return new Date(v).toLocaleString('ja-JP'); } catch { return String(v); }
  }

  async function apiGet(path) {
    const res = await fetch(`${CONFIG.API_BASE}${path}`);
    if (!res.ok) throw new Error(`API ${res.status}`);
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(`${CONFIG.API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) {
      let msg = `API ${res.status}`;
      try {
        const e = await res.json();
        msg = e.detail || e.message || msg;
      } catch {}
      throw new Error(msg);
    }
    return res.json();
  }

  async function getRunById(runId) {
    const id = Number(runId);
    if (!id) throw new Error('Invalid run id');
    if (state.runCache[id]) return state.runCache[id];
    const data = await apiGet(`/prediction/vix/run/${id}`);
    if (data && !data.status) state.runCache[id] = data;
    return data;
  }

  function renderFeatureCards(payload) {
    const box = $('featureCards');
    const snapshot = payload?.feature_snapshot || {};
    const preds = payload?.predictions || [];
    const avgPred = preds.length ? preds.reduce((a, x) => a + Number(x.predicted_vix || 0), 0) / preds.length : null;
    const avgConf = preds.length ? preds.reduce((a, x) => a + Number(x.confidence_score || 0), 0) / preds.length / preds.length : null;
    const cards = [
      ['Run ID', payload?.run_id ?? '--'],
      ['As-Of', payload?.asof_date ?? '--'],
      ['VIX As-Of', fmtNum(snapshot.vix_close_asof)],
      ['Avg Forecast', fmtNum(avgPred)],
      ['Avg Confidence', avgConf == null ? '--' : `${Math.round(avgConf * 100)}%`],
      ['Created At', fmtDateTime(payload?.created_at)]
    ];
    box.innerHTML = cards.map(([label, value]) => `
      <div class="mini-card">
        <div class="label">${label}</div>
        <div class="value">${value}</div>
      </div>
    `).join('');
  }

  function renderPredictionTable(payload) {
    const wrap = $('predictionTableWrap');
    const rows = payload?.predictions || [];
    if (!rows.length) {
      wrap.innerHTML = '<div class="text-muted">No prediction rows</div>';
      return;
    }
    const vix20Label = (r) => {
      const p = Number(r?.predicted_vix);
      const lo = Number(r?.predicted_vix_lower);
      const hi = Number(r?.predicted_vix_upper);
      if (Number.isFinite(lo) && Number.isFinite(hi)) {
        if (lo > 20) return 'Above20';
        if (hi < 20) return 'Below20';
        return 'Cross20';
      }
      if (Number.isFinite(p)) return p > 20 ? 'Above20' : 'Below20';
      return '--';
    };
    wrap.innerHTML = `
      <table class="vix-table">
        <thead>
          <tr>
            <th>H</th>
            <th>Date</th>
            <th class="text-right">Pred VIX</th>
            <th class="text-right">Range</th>
            <th>VIX20</th>
            <th class="text-right">Conf</th>
            <th class="text-right">Blend</th>
            <th>Regime</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td>T+${r.horizon_day}</td>
              <td>${fmtDate(r.target_date)}</td>
              <td class="text-right"><b>${fmtNum(r.predicted_vix)}</b></td>
              <td class="text-right">${fmtNum(r.predicted_vix_lower)} - ${fmtNum(r.predicted_vix_upper)}</td>
              <td><span class="pill">${vix20Label(r)}</span></td>
              <td class="text-right">${r.confidence_score == null ? '--' : `${Math.round(Number(r.confidence_score) * 100)}%`}</td>
              <td class="text-right">${r.blend_alpha_ols == null ? '--' : fmtNum(r.blend_alpha_ols, 2)}</td>
              <td><span class="pill">${r.regime_bucket || '--'}</span></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderBacktest(payload) {
    const el = $('backtestSummary');
    const bt = payload?.backtest_summary || {};
    const metrics = bt.metrics_by_horizon || {};
    const keys = Object.keys(metrics).sort((a, b) => Number(a) - Number(b));
    if (!keys.length) {
      el.innerHTML = '<div class="text-muted">No backtest summary</div>';
      $('backtestJsonBox').textContent = 'No data';
      return;
    }
    const rows = keys.map(k => {
      const m = metrics[k] || {};
      const g = m.gap_feature_signal || {};
      const gapTop = Object.entries(g).sort((a, b) => Math.abs(Number(b[1] || 0)) - Math.abs(Number(a[1] || 0))).slice(0, 2);
      return `
        <tr>
          <td>T+${k}</td>
          <td class="text-right">${fmtNum(m.mae)}</td>
          <td class="text-right">${fmtNum(m.rmse)}</td>
          <td class="text-right">${fmtNum(m.residual_bias_mean)}</td>
          <td>${gapTop.map(([n, v]) => `${n}:${fmtNum(v, 3)}`).join('<br>') || '--'}</td>
        </tr>
      `;
    }).join('');
    el.innerHTML = `
      <div class="text-muted text-sm mb-4">
        Range: ${bt.backtest_start || '--'} -> ${bt.backtest_end || '--'} / Train rows(max): ${bt.training_rows_used_max ?? '--'}
      </div>
      <table class="vix-table">
        <thead>
          <tr>
            <th>Horizon</th>
            <th class="text-right">MAE</th>
            <th class="text-right">RMSE</th>
            <th class="text-right">Bias</th>
            <th>Gap Signals (for next feature tuning)</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    $('backtestJsonBox').textContent = JSON.stringify(bt, null, 2).slice(0, 12000);
  }

  function renderNewsFeatures(payload) {
    $('newsFeaturesBox').textContent = JSON.stringify(payload?.news_features || {}, null, 2);
    renderNewsUsage(payload);
  }

  function renderNewsUsage(payload) {
    const el = $('newsUsageWrap');
    if (!el) return;
    const nf = payload?.news_features || {};
    const usage = nf.news_usage || {};
    const refresh = nf.news_refresh || {};
    const docs = Array.isArray(usage.docs_sample) ? usage.docs_sample : [];
    const queries = Array.isArray(refresh.queries) ? refresh.queries : [];
    const docsBySymbol = usage.docs_by_symbol || {};
    const docsBySource = usage.docs_by_source_type || {};
    el.innerHTML = `
      <div class="mb-4">
        <b>News Refresh:</b> ${refresh.status || 'n/a'} ${refresh.returncode != null ? `(exit=${refresh.returncode})` : ''}
      </div>
      <div class="mb-4">
        <b>Docs Used (last ${usage.window_days || 7}d):</b> ${usage.docs_used_count ?? 0}
      </div>
      <div class="mb-4">
        <b>Docs by Symbol:</b> <span class="code-inline">${JSON.stringify(docsBySymbol)}</span>
      </div>
      <div class="mb-4">
        <b>Docs by Source:</b> <span class="code-inline">${JSON.stringify(docsBySource)}</span>
      </div>
      <div class="mb-4">
        <b>Queries used for refresh:</b>
        ${queries.length ? `<ul style="margin:8px 0 0 18px;">${queries.map(q => `<li><b>${q.symbol_key}</b> [${q.label}] : ${q.query}</li>`).join('')}</ul>` : '<div class="text-muted">No refresh query info</div>'}
      </div>
      <div>
        <b>Sample docs used:</b>
        ${docs.length ? `<ul style="margin:8px 0 0 18px;">${docs.map(d => `<li>${d.published_at ? fmtDateTime(d.published_at) : '--'} | <b>${d.symbol_key}</b> | ${d.title || '(no title)'}</li>`).join('')}</ul>` : '<div class="text-muted">No sample docs found</div>'}
      </div>
    `;
  }

  function renderMeta(payload) {
    $('runMeta').textContent = payload?.created_at
      ? `Latest Run: ${fmtDateTime(payload.created_at)} | As-Of: ${payload.asof_date}`
      : 'No run yet';
    $('modelBadge').textContent = `model: ${payload?.model_version || '--'}`;
  }

  function summarizeRun(payload) {
    const preds = payload?.predictions || [];
    const avgPred = preds.length ? preds.reduce((a, x) => a + Number(x.predicted_vix || 0), 0) / preds.length : null;
    const avgConf = preds.length ? preds.reduce((a, x) => a + Number(x.confidence_score || 0), 0) / preds.length / preds.length : null;
    return { avgPred, avgConf };
  }

  function renderRunCompare() {
    const el = $('runCompareSummary');
    const cmpEl = $('horizonCompareWrap');
    if (!el) return;
    if (!state.latest || !state.comparePayload) {
      el.textContent = 'Select a previous run to compare with current.';
      if (cmpEl) cmpEl.textContent = 'No compare run selected.';
      renderFeatureDiff(null, null);
      return;
    }
    const base = state.comparePayload;
    const cur = state.latest;
    if (!base || !cur) {
      el.textContent = 'Comparison data unavailable.';
      if (cmpEl) cmpEl.textContent = 'Comparison data unavailable.';
      renderFeatureDiff(null, null);
      return;
    }
    renderFeatureDiff(cur, base);
    const curAvgPred = summarizeRun(cur).avgPred;
    const curAvgConf = summarizeRun(cur).avgConf;
    const baseSum = summarizeRun(base);
    const dPred = (curAvgPred == null || baseSum.avgPred == null) ? null : (curAvgPred - Number(baseSum.avgPred));
    const dConf = (curAvgConf == null || baseSum.avgConf == null) ? null : (curAvgConf - Number(baseSum.avgConf));
    el.innerHTML = `
      Compare current run <b>#${cur.run_id}</b> vs selected <b>#${base.run_id}</b>:
      AvgPred Δ <b>${dPred == null ? '--' : (dPred >= 0 ? '+' : '') + fmtNum(dPred)}</b>,
      AvgConf Δ <b>${dConf == null ? '--' : (dConf >= 0 ? '+' : '') + fmtNum(dConf, 3)}</b>
    `;

    if (cmpEl) {
      const curMap = new Map((cur.predictions || []).map(p => [Number(p.horizon_day), p]));
      const baseMap = new Map((base.predictions || []).map(p => [Number(p.horizon_day), p]));
      const horizons = Array.from(new Set([...curMap.keys(), ...baseMap.keys()])).sort((a, b) => a - b);
      if (!horizons.length) {
        cmpEl.textContent = 'No horizon rows available for compare.';
      } else {
        cmpEl.innerHTML = `
          <table class="vix-table">
            <thead>
              <tr>
                <th>H</th>
                <th>Date(A)</th>
                <th class="text-right">Pred A</th>
                <th class="text-right">Pred B</th>
                <th class="text-right">Δ(A-B)</th>
                <th class="text-right">Conf A</th>
                <th class="text-right">Conf B</th>
              </tr>
            </thead>
            <tbody>
              ${horizons.map(h => {
                const a = curMap.get(h) || {};
                const b = baseMap.get(h) || {};
                const da = Number(a.predicted_vix);
                const db = Number(b.predicted_vix);
                const dd = (Number.isFinite(da) && Number.isFinite(db)) ? (da - db) : null;
                return `
                  <tr>
                    <td>T+${h}</td>
                    <td>${fmtDate(a.target_date || b.target_date)}</td>
                    <td class="text-right">${fmtNum(a.predicted_vix)}</td>
                    <td class="text-right">${fmtNum(b.predicted_vix)}</td>
                    <td class="text-right">${dd == null ? '--' : (dd >= 0 ? '+' : '') + fmtNum(dd)}</td>
                    <td class="text-right">${a.confidence_score == null ? '--' : fmtNum(a.confidence_score, 3)}</td>
                    <td class="text-right">${b.confidence_score == null ? '--' : fmtNum(b.confidence_score, 3)}</td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        `;
      }
    }
  }

  function renderFeatureDiff(cur, base) {
    const el = $('featureDiffWrap');
    if (!el) return;
    if (!cur || !base) {
      el.textContent = 'Select a compare run to inspect feature/news diffs.';
      return;
    }
    const s1 = cur.feature_snapshot || {};
    const s2 = base.feature_snapshot || {};
    const n1 = cur.news_features || {};
    const n2 = base.news_features || {};
    const keyList = [
      'vix_close_asof',
      'news_weighted_risk_score_7d',
      'news_auto_weighted_risk_score_7d',
      'news_event_week_score_7d',
      'llm_risk_signal_mean',
      'rag_doc_count_7d',
      'rag_weighted_risk_score_7d',
      'rag_event_week_score_7d',
    ];
    const rows = keyList.map(k => {
      const a = (k in s1) ? s1[k] : n1[k];
      const b = (k in s2) ? s2[k] : n2[k];
      const na = Number(a);
      const nb = Number(b);
      const d = (Number.isFinite(na) && Number.isFinite(nb)) ? (na - nb) : null;
      return `<tr><td>${k}</td><td class="text-right">${a ?? '--'}</td><td class="text-right">${b ?? '--'}</td><td class="text-right">${d == null ? '--' : ((d>=0?'+':'') + fmtNum(d, 3))}</td></tr>`;
    }).join('');
    el.innerHTML = `
      <div class="text-muted text-sm mb-4">Current #${cur.run_id} vs Compare #${base.run_id}</div>
      <table class="vix-table">
        <thead><tr><th>Feature</th><th class="text-right">Current</th><th class="text-right">Compare</th><th class="text-right">Δ</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="text-muted text-sm" style="margin-top:10px;">
        Auto weights(current): ${JSON.stringify((cur.backtest_summary || {}).auto_news_weights || {})}
      </div>
      <div class="text-muted text-sm" style="margin-top:6px;">
        Auto weights(compare): ${JSON.stringify((base.backtest_summary || {}).auto_news_weights || {})}
      </div>
    `;
  }

  function renderRunHistory() {
    const box = $('runHistoryList');
    if (!box) return;
    const items = state.runs || [];
    if (!items.length) {
      box.innerHTML = '<div class="text-muted">No runs</div>';
      return;
    }
    box.innerHTML = items.map(r => `
      <div class="history-item ${Number(state.selectedRunId) === Number(r.run_id) ? 'active' : ''}" data-run-id="${r.run_id}">
        <div class="row1">
          <span>#${r.run_id} / ${r.asof_date || '--'}</span>
          <span>${r.avg_pred_vix == null ? '--' : `avg ${fmtNum(r.avg_pred_vix)}`}</span>
        </div>
        <div class="row2">
          ${fmtDateTime(r.created_at)} | H=${r.horizon_days} | BT=${r.backtest_years}y | conf=${r.avg_confidence == null ? '--' : fmtNum(r.avg_confidence, 3)} | realized=${r.realized_count ?? 0}${r.realized_mae == null ? '' : ` / mae=${fmtNum(r.realized_mae, 2)}`}
        </div>
        <div class="history-actions">
          <button type="button" data-action="load" data-run-id="${r.run_id}">Load</button>
          <button type="button" data-action="compare" data-run-id="${r.run_id}">Compare</button>
        </div>
      </div>
    `).join('');
    box.querySelectorAll('button[data-action]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const runId = Number(btn.getAttribute('data-run-id'));
        const action = btn.getAttribute('data-action');
        if (!runId || !action) return;
        try {
          const data = await getRunById(runId);
          if (!data || data.status) return;
          if (action === 'load') {
            state.selectedRunId = runId;
            renderAll(data);
          } else if (action === 'compare') {
            state.compareRunId = runId;
            state.comparePayload = data;
            renderRunCompare();
          }
          renderRunHistory();
        } catch (e) {
          console.error(`[PredictVIX] ${action} run failed`, e);
          if (typeof UI !== 'undefined') UI.showToast(`Run ${action} failed: ${e.message}`, 'error', 3000);
        }
      });
    });
  }

  async function backfillActuals() {
    const btn = $('backfillActualsBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Backfilling...'; }
    try {
      const data = await apiPost('/prediction/vix/backfill-actuals', { run_limit: 1000 });
      await loadRuns();
      if (typeof UI !== 'undefined') {
        UI.showToast(`Backfill actuals: ${data.updated_rows ?? 0} rows updated`, 'success', 3000);
      }
    } catch (e) {
      console.error('[PredictVIX] backfill actuals failed', e);
      if (typeof UI !== 'undefined') UI.showToast(`Backfill failed: ${e.message}`, 'error', 4000);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Backfill Actuals'; }
    }
  }

  function renderAll(payload) {
    state.latest = payload;
    if (payload?.run_id) state.runCache[Number(payload.run_id)] = payload;
    if (payload?.run_id && state.selectedRunId == null) state.selectedRunId = Number(payload.run_id);
    renderMeta(payload);
    renderFeatureCards(payload);
    renderPredictionTable(payload);
    renderBacktest(payload);
    renderNewsFeatures(payload);
    renderRunCompare();
  }

  async function loadRuns() {
    try {
      const data = await apiGet('/prediction/vix/runs?limit=30');
      state.runs = Array.isArray(data?.items) ? data.items : [];
      if (state.selectedRunId == null && state.runs.length) state.selectedRunId = Number(state.runs[0].run_id);
      renderRunHistory();
      renderRunCompare();
    } catch (e) {
      console.error('[PredictVIX] runs load failed', e);
    }
  }

  function renderErrorRanking(items) {
    const el = $('errorRankingWrap');
    if (!el) return;
    if (!items || !items.length) {
      el.textContent = 'No realized predictions found (actual_vix is null for all loaded runs).';
      return;
    }
    el.innerHTML = `
      <table class="vix-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>H</th>
            <th>Date</th>
            <th class="text-right">Pred</th>
            <th class="text-right">Actual</th>
            <th class="text-right">AbsErr</th>
            <th>Regime</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          ${items.slice(0, 20).map(x => `
            <tr>
              <td>#${x.run_id}</td>
              <td>T+${x.horizon_day}</td>
              <td>${fmtDate(x.target_date)}</td>
              <td class="text-right">${fmtNum(x.predicted_vix)}</td>
              <td class="text-right">${fmtNum(x.actual_vix)}</td>
              <td class="text-right"><b>${fmtNum(x.abs_error)}</b></td>
              <td>${x.regime_bucket || '--'}</td>
              <td>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                  <button type="button" class="err-action-btn" data-action="load" data-run-id="${x.run_id}">Load</button>
                  <button type="button" class="err-action-btn" data-action="compare" data-run-id="${x.run_id}">Compare</button>
                </div>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
    el.querySelectorAll('.err-action-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const runId = Number(btn.getAttribute('data-run-id'));
        const action = btn.getAttribute('data-action');
        if (!runId || !action) return;
        try {
          const data = await getRunById(runId);
          if (!data || data.status) return;
          if (action === 'load') {
            state.selectedRunId = runId;
            renderAll(data);
          } else {
            state.compareRunId = runId;
            state.comparePayload = data;
            renderRunCompare();
          }
          renderRunHistory();
          if (typeof UI !== 'undefined') UI.showToast(`Run #${runId} ${action}ed`, 'success', 1500);
        } catch (e) {
          console.error('[PredictVIX] error ranking action failed', e);
          if (typeof UI !== 'undefined') UI.showToast(`Run action failed: ${e.message}`, 'error', 2500);
        }
      });
    });
  }

  async function analyzeRealizedErrors() {
    const btn = $('analyzeErrorsBtn');
    const el = $('errorRankingWrap');
    if (btn) { btn.disabled = true; btn.textContent = 'Analyzing...'; }
    if (el) el.textContent = 'Loading run details and computing realized error ranking...';
    try {
      if (!state.runs.length) await loadRuns();
      const targets = (state.runs || []).slice(0, 30);
      const loaded = [];
      for (const r of targets) {
        try {
          const full = await getRunById(r.run_id);
          if (full && !full.status) loaded.push(full);
        } catch (e) {
          console.error('[PredictVIX] analyze load run failed', r.run_id, e);
        }
      }
      const rows = [];
      for (const run of loaded) {
        for (const p of (run.predictions || [])) {
          const pred = Number(p.predicted_vix);
          const actual = Number(p.actual_vix);
          if (!Number.isFinite(pred) || !Number.isFinite(actual)) continue;
          rows.push({
            run_id: run.run_id,
            horizon_day: p.horizon_day,
            target_date: p.target_date,
            predicted_vix: pred,
            actual_vix: actual,
            abs_error: Math.abs(pred - actual),
            regime_bucket: p.regime_bucket,
          });
        }
      }
      rows.sort((a, b) => b.abs_error - a.abs_error);
      renderErrorRanking(rows);
      if (typeof UI !== 'undefined') UI.showToast(`Error ranking built from ${loaded.length} runs`, 'success', 2500);
    } catch (e) {
      console.error('[PredictVIX] analyze errors failed', e);
      if (el) el.textContent = `Analyze failed: ${e.message}`;
      if (typeof UI !== 'undefined') UI.showToast(`Analyze failed: ${e.message}`, 'error', 3500);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Analyze'; }
    }
  }

  async function loadLatest() {
    try {
      const data = await apiGet('/prediction/vix/latest');
      if (data && !data.status) renderAll(data);
    } catch (e) {
      console.error('[PredictVIX] latest load failed', e);
    }
  }

  async function loadDefaultAsof() {
    try {
      const fresh = await apiGet('/system/data-freshness');
      const d = fresh?.datasets?.price_daily?.latest_trading_date;
      if (d) $('asofDate').value = d;
    } catch {
      const t = new Date();
      $('asofDate').value = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    }
  }

  async function runPrediction() {
    if (state.running) return;
    state.running = true;
    const btn = $('runBtn');
    btn.disabled = true;
    btn.textContent = 'Running... (training + backtest + save)';
    try {
      const body = {
        asof_date: $('asofDate').value || null,
        horizon_days: Number($('horizonDays').value || 5),
        backtest_years: Number($('backtestYears').value || 3),
        refresh_news: String($('refreshNews')?.value || 'true') === 'true',
      };
      const data = await apiPost('/prediction/vix/run', body);
      renderAll(data);
      await loadRuns();
      state.selectedRunId = Number(data?.run_id || state.selectedRunId);
      state.comparePayload = null;
      state.compareRunId = null;
      renderRunHistory();
      renderRunCompare();
      if (typeof UI !== 'undefined') UI.showToast('Predict VIX run completed and saved to DB', 'success', 2500);
    } catch (e) {
      console.error('[PredictVIX] run failed', e);
      if (typeof UI !== 'undefined') UI.showToast(`Predict VIX failed: ${e.message}`, 'error', 5000);
    } finally {
      state.running = false;
      btn.disabled = false;
      btn.textContent = 'Run Predict VIX';
    }
  }

  async function init() {
    $('runBtn')?.addEventListener('click', runPrediction);
    $('refreshRunsBtn')?.addEventListener('click', loadRuns);
    $('backfillActualsBtn')?.addEventListener('click', backfillActuals);
    $('analyzeErrorsBtn')?.addEventListener('click', analyzeRealizedErrors);
    await loadDefaultAsof();
    await loadLatest();
    await loadRuns();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
