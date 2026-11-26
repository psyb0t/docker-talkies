from collections.abc import Mapping
from typing import Any, TypeVar, Union, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SpeechRequest")


@_attrs_define
class SpeechRequest:
    """OpenAI-compatible POST body for ``/v1/audio/speech``.

    ``instructions`` controls speech style for backends that support it
    (Qwen3-TTS: passed as the ``instruct`` parameter to ``generate_voice_clone``;
    works best in ICL mode i.e. when a ``.txt`` transcript exists for the voice).
    Ignored by Kokoro, which has no instruction-prompt input.
    ``speed`` clamped to 0.25-4.0 (matches OpenAI's documented range).

        Attributes:
            model (str):
            input_ (str):
            voice (Union[None, Unset, str]):
            response_format (Union[None, Unset, str]):
            speed (Union[None, Unset, float]):
            instructions (Union[None, Unset, str]):
            language (Union[None, Unset, str]):
            temperature (Union[None, Unset, float]):
            top_k (Union[None, Unset, int]):
            top_p (Union[None, Unset, float]):
            repetition_penalty (Union[None, Unset, float]):
            max_new_tokens (Union[None, Unset, int]):
            do_sample (Union[None, Unset, bool]):
    """

    model: str
    input_: str
    voice: Union[None, Unset, str] = UNSET
    response_format: Union[None, Unset, str] = UNSET
    speed: Union[None, Unset, float] = UNSET
    instructions: Union[None, Unset, str] = UNSET
    language: Union[None, Unset, str] = UNSET
    temperature: Union[None, Unset, float] = UNSET
    top_k: Union[None, Unset, int] = UNSET
    top_p: Union[None, Unset, float] = UNSET
    repetition_penalty: Union[None, Unset, float] = UNSET
    max_new_tokens: Union[None, Unset, int] = UNSET
    do_sample: Union[None, Unset, bool] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model = self.model

        input_ = self.input_

        voice: Union[None, Unset, str]
        if isinstance(self.voice, Unset):
            voice = UNSET
        else:
            voice = self.voice

        response_format: Union[None, Unset, str]
        if isinstance(self.response_format, Unset):
            response_format = UNSET
        else:
            response_format = self.response_format

        speed: Union[None, Unset, float]
        if isinstance(self.speed, Unset):
            speed = UNSET
        else:
            speed = self.speed

        instructions: Union[None, Unset, str]
        if isinstance(self.instructions, Unset):
            instructions = UNSET
        else:
            instructions = self.instructions

        language: Union[None, Unset, str]
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        temperature: Union[None, Unset, float]
        if isinstance(self.temperature, Unset):
            temperature = UNSET
        else:
            temperature = self.temperature

        top_k: Union[None, Unset, int]
        if isinstance(self.top_k, Unset):
            top_k = UNSET
        else:
            top_k = self.top_k

        top_p: Union[None, Unset, float]
        if isinstance(self.top_p, Unset):
            top_p = UNSET
        else:
            top_p = self.top_p

        repetition_penalty: Union[None, Unset, float]
        if isinstance(self.repetition_penalty, Unset):
            repetition_penalty = UNSET
        else:
            repetition_penalty = self.repetition_penalty

        max_new_tokens: Union[None, Unset, int]
        if isinstance(self.max_new_tokens, Unset):
            max_new_tokens = UNSET
        else:
            max_new_tokens = self.max_new_tokens

        do_sample: Union[None, Unset, bool]
        if isinstance(self.do_sample, Unset):
            do_sample = UNSET
        else:
            do_sample = self.do_sample

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model": model,
                "input": input_,
            }
        )
        if voice is not UNSET:
            field_dict["voice"] = voice
        if response_format is not UNSET:
            field_dict["response_format"] = response_format
        if speed is not UNSET:
            field_dict["speed"] = speed
        if instructions is not UNSET:
            field_dict["instructions"] = instructions
        if language is not UNSET:
            field_dict["language"] = language
        if temperature is not UNSET:
            field_dict["temperature"] = temperature
        if top_k is not UNSET:
            field_dict["top_k"] = top_k
        if top_p is not UNSET:
            field_dict["top_p"] = top_p
        if repetition_penalty is not UNSET:
            field_dict["repetition_penalty"] = repetition_penalty
        if max_new_tokens is not UNSET:
            field_dict["max_new_tokens"] = max_new_tokens
        if do_sample is not UNSET:
            field_dict["do_sample"] = do_sample

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model = d.pop("model")

        input_ = d.pop("input")

        def _parse_voice(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        voice = _parse_voice(d.pop("voice", UNSET))

        def _parse_response_format(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        response_format = _parse_response_format(d.pop("response_format", UNSET))

        def _parse_speed(data: object) -> Union[None, Unset, float]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, float], data)

        speed = _parse_speed(d.pop("speed", UNSET))

        def _parse_instructions(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        instructions = _parse_instructions(d.pop("instructions", UNSET))

        def _parse_language(data: object) -> Union[None, Unset, str]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, str], data)

        language = _parse_language(d.pop("language", UNSET))

        def _parse_temperature(data: object) -> Union[None, Unset, float]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, float], data)

        temperature = _parse_temperature(d.pop("temperature", UNSET))

        def _parse_top_k(data: object) -> Union[None, Unset, int]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, int], data)

        top_k = _parse_top_k(d.pop("top_k", UNSET))

        def _parse_top_p(data: object) -> Union[None, Unset, float]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, float], data)

        top_p = _parse_top_p(d.pop("top_p", UNSET))

        def _parse_repetition_penalty(data: object) -> Union[None, Unset, float]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, float], data)

        repetition_penalty = _parse_repetition_penalty(d.pop("repetition_penalty", UNSET))

        def _parse_max_new_tokens(data: object) -> Union[None, Unset, int]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, int], data)

        max_new_tokens = _parse_max_new_tokens(d.pop("max_new_tokens", UNSET))

        def _parse_do_sample(data: object) -> Union[None, Unset, bool]:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Union[None, Unset, bool], data)

        do_sample = _parse_do_sample(d.pop("do_sample", UNSET))

        speech_request = cls(
            model=model,
            input_=input_,
            voice=voice,
            response_format=response_format,
            speed=speed,
            instructions=instructions,
            language=language,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
        )

        speech_request.additional_properties = d
        return speech_request

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
