const state = { metrics: [], store: "STORE1", view: "overview", filters: {} };
const money = value => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));

async function loadMetrics() {
  const response = await fetch("/api/metrics");
  if (!response.ok) throw new Error("Metrics could not be loaded");
  state.metrics = await response.json();
  renderTabs();
  renderStore();
}

function current() { return state.metrics.find(item => item.store === state.store); }

function renderTabs() {
  const tabs = document.querySelector("#store-tabs");
  tabs.innerHTML = state.metrics.map(item => `<button class="${item.store === state.store ? "active" : ""}" data-store="${item.store}">${item.store}</button>`).join("");
  tabs.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
    state.store = button.dataset.store;
    state.view = "overview";
    state.filters = {};
    renderTabs();
    renderStore();
    showView("overview");
  }));
}

function renderStore() {
  const data = current();
  document.querySelector("#store-title").textContent = data.store;
  document.querySelector("#quality-note").textContent = `${data.quality.valid_orders.toLocaleString()} clean orders · ${data.quality.rejected_orders} rejected · ${data.raw_user_rows.toLocaleString()} identity rows reconciled`;
  document.querySelector("#top-day-value").textContent = money(data.top_days[0].revenue);
  document.querySelector("#top-day-date").textContent = data.top_days[0].date;
  document.querySelector("#kpis").innerHTML = [
    ["Real unique users", data.unique_users.toLocaleString(), `${data.raw_user_rows - data.unique_users} alias/address/contact variations reconciled`],
    ["Author sets", data.unique_author_sets.toLocaleString(), "Distinct solo and co-author combinations"],
    ["Popular author set", data.popular_authors, `${data.popular_units.toLocaleString()} books sold`],
    ["Best buyer", `[${data.best_buyer_ids.join(", ")}]`, `${money(data.best_buyer_spend)} total spend`],
  ].map(([label, value, note]) => `<article class="kpi"><span class="label">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><p>${escapeHtml(note)}</p></article>`).join("");
  document.querySelector("#top-days").innerHTML = data.top_days.map((day, index) => `<div class="rank-row"><b class="rank">${index + 1}</b><strong>${day.date}</strong><span>${money(day.revenue)}</span></div>`).join("");
  drawChart(data.daily_revenue);
}

function drawChart(rows) {
  const svg = document.querySelector("#revenue-chart");
  const width = 900, height = 330, left = 64, right = 18, top = 18, bottom = 42;
  const values = rows.map(row => row.revenue);
  const max = Math.max(...values) * 1.08;
  const x = index => left + (index / Math.max(rows.length - 1, 1)) * (width - left - right);
  const y = value => top + (1 - value / max) * (height - top - bottom);
  const path = rows.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(row.revenue).toFixed(1)}`).join(" ");
  const area = `${path} L${x(rows.length - 1)},${height - bottom} L${left},${height - bottom} Z`;
  const grids = [0, .25, .5, .75, 1].map(level => {
    const yy = y(max * level);
    return `<line class="gridline" x1="${left}" x2="${width-right}" y1="${yy}" y2="${yy}"/><text class="axis-label" x="4" y="${yy+4}">${money(max*level).replace('.00','')}</text>`;
  }).join("");
  const ticks = [0, Math.floor((rows.length-1)/2), rows.length-1].map(index => `<text class="axis-label" text-anchor="middle" x="${x(index)}" y="${height-12}">${rows[index].date}</text>`).join("");
  svg.innerHTML = `<defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e56d3f" stop-opacity=".24"/><stop offset="1" stop-color="#e56d3f" stop-opacity="0"/></linearGradient></defs>${grids}<path class="revenue-area" d="${area}"/><path class="revenue-line" d="${path}"/>${ticks}`;
}

function showView(view) {
  document.querySelectorAll(".view").forEach(node => node.classList.remove("active"));
  document.querySelector(view === "overview" ? "#overview-view" : "#explorer-view").classList.add("active");
}

async function applyAction(action) {
  state.store = action.store;
  state.view = action.view;
  state.filters = action.filters;
  renderTabs();
  renderStore();
  if (action.view === "overview") { showView("overview"); return; }
  showView("explorer");
  const params = new URLSearchParams({ limit: "50" });
  let endpoint;
  if (action.view === "users") {
    (action.filters.email_prefixes || []).forEach(prefix => params.append("email_prefix", prefix));
    if (action.filters.search) params.set("name_contains", action.filters.search);
    endpoint = `/api/users/${action.store}?${params}`;
  } else if (action.view === "books") {
    params.set("person_name_filter", action.filters.person_name_filter || "any");
    if (action.filters.search) params.set("title_contains", action.filters.search);
    endpoint = `/api/books/${action.store}?${params}`;
  } else {
    document.querySelector("#explorer-title").textContent = "Revenue filter applied";
    document.querySelector("#filter-summary").textContent = JSON.stringify(action.filters);
    document.querySelector("#explorer-head").innerHTML = "";
    document.querySelector("#explorer-body").innerHTML = "";
    return;
  }
  const response = await fetch(endpoint);
  const payload = await response.json();
  renderExplorer(action.view, payload, action.filters);
}

function renderExplorer(view, payload, filters) {
  const rows = payload.rows || [];
  document.querySelector("#explorer-title").textContent = view === "users" ? "Selected users" : "Filtered books";
  document.querySelector("#filter-summary").textContent = `${view === "users" ? payload.matched_rows + " rows / " + payload.unique_people + " real people" : payload.matched_books + " books"} · ${JSON.stringify(filters)}`;
  const columns = view === "users" ? ["user_id", "entity_id", "name", "email"] : ["book_id", "title", "authors", "title_has_person_name"];
  document.querySelector("#explorer-head").innerHTML = `<tr>${columns.map(column => `<th>${column.replaceAll("_", " ")}</th>`).join("")}</tr>`;
  document.querySelector("#explorer-body").innerHTML = rows.map(row => `<tr>${columns.map(column => `<td>${escapeHtml(row[column])}</td>`).join("")}</tr>`).join("");
}

function addMessage(role, text) {
  const messages = document.querySelector("#messages");
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
}

async function ask(message) {
  addMessage("user", message);
  const input = document.querySelector("#chat-input");
  input.disabled = true;
  try {
    const response = await fetch("/api/chat", { method: "POST", headers: {"content-type":"application/json"}, body: JSON.stringify({ message, current_store: state.store }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Agent request failed");
    addMessage("assistant", payload.answer || "Done.");
    document.querySelector("#tool-trace").textContent = payload.tool_trace.map(step => step.tool).join(" → ") || "No tool call returned.";
    for (const action of payload.actions || []) await applyAction(action);
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
}

document.querySelector("#chat-form").addEventListener("submit", event => {
  event.preventDefault();
  const input = document.querySelector("#chat-input");
  const value = input.value.trim();
  if (!value) return;
  input.value = "";
  ask(value);
});
document.querySelectorAll(".example").forEach(button => button.addEventListener("click", () => ask(button.dataset.prompt)));
document.querySelector("#reset-view").addEventListener("click", () => { state.view = "overview"; state.filters = {}; showView("overview"); });

loadMetrics().catch(error => addMessage("error", error.message));
