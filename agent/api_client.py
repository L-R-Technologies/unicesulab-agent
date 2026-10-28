import requests

from agent import __version__
from agent.constants import API_TIMEOUT_SECONDS

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500
_HTTP_CLIENT_ERROR = 400
_ERROR_TEXT_MAX_CHARS = 200


class ApiError(Exception):
    pass


class ApiAuthError(ApiError):
    pass


class ApiUnavailableError(ApiError):
    pass


class ApiRejectedError(ApiError):
    pass


class ApiClient:
    def __init__(
        self, base_url: str, token: str, session: requests.Session | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                timeout=API_TIMEOUT_SECONDS,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError(f"Servidor inacessível: {exc}") from exc

        status = response.status_code
        if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            raise ApiAuthError(
                f"Autenticação recusada (HTTP {status}): {_error_message(response)}"
            )
        if status == _HTTP_TOO_MANY_REQUESTS or status >= _HTTP_SERVER_ERROR:
            raise ApiUnavailableError(f"Erro no servidor (HTTP {status})")
        if status >= _HTTP_CLIENT_ERROR:
            raise ApiRejectedError(
                f"Requisição recusada (HTTP {status}): {_error_message(response)}"
            )
        return response

    def ping(self) -> dict:
        response = self._request("GET", "/api/agent/ping", params={"agent_version": __version__})
        return _data_of(response)

    def send_capture(
        self, raw_text: str, raw_hash: str, captured_at: str, serial_settings: dict
    ) -> dict:
        response = self._request(
            "POST",
            "/api/agent/lab-captures",
            json={
                "raw_text": raw_text,
                "raw_hash": raw_hash,
                "captured_at": captured_at,
                "agent_version": __version__,
                "serial_settings": serial_settings,
            },
        )
        return _data_of(response)


def _data_of(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiError("Resposta do servidor não é JSON válido") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ApiError("Resposta do servidor sem o campo 'data'")
    return data


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:_ERROR_TEXT_MAX_CHARS]
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    return response.text[:_ERROR_TEXT_MAX_CHARS]
