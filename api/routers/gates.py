from fastapi import APIRouter, HTTPException
from api.models import (
    UnifiedCheckRequest, UnifiedCheckResponse, GateListResponse, GateModel
)
from api.services.braintree import check_card_braintree_iditarod
from api.services.paypal import check_pp_card
from api.services.shopify import check_shopify_card
from api.services.stripe import check_stripe_card
from api.services.sk import check_sk_card
import asyncio
from concurrent.futures import ThreadPoolExecutor
import re

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=20)

# Available gates registry
GATES = [
    GateModel(id="braintree", name="Braintree Iditarod", description="Braintree Auth/Charge", type="auth"),
    GateModel(id="paypal", name="PayPal", description="PayPal 1$ Charge", type="charge"),
    GateModel(id="shopify", name="Shopify", description="Shopify Charge (requires site)", type="charge"),
    GateModel(id="stripe", name="Stripe", description="Stripe Auth", type="auth"),
    GateModel(id="sk", name="Stripe SK", description="Stripe Secret Key Checker", type="charge"),
]

def parse_card(card_str: str):
    """Helper to parse card string into components"""
    # Simple regex for CC|MM|YY|CVC
    match = re.search(r"(\d{13,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})", card_str)
    if match:
        return match.groups()
    return None, None, None, None

@router.get("/models", response_model=GateListResponse)
async def list_gates():
    """List all available gates (like OpenAI models)"""
    return GateListResponse(data=GATES)

@router.post("/check", response_model=UnifiedCheckResponse)
async def check_card(request: UnifiedCheckRequest):
    """
    Unified endpoint to check cards.
    Select the gate using the 'gate' parameter (like 'model' in OpenAI).
    """
    gate_id = request.gate.lower()
    
    # Parse card details if not fully provided
    cc, mm, yy, cvc = None, None, None, None
    if request.mm and request.yy and request.cvc:
        cc = request.card
        mm = request.mm
        yy = request.yy
        cvc = request.cvc
    else:
        # Try to parse from string
        parsed_cc, parsed_mm, parsed_yy, parsed_cvc = parse_card(request.card)
        if parsed_cc:
            cc, mm, yy, cvc = parsed_cc, parsed_mm, parsed_yy, parsed_cvc
        else:
            # Fallback for gateways that accept raw strings, or error for those that don't
            cc = request.card

    result = {}
    
    # --- Dispatcher Logic ---
    if gate_id == "braintree":
        if not (cc and mm and yy and cvc):
            raise HTTPException(status_code=400, detail="Invalid card format. Need CC|MM|YY|CVC")
            
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            executor, 
            check_card_braintree_iditarod, 
            cc, mm, yy, cvc
        )
        result = {
            "status": res["status"],
            "message": res["message"],
            "elapsed": None # Braintree func currently doesn't return elapsed
        }

    elif gate_id == "paypal":
        res = await check_pp_card(request.card) # PayPal service handles raw string
        result = {
            "status": res["status"],
            "message": res["message"],
            "details": {"api_status": res.get("api_status")}
        }

    elif gate_id == "shopify":
        if not request.site:
            raise HTTPException(status_code=400, detail="Shopify gate requires 'site' parameter")
        
        res = await check_shopify_card(request.card, request.site)
        result = {
            "status": res["status"],
            "message": res["message"],
            "elapsed": res.get("elapsed"),
            "details": {"price": res.get("price")}
        }

    elif gate_id == "stripe":
        res = await check_stripe_card(request.card)
        result = {
            "status": res["status"],
            "message": res["message"],
            "elapsed": res.get("elapsed")
        }

    elif gate_id == "sk":
        res = await check_sk_card(request.card)
        result = {
            "status": res["status"],
            "message": res["message"],
            "elapsed": res.get("elapsed"),
            "details": {"decline_code": res.get("decline_code")}
        }

    else:
        raise HTTPException(status_code=404, detail=f"Gate '{gate_id}' not found. Check GET /api/v1/gates/models")

    # Construct standardized response
    return UnifiedCheckResponse(
        gate=gate_id,
        status=result.get("status", "unknown"),
        message=result.get("message", "No response"),
        elapsed=result.get("elapsed"),
        details=result.get("details"),
        input_card=request.card
    )
