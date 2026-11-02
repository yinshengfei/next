"""Non-secret preferences on disk; credentials only in macOS Keychain."""

import json
import os
import sys
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator


CONFIG_DIR = Path.home() / "Library" / "Application Support" / "Next"
CONFIG_PATH = CONFIG_DIR / "config.json"
KEYCHAIN_SERVICE = "Next Local Task Assistant"
OPENAI_BASE_URL = "https://api.openai.com/v1"


class SetupError(Exception):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "openai-compatible"] = "openai"
    base_url: str = Field(default=OPENAI_BASE_URL, min_length=1, max_length=2048)
    model: str = Field(default="gpt-5.4-mini", min_length=1, max_length=100)
    overview_model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    todoist_use_environment_proxy: bool = True
    port: int = Field(default=8765, ge=1024, le=65535)
    language: str = Field(default="English", min_length=1, max_length=50)
    # Optional names or IDs, matched exactly (names are case-insensitive).
    important_projects: List[str] = Field(default_factory=list, max_length=30)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # A base URL is public configuration, never a place for a credential.
        parsed = TypeAdapter(AnyHttpUrl).validate_python(value)
        if (
            parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
            or parsed.query is not None or parsed.fragment is not None
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Use an HTTPS base URL without credentials, query parameters, or fragments.")
        parts = urlsplit(str(parsed))
        path = parts.path.rstrip("/")
        if path.endswith(("/responses", "/chat/completions")):
            raise ValueError("Use the API base URL, not a complete request endpoint.")
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    @field_validator("model", "overview_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value is None:
            return value
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("Copy the exact model ID without spaces or line breaks.")
        return value

    @property
    def ai_key_account(self) -> str:
        # Keep existing official keys working, but never forward one to a new host.
        if self.base_url == OPENAI_BASE_URL:
            return "openai"
        return "ai:" + self.base_url


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    if not path.exists():
        return Settings()
    try:
        return Settings.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        raise SetupError("Could not read config.json. Check the HTTPS base URL, model, and README example.") from None


def save_settings(settings: Settings, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(settings.model_dump(), output, indent=2, ensure_ascii=False)
        output.write("\n")
    os.chmod(path, 0o600)


def keychain():
    if sys.platform != "darwin":
        raise SetupError("Credential storage requires macOS Keychain.")
    try:
        from keyring.backends.macOS import Keyring
        return Keyring()
    except Exception:
        raise SetupError("macOS Keychain is unavailable. Unlock your login keychain and try again.") from None


def get_secret(account: str) -> str:
    try:
        value = keychain().get_password(KEYCHAIN_SERVICE, account)
    except SetupError:
        raise
    except Exception:
        raise SetupError("Cannot access macOS Keychain. Unlock it and try again.") from None
    if not value:
        raise SetupError("Credentials are missing. Run: python -m next_app setup")
    return value


def set_secret(account: str, value: str) -> None:
    try:
        keychain().set_password(KEYCHAIN_SERVICE, account, value)
    except SetupError:
        raise
    except Exception:
        raise SetupError("Could not save credentials to macOS Keychain.") from None
