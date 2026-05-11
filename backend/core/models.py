from sqlalchemy import Column, Integer, String, DateTime, JSON, Text
from sqlalchemy.sql import func
from core.database import Base

class BlogPost(Base):
    """
    PostgreSQL table for storing the generated JSON Blog blocks.
    Includes tenant_id for strict workspace isolation.
    """
    __tablename__ = "blogs"

    id = Column(Integer, primary_key=True, index=True)
    
    #  Multi-Tenant Security
    tenant_id = Column(String, index=True, nullable=False)
    
    #  Author Info
    author_id = Column(String, index=True, nullable=False)
    author_name = Column(String)
    
    #  Blog Content
    title = Column(String, nullable=False)
    content_blocks = Column(JSON, nullable=False)  # Stores your AI JSON Lego Blocks!
    
    #  Media & Meta
    tags = Column(JSON, default=[])
    cover_image_url = Column(String, nullable=True)
    youtube_url = Column(String, nullable=True)
    
    #  Workflow Status
    # Users create 'pending' blogs. Admins approve them to 'published'.
    status = Column(String, default="pending", index=True) 
    
    #  Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ImageAsset(Base):
    """
    PostgreSQL table for storing generated AI Images.
    """
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    
    # 👤 Owner Info
    owner_id = Column(String, index=True, nullable=False)
    owner_name = Column(String)
    
    #  Image Details
    image_url = Column(String, nullable=False)
    source = Column(String, default="nano")
    
    # Store the exact AI prompt used to generate the image
    meta_data = Column(JSON, default={}) 
    
    #  Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# PASTE THIS AT THE BOTTOM OF YOUR MODELS FILE

class TenantAPIKey(Base):
    """
    Stores the public API keys for the headless CMS feature.
    Maps a random string key to a specific tenant.
    """
    __tablename__ = "tenant_api_keys"

    tenant_id = Column(String, primary_key=True, index=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())