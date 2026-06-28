#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate v2rayN / Clash / Mihomo subscription files.

Sources (in priority order):
  1. Alvin9999-newpac/fanqiang Wiki pages (vmess/vless/ss/ssr/trojan/hysteria2 URIs)
  2. Alvin9999's GitLab ipupdate repo - per-tool Clash YAML / JSON configs
     (this is the same set of URLs the bundled browsers fetch as "IP update address")

Output (under ./subscribe/):
  - v2rayn.txt            Plain text, one URI per line (modern v2rayN)
  - v2rayn_base64.txt     Base64-encoded blob (classic v2rayN subscription)
  - clash.yaml            Mihomo/Clash Meta YAML (merged from all sources)
  - meta.json             Generation metadata for debugging
"""

import base64
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# Configuration
# ============================================================

WIKI_SOURCES = [
    {
        "name": "v2ray免费账号",
        "url": "https://raw.githubusercontent.com/wiki/Alvin9999-newpac/fanqiang/v2ray%E5%85%8D%E8%B4%B9%E8%B4%A6%E5%8F%B7.md",
    },
    {
        "name": "ss免费账号",
        "url": "https://raw.githubusercontent.com/wiki/Alvin9999-newpac/fanqiang/ss%E5%85%8D%E8%B4%B9%E8%B4%A6%E5%8F%B7.md",
    },
    {
        "name": "Goflyway免费账号",
        "url": "https://raw.githubusercontent.com/wiki/Alvin9999-newpac/fanqiang/Goflyway%E5%85%8D%E8%B4%B9%E8%B4%A6%E5%8F%B7.md",
    },
]

# GitLab ipupdate repo - per-tool configs that the bundled browsers fetch.
# Primary host: gitlab.com/free9999/ipupdate
# Backup host:  www.67867867.xyz/Alvin9999/PAC/refs/heads/master
# Each entry: (tool_name, [node_indices], config_filename, format)
#   format: "clash-yaml" = ready to merge into Clash config
#           "json-hysteria2" / "json-juicity" / "json-naiveproxy" / "json-mieru"
#                              -> parsed and converted to Clash proxy entry
#           "json-singbox" / "json-xray" -> parse outbounds/outbounds[0] for proxy info
GITLAB_IPUPDATE_TOOLS = [
    # Clash/Mihomo YAML - directly mergeable
    {"tool": "quick",       "nodes": [1, 2, 3, 4],    "file": "config.yaml",  "format": "clash-yaml"},
    {"tool": "clash.meta2", "nodes": [1, 2, 3, 4, 5, 6], "file": "config.yaml", "format": "clash-yaml"},
    {"tool": "shadowquic",  "nodes": [1, 2],           "file": "client.yaml",  "format": "shadowquic-yaml"},
    # JSON-format tool configs - need conversion
    {"tool": "hysteria2",   "nodes": [1, 2, 3, 4],    "file": "config.json",  "format": "json-hysteria2"},
    {"tool": "hysteria",    "nodes": [1, 2, 3, 4],    "file": "config.json",  "format": "json-hysteria"},
    {"tool": "juicity",     "nodes": [1, 2],           "file": "config.json",  "format": "json-juicity"},
    {"tool": "naiveproxy",  "nodes": [1, 2],           "file": "config.json",  "format": "json-naiveproxy"},
    {"tool": "mieru",       "nodes": [1, 2],           "file": "config.json",  "format": "json-mieru"},
    {"tool": "singbox",     "nodes": [1, 2],           "file": "config.json",  "format": "json-singbox"},
    {"tool": "xray",        "nodes": [1, 2, 3, 4],    "file": "config.json",  "format": "json-xray"},
]

GITLAB_BASE = "https://gitlab.com/free9999/ipupdate/-/raw/master/backup/img/1/2/ip"
BACKUP_BASE = "https://www.67867867.xyz/Alvin9999/PAC/refs/heads/master/backup/img/1/2/ip"

OUTPUT_DIR = Path("subscribe")
BEIJING_TZ = timezone(timedelta(hours=8))

# ============================================================
# URI patterns (extracted from Wiki markdown)
# ============================================================

URI_PATTERNS = [
    r"vmess://[A-Za-z0-9+/=_-]+",
    r"vless://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    r"ssr://[A-Za-z0-9+/=_-]+",
    r"trojan://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    r"hysteria2?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    r"hy2://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    r"ss://[A-Za-z0-9+/=_\-]+@[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    r"ss://[A-Za-z0-9+/=_\-]+#[^\s|)\"'<>]+",
]


# ============================================================
# Helpers
# ============================================================


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (v2rayn-subscription-updater)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_with_fallback(primary: str, backup: str | None, timeout: int = 20) -> tuple[str, str]:
    """Try primary URL first; on failure try backup. Returns (content, source_label)."""
    try:
        return fetch(primary, timeout), "primary"
    except Exception as e:
        if backup:
            try:
                return fetch(backup, timeout), "backup"
            except Exception:
                pass
        raise


def extract_uris(markdown: str) -> list[str]:
    found = []
    seen = set()
    for pattern in URI_PATTERNS:
        for m in re.finditer(pattern, markdown):
            uri = m.group(0).rstrip(".,;)]")
            uri = uri.split()[0] if uri.split() else uri
            if len(uri) < 20:
                continue
            if uri.startswith("ss://") and "@" in uri:
                userinfo = uri[len("ss://"):].rsplit("@", 1)[0]
                if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", userinfo):
                    continue
            if uri not in seen:
                seen.add(uri)
                found.append(uri)
    return found


# ============================================================
# Convert GitLab tool configs → Clash proxy entries
# ============================================================


def make_proxy_name(tool: str, idx: int, server: str | None = None) -> str:
    suffix = f"-{server}" if server else ""
    return f"{tool}-{idx}{suffix}"[:60]


def parse_clash_yaml_proxies(yaml_text: str, tool: str, idx: int) -> list[dict]:
    """Extract proxy entries from a Clash YAML config.
    Naive parser - only handles the 'proxies:' block, list of dicts.
    Always overrides the proxy name to ensure uniqueness."""
    proxies = []
    # Find the proxies: block - everything from 'proxies:' to next top-level key
    m = re.search(r"^proxies:\s*\n((?:[ \t]+.*\n?)+)", yaml_text, re.MULTILINE)
    if not m:
        return proxies
    block = m.group(1)

    # Split by lines starting with "  - " (a new proxy entry)
    entries = re.split(r"\n[ \t]*-[ \t]+", "\n" + block)
    for entry in entries[1:]:  # skip first empty
        proxy = {}
        current_key = None
        for line in entry.splitlines():
            if not line.strip():
                continue
            # Match "key: value" at the same indent
            m_kv = re.match(r"[ \t]*(\S+):\s*(.*)$", line)
            if not m_kv:
                continue
            key, val = m_kv.group(1), m_kv.group(2).strip()
            # Skip noise fields we'll re-add canonically below
            if key == "request-timeout":
                continue
            if val:
                # Parse YAML inline list "[item1, item2]"
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    if inner:
                        items = []
                        for it in inner.split(","):
                            it = it.strip()
                            if (it.startswith('"') and it.endswith('"')) or (it.startswith("'") and it.endswith("'")):
                                it = it[1:-1]
                            if it:
                                items.append(it)
                        proxy[key] = items if len(items) > 1 else (items[0] if items else "")
                    else:
                        proxy[key] = []
                    current_key = key
                    continue
                # Strip quotes
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                # Convert common bool/int
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                elif val.lstrip("-").isdigit():
                    val = int(val)
                proxy[key] = val
                current_key = key
            else:
                current_key = key
        if proxy.get("type") and proxy.get("server"):
            # ALWAYS override name to ensure uniqueness across all sources
            proxy["name"] = make_proxy_name(tool, idx, proxy.get("server"))
            # Default alpn for TUIC/Hysteria if missing
            if proxy.get("type") in ("tuic", "hysteria", "hysteria2") and "alpn" not in proxy:
                proxy["alpn"] = ["h3"]
            proxies.append(proxy)
    return proxies


def convert_hysteria2_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Convert hysteria2 config.json to Clash proxy entry."""
    try:
        c = json.loads(json_text)
        server, port = c["server"].rsplit(":", 1)
        return {
            "name": make_proxy_name(tool, idx, server),
            "type": "hysteria2",
            "server": server,
            "port": int(port),
            "password": c.get("auth", ""),
            "sni": c.get("tls", {}).get("sni", server),
            "skip-cert-verify": c.get("tls", {}).get("insecure", False),
            "up": c.get("bandwidth", {}).get("up", "11 Mbps"),
            "down": c.get("bandwidth", {}).get("down", "55 Mbps"),
        }
    except Exception:
        return None


