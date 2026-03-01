import dotenv from 'dotenv';
dotenv.config();

export const settings = {
  // Server
  port: process.env.PORT || 3000,
  env: process.env.NODE_ENV || 'development',

  // API Rate Limiting (Yahoo Finance)
  api: {
    requestDelayMs: 1500,     // Delay between API calls (increased for rate limiting)
    maxRetries: 3,
    cacheMinutes: 15         // Cache quotes for 15 minutes
  },

  // Email Notifications
  email: {
    enabled: !!process.env.EMAIL_USER,
    user: process.env.EMAIL_USER,
    pass: process.env.EMAIL_PASS,
    to: process.env.EMAIL_USER  // Send to self by default
  },

  // Telegram Notifications
  telegram: {
    enabled: !!process.env.TELEGRAM_BOT_TOKEN,
    botToken: process.env.TELEGRAM_BOT_TOKEN,
    chatId: process.env.TELEGRAM_CHAT_ID
  },

  // Alert Settings
  alerts: {
    checkIntervalMinutes: parseInt(process.env.ALERT_CHECK_INTERVAL_MINUTES) || 60,
    priceChangeThreshold: 5,  // Alert if price changes more than 5%
  },

  // Scheduler
  scheduler: {
    dailyReportTime: '16:30',   // 4:30 PM IST (after market close)
    timezone: 'Asia/Kolkata'
  },

  // Analysis Settings
  analysis: {
    rsiPeriod: 14,
    macdFast: 12,
    macdSlow: 26,
    macdSignal: 9,
    smaShortPeriod: 20,
    smaLongPeriod: 50,
    correlationDays: 90
  }
};
