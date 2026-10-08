# imiwatch — 画面の一部を、あなたの言葉で見張る

画面の好きな範囲をドラッグで選び、「何が起きたら知りたいか」を自然な文章で書くだけで、
その範囲を定期的に撮影して **判定モデル（decision model）** に判定させ、
時系列グラフに記録し、条件を満たしたら通知する Windows 用の試作ツールです。

```
範囲をドラッグ ─▶ 一言で依頼 ─▶ LLM が観点に分解（設計図） ─▶ 判定モデルが定期的に判定 ─▶ グラフ・通知
```

- **見たいもの・条件はユーザーが自由に決める**：在庫表示、チャット欄、グラフ、ダッシュボードなど
- **「変化」ではなく「意味」で知らせる**：広告や時刻の更新では鳴らず、書いた条件が成り立ったときだけ通知
- **測ってグラフにする**：問いごとの値と、それをまとめた指標（0〜1）を時系列で記録
- **判定モデルを切り替えられる**：見張りごとに、いつでも変更可能

> ⚠️ 試作品です。見逃しや誤報は必ず起きます。防犯・安全・お金に関わる用途には使わないでください。

## 必要なもの

- Windows 10 / 11
- Python 3.10 以上（[python.org](https://www.python.org/downloads/windows/) の公式インストーラ推奨。tkinter が含まれます）
- 判定モデルの API キー（下の表のどれか1つ）。試すだけなら「モック」でキーなしでも動きます
- （推奨）[OpenRouter](https://openrouter.ai/) の API キー：依頼を観点に分解する「設計役」の LLM に使います。
  ない場合は、依頼文をそのまま1つの問いにする簡易設計になります

## 起動

```bat
git clone https://github.com/ShunsukeTamura06/imiwatch.git
cd imiwatch
run.bat
```

`run.bat` は初回に `.venv` を作って依存パッケージを入れ、アプリを起動します。
エラーを確認したいときは `run_console.bat`（コンソール付き）を使ってください。

手動で起動する場合:

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m imiwatch
```

## 使い方

1. 右上の **設定** で API キーと既定の判定モデルを入れる
2. **＋ 新しい見張り** を押す
   1. 「画面上でドラッグして選ぶ」で範囲を選択（Esc で中止）
   2. 知りたいことを書く（例:「この在庫表示が『在庫あり』に変わったら教えて」）
   3. **判定を設計する** → AI が 3〜6 個の観点（問い）と重み・通知条件・推奨間隔を提案
      - 右側の JSON を直接書き換えて **JSON の編集を反映** で調整できます
   4. **今の画面で試し判定** で、いまの値を確認
   5. 名前と取得間隔を決めて **保存して開始**
3. メイン画面で、グラフ・最新画像・観点ごとの値・ログを確認
   - 判定モデル・取得間隔・通知の有無は、見張りごとにその場で変更できます

## 判定モデルの切り替え

| 選択肢 | 画像 | 必要な設定 | メモ |
|---|---|---|---|
| Cloudflare **Clef-flash** | ○ | アカウントID・APIトークン | 速い。最初に試すならこれ |
| Cloudflare **Clef** | ○ | 同上 | 27B。精度重視 |
| **Perplexity** Decisions API | ○ | APIキー | `pplx-decider-v1.1-27b` など |
| **jev-local（imajev-4b）** | ○ | サーバーURL（・APIキー） | オープンウェイトを自前の GPU サーバーで動かす。画像は外部 API に送られない。下記参照 |
| **SystemOne 互換**（TypeSafe Jev など） | △ | URL・APIキー・モデル | Jev は画像を読めません。画像対応のエンドポイントなら「画像の送り方」を clef / perplexity に |
| **LLM で代用**（OpenRouter） | ○ | OpenRouter キー | 判定モデルがなくても試せる代用品。遅く、確率は較正されていません |
| **モック** | ○ | なし | API を呼ばない動作確認用 |

- Cloudflare の API トークンは「Workers AI」の権限で作成します
- キーは環境変数でも渡せます：`CLOUDFLARE_ACCOUNT_ID` `CLOUDFLARE_API_TOKEN` `PERPLEXITY_API_KEY` `TYPESAFE_API_KEY` `OPENROUTER_API_KEY` `KEV_API_KEY`（jev-local）

### jev-local（オープンウェイトの imajev-4b）を使う

[jev-local](https://github.com/ShunsukeTamura06/jev-local) の画像対応版（`feat/imajev-vision`）を EC2 などの GPU サーバーで起動し、
その `POST /v1/decisions` に接続します。

1. GPU サーバー側（jev-local の README どおり）

   ```bash
   git clone -b feat/imajev-vision https://github.com/ShunsukeTamura06/jev-local.git
   cd jev-local
   python3.12 -m venv .venv && .venv/bin/python -m pip install --upgrade pip
   ./scripts/install_vision_model.sh
   ./scripts/start.sh            # 127.0.0.1:8008 で待ち受け
   ```

2. Windows 側で SSH トンネルを張る（サーバーを外部に公開しないため）

   ```bat
   ssh -N -L 8008:127.0.0.1:8008 ec2-user@<EC2のアドレス>
   ```

3. imiwatch の設定で「jev-local」のサーバーURLを `http://127.0.0.1:8008`（既定値）にする。
   サーバーで `KEV_API_KEY` を設定した場合は、同じ値を APIキー欄に入れる

imiwatch 側の対応:

| imiwatch の問い | imajev のフィールド | 備考 |
|---|---|---|
| noul | boolean | `criteria.true/false` → `yes_description` / `no_description` |
| choice | choice | 選択肢 → `options` |
| score | ordinal | 段階 → `levels`（0, 1, 2 …）。グラフ用に段階の期待値を使う |

- imajev は **判断保留（abstained）** を返すことがあります。保留した問いは指標の計算から外し、ログに `判断保留: <問い>` と残します
- 「不明」の確率は「はい」に混ぜません（jev-local の推奨どおり、再正規化しない）
- 1リクエスト8項目までなので、それを超える問いは分割して送ります
- imajev は英語中心で学習されているため、判定モデルに jev-local を選んで設計すると、**問いは英語**で作られます（名前や通知文は日本語のまま）
- 旧版（main ブランチの Kev-4B）は画像を読めません。画像の判定には imajev 版を使ってください

新しい判定モデルを足すには、`imiwatch/judges/` に `Judge` を継承したクラスを作り、
`imiwatch/judges/__init__.py` の `PROVIDERS` に登録します。

## 仕組み

| 部品 | ファイル | 役割 |
|---|---|---|
| 範囲選択 | `region.py` | 全画面の半透明の幕にドラッグで範囲を描く（物理ピクセル） |
| 撮影 | `capture.py` | `mss` で範囲を撮影、長辺 768px に縮小、差分用の指紋を作る |
| 設計役 | `planner.py` | 依頼文（＋今の画面）から、問い・重み・通知条件の設計図を作る |
| 設計図 | `plan.py` | 検証、0〜1 への正規化、指標の計算、通知ルール（連続回数・クールダウン・再武装） |
| 判定 | `judges/` | 各社 API の差を吸収して SystemOne 形式の答えを返す |
| 実行 | `engine.py` | 撮影 → 変化がなければ判定を省略 → 判定 → 記録 → 通知判定 |
| 画面 | `app.py` | tkinter + matplotlib |

**指標の計算**：noul は「はい」の確率、score は段階 ÷（段階数 − 1）、choice は指定した選択肢の確率を 0〜1 の値とし、
設計図の重みで加重平均したものを指標とします。

**コストを抑える工夫**：前回の撮影と画素がほとんど変わっていなければ（平均差 < 2.0）、API を呼ばずに前回の値を使い回します（ログでは `reused`）。

## データの保存先

`%APPDATA%\imiwatch\`

- `settings.json`：設定（**API キーは暗号化せずに保存**されます）
- `monitors.json`：見張りの定義
- `monitors\<id>\log.csv`：判定ログ（時刻・状態・指標・観点ごとの値・モデル・所要時間・通知）
- `monitors\<id>\latest.jpg`、`notified_*.jpg`：最新の画像と、通知したときの画像

## 制約と注意

- **画面に見えている範囲しか撮れません**。ウィンドウが隠れる・最小化・スリープ・画面ロック中は正しく判定できません
- 範囲は画面上の座標で記録するため、ウィンドウを動かすと見張る対象がずれます
- **選んだ範囲の画像は、選んだ判定モデルの提供元（と、設計時は OpenRouter）に送信されます**。
  jev-local を使えば、判定の画像は自分のサーバーの外に出ません（設計時の OpenRouter は別。キーを入れなければ簡易設計になります）。
  個人情報や業務上の機密が映る範囲は選ばないでください
- 判定モデルは数を数える・計算する・細かい文字を読むのが苦手です。設計役の LLM はそうした依頼を見た目で判断できる問いに言い換えます
- 通知は Windows のトースト通知（`winotify`）を使い、使えない場合は画面右下に簡易表示します

## 開発

```bash
pip install -r requirements-dev.txt
python -m pytest
```

テストは API をモックし、画面撮影なしで、設計図・判定・指標・通知・保存の流れを確認します。

## ライセンス

MIT
