"""icons.py — a small inline-SVG icon set.

Why hand-rolled rather than an icon font:

* Emoji (which is what this replaced) are rendered by the operating system, so
  the same glyph looks different on Windows, macOS and Android, and neither its
  weight nor its colour can be controlled. That inconsistency is most of what
  makes an interface look unfinished.
* Material Symbols / Lucide / Heroicons are all somebody's default — Google's,
  shadcn's, Tailwind's. Recognisable defaults read as generic.
* An icon font is also a render-blocking request to a third party on every page
  load. These cost nothing and are always available.

Drawn on a 24x24 grid with a 1.6px stroke, rounded caps and joins, tuned to sit
with Public Sans at body size. Everything inherits `currentColor`, so an icon
takes the colour of whatever it sits in.

IMPORTANT: these return raw SVG markup, so they only work where HTML is
rendered — st.markdown(..., unsafe_allow_html=True) and f-string templates.
Streamlit *widget labels* (st.button, st.expander, st.error) render their label
as plain text, so SVG cannot be used there; drop the icon instead. st.error and
st.warning already draw their own status icon, which is why a leading "!" or
cross in those messages was always redundant.
"""

# Path data only — the wrapper supplies size, stroke and colour.
_PATHS = {
    "check":    '<polyline points="4 12.5 9.5 18 20 6.5"/>',
    "cross":    '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
    "download": '<path d="M12 3v12"/><polyline points="7 11 12 16 17 11"/>'
                '<path d="M4 20h16"/>',
    "warning":  '<path d="M12 4.5 21 19.5H3z"/><line x1="12" y1="10" x2="12" y2="14"/>'
                '<line x1="12" y1="16.8" x2="12" y2="16.9"/>',
    "info":     '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16.5"/>'
                '<line x1="12" y1="7.6" x2="12" y2="7.7"/>',
    "plus":     '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "search":   '<circle cx="10.5" cy="10.5" r="6.5"/><line x1="15.5" y1="15.5" x2="20" y2="20"/>',
    "refresh":  '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><polyline points="20 4 20 9 15 9"/>',
    "trash":    '<path d="M4 7h16"/><path d="M9.5 7V4.5h5V7"/>'
                '<path d="M6.5 7l1 12.5h9L17.5 7"/>',
    "external": '<path d="M14 4h6v6"/><line x1="20" y1="4" x2="11" y2="13"/>'
                '<path d="M18 14.5V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V7.5'
                'A1.5 1.5 0 0 1 5 6h4.5"/>',
    "chart":    '<polyline points="3 16.5 9 10.5 13 14.5 21 6.5"/><path d="M3 20.5h18"/>',
}


def icon(name, size=16, stroke=1.6, cls="", style=""):
    """Inline SVG for `name`, coloured by the surrounding `currentColor`.

    Returns '' for an unknown name rather than raising — an icon is decoration,
    and a typo in a template shouldn't take a page down.
    """
    path = _PATHS.get(name)
    if not path:
        return ""
    return (
        f'<svg class="qw-icon {cls}" style="{style}" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
        f'focusable="false">{path}</svg>'
    )


def bullet(name, text, color=None, size=15):
    """An icon + label row for feature lists — keeps the icon optically aligned
    with the first line of text rather than sitting on the baseline."""
    c = f"color:{color};" if color else ""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:0.55rem;'
        f'line-height:1.6">'
        f'<span style="{c}display:inline-flex;flex:0 0 auto">{icon(name, size)}</span>'
        f'<span>{text}</span></span>'
    )
