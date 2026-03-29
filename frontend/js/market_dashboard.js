(function () {
  const API_BASE = window.CONFIG?.API_BASE || "http://localhost:8010/api/v1";

  function esc(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fmtVal(v) {
    if (v === null || v === undefined) return "-";
    if (Array.isArray(v)) return v.length ? v.join(", ") : "-";
    if (typeof v === "boolean") return v ? "達成" : "未達";
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function gateCls(g) {
    const v = String(g || "").toUpperCase();
    if (v === "ON") return "badge-success";
    if (v === "HALF") return "badge-warning";
    if (v === "OFF") return "badge-danger";
    return "badge-neutral";
  }

  function regimeCls(label) {
    if (label === "強気" || label === "買い可") return "badge-success";
    if (label === "注意" || label === "弱気") return "badge-warning";
    if (label === "新規買い停止") return "badge-danger";
    return "badge-neutral";
  }

  function badge(text, cls) {
    return `<span class="badge ${cls || "badge-neutral"}">${esc(text)}</span>`;
  }

  const SKIP_KEYS = new Set(["data_status"]);

  function ftdStatusCls(statusType) {
    if (statusType === "confirmed") return "badge-success";
    if (statusType === "failed" || statusType === "reset") return "badge-danger";
    if (statusType === "waiting_critical") return "badge-warning";
    if (statusType === "declining") return "badge-danger";
    return "badge-neutral";
  }

  function ftdRow(label, value, highlight) {
    const cls = highlight ? 'style="color:var(--color-danger);font-weight:700;"' : 'style="font-family:var(--font-mono);font-weight:600;"';
    const display = (value === null || value === undefined) ? "-" : String(value);
    return `<div class="kv-row"><span class="kv-key">${esc(label)}</span><span class="text-sm" ${cls}>${esc(display)}</span></div>`;
  }

  function renderFtdBlock(f, label) {
    if (!f) return `<div class="text-muted text-sm">${esc(label)}: データなし</div>`;
    const statusType = f.status_type || "";
    const isFailed = statusType === "failed" || statusType === "reset";
    const hasFtd = !!f.ftd_date;
    return `
      <div style="margin-bottom:12px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <span style="font-size:var(--fs-sm);font-weight:700;color:var(--text-muted);">${esc(label)}</span>
          ${badge(f.status || "-", ftdStatusCls(statusType))}
        </div>
        <div class="kv-list">
          ${ftdRow("Day1（反発起点）", f.day1_date)}
          ${ftdRow("現在のDay数", f.day_count != null ? `Day ${f.day_count}` : null)}
          ${ftdRow("FTD発生日", f.ftd_date)}
          ${ftdRow("FTD Day番号", f.ftd_day != null ? `Day ${f.ftd_day}` : null)}
          ${ftdRow("FTD上昇率", f.ftd_change_pct != null ? `+${f.ftd_change_pct}%` : null)}
          ${ftdRow("Day1安値割れ", f.day1_undercut ? "あり（リセット）" : "なし", f.day1_undercut)}
          ${hasFtd ? ftdRow("FTD後配布日", f.post_ftd_dd_date || "なし", !!f.post_ftd_dd_date) : ""}
          ${hasFtd ? ftdRow("FTD安値割れ", f.ftd_low_breached ? "あり（失敗兆候）" : "なし", f.ftd_low_breached) : ""}
        </div>
      </div>
    `;
  }

  function renderFtdSection(ftd) {
    if (!ftd) return '<div class="text-muted text-sm">データなし</div>';
    return `
      ${renderFtdBlock(ftd.qqq, "QQQ（NASDAQ代替）")}
      <div style="border-top:1px solid var(--border);margin:8px 0;"></div>
      ${renderFtdBlock(ftd.spy, "SPY（S&amp;P500代替）")}
    `;
  }

  function kvRows(obj) {
    if (!obj || typeof obj !== "object") return '<div class="text-muted text-sm">データなし</div>';
    const entries = Object.entries(obj).filter(([k]) => !SKIP_KEYS.has(k));
    if (!entries.length) return '<div class="text-muted text-sm">データなし</div>';
    return `<div class="kv-list">${entries.map(([k, v]) => {
      const display = fmtVal(v);
      const valCls = display === "-" ? "text-muted" : "";
      return `<div class="kv-row"><span class="kv-key">${esc(k)}</span><span class="text-sm ${valCls}" style="font-family:var(--font-mono);font-weight:600;">${esc(display)}</span></div>`;
    }).join("")}</div>`;
  }

  function render(data) {
    const summary = data?.summary || {};
    const sections = data?.sections || {};

    // Regime badge in panel-header
    const regimeBadgeEl = document.getElementById("mRegimeBadge");
    if (regimeBadgeEl) {
      regimeBadgeEl.innerHTML = badge(summary.market_regime || "-", regimeCls(summary.market_regime));
    }

    // Summary metrics
    const summaryContent = document.getElementById("mSummaryContent");
    if (summaryContent) {
      const triggers = Array.isArray(summary.triggers) ? summary.triggers : [];
      summaryContent.innerHTML = `
        <div class="summary-metrics">
          <div class="metric-box">
            <div class="metric-label">新規買い許可</div>
            <div class="metric-value">${badge(summary.new_buy_permission || "-", gateCls(summary.new_buy_permission))}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">推奨現金比率</div>
            <div class="metric-value">${esc(summary.recommended_cash_ratio ?? "-")}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">ブレイクアウト信頼度</div>
            <div class="metric-value">${esc(summary.breakout_reliability ?? "-")}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">ポジションサイズ上限</div>
            <div class="metric-value">${summary.recommended_position_size_pct != null ? esc(summary.recommended_position_size_pct) + "%" : "-"}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">VIX</div>
            <div class="metric-value">${esc(summary.vix_level ?? "-")}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">NASDAQ DD</div>
            <div class="metric-value">${esc(summary.nasdaq_dd_count_5w ?? "-")}</div>
          </div>
          <div class="metric-box">
            <div class="metric-label">SP500 DD</div>
            <div class="metric-value">${esc(summary.sp500_dd_count_5w ?? "-")}</div>
          </div>
        </div>
        ${triggers.length ? `
          <div class="trigger-row">
            <div class="kv-key" style="margin-bottom:6px;">発動中ルール</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;">
              ${triggers.map(t => badge(t, "badge-warning")).join("")}
            </div>
          </div>
        ` : ""}
      `;
    }

    // Section cards
    const sectionsEl = document.getElementById("mSections");
    if (sectionsEl) {
      const cards = [
        { title: "フォロースルーデー（FTD）分析", key: "ftd_analysis", custom: true },
        { title: "ヒンデンブルグオーメン", data: sections.hindenburg_omen },
        { title: "ディストリビューションデー（DD）", data: sections.distribution_days },
        { title: "市場内部構造（ブレッドス）", data: sections.breadth },
        { title: "クレジット / マクロ", data: sections.credit_macro },
        { title: "センチメント / オプション", data: sections.sentiment },
        { title: "市場ルール発動状態", data: sections.rules },
      ];
      sectionsEl.innerHTML = cards.map(c => {
        const body = c.custom
          ? renderFtdSection(sections[c.key])
          : kvRows(c.data);
        return `
          <div class="glass-panel">
            <div class="panel-header">
              <div class="panel-title">${esc(c.title)}</div>
            </div>
            ${body}
          </div>
        `;
      }).join("");
    }

    // Timestamp
    const lastUpdated = document.getElementById("lastUpdated");
    if (lastUpdated && data.generated_at) {
      const d = new Date(data.generated_at + "Z");
      lastUpdated.textContent = `更新: ${d.toLocaleString("ja-JP")}`;
    }
  }

  async function init() {
    // Phase 1: 複数 API を並列 fetch（集約 API は Phase 2 で実装予定）
    const [envResult, screenerResult, vixResult] = await Promise.allSettled([
      fetch(`${API_BASE}/market/environment`).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))),
      fetch(`${API_BASE}/screener?limit=10&sort=score`).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))),
      fetch(`${API_BASE}/market/vix-prediction`).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))),
    ]);

    if (envResult.status === "fulfilled") {
      render(envResult.value);
    } else {
      const el = document.getElementById("mSummary");
      if (el) el.innerHTML = `<div class="alert alert-danger">マーケット環境データ取得失敗: ${esc(envResult.reason?.message || "")}</div>`;
      console.error("[Market Dashboard] market/environment failed", envResult.reason);
    }

    if (screenerResult.status === "fulfilled") {
      renderTopPicks(screenerResult.value);
    } else {
      renderPanelError("mTopPicks", "スクリーナー", screenerResult.reason?.message);
      console.warn("[Market Dashboard] screener fetch failed", screenerResult.reason);
    }

    if (vixResult.status === "fulfilled") {
      renderVixPanel(vixResult.value);
    } else {
      renderPanelError("mVixPanel", "VIX予測", vixResult.reason?.message);
      console.warn("[Market Dashboard] vix-prediction fetch failed", vixResult.reason);
    }
  }

  function renderPanelError(elId, label, reason) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = `<div class="text-muted text-sm" style="color:#f87171;">⚠ ${esc(label)}取得失敗<br><span style="font-size:0.72em;opacity:0.7;">${esc(reason || "")}</span></div>`;
  }

  function renderTopPicks(data) {
    const el = document.getElementById("mTopPicks");
    if (!el) return;
    const items = Array.isArray(data) ? data : (data?.results || data?.stocks || []);
    if (!items.length) {
      el.innerHTML = '<div class="text-muted text-sm">データなし</div>';
      return;
    }
    el.innerHTML = `<div class="kv-list">${items.slice(0, 10).map(s => {
      const sym = s.symbol_key || s.symbol || "-";
      const score = s.score != null ? Number(s.score).toFixed(1) : "-";
      const name = s.name ? ` <span style="color:var(--text-muted);font-size:0.75em;">${esc(s.name)}</span>` : "";
      return `<div class="kv-row"><span class="kv-key">${esc(sym)}</span><span class="text-sm" style="font-family:var(--font-mono);font-weight:600;">${esc(score)}${name}</span></div>`;
    }).join("")}</div>`;
  }

  function renderVixPanel(data) {
    const el = document.getElementById("mVixPanel");
    if (!el) return;
    if (!data || typeof data !== "object") {
      el.innerHTML = '<div class="text-muted text-sm">データなし</div>';
      return;
    }
    // VIX予測の主要フィールドのみ表示（全フィールドは冗長）
    const SHOW_KEYS = ["vix_current", "vix_pred_5d", "vix_pred_class", "vix_regime", "term_structure", "updated_at"];
    const subset = Object.fromEntries(
      Object.entries(data).filter(([k]) => SHOW_KEYS.includes(k) || k.startsWith("vix_"))
    );
    el.innerHTML = kvRows(Object.keys(subset).length ? subset : data);
  }

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", init)
    : init();
})();
