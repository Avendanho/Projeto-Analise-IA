from enum import Enum
from typing import Optional, List
from analiseia.config.settings import get_settings
from analiseia.analysis.domain.models import ModelProfile
from analiseia.analysis.infrastructure.llm import LLMClient

class DifficultyLevel(Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"

class ModelRouter:
    """Roteador inteligente para escolher o modelo LLM correto com base na dificuldade e multimodalidade."""
    
    def __init__(self):
        self.settings = get_settings()
        self._primary_profile = ModelProfile(
            provider=self.settings.ai_provider,
            model_name=self.settings.ai_primary_model,
            multimodal=False,
            temperature=self.settings.ai_temperature,
            thinking=self.settings.ai_enable_thinking,
            timeout=self.settings.ai_timeout,
            retry_limit=self.settings.ai_max_retries,
            concurrency=self.settings.ai_max_concurrent_requests,
            max_output_tokens=8192
        )
        self._vision_profile = ModelProfile(
            provider=self.settings.ai_provider,
            model_name=self.settings.ai_vision_model,
            multimodal=True,
            temperature=self.settings.ai_temperature,
            thinking=False,
            timeout=self.settings.ai_timeout,
            retry_limit=self.settings.ai_max_retries,
            concurrency=self.settings.ai_max_concurrent_requests,
            max_output_tokens=8192
        )
        self._verifier_profile = ModelProfile(
            provider=self.settings.ai_provider,
            model_name=self.settings.ai_verifier_model,
            multimodal=False,
            temperature=self.settings.ai_temperature,
            thinking=True, # Verificador geralmente precisa pensar mais
            timeout=self.settings.ai_timeout,
            retry_limit=self.settings.ai_max_retries,
            concurrency=self.settings.ai_max_concurrent_requests,
            max_output_tokens=8192
        )

    def _get_client_for_profile(self, profile: ModelProfile) -> LLMClient:
        provider = profile.provider.lower()
        if provider == "ollama":
            from .ollama_provider import OllamaProvider
            return OllamaProvider(profile)
        elif provider == "gemini":
            from .gemini_provider import GeminiProvider
            return GeminiProvider(profile)
        elif provider == "openai":
            from .openai_provider import OpenAIProvider
            return OpenAIProvider(profile)
        else:
            raise ValueError(f"Provedor não suportado: {profile.provider}")

    def route_for_classification(self, requires_vision: bool = False, difficulty: DifficultyLevel = DifficultyLevel.EASY) -> LLMClient:
        """Retorna o cliente LLM apropriado para a classificação primária de um critério."""
        if requires_vision:
            return self._get_client_for_profile(self._vision_profile)
            
        if difficulty == DifficultyLevel.HARD:
            # Em cenários muito difíceis (conflito direto), o verificador pode ser usado de cara
            return self._get_client_for_profile(self._verifier_profile)
            
        return self._get_client_for_profile(self._primary_profile)

    def route_for_verification(self) -> LLMClient:
        """Retorna o cliente usado para o EvidenceVerifier ou FinalValidator."""
        return self._get_client_for_profile(self._verifier_profile)

def get_model_router() -> ModelRouter:
    return ModelRouter()
