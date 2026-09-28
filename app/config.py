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
    rate_limits_enabled: bool = True
    # Only behind a trusted reverse proxy: take the client IP from X-Forwarded-For.
    trust_forwarded_for: bool = False

    # Text model (OpenAI-compatible, OpenRouter by default).
    llm_base_url: str = "https://openrouter.ai/api/v1"
    # Optional comma-separated copies of the same server (round-robin + failover), e.g.
    # LLM_BASE_URLS=http://gpu1:8003/v1,http://gpu2:8003/v1 ; same for ASR_BASE_URLS / TTS_BASE_URLS.
    llm_base_urls: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 60
    llm_max_tool_rounds: int = 4
    # Replies are 1-3 sentences; a tight cap also bounds any runaway repetition.
    llm_max_tokens: int = 450
    llm_temperature: float = 0.3
    # OpenRouter reasoning control: "" = provider default, "off", or low/medium/high.
    llm_reasoning: str = ""
    # Extra JSON merged into chat requests, e.g. for local vLLM Qwen3.5 non-thinking mode:
    # {"chat_template_kwargs": {"enable_thinking": false}}
    llm_extra_body: dict = {}

    # Optional embeddings; keyword retrieval is used when unset.
    embedding_model: str = ""
    rag_top_k: int = 4

    # vLLM speech-to-text (Qwen3-ASR) and Kokoro text-to-speech, each in its own venv.
    asr_base_url: str = "http://localhost:8001/v1"
    asr_base_urls: str = ""
    asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    asr_api_key: str = ""
    tts_base_url: str = "http://localhost:8002/v1"
    tts_base_urls: str = ""
    tts_cache_mb: int = 32
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
