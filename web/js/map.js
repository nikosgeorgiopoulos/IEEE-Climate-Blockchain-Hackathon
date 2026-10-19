// Live map of Athens: one marker per sensor, coloured by PM2.5, badged by verification status.
"use strict";

Topbar.init("index");

const map = L.map("map", { zoomControl: true, attributionControl: true }).setView([37.99, 23.73], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · data <a href="https://sensor.community">Sensor.Community</a>',
}).addTo(map);

const markers = new Map(); // device id -> L.Marker
let fitted = false;

document.getElementById("legend-scale").replaceChildren(
  ...PM_BANDS.map((b) => el("div", { style: { background: b.color } }, b.label))
);

function markerIcon(d) {
  const pm = d.latest ? d.latest.pm25 : null;
  const st = STATUS[d.status] || STATUS.none;
  return L.divIcon({
    className: "",
    iconSize: [38, 38],
    iconAnchor: [19, 19],
    popupAnchor: [0, -18],
    html: `<div class="pm-marker ${d.source === "simulator" ? "sim" : ""}" style="--c:${pmBand(pm).color}">
             ${pm === null ? "–" : Math.round(pm)}
             <span class="badge ${esc(d.status)}">${st.icon}</span>
           </div>`,
  });
}

function popupHtml(d) {
  const l = d.latest || {};
  const st = STATUS[d.status] || STATUS.none;
  const src = SOURCE[d.source] || { label: d.source, tag: "" };
  const ratio = d.verified_ratio === null ? "–" : `${d.n_verified}/${d.n_readings}`;
  return `
    <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
      <div><b style="font-size:15px">${esc(d.name || d.id)}</b><br>
        <span class="tag ${src.tag}">${esc(src.label)}</span>
        <span class="faint" style="font-size:12px"> ${esc(d.id)}</span></div>
      <span class="pill ${st.cls}">${st.icon} ${st.label}</span>
    </div>
    <div style="display:flex;gap:18px;margin:12px 0 6px">
      <div><div class="faint" style="font-size:11.5px">PM2.5</div>
        <div style="font-size:24px;font-weight:800;color:${pmBand(l.pm25).color}">${fmt.num(l.pm25)}</div></div>
      <div><div class="faint" style="font-size:11.5px">PM10</div>
        <div style="font-size:24px;font-weight:800">${fmt.num(l.pm10)}</div></div>
      <div><div class="faint" style="font-size:11.5px">verified (24 h)</div>
        <div style="font-size:24px;font-weight:800">${ratio}</div></div>
    </div>
    <div class="muted" style="font-size:12.5px">Last reading ${fmt.ago(l.ts)} · signed by ${d.signer === "gateway" ? "gateway" : "device"}</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <a class="btn sm primary" href="/sensor.html?id=${encodeURIComponent(d.id)}">Open sensor →</a>
      ${l.id ? `<button class="btn sm" onclick="Verify.open(${Number(l.id)})">Verify latest</button>` : ""}
    </div>`;
}

function renderList(devices) {
  const list = document.getElementById("device-list");
  const sorted = [...devices].sort((a, b) => ((b.latest || {}).pm25 ?? -1) - ((a.latest || {}).pm25 ?? -1));
  list.replaceChildren(
    ...sorted.map((d) => {
      const st = STATUS[d.status] || STATUS.none;
      const pm = d.latest ? d.latest.pm25 : null;
      return el(
        "div",
        {
          class: "device-row",
          title: d.id,
          onclick: () => {
            const m = markers.get(d.id);
            if (m) { map.flyTo(m.getLatLng(), 13, { duration: 0.8 }); m.openPopup(); }
          },
        },
        el("span", { class: "swatch", style: { background: pmBand(pm).color, width: "12px", height: "12px" } }),
        el("span", { class: "name" }, d.name || d.id, " ", sourceTag(d.source)),
        el("span", { class: "pm" }, fmt.num(pm)),
        el("span", { class: "mini-badge", style: { background: st.color, color: d.status === "pending" ? "#1a1000" : "#fff" } }, st.icon)
      );
    })
  );
  if (!devices.length) list.replaceChildren(el("div", { class: "empty" }, "No sensors yet — run make seed."));
}

async function refreshDevices() {
  try {
    const devices = (await API.get("/api/devices")).filter((d) => d.lat !== null && d.lon !== null);
    for (const d of devices) {
      let m = markers.get(d.id);
      if (!m) {
        m = L.marker([d.lat, d.lon], { icon: markerIcon(d), riseOnHover: true }).addTo(map);
        m.bindPopup(popupHtml(d), { maxWidth: 340 });
        markers.set(d.id, m);
      } else {
        m.setIcon(markerIcon(d));
        m.setPopupContent(popupHtml(d));
      }
    }
    if (!fitted && devices.length) {
      map.fitBounds(L.latLngBounds(devices.map((d) => [d.lat, d.lon])).pad(0.12), { maxZoom: 12 });
      fitted = true;
    }
    renderList(devices);
    const real = devices.filter((d) => d.source !== "simulator").length;
    document.getElementById("c-devices").textContent = devices.length;
    document.getElementById("c-devices-l").textContent = `sensors · ${real} real, ${devices.length - real} sim`;
  } catch (e) {
    console.error(e);
  }
}

Topbar.onHealth((h) => {
  document.getElementById("c-readings").textContent = h.counts.readings.toLocaleString();
  document.getElementById("c-batches").textContent = h.counts.anchored.toLocaleString();
  const la = h.last_anchor;
  const body = document.getElementById("last-anchor-body");
  if (la) {
    const label = la.anchor_backend === "polygon" ? "View on Polygonscan ↗" : la.anchor_backend === "ots" ? "OpenTimestamps ↗" : "View mock-chain record ↗";
    body.replaceChildren(
      el("div", { style: { color: "var(--text)", fontWeight: 600 } }, `Batch #${la.id}`, el("span", { class: "muted", style: { fontWeight: 400 } }, ` · ${fmt.ago(la.anchored_at)}`)),
      el("div", { class: "mono", style: { fontSize: "12.5px" } }, la.anchor_block ? `block ${la.anchor_block} · ` : "", fmt.hash(la.anchor_tx, 12)),
      la.anchor_url ? el("a", { href: la.anchor_url, target: "_blank", rel: "noopener", style: { fontSize: "13.5px" } }, label) : ""
    );
  }
});
setInterval(() => {
  const s = Topbar.secondsToBatch();
  document.getElementById("c-next").textContent = s === null ? "off" : fmt.mmss(s);
}, 1000);

refreshDevices();
setInterval(refreshDevices, 15000);
