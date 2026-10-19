// Batch list: root (copyable), leaf count, anchor status pill and explorer link.
"use strict";

Topbar.init("batches");

const BACKEND_LABEL = { mock: "Mock chain", polygon: "Polygon Amoy", ots: "Bitcoin (OTS)" };

function period(b) {
  if (!b.period_start) return "–";
  const s = b.period_start.replace("T", " ").replace("Z", "");
  const e = b.period_end.replace("T", " ").replace("Z", "");
  return s.slice(0, 10) === e.slice(0, 10) ? `${s.slice(0, 16)} → ${e.slice(11, 16)}` : `${s.slice(0, 16)} → ${e.slice(0, 16)}`;
}

function linkLabel(b) {
  if (b.anchor_backend === "polygon") return "Polygonscan ↗";
  if (b.anchor_backend === "ots") return "OTS proof ↗";
  return "mock record ↗";
}

async function load() {
  const data = await API.get("/api/batches?limit=500");
  const rows = data.batches;
  const tbody = document.getElementById("rows");
  if (!rows.length) {
    tbody.replaceChildren(el("tr", {}, el("td", { colspan: 9, class: "empty" }, "No batches yet. They close automatically, or press “Close a batch now”.")));
  } else {
    tbody.replaceChildren(
      ...rows.map((b) =>
        el(
          "tr",
          { id: `batch-${b.id}` },
          el("td", {}, el("b", {}, `#${b.id}`)),
          el("td", {}, fmt.ago(b.created_at)),
          el("td", { class: "mono", style: { fontSize: "13px" } }, period(b)),
          el("td", { class: "num" }, b.n_leaves.toLocaleString()),
          el("td", {},
            el("span", { class: "hash", title: b.merkle_root_hex }, fmt.hash(b.merkle_root_hex, 12)), " ",
            el("button", { class: "btn sm icon", title: "Copy full root", onclick: () => copyText(b.merkle_root_hex) }, "⧉")
          ),
          el("td", {}, BACKEND_LABEL[b.anchor_backend] || b.anchor_backend),
          el("td", {}, anchorPill(b.anchor_status)),
          el("td", { class: "num" }, b.anchor_block ?? "–"),
          el("td", {},
            b.anchor_url
              ? el("a", { href: b.anchor_url, target: "_blank", rel: "noopener", title: b.anchor_tx || "" }, linkLabel(b))
              : el("span", { class: "faint" }, b.anchor_status === "pending" ? "waiting…" : "–")
          )
        )
      )
    );
    if (location.hash) {
      const row = document.querySelector(location.hash);
      if (row && !row.dataset.scrolled) { row.scrollIntoView({ block: "center" }); row.style.outline = "2px solid var(--ok)"; row.dataset.scrolled = 1; }
    }
  }
  const leaves = rows.reduce((a, b) => a + b.n_leaves, 0);
  const anchored = rows.filter((b) => b.anchor_status === "confirmed").length;
  document.getElementById("b-total").textContent = data.total;
  document.getElementById("b-anchored").textContent = `${anchored} confirmed on-chain`;
  document.getElementById("b-leaves").textContent = leaves.toLocaleString();
  document.getElementById("b-avg").textContent = rows.length ? `≈ ${Math.round(leaves / rows.length)} readings per transaction` : "–";
}

Topbar.onHealth((h) => {
  document.getElementById("b-backend").textContent = BACKEND_LABEL[h.backend] || h.backend;
  document.getElementById("b-backend-sub").textContent =
    h.backend === "ots" ? "Bitcoin confirmation takes a few hours" : h.backend === "mock" ? "offline dev mode" : "testnet";
  document.getElementById("b-unbatched").textContent = `${h.counts.unbatched} readings waiting`;
  document.getElementById("btn-batch").classList.toggle("hidden", !h.demo_mode);
});
setInterval(() => {
  const s = Topbar.secondsToBatch();
  document.getElementById("b-next").textContent = s === null ? "off" : fmt.mmss(s);
}, 1000);

document.getElementById("btn-batch").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const b = await API.post("/api/demo/batch-now");
    toast(b.id ? `Batch #${b.id} closed: ${b.n_leaves} readings, status ${b.anchor_status}` : "Nothing to batch");
    await load();
    Topbar.refresh();
  } catch (err) {
    toast(`Failed: ${err.message}`);
  } finally {
    e.target.disabled = false;
  }
});

load().catch((e) => toast(e.message));
setInterval(() => load().catch(() => {}), 15000);
