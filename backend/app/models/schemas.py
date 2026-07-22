from pydantic import BaseModel, EmailStr, Field
from typing import List, Literal, Optional
from datetime import datetime

# ---------------- AUTH ----------------
class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: Literal["user", "cms_admin"] = "user"   # keep role


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: Literal["user", "cms_admin"]


class AdminBlogMiniOut(BaseModel):
    id: str
    title: str = ""
    status: Literal["saved", "pending", "published", "rejected"]
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None


class AdminBlogCountsOut(BaseModel):
    saved: int = 0
    pending: int = 0
    published: int = 0
    rejected: int = 0


class AdminUserWithBlogsOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: Literal["user", "cms_admin"]
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    blog_counts: AdminBlogCountsOut = AdminBlogCountsOut()
    blogs: List[AdminBlogMiniOut] = []


class AdminDataOut(BaseModel):
    users: List[AdminUserWithBlogsOut] = []


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    admin_data: Optional[AdminDataOut] = None  # 


# ---------------- BLOG CONTENT (FINAL ONLY) ----------------

class YoutubeBlogIn(BaseModel):
    youtube_url: str
    tone: Optional[str] = "Formal"
    language: str = "English"
    image_count: Optional[int] = 0

class FinalBlog(BaseModel):
    blocks: List[dict] = []


# Metadata that came from your multi-step form (final selected/manual only)
class BlogMeta(BaseModel):
    language: str = "English"
    tone: str
    creativity: str

    focus_or_niche: str = ""
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""

    youtube_url: str = ""

    selected_idea: str = ""
    title: str = ""
    intro_md: str = ""
    outline: List[str] = []

    image_prompt: str = ""
    cover_image_url: str = ""
    category_name: str = ""


class AdminReview(BaseModel):
    requested_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    feedback: str = ""


class BlogCommentIn(BaseModel):
    """Schema for adding a comment to a blog"""
    comment: str


class BlogCreateIn(BaseModel):
    """
    Store only final blog + final metadata.
    """
    meta: BlogMeta
    final_blog: FinalBlog


class BlogOut(BaseModel):
    id: str
    owner_id: str
    owner_name: str

    status: Literal["saved", "pending", "published", "rejected"]
    meta: BlogMeta
    final_blog: FinalBlog

    admin_review: AdminReview
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class BlogListItem(BaseModel):
    id: str
    title: str
    created_by: str
    created_at: datetime
    status: Literal["saved", "pending", "published", "rejected"]


# ---------------- AI INPUTS ----------------
AI_OPTIONS_COUNT = 5  # default count
AI_OPTIONS_MAX = 10

class TopicIdeasIn(BaseModel):
    # first page dialog box input
    language: str = "English"
    focus_or_niche: str = Field(min_length=3)
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""

    tone: str
    creativity: str
    count: int = Field(default=AI_OPTIONS_COUNT, ge=1, le=AI_OPTIONS_MAX)
    session_id: Optional[str] = None  # shared across full blog workflow for grouping in Langfuse
    model: Optional[str] = None  # LLM model override (e.g. "gpt-4o", "gemini-2.5-flash")


class TitlesIn(BaseModel):
    language: str = "English"
    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""
    selected_idea: str
    session_id: Optional[str] = None
    model: Optional[str] = None


class ImagePromptsIn(BaseModel):

    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""
    selected_idea: str
    title: str
    session_id: Optional[str] = None
    model: Optional[str] = None


class IntrosIn(BaseModel):
    language: str = "English"
    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""
    selected_idea: str
    title: str
    session_id: Optional[str] = None
    model: Optional[str] = None


class OutlinesIn(BaseModel):
    language: str = "English"
    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""
    selected_idea: str
    title: str
    intro_md: str
    session_id: Optional[str] = None
    model: Optional[str] = None


class ImageGenerateIn(BaseModel):
    # plus past info (passed as context)
    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    selected_idea: str
    title: str

    prompt: str
    aspect_ratio: Literal["1:1", "4:3", "3:4", "16:9", "9:16"] = "4:3"
    quality: Literal["low", "medium", "high"] = "high"
    primary_color: str = "#4443E4"
    source: Literal["blog", "nano"] = "nano"
    save_to_gallery: bool = True
    session_id: Optional[str] = None
    image_model: Literal["gemini", "openai"] = "gemini"  # which image provider to use


class ImageSaveIn(BaseModel):
    image_url: str
    meta: dict = {}
    source: Optional[str] = None


class ImageOut(BaseModel):
    image_url: str
    meta: dict


class GenerateBlogIn(BaseModel):
    """
    Called on 'Generate Blog' button from review page.
    """
    language: str = "English"
    tone: str
    creativity: str
    focus_or_niche: str
    targeted_keyword: str = ""
    targeted_audience: str = ""
    reference_links: str = ""

    youtube_url: str = ""
    youtube_transcript: str = ""

    selected_idea: str
    title: str
    intro_md: str
    outline: List[str]

    cover_image_url: str = ""
    primary_color: str = "#4443E4"
    session_id: Optional[str] = None
    model: Optional[str] = None  # LLM model override


class OptionsOut(BaseModel):
    options: List[str]
