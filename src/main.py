import csv
import yfinance
import datetime

today = datetime.date.today()

def read_csv():
    """
    CSVファイルを読み込み、銘柄コードのリストを返す
    """
    stocks_list = []
    with open("..//stocks_list//us_stocks_list.csv", "r") as f:
        reader = csv.reader(f)
        for stock_name in reader:
            stocks_list.append(stock_name[0])
    return stocks_list


def stocks_price_get(stocks_list):
    """
    """
    for indivisual_stock in stocks_list:
        ticker_data = yfinance.Ticker(indivisual_stock)
        us_stocks = ticker_data.history(start='2018-01-01', end=today)
        print(us_stocks)


def main():
    stocks_list = read_csv()
    print(stocks_list)
    stocks_price_get(stocks_list)


if __name__ == "__main__":
    main()
