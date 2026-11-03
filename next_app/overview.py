"""Deterministic coverage and dates around AI-proposed workstream layout."""

from datetime import date, datetime, timedelta, timezone


def build_overview(active, completed, layout, today: date = None, coverage_since: str = None,
                   now: datetime = None):
    today = today or date.today()
    week_end = today + timedelta(days=6 - today.weekday())
    now = now or datetime.now(timezone.utc)
    recent_start = now - timedelta(days=7)
    records = []
    for item in active:
        records.append({**item, "task_id": "open:" + item["id"], "finished": False})
    for item in completed:
        records.append({**item, "task_id": "done:" + item["id"], "finished": True})
    # The code owns membership. AI can only change workstream, order, emphasis and attention.
    buckets = {name: [] for name in ("today", "week", "all", "finished")}
    for item in records:
        if item["finished"]:
            try:
                completed_at = datetime.fromisoformat(item["completed_at"].replace("Z", "+00:00"))
                if recent_start <= completed_at <= now:
                    buckets["finished"].append(item)
            except (ValueError, TypeError):
                pass
            continue
        buckets["all"].append(item)
        dates = []
        for field in ("due", "deadline"):
            if item.get(field):
                try:
                    dates.append(date.fromisoformat(item[field][:10]))
                except ValueError:
                    pass
        if dates:
            nearest = min(dates)
            if nearest <= today:
                buckets["today"].append(item)
            if nearest <= week_end:
                buckets["week"].append(item)

    stats = {}
    for item in records:
        placement = layout.get(item["task_id"]) or {}
        key = placement.get("stream_id") or ("project:" + item.get("project_id", ""))
        counts = stats.setdefault(key, {"open": 0, "finished": 0})
        counts["finished" if item["finished"] else "open"] += 1

    sections = []
    for name, title in (("today", "Today"), ("week", "This Week"), ("all", "All"), ("finished", "Finished")):
        areas = {}
        for item in buckets[name]:
            placement = layout.get(item["task_id"]) or {}
            area_key = placement.get("area_id") or "unorganized"
            area = areas.setdefault(area_key, {
                "key": area_key, "title": placement.get("area_title") or "Tasks awaiting AI organization",
                "order": placement.get("area_order", 10000), "workstreams": {},
            })
            key = placement.get("stream_id") or ("project:" + item.get("project_id", ""))
            group = area["workstreams"].setdefault(key, {
                "key": key, "title": placement.get("stream_title") or item.get("project_name") or "Other work",
                "emphasis": "quiet", "order": placement.get("stream_order", 10000),
                "tasks": [], "progress": stats[key],
            })
            if placement.get("emphasis") == "focus":
                group["emphasis"] = "focus"
            elif placement.get("emphasis") == "standard" and group["emphasis"] != "focus":
                group["emphasis"] = "standard"
            group["tasks"].append({
                "id": item["id"], "name": item.get("name", ""),
                "description": item.get("description", ""),
                "project": item.get("project_name", ""), "due": item.get("due"),
                "deadline": item.get("deadline"), "completed_at": item.get("completed_at"),
                "priority": item.get("priority", 1), "duration_minutes": item.get("duration_minutes"),
                "project_id": item.get("project_id", ""),
                "snapshot": item if not item["finished"] else None,
                "attention": placement.get("attention"), "order": placement.get("order", 10000),
            })
        ordered_areas = sorted(areas.values(), key=lambda area: (area["order"], area["title"]))
        for area in ordered_areas:
            groups = sorted(area["workstreams"].values(), key=lambda group: (group["order"], group["title"]))
            for group in groups:
                group["tasks"].sort(key=lambda task: (task["order"], task["name"]))
            area["workstreams"] = groups
        sections.append({"key": name, "title": title, "count": len(buckets[name]), "areas": ordered_areas})
    assert sections[2]["count"] == len(active)
    return {"sections": sections, "active_count": len(active),
            "completed_cached": len(completed), "history_since": coverage_since}
