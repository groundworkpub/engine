// tools/lead_ops/server/wa_client.js
// Baileys WhatsApp Client with 24/7 Keepalive, Typing Simulation, and Inbound Auto-Pause

const pino = require('pino');
const path = require('path');
const QRCode = require('qrcode');
const { restoreSessionFromSupabase, backupSessionToSupabase } = require('./session_sync');

let makeWASocket, useMultiFileAuthState, DisconnectReason, delay;

async function loadBaileys() {
  if (!makeWASocket) {
    const b = await import('@whiskeysockets/baileys');
    makeWASocket = b.default || b.makeWASocket;
    useMultiFileAuthState = b.useMultiFileAuthState;
    DisconnectReason = b.DisconnectReason;
    delay = b.delay;
  }
}

class WAClient {
  constructor({ supabase, bot, chatId, authDir = './auth_info' }) {
    this.supabase = supabase;
    this.bot = bot;
    this.chatId = chatId;
    this.authDir = authDir;
    this.sock = null;
    this.isConnected = false;
    this.userJid = null;
    this.queue = [];
    this.isProcessingQueue = false;
    this.inboundReplyHandlers = new Map(); // Maps telegramMsgId -> { contactPhone, jid }
  }

  async init() {
    await loadBaileys();
    console.log('[WAClient] Initializing Baileys Socket...');
    await restoreSessionFromSupabase(this.supabase, 'default', this.authDir);

    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    this.state = state;
    this.saveCreds = saveCreds;

    this.sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      logger: pino({ level: 'silent' }),
      browser: ['Ubuntu', 'Chrome', '124.0.0.0'],
      syncFullHistory: false
    });

    this.sock.ev.on('creds.update', async () => {
      await saveCreds();
      await backupSessionToSupabase(this.supabase, 'default', this.authDir);
    });

    this.sock.ev.on('connection.update', async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('[WAClient] New QR code generated.');
        try {
          const qrBuffer = await QRCode.toBuffer(qr);
          await this.bot.telegram.sendPhoto(this.chatId, { source: qrBuffer }, {
            caption: '📷 <b>Scan QR Code WhatsApp</b>\n\nBuka WhatsApp > Perangkat Tertaut > Tautkan Perangkat. Atau gunakan perintah <code>/pair 08xxxxxxx</code> untuk pairing code 8 digit tanpa kamera.',
            parse_mode: 'HTML'
          });
        } catch (e) {
          console.error('[WAClient] Failed to send QR code to Telegram:', e.message);
        }
      }

      if (connection === 'close') {
        const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
        console.log('[WAClient] Connection closed. Reconnecting:', shouldReconnect);
        this.isConnected = false;
        if (shouldReconnect) {
          setTimeout(() => this.init(), 5000);
        } else {
          await this.bot.telegram.sendMessage(this.chatId, '⚠️ <b>WhatsApp Terputus / Logged Out</b>\nSilakan jalankan <code>/pair &lt;nomor_hp&gt;</code> untuk menautkan ulang.', { parse_mode: 'HTML' });
        }
      } else if (connection === 'open') {
        console.log('[WAClient] WhatsApp connected successfully!');
        this.isConnected = true;
        this.userJid = this.sock.user?.id;
        const myNum = this.userJid ? this.userJid.split(':')[0] : 'Unknown';
        const myName = this.sock.user?.name || 'Lead Hunter';

        await backupSessionToSupabase(this.supabase, 'default', this.authDir, myNum, myName);

        await this.bot.telegram.sendMessage(
          this.chatId,
          `🟢 <b>WhatsApp Berhasil Terhubung 24/7!</b>\n\n👤 <b>Nama:</b> ${myName}\n📱 <b>Nomor:</b> <code>+${myNum}</code>\n☁️ <b>Host:</b> Render Singapore\n🛡️ <b>Mode:</b> Anti-Ban Paced Active`,
          { parse_mode: 'HTML' }
        );
      }
    });

    // Inbound Messages Listener
    this.sock.ev.on('messages.upsert', async ({ messages, type }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        if (!msg.message || msg.key.fromMe) continue;

        const senderJid = msg.key.remoteJid;
        if (!senderJid || senderJid.endsWith('@g.us')) continue; // Ignore groups

        const rawPhone = senderJid.split('@')[0];
        const e164 = rawPhone.startsWith('62') ? `+${rawPhone}` : `+${rawPhone}`;

        const text = msg.message.conversation ||
                     msg.message.extendedTextMessage?.text ||
                     msg.message.imageMessage?.caption ||
                     '[Media / Dokumen WhatsApp]';

        console.log(`[WAClient] Inbound reply received from ${e164}: "${text}"`);
        await this.handleInboundReply(e164, text, senderJid);
      }
    });
  }

  async requestPairingCode(phoneNumber) {
    if (!this.sock) throw new Error('WhatsApp Socket belum siap.');
    const cleanNum = phoneNumber.replace(/[^0-9]/g, '');
    const code = await this.sock.requestPairingCode(cleanNum);
    console.log(`[WAClient] Pairing code generated: ${code}`);
    return code;
  }

  async handleInboundReply(phoneE164, text, jid) {
    try {
      // 1. Check if this phone exists in pipeline_leads
      const { data: leads } = await this.supabase
        .from('pipeline_leads')
        .select('*')
        .eq('contact_phone', phoneE164)
        .limit(1);

      const lead = leads && leads.length > 0 ? leads[0] : null;

      // 2. Immediately FREEZE / AUTO-PAUSE bot on this lead
      if (lead) {
        await this.supabase
          .from('pipeline_leads')
          .update({
            status: 'replied',
            contact_wa_status: 'active_replied',
            updated_at: new Date().toISOString()
          })
          .eq('id', lead.id);
      }

      // 3. Log to pipeline_messages
      await this.supabase.from('pipeline_messages').insert({
        lead_id: lead ? lead.id : null,
        recipient_phone: phoneE164,
        direction: 'inbound',
        message_body: text,
        sent_at: new Date().toISOString()
      });

      // 4. Push urgent alert to Telegram with Hybrid Reply buttons
      const leadTitle = lead ? lead.title : 'Kontak Properti';
      const cleanWaNum = phoneE164.replace('+', '');
      const waDirectUrl = `https://wa.me/${cleanWaNum}`;

      const alertMsg = (
        `🔔 <b>[BALASAN WHATSAPP MASUK]</b>\n\n` +
        `🏢 <b>Properti:</b> ${leadTitle}\n` +
        `👤 <b>Pengirim:</b> ${lead?.contact_name || 'Broker / Owner'} (<code>${phoneE164}</code>)\n` +
        `💬 <b>Isi Pesan:</b>\n<i>"${text}"</i>\n\n` +
        `⚠️ <i>Bot otomatis DIBEKUKAN pada nomor ini agar percakapan 100% alami.</i>\n` +
        `💡 <i>Anda dapat langsung mereply notifikasi Telegram ini untuk membalas, atau klik tombol di bawah:</i>`
      );

      const sentMsg = await this.bot.telegram.sendMessage(this.chatId, alertMsg, {
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [
            [{ text: '📱 Buka Chat di WhatsApp (wa.me)', url: waDirectUrl }]
          ]
        }
      });

      // Store in memory mapping for Telegram reply takeover
      this.inboundReplyHandlers.set(sentMsg.message_id, {
        phoneE164,
        jid,
        leadId: lead?.id
      });
    } catch (err) {
      console.error('[WAClient] Error handling inbound reply:', err.message);
    }
  }

  async sendDirectReply(targetJid, replyText) {
    if (!this.isConnected || !this.sock) {
      throw new Error('WhatsApp tidak terhubung.');
    }
    // Simulate typing 3-5 seconds
    await this.sock.sendPresenceUpdate('composing', targetJid);
    await delay(3000 + Math.random() * 2000);
    await this.sock.sendPresenceUpdate('paused', targetJid);

    const sent = await this.sock.sendMessage(targetJid, { text: replyText });
    return sent;
  }

  // Paced outbound inquiry dispatch with Gaussian jitter (45-120s)
  async queueInquiry(leadId) {
    const { data: lead, error } = await this.supabase
      .from('pipeline_leads')
      .select('*')
      .eq('id', leadId)
      .single();

    if (error || !lead) {
      throw new Error(`Lead ID ${leadId} tidak ditemukan.`);
    }

    this.queue.push(lead);
    console.log(`[WAClient] Lead queued for dispatch: ${lead.title} (${lead.contact_phone}). Queue size: ${this.queue.length}`);

    if (!this.isProcessingQueue) {
      this.processQueue();
    }
    return this.queue.length;
  }

  async processQueue() {
    if (this.queue.length === 0) {
      this.isProcessingQueue = false;
      return;
    }

    this.isProcessingQueue = true;
    const lead = this.queue.shift();

    try {
      if (!this.isConnected) {
        throw new Error('WhatsApp belum terhubung. Harap login / scan QR terlebih dahulu.');
      }

      const rawNum = lead.contact_phone.replace(/[^0-9]/g, '');
      const jid = `${rawNum}@s.whatsapp.net`;

      // 1. Silent onWhatsApp Check (Zero messages)
      const [onWa] = await this.sock.onWhatsApp(rawNum);
      if (!onWa || !onWa.exists) {
        console.log(`[WAClient] ${rawNum} is NOT on WhatsApp. Marking invalid.`);
        await this.supabase
          .from('pipeline_leads')
          .update({ contact_wa_status: 'inactive', status: 'error' })
          .eq('id', lead.id);
        
        await this.bot.telegram.sendMessage(
          this.chatId,
          `❌ <b>Nomor Tidak Terdaftar di WhatsApp:</b> ${lead.contact_phone}\n🏢 ${lead.title}`,
          { parse_mode: 'HTML' }
        );
        this.scheduleNextQueueItem();
        return;
      }

      // 2. Anti-Ban Randomized Delay (45 - 120 detik)
      const delayMs = Math.floor(45000 + Math.random() * 75000);
      const delaySec = Math.round(delayMs / 1000);
      console.log(`[WAClient] Anti-ban pacing delay: waiting ${delaySec}s before sending to ${lead.contact_name}...`);

      await this.bot.telegram.sendMessage(
        this.chatId,
        `⏳ <b>Memulai Pacing Anti-Ban:</b> Menunggu ${delaySec} detik sebelum mengirim inquiry ke <b>${lead.contact_name}</b> (+${rawNum})...`,
        { parse_mode: 'HTML' }
      );

      await delay(delayMs);

      // 3. Simulate Typing (5 - 8 detik)
      await this.sock.sendPresenceUpdate('composing', jid);
      await delay(5000 + Math.random() * 3000);
      await this.sock.sendPresenceUpdate('paused', jid);

      // 4. Reverse Buyer Inquiry Spintax
      const lt = lead.attributes?.luas_tanah || 'unit';
      const loc = lead.district || lead.city || 'area tersebut';
      const templates = [
        `Halo Pak ${lead.contact_name}, apakah unit gudang/pabrik ${lt}m² di ${loc} ini masih available? Klien manufaktur kami sedang mencari unit siap pakai di area ini.`,
        `Selamat pagi Pak ${lead.contact_name}, izin menanyakan untuk listing di ${loc} (LT ${lt}m²), apakah unitnya masih tersedia? Ada rekanan kami yang membutuhkan unit di lokasi ini.`,
        `Halo Pak ${lead.contact_name}, mau tanya apakah properti ${lead.category} di ${loc} ini masih open? Klien industri kami tertarik melihat spek dan ketersediaannya.`
      ];
      const messageText = templates[Math.floor(Math.random() * templates.length)];

      // 5. Send WhatsApp Message
      await this.sock.sendMessage(jid, { text: messageText });
      console.log(`[WAClient] Message sent successfully to ${rawNum}`);

      // 6. Update Database
      await this.supabase
        .from('pipeline_leads')
        .update({ status: 'contacted', updated_at: new Date().toISOString() })
        .eq('id', lead.id);

      await this.supabase.from('pipeline_messages').insert({
        lead_id: lead.id,
        recipient_phone: lead.contact_phone,
        direction: 'outbound',
        message_body: messageText,
        sent_at: new Date().toISOString()
      });

      // 7. Push Telegram Confirmation
      await this.bot.telegram.sendMessage(
        this.chatId,
        `✅ <b>Inquiry WhatsApp Berhasil Terkirim!</b>\n\n👤 <b>Tujuan:</b> ${lead.contact_name} (<code>${lead.contact_phone}</code>)\n🏢 <b>Properti:</b> ${lead.title}\n💬 <b>Pesan:</b>\n<i>"${messageText}"</i>`,
        {
          parse_mode: 'HTML',
          reply_markup: {
            inline_keyboard: [
              [{ text: '📱 Pantau di WhatsApp', url: `https://wa.me/${rawNum}` }]
            ]
          }
        }
      );
    } catch (err) {
      console.error(`[WAClient] Error dispatching lead ${lead.id}:`, err.message);
      await this.bot.telegram.sendMessage(
        this.chatId,
        `⚠️ <b>Gagal Mengirim WA:</b> ${err.message}\nLead: ${lead.title}`,
        { parse_mode: 'HTML' }
      );
    }

    this.scheduleNextQueueItem();
  }

  scheduleNextQueueItem() {
    setTimeout(() => {
      this.processQueue();
    }, 5000);
  }
}

module.exports = WAClient;
