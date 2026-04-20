"""
Configuration module for the automation pipeline.
Loads environment variables and provides typed configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the same directory as this file.
# Exported as `env_path` so other modules (e.g. chat.py) can write to
# the *same* file that we read from — keeping API key persistence correct.
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Also check project root .env.local as a fallback (won't override existing vars)
_root_env_local = Path(__file__).parent.parent.parent / ".env.local"
if _root_env_local.exists():
    load_dotenv(dotenv_path=_root_env_local, override=False)


@dataclass
class Config:
    """Configuration for the automation pipeline."""
    
    # Google Gemini API
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gemini-flash-latest"))
    
    # Browser-use settings
    headless: bool = field(default_factory=lambda: os.getenv("HEADLESS", "false").lower() == "true")
    
    # Paths
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent)
    
    def validate(self) -> None:
        """Validate required configuration."""
        if not self.google_api_key:
            raise ValueError(
                "GOOGLE_API_KEY environment variable is required. "
                "Get one at https://aistudio.google.com/app/apikey"
            )


# Global config instance
config = Config()


