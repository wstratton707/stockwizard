"""chart_tokens.py — design tokens for every chart in the app. No chart logic.

Why this file exists
--------------------
Before this, 37 Plotly figures across five files were each styled inline. That
produced four independent palettes, three incompatible `template` regimes
(`None`, `"plotly_white"`, and eight charts with no template at all), eight
different hexes all serving as "the blue", and `plot_bgcolor` alternating
between white and #f8fafc with no rule behind it.

It also produced a live bug: every chart hardcoded `family="DM Sans"`, a font
`styles.css` deliberately never loads (see its header comment). Every chart on
the site was silently falling back to the browser default sans and did not match
the surrounding UI.

Nothing here imports plotly. This module is values only, so it can be read by
the web charts, the matplotlib report builders, and any future renderer without
dragging a charting library along. `chart_theme.py` turns these into layouts.

Aesthetic target: institutional financial terminal — dense, quiet, precise.
Not a marketing dashboard.
"""

# ── Color ────────────────────────────────────────────────────────────────────
# `brand` is the one token shared with styles.css (--accent). Everything else is
# chart-specific: series colours should not drift when the brand blue changes.


class color:
    paper = "#FFFFFF"
    ink = "#14171A"           # price line, primary text
    ink_muted = "#6B7280"     # axis labels, secondary text
    ink_faint = "#9CA3AF"     # footnotes, disclaimers

    rule = "#E5E7EB"          # dividers, borders
    grid = "#EFF1F3"          # horizontal gridlines
    grid_fy = "#F5F6F8"       # fiscal-year vertical hairlines

    # Valuation corridor. Was a sage/mint green pair, which read as a copy of a
    # well-known valuation chart and shared nothing with the rest of the app.
    # Blue ties it to --accent, and the luminance is matched to the old green so
    # the black price line still reads where it crosses the base band.
    corridor_base = "#3F6C9C"  # primary valuation band (steel blue)
    corridor_high = "#C3DDF5"  # upper valuation band (light blue)
    corridor_edge = "#2C5580"  # top edge of the base band
    # Band fills carry alpha so the gridlines read through instead of the bands
    # sitting on the page as two flat blocks of colour.
    corridor_base_fill = "rgba(63,108,156,0.88)"
    corridor_high_fill = "rgba(195,221,245,0.68)"

    value_line = "#D98324"    # fair value / normal-multiple line (amber)
    income_line = "#E0B341"   # dividend / income line (gold)

    recession = "#E4E6E8"     # recession bands
    forecast = "#F7F8F9"      # forecast / projection overlay
    no_coverage = "#FAFAFB"   # periods with no data

    brand = "#1D4ED8"         # links, active states, primary buttons (== --accent)
    negative = "#B42318"
    positive = "#067647"

    # Categorical ramp for multi-series charts (peers, sectors, holdings).
    # Ordered for maximum separation at the first four entries, which is as many
    # as most of these charts actually use.
    series = ("#1D4ED8", "#D98324", "#4F7A5B", "#8B5CF6",
              "#0E7490", "#B42318", "#6B7280", "#E0B341")

    # Diverging ramp for correlation heatmaps. Replaces the four different
    # red/white/blue and RdYlGn scales previously used across the app.
    diverging = ((0.0, "#B42318"), (0.5, "#FFFFFF"), (1.0, "#1D4ED8"))


# ── Stroke widths ────────────────────────────────────────────────────────────
# Hairlines throughout. Thick strokes are the clearest tell of a chart built by
# someone who does not work with market data.


class stroke:
    price = 1.25
    value = 1.75
    income = 1.25
    grid = 1
    rule = 1
    fy_hair = 0.5


# ── Markers ──────────────────────────────────────────────────────────────────
# Hollow, annual only. Never on the price line.


class marker:
    size = 5
    stroke_width = 1
    fill = color.paper


# ── Type ─────────────────────────────────────────────────────────────────────
# Two faces, deliberately paired.
#
# IBM Plex Sans carries all chart text. It is an institutional face with true
# tabular figures, which is what the data grids need. Newsreader stays on
# display type only — the app's editorial serif, unchanged.
#
# Named `font` rather than the spec's `type` so it does not shadow the builtin.


class font:
    display = "'Newsreader', Georgia, serif"                  # chart header, ticker
    data = "'IBM Plex Sans', system-ui, sans-serif"           # grid, axes, fact rows

    class size:
        ticker = 20        # weight 600, letter-spacing -0.01em
        price = 22         # weight 500, tabular-nums
        section_lbl = 10   # weight 600, uppercase, letter-spacing 0.08em
        axis = 10
        grid = 11
        fact_label = 12
        fact_value = 12    # weight 500, tabular-nums
        footnote = 11


# ── Layout ───────────────────────────────────────────────────────────────────


class layout:
    plot_ratio = 1.85      # width : height of the plot area
    plot_padding = {"top": 8, "right": 16, "bottom": 4, "left": 44}
    grid_row_h = 18        # data-grid row height
    fact_row_h = 28        # sidebar fact row height
    radius = 2             # near-square. never above 4.

    # Typical rendered width of the chart column (the [1, 3.2] split of the
    # content area at 1440px). Used to derive plot height from plot_ratio.
    ref_width = 880


def plot_height(width=None, ratio=None):
    """Plot-area height honouring `plot_ratio`.

    Plotly needs an absolute pixel height; it has no aspect-ratio primitive and
    Streamlit does not tell us the rendered width. `ref_width` is the measured
    typical width, so this yields the intended proportion at the common case and
    degrades gracefully elsewhere — a wide-and-short plot area flattens the
    curve, which is the specific failure this guards against.
    """
    w = layout.ref_width if width is None else width
    r = layout.plot_ratio if ratio is None else ratio
    return int(round(w / r))
