// One sensor: 24 h chart, stats, and a readings table with a Verify button per row.
"use strict";

Topbar.init("");

const deviceId = new URLSearchParams(location.search).get("id");
let chart = null;

function drawChart(readings) {
  const labels = readings.map((r) => fmt.time(r.ts));
  const pm25 = readings.map((r) => r.pm25);
  const bad = readings.map((r) => (r.status === "tampered" ? r.pm25 : null));
  const data = {
    labels,
    datasets: [
      { label: "PM2.5", data: pm25, borderColor: "#22d3ee", backgroundColor: "rgba(34,211,238,0.12)", fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2.2 },
      { label: "PM10", data: readings.map((r) => r.pm10), borderColor: "#a78bfa", tension: 0.3, pointRadius: 0, borderWidth: 1.8 },
      { label: "WHO PM2.5 guideline", data: readings.map(() => WHO_PM25_24H), borderColor: "#cbd5e1", borderDash: [6, 6], pointRadius: 0, borderWidth: 1.4 },
      { label: "Fails verification", data: bad, borderColor: "#ef4444", backgroundColor: "#ef4444", showLine: false, pointRadius: 6, pointHoverRadius: 8 },
    ],
  };
  if (chart) {
    chart.data = data;
    chart.update("none");
    return;
  }
  if (typeof Chart === "undefined") {
    document.querySelector(".chart-box").replaceChildren(el("div", { class: "empty" }, "Chart.js could not be loaded (offline?)."));
    return;
  }
  Chart.defaults.color = "#8d9cb0";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  chart = new Chart(document.getElementById("chart"), {
    type: "line",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { color: "rgba(255,255,255,0.04)" }, ticks: { maxTicksLimit: 12 } },
        y: { beginAtZero: true, grid: { color: "rgba(255,255,255,0.06)" }, title: { display: true, text: "µg/m³" } },
      },
      plugins: { legend: { labels: { boxWidth: 14, usePointStyle: true } } },
    },
  });
}

function renderTable(readings) {
  const tbody = document.getElementById("rows");
  const rows = [...readings].reverse().slice(0, 300);
  if (!rows.length) {
    tbody.replaceChildren(el("tr", {}, el("td", { colspan: 8, class: "empty" }, "No readings yet.")));
    return;
  }
  tbody.replaceChildren(
    ...rows.map((r) =>
      el(
        "tr",
        { class: r.status === "tampered" ? "tampered" : "" },
        el("td", {}, fmt.dateTime(r.ts)),
        el("td", { class: "num", style: { color: pmBand(r.pm25).color, fontWeight: 700 } }, fmt.num(r.pm25, 2)),
        el("td", { class: "num" }, fmt.num(r.pm10, 2)),
        el("td", { class: "num" }, fmt.num(r.temperature, 1)),
        el("td", { class: "num" }, fmt.num(r.humidity, 0)),
        el("td", {}, r.batch_id ? el("a", { href: `/batches.html#batch-${r.batch_id}` }, `#${r.batch_id}`) : el("span", { class: "faint" }, "next")),
        el("td", {}, statusPill(r.status)),
        el("td", { class: "num" }, el("button", { class: "btn sm", onclick: () => Verify.open(r.id).then(load) }, "Verify"))
      )
    )
  );
  document.getElementById("table-meta").textContent = `${readings.length} readings · newest first`;
}

async function load() {
  if (!deviceId) {
    document.getElementById("title").textContent = "No sensor selected";
    return;
  }
  const [data, devices] = await Promise.all([
    API.get(`/api/devices/${encodeURIComponent(deviceId)}/readings?hours=24`),
    API.get("/api/devices"),
  ]);
  const d = data.device;
  const info = devices.find((x) => x.id === deviceId) || {};
  const src = SOURCE[d.source] || { label: d.source };
  document.title = `${d.name || d.id} · ProofOfAir`;
  document.getElementById("title").replaceChildren(d.name || d.id, " ", sourceTag(d.source));
  document.getElementById("subtitle").replaceChildren(
    `${src.label} · ${d.id} · ${fmt.num(d.lat, 4)}, ${fmt.num(d.lon, 4)} · signed by `,
    el("b", {}, d.signer === "gateway" ? "the ProofOfAir gateway" : "the device's own key"),
    " ", el("span", { class: "mono faint" }, fmt.hash(d.pubkey_hex, 12))
  );
  document.getElementById("head-status").replaceChildren(statusPill(info.status || "none"));

  const rs = data.readings;
  const last = rs[rs.length - 1];
  if (last) {
    const band = pmBand(last.pm25);
    document.getElementById("s-pm25").replaceChildren(fmt.num(last.pm25), el("small", {}, "µg/m³"));
    document.getElementById("s-pm25").style.color = band.color;
    document.getElementById("s-pm25-sub").textContent = `${band.name} (WHO: 15)`;
    document.getElementById("s-pm10").replaceChildren(fmt.num(last.pm10), el("small", {}, "µg/m³"));
    document.getElementById("s-time").textContent = `at ${fmt.dateTime(last.ts)}`;
    const vals = rs.map((r) => r.pm25).filter((v) => v !== null);
    const avg = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    document.getElementById("s-count").textContent = rs.length;
    document.getElementById("s-avg").textContent = `24 h mean PM2.5 ${fmt.num(avg)} µg/m³`;
  }
  const ok = rs.filter((r) => r.status === "verified").length;
  const badN = rs.filter((r) => r.status === "tampered").length;
  document.getElementById("s-ratio").textContent = rs.length ? `${Math.round((100 * ok) / rs.length)}%` : "–";
  document.getElementById("s-ratio").style.color = badN ? "var(--bad)" : "var(--ok)";
  document.getElementById("s-ratio-sub").textContent = `${ok} verified · ${rs.length - ok - badN} pending · ${badN} failing`;

  drawChart(rs);
  renderTable(rs);
}

load().catch((e) => toast(`Could not load sensor: ${e.message}`));
setInterval(() => load().catch(() => {}), 30000);
