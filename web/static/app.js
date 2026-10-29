const MODELS = {
  "nvidia/Nemotron-3_5-Lightning": "Lightning",
  "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "Nano",
  "nvidia/nemotron-3-super-120b-a12b": "Super",
  "nvidia/Nemotron-3-Ultra-550b-a55b": "Ultra",
};

const REASONS = {
  unsupported: "Unsupported",
  negated: "Negated",
  historical_resolved: "Historical",
  family_history: "Family history",
  screening_or_rule_out: "Screening or rule-out",
  hypothetical_plan: "Hypothetical",
  copied_forward_unclear: "Copied forward",
  rejected: "Rejected",
};

const GAPS = {
  missing: "Add",
  specificity_upgrade: "Specificity upgrade",
  combination: "Combination",
  unsupported_existing: "Unsupported on the claim",
};

const DECISIONS = {
  accept: "Accept",
  review: "Review",
  abstain: "Abstain",
};

const COACH = [
  "Step 1 of 3. Start with Margaret Whitmore. Her chart is the short path.",
  "Step 2 of 3. Audit chart puts the worklist on top. The quote sits under the rationale.",
  "Step 3 of 3. Dropped by Nemotron lists every refusal. The receipt names the model, the tokens, and the cost.",
];

const state = {
  seq: 0,
  patientId: null,
  payload: null,
  actions: {},
  steps: [],
  coach: 0,
};

const live = document.getElementById("live");
const picker = document.getElementById("picker");
const result = document.getElementById("result");
const evalPanel = document.getElementById("eval-panel");
const droppedDialog = document.getElementById("dropped");

function say(text) {
  live.textContent = text || "";
}

function shortModel(id) {
  if (!id) return "";
  return MODELS[id] || id;
}

function money(value) {
  const number = Number(value || 0);
  return "$" + number.toFixed(4);
}

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:") return parsed.href;
  } catch (_err) {
    return null;
  }
  return null;
}

function el(tag, attrs) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(function (entry) {
    const key = entry[0];
    const value = entry[1];
    if (value == null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, String(value));
  });
  for (let i = 2; i < arguments.length; i += 1) {
    const child = arguments[i];
    if (child == null || child === false) continue;
    node.append(child);
  }
  return node;
}

function highlight(container, note, quotes) {
  container.replaceChildren();
  const found = (quotes || []).filter(function (quote) {
    return quote && note.includes(quote);
  });
  if (!note) {
    found.forEach(function (quote) {
      container.append(el("p", {}, el("q", { text: quote })));
    });
    return;
  }
  const ranges = [];
  found.forEach(function (quote) {
    let from = 0;
    while (from < note.length) {
      const at = note.indexOf(quote, from);
      if (at < 0) break;
      ranges.push([at, at + quote.length]);
      from = at + quote.length;
    }
  });
  ranges.sort(function (a, b) { return a[0] - b[0]; });
  const merged = [];
  ranges.forEach(function (range) {
    const last = merged[merged.length - 1];
    if (!last || range[0] > last[1]) merged.push([range[0], range[1]]);
    else last[1] = Math.max(last[1], range[1]);
  });
  let cursor = 0;
  merged.forEach(function (range) {
    if (range[0] > cursor) container.append(note.slice(cursor, range[0]));
    container.append(el("mark", { text: note.slice(range[0], range[1]) }));
    cursor = range[1];
  });
  if (cursor < note.length) container.append(note.slice(cursor));
  (quotes || []).forEach(function (quote) {
    if (quote && note && !note.includes(quote)) {
      container.append(el("p", {}, el("q", { text: quote })));
    }
  });
}

function chip(text, className, title) {
  return el("span", { class: "chip " + className, text: text, title: title || "" });
}

function tagFor(payload) {
  const source = payload.delivery && payload.delivery.source;
  if (source === "cache") return "Cached";
  if (source === "not_sent") return "Not sent";
  const mode = payload.audit && payload.audit.mode;
  if (mode === "live") return "Live";
  if (mode === "degraded") return "Degraded";
  if (mode === "mock") return "Mock";
  return "Audit";
}

function showAuditTab() {
  document.getElementById("tab-audit").click();
}

