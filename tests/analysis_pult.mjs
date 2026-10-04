// Выключатель «Спрашивать, добавить ли разбор в заметки» на странице
// «Заметки»: состояние приходит из `/api/settings`, смена уходит туда же
// булевым, а отказ сервера возвращает галочку и говорит «Не сохранила».
// Функции вырезаются из `pult.js` целиком и живут в подставном окружении:
// сервер и DOM — подмены, поэтому ни пульта, ни сети, ни диска тут не нужно.
// Запуск: `node tests/analysis_pult.mjs` (из unittest — test_analysis_notes.py).
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const pult = (await readFile(new URL('../ui/web/pult.js', import.meta.url), 'utf8'))
  .replace(/\r\n/g, '\n');

/* Функция из pult.js целиком — от `function имя(` до строки `}` в начале
   строки. Именно так написан весь проект, иначе проверка проверяла бы копию. */
function вырезать(имя) {
  const начало = pult.indexOf('function ' + имя + '(');
  assert.notEqual(начало, -1, 'в pult.js нет функции ' + имя);
  const конец = pult.indexOf('\n}\n', начало);
  assert.notEqual(конец, -1, 'не нашёлся конец функции ' + имя);
  const началоСлова = pult.lastIndexOf('\n', начало) + 1;
  const началоAsync = pult.startsWith('async function ', началоСлова) ? началоСлова : начало;
  return pult.slice(началоAsync, конец + 3);
}

/* --- Подмена DOM -----------------------------------------------------------
   Ровно то, до чего дотягиваются вырезанные функции: узел с `checked`,
   `disabled`, текстом и классами. `статус` — строка под блоком, куда говорит
   `заметкиСказать`. */
function узел(тег = 'div') {
  const дети = [];
  const классы = new Set();
  const node = {
    тег, style: {}, dataset: {}, атрибуты: {},
    hidden: false, disabled: false, checked: false, value: '',
    title: '', type: '', isConnected: true, дети, классы,
    classList: {
      add: и => классы.add(и),
      remove: и => классы.delete(и),
      toggle: (и, есть) => (есть ? классы.add(и) : классы.delete(и)),
      contains: и => классы.has(и),
    },
    appendChild: x => { дети.push(x); return x; },
    append: (...xs) => { for (const x of xs) дети.push(x); },
    setAttribute: (к, з) => { node.атрибуты[к] = String(з); },
    getAttribute: к => (к in node.атрибуты ? node.атрибуты[к] : null),
    addEventListener: () => {},
  };
  Object.defineProperty(node, 'className', {
    get: () => [...классы].join(' '),
    set: з => { классы.clear(); for (const к of String(з).split(/\s+/).filter(Boolean)) классы.add(к); },
  });
  Object.defineProperty(node, 'textContent', {
    get: () => node._свойТекст || '',
    set: v => { node._свойТекст = String(v); дети.length = 0; },
  });
  return node;
}

