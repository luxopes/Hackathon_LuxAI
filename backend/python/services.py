"""Katalog a předem dohodnuté podmínky dodání služeb za simulované USD."""
import ast

CURRENCY = "USD"
SERVICES = {
    "http-cart-audit": {"name": "Cart audit", "delivery": "Three HTTP cart checks with execution receipts"},
    "short-research": {"name": "Short research", "delivery": "Short research with live web sources fetched via Apify"},
    "python-code": {"name": "Python code", "delivery": "Python code and basic tests with syntax validation"},
    "text-summary": {"name": "Text summary", "delivery": "Concise summary of the submitted text"},
    "translation": {"name": "Translation", "delivery": "Translation of the submitted text as requested"},
    "ideas": {"name": "Ideas", "delivery": "Five specific ideas with a short explanation"},
}
PROVIDERS = [
    {"id": "scout", "name": "Scout", "port": 3083, "tier": "compact", "prices": [3, 5, 2, 2, 3]},
    {"id": "insight", "name": "Insight Lab", "port": 3084, "tier": "balanced", "prices": [6, 9, 4, 4, 5]},
    {"id": "atlas", "name": "Atlas Studio", "port": 3085, "tier": "detailed", "prices": [9, 14, 7, 6, 8]},
]
GENERAL = ["short-research", "python-code", "text-summary", "translation", "ideas"]


def provider_offers(provider):
    offers = [{"id": "offer-" + provider["id"] + "-" + capability,
               "name": provider["name"], "capability": capability, "price": price,
               "tier": provider["tier"], "delivery": SERVICES[capability]["delivery"]}
              for capability, price in zip(GENERAL, provider["prices"])]
    if provider["id"] == "atlas":
        offers.append({"id": "offer-atlas-audit", "name": provider["name"], "capability": "http-cart-audit",
                       "price": 10, "tier": "detailed", "delivery": SERVICES["http-cart-audit"]["delivery"]})
    return offers


def validate_artifact(capability, artifact, sources=()):
    # Kontrolujeme sjednanou strukturu, nikoliv správnost libovolné odpovědi modelu.
    reasons = []
    if type(artifact) is not dict:
        return ["Missing structured artifact"]
    summary = artifact.get("summary")
    if type(summary) is not str or not 10 <= len(summary.strip()) <= 2000:
        reasons.append("Missing useful summary")
    if capability == "python-code":
        for field in ["code", "tests"]:
            text = artifact.get(field)
            if type(text) is not str or not 20 <= len(text) <= 18000:
                reasons.append("Missing Python " + field)
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                reasons.append("Invalid Python syntax: " + field)
                continue
            if field == "code" and not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(tree)):
                reasons.append("Python code must define a function or class")
            if field == "tests" and not any(isinstance(n, ast.Assert) or
                    (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith("assert"))
                    for n in ast.walk(tree)):
                reasons.append("Tests must include an assertion")
    elif capability == "ideas":
        ideas = artifact.get("ideas")
        if type(ideas) is not list or len(ideas) != 5 or any(type(i) is not str or not 15 <= len(i) <= 1500 for i in ideas):
            reasons.append("Exactly five explained ideas are required")
    else:
        content = artifact.get("content")
        if type(content) is not str or not 30 <= len(content.strip()) <= 18000:
            reasons.append("Missing useful text delivery")
        if capability == "short-research":
            citations = artifact.get("sources")
            fetched = {source["url"] for source in sources}
            if (type(citations) is not list or len(citations) < 2 or len(citations) > 5 or
                    any(type(c) is not dict or type(c.get("title")) is not str or not c["title"].strip()
                        or type(c.get("url")) is not str or c["url"] not in fetched for c in citations)):
                reasons.append("At least two citations must match actually fetched sources")
            elif len({c["url"] for c in citations}) < 2:
                reasons.append("Research sources must be distinct")
    return reasons
