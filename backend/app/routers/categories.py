import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from pydantic import BaseModel
from typing import Optional

from core.database import get_db
from core.deps import get_current_user
from core.models import Category, CategoryRequest

logger = logging.getLogger(__name__)

router = APIRouter()


class CategoryRequestIn(BaseModel):
    name: str
    description: Optional[str] = ""


@router.get("/categories", response_model=dict)
async def list_categories(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all active categories for the user's tenant."""
    categories = (
        db.query(Category)
        .filter(Category.tenant_id == user["tenant_id"])
        .order_by(Category.name)
        .all()
    )
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description or "",
            }
            for c in categories
        ]
    }


@router.post("/category-requests", response_model=dict)
async def request_category(
    payload: CategoryRequestIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User submits a request for a new category."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required.")

    # Check if an active category with this name already exists
    existing = (
        db.query(Category)
        .filter(
            Category.tenant_id == user["tenant_id"],
            Category.name.ilike(name),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Category '{existing.name}' already exists.",
        )

    # Check if a pending request with this name already exists
    pending = (
        db.query(CategoryRequest)
        .filter(
            CategoryRequest.tenant_id == user["tenant_id"],
            CategoryRequest.name.ilike(name),
            CategoryRequest.status == "pending",
        )
        .first()
    )
    if pending:
        raise HTTPException(
            status_code=400,
            detail="A request for this category is already pending admin approval.",
        )

    logger.warning(f"[CATEGORY REQUEST] user={user.get('id')} tenant={user.get('tenant_id')} name='{name}'")

    req = CategoryRequest(
        tenant_id=user["tenant_id"],
        requested_by_id=user["id"],
        requested_by_name=user.get("name", ""),
        name=name,
        description=(payload.description or "").strip(),
        status="pending",
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    return {
        "ok": True,
        "message": "Category request submitted. Admin will review it shortly.",
        "request_id": req.id,
    }
