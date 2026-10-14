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
- **3つの型で判定を設計できる**：はい／いいえ・選択肢・段階評価を、それぞれ専用の欄で編集。AI に型を指定して観点を提案させることもできます
- **グラフで振り返る**：ドラッグで拡大、期間の切り替え、点を選ぶとその時の画像、グラフだけを大きく表示

> ⚠️ 試作品です。見逃しや誤報は必ず起きます。防犯・安全・お金に関わる用途には使わないでください。

## 必要なもの

- Windows 10 / 11
- Python 3.10〜3.13（[python.org](https://www.python.org/downloads/windows/) の公式インストーラ推奨。範囲選択に使う tkinter が含まれます）
- Microsoft Edge WebView2 ランタイム（Windows 10 / 11 には通常入っています。画面の表示に使います）
- 判定モデルの API キー（下の表のどれか1つ）。試すだけなら「モック」でキーなしでも動きます
- （推奨）設計役の LLM：知りたいことを観点に分けるのに使います。**OpenAI 互換（/chat/completions）か Anthropic 形式（/v1/messages）**の API なら使えます
  （OpenRouter、OpenAI、Anthropic、社内の LLM ゲートウェイ、LM Studio、Ollama、vLLM など）。ない場合は、依頼文をそのまま1つの問いにする簡易設計になります

## 起動

```bat
git clone https://github.com/ShunsukeTamura06/imiwatch.git
cd imiwatch
run.bat
```

`run.bat` は初回に `.venv` を作って依存パッケージを入れ、アプリを起動します（`requirements.txt` が更新されたときも自動で入れ直します）。

- `run_console.bat`：コンソール付きで起動（エラーを確認したいとき）
- `run_browser.bat`：専用ウィンドウではなく、いつものブラウザで開く

専用ウィンドウ（pywebview）を開けない環境では、自動でブラウザ表示に切り替わります。
ブラウザ表示のときは、設定の「このアプリ」から終了できます。

手動で起動する場合:

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m imiwatch
```

## 使い方

1. 右上の **設定** で、判定モデルと設計役の LLM を設定する（「接続テスト」で確認できます）
2. **新しい見張り** を押す
   1. **範囲を選ぶ** → 画面の上でドラッグ（Esc で中止）
   2. 知りたいことを書く（例:「在庫表示が『在庫あり』に変わったら教えて」）
   3. **AI で観点に分ける** → AI が観点（問い）・重み・通知条件・取得間隔を提案
   4. 観点を整える（下の「判定の設計」）
   5. **今の画面で試す** → 指標と観点ごとの値を確認
   6. **保存して開始**
3. 一覧で見張りを選ぶと、グラフ・その時の画像・観点ごとの値・記録が見られます
   - **判定を編集** から、範囲・観点・通知・判定モデル・取得間隔をあとから変えられます

### 判定の設計（3つの型）

観点ごとに型を選び、型に合った欄に書きます。どの型も 0〜1 の値になり、重みを付けて平均したものが指標になります（重み 0 は記録だけ）。

| 型 | 書くもの | 値 |
|---|---|---|
| **はい／いいえ**（noul） | 問いと、「はい」「いいえ」に当たる見た目（任意） | 「はい」の確率 |
| **選択肢**（choice） | 問いと、選択肢（ID と説明）。値にしたい選択肢に丸を付ける | 丸を付けた選択肢の確率（丸がなければ記録だけ） |
| **段階評価**（score） | 問いと、低い順の段階（2〜10）。程度ではなく、目に見える状況で書く | 段階の期待値 ÷（段階数 − 1） |

- 「観点を追加」で型を選び、**自分で書く**か **AI に提案させる**（今ある観点と違う観点を、その型で1つ作ります）
- 型はカードの上で切り替えられます。JSON で直接編集することもできます

### グラフ

- **ドラッグ**で範囲を選んで拡大、**ホイール**で拡大縮小、**Shift+ホイール**で左右に移動、**ダブルクリック**で元に戻す
- 上のボタンで期間を切り替え（1時間・今日・7日・すべて）
- 点に**カーソルを合わせる**と、その時の値と画像。**クリック**すると右側に固定表示
- ⤢ でグラフだけを大きく表示（Esc で戻る）
- 橙の点は通知したとき、下の赤い印は判定できなかったとき

## 判定モデルの切り替え

| 選択肢 | 画像 | 必要な設定 | メモ |
|---|---|---|---|
| Cloudflare **Clef-flash** | ○ | アカウントID・APIトークン | 速い。最初に試すならこれ |
| Cloudflare **Clef** | ○ | 同上 | 27B。精度重視 |
| **Perplexity** Decisions API | ○ | APIキー | `pplx-decider-v1.1-27b` など |
| **jev-local（imajev-4b）** | ○ | サーバーURL（・APIキー） | オープンウェイトを自前の GPU サーバーで動かす。画像は外部 API に送られない。下記参照 |
| **SystemOne 互換**（TypeSafe Jev など） | △ | URL・APIキー・モデル | Jev は画像を読めません。画像対応のエンドポイントなら「画像の送り方」を clef / perplexity に |
| **LLM で代用**（OpenAI 互換） | ○ | 設計役の LLM の設定 | 判定モデルがなくても試せる代用品。画像を読めるモデルが必要。遅く、確率は較正されていません |
| **モック** | ○ | なし | API を呼ばない動作確認用 |

- Cloudflare の API トークンは「Workers AI」の権限で作成します
- キーは環境変数でも渡せます：`CLOUDFLARE_ACCOUNT_ID` `CLOUDFLARE_API_TOKEN` `PERPLEXITY_API_KEY` `TYPESAFE_API_KEY` `KEV_API_KEY`（jev-local）、設計役の LLM は `LLM_API_KEY`（なければ `OPENROUTER_API_KEY`）

### 設計役の LLM（OpenAI 互換 / Anthropic 形式）

設定の「設計役の LLM」で、接続先を選ぶか、「その他」を選んで形式と Base URL を入力します。設定画面に実際の送り先が表示されます。

| 接続先 | 形式 | Base URL | 送り先 | APIキー |
|---|---|---|---|---|
| OpenRouter | OpenAI 互換 | `https://openrouter.ai/api/v1` | `…/chat/completions` | 必要 |
| OpenAI | OpenAI 互換 | `https://api.openai.com/v1` | `…/chat/completions` | 必要 |
| Anthropic | Anthropic 形式 | `https://api.anthropic.com` | `…/v1/messages` | 必要 |
| 社内の LLM ゲートウェイなど | どちらか | ゲートウェイの URL | 形式に合わせて自動 | ゲートウェイによる |
| LM Studio / Ollama（このPC） | OpenAI 互換 | `http://localhost:1234/v1` / `http://localhost:11434/v1` | `…/chat/completions` | 不要 |

- Anthropic 形式では `x-api-key` と `anthropic-version: 2023-06-01` を付けて送ります。ゲートウェイが `Authorization: Bearer` を求める場合は「キーの送り方」で変えられます
- Base URL は `…/v1` まで書いても、`…/v1/messages` まで書いても構いません
- JSON モード（`response_format`）に対応していない OpenAI 互換サーバーでは、自動で外して送り直します
- 画像を読めないモデルでは、画像なしで設計します
- v0.1 の「OpenRouter の API キー」は、そのまま新しい設定に引き継がれます

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
| 範囲選択 | `region.py` `region_picker.py` | 別プロセスで全画面の半透明の幕を出し、ドラッグで範囲を描く（物理ピクセル） |
| 撮影 | `capture.py` | `mss` で範囲を撮影、長辺 768px に縮小、差分用の指紋を作る |
| 設計役 | `planner.py` | 依頼文（＋今の画面）から、問い・重み・通知条件の設計図を作る |
| 設計図 | `plan.py` | 検証、0〜1 への正規化、指標の計算、通知ルール（連続回数・クールダウン・再武装） |
| 判定 | `judges/` | 各社 API の差を吸収して SystemOne 形式の答えを返す |
| 実行 | `engine.py` | 撮影 → 変化がなければ判定を省略 → 判定 → 記録（振り返り用の縮小画像も保存）→ 通知判定 |
| 画面向け API | `api.py` | 画面から呼ばれる関数。どれも `{"ok": ..}` で返す |
| 画面 | `web/`（`index.html` `app.css` `app.js`） | HTML/CSS/JS。グラフは同梱の ECharts 6 |
| 起動 | `app.py` `server.py` | pywebview の専用ウィンドウ、使えなければローカルサーバー＋ブラウザ |

**指標の計算**：noul は「はい」の確率、score は段階 ÷（段階数 − 1）、choice は指定した選択肢の確率を 0〜1 の値とし、
設計図の重みで加重平均したものを指標とします。

**コストを抑える工夫**：前回の撮影と画素がほとんど変わっていなければ（平均差 < 2.0）、API を呼ばずに前回の値を使い回します（ログでは `reused`）。

## データの保存先

`%APPDATA%\imiwatch\`

- `settings.json`：設定（**API キーは暗号化せずに保存**されます）
- `monitors.json`：見張りの定義
- `monitors\<id>\log.csv`：判定ログ（時刻・状態・指標・観点ごとの値・モデル・所要時間・通知）
- `monitors\<id>\latest.jpg`、`notified_*.jpg`：最新の画像と、通知したときの画像
- `monitors\<id>\shots\*.jpg`：グラフで振り返るための、判定ごとの縮小画像（見張りごとに既定 3,000 枚まで。設定で変更可）
- `webview\`：画面の表示設定（選んだ見張り・期間など）

## プロキシ・社内ネットワークで使う

- **証明書**：HTTPS の確認に Windows の証明書ストアを使います（`truststore`）。社内の認証局の証明書を使うプロキシや社内 API でも、Windows に登録されていれば「certificate verify failed」になりません
- **プロキシ**：Windows のプロキシ設定（または環境変数 `HTTPS_PROXY`）に従います。自動構成スクリプト（PAC）だけで設定されている環境では読めないので、`HTTPS_PROXY` を設定してください
- `HTTPS_PROXY` を設定した場合は、jev-local や社内 API がプロキシを通らないよう `NO_PROXY=127.0.0.1,localhost,<社内APIのホスト>` も設定してください
- jev-local の SSH トンネルで 22 番ポートが使えない場合は、HTTPS で通る AWS の SSM ポートフォワーディングでもトンネルを張れます
- 画面の表示に外部のファイル（CDN・Web フォント）は使いません。外につながるのは、選んだ判定モデルと設計役の LLM だけです
- **つながらないときは早くあきらめます**：接続の待ち時間は 5 秒（設定の「画像と記録」で変更可）、接続テストは長くても 30 秒で打ち切ります。
  失敗したときは「プロキシで止められた」「証明書を確認できない」「名前を解決できない」「接続を拒否された」など、理由と経路（直接かプロキシ経由か）を表示します
- 自分の PC（127.0.0.1 / localhost）宛ての通信は、プロキシの設定があっても直接つなぎます

## 制約と注意

- **画面に見えている範囲しか撮れません**。ウィンドウが隠れる・最小化・スリープ・画面ロック中は正しく判定できません
- 範囲は画面上の座標で記録するため、ウィンドウを動かすと見張る対象がずれます
- **選んだ範囲の画像は、選んだ判定モデルの提供元（と、設計時は設計役の LLM）に送信されます**。
  jev-local と、ローカルの LLM（LM Studio・Ollama など）を組み合わせれば、画像は自分の管理する環境の外に出ません。
  個人情報や業務上の機密が映る範囲は選ばないでください
- 判定モデルは数を数える・計算する・細かい文字を読むのが苦手です。設計役の LLM はそうした依頼を見た目で判断できる問いに言い換えます
- 通知は Windows のトースト通知（`winotify`）を使い、使えない場合は画面の右下に表示します

## 開発

```bash
pip install -r requirements-dev.txt
python -m pytest
```

テストは API をモックし、画面撮影なしで、設計図・判定・指標・通知・保存・画面向け API・ローカルサーバーを確認します。

画面だけを開発するときは `python -m imiwatch --browser` でブラウザに表示し、`--debug` で開発者ツールを有効にできます。

## 同梱しているもの

- [Apache ECharts](https://echarts.apache.org/) 6.0.0（`imiwatch/web/vendor/echarts/`、Apache License 2.0）

## ライセンス

MIT
