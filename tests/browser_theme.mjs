// Проверка светлой темы на копии пульта и на странице телефона (порт 8775).
// Голоса нет, настройки не пишутся: пульт отдаёт подмену в памяти.
// Плитки, кнопки действий и подменю не нажимаем — это живой компьютер.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, existsSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';

const edge = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const отладка = Number(process.env.THEME_DEBUG || 9234);
const base = 'http://127.0.0.1:8775';
const кадры = process.env.THEME_SHOTS
  || 'C:\\Users\\user\\AppData\\Local\\Temp\\truba-theme-shots';
if (!existsSync(кадры)) mkdirSync(кадры, { recursive: true });

function прибрать(профиль) {
  /* Edge отпускает папку профиля не сразу после kill(), и попытка снести её
     сразу падает с EPERM. Это мусор проверки, а не повод её ругать. */
  try { rmSync(профиль, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
  catch { /* профиль переживёт проверку и уйдёт с временной папкой */ }
}

function новыйБраузер(окно, стартовая) {
  const профиль = mkdtempSync(join(tmpdir(), 'truba-theme-'));
  const процесс = spawn(edge, [
    '--headless=new', '--no-first-run', '--no-default-browser-check',
    `--window-size=${окно}`,
    '--remote-allow-origins=*', `--remote-debugging-port=${отладка}`,
    `--user-data-dir=${профиль}`, стартовая,
  ], { windowsHide: true, stdio: 'ignore' });
  return { процесс, профиль };
}

async function ждатьСтраницу(кусок) {
  for (let i = 0; i < 60; i++) {
    try {
      const страницы = await (await fetch(`http://127.0.0.1:${отладка}/json/list`)).json();
      const найдена = страницы.find(э => э.type === 'page' && э.url.includes(кусок));
      if (найдена) return найдена;
    } catch { /* Браузер ещё запускается. */ }
    await new Promise(готово => setTimeout(готово, 250));
  }
  throw Error(`Edge did not open ${кусок}`);
}

async function открытьСокет(страница) {
  const сокет = new WebSocket(страница.webSocketDebuggerUrl);
  await new Promise((done, fail) => {
    сокет.addEventListener('open', done, { once: true });
    сокет.addEventListener('error', fail, { once: true });
  });
  let порядок = 0;
  const ждущие = new Map();
  сокет.addEventListener('message', событие => {
    const ответ = JSON.parse(событие.data);
    if (ответ.id && ждущие.has(ответ.id)) {
      const пара = ждущие.get(ответ.id);
      ждущие.delete(ответ.id);
      ответ.error ? пара.fail(Error(ответ.error.message)) : пара.done(ответ.result);
    }
  });
  const call = (method, params = {}) => new Promise((done, fail) => {
    const id = ++порядок;
    ждущие.set(id, { done, fail });
    сокет.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async выражение => {
    const итог = await call('Runtime.evaluate', {
      expression: выражение, returnByValue: true, awaitPromise: true,
    });
    if (итог.exceptionDetails) throw Error(итог.exceptionDetails.text);
    return итог.result.value;
  };
  return { сокет, call, evaluate };
}

/* Ловля ошибок: страница с ошибками в консоли проверку не прошла. Ставится
   до загрузки — `Page.addScriptToEvaluateOnNewDocument`. */
const ЛОВИТЕЛЬ = `(() => {
  window.__ошибки = [];
  window.addEventListener('error', событие => {
    window.__ошибки.push('error: ' + (событие.message || событие.type));
  });
  window.addEventListener('unhandledrejection', событие => {
    window.__ошибки.push('unhandledrejection: ' + событие.reason);
  });
  return true;
})()`;

const МЕНЮ = ['панель', 'чат', 'заметки', 'голос', 'логи', 'настройки',
  'проверка', 'программы', 'команды', 'программа'];
const ПОДМЕНЮ_ГОЛОСА = ['голос', 'слух', 'распознавание', 'звук'];
const ПОДМЕНЮ_НАСТРОЕК = ['ответы', 'поиск', 'первой', 'характер', 'память',
  'телефон', 'система'];

const всё = [];
function галочка(условие, текст) {
  всё.push((условие ? 'да  ' : 'НЕТ ') + текст);
  assert.ok(условие, текст);
}

async function снимок(связь, имя) {
  const путь = join(кадры, `${имя}.png`);
  const { data } = await связь.call('Page.captureScreenshot', { format: 'png' });
  await writeFile(путь, Buffer.from(data, 'base64'));
  return путь;
}

const ждать = мс => new Promise(готово => setTimeout(готово, мс));

async function пройтиПульт(тема) {
  const { процесс, профиль } = новыйБраузер('1280,820', `${base}/pult`);
  const страница = await ждатьСтраницу('/pult');
  const связь = await открытьСокет(страница);
  try {
    await связь.call('Page.enable');
    await связь.call('Runtime.enable');
    await связь.call('Page.addScriptToEvaluateOnNewDocument', { source: ЛОВИТЕЛЬ });
    await связь.call('Page.navigate', { url: `${base}/pult` });
    await ждать(1500);

    const задано = await связь.evaluate(
      'document.documentElement.dataset.theme || ""');
    галочка(задано === тема, `тема в разметке: ${задано} (ждали ${тема})`);

    /* Все вкладки меню по очереди: нажимаем только пункты меню, плитки и
       кнопки действий не трогаем — это живой компьютер. */
    for (const раздел of МЕНЮ) {
      await связь.evaluate(
        `document.querySelector('[data-раздел="${раздел}"]').click()`);
      await ждать(700);
      const заголовок = await связь.evaluate(
        'document.getElementById("заголовок").textContent');
      галочка(!!заголовок, `вкладка «${раздел}» открылась: ${заголовок}`);
    }

    /* Подразделы «Голоса» и «Настроек» — те же вкладки, только глубже. */
    for (const под of [...ПОДМЕНЮ_ГОЛОСА, ...ПОДМЕНЮ_НАСТРОЕК]) {
      await связь.evaluate(
        `document.querySelector('[data-голос="${под}"], [data-настройка="${под}"]').click()`);
      await ждать(700);
      const видно = await связь.evaluate(
        `!!document.querySelector('.настр-секция:not([hidden])')`);
      галочка(видно, `подраздел «${под}» показан`);
    }

    /* Поле «Тема» в «Системе»: предпросмотр без «Сохранить» и возврат к
       сохранённой при уходе из настроек. */
    await связь.evaluate(
      'document.querySelector(\'[data-настройка="система"]\').click()');
    await ждать(900);
    const поле = await связь.evaluate(`(() => {
      const el = настрЭлементы && настрЭлементы.theme;
      return el ? el.value : '';
    })()`);
    галочка(поле === тема, `поле «Тема» показывает сохранённую: ${поле}`);

    const другая = тема === 'light' ? 'dark' : 'light';
    await связь.evaluate(`(() => {
      const el = настрЭлементы.theme;
      el.value = '${другая}';
      el.dispatchEvent(new Event('change'));
      return true;
    })()`);
    await ждать(400);
    const после = await связь.evaluate(
      'document.documentElement.dataset.theme');
    галочка(после === другая, `предпросмотр без «Сохранить»: ${после}`);

    /* Ушёл без сохранения — при следующей загрузке настроек должна вернуться
       сохранённая. */
    await связь.evaluate('открыть("панель")');
    await ждать(500);
    await связь.evaluate('открыть("настройки")');
    await ждать(1200);
    const вернулась = await связь.evaluate(
      'document.documentElement.dataset.theme');
    галочка(вернулась === тема, `вернулась сохранённая: ${вернулась}`);

    /* Снимки нужных страниц. */
    const кадрыПульта = {};
    for (const [раздел, имя] of [['панель', 'панель'], ['программы', 'программы'],
      ['заметки', 'заметки']]) {
      await связь.evaluate(`открыть("${раздел}")`);
      await ждать(900);
      кадрыПульта[имя] = await снимок(связь, `пульт-${тема}-${имя}`);
    }
    await связь.evaluate('открытьГолосПодраздел("голос")');
    await ждать(1200);
    кадрыПульта.озвучивание = await снимок(связь, `пульт-${тема}-озвучивание`);

    /* Мастер из «О программе»: снимаем шаги 1, 2 и 5 — те, где есть и
       железо, и ключ, и запись. Кнопка «Пройти первую настройку заново»
       должна открывать его и в светлой теме. */
    await связь.evaluate('открыть("программа")');
    await ждать(900);
    const открылся = await связь.evaluate(`(() => {
      const кнопка = [...document.querySelectorAll('button')]
        .find(к => к.textContent === 'Пройти первую настройку заново');
      if (!кнопка) return false;
      кнопка.click();
      return true;
    })()`);
    галочка(открылся, 'кнопка «Пройти первую настройку заново» нашлась');
    await ждать(900);
    галочка(await связь.evaluate('мастерОткрыт && мастерШаг === 1'),
      'мастер открылся с первого шага');
    for (const шаг of [1, 2, 5]) {
      await связь.evaluate(`мастерОткрыть(${шаг})`);
      await ждать(1200);
      const надпись = await связь.evaluate(
        'document.getElementById("мастер-шаг").textContent');
      галочка(надпись.includes(`Шаг ${шаг}`), `мастер: ${надпись}`);
      кадрыПульта[`мастер${шаг}`] =
        await снимок(связь, `пульт-${тема}-мастер-${шаг}`);
    }
    await связь.evaluate('мастерЗакрыть()');
    await ждать(400);

    const ошибки = await связь.evaluate('window.__ошибки');
    галочка(Array.isArray(ошибки) && ошибки.length === 0,
      `ошибок в консоли нет: ${JSON.stringify(ошибки)}`);
    return кадрыПульта;
  } finally {
    связь.сокет.close();
    процесс.kill();
    прибрать(профиль);
  }
}

async function пройтиТелефон(тема) {
  const { процесс, профиль } = новыйБраузер('851,393', `${base}/`);
  const страница = await ждатьСтраницу('8775/');
  const связь = await открытьСокет(страница);
  try {
    await связь.call('Page.enable');
    await связь.call('Runtime.enable');
    await связь.call('Emulation.setDeviceMetricsOverride', {
      width: 851, height: 393, deviceScaleFactor: 1, mobile: false,
    });
    await связь.call('Page.addScriptToEvaluateOnNewDocument', { source: ЛОВИТЕЛЬ });
    await связь.call('Page.navigate', { url: `${base}/` });
    await ждать(2000);

    const задано = await связь.evaluate(
      'document.documentElement.dataset.theme || ""');
    галочка(задано === тема, `телефон: тема в разметке ${задано}`);

    const главный = await снимок(связь, `телефон-${тема}-главный`);

    /* Заметки открываем из JS, а не касанием: телефон лежит на компе, и
       любое касание — это ввод. `openNotes` поднимает слой и просит список,
       а данные подкладываем сами — ответ сервера в проверке заглушен. */
    await связь.evaluate(`(() => {
      openNotes();
      drawNotes([
        { name: 'Дом', topics: [
          { name: 'Полка', entries: 3, updated: '2026-09-28' },
          { name: 'Кухня', entries: 1, updated: '2026-09-20' }] },
        { name: 'Работа', topics: [
          { name: 'Отчёт', entries: 7, updated: '2026-09-27' }] }]);
      return true;
    })()`);
    await ждать(600);
    const пусто = await снимок(связь, `телефон-${тема}-заметки-список`);
    await связь.evaluate(`(() => {
      notePicked = { section: 'Дом', topic: 'Полка' };
      drawNoteTopic({ section: 'Дом', topic: 'Полка', entries: [
        { when: '28 сентября', heading: 'Продал полку',
          text: 'Продал полку в прихожей. Дуб, три полки, крепление снял.' }] });
      return true;
    })()`);
    await ждать(600);
    const темаЗаметки = await снимок(связь, `телефон-${тема}-заметки`);

    /* Кнопок темы на телефоне быть не должно — это просил хозяин. */
    const кнопок = await связь.evaluate(
      `Array.from(document.querySelectorAll('button,[role=button]'))
        .filter(э => /тем/i.test(э.textContent || '')).length`);
    галочка(кнопок === 0, `кнопок темы на телефоне нет: ${кнопок}`);

    const ошибки = await связь.evaluate('window.__ошибки');
    галочка(Array.isArray(ошибки) && ошибки.length === 0,
      `телефон: ошибок нет: ${JSON.stringify(ошибки)}`);
    return { главный, пусто, темаЗаметки };
  } finally {
    связь.сокет.close();
    процесс.kill();
    прибрать(профиль);
  }
}

try {
  for (const тема of (process.env.THEME_ORDER || 'dark,light').split(',')) {
    const пультКадры = await пройтиПульт(тема);
    const телефонКадры = await пройтиТелефон(тема);
    for (const [имя, путь] of Object.entries({ ...пультКадры, ...телефонКадры })) {
      console.log(`${тема}/${имя}: ${путь}`);
    }
  }
  console.log(всё.join('\n'));
  console.log(`\nВсего проверок: ${всё.length}, все зелёные.`);
} catch (ошибка) {
  console.log(всё.join('\n'));
  console.error('\nПРОВАЛ:', ошибка.message);
  process.exitCode = 1;
}
