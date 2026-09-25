"""Build assets/valuation_template.xlsx from the reference valuation workbook.

The reference (assets/GOOGL_5Y_Analysis UPDATED GOOD TEMPLATE.xlsx) is our
Alphabet export rebuilt by Claude in Excel into a live model: the website
writes only input cells and every other number is a formula. This script keeps
the formulas, styles, widths, merges, frozen panes, tab colours, dropdowns and
conditional formats, and removes everything that is Alphabet's - input values,
dated or company-specific text, cell notes (which carry the editor's name) and
external links. It also:
  - drops the Website_Spec tab (a build manual for us, not for readers);
  - fixes the reference's dropdown bug (a Yes/No list on the numeric inputs
    DCF!B18:B22);
  - makes a handful of Summary sentences read correctly for any company (a
    shrinking quarter, net debt, no private stakes, no capex guidance);
  - exports the two Summary charts' XML (caches stripped, title and last data
    row as placeholders) for excel_valuation to swap back in after saving.

excel_valuation.py writes every cleared cell. Run after changing the reference:
    python tools/build_valuation_template.py
The reference itself is not committed (its notes carry a personal name).
"""
import os
import re
import sys
import zipfile

from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
REF = os.path.join(ROOT, "assets", "GOOGL_5Y_Analysis UPDATED GOOD TEMPLATE.xlsx")
OUT = os.path.join(ROOT, "assets", "valuation_template.xlsx")
CHARTS = os.path.join(ROOT, "assets", "valuation_charts")

# Cells excel_valuation writes for every company - cleared here.
CLEAR = {
    "Summary": ["B52"],
    "DCF": ["C5", "C6", "C9", "C19", "C38", "C41", "C44", "A37", "A45", "A69", "A83", "C84",
            "B8", "B11",
            "B12", "B13", "B14", "B15", "B16", "B17", "B18", "B19", "B20", "B21", "B22",
            "B25", "B28", "B30", "B31"],
    "Multiples": ["A2", "C5", "C6", "C8", "C10", "B14", "C14", "D16", "A27", "C27", "B10", "B28"],
    "Peers": ["A2", "F6", "G6", "L6", "A7:L10"],
    "Valuation_History": ["A6:F14", "A15", "A16", "A17"],
    "Financials": ["B5:K5", "B6:K6", "B8:K8", "B10:K10", "B15:E16", "B17:E22", "B24:E26",
                   "A28", "B31", "C31", "B32:C34", "B36:C37", "B40:C42", "C43", "B44:C44",
                   "B46:C47", "A50", "B53", "C53", "B54:C56", "A55", "A56"],
    "Segments": ["A6:C11", "E6:F11", "I6:I11", "A25:C29", "A30:D30", "B34:B37", "A38", "H5"],
    "Capital_Returns": ["A6:A15", "C6:H15", "A16"],
    "Risk": ["C7", "F71"],
    "Catalysts_News": ["A6:D11", "A15:F23"],
    "Methodology": ["B8", "C8", "B11", "C11", "C12", "B13", "B14", "C14", "B15", "C15", "B16",
                    "C16", "B19", "B21", "C21", "A31", "B31", "B33"],
    "Sources": ["A5:F24"],
    "Valuation_Log": ["A6:J6"],
    "Data": ["A2:G1256", "Q2:Q16"],
}

