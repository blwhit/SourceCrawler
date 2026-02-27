import asyncio
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from core.manager import ScannerManager
from core.models import ScanRequest, SearchMode, ScanStatus
from scanners.publicwww_scanner import PlaywrightManager
from scanners import ALL_SCANNERS

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
EXPORTS_DIR = Path(__file__).resolve().parent.parent / "exports"

app = FastAPI(title="SourceCrawler")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

manager = ScannerManager()


@app.on_event("startup")
async def startup():
    EXPORTS_DIR.mkdir(exist_ok=True)


@app.on_event("shutdown")
async def shutdown():
    await PlaywrightManager.shutdown()


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/scan")
async def start_scan(body: dict):
    """Start a new scan. Returns scan_id immediately."""
    query = body.get("query", "").strip()
    mode_str = body.get("mode", "string")
    debug_mode = body.get("debug_mode", False)
    if not query:
        raise HTTPException(400, "query is required")

    # Handle debug mode for Playwright (non-headless)
    # Set desired headless state BEFORE shutdown so new instance picks it up
    PlaywrightManager.set_headless(not debug_mode)
    # Restart browser so it launches with the correct headless setting
    await PlaywrightManager.shutdown()

    mode = SearchMode(mode_str)
    scan_request = ScanRequest(query=query, mode=mode)
    manager._active_scans[scan_request.scan_id] = scan_request
    return {"scan_id": scan_request.scan_id, "status": scan_request.status.value}


@app.websocket("/ws/results/{scan_id}")
async def websocket_results(websocket: WebSocket, scan_id: str):
    """WebSocket endpoint for streaming scan results."""
    await websocket.accept()

    scan_request = manager.get_scan(scan_id)
    if not scan_request:
        await websocket.send_json({"type": "error", "message": "Unknown scan_id"})
        await websocket.close()
        return

    async def on_result(result):
        try:
            await websocket.send_json({"type": "result", "data": result.to_dict()})
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def on_status(provider, status_msg):
        try:
            await websocket.send_json({
                "type": "status",
                "provider": provider,
                "message": status_msg,
            })
        except (WebSocketDisconnect, RuntimeError):
            pass

    try:
        scan_task = asyncio.create_task(
            manager.run_scan(scan_request, on_result, on_status)
        )

        async def listen_for_commands():
            try:
                while True:
                    msg = await websocket.receive_json()
                    if msg.get("action") == "cancel":
                        await manager.cancel_scan(scan_id)
            except (WebSocketDisconnect, RuntimeError):
                await manager.cancel_scan(scan_id)

        listener_task = asyncio.create_task(listen_for_commands())

        await scan_task
        listener_task.cancel()

        try:
            await websocket.send_json({
                "type": "complete",
                "total_results": len(scan_request.results),
                "errors": scan_request.errors,
            })
        except (WebSocketDisconnect, RuntimeError):
            pass

    except WebSocketDisconnect:
        await manager.cancel_scan(scan_id)
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


@app.post("/api/scan/{scan_id}/stop")
async def stop_scan(scan_id: str):
    success = await manager.cancel_scan(scan_id)
    if not success:
        raise HTTPException(404, "Scan not found or not running")
    return {"status": "cancelled"}


@app.get("/api/scan/{scan_id}/status")
async def scan_status(scan_id: str):
    scan = manager.get_scan(scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")
    return {
        "scan_id": scan_id,
        "status": scan.status.value,
        "result_count": len(scan.results),
        "errors": scan.errors,
    }


@app.get("/api/export/{scan_id}")
async def export_results(scan_id: str, format: str = "json"):
    scan = manager.get_scan(scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "json":
        filename = f"sourcecrawler_{scan_id[:8]}_{timestamp}.json"
        filepath = EXPORTS_DIR / filename
        data = [r.to_dict() for r in scan.results]
        filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return FileResponse(filepath, filename=filename, media_type="application/json")

    elif format == "csv":
        filename = f"sourcecrawler_{scan_id[:8]}_{timestamp}.csv"
        filepath = EXPORTS_DIR / filename
        keys = ["provider_name", "target_url", "code_snippet", "timestamp"]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            for r in scan.results:
                writer.writerow(r.to_dict())
        return FileResponse(filepath, filename=filename, media_type="text/csv")

    raise HTTPException(400, "format must be 'json' or 'csv'")


# --- Settings API ---

@app.get("/api/settings/scanners")
async def get_scanner_settings():
    """Return all scanners and their enabled/configured status."""
    scanners_info = []
    for scanner_cls in ALL_SCANNERS:
        scanner = scanner_cls(
            rate_limiter=manager.rate_limiter,
            config=manager.config,
        )
        scanners_info.append({
            "name": scanner.name,
            "configured": scanner.is_configured(),
            "enabled": manager.config.get("_disabled_scanners", {}).get(scanner.name) is not True,
        })
    return {"scanners": scanners_info}


@app.post("/api/settings/scanners")
async def update_scanner_settings(body: dict):
    """Enable or disable specific scanners."""
    scanners = body.get("scanners", {})
    if "_disabled_scanners" not in manager.config:
        manager.config["_disabled_scanners"] = {}
    for name, enabled in scanners.items():
        manager.config["_disabled_scanners"][name] = not enabled
    return {"status": "ok"}


@app.get("/api/settings/publicwww")
async def get_publicwww_settings():
    pw_cfg = manager.config.get("publicwww", {})
    return {
        "email": pw_cfg.get("email", ""),
        "has_password": bool(pw_cfg.get("password")),
    }


@app.post("/api/settings/publicwww")
async def update_publicwww_settings(body: dict):
    if "publicwww" not in manager.config:
        manager.config["publicwww"] = {}
    if "email" in body:
        manager.config["publicwww"]["email"] = body["email"]
    if "password" in body:
        manager.config["publicwww"]["password"] = body["password"]
    return {"status": "ok"}


@app.post("/api/clear")
async def clear_all_data():
    """Clear all scan data from memory."""
    # Cancel any running scans
    for scan_id in list(manager._active_scans.keys()):
        await manager.cancel_scan(scan_id)
    # Clear all data
    manager._active_scans.clear()
    manager._scanner_tasks.clear()
    manager._scanners_cache.clear()
    # Clean export files
    for f in EXPORTS_DIR.glob("sourcecrawler_*"):
        try:
            f.unlink()
        except OSError:
            pass
    return {"status": "cleared"}
