from typing import List, Optional
from textwrap import dedent
import logging
import json
import sys
import os

import google.generativeai as genai
from pydantic import BaseModel, Field, create_model

from core.config import settings
from app.models.schemas import AI_OPTIONS_COUNT

# Add backend root to path so langfuse modules are importable
_backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from langfuse_observer import observe
from langfuse_tracer import set_current_usage_metrics, set_current_system_prompt


def _get_model() -> "genai.GenerativeModel":
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    genai.configure(api_key=settings.GEMINI_API_KEY)
    model_name = settings.GEMINI_TEXT_MODEL or "gemini-1.5-flash"
    return genai.GenerativeModel(model_name)

# ---------- schemas for structured outputs ----------
class _StringOptions(BaseModel):
    options: List[str] = Field(min_length=AI_OPTIONS_COUNT, max_length=AI_OPTIONS_COUNT)

def _string_options_schema(count: int) -> type[BaseModel]:
    return create_model(
        f"_StringOptions_{count}",
        options=(List[str], Field(min_length=count, max_length=count)),
    )

def _sys(tone: str, creativity: str, language: str = "English") -> str:
    return (
        "You are a senior blog writer.\n"
        f"CRITICAL LANGUAGE RULE: You MUST generate the readable content entirely in {language}. Even if the user's input, keywords, niche, or selected idea are written in English, you MUST translate your response and output the text in {language}.\n"
        "CRITICAL JSON RULE: If returning JSON, YOU MUST KEEP ALL JSON KEYS IN ENGLISH. Only translate the values.\n"
        f"Tone: {tone}\n"
        f"Creativity: {creativity}\n"
        "Return ONLY valid JSON according to the schema.\n"
    )


def _call_json_model(prompt: str, system_prompt: Optional[str] = None) -> dict:
    """Call Gemini and parse JSON response from text."""
    model = _get_model()

    # Capture system prompt in Langfuse metadata (best effort)
    try:
        if system_prompt:
            set_current_system_prompt(system_prompt)
    except Exception:
        pass

    resp = model.generate_content(prompt)

    # Forward token usage to Langfuse (best effort)
    try:
        usage_meta = getattr(resp, "usage_metadata", None)
        if usage_meta:
            set_current_usage_metrics({
                "input_tokens": getattr(usage_meta, "prompt_token_count", None),
                "output_tokens": getattr(usage_meta, "candidates_token_count", None),
                "total_tokens": getattr(usage_meta, "total_token_count", None),
            }, model=settings.GEMINI_TEXT_MODEL)
    except Exception:
        pass

    text = (resp.text or "").strip()
    
    # Find where the JSON actually begins
    start_idx = text.find('{')
    if start_idx == -1:
        logging.error("No JSON object found in response: %r", text)
        raise ValueError("No JSON object found in AI response")
        
    try:
        # raw_decode reads the very first complete JSON object and automatically 
        # ignores any trailing text, markdown, or extra objects the AI appended!
        decoder = json.JSONDecoder()
        obj, idx = decoder.raw_decode(text[start_idx:])
        return obj
    except Exception as e:
        logging.error("Failed to parse JSON from Gemini response: %s\nRaw: %r", e, text)
        raise


@observe(name="gen_topic_ideas", as_type="generation")
async def gen_topic_ideas(payload: dict) -> List[str]:
    sys_prompt = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
    prompt = dedent(f"""
    {sys_prompt}
    Focus/Niche: {payload['focus_or_niche']}
    Targeted keyword: {payload.get('targeted_keyword','')}
    Targeted audience: {payload.get('targeted_audience','')}
    Reference links: {payload.get('reference_links','')}

    Generate exactly {AI_OPTIONS_COUNT} blog topic ideas.
    Each idea must be a single sentence, clear and specific.

    Return a JSON object: {{"options": [ ... ]}} with exactly {AI_OPTIONS_COUNT} strings.
    """).lstrip("\n")

    data = _call_json_model(prompt, system_prompt=sys_prompt)
    options = data.get("options") or []
    if not isinstance(options, list):
        raise ValueError("Gemini topic ideas response missing 'options' list")
    return [str(o) for o in options][:AI_OPTIONS_COUNT]

