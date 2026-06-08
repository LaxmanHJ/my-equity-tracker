/**
 * Sector classification for NSE F&O underlyings.
 *
 * Angel's gainersLosers API returns derivatives symbols (e.g. "MAXHEALTH30JUN26FUT").
 * The scrip master carries no sector field, so this static map provides a sector for
 * the market-movers widget. Keyed by the base (underlying) symbol, upper-cased.
 *
 * Unmapped symbols fall back to "—" in the API layer. Extend this map as new names
 * enter the F&O list.
 */
export const FNO_SECTOR_MAP = {
  // Banking & Financials
  HDFCBANK: 'Banking', ICICIBANK: 'Banking', AXISBANK: 'Banking', SBIN: 'Banking',
  KOTAKBANK: 'Banking', INDUSINDBK: 'Banking', BANKBARODA: 'Banking', PNB: 'Banking',
  FEDERALBNK: 'Banking', IDFCFIRSTB: 'Banking', AUBANK: 'Banking', BANDHANBNK: 'Banking',
  CANBK: 'Banking', RBLBANK: 'Banking',
  BAJFINANCE: 'Financial Services', BAJAJFINSV: 'Financial Services', CHOLAFIN: 'Financial Services',
  SBICARD: 'Financial Services', MUTHOOTFIN: 'Financial Services', SHRIRAMFIN: 'Financial Services',
  PFC: 'Financial Services', RECLTD: 'Financial Services', LICHSGFIN: 'Housing Finance',
  HDFCLIFE: 'Insurance', SBILIFE: 'Insurance', ICICIPRULI: 'Insurance', ICICIGI: 'Insurance',
  LICI: 'Insurance', HDFCAMC: 'Financial Services', JIOFIN: 'Financial Services',
  IEX: 'Financial Services', BSE: 'Financial Services', ANGELONE: 'Financial Services',
  CDSL: 'Financial Services', MANAPPURAM: 'Financial Services', 'M&MFIN': 'Financial Services',
  LTF: 'Financial Services', IIFL: 'Financial Services', PNBHOUSING: 'Housing Finance',
  PEL: 'Financial Services', ABCAPITAL: 'Financial Services',

  // IT Services
  TCS: 'IT Services', INFY: 'IT Services', WIPRO: 'IT Services', HCLTECH: 'IT Services',
  TECHM: 'IT Services', LTIM: 'IT Services', LTTS: 'IT Services', PERSISTENT: 'IT Services',
  COFORGE: 'IT Services', MPHASIS: 'IT Services', OFSS: 'IT Services',

  // Pharma & Healthcare
  SUNPHARMA: 'Pharma', DRREDDY: 'Pharma', CIPLA: 'Pharma', DIVISLAB: 'Pharma',
  AUROPHARMA: 'Pharma', LUPIN: 'Pharma', ALKEM: 'Pharma', TORNTPHARM: 'Pharma',
  BIOCON: 'Pharma', GLENMARK: 'Pharma', ZYDUSLIFE: 'Pharma', LAURUSLABS: 'Pharma',
  GRANULES: 'Pharma', IPCALAB: 'Pharma', ABBOTINDIA: 'Pharma', SYNGENE: 'Pharma',
  APOLLOHOSP: 'Healthcare', MAXHEALTH: 'Healthcare', FORTIS: 'Healthcare', LALPATHLAB: 'Healthcare',
  METROPOLIS: 'Healthcare',

  // Auto & Auto Components
  MARUTI: 'Auto', TATAMOTORS: 'Auto', 'M&M': 'Auto', 'BAJAJ-AUTO': 'Auto',
  HEROMOTOCO: 'Auto', EICHERMOT: 'Auto', TVSMOTOR: 'Auto', ASHOKLEY: 'Auto',
  BOSCHLTD: 'Auto Components', MOTHERSON: 'Auto Components', BHARATFORG: 'Auto Components',
  BALKRISIND: 'Auto Components', MRF: 'Auto Components', APOLLOTYRE: 'Auto Components',
  EXIDEIND: 'Auto Components', TIINDIA: 'Auto Components',

  // FMCG & Consumer
  HINDUNILVR: 'FMCG', ITC: 'FMCG', NESTLEIND: 'FMCG', BRITANNIA: 'FMCG',
  DABUR: 'FMCG', MARICO: 'FMCG', GODREJCP: 'FMCG', COLPAL: 'FMCG',
  TATACONSUM: 'FMCG', UBL: 'FMCG', UNITDSPR: 'FMCG', VBL: 'FMCG',
  PGHH: 'FMCG', EMAMILTD: 'FMCG',
  TITAN: 'Consumer Durables', HAVELLS: 'Consumer Durables', VOLTAS: 'Consumer Durables',
  CROMPTON: 'Consumer Durables', DIXON: 'Consumer Durables', WHIRLPOOL: 'Consumer Durables',
  BLUESTARCO: 'Consumer Durables', KAJARIACER: 'Consumer Durables',
  TRENT: 'Retail', DMART: 'Retail', ABFRL: 'Retail', PAGEIND: 'Retail',
  NYKAA: 'Retail', JUBLFOOD: 'Retail', DEVYANI: 'Retail', KALYANKJIL: 'Retail',
  TITAGARH: 'Capital Goods',

  // Energy, Oil & Gas
  RELIANCE: 'Oil & Gas', ONGC: 'Oil & Gas', BPCL: 'Oil & Gas', IOC: 'Oil & Gas',
  HINDPETRO: 'Oil & Gas', GAIL: 'Oil & Gas', PETRONET: 'Oil & Gas', IGL: 'Oil & Gas',
  MGL: 'Oil & Gas', GUJGASLTD: 'Oil & Gas', OIL: 'Oil & Gas', ATGL: 'Oil & Gas',

  // Power & Utilities
  NTPC: 'Power & Utilities', POWERGRID: 'Power & Utilities', TATAPOWER: 'Power & Utilities',
  ADANIPOWER: 'Power & Utilities', ADANIENSOL: 'Power & Utilities', JSWENERGY: 'Power & Utilities',
  NHPC: 'Power & Utilities', SJVN: 'Power & Utilities', TORNTPOWER: 'Power & Utilities',
  CESC: 'Power & Utilities', IREDA: 'Power & Utilities', INOXWIND: 'Power & Utilities',
  SUZLON: 'Power & Utilities',

  // Metals & Mining
  TATASTEEL: 'Metals & Mining', JSWSTEEL: 'Metals & Mining', HINDALCO: 'Metals & Mining',
  VEDL: 'Metals & Mining', JINDALSTEL: 'Metals & Mining', SAIL: 'Metals & Mining',
  NMDC: 'Metals & Mining', NATIONALUM: 'Metals & Mining', HINDZINC: 'Metals & Mining',
  APLAPOLLO: 'Metals & Mining', COALINDIA: 'Metals & Mining',

  // Cement
  ULTRACEMCO: 'Cement', SHREECEM: 'Cement', GRASIM: 'Cement', AMBUJACEM: 'Cement',
  ACC: 'Cement', DALBHARAT: 'Cement', JKCEMENT: 'Cement', RAMCOCEM: 'Cement',

  // Infrastructure, Construction & Capital Goods
  LT: 'Capital Goods', SIEMENS: 'Capital Goods', ABB: 'Capital Goods', BHEL: 'Capital Goods',
  CUMMINSIND: 'Capital Goods', POLYCAB: 'Capital Goods', BEL: 'Defence', HAL: 'Defence',
  MAZDOCK: 'Defence', BDL: 'Defence', SOLARINDS: 'Defence',
  ADANIPORTS: 'Infrastructure', GMRAIRPORT: 'Infrastructure', IRB: 'Infrastructure',
  CONCOR: 'Infrastructure', NBCC: 'Infrastructure', RVNL: 'Infrastructure', IRFC: 'Infrastructure',

  // Telecom & Media
  BHARTIARTL: 'Telecom', IDEA: 'Telecom', INDUSTOWER: 'Telecom', TATACOMM: 'Telecom',
  ZEEL: 'Media', SUNTV: 'Media', PVRINOX: 'Media', NETWORK18: 'Media',

  // Chemicals & Fertilisers
  PIDILITIND: 'Chemicals', SRF: 'Chemicals', UPL: 'Chemicals', PIIND: 'Chemicals',
  AARTIIND: 'Chemicals', DEEPAKNTR: 'Chemicals', NAVINFLUOR: 'Chemicals', TATACHEM: 'Chemicals',
  ATUL: 'Chemicals', COROMANDEL: 'Fertilisers', CHAMBLFERT: 'Fertilisers', GNFC: 'Fertilisers',

  // Real Estate
  DLF: 'Real Estate', GODREJPROP: 'Real Estate', OBEROIRLTY: 'Real Estate',
  PRESTIGE: 'Real Estate', LODHA: 'Real Estate', PHOENIXLTD: 'Real Estate',

  // Diversified / Other
  ADANIENT: 'Diversified', ABCAPITAL: 'Financial Services', BANKINDIA: 'Banking',
  INDHOTEL: 'Hospitality', IRCTC: 'Travel & Tourism', DELHIVERY: 'Logistics',
  ASTRAL: 'Building Materials', SUPREMEIND: 'Building Materials', INDIGO: 'Aviation',
  HUDCO: 'Housing Finance', POLICYBZR: 'Financial Services', PAYTM: 'Financial Services',
  CAMS: 'Financial Services', KEI: 'Capital Goods', CGPOWER: 'Capital Goods',
  HFCL: 'Telecom', GODREJIND: 'Diversified', BERGEPAINT: 'Paints', ASIANPAINT: 'Paints',
  KPITTECH: 'IT Services', TATAELXSI: 'IT Services', CYIENT: 'IT Services',

  // Supplementary F&O underlyings (filled from live FUTSTK list)
  INDIANB: 'Banking', YESBANK: 'Banking', UNIONBANK: 'Banking',
  BAJAJHLDNG: 'Financial Services', MOTILALOFS: 'Financial Services', KFINTECH: 'Financial Services',
  'NAM-INDIA': 'Financial Services', SAMMAANCAP: 'Financial Services', MCX: 'Financial Services',
  '360ONE': 'Financial Services', NUVAMA: 'Financial Services', MFSL: 'Insurance',
  FORCEMOT: 'Auto', HYUNDAI: 'Auto', TMPV: 'Auto',
  SONACOMS: 'Auto Components', UNOMINDA: 'Auto Components',
  MANKIND: 'Pharma', PATANJALI: 'FMCG', RADICO: 'FMCG', GODFRYPHLP: 'FMCG',
  ADANIGREEN: 'Power & Utilities', WAAREEENER: 'Power & Utilities', PREMIERENE: 'Power & Utilities',
  POWERINDIA: 'Power & Utilities', 'GVT&D': 'Capital Goods', KAYNES: 'Capital Goods',
  PGEL: 'Consumer Durables', AMBER: 'Consumer Durables',
  NAUKRI: 'Internet', ETERNAL: 'Internet', SWIGGY: 'Internet',
  COCHINSHIP: 'Defence',
};

