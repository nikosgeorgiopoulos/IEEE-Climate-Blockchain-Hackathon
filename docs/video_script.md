# Demo video script (target 4:00)

**Setup before recording**

- In `.env`: `BATCH_INTERVAL_SEC=120`. Use `ANCHOR_BACKEND=polygon` if the testnet wallet is
  funded, otherwise `mock`.
- 10 minutes before recording: `make demo`, then `make run`. `make demo` prints the URLs,
  including a Tamper Lab link pre-set to the Elefsina pollution spike.
- Browser window at 1920×1080, zoom 110% (use 100% for the Tamper Lab so the checks and the
  verdict fit on one screen). Close other tabs and hide the bookmarks bar.
- Open these tabs in order: Live map → Sensor (Elefsina, `sensor.html?id=sim-07`) →
  Polygonscan tx → Tamper Lab → Batches.

---

## 0:00–0:25 · Hook

*Visual: the live map of Athens, markers pulsing, the counters ticking.*

> "Every climate decision, from pollution fines to carbon credits, rests on sensor data.
> Who checks that the data wasn't changed?"

## 0:25–0:55 · Problem

*Visual: three short title cards, one sentence each.*

> "Greenwashing: a company reports the numbers that suit it."
> "Double counting: the same tonne of carbon gets sold twice, because nobody can audit the
> underlying data."
> "And most environmental data sits in an ordinary database: one `UPDATE` statement and a
> pollution spike quietly disappears."

## 0:55–2:40 · Live demo

1. **Map (0:55–1:15).**
   > "This is ProofOfAir, running on air-quality sensors around Athens: real Sensor.Community
   > stations, plus simulated ones so the city is covered. Colour is PM2.5 against the WHO
   > guideline. The green tick means every reading from that sensor verifies."
   Click the Elefsina marker to open the popup.

2. **Sensor page and Verify (1:15–1:50).**
   > "Elefsina, an industrial zone. Here's the last 24 hours, and the dashed line is the WHO
   > limit. Let's verify one reading."
   Click **Verify**. The five checks animate in turn:
   > "The record is rebuilt from the database, the Ed25519 signature checks out, the reading's
   > hash is in the batch, the Merkle proof leads to the root… and that root is on-chain."
   Click **View on Polygonscan**:
   > "Here's that exact root in a public Polygon transaction. Anyone can check this; you
   > don't have to trust us."

3. **Tamper Lab: hide the spike (1:50–2:25).**
   > "Now I'm the malicious database admin. Here's the afternoon emission spike: 43 µg/m³,
   > signed and anchored. I'll make it 13."
   Click **Tamper with database**. Step 2 and step 3 go red.
   > "Caught. The signature no longer matches, and the record no longer hashes to what was
   > anchored. The value was changed after it was anchored on-chain at block N, and anyone
   > can detect that, forever."
   Restore, tick **Insider attack**, and tamper again:
   > "What if the attacker also stole the signing key and re-signed? The signature passes,
   > but step 3 still fails, because the blockchain remembers the original."

4. **Restore (2:25–2:40).**
   Click **Restore**. All five checks turn green.
   > "Put the true value back, and it verifies again."

## 2:40–3:25 · How it works

*Visual: the "How it works" panel, or `docs/architecture.md` rendered.*

> "Four steps. **Sign:** each reading is serialized canonically and signed with Ed25519, on
> the device or at our gateway. **Batch:** every few minutes, new readings are grouped.
> **Merkle root:** they're hashed into a tree, and one 32-byte root commits to all of them.
> **Anchor:** that root goes on-chain in a single transaction."
>
> "So one transaction anchors thousands of readings. Anchoring a whole city every ten minutes
> costs cents a day on Polygon, or nothing with OpenTimestamps on Bitcoin."

*Visual: the Batches page, roughly 800 readings per transaction.*

## 3:25–4:00 · Impact and next steps

> "Cities can publish air-quality data that citizens can actually check. Auditors and carbon
> registries get evidence instead of spreadsheets. Citizen-science networks gain
> credibility."
>
> "Next: on-device signing with this €15 ESP32 node, so the data is protected from the sensor
> itself, then gateways run by several organizations, and links to carbon registries and the
> EU open-data portals."
>
> "ProofOfAir: the code is open source, and the link is below."

*End card: repo URL, "IEEE ClimateChain Global Hackathon · Track 4", the author's name.*

---

## Recording tips

- `BATCH_INTERVAL_SEC=120` keeps the "next batch" countdown short on screen.
- Run `make demo` about 10 minutes before you record, so a fresh batch has already been
  anchored.
- 1920×1080 with browser zoom at 110%.
- On Polygon, open the Polygonscan link once before recording so the page is cached.
- After recording, restore everything (Tamper Lab **Restore**, or
  `python scripts/tamper.py --restore ID`).
- Record the voice-over separately and keep the clicks unhurried: the verify animation
  takes about 1.8 s.
