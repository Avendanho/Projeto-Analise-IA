import os
from typing import Type, TypeVar, Optional, List
from pydantic import BaseModel
from analiseia.analysis.domain.models import ModelProfile

T = TypeVar("T", bound=BaseModel)

class LLMClient:
    """Interface abstrata para o provedor de LLM com suporte a geração simples e estruturada."""
    
    def __init__(self, profile: ModelProfile):
        self.profile = profile
        self.on_thinking = None
        
    def set_thinking_callback(self, callback):
        self.on_thinking = callback

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    def generate_structured(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        response_model: Type[T], 
        image_paths: Optional[List[str]] = None
    ) -> T:
        raise NotImplementedError

    def generate_multimodal(
        self, 
        system_prompt: str, 
        user_prompt: str, 
        image_paths: List[str]
    ) -> str:
        raise NotImplementedError
