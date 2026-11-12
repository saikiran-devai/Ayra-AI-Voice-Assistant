// Draws the glowing ring: a bright core with translucent ribbons of thin strands around it.
// It rotates gently when idle, speeds up while thinking, and also stretches out of shape while speaking.
const canvas = document.getElementById("ring");
const ctx = canvas.getContext("2d");

const SPINS = { starting: 0.45, sleeping: 0.12, listening: 0.45, thinking: 1.1, speaking: 1.6, offline: 0.2, on_call: 0.15 };  // Radians per second

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
let fullRadius;  // The ring's size with the camera off
let centerX, centerY;  // The ring's centre, which moves down while the camera is on
let cameraBlend = 0;  // 0 with the camera off, 1 with it on
let brightness = 1;  // Dims while Ayra is asleep
const SLEEP_BRIGHTNESS = 0.35;
let spin = 0;  // How far the ring has rotated
let spinSpeed = SPINS.starting;
let energy = 0;  // 0 normally, 1 while Ayra is speaking
let breathPhase = 0;  // Where the title is in its breath
const centerText = document.querySelector(".center");
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
  fullRadius = Math.min(width * 0.25, height * 0.25, 260);
  document.documentElement.style.setProperty("--ring-radius", `${fullRadius}px`);
  placeRing();
}

// With the camera on, the ring shrinks to a glow near the bottom, clear of what the user is pointing at
function placeRing() {
  const small = Math.min(width * 0.13, height * 0.09, 90);
  radius = fullRadius + (small - fullRadius) * cameraBlend;
  centerX = width / 2;
  centerY = height / 2 + (height - small * 2.6 - height / 2) * cameraBlend;
}

function gradient(angle) {
  const g = ctx.createConicGradient(angle, centerX, centerY);
  COLORS.forEach((color, i) => g.addColorStop(i / (COLORS.length - 1), color));
  return g;
}

// How far the ring bulges at a given angle: 1 when idle, a changing stretched shape while speaking
// Rises and falls a bit like speech. The ring's stretch and the title's pulse both follow it.
function speechBeat(time) {
  return 0.7 + 0.3 * Math.sin(time * 6.1) * Math.sin(time * 2.3);
}

function stretch(theta, time) {
  const beat = speechBeat(time);
  return 1 + energy * beat * (
    0.13 * Math.sin(2 * theta + spin * 2)
    + 0.06 * Math.sin(3 * theta - spin * 1.5)
  );
}

function drawRibbon(ribbon, time) {
  const phase = ribbon.offset + spin * ribbon.turn;
  const spread = ribbon.spread * (1 + energy * 1.2);
  const cx = centerX + radius * (ribbon.shift[0] * Math.cos(spin) - ribbon.shift[1] * Math.sin(spin));
  const cy = centerY + radius * (ribbon.shift[0] * Math.sin(spin) + ribbon.shift[1] * Math.cos(spin));

  ctx.globalAlpha = ribbon.alpha * brightness;
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
    ctx.globalAlpha = alpha * brightness;
    ctx.lineWidth = radius * thickness;
    ctx.beginPath();
    for (let i = 0; i <= STEPS; i++) {
      const theta = (i / STEPS) * Math.PI * 2;
      const r = radius * stretch(theta, time);
      const x = centerX + r * Math.cos(theta);
      const y = centerY + r * Math.sin(theta);
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
  cameraBlend += ((document.body.classList.contains("camera-on") ? 1 : 0) - cameraBlend) * blend;
  brightness += ((state === "sleeping" ? SLEEP_BRIGHTNESS : 1) - brightness) * blend * 0.5;  // Fades a little slower, like dozing off
  placeRing();

  // The title breathes with the ring: slowly when listening, quicker as the ring spins faster,
  // and pulsing with the same beat as the ring's stretch while Ayra speaks
  breathPhase += elapsed * (1.2 + spinSpeed * 1.5);  // Slowest while asleep, as the ring barely turns
  const breath = 0.5 + 0.5 * Math.sin(breathPhase);
  const beat = speechBeat(time);
  centerText.style.setProperty("--breath", (1 + 0.02 * breath + 0.035 * energy * beat).toFixed(4));
  centerText.style.setProperty("--glow", (0.25 + 0.4 * breath * (1 - energy) + energy * (0.45 + 0.55 * beat)).toFixed(3));

  draw(time);
  requestAnimationFrame(animate);
}

// The label under the title, faded out and back in when Ayra's state changes
const STATUS_LABELS = {
  starting: "Waking up",
  sleeping: "Say “Hey Ayra”",
  listening: "Listening",
  thinking: "Working",
  speaking: "Speaking",
  on_call: "On a call",
  offline: "Reconnecting",
};
const STATUS_FADE_MS = 250;  // Matches the transition in style.css
const statusPill = document.getElementById("status");
const statusLabel = statusPill.querySelector(".label");
let shownState = "starting";
let statusTimer = null;

function showStatus(newState) {
  if (newState === shownState || !(newState in STATUS_LABELS)) return;
  shownState = newState;
  clearTimeout(statusTimer);
  statusPill.classList.add("changing");
  statusTimer = setTimeout(() => {
    statusPill.dataset.state = newState;
    statusLabel.textContent = STATUS_LABELS[newState];
    statusPill.classList.remove("changing");
  }, STATUS_FADE_MS);
}

async function pollStatus() {
  try {
    const response = await fetch("/status");
    state = (await response.json()).state;
  } catch {
    state = "offline";
  }
  showStatus(state);
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

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// camera.js calls this. Without animation, jump straight to the new place.
function cameraChanged() {
  if (!reducedMotion) return;
  cameraBlend = document.body.classList.contains("camera-on") ? 1 : 0;
  placeRing();
  draw(0);
}

resize();
pollStatus();
if (reducedMotion) {
  draw(0);
} else {
  requestAnimationFrame(animate);
}
