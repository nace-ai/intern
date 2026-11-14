// Record backup demo videos of the PixelDrex web app (real live runs against Drex, DeepInfra, Keenable).
// Usage: node scripts/record_demo.js <outdir> <name> <mode: scene|life|news|replay> <idea, or a saved world name for replay>
// Needs pixeldrex-web running on http://127.0.0.1:8000 and Playwright (npm i playwright).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [outDir, name, mode, idea] = process.argv.slice(2);
  fs.mkdirSync(outDir, { recursive: true });
  const size = { width: 1280, height: 860 };
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: size, recordVideo: { dir: outDir, size } });
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:8000/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  if (mode !== 'scene') {
    await page.click('#m-life');
    await page.waitForTimeout(500);
  }
  if (mode === 'news') {
    await page.check('#newsbox');
    await page.waitForTimeout(400);
  }
  if (mode === 'replay') {
    // click the saved-world chip (known-good rules, fresh Drex decisions)
    await page.waitForSelector(`button.chip.saved[data-world="${idea}"]`);
    await page.hover(`button.chip.saved[data-world="${idea}"]`);
    await page.waitForTimeout(600);
    await page.click(`button.chip.saved[data-world="${idea}"]`);
  } else {
    // type the idea visibly, then run
    await page.click('#idea');
    await page.fill('#idea', '');
    await page.type('#idea', idea, { delay: 35 });
    await page.waitForTimeout(500);
    await page.click('#go');
  }

  // keep the stage in view while it runs
  await page.evaluate(() => window.scrollTo({ top: 210, behavior: 'smooth' }));
  await page.waitForSelector('#canvas img[src*="/files/"]', { timeout: 240000 });
  await page.waitForTimeout(4500); // let the finished GIF loop
  // show the rules / sources / motion panel
  await page.evaluate(() => window.scrollTo({ top: 520, behavior: 'smooth' }));
  await page.waitForTimeout(3500);
  await page.evaluate(() => window.scrollTo({ top: 210, behavior: 'smooth' }));
  await page.waitForTimeout(2000);

  const video = page.video();
  await context.close();
  await browser.close();
  const src = await video.path();
  const dst = path.join(outDir, `${name}.webm`);
  fs.renameSync(src, dst);
  console.log(dst);
})();
