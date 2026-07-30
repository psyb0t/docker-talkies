#!/bin/bash
# TTS integration tests — both kokoro slugs (PyTorch + ONNX/ORT) via
# /v1/audio/speech, with a fast ASR for the cross-modality round-trip.
# Self-contained: spawns its own --rm --gpus all container via the harness,
# tears it down on exit. Invoke directly: bash tests/integration/test_speech.sh

set -eo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness.sh
source "${_DIR}/harness.sh"
# shellcheck source=common.sh
source "${_DIR}/common.sh"

SPEECH_MODELS="whisper-large-v3-turbo,kokoro-82m,kokoro-82m-nvidia"
harness_start "$SPEECH_MODELS"

TTS_MODEL="kokoro-82m"
TTS_MODEL_NVIDIA="kokoro-82m-nvidia"

# Round-trip phrase: short, common English words. Stays within Kokoro's
# typical synthesis envelope so ASR-side assertions aren't flaky.
TTS_TEST_PHRASE="The quick brown fox jumps over the lazy dog."
TTS_EXPECTED_WORDS=(quick brown fox jumps lazy dog)

# ── shared helpers ───────────────────────────────────────────────────────────

_speech_voices_list_for_slug() {
	local slug="$1"
	local out count default_count
	out=$(talkies_get "/v1/audio/voices") || {
		echo "  FAIL: /v1/audio/voices unreachable"
		return 1
	}
	count=$(echo "$out" |
		jq --arg m "$slug" '[.voices[] | select(.model==$m)] | length' \
			2>/dev/null || echo 0)
	[ "$count" -ge 1 ] || {
		echo "  FAIL: 0 voices for $slug"
		return 1
	}
	default_count=$(echo "$out" |
		jq --arg m "$slug" '[.voices[] | select(.model==$m and .default==true)] | length' \
			2>/dev/null || echo 0)
	[ "$default_count" -ge 1 ] || {
		echo "  FAIL: $slug has no default voice"
		return 1
	}
	echo "  ok: $slug voices=$count default=$default_count"
	return 0
}

_speech_all_formats_for_slug() {
	local slug="$1"
	local rc=0 fmt outfile size head4 head2
	local tmp
	tmp=$(mktemp -d -t talkies_speech.XXXXXX) || return 2
	# shellcheck disable=SC2064
	trap "rm -rf '$tmp'" RETURN

	for fmt in mp3 opus aac flac wav pcm; do
		outfile="${tmp}/out.${fmt}"
		if ! talkies_speech "$slug" "" "Hello world." "$fmt" "$outfile"; then
			echo "  FAIL: $slug $fmt synthesis"
			rc=1
			continue
		fi
		size=$(stat -c %s "$outfile" 2>/dev/null || stat -f %z "$outfile" 2>/dev/null || echo 0)
		if [ "$size" -lt 1024 ]; then
			echo "  FAIL: $slug $fmt suspiciously small ($size bytes)"
			rc=1
			continue
		fi
		head4=$(head -c 4 "$outfile" | od -An -c | tr -d ' \n')
		head2=$(head -c 2 "$outfile" | od -An -tx1 | tr -d ' \n')
		case "$fmt" in
		wav)
			[ "$head4" = "RIFF" ] ||
				{
					echo "  FAIL: $slug wav missing RIFF (got '$head4')"
					rc=1
					continue
				}
			;;
		flac)
			[ "$head4" = "fLaC" ] ||
				{
					echo "  FAIL: $slug flac missing fLaC (got '$head4')"
					rc=1
					continue
				}
			;;
		opus)
			[ "$head4" = "OggS" ] ||
				{
					echo "  FAIL: $slug opus missing OggS (got '$head4')"
					rc=1
					continue
				}
			;;
		aac)
			# ADTS sync: first 12 bits = 0xFFF.
			if [ "${head2:0:2}" != "ff" ] || [ "${head2:2:1}" != "f" ]; then
				echo "  FAIL: $slug aac missing ADTS (got '$head2')"
				rc=1
				continue
			fi
			;;
		mp3)
			# ID3v2 ("ID3") OR MPEG frame sync (0xFFFB / 0xFFFA / 0xFFF3 / 0xFFF2).
			if [ "${head4:0:3}" != "ID3" ] && [ "${head2:0:2}" != "ff" ]; then
				echo "  FAIL: $slug mp3 missing ID3/frame-sync (got '$head4'/'$head2')"
				rc=1
				continue
			fi
			;;
		pcm) : ;; # raw s16le, only size matters
		esac
		echo "  ok: $slug $fmt size=${size}B head4='$head4'"
	done
	return $rc
}

