import aiohttp

BINLIST_URL = "https://binlist.io/lookup/{}"

async def get_bin_info(bin_number: str) -> dict:
    """
    Fetch BIN information from the binlist.io API.
    """
    if not bin_number.isdigit() or len(bin_number) < 6:
        return {"error": "Invalid BIN. Must be at least 6 digits."}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(BINLIST_URL.format(bin_number)) as resp:
                if resp.status == 429:
                    return {"error": "Rate limit exceeded. Try again later."}
                if resp.status == 404:
                    return {"error": "BIN not found."}
                if resp.status != 200:
                    return {"error": f"API request failed (status {resp.status})"}

                data = await resp.json()

                if not data.get("success", True):
                    return {"error": "Lookup failed."}

                return {
                    "bin": data.get("number", {}).get("iin", bin_number),
                    "length": data.get("number", {}).get("length", "N/A"),
                    "luhn": data.get("number", {}).get("luhn", "N/A"),
                    "scheme": data.get("scheme", "N/A"),
                    "type": data.get("type", "N/A"),
                    "brand": data.get("category", "N/A"),
                    "bank": data.get("bank", {}).get("name", "N/A"),
                    "bank_phone": data.get("bank", {}).get("phone", "N/A"),
                    "bank_url": data.get("bank", {}).get("url", "N/A"),
                    "country": data.get("country", {}).get("name", "N/A"),
                    "country_emoji": data.get("country", {}).get("emoji", ""),
                }
        except Exception as e:
            return {"error": f"Exception: {str(e)}"}
