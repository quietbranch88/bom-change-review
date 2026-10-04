"""Read-only catalog and dedicated-key check; never calls chat completions."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    from live_model import OpenRouterTransport, MODEL, PROVIDER
    from openrouter_smoke import load_key
    transport = None
    try:
        transport = OpenRouterTransport(load_key(), enabled=True)
        quote = await transport.preflight()
        result = {"result": "passed", "mode": "preflight_only", "model": MODEL, "provider": PROVIDER,
                  "reservation_usd": str(quote), "paid_model_calls": 0}
    except Exception:
        result = {"result": "blocked", "mode": "preflight_only", "reason": "dedicated_key_or_endpoint_unavailable",
                  "stage": transport.preflight_stage if transport else "dedicated_key_load",
                  "http_status": transport.last_http_status if transport else None,
                  "paid_model_calls": 0}
    print(json.dumps(result))
    return 0 if result["result"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
