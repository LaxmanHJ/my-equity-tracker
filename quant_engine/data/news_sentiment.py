import os
import requests
from textblob import TextBlob
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

class NewsSentimentFetcher:
    def __init__(self):
        self.api_key = os.getenv("NEWS_API_KEY") # User needs to provide this in .env
        self.base_url = "https://newsapi.org/v2/everything"
        
    def fetch_sentiment_for_stock(self, company_name: str, days_back: int = 7):
        """
        Fetches recent news for a company and computes an NLP sentiment score.
        Returns a score between -100 (extremely negative) and +100 (extremely positive).
        """
        if not self.api_key:
            print("WARNING: NEWS_API_KEY not found in environment variables. Returning neutral sentiment.")
            return {'score': 0, 'articles_analyzed': 0}

        from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        params = {
            'q': f'"{company_name}" AND (stock OR market OR finance OR NSE OR BSE)',
            'from': from_date,
            'language': 'en',
            'sortBy': 'relevancy',
            'apiKey': self.api_key,
            'pageSize': 20 # Limit to top 20 recent relevant articles
        }
        
        try:
            response = requests.get(self.base_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                articles = data.get('articles', [])
                
                if not articles:
                    return {'score': 0, 'articles_analyzed': 0}
                
                total_polarity = 0
                for article in articles:
                    title = article.get('title', '')
                    description = article.get('description', '')
                    text_to_analyze = f"{title}. {description}"
                    
                    # TextBlob polarity ranges from -1.0 to 1.0
                    analysis = TextBlob(text_to_analyze)
                    total_polarity += analysis.sentiment.polarity
                    
                avg_polarity = total_polarity / len(articles)
                # Map -1.0 to 1.0 range to -100 to +100 score
                score = round(avg_polarity * 100, 2)
                
                return {
                    'score': score,
                    'articles_analyzed': len(articles)
                }
            else:
                print(f"Failed to fetch news. Status: {response.status_code}")
        except Exception as e:
             print(f"Error fetching news sentiment: {e}")
             
        return {'score': 0, 'articles_analyzed': 0}

if __name__ == '__main__':
    fetcher = NewsSentimentFetcher()
    # Test with a major stock
    print("Testing News Sentiment Fetcher (Requires valid NEWS_API_KEY)...")
    result = fetcher.fetch_sentiment_for_stock("Reliance Industries")
    print(result)
