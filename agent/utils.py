import time


# ---- FALLBACK HANDLER ----
# Console output stays ASCII on purpose: Windows terminals default to
# cp1252, where printing emoji raises UnicodeEncodeError and kills the run.
def safe_invoke(models, structured_output=None, method=None, prompt=None, retries=2):
    """
    Invoke the first model that succeeds.

    A model that fails with a rate-limit error is retried in place (up to
    `retries` extra attempts, with exponential backoff) before falling back to
    the next model. Any other error moves straight to the next model. Raises
    RuntimeError with the collected errors if every model is exhausted.
    """
    errors = []
    for model in models:
        for attempt in range(retries + 1):
            try:
                print(f"\n[MODEL] Using model: {model.model_name} (attempt {attempt + 1})")
                if structured_output:
                    if method:
                        return model.with_structured_output(structured_output, method=method).invoke(prompt)
                    return model.with_structured_output(structured_output).invoke(prompt)
                return model.invoke(prompt)
            except Exception as e:
                print(f"[WARN] Model {model.model_name} failed with error: {str(e)}")
                errors.append(f"{model.model_name}: {e}")
                rate_limited = "rate_limit" in str(e).lower() or "429" in str(e)
                if rate_limited and attempt < retries:
                    wait_time = 10 * (2 ** attempt)  # exponential backoff
                    print(f"[RETRY] Rate limit hit. Retrying {model.model_name} in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                break  # not retryable, or attempts exhausted -> next model
    raise RuntimeError("All fallback models failed. Errors: " + " | ".join(errors))
