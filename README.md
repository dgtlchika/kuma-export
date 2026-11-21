# kuma-export

Uptime Kuma の設定を **読み取り専用** でエクスポートする CLI です。
依存は **Python 標準ライブラリのみ**。

Uptime Kuma の内蔵バックアップ機能は、本家でも「多くの機能が追加され、
完全なバックアップを生成・復元できない」と案内されており
（[Issue #2402](https://github.com/louislam/uptime-kuma/issues/2402)）、
設定の一覧を人間可読な形で取り出す要望が繰り返し上がっています
（[#6045](https://github.com/louislam/uptime-kuma/issues/6045) /
[#3581](https://github.com/louislam/uptime-kuma/issues/3581) /
[#855](https://github.com/louislam/uptime-kuma/issues/855)）。

本ツールは kuma.db（SQLite）を直接読んで、モニター構成の棚卸し・移行前の
現状把握・ドキュメント化に使える形で書き出します。**インポート（復元）は
行いません**（読み取り専用に絞ることで、監視を止めず・壊さず使えます）。

## できること

- **3 形式で出力**: `monitors.json`（全構造）／`monitors.csv`（表計算用）／`inventory.md`（人間可読な一覧）
- **対応関係を解決**: モニターごとのグループ・タグ・通知を ID ではなく名前で付与
- **読み取り専用**: SQLite を `mode=ro` で開くため、DB を一切変更しない

## 安全設計（重要）

監視設定には秘密が多く含まれます。本ツールは次を徹底します。

- 通知の `config`（Webhook URL・トークン等）は **出力しません**（通知は名前だけ）
- パスワード／トークン／認証／ヘッダー／ボディ等、秘密を含みうる列は列名で判定して除外し、除外した列名を終了時に表示します
- URL に埋め込まれたユーザー情報（`https://user:pass@host/...`）は `***` に伏せます

それでも、出力ファイルには監視対象の URL やホスト名が含まれます。
公開リポジトリ等へそのまま置かないでください。

## 動作環境

- Python 3.10 以上（標準ライブラリのみ）
- Uptime Kuma 1.23 以降の kuma.db（2.x 系の追加列にも列単位で追随します）

## インストール

```bash
pip install git+https://github.com/dgtlchika/kuma-export
```

clone して直接実行もできます（依存ゼロ）。

```bash
git clone https://github.com/dgtlchika/kuma-export
cd kuma-export
python3 kuma_export.py /path/to/kuma.db
```

## 使い方

kuma.db は通常、Docker ボリューム内の `kuma.db`、またはデータディレクトリ直下にあります。

```bash
kuma-export /var/lib/docker/volumes/uptime-kuma/_data/kuma.db --out-dir ./kuma-export
```

実行例:

```
モニター 17件 / グループ 2件 / 通知 1件
書き出しました: ./kuma-export/monitors.json, monitors.csv, inventory.md
秘密を含むため除外した列: monitor.basic_auth_pass, notification.config
```

稼働中の Kuma に対しても読み取り専用で開けますが、整合したスナップショットが
必要な場合は DB ファイルのコピーに対して実行してください。

## 制限

- インポート／復元機能はありません（意図的な仕様です）
- ステータスページの設定、心拍（監視履歴）データは対象外です
- 稼働中 DB を直接読む場合、書き込み途中の行が混ざる可能性があります

## テスト

```bash
python3 -m pytest tests/
```

## ライセンス

MIT
