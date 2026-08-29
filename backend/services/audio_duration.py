"""Best-effort audio duration detection, for real Sarvam speech-to-text cost tracking (see
ask_janmitra_service.py's `stt_span` -- STT is billed per hour of audio, so a real cost needs a
real duration, which the raw audio bytes alone don't carry).

Two paths, tried in order:
1. `mutagen` (a well-established, pure-Python audio-metadata library) -- correctly handles MP4/M4A
   and OGG containers, which covers Safari's and some other browsers' `MediaRecorder` output (see
   frontend-react/src/lib/useAudioRecorder.ts's `pickMimeType()` candidate list).
2. A small hand-rolled WebM/Matroska EBML duration reader -- `mutagen` has NO WebM support at all
   (confirmed directly against its installed module list, 2026-08-27), yet `audio/webm` is
   Chrome's and Firefox's default `MediaRecorder` output and therefore this app's most common real
   case. This reads just the two EBML elements actually needed (`Segment > Info > Duration` and
   `TimecodeScale`) rather than implementing a general Matroska parser.

Best-effort by design, matching every other optional AI-adjacent signal in this codebase (see
answer_generation_service.py's token/cost extraction, translation_service.py's language
detection): returns `None` -- never raises -- if the format isn't recognized, the file is
malformed, or (a real, documented gap, not a bug) the browser's `MediaRecorder` produced a WebM
blob with no explicit Duration element at all -- a known quirk of some browsers' live-recording
output, which this module cannot work around (there is no reliable duration to read in that case,
short of decoding every audio frame, which is out of scope for a cost-observability signal).
"""

from __future__ import annotations

import io
import logging
import struct

logger = logging.getLogger(__name__)

_ID_SEGMENT = 0x18538067
_ID_INFO = 0x1549A966
_ID_TIMECODE_SCALE = 0x2AD7B1
_ID_DURATION = 0x4489


def _read_vint_length(data: bytes, pos: int) -> int | None:
    """EBML variable-length integers are self-describing: the number of leading zero bits before
    the first set bit in the first byte gives the total length (1-8 bytes) of the integer."""
    if pos >= len(data):
        return None
    first = data[pos]
    if first == 0:
        return None
    length = 1
    mask = 0x80
    while not (first & mask):
        mask >>= 1
        length += 1
    return length if length <= 8 else None


def _read_element_id(data: bytes, pos: int) -> tuple[int, int] | None:
    """Element IDs keep their own length-marker bit as part of the canonical value (unlike sizes
    below) -- e.g. Segment's marker+value together are the well-known constant 0x18538067."""
    length = _read_vint_length(data, pos)
    if length is None or pos + length > len(data):
        return None
    return int.from_bytes(data[pos:pos + length], "big"), pos + length


def _read_element_size(data: bytes, pos: int) -> tuple[int, int] | None:
    """Sizes strip the length-marker bit from the first byte before interpreting the rest as an
    unsigned integer. A size whose data bits are ALL 1 means "unknown size" (used for live/
    streamed Matroska output) -- treated as unparseable here rather than guessed at."""
    length = _read_vint_length(data, pos)
    if length is None or pos + length > len(data):
        return None
    marker = 0x80 >> (length - 1)
    value = data[pos] & (marker - 1)
    for b in data[pos + 1:pos + length]:
        value = (value << 8) | b
    if value == (1 << (7 * length)) - 1:
        return None
    return value, pos + length


def _find_direct_children(data: bytes, start: int, end: int) -> dict[int, tuple[int, int]]:
    """Walks direct child elements of a master element in [start, end), without recursing into
    their content -- e.g. a Cluster's own audio data is skipped over by its size, never read.
    Returns {element_id: (content_start, content_end)}; a repeated id keeps the last occurrence,
    which is fine for the fixed handful of ids this module ever looks up."""
    children: dict[int, tuple[int, int]] = {}
    pos = start
    while pos < end:
        id_result = _read_element_id(data, pos)
        if id_result is None:
            break
        elem_id, pos = id_result
        size_result = _read_element_size(data, pos)
        if size_result is None:
            break
        size, pos = size_result
        content_start, content_end = pos, pos + size
        if content_end > end or content_end < content_start:
            break
        children[elem_id] = (content_start, content_end)
        pos = content_end
    return children


def _webm_duration_seconds(data: bytes) -> float | None:
    segment_range = _find_direct_children(data, 0, len(data)).get(_ID_SEGMENT)
    if segment_range is None:
        return None
    info_range = _find_direct_children(data, *segment_range).get(_ID_INFO)
    if info_range is None:
        return None
    info_children = _find_direct_children(data, *info_range)

    duration_range = info_children.get(_ID_DURATION)
    if duration_range is None:
        return None
    duration_bytes = data[duration_range[0]:duration_range[1]]
    if len(duration_bytes) == 4:
        raw_duration = struct.unpack(">f", duration_bytes)[0]
    elif len(duration_bytes) == 8:
        raw_duration = struct.unpack(">d", duration_bytes)[0]
    else:
        return None

    # Nanoseconds per tick -- 1,000,000 (1ms) is Matroska's own documented default when this
    # element is absent, not a guess specific to this app.
    timecode_scale = 1_000_000
    scale_range = info_children.get(_ID_TIMECODE_SCALE)
    if scale_range is not None:
        scale_bytes = data[scale_range[0]:scale_range[1]]
        if scale_bytes:
            timecode_scale = int.from_bytes(scale_bytes, "big")

    return raw_duration * timecode_scale / 1_000_000_000


def get_audio_duration_seconds(audio_bytes: bytes) -> float | None:
    """Best-effort real audio duration in seconds, or `None` if it couldn't be determined (see
    this module's docstring for the known WebM-with-no-Duration-element gap)."""
    try:
        import mutagen

        parsed = mutagen.File(io.BytesIO(audio_bytes))
        if parsed is not None and parsed.info is not None and parsed.info.length:
            return float(parsed.info.length)
    except Exception:
        logger.debug("mutagen could not read audio duration; trying the WebM fallback.", exc_info=True)

    try:
        duration = _webm_duration_seconds(audio_bytes)
        if duration is not None and duration > 0:
            return duration
    except Exception:
        logger.debug("WebM duration parsing failed.", exc_info=True)

    return None