async function runAudit(body, label) {
  const seq = ++state.seq;
  state.steps = [];
  state.patientId = body.patient_id || null;
  say(label || "Auditing…");
  document.querySelectorAll("[data-audit]").forEach(function (button) {
    button.disabled = button.getAttribute("data-audit") === body.patient_id;
  });
  showAuditTab();
  try {
    const response = await fetch("/api/audit/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (seq !== state.seq) return;
    const type = response.headers.get("content-type") || "";
    if (!type.includes("text/event-stream")) {
      const data = await response.json().catch(function () { return {}; });
      say("");
      showProblem(data.detail || "The audit did not run.");
      return;
    }
    await readSSE(response, function (event) {
      if (seq !== state.seq) return;
      if (event.name === "status") say(event.data.message || "");
      if (event.name === "step") {
        state.steps.push(event.data);
        renderProvisional(body.patient_id);
      }
      if (event.name === "error") showProblem(event.data.detail || "The audit did not run.");
      if (event.name === "result") renderResult(event.data);
    });
  } catch (_err) {
    if (seq === state.seq) showProblem("The page could not reach the demo server.");
  } finally {
    if (seq === state.seq) {
      document.querySelectorAll("[data-audit]").forEach(function (button) {
        button.disabled = false;
      });
    }
  }
}

function showProblem(message) {
  result.hidden = false;
  picker.hidden = true;
  const head = document.getElementById("result-head");
  head.replaceChildren(
    el("p", { class: "alert", role: "alert", text: message }),
    el("div", { class: "result-tools" },
      el("button", { type: "button", class: "ghost", id: "back-charts", text: "Back to charts" })
    )
  );
  document.getElementById("back-charts").addEventListener("click", showPicker);
  document.getElementById("worklist").replaceChildren();
  document.getElementById("rail").replaceChildren();
  document.getElementById("receipt").replaceChildren();
  say(message);
}

async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parseEvent(raw);
      if (parsed) onEvent(parsed);
      split = buffer.indexOf("\n\n");
    }
  }
}

function parseEvent(raw) {
  let name = "message";
  const data = [];
  raw.split("\n").forEach(function (line) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  });
  if (!data.length) return null;
  try {
    return { name: name, data: JSON.parse(data.join("\n")) };
  } catch (_err) {
    return null;
  }
}

function renderProvisional(patientId) {
  result.hidden = false;
  picker.hidden = true;
  const head = document.getElementById("result-head");
  if (!head.childElementCount) {
    head.replaceChildren(el("p", { class: "oknote", text: "Reading the chart…" }));
  }
  renderRail(state.steps, true);
}

function renderResult(payload) {
  state.payload = payload;
  state.actions = {};
  result.hidden = false;
  picker.hidden = true;
  const delivery = payload.delivery || {};
  say(delivery.message || "");
  const head = document.getElementById("result-head");
  head.replaceChildren();
  if (!payload.audit) {
    head.append(
      el("p", { class: "oknote", role: "status", text: delivery.message || "Checked." }),
      el("div", { class: "result-tools" },
        el("button", { type: "button", class: "ghost", text: "Back to charts", id: "back-charts" })
      )
    );
    document.getElementById("back-charts").addEventListener("click", showPicker);
    document.getElementById("worklist").replaceChildren();
    document.getElementById("rail").replaceChildren();
    document.getElementById("receipt").replaceChildren();
    return;
  }
  const audit = payload.audit;
  const summary = audit.summary || {};
  const kept = summary.worklist_count || 0;
  if (delivery.source === "cache") {
    head.append(el("p", { class: "cache-banner", role: "alert", id: "cache-banner",
      text: delivery.banner || "Showing a cached result: live capacity reached, try again shortly." }));
  }
  head.append(
    el("p", { class: "oknote", role: "status" },
      el("span", { class: "tag", text: tagFor(payload) }),
      document.createTextNode(delivery.message || "")
    )
  );
  const tools = el("div", { class: "result-tools" });
  tools.append(
    el("button", { type: "button", class: "ghost", text: "Back to charts", id: "back-charts" }),
    el("button", { type: "button", class: "ghost", text: "Re-run live", id: "rerun-live" })
  );
  if (delivery.source === "upstream") {
    tools.append(el("button", { type: "button", class: "ghost", text: "Show cached run", id: "show-cache" }));
  }
  head.append(tools);
  document.getElementById("back-charts").addEventListener("click", showPicker);
  document.getElementById("rerun-live").addEventListener("click", function () {
    runAudit({ patient_id: audit.patient_id, mode: "live", pace_ms: 0 }, "Running the live audit…");
  });
  const cacheButton = document.getElementById("show-cache");
  if (cacheButton) {
    cacheButton.addEventListener("click", function () {
      runAudit({ patient_id: audit.patient_id, mode: "cached", pace_ms: 30 }, "Opening the cached audit…");
    });
  }

  const claim = ((payload.chart && payload.chart.coded) || []).map(function (row) {
    return row.code;
  }).join(" · ");
  const score = el("div", { class: "score" });
  score.append(
    el("p", { class: "kept" }, document.createTextNode(String(kept)), el("span", { text: "to review" })),
    el("div", {},
      el("h2", { class: "headline", id: "work-heading", tabindex: "-1", text: summary.headline || (kept + " codes to review") }),
      el("p", { class: "subhead", text: (audit.patient_name || "Chart") + (claim ? " · On the claim " + claim : "") + " · " + (summary.candidates_generated || 0) + " candidates in" })
    ),
    el("button", { type: "button", id: "open-dropped", text: "Dropped by Nemotron (" + (summary.dropped_count || 0) + ")" })
  );
  head.append(score);
  document.getElementById("open-dropped").addEventListener("click", openDropped);
  const heading = document.getElementById("work-heading");
  if (heading) heading.focus();

  renderWorklist(payload);
  renderRail(audit.timeline || [], false);
  renderReceipt(audit);
}

