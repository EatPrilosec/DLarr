from typing import Dict, Any, List, Optional
import httpx


class OMDbClient:
    BASE_URL = "https://www.omdbapi.com/"

    @staticmethod
    async def test_connection(api_key: str) -> Dict[str, Any]:
        if not api_key:
            return {"success": False, "message": "OMDb API key is empty"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(OMDbClient.BASE_URL, params={"apikey": api_key, "i": "tt0944947"})
                data = response.json()
                if data.get("Response") == "True":
                    return {"success": True, "message": "Connected to OMDb API successfully"}
                else:
                    return {"success": False, "message": f"OMDb Error: {data.get('Error', 'Unknown error')}"}
            except Exception as e:
                return {"success": False, "message": f"OMDb connection error: {str(e)}"}

    @staticmethod
    async def get_show_by_imdb(api_key: str, imdb_id: str) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(OMDbClient.BASE_URL, params={"apikey": api_key, "i": imdb_id, "type": "series"})
            if response.status_code == 200:
                data = response.json()
                if data.get("Response") == "True":
                    return data
        return None

    @staticmethod
    async def get_season_episodes(api_key: str, imdb_id: str, season_number: int) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                OMDbClient.BASE_URL,
                params={"apikey": api_key, "i": imdb_id, "Season": season_number}
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("Response") == "True":
                    return data.get("Episodes", [])
        return []
