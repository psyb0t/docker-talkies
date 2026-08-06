#!/bin/bash
# End-to-end coverage for the parakeet_cpp / nemotron-3.5-asr-0.6b slug —
# multilingual ASR via parakeet.cpp + ggml. Self-contained: spawns its own
# --rm --gpus all container via the harness, tears it down on exit. The
# model runs through the CUDA-enabled precompiled libparakeet.so bundle and
# therefore requires the CUDA host harness.
#
# Invoke directly. Per-test filter via positional args:
#
#     bash tests/integration/e2e_nemotron_asr.sh
#     bash tests/integration/e2e_nemotron_asr.sh test_nemotron_fixture_round_trip
#     bash tests/integration/e2e_nemotron_asr.sh round_trip      # substring match
#
# Exercises:
#   - Slug shows up in /v1/models with modality=asr (default).
#   - Fixture audio.mp3 transcribes to the canonical text (audio.mp3.txt).
#   - Verbose-json returns per-word timestamps (with_timestamps path).
#   - Explicit `language=en` request path works (lang-aware C-API entry point).
#   - Unknown locale returns 4xx (no silent fallback).

set -eo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness.sh
source "${_DIR}/harness.sh"
# shellcheck source=common.sh
source "${_DIR}/common.sh"

NEMOTRON_MODELS="nemotron-3.5-asr-0.6b"
harness_start "$NEMOTRON_MODELS"

ASR_SLUG="nemotron-3.5-asr-0.6b"
FIXTURE_MP3="${_HARNESS_REPO_ROOT}/tests/integration/.fixtures/audio.mp3"
FIXTURE_TXT="${_HARNESS_REPO_ROOT}/tests/integration/.fixtures/audio.mp3.txt"

if [ ! -f "$FIXTURE_MP3" ] || [ ! -f "$FIXTURE_TXT" ]; then
	echo "FATAL: fixture(s) missing — needs $FIXTURE_MP3 + $FIXTURE_TXT" >&2
	exit 2
fi

EXPECTED_TEXT=$(tr -d '\r\n' <"$FIXTURE_TXT")
# Pull a content-word list out of the canonical phrase so we can substring-
# match the ASR output without caring about punctuation / casing variance.
read -ra EXPECTED_WORDS <<<"$(echo "$EXPECTED_TEXT" | talkies_normalize_text)"

# ── /v1/models exposes nemotron with modality=asr ────────────────────────────

test_nemotron_listed_in_models() {
	local out modality
	out=$(talkies_get "/v1/models") || {
		echo "  FAIL: /v1/models"
		return 1
	}
	modality=$(echo "$out" | jq -r --arg m "$ASR_SLUG" \
		'.data[] | select(.id==$m) | .modality // "asr"')
	if [ -z "$modality" ]; then
		echo "  FAIL: $ASR_SLUG not in /v1/models"
		echo "  got: $(echo "$out" | jq -c '[.data[].id]')"
		return 1
	fi
	if [ "$modality" != "asr" ]; then
		echo "  FAIL: $ASR_SLUG modality='$modality' (expected asr)"
		return 1
	fi
	echo "  ok: $ASR_SLUG listed, modality=asr"
	echo "OK: ${FUNCNAME[0]}"
}

# ── Fixture audio transcribes back to the canonical text ─────────────────────

test_nemotron_fixture_round_trip() {
	local out text normalized missing=() word
	out=$(talkies_transcribe "$ASR_SLUG" "$FIXTURE_MP3" "json") || {
		echo "  FAIL: transcribe HTTP error"
		return 1
	}
	text=$(echo "$out" | jq -r '.text' 2>/dev/null)
	if [ -z "$text" ] || [ "$text" = "null" ]; then
		echo "  FAIL: empty transcript"
		return 1
	fi
	normalized=$(echo "$text" | talkies_normalize_text)
	echo "  transcribed: \"$normalized\""
	echo "  expected   : \"$(echo "$EXPECTED_TEXT" | talkies_normalize_text)\""
	for word in "${EXPECTED_WORDS[@]}"; do
		if [[ " $normalized " != *" $word "* ]]; then
			missing+=("$word")
		fi
	done
	if [ "${#missing[@]}" -gt 0 ]; then
		echo "  FAIL: missing expected words: ${missing[*]}"
		return 1
	fi
	# Backend must strip nemotron's trailing <xx-yy> language token before
	# surfacing — leaving it in breaks downstream ASR-round-trip assertions.
	if [[ "$text" == *"<"*">"* ]]; then
		echo "  FAIL: language token leaked into transcript: \"$text\""
		return 1
	fi
	echo "  ok: all expected words present, no leaked lang tag"
	echo "OK: ${FUNCNAME[0]}"
}

