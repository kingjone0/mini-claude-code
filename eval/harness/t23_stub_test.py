"""Offline proof of the scheduler's bounded submission/stop-loss policy."""
import time
from scheduler_p1 import _stop_reason, INFRA_STOP_CONSECUTIVE

def main():
    submitted = 0
    started = 35
    consecutive = {}
    results = ["infra_error", "infra_error", "infra_error", "infra_error"]
    for result in results:
        if _stop_reason({"started_arms": started, "input_uncached": 0,
                         "cache_read": 0, "cost_usd": 0}, time.time(), consecutive):
            break
        submitted += 1
        consecutive["A"] = consecutive.get("A", 0) + (result == "infra_error")
        started += 1
    reason = _stop_reason({"started_arms": started, "input_uncached": 0,
                           "cache_read": 0, "cost_usd": 0}, time.time(), consecutive)
    assert submitted == 2 and reason and "consecutive infra" in reason
    print(f"submitted={submitted} after_stop={len(results)-submitted} reason={reason}")

if __name__ == "__main__":
    main()
