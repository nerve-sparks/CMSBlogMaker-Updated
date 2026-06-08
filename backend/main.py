from contextlib import asynccontextmanager
import os
import sys
import asyncio
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import settings
from core.database import engine, SessionLocal  #  Added SessionLocal here!
from core import models
from core.models import BlogPost #  Added BlogPost here!
from app.routers import auth, ai, blogs, admin, images

# Langfuse session grouping helpers
_backend_root = os.path.dirname(os.path.abspath(__file__))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

try:
    import jwt as _jwt
    from blog_session_store import get_or_create_session_id, reset_session_id
    from langfuse_tracer import set_current_session_id as _lf_set_session, set_current_trace_identity as _lf_set_identity
    _LANGFUSE_SESSION_ENABLED = True
except Exception:
    _LANGFUSE_SESSION_ENABLED = False

# Routes that start a brand-new blog — each must get its own fresh trace
_BLOG_FINAL_ROUTES = ("/ai/blog-generate", "/ai/youtube-to-blog")


class LangfuseSessionMiddleware(BaseHTTPMiddleware):
    """
    Runs before every request. For AI routes:
    1. Reads JWT from Authorization header (no signature check — just reading sub)
    2. Maps user_id → stable session_id via blog_session_store (2-hour TTL)
    3. Writes session_id to ContextVar via set_current_session_id()
    4. Also sets user_id and tenant_id via set_current_trace_identity()

    Result: all @observe-decorated handlers called by the same user within
    the TTL window get the same Langfuse trace_id, making them appear as
    observations under ONE parent trace in the Langfuse dashboard.
    """
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_ai_route = "/ai/" in path or path.endswith("/ai")
        _user_id_for_reset = None

        if _LANGFUSE_SESSION_ENABLED and is_ai_route:
            try:
                auth_header = request.headers.get("authorization", "")
                if auth_header.lower().startswith("bearer "):
                    token = auth_header[7:].strip()
                    # Decode without verification — we only need the sub claim
                    payload = _jwt.decode(
                        token, options={"verify_signature": False}
                    )
                    user_id = payload.get("sub") or payload.get("user_id")
                    tenant_id = payload.get("tenant_id")
                    if user_id:
                        # Always reuse existing session so ALL steps of this
                        # blog (ideas → titles → outlines → blog-generate) share
                        # the SAME trace_id and appear under ONE trace.
                        sid = get_or_create_session_id(str(user_id))
                        _lf_set_session(sid)
                        _lf_set_identity(user_id=str(user_id), tenant_id=str(tenant_id) if tenant_id else None)

                        # After a final-generation route completes, reset so the
                        # NEXT blog starts with a fresh trace_id (new trace).
                        is_final_route = any(path.endswith(r) for r in _BLOG_FINAL_ROUTES)
                        if is_final_route:
                            _user_id_for_reset = str(user_id)
            except Exception:
                pass  # Never block the request

        response = await call_next(request)

        # Post-response: rotate session so next blog → new trace
        if _user_id_for_reset:
            try:
                reset_session_id(_user_id_for_reset)
            except Exception:
                pass

        return response


# Create the PostgreSQL tables based on our models
models.Base.metadata.create_all(bind=engine)

# Thread pool configuration
THREAD_POOL_WORKERS = int(os.getenv("THREAD_POOL_WORKERS", "300"))  # Default to 300 workers

# ==========================================
#  BACKGROUND SCHEDULER TASK 
# ==========================================
async def check_scheduled_blogs():
    """Runs in the background checking the clock every 60 seconds."""
    while True:
        try:
            # Open a fresh database session for the background worker
            db = SessionLocal() 
            now = datetime.now(timezone.utc)
            
            # Find all blogs that are waiting for their scheduled time
            scheduled_blogs = db.query(BlogPost).filter(BlogPost.status == "scheduled").all()
            
            for blog in scheduled_blogs:
                content = blog.content_blocks or {}
                meta = content.get("meta", {})
                sched_str = meta.get("scheduled_at")
                
                if sched_str:
                    # Convert JS ISO string to Python datetime
                    sched_time = datetime.fromisoformat(sched_str.replace("Z", "+00:00"))
                    
                    # If the clock has struck the scheduled time, publish it!
                    if now >= sched_time:
                        blog.status = "published"
                        
                        # Also update the official "reviewed_at" time so it looks freshly published
                        admin_review = content.get("admin_review", {})
                        admin_review["reviewed_at"] = now.isoformat()
                        content["admin_review"] = admin_review
                        blog.content_blocks = content
                        
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(blog, "content_blocks")
                        
                        db.commit()
                        print(f" Automatically published scheduled blog: {blog.id}")
            db.close()
        except Exception as e:
            print(f"Scheduler error: {e}")
            
        await asyncio.sleep(5) # Wait 5 secs before checking the clock again
# ==========================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    """
    # Startup
    # Thread pool for concurrent users (blocking/sync work run via run_in_executor)
    thread_pool = ThreadPoolExecutor(max_workers=THREAD_POOL_WORKERS)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(thread_pool)
    app.state.thread_pool = thread_pool
    print(f"Thread pool started: max_workers={THREAD_POOL_WORKERS}")
    
    #  START THE SCHEDULER HERE
    scheduler_task = asyncio.create_task(check_scheduled_blogs())
    print(" Background scheduler task started")
    
    yield
    
    # Shutdown
    scheduler_task.cancel() # Safely stop checking the clock
    thread_pool.shutdown(wait=False)
    print("Thread pool shut down")


# Create the main API application
api_app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
api_app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@api_app.get("/health")
async def health_check():
    return {"status": "healthy, CI/CD running", "service": "cms-backend"}

api_app.include_router(auth.router, prefix="/auth", tags=["auth"])
api_app.include_router(ai.router, prefix="/ai", tags=["ai"])
api_app.include_router(blogs.router, tags=["blogs"])
api_app.include_router(images.router, tags=["images"])
api_app.include_router(admin.router, prefix="/admin", tags=["admin"])

# Create root app AND ATTACH THE LIFESPAN HERE
app = FastAPI(lifespan=lifespan)
app.mount("/", api_app)

# Middleware must be on the ROOT app so ContextVar values set here propagate
# correctly to route handlers. Adding it to the mounted sub-app (api_app)
# breaks ContextVar propagation due to how Starlette's BaseHTTPMiddleware
# isolates context in sub-app scopes.
if _LANGFUSE_SESSION_ENABLED:
    app.add_middleware(LangfuseSessionMiddleware) 

