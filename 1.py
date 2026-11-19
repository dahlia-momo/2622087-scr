import time
import sqlite3
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# 1. データベース（SQLite）の初期準備（1ページごとに安全に保存するため先に作成）
db_name = "google_repos.db"
conn = sqlite3.connect(db_name)
cursor = conn.cursor()

cursor.execute("DROP TABLE IF EXISTS repositories")
cursor.execute("""
CREATE TABLE repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_name TEXT NOT NULL UNIQUE,
    language TEXT,
    stars TEXT
)
""")
conn.commit()

page_num = 1
total_saved = 0

print("Googleの全リポジトリのスクレイピングを開始します...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    while True:
        url = f"https://github.com/orgs/google/repositories?page={page_num}"
        print(f"{page_num}ページ目を取得中: {url}")

        page_data = []
        # レート制限（429エラー）や読み込み遅延に備えて最大3回までリトライ
        for attempt in range(1, 4):
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # サーバーに負荷をかけないよう必ず1秒待機
                time.sleep(1)

                # 429エラー（アクセス制限）の場合は長めに待機して再試行
                if response and response.status == 429:
                    wait_sec = 20 * attempt
                    print(f"  [!] アクセス制限を検知しました。{wait_sec}秒待機して再試行します ({attempt}/3)...")
                    time.sleep(wait_sec)
                    continue

                # リポジトリ一覧が描画されるまで少し待つ（最大5秒）
                try:
                    page.wait_for_selector("a[href*='/stargazers']", timeout=5000)
                except Exception:
                    pass

                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                # もし「You have exceeded a secondary rate limit」などの制限画面なら待機してリトライ
                if "rate limit" in soup.get_text().lower() or "too many requests" in soup.get_text().lower():
                    wait_sec = 20 * attempt
                    print(f"  [!] レート制限メッセージが表示されました。{wait_sec}秒待機します ({attempt}/3)...")
                    time.sleep(wait_sec)
                    continue

                repo_links = soup.find_all("a", href=lambda x: x and x.startswith("/google/") and x.count("/") == 2)
                seen_in_page = set()

                for link in repo_links:
                    repo_name = link.get_text(strip=True)
                    href = link.get("href", "")

                    if not repo_name or href in ["/google/repositories", "/google/people", "/google/projects", "/google/packages", "/google/sponsoring"]:
                        continue
                    if repo_name in seen_in_page:
                        continue

                    card = link
                    for _ in range(6):
                        if card.parent:
                            card = card.parent
                            if card.find("a", href=lambda x: x and x.endswith("/stargazers")):
                                break

                    star_link = card.find("a", href=lambda x: x and x.endswith("/stargazers"))
                    if not star_link:
                        continue

                    seen_in_page.add(repo_name)
                    stars = star_link.get_text(strip=True) if star_link else "0"

                    language = "None"
                    lang_span = card.find("span", attrs={"data-testid": "repository-language-label"})
                    if lang_span:
                        language = lang_span.get_text(strip=True)
                    else:
                        for span in card.find_all("span"):
                            text = span.get_text(strip=True)
                            if (
                                text
                                and text != repo_name
                                and text != stars
                                and text not in ["Public", "Archived", "Public archive"]
                                and "Updated" not in text
                                and "License" not in text
                                and not any(lic in text for lic in ["Apache", "MIT", "BSD", "GPL", "Other"])
                                and not text.replace(".", "").replace("k", "").replace(",", "").isdigit()
                                and len(text) < 25
                            ):
                                if span.find_previous_sibling("span"):
                                    language = text
                                    break

                    page_data.append((f"google/{repo_name}", language, stars))

                # 1件以上取れたら成功なのでリトライループを抜ける
                if len(page_data) > 0:
                    break
                else:
                    # 0件だった場合、最終ページなのか一時的な読み込み失敗なのか判別するため少し待って再試行
                    if attempt < 3:
                        print(f"  [!] 0件でした。最終ページか確認するため10秒待機して再試行します ({attempt}/3)...")
                        time.sleep(10)

            except Exception as e:
                print(f"  [!] 通信エラー発生: {e}。10秒待機して再試行します ({attempt}/3)...")
                time.sleep(10)

        # 3回試しても0件なら「本当に最終ページを超えた」と判断して終了
        if len(page_data) == 0:
            print("すべてのページの取得が完了しました。")
            break

        # 取得したデータをDBに保存（重複は無視）
        cursor.executemany("""
        INSERT OR IGNORE INTO repositories (repo_name, language, stars)
        VALUES (?, ?, ?)
        """, page_data)
        conn.commit()

        # 現在のDB内の総件数を取得
        cursor.execute("SELECT COUNT(*) FROM repositories")
        total_saved = cursor.fetchone()[0]
        print(f"  -> このページで {len(page_data)} 件取得（DB累計: {total_saved} 件）")

        page_num += 1

    browser.close()

print(f"\nスクレイピング完了！合計 {total_saved} 件のデータを保存しました。\n")

# 3. 保存したデータをSELECT文で表示する
print("=== データベース内の保存データ一覧（SELECT結果） ===")
cursor.execute("SELECT id, repo_name, language, stars FROM repositories")
rows = cursor.fetchall()

for row in rows:
    print(f"ID: {row[0]} | リポジトリ名: {row[1]} | 主要言語: {row[2]} | スター数: {row[3]}")

print("====================================================")
print(f"合計: {len(rows)} 件のデータを表示しました。")

conn.close()