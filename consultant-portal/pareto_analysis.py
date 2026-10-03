"""Auditable customer/product concentration and QUAD review signals."""

from collections import defaultdict
from math import ceil


def totals(rows):
    sales = sum(r["sales"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    return {"sales": sales, "cost": cost, "gross_profit": sales - cost,
            "gross_margin_pct": 100 * (sales - cost) / sales if sales > 0 else None,
            "row_count": len(rows),
            "gross_loss": sum(max(0, r["cost"] - r["sales"]) for r in rows),
            "loss_rows": sum(r["cost"] > r["sales"] for r in rows)}


def ranking(groups, metric):
    ordered = sorted(groups, key=lambda r: (-r[metric], r["id"]))
    base = sum(max(0, r[metric]) for r in ordered)
    cumulative = 0.0
    result = []
    for position, group in enumerate(ordered, 1):
        amount = max(0, group[metric])
        vital = amount > 0 and cumulative < base * .8
        cumulative += amount
        result.append({**group, "rank": position,
                       "share_pct": 100 * amount / base if base else None,
                       "cumulative_pct": 100 * cumulative / base if base else None,
                       "in_80_group": vital})
    selected = [r for r in result if r["in_80_group"]]
    top_count = ceil(len(result) * .2) if result else 0
    return {"basis": metric, "positive_base": base,
            "net_total": sum(r[metric] for r in ordered),
            "negative_total": sum(min(0, r[metric]) for r in ordered),
            "groups_to_80": len(selected), "total_groups": len(result),
            "pct_groups_to_80": 100 * len(selected) / len(result) if result else None,
            "coverage_pct": sum(r["share_pct"] for r in selected) if base else None,
            "top_20_count": top_count,
            "top_20_share_pct": sum(r["share_pct"] for r in result[:top_count]) if base else None,
            "rows": result}


QUADS = {
    "Q1": ("High sales / high margin", "Protect service and capacity; validate opportunities to grow."),
    "Q2": ("High sales / low margin", "Prioritize pricing, discounts, freight, cost-to-serve and cost accuracy reviews."),
    "Q3": ("Low sales / high margin", "Assess targeted growth, cross-selling and repeat-order potential."),
    "Q4": ("Low sales / low margin", "Review minimum orders, complexity, pricing and service terms before making changes."),
    "Review": ("Not classifiable", "Validate zero/negative net sales or the unavailable business margin before classification."),
}


def detailed_analysis(current, prior):
    business = totals(current)
    threshold = business["gross_margin_pct"]
    dimensions = {}
    for field in ("customer", "item"):
        grouped, previous = defaultdict(list), defaultdict(list)
        for row in current:
            grouped[row[field]].append(row)
        for row in prior:
            previous[row[field]].append(row)
        groups = []
        for key, rows in grouped.items():
            now, old = totals(rows), totals(previous[key])
            groups.append({"id": key, **now,
                           "name": next((r.get(field + "_name") for r in rows if r.get(field + "_name")), ""),
                           "prior_sales": old["sales"], "prior_gross_profit": old["gross_profit"],
                           "sales_change": now["sales"] - old["sales"],
                           "gross_profit_change": now["gross_profit"] - old["gross_profit"],
                           "has_prior_activity": key in previous and bool(previous[key])})
        sales = ranking(groups, "sales")
        high = {r["id"] for r in sales["rows"] if r["in_80_group"]}
        assignments = {}
        for group in groups:
            if group["sales"] <= 0 or threshold is None:
                quad = "Review"
            else:
                above = group["gross_margin_pct"] >= threshold
                quad = ("Q1" if above else "Q2") if group["id"] in high else ("Q3" if above else "Q4")
            assignments[group["id"]] = quad
            group["quad"] = quad
        # Rebuild rankings to carry the same classification across every metric.
        quads = []
        for code, (label, action) in QUADS.items():
            members = [g for g in groups if g["quad"] == code]
            rows = [r for r in current if assignments[r[field]] == code]
            quads.append({"code": code, "label": label, "action": action,
                          "group_count": len(members), "members": sorted(g["id"] for g in members), **totals(rows)})
        dimensions[field] = {
            "sales": ranking(groups, "sales"),
            "gross_profit": ranking(groups, "gross_profit"),
            "gross_loss": ranking(groups, "gross_loss"),
            "quads": quads,
            "prior_only": [{"id": key, **totals(rows)} for key, rows in sorted(previous.items())
                           if key not in grouped and rows],
        }
    high_customers = {r["id"] for r in dimensions["customer"]["sales"]["rows"] if r["in_80_group"]}
    high_items = {r["id"] for r in dimensions["item"]["sales"]["rows"] if r["in_80_group"]}
    matrix = []
    for code, customer_high, item_high, action in (
        ("80/80", True, True, "Protect core customer/product relationships and review margin leakage."),
        ("80/20", True, False, "Review long-tail products supplied to core customers; check basket and service value."),
        ("20/80", False, True, "Evaluate growth of core products with smaller customers."),
        ("20/20", False, False, "Review complexity, order economics and strategic exceptions."),
    ):
        rows = [r for r in current if (r["customer"] in high_customers) == customer_high
                and (r["item"] in high_items) == item_high]
        pairs = defaultdict(list)
        for row in rows:
            pairs[(row["customer"], row["item"])].append(row)
        matrix.append({"code": code, "action": action, **totals(rows),
                       "customer_count": len({r["customer"] for r in rows}),
                       "item_count": len({r["item"] for r in rows}),
                       "pairs": [{"customer": c, "item": i, **totals(values)}
                                 for (c, i), values in sorted(pairs.items())]})
    return {"version": 1, "margin_threshold_pct": threshold,
            "dimensions": dimensions, "customer_product_matrix": matrix,
            "definitions": [
                "80 group: the smallest ranked set reaching at least 80% of positive group net sales, including the crossing group. Remaining groups form the 20 tail; this does not imply 20% of entities.",
                "Profit and loss rankings use positive group gross profit and transaction-level gross loss respectively. Negative amounts are disclosed separately, not included in positive concentration denominators.",
                "Top 20% uses the ceiling of 20% of active entities. Ties are ordered by identifier for repeatable results.",
                "Profitability QUADs compare group gross margin with weighted business gross margin. Equality is high margin. Zero/negative group sales or an unavailable business margin are marked Review.",
                "Customer/product matrix: first number is the customer revenue group, second is the product revenue group. All transactions, including credits and zero-price rows, remain in their assigned cell.",
                "Amounts use the workbook currency; no currency conversion is performed. Gross profit excludes operating expenses. QUAD recommendations are review priorities, not confirmed EBITDA savings.",
            ]}
