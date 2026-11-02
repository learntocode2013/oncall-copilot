const state = {
  incidents: [],
  activeId: null,
  active: null,
  stream: null,
  feed: null,
};

const $ = (id) => document.getElementById(id);
const terminalStatuses = new Set(["RECOVERED", "REJECTED", "ESCALATED", "APPROVAL_MISMATCH"]);
const stageOrder = ["scope", "hypotheses", "test", "act", "verify", "communicate"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* response is not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

function friendlyTime(value) {
  if (!value) return "now";
  const date = new Date(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (value === "recovered") return "recovered";
  if (["rejected", "escalated", "approval_mismatch"].includes(value)) return "rejected";
  return "";
}

async function loadIncidents(selectNewest = false) {
  try {
    state.incidents = await api("/api/incidents");
    $("connection").classList.remove("offline");
    $("connection").lastChild.textContent = " API connected";
    renderQueue();
    if (selectNewest && state.incidents[0]) selectIncident(state.incidents[0].incident_id);
  } catch (error) {
    $("connection").classList.add("offline");
    $("connection").lastChild.textContent = " API unavailable";
    showToast(error.message);
  }
}

function upsertIncident(incident) {
  const index = state.incidents.findIndex((item) => item.incident_id === incident.incident_id);
  if (index >= 0) state.incidents[index] = incident;
  else state.incidents.unshift(incident);
}

function connectIncidentFeed() {
  if (state.feed) state.feed.close();
  const feed = new EventSource("/api/incidents/stream");
  state.feed = feed;
  feed.addEventListener("incident", (event) => {
    const payload = JSON.parse(event.data);
    const incident = payload.incident;
    const isNew = !state.incidents.some((item) => item.incident_id === incident.incident_id);
    upsertIncident(incident);
    renderQueue();
    if (isNew && !state.activeId) {
      selectIncident(incident.incident_id);
      showToast("New incident received");
    } else if (isNew) {
      const title = incident.alert?.labels?.alertname || "New incident";
      showToast(`${title} added to the queue`);
    }
  });
  feed.onopen = () => {
    $("connection").classList.remove("offline");
    if (!state.activeId) $("connection").lastChild.textContent = " Incident feed connected";
  };
  feed.onerror = () => {
    if (!state.stream) {
      $("connection").classList.add("offline");
      $("connection").lastChild.textContent = " Feed reconnecting";
    }
  };
}

function renderQueue() {
  const active = state.incidents.filter((item) => !item.terminal).length;
  $("active-count").textContent = active;
  $("resolved-count").textContent = state.incidents.length - active;
  const list = $("incident-list");
  if (!state.incidents.length) {
    list.innerHTML = '<div class="list-empty">No incidents yet.</div>';
    return;
  }
  list.innerHTML = state.incidents.map((incident) => {
    const title = incident.alert?.labels?.alertname || "Untitled incident";
    const summary = incident.alert?.annotations?.summary || "No summary";
    const resolved = terminalStatuses.has(incident.status) ? "resolved" : "";
    return `<button class="incident-card ${incident.incident_id === state.activeId ? "active" : ""}" data-id="${escapeHtml(incident.incident_id)}" type="button">
      <div class="incident-card-top"><strong>${escapeHtml(title)}</strong><span class="mini-status ${resolved}">${escapeHtml(incident.status)}</span></div>
      <p>${escapeHtml(summary)}</p>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-id]").forEach((button) => button.addEventListener("click", () => selectIncident(button.dataset.id)));
}

async function selectIncident(incidentId) {
  state.activeId = incidentId;
  renderQueue();
  if (state.stream) state.stream.close();
  try {
    state.active = await api(`/incidents/${encodeURIComponent(incidentId)}`);
    renderIncident();
    connectStream(incidentId);
  } catch (error) {
    showToast(error.message);
  }
}

function connectStream(incidentId) {
  const stream = new EventSource(`/incidents/${encodeURIComponent(incidentId)}/stream`);
  state.stream = stream;
  stream.addEventListener("update", (event) => {
    const payload = JSON.parse(event.data);
    if (state.activeId !== incidentId) return;
    state.active = payload.incident;
    upsertIncident(payload.incident);
    renderQueue();
    renderIncident();
    if (payload.incident.terminal) stream.close();
  });
  stream.onopen = () => {
    $("connection").classList.remove("offline");
    $("connection").lastChild.textContent = " Live stream connected";
  };
  stream.onerror = () => {
    if (!state.active?.terminal) {
      $("connection").classList.add("offline");
      $("connection").lastChild.textContent = " Stream reconnecting";
    }
  };
}

function renderIncident() {
  const incident = state.active;
  if (!incident) return;
  $("empty-state").classList.add("hidden");
  $("incident-view").classList.remove("hidden");
  const alert = incident.alert || {};
  const labels = alert.labels || {};
  const annotations = alert.annotations || {};
  const graph = incident.state || {};
  $("status-pill").textContent = incident.status;
  $("status-pill").className = `status-pill ${statusClass(incident.status)}`;
  $("incident-mode").textContent = incident.mode;
  $("incident-id").textContent = incident.incident_id;
  $("incident-title").textContent = labels.alertname || "Incident";
  $("incident-summary").textContent = annotations.summary || "No summary provided";
  $("incident-service").textContent = labels.service || "unknown";
  $("incident-severity").textContent = graph.severity || labels.severity || "unassigned";
  $("tool-used").textContent = graph.tool_calls || 0;
  $("tool-budget").textContent = incident.tool_budget;
  $("stage-badge").textContent = incident.stage;
  renderIntegration(incident);
  renderStages(incident.stage);
  renderTimeline(incident.events || []);
  renderPlan(graph.plan || { steps: [] });
  renderHypotheses(graph.hypotheses || []);
  renderEvidence(graph.evidence || []);
  renderApproval(incident);
  renderVerification(graph.verification);
}

function renderIntegration(incident) {
  const banner = $("integration-banner");
  if (incident.mode === "demo") {
    banner.classList.remove("live");
    banner.innerHTML = '<span class="integration-icon">⌁</span><div><strong>Guided demo</strong><span>Telemetry and actions are simulated; no model tokens are used.</span></div>';
  } else {
    banner.classList.add("live");
    banner.innerHTML = '<span class="integration-icon">↗</span><div><strong>Hybrid live integration</strong><span>Prometheus, Jaeger, and OpenSearch reads are live. Writes and verification still use the scripted shop.</span></div>';
  }
}

function renderStages(stage) {
  const current = Math.max(0, stageOrder.indexOf(stage));
  document.querySelectorAll("#stage-rail li").forEach((item, index) => {
    item.classList.toggle("done", index < current);
    item.classList.toggle("current", index === current);
    if (index < current) item.querySelector("span").textContent = "✓";
    else item.querySelector("span").textContent = String(index + 1);
  });
}

function renderTimeline(events) {
  const timeline = $("timeline");
  if (!events.length) {
    timeline.innerHTML = '<div class="timeline-empty">Waiting for the first event…</div>';
    return;
  }
  timeline.innerHTML = [...events].reverse().map((event) => `<div class="timeline-item">
    <small>${escapeHtml(friendlyTime(event.time))} · ${escapeHtml(event.type)}</small>
    <strong>${escapeHtml(event.message)}</strong>
  </div>`).join("");
}

function renderPlan(plan) {
  const steps = plan.steps || [];
  $("plan-count").textContent = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  $("plan-list").innerHTML = steps.length ? steps.map((step, index) => `<div class="plan-step">
    <span>${index + 1}</span><div><strong>${escapeHtml(step.tool)}</strong><small>${escapeHtml(JSON.stringify(step.args || {}))}</small></div><em>queued</em>
  </div>`).join("") : '<div class="list-empty">No reads queued at this stage.</div>';
}

function renderHypotheses(hypotheses) {
  const list = $("hypothesis-list");
  if (!hypotheses.length) {
    list.innerHTML = '<div class="list-empty">No evidence-backed hypothesis yet.</div>';
    return;
  }
  const ranked = [...hypotheses].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
  list.innerHTML = ranked.map((item) => {
    const confidence = Math.round((item.confidence || 0) * 100);
    return `<article class="hypothesis"><div class="hypothesis-top"><span class="hypothesis-service">${escapeHtml(item.service)}</span><span class="confidence">${confidence}%</span></div>
      <div class="confidence-bar"><i style="width:${confidence}%"></i></div><p>${escapeHtml(item.statement)}</p><small>${(item.evidence_refs || []).length} evidence reference(s)</small></article>`;
  }).join("");
}

function renderEvidence(evidence) {
  $("evidence-count").textContent = evidence.length;
  $("evidence-list").innerHTML = evidence.length ? evidence.map((item) => `<article class="evidence-card">
    <span class="evidence-icon">${escapeHtml(String(item.kind || "ref").slice(0, 2))}</span><div><strong>${escapeHtml(item.ref)}</strong><small>${escapeHtml(item.kind)} · ${escapeHtml(item.source)}</small></div>
  </article>`).join("") : '<div class="list-empty">Read-only evidence will appear here.</div>';
}

function renderApproval(incident) {
  const panel = $("approval-panel");
  const approval = incident.approval;
  if (!approval || incident.status !== "AWAITING_APPROVAL") {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const command = approval.command || {};
  $("approval-command").textContent = `${command.action || "action"}(${JSON.stringify(command.args || {})})`;
  $("approval-effect").textContent = approval.expected_effect || "No expected effect supplied";
  $("approval-hash").textContent = approval.payload_hash || "—";
}

function renderVerification(verification) {
  const panel = $("verification-panel");
  if (!verification?.recovered) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("verification-copy").textContent = `${verification.signal || "Recovery signal"} returned to a healthy range.`;
}

async function startDemo() {
  setBusy(true);
  try {
    const incident = await api("/api/demo", { method: "POST", body: "{}" });
    await loadIncidents();
    await selectIncident(incident.incident_id);
    showToast("Guided incident started");
  } catch (error) { showToast(error.message); }
  finally { setBusy(false); }
}

async function startLive() {
  setBusy(true);
  const alert = {
    status: "firing",
    labels: { alertname: "PaymentServiceFailed", service: "checkout", severity: "critical" },
    annotations: { summary: "Payment service failed during checkout" },
    startsAt: new Date().toISOString(),
  };
  try {
    const incident = await api("/alerts?config_id=c1_single&tool_budget=10", { method: "POST", body: JSON.stringify(alert) });
    await loadIncidents();
    await selectIncident(incident.incident_id);
    showToast("Live investigation started");
  } catch (error) { showToast(error.message); }
  finally { setBusy(false); }
}

async function decide(decision) {
  const incident = state.active;
  if (!incident?.approval) return;
  setBusy(true);
  try {
    await api(`/incidents/${encodeURIComponent(incident.incident_id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ decision, payload_hash: decision === "approve" ? incident.approval.payload_hash : null }),
    });
    showToast(decision === "approve" ? "Exact action approved" : "Action rejected");
  } catch (error) { showToast(error.message); }
  finally { setBusy(false); }
}

function setBusy(busy) {
  ["demo-button", "empty-demo-button", "live-button", "approve-button", "reject-button"].forEach((id) => {
    $(id).disabled = busy;
  });
}

$("demo-button").addEventListener("click", startDemo);
$("empty-demo-button").addEventListener("click", startDemo);
$("live-button").addEventListener("click", startLive);
$("refresh-button").addEventListener("click", () => loadIncidents());
$("approve-button").addEventListener("click", () => decide("approve"));
$("reject-button").addEventListener("click", () => decide("reject"));
$("copy-command").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("approval-command").textContent);
  showToast("Command copied");
});

loadIncidents().then(connectIncidentFeed);
