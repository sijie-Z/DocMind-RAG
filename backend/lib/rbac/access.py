from typing import Dict, Any


def is_accessible(user: Dict[str, Any], meta: Dict[str, Any] | None) -> bool:
    """
    功能：判断用户是否有权访问该文本块。
    小白解释：分三层：私人->组织->公开，满足之一即可访问。
    """
    if not meta:
        return True

    visibility = (meta.get("visibility") or "public").lower()
    owner = meta.get("owner")
    org_tag = meta.get("org_tag")

    if visibility == "public":
        return True
    if visibility == "private":
        return bool(user and user.get("id") == owner)
    if visibility == "org":
        u_tags = set(user.get("org_tags") or [])
        return org_tag in u_tags
    return False
    