function showPicker() {
  result.hidden = true;
  picker.hidden = false;
  say("");
  const card = document.getElementById("card-" + (state.patientId || "margaret-whitmore"));
  if (card) card.scrollIntoView({ block: "start" });
}

function renderWorklist(payload) {
  const box = document.getElementById("worklist");
  box.replaceChildren();
  const audit = payload.audit;
  const rows = audit.worklist || [];
  if (!rows.length) {
    box.append(el("div", { class: "empty-work" },
      el("h3", { text: "Nothing to sign." }),
      el("p", { text: "Nemotron dropped every candidate. Open the drawer to see why. That is the audit." })
    ));
  }
  rows.forEach(function (item) {
    box.append(renderRow(payload, item));
  });
  renderDepth(box, audit.depth);
}

// Which cascade depth ran. C4a stays at Lightning triage unless the chart has a combination or
// specificity candidate; then it runs Super, Tavily and Ultra on it.
function renderDepth(box, depth) {
  if (!depth || !depth.config) return;
  const text = depth.adaptive
    ? "Adaptive cascade: ran " + depth.depth_used + (depth.escalated ? " (" + depth.reason + ")." : ". " + (depth.reason || "") + ", so Super, Tavily and Ultra were skipped.")
    : "Cascade depth " + depth.config + ".";
  box.append(el("p", { class: "depth-note", text: text }));
}

