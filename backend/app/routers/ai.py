import json
import logging
import re
import sys
import os
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from fastapi import APIRouter, Depends, HTTPException, status

from app.models.schemas import (
    TopicIdeasIn, TitlesIn, ImagePromptsIn, IntrosIn, OutlinesIn, 
    ImageGenerateIn, ImageOut, GenerateBlogIn, OptionsOut, YoutubeBlogIn
)
import app.services.gemini_service as _gemini_svc
import app.services.openai_service as _openai_svc
from app.services.gemini_service import gen_youtube_blog_json
from app.services.image_service import generate_cover_image
from core.deps import get_current_user

# Add backend root to path so langfuse modules are importable
_backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from langfuse_observer import observe
from langfuse_tracer import set_current_input_data

logger = logging.getLogger(__name__)

AVAILABLE_TEXT_MODELS = [
    # ── Google Gemini ──────────────────────────────────────────────────────────
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "google",
        "tier": "pro",
        "speed": "slow",
        "badge": "Best Quality",
        "description": "Highest quality with deep thinking. Best for long-form, research-heavy blogs.",
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "google",
        "tier": "standard",
        "speed": "fast",
        "badge": "Recommended",
        "description": "Fast with great quality. Best balance for most blogs.",
    },
    {
        "id": "gemini-2.0-flash",
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "tier": "standard",
        "speed": "fast",
        "badge": None,
        "description": "Previous generation. Reliable and fast.",
    },
    # ── OpenAI ─────────────────────────────────────────────────────────────────
    {
        "id": "o3",
        "name": "OpenAI o3",
        "provider": "openai",
        "tier": "pro",
        "speed": "slow",
        "badge": "Best Reasoning",
        "description": "Maximum reasoning power. Best for technical, analytical blogs.",
    },
    {
        "id": "o4-mini",
        "name": "OpenAI o4 Mini",
        "provider": "openai",
        "tier": "standard",
        "speed": "medium",
        "badge": "Fast Reasoning",
        "description": "Reasoning model at faster speed. Great for structured content.",
    },
    {
        "id": "gpt-4.1",
        "name": "GPT-4.1",
        "provider": "openai",
        "tier": "pro",
        "speed": "medium",
        "badge": "Latest GPT",
        "description": "OpenAI's latest flagship. Excellent writing quality.",
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "provider": "openai",
        "tier": "standard",
        "speed": "fast",
        "badge": None,
        "description": "Balanced quality and speed.",
    },
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "provider": "openai",
        "tier": "budget",
        "speed": "fast",
        "badge": "Fastest",
        "description": "Lightweight and fast. Good for quick drafts.",
    },
]

AVAILABLE_IMAGE_MODELS = [
    {
        "id": "gemini",
        "name": "Imagen 3",
        "provider": "google",
        "badge": "Recommended",
        "description": "Google's Imagen 3. Vivid, high-detail illustrations.",
    },
    {
        "id": "openai",
        "name": "GPT Image 1",
        "provider": "openai",
        "badge": "Latest",
        "description": "OpenAI's latest image model. Realistic and versatile.",
    },
]

# OpenAI model prefixes — used for service routing
_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")

def _svc(model: str | None):
    """Pick gemini or openai service based on model name."""
    if model and any(model.startswith(p) for p in _OPENAI_PREFIXES):
        svc = _openai_svc
    else:
        svc = _gemini_svc
    logger.warning(f"[MODEL ROUTING] requested='{model or 'default'}' → service={svc.__name__}")
    return svc

router = APIRouter(dependencies=[Depends(get_current_user)])


def _raise_ai_error(err: Exception):
    msg = str(err)
    lower = msg.lower()

    if "resource_exhausted" in lower or "quota" in lower:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI quota exhausted. Check billing configuration."
        )
    if "getaddrinfo failed" in lower or "name resolution" in lower:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable. Network or DNS resolution failure."
        )
    if "response modalities" in lower and "image" in lower:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image generation unsupported by the current model configuration."
        )
    if "not found" in lower and "models/" in lower:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Specified AI model not found."
        )
    if "api_key" in lower or "missing key inputs" in lower:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key configuration missing."
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)


@router.get("/models", response_model=dict)
async def get_models():
    """Return available LLM and image models for the frontend to display."""
    return {"text_models": AVAILABLE_TEXT_MODELS, "image_models": AVAILABLE_IMAGE_MODELS}


@router.post("/ideas", response_model=OptionsOut)
@observe(name="api_topic_ideas", as_type="pipeline")
async def topic_ideas(payload: TopicIdeasIn):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        options = await _svc(data.get("model")).gen_topic_ideas(data)
        return {"options": options}
    except Exception as e:
        _raise_ai_error(e)


@router.post("/titles", response_model=OptionsOut)
@observe(name="api_titles", as_type="pipeline")
async def titles(payload: TitlesIn):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        options = await _svc(data.get("model")).gen_titles(data)
        return {"options": options}
    except Exception as e:
        _raise_ai_error(e)


@router.post("/intros", response_model=OptionsOut)
@observe(name="api_intros", as_type="pipeline")
async def intros(payload: IntrosIn):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        options = await _svc(data.get("model")).gen_intros(data)
        return {"options": options}
    except Exception as e:
        _raise_ai_error(e)


