"""Reading damage photos with Claude on Bedrock.

A photo on its own is not searchable by meaning. Labelling each one with the
damage type, the panel it is on, how bad it is and a sentence describing it gives
the corpus something semantic to filter and rerank against, and it is how a fleet
operator would onboard their own claim photos rather than hand-typing metadata
for several hundred images.

The labels come back as structured JSON constrained to a fixed vocabulary, so
they can be used as filters in a vector index rather than as free text.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# Fixed vocabularies. Free-text labels cannot be used as index filters, and a
# model left to invent categories will produce forty ways of saying "scratch".
DAMAGE_TYPES = [
    "scratch", "scuff", "dent", "crack", "shatter", "tear",
    "misalignment", "paint-transfer", "rust", "missing-part", "none",
]

PANELS = [
    "front-bumper", "rear-bumper", "bonnet", "boot-lid", "roof",
    "front-door-left", "front-door-right", "rear-door-left", "rear-door-right",
    "front-wing-left", "front-wing-right", "rear-quarter-left", "rear-quarter-right",
    "headlamp", "tail-light", "windscreen", "rear-screen", "side-window",
    "wing-mirror", "wheel", "grille", "sill", "unknown",
]

SEVERITIES = ["cosmetic", "minor", "moderate", "severe"]

ANGLES = [
    "front", "front-three-quarter", "side", "rear-three-quarter", "rear",
    "close-up", "interior", "unknown",
]

PROMPT = f"""You are assessing a vehicle damage photograph for a rental fleet's
claims system.

Return JSON only, no prose, with exactly these keys:

  "damageType": one of {DAMAGE_TYPES}
  "panel": one of {PANELS}
  "severity": one of {SEVERITIES}
  "angle": one of {ANGLES}
  "description": one sentence an assessor would write, naming what is damaged
                 and how, for example "Deep scratch running along the rear
                 quarter panel with paint transfer".
  "bodyColour": the vehicle's colour in one word
  "confidence": your confidence from 0 to 1 that the damage assessment is right

If the vehicle appears undamaged, use "none" for damageType and "unknown" for
panel, and say so in the description. Do not guess a make or model."""


@dataclass
class VisionUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    failures: int = 0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "seconds": round(self.seconds, 1),
            "failures": self.failures,
            "model": MODEL_ID,
        }


@dataclass
class Labeller:
    """Turns a photo into structured, filterable metadata."""

    model_id: str = MODEL_ID
    profile: str | None = field(default_factory=lambda: os.getenv("AWS_PROFILE"))
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    _client: object = None
    usage: VisionUsage = field(default_factory=VisionUsage)

    @property
    def available(self) -> bool:
        return bool(
            self.profile
            or os.getenv("AWS_ACCESS_KEY_ID")
            or (
                os.getenv("AWS_ROLE_ARN")
                and os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")
            )
        )

    @property
    def client(self):
        if self._client is None:
            import boto3

            session = (
                boto3.Session(profile_name=self.profile, region_name=self.region)
                if self.profile
                else boto3.Session(region_name=self.region)
            )
            self._client = session.client("bedrock-runtime")
        return self._client

    def label(self, image_bytes: bytes, image_format: str = "jpeg") -> dict:
        """Describe one photo. Returns a fallback record rather than raising."""
        began = time.perf_counter()
        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=[{
                    "role": "user",
                    "content": [
                        {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                        {"text": PROMPT},
                    ],
                }],
                inferenceConfig={"maxTokens": 400, "temperature": 0},
            )
            text = response["output"]["message"]["content"][0]["text"]
            usage = response.get("usage", {})
            self.usage.calls += 1
            self.usage.input_tokens += usage.get("inputTokens", 0)
            self.usage.output_tokens += usage.get("outputTokens", 0)
            self.usage.seconds += time.perf_counter() - began
            return self._parse(text)
        except Exception as exc:  # noqa: BLE001
            self.usage.failures += 1
            logger.warning("Vision labelling failed: %s", exc)
            return self._fallback(str(exc))

    def _parse(self, text: str) -> dict:
        """Pull JSON out of the reply and hold it to the vocabularies."""
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return self._fallback("no JSON in reply")

        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            return self._fallback(f"bad JSON: {exc}")

        def constrain(value, allowed, default):
            value = str(value or "").strip().lower()
            return value if value in allowed else default

        return {
            "damageType": constrain(raw.get("damageType"), DAMAGE_TYPES, "scratch"),
            "panel": constrain(raw.get("panel"), PANELS, "unknown"),
            "severity": constrain(raw.get("severity"), SEVERITIES, "minor"),
            "angle": constrain(raw.get("angle"), ANGLES, "unknown"),
            "description": str(raw.get("description") or "").strip()[:400],
            "bodyColour": str(raw.get("bodyColour") or "").strip().lower()[:20],
            "confidence": float(raw.get("confidence") or 0.5),
            "labelledBy": self.model_id,
        }

    def _fallback(self, reason: str) -> dict:
        return {
            "damageType": "scratch", "panel": "unknown", "severity": "minor",
            "angle": "unknown", "description": "", "bodyColour": "",
            "confidence": 0.0, "labelledBy": self.model_id, "error": reason,
        }


def to_jpeg(image, max_side: int = 1024, quality: int = 85) -> bytes:
    """Downscale and compress before anything else touches the image.

    Source photos run to a couple of megabytes each. At demo resolution 1024 px
    at quality 85 is indistinguishable and about a tenth of the size, which
    matters when the bytes are stored in the document alongside the vector.
    """
    from PIL import Image

    if image.mode != "RGB":
        image = image.convert("RGB")
    image = image.copy()
    image.thumbnail((max_side, max_side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def to_data_url(image_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()


_labeller: Labeller | None = None


def get_labeller() -> Labeller:
    global _labeller
    if _labeller is None:
        _labeller = Labeller()
    return _labeller
