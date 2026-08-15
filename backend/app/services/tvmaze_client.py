from typing import Dict, Any, List, Optional
import httpx


class TVmazeClient:
    BASE_URL = "https://api.tvmaze.com"

    @staticmethod
    async def test_connection() -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if tvdb_id:
                try:
                    res = await client.get(f"{TVmazeClient.BASE_URL}/lookup/shows", params={"thetvdb": tvdb_id})
                    if res.status_code == 200 and res.json():
                        return res.json()
                except Exception:
                    pass
            
            if imdb_id:
                try:
                    res = await client.get(f"{TVmazeClient.BASE_URL}/lookup/shows", params={"imdb": imdb_id})
                    if res.status_code == 200 and res.json():
                        return res.json()
                except Exception:
                    pass

            if title:
                titles_to_try = [title]
                if not title.lower().startswith("the "):
                    titles_to_try.append(f"The {title}")
                elif title.lower().startswith("the "):
                    titles_to_try.append(title[4:])

                for t in titles_to_try:
                    try:
                        res = await client.get(f"{TVmazeClient.BASE_URL}/singlesearch/shows", params={"q": t})
                        if res.status_code == 200 and res.json():
                            return res.json()
                    except Exception:
                        pass

        return None

    @staticmethod
    async def get_episodes(tvmaze_id: int) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                response = await client.get(f"{TVmazeClient.BASE_URL}/shows/{tvmaze_id}/episodes", params={"specials": "1"})
                if response.status_code == 200:
                    return response.json()
            except Exception:
                pass
        return []
