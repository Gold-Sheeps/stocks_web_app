import argparse
import yfinance as yf
import datetime
import pandas
from pathlib import Path
import time  # 冒頭に追加
import const
import make_tikcker_and_market
import canslim_debt_check
import canslim_entory_point
import canslim_flat_base
import canslim_market_enviroment_check
import canslim_rs_ranking
import canslim_rule_roe_percentage
import canslim_rule_sales_percentage

# ★ client未定義で落ちるので追加（AIを使うなら必須）
from openai import OpenAI

client = OpenAI(api_key=const.openai_api_key)  # OPENAI_API_KEY が必要

pandas.options.display.max_rows = None
pandas.options.display.max_columns = None

csv_save_path = (
    r"C:\\Users\\o_van\\development\\stocks_price_getter\\stocks_csv_storage"
)

list_all_of_stocks_data = []


def create_ticker(ticker_symble):
    """yahooファイナンスようにインスタンス作成"""
    stocks_ticker = yf.Ticker(ticker_symble)
    return stocks_ticker


def open_hight_low_close_volume_data(
    stocks_ticker, today, ticker_symble, csv_save_path
):
    # 価格取得
    try:
        df = stocks_ticker.history(start="2022-01-01", end=today, auto_adjust=False)
    except Exception as e:
        print(f"[{ticker_symble}] history取得で例外 -> {type(e).__name__}: {e}")
        return None

    # 取得失敗ガード
    if df is None or df.empty or "Close" not in df.columns:
        print(f"[{ticker_symble}] 価格データ取得失敗（empty/Closeなし）。スキップ")
        return None

    close = df["Close"].dropna()
    if close.empty:
        print(f"[{ticker_symble}] Closeが全てNaN。スキップ")
        return None

    # CSV保存（安全なパス）
    out_dir = Path(csv_save_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ticker_symble}.csv"
    df.to_csv(out_path)

    latest_close_value = float(close.iloc[-1])

    # $15以上チェック
    if latest_close_value > 15:
        return df
    return None


def get_eps_data(
    stocks_ticker,
    ticker_symble: str,
    min_growth_pct: float = 25.0,
    require_consecutive: int = 2,
):
    try:
        earnings_dates = stocks_ticker.earnings_dates
    except Exception as e:
        print(
            f"[EPS] {ticker_symble}: earnings_dates取得失敗 -> {type(e).__name__}: {e}"
        )
        return False

    if (
        earnings_dates is None
        or earnings_dates.empty
        or "Reported EPS" not in earnings_dates.columns
    ):
        print(f"[EPS] {ticker_symble}: earnings_datesが空 / Reported EPSなし")
        return False

    eps_series = (
        earnings_dates["Reported EPS"]
        .dropna()
        .astype(float)
        .sort_index(ascending=False)
    )

    eps_list = eps_series.to_list()
    eps_date_list = list(eps_series.index)

    if len(eps_list) < 5:
        print(f"[EPS] {ticker_symble}: データ不足（{len(eps_list)}点）")
        return False

    growth_flags = []

    for i in range(len(eps_list) - 4):
        current_eps = eps_list[i]
        past_year_eps = eps_list[i + 4]

        if past_year_eps <= 0:
            meets = False
            status = "NG ❌"
            rate_str = "NA"
        else:
            eps_growth_rate = ((current_eps - past_year_eps) / abs(past_year_eps)) * 100
            meets = eps_growth_rate >= min_growth_pct
            status = "OK ✅" if meets else "NG ❌"
            rate_str = f"{eps_growth_rate:.2f}%"

        growth_flags.append(meets)

        print(
            f"{eps_date_list[i].date()} vs {eps_date_list[i+4].date()} | "
            f"EPS: {current_eps:.2f} -> {past_year_eps:.2f} | "
            f"成長率 {rate_str} | {status}"
        )

    streak = 0
    for flag in growth_flags:
        if flag:
            streak += 1
        else:
            break

    passed = streak >= require_consecutive
    print(
        f"[判定] {ticker_symble}: 直近{require_consecutive}四半期でEPS成長率{min_growth_pct}%以上 "
        f"{'✅' if passed else '❌'}"
    )
    return passed


