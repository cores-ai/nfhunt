import io
import os
import re
import json
import hashlib
import zipfile
import tempfile
import subprocess
import contextlib
import logging

logger = logging.getLogger(__name__)

def parse_combo_line(line: str):
    """Parses a single line for username:password or email:password, ignoring headers and junk."""
    line = line.strip()
    if not line or len(line) < 5:
        return None
    if line.startswith(
        ("#", ".", "http://", "https://", "Cookie:", "[", "{", "<", "FORMAT", "PROCESS", "~", "=", "-", "*", "!", "/", " ", "▶", "•", "★", "🔥")
    ):
        return None

    # 1. Pipe format combo with metadata (e.g., user@domain.com:password | Plan = ... | Country = ...)
    if "|" in line:
        m = re.match(r"^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})[:;|\t]([^\s|;]+)", line)
        if m:
            u, p = m.group(1).strip(), m.group(2).strip()
            if p and not p.startswith(("http://", "https://")):
                return f"{u}:{p}"

    # 2. Key-value style: Email: ... Password: ...
    if re.search(r"email\s*[:=]", line, re.IGNORECASE) and re.search(r"pass(?:word)?\s*[:=]", line, re.IGNORECASE):
        em = re.search(r"email\s*[:=]\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", line, re.IGNORECASE)
        pw = re.search(r"pass(?:word)?\s*[:=]\s*([^\s,;]+)", line, re.IGNORECASE)
        if em and pw:
            return f"{em.group(1).strip()}:{pw.group(1).strip()}"

    # 3. Standard user:pass or email:pass
    m = re.match(r"^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})[:;|\t]([^\s\r\n]+)$", line)
    if m:
        u, p = m.group(1).strip(), m.group(2).strip()
        if p and not p.startswith(("http://", "https://")):
            return f"{u}:{p}"

    return None

def get_item_unique_fingerprint(item_str: str) -> str:
    s = str(item_str).strip()
    if not s:
        return ""

    # 1. NetflixId cookie
    nid_m = re.search(r"(?:NetflixId|netflixid)[\t=:\s]+([^\t;\r\n\s\"]+)", s)
    if nid_m:
        val = nid_m.group(1).strip()
        if len(val) > 10:
            return f"netflixid:{val[:80]}"

    # 2. SecureNetflixId
    snid_m = re.search(r"(?:SecureNetflixId|securenetflixid)[\t=:\s]+([^\t;\r\n\s\"]+)", s)
    if snid_m:
        val = snid_m.group(1).strip()
        if len(val) > 10:
            return f"sec_netflixid:{val[:80]}"

    # 3. NFToken parameter or link
    token_m = re.search(r"nftoken=([a-zA-Z0-9%_\-+=/]+)", s, re.IGNORECASE)
    if token_m:
        val = token_m.group(1).strip()
        if len(val) > 10:
            return f"nftoken:{val[:80]}"

    # 4. Combo Email:Password
    combo_m = re.match(r"^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}):", s)
    if combo_m:
        return f"email:{combo_m.group(1).lower().strip()}"

    return f"hash:{hashlib.md5(s.encode('utf-8', errors='ignore')).hexdigest()}"

def deduplicate_items(items: list) -> list:
    seen = set()
    deduped = []
    for item in items:
        fp = get_item_unique_fingerprint(item)
        if not fp:
            continue
        if fp not in seen:
            seen.add(fp)
            deduped.append(item)
    return deduped

