# -*- coding: utf-8 -*-
"""Build SSH share URIs in the format the client app exports:
ssh://BASE64(user:pass)@host:port#remark
"""
import base64
import urllib.parse


def build_ssh_uri(username: str, password: str, host: str, port: int, remark: str) -> str:
    username = str(username or "").strip()
    password = str(password or "")
    host = str(host or "").strip()
    if not username or not password or not host:
        raise ValueError("SSH URI requires username, password, and host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    userinfo = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    label = urllib.parse.quote(str(remark or "SSH"), safe="")
    return f"ssh://{userinfo}@{host}:{int(port)}#{label}"
