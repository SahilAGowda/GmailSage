"""
All Gmail-mutating actions live here, and only here — one place to audit.
Nothing in this file deletes mail. Archiving = removing INBOX label,
which is fully reversible from All Mail.
"""

LABEL_PREFIX = "GmailSage"

CATEGORY_LABELS = {
    "interview": f"{LABEL_PREFIX}/Interview",
    "offer": f"{LABEL_PREFIX}/Offer",
    "deadline": f"{LABEL_PREFIX}/Deadline",
    "personal": f"{LABEL_PREFIX}/Personal",
    "job_alert": f"{LABEL_PREFIX}/Job Alerts",
    "newsletter": f"{LABEL_PREFIX}/Newsletter",
    "promo": f"{LABEL_PREFIX}/Promo",
    "learning": f"{LABEL_PREFIX}/Learning",
    "finance": f"{LABEL_PREFIX}/Finance",
    "social": f"{LABEL_PREFIX}/Social",
    "updates": f"{LABEL_PREFIX}/Updates",
    "other": f"{LABEL_PREFIX}/Other",
}

# Gmail label colors (backgroundColor + textColor) — curated for visibility
LABEL_COLORS = {
    "interview": {"backgroundColor": "#16a765", "textColor": "#ffffff"},  # green
    "offer": {"backgroundColor": "#0b804b", "textColor": "#ffffff"},      # dark green
    "deadline": {"backgroundColor": "#fb4c2f", "textColor": "#ffffff"},   # red
    "personal": {"backgroundColor": "#4986e7", "textColor": "#ffffff"},   # blue
    "job_alert": {"backgroundColor": "#999999", "textColor": "#ffffff"},  # gray
    "newsletter": {"backgroundColor": "#cccccc", "textColor": "#000000"}, # light gray
    "promo": {"backgroundColor": "#fad165", "textColor": "#000000"},      # yellow
    "learning": {"backgroundColor": "#a479e2", "textColor": "#ffffff"},   # purple
    "finance": {"backgroundColor": "#fbe983", "textColor": "#000000"},    # light yellow
    "social": {"backgroundColor": "#b6cff5", "textColor": "#0d3472"},     # light blue (valid)
    "updates": {"backgroundColor": "#a2dcc1", "textColor": "#000000"},    # mint
    "other": {"backgroundColor": "#efefef", "textColor": "#000000"},      # off-white
}

_label_id_cache = {}


def _get_or_create_label(service, label_name: str) -> str:
    if label_name in _label_id_cache:
        return _label_id_cache[label_name]

    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"] == label_name:
            _label_id_cache[label_name] = lbl["id"]
            # Patch color if missing/wrong (migrate Triage -> GmailSage)
            cat = next((k for k, v in CATEGORY_LABELS.items() if v == label_name), None)
            if cat and lbl.get("color") != LABEL_COLORS.get(cat):
                try:
                    service.users().labels().patch(
                        userId="me", id=lbl["id"],
                        body={"color": LABEL_COLORS[cat]}
                    ).execute()
                except Exception:
                    pass
            return lbl["id"]

    # Find category for color
    cat = next((k for k, v in CATEGORY_LABELS.items() if v == label_name), None)
    body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    if cat and cat in LABEL_COLORS:
        body["color"] = LABEL_COLORS[cat]

    created = (
        service.users()
        .labels()
        .create(userId="me", body=body)
        .execute()
    )
    _label_id_cache[label_name] = created["id"]
    return created["id"]


def apply_label_and_route(service, message_id: str, category: str, priority: str, skip_inbox_for_low: bool = True):
    label_name = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
    label_id = _get_or_create_label(service, label_name)

    add_labels = [label_id]
    remove_labels = []

    if skip_inbox_for_low and priority == "low":
        remove_labels.append("INBOX")  # archive — reversible, not delete

    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": add_labels, "removeLabelIds": remove_labels},
    ).execute()


def undo_route(service, message_id: str, category: str):
    """Reverses triage: removes GmailSage label and restores INBOX."""
    label_name = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
    # Find label id without creating it
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == label_name), None)
    remove = [label_id] if label_id else []
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": ["INBOX"], "removeLabelIds": remove},
    ).execute()
