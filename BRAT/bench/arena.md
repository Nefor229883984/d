# Arena benchmark

Request handler: 3000 requests, 600 failing.

| | With arena | Without arena |
|---|---|---|
| Time | 0.036 s | 0.061 s |
| 16 MiB limit | pass | fail |

Arena reclaims memory on the error path, so the program fits under a hard
16 MiB budget. Without arena it exceeds the limit.
