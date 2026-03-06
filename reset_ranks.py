import json

try:
    with open("jobs_db.json", "r", encoding="utf-8") as f:
        db = json.load(f)

    count = 0
    for url, data in db.items():
        if data.get("status") != "Applied":
            # the user wants everything not "Applied" to be re-evaluated
            # which means rank = "UNKNOWN"
            data["rank"] = "UNKNOWN"
            data["reason"] = "Reset for re-evaluation"
            count += 1

    with open("jobs_db.json", "w", encoding="utf-8") as f:
        json.dump(db, f, indent=4)

    print(f"Successfully reset {count} jobs to UNKNOWN rank.")
except Exception as e:
    print(f"Error: {e}")
