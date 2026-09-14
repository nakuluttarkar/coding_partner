import re
import sys
import time

# Console output stays ASCII on purpose: Windows terminals default to
# cp1252, where printing emoji raises UnicodeEncodeError and kills the run.

# Groq signals every per-minute cap (TPM, OTPM) with rate_limit_exceeded, and
# sends it as 413 as well as 429 depending on which limit was hit.
_RATE_LIMIT_MARKERS = ("rate_limit", "429", "413", "too many requests", "request too large")

# Groq often says how long to wait, e.g. "Please try again in 7.482s".
_RETRY_AFTER_RE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)

# Per-minute windows reset after 60s, so back off in that ballpark rather than
# hammering a limit that has not cleared yet.
_BACKOFF_BASE_SECONDS = 20
_BACKOFF_CAP_SECONDS = 60


def console(message):
    """print() that cannot kill a run on a non-UTF-8 console."""
    try:
        print(message)
        return
    except UnicodeEncodeError:
        pass
    try:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(str(message).encode(encoding, errors="replace").decode(encoding, errors="replace"))
    except UnicodeEncodeError:
        # Last resort: pure ASCII is printable on every console.
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def is_rate_limited(exc) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def is_output_truncated(exc) -> bool:
    """A tool call whose JSON never parsed -- the generation was cut off mid-string."""
    text = str(exc).lower()
    return "tool_use_failed" in text or "failed to parse tool call" in text


def backoff_seconds(exc, attempt: int) -> float:
    """Honour the wait Groq asks for, else exponential backoff within the cap."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if match:
        return min(float(match.group(1)) + 1.0, _BACKOFF_CAP_SECONDS)
    return min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)


def invoke_with_fallback(models, call, retries=2, label="Model"):
    """Run ``call(model)`` against each model in turn, returning the first success.

    A model that fails on a rate limit is retried in place (up to ``retries``
    extra attempts, backing off) before falling back to the next one, because
    Groq's per-minute caps clear on their own. Any other error moves straight to
    the next model. Raises RuntimeError with every collected error if all are
    exhausted.
    """
    errors = []
    for model in models:
        for attempt in range(retries + 1):
            try:
                console(f"\n[MODEL] {label}: {model.model_name} (attempt {attempt + 1})")
                return call(model)
            except Exception as e:
                console(f"[WARN] {model.model_name} failed: {e}")
                errors.append(f"{model.model_name}: {e}")
                if is_rate_limited(e) and attempt < retries:
                    wait = backoff_seconds(e, attempt)
                    console(f"[RETRY] Rate limit hit. Retrying {model.model_name} in {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                break  # not retryable, or attempts exhausted -> next model

    hint = ""
    if any(is_output_truncated(msg) for msg in errors):
        hint = (" One model's tool call was cut off mid-generation, which means the file"
                " is too large to write in a single call -- ask for a smaller file.")
    elif any(is_rate_limited(msg) for msg in errors):
        hint = (" These are Groq free-tier per-minute limits (8K tokens/min shared,"
                " and a small output-tokens/min cap on some models). Waiting a minute"
                " or generating smaller files usually clears it.")
    raise RuntimeError("All fallback models failed. Errors: " + " | ".join(errors) + hint)


def safe_invoke(models, structured_output=None, method=None, prompt=None, retries=2):
    """Invoke the first model that succeeds, with structured output if requested."""

    def call(model):
        if structured_output:
            if method:
                return model.with_structured_output(structured_output, method=method).invoke(prompt)
            return model.with_structured_output(structured_output).invoke(prompt)
        return model.invoke(prompt)

    return invoke_with_fallback(models, call, retries=retries, label="Using model")
