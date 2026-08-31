"""chart_theme.py — the one chart style every figure in the app inherits.

Usage
-----
Style any existing figure with a single call, replacing its inline layout:

    from chart_theme import style, value_axis

    fig = go.Figure(...)
    style(fig, height=380)                       # line/area/bar, currency y-axis
    style(fig, height=320, grid=False)           # heatmaps — no gridlines
    style(fig, height=300, y=pct_axis())         # percentage y-axis
    style(fig, height=400, legend=None)          # no legend

`style()` mutates and returns the figure, so it can wrap a construction call.

Design rules enforced here (from the chart spec)
------------------------------------------------
* Horizontal gridlines only. Vertical separation is carried by faint
  fiscal-year hairlines, never by a full grid.
* No axis spines, no plot border, no shadow, no gradient fill.
* The value axis is always labelled — an unlabelled y-axis was the single most
  visible credibility gap in the previous charts.
* Filled-area charts start the value axis at zero. Truncating the base of an
  area chart misrepresents the magnitude it is drawing.
* Every number renders in IBM Plex Sans, which has true tabular figures, so
  digits sit in fixed-width columns and grids do not shift between frames.
* Legends are plain text outside the plot — never a floating rounded box.

Note on fonts: all previous charts asked for "DM Sans", which `styles.css`
deliberately never loads, so every chart silently fell back to the browser
default and did not match the surrounding UI. `chart_tokens.font.data` is the
only font name that should appear in chart code from here on.
"""
from chart_tokens import color, stroke, marker, font, layout, plot_height

__all__ = [
    "style", "value_axis", "pct_axis", "time_axis", "category_axis",
    "linear_axis", "plain_axis", "hover", "legend_inline", "spike_config",
    "add_recession",
    "add_no_coverage", "add_forecast", "add_fy_hairlines", "series_color",
    "plot_height",
    "color", "stroke", "marker", "font", "layout",
]

# Transparent, so a figure sits on whatever surface the page gives it rather
# than punching a white rectangle through a tinted section band.
_TRANSPARENT = "rgba(0,0,0,0)"


