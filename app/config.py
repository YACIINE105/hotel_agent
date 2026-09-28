from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./hotel_agent.db"
    # Signs guest widget sessions. Must be set outside development.
    session_secret: str = "dev-only-change-me"
    guest_session_minutes: int = 240
    seed_demo: bool = True

    # Text model (OpenAI-compatible, OpenRouter by default).
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 60
    llm_max_tool_rounds: int = 4

    # Optional embeddings; keyword retrieval is used when unset.
    embedding_model: str = ""
    rag_top_k: int = 4

    # vLLM speech-to-text (Qwen3-ASR) and Kokoro text-to-speech, each in its own venv.
    asr_base_url: str = "http://localhost:8001/v1"
    asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    asr_api_key: str = ""
    tts_base_url: str = "http://localhost:8002/v1"
    tts_model: str = "oddadmix/Kokoro-7M-Distill"
    tts_voice: str = "af_msa"
    tts_api_key: str = ""
    # Kokoro-7M is English-only; other languages get text without audio.
    tts_languages: str = "en"
    tts_prefetch: int = 2

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def tts_language_set(self) -> set[str]:
        return {x.strip() for x in self.tts_languages.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
