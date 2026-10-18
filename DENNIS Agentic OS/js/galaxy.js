// =========================================================================
// HERMES APOLLO - Jarvis Voice Core & Reactive Galaxy Simulation
// Voice Wake Command System ("Hey Apollo" / Jarvis Protocol)
// =========================================================================

class HermesGalaxy {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.stars = [];
    this.arms = 2;
    this.starCount = 360;
    this.angleOffset = 0;

    // Voice & State Engine
    this.state = 'standby'; // 'standby' | 'listening' | 'awake' | 'speaking' | 'muted'
    this.audioLevel = 0.0;
    this.targetAudioLevel = 0.0;
    this.shockwaves = [];
    this.wakeTimeout = null;
    this.isVoiceWakeActive = true;
    this.hasSpeechRec = false;
    this.recognition = null;
    this.synth = window.speechSynthesis || null;
    this.audioCtx = null;
    this.preferredVoice = null;
    this.toastTimer = null;

    this.init();
    this.initAudioAndVoice();
  }

  // -----------------------------------------------------------------------
  // 1. INITIALIZATION & RESIZING
  // -----------------------------------------------------------------------
  init() {
    this.resize();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.canvas);
    this.bindDomElements();
    this.animate();
  }

  resize() {
    const { width, height } = this.canvas.getBoundingClientRect();
    const canvasWidth = Math.round(width);
    const canvasHeight = Math.round(height);

    if (!canvasWidth || !canvasHeight || (this.canvas.width === canvasWidth && this.canvas.height === canvasHeight)) return;

    this.canvas.width = canvasWidth;
    this.canvas.height = canvasHeight;
    this.createGalaxy();
  }

  createGalaxy() {
    this.stars = [];
    for (let i = 0; i < this.starCount; i++) {
      // Logarithmic spiral distribution
      const r = Math.pow(Math.random(), 2) * (this.canvas.width * 0.44);
      const armIndex = i % this.arms;
      const armAngle = (armIndex * 2 * Math.PI) / this.arms;
      const spiralSpread = (Math.random() - 0.5) * 0.48;
      const angle = armAngle + r * 0.08 + spiralSpread;

      // Dynamic Apollo Color palette: Neon Cyan, Arc Blue, Violet, Solar Gold, White
      let color = '#38bdf8';
      if (r < 14) {
        color = '#ffffff';
      } else if (r < 32) {
        color = Math.random() > 0.4 ? '#00f0ff' : '#67e8f9';
      } else if (r < 65) {
        color = Math.random() > 0.5 ? '#818cf8' : '#38bdf8';
      } else {
        color = Math.random() > 0.4 ? '#c084fc' : '#0284c7';
      }

      this.stars.push({
        r: r,
        baseAngle: angle,
        size: Math.random() * 1.5 + 0.4,
        color: color,
        speed: 0.008 + (0.016 / (1 + r * 0.04))
      });
    }
  }

  // -----------------------------------------------------------------------
  // 2. WEB AUDIO & JARVIS ACOUSTIC CHIMES
  // -----------------------------------------------------------------------
  getAudioContext() {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioCtx = new AudioContextClass();
      }
    }
    if (this.audioCtx && this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  // Ascending futuristic Jarvis wake chime (Dual harmonic cyber chime)
  playWakeChime() {
    try {
      const ctx = this.getAudioContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();
      const filter = ctx.createBiquadFilter();

      filter.type = 'bandpass';
      filter.frequency.setValueAtTime(1200, now);
      filter.Q.setValueAtTime(3, now);

      osc1.type = 'sine';
      osc2.type = 'triangle';

      // First note: 587.33 Hz (D5), slides up to 1174.66 Hz (D6)
      osc1.frequency.setValueAtTime(587.33, now);
      osc1.frequency.exponentialRampToValueAtTime(1174.66, now + 0.16);

      osc2.frequency.setValueAtTime(880, now);
      osc2.frequency.exponentialRampToValueAtTime(1760, now + 0.18);

      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(0.18, now + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.45);

      osc1.connect(filter);
      osc2.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);

      osc1.start(now);
      osc2.start(now);
      osc1.stop(now + 0.48);
      osc2.stop(now + 0.48);
    } catch (_) {}
  }

  // Soft affirmation chirp for executed commands
  playAckChime() {
    try {
      const ctx = this.getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(1320, now + 0.1);

      gain.gain.setValueAtTime(0.12, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.2);
    } catch (_) {}
  }

  // Descending soft tone when Apollo returns to standby
  playSleepChime() {
    try {
      const ctx = this.getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(440, now + 0.2);

      gain.gain.setValueAtTime(0.08, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.25);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.26);
    } catch (_) {}
  }

  // -----------------------------------------------------------------------
  // 3. SPEECH SYNTHESIS (JARVIS VOICE)
  // -----------------------------------------------------------------------
  initSpeechSynthesis() {
    if (!this.synth) return;

    const findVoice = () => {
      const voices = this.synth.getVoices();
      if (!voices || voices.length === 0) return;

      // Prefer British / refined English voice like Paul Bettany's Jarvis
      this.preferredVoice =
        voices.find(v => v.name.includes('Daniel') || v.name.includes('George') || v.name.includes('Oliver') || v.lang === 'en-GB') ||
        voices.find(v => v.name.includes('Samantha') || v.name.includes('Alex') || v.lang.startsWith('en')) ||
        voices[0];
    };

    findVoice();
    if (typeof this.synth.onvoiceschanged !== 'undefined') {
      this.synth.onvoiceschanged = findVoice;
    }
  }

  speak(text, onDone = null) {
    if (!this.synth) {
      if (onDone) onDone();
      return;
    }

    try {
      this.synth.cancel();

      const utterance = new SpeechSynthesisUtterance(text);
      if (this.preferredVoice) {
        utterance.voice = this.preferredVoice;
      }
      utterance.rate = 1.03;
      utterance.pitch = 0.96;
      utterance.volume = 0.95;

      utterance.onstart = () => {
        this.state = 'speaking';
        this.updateUiStatus('Apollo Speaking...', 'emerald');
        this.setEqualizerActive(true);
        this.showHudToast(text);
        this.spawnShockwave('#00f0ff');
      };

      utterance.onend = () => {
        this.setEqualizerActive(false);
        if (this.wakeTimeout) {
          this.state = 'awake';
          this.updateUiStatus('Apollo Awake • Listening...', 'cyan');
        } else {
          this.state = 'standby';
          this.updateUiStatus('Listening for "Hey Apollo"', 'cyan');
        }
        if (onDone) onDone();
      };

      utterance.onerror = () => {
        this.setEqualizerActive(false);
        this.state = 'standby';
        this.updateUiStatus('Listening for "Hey Apollo"', 'cyan');
        if (onDone) onDone();
      };

      this.synth.speak(utterance);
    } catch (_) {
      if (onDone) onDone();
    }
  }

  // -----------------------------------------------------------------------
  // 4. SPEECH RECOGNITION & VOICE WAKE COMMANDS
  // -----------------------------------------------------------------------
  initAudioAndVoice() {
    this.initSpeechSynthesis();

    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      this.hasSpeechRec = false;
      this.updateUiStatus('Click to Wake (Voice API N/A)', 'slate');
      return;
    }

    this.hasSpeechRec = true;
    try {
      this.recognition = new SpeechRec();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.lang = 'en-US';

      this.recognition.onstart = () => {
        this.isListening = true;
        this.updateMicButton(true);
        if (this.state !== 'awake' && this.state !== 'speaking') {
          this.updateUiStatus('Listening for "Hey Apollo"', 'cyan');
        }
      };

      this.recognition.onresult = (event) => {
        let transcript = '';
        let isFinal = false;

        for (let i = event.resultIndex; i < event.results.length; i++) {
          transcript += event.results[i][0].transcript;
          if (event.results[i].isFinal) isFinal = true;
        }

        const cleanTranscript = transcript.trim().toLowerCase();
        if (!cleanTranscript) return;

        this.updateTranscriptText(`Dennis: "${transcript.trim()}"`);
        this.targetAudioLevel = 0.7;

        // Wake word detection: "hey apollo", "apollo", "hermes", "jarvis", "wake up"
        const isWakeWord =
          cleanTranscript.includes('hey apollo') ||
          cleanTranscript.includes('apollo') ||
          cleanTranscript.includes('hermes') ||
          cleanTranscript.includes('jarvis') ||
          cleanTranscript.includes('wake up');

        if (isWakeWord) {
          // If wake word is accompanied by a command in the same sentence
          const commandAfterWake = cleanTranscript
            .replace(/hey apollo|apollo|hermes|jarvis|wake up/gi, '')
            .trim();

          if (commandAfterWake.length > 2) {
            this.wake(false);
            if (isFinal) {
              this.executeVoiceCommand(commandAfterWake);
            }
          } else {
            // Standalone wake word: "Hey Apollo"
            this.wake(true);
          }
        } else if (this.state === 'awake' || this.state === 'listening') {
          // Apollo is currently awake and awaiting a command!
          if (isFinal) {
            this.executeVoiceCommand(cleanTranscript);
          }
        }
      };

      this.recognition.onerror = (event) => {
        if (event.error === 'not-allowed') {
          this.isVoiceWakeActive = false;
          this.updateMicButton(false);
          this.updateUiStatus('Mic Denied • Click to Wake', 'slate');
        }
      };

      this.recognition.onend = () => {
        this.isListening = false;
        // Auto-restart continuous listening if voice wake remains active
        if (this.isVoiceWakeActive) {
          setTimeout(() => {
            if (this.isVoiceWakeActive && !this.isListening) {
              try {
                this.recognition.start();
              } catch (_) {}
            }
          }, 350);
        }
      };

      // Start listening initially if voice wake is active
      if (this.isVoiceWakeActive) {
        try {
          this.recognition.start();
        } catch (_) {}
      }
    } catch (_) {
      this.hasSpeechRec = false;
    }
  }

  // -----------------------------------------------------------------------
  // 5. VOICE COMMAND EXECUTION (JARVIS OS PROTOCOLS)
  // -----------------------------------------------------------------------
  executeVoiceCommand(cmd) {
    this.playAckChime();
    this.targetAudioLevel = 0.9;
    const lower = cmd.toLowerCase().trim();

    // 1. Workspace
    if (lower.includes('workspace')) {
      if (window.switchMainView) {
        window.switchMainView('workspace');
      } else {
        this.triggerNavigation('Workspace');
      }
      this.respond('Opening Workspace, Dennis.');
      return;
    }

    // 2. Trading Den
    if (lower.includes('trading') || lower.includes('market making') || lower.includes('arbitrage')) {
      if (window.switchMainView) {
        window.switchMainView('trading-den');
      } else {
        this.triggerNavigation('Trading Den');
      }
      this.respond('Accessing Trading Den and MMcloneLoopEngineering, Dennis.');
      return;
    }

    // 3. Vault
    if (lower.includes('vault') || lower.includes('security')) {
      this.triggerNavigation('Vault');
      this.respond('Opening Vault secure credentials.');
      return;
    }

    // 4. Settings
    if (lower.includes('setting') || lower.includes('config')) {
      this.triggerNavigation('Settings');
      this.respond('Opening System Settings, Dennis.');
      return;
    }

    // 5. Hermes Chat
    if (lower.includes('chat')) {
      this.triggerHeaderTab('Hermes Chat');
      this.respond('Hermes Chat activated, Dennis.');
      return;
    }

    // 6. Bots Catalog
    if (lower.includes('bot')) {
      this.triggerHeaderTab('Bots');
      this.respond('Switching to Bots catalog.');
      return;
    }

    // 7. Studio
    if (lower.includes('studio')) {
      this.triggerHeaderTab('Studio');
      this.respond('Studio mode engaged.');
      return;
    }

    // 8. System Status / Diagnostics
    if (lower.includes('status') || lower.includes('diagnostic') || lower.includes('report')) {
      this.respond('All systems operational, Dennis. Core runtime online, zero errors, neural cosmos synchronized.');
      return;
    }

    // 9. Time
    if (lower.includes('time') || lower.includes('clock')) {
      const now = new Date();
      const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      this.respond(`It is currently ${timeStr}, Dennis.`);
      return;
    }

    // 10. Identity
    if (lower.includes('who are you') || lower.includes('what are you')) {
      this.respond('I am Hermes Apollo, your agentic voice assistant, modeled after Jarvis.');
      return;
    }

    // 11. New Project Session
    if (lower.includes('new session') || lower.includes('create session')) {
      const newSessionBtn = document.getElementById('ws-new-session');
      if (newSessionBtn) newSessionBtn.click();
      this.respond('Initializing new project session, Dennis.');
      return;
    }

    // 12. Sleep / Standby
    if (lower.includes('sleep') || lower.includes('standby') || lower.includes('goodbye') || lower.includes('thank you') || lower.includes('thanks')) {
      this.respond('Standing by, Dennis. Just say "Hey Apollo" when you need me.', () => {
        this.sleep();
      });
      return;
    }

    // 13. General command fallback
    this.respond(`Voice command received, Dennis: "${cmd}". Routing to agent core.`);
  }

  respond(text, onDone = null) {
    this.updateTranscriptText(`Apollo: "${text}"`);
    this.showHudToast(text);
    this.speak(text, onDone);
  }

  triggerNavigation(name) {
    const items = document.querySelectorAll('.sidebar-nav-item');
    items.forEach(item => {
      if (item.textContent.toLowerCase().includes(name.toLowerCase())) {
        item.click();
      }
    });
  }

  triggerHeaderTab(name) {
    const tabs = document.querySelectorAll('.header-nav-tab');
    tabs.forEach(tab => {
      if (tab.textContent.toLowerCase().includes(name.toLowerCase())) {
        tab.click();
      }
    });
  }

  // -----------------------------------------------------------------------
  // 6. WAKE & SLEEP LIFECYCLE
  // -----------------------------------------------------------------------
  wake(speakGreeting = true) {
    if (this.wakeTimeout) {
      clearTimeout(this.wakeTimeout);
    }

    const widget = document.getElementById('hermes-apollo-widget');
    if (widget) widget.classList.add('apollo-awake');

    this.playWakeChime();
    this.spawnShockwave('#00f0ff');
    this.spawnShockwave('#38bdf8');
    this.state = 'awake';
    this.updateUiStatus('Apollo Awake • Listening...', 'cyan');
    this.setEqualizerActive(true);

    if (speakGreeting) {
      const greetings = [
        'At your service, Dennis.',
        'Apollo online. How may I assist you, Dennis?',
        'Online and listening, Dennis.',
        'Yes, Dennis? Systems ready.'
      ];
      const selected = greetings[Math.floor(Math.random() * greetings.length)];
      this.respond(selected);
    }

    // Stay awake for 10 seconds awaiting command before returning to standby
    this.wakeTimeout = setTimeout(() => {
      this.sleep();
    }, 10000);
  }

  sleep() {
    if (this.wakeTimeout) {
      clearTimeout(this.wakeTimeout);
      this.wakeTimeout = null;
    }

    const widget = document.getElementById('hermes-apollo-widget');
    if (widget) widget.classList.remove('apollo-awake');

    this.playSleepChime();
    this.state = this.isVoiceWakeActive ? 'standby' : 'muted';
    this.setEqualizerActive(false);

    if (this.isVoiceWakeActive) {
      this.updateUiStatus('Listening for "Hey Apollo"', 'cyan');
    } else {
      this.updateUiStatus('Voice Wake Muted', 'slate');
    }
  }

  toggleVoiceWake() {
    this.isVoiceWakeActive = !this.isVoiceWakeActive;

    if (this.isVoiceWakeActive) {
      this.updateMicButton(true);
      this.updateUiStatus('Listening for "Hey Apollo"', 'cyan');
      if (this.recognition && !this.isListening) {
        try {
          this.recognition.start();
        } catch (_) {}
      }
      this.respond('Voice wake active. Listening for "Hey Apollo".');
    } else {
      this.updateMicButton(false);
      this.updateUiStatus('Voice Wake Muted', 'slate');
      if (this.recognition && this.isListening) {
        try {
          this.recognition.stop();
        } catch (_) {}
      }
      this.sleep();
    }
  }

  // -----------------------------------------------------------------------
  // 7. UI BINDINGS & HUD CONTROLS
  // -----------------------------------------------------------------------
  bindDomElements() {
    // 1. Click Canvas / Wrap to Wake
    const canvasWrap = document.getElementById('apollo-canvas-wrap');
    if (canvasWrap) {
      canvasWrap.addEventListener('click', () => {
        this.getAudioContext();
        this.wake(true);
      });
    }

    // 2. Test Wake Button
    const testWakeBtn = document.getElementById('apollo-test-wake');
    if (testWakeBtn) {
      testWakeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.getAudioContext();
        this.wake(true);
      });
    }

    // 3. Mic / Voice Wake Toggle Button
    const micToggleBtn = document.getElementById('apollo-mic-toggle');
    if (micToggleBtn) {
      micToggleBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.getAudioContext();
        this.toggleVoiceWake();
      });
    }

    // 4. Header Nav Apollo Tab
    const headerApollo = document.getElementById('header-nav-apollo');
    if (headerApollo) {
      headerApollo.addEventListener('click', () => {
        const widget = document.getElementById('hermes-apollo-widget');
        if (widget) {
          widget.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        this.getAudioContext();
        this.wake(true);
      });
    }
  }

  updateUiStatus(text, color = 'cyan') {
    const textEl = document.getElementById('apollo-status-text');
    const dotEl = document.getElementById('apollo-status-dot');

    if (textEl) textEl.textContent = text;
    if (dotEl) {
      dotEl.className = 'w-2 h-2 rounded-full status-dot-pulse';
      if (color === 'emerald') {
        dotEl.classList.add('bg-emerald-400');
      } else if (color === 'slate') {
        dotEl.classList.add('bg-slate-500');
      } else {
        dotEl.classList.add('bg-cyan-400');
      }
    }
  }

  updateMicButton(active) {
    const label = document.getElementById('apollo-mic-label');
    const icon = document.getElementById('apollo-mic-icon');
    const btn = document.getElementById('apollo-mic-toggle');

    if (label) label.textContent = active ? 'Voice Wake ON' : 'Voice Wake OFF';
    if (icon) {
      icon.setAttribute('data-lucide', active ? 'mic' : 'mic-off');
      if (window.lucide) window.lucide.createIcons();
    }
    if (btn) {
      if (active) {
        btn.classList.remove('bg-slate-900/80', 'text-slate-400', 'border-slate-800');
        btn.classList.add('bg-blue-950/80', 'text-cyan-300', 'border-cyan-800/50');
      } else {
        btn.classList.remove('bg-blue-950/80', 'text-cyan-300', 'border-cyan-800/50');
        btn.classList.add('bg-slate-900/80', 'text-slate-400', 'border-slate-800');
      }
    }
  }

  setEqualizerActive(active) {
    const eq = document.getElementById('apollo-equalizer');
    if (eq) {
      if (active) {
        eq.classList.add('apollo-eq-active');
      } else {
        eq.classList.remove('apollo-eq-active');
      }
    }
  }

  updateTranscriptText(text) {
    const el = document.getElementById('apollo-transcript-text');
    if (el) {
      el.textContent = text;
    }
  }

  showHudToast(msg) {
    const toast = document.getElementById('apollo-hud-toast');
    const toastMsg = document.getElementById('apollo-hud-toast-msg');
    if (!toast || !toastMsg) return;

    toastMsg.textContent = msg;
    toast.classList.add('visible');

    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => {
      toast.classList.remove('visible');
    }, 4500);
  }

  spawnShockwave(color = '#00f0ff') {
    this.shockwaves.push({
      r: 10,
      maxR: Math.min(this.canvas.width, this.canvas.height) * 0.46,
      opacity: 0.9,
      speed: 2.2,
      color: color
    });
  }

  // -----------------------------------------------------------------------
  // 8. REAL-TIME CANVAS ANIMATION LOOP (GALAXY & ARC CORE)
  // -----------------------------------------------------------------------
  animate() {
    requestAnimationFrame(() => this.animate());

    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const cx = this.canvas.width / 2;
    const cy = this.canvas.height / 2;
    const verticalScale = (this.canvas.height * 0.44) / (this.canvas.width * 0.42);

    // Smooth audio reactivity decay / interpolation
    this.audioLevel += (this.targetAudioLevel - this.audioLevel) * 0.12;
    this.targetAudioLevel *= 0.94;

    // Determine speed & pulse based on state
    let speedMultiplier = 1.0;
    let coreGlowSize = 34 + this.audioLevel * 18;

    if (this.state === 'awake') {
      speedMultiplier = 2.4;
      coreGlowSize = 44 + Math.sin(Date.now() * 0.008) * 6;
    } else if (this.state === 'speaking') {
      speedMultiplier = 2.0;
      coreGlowSize = 40 + Math.sin(Date.now() * 0.012) * 10;
    } else if (this.state === 'listening') {
      speedMultiplier = 1.6;
      coreGlowSize = 36 + Math.sin(Date.now() * 0.006) * 4;
    } else if (this.state === 'muted') {
      speedMultiplier = 0.5;
      coreGlowSize = 24;
    }

    // 1. Draw Expanding Arc Reactor Shockwaves
    for (let i = this.shockwaves.length - 1; i >= 0; i--) {
      const sw = this.shockwaves[i];
      sw.r += sw.speed;
      sw.opacity -= 0.018;

      if (sw.opacity <= 0 || sw.r >= sw.maxR) {
        this.shockwaves.splice(i, 1);
        continue;
      }

      this.ctx.save();
      this.ctx.beginPath();
      this.ctx.ellipse(cx, cy, sw.r, sw.r * verticalScale, 0, 0, Math.PI * 2);
      this.ctx.strokeStyle = sw.color;
      this.ctx.globalAlpha = sw.opacity;
      this.ctx.lineWidth = 1.8;
      this.ctx.shadowColor = sw.color;
      this.ctx.shadowBlur = 8;
      this.ctx.stroke();
      this.ctx.restore();
    }

    // 2. Galactic Core Glow
    const glow = this.ctx.createRadialGradient(cx, cy, 2, cx, cy, coreGlowSize);
    if (this.state === 'awake') {
      glow.addColorStop(0, 'rgba(255, 255, 255, 1.0)');
      glow.addColorStop(0.25, 'rgba(0, 240, 255, 0.95)');
      glow.addColorStop(0.65, 'rgba(168, 85, 247, 0.5)');
      glow.addColorStop(1, 'rgba(6, 9, 19, 0)');
    } else if (this.state === 'speaking') {
      glow.addColorStop(0, 'rgba(255, 255, 255, 1.0)');
      glow.addColorStop(0.25, 'rgba(245, 158, 11, 0.9)');
      glow.addColorStop(0.65, 'rgba(56, 189, 248, 0.5)');
      glow.addColorStop(1, 'rgba(6, 9, 19, 0)');
    } else {
      glow.addColorStop(0, 'rgba(255, 255, 255, 0.95)');
      glow.addColorStop(0.2, 'rgba(56, 189, 248, 0.7)');
      glow.addColorStop(0.55, 'rgba(168, 85, 247, 0.35)');
      glow.addColorStop(1, 'rgba(6, 9, 19, 0)');
    }

    this.ctx.fillStyle = glow;
    this.ctx.beginPath();
    this.ctx.arc(cx, cy, coreGlowSize, 0, Math.PI * 2);
    this.ctx.fill();

    // 3. Render Spiral Galaxy Stars
    this.angleOffset += 0.006 * speedMultiplier;

    this.stars.forEach(star => {
      const currentAngle = star.baseAngle + this.angleOffset * (star.speed * 80);
      // Perspective compression to create 3D elliptical tilted disk
      const x = cx + Math.cos(currentAngle) * star.r;
      const y = cy + Math.sin(currentAngle) * (star.r * verticalScale);

      this.ctx.beginPath();
      this.ctx.arc(x, y, star.size, 0, Math.PI * 2);
      this.ctx.fillStyle = star.color;
      this.ctx.shadowColor = star.color;
      this.ctx.shadowBlur = (this.state === 'awake' || star.r < 25) ? 4 : 0;
      this.ctx.fill();
      this.ctx.shadowBlur = 0;
    });
  }
}

// Global initialization & dual-export for backward compatibility
document.addEventListener('DOMContentLoaded', () => {
  window.hermesApollo = new HermesGalaxy('galaxy-canvas');
  window.hermesGalaxy = window.hermesApollo;
});
