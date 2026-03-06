"""
Centralized configuration management for Vidmaker3

Uses Pydantic for settings validation and environment variable loading.
"""

import os
from functools import lru_cache
import os
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict

# Load .env file first
from dotenv import load_dotenv
import pathlib

# Find .env relative to project root (where this file is located)
project_root = pathlib.Path(__file__).parent.parent.resolve()
env_path = project_root / ".env"
print(f"DEBUG: Loading .env from: {env_path}")
print(f"DEBUG: .env exists: {env_path.exists()}")
load_dotenv(env_path)

# Debug: print env var
import os
print(f"DEBUG: DISCORD_BOT_TOKEN from env: {os.environ.get('DISCORD_BOT_TOKEN', 'NOT FOUND')[:20]}...")


class RedisSettings(BaseModel):
    """Redis connection settings"""
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    password: Optional[str] = Field(default=None)
    db: int = Field(default=0)
    
    def __init__(self, **data):
        # Support REDIS_URL environment variable
        if 'REDIS_URL' in os.environ:
            import re
            url = os.environ['REDIS_URL']
            # Parse redis://host:port/db
            match = re.match(r'redis://(?:.*@)?([^:]+):(\d+)(?:/(\d+))?', url)
            if match:
                data['host'] = match.group(1)
                data['port'] = int(match.group(2))
                if match.group(3):
                    data['db'] = int(match.group(3))
        super().__init__(**data)
    
    @property
    def url(self) -> str:
        # Check for REDIS_URL env var first
        if os.getenv('REDIS_URL'):
            return os.getenv('REDIS_URL')
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class R2Settings(BaseModel):
    """Cloudflare R2 storage settings"""
    access_key_id: str = Field(default="")
    secret_access_key: str = Field(default="")
    endpoint_url: str = Field(default="")
    region: str = Field(default="auto")
    bucket_name: str = Field(default="")


class GoogleSettings(BaseModel):
    """Google API settings"""
    spreadsheet_id: str = Field(default="")
    credentials_path: str = Field(default="")


class ProcessingSettings(BaseModel):
    """Video processing settings"""
    max_workers: int = Field(default=5)
    max_queue_size: int = Field(default=100)
    job_timeout: int = Field(default=3600)  # 1 hour
    temp_folder: str = Field(default="temp")
    
    @field_validator("max_workers")
    @classmethod
    def validate_workers(cls, v):
        if v < 1:
            return 1
        if v > 24:
            return 24
        return v


class FeatureFlags(BaseModel):
    """Feature toggle flags"""
    enable_dynamic_crop: bool = Field(default=True)
    enable_transcription: bool = Field(default=True)
    enable_face_detection: bool = Field(default=True)


class Settings(BaseModel):
    """Main application settings - loads from environment variables"""
    
    # Discord
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    
    # Redis
    redis: RedisSettings = Field(default_factory=RedisSettings)
    
    # R2 Storage
    r2: R2Settings = Field(default_factory=R2Settings)
    
    # Google
    google: GoogleSettings = Field(default_factory=GoogleSettings)
    
    # Processing
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    
    # Features
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    
    # External APIs
    rapidapi_key: str = Field(default="", alias="RAPIDAPI_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    fal_key: str = Field(default="", alias="FAL_KEY")
    
    # Webhooks
    make_webhook_url: str = Field(default="", alias="MAKE_WEBHOOK_URL")
    tweet_webhook_url: str = Field(default="", alias="TWEET_WEBHOOK_URL")
    
    # Paths (with Docker-friendly defaults)
    ffmpeg_path: str = Field(default="ffmpeg")
    imagick_path: str = Field(default="/usr/bin/convert")
    
    # Use model_config for Pydantic v2
    model_config = ConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        populate_by_name = True,
        extra = "allow",
        case_sensitive = True
    )


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Loads from environment variables on first call.
    """
    # Make sure dotenv is loaded before getting settings
    from dotenv import load_dotenv
    import pathlib
    project_root = pathlib.Path(__file__).parent.parent.resolve()
    env_path = project_root / ".env"
    load_dotenv(env_path, override=True)
    
    return Settings()
