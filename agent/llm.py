import asyncio
import os
import re
import time
from collections.abc import Callable

import anthropic
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from sarvamai import AsyncSarvamAI

from agent import state, vision
from agent.graph import build_agent
from agent.tools import new_turn
from voice import text_to_speech as tts
from voice.text_to_speech import keep_output_awake, speak_clauses

MAX_GRAPH_STEPS = 20  # Stops a runaway loop of tool calls. An order can take several rounds: search, cart, review.

# A clause ends at sentence punctuation or a pause mark, followed by a space
CLAUSE_END = re.compile(r"(?P<sentence>[.!?…]+[\"')\]]*\s|\n)|[,;:—]\s")
MIN_CLAUSE_WORDS = 4  # Shorter pieces ending in a comma wait for more words, so speech doesn't sound choppy
LIST_NUMBER = re.compile(r"\d+[.)]")
CAMERA_NOTE = {"type": "text", "text": "[Camera picture]"}  # Marks where a picture was, once it's dropped from the history
ABBREVIATIONS = {"dr.", "mr.", "mrs.", "ms.", "st.", "vs.", "e.g.", "i.e."}  # These full stops don't end a sentence


def _next_clause(text: str) -> tuple[str, str] | None:
    """Split a finished clause off the front of text, returning it and the rest, or None if there isn't one yet"""
    for match in CLAUSE_END.finditer(text):
        clause = text[:match.end()].strip()
        words = clause.split()
        # Blank lines, stray symbols, abbreviations and list numbers like "1." don't end a clause
        if not any(character.isalnum() for character in clause):
            continue
        if words[-1].lower() in ABBREVIATIONS or LIST_NUMBER.fullmatch(words[-1]):
            continue
        if match.group("sentence") or len(words) >= MIN_CLAUSE_WORDS:
            return clause, text[match.end():]
    return None


def _chunk_text(chunk: AIMessageChunk) -> str:
    """Return the speakable text in a streamed chunk, skipping thinking and tool calls"""
    if isinstance(chunk.content, str):
        return chunk.content
    return "".join(block.get("text", "") for block in chunk.content if block.get("type") == "text")


async def get_reply(
    agent,
    history: list,
    user_text: str,
    clauses: asyncio.Queue,
    should_stop: Callable[[], bool],
    image: dict | None = None,
) -> None:
    """Run the agent on one finished turn, putting each clause on the queue as soon as it's complete.
    With the camera on, image is its newest picture, sent along with what the user said."""
    new_turn()
    if image is None:
        user_message = HumanMessage(user_text)
    else:
        user_message = HumanMessage([image, {"type": "text", "text": user_text}])
    print("🤖 ", end="", flush=True)

    error = None
    final = None
    stopped = False
    said_filler = False
    unsent = ""
    sent = []

    def send(clause: str) -> None:
        sent.append(clause)
        clauses.put_nowait(clause)

    def send_ready_clauses() -> bool:
        """Queue every finished clause, returning False if the reply should stop"""
        nonlocal unsent
        while split := _next_clause(unsent):
            if should_stop():
                return False
            clause, unsent = split
            send(clause)
        return True

    def send_leftover() -> None:
        nonlocal unsent
        if unsent.strip() and not should_stop():
            send(unsent.strip())
        unsent = ""

    try:
        async for mode, payload in agent.astream(
            {"messages": [*history, user_message]},
            config={"recursion_limit": MAX_GRAPH_STEPS},
            stream_mode=["messages", "values"],
        ):
            if mode == "values":
                final = payload["messages"]
                continue

            chunk, _ = payload
            if not isinstance(chunk, AIMessageChunk):
                continue

            text = _chunk_text(chunk)
            print(text, end="", flush=True)
            unsent += text
            # Claude's own web search arrives as a content block rather than a tool call
            server_tools = [
                {"name": block.get("name")} for block in (chunk.content if isinstance(chunk.content, list) else [])
                if block.get("type") == "server_tool_use"
            ]
            for call in [*chunk.tool_call_chunks, *server_tools]:
                if call.get("name"):
                    print(f"\n🔧 {call['name']}", flush=True)
                    send_leftover()  # Say anything Claude wrote before the tool while it runs
                    # Fill the silence, unless Ayra has already said something this turn
                    if not sent and not said_filler and not should_stop():
                        clauses.put_nowait(tts.FILLER)
                        said_filler = True

            if not send_ready_clauses():
                stopped = True
                break

        if not stopped:
            send_leftover()
    except anthropic.AuthenticationError:
        error = "Claude rejected the API key - check ANTHROPIC_API_KEY in .env"
    except anthropic.RateLimitError:
        error = "Claude rate limit reached - try again shortly"
    except anthropic.APIStatusError as e:
        error = f"Claude API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        error = f"Couldn't reach Claude: {e.__cause__ or e}"
    print()

    if error is None and not stopped and final and final[-1].response_metadata.get("stop_reason") == "refusal":
        error = "Claude declined to answer that"

    if error:
        print(f"⚠️  {error}")  # Leave this turn out of the history, so it isn't resent with the next one
    elif stopped:
        # Cut off partway, so remember the question and only the clauses that were sent to be spoken
        history.append(user_message)
        if sent:
            history.append(AIMessage(" ".join(sent)))
    elif final:
        # Keep everything, including thinking and tool calls, as the API expects them back
        history[:] = final
    _drop_old_pictures(history)


def _drop_old_pictures(history: list) -> None:
    """Swap camera pictures in earlier turns for a note, so each turn sends only the newest one"""
    for index, message in enumerate(history):
        if isinstance(message, HumanMessage) and isinstance(message.content, list):
            if any(block.get("type") == "image" for block in message.content if isinstance(block, dict)):
                content = [CAMERA_NOTE if isinstance(block, dict) and block.get("type") == "image" else block
                           for block in message.content]
                history[index] = HumanMessage(content)


async def reply_worker(queue: asyncio.Queue) -> None:
    """Answer finished turns one at a time, in order"""
    agent = build_agent()
    tts_client = AsyncSarvamAI(api_subscription_key=os.environ["SARVAM_AI_API_KEY"])
    history = []
    keep_output_awake()
    while True:
        user_text = await queue.get()
        # Turns that piled up while Ayra was busy are usually one thought split by pauses
        while not queue.empty():
            user_text += " " + queue.get_nowait()

        state.update(state.THINKING)
        tts.turn_started = time.perf_counter()
        clauses = asyncio.Queue()
        speaker = asyncio.create_task(speak_clauses(tts_client, clauses))
        # Stop the reply if Ayra was interrupted, or if the user has started a new turn
        should_stop = lambda: speaker.done() or not queue.empty()
        try:
            await get_reply(agent, history, user_text, clauses, should_stop, vision.image_block())
        except Exception as e:  # One bad reply must not stop Ayra answering for good
            print(f"\n⚠️  Reply failed: {e!r}")
        clauses.put_nowait(None)
        try:
            await speaker
        except Exception as e:
            print(f"⚠️  Speech failed: {e!r}")
        state.update(state.LISTENING)
