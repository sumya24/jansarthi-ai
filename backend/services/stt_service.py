"""Chunked speech-to-text transcription -- shared by ComplaintAgent's voice-complaint flow and
Ask Sarthi's voice-assistant flow (ask_sarthi_service.ask_voice()).

Relocated, unmodified, from backend/services/complaint_agent.py (a pure move -- see git history
for the original location) so both callers reuse the exact same retry/gap-marker chunk-stitching
logic instead of a second implementation. `ComplaintAgent._transcribe_chunks` is now a thin
wrapper around `transcribe_chunks()` here -- its own signature/behavior is unchanged.
"""

import logging

from backend.services.sarvam_client import AIServiceError, SarvamClient

logger = logging.getLogger(__name__)

# Inserted into the transcript in place of a chunk that failed to transcribe even after a
# retry, so a citizen's complaint stays honest about a gap instead of silently reading as if
# nothing were missing (a dropped chunk could easily be the one with the actual location or
# key detail in it -- see docs/ai_pipeline_limits.md for why chunks can fail at all).
STT_GAP_MARKER = "[a few seconds of the recording could not be transcribed]"


def transcribe_chunks(sarvam_client: SarvamClient, audio_chunks: list[bytes], bcp47_language: str) -> str:
    """Transcribe each audio chunk in order and join the results into one transcript.

    Each chunk is sent as its own independent speech-to-text call. A chunk that fails
    is retried once -- most STT failures are transient (a network blip, a momentary API
    hiccup), so a single retry recovers the majority of them before the citizen notices
    anything happened. If a chunk still fails after the retry, it's skipped and replaced
    with an explicit gap marker in the transcript, rather than either discarding the
    whole complaint/question or silently stitching the surviving chunks together as if
    nothing were missing.

    Args:
        sarvam_client: The Sarvam client to transcribe each chunk with.
        audio_chunks: Ordered raw-audio byte chunks, each independently transcribable.
        bcp47_language: BCP-47 language code of the spoken audio, e.g. "mr-IN".

    Returns:
        The joined transcript, in chunk order, with `STT_GAP_MARKER` in place of any
        chunk that could not be transcribed.

    Raises:
        AIServiceError: If every chunk fails to transcribe (nothing usable came back
            at all).
    """
    total = len(audio_chunks)
    pieces: list[str] = []
    succeeded = 0
    failed = 0

    for index, chunk in enumerate(audio_chunks, start=1):
        transcript: str | None = None
        for attempt in (1, 2):
            try:
                logger.info("STT chunk %d/%d started (attempt %d/2)", index, total, attempt)
                transcript = sarvam_client.transcribe(chunk, bcp47_language)
                logger.info(
                    "STT chunk %d/%d succeeded (attempt %d/2, %d chars)",
                    index, total, attempt, len(transcript),
                )
                break
            except AIServiceError as exc:
                if attempt == 1:
                    logger.warning(
                        "STT chunk %d/%d failed on attempt 1/2, retrying: %s", index, total, exc
                    )
                else:
                    logger.error(
                        "STT chunk %d/%d failed on attempt 2/2, giving up on this chunk: %s",
                        index, total, exc,
                    )

        if transcript is not None:
            succeeded += 1
            # An empty-but-successful transcript (e.g. a near-silent trailing chunk) is
            # not a gap -- there was genuinely nothing to transcribe, so add nothing.
            if transcript.strip():
                pieces.append(transcript.strip())
        else:
            failed += 1
            pieces.append(STT_GAP_MARKER)

    logger.info(
        "STT chunking complete: %d/%d chunk(s) succeeded, %d gap(s) inserted", succeeded, total, failed
    )
    if succeeded == 0:
        raise AIServiceError("Speech-to-text service failed. Please try again.")

    return " ".join(pieces)
