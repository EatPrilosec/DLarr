from typing import Dict, Any, List, Optional
import httpx


class OpenSubtitlesClient:
    BASE_URL = "https://api.opensubtitles.com/api/v1"

    @staticmethod
    async def test_connection(api_key: str, user_agent: str = "DLarr v0.1") -> Dict[str, Any]:
        if not api_key:
            return {"success": False, "message": "OpenSubtitles API key is empty"}

        headers = {
            "Api-Key": api_key,
            "User-Agent": user_agent,
            "Accept": "application/json"
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{OpenSubtitlesClient.BASE_URL}/infos/user", headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    user = data.get("data", {}).get("user", {})
                    return {
                        "success": True,
                        "message": f"Connected to OpenSubtitles (User: {user.get('username', 'Active')})",
                        "details": data.get("data")
                    }
                elif response.status_code == 401:
                    return {"success": False, "message": "OpenSubtitles API key invalid or unauthorized"}
                else:
                    return {"success": False, "message": f"OpenSubtitles returned HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"OpenSubtitles error: {str(e)}"}

    @staticmethod
    async def search_subtitles(
        api_key: str,
        user_agent: str = "DLarr v0.1",
        imdb_id: Optional[str] = None,
        tmdb_id: Optional[int] = None,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        languages: str = "en"
    ) -> List[Dict[str, Any]]:
        headers = {
            "Api-Key": api_key,
            "User-Agent": user_agent,
            "Accept": "application/json"
        }
        params = {"languages": languages}
        if imdb_id:
            # OpenSubtitles accepts numeric IMDb ID or tt format
            clean_imdb = imdb_id.replace("tt", "")
            if clean_imdb.isdigit():
                params["imdb_id"] = clean_imdb
        if tmdb_id:
            params["tmdb_id"] = str(tmdb_id)
        if season_number is not None:
            params["season_number"] = str(season_number)
        if episode_number is not None:
            params["episode_number"] = str(episode_number)

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{OpenSubtitlesClient.BASE_URL}/subtitles", headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
        return []
