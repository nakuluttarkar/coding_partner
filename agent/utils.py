import re
import sys
import time

# Console output stays ASCII on purpose: Windows terminals default to
# cp1252, where printing emoji raises UnicodeEncodeError and kills the run.

# Groq signals every per-minute cap (TPM, OTPM) with rate_limit_exceeded, and
# sends it as 413 as well as 429 depending on which limit was hit.
_RATE_LIMIT_MARKERS = ("rate_limit", "429", "413", "too many requests", "request too large")

# Limits a short in-place retry cannot clear, so the same model is not retried:
# - "Request too large": this one request exceeds the per-minute limit on its
#   own, so an identical retry fails identically (seen three times in a row).
# - Daily limits: tokens or requests per day free up over minutes to hours.
_TOO_LARGE_MARKER = "request too large"
_DAILY_MARKERS = ("per day", "(tpd)", "(rpd)")

# Groq says how long to wait, in units that grow with the wait: "975ms",
# "7.482s", "4m47.712s", "1h2m3s". Every form has to be read, or a wait of
# minutes is mistaken for seconds and retried pointlessly.
_RETRY_AFTER_RE = re.compile(r"try again in\s+((?:\d+(?:\.\d+)?(?:ms|h|m|s))+)", re.IGNORECASE)
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)", re.IGNORECASE)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# Per-minute windows reset after 60s, so back off in that ballpark rather than
# hammering a limit that has not cleared yet. A limit that asks for a longer
# wait than the cap is not worth waiting out on the same model.
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


def is_daily_limit(exc) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _DAILY_MARKERS)


def is_request_too_large(exc) -> bool:
    return _TOO_LARGE_MARKER in str(exc).lower()


def is_output_truncated(exc) -> bool:
    """A tool call whose JSON never parsed -- the generation was cut off mid-string."""
    text = str(exc).lower()
    return "tool_use_failed" in text or "failed to parse tool call" in text


def parse_retry_after(exc):
    """Seconds Groq asked us to wait, or None if the error does not say."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    return sum(float(value) * _UNIT_SECONDS[unit.lower()]
               for value, unit in _DURATION_PART_RE.findall(match.group(1)))


def is_retryable_in_place(exc) -> bool:
    """A rate limit worth waiting out on the same model.

    Only per-minute limits that clear within the backoff cap qualify. A request
    that is too large on its own, a daily limit, or a wait longer than the cap
    all fall through to the next model instead of burning retries that cannot
    succeed -- a live run lost two minutes per step retrying a daily limit.
    """
    if not is_rate_limited(exc) or is_request_too_large(exc) or is_daily_limit(exc):
        return False
    wait = parse_retry_after(exc)
    return wait is None or wait <= _BACKOFF_CAP_SECONDS


def _skip_reason(exc) -> str:
    if is_daily_limit(exc):
        return "daily limit reached"
    if is_request_too_large(exc):
        return "request is larger than the per-minute limit"
    wait = parse_retry_after(exc)
    if wait is not None and wait > _BACKOFF_CAP_SECONDS:
        return f"limit clears in {wait:.0f}s, too long to wait"
    return "still rate limited after retries"


def backoff_seconds(exc, attempt: int) -> float:
    """Honour the wait Groq asks for, else exponential backoff within the cap."""
    wait = parse_retry_after(exc)
    if wait is not None:
        # Small buffer so the window has definitely rolled over.
        return min(wait + 1.0, _BACKOFF_CAP_SECONDS)
    return min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)


def invoke_with_fallback(models, call, retries=2, label="Model"):
    """Run ``call(model)`` against each model in turn, returning the first success.

    A model that fails on a per-minute rate limit that will clear soon is
    retried in place (up to ``retries`` extra attempts, backing off). Every
    other failure -- including limits that cannot clear in time -- moves
    straight to the next model. Raises RuntimeError with every collected error
    if all are exhausted.
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
                if is_retryable_in_place(e) and attempt < retries:
                    wait = backoff_seconds(e, attempt)
                    console(f"[RETRY] Rate limit hit. Retrying {model.model_name} in {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                if is_rate_limited(e):
                    console(f"[SKIP] {model.model_name}: {_skip_reason(e)}; trying the next model")
                break  # not retryable, or attempts exhausted -> next model

    hint = ""
    if any(is_output_truncated(msg) for msg in errors):
        hint = (" One model's tool call was cut off mid-generation, which means the file"
                " is too large to write in a single call -- ask for a smaller file.")
    elif any(is_daily_limit(msg) for msg in errors):
        hint = (" Some models have used up their Groq free-tier daily token allowance,"
                " which frees up gradually over the following hours; retrying right away"
                " will not help.")
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
