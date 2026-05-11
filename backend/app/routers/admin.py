from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from pydantic import BaseModel

from core.database import get_db
from core.models import BlogPost, TenantAPIKey
import secrets
from core.deps import require_admin
from app.models.schemas import BlogCommentIn

router = APIRouter( tags=["Admin Approvals"])

class ScheduleIn(BaseModel):
    scheduled_at: str  # ISO string from the frontend

@router.post("/generate-api-key", response_model=dict)
async def generate_api_key(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    tenant_id = admin.get("tenant_id")
    new_key = f"sk_live_{secrets.token_hex(24)}"
    
    db_key = db.query(TenantAPIKey).filter(TenantAPIKey.tenant_id == tenant_id).first()
    
    if db_key:
        db_key.api_key = new_key
    else:
        db_key = TenantAPIKey(tenant_id=tenant_id, api_key=new_key)
        db.add(db_key)
        
    db.commit()
    return {"api_key": new_key, "message": "Key generated successfully"}

@router.get("/api-key", response_model=dict)
async def get_api_key(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    db_key = db.query(TenantAPIKey).filter(TenantAPIKey.tenant_id == admin.get("tenant_id")).first()
    return {"api_key": db_key.api_key if db_key else None}

@router.get("/blogs/pending", response_model=dict)
async def list_pending_blogs(
    admin: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    skip = (page - 1) * limit
    tenant_id = admin.get("tenant_id")
    
    query = db.query(BlogPost).filter(
        BlogPost.tenant_id == tenant_id,
        or_(
            BlogPost.status == None,
            ~BlogPost.status.in_(["approved", "published", "rejected", "Approved", "Published"])
        )
    )
    
    total = query.count()
    blogs = query.order_by(desc(BlogPost.created_at)).offset(skip).limit(limit).all()
    
    items = []
    for b in blogs:
        blocks = b.content_blocks or {}
        meta = blocks.get("meta", {}) if isinstance(blocks.get("meta"), dict) else {}
        
        items.append({
            "id": str(b.id), 
            "title": b.title or meta.get("title", ""),
            "language": meta.get("language", "English"),
            "tone": meta.get("tone", ""),
            "created_by": b.author_name or "Admin",
            "created_at": b.created_at,
            "status": b.status or "pending",
        })
        
    return {"items": items, "page": page, "limit": limit, "total": total}

@router.get("/blogs/published", response_model=dict)
async def list_published_blogs(
    admin: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    skip = (page - 1) * limit
    tenant_id = admin.get("tenant_id")
    
    query = db.query(BlogPost).filter(
        BlogPost.tenant_id == tenant_id,
        BlogPost.status.in_(["approved", "published"])
    )
    
    total = query.count()
    blogs = query.order_by(desc(BlogPost.created_at)).offset(skip).limit(limit).all()
    
    items = []
    for b in blogs:
        blocks = b.content_blocks or {}
        meta = blocks.get("meta", {}) if isinstance(blocks.get("meta"), dict) else {}
        
        items.append({
            "id": str(b.id), 
            "title": b.title or meta.get("title", ""),
            "language": meta.get("language", "English"),
            "tone": meta.get("tone", ""),
            "created_by": b.author_name or "Admin",
            "created_at": b.created_at,
            "status": b.status,
        })
        
    return {"items": items, "page": page, "limit": limit, "total": total}

@router.post("/blogs/{blog_id}/approve", response_model=dict)
async def approve_blog(
    blog_id: str,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    b_id = int(blog_id)
    blog = db.query(BlogPost).filter(
        BlogPost.id == b_id,
        BlogPost.tenant_id == admin.get("tenant_id")
    ).first()
    
    if not blog: raise HTTPException(status_code=404)
    blog.status = "published"
    db.commit()
    return {"ok": True, "status": "published"}

@router.post("/blogs/{blog_id}/reject", response_model=dict)
async def reject_blog(
    blog_id: str,
    feedback: str = Query(""),
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    b_id = int(blog_id)
    blog = db.query(BlogPost).filter(
        BlogPost.id == b_id,
        BlogPost.tenant_id == admin.get("tenant_id")
    ).first()
    
    if not blog: raise HTTPException(status_code=404)

    content = blog.content_blocks or {}
    admin_review = content.get("admin_review", {})
    admin_review["feedback"] = feedback or "Blog rejected. Please review and resubmit."
    content["admin_review"] = admin_review
    
    blog.content_blocks = content
    blog.status = "rejected"
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(blog, "content_blocks")
    
    db.commit()
    return {"ok": True, "status": "rejected"}

@router.post("/blogs/{blog_id}/comment", response_model=dict)
async def add_blog_comment(blog_id: str, payload: BlogCommentIn, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    b_id = int(blog_id)
    b = db.query(BlogPost).filter(BlogPost.id == b_id).first()
    
    if not b:
        raise HTTPException(status_code=404, detail="Blog not found")
    if b.tenant_id != admin.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

    content = b.content_blocks or {}
    admin_review = content.get("admin_review", {})
    
    existing_feedback = admin_review.get("feedback", "")
    new_feedback = payload.comment.strip()
    
    if existing_feedback and new_feedback:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        updated_feedback = f"{existing_feedback}\n\n--- {admin.get('name', 'Admin')} ({timestamp}) ---\n{new_feedback}"
    else:
        updated_feedback = new_feedback

    admin_review["feedback"] = updated_feedback
    
    if not admin_review.get("reviewed_by"):
        admin_review["reviewed_by"] = admin["id"]
        admin_review["reviewed_by_name"] = admin["name"]

    content["admin_review"] = admin_review
    b.content_blocks = content
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(b, "content_blocks")
    
    db.commit()
    return {"ok": True, "comment": new_feedback}

@router.post("/blogs/{blog_id}/delete", response_model=dict)
async def soft_delete_blog(blog_id: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    b_id = int(blog_id)
    blog = db.query(BlogPost).filter(BlogPost.id == b_id, BlogPost.tenant_id == admin.get("tenant_id")).first()
    if not blog: raise HTTPException(status_code=404)
    blog.status = "deleted"
    db.commit()
    return {"ok": True, "status": "deleted"}

@router.post("/blogs/{blog_id}/restore", response_model=dict)
async def restore_blog(blog_id: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    b_id = int(blog_id)
    blog = db.query(BlogPost).filter(BlogPost.id == b_id, BlogPost.tenant_id == admin.get("tenant_id")).first()
    if not blog: raise HTTPException(status_code=404)
    blog.status = "saved"
    db.commit()
    return {"ok": True, "status": "saved"}

@router.post("/blogs/{blog_id}/schedule", response_model=dict)
async def schedule_blog(
    blog_id: str, 
    payload: ScheduleIn,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    b_id = int(blog_id)
    blog = db.query(BlogPost).filter(
        BlogPost.id == b_id,
        BlogPost.tenant_id == admin.get("tenant_id")
    ).first()
    
    if not blog: raise HTTPException(status_code=404)

    blog.status = "scheduled"
    
    content = blog.content_blocks or {}
    meta = content.get("meta", {})
    meta["scheduled_at"] = payload.scheduled_at
    content["meta"] = meta
    blog.content_blocks = content
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(blog, "content_blocks")
    
    db.commit()
    return {"ok": True, "status": "scheduled", "scheduled_at": payload.scheduled_at}