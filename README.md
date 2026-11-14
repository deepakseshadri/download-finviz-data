# download-finviz-data

www.finviz.com has a wealth of information about US equities and ETFs in the "screener" section of their website.
This project downloads every row and every field of both the stock screener (`/screener/stocks`) and the ETF
screener (`/screener/etf`) and stores it as CSV (optionally gzip-compressed).

Each screener page embeds its data as JSON (`<script type="application/json">`); that payload is parsed directly.
The visible HTML grid is virtualized and only renders a subset of the columns, so it is not scraped.

Only one session is established to their site per run, and requests are spaced out (see `--delay`) so the load on
their server is minimal. Do not parallelize or speed it up: Cloudflare blocks the IP (for 10+ minutes, observed
2026-11-13) after ~50 pages at ~3.6s spacing, and every extra attempt while blocked risks a longer block. A full run
is ~590 pages and takes ~2 hours at the default delay, longer if it has to wait out a block.

Running it once a day, after market close, should be more than enough as data is not updated that often.


## Installation

```
cd download-finviz-data
pip install -e .
```

## Uninstall

```
pip uninstall download-finviz-data
```

## Usage

To see available options
```
download_finviz_data -h
```

Download everything to a gzip-compressed CSV (gzip is used whenever the file name ends in `.gz`)
```
download_finviz_data --out-file /tmp/finviz.csv.gz
```

Quick check that the site still parses (first page of each screener only)
```
download_finviz_data -t -o /tmp/finviz_test.csv.gz
```

| Option | Default | Description |
|---|---|---|
| `-o`, `--out-file` | required | Output path; `.gz` suffix means gzip |
| `-d`, `--delay` | `10.0` | Base seconds between requests; each wait is stretched by a random +10–30% (never shorter) |
| `-t`, `--test` | off | Only the first page of each screener |
| `-l`, `--log-level` | `INFO` | `DEBUG`, `INFO` or `ERROR` |

The process exits with status 1 and logs the reason if anything goes wrong; no output file is written in that case.

## Output format

* One row per ticker: stocks first, then ETFs.
* Columns are Finviz's own field names, as returned (`ticker`, `company`, `marketCap`, `PE`, `e.NetExpenseRatio`,
  `range52w.low`, ...). `ticker` and `company` come first, the rest in the order Finviz returns them. The set of
  columns is whatever Finviz returns, so new fields appear automatically; read columns by name, not position.
* Fields that don't apply to a row (e.g. `PE` for an ETF, `e.NetExpenseRatio` for a stock) are empty.
* Values are raw numbers, not display strings. Floats are rounded to 4 decimal places.
  * `marketCap`, `sharesOutstanding2` and `sharesFloat` are in millions (e.g. `49044.35` = $49.04B).
  * Percentages are plain numbers in percent units (`dividendYield` `0.59` = 0.59%).
  * `earningsDate` is an ISO datetime (`2026-11-23T16:30:00`).

```python
import pandas as pd
df = pd.read_csv('/tmp/finviz.csv.gz')
```

## Reliability

* Cloudflare challenges, HTTP 429/5xx and connection errors are retried on the same page after 15, 30, 30 and 30
  minutes (same +10–30% jitter); rows already fetched are kept, so the run resumes where it was blocked. If one page
  still fails, or the run's retry waits would exceed 4 hours in total, the run fails.
* Progress is logged every 50 pages.
* A run fails rather than writing bad data when the page's JSON is missing, a page comes back empty or with the
  wrong offset, a row has no ticker, or far fewer unique tickers arrive than Finviz reports. Small drift (listings
  changing during the run) and duplicate tickers are logged as warnings.

## Repairing truncated tickers (files from 2026-07-15 to 2026-09-29)

A Finviz markup change made the old scraper keep only the first character of each ticker in those files; every other
column is intact. `repair` recovers the tickers from the company names using the intact files in the same directory
(nearest sighting in time, same first letter, and Finviz's ticker sort order as a cross-check):

```
python -m download_finviz_data.repair --data-dir ~/Downloads/finviz_data --dry-run   # counts only
python -m download_finviz_data.repair --data-dir ~/Downloads/finviz_data
```

* Originals are copied to `_backup_truncated/` first; only the ticker field changes.
* Rows that can't be recovered with certainty keep their single letter and are listed in `_repair_report.csv`
  (`not_found`, `ambiguous` or `order_conflict`).
* Re-runs start from the backups, so running it again after newer full downloads exist recovers more (e.g. ETFs
  listed after July, which no earlier file knows).

## Testing

```
pip install pytest
pytest              # offline tests against saved Finviz pages
pytest -m live      # also hits finviz.com (2 requests)
```

## License
[MIT](https://choosealicense.com/licenses/mit/)
