from http import HTTPStatus
from typing import Any, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.list_models_v1_models_get_response_list_models_v1_models_get import (
    ListModelsV1ModelsGetResponseListModelsV1ModelsGet,
)
from ...types import Response


def _get_kwargs() -> dict[str, Any]:
    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/models",
    }

    return _kwargs


def _parse_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Optional[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    if response.status_code == 200:
        response_200 = ListModelsV1ModelsGetResponseListModelsV1ModelsGet.from_dict(response.json())

        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Response[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    """List available models

     Return model IDs, capabilities and concurrency limits. Select a transcription or speech model by its
    ID in the corresponding request.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    """List available models

     Return model IDs, capabilities and concurrency limits. Select a transcription or speech model by its
    ID in the corresponding request.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ListModelsV1ModelsGetResponseListModelsV1ModelsGet
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    """List available models

     Return model IDs, capabilities and concurrency limits. Select a transcription or speech model by its
    ID in the corresponding request.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[ListModelsV1ModelsGetResponseListModelsV1ModelsGet]:
    """List available models

     Return model IDs, capabilities and concurrency limits. Select a transcription or speech model by its
    ID in the corresponding request.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ListModelsV1ModelsGetResponseListModelsV1ModelsGet
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
