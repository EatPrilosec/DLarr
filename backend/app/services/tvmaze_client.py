from typing import Dict, Any, List, Optional
import httpx


class TVmazeClient:
    BASE_URL = "https://api.tvmaze.com"

    @staticmethod
    async def test_connection() -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{TVmazeClient.BASE_URL}/shows/1")
                if response.status_code == 200:
                    return {"success": True, "message": "Connected to TVmaze public API successfully"}
                else:
                    return {"success": False, "message": f"TVmaze returned HTTP {response.status_code}"}
            except Exception as e:
                return {"success": False, "message": f"TVmaze connection error: {str(e)}"}

    @staticmethod
    async def lookup_show(
        tvdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        title: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if tvdb_id:
                try:
                    res = await client.get(f"{TVmazeClient.BASE_URL}/lookup/shows", params={"thetvdb": tvdb_id})
                    if res.status_code in (200, 301, 302, 307, 308):
                        return res.json()
                except Exception:
                    pass
            
            if imdb_id:
                try:
                    res = await client.get(f"{TVmazeClient.BASE_URL}/lookup/shows", params={"imdb": imdb_id})
                    if res.status_code in (200, 301, 302, 307, 308):
                        return res.json()
                except Exception:
                    pass

            if title:
                try:
                    res = await client.get(f"{TVmazeClient.BASE_URL}/singlesearch/shows", params={"q": title})
                    if res.status_code == 200:
                        return res.json()
                except Exception:
                    pass

        return None

    @staticmethod
    async def get_episodes(tvmaze_id: int) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{TVmazeClient.BASE_URL}/shows/{tvmaze_id}/episodes", params={"specials": "1"})
            if response.status_code == 200:
                return response.json()
        return []
