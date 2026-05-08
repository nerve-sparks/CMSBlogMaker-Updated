import base64
import logging
import os
import uuid
from io import BytesIO
from textwrap import dedent

import requests
from PIL import Image
from google import genai
from google.genai import types
from openai import OpenAI
from google.cloud import storage

from core.config import settings

logger = logging.getLogger(__name__)

# Get absolute path to uploads directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

_client = None
_storage_client = None

def _normalize_model(name: str) -> str:
    if not name:
        return name
    return name if name.startswith("models/") else f"models/{name}"

def _get_client() -> genai.Client:
    global _client
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    if _client is None:
        try:
            _client = genai.Client(api_key=settings.GEMINI_API_KEY)
        except Exception as e:
            logger.warning(f"Failed to initialize Gemini client: {e}")
            raise
    return _client

openai_client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

def _get_storage_client() -> storage.Client:
    """
    Get Google Cloud Storage client.
    Uses GOOGLE_APPLICATION_CREDENTIALS for GCS bucket access.
    This is separate from FIREBASE_CREDENTIALS_PATH used for Firestore.
    """
    global _storage_client
    if _storage_client is None:
        from google.oauth2 import service_account
        
        # Use GOOGLE_APPLICATION_CREDENTIALS for GCS bucket (separate from Firestore credentials)
        creds_path = None
        
        # Priority: GOOGLE_APPLICATION_CREDENTIALS environment variable
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        # Fallback: settings.GOOGLE_APPLICATION_CREDENTIALS
        elif settings.GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(settings.GOOGLE_APPLICATION_CREDENTIALS):
            creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        
        if creds_path and os.path.exists(creds_path):
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            _storage_client = storage.Client(credentials=credentials, project=settings.FIREBASE_PROJECT_ID)
            logger.info(f"GCS Storage client initialized with credentials from: {creds_path}")
        else:
            
            _storage_client = storage.Client(project=settings.FIREBASE_PROJECT_ID)
            logger.info("GCS Storage client initialized with default credentials")
    return _storage_client

def _require_bucket() -> str:
    bucket = settings.GCS_BUCKET
    if not bucket:
        raise RuntimeError("GCS_BUCKET is not set.")
    return bucket

def _gcs_object_name(filename: str) -> str:
    prefix = (settings.GCS_FOLDER or "").strip("/")
    return f"{prefix}/{filename}" if prefix else filename

def _gcs_public_url(object_name: str) -> str:
    base = (settings.GCS_PUBLIC_BASE or "https://storage.googleapis.com").rstrip("/")
    return f"{base}/{_require_bucket()}/{object_name}"

