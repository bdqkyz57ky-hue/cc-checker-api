import json

async def charge_resp(result):
    """
    Parses Stripe API JSON response into a clean status message
    without returning the raw API response.
    """

    try:
        # Ensure we have a string form to parse
        if not isinstance(result, str):
            raw_result = json.dumps(result)
        else:
            raw_result = result

        result_lower = raw_result.lower()

        # Default output
        output = {
            "status": "Unknown ❌"
        }

        # FIX: Check for simple "approved" or "CCN Live" strings first
        if "approved" in result_lower:
            output["status"] = "Approved ✅"
            return output
        if "ccn live" in result_lower:
            output["status"] = "CCN Live ❎"
            return output

        # ✅ SUCCESS CASES
        if '"status":"succeeded"' in result_lower or '"status":"suceeded"' in result_lower:
            output["status"] = "Approved ✅"
            return output

        if "payment method successfully added" in result_lower or "payment_intent_succeeded" in result_lower:
            output["status"] = "Approved ✅"
            return output

        # ❌ FUNDS & CVC
        if "insufficient funds" in result_lower:
            output["status"] = "Insufficient Funds ❎"
            return output

        if "incorrect_cvc" in result_lower or "security code is incorrect" in result_lower:
            output["status"] = "CCN Live ❎"
            return output

        if "invalid_cvc" in result_lower:
            output["status"] = "Invalid CVC ❌"
            return output

        # ❌ CARD TYPE / PURCHASE SUPPORT
        # FIX: Added a more generic check to catch different phrasing of this error
        if "transaction_not_allowed" in result_lower or "doesn't support purchase" in result_lower:
            output["status"] = "Card Doesn't Support Purchase ❎"
            return output

        # ❌ EXPIRY ERRORS
        if "expired_card" in result_lower or "your card has expired" in result_lower:
            output["status"] = "Expired Card ❌"
            return output

        if "invalid_expiry_month" in result_lower:
            output["status"] = "Invalid Expiry Month ❌"
            return output

        if "invalid_expiry_year" in result_lower:
            output["status"] = "Invalid Expiry Year ❌"
            return output

        # ❌ CARD NUMBER ERRORS
        if "incorrect_number" in result_lower or "your card number is incorrect" in result_lower:
            output["status"] = "Incorrect Card Number ❌"
            return output

        if "invalid account" in result_lower:
            output["status"] = "Dead Card ❌"
            return output

        # 3D SECURE / CHALLENGE REQUIRED
        if "three_d_secure_redirect" in result_lower or \
           "card_error_authentication_required" in result_lower or \
           "stripe_3ds2_fingerprint" in result_lower:
            output["status"] = "3D Challenge Required ❎"
            return output

        # ❌ STOLEN / LOST
        if "stolen_card" in result_lower:
            output["status"] = "Stolen Card ❌"
            return output

        if "lost_card" in result_lower:
            output["status"] = "Lost Card ❌"
            return output

        if "pickup_card" in result_lower:
            output["status"] = "Pickup Card ❌"
            return output

        # ❌ DECLINE REASONS
        if "generic_decline" in result_lower or \
           "your card was declined" in result_lower or \
           "do not honor" in result_lower:
            output["status"] = "Card Declined ❌"
            return output

        if "fraudulent" in result_lower:
            output["status"] = "Fraudulent ❌"
            return output

        if "setup_intent_authentication_failure" in result_lower:
            output["status"] = "Authentication Failure ❌"
            return output

        # ❌ STRIPE API / TOKEN ISSUES
        if "invalid api key" in result_lower or \
           "testmode_charges_only" in result_lower or \
           "api_key_expired" in result_lower:
            output["status"] = "Stripe API Key Error ❌"
            return output

        if "please update bearer token" in result_lower:
            output["status"] = "Token Expired Admin Notified ❌"
            return output

        return output

    except Exception as e:
        return {
            "status": f"Error parsing response ❌: {str(e)}"
        }
