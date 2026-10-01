// Проверка мастера первого запуска на копии пульта (порт 8775, без голоса).
// Настройки отдаёт подмена в памяти: живой settings.json хозяина не трогаем.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, basename } from 'node:path';
import assert from 'node:assert/strict';

const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const profile = mkdtempSync(join(tmpdir(), 'truba-wizard-'));
const port = 9233;
const pult = 'http://127.0.0.1:8775/pult';
const browser = spawn(edge, [
  '--headless=new', '--no-first-run', '--no-default-browser-check',
  '--window-size=1280,820',
  '--remote-allow-origins=*', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, pult,
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
  /* Подмена ставится до загрузки страницы: `/api/settings` отдаёт
     `first_run_done: false`, голос выключен, устройств и segno нет. Настоящий
     сервер при этом запущен на 8775 без голоса и без записи настроек. */
  const подмена = await readFile(new URL('./wizard_stub.js', import.meta.url), 'utf8');
  await call('Page.enable');
  await call('Page.addScriptToEvaluateOnNewDocument', { source: подмена });
  await call('Page.navigate', { url: pult });
  await new Promise(готово => setTimeout(готово, 1500));

  let opened = false;
  for (let i = 0; i < 60; i++) {
    opened = await evaluate('!document.getElementById("мастер").hidden');
    if (opened) break;
    await new Promise(done => setTimeout(done, 200));
  }
  assert.ok(opened, 'The wizard did not open on a fresh install');
  assert.equal(await evaluate('document.getElementById("мастер-шаг").textContent'),
    'Шаг 1 из 6');
  /* Шаг 1: привет и «твой компьютер». */
  const первый = await evaluate(`(async () => {
    for (let i = 0; i < 40; i++) {
      if (document.querySelector('#мастер-тело .железо-строка')) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return {
      строки: [...document.querySelectorAll('#мастер-тело .железо-строка')].map(у => у.textContent),
      назадСпрятан: document.getElementById('мастер-назад').hidden,
    };
  })()`);
  assert.equal(первый.строки.length, 3);
  assert.match(первый.строки.join(' '), /Процессор/);
  assert.match(первый.строки.join(' '), /Память/);
  assert.match(первый.строки.join(' '), /Видеокарта/);
  assert.equal(первый.назадСпрятан, true, 'Back must be hidden on the first step');

  /* Шаг 2: мозг. */
  const мозг = await evaluate(`(async () => {
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 60; i++) {
      if (мастерШаг === 2 && мастерШаги[2] && мастерШаги[2].провайдеры.deepseek) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    const эл = мастерШаги[2] || {};
    return { загружен: !!(эл.провайдеры && эл.провайдеры.deepseek), шаг: мастерШаг };
  })()`);
  assert.equal(мозг.загружен, true, 'The brain step did not load the services');
  const мозгДанные = await evaluate(`(() => {
    const эл = мастерШаги[2];
    return {
      шаг: мастерШаг,
      сервисы: [...эл.provider.options].map(пункт => пункт.textContent),
      подсказка: эл.подсказка.textContent,
      ключСпрятан: эл.api_key.type === 'password',
      назадВиден: !document.getElementById('мастер-назад').hidden,
    };
  })()`);
  assert.equal(мозг.шаг, 2);
  assert.ok(мозгДанные.сервисы.includes('DeepSeek'));
  assert.match(мозгДанные.подсказка, /Create new API key/);
  assert.equal(мозгДанные.ключСпрятан, true);
  assert.equal(мозгДанные.назадВиден, true);

  const безКлюча = await evaluate(`(async () => {
    window.confirm = () => true;
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 40; i++) {
      if (мастерШаг === 3) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return { шаг: мастерШаг, отправлено: window.__отправлено.slice() };
  })()`);
  assert.equal(безКлюча.шаг, 3, 'The brain step must be passable without a key');
  assert.ok(безКлюча.отправлено.some(тело => тело.provider === 'deepseek'
    && тело.model === 'deepseek-flash'));
  /* Шаг 3: характер, выбор меняет отправку. */
  const характер = await evaluate(`(async () => {
    for (let i = 0; i < 40; i++) {
      if (мастерШаги[3] && мастерШаги[3].карточки.children.length) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    const карточки = [...document.querySelectorAll('#мастер-тело .характер-карточка')];
    const отмечена = карточки.find(к => к.classList.contains('активный'));
    // Сохранённый характер отмечен, но выбираем другой: только тогда
    // мастер что-то отправляет (иначе у человека свой текст).
    const другая = карточки.find(к => к !== отмечена);
    другая.click();
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 40; i++) {
      if (мастерШаг === 4) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return { шаг: мастерШаг, было: отмечена.dataset.характер,
      выбрана: другая.dataset.характер, отправлено: window.__отправлено.slice() };
  })()`);
  assert.equal(характер.шаг, 4);
  assert.equal(характер.было, 'friendly', 'The saved preset must be preselected');
  assert.equal(характер.выбрана, 'calm');
  assert.ok(характер.отправлено.some(тело => тело.persona_preset === 'calm'
    && тело.persona === 'Да.'), 'A changed persona must be sent');
  // Не менял выбор — ничего не отправляем: у человека может быть свой текст.
  assert.ok(!характер.отправлено.some(тело => тело.persona_preset === 'friendly'),
    'An unchanged persona must not be sent');

  /* Шаг 4: микрофон и звук, голос выключен → «Записать 3 секунды» и «Прослушать». */
  const звук = await evaluate(`(async () => {
    for (let i = 0; i < 40; i++) {
      if (мастерШаги[4] && мастерШаги[4].звукСтатус.textContent) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    const эл = мастерШаги[4];
    const списки = [...эл.mic_name.options].map(пункт => пункт.textContent);
    эл.mic_name.value = 'Микрофон (USB)';
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 40; i++) {
      if (мастерШаг === 5) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return { шаг: мастерШаг, списки, перезапуск: эл.перезапустить.hidden,
      отправлено: window.__отправлено.slice() };
  })()`);
  assert.equal(звук.шаг, 5);
  assert.ok(звук.списки.some(текст => текст.includes('Микрофон (USB)')));
  assert.equal(звук.перезапуск, true, 'Restart is hidden while the voice is off');
  assert.ok(звук.отправлено.some(тело => тело.mic_name === 'Микрофон (USB)'));

  /* Шаг 5 и 6: телефон, «Начать» — мастер закрывается. */
  const телефон = await evaluate(`(async () => {
    for (let i = 0; i < 40; i++) {
      if (мастерШаги[5] && мастерШаги[5].телАдрес.options.length) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    const адреса = [...мастерШаги[5].телАдрес.options].map(пункт => пункт.value);
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 40; i++) {
      if (мастерШаг === 6) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    document.getElementById('мастер-дальше').click();
    for (let i = 0; i < 40; i++) {
      if (!мастерОткрыт) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return { адреса, спрятан: document.getElementById('мастер').hidden,
      раздел: текущий,
      флаг: window.__отправлено.some(тело => тело.first_run_done === true),
      ошибки: window.__ошибки.slice() };
  })()`);
  assert.ok(телефон.адреса.length > 0, 'The phone address must come from the server');
  assert.equal(телефон.флаг, true, 'The last step must mark the first run as done');
  assert.equal(телефон.спрятан, true, 'The wizard must close after «Начать»');
  assert.equal(телефон.раздел, 'панель');
  assert.deepEqual(телефон.ошибки, []);
  /* Повторный вход из «О программе» — мастер открывается заново. */
  const заново = await evaluate(`(async () => {
    открыть('программа');
    for (let i = 0; i < 40; i++) {
      if (document.querySelector('.обн-абзац')) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    const страница = {
      абзацев: document.querySelectorAll('.обн-абзац').length,
      кнопкаМастера: [...document.querySelectorAll('button')]
        .some(кнопка => кнопка.textContent === 'Пройти первую настройку заново'),
    };
    [...document.querySelectorAll('button')]
      .find(кнопка => кнопка.textContent === 'Пройти первую настройку заново').click();
    await new Promise(готово => setTimeout(готово, 400));
    return Object.assign({}, страница, { открыт: мастерОткрыт, шаг: мастерШаг,
      ошибки: window.__ошибки.slice() });
  })()`);
  assert.ok(заново.абзацев >= 4, 'The about page must show the program text');
  assert.equal(заново.кнопкаМастера, true);
  assert.equal(заново.открыт, true, 'The about page must reopen the wizard');
  assert.equal(заново.шаг, 1);
  assert.deepEqual(заново.ошибки, []);

  /* Пропустить настройку — confirm и запись флага. */
  const пропуск = await evaluate(`(async () => {
    let спросили = '';
    window.confirm = текст => { спросили = текст; return true; };
    document.getElementById('мастер-пропустить').click();
    for (let i = 0; i < 40; i++) {
      if (!мастерОткрыт) break;
      await new Promise(готово => setTimeout(готово, 150));
    }
    return { спросили, спрятан: document.getElementById('мастер').hidden,
      флаг: window.__отправлено.some(тело => тело.first_run_done === true),
      ошибки: window.__ошибки.slice() };
  })()`);
  assert.match(пропуск.спросили, /Пропустить первую настройку/);
  assert.equal(пропуск.спрятан, true);
  assert.equal(пропуск.флаг, true);
  assert.deepEqual(пропуск.ошибки, []);

  /* Окна 1000×700 и 1280×820: карточка и кнопки на месте. */
  for (const окно of [[1000, 700], [1280, 820]]) {
    await call('Emulation.setDeviceMetricsOverride',
      { width: окно[0], height: окно[1], deviceScaleFactor: 1, mobile: false });
    await evaluate('мастерОткрыть(2)');
    await new Promise(готово => setTimeout(готово, 400));
    const вид = await evaluate(`(() => {
      const карточка = document.querySelector('.мастер-карточка').getBoundingClientRect();
      const низ = document.querySelector('.мастер-низ').getBoundingClientRect();
      return {
        влезает: карточка.bottom <= window.innerHeight + 1
          && карточка.right <= window.innerWidth + 1,
        кнопкиВидны: низ.bottom <= window.innerHeight + 1 && низ.height > 0,
        ширина: Math.round(карточка.width),
      };
    })()`);
    assert.equal(вид.влезает, true, 'The card must fit ' + окно.join('x'));
    assert.equal(вид.кнопкиВидны, true, 'The buttons must be visible ' + окно.join('x'));
    assert.ok(вид.ширина <= 760, 'The card is wider than 760 px');
    await evaluate('мастерЗакрыть()');
  }
  await call('Emulation.clearDeviceMetricsOverride');
  process.stdout.write('Wizard OK: 6 steps; back; skip; about page; 1000x700; 1280x820; no JS errors.\n');
} finally {
  socket?.close();
  browser.kill();
  const safe = resolve(profile);
  if (safe.startsWith(resolve(tmpdir()) + '\\') && basename(safe).startsWith('truba-wizard-')) {
    try { rmSync(safe, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); }
    catch { /* Edge may still be releasing its profile. */ }
  }
}
