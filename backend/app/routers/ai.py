import logging
import sys
import os
import requests as _requests
from urllib.parse import urlparse, parse_qs
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
]

AVAILABLE_IMAGE_MODELS = [
    {
        "id": "gemini",
        "name": "Imagen 3",
        "provider": "google",
        "badge": "Recommended",
        "description": "Google's Imagen 3. Vivid, high-detail illustrations.",
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


# Simple in-process transcript cache — avoids double Supadata calls within same session
# { video_id: (transcript_str, timestamp) }
import time as _time
_transcript_cache: dict = {}
_TRANSCRIPT_CACHE_TTL = 3600  # 1 hour


def _get_video_id(url: str) -> str | None:
    if not url:
        return None
    if "youtu.be" in url:
        vid = url.split("/")[-1].split("?")[0]
        return vid or None
    if "youtube.com" in url:
        qs = parse_qs(urlparse(url).query)
        return qs.get("v", [None])[0]
    return None


def _extract_youtube_transcript(url: str) -> str:
    """Fetch transcript via Supadata API with in-memory cache to avoid duplicate calls."""
    from core.config import settings

    if not url:
        return ""

    if not settings.SUPADATA_API_KEY:
        raise RuntimeError("SUPADATA_API_KEY is not configured. Sign up at supadata.ai to get a key.")

    # Check cache first
    video_id = _get_video_id(url) or url
    cached = _transcript_cache.get(video_id)
    if cached:
        transcript, fetched_at = cached
        if _time.time() - fetched_at < _TRANSCRIPT_CACHE_TTL:
            logger.warning(f"[YOUTUBE] Transcript served from cache for {video_id} ({len(transcript)} chars).")
            return transcript

    try:
        logger.warning(f"[YOUTUBE] Fetching transcript via Supadata for: {url}")
        resp = _requests.get(
            "https://api.supadata.ai/v1/youtube/transcript",
            params={"url": url, "text": True},
            headers={"x-api-key": settings.SUPADATA_API_KEY},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("content", "")
        if not content:
            raise RuntimeError("Supadata returned empty transcript.")

        transcript = content if isinstance(content, str) else " ".join(
            chunk.get("text", "") for chunk in content if isinstance(chunk, dict)
        )
        transcript = transcript.strip()

        # Store in cache
        _transcript_cache[video_id] = (transcript, _time.time())

        logger.warning(f"[YOUTUBE] Transcript fetched — {len(transcript)} chars.")
        return transcript

    except _requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else "unknown"
        logger.error(f"[YOUTUBE] Supadata HTTP error {status_code}: {e}")
        raise RuntimeError(f"Supadata API error ({status_code}). Check your API key or quota at supadata.ai.")
    except Exception as e:
        logger.error(f"[YOUTUBE] Transcript extraction failed: {e}", exc_info=True)
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
        from app.services.reference_service import fetch_reference_content

        payload_dict = payload.model_dump()
        set_current_input_data(payload_dict)

        if payload.youtube_url and not payload.youtube_transcript:
            transcript = _extract_youtube_transcript(payload.youtube_url)
            if transcript:
                payload_dict["youtube_transcript"] = transcript

        # Fetch reference URL content before calling LLM
        if payload.reference_links and payload.reference_links.strip():
            payload_dict["reference_content"] = fetch_reference_content(payload.reference_links)

        structured_blog = await _svc(payload_dict.get("model")).gen_final_blog_json(payload_dict)

        title = structured_blog.get("title") or payload_dict.get("title", "Untitled")
        intro_md = structured_blog.get("intro_md", "")
        sections = structured_blog.get("sections", [])
        if not isinstance(sections, list):
            sections = []
        conclusion_md = structured_blog.get("conclusion_md", "")

        markdown_parts = [f"# {title}", "", intro_md]
        for section in sections:
            markdown_parts += ["", f"## {section.get('heading', '')}", "", section.get("content_md", "")]
        if conclusion_md:
            markdown_parts += ["", "## Conclusion", "", conclusion_md]
        clean_markdown = "\n".join(markdown_parts).strip()

        return {
            "markdown": clean_markdown,
            "render": {
                "title": title,
                "intro_md": intro_md,
                "sections": sections,
                "conclusion_md": conclusion_md
            }
        }
    except Exception as e:
        logger.error(f"Generation failed: {str(e)}", exc_info=True)
        _raise_ai_error(e)