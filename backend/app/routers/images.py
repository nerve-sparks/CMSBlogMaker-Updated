from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc

from core.database import get_db
from core.models import ImageAsset
from app.models.schemas import ImageSaveIn
from core.deps import get_current_user

router = APIRouter()

def _parse_id(item_id: str) -> int:
    try:
        return int(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format.")


@router.post("/images/save", response_model=dict)
async def save_image(payload: ImageSaveIn, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(ImageAsset).filter(
        ImageAsset.owner_id == user["id"], 
        ImageAsset.image_url == payload.image_url
    ).first()
    
    if not existing:
        db_image = ImageAsset(
            owner_id=user["id"],
            owner_name=user.get("name", ""),
            image_url=payload.image_url,
            source=payload.source,
            meta_data=payload.meta or {}
        )
        db.add(db_image)
        db.commit()

    return {"image_url": payload.image_url, "meta": payload.meta or {}}


@router.delete("/images/{image_id}", response_model=dict)
async def delete_image(image_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    img_id = _parse_id(image_id)
    img = db.query(ImageAsset).filter(ImageAsset.id == img_id).first()
    
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
        
    if img.owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="Not allowed to delete this image")
    
    db.delete(img)
    db.commit()
    return {"ok": True, "image_id": image_id}


@router.get("/images", response_model=dict)
async def list_images(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    source: str | None = Query(None),
):
    try:
        skip = (page - 1) * limit
        query = db.query(ImageAsset).filter(ImageAsset.owner_id == user["id"])
        
        if source:
            if source == "ai":
                query = query.filter(
                    or_(
                        ImageAsset.source.in_(["nano", "blog"]),
                        ImageAsset.source.is_(None)
                    )
                )
            elif source == "nano":
                query = query.filter(
                    or_(
                        ImageAsset.source == "nano",
                        ImageAsset.source.is_(None)
                    )
                )
            else:
                query = query.filter(ImageAsset.source == source)
                
        total = query.count()
        images = query.order_by(desc(ImageAsset.created_at)).offset(skip).limit(limit).all()
        
        items = []
        for img in images:
            items.append({
                "id": str(img.id),
                "image_url": img.image_url,
                "meta": img.meta_data,
                "source": img.source,
                "created_at": img.created_at,
            })

        return {"items": items, "page": page, "limit": limit, "total": total}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))