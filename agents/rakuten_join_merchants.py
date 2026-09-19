import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path('.env.local'))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rakuten_join")

storage_state_file = Path('agents/.rakuten_profile/storage_state.json') if Path('agents/.rakuten_profile/storage_state.json').exists() else Path('agents/.rakuten_storage_state.json')
results_file = Path('scratch/rakuten_join_results.json')

TARGET_MERCHANTS = [
    # Tier 1 Tech & Computing
    {"mid": 47364, "name": "Lenovo", "pillar": "tech"},
    {"mid": 53591, "name": "Samsung UK", "pillar": "tech"},
    {"mid": 44583, "name": "Newegg", "pillar": "tech"},
    {"mid": 44584, "name": "Newegg Business", "pillar": "tech"},
    {"mid": 44589, "name": "Newegg Canada", "pillar": "tech"},
    {"mid": 54233, "name": "Keeper Security", "pillar": "tech"},
    {"mid": 53953, "name": "CyberPowerPC", "pillar": "tech"},
    {"mid": 54180, "name": "Jameco Electronics", "pillar": "tech"},
    {"mid": 53778, "name": "Electronics Expo", "pillar": "tech"},
    
    # Tier 1 Money & Finance
    {"mid": 54319, "name": "Live Oak Bank", "pillar": "money"},
    {"mid": 54136, "name": "Card Depot", "pillar": "money"},
    {"mid": 54073, "name": "Happen Bank Personal Loans", "pillar": "money"},
    
    # Tier 1 Home & Appliances
    {"mid": 54318, "name": "Choice Furniture Superstore", "pillar": "home"},
    {"mid": 54048, "name": "Greenworks Tools Canada", "pillar": "home"},
    {"mid": 53997, "name": "Designer Appliances", "pillar": "home"},
    
    # Tier 1 Body & Health
    {"mid": 54341, "name": "Pharmacy Direct", "pillar": "body"},
    {"mid": 54011, "name": "Honeydew Care", "pillar": "body"},
    {"mid": 53971, "name": "For Wellness", "pillar": "body"},
    {"mid": 53951, "name": "MyProtein APAC", "pillar": "body"},
    
    # Network Programs & Top Retail
    {"mid": 560, "name": "Rakuten Advertising Welcome Program", "pillar": "lifestyle_retail"},
    {"mid": 815, "name": "Sharper Image", "pillar": "lifestyle_retail"},
    {"mid": 54356, "name": "Zappos Creator Program", "pillar": "lifestyle_retail"},
]

async def ensure_authenticated_page(context):
    page = await context.new_page()
    await page.goto('https://publisher.rakutenadvertising.com/sites/597352/overview', wait_until='networkidle')
    await page.wait_for_timeout(2000)
    
    if 'auth.rakutenmarketing.com' in page.url or 'auth' in page.url:
        logger.info("Session expired or unauthenticated. Logging in...")
        email = os.getenv('RAKUTEN_EMAIL')
        pwd = os.getenv('RAKUTEN_PASSWORD')
        await page.wait_for_selector('#username', timeout=15000)
        await page.locator('#username').click()
        await page.keyboard.type(email, delay=25)
        await page.locator('#password').click()
        await page.keyboard.type(pwd, delay=25)
        await page.locator('input[type="submit"], button[type="submit"]').first.click()
        await page.wait_for_url(lambda u: 'publisher.rakutenadvertising.com' in u and 'auth' not in u, timeout=30000)
        await page.wait_for_timeout(3000)
        await context.storage_state(path=str(storage_state_file))
        logger.info("Re-authenticated and saved state.")
    return page

async def process_merchant(page, target):
    mid = target["mid"]
    name = target["name"]
    pillar = target["pillar"]
    logger.info(f"Processing MID {mid} ({name}) [{pillar}]...")
    
    res = await page.evaluate('''async (mid) => {
        const out = { mid };
        try {
            // 1. Check current status
            const getRes = await fetch(`/api/partnerships/${mid}`);
            if (!getRes.ok) {
                out.status_code = getRes.status;
                out.error = `HTTP ${getRes.status}`;
                return out;
            }
            const data = await getRes.json();
            out.initial_status = data.status;
            out.canPartner = data.canPartner;
            out.advertiserName = data.advertiser?.profile?.name || data.advertiser?.name;
            out.offer = data.offer;
            
            // If already active or pending, no need to re-apply
            if (data.status === 'active' || data.status === 'pending') {
                out.final_status = data.status;
                out.action = 'skipped_already_' + data.status;
                return out;
            }
            
            // 2. Submit application if canPartner
            if (data.canPartner) {
                const postRes = await fetch(`/api/partnerships/${mid}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify({ siteID: 597352 })
                });
                out.post_code = postRes.status;
                
                // 3. Verify new status
                const verifyRes = await fetch(`/api/partnerships/${mid}`);
                if (verifyRes.ok) {
                    const verifyData = await verifyRes.json();
                    out.final_status = verifyData.status;
                    out.applyDate = verifyData.applyDate;
                    out.action = 'applied';
                } else {
                    out.final_status = 'unknown';
                    out.action = 'applied_verify_failed';
                }
            } else {
                out.final_status = data.status;
                out.action = 'cannot_partner';
            }
            return out;
        } catch(e) {
            out.error = e.message;
            return out;
        }
    }''', mid)
    
    logger.info(f"Result for {name} ({mid}): action={res.get('action')} | status={res.get('final_status')}")
    return res

async def main():
    logger.info(f"Starting Rakuten Application Engine for {len(TARGET_MERCHANTS)} Tier-1 targets...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
        )
        context = await browser.new_context(
            storage_state=str(storage_state_file) if storage_state_file.exists() else None,
            viewport={'width': 1920, 'height': 1080}
        )
        page = await ensure_authenticated_page(context)
        
        all_results = []
        for target in TARGET_MERCHANTS:
            try:
                res = await process_merchant(page, target)
                all_results.append({**target, **res})
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"Error processing {target['name']}: {e}")
                all_results.append({**target, "error": str(e)})
                
        with open(results_file, 'w') as f:
            json.dump(all_results, f, indent=2)
            
        logger.info(f"Finished processing all targets! Results saved to {results_file}")
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
