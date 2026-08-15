from typing import Dict, Any, List, Optional
import httpx


class SonarrClient:
    @staticmethod
    async def test_connection(url: str, api_key: str) -> Dict[str, Any]:
        url = url.rstrip("/")
        if not url.startswith("http"):
            url = f"http://{url}"
        
        headers = {"X-Api-Key": api_key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{url}/api/v3/system/status", headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "success": True,
                        "message": f"Connected to Sonarr v{data.get('version', 'unknown')}",
                        "details": {"version": data.get("version"), "app_name": data.get("appName")}
                    }
                else:
                    return {
                        "success": False,
                        "message": f"Sonarr returned HTTP status {response.status_code}: {response.text[:200]}"
                    }
            except Exception as e:
                return {"success": False, "message": f"Connection error: {str(e)}"}

    @staticmethod
    async def get_series(url: str, api_key: str) -> List[Dict[str, Any]]:
        url = url.rstrip("/")
        headers = {"X-Api-Key": api_key}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{url}/api/v3/series", headers=headers)
            response.raise_for_status()
            return response.json()

    @staticmethod
    async def get_series_detail(url: str, api_key: str, series_id: int) -> Dict[str, Any]:
        url = url.rstrip("/")
        headers = {"X-Api-Key": api_key}
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{url}/api/v3/series/{series_id}", headers=headers)
            response.raise_for_status()
            return response.json()

    @staticmethod
    async def get_episodes(url: str, api_key: str, series_id: int) -> List[Dict[str, Any]]:
        url = url.rstrip("/")
        headers = {"X-Api-Key": api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{url}/api/v3/episode?seriesId={series_id}", headers=headers)
            response.raise_for_status()
            return response.json()
