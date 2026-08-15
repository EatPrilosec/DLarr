from typing import Dict, Any, List, Optional
import httpx


class SubDLClient:
    BASE_URL = "https://api.subdl.com/api/v1"

    @staticmethod
    async def test_connection(api_key: str) -> Dict[str, Any]:
        if not api_key:
            return {"success": False, "message": "SubDL API key is empty"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{SubDLClient.BASE_URL}/me", params={"api_key": api_key})
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") is True or "data" in data or "email" in data:
                        return {"success": True, "message": "Connected to SubDL API successfully"}
                # Try subtitles search as fallback test
                test_res = await client.get(f"{SubDLClient.BASE_URL}/subtitles", params={"api_key": api_key, "film_name": "Breaking Bad"})
                if test_res.status_code == 200:
                    return {"success": True, "message": "Connected to SubDL API successfully"}
                return {"success": False, "message": f"SubDL API returned HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"SubDL connection error: {str(e)}"}

    @staticmethod
    async def search_subtitles(
        api_key: str,
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int] = None,
        film_name: Optional[str] = None,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        languages: str = "en"
    ) -> List[Dict[str, Any]]:
        params = {"api_key": api_key, "languages": languages}
        if imdb_id:
            params["imdb_id"] = imdb_id
        if tmdb_id:
            params["tmdb_id"] = str(tmdb_id)
        if film_name:
            params["film_name"] = film_name
        if season_number is not None:
            params["season_number"] = season_number
        if episode_number is not None:
            params["episode_number"] = episode_number

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{SubDLClient.BASE_URL}/subtitles", params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") is True and "subtitles" in data:
                    return data.get("subtitles", [])
                elif "results" in data:
                    return data.get("results", [])
        return []

    @staticmethod
    async def download_subtitle_content(url: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.text
        return None
