"""Entry point: fetch all sources, filter, score, de-duplicate and write the dashboard data."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402
import sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LEADS_FILE = ROOT / "docs" / "leads.json"
STATUS_FILE = ROOT / "docs" / "status.json"
SEEN_FILE = ROOT / "data" / "seen.json"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    cfg = read_json(ROOT / "config.json", None)
    if cfg is None:
        sys.exit("config.json missing or invalid")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg["max_age_hours"])

    status = {}
    raw = {}
    for name, fetcher in sources.SOURCES:
        try:
            found = fetcher(cfg)
            status[name] = {"ok": True, "count": len(found)}
            for lead in found:
                raw[lead["id"]] = lead
        except Exception as exc:
            status[name] = {"ok": False, "count": 0, "error": str(exc)[:200]}
        print(f"{name}: {status[name]}")

    seen = read_json(SEEN_FILE, {})
    pats = scoring.build_patterns(cfg)
    candidates, rejected = [], {}
    for lead in raw.values():
        if lead["id"] in seen or datetime.fromisoformat(lead["posted"]) < cutoff:
            continue
        result, reason = scoring.heuristic(lead, pats)
        seen[lead["id"]] = now.isoformat()
        if result is None:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        lead.update(result)
        lead["found"] = now.isoformat()
        candidates.append(lead)

    key = os.environ.get("GEMINI_API_KEY")
    candidates.sort(key=lambda l: -l["score"])
    if key and candidates:
        scoring.refine_with_ai(candidates, cfg, key)
    new_leads = [l for l in candidates if l["score"] >= cfg["min_score"]]
    for lead in new_leads:
        lead["snippet"] = lead.pop("body")[:600]
    print(f"new candidates: {len(candidates)}, kept: {len(new_leads)}, rejected: {rejected}")

    old = read_json(LEADS_FILE, {"leads": []})["leads"]
    leads = sorted(new_leads + old, key=lambda l: l["found"], reverse=True)[: cfg["max_leads_kept"]]
    if new_leads or not LEADS_FILE.exists():
        write_json(LEADS_FILE, {"leads": leads})

    week_ago = (now - timedelta(days=7)).isoformat()
    write_json(SEEN_FILE, {k: v for k, v in seen.items() if v > week_ago})
    write_json(STATUS_FILE, {"updated": now.isoformat(), "sources": status, "ai": bool(key)})


if __name__ == "__main__":
    main()
