/* Объёмные ленты текут под голос; фаза накапливается без скачков. */
((scope) => {
  const tau = Math.PI * 2;
  const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
  const ease = (value, target, dt, seconds) => value + (target - value) * -Math.expm1(-dt / seconds);
  class TrubaWave {
    constructor(color) {
      this.color = color.slice();
      this.drive = 0;
      this.glow = 0;
      this.audioPhase = 0;
      this.position = [0, 0];
      this.maxHalfHeight = Infinity;
      this.bands = Array.from({ length: 18 }, (_, band) => {
        const v = (band - 8.5) / 8.5;
        return {v, weight: 1 - Math.abs(v), points: Array.from({ length: 140 }, (_, i) => {
          const a = i / 140 * tau;
          return {c: Math.cos(a), s: Math.sin(a), s4: Math.sin(a * 4 + v * 2.8),
            c4: Math.cos(a * 4 + v * 2.8), s3: Math.sin(a * 3), c3: Math.cos(a * 3),
            sy: Math.sin(a * 3 + v * 2), cy: Math.cos(a * 3 + v * 2)};
        })};
      });
      this.sparks = Array.from({length: 260}, (_, i) => ({
        c: Math.cos(i * 2.39996), s: Math.sin(i * 2.39996), r: .65 + (i % 57) / 57 * .9,
      }));
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
      const target = TrubaWave.audioTarget(voice);
      this.drive = ease(this.drive, target, dt, target > this.drive ? .10 : .28);
      this.glow = ease(this.glow, TrubaWave.glowTarget(voice), dt, .18);
      this.audioPhase = (this.audioPhase + dt * this.drive * 1.45) % (tau * 20);
      this.color = this.color.map((value, i) => ease(value, targetColor[i], dt, .6));
    }
    geometry(t, width, height, reduced = false) {
      if (reduced) t = 0;
      const phase = reduced ? 0 : this.audioPhase, drive = reduced ? 0 : this.drive;
      // Запас под максимальную волну удерживает ленты над подписью при любой громкости.
      return {radius: Math.min(width * .29, height * .27, 350, this.maxHalfHeight / .9), cx: width / 2,
        cy: Number.isFinite(this.centerY) ? this.centerY : height * .40,
        radial: .055 + drive * .065, secondary: .028 + drive * .025, vertical: .045 + drive * .075,
        s: Math.sin(t * .16 + phase), c: Math.cos(t * .16 + phase),
        s2: Math.sin(t * .09 + phase * .65), c2: Math.cos(t * .09 + phase * .65),
        sy: Math.sin(t * .12 + phase * .8), cyWave: Math.cos(t * .12 + phase * .8),
        sr: Math.sin(t * .008), cr: Math.cos(t * .008)};
    }
    point(point, band, geometry, position = []) {
      const g = geometry;
      const ripple = (point.s4 * g.c + point.c4 * g.s) * g.radial + (point.c3 * g.c2 + point.s3 * g.s2) * g.secondary;
      const r = g.radius * (.80 + band.v * .21 + ripple);
      position[0] = g.cx + point.c * r * 1.23;
      position[1] = g.cy + point.s * r * .64 + (point.sy * g.cyWave + point.cy * g.sy) * g.radius * g.vertical;
      return position;
    }
    paint(ctx, t, width, height, reduced = false, light = false) {
      if (!ctx || !width || !height) return;
      const g = this.geometry(t, width, height, reduced);
      const rgb = this.color.map(Math.round).join(',');
      ctx.clearRect(0, 0, width, height);
      const glow = ctx.createRadialGradient(g.cx, g.cy, g.radius * .2, g.cx, g.cy, g.radius * 1.6);
      glow.addColorStop(0, `rgba(${rgb},.008)`);
      glow.addColorStop(.5, `rgba(${rgb},${.022 + this.glow * .016})`);
      glow.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = glow;
      ctx.fillRect(g.cx - g.radius * 1.6, g.cy - g.radius * 1.6, g.radius * 3.2, g.radius * 3.2);
      for (const band of this.bands) {
        // На светлом фоне прозрачные края теряются: усиливаем точки, сохраняя форму.
        const dot = (light ? .78 : .65) + band.weight * .38;
        const opacity = light ? .42 + band.weight * (.36 + this.glow * .16)
          : .18 + band.weight * (.43 + this.glow * .18);
        ctx.fillStyle = `rgba(${rgb},${opacity})`;
        ctx.beginPath();
        for (const point of band.points) {
          const [x, y] = this.point(point, band, g, this.position);
          ctx.moveTo(x + dot, y); ctx.arc(x, y, dot, 0, tau);
        }
        ctx.fill();
      }
      ctx.fillStyle = `rgba(${rgb},${(light ? .25 : .14) + this.glow * .08})`;
      ctx.beginPath();
      for (const point of this.sparks) {
        const r = g.radius * point.r;
        const x = g.cx + (point.c * g.cr - point.s * g.sr) * r * 1.2;
        const y = g.cy + (point.s * g.cr + point.c * g.sr) * r * .66;
        ctx.moveTo(x + .6, y); ctx.arc(x, y, .6, 0, tau);
      }
      ctx.fill();
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = TrubaWave;
  else scope.TrubaWave = TrubaWave;
})(typeof window === 'undefined' ? globalThis : window);