def volume_check_over_60000(three_years_stocks_price, ticker_symble):
    """出来高が急増している確認（★return追加）"""
    volume_series = three_years_stocks_price["Volume"].dropna()
    if len(volume_series) < 50:
        print(f"[{ticker_symble}] Volumeデータ不足({len(volume_series)}日)")
        return False

    avg_volume_50 = volume_series.tail(50).mean()
    latest_volume = volume_series.iloc[-1]

    # 「60000以上」も見たいならここで入れる（あなたの意図に合わせた）
    if avg_volume_50 < 60000:
        volume_spike = False
    else:
        volume_spike = (latest_volume / avg_volume_50) >= 1.5

    print("出来高急増？:", volume_spike)
    return bool(volume_spike)


def check_latest_operating_cashflow_positive(
    stocks_ticker, ticker_symbol: str | None = None
):
    """
    年次キャッシュフローから「直近年の営業CFがプラスか」を判定する
    戻り値: (判定結果bool, 直近年の営業CF値 or None, 直近年カラム名 or None)
    """
    cf = stocks_ticker.cashflow
    if cf is None or cf.empty:
        print(f"[{ticker_symbol}] cashflowデータが取得できませんでした")
        return False, None, None

    candidates = [
        "Total Cash From Operating Activities",
        "Total Cashflow From Operations",
        "Operating Cash Flow",
        "Net Cash Provided by Operating Activities",
    ]

    ocf_series = None
    for name in candidates:
        if name in cf.index:
            ocf_series = cf.loc[name]
            break

    if ocf_series is None:
        matches = [
            idx
            for idx in cf.index
            if "operat" in str(idx).lower() and "cash" in str(idx).lower()
        ]
        if matches:
            ocf_series = cf.loc[matches[0]]
        else:
            print(
                f"[{ticker_symbol}] 営業CFの行が見つかりません。先頭の行名例: {list(cf.index)[:8]}"
            )
            return False, None, None

    cols = list(ocf_series.index)

    def to_dt(x):
        try:
            return pandas.to_datetime(x)
        except Exception:
            return pandas.NaT

    valid = [(c, to_dt(c)) for c in cols]
    valid2 = [(c, d) for c, d in valid if not pandas.isna(d)]
    latest_col = max(valid2, key=lambda t: t[1])[0] if valid2 else cols[0]

    latest_ocf = ocf_series[latest_col]
    if pandas.isna(latest_ocf):
        print(f"[{ticker_symbol}] 直近年の営業CFがNaNでした（判定不可）")
        return False, None, latest_col

    passed = float(latest_ocf) > 0
    print(
        f"[{ticker_symbol}] 直近年 営業CF({latest_col}) = {latest_ocf} -> {'OK' if passed else 'NG'}"
    )
    return passed, float(latest_ocf), latest_col


