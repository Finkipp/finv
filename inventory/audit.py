from contextvars import ContextVar
from contextlib import contextmanager


_audit_context = ContextVar("inventory_audit_context", default={})


def bind_request(user, ip_address):
    return _audit_context.set({"user": user, "ip_address": ip_address, "note": ""})


def reset_request(token):
    _audit_context.reset(token)


def get_audit_context():
    return _audit_context.get()


def set_audit_note(note):
    context = dict(_audit_context.get())
    context["note"] = (note or "").strip()
    _audit_context.set(context)


def consume_audit_note():
    context = dict(_audit_context.get())
    note = context.get("note", "")
    context["note"] = ""
    _audit_context.set(context)
    return note


def audit_is_suspended():
    return bool(_audit_context.get().get("suspended"))


@contextmanager
def suspend_audit():
    context = dict(_audit_context.get())
    context["suspended"] = True
    token = _audit_context.set(context)
    try:
        yield
    finally:
        _audit_context.reset(token)
