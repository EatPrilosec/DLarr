from typing import Dict, Any, List, Optional
import httpx


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    @staticmethod
    async def test_connection(api_key: str) -> Dict[str, Any]:
        if not api_key:
            return {"success": False, "message": "TMDB API key is empty"}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    f"{TMDBClient.BASE_URL}/configuration",
                    params={"api_key": api_key}
                )
                if response.status_code == 200:
                    return {"success": True, "message": "Connected to TMDB API successfully"}
                else:
                    return {"success": False, "message": f"TMDB returned status {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"TMDB connection error: {str(e)}"}

    @staticmethod
    async def find_by_external_id(api_key: str, external_id: str, source: str = "tvdb_id") -> Optional[int]:
        """Find TMDB TV show ID via TVDB ID or IMDb ID"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{TMDBClient.BASE_URL}/find/{external_id}",
                params={"api_key": api_key, "external_source": source}
            )
            if response.status_code == 200:
                results = response.json().get("tv_results", [])
                if results:
                    return results[0].get("id")
        return None

    @staticmethod
    async def search_tv(api_key: str, title: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        params = {"api_key": api_key, "query": title}
        if year:
            params["first_air_date_year"] = year

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{TMDBClient.BASE_URL}/search/tv", params=params)
            if response.status_code == 200:
                return response.json().get("results", [])
        return []

    @staticmethod
    async def get_show_details(api_key: str, tmdb_id: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{TMDBClient.BASE_URL}/tv/{tmdb_id}",
                params={"api_key": api_key, "append_to_response": "alternative_titles,translations"}
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    async def get_season_episodes(api_key: str, tmdb_id: int, season_number: int) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{TMDBClient.BASE_URL}/tv/{tmdb_id}/season/{season_number}",
                params={"api_key": api_key, "append_to_response": "translations"}
            )
            if response.status_code == 200:
                return response.json().get("episodes", [])
        return []
