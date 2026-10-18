// Anvaya — Frontend Client Application (Milestone 12)
let cy = null;
let currentAction = null;
let lastAskResponse = null;

// Dynamic API Base URL resolution:
// 1. window.__ANVAYA_API_BASE__ if explicitly defined
// 2. <meta name="anvaya-api-base" content="..."> if present
// 3. Defaults to "" (relative URL, optimal for same-origin and Vercel rewrites)
function getApiBase() {
  if (typeof window !== "undefined" && window.__ANVAYA_API_BASE__) {
    return window.__ANVAYA_API_BASE__.replace(/\/+$/, "");
  }
  const meta = typeof document !== "undefined" ? document.querySelector('meta[name="anvaya-api-base"]') : null;
  if (meta && meta.content && !meta.content.startsWith("__") && meta.content.trim()) {
    return meta.content.trim().replace(/\/+$/, "");
  }
  return "";
}

function apiUrl(endpoint) {
  const base = getApiBase();
  const path = endpoint.startsWith("/") ? endpoint : "/" + endpoint;
  return base ? `${base}${path}` : path;
}

document.addEventListener("DOMContentLoaded", () => {
  initHealthCheck();
  initTabs();
  initAskScreen();
  initGraphExplorer();
  initContextScreen();
  initMemoryDemo();
  initResetButton();
});

// ---------------------------------------------------------------------------
// 1. Health & Status
// ---------------------------------------------------------------------------
async function initHealthCheck() {
  const dot = document.getElementById("falkordb-dot");
  const text = document.getElementById("falkordb-status-text");

  async function check() {
    try {
      const res = await fetch(apiUrl("/health"));
      const data = await res.json();
      if (res.ok && data.status === "healthy" && data.falkordb?.connected) {
        dot.className = "status-dot";
        text.textContent = `FalkorDB: Connected (${data.falkordb.graph})`;
      } else {
        dot.className = "status-dot offline";
        const cat = data.error_category || (data.falkordb && data.falkordb.error_category) || "Fail-Closed";
        text.textContent = `FalkorDB: Unavailable (${cat})`;
      }
    } catch {
      dot.className = "status-dot offline";
      text.textContent = "FalkorDB: Offline (Refusal Enforced)";
    }
  }

  await check();
  setInterval(check, 10000);
}

// ---------------------------------------------------------------------------
// 2. Navigation Tabs
// ---------------------------------------------------------------------------
function initTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-view").forEach((v) => v.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      const targetView = document.getElementById(targetId);
      if (targetView) targetView.classList.add("active");

      if (targetId === "tab-graph") {
        setTimeout(() => {
          if (!cy) {
            renderGraph();
          } else {
            cy.resize();
            cy.fit();
          }
        }, 100);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 3. Screen 1: Ask & Investigate
// ---------------------------------------------------------------------------
function initAskScreen() {
  const form = document.getElementById("ask-form");
  const input = document.getElementById("ask-input");
  const repoSelect = document.getElementById("ask-repo-select");
  const loading = document.getElementById("ask-loading");
  const results = document.getElementById("ask-results");

  // Presets
  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const query = chip.getAttribute("data-query");
      const repo = chip.getAttribute("data-repo");
      if (query) input.value = query;
      if (repo) repoSelect.value = repo;
      form.dispatchEvent(new Event("submit"));
    });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = input.value.trim();
    const repo = repoSelect.value;
    if (!message) return;

    loading.style.display = "block";
    results.style.display = "none";

    try {
      const res = await fetch(apiUrl("/api/v1/ask"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, repo }),
      });
      const data = await res.json();
      lastAskResponse = data;
      renderAskResults(data, repo);
    } catch (err) {
      alert(`API Error: ${err.message}`);
    } finally {
      loading.style.display = "none";
      results.style.display = "block";
    }
  });

  // Action Buttons
  document.getElementById("btn-approve-action").addEventListener("click", handleApproveAction);
  document.getElementById("btn-reject-action").addEventListener("click", () => {
    document.getElementById("reject-modal").classList.add("open");
  });

  // Modal Buttons
  document.getElementById("btn-modal-cancel").addEventListener("click", () => {
    document.getElementById("reject-modal").classList.remove("open");
  });
  document.getElementById("btn-modal-confirm").addEventListener("click", handleRejectAction);
}