function renderRow(payload, item) {
  const decision = item.decision || "review";
  const article = el("article", { class: "work-row decision-" + decision });
  const quotes = item.verified_quotes || item.supporting_quotes || [];
  const top = el("div", { class: "work-top" });
  top.append(
    el("p", { class: "code", text: item.icd10_code || "—" }),
    el("div", {},
      el("h3", { text: item.code_display || "Code" }),
      (function () {
        const chips = el("div", { class: "chips" });
        chips.append(chip(GAPS[item.gap_type] || item.gap_type || "Code", "gap"));
        chips.append(chip(DECISIONS[decision] || decision, decision));
        if (typeof item.confidence === "number") {
          chips.append(chip(Math.round(item.confidence * 100) + "% confidence", "confidence"));
        }
        if (item.decided_by) {
          chips.append(chip(shortModel(item.decided_by), "model", item.decided_by));
        }
        if (item.hcc_category && String(item.hcc_category).indexOf("HCC") === 0) {
          chips.append(chip(item.hcc_category, "hcc", item.hcc_description || ""));
        }
        if (item.upgrade_from) chips.append(chip("From " + item.upgrade_from, "from"));
        return chips;
      })()
    )
  );
  article.append(top);
  article.append(el("p", { class: "rationale", text: item.rationale || "" }));
  if (quotes[0]) article.append(el("p", { class: "quote-preview" }, el("q", { text: quotes[0] })));

  const actions = el("div", { class: "row-actions" });
  const accept = el("button", { type: "button", text: "Accept", "aria-pressed": state.actions[item.id] === "accept" ? "true" : "false" });
  const query = el("button", { type: "button", class: "ghost", text: "Draft physician query" });
  const dismiss = el("button", { type: "button", class: "ghost", text: "Dismiss", "aria-pressed": state.actions[item.id] === "dismiss" ? "true" : "false" });
  const evidenceButton = el("button", {
    type: "button",
    class: "ghost",
    text: "Evidence",
    "aria-expanded": "false",
    "aria-controls": "evidence-" + item.id,
  });
  actions.append(accept, query, dismiss, evidenceButton);
  article.append(actions);

  const evidence = el("div", { class: "evidence", id: "evidence-" + item.id, hidden: "hidden" });
  const note = el("div", { class: "note", tabindex: "0" });
  highlight(note, (payload.chart && payload.chart.note) || "", quotes);
  evidence.append(el("h4", { text: "Chart" }), note);
  if (item.lab) {
    const lab = item.lab;
    evidence.append(el("h4", { text: "Lab" }), el("p", {
      text: [lab.name, lab.value, lab.unit].filter(Boolean).join(" ") + (lab.loinc ? " · LOINC " + lab.loinc : ""),
    }));
  }
  evidence.append(el("h4", { text: "MEAT" }));
  const meat = el("ul", { class: "meat" });
  ["monitor", "evaluate", "assess", "treat"].forEach(function (key) {
    const value = item.meat && item.meat[key];
    const li = el("li", {}, el("span", { text: key[0].toUpperCase() + key.slice(1) }));
    li.append(el("strong", { text: value ? "Quoted" : "None" }));
    if (value) li.append(document.createTextNode(value));
    meat.append(li);
  });
  evidence.append(meat);
  (item.guideline_citations || []).forEach(function (citation) {
    evidence.append(renderCitation(citation));
  });
  const queryBox = el("div", { class: "query", hidden: "hidden" });
  evidence.append(queryBox);
  article.append(evidence);

  accept.addEventListener("click", function () {
    state.actions[item.id] = "accept";
    accept.setAttribute("aria-pressed", "true");
    dismiss.setAttribute("aria-pressed", "false");
    sendFeedback(payload, item, "accept");
  });
  dismiss.addEventListener("click", function () {
    state.actions[item.id] = "dismiss";
    dismiss.setAttribute("aria-pressed", "true");
    accept.setAttribute("aria-pressed", "false");
    sendFeedback(payload, item, "dismiss");
  });
  query.addEventListener("click", function () {
    state.actions[item.id] = "query";
    evidence.hidden = false;
    evidenceButton.setAttribute("aria-expanded", "true");
    queryBox.hidden = false;
    queryBox.replaceChildren();
    if (item.physician_query) {
      queryBox.append(
        el("p", { text: "Non-leading query. It asks for the clinical status and does not name a code." }),
        el("p", { text: item.physician_query }),
        el("p", { class: "meta", text: "Drafted for sign-off. Nothing was sent to a physician." })
      );
    } else {
      queryBox.append(el("p", { text: "No physician query was drafted for this row." }));
    }
    sendFeedback(payload, item, "query");
  });
  evidenceButton.addEventListener("click", function () {
    const open = evidence.hidden;
    evidence.hidden = !open;
    evidenceButton.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const mark = note.querySelector("mark");
      if (mark) note.scrollTop = Math.max(0, mark.offsetTop - 16);
    }
  });
  return article;
}

function renderCitation(citation) {
  const retrieved = citation.retrieved || "fixture";
  const provenance = citation.provenance || "";
  const where = citation.section ? citation.section + (citation.page ? " · p. " + citation.page : "") : "";
  let label = "Fixture";
  if (provenance === "tavily_live") {
    label = "Tavily live" + (retrieved === "cache" ? " (cached this run)" : "");
  } else if (provenance === "curated_fallback" || retrieved === "anchor") {
    label = "Curated fallback";
  } else if (retrieved === "live") label = "Tavily · live";
  else if (retrieved === "cache") label = "Tavily · cached" + (citation.cached_at ? " " + citation.cached_at : "");
  else label = "Fixture · not a live excerpt";
  const block = el("div", { class: "query" });
  const line = el("p", {}, chip(label, "cite"));
  if (citation.fiscal_year) line.append(chip(citation.fiscal_year + (where ? " · " + where : ""), "cite"));
  else if (where) line.append(chip(where, "cite"));
  if (citation.verified_verbatim) line.append(chip("Verbatim in official PDF", "cite"));
  if (citation.anchor_status === "superseded") line.append(chip("Curated passage superseded", "review"));
  const tierLabel = citation.source_label || (citation.source_tier === "secondary" ? "Secondary source" : citation.source_tier === "official" ? "Official guidelines" : "");
  if (tierLabel) line.append(chip(tierLabel, citation.source_tier === "secondary" ? "review" : "cite"));
  const href = safeUrl(citation.url);
  if (href) {
    line.append(el("a", { href: href, target: "_blank", rel: "noopener noreferrer", text: citation.title || citation.source || "Official source" }));
  } else if (citation.title) {
    line.append(document.createTextNode(" " + citation.title));
  }
  block.append(line);
  if (citation.quote) block.append(el("p", { text: citation.quote }));
  return block;
}

