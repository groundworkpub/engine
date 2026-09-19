"""
Zero-Knowledge Encrypted Session Vault.
Encrypts and decrypts Playwright storage_state (cookies & localStorage) using AES-256-GCM.
Enables cloud runners and local agents to hydrate authenticated sessions securely from Supabase.
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dotenv
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Ensure workspace and agents are in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

dotenv.load_dotenv(os.path.join(WORKSPACE_DIR, ".env.local"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("session_vault")


class SessionVault:
    """Zero-knowledge AES-256-GCM encrypted session vault backed by Supabase."""

    def __init__(self, secret_key: str | None = None):
        raw_key = (
            secret_key
            or os.getenv("AFFILIATE_SESSION_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or "groundwork-default-vault-salt-2026"
        )
        # Derive a 256-bit (32-byte) key deterministically using SHA-256
        self.aes_key = hashlib.sha256(raw_key.encode("utf-8")).digest()
        self.aesgcm = AESGCM(self.aes_key)

        # Initialize Supabase client
        sb_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        sb_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if sb_url and sb_key:
            from supabase import create_client
            self.sb = create_client(sb_url, sb_key)
        else:
            self.sb = None
            logger.warning("Supabase credentials unset. Session vault operating in local memory mode.")

    def encrypt_data(self, data: dict[str, Any]) -> tuple[str, str, str]:
        """
        Encrypts a dictionary into AES-256-GCM ciphertext, IV, and auth tag (all Base64).
        """
        plaintext = json.dumps(data).encode("utf-8")
        # 12-byte nonce/IV recommended for AES-GCM
        iv = os.urandom(12)
        # In cryptography's AESGCM, encrypt returns ciphertext + 16-byte tag appended
        ciphertext_with_tag = self.aesgcm.encrypt(iv, plaintext, None)
        ciphertext = ciphertext_with_tag[:-16]
        auth_tag = ciphertext_with_tag[-16:]

        return (
            base64.b64encode(ciphertext).decode("utf-8"),
            base64.b64encode(iv).decode("utf-8"),
            base64.b64encode(auth_tag).decode("utf-8")
        )

    def decrypt_data(self, ciphertext_b64: str, iv_b64: str, auth_tag_b64: str) -> dict[str, Any]:
        """
        Decrypts AES-256-GCM Base64 components back into a dictionary.
        Raises InvalidTag if tampered or corrupt.
        """
        ciphertext = base64.b64decode(ciphertext_b64)
        iv = base64.b64decode(iv_b64)
        auth_tag = base64.b64decode(auth_tag_b64)

        ciphertext_with_tag = ciphertext + auth_tag
        decrypted_bytes = self.aesgcm.decrypt(iv, ciphertext_with_tag, None)
        return json.loads(decrypted_bytes.decode("utf-8"))

    def save_session(
        self,
        network: str,
        storage_state: dict[str, Any],
        metadata: dict[str, Any] | None = None
    ) -> bool:
        """Encrypts storage_state and upserts into Supabase table public.session_vault."""
        if not self.sb:
            logger.error("Cannot save session: Supabase client not initialized.")
            return False

        ciphertext_b64, iv_b64, tag_b64 = self.encrypt_data(storage_state)
        meta = metadata or {}
        meta["updated_by_platform"] = sys.platform
        meta["cookies_count"] = len(storage_state.get("cookies", []))

        payload = {
            "network": network.lower(),
            "ciphertext": ciphertext_b64,
            "iv": iv_b64,
            "auth_tag": tag_b64,
            "metadata": meta,
            "is_valid": True,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            res = self.sb.table("session_vault").upsert(payload, on_conflict="network").execute()
            if res.data:
                logger.info(f"✓ Encrypted session for '{network}' successfully stored in Supabase Vault.")
                return True
        except Exception as exc:
            logger.error(f"Failed to upsert session into Supabase: {exc}")

        return False

    def load_session(self, network: str) -> dict[str, Any] | None:
        """Downloads encrypted session from Supabase, decrypts it, and returns storage_state."""
        if not self.sb:
            return None

        try:
            res = (
                self.sb.table("session_vault")
                .select("ciphertext, iv, auth_tag, is_valid, metadata")
                .eq("network", network.lower())
                .limit(1)
                .execute()
            )
            if not res.data:
                logger.info(f"No stored session found in vault for network '{network}'.")
                return None

            row = res.data[0]
            if not row.get("is_valid", True):
                logger.warning(f"Stored session for '{network}' is flagged as invalid/expired.")
                return None

            storage_state = self.decrypt_data(
                ciphertext_b64=row["ciphertext"],
                iv_b64=row["iv"],
                auth_tag_b64=row["auth_tag"]
            )
            logger.info(f"✓ Successfully decrypted session for '{network}' from Supabase Vault.")
            return storage_state

        except Exception as exc:
            logger.error(f"Error loading/decrypting session for '{network}': {exc}")
            return None

    def invalidate_session(self, network: str, reason: str = "Session expired") -> bool:
        """Marks a session as invalid in Supabase and triggers an alert."""
        if not self.sb:
            return False
        try:
            self.sb.table("session_vault").update({
                "is_valid": False,
                "metadata": {"invalidation_reason": reason, "invalidated_at": datetime.now(timezone.utc).isoformat()}
            }).eq("network", network.lower()).execute()

            # Push Telegram notification
            from core.telegram_gate import TelegramGate
            gate = TelegramGate()
            gate.send_message(
                f"<b>⚠️ Groundwork Session Alert: {network.upper()} Expired</b>\n\n"
                f"Sesi browser untuk <code>{network}</code> kedaluwarsa atau membutuhkan 2FA.\n"
                f"Buka <code>http://localhost:8080</code> dan klik tombol <b>[Re-Auth Session]</b> "
                f"atau jalankan perintah di Mac:\n"
                f"<code>python3 agents/session_vault.py --refresh {network}</code>"
            )
            logger.info(f"Session for '{network}' marked as invalid. Alert dispatched.")
            return True
        except Exception as exc:
            logger.warning(f"Failed to invalidate session for '{network}': {exc}")
            return False

    def refresh_session_interactive(self, network: str = "awin") -> bool:
        """
        Interactive login flow on local Mac. Launches visible browser window,
        waits for user to log in and pass 2FA, then captures and encrypts storage state.
        """
        import asyncio
        from playwright.async_api import async_playwright

        async def _run():
            logger.info(f"Starting interactive session refresh for '{network.upper()}'...")
            login_url = "https://ui.awin.com/login" if network == "awin" else "https://app.impact.com/login"

            profile_dir = Path(WORKSPACE_DIR) / "agents" / f".{network}_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)

            async with async_playwright() as p:
                ctx = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel="chrome",
                    headless=False,
                    viewport={"width": 1366, "height": 768},
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()

                print("\n" + "="*70)
                print(f"🔑 INTERACTIVE LOGIN: {network.upper()}")
                print(f"Jendela browser Google Chrome telah terbuka.")
                print(f"Silakan login dan selesaikan tantangan 2FA (Two-Factor Authentication).")
                print("="*70 + "\n")

                await page.goto(login_url, timeout=60000, wait_until="domcontentloaded")

                # Wait until navigation reaches a post-login dashboard or 3 minutes timeout
                success = False
                for _ in range(36):  # Check every 5 seconds for up to 180 seconds
                    await page.wait_for_timeout(5000)
                    cur_url = page.url.lower()
                    if ("dashboard" in cur_url or "home" in cur_url or "overview" in cur_url) and "login" not in cur_url:
                        logger.info(f"✓ Detected successful login to {cur_url}")
                        success = True
                        break

                if not success:
                    # Give prompt to press Enter in terminal if user is already logged in
                    print("Jika Anda sudah berhasil masuk ke dashboard, tekan [ENTER] di sini...")
                    input()
                    success = True

                # Extract storage state
                state_dict = await ctx.storage_state()
                await ctx.close()

                # Save encrypted state to Supabase
                if self.save_session(network, state_dict, metadata={"interactive_login_host": "local_mac"}):
                    print(f"\n[✓] Sesi {network.upper()} berhasil dienkripsi dan disimpan di Supabase Session Vault!")
                    return True
                else:
                    print(f"\n[!] Gagal menyimpan sesi ke Supabase.")
                    return False

        return asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description="Groundwork Zero-Knowledge Encrypted Session Vault")
    parser.add_argument("--network", choices=["awin", "impact", "clickbank"], default="awin", help="Affiliate network")
    parser.add_argument("--save-local", action="store_true", help="Export local profile to Supabase vault")
    parser.add_argument("--refresh", action="store_true", help="Launch interactive browser to log in and refresh session")
    parser.add_argument("--load", action="store_true", help="Download and decrypt session from Supabase vault")
    parser.add_argument("--verify", action="store_true", help="Verify session validity")

    args = parser.parse_args()
    vault = SessionVault()

    if args.refresh:
        vault.refresh_session_interactive(args.network)
    elif args.load:
        state = vault.load_session(args.network)
        if state:
            print(f"Loaded storage state with {len(state.get('cookies', []))} cookies and {len(state.get('origins', []))} origins.")
        else:
            print("Failed to load session.")
    elif args.save_local:
        # Check if local profile exists
        profile_file = Path(WORKSPACE_DIR) / "agents" / f".{args.network}_profile" / "storage_state.json"
        if profile_file.exists():
            with open(profile_file, "r") as f:
                state = json.load(f)
            vault.save_session(args.network, state)
        else:
            print(f"No storage_state.json found at {profile_file}. Use --refresh instead.")
    else:
        # Default: show status
        state = vault.load_session(args.network)
        print(f"Vault Status for '{args.network}': {'ACTIVE' if state else 'UNINITIALIZED / EXPIRED'}")


if __name__ == "__main__":
    main()
