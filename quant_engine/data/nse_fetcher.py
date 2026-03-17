import requests
import time
import json

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
