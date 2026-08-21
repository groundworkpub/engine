from podcast_syndicator import get_podcastindex_headers


def test_get_podcastindex_headers():
    headers = get_podcastindex_headers(
        api_key="TEST_KEY", api_secret="TEST_SECRET"
    )
    assert headers["User-Agent"] == "GroundworkPodcastEngine/1.0"
    assert headers["X-Auth-Key"] == "TEST_KEY"
    assert "X-Auth-Date" in headers
    assert len(headers["Authorization"]) == 40  # SHA-1 hex hash