@observe(name="gen_titles", as_type="generation")
async def gen_titles(payload: dict) -> List[str]:
    try:
        sys_prompt = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
        prompt = dedent(f"""
        {sys_prompt}
        Focus/Niche: {payload['focus_or_niche']}
        Keyword: {payload.get('targeted_keyword','')}
        Audience: {payload.get('targeted_audience','')}
        Selected idea: {payload['selected_idea']}

        Generate exactly {AI_OPTIONS_COUNT} SEO-friendly blog titles.
        No quotes, no emojis.

        Return a JSON object: {{"options": [ ... ]}} with exactly {AI_OPTIONS_COUNT} strings.
        """).lstrip("\n")

        data = _call_json_model(prompt, system_prompt=sys_prompt)
        options = data.get("options") or []
        if not isinstance(options, list):
            raise ValueError("Gemini titles response missing 'options' list")
        return [str(o) for o in options][:AI_OPTIONS_COUNT]
    except Exception as e:
        logging.error(f"Error generating titles: {e}")
        raise

@observe(name="gen_intros", as_type="generation")
async def gen_intros(payload: dict) -> List[str]:
    try:
        sys_prompt = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
        prompt = dedent(f"""
        {sys_prompt}
        Focus/Niche: {payload['focus_or_niche']}
        Keyword: {payload.get('targeted_keyword','')}
        Audience: {payload.get('targeted_audience','')}
        Selected idea: {payload['selected_idea']}
        Title: {payload['title']}

        Generate exactly {AI_OPTIONS_COUNT} intro paragraphs in Markdown.
        Each intro: 80-140 words.

        Return a JSON object: {{"options": [ ... ]}} with exactly {AI_OPTIONS_COUNT} strings.
        """).lstrip("\n")

        data = _call_json_model(prompt, system_prompt=sys_prompt)
        options = data.get("options") or []
        if not isinstance(options, list):
            raise ValueError("Gemini intros response missing 'options' list")
        return [str(o) for o in options][:AI_OPTIONS_COUNT]
    except Exception as e:
        logging.error(f"Error generating intros: {e}")
        raise

class _OutlineVariant(BaseModel):
    outline: List[str] = Field(min_length=6, max_length=12)

class _OutlineOptions(BaseModel):
    options: List[_OutlineVariant] = Field(min_length=AI_OPTIONS_COUNT, max_length=AI_OPTIONS_COUNT)

@observe(name="gen_outlines", as_type="generation")
async def gen_outlines(payload: dict):
    try:
        sys_prompt = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
        prompt = dedent(f"""
        {sys_prompt}
        Focus/Niche: {payload['focus_or_niche']}
        Keyword: {payload.get('targeted_keyword','')}
        Audience: {payload.get('targeted_audience','')}
        Selected idea: {payload['selected_idea']}
        Title: {payload['title']}
        Intro: {payload['intro_md']}

        Generate exactly {AI_OPTIONS_COUNT} outline variants.
        Each outline should be 6-10 headings.
        Headings must be short and not numbered.

        Return a JSON object: {{"options": [{{"outline": [..] }}, ...]}}.
        """).lstrip("\n")

        data = _call_json_model(prompt, system_prompt=sys_prompt)
        options = data.get("options") or []
        if not isinstance(options, list):
            raise ValueError("Gemini outlines response missing 'options' list")
        normalized = []
        for o in options[:AI_OPTIONS_COUNT]:
            outline = (o or {}).get("outline") if isinstance(o, dict) else None
            if not isinstance(outline, list):
                continue
            normalized.append({"outline": [str(h) for h in outline]})
        return normalized
    except Exception as e:
        logging.error(f"Error generating outlines: {e}")
        raise

@observe(name="gen_image_prompts", as_type="generation")
async def gen_image_prompts(payload: dict) -> List[str]:
    try:
        # Note: Image prompts are usually best kept in English for AI image generators, 
        # but we are passing the selected language just in case you want the user to see the options in their language.
        sys_prompt = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
        prompt = dedent(f"""
        {sys_prompt}
        Focus/Niche: {payload['focus_or_niche']}
        Keyword: {payload.get('targeted_keyword','')}
        Selected idea: {payload['selected_idea']}
        Title: {payload['title']}

        Generate exactly {AI_OPTIONS_COUNT} blog cover image prompts.
        Avoid text/logos/watermarks.

        Return a JSON object: {{"options": [ ... ]}} with exactly {AI_OPTIONS_COUNT} strings.
        """).lstrip("\n")

        data = _call_json_model(prompt, system_prompt=sys_prompt)
        options = data.get("options") or []
        if not isinstance(options, list):
            raise ValueError("Gemini image prompts response missing 'options' list")
        return [str(o) for o in options][:AI_OPTIONS_COUNT]
    except Exception as e:
        logging.error(f"Error generating image prompts: {e}")
        raise

