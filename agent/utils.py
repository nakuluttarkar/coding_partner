import os, time
# ---- FALLBACK HANDLER ----
def safe_invoke(models, structured_output=None, method=None, prompt=None, retries=2):
    """
    Try invoking multiple models in fallback order.
    """
    for i, model in enumerate(models):
        try:
            print(f"\n🧠 Using model: {model.model_name}")
            if structured_output:
                if method:
                    return model.with_structured_output(structured_output, method=method).invoke(prompt)
                else:
                    return model.with_structured_output(structured_output).invoke(prompt)
            else:
                return model.invoke(prompt)
        except Exception as e:
            print(f"⚠️ Model {model.model_name} failed with error: {str(e)}")
            if "rate_limit" in str(e).lower() or "429" in str(e):
                if retries > 0:
                    wait_time = 10 * (2 ** (i))  # exponential backoff
                    print(f"⏳ Rate limit hit. Retrying with next model in {wait_time}s...")
                    time.sleep(wait_time)
            continue
    raise RuntimeError("❌ All fallback models failed after retries.")