def _content_type_from_ext(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    if ext == "webp":
        return "image/webp"
    if ext == "gif":
        return "image/gif"
    if ext == "bmp":
        return "image/bmp"
    return "application/octet-stream"

def upload_bytes_to_gcs(data: bytes, filename: str, content_type: str | None = None) -> str:
    bucket_name = _require_bucket()
    bucket = _get_storage_client().bucket(bucket_name)
    object_name = _gcs_object_name(filename)
    blob = bucket.blob(object_name)
    blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
    return _gcs_public_url(object_name)

_BASE64_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r")

def _detect_image_kind(data: bytes) -> str | None:
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return None

def _looks_like_base64(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:256]
    return all(b in _BASE64_CHARS for b in sample)

def _normalize_image_bytes(data: bytes) -> bytes:
    if not data:
        return data
    if data.startswith(b"data:"):
        _, _, b64_data = data.partition(b",")
        try:
            decoded = base64.b64decode(b64_data, validate=False)
        except Exception:
            return data
        return decoded
    if _detect_image_kind(data):
        return data
    if _looks_like_base64(data):
        try:
            decoded = base64.b64decode(data, validate=False)
        except Exception:
            return data
        if _detect_image_kind(decoded):
            return decoded
    return data

def _extension_from_bytes(data: bytes, mime_type: str | None) -> str:
    if mime_type:
        mime = mime_type.lower()
        if "png" in mime:
            return "png"
        if "jpeg" in mime or "jpg" in mime:
            return "jpg"
        if "webp" in mime:
            return "webp"
        if "gif" in mime:
            return "gif"
        if "bmp" in mime:
            return "bmp"
    kind = _detect_image_kind(data)
    if kind == "jpeg":
        return "jpg"
    if kind:
        return kind
    return "png"

def _prepare_image(data: bytes, mime_type: str | None) -> tuple[bytes, str]:
    normalized = _normalize_image_bytes(data)
    ext = _extension_from_bytes(normalized, mime_type)
    return normalized, ext

import asyncio


async def generate_cover_image(payload: dict) -> dict:
    os.makedirs("uploads", exist_ok=True)

    final_prompt = dedent(f"""
    Create a high-quality blog cover image.
    Language context: English blog.
    Tone: {payload['tone']}
    Creativity: {payload['creativity']}
    Focus/Niche: {payload['focus_or_niche']}
    Keyword: {payload.get('targeted_keyword','')}
    Title: {payload.get('title','')}

    Aspect ratio: {payload['aspect_ratio']}
    Quality: {payload['quality']}
    Primary color: {payload['primary_color']}
    User prompt: {payload['prompt']}
    No watermark, no logos, no text.
    """).lstrip("\n")

   
    
    def run_sync_generation():
        try:
            client = _get_client()
            cfg = types.GenerateContentConfig(
                response_modalities=["Image"],
                image_config=types.ImageConfig(aspect_ratio=payload["aspect_ratio"]),
            )

            
            resp = client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=[final_prompt],
                config=cfg,
            )

            for part in resp.parts:
                if part.inline_data is not None:
                    raw_bytes, ext = _prepare_image(part.inline_data.data, part.inline_data.mime_type)
                    filename = f"{uuid.uuid4().hex}.{ext}"
                    content_type = _content_type_from_ext(ext)
                    image_url = upload_bytes_to_gcs(raw_bytes, filename, content_type)

                    return {
                        "image_url": image_url,
                        "meta": {
                            "aspect_ratio": payload["aspect_ratio"],
                            "quality": payload["quality"],
                            "primary_color": payload["primary_color"],
                            "model": settings.GEMINI_IMAGE_MODEL,
                            "prompt": payload["prompt"],
                        },
                    }

            raise RuntimeError("Image model did not return an image in the response parts.")
        
        except Exception as e:
            logger.warning(f"Gemini image generation failed: {e}. Falling back to OpenAI DALL-E.")
            
            # Fallback to OpenAI DALL-E
            if openai_client is None:
                raise RuntimeError(f"Gemini error: {e}. OpenAI API key not configured.")
            
            aspect_ratio_map = {
                "1:1": "1024x1024",
                "4:3": "1792x1024", "16:9": "1792x1024",
                "3:4": "1024x1792", "9:16": "1024x1792",
            }
            size = aspect_ratio_map.get(payload["aspect_ratio"], "1024x1024")
            quality_map = {"low": "standard", "medium": "standard", "high": "hd"}
            dall_e_quality = quality_map.get(payload["quality"], "hd")
            
            try:
                
                response = openai_client.images.generate(
                    model=settings.OPENAI_IMAGE_MODEL,
                    prompt=final_prompt,
                    size=size,
                    quality=dall_e_quality,
                    n=1,
                )
                
                image_url = response.data[0].url
                
                
                img_response = requests.get(image_url, stream=True, timeout=30)
                img_response.raise_for_status()
                
                img = Image.open(BytesIO(img_response.content))
                filename = f"{uuid.uuid4().hex}.png"
                buf = BytesIO()
                img.save(buf, "PNG")
                image_url = upload_bytes_to_gcs(buf.getvalue(), filename, "image/png")

                return {
                    "image_url": image_url,
                    "meta": {
                        "aspect_ratio": payload["aspect_ratio"],
                        "quality": payload["quality"],
                        "primary_color": payload["primary_color"],
                        "model": settings.OPENAI_IMAGE_MODEL,
                        "prompt": payload["prompt"],
                    },
                }
            except Exception as openai_error:
                raise RuntimeError(f"Gemini error: {e}. OpenAI error: {str(openai_error)}")

   
    return await asyncio.to_thread(run_sync_generation)