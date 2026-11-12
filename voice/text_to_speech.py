import asyncio
import atexit
import re
import subprocess
import time

from sarvamai import AsyncSarvamAI
from sarvamai.core.api_error import ApiError

from agent import state

MODEL = "bulbul:v4-flash"
VOICE = "shubh_enhi_ads"
LANGUAGE = "en-IN"
PACE = 1  # Speaking pace: 1 is normal, from 0.5 to 2.0
SAMPLE_RATE = 24000
LEAD_IN_SECONDS = 0.3  # Silence played just before the voice, while the player starts up
FILLER = "Let me check."  # Said as soon as a tool starts, so the wait isn't silent
FILLER_PAUSE_SECONDS = 0.4  # Silence after the filler, so the answer doesn't run straight on from it
TAIL_PADDING_SECONDS = 0.25  # Silence after the last word, as the player stops slightly before its audio has finished
ECHO_TAIL_SECONDS = 0.5  # Keep treating the mic as echo this long after Ayra finishes
PLAYER_RETRY_SECONDS = 0.3  # Wait before restarting a player that couldn't open the speaker

FFPLAY = ["ffplay", "-loglevel", "error", "-nodisp"]  # Audio only, no window

speaking = asyncio.Event()  # Set from the moment Ayra starts preparing speech until echo could have died away
last_spoken = ""  # What Ayra said most recently, used to recognise its own voice in the mic

turn_started = None  # When Ayra began working on the current reply, for the timing line



class Say(str):
    """A fixed line for Ayra to say straight away, without asking Claude, like "Yes?" after "Hey Ayra" """


_player = None
_interrupted = False
_clauses = None


def keep_output_awake() -> None:
    """Play silence until the program exits, so Bluetooth headphones don't doze off between replies"""
    silence = subprocess.Popen(
        [*FFPLAY, "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono"],
        stdin=subprocess.DEVNULL,
    )
    atexit.register(silence.kill)


async def _start_player() -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *FFPLAY,
        "-autoexit",                # Quit when the audio ends
        "-f", "s16le",              # Input format: raw 16-bit PCM (linear16)
        "-ar", str(SAMPLE_RATE),
        "-ch_layout", "mono",
        "-i", "pipe:0",             # Read the audio from stdin
        stdin=asyncio.subprocess.PIPE,
    )


async def _play(data: bytes) -> None:
    """Send audio to the player. If the player quit by itself, usually because macOS was switching speakers
    as it started, start a new one and carry on, rather than staying silent for the rest of the reply."""
    global _player
    try:
        _player.stdin.write(data)
        await _player.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        if _interrupted:
            raise
        print("\n🔈 The speaker didn't open, trying again")
        await asyncio.sleep(PLAYER_RETRY_SECONDS)
        _player = await _start_player()
        _player.stdin.write(_silence(LEAD_IN_SECONDS) + data)
        await _player.stdin.drain()  # A second failure ends this reply, so it can't loop


def _speakable(text: str) -> str:
    """Remove markdown such as **bold** or list markers, so they aren't read out"""
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text)
    return re.sub(r"[*_#`]+", "", text).strip()


def _silence(seconds: float) -> bytes:
    return bytes(int(SAMPLE_RATE * seconds) * 2)  # 2 bytes per sample


async def speak_clauses(client: AsyncSarvamAI, clauses: asyncio.Queue) -> None:
    """Say each clause as it arrives on the queue, until None arrives or interrupt() stops it"""
    global last_spoken, _player, _interrupted, _clauses
    last_spoken = ""
    _interrupted = False
    _player = None
    _clauses = clauses

    try:
        while (clause := await clauses.get()) is not None and not _interrupted:
            last_spoken = f"{last_spoken} {clause}".strip()
            speaking.set()

            async for chunk in client.text_to_speech.convert_stream(
                text=_speakable(clause) or clause,
                language_code=LANGUAGE,
                speaker=VOICE,
                model=MODEL,
                pace=PACE,
                speech_sample_rate=SAMPLE_RATE,
                output_audio_codec="linear16",  # Raw audio, without a WAV header
            ):
                if _interrupted:
                    return

                # Start the player only now, so the lead-in runs straight into the voice
                # instead of running out while the first audio is still on its way
                if _player is None:
                    _player = await _start_player()
                    _player.stdin.write(_silence(LEAD_IN_SECONDS))
                    state.update(state.SPEAKING)
                    if turn_started is not None:
                        print(f"\n⏱️  First audio after {time.perf_counter() - turn_started:.1f}s")

                await _play(chunk)

            if clause == FILLER and _player is not None and not _interrupted:
                await _play(_silence(FILLER_PAUSE_SECONDS))

        if _player is None or _interrupted:
            return

        # Let the player finish whatever audio it still has buffered
        await _play(_silence(TAIL_PADDING_SECONDS))
        _player.stdin.close()
        await _player.wait()
        state.update(state.LISTENING)
        if _player.returncode == 0:  # Finished by itself, rather than being interrupted
            await asyncio.sleep(ECHO_TAIL_SECONDS)  # Flux may still be transcribing Ayra's last words
    except (BrokenPipeError, ConnectionResetError):
        if not _interrupted:
            print("⚠️  Couldn't play audio. Check the Mac's sound output.")
        # Otherwise interrupt() closed the player while audio was still arriving
    except ApiError as e:
        print(f"⚠️  Sarvam text-to-speech error {e.status_code}: {e.body}")
    finally:
        interrupt()
        state.update(state.LISTENING)
        speaking.clear()


def interrupt() -> None:
    """Stop playback immediately, or cancel it if the first audio hasn't arrived yet"""
    global _interrupted
    _interrupted = True
    if _clauses is not None:
        _clauses.put_nowait(None)  # Wake speak_clauses if it's waiting for the next clause
    if _player is not None and _player.returncode is None:
        _player.kill()
