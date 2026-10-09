/* DeRot breathing engine. Dependency-free, no build step.
 *
 * Usage: <div class="breath-tool"
 *             data-phases='[{"label":"Breathe in","seconds":4,"kind":"in"}, ...]'
 *             data-minutes="1"
 *             data-custom></div>   (data-custom adds the inhale/hold/exhale/hold inputs)
 *
 * kind: "in" (orb grows to full), "top" (short top-up inhale, grows to full),
 *       "hold" (orb stays where it is), "out" (orb shrinks to rest).
 * Optional "to" (0.5 to 1) overrides the orb size at the end of a phase.
 *
 * Timing model: nothing is counted with intervals. The engine keeps the total
 * elapsed run time (accumulated time before the last pause + time since the last
 * start) and derives the current cycle, phase and countdown from that number on
 * every tick. Exactly one timeout is ever pending (tick() always clears before it
 * schedules, pause/reset/complete clear it), so pause and resume cannot drift or
 * double-schedule.
 */
(function () {
  'use strict';

  var REST = 0.5;
  var MINUTE_CHOICES = [1, 2, 3, 5];
  var instanceCount = 0;

  function now() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  function fmtTime(ms) {
    var s = Math.max(0, Math.ceil(ms / 1000));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  function parsePhases(raw) {
    if (!raw) return null;
    try {
      var list = JSON.parse(raw);
      return Array.isArray(list) ? list : null;
    } catch (e) {
      return null;
    }
  }

  // Precompute each phase's duration in ms and the orb size it ends at.
  function normalise(list) {
    var phases = [];
    (list || []).forEach(function (p) {
      var sec = Number(p.seconds);
      if (!(sec > 0)) return;
      phases.push({
        label: String(p.label || ''),
        seconds: sec,
        ms: Math.round(sec * 1000),
        kind: p.kind || 'hold',
        to: typeof p.to === 'number' ? p.to : null
      });
    });
    // Two passes so a hold at the start of the cycle inherits the size from
    // the last phase of the previous cycle.
    var last = REST;
    for (var pass = 0; pass < 2; pass++) {
      for (var i = 0; i < phases.length; i++) {
        var p = phases[i];
        var target;
        if (p.to !== null) target = p.to;
        else if (p.kind === 'in' || p.kind === 'top') target = 1;
        else if (p.kind === 'out') target = REST;
        else target = last;
        p.target = target;
        last = target;
      }
    }
    return phases;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function BreathTool(root) {
    this.root = root;
    this.id = 'bt' + (++instanceCount);
    this.custom = root.hasAttribute('data-custom');
    var mins = parseInt(root.getAttribute('data-minutes'), 10);
    this.minutes = MINUTE_CHOICES.indexOf(mins) >= 0 ? mins : 1;
    this.phases = normalise(parsePhases(root.getAttribute('data-phases')) || [
      { label: 'Breathe in', seconds: 4, kind: 'in' },
      { label: 'Breathe out', seconds: 6, kind: 'out' }
    ]);

    this.state = 'idle';     // idle | running | paused | done
    this.accum = 0;          // ms of run time before the current start
    this.startedAt = 0;      // now() at the last start/resume
    this.timer = null;       // the single pending timeout
    this.phaseKey = -1;      // global index of the phase last applied
    this.needsVisual = false;
    this.sound = false;
    this.ctx = null;
    this.wake = null;
    this.reducedQuery = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

    this.build();
    this.bind();
    this.renderIdle();
  }

  BreathTool.prototype.reduced = function () {
    return !!(this.reducedQuery && this.reducedQuery.matches);
  };

  BreathTool.prototype.build = function () {
    var root = this.root;
    root.innerHTML = '';
    root.classList.add('bt');

    var stage = el('div', 'bt-stage');
    stage.setAttribute('aria-hidden', 'true');
    stage.appendChild(el('div', 'bt-ring'));
    this.orb = el('div', 'bt-orb');
    stage.appendChild(this.orb);
    this.countEl = el('div', 'bt-count');
    stage.appendChild(this.countEl);
    root.appendChild(stage);

    var horizon = el('div', 'horizon-line bt-horizon');
    horizon.setAttribute('aria-hidden', 'true');
    root.appendChild(horizon);

    this.phaseEl = el('p', 'bt-phase', 'Ready when you are');
    root.appendChild(this.phaseEl);

    var meta = el('p', 'bt-meta');
    this.cycleEl = el('span', 'bt-cycle');
    this.timeEl = el('span', 'bt-time');
    meta.appendChild(this.cycleEl);
    meta.appendChild(el('span', 'bt-dot', '·'));
    meta.appendChild(this.timeEl);
    root.appendChild(meta);

    this.live = el('div', 'sr-only');
    this.live.setAttribute('aria-live', 'polite');
    this.live.setAttribute('aria-atomic', 'true');
    root.appendChild(this.live);

    this.doneEl = el('p', 'bt-done');
    this.doneEl.hidden = true;
    root.appendChild(this.doneEl);

    var controls = el('div', 'bt-controls');
    this.toggleBtn = el('button', 'btn-primary bt-toggle', 'Start');
    this.toggleBtn.type = 'button';
    this.resetBtn = el('button', 'bt-btn bt-reset', 'Reset');
    this.resetBtn.type = 'button';
    controls.appendChild(this.toggleBtn);
    controls.appendChild(this.resetBtn);
    if (this.custom) root.appendChild(this.buildCustom());
    root.appendChild(controls);

    this.patternEl = el('p', 'bt-pattern');
    root.appendChild(this.patternEl);

    var opts = el('div', 'bt-options');
    var fs = el('fieldset', 'bt-length');
    fs.appendChild(el('legend', null, 'Session length'));
    var seg = el('div', 'bt-seg');
    var self = this;
    this.lengthInputs = [];
    MINUTE_CHOICES.forEach(function (m) {
      var id = self.id + '-len-' + m;
      var input = document.createElement('input');
      input.type = 'radio';
      input.name = self.id + '-len';
      input.id = id;
      input.value = String(m);
      if (m === self.minutes) input.checked = true;
      var label = el('label', null, m + ' min');
      label.setAttribute('for', id);
      seg.appendChild(input);
      seg.appendChild(label);
      self.lengthInputs.push(input);
    });
    fs.appendChild(seg);
    opts.appendChild(fs);

    var sw = el('label', 'bt-switch');
    this.soundInput = document.createElement('input');
    this.soundInput.type = 'checkbox';
    sw.appendChild(this.soundInput);
    sw.appendChild(el('span', 'bt-switch-track'));
    sw.appendChild(el('span', 'bt-switch-text', 'Soft sound cues'));
    opts.appendChild(sw);
    root.appendChild(opts);

    root.appendChild(el('p', 'bt-hint', 'Tip: press Space to start or pause.'));
  };

  BreathTool.prototype.buildCustom = function () {
    var wrap = el('fieldset', 'bt-custom');
    wrap.appendChild(el('legend', null, 'Your pattern (seconds)'));
    var grid = el('div', 'bt-custom-grid');
    var fields = [
      { key: 'in', label: 'Breathe in', def: 4, min: 1 },
      { key: 'hold1', label: 'Hold after in', def: 2, min: 0 },
      { key: 'out', label: 'Breathe out', def: 6, min: 1 },
      { key: 'hold2', label: 'Hold after out', def: 0, min: 0 }
    ];
    var self = this;
    this.customInputs = {};
    fields.forEach(function (f) {
      var id = self.id + '-' + f.key;
      var cell = el('div', 'bt-field');
      var label = el('label', null, f.label);
      label.setAttribute('for', id);
      var input = document.createElement('input');
      input.type = 'number';
      input.inputMode = 'numeric';
      input.id = id;
      input.min = String(f.min);
      input.max = '12';
      input.step = '1';
      input.value = String(f.def);
      input.setAttribute('data-min', String(f.min));
      input.setAttribute('data-label', f.label);
      cell.appendChild(label);
      cell.appendChild(input);
      grid.appendChild(cell);
      self.customInputs[f.key] = input;
    });
    wrap.appendChild(grid);
    this.customError = el('p', 'bt-error');
    this.customError.setAttribute('role', 'alert');
    wrap.appendChild(this.customError);
    wrap.appendChild(el('p', 'bt-field-note', 'Breathe in and out: 1 to 12 seconds. Holds: 0 to 12 seconds (0 skips the hold).'));
    this.phases = this.readCustom() || this.phases;
    return wrap;
  };

  // Returns a phase list, or null (and shows an error) when a value is invalid.
  BreathTool.prototype.readCustom = function () {
    var errors = [];
    var vals = {};
    var keys = ['in', 'hold1', 'out', 'hold2'];
    for (var i = 0; i < keys.length; i++) {
      var input = this.customInputs[keys[i]];
      var min = parseInt(input.getAttribute('data-min'), 10);
      var raw = String(input.value).trim();
      var n = Number(raw);
      var ok = raw !== '' && Math.floor(n) === n && n >= min && n <= 12;
      input.setAttribute('aria-invalid', ok ? 'false' : 'true');
      if (!ok) {
        errors.push(input.getAttribute('data-label') + ' needs a whole number from ' + min + ' to 12.');
      }
      vals[keys[i]] = n;
    }
    if (this.customError) this.customError.textContent = errors.join(' ');
    if (errors.length) return null;
    var list = [{ label: 'Breathe in', seconds: vals['in'], kind: 'in' }];
    if (vals.hold1 > 0) list.push({ label: 'Hold', seconds: vals.hold1, kind: 'hold' });
    list.push({ label: 'Breathe out', seconds: vals.out, kind: 'out' });
    if (vals.hold2 > 0) list.push({ label: 'Hold', seconds: vals.hold2, kind: 'hold' });
    return normalise(list);
  };

  BreathTool.prototype.bind = function () {
    var self = this;
    this.toggleBtn.addEventListener('click', function () { self.toggle(); });
    this.resetBtn.addEventListener('click', function () { self.reset(); });

    this.lengthInputs.forEach(function (input) {
      input.addEventListener('change', function () {
        if (!input.checked) return;
        self.minutes = parseInt(input.value, 10);
        if (self.state === 'running') return; // inputs are disabled while running
        if (self.state === 'done') self.reset();
        else if (self.state === 'paused') {
          // A shorter session than the time already breathed just starts over.
          if (self.elapsed() >= self.endMs()) self.reset();
          else self.renderProgress();
        } else self.renderIdle();
      });
    });

    this.soundInput.addEventListener('change', function () {
      self.sound = self.soundInput.checked;
      if (self.sound) self.cue('in', 0.05); // created inside a user gesture
    });

    if (this.custom) {
      Object.keys(this.customInputs).forEach(function (k) {
        self.customInputs[k].addEventListener('input', function () {
          var phases = self.readCustom();
          if (!phases) {
            self.toggleBtn.disabled = true;
            return;
          }
          self.toggleBtn.disabled = false;
          self.phases = phases;
          self.reset();
        });
      });
    }

    document.addEventListener('keydown', function (e) {
      if (e.key !== ' ' && e.code !== 'Space') return;
      if (e.repeat || e.altKey || e.ctrlKey || e.metaKey) return;
      var t = e.target;
      var tag = t && t.tagName ? t.tagName.toLowerCase() : '';
      // Let buttons, links and form fields handle Space natively.
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button' || tag === 'a' || tag === 'summary' || (t && t.isContentEditable)) return;
      e.preventDefault();
      if (!self.toggleBtn.disabled) self.toggle();
    });

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && self.state === 'running') {
        self.requestWake();
        self.tick(); // catch the display up immediately after a throttled background tab
      }
    });

    if (this.reducedQuery) {
      var onChange = function () { if (self.state === 'running') { self.needsVisual = true; self.tick(); } };
      if (this.reducedQuery.addEventListener) this.reducedQuery.addEventListener('change', onChange);
      else if (this.reducedQuery.addListener) this.reducedQuery.addListener(onChange);
    }
  };

  /* ---------- timing ---------- */

  BreathTool.prototype.cycleMs = function () {
    var total = 0;
    for (var i = 0; i < this.phases.length; i++) total += this.phases[i].ms;
    return total;
  };

  BreathTool.prototype.elapsed = function () {
    return this.accum + (this.state === 'running' ? now() - this.startedAt : 0);
  };

  // Sessions always finish on a whole cycle, so you never stop mid-breath.
  BreathTool.prototype.totalCycles = function () {
    return Math.max(1, Math.ceil((this.minutes * 60000) / this.cycleMs()));
  };

  BreathTool.prototype.endMs = function () {
    return this.totalCycles() * this.cycleMs();
  };

  BreathTool.prototype.locate = function (t) {
    var c = this.cycleMs();
    var n = this.phases.length;
    var cycle = Math.floor(t / c);
    var within = t - cycle * c;
    for (var i = 0; i < n; i++) {
      var ms = this.phases[i].ms;
      if (within < ms) {
        return { cycle: cycle, index: i, key: cycle * n + i, remaining: ms - within };
      }
      within -= ms;
    }
    // Floating point edge: treat as the very end of the last phase.
    return { cycle: cycle, index: n - 1, key: cycle * n + n - 1, remaining: 0 };
  };

  BreathTool.prototype.toggle = function () {
    if (this.state === 'running') this.pause();
    else this.start();
  };

  BreathTool.prototype.start = function () {
    if (this.state === 'idle' || this.state === 'done') {
      this.accum = 0;
      this.phaseKey = -1;
      this.doneEl.hidden = true;
    }
    this.needsVisual = true; // re-aim the orb from wherever it currently sits
    this.state = 'running';
    this.startedAt = now();
    if (this.sound) this.audio();
    this.requestWake();
    this.setInputsDisabled(true);
    this.root.classList.add('is-running');
    this.root.classList.remove('is-paused');
    this.toggleBtn.textContent = 'Pause';
    this.tick();
  };

  BreathTool.prototype.pause = function () {
    if (this.state !== 'running') return;
    this.accum += now() - this.startedAt;
    this.state = 'paused';
    this.clearTimer();
    this.freezeOrb();
    this.releaseWake();
    this.setInputsDisabled(false);
    this.root.classList.remove('is-running');
    this.root.classList.add('is-paused');
    this.toggleBtn.textContent = 'Resume';
    this.phaseEl.textContent = 'Paused';
    this.announce('Paused');
  };

  BreathTool.prototype.reset = function () {
    this.clearTimer();
    this.state = 'idle';
    this.accum = 0;
    this.phaseKey = -1;
    this.needsVisual = false;
    this.releaseWake();
    this.setInputsDisabled(false);
    this.root.classList.remove('is-running', 'is-paused', 'is-done');
    this.doneEl.hidden = true;
    this.setOrb(REST, 600);
    this.renderIdle();
  };

  BreathTool.prototype.complete = function () {
    this.clearTimer();
    var cycles = this.totalCycles();
    this.state = 'done';
    this.accum = this.endMs();
    this.releaseWake();
    this.setInputsDisabled(false);
    this.root.classList.remove('is-running', 'is-paused');
    this.root.classList.add('is-done');
    this.setOrb(REST, 1200);
    this.countEl.textContent = '';
    this.phaseEl.textContent = 'Nicely done';
    this.cycleEl.textContent = cycles + (cycles === 1 ? ' cycle' : ' cycles');
    this.timeEl.textContent = fmtTime(this.endMs());
    this.toggleBtn.textContent = 'Start again';
    this.doneEl.textContent = 'That was ' + cycles + (cycles === 1 ? ' slow breath cycle' : ' slow breath cycles') +
      '. Before you jump back in, notice your shoulders, your jaw, your breathing. Stay here as long as you like.';
    this.doneEl.hidden = false;
    this.announce('Session complete. ' + cycles + (cycles === 1 ? ' cycle.' : ' cycles.'));
    this.cue('out', 0.06);
  };

  BreathTool.prototype.clearTimer = function () {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  };

  BreathTool.prototype.tick = function () {
    this.clearTimer();
    if (this.state !== 'running') return;

    var t = this.elapsed();
    var end = this.endMs();
    if (t >= end) {
      this.complete();
      return;
    }

    var pos = this.locate(t);
    var isNew = pos.key !== this.phaseKey;
    if (isNew || this.needsVisual) {
      this.applyPhase(pos, isNew);
      this.phaseKey = pos.key;
      this.needsVisual = false;
    }
    this.renderCount(pos, t, end);

    // Wake at the next whole-second change of the countdown, or the phase end.
    var rem = pos.remaining;
    var toNextSecond = rem % 1000;
    if (toNextSecond < 1) toNextSecond = 1000;
    var wait = Math.min(toNextSecond, rem) + 8;
    var self = this;
    this.timer = setTimeout(function () { self.tick(); }, Math.max(16, wait));
  };

  /* ---------- rendering ---------- */

  BreathTool.prototype.applyPhase = function (pos, isNew) {
    var p = this.phases[pos.index];
    this.setOrb(p.target, pos.remaining, p.kind === 'top' ? 'ease-out' : 'cubic-bezier(0.45, 0, 0.55, 1)');
    this.root.setAttribute('data-kind', p.kind);
    this.phaseEl.textContent = p.label;
    if (isNew) {
      this.announce(p.label + ', ' + p.seconds + (p.seconds === 1 ? ' second' : ' seconds'));
      this.cue(p.kind);
    }
  };

  BreathTool.prototype.setOrb = function (scale, durationMs, easing) {
    var orb = this.orb;
    var reduced = this.reduced();
    var opacity = reduced ? (0.42 + (scale - REST) * 1.16) : 1;
    var d = Math.max(0, Math.round(durationMs || 0));
    var ease = easing || 'cubic-bezier(0.45, 0, 0.55, 1)';
    void orb.offsetWidth; // commit any frozen value before the new transition
    orb.style.transition = 'transform ' + d + 'ms ' + ease + ', opacity ' + d + 'ms ' + ease;
    orb.style.transform = reduced ? 'scale(1)' : 'scale(' + scale + ')';
    orb.style.opacity = String(opacity);
  };

  // Hold the orb exactly where it is mid-transition.
  BreathTool.prototype.freezeOrb = function () {
    var orb = this.orb;
    var cs = window.getComputedStyle(orb);
    var transform = cs.transform;
    var opacity = cs.opacity;
    orb.style.transition = 'none';
    orb.style.transform = transform && transform !== 'none' ? transform : '';
    orb.style.opacity = opacity;
  };

  BreathTool.prototype.renderCount = function (pos, t, end) {
    this.countEl.textContent = String(Math.max(1, Math.ceil(pos.remaining / 1000)));
    this.cycleEl.textContent = 'Cycle ' + Math.min(pos.cycle + 1, this.totalCycles()) + ' of ' + this.totalCycles();
    this.timeEl.textContent = fmtTime(end - t) + ' left';
  };

  BreathTool.prototype.renderProgress = function () {
    var t = this.elapsed();
    this.renderCount(this.locate(t), t, this.endMs());
  };

  BreathTool.prototype.renderIdle = function () {
    var first = this.phases[0];
    this.toggleBtn.textContent = 'Start';
    this.phaseEl.textContent = 'Ready when you are';
    this.countEl.textContent = first ? String(first.seconds) : '';
    this.cycleEl.textContent = this.totalCycles() + (this.totalCycles() === 1 ? ' cycle' : ' cycles');
    this.timeEl.textContent = fmtTime(this.endMs());
    this.patternEl.textContent = this.phases.map(function (p) {
      return p.label + ' ' + p.seconds;
    }).join(' · ');
    this.root.removeAttribute('data-kind');
    if (!this.orb.style.transform) this.setOrb(REST, 0);
  };

  BreathTool.prototype.setInputsDisabled = function (disabled) {
    this.lengthInputs.forEach(function (i) { i.disabled = disabled; });
    if (this.custom) {
      var inputs = this.customInputs;
      Object.keys(inputs).forEach(function (k) { inputs[k].disabled = disabled; });
    }
  };

  BreathTool.prototype.announce = function (text) {
    // Clear first so repeated labels ("Hold", "Hold") are still read out.
    var live = this.live;
    live.textContent = '';
    setTimeout(function () { live.textContent = text; }, 40);
  };

  /* ---------- sound (WebAudio, no files) ---------- */

  BreathTool.prototype.audio = function () {
    if (!this.ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try { this.ctx = new AC(); } catch (e) { return null; }
    }
    if (this.ctx.state === 'suspended') {
      try { this.ctx.resume(); } catch (e) { /* ignore */ }
    }
    return this.ctx;
  };

  BreathTool.prototype.cue = function (kind, peak) {
    if (!this.sound) return;
    var ctx = this.audio();
    if (!ctx) return;
    var freq = { 'in': 392, 'top': 523.25, 'hold': 329.63, 'out': 261.63 }[kind] || 329.63;
    var vol = peak || (kind === 'hold' ? 0.04 : 0.07);
    try {
      var t0 = ctx.currentTime + 0.01;
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, t0);
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(vol, t0 + 0.06);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.1);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + 1.2);
    } catch (e) { /* sound is optional */ }
  };

  /* ---------- screen wake lock ---------- */

  BreathTool.prototype.requestWake = function () {
    var self = this;
    try {
      if (!('wakeLock' in navigator) || this.wake || this.wakePending) return;
      this.wakePending = true;
      navigator.wakeLock.request('screen').then(function (sentinel) {
        self.wakePending = false;
        if (self.state !== 'running') {
          sentinel.release().catch(function () {});
          return;
        }
        self.wake = sentinel;
        sentinel.addEventListener('release', function () {
          if (self.wake === sentinel) self.wake = null;
        });
      }).catch(function () { self.wakePending = false; });
    } catch (e) {
      this.wakePending = false;
    }
  };

  BreathTool.prototype.releaseWake = function () {
    try {
      if (this.wake) {
        var w = this.wake;
        this.wake = null;
        w.release().catch(function () {});
      }
    } catch (e) { /* ignore */ }
  };

  function init() {
    var nodes = document.querySelectorAll('.breath-tool');
    for (var i = 0; i < nodes.length; i++) {
      if (!nodes[i].__breathTool) nodes[i].__breathTool = new BreathTool(nodes[i]);
    }
  }

  window.DeRotBreathing = { init: init, BreathTool: BreathTool };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
