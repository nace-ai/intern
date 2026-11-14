# Vendored offline assets

No CDN at demo time. Everything here is pinned and committed. Total size ~350 KB.

## Chart.js

| File | Version | Source | Licence |
|---|---|---|---|
| `chart.umd.min.js` | 4.4.4 | https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js | MIT — see `LICENSE-chartjs.txt` |

UMD build: exposes `window.Chart`. Load it before `js/core.js` (see CONTRACT §5).

## Fonts

Latin subset woff2 only. `fonts.css` holds the `@font-face` rules (`font-display: swap`).

| File | Family | Weight | Version | Source |
|---|---|---|---|---|
| `fonts/inter-latin-400-normal.woff2` | Inter | 400 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-400-normal.woff2 |
| `fonts/inter-latin-500-normal.woff2` | Inter | 500 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-500-normal.woff2 |
| `fonts/inter-latin-600-normal.woff2` | Inter | 600 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-600-normal.woff2 |
| `fonts/inter-latin-700-normal.woff2` | Inter | 700 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-700-normal.woff2 |
| `fonts/inter-latin-800-normal.woff2` | Inter | 800 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/inter@5.1.0/files/inter-latin-800-normal.woff2 |
| `fonts/jetbrains-mono-latin-400-normal.woff2` | JetBrains Mono | 400 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.1.0/files/jetbrains-mono-latin-400-normal.woff2 |
| `fonts/jetbrains-mono-latin-600-normal.woff2` | JetBrains Mono | 600 | 5.1.0 | https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5.1.0/files/jetbrains-mono-latin-600-normal.woff2 |

Licences: SIL Open Font License 1.1 (OFL).

- Inter — `fonts/OFL-Inter.txt`, upstream https://github.com/rsms/inter (v4.0)
- JetBrains Mono — `fonts/OFL-JetBrainsMono.txt`, upstream https://github.com/JetBrains/JetBrainsMono (v2.304)

## Use

```html
<link rel="stylesheet" href="vendor/fonts/fonts.css">
<script src="vendor/chart.umd.min.js"></script>
```

`font-family: "Inter", system-ui, sans-serif` and `font-family: "JetBrains Mono", ui-monospace, monospace`.
