from http import HTTPStatus
from io import BytesIO
from typing import Any, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.speech_request import SpeechRequest
from ...types import File, Response


def _get_kwargs(
    *,
    body: SpeechRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/audio/speech",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Optional[Union[File, HTTPValidationError]]:
    if response.status_code == 200:
        response_200 = File(payload=BytesIO(response.content))

        return response_200
    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Response[Union[File, HTTPValidationError]]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
    body: SpeechRequest,
) -> Response[Union[File, HTTPValidationError]]:
    """Speech

    Args:
        body (SpeechRequest): OpenAI-compatible POST body for ``/v1/audio/speech``.

            ``instructions`` controls speech style for backends that support it
            (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
            works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
            Ignored by Kokoro, which has no instruction-prompt input.
            ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[File, HTTPValidationError]]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: Union[AuthenticatedClient, Client],
    body: SpeechRequest,
) -> Optional[Union[File, HTTPValidationError]]:
    """Speech

    Args:
        body (SpeechRequest): OpenAI-compatible POST body for ``/v1/audio/speech``.

            ``instructions`` controls speech style for backends that support it
            (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
            works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
            Ignored by Kokoro, which has no instruction-prompt input.
            ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[File, HTTPValidationError]
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
    body: SpeechRequest,
) -> Response[Union[File, HTTPValidationError]]:
    """Speech

    Args:
        body (SpeechRequest): OpenAI-compatible POST body for ``/v1/audio/speech``.

            ``instructions`` controls speech style for backends that support it
            (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
            works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
            Ignored by Kokoro, which has no instruction-prompt input.
            ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Union[File, HTTPValidationError]]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: Union[AuthenticatedClient, Client],
    body: SpeechRequest,
) -> Optional[Union[File, HTTPValidationError]]:
    """Speech

    Args:
        body (SpeechRequest): OpenAI-compatible POST body for ``/v1/audio/speech``.

            ``instructions`` controls speech style for backends that support it
            (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
            works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
            Ignored by Kokoro, which has no instruction-prompt input.
            ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Union[File, HTTPValidationError]
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
