# pinbook-spider

simple scraper for redbook 🍠 and pinterest 🍎

use selenium to login and get data automatically.

## redbook

### features

- [x] get all post url use profile url
- [x] get explore post info

### Usage

1. Install requirements.

```bash
pip install -r redbook/requirements.txt
```

2. Run the chrome for redbook, open url "<https://www.xiaohongshu.com>" and login with your account , and keep the window open.

```bash
sh redbook/start_chrome.sh
```

3. Run the scraper for redbook.

```bash
python redbook/redbook.py
```

## License

[MIT](https://choosealicense.com/licenses/mit/)
