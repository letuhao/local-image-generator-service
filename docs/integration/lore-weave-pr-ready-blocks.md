# LoreWeave Guide PR Ready Blocks

Use the following sections as direct paste blocks for LoreWeave's external integration guide.

## Block 1: Async invocation contract

```md
### Async image generation contract

Set `mode=async` in `POST /v1/images/generations` to receive immediate acknowledgment:

- Response status: `202 Accepted`
- Response body: `{ "id": "<job_id>", "status": "processing" }`
- Response header: `X-Job-Id: <job_id>`

Poll terminal state from:

- `GET /v1/images/generations/{job_id}`

Terminal states:

- `completed`: response includes `data[]` URLs (or `b64_json` when requested).
- `failed` / `abandoned`: response includes an `error` object.
```

## Block 2: Optional webhook request field

```md
### Optional webhook callback

Clients may include:

```json
"webhook": {
  "url": "https://<receiver-host>/v1/webhooks/image-gen",
  "headers": {
    "X-Tenant": "example-tenant"
  }
}
```

Validation highlights:

- In prod mode, webhook URL must be `https`.
- Host must match service allowlist.
- Reserved headers cannot be overridden:
  - `Host`
  - `Authorization`
  - `Content-Type`
  - `User-Agent`
  - Any `X-ImageGen-*` header
```

## Block 3: Receiver verification contract

```md
### Receiver verification requirements

Webhook receiver must:

1. Read raw request body bytes.
2. Parse `X-ImageGen-Signature` in format:
   - `t=<unix_ts>,v1=<hex_sha256>`
3. Verify HMAC-SHA256 over:
   - `"<t>.<raw_body_bytes>"`
4. Enforce replay guard:
   - reject when `|now - t| > 300s`
5. Compare digests with constant-time function.
6. Dedupe by `X-ImageGen-Job-Id` in durable storage.
7. Persist terminal result idempotently.
8. Return 2xx quickly after persistence.

Delivery is at-least-once; duplicates are expected and must be safe.
```

## Block 4: Recommended LoreWeave endpoint

```md
### Suggested route

Implement:

- `POST /v1/webhooks/image-gen`

Authentication model for this endpoint should be signature-based (no bearer requirement from sender). The endpoint must be idempotent and safe under retries.
```

## Block 5: Test plan checklist

```md
### Integration checklist

- [ ] Async submit returns 202 + job id.
- [ ] Poll endpoint converges to terminal.
- [ ] Signed webhook delivery is accepted by receiver.
- [ ] Receiver rejects stale timestamp payloads.
- [ ] Receiver rejects invalid signatures.
- [ ] Duplicate delivery for same `X-ImageGen-Job-Id` is deduped.
- [ ] LoreWeave marks generation terminal from webhook without double-writing side effects.
```

## Notes for reviewers

- This contract intentionally requires durable dedupe because sender retries and network ambiguity can produce duplicates.
- Verification must run before JSON parsing to avoid "accept-then-fail-verify" ordering bugs.
