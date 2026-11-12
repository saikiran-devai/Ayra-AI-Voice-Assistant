"""What Ayra is doing right now, shown on the website."""
STARTING = "starting"  # Not connected to Flux yet
LISTENING = "listening"
THINKING = "thinking"  # Working on a reply, before the voice starts
SPEAKING = "speaking"
ON_CALL = "on_call"  # The user is on a phone call, so Ayra stays quiet and ignores what it hears

current = STARTING


def update(new: str) -> None:
    """Change the state, unless the user is on a call, which only end_call() finishes"""
    global current
    if current != ON_CALL:
        current = new


def start_call() -> None:
    global current
    current = ON_CALL


def end_call() -> bool:
    """Go back to listening after a call, returning whether there was one"""
    global current
    if current != ON_CALL:
        return False
    current = LISTENING
    return True
