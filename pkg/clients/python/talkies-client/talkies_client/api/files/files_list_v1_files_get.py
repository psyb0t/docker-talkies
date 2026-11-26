from http import HTTPStatus
from typing import Any, Optional, Union

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.files_list_v1_files_get_response_files_list_v1_files_get import (
    FilesListV1FilesGetResponseFilesListV1FilesGet,
)
from ...types import Response


def _get_kwargs() -> dict[str, Any]:
    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v1/files",
    }

    return _kwargs


def _parse_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Optional[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    if response.status_code == 200:
        response_200 = FilesListV1FilesGetResponseFilesListV1FilesGet.from_dict(response.json())

        return response_200
    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: Union[AuthenticatedClient, Client], response: httpx.Response
) -> Response[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    """List staged files

     Return the relative paths and sizes of files in the staging area.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FilesListV1FilesGetResponseFilesListV1FilesGet]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    """List staged files

     Return the relative paths and sizes of files in the staging area.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FilesListV1FilesGetResponseFilesListV1FilesGet
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Response[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    """List staged files

     Return the relative paths and sizes of files in the staging area.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[FilesListV1FilesGetResponseFilesListV1FilesGet]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: Union[AuthenticatedClient, Client],
) -> Optional[FilesListV1FilesGetResponseFilesListV1FilesGet]:
    """List staged files

     Return the relative paths and sizes of files in the staging area.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        FilesListV1FilesGetResponseFilesListV1FilesGet
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
