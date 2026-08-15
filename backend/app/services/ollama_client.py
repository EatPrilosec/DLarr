import json
import re
from typing import Dict, Any, List, Optional, Tuple
import httpx


def extract_json_from_llm(content: str) -> Any:
    if not content:
        raise ValueError("Empty LLM response content")

    # 1. Strip thinking tags <think>...</think>
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()

    # 2. Try direct parse
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 3. Strip markdown code fences ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass

    # 4. Find outermost { ... }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(cleaned[first_brace:last_brace + 1])
        except Exception:
            pass

    # 5. Find outermost [ ... ]
    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(cleaned[first_bracket:last_bracket + 1])
        except Exception:
            pass

    raise ValueError(f"Could not parse valid JSON from LLM response: {cleaned[:200]}")


class OllamaClient:
    @staticmethod
    async def test_connection(base_url: str) -> Dict[str, Any]:
        url = base_url.rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    return {
                        "success": True,
                        "message": f"Connected to Ollama ({len(models)} models available)",
                        "available_models": models,
                        "details": {"model_count": len(models)}
                    }
                else:
                    return {"success": False, "message": f"Ollama returned status {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"Cannot connect to Ollama at {url}: {str(e)}"}

    @staticmethod
    async def list_models(base_url: str) -> List[str]:
        url = base_url.rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{url}/api/tags")
                if response.status_code == 200:
                    return [m.get("name") for m in response.json().get("models", [])]
            except Exception:
                pass
        return []

    @staticmethod
    async def query_model_structured(
        base_url: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        url = base_url.rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            content = data.get("message", {}).get("content", "")
            return extract_json_from_llm(content)

    @classmethod
    async def generate_with_fallback(
        cls,
        base_url: str,
        primary_model: str,
        fallback_model: Optional[str],
        system_prompt: str,
        user_prompt: str
    ) -> Tuple[Dict[str, Any], str]:
        """
        Executes structured query against primary_model.
        If it fails, automatically attempts fallback_model.
        Returns: (parsed_json_result, model_used)
        """
        try:
            result = await cls.query_model_structured(
                base_url=base_url,
                model=primary_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt
            )
            return result, primary_model
        except Exception as primary_error:
            if fallback_model and fallback_model != primary_model:
                try:
                    result = await cls.query_model_structured(
                        base_url=base_url,
                        model=fallback_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt
                    )
                    return result, fallback_model
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"Both primary ({primary_model}) and fallback ({fallback_model}) failed: "
                        f"Primary: {str(primary_error)}, Fallback: {str(fallback_error)}"
                    )
            raise primary_error
