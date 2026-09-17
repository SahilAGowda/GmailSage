"""
All Gmail-mutating actions live here, and only here — one place to audit.
Nothing in this file deletes mail. Archiving = removing INBOX label,
which is fully reversible from All Mail.
"""

LABEL_PREFIX = "Triage"

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

_label_id_cache = {}


def _get_or_create_label(service, label_name: str) -> str:
    if label_name in _label_id_cache:
        return _label_id_cache[label_name]

    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"] == label_name:
            _label_id_cache[label_name] = lbl["id"]
            return lbl["id"]

    created = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
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
    """Reverses triage: removes Triage label and restores INBOX."""
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
