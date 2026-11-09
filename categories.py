"""Search presets and metadata-based heuristic categorization."""
import re

CATEGORIES = {
    "Web Design": {"query": "topic:web-design stars:>50 archived:false", "terms": ["web-design", "webdesign", "frontend", "front-end", "ui-components", "css-framework", "tailwind", "html-template", "web design", "landing page"]},
    "Reverse Engineering": {"query": "topic:reverse-engineering stars:>50 archived:false", "terms": ["reverse-engineering", "reverse engineering", "decompiler", "binary-analysis", "disassembler", "ghidra", "ida-pro", "radare2", "frida"]},
    "Cybersecurity": {"query": "topic:cybersecurity stars:>50 archived:false", "terms": ["cybersecurity", "security-tools", "pentesting", "vulnerability-scanner", "infosec", "penetration-testing", "network-security"]},
    "AI & Machine Learning": {"query": "topic:machine-learning stars:>100 archived:false", "terms": ["machine-learning", "artificial-intelligence", "large-language-model", "llm", "deep-learning", "generative-ai", "computer-vision"]},
    "Automation": {"query": "topic:automation stars:>50 archived:false", "terms": ["automation", "workflow-automation", "rpa", "scripting", "automate", "ansible", "n8n"]},
    "DevOps & Cloud": {"query": "topic:devops stars:>50 archived:false", "terms": ["devops", "kubernetes", "terraform", "ci-cd", "infrastructure-as-code", "observability", "docker"]},
    "Self-Hosting": {"query": "topic:self-hosted stars:>50 archived:false", "terms": ["self-hosted", "selfhosting", "self-hosting", "homelab", "homelab", "personal-cloud"]},
    "Mobile Apps": {"query": "topic:mobile-app stars:>50 archived:false", "terms": ["mobile-app", "android-app", "ios-app", "flutter", "react-native", "swiftui"]},
    "Developer Tools": {"query": "topic:developer-tools stars:>50 archived:false", "terms": ["developer-tools", "developer-tool", "cli", "terminal", "code-editor", "ide-plugin", "debugger"]},
    "Data & Analytics": {"query": "topic:data-science stars:>50 archived:false", "terms": ["data-science", "data-analysis", "business-intelligence", "data-visualization", "analytics", "etl"]},
    "Game Development": {"query": "topic:game-development stars:>50 archived:false", "terms": ["game-development", "game-engine", "gamedev", "unity", "godot", "unreal-engine"]},
    "Robotics": {"query": "topic:robotics stars:>20 archived:false", "terms": ["robotics", "robotic", "robot-operating-system", "ros2", "robot-arm", "autonomous-robot", "robot control", "robot operating system"]},
    "Windows Tools": {"query": "topic:windows stars:>50 archived:false", "terms": ["windows", "windows-app", "windows-tools", "win32", "powershell-tool", "windows-utility"]},
    "macOS Tools": {"query": "topic:macos stars:>50 archived:false", "terms": ["macos", "macos-app", "mac-app", "osx", "macos-tools", "mac-utility"]},
    "Linux Tools": {"query": "topic:linux stars:>50 archived:false", "terms": ["linux", "linux-app", "linux-tools", "linux-utility", "linux-desktop"]},
}
DEFAULT_CATEGORY = "Other"

# Legacy names are migrated in SQLite during the next application startup.
# These values are not displayed in the English interface.
LEGACY_CATEGORY_NAMES = {
    "Webdesign": "Web Design",
    "KI & Machine Learning": "AI & Machine Learning",
    "Automatisierung": "Automation",
    "Daten & Analyse": "Data & Analytics",
    "Robotik": "Robotics",
    "Sonstiges": "Other",
}


def classify(repo):
    topics = [str(t).lower() for t in repo.get("topics", [])]
    description = str(repo.get("description") or "").lower()
    name = str(repo.get("full_name") or "").lower()
    best, best_score = DEFAULT_CATEGORY, 0
    for category, spec in CATEGORIES.items():
        score = 0
        for term in spec["terms"]:
            if term in topics:
                score += 4
            if term in name:
                score += 2
            if term in description:
                score += 1
        if score > best_score:
            best, best_score = category, score
    return best


PLATFORM_TOPICS = {
    "Windows Tools": {"windows", "windows-app", "windows-tools", "win32", "windows-utility"},
    "macOS Tools": {"macos", "macos-app", "osx", "mac-app", "macos-tools", "mac-utility"},
    "Linux Tools": {"linux", "linux-app", "linux-tools", "linux-utility", "linux-desktop"},
}

PLATFORM_NAMES = {
    "Windows Tools": re.compile(r"\bwindows\b", re.IGNORECASE),
    "macOS Tools": re.compile(r"\b(?:macos|mac\s*os\s*x|os\s*x)\b", re.IGNORECASE),
    "Linux Tools": re.compile(r"\blinux\b", re.IGNORECASE),
}

# Only inspect platform-specific clauses. A generic mention of "Windows" in an
# arbitrary description is not evidence that a tool *runs* on Windows.
# The pattern deliberately works with short GitHub descriptions such as
# "Tool for automatic PS5 executables porting to Linux and Windows".
PLATFORM_SUPPORT_PHRASES = re.compile(
    r"\b(?:for|supports?|runs?\s+on|works?\s+on|available\s+(?:for|on)|"
    r"compatible\s+with|port(?:s|ing|ed)?\s+to|targets?|targeting|builds?\s+for)\b"
    r"(?:\s+|\s*:\s*)([^.;!?]{0,120})",
    re.IGNORECASE,
)


def platforms_from_description(description):
    """Infer OS tags from an explicit support/target platform clause.

    This is a heuristic, not a compatibility guarantee. Manual category choices
    override auto-detection in the database.
    """
    if not description:
        return set()
    detected = set()
    for clause in PLATFORM_SUPPORT_PHRASES.findall(str(description)):
        for category, matcher in PLATFORM_NAMES.items():
            if matcher.search(clause):
                detected.add(category)
    return detected


def classify_multiple(repo, primary=None):
    """Primary category plus operating systems described in GitHub metadata.

    Explicit OS topics and support phrases in the repository description are
    considered. "cross-platform" alone does not imply all three OSes.
    """
    result = {primary or classify(repo)}
    topics = {str(t).lower() for t in repo.get("topics", [])}
    for category, markers in PLATFORM_TOPICS.items():
        if topics.intersection(markers):
            result.add(category)
    result.update(platforms_from_description(repo.get("description")))
    # A known emerging topic is matched against a controlled taxonomy. Never
    # automatically create arbitrary categories from unknown GitHub topics.
    robotics_topics = {"robotics", "robotic", "robot-operating-system", "ros", "ros2", "robot-arm", "autonomous-robot"}
    description = str(repo.get("description") or "")
    if topics.intersection(robotics_topics) or re.search(r"\b(?:robotic(?:s)?|robot\s+arm|robot\s+operating\s+system|ros\s*2)\b", description, re.I):
        result.add("Robotics")
    return sorted(result)