_speech_default_voice_for_slug() {
	local slug="$1"
	local outfile size
	outfile=$(mktemp -t talkies_default.XXXXXX) || return 2
	# shellcheck disable=SC2064
	trap "rm -f '$outfile'" RETURN
	if ! talkies_speech "$slug" "" "Default voice test." "wav" "$outfile"; then
		echo "  FAIL: $slug synthesis with omitted voice"
		return 1
	fi
	size=$(stat -c %s "$outfile" 2>/dev/null || stat -f %z "$outfile" 2>/dev/null || echo 0)
	[ "$size" -ge 1024 ] ||
		{
			echo "  FAIL: $slug default-voice wav too small ($size bytes)"
			return 1
		}
	echo "  ok: $slug default-voice wav size=${size}B"
	return 0
}

_speech_unknown_voice_for_slug() {
	local slug="$1"
	local body code
	body=$(jq -n --arg m "$slug" \
		'{model:$m, voice:"this_voice_does_not_exist", input:"hi", response_format:"wav"}')
	code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
		-H "Content-Type: application/json" -d "$body" \
		"${TALKIES_BASE_URL}/v1/audio/speech")
	assert_eq "$code" "400" "$slug unknown voice → 400" || return 1
	return 0
}

_speech_empty_input_for_slug() {
	local slug="$1"
	local body code
	body=$(jq -n --arg m "$slug" '{model:$m, input:"   ", response_format:"wav"}')
	code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
		-H "Content-Type: application/json" -d "$body" \
		"${TALKIES_BASE_URL}/v1/audio/speech")
	assert_eq "$code" "400" "$slug empty input → 400" || return 1
	return 0
}