def convert_hysteria_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Convert hysteria v1 config.json to Clash proxy entry."""
    try:
        c = json.loads(json_text)
        server, port = c["server"].rsplit(":", 1)
        return {
            "name": make_proxy_name(tool, idx, server),
            "type": "hysteria",
            "server": server,
            "port": int(port),
            "auth-str": c.get("auth", c.get("password", "")),
            "sni": c.get("sni", server),
            "skip-cert-verify": c.get("insecure", False),
            "up": "11 Mbps",
            "down": "55 Mbps",
            "alpn": ["h3"],
            "protocol": "udp",
        }
    except Exception:
        return None


def convert_juicity_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Convert juicity config.json to Clash proxy entry."""
    try:
        c = json.loads(json_text)
        server, port = c["server"].rsplit(":", 1)
        return {
            "name": make_proxy_name(tool, idx, server),
            "type": "tuic",
            "server": server,
            "port": int(port),
            "uuid": c.get("uuid", ""),
            "password": c.get("password", ""),
            "sni": c.get("sni", server),
            "alpn": ["h3"],
            "congestion-controller": c.get("congestion_control", "bbr"),
            "udp-relay-mode": "native",
            "skip-cert-verify": c.get("allow_insecure", True),
        }
    except Exception:
        return None


def convert_naiveproxy_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Convert naiveproxy config.json to Clash proxy entry (as http proxy)."""
    try:
        c = json.loads(json_text)
        proxy_url = c["proxy"]  # https://user:pass@host:port
        m = re.match(r"https?://([^:]+):([^@]+)@([^:]+):(\d+)", proxy_url)
        if not m:
            return None
        user, pwd, host, port = m.groups()
        return {
            "name": make_proxy_name(tool, idx, host),
            "type": "http",
            "server": host,
            "port": int(port),
            "username": user,
            "password": pwd,
            "tls": True,
            "sni": host,
            "skip-cert-verify": True,
        }
    except Exception:
        return None


def convert_mieru_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Mieru has no Clash equivalent - return a comment-only entry."""
    try:
        c = json.loads(json_text)
        profile = c["profiles"][0]
        server = profile["servers"][0]
        ip = server["ipAddress"]
        port = server["portBindings"][0]["port"]
        return {
            "name": make_proxy_name(tool, idx, ip),
            "type": "socks5",  # placeholder - mieru isn't directly supported by Clash
            "server": "127.0.0.1",
            "port": 1080,
            "_note": f"mieru node {ip}:{port} - use mieru client directly",
        }
    except Exception:
        return None


