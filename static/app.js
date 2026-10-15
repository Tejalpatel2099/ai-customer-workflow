/* AI Customer Workflow — single-page support inbox.
   Plain fetch() calls against the FastAPI JSON API; no framework. */

const state = { selectedId: null, tickets: [] };

const $ = (sel) => document.querySelector(sel);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function chip(text, kind) {
  return `<span class="chip chip-${kind}">${text}</span>`;
}

function statusKind(status) {
  return {
    "New": "new",
    "Pending approval": "pending",
    "Resolved": "resolved",
    "Needs attention": "attention",
  }[status] || "new";
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

async function loadStats() {
  const s = await api("/api/stats");
  $("#stat-total").textContent = s.total;
  $("#stat-new").textContent = s.by_status["New"];
  $("#stat-pending").textContent = s.by_status["Pending approval"];
  $("#stat-resolved").textContent = s.by_status["Resolved"];
  $("#stat-attention").textContent = s.by_status["Needs attention"];
  $("#stat-approval").textContent =
    s.approval_rate === null ? "–" : `${Math.round(s.approval_rate * 100)}%`;

  const badge = $("#mode-badge");
  badge.textContent = s.mode === "llm" ? "Engine: LLM" : "Engine: DEMO (rule-based)";
  badge.classList.toggle("mode-demo", s.mode !== "llm");
  $("#demo-banner").hidden = s.mode === "llm";

  const cats = Object.entries(s.by_category)
    .map(([c, n]) => `<span>${c}: <b>${n}</b></span>`).join("");
  const urg = Object.entries(s.by_urgency)
    .map(([u, n]) => `<span>${u}: <b>${n}</b></span>`).join("");
  $("#breakdown").innerHTML =
    `<div><h3>By category</h3>${cats}</div><div><h3>By urgency</h3>${urg}</div>`;

  $("#activity-list").innerHTML = s.recent_activity
    .map((e) => `<li><b>${e.action}</b> — ${e.detail}
      <span class="when">${e.created_at.replace("T", " ").slice(0, 16)} UTC</span></li>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Inbox
// ---------------------------------------------------------------------------

async function loadTickets() {
  const filter = $("#status-filter").value;
  const qs = filter ? `?status=${encodeURIComponent(filter)}` : "";
  state.tickets = await api(`/api/tickets${qs}`);
  const list = $("#ticket-list");
  list.innerHTML = state.tickets.map((t) => `
    <li class="ticket ${t.id === state.selectedId ? "selected" : ""}"
        data-id="${t.id}">
      <div class="ticket-top">
        <span class="ticket-subject">${t.subject}</span>
        ${chip(t.status, statusKind(t.status))}
      </div>
      <div class="ticket-meta">
        ${t.customer_name} · ${t.ai_category || t.category}
        ${t.urgency ? `· ${chip(t.urgency, "urg-" + t.urgency)}` : ""}
      </div>
    </li>`).join("");
  list.querySelectorAll(".ticket").forEach((el) =>
    el.addEventListener("click", () => selectTicket(Number(el.dataset.id))));
}

// ---------------------------------------------------------------------------
// Detail + approval workflow
// ---------------------------------------------------------------------------

async function selectTicket(id) {
  state.selectedId = id;
  const t = await api(`/api/tickets/${id}`);
  renderDetail(t);
  await loadTickets();
}

function renderDetail(t) {
  const el = $("#detail");
  const aiBlock = t.summary ? `
    <div class="ai-box">
      <div class="ai-row">
        ${chip("AI: " + t.ai_category, "cat")}
        ${chip("urgency: " + t.urgency, "urg-" + t.urgency)}
        ${chip("sentiment: " + t.sentiment, "sent")}
        ${chip("mode: " + t.mode, t.mode === "llm" ? "mode-llm" : "mode-demo-chip")}
      </div>
      <p><b>Summary:</b> ${t.summary}</p>
      <p><b>Recommended next action:</b> ${t.next_action}</p>
      <label class="draft-label" for="draft">Draft reply (editable)</label>
      <textarea id="draft" rows="9" ${t.status !== "Pending approval" ? "disabled" : ""}>${t.draft_reply}</textarea>
    </div>` : `<p class="empty">Not processed yet. Run the AI pipeline to generate a draft.</p>`;

  const actions = [];
  if (t.status === "New" || t.status === "Needs attention") {
    actions.push(`<button class="btn primary" id="btn-process">Process with AI</button>`);
  }
  if (t.status === "Pending approval") {
    actions.push(`<button class="btn approve" id="btn-approve">Approve &amp; send</button>`);
    actions.push(`<button class="btn" id="btn-edit">Save edit</button>`);
    actions.push(`<button class="btn reject" id="btn-reject">Reject</button>`);
  }

  el.innerHTML = `
    <h2>${t.subject}</h2>
    <p class="meta">${t.customer_name} &lt;${t.customer_email}&gt; · ${chip(t.status, statusKind(t.status))}</p>
    <p class="body">${t.body}</p>
    ${aiBlock}
    <div class="actions">${actions.join("")}</div>
    <p class="error" id="action-error"></p>`;

  const wire = (id, fn) => {
    const b = document.getElementById(id);
    if (b) b.addEventListener("click", fn);
  };
  wire("btn-process", () => act(t.id, "process"));
  wire("btn-approve", () => act(t.id, "approve"));
  wire("btn-reject", () => act(t.id, "reject"));
  wire("btn-edit", async () => {
    await act(t.id, "edit", { draft_reply: document.getElementById("draft").value });
  });
}

async function act(id, action, body) {
  const errEl = $("#action-error");
  try {
    const t = await api(`/api/tickets/${id}/${action}`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
    renderDetail(t);
    await Promise.all([loadTickets(), loadStats()]);
  } catch (err) {
    if (errEl) errEl.textContent = err.message;
  }
}

// ---------------------------------------------------------------------------

$("#status-filter").addEventListener("change", loadTickets);
Promise.all([loadTickets(), loadStats()]).catch((err) => {
  document.querySelector("#ticket-list").innerHTML =
    `<li class="ticket">Failed to load: ${err.message}</li>`;
});
