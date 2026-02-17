import yfinance as yf
import pandas as pd
import time
from typing import List, Tuple, Dict, Optional
import yfinance.exceptions

# リストの要素定義: (display_ticker, api_ticker)
UniverseItem = Tuple[str, str]


def _chunk(lst: List[str], size: int):
    """リストを指定サイズに分割するジェネレータ"""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _download_close_map(
    tickers: List[str], period: str = "14mo"
) -> Dict[str, pd.Series]:
    """
    tickers をまとめてDLして Close(終値) だけ辞書化して返す
    - 堅牢なエラーハンドリングとリトライ機能付き
    """
    close_map: Dict[str, pd.Series] = {}

    if not tickers:
        return close_map

    # リトライ設定
    max_retries = 3
    retry_delay = 5  # 秒

    data = None

    for attempt in range(max_retries):
        try:
            data = yf.download(
                tickers=tickers,
                period=period,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                progress=False,
                threads=True,
            )
            break  # 成功したらループを抜ける

        except yfinance.exceptions.YFRateLimitError:
            print(f"[WARN]️ Rate Limit Hit. Attempt {attempt+1}/{max_retries}.")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print("❌ Max retries reached for this chunk.")
                return close_map

        except Exception as e:
            print(f"Download error: {e}")
            return close_map

    if data is None or data.empty:
        return close_map

    # データ抽出処理（MultiIndex対応）
    if isinstance(data.columns, pd.MultiIndex):
        for t in tickers:
            try:
                if t in data.columns.get_level_values(0):
                    if "Close" in data[t].columns:
                        s = data[t]["Close"].dropna()
                        if not s.empty:
                            close_map[t] = s
            except Exception:
                pass
    else:
        # 単一銘柄の場合
        if "Close" in data.columns:
            s = data["Close"].dropna()
            if not s.empty:
                if len(tickers) > 0:
                    close_map[tickers[0]] = s

    return close_map


def _extract_prices(close: pd.Series) -> Optional[dict]:
    """
    0,3,6,9,12ヶ月相当（営業日ベース）の株価を抽出
    """
    close = close.dropna()
    # 252営業日(約1年)以上のデータが必要
    if len(close) < 252:
        return None

    try:
        p = {
            "p0": float(close.iloc[-1]),  # 現在
            "p3": float(close.iloc[-63]),  # 3ヶ月前
            "p6": float(close.iloc[-126]),  # 6ヶ月前
            "p9": float(close.iloc[-189]),  # 9ヶ月前
            "p12": float(close.iloc[-252]),  # 1年前
        }
    except Exception:
        return None

    # 株価が0以下の異常値を除外
    if any(v <= 0 for v in p.values()):
        return None
    return p


def weighted_relative_score(stock_close: pd.Series) -> Optional[float]:
    """
    IBD擬似計算式に基づいて「絶対スコア」を算出
    Formula:
      0.4 * (C - C63)/C63
    + 0.2 * (C - C126)/C126
    + 0.2 * (C - C189)/C189
    + 0.2 * (C - C252)/C252
    """
    sp = _extract_prices(stock_close)
    if sp is None:
        return None

    # 各期間の騰落率計算
    roc_3m = (sp["p0"] - sp["p3"]) / sp["p3"]
    roc_6m = (sp["p0"] - sp["p6"]) / sp["p6"]
    roc_9m = (sp["p0"] - sp["p9"]) / sp["p9"]
    roc_12m = (sp["p0"] - sp["p12"]) / sp["p12"]

    # 重み付け計算（これが絶対スコアになります）
    raw_score = (
        (roc_3m * 0.4) + (roc_6m * 0.2) + (roc_9m * 0.2) + (roc_12m * 0.2)
    ) * 100

    return float(raw_score)


def get_tickers_from_csv(csv_path="Monex_US_LIST.csv") -> List[UniverseItem]:
    """
    MonexのCSVを読み込み、(DisplayTicker, ApiTicker) のリストを返す
    """
    print(f"--- {csv_path} を読み込み中... ---")
    try:
        # 日本語CSV（Shift_JIS / cp932）対応
        df = pd.read_csv(
            csv_path,
            header=None,
            skiprows=1,
            usecols=[0, 4],  # 0:Ticker, 4:Type
            names=["ticker", "type"],
            encoding="cp932",
        )
    except FileNotFoundError:
        print(f"[エラー] {csv_path} が見つかりません。")
        return []
    except Exception as e:
        print(f"[エラー] CSV読み込み失敗: {e}")
        return []

    # "Common Stock" のみに限定
    common_stock_df = df[df["type"] == "Common Stock"]

    universe = []
    for ticker in common_stock_df["ticker"]:
        # yfinance用にシンボル変換 (例: BRK.B -> BRK-B)
        api_ticker = ticker.replace(".", "-")
        universe.append((ticker, api_ticker))

    print(f"--- リスト作成完了: {len(universe)} 件 ---")
    return universe


def build_rs_from_universe(
    universe: List[UniverseItem],
    chunk_size: int = 150,
    period: str = "14mo",
) -> pd.DataFrame:
    """
    universe から株価を取得し、絶対スコアを計算した後、
    ★全銘柄内での順位（1〜99）に変換して返す★
    """
    # API用ティッカーリスト作成
    api_tickers = sorted(list(set([api for _, api in universe])))

    print(f"RS算出用データ取得開始: 対象 {len(api_tickers)} 銘柄")

    # 1. 全銘柄の株価データを取得
    close_map: Dict[str, pd.Series] = {}
    chunks = list(_chunk(api_tickers, chunk_size))

    for i, part in enumerate(chunks):
        print(f"Downloading chunk {i+1}/{len(chunks)} ...")
        part_map = _download_close_map(part, period=period)
        close_map.update(part_map)
        time.sleep(2.0)  # API制限回避用

    print("データ取得完了。スコア計算中...")

    # 2. 絶対スコア(RawScore)の計算
    records = []
    for display, api in universe:
        stock_close = close_map.get(api)
        if stock_close is None:
            continue

        score = weighted_relative_score(stock_close)
        if score is None:
            continue

        records.append(
            {
                "Ticker": display,
                "ApiTicker": api,
                "RawScore": score,  # まだ絶対値の状態
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return df

    # 3. ★ランキング化処理 (Percentile Ranking)★
    # ここで絶対スコアを全銘柄中の順位(1〜99)に変換します
    # rank(pct=True) は 0.0〜1.0 の値を返します
    # 例: 上位1% -> 0.99... -> 98点台 -> +1して99
    df["RS_Rating"] = (df["RawScore"].rank(pct=True) * 98) + 1

    # 小数点を切り捨てて整数にする (IBDスタイル)
    df["RS_Rating"] = df["RS_Rating"].astype(int)

    # RSの高い順に並び替え
    df = df.sort_values("RS_Rating", ascending=False).reset_index(drop=True)

    return df
