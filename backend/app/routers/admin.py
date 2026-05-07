from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_

from core.database import get_db
from core.models import BlogPost
from core.deps import require_admin

router = APIRouter(prefix="/admin", tags=["Admin Approvals"])

@router.get("/blogs/pending", response_model=dict)
async def list_pending_blogs(
    admin: dict = Depends(require_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    skip = (page - 1) * limit
    tenant_id = admin.get("tenant_id")
    
   
    # Grab blogs for this tenant where status is NULL, empty, or anything OTHER than published/approved/rejected
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
    blog_id: int,  
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    blog = db.query(BlogPost).filter(
        BlogPost.id == blog_id,
        BlogPost.tenant_id == admin.get("tenant_id")
    ).first()
    
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found or unauthorized")

    blog.status = "published"
    db.commit()
    
    return {"ok": True, "status": "published"}

@router.post("/blogs/{blog_id}/reject", response_model=dict)
async def reject_blog(
    blog_id: int, 
    feedback: str = Query(""),
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    blog = db.query(BlogPost).filter(
        BlogPost.id == blog_id,
        BlogPost.tenant_id == admin.get("tenant_id")
    ).first()
    
    if not blog:
        raise HTTPException(status_code=404, detail="Blog not found or unauthorized")

    blog.status = "rejected"
    db.commit()
    
    return {"ok": True, "status": "rejected"}