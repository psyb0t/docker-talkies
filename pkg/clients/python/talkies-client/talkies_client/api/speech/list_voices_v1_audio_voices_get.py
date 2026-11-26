from http import HTTPStatus
from typing import Any, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.list_voices_v1_audio_voices_get_response_list_voices_v1_audio_voices_get import (
    ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet,
)
from ...types import Response


def _get_kwargs() -> dict[str, Any]:
    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/audio/voices",
    }

    return _kwargs


def _parse_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Optional[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    if response.status_code == 200:
        response_200 = ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet.from_dict(response.json())

        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Response[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    r"""List Voices

     List available TTS voices across all loaded TTS models.

    Returned shape: ``{\"voices\": [{\"voice\": str, \"model\": str,
    \"default\": bool, \"origin\": str?}]}``. Pass any ``voice`` value to
    ``POST /v1/audio/speech`` together with its ``model`` slug — voices
    are not interchangeable across models (each TTS engine owns its own
    catalog).

    ``origin`` is set to ``\"builtin\"`` (shipped in the image) or
    ``\"custom\"`` (mounted via ``/data/custom-voices/``) for backends that
    expose a ``voice_origins()`` method; omitted otherwise.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    r"""List Voices

     List available TTS voices across all loaded TTS models.

    Returned shape: ``{\"voices\": [{\"voice\": str, \"model\": str,
    \"default\": bool, \"origin\": str?}]}``. Pass any ``voice`` value to
    ``POST /v1/audio/speech`` together with its ``model`` slug — voices
    are not interchangeable across models (each TTS engine owns its own
    catalog).

    ``origin`` is set to ``\"builtin\"`` (shipped in the image) or
    ``\"custom\"`` (mounted via ``/data/custom-voices/``) for backends that
    expose a ``voice_origins()`` method; omitted otherwise.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    r"""List Voices

     List available TTS voices across all loaded TTS models.

    Returned shape: ``{\"voices\": [{\"voice\": str, \"model\": str,
    \"default\": bool, \"origin\": str?}]}``. Pass any ``voice`` value to
    ``POST /v1/audio/speech`` together with its ``model`` slug — voices
    are not interchangeable across models (each TTS engine owns its own
    catalog).

    ``origin`` is set to ``\"builtin\"`` (shipped in the image) or
    ``\"custom\"`` (mounted via ``/data/custom-voices/``) for backends that
    expose a ``voice_origins()`` method; omitted otherwise.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet]:
    r"""List Voices

     List available TTS voices across all loaded TTS models.

    Returned shape: ``{\"voices\": [{\"voice\": str, \"model\": str,
    \"default\": bool, \"origin\": str?}]}``. Pass any ``voice`` value to
    ``POST /v1/audio/speech`` together with its ``model`` slug — voices
    are not interchangeable across models (each TTS engine owns its own
    catalog).

    ``origin`` is set to ``\"builtin\"`` (shipped in the image) or
    ``\"custom\"`` (mounted via ``/data/custom-voices/``) for backends that
    expose a ``voice_origins()`` method; omitted otherwise.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ListVoicesV1AudioVoicesGetResponseListVoicesV1AudioVoicesGet
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
