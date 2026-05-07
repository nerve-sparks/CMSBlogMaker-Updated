# import base64
# import logging
# import os
# import uuid
# import asyncio
# from io import BytesIO
# from textwrap import dedent

# import requests
# from PIL import Image
# from google import genai
# from google.genai import types
# from openai import OpenAI
# from google.cloud import storage

# from core.config import settings

# logger = logging.getLogger(__name__)

# # Get absolute path to uploads directory
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
# os.makedirs(UPLOADS_DIR, exist_ok=True)

# _client = None
# _storage_client = None

# def _normalize_model(name: str) -> str:
#     if not name:
#         return name
#     return name if name.startswith("models/") else f"models/{name}"

# def _get_client() -> genai.Client:
#     global _client
#     if not settings.GEMINI_API_KEY:
#         raise RuntimeError("GEMINI_API_KEY is not set.")
#     if _client is None:
#         try:
#             _client = genai.Client(api_key=settings.GEMINI_API_KEY)
#         except Exception as e:
#             logger.warning(f"Failed to initialize Gemini client: {e}")
#             raise
#     return _client

# openai_client = OpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

# def _get_storage_client() -> storage.Client:
#     global _storage_client
#     if _storage_client is None:
#         from google.oauth2 import service_account
        
#         creds_path = None
#         if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
#             creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
#         elif hasattr(settings, "GOOGLE_APPLICATION_CREDENTIALS") and settings.GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(settings.GOOGLE_APPLICATION_CREDENTIALS):
#             creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        
#         if creds_path and os.path.exists(creds_path):
#             credentials = service_account.Credentials.from_service_account_file(creds_path)
#             _storage_client = storage.Client(credentials=credentials)
#             logger.info(f"GCS Storage client initialized with credentials from: {creds_path}")
#         else:
#             _storage_client = storage.Client()
#             logger.info("GCS Storage client initialized with default credentials")
#     return _storage_client

# def _require_bucket() -> str:
#     bucket = settings.GCS_BUCKET
#     if not bucket:
#         raise RuntimeError("GCS_BUCKET is not set.")
#     return bucket

# def _gcs_object_name(filename: str) -> str:
#     prefix = (settings.GCS_FOLDER or "").strip("/")
#     return f"{prefix}/{filename}" if prefix else filename

# def _gcs_public_url(object_name: str) -> str:
#     base = (settings.GCS_PUBLIC_BASE or "https://storage.googleapis.com").rstrip("/")
#     return f"{base}/{_require_bucket()}/{object_name}"

# def upload_bytes_to_gcs(data: bytes, filename: str, content_type: str | None = None) -> str:
#     bucket_name = _require_bucket()
#     bucket = _get_storage_client().bucket(bucket_name)
#     object_name = _gcs_object_name(filename)
#     blob = bucket.blob(object_name)
#     blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
#     return _gcs_public_url(object_name)

# _BASE64_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r")

# def _detect_image_kind(data: bytes) -> str | None:
#     if not data:
#         return None
#     if data.startswith(b"\x89PNG\r\n\x1a\n"):
#         return "png"
#     if data[:3] == b"\xff\xd8\xff":
#         return "jpeg"
#     if data[:6] in (b"GIF87a", b"GIF89a"):
#         return "gif"
#     if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
#         return "webp"
#     if data[:2] == b"BM":
#         return "bmp"
#     return None

# def _looks_like_base64(data: bytes) -> bool:
#     if not data:
#         return False
#     sample = data[:256]
#     return all(b in _BASE64_CHARS for b in sample)

# def _normalize_image_bytes(data: bytes) -> bytes:
#     if not data:
#         return data
#     if data.startswith(b"data:"):
#         _, _, b64_data = data.partition(b",")
#         try:
#             decoded = base64.b64decode(b64_data, validate=False)
#         except Exception:
#             return data
#         return decoded
#     if _detect_image_kind(data):
#         return data
#     if _looks_like_base64(data):
#         try:
#             decoded = base64.b64decode(data, validate=False)
#         except Exception:
#             return data
#         if _detect_image_kind(decoded):
#             return decoded
#     return data

# def _extension_from_bytes(data: bytes, mime_type: str | None) -> str:
#     if mime_type:
#         mime = mime_type.lower()
#         if "png" in mime:
#             return "png"
#         if "jpeg" in mime or "jpg" in mime:
#             return "jpg"
#         if "webp" in mime:
#             return "webp"
#         if "gif" in mime:
#             return "gif"
#         if "bmp" in mime:
#             return "bmp"
#     kind = _detect_image_kind(data)
#     if kind == "jpeg":
#         return "jpg"
#     if kind:
#         return kind
#     return "png"

# def _prepare_image(data: bytes, mime_type: str | None) -> tuple[bytes, str]:
#     normalized = _normalize_image_bytes(data)
#     ext = _extension_from_bytes(normalized, mime_type)
#     return normalized, ext


