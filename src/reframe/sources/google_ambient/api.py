"""Minimal client for the Google Photos Ambient API.

https://developers.google.com/photos/ambient/reference/rest
"""

from typing import Any, Optional

import requests

from .auth import TokenStore

BASE_URL = "https://photosambient.googleapis.com/v1"


class AmbientError(RuntimeError):
    pass


class AmbientClient:
    def __init__(self, tokens: TokenStore):
        self.tokens = tokens
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
        resp = self.session.request(method, BASE_URL + path, headers=headers, timeout=30, **kwargs)
        if not resp.ok:
            raise AmbientError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}

    def create_device(self, display_name: str, request_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "/devices", params={"requestId": request_id}, json={"displayName": display_name}
        )

    def get_device(self, device_id: str) -> dict[str, Any]:
        return self._request("GET", f"/devices/{device_id}")

    def delete_device(self, device_id: str) -> None:
        self._request("DELETE", f"/devices/{device_id}")

    def list_media_items(
        self,
        device_id: str,
        media_source_id: Optional[str] = None,
        page_size: int = 100,
        page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"deviceId": device_id, "pageSize": page_size}
        if media_source_id:
            params["mediaSourceId"] = media_source_id
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "/mediaItems", params=params)

    def list_all_media_items(
        self, device_id: str, media_source_id: Optional[str] = None, max_pages: int = 20
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return (items, complete).

        `complete` is False when max_pages was hit with a nextPageToken still
        pending; such a listing must not be used to infer removals. Each page
        costs one of the 240 daily list requests per device, hence max_pages.
        """
        items: list[dict[str, Any]] = []
        page_token = None
        for _ in range(max_pages):
            page = self.list_media_items(device_id, media_source_id, page_token=page_token)
            items.extend(page.get("mediaItems", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                return items, True
        return items, False

    def download(self, base_url: str, suffix: str, authenticated: bool = True) -> requests.Response:
        """Fetch bytes; `suffix` is e.g. "=w1280-h1280" for photos or "=dv" for videos."""
        headers = {"Authorization": f"Bearer {self.tokens.access_token()}"} if authenticated else {}
        return self.session.get(base_url + suffix, headers=headers, timeout=120)