# Final blog generation returns ONE markdown (not 5)
@observe(name="gen_final_blog_markdown", as_type="generation")
async def gen_final_blog_markdown(payload: dict) -> str:
    refs = payload.get("reference_links", "")
    cover = payload.get("cover_image_url", "")
    title = payload["title"]

    cover_rule = (
        f'- Second element must be {{"type": "image", "url": "{cover}", "alt": "Cover"}}'
        if cover else ""
    )
    refs_rule = (
        '- End with a {"type": "h2", "content": "References"} block followed by a {"type": "ul", "items": [...]} block with the reference links'
        if refs else ""
    )

    system_prompt_text = _sys(payload.get('tone', 'Formal'), payload.get('creativity', 'Regular'), payload.get('language', 'English'))
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

    model = _get_model()
    resp = model.generate_content(
        prompt,
        generation_config={"temperature": 0.7},
    )

    # Forward token usage to Langfuse
    try:
        usage_meta = getattr(resp, "usage_metadata", None)
        if usage_meta:
            set_current_usage_metrics({
                "input_tokens": getattr(usage_meta, "prompt_token_count", None),
                "output_tokens": getattr(usage_meta, "candidates_token_count", None),
                "total_tokens": getattr(usage_meta, "total_token_count", None),
            }, model=settings.GEMINI_TEXT_MODEL)
    except Exception:
        pass

    return (resp.text or "").strip()


@observe(name="gen_youtube_blog_json", as_type="generation")
async def gen_youtube_blog_json(payload: dict) -> dict:
    """
    Takes a YouTube transcript and user preferences, and generates a fully
    structured blog post in JSON format (Lego Blocks).
    """
    transcript = payload.get("youtube_transcript", "")
    tone = payload.get("tone", "Formal")
    language = payload.get("language", "English")

    system_prompt_text = (
        "You are an elite, senior technical blog writer.\n"
        "Your task is to convert a YouTube video transcript into a highly engaging, structured blog post."
    )
    set_current_system_prompt(system_prompt_text)
    
    prompt = dedent(f"""
    You are an elite, senior technical blog writer.
    Your task is to convert the following YouTube video transcript into a highly engaging, structured blog post.

    REQUIREMENTS:
    - Tone: {tone}
    - Language: The actual blog content MUST be written in {language}. If the transcript below is in Hindi, Spanish, or any other language, you MUST accurately translate it.
    - CRITICAL RULE: YOU MUST KEEP ALL JSON KEYS IN ENGLISH. Do not translate the keys like "meta", "final_blog", "title", "intro_md", "outline", "render", "sections", "heading", "content_md", "conclusion_md". Only translate the values.
    - Quality: Do not just summarize. Write a comprehensive, standalone article that flows naturally.
    
    You MUST return ONLY a valid JSON object matching this exact schema:
    {{
      "meta": {{
        "title": "A catchy, SEO-friendly title",
        "intro_md": "A strong 1-2 paragraph introduction in markdown",
        "outline": ["Heading 1", "Heading 2", "Heading 3"] 
      }},
      "final_blog": {{
        "render": {{
          "title": "A catchy, SEO-friendly title (same as above)",
          "intro_md": "The exact same intro markdown",
          "sections": [
            {{
              "heading": "Heading 1",
              "content_md": "Detailed markdown content for this section."
            }},
            {{
              "heading": "Heading 2",
              "content_md": "Detailed markdown content for this section."
            }}
          ],
          "conclusion_md": "A strong concluding paragraph in markdown."
        }}
      }}
    }}

    YOUTUBE TRANSCRIPT:
    {transcript[:25000]}  
    """).strip()

    data = _call_json_model(prompt)
    return data