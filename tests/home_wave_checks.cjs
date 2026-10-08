const assert = require('node:assert/strict');
const Wave = require('../ui/web/home_wave.js');
const fs = require('node:fs');
const vm = require('node:vm');
const color = [107, 138, 253];
const voice = (activity, in_conversation = true, level = 1) => ({
  running: true, ready: true, activity, in_conversation, input_level: level, output_level: level,
});
let passed = 0;
function check(name, test) { test(); passed++; console.log('OK ' + name); }
check('room speech has the same target as silent waiting', () => {
  assert.equal(Wave.phase(voice('hearing', false)), 'listening');
  assert.equal(Wave.glowTarget(voice('hearing', false)), Wave.glowTarget(voice('listening', false, 0)));
});
check('assistant speech glows even outside a conversation', () => {
  assert(Wave.glowTarget(voice('speaking', false)) > Wave.glowTarget(voice('listening', false)));
});
check('voice moves volumetric ribbons visibly without resizing the whole shape', () => {
  const wave = new Wave(color);
  const g = wave.geometry(3600, 1920, 1080);
  const before = wave.bands.flatMap(b => b.points.map(p => wave.point(p, b, g)));
  for (let i = 0; i < 30; i++) wave.advance(1 / 30, voice('speaking'), color);
  const active = wave.geometry(3600,1920,1080);
  const after = wave.bands.flatMap(b => b.points.map(p => wave.point(p,b,active)));
  assert.equal(active.radius,g.radius);
  assert(Math.max(...before.map(([x,y],i) => Math.hypot(after[i][0]-x,after[i][1]-y))) > 25);
});
check('no long-running phase jumps after an hour', () => {
  const wave = new Wave(color);
  for (const time of [0, 60, 600, 3600, 36000]) {
    const a = wave.geometry(time, 1920, 1080), b = wave.geometry(time + 1 / 30, 1920, 1080);
    for (const band of wave.bands) for (const p of band.points) {
      const [x, y] = wave.point(p, band, a), [xx, yy] = wave.point(p, band, b);
      assert(Math.hypot(xx - x, yy - y) < .2, 'point displacement exceeds .2 px/frame');
    }
  }
});
check('brightness jumps are bounded even with maximum alternating levels', () => {
  const wave = new Wave(color);
  for (let i = 0; i < 900; i++) {
    const old = wave.glow;
    wave.advance(1 / 30, voice(i % 2 ? 'speaking' : 'listening', true, i % 2), color);
    assert(Math.abs(wave.glow - old) < .13);
    assert(wave.glow >= 0 && wave.glow <= .75);
  }
});
check('brightness transitions take equal time at different frame rates', () => {
  const result = hz => {
    const wave = new Wave(color);
    for (let i = 0; i < hz * 2; i++) wave.advance(1 / hz, voice('speaking'), color);
    for (let i = 0; i < hz; i++) wave.advance(1 / hz, voice('listening'), color);
    return wave.glow;
  };
  assert(Math.abs(result(30) - result(60)) < 1e-12);
  assert(Math.abs(result(20) - result(60)) < 1e-12);
});
check('reduced motion freezes geometry but retains speech glow', () => {
  const wave = new Wave(color);
  const still = wave.geometry(0, 1280, 820, true);
  wave.advance(.1, voice('speaking'), color);
  assert.deepEqual(still, wave.geometry(600, 1280, 820, true));
  assert(wave.glow > 0);
});
check('invalid levels and missing voice remain finite', () => {
  const wave = new Wave(color);
  for (const value of [undefined, NaN, -1, Infinity, 'bad']) {
    wave.advance(.033, { ...voice('speaking'), output_level: value }, color);
    assert(Number.isFinite(wave.glow));
  }
  assert.equal(Wave.phase(null), 'off');
  assert.equal(Wave.glowTarget(null), 0);
});
function previewClock(refresh = 60) {
  const callbacks = [];
  const document = { hidden: false, documentElement: {dataset: {theme: 'dark'}} };
  const context = { window: {}, document, TrubaWave: Wave, requestAnimationFrame: fn => { callbacks.push(fn); return callbacks.length; },
    cancelAnimationFrame: () => {} };
  vm.runInNewContext(fs.readFileSync(require.resolve('../ui/web/home.js'), 'utf8'), context);
  const stamps = [];
  let stamp = 0;
  const home = { disposed: false, frame: 0, time: 0, reduced: {matches: false}, wave: new Wave(color),
    voice: voice('listening'), phase: 'listening', phaseColors: {listening: color}, lightColors: {listening: color},
    draw: () => stamps.push(stamp) };
  const animate = () => context.window.TrubaHome.prototype.animate.call(home);
  const tick = () => { stamp += 1000 / refresh; callbacks.shift()?.(stamp); };
  return {home, document, stamps, animate, tick, context};
}
check('actual home scheduler keeps 60 frames/s at 60/120/144/240 Hz', () => {
  for (const hz of [60, 120, 144, 240]) {
    const clock = previewClock(hz);
    clock.animate();
    for (let i = 0; i < hz * 60; i++) clock.tick();
    assert(Math.abs(clock.stamps.length - 3600) <= 1, `${hz} Hz: ${clock.stamps.length} frames`);
    const intervals = clock.stamps.slice(1).map((stamp, i) => stamp - clock.stamps[i]);
    assert(Math.max(...intervals) <= 1000 / 60 + 1000 / hz + .01);
  }
});
check('hidden document stops rendering and resumes without a time jump', () => {
  const clock = previewClock();
  clock.animate();
  for (let i = 0; i < 120; i++) clock.tick();
  const time = clock.home.time, count = clock.stamps.length;
  clock.document.hidden = true;
  for (let i = 0; i < 600; i++) clock.tick();
  assert.equal(clock.stamps.length, count);
  clock.document.hidden = false;
  clock.animate(); clock.tick();
  assert.equal(clock.home.time, time);
});
check('hearing and listening keep the same title without repeated writes', () => {
  const clock = previewClock();
  let writes = 0;
  const title = {value: '', get textContent() {return this.value;}, set textContent(value) {this.value = value; writes++;}};
  const elements = new Map([['.home-title', title]]);
  const element = name => {
    if (!elements.has(name)) elements.set(name, {querySelector: element});
    return elements.get(name);
  };
  const home = {disposed: false, root: {dataset: {}, querySelector: element, querySelectorAll: () => []},
    volume: {}, volumeLabel: () => {}, fitWave: () => {}};
  for (let i = 0; i < 100; i++) clock.context.window.TrubaHome.prototype.update.call(home, voice(i % 2 ? 'hearing' : 'listening'));
  assert.equal(title.textContent, 'Слушаю');
  assert.equal(writes, 1);
});
check('louder audio makes the actual wave taller, pauses return it to rest', () => {
  const height = level => {
    const wave = new Wave(color);
    for (let i=0;i<120;i++) wave.advance(1/60,{...voice('speaking'),output_level:level},color);
    wave.audioPhase = 0;
    const band=wave.bands[9], g=wave.geometry(0,1920,1080);
    // Вычитаем плоскость овала: проверяем движение ленты, а не размер фигуры.
    return Math.max(...band.points.map(p => Math.abs(wave.point(p,band,g)[1]-g.cy-p.s*g.radius*(.80+band.v*.21)*.64)));
  };
  assert(height(.08)>height(.01)*1.3);
  assert(height(.01)>height(0)*1.3);
  const wave = new Wave(color);
  for (let i=0;i<60;i++) wave.advance(1/60,voice('speaking'),color);
  for (let i=0;i<120;i++) wave.advance(1/60,voice('speaking',true,0),color);
  assert(wave.drive<.001);
});
check('loudness steps have bounded movement even after an hour', () => {
  const wave=new Wave(color), band=wave.bands[9];
  wave.audioPhase=Math.PI*2*20-.002;
  let g=wave.geometry(3600,1920,1080), before=band.points.map(p=>wave.point(p,band,g));
  for (let i=0;i<300;i++) {
    wave.advance(1/60,voice('speaking',true,i%18<9?1:0),color);
    g=wave.geometry(3600+(i+1)/60,1920,1080);
    const after=band.points.map(p=>wave.point(p,band,g));
    assert(Math.max(...before.map(([x,y],j)=>Math.hypot(after[j][0]-x,after[j][1]-y)))<10);
    before=after;
  }
});
check('volumetric ribbons keep bounded oval proportions and clear the title', () => {
  const wave=new Wave(color);
  wave.maxHalfHeight=160;
  for (let i=0;i<120;i++) wave.advance(1/60,voice('speaking'),color);
  for (const t of [0,1,10,100,3600]) {
    const g=wave.geometry(t,1920,1080);
    const positions=[];
    for (const band of wave.bands) for(const p of band.points) {
      const [x,y]=wave.point(p,band,g);
      assert(Math.abs(x-g.cx)<g.radius*1.5);
      assert(Math.abs(y-g.cy)<wave.maxHalfHeight);
      positions.push([x,y]);
    }
    const width=Math.max(...positions.map(p=>p[0]))-Math.min(...positions.map(p=>p[0]));
    const height=Math.max(...positions.map(p=>p[1]))-Math.min(...positions.map(p=>p[1]));
    assert(width>height*1.4, 'shape has lost its broad oval silhouette');
  }
});
console.log(JSON.stringify({passed}));