@router.post("/outlines", response_model=dict)
@observe(name="api_outlines", as_type="pipeline")
async def outlines(payload: OutlinesIn):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        options = await _svc(data.get("model")).gen_outlines(data)
        return {"options": options}
    except Exception as e:
        _raise_ai_error(e)


@router.post("/image-prompts", response_model=OptionsOut)
@observe(name="api_image_prompts", as_type="pipeline")
async def image_prompts(payload: ImagePromptsIn):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        options = await _svc(data.get("model")).gen_image_prompts(data)
        return {"options": options}
    except Exception as e:
        _raise_ai_error(e)


@router.post("/image-generate", response_model=ImageOut)
@observe(name="api_image_generate", as_type="pipeline")
async def image_generate(payload: ImageGenerateIn, user: dict = Depends(get_current_user)):
    try:
        data = payload.model_dump()
        set_current_input_data({k: v for k, v in data.items() if k != "save_to_gallery"})
        save_to_gallery = data.pop("save_to_gallery", True)
        result = await generate_cover_image(data)
        if save_to_gallery:
            logger.info(f"Image generated for user {user.get('id')}. Skipping Firestore, ready for Postgres.")
        return result
    except Exception as e:
        logger.error(f"Image generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


def _extract_youtube_transcript(url: str) -> str:
    if not url:
        return ""
    video_id = None
    if "youtu.be" in url:
        video_id = url.split("/")[-1].split("?")[0]
    elif "youtube.com" in url:
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        video_id = query_params.get("v", [None])[0]
    if not video_id:
        return ""
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        first_available = next(iter(transcript_list))
        transcript_data = first_available.fetch()
        full_text = []
        for item in transcript_data:
            text = getattr(item, "text", "")
            if not text and isinstance(item, dict):
                text = item.get("text", "")
            if text:
                full_text.append(text.replace('\n', ' '))
        return " ".join(full_text)
    except Exception as e:
        logger.error(f"Failed to fetch YouTube transcript: {e}", exc_info=True)
        return ""

@router.post("/youtube-to-blog")
@observe(name="api_youtube_to_blog", as_type="pipeline")
async def youtube_to_blog(payload: YoutubeBlogIn, user: dict = Depends(get_current_user)):
    try:
        data = payload.model_dump()
        set_current_input_data(data)
        transcript = _extract_youtube_transcript(data["youtube_url"])
        if not transcript:
            raise HTTPException(status_code=400, detail="Could not extract transcript.")
        data["youtube_transcript"] = transcript
        structured_blog = await gen_youtube_blog_json(data)
        if "meta" not in structured_blog or "final_blog" not in structured_blog:
            raise HTTPException(status_code=500, detail="AI failed to generate correct JSON structure.")
        return {
            "blog_id": "temp-youtube-id", 
            "meta": {
                **structured_blog["meta"],
                "language": data["language"],
                "tone": data["tone"],
                "youtube_url": data["youtube_url"],
                "image_count": data["image_count"],
            },
            "final_blog": structured_blog["final_blog"]
        }
    except Exception as e:
        logger.error(f"YouTube to Blog failed: {str(e)}", exc_info=True)
        _raise_ai_error(e)


@router.post("/blog-generate")
@observe(name="api_blog_generate", as_type="pipeline")
async def blog_generate(payload: GenerateBlogIn):
    try:
        payload_dict = payload.model_dump()
        set_current_input_data(payload_dict)
        if payload.youtube_url:
            transcript = _extract_youtube_transcript(payload.youtube_url)
            if transcript:
                payload_dict["youtube_transcript"] = transcript

        raw_text = await _svc(payload_dict.get("model")).gen_final_blog_markdown(payload_dict)
        clean_markdown = raw_text.strip()

        try:
            json_str = re.sub(r'^```(json)?\n', '', clean_markdown)
            json_str = re.sub(r'\n```$', '', json_str)
            parsed_json = json.loads(json_str)
            for key, value in parsed_json.items():
                if isinstance(value, str) and "#" in value:
                    clean_markdown = value
                    break
        except Exception:
            pass

        clean_markdown = clean_markdown.replace("\\n", "\n")
        lines = clean_markdown.split('\n')
        intro_lines = []
        sections = []
        conclusion_lines = []
        current_mode = "intro"
        current_heading = ""
        current_content = []

        for line in lines:
            if line.startswith("# "):
                continue
            if line.startswith("## "):
                if current_mode == "section":
                    sections.append({
                        "heading": current_heading,
                        "content_md": "\n".join(current_content).strip()
                    })
                heading_text = line.replace("## ", "").strip()
                if "conclusion" in heading_text.lower() or "summary" in heading_text.lower():
                    current_mode = "conclusion"
                else:
                    current_mode = "section"
                    current_heading = heading_text
                    current_content = []
            else:
                if current_mode == "intro":
                    intro_lines.append(line)
                elif current_mode == "section":
                    current_content.append(line)
                elif current_mode == "conclusion":
                    conclusion_lines.append(line)
                    
        if current_mode == "section" and current_heading:
            sections.append({
                "heading": current_heading,
                "content_md": "\n".join(current_content).strip()
            })

        return {
            "markdown": clean_markdown,
            "render": {
                "title": payload_dict.get("title", "Untitled"),
                "intro_md": "\n".join(intro_lines).strip(),
                "sections": sections,
                "conclusion_md": "\n".join(conclusion_lines).strip()
            }
        }
    except Exception as e:
        logger.error(f"Generation failed: {str(e)}", exc_info=True)
        _raise_ai_error(e)