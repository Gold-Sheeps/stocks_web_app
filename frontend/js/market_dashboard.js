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
        { title: "ヒンデンブルグオーメン", data: sections.hindenburg_omen },
        { title: "ディストリビューションデー（DD）", data: sections.distribution_days },
        { title: "市場内部構造（ブレッドス）", data: sections.breadth },
        { title: "クレジット / マクロ", data: sections.credit_macro },
        { title: "センチメント / オプション", data: sections.sentiment },
        { title: "市場ルール発動状態", data: sections.rules },
      ];
      sectionsEl.innerHTML = cards.map(c => `
        <div class="glass-panel">
          <div class="panel-header">
            <div class="panel-title">${esc(c.title)}</div>
          </div>
          ${kvRows(c.data)}
        </div>
      `).join("");
    }

    // Timestamp
    const lastUpdated = document.getElementById("lastUpdated");
    if (lastUpdated && data.generated_at) {
      const d = new Date(data.generated_at + "Z");
      lastUpdated.textContent = `更新: ${d.toLocaleString("ja-JP")}`;
    }
  }

  async function init() {
    try {
      const res = await fetch(`${API_BASE}/market/environment`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      render(data);
    } catch (e) {
      const el = document.getElementById("mSummary");
      if (el) el.innerHTML = `<div class="alert alert-danger">データ取得失敗: ${esc(e.message)}</div>`;
      console.error("[Market Dashboard] load failed", e);
    }
  }

  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", init)
    : init();
})();
