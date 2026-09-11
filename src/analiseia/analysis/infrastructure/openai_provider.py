#!/usr/bin/env python3
"""
OpenAI LLM provider implementation.
"""

import os
from typing import Optional, List, Type
from pydantic import BaseModel

from .llm import LLMClient
from analiseia.analysis.domain.models import ModelProfile


class OpenAIProvider(LLMClient):
    """OpenAI LLM provider."""

    def __init__(self, profile: ModelProfile):
        super().__init__(profile)
        # Import here to avoid hard dependency if not used
        try:
            import openai
            self.client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        except ImportError:
            raise ImportError("openai package is required for OpenAI provider")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text response from OpenAI."""
        try:
            response = self.client.chat.completions.create(
                model=self.profile.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.profile.temperature,
                max_tokens=4000,  # Reasonable default
            )

            return response.choices[0].message.content
        except Exception as e:
            # Fallback to basic error handling
            raise RuntimeError(f"OpenAI generation failed: {str(e)}")

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        image_paths: Optional[List[str]] = None
    ) -> BaseModel:
        """Generate structured response from OpenAI."""
        try:
            # OpenAI supports structured output via response_format
            response = self.client.chat.completions.create(
                model=self.profile.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=self.profile.temperature,
                max_tokens=4000,
            )

            # Parse the JSON response into the Pydantic model
            import json
            json_data = json.loads(response.choices[0].message.content)
            return response_model(**json_data)
        except Exception as e:
            raise RuntimeError(f"OpenAI structured generation failed: {str(e)}")

    def generate_multimodal(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: List[str]
    ) -> str:
        """Generate multimodal response from OpenAI."""
        try:
            import base64

            # Prepare the content with images
            content = [{"type": "text", "text": f"{system_prompt}\n\n{user_prompt}"}]

            # Add images
            for image_path in image_paths:
                with open(image_path, "rb") as img_file:
                    img_data = base64.b64encode(img_file.read()).decode('utf-8')

                # Determine mime type based on extension
                if image_path.lower().endswith('.png'):
                    mime_type = "image/png"
                elif image_path.lower().endswith('.jpg') or image_path.lower().endswith('.jpeg'):
                    mime_type = "image/jpeg"
                else:
                    mime_type = "image/png"  # default

                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{img_data}"
                    }
                })

            response = self.client.chat.completions.create(
                model=self.profile.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": content
                    }
                ],
                max_tokens=4000,
            )

            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"OpenAI multimodal generation failed: {str(e)}")