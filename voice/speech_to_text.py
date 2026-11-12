import asyncio
import re

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from agent import state
from agent.tools import contact_names
from voice import text_to_speech as tts
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
NAME = r"(?:ayra|aira|ayira|eira|ira|ara|era)"  # How Flux tends to hear "Ayra"
WAKE_PHRASE = re.compile(rf"\b(?:hey|hi|hello|okay|ok)\b[\s,.!-]*{NAME}\b", re.IGNORECASE)  # "Hey Ayra"
SLEEP_PHRASE = re.compile(
    rf"\b(?:bye|bye-bye|goodbye|good bye|good night)\b[\s,.!-]*{NAME}\b"  # "Bye Ayra"
    rf"|\b{NAME}\b[\s,.!-]*(?:go to sleep|bye\b)",  # "Ayra, go to sleep" or "Ayra, bye"
    re.IGNORECASE,
)
WAKE_REPLY = "Yes?"
SLEEP_REPLY = "Bye! I'll be here when you need me."
KEYTERMS = ["Ayra", "stop", "Jyotirlinga", "Jyotirlingas", "Jyotirling", "Jyotirlings", "Swiggy", "Instamart"]  # Unusual words Flux should listen out for

GREEN = "\033[92m"
YELLOW = "\033[93m"
ORANGE = "\033[91m"  # Bright red, as terminals have no standard orange
RED = "\033[31m"
UNCLEAR_CONFIDENCE = 0.7  # Words below this are marked for Claude as possibly misheard, like "courier" for "cool"
UNCLEAR_MARK = "(?)"
RESET = "\033[0m"


def confidence_color(confidence: float) -> str:
    if confidence >= 0.90:
        return GREEN
    if confidence >= 0.80:
        return YELLOW
    if confidence >= 0.70:
        return ORANGE
    return RED


def mark_unclear(words: list) -> str:
    """The turn's text for Claude, with words speech-to-text wasn't sure of marked, so Claude can allow for a mishearing"""
    return " ".join(f"{w.word}{UNCLEAR_MARK}" if w.confidence < UNCLEAR_CONFIDENCE else w.word for w in words)


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
            print("🎙️  Listening to your microphone - say \"Hey Ayra\" to wake me, \"Bye Ayra\" to put me to sleep, Ctrl+C to quit")
            state.update(state.LISTENING)
        bytes_sent += len(chunk)
    await process.wait()
    print("⚠️  The microphone stopped sending audio - restart Ayra")


def route_turn(user_text: str | None, heard: str, kept_words: list, turn_queue: asyncio.Queue) -> None:
    """Decide what a finished turn means: wake Ayra, put it to sleep, a question to answer, or nothing.
    user_text is the user's own words, after Ayra's echo is removed, and heard is everything Flux heard."""
    if state.awake:
        # Checked on everything heard, as "Bye Ayra" said over Ayra's voice counts as a command and gets cut from user_text.
        # Ayra never says it, so it can't be Ayra's own echo.
        if SLEEP_PHRASE.search(heard) and not SLEEP_PHRASE.search(tts.last_spoken):
            print(f"🎤 {heard}\n😴 Going to sleep - say \"Hey Ayra\" to wake me")
            tts.interrupt()
            state.sleep()
            turn_queue.put_nowait(tts.Say(SLEEP_REPLY))
            return
    elif user_text:
        wake = WAKE_PHRASE.search(user_text)
        if not wake:
            print(f"💤 Asleep, so ignored: {user_text}")
            return
        print("👋 Awake")
        state.wake()
        if not re.search(r"[a-z0-9]", user_text[wake.end():], re.IGNORECASE):
            turn_queue.put_nowait(tts.Say(WAKE_REPLY))  # Just "Hey Ayra", so answer at once, without Claude
            return

    if user_text:
        print(f"🎤 {user_text}")
        print_confidence(kept_words)
        turn_queue.put_nowait(mark_unclear(kept_words) or user_text)


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
            if message.event == "EndOfTurn":
                route_turn(user_text, message.transcript, turn_filter.kept_words, turn_queue)
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