# async def generate_cover_image(payload: dict) -> dict:
#     os.makedirs("uploads", exist_ok=True)

#     final_prompt = dedent(f"""
#     Create a high-quality blog cover image.
#     Language context: English blog.
#     Tone: {payload.get('tone', 'Formal')}
#     Creativity: {payload.get('creativity', 'Regular')}
#     Focus/Niche: {payload.get('focus_or_niche', '')}
#     Title: {payload.get('title','')}
#     Primary color: {payload.get('primary_color', '')}
#     User prompt: {payload.get('prompt', '')}
#     No watermark, no logos, no text.
#     """).lstrip("\n")

#     def run_sync_generation():
#         try:
#             client = _get_client()
#             aspect_ratio_str = payload.get("aspect_ratio", "1:1")
#             model_name = settings.GEMINI_IMAGE_MODEL
            
#             logger.info(f"Attempting to generate image with Paid Gemini Model: {model_name}")

#             # 🚨 SMART ROUTING
#             if "imagen" in model_name.lower():
#                 result = client.models.generate_images(
#                     model=model_name, 
#                     prompt=final_prompt,
#                     config=types.GenerateImagesConfig(
#                         number_of_images=1,
#                         aspect_ratio=aspect_ratio_str,
#                         output_mime_type="image/png",
#                         person_generation="ALLOW_ADULT" # Bypasses the people-blocking safety filter
#                     )
#                 )
#                 if not result or not result.generated_images:
#                     raise RuntimeError("Gemini returned an empty image list.")
#                 image_bytes = result.generated_images[0].image.image_bytes
                
#             else:
#                 cfg = types.GenerateContentConfig(
#                     response_modalities=["IMAGE"],
#                     safety_settings=[
#                         types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
#                         types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
#                         types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
#                         types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
#                     ]
#                 )
#                 prompt_with_ratio = final_prompt + f"\nASPECT RATIO: {aspect_ratio_str}"
                
#                 result = client.models.generate_content(
#                     model=model_name, 
#                     contents=[prompt_with_ratio],
#                     config=cfg,
#                 )
#                 if not result or not result.parts or not result.parts[0].inline_data:
#                     raise RuntimeError("Gemini multimodal returned an empty or blocked response.")
#                 image_bytes = result.parts[0].inline_data.data

#             # Save the image bytes to a file
#             img = Image.open(BytesIO(image_bytes))
#             filename = f"{uuid.uuid4().hex}.png"
#             path = os.path.join(UPLOADS_DIR, filename)
#             img.save(path)

#             return {
#                 "image_url": f"{settings.PUBLIC_BASE_URL}/uploads/{filename}",
#                 "meta": {
#                     "aspect_ratio": aspect_ratio_str,
#                     "quality": payload.get("quality", "standard"),
#                     "primary_color": payload.get("primary_color", ""),
#                     "model": model_name,
#                     "prompt": payload.get("prompt", ""),
#                 },
#             }
        
#         except Exception as e:
#             logger.warning(f"Gemini image generation failed: {e}. Falling back to OpenAI DALL-E.")
            
#             if openai_client is None:
#                 raise RuntimeError(f"Gemini error: {e}. OpenAI API key not configured.")
            
#             aspect_ratio_map = {
#                 "1:1": "1024x1024", "4:3": "1792x1024", "16:9": "1792x1024", 
#                 "3:4": "1024x1792", "9:16": "1024x1792",
#             }
#             size = aspect_ratio_map.get(payload.get("aspect_ratio", "1:1"), "1024x1024")
#             quality_map = {"low": "standard", "medium": "standard", "high": "hd"}
#             dall_e_quality = quality_map.get(payload.get("quality", "standard"), "hd")
            
#             safe_dalle_prompt = f"A high-quality, abstract blog cover illustration representing: {payload.get('prompt', 'business and governance')}. Primary color theme: {payload.get('primary_color', '#000000')}. Professional style, no text, no watermarks, no specific people."

#             try:
#                 response = openai_client.images.generate(
#                     model=settings.OPENAI_IMAGE_MODEL,
#                     prompt=safe_dalle_prompt,
#                     size=size,
#                     quality=dall_e_quality,
#                     n=1,
#                 )
#                 image_url = response.data[0].url
#                 img_response = requests.get(image_url, stream=True, timeout=30)
#                 img_response.raise_for_status()
                
#                 img = Image.open(BytesIO(img_response.content))
#                 filename = f"{uuid.uuid4().hex}.png"
#                 path = os.path.join(UPLOADS_DIR, filename)
#                 img.save(path)
                
#                 return {
#                     "image_url": f"{settings.PUBLIC_BASE_URL}/uploads/{filename}",
#                     "meta": {
#                         "aspect_ratio": payload.get("aspect_ratio", "1:1"),
#                         "quality": payload.get("quality", "standard"),
#                         "primary_color": payload.get("primary_color", ""),
#                         "model": settings.OPENAI_IMAGE_MODEL,
#                         "prompt": payload.get("prompt", ""),
#                     },
#                 }
#             except Exception as openai_error:
#                 raise RuntimeError(f"Gemini error: {e}. OpenAI error: {str(openai_error)}")

