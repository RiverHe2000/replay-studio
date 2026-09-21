# Security and deployment boundaries

The default service binds to loopback. It is intended for an individual or a small trusted workspace. External operation requires TLS, secure cookies, explicit allowed origins, restricted database/object credentials, a reverse-proxy upload limit and operator-managed disk/backup retention. Model code loads without `trust_remote_code`; model output cannot select paths, execute shell commands or change project authorization.

Verified application boundaries include authentication, CSRF, same-origin writes, project isolation, viewer/editor roles, upload overlap handling, object path traversal, deleted-asset access, stale job tokens, saved-version concurrency and finite media ranges. Tests are evidence for specified cases, not a proof that every attack is impossible.

- Every media, frame, transcript, search and export route checks membership. Private S3 objects are not made public; the API materializes and streams them.
- Passwords use per-user random salts and PBKDF2-HMAC-SHA256 with 600,000 iterations. Session token hashes are stored; browser JavaScript does not receive the HttpOnly session cookie value. Login responses contain a separate CSRF token.
- A database-backed client-address rate limit runs before login KDF work. Behind a proxy, configure trusted forwarded-address handling deliberately; otherwise many users can share one limit. There is no email verification, password reset or MFA in v0.1.
- FFmpeg receives argument arrays, local inputs, restricted protocols/formats, bounded durations and timeouts. User captions never enter shell/filter code. Invalid media fails with an explicit job state. FFmpeg and model dependencies still process untrusted content: keep their packages current, use the supplied unprivileged container profile for external users, and restrict outbound network once weights are cached.
- Clip data is range-checked; automatic plans cite existing evidence, and humans can make manual edits. An AI-generated semantic interpretation is not verified ground truth. Never treat a similarity score as a factual confidence percentage.
- Upload limits and reserved project quotas cover original bytes, not total installation storage. Failed attempts, proxy files, model caches, backups and exports require capacity monitoring. Abandoned uploads can be explicitly discarded; scheduled retention is not silently enabled.
- Logical deletion revokes application access. Quiesced garbage collection removes the live installation's content; backups retain their own lifecycle. Member removal does not retract files a member already downloaded.
- Public logs and reports must exclude credentials, session headers and private source content. The scripted artifacts in this repository use synthetic footage; future user-study artifacts require separate consent and access controls.

The backup format contains account data and private media. Keep it encrypted and restrict file access. No production secrets belong in `.env` committed to source control, demo access files, browser traces or public issue reports.
