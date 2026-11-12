"""Ayra, a voice assistant: microphone -> Flux speech-to-text -> Claude -> Sarvam text-to-speech -> speakers.

Run this file to start the website and the voice assistant together.
"""
import asyncio
import logging
import sys
import threading

from dotenv import load_dotenv

load_dotenv()  # API keys from .env, before the SDKs below are imported

from flask import Flask, abort, jsonify, render_template, request

from agent import state, vision
from agent.llm import reply_worker
from voice import text_to_speech as tts
from voice.speech_to_text import listen

STATIC_FILE_TYPES = (".js", ".css", ".png", ".ico", ".svg", ".jpg", ".webp", ".woff2")
PORT = 5050  # macOS often uses port 5000 for AirPlay

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
app.config["MAX_CONTENT_LENGTH"] = 3_000_000  # Camera frames are well under this
voice_loop = None  # The voice assistant's event loop, which runs in its own thread


def stop_ayra(source: str) -> None:
    """Stop Ayra talking or working on a reply, or resume it after a phone call. Safe to call from any thread."""
    def stop() -> None:
        if state.end_call():
            print(f"▶️  Ayra is listening again, resumed from {source}")
        elif state.current in (state.THINKING, state.SPEAKING):
            print(f"✋ Stopped from {source}")
            tts.interrupt()

    if voice_loop is not None:
        voice_loop.call_soon_threadsafe(stop)


@app.before_request
def only_web_files_from_static():
    """Never serve anything from static/ except the page's own files, so a stray sign-in file can't leak"""
    if request.path.startswith("/static/") and not request.path.endswith(STATIC_FILE_TYPES):
        abort(404)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/status")
def status():
    """Tells the page what Ayra is doing, so the ring can react"""
    return jsonify(state=state.current)


@app.post("/stop")
def stop():
    """Lets the page stop Ayra, for when the mic can't hear the user over Ayra's own voice"""
    stop_ayra("the website")
    return "", 204


@app.post("/camera/frame")
def camera_frame():
    """The page sends the camera's newest picture about once a second while camera mode is on"""
    jpeg = request.get_data(cache=False)
    if request.mimetype != "image/jpeg" or not jpeg.startswith(b"\xff\xd8") or len(jpeg) > vision.MAX_FRAME_BYTES:
        abort(400)
    vision.save_frame(jpeg)
    return "", 204


@app.post("/camera/off")
def camera_off():
    vision.camera_off()
    return "", 204


async def run_voice() -> None:
    global voice_loop
    voice_loop = asyncio.get_running_loop()
    if sys.stdin.isatty():
        # Pressing Enter in the terminal stops Ayra, as the mic can't always hear "stop" over Ayra's voice
        voice_loop.add_reader(sys.stdin, lambda: (sys.stdin.readline(), stop_ayra("the keyboard")))

    # Finished turns wait here, so slow replies never hold up the microphone audio
    turn_queue = asyncio.Queue()
    reply_task = asyncio.create_task(reply_worker(turn_queue))  # Held so Python doesn't clean up the running task
    await listen(turn_queue)


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # The page checks /status constantly, so don't log each request
    threading.Thread(target=lambda: asyncio.run(run_voice()), daemon=True).start()
    print(f"🌐 Open http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT)
