import asyncio
import re

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from agent import state
from agent.tools import contact_names
from voice.barge_in import TurnFilter

# Microphone input for ffmpeg, in avfoundation's "video:audio" format (no video, default audio device).
# To pick a specific mic, list devices with: ffmpeg -f avfoundation -list_devices true -i ""
# then use its index, e.g. ":1"
MIC_DEVICE = ":default"
MIC_SAMPLE_RATE = 16000
MIC_CHUNK_BYTES = 1024  # About 32ms of audio
MIC_READY_BYTES = MIC_SAMPLE_RATE  # About 0.5s of audio at 2 bytes per sample, enough to know the mic has settled
MAX_CONTACT_KEYTERMS = 50
RESUME_PHRASE = re.compile(r"\b(ayra|ira|aira)\b.*\b(back|resume|done|finished|listen)", re.IGNORECASE)  # "Ayra, I'm back"
KEYTERMS = ["Ayra", "Jyotirlinga", "Jyotirlingas", "Jyotirling", "Jyotirlings", "Swiggy", "Instamart"]  # Unusual words Flux should listen out for

GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[91m"  # Bright red, as terminals have no standard orange
RED = "\033[31m"
RESET = "\033[0m"


def confidence_color(confidence: float) -> str:
    if confidence >= 0.90:
        return GREEN
    if confidence >= 0.80:
        return YELLOW
    if confidence >= 0.70:
        return ORANGE
    return RED


def print_confidence(words: list) -> None:
    colored = [f"{confidence_color(w.confidence)}{w.word}({w.confidence:.2f}){RESET}" for w in words]
    print(f"   📝 {' | '.join(colored)}")


async def stream_microphone(connection) -> None:
    """Capture the microphone as linear16 PCM with ffmpeg and send it to Flux"""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",           # Only print real errors
        "-nostats",                     # No progress output
        "-f", "avfoundation",           # Input: macOS capture devices
        "-i", MIC_DEVICE,
        "-f", "s16le",                  # Output: 16-bit little-endian PCM (linear16)
        "-ar", str(MIC_SAMPLE_RATE),
        "-ac", "1",                     # Mono
        "-",                            # Write to stdout
        stdout=asyncio.subprocess.PIPE,
    )

    # Only say Ayra is listening once audio is really flowing, so the user's first words aren't lost
    bytes_sent = 0
    while chunk := await process.stdout.read(MIC_CHUNK_BYTES):
        await connection.send_media(chunk)
        if bytes_sent < MIC_READY_BYTES <= bytes_sent + len(chunk):
            print("🎙️  Listening to your microphone - press Ctrl+C to stop")
            state.update(state.LISTENING)
        bytes_sent += len(chunk)
    await process.wait()
    print("⚠️  The microphone stopped sending audio - restart Ayra")


async def listen(turn_queue: asyncio.Queue) -> None:
    """Stream the microphone to Flux and put each finished turn from the user on the queue"""
    client = AsyncDeepgramClient()  # Reads DEEPGRAM_API_KEY from the environment
    turn_filter = TurnFilter()

    def on_message(message) -> None:
        # Every event goes through the filter so the user can interrupt mid-reply,
        # but only a finished turn with the user's own words in it gets printed and answered
        if getattr(message, "event", None) and message.transcript:
            if state.current == state.ON_CALL:
                # On a phone call, so only listen for the user saying they're back
                if message.event == "EndOfTurn":
                    if RESUME_PHRASE.search(message.transcript):
                        state.end_call()
                        print("▶️  Ayra is listening again")
                    else:
                        print("📞 Ignored during your call")
                return
            user_text = turn_filter.process(message.words, message.event)
            if user_text:
                print(f"🎤 {user_text}")
                print_confidence(turn_filter.kept_words)
                turn_queue.put_nowait(user_text)
        elif getattr(message, "type", None) == "Connected":
            print("✅ Connected to Deepgram Flux - Ready for audio!")

    try:
        async with client.listen.v2.connect(
            model="flux-general-en",
            encoding="linear16",
            sample_rate=str(MIC_SAMPLE_RATE),
            eot_threshold="0.85",   # Be surer the user has finished, so a thinking pause doesn't split a sentence (default 0.7)
            eot_timeout_ms="8000",  # Allow longer silence before forcing the turn to end (default 5000)
            keyterm=KEYTERMS + contact_names()[:MAX_CONTACT_KEYTERMS],  # Contact names are easy to mishear
        ) as connection:
            connection.on(EventType.OPEN, lambda _: print("Connection opened"))
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, lambda _: print("Connection closed"))
            connection.on(EventType.ERROR, lambda error: print(f"Caught: {error}"))

            deepgram_task = asyncio.create_task(connection.start_listening())

            try:
                await stream_microphone(connection)
            except Exception as e:
                print(f"Error during audio capture: {e}")

            # The microphone has stopped, so give Flux a moment to finish up
            try:
                await asyncio.wait_for(deepgram_task, timeout=60)
            except asyncio.TimeoutError:
                print("Stream timeout after 60 seconds")
                deepgram_task.cancel()

    except Exception as e:
        print(f"Caught: {e}")
