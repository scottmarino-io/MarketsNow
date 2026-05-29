"""Ticker universe definitions — S&P 100 + Nasdaq 100, combined and deduplicated (~170 tickers)."""

SP100 = [
    "AAPL","ABBV","ABT","ACN","ADBE","AIG","AMD","AMGN","AMZN","APD",
    "AXP","BA","BAC","BK","BKNG","BLK","BMY","BRK-B","C","CAT",
    "CL","CMCSA","COF","COP","COST","CRM","CSCO","CVS","CVX","DE",
    "DHR","DIS","DUK","EMR","ETN","EXC","F","FDX","GD","GE",
    "GILD","GM","GOOGL","GS","HD","HON","IBM","INTC","INTU","ISRG",
    "JNJ","JPM","KO","LIN","LLY","LOW","MA","MCD","MDT","MET",
    "META","MMC","MMM","MO","MRK","MS","MSFT","NEE","NFLX","NKE",
    "NVDA","ORCL","PEP","PFE","PG","PLD","PM","PYPL","QCOM","RTX",
    "SBUX","SCHW","SO","SPGI","T","TGT","TMO","TMUS","TXN","UNH",
    "UNP","UPS","USB","V","VZ","WFC","WMT","XOM","CVX","LLY",
]

NASDAQ100 = [
    "AAPL","ABNB","ADBE","ADI","ADP","ADSK","AEP","AMAT","AMD","AMGN",
    "AMZN","ASML","AVGO","BIIB","BKNG","CDNS","CEG","CHTR","CMCSA","COST",
    "CPRT","CRWD","CSCO","CSX","CTAS","CTSH","DASH","DDOG","DLTR","DXCM",
    "EA","EXC","FAST","FTNT","GEHC","GILD","GOOGL","HON","IDXX","ILMN",
    "INTC","INTU","ISRG","KDP","KHC","KLAC","LRCX","LULU","MAR","MCHP",
    "MDLZ","MELI","META","MNST","MRK","MRVL","MSFT","MU","NFLX","NVDA",
    "NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR","PEP","PYPL","QCOM",
    "REGN","ROP","ROST","SBUX","SNPS","TEAM","TMUS","TSLA","TTD","TXN",
    "VRSK","VRTX","WDAY","XEL","ZS","ZM","AXON","BKR","CDW","CSGP",
]

COMBINED = sorted(set(SP100 + NASDAQ100))

SECTOR_OVERRIDES = {
    "BRK-B": "Financials",
    "ASML":  "Technology",
    "MELI":  "Consumer Cyclical",
    "DASH":  "Technology",
    "DDOG":  "Technology",
    "TTD":   "Technology",
    "AXON":  "Technology",
    "CRWD":  "Technology",
    "ZS":    "Technology",
    "ZM":    "Technology",
    "WDAY":  "Technology",
    "TEAM":  "Technology",
    "ABNB":  "Consumer Cyclical",
    "LULU":  "Consumer Cyclical",
    "MAR":   "Consumer Cyclical",
    "PCAR":  "Industrials",
    "BKR":   "Energy",
    "CDW":   "Technology",
}
