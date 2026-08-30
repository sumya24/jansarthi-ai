"""Mirrors this project's own Ask Sarthi prompt files into LangSmith's Prompt Hub, for
versioning and side-by-side testing in the Playground -- WITHOUT changing how the running app
actually loads prompts.

Deliberately mirror-only: `AnswerGenerationService` (see
backend/services/answer_generation_service.py) keeps reading
prompts/ask_sarthi_system_prompt.txt and prompts/ask_sarthi_answer_prompt.txt via
backend/config.py's `get_prompt()` exactly as before -- this script never becomes the source of
truth for what the running app actually sends to Sarvam, and nothing in the request path calls
LangSmith to fetch a prompt. Re-running this script after editing either .txt file pushes a new
commit to the same LangSmith prompt, so LangSmith's own version history (compare commits, roll
back, see who changed what when) tracks every change made to these files over time -- purely a
visibility/experimentation layer, not a runtime dependency.

Why not switch the app to load prompts FROM LangSmith instead: that would add a network call (or
a cache-invalidation problem) to the answer-generation hot path, for a capability (shipping a
prompt change without a code deploy) this project doesn't currently need -- see
docs/ask_sarthi_langsmith_observability.md's "Prompt Hub" section for this tradeoff, and
revisit it if that need actually shows up.

The two prompt files are pushed as ONE LangChain ChatPromptTemplate (system message + human
message), matching exactly the two messages AnswerGenerationService.generate() actually sends to
Sarvam -- not a reformatting, just the same two strings wrapped in the shape LangSmith's
push_prompt() expects.

Usage:
    python scripts/push_prompts_to_langsmith.py

Requires LANGSMITH_TRACING=true and LANGSMITH_API_KEY set (see .env).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client

from backend.config import settings

PROMPT_IDENTIFIER = "janmitra-ask-sarthi-answer-prompt"


def main() -> None:
    if not settings.LANGSMITH_API_KEY:
        raise SystemExit("LANGSMITH_API_KEY is not set (see .env) -- required to push to the Prompt Hub.")

    system_prompt = (settings.PROMPTS_DIR / "ask_sarthi_system_prompt.txt").read_text(encoding="utf-8")
    answer_prompt = (settings.PROMPTS_DIR / "ask_sarthi_answer_prompt.txt").read_text(encoding="utf-8")

    template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", answer_prompt),
    ])

    client = Client(api_key=settings.LANGSMITH_API_KEY, api_url=settings.LANGSMITH_ENDPOINT)
    url = client.push_prompt(
        PROMPT_IDENTIFIER,
        object=template,
        description=(
            "Mirror of prompts/ask_sarthi_system_prompt.txt + ask_sarthi_answer_prompt.txt "
            "(see backend/services/answer_generation_service.py). The running app reads the "
            "local .txt files directly -- this is a versioned copy for Playground testing and "
            "change history, not the source the app actually loads from. Re-run "
            "scripts/push_prompts_to_langsmith.py after editing either file to push a new commit."
        ),
        commit_description="Pushed by scripts/push_prompts_to_langsmith.py",
    )

    print(f"Pushed to LangSmith Prompt Hub: {url}")
    print("View/edit/test it under Prompts in the LangSmith UI. Editing it there does NOT change")
    print("what the running app sends to Sarvam -- see this script's module docstring for why.")


if __name__ == "__main__":
    main()
