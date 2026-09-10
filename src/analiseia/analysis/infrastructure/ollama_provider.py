import os
import json
import base64
from typing import Type, TypeVar, List, Optional
from pydantic import BaseModel, ValidationError
import openai
from .llm import LLMClient
from analiseia.analysis.domain.models import ModelProfile

T = TypeVar("T", bound=BaseModel)

class OllamaProvider(LLMClient):
    def __init__(self, profile: ModelProfile):
        super().__init__(profile)
        self.client = None
        self._init_client()
        
    def _init_client(self):
        base_urls = [
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "http://host.docker.internal:11434/v1"
        ]
        for url in base_urls:
            try:
                self.client = openai.OpenAI(base_url=url, api_key="ollama")
                self.client.models.list()
                return
            except Exception:
                pass
                
    def _build_content(self, user_prompt: str, image_paths: Optional[List[str]] = None) -> List[dict]:
        content_list = []
        if image_paths and self.profile.multimodal:
            for img_path in image_paths:
                try:
                    with open(img_path, "rb") as image_file:
                        encoded = base64.b64encode(image_file.read()).decode("utf-8")
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}
                    })
                except Exception:
                    pass
        content_list.append({"type": "text", "text": user_prompt})
        return content_list

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.client:
            raise Exception("Ollama não está rodando.")
        
        response = self.client.chat.completions.create(
            model=self.profile.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=self.profile.temperature,
            timeout=self.profile.timeout,
            extra_body={"options": {"num_ctx": self.profile.context_limit, "num_predict": self.profile.max_output_tokens}}
        )
        return response.choices[0].message.content

    def generate_multimodal(self, system_prompt: str, user_prompt: str, image_paths: List[str]) -> str:
        if not self.client:
            raise Exception("Ollama não está rodando.")
        
        content = self._build_content(user_prompt, image_paths)
        response = self.client.chat.completions.create(
            model=self.profile.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            temperature=self.profile.temperature,
            timeout=self.profile.timeout,
            extra_body={"options": {"num_ctx": self.profile.context_limit, "num_predict": self.profile.max_output_tokens}}
        )
        return response.choices[0].message.content

    def generate_structured(self, system_prompt: str, user_prompt: str, response_model: Type[T], image_paths: Optional[List[str]] = None) -> T:
        if not self.client:
            raise Exception("Ollama não está rodando.")
            
        content_list = self._build_content(user_prompt, image_paths)
        
        schema_dict = response_model.model_json_schema()
        schema_prompt = f"\n\nResponda APENAS em JSON estritamente aderente ao seguinte schema:\n{json.dumps(schema_dict)}"
        
        messages = [
            {"role": "system", "content": system_prompt + schema_prompt},
            {"role": "user", "content": content_list}
        ]
        
        last_exception = None
        
        for attempt in range(self.profile.retry_limit):
            response = self.client.chat.completions.create(
                model=self.profile.model_name,
                messages=messages,
                response_format={"type": "json_object"} if self.profile.structured_output else None,
                temperature=self.profile.temperature,
                timeout=self.profile.timeout,
                extra_body={"options": {"num_ctx": self.profile.context_limit, "num_predict": self.profile.max_output_tokens}}
            )
            
            
            msg_obj = response.choices[0].message
            json_str = msg_obj.content
            reasoning_content = getattr(msg_obj, 'reasoning_content', None)
            
            # DeepSeek-R1 / Qwen3-R1 models embed thinking in <think> tags natively when json mode is forced
            if "<think>" in json_str:
                import re
                think_match = re.search(r'<think>(.*?)</think>', json_str, re.DOTALL)
                if think_match:
                    reasoning_content = think_match.group(1).strip()
                json_str = re.sub(r'<think>.*?</think>', '', json_str, flags=re.DOTALL).strip()
            
            if reasoning_content and hasattr(self, 'on_thinking'):
                self.on_thinking(reasoning_content)

            try:
                return response_model.model_validate_json(json_str)
            except ValidationError as e:
                last_exception = e
                # Fallback feedback
                keys = list(schema_dict.get("properties", {}).keys())
                error_msg = f"ATENÇÃO: JSON inválido. Você deve retornar um objeto JSON contendo OBRIGATORIAMENTE as chaves: {keys}.\nErro:\n{str(e)}\n\nCorrija imediatamente."
                messages.append({"role": "assistant", "content": json_str})
                messages.append({"role": "user", "content": error_msg})
                
        raise last_exception
