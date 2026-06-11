// ── Toast notification system ──────────────────────────────────────────────
(function initToasts() {
  const container = document.createElement("div");
  container.id = "toast-container";
  document.body.appendChild(container);
})();

const TOAST_ICONS = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };

function showToast(message, type = "info", title = null, duration = 4000) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;

  const defaultTitles = { success: "Success", error: "Error", warning: "Warning", info: "Info" };
  const displayTitle = title || defaultTitles[type] || "Notice";

  toast.innerHTML = `
    <span class="toast-icon">${TOAST_ICONS[type] || "ℹ"}</span>
    <div class="toast-body">
      <div class="toast-title">${escapeHtml(displayTitle)}</div>
      <div class="toast-msg">${escapeHtml(message)}</div>
    </div>`;

  toast.addEventListener("click", () => dismissToast(toast));
  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => dismissToast(toast), duration);
  }
  return toast;
}

function dismissToast(toast) {
  if (!toast || toast.classList.contains("removing")) return;
  toast.classList.add("removing");
  toast.addEventListener("animationend", () => toast.remove(), { once: true });
}

// ── State ──────────────────────────────────────────────────────────────────
const statusText = document.getElementById("statusText");
let aiEnabled      = false;
let aiRemediationMode = "read-write";
let aiRemediationNamespaces = "*";
let activeFindings = [];
let solvedFindings = [];
let currentTab     = "active";
let allPods        = [];
let modalTab       = "all";
let aiOutcomeByFinding = {};

