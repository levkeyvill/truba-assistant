/* Подмена ответов сервера для проверки мастера в браузере.
   Подключается через `Page.addScriptToEvaluateOnNewDocument`
   (tests/browser_wizard.mjs), поэтому выполняется до скрипта пульта и после
   каждой перезагрузки: голос выключен, устройства и погода — выдуманные,
   QR отвечает 501 (как без библиотеки segno), а настройки отдаются из памяти
   с `first_run_done: false`. Живой settings.json хозяина не трогаем. */
(() => {
  window.__ошибки = [];
  window.addEventListener('error', событие => window.__ошибки.push('error: ' + событие.message));
  window.addEventListener('unhandledrejection',
    событие => window.__ошибки.push('rejection: ' + событие.reason));
  window.__отправлено = [];
  const провайдеры = {
    deepseek: { model: 'deepseek-flash', has_key: false, local: false },
    openrouter: { model: 'deepseek/deepseek-v4-flash', has_key: false, local: false },
    minimax: { model: 'MiniMax-M3', has_key: false, local: false },
    openai: { model: 'gpt-5.6-luna', has_key: false, local: false },
    local: { model: '', has_key: false, local: true },
  };
  const настройки = {
    provider: 'deepseek', first_run_done: false, persona_preset: 'friendly',
    mic_name: '', mic_channel: 0, speaker_name: '', output: 'speakers',
    weather_city: '', weather_lat: null, weather_lon: null, update_check: true,
  };
  const настоящий = window.fetch.bind(window);
  window.fetch = async (адрес, опции) => {
    const текст = String(адрес);
    if (текст === '/api/settings' && опции && опции.method === 'POST') {
      window.__отправлено.push(JSON.parse(опции.body));
      return new Response(JSON.stringify({ ok: true }), { status: 200,
        headers: { 'Content-Type': 'application/json' } });
    }
    if (текст === '/api/settings') {
      return new Response(JSON.stringify({ ok: true, provider: 'deepseek',
        providers: провайдеры, settings: настройки, persona: 'Привет',
        addresses: ['192.168.1.50'] }), { status: 200,
        headers: { 'Content-Type': 'application/json' } });
    }
    if (текст === '/api/personas') {
      return new Response(JSON.stringify({ ok: true, default: 'friendly', presets: [
        { id: 'friendly', title: 'Дружелюбная', hint: 'просто и по-человечески', text: 'Привет!' },
        { id: 'calm', title: 'Спокойная', hint: 'коротко и тихо', text: 'Да.' },
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (текст === '/api/audio') {
      return new Response(JSON.stringify({ ok: true,
        inputs: [{ name: 'Микрофон (USB)', default: true, channels: 2 }],
        outputs: [{ name: 'Колонки (USB)', default: true, channels: 2 }],
        current: { mic_name: '', mic_channel: 0, speaker_name: '', output: 'speakers' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (текст === '/api/runtime') {
      return new Response(JSON.stringify({ ok: true, voice: { running: false } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (текст.indexOf('/api/audio/level') === 0 || текст === '/api/audio/test'
        || текст.indexOf('/api/weather/find') === 0) {
      return new Response(JSON.stringify({ ok: true, level: 0.4, places: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (текст.indexOf('/api/hardware') === 0) {
      return new Response(JSON.stringify({ ok: true, hw: {
        cpu: { name: 'Тестовый процессор', cores: 8, threads: 16 },
        ram_gb: 32, gpus: [{ name: 'Тестовая видеокарта', vram_gb: 12 }] },
        recommend: { voice: 'silero', voice_why: 'Голос на процессоре — быстро и тихо.' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (текст.indexOf('/api/phone/qr') === 0) {
      return new Response('нет segno', { status: 501 });
    }
    return настоящий(адрес, опции);
  };
})();
