import yfinance as yf
import pandas as pd


def calculate_yoy_growth(data_list, dates, label, threshold_25=25, threshold_18=18):
    """
    YoY（前年同期比）成長率を計算して表示する
    - data_list: 最新が先頭のリスト（例: revenue_list[0] が最新）
    - dates: data_list と同じ並びの日時インデックス
    """
    found_data = False

    for index_number, current_value in enumerate(data_list):
        # 4四半期前（前年同期）の値が存在するか
        if index_number + 4 < len(data_list):
            past_year_value = data_list[index_number + 4]

            # 欠損チェック
            if (
                pd.notnull(current_value)
                and pd.notnull(past_year_value)
                and past_year_value != 0
            ):
                growth = (
                    (current_value - past_year_value) / abs(past_year_value)
                ) * 100

                # 日付表示（yfinanceの列は Timestamp なので strftime できる想定）
                try:
                    report_date = dates[index_number].strftime("%Y-%m-%d")
                except Exception:
                    report_date = str(dates[index_number])

                status = (
                    "✅"
                    if growth >= threshold_25
                    else ("📈" if growth >= threshold_18 else "  ")
                )
                print(
                    f"発表日: {report_date} | {label}成長率(YoY): {growth:6.2f}% {status}"
                )
                found_data = True
        else:
            break

    if not found_data:
        print(
            f"{label}のYoY計算に使えるデータが見つかりませんでした（期数不足/欠損の可能性）。"
        )

    return found_data


def check_sales_rule(revenue_list, dates):
    """
    3-2. 売上（必須）
    - 直近の売上が YoY +25%以上
      (データ不足なら) 直近3四半期が上向き（QoQで連続増）
    戻り値: (pass_or_fail: bool, reason: str)
    """

    # まず「直近YoY(+25%)」を判定できるか（最低5期必要）
    can_calc_yoy = len(revenue_list) >= 5

    if can_calc_yoy:
        current_revenue = revenue_list[0]
        past_year_revenue = revenue_list[4]

        # 欠損や0割り対策
        if (
            pd.notnull(current_revenue)
            and pd.notnull(past_year_revenue)
            and past_year_revenue != 0
        ):
            growth = (
                (current_revenue - past_year_revenue) / abs(past_year_revenue)
            ) * 100

            # 直近の表示日付
            try:
                report_date = dates[0].strftime("%Y-%m-%d")
            except Exception:
                report_date = str(dates[0])

            if growth >= 25:
                return True, f"直近売上YoYが+25%以上（{report_date}: {growth:.2f}%）"
            else:
                # YoYが計算できたが基準未達
                return False, f"直近売上YoYが+25%未満（{report_date}: {growth:.2f}%）"
        else:
            # YoY判定に必要な値が欠損している
            can_calc_yoy = False

    # ここに来た場合：
    # - 期数不足でYoYが計算できない、または欠損でYoY判定できない
    # → 代替条件「直近3四半期が上向き」を判定する（最低3期必要）
    if len(revenue_list) >= 3:
        r0, r1, r2 = revenue_list[0], revenue_list[1], revenue_list[2]

        if pd.notnull(r0) and pd.notnull(r1) and pd.notnull(r2):
            up_3q = (r0 > r1) and (r1 > r2)
            if up_3q:
                return (
                    True,
                    "YoY判定不可（期数不足/欠損）のため代替：直近3四半期が上向き（QoQ連続増）",
                )
            else:
                return (
                    False,
                    "YoY判定不可（期数不足/欠損）のため代替：直近3四半期が上向きになっていない",
                )
        else:
            return False, "YoY判定不可＋直近3四半期の売上に欠損があり、上向き判定も不可"
    else:
        return False, "売上データが3期未満で、YoYも上向き判定も不可"


def check_can_slim_sales(ticker_symbol):
    ticker = yf.Ticker(ticker_symbol)

    print(f"=== {ticker_symbol} CAN-SLIM 3-2 売上チェック ===")

    q_financials = ticker.quarterly_financials
    if q_financials is None or q_financials.empty:
        print("[WARN]️ 四半期財務データ（quarterly_financials）が取得できませんでした。")
        return

    # 期数チェック（参考表示）
    print(f"取得できた四半期数: {len(q_financials.columns)} 期")
    if len(q_financials.columns) < 5:
        print("[WARN]️ YoY(+25%)の計算には最低5期必要です（期数不足の可能性あり）。")
        print("   → 期数不足/欠損の場合は『直近3四半期が上向き』で代替判定します。")

    if "Total Revenue" not in q_financials.index:
        print("[WARN]️ 'Total Revenue' が quarterly_financials に存在しませんでした。")
        return

    revenue_data = q_financials.loc["Total Revenue"]
    revenue_list = revenue_data.tolist()

    # --- ここから追加：売上データ5期分の生データ表示 ---
    print("\n--- 売上データ（最新から5期分）: Total Revenue ---")
    for i in range(min(5, len(revenue_list))):
        try:
            report_date = revenue_data.index[i].strftime("%Y-%m-%d")
        except Exception:
            report_date = str(revenue_data.index[i])

        value = revenue_list[i]
        # そのままでもいいけど、見やすく桁区切り（欠損はそのまま）
        if pd.notnull(value):
            print(f"Index {i}: {report_date} | {value:,.0f}")
        else:
            print(f"Index {i}: {report_date} | None")

    # YoYで実際に比較する「直近 vs 4期前」も明示
    if (
        len(revenue_list) >= 5
        and pd.notnull(revenue_list[0])
        and pd.notnull(revenue_list[4])
        and revenue_list[4] != 0
    ):
        yoy = ((revenue_list[0] - revenue_list[4]) / abs(revenue_list[4])) * 100

        try:
            d0 = revenue_data.index[0].strftime("%Y-%m-%d")
            d4 = revenue_data.index[4].strftime("%Y-%m-%d")
        except Exception:
            d0 = str(revenue_data.index[0])
            d4 = str(revenue_data.index[4])

        print("\n--- YoYで使った比較（直近 vs 4期前）---")
        print(f"直近(Index 0): {d0} | {revenue_list[0]:,.0f}")
        print(f"前年(Index 4): {d4} | {revenue_list[4]:,.0f}")
        print(f"売上YoY成長率: {yoy:.2f}%")
    else:
        print("\n--- YoY比較（直近 vs 4期前）---")
        print("データ不足 or 欠損のため、直近YoY(+25%)判定ができません。")
    # --- 追加ここまで ---

    print("\n--- 参考表示：四半期売上のYoY一覧 ---")
    calculate_yoy_growth(revenue_list, revenue_data.index, "売上")

    print("\n--- 判定：3-2 売上（必須） ---")
    ok, reason = check_sales_rule(revenue_list, revenue_data.index)
    print(("✅ 合格: " if ok else "❌ 不合格: ") + reason)
