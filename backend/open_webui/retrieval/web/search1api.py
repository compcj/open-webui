from __future__ import annotations

from open_webui.retrieval.web.main import SearchResult, get_filtered_results
from open_webui.utils.session_pool import get_session


async def search_search1api(
    api_key: str,
    query: str,
    count: int,
    filter_list: list[str | None] | None = None,
    *,
    endpoint: str = 'search',
) -> list[SearchResult]:
    """Search the web using Search1API and return normalized results."""
    if endpoint not in ('search', 'ask'):
        raise ValueError(f'Unsupported Search1API endpoint: {endpoint!r}')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {'query': query}
    if endpoint == 'search':
        payload['max_results'] = count
    session = await get_session()
    async with session.post(
        f'https://api.search1api.com/{endpoint}',
        headers=headers,
        json=payload,
    ) as response:
        response.raise_for_status()
        data = await response.json()

    results = data.get('results', [])
    if filter_list:
        results = get_filtered_results(results, filter_list)
    return [
        SearchResult(
            link=result.get('link', ''),
            title=result.get('title'),
            snippet=result.get('snippet') or result.get('content'),
        )
        for result in results[:count]
    ]
