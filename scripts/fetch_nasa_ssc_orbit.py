from datetime import UTC, datetime, timedelta
import hashlib, json
from pathlib import Path
from sscws.sscws import SscWs

OUT = Path("data/archive/orbit/iss_sscweb_gse_20240501_20240701.json")
ssc = SscWs()
points = []
start = datetime(2024, 5, 1, tzinfo=UTC)
for offset in range(0, 61, 5):
    a = start + timedelta(days=offset)
    b = min(a + timedelta(days=5), datetime(2024, 7, 1, tzinfo=UTC))
    result = ssc.get_locations(["iss"], [a.isoformat(), b.isoformat()])
    data = result["Data"][0]; coords = data["Coordinates"][0]
    for i in range(0, len(data["Time"]), 10):
        points.append({"time": data["Time"][i].astimezone(UTC).isoformat(), "x_km": float(coords["X"][i]), "y_km": float(coords["Y"][i]), "z_km": float(coords["Z"][i])})
points = list({item["time"]: item for item in points}.values())
payload = {"source": "NASA GSFC SSCWeb", "observatory": "iss", "coordinate_system": "GSE", "query_start": start.isoformat(), "query_end": datetime(2024, 7, 1, tzinfo=UTC).isoformat(), "sample_minutes": 10, "retrieved_at": datetime.now(UTC).isoformat(), "source_url": "https://sscweb.gsfc.nasa.gov/WS/sscr/2/", "points": points}
OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(OUT, len(points), hashlib.sha256(OUT.read_bytes()).hexdigest())