function renderRail(steps, activeLast) {
  const rail = document.getElementById("rail");
  rail.replaceChildren(el("h2", { text: "Which model did what" }));
  if (!steps.length) return;
  const ran = steps.filter(function (step) { return step.status !== "skipped"; }).map(function (step) {
    const who = shortModel(step.model);
    return who ? step.label + " (" + who + ")" : step.label;
  });
  rail.append(el("p", { text: ran.join(" → ") }));
  const list = el("ol", {});
  steps.forEach(function (step, index) {
    const skipped = step.status === "skipped";
    const current = activeLast && index === steps.length - 1;
    const row = el("li", { class: "step" + (skipped ? " skipped" : "") + (current ? " is-active" : "") });
    const who = shortModel(step.model);
    row.append(
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("div", {},
        el("strong", { text: step.label || step.step }),
        el("div", { class: "who", text: who || (step.step === "verifier" ? "Deterministic verifier" : "No model") }),
        el("div", { text: (skipped ? "Not needed. " : "") + (step.detail || "") })
      ),
      el("span", { class: "ms", text: skipped ? "—" : (step.latency_ms || 0) + " ms" })
    );
    list.append(row);
  });
  rail.append(list);
  if (steps.some(function (step) { return step.mock; })) {
    rail.append(el("p", { class: "disclaimer", text: "Recorded routing for this cached run, unless the audit above is marked Live." }));
  }
}

function renderReceipt(audit) {
  const box = document.getElementById("receipt");
  const receipt = audit.receipt || {};
  const totals = receipt.totals || {};
  box.replaceChildren(el("h2", { text: "Receipt" }));
  const table = el("table", {});
  (receipt.steps || []).forEach(function (step) {
    if (!step.calls && !step.cost_usd && !step.tavily_credits && !step.cache_hits) return;
    const who = shortModel(step.model) || step.step;
    const bits = [];
    if (step.calls) bits.push(step.calls + " calls");
    if (step.input_tokens || step.output_tokens) {
      bits.push((step.input_tokens || 0) + " in / " + (step.output_tokens || 0) + " out");
    }
    if (step.tavily_credits) bits.push(step.tavily_credits + " Tavily credits");
    if (step.cache_hits) bits.push(step.cache_hits + " cache hits");
    const row = el("tr", {});
    row.append(el("td", { text: who + (bits.length ? " · " + bits.join(" · ") : "") }), el("td", { text: money(step.cost_usd) }));
    table.append(row);
  });
  const total = el("tr", { class: "total" });
  total.append(
    el("td", { text: "Total · " + (totals.latency_ms || 0) + " ms · " + (totals.input_tokens || 0) + " in / " + (totals.output_tokens || 0) + " out" }),
    el("td", { text: money(totals.cost_usd) })
  );
  table.append(total);
  box.append(table);
  const credits = el("p", {
    text: "Tavily credits " + (totals.tavily_credits || 0) + " · cache hits " + (totals.tavily_cache_hits || 0),
  });
  box.append(credits);
  if (receipt.disclaimer) box.append(el("p", { class: "disclaimer", text: receipt.disclaimer }));
  else if (audit.mode === "mock") {
    box.append(el("p", { class: "disclaimer", text: "Mock or estimated usage. Not a live Token Factory bill." }));
  }
}

