// Перенос настроек в пульте: галочки частей, «Сохранить в файл», «Показать в
// папке», импорт из файла (выбор, разбор, вопрос, применение) — и тот же
// блок на первом шаге мастера. Функции вырезаются из `pult.js` целиком и живут
// в подставном окружении: сервер, DOM, файловый выбор и `window.confirm` —
// подмены, поэтому ни диска, ни сети, ни настоящей Трубы тут не нужно.
// Запуск: `node tests/transfer_pult.mjs` (из unittest — test_transfer_pult.py).
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
   Ровно то, до чего дотягиваются вырезанные функции: узел с классом, текстом,
   атрибутами и слушателями. `событие` нужно, чтобы проверить выбор файла так,
   как это делает браузер. */
function узел(тег = 'div') {
  const дети = [];
  const классы = new Set();
  const слушатели = {};
  const node = {
    тег, style: {}, dataset: {}, атрибуты: {},
    hidden: false, disabled: false, checked: false, value: '', files: null,
    title: '', type: '', accept: '', isConnected: true, кликнут: false,
    дети, классы,
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
    addEventListener: (вид, что) => { (слушатели[вид] = слушатели[вид] || []).push(что); },
    click: () => {
      node.кликнут = true;
      for (const что of слушатели.click || []) что();
    },
    событие: вид => { for (const что of слушатели[вид] || []) что(); },
  };
  Object.defineProperty(node, 'className', {
    get: () => [...классы].join(' '),
    set: з => { классы.clear(); for (const к of String(з).split(/\s+/).filter(Boolean)) классы.add(к); },
  });
  Object.defineProperty(node, 'innerHTML', {
    get: () => '', set: () => { дети.length = 0; },
  });
  Object.defineProperty(node, 'textContent', {
    get: () => node._свойТекст || '',
    set: v => { node._свойТекст = String(v); дети.length = 0; },
  });
  node.текст = () => (node._свойТекст || '')
    + дети.map(с => (с.текст ? с.текст() : String(с.textContent || ''))).join('');
  return node;
}

/* Нарисованный узел по классу — галочка, кнопка, строка под ними. */
function найти(корень, класс) {
  for (const с of корень.дети) {
    if (!с.классы) continue;
    if (с.классы.has(класс)) return с;
    const вложенный = найти(с, класс);
    if (вложенный) return вложенный;
  }
  return null;
}

/* Кнопка с текстом: блок собирается из настоящих кнопок пульта, а класс у них
   один — искать надо по подписи. */
function найтиТекст(корень, текст) {
  for (const с of корень.дети) {
    if (с.textContent === текст) return с;
    const вложенный = найтиТекст(с, текст);
    if (вложенный) return вложенный;
  }
  return null;
}

/* Ответы сервера — те, что он и правда шлёт (`core/transfer.py`). */
const ЧАСТИ = [
  { id: 'settings', title: 'Настройки', files: 1, bytes: 120, size: '120 Б', ready: true },
  { id: 'keys', title: 'Ключи доступа к облаку', files: 1, bytes: 60, size: '60 Б', ready: true },
];
const В_ФАЙЛЕ = {
  ok: true, version: '1.2', created: '2026-10-02 11:30', files: 2, bytes: 180,
  token: 'токен-разбора',
  parts: [
    { id: 'settings', title: 'Настройки', files: 1, bytes: 120, size: '120 Б' },
    { id: 'memory', title: 'Память о тебе и история разговора', files: 1, bytes: 60, size: '60 Б' },
  ],
};

