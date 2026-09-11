#!/usr/bin/env python3
"""
Gemini LLM provider implementation.
"""

import os
from typing import Optional, List, Type
from pydantic import BaseModel

from .llm import LLMClient
from analiseia.analysis.domain.models import ModelProfile


class GeminiProvider(LLMClient):
    """Google Gemini LLM provider."""

    def __init__(self, profile: ModelProfile):
        super().__init__(profile)
        # Import here to avoid hard dependency if not used
        try:
            from google import genai
            self.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        except ImportError:
            raise ImportError("google-genai package is required for Gemini provider")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Generate text response from Gemini."""
        try:
            # Combine system and user prompts for Gemini
            full_prompt = f"{system_prompt}\n\n{user_prompt}"

            response = self.client.models.generate_content(
                model=self.profile.model_name,
                contents=full_prompt,
                # Generation config can be added here if needed
            )

            return response.text
        except Exception as e:
            # Fallback to basic error handling
            raise RuntimeError(f"Gemini generation failed: {str(e)}")

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[BaseModel],
        image_paths: Optional[List[str]] = None
    ) -> BaseModel:
        """Generate structured response from Gemini."""
        try:
            # For structured output, we need to use Gemini's JSON mode
            full_prompt = f"{system_prompt}\n\n{user_prompt}"

            # Configure for JSON output
            generation_config = {
                "response_mime_type": "application/json",
            }

            response = self.client.models.generate_content(
                model=self.profile.model_name,
                contents=full_prompt,
                generation_config=generation_config,
            )

            # Parse the JSON response into the Pydantic model
            import json
            json_data = json.loads(response.text)
            return response_model(**json_data)
        except Exception as e:
            raise RuntimeError(f"Gemini structured generation failed: {str(e)}")

    def generate_multimodal(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: List[str]
    ) -> str:
        """Generate multimodal response from Gemini."""
        try:
            import base64

            # Prepare the contents with images
            contents = []

            # Add text prompt
            text_part = f"{system_prompt}\n\n{user_prompt}"
            contents.append({"text": text_part})

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

                contents.append({
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": img_data
                    }
                })

            response = self.client.models.generate_content(
                model=self.profile.model_name,
                contents=contents
            )

            return response.text
        except Exception as e:
            raise RuntimeError(f"Gemini multimodal generation failed: {str(e)}")