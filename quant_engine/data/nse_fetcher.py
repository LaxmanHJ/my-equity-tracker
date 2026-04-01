import requests
import time
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

class NSEFetcher:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.nseindia.com/'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self._initialize_session()

    def _initialize_session(self):
        """Hit the main page to get necessary cookies"""
        try:
            self.session.get('https://www.nseindia.com', timeout=10)
        except Exception as e:
            print(f"Failed to initialize session: {e}")

    def fetch_vix_history(self, from_date: date, to_date: date) -> list[dict]:
        """
        Fetch historical India VIX from NSE's API.

        NSE requires the session to have visited the VIX chart page before
        the historical API will respond — we warm it up here automatically.

        NSE limits each request to ~1 year, so large ranges are split into
        yearly chunks automatically.

        Args:
            from_date: Start date (inclusive).
            to_date:   End date (inclusive).

        Returns:
            List of {"date": "YYYY-MM-DD", "vix": float} dicts, sorted ascending.
            Empty list on failure.
        """
        from datetime import datetime

        # Warm up the VIX chart page so NSE sets the right cookies/Referer
        vix_page = "https://www.nseindia.com/market-data/india-vix"
        try:
            self.session.get(vix_page, timeout=10)
            time.sleep(1)
        except Exception as exc:
            logger.warning("Could not warm up VIX page: %s", exc)

        # Update Referer to the VIX page for subsequent API calls
        self.session.headers.update({"Referer": vix_page})

        url      = "https://www.nseindia.com/api/historical/vixhistory"
        all_rows = []

        # Split into ~11-month chunks to stay within NSE's per-request limit
        chunk_start = from_date
        while chunk_start <= to_date:
            chunk_end = min(
                date(chunk_start.year + 1, chunk_start.month, chunk_start.day) - timedelta(days=1),
                to_date,
            )

            from_str = chunk_start.strftime("%d-%m-%Y")
            to_str   = chunk_end.strftime("%d-%m-%Y")

            try:
                resp = self.session.get(
                    url,
                    params={"data": "byDate", "fromDate": from_str, "toDate": to_str},
                    timeout=15,
                )

                if resp.status_code != 200:
                    logger.warning(
                        "NSE VIX history returned HTTP %d for %s→%s — retrying with fresh session",
                        resp.status_code, from_str, to_str,
                    )
                    time.sleep(2)
                    self._initialize_session()
                    time.sleep(1)
                    chunk_start = chunk_end + timedelta(days=1)
                    continue

                data = resp.json().get("data", [])
                for row in data:
                    try:
                        raw_date = row.get("EOD_TIMESTAMP", "").strip()
                        close    = float(row.get("EOD_CLOSE_INDEX_VAL", 0))
                        if not raw_date or close <= 0:
                            continue
                        parsed = None
                        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
                            try:
                                parsed = datetime.strptime(raw_date, fmt).date()
                                break
                            except ValueError:
                                continue
                        if parsed:
                            all_rows.append({"date": str(parsed), "vix": round(close, 4)})
                    except (ValueError, TypeError):
                        continue

                logger.info("Fetched %d VIX rows for %s → %s", len(data), from_str, to_str)
                time.sleep(1)

            except Exception as exc:
                logger.warning("Failed to fetch VIX chunk %s→%s: %s", from_str, to_str, exc)
                time.sleep(2)

            chunk_start = chunk_end + timedelta(days=1)

        all_rows.sort(key=lambda r: r["date"])
        return all_rows

    def fetch_vix(self):
        """Fetch current India VIX"""
        url = "https://www.nseindia.com/api/allIndices"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for idx_data in data.get('data', []):
                    if idx_data.get('indexSymbol') == 'INDIA VIX':
                        return {
                            'vix': idx_data.get('last'),
                            'percentChange': idx_data.get('percentChange'),
                            'timestamp': data.get('timestamp')
                        }
            else:
                print(f"Failed to fetch VIX data. Status: {response.status_code}")
                # Sometimes NSE needs a brief pause before sub-requests
                time.sleep(1)
                self._initialize_session()
        except Exception as e:
            print(f"Error fetching VIX: {e}")
        return None

    def fetch_nifty_oi(self):
        """Fetch Nifty Open Interest Data (Put-Call Ratio)"""
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if 'filtered' in data:
                    filtered_data = data['filtered']
                    tot_ce_oi = filtered_data.get('CE', {}).get('totOI', 0)
                    tot_pe_oi = filtered_data.get('PE', {}).get('totOI', 0)
                    
                    pcr = tot_pe_oi / tot_ce_oi if tot_ce_oi > 0 else 0
                    return {
                        'total_ce_oi': tot_ce_oi,
                        'total_pe_oi': tot_pe_oi,
                        'pcr': round(pcr, 4),
                        'timestamp': data.get('records', {}).get('timestamp')
                    }
            else:
                 print(f"Failed to fetch OI data. Status: {response.status_code}")
                 time.sleep(1)
                 self._initialize_session()       
        except Exception as e:
            print(f"Error fetching NIFTY OI: {e}")
        return None

if __name__ == '__main__':
    fetcher = NSEFetcher()
    time.sleep(1) # wait between init and api call
    print("Fetching VIX...")
    vix_data = fetcher.fetch_vix()
    print(vix_data)
    time.sleep(1)
    print("Fetching NIFTY OI...")
    oi_data = fetcher.fetch_nifty_oi()
    print(oi_data)
