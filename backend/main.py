from contextlib import asynccontextmanager
import os
import asyncio
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.database import engine, SessionLocal  #  Added SessionLocal here!
from core import models
from core.models import BlogPost #  Added BlogPost here!
from app.routers import auth, ai, blogs, admin, images

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
# app.mount("/cms-backend", api_app) 
app.mount("/", api_app) 