# The intelligibility check — synthesize a known phrase, transcribe back via
# the fastest ASR available, assert every high-signal word from
# TTS_EXPECTED_WORDS is in the transcript. Bytes-out + non-zero duration are
# NOT the same as "the audio is the words we asked for".
_speech_round_trip_for_slug() {
	local slug="$1"
	local asr_model
	asr_model=$(talkies_pick_fast_asr_model) || {
		echo "  SKIP: no fast ASR available on server"
		return 0
	}
	echo "  using tts=$slug asr=$asr_model"

	local tmp wavfile
	tmp=$(mktemp -d -t talkies_rt.XXXXXX) || return 2
	# shellcheck disable=SC2064
	trap "rm -rf '$tmp'" RETURN
	wavfile="${tmp}/spoken.wav"

	# Cold-load + evict path.
	talkies_method POST "/unload" >/dev/null 2>&1 || true

	if ! talkies_speech "$slug" "af_heart" "$TTS_TEST_PHRASE" "wav" "$wavfile"; then
		echo "  FAIL: $slug synthesis"
		return 1
	fi
	local size
	size=$(stat -c %s "$wavfile" 2>/dev/null || stat -f %z "$wavfile" 2>/dev/null || echo 0)
	[ "$size" -ge 4096 ] || {
		echo "  FAIL: $slug wav too small ($size bytes)"
		return 1
	}
	echo "  $slug produced wav ($size bytes)"

	local json_out verbose_out text normalized
	json_out=$(talkies_transcribe "$asr_model" "$wavfile" "json") || {
		echo "  FAIL: ASR transcribe via $asr_model"
		return 1
	}
	if ! echo "$json_out" | jq -e '
        type == "object"
        and ((keys | sort) == ["text"])
        and (.text | type == "string" and length > 0)
    ' >/dev/null; then
		echo "  FAIL: $asr_model json response is not the OpenAI text envelope"
		echo "  body: $(echo "$json_out" | jq -c . 2>/dev/null || printf '%s' "$json_out")"
		return 1
	fi
	text=$(echo "$json_out" | jq -r '.text')
	if [ -z "$text" ] || [ "$text" = "null" ]; then
		echo "  FAIL: ASR returned empty text"
		return 1
	fi

	verbose_out=$(talkies_transcribe "$asr_model" "$wavfile" "verbose_json" \
		"timestamp_granularities[]=segment" \
		"timestamp_granularities[]=word") || {
		echo "  FAIL: verbose ASR transcribe via $asr_model"
		return 1
	}
	if ! echo "$verbose_out" | jq -e '
        type == "object"
        and ((keys | sort) == ["duration", "language", "segments", "task", "text", "words"])
        and (.task == "transcribe")
        and (.language | type == "string" and length > 0)
        and (.duration | type == "number" and . >= 0)
        and (.text | type == "string" and length > 0)
        and (.segments | type == "array")
        and (.words | type == "array")
        and all(
            .segments[];
            type == "object"
            and ((keys | sort) == ["avg_logprob", "compression_ratio", "end", "id", "no_speech_prob", "seek", "start", "temperature", "text", "tokens"])
            and (.id | type == "number")
            and (.seek | type == "number")
            and ((.start | type) == "number" and .start >= 0)
            and ((.end | type) == "number" and .end >= .start)
            and (.text | type == "string")
            and (.tokens | type == "array")
            and (.temperature | type == "number")
            and ((.avg_logprob == null) or (.avg_logprob | type == "number"))
            and ((.compression_ratio == null) or (.compression_ratio | type == "number"))
            and ((.no_speech_prob == null) or (.no_speech_prob | type == "number"))
        )
        and all(
            .words[];
            type == "object"
            and (.word | type == "string")
            and ((.start | type) == "number" and .start >= 0)
            and ((.end | type) == "number" and .end >= .start)
        )
    ' >/dev/null; then
		echo "  FAIL: $asr_model verbose_json response does not match the documented contract"
		echo "  body: $(echo "$verbose_out" | jq -c . 2>/dev/null || printf '%s' "$verbose_out")"
		return 1
	fi
	if [ "$text" != "$(echo "$verbose_out" | jq -r '.text')" ]; then
		echo "  FAIL: json and verbose_json returned different transcript text"
		return 1
	fi
	normalized=$(echo "$text" | talkies_normalize_text)
	echo "  transcribed: \"$normalized\""

	local missing=() word
	for word in "${TTS_EXPECTED_WORDS[@]}"; do
		[[ " $normalized " == *" $word "* ]] || missing+=("$word")
	done
	if [ "${#missing[@]}" -gt 0 ]; then
		echo "  FAIL: $slug missing words: ${missing[*]}"
		echo "  spoken: \"$TTS_TEST_PHRASE\""
		echo "  raw:    \"$text\""
		return 1
	fi
	echo "  ok: all expected words present (${TTS_EXPECTED_WORDS[*]})"
	return 0
}

# ── per-slug wrappers (stable test IDs for CI logs) ─────────────────────────

