"""Evidence-aware deterministic engagement calculations; no automatic approval."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from method_schema import SCHEMA


def number(value):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid number") from exc
    if not result.is_finite() or abs(result) > Decimal("1e15"):
        raise ValueError("Numbers must be finite and no larger than 1,000,000,000,000,000")
    return result


def validate_case(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported case file. Use a PIMEC Method version 1 case.")
    records = data.get("records", {})
    gates = data.get("gates", {})
    if not isinstance(records, dict) or not isinstance(gates, dict):
        raise ValueError("Case records and readiness checks must be objects")
    clean = {"version": 1, "records": {}, "gates": {}}
    def clean_row(row, fields):
        if not isinstance(row, dict):
            raise ValueError("Each record must be an object")
        result = {}
        for field in fields:
            val = row.get(field["key"], "")
            if not isinstance(val, str) or len(val) > 8000:
                raise ValueError("Each field must be text of at most 8,000 characters")
            val = val.strip()
            if field["type"] in ("number", "score") and val:
                n = number(val)
                if field["type"] == "score" and (n < 1 or n > 5 or n != int(n)):
                    raise ValueError("Priority scores must be whole numbers from 1 to 5")
            if field["type"] == "select" and val and val not in field["options"]:
                raise ValueError("Unknown choice for " + field["label"])
            if field["type"] == "date" and val:
                date.fromisoformat(val)
            result[field["key"]] = val
        return result
    for i, stage in enumerate(SCHEMA["stages"]):
        clean["gates"][str(i)] = clean_row(gates.get(str(i), {}), SCHEMA["gate_fields"])
        for reg in stage["registers"]:
            rows = records.get(reg["key"], [])
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError("Use no more than 200 records per register")
            clean["records"][reg["key"]] = [clean_row(row, reg["fields"]) for row in rows]
    return clean


def analyze_case(raw):
    data = validate_case(raw)
    records = data["records"]
    warnings = []
    result = {"source": SCHEMA["source"], "case": data, "warnings": warnings,
              "readiness": [], "opportunities": [], "pareto": [], "priorities": [],
              "performance": [], "benefits": [], "follow_up": [], "process_flow": [], "pilots": []}
    today = date.today()
    required = {
        "charter": ("client", "problem", "outcome", "scope", "period", "currency"),
        "owners": ("role", "person", "outcome", "authority"),
        "measures": ("name", "definition", "unit", "source", "owner", "frequency", "period", "baseline", "target"),
        "rhythm": ("name", "frequency", "owner", "purpose", "record"),
        "datasets": ("name", "purpose", "source", "checks"),
        "observations": ("process", "date", "observer", "source"),
        "causes": ("problem", "hypothesis", "test", "evidence", "conclusion"),
        "opportunities": ("id", "name", "category", "unit", "period", "mechanism", "assumptions", "evidence", "owner"),
        "initiatives": ("id", "name", "result", "owner", "resources", "funding", "date", "milestones", "decision"),
        "actions": ("action", "owner", "date", "status"),
        "pilots": ("scope", "baseline", "actual", "period", "factors", "consequences", "evidence", "decision", "owner", "standard"),
        "daily": ("date", "owner", "yesterday", "today", "gap", "action"),
        "benefits": ("initiative", "name", "category", "unit", "period", "calculation", "evidence", "owner", "status"),
        "decisions": ("issue", "decision", "owner", "date", "status"),
        "standards": ("process", "owner", "sequence", "checks", "exceptions", "evidence"),
        "training": ("person", "backup", "demonstration", "assessor", "result"),
        "controls": ("measure", "owner", "frequency", "target", "trigger", "response", "escalation"),
        "audits": ("date", "owner", "type", "adherence", "results"),
        "acceptance": ("sponsor", "process_owner", "date", "evidence", "readiness"),
    }
    # Readiness is a recorded human decision, never inferred solely from a filled form.
    for i, stage in enumerate(SCHEMA["stages"]):
        gate = data["gates"][str(i)]
        missing = [reg["title"] for reg in stage["registers"] if not records[reg["key"]]]
        for reg in stage["registers"]:
            for j, row in enumerate(records[reg["key"]]):
                empty = [k for k in required[reg["key"]] if not row[k]]
                if empty:
                    missing.append(f"{reg['title']} record {j + 1}: {', '.join(empty)}")
        accepted = (gate["status"] == "Accepted" and all(gate[k] for k in ("reviewer", "date", "evidence")) and not missing)
        result["readiness"].append({"stage": stage["title"], "deliverable": stage["output"],
                                    "status": "Recorded acceptance" if accepted else "Needs review",
                                    "missing_registers": missing, "criterion": stage["gate"]})
        if gate["status"] == "Accepted" and not accepted:
            warnings.append(stage["title"] + ": acceptance needs approver, date, evidence and stage records.")
    for m in records["measures"]:
        base, actual, target = [number(m[k]) for k in ("baseline", "actual", "target")]
        delta = actual - base if base is not None and actual is not None else None
        comparable = bool(m["period"] and m["actual_period"] and m["context"])
        result["performance"].append({"measure": m["name"], "unit": m["unit"], "change": float(delta) if delta is not None else None,
            "percent_change": float(delta / abs(base) * 100) if delta is not None and base else None,
            "target_met": (actual >= target if m["direction"] == "Higher" else actual <= target) if actual is not None and target is not None and m["direction"] else None,
            "basis": "Comparison context recorded" if comparable else "Comparability not established"})
        if m["quality"] != "Validated":
            warnings.append("Provisional baseline: " + m["name"])
    grouped = defaultdict(list)
    ids = set()
    for o in records["opportunities"]:
        label = o["id"] or o["name"] or "Unnamed opportunity"
        if o["id"] in ids or not o["id"]:
            warnings.append(label + ": opportunity ID must be unique and present.")
            continue
        ids.add(o["id"])
        ql, qh, rl, rh = [number(o[k]) for k in ("quantity_low", "quantity_high", "rate_low", "rate_high")]
        if None in (ql, qh, rl, rh):
            warnings.append(label + ": range incomplete; no value calculated.")
            continue
        if min(ql, qh, rl, rh) < 0 or ql > qh or rl > rh:
            warnings.append(label + ": quantities/rates must be nonnegative with low ≤ high; excluded.")
            continue
        low, high = ql * rl, qh * rh
        item = {"id": label, "name": o["name"], "category": o["category"], "unit": o["unit"], "period": o["period"],
                "low": float(low), "high": float(high), "basis": "Estimated opportunity; not delivered savings"}
        result["opportunities"].append(item)
        if all(o[k] for k in ("category", "unit", "period", "evidence", "mechanism", "assumptions")) and not o["overlap"]:
            grouped[(o["category"], o["unit"].casefold(), o["period"].casefold())].append(item)
        else:
            warnings.append(label + ": excluded from Pareto until basis, unit, period and evidence are complete and overlap is resolved.")
    for (category, unit, period), items in grouped.items():
        ranked = sorted(items, key=lambda x: x["low"], reverse=True)
        total = sum(item["low"] for item in ranked)
        cumulative = 0
        count = 0
        output_items = []
        for item in ranked:
            before = cumulative
            cumulative += item["low"]
            item = dict(item, cumulative_percent=round(cumulative / total * 100, 2) if total else 0)
            if total and before < total * .8:
                count += 1
            output_items.append(item)
        result["pareto"].append({"category": category, "unit": unit, "period": period, "basis": "Low estimate",
                                  "total": total, "items_to_80_percent": count, "total_items": len(items),
                                  "ranked": output_items})
    for initiative in records["initiatives"]:
        scores = [number(initiative[k]) for k in ("customer", "delivery", "risk", "resources_score", "skills", "time")]
        if all(n is not None for n in scores):
            result["priorities"].append({"initiative": initiative["id"] or initiative["name"],
                "impact": round(float(sum(scores[:3]) / 3), 2), "feasibility": round(float(sum(scores[3:]) / 3), 2),
                "score": round(float(sum(scores) / 6), 2), "decision": initiative["selection"]})
        if initiative["selection"] == "Selected" and not all(initiative[k] for k in ("owner", "result", "resources", "funding", "date", "milestones", "decision")):
            warnings.append((initiative["id"] or initiative["name"]) + ": selected initiative lacks owner, measurable target, resources, approval, milestones or due date.")
    result["priorities"].sort(key=lambda x: x["score"], reverse=True)
    for observation in records["observations"]:
        processing, waiting = number(observation["processing"]), number(observation["waiting"])
        if processing is not None and waiting is not None:
            if min(processing, waiting) < 0:
                warnings.append("Process times must be nonnegative: " + observation["process"])
                continue
            elapsed = processing + waiting
            result["process_flow"].append({"process": observation["process"], "processing_minutes": float(processing),
                "waiting_minutes": float(waiting), "total_minutes": float(elapsed),
                "waiting_percent": float(waiting / elapsed * 100) if elapsed else None})
    for pilot in records["pilots"]:
        baseline, actual = number(pilot["baseline"]), number(pilot["actual"])
        if baseline is not None and actual is not None:
            result["pilots"].append({"initiative": pilot["initiative"], "measure": pilot["measure"],
                "change": float(actual - baseline), "percent_change": float((actual - baseline) / abs(baseline) * 100) if baseline else None,
                "decision": pilot["decision"] or "Review conditions, comparison factors and unintended consequences before expanding."})
    totals = defaultdict(lambda: {"achieved": Decimal(0), "annualized": Decimal(0)})
    benefit_keys = set()
    overlap_keys = set()
    for b in records["benefits"]:
        claim_key = (b["initiative"].casefold(), b["name"].casefold(), b["category"], b["unit"].casefold(), b["period"].casefold())
        overlap_key = (b["overlap"].casefold(), b["category"], b["unit"].casefold(), b["period"].casefold())
        if claim_key in benefit_keys or (b["overlap"] and overlap_key in overlap_keys):
            warnings.append((b["name"] or "Benefit") + ": duplicate/shared claim excluded; record a shared result once.")
            continue
        valid = (b["status"] == "Validated" and all(b[k] for k in ("initiative", "category", "unit", "period", "baseline", "target", "actual", "calculation", "evidence", "owner", "reviewer", "review_date"))
                 and (b["category"] not in ("Profit", "Cash") or bool(b["finance"]))
                 and (not b["overlap"] or bool(b["allocation"])))
        if not valid:
            warnings.append((b["name"] or "Benefit") + ": excluded from validated totals until evidence, review, Finance approval where applicable and overlap allocation are recorded.")
            continue
        benefit_keys.add(claim_key)
        if b["overlap"]:
            overlap_keys.add(overlap_key)
        key = (b["category"], b["unit"].casefold(), b["period"].casefold())
        for field, output in (("amount", "achieved"), ("annualized", "annualized")):
            amount = number(b[field])
            if amount is not None:
                totals[key][output] += amount
    result["benefits"] = [{"category": k[0], "unit": k[1], "period": k[2], "achieved_to_date": float(v["achieved"]),
                            "estimated_annualized": float(v["annualized"])} for k, v in totals.items()]
    for action in records["actions"]:
        if action["status"] == "Complete" and not action["evidence"]:
            warnings.append("Completion evidence missing: " + action["action"])
        if action["date"] and date.fromisoformat(action["date"]) < today and action["status"] != "Complete":
            warnings.append("Overdue action: " + action["action"] + " — " + action["owner"])
    for d in records["datasets"]:
        if d["status"] != "Validated" or not d["checks"]:
            warnings.append("Dataset validation outstanding: " + d["name"])
    for c in records["causes"]:
        if c["conclusion"] == "Supported" and not c["evidence"]:
            warnings.append("Supported cause lacks evidence: " + c["hypothesis"])
    for t in records["training"]:
        if t["result"] != "Demonstrated" or not all(t[k] for k in ("demonstration", "assessor", "backup")):
            warnings.append("Capability or backup evidence outstanding: " + t["person"])
    for decision in records["decisions"]:
        if decision["status"] != "Resolved" and decision["date"] and date.fromisoformat(decision["date"]) < today:
            warnings.append("Overdue decision: " + decision["issue"] + " — " + decision["owner"])
    charters = records["charter"]
    if len(charters) > 1:
        warnings.append("Use one charter per case; follow-up dates use the first charter.")
    if charters and charters[0]["handover"]:
        handover = date.fromisoformat(charters[0]["handover"])
        result["follow_up"] = [{"review": f"{days}-day review", "date": (handover + timedelta(days=days)).isoformat()} for days in (30, 60, 90)]
    result["summary"] = {"register_records": sum(len(v) for v in records.values()), "recorded_stage_acceptances": sum(s["status"] == "Recorded acceptance" for s in result["readiness"]),
                         "review_flags": len(warnings), "opportunities": len(result["opportunities"])}
    return result
