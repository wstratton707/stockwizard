"""Curated industry peer groups.

Peers used to be "same broad sector, nearest market cap". For Apple that meant
Nvidia, Broadcom and Micron - chipmakers that share a sector label and a size
bracket with Apple and nothing else. A reader comparing Apple's multiples wants
the companies it competes with for the same customers and the same investors.

The broad sectors come from the ranked universe; the industries do not exist
anywhere in our data, so they are written down here. Each group lists US
filers whose statements are in SEC XBRL (us-gaap), because a peer the
fundamentals pipeline cannot read is a blank row. Samsung and Sony, the obvious
hardware comparables for Apple, file under IFRS or not at all with the SEC and
are left out for that reason.

A ticker in several groups takes the first. Names not listed fall back to the
sector-and-size rule in portfolio_data.suggest_peers.
"""

# Order matters: the first group containing a ticker is its group.
INDUSTRY_GROUPS = {
    "Mega-cap platforms":         ["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
    "Semiconductors":             ["NVDA", "AVGO", "AMD", "QCOM", "TXN", "INTC", "MU",
                                   "ADI", "MRVL", "NXPI", "MCHP", "ON"],
    "Semiconductor equipment":    ["AMAT", "LRCX", "KLAC", "TER"],
    "Software":                   ["ORCL", "CRM", "ADBE", "NOW", "INTU", "WDAY",
                                   "SNPS", "CDNS", "PLTR", "ADSK", "SNOW", "DDOG", "MDB",
                                   "NET", "TEAM"],
    "Cybersecurity":              ["PANW", "CRWD", "FTNT", "ZS", "OKTA"],
    "IT hardware & networking":   ["CSCO", "ANET", "DELL", "HPQ", "HPE", "STX", "WDC",
                                   "KEYS"],
    "IT services":                ["IBM", "ACN", "CTSH", "IT", "EPAM"],
    "Streaming & entertainment":  ["NFLX", "DIS", "CMCSA", "ROKU"],
    "Autos & EVs":                ["TSLA", "GM", "F", "RIVN", "LCID"],
    "Big-box & warehouse retail": ["WMT", "COST", "TGT", "DG", "DLTR", "BJ"],
    "Home improvement":           ["HD", "LOW"],
    "Off-price & apparel retail": ["TJX", "ROST", "BURL", "GAP"],
    "Apparel & footwear":         ["NKE", "LULU", "DECK", "VFC", "UAA"],
    "Restaurants":                ["MCD", "SBUX", "CMG", "YUM", "DRI", "QSR"],
    "Beverages":                  ["KO", "PEP", "KDP", "MNST"],
    "Household products":         ["PG", "CL", "KMB", "CHD", "CLX"],
    "Packaged food":              ["MDLZ", "GIS", "HSY", "KHC", "CPB", "CAG"],
    "Tobacco":                    ["PM", "MO"],
    "Money-center banks":         ["JPM", "BAC", "WFC", "C"],
    "Regional banks":             ["USB", "PNC", "TFC", "FITB", "MTB", "KEY", "RF"],
    "Investment banks & brokers": ["GS", "MS", "SCHW", "IBKR", "RJF"],
    "Payments & cards":           ["V", "MA", "AXP", "PYPL", "COF"],
    "Asset managers":             ["BLK", "BX", "KKR", "APO", "TROW"],
    "Property & casualty insurance": ["PGR", "TRV", "ALL", "CB", "AIG", "HIG"],
    "Pharmaceuticals":            ["LLY", "JNJ", "MRK", "ABBV", "PFE", "BMY"],
    "Biotechnology":              ["AMGN", "GILD", "VRTX", "REGN", "BIIB"],
    "Medical devices":            ["ABT", "MDT", "SYK", "BSX", "ISRG", "EW"],
    "Managed care":               ["UNH", "ELV", "CI", "HUM", "CVS", "CNC"],
    "Oil & gas":                  ["XOM", "CVX", "COP", "EOG", "OXY", "DVN", "FANG"],
    "Aerospace & defense":        ["BA", "LMT", "RTX", "NOC", "GD", "LHX"],
    "Machinery":                  ["CAT", "DE", "PCAR", "CMI"],
    "Railroads":                  ["UNP", "CSX", "NSC"],
    "Airlines":                   ["DAL", "UAL", "AAL", "LUV"],
    "Parcel & logistics":         ["UPS", "FDX"],
    "Telecom":                    ["T", "VZ", "TMUS"],
    "Electric utilities":         ["NEE", "DUK", "SO", "D", "AEP", "EXC"],
}

# The class-share line people search for maps to the same company.
_ALIASES = {"GOOG": "GOOGL", "BRK.A": "BRK.B", "BRK-B": "BRK.B"}


def canonical(ticker):
    t = (ticker or "").strip().upper()
    return _ALIASES.get(t, t)


def peer_group_for(ticker):
    """(group name, the OTHER members) for `ticker`, or None when not curated."""
    t = canonical(ticker)
    for name, members in INDUSTRY_GROUPS.items():
        if t in members:
            return name, [m for m in members if m != t]
    return None