# Formulas rewritten so the sentence reads right for any company.
FORMULAS = {
    ("Summary", "A36"): '="• Revenue growth ran "&TEXT(MIN(Financials!C18:E18),"+0%;-0%")&" to "'
                        '&TEXT(MAX(Financials!C18:E18),"+0%;-0%")&" y/y over the last three quarters"'
                        '&IF(Data!$Q$9="","",", "&Data!$Q$9)&"."',
    ("Summary", "D36"): '="• "&Data!$Q$10',
    ("Summary", "D37"): '=IF(Financials!C56>Financials!B56*1.005,"• Dilution: "&Data!$Q$11&" lifted the share '
                        'count to "&TEXT(Financials!C56,"0.00")&"B from "&TEXT(Financials!B56,"0.00")&"B at "'
                        '&Financials!K5&".",IF(Financials!C56<Financials!B56*0.995,"• Share count "'
                        '&TEXT(Financials!C56,"0.00")&"B vs "&TEXT(Financials!B56,"0.00")&"B at "&Financials!K5'
                        '&" — buybacks are shrinking the base.","• Share count steady at "'
                        '&TEXT(Financials!C56,"0.00")&"B; "&Data!$Q$11&"."))',
    ("Summary", "A38"): '=IFERROR("• Gross margin "&IF(Multiples!C28>=Multiples!B28,"widened","narrowed")&" to "'
                        '&TEXT(Multiples!C28,"0.0%")&" TTM from "&TEXT(Multiples!B28,"0.0%")&" in "'
                        '&Financials!K5&"; operating margin is "&TEXT(Financials!F23,"0.0%")&".",'
                        '"• Operating margin is "&TEXT(Financials!F23,"0.0%")&" over the last twelve months.")',
    ("Summary", "D38"): '="• Capex runs at "&TEXT(DCF!B40,"0%")&" of revenue"&IF(Data!$Q$12="",""," ("&Data!$Q$12'
                        '&")")&"; the TTM FCF margin is "&TEXT(DCF!B38,"0.0%")&" vs "&TEXT(Financials!K11,"0.0%")'
                        '&" in "&Financials!K5&"."',
    ("Summary", "A39"): '="• $"&TEXT(Financials!C35,"#,##0")&"B of cash and securities ("&IF(Financials!C39>=0,'
                        '"net cash $"&TEXT(Financials!C39,"#,##0")&"B","net debt $"&TEXT(-Financials!C39,"#,##0")'
                        '&"B")&")"&IF(Financials!C54>0," plus $"&TEXT(Financials!C54,"#,##0")&"B of private '
                        'equity stakes not captured by FCF","")&"."',
    ("Summary", "B52"): '="Valuation vs "&Data!$Q$7',
    ("Multiples", "D15"): '="Market cap ÷ net income (FY) · price ÷ diluted EPS, last 4 qtrs (TTM)."'
                          '&IF(Financials!F26>0.005," TTM is flattered by $"&TEXT(Financials!F26,"0.00")'
                          '&" of equity-security gains.","")',
    ("Multiples", "D24"): '="Equity \'spread\' over T-bills (risk-free "&TEXT(DCF!$B$14,"0.00%")&")"',
    ("Summary", "E23"): '="At "&TEXT(B9,"$0")&", "&Data!$Q$2&IF(N(C20)>0," trades at "&TEXT(C20,"0.0x")'
                        '&" trailing earnings"&IF(Financials!F19-Financials!F27>0.005," excluding one-off '
                        'investment gains ("&TEXT(Multiples!C15,"0.0x")&" GAAP)","")&IFERROR(", vs a historical '
                        'average of "&TEXT(Valuation_History!D17,"0.0x"),"")," has no trailing earnings to price")'
                        '&". The base-case DCF ("&TEXT(G10,"$0")&", "&TEXT(G11,"+0%;-0%")&") assumes "'
                        '&TEXT(DCF!B9,"0%")&" year-1 revenue growth fading to "&TEXT(DCF!B8,"0.0%")&", a "'
                        '&TEXT(DCF!B10,"0%")&" operating margin, and capex "&IF(DCF!B19<DCF!B40,"easing",'
                        '"rising")&" from "&TEXT(DCF!B40,"0%")&" to "&TEXT(DCF!B19,"0%")&" of revenue. To justify '
                        'the price the market needs "&IFERROR(TEXT(DCF!B79,"0%")&" year-1 growth","growth beyond '
                        'the solvable range")&" or a "&IFERROR(TEXT(DCF!B82,"0%"),"n/a")&" long-run operating '
                        'margin. "&IF(H11<0,"No scenario reaches the price.",IF(F11>0,"Even the bear case is '
                        'above the price.","The price sits between the bear ("&TEXT(F10,"$0")&") and bull ("'
                        '&TEXT(H10,"$0")&") cases; probability-weighted value "&TEXT(F13,"$0")&" ("'
                        '&TEXT(F14,"+0%;-0%")&")."))',
    ("Summary", "D43"): '="• The terminal value implies "&TEXT(DCF!B96,"0.0x")&" EV/EBITDA — "&IF(DCF!B96<8,'
                        '"conservative against the ~10–20x mature companies trade at, so the base case leans '
                        'cautious.",IF(DCF!B96>25,"aggressive; the base case may be too generous.","within '
                        'the normal range for mature companies."))',
    ("Scenarios", "B15"): '="Growth slows: revenue "&TEXT(B6,"+0%;-0%")&" in year one, operating margin settles '
                          'at "&TEXT(B7,"0%")&" (vs "&TEXT(DCF!$B$39,"0%")&" today) and a "&TEXT(B8,"0.0%")'
                          '&" discount rate prices in more risk."',
    ("Scenarios", "C15"): '="Revenue "&TEXT(C6,"+0%;-0%")&" in year one, fading to "&TEXT(C9,"0.0%")&"; '
                          'operating margin "&TEXT(C7,"0%")&" ("&DCF!$B$17&" anchor); capex "&IF(DCF!$B$19'
                          '<DCF!$B$40,"falls","rises")&" from "&TEXT(DCF!$B$40,"0%")&" to "&TEXT(DCF!$B$19,"0%")'
                          '&" of revenue by year "&DCF!$B$11&"."',
    ("Scenarios", "D15"): '="Momentum holds: revenue "&TEXT(D6,"+0%;-0%")&" in year one, operating margin '
                          'expands to "&TEXT(D7,"0%")&" as investment pays off, with a "&TEXT(D8,"0.0%")&" WACC '
                          'and "&TEXT(D9,"0.0%")&" terminal growth."',
    ("Valuation_History", "A19"): '=IFERROR("On clean earnings the stock trades at "&TEXT(D15,"0.0")&"x, versus '
                                  'a "&TEXT(D17,"0.0")&"x average for the years above — "&IF(D15>D17,"above",'
                                  '"below")&" its own history."&IF(Financials!F26>0.005," The GAAP TTM P/E of "'
                                  '&TEXT(Multiples!C15,"0.0")&"x is flattered by one-off investment gains.",""),'
                                  '"Not enough price and earnings history for a P/E comparison.")',
    ("Capital_Returns", "A18"): '=IFERROR("Over "&COUNT(B6:B15)&" fiscal years "&Data!$Q$2&" returned "'
                                '&TEXT(F16,"0%")&" of free cash flow to shareholders"&IF(C16>0,"; stock-based '
                                'pay of $"&TEXT(G16,"0")&"B equalled "&TEXT(G16/C16,"0%")&" of buybacks, so part '
                                'of the buyback only offsets dilution",""),"")&"."&IF(SUM(D6:D15)=0," No dividends '
                                'were paid.",IF(N(D6)>0,"",IFERROR(" Dividends began in "&INDEX(A6:A15,MATCH(TRUE,'
                                'INDEX(D6:D15>0,0),0))&".","")))',
    ("Summary", "G20"): '="vs today "&IFERROR(TEXT(DCF!B39,"0%"),"n/a")&IFERROR(" · peers "&TEXT(MEDIAN('
                        'Peers!H7:H10),"0%"),"")',
    ("Scenarios", "E6"): "Bear/bull = base ∓ the spread in column F (blue). Spreads scale with the company: "
                         "the margin swing is a quarter of the base margin (1–5 points) and the growth swing 40% "
                         "of base growth (2–6 points). Capex, D&A and timing follow the DCF tab.",
    ("Summary", "A63"):'=IF(ABS(N(F60))>=0.1,"⚠ Base case moved "&TEXT(F60,"+0%;-0%")&" since the last '
                        'report — see Methodology (Model changes) for why.","")',
    ("Financials", "A2"):'="$ in billions except per-share data · "&Data!$Q$2&"\'s fiscal year ends "&Data!$Q$6',
    ("Methodology", "B6"): None,
    ("Valuation_History", "C17"): "=AVERAGE(C6:C14)",
    ("Valuation_History", "D17"): "=AVERAGE(D6:D14)",
    ("Valuation_History", "E17"): "=AVERAGE(E6:E14)",
    ("Valuation_History", "F17"): "=AVERAGE(F6:F14)",
    ("Scenarios", "B54"): "=MIN(Valuation_History!D6:D14)*Financials!F27",
    ("Scenarios", "C54"): "=MAX(Valuation_History!D6:D14)*Financials!F27",
}