const S = {
  запросы: [],
  вопросы: [],
  подтверждать: true,
  ответы: {},
  /* Пока идёт запрос, кнопки уже неактивны — проверяем это прямо в подмене
     сервера, а не по коду. */
  наЗапросе: null,
  fetch: async (адрес, опции) => {
    S.запросы.push({ адрес, тело: опции?.body, метод: опции?.method || 'GET',
      заголовки: опции?.headers || {} });
    if (S.наЗапросе) S.наЗапросе(адрес);
    const свой = S.ответы[String(адрес)];
    if (свой) return свой;
    if (String(адрес) === '/api/transfer/parts') {
      return { ok: true, status: 200, json: async () => ({ ok: true, parts: ЧАСТИ }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  },
};

const ПОДСТАНОВКА = `
  const fetch = (адрес, опции) => S.fetch(адрес, опции);
  const document = {
    createElement: тег => узел(тег),
    createTextNode: текст => ({ текст: () => текст, textContent: текст }),
  };
  const window = { confirm: вопрос => { S.вопросы.push(вопрос); return S.подтверждать; } };
  const мастерШаги = {};
  let мастерШаг = 1;
  // Шаг «Привет» зовёт ещё и разговор о железе — тут сети нет, и для переноса
  // это неважно: подменяем пустотой, смотрим только на нарисованный блок.
  function мастерШагПриветЗагрузить() {}
` + ['переносБлок', 'переносСказать', 'переносЖдёт', 'переносОшибка', 'переносГалочки',
  'переносОтмеченные', 'переносСвод', 'переносЧастиЗагрузить', 'переносЭкспорт',
  'переносПоказать', 'переносИмпорт', 'переносПрименить', 'мастерЗаголовок',
  'мастерТекст', 'мастерШагПривет'].map(вырезать).join('\n')
  + '\nreturn { переносБлок, переносЧастиЗагрузить, переносГалочки, переносИмпорт,\n'
  + '  переносПрименить, переносЭкспорт, переносПоказать, мастерШагПривет };\n';

const api = new Function('S', 'узел', ПОДСТАНОВКА)(S, узел);

/* Дождаться цепочек промисов: запросы идут через await. */
const тик = () => new Promise(готово => setImmediate(готово));
async function переждать(раз = 6) {
  for (let шаг = 0; шаг < раз; шаг++) await тик();
}

function запросы(адрес) {
  return S.запросы.filter(з => з.адрес === адрес);
}

const файлПереноса = () => ({
  name: 'Труба-перенос-2026-10-02_1130.zip', type: 'application/zip',
});

/* Блок в настройках: с экспортом и кнопкой «Показать в папке». */
function блокЭкспорта() {
  const куда = узел('div');
  const эл = api.переносБлок(куда, 'Перенос настроек',
    'Сохрани настройки в файл — после переустановки Трубы перенеси их обратно, '
    + 'ничего не настраивая заново', true);
  return { куда, эл };
}

/* Блок в мастере: только перенос из файла. */
function блокИмпорта() {
  const куда = узел('div');
  const эл = api.переносБлок(куда, 'Перенос настроек',
    'Уже пользовался Трубой? Перенеси настройки из файла', false);
  return { куда, эл };
}

/* --- Блок: пояснение, кнопки, спрятанный выбор файла ------------------------ */
{
  S.запросы.length = 0;
  const { куда, эл } = блокЭкспорта();
  assert.match(куда.текст(), /Сохрани настройки в файл/,
    'хозяин должен понять, зачем это, одной строкой');
  assert.ok(найтиТекст(эл.блок, 'Сохранить в файл'), 'есть кнопка экспорта');
  assert.ok(найтиТекст(эл.блок, 'Показать в папке'), 'есть кнопка «Показать в папке»');
  assert.ok(найтиТекст(эл.блок, 'Перенести из файла…'), 'есть кнопка импорта');
  assert.equal(эл.файл.type, 'file', 'выбор файла родной, спрятан под кнопкой');
  assert.equal(эл.файл.accept, '.zip', 'браузер отбирает только наш ZIP');
  assert.ok(эл.файл.классы.has('образец-файл'), 'input спрятан, как у образца голоса');
  assert.equal(эл.показать.hidden, true, 'показывать нечего до сохранения');
  assert.equal(эл.применить.hidden, true, 'переносить нечего до разбора файла');
  assert.equal(эл.сохр.hidden, false);
  // Кнопка импорта только открывает выбор файла — сам файл уходит отдельно.
  эл.перенести.click();
  assert.equal(эл.файл.кликнут, true, 'кнопка открывает выбор файла');
  assert.equal(S.запросы.length, 0, 'пока файл не выбран, запросов нет');
}


/* --- Галочки частей: всё отмечено, кроме ключей облака ---------------------- */
{
  S.запросы.length = 0;
  const { эл } = блокЭкспорта();
  await api.переносЧастиЗагрузить(эл);
  assert.deepEqual(Object.keys(эл.галочки), ['settings', 'keys'],
    'галочки ровно по частям, что прислал сервер');
  assert.equal(эл.галочки.settings.checked, true);
  assert.equal(эл.галочки.keys.checked, false,
    'ключ облака без нужды в файл не кладём');
  assert.match(эл.блок.текст(), /Настройки/);
  assert.match(эл.блок.текст(), /120 Б/, 'размер части хозяину виден');
  assert.match(эл.блок.текст(),
    /в файле будет ключ доступа к облаку — никому его не отправляй/,
    'у ключей подпись-предупреждение, а не молчание');
  assert.equal(запросы('/api/transfer/parts').length, 1);
}


/* --- «Сохранить в файл»: уходят ровно отмеченные части ---------------------- */
{
  S.запросы.length = 0;
  S.ответы['/api/transfer/export'] = {
    ok: true, status: 200,
    json: async () => ({ ok: true,
      path: 'C:/Хозяин/Documents/Труба/Труба-перенос-2026-10-02_1130.zip',
      parts: ['settings'], bytes: 4096 }),
  };
  const { эл } = блокЭкспорта();
  await api.переносЧастиЗагрузить(эл);
  эл.галочки.keys.checked = true;
  S.запросы.length = 0;
  await api.переносЭкспорт(эл);
  const ушли = запросы('/api/transfer/export');
  assert.equal(ушли.length, 1);
  assert.equal(ушли[0].метод, 'POST');
  assert.deepEqual(JSON.parse(ушли[0].тело), { parts: ['settings', 'keys'] },
    'шлём то, что хозяин отметил, — включая ключи, если он их отметил');
  assert.match(эл.статус.textContent, /^Сохранено: .*Труба-перенос/,
    'пульт говорит, куда сохранил');
  assert.equal(эл.показать.hidden, false, 'после сохранения файл можно показать');
  assert.equal(эл.сохр.disabled, false, 'кнопка снова доступна');
}


/* --- «Показать в папке»: путь из ответа, а не вписанный руками -------------- */
{
  S.запросы.length = 0;
  S.ответы['/api/transfer/reveal'] = {
    ok: true, status: 200,
    json: async () => ({ ok: true,
      path: 'C:/Хозяин/Documents/Труба/Труба-перенос-2026-10-02_1130.zip' }),
  };
  const { эл } = блокЭкспорта();
  await api.переносЧастиЗагрузить(эл);
  await api.переносЭкспорт(эл);
  S.запросы.length = 0;
  эл.показать.click();
  await переждать();
  const показали = запросы('/api/transfer/reveal');
  assert.equal(показали.length, 1);
  assert.deepEqual(JSON.parse(показали[0].тело), { path: эл.путь },
    'показываем ровно тот файл, который сохранили');
  assert.match(эл.статус.textContent, /Показал в папке/);
}

/* --- Импорт: файл уходит в разбор телом, части рисуются -------------------- */
{
  S.запросы.length = 0;
  S.вопросы.length = 0;
  S.ответы['/api/transfer/inspect'] = { ok: true, status: 200, json: async () => В_ФАЙЛЕ };
  const { эл } = блокИмпорта();
  assert.equal(эл.сохр, null, 'в мастере сохранять нечего — только перенос');
  const файл = файлПереноса();
  эл.файл.files = [файл];
  // Пока идёт запрос, кнопки неактивны.
  S.наЗапросе = () => {
    assert.equal(эл.перенести.disabled, true, 'идёт разбор — кнопка неактивна');
  };
  await api.переносИмпорт(эл);
  S.наЗапросе = null;
  const разборы = запросы('/api/transfer/inspect');
  assert.equal(разборы.length, 1);
  assert.equal(разборы[0].метод, 'POST');
  assert.equal(разборы[0].тело, файл, 'тело запроса — сам ZIP, а не JSON');
  assert.equal(разборы[0].заголовки['Content-Type'], 'application/zip');
  assert.deepEqual(Object.keys(эл.галочки), ['settings', 'memory'],
    'рисуем ровно то, что в файле');
  assert.equal(эл.галочки.settings.checked, true);
  assert.equal(эл.галочки.memory.checked, true, 'всё, что в файле, отмечено');
  assert.match(эл.свод.textContent, /в файле частей: 2/);
  assert.match(эл.свод.textContent, /Труба 1\.2/, 'версия файла видна');
  assert.match(эл.свод.textContent, /файл от 2026-10-02/, 'дата файла видна');
  assert.match(эл.блок.текст(), /60 Б/, 'размер части из файла виден');
  assert.equal(эл.применить.hidden, false, 'после разбора переносить можно');
  assert.equal(эл.перенести.disabled, false, 'вернулись — кнопка снова доступна');
  assert.equal(S.вопросы.length, 0, 'разбор сам по себе ничего не спрашивает');
  assert.equal(запросы('/api/transfer/apply').length, 0, 'и не применяет');
  assert.equal(эл.токен, 'токен-разбора');
}


/* --- Отказ в confirm: переноса нет, всё остальное на месте ------------------ */
{
  S.запросы.length = 0;
  S.вопросы.length = 0;
  S.подтверждать = false;
  S.ответы['/api/transfer/apply'] = {
    ok: true, status: 200,
    json: async () => ({ ok: true, parts: ['settings', 'memory'], bytes: 180 }),
  };
  const { эл } = блокИмпорта();
  эл.файл.files = [файлПереноса()];
  await api.переносИмпорт(эл);
  await api.переносПрименить(эл);
  assert.equal(S.вопросы.length, 1, 'замену настроек спрашиваем');
  assert.match(S.вопросы[0], /Текущие настройки будут заменены/);
  assert.match(S.вопросы[0], /Пульт перезапустится/);
  assert.equal(запросы('/api/transfer/apply').length, 0,
    'хозяин отказался — пульт ничего не отправляет');
  assert.equal(эл.применить.disabled, false, 'кнопка вернётся для повтора');
  S.подтверждать = true;
}


/* --- Согласие: применяем ровно отмеченное, с токеном разбора ---------------- */
{
  S.запросы.length = 0;
  S.вопросы.length = 0;
  S.ответы['/api/transfer/apply'] = {
    ok: true, status: 200,
    json: async () => ({ ok: true, parts: ['settings'], bytes: 120 }),
  };
  const { эл } = блокИмпорта();
  эл.файл.files = [файлПереноса()];
  await api.переносИмпорт(эл);
  эл.галочки.memory.checked = false;
  S.запросы.length = 0;
  await api.переносПрименить(эл);
  const применено = запросы('/api/transfer/apply');
  assert.equal(применено.length, 1);
  assert.equal(применено[0].метод, 'POST');
  assert.deepEqual(JSON.parse(применено[0].тело),
    { token: 'токен-разбора', parts: ['settings'] },
    'применяем тот файл, что разобрали, и те части, что хозяин оставил');
  assert.match(эл.статус.textContent, /Переношу… пульт перезапустится/);
  assert.ok(!эл.статус.классы.has('плохо'), 'успех — не ошибка');
  delete S.ответы['/api/transfer/apply'];
}

/* --- Ошибка сервера видна словами, кнопки снова доступны --------------------- */
{
  S.запросы.length = 0;
  S.ответы['/api/transfer/inspect'] = {
    ok: false, status: 400,
    json: async () => ({ ok: false,
      error: 'это не файл переноса Трубы: нужен наш ZIP с transfer.json' }),
  };
  const { эл } = блокИмпорта();
  эл.файл.files = [файлПереноса()];
  await api.переносИмпорт(эл);
  assert.match(эл.статус.textContent, /нужен наш ZIP с transfer\.json/,
    'причина отказа сервера показывается хозяину, а не «что-то не так»');
  assert.ok(эл.статус.классы.has('плохо'), 'ошибка — красным');
  assert.equal(эл.перенести.disabled, false, 'кнопка снова доступна');
  assert.equal(эл.применить.hidden, true, 'переносить нечего — файла не разобрали');

  // Тот же путь с экспортом: сервер отказал — пульт говорит почему.
  S.ответы['/api/transfer/export'] = {
    ok: false, status: 400,
    json: async () => ({ ok: false, error: 'не выбрано, что переносить' }),
  };
  const второй = блокЭкспорта().эл;
  await api.переносЧастиЗагрузить(второй);
  await api.переносЭкспорт(второй);
  assert.match(второй.статус.textContent, /не выбрано, что переносить/);
  assert.ok(второй.статус.классы.has('плохо'));
  assert.equal(второй.сохр.disabled, false, 'и снова можно повторить');
  delete S.ответы['/api/transfer/export'];
  delete S.ответы['/api/transfer/inspect'];
}


/* --- Мастер, шаг 1: та же кнопка и та же функция --------------------------- */
{
  S.запросы.length = 0;
  S.вопросы.length = 0;
  S.ответы['/api/transfer/inspect'] = { ok: true, status: 200, json: async () => В_ФАЙЛЕ };
  const тело = узел('div');
  api.мастерШагПривет(тело);
  const блокПереноса = найти(тело, 'перенос-блок');
  assert.ok(блокПереноса, 'на первом шаге мастера есть блок переноса');
  assert.match(блокПереноса.текст(),
    /Уже пользовался Трубой\? Перенеси настройки из файла/);
  assert.ok(найтиТекст(блокПереноса, 'Перенести из файла…'),
    'та же кнопка импорта, что в настройках');
  assert.ok(!найтиТекст(блокПереноса, 'Сохранить в файл'),
    'в мастере экспорта нет — настраивать тут нечего');
  // Выбор файла из мастера идёт той же дорогой: тот же input и тот же разбор.
  const файлМастера = файлПереноса();
  const input = найти(блокПереноса, 'образец-файл');
  input.files = [файлМастера];
  input.событие('change');
  await переждать();
  const разборы = запросы('/api/transfer/inspect');
  assert.equal(разборы.length, 1, 'мастер зовёт ту же функцию разбора файла');
  assert.equal(разборы[0].тело, файлМастера);
  assert.ok(найтиТекст(блокПереноса, 'Перенести'),
    'и та же кнопка применения после разбора');
  delete S.ответы['/api/transfer/inspect'];
}


process.stdout.write('Transfer in the pult OK: parts are checked except the cloud key, '
  + 'export sends what was ticked, reveal uses the saved path, import reads the zip '
  + 'body, asks before replacing and applies only the chosen parts with its token, '
  + 'and the first wizard step uses the same block.\n');