def convert_singbox_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Extract first outbound from sing-box config.json → Clash proxy entry."""
    try:
        c = json.loads(json_text)
        ob = c["outbounds"][0]
        t = ob.get("type")
        if t == "tuic":
            return {
                "name": make_proxy_name(tool, idx, ob.get("server")),
                "type": "tuic",
                "server": ob["server"],
                "port": int(ob["server_port"]),
                "uuid": ob.get("uuid", ""),
                "password": ob.get("password", ""),
                "sni": ob.get("tls", {}).get("server_name", ob["server"]),
                "alpn": ob.get("tls", {}).get("alpn", ["h3"]),
                "congestion-controller": ob.get("congestion_control", "bbr"),
                "udp-relay-mode": "native",
                "skip-cert-verify": ob.get("tls", {}).get("insecure", True),
            }
        if t == "hysteria2":
            return {
                "name": make_proxy_name(tool, idx, ob.get("server")),
                "type": "hysteria2",
                "server": ob["server"],
                "port": int(ob["server_port"]),
                "password": ob.get("password", ""),
                "sni": ob.get("tls", {}).get("server_name", ob["server"]),
                "skip-cert-verify": ob.get("tls", {}).get("insecure", False),
                "up": "11 Mbps",
                "down": "55 Mbps",
            }
        if t == "vless":
            proxy = {
                "name": make_proxy_name(tool, idx, ob.get("server")),
                "type": "vless",
                "server": ob["server"],
                "port": int(ob["server_port"]),
                "uuid": ob.get("uuid", ""),
                "tls": bool(ob.get("tls", {}).get("enabled", False)),
                "network": "tcp",
            }
            if ob.get("flow"):
                proxy["flow"] = ob["flow"]
            if ob.get("tls", {}).get("server_name"):
                proxy["servername"] = ob["tls"]["server_name"]
            return proxy
        return None
    except Exception:
        return None


def convert_xray_json(json_text: str, tool: str, idx: int) -> dict | None:
    """Extract first outbound from xray config.json → Clash proxy entry."""
    try:
        c = json.loads(json_text)
        obs = c.get("outbounds", [])
        for ob in obs:
            proto = ob.get("protocol")
            if proto in ("vless", "vmess", "trojan", "shadowsocks"):
                server = ob["settings"]["vnext" if proto in ("vless","vmess") else "servers"][0]
                if proto == "vless":
                    proxy = {
                        "name": make_proxy_name(tool, idx, server["address"]),
                        "type": "vless",
                        "server": server["address"],
                        "port": int(server["port"]),
                        "uuid": server["users"][0]["id"],
                        "tls": bool(ob.get("streamSettings", {}).get("tlsSettings")),
                        "network": ob.get("streamSettings", {}).get("network", "tcp"),
                    }
                    flow = server["users"][0].get("flow")
                    if flow:
                        proxy["flow"] = flow
                    return proxy
                if proto == "vmess":
                    return {
                        "name": make_proxy_name(tool, idx, server["address"]),
                        "type": "vmess",
                        "server": server["address"],
                        "port": int(server["port"]),
                        "uuid": server["users"][0]["id"],
                        "alterId": int(server["users"][0].get("alterId", 0)),
                        "cipher": "auto",
                        "network": ob.get("streamSettings", {}).get("network", "tcp"),
                    }
                if proto == "trojan":
                    return {
                        "name": make_proxy_name(tool, idx, server["address"]),
                        "type": "trojan",
                        "server": server["address"],
                        "port": int(server["port"]),
                        "password": server["password"],
                        "sni": server["address"],
                    }
                if proto == "shadowsocks":
                    return {
                        "name": make_proxy_name(tool, idx, server["address"]),
                        "type": "ss",
                        "server": server["address"],
                        "port": int(server["port"]),
                        "cipher": server["method"],
                        "password": server["password"],
                    }
        return None
    except Exception:
        return None


def convert_shadowquic_yaml(yaml_text: str, tool: str, idx: int) -> dict | None:
    """shadowquic has no Clash equivalent - mark as unsupported."""
    try:
        m = re.search(r"addr:\s*\"([^\"]+)\"", yaml_text)
        if m:
            addr = m.group(1)
            return {
                "name": make_proxy_name(tool, idx, addr.split(":")[0]),
                "type": "socks5",
                "server": "127.0.0.1",
                "port": 4080,
                "_note": f"shadowquic node {addr} - use shadowquic client directly",
            }
    except Exception:
        pass
    return None


CONVERTERS = {
    "clash-yaml":      lambda txt, tool, idx: parse_clash_yaml_proxies(txt, tool, idx),
    "json-hysteria2":  lambda txt, tool, idx: [convert_hysteria2_json(txt, tool, idx)] if convert_hysteria2_json(txt, tool, idx) else [],
    "json-hysteria":   lambda txt, tool, idx: [convert_hysteria_json(txt, tool, idx)] if convert_hysteria_json(txt, tool, idx) else [],
    "json-juicity":    lambda txt, tool, idx: [convert_juicity_json(txt, tool, idx)] if convert_juicity_json(txt, tool, idx) else [],
    "json-naiveproxy": lambda txt, tool, idx: [convert_naiveproxy_json(txt, tool, idx)] if convert_naiveproxy_json(txt, tool, idx) else [],
    "json-mieru":      lambda txt, tool, idx: [convert_mieru_json(txt, tool, idx)] if convert_mieru_json(txt, tool, idx) else [],
    "json-singbox":    lambda txt, tool, idx: [convert_singbox_json(txt, tool, idx)] if convert_singbox_json(txt, tool, idx) else [],
    "json-xray":       lambda txt, tool, idx: [convert_xray_json(txt, tool, idx)] if convert_xray_json(txt, tool, idx) else [],
    "shadowquic-yaml": lambda txt, tool, idx: [convert_shadowquic_yaml(txt, tool, idx)] if convert_shadowquic_yaml(txt, tool, idx) else [],
}


# ============================================================
# Clash YAML emission
# ============================================================


def emit_clash_yaml(proxies: list[dict], source_info: list[dict]) -> str:
    """Emit a complete Clash/Mihomo YAML config from proxy list."""
    if not proxies:
        return "# No proxies parsed\n"

    lines = [
        "# Clash/Mihomo config generated by v2rayn-subscription-action",
        "# Generated at: " + datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        f"# Total proxies: {len(proxies)}",
        "",
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: info",
        "",
        "proxies:",
    ]
    for p in proxies:
        lines.append(f"  - name: {json.dumps(p.get('name',''), ensure_ascii=False)}")
        for k, v in p.items():
            if k == "name":
                continue
            if k.startswith("_"):
                continue  # skip metadata fields
            if isinstance(v, dict):
                lines.append(f"    {k}:")
                for dk, dv in v.items():
                    if isinstance(dv, str):
                        lines.append(f"      {dk}: {json.dumps(dv, ensure_ascii=False)}")
                    else:
                        lines.append(f"      {dk}: {dv}")
            elif isinstance(v, list):
                lines.append(f"    {k}:")
                for item in v:
                    if isinstance(item, str):
                        lines.append(f"      - {json.dumps(item, ensure_ascii=False)}")
                    else:
                        lines.append(f"      - {item}")
            elif isinstance(v, str):
                lines.append(f"    {k}: {json.dumps(v, ensure_ascii=False)}")
            elif isinstance(v, bool):
                lines.append(f"    {k}: {'true' if v else 'false'}")
            else:
                lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append("proxy-groups:")
    lines.append('  - name: "PROXY"')
    lines.append("    type: select")
    lines.append("    proxies:")
    for p in proxies:
        if not p.get("_note"):  # skip placeholder entries
            lines.append(f"      - {json.dumps(p['name'], ensure_ascii=False)}")
    lines.append('      - "DIRECT"')
    lines.append('  - name: "AUTO"')
    lines.append("    type: url-test")
    lines.append('    url: "https://www.gstatic.com/generate_204"')
    lines.append("    interval: 300")
    lines.append("    proxies:")
    for p in proxies:
        if not p.get("_note"):
            lines.append(f"      - {json.dumps(p['name'], ensure_ascii=False)}")
    lines.append("")
    lines.append("rules:")
    lines.append("  - MATCH,PROXY")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now_beijing = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S") + " (UTC+8)"

    all_uris: list[str] = []
    all_proxies: list[dict] = []
    source_info: list[dict] = []

    # ---- 1. Wiki markdown URI extraction ----
    for src in WIKI_SOURCES:
        try:
            md = fetch(src["url"])
            uris = extract_uris(md)
            print(f"[OK]   Wiki  {src['name']:25s}  URIs: {len(uris)}")
            all_uris.extend(uris)
            source_info.append({
                "type": "wiki", "source": src["name"], "url": src["url"],
                "uri_count": len(uris), "status": "ok"
            })
        except Exception as e:
            print(f"[FAIL] Wiki  {src['name']:25s}  {e}")
            source_info.append({
                "type": "wiki", "source": src["name"], "url": src["url"],
                "error": str(e), "status": "fail"
            })

    # ---- 2. GitLab ipupdate per-tool configs ----
    for tool_cfg in GITLAB_IPUPDATE_TOOLS:
        tool = tool_cfg["tool"]
        fmt = tool_cfg["format"]
        filename = tool_cfg["file"]
        converter = CONVERTERS[fmt]

        for n in tool_cfg["nodes"]:
            primary_url = f"{GITLAB_BASE}/{tool}/{n}/{filename}"
            backup_url = f"{BACKUP_BASE}/{tool}/{n}/{filename}"
            label = f"{tool}/{n}"
            try:
                content, used = fetch_with_fallback(primary_url, backup_url)
                proxies = converter(content, tool, n)
                # Skip placeholder entries from unsupported tools for cleaner output
                real_proxies = [p for p in proxies if not p.get("_note")]
                print(f"[OK]   GitLab  {label:25s}  proxies: {len(real_proxies)}  via {used}")
                all_proxies.extend(real_proxies)
                source_info.append({
                    "type": "gitlab-ipupdate", "source": label,
                    "url": primary_url, "backup_url": backup_url,
                    "proxy_count": len(real_proxies), "via": used, "status": "ok"
                })
            except Exception as e:
                print(f"[FAIL] GitLab  {label:25s}  {e}")
                source_info.append({
                    "type": "gitlab-ipupdate", "source": label,
                    "url": primary_url, "error": str(e), "status": "fail"
                })

    # ---- 3. Deduplicate URIs ----
    seen = set()
    unique_uris: list[str] = []
    for u in all_uris:
        if u not in seen:
            seen.add(u)
            unique_uris.append(u)

    # ---- 4. Write outputs ----
    plain = "\n".join(unique_uris)
    (OUTPUT_DIR / "v2rayn.txt").write_text(plain, encoding="utf-8")
    b64 = base64.b64encode(plain.encode("utf-8")).decode("ascii")
    (OUTPUT_DIR / "v2rayn_base64.txt").write_text(b64, encoding="utf-8")

    clash_yaml = emit_clash_yaml(all_proxies, source_info)
    (OUTPUT_DIR / "clash.yaml").write_text(clash_yaml, encoding="utf-8")

    meta = {
        "generated_at": now_beijing,
        "total_uris_from_wiki": len(unique_uris),
        "total_clash_proxies": len(all_proxies),
        "sources": source_info,
    }
    (OUTPUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("=== Summary ===")
    print(f"Wiki URIs (v2rayn.txt)     : {len(unique_uris)}")
    print(f"Clash proxies (clash.yaml) : {len(all_proxies)}")
    print(f"Generated at               : {now_beijing}")
    print(f"Output dir                 : {OUTPUT_DIR.resolve()}")
    print()
    print("Subscription URLs (after pushing to GitHub):")
    print(f"  v2rayN plain : https://raw.githubusercontent.com/<USER>/<REPO>/main/subscribe/v2rayn.txt")
    print(f"  v2rayN b64   : https://raw.githubusercontent.com/<USER>/<REPO>/main/subscribe/v2rayn_base64.txt")
    print(f"  Clash YAML   : https://raw.githubusercontent.com/<USER>/<REPO>/main/subscribe/clash.yaml")
    print(f"  jsDelivr CDN : https://cdn.jsdelivr.net/gh/<USER>/<REPO>@main/subscribe/clash.yaml")

    if len(unique_uris) == 0 and len(all_proxies) == 0:
        print("\n[WARN] No URIs or proxies extracted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