const S = {
  запросы: [],
  /* Что сервер отдаст на `GET /api/settings`. Мусор в значении — тоже
     проверка: пульт не должен ставить галочку по не-булеву. */
  настройки: { offer_analysis_note: true },
  /* Ответ на POST: null — обычный успех, иначе подмена целиком. */
  ответПоста: null,
  fetch: async (адрес, опции) => {
    S.запросы.push({ адрес, тело: опции?.body, метод: опции?.method || 'GET' });
    if (опции?.method === 'POST') {
      return S.ответПоста || { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    return { ok: true, status: 200,
      json: async () => ({ ok: true, settings: S.настройки }) };
  },
};

const ПОДСТАНОВКА = `
  const fetch = (адрес, опции) => S.fetch(адрес, опции);
  // ` + '`заметкиЭлементы`' + ` в pult.js — переменная уровня страницы, и обе
  // функции проверяют, что работают с текущей страницей. Подменяем так же.
  let заметкиЭлементы = null;
  function задатьЗаметки(эл) { заметкиЭлементы = эл; }
`
  + ['заметкиСказать', 'заметкиЗагрузитьРазбор', 'заметкиСпрашиватьРазбор']
    .map(вырезать).join('\n')
  + '\nreturn { задатьЗаметки, заметкиЗагрузитьРазбор, заметкиСпрашиватьРазбор };\n';

const api = new Function('S', 'узел', ПОДСТАНОВКА)(S, узел);

/* Дождаться цепочек промисов: запросы идут через await. */
const тик = () => new Promise(готово => setImmediate(готово));
async function переждать(раз = 4) {
  for (let шаг = 0; шаг < раз; шаг++) await тик();
}

function запросы(адрес) {
  return S.запросы.filter(з => з.адрес === адрес);
}

/* Страница заметок: ровно те элементы, до которых дотягиваются обе функции —
   выключатель `спроситьРазбор` и строка состояния `статус`. */
function страница() {
  const эл = {
    спроситьРазбор: узел('input'),
    статус: узел('span'),
    сменитьПапку: узел('button'),
    поУмолчанию: узел('button'),
  };
  api.задатьЗаметки(эл);
  return эл;
}


/* --- Загрузка: галочка по настройке, а не по умолчанию --------------------- */
{
  S.запросы.length = 0;
  S.настройки = { offer_analysis_note: false };
  const эл = страница();
  await api.заметкиЗагрузитьРазбор(эл);
  assert.equal(эл.спроситьРазбор.checked, false,
    'выключенная настройка должна снять галочку, а не поставить её');
  assert.equal(запросы('/api/settings').length, 1);
  assert.equal(запросы('/api/settings')[0].метод, 'GET', 'состояние спрашиваем, не шлём');

  S.запросы.length = 0;
  S.настройки = { offer_analysis_note: true };
  const включено = страница();
  await api.заметкиЗагрузитьРазбор(включено);
  assert.equal(включено.спроситьРазбор.checked, true, 'включённая настройка — галочка на месте');

  // Ключа нет вовсе (правка руками) — по умолчанию спрашиваем: молча забывать
  // о разборах хозяин не просил.
  S.настройки = {};
  const без_ключа = страница();
  await api.заметкиЗагрузитьРазбор(без_ключа);
  assert.equal(без_ключа.спроситьРазбор.checked, true,
    'без ключа спрашиваем по умолчанию');

  // Мусор вместо булева — тоже «спрашиваем»: значение из будущей версии
  // не должно молча выключать вопрос.
  S.настройки = { offer_analysis_note: 'нет' };
  const мусор = страница();
  await api.заметкиЗагрузитьРазбор(мусор);
  assert.equal(мусор.спроситьРазбор.checked, true, 'мусор в настройке — галочка на месте');
}


/* --- Смена: уходит булевым, страница говорит словами ------------------------ */
{
  S.запросы.length = 0;
  S.ответПоста = null;
  const эл = страница();
  эл.спроситьРазбор.checked = false;
  await api.заметкиСпрашиватьРазбор(эл);
  const пост = запросы('/api/settings').filter(з => з.метод === 'POST');
  assert.equal(пост.length, 1, 'смена уходит на сервер сразу, как папка заметок');
  assert.deepEqual(JSON.parse(пост[0].тело), { offer_analysis_note: false },
    'на сервер уходит булево, а не строка');
  assert.match(эл.статус.textContent, /больше не спрашиваю/);
  assert.ok(!эл.статус.классы.has('плохо'), 'успех — не ошибка');
  assert.equal(эл.спроситьРазбор.disabled, false, 'галочка снова доступна');

  S.запросы.length = 0;
  const обратно = страница();
  обратно.спроситьРазбор.checked = true;
  await api.заметкиСпрашиватьРазбор(обратно);
  assert.deepEqual(
    JSON.parse(запросы('/api/settings').filter(з => з.метод === 'POST')[0].тело),
    { offer_analysis_note: true });
  assert.match(обратно.статус.textContent, /спрошу/);
}


/* --- Отказ сервера: галочка возвращается, а хозяин видит почему ------------- */
{
  S.запросы.length = 0;
  S.ответПоста = { ok: false, status: 400,
    json: async () => ({ ok: false, errors: ['offer_analysis_note: нужно true/false'] }) };
  const эл = страница();
  эл.спроситьРазбор.checked = false;
  await api.заметкиСпрашиватьРазбор(эл);
  assert.equal(эл.спроситьРазбор.checked, true,
    'не сохранилось — галочка возвращается туда, где она на сервере');
  assert.match(эл.статус.textContent, /^Не сохранила: /, 'хозяин видит «Не сохранила»');
  assert.match(эл.статус.textContent, /нужно true\/false/,
    'и причину отказа, а не «сервер ответил 400»');
  assert.ok(эл.статус.классы.has('плохо'), 'отказ — красным');
  assert.equal(эл.спроситьРазбор.disabled, false, 'и снова можно повторить');

  // Страница сменилась, пока летел запрос: её трогать нельзя.
  S.ответПоста = null;
  const старая = страница();
  const новая = страница();
  старая.спроситьРазбор.checked = false;
  api.заметкиСпрашиватьРазбор(старая);
  await переждать();
  assert.equal(новая.спроситьРазбор.checked, false,
    'уехавший запрос не должен трогать уже другую страницу заметок');
}


process.stdout.write('Analysis note switch OK: the checkbox follows offer_analysis_note '
  + '(junk and a missing key still ask), a change is posted as a boolean at once, '
  + 'and a refused save puts the checkbox back and says "Не сохранила".\n');
