// Короткая проверка настоящего интерфейса без установки браузерных пакетов.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, basename } from 'node:path';
import assert from 'node:assert/strict';

const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const profile = mkdtempSync(join(tmpdir(), 'truba-models-'));
const port = 9231;
const browser = spawn(edge, [
  '--headless=new', '--no-first-run', '--no-default-browser-check',
  '--window-size=1280,800',
  '--remote-allow-origins=*', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, 'http://127.0.0.1:8765/pult',
], { windowsHide: true, stdio: 'ignore' });

let socket;
try {
  let page;
  for (let i = 0; i < 40; i++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      page = pages.find(item => item.type === 'page' && item.url.includes('/pult'));
      if (page) break;
    } catch { /* Браузер ещё запускается. */ }
    await new Promise(done => setTimeout(done, 250));
  }
  assert.ok(page, 'Edge did not open the pult');
  socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((done, fail) => {
    socket.addEventListener('open', done, { once: true });
    socket.addEventListener('error', fail, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const reply = JSON.parse(event.data);
    if (reply.id && pending.has(reply.id)) {
      const { done, fail } = pending.get(reply.id);
      pending.delete(reply.id);
      reply.error ? fail(Error(reply.error.message)) : done(reply.result);
    }
  });
  const call = (method, params = {}) => new Promise((done, fail) => {
    const id = ++sequence;
    pending.set(id, { done, fail });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.text);
    return result.result.value;
  };
  let panelReady = false;
  for (let i = 0; i < 40; i++) {
    panelReady = await evaluate('typeof панельГолосЭлементы !== "undefined" && !!панельГолосЭлементы?.корень?.isConnected');
    if (panelReady) break;
    await new Promise(done => setTimeout(done, 200));
  }
  assert.ok(panelReady, 'Panel voice controls did not render');
  if (process.env.TRUBA_SCREENSHOT === '1') {
    const screenshot = await call('Page.captureScreenshot', { format: 'png' });
    const target = join(tmpdir(), 'truba_panel_actions.png');
    writeFileSync(target, Buffer.from(screenshot.data, 'base64'));
    process.stdout.write(`Screenshot: ${target}\n`);
  }
  const panel = await evaluate(`(async () => {
    const original = window.fetch;
    const calls = [];
    window.fetch = async (url, options) => {
      if (String(url).startsWith('/api/voice/')) {
        calls.push(String(url));
        return new Response(JSON.stringify({ ok: true, voice: {
          running: String(url).endsWith('/toggle'), mode: 'name', in_conversation: false,
        }}), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return original(url, options);
    };
    голосГолос = { running: false, mode: 'off', in_conversation: false };
    обновитьПанельГолос();
    document.querySelector('.панель-голос .голос-кнопка.главная').click();
    for (let i = 0; i < 30 && панельГолосЖдём; i++) {
      await new Promise(done => setTimeout(done, 10));
    }
    window.fetch = original;
    return {
      calls, label: document.querySelector('.панель-голос .голос-кнопка.главная').textContent,
      mode: document.querySelector('.панель-голос select').value,
    };
  })()`);
  assert.deepEqual(panel.calls, ['/api/voice/mode', '/api/voice/toggle']);
  assert.equal(panel.label, 'Выключить голос');
  assert.equal(panel.mode, 'name');
  for (let i = 0; i < 40; i++) {
    if (await evaluate('!!document.querySelector(\'[data-раздел="настройки"]\')')) break;
    await new Promise(done => setTimeout(done, 200));
  }
  await evaluate('document.querySelector(\'[data-раздел="настройки"]\').click()');
  let ready = false;
  for (let i = 0; i < 40; i++) {
    ready = await evaluate('typeof настрЭлементы !== "undefined" && !!настрЭлементы && настрЭлементы.model.tagName === "INPUT" && !!настрЭлементы.провайдеры.deepseek');
    if (ready) break;
    await new Promise(done => setTimeout(done, 200));
  }
  assert.ok(ready, 'Editable model field did not load');
  const models = await evaluate(`(async () => {
    const original = window.fetch;
    window.fetch = async (url, options) => String(url).endsWith('/api/settings/models')
      ? new Response(JSON.stringify({ ok: true, models: ['future-model', 'another-model'] }),
          { status: 200, headers: { 'Content-Type': 'application/json' } })
      : original(url, options);
    настрЭлементы.provider.value = 'openai';
    настрЭлементы.provider.dispatchEvent(new Event('change'));
    await настрЗагрузитьМодели();
    настрЭлементы.model.value = 'my-own-model';
    настрЭлементы.model.dispatchEvent(new Event('input'));
    const result = {
      choices: [...настрЭлементы.списокМоделей.options].map(option => option.value),
      current: настрТекущаяМодель(),
      eyeOnly: !настрЭлементы.ключПоказать.textContent.trim(),
    };
    window.fetch = original;
    return result;
  })()`);
  assert.deepEqual(models.choices, ['future-model', 'another-model']);
  assert.equal(models.current, 'my-own-model');
  assert.equal(models.eyeOnly, true);
  const eye = await evaluate(`(async () => {
    настрЭлементы.model.value = настрЭлементы.провайдеры.openai.model;
    настрЭлементы.ключПоказать.click();
    for (let i = 0; i < 30 && настрЭлементы.api_key.type !== 'text'; i++) {
      await new Promise(done => setTimeout(done, 50));
    }
    const revealed = настрЭлементы.api_key.type === 'text' && настрЭлементы.api_key.value.length > 0;
    настрЭлементы.ключПоказать.click();
    return { revealed, hiddenAgain: настрЭлементы.api_key.type === 'password' };
  })()`);
  assert.equal(eye.revealed, true);
  assert.equal(eye.hiddenAgain, true);
  if (process.env.TRUBA_SCREENSHOT === '1') {
    const screenshot = await call('Page.captureScreenshot', { format: 'png' });
    const target = join(tmpdir(), 'truba_model_settings.png');
    writeFileSync(target, Buffer.from(screenshot.data, 'base64'));
    process.stdout.write(`Settings screenshot: ${target}\n`);
  }
  process.stdout.write('UI OK: panel voice controls; live model list; arbitrary model ID; saved-key eye.\n');
} finally {
  socket?.close();
  browser.kill();
  const safe = resolve(profile);
  if (safe.startsWith(resolve(tmpdir()) + '\\') && basename(safe).startsWith('truba-models-')) {
    try { rmSync(safe, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
    catch { /* Edge may still be releasing its profile. */ }
  }
}
