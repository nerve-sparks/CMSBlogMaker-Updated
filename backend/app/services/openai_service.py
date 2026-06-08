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
    prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Targeted keyword: {payload.get('targeted_keyword','')}
    Targeted audience: {payload.get('targeted_audience','')}
    Reference links: {payload.get('reference_links','')}

    Generate exactly {AI_OPTIONS_COUNT} blog topic ideas.
    Each idea must be a single sentence, clear and specific.
    Return a JSON object with an "options" array containing exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that returns only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    result = json.loads(response.choices[0].message.content)
    return result.get("options", [])

@observe(name="gen_titles_openai", as_type="generation")
async def gen_titles(payload: dict) -> List[str]:
    prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}

    Generate exactly {AI_OPTIONS_COUNT} SEO-friendly blog titles.
    No quotes, no emojis.
    Return a JSON object with an "options" array containing exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that returns only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    result = json.loads(response.choices[0].message.content)
    return result.get("options", [])

@observe(name="gen_intros_openai", as_type="generation")
async def gen_intros(payload: dict) -> List[str]:
    prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}

    Generate exactly {AI_OPTIONS_COUNT} intro paragraphs in Markdown.
    Each intro: 80-140 words.
    Return a JSON object with an "options" array containing exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that returns only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    result = json.loads(response.choices[0].message.content)
    return result.get("options", [])

class _OutlineVariant(BaseModel):
    outline: List[str] = Field(min_length=6, max_length=12)

class _OutlineOptions(BaseModel):
    options: List[_OutlineVariant] = Field(min_length=AI_OPTIONS_COUNT, max_length=AI_OPTIONS_COUNT)

@observe(name="gen_outlines_openai", as_type="generation")
async def gen_outlines(payload: dict):
    prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Intro: {payload['intro_md']}

    Generate exactly {AI_OPTIONS_COUNT} outline variants.
    Each outline should be 6-10 headings.
    Headings must be short and not numbered.
    Return a JSON object with an "options" array containing exactly {AI_OPTIONS_COUNT} objects.
    Each object should have an "outline" array with 6-12 string headings.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that returns only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    result = json.loads(response.choices[0].message.content)
    return result.get("options", [])

@observe(name="gen_image_prompts_openai", as_type="generation")
async def gen_image_prompts(payload: dict) -> List[str]:
    prompt = dedent(f"""
    {_sys(payload['tone'], payload['creativity'])}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}

    Generate exactly {AI_OPTIONS_COUNT} blog cover image prompts.
    Avoid text/logos/watermarks.
    Return a JSON object with an "options" array containing exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that returns only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    result = json.loads(response.choices[0].message.content)
    return result.get("options", [])

# Final blog generation returns ONE markdown (not 5)
@observe(name="cms_blog_maker", as_type="generation")
async def gen_final_blog_markdown(payload: dict) -> str:
    refs = payload.get("reference_links", "")

    system_prompt_text = _sys(payload['tone'], payload['creativity'])
    set_current_system_prompt(system_prompt_text)

    prompt = dedent(f"""
    {system_prompt_text}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Audience: {payload.get('targeted_audience','')}
    Reference links: {refs}

    Selected idea: {payload['selected_idea']}
    Title: {payload['title']}
    Intro (markdown): {payload['intro_md']}
    Outline headings: {payload['outline']}
    Cover image url: {payload.get('cover_image_url','')}

    Write a complete blog post in Markdown.
    Rules:
    - Start with '# {{Title}}'
    - If cover_image_url is not empty, include: ![Cover](cover_image_url)
    - Use '##' headings based on the outline
    - Include a '## Conclusion' section
    - If reference links exist, include '## References' with bullet links.
    Return ONLY the Markdown text.
    """).lstrip("\n")

    client = _get_client()
    response = client.chat.completions.create(
        model=settings.OPENAI_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a senior blog writer. Return only the Markdown text, no additional commentary."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
    )

    _capture_openai_usage(response)
    
    return (response.choices[0].message.content or "").strip()
