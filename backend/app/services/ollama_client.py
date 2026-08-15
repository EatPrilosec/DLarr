import json
from typing import Dict, Any, List, Optional, Tuple
import httpx


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
            return json.loads(content)

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
        # Try primary model
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
