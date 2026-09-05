// tools/lead_ops/server/index.js
// Express Server + Baileys WhatsApp Daemon + Telegram Bot Controller (24/7 Render Cloud)

const express = require('express');
const path = require('path');
const fs = require('fs');
const { createClient } = require('@supabase/supabase-js');
const setupBotController = require('./bot_controller');
const WAClient = require('./wa_client');

// Load environment variables (.env.local fallback for local test)
const envLocalPath = path.resolve(__dirname, '../../../.env.local');
if (fs.existsSync(envLocalPath)) {
  const envContent = fs.readFileSync(envLocalPath, 'utf8');
  for (const line of envContent.split('\n')) {
    if (line.includes('=') && !line.trim().startsWith('#')) {
      const [k, ...v] = line.split('=');
      const key = k.trim();
      const val = v.join('=').trim().replace(/^["']|["']$/g, '');
      if (!process.env[key]) process.env[key] = val;
    }
  }
}

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const BOT_TOKEN = process.env.LEAD_HUNTER_BOT_TOKEN;
const CHAT_ID = process.env.LEAD_HUNTER_CHAT_ID;
const PORT = process.env.PORT || 10000;
const ROOT_DIR = path.resolve(__dirname, '../../..');

if (!SUPABASE_URL || !SUPABASE_KEY || !BOT_TOKEN || !CHAT_ID) {
  console.error('[FATAL] Missing required credentials in environment variables.');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
const app = express();
app.use(express.json());

// Express Health & Status endpoints for Cloudflare Keepalive Pinger
app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    uptime_seconds: Math.floor(process.uptime()),
    service: 'lead-ops-daemon',
    region: 'singapore'
  });
});

app.get('/status', (req, res) => {
  res.status(200).json({
    wa_connected: waClient.isConnected,
    wa_user: waClient.userJid,
    queue_size: waClient.queue.length,
    timestamp: new Date().toISOString()
  });
});

// Setup WA Client & Bot Controller
const authDir = path.join(__dirname, 'auth_info');
const waClient = new WAClient({
  supabase,
  bot: null, // Will assign below
  chatId: CHAT_ID,
  authDir
});

const bot = setupBotController({
  botToken: BOT_TOKEN,
  chatId: CHAT_ID,
  supabase,
  waClient,
  rootDir: ROOT_DIR
});

// Attach bot to waClient
waClient.bot = bot;

// Start Services
async function start() {
  console.log('=== MEMULAI LEAD OPS DAEMON 24/7 (RENDER SINGAPORE) ===');
  
  // 1. Launch Bot Polling
  console.log('[Telegram] Launching bot polling...');
  bot.launch().then(() => {
    console.log('[Telegram] Bot polling active for @hunterdev99_bot.');
  }).catch((err) => {
    console.error('[Telegram] Bot launch error:', err.message);
  });

  // 2. Launch Express HTTP Server
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[Express] HTTP Server listening on port ${PORT}.`);
  });

  // 3. Initialize WhatsApp Socket
  await waClient.init();
}

// Graceful shutdown
process.once('SIGINT', () => {
  bot.stop('SIGINT');
  process.exit(0);
});
process.once('SIGTERM', () => {
  bot.stop('SIGTERM');
  process.exit(0);
});

start().catch((err) => {
  console.error('[FATAL] Unhandled error during startup:', err);
  process.exit(1);
});
