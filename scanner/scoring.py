"""Keyword filtering, heuristic scoring and optional Gemini relevance check."""
import json
import re
import time
import urllib.error
import urllib.request


def _pattern(words):
    words = sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True)
    if not words:
        return None
    return re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(re.escape(w) for w in words), re.I)


def build_patterns(cfg):
    return {
        "include": _pattern(cfg["include_keywords"]),
        "hiring": _pattern(cfg["hiring_signals"]),
        "exclude": _pattern(cfg["exclude_signals"]),
        "country": _pattern(cfg["exclude_countries"]),
    }


def _matches(pattern, text):
    if pattern is None:
        return []
    seen = []
    for m in pattern.finditer(text):
        word = m.group(0).lower()
        if word not in seen:
            seen.append(word)
    return seen


BUDGET = re.compile(r"\$\s?\d|\d+\s?(usd|eur|gbp)\b|\bbudget\b", re.I)
HELP_QUESTION = re.compile(r"\b(how (do|can|to)|why (is|does)|help me|any (tips|advice)|is it worth)\b", re.I)


def heuristic(lead, pats):
    """Return (result, reason). result is None when the lead is rejected."""
    title, body = lead["title"], lead["body"]
    text = f"{title} {body}"
    if pats["exclude"] and pats["exclude"].search(f"{title} {body[:300]}"):
        return None, "for-hire"
    if pats["country"] and pats["country"].search(text):
        return None, "country"
    keywords = _matches(pats["include"], text)
    if not keywords:
        return None, "no-keyword"
    signals = _matches(pats["hiring"], text)
    jobboard = lead["kind"] == "jobboard"
    if not signals and not jobboard:
        return None, "no-hiring-signal"

    score = min(40, 25 + 5 * len(keywords))
    score += 30 if jobboard else min(35, 15 + 10 * len(signals))
    if BUDGET.search(text):
        score += 15
    if "hiring" in title.lower():
        score += 10
    if not jobboard and HELP_QUESTION.search(title):
        score -= 20
    return {"score": max(0, min(100, score)), "keywords": keywords[:6]}, "ok"


PROMPT = """You screen online posts for a freelancer who sells WordPress, Elementor, SEO and AI-search (AEO/GEO) services.
Decide whether the post is someone looking to HIRE or BUY such work (a job, project or client request).
Posts offering services, asking casual how-to questions, or giving advice are NOT hiring posts.
Reply with JSON only: {{"is_hiring": true|false, "relevance": 0-100, "reason": "max 15 words"}}

Title: {title}
Source: {source}
Text: {body}"""


API = "https://generativelanguage.googleapis.com/v1beta"


def pick_model(key, preferred):
    """Use the configured model if the key can reach it, else the newest plain 'flash' model."""
    req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        models = json.loads(resp.read()).get("models", [])
    names = [m["name"].split("/")[-1] for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
    if preferred in names:
        return preferred
    flash = [n for n in names if re.fullmatch(r"gemini-[\d.]+-flash", n)]
    if not flash:
        raise RuntimeError(f"no usable flash model; key can use: {names[:15]}")
    return max(flash, key=lambda n: float(re.search(r"gemini-([\d.]+)-flash", n).group(1)))


def ai_score(lead, model, key):
    prompt = PROMPT.format(title=lead["title"], source=lead["source"], body=lead["body"][:1500])
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    req = urllib.request.Request(
        f"{API}/models/{model}:generateContent",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from model {model}: {exc.read()[:300].decode('utf-8', 'replace')}")
    return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])


def refine_with_ai(candidates, cfg, key):
    """Blend Gemini's verdict into the heuristic score. Stops quietly if the free quota runs out."""
    done = 0
    try:
        model = pick_model(key, cfg["gemini_model"])
    except Exception as exc:
        print(f"  AI scoring skipped: {exc}")
        return 0
    print(f"  AI model: {model}")
    for lead in candidates[: cfg["ai_max_per_run"]]:
        try:
            verdict = ai_score(lead, model, key)
        except Exception as exc:  # quota, network, malformed JSON: keep heuristic score
            print(f"  AI scoring stopped: {exc}")
            break
        relevance = int(verdict.get("relevance", 0))
        if verdict.get("is_hiring"):
            lead["score"] = round((lead["score"] + 2 * relevance) / 3)
        else:
            lead["score"] = min(lead["score"], relevance, 15)
        lead["ai_reason"] = str(verdict.get("reason", ""))[:160]
        done += 1
        time.sleep(4.5)  # free tier allows ~15 requests/minute
    return done