def _rgba(hex_color, alpha):
    """`#RRGGBB` -> `rgba(r,g,b,alpha)`. Plotly needs rgba() for partial fills."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# ── Axes ─────────────────────────────────────────────────────────────────────

def value_axis(prefix="$", tick_format=",.0f", zero=True, side="left", **kw):
    """Labelled currency axis. Left-hand side by default, in the 44px gutter.

    `zero=True` pins the base at zero, which is required for any chart with a
    filled area and harmless for most line charts.

    `side="right"` is the convention for a price-over-time chart, and every
    finance site follows it for the same reason: a price series is read
    left-to-right, so the eye finishes at the newest bar on the right edge and
    the axis it needs to read against should be the one it has just arrived at.
    A left axis makes the reader traverse the whole plot backwards to price the
    latest point. Use it for price; keep the left default for everything whose
    x-axis is not time-ordered.
    """
    ax = dict(
        tickprefix=prefix, tickformat=tick_format,
        side=side,
        nticks=6,
        tickfont=dict(size=font.size.axis, color=color.ink_muted, family=font.data),
        gridcolor=color.grid, griddash="solid", gridwidth=stroke.grid,
        showline=False, zeroline=False, showspikes=False,
    )
    if zero:
        ax["rangemode"] = "tozero"
    ax.update(kw)
    return ax


def pct_axis(tick_format=".0f", zero=False, **kw):
    """Percentage axis — suffixed, not prefixed."""
    ax = value_axis(prefix="", tick_format=tick_format, zero=zero, **kw)
    ax["ticksuffix"] = "%"
    return ax


def plain_axis(zero=False, tick_format=None, **kw):
    """Unit-less numeric axis (ratios, counts, RSI, correlation).

    `tick_format` is an explicit parameter, not part of `**kw`: forwarding it
    positionally while callers also passed it by keyword raised
    "got multiple values for keyword argument 'tick_format'".
    """
    return value_axis(prefix="", tick_format=tick_format, zero=zero, **kw)


def time_axis(fy_ticks=True, **kw):
    """Date axis. Ticks land on fiscal-year boundaries, labelled every 1-2 years.

    No vertical gridlines: `add_fy_hairlines()` draws the year separation at
    0.5px instead, which reads as structure rather than as a grid.
    """
    ax = dict(
        type="date",
        tickfont=dict(size=font.size.axis, color=color.ink_muted, family=font.data),
        showgrid=False, showline=False, zeroline=False,
        ticks="outside", ticklen=3, tickcolor=color.rule,
        # Belt and braces with MIN_BOTTOM_MARGIN: automargin only ever GROWS the
        # margin, so a long or rotated label still can't be cut off.
        automargin=True,
    )
    if fy_ticks:
        ax.update(tickformat="%Y", dtick="M12")
    ax.update(kw)
    return ax


def linear_axis(title=None, **kw):
    """Numeric x-axis (trading days, horizon, bucket index). No grid, no spine."""
    ax = dict(
        title=title,
        tickfont=dict(size=font.size.axis, color=color.ink_muted, family=font.data),
        title_font=dict(size=font.size.fact_label, color=color.ink_muted,
                        family=font.data),
        showgrid=False, showline=False, zeroline=False,
    )
    ax.update(kw)
    return ax


def category_axis(**kw):
    """Discrete axis for bar charts. No grid, no spine."""
    ax = dict(
        type="category",
        tickfont=dict(size=font.size.axis, color=color.ink_muted, family=font.data),
        showgrid=False, showline=False, zeroline=False,
        automargin=True,
    )
    ax.update(kw)
    return ax


# ── Chrome ───────────────────────────────────────────────────────────────────

def hover():
    """Tooltip: ink background, white text, 2px radius, no shadow, no arrow."""
    return dict(
        bgcolor=color.ink, bordercolor=color.ink,
        font=dict(color=color.paper, size=font.size.fact_value, family=font.data),
        align="left",
    )


def legend_inline(position="top-right"):
    """Plain-text legend outside the plot. Never a floating rounded box."""
    common = dict(
        orientation="h",
        font=dict(size=font.size.grid, color=color.ink_muted, family=font.data),
        bgcolor=_TRANSPARENT, borderwidth=0,
        itemsizing="constant",
    )
    if position == "top-left":
        common.update(yanchor="bottom", y=1.02, xanchor="left", x=0)
    elif position == "bottom":
        # Left-aligned to the plot's left edge, not centred: a centred legend
        # floats without an anchor, and the eye reads a chart left-to-right.
        common.update(yanchor="top", y=-0.18, xanchor="left", x=0)
    else:  # top-right
        common.update(yanchor="bottom", y=1.02, xanchor="right", x=1)
    return common


def series_color(i):
    """i-th categorical series colour, wrapping."""
    return color.series[i % len(color.series)]


# ── The one entry point ──────────────────────────────────────────────────────

# Bottom padding has to clear an OUTSIDE tick (3px), an 11px tick label and its
# descenders. The default was 30px, which fits the glyphs by about a pixel — and
# Streamlit sizes the chart iframe to exactly the figure height, so anything that
# overflows is cut rather than scrolled. Date labels came out visibly clipped on
# the Analysis page.
#
# Raising the default alone would not have fixed it: two dozen charts pass their
# own `margin=` and most of them said b=30. So the floor is enforced here rather
# than left to each call site, where the next chart added would miss it again.
# Charts with no cartesian x-axis (pie, treemap, indicator) are exempt — several
# deliberately use b=0, and padding under a pie is just dead space.
MIN_BOTTOM_MARGIN = 44

_NO_XAXIS = {"pie", "sunburst", "treemap", "funnelarea", "indicator"}


def _bottom_margin(fig, margin: dict) -> dict:
    """Return `margin` with `b` raised to the floor where an x-axis is drawn."""
    try:
        if fig.data and all(getattr(t, "type", "") in _NO_XAXIS for t in fig.data):
            return margin
    except Exception:
        pass
    m = dict(margin)
    m["b"] = max(m.get("b", 0) or 0, MIN_BOTTOM_MARGIN)
    return m


def style(fig, *, height=None, x=None, y=None, y2=None, legend="top-right",
          grid=True, crosshair=True, margin=None, bar_gap=None, zoom=False,
          **overrides):
    """Apply the house style to `fig`. Mutates and returns it.

    Parameters
    ----------
    height     : plot height in px. Defaults to the `plot_ratio` proportion.
    x, y, y2   : axis dicts, e.g. from `time_axis()` / `value_axis()`. Defaults
                 are a plain time axis and a labelled currency axis.
    legend     : "top-right" | "top-left" | "bottom" | None
    grid       : False strips horizontal gridlines (heatmaps, pies).
    crosshair  : False disables the hover spike.
    zoom       : True re-enables drag-to-zoom. Off by default — see below.
    margin     : override the default padding dict.
    **overrides: passed straight to `update_layout`, so any chart can still
                 deviate deliberately — the point is that deviation becomes
                 visible in the diff rather than being the default state.
    """
    pad = layout.plot_padding
    lay = dict(
        height=height if height is not None else plot_height(),
        template=None,
        plot_bgcolor=color.paper,
        paper_bgcolor=_TRANSPARENT,
        margin=_bottom_margin(fig, margin or dict(
            l=pad["left"], r=pad["right"],
            t=pad["top"] + 22, b=pad["bottom"] + 44)),
        font=dict(family=font.data, color=color.ink_muted, size=font.size.grid),
        hovermode="x unified" if crosshair else "closest",
        hoverlabel=hover(),
        showlegend=legend is not None,
        # Motion is a distraction in a data view; it also fights
        # prefers-reduced-motion, which styles.css honours.
        transition=dict(duration=0),
    )
    if legend is not None:
        lay["legend"] = legend_inline(legend)

    ax_x = time_axis() if x is None else dict(x)
    ax_y = value_axis() if y is None else dict(y)
    if not grid:
        ax_y["showgrid"] = False
    if crosshair:
        ax_x.update(spike_config())
    # ── Zoom is off unless a chart asks for it ───────────────────────────────
    # Plotly enables drag-to-zoom by default, and app.py hides the modebar for a
    # cleaner look. Together those give you a chart you can zoom INTO but not
    # OUT of: the only way back is a double-click nobody advertises. A stray
    # trackpad drag would leave a Free Cash Flow chart showing one bar against a
    # 90-105 axis, or a price chart with the line pinned to the bottom edge and
    # most of the panel empty — permanently, from the reader's point of view.
    #
    # These are read-only analytical charts over a fixed window. Zoom was never
    # the interaction anyone wanted here; reading a value off the line is, and
    # the hover crosshair already does that. setdefault so a chart that sets
    # `fixedrange` itself still wins.
    ax_x.setdefault("fixedrange", not zoom)
    ax_y.setdefault("fixedrange", not zoom)
    lay["xaxis"] = ax_x
    lay["yaxis"] = ax_y
    if y2 is not None:
        ax_y2 = dict(y2)
        ax_y2.setdefault("fixedrange", not zoom)
        lay["yaxis2"] = ax_y2
    if not zoom:
        # Also stops click-drag starting a selection rectangle at all.
        lay["dragmode"] = False
    if bar_gap is not None:
        lay["bargap"] = bar_gap

    lay.update(overrides)
    fig.update_layout(**lay)
    return fig


# ── Return since the start of the window ─────────────────────────────────────

def since_start(values, value_fmt="$%{y:,.2f}", name=None):
    """(customdata, hovertemplate) adding "% since <start>" to a hover.

    Zoom used to be the only way to interrogate a chart, and it was the wrong
    tool: dragging a box around a region answers "what did this look like
    magnified", when the question a reader actually has is "how much is this up
    from where it started". That is one number, and it can simply be shown.

    Pass the same series you passed as `y`. The first finite value is the base;
    every point carries its percent change from it, so hovering anywhere reads
    the cumulative return to that date without arithmetic.

    A zero or missing base leaves the cell blank rather than dividing by it —
    a portfolio can legitimately open at zero before its first contribution.
    """
    import math

    base = None
    for v in values:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv) and fv != 0:
            base = fv
            break

    # The percentage is formatted HERE, in Python, and shipped as a finished
    # string. Plotly ignored the `:+.1f` spec on customdata in x-unified mode and
    # printed the raw float — "3.8999999999999924% since start". Rather than
    # discover which combination of hovermode and indexing it does honour,
    # customdata carries text and the template just prints it.
    def _pct(v):
        if base is None:
            return ""
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return ""
        if not math.isfinite(fv):
            return ""
        return f"{(fv / base - 1.0) * 100.0:+.1f}% since start"

    custom = [[_pct(v)] for v in values]

    # Built by concatenation, not f-string interpolation: value_fmt already
    # contains Plotly's own %{...} braces and must pass through untouched.
    tail = "<extra>" + name + "</extra>" if name else "<extra></extra>"
    tmpl = (value_fmt
            + "<span style='color:" + color.ink_muted + "'>"
            + "  %{customdata[0]}</span>"
            + tail)
    return custom, tmpl


# ── Region shading ───────────────────────────────────────────────────────────
# All draw below the data (`layer="below"`) and carry no stroke, so they read as
# context rather than as another series.

def add_recession(fig, spans, **kw):
    """Shade recession spans. `spans` is [(start, end), ...] of date-likes."""
    for x0, x1 in spans or ():
        fig.add_vrect(x0=x0, x1=x1, fillcolor=_rgba(color.recession, 0.55),
                      line_width=0, layer="below", **kw)
    return fig


def spike_config():
    """Crosshair spike settings — apply to whichever x-axis carries the plot.

    On a subplot figure `style()` writes these to `xaxis`, which is row 1; the
    plot usually lives on `xaxis2`. Re-apply there explicitly.
    """
    return dict(showspikes=True, spikemode="across", spikesnap="cursor",
                spikecolor=_rgba(color.ink, 0.30), spikethickness=1,
                spikedash="solid")


def add_no_coverage(fig, x0, x1, label="No coverage", **kw):
    """Shade the pre-coverage region and say so.

    A series that simply starts partway across the plot with no explanation
    reads as missing data or a bug. Naming the gap is the difference.
    """
    if x0 is None or x1 is None:
        return fig
    fig.add_vrect(x0=x0, x1=x1, fillcolor=color.no_coverage, line_width=0,
                  layer="below", **kw)
    if label:
        fig.add_annotation(
            x=x0, y=1, yref="paper", xanchor="left", yanchor="top",
            text=label, showarrow=False, xshift=6, yshift=-4,
            font=dict(size=font.size.footnote, color=color.ink_faint,
                      family=font.data))
    return fig


def add_forecast(fig, x0, x1):
    """Overlay the projected region and rule its boundary."""
    if x0 is None:
        return fig
    fig.add_vrect(x0=x0, x1=x1, fillcolor=color.forecast, line_width=0,
                  layer="below")
    fig.add_vline(x=x0, line_width=stroke.fy_hair, line_color=color.rule,
                  layer="below")
    return fig


def add_fy_hairlines(fig, xs, **kw):
    """0.5px vertical hairlines at fiscal-year boundaries.

    This replaces vertical gridlines. A full vertical grid competes with the
    data; a hairline at each year reads as a ruled page.

    Pass `row=`/`col=` on a subplot figure — without them Plotly draws the line
    across every subplot, which would rule the data grids as well as the plot.
    """
    for xv in xs or ():
        fig.add_vline(x=xv, line_width=stroke.fy_hair, line_color=color.grid_fy,
                      layer="below", **kw)
    return fig
