// Shared helpers: API calls, formatting, the top bar. No build step, no framework.
"use strict";

const API = {
  async get(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail || r.statusText}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(`${r.status} ${data.detail || r.statusText}`);
    return data;
  },
};

const WHO_PM25_24H = 15; // WHO 2021 air quality guideline, µg/m³, 24-hour mean

const PM_BANDS = [
  { max: 15, color: "#22d3ee", label: "≤15", name: "Within WHO guideline" },
  { max: 25, color: "#60a5fa", label: "15–25", name: "Above guideline" },
  { max: 37.5, color: "#a78bfa", label: "25–37.5", name: "Elevated" },
  { max: 50, color: "#e879f9", label: "37.5–50", name: "High" },
  { max: Infinity, color: "#fda4af", label: ">50", name: "Very high" },
];

function pmBand(pm25) {
  if (pm25 === null || pm25 === undefined) return { color: "#5d6d82", label: "–", name: "No data" };
  return PM_BANDS.find((b) => pm25 <= b.max);
}

const STATUS = {
  verified: { cls: "ok", icon: "✓", label: "Verified", color: "var(--ok)" },
  tampered: { cls: "bad", icon: "✗", label: "Tampered", color: "var(--bad)" },
  pending: { cls: "warn", icon: "…", label: "Pending", color: "var(--warn)" },
  none: { cls: "neutral", icon: "–", label: "No data", color: "var(--faint)" },
};

const SOURCE = {
  sensor_community: { tag: "real", label: "Real · Sensor.Community", short: "REAL" },
  simulator: { tag: "sim", label: "Simulated", short: "SIM" },
  esp32: { tag: "esp32", label: "ESP32 · device-signed", short: "ESP32" },
};

const fmt = {
  num(v, d = 1) {
    return v === null || v === undefined ? "–" : Number(v).toFixed(d);
  },
  time(ts) {
    return ts ? new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "–";
  },
  dateTime(ts) {
    if (!ts) return "–";
    return new Date(ts).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  },
  ago(ts) {
    if (!ts) return "–";
    const s = Math.round((Date.now() - new Date(ts).getTime()) / 1000);
    if (s < 0) return "just now";
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
  },
  hash(h, n = 10) {
    if (!h) return "–";
    return h.length > n + 8 ? `${h.slice(0, n)}…${h.slice(-6)}` : h;
  },
  mmss(sec) {
    if (sec === null || sec === undefined) return "–";
    sec = Math.max(0, Math.round(sec));
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  },
};

// Tiny DOM builder: el("div", {class: "x", onclick: fn}, "text", childNode)
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function statusPill(status) {
  const s = STATUS[status] || STATUS.none;
  return el("span", { class: `pill ${s.cls}` }, `${s.icon} ${s.label}`);
}

function anchorPill(status) {
  const map = { confirmed: "ok", failed: "bad", pending: "warn", submitted: "warn" };
  return el("span", { class: `pill ${map[status] || "neutral"}` }, status || "—");
}

function sourceTag(source) {
  const s = SOURCE[source] || { tag: "", short: source };
  return el("span", { class: `tag ${s.tag}` }, s.short);
}

function toast(msg, ms = 2200) {
  const t = el("div", { class: "toast" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), ms);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    window.prompt("Copy:", text);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

const LOGO_SVG = `
<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
  <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#22c55e"/></linearGradient></defs>
  <path d="M16 2.5 27.5 7v8.6c0 7-4.9 12.2-11.5 13.9C9.4 27.8 4.5 22.6 4.5 15.6V7z" fill="none" stroke="url(#lg)" stroke-width="2.2" stroke-linejoin="round"/>
  <path d="M10 13.2c2-1.6 4-1.6 6 0s4 1.6 6 0M10 17.6c2-1.6 4-1.6 6 0s4 1.6 6 0" fill="none" stroke="#8fb3c9" stroke-width="1.6" stroke-linecap="round"/>
  <circle cx="22.7" cy="22.4" r="5.1" fill="#22c55e"/>
  <path d="m20.4 22.5 1.6 1.6 3-3.2" fill="none" stroke="#04130a" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

// Renders the shared top bar and keeps the "next batch" countdown ticking.
const Topbar = {
  health: null,
  fetchedAt: 0,
  listeners: [],
  init(active) {
    const links = [
      ["index", "/", "Live map"],
      ["batches", "/batches.html", "Batches"],
      ["tamper", "/tamper.html", "Tamper Lab"],
    ];
    const bar = el(
      "header",
      { class: "topbar" },
      el("a", { class: "brand", href: "/", html: `${LOGO_SVG}<span>ProofOfAir</span><small>tamper-evident air quality · Athens</small>` }),
      el(
        "nav",
        { class: "nav" },
        links.map(([key, href, label]) =>
          el("a", { href, class: `${key === active ? "active" : ""} ${key === "tamper" ? "lab" : ""}` }, label)
        )
      ),
      el("div", { class: "spacer" }),
      el("span", { class: "chip", id: "chip-backend" }, el("span", { class: "dot live" }), "connecting…"),
      el("span", { class: "chip", id: "chip-batch" }, "next batch ", el("b", {}, "–"))
    );
    document.body.prepend(bar);
    this.refresh();
    setInterval(() => this.refresh(), 10000);
    setInterval(() => this.tick(), 1000);
  },
  onHealth(fn) {
    this.listeners.push(fn);
    if (this.health) fn(this.health);
  },
  async refresh() {
    try {
      this.health = await API.get("/api/health");
      this.fetchedAt = Date.now();
      const h = this.health;
      const label = { mock: "mock chain (offline)", polygon: "Polygon Amoy", ots: "Bitcoin · OpenTimestamps" }[h.backend] || h.backend;
      document.getElementById("chip-backend").replaceChildren(
        el("span", { class: "dot live" }), "anchoring to ", el("b", {}, label)
      );
      this.listeners.forEach((fn) => fn(h));
      this.tick();
    } catch (e) {
      document.getElementById("chip-backend").replaceChildren(
        el("span", { class: "dot", style: { background: "var(--bad)" } }), "API offline"
      );
    }
  },
  secondsToBatch() {
    const h = this.health;
    if (!h || h.next_batch_in_sec === null) return null;
    return Math.max(0, h.next_batch_in_sec - (Date.now() - this.fetchedAt) / 1000);
  },
  tick() {
    const chip = document.getElementById("chip-batch");
    if (!chip || !this.health) return;
    const s = this.secondsToBatch();
    chip.replaceChildren("next batch ", el("b", {}, s === null ? "scheduler off" : fmt.mmss(s)));
    if (s !== null && s <= 0 && Date.now() - this.fetchedAt > 3000) this.refresh();
  },
};