#     return await asyncio.to_thread(run_sync_generation)


import base64
import logging
import os
import uuid
import asyncio
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
    global _storage_client
    if _storage_client is None:
        from google.oauth2 import service_account
        
        creds_path = None
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        elif hasattr(settings, "GOOGLE_APPLICATION_CREDENTIALS") and settings.GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(settings.GOOGLE_APPLICATION_CREDENTIALS):
            creds_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        
        if creds_path and os.path.exists(creds_path):
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            _storage_client = storage.Client(credentials=credentials)
            logger.info(f"GCS Storage client initialized with credentials from: {creds_path}")
        else:
            _storage_client = storage.Client()
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


async def generate_cover_image(payload: dict) -> dict:
    os.makedirs("uploads", exist_ok=True)

    # Use the exact prompt the user provided, no matter where it came from
    user_prompt = payload.get('prompt', '').strip()
    
    # If there's no prompt, use a generic fallback
    if not user_prompt:
         user_prompt = "A professional high-quality blog cover image."

    # Construct the final prompt, using the user's prompt as the core
    final_prompt = dedent(f"""
    Create a high-quality, professional blog cover image.
    Visual Description: {user_prompt}
    Primary color theme: {payload.get('primary_color', '#000000')}
    Style: Editorial illustration, cinematic lighting.
    Constraints: NO text, NO watermarks, NO logos.
    """).lstrip("\n")

    def run_sync_generation():
        try:
            client = _get_client()
            aspect_ratio_str = payload.get("aspect_ratio", "1:1")
            model_name = settings.GEMINI_IMAGE_MODEL
            
            logger.info(f"Attempting to generate image with Paid Gemini Model: {model_name}")

            # 🚨 SMART ROUTING
            if "imagen" in model_name.lower():
                result = client.models.generate_images(
                    model=model_name, 
                    prompt=final_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=aspect_ratio_str,
                        output_mime_type="image/png",
                        person_generation="ALLOW_ADULT" 
                    )
                )
                if not result or not result.generated_images:
                    raise RuntimeError("Gemini returned an empty image list.")
                image_bytes = result.generated_images[0].image.image_bytes
                
            else:
                cfg = types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    safety_settings=[
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    ]
                )
                prompt_with_ratio = final_prompt + f"\nASPECT RATIO: {aspect_ratio_str}"
                
                result = client.models.generate_content(
                    model=model_name, 
                    contents=[prompt_with_ratio],
                    config=cfg,
                )
                if not result or not result.parts or not result.parts[0].inline_data:
                    raise RuntimeError("Gemini multimodal returned an empty or blocked response.")
                image_bytes = result.parts[0].inline_data.data

            # Save the image bytes to a file
            img = Image.open(BytesIO(image_bytes))
            filename = f"{uuid.uuid4().hex}.png"
            path = os.path.join(UPLOADS_DIR, filename)
            img.save(path)

            return {
                "image_url": f"{settings.PUBLIC_BASE_URL}/uploads/{filename}",
                "meta": {
                    "aspect_ratio": aspect_ratio_str,
                    "quality": payload.get("quality", "standard"),
                    "primary_color": payload.get("primary_color", ""),
                    "model": model_name,
                    "prompt": user_prompt, # Return the original user prompt
                },
            }
        
        except Exception as e:
            logger.warning(f"Gemini image generation failed: {e}. Falling back to OpenAI DALL-E.")
            
            if openai_client is None:
                raise RuntimeError(f"Gemini error: {e}. OpenAI API key not configured.")
            
            aspect_ratio_map = {
                "1:1": "1024x1024", "4:3": "1792x1024", "16:9": "1792x1024", 
                "3:4": "1024x1792", "9:16": "1024x1792",
            }
            size = aspect_ratio_map.get(payload.get("aspect_ratio", "1:1"), "1024x1024")
            quality_map = {"low": "standard", "medium": "standard", "high": "hd"}
            dall_e_quality = quality_map.get(payload.get("quality", "standard"), "hd")
            
            # Pass the user's prompt directly to DALL-E
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
                path = os.path.join(UPLOADS_DIR, filename)
                img.save(path)
                
                return {
                    "image_url": f"{settings.PUBLIC_BASE_URL}/uploads/{filename}",
                    "meta": {
                        "aspect_ratio": payload.get("aspect_ratio", "1:1"),
                        "quality": payload.get("quality", "standard"),
                        "primary_color": payload.get("primary_color", ""),
                        "model": settings.OPENAI_IMAGE_MODEL,
                        "prompt": user_prompt, # Return the original user prompt
                    },
                }
            except Exception as openai_error:
                raise RuntimeError(f"Gemini error: {e}. OpenAI error: {str(openai_error)}")

    return await asyncio.to_thread(run_sync_generation)