// ── Helpers ────────────────────────────────────────────────────────────────
function formatDate(value) {
  if (!value) return "Never";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function duration(from, to) {
  if (!from || !to) return "—";
  const ms = new Date(to) - new Date(from);
  if (ms < 0) return "—";
  const m = Math.floor(ms / 60000);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  return `${m}m`;
}

function escapeHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function list(items) {
  if (!items || items.length === 0) return "<p>No data.</p>";
  return `<ul>${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
}

function hasAiAdvisory(finding) {
  return Boolean(finding.needs_ai_analysis || finding.ai_used || finding.ai_analysis);
}

function rootCause(f) {
  return f.ai_analysis?.probable_root_cause || f.local_analysis?.reason || f.problem_type;
}

function findingNum(f) {
  return f.finding_number ? `#${f.finding_number}` : "—";
}

function statusBadge(status) {
  const labels = {
    open: "Open",
    remediating: "Applying Fix…",
    resolved: "✓ Solved",
    resolved_by_ai: "✓ Solved by AI",
    resolved_manually: "✓ Manually Solved",
    archived: "✓ Archived"
  };
  return `<span class="status-badge ${status}">${labels[status] || status}</span>`;
}

/**
 * Render a visual confidence bar.
 * @param {number} value  0.0 – 1.0
 */
function confidenceBar(value) {
  const pct = Math.round((value || 0) * 100);
  const tier = pct >= 80 ? "high" : pct >= 60 ? "medium" : "low";
  const color = tier === "high" ? "var(--accent)" : tier === "medium" ? "var(--medium)" : "var(--critical)";
  return `
    <div class="confidence-bar-wrap">
      <div class="confidence-bar">
        <div class="confidence-bar-fill ${tier}" style="width:${pct}%"></div>
      </div>
      <span class="confidence-label" style="color:${color}">${pct}%</span>
    </div>`;
}

/**
 * Render AI audit history entries.
 * @param {Array} history
 */
function renderAuditHistory(history) {
  if (!history || history.length === 0) return "<p style='font-size:13px;color:var(--muted);'>No AI calls recorded for this finding.</p>";
  const items = history.map(entry => {
    const time = entry.timestamp ? formatDate(entry.timestamp) : "—";
    const model = entry.model || "unknown model";
    const outcome = entry.outcome || "unknown";
    const text = entry.summary || entry.error || (outcome === "skipped" ? "Skipped — rate limit or disabled" : "No summary");
    return `
      <li class="ai-audit-entry ${outcome}">
        <span class="entry-time">${escapeHtml(time)}</span>
        <div>
          <span class="entry-text">${escapeHtml(text)}</span>
          <span class="entry-model"> · ${escapeHtml(model)}</span>
        </div>
      </li>`;
  }).reverse(); // newest first
  return `<ul class="ai-audit-timeline">${items.join("")}</ul>`;
}

/**
 * Render crash trend badge if events > 1.
 */
function crashTrendBadge(trend) {
  if (!trend || trend.event_count <= 1) return "";
  return `<span class="crash-trend-badge">🔁 ${trend.event_count}× in 24h</span>`;
}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(tab) {
  currentTab = tab;
  document.getElementById("panel-active").style.display = tab === "active" ? "" : "none";
  document.getElementById("panel-solved").style.display = tab === "solved" ? "" : "none";
  document.getElementById("tab-active").classList.toggle("active", tab === "active");
  document.getElementById("tab-solved").classList.toggle("active", tab === "solved");
  document.getElementById("detailPanel").innerHTML =
    '<div class="empty-state">Select a finding to inspect evidence and remediation steps.</div>';
}
window.switchTab = switchTab;

// ── Detail panel ──────────────────────────────────────────────────────────
function renderDetails(finding, isArchived = false, focusSection = "") {
  const proposedFix = finding.ai_analysis?.proposed_fix || finding.local_analysis?.proposed_fix;
  const aiNeedsAnalysis = finding.needs_ai_analysis;
  const canTryAiResolve = aiEnabled && !finding.resolved && finding.status !== "remediating";
  const panel = document.getElementById("detailPanel");
  const aiOutcome = aiOutcomeByFinding[finding.id];
  const trend = finding.evidence?.crash_trend_24h;

  // Resolved banner
  let banner = "";
  if (finding.resolved && finding.resolved_at) {
    let titleText = "Problem Resolved";
    if (finding.status === "resolved_by_ai") {
      titleText = "Solved by AI";
    } else if (finding.status === "resolved_manually") {
      titleText = "Manually Solved";
    }
    banner = `
      <div class="resolved-banner">
        <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <div>
          <strong>${titleText}</strong>
          <div style="font-size:12px; margin-top:2px; color: var(--muted);">Resolved at ${formatDate(finding.resolved_at)}</div>
        </div>
      </div>`;
  }

  // Archive meta
  let archiveMeta = "";
  if (isArchived) {
    archiveMeta = `
      <dl class="archive-meta">
        <dt>First detected</dt><dd>${formatDate(finding.first_seen)}</dd>
        <dt>Last seen</dt><dd>${formatDate(finding.last_seen)}</dd>
        <dt>Resolved at</dt><dd>${formatDate(finding.resolved_at)}</dd>
        <dt>Open duration</dt><dd>${duration(finding.first_seen, finding.resolved_at)}</dd>
      </dl>`;
  }

  // AI action / remediation buttons
  let aiActionHtml = "";
  if (canTryAiResolve) {
    aiActionHtml = `
      <div style="margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <button id="aiResolveBtn" class="primary" style="background:#5ec2ff;border-color:#5ec2ff;color:#041018;" onclick="applyAiRemediation('${finding.id}', this)">Solve with AI</button>
        <button id="aiStreamBtn" style="background:transparent;border-color:var(--accent);color:var(--accent);" onclick="streamAiAnalysis('${finding.id}', this)">Re-analyze with AI ↗</button>
        <span style="font-size:12px;color:var(--muted);">AI will analyze and present a self-healing plan for your approval.</span>
      </div>`;
  } else if (!aiEnabled && !finding.resolved && finding.status !== "remediating") {
    aiActionHtml = `
      <div style="margin-bottom:14px;font-size:13px;color:#ffab00;padding:12px;background:rgba(255, 171, 0, 0.08);border:1px solid #ffab00;border-radius:6px;">
        ⚠️ AI is disabled. Enable Pioneer AI Analysis in settings to inspect suggestions.
      </div>`;
  }

  const aiOutcomeHtml = aiOutcome
    ? `<div style="margin-bottom:16px;padding:14px 16px;border-radius:10px;background:var(--card-bg);border-left:4px solid ${aiOutcome.type === "error" ? "#ff6b6b" : "#ffab00"};">
        <div style="font-weight:700;margin-bottom:6px;">AI Decision: ${escapeHtml(aiOutcome.title)}</div>
        <div style="font-size:14px;color:var(--text);">${escapeHtml(aiOutcome.message)}</div>
       </div>`
    : "";

  // Proposed fix box
  let remediationHtml = "";
  if (proposedFix && !isArchived && !finding.resolved && finding.status !== "remediating") {
    const hasExecutableFix = proposedFix.resource_kind && proposedFix.resource_name && proposedFix.namespace;
    if (hasExecutableFix) {
      const isRolloutRestart = proposedFix.action === "rollout_restart";
      const patchDisplay = isRolloutRestart
        ? `<code style="font-size:13px;">kubectl rollout restart ${escapeHtml(proposedFix.resource_kind?.toLowerCase() || "deployment")}/${escapeHtml(proposedFix.resource_name)} -n ${escapeHtml(proposedFix.namespace)}</code>`
        : `<pre>${escapeHtml(JSON.stringify(proposedFix.patch_body, null, 2))}</pre>`;
      remediationHtml = `
        <div class="remediation-box" style="margin-bottom:20px;padding:15px;border-radius:8px;background:var(--card-bg);border-left:4px solid var(--accent-success);">
          <h4 style="margin-top:0;color:var(--accent-success);">Proposed Action</h4>
          <p><strong>Explanation:</strong> ${escapeHtml(proposedFix.explanation)}</p>
          ${patchDisplay}
        </div>`;
    } else {
      const explanation = proposedFix.explanation || proposedFix.note || JSON.stringify(proposedFix);
      remediationHtml = `
        <div class="remediation-box" style="margin-bottom:20px;padding:15px;border-radius:8px;background:var(--card-bg);border-left:4px solid var(--muted);">
          <h4 style="margin-top:0;color:var(--muted);">Suggested Remediation Note</h4>
          <p>${escapeHtml(explanation)}</p>
        </div>`;
    }
  }

  // AI error display
  const aiErrorHtml = finding.ai_error
    ? `<div class="ai-error-box"><strong>AI Error:</strong> ${escapeHtml(finding.ai_error)}</div>`
    : "";

  // AI analysis section
  let aiSectionContent = "";
  if (finding.ai_analysis) {
    aiSectionContent = `
      <p>${escapeHtml(finding.ai_analysis.summary || "AI analysis available.")}</p>
      <div class="confidence-bar-wrap" style="margin:8px 0 12px;">
        <span style="font-size:12px;color:var(--muted);min-width:80px;">AI Confidence</span>
        ${confidenceBar(parseFloat(finding.ai_analysis.confidence) || 0)}
      </div>
      <dl class="archive-meta">
        <dt>Auto Apply</dt><dd>${finding.ai_analysis.should_auto_apply ? "Yes" : "No"}</dd>
      </dl>
      <p>${escapeHtml(finding.ai_analysis.probable_root_cause || finding.ai_analysis.manual_fix_summary || "No additional AI root cause available.")}</p>`;
  } else {
    const pendingMsg = aiNeedsAnalysis
      ? (aiEnabled
          ? (finding.ai_error || "AI analysis is pending. Run a scan or click Re-analyze with AI.")
          : "AI analysis is disabled. Enable Pioneer AI Analysis in settings.")
      : "This finding is fully handled by rules; AI analysis is optional.";
    aiSectionContent = `<p>${escapeHtml(pendingMsg)}</p>${aiErrorHtml}`;
  }

  panel.innerHTML = `
    ${banner}
    <div class="detail-title">
      <div>
        <h2>${escapeHtml(finding.problem_type)}</h2>
        <p>${escapeHtml(finding.namespace)} / ${escapeHtml(finding.resource_name)}</p>
      </div>
      <span class="badge ${escapeHtml(finding.severity)}">${escapeHtml(finding.severity)}</span>
    </div>
    <div class="kv">
      <span>Finding</span><strong>${findingNum(finding)}</strong>
      <span>Status</span><strong>${statusBadge(isArchived ? "archived" : finding.status)}</strong>
      <span>AI status</span><strong>${finding.ai_used ? "Used" : finding.ai_error ? "Error" : (finding.needs_ai_analysis ? "Pending" : "Local only")}</strong>
      <span>First seen</span><strong>${formatDate(finding.first_seen)}</strong>
      <span>Last seen</span><strong>${formatDate(finding.last_seen)}</strong>
      ${finding.resolved_at ? `<span>Resolved</span><strong>${formatDate(finding.resolved_at)}</strong>` : ""}
      <span>Confidence</span>
      <strong style="display:block;">${confidenceBar(finding.confidence)}</strong>
    </div>
    ${trend && trend.event_count > 1 ? `<div style="margin-top:10px;">${crashTrendBadge(trend)} <span style="font-size:12px;color:var(--muted);margin-left:6px;">Detected ${trend.event_count} times in the last 24h (max ${trend.max_restarts} restarts)</span></div>` : ""}
    ${archiveMeta}
    <h3 id="rulesSection">Detected by Rules</h3>
    <dl class="archive-meta">
      <dt>Rule ID</dt><dd>${escapeHtml(finding.rule_id || finding.local_analysis?.rule_id || "n/a")}</dd>
      <dt>Rule Confidence</dt><dd>${escapeHtml(String(finding.rule_confidence ?? finding.confidence ?? "n/a"))}</dd>
      <dt>Safe Auto Fix</dt><dd>${finding.rule_fix_available ? "Yes" : "No"}</dd>
      <dt>Needs AI</dt><dd>${aiNeedsAnalysis ? "Yes" : "No"}</dd>
    </dl>
    <p>${escapeHtml(finding.local_analysis?.reason || finding.problem_type || "No rule summary available.")}</p>
    ${list(finding.local_analysis?.evidence || [])}
    <h3 id="aiSection">AI Analysis</h3>
    ${aiSectionContent}
    <h3 id="actionPlanSection">Action Plan</h3>
    ${aiOutcomeHtml}
    ${aiActionHtml}
    ${remediationHtml}
    ${list(finding.ai_analysis?.action_plan || finding.ai_analysis?.recommended_actions || finding.recommended_actions)}
    <h3>Commands to verify</h3>
    ${list(finding.ai_analysis?.commands_to_verify || finding.commands_to_verify)}
    <h3>Prevention</h3>
    ${list(finding.ai_analysis?.prevention || [])}
    <h3>AI Audit History</h3>
    ${renderAuditHistory(finding.ai_history)}
    <h3>Evidence</h3>
    <pre>${escapeHtml(JSON.stringify(finding.evidence, null, 2))}</pre>
    <h3>Raw Analysis</h3>
    <pre>${escapeHtml(JSON.stringify({ rules: finding.local_analysis, ai: finding.ai_analysis || { note: finding.ai_error || "Not requested" } }, null, 2))}</pre>
  `;

  if (focusSection) {
    const target = document.getElementById(focusSection);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

// ── AI Streaming analysis ──────────────────────────────────────────────────
async function streamAiAnalysis(findingId, btn) {
  const panel = document.getElementById("detailPanel");
  const aiSection = document.getElementById("aiSection");
  if (!aiSection) return;

  const indicator = document.createElement("div");
  indicator.className = "ai-streaming-indicator";
  indicator.innerHTML = `
    <div class="ai-streaming-dots"><span></span><span></span><span></span></div>
    <span id="streamStatusMsg">Connecting to AI…</span>`;
  aiSection.insertAdjacentElement("afterend", indicator);

  if (btn) { btn.disabled = true; btn.textContent = "Analyzing…"; }

  try {
    const source = new EventSource(`/api/findings/${findingId}/ai-stream`);

    source.addEventListener("status", (e) => {
      const data = JSON.parse(e.data);
      const msg = document.getElementById("streamStatusMsg");
      if (msg) msg.textContent = data.message || "Processing…";
    });

    source.addEventListener("result", (e) => {
      source.close();
      indicator.remove();
      const updated = JSON.parse(e.data);
      // Update the cached finding
      const idx = activeFindings.findIndex(f => f.id === findingId);
      if (idx !== -1) activeFindings[idx] = updated;
      renderDetails(updated, false, "aiSection");
      showToast("AI analysis complete.", "success", "AI Analysis");
    });

    source.addEventListener("error", (e) => {
      source.close();
      indicator.remove();
      let msg = "AI analysis failed.";
      try { msg = JSON.parse(e.data).message; } catch (_) {}
      showToast(msg, "error", "AI Error", 6000);
      if (btn) { btn.disabled = false; btn.textContent = "Re-analyze with AI ↗"; }
    });

    source.onerror = () => {
      source.close();
      indicator.remove();
      showToast("Connection to AI stream lost.", "error", "AI Error");
      if (btn) { btn.disabled = false; btn.textContent = "Re-analyze with AI ↗"; }
    };
  } catch (err) {
    indicator.remove();
    showToast(err.message || String(err), "error", "AI Error");
    if (btn) { btn.disabled = false; btn.textContent = "Re-analyze with AI ↗"; }
  }
}
window.streamAiAnalysis = streamAiAnalysis;

// ── Remediation polling ────────────────────────────────────────────────────
let remediationPollTimer = null;

function startRemediationPolling() {
  if (remediationPollTimer) return;
  statusText.textContent = "Watching fix progress…";
  remediationPollTimer = setInterval(async () => {
    await load().catch(() => {});
    const stillRemediating = activeFindings.some(f => f.status === "remediating");
    if (!stillRemediating) {
      clearInterval(remediationPollTimer);
      remediationPollTimer = null;
    }
  }, 10000);
}

// ── AI interactive remediation ─────────────────────────────────────────────
async function applyAiRemediation(findingId, clickedBtn = null) {
  const btn = clickedBtn || document.getElementById("aiResolveBtn");
  const originalText = btn ? btn.textContent : "Solve with AI";
  if (btn) { btn.disabled = true; btn.textContent = "AI planning…"; }

  try {
    const r = await fetch(`/api/findings/${findingId}/ai-plan?_=${Date.now()}`);
    const res = await r.json();
    if (r.ok && res.success) {
      const plan = res.plan;
      const isAi = plan.is_ai ?? false;
      const title = isAi ? "AI Remediation Plan" : "Local Fallback Remediation";
      const themeColor = isAi ? "#5ec2ff" : "#ffab00";
      const bgColor = isAi ? "rgba(94,194,255,0.08)" : "rgba(255, 171, 0, 0.08)";
      const buttonLabel = isAi ? "Confirm and Apply Fix" : "Confirm and Apply Local Fix";

      let formHtml = `
        <div id="interactiveRemediationForm" style="margin-top:15px;margin-bottom:15px;padding:16px;border-radius:8px;background:${bgColor};border:1px solid ${themeColor};">
          <h4 style="margin-top:0;color:${themeColor};margin-bottom:8px;">${escapeHtml(title)}</h4>
          <p style="font-size:13px;margin-bottom:14px;line-height:1.4;color:var(--text);">${escapeHtml(plan.explanation)}</p>
          <form id="aiExecuteForm" onsubmit="executeAiRemediation(event, '${findingId}')">`;

      plan.inputs.forEach(input => {
        if (input.type === "textarea") {
          formHtml += `<div style="margin-bottom:14px;">
            <label style="display:block;font-size:12px;color:var(--muted);margin-bottom:4px;font-weight:bold;">${escapeHtml(input.label)}</label>
            <textarea name="${escapeHtml(input.name)}" style="width:100%;height:80px;font-family:monospace;font-size:12px;padding:10px;background:var(--panel-2);border:1px solid var(--line);border-radius:6px;color:var(--text);box-sizing:border-box;margin-bottom:4px;">${escapeHtml(input.value)}</textarea>
            <span style="display:block;font-size:11px;color:var(--muted);">${escapeHtml(input.description)}</span>
          </div>`;
        } else if (input.type === "checkbox") {
          formHtml += `
            <div style="background: rgba(94, 194, 255, 0.05); border: 1px dashed ${themeColor}; border-radius: 8px; padding: 14px; margin-top: 12px; margin-bottom: 12px; display: flex; align-items: flex-start; gap: 12px; box-shadow: inset 0 1px 3px rgba(0,0,0,0.2);">
              <input type="checkbox" id="auth-check-${findingId}" name="${escapeHtml(input.name)}" ${input.value === "true" ? "checked" : ""} style="width: 18px; height: 18px; margin-top: 2px; cursor: pointer; accent-color: ${themeColor}; flex-shrink: 0;">
              <label for="auth-check-${findingId}" style="font-size: 13px; font-weight: 600; color: var(--text); cursor: pointer; line-height: 1.4; user-select: none;">
                ${escapeHtml(input.description)}
              </label>
            </div>`;
        } else {
          formHtml += `<div style="margin-bottom:14px;">
            <label style="display:block;font-size:12px;color:var(--muted);margin-bottom:4px;font-weight:bold;">${escapeHtml(input.label)}</label>
            <input type="text" name="${escapeHtml(input.name)}" value="${escapeHtml(input.value)}" style="width:100%;padding:8px 12px;background:var(--panel-2);border:1px solid var(--line);border-radius:6px;color:var(--text);box-sizing:border-box;margin-bottom:4px;">
            <span style="display:block;font-size:11px;color:var(--muted);">${escapeHtml(input.description)}</span>
          </div>`;
        }
      });

      const finding = activeFindings.find(f => f.id === findingId);
      const isReadOnly = aiRemediationMode === "read-only";
      const namespaces = aiRemediationNamespaces.split(",").map(ns => ns.trim()).filter(Boolean);
      const isNamespaceAllowed = namespaces.includes("*") || (finding && namespaces.includes(finding.namespace));

      let actionButtonsHtml = "";
      if (isReadOnly) {
        actionButtonsHtml = `
          <div style="font-size:13px;color:#ffab00;padding:12px;background:rgba(255,171,0,0.08);border:1px solid #ffab00;border-radius:8px;margin-bottom:12px;width:100%;font-weight:600;">
            ⚠️ Agent is in Read-Only mode. Automatic execution is disabled.
          </div>
          <button type="button" class="tab-btn" onclick="cancelAiRemediation('${findingId}')" style="padding:10px 18px;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--text);border-radius:6px;cursor:pointer;font-weight:600;">Close</button>
        `;
      } else if (!isNamespaceAllowed) {
        actionButtonsHtml = `
          <div style="font-size:13px;color:#ff4444;padding:12px;background:rgba(255,68,68,0.08);border:1px solid #ff4444;border-radius:8px;margin-bottom:12px;width:100%;font-weight:600;">
            ⚠️ Remediation is not authorized in namespace '${escapeHtml(finding ? finding.namespace : "")}' by settings policy.
          </div>
          <button type="button" class="tab-btn" onclick="cancelAiRemediation('${findingId}')" style="padding:10px 18px;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--text);border-radius:6px;cursor:pointer;font-weight:600;">Close</button>
        `;
      } else {
        actionButtonsHtml = `
          <button type="submit" class="primary" style="background:${themeColor};border-color:${themeColor};color:#041018;padding:10px 20px;font-size:13px;font-weight:800;border-radius:6px;box-shadow: 0 4px 12px rgba(57,196,165,0.25);cursor:pointer;display:inline-flex;align-items:center;gap:6px;transition:all 0.2s ease;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:2px;"><polyline points="20 6 9 17 4 12"/></svg>
            ${escapeHtml(buttonLabel)}
          </button>
          <button type="button" class="tab-btn" onclick="cancelAiRemediation('${findingId}')" style="padding:10px 20px;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--text);border-radius:6px;cursor:pointer;font-weight:600;transition:all 0.2s ease;">
            Cancel
          </button>
        `;
      }

      formHtml += `
            <div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;width:100%;">
              ${actionButtonsHtml}
            </div>
          </form>
        </div>`;

      const existingForm = document.getElementById("interactiveRemediationForm");
      if (existingForm) {
        existingForm.outerHTML = formHtml;
      } else {
        const anchor = document.getElementById("actionPlanSection") || document.getElementById("aiSection");
        if (anchor) {
          anchor.insertAdjacentHTML("afterend", formHtml);
        } else {
          document.getElementById("detailPanel").innerHTML += formHtml;
        }
      }
      document.getElementById("interactiveRemediationForm")?.scrollIntoView({ behavior: "smooth", block: "nearest" });

    } else {
      const msg = res.message || res.detail || "No automatic remediation available. Please resolve manually.";
      const manualHtml = `
        <div id="interactiveRemediationForm" style="margin-top:15px;margin-bottom:15px;padding:16px;border-radius:8px;background:rgba(255,171,0,0.08);border:1px solid #ffab00;">
          <h4 style="margin-top:0;color:#ffab00;margin-bottom:8px;">Manual Action Required</h4>
          <p style="font-size:13px;line-height:1.4;color:var(--text);margin-bottom:12px;">${escapeHtml(msg)}</p>
          <button type="button" class="tab-btn" onclick="cancelAiRemediation('${findingId}')" style="padding:6px 12px;font-size:12px;border:1px solid var(--line);background:transparent;color:var(--text);">Close</button>
        </div>`;
      const existingForm = document.getElementById("interactiveRemediationForm");
      if (existingForm) {
        existingForm.outerHTML = manualHtml;
      } else {
        const anchor = document.getElementById("actionPlanSection");
        if (anchor) anchor.insertAdjacentHTML("afterend", manualHtml);
        else document.getElementById("detailPanel").innerHTML += manualHtml;
      }
      document.getElementById("interactiveRemediationForm")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  } catch (err) {
    showToast(err.message || String(err), "error", "Network Error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalText; }
  }
}
window.applyAiRemediation = applyAiRemediation;

async function executeAiRemediation(event, findingId) {
  event.preventDefault();
  const form = event.target;
  const submitBtn = form.querySelector('button[type="submit"]');
  const originalText = submitBtn ? submitBtn.textContent : "Confirm and Apply Fix";
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Applying Fix…"; }

  const finding = activeFindings.find(f => f.id === findingId);
  const originalStatus = finding ? finding.status : "open";

  // Transition to remediating (Applying Fix) state immediately in the UI
  if (finding) {
    finding.status = "remediating";
    renderTable(activeFindings);
    renderDetails(finding);
  }

  const formData = new FormData(form);
  const inputs = {};
  formData.forEach((value, key) => {
    const inputElement = form.querySelector(`[name="${key}"]`);
    if (inputElement && inputElement.type === "checkbox") {
      inputs[key] = String(inputElement.checked);
    } else {
      inputs[key] = value;
    }
  });

  let executionSuccess = false;
  let errorDetail = "";
  let successMessage = "";

  try {
    const r = await fetch(`/api/findings/${findingId}/ai-execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs }),
    });
    const res = await r.json();
    if (r.ok && res.success) {
      executionSuccess = true;
      successMessage = res.message || "Fix applied successfully.";
    } else {
      errorDetail = res.detail || "Remediation failed.";
    }
  } catch (err) {
    errorDetail = err.message || String(err);
  }

  // Run immediate scan to verify status, whether execution succeeded or failed!
  statusText.textContent = "Running scan to verify status…";
  try {
    await fetch("/api/scan", { method: "POST" });
  } catch (scanErr) {
    console.error("Scan verification failed:", scanErr);
  }

  // Load updated data
  await load();

  if (executionSuccess) {
    document.getElementById("interactiveRemediationForm")?.remove();
    showToast(successMessage, "success", "Remediation Applied");
    startRemediationPolling();
  } else {
    // Revert status if execution failed
    if (finding) {
      finding.status = originalStatus;
      renderTable(activeFindings);
      renderDetails(finding);
    }
    showToast(errorDetail, "error", "Remediation Error", 6000);
  }
}
window.executeAiRemediation = executeAiRemediation;

function cancelAiRemediation(findingId) {
  const current = activeFindings.find(f => f.id === findingId) || solvedFindings.find(f => f.id === findingId);
  if (current) {
    const isSolved = solvedFindings.some(f => f.id === findingId);
    renderDetails(current, isSolved);
  } else {
    document.getElementById("interactiveRemediationForm")?.remove();
  }
}
window.cancelAiRemediation = cancelAiRemediation;

// ── Render active table ────────────────────────────────────────────────────
function renderTable(findings) {
  const table = document.getElementById("findingsTable");
  table.innerHTML = "";

  if (findings.length === 0) {
    table.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:32px;">No active findings — cluster looks healthy 🎉</td></tr>`;
    return;
  }

  findings.forEach(f => {
    const isResolved = f.resolved;
    const isRemediating = f.status === "remediating";
    const canShowAiResolve = aiEnabled && !isResolved && !isRemediating;
    const rowClass = isResolved ? "row-resolved" : isRemediating ? "row-remediating" : "";
    const row = document.createElement("tr");
    if (rowClass) row.className = rowClass;

    let fixBtnHtml = "";
    if (isRemediating) {
      fixBtnHtml = `<button type="button" class="fix-btn" disabled style="background:var(--muted);color:#07110f;border-color:transparent;margin-left:6px;opacity:0.6;cursor:not-allowed;">Applying…</button>`;
    } else if (canShowAiResolve) {
      fixBtnHtml = `<button type="button" class="ai-resolve-btn" style="background:#5ec2ff;color:#041018;border-color:#5ec2ff;margin-left:6px;">Solve with AI</button>`;
    }

    row.innerHTML = `
      <td class="finding-num">${findingNum(f)}</td>
      <td><span class="badge ${escapeHtml(f.severity)}">${escapeHtml(f.severity)}</span></td>
      <td>${escapeHtml(f.namespace)}</td>
      <td>${escapeHtml(f.resource_kind)}/${escapeHtml(f.resource_name)}</td>
      <td>${escapeHtml(f.problem_type)}</td>
      <td>${escapeHtml(rootCause(f)).slice(0, 100)}</td>
      <td>${statusBadge(f.status)}</td>
      <td>${f.ai_used ? "✓" : f.ai_error ? "⚠" : "—"}</td>
      <td>${formatDate(f.last_seen)}</td>
      <td style="white-space:nowrap;">
        <button type="button" class="open-btn">Open</button>
        ${fixBtnHtml}
      </td>`;

    row.querySelector(".open-btn").addEventListener("click", () => renderDetails(f));
    if (canShowAiResolve) {
      row.querySelector(".ai-resolve-btn").addEventListener("click", async (evt) => {
        applyAiRemediation(f.id, evt.currentTarget);
      });
    }
    table.appendChild(row);
  });
}

// ── Render solved table ────────────────────────────────────────────────────
function renderSolvedTable(findings) {
  const table = document.getElementById("solvedTable");
  table.innerHTML = "";

  if (findings.length === 0) {
    table.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:32px;">No solved problems yet — fix an issue to see it here ✨</td></tr>`;
    return;
  }

  findings.forEach(f => {
    const row = document.createElement("tr");
    row.className = "row-archived";
    row.innerHTML = `
      <td class="finding-num">${findingNum(f)}</td>
      <td><span class="badge ${escapeHtml(f.severity)}">${escapeHtml(f.severity)}</span></td>
      <td>${escapeHtml(f.namespace)}</td>
      <td>${escapeHtml(f.resource_kind)}/${escapeHtml(f.resource_name)}</td>
      <td>${escapeHtml(f.problem_type)}</td>
      <td style="font-size:12px;color:var(--muted);">${escapeHtml(rootCause(f)).slice(0, 80)}</td>
      <td>${statusBadge(f.status)}</td>
      <td>${formatDate(f.resolved_at)}</td>
      <td>${duration(f.first_seen, f.resolved_at)}</td>
      <td><button type="button" class="open-btn">Open</button></td>`;

    row.querySelector(".open-btn").addEventListener("click", () => renderDetails(f, true));
    table.appendChild(row);
  });
}

// ── Main load ──────────────────────────────────────────────────────────────
async function load() {
  statusText.textContent = "Refreshing…";
  try {
    const [summaryRes, findingsRes, solvedRes, configRes] = await Promise.all([
      fetch(`/api/summary?_=${Date.now()}`),
      fetch(`/api/findings?_=${Date.now()}`),
      fetch(`/api/findings/resolved?_=${Date.now()}`),
      fetch(`/api/config?_=${Date.now()}`),
    ]);

    const config = await configRes.json();
    // ai_ready = enabled AND key present AND endpoint configured
    aiEnabled = config.ai_ready ?? false;
    aiRemediationMode = config.ai_remediation_mode ?? "read-write";
    aiRemediationNamespaces = config.ai_remediation_namespaces ?? "*";

    // Update scan button text based on AI status
    const scanBtn = document.getElementById("scanBtn");
    if (scanBtn) {
      scanBtn.textContent = aiEnabled ? "Run Scan with AI" : "Run Scan";
    }

    // AI status text + warning badge
    const aiStatusText = document.getElementById("aiStatusText");
    if (aiStatusText) {
      const statusMap = {
        active:      { text: "Active",               color: "var(--accent)" },
        disabled:    { text: "Disabled (Local Only)", color: "var(--muted)" },
        no_key:      { text: "⚠ No API Key",          color: "var(--critical)" },
        no_endpoint: { text: "⚠ No Endpoint",         color: "var(--critical)" },
        invalid_key: { text: "⚠ Invalid API Key",     color: "var(--critical)" },
      };
      const s = statusMap[config.ai_status] || statusMap.disabled;
      aiStatusText.textContent = s.text;
      aiStatusText.style.color = s.color;
    }

    // Persistent warning banner when AI is toggled ON but key/endpoint missing or invalid
    let warnBanner = document.getElementById("aiKeyWarningBanner");
    const needsWarning = config.ai_enabled && !config.ai_ready;
    if (needsWarning) {
      if (!warnBanner) {
        warnBanner = document.createElement("div");
        warnBanner.id = "aiKeyWarningBanner";
        warnBanner.style.cssText = "position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#ff4444;color:#fff;padding:12px 20px;font-size:14px;font-weight:600;display:flex;align-items:center;gap:12px;box-shadow:0 -2px 12px rgba(0,0,0,0.3);";
        document.body.appendChild(warnBanner);
      }
      
      let warningText = "";
      if (config.ai_status === "no_key") {
        warningText = "<strong>AI is enabled but the API key is missing.</strong> Add a Pioneer API key in the settings panel or disable AI.";
      } else if (config.ai_status === "invalid_key") {
        warningText = "<strong>AI is enabled but the API key is invalid.</strong> The key could not be verified. Enter a valid API key in the settings panel.";
      } else {
        warningText = "<strong>AI is enabled but the endpoint is not configured.</strong> Check the PIONEER_ENDPOINT environment variable.";
      }

      warnBanner.innerHTML = `
        <span style="font-size:20px;">⚠️</span>
        <span>${warningText}</span>
        <button onclick="document.getElementById('aiKeyWarningBanner').remove()" style="margin-left:auto;background:transparent;border:1px solid #fff;color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px;">Dismiss</button>`;
    } else if (warnBanner) {
      warnBanner.remove();
    }

    // Settings form (init once)
    const scanInput = document.getElementById("setting_scan_interval");
    if (scanInput && !scanInput.dataset.initialized) {
      scanInput.value = config.scan_interval_seconds ?? 600;
      document.getElementById("setting_ai_min_severity").value = config.ai_min_severity ?? "High";
      document.getElementById("setting_ai_rate_limit").value = config.ai_rate_limit_per_scan ?? 5;
      document.getElementById("setting_log_line_limit").value = config.log_line_limit ?? 150;
      document.getElementById("setting_ai_timeout").value = config.ai_timeout_seconds ?? 30;
      document.getElementById("setting_ai_enabled").checked = config.ai_enabled ?? false;
      populateModelSelect(config.pioneer_model);

      const apiKeyInput = document.getElementById("setting_pioneer_api_key");
      if (apiKeyInput) {
        apiKeyInput.placeholder = config.pioneer_api_key_configured ? "•••••••• (Configured)" : "Enter API Key";
        apiKeyInput.value = "";
      }
      const endpointInput = document.getElementById("setting_pioneer_endpoint");
      if (endpointInput) {
        endpointInput.value = config.pioneer_endpoint || "";
      }
      const modeSelect = document.getElementById("setting_ai_remediation_mode");
      if (modeSelect) {
        modeSelect.value = config.ai_remediation_mode ?? "read-write";
      }
      const nsInput = document.getElementById("setting_ai_remediation_namespaces");
      if (nsInput) {
        nsInput.value = config.ai_remediation_namespaces ?? "*";
      }

      scanInput.dataset.initialized = "true";
    }

    const summary  = await summaryRes.json();
    activeFindings = await findingsRes.json();
    solvedFindings = await solvedRes.json();
    allPods        = summary.resources || [];

    // Cluster name
    const clusterNameEl = document.getElementById("clusterName");
    if (clusterNameEl) clusterNameEl.textContent = summary.cluster_name || "Unknown";

    // Pod stats
    const podStatsEl = document.getElementById("podStats");
    if (podStatsEl) {
      if (summary.kubernetes_available) {
        const healthy   = summary.resources_healthy ?? 0;
        const total     = summary.resources_total ?? 0;
        const unhealthy = summary.resources_unhealthy ?? 0;
        podStatsEl.innerHTML = `<span style="color:var(--accent);font-weight:bold;">${healthy}</span> / ${total} Healthy ${unhealthy > 0 ? `<span style="color:var(--critical);font-weight:bold;margin-left:4px;">(${unhealthy} Unhealthy)</span>` : ""}`;
      } else {
        podStatsEl.textContent = "Unavailable";
        podStatsEl.style.color = "var(--critical)";
      }
    }

    // Summary counts
    document.getElementById("totalFindings").textContent = summary.total_findings ?? 0;
    document.getElementById("criticalCount").textContent = summary.by_severity?.Critical ?? 0;
    document.getElementById("highCount").textContent     = summary.by_severity?.High ?? 0;
    document.getElementById("mediumCount").textContent   = summary.by_severity?.Medium ?? 0;
    document.getElementById("lowCount").textContent      = summary.by_severity?.Low ?? 0;
    document.getElementById("aiRequests").textContent    = summary.ai_requests_total ?? 0;
    document.getElementById("lastScan").textContent      = summary.last_scan_timestamp
      ? formatDate(summary.last_scan_timestamp * 1000) : "Never";

    // Tab badges
    const activeBadge = document.getElementById("badge-active");
    const solvedBadge = document.getElementById("badge-solved");
    activeBadge.textContent = activeFindings.length;
    activeBadge.className   = `tab-badge ${activeFindings.length > 0 ? "has-items" : ""}`;
    solvedBadge.textContent = solvedFindings.length;
    solvedBadge.className   = "tab-badge solved-count";

    renderTable(activeFindings);
    renderSolvedTable(solvedFindings);

    if (document.getElementById("podsModal")?.style.display === "flex") {
      renderPodsModalList();
    }

    const activeCount      = activeFindings.filter(f => f.status !== "remediating").length;
    const remediatingCount = activeFindings.filter(f => f.status === "remediating").length;
    statusText.textContent = `${activeCount} active${remediatingCount > 0 ? `, ${remediatingCount} applying fix` : ""}`;

  } catch (err) {
    if (err.message && (err.message.includes("Failed to fetch") || err.message.includes("fetch") || err.message.includes("network") || err.message.includes("type"))) {
      statusText.innerHTML = `<span style="color:var(--muted);">⚠️ Offline (Reconnecting...)</span>`;
    } else {
      statusText.innerHTML = `<span style="color:var(--critical);">Error: ${escapeHtml(err.message || String(err))}</span>`;
    }
  }
}

// ── Events ─────────────────────────────────────────────────────────────────
document.getElementById("scanBtn").addEventListener("click", async () => {
  statusText.textContent = "Scan running…";
  try {
    const endpoint = aiEnabled ? "/api/scan-with-ai" : "/api/scan";
    await fetch(endpoint, { method: "POST" });
    await load();
    if (!aiEnabled) {
      showToast("Scan completed (without AI).", "info", "Scan");
    } else {
      showToast("Scan completed with AI analysis.", "success", "Scan");
    }
  } catch (err) {
    showToast(err.message || "Scan failed.", "error", "Scan Error");
  }
});

// Populate the AI Model dropdown. Loads the live model list from the provider
// (GET /api/ai-models) and falls back to the built-in catalog the backend
// returns when the provider is unreachable. Runs once during settings init.
async function populateModelSelect(current) {
  const sel = document.getElementById("setting_pioneer_model");
  if (!sel) return;

  let models = [];
  let cur = current ?? "claude-haiku-4-5";
  try {
    const r = await fetch("/api/ai-models");
    if (r.ok) {
      const data = await r.json();
      models = Array.isArray(data.models) ? data.models : [];
      cur = current ?? data.current ?? cur;
    }
  } catch (_) {
    // Offline — fall through to the minimal client-side default below.
  }

  if (!models.length) models = ["claude-haiku-4-5", "claude-opus-4-8", "claude-sonnet-4-6"];
  // Ensure the currently-selected model is always present and selectable.
  if (cur && !models.includes(cur)) models.unshift(cur);

  sel.innerHTML = models
    .map(m => `<option value="${escapeHtml(m)}"${m === cur ? " selected" : ""}>${escapeHtml(m)}</option>`)
    .join("");
  sel.value = cur;
}

async function saveSettings(event) {
  event.preventDefault();
  const form     = event.target;
  const formData = new FormData(form);

  const apiKeyVal = document.getElementById("setting_pioneer_api_key").value;
  const endpointVal = document.getElementById("setting_pioneer_endpoint").value;

  const data = {
    scan_interval_seconds:  parseInt(formData.get("scan_interval_seconds")),
    ai_min_severity:        formData.get("ai_min_severity"),
    ai_rate_limit_per_scan: parseInt(formData.get("ai_rate_limit_per_scan")),
    log_line_limit:         parseInt(formData.get("log_line_limit")),
    ai_timeout_seconds:     parseFloat(formData.get("ai_timeout_seconds")),
    pioneer_model:          formData.get("pioneer_model"),
    ai_enabled:             document.getElementById("setting_ai_enabled").checked,
    pioneer_endpoint:       endpointVal,
    ai_remediation_mode:    formData.get("ai_remediation_mode"),
    ai_remediation_namespaces: formData.get("ai_remediation_namespaces"),
  };

  if (apiKeyVal.trim() !== "") {
    data.pioneer_api_key = apiKeyVal;
  }

  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const res = await r.json();
    if (r.ok && res.success) {
      if (res.warning) {
        showToast(res.warning, "warning", "Settings Warning");
      } else {
        showToast("Settings saved successfully.", "success", "Settings");
      }
      delete document.getElementById("setting_scan_interval").dataset.initialized;
      await load();
    } else {
      showToast(res.detail || "Failed to save settings.", "error", "Settings Error");
      delete document.getElementById("setting_scan_interval").dataset.initialized;
      await load();
    }
  } catch (err) {
    showToast(err.message || String(err), "error", "Network Error");
  }
}
window.saveSettings = saveSettings;

// ── Pods Modal ─────────────────────────────────────────────────────────────
function openPodsModal() {
  document.getElementById("podsModal").style.display = "flex";
  renderPodsModalList();
}
window.openPodsModal = openPodsModal;

function closePodsModal() {
  document.getElementById("podsModal").style.display = "none";
}
window.closePodsModal = closePodsModal;

function switchModalTab(tab) {
  modalTab = tab;
  document.querySelectorAll(".modal-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.id === `modal-tab-${tab}`);
  });
  renderPodsModalList();
}
window.switchModalTab = switchModalTab;

function renderPodsModalList() {
  const tbody = document.getElementById("podsModalTableBody");
  tbody.innerHTML = "";

  const filtered = allPods.filter(p => {
    if (modalTab === "healthy")   return p.healthy;
    if (modalTab === "unhealthy") return !p.healthy;
    return true;
  });

  document.getElementById("modal-count-all").textContent       = allPods.length;
  document.getElementById("modal-count-healthy").textContent   = allPods.filter(p => p.healthy).length;
  document.getElementById("modal-count-unhealthy").textContent = allPods.filter(p => !p.healthy).length;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px;">No resources to show.</td></tr>`;
    return;
  }

  filtered.forEach(p => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><span class="pod-status-dot ${p.healthy ? "healthy" : "unhealthy"}">${p.healthy ? "Healthy" : "Unhealthy"}</span></td>
      <td style="color:var(--muted);font-size:12px;font-weight:500;">${escapeHtml(p.type)}</td>
      <td style="color:var(--muted);">${escapeHtml(p.namespace)}</td>
      <td style="font-weight:600;">${escapeHtml(p.name)}</td>
      <td style="font-family:monospace;font-size:12px;color:${p.healthy ? "var(--muted)" : "var(--critical)"};">${escapeHtml(p.details)}</td>`;
    tbody.appendChild(row);
  });
}

// Pods info tag click — open modal
document.getElementById("podsInfoTag")?.addEventListener("click", openPodsModal);

// Modal: close-button and backdrop
document.getElementById("podsModalCloseBtn")?.addEventListener("click", closePodsModal);
document.getElementById("podsModal")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closePodsModal();
});

// ── Create Demo Problem ─────────────────────────────────────────────────────
const createProblemBtn = document.getElementById("createProblemBtn");
const problemsModal = document.getElementById("problemsModal");
const problemsModalCloseBtn = document.getElementById("problemsModalCloseBtn");
const problemsModalList = document.getElementById("problemsModalList");

function openProblemsModal() {
  if (problemsModal) problemsModal.style.display = "flex";
}
function closeProblemsModal() {
  if (problemsModal) problemsModal.style.display = "none";
}

if (createProblemBtn) {
  createProblemBtn.addEventListener("click", async () => {
    openProblemsModal();
    if (problemsModalList) {
      problemsModalList.innerHTML = '<div style="text-align:center;padding:20px;color:var(--muted);">Loading scenarios…</div>';
    }
    try {
      const r = await fetch(`/api/demo/problems?_=${Date.now()}`);
      const list = await r.json();
      if (r.ok && Array.isArray(list) && problemsModalList) {
        problemsModalList.innerHTML = "";
        list.forEach(p => {
          const card = document.createElement("div");
          card.className = "problem-card";
          
          const sevClass = (p.severity || "medium").toLowerCase();
          
          card.innerHTML = `
            <div class="problem-info">
              <div class="problem-title-row">
                <span class="problem-title">${escapeHtml(p.title)}</span>
                <span class="problem-badge ${sevClass}">${escapeHtml(p.severity)}</span>
              </div>
              <div class="problem-desc">${escapeHtml(p.description)}</div>
            </div>
            <button class="primary deploy-problem-btn" style="padding: 6px 14px; font-size:12px; border-radius:4px; font-weight:700;">Deploy</button>
          `;
          
          const deployBtn = card.querySelector(".deploy-problem-btn");
          deployBtn.addEventListener("click", () => deployProblem(p.file, deployBtn));
          
          problemsModalList.appendChild(card);
        });
      } else {
        if (problemsModalList) {
          problemsModalList.innerHTML = '<div style="text-align:center;padding:20px;color:var(--critical);">Failed to load scenarios.</div>';
        }
      }
    } catch (err) {
      if (problemsModalList) {
        problemsModalList.innerHTML = `<div style="text-align:center;padding:20px;color:var(--critical);">Error: ${escapeHtml(err.message || String(err))}</div>`;
      }
    }
  });
}

async function deployProblem(filename, btnElement) {
  const originalText = btnElement.textContent;
  btnElement.disabled = true;
  btnElement.textContent = "Deploying…";
  statusText.textContent = `Deploying demo problem ${filename}…`;
  
  try {
    const r = await fetch("/api/demo/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ problem_file: filename }),
    });
    const res = await r.json();
    if (r.ok && res.success) {
      showToast("Demo scenario deployed successfully. Scan running…", "success", "Scenario Deployed");
      closeProblemsModal();
      await load();
    } else {
      showToast(res.detail || "Failed to deploy scenario.", "error", "Deployment Error", 6000);
    }
  } catch (err) {
    showToast(err.message || String(err), "error", "Network Error");
  } finally {
    btnElement.disabled = false;
    btnElement.textContent = originalText;
  }
}

if (problemsModalCloseBtn) {
  problemsModalCloseBtn.addEventListener("click", closeProblemsModal);
}
if (problemsModal) {
  problemsModal.addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeProblemsModal();
  });
}

// ── Boot ───────────────────────────────────────────────────────────────────
load().catch(err => { statusText.textContent = `Load failed: ${err}`; });
setInterval(async () => { await load().catch(() => {}); }, 60000);
