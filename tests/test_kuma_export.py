import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kuma_export import export_db, is_secret_column, redact_url, to_csv, to_markdown  # noqa: E402

SECRET_VALUE = "super-secret-webhook-token-12345"
AUTH_PASS = "p@ssw0rd-leak-check"


def make_fixture_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE monitor (
            id INTEGER PRIMARY KEY, name TEXT, active INTEGER, user_id INTEGER,
            interval INTEGER, url TEXT, type TEXT, hostname TEXT, port INTEGER,
            basic_auth_pass TEXT, headers TEXT
        );
        CREATE TABLE "group" (id INTEGER PRIMARY KEY, name TEXT, active INTEGER);
        CREATE TABLE monitor_group (monitor_id INTEGER, group_id INTEGER);
        CREATE TABLE tag (id INTEGER PRIMARY KEY, name TEXT, color TEXT);
        CREATE TABLE monitor_tag (monitor_id INTEGER, tag_id INTEGER, value TEXT);
        CREATE TABLE notification (id INTEGER PRIMARY KEY, name TEXT, config TEXT, active INTEGER);
        CREATE TABLE monitor_notification (monitor_id INTEGER, notification_id INTEGER);
        """
    )
    cur.execute(
        "INSERT INTO monitor VALUES (1, 'Web', 1, 1, 60, 'https://user:%s@example.com/health', 'http', NULL, NULL, ?, ?)"
        % AUTH_PASS,
        (AUTH_PASS, '{"Authorization": "Bearer x"}'),
    )
    cur.execute(
        "INSERT INTO monitor VALUES (2, 'DBサーバー', 0, 1, 300, NULL, 'ping', 'db.internal', NULL, NULL, NULL)"
    )
    cur.execute("INSERT INTO \"group\" VALUES (1, '本番', 1)")
    cur.execute("INSERT INTO monitor_group VALUES (1, 1)")
    cur.execute("INSERT INTO tag VALUES (1, '重要', '#ff0000')")
    cur.execute("INSERT INTO monitor_tag VALUES (1, 1, '高')")
    cur.execute(
        "INSERT INTO notification VALUES (1, 'ntfy通知', ?, 1)",
        (json.dumps({"ntfyTopic": SECRET_VALUE}),),
    )
    cur.execute("INSERT INTO monitor_notification VALUES (1, 1)")
    con.commit()
    con.close()
    return path


def test_is_secret_column():
    assert is_secret_column("basic_auth_pass")
    assert is_secret_column("notification_config")
    assert not is_secret_column("url")
    assert not is_secret_column("name")


def test_redact_url():
    assert redact_url("https://user:pass@example.com/x") == "https://***@example.com/x"
    assert redact_url("https://example.com/x") == "https://example.com/x"
    assert redact_url(None) is None


def test_export_structure_and_redaction(tmp_path):
    db = make_fixture_db(tmp_path / "kuma.db")
    data, excluded = export_db(db)
    assert len(data["monitors"]) == 2
    m1 = data["monitors"][0]
    assert m1["name"] == "Web"
    assert m1["groups"] == ["本番"]
    assert m1["tags"] == ["重要=高"]
    assert m1["notifications"] == ["ntfy通知"]
    # 秘密がどこにも出ていないこと
    blob = json.dumps(data, ensure_ascii=False)
    assert SECRET_VALUE not in blob
    assert AUTH_PASS not in blob
    assert "basic_auth_pass" in " ".join(excluded)
    assert "notification.config" in excluded
    # URL のユーザー情報は伏せ字
    assert m1["url"] == "https://***@example.com/health"


def test_csv_and_markdown(tmp_path):
    db = make_fixture_db(tmp_path / "kuma.db")
    data, _ = export_db(db)
    csv_text = to_csv(data)
    assert "Web" in csv_text and "本番" in csv_text
    assert SECRET_VALUE not in csv_text
    md = to_markdown(data)
    assert "モニター数: 2" in md
    assert "DBサーバー" in md
    assert SECRET_VALUE not in md


def test_readonly_does_not_modify(tmp_path):
    db = make_fixture_db(tmp_path / "kuma.db")
    before = db.read_bytes()
    export_db(db)
    assert db.read_bytes() == before