/**
 * Strip an Angel derivatives trading symbol down to its base underlying.
 *   "MAXHEALTH30JUN26FUT" -> "MAXHEALTH"
 *   "NIFTY26JUN24FUT"     -> "NIFTY"
 * Falls back to returning the input (upper-cased, suffixes trimmed) if no match.
 */
export function baseSymbolFromTrading(tradingSymbol) {
  if (!tradingSymbol) return '';
  const s = String(tradingSymbol).toUpperCase().trim();
  // Futures: <BASE><dd><MON><yy>FUT
  const fut = s.match(/^(.+?)\d{2}[A-Z]{3}\d{2}FUT$/);
  if (fut) return fut[1];
  // Generic fallback: drop trailing -EQ / -FUT
  return s.replace(/-(EQ|FUT)$/i, '');
}

/**
 * Resolve a sector for an Angel derivatives trading symbol. Returns "—" when unknown.
 */
export function sectorForTradingSymbol(tradingSymbol) {
  const base = baseSymbolFromTrading(tradingSymbol);
  return FNO_SECTOR_MAP[base] || '—';
}

/**
 * Resolve a sector for a plain NSE equity symbol (e.g. "RELIANCE", "M&M",
 * "BAJAJ-AUTO", "RELIANCE-EQ"). Returns "—" when unknown.
 */
export function sectorForSymbol(symbol) {
  if (!symbol) return '—';
  const key = String(symbol).toUpperCase().trim().replace(/-EQ$/i, '');
  return FNO_SECTOR_MAP[key] || '—';
}

export default { FNO_SECTOR_MAP, baseSymbolFromTrading, sectorForTradingSymbol, sectorForSymbol };
