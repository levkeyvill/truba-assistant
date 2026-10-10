/* Объёмные ленты текут под голос; фаза накапливается без скачков.

   Вид настраивается в «Настройки → Главная» (`TrubaWave.look`): фигура
   (кольцо или куб), круглость кольца, отклик на голос, яркость точек,
   мерцание, скорость вращения, «прыжки» точек под голос, цвет, перелив
   градиентом и живой фон (дымка и редкая пыль по окну).

   Точки рисуются пачками по яркости и размеру, а не по одной: главная
   крутится всё время, и тысяча отдельных заливок на кадр — лишняя нагрузка.
   Яркость пачки задаёт `globalAlpha`, цвет — сплошной или градиент. */
((scope) => {
  const tau = Math.PI * 2;
  const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
  const ease = (value, target, dt, seconds) => value + (target - value) * -Math.expm1(-dt / seconds);
  const mix = (a, b, f) => a.map((value, i) => value + (b[i] - value) * f);
  // Ширина кольца относительно радиуса; высота — она же, умноженная на круглость.
  const RX = 1.23;
  // Ступени мерцания и глубины: столько отдельных заливок на ленту.
  const TWINKLE_STEPS = 4;
  const DEPTH_STEPS = 2;
  /* Палитры фигуры: цвет, когда она слушает, когда говорит, и второй цвет
     перелива. Состояния «думаю», «загружаюсь» (янтарный) и «выключена»
     (серый) у всех одни — по ним видно, что происходит. Зелёного нет:
     он в пульте только у индикаторов «работает». */
  const PALETTES = Object.freeze({
    blue: {listening: [107, 138, 253], speaking: [184, 184, 232], second: [168, 120, 245],
      light: {listening: [47, 76, 187], speaking: [70, 62, 151], second: [104, 58, 170]}},
    violet: {listening: [150, 112, 240], speaking: [206, 182, 250], second: [226, 112, 196],
      light: {listening: [96, 58, 176], speaking: [118, 80, 170], second: [150, 50, 120]}},
    sky: {listening: [92, 186, 240], speaking: [172, 224, 250], second: [107, 138, 253],
      light: {listening: [24, 104, 160], speaking: [40, 92, 150], second: [47, 76, 187]}},
    pink: {listening: [236, 112, 172], speaking: [250, 182, 214], second: [160, 120, 245],
      light: {listening: [166, 46, 104], speaking: [150, 60, 110], second: [100, 60, 170]}},
    silver: {listening: [190, 200, 216], speaking: [236, 240, 250], second: [120, 160, 240],
      light: {listening: [72, 82, 100], speaking: [60, 66, 84], second: [47, 76, 187]}},
  });
  const LOOK = Object.freeze({shape: 'ring', roundness: .70, reaction: .80, brightness: 1, shimmer: true,
    spin: .70, jumps: .60, color: 'blue', gradient: false, backdrop: true});
  // Насколько высоко подпрыгивает точка при полной громкости: доля радиуса.
  const JUMP_OUT = .06, JUMP_UP = .05;
  // Резкий подскок и мягкое возвращение: степень у положительной полуволны.
  const hop = (phase) => { const v = Math.max(0, Math.sin(phase)); return v * v * v; };
  // Пылинки живого фона: место, скорость и мерцание — постоянные на запуск.
  const DUST = 90;
  const number = (value, min, max, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
  };
  // Рёбра куба: пары вершин с одной отличающейся координатой.
  const cubePoints = () => {
    const points = [];
    const corners = [];
    for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) corners.push([x, y, z]);
    for (let i = 0; i < corners.length; i++) for (let j = i + 1; j < corners.length; j++) {
      const a = corners[i], b = corners[j];
      if (a.filter((v, k) => v !== b[k]).length !== 1) continue;
      for (let n = 0; n <= 36; n++) {
        const f = n / 36;
        points.push({x: a[0] + (b[0] - a[0]) * f, y: a[1] + (b[1] - a[1]) * f, z: a[2] + (b[2] - a[2]) * f,
          edge: true, tw: (i * 7 + j * 13 + n * 5) % 17 / 17 * tau, jf: 1.6 + (i * 3 + n) % 7 / 7 * 1.2});
      }
    }
    // Грани — редкой сеткой и тусклее рёбер: объём без каши из точек.
    for (let axis = 0; axis < 3; axis++) for (const side of [-1, 1]) {
      for (let u = 1; u < 8; u++) for (let v = 1; v < 8; v++) {
        const p = [0, 0, 0];
        p[axis] = side; p[(axis + 1) % 3] = u / 4 - 1; p[(axis + 2) % 3] = v / 4 - 1;
        points.push({x: p[0], y: p[1], z: p[2], edge: false, tw: (u * 11 + v * 7 + axis * 3) % 19 / 19 * tau,
          jf: 1.6 + (u * 5 + v + axis) % 9 / 9 * 1.2});
      }
    }
    return points;
  };
  class TrubaWave {
    constructor(color, look) {
      this.color = color.slice();
      this.drive = 0;
      // Громкость с быстрым откликом: по ней прыгают точки, а плавная
      // `drive` ведёт волну по фигуре.
      this.pulse = 0;
      this.glow = 0;
      // Насколько фигура «в своём цвете»: 1, когда слушает или говорит; в
      // остальных состояниях перелив гаснет вместе с цветом.
      this.tint = 0;
      this.audioPhase = 0;
      this.position = [0, 0];
      this.maxHalfHeight = Infinity;
      this.look = TrubaWave.look(look);
      this.bands = Array.from({ length: 18 }, (_, band) => {
        const v = (band - 8.5) / 8.5;
        return {v, weight: 1 - Math.abs(v), points: Array.from({ length: 140 }, (_, i) => {
          const a = i / 140 * tau;
          return {c: Math.cos(a), s: Math.sin(a), s4: Math.sin(a * 4 + v * 2.8),
            c4: Math.cos(a * 4 + v * 2.8), s3: Math.sin(a * 3), c3: Math.cos(a * 3),
            sy: Math.sin(a * 3 + v * 2), cy: Math.cos(a * 3 + v * 2),
            tw: ((band * 37 + i * 61) % 97) / 97 * tau, tf: .6 + ((band * 13 + i * 29) % 31) / 31 * .9,
            jf: 1.6 + ((band * 7 + i * 3) % 11) / 11 * 1.2};
        })};
      });
      this.sparks = Array.from({length: 260}, (_, i) => ({
        c: Math.cos(i * 2.39996), s: Math.sin(i * 2.39996), r: .65 + (i % 57) / 57 * .9,
      }));
      this.cube = cubePoints();
      this.dust = Array.from({length: DUST}, (_, i) => ({
        x: (i * .618034) % 1, y: (i * .754877) % 1, rise: .004 + (i % 7) / 7 * .010,
        sway: (i % 5) / 5 * tau, r: .5 + (i % 4) * .28, tw: (i * 2.39996) % tau,
      }));
    }
    /* Вид из настроек: проценты из пульта (`home_roundness: 70`) или доли. */
    static look(raw) {
      const src = raw || {};
      const share = (value, min, max, fallback) => {
        const n = Number(value);
        return number(Number.isFinite(n) && n > 2 ? n / 100 : n, min, max, fallback);
      };
      const flag = (value, fallback) => value === undefined || value === null ? fallback : !!value;
      return {
        shape: src.shape === 'cube' ? 'cube' : 'ring',
        roundness: share(src.roundness, .52, 1, LOOK.roundness),
        reaction: share(src.reaction, .2, 1.5, LOOK.reaction),
        brightness: share(src.brightness, .6, 1.6, LOOK.brightness),
        shimmer: flag(src.shimmer, LOOK.shimmer),
        spin: share(src.spin, .2, 1.5, LOOK.spin),
        jumps: share(src.jumps, 0, 1, LOOK.jumps),
        color: Object.prototype.hasOwnProperty.call(PALETTES, src.color) ? src.color : LOOK.color,
        gradient: flag(src.gradient, LOOK.gradient),
        backdrop: flag(src.backdrop, LOOK.backdrop),
      };
    }
    setLook(look) { this.look = TrubaWave.look(look); }
    /* Цвета состояний для выбранной палитры: {listening, hearing, speaking}.
       Остальные состояния главная берёт из своей общей таблицы. */
    static colors(look, light = false) {
      const palette = PALETTES[TrubaWave.look(look).color];
      const set = light ? palette.light : palette;
      return {listening: set.listening, hearing: set.listening, speaking: set.speaking};
    }
    static phase(voice) {
      const phase = voice?.activity || (!voice?.running ? 'off' : !voice.ready ? 'loading' : 'listening');
      return phase === 'hearing' && !voice?.in_conversation ? 'listening' : phase;
    }
    static audioTarget(voice) {
      const phase = TrubaWave.phase(voice);
      if (phase === 'speaking') return clamp(Math.sqrt(clamp(voice?.output_level) / .09));
      if (phase === 'hearing') return clamp((clamp(voice?.input_level) - .025) / .8);
      return 0;
    }
    static glowTarget(voice) {
      const phase = TrubaWave.phase(voice);
      return (phase === 'off' || phase === 'loading' ? 0 : .10) + .65 * TrubaWave.audioTarget(voice);
    }
    advance(dt, voice, targetColor) {
      dt = Math.max(0, Math.min(.1, dt));
      const target = clamp(TrubaWave.audioTarget(voice) * this.look.reaction);
      this.drive = ease(this.drive, target, dt, target > this.drive ? .10 : .28);
      this.pulse = ease(this.pulse, target, dt, target > this.pulse ? .05 : .16);
      this.glow = ease(this.glow, TrubaWave.glowTarget(voice), dt, .18);
      const own = ['listening', 'hearing', 'speaking'].includes(TrubaWave.phase(voice)) ? 1 : 0;
      this.tint = ease(this.tint, own, dt, .6);
      this.audioPhase = (this.audioPhase + dt * this.drive * 1.45) % (tau * 20);
      this.color = this.color.map((value, i) => ease(value, targetColor[i], dt, .6));
    }
    geometry(t, width, height, reduced = false) {
      if (reduced) t = 0;
      const phase = reduced ? 0 : this.audioPhase, drive = reduced ? 0 : this.drive;
      const ry = RX * this.look.roundness;
      // Скорость вращения из настроек меняет только ход времени фигуры;
      // прыжки точек идут по своим часам `jt` и от неё не зависят.
      const jt = t;
      t *= this.look.spin;
      // Запас под максимальную волну удерживает ленты над подписью при любой
      // громкости и круглости: высота кольца растёт с `ry`.
      return {radius: Math.min(width * .29, height * .27, 350, this.maxHalfHeight / (1.15 * ry + .17)),
        cx: width / 2, ry,
        cy: Number.isFinite(this.centerY) ? this.centerY : height * .40,
        radial: .055 + drive * .065, secondary: .028 + drive * .025, vertical: .045 + drive * .075,
        s: Math.sin(t * .16 + phase), c: Math.cos(t * .16 + phase),
        s2: Math.sin(t * .09 + phase * .65), c2: Math.cos(t * .09 + phase * .65),
        sy: Math.sin(t * .12 + phase * .8), cyWave: Math.cos(t * .12 + phase * .8),
        sr: Math.sin(t * .008), cr: Math.cos(t * .008), t, jt,
        jump: reduced ? 0 : this.look.jumps * this.pulse};
    }
    point(point, band, geometry, position = []) {
      const g = geometry;
      const ripple = (point.s4 * g.c + point.c4 * g.s) * g.radial + (point.c3 * g.c2 + point.s3 * g.s2) * g.secondary;
      const up = g.jump ? g.jump * hop(g.jt * point.jf * 2.4 + point.tw) : 0;
      const r = g.radius * (.80 + band.v * .21 + ripple + up * JUMP_OUT);
      position[0] = g.cx + point.c * r * RX;
      position[1] = g.cy + point.s * r * g.ry + (point.sy * g.cyWave + point.cy * g.sy) * g.radius * g.vertical
        - up * g.radius * JUMP_UP;
      return position;
    }
    /* Чем заливать точки: сплошной цвет или перелив через всю фигуру. */
    ink(ctx, g, light) {
      const rgb = this.color.map(Math.round).join(',');
      if (!this.look.gradient || !ctx.createLinearGradient) return `rgb(${rgb})`;
      const palette = PALETTES[this.look.color];
      const second = mix(this.color, (light ? palette.light : palette).second, this.tint * .85);
      const span = g.radius * 1.35;
      const angle = g.t * .05;
      const dx = Math.cos(angle) * span, dy = Math.sin(angle) * span * .6;
      const fill = ctx.createLinearGradient(g.cx - dx, g.cy - dy, g.cx + dx, g.cy + dy);
      fill.addColorStop(0, `rgb(${rgb})`);
      fill.addColorStop(1, `rgb(${second.map(Math.round).join(',')})`);
      return fill;
    }
    paint(ctx, t, width, height, reduced = false, light = false) {
      if (!ctx || !width || !height) return;
      const g = this.geometry(t, width, height, reduced);
      const rgb = this.color.map(Math.round).join(',');
      const bright = this.look.brightness;
      ctx.clearRect(0, 0, width, height);
      if (this.look.backdrop) this.paintBackdrop(ctx, g, rgb, width, height, light, reduced);
      const glow = ctx.createRadialGradient(g.cx, g.cy, g.radius * .2, g.cx, g.cy, g.radius * 1.6);
      glow.addColorStop(0, `rgba(${rgb},.008)`);
      glow.addColorStop(.5, `rgba(${rgb},${(.022 + this.glow * .016) * bright})`);
      glow.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = glow;
      ctx.fillRect(g.cx - g.radius * 1.6, g.cy - g.radius * 1.6, g.radius * 3.2, g.radius * 3.2);
      ctx.fillStyle = this.ink(ctx, g, light);
      if (this.look.shape === 'cube') this.paintCube(ctx, g, light, reduced);
      else this.paintRing(ctx, g, light, reduced);
      ctx.globalAlpha = Math.min(.6, ((light ? .25 : .14) + this.glow * .08) * bright);
      ctx.beginPath();
      for (const point of this.sparks) {
        const r = g.radius * point.r;
        const x = g.cx + (point.c * g.cr - point.s * g.sr) * r * 1.2;
        const y = g.cy + (point.s * g.cr + point.c * g.sr) * r * 1.2 * this.look.roundness * 1.07;
        ctx.moveTo(x + .6, y); ctx.arc(x, y, .6, 0, tau);
      }
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    /* Живой фон: дымка цвета фигуры по всему окну и редкие пылинки, которые
       медленно плывут вверх и еле заметно мерцают. Без мерцания и при
       «меньше движения» пыль стоит на месте. */
    paintBackdrop(ctx, g, rgb, width, height, light, reduced) {
      const haze = ctx.createRadialGradient(g.cx, g.cy, 0, g.cx, g.cy, Math.max(width, height) * .75);
      haze.addColorStop(0, `rgba(${rgb},${light ? .05 : .045})`);
      haze.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = haze;
      ctx.fillRect(0, 0, width, height);
      const t = reduced ? 0 : g.jt;
      const twinkle = this.look.shimmer && !reduced;
      ctx.fillStyle = `rgb(${rgb})`;
      for (const level of [0, 1]) {
        ctx.globalAlpha = (light ? .16 : .12) * (level ? 1.6 : 1) * this.look.brightness;
        ctx.beginPath();
        for (const p of this.dust) {
          const on = twinkle ? (Math.sin(t * .7 + p.tw) > .35 ? 1 : 0) : (p.r > .9 ? 1 : 0);
          if (on !== level) continue;
          const x = ((p.x + Math.sin(t * .08 + p.sway) * .01) % 1 + 1) % 1 * width;
          const y = ((p.y - t * p.rise) % 1 + 1) % 1 * height;
          ctx.moveTo(x + p.r, y); ctx.arc(x, y, p.r, 0, tau);
        }
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    /* Кольцо. С мерцанием точки еле заметно дышат яркостью, а передний край
       кольца (нижняя половина) чуть крупнее и ярче заднего — объём без
       свечения. Без мерцания — ровные точки. Цвет уже выставлен в `paint`. */
    paintRing(ctx, g, light, reduced) {
      const bright = this.look.brightness, shimmer = this.look.shimmer && !reduced;
      const size = 1 + (bright - 1) * .3;
      const buckets = TWINKLE_STEPS * DEPTH_STEPS;
      for (const band of this.bands) {
        const base = light ? .42 + band.weight * (.36 + this.glow * .16)
          : .18 + band.weight * (.43 + this.glow * .18);
        const dot = ((light ? .78 : .65) + band.weight * .38) * size;
        if (!shimmer) {
          ctx.globalAlpha = Math.min(.95, base * bright);
          ctx.beginPath();
          for (const point of band.points) {
            const [x, y] = this.point(point, band, g, this.position);
            ctx.moveTo(x + dot, y); ctx.arc(x, y, dot, 0, tau);
          }
          ctx.fill();
          continue;
        }
        const paths = Array.from({length: buckets}, () => []);
        for (const point of band.points) {
          const [x, y] = this.point(point, band, g, this.position);
          const tw = (Math.sin(g.t * point.tf + point.tw) + 1) / 2;
          const k = Math.min(TWINKLE_STEPS - 1, Math.floor(tw * TWINKLE_STEPS));
          const front = point.s > 0 ? 1 : 0;
          paths[front * TWINKLE_STEPS + k].push(x, y);
        }
        for (let i = 0; i < buckets; i++) {
          const list = paths[i];
          if (!list.length) continue;
          const front = i >= TWINKLE_STEPS ? 1 : 0, k = i % TWINKLE_STEPS;
          const alpha = base * bright * (.86 + .14 * k / (TWINKLE_STEPS - 1)) * (front ? 1.12 : .92);
          const r = dot * (front ? 1.1 : .94);
          ctx.globalAlpha = Math.min(.95, alpha);
          ctx.beginPath();
          for (let j = 0; j < list.length; j += 2) {
            ctx.moveTo(list[j] + r, list[j + 1]); ctx.arc(list[j], list[j + 1], r, 0, tau);
          }
          ctx.fill();
        }
      }
    }
    /* Куб из точек: вращается спокойно, под голос быстрее и «дышит» волной
       по граням. Ближние точки крупнее и ярче дальних. */
    paintCube(ctx, g, light, reduced) {
      const bright = this.look.brightness, shimmer = this.look.shimmer && !reduced;
      const t = g.t, phase = reduced ? 0 : this.audioPhase, drive = reduced ? 0 : this.drive;
      // При наклоне до 0.62 рад угол куба уходит по высоте на 1.7 стороны,
      // перспектива (×1.28) и «дыхание» (×1.06) — ещё немного: запас 2.35.
      const side = Math.min(g.radius * .62, this.maxHalfHeight / 2.35);
      // Оборот примерно за минуту при скорости 70%, под голос — чуть быстрее.
      // Скорость задаётся в настройках (`spin`).
      const spin = this.look.spin;
      const ay = t * .14 + phase * .18 * spin, ax = .5 + .10 * Math.sin(t * .13);
      const sy = Math.sin(ay), cy = Math.cos(ay), sx = Math.sin(ax), cx = Math.cos(ax);
      const size = 1 + (bright - 1) * .3;
      const steps = 4;
      const paths = Array.from({length: steps * 2}, () => []);
      for (const p of this.cube) {
        const up = g.jump ? g.jump * hop(g.jt * p.jf * 2.4 + p.tw) : 0;
        const wave = (1 + drive * .06 * Math.sin(4 * (p.x + p.y + p.z) + phase * 3)) * (1 + up * .08);
        const x0 = p.x * wave, y0 = p.y * wave, z0 = p.z * wave;
        const x1 = x0 * cy + z0 * sy, z1 = -x0 * sy + z0 * cy;
        const y2 = y0 * cx - z1 * sx, z2 = y0 * sx + z1 * cx;
        const persp = 8 / (8 - z2);
        const depth = Math.min(steps - 1, Math.floor((z2 + 1.8) / 3.6 * steps));
        const tw = shimmer ? (Math.sin(t * 1.1 + p.tw) > 0 ? 1 : 0) : 1;
        paths[(p.edge ? steps : 0) + Math.max(0, depth)].push(
          g.cx + x1 * side * persp, g.cy + y2 * side * persp - up * side * .12, tw);
      }
      for (let i = 0; i < paths.length; i++) {
        const list = paths[i];
        if (!list.length) continue;
        const edge = i >= steps, depth = i % steps;
        const near = (depth + 1) / steps;
        const base = (light ? .30 : .14) + (edge ? (light ? .45 : .5) : .12) * near + this.glow * .15;
        const r = ((edge ? 1.05 : .75) + near * .45) * size;
        for (const on of [0, 1]) {
          ctx.globalAlpha = Math.min(.95, base * bright * (on ? 1 : (shimmer ? .82 : 1)));
          ctx.beginPath();
          for (let j = 0; j < list.length; j += 3) {
            if (list[j + 2] !== on) continue;
            ctx.moveTo(list[j] + r, list[j + 1]); ctx.arc(list[j], list[j + 1], r, 0, tau);
          }
          ctx.fill();
        }
      }
    }
  }
  TrubaWave.LOOK = LOOK;
  TrubaWave.PALETTES = PALETTES;
  if (typeof module !== 'undefined' && module.exports) module.exports = TrubaWave;
  else scope.TrubaWave = TrubaWave;
})(typeof window === 'undefined' ? globalThis : window);
