"""OpenRouter chat models for the commander and the specialists.

Construction is lazy: importing this module does not ask for an API key.
``Agent`` calls ``make_llm`` only when it is given a model id rather than a
ready-made chat model.
"""

import os

from langchain_openai import ChatOpenAI

from .config import COMMANDER_MODEL, OPENROUTER_BASE_URL, SPECIALIST_MODEL

__all__ = ["COMMANDER_MODEL", "SPECIALIST_MODEL", "make_llm"]


def make_llm(model=None, temperature=0, max_tokens=1000):
    """A LangChain chat model pointed at OpenRouter.

    ``model`` defaults to the specialist id. Pass ``COMMANDER_MODEL`` for the
    incident commander.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to the environment before calling the model."
        )
    return ChatOpenAI(
        model=model or SPECIALIST_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        base_url=OPENROUTER_BASE_URL,
        api_key=key,
        timeout=60,
    )
