"""
Thin Gemini client for Layer 6's transformation-code-inspection step
(framework doc Section 8.1: "An LLM can assist by reading the code and
correlating it with the defect pattern").

Deliberately degrades gracefully when GEMINI_API_KEY isn't set, rather than
failing the whole layer -- boundary testing (the other half of Layer 6) is
fully self-contained and shouldn't require an API key just to run. When no
key is configured, a rule-based fallback explanation is returned instead,
clearly labeled as such.

NOTE: the actual `client.models.generate_content(...)` call below has not
been exercised against a live Gemini endpoint in this development
environment (no network egress to Google's API here) -- verify it against
the current google-genai SDK once GEMINI_API_KEY is actually configured,
and adjust the call shape if the SDK's interface has moved since.
"""
from __future__ import annotations

import os

from app.core.config import settings

PROMPT_TEMPLATE = """You are assisting with root cause analysis for a data quality defect.

DEFECT FINGERPRINT:
- Type: {defect_type}
- Affected field(s): {affected_fields}
- Failure pattern: {failure_pattern}
- Segment: {failure_distribution}

CANDIDATE TRANSFORMATION CODE (the pipeline stage this defect was traced to via boundary testing):
Description: {transform_description}

```sql
{transform_code}
```

Does this transformation code contain logic that could plausibly produce the observed defect?
Answer in 2-4 sentences: point to the specific line or expression responsible if you find one,
or state clearly if the code looks correct and the cause likely lies elsewhere.
"""


def inspect_transform_code(fingerprint: dict, transform_description: str, transform_code: str) -> dict:
    """
    Returns dict(source="llm"|"fallback", explanation=str). Never raises --
    any failure to reach the LLM degrades to the rule-based fallback rather
    than breaking the layer.
    """
    api_key = settings.gemini_api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"source": "fallback", "explanation": _fallback_explanation(fingerprint, transform_code)}

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = PROMPT_TEMPLATE.format(
            defect_type=fingerprint["defect_type"],
            affected_fields=fingerprint["affected_fields"],
            failure_pattern=fingerprint["failure_pattern"],
            failure_distribution=fingerprint["failure_distribution"],
            transform_description=transform_description,
            transform_code=transform_code,
        )
        response = client.models.generate_content(model=settings.gemini_model, contents=prompt)
        return {"source": "llm", "explanation": response.text}
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any LLM failure should degrade, not crash
        fallback = _fallback_explanation(fingerprint, transform_code)
        return {"source": "fallback", "explanation": f"[LLM call failed: {exc}] {fallback}"}


def _fallback_explanation(fingerprint: dict, transform_code: str) -> str:
    """
    A simple keyword-overlap heuristic used when no LLM is available: does
    the transformation code even mention the affected field(s)? This is a
    much weaker signal than genuine LLM code reading, but it keeps the
    layer honest and non-empty without an API key.
    """
    fields = fingerprint["affected_fields"]
    mentioned = [f for f in fields if f in transform_code]
    if mentioned:
        return (
            f"(No LLM configured -- keyword fallback.) The transformation code references "
            f"{mentioned}, which {'is' if len(mentioned) == 1 else 'are'} among the defect's "
            "affected field(s) -- worth manual review of this transform as a candidate cause."
        )
    return (
        "(No LLM configured -- keyword fallback.) The transformation code does not mention "
        f"the affected field(s) {fields} by name -- if this is genuinely the injection stage, "
        "the defect may arise from an implicit/derived expression rather than a direct reference."
    )
