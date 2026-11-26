from collections.abc import Mapping
from typing import Any, TypeVar, Union, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from .. import types
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyTranscribeV1AudioTranscriptionsPost")


@_attrs_define
class BodyTranscribeV1AudioTranscriptionsPost:
    """
    Attributes:
        model (str): ID of a configured transcription model.
        file (Union[None, Unset, str]): Audio to upload. Omit when using file_path.
        file_path (Union[None, Unset, str]): Staged path or URL. Omit with a file upload.
        language (Union[None, Unset, str]): Language hint for models that support it.
        response_format (Union[Unset, str]): Transcript format such as json, text, srt or vtt. Default: 'json'.
        prompt (Union[None, Unset, str]): Accepted for OpenAI compatibility and ignored.
        temperature (Union[None, Unset, float]): Accepted for OpenAI compatibility and ignored.
        timestamp_granularities (Union[Unset, list[str]]): OpenAI timestamp option. Verbose JSON returns both levels.
        diarization (Union[None, Unset, str]): Set true to transcribe stereo channels separately.
    """

    model: str
    file: Union[None, Unset, str] = UNSET
    file_path: Union[None, Unset, str] = UNSET
    language: Union[None, Unset, str] = UNSET
    response_format: Union[Unset, str] = "json"
    prompt: Union[None, Unset, str] = UNSET
    temperature: Union[None, Unset, float] = UNSET
    timestamp_granularities: Union[Unset, list[str]] = UNSET
    diarization: Union[None, Unset, str] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model = self.model

        file: Union[None, Unset, str]
        if isinstance(self.file, Unset):
            file = UNSET
        else:
            file = self.file

        file_path: Union[None, Unset, str]
        if isinstance(self.file_path, Unset):
            file_path = UNSET
        else:
            file_path = self.file_path

        language: Union[None, Unset, str]
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        response_format = self.response_format

        prompt: Union[None, Unset, str]
        if isinstance(self.prompt, Unset):
            prompt = UNSET
        else:
            prompt = self.prompt

        temperature: Union[None, Unset, float]
        if isinstance(self.temperature, Unset):
            temperature = UNSET
        else:
            temperature = self.temperature

        timestamp_granularities: Union[Unset, list[str]] = UNSET
        if not isinstance(self.timestamp_granularities, Unset):
            timestamp_granularities = self.timestamp_granularities

        diarization: Union[None, Unset, str]
        if isinstance(self.diarization, Unset):
            diarization = UNSET
        else:
            diarization = self.diarization

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model": model,
            }
        )
        if file is not UNSET:
            field_dict["file"] = file
        if file_path is not UNSET:
            field_dict["file_path"] = file_path
        if language is not UNSET:
            field_dict["language"] = language
        if response_format is not UNSET:
            field_dict["response_format"] = response_format
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if temperature is not UNSET:
            field_dict["temperature"] = temperature
        if timestamp_granularities is not UNSET:
            field_dict["timestamp_granularities[]"] = timestamp_granularities
        if diarization is not UNSET:
            field_dict["diarization"] = diarization

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("model", (None, str(self.model).encode(), "text/plain")))

        if not isinstance(self.file, Unset):
            if isinstance(self.file, str):
                files.append(("file", (None, str(self.file).encode(), "text/plain")))
            else:
                files.append(("file", (None, str(self.file).encode(), "text/plain")))

        if not isinstance(self.file_path, Unset):
            if isinstance(self.file_path, str):
                files.append(("file_path", (None, str(self.file_path).encode(), "text/plain")))
            else:
                files.append(("file_path", (None, str(self.file_path).encode(), "text/plain")))

        if not isinstance(self.language, Unset):
            if isinstance(self.language, str):
                files.append(("language", (None, str(self.language).encode(), "text/plain")))
            else:
                files.append(("language", (None, str(self.language).encode(), "text/plain")))

        if not isinstance(self.response_format, Unset):
            files.append(("response_format", (None, str(self.response_format).encode(), "text/plain")))

        if not isinstance(self.prompt, Unset):
            if isinstance(self.prompt, str):
                files.append(("prompt", (None, str(self.prompt).encode(), "text/plain")))
            else:
                files.append(("prompt", (None, str(self.prompt).encode(), "text/plain")))

        if not isinstance(self.temperature, Unset):
            if isinstance(self.temperature, float):
                files.append(("temperature", (None, str(self.temperature).encode(), "text/plain")))
            else:
                files.append(("temperature", (None, str(self.temperature).encode(), "text/plain")))

        if not isinstance(self.timestamp_granularities, Unset):
            for timestamp_granularities_item_element in self.timestamp_granularities:
                files.append(
                    (
                        "timestamp_granularities[]",
                        (None, str(timestamp_granularities_item_element).encode(), "text/plain"),
                    )
                )

        if not isinstance(self.diarization, Unset):
            if isinstance(self.diarization, str):
                files.append(("diarization", (None, str(self.diarization).encode(), "text/plain")))
            else:
                files.append(("diarization", (None, str(self.diarization).encode(), "text/plain")))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model = d.pop("model")

        def _parse_file(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        file = _parse_file(d.pop("file", UNSET))

        def _parse_file_path(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        file_path = _parse_file_path(d.pop("file_path", UNSET))

        def _parse_language(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        language = _parse_language(d.pop("language", UNSET))

        response_format = d.pop("response_format", UNSET)

        def _parse_prompt(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        prompt = _parse_prompt(d.pop("prompt", UNSET))

        def _parse_temperature(data: object) -> Union[None, Unset, float]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, float], data)

        temperature = _parse_temperature(d.pop("temperature", UNSET))

        timestamp_granularities = cast(list[str], d.pop("timestamp_granularities[]", UNSET))

        def _parse_diarization(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        diarization = _parse_diarization(d.pop("diarization", UNSET))

        body_transcribe_v1_audio_transcriptions_post = cls(
            model=model,
            file=file,
            file_path=file_path,
            language=language,
            response_format=response_format,
            prompt=prompt,
            temperature=temperature,
            timestamp_granularities=timestamp_granularities,
            diarization=diarization,
        )

        body_transcribe_v1_audio_transcriptions_post.additional_properties = d
        return body_transcribe_v1_audio_transcriptions_post

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
