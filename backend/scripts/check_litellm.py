"""
Verify the LiteLLM proxy setup end to end.

Run after setting USE_LITELLM / LLM_BASE_URL / LLM_API_KEY in .env:

    cd backend && ./venv/Scripts/python.exe scripts/check_litellm.py

Checks, in order:
  1. credentials are present and the proxy is reachable
  2. every _LITELLM_MODEL_MAP target actually exists on the proxy
  3. every model in the app's picker returns usable JSON through the proxy

Prints model ids only — never the API key.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from core.config import settings  # noqa: E402
from app.routers.ai import AVAILABLE_TEXT_MODELS  # noqa: E402
from app.services.gemini_service import _LITELLM_MODEL_MAP, _to_litellm_model  # noqa: E402


def main() -> int:
    print("=" * 66)
    print(f"USE_LITELLM      = {settings.USE_LITELLM}")
    print(f"LLM_BASE_URL     = {settings.LLM_BASE_URL or '(unset)'}")
    print(f"LLM_API_KEY set  = {bool(settings.LLM_API_KEY)}")
    print("=" * 66)

    if not settings.LLM_BASE_URL or not settings.LLM_API_KEY:
        print("\nFAIL: set LLM_BASE_URL and LLM_API_KEY in backend/.env first.")
        return 1
    if not settings.USE_LITELLM:
        print("\nNote: USE_LITELLM=false — the app still calls providers directly.")
        print("      Probing the proxy anyway so the mapping can be verified.\n")

    from openai import OpenAI

    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)

    # 1. What does the proxy actually serve?
    try:
        available = sorted(m.id for m in client.models.list().data)
    except Exception as e:
        print(f"\nFAIL: could not reach the proxy — {type(e).__name__}: {e}")
        return 1

    print(f"\n--- proxy serves {len(available)} model(s) ---")
    for mid in available:
        print(f"  {mid}")

    # 2. Are our mapping targets real?
    print("\n--- _LITELLM_MODEL_MAP targets ---")
    bad = []
    for app_id, proxy_id in _LITELLM_MODEL_MAP.items():
        ok = proxy_id in available
        print(f"  {'OK  ' if ok else 'MISSING'}  {app_id:<26} -> {proxy_id}")
        if not ok:
            bad.append((app_id, proxy_id))

    # 3. Does each picker model actually generate through the proxy?
    print("\n--- live call per picker model ---")
    failures = 0
    for m in AVAILABLE_TEXT_MODELS:
        target = _to_litellm_model(m["id"])
        try:
            r = client.chat.completions.create(
                model=target,
                messages=[{"role": "user", "content": 'Return ONLY JSON: {"ok": true}'}],
                response_format={"type": "json_object"},
            )
            text = (r.choices[0].message.content or "").strip()
            status = "OK  " if "{" in text else "NO JSON"
            print(f"  {status}  {m['id']:<26} -> {target:<28} {text[:40]!r}")
            if "{" not in text:
                failures += 1
        except Exception as e:
            print(f"  FAIL  {m['id']:<26} -> {target:<28} {type(e).__name__}: {str(e)[:70]}")
            failures += 1

    print("\n" + "=" * 66)
    if bad:
        print("Fix these in _LITELLM_MODEL_MAP (app/services/gemini_service.py):")
        for app_id, proxy_id in bad:
            print(f"  {app_id!r}: {proxy_id!r}  <- not served by the proxy")
        print("Pick the closest id from the list above.")
    if failures:
        print(f"{failures} model(s) failed to generate. See errors above.")
        return 1
    if not bad:
        print("All picker models generate correctly through the proxy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
