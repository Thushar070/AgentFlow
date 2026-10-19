"""Run exactly one registered action in an interruptible child process."""

import json
import sys

from agentflow.registry import ActionFailure, run_action


def main() -> int:
    try:
        request = json.load(sys.stdin)
        output = run_action(request["action"], request["inputs"])
    except ActionFailure as exc:
        response = {"ok": False, "category": exc.category.value, "error": exc.safe_message}
    except Exception:
        response = {
            "ok": False,
            "category": "execution_failed",
            "error": "Action execution failed.",
        }
    else:
        response = {"ok": True, "output": output}
    sys.stdout.write(json.dumps(response, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
