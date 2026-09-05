// tools/lead_ops/server/session_sync.js
// Synchronizes Baileys multi-file auth state with Supabase PostgreSQL (pipeline_sessions)
// Guarantees zero-loss session persistence across Render restarts/redeploys.

const fs = require('fs');
const path = require('path');

async function restoreSessionFromSupabase(supabase, sessionId = 'default', authDir = './auth_info') {
  try {
    const { data, error } = await supabase
      .from('pipeline_sessions')
      .select('*')
      .eq('id', sessionId)
      .single();

    if (error || !data) {
      console.log(`[SessionSync] No existing session found in Supabase for id='${sessionId}'.`);
      return false;
    }

    if (!fs.existsSync(authDir)) {
      fs.mkdirSync(authDir, { recursive: true });
    }

    const files = data.metadata?.files || {};
    const fileKeys = Object.keys(files);

    if (fileKeys.length === 0 && data.creds) {
      fs.writeFileSync(path.join(authDir, 'creds.json'), JSON.stringify(data.creds, null, 2));
      console.log(`[SessionSync] Restored creds.json from Supabase.`);
      return true;
    }

    for (const fileName of fileKeys) {
      const filePath = path.join(authDir, fileName);
      fs.writeFileSync(filePath, files[fileName], 'utf8');
    }

    console.log(`[SessionSync] Successfully restored ${fileKeys.length} auth files from Supabase.`);
    return true;
  } catch (err) {
    console.error(`[SessionSync] Failed to restore session from Supabase:`, err.message);
    return false;
  }
}

async function backupSessionToSupabase(supabase, sessionId = 'default', authDir = './auth_info', phoneNumber = null, pushName = null) {
  try {
    if (!fs.existsSync(authDir)) return;

    const files = fs.readdirSync(authDir);
    const filesMap = {};
    let credsData = null;

    for (const file of files) {
      const filePath = path.join(authDir, file);
      if (fs.statSync(filePath).isFile()) {
        const content = fs.readFileSync(filePath, 'utf8');
        filesMap[file] = content;
        if (file === 'creds.json') {
          try {
            credsData = JSON.parse(content);
          } catch (e) {}
        }
      }
    }

    const payload = {
      id: sessionId,
      creds: credsData,
      phone_number: phoneNumber || credsData?.me?.id?.split(':')[0] || null,
      push_name: pushName || credsData?.me?.name || null,
      is_active: true,
      metadata: {
        files: filesMap,
        total_files: Object.keys(filesMap).length,
        synced_at: new Date().toISOString()
      },
      updated_at: new Date().toISOString()
    };

    const { error } = await supabase
      .from('pipeline_sessions')
      .upsert(payload, { onConflict: 'id' });

    if (error) {
      console.error(`[SessionSync] Error upserting session to Supabase:`, error.message);
    } else {
      console.log(`[SessionSync] Successfully backed up ${Object.keys(filesMap).length} auth files to Supabase.`);
    }
  } catch (err) {
    console.error(`[SessionSync] Failed to backup session to Supabase:`, err.message);
  }
}

module.exports = {
  restoreSessionFromSupabase,
  backupSessionToSupabase
};
