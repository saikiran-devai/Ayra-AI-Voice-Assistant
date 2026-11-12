// Camera mode: shows the camera full screen and sends the server a small picture about once a second.
// While pictures keep arriving, each question goes to Claude with the newest one, about whatever is in the middle of the view.
const FRAME_INTERVAL_MS = 1000;
const FRAME_MAX_SIDE = 768;  // Big enough to read labels, small enough to send and process quickly
const FRAME_QUALITY = 0.7;

const video = document.getElementById("camera");
const toggleButton = document.getElementById("camera-toggle");
const flipButton = document.getElementById("flip");
const snapshot = document.createElement("canvas");

let stream = null;
let frameTimer = null;
let facing = "environment";  // The back camera on a phone. Laptops just have the one.
let sending = false;

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    alert("This browser can't use the camera here. On a phone, the page needs to be opened over HTTPS.");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch (error) {
    alert(`Couldn't open the camera: ${error.message}`);
    return;
  }
  video.srcObject = stream;
  video.classList.toggle("mirrored", facing === "user");
  document.body.classList.add("camera-on");
  toggleButton.setAttribute("aria-pressed", "true");
  toggleButton.setAttribute("aria-label", "Turn camera off");
  const cameras = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === "videoinput");
  flipButton.hidden = cameras.length < 2;
  cameraChanged();
  clearInterval(frameTimer);
  frameTimer = setInterval(sendFrame, FRAME_INTERVAL_MS);
  sendFrame();
}

function stopCamera() {
  clearInterval(frameTimer);
  frameTimer = null;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  document.body.classList.remove("camera-on");
  toggleButton.setAttribute("aria-pressed", "false");
  toggleButton.setAttribute("aria-label", "Turn camera on");
  flipButton.hidden = true;
  cameraChanged();
  fetch("/camera/off", { method: "POST" }).catch(() => {});
}

function sendFrame() {
  if (sending || !video.videoWidth) return;  // Skip a beat rather than pile up pictures on a slow connection
  const scale = Math.min(1, FRAME_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
  snapshot.width = Math.round(video.videoWidth * scale);
  snapshot.height = Math.round(video.videoHeight * scale);
  snapshot.getContext("2d").drawImage(video, 0, 0, snapshot.width, snapshot.height);
  sending = true;
  snapshot.toBlob((blob) => {
    if (!blob || !stream) {
      sending = false;
      return;
    }
    fetch("/camera/frame", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob })
      .catch(() => {})
      .finally(() => { sending = false; });
  }, "image/jpeg", FRAME_QUALITY);
}

toggleButton.addEventListener("click", () => (stream ? stopCamera() : startCamera()));

flipButton.addEventListener("click", async () => {
  facing = facing === "environment" ? "user" : "environment";
  stream?.getTracks().forEach((track) => track.stop());
  await startCamera();
});

// Like Gemini, the camera goes off when the page is hidden, such as switching apps or locking the phone
document.addEventListener("visibilitychange", () => {
  if (document.hidden && stream) stopCamera();
});
window.addEventListener("pagehide", () => stream && navigator.sendBeacon("/camera/off"));
