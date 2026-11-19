// One recorded demo attempt: headless Chromium films the llm-scope dashboard
// while a real ~2600-token generation runs on Strata, then reports tok/s.
// Usage: node capture.js <name> [maxtoks]  → prints JSON {video, still, tok_s, ...}
const { chromium } = require('playwright');
const { execFile } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');

const NAME = process.argv[2] || 'attempt';
const MAXTOK = Number(process.argv[3] || 2600);
const DIR = process.env.OUT_DIR || (process.env.HOME + '/Downloads/llm-scope-capture');
const SCOPE = process.env.SCOPE || 'http://127.0.0.1:8089';
const STRATA = process.env.STRATA || 'http://127.0.0.1:8080';
const MODEL = process.env.STRATA_MODEL || 'qwen3.8-flash-next-unsloth-ud-iq2_xs';

const PROMPT = process.env.PROMPT || 'Explain in exhaustive technical detail, continuing until you have written at least 2500 words and never truncating, how a modern transformer inference engine generates text token by token: attention, KV cache, speculative decoding, batching, quantization, and memory bandwidth. Write it as a continuous essay.';

const json = (url, body) => new Promise((resolve, reject) => {
  execFile('curl', ['-s', '-m', '300', '-H', 'content-type: application/json',
    ...(body ? ['-d', JSON.stringify(body)] : []), url],
    (err, stdout) => err ? reject(err) : resolve(stdout));
});

(async () => {
  await fs.mkdir(DIR, { recursive: true });
  const gen = json(`${STRATA}/v1/chat/completions`, { model: MODEL, max_tokens: MAXTOK, stream: false, messages: [{ role: 'user', content: PROMPT }] });
  await new Promise(r => setTimeout(r, 800)); // engine ramps while the browser boots
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, recordVideo: { dir: DIR, size: { width: 1600, height: 900 } } });
  const page = await context.newPage();
  await page.goto(`${SCOPE}/`, { waitUntil: 'load' });
  await page.click('button[data-s="900"]'); // 15m window so the run fills the chart
  const before = JSON.parse(await json(`${SCOPE}/api/state`)).completed;
  for (let i = 0; i < 300; i++) {
    if (JSON.parse(await json(`${SCOPE}/api/state`)).completed > before) break;
    await new Promise(r => setTimeout(r, 1000));
    if (i === 299) throw new Error('request did not complete within timeout');
  }
  await page.waitForTimeout(5000); // let the dashboard's own poll draw the bar
  const still = `${DIR}/${NAME}-still.png`;
  await page.screenshot({ path: still });
  await context.close();
  const video = await page.video().path();
  await fs.copyFile(video, `${DIR}/${NAME}.webm`);
  let cursor = 0, lastDone = null;
  for (;;) { // walk the retained events to the tail to report the run's tok/s
    const d = JSON.parse(await json(`${SCOPE}/api/events?after=${cursor}`));
    for (const e of d.events) if (e.type === 'done') lastDone = e;
    if (!d.more) break;
    cursor = d.cursor;
  }
  const answer = await gen;
  console.log(JSON.stringify({ video: `${DIR}/${NAME}.webm`, still, tok_s: lastDone.tok_s, tokens: lastDone.tokens, seconds: lastDone.seconds, answer_chars: answer.length }));
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
