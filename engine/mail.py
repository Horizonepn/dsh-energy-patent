#!/usr/bin/env python3
"""Read a patent mailbox over IMAP and send drafts over SMTP.
Emits exactly one JSON object on stdout. Passwords are read from config,
environment, or a per-call test spec, and are never emitted.
"""

from __future__ import annotations
import argparse, email.policy, html, imaplib, json, os, re, smtplib, socket, ssl, sys
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesHeaderParser, BytesParser
from email.utils import (formataddr, formatdate, getaddresses, make_msgid,
                         parseaddr, parsedate_to_datetime)

socket.setdefaulttimeout(20)
HEADER_FIELDS = "FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

class MailError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message

def decode_rfc2047(value) -> str:
    """Decode RFC 2047 headers, tolerating unknown or wrong charsets."""
    if value is None:
        return ""
    try:
        chunks = decode_header(str(value))
    except Exception:
        return str(value)
    return "".join(chunk if isinstance(chunk, str) else _decode_bytes(chunk, charset)
                   for chunk, charset in chunks)
def html_to_text(value) -> str:
    """Remove markup, turn block tags into newlines, and unescape entities."""
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", str(value))
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    block = r"p|div|li|tr|h[1-6]|blockquote|pre|table|section|article"
    text = re.sub(r"(?i)</(?:" + block + r")\s*>", "\n", text)
    text = re.sub(r"(?i)<(?:" + block + r")\b[^>]*>", "\n", text)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", "", text))
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
def _decode_bytes(raw: bytes, charset: str | None = None) -> str:
    if not raw:
        return ""
    for encoding in ([charset] if charset else []) + ["utf-8", "gb18030", "latin-1"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")
def _safe_filename(name) -> str:
    """Return one safe path component with no separators, .., or controls."""
    raw = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', "_", decode_rfc2047(name).strip())
    while ".." in raw:
        raw = raw.replace("..", "_")
    raw = raw.strip(" .") or "attachment"
    stem, extension = os.path.splitext(raw)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    if stem.upper() in reserved:
        stem += "_"
    extension = extension[:20]
    return stem[:max(1, 160 - len(extension))] + extension
def _write_unique(directory: str, filename: str, data: bytes) -> str:
    os.makedirs(directory, exist_ok=True)
    stem, extension = os.path.splitext(filename)
    for index in range(10000):
        candidate = filename if index == 0 else f"{stem}-{index}{extension}"
        path = os.path.abspath(os.path.join(directory, candidate))
        try:
            with open(path, "xb") as handle:
                handle.write(data)
            return path
        except FileExistsError:
            continue
    raise OSError("could not create a unique filename")
def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, (bool, int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default
def _to_int(value, default: int, label: str, code: str, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise MailError(code, f"Invalid {label}") from None
    if isinstance(value, bool) or not low <= number <= high:
        raise MailError(code, f"Invalid {label}: must be between {low} and {high}")
    return number
def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

def _collect_secrets(*objects) -> set[str]:
    found = set()
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                key, env = str(key).lower(), None
                if key == "password" and value:
                    found.add(str(value))
                if key == "passwordenv" and isinstance(value, str) and value:
                    env = os.environ.get(value)
                if env:
                    found.add(env)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
    for obj in objects:
        walk(obj)
    return found
def _redact(message, secrets: set[str]) -> str:
    text = str(message or "Unknown error")
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    return text.replace("\r", " ").replace("\n", " ")[:1500]

def _load_config(path: str) -> dict:
    if not path:
        raise MailError("CONFIG_ERROR", "No config file specified")
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        raise MailError("CONFIG_ERROR", "Config file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise MailError("CONFIG_ERROR", f"Cannot read config: {exc}") from None
    except json.JSONDecodeError as exc:
        raise MailError("CONFIG_ERROR", f"Invalid config JSON at line {exc.lineno}") from None
    if not isinstance(config, dict):
        raise MailError("CONFIG_ERROR", "Config root must be an object")
    return config
def _read_spec(path: str | None) -> dict:
    if not path:
        return {}
    try:
        raw = sys.stdin.buffer.read() if path == "-" else open(path, "rb").read()
    except OSError as exc:
        raise MailError("INPUT_ERROR", f"Cannot read input spec: {exc}") from None
    if not raw.strip():
        return {}
    try:
        spec = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MailError("INPUT_ERROR", f"Invalid input spec JSON: {exc}") from None
    if not isinstance(spec, dict):
        raise MailError("INPUT_ERROR", "Input spec root must be an object")
    return spec
def _resolve_password(section: dict, spec: dict) -> str | None:
    supplied = spec.get("password") if isinstance(spec, dict) else None
    if supplied:
        return str(supplied)
    if section.get("password"):
        return str(section["password"])
    env_name = section.get("passwordEnv")
    return os.environ.get(env_name) if isinstance(env_name, str) and env_name else None

def _settings(config: dict, name: str) -> dict:
    section = config.get(name)
    if not isinstance(section, dict):
        raise MailError("CONFIG_ERROR", f"Config section '{name}' is missing")
    host = section.get("host")
    if not isinstance(host, str) or not host.strip():
        raise MailError("CONFIG_ERROR", f"Config section '{name}' has no host")
    default_port = 993 if name == "imap" else 465
    port = _to_int(section.get("port", default_port), default_port, "port",
                   "CONFIG_ERROR", 1, 65535)
    user = section.get("user")
    return {
        "host": host.strip(), "port": port,
        "secure": _as_bool(section.get("secure"),
                           True if name == "imap" else port in (465, 8465)),
        "starttls": _as_bool(section.get("starttls"), False),
        "user": user.strip() if isinstance(user, str) else "", "section": section,
    }
def _mailbox(config: dict, requested=None) -> str:
    if requested:
        mailbox = str(requested)
    else:
        section = config.get("imap")
        mailbox = str(section.get("mailbox") or "INBOX") if isinstance(section, dict) else "INBOX"
    mailbox = mailbox.strip() or "INBOX"
    if "\r" in mailbox or "\n" in mailbox:
        raise MailError("CONFIG_ERROR", "Invalid mailbox name")
    return mailbox
def _uid(spec: dict) -> str:
    value = str(spec.get("uid") or "").strip()
    if not value.isdigit() or int(value) <= 0:
        raise MailError("INPUT_ERROR", "A positive numeric uid is required")
    return value
def _logout(connection) -> None:
    try:
        connection.logout()
    except Exception:
        pass

def _connect_imap(config: dict, spec: dict):
    settings = _settings(config, "imap")
    password = _resolve_password(settings["section"], spec)
    if settings["user"] and not password:
        raise MailError("NO_PASSWORD", "No IMAP password in config, environment, or spec")
    connection = None
    try:
        if settings["secure"]:
            connection = imaplib.IMAP4_SSL(settings["host"], settings["port"], timeout=20)
        else:
            connection = imaplib.IMAP4(settings["host"], settings["port"], timeout=20)
            if settings["starttls"]:
                connection.starttls(ssl_context=ssl.create_default_context())
        if settings["user"]:
            connection.login(settings["user"], password)
        return connection
    except Exception:
        if connection:
            _logout(connection)
        raise
def _select(connection, mailbox: str) -> None:
    status, _ = connection.select(mailbox, readonly=True)
    if status != "OK":
        raise MailError("IMAP_ERROR", f"Could not select mailbox '{mailbox}'")
def _fetch_bytes(data, marker: str):
    marker = marker.encode("ascii").upper()
    chunks, found = [], False
    for item in data or []:
        if isinstance(item, tuple) and item:
            prefix = item[0] if isinstance(item[0], bytes) else b""
            if marker in prefix.upper():
                found = True
                chunks.extend(part for part in item[1:] if isinstance(part, bytes))
    return b"".join(chunks) if found else None
def _seen(data) -> bool:
    return any(isinstance(h := (i[0] if isinstance(i, tuple) and i else i), bytes)
               and b"\\seen" in h.lower() for i in data or [])
def _fetch_header(connection, uid: str):
    command = "(FLAGS BODY.PEEK[HEADER.FIELDS (" + HEADER_FIELDS + ")])"
    status, data = connection.uid("FETCH", uid, command)
    if status != "OK":
        raise MailError("IMAP_ERROR", f"Could not fetch headers for UID {uid}")
    raw = _fetch_bytes(data, "BODY[HEADER")
    if raw is None:
        raise MailError("IMAP_ERROR", f"Message UID {uid} was not found")
    return BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw), _seen(data)
def _header(message, name: str) -> str:
    value = message.get(name)
    return "" if value is None else decode_rfc2047(value).strip()
def _date_iso(raw: str) -> str:
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed is None:
            return raw
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
                else parsed).isoformat()
    except Exception:
        return raw
def _refs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(ref for ref in _refs(item) if ref not in result)
        return result
    text = str(value)
    return list(dict.fromkeys(re.findall(r"<[^<>]+>", text)
                              or [part for part in text.split() if part]))
def _payload(part, as_text=False):
    try:
        data = part.get_payload(decode=True)
    except Exception:
        data = None
    if data is None:
        raw = part.get_payload()
        data = raw.encode("utf-8", "replace") if isinstance(raw, str) else b""
    return _decode_bytes(data, part.get_content_charset()) if as_text else data
def _is_attachment(part) -> bool:
    if part.is_multipart():
        return False
    if _header(part, "Content-Disposition").lower().startswith("attachment"):
        return True
    try:
        return bool(part.get_filename())
    except Exception:
        return False
def _body_text(message) -> str:
    plain, rich = [], []
    for part in message.walk():
        if part.is_multipart() or _is_attachment(part):
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        text = _payload(part, True).strip()
        if text and content_type == "text/html":
            text = html_to_text(text)
        if text:
            (plain if content_type == "text/plain" else rich).append(text)
    return "\n\n".join(plain or rich).strip()
def _attachments(message):
    items = []
    for part in message.walk():
        if not _is_attachment(part):
            continue
        try:
            filename = decode_rfc2047(part.get_filename() or "").strip()
        except Exception:
            filename = ""
        items.append({"filename": filename or f"attachment-{len(items) + 1}",
                      "contentType": part.get_content_type(), "data": _payload(part)})
    return items
def _snippet(raw: bytes) -> str:
    text = _decode_bytes(raw)
    if re.search(r"(?is)<(?:html|body|p|div|br)\b", text):
        text = html_to_text(text)
    return re.sub(r"\s+", " ",
                  text.replace("\r\n", "\n").replace("\r", "\n")).strip()[:160]
def _raw_has_attachment(raw: bytes) -> bool:
    pattern = (br"(?im)^(?:content-disposition:\s*attachment\b|"
               br"content-type:[^\r\n]*\b(?:filename|name)\s*=)")
    return bool(raw and re.search(pattern, raw[:500000]))

def action_inbox(config: dict, spec: dict) -> dict:
    mailbox = _mailbox(config, spec.get("mailbox"))
    limit = _to_int(spec.get("limit"), 20, "limit", "INPUT_ERROR", 1, 1000)
    since_days = _to_int(spec.get("sinceDays"), 30, "sinceDays", "INPUT_ERROR", 0, 36500)
    query = str(spec.get("query") or "").strip().casefold()
    connection = _connect_imap(config, spec)
    try:
        _select(connection, mailbox)
        criteria = (["UNSEEN"] if _as_bool(spec.get("unseenOnly"), False) else [])
        day = datetime.now().date() - timedelta(days=since_days)
        criteria.append(f"SINCE {day.day:02d}-{MONTHS[day.month - 1]}-{day.year}")
        status, data = connection.uid("search", None, *criteria)
        if status != "OK":
            raise MailError("IMAP_ERROR", "IMAP search failed")
        uids = [token.decode("ascii") for chunk in data or [] if isinstance(chunk, bytes)
                for token in chunk.split() if token.isdigit()]
        page = uids[-(max(limit * 4, 100) if query else limit):]
        messages = []
        for uid in page:
            header_message, seen = _fetch_header(connection, uid)
            snippet, has_attachments = "", False
            try:
                text_status, text_data = connection.uid("FETCH", uid, "(BODY.PEEK[TEXT])")
                if text_status == "OK":
                    raw = _fetch_bytes(text_data, "BODY[TEXT]")
                    if raw:
                        snippet, has_attachments = _snippet(raw), _raw_has_attachment(raw)
            except Exception:
                pass
            subject, full_from = _header(header_message, "Subject"), _header(header_message, "From")
            display, address = parseaddr(full_from)
            messages.append({
                "uid": uid, "subject": subject, "from": full_from,
                "fromName": decode_rfc2047(display), "fromAddr": address,
                "to": _header(header_message, "To"),
                "date": _date_iso(_header(header_message, "Date")), "snippet": snippet,
                "hasAttachments": has_attachments, "seen": seen,
                "messageId": _header(header_message, "Message-ID")})
        if query:
            messages = [item for item in messages if query in " ".join(
                (item["subject"], item["from"], item["snippet"])).casefold()][-limit:]
        return {"mailbox": mailbox, "count": len(messages), "messages": messages}
    finally:
        _logout(connection)

def action_read(config: dict, spec: dict) -> dict:
    uid = _uid(spec)
    save_dir = spec.get("saveDir") or config.get("attachmentDir") or ""
    connection = _connect_imap(config, spec)
    try:
        _select(connection, _mailbox(config))
        status, data = connection.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            raise MailError("IMAP_ERROR", f"Could not fetch message UID {uid}")
        raw = _fetch_bytes(data, "BODY[]")
        if not raw:
            raise MailError("IMAP_ERROR", f"Message UID {uid} was not found")
        message = message_from_bytes(raw, policy=email.policy.compat32)
        full_from = _header(message, "From")
        display, address = parseaddr(full_from)
        attachments, saved = [], []
        directory = os.path.abspath(os.path.expanduser(str(save_dir))) if save_dir else ""
        for item in _attachments(message):
            safe_name = _safe_filename(item["filename"])
            info = {"filename": item["filename"], "contentType": item["contentType"],
                    "size": len(item["data"])}
            if _as_bool(spec.get("saveAttachments"), False) and directory:
                try:
                    path = _write_unique(directory, safe_name, item["data"])
                except OSError as exc:
                    raise MailError("IMAP_ERROR", f"Could not save attachment: {exc}") from None
                info["savedPath"] = path
                saved.append({"filename": safe_name, "savedPath": path,
                              "size": len(item["data"])})
            attachments.append(info)
        return {
            "uid": uid, "subject": _header(message, "Subject"), "from": full_from,
            "fromName": decode_rfc2047(display), "fromAddr": address,
            "to": _header(message, "To"), "date": _date_iso(_header(message, "Date")),
            "messageId": _header(message, "Message-ID"),
            "inReplyTo": _header(message, "In-Reply-To"),
            "references": _refs(_header(message, "References")),
            "bodyText": _body_text(message), "attachments": attachments,
            "savedAttachments": saved}
    finally:
        _logout(connection)
def _fetch_original(config: dict, spec: dict, uid: str):
    connection = _connect_imap(config, spec)
    try:
        _select(connection, _mailbox(config))
        return _fetch_header(connection, uid)[0]
    finally:
        _logout(connection)

def action_draft(config: dict, spec: dict) -> dict:
    smtp = config.get("smtp") if isinstance(config.get("smtp"), dict) else {}
    imap = config.get("imap") if isinstance(config.get("imap"), dict) else {}
    user = str(smtp.get("user") or imap.get("user") or "").strip()
    if not user:
        raise MailError("CONFIG_ERROR", "A mailbox user is required for drafts")
    to = spec.get("to") or ""
    to = ", ".join(str(item).strip() for item in to if str(item).strip()) \
        if isinstance(to, (list, tuple)) else str(to).strip()
    subject = str(spec.get("subject") or "").strip()
    body = str(spec.get("body") or "")
    in_reply_to = str(spec.get("inReplyTo") or "").strip()
    refs = _refs(spec.get("references"))
    if str(spec.get("uid") or "").strip():
        original = _fetch_original(config, spec, _uid(spec))
        original_from = _header(original, "From")
        original_subject = _header(original, "Subject")
        original_id = _header(original, "Message-ID")
        if not to:
            to = original_from
        if not subject:
            subject = (original_subject if re.match(r"^\s*re\s*:", original_subject, re.I)
                       else f"Re: {original_subject}".strip())
        if not in_reply_to and original_id:
            in_reply_to = original_id
        for ref in _refs(_header(original, "References")) + ([original_id] if original_id else []):
            if ref not in refs:
                refs.append(ref)
    cc = spec.get("cc") or []
    cc = [part.strip() for part in cc.split(",") if part.strip()] if isinstance(cc, str) else \
        [str(item).strip() for item in cc if str(item).strip()]
    signature = str(config.get("signature") or "")
    final_body = body + (("\n\n" + signature) if signature else "")
    message = EmailMessage()
    from_name = str(config.get("fromName") or "").strip()
    message["From"] = formataddr((from_name, user)) if from_name else user
    for key, value in (("To", to), ("Cc", ", ".join(cc)), ("In-Reply-To", in_reply_to),
                       ("References", " ".join(refs))):
        if value:
            message[key] = value
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    domain = user.rsplit("@", 1)[1] if "@" in user else None
    message["Message-ID"] = make_msgid(domain=domain) if domain else make_msgid()
    message.set_content(final_body, cte="quoted-printable")
    raw = message.as_bytes(policy=email.policy.SMTP)
    directory = os.path.abspath(os.path.expanduser(str(
        spec.get("draftDir") or config.get("draftDir") or "drafts")))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"-{2,}", "-", "".join(
        char if char.isalnum() or char in "-_" else "-" for char in subject)).strip("-_")
    path = _write_unique(directory, f"{stamp}-{(slug[:48] or 'draft')}.eml", raw)
    return {"draftPath": path, "to": to, "subject": subject, "body": final_body,
            "inReplyTo": in_reply_to, "references": refs, "bytes": len(raw)}

def action_send(config: dict, spec: dict) -> dict:
    if spec.get("confirm") != "SEND":
        raise MailError("CONFIRM_REQUIRED", "Refusing to send without confirm=SEND")
    if not spec.get("draftPath"):
        raise MailError("DRAFT_ERROR", "A draftPath is required")
    path = os.path.abspath(os.path.expanduser(str(spec["draftPath"])))
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise MailError("DRAFT_ERROR", f"Cannot read draft: {exc}") from None
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
    except Exception:
        raise MailError("DRAFT_ERROR", "Cannot parse the draft message") from None
    from_addr = parseaddr(_header(message, "From"))[1]
    if not from_addr:
        raise MailError("DRAFT_ERROR", "Draft has no From address")
    values = [str(value) for header in ("To", "Cc", "Bcc")
              for value in message.get_all(header, []) or []]
    recipients = list(dict.fromkeys(address for _, address in getaddresses(values) if address))
    if not recipients:
        raise MailError("DRAFT_ERROR", "Draft has no recipients")
    settings = _settings(config, "smtp")
    password = _resolve_password(settings["section"], spec)
    if settings["user"] and not password:
        raise MailError("NO_PASSWORD", "No SMTP password in config, environment, or spec")
    client = None
    try:
        if settings["secure"]:
            client = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=20,
                                      context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(settings["host"], settings["port"], timeout=20)
            if settings["starttls"]:
                client.starttls(context=ssl.create_default_context())
        if settings["user"]:
            client.login(settings["user"], password)
        client.send_message(message, from_addr=from_addr, to_addrs=recipients)
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                pass
    return {"sent": True, "to": recipients, "subject": _header(message, "Subject"),
            "messageId": _header(message, "Message-ID")}

def _run_status(path: str | None, spec: dict) -> dict:
    if not path:
        return {"ok": True, "configured": False, "reason": "No config file specified"}
    try:
        config = _load_config(path)
    except Exception as exc:
        return {"ok": True, "configured": False, "reason": _redact(exc, set())}
    try:
        imap = _settings(config, "imap")
        smtp = _settings(config, "smtp")
    except Exception as exc:
        return {"ok": True, "configured": False,
                "reason": _redact(exc, _collect_secrets(config, spec))}
    imap_password = _resolve_password(imap["section"], spec)
    smtp_password = _resolve_password(smtp["section"], spec)
    return {
        "ok": True, "configured": True,
        "imap": {key: imap[key] for key in ("host", "port", "user", "secure")} |
                {"passwordSet": imap_password is not None},
        "smtp": {key: smtp[key] for key in ("host", "port", "user", "secure")} |
                {"passwordSet": smtp_password is not None},
        "draftDir": str(config.get("draftDir") or ""),
        "attachmentDir": str(config.get("attachmentDir") or "")}

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise MailError("INPUT_ERROR", f"Invalid arguments: {message}")

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass
    secrets, action = set(), None
    try:
        parser = _Parser(prog="mail.py", description="Read a patent mailbox and send drafts.")
        parser.add_argument("--config", help="Path to config.json")
        parser.add_argument("--action", required=True,
                            choices=["status", "inbox", "read", "draft", "send"])
        parser.add_argument("--input", help="Path to spec.json, or - for stdin")
        args = parser.parse_args(argv)
        action = args.action
        spec = _read_spec(args.input)
        if action == "status":
            _emit(_run_status(args.config, spec))
            return 0
        if action == "send" and spec.get("confirm") != "SEND":
            raise MailError("CONFIRM_REQUIRED", "Refusing to send without confirm=SEND")
        config = _load_config(args.config)
        secrets = _collect_secrets(config, spec)
        result = {"inbox": action_inbox, "read": action_read, "draft": action_draft,
                  "send": action_send}[action](config, spec)
        _emit({"ok": True, **result})
        return 0
    except MailError as exc:
        _emit({"ok": False, "error": _redact(exc.message, secrets), "code": exc.code})
    except Exception as exc:
        code = {"inbox": "IMAP_ERROR", "read": "IMAP_ERROR", "draft": "DRAFT_ERROR",
                "send": "SMTP_ERROR"}.get(action, "CONFIG_ERROR")
        _emit({"ok": False, "error": _redact(exc, secrets), "code": code})
    return 1
if __name__ == "__main__":
    raise SystemExit(main())
