from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import AssessmentRequest, AssessmentResponse
from app.services.assessment import assess
from app.services.storage import load_result, save_result
from app.services.export import build_export, build_windows_csv
from app.services.experiment import compare_with_baseline

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="EVA Window Risk Planner", version="1.0.1")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def landing() -> FileResponse:
    """Главная страница: описание сервиса и переход к расчёту."""
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/app", include_in_schema=False)
def index() -> FileResponse:
    """Рабочее окно: параметры, расчёт и сравнение окон."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "data_mode": "live-with-cache"}


@app.post("/api/assess", response_model=AssessmentResponse)
def create_assessment(request: AssessmentRequest) -> AssessmentResponse:
    result = assess(request)
    save_result(result)
    return result


@app.get("/api/results/{result_id}")
def get_result(result_id: str) -> JSONResponse:
    saved = load_result(result_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved result not found")
    return JSONResponse(saved)


@app.get("/api/results/{result_id}/result.json")
def export_result_json(result_id: str) -> Response:
    saved = load_result(result_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved result not found")
    import json
    return Response(
        json.dumps(saved, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eva-{result_id}.json"'},
    )


@app.get("/api/results/{result_id}/export")
def export_result(result_id: str) -> Response:
    saved = load_result(result_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved result not found")
    return Response(build_export(saved), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="eva-{result_id}.zip"'})


@app.get("/api/results/{result_id}/baseline")
def baseline_result(result_id: str) -> JSONResponse:
    saved = load_result(result_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved result not found")
    result = AssessmentResponse.model_validate(saved["result"])
    return JSONResponse(compare_with_baseline(result))


@app.get("/api/results/{result_id}/windows.csv")
def export_windows_csv(result_id: str) -> Response:
    saved = load_result(result_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved result not found")
    return Response(
        build_windows_csv(saved["result"]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eva-{result_id}-windows.csv"'},
    )