def extract_items_from_text(content: str) -> list[str]:
    """
    Universal smart item extractor that detects and accurately splits:
    - Multi-account HIT logs (Netflix HIT NO.1, Account Details:, box borders)
    - Direct Watch / NF Token link lists
    - 'Valid Cookie' delimiter blocks
    - JSON Array of cookies (single or concatenated)
    - Netscape cookie blocks with multiple accounts
    - Single account text files
    - Line-by-line combos and standalone tokens
    """
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        return []

    # 1. JSON Array of cookies
    if content.startswith("[") and content.endswith("]"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return [content]
        except Exception:
            pass

    # Concatenated JSON arrays (e.g. ][ or ]\n[)
    if "][" in content or re.search(r"\]\s*\[", content):
        parts = re.split(r"\]\s*\[", content)
        json_blocks = []
        for i, p in enumerate(parts):
            chunk = p
            if not chunk.startswith("["):
                chunk = "[" + chunk
            if not chunk.endswith("]"):
                chunk = chunk + "]"
            try:
                json.loads(chunk)
                json_blocks.append(chunk)
            except Exception:
                pass
        if len(json_blocks) >= 2:
            return deduplicate_items(json_blocks)

    # 2. "Valid Cookie" delimiter format
    if "Valid Cookie" in content:
        raw_blocks = content.split("Valid Cookie")
        items = []
        for b in raw_blocks:
            b_str = b.strip()
            if not b_str:
                continue
            if "nftoken=" in b_str and not ("\t" in b_str or "NetflixId=" in b_str):
                match = re.search(r"https?://[^\s]*netflix\.com[^\s]*nftoken=[^\s]+|https?://[^\s]*nftoken=[^\s]+", b_str)
                items.append(match.group(0).strip() if match else b_str)
            else:
                items.append("Valid Cookie\n\n" + b_str if not b_str.startswith("Valid Cookie") else b_str)
        if items:
            return deduplicate_items(items)

    # 3. Checker Hit logs / multi-account result formats (HIT NO.1, ACCOUNT NO.1)
    hit_pat = re.compile(
        r"(?mi)^[ \t]*(?:[━=\-]{4,}\s*\n)?[ \t]*(?:Netflix\s+)?(?:HIT|ACCOUNT|RESULT)\s*(?:NO\.?|#|:)?\s*\d+",
        re.MULTILINE,
    )
    indices = [m.start() for m in hit_pat.finditer(content)]
    if len(indices) >= 2:
        blocks = []
        for i in range(len(indices)):
            start = indices[i]
            end = indices[i + 1] if i + 1 < len(indices) else len(content)
            chunk = content[start:end].strip()
            if chunk and (
                "netflixid" in chunk.lower()
                or "nftoken=" in chunk.lower()
                or "securenetflixid" in chunk.lower()
                or "email:" in chunk.lower()
            ):
                blocks.append(chunk)
        if len(blocks) >= 2:
            return deduplicate_items(blocks)

    # 4. Repeating Account Details: blocks
    acc_pat = re.compile(r"(?mi)^[ \t]*(?:[━=\-]{4,}\s*\n)?[ \t]*(?:👤\s*)?Account\s+Details:\s*$", re.MULTILINE)
    indices = [m.start() for m in acc_pat.finditer(content)]
    if len(indices) >= 2:
        blocks = []
        for i in range(len(indices)):
            start = indices[i]
            end = indices[i + 1] if i + 1 < len(indices) else len(content)
            chunk = content[start:end].strip()
            if chunk and (
                "netflixid" in chunk.lower()
                or "nftoken=" in chunk.lower()
                or "securenetflixid" in chunk.lower()
            ):
                blocks.append(chunk)
        if len(blocks) >= 2:
            return deduplicate_items(blocks)

    # 5. Direct Watch / Login Link separated blocks
    token_pat = re.compile(
        r"(?mi)^[ \t]*(?:Direct\s+Watch|Phone\s+Login|PC\s+Login|TV\s+Activation|Token\s+Link|Token):\s*https?://",
        re.MULTILINE,
    )
    indices = [m.start() for m in token_pat.finditer(content)]
    if len(indices) >= 2 and "netflixid" not in content.lower():
        blocks = []
        nftoken_regex = re.compile(r"(https?://[^\s]*netflix\.com[^\s]*nftoken=[^\s]+|https?://[^\s]*nftoken=[^\s]+)")
        for line in content.splitlines():
            m = nftoken_regex.search(line)
            if m:
                blocks.append(m.group(1))
        if blocks:
            return deduplicate_items(blocks)

    # 6. Multi-dash horizontal separators between accounts
    if re.search(r"\n[━=\-]{8,}\n", content):
        blocks = [b.strip() for b in re.split(r"\n[━=\-]{8,}\n", content) if b.strip()]
        valid_blocks = [
            b for b in blocks
            if ("netflixid" in b.lower() or "nftoken=" in b.lower() or "securenetflixid" in b.lower())
        ]
        if len(valid_blocks) >= 2:
            return deduplicate_items(valid_blocks)

    # 7. Check if predominantly Netscape cookie format
    lines = content.splitlines()
    netscape_lines = [
        line_str for line_str in lines
        if ("\t" in line_str or "TRUE\t" in line_str or "FALSE\t" in line_str) and not line_str.startswith("#")
    ]
    valid_non_comment = [line_str for line_str in lines if line_str.strip() and not line_str.strip().startswith("#")]

    if netscape_lines and len(netscape_lines) >= max(2, len(valid_non_comment) * 0.4):
        # Check if file contains MULTIPLE accounts
        netflixid_count = sum(
            1 for line_str in lines
            if any(k in line_str.lower() for k in ["netflixid\t", "netflixid ", "securenetflixid\t", "securenetflixid "])
        )
        if netflixid_count > 1:
            accounts = []
            curr_acc = []
            for line_str in lines:
                s = line_str.strip()
                is_header = s.startswith(("# Netscape", "# HTTP Cookie File", "===", "---"))
                if not s:
                    if curr_acc and any("netflixid" in line_item.lower() for line_item in curr_acc):
                        accounts.append("\n".join(curr_acc))
                        curr_acc = []
                    continue
                if is_header and curr_acc and any("netflixid" in line_item.lower() for line_item in curr_acc):
                    accounts.append("\n".join(curr_acc))
                    curr_acc = []
                curr_acc.append(s)
            if curr_acc and any("netflixid" in line_item.lower() for line_item in curr_acc):
                accounts.append("\n".join(curr_acc))
            if len(accounts) >= 2:
                return deduplicate_items(accounts)
        return [content]

    # 8. Fallback: Line-by-line combo & token extraction
    items = []
    current_cookie_lines = []
    nftoken_regex = re.compile(r"(https?://[^\s]*netflix\.com[^\s]*nftoken=[^\s]+|https?://[^\s]*nftoken=[^\s]+)")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_cookie_lines:
                items.append("\n".join(current_cookie_lines))
                current_cookie_lines = []
            continue

        if re.match(r"^[-=*#]{3,}$", stripped):
            if current_cookie_lines:
                items.append("\n".join(current_cookie_lines))
                current_cookie_lines = []
            continue

        has_session = (
            "netflixid=" in stripped.lower()
            or "securenetflixid=" in stripped.lower()
            or "cookies =" in stripped.lower()
            or "nftoken=" in stripped.lower()
        )
        if has_session:
            if current_cookie_lines:
                items.append("\n".join(current_cookie_lines))
                current_cookie_lines = []
            items.append(stripped)
            continue

        parsed_combo = parse_combo_line(stripped)
        if parsed_combo:
            if current_cookie_lines:
                items.append("\n".join(current_cookie_lines))
                current_cookie_lines = []
            items.append(parsed_combo)
            continue

        token_match = nftoken_regex.search(stripped)
        if token_match:
            if current_cookie_lines:
                items.append("\n".join(current_cookie_lines))
                current_cookie_lines = []
            items.append(token_match.group(1))
            continue

        if ("\t" in stripped and (".netflix.com" in stripped or "TRUE" in stripped or "FALSE" in stripped)) or "netflixid=" in stripped.lower() or "securenetflixid=" in stripped.lower():
            current_cookie_lines.append(stripped)

    if current_cookie_lines:
        items.append("\n".join(current_cookie_lines))

    if items:
        return deduplicate_items(items)

    return [content]

def _decode_bytes_to_text(b: bytes) -> str | None:
    if not b:
        return None
    # Reject raw archives and heavy binaries
    if b.startswith(b"PK\x03\x04") or b[:1024].count(b"\x00") > 10:
        return None
    for enc in ["utf-8", "utf-16", "cp1252", "latin1"]:
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return b.decode("utf-8", errors="ignore")
    except Exception:
        return None

def extract_all_text_files_recursively(file_bytes: bytes, file_name: str, depth: int = 0) -> list[tuple[str, str]]:
    """
    Recursively extracts all text files from archives (zip inside zip, rar, tar, 7z).
    Returns list of (inner_filename, decoded_text).
    """
    if depth > 10 or not file_bytes:
        return []

    results = []
    fname_lower = file_name.lower()

    # 1. Native ZIP handling (or file starting with PK\x03\x04 header)
    if fname_lower.endswith(".zip") or file_bytes.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                for member_name in zf.namelist():
                    if member_name.endswith(("/", "\\")) or "__MACOSX" in member_name or os.path.basename(member_name).startswith("."):
                        continue
                    try:
                        inner_bytes = zf.read(member_name)
                    except Exception:
                        continue

                    inner_lower = member_name.lower()
                    # Check if the inner file is itself an archive (nested zip inside zip!)
                    if any(inner_lower.endswith(ext) for ext in [".zip", ".rar", ".tar", ".tar.gz", ".tgz", ".7z", ".gz"]) or inner_bytes.startswith(b"PK\x03\x04"):
                        nested = extract_all_text_files_recursively(inner_bytes, member_name, depth + 1)
                        results.extend(nested)
                    else:
                        text = _decode_bytes_to_text(inner_bytes)
                        if text and text.strip():
                            results.append((member_name, text))
            return results
        except Exception as e:
            logger.debug("Nested zip extraction error on %s: %s", file_name, e)

    # 2. Other archives (.rar, .tar, .7z) using system tar
    if any(fname_lower.endswith(ext) for ext in [".rar", ".tar", ".tar.gz", ".tgz", ".7z", ".gz"]):
        suffix = os.path.splitext(fname_lower)[1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(file_bytes)
            tf_path = tf.name

        with tempfile.TemporaryDirectory() as td:
            try:
                subprocess.run(
                    ["tar", "-xf", tf_path, "-C", td],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30
                )
                for root, _, files in os.walk(td):
                    for f in files:
                        if f.startswith("."):
                            continue
                        full_path = os.path.join(root, f)
                        try:
                            with open(full_path, "rb") as fr:
                                b = fr.read(50 * 1024 * 1024)
                            if any(f.lower().endswith(ext) for ext in [".zip", ".rar", ".tar", ".7z", ".gz"]) or b.startswith(b"PK\x03\x04"):
                                nested = extract_all_text_files_recursively(b, f, depth + 1)
                                results.extend(nested)
                            else:
                                text = _decode_bytes_to_text(b)
                                if text and text.strip():
                                    results.append((f, text))
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    os.remove(tf_path)
        return results

    # 3. Plain text file fallback
    text = _decode_bytes_to_text(file_bytes)
    if text and text.strip():
        results.append((file_name, text))

    return results

def parse_uploaded_file_to_cookies(file_path: str, file_name: str) -> list[dict]:
    """
    Parses any uploaded file (ZIP, nested ZIPs, RAR, TXT, multi-account logs) into individual cookie/combo items.
    Returns: list of dicts [{'name': '...', 'content': '...'}]
    """
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read(100 * 1024 * 1024)  # up to 100MB
    except Exception as e:
        logger.error("Error reading file %s: %s", file_path, e)
        return []

    extracted_files = extract_all_text_files_recursively(file_bytes, file_name)
    cookies_data = []

    for inner_name, text in extracted_files:
        if not text or not text.strip():
            continue

        clean_inner_name = os.path.basename(inner_name)
        items = extract_items_from_text(text)

        if len(items) > 1:
            for idx, item in enumerate(items, 1):
                cookies_data.append({
                    "name": f"{clean_inner_name} [#{idx}]",
                    "content": item
                })
        elif len(items) == 1:
            cookies_data.append({
                "name": clean_inner_name,
                "content": items[0]
            })
        else:
            if any(k in text.lower() for k in ["netflix.com", "netflixid", "securenetflixid", "cookie", "nftoken"]) or "@" in text:
                cookies_data.append({
                    "name": clean_inner_name,
                    "content": text
                })

    return cookies_data
