// Neural Brain Cosmos Canvas Simulation
class NeuralCosmos {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');
    this.nodes = [];
    this.pulses = [];
    this.stars = [];
    this.width = 0;
    this.height = 0;
    this.mouse = { x: -1000, y: -1000, active: false };

    this.init();
  }

  init() {
    this.resize();
    window.addEventListener('resize', () => this.resize());
    
    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      this.mouse.x = e.clientX - rect.left;
      this.mouse.y = e.clientY - rect.top;
      this.mouse.active = true;
    });

    this.canvas.addEventListener('mouseleave', () => {
      this.mouse.active = false;
    });

    this.createStars();
    this.createBrainNodes();
    this.animate();
  }

  resize() {
    const parent = this.canvas.parentElement;
    this.width = this.canvas.width = parent.clientWidth;
    this.height = this.canvas.height = parent.clientHeight;
    this.createStars();
    this.createBrainNodes();
  }

  createStars() {
    this.stars = [];
    const count = Math.floor((this.width * this.height) / 3500);
    for (let i = 0; i < count; i++) {
      this.stars.push({
        x: Math.random() * this.width,
        y: Math.random() * this.height,
        radius: Math.random() * 1.2 + 0.3,
        alpha: Math.random() * 0.7 + 0.2,
        twinkleSpeed: (Math.random() * 0.02 + 0.005) * (Math.random() > 0.5 ? 1 : -1)
      });
    }
  }

  createBrainNodes() {
    this.nodes = [];
    this.pulses = [];

    // Center of brain nebula (shifted right as in the mockup)
    const centerX = this.width * 0.65;
    const centerY = this.height * 0.5;
    const scale = Math.min(this.width / 1100, this.height / 350, 1.2);

    // Define approximate 2D brain hemisphere coordinates
    const brainCoords = [
      // Left hemisphere / core
      { x: -50, y: -20 }, { x: -30, y: -50 }, { x: -10, y: -65 },
      { x: -40, y: 10 }, { x: -60, y: -35 }, { x: -20, y: -10 },
      { x: -35, y: 35 }, { x: -15, y: 30 }, { x: -45, y: -5 },
      // Right hemisphere / core
      { x: 50, y: -20 }, { x: 30, y: -50 }, { x: 10, y: -65 },
      { x: 40, y: 10 }, { x: 60, y: -35 }, { x: 20, y: -10 },
      { x: 35, y: 35 }, { x: 15, y: 30 }, { x: 45, y: -5 },
      // Central lobes & stem
      { x: 0, y: -40 }, { x: 0, y: -10 }, { x: 0, y: 20 }, { x: 0, y: 55 },
      // Outer orbital nodes
      { x: -180, y: -70, isAnchor: 'project-plan' },
      { x: 200, y: -80, isAnchor: 'research' },
      { x: 230, y: 20, isAnchor: 'system-architecture' },
      { x: 220, y: 90, isAnchor: 'goals' },
      { x: -30, y: 95, isAnchor: 'strategy-notes' },
      { x: -150, y: 70, isAnchor: 'market-analysis' },
      // Intermediate synapsing nodes
      { x: -110, y: -40 }, { x: -90, y: 20 }, { x: -120, y: 50 },
      { x: 110, y: -45 }, { x: 140, y: -10 }, { x: 130, y: 60 },
      { x: 80, y: 75 }, { x: -80, y: 75 }
    ];

    brainCoords.forEach((pt, i) => {
      this.nodes.push({
        id: i,
        x: centerX + pt.x * scale,
        y: centerY + pt.y * scale,
        baseX: centerX + pt.x * scale,
        baseY: centerY + pt.y * scale,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
        radius: pt.isAnchor ? 3.5 : (Math.random() * 2 + 1.5),
        color: pt.isAnchor ? '#00f0ff' : (i % 3 === 0 ? '#38bdf8' : (i % 2 === 0 ? '#c084fc' : '#ec4899')),
        glowColor: i % 2 === 0 ? 'rgba(168, 85, 247, 0.4)' : 'rgba(56, 189, 248, 0.4)',
        isAnchor: pt.isAnchor || null
      });
    });
  }

  triggerPulse() {
    if (this.nodes.length < 2) return;
    const fromIdx = Math.floor(Math.random() * this.nodes.length);
    // Find nearby node
    const nearby = this.nodes.filter((n, idx) => {
      if (idx === fromIdx) return false;
      const dx = n.x - this.nodes[fromIdx].x;
      const dy = n.y - this.nodes[fromIdx].y;
      return Math.sqrt(dx * dx + dy * dy) < 180;
    });

    if (nearby.length > 0) {
      const target = nearby[Math.floor(Math.random() * nearby.length)];
      this.pulses.push({
        x1: this.nodes[fromIdx].x,
        y1: this.nodes[fromIdx].y,
        x2: target.x,
        y2: target.y,
        progress: 0,
        speed: 0.025 + Math.random() * 0.02,
        color: Math.random() > 0.5 ? '#00f0ff' : '#ec4899'
      });
    }
  }

  animate() {
    requestAnimationFrame(() => this.animate());

    this.ctx.clearRect(0, 0, this.width, this.height);

    // Draw background nebula clouds
    const centerX = this.width * 0.65;
    const centerY = this.height * 0.5;

    // Outer Purple/Magenta Glow
    const gradient = this.ctx.createRadialGradient(
      centerX, centerY, 10,
      centerX, centerY, Math.max(this.width * 0.35, 220)
    );
    gradient.addColorStop(0, 'rgba(147, 51, 234, 0.28)');
    gradient.addColorStop(0.35, 'rgba(59, 130, 246, 0.18)');
    gradient.addColorStop(0.7, 'rgba(168, 85, 247, 0.06)');
    gradient.addColorStop(1, 'rgba(6, 9, 19, 0)');

    this.ctx.fillStyle = gradient;
    this.ctx.fillRect(0, 0, this.width, this.height);

    // Draw Stars
    this.stars.forEach(star => {
      star.alpha += star.twinkleSpeed;
      if (star.alpha > 0.95 || star.alpha < 0.15) {
        star.twinkleSpeed = -star.twinkleSpeed;
      }
      this.ctx.beginPath();
      this.ctx.arc(star.x, star.y, star.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = `rgba(255, 255, 255, ${star.alpha})`;
      this.ctx.fill();
    });

    // Draw Interconnected Filaments
    for (let i = 0; i < this.nodes.length; i++) {
      for (let j = i + 1; j < this.nodes.length; j++) {
        const n1 = this.nodes[i];
        const n2 = this.nodes[j];
        const dx = n2.x - n1.x;
        const dy = n2.y - n1.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const maxDist = (n1.isAnchor || n2.isAnchor) ? 170 : 110;

        if (dist < maxDist) {
          const alpha = (1 - dist / maxDist) * 0.45;
          this.ctx.beginPath();
          this.ctx.moveTo(n1.x, n1.y);
          this.ctx.lineTo(n2.x, n2.y);
          this.ctx.strokeStyle = `rgba(139, 92, 246, ${alpha})`;
          this.ctx.lineWidth = 1;
          this.ctx.stroke();
        }
      }
    }

    // Trigger occasional synaptic pulses
    if (Math.random() < 0.08) {
      this.triggerPulse();
    }

    // Update & draw pulses
    for (let i = this.pulses.length - 1; i >= 0; i--) {
      const p = this.pulses[i];
      p.progress += p.speed;
      if (p.progress >= 1) {
        this.pulses.splice(i, 1);
        continue;
      }

      const currX = p.x1 + (p.x2 - p.x1) * p.progress;
      const currY = p.y1 + (p.y2 - p.y1) * p.progress;

      this.ctx.beginPath();
      this.ctx.arc(currX, currY, 2.5, 0, Math.PI * 2);
      this.ctx.fillStyle = p.color;
      this.ctx.shadowColor = p.color;
      this.ctx.shadowBlur = 10;
      this.ctx.fill();
      this.ctx.shadowBlur = 0; // reset
    }

    // Update and draw Nodes
    this.nodes.forEach(node => {
      // Gentle drift around base position
      node.x += node.vx;
      node.y += node.vy;

      if (Math.abs(node.x - node.baseX) > 12) node.vx = -node.vx;
      if (Math.abs(node.y - node.baseY) > 12) node.vy = -node.vy;

      // Mouse subtle interaction
      if (this.mouse.active) {
        const mdx = this.mouse.x - node.x;
        const mdy = this.mouse.y - node.y;
        const mDist = Math.sqrt(mdx * mdx + mdy * mdy);
        if (mDist < 100) {
          node.x -= (mdx / mDist) * 1.5;
          node.y -= (mdy / mDist) * 1.5;
        }
      }

      // Draw Node Outer Glow
      this.ctx.beginPath();
      this.ctx.arc(node.x, node.y, node.radius * 2.5, 0, Math.PI * 2);
      this.ctx.fillStyle = node.glowColor;
      this.ctx.fill();

      // Draw Node Core
      this.ctx.beginPath();
      this.ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = node.color;
      this.ctx.fill();
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.neuralCosmos = new NeuralCosmos('cosmos-canvas');
});
