# LoreWeave Receiver Reference

This document is the copy-paste receiver contract for LoreWeave to consume webhook deliveries from `local-image-generator-service`.

It is derived from `docs/architecture/image-gen-service.md` webhook contract sections.

## Delivery model

- Terminal events only: `job.completed` and `job.failed`.
- At-least-once delivery semantics: retries can occur and duplicate deliveries are expected.
- Receiver must implement durable dedupe keyed by `X-ImageGen-Job-Id`.

## Required request headers

- `X-ImageGen-Event`: event name (`job.completed` or `job.failed`)
- `X-ImageGen-Job-Id`: stable job id (for dedupe)
- `X-ImageGen-Delivery-Id`: unique attempt id
- `X-ImageGen-Attempt`: 1-based attempt counter
- `X-ImageGen-Timestamp`: unix timestamp seconds
- `X-ImageGen-Signature`: `t=<unix_ts>,v1=<hex_sha256>`
- `Content-Type`: `application/json`

The receiver must verify the signature over raw request body bytes before JSON parsing.

## Signature verification contract

- Signing input: `timestamp + "." + raw_body_bytes`
- Algorithm: HMAC-SHA256
- Header format: `X-ImageGen-Signature: t=<unix_ts>,v1=<hex>`
- Replay guard: reject if `abs(now - ts) > 300` seconds
- Compare digests with constant-time equality
- Secret rotation: receiver tries all active secrets; success if any matches

## Go reference snippet

```go
package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

type payload struct {
	Event string `json:"event"`
	Job   struct {
		ID string `json:"id"`
	} `json:"job"`
}

func parseSig(sigHeader string) (int64, []byte, error) {
	parts := strings.Split(sigHeader, ",")
	if len(parts) != 2 {
		return 0, nil, errors.New("invalid signature header parts")
	}
	var ts int64
	var hexSig string
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if strings.HasPrefix(p, "t=") {
			v := strings.TrimPrefix(p, "t=")
			n, err := strconv.ParseInt(v, 10, 64)
			if err != nil {
				return 0, nil, errors.New("invalid t value")
			}
			ts = n
		} else if strings.HasPrefix(p, "v1=") {
			hexSig = strings.TrimPrefix(p, "v1=")
		}
	}
	if ts == 0 || hexSig == "" {
		return 0, nil, errors.New("missing t or v1")
	}
	b, err := hex.DecodeString(hexSig)
	if err != nil {
		return 0, nil, errors.New("invalid hex signature")
	}
	return ts, b, nil
}

func VerifyAndHandle(w http.ResponseWriter, r *http.Request, secrets []string) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body", http.StatusBadRequest)
		return
	}

	sigHeader := r.Header.Get("X-ImageGen-Signature")
	ts, received, err := parseSig(sigHeader)
	if err != nil {
		http.Error(w, "bad signature header", http.StatusBadRequest)
		return
	}

	now := time.Now().Unix()
	if d := now - ts; d > 300 || d < -300 {
		http.Error(w, "stale timestamp", http.StatusUnauthorized)
		return
	}

	msg := append([]byte(strconv.FormatInt(ts, 10)+"."), body...)
	valid := false
	for _, secret := range secrets {
		mac := hmac.New(sha256.New, []byte(secret))
		mac.Write(msg)
		if hmac.Equal(mac.Sum(nil), received) {
			valid = true
			break
		}
	}
	if !valid {
		http.Error(w, "invalid signature", http.StatusUnauthorized)
		return
	}

	var p payload
	if err := json.Unmarshal(body, &p); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}

	// Durable dedupe must happen before applying side effects.
	// Use X-ImageGen-Job-Id for idempotency key in persistent storage.

	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok":true}`))
}
```

## Receiver checklist

- Verify signature over raw bytes before parse.
- Enforce timestamp skew window.
- Use constant-time digest comparison.
- Dedupe by `X-ImageGen-Job-Id` in durable storage.
- Commit terminal state idempotently.
- Return 2xx quickly (target under 10s) after persistence.
