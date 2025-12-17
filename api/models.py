from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class GateModel(BaseModel):
    id: str
    name: str
    description: str
    type: str = "charge"  # charge, auth, etc.

class GateListResponse(BaseModel):
    object: str = "list"
    data: List[GateModel]

class UnifiedCheckRequest(BaseModel):
    gate: str = Field(..., description="The ID of the gate to use (e.g. 'braintree', 'paypal')")
    card: str = Field(..., description="Full card string (CC|MM|YY|CVC) or just number if details provided")
    mm: Optional[str] = None
    yy: Optional[str] = None
    cvc: Optional[str] = None
    site: Optional[str] = Field(None, description="Required for Shopify gate")
    
class UnifiedCheckResponse(BaseModel):
    gate: str
    status: str
    message: str
    elapsed: Optional[float] = None
    details: Optional[Dict[str, Any]] = None
    input_card: str
