# bench/arena.md — arena request handler

Measured on this machine (2 cores, 3.7 GB RAM). Re-run before quoting.

Programs: `bench/arena_request.brat` (with arena) and `bench/arena_request_noarena.brat` (without).

| Metric | With arena | Without arena |
|---|---|---|
| Time (3000 requests) | 0.036 s | 0.061 s |
| 16 MiB limit | ✅ passes | ❌ exceeds |

Conclusion: arena reclaims memory on the Err path, allowing the program to fit
under a hard 16 MiB budget while the non-arena version does not.
