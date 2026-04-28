# autopage

Fit a `.txt` file onto the fewest PDF pages possible at the largest readable font size.

- **Single-page mode**: if the text fits at `>= --min-size`, one page is produced with
  lines spread to fill the full height.
- **Multi-page mode**: when `--min-size` forces overflow, the font is maximized
  (width-constrained, capped at `--max-size`) and lines are distributed evenly
  across as many pages as needed.
- **Horizontal rules**: a line in the source matching `^\s*-{3,}\s*$` (3+ hyphens,
  optional surrounding whitespace) is rendered as a thin horizontal line in the
  gap between its neighbours. Marker lines do not consume vertical space and do
  not affect pagination. Consecutive markers collapse to one rule; a rule whose
  neighbours fall on different pages is drawn at the bottom of the earlier page.
- **Line numbering**: `--number/-n` prints `G.N` labels in the right gutter,
  where `G` is the group (delimited by horizontal rules) and `N` resets within
  each group. `--start-group/-s N` starts at a specific group number (implies
  `--number`). Blank lines and indented lines (treated as continuations of the
  previous item) get no label and do not advance the counter. Labels are drawn
  on top of the body text, so a long line may overlap its label.

## Usage

```
./autopage.py input.txt [output.pdf] [--paper A4|LETTER] [--landscape]
```

The script uses a [uv inline-script](https://docs.astral.sh/uv/guides/scripts/) shebang
(`#!/usr/bin/env -S uv run --script`), so it runs standalone with no setup — `uv` fetches
dependencies on first run.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--paper A4\|LETTER` | `A4` | Paper size |
| `--landscape` | off | Landscape orientation |
| `--margins T,R,B,L` | `36,36,36,36` | Margins in points (36 = 0.5") |
| `--font NAME` | `Courier` | Monospace font family (reportlab registered name) |
| `--tabsize N` | `8` | Tab expansion width |
| `--min-size N` | `10` | Lower bound on font size. If text won't fit on one page at this size, spill onto multiple pages instead of shrinking further. |
| `--max-size N` | none | Cap font size in pt |
| `--max-leading F` | `1.5` | Max line spacing as multiple of font size |
| `--number\|-n` | off | Number lines as `G.N` in the right gutter |
| `--start-group\|-s N` | `1` | Group number to start at (implies `--number`) |

## Tests

```
uv run pytest
```

## License

MIT — see [LICENSE](LICENSE).