function openDropped() {
  const audit = state.payload && state.payload.audit;
  if (!audit) return;
  const list = document.getElementById("dropped-list");
  const title = document.getElementById("dropped-title");
  const count = (audit.summary && audit.summary.dropped_count) || (audit.dropped || []).length;
  title.textContent = "Dropped by Nemotron (" + count + ")";
  list.replaceChildren();
  (audit.dropped || []).forEach(function (item) {
    const block = el("article", { class: "drop" });
    const who = item.dropped_by === "verifier" ? "Verifier" : shortModel(item.model) || item.dropped_by || "";
    block.append(
      el("div", { class: "chips" },
        chip(REASONS[item.reason] || item.reason || "Dropped", "review"),
        chip(who, "model", item.model || "")
      ),
      el("h3", {}, el("span", { class: "code", text: item.icd10_code || "" }), document.createTextNode(" " + (item.code_display || "")))
    );
    if (item.reason_detail) block.append(el("p", { text: item.reason_detail }));
    (item.supporting_quotes || []).forEach(function (quote) {
      block.append(el("q", { text: quote }));
    });
    list.append(block);
  });
  if (!(audit.dropped || []).length) {
    list.append(el("p", { text: "Nothing was dropped." }));
  }
  droppedDialog.showModal();
}

async function sendFeedback(payload, item, action) {
  try {
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audit_id: payload.audit && payload.audit.audit_id,
        item_id: item.id,
        action: action,
      }),
    });
  } catch (_err) {
    /* The button state already records the choice on screen. */
  }
}

function showCoach() {
  const box = document.getElementById("coach");
  let done = false;
  try { done = localStorage.getItem("codesentinel-coach-v1") === "done"; } catch (_err) { done = false; }
  if (done) {
    box.hidden = true;
    return;
  }
  document.getElementById("coach-step").textContent = COACH[state.coach];
  document.getElementById("coach-next").textContent = state.coach === COACH.length - 1 ? "Done" : "Next";
}

document.getElementById("coach-next").addEventListener("click", function () {
  state.coach += 1;
  if (state.coach >= COACH.length) {
    try { localStorage.setItem("codesentinel-coach-v1", "done"); } catch (_err) { /* ignore */ }
    document.getElementById("coach").hidden = true;
    return;
  }
  showCoach();
});
document.getElementById("coach-skip").addEventListener("click", function () {
  try { localStorage.setItem("codesentinel-coach-v1", "done"); } catch (_err) { /* ignore */ }
  document.getElementById("coach").hidden = true;
});

document.querySelectorAll("[data-audit]").forEach(function (button) {
  button.addEventListener("click", function () {
    const patientId = button.getAttribute("data-audit");
    document.querySelectorAll(".spine nav button").forEach(function (nav) {
      nav.removeAttribute("aria-current");
      if (nav.getAttribute("data-jump") === patientId) nav.setAttribute("aria-current", "true");
    });
    runAudit({ patient_id: patientId, mode: "auto", pace_ms: 35 }, "Auditing…");
  });
});

document.querySelectorAll("[data-jump]").forEach(function (button) {
  button.addEventListener("click", function () {
    const id = button.getAttribute("data-jump");
    showAuditTab();
    showPicker();
    const card = document.getElementById("card-" + id);
    if (card) {
      card.scrollIntoView({ block: "start" });
      const audit = card.querySelector("[data-audit]");
      if (audit) audit.focus();
    }
    document.querySelectorAll(".spine nav button").forEach(function (nav) {
      nav.removeAttribute("aria-current");
    });
    button.setAttribute("aria-current", "true");
  });
});

document.getElementById("paste-form").addEventListener("submit", function (event) {
  event.preventDefault();
  const text = document.getElementById("paste").value.trim();
  if (!text) {
    showProblem("Paste a demo chart first (no PHI).");
    return;
  }
  runAudit({
    mode: "auto",
    pace_ms: 0,
    profile: {
      fhir_patient_id: "pasted-note",
      demographics: { full_name: "Pasted note", found: true },
      clinical_notes: [{ text: text }],
      observations: [],
      medications: [],
      conditions: [],
      encounters: [],
    },
  }, "Checking the note…");
});

