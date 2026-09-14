import os
import pathlib
import sys

# agent.graph constructs its Groq client at import time, so a key must exist
# before any test imports it. A dummy value is enough -- constructing the client
# is all that is checked; no test makes a real request. load_dotenv() does not
# override already-set variables, so this survives an empty .env.
os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
