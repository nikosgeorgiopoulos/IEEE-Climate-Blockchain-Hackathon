// Tamper Lab: edit an anchored reading in the database and watch verification fail.
"use strict";

Topbar.init("tamper");

const $ = (id) => document.getElementById(id);
const state = { reading: null, busy: false };
const params = new URLSearchParams(location.search);

function setBusy(b) {
  state.busy = b;
  for (const id of ["btn-tamper", "btn-verify", "btn-restore", "device", "reading"]) $(id).disabled = b;
  if (!b) $("btn-restore").disabled = !(state.reading && state.reading.tampered);
}

async function loadDevices() {
  const devices = (await API.get("/api/devices")).filter((d) => d.latest);
  const sel = $("device");
  sel.replaceChildren(
    ...devices.map((d) =>
      el("option", { value: d.id }, `${d.name || d.id}  ·  ${(SOURCE[d.source] || {}).short || d.source}`)
    )
  );
  const wanted = params.get("device") || (devices.find((d) => d.id === "sim-07") || devices[0] || {}).id;
  if (wanted) sel.value = wanted;
  await loadReadings();
}

async function loadReadings() {
  const id = $("device").value;
  if (!id) return;
  const data = await API.get(`/api/devices/${encodeURIComponent(id)}/readings?hours=72&anchored_only=true`);
  const rows = data.readings.slice(-80).reverse();
  const sel = $("reading");
  if (!rows.length) {
    sel.replaceChildren(el("option", { value: "" }, "No anchored readings yet — wait for the next batch"));
    showReading(null);
    return;
  }
  const orig = (r) => (r.tampered ? r.original_pm25 : r.pm25);
  const spike = rows.reduce((a, b) => (orig(b) > orig(a) ? b : a));
  sel.replaceChildren(
    ...rows.map((r) =>
      el(
        "option",
        { value: r.id },
        `${fmt.dateTime(r.ts)} · PM2.5 ${fmt.num(orig(r), 2)} · batch #${r.batch_id}` +
          (r.id === spike.id ? "  ▲ spike" : "") +
          (r.tampered ? "  ✗ TAMPERED" : "")
      )
    )
  );
  const wanted = params.get("reading");
  sel.value = wanted && rows.some((r) => String(r.id) === wanted) ? wanted : spike.id;
  params.delete("reading");
  await selectReading();
}

async function selectReading() {
  const id = $("reading").value;
  if (!id) return showReading(null);
  showReading(await API.get(`/api/readings/${id}`));
  $("verify-out").replaceChildren(el("div", { class: "empty" }, "Press ", el("b", {}, "Verify"), " to check this reading, or tamper with it first."));
  $("explain").replaceChildren();
}

function showReading(r) {
  state.reading = r;
  if (!r) {
    $("orig-val").replaceChildren("–", el("small", {}, "µg/m³ PM2.5"));
    $("orig-meta").textContent = "–";
    $("edit-val").value = "";
    setBusy(false);
    return;
  }
  const original = r.tampered ? r.original_pm25 : r.pm25;
  $("orig-val").replaceChildren(fmt.num(original, 2), el("small", {}, "µg/m³ PM2.5"));
  $("orig-meta").replaceChildren(
    `${r.device_id} · ${fmt.dateTime(r.ts)} · batch #${r.batch_id}`,
    r.anchor_block ? ` · block ${r.anchor_block}` : "",
    el("br"),
    "signature ",
    el("span", { class: "mono" }, fmt.hash(r.signature_hex, 14))
  );
  const card = $("edit-card");
  if (r.tampered) {
    $("edit-val").value = fmt.num(r.pm25, 2);
    card.classList.add("dirty");
    $("edit-seal").className = "seal pill bad";
    $("edit-seal").textContent = "✗ edited in DB";
    $("edit-meta").textContent = `Changed from ${fmt.num(original, 2)} to ${fmt.num(r.pm25, 2)} µg/m³ directly in SQLite, after anchoring.`;
  } else {
    $("edit-val").value = (Math.round(original * 0.3 * 100) / 100).toFixed(2);
    card.classList.remove("dirty");
    $("edit-seal").className = "seal pill neutral";
    $("edit-seal").textContent = "database row";
    $("edit-meta").textContent = "Default edit: × 0.3 — make the spike disappear before the inspector looks.";
  }
  setBusy(false);
}

async function refreshReading() {
  showReading(await API.get(`/api/readings/${state.reading.id}`));
  // keep the dropdown labels in sync (✗ TAMPERED marker)
  const keep = state.reading.id;
  params.set("reading", keep);
  await loadReadings();
}

function explain(report) {
  const box = $("explain");
  const block = report.anchor && report.anchor.block;
  const where = block ? `at block ${block}` : `in batch #${report.batch_id}`;
  if (report.status === "tampered") {
    const steps = report.checks.filter((c) => c.status === "fail").map((c) => c.step);
    const insider = steps.length === 1 && steps[0] === 3;
    box.replaceChildren(
      el("div", { class: "explain" },
        el("b", {}, `The value was changed after it was anchored on-chain ${where}. The change is detectable by anyone, forever.`),
        el("div", { class: "muted", style: { marginTop: "6px", fontSize: "14px" } },
          insider
            ? "Even with a freshly forged, valid signature, the record no longer hashes to the leaf committed in the Merkle root that is on the public ledger (step 3)."
            : `The edited row no longer matches its signature (step 2) and no longer hashes to the leaf committed in the anchored Merkle root (step 3). Failed steps: ${steps.join(", ")}.`)
      )
    );
  } else if (report.status === "verified") {
    box.replaceChildren(el("div", { class: "explain ok" },
      el("b", {}, "All five checks pass."), " The database row matches what was signed and anchored ", where, "."));
  } else {
    box.replaceChildren();
  }
}

async function runVerify() {
  if (!state.reading) return;
  setBusy(true);
  try {
    const report = await Verify.run(state.reading.id, $("verify-out"));
    $("verify-meta").textContent = `reading #${report.reading_id} · ${new Date().toLocaleTimeString()}`;
    explain(report);
  } catch (e) {
    toast(`Verify failed: ${e.message}`);
  } finally {
    setBusy(false);
  }
}

$("device").addEventListener("change", () => loadReadings().catch((e) => toast(e.message)));
$("reading").addEventListener("change", () => selectReading().catch((e) => toast(e.message)));
$("btn-verify").addEventListener("click", runVerify);

$("btn-tamper").addEventListener("click", async () => {
  if (!state.reading) return;
  const value = parseFloat($("edit-val").value);
  setBusy(true);
  try {
    await API.post(`/api/demo/tamper/${state.reading.id}`, {
      pm25: Number.isFinite(value) ? value : null,
      resign: $("opt-resign").checked,
    });
    $("explain").replaceChildren();
    await refreshReading();
    toast("Database row edited. Now anyone verifies it…");
    await runVerify();
  } catch (e) {
    toast(`Tamper failed: ${e.message}`);
    setBusy(false);
  }
});

$("btn-restore").addEventListener("click", async () => {
  if (!state.reading) return;
  setBusy(true);
  try {
    await API.post(`/api/demo/restore/${state.reading.id}`);
    $("explain").replaceChildren();
    await refreshReading();
    await runVerify();
  } catch (e) {
    toast(`Restore failed: ${e.message}`);
    setBusy(false);
  }
});

Topbar.onHealth((h) => {
  $("demo-off").classList.toggle("hidden", h.demo_mode);
  $("btn-tamper").disabled = state.busy || !h.demo_mode;
});

loadDevices().catch((e) => toast(`Could not load devices: ${e.message}`));
