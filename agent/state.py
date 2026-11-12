"""What Ayra is doing right now, shown on the website."""
STARTING = "starting"  # Not connected to Flux yet
SLEEPING = "sleeping"  # Hearing everything but answering nothing, until the user says "Hey Ayra"
LISTENING = "listening"
THINKING = "thinking"  # Working on a reply, before the voice starts
SPEAKING = "speaking"
ON_CALL = "on_call"  # The user is on a phone call, so Ayra stays quiet and ignores what it hears

current = STARTING
awake = False  # Ayra starts asleep, and "Hey Ayra" wakes it


def update(new: str) -> None:
    """Change the state, unless the user is on a call, which only end_call() finishes. Ready while asleep means sleeping."""
    global current
    if current != ON_CALL:
        current = SLEEPING if new == LISTENING and not awake else new


def wake() -> None:
    global awake
    awake = True
    update(LISTENING if current == SLEEPING else current)


def sleep() -> None:
    """Stop answering. If Ayra is still saying goodbye, it shows as sleeping once it finishes."""
    global awake
    awake = False
    update(LISTENING if current == LISTENING else current)


def start_call() -> None:
    global current
    current = ON_CALL


def end_call() -> bool:
    """Go back to listening after a call, returning whether there was one"""
    global current
    if current != ON_CALL:
        return False
    current = LISTENING if awake else SLEEPING
    return True
