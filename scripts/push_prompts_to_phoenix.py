"""Mirrors this project's own Ask Sarthi prompt files into a self-hosted Phoenix instance's Prompt
Hub, for versioning and Playground testing -- WITHOUT changing how the running app actually loads
prompts. Same purpose and same mirror-only contract as scripts/push_prompts_to_langsmith.py (see
that script's own docstring) -- this is Phoenix's equivalent, not a replacement for it.

Deliberately mirror-only: `AnswerGenerationService` (see
backend/services/answer_generation_service.py) keeps reading
prompts/ask_sarthi_system_prompt.txt and prompts/ask_sarthi_answer_prompt.txt directly --
this script never becomes the source of truth for what the running app actually sends to Sarvam.
Re-running this script after editing either .txt file pushes a new prompt version, so Phoenix's
own version history tracks every change over time -- purely a visibility/experimentation layer.

Runs in this project's MAIN environment (NOT the isolated .phoenix-venv/) -- unlike the full
arize-phoenix server package, `arize-phoenix-client` (this script's only Phoenix dependency) has a
lightweight dependency tree that doesn't conflict with this project's pinned versions (confirmed
directly; see PHOENIX_TRACING_PLAN.md), so it's a normal requirements.txt entry.

Usage:
    python scripts/push_prompts_to_phoenix.py

Requires a running Phoenix server (see PHOENIX_TRACING_PLAN.md's "How to run this locally").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phoenix.client import Client
from phoenix.client.types import PromptVersion

from backend.config import settings

PROMPT_NAME = "jansarthi-ask-sarthi-answer-prompt"
PHOENIX_BASE_URL = "http://localhost:6006"


def main() -> None:
    system_prompt = (settings.PROMPTS_DIR / "ask_sarthi_system_prompt.txt").read_text(encoding="utf-8")
    answer_prompt = (settings.PROMPTS_DIR / "ask_sarthi_answer_prompt.txt").read_text(encoding="utf-8")

    # model_provider="OPENAI" is a labeling compromise, not a real vendor claim -- Phoenix has no
    # "SARVAM" option, and Sarvam's own chat completions API is OpenAI-shaped (same
    # messages/choices structure, see answer_generation_service.py), so this is the closest
    # template-compatible fit for how the Playground renders/tests the prompt. model_name is the
    # real one (settings.LLM_MODEL, "sarvam-105b") regardless of the provider label.
    version = PromptVersion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": answer_prompt},
        ],
        model_name=settings.LLM_MODEL,
        model_provider="OPENAI",
        description=(
            "Mirror of prompts/ask_sarthi_system_prompt.txt + ask_sarthi_answer_prompt.txt "
            "(see backend/services/answer_generation_service.py). The running app reads the "
            "local .txt files directly -- this is a versioned copy for Playground testing and "
            "change history, not the source the app actually loads from."
        ),
    )

    client = Client(base_url=PHOENIX_BASE_URL)
    result = client.prompts.create(
        name=PROMPT_NAME,
        version=version,
        prompt_description="Ask Sarthi's answer-generation prompt (mirror, see this script's own module docstring).",
    )

    print(f"Pushed to Phoenix Prompt Hub: {PHOENIX_BASE_URL}/prompts")
    print(f"Prompt name: {PROMPT_NAME}, version id: {result.id}")
    print("View/edit/test it under Prompts in the Phoenix UI. Editing it there does NOT change")
    print("what the running app sends to Sarvam -- see this script's module docstring for why.")


if __name__ == "__main__":
    main()