# ── verbose_json carries per-word timestamps ─────────────────────────────────

test_nemotron_verbose_json_words() {
	local out word_count first_word seg_count first_seg
	out=$(talkies_transcribe "$ASR_SLUG" "$FIXTURE_MP3" "verbose_json" \
		"timestamp_granularities[]=segment" \
		"timestamp_granularities[]=word") || {
		echo "  FAIL: verbose_json HTTP error"
		return 1
	}
	word_count=$(echo "$out" | jq '.words // [] | length' 2>/dev/null || echo 0)
	if [ "$word_count" -lt 1 ]; then
		echo "  FAIL: no words in verbose_json (count=$word_count)"
		echo "  body: $(echo "$out" | jq -c . | head -c 400)"
		return 1
	fi
	first_word=$(echo "$out" | jq -c '.words[0]')
	local start end
	start=$(echo "$first_word" | jq -r '.start')
	end=$(echo "$first_word" | jq -r '.end')
	if [ -z "$start" ] || [ -z "$end" ] || [ "$start" = "null" ] || [ "$end" = "null" ]; then
		echo "  FAIL: first word missing start/end ($first_word)"
		return 1
	fi
	# OpenAI verbose_json requires a segments array. parakeet.cpp's C-API
	# doesn't surface segments natively, so the backend synthesizes them
	# from the word list (gap-grouped). One short fixture phrase = >=1 segment.
	seg_count=$(echo "$out" | jq '.segments // [] | length' 2>/dev/null || echo 0)
	if [ "$seg_count" -lt 1 ]; then
		echo "  FAIL: no segments in verbose_json (count=$seg_count) — backend should synthesize"
		echo "  body: $(echo "$out" | jq -c . | head -c 400)"
		return 1
	fi
	first_seg=$(echo "$out" | jq -c '.segments[0]')
	local seg_id seg_text
	seg_id=$(echo "$first_seg" | jq -r '.id')
	seg_text=$(echo "$first_seg" | jq -r '.text')
	if [ "$seg_id" != "0" ] || [ -z "$seg_text" ] || [ "$seg_text" = "null" ]; then
		echo "  FAIL: segment[0] malformed ($first_seg)"
		return 1
	fi
	echo "  ok: $word_count word(s) + $seg_count segment(s); seg[0]=\"$seg_text\""
	echo "OK: ${FUNCNAME[0]}"
}

# ── Explicit language=en hits the lang-aware C-API code path ─────────────────

test_nemotron_explicit_language() {
	local out text normalized
	out=$(talkies_transcribe "$ASR_SLUG" "$FIXTURE_MP3" "json" "language=en") || {
		echo "  FAIL: language=en transcribe HTTP error"
		return 1
	}
	text=$(echo "$out" | jq -r '.text')
	if [ -z "$text" ] || [ "$text" = "null" ]; then
		echo "  FAIL: empty transcript with language=en"
		return 1
	fi
	normalized=$(echo "$text" | talkies_normalize_text)
	if [[ " $normalized " != *" code "* ]] && [[ " $normalized " != *" line "* ]]; then
		echo "  FAIL: language=en transcript missing expected substrings"
		echo "  got: \"$normalized\""
		return 1
	fi
	echo "  ok: language=en transcript: \"$normalized\""
	echo "OK: ${FUNCNAME[0]}"
}

# ── Unknown locale → 4xx (no silent fallback to auto) ────────────────────────

test_nemotron_unknown_language_4xx() {
	local code body tmp
	tmp=$(mktemp -t nem_lang_err.XXXXXX) || return 2
	# shellcheck disable=SC2064
	trap "rm -f '$tmp'" RETURN
	code=$(curl -s -o "$tmp" -w "%{http_code}" --max-time 120 \
		-F "model=$ASR_SLUG" \
		-F "response_format=json" \
		-F "language=xx-nonexistent" \
		-F "file=@${FIXTURE_MP3}" \
		"${TALKIES_BASE_URL}/v1/audio/transcriptions")
	# Server may surface as 400 (validation) or 500 (backend ValueError) — both
	# are acceptable; what we're guarding is "never silently succeed with auto".
	if [ "$code" = "200" ]; then
		body=$(head -c 400 "$tmp")
		echo "  FAIL: unknown lang returned 200 (expected 4xx/5xx). body: $body"
		return 1
	fi
	echo "  ok: unknown language → HTTP $code"
	echo "OK: ${FUNCNAME[0]}"
}

harness_run_tests \
	test_nemotron_listed_in_models \
	test_nemotron_fixture_round_trip \
	test_nemotron_verbose_json_words \
	test_nemotron_explicit_language \
	test_nemotron_unknown_language_4xx
