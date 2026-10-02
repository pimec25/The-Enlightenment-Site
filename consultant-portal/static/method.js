"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { schema: null, data: { version: 1, records: {}, gates: {} }, active: 0, dirty: false, report: null };
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; };
  const message = (text) => { $("message").textContent = text; };
  const changed = () => { state.dirty = true; state.report = null; $("report").hidden = true; message("Unsaved changes — download your case before leaving."); };
  function input(field, record) {
    const label = el("label", field.label, "field");
    let node;
    if (field.type === "select") {
      node = el("select");
      node.append(new Option("Choose…", ""));
      field.options.forEach(value => node.append(new Option(value, value)));
    } else if (field.type === "textarea") node = el("textarea");
    else { node = el("input"); node.type = field.type === "score" ? "number" : field.type; }
    if (field.type === "score") { node.min = "1"; node.max = "5"; node.step = "1"; }
    if (field.type === "number") node.step = "any";
    node.maxLength = 8000;
    node.value = record[field.key] || "";
    node.addEventListener("input", () => { record[field.key] = node.value; changed(); });
    label.append(node); return label;
  }
  function fields(definitions, record) { const box = el("div", null, "fields"); definitions.forEach(field => box.append(input(field, record))); return box; }
  function render() {
    $("stages").replaceChildren();
    state.schema.stages.forEach((stage, index) => {
      const button = el("button", `${index + 1}. ${stage.title}`);
      if (index === state.active) button.setAttribute("aria-current", "step");
      button.addEventListener("click", () => { state.active = index; render(); });
      $("stages").append(button);
    });
    const stage = state.schema.stages[state.active];
    const section = el("section", null, "stage");
    const top = el("div", null, "stage-top"); top.append(el("h2", `${state.active + 1}. ${stage.title}`), el("span", stage.period, "tag")); section.append(top);
    section.append(el("p", "Deliverable: " + stage.output, "muted"));
    stage.registers.forEach(reg => {
      const rows = state.data.records[reg.key] ||= [];
      const details = el("details"); details.open = rows.length > 0 || reg === stage.registers[0];
      const summary = el("summary", `${reg.title} (${rows.length})`); details.append(summary, el("p", reg.hint, "muted"));
      const rowContainer = el("div");
      function showRows() {
        rowContainer.replaceChildren(); summary.textContent = `${reg.title} (${rows.length})`;
        rows.forEach((record, index) => {
          const row = el("div", null, "row"); row.append(el("h3", `Record ${index + 1}`), fields(reg.fields, record));
          const remove = el("button", "Remove record", "remove");
          remove.addEventListener("click", () => { if (confirm("Remove this record from the case?")) { rows.splice(index, 1); changed(); showRows(); } });
          row.append(remove); rowContainer.append(row);
        });
      }
      showRows(); details.append(rowContainer);
      const add = el("button", "+ Add record", "secondary");
      add.addEventListener("click", () => { if (rows.length >= 200) return message("Maximum 200 records per register."); rows.push({}); details.open = true; changed(); showRows(); });
      details.append(add); section.append(details);
    });
    const gate = el("div", null, "gate"); gate.append(el("h3", "Stage readiness and approval"), el("p", stage.gate));
    const record = state.data.gates[String(state.active)] ||= {};
    gate.append(fields(state.schema.gate_fields, record)); section.append(gate);
    $("workspace").replaceChildren(section);
  }
  async function review(data) {
    const response = await fetch("/consultant/method/review", {method: "POST", credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(data)});
    if (!response.headers.get("content-type")?.includes("application/json")) throw new Error("The service is warming up or your session has expired. Reopen Analysis agent from the dashboard after downloading your case.");
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The case could not be reviewed.");
    return result;
  }
  function download(value, name) {
    const blob = new Blob([JSON.stringify(value, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob); const anchor = el("a"); anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const format = value => value == null ? "—" : typeof value === "number" ? new Intl.NumberFormat(undefined, {maximumFractionDigits: 2}).format(value) : Array.isArray(value) ? value.join("; ") : String(value);
  function table(container, title, columns, rows) {
    const section = el("div", null, "report-section"); section.append(el("h3", title));
    if (!rows.length) { section.append(el("p", "No eligible records yet.", "muted")); container.append(section); return; }
    const wrap = el("div", null, "table-wrap"), grid = el("table"), head = el("thead"), tr = el("tr");
    columns.forEach(([key, label]) => tr.append(el("th", label))); head.append(tr); grid.append(head);
    const body = el("tbody"); rows.forEach(row => { const line = el("tr"); columns.forEach(([key]) => line.append(el("td", format(row[key])))); body.append(line); });
    grid.append(body); wrap.append(grid); section.append(wrap); container.append(section);
  }
  function showReport(report) {
    const out = $("report-content"); out.replaceChildren();
    const stats = el("div", null, "stats");
    [["register_records", "Records collected"], ["recorded_stage_acceptances", "Stage acceptances"], ["opportunities", "Estimated opportunities"], ["review_flags", "Items to review"]].forEach(([key, label]) => { const card = el("div", null, "stat"); card.append(el("strong", report.summary[key]), el("span", label)); stats.append(card); }); out.append(stats);
    table(out, "Six-stage readiness", [["stage", "Stage"], ["status", "Recorded status"], ["missing_registers", "Missing registers"], ["criterion", "Acceptance requirement"]], report.readiness);
    table(out, "Performance dashboard", [["measure", "Measure"], ["unit", "Unit"], ["change", "Actual minus baseline"], ["percent_change", "% change"], ["target_met", "Target met"], ["basis", "Comparison basis"]], report.performance);
    table(out, "Observed process flow", [["process", "Step / process"], ["processing_minutes", "Processing minutes"], ["waiting_minutes", "Waiting minutes"], ["total_minutes", "Total minutes"], ["waiting_percent", "Waiting %"]], report.process_flow);
    table(out, "Pilot comparison — investigate other causes before attributing improvement", [["initiative", "Initiative"], ["measure", "Measure"], ["change", "Actual minus baseline"], ["percent_change", "% change"], ["decision", "Recorded decision / next step"]], report.pilots);
    table(out, "Opportunity ranges — estimates only", [["id", "ID"], ["name", "Opportunity"], ["category", "Category"], ["unit", "Unit"], ["period", "Period"], ["low", "Low"], ["high", "High"]], report.opportunities);
    report.pareto.forEach(group => {
      out.append(el("p", `${group.category} · ${group.unit} · ${group.period}: ${group.items_to_80_percent} of ${group.total_items} opportunities reach 80% of the total low estimate. This is the observed distribution, not an assumed 80/20 rule.`));
      table(out, "Pareto ranking", [["name", "Opportunity"], ["low", "Low estimate"], ["cumulative_percent", "Cumulative %"]], group.ranked);
    });
    table(out, "Prioritized roadmap — equal-weight scores", [["initiative", "Initiative"], ["impact", "Impact / 5"], ["feasibility", "Feasibility / 5"], ["score", "Mean / 5"], ["decision", "Recorded decision"]], report.priorities);
    table(out, "Validated benefit totals — kept separate by category, unit and period", [["category", "Category"], ["unit", "Unit"], ["period", "Period"], ["achieved_to_date", "Achieved to date"], ["estimated_annualized", "Estimated annualized"]], report.benefits);
    out.append(el("p", "Cash release is not profit. Capacity is not financial value unless it enables profitable demand or reduces actual expense. Estimated annualized benefits are not savings achieved to date.", "notice"));
    const flags = el("section"); flags.append(el("h3", "Next actions and review flags"));
    const list = el("ul"); report.warnings.forEach(text => list.append(el("li", text, "flag"))); if (!report.warnings.length) list.append(el("li", "No automated flags. Human evidence and stage acceptance checks still apply.")); flags.append(list); out.append(flags);
    table(out, "Suggested follow-up dates — confirm with the client", [["review", "Review"], ["date", "Date"]], report.follow_up);
    const pack = el("details"); pack.append(el("summary", "Engagement deliverables and evidence pack"));
    state.schema.stages.forEach((stage, index) => {
      pack.append(el("h2", stage.output));
      stage.registers.forEach(reg => {
        report.case.records[reg.key].forEach((row, i) => table(pack, `${reg.title} · Record ${i + 1}`, [["field", "Information"], ["value", "Recorded evidence / value"]], reg.fields.filter(f => row[f.key]).map(f => ({field: f.label, value: row[f.key]}))));
      });
      table(pack, "Recorded stage decision", [["field", "Information"], ["value", "Value"]], state.schema.gate_fields.map(f => ({field: f.label, value: report.case.gates[String(index)][f.key]})));
    }); out.append(pack);
    $("report").hidden = false; $("report").scrollIntoView({behavior: "smooth"});
  }
  $("save").addEventListener("click", () => { download(state.data, "pimec-method-case.json"); state.dirty = false; message("Case download started. Keep the file in your approved client storage."); });
  $("review").addEventListener("click", async () => {
    $("review").disabled = true; message("Reviewing evidence, readiness and calculations…");
    try { state.report = await review(state.data); showReport(state.report); message("Review ready. Download your case to preserve your entries."); }
    catch (error) { message(error.message); }
    finally { $("review").disabled = false; }
  });
  $("import").addEventListener("change", async event => {
    const file = event.target.files[0]; if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error("Case exceeds the 2 MB limit.");
      if (state.dirty && !confirm("Replace unsaved entries with this case?")) return;
      const report = await review(JSON.parse(await file.text()));
      state.data = report.case; state.report = null; state.dirty = false; state.active = 0; $("report").hidden = true; render(); message("Case opened. Changes must be downloaded to save them.");
    } catch (error) { message("Could not open case: " + error.message); }
    finally { event.target.value = ""; }
  });
  $("new").addEventListener("click", () => { if (!confirm("Start a new case? Download the current case first if you need to keep it.")) return; state.data = {version: 1, records: {}, gates: {}}; state.active = 0; changed(); render(); });
  $("export-report").addEventListener("click", () => { if (state.report) download(state.report, "pimec-method-review.json"); });
  $("print").addEventListener("click", () => { document.querySelectorAll("#report details").forEach(node => node.open = true); window.print(); });
  window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
  fetch("/consultant/method/schema", {credentials: "same-origin", cache: "no-store"}).then(async response => {
    if (!response.ok) throw new Error("Sign in again through the Consultant dashboard to open the workspace.");
    state.schema = await response.json(); render(); message("Start with the charter, or open a saved case.");
  }).catch(error => { message(error.message); $("review").disabled = true; });
})();
