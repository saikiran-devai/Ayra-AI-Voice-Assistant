"""Decide whether speech heard while Ayra talks is the user, or Ayra's own voice from the speakers.

On speakers the mic hears Ayra, and Flux can mishear that as almost anything, so its words can't be
told apart from the user's. There, only a command word interrupts and everything else is ignored.
With headphones there's no echo, so any clear speech can interrupt.
"""
import re

from voice import text_to_speech as tts

HEADPHONES = False  # True only if the mic can't hear Ayra at all, as any clear speech then interrupts. Some wired headsets still leak.
STOP_WORDS = {"stop", "wait", "ayra", "aira", "enough"}  # Interrupt Ayra, with headphones or on speakers
MIN_CLEAR_WORDS = 2  # With headphones, how many clear words it takes to interrupt
MIN_WORD_CONFIDENCE = 0.8  # Noise usually transcribes as low-confidence words, which can't interrupt
MIN_STOP_CONFIDENCE = 0.6  # A shouted "stop" over Ayra's voice is often heard less clearly, so it needs less
MAX_RECOVERED_WORDS = 3  # How many of the user's first words can be taken back from the end of Ayra's echo
MIN_TURN_CONFIDENCE = 0.6  # A finished turn averaging below this is treated as noise and ignored


def _normalize(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())

def _spoken_words() -> set[str]:
    """Return the words of Ayra's latest reply"""
    return {_normalize(word) for word in tts.last_spoken.split()}

def _find_user_start(heard: list[str], confidences: list[float]) -> int | None:
    """Return the index where the user's own words start, or None if they haven't interrupted"""
    clear = [index for index, confidence in enumerate(confidences) if confidence >= MIN_WORD_CONFIDENCE]

    # On speakers, a command word Ayra is saying itself doesn't count
    spoken = set() if HEADPHONES else _spoken_words()
    for index in reversed(range(len(heard))):  # Use the latest command, in case there are several
        if heard[index] in STOP_WORDS and heard[index] not in spoken and confidences[index] >= MIN_STOP_CONFIDENCE:
            return index + 1  # The command itself isn't part of the question

    if HEADPHONES and len(clear) >= MIN_CLEAR_WORDS:
        return 0
    return None

class TurnFilter:
    """Tracks one Flux turn at a time and stops Ayra as soon as the user interrupts"""

    def __init__(self):
        self.kept_words = []  # The word objects behind the last text returned, for showing their confidence
        self._reset()

    def _reset(self):
        self.overlapped = False  # Was Ayra talking at any point during this turn?
        self.user_start = None  # Index of the user's first word, once they've interrupted
        self.echo_end = 0  # How many words had been heard when Ayra last spoke

    def process(self, turn_words: list, event: str) -> str | None:
        """Handle one Flux event and return the user's words when a turn with some in it ends"""
        words = [word.word for word in turn_words]
        confidences = [word.confidence for word in turn_words]
        heard = [_normalize(word) for word in words]

        if tts.speaking.is_set():
            self.overlapped = True
            self.echo_end = len(words)
            if self.user_start is None:
                self.user_start = _find_user_start(heard, confidences)
                if self.user_start is not None:
                    print("✋ Interrupted")
                    tts.interrupt()

        if event != "EndOfTurn":
            return None

        if not self.overlapped:
            start = 0  # Ayra was quiet for the whole turn, so it's all the user
        elif self.user_start is not None:
            # Flux may have revised earlier words since the interruption, shifting the positions,
            # so look for the command again in the final transcript
            found = _find_user_start(heard, confidences)
            start = self.user_start if found is None else found
        elif HEADPHONES:
            start = 0  # The mic can't hear Ayra through headphones, so it's all the user
        else:
            start = self.echo_end  # Flux can keep one turn open, so the user's words may follow Ayra's echo
            # The user often starts talking just as Ayra finishes, so their first words land at the end of the echo.
            # Take back clear words there that Ayra never said, as long as the user went on talking after them.
            if start < len(words):
                spoken = _spoken_words()
                while (start > 0 and self.echo_end - start < MAX_RECOVERED_WORDS
                       and heard[start - 1] and heard[start - 1] not in spoken
                       and confidences[start - 1] >= MIN_WORD_CONFIDENCE):
                    start -= 1

        overlapped = self.overlapped
        interrupted = self.user_start is not None
        self._reset()

        # Leave out anything with no letters or digits, such as Flux's "--"
        kept = [index for index in range(start, len(words)) if heard[index]]
        self.kept_words = [turn_words[index] for index in kept]
        user_heard = [heard[index] for index in kept]

        text = " ".join(words[index] for index in kept)

        if not kept:
            if words and not interrupted:
                print(f"🔇 Ignored while Ayra was talking: {' '.join(words)}")
            return None
        if sum(confidences[index] for index in kept) / len(kept) < MIN_TURN_CONFIDENCE:
            print(f"🔇 Ignored as unclear: {text}")
            return None
        if all(word in STOP_WORDS for word in user_heard):
            # Saying just the name while Ayra is quiet is a greeting, so let Claude answer it
            if not overlapped and "ayra" in user_heard:
                return text
            # Otherwise a command on its own is not a question
            if not overlapped:
                print(f"🔇 Nothing to stop, so ignored: {text}")
            return None
        # After a command, words that all came from Ayra's reply are its voice dying away, not the user
        if overlapped and not HEADPHONES and all(word in _spoken_words() for word in user_heard):
            print(f"🔇 Ignored as Ayra's echo: {text}")
            return None
        # Show what was cut from the front, as it may have been the user starting to talk as Ayra finished
        if overlapped and not interrupted and start > 0:
            print(f"🔇 Dropped from the start, heard while Ayra was talking: {' '.join(words[:start])}")
        return text
