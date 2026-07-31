from typing import List
from textwrap import dedent
import json
import sys
import os

from openai import OpenAI
from pydantic import BaseModel, Field

from core.config import settings
from app.models.schemas import AI_OPTIONS_COUNT

# Add backend root to path so langfuse modules are importable
_backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from langfuse_observer import observe
from langfuse_tracer import set_current_usage_metrics, set_current_system_prompt

# Initialize client lazily to avoid import errors if API key is missing
_client = None

def _get_client():
    global _client
    if _client is None:
        if settings.USE_LITELLM:
            if not settings.LLM_API_KEY or not settings.LLM_BASE_URL:
                raise RuntimeError("LLM_API_KEY and LLM_BASE_URL must be set when USE_LITELLM=true.")
            _client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
        else:
            if not settings.OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY is not set.")
            _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client

# ---------- schemas for structured outputs ----------
class _StringOptions(BaseModel):
    options: List[str] = Field(min_length=AI_OPTIONS_COUNT, max_length=AI_OPTIONS_COUNT)

def _sys(tone: str, creativity: str) -> str:
    return (
        "You are a senior blog writer.\n"
        "Language must be English.\n"
        f"Tone: {tone}\n"
        f"Creativity: {creativity}\n"
        "Return ONLY valid JSON according to the schema.\n"
    )

# o-series models (o1, o3, o4-*) don't support temperature or system messages
_REASONING_PREFIXES = ("o1", "o3", "o4")

def _is_reasoning_model(model: str) -> bool:
    return any(model.startswith(p) for p in _REASONING_PREFIXES)

def _chat_json(client, model: str, system: str, user: str) -> str:
    """Call chat completions and return raw content string. Handles o-series quirks."""
    if _is_reasoning_model(model):
        # o-series: no temperature, no system role, no response_format
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"{system}\n\n{user}"}],
        )
    else:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
    _capture_openai_usage(response)
    return response.choices[0].message.content or ""

def _capture_openai_usage(response):
    """Forward OpenAI usage metrics to Langfuse (best effort)."""
    try:
        if response.usage:
            set_current_usage_metrics({
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }, model=settings.OPENAI_TEXT_MODEL)
    except Exception:
        pass

@observe(name="gen_topic_ideas_openai", as_type="generation")
async def gen_topic_ideas(payload: dict) -> List[str]:
    sys_prompt = "You are a helpful assistant that returns only valid JSON."
    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Targeted keyword: {payload.get('targeted_keyword','')}
    Targeted audience: {payload.get('targeted_audience','')}
    Generate exactly {AI_OPTIONS_COUNT} blog topic ideas. Each must be a single clear sentence.
    Return a JSON object: {{"options": [...]}} with exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    raw = _chat_json(_get_client(), model, sys_prompt, user_prompt)
    return json.loads(raw).get("options", [])


@observe(name="gen_titles_openai", as_type="generation")
async def gen_titles(payload: dict) -> List[str]:
    sys_prompt = "You are a helpful assistant that returns only valid JSON."
    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}
    Generate exactly {AI_OPTIONS_COUNT} SEO-friendly blog titles. No quotes, no emojis.
    Return a JSON object: {{"options": [...]}} with exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    raw = _chat_json(_get_client(), model, sys_prompt, user_prompt)
    return json.loads(raw).get("options", [])


@observe(name="gen_intros_openai", as_type="generation")
async def gen_intros(payload: dict) -> List[str]:
    sys_prompt = "You are a helpful assistant that returns only valid JSON."
    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Generate exactly {AI_OPTIONS_COUNT} intro paragraphs in Markdown, each 80-140 words.
    Return a JSON object: {{"options": [...]}} with exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    raw = _chat_json(_get_client(), model, sys_prompt, user_prompt)
    return json.loads(raw).get("options", [])


@observe(name="gen_outlines_openai", as_type="generation")
async def gen_outlines(payload: dict):
    sys_prompt = "You are a helpful assistant that returns only valid JSON."
    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Intro: {payload['intro_md']}
    Generate exactly {AI_OPTIONS_COUNT} outline variants, each with 6-10 short headings (not numbered).
    Return a JSON object: {{"options": [{{"outline": [...]}}]}} with exactly {AI_OPTIONS_COUNT} objects.
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    raw = _chat_json(_get_client(), model, sys_prompt, user_prompt)
    return json.loads(raw).get("options", [])


@observe(name="gen_image_prompts_openai", as_type="generation")
async def gen_image_prompts(payload: dict) -> List[str]:
    sys_prompt = "You are a helpful assistant that returns only valid JSON."
    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Generate exactly {AI_OPTIONS_COUNT} blog cover image prompts. Avoid text, logos, watermarks.
    Return a JSON object: {{"options": [...]}} with exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    raw = _chat_json(_get_client(), model, sys_prompt, user_prompt)
    return json.loads(raw).get("options", [])


@observe(name="cms_blog_maker", as_type="generation")
async def gen_final_blog_json(payload: dict) -> dict:
    sys_prompt = "You are a senior blog writer. Return only valid JSON, no commentary."
    set_current_system_prompt(sys_prompt)

    reference_content = payload.get("reference_content", "").strip()
    reference_block = (
        f"\nReference material to draw from (quote, paraphrase, use as source):\n{reference_content}\n"
        if reference_content else ""
    )

    user_prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    {reference_block}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Intro (markdown): {payload['intro_md']}
    Outline headings: {payload['outline']}
    Cover image url: {payload.get('cover_image_url','')}

    Write a complete blog post.
    Rules:
    - Include a section for the conclusion.
    - Use the outline headings to build the "sections" array, in order.
    - Section "content_md" values should be Markdown (no headings inside them).
    - If reference material was provided, incorporate facts and insights from it naturally.

    Return ONLY a valid JSON object matching this exact schema:
    {{
      "title": "{payload['title']}",
      "intro_md": "Introduction in markdown",
      "sections": [
        {{"heading": "Heading 1", "content_md": "Markdown content for this section."}}
      ],
      "conclusion_md": "Concluding paragraph in markdown."
    }}
    """).lstrip("\n")

    model = payload.get("model") or settings.OPENAI_TEXT_MODEL
    client = _get_client()
    raw = _chat_json(client, model, sys_prompt, user_prompt)
    return json.loads(raw)