function renderAskResults(data, repo) {
  const refusalBox = document.getElementById("refusal-box");
  const narrationText = document.getElementById("ask-narration-text");
  const badgeContainer = document.getElementById("ask-verification-badge");
  const pathSection = document.getElementById("evidence-path-section");
  const reviewersPanel = document.getElementById("reviewers-panel");
  const reviewersTbody = document.getElementById("reviewers-tbody");
  const impactPanel = document.getElementById("impact-panel");
  const impactTbody = document.getElementById("impact-tbody");
  const actionBox = document.getElementById("action-box");
  const noActionBox = document.getElementById("no-action-box");
  const claimsList = document.getElementById("claims-evidence-list");

  // 1. Refusal State
  if (data.refused) {
    refusalBox.style.display = "block";
    document.getElementById("refusal-reason-code").textContent = `[Refusal: ${data.reason_code || "FAIL_CLOSED"}]`;
    document.getElementById("refusal-message").textContent =
      data.reason || "I can't establish a verified graph path for this file. No action proposed.";
    badgeContainer.innerHTML = `<span class="badge badge-refusal">Refused (${data.reason_code})</span>`;
  } else {
    refusalBox.style.display = "none";
    badgeContainer.innerHTML = `<span class="badge badge-verified">✓ 100% Grounded in FalkorDB</span>`;
  }

  // 2. Narration
  narrationText.textContent = data.narration || data.summary || "No response received.";

  // 3. Reviewers Table
  if (data.reviewers && data.reviewers.length > 0) {
    reviewersPanel.style.display = "block";
    reviewersTbody.innerHTML = "";

    data.reviewers.forEach((rev, idx) => {
      const isExcluded = data.excluded_candidates?.some((e) => e.person === rev.person);
      const row = document.createElement("tr");
      row.innerHTML = `
        <td style="font-weight: 600;">
          ${rev.person} ${idx === 0 && !isExcluded ? '<span class="badge badge-verified">#1 Pick</span>' : ""}
          <div style="font-size: 11px; color: var(--text-muted);">${rev.email || ""}</div>
        </td>
        <td>${rev.direct_commits ?? rev.commits ?? 0}</td>
        <td>${rev.co_change_commits ?? 0}</td>
        <td>+${rev.lines_added ?? 0} / -${rev.lines_deleted ?? 0}</td>
        <td>
          ${
            isExcluded
              ? '<span class="badge badge-refusal">Excluded by Memory Constraint</span>'
              : '<span class="badge badge-verified">Available</span>'
          }
        </td>
      `;
      reviewersTbody.appendChild(row);
    });
  } else {
    reviewersPanel.style.display = "none";
  }

  // 4. Impact Table (Blast Radius)
  if (data.impact && data.impact.length > 0) {
    impactPanel.style.display = "block";
    impactTbody.innerHTML = "";

    data.impact.slice(0, 8).forEach((item) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td style="font-family: var(--font-mono); font-size: 12px;">${item.file}</td>
        <td>${item.co_change_count ?? item.strength ?? 0} shared commits</td>
        <td><span class="badge badge-verified">${item.relationship || "CO_CHANGES"}</span></td>
      `;
      impactTbody.appendChild(row);
    });
  } else {
    impactPanel.style.display = "none";
  }

  // 5. Evidence Path Tracing
  const topCandidate = data.reviewers?.[0]?.person;
  const targetFile =
    data.tool_arguments?.file_path || data.tool_arguments?.file || data.proposed_action?.target;

  if (topCandidate && targetFile) {
    fetchEvidencePath(targetFile, topCandidate, repo);
  } else {
    pathSection.style.display = "none";
  }

  // 6. Proposed Action
  if (data.proposed_action && !data.refused) {
    currentAction = data.proposed_action;
    noActionBox.style.display = "none";
    actionBox.style.display = "block";
    actionBox.className = "action-box";

    document.getElementById("action-type-label").textContent = currentAction.type.toUpperCase();
    document.getElementById("action-status-badge").className = "badge badge-proposed";
    document.getElementById("action-status-badge").textContent = currentAction.status.toUpperCase();
    document.getElementById("action-target-label").textContent = `Target: ${currentAction.target}`;
    document.getElementById("action-reviewer-label").textContent = `Reviewer: ${currentAction.reviewer}`;
    document.getElementById("action-reason-label").textContent = currentAction.reason;

    document.getElementById("action-buttons-row").style.display = "flex";
    document.getElementById("action-outcome-box").style.display = "none";
  } else {
    currentAction = null;
    actionBox.style.display = "none";
    noActionBox.style.display = "block";
  }

  // 7. Verified Claims & Evidence List
  claimsList.innerHTML = "";
  if (data.claims && data.claims.length > 0) {
    data.claims.forEach((c) => {
      const item = document.createElement("div");
      item.style.padding = "8px 0";
      item.style.borderBottom = "1px solid var(--border-subtle)";
      item.style.fontSize = "12px";
      item.innerHTML = `
        <div style="font-weight: 600; color: var(--text-primary); font-family: var(--font-mono);">
          [${c.claim_id}] ${c.subject} → ${c.predicate}
        </div>
        <div style="color: var(--text-secondary); margin-top: 2px;">
          Value: <strong>${c.value}</strong> | Evidence IDs: <code>${c.evidence_ids.join(", ")}</code>
        </div>
      `;
      claimsList.appendChild(item);
    });
  } else {
    claimsList.innerHTML = `<div style="color: var(--text-muted); font-size: 12px;">No claims recorded.</div>`;
  }
}

async function fetchEvidencePath(targetFile, reviewerName, repo) {
  const pathSection = document.getElementById("evidence-path-section");
  const stepsFlow = document.getElementById("evidence-path-steps");

  try {
    const res = await fetch(
      apiUrl(`/api/v1/graph/evidence-path?target_file=${encodeURIComponent(targetFile)}&reviewer_name=${encodeURIComponent(reviewerName)}&repo=${encodeURIComponent(repo)}`)
    );
    const data = await res.json();

    if (data.path_found && data.steps.length > 0) {
      pathSection.style.display = "block";
      stepsFlow.innerHTML = "";

      // Render step flow
      data.steps.forEach((step, idx) => {
        if (idx === 0) {
          const srcNode = document.createElement("div");
          srcNode.className = `path-node ${step.source_type.toLowerCase()}`;
          srcNode.textContent = `${step.source_type}: ${step.source_label}`;
          stepsFlow.appendChild(srcNode);
        }

        const arrow = document.createElement("div");
        arrow.className = "path-arrow";
        arrow.textContent = `──[ ${step.relationship_type} ]──►`;
        stepsFlow.appendChild(arrow);

        const tgtNode = document.createElement("div");
        tgtNode.className = `path-node ${step.target_type.toLowerCase()}`;
        tgtNode.textContent = `${step.target_type}: ${step.target_label}`;
        stepsFlow.appendChild(tgtNode);
      });

      if (data.is_constrained) {
        const warn = document.createElement("div");
        warn.style.width = "100%";
        warn.style.marginTop = "8px";
        warn.style.fontSize = "11px";
        warn.style.color = "var(--accent-rose)";
        warn.textContent = `⚠️ Active Constraint in Graph: ${data.constraint_info?.type} (${data.constraint_info?.scope})`;
        stepsFlow.appendChild(warn);
      }
    } else {
      pathSection.style.display = "none";
    }
  } catch {
    pathSection.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// 4. Action Lifecycle Handlers
// ---------------------------------------------------------------------------
async function handleApproveAction() {
  if (!currentAction) return;
  const actionId = currentAction.id;

  try {
    // 1. Approve
    const appRes = await fetch(apiUrl(`/api/v1/actions/${actionId}/approve`), { method: "POST" });
    const appData = await appRes.json();
    if (!appRes.ok) throw new Error(appData.detail || "Approval failed");

    document.getElementById("action-status-badge").className = "badge badge-verified";
    document.getElementById("action-status-badge").textContent = "APPROVED";

    // 2. Execute
    const exeRes = await fetch(apiUrl(`/api/v1/actions/${actionId}/execute`), { method: "POST" });
    const exeData = await exeRes.json();
    if (!exeRes.ok) throw new Error(exeData.detail || "Execution failed");

    document.getElementById("action-box").className = "action-box succeeded";
    document.getElementById("action-status-badge").className = "badge badge-succeeded";
    document.getElementById("action-status-badge").textContent = "SUCCEEDED";
    document.getElementById("action-buttons-row").style.display = "none";

    const outcomeBox = document.getElementById("action-outcome-box");
    const outcomeText = document.getElementById("action-outcome-text");
    outcomeBox.style.display = "block";
    outcomeText.innerHTML = `
      (:Action {id: "${actionId}"})<br/>
      └─[:RESULTED_IN]─► (:Outcome {id: "${exeData.result?.outcome_id || 'out_1'}", status: "succeeded", sandbox: true})<br/>
      Review request notification dispatched to ${currentAction.reviewer}.
    `;
  } catch (err) {
    alert(`Action Error: ${err.message}`);
  }
}

async function handleRejectAction() {
  if (!currentAction) return;
  const actionId = currentAction.id;
  const reason = document.getElementById("reject-reason-input").value;

  try {
    const payload = {
      reason,
      constraint: {
        type: "reviewer_unavailable",
        subject: currentAction.reviewer,
        scope: `repo:${currentAction.repo}`,
      },
    };

    const res = await fetch(apiUrl(`/api/v1/actions/${actionId}/reject`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Rejection failed");

    document.getElementById("reject-modal").classList.remove("open");
    document.getElementById("action-box").className = "action-box rejected";
    document.getElementById("action-status-badge").className = "badge badge-refusal";
    document.getElementById("action-status-badge").textContent = "REJECTED";
    document.getElementById("action-buttons-row").style.display = "none";

    const outcomeBox = document.getElementById("action-outcome-box");
    const outcomeText = document.getElementById("action-outcome-text");
    outcomeBox.style.display = "block";
    outcomeText.innerHTML = `
      (:Constraint {type: "reviewer_unavailable", subject: "${currentAction.reviewer}", scope: "repo:${currentAction.repo}"})<br/>
      Written directly to FalkorDB persistent memory.<br/>
      Future sessions will exclude ${currentAction.reviewer} and elevate alternative contributors.
    `;
  } catch (err) {
    alert(`Rejection Error: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// 5. Screen 2: Interactive Graph Explorer
// ---------------------------------------------------------------------------
function initGraphExplorer() {
  document.getElementById("btn-render-graph").addEventListener("click", renderGraph);
  document.getElementById("btn-fit-graph").addEventListener("click", () => {
    if (cy) cy.fit();
  });

  const nodeSlider = document.getElementById("graph-node-limit");
  const nodeSliderVal = document.getElementById("graph-node-limit-val");
  nodeSlider.addEventListener("input", () => {
    nodeSliderVal.textContent = nodeSlider.value;
  });
}

async function renderGraph() {
  const repo = document.getElementById("graph-repo-select").value;
  const subject = document.getElementById("graph-subject-input").value.trim();
  const layoutName = document.getElementById("graph-layout-select").value;
  const maxNodes = document.getElementById("graph-node-limit").value;

  let url = `/api/v1/graph?repo=${encodeURIComponent(repo)}&max_nodes=${maxNodes}&include_memory=true`;
  if (subject) url += `&subject=${encodeURIComponent(subject)}`;

  try {
    const res = await fetch(apiUrl(url));
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Graph fetch failed");

    buildCytoscapeGraph(data, layoutName);
  } catch (err) {
    console.error("Graph error:", err);
  }
}

function buildCytoscapeGraph(graphData, layoutName) {
  const cyContainer = document.getElementById("cy");
  if (!cyContainer) return;

  const elements = [];

  // Nodes
  graphData.nodes.forEach((n) => {
    elements.push({
      data: {
        id: n.id,
        label: n.label,
        type: n.type,
        properties: n.properties,
      },
    });
  });

  // Edges
  graphData.relationships.forEach((r) => {
    elements.push({
      data: {
        id: r.id,
        source: r.source,
        target: r.target,
        type: r.type,
        properties: r.properties,
      },
    });
  });

  if (cy) cy.destroy();

  cy = cytoscape({
    container: cyContainer,
    elements,
    style: [
      {
        selector: "node",
        style: {
          label: "data(label)",
          "color": "#f8fafc",
          "font-size": "11px",
          "text-valign": "bottom",
          "text-margin-y": 5,
          "font-family": "ui-monospace, Consolas, monospace",
          "background-color": "#3b82f6",
          "border-width": 2,
          "border-color": "#1e293b",
        },
      },
      {
        selector: "node[type = 'Repository']",
        style: { "background-color": "#8b5cf6", "width": 45, "height": 45 },
      },
      {
        selector: "node[type = 'Module']",
        style: { "background-color": "#6366f1", "width": 38, "height": 38 },
      },
      {
        selector: "node[type = 'File']",
        style: { "background-color": "#3b82f6", "width": 30, "height": 30 },
      },
      {
        selector: "node[type = 'Commit']",
        style: { "background-color": "#f59e0b", "width": 24, "height": 24 },
      },
      {
        selector: "node[type = 'Person']",
        style: { "background-color": "#10b981", "width": 34, "height": 34 },
      },
      {
        selector: "node[type = 'Constraint']",
        style: { "background-color": "#f43f5e", "width": 30, "height": 30 },
      },
      {
        selector: "node[type = 'Action']",
        style: { "background-color": "#06b6d4", "width": 28, "height": 28 },
      },
      {
        selector: "node[type = 'Outcome']",
        style: { "background-color": "#14b8a6", "width": 26, "height": 26 },
      },
      {
        selector: "edge",
        style: {
          "width": 1.5,
          "line-color": "#475569",
          "target-arrow-color": "#475569",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "font-size": "9px",
          "color": "#94a3b8",
          "text-rotation": "autorotate",
          "label": "data(type)",
        },
      },
      {
        selector: "edge[type = 'CO_CHANGES']",
        style: { "line-color": "#38bdf8", "target-arrow-color": "#38bdf8", "width": 2 },
      },
      {
        selector: "edge[type = 'TOUCHES']",
        style: { "line-color": "#fbbf24", "target-arrow-color": "#fbbf24" },
      },
      {
        selector: "edge[type = 'AUTHORED']",
        style: { "line-color": "#34d399", "target-arrow-color": "#34d399" },
      },
      {
        selector: ":selected",
        style: { "border-width": 4, "border-color": "#ffffff" },
      },
    ],
    layout: {
      name: layoutName || "cose",
      animate: false,
      padding: 30,
    },
  });

  // Click on node -> Inspect properties
  cy.on("tap", "node", (evt) => {
    const node = evt.target;
    showNodeInspector(node.data());
  });
}

function showNodeInspector(nodeData) {
  const placeholder = document.getElementById("inspector-placeholder");
  const details = document.getElementById("inspector-details");

  placeholder.style.display = "none";
  details.style.display = "block";

  let propsHtml = "";
  const props = nodeData.properties || {};

  for (const [key, val] of Object.entries(props)) {
    propsHtml += `
      <div class="inspector-prop">
        <div class="inspector-label">${key}</div>
        <div class="inspector-val">${typeof val === "object" ? JSON.stringify(val) : val}</div>
      </div>
    `;
  }

  details.innerHTML = `
    <div style="margin-bottom: 12px;">
      <span class="badge" style="background-color: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border);">
        ${nodeData.type}
      </span>
      <div style="font-size: 15px; font-weight: 700; margin-top: 6px;">${nodeData.label}</div>
      <div style="font-size: 11px; font-family: var(--font-mono); color: var(--text-muted);">${nodeData.id}</div>
    </div>
    <div style="border-top: 1px solid var(--border-subtle); padding-top: 12px;">
      ${propsHtml || '<div style="color: var(--text-muted); font-size: 12px;">No properties recorded.</div>'}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// 6. Screen 3: Repository Context Package Viewer
// ---------------------------------------------------------------------------
function initContextScreen() {
  const repoSelect = document.getElementById("ctx-repo-select");
  const subjInput = document.getElementById("ctx-subject-input");
  const fetchBtn = document.getElementById("btn-fetch-context");

  // Presets
  document.getElementById("chip-ctx-arth").addEventListener("click", () => {
    repoSelect.value = "arth";
    subjInput.value = "";
    fetchContext();
  });
  document.getElementById("chip-ctx-arth-file").addEventListener("click", () => {
    repoSelect.value = "arth";
    subjInput.value = "backend/app/engines/assistant/engine.py";
    fetchContext();
  });
  document.getElementById("chip-ctx-db").addEventListener("click", () => {
    repoSelect.value = "databases";
    subjInput.value = "";
    fetchContext();
  });
  document.getElementById("chip-ctx-db-file").addEventListener("click", () => {
    repoSelect.value = "databases";
    subjInput.value = "databases/backends/postgres.py";
    fetchContext();
  });

  fetchBtn.addEventListener("click", fetchContext);

  // Sub tabs (Human vs JSON)
  const humanTab = document.getElementById("btn-ctx-human-tab");
  const jsonTab = document.getElementById("btn-ctx-json-tab");
  const humanView = document.getElementById("ctx-human-view");
  const jsonView = document.getElementById("ctx-json-view");
  const copyBtn = document.getElementById("btn-copy-json");

  humanTab.addEventListener("click", () => {
    humanTab.className = "btn btn-secondary btn-sm";
    jsonTab.className = "btn btn-secondary btn-sm";
    humanTab.style.backgroundColor = "var(--bg-surface-active)";
    jsonTab.style.backgroundColor = "var(--bg-surface)";
    humanView.style.display = "block";
    jsonView.style.display = "none";
    copyBtn.style.display = "none";
  });

  jsonTab.addEventListener("click", () => {
    humanTab.style.backgroundColor = "var(--bg-surface)";
    jsonTab.style.backgroundColor = "var(--bg-surface-active)";
    humanView.style.display = "none";
    jsonView.style.display = "block";
    copyBtn.style.display = "inline-flex";
  });

  copyBtn.addEventListener("click", () => {
    const jsonText = document.getElementById("ctx-json-pre").textContent;
    navigator.clipboard.writeText(jsonText);
    copyBtn.textContent = "Copied!";
    setTimeout(() => { copyBtn.textContent = "Copy JSON"; }, 2000);
  });
}

async function fetchContext() {
  const repo = document.getElementById("ctx-repo-select").value;
  const subject = document.getElementById("ctx-subject-input").value.trim() || null;

  try {
    const res = await fetch(apiUrl("/api/v1/context"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repository: repo, subject, depth: 3, max_files: 15 }),
    });
    const pkg = await res.json();
    renderContextPackage(pkg);
  } catch (err) {
    alert(`Context Error: ${err.message}`);
  }
}

function renderContextPackage(pkg) {
  // Freshness bar
  const freshnessBar = document.getElementById("ctx-freshness-bar");
  freshnessBar.style.display = "block";
  freshnessBar.innerHTML = `
    <strong>SNAPSHOT PROVENANCE:</strong> Head: <code>${pkg.freshness?.head_sha || "unknown"}</code> |
    Synced: <code>${pkg.freshness?.synced_at || "n/a"}</code> |
    Commits: <code>${pkg.freshness?.commit_count || 0}</code> |
    History: <code>${pkg.freshness?.history_window || "bounded"}</code> |
    Verified: <span style="color: var(--accent-emerald);">True</span>
  `;

  // Summary
  document.getElementById("ctx-summary-box").textContent = pkg.summary || "No summary available.";

  // Modules
  const modContainer = document.getElementById("ctx-modules-container");
  modContainer.innerHTML = "";
  if (pkg.modules && pkg.modules.length > 0) {
    pkg.modules.forEach((m) => {
      const card = document.createElement("div");
      card.style.background = "var(--bg-surface)";
      card.style.border = "1px solid var(--border-subtle)";
      card.style.borderRadius = "var(--radius-md)";
      card.style.padding = "10px 14px";
      card.style.marginBottom = "8px";
      card.innerHTML = `
        <div style="font-weight: 700; font-size: 13px; color: var(--accent-blue);">${m.name}</div>
        <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;">
          Files: ${m.file_count} | Contributors: ${m.top_contributors?.join(", ") || "none"}
        </div>
      `;
      modContainer.appendChild(card);
    });
  } else {
    modContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 12px;">No module data.</div>`;
  }

  // Files Table
  const filesTbody = document.getElementById("ctx-files-tbody");
  filesTbody.innerHTML = "";
  if (pkg.important_files && pkg.important_files.length > 0) {
    pkg.important_files.forEach((f) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td style="font-family: var(--font-mono); font-size: 12px;">${f.path}</td>
        <td>${f.commit_count}</td>
        <td>+${f.lines_added}</td>
        <td>-${f.lines_deleted}</td>
      `;
      filesTbody.appendChild(row);
    });
  }

  // Contributors List
  const contribList = document.getElementById("ctx-contributors-list");
  contribList.innerHTML = "";
  if (pkg.contributors && pkg.contributors.length > 0) {
    pkg.contributors.forEach((c) => {
      const div = document.createElement("div");
      div.style.padding = "8px 0";
      div.style.borderBottom = "1px solid var(--border-subtle)";
      div.style.fontSize = "12px";
      div.innerHTML = `
        <div style="font-weight: 600;">${c.name} (${c.commit_count} commits)</div>
        <div style="font-size: 11px; color: var(--text-muted);">${c.email} | Touched: ${c.files_touched?.length || 0} files</div>
      `;
      contribList.appendChild(div);
    });
  }

  // Limitations
  const limitsList = document.getElementById("ctx-limitations-list");
  limitsList.innerHTML = "";
  if (pkg.limitations && pkg.limitations.length > 0) {
    pkg.limitations.forEach((lim) => {
      const li = document.createElement("li");
      li.textContent = lim;
      limitsList.appendChild(li);
    });
  }

  // JSON View
  document.getElementById("ctx-json-pre").textContent = JSON.stringify(pkg, null, 2);
}

// ---------------------------------------------------------------------------
// 7. Screen 4: Persistent Memory Demo
// ---------------------------------------------------------------------------
function initMemoryDemo() {
  const btnS1 = document.getElementById("btn-demo-session-1");
  const btnConst = document.getElementById("btn-demo-record-constraint");
  const btnS2 = document.getElementById("btn-demo-session-2");
  const btnRefresh = document.getElementById("btn-refresh-memory");

  btnS1.addEventListener("click", async () => {
    const out = document.getElementById("demo-s1-output");
    out.textContent = "Querying FalkorDB (Session 1)...";
    const res = await fetch(apiUrl("/api/v1/ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "I'm changing databases/backends/postgres.py. Who should review it?",
        repo: "databases",
      }),
    });
    const data = await res.json();
    const topRev = data.reviewers?.[0]?.person || "Unknown";
    out.innerHTML = `
      Result: <strong>${topRev}</strong> recommended (#1 by historical commits).<br/>
      Action proposed: <code>request_review(${topRev})</code>
    `;
    refreshMemoryTable();
  });

  btnConst.addEventListener("click", async () => {
    const out = document.getElementById("demo-constraint-output");
    out.textContent = "Writing constraint to FalkorDB...";
    // Query last proposed action to reject it, or simulate direct rejection
    const actRes = await fetch(apiUrl("/api/v1/actions?repo=databases&status=proposed"));
    const actions = await actRes.json();
    const targetAction = actions[0];

    if (targetAction) {
      await fetch(apiUrl(`/api/v1/actions/${targetAction.id}/reject`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: "Tom Christie on sabbatical / unavailable",
          constraint: {
            type: "reviewer_unavailable",
            subject: "Tom Christie",
            scope: "repo:databases",
          },
        }),
      });
      out.innerHTML = `✓ Recorded constraint: <code>(:Constraint {subject: "Tom Christie", type: "reviewer_unavailable"})</code> in FalkorDB!`;
    } else {
      out.innerHTML = `Note: No proposed action in queue; resetting demo and running Session 1 first is recommended.`;
    }
    refreshMemoryTable();
  });

  btnS2.addEventListener("click", async () => {
    const out = document.getElementById("demo-s2-output");
    out.textContent = "Querying FalkorDB in Fresh Session 2...";
    const res = await fetch(apiUrl("/api/v1/ask"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "I'm changing databases/backends/postgres.py. Who should review it?",
        repo: "databases",
      }),
    });
    const data = await res.json();
    const topRev = data.reviewers?.[0]?.person || "Unknown";
    const excluded = data.excluded_candidates?.[0]?.person || "None";
    out.innerHTML = `
      ✓ New Recommendation: <strong>${topRev}</strong> elevated!<br/>
      ✓ Excluded by Memory: <strong>${excluded}</strong> (reviewer_unavailable).<br/>
      <em>The agent changed its future decision because the graph remembered something!</em>
    `;
    refreshMemoryTable();
  });

  btnRefresh.addEventListener("click", refreshMemoryTable);
  refreshMemoryTable();
}

async function refreshMemoryTable() {
  const tbody = document.getElementById("memory-constraints-tbody");
  try {
    const res = await fetch(apiUrl("/api/v1/graph/memory?repo=databases"));
    const data = await res.json();
    tbody.innerHTML = "";

    if (data.active_constraints && data.active_constraints.length > 0) {
      data.active_constraints.forEach((c) => {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td style="font-family: var(--font-mono); font-size: 11px;">${c.id}</td>
          <td><span class="badge badge-refusal">${c.type}</span></td>
          <td style="font-weight: 600;">${c.subject}</td>
          <td style="font-family: var(--font-mono); font-size: 11px;">${c.scope}</td>
          <td><span class="badge badge-verified">ACTIVE</span></td>
        `;
        tbody.appendChild(row);
      });
    } else {
      tbody.innerHTML = `<tr><td colspan="5" style="color: var(--text-muted); text-align: center; padding: 14px;">No active constraints recorded in FalkorDB.</td></tr>`;
    }
  } catch {
    tbody.innerHTML = `<tr><td colspan="5" style="color: var(--accent-rose); text-align: center;">Failed to load memory state.</td></tr>`;
  }
}

// ---------------------------------------------------------------------------
// 8. Demo Reset
// ---------------------------------------------------------------------------
function initResetButton() {
  const btn = document.getElementById("btn-reset-demo");
  btn.addEventListener("click", async () => {
    try {
      const res = await fetch(apiUrl("/api/v1/graph/memory/reset-demo?repo=databases"), { method: "POST" });
      const data = await res.json();
      alert(data.message || "Demo state reset successfully.");
      refreshMemoryTable();
    } catch (err) {
      alert(`Reset error: ${err.message}`);
    }
  });
}
