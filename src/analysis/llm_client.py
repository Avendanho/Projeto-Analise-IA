import os
import json
from typing import Callable, Tuple

def get_llm_client() -> Tuple[Callable[[str, str], str], str]:
    """
    Retorna uma tupla contendo:
    1. Uma função analyze_article(system_prompt: str, user_prompt: str) -> str
       que executa a chamada ao LLM e retorna a string (esperada como JSON)
    2. Uma string com o nome do provider utilizado (ex: "Gemini 2.5 Flash")
    
    A hierarquia é: Gemini -> Claude -> OpenAI -> Ollama
    """
    
    # 1. Tentar Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            # Testa o cliente rapidamente
            client = genai.Client(api_key=gemini_key)
            
            def gemini_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                contents = []
                if image_paths:
                    from PIL import Image
                    for img_path in image_paths:
                        try:
                            contents.append(Image.open(img_path))
                        except Exception:
                            pass
                contents.append(user_prompt)
                
                # O Gemini prefere receber system instructions na configuração do modelo
                response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=0.0
                    )
                )
                return response.text
                
            return gemini_analyze, "Gemini 3.6 Flash"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar Gemini ({e}). Tentando próximo provider...")

    # 2. Tentar Claude (Anthropic)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            
            def claude_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                content_list = []
                if image_paths:
                    import base64
                    import mimetypes
                    for img_path in image_paths:
                        try:
                            mime_type, _ = mimetypes.guess_type(img_path)
                            if not mime_type: mime_type = "image/jpeg"
                            with open(img_path, "rb") as image_file:
                                encoded = base64.b64encode(image_file.read()).decode("utf-8")
                            content_list.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": encoded
                                }
                            })
                        except Exception:
                            pass
                
                content_list.append({"type": "text", "text": user_prompt})
                
                # O Claude também usa system prompts na sua config de mensagem
                # e podemos forçar JSON garantindo que ele responda como um formato estruturado
                # Adicionamos um pre-fill "{" para forçar o output JSON
                response = client.messages.create(
                    model="claude-3-7-sonnet-20250219",
                    max_tokens=2048,
                    temperature=0.0,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": content_list},
                        {"role": "assistant", "content": "{"}
                    ]
                )
                
                # Se o response não começar com {, nós colocamos de volta o { que usamos de prefill
                text = response.content[0].text
                if not text.lstrip().startswith("{"):
                    text = "{" + text
                    
                return text
                
            return claude_analyze, "Claude 3.7 Sonnet"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar Claude ({e}). Tentando próximo provider...")

    # 3. Tentar OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            
            def openai_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
                content_list = []
                if image_paths:
                    import base64
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
                
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content_list}
                    ],
                    response_format={"type": "json_object"},
            temperature=0.0
                )
                return response.choices[0].message.content
                
            return openai_analyze, "OpenAI gpt-4o-mini"
        except Exception as e:
            print(f"⚠️ Aviso: Falha ao inicializar OpenAI ({e}). Tentando próximo provider...")

    # 4. Fallback Ollama Local
    import openai
    model_name = os.environ.get("LLM_MODEL", "qwen2.5")
    client = None
    
    # Tenta localhost primeiro
    try:
        client = openai.OpenAI(base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), api_key="ollama")
        client.models.list()
    except:
        # Tenta docker network fallback se localhost falhar
        try:
            client = openai.OpenAI(base_url="http://host.docker.internal:11434/v1", api_key="ollama")
            client.models.list()
        except Exception as e:
            pass
            
    def ollama_analyze(system_prompt: str, user_prompt: str, image_paths: list = None) -> str:
        if not client:
            raise Exception("Nenhum provider de IA disponível. Configure as API Keys no .env ou inicie o Ollama local.")
            
        content_list = []
        if image_paths:
            import base64
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
            
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_list}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            extra_body={"options": {"num_ctx": 32768, "num_predict": 2048}}
        )
        return response.choices[0].message.content
        
    return ollama_analyze, f"Ollama Local ({model_name})"
