import os
import uuid
from datetime import datetime, timezone
import re

from fastapi import Header
from core.models import TenantAPIKey

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func

from core.deps import get_current_user, require_admin
from core.database import get_db
from core.models import BlogPost, ImageAsset
from app.models.schemas import BlogCreateIn, BlogOut, BlogCommentIn
from app.services.image_service import upload_bytes_to_gcs

router = APIRouter()

def _parse_id(item_id: str) -> int:
    try:
        return int(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format.")

def _map_blog_for_list(b: BlogPost) -> dict:
    content = b.content_blocks or {}
    meta = content.get("meta", {})
    admin_review = content.get("admin_review", {})
    
    return {
        "id": str(b.id),
        "title": b.title,
        "language": meta.get("language", "English"),
        "tone": meta.get("tone", ""),
        "creativity": meta.get("creativity", ""),
        "created_by": b.author_name,
        "owner_id": b.author_id,
        "created_at": b.created_at,
        "requested_at": admin_review.get("requested_at"),
        "published_at": admin_review.get("reviewed_at") if b.status == "published" else None,
        "reviewed_at": admin_review.get("reviewed_at"),
        "reviewed_by": admin_review.get("reviewed_by_name"),
        "status": b.status,
        "category_name": b.category_name or "",
    }

def _map_blog_detail(b: BlogPost) -> dict:
    content = b.content_blocks or {}
    return {
        "id": str(b.id),
        "owner_id": b.author_id,
        "owner_name": b.author_name,
        "status": b.status,
        "meta": content.get("meta", {}),
        "final_blog": content.get("final_blog", {}),
        "admin_review": content.get("admin_review", {}),
        "created_at": b.created_at,
        "updated_at": b.updated_at,
        "published_at": content.get("admin_review", {}).get("reviewed_at") if b.status == "published" else None
    }

@router.post("/blog", response_model=dict)
async def save_blog(payload: BlogCreateIn, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    meta_dict = payload.meta.model_dump()
    final_blog_dict = payload.final_blog.model_dump()

    title = meta_dict.get("title") or final_blog_dict.get("render", {}).get("title", "Untitled")
    category_name = meta_dict.get("category_name") or ""

    content_blocks = {
        "meta": meta_dict,
        "final_blog": final_blog_dict,
        "admin_review": {
            "requested_at": None,
            "reviewed_at": None,
            "reviewed_by": None,
            "reviewed_by_name": None,
            "feedback": "",
        }
    }

    db_blog = BlogPost(
        tenant_id=user.get("tenant_id", "default"),
        author_id=user.get("id"),
        author_name=user.get("name", ""),
        title=title,
        category_name=category_name or None,
        content_blocks=content_blocks,
        status="saved"
    )
    
    db.add(db_blog)
    db.commit()
    db.refresh(db_blog)
    
    return {"blog_id": str(db_blog.id), "status": "saved"}

@router.get("/blog", response_model=dict)
async def list_my_blogs(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=5, le=50),
    search: str = Query("", description="Search query filter"),
    category: str = Query("", description="Filter by category name"),
):
    skip = (page - 1) * limit
    query = db.query(BlogPost).filter(BlogPost.author_id == user["id"])

    if search.strip():
        query = query.filter(func.lower(BlogPost.title).ilike(f"%{search.strip().lower()}%"))

    if category.strip():
        query = query.filter(func.lower(BlogPost.category_name).ilike(f"%{category.strip().lower()}%"))

    total = query.count()
    blogs = query.order_by(desc(BlogPost.created_at)).offset(skip).limit(limit).all()

    items = [_map_blog_for_list(b) for b in blogs]
    return {"items": items, "page": page, "limit": limit, "total": total}

@router.get("/blogs/stats", response_model=dict)
async def blog_stats(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    base_query = db.query(BlogPost).filter(BlogPost.author_id == user["id"])
    
    total = base_query.count()
    saved = base_query.filter(BlogPost.status == "saved").count()
    pending = base_query.filter(BlogPost.status == "pending").count()
    published = base_query.filter(BlogPost.status == "published").count()
    
    images_count = db.query(ImageAsset).filter(
        ImageAsset.owner_id == user["id"],
        or_(
            ImageAsset.source.in_(["nano", "blog"]),
            ImageAsset.source.is_(None)
        )
    ).count()

    return {
        "total_blogs": total,
        "saved_blogs": saved,
        "pending_blogs": pending,
        "published_blogs": published,
        "generated_images": images_count,
    }

MAX_MB = 5
MAX_BYTES = MAX_MB * 1024 * 1024

@router.post("/blogs/uploads/images", response_model=dict)
async def upload_image(
    file: UploadFile = File(...), 
    content_length: int = Header(None), # Grabs the file size from the request header
    user: dict = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    # 1. Quick Reject: Stop it before it even downloads if the header says it's too big
    if content_length and content_length > MAX_BYTES:
        raise HTTPException(
            status_code=413, 
            detail=f"Image is too large. Maximum allowed size is {MAX_MB}MB."
        )

    # 2. Deep Verification: Read the file securely
    data = await file.read()
    
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413, 
            detail=f"Image is too large. Maximum allowed size is {MAX_MB}MB."
        )

    # 3. Process and Upload to GCS
    ext = os.path.splitext(file.filename or "")[-1].lower()
    if not ext:
        ext = ".png"

    filename = f"{uuid.uuid4().hex}{ext}"
    image_url = upload_bytes_to_gcs(data, filename, file.content_type or None)
    
    db_image = ImageAsset(
        owner_id=user["id"],
        owner_name=user.get("name", ""),
        image_url=image_url,
        source="upload",
        meta_data={
            "filename": file.filename or filename,
            "content_type": file.content_type or "",
            "size": len(data),
        }
    )
    db.add(db_image)
    db.commit()
    
    return {"image_url": image_url}

@router.get("/blogs/{blog_id}")
async def get_blog(blog_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    
    if b.author_id != user["id"]:
        if user.get("role") != "admin" or b.tenant_id != user.get("tenant_id"):
            raise HTTPException(status_code=403, detail="Not allowed")

    return _map_blog_detail(b)

@router.delete("/blogs/{blog_id}", response_model=dict)
async def delete_blog_route(blog_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    
    if b.author_id != user["id"]:
        if user.get("role") != "admin" or b.tenant_id != user.get("tenant_id"):
            raise HTTPException(status_code=403, detail="Not allowed")

    db.delete(b)
    db.commit()
    return {"ok": True}

@router.put("/blogs/{blog_id}", response_model=dict)
async def update_blog_route(blog_id: str, payload: BlogCreateIn, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    
    if b.author_id != user["id"]:
        if user.get("role") != "admin" or b.tenant_id != user.get("tenant_id"):
            raise HTTPException(status_code=403, detail="Not allowed")

    content = b.content_blocks or {}
    content["meta"] = payload.meta.model_dump()
    content["final_blog"] = payload.final_blog.model_dump()

    b.content_blocks = content
    b.title = content["meta"].get("title") or content["final_blog"].get("render", {}).get("title", "Untitled")
    b.category_name = content["meta"].get("category_name") or b.category_name

    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(b, "content_blocks")
    
    db.commit()
    return {"ok": True, "blog_id": blog_id}

@router.post("/blogs/{blog_id}/publish-request", response_model=dict)
async def request_publish(blog_id: str, payload: BlogCreateIn, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    if b.author_id != user["id"]:
        raise HTTPException(status_code=403, detail="Not allowed")
    if b.status == "published":
        raise HTTPException(status_code=400, detail="Already published")

    content = b.content_blocks or {}
    content["meta"] = payload.meta.model_dump()
    content["final_blog"] = payload.final_blog.model_dump()
    
    admin_review = content.get("admin_review", {})
    admin_review["requested_at"] = datetime.now(timezone.utc).isoformat()
    admin_review["feedback"] = ""
    content["admin_review"] = admin_review

    b.content_blocks = content
    b.status = "pending"
    b.title = content["meta"].get("title") or content["final_blog"].get("render", {}).get("title", "Untitled")
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(b, "content_blocks")
    
    db.commit()
    return {"ok": True, "status": "pending", "blog_id": blog_id}

@router.post("/blogs/{blog_id}/draft", response_model=dict)
async def change_to_draft(blog_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    
    if b.author_id != user["id"]:
        if user.get("role") != "admin" or b.tenant_id != user.get("tenant_id"):
            raise HTTPException(status_code=403, detail="Not allowed")
            
    if b.status != "published":
        raise HTTPException(status_code=400, detail="Blog is not published")

    b.status = "saved"
    db.commit()
    return {"ok": True, "status": "saved"}

@router.get("/public/blogs", response_model=dict)
async def list_public_blogs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    authorization: str = Header(None),
    category: str = Query("", description="Filter by category name"),
    db: Session = Depends(get_db),
):
    if not authorization or not authorization.startswith("Bearer sk_live_"):
        raise HTTPException(status_code=401, detail="Missing or invalid API Key. Format: 'Bearer sk_live_...'")
        
    api_key = authorization.replace("Bearer ", "").strip()
    
    db_key = db.query(TenantAPIKey).filter(TenantAPIKey.api_key == api_key).first()
    if not db_key:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
        
    tenant_id = db_key.tenant_id

    skip = (page - 1) * limit
    query = db.query(BlogPost).filter(
        BlogPost.tenant_id == tenant_id,
        BlogPost.status == "published"
    )

    if category.strip():
        query = query.filter(func.lower(BlogPost.category_name).ilike(f"%{category.strip().lower()}%"))

    total = query.count()
    blogs = query.order_by(desc(BlogPost.updated_at)).offset(skip).limit(limit).all()
    
    items = []
    for b in blogs:
        content = b.content_blocks or {}
        meta = content.get("meta", {})
        render = content.get("final_blog", {}).get("render", {})
        
        cover_url = b.cover_image_url or meta.get("cover_image_url") or render.get("cover_image_url") or ""
        
        if not cover_url:
            match = re.search(r"!\[.*?\]\((.*?)\)", str(content))
            if match:
                cover_url = match.group(1).split(" ")[0].strip()

        raw_intro = render.get("intro_md", "") or meta.get("intro_md", "")
        clean_intro = re.sub(r"!\[.*?\]\(.*?\)", "", raw_intro).strip()
        
        items.append({
            "id": str(b.id),
            "title": render.get("title", "") or meta.get("title", ""),
            "cover_image_url": cover_url,
            "intro": clean_intro,
            "author": b.author_name,
            "category": b.category_name or meta.get("focus_or_niche", ""),
            "published_at": content.get("admin_review", {}).get("reviewed_at"),
        })

    return {"items": items, "page": page, "limit": limit, "total": total}


@router.get("/public/blogs/{blog_id}", response_model=dict)
async def get_public_blog(
    blog_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    if not authorization or not authorization.startswith("Bearer sk_live_"):
        raise HTTPException(status_code=401, detail="Missing or invalid API Key. Format: 'Bearer sk_live_...'")

    api_key = authorization.replace("Bearer ", "").strip()
    db_key = db.query(TenantAPIKey).filter(TenantAPIKey.api_key == api_key).first()
    if not db_key:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")

    b_id = _parse_id(blog_id)
    b = db.query(BlogPost).filter(
        BlogPost.id == b_id,
        BlogPost.tenant_id == db_key.tenant_id,
        BlogPost.status == "published"
    ).first()

    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")

    content = b.content_blocks or {}
    meta = content.get("meta", {})
    render = content.get("final_blog", {}).get("render", {})

    cover_url = b.cover_image_url or meta.get("cover_image_url") or render.get("cover_image_url") or ""
    if not cover_url:
        match = re.search(r"!\[.*?\]\((.*?)\)", str(content))
        if match:
            cover_url = match.group(1).split(" ")[0].strip()

    return {
        "id": str(b.id),
        "title": render.get("title", "") or meta.get("title", ""),
        "cover_image_url": cover_url,
        "author": b.author_name,
        "category": b.category_name or meta.get("focus_or_niche", ""),
        "published_at": content.get("admin_review", {}).get("reviewed_at"),
        "content": content.get("final_blog", {}),
    }