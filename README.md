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
  in the right gutter; the body font is shrunk just enough to reserve gutter
  space (sized for the widest label plus one character of padding), so labels
  never overlap the text.
- **Page header**: every page carries a small right-aligned line in the top
  margin: the document title (first non-blank, non-rule line of the source),
  the page number as `p. N/M`, and the input file's mtime. Only the title is
  truncated (with `...`) if the line would run past the left margin; the page
  number and date are never clipped. Suppress the whole line with
  `--no-header`. It is drawn above the body area, so it never affects layout.

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
| `--paper\|-p A4\|LETTER` | `A4` | Paper size |
| `--landscape\|-l` | off | Landscape orientation |
| `--margins\|-m T,R,B,L` | `36,36,36,36` | Margins in points (36 = 0.5") |
| `--font\|-f NAME` | `Courier` | Monospace font family (reportlab registered name) |
| `--list-fonts\|-F` | - | Print the fonts usable with `--font`, monospace ones marked, and exit |
| `--tabsize\|-t N` | `8` | Tab expansion width |
| `--min-size\|-i N` | `10` | Lower bound on font size. If text won't fit on one page at this size, spill onto multiple pages instead of shrinking further. |
| `--max-size\|-x N` | none | Cap font size in pt |
| `--max-leading\|-L F` | `1.5` | Max line spacing as multiple of font size |
| `--number\|-n` | off | Number lines as `G.N` in the right gutter |
| `--start-group\|-s N` | `1` | Group number to start at (implies `--number`) |
| `--header\|-H` / `--no-header` | on | Print `<title>  p. N/M  YYYY-MM-DD HH:MM:SS` in the top margin (drawn outside the body, layout-neutral). The negated form has no short option. |

### Fonts

`--list-fonts` prints the 14 standard PDF base fonts, which need no embedding:

```
$ ./autopage.py --list-fonts
Courier                (monospace)
Courier-Bold           (monospace)
Courier-BoldOblique    (monospace)
Courier-Oblique        (monospace)
Helvetica
...
```

The marker is measured, not hardcoded: a font counts as monospace when every printable
ASCII character has the same advance width. Since autopage lays out plain text, the
`Courier*` family is normally what you want. Note the name is `Times-Roman`, not `Times`.

## Tests

```
uv run pytest
```

## License

MIT — see [LICENSE](LICENSE).
