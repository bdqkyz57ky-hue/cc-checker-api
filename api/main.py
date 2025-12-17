from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.routers import gates
import uvicorn
import os

app = FastAPI(
    title="BlackXCard CC Checker API",
    description="Unified API for checking credit cards via various gateways",
    version="2.0.0"
)

# Mount the docs directory to serve static files if needed, 
# but we will serve the index.html directly at /documentation
app.mount("/static", StaticFiles(directory="docs"), name="static")

app.include_router(gates.router, prefix="/api/v1/gates", tags=["gates"])

@app.get("/")
async def root():
    return {"message": "BlackXCard API is running. Visit /documentation for usage guide."}

@app.get("/documentation")
async def documentation():
    """Serve the custom HTML documentation"""
    return FileResponse(os.path.join("docs", "index.html"))

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