const tabs = [document.getElementById("tab-audit"), document.getElementById("tab-eval")];
tabs.forEach(function (tab, index) {
  tab.addEventListener("click", function () {
    const auditOn = tab.id === "tab-audit";
    document.getElementById("audit-panel").hidden = !auditOn;
    evalPanel.hidden = auditOn;
    tabs.forEach(function (other, otherIndex) {
      const on = other === tab;
      other.setAttribute("aria-selected", on ? "true" : "false");
      other.tabIndex = on ? 0 : -1;
    });
    if (!auditOn) loadEval();
  });
  tab.addEventListener("keydown", function (event) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index + tabs.length - 1) % tabs.length;
    tabs[next].click();
    tabs[next].focus();
  });
});

async function loadEval() {
  if (evalPanel.dataset.loaded === "yes" || evalPanel.dataset.loaded === "loading") return;
  evalPanel.dataset.loaded = "loading";
  evalPanel.replaceChildren(el("p", { text: "Loading the eval tab…" }));
  try {
    const response = await fetch("/api/eval");
    const data = await response.json();
    renderEval(data);
    evalPanel.dataset.loaded = "yes";
  } catch (_err) {
    evalPanel.dataset.loaded = "";
    evalPanel.replaceChildren(el("p", { class: "alert", text: "The eval tab could not be loaded." }));
  }
}

function renderEval(data) {
  evalPanel.replaceChildren();
  const baseline = data.baseline || {};
  const block = el("div", { class: "eval-block" });
  block.append(
    el("h2", { text: "Before Nemotron, rules queue" }),
    el("p", { text: baseline.warning || "" }),
    el("p", { class: "meta", text: baseline.source || "" })
  );
  const numbers = el("div", { class: "numbers" });
  numbers.append(
    stat("Notes", baseline.notes),
    stat("Approved", baseline.approved),
    stat("Review items", baseline.review)
  );
  block.append(numbers);
  const after = el("article", {});
  after.append(el("h2", { text: "After Nemotron" }));
  if (data.status === "loaded" && (data.configs || []).length) {
    after.append(el("p", { text: (data.mode === "live" ? "Measured rows from the live Token Factory run, " + (data.source_label || "eval/results/live-v9/summary.json") + "." : "Mock regression rows from eval/results/mock/summary.json, not live model quality.") + " The MTSamples counts above stay queue counts, not precision." }));
    if (data.mode_detail) after.append(el("p", { class: "meta", text: data.mode_detail }));
    after.append(configTable(data.configs));
  } else {
    after.append(el("p", { class: "waiting", text: "Not measured yet." }));
    after.append(el("p", { text: data.metrics_note || "" }));
  }
  block.append(after);
  evalPanel.append(block);

  if ((data.measured_failures || []).length) {
    const measured = el("div", { class: "eval-block" });
    measured.append(el("h2", { text: "Published misses" }));
    const list = el("div", { class: "fail-list" });
    data.measured_failures.forEach(function (row) {
      list.append(el("article", { class: "fail" },
        el("h3", { text: row.title || "Miss" }),
        el("p", { text: row.cause || "" }),
        row.example ? el("p", { text: row.example }) : null
      ));
    });
    measured.append(list);
    evalPanel.append(measured);
  }

  const classes = el("div", { class: "eval-block" });
  classes.append(
    el("h2", { text: "Known failure classes" }),
    el("p", { text: "These are the traps the audit is built to catch. Rates are not filled in until eval/results says so." })
  );
  const list = el("div", { class: "fail-list" });
  (data.known_failure_classes || []).forEach(function (row) {
    list.append(el("article", { class: "fail" },
      el("h3", { text: row.title || "" }),
      el("p", { text: row.detail || "" })
    ));
  });
  classes.append(list);
  evalPanel.append(classes);
}

function stat(label, value) {
  return el("article", {}, el("strong", { text: value == null ? "—" : String(value) }), el("span", { text: label }));
}

function configTable(configs) {
  const keys = ["id", "label", "precision", "recall", "review_burden", "usd_per_chart", "tp", "fp", "fn", "p50_latency_s"];
  const table = el("table", { class: "bench" });
  const head = el("tr", {});
  keys.forEach(function (key) { head.append(el("th", { text: key.replaceAll("_", " ") })); });
  table.append(el("thead", {}, head));
  const body = el("tbody", {});
  configs.forEach(function (row) {
    const tr = el("tr", {});
    keys.forEach(function (key) {
      const value = row[key];
      tr.append(el("td", { text: value == null || value === "" ? "—" : String(value) }));
    });
    body.append(tr);
  });
  table.append(body);
  return table;
}

showCoach();
