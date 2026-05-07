from contextlib import asynccontextmanager
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.database import engine
from core import models
from app.routers import auth, ai, blogs, admin, images

# Create the PostgreSQL tables based on our models
models.Base.metadata.create_all(bind=engine)

# Thread pool configuration
THREAD_POOL_WORKERS = int(os.getenv("THREAD_POOL_WORKERS", "300"))  # Default to 300 workers


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
    
    yield
    
    # Shutdown
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
    """Health check endpoint."""
    return {"status": "healthy, CI/CD running", "service": "cms-backend"}


api_app.include_router(auth.router, prefix="/auth", tags=["auth"])
api_app.include_router(ai.router, prefix="/ai", tags=["ai"])
api_app.include_router(blogs.router, tags=["blogs"])
api_app.include_router(images.router, tags=["images"])
api_app.include_router(admin.router, prefix="/admin", tags=["admin"])

# Create root app and mount API at /cms-backend
app = FastAPI()
app.mount("/", api_app)