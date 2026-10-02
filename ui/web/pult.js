/* Пульт «Трубы». Первый этап: меню, карточки состояния, Панель-компьютер. */

const РАЗДЕЛЫ = {
  панель: {
    имя: 'Панель',
    описание: 'Состояние сервисов и нагрузка компьютера',
  },
  чат: {
    имя: 'Чат',
    описание: 'Переписка с ней. Голосовые реплики сюда не сыплются',
    скоро: 'Поле ввода и ответы потоком. Память общая с голосом — она один человек, а не два. Позже сюда же лягут документы, файлы и поиск.',
  },
  заметки: {
    имя: 'Заметки',
    описание: 'Надиктованное голосом. Эти же файлы открываются в Obsidian',
    скоро: '',
  },
  голос: {
    имя: 'Голос',
    описание: 'Включение, озвучивание и слух. Что она слышала и отвечала — в Логах',
    скоро: 'Лента голосовых событий. Появится на втором этапе: услышал, ответил, пропустил и почему, перебили и с какой громкостью.',
  },
  логи: {
    имя: 'Логи',
    описание: 'Всё, что происходило: разговор, интернет, телефон, программы, ошибки',
    скоро: 'Таблица с поиском, фильтром по типу события и деталями справа. Третий этап.',
  },
  настройки: {
    имя: 'Настройки',
    описание: 'Выбери раздел и настрой работу Трубы',
    скоро: '',
  },
  проверка: {
    имя: 'Проверка',
    описание: 'Замеры микрофона, скоростей и порога перебивания',
    скоро: 'Переезжает из старого пульта целиком. Третий этап.',
  },
  программы: {
    имя: 'Программы',
    описание: 'Кнопки, которые видно на телефоне',
    скоро: '',
  },
  команды: {
    имя: 'Команды',
    описание: 'Как говорить с Трубой и что она умеет',
    скоро: '',
  },
  /* Свой пункт, а не раздел настроек: хозяин ходит сюда сам, когда что-то
     сломалось или вышла новая версия (28.09). */
  программа: {
    имя: 'О программе',
    описание: 'Что Труба делает, как себя ведёт и что уходит в интернет',
    скоро: '',
  },
  /* Не пункт меню: открывается сердечком внизу. */
  поддержка: {
    имя: 'Поддержать',
    описание: 'Автор Трубы и где его найти',
    скоро: '',
  },
};

const КАРТОЧКИ = ['слух', 'голос', 'мозг', 'телефон', 'память'];

const $ = (id) => document.getElementById(id);
const меню = $('меню');
const карточки = $('карточки');
const лист = $('лист');
const связь = $('связь');

/* ---------- Разделы ---------- */

/* Текущий раздел и последнее живое состояние: Панель рисуем из того же
   /state, что и верхние карточки, ничего нового серверу не просим. */
let текущий = 'панель';
let последние = null;

/* Панель: верхние карточки показывают сводку, а одна компактная строка
   управляет голосом. Остальные настройки здесь не дублируем. */

/* Число из /state: пропуски показываем прочерком, никакого NaN. */
function число(значение) {
  if (typeof значение !== 'number' || !Number.isFinite(значение)) return null;
  return значение;
}

function проценты(значение) {
  const n = число(значение);
  if (n === null) return '—';
  return Math.round(n) + '%';
}

function доля(значение) {
  const n = число(значение);
  if (n === null) return null;
  return Math.max(0, Math.min(100, n));
}

function гигабайты(занято, всего) {
  const з = число(занято);
  const в = число(всего);
  if (з === null || в === null) return '—';
  return з.toFixed(1) + ' из ' + в.toFixed(1) + ' ГБ';
}

function метрика(название, значение, подпись, заполнение, синяя = false) {
  const карта = document.createElement('div');
  карта.className = 'метрика' + (синяя ? ' синяя' : '');
  const имя = document.createElement('div');
  имя.className = 'метрика-имя';
  имя.textContent = название;
  const цифра = document.createElement('div');
  цифра.className = 'метрика-значение';
  цифра.textContent = значение;
  карта.appendChild(имя);
  карта.appendChild(цифра);
  if (подпись !== undefined && подпись !== null && подпись !== '') {
    const низ = document.createElement('div');
    низ.className = 'метрика-подпись';
    низ.textContent = подпись;
    карта.appendChild(низ);
  }
  const шкала = document.createElement('div');
  шкала.className = 'метрика-шкала';
  const полоса = document.createElement('i');
  const ширина = доля(заполнение);
  полоса.style.width = (ширина === null ? 0 : ширина) + '%';
  шкала.appendChild(полоса);
  карта.appendChild(шкала);
  return карта;
}

/* Мгновенный повтор NVIDIA: включён ли и следит ли за ним Труба
   (core/replay.py). NVIDIA сама выключает его, когда Codex управляет
   компьютером, — сторож включает обратно. */
function строкаПовтора() {
  const повтор = (последние && последние.повтор) || {};
  const строка = document.createElement('div');
  строка.className = 'повтор-строка';
  const точка = document.createElement('i');
  точка.className = 'повтор-точка' + (повтор.вкл === true ? ' вкл' : повтор.вкл === false ? ' выкл' : '');
  строка.appendChild(точка);
  const текст = document.createElement('span');
  текст.className = 'повтор-текст';
  текст.textContent = 'Мгновенный повтор NVIDIA: ' + (повтор.текст || 'не знаю');
  строка.appendChild(текст);
  const подпись = document.createElement('label');
  подпись.className = 'повтор-следить';
  подпись.title = 'Если NVIDIA выключит повтор сама — Труба включит обратно. Выключенный тобой не трогает';
  const галочка = document.createElement('input');
  галочка.type = 'checkbox';
  галочка.className = 'настр-галочка';
  галочка.checked = повтор.следить !== false;
  подпись.appendChild(галочка);
  подпись.appendChild(document.createTextNode('включать обратно сам'));
  строка.appendChild(подпись);
  галочка.addEventListener('change', async () => {
    const хочу = галочка.checked;
    if (последние && последние.повтор) последние.повтор.следить = хочу;
    try {
      const ответ = await fetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ replay_guard: хочу }),
      });
      const данные = await ответ.json().catch(() => ({}));
      if (!ответ.ok || !данные.ok) throw new Error(данные.error || (данные.errors || []).join('; ') || ('HTTP ' + ответ.status));
    } catch (e) {
      if (последние && последние.повтор) последние.повтор.следить = !хочу;
      галочка.checked = !хочу;
      текст.textContent = 'Не вышло: ' + e.message;
    }
  });
  return строка;
}

/* Как часто Труба заговаривает сама. Список один на форму настроек и на
   строку Панели: разъехавшиеся тексты хозяин сразу и не заметит, а потом
   не найдёт нужного. */
const ЧАСТОТЫ_ПЕРВОЙ = [
  ['never', 'Никогда'],
  ['rare', 'Редко — раз в час молчания'],
  ['sometimes', 'Иногда — раз в 25 минут'],
  ['often', 'Часто — раз в 10 минут'],
];
/* Текст частоты для Панели: из «Никогда» — «выключено», из «Иногда — раз в
   25 минут» — «иногда — раз в 25 минут». Пустая строка у «никогда». */
function частотаКоротко(код) {
  const пара = ЧАСТОТЫ_ПЕРВОЙ.find(([к]) => к === код);
  if (!пара || пара[0] === 'never') return '';
  return пара[1].charAt(0).toLowerCase() + пара[1].slice(1);
}

/* Поиск в интернете и «заговаривает сама» — двумя строками с галочками на
   Панели. Каждая шлёт настройку одним полем, как строка повтора NVIDIA, и
   при ошибке возвращает галочку на место.

   `вкл` и `выкл` — значения, которые кладём в срез на время, пока сервер
   ответит. Для поиска это true/false, для заходов — частота строкой: туда
   нельзя класть галочку, иначе подпись на мгновение останется пустой. */
function строкаВозможности(название, включено, текст, подписьГалочки, шлёт, раздел, вкл, выкл) {
  const строка = document.createElement('div');
  строка.className = 'повтор-строка возможности-строка';
  const точка = document.createElement('i');
  точка.className = 'повтор-точка' + (включено ? ' вкл' : '');
  строка.appendChild(точка);
  const надпись = document.createElement('span');
  надпись.className = 'повтор-текст';
  надпись.textContent = текст;
  строка.appendChild(надпись);
  const метка = document.createElement('label');
  метка.className = 'повтор-следить';
  const галочка = document.createElement('input');
  галочка.type = 'checkbox';
  галочка.className = 'настр-галочка';
  галочка.checked = включено;
  метка.appendChild(галочка);
  метка.appendChild(document.createTextNode(подписьГалочки));
  строка.appendChild(метка);
  const переход = document.createElement('button');
  переход.type = 'button';
  переход.className = 'панель-активность-все возможности-переход';
  переход.textContent = 'Настроить →';
  переход.addEventListener('click', () => открытьНастройкиПодраздел(раздел));
  строка.appendChild(переход);
  галочка.addEventListener('change', async () => {
    const хочу = галочка.checked;
    const было = возможностиСейчас();
    const прежнее = было ? было[название] : undefined;
    if (было) было[название] = хочу ? вкл : выкл;
    try {
      const ответ = await fetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(шлёт(хочу)),
      });
      const данные = await ответ.json().catch(() => ({}));
      if (!ответ.ok || !данные.ok) {
        throw new Error(данные.error || (данные.errors || []).join('; ') || ('HTTP ' + ответ.status));
      }
    } catch (e) {
      if (было) было[название] = прежнее;
      галочка.checked = !хочу;
      надпись.textContent = 'Не вышло: ' + e.message;
    }
  });
  return строка;
}

function возможностиСейчас() {
  return (последние && последние.возможности) || null;
}

/* Где искать — по-человечески, без «web_search_mode». */
const ГДЕ_ИСКАТЬ = { free: 'бесплатно', auto: 'сначала бесплатно', paid: 'платно' };

function блокВозможностей() {
  const данные = возможностиСейчас() || {};
  const блок = document.createElement('section');
  блок.className = 'панель-блок панель-возможности';
  блок.appendChild(шапкаБлока('Возможности'));

  const поискВкл = данные.поиск === true;
  const строкаПоиска = строкаВозможности(
    'поиск', поискВкл,
    'Поиск в интернете: ' + (поискВкл
      ? 'включён · ' + (ГДЕ_ИСКАТЬ[данные.поиск_где] || 'бесплатно')
      : 'выключен'),
    'включён',
    (хочу) => ({ web_search: хочу }),
    'поиск', true, false);
  блок.appendChild(строкаПоиска);

  // Возвращать заходы обратно нужно к той частоте, что была последней:
  // включить «никогда» обратно — значит ничего не сделать.
  const частота = данные.первой || 'never';
  const перваяВкл = частота !== 'never';
  const последняя = данные.первой_последняя || 'sometimes';
  const строкаПервой = строкаВозможности(
    'первой', перваяВкл,
    'Сама заговаривает: ' + (перваяВкл ? частотаКоротко(частота) : 'выключено'),
    'включено',
    (хочу) => ({ proactive: хочу ? последняя : 'never' }),
    'первой', последняя, 'never');
  блок.appendChild(строкаПервой);
  return блок;
}

/* ---------- Напоминания и таймеры на Панели ----------

   Список приходит из `/api/reminders`, а ставит его модель голосом — пульт
   не разбирает время и не притворяется будильником. Ему нужен только
   список, чтобы хозяин видел, что стоит, и «✕» на каждой строке, чтобы
   снять не заходя в разговор.

   `due` приходит ISO-строкой с поясом, и «через N мин» считается здесь
   часами браузера: они те же, что у будильника, поэтому расхождение
   показать негде. */
let напоминанияКэш = [];
let напоминанияКогда = 0;
let напоминанияТаймер = null;

/* Раз в 30 с: цифра «через N мин» меняется медленно, а таймер на каждую
   строку — это десяток setInterval на Панели, которой может и не быть. Один
   таймер на весь блок, и живёт он только пока блок на экране (снимается в
   `нарисоватьПанель` и при уходе с Панели). */
const НАПОМИНАНИЯ_ПАУЗА = 30000;

async function спроситьНапоминания(срочно) {
  if (!срочно && Date.now() - напоминанияКогда < 15000) return;
  напоминанияКогда = Date.now();
  try {
    const ответ = await fetch('/api/reminders', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    напоминанияКэш = Array.isArray(данные.items) ? данные.items : [];
    if (текущий === 'панель') нарисоватьПанель();
  } catch (e) {
    /* Молчим: блок останется с прежним списком. */
  }
}

/* «17:00 · через 23 мин — вытащить пиццу». У таймера без слов хвост —
   «таймер»: иначе строка была бы «17:05 · через 4 мин — » и обрывалась
   бы на тире. */
function напоминаниеФраза(запись) {
  const момент = new Date(запись && запись.due);
  if (Number.isNaN(момент.getTime())) return 'напоминание';
  const часы = String(момент.getHours()).padStart(2, '0')
    + ':' + String(момент.getMinutes()).padStart(2, '0');
  const минут = Math.max(0, Math.round((момент.getTime() - Date.now()) / 60000));
  const через = минут > 0 ? 'через ' + минут + ' мин' : 'меньше минуты';
  const оЧем = String((запись && запись.text) || '').trim()
    || ((запись && запись.kind) === 'timer' ? 'таймер' : 'напоминание');
  return часы + ' · ' + через + ' — ' + оЧем;
}

async function напоминаниеОтменить(id) {
  // Строку убираем сразу: отмена локальная и почти всегда проходит. Если
  // сервер откажет — вернём список и покажем всё как было, иначе на экране
  // остался бы «✕», который ничего не снял.
  напоминанияКэш = напоминанияКэш.filter((одна) => одна.id !== id);
  if (текущий === 'панель') нарисоватьПанель();
  try {
    const ответ = await fetch('/api/reminders/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) спроситьНапоминания(true);
  } catch (e) {
    спроситьНапоминания(true);
  }
}

function напоминаниеСтрока(запись) {
  const строка = document.createElement('div');
  строка.className = 'напоминание-строка';
  const текст = document.createElement('span');
  текст.className = 'напоминание-текст';
  текст.textContent = напоминаниеФраза(запись);
  строка.appendChild(текст);
  const отмена = document.createElement('button');
  отмена.type = 'button';
  отмена.className = 'напоминание-отмена';
  отмена.textContent = '✕';
  отмена.title = 'Отменить';
  // Без confirm: это не удаление данных, а снятие будильника. Спрашивать
  // подтверждение там, где ничего не пропадёт, только раздражает.
  отмена.addEventListener('click', () => напоминаниеОтменить(запись.id));
  строка.appendChild(отмена);
  return строка;
}

function напоминанияТаймерСнять() {
  if (напоминанияТаймер === null) return;
  clearInterval(напоминанияТаймер);
  напоминанияТаймер = null;
}

/* Блок виден, только когда есть активные: пустое «НАПОМИНАНИЯ» занимало бы
   место на Панели и обещало бы, что список сломан. */
function блокНапоминаний() {
  if (!напоминанияКэш.length) {
    напоминанияТаймерСнять();
    return null;
  }
  const блок = document.createElement('section');
  блок.className = 'панель-блок панель-напоминания';
  блок.appendChild(шапкаБлока('Напоминания'));
  const список = document.createElement('div');
  список.className = 'напоминания-строки';
  for (const запись of напоминанияКэш) {
    список.appendChild(напоминаниеСтрока(запись));
  }
  блок.appendChild(список);
  if (напоминанияТаймер === null) {
    напоминанияТаймер = setInterval(() => {
      // Панель могли закрыть, а таймер — остаться: снять себя здесь же.
      if (текущий !== 'панель') {
        напоминанияТаймерСнять();
        return;
      }
      нарисоватьПанель();
    }, НАПОМИНАНИЯ_ПАУЗА);
  }
  return блок;
}

/* Напоминание поставили, сняли или оно сработало — список надо перечитать.
   События те же, что идут в журнал (`reminder`, `reminder_cancel`,
   `reminder_fired`), Панель узнаёт о них из ленты `/api/runtime`. */
function напоминаниеСобытие(событие) {
  return !!событие && ['reminder', 'reminder_cancel', 'reminder_fired']
    .includes(событие.kind);
}

/* Расход: /api/usage отдаёт три периода, курс и откуда цены. Раз в 15 с —
   сумма меняется медленно, а сервер читает журнал расхода целиком. */
let расходКэш = null;
let расходКогда = 0;

async function спроситьРасход(срочно) {
  if (!срочно && Date.now() - расходКогда < 15000) return;
  расходКогда = Date.now();
  try {
    const ответ = await fetch('/api/usage', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    расходКэш = данные;
    if (текущий === 'панель') нарисоватьПанель();
  } catch (e) {
    /* Молчим: блок останется с прежними числами. */
  }
}

/* Токены — по-человечески: 312 тыс., 1.4 млн. */
function токены(сколько) {
  const n = число(сколько);
  if (n === null) return '—';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' млн';
  if (n >= 1e3) return String(Math.round(n / 1e3)) + ' тыс.';
  return String(n);
}

function доллары(usd) {
  const n = число(usd);
  if (n === null) return '—';
  if (n > 0 && n < 0.01) return '$' + n.toFixed(4);
  return '$' + n.toFixed(2);
}

/* Имя модели без провайдера: «deepseek/deepseek-flash» → «deepseek-flash». */
function короткаяМодель(имя) {
  const части = String(имя || '').split('/');
  return части[части.length - 1] || 'без модели';
}

function сноскаРасхода(данные) {
  const части = [];
  const сегодня = (данные.periods && данные.periods.today) || {};
  const модели = Array.isArray(сегодня.models) ? сегодня.models : [];
  const дорогая = модели.find((м) => число(м.usd) !== null);
  if (дорогая) {
    части.push('дороже всего сегодня: ' + короткаяМодель(дорогая.model) + ' · ' +
      доллары(дорогая.usd));
  }
  const цены = данные.prices || {};
  части.push('цены: OpenAI и DeepSeek — таблица от ' + (цены.openai || '—') +
    ', OpenRouter — живые' + (цены.openrouter ? ', обновлены ' + цены.openrouter : ''));
  return части.join(' · ');
}

function столбецРасхода(подпись, период, курс) {
  const столбец = document.createElement('div');
  столбец.className = 'расход-столбец';
  const имя = document.createElement('div');
  имя.className = 'расход-период';
  имя.textContent = подпись;
  столбец.appendChild(имя);

  const usd = число(период.usd);
  const рубл = число(курс);
  // Ноль с плашкой «без цены» — это не «ничего не потрачено», а «не знаем,
  // сколько»: крупным числом молчать честнее, чем писать ноль.
  const безЦены = период.unpriced === true && (usd === null || usd === 0);
  const деньги = document.createElement('div');
  деньги.className = 'расход-деньги';
  if (usd === null || безЦены) {
    деньги.textContent = '—';
  } else if (рубл !== null) {
    деньги.textContent = '≈ ' + (usd * рубл >= 100
      ? Math.round(usd * рубл) : (usd * рубл).toFixed(2)) + ' ₽';
  } else {
    деньги.textContent = доллары(usd);
  }
  if (период.unpriced === true) {
    const приписка = document.createElement('span');
    приписка.className = 'расход-без-цены';
    приписка.textContent = '+ без цены';
    деньги.appendChild(приписка);
  }
  // Локальная модель не «без цены», а действительно бесплатна: говорим
  // словами, чтобы ноль в сводке не выглядел как забывшаяся метрика.
  if (период.local === true) {
    const приписка = document.createElement('span');
    приписка.className = 'расход-без-цены';
    приписка.textContent = 'локальная — бесплатно';
    деньги.appendChild(приписка);
  }
  столбец.appendChild(деньги);

  // Доллары мельче — только когда крупными уже идут рубли, и сумма известна.
  if (рубл !== null && usd !== null && !безЦены) {
    const подытог = document.createElement('div');
    подытог.className = 'расход-подытог';
    подытог.textContent = доллары(usd);
    столбец.appendChild(подытог);
  }

  const поток = document.createElement('div');
  поток.className = 'расход-токены';
  const вход = токены(период.prompt);
  const кеш = число(период.cached);
  поток.textContent = 'вход ' + вход +
    (кеш !== null && кеш > 0 ? ' · из кеша ' + токены(кеш) : '') +
    ' · выход ' + токены(период.completion);
  столбец.appendChild(поток);
  return столбец;
}

function блокРасхода() {
  const данные = расходКэш || {};
  const периоды = данные.periods || {};
  const блок = document.createElement('section');
  блок.className = 'панель-блок расход';
  // В шапке блока — «Настроить →» в «Ответы и подключение»: там ключ, модель
  // и цена запроса, из которой этот расход и складывается.
  const шапкаРасхода = шапкаБлока('Расход');
  const настроитьРасход = document.createElement('button');
  настроитьРасход.type = 'button';
  настроитьРасход.className = 'панель-активность-все';
  настроитьРасход.textContent = 'Настроить →';
  настроитьРасход.addEventListener('click', () => открытьНастройкиПодраздел('ответы'));
  шапкаРасхода.appendChild(настроитьРасход);
  блок.appendChild(шапкаРасхода);
  const строки = document.createElement('div');
  строки.className = 'расход-строки';
  for (const [имя, подпись] of [['session', 'Сеанс'], ['today', 'Сегодня'],
                               ['week', '7 дней']]) {
    строки.appendChild(столбецРасхода(подпись, периоды[имя] || {}, данные.rub));
  }
  блок.appendChild(строки);
  const сноска = document.createElement('div');
  сноска.className = 'расход-сноска';
  сноска.textContent = расходКэш ? сноскаРасхода(данные) : 'Считаю расход…';
  блок.appendChild(сноска);
  return блок;
}

/* Заголовок блока Панели — тот же, что у «Компьютера». */
function шапкаБлока(заголовок) {
  const шапка = document.createElement('div');
  шапка.className = 'панель-блок-шапка';
  const имя = document.createElement('h2');
  имя.textContent = заголовок;
  шапка.appendChild(имя);
  return шапка;
}

function нарисоватьПанель() {
  лист.classList.add('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.innerHTML = '';
  const железо = (последние && последние.железо) || {};

  /* Панель — ряд блоков одной семьи. Первый — голос, второй — «Компьютер»:
     02.10 хозяин: «нужно отделить голос от компьютера в панели» — раньше они
     делили одну рамку. Остальное — в своих блоках ниже. */
  const верх = document.createElement('section');
  верх.className = 'панель-блок панель-голос-блок';

  const управление = document.createElement('div');
  управление.className = 'панель-голос';
  const подписи = document.createElement('div');
  подписи.className = 'панель-голос-подписи';
  const название = document.createElement('strong');
  название.textContent = 'Голос';
  подписи.appendChild(название);
  const состояние = document.createElement('span');
  состояние.textContent = 'Проверяю…';
  подписи.appendChild(состояние);
  управление.appendChild(подписи);
  const действия = document.createElement('div');
  действия.className = 'панель-голос-действия';
  const выборПодпись = document.createElement('label');
  выборПодпись.className = 'панель-голос-режим';
  выборПодпись.appendChild(document.createTextNode('Слушать'));
  const выбор = document.createElement('select');
  выбор.className = 'голос-выбор';
  выбор.setAttribute('aria-label', 'Режим прослушивания');
  for (const [значение, текст] of РЕЖИМЫ) {
    const пункт = document.createElement('option');
    пункт.value = значение;
    пункт.textContent = текст;
    выбор.appendChild(пункт);
  }
  выборПодпись.appendChild(выбор);
  действия.appendChild(выборПодпись);
  const тише = document.createElement('button');
  тише.type = 'button';
  тише.className = 'голос-кнопка';
  тише.textContent = 'Замолчать';
  действия.appendChild(тише);
  const включить = document.createElement('button');
  включить.type = 'button';
  включить.className = 'голос-кнопка главная';
  включить.textContent = 'Включить голос';
  действия.appendChild(включить);
  // В конце строки голоса — «Настроить →» в «Голос → Озвучивание».
  const настроитьГолос = document.createElement('button');
  настроитьГолос.type = 'button';
  настроитьГолос.className = 'панель-активность-все';
  настроитьГолос.textContent = 'Настроить →';
  настроитьГолос.addEventListener('click', () => открытьГолосПодраздел('голос'));
  действия.appendChild(настроитьГолос);
  управление.appendChild(действия);
  верх.appendChild(управление);
  /* Подробность — отдельной строкой ПОД карточкой, во всю ширину: длинный
     `loading_text` («качаю модель Higgs: 0,0 из ~9,3 ГБ (0 %)») стоял в
     строке состояния, разрывал карточку и двигал кнопку «Выключить голос»
     (02.10). Здесь же полоса скачивания модели с «Отменить». */
  const подробности = document.createElement('div');
  подробности.className = 'панель-голос-подробности';
  подробности.hidden = true;
  const подробностиТекст = document.createElement('div');
  подробностиТекст.className = 'панель-голос-подробности-текст';
  подробностиТекст.textContent = '';
  const подробностиПолоса = document.createElement('div');
  подробностиПолоса.className = 'голоса-полоса-место';
  // Установка библиотек — тот же ход, но вторая полоса: пока ставятся пакеты,
  // моделей обычно не качается, и обе строки нужны под карточкой.
  const подробностиБибПолоса = document.createElement('div');
  подробностиБибПолоса.className = 'голоса-полоса-место';
  подробности.append(подробностиТекст, подробностиПолоса, подробностиБибПолоса);
  верх.appendChild(подробности);
  панельГолосЭлементы = { корень: управление, состояние, выбор, тише, включить,
    подробности, подробностиТекст, подробностиПолоса, подробностиБибПолоса };
  выбор.addEventListener('change', () => панельГолосДействие('mode', выбор.value));
  тише.addEventListener('click', () => панельГолосДействие('hush'));
  включить.addEventListener('click', () => панельГолосДействие('toggle'));

  const секция = document.createElement('section');
  секция.className = 'компьютер';

  const шапка = document.createElement('div');
  шапка.className = 'компьютер-шапка';
  const заголовок = document.createElement('h2');
  заголовок.textContent = 'Компьютер';
  шапка.appendChild(заголовок);
  if (typeof железо.time === 'string' && железо.time.trim() !== '') {
    const снимок = document.createElement('span');
    снимок.textContent = 'обновлено ' + железо.time.trim();
    шапка.appendChild(снимок);
  }
  секция.appendChild(шапка);

  const сетка = document.createElement('div');
  сетка.className = 'метрики';

  const температураЦп = число(железо.cpu_temp);
  сетка.appendChild(
    метрика(
      'CPU',
      проценты(железо.cpu),
      температураЦп === null ? 'текущая загрузка' : температураЦп + ' °C',
      железо.cpu
    )
  );
  сетка.appendChild(
    метрика(
      'RAM',
      проценты(железо.ram),
      гигабайты(железо.ram_used, железо.ram_total),
      железо.ram,
      true
    )
  );

  const температура = число(железо.gpu_temp);
  сетка.appendChild(
    метрика(
      'GPU',
      проценты(железо.gpu_load),
      температура === null ? 'текущая загрузка' : температура + ' °C',
      железо.gpu_load
    )
  );

  const занято = число(железо.gpu_mem_used);
  const всего = число(железо.gpu_mem_total);
  let доля = null;
  if (занято !== null && всего !== null && всего > 0 && занято >= 0) {
    доля = (занято / всего) * 100;
  }
  сетка.appendChild(
    метрика(
      'VRAM',
      проценты(доля),
      гигабайты(занято, всего),
      доля,
      true
    )
  );
  секция.appendChild(сетка);
  лист.appendChild(верх);
  const компьютерБлок = document.createElement('section');
  компьютерБлок.className = 'панель-блок';
  компьютерБлок.appendChild(секция);
  лист.appendChild(компьютерБлок);
  обновитьПанельГолос();
  /* Модель качественного голоса качает пульт, а не сам голос, поэтому его ход
     на Панели виден только после вопроса `/api/voices/status` — и установка
     библиотек тоже. Один раз за отрисовку этого достаточно: пока идёт дело,
     вопросы идут по своему таймеру. */
  if (!голосаИдёт() && !document.hidden) голосаОдинРаз().then(голосаОпросЕслиИдёт);

  /* Мгновенный повтор NVIDIA — отдельной узкой строкой-блоком: под
     «Компьютером» он больше не нужен, а заголовок делает его своим. */
  const запись = document.createElement('section');
  запись.className = 'панель-блок панель-блок-узкий';
  запись.appendChild(шапкаБлока('Запись игры'));
  запись.appendChild(строкаПовтора());
  лист.appendChild(запись);

  /* «Возможности» — две строки-переключателя рядом с «Записью игры». Это
     самые ходовые настройки из всех: включить и выключить их хозяин должен
     не заходя в Настройки. Данные — из /state, как у «Записи игры». */
  лист.appendChild(блокВозможностей());

  /* «Напоминания» — рядом с «Возможностями»: тоже то, о чём хозяин должен
     знать, не заходя ни в разговор, ни в Настройки. Блок возвращает null,
     когда напоминаний нет, — тогда на Панели ничего не лишнего. */
  const напоминания = блокНапоминаний();
  if (напоминания) лист.appendChild(напоминания);
  спроситьНапоминания();

  /* «Расход» — отдельной строкой во всю ширину: три столбца с суммами и
     токенами в половине окна разваливались. Данные приходят отдельным
     запросом — /state о расходе не знает. */
  лист.appendChild(блокРасхода());
  спроситьРасход();

  /* Дальше — «Разговор» и «Последние события» рядом в две колонки.
     Одна короткая строка событий за текущий запуск, без копии ленты Голоса. */
  const низ = document.createElement('div');
  низ.className = 'панель-низ';

  const разговор = document.createElement('section');
  разговор.className = 'панель-блок разговор';
  разговор.appendChild(шапкаБлока('Разговор'));
  const показатели = document.createElement('div');
  показатели.className = 'разговор-строки';
  const обзор = голосОбзор || {};
  const первыйЗвук = число(обзор.last_first_sound);
  const показателиРазговора = [
    ['Звук ответа', первыйЗвук !== null && первыйЗвук > 0 ? первыйЗвук.toFixed(2) + ' с' : '—'],
    ['Пропущено', Number.isInteger(обзор.ignored) ? String(обзор.ignored) : '—'],
    ['Перебивания', Number.isInteger(обзор.interrupted) ? String(обзор.interrupted) : '—'],
  ];
  for (const [имя, текст] of показателиРазговора) {
    const ячейка = document.createElement('div');
    ячейка.className = 'разговор-ячейка';
    const подпись = document.createElement('div');
    подпись.className = 'разговор-имя';
    подпись.textContent = имя;
    ячейка.appendChild(подпись);
    const значение = document.createElement('div');
    значение.className = 'разговор-значение';
    значение.textContent = текст;
    ячейка.appendChild(значение);
    показатели.appendChild(ячейка);
  }
  разговор.appendChild(показатели);
  const сноска = document.createElement('div');
  сноска.className = 'разговор-подпись';
  const распознала = число(обзор.last_stt_time);
  сноска.textContent = 'За текущий запуск' + (распознала !== null
    ? ' · последняя фраза распознана за ' + распознала.toFixed(2) + ' с' : '');
  разговор.appendChild(сноска);
  низ.appendChild(разговор);

  const события = голосСобытия.filter((событие) =>
    ['heard', 'spoken', 'ignored', 'interrupted', 'conversation', 'error'].includes(событие.kind)).slice(-3).reverse();
  const активность = document.createElement('section');
  активность.className = 'панель-блок панель-активность';
  const активностьШапка = шапкаБлока('Последние события');
  const открытьГолос = document.createElement('button');
  открытьГолос.type = 'button';
  открытьГолос.className = 'панель-активность-все';
  открытьГолос.textContent = 'Вся история →';
  открытьГолос.addEventListener('click', () => открытьЛоги('разговор'));
  активностьШапка.appendChild(открытьГолос);
  активность.appendChild(активностьШапка);
  if (!события.length) {
    const пусто = document.createElement('div');
    пусто.className = 'панель-активность-пусто';
    const сохранено = обзор.last_saved_at ? new Date(обзор.last_saved_at) : null;
    if (сохранено && !Number.isNaN(сохранено.getTime())) {
      const сегодня = new Date();
      const день = сохранено.toDateString() === сегодня.toDateString()
        ? 'сегодня' : сохранено.toLocaleDateString('ru-RU');
      пусто.textContent = 'Сейчас тихо · последняя сохранённая реплика ' + день +
        ' в ' + сохранено.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) + '.';
    } else пусто.textContent = 'Пока нет сохранённого разговора.';
    активность.appendChild(пусто);
  } else for (const событие of события) {
    const строка = document.createElement('div');
    строка.className = 'панель-активность-строка';
    const время = document.createElement('span');
    время.textContent = событие.time || '';
    строка.appendChild(время);
    const описание = document.createElement('span');
    const данные = событие.payload && typeof событие.payload === 'object' ? событие.payload : {};
    if (событие.kind === 'heard') описание.textContent = 'Фраза распознана за ' + времяСобытия(данные.stt_time);
    else if (событие.kind === 'spoken') описание.textContent = 'Ответ прозвучал за ' + времяСобытия(данные.total);
    else if (событие.kind === 'ignored') описание.textContent = 'Пропущена фраза · ' + String(данные.why || 'без причины');
    else if (событие.kind === 'interrupted') описание.textContent = 'Ответ перебили';
    else if (событие.kind === 'conversation') описание.textContent = событие.payload ? 'Разговор начат' : 'Разговор закрыт';
    else описание.textContent = 'Ошибка голоса · смотри журнал';
    строка.appendChild(описание);
    активность.appendChild(строка);
  }
  низ.appendChild(активность);
  лист.appendChild(низ);
}

/* Чат держим в памяти страницы: при переключении вкладок ленту не
   пересоздаём, а возвращаем как была. */
let разговор = [];
let чатЖдём = false;
let чатЭлементы = null;

function строкаРазговора(текст, вид) {
  const строка = document.createElement('div');
  строка.className = 'чат-строка' + (вид ? ' ' + вид : '');
  const тело = document.createElement('div');
  тело.className = 'чат-текст';
  тело.textContent = текст;
  строка.appendChild(тело);
  return строка;
}

function обновитьКнопку() {
  if (!чатЭлементы) return;
  const естьТекст = чатЭлементы.поле.value.trim() !== '';
  чатЭлементы.кнопка.disabled = !естьТекст || чатЖдём;
  чатЭлементы.кнопка.title = чатЖдём ? 'Ждём ответ…' : '';
  чатЭлементы.новый.disabled = чатЖдём;
}

function перерисоватьЛенту() {
  if (!чатЭлементы) return;
  const лента = чатЭлементы.лента;
  лента.innerHTML = '';
  if (разговор.length === 0) {
    const пусто = document.createElement('div');
    пусто.className = 'чат-пусто';
    const заголовок = document.createElement('b');
    заголовок.textContent = 'Новый разговор';
    пусто.appendChild(заголовок);
    const подпись = document.createElement('span');
    подпись.textContent = 'Память общая с голосом';
    пусто.appendChild(подпись);
    лента.appendChild(пусто);
    return;
  }
  for (const реплика of разговор) {
    if (реплика.думает) {
      лента.appendChild(строкаРазговора('думает…', 'думает'));
    } else if (реплика.ошибка) {
      лента.appendChild(строкаРазговора(реплика.текст, 'ошибка'));
    } else {
      лента.appendChild(строкаРазговора(реплика.текст, реплика.роль));
    }
  }
  лента.scrollTop = лента.scrollHeight;
}

async function отправитьЧат() {
  if (!чатЭлементы || чатЖдём) return;
  const текст = чатЭлементы.поле.value.trim();
  if (!текст) return;
  чатЭлементы.поле.value = '';
  разговор.push({ роль: 'я', текст });
  разговор.push({ думает: true });
  чатЖдём = true;
  перерисоватьЛенту();
  обновитьКнопку();
  try {
    const ответ = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: текст }),
    });
    if (!ответ.ok || !ответ.body) {
      let данные = null;
      try { данные = await ответ.json(); } catch (e) { данные = null; }
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    const читатель = ответ.body.getReader();
    const декодер = new TextDecoder();
    let хвост = '';
    let завершено = false;
    let реплика = null;
    while (true) {
      const часть = await читатель.read();
      хвост += декодер.decode(часть.value || new Uint8Array(), { stream: !часть.done });
      const строки = хвост.split('\n');
      хвост = строки.pop();
      for (const строка of строки) {
        if (!строка.trim()) continue;
        const событие = JSON.parse(строка);
        if (событие.type === 'chunk') {
          const кусок = String(событие.text || '').trim();
          if (!кусок) continue;
          if (!реплика) {
            разговор = разговор.filter((р) => !р.думает);
            реплика = { роль: 'она', текст: '' };
            разговор.push(реплика);
          }
          реплика.текст += (реплика.текст ? ' ' : '') + кусок;
          перерисоватьЛенту();
        } else if (событие.type === 'error') {
          throw new Error(String(событие.error || 'ответ прервался'));
        } else if (событие.type === 'done') {
          завершено = true;
        }
      }
      if (часть.done) break;
    }
    if (!завершено) throw new Error('связь прервалась до конца ответа');
  } catch (e) {
    разговор = разговор.filter((р) => !р.думает);
    разговор.push({ ошибка: true, текст: 'Не вышло: ' + e.message + '. Попробуй ещё раз.' });
  }
  чатЖдём = false;
  перерисоватьЛенту();
  обновитьКнопку();
  if (чатЭлементы?.корень.isConnected) чатЭлементы.поле.focus();
}

async function новыйРазговор() {
  if (!чатЭлементы || чатЖдём) return;
  if (!window.confirm('Начать новый разговор? Прошлая переписка перестанет влиять на ответы. Факты о тебе останутся.')) return;
  чатЖдём = true;
  обновитьКнопку();
  try {
    const ответ = await fetch('/api/settings/clear-history', { method: 'POST' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    разговор = [];
    перерисоватьЛенту();
  } catch (e) {
    разговор.push({ ошибка: true, текст: 'Не удалось начать новый разговор: ' + e.message });
    перерисоватьЛенту();
  } finally {
    чатЖдём = false;
    обновитьКнопку();
  }
}

function нарисоватьЧат() {
  лист.classList.remove('компьютерный');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.add('чатовый');

  /* Историю храним в «разговор»: корень чата живёт между заходами
     во вкладку — откры() чистит лист, но ссылка остаётся, возвращаем как был. */
  if (чатЭлементы) {
    лист.innerHTML = '';
    лист.appendChild(чатЭлементы.корень);
    перерисоватьЛенту();
    обновитьКнопку();
    return;
  }

  лист.innerHTML = '';

  const чат = document.createElement('div');
  чат.className = 'чат';

  const лента = document.createElement('div');
  лента.className = 'чат-лента';
  чат.appendChild(лента);

  const низ = document.createElement('div');
  низ.className = 'чат-низ';
  const ввод = document.createElement('div');
  ввод.className = 'чат-ввод';
  const поле = document.createElement('textarea');
  поле.placeholder = 'Напиши сообщение…';
  поле.rows = 1;
  ввод.appendChild(поле);
  const кнопка = document.createElement('button');
  кнопка.type = 'button';
  кнопка.setAttribute('aria-label', 'Отправить');
  кнопка.title = 'Отправить';
  кнопка.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15m-6-6 6 6-6 6"/></svg>';
  кнопка.disabled = true;
  ввод.appendChild(кнопка);
  низ.appendChild(ввод);
  const подсказка = document.createElement('div');
  подсказка.className = 'чат-подсказка';
  const клавиши = document.createElement('span');
  клавиши.textContent = 'Enter — отправить · Shift+Enter — новая строка';
  подсказка.appendChild(клавиши);
  const новый = document.createElement('button');
  новый.type = 'button';
  новый.className = 'чат-новый';
  новый.textContent = 'Новый разговор';
  подсказка.appendChild(новый);
  низ.appendChild(подсказка);
  чат.appendChild(низ);

  лист.appendChild(чат);
  чатЭлементы = { корень: чат, лента, поле, кнопка, новый };

  поле.addEventListener('input', обновитьКнопку);
  поле.addEventListener('keydown', (событие) => {
    if (событие.key === 'Enter' && !событие.shiftKey) {
      событие.preventDefault();
      отправитьЧат();
    }
  });
  кнопка.addEventListener('click', отправитьЧат);
  новый.addEventListener('click', новыйРазговор);

  перерисоватьЛенту();
  обновитьКнопку();
}

/* Голос: управление через /api/voice/*, состояние и события через
   /api/runtime?after=ID. Корень пересоздаём при каждом заходе,
   события копим в голосСобытия, опрос — раз в 2 секунды. */
let голосЭлементы = null;
let голосСобытия = [];
let голосПоследний = 0;
let голосГолос = null;
let голосТянем = false;
let голосОбзор = null;
let панельГолосЭлементы = null;
let панельГолосЖдём = false;
let панельГолосОшибка = '';

const РЕЖИМЫ = [
  ['always', 'Всегда'],
  ['name', 'По имени'],
  ['off', 'Выкл'],
];

function времяСобытия(значение) {

  if (значение === null || значение === undefined || значение === '') return '—';

  const n = число(Number(значение));

  return n === null ? '—' : n.toFixed(2) + ' с';

}



function применитьГолос(состояние) {
  if (!состояние) return;
  голосГолос = состояние;
  обновитьПанельГолос();
  // Уровень могли сменить голосом или с телефона, пока форма открыта:
  // подтягиваем его в поле, иначе «Сохранить» вернул бы старый.
  if (typeof состояние.volume === 'number' && настрЭлементы?.voice_volume &&
      настрСтраница === 'голос') {
    настрЭлементы.voice_volume.value = String(состояние.volume);
    настрЭлементы.voice_volume.синхПолзунок?.();
  }
  if (!голосЭлементы || текущий !== 'голос') return;
  const работает = !!состояние.running;
  const грузится = работает && состояние.ready === false;
  голосЭлементы.точка.classList.toggle('живая', работает && !грузится);
  голосЭлементы.заголовок.textContent = состояние.stopping ? 'Голос выключается…' :
    грузится ? голосЗагрузка(состояние, 'Голос загружается') :
    работает ? 'Голос включён, слушает' : 'Голос выключен';
  let подписьРежима = 'Выкл';
  if (состояние.mode === 'always') подписьРежима = 'Всегда';
  else if (состояние.mode === 'name') подписьРежима = 'По имени';
  if (состояние.in_conversation) подписьРежима += ' · в разговоре';
  голосЭлементы.режим.textContent = 'Режим: ' + подписьРежима;
  голосЭлементы.переключатель.textContent = состояние.stopping ? 'Выключаю…' :
    работает ? 'Выключить' : 'Включить';
  голосЭлементы.переключатель.disabled = !!состояние.stopping;
  голосЭлементы.переключатель.title = '';
  голосЭлементы.замолчать.disabled = !работает;
  голосЭлементы.замолчать.title = работает ? '' : 'Голос выключен';
  if (typeof состояние.mode === 'string') голосЭлементы.выбор.value = состояние.mode;
}

/* «Загружается…» с тем, что именно: проценты скачивания Higgs (~9 ГБ при
   первом включении) — иначе минуты «подожди» выглядят зависанием (01.10). */
function голосЗагрузка(состояние, начало) {
  const что = String(состояние?.loading_text || '').trim();
  return что ? начало + ': ' + что : начало + '… подожди';
}

async function голосЗапрос(адрес, тело) {
  const параметры = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
  if (тело !== undefined) параметры.body = JSON.stringify(тело);
  const ответ = await fetch(адрес, параметры);
  let данные = null;
  try {
    данные = await ответ.json();
  } catch (e) {
    данные = null;
  }
  if (!ответ.ok || !данные || !данные.ok) {
    throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
  }
  return данные.voice || null;
}

function обновитьПанельГолос() {
  const эл = панельГолосЭлементы;
  if (!эл || !эл.корень.isConnected) return;
  const голос = голосГолос;
  const работает = !!голос?.running;
  const режим = голос?.mode || 'off';
  const названия = { always: 'всегда', name: 'по имени', off: 'не слушает' };
  const грузится = работает && голос?.ready === false;
  /* Строка состояния — короткая, без `loading_text`: длинный текст загрузки
     разрывал карточку и прыгала ширина кнопки «Выключить голос» (02.10).
     Подробность — отдельной строкой под карточкой (`панельГолосПодробности`). */
  эл.состояние.textContent = панельГолосОшибка || (!голос ? 'Проверяю…' :
    (голос.stopping ? 'Выключается…' :
      грузится ? 'Загружается…' : работает ? 'Работает' : 'Выключен') +
    ' · ' + названия[режим] +
    (голос.in_conversation ? ' · в разговоре' : ''));
  эл.состояние.classList.toggle('ошибка', !!панельГолосОшибка);
  панельГолосПодробности(эл, голос);
  эл.корень.classList.toggle('работает', работает);
  эл.выбор.value = режим;
  эл.выбор.disabled = !голос || панельГолосЖдём;
  эл.тише.disabled = !работает || панельГолосЖдём;
  эл.включить.disabled = !голос || панельГолосЖдём || !!голос.stopping;
  эл.включить.textContent = голос?.stopping ? 'Выключаю…' :
    работает ? 'Выключить голос' : 'Включить голос';
}

/* Строка под карточкой голоса: чем занята загрузка голоса, какая модель
   качается и ставятся ли библиотеки. Ничего не идёт — её нет вовсе, а не пустое
   место. Полоса — та же функция, что в «Голос → Озвучивании» (`голосаПолоса`
   для моделей и её близнец `голосаБибПолоса` для установки). */
function панельГолосПодробности(эл, голос) {
  if (!эл || !эл.подробности) return;
  const подробность = String((голос && голос.loading_text) || '').trim();
  const качается = !!(голосаХод && голосаХод.active);
  const ставятся = !!(голосаБибХод && голосаБибХод.active);
  эл.подробности.hidden = !подробность && !качается && !ставятся;
  эл.подробностиТекст.textContent = подробность;
  if (эл.подробностиПолоса) голосаПолоса(эл.подробностиПолоса, голосаХод);
  if (эл.подробностиБибПолоса) {
    голосаБибПолоса(эл.подробностиБибПолоса, голосаБибХод);
  }
}

async function панельГолосДействие(вид, значение) {
  if (панельГолосЖдём || !голосГолос) return;
  панельГолосЖдём = true;
  панельГолосОшибка = '';
  обновитьПанельГолос();
  try {
    if (вид === 'toggle' && !голосГолос.running && голосГолос.mode === 'off') {
      // Быстрый запуск из режима «Выкл» должен действительно начать слушать.
      применитьГолос(await голосЗапрос('/api/voice/mode', { value: 'name' }));
    }
    const адрес = вид === 'toggle' ? '/api/voice/toggle' :
      вид === 'hush' ? '/api/voice/hush' : '/api/voice/mode';
    const тело = вид === 'mode' ? { value: значение } : undefined;
    применитьГолос(await голосЗапрос(адрес, тело));
  } catch (e) {
    панельГолосОшибка = 'Не вышло: ' + e.message;
  } finally {
    панельГолосЖдём = false;
    обновитьПанельГолос();
  }
}

async function переключитьГолос() {
  if (!голосЭлементы) return;
  голосЭлементы.переключатель.disabled = true;
  try {
    применитьГолос(await голосЗапрос('/api/voice/toggle'));
  } catch (e) {
    голосЭлементы.переключатель.disabled = false;
    голосЭлементы.режим.textContent = 'Не вышло: ' + e.message;
  }
}

async function замолчать() {
  if (!голосЭлементы) return;
  голосЭлементы.замолчать.disabled = true;
  try {
    применитьГолос(await голосЗапрос('/api/voice/hush'));
  } catch (e) {
    if (голосЭлементы) голосЭлементы.режим.textContent = 'Не вышло: ' + e.message;
  }
  if (голосЭлементы && голосГолос) применитьГолос(голосГолос);
}

async function сменитьРежим(значение) {
  if (!голосЭлементы) return;
  try {
    применитьГолос(await голосЗапрос('/api/voice/mode', { value: значение }));
  } catch (e) {
    if (голосЭлементы) голосЭлементы.режим.textContent = 'Не вышло: ' + e.message;
    if (голосЭлементы && голосГолос && typeof голосГолос.mode === 'string') {
      голосЭлементы.выбор.value = голосГолос.mode;
    }
  }
}

/* Заметка записана — или только началась диктовка. Голос шлёт события вида
   `note`: «диктовка начата», «отменена», «пусто — записывать нечего» и
   «записала в …». Странице «Заметки» интересны только последние: они
   означают, что на диске появилась новая запись. */
function заметкиНоваяЗапись(событие) {
  return !!событие && событие.kind === 'note' &&
    String(событие.payload || '').includes('записала');
}

/* Периодический опрос рантайма: состояние голоса + новые события после ID. */
async function опроситьРантайм() {
  if ((текущий !== 'голос' && текущий !== 'панель' && текущий !== 'заметки') ||
      голосТянем) return;
  голосТянем = true;
  try {
    const ответ = await fetch('/api/runtime?after=' + голосПоследний, { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    if (данные.voice) применитьГолос(данные.voice);
    const обзорБыл = JSON.stringify(голосОбзор);
    if (данные.overview && typeof данные.overview === 'object') голосОбзор = данные.overview;
    let записали = false;
    let напомнили = false;
    if (Array.isArray(данные.events) && данные.events.length > 0) {
      for (const событие of данные.events) {
        голосСобытия.push(событие);
        if (typeof событие.id === 'number' && событие.id > голосПоследний) {
          голосПоследний = событие.id;
        }
        if (заметкиНоваяЗапись(событие)) записали = true;
        if (напоминаниеСобытие(событие)) напомнили = true;
      }
      if (голосСобытия.length > 300) голосСобытия = голосСобытия.slice(-300);
    }
    if (текущий === 'панель' && (обзорБыл !== JSON.stringify(голосОбзор) ||
      (Array.isArray(данные.events) && данные.events.length > 0))) нарисоватьПанель();
    // Труба только что записала мысль: список на странице «Заметки» уже
    // устарел, а перечитывать его без события было бы незачем.
    if (текущий === 'заметки' && записали) заметкиЗагрузить(true);
    // Напоминание поставили, сняли или оно сработало — «Напоминания» на
    // Панели показывают старое. Здесь узнаём об этом раньше всех: список
    // ведь не в /state.
    if (напомнили) спроситьНапоминания(true);
  } catch (e) {
    /* Молчим: общий опрос /state уже показывает связь. */
  }
  голосТянем = false;
}

function нарисоватьГолос() {
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('логовой');
  лист.classList.add('голосовой');
  лист.innerHTML = '';

  const голос = document.createElement('div');
  голос.className = 'голос';

  const верх = document.createElement('div');
  верх.className = 'голос-верх';

  const инфо = document.createElement('div');
  инфо.className = 'голос-инфо';
  const точка = document.createElement('span');
  точка.className = 'голос-точка';
  инфо.appendChild(точка);
  const тексты = document.createElement('div');
  тексты.className = 'голос-тексты';
  const заголовокСтроки = document.createElement('div');
  заголовокСтроки.className = 'голос-заголовок';
  заголовокСтроки.textContent = 'Голос…';
  тексты.appendChild(заголовокСтроки);
  const подпись = document.createElement('div');
  подпись.className = 'голос-подпись';
  подпись.textContent = 'Режим: …';
  тексты.appendChild(подпись);
  инфо.appendChild(тексты);
  верх.appendChild(инфо);

  const действия = document.createElement('div');
  действия.className = 'голос-действия';

  const выбор = document.createElement('select');
  выбор.className = 'голос-выбор';
  for (const [значение, название] of РЕЖИМЫ) {
    const пункт = document.createElement('option');
    пункт.value = значение;
    пункт.textContent = название;
    выбор.appendChild(пункт);
  }
  выбор.title = 'Режим прослушивания';
  действия.appendChild(выбор);

  const замолчатьКнопка = document.createElement('button');
  замолчатьКнопка.type = 'button';
  замолчатьКнопка.className = 'голос-кнопка';
  замолчатьКнопка.textContent = 'Замолчать';
  замолчатьКнопка.disabled = true;
  действия.appendChild(замолчатьКнопка);

  const включить = document.createElement('button');
  включить.type = 'button';
  включить.className = 'голос-кнопка главная';
  включить.textContent = 'Включить';
  включить.disabled = true;
  верх.appendChild(действия);
  верх.appendChild(включить);
  голос.appendChild(верх);

  // Лента событий переехала в Логи (фильтр «Разговор»): два места с
  // одним и тем же только путали. Здесь — ссылка туда.
  const вЖурнал = document.createElement('button');
  вЖурнал.type = 'button';
  вЖурнал.className = 'голос-кнопка';
  вЖурнал.textContent = 'Журнал разговора';
  вЖурнал.title = 'Что услышала, что ответила, что пропустила — в Логах';
  вЖурнал.addEventListener('click', () => открытьЛоги('разговор'));
  действия.insertBefore(вЖурнал, действия.firstChild);
  // Озвучивание и слух — та же форма, что в Настройках. Живёт, пока
  // открыт «Голос»: несохранённые поля не теряются при переключении.
  const настройкиМесто = document.createElement('div');
  настройкиМесто.className = 'голос-настройки';
  голос.appendChild(настройкиМесто);

  лист.appendChild(голос);
  голосЭлементы = {
    корень: голос, точка, заголовок: заголовокСтроки, режим: подпись,
    переключатель: включить, замолчать: замолчатьКнопка, выбор,
    настройкиМесто, настройкиЕсть: false,
  };

  включить.addEventListener('click', переключитьГолос);
  замолчатьКнопка.addEventListener('click', замолчать);
  выбор.addEventListener('change', () => сменитьРежим(выбор.value));

  показатьГолосПодраздел(голосПодраздел);
  if (голосГолос) применитьГолос(голосГолос);
  опроситьРантайм();
}

function показатьГолосПодраздел(код) {
  голосПодраздел = НАСТР_СТРАНИЦЫ.голос.includes(код) ? код : НАСТР_СТРАНИЦЫ.голос[0];
  document.querySelectorAll('#подменю-голоса [data-голос]').forEach((кнопка) => {
    кнопка.classList.toggle('активный', кнопка.dataset.голос === голосПодраздел);
  });
  const эл = голосЭлементы;
  if (!эл || !эл.корень.isConnected) return;
  if (!эл.настройкиЕсть) {
    построитьНастройки(эл.настройкиМесто, 'голос');
    эл.настройкиЕсть = true;
  } else {
    настрСтраница = 'голос';
    настрПоказатьРаздел(голосПодраздел);
  }
}

/* ---------- Логи ---------- */

/* Логи — живой хвост session.log; пауза не останавливает запись на диск. */
let логиЭлементы = null;
let логиСтроки = [];
let логиТянем = false;
let логиТаймер = null;
let логиПауза = false;
let логиПосле = 0;
let логиOffset = 0;

/* Журнал один на всё: разговор, интернет, телефон, программы, ошибки.
   Раньше разговор жил отдельной лентой в «Голосе» — хозяин попросил
   свести в одно место с фильтром. */
const ЛОГИ_ФИЛЬТРЫ = [
  ['все', 'Всё'], ['разговор', 'Разговор'], ['интернет', 'Интернет'],
  ['телефон', 'Телефон'], ['программы', 'Программы'], ['ошибки', 'Ошибки'],
  ['система', 'Система'],
];
let логиФильтр = (() => {
  try { return localStorage.getItem('логиФильтр') || 'разговор'; } catch (e) { return 'разговор'; }
})();

function типЛога(строка) {
  const текст = String(строка).replace(/^\[\d\d:\d\d:\d\d\]\s*/, '').toLowerCase();
  if (/интернет не помог/.test(текст)) return 'ошибки';
  if (/поиск в интернете|прочитала страницу|проверка поиска|искать в интернете/.test(текст)) return 'интернет';
  if (/ошиб|не удалось|не удался|не выш|не запуст|упал|не собран|не добавлен|не включился|не сохран/.test(текст)) return 'ошибки';
  // «задержка … с: слух … · модель …» — строка замера ответа (событие
  // timing из голосового цикла), и в разговоре она нужнее, чем в системе.
  if (/^(услышала|услышал |ответила|пропустил|перебили|разговор (начат|закрыт)|закрыла разговор|команда голосом|запомнила|поправила память|забыла:|память:|задержка )/.test(текст)) return 'разговор';
  // Нажатие пункта подменю — это действие программы, а не разговор:
  // «кнопка телефона» дальше уводит его в «Телефон», где его не ждут.
  if (/кнопка телефона: .+ → /.test(текст)) return 'программы';
  if (/программ|кнопок запуска|открыть программу/.test(текст)) return 'программы';
  if (/телефон|соединени|режим слуха с телефона|кнопка телефона/.test(текст)) return 'телефон';
  return 'система';
}

/* Оттенок строки внутри разговора: её речь, его речь, пропуск. */
function видСтроки(текст) {
  if (/^ответила/.test(текст)) return ' логи-она';
  if (/^услышала|^услышал /.test(текст)) return ' логи-он';
  if (/^пропустил/.test(текст)) return ' логи-мимо';
  return '';
}

function открытьЛоги(фильтр) {
  if (фильтр) логиФильтр = фильтр;
  if (текущий !== 'логи') открыть('логи');
  else перерисоватьЛоги(true);
}

function статусЛогов() {
  if (!логиЭлементы) return;
  const count = логиСтроки.length ? 'строк: ' + логиСтроки.length : 'пусто';
  логиЭлементы.статус.textContent = (логиПауза ? 'пауза' : 'обновляется') + ' · ' + count;
}

function перерисоватьЛоги(вниз = false) {
  if (!логиЭлементы) return;
  const фильтр = логиЭлементы.поиск.value.trim().toLowerCase();
  const тип = логиФильтр;
  const лента = логиЭлементы.лента;
  const стараяПрокрутка = лента.scrollTop;
  const уКонца = лента.scrollHeight - лента.scrollTop - лента.clientHeight < 40;
  лента.innerHTML = '';
  const видимые = логиСтроки.filter((строка) =>
    (тип === 'все' || типЛога(строка) === тип) &&
    (фильтр === '' || строка.toLowerCase().includes(фильтр)));
  if (видимые.length === 0) {
    const пусто = document.createElement('div');
    пусто.className = 'логи-пусто';
    пусто.textContent = логиСтроки.length === 0
      ? 'Логов пока нет'
      : 'Ничего не нашлось';
    лента.appendChild(пусто);
    return;
  }
  for (const строка of видимые.slice(-500)) {
    const ряд = document.createElement('div');
    const части = /^\[(\d\d:\d\d:\d\d)\]\s*(.*)$/s.exec(строка);
    const текст = части ? части[2] : строка;
    ряд.className = 'логи-строка логи-' + типЛога(строка) + видСтроки(текст);
    const время = document.createElement('span');
    время.className = 'логи-время';
    время.textContent = части ? части[1] : '';
    ряд.appendChild(время);
    const тело = document.createElement('span');
    тело.className = 'логи-текст';
    тело.textContent = текст;
    ряд.appendChild(тело);
    лента.appendChild(ряд);
  }
  лента.scrollTop = вниз || уКонца ? лента.scrollHeight : стараяПрокрутка;
}

async function загрузитьЛоги() {
  if (!логиЭлементы || логиТянем) return;
  const элементы = логиЭлементы;
  логиТянем = true;
  элементы.кнопка.disabled = true;
  элементы.очистить.disabled = true;
  try {
    const адрес = '/api/logs?limit=500' + (логиПосле ? '&after=' + логиПосле : '');
    const ответ = await fetch(адрес, { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    if (логиЭлементы !== элементы || логиПауза) return;
    const новые = Array.isArray(данные.lines) ? данные.lines.map(String) : [];
    if (Number.isSafeInteger(данные.offset) && данные.offset >= 0) логиOffset = данные.offset;
    const изменились = новые.length !== логиСтроки.length ||
      новые.some((строка, i) => строка !== логиСтроки[i]);
    логиСтроки = новые;
    статусЛогов();
    if (изменились) перерисоватьЛоги();
  } catch (e) {
    if (логиЭлементы === элементы) элементы.статус.textContent = 'Не вышло: ' + e.message;
  } finally {
    логиТянем = false;
    элементы.кнопка.disabled = false;
    элементы.очистить.disabled = логиOffset === 0;
  }
}

function нарисоватьЛоги() {
  логиПауза = false;
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.add('логовой');
  лист.innerHTML = '';

  const логи = document.createElement('div');
  логи.className = 'логи';

  const верх = document.createElement('div');
  верх.className = 'логи-верх';

  const поиск = document.createElement('input');
  поиск.type = 'search';
  поиск.className = 'логи-поиск';
  поиск.placeholder = 'Поиск по журналу…';
  верх.appendChild(поиск);

  const фильтры = document.createElement('div');
  фильтры.className = 'логи-фильтры';
  фильтры.setAttribute('role', 'tablist');
  for (const [код, название] of ЛОГИ_ФИЛЬТРЫ) {
    const кнопкаФильтра = document.createElement('button');
    кнопкаФильтра.type = 'button';
    кнопкаФильтра.dataset.фильтр = код;
    кнопкаФильтра.textContent = название;
    кнопкаФильтра.classList.toggle('активный', код === логиФильтр);
    кнопкаФильтра.addEventListener('click', () => {
      логиФильтр = код;
      try { localStorage.setItem('логиФильтр', код); } catch (e) { /* не страшно */ }
      фильтры.querySelectorAll('button').forEach((б) => б.classList.toggle('активный', б === кнопкаФильтра));
      перерисоватьЛоги(true);
    });
    фильтры.appendChild(кнопкаФильтра);
  }

  const статус = document.createElement('span');
  статус.className = 'логи-статус';
  статус.textContent = '…';
  верх.appendChild(статус);

  const кнопка = document.createElement('button');
  кнопка.type = 'button';
  кнопка.className = 'голос-кнопка главная';
  кнопка.textContent = 'Обновить';
  верх.appendChild(кнопка);

  const пауза = document.createElement('button');
  пауза.type = 'button';
  пауза.className = 'голос-кнопка';
  пауза.textContent = 'Пауза';
  пауза.title = 'Останавливает обновление экрана, но журнал продолжает записываться';
  верх.appendChild(пауза);

  const очистить = document.createElement('button');
  очистить.type = 'button';
  очистить.className = 'голос-кнопка';
  очистить.textContent = 'Очистить экран';
  очистить.title = 'Скроет старые строки только здесь; файл журнала останется';
  очистить.disabled = логиOffset === 0;
  верх.appendChild(очистить);

  const скачать = document.createElement('a');
  скачать.className = 'голос-кнопка логи-скачать';
  скачать.href = '/api/logs/download';
  скачать.download = '';
  скачать.textContent = 'Скачать журнал';
  скачать.title = 'Скачает весь журнал, а не только видимые строки';
  верх.appendChild(скачать);
  логи.appendChild(верх);
  логи.appendChild(фильтры);

  const лента = document.createElement('div');
  лента.className = 'логи-лента';
  логи.appendChild(лента);

  лист.appendChild(логи);
  логиЭлементы = { корень: логи, поиск, фильтры, кнопка, очистить, статус, лента };

  поиск.addEventListener('input', () => перерисоватьЛоги(true));
  кнопка.addEventListener('click', загрузитьЛоги);
  пауза.addEventListener('click', () => {
    логиПауза = !логиПауза;
    пауза.textContent = логиПауза ? 'Продолжить' : 'Пауза';
    статусЛогов();
    if (!логиПауза) загрузитьЛоги();
  });
  очистить.addEventListener('click', () => {
    логиПосле = логиOffset;
    логиСтроки = [];
    перерисоватьЛоги();
    статусЛогов();
  });

  перерисоватьЛоги();
  загрузитьЛоги();
  if (логиТаймер !== null) clearInterval(логиТаймер);
  логиТаймер = setInterval(() => {
    if (текущий === 'логи' && !логиПауза &&
        логиЭлементы?.корень === логи && логи.isConnected) {
      загрузитьЛоги();
    }
  }, 4000);
}

/* ---------- Настройки ---------- */
let настрЭлементы = null;
let настрТянем = false;
let настрБаза = { persona: '', memory: '' };
/* Готовые характеры (`/api/personas`) и выбранный. Выбранный уходит в
   настройки `persona_preset`: от него зависят быстрые фразы без модели —
   «спокойной ночи», «на связи» при запуске. */
let характерыГотовые = [];
let характерВыбранный = 'pizdabol';
let настрОбразцы = [];
let настрПлеер = null;
let настрПробаUrl = null;
let настрТекущийРаздел = 'ответы';
/* --- Звук: микрофон, колонки, живой уровень -------------------------------
   Списки устройств приходят с сервера (`/api/audio`), и у хозяина их
   двадцать три — вручную их не перечислишь. `звукСписки` — ответ целиком;
   `null` значит «списки ещё не пришли», и тогда сохранение их не трогает,
   иначе «Сохранить» ради другой настройки стёр бы выбор микрофона.
   `звукСохранено` — то, что лежит на сервере: по нему проверка понимает,
   свой ли выбор стоит в форме (см. `проверитьЗвук`). */
let звукСписки = null;
let звукСохранено = { mic_name: '', mic_channel: 0, speaker_name: '' };
/* Один таймер на весь раздел: ушёл хозяин с подраздела или в другую вкладку —
   опрос level'а прекращается, иначе он шлёт запросы в пустоту. */
let звукТаймер = null;
/* Железо спрашиваем один раз за открытие формы: оно не меняется, а
   определение занимает до пяти секунд (`nvidia-smi`). */
let железоГрузили = false;

/* Тема оформления. Ставится в `data-theme` на `<html>` — там же сервер
   вписывает его прямо в страницу (core/phone.py), поэтому при запуске
   пульт не мигает тёмным. Здесь то же самое, но для предпросмотра: хозяин
   выбрал «Светлая» — пульт перекрасился сейчас, а записана тема будет
   только по кнопке «Сохранить». Ушёл без неё — при следующей загрузке
   настроек вернётся сохранённая (см. `заполнитьНастройки`). */
const ТЕМЫ = [['dark', 'Тёмная'], ['light', 'Светлая']];
function темуПоставить(значение) {
  document.documentElement.dataset.theme = значение === 'light' ? 'light' : 'dark';
  темаЗначокОбновить();
}
/* Значок темы в шапке. Рисуем то, что будет по нажатию: в тёмной — солнце
   («станет светло»), в светлой — луну. Оба значка currentColor, без зелёного. */
const ТЕМА_ЗНАЧКИ = {
  dark: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v3m0 16v3M4.2 4.2l2.1 2.1m11.4 11.4 2.1 2.1M1 12h3m16 0h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1',
  light: 'M20.5 14.3A8.6 8.6 0 0 1 9.7 3.5a8.6 8.6 0 1 0 10.8 10.8z',
};
function темаЗначокОбновить() {
  const кнопка = $('тема-значок');
  if (!кнопка) return;
  const тёмная = document.documentElement.dataset.theme !== 'light';
  const путь = кнопка.querySelector('path');
  if (путь) путь.setAttribute('d', ТЕМА_ЗНАЧКИ[тёмная ? 'dark' : 'light']);
  const подпись = тёмная ? 'Светлая тема' : 'Тёмная тема';
  кнопка.title = подпись;
  кнопка.setAttribute('aria-label', подпись);
}
/* Тема — не то, что надо подтверждать: переключили значком — пульт перекрасился
   сразу и тема ушла на сервер (`save_settings` сам перешлёт её телефону).
   Если форма настроек открыта, её поле «Тема» показывает то же — без отметки
   «несохранённое»: она и правда сохранена. Сервер не принял — возвращаем
   прежнюю и говорим почему. */
async function темаЗначокПереключить() {
  const кнопка = $('тема-значок');
  if (!кнопка || кнопка.disabled) return;
  const прежняя = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  const новая = прежняя === 'light' ? 'dark' : 'light';
  темуПоставить(новая);
  if (настрЭлементы && настрЭлементы.theme) настрЭлементы.theme.value = новая;
  кнопка.disabled = true;
  try {
    const ответ = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme: новая }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    темаПодпись('');
  } catch (e) {
    темуПоставить(прежняя);
    if (настрЭлементы && настрЭлементы.theme) настрЭлементы.theme.value = прежняя;
    темаПодпись('Тема не сохранена: ' + ((e && e.message) ? e.message : e), true);
  }
  if ($('тема-значок') === кнопка) кнопка.disabled = false;
}
let темаПодписьТаймер = null;
function темаПодпись(текст, плохо) {
  const узел = $('тема-подпись');
  if (!узел) return;
  узел.textContent = текст;
  узел.classList.toggle('плохо', !!плохо);
  clearTimeout(темаПодписьТаймер);
  if (текст) {
    темаПодписьТаймер = setTimeout(() => {
      узел.textContent = ''; узел.classList.remove('плохо');
    }, 4000);
  }
}

const НАСТР_РАЗДЕЛЫ = [
  ['ответы', 'Ответы и подключение', 'Модель, ключ доступа и длина ответов'],
  ['поиск', 'Поиск', 'Поиск в интернете: где искать и сколько ждать'],
  ['первой', 'Сама заговаривает', 'Когда Труба начинает разговор сама и смотрит ли при этом на экран'],
  ['характер', 'Характер', 'Как Труба ведёт себя в разговоре'],
  ['память', 'Память', 'Что Труба помнит о тебе'],
  ['голос', 'Озвучивание', 'Как звучат ответы'],
  ['слух', 'Слух', 'Когда и кого Труба слушает'],
  ['распознавание', 'Распознавание', 'Как Труба разбирает твою речь'],
  ['звук', 'Звук', 'Откуда Труба слышит и куда говорит'],
  ['телефон', 'Телефон', 'Куда передавать звук и как подключиться'],
  ['система', 'Система', 'Запуск Трубы вместе с Windows'],
];

/* Настройки — одна форма с одной кнопкой «Сохранить», но показывается на
   двух страницах: озвучивание и слух живут в «Голосе», остальное — в
   «Настройках». Все секции при этом остаются в форме (скрытыми), поэтому
   сохранение отправляет полный набор, как и раньше. */
const НАСТР_СТРАНИЦЫ = {
  настройки: ['ответы', 'поиск', 'первой', 'характер', 'память', 'телефон', 'система'],
  голос: ['голос', 'слух', 'распознавание', 'звук'],
};
let настрСтраница = 'настройки';
let голосПодраздел = 'голос';

/* Видимые подписи настроек: ключ API не меняем, меняем только текст на экране. */
const НАСТР_НАЗВАНИЯ = {
  provider: ['Сервис ответов', 'Где Труба получает ответ'],
  model: ['Модель', 'Выбери из списка или впиши точный ID'],
  api_key: ['Ключ доступа', ''],
  temperature: ['Свобода ответа', 'Ниже — точнее, выше — разнообразнее'],
  max_tokens: ['Максимум текста', 'Предел длины одного ответа'],
  history_turns: ['Контекст разговора', 'Сколько последних реплик учитывает'],
  web_search: ['Поиск в интернете', 'Сама ищет свежее: новости, цены, курсы, погоду. Каждый поиск виден в Логах'],
  search_sound: ['Звук во время поиска', 'Тихий фон, пока она ищет в интернете. Без него — тишина до ответа'],
  web_search_mode: ['Где искать', 'Бесплатно — Yahoo, Brave, DuckDuckGo, Яндекс и Bing, иногда отказывают минут на десять. Платно — поиск OpenAI, надёжнее'],
  web_search_budget: ['Ждать поиск, с', 'Сколько секунд она может искать, прежде чем ответить тем, что нашла'],
  proactive: ['Заговаривать первой', 'Только когда ты за компом, не в созвоне Discord и не заглушил её кругом. Каждый заход — запрос в облако'],
  proactive_look: ['Иногда смотреть на экран', 'Снимок уходит в облачную модель. Личное на экране она не описывает'],
  hedge: ['Страховка от заминок облака', 'если основная модель молчит дольше 2.5 с, тот же вопрос уходит запасной; отвечает первая. Стоит копейки, но при заминке платишь за оба'],
  tts_engine: ['Способ озвучивания', 'Чем озвучивает ответы. Сколько качать и сколько займёт на видеокарте — в «Качественных голосах» выше'],
  silero_speaker: ['Диктор Silero', 'Чьим голосом говорит'],
  silero_model: ['Файл голоса Silero', 'Имя файла модели диктора'],
  /* Образец голоса — это файл записи .wav или .mp3, а не «пресет модели»:
     ESpeech и Higgs копируют тембр по такой записи. В публичную установку
     образцы не входят (это голоса живых людей), поэтому пустой список — это
     не поломка, а обычное состояние, и добавляет запись сам хозяин. */
  voice_name: ['Образец голоса',
    'Запись .wav или .mp3, чей голос копирует ESpeech или Higgs. В установку не входит — добавь свою запись ниже'],
  tts_speed: ['Скорость речи', 'Выше — говорит быстрее'],
  tts_nfe: ['Качество голоса ESpeech', 'Больше — чище, но медленнее'],
  tts_gap: ['Пауза между фразами', 'Длина тишины в секундах'],
  voice_volume: ['Громкость голоса', 'Только её голос — на телефоне и в колонках. 10 — как сейчас, громче не делаем: захрипит'],
  higgs_gentle: ['Бережно к видеокарте', 'Higgs готовит речь чуть впереди звука, а не вдвое быстрее — меньше лагов в играх'],
  duck_level: ['Приглушение фона', 'Как тихо становится остальное, пока говорит'],
  barge_in_level: ['Порог перебивания', 'Ниже — легче перебить её голосом'],
  barge_instant: ['Перебивать сразу', 'Замолкает, как только ты начинаешь говорить поверх неё. Выключи, если она сама себя обрывает'],
  listen_mode: ['Когда слушает', 'Всегда, по имени или выкл'],
  follow_up_window: ['Окно разговора', 'Сколько секунд после реплики ждёт продолжения'],
  require_name_when_noisy: ['Имя при шуме', 'В шуме отзывается только на имя'],
  voice_app_guard: ['Не отвечать без имени, пока говорят в Discord, Telegram или браузере', 'фразы, сказанные, пока звучит звонок или видео, без её имени пропускаются'],
  owner_only: ['Только хозяин', 'Слушается лишь знакомый голос'],
  voice_autostart: ['Включать при запуске', 'Голос включается сам, как только открыт пульт'],
  owner_threshold: ['Строгость узнавания', 'Выше — придирчивее к голосу'],
  stt_model: ['Модель распознавания', 'Какая модель разбирает твою речь. Первый запуск новой качает её с Hugging Face — это надолго'],
  stt_quantization: ['Сжатие модели', 'Сжатая — быстрее; полная — точнее, но медленнее'],
  output: ['Куда её голос', 'Колонки или телефон'],
  mic_name: ['Микрофон', 'Откуда Труба тебя слышит'],
  mic_channel: ['Вход', 'У звуковых карт с двумя входами микрофон бывает во втором. Обычному микрофону — «Вход 1»'],
  speaker_name: ['Колонки', 'Куда говорить, когда выбраны колонки. Здесь же звучит проверка'],
  уровень: ['Уровень', 'Показывает микрофон, который слушает сейчас. Выбрала другой — сохрани, и он заработает после перезапуска голоса'],
  weather_city: ['Город', 'Город для погоды на телефоне'],
  autostart: ['Запускать Трубу вместе с Windows', 'При входе в Windows пульт стартует свёрнутым в трей, голос — если включено «Включать при запуске»'],
  theme: ['Тема', 'Тёмная или светлая. Перекрашивает пульт и экран телефона сразу, записывается кнопкой «Сохранить»'],
  phone_address: ['Адрес для телефона', 'Открой в браузере телефона в той же сети Wi-Fi'],
  local_url: ['Адрес локального сервера', 'Куда ходить за ответом. Кнопки под полем вписывают готовый адрес'],
  update_check: ['Проверять обновления при запуске', 'Один поход в интернет при открытии пульта. Вышла новая — появится точка у «Настроек»'],
};

/* Локальный сервер: с Трубой он не проверялся, и хозяин должен видеть это
   до того, как услышит первый ответ, а не после. Текст один и тот же —
   и в этом блоке, и в подписи сервиса в списке. */
const ЛОКАЛЬНЫЙ_СЕРВИС = 'local';
/* Подписи сервисов — и в списке формы, и в шаге «Мозг» мастера первого
   запуска: два места с разными словами про один сервис путают. */
const СЕРВИС_НАЗВАНИЯ = {
  deepseek: 'DeepSeek', openrouter: 'OpenRouter', minimax: 'MiniMax', openai: 'OpenAI',
  local: 'Локальная (не проверялась)',
};
/* Как взять ключ — по одной строке на сервис. Хозяину нужен не адрес, а
   порядок действий: зарегистрироваться, пополнить баланс, создать ключ. */
const СЕРВИС_ПОДСКАЗКИ = {
  deepseek: 'Зарегистрируйся, пополни баланс, раздел API keys → Create new API key',
  openrouter: 'Keys → Create key; есть бесплатные модели',
  minimax: 'API Keys → Create new secret key',
  openai: 'API keys → Create new secret key; из России работает только через VPN',
};
const ЛОКАЛЬНАЯ_ПРЕДУПРЕЖДЕНИЕ =
  'Локальная модель с Трубой не проверялась — работать может по-разному. ' +
  'Запуск программ, YouTube, заметки, снимок экрана и поиск в интернете ' +
  'работают, только если модель умеет вызывать инструменты (function calling). ' +
  '«Глянь на экран» — только если модель понимает картинки. Память и заметки ' +
  'ждут от неё аккуратный JSON — слабая модель может путаться. Ответы бывают ' +
  'медленнее: видеокарту делят модель, голос Higgs и игры. Если локальный сервер ' +
  'не отвечает, а у облачного сервиса есть ключ, ответит облачный — это платно, ' +
  'строка об этом будет в Логах.';
/* Готовые адреса: хозяин не работает с командной строкой, поэтому три
   кнопки подсказывают ровно то, что копируют в поле. */
const ЛОКАЛЬНЫЕ_АДРЕСА = [
  ['Ollama', 'http://127.0.0.1:11434/v1'],
  ['LM Studio', 'http://127.0.0.1:1234/v1'],
  ['llama.cpp', 'http://127.0.0.1:8080/v1'],
];
function настрПоле(строка, подпись, узел, намёк = '') {
  const ряд = document.createElement('label');
  ряд.className = 'настр-ряд';
  const имя = document.createElement('span');
  имя.className = 'настр-имя';
  const пара = НАСТР_НАЗВАНИЯ[подпись];
  имя.textContent = пара ? пара[0] : подпись;
  ряд.appendChild(имя);
  ряд.appendChild(узел);
  const текстНамёка = намёк || (пара && пара[1]) || '';
  if (текстНамёка) {
    const намёкУзел = document.createElement('span');
    намёкУзел.className = 'настр-намёк';
    намёкУзел.textContent = текстНамёка;
    ряд.appendChild(намёкУзел);
  }
  строка.appendChild(ряд);
  return узел;
}
function настрВвод(значение) {
  const ввод = document.createElement('input');
  ввод.type = 'text';
  ввод.className = 'настр-ввод';
  ввод.value = значение === null || значение === undefined ? '' : String(значение);
  return ввод;
}
function настрЧислоПоле(значение) {
  const ввод = document.createElement('input');
  ввод.type = 'number';
  ввод.className = 'настр-ввод настр-число';
  ввод.value = значение === null || значение === undefined ? '' : String(значение);
  return ввод;
}
function настрПолзунок(поле, min, max, step) {
  const ряд = поле.closest('.настр-ряд');
  ряд.classList.add('с-ползунком');
  const шкала = document.createElement('input');
  шкала.type = 'range';
  шкала.className = 'настр-ползунок';
  шкала.min = String(min); шкала.max = String(max); шкала.step = String(step);
  шкала.setAttribute('aria-label', ряд.querySelector('.настр-имя')?.textContent || 'Значение');
  const синх = () => {
    const число = Number(поле.value.replace(',', '.'));
    if (Number.isFinite(число)) шкала.value = String(Math.max(min, Math.min(max, число)));
  };
  шкала.addEventListener('input', () => { поле.value = шкала.value; });
  поле.addEventListener('input', синх);
  ряд.insertBefore(шкала, ряд.querySelector('.настр-намёк'));
  поле.синхПолзунок = синх;
  синх();
}
function настрВыбор(варианты, значение) {
  const выбор = document.createElement('select');
  выбор.className = 'настр-выбор';
  for (const [код, название] of варианты) {
    const пункт = document.createElement('option');
    пункт.value = код;
    пункт.textContent = название;
    выбор.appendChild(пункт);
  }
  выбор.value = значение;
  if (выбор.selectedIndex < 0 && выбор.options.length > 0) выбор.selectedIndex = 0;
  return выбор;
}
function настрГалочка(включено) {
  const ввод = document.createElement('input');
  ввод.type = 'checkbox';
  ввод.className = 'настр-галочка';
  ввод.checked = !!включено;
  return ввод;
}
function настрСекция(корень, код, заголовок) {
  const секция = document.createElement('section');
  секция.className = 'настр-секция';
  секция.dataset.sectionId = код;
  секция.setAttribute('aria-label', заголовок);
  корень.appendChild(секция);
  return секция;
}
function настрСтатус(текст, плохо) {
  if (!настрЭлементы) return;
  настрЭлементы.статус.textContent = текст;
  настрЭлементы.статус.classList.toggle('плохо', !!плохо);
}
function настрЧислоИли(узел, ключ, целое, ошибки) {
  const сырое = узел.value.trim().replace(',', '.');
  const ч = Number(сырое);
  if (сырое === '' || !Number.isFinite(ч) || (целое && !Number.isInteger(ч))) {
    const подпись = НАСТР_НАЗВАНИЯ[ключ]?.[0] || ключ;
    ошибки.push({ ключ, текст: подпись + ': введи ' + (целое ? 'целое число' : 'число') });
    return undefined;
  }
  return ч;
}

function настрПоказатьРаздел(код) {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const свои = НАСТР_СТРАНИЦЫ[настрСтраница];
  const выбран = свои.includes(код) ? код : свои[0];
  if (настрСтраница === 'настройки') настрТекущийРаздел = выбран;
  else голосПодраздел = выбран;
  эл.раздел.value = выбран;
  эл.пояснение.textContent = НАСТР_РАЗДЕЛЫ.find(([id]) => id === выбран)[2];
  for (const секция of эл.секции) секция.hidden = секция.dataset.sectionId !== выбран;
  /* Кнопка проверки — одна на раздел. У «Ответов» — связь с моделью, у
     «Поиска» — сам поиск, у «Озвучивания» — проба голоса; в остальных
     разделах проверять нечего, и кнопки просто не видны. */
  эл.проверитьСвязь.hidden = выбран !== 'ответы';
  эл.проверитьПоиск.hidden = выбран !== 'поиск';
  эл.послушатьПробу.hidden = выбран !== 'голос';
  document.querySelectorAll('#подменю-настроек [data-настройка]').forEach((кнопка) => {
    кнопка.classList.toggle('активный', настрСтраница === 'настройки' && кнопка.dataset.настройка === выбран);
  });
  document.querySelectorAll('#подменю-голоса [data-голос]').forEach((кнопка) => {
    кнопка.classList.toggle('активный', настрСтраница === 'голос' && кнопка.dataset.голос === выбран);
  });
  /* «Распознавание» — раздел, где нужны сведения с сервера: какая модель
     реально разбирает речь. «Звук»
     спрашивает список устройств и запускает полоску уровня, а «Озвучивание»
     — железо (один раз за форму, см. `железоГрузили`). */
  if (выбран === 'распознавание') загрузитьРаспознавание();
  if (выбран === 'звук') { загрузитьЗвук(эл); звукУровеньВключить(эл); }
  else звукУровеньВыключить();
  if (выбран === 'голос') загрузитьЖелезо();
}

function настрПоказатьПоле(узел) {
  const секция = узел?.closest('.настр-секция');
  if (секция && !НАСТР_СТРАНИЦЫ[настрСтраница].includes(секция.dataset.sectionId)) {
    настрСтатус('Поправь поле в разделе «' +
      (НАСТР_РАЗДЕЛЫ.find(([id]) => id === секция.dataset.sectionId)?.[1] || '') + '»', true);
    return;
  }
  if (секция) настрПоказатьРаздел(секция.dataset.sectionId);
  if (узел?.focus) узел.focus();
}

function настрПоляОзвучивания() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const espeech = эл.tts_engine.value === 'espeech';
  const образец = espeech || эл.tts_engine.value === 'higgs';
  эл.образецБлок.hidden = !образец;
  for (const поле of [эл.silero_speaker, эл.silero_model]) {
    поле.closest('.настр-ряд').hidden = образец;
  }
  эл.tts_nfe.closest('.настр-ряд').hidden = !espeech;
}
async function загрузитьНастройки() {
  if (!настрЭлементы || настрТянем) return;
  настрТянем = true;
  настрСтатус('Загружаю…', false);
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && (данные.error || (данные.errors || []).join('; '))) || ('сервер ответил ' + ответ.status));
    }
    заполнитьНастройки(данные);
    настрСтатус('Готово', false);
    настрЗагрузитьМодели();
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
}

/* Ключ доступа. Хозяин хочет видеть, что ключ реально стоит, поэтому при
   сохранённом ключе в поле кладём сам ключ (точками, поле password), а не
   серые кружочки-заглушку. Введённое хозяином показываем как есть. */
async function настрПоказатьКлюч() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const имя = эл.provider.value;
  const введённый = эл.ключиВПравке[имя];
  if (введённый !== undefined) {
    эл.api_key.value = введённый;
    return;
  }
  // Уже показывали: второй раз сервер не спрашиваем.
  if (эл.сохранённыеКлючи[имя] !== undefined) {
    эл.api_key.value = эл.сохранённыеКлючи[имя];
    return;
  }
  if (!эл.провайдеры?.[имя]?.has_key) {
    эл.api_key.value = '';
    return;
  }
  try {
    const ответ = await fetch('/api/settings/key/reveal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
      body: JSON.stringify({ provider: имя }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    // Пока летел запрос, сервис могли сменить — такой ответ уже не наш.
    if (настрЭлементы !== эл || эл.provider.value !== имя) return;
    const ключ = данные.key || '';
    эл.сохранённыеКлючи[имя] = ключ;
    // Подстановка не трогает `ключиВПравке` и не шлёт `input`: ключ сохранён,
    // хозяин его не вводил, и «Сохранить» не должен слать его второй раз.
    эл.api_key.value = ключ;
  } catch (e) {
    if (настрЭлементы === эл && эл.provider.value === имя) {
      эл.api_key.value = '';
      настрСтатус('Не удалось показать ключ: ' + e.message, true);
    }
  }
}

function настрПодсказкаКлюча() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const сохранён = !!эл.провайдеры?.[эл.provider.value]?.has_key;
  // Заглушки-точек больше нет: при сохранённом ключе в поле сам ключ.
  эл.api_key.placeholder = 'Вставь ключ API';
  эл.ключНамёк.textContent = сохранён
    ? 'Ключ сохранён'
    : 'Ключа нет — вставь и сохрани';
}

function настрПоказатьМодель() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const имя = эл.provider.value;
  const данные = эл.провайдеры?.[имя] || {};
  const текущее = эл.моделиВПравке[имя] || данные.model || '';
  эл.model.value = текущее;
  эл.списокМоделей.replaceChildren();
  эл.моделиСтатус.textContent = 'Список моделей ещё не загружен';
}

/* Локальный сервер — отдельный случай: адрес и предупреждение нужны
   только при нём, ключ, наоборот, не нужен. */
function настрПоказатьЛокальное() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const локальный = эл.provider.value === ЛОКАЛЬНЫЙ_СЕРВИС;
  эл.локальноеПредупреждение.hidden = !локальный;
  эл.local_urlРяд.hidden = !локальный;
  эл.рядКлюча.hidden = локальный;
  // У локального сервера моделей нет в нашем списке: они приходят с него.
  эл.моделиСтатус.textContent = локальный
    ? 'Список моделей берётся у локального сервера: обнови его или впиши ID вручную'
    : 'Список моделей ещё не загружен';
}

function настрТекущаяМодель() {
  if (!настрЭлементы) return '';
  const эл = настрЭлементы;
  return эл.model.value.trim();
}

async function настрЗагрузитьМодели() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const имя = эл.provider.value;
  const локальный = имя === ЛОКАЛЬНЫЙ_СЕРВИС;
  const номер = ++эл.номерЗагрузкиМоделей;
  эл.моделиСтатус.textContent = 'Обновляю список…';
  эл.обновитьМодели.disabled = true;
  try {
    const ответ = await fetch('/api/settings/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
      body: JSON.stringify({ provider: имя, key: эл.api_key.value.trim() }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    if (настрЭлементы !== эл || номер !== эл.номерЗагрузкиМоделей || эл.provider.value !== имя) return;
    эл.списокМоделей.replaceChildren();
    for (const id of данные.models || []) {
      const пункт = document.createElement('option');
      пункт.value = id;
      эл.списокМоделей.appendChild(пункт);
    }
    const выбранная = эл.model.value.trim();
    эл.моделиСтатус.textContent = выбранная && !(данные.models || []).includes(выбранная)
      ? `Моделей: ${(данные.models || []).length} · текущей нет в каталоге, проверь ID`
      : `Моделей: ${(данные.models || []).length} · можно вписать любой ID`;
  } catch (e) {
    if (настрЭлементы === эл && номер === эл.номерЗагрузкиМоделей) {
      // У локального сервера ключа нет — значит, и винить его не за что:
      // сервер либо выключен, либо адрес вписан не тот.
      эл.моделиСтатус.textContent = локальный
        ? 'Локальный сервер не отвечает по адресу ' + (эл.local_url.value.trim() || '—')
          + ' · запусти Ollama / LM Studio или впиши ID вручную'
        : 'Список недоступен · впиши ID вручную или проверь ключ';
    }
  } finally {
    if (настрЭлементы === эл && номер === эл.номерЗагрузкиМоделей) эл.обновитьМодели.disabled = false;
  }
}

/* «Отменить правки» неактивна, пока поле равно сохранённому. Оживает
   сама, как только хозяин что-то ввёл. */
function настрОбновитьОтмену() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  for (const ключ of ['persona', 'memory']) {
    const кнопка = ключ === 'persona' ? эл.отменитьХарактер : эл.отменитьПамять;
    if (!кнопка) continue;
    кнопка.disabled = эл[ключ].value === настрБаза[ключ];
  }
}

async function настрВернутьТекст(ключ) {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const поле = эл[ключ];
  const название = ключ === 'persona' ? 'характере' : 'памяти';
  if (поле.value !== настрБаза[ключ] &&
      !window.confirm('Отменить несохранённые правки в ' + названии + '?')) return;
  настрТянем = true;
  настрСтатус('Отменяю…', false);
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    if (настрЭлементы !== эл) return;
    поле.value = typeof данные[ключ] === 'string' ? данные[ключ] : '';
    настрБаза[ключ] = поле.value;
    if (ключ === 'persona') {
      /* Вернулся текст — вернулся и сохранённый готовый характер. */
      характерВыбранный = (данные.settings && данные.settings.persona_preset) || 'pizdabol';
      характерОтметить();
    }
    настрОбновитьОтмену();
    настрСтатус('Правки отменены', false);
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  finally { настрТянем = false; }
}
function заполнитьНастройки(данные) {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const s = (данные && данные.settings) || {};
  const провайдеры = (данные && данные.providers) || {};
  эл.провайдеры = провайдеры;
  эл.моделиВПравке = Object.fromEntries(
    Object.entries(провайдеры).map(([имя, запись]) => [имя, запись.model || ''])
  );
  эл.provider.innerHTML = '';
  for (const имя of Object.keys(провайдеры)) {
    const пункт = document.createElement('option');
    пункт.value = имя;
    пункт.textContent = СЕРВИС_НАЗВАНИЯ[имя] || имя;
    эл.provider.appendChild(пункт);
  }
  if (данные && typeof данные.provider === 'string' && провайдеры[данные.provider]) эл.provider.value = данные.provider;
  if (эл.provider.selectedIndex < 0 && эл.provider.options.length > 0) эл.provider.selectedIndex = 0;
  настрПоказатьМодель();
  настрПоказатьЛокальное();
  настрПодсказкаКлюча();
  эл.local_url.value = typeof s.local_url === 'string' ? s.local_url : '';
  // Ключ подставляет сама настрПоказатьКлюч: введённый хозяином или тот,
  // что сохранён на сервере. Это запрос, поэтому поле заполнится сразу
  // после загрузки, а не строчкой выше.
  настрПоказатьКлюч();
  эл.temperature.value = s.temperature ?? '';
  эл.max_tokens.value = s.max_tokens ?? '';
  эл.history_turns.value = s.history_turns ?? '';
  эл.web_search.checked = s.web_search !== false;
  эл.search_sound.checked = s.search_sound !== false;
  эл.web_search_mode.value = s.web_search_mode ?? 'free';
  if (эл.web_search_mode.selectedIndex < 0) эл.web_search_mode.selectedIndex = 0;
  эл.web_search_budget.value = s.web_search_budget ?? 12;
  эл.web_search_budget.синхПолзунок?.();
  настрПоляПоиска();
  эл.persona.value = typeof данные.persona === 'string' ? данные.persona : '';
  эл.memory.value = typeof данные.memory === 'string' ? данные.memory : '';
  настрБаза = { persona: эл.persona.value, memory: эл.memory.value };
  характерВыбранный = s.persona_preset || 'pizdabol';
  характерОтметить();
  настрОбновитьОтмену();
  эл.tts_engine.value = s.tts_engine ?? 'silero';
  if (эл.tts_engine.selectedIndex < 0) эл.tts_engine.selectedIndex = 0;
  настрПоляОзвучивания();
  эл.silero_speaker.value = s.silero_speaker ?? '';
  эл.silero_model.value = s.silero_model ?? '';
  const голоса = Array.isArray(данные.voices) ? данные.voices.map(String) : [];
  const текущее = s.voice_name ? String(s.voice_name) : '';
  const все = голоса.includes(текущее) || текущее === '' ? голоса : [текущее].concat(голоса);
  эл.voice_name.innerHTML = '';
  if (все.length === 0) {
    const пункт = document.createElement('option');
    пункт.value = '';
    /* Пустой список — обычное состояние чистой установки: образцы не входят
       в выпуск (это голоса живых людей). Пишем это прямо здесь, а не молча
       оставляем прочерк, который выглядит как поломка. */
    пункт.textContent = 'образцов нет — добавь запись .wav или .mp3 ниже';
    эл.voice_name.appendChild(пункт);
  } else {
    for (const г of все) {
      const пункт = document.createElement('option');
      пункт.value = г;
      пункт.textContent = г === '' ? '—' : г;
      эл.voice_name.appendChild(пункт);
    }
  }
  эл.voice_name.value = текущее;
  if (эл.voice_name.selectedIndex < 0) эл.voice_name.selectedIndex = 0;
  настрОбразцы = Array.isArray(данные.voice_samples) ? данные.voice_samples : [];
  показатьОбразец();
  эл.tts_speed.value = s.tts_speed ?? '';
  эл.tts_nfe.value = s.tts_nfe ?? '';
  эл.tts_gap.value = s.tts_gap ?? '';
  эл.voice_volume.value = s.voice_volume ?? '';
  эл.higgs_gentle.checked = s.higgs_gentle !== false;
  эл.duck_level.value = s.duck_level ?? '';
  эл.barge_in_level.value = s.barge_in_level ?? '';
  эл.follow_up_window.value = s.follow_up_window ?? '';
  for (const поле of [эл.temperature, эл.history_turns, эл.tts_speed, эл.tts_nfe,
    эл.tts_gap, эл.voice_volume, эл.duck_level, эл.barge_in_level,
    эл.owner_threshold,
    эл.follow_up_window]) поле.синхПолзунок?.();
  эл.hedge.checked = !!s.hedge;
  эл.proactive.value = s.proactive ?? 'never';
  if (эл.proactive.selectedIndex < 0) эл.proactive.selectedIndex = 0;
  эл.proactive_look.checked = s.proactive_look !== false;
  // Галочка гаснет при «никогда»: смотреть на экран незачем, если смотреть
  // некогда.
  эл.proactive_look.disabled = эл.proactive.value === 'never';
  эл.require_name_when_noisy.checked = !!s.require_name_when_noisy;
  // По умолчанию включено: без этого перебивания не было вовсе.
  эл.barge_instant.checked = s.barge_instant !== false;
  эл.voice_app_guard.checked = s.voice_app_guard !== false;
  эл.owner_only.checked = !!s.owner_only;
  эл.voice_autostart.checked = s.voice_autostart !== false;
  эл.owner_only.disabled = !данные.owner_known;
  эл.owner_threshold.value = s.owner_threshold ?? '';
  эл.output.value = s.output ?? 'speakers';
  if (эл.output.selectedIndex < 0) эл.output.selectedIndex = 0;
  // Галочка показывает факт: есть ли ярлык в папке автозагрузки, а не
  // значение из settings.json.
  эл.autostart.checked = !!данные.autostart;
  /* Тема — сохранённая, а не выбранная в форме: ушёл без «Сохранить» —
     пульт вернулся к тому, что записано. Так и обещано в подписи поля. */
  эл.theme.value = s.theme === 'light' ? 'light' : 'dark';
  if (эл.theme.selectedIndex < 0) эл.theme.selectedIndex = 0;
  темуПоставить(эл.theme.value);
  телефонКлюч = typeof данные.phone_key === 'string' ? данные.phone_key : '';
  запомнитьПортТелефона(данные);
  const адреса = Array.isArray(данные.addresses) ? данные.addresses.map(String) : [];
  const ссылки = адреса.map(настрАдресТелефона).filter(Boolean);
  const выбранный = эл.телАдрес.value;
  эл.телАдрес.innerHTML = '';
  for (const ссылка of ссылки) {
    const пункт = document.createElement('option');
    пункт.value = ссылка;
    пункт.textContent = ссылка;
    эл.телАдрес.appendChild(пункт);
  }
  if (ссылки.includes(выбранный)) эл.телАдрес.value = выбранный;
  if (эл.неГаситьОбновить) эл.неГаситьОбновить();
  if (эл.телКод) настрПоказатьКод(эл.телАдрес, эл.телКод);
  эл.адреса.textContent = ссылки.length === 0
    ? 'Адрес не найден — проверь подключение компьютера к сети'
    : 'После открытия страницы на телефоне коснись экрана, чтобы разрешить воспроизведение звука.';
  эл.телКопировать.disabled = !эл.телАдрес.value;
  эл.телСтатус.textContent = ссылки.length > 1 ? 'Если первый адрес не открывается, попробуй другой' : '';
  elтелСтатусПлохо(эл.телСтатус, false);
  эл.владелец.textContent = данные.owner_known
    ? 'Голос хозяина записан — можно включить отбор по голосу'
    : 'Сначала запиши свой голос — кнопка ниже, иначе отбор не заработает';
  // Записанный образец виден как «Записать заново»: переписывать хороший
  // голос незачем, а вот испорченную запись — да.
  эл.хозяинЗаписать.textContent = данные.owner_known ? 'Записать заново' : 'Записать мой голос';
  // Список моделей приходит отдельно (`/api/stt`) и догружается позже, а
  // значения из снимка ставим сразу: иначе первый выбор был бы пустым.
  эл.stt_quantization.value = s.stt_quantization === 'none' ? 'none' : 'int8';
  if (эл.stt_quantization.selectedIndex < 0) эл.stt_quantization.selectedIndex = 0;
  if (typeof s.stt_model === 'string' && s.stt_model) эл.stt_model.value = s.stt_model;
  /* Звук: запоминаем сохранённое — по нему проверка понимает, свой ли выбор
     стоит в форме, и так решается, отправлять ли `mic_name` вообще. Списки
     приходят отдельно (`/api/audio`) и догружаются позже, а значения из
     снимка ставим сразу: иначе первый выбор был бы пустым. */
  звукСохранено = {
    mic_name: typeof s.mic_name === 'string' ? s.mic_name : '',
    mic_channel: число(s.mic_channel) || 0,
    speaker_name: typeof s.speaker_name === 'string' ? s.speaker_name : '',
  };
  if (звукСписки) {
    звукЗаполнить(эл.mic_name, звукСписки.inputs, звукСохранено.mic_name);
    звукЗаполнить(эл.speaker_name, звукСписки.outputs, звукСохранено.speaker_name);
    звукПоказатьВход(эл);
  }
  /* Погода: город и координаты помнятся, пока хозяин не выберет другой. */
  эл.погГородСохранено = typeof s.weather_city === 'string' ? s.weather_city : '';
  эл.погШирота = число(s.weather_lat);
  эл.погДолгота = число(s.weather_lon);
  эл.погЧерновик = null;
  погодаЗаполнить(эл);
  загрузитьРаспознавание();
  if (выбранРаздел() === 'звук') загрузитьЗвук(эл);
}

/* Какой подраздел сейчас показан: на странице «Голоса» это подраздел голоса,
   на «Настройках» — раздел настроек. Оба живут в одной форме. */
function выбранРаздел() {
  return настрСтраница === 'голос' ? голосПодраздел : настрТекущийРаздел;
}

/* Ключ привязки телефона (`/api/settings` → `phone_key`). Входит в ссылку и
   QR-код: без него комп не пускает телефон из сети (29.09). Телефон
   запоминает ключ при первом открытии ссылки — дальше он ему не нужен. */
let телефонКлюч = '';

/* Порт этого пульта (`/api/settings` → `phone_port`). У двух копий Трубы на
   одном компьютере порты разные, и адрес телефона должен вести к той копии,
   чьё окно открыто. Пока значение не пришло (или страница открыта не с
   пульта) — берём порт самой страницы, а не привычные 8765. */
let телефонПорт = '';

/* Запомнить порт пульта из `/api/settings`. Мусор и не число — молча: адрес
   соберётся по порту страницы, и это всё равно верно, ведь страница
   отдана тем же пультом. */
function запомнитьПортТелефона(данные) {
  const п = Number(данные && данные.phone_port);
  телефонПорт = Number.isInteger(п) && п > 0 ? String(п) : '';
}

function портТелефона() {
  const п = Number(телефонПорт);
  if (Number.isInteger(п) && п > 0) return String(п);
  return location.port || '';
}

function настрАдресТелефона(адрес) {
  const с = String(адрес === undefined || адрес === null ? '' : адрес).trim();
  let ссылка = '';
  if (/^https?:\/\/[^\s]+$/i.test(с)) ссылка = с;
  else if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(с)) {
    ссылка = 'http://' + с + ':' + портТелефона();
  }
  if (!ссылка) return '';
  if (!телефонКлюч || ссылка.includes('?')) return ссылка;
  return ссылка.replace(/\/?$/, '/') + '?k=' + encodeURIComponent(телефонКлюч);
}

function elтелСтатусПлохо(узел, плохо) {
  if (узел && узел.classList) узел.classList.toggle('плохо', !!плохо);
}

async function скопироватьАдресТелефона() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const ссылка = эл.телАдрес.value;
  const статус = эл.телСтатус;
  const показать = (текст, плохо) => {
    if (статус) { статус.textContent = текст; elтелСтатусПлохо(статус, плохо); }
    else настрСтатус(текст, плохо);
  };
  if (!ссылка) { показать('Нет адреса для копирования', true); return; }
  показать('Копирую…', false);
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(ссылка);
    } else {
      const поле = document.createElement('textarea');
      поле.value = ссылка;
      поле.setAttribute('readonly', '');
      поле.style.position = 'fixed';
      поле.style.opacity = '0';
      document.body.appendChild(поле);
      поле.select();
      const вышло = document.execCommand('copy');
      поле.remove();
      if (!вышло) throw new Error('буфер обмена недоступен');
    }
    показать('Адрес скопирован', false);
  } catch (e) { показать('Не вышло скопировать: ' + (e && e.message ? e.message : e), true); }
}


async function сохранитьНастройки() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const ошибки = [];
  const тело = {
    provider: эл.provider.value,
    model: настрТекущаяМодель(),
    // Пустой адрес не шлём вовсе: иначе сохранение любой другой настройки
    // стёрло бы адрес, который хозяин когда-то вписал.
    local_url: эл.local_url.value.trim() || undefined,
    temperature: настрЧислоИли(эл.temperature, 'temperature', false, ошибки),
    max_tokens: настрЧислоИли(эл.max_tokens, 'max_tokens', true, ошибки),
    history_turns: настрЧислоИли(эл.history_turns, 'history_turns', true, ошибки),
    web_search: эл.web_search.checked,
    search_sound: эл.search_sound.checked,
    web_search_mode: эл.web_search_mode.value,
    web_search_budget: настрЧислоИли(эл.web_search_budget, 'web_search_budget', false, ошибки),
    hedge: эл.hedge.checked,
    proactive: эл.proactive.value,
    proactive_look: эл.proactive_look.checked,
    persona: эл.persona.value,
    persona_preset: характерВыбранный,
    memory: эл.memory.value,
    tts_engine: эл.tts_engine.value,
    silero_speaker: эл.silero_speaker.value.trim(),
    silero_model: эл.silero_model.value.trim(),
    voice_name: эл.voice_name.value,
    tts_speed: настрЧислоИли(эл.tts_speed, 'tts_speed', false, ошибки),
    tts_nfe: эл.tts_engine.value === 'espeech'
      ? настрЧислоИли(эл.tts_nfe, 'tts_nfe', true, ошибки) : undefined,
    tts_gap: настрЧислоИли(эл.tts_gap, 'tts_gap', false, ошибки),
    voice_volume: настрЧислоИли(эл.voice_volume, 'voice_volume', true, ошибки),
    higgs_gentle: эл.higgs_gentle.checked,
    duck_level: настрЧислоИли(эл.duck_level, 'duck_level', false, ошибки),
    barge_in_level: настрЧислоИли(эл.barge_in_level, 'barge_in_level', false, ошибки),
    barge_instant: эл.barge_instant.checked,
    follow_up_window: настрЧислоИли(эл.follow_up_window, 'follow_up_window', false, ошибки),
    require_name_when_noisy: эл.require_name_when_noisy.checked,
    voice_app_guard: эл.voice_app_guard.checked,
    owner_only: эл.owner_only.checked,
    voice_autostart: эл.voice_autostart.checked,
    owner_threshold: настрЧислоИли(эл.owner_threshold, 'owner_threshold', false, ошибки),
    // Пустую модель не шлём: список мог ещё не догрузиться, и сохранение
    // любой другой настройки сбросило бы выбор на модель по умолчанию.
    stt_model: эл.stt_model.value || undefined,
    stt_quantization: эл.stt_quantization.value,
    output: эл.output.value,
    /* Звуковые ключи — только когда списки уже пришли (`/api/audio`). Иначе
       сохранение любой другой настройки отправило бы пустой `mic_name` и
       стёрло бы выбор микрофона, который хозяин когда-то сделал. */
    mic_name: звукСписки ? эл.mic_name.value : undefined,
    mic_channel: звукСписки
      ? (эл.mic_channelРяд && эл.mic_channelРяд.hidden
        ? 0 : звукЧисло(эл.mic_channel.value)) : undefined,
    speaker_name: звукСписки ? эл.speaker_name.value : undefined,
    autostart: эл.autostart.checked,
    theme: эл.theme.value,
  };
  /* Город погоды уходит, только если хозяин его правда поменял: иначе
     сохранение ради другой настройки обнулило бы погоду на телефоне. */
  if (эл.погЧерновик) {
    тело.weather_city = эл.погЧерновик.city;
    тело.weather_lat = эл.погЧерновик.lat;
    тело.weather_lon = эл.погЧерновик.lon;
  }
  if (ошибки.length > 0) {
    настрСтатус(ошибки[0].текст, true);
    настрПоказатьПоле(эл[ошибки[0].ключ]);
    return;
  }
  if (!тело.model) {
    настрСтатус(тело.provider === ЛОКАЛЬНЫЙ_СЕРВИС
      ? 'Впиши модель или обнови список' : 'Выбери или впиши модель', true);
    настрПоказатьПоле(эл.model);
    return;
  }
  if (тело.provider === ЛОКАЛЬНЫЙ_СЕРВИС && !тело.local_url) {
    настрСтатус('Впиши адрес локального сервера — или нажми Ollama', true);
    настрПоказатьПоле(эл.local_url);
    return;
  }
  if (тело.tts_engine === 'silero' && (!тело.silero_speaker || !тело.silero_model)) {
    const поле = !тело.silero_speaker ? эл.silero_speaker : эл.silero_model;
    настрСтатус('Укажи диктора и файл голоса Silero', true);
    настрПоказатьПоле(поле);
    return;
  }
  /* Образца нет — это не причина отменять сохранение. Раньше здесь был
     `return`, и выбор Higgs или ESpeech без готового образца не писался
     вовсе: после перезапуска пульт снова показывал Silero, а хозяин не
     понимал почему (29.09, отзыв о 0.9.6). Теперь выбор сохраняется, а
     сервер в `note` объясняет, что говорить пока нечем и куда добавить
     запись. */
  if (тело.tts_engine === 'silero') delete тело.voice_name;
  else { delete тело.silero_speaker; delete тело.silero_model; }
  if (тело.persona === настрБаза.persona) delete тело.persona;
  if (тело.memory === настрБаза.memory) delete тело.memory;
  // Правили память — сервер должен знать, какой мы её видели: факты,
  // дописанные разбором разговора после открытия страницы, не сотрутся.
  else тело.memory_base = настрБаза.memory;
  const введённыйКлюч = эл.api_key.value.trim();
  if (введённыйКлюч && введённыйКлюч !== эл.сохранённыеКлючи[эл.provider.value]) тело.api_key = введённыйКлюч;
  настрТянем = true;
  эл.сохранить.disabled = true;
  настрСтатус('Сохраняю…', false);
  try {
    const ответ = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(тело) });
    let данные = null;
    try { данные = await ответ.json(); } catch (e) { данные = null; }
    if (!ответ.ok || !данные || !данные.ok) {
      const сообщение = (данные && (данные.error || (данные.errors || []).join('; '))) || ('сервер ответил ' + ответ.status);
      const полеОшибка = /^([a-z_]+):\s*(.*)$/.exec(сообщение);
      if (полеОшибка && эл[полеОшибка[1]]) {
        настрПоказатьПоле(эл[полеОшибка[1]]);
        throw new Error((НАСТР_НАЗВАНИЯ[полеОшибка[1]]?.[0] || полеОшибка[1]) + ': ' + полеОшибка[2]);
      }
      throw new Error(сообщение);
    }
    if (тело.api_key) эл.сохранённыеКлючи[эл.provider.value] = тело.api_key;
    эл.ключиВПравке[эл.provider.value] = введённыйКлюч;
    if (данные.settings) заполнитьНастройки(данные.settings);
    настрЗагрузитьМодели();
    // Выбрал движок, а его модели на диске нет: голос звучать не станет, пока
    // её не скачать. Молча сохранить выбор — значит оставить хозяина с
    // молчащим голосом, поэтому говорим прямо и показываем блок выбора.
    if (голосаНапомнить(эл, тело.tts_engine)) {
      настрСтатус('Модель не скачана — отметь её в „Качественных голосах“ '
        + 'ниже и нажми „Скачать выбранное“', false);
    } else {
      настрСтатус(данные.note || 'Сохранено', false);
    }
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы) настрЭлементы.сохранить.disabled = false;
}

/* --- Образец голоса хозяина: «Голос → Слух» -------------------------------
   Запись живёт в настройках, а не на странице «Проверка»: ею пользуются
   «Только хозяин» и «Строгость узнавания», и хозяину нужен рядом текст для
   чтения. Эндпоинты те же — `/api/check/enroll/…` и `/api/check/job/<id>`. */

function хозяинЖива(эл) {
  // Форма настроек перестраивается при каждом заходе, и пока запрос летел,
  // хозяин мог уйти на другую страницу. `isConnected` — прямой признак, что
  // наш блок ещё в документе: `настрЭлементы` на закрытой странице тот же.
  return !!эл && настрЭлементы === эл && !!эл.хозяинЗаписать
    && эл.хозяинЗаписать.isConnected;
}

async function хозяинНачатьЗапись(эл) {
  if (!хозяинЖива(эл) || эл.хозяинЗаписать.disabled) return;
  эл.хозяинЗаписать.disabled = true;
  эл.хозяинЗакончить.disabled = true;
  эл.хозяинИтог.hidden = true;
  эл.хозяинИтог.innerHTML = '';
  эл.хозяинСтатус.classList.remove('плохо');
  эл.хозяинСтатус.textContent = 'начинаем…';
  // Текст для чтения появляется только на время записи.
  эл.хозяинТекст.hidden = false;
  try {
    const пуск = await провПост('/api/check/enroll/start');
    if (!хозяинЖива(эл)) return;
    эл.записьId = пуск.id;
    эл.хозяинЗакончить.disabled = false;
    эл.хозяинСтатус.textContent = 'читай текст… 0 с';
    хозяинСледитьЗапись(эл, пуск.id);
  } catch (e) {
    if (!хозяинЖива(эл)) return;
    эл.хозяинТекст.hidden = true;
    эл.хозяинСтатус.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.хозяинСтатус.classList.add('плохо');
    эл.хозяинЗаписать.disabled = false;
  }
}

function хозяинСледитьЗапись(эл, id) {
  const тик = async () => {
    if (!хозяинЖива(эл) || эл.записьId !== id) return;
    let job = null;
    try {
      job = await провДжоба(id);
    } catch (e) {
      return;
    }
    if (!хозяинЖива(эл) || эл.записьId !== id) return;
    const состояние = job && job.state;
    if (состояние === 'running') {
      const сек = (job.seconds !== undefined && job.seconds !== null) ? job.seconds : '…';
      эл.хозяинСтатус.textContent = 'читай текст… ' + сек + ' с';
      эл.опрос = setTimeout(() => хозяинСледитьЗапись(эл, id), 600);
      return;
    }
    if (состояние === 'processing') {
      эл.хозяинСтатус.textContent = 'считаем отпечаток…';
      эл.хозяинЗакончить.disabled = true;
      эл.опрос = setTimeout(() => хозяинСледитьЗапись(эл, id), 600);
      return;
    }
    хозяинПоказатьЗапись(эл, job);
  };
  эл.опрос = setTimeout(тик, 600);
}

async function хозяинЗакончитьЗапись(эл) {
  if (!хозяинЖива(эл) || !эл.записьId) return;
  эл.хозяинЗакончить.disabled = true;
  эл.хозяинСтатус.textContent = 'считаем отпечаток…';
  try {
    await провПост('/api/check/enroll/stop');
  } catch (e) {
    if (хозяинЖива(эл)) {
      эл.хозяинСтатус.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
      эл.хозяинСтатус.classList.add('плохо');
      эл.хозяинЗакончить.disabled = false;
    }
  }
}

function хозяинПоказатьЗапись(эл, job) {
  эл.записьId = null;
  if (!хозяинЖива(эл)) return;
  эл.хозяинЗаписать.disabled = false;
  эл.хозяинЗакончить.disabled = true;
  эл.хозяинТекст.hidden = true;
  if (!job || job.state === 'error') {
    эл.хозяинСтатус.textContent = 'Не вышло: ' + ((job && job.error) || 'запись не удалась');
    эл.хозяинСтатус.classList.add('плохо');
    return;
  }
  const разброс = job.spread || null;
  const среднее = (разброс && Number.isFinite(Number(разброс.mean))) ? Number(разброс.mean).toFixed(2) : '—';
  const кусков = job.taken || job.parts || '?';
  эл.хозяинСтатус.classList.remove('плохо');
  эл.хозяинСтатус.textContent = 'Голос записан: кусков ' + кусков + ', сходятся на ' + среднее + ', ' + (job.seconds || '?') + ' с.';
  /* Сервер подобрал строгость и уже сохранил её. В форме поле ещё со
     старым значением, и следующее «Сохранить» записало бы его обратно —
     то есть стёрло бы только что подобранное. */
  const строгость = Number(job.owner_threshold);
  const естьСтрогость = job.owner_threshold !== undefined && Number.isFinite(строгость);
  if (естьСтрогость) {
    эл.owner_threshold.value = String(строгость);
    эл.owner_threshold.синхПолзунок?.();
  }
  // Галочка «Только хозяин» была погашена — теперь ей можно щёлкнуть.
  эл.owner_only.disabled = false;
  эл.владелец.textContent = 'Голос хозяина записан — можно включить отбор по голосу';
  эл.хозяинЗаписать.textContent = 'Записать заново';
  эл.хозяинИтог.hidden = false;
  эл.хозяинИтог.innerHTML = '';
  провЦифра(эл.хозяинИтог, 'кусков взято / длина', кусков + ' / ' + (job.seconds || '?') + ' с');
  if (разброс) провЦифра(эл.хозяинИтог, 'сходство кусков (среднее)', среднее);
  if (естьСтрогость) {
    провЦифра(эл.хозяинИтог, 'строгость распознавания', строгость.toFixed(2) + ' · сохранена');
  }
  if (job.threshold_error) {
    const ошибка = document.createElement('div');
    ошибка.className = 'пров-намёк пров-ошибка';
    ошибка.textContent = 'Голос записан, но строгость не сохранилась: ' + job.threshold_error;
    эл.хозяинИтог.appendChild(ошибка);
  }
  const совет = document.createElement('div');
  совет.className = 'пров-намёк';
  совет.textContent = 'Включи «Только хозяин» выше и нажми «Сохранить».';
  эл.хозяинИтог.appendChild(совет);
}

/* --- Распознавание речи: «Голос → Распознавание» --------------------------
   Список моделей и строка «что сейчас работает» приходят с сервера
   (`GET /api/stt`): показывать надо не то, что выбрано в форме, а то, что
   действительно разбирает речь — после «Сохранить» модель меняется не сразу,
   а пока качается. */

function распЖива(эл) {
  return !!эл && настрЭлементы === эл && !!эл.распПроверить
    && эл.распПроверить.isConnected;
}

function распПояснить(эл) {
  const модель = (эл.stt_model.selectedOptions[0] || {}).dataset || {};
  const части = [];
  if (модель.note) части.push(модель.note);
  if (модель.size) части.push('Размер: ' + модель.size);
  эл.распПояснение.textContent = части.length ? части.join(' ') : '…';
}

function показатьРасп(эл, данные) {
  if (!распЖива(эл) || !данные) return;
  const модели = Array.isArray(данные.models) ? данные.models : [];
  const сжатия = Array.isArray(данные.quantizations) ? данные.quantizations : [];
  const было = эл.stt_model.value;
  эл.stt_model.innerHTML = '';
  for (const м of модели) {
    const пункт = document.createElement('option');
    пункт.value = м.id;
    пункт.textContent = м.title + (м.size ? ' · ' + м.size : '');
    пункт.dataset.note = м.note || '';
    пункт.dataset.size = м.size || '';
    эл.stt_model.appendChild(пункт);
  }
  if (модели.length === 0) {
    const пункт = document.createElement('option');
    пункт.value = '';
    пункт.textContent = '—';
    эл.stt_model.appendChild(пункт);
  } else if (модели.some((м) => м.id === было)) {
    эл.stt_model.value = было;
  }
  if (эл.stt_model.selectedIndex < 0) эл.stt_model.selectedIndex = 0;
  if (сжатия.length) {
    const былоСжатие = эл.stt_quantization.value;
    эл.stt_quantization.innerHTML = '';
    for (const к of сжатия) {
      const пункт = document.createElement('option');
      пункт.value = к.id;
      пункт.textContent = к.title;
      пункт.dataset.note = к.note || '';
      эл.stt_quantization.appendChild(пункт);
    }
    if (сжатия.some((к) => к.id === былоСжатие)) эл.stt_quantization.value = былоСжатие;
    if (эл.stt_quantization.selectedIndex < 0) эл.stt_quantization.selectedIndex = 0;
  }
  распПояснить(эл);
  эл.распСтатус.classList.remove('плохо');
  if (данные.loading) {
    эл.распСтатус.textContent = 'Загружаю модель…';
  } else if (данные.error) {
    эл.распСтатус.textContent = 'Не загрузилась: ' + данные.error;
    эл.распСтатус.classList.add('плохо');
  } else {
    эл.распСтатус.textContent = (данные.title || данные.model)
      ? 'Сейчас работает: ' + (данные.title || данные.model)
      : 'Голос выключен — модель встанет при включении';
  }
}

async function загрузитьРаспознавание() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  try {
    const ответ = await fetch('/api/stt', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    показатьРасп(эл, данные);
    // Пока идёт загрузка, статус ещё раз проверим: она может кончиться
    // прям сейчас, и хозяину надо увидеть «готово», не перезаходя в раздел.
    if (данные.loading) {
      setTimeout(() => { if (распЖива(эл)) загрузитьРаспознавание(); }, 1500);
    }
  } catch (e) {
    // Нет связи — строка состояния просто останется прежней.
  }
}

async function проверитьРаспознавание(эл) {
  if (!распЖива(эл) || эл.распПроверить.disabled) return;
  эл.распПроверить.disabled = true;
  эл.распИтог.hidden = true;
  эл.распИтог.innerHTML = '';
  эл.распСтатус.classList.remove('плохо');
  эл.распСтатус.textContent = 'Скажи что-нибудь — жду до 10 секунд…';
  try {
    const ответ = await fetch('/api/stt/test', { method: 'POST' });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    if (!распЖива(эл)) return;
    эл.распИтог.hidden = false;
    эл.распИтог.innerHTML = '';
    const текст = document.createElement('div');
    текст.className = 'пров-текст';
    текст.textContent = данные.text || 'Ничего не разобрала — скажи поближе к микрофону';
    эл.распИтог.appendChild(текст);
    провЦифра(эл.распИтог, 'записано', (данные.seconds || '?') + ' с');
    эл.распСтатус.textContent = 'Готово';
  } catch (e) {
    if (!распЖива(эл)) return;
    эл.распСтатус.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.распСтатус.classList.add('плохо');
  }
  if (распЖива(эл)) эл.распПроверить.disabled = false;
}

/* --- Звук: микрофон, колонки, уровень и проверка ---------------------------
   «Как в играх»: список микрофонов, уровень, куда идёт голос, и две кнопки —
   «Записать 3 секунды» и «Прослушать». Всё это — обычные поля общей формы
   настроек, поэтому в списке `НАСТР_СТРАНИЦЫ.голос` стоит `'звук'`, а секция с
   теми же полями одна: второго поля `output` в разделе «Телефон» не осталось. */

/* Число из поля формы или `data-*`: там всегда строка, а общая `число()`
   берёт только настоящие числа и на строку отвечает пустотой — «Вход 2»
   молча превращался бы в «Вход 1». */
function звукЧисло(значение) {
  const n = Number(значение);
  return Number.isFinite(n) ? n : 0;
}

/* Жива ли форма: пока запрос летел, хозяин мог уйти на другую страницу.
   Общая проверка одна на всех; мастер первого запуска подставляет свою
   (см. `мастерЖива`), поэтому она передаётся параметром. */
function настройкиЖивы(эл) {
  return настрЭлементы === эл;
}

function звукЖива(эл, жить) {
  // Та же проверка, что у `распЖива`: форма перестраивается при каждом заходе,
  // и пока запрос летел, хозяин мог уйти на другую страницу.
  const ок = жить || настройкиЖивы;
  return !!эл && ок(эл) && !!эл.mic_name
    && эл.mic_name.isConnected;
}

function звукСбросить() {
  // Форма перестроилась — прошлые списки и таймер больше не про эту страницу.
  звукСписки = null;
  звукУровеньВыключить();
  железоГрузили = false;
}

/* Пункт списка: «Как в Windows» и сами устройства. Сохранённое имя — это
   подстрока (например, название звуковой карты), поэтому пункт, который её
   содержит, получает value ровно из сохранённой строки: иначе «Сохранить»
   превратил бы подстроку в
   полное имя устройства и считал бы это правкой. */
function звукЗаполнить(выбор, устройства, сохранённое) {
  const список = Array.isArray(устройства) ? устройства : [];
  const свой = список.find((у) => у && у.default) || null;
  выбор.innerHTML = '';
  const первый = document.createElement('option');
  первый.value = '';
  первый.textContent = 'Как в Windows' + (свой && свой.name ? ' — ' + свой.name : '');
  выбор.appendChild(первый);
  for (const у of список) {
    if (!у || !у.name) continue;
    const пункт = document.createElement('option');
    пункт.value = String(у.name);
    пункт.textContent = String(у.name);
    пункт.dataset.channels = String(число(у.channels) || 0);
    выбор.appendChild(пункт);
  }
  const сохранён = String(сохранённое || '').trim();
  if (сохранён) {
    const наш = Array.from(выбор.options).find((п) =>
      п.value !== '' && п.value.toLowerCase().includes(сохранён.toLowerCase()));
    if (наш) наш.value = сохранён;
    else {
      // Устройство отключили или переименовали: выбор сохраняем, но говорим
      // честно, что сейчас такого нет.
      const пункт = document.createElement('option');
      пункт.value = сохранён;
      пункт.textContent = сохранён + ' — сейчас не подключён';
      пункт.dataset.channels = '0';
      выбор.appendChild(пункт);
    }
  }
  выбор.value = сохранён;
  if (выбор.selectedIndex < 0) выбор.selectedIndex = 0;
}

/* Ряд «Вход» нужен только у карт с двумя входами: у обычного микрофона
   второго входа нет, и поле было бы враньём. Списки не пришли — ряд виден и
   уходит то, что выбрано (см. `сохранитьНастройки`). */
function звукПоказатьВход(эл, жить) {
  if (!звукЖива(эл, жить) || !эл.mic_channelРяд) return;
  if (!звукСписки) { эл.mic_channelРяд.hidden = false; return; }
  const пункт = (эл.mic_name.options[эл.mic_name.selectedIndex]) || null;
  const каналов = пункт ? звукЧисло(пункт.dataset.channels) : 0;
  эл.mic_channelРяд.hidden = каналов < 2;
}

async function загрузитьЗвук(эл, жить) {
  if (!эл) return;
  try {
    const ответ = await fetch('/api/audio', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    if (!звукЖива(эл, жить)) return;
    звукСписки = {
      inputs: Array.isArray(данные.inputs) ? данные.inputs : [],
      outputs: Array.isArray(данные.outputs) ? данные.outputs : [],
    };
    // Текущий выбор с сервера — источник правды: он же, что в `звукСохранено`.
    if (данные.current) {
      звукСохранено = {
        mic_name: String(данные.current.mic_name || ''),
        mic_channel: число(данные.current.mic_channel) || 0,
        speaker_name: String(данные.current.speaker_name || ''),
      };
    }
    звукЗаполнить(эл.mic_name, звукСписки.inputs, звукСохранено.mic_name);
    звукЗаполнить(эл.speaker_name, звукСписки.outputs, звукСохранено.speaker_name);
    if (эл.mic_channel.selectedIndex < 0) эл.mic_channel.selectedIndex = 0;
    эл.mic_channel.value = String(звукСохранено.mic_channel);
    звукПоказатьВход(эл, жить);
  } catch (e) {
    // Нет связи или нет звука на компьютере: форма остаётся рабочей,
    // списки пустые, и «Сохранить» их не трогает (см. `звукСписки`).
  }
}

/* --- Тесты ниже в этом файле --- */

/* --- Погода: город для телефона ---------------------------------------------
   Геокодер один на всех (`/api/weather/find`), вручную координаты хозяину
   вписывать незачем. Выбранный город живёт в черновике и уходит с остальными
   настройками — но только если хозяин его правда поменял. */

function погодаЗаполнить(эл) {
  if (!эл || !эл.погГород) return;
  const черновик = эл.погЧерновик || {
    city: String(эл.погГородСохранено || ''),
  };
  эл.погГород.textContent = черновик.city
    ? 'Сейчас: ' + черновик.city + (эл.погЧерновик ? ' — нажми «Сохранить»' : '')
    : 'Погода выключена — выбери город';
  эл.погБез.hidden = !черновик.city;
  эл.погСписок.innerHTML = '';
}

function погодаВыбрать(эл, место) {
  if (!эл || !место) return;
  эл.погЧерновик = {
    city: String(место.name || ''),
    lat: число(место.lat),
    lon: число(место.lon),
  };
  погодаЗаполнить(эл);
}

function погодаБезГорода(эл) {
  if (!эл) return;
  эл.погЧерновик = { city: '', lat: null, lon: null };
  погодаЗаполнить(эл);
}

async function найтиГород(эл, жить) {
  if (!эл || !эл.погВвод) return;
  const ок = жить || настройкиЖивы;
  const запрос = эл.погВвод.value.trim();
  эл.погСписок.innerHTML = '';
  if (!запрос) { эл.погСтатус.textContent = 'Впиши название города'; return; }
  эл.погСтатус.textContent = 'Ищу…';
  try {
    const ответ = await fetch('/api/weather/find?q=' + encodeURIComponent(запрос),
      { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ок(эл)) return;
    const места = (данные && Array.isArray(данные.places)) ? данные.places : [];
    if (места.length === 0) {
      эл.погСтатус.textContent = 'Ничего не нашла — проверь название или интернет';
      return;
    }
    эл.погСтатус.textContent = '';
    for (const место of места) {
      const кнопка = document.createElement('button');
      кнопка.type = 'button';
      кнопка.className = 'голос-кнопка пог-место';
      кнопка.textContent = [место.name, место.region, место.country]
        .filter(Boolean).join(', ');
      кнопка.addEventListener('click', () => погодаВыбрать(эл, место));
      эл.погСписок.appendChild(кнопка);
    }
  } catch (e) {
    if (!ок(эл)) return;
    эл.погСтатус.textContent = 'Ничего не нашла — проверь название или интернет';
  }
}

function показатьЖелезо(эл, данные) {
  const hw = (данные && данные.hw) || {};
  const совет = (данные && данные.recommend) || {};
  // Для списка движков: Higgs только там, где железо его тянет (FP8 — RTX 40
  // и 50 серии, от 8 ГБ). Совет приходит раньше статуса голосов.
  эл.железоСовет = совет;
  const винда = hw.windows || {};
  const процессор = hw.cpu || {};
  const gpu = Array.isArray(hw.gpus) ? hw.gpus : [];
  // Короткий итог — в шапке свёрнутого блока: сразу видно, какую модель
  // качать можно, не раскрывая список железа.
  if (эл.железоИтог) {
    эл.железоИтог.textContent = совет.voice_why || 'Железо определено, подробности — раскрой';
  }
  эл.железоБлок.innerHTML = '';
  const строка = (имя, значение) => {
    const у = document.createElement('div');
    у.className = 'железо-строка';
    const п = document.createElement('span');
    п.textContent = имя;
    const з = document.createElement('b');
    з.textContent = значение;
    у.append(п, з);
    эл.железоБлок.appendChild(у);
  };
  if (винда.release) {
    строка('Windows', винда.release + (винда.version ? ' (сборка ' + винда.version + ')' : ''));
  }
  if (процессор.name) {
    строка('Процессор', [процессор.name,
      (число(процессор.cores) || 0) + ' ядер',
      (число(процессор.threads) || 0) + ' потоков'].join(', '));
  }
  if (hw.ram_gb !== undefined && hw.ram_gb !== null) {
    строка('Память', String(hw.ram_gb) + ' ГБ');
  }
  if (hw.disk_free_gb !== undefined && hw.disk_free_gb !== null) {
    строка('Свободно на диске', String(hw.disk_free_gb) + ' ГБ');
  }
  if (gpu.length === 0) строка('Видеокарта', 'NVIDIA не найдена');
  for (const карта of gpu) {
    if (!карта || !карта.name) continue;
    строка('Видеокарта', [карта.name,
      (Math.round(число(карта.vram_gb) || 0)) + ' ГБ',
      карта.driver ? 'драйвер ' + карта.driver : null]
      .filter(Boolean).join(', '));
  }
  for (const текст of [совет.voice_why, совет.stt_why]) {
    if (!текст) continue;
    const стр = document.createElement('div');
    стр.className = 'железо-совет';
    стр.textContent = текст;
    эл.железоБлок.appendChild(стр);
  }
  for (const текст of (Array.isArray(совет.warnings) ? совет.warnings : [])) {
    if (!текст) continue;
    const стр = document.createElement('div');
    стр.className = 'железо-совет плохо';
    стр.textContent = текст;
    эл.железоБлок.appendChild(стр);
  }
}

/* Качественные голоса: библиотеки и модели — разное (29.09, отзыв о 0.9.6).
   Библиотеки ставит установщик, а модели качает пульт по выбору хозяина — голос
   в сеть не ходит вовсе (02.10). Поэтому выбор моделей виден всегда, где
   поставить или скачать можно: раньше тут была одна кнопка, и на вопрос
   «почему нет выбора?» ответа не было. */
function показатьГолоса(эл, данные) {
  if (!эл || !данные) return;
  const стоят = !!данные.installed;
  const ставятся = !!(данные.libs && данные.libs.active);
  эл.железоДанные = данные;
  if (данные.running || ставятся) {
    эл.железоГолоса.hidden = true;
    /* Пока ставятся библиотеки, всё сказано полосой над кнопкой: вторая
       строка «ставлю…» под ней только повторяла её (хозяин, 02.10). */
    эл.железоГолосаСтатус.textContent = ставятся ? '' : 'Качественные голоса ставятся…';
  } else if (стоят && голосаВсеНаДиске(данные.weights)) {
    /* Всё уже стоит и скачано: кнопка «Скачать выбранное» качать не будет, а
       «осталось скачать… Обе модели уже на диске» противоречило само себе
       (02.10, снимки для README). */
    эл.железоГолоса.hidden = true;
    эл.железоГолосаСтатус.textContent = 'Качественные голоса стоят, обе модели на диске';
  } else if (стоят || данные.possible) {
    эл.железоГолоса.hidden = false;
    эл.железоГолосаСтатус.textContent = стоят
      ? 'Библиотеки стоят — осталось скачать выбранные модели' + голосаВеса(данные.weights)
      : 'Библиотеки ещё не стоят — поставлю их здесь, потом скачаю выбранные модели';
  } else {
    эл.железоГолоса.hidden = true;
    /* Про место на диске уже сказано красной строкой выше, в советах по
       железу — 29.09 человек прислал снимок, где оно стояло дважды. Здесь —
       что с этим делать, без повтора. */
    эл.железоГолосаСтатус.textContent = данные.reason === 'disk'
      ? 'Освободи место на диске — тогда здесь появится кнопка установки'
      : (данные.why || 'Качественные голоса поставить не выйдет');
  }
  /* Раскрытие блока «Твой компьютер»: голоса стоят — свёрнут (итог и так
     виден в шапке), а поставить можно и не стоит — раскрыт сразу, чтобы
     выбор моделей не спрятался. Помнит хозяин своё решение — оно важнее. */
  const помнили = железоВыбор();
  if (помнили === null) железоРаскрыть(эл, !стоят && !!данные.possible, false);
  else железоРаскрыть(эл, помнили === '1', false);
  // Выбор способа озвучивания: без качественных голосов ESpeech и Higgs не
  // запустятся, и оставлять их выбранными — значит обещать голос, которого не
  // будет. Уже сохранённый движок не выключаем: иначе хозяин не смог бы уйти
  // из раздела, пока не поставит голоса.
  настрПометитьДвижки(эл, стоят);
  голосаСостояния(эл, данные);
  голосаХодШапки(эл, данные);
}

/* Ход в шапке «Твоего компьютера»: мигающая точка и процент, пока что-то
   качается или ставится. Виден только у свёрнутой карточки (CSS): у
   развёрнутой то же самое показывает полоса, и повторять его незачем. */
function голосаХодШапки(эл, данные) {
  if (!эл || !эл.железоХод || !эл.железоКнопка) return;
  const биб = данные && данные.libs;
  const ход = данные && данные.download;
  let текст = '';
  if (биб && биб.active) {
    текст = 'Ставлю библиотеки голосов — ' + голосаБибШаги(биб).процент + ' %';
  } else if (ход && ход.active) {
    текст = 'Качаю ' + String(ход.title || голосаИмя(ход.model) || 'модель')
      + ' — ' + голосаПроцент(ход) + ' %';
  }
  эл.железоХод.textContent = текст;
  эл.железоКнопка.classList.toggle('идёт', !!текст);
}

/* Модели качественных голосов: имя, подпись для человека и состояние.
   Две цифры у Higgs — разные вещи: 9,3 ГБ — файл на диске, 4,3 ГБ — сколько он
   занимает на видеокарте после сжатия в FP8. Хозяин их путал (02.10), поэтому
   обе подписаны своими словами. */
const ВЕСА_ГОЛОСОВ = [
  ['higgs', 'Higgs', 'лучше звучит; скачать 9,3 ГБ, на видеокарте 4,3 ГБ; нужна RTX 40 или 50'],
  ['espeech', 'ESpeech', 'скачать 2,7 ГБ, на видеокарте ~2 ГБ'],
];

function голосаИмя(модель) {
  const нашли = ВЕСА_ГОЛОСОВ.find(([ключ]) => ключ === модель);
  return нашли ? нашли[1] : String(модель || '');
}

/* Короткая строка о том, что уже на диске. Библиотеки стоят — ещё не значит,
   что модели скачаны (29.09): сказанное здесь «скачано» значит ровно то, что
   лежит на диске. */
function голосаВсеНаДиске(веса) {
  return !!веса && typeof веса === 'object'
    && ВЕСА_ГОЛОСОВ.every(([ключ]) => веса[ключ] === true);
}

function голосаВеса(веса) {
  if (!веса || typeof веса !== 'object') return '';
  const есть = ВЕСА_ГОЛОСОВ.filter(([ключ]) => веса[ключ]).map(([, имя]) => имя);
  if (есть.length === 0) {
    return '. Моделей на диске нет — отметь нужную и нажми «Скачать выбранное»';
  }
  if (есть.length === ВЕСА_ГОЛОСОВ.length) return '. Обе модели уже на диске';
  return '. На диске: ' + есть.join(' и ');
}

/* Состояние у каждой модели: «скачана» (зелёный — это индикатор), «не скачана»
   или «качается N %». Галочки по умолчанию — из выбора хозяина (`wanted`), а его
   нет — по железу: Higgs там, где карта его тянет, иначе ESpeech. Их ставим
   один раз за форму: опрос идёт каждые две секунды и не должен стирать то, что
   хозяин отметил сам. */
function голосаСостояния(эл, данные) {
  if (!эл || !эл.голосаРяды) return;
  const веса = (данные && данные.weights) || {};
  const ход = (данные && данные.download) || {};
  const стоят = !!(данные && данные.installed);
  for (const [ключ] of ВЕСА_ГОЛОСОВ) {
    const ряд = эл.голосаРяды[ключ];
    if (!ряд) continue;
    const наДиске = !!веса[ключ];
    const качается = !!ход.active && ход.model === ключ;
    ряд.отметка.disabled = наДиске;
    ряд.состояние.textContent = наДиске ? 'скачана'
      : качается ? 'качается ' + голосаПроцент(ход) + ' %' : 'не скачана';
    ряд.состояние.classList.toggle('есть', наДиске);
  }
  if (!эл.голосаГалочки) {
    эл.голосаГалочки = true;
    const совет = эл.железоСовет || {};
    const выбрано = (данные && Array.isArray(данные.wanted) && данные.wanted.length)
      ? данные.wanted : [совет.voice === 'higgs' ? 'higgs' : 'espeech'];
    for (const ряд of Object.values(эл.голосаРяды)) {
      ряд.отметка.checked = выбрано.indexOf(ряд.модель) >= 0;
    }
  }
  if (эл.железоГолоса && !эл.железоГолоса.hidden) {
    эл.железоГолоса.textContent = стоят ? 'Скачать выбранное' : 'Установить выбранное';
    эл.железоГолоса.title = стоят ? 'Качать выбранные модели здесь же, в пульте, с полоской'
      : 'Поставить библиотеки здесь же, в пульте, с полоской и отменой';
  }
  if (эл.голосаПолоса) голосаПолоса(эл.голосаПолоса, ход);
  if (эл.голосаБибПолоса) {
    голосаБибПолоса(эл.голосаБибПолоса, (данные && данные.libs) || null);
  }
}

/* Модель выбранного движка на диске? Если нет — после «Сохранить» говорим об
   этом прямо и раскрываем блок качественных голосов: иначе хозяин сохранит
   Higgs и будет ждать голос, который не запустится (02.10). Статуса железа
   могло не быть (хозяин не открывал блок) — тогда и напоминать не о чем. */
function голосаНапомнить(эл, движок) {
  if (!эл || !эл.железоДанные) return false;
  if (движок !== 'higgs' && движок !== 'espeech') return false;
  const веса = эл.железоДанные.weights || {};
  if (веса[движок]) return false;
  настрПоказатьРаздел('голос');
  железоРаскрыть(эл, true, false);
  if (эл.железоБлок && typeof эл.железоБлок.scrollIntoView === 'function') {
    эл.железоБлок.scrollIntoView({ block: 'nearest' });
  }
  return true;
}

/* Отмеченные хозяином модели. Уже скачанное повторно качать нечего, поэтому
   его галка неактивна и в список не попадает. */
function голосаВыбор(эл) {
  const ряды = (эл && эл.голосаРяды) || {};
  return ВЕСА_ГОЛОСОВ.map(([ключ]) => ключ)
    .filter((ключ) => ряды[ключ] && ряды[ключ].отметка.checked && !ряды[ключ].отметка.disabled);
}

function настрПометитьДвижки(эл, стоят) {
  if (!эл || !эл.tts_engine) return;
  /* Higgs держится на FP8, а он есть только у RTX 40 и 50 серии: на других
     картах голос не поднимется (29.09, проверка выпуска). Совета по железу
     нет — не гадаем и не запрещаем. */
  const совет = эл.железоСовет;
  const higgsТянет = !совет || !совет.voice || совет.voice === 'higgs';
  for (const пункт of Array.from(эл.tts_engine.options)) {
    if (пункт.value !== 'espeech' && пункт.value !== 'higgs') continue;
    /* Подписи с настоящими размерами: 9,3 ГБ — файл, который скачивается,
       4,3 ГБ — сколько Higgs занимает на видеокарте. Прежние «3 ГБ» и
       «4.3 ГБ» в одном списке путали хозяина (02.10). */
    /* Коротко: длинная подпись обрезалась в закрытом списке (02.10).
       Размеры — в «Качественных голосах» выше. */
    const база = (пункт.value === 'espeech' ? 'ESpeech · по образцу'
      : 'Higgs · по образцу');
    const выбран = эл.tts_engine.value;
    const железоНет = пункт.value === 'higgs' && !higgsТянет;
    /* Библиотеки у обеих моделей общие, поэтому в списке открыты обе — и без
       пометки казалось, что скачаны обе (хозяин, 02.10: «он обе скачал что
       ли?»). Выбрать нескачанную можно: её модель качается в «Качественных
       голосах», и голос поднимется сам, когда она скачается. */
    const веса = (эл.железоДанные && эл.железоДанные.weights) || null;
    const нетМодели = !!веса && веса[пункт.value] === false;
    пункт.disabled = (!стоят || железоНет) && пункт.value !== выбран;
    пункт.textContent = !пункт.disabled ? база + (нетМодели ? ' — не скачана' : '')
      : (!стоят ? база + ' — нужны качественные голоса' : база + ' — нужна RTX 40 или 50, от 8 ГБ');
  }
}

/* Кнопка блока: библиотеки стоят — качаем выбранные модели здесь же, в пульте,
   и полоса идёт под блоком; не стоят — ставим библиотеки тоже здесь, в фоне, с
   полосой и «Отменить» (только если пульт придётся один раз перезапустить
   «без голоса», про это сказано в вопросе).
   Выбор хозяина уходит вместе с запросом, поэтому и скачать, и поставить
   можно именно то, что он отметил (02.10: «сразу скачивать то, что выбрал»). */
async function поставитьГолоса(эл) {
  if (!эл || эл.железоГолоса.disabled) return;
  const модели = голосаВыбор(эл);
  if (!модели.length) {
    эл.железоГолосаСтатус.classList.add('плохо');
    эл.железоГолосаСтатус.textContent = 'Отметь модель: Higgs или ESpeech';
    return;
  }
  const стоят = !!(эл.железоДанные && эл.железоДанные.installed);
  if (!стоят && !window.confirm('Поставлю библиотеки голосов (около 4,5 ГБ, 10–20 '
    + 'минут) прямо здесь, с полосой. Если голос уже работал, пульт один раз '
    + 'перезапустится секунд на десять. Потом скачаются выбранные модели. '
    + 'Начать?')) return;
  эл.железоГолоса.disabled = true;
  эл.железоГолосаСтатус.classList.remove('плохо');
  эл.железоГолосаСтатус.textContent = стоят ? 'Начинаю скачивать…' : 'Ставлю библиотеки…';
  let перезапуск = false;
  try {
    const ответ = await fetch(стоят ? '/api/voices/download' : '/api/voices/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models: модели }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (настрЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    перезапуск = !!данные.restart;
    if (данные.already) {
      эл.железоГолосаСтатус.textContent = 'Эти модели уже на диске — качать нечего';
    } else if (!стоят && данные.restart) {
      // Torch уже был в памяти: пульт сейчас перезапустится «без голоса», а
      // библиотеки поставит уже он — с полосой.
      эл.железоГолосаСтатус.textContent = 'Перезапускаю пульт — потом поставлю '
        + 'библиотеки и скачаю модели';
    } else if (!стоят) {
      эл.железоГолосаСтатус.textContent = 'Ставлю библиотеки голосов…';
    } else {
      эл.железоГолосаСтатус.textContent = 'Качаю: ' + модели.map(голосаИмя).join(', ');
    }
  } catch (e) {
    if (настрЭлементы !== эл) return;
    эл.железоГолосаСтатус.classList.add('плохо');
    эл.железоГолосаСтатус.textContent = (e && e.message) ? e.message : String(e);
  } finally {
    if (настрЭлементы === эл) эл.железоГолоса.disabled = false;
  }
  /* Опрос нужен и когда ставятся библиотеки: полоса идёт по таймеру, а
     событий у установки нет (02.10 — так же было с моделями). А вот перезапуск
     пульта: окно сейчас исчезнет, спрашивать `/api/voices/status` некому. */
  if (!перезапуск) голосаОпросВключить();
}

/* --- Полоска скачивания моделей -------------------------------------------
   Один рисунок на весь пульт: и под блоком «Качественные голоса», и под
   карточкой голоса на Панели. Синяя, как у остальных мер пульта: зелёный
   здесь — только индикатор «скачана». */

function голосаПроцент(ход) {
  const всего = число(ход && ход.total) || 0;
  if (всего <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round(((число(ход.done) || 0) * 100) / всего)));
}

function голосаГБ(байты) {
  const сколько = число(байты);
  if (сколько === null) return '?';
  return (сколько / 1e9).toFixed(1).replace('.', ',') + ' ГБ';
}

/* `куда` — пустой узел, в который рисуется полоса. Качать нечего и ошибки нет —
   узел пустеет и прячется: пустого места под несуществующую загрузку быть не
   должно (02.10). */
function голосаПолоса(куда, ход) {
  if (!куда) return;
  куда.innerHTML = '';
  const качается = !!(ход && ход.active);
  const ошибка = String((ход && ход.error) || '').trim();
  куда.hidden = !качается && !ошибка;
  if (куда.hidden) return;
  if (качается) {
    const голос = String(ход.title || голосаИмя(ход.model) || 'модель');
    const процент = голосаПроцент(ход);
    const надпись = document.createElement('div');
    надпись.className = 'голоса-полоса-текст';
    надпись.textContent = 'Качаю ' + голос + ': ' + голосаГБ(ход.done) + ' из '
      + голосаГБ(ход.total) + ' (' + процент + ' %)';
    куда.appendChild(надпись);
    const полоса = document.createElement('div');
    полоса.className = 'голоса-полоса';
    полоса.setAttribute('role', 'progressbar');
    полоса.setAttribute('aria-valuenow', String(процент));
    const заливка = document.createElement('i');
    заливка.style.width = процент.toFixed(1) + '%';
    полоса.appendChild(заливка);
    куда.appendChild(полоса);
    const низ = document.createElement('div');
    низ.className = 'голоса-полоса-низ';
    const отменить = document.createElement('button');
    отменить.type = 'button';
    отменить.className = 'голос-кнопка';
    отменить.textContent = 'Отменить';
    отменить.title = 'Остановить скачивание. Докачать можно в следующий раз';
    отменить.addEventListener('click', () => голосаОтменить(отменить));
    низ.appendChild(отменить);
    const очередь = Array.isArray(ход.queue) ? ход.queue : [];
    if (очередь.length) {
      const дальше = document.createElement('span');
      дальше.className = 'голоса-очередь';
      дальше.textContent = 'дальше: ' + очередь.map(голосаИмя).join(', ');
      низ.appendChild(дальше);
    }
    куда.appendChild(низ);
  }
  if (ошибка) {
    const строка = document.createElement('div');
    строка.className = 'голоса-ошибка';
    строка.textContent = 'Не скачалось: ' + ошибка;
    куда.appendChild(строка);
  }
}

async function голосаОтменить(кнопка) {
  if (кнопка) кнопка.disabled = true;
  try {
    const ответ = await fetch('/api/voices/download/cancel', { method: 'POST' });
    if (!ответ.ok) throw new Error('сервер ответил ' + ответ.status);
  } catch (e) {
    /* Отмена — не беда: сервер сам увидит, что качать больше нечего. Молчим,
       чтобы не пугать хозяина ошибкой на пустом месте. */
  }
  await голосаОдинРаз();
}

/* --- Полоска установки библиотек ------------------------------------------
   Близнец `голосаПолоса`: тот же вид, тот же «Отменить». Процент — по объёму:
   сервер знает, сколько библиотеки занимают распакованными (`total`, замер
   02.10 на рабочей Трубе), и сколько уже легло в кеш (`downloaded`). Пока
   идёт — не больше 99 %: размер примерный, а «100 %» на ещё идущей установке
   обманул бы. Нет `total` (старый сервер) — по шагам. */

function голосаБибШаги(ход) {
  const шаг = число(ход && ход.step) || 0;
  const шагов = число(ход && ход.steps) || 2;
  const всего = число(ход && ход.total) || 0;
  const есть = число(ход && ход.downloaded) || 0;
  const процент = всего > 0
    ? Math.min(99, Math.floor((есть * 100) / всего))
    : (шагов > 0 ? Math.round(((Math.max(шаг, 1) - 1) * 100) / шагов) : 0);
  return { шаг, шагов, процент, всего, есть };
}

function голосаБибПолоса(куда, ход) {
  if (!куда) return;
  куда.innerHTML = '';
  const ставится = !!(ход && ход.active);
  const ошибка = String((ход && ход.error) || '').trim();
  куда.hidden = !ставится && !ошибка;
  if (куда.hidden) return;
  if (ставится) {
    const { шаг, шагов, процент, всего, есть } = голосаБибШаги(ход);
    const надпись = document.createElement('div');
    надпись.className = 'голоса-полоса-текст';
    надпись.textContent = 'Ставлю библиотеки голосов: шаг ' + (шаг || 1) + ' из '
      + шагов + ' — ' + String(ход.title || 'пакеты') + ', '
      + (всего > 0
        ? голосаГБ(есть) + ' из ~' + голосаГБ(всего) + ' (' + процент + ' %)'
        : 'скачано ' + голосаГБ(есть));
    куда.appendChild(надпись);
    const полоса = document.createElement('div');
    полоса.className = 'голоса-полоса';
    полоса.setAttribute('role', 'progressbar');
    // Полоса показывает законченные шаги, а не процент байтов: сколько весит
    // установка, пульт не знает и врать не должен.
    полоса.setAttribute('aria-valuenow', String(Math.max(0, процент)));
    const заливка = document.createElement('i');
    заливка.style.width = Math.max(0, Math.min(100, процент)).toFixed(1) + '%';
    полоса.appendChild(заливка);
    куда.appendChild(полоса);
    const низ = document.createElement('div');
    низ.className = 'голоса-полоса-низ';
    const отменить = document.createElement('button');
    отменить.type = 'button';
    отменить.className = 'голос-кнопка';
    отменить.textContent = 'Отменить';
    отменить.title = 'Остановить установку. Продолжить можно в следующий раз';
    отменить.addEventListener('click', () => голосаБибОтменить(отменить));
    низ.appendChild(отменить);
    куда.appendChild(низ);
  }
  if (ошибка) {
    const строка = document.createElement('div');
    строка.className = 'голоса-ошибка';
    строка.textContent = 'Не поставилось: ' + ошибка;
    куда.appendChild(строка);
  }
}

async function голосаБибОтменить(кнопка) {
  if (кнопка) кнопка.disabled = true;
  try {
    const ответ = await fetch('/api/voices/libs/cancel', { method: 'POST' });
    if (!ответ.ok) throw new Error('сервер ответил ' + ответ.status);
  } catch (e) {
    /* Как у скачивания: молчим. Сервер и сам увидит, что ставить нечего. */
  }
  await голосаОдинРаз();
}

/* --- Опрос хода скачивания: один таймер на весь пульт ---------------------
   Проценты идут без событий, поэтому их спрашивают по таймеру — так же, как
   уровень микрофона (`звукУровеньВключить`): раз в две секунды, одним таймером
   на весь пульт и не при скрытом окне. Качать нечего — таймер снят. Установка
   библиотек спрашивается тем же таймером: пока идёт она или качаются модели. */

const ГОЛОСА_ПАУЗА = 2000;
let голосаТаймер = null;
let голосаХод = null;
let голосаБибХод = null;

/* Идёт ли что-то из того, о чём пульт рисует полосу: скачивание моделей или
   установка библиотек. Пульт перезапускается один раз и только если torch уже
   был в памяти, поэтому эти два дела вместе не встречаются — но полоса и опрос
   должны знать про оба. */
function голосаИдёт() {
  return !!((голосаХод && голосаХод.active) || (голосаБибХод && голосаБибХод.active));
}

/* Идёт дело — поднять опрос. Нужна там, где ход узнали одним вопросом: пульт
   открылся уже посреди установки (перезапуск «без голоса» ради неё) или
   докачки моделей — без опроса полоса застыла бы на первом снимке. */
function голосаОпросЕслиИдёт() {
  if (голосаИдёт()) голосаОпросВключить();
}

function голосаОпросВыключить() {
  if (голосаТаймер === null) return;
  clearTimeout(голосаТаймер);
  голосаТаймер = null;
}

function голосаОпросВключить() {
  if (голосаТаймер !== null) return;
  if (typeof document !== 'undefined' && document.hidden) return;
  const шаг = async () => {
    голосаТаймер = null;
    if (document.hidden) return;
    await голосаОдинРаз();
    if (document.hidden) return;
    if (голосаИдёт()) голосаТаймер = setTimeout(шаг, ГОЛОСА_ПАУЗА);
  };
  шаг();
}

async function голосаОдинРаз() {
  try {
    const ответ = await fetch('/api/voices/status', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    голосаХод = данные.download || null;
    голосаБибХод = данные.libs || null;
    if (!голосаИдёт()) {
      // Качать и ставить нечего: галочки и подписи всё равно могли устареть
      // (модель могла скачаться или удалиться), поэтому картинку всё равно
      // обновляем.
      if (голосаТаймер !== null) голосаОпросВыключить();
    }
    const эл = настрЭлементы;
    if (эл && эл.железоБлок && настрСтраница === 'голос' && голосПодраздел === 'голос'
        && эл.железоБлок.isConnected) {
      показатьГолоса(эл, данные);
    }
    if (текущий === 'панель') обновитьПанельГолос();
  } catch (e) {
    /* Молчим: один неудачный вопрос не должен ронять загрузку. */
  }
}

async function проверитьЗвук(эл, жить) {
  if (!звукЖива(эл, жить) || эл.звукПроверить.disabled) return;
  if (звукНеСохранено(эл, жить)) {
    // Проверка идёт на сохранённом микрофоне, а не на том, что в форме:
    // сказать «записала 3 с» и спеть чужим голосом — хуже, чем не сказать.
    эл.звукСтатус.textContent = 'Сначала нажми «Сохранить» — проверка идёт на сохранённом';
    эл.звукСтатус.classList.add('плохо');
    return;
  }
  await звукЗаписать(эл, жить);
}

/* Сама запись. Форма настроек зовёт её после своей проверки, мастер — после
   `мастерСохранитьЗвук` (см. `мастерПроверитьЗвук`): там кнопки «Сохранить»
   нет, и сохраняет он сам. `куда` — адрес запроса: мастер шлёт
   `/api/audio/test?probe=1`, и тогда голос на время записи снимает и возвращает
   сам сервер. Кнопку она берёт под себя сразу: два нажатия подряд не должны
   слать две записи.

   Обратный отсчёт — на клиенте: запрос идёт три секунды, а сервер отвечает
   один раз в конце, и без счёта хозяин не понимает, идёт запись или кнопка
   зависла. */
async function звукЗаписать(эл, жить, куда) {
  if (!звукЖива(эл, жить)) return;
  эл.звукПроверить.disabled = true;
  эл.звукСтатус.classList.remove('плохо');
  let таймер = null;
  const остановка = () => {
    if (таймер === null) return;
    clearTimeout(таймер);
    таймер = null;
  };
  try {
    let осталось = 3;
    /* Проба мастера (`?probe=1`) сначала останавливает голос — это секунды,
       и запись начинается не в момент нажатия. Отсчёт «3, 2, 1» тогда
       кончился бы раньше записи, человек замолчал бы и получил «тишину».
       Поэтому там без отсчёта: говорить, пока не появится результат. */
    const сПаузой = String(куда || '').includes('probe=1');
    эл.звукСтатус.textContent = сПаузой
      ? 'Говори что-нибудь, пока не появится результат…'
      : 'Говори… ' + осталось;
    if (!сПаузой) таймер = setTimeout(function шаг() {
      таймер = null;
      if (!звукЖива(эл, жить)) return;
      осталось -= 1;
      if (осталось > 0) {
        эл.звукСтатус.textContent = 'Говори… ' + осталось;
        таймер = setTimeout(шаг, 1000);
      }
    }, 1000);
    const ответ = await fetch(куда || '/api/audio/test', { method: 'POST' });
    const данные = await ответ.json().catch(() => ({}));
    if (!звукЖива(эл, жить)) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    // Что услышал микрофон — главный ответ проверки, а не «записала 3 с».
    эл.звукСтатус.textContent = String(данные.verdict_text
      || ('Записала ' + (данные.seconds || 3) + ' с'));
    if (данные.verdict === 'silent') эл.звукСтатус.classList.add('плохо');
    звукЗаписьПоказать(эл, данные);
    if (эл.звукПрослушать) эл.звукПрослушать.disabled = false;
  } catch (e) {
    if (!звукЖива(эл, жить)) return;
    эл.звукСтатус.textContent = (e && e.message) ? e.message : String(e);
    эл.звукСтатус.classList.add('плохо');
  } finally {
    остановка();
  }
  if (звукЖива(эл, жить)) эл.звукПроверить.disabled = false;
}

/* Строка под кнопками: «Запись: ▮▮▮▯▯ слышно хорошо». Полоска уровня показывает
   живой микрофон, а эта — что именно легла в запись, и держит результат, пока
   хозяин не нажмёт снова. Пять делений, потому что полоска в пять же шагов
   привычнее глазу, чем число процентов. */
function звукЗаписьПоказать(эл, данные) {
  if (!эл || !эл.звукЗапись) return;
  const level = Math.max(0, Math.min(1, число(данные.level)));
  let знаки = '';
  for (let i = 0; i < 5; i++) {
    знаки += (i / 5 < level) ? '▮' : '▯';
  }
  эл.звукЗапись.textContent = 'Запись: ' + знаки + ' '
    + String(данные.verdict_text || '');
  // Красным показываем только текущую запись: после «тишины» удачный повтор
  // должен убрать пометку, а не висеть до перезагрузки пульта.
  эл.звукЗапись.classList.toggle('плохо', данные.verdict === 'silent');
}

/* Прослушать запись — второе нажатие, а не продолжение записи. Раньше запись
   играла сразу и один раз мимо хозяина: не в те колонки, не в тот момент, и
   переслушать было нечем. Голосом не управляем: колонки он не держит
   монопольно, а гасить голос из вкладки нельзя — закрыл вкладку, и труба
   осталась молчащей. */
async function прослушатьЗапись(эл, жить) {
  if (!звукЖива(эл, жить) || !эл.звукПрослушать) return;
  if (эл.звукПрослушать.disabled) return;
  эл.звукПрослушать.disabled = true;
  эл.звукСтатус.classList.remove('плохо');
  эл.звукСтатус.textContent = 'Играю в колонки…';
  try {
    const ответ = await fetch('/api/audio/test/play', { method: 'POST' });
    const данные = await ответ.json().catch(() => ({}));
    if (!звукЖива(эл, жить)) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    const части = ['Проиграла в: ' + (данные.speaker || 'колонки')];
    // Подсказка именно после звука: пока играло, она была неуместна, а после
    // «не слышно» хозяину нужен следующий шаг, а не молчание.
    части.push('Не слышно? Выбери другие колонки выше и нажми «Прослушать» ещё раз');
    if (данные.note) части.push(данные.note);
    эл.звукСтатус.textContent = части.join(' · ');
  } catch (e) {
    if (!звукЖива(эл, жить)) return;
    эл.звукСтатус.textContent = (e && e.message) ? e.message : String(e);
    эл.звукСтатус.classList.add('плохо');
  }
  if (звукЖива(эл, жить) && эл.звукПрослушать) эл.звукПрослушать.disabled = false;
}

/* Отличается ли выбор в форме от сохранённого. Списков нет — сравнивать
   нечего, и проверка идёт: сервер всё равно скажет, что микрофон занят. */
function звукНеСохранено(эл, жить) {
  if (!звукЖива(эл, жить) || !звукСписки) return false;
  const вход = эл.mic_channelРяд && эл.mic_channelРяд.hidden
    ? 0 : звукЧисло(эл.mic_channel.value);
  return String(эл.mic_name.value) !== String(звукСохранено.mic_name)
    || вход !== (число(звукСохранено.mic_channel) || 0)
    || String(эл.speaker_name.value) !== String(звукСохранено.speaker_name);
}

/* --- Железо и качественные голоса: блок в «Озвучивании» --------------------
   Определение занимает до пяти секунд и не меняется, поэтому спрашиваем один
   раз за открытие формы — сервер всё равно кеширует ответ на минуту. */

/* Блок свёрнут по умолчанию: сразу видно, какую модель качать можно (это
   пишет `recommend.voice_why` в шапке), а подробности железа — по запросу.
   Развёрнут он сразу только один случай: качественных голосов нет, а
   поставить можно — кнопку установки нельзя прятать от того, кому она
   нужна. Выбор хозяина (открывал/закрывал сам) важнее этого правила. */
const ЖЕЛЕЗО_КЛЮЧ = 'железоОткрыт';
function железоВыбор() {
  try { return localStorage.getItem(ЖЕЛЕЗО_КЛЮЧ); } catch (e) { return null; }
}
function железоРаскрыть(эл, открыт, помнить) {
  if (!эл || !эл.железоКнопка || !эл.железоТело) return;
  if (помнить) {
    try { localStorage.setItem(ЖЕЛЕЗО_КЛЮЧ, открыт ? '1' : '0'); } catch (e) { /* не страшно */ }
  }
  эл.железоКнопка.classList.toggle('раскрыто', !!открыт);
  эл.железоКнопка.setAttribute('aria-expanded', открыт ? 'true' : 'false');
  if (эл.железоСтрелка) эл.железоСтрелка.textContent = открыт ? '▾' : '▸';
  эл.железоТело.classList.toggle('раскрыто', !!открыт);
}
function железоПеревернуть(эл) {
  if (!эл || !эл.железоКнопка) return;
  железоРаскрыть(эл, !эл.железоКнопка.classList.contains('раскрыто'), true);
}

async function загрузитьЖелезо() {
  const эл = настрЭлементы;
  if (!эл || !эл.железоБлок || железоГрузили) return;
  железоГрузили = true;
  try {
    const ответ = await fetch('/api/hardware', { cache: 'no-store' });
    const данные = await ответ.json();
    if (настрЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('железо не определилось');
    показатьЖелезо(эл, данные);
    // Качественные голоса — тот же раздел: без них ESpeech и Higgs не выбрать.
    const статус = await fetch('/api/voices/status', { cache: 'no-store' });
    const данныеГолосов = await статус.json().catch(() => null);
    if (настрЭлементы !== эл) return;
    if (статус.ok && данныеГолосов && данныеГолосов.ok) {
      // Загрузка могла идти уже к этой минуте (хозяин нажал «Скачать» и ушёл
      // на другой раздел) — тогда полоса и опрос встают сразу.
      голосаХод = данныеГолосов.download || null;
      голосаБибХод = данныеГолосов.libs || null;
      показатьГолоса(эл, данныеГолосов);
      голосаОпросЕслиИдёт();
    }
  } catch (e) {
    if (настрЭлементы !== эл) return;
    эл.железоБлок.textContent = 'Не вышло узнать компьютер: '
      + ((e && e.message) ? e.message : e);
  }
}

/* Полоска уровня. Сервер отдаёт громкость уже «под глаз» — корень из доли
   от полной речи (`core/audio_in.py::voice_level`, та же, что светит рамкой
   телефона), поэтому рисуем как есть. Децибелы поверх неё сжимали бы шкалу
   второй раз: тишина комнаты стояла бы на трёх четвертях полоски. */
function звукУровеньПоказать(эл, level, тихо, жить) {
  if (!звукЖива(эл, жить) || !эл.уровень) return;
  const доля = Math.max(0, Math.min(1, Number(level) || 0));
  эл.уровеньFill.style.width = (доля * 100).toFixed(1) + '%';
  эл.уровень.classList.toggle('приглушено', !!тихо);
  if (тихо) {
    эл.уровеньПодпись.textContent = 'Голос выключен — уровня нет. Включи голос или нажми «Записать 3 секунды»';
  } else if (эл.уровеньПодпись.dataset.тихо === '1') {
    эл.уровеньПодпись.textContent = '';
    эл.уровеньПодпись.dataset.тихо = '';
  }
}

async function звукУровеньОдинРаз(эл, жить) {
  try {
    const ответ = await fetch('/api/audio/level', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!звукЖива(эл, жить)) return;
    if (!ответ.ok || !данные || !данные.ok) {
      звукУровеньПоказать(эл, 0, true, жить);
      if (эл.уровеньПодпись.dataset.тихо !== '1') эл.уровеньПодпись.dataset.тихо = '1';
      return;
    }
    звукУровеньПоказать(эл, число(данные.level) || 0, false, жить);
  } catch (e) {
    if (звукЖива(эл, жить)) звукУровеньПоказать(эл, 0, true, жить);
  }
}

/* Вернулись в окно — полоска снова идёт, если открыт «Звук». Сама по себе
   она при уходе останавливается (`document.hidden` в `звукУровеньВключить`).
   У мастера своя полоска выбранного микрофона (`мастерУровеньВключить`):
   вернувшись на шаг 4, поднимаем и её. */
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    звукУровеньВыключить(); мастерУровеньВыключить(); голосаОпросВыключить();
    return;
  }
  // Скачивание моделей и установка библиотек не спрашивают статус при скрытом
  // окне, поэтому на возврате опрос надо поднять заново.
  голосаОпросЕслиИдёт();
  if (настрЭлементы && настрСтраница === 'голос' && голосПодраздел === 'звук') {
    звукУровеньВключить(настрЭлементы);
  }
  if (мастерОткрыт && мастерШаг === 4 && мастерШаги[4]) {
    мастерУровеньВключить(мастерШаги[4]);
  }
});

function звукУровеньВыключить() {
  if (звукТаймер) { clearTimeout(звукТаймер); звукТаймер = null; }
}

function звукУровеньВключить(эл, жить) {
  звукУровеньВыключить();
  if (!эл) return;
  if (!звукЖива(эл, жить) || document.hidden) return;
  // Один таймер на весь раздел: ушёл хозяин со вкладки (`document.hidden`) —
  // запросы прекращаются, а вернулся — снова начинаются.
  const шаг = async () => {
    звукТаймер = null;
    if (!звукЖива(эл, жить) || document.hidden) return;
    await звукУровеньОдинРаз(эл, жить);
    if (!звукЖива(эл, жить) || document.hidden) return;
    звукТаймер = setTimeout(шаг, 150);
  };
  шаг();
}

/* Список последних реплик: кто говорил, когда и что именно. Загружается
   только по нажатию (`/api/settings/history`) — это чтение, ничего не стирает
   и никуда наружу не отправляет. Хозяин 1 октября: «18 реплик в разговоре» —
   вижу, а открыть негде. */
async function показатьИсториюРазговора() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const список = эл.историяСписок;
  if (!список) return;
  настрТянем = true;
  список.hidden = false;
  список.textContent = '';
  список.appendChild(историяСтрока('', 'Открываю…'));
  try {
    const ответ = await fetch('/api/settings/history', { cache: 'no-store' });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    нарисоватьИсторию(список, Array.isArray(данные.turns) ? данные.turns : []);
  } catch (e) {
    /* Форму могли закрыть или перерисовать, пока ждали ответ: тогда трогать
       нечего, иначе — говорим словами, а не белым списком. */
    if (настрЭлементы === эл && список.isConnected) {
      список.textContent = '';
      список.appendChild(историяСтрока('', 'Не получила историю: ' + e.message));
    }
  } finally {
    /* `настрТянем` общий для всей формы: забыть его здесь означало бы, что
       после одной неудачи пульт перестал сохранять настройки. */
    if (настрЭлементы === эл) настрТянем = false;
  }
}

/* Одна реплика: кто, время и весь текст. Текст — слова хозяина и её ответы,
   разметкой быть не может, поэтому `textContent`. */
function историяСтрока(роль, текст, время) {
  const el = document.createElement('div');
  el.className = 'история-реплика';
  const шапка = document.createElement('div');
  шапка.className = 'история-шапка';
  const кто = document.createElement('span');
  кто.className = 'история-кто';
  кто.textContent = роль;
  шапка.appendChild(кто);
  if (время) {
    const когда = document.createElement('span');
    когда.className = 'история-когда';
    когда.textContent = время;
    шапка.appendChild(когда);
  }
  el.appendChild(шапка);
  if (текст) {
    /* `pre-line` держит переносы хозяина: реплика могла прийти в несколько
       строк, и склеенная в одну читалась бы иначе, чем её сказали. */
    const тело = document.createElement('div');
    тело.className = 'история-текст';
    тело.textContent = текст;
    el.appendChild(тело);
  }
  return el;
}

/* Время реплики: «сегодня 17:04», «вчера 9:30», а на дату — «1.10 17:04».

  Мозг пишет в историю местное время без пояса (`datetime.now()`), поэтому
  браузер и разбирает его как местное — по часам того же компьютера, где шёл
  разговор. Считать по часам телефона нельзя: пояс у них может разойтись, и
  рядом с «сейчас» встал бы вчерашний разговор. */
function историяВремя(значение) {
  if (!значение) return '';
  const момент = new Date(значение);
  if (isNaN(момент.getTime())) return '';
  const сегодня = new Date();
  const вчера = new Date(сегодня.getTime() - 86400000);
  const часы = String(момент.getHours()).padStart(2, '0') + ':' + String(момент.getMinutes()).padStart(2, '0');
  if (момент.toDateString() === сегодня.toDateString()) return 'сегодня ' + часы;
  if (момент.toDateString() === вчера.toDateString()) return 'вчера ' + часы;
  return String(момент.getDate()).padStart(2, '0') + '.'
    + String(момент.getMonth() + 1).padStart(2, '0') + ' ' + часы;
}

function нарисоватьИсторию(список, реплики) {
  список.textContent = '';
  if (!реплики.length) {
    список.appendChild(историяСтрока('', 'История пуста. Скажи ей что-нибудь.'));
    return;
  }
  /* Старые сверху, новые снизу: так же, как шёл разговор, и сразу видно
     конец — его обычно и ищут. */
  реплики.forEach((one) => {
    const реплика = one || {};
    const текст = typeof реплика.content === 'string' ? реплика.content : '';
    if (!текст) return;
    const своя = реплика.role === 'user';
    список.appendChild(историяСтрока(своя ? 'Ты' : 'Труба', текст, историяВремя(реплика.at)));
  });
  if (!список.childElementCount) {
    список.appendChild(историяСтрока('', 'История пуста.'));
  }
  список.scrollTop = список.scrollHeight;
}

async function очиститьИсторию() {
  if (!настрЭлементы || настрТянем) return;
  if (!window.confirm('Очистить историю разговора? Память и характер останутся.')) return;
  настрТянем = true;
  настрЭлементы.очистить.disabled = true;
  настрСтатус('Чищу историю…', false);
  try {
    const ответ = await fetch('/api/settings/clear-history', { method: 'POST' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    разговор = [];
    перерисоватьЛенту();
    настрСтатус('История очищена', false);
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы) настрЭлементы.очистить.disabled = false;
  /* Список, если он открыт, после очистки показывал бы то, чего уже нет:
     перечитываем его начисто. Закрытым не трогаем — открывать заново без
     нажатия не будем. */
  if (настрЭлементы && настрЭлементы.историяСписок && !настрЭлементы.историяСписок.hidden) {
    показатьИсториюРазговора();
  }
}

async function забытьПамять() {
  if (!настрЭлементы || настрТянем) return;
  if (!window.confirm('Стереть все сохранённые факты о тебе? История разговора останется.')) return;
  const эл = настрЭлементы;
  настрТянем = true;
  эл.забыть.disabled = true;
  настрСтатус('Стираю память…', false);
  try {
    const ответ = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ memory: '' }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) {
      throw new Error(данные.error || (данные.errors || []).join('; ') || ('сервер ответил ' + ответ.status));
    }
    эл.memory.value = '';
    настрБаза.memory = '';
    настрОбновитьОтмену();
    настрСтатус('Память очищена', false);
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы === эл) эл.забыть.disabled = false;
}

function показатьОбразец() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const образец = настрОбразцы.find(x => x.name === эл.voice_name.value);
  эл.образецОписание.textContent = образец
    ? `${образец.seconds} с · ${образец.text || 'текст не распознан'}`
    : 'Образец не выбран';
  эл.образецИграть.disabled = !образец;
  эл.образецУдалить.disabled = !образец || настрОбразцы.length < 2;
}

function прослушатьОбразец() {
  if (!настрЭлементы || !настрЭлементы.voice_name.value) return;
  if (настрПлеер) настрПлеер.pause();
  настрПлеер = new Audio('/api/voice-samples/' + encodeURIComponent(настрЭлементы.voice_name.value) + '/audio');
  настрПлеер.play().catch(e => настрСтатус('Не удалось прослушать: ' + e.message, true));
}

/* «95», «95,5», «1:35», «1:02:05» → секунды. Непонятное — NaN. */
function секундыИз(текст) {
  const части = String(текст || '').trim().replace(',', '.').split(':');
  if (!части[0] || части.length > 3) return NaN;
  let итог = 0;
  for (const часть of части) {
    if (!/^\d+(\.\d+)?$/.test(часть)) return NaN;
    итог = итог * 60 + Number(часть);
  }
  return итог;
}
function какВремя(секунды) {
  const с = Math.max(0, Math.round(секунды));
  const ч = Math.floor(с / 3600), м = Math.floor(с % 3600 / 60), ост = с % 60;
  const мм = ч ? String(м).padStart(2, '0') : String(м);
  return (ч ? ч + ':' : '') + мм + ':' + String(ост).padStart(2, '0');
}
function образецСтатус(текст, плохо) {
  if (!настрЭлементы) return;
  настрЭлементы.образецШаг.textContent = текст;
  настрЭлементы.образецШаг.classList.toggle('плохо', !!плохо);
}
let образецДлительность = null;
function выбранФайлОбразца() {
  const эл = настрЭлементы;
  if (!эл) return;
  const файл = эл.образецФайл.files && эл.образецФайл.files[0];
  образецДлительность = null;
  эл.образецДобавить.disabled = !файл;
  if (!файл) {
    эл.образецФайлИнфо.textContent = '';
    образецСтатус('Сначала выбери запись', false);
    return;
  }
  эл.образецФайлИнфо.textContent = 'Выбрано: ' + файл.name;
  образецСтатус('Теперь нажми «Добавить голос»', false);
  // Длину узнаём у самого браузера — чтобы было видно, откуда резать.
  const ссылка = URL.createObjectURL(файл);
  const звук = new Audio();
  звук.preload = 'metadata';
  звук.onloadedmetadata = () => {
    if (Number.isFinite(звук.duration) && эл.образецФайл.files[0] === файл) {
      образецДлительность = звук.duration;
      эл.образецФайлИнфо.textContent = 'Выбрано: ' + файл.name + ' · длина ' + какВремя(звук.duration);
    }
    URL.revokeObjectURL(ссылка);
  };
  звук.onerror = () => URL.revokeObjectURL(ссылка);
  звук.src = ссылка;
}
function кусокОбразца() {
  const эл = настрЭлементы;
  const начало = секундыИз(эл.образецНачало.value);
  const длина = Number(String(эл.образецДлина.value).replace(',', '.'));
  if (!Number.isFinite(начало) || начало < 0) return { ошибка: 'Начало пиши секундами или минутами: 95 или 1:35' };
  if (!Number.isFinite(длина) || длина < 1 || длина > 12) return { ошибка: 'Длина куска — от 1 до 12 секунд' };
  if (образецДлительность !== null && начало >= образецДлительность) {
    return { ошибка: 'В записи всего ' + какВремя(образецДлительность) + ' — начало дальше конца' };
  }
  return { начало, длина };
}
let образецПлеерКуска = null;
function прослушатьКусок() {
  const эл = настрЭлементы;
  const файл = эл && эл.образецФайл.files && эл.образецФайл.files[0];
  if (!файл) { образецСтатус('Сначала выбери запись', true); return; }
  const кусок = кусокОбразца();
  if (кусок.ошибка) { образецСтатус(кусок.ошибка, true); return; }
  if (образецПлеерКуска) { образецПлеерКуска.pause(); URL.revokeObjectURL(образецПлеерКуска.src); }
  const звук = new Audio(URL.createObjectURL(файл));
  образецПлеерКуска = звук;
  звук.addEventListener('loadedmetadata', () => { звук.currentTime = кусок.начало; звук.play().catch(() => {}); }, { once: true });
  звук.addEventListener('timeupdate', () => {
    if (звук.currentTime >= кусок.начало + кусок.длина) звук.pause();
  });
  образецСтатус('Играет кусок с ' + какВремя(кусок.начало) + ', ' + кусок.длина + ' с. Подходит — жми «Добавить голос»', false);
}

async function добавитьОбразец() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const файл = эл.образецФайл.files && эл.образецФайл.files[0];
  if (!файл) { образецСтатус('Сначала выбери запись', true); return; }
  const форма = new FormData();
  форма.append('file', файл);
  if (эл.образецРучной.checked) {
    const кусок = кусокОбразца();
    if (кусок.ошибка) { образецСтатус(кусок.ошибка, true); return; }
    форма.append('manual', '1');
    форма.append('start', String(кусок.начало));
    форма.append('seconds', String(кусок.длина));
  }
  настрТянем = true;
  эл.образецДобавить.disabled = true;
  образецСтатус('Готовлю запись и распознаю текст — до минуты…', false);
  try {
    const ответ = await fetch('/api/voice-samples', { method: 'POST', body: форма });
    const данные = await ответ.json();
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || 'Не удалось добавить голос');
    const образец = данные.sample;
    настрОбразцы.push(образец);
    const пункт = document.createElement('option');
    пункт.value = образец.name;
    пункт.textContent = образец.name;
    эл.voice_name.appendChild(пункт);
    эл.voice_name.value = образец.name;
    // Файл не сбрасываем: можно тут же вырезать из той же записи другой кусок.
    показатьОбразец();
    образецСтатус('Готово: голос «' + образец.name + '» добавлен и выбран выше. ' +
      'Прослушай его и нажми «Сохранить» внизу — тогда она заговорит этим голосом.', false);
    настрСтатус('Нажми «Сохранить», чтобы выбрать новый голос', false);
  } catch (e) { образецСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы === эл) эл.образецДобавить.disabled = !(эл.образецФайл.files && эл.образецФайл.files[0]);
}

async function удалитьОбразец() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const имя = эл.voice_name.value;
  if (!имя || !window.confirm('Удалить образец голоса «' + имя + '»? Вернуть его можно будет только из исходной записи.')) return;
  настрТянем = true;
  эл.образецУдалить.disabled = true;
  try {
    const ответ = await fetch('/api/voice-samples/' + encodeURIComponent(имя), { method: 'DELETE' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || 'Не удалось удалить голос');
    настрОбразцы = данные.samples;
    эл.voice_name.querySelectorAll('option').forEach(option => { if (option.value === имя) option.remove(); });
    эл.voice_name.value = данные.selected || (настрОбразцы[0] && настрОбразцы[0].name) || '';
    показатьОбразец();
    настрСтатус('Образец удалён', false);
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы === эл) показатьОбразец();
}

/* Без интернета выбирать, где искать, незачем — прячем. Заодно гасим кнопку
   проверки: проверить нечего, и «Не вышло» только смутит. */
function настрПоляПоиска() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const вкл = эл.web_search.checked;
  for (const поле of [эл.web_search_mode, эл.web_search_budget, эл.search_sound]) поле.closest('.настр-ряд').hidden = !вкл;
  эл.проверитьПоиск.disabled = !вкл;
}

async function проверитьПоиск() {
  if (!настрЭлементы) return;
  const эл = настрЭлементы;
  const режим = эл.web_search_mode.value;
  эл.проверитьПоиск.disabled = true;
  настрСтатус(режим === 'paid' ? 'Ищу через OpenAI…' : 'Ищу…', false);
  try {
    const ответ = await fetch('/api/web/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: режим }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    const кто = данные.backend === 'openai' ? 'платный поиск OpenAI' : данные.backend;
    настрСтатус('Работает: ' + кто + ', ' + данные.took + ' с. ' +
      (данные.snippet || данные.title || ''), false);
  } catch (e) {
    настрСтатус('Не вышло: ' + e.message, true);
  }
  if (настрЭлементы === эл) настрПоляПоиска();
}

async function проверитьСвязь() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  настрТянем = true;
  эл.проверитьСвязь.disabled = true;
  настрСтатус('Проверяю связь коротким запросом…', false);
  try {
    const ответ = await fetch('/api/settings/test-provider', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: эл.provider.value, model: настрТекущаяМодель(), key: эл.api_key.value.trim() }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    настрСтатус('Связь есть. Ответ: ' + данные.answer, false);
  } catch (e) { настрСтатус('Связь не прошла: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы === эл) эл.проверитьСвязь.disabled = false;
}

async function послушатьПробу() {
  if (!настрЭлементы || настрТянем) return;
  const эл = настрЭлементы;
  const скорость = Number(эл.tts_speed.value.replace(',', '.'));
  const качество = Number(эл.tts_nfe.value.replace(',', '.'));
  if (!Number.isFinite(скорость) || скорость < 0.5 || скорость > 2) {
    настрСтатус('Скорость речи должна быть от 0.5 до 2', true); return;
  }
  if (эл.tts_engine.value === 'espeech' && (!Number.isInteger(качество) || качество < 4 || качество > 64)) {
    настрСтатус('Качество ESpeech должно быть от 4 до 64', true); return;
  }
  настрТянем = true;
  эл.послушатьПробу.disabled = true;
  настрСтатус('Готовлю пробу голоса…', false);
  try {
    const ответ = await fetch('/api/settings/voice-preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tts_engine: эл.tts_engine.value, silero_speaker: эл.silero_speaker.value.trim(),
        silero_model: эл.silero_model.value.trim(), voice_name: эл.voice_name.value,
        tts_speed: скорость, tts_nfe: качество }),
    });
    if (!ответ.ok) {
      const данные = await ответ.json().catch(() => ({}));
      throw new Error(данные.error || ('HTTP ' + ответ.status));
    }
    const звук = await ответ.blob();
    if (настрПробаUrl) URL.revokeObjectURL(настрПробаUrl);
    настрПробаUrl = URL.createObjectURL(звук);
    эл.плеерПроба.src = настрПробаUrl;
    эл.плеерПроба.hidden = false;
    try {
      await эл.плеерПроба.play();
      настрСтатус('Проба готова', false);
    } catch (e) { настрСтатус('Проба готова — нажми ▶ на проигрывателе', false); }
  } catch (e) { настрСтатус('Не вышло: ' + e.message, true); }
  настрТянем = false;
  if (настрЭлементы === эл) эл.послушатьПробу.disabled = false;
}

/* ---------- «О программе»: версия, автор и обновления ----------

   Сервер уже всё умеет: спрашивает GitHub, качает архив и ставит его в
   фоне. Здесь только слова для хозяина. Состояния с сервера (`newer`,
   `latest` и прочие) на экране не появляются — переводим их в текст в
   одном месте, `обнСловаПроверки`.

   Установка идёт мимо страницы, поэтому пока она идёт, раз в секунду
   спрашиваем `/api/update/status` и показываем шаги. Страница перерисовывается
   целиком при каждом открытии, поэтому всё, что нужно помнить между
   отрисовками, лежит в переменных ниже. */
let обнЗначокНужно = false;    // «вышла новая версия» — точка у «О программе»
let обнПроверка = null;        // последний ответ /api/update/check
let обнШаги = [];              // шаги установки
let обнИтог = null;            // итог установки от сервера
let обнИдёт = false;           // установка идёт прямо сейчас
let обнОпрос = null;           // таймер опроса шагов
/* Страница «О программе» — свой пункт меню, а не раздел общей формы: своя
   жизнь, свой набор узлов и своя занятость (у формы настроек она своя). */
let обнЭлементы = null;
let обнТянем = false;

/* Что Труба делает и куда что уходит. Хозяин (28.09): «немного написать, что
   программа делает и как себя ведёт». Абзацами, обычным текстом: разметку
   из интернета сюда не тащим, а текст этот — наш собственный. */
const ТЕКСТ_О_ПРОГРАММЕ = [
  'Труба — голосовой помощник для Windows. Слушает микрофон компьютера, думает в облаке (или на локальной модели), отвечает голосом — в колонки или через старый телефон, который становится динамиком и пультом.',
  'Что умеет: отвечать на вопросы и искать свежее в интернете, запускать и закрывать программы из списка, открывать YouTube, делать снимок экрана и смотреть на него, читать, переводить и разбирать скопированный текст, а ещё исправлять или переписывать его и класть обратно в буфер, читать документы (PDF, Word, текстовые файлы — открытый в программе, выделенный в проводнике, последний скачанный или найденный по названию): пересказать, ответить на вопрос по тексту или прочесть вслух, выключать и усыплять компьютер с голосовым подтверждением, сохранять игровой момент NVIDIA, записывать заметки голосом.',
  'Как себя ведёт: отзывается на имя «Труба»; после ответа несколько секунд слушает без имени — можно продолжать разговор. «Хватит» или касание круга на телефоне — замолчать. Пока звучит Discord, Telegram или видео, без имени не отвечает. Программы запускает только из твоего списка. Может сама заговорить, если включить это в настройках, — по умолчанию молчит.',
  'Что уходит в интернет: твои фразы и её ответы — в выбранный сервис ответов (через него же она выписывает память и приводит в порядок заметки); скопированный текст — только если просишь перевести его или разобрать (пересказать, объяснить простыми словами, найти ошибки, ответить по нему); текст документа — если просишь пересказать его или спрашиваешь по нему (чтение вслух не уходит); снимок экрана — когда просишь посмотреть или если включено «Иногда смотреть на экран»; поисковые запросы — в поисковики; погода — в Open-Meteo, если выбран город. Чтение скопированного и документов вслух, распознавание речи и голос работают на этом компьютере, память и заметки хранятся здесь же.',
  'Лицензия: PolyForm Noncommercial 1.0.0 — бесплатно пользоваться, изучать, переделывать и делиться можно; продавать и встраивать в платные продукты нельзя. Голоса Silero и Higgs — тоже только некоммерческие. Список сторонних лицензий — в файле THIRD_PARTY.md в папке Трубы.',
];

/* Точка «есть обновление» — у пункта «О программе» в меню. Подменю у него
   нет, поэтому точка одна: иначе пришлось бы проверять, где именно мы
   сейчас стоим. */
function обнПоказатьЗначок(нужно) {
  обнЗначокНужно = !!нужно;
  document.querySelectorAll('#меню .пункт[data-раздел="программа"]').forEach((пункт) => {
    пункт.classList.toggle('с-обновлением', обнЗначокНужно);
  });
}

/* Одно место, где `state` сервера становится словами. */
function обнСловаПроверки(проверка) {
  if (!проверка || !проверка.state) return 'Ещё не проверяла — нажми «Проверить обновления»';
  if (проверка.state === 'newer') return 'Вышла версия ' + (проверка.latest || '');
  if (проверка.state === 'latest') return 'У тебя последняя версия';
  if (проверка.state === 'ahead') return 'У тебя версия новее опубликованной';
  if (проверка.state === 'no_repo') {
    return 'Репозиторий на GitHub ещё не опубликован — обновляться пока неоткуда';
  }
  if (проверка.state === 'error') return проверка.error || 'Проверка не получилась';
  return 'Ответ сервера не понял: ' + проверка.state;
}

function elобнПлохо(узел, плохо) {
  if (узел && узел.classList) узел.classList.toggle('плохо', !!плохо);
}

/* Шапка, строка состояния и блок выпуска. Рисуется из уже известного:
   либо из ответа `/api/about` (пульт проверяет обновления сам при запуске —
   тогда нового запроса не делаем), либо из ответа на кнопку. */
function обнНарисовать(эл, данные) {
  if (!эл) return;
  if (данные) {
    if (данные.version) эл.обнВерсия.textContent = 'версия ' + данные.version;
    if (данные.author) эл.обнАвтор.textContent = 'автор — ' + данные.author;
  }
  const новая = обнПроверка && обнПроверка.state === 'newer';
  const естьВыпуск = обнПроверка &&
    (обнПроверка.state === 'newer' || обнПроверка.state === 'latest');
  if (!обнИдёт && !обнИтог) {
    эл.обнСтатус.textContent = обнСловаПроверки(обнПроверка);
    elобнПлохо(эл.обнСтатус, !!(обнПроверка && обнПроверка.state === 'error'));
  }
  // После нажатия «Проверить» показываем состав выпуска и тем, у кого он
  // уже стоит: иначе GitHub присылает заметки, а хозяин их не видит.
  эл.обнБлок.hidden = !естьВыпуск && !обнИдёт && !обнИтог;
  эл.обнПоставить.hidden = !новая;
  if (естьВыпуск) {
    эл.обнНазвание.textContent = 'Что изменилось в версии ' + (обнПроверка.latest || '');
    /* Заметки с GitHub — только текстом. Там может быть что угодно, а
       пульт показывает это хозяину как его собственный текст. */
    эл.обнЗаметки.textContent = обнПроверка.notes || 'Заметок к выпуску нет.';
    if (новая) эл.обнПоставить.textContent = 'Обновить до ' + (обнПроверка.latest || '');
  } else {
    эл.обнНазвание.textContent = '';
    эл.обнЗаметки.textContent = '';
  }
  обнНарисоватьШаги(эл);
  обнНарисоватьИтог(эл);
}

function обнНарисоватьШаги(эл) {
  эл.обнШаги.textContent = '';
  const видно = обнИдёт && обнШаги.length > 0;
  эл.обнШаги.hidden = !видно;
  for (let номер = 0; номер < обнШаги.length; номер++) {
    const шаг = document.createElement('div');
    // Последний шаг — тот, что идёт сейчас: его и подсвечиваем.
    шаг.className = номер === обнШаги.length - 1 ? 'обн-шаг обн-шаг-текущий' : 'обн-шаг';
    шаг.textContent = обнШаги[номер];
    эл.обнШаги.appendChild(шаг);
  }
}

function обнНарисоватьИтог(эл) {
  if (!обнИтог) {
    эл.обнИтог.hidden = true;
    эл.обнИтог.textContent = '';
    эл.обнПерезапуск.hidden = true;
    return;
  }
  эл.обнИтог.hidden = false;
  if (обнИтог.ok) {
    эл.обнИтог.textContent = 'Обновлено до ' + (обнИтог.to || (обнПроверка && обнПроверка.latest) || '');
    elобнПлохо(эл.обнИтог, false);
    // Перезапуск нужен всегда: файлы новой версии записаны, а в памяти
    // пульт ещё старый.
    эл.обнПерезапуск.hidden = false;
  } else {
    let текст = обнИтог.error || 'Обновление не получилось';
    if (обнИтог.rolled_back) текст += ' — Вернула прежнюю версию';
    эл.обнИтог.textContent = текст;
    elобнПлохо(эл.обнИтог, true);
    эл.обнПерезапуск.hidden = true;
  }
}

function обнБлокировать(эл, занято) {
  // На время установки обе кнопки молчат: второй «Обновить» сервер всё
  // равно не пустит, а хозяину — лишняя надежда.
  эл.обнПроверитьКнопка.disabled = занято;
  эл.обнПоставить.disabled = занято;
}

/* Открыли «О программе»: спросим сервер, что он знает о версии. Если пульт
   уже проверял обновления при запуске, результат лежит в `checked.last` —
   показываем его, нового похода на GitHub не делаем. */
async function загрузитьПрограмму() {
  const эл = обнЭлементы;
  if (!эл) return;
  try {
    const ответ = await fetch('/api/about', { cache: 'no-store' });
    const данные = await ответ.json();
    if (обнЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    const известное = данные.checked || {};
    if (известное.last) {
      обнПроверка = известное.last;
      обнШаги = Array.isArray(известное.steps) ? известное.steps : [];
      обнИтог = известное.result || null;
      // Пульт мог начать установку до того, как хозяин открыл страницу.
      обнИдёт = !!известное.running;
    }
    // Галочка «Проверять обновления при запуске» живёт здесь и сохраняется
    // сразу при переключении: общей формы на этой странице нет.
    эл.обнГалочка.checked = данные.update_check !== false;
    обнНарисовать(эл, данные);
    обнПоказатьЗначок(!!(обнПроверка && обнПроверка.state === 'newer'));
    обнБлокировать(эл, обнИдёт);
    if (обнИдёт) обнНачатьОпрос();
  } catch (e) {
    if (обнЭлементы !== эл) return;
    эл.обнВерсия.textContent = 'версия не узналась';
    эл.обнСтатус.textContent = 'Не вышло узнать про обновления: ' + e.message;
    elобнПлохо(эл.обнСтатус, true);
  }
}

async function проверитьОбновления() {
  const эл = обнЭлементы;
  if (!эл || обнТянем) return;
  обнТянем = true;
  эл.обнПроверитьКнопка.disabled = true;
  эл.обнСтатус.textContent = 'Спрашиваю GitHub…';
  elобнПлохо(эл.обнСтатус, false);
  try {
    const ответ = await fetch('/api/update/check', { method: 'POST' });
    const данные = await ответ.json();
    if (обнЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    // Новая проверка — прежняя установка больше не в тему: иначе старый
    // итог «Обновлено до…» остался бы висеть под новым выпуском.
    обнПроверка = данные;
    обнИтог = null;
    обнНарисовать(эл, null);
    // Хозяин сам нажал «Проверить обновления» и смотрит результат — плашка
    // поверх этого лишняя.
    обнПлашкаПоказана = true;
    обнПоказатьЗначок(данные.state === 'newer');
  } catch (e) {
    if (обнЭлементы !== эл) return;
    эл.обнСтатус.textContent = 'Не вышло проверить: ' + e.message;
    elобнПлохо(эл.обнСтатус, true);
  }
  обнТянем = false;
  if (обнЭлементы === эл) обнБлокировать(эл, обнИдёт);
}

async function поставитьОбновление() {
  const эл = обнЭлементы;
  if (!эл || обнТянем || обнИдёт) return;
  if (!обнПроверка || обнПроверка.state !== 'newer') {
    эл.обнСтатус.textContent = 'Сначала проверь обновления';
    elобнПлохо(эл.обнСтатус, true);
    return;
  }
  обнТянем = true;
  обнИдёт = true;
  обнИтог = null;
  обнШаги = [];
  обнБлокировать(эл, true);
  обнНарисовать(эл, null);
  try {
    const ответ = await fetch('/api/update/install', { method: 'POST' });
    const данные = await ответ.json().catch(() => ({}));
    if (обнЭлементы !== эл) return;
    if (!данные || !данные.ok) {
      /* Отказ сервера — обычно 409: уже идёт, не проверено или это рабочая
         папка разработки. Слова его и показываем, свои не выдумываем.
         Отказ идёт в тот же итог, что и неудачная установка, — иначе
         следующая отрисовка сейчас же затрёт его строкой состояния. */
      обнИдёт = false;
      обнИтог = { ok: false, error: (данные && данные.error)
        || ('Не вышло начать обновление (' + ответ.status + ')') };
      обнНарисовать(эл, null);
      обнБлокировать(эл, false);
      return;
    }
  } catch (e) {
    if (обнЭлементы !== эл) return;
    обнИдёт = false;
    обнИтог = { ok: false, error: 'Не вышло начать обновление: ' + e.message };
    обнНарисовать(эл, null);
    обнБлокировать(эл, false);
    return;
  }
  обнТянем = false;
  if (обнЭлементы === эл) {
    обнНарисовать(эл, null);
    обнНачатьОпрос();
  }
}

/* Шаги установки приходят с сервера по одной штуке в секунду — так хозяин
   видит, что дело идёт, а не зависло. Останавливаемся, как только сервер
   сказал, что установка кончилась. */
function обнНачатьОпрос() {
  if (обнОпрос !== null) clearInterval(обнОпрос);
  обнОпрос = setInterval(опроситьУстановку, 1000);
  опроситьУстановку();
}

function обнОстановитьОпрос() {
  if (обнОпрос !== null) clearInterval(обнОпрос);
  обнОпрос = null;
}

async function опроситьУстановку() {
  const эл = обнЭлементы;
  if (!обнИдёт || !эл) { обнОстановитьОпрос(); return; }
  try {
    const ответ = await fetch('/api/update/status', { cache: 'no-store' });
    const данные = await ответ.json();
    if (обнЭлементы !== эл) { обнОстановитьОпрос(); return; }
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    обнШаги = Array.isArray(данные.steps) ? данные.steps : [];
    if (!данные.running) {
      обнИдёт = false;
      обнОстановитьОпрос();
      /* Итог приходит сюда же. Если сервер не сказал ничего, показываем
         это честно, а не «обновлено». */
      обнИтог = данные.result || { ok: false, error: 'Обновление закончилось, а ответа нет' };
      if (данные.last) обнПроверка = данные.last;
      // Версия стала новее: точка у Настроек больше не нужна.
      if (обнИтог.ok) обнПоказатьЗначок(false);
    }
    обнБлокировать(эл, обнИдёт);
    обнНарисовать(эл, null);
  } catch (e) {
    if (обнЭлементы !== эл) { обнОстановитьОпрос(); return; }
    // Сеть моргнула — один раз покажем и продолжим ждать: установка идёт
    // на сервере, от нашего запроса она не зависит.
    эл.обнСтатус.textContent = 'Связь с сервером моргнула — продолжаю ждать';
    elобнПлохо(эл.обнСтатус, true);
  }
}

async function перезапуститьПульт() {
  const эл = обнЭлементы;
  if (!эл) return;
  эл.обнПерезапуск.disabled = true;
  try {
    const ответ = await fetch('/api/update/restart', { method: 'POST' });
    const данные = await ответ.json().catch(() => ({}));
    if (обнЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    // Пульт сейчас закроется и откроется сам: ждать нечего, только
    // предупредить хозяина, чтобы он не думал, что всё зависло.
    эл.обнИтог.textContent = 'Перезапускаю…';
    elобнПлохо(эл.обнИтог, false);
    эл.обнПерезапуск.disabled = true;
  } catch (e) {
    if (обнЭлементы !== эл) return;
    эл.обнПерезапуск.disabled = false;
    эл.обнИтог.textContent = 'Перезапуск не получился: ' + e.message;
    elобнПлохо(эл.обнИтог, true);
  }
}

/* Значок «есть обновление» должен появиться вместе с пультом, а не после
   того, как хозяин сам догадается заглянуть в настройки.

   Проверка при запуске идёт с задержкой (`updater.START_DELAY`), а пульт
   открывается раньше — поэтому одного запроса `/api/about` мало: он был бы
   сделан до того, как сервер узнал ответ. Смотрим `/api/about` повторно, пока
   результата нет (он на GitHub не ходит, только читает известное серверу).
   Пульт, открытый позже, увидит готовый результат с первой попытки.

   Что делать с результатом — одно место, `обнРешениеПроверки`: молчание при
   ошибке, при выключенной проверке и когда новой версии нет. Ложное «есть
   обновление» хозяину страшнее, чем отсутствие уведомления. */
let обнПлашкаПоказана = false;   // в этом запуске плашку уже показывали
let обнПлашкаУзел = null;
const ОБН_ПОПЫТКИ = 10;          // десять раз по десять секунд — две минуты
const ОБН_ПАУЗА = 10000;

/* '' — молчим; 'точка' — новая версия есть, но плашку уже показывали;
   'плашка' — сказать хозяину один раз. */
function обнРешениеПроверки(последняя, плашкаБыла) {
  if (!последняя || последняя.state !== 'newer') return '';
  return плашкаБыла ? 'точка' : 'плашка';
}

async function проверитьЗначокПриЗапуске() {
  for (let попытка = 0; попытка < ОБН_ПОПЫТКИ; попытка++) {
    let данные;
    try {
      const ответ = await fetch('/api/about', { cache: 'no-store' });
      данные = await ответ.json();
      if (!ответ.ok || !данные || !данные.ok) return;
    } catch (e) {
      // Нет связи — уведомления не будет, но и пульт из-за этого не падает.
      return;
    }
    const известное = данные.checked || {};
    const решение = обнРешениеПроверки(известное.last, обнПлашкаПоказана);
    if (решение) {
      обнПоказатьЗначок(true);
      if (решение === 'плашка') обнПоказатьПлашку(известное.last);
      return;   // результат получен — дальше спрашивать незачем
    }
    if (известное.last) return;              // проверили, новой версии нет
    if (данные.update_check === false) return; // выключено — ждать нечего
    // После последней попытки ждать незачем: дальше запросов не будет.
    if (попытка + 1 < ОБН_ПОПЫТКИ) {
      await new Promise((окончить) => setTimeout(окончить, ОБН_ПАУЗА));
    }
  }
}

/* Плашка в углу пульта: номер версии и кнопка «О программе». Установку не
   предлагаем — хозяин сам решает, когда обновляться (хост: не навязывать). */
function обнПоказатьПлашку(последняя) {
  обнПлашкаПоказана = true;
  if (обнПлашкаУзел) return;
  const плашка = document.createElement('div');
  плашка.className = 'обн-плашка';
  плашка.setAttribute('role', 'status');
  const текст = document.createElement('span');
  текст.textContent = 'Вышла версия ' + (последняя.latest || '')
    + ' — что изменилось, в «О программе».';
  const кнопка = document.createElement('button');
  кнопка.type = 'button';
  кнопка.className = 'голос-кнопка';
  кнопка.textContent = 'О программе';
  кнопка.addEventListener('click', () => {
    обнСкрытьПлашку();
    открыть('программа');
  });
  const закрыть = document.createElement('button');
  закрыть.type = 'button';
  закрыть.className = 'обн-плашка-скрыть';
  закрыть.title = 'Скрыть';
  закрыть.textContent = '×';
  закрыть.addEventListener('click', () => обнСкрытьПлашку());
  плашка.append(текст, кнопка, закрыть);
  document.body.appendChild(плашка);
  обнПлашкаУзел = плашка;
}

/* Плашка живёт один раз: закрыли крестиком, нажали кнопку или открыли
   «О программе» сами — второй раз она не появляется. */
function обнСкрытьПлашку() {
  if (обнПлашкаУзел && обнПлашкаУзел.remove) обнПлашкаУзел.remove();
  обнПлашкаУзел = null;
}

/* ---------- «О программе»: свой пункт меню ----------
   Хозяин (28.09): «О программе надо выставить отдельным пунктом, без вкладки
   „Настройки“». Здесь версия, автор, обновления, текст о том, что программа
   делает и куда что уходит, галочка проверки обновлений (сохраняется сразу) и
   кнопка «Пройти первую настройку заново». «Поддержать автора» хозяин убрал
   («навязчиво») — сердечко внизу меню осталось. */
function нарисоватьОПрограмме() {
  обнОстановитьОпрос();
  // Хозяин открыл «О программе» сам — плашка ему больше не нужна.
  обнПлашкаПоказана = true;
  обнСкрытьПлашку();
  обнЭлементы = null;
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.add('настроечный');
  лист.innerHTML = '';
  const корень = document.createElement('div');
  корень.className = 'обн-страница';
  лист.appendChild(корень);
  const обнШапка = document.createElement('div');
  обнШапка.className = 'обн-шапка';
  const обнШапкаРяд = document.createElement('div');
  обнШапкаРяд.className = 'обн-шапка-ряд';
  const обнИмя = document.createElement('div');
  обнИмя.className = 'обн-имя';
  обнИмя.textContent = 'Труба';
  const обнВерсия = document.createElement('div');
  обнВерсия.className = 'обн-строка';
  обнВерсия.textContent = 'версия …';
  const обнАвтор = document.createElement('div');
  обнАвтор.className = 'обн-строка';
  обнАвтор.textContent = 'автор — …';
  обнШапкаРяд.append(обнИмя, обнВерсия, обнАвтор);
  обнШапка.appendChild(обнШапкаРяд);
  корень.appendChild(обнШапка);
  const обнРяд = document.createElement('div');
  обнРяд.className = 'обн-ряд';
  const обнСтатус = document.createElement('span');
  обнСтатус.className = 'обн-статус';
  обнСтатус.textContent = 'Ещё не проверяла — нажми «Проверить обновления»';
  const обнПроверитьКнопка = document.createElement('button');
  обнПроверитьКнопка.type = 'button';
  обнПроверитьКнопка.className = 'голос-кнопка';
  обнПроверитьКнопка.textContent = 'Проверить обновления';
  обнПроверитьКнопка.title = 'Один поход на GitHub; настройки не меняются';
  обнРяд.append(обнСтатус, обнПроверитьКнопка);
  корень.appendChild(обнРяд);
  const обнБлок = document.createElement('div');
  обнБлок.className = 'обн-блок';
  обнБлок.hidden = true;
  const обнНазвание = document.createElement('div');
  обнНазвание.className = 'обн-название';
  const обнЗаметки = document.createElement('div');
  обнЗаметки.className = 'обн-заметки';
  const обнПоставить = document.createElement('button');
  обнПоставить.type = 'button';
  обнПоставить.className = 'голос-кнопка главная';
  const обнШаги = document.createElement('div');
  обнШаги.className = 'обн-шаги';
  обнШаги.hidden = true;
  const обнИтог = document.createElement('div');
  обнИтог.className = 'обн-итог';
  обнИтог.hidden = true;
  const обнПерезапуск = document.createElement('button');
  обнПерезапуск.type = 'button';
  обнПерезапуск.className = 'голос-кнопка главная';
  обнПерезапуск.textContent = 'Перезапустить пульт';
  обнПерезапуск.title = 'Пульт закроется и откроется сам';
  обнПерезапуск.hidden = true;
  обнБлок.append(обнНазвание, обнЗаметки, обнПоставить, обнШаги, обнИтог, обнПерезапуск);
  корень.appendChild(обнБлок);
  /* Галочка проверки обновлений — вне общей формы настроек, поэтому шлётся
     сразу при переключении: своей кнопки «Сохранить» у страницы нет. */
  const обнРядГалочки = document.createElement('label');
  обнРядГалочки.className = 'обн-галочка';
  const обнГалочка = document.createElement('input');
  обнГалочка.type = 'checkbox';
  обнГалочка.className = 'настр-галочка';
  обнГалочка.checked = true;
  const обнГалочкаТекст = document.createElement('span');
  обнГалочкаТекст.textContent = 'Проверять обновления при запуске';
  обнРядГалочки.append(обнГалочка, обнГалочкаТекст);
  корень.appendChild(обнРядГалочки);
  /* Текст о программе — константа в коде, абзац за абзацем, без разметки
     извне: единственный источник, который тут вообще не с сервера. */
  const текстБлок = document.createElement('div');
  текстБлок.className = 'обн-текст';
  for (const абзац of ТЕКСТ_О_ПРОГРАММЕ) {
    const строка = document.createElement('p');
    строка.className = 'обн-абзац';
    строка.textContent = абзац;
    текстБлок.appendChild(строка);
  }
  корень.appendChild(текстБлок);
  const мастерРяд = document.createElement('div');
  мастерРяд.className = 'обн-ряд';
  const мастерЕщё = document.createElement('button');
  мастерЕщё.type = 'button';
  мастерЕщё.className = 'голос-кнопка';
  мастерЕщё.textContent = 'Пройти первую настройку заново';
  мастерЕщё.title = 'Откроет мастер первого запуска с первого шага';
  мастерРяд.appendChild(мастерЕщё);
  корень.appendChild(мастерРяд);
  обнЭлементы = { обнВерсия, обнАвтор, обнСтатус, обнПроверитьКнопка, обнБлок,
    обнНазвание, обнЗаметки, обнПоставить, обнШаги, обнИтог, обнПерезапуск, обнГалочка };
  обнПроверитьКнопка.addEventListener('click', проверитьОбновления);
  обнПоставить.addEventListener('click', поставитьОбновление);
  обнПерезапуск.addEventListener('click', перезапуститьПульт);
  мастерЕщё.addEventListener('click', () => мастерОткрыть());
  обнГалочка.addEventListener('change', сохранитьПроверкуОбновлений);
  загрузитьПрограмму();
}

/* Галочка «Проверять обновления при запуске» ушла с общей формы, поэтому
   сохраняется сама: `POST /api/settings {update_check}`. */
async function сохранитьПроверкуОбновлений() {
  if (!обнЭлементы) return;
  const эл = обнЭлементы;
  const хотим = !!эл.обнГалочка.checked;
  эл.обнСтатус.textContent = 'Сохраняю…';
  elобнПлохо(эл.обнСтатус, false);
  try {
    const ответ = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ update_check: хотим }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (обнЭлементы !== эл) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    эл.обнСтатус.textContent = хотим
      ? 'Буду проверять обновления при запуске пульта'
      : 'Обновления проверять не буду';
  } catch (e) {
    if (обнЭлементы !== эл) return;
    // Сервер не принял — возвращаем галочку: иначе она врала бы до следующего
    // открытия пульта.
    эл.обнГалочка.checked = !хотим;
    эл.обнСтатус.textContent = 'Не вышло сохранить: ' + e.message;
    elобнПлохо(эл.обнСтатус, true);
  }
}

function нарисоватьНастройки() {
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.add('настроечный');
  лист.innerHTML = '';
  построитьНастройки(лист, 'настройки');
}

/* Текст для записи образца хозяина. Раньше он жил на странице «Проверка»,
   а образец записывается в «Голос → Слух» — туда ему и место. */
const ТЕКСТ_ОБРАЗЦА =
  'Ну что, давай знакомиться как следует.\n\nЗа окном опять зарядил дождь, я сижу перед двумя мониторами\n' +
  'с чашкой остывшего чая, и мне лень куда-то идти.\n\nЖёлтый шмель жужжит над шершавой щепкой, а рыжий кот дрыхнет\n' +
  'на широком подоконнике. Шестьдесят три, двести сорок восемь.\n\n' +
  'Запусти дискорд, сохрани момент, покажи что там на экране —\n' +
  'вот это я и буду тебе говорить каждый день.';

/* --- Перенос настроек: файл, который уносит Трубу на другой компьютер ---
   Один набор функций на оба места — «Настройки → Система» и первый шаг
   мастера: слова вокруг разные, работа одна. Блок собирается один раз, а
   места зовут его сами: мастеру экспорт не нужен, там только перенос из
   файла. */

/* Блок переноса в `куда`. `экспорт` — рисувать ли «Сохранить в файл» с
   «Показать в папке»: в мастере настроек ещё нечего сохранять. */
function переносБлок(куда, заголовок, пояснение, экспорт) {
  const блок = document.createElement('div');
  блок.className = 'перенос-блок';
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = заголовок;
  блок.appendChild(шапка);
  const объяснение = document.createElement('div');
  объяснение.className = 'настр-описание';
  объяснение.textContent = пояснение || '';
  блок.appendChild(объяснение);
  const части = document.createElement('div');
  блок.appendChild(части);
  const свод = document.createElement('div');
  свод.className = 'настр-описание перенос-свод';
  блок.appendChild(свод);
  const ряд = document.createElement('div');
  ряд.className = 'настр-телряд';
  блок.appendChild(ряд);
  /* Родной выбор файла спрятан, как у записи образца голоса: кнопка только
     открывает его, а тело запроса уходит на разбор целиком. */
  const файл = document.createElement('input');
  файл.type = 'file';
  файл.accept = '.zip';
  файл.className = 'образец-файл';
  блок.appendChild(файл);
  const статус = document.createElement('div');
  статус.className = 'настр-статус';
  блок.appendChild(статус);
  const эл = { блок, части, свод, файл, статус, галочки: {},
    путь: '', токен: '', ждёт: false, сохр: null, показать: null };
  if (экспорт) {
    эл.сохр = document.createElement('button');
    эл.сохр.type = 'button';
    эл.сохр.className = 'голос-кнопка главная';
    эл.сохр.textContent = 'Сохранить в файл';
    ряд.appendChild(эл.сохр);
    эл.сохр.addEventListener('click', () => переносЭкспорт(эл));
    эл.показать = document.createElement('button');
    эл.показать.type = 'button';
    эл.показать.className = 'голос-кнопка';
    эл.показать.textContent = 'Показать в папке';
    /* Появляется после сохранения: показывать нечего. */
    эл.показать.hidden = true;
    ряд.appendChild(эл.показать);
    эл.показать.addEventListener('click', () => переносПоказать(эл));
  }
  эл.перенести = document.createElement('button');
  эл.перенести.type = 'button';
  эл.перенести.className = 'голос-кнопка';
  эл.перенести.textContent = 'Перенести из файла…';
  ряд.appendChild(эл.перенести);
  эл.перенести.addEventListener('click', () => файл.click());
  эл.применить = document.createElement('button');
  эл.применить.type = 'button';
  эл.применить.className = 'голос-кнопка главная';
  эл.применить.textContent = 'Перенести';
  /* Пока не разобрали файл — переносить нечего. */
  эл.применить.hidden = true;
  ряд.appendChild(эл.применить);
  эл.применить.addEventListener('click', () => переносПрименить(эл));
  файл.addEventListener('change', () => переносИмпорт(эл));
  куда.appendChild(блок);
  return эл;
}

/* Текст под кнопками. Ошибка — классом `плохо`, как у остальных строк
   пульта: хозяину надо видеть, что сервер сказал, а не молчание. */
function переносСказать(эл, текст, плохо) {
  if (!эл || !эл.статус) return;
  эл.статус.textContent = текст || '';
  эл.статус.classList.toggle('плохо', !!плохо);
}

/* Пока идёт запрос, кнопки неактивны: два щелчка подряд означали бы две
   пачки настроек или два применения. Вернулись — снова можно. */
function переносЖдёт(эл, ждёт) {
  if (!эл) return;
  эл.ждёт = !!ждёт;
  for (const кнопка of [эл.сохр, эл.перенести, эл.применить, эл.показать]) {
    if (кнопка) кнопка.disabled = !!ждёт;
  }
}

/* Причина отказа сервера — его словами, а не «что-то пошло не так». */
function переносОшибка(данные, ответ) {
  const текст = данные && (данные.error
    || (Array.isArray(данные.errors) ? данные.errors.join('; ') : ''));
  return текст || ('сервер ответил ' + ((ответ && ответ.status) || 'непонятно'));
}

/* Галочки частей. `отметить` решает, что отмечено по умолчанию: при
   экспорте — всё, кроме ключей облака; при импорте — всё, что в файле. */
function переносГалочки(эл, части, отметить) {
  if (!эл) return;
  эл.части.textContent = '';
  эл.галочки = {};
  for (const часть of Array.isArray(части) ? части : []) {
    if (!часть || !часть.id) continue;
    const ряд = document.createElement('label');
    ряд.className = 'настр-ряд';
    const галочка = document.createElement('input');
    галочка.type = 'checkbox';
    галочка.className = 'настр-галочка';
    галочка.value = часть.id;
    галочка.checked = отметить ? !!отметить(часть) : true;
    const имя = document.createElement('span');
    имя.className = 'настр-имя';
    имя.textContent = часть.title || часть.id;
    ряд.appendChild(галочка);
    ряд.appendChild(имя);
    const подпись = document.createElement('span');
    подпись.className = 'настр-намёк';
    /* Ключи — единственная часть, которую лучше не класть в файл без
       нужды: в файле будет ключ доступа к облаку. */
    подпись.textContent = часть.id === 'keys'
      ? 'в файле будет ключ доступа к облаку — никому его не отправляй'
      : (часть.size || '');
    ряд.appendChild(подпись);
    эл.части.appendChild(ряд);
    эл.галочки[часть.id] = галочка;
  }
}

/* Что хозяин отметил — по галочкам, а не по памяти: он их и смотрел. */
function переносОтмеченные(эл) {
  const свои = [];
  for (const [имя, галочка] of Object.entries(эл.галочки || {})) {
    if (галочка.checked) свои.push(имя);
  }
  return свои;
}

/* Что переносится с этой машины: спрашиваем у сервера и рисуем галочки. */
async function переносЧастиЗагрузить(эл) {
  if (!эл) return;
  try {
    const ответ = await fetch('/api/transfer/parts', { cache: 'no-store' });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error(переносОшибка(данные, ответ));
    }
    переносГалочки(эл, данные.parts, (часть) => часть.id !== 'keys');
    переносСказать(эл, 'Отметь, что переносить, и нажми «Сохранить в файл»');
  } catch (e) {
    переносСказать(эл, 'Не вышло спросить, что переносится: '
      + ((e && e.message) ? e.message : e), true);
  }
}

/* Собрать файл переноса из отмеченного. */
async function переносЭкспорт(эл) {
  if (!эл || эл.ждёт) return;
  const части = переносОтмеченные(эл);
  if (!части.length) {
    переносСказать(эл, 'Отметь, что переносить', true);
    return;
  }
  переносЖдёт(эл, true);
  try {
    const ответ = await fetch('/api/transfer/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parts: части }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error(переносОшибка(данные, ответ));
    }
    эл.путь = данные.path || '';
    if (эл.показать) {
      эл.показать.hidden = !эл.путь;
      эл.показать.disabled = false;
    }
    переносСказать(эл, 'Сохранено: ' + (эл.путь || 'файл переноса'));
  } catch (e) {
    переносСказать(эл, 'Не вышло сохранить: ' + ((e && e.message) ? e.message : e), true);
  } finally {
    переносЖдёт(эл, false);
  }
}

/* Показать сохранённый файл в проводнике — путь берём из ответа экспорта,
   руками его никто не вводит. */
async function переносПоказать(эл) {
  if (!эл || эл.ждёт) return;
  if (!эл.путь) {
    переносСказать(эл, 'Сначала сохрани файл', true);
    return;
  }
  переносЖдёт(эл, true);
  try {
    const ответ = await fetch('/api/transfer/reveal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: эл.путь }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error(переносОшибка(данные, ответ));
    }
    переносСказать(эл, 'Показал в папке: ' + (данные.path || эл.путь));
  } catch (e) {
    переносСказать(эл, 'Не вылось показать: ' + ((e && e.message) ? e.message : e), true);
  } finally {
    переносЖдёт(эл, false);
  }
}

/* Одна строка о том, что в файле: сколько частей, версия Трубы и когда файл
   сделан. Хозяин переносит на новую машину и должен понять, из чего это
   сделано, ещё до применения. */
function переносСвод(данные) {
  const куски = [];
  const сколько = Array.isArray(данные.parts) ? данные.parts.length : 0;
  куски.push('в файле частей: ' + сколько);
  if (данные.version) куски.push('Труба ' + данные.version);
  if (данные.created) куски.push('файл от ' + данные.created);
  if (данные.size) куски.push(данные.size);
  return куски.join(' · ');
}

/* Разбор файла: сам ZIP уходит телом запроса (он большой и бинарный), а
   приходит список частей и токен. Галочки рисуются сразу — хозяин решает,
   что из этого переносить, ещё до применения. */
async function переносИмпорт(эл) {
  if (!эл || эл.ждёт) return;
  const файл = эл.файл.files && эл.файл.files[0];
  if (!файл) {
    переносСказать(эл, 'Сначала выбери файл переноса', true);
    return;
  }
  переносЖдёт(эл, true);
  try {
    const ответ = await fetch('/api/transfer/inspect', {
      method: 'POST', headers: { 'Content-Type': 'application/zip' },
      body: файл,
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error(переносОшибка(данные, ответ));
    }
    эл.токен = данные.token || '';
    эл.свод.textContent = переносСвод(данные);
    переносГалочки(эл, данные.parts);
    if (эл.применить) эл.применить.hidden = !эл.токен;
    переносСказать(эл, 'Отметь, что переносить, и нажми «Перенести»');
  } catch (e) {
    эл.токен = '';
    if (эл.применить) эл.применить.hidden = true;
    переносСказать(эл, 'Не вышло прочесть файл: '
      + ((e && e.message) ? e.message : e), true);
  } finally {
    переносЖдёт(эл, false);
  }
}

/* Применение. Пульт после этого перезапускается, а мастер больше не
   откроется (сервер ставит `first_run_done`) — поэтому спрашиваем один раз
   и прямо говорим, что будет. */
async function переносПрименить(эл) {
  if (!эл || эл.ждёт) return;
  const части = переносОтмеченные(эл);
  if (!эл.токен) {
    переносСказать(эл, 'Сначала выбери файл переноса', true);
    return;
  }
  if (!части.length) {
    переносСказать(эл, 'Отметь, что переносить', true);
    return;
  }
  if (!window.confirm('Текущие настройки будут заменены (копия сохранится). '
    + 'Пульт перезапустится. Продолжить?')) return;
  переносЖдёт(эл, true);
  try {
    const ответ = await fetch('/api/transfer/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: эл.токен, parts: части }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error(переносОшибка(данные, ответ));
    }
    переносСказать(эл, 'Переношу… пульт перезапустится');
  } catch (e) {
    переносСказать(эл, 'Не вышло перенести: ' + ((e && e.message) ? e.message : e), true);
  } finally {
    переносЖдёт(эл, false);
  }
}

/* Форма настроек целиком — в `куда`. Видны секции только этой страницы. */
function построитьНастройки(куда, страница) {
  настрСтраница = страница;
  const корень = document.createElement('div');
  корень.className = 'настройки';
  куда.appendChild(корень);
  const навигация = document.createElement('div');
  навигация.className = 'настр-навигация';
  const раздел = настрВыбор(НАСТР_РАЗДЕЛЫ.map(([код, название]) => [код, название]), настрТекущийРаздел);
  const пояснение = document.createElement('span');
  пояснение.className = 'настр-пояснение';
  навигация.appendChild(пояснение);
  корень.appendChild(навигация);
  const содержимое = document.createElement('div');
  корень.appendChild(содержимое);
  const мозг = настрСекция(содержимое, 'ответы', 'Ответы и подключение');
  const provider = настрВыбор([], '');
  настрПоле(мозг, 'provider', provider);
  // Предупреждение о локальной модели — под выбором сервиса, чтобы хозяин
  // прочитал его до первого ответа. Прячется, пока выбран не локальный.
  const локальноеПредупреждение = document.createElement('div');
  локальноеПредупреждение.className = 'настр-локальное-предупреждение';
  локальноеПредупреждение.textContent = ЛОКАЛЬНАЯ_ПРЕДУПРЕЖДЕНИЕ;
  мозг.appendChild(локальноеПредупреждение);
  // Адрес локального сервера — только для локального: облаку он не нужен.
  const local_url = настрВвод('');
  local_url.placeholder = 'http://127.0.0.1:11434/v1';
  local_url.autocomplete = 'off';
  const адресПоле = document.createElement('div');
  // Своё поле, а не «модельное»: три кнопки рядом сжимали ввод до «http».
  адресПоле.className = 'настр-модель-поле настр-адрес-поле';
  адресПоле.appendChild(local_url);
  for (const [имя, адрес] of ЛОКАЛЬНЫЕ_АДРЕСА) {
    const кнопка = document.createElement('button');
    кнопка.type = 'button';
    кнопка.className = 'настр-иконка настр-адрес-кнопка';
    кнопка.textContent = имя;
    кнопка.title = 'Вписать адрес: ' + адрес;
    кнопка.setAttribute('aria-label', 'Вписать адрес ' + имя);
    кнопка.addEventListener('click', () => { local_url.value = адрес; });
    адресПоле.appendChild(кнопка);
  }
  настрПоле(мозг, 'local_url', адресПоле);
  const local_urlРяд = local_url.closest('.настр-ряд');
  const model = настрВвод('');
  model.placeholder = 'Впиши ID модели или выбери из списка';
  model.autocomplete = 'off';
  const списокМоделей = document.createElement('datalist');
  списокМоделей.id = 'настр-список-моделей';
  model.setAttribute('list', списокМоделей.id);
  const модельПоле = document.createElement('div');
  модельПоле.className = 'настр-модель-поле';
  модельПоле.appendChild(model);
  модельПоле.appendChild(списокМоделей);
  const обновитьМодели = document.createElement('button');
  обновитьМодели.type = 'button';
  обновитьМодели.className = 'настр-иконка';
  обновитьМодели.textContent = '↻';
  обновитьМодели.title = 'Обновить список моделей';
  обновитьМодели.setAttribute('aria-label', 'Обновить список моделей');
  модельПоле.appendChild(обновитьМодели);
  настрПоле(мозг, 'model', модельПоле);
  const моделиСтатус = document.createElement('div');
  моделиСтатус.className = 'настр-модели-статус';
  моделиСтатус.setAttribute('aria-live', 'polite');
  мозг.appendChild(моделиСтатус);
  const api_key = document.createElement('input');
  api_key.type = 'password';
  api_key.className = 'настр-ввод';
  api_key.autocomplete = 'new-password';
  api_key.placeholder = 'новый ключ…';
  api_key.setAttribute('aria-label', 'Новый ключ доступа');
  const рядКлюча = document.createElement('div');
  рядКлюча.className = 'настр-ряд';
  const имяКлюча = document.createElement('span');
  имяКлюча.className = 'настр-имя';
  имяКлюча.textContent = 'Ключ доступа';
  рядКлюча.appendChild(имяКлюча);
  const ключПоле = document.createElement('div');
  ключПоле.className = 'настр-ключ-поле';
  ключПоле.appendChild(api_key);
  const ключПоказать = document.createElement('button');
  ключПоказать.type = 'button';
  ключПоказать.className = 'настр-иконка';
  ключПоказать.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  ключПоказать.title = 'Показать ключ';
  ключПоказать.setAttribute('aria-label', 'Показать ключ');
  ключПоле.appendChild(ключПоказать);
  рядКлюча.appendChild(ключПоле);
  const ключНамёк = document.createElement('span');
  ключНамёк.className = 'настр-намёк';
  ключНамёк.textContent = '';
  рядКлюча.appendChild(ключНамёк);
  мозг.appendChild(рядКлюча);
  const temperature = настрЧислоПоле('');
  настрПоле(мозг, 'temperature', temperature);
  настрПолзунок(temperature, 0, 1.5, 0.05);
  const max_tokens = настрЧислоПоле('');
  настрПоле(мозг, 'max_tokens', max_tokens);
  const history_turns = настрЧислоПоле('');
  настрПоле(мозг, 'history_turns', history_turns);
  настрПолзунок(history_turns, 0, 50, 2);
  const hedge = настрГалочка(false);
  настрПоле(мозг, 'hedge', hedge);

  /* Поиск в интернете — свой раздел: раньше он лежал вместе со всем
     остальным и тонул среди полей про модель. */
  const поиск = настрСекция(содержимое, 'поиск', 'Поиск');
  const web_search = настрГалочка(true);
  настрПоле(поиск, 'web_search', web_search);
  const search_sound = настрГалочка(true);
  настрПоле(поиск, 'search_sound', search_sound);
  const web_search_mode = настрВыбор([
    ['free', 'Бесплатно'],
    ['auto', 'Бесплатно, не вышло — платно'],
    ['paid', 'Платно · около 1 ₽ за поиск'],
  ], 'free');
  настрПоле(поиск, 'web_search_mode', web_search_mode);
  const web_search_budget = настрЧислоПоле('');
  настрПоле(поиск, 'web_search_budget', web_search_budget);
  настрПолзунок(web_search_budget, 4, 30, 1);

  /* Когда Труба начинает разговор сама — тоже свой раздел. */
  const первая = настрСекция(содержимое, 'первой', 'Сама заговаривает');
  const proactive = настрВыбор(ЧАСТОТЫ_ПЕРВОЙ, 'never');
  настрПоле(первая, 'proactive', proactive);
  const proactive_look = настрГалочка(true);
  настрПоле(первая, 'proactive_look', proactive_look);
  // Галочка про экран бессмысленна, пока заходы выключены: смотреть некуда.
  const настрПоляПроактивности = () => {
    proactive_look.disabled = proactive.value === 'never';
  };
  proactive.addEventListener('change', настрПоляПроактивности);
  настрПоляПроактивности();
  const характер = настрСекция(содержимое, 'характер', 'Характер');
  const характерПояснение = document.createElement('div');
  характерПояснение.className = 'настр-описание';
  характерПояснение.textContent = 'Инструкция, которая влияет на манеру каждого ответа. Списки и разметку ей неудобно читать вслух.';
  характер.appendChild(характерПояснение);
  const характерКарточки = document.createElement('div');
  характерКарточки.className = 'характер-карточки';
  характер.appendChild(характерКарточки);
  const характерНамёк = document.createElement('div');
  характерНамёк.className = 'настр-описание';
  характерНамёк.textContent = 'Можно выбрать готовый и поправить под себя. Имя «Труба» менять не надо — на него она откликается.';
  характер.appendChild(характерНамёк);
  const persona = document.createElement('textarea');
  // Характер — длинный текст: при шести строках его приходилось каждый раз
  // растягивать мышкой (хозяин 27.09 — «в три раза выше»).
  persona.className = 'настр-текст настр-текст-высокий';
  persona.rows = 6;
  характер.appendChild(persona);
  const отменитьХарактер = document.createElement('button');
  отменитьХарактер.type = 'button';
  отменитьХарактер.className = 'голос-кнопка настр-перечитать';
  отменитьХарактер.textContent = 'Отменить правки';
  отменитьХарактер.title = 'Вернуть характер к тому, что сохранено';
  характер.appendChild(отменитьХарактер);
  const память = настрСекция(содержимое, 'память', 'Память');
  const памятьПояснение = document.createElement('div');
  памятьПояснение.className = 'настр-описание';
  /* Два разных хранилища, и их путают чаще всего: память — короткие факты
     о хозяине (одна строка — один факт), история — последние реплики
     разговора дословно, их показывает кнопка ниже. Счётчик «N реплик в
     разговоре» на Панели считает именно записи истории, а не пары. */
  памятьПояснение.textContent = 'Здесь — короткие факты о тебе. История — последние реплики: твои слова и ответы Трубы. Счётчик на панели показывает количество отдельных реплик.';
  память.appendChild(памятьПояснение);
  const memory = document.createElement('textarea');
  memory.className = 'настр-текст';
  memory.rows = 6;
  память.appendChild(memory);
  const действияПамяти = document.createElement('div');
  действияПамяти.className = 'настр-телряд';
  память.appendChild(действияПамяти);
  const отменитьПамять = document.createElement('button');
  отменитьПамять.type = 'button';
  отменитьПамять.className = 'голос-кнопка';
  отменитьПамять.textContent = 'Отменить правки';
  отменитьПамять.title = 'Вернуть память к тому, что сохранено';
  действияПамяти.appendChild(отменитьПамять);
  const забыть = document.createElement('button');
  забыть.type = 'button';
  забыть.className = 'голос-кнопка';
  забыть.textContent = 'Забыть все факты';
  забыть.title = 'Стереть факты о тебе. История разговора останется';
  действияПамяти.appendChild(забыть);
  /* Просмотр истории — только по нажатию: показывать её каждый раз, как
     открывается раздел, незачем, а список длинный. Рядом с «Очистить
     историю», чтобы обе кнопки про историю стояли вместе. */
  const показатьИсторию = document.createElement('button');
  показатьИсторию.type = 'button';
  показатьИсторию.className = 'голос-кнопка';
  показатьИсторию.textContent = 'Показать историю';
  показатьИсторию.title = 'Показать последние реплики разговора. Ничего не стирает';
  действияПамяти.appendChild(показатьИсторию);
  const очистить = document.createElement('button');
  очистить.type = 'button';
  очистить.className = 'голос-кнопка';
  очистить.textContent = 'Очистить историю';
  очистить.title = 'Стереть историю разговора. Факты и характер останутся';
  действияПамяти.appendChild(очистить);
  const историяСписок = document.createElement('div');
  историяСписок.className = 'история-список';
  историяСписок.hidden = true;
  память.appendChild(историяСписок);

  const голосСекция = настрСекция(содержимое, 'голос', 'Озвучивание');
  /* «Твой компьютер» — первым в «Озвучивании» (хозяин 28.09: «логичнее наверх,
     чтобы сразу было понятно, какую модель качать можно»): одна строка с
     коротким итогом, стрелка и разворот. Внутри — строки железа, советы и
     кнопка постановки качественных голосов, без которых ESpeech и Higgs в
     списке способов озвучивания выключены. */
  const железоКарточка = document.createElement('div');
  железоКарточка.className = 'железо-карточка';
  const железоКнопка = document.createElement('button');
  железоКнопка.type = 'button';
  железоКнопка.className = 'железо-шапка';
  железоКнопка.title = 'Показать, что за компьютер';
  железоКнопка.setAttribute('aria-expanded', 'false');
  const железоИмя = document.createElement('span');
  железоИмя.className = 'железо-имя';
  железоИмя.textContent = 'Твой компьютер';
  const железоИтог = document.createElement('span');
  железоИтог.className = 'железо-итог';
  железоИтог.textContent = 'Смотрю, что за компьютер…';
  const железоСтрелка = document.createElement('span');
  железоСтрелка.className = 'железо-стрелка';
  железоСтрелка.setAttribute('aria-hidden', 'true');
  железоСтрелка.textContent = '▸';
  /* Ход загрузки моделей или установки библиотек — в шапке, пока карточка
     свёрнута (02.10, хозяин: «тут нет нифига» — полоса была только внутри).
     Развёрнута — шапка снова про железо, а ход виден полосой (CSS). */
  const железоХод = document.createElement('span');
  железоХод.className = 'железо-ход';
  железоКнопка.append(железоИмя, железоИтог, железоХод, железоСтрелка);
  железоКарточка.appendChild(железоКнопка);
  const железоТело = document.createElement('div');
  железоТело.className = 'железо-тело';
  железоКарточка.appendChild(железоТело);
  const железоБлок = document.createElement('div');
  железоБлок.className = 'железо-блок';
  железоБлок.textContent = 'Смотрю, что за компьютер…';
  железоТело.appendChild(железоБлок);
  /* Качественные голоса — выбор моделей: по строке на модель, с галочкой,
     размером и состоянием. Хозяин 02.10 спросил «почему нет выбора?», а раньше
     тут была одна кнопка установки. Строки строятся один раз за форму, а
     состояние и полосу обновляет опрос (`показатьГолоса`). */
  const голосаБлок = document.createElement('div');
  голосаБлок.className = 'железо-голоса';
  const голосаИмя = document.createElement('div');
  голосаИмя.className = 'железо-голоса-имя';
  голосаИмя.textContent = 'Качественные голоса';
  const голосаСтроки = document.createElement('div');
  голосаСтроки.className = 'железо-голоса-строки';
  const голосаРяды = {};
  for (const [модель, имя, подпись] of ВЕСА_ГОЛОСОВ) {
    const ряд = document.createElement('label');
    ряд.className = 'железо-голоса-строка';
    const отметка = document.createElement('input');
    отметка.type = 'checkbox';
    отметка.className = 'железо-голоса-галка';
    const текст = document.createElement('span');
    текст.className = 'железо-голоса-текст';
    const назван = document.createElement('b');
    назван.textContent = имя;
    текст.appendChild(назван);
    текст.appendChild(document.createTextNode(' — ' + подпись));
    const состояние = document.createElement('span');
    состояние.className = 'железо-голоса-состояние';
    состояние.textContent = 'не скачана';
    ряд.append(отметка, текст, состояние);
    голосаСтроки.appendChild(ряд);
    голосаРяды[модель] = { модель, узел: ряд, отметка, состояние };
  }
  const голосаПолосаМесто = document.createElement('div');
  голосаПолосаМесто.className = 'голоса-полоса-место';
  голосаПолосаМесто.hidden = true;
  // Установка библиотек рисуется рядом со скачиванием моделей: хозяин нажал
  // одну кнопку, и ход обеих частей виден здесь же, с «Отменить».
  const голосаБибПолосаМесто = document.createElement('div');
  голосаБибПолосаМесто.className = 'голоса-полоса-место';
  голосаБибПолосаМесто.hidden = true;
  голосаБлок.append(голосаИмя, голосаСтроки, голосаПолосаМесто,
                    голосаБибПолосаМесто);
  железоТело.appendChild(голосаБлок);
  const железоРяд = document.createElement('div');
  железоРяд.className = 'настр-телряд';
  const железоГолоса = document.createElement('button');
  железоГолоса.type = 'button';
  железоГолоса.className = 'голос-кнопка главная';
  железоГолоса.textContent = 'Скачать выбранное';
  железоГолоса.title = 'Качать отмеченные модели здесь же, в пульте, с полоской';
  железоГолоса.hidden = true;
  const железоГолосаСтатус = document.createElement('span');
  железоГолосаСтатус.className = 'настр-статус';
  железоГолосаСтатус.textContent = '';
  железоРяд.append(железоГолоса, железоГолосаСтатус);
  железоТело.appendChild(железоРяд);
  голосСекция.appendChild(железоКарточка);
  const tts_engine = настрВыбор([
    ['silero', 'Silero · без видеокарты'],
    ['espeech', 'ESpeech · по образцу'],
    ['higgs', 'Higgs · по образцу'],
  ], 'silero');
  настрПоле(голосСекция, 'tts_engine', tts_engine);
  const silero_speaker = настрВвод('');
  настрПоле(голосСекция, 'silero_speaker', silero_speaker);
  const silero_model = настрВвод('');
  настрПоле(голосСекция, 'silero_model', silero_model);
  const образецБлок = document.createElement('div');
  образецБлок.hidden = true;
  голосСекция.appendChild(образецБлок);
  const voice_name = настрВыбор([], '');
  настрПоле(образецБлок, 'voice_name', voice_name);
  const образецОписание = document.createElement('div');
  образецОписание.className = 'настр-адреса';
  образецОписание.textContent = '…';
  образецБлок.appendChild(образецОписание);
  const образецДействия = document.createElement('div');
  образецДействия.className = 'настр-телряд';
  образецБлок.appendChild(образецДействия);
  const образецИграть = document.createElement('button');
  образецИграть.type = 'button'; образецИграть.className = 'голос-кнопка';
  образецИграть.textContent = 'Прослушать';
  образецДействия.appendChild(образецИграть);
  const образецУдалить = document.createElement('button');
  образецУдалить.type = 'button'; образецУдалить.className = 'голос-кнопка';
  образецУдалить.textContent = 'Удалить образец';
  образецДействия.appendChild(образецУдалить);
  const образецЗаголовок = document.createElement('div');
  образецЗаголовок.className = 'образец-заголовок';
  образецЗаголовок.textContent = 'Новый голос из записи';
  образецБлок.appendChild(образецЗаголовок);
  /* Не «всё в одну строку», а три пронумерованных шага (хозяин 28.09: «всё
     в одну строку напихано»): кружок с цифрой слева, как в мастерах. Шаги 2 и
     3 приглушены, пока не выбран файл, — но не спрятаны: видно весь путь. */
  const образецШаги = document.createElement('div');
  образецШаги.className = 'образец-шаги';
  образецБлок.appendChild(образецШаги);
  const образецШагРяд = (номер, подпись) => {
    const ряд = document.createElement('div');
    ряд.className = 'образец-шаг';
    const кружок = document.createElement('span');
    кружок.className = 'образец-номер';
    кружок.textContent = номер;
    const тело = document.createElement('div');
    тело.className = 'образец-шаг-тело';
    const имя = document.createElement('div');
    имя.className = 'образец-шаг-имя';
    имя.textContent = подпись;
    тело.appendChild(имя);
    ряд.append(кружок, тело);
    образецШаги.appendChild(ряд);
    return { ряд, кружок, тело };
  };
  /* Шаг 1. Родной выбор файла спрятан, но он тот же самый `образецФайл` с теми
     же обработчиками: кнопка только открывает его, а имя файла и длительность
     показывает браузер в строке под ней. */
  const шагВыбор = образецШагРяд('1', 'Выбери запись');
  const образецВыбрать = document.createElement('button');
  образецВыбрать.type = 'button'; образецВыбрать.className = 'голос-кнопка';
  образецВыбрать.textContent = 'Выбрать запись…';
  образецВыбрать.title = 'Откроется выбор файла: wav, mp3, flac, ogg, m4a';
  шагВыбор.тело.appendChild(образецВыбрать);
  const образецФайл = document.createElement('input');
  образецФайл.type = 'file'; образецФайл.accept = '.wav,.mp3,.flac,.ogg,.m4a,audio/*';
  образецФайл.className = 'образец-файл';
  образецВыбрать.addEventListener('click', () => образецФайл.click());
  шагВыбор.тело.appendChild(образецФайл);
  const образецФайлИнфо = document.createElement('div');
  образецФайлИнфо.className = 'образец-файл-инфо';
  шагВыбор.тело.appendChild(образецФайлИнфо);
  const образецПодсказка = document.createElement('div');
  образецПодсказка.className = 'образец-подсказка';
  образецПодсказка.textContent = 'подойдёт любая, хоть часовой стрим — wav, mp3, flac, ogg, m4a. '
    // Лицензия Higgs прямо запрещает копировать голос без согласия его
    // хозяина — и это просто честно по отношению к людям.
    + 'Только свой голос или голос человека, который разрешил его копировать.';
  шагВыбор.тело.appendChild(образецПодсказка);
  /* Шаг 2. Два варианта переключателем: «сама найдёт кусок» (по умолчанию) или
     «вырезать вручную» с полями секунды и длины. Галочку `образецРучной`
     оставили и спрятали: на неё смотрит отправка образца, — она значит ровно
     то же, что и раньше, а радио с ней синхронны. */
  const шагКусок = образецШагРяд('2', 'Кусок речи');
  const образецПереключатель = document.createElement('div');
  образецПереключатель.className = 'образец-переключатель';
  образецПереключатель.setAttribute('role', 'radiogroup');
  образецПереключатель.setAttribute('aria-label', 'Откуда взять кусок речи');
  const образецРучной = document.createElement('input');
  образецРучной.type = 'checkbox'; образецРучной.className = 'настр-галочка образец-скрытый';
  образецРучной.tabIndex = -1;
  образецРучной.setAttribute('aria-hidden', 'true');
  const вариант = (значение, подпись, проверен) => {
    const строка = document.createElement('label');
    строка.className = 'образец-вариант';
    const переключатель = document.createElement('input');
    переключатель.type = 'radio';
    переключатель.name = 'образец-режим';
    переключатель.value = значение;
    переключатель.className = 'настр-галочка';
    переключатель.checked = !!проверен;
    строка.append(переключатель, document.createTextNode(подпись));
    образецПереключатель.appendChild(строка);
    return переключатель;
  };
  const образецСам = вариант('сам', 'Найти чистый кусок сама', true);
  const образецВручную = вариант('вручную', 'Вырезать вручную', false);
  образецПереключатель.appendChild(образецРучной);
  шагКусок.тело.appendChild(образецПереключатель);
  const образецНарезка = document.createElement('div');
  образецНарезка.className = 'настр-телряд'; образецНарезка.hidden = true;
  // Начало — текстом: «95» и «1:35» значат одно и то же.
  const образецНачало = настрВвод('0');
  образецНачало.inputMode = 'decimal';
  образецНачало.placeholder = '0 или 1:35';
  образецНачало.classList.add('настр-число');
  const образецДлина = настрЧислоПоле('9');
  образецДлина.step = '0.5'; образецДлина.min = '1'; образецДлина.max = '12';
  образецНарезка.appendChild(document.createTextNode('С секунды'));
  образецНарезка.appendChild(образецНачало);
  образецНарезка.appendChild(document.createTextNode('Длина, с (до 12)'));
  образецНарезка.appendChild(образецДлина);
  const образецКусок = document.createElement('button');
  образецКусок.type = 'button'; образецКусок.className = 'голос-кнопка';
  образецКусок.textContent = 'Прослушать кусок';
  образецНарезка.appendChild(образецКусок);
  /* Нарезка — под вариантом «Вырезать вручную», внутри шага 2. */
  шагКусок.тело.appendChild(образецНарезка);
  /* Шаг 3. Кнопка та же и неактивна, пока файл не выбран; статус под ней. */
  const шагДобавить = образецШагРяд('3', 'Добавить');
  const образецДобавить = document.createElement('button');
  образецДобавить.type = 'button'; образецДобавить.className = 'голос-кнопка главная образец-добавить';
  образецДобавить.textContent = 'Добавить голос';
  образецДобавить.disabled = true;
  шагДобавить.тело.appendChild(образецДобавить);
  const образецШаг = document.createElement('div');
  образецШаг.className = 'настр-статус образец-статус';
  образецШаг.textContent = 'Сначала выбери запись';
  шагДобавить.тело.appendChild(образецШаг);
  /* Шаги 2 и 3 приглушены, пока файл не выбран; выбранный файл — галочка в
     кружке первого шага. Радио и спрятанная галочка `образецРучной` — одно и
     то же: на галочку смотрят отправка образца и показ полей нарезки. */
  const образецПоляОбновить = () => {
    const есть = !!(образецФайл.files && образецФайл.files.length);
    шагВыбор.ряд.classList.toggle('готов', есть);
    шагВыбор.кружок.textContent = есть ? '✓' : '1';
    шагКусок.ряд.classList.toggle('приглушён', !есть);
    шагДобавить.ряд.classList.toggle('приглушён', !есть);
    образецВручную.checked = образецРучной.checked;
    образецСам.checked = !образецРучной.checked;
    образецНарезка.hidden = !образецРучной.checked;
  };
  for (const переключатель of [образецСам, образецВручную]) {
    переключатель.addEventListener('change', () => {
      образецРучной.checked = образецВручную.checked;
      образецРучной.dispatchEvent(new Event('change'));
      образецПоляОбновить();
    });
  }
  образецФайл.addEventListener('change', образецПоляОбновить);
  образецПоляОбновить();
  const tts_speed = настрЧислоПоле('');
  настрПоле(голосСекция, 'tts_speed', tts_speed);
  настрПолзунок(tts_speed, 0.7, 1.3, 0.05);
  const tts_nfe = настрЧислоПоле('');
  настрПоле(голосСекция, 'tts_nfe', tts_nfe);
  настрПолзунок(tts_nfe, 8, 64, 1);
  const tts_gap = настрЧислоПоле('');
  настрПоле(голосСекция, 'tts_gap', tts_gap);
  настрПолзунок(tts_gap, 0, 1, 0.05);
  const voice_volume = настрЧислоПоле('');
  настрПоле(голосСекция, 'voice_volume', voice_volume);
  настрПолзунок(voice_volume, 1, 10, 1);
  const higgs_gentle = настрГалочка(true);
  настрПоле(голосСекция, 'higgs_gentle', higgs_gentle);
  const duck_level = настрЧислоПоле('');
  настрПоле(голосСекция, 'duck_level', duck_level);
  настрПолзунок(duck_level, 0, 1, 0.05);
  const barge_in_level = настрЧислоПоле('');
  настрПоле(голосСекция, 'barge_in_level', barge_in_level);
  настрПолзунок(barge_in_level, 0.01, 0.3, 0.01);
  const barge_instant = настрГалочка(true);
  настрПоле(голосСекция, 'barge_instant', barge_instant);
  /* «Твой компьютер» уехал наверх секции, к «Способу озвучивания» (см. выше):
     сразу видно, какую модель качать можно, и не листать до конца. */
  const послушатьПробуКнопка = document.createElement('button');
  послушатьПробуКнопка.type = 'button';
  послушатьПробуКнопка.className = 'голос-кнопка настр-проверка';
  послушатьПробуКнопка.textContent = 'Послушать пробу';
  послушатьПробуКнопка.title = 'Проба прозвучит на компьютере';
  const плеерПроба = document.createElement('audio');
  плеерПроба.controls = true;
  плеерПроба.hidden = true;
  плеерПроба.className = 'настр-плеер';
  голосСекция.appendChild(плеерПроба);
  const слух = настрСекция(содержимое, 'слух', 'Слух');
  // Режим слуха здесь не дублируем: он переключается строкой вверху
  // «Голоса», на Панели и с телефона. Второе поле в форме молча
  // возвращало бы старый режим при «Сохранить».
  const voice_autostart = настрГалочка(true);
  настрПоле(слух, 'voice_autostart', voice_autostart);
  const follow_up_window = настрЧислоПоле('');
  настрПоле(слух, 'follow_up_window', follow_up_window);
  настрПолзунок(follow_up_window, 10, 600, 10);
  const require_name_when_noisy = настрГалочка(false);
  настрПоле(слух, 'require_name_when_noisy', require_name_when_noisy);
  const voice_app_guard = настрГалочка(true);
  настрПоле(слух, 'voice_app_guard', voice_app_guard);
  const owner_only = настрГалочка(false);
  настрПоле(слух, 'owner_only', owner_only);
  const owner_threshold = настрЧислоПоле('');
  настрПоле(слух, 'owner_threshold', owner_threshold);
  настрПолзунок(owner_threshold, 0, 1, 0.01);
  const владелец = document.createElement('div');
  владелец.className = 'настр-адреса';
  владелец.textContent = '…';
  слух.appendChild(владелец);
  /* Образец голоса живёт рядом с «Только хозяин» и «Строгость узнавания»:
     это и есть те два поля, ради которых он записывается. Имена — с
     «хозяин», а не с «образец»: `образец*` в этой же функции уже заняты
     блоком нового голоса из записи. */
  const хозяинЗаголовок = document.createElement('div');
  хозяинЗаголовок.className = 'образец-заголовок';
  хозяинЗаголовок.textContent = 'Образец твоего голоса';
  слух.appendChild(хозяинЗаголовок);
  const хозяинОписание = document.createElement('div');
  хозяинОписание.className = 'настр-адреса';
  хозяинОписание.textContent = 'Нужен для «Только хозяин»: так Труба отличает твой голос '
    + 'от колонок, Discord и других людей. Читать вслух около 40 секунд, обычным голосом, '
    + 'с обычного места';
  слух.appendChild(хозяинОписание);
  // Текст для чтения виден только пока идёт запись: до неё он лишний.
  const хозяинТекст = document.createElement('div');
  хозяинТекст.className = 'пров-текст';
  хозяинТекст.textContent = ТЕКСТ_ОБРАЗЦА;
  хозяинТекст.hidden = true;
  слух.appendChild(хозяинТекст);
  const хозяинРяд = document.createElement('div');
  хозяинРяд.className = 'настр-телряд';
  const хозяинЗаписать = document.createElement('button');
  хозяинЗаписать.type = 'button';
  хозяинЗаписать.className = 'голос-кнопка главная';
  хозяинЗаписать.textContent = 'Записать мой голос';
  хозяинЗаписать.title = 'Запишет твой голос — Труба станет отличать тебя от колонок и других людей';
  const хозяинЗакончить = document.createElement('button');
  хозяинЗакончить.type = 'button';
  хозяинЗакончить.className = 'голос-кнопка';
  хозяинЗакончить.textContent = 'Закончить';
  хозяинЗакончить.disabled = true;
  const хозяинСтатус = document.createElement('span');
  хозяинСтатус.className = 'настр-статус';
  хозяинСтатус.textContent = '';
  хозяинРяд.appendChild(хозяинЗаписать);
  хозяинРяд.appendChild(хозяинЗакончить);
  хозяинРяд.appendChild(хозяинСтатус);
  слух.appendChild(хозяинРяд);
  const хозяинИтог = document.createElement('div');
  хозяинИтог.className = 'пров-итог';
  хозяинИтог.hidden = true;
  слух.appendChild(хозяинИтог);
  /* Распознавание речи — свой раздел «Голоса»: список моделей приходит с
     сервера (`/api/stt`), а пояснение и размер под ним меняются вместе с
     выбором. */
  const расп = настрСекция(содержимое, 'распознавание', 'Распознавание');
  const распВступление = document.createElement('div');
  распВступление.className = 'настр-описание';
  распВступление.textContent = 'Какая модель разбирает твою речь. Меняется на ходу: '
    + 'Труба перезагрузит её сама, а текущую фразу доскажет старой.';
  расп.appendChild(распВступление);
  const stt_model = настрВыбор([], '');
  настрПоле(расп, 'stt_model', stt_model);
  const распПояснение = document.createElement('div');
  распПояснение.className = 'настр-описание';
  распПояснение.textContent = '…';
  расп.appendChild(распПояснение);
  const stt_quantization = настрВыбор([['int8', 'Сжатая — быстрее'], ['none', 'Полная — точнее, но медленнее']], 'int8');
  настрПоле(расп, 'stt_quantization', stt_quantization);
  const распРяд = document.createElement('div');
  распРяд.className = 'настр-телряд';
  const распПроверить = document.createElement('button');
  распПроверить.type = 'button';
  распПроверить.className = 'голос-кнопка';
  распПроверить.textContent = 'Проверить распознавание';
  распПроверить.title = 'Запишет около 4 секунд с микрофона и покажет, что услышала Труба';
  const распСтатус = document.createElement('span');
  распСтатус.className = 'настр-статус';
  распСтатус.textContent = '…';
  распРяд.appendChild(распПроверить);
  распРяд.appendChild(распСтатус);
  расп.appendChild(распРяд);
  const распИтог = document.createElement('div');
  распИтог.className = 'пров-итог';
  распИтог.hidden = true;
  расп.appendChild(распИтог);
  /* Звук — свой подраздел «Голоса», последний: откуда Труба слышит, куда
     говорит и как это проверить. Поле «Куда её голос» переехало сюда из
     «Телефона» (там вместо него осталась строка-указатель), поэтому в форме
     оно одно, и «Сохранить» шлёт ровно один `output`. */
  const звук = настрСекция(содержимое, 'звук', 'Звук');
  const звукВступление = document.createElement('div');
  звукВступление.className = 'настр-описание';
  звукВступление.textContent = 'Выбери микрофон и куда идёт её голос — как в настройках звука игры.';
  звук.appendChild(звукВступление);
  const mic_name = настрВыбор([], '');
  настрПоле(звук, 'mic_name', mic_name);
  const mic_channel = настрВыбор([['0', 'Вход 1'], ['1', 'Вход 2']], '0');
  настрПоле(звук, 'mic_channel', mic_channel);
  const mic_channelРяд = mic_channel.closest('.настр-ряд');
  const уровень = document.createElement('div');
  уровень.className = 'звук-уровень';
  const уровеньFill = document.createElement('i');
  уровень.appendChild(уровеньFill);
  настрПоле(звук, 'уровень', уровень,
    'Показывает микрофон, который слушает сейчас. Выбрала другой — сохрани, и он заработает после перезапуска голоса');
  // Полоска идёт во всю ширину поля, а подпись с намёком — под ней.
  уровень.closest('.настр-ряд').classList.add('с-уровнем');
  const уровеньПодпись = document.createElement('div');
  уровеньПодпись.className = 'звук-уровень-подпись';
  уровеньПодпись.textContent = '';
  звук.appendChild(уровеньПодпись);
  const output = настрВыбор([['speakers', 'Колонки'], ['phone', 'Телефон']], 'speakers');
  настрПоле(звук, 'output', output);
  const speaker_name = настрВыбор([], '');
  настрПоле(звук, 'speaker_name', speaker_name);
  const звукРяд = document.createElement('div');
  звукРяд.className = 'настр-телряд';
  const звукПроверить = document.createElement('button');
  звукПроверить.type = 'button';
  звукПроверить.className = 'голос-кнопка';
  звукПроверить.textContent = 'Записать 3 секунды';
  звукПроверить.title = 'Запишет 3 секунды с выбранного микрофона. '
    + 'Пока голос включён, микрофон занят им — смотри на полоску уровня';
  /* Прослушивание — второе нажатие, а не продолжение записи: запись
     проигрывалась один раз мимо хозяина, и проверить было нечем. */
  const звукПрослушать = document.createElement('button');
  звукПрослушать.type = 'button';
  звукПрослушать.className = 'голос-кнопка';
  звукПрослушать.textContent = 'Прослушать';
  звукПрослушать.title = 'Проиграет запись в выбранные колонки';
  звукПрослушать.disabled = true;
  const звукСтатус = document.createElement('span');
  звукСтатус.className = 'настр-статус';
  звукСтатус.textContent = '';
  звукРяд.append(звукПроверить, звукПрослушать, звукСтатус);
  звук.appendChild(звукРяд);
  /* Строка результата записи — под кнопками и отдельно от статуса: полоска
     уровня показывает живой микрофон, а эта — что услышала запись. */
  const звукЗапись = document.createElement('div');
  звукЗапись.className = 'звук-уровень-подпись';
  звукЗапись.textContent = '';
  звук.appendChild(звукЗапись);
  const телефон = настрСекция(содержимое, 'телефон', 'Телефон');
  const телефонУказатель = document.createElement('div');
  телефонУказатель.className = 'настр-описание';
  телефонУказатель.textContent = 'Куда идёт её голос — колонки или телефон — выбирается в «Голос → Звук»';
  телефон.appendChild(телефонУказатель);
  const телАдрес = настрВыбор([], '');
  настрПоле(телефон, 'phone_address', телАдрес);
  const адреса = document.createElement('div');
  адреса.className = 'настр-адреса';
  адреса.textContent = '…';
  телефон.appendChild(адреса);
  const телРяд = document.createElement('div');
  телРяд.className = 'настр-телряд';
  const телКопировать = document.createElement('button');
  телКопировать.type = 'button';
  телКопировать.className = 'голос-кнопка';
  телКопировать.textContent = 'Копировать адрес';
  телКопировать.disabled = true;
  телРяд.appendChild(телКопировать);
  const телСтатус = document.createElement('span');
  телСтатус.className = 'настр-статус';
  телСтатус.textContent = '…';
  телРяд.appendChild(телСтатус);
  телефон.appendChild(телРяд);
  /* QR-код — и здесь, а не только в мастере: с 29.09 в ссылке ключ привязки,
     и набирать её на телефоне руками никто не станет (хозяин открыл
     «Телефон» после обновления, а кода там не было). */
  const телКод = document.createElement('div');
  телКод.className = 'мастер-qr настр-qr';
  телКод.hidden = true;
  телефон.appendChild(телКод);
  телАдрес.addEventListener('change', () => настрПоказатьКод(телАдрес, телКод));
  const неГаситьОбновить = телефонНеГасить(телефон, телАдрес);
  телефонВоВесьЭкран(телефон);
  /* Погода на телефоне: город выбирается здесь, а не вписывается руками.
     Координаты хозяину знать незачем — их берёт геокодер (`/api/weather/find`). */
  const погЗаголовок = document.createElement('div');
  погЗаголовок.className = 'образец-заголовок';
  погЗаголовок.textContent = 'Погода на телефоне';
  телефон.appendChild(погЗаголовок);
  const погГород = document.createElement('div');
  погГород.className = 'настр-адреса';
  погГород.textContent = '…';
  телефон.appendChild(погГород);
  const погРяд = document.createElement('div');
  погРяд.className = 'настр-телряд';
  const погВвод = настрВвод('');
  погВвод.placeholder = 'Название города';
  погВвод.classList.add('пог-ввод');
  const погНайти = document.createElement('button');
  погНайти.type = 'button';
  погНайти.className = 'голос-кнопка';
  погНайти.textContent = 'Найти';
  погНайти.title = 'Найдёт города с таким названием — выбери нужный из списка';
  погРяд.append(погВвод, погНайти);
  телефон.appendChild(погРяд);
  const погСписок = document.createElement('div');
  погСписок.className = 'пог-список';
  телефон.appendChild(погСписок);
  const погБез = document.createElement('button');
  погБез.type = 'button';
  погБез.className = 'голос-кнопка';
  погБез.textContent = 'Без погоды';
  погБез.title = 'Убрать город: погода на телефоне исчезнет';
  погБез.hidden = true;
  телефон.appendChild(погБез);
  const погСтатус = document.createElement('div');
  погСтатус.className = 'настр-статус';
  погСтатус.textContent = '';
  телефон.appendChild(погСтатус);
  const система = настрСекция(содержимое, 'система', 'Система');
  // Состояние — не в settings.json, а по факту наличия ярлыка в папке
  // автозагрузки Windows, поэтому и приходит отдельным полем снимка.
  const autostart = настрГалочка(false);
  настрПоле(система, 'autostart', autostart);
  /* Тема — единственное поле, что перекрашивает пульт сразу, не дожидаясь
     «Сохранить»: иначе хозяин выбрал бы «Светлая», нажал «Сохранить» и
     только тогда увидел бы результат — а проверяют тему глазами. */
  const theme = настрВыбор(ТЕМЫ, 'dark');
  настрПоле(система, 'theme', theme);
  theme.addEventListener('change', () => темуПоставить(theme.value));

  /* Перенос настроек: файл, который уносит Трубу на другой компьютер. Блок
     тот же, что зовёт первый шаг мастера, — работа одна на оба места. */
  const перенос = переносБлок(система, 'Перенос настроек',
    'Сохрани настройки в файл — после переустановки Трубы перенеси их обратно, '
    + 'ничего не настраивая заново', true);
  переносЧастиЗагрузить(перенос);

  /* «О программе» — отдельный пункт меню, а не раздел этой формы: страницу
     рисует `нарисоватьОПрограмме` (см. ниже), и `first_run_done` живёт
     вместе с ней. В форме от неё ничего не осталось. */

  const низ = document.createElement('div');
  низ.className = 'настр-низ';
  const сохранить = document.createElement('button');
  сохранить.type = 'button';
  сохранить.className = 'голос-кнопка главная';
  сохранить.textContent = 'Сохранить';
  низ.appendChild(сохранить);
  /* Три кнопки проверки живут внизу формы и меняются разделом: у «Ответов» —
     связь с моделью, у «Поиска» — сам поиск, у «Озвучивания» — проба голоса.
     В остальных разделах кнопки скрыты, а строка остаётся пустой справа. */
  const проверитьСвязьКнопка = document.createElement('button');
  проверитьСвязьКнопка.type = 'button';
  проверитьСвязьКнопка.className = 'голос-кнопка настр-проверка';
  проверитьСвязьКнопка.textContent = 'Проверить связь';
  проверитьСвязьКнопка.title = 'Один короткий запрос к модели; настройки не сохраняются';
  низ.appendChild(проверитьСвязьКнопка);
  const проверитьПоискКнопка = document.createElement('button');
  проверитьПоискКнопка.type = 'button';
  проверитьПоискКнопка.className = 'голос-кнопка настр-проверка';
  проверитьПоискКнопка.textContent = 'Проверить поиск';
  проверитьПоискКнопка.title = 'Один запрос выбранным способом; платный стоит около рубля';
  низ.appendChild(проверитьПоискКнопка);
  низ.appendChild(послушатьПробуКнопка);
  const статус = document.createElement('span');
  статус.className = 'настр-статус';
  статус.textContent = '…';
  низ.appendChild(статус);
  корень.appendChild(низ);
  настрЭлементы = { раздел, пояснение, секции: [мозг, поиск, первая, характер, память, голосСекция, слух, расп, звук, телефон, система], provider, локальноеПредупреждение, local_url, local_urlРяд, рядКлюча, model, списокМоделей, обновитьМодели, моделиСтатус, номерЗагрузкиМоделей: 0, провайдеры: {}, моделиВПравке: {}, ключиВПравке: {}, сохранённыеКлючи: {}, ключНамёк, ключПоказать, api_key, temperature, max_tokens, history_turns, web_search, search_sound, web_search_mode, web_search_budget, hedge, проверитьПоиск: проверитьПоискКнопка, проверитьСвязь: проверитьСвязьКнопка, proactive, proactive_look, persona, memory, отменитьХарактер, отменитьПамять, забыть, показатьИсторию, очистить, историяСписок, tts_engine, silero_speaker, silero_model, образецБлок, voice_name, образецОписание, образецИграть, образецУдалить, образецФайл, образецФайлИнфо, образецРучной, образецНачало, образецДлина, образецКусок, образецДобавить, образецШаг, tts_speed, tts_nfe, tts_gap, voice_volume, higgs_gentle, duck_level, barge_in_level, barge_instant, послушатьПробу: послушатьПробуКнопка, плеерПроба, follow_up_window, require_name_when_noisy, voice_app_guard, owner_only, voice_autostart, owner_threshold, владелец, хозяинЗаписать, хозяинЗакончить, хозяинСтатус, хозяинТекст, хозяинИтог, stt_model, stt_quantization, распПояснение, распПроверить, распСтатус, распИтог, output, mic_name, mic_channel, mic_channelРяд, уровень, уровеньFill, уровеньПодпись, speaker_name, звукПроверить, звукПрослушать, звукЗапись, звукСтатус, железоБлок, железоГолоса, железоГолосаСтатус, железоКарточка, железоКнопка, железоХод, железоТело, железоИтог, железоСтрелка, железоДанные: null, голосаБлок, голосаРяды, голосаПолоса: голосаПолосаМесто, голосаБибПолоса: голосаБибПолосаМесто, погГород, погГородСохранено: '', погШирота: null, погДолгота: null, погЧерновик: null, погВвод, погНайти, погСписок, погБез, погСтатус, autostart, theme, адреса, телАдрес, телКопировать, телСтатус, телКод, неГаситьОбновить, сохранить, статус };
  // Форма новая — прошлые списки устройств, таймер уровня и отметка о железе
  // не про неё. Раньше первого показа раздела: иначе только что пришедшие
  // списки микрофонов тут же сбрасывались бы.
  звукСбросить();
  настрПоказатьРаздел(страница === 'голос' ? голосПодраздел : настрТекущийРаздел);
  раздел.addEventListener('change', () => настрПоказатьРаздел(раздел.value));
  provider.addEventListener('change', () => {
    const эл = настрЭлементы;
    эл.номерЗагрузкиМоделей++;
    настрПоказатьКлюч();
    api_key.type = 'password';
    ключПоказать.title = 'Показать ключ';
    ключПоказать.setAttribute('aria-label', 'Показать ключ');
    настрПоказатьМодель();
    настрПоказатьЛокальное();
    настрПодсказкаКлюча();
    настрЗагрузитьМодели();
  });
  model.addEventListener('input', () => { настрЭлементы.моделиВПравке[provider.value] = model.value; });
  обновитьМодели.addEventListener('click', настрЗагрузитьМодели);
  let таймерМоделей;
  api_key.addEventListener('input', () => {
    настрЭлементы.ключиВПравке[provider.value] = api_key.value;
    clearTimeout(таймерМоделей);
    таймерМоделей = setTimeout(настрЗагрузитьМодели, 700);
  });
  /* Глазик только переключает вид поля. Если ключ в поле ещё не подставлен
     (хозяин сменил сервис и сразу ткнул в глаз), тянем его тем же запросом.
     `ключиВПравке` тут не трогаем: подставленный ключ не считается введённым. */
  ключПоказать.addEventListener('click', async () => {
    if (api_key.type === 'password' && !api_key.value) await настрПоказатьКлюч();
    api_key.type = api_key.type === 'password' ? 'text' : 'password';
    const подпись = api_key.type === 'password' ? 'Показать ключ' : 'Скрыть ключ';
    ключПоказать.title = подпись;
    ключПоказать.setAttribute('aria-label', подпись);
  });
  отменитьХарактер.addEventListener('click', () => настрВернутьТекст('persona'));
  характерЗагрузить(характерКарточки, persona);
  отменитьПамять.addEventListener('click', () => настрВернутьТекст('memory'));
  persona.addEventListener('input', настрОбновитьОтмену);
  persona.addEventListener('input', характерОтметить);
  memory.addEventListener('input', настрОбновитьОтмену);
  настрОбновитьОтмену();
  сохранить.addEventListener('click', сохранитьНастройки);
  проверитьСвязьКнопка.addEventListener('click', проверитьСвязь);
  проверитьПоискКнопка.addEventListener('click', проверитьПоиск);
  web_search.addEventListener('change', настрПоляПоиска);
  послушатьПробуКнопка.addEventListener('click', послушатьПробу);
  очистить.addEventListener('click', очиститьИсторию);
  показатьИсторию.addEventListener('click', показатьИсториюРазговора);
  забыть.addEventListener('click', забытьПамять);
  телКопировать.addEventListener('click', скопироватьАдресТелефона);
  voice_name.addEventListener('change', показатьОбразец);
  образецИграть.addEventListener('click', прослушатьОбразец);
  образецУдалить.addEventListener('click', удалитьОбразец);
  образецДобавить.addEventListener('click', добавитьОбразец);
  образецФайл.addEventListener('change', выбранФайлОбразца);
  образецКусок.addEventListener('click', прослушатьКусок);
  образецРучной.addEventListener('change', () => { образецНарезка.hidden = !образецРучной.checked; });
  tts_engine.addEventListener('change', настрПоляОзвучивания);
  mic_name.addEventListener('change', () => звукПоказатьВход(настрЭлементы));
  звукПроверить.addEventListener('click', () => проверитьЗвук(настрЭлементы));
  звукПрослушать.addEventListener('click', () => прослушатьЗапись(настрЭлементы));
  железоГолоса.addEventListener('click', () => поставитьГолоса(настрЭлементы));
  железоКнопка.addEventListener('click', () => железоПеревернуть(настрЭлементы));
  /* Пока голоса не спросили, блок свёрнут (или как хозяин оставлял в прошлый
     раз) — `показатьГолоса` потом решит, надо ли раскрыть. */
  железоРаскрыть(настрЭлементы, железоВыбор() === '1', false);
  погНайти.addEventListener('click', () => найтиГород(настрЭлементы));
  погВвод.addEventListener('keydown', (событие) => {
    if (событие.key === 'Enter') { событие.preventDefault(); найтиГород(настрЭлементы); }
  });
  погБез.addEventListener('click', () => погодаБезГорода(настрЭлементы));
  хозяинЗаписать.addEventListener('click', () => хозяинНачатьЗапись(настрЭлементы));
  хозяинЗакончить.addEventListener('click', () => хозяинЗакончитьЗапись(настрЭлементы));
  распПроверить.addEventListener('click', () => проверитьРаспознавание(настрЭлементы));
  stt_model.addEventListener('change', () => распПояснить(настрЭлементы));
  загрузитьНастройки();
}

/* ---------- Программы: компактный редактор списка ---------- */
let прогПриложения = [];
let прогЗагрузка = false;
let прогСохранённое = new Map();
/* Сохранённый список — строкой, а не по кнопкам: поменять порядок тоже
   правка, и «Сохранить» должен это видеть. */
let прогСохранённоеСписок = '[]';
/* Вид сетки: `прогСетка` — что сохранено, `прогСеткаЧерновик` — что выбрано
   хозяином. Экран телефона смотрит на черновик, сервер — на сохранённое.
   Колонок здесь нет: их всегда четыре, а рядов один или два
   (`ПРОГ_РЯДЫ`). */
/* Рядов два: хозяин попросил «сделать 2 и 1 ряд приложений» (02.10), поэтому
   в переключателе «Ряды» две кнопки. Старое «3» из живого settings.json пульт
   показывает и сохраняет как два — трёх рядов на телефоне не помещалось. */
const ПРОГ_РЯДЫ = [1, 2];
const ПРОГ_РЯДЫ_УМОЛЧАНИЕ = 2;
/* Что бы ни пришло из settings.json или из старой страницы, телефон умеет
   один или два ряда: всё остальное молча становится двумя. */
function прогНормаРядов(значение) {
  const n = Number(значение);
  return ПРОГ_РЯДЫ.includes(n) ? n : ПРОГ_РЯДЫ_УМОЛЧАНИЕ;
}
let прогСетка = { rows: ПРОГ_РЯДЫ_УМОЛЧАНИЕ, style: 'plate', labels: false };
let прогСеткаЧерновик = { rows: ПРОГ_РЯДЫ_УМОЛЧАНИЕ, style: 'plate', labels: false };
let прогСеткаСохранённая = JSON.stringify(прогСетка);

/* Нижние кнопки телефона: `прогКнопки` — что сохранено на компе,
   `прогКнопкиЧерновик` — что выбрал хозяин. Всегда четыре ячейки, по числу
   мест внизу экрана; макет смотрит на черновик, сервер — на сохранённое.
   Хозяин 28.09 захотел менять их на свои: смена сцены в OBS, «заглушить себя»
   в Discord, горячая клавиша. */
const ПРОГ_КНОПКИ_УМОЛЧАНИЕ = [
  { kind: 'builtin', id: 'screenshot' },
  { kind: 'builtin', id: 'moment' },
  { kind: 'builtin', id: 'search' },
  { kind: 'builtin', id: 'note_start' },
];
let прогКнопки = ПРОГ_КНОПКИ_УМОЛЧАНИЕ.map((ячейка) => ({ ...ячейка }));
let прогКнопкиЧерновик = ПРОГ_КНОПКИ_УМОЛЧАНИЕ.map((ячейка) => ({ ...ячейка }));
let прогКнопкиСохранённые = JSON.stringify(прогКнопки);
/* Подписи и значки встроенных кнопок — те же, что рисует телефон, иначе в
   пульте была бы одна картинка, а на экране другая. */
const ПРОГ_КНОПКИ_ВСТРОЕННЫЕ = {
  screenshot: { title: 'экран', icon: 'camera' },
  moment: { title: 'момент', icon: 'replay' },
  search: { title: 'найти', icon: 'search' },
  note_start: { title: 'заметка', icon: 'note' },
};
/* Что делает кнопка — выбор в панели справа. Порядок как у хозяина в голове:
   сначала привычное, потом своё, в конце — убрать. */
const ПРОГ_КНОПКИ_ВИДЫ = [
  ['builtin:screenshot', 'Снимок экрана'],
  ['builtin:moment', 'Момент (повтор NVIDIA)'],
  ['builtin:search', 'Найти голосом'],
  ['builtin:note_start', 'Заметка'],
  ['hotkey', 'Сочетание клавиш'],
  ['app', 'Открыть программу'],
  ['menu', 'Пункт подменю программы'],
  ['none', 'Пусто'],
];
function прогАргументыВСтроку(args) {
  if (!Array.isArray(args) || !args.length) return '';
  return args.map((a) => {
    const s = String(a === undefined || a === null ? '' : a);
    if (s === '') return '""';
    if (/[\s"\\]/.test(s)) return '"' + s.replace(/(["\\])/g, '\\$1') + '"';
    return s;
  }).join(' ');
}
function прогРазобратьАргументы(строка) {
  const исход = String(строка === undefined || строка === null ? '' : строка).trim();
  if (!исход) return { ok: true, args: [] };
  const итог = [];
  let тек = '';
  let кавычка = null;
  let вСлове = false;
  for (let i = 0; i < исход.length; i++) {
    const с = исход[i];
    if (кавычка) {
      if (с === '\\' && i + 1 < исход.length) {
        const след = исход[i + 1];
        if (след === кавычка || след === '\\') { тек += след; i++; вСлове = true; continue; }
      }
      if (с === кавычка) { кавычка = null; вСлове = true; continue; }
      тек += с; вСлове = true;
    } else if (с === '"' || с === "'") { кавычка = с; вСлове = true;
    } else if (с === ' ' || с === '\t' || с === '\n' || с === '\r') {
      if (вСлове) { итог.push(тек); тек = ''; вСлове = false; }
    } else { тек += с; вСлове = true; }
  }
  if (кавычка) return { ok: false, ошибка: 'не закрыта кавычка' };
  if (вСлове) итог.push(тек);
  return { ok: true, args: итог };
}
function прогНорма(п, индекс) {
  const app = (п && typeof п === 'object') ? п : {};
  const kind = ['url', 'store', 'folder'].includes(app.kind) ? app.kind : 'app';
  const out = {
    id: String(app.id === undefined || app.id === null ? 'app-' + (индекс + 1) : app.id).trim(),
    title: String(app.title === undefined || app.title === null ? '' : app.title),
    kind: kind,
    icon: String(app.icon === undefined || app.icon === null ? '' : app.icon),
    icon_source: ['drawn', 'exe', 'file'].includes(app.icon_source) ? app.icon_source : (app.path ? 'exe' : 'drawn'),
    icon_file: String(app.icon_file || ''),
    image: String(app.image || ''),
    /* Цвет рисованного значка: телефон красит им плитку, а без него берёт
       свой цвет по id программы. */
    color: /^#[0-9a-f]{6}$/i.test(String(app.color || '')) ? String(app.color) : '',
    menu: Array.isArray(app.menu) ? app.menu.map(прогНормаПункт) : [],
    bookmarks: app.bookmarks === 'firefox' ? 'firefox' : '',
    /* Прозвища для голоса («закрой телегу»). В редакторе их нет, но терять
       их при «Сохранить» нельзя — они пишутся руками в apps.json. */
    aliases: Array.isArray(app.aliases) ? app.aliases.map((a) => String(a)) : [],
  };
  if (kind === 'app') {
    out.path = String(app.path === undefined || app.path === null ? '' : app.path);
    out.args = Array.isArray(app.args) ? app.args.map((a) => String(a)) : [];
    out.how = app.how === 'direct' ? 'direct' : 'shell';
    /* Имя процесса, по которому программу закрывают. У ChatGPT запускается
       LaunchCodex.exe, а живёт ChatGPT.exe — 28.09 поле потерялось при
       «Сохранить», и «закрой ChatGPT» перестало работать. */
    out.process = String(app.process || '');
  } else if (kind === 'folder') {
    /* Своя папка хозяина: у неё, как у программы, только путь. Значок оболочка
       умеет и у папки (core/app_icons.py), поэтому источник тот же. */
    out.path = String(app.path === undefined || app.path === null ? '' : app.path);
  } else if (kind === 'url') { out.url = String(app.url === undefined || app.url === null ? '' : app.url);
  } else { out.app_id = String(app.app_id === undefined || app.app_id === null ? '' : app.app_id); }
  return out;
}
/* Пункт подменю для телефона. `keys` и `url` — по виду: горячее сочетание
   пишется текстом, сайт адресом. Пустые поля оставляем, чтобы форма в пульте
   не прыгала при перерисовке. */
function прогНормаПункт(п) {
  const item = (п && typeof п === 'object') ? п : {};
  const kind = item.kind === 'site' ? 'site' : 'hotkey';
  const out = {
    kind: kind,
    id: String(item.id || '').trim(),
    title: String(item.title || ''),
    icon: String(item.icon || ''),
  };
  if (kind === 'hotkey') out.keys = String(item.keys || '');
  else out.url = String(item.url || '');
  return out;
}
/* Свободное имя пункта в пределах кнопки: сервер откажется от повтора.
   Название русское — значит и имя придумываем из него: латиницей вручную
   хозяину переводить не нужно (`прогIdИзНазвания`). */
const ПРОГ_ТРАНСЛИТ = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z',
  и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
  с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh',
  щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
};
function прогIdИзНазвания(название) {
  const слово = String(название || '').toLowerCase();
  let out = '';
  for (const буква of слово) {
    const замена = ПРОГ_ТРАНСЛИТ[буква];
    out += замена === undefined ? буква : замена;
  }
  return out.replace(/[^a-z0-9]/g, '');
}
/* Своё ли имя кнопки: латиница, цифры, точка, дефис или подчёркивание — ровно
   то, что примет сервер (`launcher.чистое_id`). Русское имя не годится: телефон
   узнаёт кнопку по латинскому `id`, поэтому такое мы заменяем придуманным —
   иначе клиент и сервер посчитали бы разное. */
function прогСвоёId(значение) {
  const текст = String(значение == null ? '' : значение).trim();
  return /^[A-Za-z0-9._-]{1,64}$/.test(текст) ? текст : '';
}
/* Основа имени кнопки из названия: как на сервере (`launcher.чистое_id`).
   Название режется до 60 знаков — иначе длинное русское название дало бы здесь
   одно имя, а там другое. */
function прогОсноваIdКнопки(название) {
  const основа = прогIdИзНазвания(String(название || '').slice(0, 60)).slice(0, 64);
  return основа || 'app';
}
/* Свободное имя кнопки из её названия. Свою строку не считаем занятой: пустой
   `id` — это как раз она, и придумывать надо имя, которого ещё нет. Длина — те
   же 64 знака, что и на сервере, а номер вписывается в них.

   Заняты имена соседей — те, что уже стоят в их строках: строки собираются
   по порядку, и каждой её имя возвращается в поле сразу, поэтому к моменту
   расчёта предыдущие строки уже названы (сервер поступает так же). */
function прогСвободныйIdКнопки(индекс, название) {
  const заняты = new Set(прогПриложения
    .filter((_, i) => i !== индекс)
    .map((п) => прогСвоёId(п.id))
    .filter(Boolean));
  let base = прогОсноваIdКнопки(название);
  if (!заняты.has(base)) return base;
  for (let n = 2; n < 1000; n++) {
    const хвост = String(n);
    const кандидат = base.slice(0, Math.max(1, 64 - хвост.length)) + хвост;
    if (!заняты.has(кандидат)) return кандидат;
  }
  return '';
}
/* Имя пункта меню — те же 40 знаков, что проверяет сервер (`_check_menu`), и
   номер вписывается в них, а не обрезается. */
function прогСвободныйIdПункта(список, основа) {
  const заняты = new Set(список.map((п) => прогСвоёId(п.id)).filter(Boolean));
  let base = (String(основа || '').replace(/[^A-Za-z0-9._-]/g, '')
    || прогIdИзНазвания(основа)).slice(0, 40);
  if (!base) base = 'item';
  if (!заняты.has(base)) return base;
  for (let n = 2; n < 1000; n++) {
    const хвост = String(n);
    const кандидат = base.slice(0, Math.max(1, 40 - хвост.length)) + хвост;
    if (!заняты.has(кандидат)) return кандидат;
  }
  return '';
}
function прогПоле(сетка, имя, ярлык, тип, значение, намёк, подсказка, широкий) {
  const обёртка = document.createElement(имя === 'path' ? 'div' : 'label');
  обёртка.className = 'прог-поле' + (широкий ? ' широкий' : '');
  обёртка.setAttribute('data-поле-обёртка', имя);
  const яр = document.createElement('span');
  яр.textContent = ярлык;
  let ввод;
  if (тип === 'select') {
    ввод = document.createElement('select');
    let варианты = [['shell', 'обычный'], ['direct', 'прямой']];
    if (имя === 'kind') варианты = [['app', 'программа'], ['url', 'ссылка'], ['store', 'приложение Windows'], ['folder', 'папка']];
    else if (имя === 'icon_source') варианты = [['drawn', 'рисованный'], ['exe', 'из программы'], ['file', 'свой файл']];
    for (const [з, т] of варианты) {
      const оп = document.createElement('option');
      оп.value = з; оп.textContent = т;
      ввод.appendChild(оп);
    }
    ввод.value = значение;
  } else {
    ввод = document.createElement('input');
    ввод.type = 'text';
    ввод.value = значение === undefined || значение === null ? '' : String(значение);
    if (намёк) ввод.placeholder = намёк;
    if (подсказка) ввод.title = подсказка;
  }
  ввод.dataset.поле = имя;
  обёртка.appendChild(яр);
  обёртка.appendChild(ввод);
  сетка.appendChild(обёртка);
  return ввод;
}
/* Источник значка выбирается кнопками, а не списком: набор рисунков, файл и
   «из самой программы» — три разных блока, и видно должен быть ровно один из
   них. Сам блок «значок» виден всегда — в нём переключатель. */
function прогПрименитьЗначок(строкаEl) {
  /* У папки значок тоже берётся из оболочки — у неё есть свой путь, и
     core/app_icons.py отдаёт значок папки так же, как значок программы. */
  const вид = ['app', 'folder'].includes(прогВзять(строкаEl, 'kind')) ? 'app' : 'ссылка';
  const поле = строкаEl.querySelector('[data-поле="icon_source"]');
  const ист = поле ? поле.value : 'drawn';
  let тек = ист === 'exe' || ист === 'file' ? ист : 'drawn';
  /* Значок «из самой программы» берётся из файла программы. У ссылки и
     приложения Магазина такого файла нет, поэтому такую кнопку им не
     предлагаем, а исподтишка молча возвращаем их к рисованному значку. */
  if (тек === 'exe' && вид !== 'app') { тек = 'drawn'; if (поле) поле.value = 'drawn'; }
  const показать = (имя, видно) => {
    const узел = строкаEl.querySelector('[data-блок="' + имя + '"]');
    if (узел) узел.hidden = !видно;
  };
  показать('набор', тек === 'drawn');
  показать('цвет', тек === 'drawn');
  показать('файл', тек === 'file');
  /* Отмечаем выбранный рисунок: после возврата к рисованному значку хозяин
     должен сразу видеть, какой именно, а не гадать по памяти. */
  const имя = прогВзять(строкаEl, 'icon') || 'app';
  строкаEl.querySelectorAll('.прог-значок').forEach((к) => {
    к.classList.toggle('выбран', к.dataset.имя === имя);
  });
  строкаEl.querySelectorAll('[data-источник]').forEach((к) => {
    к.classList.toggle('активный', к.dataset.источник === тек);
    к.hidden = к.dataset.источник === 'exe' && вид !== 'app';
  });
}
function прогПрименитьВид(строкаEl) {
  const sel = строкаEl.querySelector('[data-поле="kind"]');
  const вид = sel ? sel.value : 'app';
  /* Путь есть и у программы, и у папки — это единственное, что нужно обеим.
     Способ запуска и параметры — только программе: папку проводник открывает
     сам, запускать её нечем. */
  const обPath = строкаEl.querySelector('[data-поле-обёртка="path"]');
  if (обPath) обPath.hidden = !['app', 'folder'].includes(вид);
  for (const имя of ['how', 'args']) {
    const об = строкаEl.querySelector('[data-поле-обёртка="' + имя + '"]');
    if (об) об.hidden = вид !== 'app';
  }
  const обUrl = строкаEl.querySelector('[data-поле-обёртка="url"]');
  if (обUrl) обUrl.hidden = вид !== 'url';
  const обStore = строкаEl.querySelector('[data-поле-обёртка="app_id"]');
  if (обStore) обStore.hidden = вид !== 'store';
  /* Подпись поля меняется по виду: у папки это «папка», а не «файл
     программы», и хозяин должен видеть, что вписывает. */
  const ярPath = обPath ? обPath.querySelector('span') : null;
  if (ярPath) ярPath.textContent = вид === 'folder' ? 'папка' : 'файл программы';
}
function прогВзять(строкаEl, имя) {
  const el = строкаEl.querySelector('[data-поле="' + имя + '"]');
  return el ? el.value : '';
}
function прогСобратьСтроку(строкаEl, индекс) {
  const п = прогПриложения[индекс];
  if (!п) return null;
  п.id = прогВзять(строкаEl, 'id').trim();
  п.title = прогВзять(строкаEl, 'title');
  /* Пустое или русское `id` не беда: придумываем его из названия, чтобы
     хозяину не приходилось переводить «Мой блог» в `moyblog` руками. Поле
     показываем заполненным — иначе было бы не видно, какое имя уйдёт на
     телефон. Написанное руками латинское имя не трогаем: телефон помнит
     кнопку именно по нему. */
  if (!прогСвоёId(п.id)) {
    const придуманный = прогСвободныйIdКнопки(индекс, п.title);
    if (придуманный) {
      п.id = придуманный;
      const полеId = строкаEl.querySelector('[data-поле="id"]');
      if (полеId) полеId.value = придуманный;
    }
  }
  п.icon = прогВзять(строкаEl, 'icon').trim();
  const ист = прогВзять(строкаEl, 'icon_source');
  п.icon_source = ист === 'exe' || ист === 'file' ? ист : 'drawn';
  /* Цвет есть только у рисованного значка: у картинки он не виден, а
     лишний ключ в apps.json хозяину ничего не скажет. Кнопка «Цвет телефона»
     не стирает поле, а помечает его сброшенным: иначе следующая же правка
     названия вернула бы прежний цвет обратно сам собой. */
  const цветEl = строкаEl.querySelector('[data-поле="color"]');
  const цвет = цветEl ? цветEl.value : '';
  if (п.icon_source === 'drawn' && цветEl && цветEl.dataset.сброшен !== '1'
      && /^#[0-9a-f]{6}$/i.test(цвет)) п.color = цвет;
  else delete п.color;
  const вид = прогВзять(строкаEl, 'kind');
  п.kind = ['url', 'store', 'folder'].includes(вид) ? вид : 'app';
  delete п.path; delete п.args; delete п.how; delete п.url; delete п.app_id;
  if (п.kind === 'app') {
    п.path = прогВзять(строкаEl, 'path');
    п.how = прогВзять(строкаEl, 'how') === 'direct' ? 'direct' : 'shell';
    const разбор = прогРазобратьАргументы(прогВзять(строкаEl, 'args'));
    if (!разбор.ok) return { ошибка: 'Строка ' + (индекс + 1) + ': аргументы — ' + разбор.ошибка + '. Пример: --incognito "C:\\Мои файлы\\x.txt"' };
    п.args = разбор.args;
  } else if (п.kind === 'folder') {
    /* Путь папки — это и есть всё, что нужно: проводник откроет его сам.
       Имя процесса у папки ни к чему — закрыть её нечем. */
    п.path = прогВзять(строкаEl, 'path').trim();
    delete п.process;
  } else if (п.kind === 'url') { п.url = прогВзять(строкаEl, 'url').trim();
  } else { п.app_id = прогВзять(строкаEl, 'app_id').trim(); }
  п.menu = прогСобратьМеню(строкаEl);
  const галочка = строкаEl.querySelector('[data-поле="bookmarks"]');
  п.bookmarks = галочка && галочка.checked ? 'firefox' : '';
  return { ok: true };
}
/* Меню лежит в своих полях, у каждого пункта свой префикс, иначе десяток
   одинаковых `title` перепутался бы между собой. */
function прогСобратьМеню(строкаEl) {
  const пункты = [];
  строкаEl.querySelectorAll('[data-меню-пункт]').forEach((узел) => {
    const вид = узел.getAttribute('data-меню-пункт') === 'site' ? 'site' : 'hotkey';
    const значение = (имя) => {
      const el = узел.querySelector('[data-меню-поле="' + имя + '"]');
      return el ? el.value : '';
    };
    const пункт = {
      kind: вид,
      id: значение('id').trim(),
      title: значение('title').trim(),
      icon: значение('icon').trim(),
    };
    if (вид === 'hotkey') пункт.keys = значение('keys').trim();
    else пункт.url = значение('url').trim();
    if (вид === 'hotkey') {
      /* Галочка и выбор — не текстовые поля, поэтому их читаем отдельно:
         value() у checkbox и select означал бы совсем не то. */
      const гал = узел.querySelector('[data-меню-поле="toggle"]');
      if (гал && гал.checked) пункт.toggle = true;
      const implies = значение('implies').trim();
      if (implies) пункт.implies = implies;
    }
    пункты.push(пункт);
  });
  return пункты;
}
function прогСобратьВсе(списокEl) {
  const строки = Array.from(списокEl.querySelectorAll('.прог-строка'));
  for (const строка of строки) {
    /* Индекс — из самой строки: показывается только выбранная программа, и
       её номер в списке не совпадает с её местом в панели. */
    const рез = прогСобратьСтроку(строка, Number(строка.dataset.индекс));
    if (рез && рез.ошибка) return { ошибка: рез.ошибка };
  }
  return { ok: true };
}
function прогПроверить(списокEl, статусEl, громко) {
  const итог = прогСобратьВсе(списокEl);
  if (итог.ошибка) { if (громко) прогСказать(статусEl, итог.ошибка, true); return false; }
  const виденные = new Set();
  for (let i = 0; i < прогПриложения.length; i++) {
    const п = прогПриложения[i];
    const ном = 'Строка ' + (i + 1) + ': ';
    if (!п.id) { if (громко) прогСказать(статусEl, ном + 'впиши название — имя кнопки придумаем сами.', true); return false; }
    if (виденные.has(п.id)) { if (громко) прогСказать(статусEl, 'Повтор id «' + п.id + '» в строке ' + (i + 1) + '.', true); return false; }
    виденные.add(п.id);
    if (!String(п.title || '').trim()) { if (громко) прогСказать(статусEl, ном + 'заполни название кнопки.', true); return false; }
    if (п.kind === 'app' && !String(п.path || '').trim()) { if (громко) прогСказать(статусEl, ном + 'заполни путь программы.', true); return false; }
    if (п.kind === 'url' && !String(п.url || '').trim()) { if (громко) прогСказать(статусEl, ном + 'заполни ссылку.', true); return false; }
    if (п.kind === 'store' && !String(п.app_id || '').trim()) { if (громко) прогСказать(статусEl, ном + 'заполни app_id магазина.', true); return false; }
    for (let m = 0; m < (п.menu || []).length; m++) {
      const пункт = п.menu[m];
      const где = 'Строка ' + (i + 1) + ', пункт ' + (m + 1) + ': ';
      if (!String(пункт.title || '').trim()) { if (громко) прогСказать(статусEl, где + 'заполни название пункта.', true); return false; }
      if (пункт.kind === 'site' && !/^https?:\/\//i.test(String(пункт.url || '').trim())) {
        if (громко) прогСказать(статусEl, где + 'нужен адрес, начинающийся с http:// или https://.', true);
        return false;
      }
    }
  }
  return true;
}
function прогСказать(статусEl, текст, плохо) {
  if (!статусEl) return;
  статусEl.textContent = текст;
  статусEl.classList.toggle('плохо', !!плохо);
}
/* ---------- Экран телефона: живой макет в iframe ----------
   Вместо схематичной картинки — настоящая страница телефона в режиме правки
   (`/?edit=1`): она не подключается к серверу, а берёт значки и подписи
   только от нас. Размер её — тот, что сообщил настоящий телефон
   (`viewport` в ответе `/api/apps`): у разных телефонов он разный, и макет
   не того размера рисовал бы значки не того размера. Пока телефон ни разу
   не подключался, берём прежние 851×393 — телефон в альбомной ориентации.
   Масштаб под ширину колонки считаем сами: растягивать макет на всю
   ширину значило бы растянуть его значки. */
let ПРОГ_ШИРИНА_ЭКРАНА = 851;
let ПРОГ_ВЫСОТА_ЭКРАНА = 393;
/* Правка идёт на каждое нажатие клавиши, а список значков возить незачем
   часто: между отправками пауза, последняя отправка её дожидается. */
const ПРОГ_ПАУЗА_ЭКРАНА = 150;

function прогМасштабЭкрана(ctx) {
  if (!ctx.экран || !ctx.рамка) return;
  const есть = ctx.рамка.clientWidth;
  if (!есть) return;
  const k = Math.min(1, есть / ПРОГ_ШИРИНА_ЭКРАНА);
  ctx.экран.style.transform = 'scale(' + k + ')';
  ctx.рамка.style.height = Math.round(ПРОГ_ВЫСОТА_ЭКРАНА * k) + 'px';
}
/* Размер экрана из ответа сервера. Плохой ответ — прежние 851×393, а не пустое
   окно: макет должен быть всегда, иначе вкладка выглядит сломанной.
   Макет всегда альбомный: телефон в проекте лежит горизонтально, а прислать
   он мог «стоячий» размер (повернули и отключили) — макет бы встал столбом.
   Поэтому ширина — большее из двух чисел, высота — меньшее. */
function прогВзятьРазмерЭкрана(данные) {
  const размер = данные && данные.viewport;
  if (!размер || typeof размер !== 'object') return false;
  const w = Number(размер.w), h = Number(размер.h);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w < 200 || h < 200) return false;
  ПРОГ_ШИРИНА_ЭКРАНА = Math.round(Math.max(w, h));
  ПРОГ_ВЫСОТА_ЭКРАНА = Math.round(Math.min(w, h));
  return true;
}
/* Размер экрана — в iframe и в подпись под ним. Молчаливый макет хозяин принял
   бы за свой телефон, а он не его. */
function прогПрименитьРазмерЭкрана(ctx, размерИзвестен) {
  if (ctx.экран) {
    ctx.экран.style.width = ПРОГ_ШИРИНА_ЭКРАНА + 'px';
    ctx.экран.style.height = ПРОГ_ВЫСОТА_ЭКРАНА + 'px';
  }
  if (ctx.подпись) {
    ctx.подпись.textContent = размерИзвестен
      ? 'Экран телефона ' + ПРОГ_ШИРИНА_ЭКРАНА + '×' + ПРОГ_ВЫСОТА_ЭКРАНА
      : 'Размер телефона ещё не известен — открой Трубу на телефоне';
  }
  прогМасштабЭкрана(ctx);
}
/* Вид сетки в том виде, в каком его ждёт страница телефона. */
function прогВидСетки(ctx) {
  /* Экран показывает черновик: переключил — сразу видно, ещё до «Сохранить».
     Колонок нет: телефон рисует ровно четыре, сколько бы мы сюда ни вписали. */
  const с = прогСеткаЧерновик;
  /* Ряды макета — из черновика (нормализованные): макет должен показывать
     ровно то, что нарисует телефон, а не то, что осталось в старом
     settings.json. */
  return { cols: 4, rows: прогНормаРядов(с.rows), style: с.style,
           labels: !!с.labels };
}
/* Значки на экране — те, что вернул `/api/apps/preview` по черновику, а
   названия и цвета — из самого черновика: телефон рисует ровно это.
   Пункты подменю макет рисует теми же плитками, что и телефон: без них
   нажать на значок в макете было бы не на что. Название без текста показываем
   как «без названия» — иначе плитка подписывается пустотой и в подменю, и в
   списке. */
function прогПозиции(ctx) {
  return прогПриложения.map((п) => ({
    id: п.id,
    title: п.title,
    icon: п.icon,
    color: п.color || '',
    image: ctx.картинки.get(п.id) || '',
    menu: прогПунктыМеню(п),
  }));
}
/* Пункты подменю для макета: по виду, с названием и адресом или клавишами. */
function прогПунктыМеню(п) {
  return (Array.isArray(п.menu) ? п.menu : []).map((м) => {
    const вид = м.kind === 'site' ? 'site' : 'hotkey';
    const пункт = {
      kind: вид,
      title: String(м.title || '').trim() || 'без названия',
      icon: String(м.icon || ''),
    };
    if (вид === 'hotkey') пункт.keys = String(м.keys || '');
    else пункт.url = String(м.url || '');
    return пункт;
  });
}
function прогОтправитьЭкран(ctx) {
  if (!ctx.готов || !ctx.экран || !ctx.экран.contentWindow) return;
  ctx.экран.contentWindow.postMessage({
    type: 'truba-edit',
    items: прогПозиции(ctx),
    layout: прогВидСетки(ctx),
    selected: ctx.выбранный || '',
    /* Нижние кнопки — по черновику: переключил вид кнопки, и макет сразу её
       перерисовал, ещё до «Сохранить». */
    actions: прогВидКнопок(),
    action: Number.isInteger(ctx.слот) ? ctx.слот : null,
  }, location.origin);
}
function прогОбновитьЭкран(ctx) {
  if (ctx.таймерЭкрана) return;
  ctx.таймерЭкрана = setTimeout(() => {
    ctx.таймерЭкрана = null;
    прогОтправитьЭкран(ctx);
  }, ПРОГ_ПАУЗА_ЭКРАНА);
}
async function прогОбновитьПревью(ctx) {
  const номер = ctx.превьюЗапрос = (ctx.превьюЗапрос || 0) + 1;
  try {
    const ответ = await fetch('/api/apps/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apps: прогПриложения.map(прогГотовое) }),
    });
    const данные = await ответ.json();
    if (!ответ.ok || !данные.ok || номер !== ctx.превьюЗапрос) return;
    /* Значки кладём в карту, а не в черновик: `прогГотовое` их не шлёт, а на
       экране телефона именно картинка от `image`. */
    ctx.картинки = new Map((данные.items || []).map((п) => [п.id, п.image || '']));
    прогПриложения.forEach((п) => { п.image = ctx.картинки.get(п.id) || ''; });
    прогОбновитьЭкран(ctx);
    if (ctx.свойства && ctx.свойства.firstElementChild) {
      const значок = ctx.свойства.querySelector('.прог-образ');
      if (значок) прогНарисоватьОбраз(значок, прогПриложения[ctx.индекс]);
    }
  } catch (e) { /* Не мешаем редактированию, если предпросмотр недоступен. */ }
}
/* Значки пунктов меню. Те же линейные, что и у телефона, — иначе телефон
   нарисовал бы вместо микрофона общий квадрат и хозяин не понял бы, где что. */
const ПРОГ_ЗНАЧКИ_ПУНКТ = {
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>',
  headphones: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15v-3a8 8 0 0 1 16 0v3"/><path d="M4 15a2 2 0 0 1 2-2h1v7H6a2 2 0 0 1-2-2z"/><path d="M20 15a2 2 0 0 0-2-2h-1v7h1a2 2 0 0 0 2-2z"/></svg>',
  keyboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h12"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
  camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 8a2 2 0 0 1 2-2h2l1.4-2h7.2L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="12.5" r="3.5"/></svg>',
  replay: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4"/><path d="M3 4v5h5"/><path d="M10.5 9.5l5 2.5-5 2.5z" fill="currentColor"/></svg>',
  browser: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="4"/><path d="M10 9.5l5 2.5-5 2.5z" fill="currentColor"/></svg>',
};
/* Имя клавиши из keydown. Модификаторы в key не входят, поэтому пишем их
   отдельно — иначе «Ctrl» попал бы в поле вместо `ctrl`. */
function прогИмяКлавиши(событие) {
  const key = String(событие.key || '');
  if (['Control', 'Shift', 'Alt', 'Meta', 'CapsLock'].includes(key)) return '';
  /* Сначала физическая клавиша (`code`): на русской раскладке `key` — это
     «л» вместо K, и сочетание не ловилось вовсе (02.10). Сервер ждёт
     латиницу — ту же, что подписана на клавише. */
  const code = String(событие.code || '');
  let м = /^Key([A-Z])$/.exec(code);
  if (м) return м[1].toLowerCase();
  м = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  if (м) return м[1];
  if (/^F([1-9]|1\d|2[0-4])$/.test(code)) return code.toLowerCase();
  if (key === ' ' || key === 'Spacebar') return 'space';
  if (key === 'Escape') return 'esc';
  if (key === 'Enter') return 'enter';
  if (key === 'Tab') return 'tab';
  if (key === 'ArrowLeft') return 'left';
  if (key === 'ArrowUp') return 'up';
  if (key === 'ArrowRight') return 'right';
  if (key === 'ArrowDown') return 'down';
  if (/^F([1-9]|1\d|2[0-4])$/.test(key)) return key.toLowerCase();
  if (key.length === 1 && /^[a-z0-9]$/i.test(key)) return key.toLowerCase();
  return '';
}
/* Из нажатия — строка сочетания в том виде, в каком её ждёт сервер.
   Порядок тот же, что на сервере: ctrl, shift, alt, win, потом клавиша. */
function прогСочетаниеИзСобытия(событие) {
  const имя = прогИмяКлавиши(событие);
  if (!имя) return '';
  const части = [];
  if (событие.ctrlKey) части.push('ctrl');
  if (событие.shiftKey) части.push('shift');
  if (событие.altKey) части.push('alt');
  if (событие.metaKey) части.push('win');
  if (!части.length) return '';
  return части.concat(имя).join('+');
}
function прогПолеПункта(узел, имя, ярлык, значение, подсказка) {
  const об = document.createElement('label');
  об.className = 'прог-поле';
  const яр = document.createElement('span');
  яр.textContent = ярлык;
  const ввод = document.createElement('input');
  ввод.type = 'text';
  ввод.value = значение === undefined || значение === null ? '' : String(значение);
  ввод.setAttribute('data-меню-поле', имя);
  if (подсказка) { ввод.placeholder = подсказка; ввод.title = подсказка; }
  об.appendChild(яр);
  об.appendChild(ввод);
  узел.appendChild(об);
  return ввод;
}
function прогПостроитьПункт(пункт, ctx, перерисовать, соседи) {
  const вид = пункт.kind === 'site' ? 'site' : 'hotkey';
  const узел = document.createElement('div');
  узел.className = 'прог-меню-пункт';
  узел.setAttribute('data-меню-пункт', вид);

  const знак = document.createElement('span');
  знак.className = 'прог-меню-знак';
  знак.title = 'Значок пункта на телефоне';
  знак.innerHTML = ПРОГ_ЗНАЧКИ_ПУНКТ[пункт.icon]
    || ПРОГ_ЗНАЧКИ_ПУНКТ[вид === 'site' ? 'link' : 'keyboard'];
  узел.appendChild(знак);

  const сетка = document.createElement('div');
  сетка.className = 'прог-меню-сетка';
  прогПолеПункта(сетка, 'title', 'название', пункт.title, 'Микрофон');
  if (вид === 'hotkey') {
    const поле = прогПолеПункта(сетка, 'keys', 'сочетание', пункт.keys, 'ctrl+shift+alt+m');
    // Ловим нажатое сочетание и пишем его строкой. Вручную тоже можно:
    // хозяину проще вписать, чем вспоминать, что там у Discord.
    поле.addEventListener('keydown', (событие) => {
      const текст = прогСочетаниеИзСобытия(событие);
      const самМодификатор = ['Control', 'Shift', 'Alt', 'Meta', 'CapsLock']
        .includes(String(событие.key || ''));
      if (!текст) {
        if (!самМодификатор) {
          событие.preventDefault();
          прогСказать(ctx.статус,
            'Нужен модификатор: ctrl, shift, alt или win, а потом клавиша.', true);
        }
        return;
      }
      событие.preventDefault();
      поле.value = текст;
      /* Сначала в данные, потом перерисовка: `перерисовать` строит панель из
         `прогПриложения`, и без сбора пойманное сочетание тут же затиралось
         прежним — «нажимаю, а в строку не записывается» (02.10). */
      прогСобратьВсе(ctx.список);
      перерисовать();
      прогСказать(ctx.статус, 'Сочетание поймано: ' + текст, false);
    });

    /* Переключатель: телефон будет помнить, что уже «выключено», и подсвечивать
       это. Состояние — наш счёт нажатий (RPC программы закрыт), поэтому
       удержание плитки на телефоне его поправляет, ничего не нажимая. */
    const обГал = document.createElement('label');
    обГал.className = 'прог-меню-галочка';
    const гал = document.createElement('input');
    гал.type = 'checkbox';
    гал.setAttribute('data-меню-поле', 'toggle');
    гал.checked = пункт.toggle === true;
    const надпись = document.createElement('span');
    надпись.textContent = 'Переключатель (помнить вкл/выкл)';
    надпись.title = 'Телефон подсветит, что этот пункт уже «выключен»';
    обГал.appendChild(гал);
    обГал.appendChild(надпись);
    сетка.appendChild(обГал);

    /* «Включает также…» — честная поправка для случаев вроде Discord, где
       выключенный звук выключает и микрофон. Связать можно только с другим
       пунктом этой же программы. */
    const обСвязь = document.createElement('label');
    обСвязь.className = 'прог-поле прог-меню-связь';
    const ярСвязь = document.createElement('span');
    ярСвязь.textContent = 'включает также…';
    const связь = document.createElement('select');
    связь.setAttribute('data-меню-поле', 'implies');
    связь.title = 'Пока этот пункт «выключен», связанный тоже будет подсвечен';
    const пустаяСвязь = document.createElement('option');
    пустаяСвязь.value = '';
    пустаяСвязь.textContent = '— ничего —';
    связь.appendChild(пустаяСвязь);
    (соседи || []).forEach((другой) => {
      if (!другой || другой === пункт || !другой.id) return;
      const opt = document.createElement('option');
      opt.value = другой.id;
      opt.textContent = (другой.title || другой.id) + ' (' + другой.id + ')';
      if (пункт.implies === другой.id) opt.selected = true;
      связь.appendChild(opt);
    });
    обСвязь.appendChild(ярСвязь);
    обСвязь.appendChild(связь);
    сетка.appendChild(обСвязь);
  } else {
    прогПолеПункта(сетка, 'url', 'адрес', пункт.url, 'https://github.com');
  }
  узел.appendChild(сетка);

  const панель = document.createElement('div');
  панель.className = 'прог-меню-панель';
  const выбор = document.createElement('select');
  выбор.setAttribute('data-меню-поле', 'icon');
  выбор.className = 'прог-меню-значки';
  выбор.title = 'Значок пункта на телефоне';
  const пусто = document.createElement('option');
  пусто.value = '';
  пусто.textContent = 'значок';
  выбор.appendChild(пусто);
  Object.keys(ПРОГ_ЗНАЧКИ_ПУНКТ).forEach((имя) => {
    const opt = document.createElement('option');
    opt.value = имя;
    opt.textContent = имя;
    if (пункт.icon === имя) opt.selected = true;
    выбор.appendChild(opt);
  });
  панель.appendChild(выбор);

  // Изменения в полях не записаны в модель, пока не нажата кнопка. Поэтому
  // сначала собираем всю строку — иначе перестановка затирала бы правку.
  const мояКнопка = () => {
    const ряд = узел.closest('.прог-строка');
    прогСобратьВсе(ctx.список);
    return прогПриложения[Number(ряд.dataset.индекс)];
  };
  const кнопка = (текст, подсказка, мод, действие) => {
    const к = document.createElement('button');
    к.type = 'button';
    к.className = 'прог-кнопка' + мод;
    к.textContent = текст;
    к.title = подсказка;
    к.addEventListener('click', действие);
    панель.appendChild(к);
    return к;
  };
  /* Номер пункта — по месту узла среди пунктов своей строки, а не
     `indexOf(пункт)`: `мояКнопка()` пересобирает меню из формы НОВЫМИ
     объектами, и старый `пункт` в нём не находился — «✕» и «↑↓» молча
     ничего не делали (02.10, отзыв хозяина о 0.9.8). Номер берём ДО сборки:
     после перерисовки узел отсоединён. */
  const мойНомер = () => {
    const ряд = узел.closest('.прог-строка');
    if (!ряд) return -1;
    return Array.from(ряд.querySelectorAll('[data-меню-пункт]')).indexOf(узел);
  };
  const сдвинуть = (шаг) => {
    const откуда = мойНомер();
    const тек = мояКнопка();
    const список = тек.menu;
    if (откуда < 0 || !список || откуда >= список.length) return;
    const куда = откуда + шаг;
    if (куда < 0 || куда >= список.length) return;
    const с = список.splice(откуда, 1)[0];
    список.splice(куда, 0, с);
    перерисовать();
    прогСказать(ctx.статус, 'Порядок пункта изменён. Не забудь «Сохранить».', false);
  };
  const убрать = () => {
    const где = мойНомер();
    const тек = мояКнопка();
    if (где < 0 || !тек.menu || где >= тек.menu.length) return;
    тек.menu.splice(где, 1);
    перерисовать();
    прогСказать(ctx.статус, 'Пункт убран. Не забудь «Сохранить».', false);
  };
  кнопка('↑', 'Выше в меню', '', () => сдвинуть(-1));
  кнопка('↓', 'Ниже в меню', '', () => сдвинуть(1));
  кнопка('✕', 'Убрать пункт', ' убрать', убрать);
  узел.appendChild(панель);
  return узел;
}
function прогПостроитьМеню(п, ctx) {
  const об = document.createElement('div');
  об.className = 'прог-меню';
  const ряд = () => об.closest('.прог-строка');
  const перерисовать = () => прогПерерисовать(ctx);

  const шапка = document.createElement('button');
  шапка.type = 'button';
  шапка.className = 'прог-меню-шапка';
  const тело = document.createElement('div');
  тело.className = 'прог-меню-тело';
  // Пустое меню свёрнуто: иначе у семи программ семь пустых разделов.
  тело.hidden = !(Array.isArray(п.menu) && п.menu.length) && !п.bookmarks;
  const списокEl = document.createElement('div');
  списокEl.className = 'прог-меню-список';
  тело.appendChild(списокEl);

  const добавление = document.createElement('div');
  добавление.className = 'прог-меню-добавление';
  const сайтКнопка = document.createElement('button');
  сайтКнопка.type = 'button';
  сайтКнопка.className = 'голос-кнопка';
  сайтКнопка.textContent = '+ Сайт';
  сайтКнопка.title = 'Открывать адрес в этой программе, а не в чужом браузере';
  const клавишиКнопка = document.createElement('button');
  клавишиКнопка.type = 'button';
  клавишиКнопка.className = 'голос-кнопка';
  клавишиКнопка.textContent = '+ Сочетание клавиш';
  клавишиКнопка.title = 'Нажать на компьютере за тебя';
  добавление.appendChild(сайтКнопка);
  добавление.appendChild(клавишиКнопка);
  тело.appendChild(добавление);

  const добавить = (вид) => () => {
    /* Номер строки берём ДО перерисовки: `прогПерерисовать` чистит
       `ctx.свойства.innerHTML`, и узел меню `об` после этого отсоединён от
       DOM — `об.closest('.прог-строка')` даёт null, а `null.dataset` роняет
       обработчик. Из-за этого «+ Сайт» не добавлял пункт вовсе. */
    const строкаEl = ряд();
    const номер = строкаEl ? Number(строкаEl.dataset.индекс) : -1;
    /* Правки в полях ещё не в модели: собираем их, иначе новый пункт встал бы
       в список поверх несохранённого. */
    прогСобратьВсе(ctx.список);
    прогДобавитьПункт(ctx, номер, вид);
  };
  сайтКнопка.addEventListener('click', добавить('site'));
  клавишиКнопка.addEventListener('click', добавить('hotkey'));

  // Панель закладок есть только у Firefox: это его файл, и у другой
  // программы закладок не бывает — голочка смотрелась бы обещанием впустую.
  const этоFirefox = /firefox/i.test(
    String(п.id || '') + ' ' + String(п.title || '') + ' ' + String(п.path || ''));
  if (этоFirefox) {
    const обГал = document.createElement('label');
    обГал.className = 'прог-меню-галочка';
    const гал = document.createElement('input');
    гал.type = 'checkbox';
    гал.setAttribute('data-поле', 'bookmarks');
    гал.checked = п.bookmarks === 'firefox';
    const надпись = document.createElement('span');
    надпись.textContent = 'Показывать панель закладок Firefox';
    надпись.title = 'Закладки с панели добавятся в меню после ручных пунктов, с их логотипами';
    обГал.appendChild(гал);
    обГал.appendChild(надпись);
    тело.appendChild(обГал);
  }

  const подсказка = document.createElement('div');
  подсказка.className = 'прог-меню-подсказка';
  подсказка.textContent = 'Пункты идут на телефон в этом порядке. Телефон добавит сверху «Открыть программу». Переключатель телефон запомнит и подсветит, а удержанием плитки подсветку можно поправить, не нажимая клавиши. После правок нажми «Сохранить».';

  const перерисоватьПункты = () => {
    списокEl.innerHTML = '';
    const меню = Array.isArray(п.menu) ? п.menu : [];
    if (!меню.length) {
      const пусто = document.createElement('div');
      пусто.className = 'прог-пусто';
      пусто.textContent = 'Подменю пустое — добавь сочетание клавиш или сайт.';
      списокEl.appendChild(пусто);
    } else {
      /* Соседи нужны пункту для выбора «включает также…»: связать можно
         только с другим пунктом этой же программы. */
      меню.forEach((пункт) =>
        списокEl.appendChild(
          прогПостроитьПункт(пункт, ctx, перерисовать, меню)));
    }
    const сколько = меню.length ? ' · ' + меню.length : '';
    шапка.textContent = 'Меню на телефоне' + сколько + (тело.hidden ? ' ▾' : ' ▴');
    подсказка.hidden = тело.hidden;
  };
  перерисоватьПункты();

  шапка.addEventListener('click', () => {
    тело.hidden = !тело.hidden;
    перерисоватьПункты();
  });
  об.appendChild(шапка);
  об.appendChild(тело);
  об.appendChild(подсказка);
  return об;
}
/* Крупный значок в «Свойствах» — тот же рисунок, что на экране телефона:
   картинка, если она есть, иначе рисованный значок его цвета. Разметка в
   `innerHTML` приходит только из набора значков ниже: вставок сюда хозяин
   не делает. */
function прогНарисоватьОбраз(узел, п) {
  узел.innerHTML = '';
  узел.style.color = '';
  if (!п) { узел.classList.add('пусто'); узел.textContent = '?'; return; }
  узел.classList.remove('пусто');
  if (п.image) {
    const картинка = document.createElement('img');
    картинка.src = п.image;
    картинка.alt = '';
    узел.appendChild(картинка);
    return;
  }
  узел.innerHTML = ПРОГ_ЗНАЧКИ_ПРОГРАММ[п.icon] || ПРОГ_ЗНАЧКИ_ПРОГРАММ.app;
  узел.style.color = п.color || '';
}
/* Рисунки программ — те же, что телефон рисует сам, когда у кнопки нет
   своей картинки. Набор один на пульт и телефон, иначе хозяин выбрал бы
   значок, а на экране был бы другой. */
const ПРОГ_ЗНАЧКИ_ПРОГРАММ = {
  app: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg>',
  browser: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.5 7.2L4 21l1.8-5A8 8 0 1 1 21 12z"/></svg>',
  code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l-6-6 6-6M15 6l6 6-6 6"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="4"/><path d="M10 9.5l5 2.5-5 2.5z" fill="currentColor"/></svg>',
  camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 8a2 2 0 0 1 2-2h2l1.4-2h7.2L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="12.5" r="3.5"/></svg>',
  replay: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4"/><path d="M3 4v5h5"/><path d="M10.5 9.5l5 2.5-5 2.5z" fill="currentColor"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8"/></svg>',
  note: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>',
  sound: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>',
};
/* Свойства выбранной программы. Панель одна, поэтому номер строки в списке
   ей не нужен — но `data-индекс` остаётся: по нему пункты меню находят свою
   программу, когда список переставили. */
function прогПостроитьСтроку(п, индекс, ctx) {
  const строка = document.createElement('div');
  строка.className = 'прог-строка';
  // Номер строки в списке: по нему пункты меню находят свою программу,
  // когда список переставили, а номер «по памяти» уже не годится.
  строка.dataset.индекс = String(индекс);
  const шапка = document.createElement('div');
  шапка.className = 'прог-шапка';
  const образ = document.createElement('div');
  образ.className = 'прог-образ';
  прогНарисоватьОбраз(образ, п);
  шапка.appendChild(образ);
  const подпись = document.createElement('div');
  подпись.className = 'прог-подпись';
  const сколькоМеню = (п.menu || []).length;
  подпись.textContent = (п.title || 'Без названия')
    + (сколькоМеню ? ' · меню: ' + сколькоМеню : '');
  подпись.title = п.kind === 'app' ? String(п.path || '') : п.kind === 'url' ? String(п.url || '') : String(п.app_id || '');
  if (ctx.проверено && ctx.проверено.get(п.id) === false) {
    строка.classList.add('не-найдена');
    подпись.textContent += ' · программа не найдена';
  }
  const двиг = document.createElement('div');
  двиг.className = 'прог-двиг';
  const сделатьКнопку = (т, подсказка, мод) => {
    const к = document.createElement('button');
    к.className = 'прог-кнопка' + мод;
    к.type = 'button'; к.textContent = т; к.title = подсказка;
    двиг.appendChild(к);
    return к;
  };
  const раньше = сделатьКнопку('←', 'Поставить на место раньше', '');
  const позже = сделатьКнопку('→', 'Поставить на место позже', '');
  const пуск = сделатьКнопку('Открыть', 'Запустить сейчас', ' пуск');
  const убрать = сделатьКнопку('Удалить', 'Убрать программу с телефона', ' убрать');
  шапка.appendChild(подпись); шапка.appendChild(двиг);
  строка.appendChild(шапка);
  const сетка = document.createElement('div');
  сетка.className = 'прог-сетка';
  прогПоле(сетка, 'id', 'имя кнопки', 'text', п.id, 'moyblog',
    'Служебное имя латиницей. Оставь пустым — придумаем сами из названия', false);
  прогПоле(сетка, 'kind', 'что открывает', 'select', п.kind, '', '', false);
  прогПоле(сетка, 'title', 'название на телефоне', 'text', п.title, 'Блокнот', '', true);
  /* Источник значка — не список, а три кнопки под набором рисунков: хозяину
     нужен сам рисунок, а не слово «drawn». Значение всё равно едет в
     `icon_source`, поэтому поле скрытое и сбор строки не меняется. */
  const источник = document.createElement('input');
  источник.type = 'text';
  источник.dataset.поле = 'icon_source';
  источник.value = п.icon_source || 'drawn';
  источник.hidden = true;
  сетка.appendChild(источник);
  const обIcon = document.createElement('div');
  обIcon.className = 'прог-поле широкий';
  обIcon.setAttribute('data-поле-обёртка', 'icon');
  const iconЯр = document.createElement('span');
  iconЯр.textContent = 'значок';
  обIcon.appendChild(iconЯр);
  const iconВвод = document.createElement('input');
  iconВвод.type = 'text';
  iconВвод.dataset.поле = 'icon';
  iconВвод.value = п.icon || 'app';
  iconВвод.hidden = true;
  обIcon.appendChild(iconВвод);
  /* Источники значка — три кнопки-переключателя, как строка «Сетка» над
     экраном: слова «рисованный»/«из программы» хозяину ничего не говорят, а
     видно тут же, что нажато. */
  const источники = document.createElement('div');
  источники.className = 'прог-переключатели';
  for (const [значение, подпись, намёк] of [
    ['drawn', 'Из набора', 'Телефон нарисует значок сам'],
    ['file', 'Из файла…', 'Своя картинка'],
    ['exe', 'Из самой программы', 'Возьмёт значок из файла программы'],
  ]) {
    const к = document.createElement('button');
    к.type = 'button';
    к.textContent = подпись;
    к.title = намёк;
    к.setAttribute('aria-label', подпись);
    к.dataset.источник = значение;
    источники.appendChild(к);
  }
  обIcon.appendChild(источники);
  const iconСетка = document.createElement('div');
  iconСетка.className = 'прог-значки';
  iconСетка.setAttribute('data-блок', 'набор');
  Object.keys(ПРОГ_ЗНАЧКИ_ПРОГРАММ).forEach((имя) => {
    const к = document.createElement('button');
    к.type = 'button';
    к.className = 'прог-значок';
    к.title = 'значок «' + имя + '»';
    к.setAttribute('aria-label', 'значок ' + имя);
    /* Имя значка в подписи можно не искать по строке: поменяем текст — и
       выбор значка поедет вместе с ним. */
    к.dataset.имя = имя;
    к.innerHTML = ПРОГ_ЗНАЧКИ_ПРОГРАММ[имя];
    if ((п.icon || 'app') === имя) к.classList.add('выбран');
    iconСетка.appendChild(к);
  });
  обIcon.appendChild(iconСетка);
  /* Цвет рисованного значка: телефон красит им плитку, а без него берёт свой,
     по названию программы. */
  const цветРяд = document.createElement('div');
  цветРяд.className = 'прог-файл-ряд';
  цветРяд.setAttribute('data-блок', 'цвет');
  const цветЯр = document.createElement('span');
  цветЯр.className = 'прог-файл-намёк';
  цветЯр.textContent = 'цвет значка';
  const цвет = document.createElement('input');
  цвет.type = 'color';
  цвет.dataset.поле = 'color';
  цвет.value = /^#[0-9a-f]{6}$/i.test(String(п.color || '')) ? п.color : '#9aa7ff';
  const цветСброс = document.createElement('button');
  цветСброс.type = 'button';
  цветСброс.className = 'голос-кнопка';
  цветСброс.textContent = 'Цвет телефона';
  цветСброс.title = 'Убрать свой цвет: телефон возьмёт свой, по названию программы';
  цветРяд.appendChild(цветЯр);
  цветРяд.appendChild(цвет);
  цветРяд.appendChild(цветСброс);
  обIcon.appendChild(цветРяд);
  сетка.appendChild(обIcon);
  const файлОб = document.createElement('div');
  файлОб.className = 'прог-поле широкий';
  файлОб.setAttribute('data-поле-обёртка', 'icon_file');
  файлОб.setAttribute('data-блок', 'файл');
  const файлЯр = document.createElement('span');
  файлЯр.textContent = 'свой файл';
  файлОб.appendChild(файлЯр);
  const файлРяд = document.createElement('div');
  файлРяд.className = 'прог-файл-ряд';
  const файлВвод = document.createElement('input');
  файлВвод.type = 'file';
  файлВвод.accept = 'image/*';
  файлВвод.hidden = true;
  файлВвод.setAttribute('data-поле-ввод', 'icon_file');
  const файлКнопка = document.createElement('button');
  файлКнопка.type = 'button';
  файлКнопка.className = 'голос-кнопка';
  файлКнопка.textContent = 'Картинка…';
  const программаКнопка = document.createElement('button');
  программаКнопка.type = 'button';
  программаКнопка.className = 'голос-кнопка';
  программаКнопка.textContent = 'Значок из программы…';
  const файлНамёк = document.createElement('span');
  файлНамёк.className = 'прог-файл-намёк';
  файлНамёк.textContent = п.icon_file || 'файл не выбран';
  файлРяд.appendChild(файлВвод);
  файлРяд.appendChild(файлКнопка);
  файлРяд.appendChild(программаКнопка);
  файлРяд.appendChild(файлНамёк);
  файлОб.appendChild(файлРяд);
  сетка.appendChild(файлОб);
  const путьВвод = прогПоле(сетка, 'path', 'файл программы', 'text', п.path || '', 'C:\\Windows\\notepad.exe', '', true);
  const выбратьПуть = document.createElement('button');
  выбратьПуть.type = 'button';
  выбратьПуть.className = 'голос-кнопка прог-выбрать-путь';
  выбратьПуть.textContent = 'Выбрать файл…';
  путьВвод.parentElement.appendChild(выбратьПуть);
  прогПоле(сетка, 'how', 'способ запуска', 'select', п.how || 'shell', '', '', false);
  прогПоле(сетка, 'args', 'параметры запуска', 'text', прогАргументыВСтроку(п.args), '--incognito "C:\\Мои файлы\\x.txt"', 'Обычно можно оставить пустым; кавычки нужны для путей с пробелами', true);
  прогПоле(сетка, 'url', 'адрес сайта', 'text', п.url || '', 'kinopoisk.ru', 'Можно без https:// — добавится автоматически', true);
  прогПоле(сетка, 'app_id', 'ID приложения Windows', 'text', п.app_id || '', 'Microsoft.!…', 'Идентификатор приложения из Магазина Windows', true);
  строка.appendChild(сетка);
  строка.appendChild(прогПостроитьМеню(п, ctx));
  прогПрименитьВид(строка);
  прогПрименитьЗначок(строка);
  const освежить = () => {
    const тек = прогПриложения[индекс];
    if (!тек) return;
    const сколько = (тек.menu || []).length;
    подпись.textContent = (тек.title || 'Без названия')
      + (сколько ? ' · меню: ' + сколько : '');
    подпись.title = тек.kind === 'app' ? String(тек.path || '')
      : String(тек.url || тек.app_id || '');
    прогНарисоватьОбраз(образ, тек);
    строка.classList.remove('не-найдена');
    if (ctx.проверено) ctx.проверено.delete(п.id);
    прогОбновитьЭкран(ctx);
  };
  /* Значок из набора: источник всегда «рисованный», а выбор сразу виден на
     экране телефона — иначе пришлось бы гадать, какой рисунок взялся. */
  iconСетка.querySelectorAll('.прог-значок').forEach((к) => {
    к.addEventListener('click', () => {
      iconВвод.value = к.dataset.имя;
      источник.value = 'drawn';
      прогПрименитьЗначок(строка);
      const рез = прогСобратьСтроку(строка, индекс);
      if (рез && рез.ошибка) { прогСказать(ctx.статус, рез.ошибка, true); return; }
      освежить();
      прогОбновитьПревью(ctx);
    });
  });
  /* Переключатель источников: блок с рисунками и блок с файлом меняются
     местами, а значок на экране телефона — сразу. */
  источники.querySelectorAll('button').forEach((к) => {
    к.addEventListener('click', () => {
      источник.value = к.dataset.источник;
      прогПрименитьЗначок(строка);
      const рез = прогСобратьСтроку(строка, индекс);
      if (рез && рез.ошибка) { прогСказать(ctx.статус, рез.ошибка, true); return; }
      освежить();
      прогОбновитьПревью(ctx);
      /* Молча выбранный источник — это обида: значок не поменялся бы, а
         хозяин думал, что поменялся. */
      if (источник.value === 'file' && !прогПриложения[индекс].icon_file) {
        прогСказать(ctx.статус, 'Выбери картинку — нажми «Картинка…» или «Значок из программы…».', false);
      } else if (источник.value === 'exe' && !String(прогПриложения[индекс].path || '').trim()) {
        прогСказать(ctx.статус, 'Сначала укажи файл программы — значок брать неоткуда.', true);
      }
    });
  });
  цвет.addEventListener('input', () => {
    delete цвет.dataset.сброшен;
    const рез = прогСобратьСтроку(строка, индекс);
    if (рез && рез.ошибка) { прогСказать(ctx.статус, рез.ошибка, true); return; }
    освежить();
  });
  цветСброс.addEventListener('click', () => {
    цвет.value = '#9aa7ff';
    цвет.dataset.сброшен = '1';
    delete прогПриложения[индекс].color;
    освежить();
    прогОбновитьПревью(ctx);
    прогСказать(ctx.статус, 'Цвет телефона. Нажми «Сохранить».', false);
  });
  выбратьПуть.addEventListener('click', async () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.pick_program) {
      прогСказать(ctx.статус, 'Выбор файла работает в окне Трубы. В браузере путь можно вписать вручную.', true);
      return;
    }
    выбратьПуть.disabled = true;
    try {
      const данные = await api.pick_program(прогПриложения.map((item) => item.id));
      if (текущий !== 'программы' || !строка.isConnected || данные.cancelled) return;
      if (!данные.ok || !данные.app) throw new Error(данные.error || 'файл не выбран');
      путьВвод.value = данные.app.path;
      const название = строка.querySelector('[data-поле="title"]');
      if (название && !название.value.trim()) название.value = данные.app.title;
      прогСобратьСтроку(строка, индекс);
      освежить();
      прогОбновитьПревью(ctx);
      прогСказать(ctx.статус, 'Файл выбран. Нажми «Сохранить».', false);
    } catch (e) {
      прогСказать(ctx.статус, 'Не получилось выбрать файл: ' + (e && e.message ? e.message : e), true);
    } finally { выбратьПуть.disabled = false; }
  });
  /* Смена вида меняет и значок: ссылке или приложению Магазина «из самой
     программы» неоткуда взять, и пусть телефон рисует набор. */
  сетка.querySelector('[data-поле="kind"]').addEventListener('change', () => {
    прогСобратьСтроку(строка, индекс);
    прогПрименитьВид(строка);
    прогПрименитьЗначок(строка);
    освежить();
    прогОбновитьПревью(ctx);
  });
  файлКнопка.addEventListener('click', () => файлВвод.click());
  программаКнопка.addEventListener('click', async () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api || !api.pick_icon) {
      прогСказать(ctx.статус, 'Значок из программы выбирается в окне Трубы, не во вкладке браузера.', true);
      return;
    }
    const ключ = прогВзять(строка, 'id').trim();
    if (!/^[A-Za-z0-9._-]{1,64}$/.test(ключ)) {
      прогСказать(ctx.статус, 'Сначала задай id кнопки латиницей без пробелов.', true);
      return;
    }
    программаКнопка.disabled = true;
    try {
      const данные = await api.pick_icon(ключ);
      if (данные.cancelled) return;
      if (!данные.ok) throw new Error(данные.error || 'не удалось взять значок');
      const тек = прогПриложения[индекс];
      тек.icon_source = 'file';
      тек.icon_file = String(данные.icon_file || '');
      const сел = строка.querySelector('[data-поле="icon_source"]');
      if (сел) сел.value = 'file';
      файлНамёк.textContent = тек.icon_file;
      прогПрименитьЗначок(строка);
      прогОбновитьПревью(ctx);
      прогСказать(ctx.статус, 'Значок готов. Нажми «Сохранить», чтобы отправить его на телефон.', false);
    } catch (e) {
      прогСказать(ctx.статус, 'Не вышло: ' + (e && e.message ? e.message : e), true);
    } finally { программаКнопка.disabled = false; }
  });
  файлВвод.addEventListener('change', () => {
    const файл = файлВвод.files && файлВвод.files[0];
    if (!файл) return;
    if (файл.size > 5 * 1024 * 1024) {
      прогСказать(ctx.статус, 'Картинка должна быть не больше 5 МБ.', true);
      файлВвод.value = '';
      return;
    }
    const тек = прогПриложения[индекс];
    const ключ = String((тек && тек.id) || прогВзять(строка, 'id') || '').trim();
    if (!ключ) { прогСказать(ctx.статус, 'Строка ' + (индекс + 1) + ': сначала заполни id.', true); файлВвод.value = ''; return; }
    файлНамёк.textContent = 'Загружаю…';
    const читатель = new FileReader();
    читатель.onload = async () => {
      try {
        const ответ = await fetch('/api/apps/icon', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: ключ, data: String(читатель.result || '') }),
        });
        const данные = await ответ.json().catch(() => ({}));
        if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
        тек.icon_source = 'file';
        тек.icon_file = String(данные.icon_file || '');
        const сел = строка.querySelector('[data-поле="icon_source"]');
        if (сел) сел.value = 'file';
        файлНамёк.textContent = тек.icon_file || 'загружено';
        прогПрименитьЗначок(строка);
        прогОбновитьПревью(ctx);
        прогСказать(ctx.статус, 'Значок загружен. Нажми «Сохранить».', false);
      } catch (e) { файлНамёк.textContent = 'не загрузилось'; прогСказать(ctx.статус, 'Значок не загрузился: ' + (e && e.message ? e.message : e), true); }
      finally { файлВвод.value = ''; }
    };
    читатель.onerror = () => { файлНамёк.textContent = 'не прочиталось'; прогСказать(ctx.статус, 'Не смог прочитать файл картинки.', true); файлВвод.value = ''; };
    читатель.readAsDataURL(файл);
  });
  сетка.querySelectorAll('input[type="text"],select').forEach((el) => {
    if (el === файлВвод) return;
    const обновитьЗначок = () => {
      if (!['path', 'icon', 'icon_file', 'id', 'color'].includes(el.dataset.поле)) return;
      clearTimeout(ctx.таймерПревью);
      ctx.таймерПревью = setTimeout(() => {
        if (ctx.корень?.isConnected) прогОбновитьПревью(ctx);
      }, 450);
    };
    el.addEventListener('input', () => {
      const рез = прогСобратьСтроку(строка, индекс);
      if (рез && рез.ok) { освежить(); обновитьЗначок(); }
    });
    el.addEventListener('change', () => {
      const рез = прогСобратьСтроку(строка, индекс);
      if (рез && рез.ok) { освежить(); обновитьЗначок(); }
    });
  });
  раньше.addEventListener('click', () => {
    прогСобратьВсе(ctx.список); if (индекс <= 0) return;
    const т = прогПриложения[индекс - 1];
    прогПриложения[индекс - 1] = прогПриложения[индекс]; прогПриложения[индекс] = т;
    ctx.индекс = индекс - 1;
    прогПерерисовать(ctx);
    прогСказать(ctx.статус, 'Поставлено выше. Не забудь «Сохранить».', false);
  });
  позже.addEventListener('click', () => {
    прогСобратьВсе(ctx.список); if (индекс >= прогПриложения.length - 1) return;
    const т = прогПриложения[индекс + 1];
    прогПриложения[индекс + 1] = прогПриложения[индекс]; прогПриложения[индекс] = т;
    ctx.индекс = индекс + 1;
    прогПерерисовать(ctx);
    прогСказать(ctx.статус, 'Поставлено ниже. Не забудь «Сохранить».', false);
  });
  /* Удаление спрашиваем: программа уходит с телефона, а вернуть её придётся
     заново вписывать. */
  убрать.addEventListener('click', () => {
    if (!window.confirm('Убрать программу «' + ((п.title || п.id)) + '» с телефона?')) return;
    прогСобратьВсе(ctx.список);
    прогПриложения.splice(индекс, 1);
    ctx.индекс = Math.min(индекс, прогПриложения.length - 1);
    прогПерерисовать(ctx);
    прогОбновитьПревью(ctx);
    прогСказать(ctx.статус, 'Программа убрана. Не забудь «Сохранить».', false);
  });
  пуск.addEventListener('click', async () => {
    const рез = прогСобратьСтроку(строка, индекс);
    if (рез && рез.ошибка) { прогСказать(ctx.статус, рез.ошибка, true); return; }
    const тек = прогПриложения[индекс];
    if (!тек.id) { прогСказать(ctx.статус, 'Строка ' + (индекс + 1) + ': сначала заполни id.', true); return; }
    if (прогСохранённое.get(тек.id) !== JSON.stringify(прогГотовое(тек))) {
      прогСказать(ctx.статус, 'Сначала сохрани изменения — запуск берёт сохранённую кнопку.', true);
      return;
    }
    пуск.disabled = true;
    try {
      const ответ = await fetch('/api/apps/launch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: тек.id }),
      });
      const данные = await ответ.json().catch(() => ({}));
      if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
      прогСказать(ctx.статус, '«' + (тек.title || тек.id) + '» запущено.', false);
    } catch (e) { прогСказать(ctx.статус, 'Не запустилось: ' + (e && e.message ? e.message : e), true); }
    finally { пуск.disabled = false; }
  });
  return строка;
}
/* Правая колонка — свойства одной программы, как «Свойства» в Windows. Ничего
   не выбрано — подсказка с двумя способами начать. */
/* Панель «Кнопка внизу N»: что она делает и как это настроить. Поля — по
   виду, а вид меняется на лету: перерисовываем панель целиком, иначе остались
   бы поля от прежнего вида (например, программа без подменю).
   Названия панели дважды не печатаем: шапка правой колонки уже сказала
   «Кнопка внизу N» (см. `прогПерерисовать`). */
function прогПостроитьКнопку(ctx, слот) {
  const ячейка = прогКнопкиЧерновик[слот] || { ...ПРОГ_КНОПКИ_УМОЛЧАНИЕ[слот] };
  const панель = document.createElement('div');
  панель.className = 'прог-строка прог-низ-кнопка';
  панель.setAttribute('data-слот', String(слот));

  const сетка = document.createElement('div');
  сетка.className = 'прог-меню-сетка';
  панель.appendChild(сетка);

  const выбор = document.createElement('select');
  выбор.setAttribute('data-кнопка-поле', 'kind');
  выбор.title = 'Что делает эта кнопка на телефоне';
  ПРОГ_КНОПКИ_ВИДЫ.forEach(([значение, подпись]) => {
    const опция = document.createElement('option');
    /* Встроенные кнопки различаются id, поэтому в списке своё значение на
       каждую (`builtin:поиск`), а не один общий `builtin`. */
    опция.value = значение;
    опция.textContent = подпись;
    выбор.appendChild(опция);
  });
  const встроенная = ячейка.kind === 'builtin'
    ? 'builtin:' + String(ячейка.id) : String(ячейка.kind);
  выбор.value = ПРОГ_КНОПКИ_ВИДЫ.some(([з]) => з === встроенная) ? встроенная : 'none';
  сетка.appendChild(прогОбёрткаКнопки('Что делает', выбор));
  выбор.addEventListener('change', () => {
    const [вид, id] = String(выбор.value).split(':');
    const новая = { kind: вид };
    if (вид === 'builtin') новая.id = id;
    /* Старые поля прежнего вида не переносим: у «сочетания» и у «пункта
       подменю» они разные, и тянуть их в другую ячейку — враньё. */
    if (вид === 'hotkey' && ячейка.kind === 'hotkey') {
      новая.title = ячейка.title; новая.icon = ячейка.icon; новая.keys = ячейка.keys;
    } else if (вид === 'hotkey') {
      новая.title = 'моя кнопка'; новая.icon = 'keyboard'; новая.keys = '';
    }
    if (вид === 'app' && ячейка.kind === 'app') новая.app = ячейка.app;
    else if (вид === 'app') новая.app = прогПриложения.length ? прогПриложения[0].id : '';
    if (вид === 'menu') {
      const сМеню = прогПрограммыСМеню();
      новая.app = (ячейка.kind === 'menu' && сМеню.some((п) => п.id === ячейка.app))
        ? ячейка.app : (сМеню.length ? сМеню[0].id : '');
      новая.item = ячейка.kind === 'menu' ? ячейка.item : '';
    }
    прогКнопкиЧерновик[слот] = новая;
    прогПерерисовать(ctx);
  });
  return прогДописатьПоляКнопки(ctx, слот, ячейка, панель, сетка);
}

/* Поля панели кнопки — по её виду, плюс кнопка возврата и намёк. */
function прогДописатьПоляКнопки(ctx, слот, ячейка, панель, сетка) {
  const обновить = () => { прогПометитьЧерновик(ctx); прогОбновитьЭкран(ctx); };

  if (ячейка.kind === 'hotkey') {
    сетка.appendChild(прогОбёрткаКнопки('название',
      прогВводКнопки(ctx, слот, 'title', ячейка.title, 'Смена сцены')));
    /* Значок — тем же списком, что у пунктов подменю: набор значков на
       телефон и на пульт один (иначе телефон нарисовал бы то, чего пульт и не
       предлагал). */
    const знак = document.createElement('select');
    знак.setAttribute('data-кнопка-поле', 'icon');
    знак.className = 'прог-меню-значки';
    знак.title = 'Значок кнопки на телефоне';
    Object.keys(ПРОГ_ЗНАЧКИ_ПУНКТ).forEach((имя) => {
      const опция = document.createElement('option');
      опция.value = имя;
      опция.textContent = имя;
      знак.appendChild(опция);
    });
    знак.value = ПРОГ_ЗНАЧКИ_ПУНКТ[ячейка.icon] ? ячейка.icon : 'keyboard';
    знак.addEventListener('change', () => {
      прогКнопкиЧерновик[слот].icon = знак.value;
      обновить();
    });
    сетка.appendChild(прогОбёрткаКнопки('значок', знак));

    /* Ввод сочетания — тот же, что у пункта подменю «+ Сочетание клавиш»:
       нажимаешь клавиши прямо в поле, они сами пишутся строкой. Второй
       такой ввод был бы двумя правдами об одном и том же. */
    const поле = прогВводКнопки(ctx, слот, 'keys', ячейка.keys, 'ctrl+shift+alt+m');
    поле.addEventListener('keydown', (событие) => {
      const текст = прогСочетаниеИзСобытия(событие);
      const самМодификатор = ['Control', 'Shift', 'Alt', 'Meta', 'CapsLock']
        .includes(String(событие.key || ''));
      if (!текст) {
        if (!самМодификатор) {
          событие.preventDefault();
          прогСказать(ctx.статус,
            'Нужен модификатор: ctrl, shift, alt или win, а потом клавиша.', true);
        }
        return;
      }
      событие.preventDefault();
      поле.value = текст;
      прогКнопкиЧерновик[слот].keys = текст;
      обновить();
      прогСказать(ctx.статус, 'Сочетание поймано: ' + текст, false);
    });
    сетка.appendChild(прогОбёрткаКнопки('сочетание', поле));
  }

  if (ячейка.kind === 'app' || ячейка.kind === 'menu') {
    const программы = document.createElement('select');
    программы.setAttribute('data-кнопка-поле', 'app');
    программы.title = 'Программа из списка «Программы»';
    /* Для пункта подменю — только те, у кого меню вообще есть: выбирать
       пункт не из чего, а предлагать пустой список — враньё. */
    const список = ячейка.kind === 'menu' ? прогПрограммыСМеню() : прогПриложения;
    if (!список.length) {
      const пусто = document.createElement('option');
      пусто.value = '';
      пусто.textContent = '— программ пока нет —';
      программы.appendChild(пусто);
      программы.disabled = true;
    }
    список.forEach((п) => {
      const опция = document.createElement('option');
      опция.value = п.id;
      опция.textContent = п.title || п.id;
      if (п.id === ячейка.app) опция.selected = true;
      программы.appendChild(опция);
    });
    программы.addEventListener('change', () => {
      прогКнопкиЧерновик[слот].app = программы.value;
      /* Программа сменилась — пункт прежней программы тут больше не подходит,
         поэтому пункт выбирается заново. */
      if (ячейка.kind === 'menu') прогКнопкиЧерновик[слот].item = '';
      прогПерерисовать(ctx);
    });
    сетка.appendChild(прогОбёрткаКнопки('программа', программы));
  }

  if (ячейка.kind === 'menu') {
    const программа = прогПриложения.find((п) => п.id === ячейка.app);
    const пункты = программа && Array.isArray(программа.menu) ? программа.menu : [];
    const выборПункта = document.createElement('select');
    выборПункта.setAttribute('data-кнопка-поле', 'item');
    выборПункта.title = 'Пункт подменю этой программы';
    if (!пункты.length) {
      const пусто = document.createElement('option');
      пусто.value = '';
      пусто.textContent = '— у программы нет подменю —';
      выборПункта.appendChild(пусто);
      выборПункта.disabled = true;
    }
    пункты.forEach((пункт) => {
      const опция = document.createElement('option');
      /* Ключ пункта на сервере — «вид:имя», ровно как в подменю на телефоне. */
      const ключ = (пункт.kind === 'site' ? 'site:' : 'hotkey:') + пункт.id;
      опция.value = ключ;
      опция.textContent = пункт.title || пункт.id;
      if (String(ячейка.item) === ключ) опция.selected = true;
      выборПункта.appendChild(опция);
    });
    выборПункта.addEventListener('change', () => {
      прогКнопкиЧерновик[слот].item = выборПункта.value;
      обновить();
    });
    сетка.appendChild(прогОбёрткаКнопки('пункт', выборПункта));
  }

  const низ = document.createElement('div');
  низ.className = 'прог-меню-панель';
  /* «Вернуть как было» — кнопка по умолчанию **для этого места**: у первого
     снимок экрана, у второго момент, и так далее. */
  const вернуть = document.createElement('button');
  вернуть.type = 'button';
  вернуть.className = 'прог-кнопка';
  вернуть.textContent = 'Вернуть как было';
  вернуть.title = 'Вернуть эту кнопку в исходное состояние';
  вернуть.addEventListener('click', () => {
    прогКнопкиЧерновик[слот] = { ...ПРОГ_КНОПКИ_УМОЛЧАНИЕ[слот] };
    прогПерерисовать(ctx);
    прогСказать(ctx.статус, 'Кнопка внизу ' + (слот + 1) + ' вернулась в исходное.', false);
  });
  низ.appendChild(вернуть);
  панель.appendChild(низ);

  const намёк = document.createElement('div');
  намёк.className = 'прог-намёк';
  /* Подсказка с примерами хозяина: без неё «Сочетание клавиш» звучит
     абстрактно, а хозяин спросил именно про это (28.09). */
  намёк.textContent = 'Например: сцена в OBS — назначь в OBS горячую клавишу '
    + 'и впиши её сюда; «заглушить себя» в Discord — пункт подменю Discord '
    + '«Микрофон».';
  панель.appendChild(намёк);
  return панель;
}

/* Обёртка поля кнопки — та же, что у полей программы. */
function прогОбёрткаКнопки(ярлык, узел) {
  const об = document.createElement('label');
  об.className = 'прог-поле';
  const надпись = document.createElement('span');
  надпись.textContent = ярлык;
  об.appendChild(надпись);
  об.appendChild(узел);
  return об;
}

/* Поле кнопки. Значения `<input>` — строки, поэтому проверять их надо
   `Number(...)` с проверкой, а не общей `число()`: та на строку отвечает
   `null` (coordination/ГРАБЛИ.md). */
function прогВводКнопки(ctx, слот, имя, значение, намёк) {
  const ввод = document.createElement('input');
  ввод.type = 'text';
  ввод.value = значение === undefined || значение === null ? '' : String(значение);
  ввод.placeholder = намёк || '';
  ввод.title = намёк || '';
  ввод.setAttribute('data-кнопка-поле', имя);
  ввод.addEventListener('input', () => {
    const ячейка = прогКнопкиЧерновик[слот];
    if (!ячейка) return;
    ячейка[имя] = ввод.value;
    /* Макет смотрит на черновик, поэтому подпись видна сразу, ещё до
       «Сохранить». */
    прогОбновитьЭкран(ctx);
  });
  return ввод;
}

/* Кнопка «← Все программы». Путь хозяина должен быть «список → свойства →
   назад к списку», а раньше из свойств выйти было некуда: единственный
   способ — угадать номер плитки на макете телефона. Ставим её в шапку
   правой колонки, где человек уже смотрит. */
function прогКнопкаНазад(ctx) {
  const к = document.createElement('button');
  к.type = 'button';
  к.className = 'голос-кнопка прог-назад';
  к.textContent = '← Все программы';
  к.title = 'Вернуться к списку всех программ';
  к.addEventListener('click', () => прогПоказатьСписок(ctx));
  return к;
}
function прогПоказатьСписок(ctx) {
  ctx.индекс = -1;
  ctx.слот = -1;
  ctx.выбранный = '';
  прогПерерисовать(ctx);
}
function прогПерерисовать(ctx) {
  ctx.свойства.innerHTML = '';
  ctx.список = ctx.свойства;
  /* Выбрана нижняя кнопка (ctx.слот) — вместо свойств программы панель
     кнопки: касание кнопки в макете приходит `truba-edit-action`. */
  /* Шапка тоже меняется: выбрана кнопка — это не «Свойства программы». */
  /* Кнопку «← Все программы» показываем ровно тогда, когда есть из чего
     возвращаться: выбрана нижняя кнопка или программа. */
  const выбранСлот = Number.isInteger(ctx.слот) && ctx.слот >= 0;
  const выбрано = выбранСлот
    || (Number.isInteger(ctx.индекс) && ctx.индекс >= 0 && !!прогПриложения[ctx.индекс]);
  if (ctx.шапка) {
    ctx.шапка.textContent = выбранСлот
      ? 'Кнопка внизу ' + (ctx.слот + 1) : 'Свойства программы';
  }
  if (ctx.шапкаРяд) ctx.шапкаРяд.hidden = !выбрано;
  if (выбранСлот) {
    ctx.свойства.appendChild(прогПостроитьКнопку(ctx, ctx.слот));
  } else {
    const п = прогПриложения[ctx.индекс];
    if (п) ctx.свойства.appendChild(прогПостроитьСтроку(п, ctx.индекс, ctx));
    else {
      const пусто = document.createElement('div');
      пусто.className = 'прог-пусто';
      /* Плитки «+» в сетке больше нет, поэтому и звать её нечем: добавление —
         кнопка «+ Программа» под экраном, как и подсказка под макетом. */
      пусто.textContent = 'Нажми значок на экране телефона, чтобы изменить его, '
        + 'или кнопку внизу — чтобы поменять, что она делает, '
        + 'или кнопка «+ Программа» под экраном — добавить новую.';
      ctx.свойства.appendChild(пусто);
    }
  }
  ctx.счёт.textContent = прогЗагрузка ? 'Загрузка…' : ('Программ: ' + прогПриложения.length);
  прогПометитьЧерновик(ctx);
  прогОбновитьЭкран(ctx);
}
/* Несохранённые правки видно по кнопке: иначе хозяин нажмёт «Сохранить» на
   другой вкладке и так и не узнает, что здесь что-то менял. */
function прогПометитьЧерновик(ctx) {
  const есть = прогЕстьЧерновик();
  ctx.сохранить.classList.toggle('несохранено', есть);
  ctx.пометка.hidden = !есть;
}
function прогЕстьЧерновик() {
  if (JSON.stringify(прогПриложения.map(прогГотовое)) !== прогСохранённоеСписок) return true;
  if (JSON.stringify(прогСеткаЧерновик) !== прогСеткаСохранённая) return true;
  return прогЕстьЧерновикКнопок();
}
async function прогПроверитьПути(ctx, кнопка) {
  const сбор = прогСобратьВсе(ctx.список);
  if (сбор.ошибка) { прогСказать(ctx.статус, сбор.ошибка, true); return; }
  кнопка.disabled = true;
  прогСказать(ctx.статус, 'Проверяю программы на компьютере…', false);
  try {
    const ответ = await fetch('/api/apps/check', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apps: прогПриложения.map(прогГотовое) }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    const результаты = Array.isArray(данные.results) ? данные.results : [];
    ctx.проверено = new Map(результаты.map(x => [x.id, !!x.found]));
    прогПерерисовать(ctx);
    const неНайдены = результаты.filter(x => !x.found).map(x => x.title || x.id || '?');
    прогСказать(ctx.статус, неНайдены.length
      ? 'Не нашёл: ' + неНайдены.join(', ') + '. Эти кнопки не появятся на телефоне, пока не исправишь путь.'
      : 'Все программы найдены. Ссылки и приложения Магазина не требуют проверки пути.', !!неНайдены.length);
  } catch (e) { прогСказать(ctx.статус, 'Проверка не удалась: ' + (e && e.message ? e.message : e), true); }
  finally { кнопка.disabled = false; }
}
async function прогСохранить(ctx, кнопка) {
  if (!прогПроверить(ctx.список, ctx.статус, true)) return;
  кнопка.disabled = true;
  прогСказать(ctx.статус, 'Сохраняю…', false);
  try {
    const тело = { apps: прогПриложения.map((п) => прогГотовое(п)) };
    const ответ = await fetch('/api/apps', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(тело),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные.ok) throw new Error(данные.error || ('HTTP ' + ответ.status));
    /* Сервер — последнее слово по именам: он придумывает их из русского
       названия и разводит повторы. Берём его ответ в черновик (порядок тот же),
       иначе в форме остался бы русский `id`, которого телефон не узнает. */
    if (Array.isArray(данные.apps) && данные.apps.length === прогПриложения.length) {
      данные.apps.forEach((сохранённая, номер) => {
        const п = прогПриложения[номер];
        if (!п || !сохранённая || !сохранённая.id) return;
        п.id = String(сохранённая.id);
        const пункты = Array.isArray(сохранённая.menu) ? сохранённая.menu : null;
        (п.menu || []).forEach((пункт, номерПункта) => {
          if (пункты && пункты[номерПункта] && пункты[номерПункта].id) {
            пункт.id = String(пункты[номерПункта].id);
          }
        });
      });
    }
    /* Вид сетки и нижние кнопки — ключи настроек. Их в `/api/settings` шлём
       ровно столько: чужие разделы пульт не должен переписывать под себя.
       Колонки не шлём: их всегда четыре, и хозяин их не выбирает. Шлём оба
       набора одним запросом — за два раза второй мог бы не уйти, и хозяин
       увидел бы «сохранено» при несохранённых кнопках. */
    if (прогЕстьЧерновикСетки() || прогЕстьЧерновикКнопок()) {
      const настройки = {};
      if (прогЕстьЧерновикСетки()) {
        настройки.phone_rows = прогСеткаЧерновик.rows;
        настройки.phone_icon_style = прогСеткаЧерновик.style;
        настройки.phone_labels = прогСеткаЧерновик.labels;
      }
      if (прогЕстьЧерновикКнопок()) {
        настройки.phone_actions = прогКнопкиЧерновик;
      }
      const сетка = await fetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(настройки),
      });
      const сеткаДанные = await сетка.json().catch(() => ({}));
      if (!сетка.ok || !сеткаДанные.ok) {
        throw new Error('список сохранён, а настройки телефона — нет: '
          + (сеткаДанные.error || ('HTTP ' + сетка.status)));
      }
      Object.assign(прогСетка, прогСеткаЧерновик);
      прогСеткаСохранённая = JSON.stringify(прогСетка);
      /* Кнопки сохранились, но сервер поправил кривые ячейки на свои
         умолчания. Ответ `/api/settings` — снимок настроек, а значения лежат
         в нём в поле `settings`; берём оттуда, иначе «несохранённые
         изменения» горели бы вечно. Не нашли — оставляем наш черновик: он
         уже проверен той же логикой. */
      const значения = сеткаДанные.settings && сеткаДанные.settings.settings;
      const ответ = значения && Array.isArray(значения.phone_actions)
        ? значения.phone_actions : прогКнопкиЧерновик;
      прогКнопки = ПРОГ_КНОПКИ_УМОЛЧАНИЕ.map((_, номер) =>
        прогНормаКнопки(ответ[номер], номер));
      прогКнопкиЧерновик = прогКнопки.map((ячейка) => ({ ...ячейка }));
      прогКнопкиСохранённые = JSON.stringify(прогКнопки);
    }
    /* После ответа сервера имена в черновике могли измениться. Снимок
       «сохранено» должен отражать уже их, иначе пульт сразу после успешного
       сохранения ложно покажет несохранённые изменения. */
    const сохранены = прогПриложения.map(прогГотовое);
    прогСохранённое = new Map(сохранены.map((п) => [п.id, JSON.stringify(п)]));
    прогСохранённоеСписок = JSON.stringify(сохранены);
    прогСказать(ctx.статус, 'Сохранено: программ ' + тело.apps.length + '.', false);
    прогПометитьЧерновик(ctx);
    /* Панель перерисовывается: в полях должны стоять те имена, что записал
       сервер, иначе хозяин увидит русский `id` и решит, что правка не взялась.
       Пул при этом остаётся открытым — вкладку никто не перезагружает. */
    прогПерерисовать(ctx);
    прогОбновитьПревью(ctx);
  } catch (e) { прогСказать(ctx.статус, 'Не сохранилось: ' + (e && e.message ? e.message : e), true); }
  finally { кнопка.disabled = false; }
}
function прогЕстьЧерновикСетки() {
  return JSON.stringify(прогСеткаЧерновик) !== JSON.stringify(прогСетка);
}

/* ---------- Нижние кнопки телефона ---------- */

/* Ячейка из настроек в ячейку редактора. Кривое значение — ячейка по
   умолчанию на своём месте: сервер проверяет так же (core/settings.py), и
   пульт не должен показывать то, что потом молча заменится. */
function прогНормаКнопки(ячейка, номер) {
  const умолчание = { ...ПРОГ_КНОПКИ_УМОЛЧАНИЕ[Math.min(nомер, 3)] };
  const cell = (ячейка && typeof ячейка === 'object') ? ячейка : {};
  const вид = String(cell.kind || '');
  if (вид === 'none') return { kind: 'none' };
  if (вид === 'builtin' && ПРОГ_КНОПКИ_ВСТРОЕННЫЕ[cell.id]) {
    return { kind: 'builtin', id: String(cell.id) };
  }
  if (вид === 'hotkey') {
    const title = String(cell.title || '').slice(0, 24);
    const keys = String(cell.keys || '').trim();
    /* Сочетание без названия или без клавиш — брать нечего: сервер такую
       ячейку всё равно заменил бы на кнопку по умолчанию. */
    if (!title || !keys) return умолчание;
    const icon = String(cell.icon || '');
    return {
      kind: 'hotkey', title, keys,
      icon: ПРОГ_ЗНАЧКИ_ПУНКТ[icon] ? icon : 'keyboard',
    };
  }
  if (вид === 'app' && String(cell.app || '').trim()) {
    return { kind: 'app', app: String(cell.app).trim() };
  }
  if (вид === 'menu' && String(cell.app || '').trim() && String(cell.item || '').trim()) {
    return { kind: 'menu', app: String(cell.app).trim(), item: String(cell.item).trim() };
  }
  return умолчание;
}

/* Список для макета телефона: то же, что рисует телефон по сообщению
   `actions`, но по черновику хозяина. Значок программы и пункта берём из
   `apps.json` черновика — тем же, чем на экране. */
function прогВидКнопок() {
  return прогКнопкиЧерновик.map((ячейка, slot) => {
    if (ячейка.kind === 'none') return null;
    if (ячейка.kind === 'builtin') {
      const встроенная = ПРОГ_КНОПКИ_ВСТРОЕННЫЕ[ячейка.id] || { title: 'кнопка', icon: 'app' };
      return { slot, kind: 'builtin', id: ячейка.id, title: встроенная.title, icon: встроенная.icon };
    }
    if (ячейка.kind === 'hotkey') {
      return { slot, kind: 'hotkey', title: ячейка.title, icon: ячейка.icon || 'keyboard' };
    }
    if (ячейка.kind === 'app') {
      const программа = прогПриложения.find((п) => п.id === ячейка.app);
      return {
        slot, kind: 'app', id: ячейка.app,
        title: (программа && программа.title) || ячейка.app,
        icon: (программа && программа.icon) || 'app',
        image: (программа && программа.image) || '',
      };
    }
    /* Пункт подменю: заголовок и значок — из того пункта, который хозяин
       выбрал. Не нашли — показываем его ключ, нажать всё равно можно.
       Ключ ячейки — «вид:имя» (как на сервере), а у пункта в черновике одно
       `id`: префикс вида отбрасываем. */
    const программа = прогПриложения.find((п) => п.id === ячейка.app);
    const имя = String(ячейка.item || '').split(':').pop();
    const пункт = программа && (программа.menu || []).find((м) => м.id === имя);
    return {
      slot, kind: 'menu', id: ячейка.item, app: ячейка.app,
      title: (пункт && пункт.title) || имя,
      icon: (пункт && ПРОГ_ЗНАЧКИ_ПУНКТ[пункт.icon]) ? пункт.icon : 'keyboard',
    };
  }).filter(Boolean);
}

function прогЕстьЧерновикКнопок() {
  return JSON.stringify(прогКнопкиЧерновик) !== прогКнопкиСохранённые;
}

/* Список программ, у которых есть подменю: выбирать пункт не из чего, если
   меню нет. */
function прогПрограммыСМеню() {
  return прогПриложения.filter((п) => Array.isArray(п.menu) && п.menu.length);
}
function прогГотовое(п) {
  const б = { id: п.id.trim(), title: String(п.title || ''), kind: п.kind, icon: String(п.icon || '') };
  if (п.icon_source) б.icon_source = п.icon_source;
  if (п.icon_source === 'file' && п.icon_file) б.icon_file = п.icon_file;
  if (/^#[0-9a-f]{6}$/i.test(String(п.color || ''))) б.color = String(п.color);
  if (п.kind === 'app') {
    б.path = String(п.path || ''); б.args = Array.isArray(п.args) ? п.args : [];
    б.how = п.how === 'direct' ? 'direct' : 'shell';
    if (String(п.process || '').trim()) б.process = String(п.process).trim();
  } else if (п.kind === 'url') {
    const адрес = String(п.url || '').trim();
    б.url = адрес && !адрес.includes('://') ? 'https://' + адрес : адрес;
  } else if (п.kind === 'folder') {
    б.path = String(п.path || '').trim();
  }
  else { б.app_id = String(п.app_id || '').trim(); }
  if (Array.isArray(п.aliases) && п.aliases.length) б.aliases = п.aliases.map((a) => String(a));
  if (!б.icon) delete б.icon;
  /* Пункт отсеиваем по названию, а не по имени: имя придумывает сервер из
     названия, и пустое поле `id` у только что добавленного пункта — это
     нормально, а не повод его выбросить (так терялся пункт подменю). */
  const меню = Array.isArray(п.menu) ? п.menu
    .filter((м) => String(м.title || '').trim())
    .map((м) => {
      const вид = м.kind === 'site' ? 'site' : 'hotkey';
      const пункт = { kind: вид, id: String(м.id || '').trim(), title: String(м.title).trim() };
      if (!пункт.id) delete пункт.id;
      if (String(м.icon || '').trim()) пункт.icon = String(м.icon).trim();
      if (вид === 'hotkey') пункт.keys = String(м.keys || '').trim();
      else пункт.url = String(м.url || '').trim();
      return пункт;
    }) : [];
  if (меню.length) б.menu = меню;
  if (п.bookmarks === 'firefox') б.bookmarks = 'firefox';
  return б;
}
function прогСвободныйId(основа) {
  const заняты = new Set(прогПриложения.map((п) => п.id));
  let n = 1;
  while (заняты.has(основа + '-' + n)) n++;
  return основа + '-' + n;
}
/* Ссылка и ручная программа добавляются прямо в черновик: поля новой строки
   сразу в панели свойств, а экран телефона обновляется предпросмотром. */
/* Слушаем сообщения только от нашего экрана телефона и только с нашего
   адреса: страниц-отправителей в пульте много, а доверять им список программ
   нельзя. */
function прогСлушатьЭкран(ctx) {
  const обработчик = (событие) => {
    if (текущий !== 'программы' || !ctx.корень.isConnected) return;
    if (событие.source !== ctx.экран.contentWindow) return;
    if (событие.origin !== location.origin) return;
    const msg = событие.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'truba-edit-ready') {
      ctx.готов = true;
      прогОтправитьЭкран(ctx);
      return;
    }
    if (msg.type === 'truba-edit-pick') {
      прогВыбрать(ctx, String(msg.id || ''));
      return;
    }
    if (msg.type === 'truba-edit-action') {
      /* Касание нижней кнопки в макете: показываем «Кнопка внизу N». */
      прогВыбратьКнопку(ctx, Number(msg.slot));
      return;
    }
    if (msg.type === 'truba-edit-add') {
      прогПоказатьДобавление(ctx, true);
      return;
    }
    if (msg.type === 'truba-edit-menu-add') {
      прогДобавитьПунктИзМакета(ctx, msg.app, msg.kind);
      return;
    }
    if (msg.type === 'truba-edit-menu-pick') {
      прогВыбратьПунктИзМакета(ctx, msg.app, msg.index);
      return;
    }
    if (msg.type === 'truba-edit-order') {
      прогПорядок(ctx, msg.ids);
    }
  };
  window.addEventListener('message', обработчик);
  ctx.отписка = () => window.removeEventListener('message', обработчик);
}
function прогВыбрать(ctx, id) {
  const куда = прогПриложения.findIndex((п) => п.id === id);
  if (куда < 0) return;
  ctx.индекс = куда;
  ctx.выбранный = id;
  /* Выбрана программа — панель кнопки вправе уйти: обе панели на одном
     месте, и оставить обе значило бы показать две разные вещи сразу. */
  ctx.слот = -1;
  прогПерерисовать(ctx);
  const строка = ctx.свойства.querySelector('.прог-строка');
  if (строка) строка.scrollIntoView({ block: 'nearest' });
}

/* Выбрана нижняя кнопка макета. Номер с макета — число, но проверить надо:
   `Number('мусор')` — это `NaN`, и `NaN` в `ctx.слот` ломал бы панель. */
function прогВыбратьКнопку(ctx, slot) {
  const номер = Number(slot);
  if (!Number.isInteger(номер) || номер < 0 || номер >= ПРОГ_КНОПКИ_УМОЛЧАНИЕ.length) {
    return;
  }
  ctx.слот = номер;
  ctx.выбранный = '';
  ctx.индекс = -1;
  прогПерерисовать(ctx);
  const панель = ctx.свойства.querySelector('.прог-низ-кнопка');
  if (панель) панель.scrollIntoView({ block: 'nearest' });
}
function прогПорядок(ctx, ids) {
  if (!Array.isArray(ids)) return;
  /* Порядок с экрана — это ids всех плиток. Переставляем по нему, а кого не
     назвали (только что добавленная) оставляем в конце: терять программу
     молча хуже, чем поставить её не туда. */
  const по = new Map(прогПриложения.map((п, i) => [п.id, i]));
  const новый = [];
  for (const id of ids) {
    if (по.has(String(id)) && !новый.includes(прогПриложения[по.get(String(id))])) {
      новый.push(прогПриложения[по.get(String(id))]);
    }
  }
  for (const п of прогПриложения) if (!новый.includes(п)) новый.push(п);
  if (новый.length !== прогПриложения.length) return;
  const было = ctx.выбранный;
  прогПриложения = новый;
  ctx.индекс = Math.max(0, новый.findIndex((п) => п.id === было));
  прогПерерисовать(ctx);
  прогОбновитьПревью(ctx);
  прогСказать(ctx.статус, 'Порядок изменён. Нажми «Сохранить», чтобы обновить телефон.', false);
}

/* Номер программы по имени. Сообщение может прийти откуда угодно, а список
   программ ему доверять нельзя: неизвестное имя — это не программа. */
function прогНомерПоId(id) {
  if (typeof id !== 'string' || !id) return -1;
  return прогПриложения.findIndex((п) => п.id === id);
}

/* Добавить пункт подменю выбранной программе: номер строки и вид. Одна
   функция на обе дороги — с макета телефона и с кнопок «+ Сайт» / «+ Сочетание
   клавиш», — иначе пути разошлись бы, и с макета пункт появлялся бы не с тем
   значком и не в том списке. */
function прогДобавитьПункт(ctx, номер, вид) {
  const тек = прогПриложения[номер];
  if (!тек) return;
  if (!Array.isArray(тек.menu)) тек.menu = [];
  const пункт = {
    kind: вид,
    id: прогСвободныйIdПункта(тек.menu, вид),
    title: '',
    icon: вид === 'site' ? 'browser' : 'keyboard',
  };
  if (вид === 'site') пункт.url = ''; else пункт.keys = '';
  тек.menu.push(пункт);
  /* Перерисовка открывает программу: её «Меню на телефоне» теперь не пустое,
     а значит, раздел раскрыт и новый пункт виден. Фокус — в его названии:
     хозяину остаётся только вписать текст. */
  прогВыбрать(ctx, тек.id);
  const поля = ctx.свойства.querySelectorAll('[data-меню-поле="title"]');
  const последнее = поля[поля.length - 1];
  if (последнее) последнее.focus();
  прогОбновитьПревью(ctx);
  прогСказать(ctx.статус, вид === 'site'
    ? 'Пункт-сайт добавлен: впиши название и адрес. Название можно русским — имя придумаем сами.'
    : 'Пункт-сочетание добавлен: впиши название и нажми клавиши прямо в поле.',
    false);
}

/* Плитка «+» в подменю на макете: тот же пункт, что и от кнопок в панели.
   Вид и программу проверяем — сообщение может прийти откуда угодно. */
function прогДобавитьПунктИзМакета(ctx, app, kind) {
  const вид = typeof kind === 'string' ? kind : '';
  if (вид !== 'site' && вид !== 'hotkey') return;
  if (прогНомерПоId(app) < 0) return;
  /* Правки в полях ещё не в модели: собираем их, иначе новый пункт встал бы
     в список поверх несохранённого. Номер берём ПОСЛЕ сбора — поле «имя
     кнопки» хозяин мог поменять, и программа с тем же `id` уже другая. */
  if (ctx.список) прогСобратьВсе(ctx.список);
  прогДобавитьПункт(ctx, прогНомерПоId(app), вид);
}

/* Пункт подменю выбран на макете: показать его название. Номер — целое и в
   пределах списка; иначе сообщение молча игнорируем, а не показываем чужой. */
function прогВыбратьПунктИзМакета(ctx, app, index) {
  if (прогНомерПоId(app) < 0) return;
  if (typeof index !== 'number' || !Number.isInteger(index) || index < 0) return;
  /* Правки в полях ещё не в модели: собираем их, иначе показывали бы не тот
     пункт, который виден на экране. Номер — после сбора, как и в добавлении. */
  if (ctx.список) прогСобратьВсе(ctx.список);
  const номер = прогНомерПоId(app);
  if (номер < 0) return;
  const тек = прогПриложения[номер];
  const сколько = Array.isArray(тек.menu) ? тек.menu.length : 0;
  if (index >= сколько) return;
  прогВыбрать(ctx, тек.id);
  const узлы = ctx.свойства.querySelectorAll('[data-меню-пункт]');
  const узел = узлы[index];
  if (!узел) return;
  узел.scrollIntoView({ block: 'nearest' });
  const поле = узел.querySelector('[data-меню-поле="title"]');
  if (поле) поле.focus();
}
/* Меню «+» с экрана телефона: три способа добавить, как и раньше наверху. */
/* ---------- «Из запущенных…» ----------
   Хозяин 28 сентября: чтобы добавить программу, не нужно искать файл на диске
   C — покажи, что уже запущено, и дай выбрать. Список приходит с компа
   (`/api/apps/running`): окна верхнего уровня, одно окно на программу.
   Уже добавленные показываем приглушённо и не нажимаются — иначе получится
   две одинаковые кнопки. */
function прогСпрятатьЗапущенные(ctx) {
  if (ctx.запущенные) ctx.запущенные.hidden = true;
}
function прогНарисоватьЗапущенные(ctx, список) {
  const панель = ctx.запущенные;
  if (!панель) return;
  панель.textContent = '';
  if (!список.length) {
    /* Ничего не запущено — это не поломка, а пустой компьютер. */
    панель.appendChild(прогТекстЗапущенных(
      'Не вижу открытых программ — запусти нужную и нажми «Обновить»'));
    const обновить = document.createElement('button');
    обновить.type = 'button';
    обновить.className = 'голос-кнопка';
    обновить.textContent = 'Обновить';
    обновить.addEventListener('click', () => прогЗагрузитьЗапущенные(ctx));
    панель.appendChild(обновить);
    return;
  }
  for (const строка of список) {
    панель.appendChild(прогКарточкаЗапущенного(ctx, строка));
  }
}
function прогТекстЗапущенных(текст) {
  const узел = document.createElement('div');
  узел.className = 'прог-запущенные-пусто';
  узел.textContent = текст;
  return узел;
}
function прогКарточкаЗапущенного(ctx, строка) {
  const карточка = document.createElement('button');
  карточка.type = 'button';
  карточка.className = 'прог-запущенная';
  /* Уже добавленную нельзя нажать: вторая кнопка на ту же программу хозяину
     не нужна, а список без пометки молчал бы, что это ошибка. */
  if (строка.already) {
    карточка.classList.add('уже');
    карточка.disabled = true;
  }
  const значок = document.createElement('span');
  значок.className = 'прог-запущенная-значок';
  if (строка.icon) {
    const картинка = document.createElement('img');
    картинка.src = строка.icon;
    картинка.alt = '';
    значок.appendChild(картинка);
  }
  карточка.appendChild(значок);
  const слова = document.createElement('span');
  слова.className = 'прог-запущенная-слова';
  const имя = document.createElement('span');
  имя.className = 'прог-запущенная-имя';
  имя.textContent = строка.title || строка.process || '';
  слова.appendChild(имя);
  const файл = document.createElement('span');
  файл.className = 'прог-запущенная-файл';
  файл.textContent = строка.already
    ? (строка.process || '') + ' — уже есть'
    : (строка.process || '');
  слова.appendChild(файл);
  карточка.appendChild(слова);
  карточка.addEventListener('click', () => {
    прогДобавитьСтроку(ctx, {
      id: прогСвободныйId('app'), title: строка.title || '',
      kind: 'app', path: строка.path, args: строка.args || [],
      how: строка.how || 'shell', process: строка.process || '',
      icon: 'app', icon_source: 'exe',
    }, 'title');
  });
  return карточка;
}
async function прогЗагрузитьЗапущенные(ctx) {
  const панель = ctx.запущенные;
  if (!панель) return;
  /* Блок добавления — общий: пока его не показали, карточек никто не увидит
     (обработчик пункта меню перед действием сворачивает блок). */
  прогПоказатьДобавление(ctx, true);
  панель.hidden = false;
  панель.textContent = '';
  панель.appendChild(прогТекстЗапущенных('Смотрю, что запущено…'));
  try {
    const ответ = await fetch('/api/apps/running', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные.ok) {
      throw new Error((данные && данные.error) || ('HTTP ' + ответ.status));
    }
    /* Вкладку могли закрыть, пока список шёл с компа. */
    if (!панель.isConnected || текущий !== 'программы') return;
    const список = Array.isArray(данные.apps) ? данные.apps : [];
    прогНарисоватьЗапущенные(ctx, список);
  } catch (e) {
    if (!панель.isConnected) return;
    панель.textContent = '';
    панель.appendChild(прогТекстЗапущенных(
      'Не получилось посмотреть запущенные: ' + (e && e.message ? e.message : e)));
  }
}
function прогПоказатьДобавление(ctx, видно) {
  ctx.добавление.hidden = !видно;
  /* Свернули меню — панель «Из запущенных» тоже: она занимала бы место
     впустую, а её состояние при следующем открытии всё равно перечитается. */
  if (!видно) прогСпрятатьЗапущенные(ctx);
  if (видно) {
    const первая = ctx.добавление.querySelector('button');
    if (первая) первая.focus();
  }
}
function прогДобавитьСтроку(ctx, данные, фокус) {
  const новая = прогНорма(данные, прогПриложения.length);
  прогПриложения.push(новая);
  ctx.индекс = прогПриложения.length - 1;
  ctx.выбранный = новая.id;
  прогПоказатьДобавление(ctx, false);
  прогПерерисовать(ctx);
  const ввод = ctx.свойства.querySelector('[data-поле="' + фокус + '"]');
  if (ввод) ввод.focus();
  прогОбновитьПревью(ctx);
  прогСказать(ctx.статус, 'Программа добавлена. Проверь поля и нажми «Сохранить».', false);
}
/* Строка «Сетка» — переключателями, как в соседних вкладках: меняют вид
   экрана сразу, а сохраняются общей кнопкой внизу. */
function прогНарисоватьСетку(ctx) {
  const ряд = document.createElement('div');
  ряд.className = 'прог-сетка-вид';
  const группы = [
    ['Ряды', [['1', '1'], ['2', '2']], 'rows'],
    ['Значки', [['plate', 'Плитка'], ['round', 'Круг'], ['bare', 'Без плитки']], 'style'],
  ];
  const кнопки = [];
  for (const [ярлык, варианты, ключ] of группы) {
    const группа = document.createElement('div');
    группа.className = 'прог-переключатели';
    const имя = document.createElement('span');
    имя.className = 'прог-переключатели-имя';
    имя.textContent = ярлык;
    группа.appendChild(имя);
    for (const [значение, подпись] of варианты) {
      const к = document.createElement('button');
      к.type = 'button';
      к.textContent = подпись;
      к.classList.toggle('активный', String(прогСеткаЧерновик[ключ]) === значение);
      к.addEventListener('click', () => {
        /* Вид значков — слово, ряды — число: сервер их так и проверяет. */
        прогСеткаЧерновик[ключ] = (ключ === 'style')
          ? значение : Number(значение);
        кнопки.forEach((пара) => пара[0].classList.toggle(
          'активный', пара[0] === к));
        прогОбновитьЭкран(ctx);
        прогПометитьЧерновик(ctx);
      });
      группа.appendChild(к);
      кнопки.push([к]);
    }
    ряд.appendChild(группа);
  }
  const галка = document.createElement('label');
  галка.className = 'прог-галочка';
  const вход = document.createElement('input');
  вход.type = 'checkbox';
  вход.checked = !!прогСеткаЧерновик.labels;
  вход.addEventListener('change', () => {
    прогСеткаЧерновик.labels = вход.checked;
    прогОбновитьЭкран(ctx);
    прогПометитьЧерновик(ctx);
  });
  const надпись = document.createElement('span');
  надпись.textContent = 'Подписи';
  галка.appendChild(вход);
  галка.appendChild(надпись);
  ряд.appendChild(галка);
  return ряд;
}
function нарисоватьПрограммы() {
  лист.classList.add('программный');
  const корень = document.createElement('div');
  корень.className = 'программы';
  const верх = document.createElement('div');
  верх.className = 'прог-верх';
  const счёт = document.createElement('div');
  счёт.className = 'прог-счёт';
  счёт.textContent = 'Загрузка…';
  верх.appendChild(счёт);
  корень.appendChild(верх);

  const ctx = {
    корень: корень, счёт: счёт, статус: null, проекция: null,
    список: null, свойства: null, экран: null, рамка: null, картинки: new Map(),
    проверено: null, готов: false, индекс: -1, выбранный: '', слот: -1,
    таймерЭкрана: null, таймерПревью: null, превьюЗапрос: 0,
  };
  корень.appendChild(прогНарисоватьСетку(ctx));

  const две = document.createElement('div');
  две.className = 'прог-две';
  /* Слева — экран телефона. Он же место, где хозяин жмёт «+»: плитка в конце
     сетки — та же, что на телефоне. */
  const левая = document.createElement('div');
  левая.className = 'прог-левая';
  const телефон = document.createElement('div');
  телефон.className = 'прог-телефон';
  const корпус = document.createElement('div');
  корпус.className = 'прог-телефон-корпус';
  const рамка = document.createElement('div');
  рамка.className = 'прог-телефон-рамка';
  const экран = document.createElement('iframe');
  экран.className = 'прог-телефон-экран';
  экран.src = '/?edit=1';
  экран.title = 'Экран телефона: нажми значок, чтобы изменить программу';
  экран.setAttribute('scrolling', 'no');
  рамка.appendChild(экран);
  корпус.appendChild(рамка);
  телефон.appendChild(корпус);
  левая.appendChild(телефон);
  ctx.экран = экран;
  ctx.рамка = рамка;
  /* Под экраном — размер настоящего телефона: без неё макет молча выдавал бы
     себя за телефон хозяина. */
  const подпись = document.createElement('div');
  подпись.className = 'прог-подпись-экрана';
  подпись.textContent = 'Размер телефона ещё не известен — открой Трубу на телефоне';
  левая.appendChild(подпись);
  ctx.подпись = подпись;
  const намёк = document.createElement('div');
  намёк.className = 'прог-намёк';
  намёк.textContent = 'Нажми значок — откроются его свойства. Нажми нижнюю кнопку — '
    + 'что она делает. Перетащи значок — поменяется порядок. Добавить — кнопка '
    + '«+ Программа» ниже.';
  левая.appendChild(намёк);
  const добавление = document.createElement('div');
  добавление.className = 'прог-добавление';
  добавление.hidden = true;
  ctx.добавление = добавление;
  /* Панель «Из запущенных…» живёт в том же блоке: хозяин просил именно его
     первым пунктом меню «+ Программа». */
  const запущенные = document.createElement('div');
  запущенные.className = 'прог-запущенные';
  запущенные.hidden = true;
  добавление.appendChild(запущенные);
  ctx.запущенные = запущенные;
  левая.appendChild(добавление);

  /* Справа — свойства выбранной программы. */
  const правая = document.createElement('div');
  правая.className = 'прог-правая';
  const шапка = document.createElement('div');
  шапка.className = 'прог-правая-шапка';
  шапка.textContent = 'Свойства программы';
  const свойства = document.createElement('div');
  свойства.className = 'прог-свойства';
  ctx.свойства = свойства;
  ctx.шапка = шапка;
  /* Ряд шапки: подпись и «← Все программы». Путь назад обязателен, поэтому он
     всегда на виду, а не появляется только после выбора. */
  const шапкаРяд = document.createElement('div');
  шапкаРяд.className = 'прог-правая-шапка-ряд';
  const назад = прогКнопкаНазад(ctx);
  шапкаРяд.appendChild(шапка);
  шапкаРяд.appendChild(назад);
  ctx.шапкаРяд = шапкаРяд;
  правая.appendChild(шапкаРяд);
  правая.appendChild(свойства);
  две.appendChild(левая);
  две.appendChild(правая);
  корень.appendChild(две);

  const низ = document.createElement('div');
  низ.className = 'прог-низ';
  const добавить = document.createElement('button');
  добавить.className = 'голос-кнопка';
  добавить.type = 'button';
  добавить.textContent = '+ Программа';
  добавить.title = 'Добавить: из запущенных, файл с компьютера, ссылка или вручную';
  const проверить = document.createElement('button');
  проверить.className = 'голос-кнопка';
  проверить.type = 'button';
  проверить.textContent = 'Проверить пути';
  const сохранить = document.createElement('button');
  сохранить.className = 'голос-кнопка главная';
  сохранить.type = 'button';
  сохранить.textContent = 'Сохранить';
  const пометка = document.createElement('span');
  пометка.className = 'прог-несохранено';
  пометка.textContent = 'Есть несохранённые изменения';
  пометка.hidden = true;
  const статус = document.createElement('div');
  статус.className = 'прог-статус';
  низ.appendChild(добавить);
  низ.appendChild(проверить);
  низ.appendChild(сохранить);
  низ.appendChild(пометка);
  низ.appendChild(статус);
  левая.appendChild(низ);
  лист.appendChild(корень);
  ctx.статус = статус;
  ctx.сохранить = сохранить;
  ctx.пометка = пометка;

  /* Меню добавления — то же, что и наверху раньше, только по кнопке «+». */
  const варианты = [
    /* Первым — «Из запущенных…»: хозяин попросил не искать файл на диске, а
       выбрать из того, что уже работает. */
    ['Из запущенных…', () => прогЗагрузитьЗапущенные(ctx)],
    ['Программа с компьютера…', async () => {
      const api = window.pywebview && window.pywebview.api;
      if (!api || !api.pick_program) {
        прогСказать(статус, 'Выбор файла работает в окне Трубы. Здесь нажми «Вручную» и впиши путь.', true);
        return;
      }
      добавить.disabled = true;
      try {
        const данные = await api.pick_program(прогПриложения.map((п) => п.id));
        if (текущий !== 'программы' || !корень.isConnected || данные.cancelled) return;
        if (!данные.ok || !данные.app) throw new Error(данные.error || 'файл не выбран');
        прогДобавитьСтроку(ctx, данные.app, 'title');
      } catch (e) {
        прогСказать(статус, 'Не получилось выбрать программу: ' + (e && e.message ? e.message : e), true);
      } finally { добавить.disabled = false; }
    }],
    ['Ссылка на сайт', () => прогДобавитьСтроку(ctx, {
      id: прогСвободныйId('link'), title: '', kind: 'url', url: '',
      icon: 'browser', icon_source: 'drawn',
    }, 'title')],
    /* Своя папка: «открой проект» голосом и плитка на телефоне. Значок —
       из оболочки, как у программы (core/app_icons.py умеет и папку). */
    ['Папка…', async () => {
      const api = window.pywebview && window.pywebview.api;
      let путь = '';
      if (api && api.pick_folder) {
        добавить.disabled = true;
        try {
          const ответ = await api.pick_folder('');
          if (текущий !== 'программы' || !корень.isConnected || ответ.cancelled) return;
          if (!ответ.ok) throw new Error(ответ.error || 'папка не выбрана');
          путь = String(ответ.path || '').trim();
        } catch (e) {
          прогСказать(статус, 'Не получилось выбрать папку: ' + (e && e.message ? e.message : e), true);
          return;
        } finally { добавить.disabled = false; }
      } else {
        /* В обычном браузере проводника нет — путь строкой. */
        путь = String(window.prompt('Полный путь к папке', '') || '').trim();
      }
      if (!путь) return;
      const имя = путь.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || путь;
      прогДобавитьСтроку(ctx, {
        id: прогСвободныйId('folder'), title: имя.slice(0, 60), kind: 'folder', path: путь,
        icon: 'app', icon_source: 'exe',
      }, 'title');
    }],
    ['Вручную', () => прогДобавитьСтроку(ctx, {
      id: прогСвободныйId('app'), title: '', kind: 'app', path: '', args: [],
      how: 'shell', icon: 'app', icon_source: 'exe',
    }, 'title')],
  ];
  for (const [подпись, действие] of варианты) {
    const к = document.createElement('button');
    к.type = 'button';
    к.className = 'голос-кнопка';
    к.textContent = подпись;
    к.addEventListener('click', () => { прогПоказатьДобавление(ctx, false); действие(); });
    добавление.appendChild(к);
  }

  добавить.addEventListener('click', () => прогПоказатьДобавление(ctx, ctx.добавление.hidden));
  сохранить.addEventListener('click', () => прогСохранить(ctx, сохранить));
  проверить.addEventListener('click', () => прогПроверитьПути(ctx, проверить));
  window.addEventListener('resize', () => прогМасштабЭкрана(ctx));
  прогСлушатьЭкран(ctx);
  (async function загрузить() {
    прогЗагрузка = true;
    счёт.textContent = 'Загрузка…';
    прогСказать(статус, 'Загружаю список…', false);
    try {
      const ответ = await fetch('/api/apps', { cache: 'no-store' });
      const данные = await ответ.json();
      if (!ответ.ok || !данные.ok) throw new Error((данные && данные.error) || ('HTTP ' + ответ.status));
      /* Размер экрана телефона едет тем же ответом: макет рисуем того размера,
         какой у настоящего телефона, иначе значки в макете врут. */
      прогПрименитьРазмерЭкрана(ctx, прогВзятьРазмерЭкрана(данные));
      const сырые = Array.isArray(данные.apps) ? данные.apps : [];
      прогПриложения = сырые.map((п, i) => прогНорма(п, i));
      прогСохранённое = new Map(прогПриложения.map((п) => [п.id, JSON.stringify(прогГотовое(п))]));
      прогСохранённоеСписок = JSON.stringify(прогПриложения.map(прогГотовое));
      await прогЗагрузитьСетку();
      прогСказать(статус, прогПриложения.length ? '' : 'Список пуст — добавь первую программу.', false);
    } catch (e) {
      прогПриложения = [];
      прогСказать(статус, 'Не загрузилось: ' + (e && e.message ? e.message : e), true);
    } finally {
      прогЗагрузка = false;
      if (текущий !== 'программы' || !корень.isConnected) return;
      if (ctx.индекс >= прогПриложения.length) ctx.индекс = прогПриложения.length - 1;
      ctx.выбранный = ctx.индекс >= 0 ? прогПриложения[ctx.индекс].id : '';
      прогПерерисовать(ctx);
      прогОбновитьПревью(ctx);
      прогМасштабЭкрана(ctx);
    }
  })();
}
/* Вид сетки — четыре ключа из `/api/settings`. Плохой ответ не должен ломать
   вкладку: тогда рисуем то, что сохранено у нас. */
async function прогЗагрузитьСетку() {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    const s = данные.settings || {};
    /* Колонок нет: их всегда четыре, и в пульте переключателя для них больше
       нет. Старое `phone_cols` в живом settings.json мы перестали читать. */
    /* Рядов один или два: старое «3» из живого settings.json показываем и
       сохраняем как два — иначе пульт предлагал бы то, чего уже нет. */
    прогСетка.rows = прогНормаРядов(s.phone_rows);
    if (['plate', 'round', 'bare'].includes(s.phone_icon_style)) {
      прогСетка.style = s.phone_icon_style;
    }
    прогСетка.labels = s.phone_labels === true;
    прогСеткаЧерновик = { ...прогСетка };
    прогСеткаСохранённая = JSON.stringify(прогСетка);
    /* Нижние кнопки — тем же ответом. Проверяем их здесь же, а не оставляем
       как пришли: сервер всё равно заменил бы кривую ячейку на кнопку по
       умолчанию, и пульт показывал бы не то, что окажется на экране. */
    const кнопки = Array.isArray(s.phone_actions) ? s.phone_actions : [];
    прогКнопки = ПРОГ_КНОПКИ_УМОЛЧАНИЕ.map((_, номер) => прогНормаКнопки(кнопки[номер], номер));
    прогКнопкиЧерновик = прогКнопки.map((ячейка) => ({ ...ячейка }));
    прогКнопкиСохранённые = JSON.stringify(прогКнопки);
  } catch (e) { /* Вид сетки подтянется при следующем сохранении. */ }
}

/* ---------- Заметки: две колонки, темы слева, записи справа ---------- */

/* Всё, что пришло с диска, рисуется узлами и textContent. Разбор текста
   заметки лежит в notes_markdown.js — там же, где он проверяется в node.
   Ни одной строки заметки в innerHTML не попадает: папку хозяин правит
   руками, а пульт виден всей сети. */
let заметкиЭлементы = null;
let заметкиДанные = null;
let заметкиВыбранная = null;   // {раздел, тема} — переживает перечитывание
let заметкиТема = null;        // ответ /api/notes/topic
let заметкиПравка = null;      // открытая форма: {вид, номер} — см. заметкиОткрытьПравку

function заметкиСказать(текст, плохо) {
  const эл = заметкиЭлементы;
  if (!эл || !эл.статус) return;
  эл.статус.textContent = текст || '';
  эл.статус.classList.toggle('плохо', !!плохо);
}

/* В шапке файла дата лежит как `2026-09-27`; на экране привычнее наша. */
function заметкиДата(значение) {
  const куски = String(значение || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return куски ? куски[3] + '.' + куски[2] + '.' + куски[1] : '';
}

/* Русские окончания: 1 запись, 2 записи, 5 записей. */
function заметкиСлово(число, одна, несколько, много) {
  const хвост = Math.abs(число) % 100;
  if (хвост >= 11 && хвост <= 14) return много;
  const один = число % 10;
  if (один === 1) return одна;
  if (один >= 2 && один <= 4) return несколько;
  return много;
}

function заметкиКнопка(текст, главная) {
  const кн = document.createElement('button');
  кн.type = 'button';
  кн.className = 'голос-кнопка' + (главная ? ' главная' : '');
  кн.textContent = текст;
  return кн;
}

async function заметкиПост(адрес, тело) {
  const ответ = await fetch(адрес, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(тело || {}),
  });
  let данные = null;
  try { данные = await ответ.json(); } catch (e) { данные = null; }
  if (!ответ.ok || !данные || данные.ok === false) {
    // Сервер приписывает имя исключения («ValueError: …») — человеку оно
    // ни о чём не говорит, показываем только суть.
    const почему = (данные && данные.error) || ('сервер ответил ' + ответ.status);
    throw new Error(String(почему).replace(/^[A-Za-z]*(Error|Exception): /, ''));
  }
  return данные;
}


/* --- Текст записи: узлы, а не разметка ---------------------------------- */

function заметкиКуски(куда, куски) {
  for (const кусок of куски || []) {
    if (!кусок || !кусок.текст) continue;
    if (кусок.жирный || кусок.курсив) {
      const узел = document.createElement(кусок.жирный ? 'b' : 'i');
      узел.textContent = кусок.текст;
      куда.appendChild(узел);
    } else {
      куда.appendChild(document.createTextNode(кусок.текст));
    }
  }
}

function заметкиБлок(родитель, блок) {
  if (блок.вид === 'заголовок') {
    const з = document.createElement(блок.уровень >= 4 ? 'h5' : 'h4');
    з.className = 'зам-подзаголовок';
    заметкиКуски(з, блок.куски);
    родитель.appendChild(з);
    return;
  }
  if (блок.вид === 'список') {
    const список = document.createElement(блок.нумерованный ? 'ol' : 'ul');
    список.className = 'зам-пункты';
    for (const пункт of блок.пункты) {
      const строка = document.createElement('li');
      заметкиКуски(строка, пункт);
      список.appendChild(строка);
    }
    родитель.appendChild(список);
    return;
  }
  if (блок.вид === 'цитата') {
    // Свёрнутый блок: модель могла вписать цитату прямо в текст заметки.
    // Название callout — его первая строка, в текст мысли она не входит.
    const свёрнуто = document.createElement('details');
    свёрнуто.className = 'зам-сырое';
    const шапка = document.createElement('summary');
    шапка.textContent = блок.заголовок || 'Цитата';
    свёрнуто.appendChild(шапка);
    const свёрнутое = document.createElement('div');
    свёрнутое.className = 'зам-сырое-тело';
    заметкиКуски(свёрнутое, блок.куски);
    свёрнуто.appendChild(свёрнутое);
    родитель.appendChild(свёрнуто);
    return;
  }
  const абзац = document.createElement('p');
  заметкиКуски(абзац, блок.куски);
  родитель.appendChild(абзац);
}


/* --- Слева: разделы, темы, поиск ----------------------------------------- */

function заметкиПерерисоватьСписок() {
  const эл = заметкиЭлементы;
  if (!эл || !эл.разделы) return;
  эл.разделы.innerHTML = '';
  const данные = заметкиДанные || {};
  const разделы = Array.isArray(данные.sections) ? данные.sections : [];
  const искомое = (эл.поиск.value || '').trim().toLowerCase();
  let найдено = 0;
  for (const раздел of разделы) {
    const темы = (раздел.topics || []).filter((т) =>
      !искомое || String(т.name || '').toLowerCase().includes(искомое));
    if (!темы.length) continue;
    найдено += темы.length;
    const блок = document.createElement('div');
    блок.className = 'зам-раздел';
    const имя = document.createElement('div');
    имя.className = 'зам-раздел-имя';
    имя.textContent = раздел.name || '';
    блок.appendChild(имя);
    const перечень = document.createElement('div');
    перечень.className = 'зам-темы';
    for (const тема of темы) {
      const выбрана = заметкиВыбранная &&
        заметкиВыбранная.раздел === раздел.name &&
        заметкиВыбранная.тема === тема.name;
      const кн = document.createElement('button');
      кн.type = 'button';
      кн.className = 'зам-тема';
      кн.classList.toggle('активная', !!выбрана);
      const подпись = document.createElement('span');
      подпись.className = 'зам-тема-имя';
      подпись.textContent = тема.name || '';
      const счёт = document.createElement('span');
      счёт.className = 'зам-тема-под';
      const записано = Number(тема.entries) || 0;
      const слова = [записано + ' ' +
        заметкиСлово(записано, 'запись', 'записи', 'записей')];
      const когда = заметкиДата(тема.updated);
      if (когда) слова.push(когда);
      счёт.textContent = слова.join(' · ');
      кн.appendChild(подпись);
      кн.appendChild(счёт);
      кн.addEventListener('click', () => заметкиВыбрать(раздел.name, тема.name));
      перечень.appendChild(кн);
    }
    блок.appendChild(перечень);
    эл.разделы.appendChild(блок);
  }
  if (!найдено) {
    const пусто = document.createElement('div');
    пусто.className = 'зам-пусто';
    // Два разных случая, а не один: «заметок нет» и «такой темы нет» —
    // хозяину это разные вещи, и одно молчаливое «ничего не найдено» сбивает.
    пусто.textContent = искомое
      ? 'Такой темы нет. Ищется по названию, целиком.'
      : 'Заметок пока нет. Скажи «Труба, запиши мысль по книге …», надиктуй и скажи '
        + '«всё» — заметка появится здесь.';
    эл.разделы.appendChild(пусто);
  }
}


/* --- Справа: выбранная тема --------------------------------------------- */

function заметкиНарисоватьТему() {
  const эл = заметкиЭлементы;
  if (!эл || !эл.тело) return;
  эл.тело.innerHTML = '';
  const тема = заметкиТема;
  if (!тема) {
    const пусто = document.createElement('div');
    пусто.className = 'пусто';
    const заголовок = document.createElement('b');
    заголовок.textContent = 'Выбери тему слева';
    пусто.appendChild(заголовок);
    пусто.appendChild(document.createTextNode(
      'Записи идут новые сверху, а надиктованное как есть — свёрнуто внизу каждой.'));
    эл.тело.appendChild(пусто);
    return;
  }
  const выбранная = заметкиВыбранная || {};
  const шапка = document.createElement('div');
  шапка.className = 'зам-шапка';
  const имена = document.createElement('div');
  имена.className = 'зам-имена';
  const заголовок = document.createElement('h2');
  заголовок.className = 'зам-заголовок';
  заголовок.textContent = тема.topic || выбранная.тема || '';
  const раздел = document.createElement('div');
  раздел.className = 'зам-раздел-под';
  раздел.textContent = 'Раздел: ' + (тема.section || выбранная.раздел || '');
  имена.appendChild(заголовок);
  имена.appendChild(раздел);
  шапка.appendChild(имена);

  const действия = document.createElement('div');
  действия.className = 'зам-действия';
  const новая = заметкиКнопка('Новая запись', true);
  новая.title = 'Дописать запись в эту тему руками';
  новая.addEventListener('click', () => заметкиОткрытьПравку({вид: 'новая'}));
  действия.appendChild(новая);
  // Кнопка в Obsidian есть только если папка заметок лежит внутри
  // хранилища: иначе Obsidian её всё равно не покажет.
  if (заметкиДанные && заметкиДанные.obsidian) {
    const вObsidian = заметкиКнопка('Открыть в Obsidian', true);
    вObsidian.title = 'Откроет эту тему в Obsidian: ' + заметкиДанные.obsidian;
    вObsidian.addEventListener('click', () => заметкиОткрыть(выбранная, true));
    действия.appendChild(вObsidian);
  } else {
    const открыть = заметкиКнопка('Открыть файл');
    открыть.title = 'Откроет файл темы программой по умолчанию';
    открыть.addEventListener('click', () => заметкиОткрыть(выбранная, false));
    действия.appendChild(открыть);
  }
  const переименовать = заметкиКнопка('Переименовать');
  переименовать.title = 'Поменять название темы или переложить её в другой раздел';
  переименовать.addEventListener('click', () => заметкиОткрытьПравку({вид: 'тема'}));
  действия.appendChild(переименовать);
  const удалить = заметкиКнопка('Удалить тему');
  удалить.classList.add('опасная');
  удалить.addEventListener('click', () => заметкиУдалитьТему(тема));
  действия.appendChild(удалить);
  шапка.appendChild(действия);
  эл.тело.appendChild(шапка);

  // Открытая форма — под шапкой, над лентой: правка и новая запись идут на всю
  // ширину колонки, а не внутри карточки.
  if (заметкиПравка && заметкиПравка.вид === 'тема') {
    эл.тело.appendChild(заметкиФормаТемы(тема));
  } else if (заметкиПравка && заметкиПравка.вид === 'новая') {
    эл.тело.appendChild(заметкиФормаНовой());
  }

  const лента = document.createElement('div');
  лента.className = 'зам-лента';
  // В файле записи идут по датам, новые в конец; на экране — новые сверху.
  // Номер из файла сохраняем: по нему удаление ищет запись.
  const записи = (тема.entries || []).map((запись, номер) =>
    Object.assign({}, запись, {номер}));
  записи.reverse();
  for (const запись of записи) лента.appendChild(заметкиЗапись(тема, запись));
  if (!записи.length) {
    const пусто = document.createElement('div');
    пусто.className = 'пусто';
    пусто.textContent = 'В этой теме пока нет записей.';
    лента.appendChild(пусто);
  }
  эл.тело.appendChild(лента);
}

function заметкиЗапись(тема, запись) {
  const карточка = document.createElement('article');
  карточка.className = 'зам-запись';
  const шапка = document.createElement('div');
  шапка.className = 'зам-запись-шапка';
  const левая = document.createElement('div');
  левая.className = 'зам-запись-левая';
  const когда = document.createElement('div');
  когда.className = 'зам-время';
  когда.textContent = запись.when || 'без даты';
  const имя = document.createElement('div');
  имя.className = 'зам-запись-имя';
  имя.textContent = запись.title || '';
  левая.appendChild(когда);
  левая.appendChild(имя);
  шапка.appendChild(левая);
  const изменить = document.createElement('button');
  изменить.type = 'button';
  изменить.className = 'зам-изменить';
  изменить.textContent = 'Изменить';
  изменить.addEventListener('click', () =>
    заметкиОткрытьПравку({вид: 'запись', номер: запись.номер}));
  шапка.appendChild(изменить);
  const убрать = document.createElement('button');
  убрать.type = 'button';
  убрать.className = 'зам-убрать';
  убрать.textContent = 'Удалить';
  убрать.addEventListener('click', () => заметкиУдалитьЗапись(тема, запись));
  шапка.appendChild(убрать);
  карточка.appendChild(шапка);

  const тело = document.createElement('div');
  тело.className = 'зам-тело';
  // Правка открыта на этой записи — вместо разобранного текста форма с полями.
  if (заметкиПравка && заметкиПравка.вид === 'запись'
      && заметкиПравка.номер === запись.номер) {
    тело.appendChild(заметкиФормаЗаписи(запись));
  } else {
    for (const блок of разобратьМаркдаун(запись.text)) заметкиБлок(тело, блок);
  }
  карточка.appendChild(тело);

  if (String(запись.raw || '').trim()) {
    // Надиктованное как есть. Причёсанный текст сверху, сырое — под ним.
    const свёрнуто = document.createElement('details');
    свёрнуто.className = 'зам-сырое';
    const шапкаСырого = document.createElement('summary');
    шапкаСырого.textContent = 'Как было сказано';
    свёрнуто.appendChild(шапкаСырого);
    const телоСырого = document.createElement('div');
    телоСырого.className = 'зам-сырое-тело';
    // Сырое — построчно и как есть: тут важно видеть, как она расслышала.
    for (const строка of String(запись.raw).split(/\r?\n/)) {
      const абзац = document.createElement('p');
      абзац.textContent = строка;
      if (строка.trim()) телоСырого.appendChild(абзац);
    }
    свёрнуто.appendChild(телоСырого);
    карточка.appendChild(свёрнуто);
  }
  return карточка;
}


/* --- Формы: правка записи, переименование темы, новая запись --------------- */

/* Одна форма за раз: открыли новую — прежняя закрылась перерисовкой темы. */
function заметкиОткрытьПравку(правка) {
  заметкиПравка = правка;
  заметкиНарисоватьТему();
}

function заметкиЗакрытьПравку() {
  заметкиПравка = null;
  заметкиНарисоватьТему();
}

/* Значения полей формы — только в `value`: текст заметки может содержать
   `<`, и в разметку он попадать не должен. */
function заметкиТекстПоле(значение) {
  const поле = document.createElement('textarea');
  поле.className = 'настр-текст';
  поле.value = значение === null || значение === undefined ? '' : String(значение);
  return поле;
}

function заметкиФормаРяд(кнопки) {
  const ряд = document.createElement('div');
  ряд.className = 'зам-форма-кнопки';
  for (const кнопка of кнопки) ряд.appendChild(кнопка);
  return ряд;
}

/* Esc — отмена в любом поле. Ctrl+Enter — главное действие, а в однострочных
   полях (переименование) и просто Enter: там он иначе ничего не делает. */
function заметкиФормаКлавиши(поля, главная) {
  for (const поле of поля) {
    поле.addEventListener('keydown', (событие) => {
      if (событие.key === 'Escape') {
        событие.preventDefault();
        заметкиЗакрытьПравку();
        return;
      }
      if (событие.key === 'Enter'
          && (событие.ctrlKey || поле.tagName !== 'TEXTAREA')) {
        событие.preventDefault();
        главная();
      }
    });
  }
}

/* Пока идёт запрос, форма гасит кнопки: второе «Сохранить» только сбило бы
   правку с толку. */
async function заметкиФормаЖдать(кнопки, действие) {
  for (const кнопка of кнопки) кнопка.disabled = true;
  try {
    await действие();
  } finally {
    for (const кнопка of кнопки) кнопка.disabled = false;
  }
}

function заметкиФормаЗаписи(запись) {
  const форма = document.createElement('div');
  форма.className = 'зам-форма';
  const заголовок = настрВвод(запись.title);
  заголовок.placeholder = 'Заголовок';
  // Текст заметки — Markdown, и в поле он как есть: правка идёт по нему.
  const текст = заметкиТекстПоле(запись.text);
  const сохранить = заметкиКнопка('Сохранить', true);
  const отмена = заметкиКнопка('Отмена');
  const кнопки = [сохранить, отмена];
  форма.appendChild(заголовок);
  форма.appendChild(текст);
  форма.appendChild(заметкиФормаРяд(кнопки));
  const сохранитьЗапись = () => заметкиСохранитьЗапись(запись, кнопки, заголовок, текст);
  сохранить.addEventListener('click', сохранитьЗапись);
  отмена.addEventListener('click', заметкиЗакрытьПравку);
  заметкиФормаКлавиши([заголовок, текст], сохранитьЗапись);
  // Форма ещё не на странице — фокус ставим, когда её вставят.
  setTimeout(() => текст.focus(), 0);
  return форма;
}


/* --- Действия ------------------------------------------------------------ */

function заметкиТемаЕсть(выбор) {
  const разделы = (заметкиДанные && заметкиДанные.sections) || [];
  return разделы.some((раздел) => раздел.name === выбор.раздел &&
    (раздел.topics || []).some((тема) => тема.name === выбор.тема));
}

function заметкиПоказатьПуть() {
  const эл = заметкиЭлементы;
  if (!эл || !эл.путь) return;
  const данные = заметкиДанные || {};
  эл.путь.textContent = данные.root || '';
  if (эл.подсказка) {
    // Подсказка про хранилище — только когда Obsidian не найден: иначе она
    // сбивала бы с толку, ведь заметки уже и так видны в нём.
    эл.подсказка.hidden = !!данные.obsidian;
  }
  // «По умолчанию» — только когда папка и правда своя: при папке по умолчанию
  // кнопка предложила бы вернуть то же самое.
  if (эл.поУмолчанию) эл.поУмолчанию.hidden = !данные.custom;
}

async function заметкиСменитьПапку() {
  const эл = заметкиЭлементы;
  if (!эл || эл.сменитьПапку.disabled) return;
  let путь = '';
  const api = window.pywebview && window.pywebview.api;
  if (api && api.pick_folder) {
    эл.сменитьПапку.disabled = true;
    try {
      const ответ = await api.pick_folder((заметкиДанные && заметкиДанные.root) || '');
      if (заметкиЭлементы !== эл) return;
      if (ответ.cancelled) return;
      if (!ответ.ok) {
        заметкиСказать(ответ.error || 'выбрать папку не вышло', true);
        return;
      }
      путь = String(ответ.path || '').trim();
    } catch (e) {
      if (заметкиЭлементы === эл) {
        заметкиСказать('Не выбрать папку: ' + (e && e.message ? e.message : e), true);
      }
      return;
    } finally {
      if (заметкиЭлементы === эл) эл.сменитьПапку.disabled = false;
    }
  } else {
    // В обычном браузере окна нет: спрашиваем путь строкой.
    путь = String(window.prompt('Полный путь к папке заметок',
      (заметкиДанные && заметкиДанные.root) || '') || '').trim();
  }
  if (!путь) return;
  await заметкиПоставитьПапку(эл, путь);
}

function заметкиПапкаПоУмолчанию() {
  const эл = заметкиЭлементы;
  if (!эл || эл.поУмолчанию.disabled) return;
  if (!window.confirm('Вернуть папку заметок по умолчанию — Документы\\Заметки Трубы? '
      + 'Заметки из нынешней папки туда не переедут.')) return;
  заметкиПоставитьПапку(эл, '');
}

/* Выключатель «Спрашивать, добавить ли разбор в заметки»: состояние — из
   `/api/settings`, смена уходит на сервер сразу, как папка заметок. Не
   ответил сервер — выключатель остаётся включённым, как по умолчанию. */
async function заметкиЗагрузитьРазбор(эл) {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok || заметкиЭлементы !== эл) return;
    эл.спроситьРазбор.checked = (данные.settings || {}).offer_analysis_note !== false;
  } catch (e) {
    /* Молчим: один неудачный вопрос не должен ломать страницу заметок. */
  }
}

async function заметкиСпрашиватьРазбор(эл) {
  if (заметкиЭлементы !== эл) return;
  const включено = !!эл.спроситьРазбор.checked;
  эл.спроситьРазбор.disabled = true;
  try {
    const ответ = await fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_analysis_note: включено }) });
    const данные = await ответ.json().catch(() => null);
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && (данные.error
        || (данные.errors || []).join('; '))) || ('сервер ответил ' + ответ.status));
    }
    if (заметкиЭлементы === эл) {
      заметкиСказать(включено
        ? 'После разбора спрошу, добавить ли его в заметки'
        : 'Про разбор в заметки больше не спрашиваю');
    }
  } catch (e) {
    if (заметкиЭлементы === эл) {
      // Не сохранилось — выключатель возвращается туда, где он на сервере.
      эл.спроситьРазбор.checked = !включено;
      заметкиСказать('Не сохранила: ' + (e && e.message ? e.message : e), true);
    }
  } finally {
    if (заметкиЭлементы === эл) эл.спроситьРазбор.disabled = false;
  }
}

async function заметкиПоставитьПапку(эл, путь) {
  if (заметкиЭлементы !== эл) return;
  эл.сменитьПапку.disabled = true;
  эл.поУмолчанию.disabled = true;
  try {
    // Своим запросом, а не `заметкиПост`: тот берёт только `error`, а сервер
    // про папку отвечает списком `errors` («нужен полный путь…») — и без
    // него хозяин увидел бы пустое «сервер ответил 400».
    const ответ = await fetch('/api/settings', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes_dir: путь }) });
    const данные = await ответ.json().catch(() => null);
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && (данные.error
        || (данные.errors || []).join('; '))) || ('сервер ответил ' + ответ.status));
    }
  } catch (e) {
    if (заметкиЭлементы === эл) {
      заметкиСказать('Не сменила папку: ' + (e && e.message ? e.message : e), true);
      эл.сменитьПапку.disabled = false;
      эл.поУмолчанию.disabled = false;
    }
    return;
  }
  if (заметкиЭлементы !== эл) return;
  await заметкиЗагрузить(true);
  if (заметкиЭлементы !== эл) return;
  эл.сменитьПапку.disabled = false;
  эл.поУмолчанию.disabled = false;
  заметкиСказать(путь
    ? 'Папка заметок: ' + путь + '. Старые заметки остались в прежней папке'
    : 'Папка заметок по умолчанию — Документы\\Заметки Трубы');
}

async function заметкиВыбрать(раздел, тема) {
  if (!заметкиЭлементы) return;
  // Форма открыта для прежней темы — в новой её не быть. Перечитывание той же
  // темы форму не трогает: иначе автообновление посреди правки съедало бы
  // несохранённый текст.
  const прежняя = заметкиВыбранная;
  if (!прежняя || прежняя.раздел !== раздел || прежняя.тема !== тема) {
    заметкиПравка = null;
  }
  заметкиВыбранная = { раздел: раздел, тема: тема };
  заметкиПерерисоватьСписок();
  заметкиНарисоватьТему();
  заметкиСказать('Открываю «' + тема + '»…');
  try {
    const ответ = await fetch('/api/notes/topic?section=' + encodeURIComponent(раздел) +
      '&topic=' + encodeURIComponent(тема), { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    // Пока ждали, хозяин мог щёлкнуть другую тему — её и показываем.
    if (!заметкиЭлементы || !заметкиВыбранная ||
        заметкиВыбранная.тема !== тема) return;
    заметкиТема = данные;
    заметкиНарисоватьТему();
    заметкиСказать('');
  } catch (e) {
    if (!заметкиЭлементы) return;
    заметкиТема = null;
    заметкиНарисоватьТему();
    заметкиСказать('Не прочитала: ' + (e && e.message ? e.message : e), true);
  }
}

async function заметкиЗагрузить(тихо) {
  if (!заметкиЭлементы) return;
  if (!тихо) заметкиСказать('Читаю папку…');
  try {
    const ответ = await fetch('/api/notes', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    if (!заметкиЭлементы) return;
    заметкиДанные = данные;
    // Выбранную тему держим, если она ещё есть: после «Обновить» или новой
    // записи голосом правая колонка не должна прыгать обратно в пустоту.
    if (заметкиВыбранная && !заметкиТемаЕсть(заметкиВыбранная)) {
      заметкиВыбранная = null;
      заметкиТема = null;
    }
    заметкиПоказатьПуть();
    заметкиПерерисоватьСписок();
    // Выбранную тему перечитываем всегда, а не только когда её нет в памяти:
    // после удаления записи или новой диктовки в эту же тему старая копия
    // показывала бы уже удалённое, и второе «Удалить» падало бы на
    // «файл изменился».
    if (заметкиВыбранная) {
      await заметкиВыбрать(заметкиВыбранная.раздел, заметкиВыбранная.тема);
    } else {
      заметкиНарисоватьТему();
    }
    if (!тихо) заметкиСказать('');
  } catch (e) {
    if (!заметкиЭлементы) return;
    заметкиСказать('Не прочитала папку: ' + (e && e.message ? e.message : e), true);
  }
}

async function заметкиОткрыть(выбор, obsidian) {
  if (!заметкиЭлементы) return;
  заметкиСказать(obsidian ? 'Открываю в Obsidian…' : 'Открываю…');
  try {
    await заметкиПост('/api/notes/open', {
      section: (выбор || {}).раздел || '',
      topic: (выбор || {}).тема || '',
      obsidian: !!obsidian,
    });
    заметкиСказать('');
  } catch (e) {
    if (!заметкиЭлементы) return;
    заметкиСказать('Не открыла: ' + (e && e.message ? e.message : e), true);
  }
}

async function заметкиУдалитьЗапись(тема, запись) {
  if (!заметкиЭлементы) return;
  const выбор = заметкиВыбранная || {};
  const имя = запись.title || запись.when || 'без названия';
  if (!window.confirm('Удалить запись «' + имя + '»? Она уйдёт в корзину заметок '
    + '(.trash), вернуть можно из папки.')) return;
  заметкиСказать('Удаляю…');
  try {
    await заметкиПост('/api/notes/delete', {
      section: выбор.раздел, topic: выбор.тема,
      index: запись.номер, heading: запись.heading,
    });
    // Тему перечитываем заново: запись могла оказаться последней, и тогда
    // номера у оставшихся поехали бы — а удаление сверяет их с заголовком.
    if (!заметкиЭлементы) return;
    if (!заметкиТемаЕсть(выбор)) {
      заметкиВыбранная = null;
      заметкиТема = null;
    }
    await заметкиЗагрузить(true);
    if (!заметкиЭлементы) return;
    заметкиСказать(заметкиВыбранная ? 'Запись в корзине, вернуть можно из папки'
      : 'Тема кончилась — ушла в корзину целиком');
  } catch (e) {
    if (!заметкиЭлементы) return;
    заметкиСказать('Не удалила: ' + (e && e.message ? e.message : e), true);
    // Файл он мог править руками: после отказа список надо перечитать.
    await заметкиЗагрузить(true);
  }
}

async function заметкиУдалитьТему(тема) {
  if (!заметкиЭлементы) return;
  const выбор = заметкиВыбранная || {};
  const сколько = (тема.entries || []).length;
  if (!window.confirm('Удалить тему «' + (тема.topic || выбор.тема) + '»? В ней '
    + сколько + ' ' + заметкиСлово(сколько, 'запись', 'записи', 'записей')
    + '. Всё уйдёт в корзину заметок (.trash), вернуть можно из папки.')) return;
  заметкиСказать('Удаляю тему…');
  try {
    await заметкиПост('/api/notes/delete', {
      section: выбор.раздел, topic: выбор.тема, all: true,
    });
    заметкиВыбранная = null;
    заметкиТема = null;
    if (!заметкиЭлементы) return;
    await заметкиЗагрузить(true);
    заметкиСказать('Тема в корзине, вернуть можно из папки');
  } catch (e) {
    if (!заметкиЭлементы) return;
    заметкиСказать('Не удалила: ' + (e && e.message ? e.message : e), true);
  }
}

async function заметкиСохранитьЗапись(запись, кнопки, заголовок, текст) {
  const выбор = заметкиВыбранная || {};
  if (!String(текст.value || '').trim()) {
    заметкиСказать('Текст пустой', true);
    return;
  }
  await заметкиФормаЖдать(кнопки, async () => {
    try {
      await заметкиПост('/api/notes/edit', {
        section: выбор.раздел, topic: выбор.тема,
        index: запись.номер, heading: запись.heading,
        title: String(заголовок.value || '').trim(), text: текст.value,
      });
    } catch (e) {
      if (!заметкиЭлементы) return;
      const почему = e && e.message ? e.message : e;
      заметкиСказать('Не сохранила: ' + почему, true);
      // Файл он мог править руками: тогда запись перечитываем, иначе второе
      // «Сохранить» упёрлось бы в тот же отказ, а правка молча пропала бы.
      if (String(почему).includes('файл изменился')) {
        заметкиПравка = null;
        await заметкиЗагрузить(true);
      }
      return;
    }
    if (!заметкиЭлементы) return;
    заметкиПравка = null;
    await заметкиЗагрузить(true);
    if (!заметкиЭлементы) return;
    заметкиСказать('Сохранила');
  });
}

/* Имя в списке берётся из шапки файла и может отличаться регистром от того,
   что прислал сервер, — ищем его без учёта регистра. */
function заметкиНайтиТему(искомое) {
  const разделы = (заметкиДанные && заметкиДанные.sections) || [];
  for (const раздел of разделы) {
    if (раздел.name !== искомое.раздел) continue;
    for (const тема of раздел.topics || []) {
      if (String(тема.name || '').toLowerCase()
          === String(искомое.тема || '').toLowerCase()) {
        return { раздел: раздел.name, тема: тема.name };
      }
    }
  }
  return null;
}

function заметкиФормаТемы(тема) {
  const форма = document.createElement('div');
  форма.className = 'зам-форма';
  const имя = настрВвод(тема.topic);
  имя.placeholder = 'Название темы';
  const раздел = настрВвод(тема.section);
  раздел.placeholder = 'Раздел';
  // Подсказки — существующие разделы; новый тоже можно, поле не выпадающее.
  const подсказки = document.createElement('datalist');
  подсказки.id = 'зам-разделы';
  for (const строка of (заметкиДанные && заметкиДанные.sections) || []) {
    const вариант = document.createElement('option');
    вариант.value = строка.name || '';
    подсказки.appendChild(вариант);
  }
  раздел.setAttribute('list', подсказки.id);
  const сохранить = заметкиКнопка('Сохранить', true);
  const отмена = заметкиКнопка('Отмена');
  const кнопки = [сохранить, отмена];
  форма.appendChild(имя);
  форма.appendChild(раздел);
  форма.appendChild(подсказки);
  форма.appendChild(заметкиФормаРяд(кнопки));
  const переименовать = () => заметкиПереименоватьТему(кнопки, имя, раздел);
  сохранить.addEventListener('click', переименовать);
  отмена.addEventListener('click', заметкиЗакрытьПравку);
  заметкиФормаКлавиши([имя, раздел], переименовать);
  setTimeout(() => имя.focus(), 0);
  return форма;
}

async function заметкиПереименоватьТему(кнопки, имя, раздел) {
  const выбор = заметкиВыбранная || {};
  const новое = String(имя.value || '').trim();
  if (!новое) {
    заметкиСказать('Название пустое', true);
    return;
  }
  await заметкиФормаЖдать(кнопки, async () => {
    let куда = null;
    try {
      куда = await заметкиПост('/api/notes/rename', {
        section: выбор.раздел, topic: выбор.тема,
        new_topic: новое, new_section: String(раздел.value || '').trim(),
      });
    } catch (e) {
      if (!заметкиЭлементы) return;
      заметкиСказать('Не переименовала: ' + (e && e.message ? e.message : e), true);
      return;
    }
    if (!заметкиЭлементы) return;
    // Сервер отдал имена так, как их потом увидит список, — им и верим.
    const искомое = { раздел: куда.section, тема: куда.topic };
    заметкиВыбранная = искомое;
    заметкиПравка = null;
    await заметкиЗагрузить(true);
    if (!заметкиЭлементы) return;
    if (!заметкиТемаЕсть(искомое)) {
      const нашли = заметкиНайтиТему(искомое);
      if (нашли) {
        await заметкиВыбрать(нашли.раздел, нашли.тема);
        if (!заметкиЭлементы) return;
      }
    }
    заметкиСказать('Переименовала');
  });
}

function заметкиФормаНовой() {
  const форма = document.createElement('div');
  форма.className = 'зам-форма';
  const заголовок = настрВвод('');
  заголовок.placeholder = 'Заголовок, можно пусто';
  const текст = заметкиТекстПоле('');
  const записать = заметкиКнопка('Записать', true);
  const отмена = заметкиКнопка('Отмена');
  const кнопки = [записать, отмена];
  форма.appendChild(заголовок);
  форма.appendChild(текст);
  форма.appendChild(заметкиФормаРяд(кнопки));
  const записатьНовую = () => заметкиЗаписатьНовую(кнопки, заголовок, текст);
  записать.addEventListener('click', записатьНовую);
  отмена.addEventListener('click', заметкиЗакрытьПравку);
  заметкиФормаКлавиши([заголовок, текст], записатьНовую);
  setTimeout(() => текст.focus(), 0);
  return форма;
}

async function заметкиЗаписатьНовую(кнопки, заголовок, текст) {
  const выбор = заметкиВыбранная || {};
  if (!String(текст.value || '').trim()) {
    заметкиСказать('Текст пустой', true);
    return;
  }
  await заметкиФормаЖдать(кнопки, async () => {
    try {
      await заметкиПост('/api/notes/add', {
        section: выбор.раздел, topic: выбор.тема,
        title: String(заголовок.value || '').trim(), text: текст.value,
      });
    } catch (e) {
      if (!заметкиЭлементы) return;
      заметкиСказать('Не записала: ' + (e && e.message ? e.message : e), true);
      return;
    }
    if (!заметкиЭлементы) return;
    заметкиПравка = null;
    await заметкиЗагрузить(true);
    if (!заметкиЭлементы) return;
    заметкиСказать('Записала');
  });
}


/* --- Страница ------------------------------------------------------------ */

function нарисоватьЗаметки() {
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.add('заметочный');
  const корень = document.createElement('div');
  корень.className = 'заметки';

  /* Слева: папка, путь, поиск, разделы с темами. */
  const левая = document.createElement('div');
  левая.className = 'зам-левая';
  const верх = document.createElement('div');
  верх.className = 'зам-верх';
  const кнопки = document.createElement('div');
  кнопки.className = 'зам-кнопки';
  const папка = заметкиКнопка('Открыть папку');
  папка.title = 'Откроет папку заметок в Проводнике';
  const обновить = заметкиКнопка('Обновить');
  обновить.title = 'Перечитать папку. Список и так обновляется сам, когда Труба запишет мысль';
  кнопки.appendChild(папка);
  кнопки.appendChild(обновить);
  верх.appendChild(кнопки);
  const путь = document.createElement('div');
  путь.className = 'зам-путь';
  путь.title = 'Папка, в которой лежат заметки';
  верх.appendChild(путь);
  // Папку меняют отсюда, а не из настроек: хозяин ищет её рядом со
  // списком заметок, а в «Системе» она была для него неочевидна.
  const папки = document.createElement('div');
  папки.className = 'зам-кнопки';
  const сменитьПапку = заметкиКнопка('Сменить папку');
  сменитьПапку.title = 'Выбрать папку, в которой Труба будет хранить заметки';
  const поУмолчанию = заметкиКнопка('По умолчанию');
  поУмолчанию.title = 'Вернуть папку «Документы\\Заметки Трубы». Старые заметки останутся в прежней папке';
  поУмолчанию.hidden = true;
  папки.appendChild(сменитьПапку);
  папки.appendChild(поУмолчанию);
  верх.appendChild(папки);
  // Спрашивать ли про разбор в заметки — здесь же, где папка: хозяин ищет
  // настройки заметок на этой странице, а не в «Настройках → Система».
  const переключатель = document.createElement('label');
  переключатель.className = 'зам-переключатель';
  переключатель.title = 'После разбора документа или скопированного текста '
    + 'Труба сама спросит, записать ли этот разбор в заметки';
  const спроситьРазбор = настрГалочка(true);
  const подписьПереключателя = document.createElement('span');
  подписьПереключателя.textContent = 'Спрашивать, добавить ли разбор в заметки';
  переключатель.appendChild(спроситьРазбор);
  переключатель.appendChild(подписьПереключателя);
  верх.appendChild(переключатель);
  левая.appendChild(верх);

  const поиск = document.createElement('input');
  поиск.type = 'search';
  поиск.className = 'зам-поиск';
  поиск.placeholder = 'Найти тему';
  поиск.title = 'Ищет тему по названию, без учёта регистра';
  левая.appendChild(поиск);

  const подсказка = document.createElement('div');
  подсказка.className = 'зам-подсказка';
  подсказка.textContent = 'Чтобы читать в Obsidian: «Открыть папку как хранилище» → эта папка.';
  левая.appendChild(подсказка);

  const разделы = document.createElement('div');
  разделы.className = 'зам-разделы';
  левая.appendChild(разделы);
  const статус = document.createElement('div');
  статус.className = 'зам-статус';
  левая.appendChild(статус);
  корень.appendChild(левая);

  /* Справа: выбранная тема. */
  const правая = document.createElement('div');
  правая.className = 'зам-правая';
  const тело = document.createElement('div');
  тело.className = 'зам-содержимое';
  правая.appendChild(тело);
  корень.appendChild(правая);
  лист.appendChild(корень);

  заметкиЭлементы = { корень: корень, разделы: разделы, тело: тело, путь: путь,
                      поиск: поиск, подсказка: подсказка, статус: статус,
                      сменитьПапку: сменитьПапку, поУмолчанию: поУмолчанию,
                      спроситьРазбор: спроситьРазбор };
  const элРазбор = заметкиЭлементы;
  спроситьРазбор.addEventListener('change', () => заметкиСпрашиватьРазбор(элРазбор));
  заметкиЗагрузитьРазбор(элРазбор);
  // Раздел могли открыть второй раз: без сброса справа остался бы прежний
  // ответ сервера, и тема выглядела бы выбранной до первого щелчка.
  заметкиТема = null;
  заметкиПравка = null;
  заметкиСказать('');

  поиск.addEventListener('input', заметкиПерерисоватьСписок);
  обновить.addEventListener('click', () => заметкиЗагрузить());
  папка.addEventListener('click', () => заметкиОткрыть(null, false));
  сменитьПапку.addEventListener('click', заметкиСменитьПапку);
  поУмолчанию.addEventListener('click', заметкиПапкаПоУмолчанию);

  заметкиПерерисоватьСписок();
  заметкиНарисоватьТему();
  заметкиЗагрузить();
}


/* ---------- Проверка: компактные замеры без карточек-в-карточках ---------- */

let провЭлементы = null;

function провОстановить() {
  const эл = провЭлементы;
  провЭлементы = null;
  if (!эл) return;
  эл.жива = false;
  if (эл.таймер) { clearInterval(эл.таймер); эл.таймер = null; }
  if (эл.опрос) { clearTimeout(эл.опрос); эл.опрос = null; }
}

function провЖива(эл) {
  return эл && эл.жива && провЭлементы === эл && текущий === 'проверка';
}

function провСекция(корень, заголовок, намёк, последняя) {
  const с = document.createElement('section');
  с.className = 'пров-секция' + (последняя ? ' последняя' : '');
  const з = document.createElement('h2');
  з.textContent = заголовок;
  с.appendChild(з);
  if (намёк) {
    const н = document.createElement('div');
    н.className = 'пров-намёк';
    н.textContent = намёк;
    с.appendChild(н);
  }
  корень.appendChild(с);
  return с;
}

function провРяд(секция) {
  const ряд = document.createElement('div');
  ряд.className = 'пров-ряд';
  секция.appendChild(ряд);
  return ряд;
}

function провКнопка(ряд, текст, главная) {
  const кн = document.createElement('button');
  кн.type = 'button';
  кн.className = 'голос-кнопка' + (главная ? ' главная' : '');
  кн.textContent = текст;
  ряд.appendChild(кн);
  return кн;
}

function провСтатус(ряд, текст) {
  const ст = document.createElement('span');
  ст.className = 'пров-статус';
  ст.textContent = текст;
  ряд.appendChild(ст);
  return ст;
}

async function провПост(адрес, тело) {
  const параметры = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
  if (тело !== undefined) параметры.body = JSON.stringify(тело);
  const ответ = await fetch(адрес, параметры);
  let данные = null;
  try {
    данные = await ответ.json();
  } catch (e) {
    данные = null;
  }
  if (!ответ.ok || !данные || данные.ok === false) {
    throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
  }
  return данные;
}

async function провДжоба(id) {
  const ответ = await fetch('/api/check/job/' + encodeURIComponent(id), { cache: 'no-store' });
  let данные = null;
  try {
    данные = await ответ.json();
  } catch (e) {
    данные = null;
  }
  if (!ответ.ok || !данные || !данные.ok) {
    throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
  }
  return данные.job;
}

function провЖдать(эл, id, шаг) {
  return new Promise((разрешить, отвергнуть) => {
    const тик = async () => {
      if (!провЖива(эл)) { отвергнуть(new Error('вкладка закрыта')); return; }
      let job = null;
      try {
        job = await провДжоба(id);
      } catch (e) {
        отвергнуть(e);
        return;
      }
      if (!провЖива(эл)) { отвергнуть(new Error('вкладка закрыта')); return; }
      const состояние = job && job.state;
      if (состояние === 'running' || состояние === 'processing') {
        if (шаг) {
          try {
            шаг(job);
          } catch (e) {}
        }
        эл.опрос = setTimeout(тик, 600);
        return;
      }
      разрешить(job);
    };
    эл.опрос = setTimeout(тик, 600);
  });
}

function провЧисло(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(3);
}

function провЦифра(куда, имя, значение) {
  const с = document.createElement('div');
  с.className = 'пров-цифра';
  const п = document.createElement('span');
  п.textContent = имя;
  с.appendChild(п);
  const з = document.createElement('b');
  з.textContent = значение;
  с.appendChild(з);
  куда.appendChild(с);
}

function провРисоватьТаблицу(эл, строки) {
  эл.таблица.innerHTML = '';
  if (!строки.length) {
    const п = document.createElement('div');
    п.className = 'пров-пусто';
    п.textContent = 'Пусто — сервер ничего не намерил.';
    эл.таблица.appendChild(п);
    return;
  }
  for (const r of строки) {
    const строка = document.createElement('div');
    строка.className = 'пров-строка' + (r.ok ? '' : ' плохая');
    const знак = document.createElement('span');
    знак.className = 'пров-знак';
    знак.textContent = r.ok ? '+' : '!';
    строка.appendChild(знак);
    const тело = document.createElement('div');
    тело.className = 'пров-тело';
    const верх = document.createElement('div');
    верх.className = 'пров-верх-строки';
    const имя = document.createElement('b');
    имя.textContent = r.name || 'замер';
    верх.appendChild(имя);
    const значение = document.createElement('span');
    значение.textContent = r.value || '';
    верх.appendChild(значение);
    тело.appendChild(верх);
    if (r.note) {
      const низ = document.createElement('div');
      низ.className = 'пров-примечание';
      низ.textContent = r.note;
      тело.appendChild(низ);
    }
    строка.appendChild(тело);
    эл.таблица.appendChild(строка);
  }
}

async function провЗапуститьВсё(эл) {
  if (эл.кнВсе.disabled) return;
  эл.кнВсе.disabled = true;
  эл.ст1.textContent = 'меряем…';
  эл.ст1.classList.remove('плохо');
  эл.таблица.innerHTML = '';
  const ход = document.createElement('div');
  ход.className = 'пров-пусто';
  ход.textContent = 'Идёт проверка — первый прогон дольше, поднимаются модели…';
  эл.таблица.appendChild(ход);
  try {
    const пуск = await провПост('/api/check/run_all');
    const job = await провЖдать(эл, пуск.id);
    if (!провЖива(эл)) return;
    if (!job || job.state === 'error') throw new Error((job && job.error) || 'проверка не прошла');
    провРисоватьТаблицу(эл, job.results || []);
    const плохо = (job.results || []).some((r) => !r.ok);
    эл.ст1.textContent = плохо ? 'есть замечания — смотри строки с «!»' : 'всё в порядке';
    эл.ст1.classList.toggle('плохо', плохо);
  } catch (e) {
    if (!провЖива(эл)) return;
    эл.ст1.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.ст1.classList.add('плохо');
  } finally {
    if (провЖива(эл)) эл.кнВсе.disabled = false;
  }
}

async function провЗамеритьМодель(эл) {
  if (эл.кнМодель.disabled) return;
  эл.кнМодель.disabled = true;
  эл.стМодель.classList.remove('плохо');
  эл.стМодель.textContent = 'жду ответ модели…';
  эл.итогМодель.hidden = true;
  эл.итогМодель.innerHTML = '';
  try {
    const пуск = await провПост('/api/check/cloud');
    const job = await провЖдать(эл, пуск.id);
    if (!провЖива(эл)) return;
    if (!job || job.state === 'error') throw new Error((job && job.error) || 'модель не ответила');
    const данные = job.result || {};
    эл.итогМодель.hidden = false;
    провЦифра(эл.итогМодель, 'модель', данные.model || данные.provider || '—');
    провЦифра(эл.итогМодель, 'до первого слова', данные.first + ' с');
    провЦифра(эл.итогМодель, 'ответ целиком', данные.total + ' с');
    эл.стМодель.textContent = 'связь работает';
  } catch (e) {
    if (!провЖива(эл)) return;
    эл.стМодель.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.стМодель.classList.add('плохо');
  } finally {
    if (провЖива(эл)) эл.кнМодель.disabled = false;
  }
}

async function провЗамеритьГолос(эл) {
  if (эл.кнГолос.disabled) return;
  эл.кнГолос.disabled = true;
  эл.рядФ.hidden = true;
  эл.голосИтог.hidden = true;
  эл.голосИтог.innerHTML = '';
  эл.стФ.textContent = '';
  эл.стФ.classList.remove('плохо');
  эл.suggested = null;
  эл.голосНачало = Date.now();
  эл.ст2.classList.remove('плохо');
  эл.ст2.textContent = 'говори… 4 с';
  if (эл.таймер) clearInterval(эл.таймер);
  эл.таймер = setInterval(() => {
    if (!провЖива(эл)) return;
    const прошло = Math.floor((Date.now() - эл.голосНачало) / 1000);
    const осталось = Math.max(0, 4 - прошло);
    if (осталось > 0) эл.ст2.textContent = 'говори… ' + осталось + ' с';
  }, 500);
  try {
    const пуск = await провПост('/api/check/voice_level');
    const job = await провЖдать(эл, пуск.id, () => {
      if (провЖива(эл)) эл.ст2.textContent = 'считаем…';
    });
    if (!провЖива(эл)) return;
    if (эл.таймер) { clearInterval(эл.таймер); эл.таймер = null; }
    if (!job || job.state === 'error') throw new Error((job && job.error) || 'замер не вышел');
    провПоказатьГолос(эл, job.result || {});
  } catch (e) {
    if (!провЖива(эл)) return;
    if (эл.таймер) { clearInterval(эл.таймер); эл.таймер = null; }
    if (e && e.message === 'вкладка закрыта') return;
    эл.ст2.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.ст2.classList.add('плохо');
    эл.кнГолос.disabled = false;
  }
}

function провПоказатьГолос(эл, данные) {
  эл.кнГолос.disabled = false;
  if (!данные.ok) {
    эл.ст2.textContent = 'Не вышло: ' + (данные.why || 'микрофон не найден');
    эл.ст2.classList.add('плохо');
    return;
  }
  эл.ст2.classList.remove('плохо');
  эл.голосИтог.hidden = false;
  эл.голосИтог.innerHTML = '';
  провЦифра(эл.голосИтог, 'громкие места речи', провЧисло(данные.loud));
  провЦифра(эл.голосИтог, 'обычная громкость', провЧисло(данные.typical));
  провЦифра(эл.голосИтог, 'самый громкий пик', провЧисло(данные.peak));
  провЦифра(эл.голосИтог, 'порог перебивания', провЧисло(данные.threshold));
  if (данные.can_interrupt) {
    эл.ст2.textContent = 'Перебить получится: речь громче порога.';
    return;
  }
  эл.ст2.textContent = 'Перебить не выйдет: речь тише порога.';
  эл.ст2.classList.add('плохо');
  const порог = Number(данные.suggested);
  if (Number.isFinite(порог)) {
    эл.suggested = порог;
    эл.рядФ.hidden = false;
    эл.кнФ.disabled = false;
    эл.кнФ.textContent = 'Поставить порог ' + порог;
    эл.стФ.textContent = 'подходящий порог: ' + порог;
  }
}

async function провПрименитьПорог(эл) {
  if (эл.suggested === null || эл.suggested === undefined) return;
  эл.кнФ.disabled = true;
  try {
    await провПост('/api/check/threshold', { value: эл.suggested });
    эл.стФ.textContent = 'Порог ' + эл.suggested + ' сохранён.';
  } catch (e) {
    эл.стФ.textContent = 'Не вышло: ' + (e && e.message ? e.message : e);
    эл.стФ.classList.add('плохо');
    эл.кнФ.disabled = false;
  }
}

function нарисоватьПроверку() {
  лист.innerHTML = '';
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.remove('настроечный');
  лист.classList.remove('программный');
  лист.classList.add('проверочный');
  провОстановить();
  const корень = document.createElement('div');
  корень.className = 'проверка';
  лист.appendChild(корень);
  // `опрос` — таймер ожидания джобы в `провЖдать`; `провОстановить` его гасит.
  const эл = { корень, таймер: null, опрос: null, жива: true, голосНачало: 0, suggested: null };
  провЭлементы = эл;

  const с1 = провСекция(корень, 'Общая проверка', 'Микрофон, распознавание, синтез и команды — без тебя. Первый прогон дольше: поднимаются модели.', false);
  const ряд1 = провРяд(с1);
  эл.кнВсе = провКнопка(ряд1, 'Проверить всё', true);
  эл.ст1 = провСтатус(ряд1, 'ещё не проверяли');
  эл.таблица = document.createElement('div');
  эл.таблица.className = 'пров-таблица';
  const пт = document.createElement('div');
  пт.className = 'пров-пусто';
  пт.textContent = 'Нажми «Проверить всё» — строки появятся здесь.';
  эл.таблица.appendChild(пт);
  с1.appendChild(эл.таблица);

  const сеть = провСекция(корень, 'Скорость модели', 'Один короткий облачный запрос после нажатия. Потратит немного лимита, историю разговора не изменит.', false);
  const рядСети = провРяд(сеть);
  эл.кнМодель = провКнопка(рядСети, 'Замерить ответ', false);
  эл.стМодель = провСтатус(рядСети, 'ещё не проверяли');
  эл.итогМодель = document.createElement('div');
  эл.итогМодель.className = 'пров-итог';
  эл.итогМодель.hidden = true;
  сеть.appendChild(эл.итогМодель);

  // Последняя секция: образца хозяина здесь больше нет, он в «Голос → Слух».
  const с2 = провСекция(корень, 'Замер голоса', 'Говори обычным голосом 4 секунды — проверим, пробьёшься ли сквозь её речь.', true);
  const ряд2 = провРяд(с2);
  эл.кнГолос = провКнопка(ряд2, 'Замерить громкость', true);
  эл.ст2 = провСтатус(ряд2, 'молчим');
  эл.голосИтог = document.createElement('div');
  эл.голосИтог.className = 'пров-итог';
  эл.голосИтог.hidden = true;
  с2.appendChild(эл.голосИтог);
  const рядФ = провРяд(с2);
  рядФ.hidden = true;
  эл.рядФ = рядФ;
  эл.кнФ = провКнопка(рядФ, 'Поставить порог', false);
  эл.стФ = провСтатус(рядФ, '');

  // Образца хозяина здесь больше нет: он записывается в «Голос → Слух»,
  // рядом с «Только хозяин» и «Строгостью узнавания», ради которых нужен.

  эл.кнВсе.addEventListener('click', () => провЗапуститьВсё(эл));
  эл.кнМодель.addEventListener('click', () => провЗамеритьМодель(эл));
  эл.кнГолос.addEventListener('click', () => провЗамеритьГолос(эл));
  эл.кнФ.addEventListener('click', () => провПрименитьПорог(эл));
}

/* ---------- Команды: что сказать и что будет ----------

   Страница не пишется руками: команды, окно разговора и список программ
   приходят из `/api/commands`, а сервер берёт их из той же таблицы, по
   которой Труба понимает речь. Переписать инструкцию вручную — значит
   через месяц врать хозяину. Всё ставится текстом: данных из ответа
   сервера в разметку не подставляем. */

/* Строка из кусочков: [['обычный текст', false], ['**жирный**', true]].
   В одной фразе живут и слова, и названия рамок — иначе второе пришлось бы
   выделять разметкой, а её тут быть не должно. */
function комЧасти(родитель, части) {
  const строка = document.createElement('p');
  строка.className = 'ком-пункт';
  for (const часть of части) {
    if (часть[1]) {
      const жирный = document.createElement('b');
      жирный.textContent = часть[0];
      строка.appendChild(жирный);
    } else {
      строка.appendChild(document.createTextNode(часть[0]));
    }
  }
  родитель.appendChild(строка);
  return строка;
}

function комСекция(корень, имя, пояснение) {
  const секция = document.createElement('section');
  секция.className = 'ком-секция';
  const заголовок = document.createElement('h2');
  заголовок.textContent = имя;
  секция.appendChild(заголовок);
  if (пояснение) {
    const намёк = document.createElement('div');
    намёк.className = 'ком-пояснение';
    намёк.textContent = пояснение;
    секция.appendChild(намёк);
  }
  корень.appendChild(секция);
  return секция;
}

/* Примеры — плашками: так их читают как то, что говорить вслух, а не как
   описание. Шрифт тот же, что у кнопок пульта. */
function комПлашка(текст) {
  const плашка = document.createElement('span');
  плашка.className = 'ком-плашка';
  плашка.textContent = '«' + текст + '»';
  return плашка;
}

/* Прозвища — мелким серым: как их называют вслух, а не как на кнопке. */
function комСписокПрограмм(карточка, программы) {
  const список = document.createElement('div');
  список.className = 'ком-программы';
  if (!программы.length) {
    const пусто = document.createElement('div');
    пусто.className = 'ком-программа';
    пусто.textContent = 'Список программ пуст — добавь их в «Программы».';
    список.appendChild(пусто);
    карточка.appendChild(список);
    return;
  }
  for (const программа of программы) {
    const строка = document.createElement('div');
    строка.className = 'ком-программа';
    const имя = document.createElement('b');
    имя.textContent = программа.title;
    строка.appendChild(имя);
    строка.appendChild(document.createTextNode(' — '));
    const прозвища = document.createElement('span');
    прозвища.className = 'ком-прозвища';
    прозвища.textContent = (программа.aliases || []).join(', ');
    строка.appendChild(прозвища);
    список.appendChild(строка);
  }
  карточка.appendChild(список);
}

function комКарточка(команда, программы) {
  const карточка = document.createElement('article');
  карточка.className = 'ком-карточка';
  const имя = document.createElement('h3');
  имя.textContent = команда.title;
  карточка.appendChild(имя);
  const что = document.createElement('p');
  что.className = 'ком-что';
  что.textContent = команда.does;
  карточка.appendChild(что);
  const примеры = document.createElement('div');
  примеры.className = 'ком-примеры';
  for (const пример of (команда.examples || [])) {
    примеры.appendChild(комПлашка(пример));
  }
  карточка.appendChild(примеры);
  if (команда.arg) {
    const арг = document.createElement('p');
    арг.className = 'ком-арг';
    арг.textContent = 'После команды скажи: ' + команда.arg;
    карточка.appendChild(арг);
  }
  // Список программ нужен только тем двум командам, где программа и есть
  // аргумент. Остальным он — лишний шум.
  if (команда.id === 'launch' || команда.id === 'close') {
    комСписокПрограмм(карточка, программы);
  }
  return карточка;
}

/* Карточка «понимает по смыслу». Отдельная функция, а не `комКарточка` с
   флагом: у навыка нет `arg` и никогда не будет списка программ, а лишнее
   условие внутри общей карточки со временем выросло бы в третью разновидность
   того же кода. Вёрстка — ровно та же, что у мгновенных команд. */
function комНавык(навык) {
  const карточка = document.createElement('article');
  карточка.className = 'ком-карточка';
  const имя = document.createElement('h3');
  имя.textContent = навык.title;
  карточка.appendChild(имя);
  const что = document.createElement('p');
  что.className = 'ком-что';
  что.textContent = навык.does;
  карточка.appendChild(что);
  const примеры = document.createElement('div');
  примеры.className = 'ком-примеры';
  for (const пример of (навык.examples || [])) {
    примеры.appendChild(комПлашка(пример));
  }
  карточка.appendChild(примеры);
  return карточка;
}

async function нарисоватьКоманды() {
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.remove('настроечный');
  лист.classList.remove('программный');
  лист.classList.remove('проверочный');
  лист.classList.add('командный');
  const корень = document.createElement('div');
  корень.className = 'команды';
  лист.appendChild(корень);
  const статус = document.createElement('div');
  статус.className = 'ком-статус';
  статус.textContent = 'Читаю команды…';
  корень.appendChild(статус);

  let данные = null;
  try {
    const ответ = await fetch('/api/commands', { cache: 'no-store' });
    данные = await ответ.json();
  } catch (e) {
    данные = null;
  }
  if (текущий !== 'команды') return;
  if (!данные || !данные.ok) {
    статус.textContent = 'Нет связи с сервером.';
    return;
  }
  статус.remove();

  const окно = typeof данные.window === 'number' && Number.isFinite(данные.window)
    ? данные.window : 0;
  const программы = Array.isArray(данные.apps) ? данные.apps : [];

  const позвать = комСекция(корень, 'Как позвать и понять, что услышала',
    'Рамка на телефоне говорит, что она сейчас делает.');
  комЧасти(позвать, [
    ['Скажи «Труба» — на телефоне загорится ', false],
    ['холодная', true],
    [' рамка: окно открыто, говори без имени. Окно — ', false],
    [окно + ' секунд тишины после её ответа', false],
  ]);
  комЧасти(позвать, [
    ['Тёплая', true],
    [' рамка — фраза дошла, она думает. ', false],
    ['Светлая', true],
    [' — говорит. ', false],
    ['Янтарная', true],
    [' — записывает заметку. Не загорелась — не дошло, повтори', false],
  ]);
  комЧасти(позвать, [
    ['Коснись круга — замолчит и закроет разговор. Удержи — слушает без имени', false],
  ]);
  комЧасти(позвать, [
    ['«Хватит», «отстань», «пока» — закрыть разговор', false],
  ]);

  const мгновенные = комСекция(корень, 'Мгновенные команды — без облака, сразу',
    'Короткая фраза из этого списка делается на месте, даже без интернета.');
  const сетка = document.createElement('div');
  сетка.className = 'ком-сетка';
  for (const команда of (данные.commands || [])) {
    сетка.appendChild(комКарточка(команда, программы));
  }
  мгновенные.appendChild(сетка);

  // Второй блок — то, что модель делает инструментами сама. Тот же класс
  // `ком-сетка` и те же карточки: разница только в том, откуда пришли данные.
  const поСмыслу = комСекция(корень, 'Понимает по смыслу — длинными фразами',
    'Слова те же, а форма произвольная: «слушай, переключи-ка на английский». '
    + 'Такое уходит в облако и делается её инструментами.');
  const сеткаСмысла = document.createElement('div');
  сеткаСмысла.className = 'ком-сетка';
  for (const навык of (данные.skills || [])) {
    сеткаСмысла.appendChild(комНавык(навык));
  }
  поСмыслу.appendChild(сеткаСмысла);

  const остальное = комСекция(корень, 'Всё остальное — своими словами');
  const абзац = document.createElement('p');
  абзац.className = 'ком-текст';
  абзац.textContent = 'Длинные просьбы и вопросы понимает модель: запустить или '
    + 'закрыть программу, включить на YouTube, записать или прочитать заметку, '
    + 'снимок экрана, глянуть на экран, сохранить момент, найти в интернете. '
    + 'Делает только то, о чём ты просишь в этой фразе; то же самое второй раз '
    + 'за пару минут не повторяет — скажет «Уже сделала». Ролик заново — скажи '
    + '«открой ещё раз».';
  остальное.appendChild(абзац);
}

function показатьЗаглушку(раздел) {
  лист.classList.remove('компьютерный');
  лист.classList.remove('чатовый');
  лист.classList.remove('голосовой');
  лист.classList.remove('логовой');
  лист.classList.remove('настроечный');
  лист.classList.remove('программный');
  лист.classList.remove('заметочный');
  const пусто = document.createElement('div');
  пусто.className = 'пусто';
  const заголовок = document.createElement('b');
  заголовок.textContent = 'Ещё не переехало';
  пусто.appendChild(заголовок);
  пусто.appendChild(document.createTextNode(раздел.скоро));
  лист.appendChild(пусто);
}

function открыть(имя) {
  const раздел = РАЗДЕЛЫ[имя];
  if (!раздел) return;
  текущий = имя;
  if (имя !== 'логи' && логиТаймер !== null) {
    clearInterval(логиТаймер);
    логиТаймер = null;
    логиЭлементы = null;
  }
  // Таймер «через N мин» живёт только пока блок на Панели: ушли на другую
  // вкладку — снимаем, вернёмся — поставит снова `блокНапоминаний`.
  if (имя !== 'панель') напоминанияТаймерСнять();
  if (имя === 'панель') спроситьНапоминания(true);

  меню.querySelectorAll('.пункт').forEach((el) => {
    el.classList.toggle('активный', el.dataset.раздел === имя);
  });
  $('подменю-настроек').classList.toggle('открыто', имя === 'настройки');
  $('подменю-голоса').classList.toggle('открыто', имя === 'голос');
  менюПоказатьРаскрытое();

  /* Верхняя лента состояний (слух, голос, мозг, телефон, память) —
     только Панель. В остальных вкладках скрываем целиком,
     при возврате в Панель показываем снова. */
  карточки.hidden = имя !== 'панель';

  $('заголовок').textContent = раздел.имя;
  $('описание').textContent = раздел.описание;
  лист.innerHTML = '';
  лист.classList.remove('настроечный');
  лист.classList.remove('программный');
  лист.classList.remove('проверочный');
  лист.classList.remove('заметочный');
  лист.classList.remove('командный');
  if (имя !== 'проверка') провОстановить();
  if (имя === 'панель') {
    расходКогда = 0;  // вернулись на Панель — покажем свежий расход сразу
    нарисоватьПанель();
  }
  else if (имя === 'чат') нарисоватьЧат();
  else if (имя === 'заметки') нарисоватьЗаметки();
  else if (имя === 'голос') нарисоватьГолос();
  else if (имя === 'логи') нарисоватьЛоги();
  else if (имя === 'настройки') нарисоватьНастройки();
  else if (имя === 'программы') нарисоватьПрограммы();
  else if (имя === 'команды') нарисоватьКоманды();
  else if (имя === 'проверка') нарисоватьПроверку();
  else if (имя === 'программа') нарисоватьОПрограмме();
  else if (имя === 'поддержка') нарисоватьПоддержку();
  else показатьЗаглушку(раздел);
  $('поддержать').classList.toggle('активный', имя === 'поддержка');
}

меню.querySelectorAll('.пункт').forEach((el) => {
  el.addEventListener('click', () => {
    if (текущий === el.dataset.раздел) {
      if (текущий === 'настройки') $('подменю-настроек').classList.toggle('открыто');
      if (текущий === 'голос') $('подменю-голоса').classList.toggle('открыто');
      менюПоказатьРаскрытое();
      return;
    }
    открыть(el.dataset.раздел);
  });
});
document.querySelectorAll('#подменю-настроек [data-настройка]').forEach((кнопка) => {
  кнопка.addEventListener('click', () => {
    настрТекущийРаздел = кнопка.dataset.настройка;
    if (текущий !== 'настройки') открыть('настройки');
    else настрПоказатьРаздел(настрТекущийРаздел);
  });
});

document.querySelectorAll('#подменю-голоса [data-голос]').forEach((кнопка) => {
  кнопка.addEventListener('click', () => открытьГолосПодраздел(кнопка.dataset.голос));
});

function открытьГолосПодраздел(код) {
  голосПодраздел = код;
  if (текущий !== 'голос') открыть('голос');
  else показатьГолосПодраздел(код);
}

/* ---------- Меню листается ----------
   Окно пульта бывает низким (1000×640), а раскрытые «Настройки» и «Голос»
   добавляют по восемь строк. Раньше лишнее молча уходило за край. Теперь
   пункты в своей полосе: кнопки ▲/▼ появляются, только когда за краем
   что-то есть, и листают на три строки; колесо тоже работает. */
const менюСписок = $('меню-список');
const менюВверх = $('меню-вверх');
const менюВниз = $('меню-вниз');
const МЕНЮ_ШАГ = 120;

function менюКнопкиЛистания() {
  if (!менюСписок) return;
  const запас = 2;
  const выше = менюСписок.scrollTop > запас;
  const ниже = менюСписок.scrollTop + менюСписок.clientHeight < менюСписок.scrollHeight - запас;
  менюВверх.hidden = !выше;
  менюВниз.hidden = !ниже;
}

/* Раскрыли подменю — показываем его целиком, а не только заголовок: иначе
   раскрытое оказалось бы за нижним краем и выглядело бы как «ничего не
   произошло». Ждём конца анимации раскрытия (0.22 с). */
function менюПоказатьРаскрытое() {
  setTimeout(() => {
    if (!менюСписок) return;
    const открытое = document.querySelector('#меню .подменю-настроек.открыто');
    if (открытое) {
      /* Листаем только полосу меню, сами: `scrollIntoView` подвинул бы ещё и
         страницу, у которой своя прокрутка запрещена. Заголовок раздела
         важнее хвоста подменю — если не влезает всё, виден заголовок. */
      const полоса = менюСписок.getBoundingClientRect();
      const низ = открытое.getBoundingClientRect().bottom;
      const пункт = открытое.previousElementSibling || открытое;
      const верх = пункт.getBoundingClientRect().top;
      let сдвиг = 0;
      if (низ > полоса.bottom) сдвиг = Math.min(низ - полоса.bottom + 4, верх - полоса.top - 4);
      else if (верх < полоса.top) сдвиг = верх - полоса.top - 4;
      if (сдвиг) менюСписок.scrollBy({ top: сдвиг, behavior: 'smooth' });
    }
    менюКнопкиЛистания();
  }, 260);
}

if (менюСписок) {
  менюВверх.addEventListener('click', () => менюСписок.scrollBy({ top: -МЕНЮ_ШАГ, behavior: 'smooth' }));
  менюВниз.addEventListener('click', () => менюСписок.scrollBy({ top: МЕНЮ_ШАГ, behavior: 'smooth' }));
  менюСписок.addEventListener('scroll', менюКнопкиЛистания, { passive: true });
  window.addEventListener('resize', менюКнопкиЛистания);
  // Подменю раскрываются анимацией — высота списка меняется без resize окна.
  document.querySelectorAll('#меню .подменю-настроек').forEach((подменю) => {
    подменю.addEventListener('transitionend', менюКнопкиЛистания);
  });
  if (window.ResizeObserver) new ResizeObserver(менюКнопкиЛистания).observe(менюСписок);
  менюКнопкиЛистания();
}

/* Карточки Панели ведут в нужное место: озвучивание и слух — в «Голос». */
function открытьНастройкиПодраздел(код) {
  if (НАСТР_СТРАНИЦЫ.голос.includes(код)) {
    открытьГолосПодраздел(код);
    return;
  }
  настрТекущийРаздел = код;
  if (текущий !== 'настройки') открыть('настройки');
  else настрПоказатьРаздел(код);
}

/* ---------- Карточки состояния ---------- */

/* Рисуем один раз, дальше только меняем текст: перерисовка целиком
   сбрасывает выделение и моргает. */
function построить() {
  карточки.innerHTML = КАРТОЧКИ.map(
    (имя) =>
      '<div class="карточка" id="к-' + имя + '" role="button" tabindex="0" title="Открыть настройки: ' + имя + '">' +
      '<div class="имя">' + имя + '</div>' +
      '<div class="главное">…</div>' +
      '<div class="внизу"><i></i><span></span></div>' +
      '</div>'
  ).join('');
  const переходы = { слух: 'слух', голос: 'голос', мозг: 'ответы', телефон: 'телефон', память: 'память' };
  for (const [имя, раздел] of Object.entries(переходы)) {
    const карточка = $('к-' + имя);
    карточка.addEventListener('click', () => открытьНастройкиПодраздел(раздел));
    карточка.addEventListener('keydown', (событие) => {
      if (событие.key === 'Enter' || событие.key === ' ') {
        событие.preventDefault();
        открытьНастройкиПодраздел(раздел);
      }
    });
  }
}

function обновить(данные) {
  for (const имя of КАРТОЧКИ) {
    const карточка = $('к-' + имя);
    const часть = данные[имя];
    if (!карточка || !часть) continue;
    карточка.querySelector('.главное').textContent = часть.главное;
    карточка.querySelector('.внизу span').textContent = часть.внизу;
    карточка.classList.toggle('плохо', !часть.хорошо);
  }
}

async function спросить() {
  try {
    const ответ = await fetch('/state', { cache: 'no-store' });
    последние = await ответ.json();
    обновить(последние);
    if (текущий === 'панель') нарисоватьПанель();
    связь.classList.add('есть');
    связь.querySelector('span').textContent = 'сервер на связи';
  } catch (e) {
    связь.classList.remove('есть');
    связь.querySelector('span').textContent = 'нет связи с сервером';
  }
  опроситьРантайм();
}

function часы() {
  const т = new Date();
  const два = (n) => String(n).padStart(2, '0');
  $('часы').textContent = два(т.getHours()) + ':' + два(т.getMinutes());
}

построить();
/* Значок темы в шапке: переключает и красит сразу, и сохраняет сам. Значок
   ставим сразу — сервер уже вписал `data-theme` в страницу, и до первого
   открытия настроек поле «Тема» может и не появиться. */
$('тема-значок').addEventListener('click', темаЗначокПереключить);
темаЗначокОбновить();
/* «Поддержать»: сердечко внизу меню открывает страницу автора. Адреса
   знает только сервер (config.py) — отсюда уходит лишь имя кнопки. */
$('поддержать').addEventListener('click', () => открыть('поддержка'));

/* Логотипы Telegram и YouTube для кнопок «Поддержать». Только два, оба
   своих: ни ссылок на файлы, ни данных из сети — хозяин видит ровно то,
   что мы нарисовали. */
const ЛОГОТИПЫ = {
  telegram: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" focusable="false">' +
    '<circle cx="12" cy="12" r="12" fill="#229ED9"/>' +
    '<path d="M5.9 11.9 17.6 7.4a.42.42 0 0 1 .53.46l-1.4 8.5a.44.44 0 0 1-.66.36l-2.9-1.85-1.4 1.35a.3.3 0 0 1-.5-.23l.2-2.72 6.32-5.7a.27.27 0 0 0-.37-.38l-7.96 4.8-3.2-.8a.3.3 0 0 1-.02-.55Z" fill="#fff"/>' +
    '</svg>',
  youtube: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" focusable="false">' +
    '<rect x="1" y="4" width="22" height="16" rx="5" fill="#FF0000"/>' +
    '<path d="M10 8.6 16.2 12 10 15.4Z" fill="#fff"/>' +
    '</svg>',
};

async function нарисоватьПоддержку() {
  const корень = document.createElement('div');
  корень.className = 'поддержка';
  const карта = document.createElement('div');
  карта.className = 'поддержка-карта';
  корень.appendChild(карта);
  лист.appendChild(корень);
  let данные = null;
  try {
    const ответ = await fetch('/api/support', { cache: 'no-store' });
    данные = await ответ.json();
  } catch (e) {
    данные = null;
  }
  if (текущий !== 'поддержка') return;
  if (!данные || !данные.ok) {
    карта.textContent = 'Нет связи с сервером.';
    return;
  }
  const имя = document.createElement('div');
  имя.className = 'поддержка-имя';
  имя.textContent = данные.name || 'Автор';
  const текст = document.createElement('div');
  текст.className = 'поддержка-текст';
  текст.textContent = данные.text || '';
  const кнопки = document.createElement('div');
  кнопки.className = 'поддержка-кнопки';
  const статус = document.createElement('div');
  статус.className = 'поддержка-статус';
  const ссылки = данные.links || {};
  /* Логотипы — строкой в коде: внешних файлов у пульта нет, а вставлять
     разметку из ответа сервера нельзя. Набор фиксированный, и риск
     подстановки чужих данных здесь нулевой. */
  [['donate', 'Поддержать', true], ['telegram', 'Telegram', false], ['youtube', 'YouTube', false]]
    .forEach(([что, подпись, главная]) => {
      if (!ссылки[что]) return;
      const кнопка = document.createElement('button');
      кнопка.type = 'button';
      if (главная) {
        кнопка.className = 'голос-кнопка главная';
        кнопка.textContent = подпись;
      } else {
        // Логотип вместо текста: квадрат 44×44, подпись — только в подсказке.
        кнопка.className = 'поддержка-логотип';
        кнопка.title = подпись;
        кнопка.setAttribute('aria-label', подпись);
        кнопка.innerHTML = ЛОГОТИПЫ[что];
      }
      кнопка.addEventListener('click', async () => {
        статус.textContent = 'Открываю в браузере…';
        try {
          const ответ = await fetch('/api/support/open', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ what: что }),
          });
          const итог = await ответ.json();
          статус.textContent = итог.ok ? '' : (итог.error || 'не открылось');
        } catch (e) {
          статус.textContent = 'нет связи с сервером';
        }
      });
      кнопки.appendChild(кнопка);
    });
  карта.appendChild(имя);
  карта.appendChild(текст);
  карта.appendChild(кнопки);
  карта.appendChild(статус);
}

/* Кнопки мастера живут в разметке (pult.html) и переживают перерисовку шага:
   сам мастер их не трогает, кроме текста «Шаг N» и полоски. Esc мастер не
   закрывает — шесть шагов, потерянных по неловкому нажатию, хуже лишнего
   щелчка (см. также `мастерЗакрыть`). */
$('мастер-дальше').addEventListener('click', мастерДальше);
$('мастер-назад').addEventListener('click', мастерНазад);
$('мастер-пропустить').addEventListener('click', мастерПропустить);

открыть('панель');
спросить();
часы();
проверитьЗначокПриЗапуске();
спроситьПервыйЗапуск();

/* Две секунды — достаточно живо для глаз и не греет процессор.
   Когда приедет лента событий, состояние поедет по сокету, а опрос
   останется запасным путём. */
setInterval(спросить, 2000);
setInterval(часы, 10000);

/* ---------- Готовые характеры ----------
   28.09 хозяин: «человек скачает, а она „привет, кожаный“ — пусть будет так,
   но лучше дать возможность нормально отвечать». Карточки над полем
   «Характер»: нажал — текст подставился, дальше правится руками и
   сохраняется общей кнопкой, как раньше. */
async function характерЗагрузить(узел, поле) {
  try {
    const ответ = await fetch('/api/personas', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные.ok) return;
    характерыГотовые = Array.isArray(данные.presets) ? данные.presets : [];
  } catch (e) { return; }
  узел.textContent = '';
  for (const готовый of характерыГотовые) {
    const карточка = document.createElement('button');
    карточка.type = 'button';
    карточка.className = 'характер-карточка';
    карточка.dataset.характер = готовый.id;
    const имя = document.createElement('span');
    имя.className = 'характер-имя';
    имя.textContent = готовый.title;
    const намёк = document.createElement('span');
    намёк.className = 'характер-намёк';
    намёк.textContent = готовый.hint;
    карточка.append(имя, намёк);
    карточка.addEventListener('click', () => характерВыбрать(готовый, поле));
    узел.appendChild(карточка);
  }
  характерОтметить();
}

function характерОтметить() {
  /* Полная подсветка — только когда в поле ровно текст готового характера.
     Поправил под себя — обводка пунктиром: манера та же, текст свой. */
  const поле = настрЭлементы && настрЭлементы.persona;
  document.querySelectorAll('.характер-карточка').forEach((карточка) => {
    const готовый = характерыГотовые.find((г) => г.id === карточка.dataset.характер);
    const наш = карточка.dataset.характер === характерВыбранный;
    const совпадает = !поле || !готовый || поле.value === готовый.text;
    карточка.classList.toggle('активный', наш && совпадает);
    карточка.classList.toggle('правленый', наш && !совпадает);
    карточка.title = наш && !совпадает ? 'Выбран этот, но текст поправлен под себя' : '';
  });
}

/* ---------- Мастер первого запуска ----------
   Пока `settings.first_run_done === false`, при загрузке пульта поверх всего
   открывается этот мастер: шесть шагов, после которых Труба отвечает голосом.
   У хозяина флаг уже `true`, и он его не видит никогда (см. также
   core/settings.py::load_settings — у кого папка Трубы была, мастер не нужен).

   Мастер — слой `#мастер` (разметка в pult.html, стили с префиксом `мастер-`
   в pult.css). Данные каждого шага читаются при входе на шаг, а не заранее
   одним запросом: половина мастера — это разговор с сервером по ходу дела.
   Esc мастер не закрывает: шесть шагов, потерянных по неловкому нажатию,
   хуже, чем лишний щелчок. */

const МАСТЕР_ШАГОВ = 6;
const мастерСлой = () => $('мастер');
let мастерШаг = 0;
let мастерОткрыт = false;
/* Пока идёт сохранение шага, «Дальше» молчит: иначе можно проскочить мимо
   ошибки, которую сервер только что вернул. */
let мастерТянем = false;
/* Данные шага переживают «Назад»: введённый ключ или город не должны
   пропадать, потому что хозяин решил вернуться и поправить предыдущий шаг. */
let мастерЧерновик = {};

/* Проверка «жив» для мастера: пока летел запрос, мастер могли закрыть или
   уйти на другой шаг. Узлы шага лежат в `мастерШаги` по номеру шага, и
   при перерисовке старый узел там сменяется — поэтому сверяем с текущим. */
function мастерЖива(эл) {
  return !!эл && мастерОткрыт && мастерШаги[мастерШаг] === эл;
}
const мастерШаги = {};

function мастерСтатус(эл, текст, плохо) {
  if (!мастерЖива(эл) || !эл.статус) return;
  эл.статус.textContent = текст || '';
  эл.статус.classList.toggle('плохо', !!плохо);
}

async function мастерСохранить(тело) {
  try {
    const ответ = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(тело),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && (данные.error || (данные.errors || []).join('; ')))
        || ('сервер ответил ' + ответ.status));
    }
    return true;
  } catch (e) {
    return (e && e.message) ? e.message : String(e);
  }
}

function мастерОткрыть(сШага) {
  const слой = мастерСлой();
  if (!слой) return;
  мастерШаг = Number(сШага) > 0 && Number(сШага) <= МАСТЕР_ШАГОВ ? Number(сШага) : 1;
  мастерОткрыт = true;
  звукУровеньВыключить();
  слой.hidden = false;
  мастерНарисоватьШаг();
}

/* Закрыть можно только «Пропустить настройку» или «Начать» — оба с записью
   `first_run_done`. Из «О программе» мастер открывается, но флаг не трогает:
   это «заново», а не «ещё не настроено». */
function мастерЗакрыть(раздел) {
  const слой = мастерСлой();
  мастерОткрыт = false;
  мастерШаг = 0;
  мастерЧерновик = {};
  звукУровеньВыключить();
  мастерУровеньВыключить();
  for (const ключ of Object.keys(мастерШаги)) delete мастерШаги[ключ];
  if (слой) слой.hidden = true;
  if (раздел) открыть(раздел);
  else if (текущий) открыть(текущий);
}

async function мастерПропустить() {
  /* Кто пропускает мастер, мимо шага «Характер» проходит — а по умолчанию он
     «Пиздабол Edition», с матом. Говорим об этом здесь же (29.09). */
  const характер = await мастерСохранённое('persona_preset');
  const сМатом = !характер || характер === 'pizdabol';
  if (!window.confirm('Пропустить первую настройку? Всё это можно сделать потом в '
    + '«Настройках» и «Голосе».'
    + (сМатом ? '\n\nХарактер останется «Пиздабол Edition» — с матом и подколками. '
      + 'Сменить его — «Настройки → Характер».' : ''))) return;
  мастерСохранить({ first_run_done: true }).then((итог) => {
    if (typeof итог === 'string') {
      const эл = мастерШаги[мастерШаг];
      if (эл) мастерСтатус(эл, 'Не вышло отметить настройку: ' + итог, true);
      return;
    }
    мастерЗакрыть();
  });
}

/* Запомнить шаг в данных этой установки (`wizard_step` в settings.json).
   Возвращает `true` или строку ошибки — как `мастерСохранить`. Вызывающий
   ждёт ответа ДО перерисовки шага: окно можно закрыть сразу после щелчка,
   и не записанный запрос просто потерялся бы. */
async function мастерШагЗапомнить(шаг) {
  return мастерСохранить({ wizard_step: шаг });
}

async function мастерНазад() {
  if (мастерШаг > 1 && !мастерТянем) {
    const эл = мастерШаги[мастерШаг];
    const прежний = мастерШаг;
    мастерШаг -= 1;
    мастерТянем = true;
    let итог;
    try { итог = await мастерШагЗапомнить(мастерШаг); } finally { мастерТянем = false; }
    /* Не записалось — остаёмся на прежнем шаге и говорим почему. */
    if (typeof итог === 'string') {
      мастерШаг = прежний;
      мастерСтатус(эл, 'Не вышло запомнить шаг: ' + итог, true);
      return;
    }
    мастерНарисоватьШаг();
  }
}

async function мастерДальше() {
  const эл = мастерШаги[мастерШаг];
  if (!эл || мастерТянем) return;
  if (мастерШаг >= МАСТЕР_ШАГОВ) {
    const итог = await мастерСохранить({ first_run_done: true });
    if (typeof итог === 'string') {
      мастерСтатус(эл, 'Не вышло сохранить: ' + итог, true);
      return;
    }
    мастерЗакрыть('панель');
    return;
  }
  if (эл.сохранить) {
    /* Пока идёт сохранение, «Дальше» не нажимается второй раз: иначе шаг
       перелистнётся мимо ошибки. */
    мастерТянем = true;
    let ошибка = '';
    try { ошибка = await эл.сохранить(); } finally { мастерТянем = false; }
    if (ошибка) { мастерСтатус(эл, ошибка, true); return; }
  }
  /* Шаг запоминаем только после того, как шаг прошёл: если `сохранить`
     вернуло ошибку, до следующего шага не переходим — иначе человек ушёл бы
     мимо того, что не сохранилось. И ждём ответа сервера, показывая новый
     шаг только после записи: окно могут закрыть, и несохранённый номер
     потерялся бы. */
  мастерШаг += 1;
  мастерТянем = true;
  let итог;
  try { итог = await мастерШагЗапомнить(мастерШаг); } finally { мастерТянем = false; }
  if (typeof итог === 'string') {
    мастерШаг -= 1;
    мастерСтатус(эл, 'Не вышло запомнить шаг: ' + итог, true);
    return;
  }
  мастерНарисоватьШаг();
}

function мастерНарисоватьШаг() {
  const слой = мастерСлой();
  /* Полоса выбранного микрофона принадлежит шагу 4: ушли на другой шаг —
     запросы прекратились, а сам слой мастера мог быть закрыт вовсе. */
  мастерУровеньВыключить();
  if (!слой || !мастерОткрыт) return;
  const тело = $('мастер-тело');
  $('мастер-шаг').textContent = 'Шаг ' + мастерШаг + ' из ' + МАСТЕР_ШАГОВ;
  $('мастер-полоса').style.width =
    Math.round((мастерШаг / МАСТЕР_ШАГОВ) * 100) + '%';
  $('мастер-назад').hidden = мастерШаг <= 1;
  $('мастер-дальше').textContent = мастерШаг >= МАСТЕР_ШАГОВ ? 'Начать' : 'Дальше';
  тело.textContent = '';
  тело.scrollTop = 0;
  const рисовальщик = МАСТЕР_РИСОВАТЬ[мастерШаг];
  if (!рисовальщик) return;
  рисовальщик(тело);
}

/* --- Шаг 1: привет и «твой компьютер» --- */

function мастерЗаголовок(тело, текст) {
  const узел = document.createElement('h2');
  узел.className = 'мастер-заголовок';
  узел.textContent = текст;
  тело.appendChild(узел);
  return узел;
}

function мастерТекст(тело, текст) {
  const узел = document.createElement('p');
  узел.className = 'мастер-текст';
  узел.textContent = текст;
  тело.appendChild(узел);
  return узел;
}

function мастерРяд(тело, подпись, узел) {
  const ряд = document.createElement('div');
  ряд.className = 'мастер-ряд';
  const имя = document.createElement('span');
  имя.textContent = подпись;
  ряд.append(имя, узел);
  тело.appendChild(ряд);
  return ряд;
}

function мастерКнопка(текст, главная) {
  const кнопка = document.createElement('button');
  кнопка.type = 'button';
  кнопка.className = 'голос-кнопка' + (главная ? ' главная' : '');
  кнопка.textContent = текст;
  return кнопка;
}

function мастерПодпись(эл, текст, плохо) {
  const узел = document.createElement('div');
  узел.className = 'мастер-подпись';
  узел.textContent = текст;
  if (плохо) узел.classList.add('плохо');
  эл.тело.appendChild(узел);
  return узел;
}

function мастерШагПривет(тело) {
  мастерЗаголовок(тело, 'Привет');
  мастерТекст(тело, 'Труба — голосовой помощник: слушает микрофон, отвечает голосом, '
    + 'запускает программы, пишет заметки, ищет в интернете. '
    + 'Пара минут настройки — и можно говорить');
  /* Перенос из файла — до «Твоего компьютера»: человек, который ставил Трубу
     раньше, не должен проходить настройку заново. Тот же блок и те же
     функции, что в «Настройках → Система», но без экспорта: сохранять
     тут ещё нечего. */
  const переносМесто = document.createElement('div');
  переносМесто.className = 'мастер-блок';
  переносБлок(переносМесто, 'Перенос настроек',
    'Уже пользовался Трубой? Перенеси настройки из файла', false);
  тело.appendChild(переносМесто);
  const блок = document.createElement('div');
  блок.className = 'мастер-блок';
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = 'Твой компьютер';
  блок.appendChild(шапка);
  const железо = document.createElement('div');
  железо.className = 'железо-блок';
  железо.textContent = 'Смотрю, что за компьютер…';
  блок.appendChild(железо);
  тело.appendChild(блок);
  const подпись = document.createElement('div');
  подпись.className = 'мастер-подпись';
  // Пусто, пока ничего не случилось: «нет связи» здесь висело и тогда,
  // когда железо пришло (28.09).
  подпись.textContent = '';
  тело.appendChild(подпись);
  const эл = { тело, железо, статус: подпись, сохранить: null };
  мастерШаги[мастерШаг] = эл;
  мастерШагПриветЗагрузить(эл);
}

/* Железо и качественные голоса — те же ответы, что у блока «Твой компьютер»
   в «Голос → Озвучивании» (`/api/hardware`, `/api/voices/status`). */
async function мастерШагПриветЗагрузить(эл) {
  try {
    const ответ = await fetch('/api/hardware', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('железо не определилось');
    const совет = данные.recommend || {};
    /* Только процессор, память и видеокарта: в приветствии длинный список
       железа лишний, а полное описание — рядом, в «Голос → Озвучивании». */
    мастерЖелезоКоротко(эл, данные);
    if (совет.voice_why) мастерПодпись(эл, совет.voice_why);
    if (совет.voice && совет.voice !== 'silero') {
      const статус = await fetch('/api/voices/status', { cache: 'no-store' });
      const данныеГолосов = await статус.json().catch(() => null);
      if (!мастерЖива(эл)) return;
      if (статус.ok && данныеГолосов && данныеГолосов.ok && !данныеГолосов.installed) {
        мастерПодпись(эл, 'Качественный голос можно поставить потом: '
          + '«Голос → Озвучивание» → «Качественные голоса»');
      }
    }
  } catch (e) {
    if (!мастерЖива(эл)) return;
    эл.железо.textContent = 'Не вышло узнать компьютер: '
      + ((e && e.message) ? e.message : e);
  }
}

/* Три строки — процессор, память, видеокарта. Вид тот же, что у блока
   «Твой компьютер» (`.железо-строка`), а набор строк короче: в приветствии
   подробное описание железа лишнее, оно живёт в «Голос → Озвучивании». */
function мастерЖелезоКоротко(эл, данные) {
  const hw = (данные && данные.hw) || {};
  const процессор = hw.cpu || {};
  const gpu = Array.isArray(hw.gpus) ? hw.gpus : [];
  эл.железо.textContent = '';
  const строка = (имя, значение) => {
    const у = document.createElement('div');
    у.className = 'железо-строка';
    const п = document.createElement('span');
    п.textContent = имя;
    const з = document.createElement('b');
    з.textContent = значение;
    у.append(п, з);
    эл.железо.appendChild(у);
  };
  if (процессор.name) {
    строка('Процессор', [процессор.name,
      (число(процессор.cores) || 0) + ' ядер',
      (число(процессор.threads) || 0) + ' потоков'].join(', '));
  }
  if (hw.ram_gb !== undefined && hw.ram_gb !== null) {
    строка('Память', String(hw.ram_gb) + ' ГБ');
  }
  if (gpu.length === 0) {
    строка('Видеокарта', 'NVIDIA не найдена');
  } else {
    for (const карта of gpu) {
      if (!карта || !карта.name) continue;
      строка('Видеокарта', [карта.name,
        (Math.round(число(карта.vram_gb) || 0)) + ' ГБ'].filter(Boolean).join(', '));
    }
  }
  if (!эл.железо.childElementCount) {
    эл.железо.textContent = 'Не вышло узнать компьютер';
  }
}

/* --- Шаг 2: мозг (сервис ответов и ключ) --- */

function мастерШагМозг(тело) {
  мастерЗаголовок(тело, 'Мозг');
  мастерТекст(тело, 'Думает Труба в облаке — нужен ключ сервиса. Проще всего DeepSeek: '
    + 'около 100 ₽ в месяц при обычных разговорах');
  const provider = настрВыбор([], '');
  мастерРяд(тело, 'Сервис ответов', provider);
  const рядКлюча = document.createElement('div');
  const ключПоле = document.createElement('div');
  ключПоле.className = 'настр-ключ-поле';
  const api_key = document.createElement('input');
  api_key.type = 'password';
  api_key.className = 'настр-ввод';
  api_key.autocomplete = 'new-password';
  api_key.setAttribute('aria-label', 'Ключ доступа');
  ключПоле.appendChild(api_key);
  const ключПоказать = document.createElement('button');
  ключПоказать.type = 'button';
  ключПоказать.className = 'настр-иконка';
  ключПоказать.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  ключПоказать.title = 'Показать ключ';
  ключПоказать.setAttribute('aria-label', 'Показать ключ');
  ключПоле.appendChild(ключПоказать);
  рядКлюча.appendChild(ключПоле);
  тело.appendChild(рядКлюча);
  const кнопки = document.createElement('div');
  кнопки.className = 'мастер-ряд';
  const гдеКлюч = мастерКнопка('Где взять ключ');
  гдеКлюч.title = 'Откроет страницу ключей этого сервиса в браузере';
  const проверить = мастерКнопка('Проверить связь');
  проверить.title = 'Один короткий запрос к модели; настройки не сохраняются';
  кнопки.append(гдеКлюч, проверить);
  тело.appendChild(кнопки);
  const подсказка = document.createElement('div');
  подсказка.className = 'мастер-подпись';
  тело.appendChild(подсказка);
  const локальныйБлок = document.createElement('div');
  локальныйБлок.hidden = true;
  const local_url = настрВвод('');
  local_url.placeholder = 'http://127.0.0.1:11434/v1';
  local_url.autocomplete = 'off';
  const адреса = document.createElement('div');
  адреса.className = 'мастер-адрес-поле';
  адреса.appendChild(local_url);
  for (const [имя, адрес] of ЛОКАЛЬНЫЕ_АДРЕСА) {
    const кнопка = document.createElement('button');
    кнопка.type = 'button';
    кнопка.className = 'настр-иконка настр-адрес-кнопка';
    кнопка.textContent = имя;
    кнопка.title = 'Вписать адрес: ' + адрес;
    кнопка.setAttribute('aria-label', 'Вписать адрес ' + имя);
    кнопка.addEventListener('click', () => { local_url.value = адрес; });
    адреса.appendChild(кнопка);
  }
  локальныйБлок.appendChild(адреса);
  const предупреждение = document.createElement('div');
  предупреждение.className = 'мастер-предупреждение';
  предупреждение.textContent = ЛОКАЛЬНАЯ_ПРЕДУПРЕЖДЕНИЕ;
  локальныйБлок.appendChild(предупреждение);
  тело.appendChild(локальныйБлок);
  const эл = { тело, provider, api_key, ключПоказать, подсказка, гдеКлюч, проверить,
    локальныйБлок, local_url, рядКлюча, статус: подсказка, провайдеры: {} };
  мастерШаги[мастерШаг] = эл;
  ключПоказать.addEventListener('click', () => {
    api_key.type = api_key.type === 'password' ? 'text' : 'password';
    const подпись = api_key.type === 'password' ? 'Показать ключ' : 'Скрыть ключ';
    ключПоказать.title = подпись;
    ключПоказать.setAttribute('aria-label', подпись);
  });
  гдеКлюч.addEventListener('click', () => мастерОткрытьКлюч(эл));
  проверить.addEventListener('click', () => мастерПроверитьСвязь(эл));
  provider.addEventListener('change', () => мастерПоказатьСервис(эл));
  эл.сохранить = () => мастерСохранитьМозг(эл);
  мастерШагМозгЗагрузить(эл);
}

function характерВыбрать(готовый, поле) {
  const hw = (данные && данные.hw) || {};
  const процессор = hw.cpu || {};
  const gpu = Array.isArray(hw.gpus) ? hw.gpus : [];
  эл.железо.textContent = '';
  const строка = (имя, значение) => {
    const у = document.createElement('div');
    у.className = 'железо-строка';
    const п = document.createElement('span');
    п.textContent = имя;
    const з = document.createElement('b');
    з.textContent = значение;
    у.append(п, з);
    эл.железо.appendChild(у);
  };
  if (процессор.name) {
    строка('Процессор', [процессор.name,
      (число(процессор.cores) || 0) + ' ядер',
      (число(процессор.threads) || 0) + ' потоков'].join(', '));
  }
  if (hw.ram_gb !== undefined && hw.ram_gb !== null) {
    строка('Память', String(hw.ram_gb) + ' ГБ');
  }
  if (gpu.length === 0) строка('Видеокарта', 'NVIDIA не найдена');
  for (const карта of gpu) {
    if (!карта || !карта.name) continue;
    строка('Видеокарта', [карта.name,
      (Math.round(число(карта.vram_gb) || 0)) + ' ГБ'].filter(Boolean).join(', '));
  }
  if (!эл.железо.childElementCount) {
    эл.железо.textContent = 'Не вышло узнать компьютер';
  }
}

/* Список сервисов и текущий выбор — из `/api/settings`: те же подписи, что в
   «Настройках → Ответы», и тот же ключ `has_key` (есть ли он на сервере). */
async function мастерШагМозгЗагрузить(эл) {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('сервер не ответил');
    эл.провайдеры = (данные && данные.providers) || {};
    const s = (данные && данные.settings) || {};
    эл.provider.textContent = '';
    for (const имя of Object.keys(эл.провайдеры)) {
      const пункт = document.createElement('option');
      пункт.value = имя;
      пункт.textContent = СЕРВИС_НАЗВАНИЯ[имя] || имя;
      эл.provider.appendChild(пункт);
    }
    if (typeof данные.provider === 'string' && эл.провайдеры[данные.provider]) {
      эл.provider.value = данные.provider;
    }
    if (эл.provider.selectedIndex < 0 && эл.provider.options.length > 0) {
      эл.provider.selectedIndex = 0;
    }
    эл.local_url.value = typeof s.local_url === 'string' ? s.local_url : '';
    мастерПоказатьСервис(эл);
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Нет связи с сервером — ключ можно будет вписать позже в '
      + '«Настройках → Ответы»', true);
  }
}

function мастерПоказатьСервис(эл) {
  if (!мастерЖива(эл)) return;
  const имя = эл.provider.value;
  const запись = эл.провайдеры[имя] || {};
  const локальный = имя === ЛОКАЛЬНЫЙ_СЕРВИС;
  эл.локальныйБлок.hidden = !локальный;
  эл.рядКлюча.hidden = локальный;
  /* Сохранённый ключ не показываем: хозяину важно, что он есть, а не сам ключ.
     «Проверить связь» пойдёт без него — сервер у себя его возьмёт. */
  эл.api_key.placeholder = запись.has_key ? 'Ключ сохранён' : 'Вставь ключ API';
  эл.подсказка.textContent = локальный ? '' : (СЕРВИС_ПОДСКАЗКИ[имя] || '');
  эл.подсказка.classList.remove('плохо');
}

/* Страницу ключей открывает сервер: адрес берётся из `config.KEY_PAGES`, а не
   из запроса, иначе кнопка была бы дырой для чужой страницы в браузере. */
async function мастерОткрытьКлюч(эл) {
  if (!мастерЖива(эл)) return;
  мастерСтатус(эл, 'Открываю страницу ключей…');
  try {
    const ответ = await fetch('/api/open/key-page', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: эл.provider.value }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, (данные && данные.ok)
      ? 'Страница открылась в браузере — ключ скопируй оттуда и вставь выше'
      : ((данные && данные.error) || 'страница не открылась'), !данные || !данные.ok);
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Нет связи с сервером', true);
  }
}

async function мастерПроверитьСвязь(эл) {
  if (!мастерЖива(эл)) return;
  const запись = эл.провайдеры[эл.provider.value] || {};
  эл.проверить.disabled = true;
  мастерСтатус(эл, 'Проверяю связь коротким запросом…');
  try {
    const ответ = await fetch('/api/settings/test-provider', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: эл.provider.value, model: запись.model || '',
        key: эл.api_key.value.trim() }),
    });
    const данные = await ответ.json().catch(() => ({}));
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) {
      throw new Error((данные && данные.error) || ('сервер ответил ' + ответ.status));
    }
    мастерСтатус(эл, 'Связь есть. Ответ: ' + данные.answer);
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Связь не прошла: ' + ((e && e.message) ? e.message : e), true);
  }
  if (мастерЖива(эл)) эл.проверить.disabled = false;
}

async function мастерСохранитьМозг(эл) {
  if (!мастерЖива(эл)) return '';
  const имя = эл.provider.value;
  if (!имя) return 'Выбери сервис ответов';
  const запись = эл.провайдеры[имя] || {};
  const локальный = имя === ЛОКАЛЬНЫЙ_СЕРВИС;
  const адрес = эл.local_url.value.trim();
  if (локальный && !адрес) return 'Впиши адрес локального сервера — или нажми Ollama';
  /* Ключа нет и раньше не было — предупреждаем, но идём дальше: человек мог
     решить вписать ключ позже. */
  if (!локальный && !запись.has_key && !эл.api_key.value.trim()) {
    if (!window.confirm('Без ключа она не сможет отвечать. Вписать ключ можно потом в '
      + '«Настройки → Ответы». Идём дальше?')) return '';
  }
  const тело = { provider: имя, model: запись.model || '' };
  const ключ = эл.api_key.value.trim();
  if (ключ) тело.api_key = ключ;
  if (локальный) тело.local_url = адрес;
  const итог = await мастерСохранить(тело);
  if (typeof итог === 'string') return 'Не вышло сохранить: ' + итог;
  мастерЧерновик.мозг = { provider: имя, model: тело.model,
    has_key: !!запись.has_key || !!ключ };
  return '';
}

/* --- Шаг 3: характер --- */

function мастерШагХарактер(тело) {
  мастерЗаголовок(тело, 'Характер');
  мастерТекст(тело, 'Выбери, как она разговаривает. Потом это меняется в '
    + '«Настройках → Характер» вместе с текстом.');
  const карточки = document.createElement('div');
  карточки.className = 'мастер-характеры';
  тело.appendChild(карточки);
  /* «Пиздабол Edition» — характер по умолчанию и фишка Трубы, но он с матом.
     Чтобы человек не прошёл мимо, нажимая «Дальше» не глядя, — предупреждение
     прямо под карточками, а на «Дальше» ещё и вопрос (29.09, решение автора). */
  const мат = document.createElement('div');
  мат.className = 'мастер-подпись мастер-мат';
  мат.textContent = 'Осторожно: в «Пиздабол Edition» она матерится и подкалывает. '
    + 'Если её услышат дети или коллеги — выбери другой характер.';
  мат.hidden = true;
  тело.appendChild(мат);
  const подсказка = document.createElement('div');
  подсказка.className = 'мастер-подпись';
  тело.appendChild(подсказка);
  const эл = { тело, карточки, мат, матДа: false, статус: подсказка, выбран: '', был: '',
    тексты: new Map() };
  мастерШаги[мастерШаг] = эл;
  эл.сохранить = () => мастерСохранитьХарактер(эл);
  мастерШагХарактерЗагрузить(эл);
}

/* Карточки те же, что в «Настройках → Характер» (`/api/personas`), и выглядят
   как там: `.характер-карточка`. Отмеченная подсвечивается оранжевой, выбор
   лежит рядом с текстом под ней. */
async function мастерШагХарактерЗагрузить(эл) {
  try {
    const ответ = await fetch('/api/personas', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('характеры не пришли');
    const пресеты = Array.isArray(данные.presets) ? данные.presets : [];
    const сохранённый = await мастерСохранённое('persona_preset');
    if (!мастерЖива(эл)) return;
    эл.был = сохранённый || (данные.default || '');
    эл.выбран = эл.был;
    эл.карточки.textContent = '';
    for (const готовый of пресеты) {
      эл.тексты.set(готовый.id, готовый.text || '');
      const карточка = document.createElement('button');
      карточка.type = 'button';
      карточка.className = 'характер-карточка';
      карточка.dataset.характер = готовый.id;
      const имя = document.createElement('span');
      имя.className = 'характер-имя';
      имя.textContent = готовый.title;
      const намёк = document.createElement('span');
      намёк.className = 'характер-намёк';
      намёк.textContent = готовый.hint;
      карточка.append(имя, намёк);
      карточка.addEventListener('click', () => {
        if (!мастерЖива(эл)) return;
        эл.выбран = готовый.id;
        мастерОтметитьХарактер(эл);
      });
      эл.карточки.appendChild(карточка);
    }
    мастерОтметитьХарактер(эл);
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Нет связи с сервером — характер можно будет выбрать позже', true);
  }
}

function мастерОтметитьХарактер(эл) {
  for (const карточка of эл.карточки.querySelectorAll('.характер-карточка')) {
    карточка.classList.toggle('активный', карточка.dataset.характер === эл.выбран);
  }
  if (эл.мат) эл.мат.hidden = эл.выбран !== 'pizdabol';
}

/* Одно поле из `/api/settings` — тем же запросом, что и вся форма: заводить
   ради одного значения второй маршрут незачем. */
async function мастерСохранённое(ключ) {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return '';
    const s = (данные && данные.settings) || {};
    return s[ключ];
  } catch (e) {
    return '';
  }
}

async function мастерСохранитьХарактер(эл) {
  if (!мастерЖива(эл)) return '';
  // Остаётся характер с матом — переспросить один раз: «Дальше» жмут не глядя.
  if (эл.выбран === 'pizdabol' && !эл.матДа) {
    if (!window.confirm('В характере «Пиздабол Edition» она матерится и подкалывает. '
      + 'Оставить его?\n\n«Отмена» — выбрать другой.')) {
      return 'Выбери характер — карточки выше';
    }
    эл.матДа = true;
  }
  /* Ничего не менял — ничего не шлём: у человека может быть свой текст
     характера, и перезапись готовым тихо испортила бы его. */
  if (!эл.выбран || эл.выбран === эл.был) return '';
  const итог = await мастерСохранить({
    persona_preset: эл.выбран,
    persona: эл.тексты.get(эл.выбран) || '',
  });
  if (typeof итог === 'string') return 'Не вышло сохранить: ' + итог;
  эл.был = эл.выбран;
  return '';
}

/* --- Шаг 4: микрофон и звук --- */

function мастерШагЗвук(тело) {
  мастерЗаголовок(тело, 'Микрофон и звук');
  мастерТекст(тело, 'Выбери микрофон, вход и куда идёт её голос — как в настройках звука игры.');
  const mic_name = настрВыбор([], '');
  мастерРяд(тело, 'Микрофон', mic_name);
  const mic_channel = настрВыбор([['0', 'Вход 1'], ['1', 'Вход 2']], '0');
  const рядВхода = мастерРяд(тело, 'Вход', mic_channel);
  const output = настрВыбор([['speakers', 'Колонки'], ['phone', 'Телефон']], 'speakers');
  мастерРяд(тело, 'Куда её голос', output);
  const speaker_name = настрВыбор([], '');
  мастерРяд(тело, 'Колонки', speaker_name);
  const уровень = document.createElement('div');
  уровень.className = 'звук-уровень';
  const уровеньFill = document.createElement('i');
  уровень.appendChild(уровеньFill);
  мастерРяд(тело, 'Уровень', уровень);
  const подписьУровня = document.createElement('div');
  подписьУровня.className = 'звук-уровень-подпись';
  подписьУровня.textContent = '';
  тело.appendChild(подписьУровня);
  const кнопки = document.createElement('div');
  кнопки.className = 'мастер-ряд';
  /* Две кнопки, а не одна: запись и прослушивание — разные дела. Раньше запись
     играла сразу, один раз и мимо хозяина, и проверить было нечем. */
  const звукПроверить = мастерКнопка('Записать 3 секунды');
  звукПроверить.title = 'Запишет 3 секунды с выбранного микрофона и покажет, что он '
    + 'услышал. Голос на это время остановит сервер и вернёт обратно';
  const звукПрослушать = мастерКнопка('Прослушать');
  звукПрослушать.title = 'Проиграет запись в выбранные колонки — столько раз, '
    + 'сколько нужно, и в любой момент';
  звукПрослушать.disabled = true;
  const перезапустить = мастерКнопка('Перезапустить голос');
  перезапустить.title = 'Нужно, если сменил микрофон: голос сейчас держит старый';
  перезапустить.hidden = true;
  кнопки.append(звукПроверить, звукПрослушать, перезапустить);
  тело.appendChild(кнопки);
  /* Строка результата записи — под кнопками. Живая полоска уровня показывает
     микрофон, а эта — что услышала запись; вердикт «тишина» должен быть виден
     без нажатия «Прослушать». */
  const звукЗапись = document.createElement('div');
  звукЗапись.className = 'звук-уровень-подпись';
  звукЗапись.textContent = '';
  тело.appendChild(звукЗапись);
  const статус = document.createElement('div');
  статус.className = 'мастер-подпись';
  тело.appendChild(статус);
  const эл = { тело, mic_name, mic_channel, mic_channelРяд: рядВхода, output,
    speaker_name, уровень, уровеньFill, уровеньПодпись: подписьУровня,
    звукПроверить, звукПрослушать, звукЗапись, звукСтатус: статус, статус,
    перезапустить, голосБыл: null,
    сохранено: { mic_name: '', mic_channel: 0, speaker_name: '', output: 'speakers' } };
  мастерШаги[мастерШаг] = эл;
  mic_name.addEventListener('change', () => {
    звукПоказатьВход(эл, мастерЖива);
    мастерПоказатьПерезапуск(эл);
    // Список перерисовался, а выбор сменился — полоска должна смотреть на
    // новое устройство, а не на то, что было выбрано секунду назад.
    мастерУровеньВключить(эл);
  });
  mic_channel.addEventListener('change', () => {
    мастерПоказатьПерезапуск(эл);
    мастерУровеньВключить(эл);
  });
  output.addEventListener('change', () => мастерПоказатьПерезапуск(эл));
  звукПроверить.addEventListener('click', () => мастерПроверитьЗвук(эл));
  звукПрослушать.addEventListener('click', () => мастерПрослушать(эл));
  перезапустить.addEventListener('click', () => мастерПерезапуститьГолос(эл));
  эл.сохранить = () => мастерСохранитьЗвук(эл);
  мастерШагЗвукЗагрузить(эл);
}

/* Списки микрофонов и колонок — те же, что в «Голос → Звук»: тот же
   `/api/audio` и та же `загрузитьЗвук`, полоска уровня — `звукУровеньВключить`
   с проверкой мастера вместо проверки формы. */
async function мастерШагЗвукЗагрузить(эл) {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('сервер не ответил');
    const s = (данные && данные.settings) || {};
    эл.сохранено = {
      mic_name: typeof s.mic_name === 'string' ? s.mic_name : '',
      mic_channel: число(s.mic_channel) || 0,
      speaker_name: typeof s.speaker_name === 'string' ? s.speaker_name : '',
      output: typeof s.output === 'string' ? s.output : 'speakers',
    };
    эл.output.value = эл.сохранено.output;
    if (эл.output.selectedIndex < 0) эл.output.selectedIndex = 0;
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Нет связи с сервером — микрофон можно будет выбрать позже '
      + 'в «Голос → Звук»', true);
  }
  if (!мастерЖива(эл)) return;
  await загрузитьЗвук(эл, мастерЖива);
  if (!мастерЖива(эл)) return;
  // Голос узнаём после списков: полоска берёт имя и вход из полей, а до
  // `загрузитьЗвук` в них ещё пусто — замер пошёл бы не туда.
  await мастерШагЗвукЖивой(эл);
  if (!мастерЖива(эл)) return;
  мастерПоказатьПерезапуск(эл);
}

/* Голос может уже работать: тогда полоска идёт по-настоящему, а записью
   микрофон занять нельзя. */
async function мастерШагЗвукЖивой(эл) {
  let работает = false;
  try {
    const ответ = await fetch('/api/runtime', { cache: 'no-store' });
    const данные = await ответ.json().catch(() => null);
    if (!мастерЖива(эл)) return;
    работает = !!(данные && данные.voice && данные.voice.running);
  } catch (e) { /* нет связи — считаем, что голос выключен */ }
  if (!мастерЖива(эл)) return;
  эл.голосБыл = работает;
  /* Полоска идёт в обоих случаях и всегда по **выбранному** микрофону:
     работающий голос может слышать другой, а на чистой установке его
     может не быть вовсе. Записью микрофон при этом не занимается. */
  мастерУровеньВключить(эл);
  if (работает) {
    мастерСтатус(эл, 'Скажи что-нибудь — полоска должна прыгать');
  } else {
    мастерСтатус(эл, 'Скажи что-нибудь в этот микрофон — полоска покажет его '
      + 'уровень, даже пока голос выключен');
  }
  мастерПоказатьПерезапуск(эл);
}

/* --- Полоска выбранного микрофона на шаге 4 ---------------------------------
   Свой опрос, а не `звукУровеньВключить`: тому адрес зашит в `/api/audio/level`,
   а здесь запрос сам называет микрофон и вход из полей — включая тот выбор,
   который хозяин ещё не сохранил. Таймер свой, чтобы не мешать вкладке «Звук»,
   и он же останавливается при уходе со слоя мастера. */
let мастерУровеньТаймер = null;
// Номер текущего прохода опроса: см. `мастерУровеньВключить`.
let мастерУровеньПроход = 0;
// Пауза между запросами. Сервер и сам не замеряет чаще трёх раз в секунду
// (`WebRuntime.PREVIEW_MIN_GAP`), поэтому столько же спрашивает страница.
const МАСТЕР_УРОВЕНЬ_ПАУЗА = 400;

function мастерУровеньВыключить() {
  if (мастерУровеньТаймер) { clearTimeout(мастерУровеньТаймер); мастерУровеньТаймер = null; }
}

/* Адрес замера: имя и вход берём из полей шага, а не из сохранённых настроек. */
function мастерУровеньАдрес(эл) {
  const имя = эл.mic_name && эл.mic_name.value ? String(эл.mic_name.value) : '';
  const вход = эл.mic_channel && эл.mic_channel.value !== '' ? эл.mic_channel.value : '0';
  return '/api/audio/level?preview=1&mic_name=' + encodeURIComponent(имя)
    + '&mic_channel=' + encodeURIComponent(String(вход));
}

async function мастерУровеньОдинРаз(эл) {
  try {
    const ответ = await fetch(мастерУровеньАдрес(эл), { cache: 'no-store' });
    const данные = await ответ.json().catch(() => null);
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) {
      // Сервер объяснил, почему полоска молчит (устройство не открывается) —
      // показываем его причину, а не тишину чужого микрофона.
      мастерУровеньПоказать(эл, 0, true);
      if (эл.уровеньПодпись) {
        эл.уровеньПодпись.textContent = (данные && данные.error)
          ? String(данные.error) : 'микрофон не отвечает';
      }
      return;
    }
    мастерУровеньПоказать(эл, число(данные.level) || 0, false);
  } catch (e) {
    if (мастерЖива(эл)) {
      мастерУровеньПоказать(эл, 0, true);
      if (эл.уровеньПодпись) эл.уровеньПодпись.textContent = 'нет связи с сервером';
    }
  }
}

/* Рисуем полоску и подпись. Подпись здесь своя: у вкладки «Звук» в тишине
   написано «Голос выключен», а здесь голос может и не быть включён — молчит
   именно выбранный микрофон, и хозяин должен видеть почему. */
function мастерУровеньПоказать(эл, level, тихо) {
  if (!мастерЖива(эл) || !эл.уровень) return;
  звукУровеньПоказать(эл, level, тихо, мастерЖива);
  if (!эл.уровеньПодпись) return;
  if (тихо) эл.уровеньПодпись.dataset.тихо = '1';
  else if (эл.уровеньПодпись.dataset.тихо === '1') {
    эл.уровеньПодпись.textContent = '';
    эл.уровеньПодпись.dataset.тихо = '';
  }
}

function мастерУровеньВключить(эл) {
  мастерУровеньВыключить();
  if (!эл || !мастерЖива(эл) || document.hidden) return;
  // Номер прохода: пока запрос летел, выбор могли сменить или шаг уйти. Старый
  // проход тогда не должен снова ставить таймер — иначе опрос ездил бы по
  // устаревшему выбору, и таймеров становилось бы всё больше.
  const проход = ++мастерУровеньПроход;
  const шаг = async () => {
    if (проход !== мастерУровеньПроход) return;
    мастерУровеньТаймер = null;
    if (!мастерЖива(эл) || document.hidden) return;
    await мастерУровеньОдинРаз(эл);
    if (проход !== мастерУровеньПроход) return;
    if (!мастерЖива(эл) || document.hidden) return;
    мастерУровеньТаймер = setTimeout(шаг, МАСТЕР_УРОВЕНЬ_ПАУЗА);
  };
  шаг();
}

/* Кнопка перезапуска — только если голос работает и микрофон сменили: иначе
   это лишний перезапуск на ровном месте. */
function мастерПоказатьПерезапуск(эл) {
  if (!мастерЖива(эл)) return;
  эл.перезапустить.hidden = !эл.голосБыл || !звукНеСохранено(эл, мастерЖива);
}

/* Перезапуск голоса: выключить и включить, дождавшись обоих состояний —
   иначе второй запрос пришёл бы, пока старый ещё сворачивается. */
async function мастерПерезапуститьГолос(эл) {
  if (!мастерЖива(эл)) return;
  эл.перезапустить.disabled = true;
  мастерСтатус(эл, 'Перезапускаю голос…');
  try {
    await fetch('/api/voice/toggle', { method: 'POST' });
    if (!мастерЖива(эл)) return;
    await мастерДождатьсяГолоса(эл, false);
    if (!мастерЖива(эл)) return;
    await fetch('/api/voice/toggle', { method: 'POST' });
    if (!мастерЖива(эл)) return;
    await мастерДождатьсяГолоса(эл, true);
    мастерСтатус(эл, 'Голос перезапущен на новом микрофоне');
    /* Голос теперь слушает ровно выбранный микрофон, но полоска остаётся
       прежней — мастерской: она смотрит на выбор из полей, а не на то, что
       сейчас держит голос. */
    мастерУровеньВключить(эл);
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Перезапуск не получился: '
      + ((e && e.message) ? e.message : e), true);
  }
  if (мастерЖива(эл)) эл.перезапустить.disabled = false;
}

/* Модель с видеокарты грузится секунды, поэтому ждём до минуты и не мучаем
   сервер чаще раза в секунду. */
async function мастерДождатьсяГолоса(эл, работает) {
  const край = Date.now() + 60000;
  while (Date.now() < край) {
    await new Promise((готово) => setTimeout(готово, 1000));
    if (!мастерЖива(эл)) return;
    try {
      const ответ = await fetch('/api/runtime', { cache: 'no-store' });
      const данные = await ответ.json().catch(() => null);
      if (!мастерЖива(эл)) return;
      if (!!(данные && данные.voice && данные.voice.running) === работает) return;
    } catch (e) { /* моргнуло — попробуем ещё */ }
  }
  throw new Error('голос не ответил за минуту');
}

async function мастерСохранитьЗвук(эл) {
  if (!мастерЖива(эл)) return '';
  /* Списков нет — сравнивать нечего, и слать нечего: сохранение любой другой
     настройки не должно стирать выбор микрофона (см. `звукСписки`). */
  if (!звукСписки) return '';
  const вход = эл.mic_channelРяд && эл.mic_channelРяд.hidden
    ? 0 : звукЧисло(эл.mic_channel.value);
  const тело = {};
  if (String(эл.mic_name.value) !== String(эл.сохранено.mic_name)) {
    тело.mic_name = эл.mic_name.value;
  }
  if (вход !== (число(эл.сохранено.mic_channel) || 0)) тело.mic_channel = вход;
  if (String(эл.speaker_name.value) !== String(эл.сохранено.speaker_name)) {
    тело.speaker_name = эл.speaker_name.value;
  }
  if (эл.output.value !== эл.сохранено.output) тело.output = эл.output.value;
  if (!Object.keys(тело).length) {
    мастерПоказатьПерезапуск(эл);
    return '';
  }
  const итог = await мастерСохранить(тело);
  if (typeof итог === 'string') return 'Не вышло сохранить: ' + итог;
  эл.сохранено = {
    mic_name: String(эл.mic_name.value),
    mic_channel: вход,
    speaker_name: String(эл.speaker_name.value),
    output: эл.output.value,
  };
  /* Общий `звукСохранено` — тоже то, что лежит на сервере: о нём судит
     `звукНеСохранено`, а значит и кнопка перезапуска голоса. */
  звукСохранено = {
    mic_name: эл.сохранено.mic_name,
    mic_channel: вход,
    speaker_name: эл.сохранено.speaker_name,
  };
  мастерПоказатьПерезапуск(эл);
  return '';
}

/* Проба микрофона в мастере. Кнопки «Сохранить» тут нет, а общая
   `проверитьЗвук` без сохранённого микрофона пробу не начинает: поэтому мастер
   сам сохраняет выбор (`мастерСохранитьЗвук`) и только потом пишет. Сохранить
   не вышло — не пишем и говорим почему: записать чужим микрофоном и сказать
   «записала» хуже, чем промолчать. Кнопку берём под себя до первого запроса,
   иначе два нажатия подряд дали бы две записи и две отправки настроек. */
async function мастерПроверитьЗвук(эл) {
  if (!мастерЖива(эл) || эл.звукПроверить.disabled) return;
  /* Выбор сохраняем всегда, даже при живом голосе: иначе жалоба «микрофон не
     слышно» осталась бы без единого сохранённого слова. */
  эл.звукПроверить.disabled = true;
  мастерСтатус(эл, 'Сохраняю выбор микрофона…');
  const ошибка = await мастерСохранитьЗвук(эл);
  if (!мастерЖива(эл)) return;
  if (ошибка) {
    мастерСтатус(эл, ошибка + ' — запись не делала', true);
    эл.звукПроверить.disabled = false;
    return;
  }
  /* Голос мог подняться сам (в чистой установке он включается при старте
     пульта) и держит микрофон. Гасит и возвращает его **сервер** в одном
     запросе (`?probe=1`): из пульта выключать его нельзя — уход со шага или
     закрытая вкладка оставили бы хозяину молчащую трубу. */
  if (эл.голосБыл) мастерСтатус(эл, 'Голос включён — сервер приостановит его на время проверки звука…');
  /* Проба пишет микрофон три секунды. Полоска в это время спрашивает уровень
     тем же устройством, и замер с пробой столкнулись бы — пробы могли бы
     прийти к «устройство занято». Поэтому таймер уровня останавливаем **до**
     запроса пробы и поднимаем обратно в `finally`, если мастер всё ещё на
     шаге 4, а окно на виду. Кнопка «Записать 3 секунды» уже под блокировкой:
     повторное нажатие не начнёт вторую пробу. */
  мастерУровеньВыключить();
  try {
    await звукЗаписать(эл, мастерЖива, '/api/audio/test?probe=1');
  } finally {
    if (мастерОткрыт && мастерШаг === 4 && мастерШаги[4] && !document.hidden) {
      мастерУровеньВключить(мастерШаги[4]);
    }
  }
}

/* «Прослушать» в мастере: на время проигрывания полоска уровня молчит, как и
   на время записи. 02.10 test5 упал, когда проигрывание наложилось на замер
   полоски (сервер теперь играет своим потоком, но пауза — вторая страховка). */
async function мастерПрослушать(эл) {
  if (!мастерЖива(эл)) return;
  мастерУровеньВыключить();
  try {
    await прослушатьЗапись(эл, мастерЖива);
  } finally {
    if (мастерОткрыт && мастерШаг === 4 && мастерШаги[4] && !document.hidden) {
      мастерУровеньВключить(мастерШаги[4]);
    }
  }
}

/* --- Шаг 5: погода и телефон --- */

function мастерШагТелефон(тело) {
  мастерЗаголовок(тело, 'Погода и телефон');
  const погода = мастерШагПогода(тело);
  const телефон = мастерШагАдресТелефона(тело);
  const эл = Object.assign({}, погода, телефон,
    { тело, погЧерновик: null, погГородСохранено: '', сохранённыеСсылки: [] });
  мастерШаги[мастерШаг] = эл;
  эл.сохранить = () => мастерСохранитьТелефон(эл);
  мастерШагТелефонЗагрузить(эл);
}

/* Город погоды ищется тем же геокодером, что и в «Настройках → Телефон»
   (`/api/weather/find`, `найтиГород`): координаты хозяину знать незачем. */
function мастерШагПогода(тело) {
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = 'Погода на телефоне';
  тело.appendChild(шапка);
  const город = document.createElement('div');
  город.className = 'настр-адреса';
  город.textContent = '…';
  тело.appendChild(город);
  const ряд = document.createElement('div');
  ряд.className = 'мастер-ряд';
  const ввод = настрВвод('');
  ввод.placeholder = 'Название города';
  ввод.classList.add('пог-ввод');
  const найти = мастерКнопка('Найти');
  найти.title = 'Найдёт города с таким названием — выбери нужный';
  ряд.append(ввод, найти);
  тело.appendChild(ряд);
  const список = document.createElement('div');
  список.className = 'пог-список';
  тело.appendChild(список);
  const без = мастерКнопка('Без погоды');
  без.title = 'Убрать город: погода на телефоне исчезнет';
  без.hidden = true;
  тело.appendChild(без);
  const статус = document.createElement('div');
  статус.className = 'мастер-подпись';
  тело.appendChild(статус);
  return { погГород: город, погВвод: ввод, погСписок: список, погБез: без,
    погСтатус: статус, погНайти: найти };
}

function мастерШагАдресТелефона(тело) {
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = 'Телефон как динамик и пульт';
  тело.appendChild(шапка);
  мастерТекст(тело, 'Труба может говорить через старый телефон — он становится '
    + 'динамиком и пультом. Телефон и компьютер должны быть в одной сети Wi-Fi');
  const рядАдреса = document.createElement('div');
  рядАдреса.className = 'мастер-ряд';
  const адрес = настрВыбор([], '');
  рядАдреса.appendChild(адрес);
  тело.appendChild(рядАдреса);
  const код = document.createElement('div');
  код.className = 'мастер-qr';
  код.hidden = true;
  тело.appendChild(код);
  const подписи = document.createElement('div');
  подписи.className = 'мастер-подпись';
  подписи.textContent = 'Наведи камеру телефона на код и открой ссылку. '
    + 'Коснись экрана телефона — так браузер разрешит звук. '
    + 'Не открывается? Windows мог спросить про брандмауэр — разреши Трубе доступ '
    + 'в частных сетях. Экран телефона гаснет — как это исправить, написано в '
    + '«Настройки → Телефон»';
  тело.appendChild(подписи);
  /* Полный экран — там же, где и остальные подсказки про телефон: без
     установленной иконки браузер адресную строку не убирает. */
  телефонВоВесьЭкран(тело);
  const рядКнопок = document.createElement('div');
  рядКнопок.className = 'мастер-ряд';
  const копировать = мастерКнопка('Копировать адрес');
  рядКнопок.appendChild(копировать);
  тело.appendChild(рядКнопок);
  const статус = document.createElement('div');
  статус.className = 'мастер-подпись';
  тело.appendChild(статус);
  return { телАдрес: адрес, телКопировать: копировать, телСтатус: статус, телКод: код };
}

async function мастерШагТелефонЗагрузить(эл) {
  /* Погода и адреса приходят одним снимком `/api/settings` — тем же, что берёт
     форма, поэтому координаты и адрес выглядят одинаково. */
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!мастерЖива(эл)) return;
    if (!ответ.ok || !данные || !данные.ok) throw new Error('сервер не ответил');
    const s = (данные && данные.settings) || {};
    эл.погГородСохранено = typeof s.weather_city === 'string' ? s.weather_city : '';
    погодаЗаполнить(эл);
    эл.погНайти.addEventListener('click', () => найтиГород(эл, мастерЖива));
    эл.погВвод.addEventListener('keydown', (событие) => {
      if (событие.key === 'Enter') { событие.preventDefault(); найтиГород(эл, мастерЖива); }
    });
    эл.погБез.addEventListener('click', () => погодаБезГорода(эл));
    телефонКлюч = typeof данные.phone_key === 'string' ? данные.phone_key : '';
    запомнитьПортТелефона(данные);
    const адреса = Array.isArray(данные.addresses) ? данные.addresses.map(String) : [];
    const ссылки = адреса.map(настрАдресТелефона).filter(Boolean);
    эл.сохранённыеСсылки = ссылки;
    эл.телАдрес.textContent = '';
    for (const ссылка of ссылки) {
      const пункт = document.createElement('option');
      пункт.value = ссылка;
      пункт.textContent = ссылка;
      эл.телАдрес.appendChild(пункт);
    }
    эл.телКопировать.disabled = ссылки.length === 0;
    if (!ссылки.length) {
      эл.телСтатус.textContent = 'Адрес не найден — проверь подключение компьютера к сети';
    } else {
      мастерПоказатьКод(эл);
    }
    эл.телАдрес.addEventListener('change', () => мастерПоказатьКод(эл));
    эл.телКопировать.addEventListener('click', () => мастерСкопироватьАдрес(эл));
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Нет связи с сервером — город и адрес можно будет выбрать позже', true);
  }
}

/* QR-код ссылки телефона в «Настройках → Телефон» — как в мастере
   (`мастерПоказатьКод`): рисует сервер, без библиотеки segno — ссылка крупно. */
function настрПоказатьКод(выбор, куда) {
  if (!выбор || !куда) return;
  const ссылка = выбор.value;
  куда.textContent = '';
  куда.classList.remove('без-кода');
  if (!ссылка) { куда.hidden = true; return; }
  const картинка = document.createElement('img');
  картинка.alt = 'QR-код ссылки для телефона';
  картинка.src = '/api/phone/qr?url=' + encodeURIComponent(ссылка);
  картинка.addEventListener('error', () => {
    куда.textContent = '';
    куда.classList.add('без-кода');
    const крупно = document.createElement('span');
    крупно.className = 'мастер-крупно';
    крупно.textContent = ссылка;
    куда.appendChild(крупно);
  });
  куда.appendChild(картинка);
  куда.hidden = false;
}

/* «Чтобы экран телефона не гас» — в «Настройках → Телефон». Держать экран
   (Wake Lock) браузер позволяет только защищённому адресу, а Труба в
   домашней сети — обычный http, и телефон засыпает по своему таймеру. Первые
   люди после выкладки (29.09) спрашивали «как на телефоне сделать https»:
   настоящий https с сертификатом тут не нужен, хватает одного из двух
   способов ниже. Адрес для флажка Chrome — ровно тот, что выбран выше. */
function телефонНеГасить(куда, выбор) {
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = 'Чтобы экран телефона не гас';
  куда.appendChild(шапка);
  const вступление = document.createElement('div');
  вступление.className = 'настр-описание';
  вступление.textContent = 'Держать экран включённым браузер разрешает только «защищённым» '
    + 'адресам (https), а Труба в домашней сети открывается по обычному http. '
    + 'Хватит любого из двух способов — настоящий https не нужен.';
  куда.appendChild(вступление);
  const шаги = document.createElement('div');
  // Своей нижней черты нет: следом «Погода на телефоне» со своей верхней, и
  // вышло бы две линии подряд (хозяин заметил 29.09).
  шаги.className = 'образец-шаги без-черты';
  const шаг = (номер, имя, куски) => {
    const ряд = document.createElement('div');
    ряд.className = 'образец-шаг';
    const кружок = document.createElement('span');
    кружок.className = 'образец-номер';
    кружок.textContent = String(номер);
    const тело = document.createElement('div');
    тело.className = 'образец-шаг-тело';
    const заголовок = document.createElement('div');
    заголовок.className = 'образец-шаг-имя';
    заголовок.textContent = имя;
    const текст = document.createElement('div');
    текст.className = 'настр-описание';
    текст.append(...куски);
    тело.append(заголовок, текст);
    ряд.append(кружок, тело);
    шаги.appendChild(ряд);
  };
  const выделить = (т) => {
    const b = document.createElement('b');
    b.className = 'не-гасить-адрес';
    b.textContent = т;
    return b;
  };
  const адрес = выделить('');
  шаг(1, 'Телефон на зарядке — «Не выключать экран»', [
    'Настройки телефона → «О телефоне» → 7 раз коснись «Номер сборки» (на Xiaomi — '
    + '«Версия MIUI» или «Версия ОС»). Появится раздел «Для разработчиков» (на Xiaomi — '
    + 'в «Расширенных настройках»): включи в нём «Не выключать экран». Пока телефон '
    + 'заряжается, экран не гаснет.']);
  шаг(2, 'Без зарядки — флажок в Chrome', [
    'В Chrome на телефоне набери в адресной строке ',
    выделить('chrome://flags/#unsafely-treat-insecure-origin-as-secure'),
    ' — такие адреса открываются только руками. В поле впиши ', адрес,
    ', справа выбери Enabled и нажми Relaunch. Экран перестанет гаснуть, а на '
    + 'телефоне появится его заряд.']);
  куда.appendChild(шаги);
  const обновить = () => {
    let начало = '';
    try { начало = new URL(выбор.value).origin; } catch (e) { начало = ''; }
    адрес.textContent = начало || 'адрес Трубы — он выше';
  };
  выбор.addEventListener('change', обновить);
  обновить();
  return обновить;
}

/* «Как открыть Трубу на весь экран» — и в шаге телефона мастера, и в
   «Настройках → Телефон», чтобы человек нашёл её позже. Без адресной строки
   браузер даёт только установленному веб-приложению, поэтому и Firefox, и
   Safari описаны честно: у Firefox есть и настоящая установка, и простой
   ярлык с «Домашнего экрана», а ярлык полноэкранность не гарантирует. */
function телефонВоВесьЭкран(куда) {
  const шапка = document.createElement('div');
  шапка.className = 'образец-заголовок';
  шапка.textContent = 'Как открыть Трубу на весь экран';
  куда.appendChild(шапка);
  const вступление = document.createElement('div');
  вступление.className = 'настр-описание';
  вступление.textContent = 'Открывай Трубу с иконки на главном экране телефона. '
    + 'Установленное веб-приложение может скрыть панели браузера, обычный ярлык — нет. '
    + 'Труба открывается по домашнему HTTP-адресу компьютера: браузер может не '
    + 'предложить установку веб-приложения, поэтому полный экран не гарантирован.';
  куда.appendChild(вступление);
  const шаги = document.createElement('div');
  шаги.className = 'образец-шаги без-черты';
  const шаг = (номер, имя, куски) => {
    const ряд = document.createElement('div');
    ряд.className = 'образец-шаг';
    const кружок = document.createElement('span');
    кружок.className = 'образец-номер';
    кружок.textContent = String(номер);
    const тело = document.createElement('div');
    тело.className = 'образец-шаг-тело';
    const заголовок = document.createElement('div');
    заголовок.className = 'образец-шаг-имя';
    заголовок.textContent = имя;
    const текст = document.createElement('div');
    текст.className = 'настр-описание';
    текст.append(...куски);
    тело.append(заголовок, текст);
    ряд.append(кружок, тело);
    шаги.appendChild(ряд);
  };
  шаг(1, 'Firefox на Android', [
    'Открой в Firefox адрес Трубы (тот, что выше), нажми меню ',
    '⋮',
    ' (три точки) и выбери «Установить» («Install») или «Добавить на главный экран». '
    + 'Открывай Трубу с появившейся иконки. ',
    // Куски склеиваются как есть: пробел в конце предыдущего — иначе
    // предложения слипаются («иконки.Firefox»).
    'Firefox может создать обычный ярлык, который откроется с панелью браузера. '
    + 'Полный экран он не гарантирует; это ограничение браузера и домашнего HTTP-адреса.']);
  шаг(2, 'iPhone (Safari или Firefox)', [
    'На iPhone полный экран у страниц не работает: кнопка «во весь экран» там '
    + 'ничего не делает, так устроен сам телефон. Панели браузера прячет только '
    + 'ярлык «На экран „Домой“» с включённой галочкой «Открывать как '
    + 'веб-приложение». ',
    'Открой адрес Трубы в Safari или в Firefox, нажми '
    + '«Поделиться» → «На экран „Домой“» → включи «Открывать как веб-приложение» '
    + 'и нажми «Добавить». Иконка откроет Трубу как приложение, без адресной строки. ',
    'Важно: ярлык добавляй со страницы, открытой по QR-коду из пульта — '
    + 'тогда он привязан к компьютеру. Ярлык из обычного адреса без ключа Трубу '
    + 'не покажет, и на экране будет написано, что ярлык не привязан.']);
  шаг(3, 'Chrome на Android', [
    'Меню ',
    '⋮',
    ' → «Установить приложение», если этот пункт есть, и открывай Трубу с иконки. '
    + '«Добавить на главный экран» может создать лишь ярлык с панелью браузера.']);
  куда.appendChild(шаги);
}

/* QR рисует сервер (`/api/phone/qr`) и отдаёт SVG. Без библиотеки segno он
   отвечает 501 — тогда показываем адрес крупно, вводить его руками не нужно. */
function мастерПоказатьКод(эл) {
  const ссылка = эл.телАдрес.value;
  эл.телКод.textContent = '';
  эл.телКод.classList.remove('без-кода');
  if (!ссылка) { эл.телКод.hidden = true; return; }
  const картинка = document.createElement('img');
  картинка.alt = 'QR-код адреса телефона';
  картинка.src = '/api/phone/qr?url=' + encodeURIComponent(ссылка);
  картинка.addEventListener('error', () => {
    if (!мастерЖива(эл)) return;
    эл.телКод.textContent = '';
    // Белая подложка нужна только коду: светлый адрес на ней не виден.
    эл.телКод.classList.add('без-кода');
    const крупно = document.createElement('span');
    крупно.className = 'мастер-крупно';
    крупно.textContent = ссылка;
    эл.телКод.appendChild(крупно);
  });
  эл.телКод.appendChild(картинка);
  эл.телКод.hidden = false;
}

async function мастерСкопироватьАдрес(эл) {
  if (!мастерЖива(эл)) return;
  const ссылка = эл.телАдрес.value;
  if (!ссылка) { мастерСтатус(эл, 'Нет адреса для копирования', true); return; }
  мастерСтатус(эл, 'Копирую…');
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(ссылка);
    } else {
      const поле = document.createElement('textarea');
      поле.value = ссылка;
      поле.setAttribute('readonly', '');
      поле.style.position = 'fixed';
      поле.style.opacity = '0';
      document.body.appendChild(поле);
      поле.select();
      const вышло = document.execCommand('copy');
      поле.remove();
      if (!вышло) throw new Error('буфер обмена недоступен');
    }
    мастерСтатус(эл, 'Адрес скопирован');
  } catch (e) {
    if (!мастерЖива(эл)) return;
    мастерСтатус(эл, 'Не вышло скопировать: '
      + ((e && e.message) ? e.message : e), true);
  }
}

async function мастерСохранитьТелефон(эл) {
  if (!мастерЖива(эл)) return '';
  /* Город уходит только если хозяин его правда выбрал: иначе сохранение
     мастера обнулило бы погоду, настроенную раньше. */
  if (эл.погЧерновик) {
    const итог = await мастерСохранить({
      weather_city: эл.погЧерновик.city,
      weather_lat: эл.погЧерновик.lat,
      weather_lon: эл.погЧерновик.lon,
    });
    if (typeof итог === 'string') return 'Не вышло сохранить город: ' + итог;
    эл.погЧерновик = null;
  }
  return '';
}

/* --- Шаг 6: готово --- */

function мастерШагГотово(тело) {
  мастерЗаголовок(тело, 'Готово');
  мастерТекст(тело, 'Скажи: «Труба, привет». Она отзывается на имя; окно разговора '
    + 'после ответа открыто несколько секунд — можно говорить без имени');
  /* Подсказка про программы: при чистой установке на телефоне только две
     кнопки (YouTube и браузер) — это стартовый список, а не предел. Сказать
     об этом прямо здесь, где человек только закончил настройку, дешевле,
     чем потом гадать, где добавить третью. */
  мастерТекст(тело, 'Пока на телефоне только две кнопки — это стартовый список. '
    + 'Свои программы и ссылки добавь в разделе «Программы»: кнопка «+ Программа», '
    + 'название можно русским, служебное имя придумаем сами.');
  const ссылки = document.createElement('div');
  ссылки.className = 'мастер-ссылки';
  тело.appendChild(ссылки);
  const строка = (текст, подсказка, раздел) => {
    const кнопка = document.createElement('button');
    кнопка.type = 'button';
    кнопка.className = 'мастер-ссылка';
    const имя = document.createElement('span');
    имя.textContent = текст;
    const стрелка = document.createElement('span');
    стрелка.textContent = подсказка;
    кнопка.append(имя, стрелка);
    кнопка.addEventListener('click', () => мастерЗакрыть(раздел));
    ссылки.appendChild(кнопка);
  };
  строка('Все команды', 'раздел «Команды»', 'команды');
  строка('Кнопки программ на телефоне', 'раздел «Программы»', 'программы');
  const статус = document.createElement('div');
  статус.className = 'мастер-подпись';
  тело.appendChild(статус);
  const эл = { тело, статус, сохранить: null };
  мастерШаги[мастерШаг] = эл;
}

const МАСТЕР_РИСОВАТЬ = {
  1: мастерШагПривет,
  2: мастерШагМозг,
  3: мастерШагХарактер,
  4: мастерШагЗвук,
  5: мастерШагТелефон,
  6: мастерШагГотово,
};

/* При загрузке пульта мастер показывается тому, у кого настройка ещё не
   сделана. У хозяина `first_run_done` уже `true`, и он его не видит никогда. */
async function спроситьПервыйЗапуск() {
  try {
    const ответ = await fetch('/api/settings', { cache: 'no-store' });
    const данные = await ответ.json();
    if (!ответ.ok || !данные || !данные.ok) return;
    const s = (данные && данные.settings) || {};
    /* Открываемся на том шаге, на котором человек остановился в прошлый раз
       (после QR это пятый), а не с первого. `first_run_done: true` — мастер
       больше не открывается: «заново» запускается только из «О программе». */
    if (s.first_run_done === false) {
      const сохранённый = Number(s.wizard_step);
      мастерОткрыть(сохранённый >= 1 && сохранённый <= МАСТЕР_ШАГОВ ? сохранённый : 1);
    }
  } catch (e) {
    // Нет связи — мастер не показываем: спросим при следующем открытии пульта.
  }
}

function характерВыбрать(готовый, поле) {
  const сейчас = поле.value;
  /* Свои правки в поле — не молча затирать: спросить. Текст, совпадающий с
     сохранённым или с самим готовым, спрашивать незачем. */
  if (сейчас.trim() && сейчас !== настрБаза.persona && сейчас !== готовый.text
      && !confirm('Заменить текст характера на «' + готовый.title + '»? Твои правки пропадут.')) {
    return;
  }
  поле.value = готовый.text || '';
  характерВыбранный = готовый.id;
  характерОтметить();
  /* Кнопка «Отменить правки» и пометка несохранённого — как при ручной правке. */
  поле.dispatchEvent(new Event('input', { bubbles: true }));
}
