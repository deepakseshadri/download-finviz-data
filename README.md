# download-finviz-data

www.finviz.com has a wealth of information about US Equities that is available on the "screener" section of their 
website.  They have 68 different metrics for each symbol and they cover pretty much all of US Equities. This project 
scrapes that information from their site and stores it in CSV format.

I have written this code such that only one session is established to their site per run so the load on their server
is minimal and doesn't trigger the access limit from each IP. I intend to keep it this way. Yes, it could be 
parallelized to have it finish in a fraction of the current runtime but the site blocks access when you cross a 
certain access threshold.


Running it once a day, after market close, should be more than enough as data is not updated
that often.


## Installation

This code has been tested only with Python 3.8+
```
cd download-finviz-data
python setup.py develop
```

## Uninstall

Use pip to remove it
```
pip uninstall download-finviz-data
```

## Usage

To see available options
```
download_finviz_data -h
```

To run it with default options and save the output to a file
```
download_finviz_data --out-file /tmp/finviz.csv
```

## Contribution
Please open an issue first to discuss what you would like to change.  Pull requests are welcome.

Please make sure to update tests as appropriate.

## License
[MIT](https://choosealicense.com/licenses/mit/)


## Testing
```
pip install coverage
coverage run -m unittest
```

To see the coverage report
```
coverage report -m
```