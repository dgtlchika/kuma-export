#!/usr/bin/env python3
"""kuma-export: Uptime Kuma の設定を読み取り専用でエクスポートする CLI。

Uptime Kuma の内蔵バックアップは公式に「完全なバックアップを生成・復元できない」
とされており（本家 Issue #2402）、設定の一覧を人間可読な形で取り出す手段が弱い。
本ツールは kuma.db（SQLite）を **読み取り専用** で開き、モニター／グループ／
タグ／通知の対応を JSON・CSV・Markdown 一覧として書き出す。依存は標準ライブラリのみ。

安全設計:
- DB は SQLite の read-only モードで開く（書き込みは一切しない）
- 通知の config（Webhook URL やトークンを含む）は出力しない。名前だけを出す
- パスワード・トークン・認証ヘッダー等を含む列は列名で判定して除外する
- URL に埋め込まれたユーザー情報（user:pass@）は伏せ字にする

使い方:
    python3 kuma_export.py /path/to/kuma.db --out-dir ./kuma-export
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# 値に秘密が含まれうる列名のパターン（部分一致・大文字小文字を区別しない）
_SECRET_COLUMN_PATTERNS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth",
    "header",
    "cookie",
    "credential",
    "body",  # HTTP リクエストボディ（認証情報を含むことがある）
    "config",  # 通知などの設定 JSON 全体
)


def is_secret_column(column: str) -> bool:
    low = column.lower()
    return any(pat in low for pat in _SECRET_COLUMN_PATTERNS)


def redact_url(url: str | None) -> str | None:
    """URL のユーザー情報を伏せる。URL でなければそのまま返す。"""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    return url


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def _columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info('{table}')")
    return [row[1] for row in cur.fetchall()]


def _fetch_dicts(cur: sqlite3.Cursor, table: str, columns: list[str]) -> list[dict]:
    cols = ", ".join(f'"{c}"' for c in columns)
    cur.execute(f'SELECT {cols} FROM "{table}"')
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def export_db(db_path: Path) -> tuple[dict, list[str]]:
    """kuma.db を読み、エクスポート用 dict と除外列リストを返す。"""
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        if not _table_exists(cur, "monitor"):
            raise SystemExit("monitor テーブルが見つかりません。Uptime Kuma の DB ではない可能性があります。")

        excluded: list[str] = []
        monitor_cols_all = _columns(cur, "monitor")
        monitor_cols = []
        for c in monitor_cols_all:
            if is_secret_column(c):
                excluded.append(f"monitor.{c}")
            else:
                monitor_cols.append(c)
        monitors = _fetch_dicts(cur, "monitor", monitor_cols)
        for m in monitors:
            if m.get("url"):
                m["url"] = redact_url(str(m["url"]))

        groups: list[dict] = []
        group_name: dict[int, str] = {}
        if _table_exists(cur, "group"):
            groups = _fetch_dicts(cur, "group", _columns(cur, "group"))
            group_name = {g["id"]: g.get("name", "") for g in groups}

        tags: list[dict] = []
        tag_name: dict[int, str] = {}
        if _table_exists(cur, "tag"):
            tags = _fetch_dicts(cur, "tag", _columns(cur, "tag"))
            tag_name = {t["id"]: t.get("name", "") for t in tags}

        notifications: list[dict] = []
        notif_name: dict[int, str] = {}
        if _table_exists(cur, "notification"):
            # config 列は秘密を含むため名前と有効状態だけを出す
            ncols = [c for c in _columns(cur, "notification") if not is_secret_column(c)]
            if "config" in _columns(cur, "notification"):
                excluded.append("notification.config")
            notifications = _fetch_dicts(cur, "notification", ncols)
            notif_name = {n["id"]: n.get("name", "") for n in notifications}

        # 対応関係
        monitor_groups: dict[int, list[str]] = {}
        if _table_exists(cur, "monitor_group"):
            for row in _fetch_dicts(cur, "monitor_group", _columns(cur, "monitor_group")):
                monitor_groups.setdefault(row["monitor_id"], []).append(
                    group_name.get(row["group_id"], str(row["group_id"]))
                )
        monitor_tags: dict[int, list[str]] = {}
        if _table_exists(cur, "monitor_tag"):
            for row in _fetch_dicts(cur, "monitor_tag", _columns(cur, "monitor_tag")):
                label = tag_name.get(row["tag_id"], str(row["tag_id"]))
                if row.get("value"):
                    label = f"{label}={row['value']}"
                monitor_tags.setdefault(row["monitor_id"], []).append(label)
        monitor_notifs: dict[int, list[str]] = {}
        if _table_exists(cur, "monitor_notification"):
            for row in _fetch_dicts(cur, "monitor_notification", _columns(cur, "monitor_notification")):
                monitor_notifs.setdefault(row["monitor_id"], []).append(
                    notif_name.get(row["notification_id"], str(row["notification_id"]))
                )

        for m in monitors:
            m["groups"] = monitor_groups.get(m["id"], [])
            m["tags"] = monitor_tags.get(m["id"], [])
            m["notifications"] = monitor_notifs.get(m["id"], [])

        data = {
            "monitors": monitors,
            "groups": groups,
            "tags": tags,
            "notifications": notifications,
        }
        return data, sorted(set(excluded))
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 出力フォーマット
# ---------------------------------------------------------------------------

_CSV_COLUMNS = [
    "id", "name", "type", "url", "hostname", "port",
    "interval", "active", "groups", "tags", "notifications",
]


def to_csv(data: dict) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for m in data["monitors"]:
        row = dict(m)
        for key in ("groups", "tags", "notifications"):
            row[key] = "; ".join(str(x) for x in m.get(key, []))
        writer.writerow(row)
    return buf.getvalue()


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(data: dict) -> str:
    monitors = data["monitors"]
    lines: list[str] = []
    lines.append("# Uptime Kuma モニター一覧")
    lines.append("")
    lines.append(f"モニター数: {len(monitors)} / グループ数: {len(data['groups'])} / 通知数: {len(data['notifications'])}")
    lines.append("")
    lines.append("| ID | 名前 | 種別 | URL / ホスト | 間隔(秒) | 状態 | グループ | タグ |")
    lines.append("| ---: | --- | --- | --- | ---: | --- | --- | --- |")
    for m in monitors:
        target = m.get("url") or ""
        if not target and m.get("hostname"):
            target = str(m["hostname"]) + (f":{m['port']}" if m.get("port") else "")
        status = "有効" if m.get("active") else "停止"
        lines.append(
            "| {id} | {name} | {type} | {target} | {interval} | {status} | {groups} | {tags} |".format(
                id=m.get("id", ""),
                name=_md_escape(str(m.get("name", ""))),
                type=_md_escape(str(m.get("type", ""))),
                target=_md_escape(str(target)),
                interval=m.get("interval", ""),
                status=status,
                groups=_md_escape(", ".join(str(x) for x in m.get("groups", []))),
                tags=_md_escape(", ".join(str(x) for x in m.get("tags", []))),
            )
        )
    lines.append("")
    if data["notifications"]:
        lines.append("## 通知（名前のみ・設定値は出力しません）")
        lines.append("")
        for n in data["notifications"]:
            lines.append(f"- {n.get('name', '')}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kuma-export",
        description="Uptime Kuma の設定を読み取り専用で JSON/CSV/Markdown へエクスポートする",
    )
    parser.add_argument("db", help="kuma.db のパス")
    parser.add_argument("--out-dir", "-o", default="./kuma-export", help="出力ディレクトリ（既定: ./kuma-export）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB ファイルがありません: {db_path}", file=sys.stderr)
        return 1

    data, excluded = export_db(db_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "monitors.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "monitors.csv").write_text(to_csv(data), encoding="utf-8")
    (out_dir / "inventory.md").write_text(to_markdown(data), encoding="utf-8")

    print(f"モニター {len(data['monitors'])}件 / グループ {len(data['groups'])}件 / 通知 {len(data['notifications'])}件")
    print(f"書き出しました: {out_dir}/monitors.json, monitors.csv, inventory.md")
    if excluded:
        print("秘密を含むため除外した列: " + ", ".join(excluded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
