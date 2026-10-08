/* Главная ПК: управление отдельно от анимации, один экземпляр на вкладку. */
(() => {
  const icons = {
    sound: '<path d="M11 4 6 8H3v8h3l5 4zM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    hush: '<path d="M11 4 6 8H3v8h3l5 4zM16 9l5 6m0-6-5 6"/>',
    power: '<path d="M12 3v9m-5-7a9 9 0 1 0 10 0"/>',
    shot: '<path d="M5 3v16h16M3 5h16v16"/>',
    read: '<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
    note: '<path d="M12 4H4v16h16v-8M10 14l1-5 8-8 4 4-8 8z"/>',
    timer: '<circle cx="12" cy="14" r="8"/><path d="M9 2h6M12 2v4m0 4v5l3 2M18 5l2-2"/>',
    full: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    pin: '<path d="m16 3 5 5-4 2-3 5-2-2-6 6m2-11 5-3z"/>',
    search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  };
  const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || ''}</svg>`;
  const node = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  };
  const session = { time: 0, color: null, wave: null };
  const chrome = { wanted: false, applied: null, busy: false, timer: null, refresh: false };
  async function windowQuiet(quiet, refresh = false) {
    chrome.wanted = quiet;
    if (refresh) { chrome.applied = null; chrome.refresh = true; }
    clearTimeout(chrome.timer);
    const api = window.pywebview?.api;
    if (!api?.set_home_quiet || chrome.busy || chrome.applied === quiet) return;
    const wanted = quiet;
    chrome.busy = true;
    chrome.refresh = false;
    try {
      const result = await api.set_home_quiet(wanted);
      if (result?.ok && !result.deferred) chrome.applied = wanted;
      else if (result?.deferred && chrome.wanted === wanted) {
        chrome.timer = setTimeout(() => windowQuiet(chrome.wanted), 250);
      }
    } catch (error) { /* Рамка остаётся штатной при недоступном нативном мосте. */ }
    finally {
      chrome.busy = false;
      if (chrome.refresh) { chrome.applied = null; windowQuiet(chrome.wanted); }
      else if (chrome.wanted !== wanted) windowQuiet(chrome.wanted);
    }
  }
  class TrubaHome {
    constructor(root, actions) {
      this.root = root;
      this.actions = actions;
      this.voice = null;
      this.phase = 'off';
      this.pinned = false;
      this.pointerInside = false;
      this.pointerPosition = null;
      this.disposed = false;
      this.frame = 0;
      this.time = session.time;
      this.controller = new AbortController();
      const signal = this.controller.signal;
      this.reduced = matchMedia('(prefers-reduced-motion: reduce)');
      root.className = 'лист home';
      root.innerHTML = `<div class="home-stage"><canvas class="home-wave" aria-hidden="true"></canvas></div>
        <div class="home-toolbar home-chrome" role="toolbar" aria-label="Управление Трубой">
          <label class="home-volume">${icon('sound')}<span>Громкость</span><input type="range" min="1" max="10" step="1" aria-label="Громкость голоса"><output></output></label>
          <span class="home-divider"></span>
          <button type="button" data-home="mute">Не слушать</button>
          <button type="button" data-home="hush">${icon('hush')}<span>Замолчать</span></button>
          <button type="button" data-home="toggle">${icon('power')}<span>Включить</span></button>
        </div>
        <div class="home-view home-chrome">
          <button type="button" data-home="pin" title="Закрепить управление" aria-label="Закрепить управление" aria-pressed="false">${icon('pin')}</button>
          <button type="button" data-home="full" title="На весь экран" aria-label="На весь экран">${icon('full')}</button>
        </div>
        <div class="home-controls"><div class="home-center">
          <h2 class="home-title">Проверяю…</h2><p class="home-subtitle"></p>
          <div class="home-modes home-chrome" role="group" aria-label="Режим слуха">
            <button type="button" data-mode="name">По имени</button>
            <button type="button" data-mode="always">Всегда</button>
            <button type="button" data-mode="off">По кнопке</button>
          </div>
          <button type="button" class="home-listen home-chrome" data-home="listen" hidden>Начать разговор</button>
          <div class="home-progress" hidden></div>
        </div>
        <div class="home-bottom home-chrome">
          <div class="home-quick" role="group" aria-label="Быстрые действия">
            <button type="button" data-home="screenshot">${icon('shot')}Скриншот</button>
            <button type="button" data-home="clipboard">${icon('read')}Прочитать буфер</button>
            <button type="button" data-home="note">${icon('note')}Новая заметка</button>
            <button type="button" data-home="timer">${icon('timer')}Таймер</button>
          </div>
          <form class="home-search" aria-label="Поиск в интернете">
            ${icon('search')}<input type="search" maxlength="1000" placeholder="Что найти?" aria-label="Поисковый запрос" autocomplete="off">
            <button type="submit" data-home="search">Открыть поиск</button>
            <button type="button" class="home-answer" data-home="answer">Найти ответ</button>
          </form>
          <div class="home-reminder" hidden></div>
        </div></div>
        <p class="home-message" role="status" aria-live="polite"></p>
        <dialog class="home-timer"><form method="dialog"><h2>Новый таймер</h2><label>Через сколько минут<input type="number" min="0.02" max="1440" step="any" value="5" required></label><div><button value="cancel">Отмена</button><button type="submit" value="start" class="home-answer">Поставить</button></div></form></dialog>`;
      this.canvas = root.querySelector('canvas');
      this.context = this.canvas.getContext('2d');
      this.volume = root.querySelector('[type=range]');
      this.query = root.querySelector('[type=search]');
      this.message = root.querySelector('.home-message');
      this.dialog = root.querySelector('dialog');
      this.phaseColors = {
        off: [110, 123, 144], loading: [217, 139, 82], thinking: [217, 139, 82],
        listening: [107, 138, 253], hearing: [107, 138, 253], speaking: [184, 184, 232],
      };
      this.lightColors = {
        off: [80, 89, 108], loading: [145, 77, 30], thinking: [145, 77, 30],
        listening: [47, 76, 187], hearing: [47, 76, 187], speaking: [70, 62, 151],
      };
      this.wave = session.wave || new TrubaWave(session.color || this.phaseColors.off);
      this.resize = new ResizeObserver(() => this.size());
      this.resize.observe(this.canvas);
      this.resize.observe(root.querySelector('.home-controls'));
      document.addEventListener('pointermove', (event) => this.pointerMove(event), { signal, passive: true });
      document.addEventListener('keydown', () => this.reveal(), { signal, capture: true });
      document.addEventListener('focusin', () => this.reveal(), { signal });
      window.addEventListener('blur', () => { this.pointerInside = false; this.hide(true); }, { signal });
      document.documentElement.addEventListener('pointerleave', () => this.pointerLeave(), { signal });
      window.addEventListener('focus', () => {
        this.pointerInside = document.documentElement.matches(':hover'); this.reveal();
      }, { signal });
      window.addEventListener('pywebviewready', () => windowQuiet(document.body.classList.contains('home-quiet')), { signal });
      document.addEventListener('visibilitychange', () => this.animate(), { signal });
      this.reduced.addEventListener('change', () => this.animate(), { signal });
      root.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-home], button[data-mode]');
        if (!button) return;
        if (button.dataset.mode) this.run('mode', { value: button.dataset.mode });
        else this.click(button.dataset.home);
      }, { signal });
      root.querySelector('form.home-search').addEventListener('submit', (event) => {
        event.preventDefault(); this.search(false);
      }, { signal });
      this.volume.addEventListener('input', () => this.volumeLabel(), { signal });
      this.volume.addEventListener('change', () => this.run('volume', { level: Number(this.volume.value) }), { signal });
      this.dialog.addEventListener('close', () => {
        if (this.dialog.returnValue === 'start') {
          this.run('timer', { seconds: Math.round(Number(this.dialog.querySelector('input').value) * 60) });
        }
        this.reveal();
      }, { signal });
      this.reveal();
      this.animate();
      this.pollVisual();
    }
    volumeLabel() {
      this.root.querySelector('output').textContent = `${this.volume.value} / 10`;
    }
    async click(kind) {
      if (kind === 'search') return;
      if (kind === 'answer') return this.search(true);
      if (kind === 'pin') {
        this.pinned = !this.pinned;
        const button = this.root.querySelector('[data-home=pin]');
        button.setAttribute('aria-pressed', String(this.pinned));
        button.title = this.pinned ? 'Скрывать управление автоматически' : 'Закрепить управление';
        this.reveal(); return;
      }
      if (kind === 'full') {
        try {
          if (window.pywebview?.api?.toggle_fullscreen) {
            const result = await window.pywebview.api.toggle_fullscreen();
            if (result?.ok === false) throw new Error(result.error);
          }
          else if (document.fullscreenElement) await document.exitFullscreen();
          else await document.documentElement.requestFullscreen();
        } catch (error) { this.notice('Полный экран недоступен в этом окне.', true); }
        return;
      }
      if (kind === 'timer') { this.dialog.returnValue = ''; this.dialog.showModal(); return; }
      if (kind === 'mute') return this.run('mode', { value: this.voice?.mode === 'off' ? 'name' : 'off' });
      return this.run(kind);
    }
    async search(research) {
      const query = this.query.value.trim();
      if (!query) { this.query.focus(); return; }
      if (research) { this.actions.research(query); return; }
      await this.run('search', { query });
    }
    async run(kind, data = {}) {
      if (this.busy) return;
      this.busy = true;
      this.reveal();
      try {
        const result = await this.actions.run(kind, data);
        if (result && !result.ok) throw new Error(result.error || result.text || 'Действие не выполнено.');
        if (result?.voice) this.update(result.voice);
        if (result?.text) this.notice(result.text);
      } catch (error) { this.notice(error.message, true); }
      finally { this.busy = false; this.reveal(); }
    }
    notice(text, bad = false) {
      if (this.disposed) return;
      this.message.textContent = text;
      this.message.classList.toggle('error', bad);
      clearTimeout(this.messageTimer);
      this.messageTimer = setTimeout(() => { this.message.textContent = ''; }, bad ? 12000 : 5000);
    }
    update(voice, state, events = [], reminders = null) {
      if (this.disposed) return;
      if (voice) this.voice = voice;
      const v = this.voice;
      this.phase = TrubaWave.phase(v);
      this.root.dataset.phase = this.phase;
      const labels = { off: 'Труба отдыхает', loading: 'Загружаюсь', thinking: 'Думаю', speaking: 'Говорю', hearing: 'Слушаю', listening: 'Слушаю' };
      const title = this.root.querySelector('.home-title');
      const label = v?.ready && v.mode === 'off' && !v.in_conversation ? 'Жду нажатия' : labels[this.phase] || 'Слушаю';
      if (title.textContent !== label) title.textContent = label;
      const uiKey = JSON.stringify([v?.running, v?.ready, v?.mode, v?.in_conversation, v?.volume, v?.stopping, v?.loading_text, label]);
      if (uiKey === this.uiKey && !state && !reminders) return;
      this.uiKey = uiKey;
      this.root.querySelector('[data-home=listen]').hidden = !v?.ready || v.mode !== 'off';
      this.root.querySelector('.home-subtitle').textContent = !v ? 'Соединяюсь с Трубой' : !v.running ? 'Включи голос, когда захочешь поговорить' :
        v.mode === 'off' ? 'Слух выключен · включи его кнопкой' : v.in_conversation ? 'Можно говорить без имени' : v.mode === 'name' ? 'Обращайся по имени' : 'Готова к разговору';
      const progress = this.root.querySelector('.home-progress');
      progress.hidden = !v?.loading_text;
      progress.textContent = v?.loading_text || '';
      const toggle = this.root.querySelector('[data-home=toggle]');
      toggle.querySelector('span').textContent = v?.stopping ? 'Выключаю…' : v?.running ? 'Выключить' : 'Включить';
      toggle.disabled = !v || !!v.stopping;
      this.root.querySelector('[data-home=hush]').disabled = !v?.running;
      this.root.querySelector('[data-home=mute]').textContent = v?.mode === 'off' ? 'Слушать' : 'Не слушать';
      this.root.querySelectorAll('[data-mode]').forEach((button) => {
        button.classList.toggle('selected', button.dataset.mode === v?.mode);
        button.setAttribute('aria-pressed', String(button.dataset.mode === v?.mode));
        button.disabled = !v;
      });
      if (document.activeElement !== this.volume) this.volume.value = String(v?.volume ?? 10);
      this.volumeLabel();
      if (reminders) {
        const reminder = this.root.querySelector('.home-reminder');
        reminder.hidden = !reminders.length;
        reminder.replaceChildren();
        if (reminders.length) {
          const due = new Date(reminders[0].due);
          const when = Number.isNaN(due.getTime()) ? '' : due.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) + ' · ';
          reminder.append(node('span', '', 'Ближайшее напоминание'), node('strong', '', when + (reminders[0].text || 'Таймер')));
        }
      }
      this.fitWave();
      if (state) this.root.classList.toggle('phone-online', !!state.телефон?.хорошо);
    }
    pointerMove(event) {
      const position = [event.screenX, event.screenY];
      // Перерасчёт рамки даёт события на прежнем месте курсора: это не новое наведение.
      if (this.pointerPosition && position.every((value, i) => value === this.pointerPosition[i])) return;
      this.pointerPosition = position;
      this.pointerInside = true;
      this.reveal();
    }
    pointerLeave() {
      this.pointerInside = false;
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => this.hide(), 200);
    }
    reveal() {
      if (this.disposed) return;
      document.body.classList.remove('home-quiet');
      windowQuiet(false);
      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => this.hide(), 3200);
    }
    hide(force = false) {
      if (this.pinned || this.busy || this.dialog.open || !force && this.pointerInside || !force && this.root.contains(document.activeElement) &&
          ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
      document.body.classList.add('home-quiet');
      windowQuiet(true);
    }
    async pollVisual() {
      if (this.disposed) return;
      try {
        if (!document.hidden && this.actions.visual) {
          const voice = await this.actions.visual(this.controller.signal);
          if (!this.disposed && voice) this.update(voice);
        }
      } catch (error) { /* Общее состояние соединения показывает опрос пульта. */ }
      if (!this.disposed) this.visualTimer = setTimeout(() => this.pollVisual(), 60);
    }
    size() {
      const rect = this.canvas.getBoundingClientRect();
      const viewportChanged = this.width !== rect.width || this.height !== rect.height;
      this.width = rect.width; this.height = rect.height;
      if (viewportChanged) windowQuiet(document.body.classList.contains('home-quiet'), true);
      this.fitWave();
      const dpr = Math.min(devicePixelRatio || 1, 1.5);
      const width = Math.round(rect.width * dpr), height = Math.round(rect.height * dpr);
      if (width !== this.canvas.width || height !== this.canvas.height) {
        this.canvas.width = width; this.canvas.height = height;
        this.context?.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.draw(this.time);
      }
    }
    fitWave() {
      const canvas = this.canvas.getBoundingClientRect();
      const title = this.root.querySelector('.home-center').getBoundingClientRect();
      const radius = Math.min(canvas.width * .29, canvas.height * .27, 350);
      const edge = title.top - canvas.top - 24;
      this.wave.centerY = Math.min(canvas.height * .40, Math.max(canvas.height * .30, edge - radius * .9));
      this.wave.maxHalfHeight = Math.max(40, edge - this.wave.centerY);
    }
    animate() {
      cancelAnimationFrame(this.frame);
      if (this.disposed || document.hidden) return;
      const interval = 1000 / (this.reduced.matches ? 30 : 60);
      let previous = null, next = null;
      const step = (stamp) => {
        if (this.disposed || document.hidden) return;
        if (next === null || stamp >= next - .5) {
          const dt = previous === null ? 0 : Math.min(.1, (stamp - previous) / 1000);
          previous = stamp;
          // Сохраняем срок следующего кадра: округление rAF не снижает частоту до 20.
          next = next === null || stamp - next > interval ? stamp + interval : next + interval;
          if (!this.reduced.matches) this.time += dt;
          const palette = document.documentElement.dataset.theme === 'light' ? this.lightColors : this.phaseColors;
          this.wave.advance(dt, this.voice, palette[this.phase] || palette.listening);
          this.draw(this.time);
        }
        this.frame = requestAnimationFrame(step);
      };
      this.frame = requestAnimationFrame(step);
    }
    draw(t) {
      this.root.dataset.motion = this.reduced.matches ? 'reduced' : 'full';
      this.root.dataset.level = this.wave.glow.toFixed(2);
      this.root.dataset.audio = this.wave.drive.toFixed(2);
      this.wave.paint(this.context, t, this.width, this.height, this.reduced.matches,
        document.documentElement.dataset.theme === 'light');
    }
    dispose() {
      this.disposed = true;
      session.time = this.time; session.color = this.wave.color.slice();
      session.wave = this.wave;
      clearTimeout(this.visualTimer);
      this.controller.abort(); this.resize.disconnect();
      cancelAnimationFrame(this.frame);
      clearTimeout(this.hideTimer); clearTimeout(this.messageTimer);
      this.dialog.close();
      document.body.classList.remove('home-quiet');
      windowQuiet(false);
    }
  }
  window.TrubaHome = TrubaHome;
})();
