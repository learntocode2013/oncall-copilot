const statusNode = document.querySelector("#status");
const faultsNode = document.querySelector("#faults");
const budgetInput = document.querySelector("#tool-budget");
const consoleLink = document.querySelector("#console-link");

function setStatus(message, isError) {
  statusNode.textContent = message;
  statusNode.classList.toggle("error", Boolean(isError));
}

function actionText(action) {
  if (!action) return "none";
  const args = Object.entries(action.args || [])
    .map(([key, value]) => `${key}=${value}`)
    .join(" ");
  return `${action.action} ${args}`.trim();
}

function card(fault) {
  const article = document.createElement("article");
  article.className = "card";

  const header = document.createElement("header");
  const title = document.createElement("h2");
  title.textContent = fault.fault_id;
  const badge = document.createElement("span");
  badge.className = fault.armed ? "badge on" : "badge";
  badge.textContent = fault.armed ? "Armed" : fault.current_variant || "unknown";
  header.append(title, badge);

  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `Flag ${fault.flag}. Arm sets ${fault.arm_variant}. Disarm sets ${fault.disarm_variant}. Current variant is ${fault.current_variant}.`;

  const truth = document.createElement("div");
  truth.className = "truth";
  const truthTitle = document.createElement("h3");
  truthTitle.textContent = "Ground truth. This stays on this page.";
  const cause = document.createElement("p");
  cause.textContent = `Root cause: ${fault.root_cause_services.join(", ")}`;
  const correct = document.createElement("p");
  correct.textContent = `Correct action: ${actionText(fault.correct_action)}`;
  const detector = document.createElement("p");
  detector.textContent = `Manifest detector: ${fault.detect.operator} ${fault.detect.threshold}`;
  const query = document.createElement("pre");
  query.textContent = fault.detect.query;
  truth.append(truthTitle, cause, correct, detector, query);

  const actions = document.createElement("div");
  actions.className = "actions";
  actions.append(
    button("Arm fault", "primary", () => run(fault, "arm")),
    button("Disarm", "", () => run(fault, "disarm")),
    button("Page the agent now", "", () => run(fault, "page"), !fault.can_page),
  );

  article.append(header, meta, truth, actions);
  if (fault.grafana_note) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = fault.grafana_note;
    article.append(note);
  } else if (!fault.can_page) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "No symptom is published for this fault, so this page cannot start the agent.";
    article.append(note);
  }
  return article;
}

function button(label, className, onClick, disabled) {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  if (className) node.className = className;
  node.disabled = Boolean(disabled);
  node.addEventListener("click", onClick);
  return node;
}

function budget() {
  const value = Number(budgetInput.value);
  if (!Number.isInteger(value) || value < 1 || value > 100) {
    throw new Error("Tool budget must be a whole number from 1 to 100.");
  }
  return value;
}

async function run(fault, action) {
  setStatus("");
  let response;
  try {
    const options = { method: "POST" };
    if (action === "page") {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({ tool_budget: budget() });
    }
    response = await fetch(`/api/faults/${encodeURIComponent(fault.fault_id)}/${action}`, options);
  } catch (error) {
    setStatus(error.message || "The lab request failed.", true);
    return;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    setStatus(payload.detail || "The lab rejected the action.", true);
    return;
  }
  if (action === "page") {
    const alert = payload.alert && payload.alert.alerts && payload.alert.alerts[0];
    const labels = (alert && alert.labels) || {};
    const incident = payload.incident_id ? ` Incident ${payload.incident_id}.` : "";
    setStatus(
      `Paged ${labels.service || "the service"} as ${labels.alertname || "the symptom"}.${incident} Open the incident console to follow it.`,
    );
  } else if (payload.changed) {
    const grafana = action === "arm" && fault.grafana_note ? " Grafana can page the agent after the rule holds." : "";
    setStatus(
      action === "arm"
        ? `${fault.flag} is now ${fault.arm_variant}.${grafana}`
        : `${fault.flag} is back to ${fault.disarm_variant}.`,
    );
  } else {
    setStatus(`${fault.flag} was already ${action === "arm" ? fault.arm_variant : fault.disarm_variant}.`);
  }
  await load();
}

function render(faults) {
  faultsNode.replaceChildren(...faults.map(card));
}

async function load() {
  const response = await fetch("/api/faults");
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    setStatus(payload.detail || "The lab could not read the faults.", true);
    return;
  }
  if (payload.console_url) consoleLink.href = payload.console_url;
  render(payload.faults || []);
}

document.querySelector("#budget-form").addEventListener("submit", (event) => event.preventDefault());
load().catch((error) => setStatus(error.message, true));