GUARD = [("Financials", "L6:M6"), ("Financials", "B7:M12"),("Financials", "B23:F23"), ("Financials", "F27"),
         ("Multiples", "B5:C37"), ("Peers", "B6:K6"), ("Peers", "B11:K11"),
         ("Valuation_History", "B15:F17"), ("Capital_Returns", "B6:G16"), ("Segments", "B16:B21"),
         ("Segments", "D12"), ("Segments", "G12"), ("Summary", "B9:C32")]

_LEAK = re.compile(r"Alphabet|Google|GOOGL|\bTAC\b|antitrust|Wyatt|Stratton|Class A|Gemini|Waymo|"
                   r"9\.44|4\.16%|12\.23|\$18B|\$446B|20[12]\d")


def _cells(ws, spec):
    if ":" in spec:
        for row in ws[spec]:
            for c in row:
                yield c
    else:
        yield ws[spec]


def main():
    wb = load_workbook(REF)
    if "Website_Spec" in wb.sheetnames:
        del wb["Website_Spec"]
    for ws in wb.worksheets:
        ws._charts = []
        ws._images = []
        for row in ws.iter_rows():
            for c in row:
                if type(c).__name__ == "MergedCell":
                    continue
                c.comment = None
                if c.hyperlink is not None and "://" in str(c.hyperlink.target or ""):
                    c.hyperlink = None
    # Print footers name the company: leave a placeholder excel_valuation fills.
    for ws in wb.worksheets:
        for part in ("oddFooter", "evenFooter", "firstFooter", "oddHeader", "evenHeader", "firstHeader"):
            hf = getattr(ws, part)
            for k in ("left", "center", "right"):
                side = getattr(hf, k)
                if side.text and re.search(r"Alphabet|GOOGL|Google", side.text):
                    side.text = re.sub(r"^.*?equity research", "{COMPANY} equity research", side.text)
    for sheet, specs in CLEAR.items():
        ws = wb[sheet]
        for spec in specs:
            for c in _cells(ws, spec):
                if type(c).__name__ != "MergedCell":
                    c.value = None
                    c.hyperlink = None
    for (sheet, addr), f in FORMULAS.items():
        wb[sheet][addr].value = f
    # Ratios read "n/a" rather than an Excel error when an input is missing (a
    # bank has no free cash flow; a company without peers has no median).
    for sheet, rng in GUARD:
        for c in _cells(wb[sheet], rng):
            v = c.value
            if (isinstance(v, str) and v.startswith("=") and not v.upper().startswith("=IFERROR(")
                    and not v.upper().startswith("=IF(")):
                c.value = f'=IFERROR({v[1:]},"n/a")'

    # The reference's dropdown bug: a Yes/No list on DCF!B18:B22, which hold
    # numbers. Keep the real lists, replace the rest with number rules.
    dcf = wb["DCF"]
    keep = []
    for dv in dcf.data_validations.dataValidation:
        cells = {str(r) for r in dv.sqref.ranges}
        if dv.type == "list" and "Yes" in str(dv.formula1):
            cells = {c for c in cells if c in ("B12", "B13", "B16")}
            if not cells:
                continue
            dv.sqref = type(dv.sqref)(" ".join(sorted(cells)))
        keep.append(dv)
    dcf.data_validations.dataValidation = keep
    pct = DataValidation(type="decimal", operator="between", formula1="0", formula2="1",
                         allow_blank=True, showErrorMessage=True, errorTitle="Out of range",
                         error="Enter a fraction between 0 and 1 (e.g. 0.12 for 12%).")
    pct.add("B18:B21")
    dcf.data_validations.append(pct)
    guide = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0",
                           allow_blank=True, showErrorMessage=True, errorTitle="Capex guidance",
                           error="Enter next year's capex in $ billions, or leave blank.")
    guide.add("B22")
    dcf.data_validations.append(guide)

    __import__("doc_props").stamp(wb)
    wb.save(OUT)
    # Nothing company-specific may survive.
    chk = load_workbook(OUT)
    hits = []
    for ws in chk.worksheets:
        for part in ("oddFooter", "evenFooter", "firstFooter", "oddHeader", "evenHeader", "firstHeader"):
            for k in ("left", "center", "right"):
                txt = getattr(getattr(ws, part), k).text or ""
                if _LEAK.search(txt):
                    hits.append(f"{ws.title} {part}.{k}: {txt[:80]}")
        for row in ws.iter_rows():
            for c in row:
                v = getattr(c.value, "text", c.value)
                if isinstance(v, str) and not v.startswith("=") and _LEAK.search(v):
                    hits.append(f"{ws.title}!{c.coordinate}: {v[:80]}")
    if hits:
        print("LEAKS:\n  " + "\n  ".join(hits))
        return 1
    print("wrote", OUT, chk.sheetnames)
    _export_charts()
    return 0


def _export_charts():
    z = zipfile.ZipFile(REF)
    os.makedirs(CHARTS, exist_ok=True)
    for name in ("xl/charts/chart1.xml", "xl/charts/chart2.xml"):
        x = z.read(name).decode("utf-8")
        x = re.sub(r"<c:(num|str)Cache>.*?</c:\1Cache>", "", x, flags=re.S)
        x = re.sub(r"<c:userShapes[^>]*/>", "", x)
        x = re.sub(r"<c:externalData[^>]*>.*?</c:externalData>", "", x, flags=re.S)
        if "<c:lineChart>" in x:
            x = re.sub(r"(<a:t>)[^<]*price[^<]*(</a:t>)", r"\g<1>{TITLE}\g<2>", x, count=1)
            out = "price_line.xml"
        else:
            out = "football.xml"
        assert not any(_LEAK.search(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", x)), out
        open(os.path.join(CHARTS, out), "w", encoding="utf-8").write(x)
        print("chart", name, "->", out, len(x))


if __name__ == "__main__":
    sys.exit(main())
