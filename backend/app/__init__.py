"""TestMyLLM backend."""
import os

# Decision D9: no telemetry from evaluation libraries, in every process (API, worker, tests).
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPTEAM_TELEMETRY_OPT_OUT", "YES")  # D29: DeepTeam attack generation
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_IN", "NO")
