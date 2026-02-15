import yfinance as yf
import pandas as pd


def check_profitability(ticker_symbol):
    ticker = yf.Ticker(ticker_symbol)
    info = ticker.info

    print(
        f"========== {ticker_symbol} 収益性チェック (ROE / Profitability) ==========\n"
    )

    # --- 1. ROE (自己資本利益率) の取得 ---
    roe = info.get("returnOnEquity")

    if roe is not None and pd.notnull(roe):
        roe_pct = float(roe) * 100
        status = "✅合格 (17%以上)" if roe_pct >= 17 else "❌基準未満"
        print(f"[ROE] 実績: {roe_pct:.2f}%  {status}")
    else:
        print("[ROE] データが取得できませんでした。")

    # --- 2. 税引後利益 (Net Income) のトレンド ---
    q_financials = ticker.quarterly_financials

    # 「Net Income」が無い銘柄もあるので候補を用意
    net_income_key = None
    for key in ["Net Income", "Net Income Common Stockholders"]:
        if key in q_financials.index:
            net_income_key = key
            break

    if net_income_key is not None:
        net_income_series = q_financials.loc[net_income_key]

        # 取得できた“生データ(期と値)”を出す：これがないと計算の正しさが確認できない
        print(f"\n[純利益の取得データ ({net_income_key})]")
        print(net_income_series)

        # NaNを落として数値化
        net_income_series = net_income_series.dropna().astype(float)

        # データが少なすぎる場合
        if len(net_income_series) == 0:
            print("\n[純利益] 値がすべて欠損でした。")
            print("\n" + "=" * 40)
            return

        # 直近がどれかを明示（列が日付なので、最大日付を直近扱いにする）
        # ※ yfinanceの列は Timestamp のことが多い
        net_income_series = net_income_series.sort_index(ascending=False)  # 最新→過去

        net_income_list = net_income_series.tolist()

        print("\n[純利益のトレンド (四半期)]")
        current_net_income = net_income_list[0]
        historical_max = max(net_income_list)

        is_near_high = (
            "✅過去最高水準"
            if current_net_income >= historical_max * 0.95
            else "段階的成長中"
        )

        if len(net_income_list) >= 2:
            prev_net_income = net_income_list[1]
            trend = (
                "📈上向き" if current_net_income > prev_net_income else "📉横ばい/下落"
            )
            print(f"直近純利益: ${current_net_income:,.0f} ({trend} / {is_near_high})")

            # 何期のデータを見ているか（日付付き）で表示
            print("\n[期ごとの純利益（直近→過去）]")
            for dt, val in net_income_series.items():
                # dt が Timestamp の場合は strftime できる
                try:
                    dt_str = dt.strftime("%Y-%m-%d")
                except:
                    dt_str = str(dt)
                print(f"  {dt_str}: ${val:,.0f}")
        else:
            print(f"直近純利益: ${current_net_income:,.0f} ({is_near_high})")
    else:
        print("\n[純利益] 'Net Income' 系の行が財務データに見つかりませんでした。")

    print("\n" + "=" * 40)
