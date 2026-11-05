# Retrieval evaluation (20 tasks)

| config | file R@5 | file R@10 | test R@10 | tokens (mean) | latency p50 / p95 (ms) |
|---|---|---|---|---|---|
| rg | 0.55 | 0.68 | 0.56 | 4341 | 49156 / 62174 |
| colgrep | 0.50 | 0.55 | 0.16 | 5720 | 38634 / 45538 |
| colgrep+rg | 0.57 | 0.78 | 0.34 | 8984 | 58904 / 63186 |
| colgrep+rg+cbm | 0.75 | 0.80 | 0.41 | 10168 | 65359 / 68877 |
