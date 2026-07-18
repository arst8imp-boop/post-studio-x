# Post Studio X（X専用・AI下書きツール）

X（旧Twitter）の投稿を、アカウント（ブランド）ごとの語り口で下書きするローカルツールです。
長文／短文／引用／リプ／会話リプ／X記事を、過去投稿を参考（RAG）にしながら並列で生成できます。

📖 **使い方ガイド（スライド形式・全11ページ）: [`docs/post_studio_x_guide.pdf`](docs/post_studio_x_guide.pdf)**

---

## セットアップ

### 0. 必要なもの（自分で用意）
- macOS（以下は Mac 前提）
- **Claude Code**（生成の認証に使用）… `https://claude.com/claude-code` からインストールしてログイン
- **Python 3.12 以上**（3.9 だと動きません）… `brew install python@3.12`
- **Neon**（無料のクラウド Postgres）の接続文字列 … `https://neon.tech` で無料登録し、Connection string をコピー
- （任意）**Anthropic / Gemini API キー** … サブスク上限時の予備・カバー画像生成用

> API キーや DB の URL は **各自で取得して `.env` に入れます**（共有はしません）。

### 1. コードを入手
```bash
git clone https://github.com/arst8imp-boop/post-studio-x.git
cd post-studio-x
```
（Git を使わない場合は GitHub の「Code → Download ZIP」でもOK）

### 2. 仮想環境と依存
```bash
python3.12 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

### 3. `.env` を作って自分の値を入れる
```bash
cp .env.example .env
# .env を開いて DATABASE_URL（Neon）を貼る。API キーは任意。
```

### 4. DB のテーブルを作る
```bash
.venv/bin/python3 setup_db.py
```

### 5. 起動
```bash
./start.sh          # http://localhost:7879 が自動で開きます
```

> **Claude Code に任せてもOK：** clone したフォルダを開いて「このツールをセットアップして起動して。Neon の URL と API キーは用意してある」と頼めば、2〜5 を代わりにやってくれます。

---

## 使い方（かんたん）
- 画面は「ペイン」の集まり。各ペインで **アカウント** と **投稿タイプ** を選び、テーマ欄に指示を書いて **生成**（Cmd+Enter でも可）。
- **RAG** … オンにすると、そのアカウントの過去投稿を参考にして語り口を寄せます。
- **まとめて生成** … テーマが入っている全ペインを一括生成。
- **バズ** … 伸びているポストから引用RT／リプの素材を拾う。
- **ネタ出し** … 過去履歴を踏まえて、まだ書いていない投稿ネタを提案。
- **履歴** … 生成物の一覧・復元。
- **カバー** … X記事タイプのとき、本文から3行を抽出してカバー画像を生成（Gemini キーが必要）。
- **アカウント** … Xアカウント（ブランド）の新規作成・名前変更・削除・組み込みの非表示。
- **アカウントを作り込む** … 作成したアカウントの「作り込む」から、声（一人称・トーン・実績数字・NG・絵文字）を設定＋自分の過去ポストを学習（RAG）させて、"自分らしい"生成に寄せられます（過去ポスト学習は埋め込み用のGeminiキーが必要）。設定はこのPCのDBだけに保存され、配布はされません。

## コストについて（重要）
- 生成は **Claude Code のサブスク認証を優先**（＝追加課金なし）。
- サブスクが上限のときだけ API 課金に切り替わりますが、`API_MONTHLY_BUDGET_JPY=0` の間は **課金をブロック**して生成を止めます。
- 画面右上の「今月のAPI課金」→ **ブロック解除トグル**で、必要なときだけ手動で API 課金を許可できます（既定はブロック）。

## 困ったら
- **ポートが使用中** … 別ポートで起動 `POST_STUDIO_PORT=7880 ./start.sh`
- **`TypeError` 等で起動しない** … Python が 3.12 か確認（`.venv/bin/python3 -V`）。3.9 なら作り直す。
- **履歴やアカウントが保存されない** … `.env` の `DATABASE_URL`（Neon）を確認。
- **カバー画像で 429** … Gemini の無料枠切れ。時間を置くか `.env` の `GEMINI_IMAGE_MODEL` で別モデル指定。

詳しい使い方は `docs/post_studio_x_guide.pdf`（スライド形式）を参照。
