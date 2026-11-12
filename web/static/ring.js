// Draws the glowing ring: a bright core with translucent ribbons of thin strands around it.
// It rotates gently when idle, speeds up while thinking, and also stretches out of shape while speaking.
const canvas = document.getElementById("ring");
const ctx = canvas.getContext("2d");

const SPINS = { starting: 0.45, listening: 0.45, thinking: 1.1, speaking: 1.6, offline: 0.2, on_call: 0.15 };  // Radians per second

const COLORS = ["#16f2e6", "#14b9e0", "#3f6fe6", "#a44ff2", "#7b3fe4", "#2a8fe0", "#16f2e6"];
const COLOR_START = -2.4;  // Where teal starts, before the ring has rotated
const RIBBONS = [
  { offset: 0.0, wobble: 0.05, spread: 0.2, turn: 1.0, alpha: 0.11, shift: [-0.02, -0.03] },
  { offset: 2.1, wobble: 0.06, spread: 0.22, turn: -0.7, alpha: 0.09, shift: [0.03, 0.0] },
  { offset: 4.2, wobble: 0.07, spread: 0.26, turn: 0.55, alpha: 0.12, shift: [-0.02, 0.1] },
];
const STRANDS = 40;
const STEPS = 120;

const BLEND_RATE = 6;  // How quickly the ring moves between states
const STATUS_POLL_MS = 150;

let width, height, radius;
let spin = 0;  // How far the ring has rotated
let spinSpeed = SPINS.starting;
let energy = 0;  // 0 normally, 1 while Ayra is speaking
let state = "starting";
let lastTime = null;

function resize() {
  const scale = Math.min(window.devicePixelRatio || 1, 2);  // Sharper than this only costs battery
  width = window.innerWidth;
  height = window.innerHeight;
  canvas.width = width * scale;
  canvas.height = height * scale;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  // The ribbons reach about 1.8 times the radius when stretched, so this keeps them on screen
  radius = Math.min(width * 0.25, height * 0.25, 260);
  document.documentElement.style.setProperty("--ring-radius", `${radius}px`);
}

function gradient(angle) {
  const g = ctx.createConicGradient(angle, width / 2, height / 2);
  COLORS.forEach((color, i) => g.addColorStop(i / (COLORS.length - 1), color));
  return g;
}

// How far the ring bulges at a given angle: 1 when idle, a changing stretched shape while speaking
function stretch(theta, time) {
  const beat = 0.7 + 0.3 * Math.sin(time * 6.1) * Math.sin(time * 2.3);  // Rises and falls a bit like speech
  return 1 + energy * beat * (
    0.13 * Math.sin(2 * theta + spin * 2)
    + 0.06 * Math.sin(3 * theta - spin * 1.5)
  );
}

function drawRibbon(ribbon, time) {
  const phase = ribbon.offset + spin * ribbon.turn;
  const spread = ribbon.spread * (1 + energy * 1.2);
  const cx = width / 2 + radius * (ribbon.shift[0] * Math.cos(spin) - ribbon.shift[1] * Math.sin(spin));
  const cy = height / 2 + radius * (ribbon.shift[0] * Math.sin(spin) + ribbon.shift[1] * Math.cos(spin));

  ctx.globalAlpha = ribbon.alpha;
  ctx.lineWidth = 1;

  for (let s = 0; s < STRANDS; s++) {
    const fraction = s / STRANDS;
    ctx.beginPath();
    for (let i = 0; i <= STEPS; i++) {
      const theta = (i / STEPS) * Math.PI * 2;
      // The strands bunch together on one side of the ring and fan out on the other
      const fan = 0.5 + 0.5 * Math.sin(theta + phase);
      const r = radius * stretch(theta, time) * (1
        + spread * fraction * fan * fan
        + ribbon.wobble * Math.sin(2 * theta + phase * 1.3) * (0.4 + fraction));
      const x = cx + r * Math.cos(theta);
      const y = cy + r * Math.sin(theta);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
  }
}

function drawCore(time) {
  // Wide, faint strokes build the glow around a bright line
  [[0.3, 0.05], [0.18, 0.1], [0.1, 0.2], [0.055, 1]].forEach(([thickness, alpha]) => {
    ctx.globalAlpha = alpha;
    ctx.lineWidth = radius * thickness;
    ctx.beginPath();
    for (let i = 0; i <= STEPS; i++) {
      const theta = (i / STEPS) * Math.PI * 2;
      const r = radius * stretch(theta, time);
      const x = width / 2 + r * Math.cos(theta);
      const y = height / 2 + r * Math.sin(theta);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.stroke();
  });
}

function draw(time) {
  ctx.globalCompositeOperation = "source-over";
  ctx.globalAlpha = 1;
  ctx.clearRect(0, 0, width, height);
  ctx.globalCompositeOperation = "lighter";
  ctx.strokeStyle = gradient(COLOR_START + spin);
  RIBBONS.forEach((ribbon) => drawRibbon(ribbon, time));
  drawCore(time);
}

function animate(now) {
  const time = now / 1000;
  const elapsed = lastTime === null ? 0 : Math.min(time - lastTime, 0.1);
  lastTime = time;

  const blend = 1 - Math.exp(-elapsed * BLEND_RATE);
  energy += ((state === "speaking" ? 1 : 0) - energy) * blend;
  spinSpeed += ((SPINS[state] ?? SPINS.listening) - spinSpeed) * blend;
  spin += spinSpeed * elapsed;

  draw(time);
  requestAnimationFrame(animate);
}

async function pollStatus() {
  try {
    const response = await fetch("/status");
    state = (await response.json()).state;
  } catch {
    state = "offline";
  }
  setTimeout(pollStatus, STATUS_POLL_MS);
}

window.addEventListener("resize", () => {
  resize();
  draw(performance.now() / 1000);
});

// Stop Ayra with a click or tap anywhere, or with Escape or Space
function stopAyra() {
  if (state === "speaking" || state === "thinking" || state === "on_call") {
    fetch("/stop", { method: "POST" }).catch(() => {});
  }
}

document.getElementById("stop").addEventListener("click", stopAyra);
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" || (event.key === " " && event.target === document.body)) {
    event.preventDefault();
    stopAyra();
  }
});

resize();
pollStatus();
if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  draw(0);
} else {
  requestAnimationFrame(animate);
}
