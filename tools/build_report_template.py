"""Build assets/report_template.xlsx from the reference workbook.

The reference (assets/AAPL_5Y_Analysis (6).xlsx) is QuantWizard's AAPL export
rebuilt by Claude in Excel. Its layout, styles, number formats, column widths,
row heights, merges, frozen panes, dropdowns and conditional formats are what
our Excel export reproduces. This script keeps all of that and strips anything
that is Apple's: hard-coded numbers, text containing digits, dates, names or
tickers, formulas that embed a year or a row count, notes and hyperlinks. What
survives is ticker-independent - generic labels and formulas that only point
at other cells - and excel_report.py writes everything else.

Run once after changing the reference:  python tools/build_report_template.py
"""
import os
import re
import sys

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
REF = os.path.join(ROOT, "assets", "AAPL_5Y_Analysis (6).xlsx")
OUT = os.path.join(ROOT, "assets", "report_template.xlsx")

FRONT = ["Summary", "Financials", "DCF", "Scenarios", "Multiples", "Risk",
         "Catalysts_News", "Methodology", "Sources"]
DATA = ["Raw_Fundamentals", "Price_Data", "Raw_News", "Raw_Peers", "Monte_Carlo"]
# Rebuilt row by row in code - the template only lends them styles.
DYNAMIC = {"Risk", "Catalysts_News", "Methodology", "Sources",
           "Raw_Fundamentals", "Price_Data", "Raw_News", "Raw_Peers", "Monte_Carlo"}

# Text that names the company or a period is regenerated; generic text stays.
_SPECIFIC = re.compile(
    r"\d|Apple|AAPL|iPhone|iPad|Mac\b|Siri|Samsung|Sony|Dell|MSFT|GOOGL|AMZN|META|NVDA|AVGO|"
    r"\bMU\b|tariff|QuantWizard|Wyatt|Cook|Services|China|App Store|foldable|Nasdaq|NASDAQ",
    re.I)
_FORMULA_SPECIFIC = re.compile(r"Apple|20\d\d|\$503|\b503\b|\b251\b|Raw_Valuation|Price_Data")


def main():
    wb = load_workbook(REF)
    for name in list(wb.sheetnames):
        if name not in FRONT + DATA:
            del wb[name]
    for ws in wb.worksheets:
        ws._charts = []
        ws._images = []
        keep_rows = 2 if ws.title in DYNAMIC else None     # title band only
        for row in ws.iter_rows():
            for c in row:
                if type(c).__name__ == "MergedCell":
                    continue
                c.comment = None
                c.hyperlink = None
                v = getattr(c.value, "text", c.value)
                if v is None:
                    continue
                if keep_rows is not None:
                    # Dynamic sheets: keep column headers of data tabs, clear the rest.
                    if ws.title in DATA and c.row == 1:
                        continue
                    c.value = None
                    continue
                if isinstance(v, str) and v.startswith("="):
                    if _FORMULA_SPECIFIC.search(v):
                        c.value = None
                elif isinstance(v, str):
                    if _SPECIFIC.search(v):
                        c.value = None
                else:
                    c.value = None                          # every hard-coded number / date
        # Titles are always regenerated.
        if ws.title in FRONT:
            ws["A1"].value = None
            ws["A2"].value = None
    # Conditional formats on dynamic ranges are rebuilt in code.
    for name in DYNAMIC:
        if name in wb.sheetnames:
            wb[name].conditional_formatting = type(wb[name].conditional_formatting)()
    __import__("doc_props").stamp(wb)
    wb.save(OUT)
    print("wrote", OUT, "sheets:", wb.sheetnames)
    _export_charts()


def _export_charts():
    """The Summary charts' XML, cached values stripped, with the title and the
    last Price_Data row left as placeholders for excel_report to fill in."""
    import zipfile
    z = zipfile.ZipFile(REF)
    wbx = z.read("xl/workbook.xml").decode()
    sheets = dict(re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wbx))
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"',
                           z.read("xl/_rels/workbook.xml.rels").decode()))
    summ = rels[sheets["Summary"]].split("/")[-1]
    srels = z.read(f"xl/worksheets/_rels/{summ}.rels").decode()
    drawing = re.findall(r'Target="\.\./drawings/([^"]+)"', srels)[0]
    drels = z.read(f"xl/drawings/_rels/{drawing}.rels").decode()
    charts = re.findall(r'Target="\.\./charts/([^"]+)"', drels)
    outdir = os.path.join(ROOT, "assets", "report_charts")
    os.makedirs(outdir, exist_ok=True)
    for ch in charts:
        x = z.read(f"xl/charts/{ch}").decode("utf-8")
        x = re.sub(r"<c:(num|str)Cache>.*?</c:\1Cache>", "", x, flags=re.S)
        x = re.sub(r"<c:userShapes[^>]*/>", "", x)
        x = re.sub(r"<c:externalData[^>]*>.*?</c:externalData>", "", x, flags=re.S)
        if "<c:barChart>" in x:
            name = "value_bar.xml"
        else:
            name = "price_line.xml"
            x = re.sub(r"(<a:t>)[^<]*price[^<]*(</a:t>)", r"\g<1>{TITLE}\g<2>", x, count=1)
        open(os.path.join(outdir, name), "w", encoding="utf-8").write(x)
        print("chart", ch, "->", name, "r:id refs:", len(re.findall(r'r:id="', x)))


if __name__ == "__main__":
    sys.exit(main())
