"""Every file QuantWizard produces says QuantWizard made it.

Office files carry author / last-modified-by fields in their document
properties. Left alone they inherit whatever the template carried: the Excel
templates are derived from reference workbooks edited on a personal account
(so every export named that person as its last editor), and python-pptx's
built-in template names its own author. Called just before each save.
"""
from datetime import datetime, timezone

AUTHOR = "QuantWizard"


def stamp(obj, title=None):
    """Set author, last-modified-by and timestamps on an openpyxl Workbook or a
    python-pptx / python-docx document. Never raises - a property that cannot
    be set must not cost the reader their file."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    try:
        props = getattr(obj, "properties", None)
        if props is not None and hasattr(props, "lastModifiedBy"):          # openpyxl
            props.creator = AUTHOR
            props.lastModifiedBy = AUTHOR
            props.created = props.modified = now
            if title:
                props.title = title
            return obj
        core = getattr(obj, "core_properties", None)                       # pptx / docx
        if core is not None:
            core.author = AUTHOR
            core.last_modified_by = AUTHOR
            core.created = core.modified = now
            core.revision = 1
            if title:
                core.title = title
    except Exception:
        pass
    return obj
