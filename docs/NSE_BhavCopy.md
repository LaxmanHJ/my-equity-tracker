                                                                                           
  ---                                                                                       
  Implementation Plan: NSE Data Integration                                                                               
  Architecture Overview                                                                                                
  - New data → Turso via Python backfill scripts (same pattern as backfill_regime.py)                                                                                                                          
  - New DB tables created in both db.js (Node.js schema init) and Python backfill scripts                                                                                                                      
  - New ML features added to trainer.py with corresponding loaders                                                                                                                                             
  - New factor added to composite.py for the live scoring engine                                                                                                                                               
                                                                                                                                                                                                               
  ---                                                                                                                                                                                                          
  Phase 1 — Delivery % + Circuit Events                                                                                                                                                                        
                                                                                                                                                                                                               
  Impact: Fix the weakest ML feature (volume, importance 0.021). No new API keys needed.
                                                                                                                                                                                                               
  1a. New DB table                                                                                                                                                                                             
                                                                                                                                                                                                               
  CREATE TABLE delivery_data (                                                                                                                                                                                 
    id       INTEGER PRIMARY KEY AUTOINCREMENT,                
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,
    delivery_qty     INTEGER,                                                                                                                                                                                  
    delivery_pct     REAL,
    circuit_hit      INTEGER DEFAULT 0,  -- 1 = upper circuit, -1 = lower circuit, 0 = none                                                                                                                    
    UNIQUE(symbol, date)                                                                                                                                                                                       
  )
  Add to db.js initDatabase() + add index on (symbol).                                                                                                                                                         
                                                                                                                                                                                                               
  1b. Backfill script: quant_engine/data/backfill_delivery.py                                                                                                                                                  
                                                                                                                                                                                                               
  python3 -m quant_engine.data.backfill_delivery --from 2022-01-01                                                                                                                                             
  - Downloads NSE Bhav Copy zip per trading day:
    - New format (post Jul 2024): nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYY}{MM}{DD}_F_0000.csv.zip                                                                                      
    - Old format: nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip        
  - Parse CSV → filter for portfolio symbols → extract DELIV_QTY, DELIV_PER, detect circuit (CLOSE == HIGH = upper, CLOSE == LOW = lower)                                                                      
  - Upsert to delivery_data                                                                                                                                                                                    
  - Respects --from date to avoid redundant downloads                                                                                                                                                          
                                                                                                                                                                                                               
  1c. New loader: quant_engine/data/delivery_loader.py                                                                                                                                                         
                                                               
  load_delivery_series(symbol, limit=365) → pd.DataFrame  # date, delivery_pct, circuit_hit                                                                                                                    
  load_circuit_status(symbol) → int  # 0, 1, -1 for today                                                                                                                                                      
                                                                                                                                                                                                               
  1d. New ML feature in trainer.py                                                                                                                                                                             
                                                                                                                                                                                                               
  Replace volume feature with delivery_score:                                                                                                                                                                  
  # delivery_score = rolling z-score of delivery_pct vs 60-day mean, clipped to [-1, +1]
  delivery_score = (delivery_pct - rolling_mean_60) / rolling_std_60                    
                                                                                                                                                                                                               
  1e. Circuit filter in composite.py                                                                                                                                                                           
                                                                                                                                                                                                               
  Before returning signal: if circuit_hit == -1 (lower circuit), override any LONG signal to HOLD.                                                                                                             
                                                                                                                                                                                                               
  ---                                                                                                                                                                                                          
  Phase 2 — Sector Indices                                     
                          
  Impact: Fix broken sector_rotation feature for single-stock sectors (TANLA, BAJAJHIND, etc.)
                                                                                                                                                                                                               
  2a. New DB table
                                                                                                                                                                                                               
  CREATE TABLE sector_indices (                                
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    index_name TEXT NOT NULL,                                                                                                                                                                                  
    close      REAL,
    pct_change REAL,                                                                                                                                                                                           
    UNIQUE(date, index_name)                                   
  )

  2b. Backfill script: quant_engine/data/backfill_sector_indices.py                                                                                                                                            
  
  python3 -m quant_engine.data.backfill_sector_indices --from 2022-01-01                                                                                                                                       
  - Downloads nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv per day
  - Single small file (~5KB), no auth required                                                                                                                                                                 
  - Parses all sector indices, upserts everything (not just portfolio sectors — store all, query selectively)
                                                                                                                                                                                                               
  2c. Industry → NSE Index mapping in config.py                                                                                                                                                                
                                                                                                                                                                                                               
  INDUSTRY_TO_NSE_INDEX = {                                                                                                                                                                                    
      "Information Technology": "NIFTY IT",                                                                                                                                                                    
      "Power":                  "NIFTY ENERGY",
      "Steel":                  "NIFTY METAL",                                                                                                                                                                 
      "Banking":                "NIFTY BANK",                  
      "Financial Services":     "NIFTY FINANCIAL SERVICES",                                                                                                                                                    
      "Chemicals":              "NIFTY CHEMICALS",                                                                                                                                                             
      # ... etc
  }                                                                                                                                                                                                            
                                                               
  2d. New loader: quant_engine/data/sector_indices_loader.py

  load_sector_series(index_name, limit=365) → pd.Series  # date → pct_change
                                                                                                                                                                                                               
  2e. Update sector_rotation feature in trainer.py                                                                                                                                                             
                                                                                                                                                                                                               
  Replace portfolio-computed sector averages with:                                                                                                                                                             
  # Per stock: look up its industry → NSE index → load 20d return of that index
  # sector_rotation = (sector_index_20d_return - nifty_20d_return) / scale     
                                                                                                                                                                                                               
  ---
  Phase 3 — FII/DII Flows + F&O OI                                                                                                                                                                             
                                                               
  Impact: Two new orthogonal market regime features. Requires NSE session cookie (same as nse_fetcher.py).                                                                                                     
                                                                                                                                                                                                               
  3a. New DB table (extend market_regime)
                                                                                                                                                                                                               
  -- Add columns to existing market_regime table:              
  ALTER TABLE market_regime ADD COLUMN fii_net_cash    REAL;  -- INR crore                                                                                                                                     
  ALTER TABLE market_regime ADD COLUMN dii_net_cash    REAL;  -- INR crore                                                                                                                                     
  ALTER TABLE market_regime ADD COLUMN fii_fo_net_long REAL;  -- futures long - short (contracts)                                                                                                              
  Single table keeps all regime features aligned by date — same pattern as VIX.                                                                                                                                
                                                                                                                                                                                                               
  3b. Backfill scripts                                                                                                                                                                                         
                                                                                                                                                                                                               
  quant_engine/data/backfill_fii_dii.py                                                                                                                                                                        
  - Historical CSV from nseindia.com/products/content/equities/equities/eq_fiidii_archives.htm (manual download initially, then daily via NSE API)
  - Live: https://www.nseindia.com/api/fiidiiTradeReact (JSON, NSE session)                                                                                                                                    
                                                               
  quant_engine/data/backfill_fo_oi.py                                                                                                                                                                          
  - archives.nseindia.com/content/nsccl/fao_participant_oi_{DDMMYYYY}.csv
  - Extract: FII Future Index Long − Future Index Short → net position                                                                                                                                         
                                                               
  3c. New ML features in trainer.py                                                                                                                                                                            
                                                               
  # fii_flow_score: rolling 10-day FII net cash, normalised to [-1, +1] via percentile rank                                                                                                                    
  # fii_fo_score:   FII futures net long, normalised via rolling percentile rank                                                                                                                               
  Both are date-level features (same value for all stocks on a given day), loaded from market_regime table — same pattern as vix_regime and nifty_trend.
                                                                                                                                                                                                               
  ---                                                          
  Phase 4 — Bulk Deals + Short Selling                                                                                                                                                                         
                                                               
  Impact: Alert triggers and contrarian signals. Lower ML value, higher operational value.
                                                                                                                                                                                                               
  4a. New DB tables
                                                                                                                                                                                                               
  CREATE TABLE bulk_block_deals (                              
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    client_name TEXT,                                                                                                                                                                                          
    trade_type  TEXT,   -- 'BUY' or 'SELL'
    quantity    INTEGER,                                                                                                                                                                                       
    price       REAL,                                          
    deal_type   TEXT,   -- 'BULK' or 'BLOCK'                                                                                                                                                                   
    UNIQUE(date, symbol, client_name, deal_type)
  );                                                                                                                                                                                                           
                                                               
  CREATE TABLE short_selling (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,                                                                                                                                                                                   
    date      TEXT NOT NULL,
    short_qty REAL,                                                                                                                                                                                            
    short_pct REAL,                                            
    UNIQUE(symbol, date)
  );

  4b. Backfill + live fetch                                                                                                                                                                                    
  
  - Bulk/Block deals: nseindia.com/api/bulk-deal and /api/block-deal (NSE session, daily)                                                                                                                      
  - Short selling: nsearchives.nseindia.com/content/equities/shortselling_{DDMMYYYY}.csv (weekly)
                                                                                                                                                                                                               
  4c. Integration with alert system                            
                                                                                                                                                                                                               
  Extend src/routes/api.js with new endpoints:                                                                                                                                                                 
  - GET /api/bulk-deals/:symbol — recent institutional activity for a stock
  - GET /api/short-selling/:symbol — short interest trend                                                                                                                                                      
                                                               
  Surface on the stock analysis page in the frontend.                                                                                                                                                          
                                                                                                                                                                                                               
  ---                                                                                                                                                                                                          
  Summary Timeline                                                                                                                                                                                             
                                                               
  ┌──────────────────────────┬─────────────────────────────────┬──────────────────────────────────────────────────────┬─────────────────────────────────────────────────────┬───────────┐
  │          Phase           │           New Tables            │                     New Scripts                      │                     ML Changes                      │  Effort   │                      
  ├──────────────────────────┼─────────────────────────────────┼──────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┼───────────┤
  │ 1 — Delivery % + Circuit │ delivery_data                   │ backfill_delivery.py, delivery_loader.py             │ Replace volume → delivery_score, add circuit filter │ ~1 day    │                      
  ├──────────────────────────┼─────────────────────────────────┼──────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┼───────────┤
  │ 2 — Sector Indices       │ sector_indices                  │ backfill_sector_indices.py, sector_indices_loader.py │ Fix sector_rotation feature                         │ ~half day │                      
  ├──────────────────────────┼─────────────────────────────────┼──────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┼───────────┤                      
  │ 3 — FII/DII + F&O OI     │ market_regime (new cols)        │ backfill_fii_dii.py, backfill_fo_oi.py               │ Add fii_flow_score, fii_fo_score                    │ ~1 day    │                      
  ├──────────────────────────┼─────────────────────────────────┼──────────────────────────────────────────────────────┼─────────────────────────────────────────────────────┼───────────┤                      
  │ 4 — Bulk + Short         │ bulk_block_deals, short_selling │ 2 backfill scripts                                   │ Alert triggers, new API endpoints                   │ ~1 day    │
  └──────────────────────────┴─────────────────────────────────┴──────────────────────────────────────────────────────┴─────────────────────────────────────────────────────┴───────────┘  