def analyze_stock_with_ai(ticker, data_summary):
    """
    収集した銘柄データをAIに渡して判定させる
    ★元の変数名に合わせて data_summary.get('price') 等を見る
    """
    prompt = f"""
あなたはCAN-SLIM投資法を熟知したAIアナリストです。以下のデータを基に「買い/監視/見送り」を日本語で簡潔に出力してください。

【銘柄】{ticker}
- 現在株価(USD): {data_summary.get('price')}
- RSレーティング: {data_summary.get('rs_rating')}
- EPS成長(判定): {data_summary.get('eps_growth')}
- 売上成長(判定): {data_summary.get('sales_growth')}
- 営業CF(判定): {data_summary.get('operating_cf_ok')} / 値: {data_summary.get('operating_cf_value')}
- ROE(判定): {data_summary.get('roe_check')}
- 出来高急増(判定): {data_summary.get('volume_spike')}

- フラットベース: {data_summary.get('flat_base')}
- ブレイク: {data_summary.get('breakout_ok')}
- 買いレンジ内(+5%): {data_summary.get('within_buy_range')}
- ブレイクかつ出来高増(目安): {data_summary.get('volume_up_on_break')}
- ピボット: {data_summary.get('pivot')}
- 買いレンジ上限(+5%): {data_summary.get('buy_upper')}
- 52週高値: {data_summary.get('high_52w')}
- 高値圏(52週高値-15%以内): {data_summary.get('near_high')}
- 出来高比(直近/50日平均): {data_summary.get('breakout_vol_ratio')}

- 負債状況: {data_summary.get('debt_status')}
- 市場環境(セクター/ベンチマーク): {data_summary.get('market_enviroment')}
- 市場全体(SPY): {data_summary.get('general_market_enviroment_spy')}
- 市場全体(QQQ): {data_summary.get('general_market_enviroment_qqq')}

【追加要件：N（New）について】
あなたの一般知識と上のシグナル（52週高値圏・新高値・ブレイク等）から、
この銘柄に「新しさ（新製品/新市場/新規需要/規制承認/業績転換/新高値など）」が“ありそうか”を推測してください。
ただし根拠が薄い場合は断定せず、必ず「要確認」として確認すべき情報源を提示してください。

【出力フォーマット】
判定: 買い/監視/見送り
理由:
- ...
- ...
買いと監視の場合はエントリーのためのピポットポイントの額を記載してください

N評価: 強い/弱い/要確認
N根拠:
- ...
要確認リスト:
- 決算資料（サプライズ/ガイダンス）
- プレスリリース（新製品/大型契約）
- 規制承認（該当業界のみ）
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": "あなたは優秀な株式投資アシスタントです。",
                },
                {"role": "user", "content": prompt},
            ],
            # temperature=1,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI分析エラー: {e}"


def main(args):
    today = datetime.date.today()

    ticker_list = canslim_rs_ranking.get_tickers_from_csv()

    rs_df = canslim_rs_ranking.build_rs_from_universe(ticker_list)
    rs_df.to_csv("rs_ranking_result.csv", index=False, encoding="utf-8-sig")

    ticker_market_list_tuple = make_tikcker_and_market.get_high_rank_tickers_from_csv()
    print(ticker_market_list_tuple)

    print("--- RS計算完了。API制限回避のため60秒待機します... ---")
    time.sleep(60)

    # ★市場全体（SPY/QQQ）のトレンドを1回だけ取得してAIへ渡す（Mの強化）
    try:
        general_market_enviroment_spy = (
            canslim_market_enviroment_check.check_market_direction_by_symbol("SPY")
        )
    except Exception as e:
        print(f"[SPY] 市場環境取得失敗: {e}")
        general_market_enviroment_spy = None

    try:
        general_market_enviroment_qqq = (
            canslim_market_enviroment_check.check_market_direction_by_symbol("QQQ")
        )
    except Exception as e:
        print(f"[QQQ] 市場環境取得失敗: {e}")
        general_market_enviroment_qqq = None

    final_report = []

    # ★戻り値タプル長が揺れても落ちないように（変数名は維持）
    for i, item in enumerate(ticker_market_list_tuple):
        # 想定: (Display, Api, RS, Sector, Industry, Benchmark)
        try:
            ticker_symble = item[1]  # Api
            market_symble = item[5]  # Benchmark
        except Exception:
            print(f"スキップ: タプル構造不一致 {item}")
            continue

        print(f"[{i}/{len(ticker_market_list_tuple)}] Checking: {ticker_symble} ...")

        stock_data = {
            "ticker": ticker_symble,
            "rs_rating": "85以上 (RSランク上位)",
        }

        # ★市場全体トレンドもAIへ渡す（変数名はこのまま）
        stock_data["general_market_enviroment_spy"] = general_market_enviroment_spy
        stock_data["general_market_enviroment_qqq"] = general_market_enviroment_qqq

        max_retries = 3
        for attempt in range(max_retries):
            try:
                stocks_ticker = create_ticker(ticker_symble)

                three_years_stocks_price = open_hight_low_close_volume_data(
                    stocks_ticker, today, ticker_symble, csv_save_path
                )

                if three_years_stocks_price is None:
                    print("  -> 価格データ取得不可または$15以下。スキップ")
                    break

                # ★CAN-SLIMの計算には df が必要。AIへは最新値floatだけ渡す。
                latest_close_value = float(
                    three_years_stocks_price["Close"].dropna().iloc[-1]
                )
                stock_data["price"] = latest_close_value

                # 既存キー名のまま格納（AI promptもそれに合わせた）
                stock_data["eps_growth"] = get_eps_data(stocks_ticker, ticker_symble)
                stock_data["volume_spike"] = volume_check_over_60000(
                    three_years_stocks_price, ticker_symble
                )

                operating_cf_ok, operating_cf_value, operating_cf_col = (
                    check_latest_operating_cashflow_positive(
                        stocks_ticker, ticker_symble
                    )
                )
                # ★要求どおり分割
                stock_data["operating_cf_ok"] = operating_cf_ok
                stock_data["operating_cf_value"] = operating_cf_value
                stock_data["operating_cf_col"] = operating_cf_col

                stock_data["debt_status"] = canslim_debt_check.check_debt(ticker_symble)

                stock_data["sales_growth"] = (
                    canslim_rule_sales_percentage.check_can_slim_sales(ticker_symble)
                )
                stock_data["roe_check"] = (
                    canslim_rule_roe_percentage.check_profitability(ticker_symble)
                )

                stock_data["market_enviroment"] = (
                    canslim_market_enviroment_check.check_market_direction_by_symbol(
                        market_symble
                    )
                )
                stock_data["flat_base"] = canslim_flat_base.check_flat_base(
                    ticker_symble
                )

                # ★ブレイク関数（提示コードは ticker_symbol だけ）
                breakout_candidate_result = (
                    canslim_entory_point.check_breakout_candidate(
                        price_df=three_years_stocks_price, ticker_symbol=ticker_symble
                    )
                )

                if breakout_candidate_result is None or (
                    "error" in breakout_candidate_result
                ):
                    stock_data["buy_point_error"] = (
                        breakout_candidate_result.get("error")
                        if breakout_candidate_result
                        else "breakout判定失敗"
                    )
                    stock_data["breakout_ok"] = False
                else:
                    # ★キーが英/日どちらでも拾えるように（提示コードは日本語キー）
                    stock_data["breakout_ok"] = breakout_candidate_result.get(
                        "base_break", breakout_candidate_result.get("ベースブレイク")
                    )
                    stock_data["within_buy_range"] = breakout_candidate_result.get(
                        "within_buy_range",
                        breakout_candidate_result.get("買いレンジ内"),
                    )
                    stock_data["volume_up_on_break"] = breakout_candidate_result.get(
                        "volume_up_on_break",
                        breakout_candidate_result.get("ブレイク日かつ出来高増"),
                    )

                    stock_data["pivot"] = breakout_candidate_result.get(
                        "pivot", breakout_candidate_result.get("ピボット")
                    )
                    stock_data["buy_upper"] = breakout_candidate_result.get(
                        "buy_upper",
                        breakout_candidate_result.get("買いレンジ上限(+5%)"),
                    )
                    stock_data["high_52w"] = breakout_candidate_result.get(
                        "high_52w", breakout_candidate_result.get("52週高値")
                    )
                    stock_data["near_high"] = breakout_candidate_result.get(
                        "near_high",
                        breakout_candidate_result.get("高値圏(52週高値-15%以内)"),
                    )
                    stock_data["breakout_vol_ratio"] = breakout_candidate_result.get(
                        "vol_ratio",
                        breakout_candidate_result.get("出来高比(直近/50日平均)"),
                    )

                if args.mode == "use_ai":
                    print("  -> AIによる最終判定を実行中...")
                    ai_decision = analyze_stock_with_ai(ticker_symble, stock_data)

                    print(f"  ★ AI判定: {ai_decision}")

                    final_report.append(
                        {"Ticker": ticker_symble, "AI_Decision": ai_decision}
                    )
                    break
                else:
                    print(f"  -> データ収集完了（AI分析なし）: {ticker_symble}")
                    final_report.append(stock_data)
                    break

            except yf.exceptions.YFRateLimitError:
                print(
                    f"[WARN]️ Rate Limit Hit on {ticker_symble}. Attempt {attempt+1}/{max_retries}."
                )
                if attempt < max_retries - 1:
                    print("   Waiting 5 seconds before retry...")
                    time.sleep(5)
                    continue
                else:
                    print("❌ Max retries reached. Skipping this stock.")

            except Exception as e:
                print(f"❌ Error on {ticker_symble}: {e}")
                break

        time.sleep(1.5)

    if final_report:
        df_report = pandas.DataFrame(final_report)
        if args.mode == "use_ai":
            df_report.to_csv(
                "ai_analysis_report.csv", index=False, encoding="utf-8-sig"
            )
            print("\n=== AI分析レポートを作成しました: ai_analysis_report.csv ===")
        else:
            df_report.to_csv(
                "Non_ai_analysis_report.csv", index=False, encoding="utf-8-sig"
            )
            print(
                "\n=== 分析レポートを作成しましたがAIは使用しておりません: Non_ai_analysis_report.csv ==="
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AIを使う場合、「 use_ai」とオプションを入力してください"
    )
    parser.add_argument("--mode", help="ai か ai を使用しないか選択してください。")
    args = parser.parse_args()
    main(args)