# kokoro-82m (PyTorch)
test_talkies_speech_voices_list() { _speech_voices_list_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_all_formats() { _speech_all_formats_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_default_voice() { _speech_default_voice_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_unknown_voice_400() { _speech_unknown_voice_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_empty_input_400() { _speech_empty_input_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_round_trip_through_asr() { _speech_round_trip_for_slug "$TTS_MODEL" && echo "OK: ${FUNCNAME[0]}"; }

# kokoro-82m-nvidia (ONNX/ORT)
test_talkies_speech_nvidia_voices_list() { _speech_voices_list_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_nvidia_all_formats() { _speech_all_formats_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_nvidia_default_voice() { _speech_default_voice_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_nvidia_unknown_voice_400() { _speech_unknown_voice_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_nvidia_empty_input_400() { _speech_empty_input_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }
test_talkies_speech_round_trip_through_asr_nvidia() { _speech_round_trip_for_slug "$TTS_MODEL_NVIDIA" && echo "OK: ${FUNCNAME[0]}"; }

# Cross-engine: same kernel serves both back-to-back without OOM / stuck
# resident state. Implicitly verifies the registry resolves both backends
# with the same lifecycle contract.
test_talkies_speech_kokoro_slugs_serve_back_to_back() {
	local tmp first second
	tmp=$(mktemp -d -t talkies_b2b.XXXXXX) || return 2
	# shellcheck disable=SC2064
	trap "rm -rf '$tmp'" RETURN
	first="${tmp}/a.wav"
	second="${tmp}/b.wav"

	talkies_method POST "/unload" >/dev/null 2>&1 || true
	talkies_speech "$TTS_MODEL" "af_heart" "Hello from pytorch." "wav" "$first" || {
		echo "  FAIL: $TTS_MODEL synth"
		return 1
	}
	talkies_speech "$TTS_MODEL_NVIDIA" "af_heart" "Hello from onnx." "wav" "$second" || {
		echo "  FAIL: $TTS_MODEL_NVIDIA synth after eviction"
		return 1
	}

	local a_size b_size
	a_size=$(stat -c %s "$first" 2>/dev/null || stat -f %z "$first" 2>/dev/null || echo 0)
	b_size=$(stat -c %s "$second" 2>/dev/null || stat -f %z "$second" 2>/dev/null || echo 0)
	if [ "$a_size" -lt 4096 ] || [ "$b_size" -lt 4096 ]; then
		echo "  FAIL: back-to-back wavs too small (a=$a_size b=$b_size)"
		return 1
	fi
	echo "  ok: pytorch -> onnx round-trip survived eviction (a=${a_size}B b=${b_size}B)"
	echo "OK: ${FUNCNAME[0]}"
}

test_talkies_speech_rejects_asr_model() {
	local asr_model body code
	asr_model=$(talkies_pick_fast_asr_model) || {
		echo "  SKIP: no ASR on server"
		return 0
	}
	body=$(jq -n --arg m "$asr_model" '{model:$m, input:"hi", response_format:"wav"}')
	code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
		-H "Content-Type: application/json" -d "$body" \
		"${TALKIES_BASE_URL}/v1/audio/speech")
	assert_eq "$code" "400" "ASR slug on /v1/audio/speech → 400" || return 1
	echo "OK: ${FUNCNAME[0]}"
}

test_talkies_transcribe_rejects_tts_model() {
	local fixture
	fixture=$(talkies_find_fixture)
	[ -n "$fixture" ] || {
		echo "  SKIP: no fixture audio"
		return 0
	}
	local code
	code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 \
		-F "model=$TTS_MODEL" \
		-F "file=@${fixture}" \
		"${TALKIES_BASE_URL}/v1/audio/transcriptions")
	assert_eq "$code" "400" "TTS slug ($TTS_MODEL) on /v1/audio/transcriptions → 400" || return 1
	echo "OK: ${FUNCNAME[0]}"
}

harness_run_tests \
	test_talkies_speech_voices_list \
	test_talkies_speech_all_formats \
	test_talkies_speech_default_voice \
	test_talkies_speech_unknown_voice_400 \
	test_talkies_speech_empty_input_400 \
	test_talkies_speech_round_trip_through_asr \
	test_talkies_speech_nvidia_voices_list \
	test_talkies_speech_nvidia_all_formats \
	test_talkies_speech_nvidia_default_voice \
	test_talkies_speech_nvidia_unknown_voice_400 \
	test_talkies_speech_nvidia_empty_input_400 \
	test_talkies_speech_round_trip_through_asr_nvidia \
	test_talkies_speech_kokoro_slugs_serve_back_to_back \
	test_talkies_speech_rejects_asr_model \
	test_talkies_transcribe_rejects_tts_model
