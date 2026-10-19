// The Verify experience: five checks that animate in one by one.
// Used as a modal (map, sensor page) and inline (Tamper Lab).
"use strict";

const Verify = {
  STEP_MS: 320,

  explorerText(anchor) {
    if (!anchor) return null;
    if (anchor.backend === "polygon") return "View on Polygonscan ↗";
    if (anchor.backend === "ots") return "View OpenTimestamps proof ↗";
    return "View mock-chain record ↗";
  },

  // Open as a modal for one reading.
  async open(readingId) {
    const body = el("div", { class: "modal-body" }, el("div", { class: "empty" }, el("span", { class: "spinner" }), " verifying…"));
    const sub = el("div", { class: "sub" }, `Reading #${readingId}`);
    const close = () => backdrop.remove();
    const backdrop = el(
      "div",
      { class: "modal-backdrop", onclick: (e) => e.target === backdrop && close() },
      el(
        "div",
        { class: "modal", role: "dialog", "aria-modal": "true" },
        el("div", { class: "modal-head" },
          el("div", {}, el("h1", {}, "Verify reading"), sub),
          el("button", { class: "close-x", onclick: close, "aria-label": "Close" }, "×")
        ),
        body
      )
    );
    const onKey = (e) => {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
    document.body.append(backdrop);
    try {
      const report = await API.get(`/api/readings/${readingId}/verify`);
      const c = JSON.parse(report.canonical || "{}");
      sub.replaceChildren(
        `Reading #${report.reading_id} · `, el("b", {}, report.device_id), ` · ${fmt.dateTime(report.ts)} · PM2.5 `,
        el("b", {}, c.pm25 ?? "–"), " µg/m³"
      );
      await this.render(body, report, { animate: true });
      return report;
    } catch (e) {
      body.replaceChildren(el("div", { class: "empty" }, `Verification request failed: ${e.message}`));
    }
  },

  // Fetch + render into an existing container (Tamper Lab).
  async run(readingId, container, opts = {}) {
    container.replaceChildren(el("div", { class: "empty" }, el("span", { class: "spinner" }), " verifying…"));
    const report = await API.get(`/api/readings/${readingId}/verify`);
    await this.render(container, report, { animate: true, ...opts });
    return report;
  },

  rowFor(step, name) {
    return el(
      "div",
      { class: "check waiting", "data-step": step },
      el("div", { class: "ic" }, ""),
      el("div", {}, el("div", { class: "title" }, el("span", { class: "stepn" }, `${step}`), name), el("div", { class: "detail" }, "waiting…")),
      el("div", { class: "state" }, "")
    );
  },

  setRow(row, check) {
    const icons = { pass: "✓", fail: "✗", pending: "…" };
    const labels = { pass: "pass", fail: "FAILED", pending: "pending" };
    row.className = `check ${check.status}`;
    row.querySelector(".ic").textContent = icons[check.status];
    row.querySelector(".detail").textContent = check.detail;
    row.querySelector(".state").textContent = labels[check.status];
  },

  async render(container, report, { animate = true, compact = false } = {}) {
    const list = el("div", { class: "checks" });
    const rows = report.checks.map((c) => this.rowFor(c.step, c.name));
    list.append(...rows);
    const verdictSlot = el("div");
    const evidenceSlot = el("div");
    container.replaceChildren(list, verdictSlot, evidenceSlot);

    for (let i = 0; i < report.checks.length; i++) {
      if (animate) {
        rows[i].className = "check running";
        rows[i].querySelector(".detail").textContent = "checking…";
        await sleep(this.STEP_MS);
      }
      this.setRow(rows[i], report.checks[i]);
    }
    if (animate) await sleep(150);
    verdictSlot.replaceChildren(this.verdict(report));
    if (!compact) evidenceSlot.replaceChildren(this.evidence(report));
  },

  verdict(report) {
    if (report.status === "verified") {
      return el("div", { class: "verdict ok" }, el("div", { class: "big" }, "✓"),
        el("div", {}, el("b", {}, "Verified end-to-end"),
          el("span", {}, "This value is exactly what was signed at capture, committed in the batch and anchored on the public ledger.")));
    }
    if (report.status === "tampered") {
      const step = report.checks.find((c) => c.step === report.first_failed_step);
      return el("div", { class: "verdict bad" }, el("div", { class: "big" }, "✗"),
        el("div", {}, el("b", {}, `Tampering detected — failed at step ${report.first_failed_step}`),
          el("span", {}, step ? `${step.name}: ${step.detail}` : "")));
    }
    return el("div", { class: "verdict warn" }, el("div", { class: "big" }, "…"),
      el("div", {}, el("b", {}, "Signed — waiting for anchoring"),
        el("span", {}, "The signature is valid. Steps 3–5 complete once this reading is in an anchored batch.")));
  },

  evidence(report) {
    const a = report.anchor || {};
    const leafBad = report.stored_leaf_hash && report.leaf_hash !== report.stored_leaf_hash;
    const dl = el("dl", { class: "evidence" });
    const add = (k, v, cls) => dl.append(el("dt", {}, k), el("dd", { class: cls || null }, v ?? "–"));
    add("Signer", `${report.signer === "gateway" ? "ProofOfAir gateway" : "device key"} · ${fmt.hash(report.pubkey_hex, 16)}`);
    add("Signature", fmt.hash(report.signature_hex, 24));
    add("Leaf (recomputed)", fmt.hash(report.leaf_hash, 24), leafBad ? "bad" : null);
    if (report.stored_leaf_hash) add("Leaf (committed)", fmt.hash(report.stored_leaf_hash, 24));
    if (report.merkle_root) {
      add("Merkle root", el("span", {}, fmt.hash(report.merkle_root, 24), " ",
        el("button", { class: "btn sm icon", title: "Copy root", onclick: () => copyText(report.merkle_root) }, "⧉")));
    }
    if (report.batch_id) add("Batch", `#${report.batch_id} · leaf ${report.leaf_index}`);
    if (a.backend) add("Anchor", `${a.backend} · ${a.status}${a.block ? ` · block ${a.block}` : ""}`);
    if (a.tx) add("Transaction", fmt.hash(a.tx, 24));

    const wrap = el("div", {}, dl);

    if (report.proof && report.leaf_hash) {
      const path = el("div", { class: "proof-path" });
      path.append(el("div", { class: "proof-node leaf" }, el("span", { class: "k" }, "leaf"), fmt.hash(report.stored_leaf_hash || report.leaf_hash, 28)));
      report.proof.forEach((s, i) => {
        path.append(el("div", { class: "proof-arrow" }, `↓ sha256(0x01 ‖ ${s.pos === "L" ? "sibling ‖ node" : "node ‖ sibling"})`));
        path.append(el("div", { class: "proof-node" }, el("span", { class: "k" }, `sibling ${s.pos}`), fmt.hash(s.hash, 28)));
      });
      path.append(el("div", { class: "proof-arrow" }, "↓"));
      path.append(el("div", { class: "proof-node root" }, el("span", { class: "k" }, "root"), fmt.hash(report.merkle_root, 28)));
      wrap.append(el("details", { class: "box" }, el("summary", {}, `Merkle proof path (${report.proof.length} hashes)`), el("div", { class: "inner" }, path)));
    }
    wrap.append(el("details", { class: "box" }, el("summary", {}, "Canonical record (recomputed from the database)"),
      el("div", { class: "inner" }, el("pre", { class: "canon" }, report.canonical))));

    const actions = el("div", { class: "evidence-actions" });
    if (a.url) actions.append(el("a", { class: "btn primary", href: a.url, target: "_blank", rel: "noopener" }, this.explorerText(a)));
    actions.append(el("a", { class: "btn", href: `/api/readings/${report.reading_id}/verify`, target: "_blank" }, "Raw JSON report"));
    actions.append(el("a", { class: "btn", href: `/sensor.html?id=${encodeURIComponent(report.device_id)}` }, "Sensor page"));
    wrap.append(actions);
    if (a.backend === "ots" && a.status !== "confirmed") {
      wrap.append(el("p", { class: "muted" }, "Bitcoin anchoring via OpenTimestamps takes a few hours to confirm; until then step 5 shows as pending."));
    }
    return wrap;
  },
};
