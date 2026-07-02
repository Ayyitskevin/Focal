// Renders scripts/og-card.html to static/og-card.png (1200x630) — the link
// preview served as og:image on the marketing pages. Dev-time only; the PNG is
// committed, so this needs to run only when the card design changes.
//
//   npm i --no-save playwright   # once, anywhere
//   node scripts/generate-og-card.mjs
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "og-card.html");
const out = path.join(here, "..", "static", "og-card.png");

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM || "/opt/pw-browsers/chromium",
}).catch(() => chromium.launch());
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.goto(`file://${src}`, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: out });
await browser.close();
console.log(`wrote ${out}`);
